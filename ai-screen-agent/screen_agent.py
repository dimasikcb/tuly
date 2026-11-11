"""
AI Screen Bridge (Computer Use) - Autonomous Test Solver
Скрипт-мост: дает Gemini глаза (захват области экрана) и руки (клики мышкой).
"""

import sys
import os
import json
import time
import threading
import queue
import base64
from collections import deque
import tkinter as tk
from tkinter import ttk, messagebox
from PIL import Image, ImageTk, ImageGrab, ImageDraw

# Настройка DPI Awareness для Windows (чтобы координаты совпали пиксель-в-пиксель на мониторах с масштабированием)
if sys.platform == "win32":
    import ctypes
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2) # PerMonitorV2
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass

import pyautogui
pyautogui.FAILSAFE = True  # Экстренная остановка: дернуть курсор в угол экрана
pyautogui.PAUSE = 0.1

# Попытка импорта официального google-genai, иначе fallback на requests
try:
    from google import genai
    from google.genai import types
    HAS_GOOGLE_GENAI = True
except ImportError:
    HAS_GOOGLE_GENAI = False

import requests

# Служебные модули рядом со скриптом (их импорт без GUI и без сети):
# agent_window   — скрытие окна из скриншотов (WDA_EXCLUDEFROMCAPTURE) + детект самоклика
# agent_progress — diff_ratio + StagnationTracker (детект зависания автоцикла)
# agent_speed    — downscale_for_api + encode_jpeg + get_session (ускорение шага)
import agent_window
import agent_progress
import agent_speed

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import theme as _theme
from theme import apple_tk as _apple_tk


def tc(role: str) -> str:
    """Цвет по семантической роли из theme/tokens.py."""
    return _theme.c(role)


def ui_font(size: int, bold: bool = False) -> tuple:
    return (_apple_tk.ui_family(), size, "bold" if bold else "normal")


def mono_font(size: int, bold: bool = False) -> tuple:
    return (_apple_tk.mono_family(), size, "bold" if bold else "normal")


CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

# Детект «экран изменился после клика» — по АБСОЛЮТНОМУ числу различающихся
# пикселей уменьшенного снапшота (agent_progress.diff_stats +
# MIN_CHANGED_PIXELS). Прежняя доля 0.5% (SCREEN_CHANGE_MIN_RATIO) пропускала
# реальный прогресс: успешный клик по радиокнопке/чекбоксу без широкой
# подсветки строки меняет ~0.016% кадра (замер симуляцией на ROI 2879x1629 —
# это ~6 пикселей снапшота 256x145) и проходил как «экран НЕ изменился», из-за
# чего промпт врал модели «опция уже выбрана — жми Далее», и ответ уходил
# невыбранным. Пайплайн сравнения детерминирован: два захвата статичного
# экрана дают ровно 0 различающихся пикселей, поэтому порог «>= 4 пикселей»
# отделяет шум от отметки чекбокса/радио (обоснование — в agent_progress).


_desktop_tls = threading.local()


def ensure_interactive_desktop():
    """Переключает поток на интерактивный рабочий стол Windows, если запущен в изолированной среде.

    Вызывается перед каждым захватом, но реально работает один раз на поток —
    рабочий стол у потока между захватами не меняется. Хэндл OpenDesktopW
    закрывается сразу после SetThreadDesktop: раньше OpenDesktopW звался на
    каждый захват и хэндл никогда не закрывался (+1 USER-хэндл на захват,
    ~3 хэндла/шаг с after-снимком — длинная сессия утекала в тысячи хэндлов).
    """
    if getattr(_desktop_tls, "done", False):
        return
    if sys.platform == "win32":
        try:
            user32 = ctypes.windll.user32
            h_def = user32.OpenDesktopW("Default", 0, False, 0x01FF)
            if h_def:
                try:
                    user32.SetThreadDesktop(h_def)
                finally:
                    user32.CloseDesktop(h_def)
        except Exception:
            pass
    _desktop_tls.done = True


def resolve_click_scale(x, y):
    """Переводит координаты из ответа модели в доли (0.0..1.0) относительно ROI.

    Промпт требует целые 0..1000, но прежняя эвристика «обе координаты <= 100
    значит шкала 0..100» ломала валидные ответы 0..1000 из левого верхнего
    угла (~10%x10% ROI): (80, 40) читалось как 80%x40% — клик улетал в центр
    вместо первой опции; (1, 1) читалось как 100%x100% — в правый нижний угол.
    Правила теперь такие (контракт промпта приоритетнее совместимости):
      * обе координаты целые (в т.ч. 80.0) => шкала 0..1000;
      * обе <= 1.0 и хотя бы одна дробная (0.4) => нормализованные 0..1;
      * обе <= 100 и хотя бы одна дробная (37.5) => проценты 0..100;
      * иначе => 0..1000.
    Выход за диапазон 0..1000 клампится.
    """
    x = min(max(float(x), 0.0), 1000.0)
    y = min(max(float(y), 0.0), 1000.0)
    if x <= 1.0 and y <= 1.0 and (not float(x).is_integer() or not float(y).is_integer()):
        return x, y
    return x / 1000.0, y / 1000.0


class ScreenSelector(tk.Toplevel):
    """Полноэкранный полупрозрачный оверлей для выделения области мышкой."""
    def __init__(self, parent, callback):
        super().__init__(parent)
        self.callback = callback
        self.attributes("-fullscreen", True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.35)
        self.configure(bg=tc("overlay_backdrop"))
        self.config(cursor="cross")

        self.start_x = None
        self.start_y = None
        self.current_rect = None

        self.canvas = tk.Canvas(self, cursor="cross", bg=tc("overlay_backdrop"), highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        # Текст подсказки
        screen_w = self.winfo_screenwidth()
        self.hint = self.canvas.create_text(
            screen_w // 2, 40,
            text="Зажмите ЛКМ и выделите рамку с тестом. Нажмите Esc для отмены.",
            fill=tc("overlay_timer"), font=ui_font(16, True)
        )

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.bind("<Escape>", lambda e: self.cancel())
        self.protocol("WM_DELETE_WINDOW", self.cancel)

    def on_press(self, event):
        self.start_x = event.x
        self.start_y = event.y
        if self.current_rect:
            self.canvas.delete(self.current_rect)
        self.current_rect = self.canvas.create_rectangle(
            self.start_x, self.start_y, self.start_x, self.start_y,
            outline=tc("roi"), width=3, fill=tc("roi")
        )

    def on_drag(self, event):
        cur_x, cur_y = event.x, event.y
        self.canvas.coords(self.current_rect, self.start_x, self.start_y, cur_x, cur_y)

    def on_release(self, event):
        end_x, end_y = event.x, event.y
        x1 = min(self.start_x, end_x)
        y1 = min(self.start_y, end_y)
        x2 = max(self.start_x, end_x)
        y2 = max(self.start_y, end_y)
        w = x2 - x1
        h = y2 - y1

        self.destroy()
        if w > 20 and h > 20:
            self.callback((x1, y1, w, h))
        else:
            self.callback(None)

    def cancel(self):
        self.destroy()
        self.callback(None)


class AIScreenAgentApp:
    def __init__(self, root):
        self.root = root
        self.root.title("AI Screen Bridge — Автокликер тестов")
        # Шрифты Tk заданы в пунктах (tk scaling = DPI/72), а geometry — в
        # физических пикселях. При масштабе Windows 150–200% окно 620x760
        # становится «маленьким» относительно 13pt → ряды обрезаются. Тот же
        # приём, что и в tools.pyw: SCALE = winfo_fpixels('1i') / 96, px(v).
        self._scale = max(1.0, self.root.winfo_fpixels("1i") / 96.0)
        def px(value: float) -> int:
            return int(round(value * self._scale))
        width = min(px(620), self.root.winfo_screenwidth() - px(40))
        height = min(px(760), self.root.winfo_screenheight() - px(90))
        self.root.geometry(f"{width}x{height}")
        self.root.minsize(min(px(560), width), min(px(680), height))
        self._theme_resolved = _theme.mode()
        self.root.configure(bg=tc("window"))
        _apple_tk.titlebar(self.root, self._theme_resolved)
        _theme.on_change(self._apply_theme_mode)
        self.root.after(2000, self._theme_tick)

        # Переменные состояния
        self.roi = None  # (x, y, w, h)
        self.is_running = False
        self.stop_requested = False
        self.worker_thread = None
        self.log_queue = queue.Queue()
        self.last_preview_img = None
        # Детект зависания автоцикла и короткая история кликов для промпта
        self._stagnation = agent_progress.StagnationTracker()
        self._action_history = deque(maxlen=3)  # последние 3 клика: (norm_x, norm_y, screen_changed)
        # Поколения воркеров: СТОП во время висящего API-вызова + быстрый СТАРТ
        # раньше сбрасывали stop_requested, старый воркер продолжал цикл, и два
        # воркера кликали одновременно. Теперь СТАРТ поднимает номер поколения,
        # а воркер предыдущего выходит по несовпадению (и не кликает: см.
        # should_abort в execute_one_cycle).
        self._worker_generation = 0
        self._single_step_running = False  # СТАРТ блокируется, пока идёт одиночный шаг
        # Fallback-скрытие окна на время захвата (когда WDA_EXCLUDEFROMCAPTURE
        # не сработал): пояснение в журнал пишем один раз, а не на каждый захват
        self._hide_fallback_logged = False
        self._capture_hide_lock = threading.Lock()
        self._capture_hide_depth = 0  # >0 — окно уже спрятано параллельным захватом

        # Загрузка конфига
        self.config = self.load_config()

        # Создание интерфейса
        self.setup_ui()

        # Зеркало Tk-переменных для worker-потока. Прямые .get() из потока
        # формально небезопасны (падают «main thread is not in main loop»,
        # если mainloop ещё не запущен), поэтому значения копируются в обычный
        # dict трассировкой на главном потоке; worker читает только dict.
        self._ui_snapshot = {
            "api_key": self.config.get("api_key", ""),
            "model": self.config.get("model", "gemini-2.5-flash"),
            "custom_hint": self.config.get("custom_instruction", ""),
            "smooth_mouse": bool(self.config.get("smooth_mouse", True)),
            "delay": float(self.config.get("delay", 2.5)),
        }
        self._install_ui_snapshot_traces()

        # Исключаем собственное окно из захвата экрана (WDA_EXCLUDEFROMCAPTURE):
        # пользователь продолжает его видеть, но окно исчезает из скриншотов,
        # OBS и шаринга экрана, а модель больше не принимает его за часть теста.
        # Статус операции (✅/⚠️) модуль сам пишет в журнал через self.log;
        # HWND при этом привязывается даже при неудаче — детект самоклика работает.
        try:
            self.root.update_idletasks()
        except Exception:
            pass
        agent_window.exclude_from_capture(self.root, self.log)

        # Восстановление сохраненного ROI
        roi_saved = self.config.get("roi")
        if roi_saved and isinstance(roi_saved, (list, tuple)) and len(roi_saved) == 4:
            self.roi = tuple(roi_saved)
            self.update_roi_label()

        # Поддержка горячих клавиш буфера в любой раскладке
        self.setup_clipboard_shortcuts()

        # Запуск цикла обработки очереди логов
        self.root.after(100, self.process_log_queue)

    def _install_ui_snapshot_traces(self):
        """Зеркалит значения Tk-переменных в self._ui_snapshot для worker-потока.

        Чтение StringVar/DoubleVar.get() из не-главного потока формально
        небезопасно (может падать «main thread is not in main loop»), поэтому
        worker-код читает обычный dict: трассировка на запись обновляет его
        на главном потоке (виджеты тоже пишут в переменную на главном).
        Начальные значения dict берутся из конфига в __init__.
        """
        def mirror(key, getter, cast=None):
            def _on_write(*_args):
                try:
                    value = getter()
                    self._ui_snapshot[key] = cast(value) if cast is not None else value
                except Exception:
                    return  # мусор в поле ввода (например, задержка) — держим прошлое значение
            return _on_write

        self.api_key_var.trace_add("write", mirror("api_key", self.api_key_var.get))
        self.model_var.trace_add("write", mirror("model", self.model_var.get))
        self.instruction_var.trace_add("write", mirror("custom_hint", self.instruction_var.get))
        self.smooth_mouse_var.trace_add("write", mirror("smooth_mouse", self.smooth_mouse_var.get, bool))
        self.delay_var.trace_add("write", mirror("delay", self.delay_var.get, float))

    def load_config(self):
        default_config = {
            "api_key": os.environ.get("GEMINI_API_KEY", ""),
            "model": "free/gemini-3.8-flash",
            "base_url": "https://api.apinex.bond/v1",
            "delay": 2.5,
            "always_on_top": True,
            "smooth_mouse": True,
            "roi": None,
            "custom_instruction": ""
        }
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    saved = json.load(f)
                    default_config.update(saved)
            except Exception as e:
                print(f"Ошибка загрузки config: {e}")
        return default_config

    def save_config(self):
        self.config["api_key"] = self.api_key_var.get().strip()
        self.config["model"] = self.model_var.get().strip()
        self.config["delay"] = float(self.delay_var.get())
        self.config["always_on_top"] = self.always_on_top_var.get()
        self.config["smooth_mouse"] = self.smooth_mouse_var.get()
        self.config["custom_instruction"] = self.instruction_var.get().strip()
        if self.roi:
            self.config["roi"] = list(self.roi)
        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(self.config, f, ensure_ascii=False, indent=2)
            self.log("💾 Настройки успешно сохранены.")
        except Exception as e:
            self.log(f"❌ Ошибка сохранения настроек: {e}")

    def setup_ui(self):
        # Настройка стилей
        _apple_tk.apply_theme(self.root)

        main_frame = ttk.Frame(self.root, padding=12)
        main_frame.pack(fill="both", expand=True)

        # 1. Секция Настроек API
        api_group = ttk.LabelFrame(main_frame, text="  1. Мозг (Gemini Vision API)  ", padding=10)
        api_group.pack(fill="x", pady=(0, 8))

        # API Key row
        key_frame = ttk.Frame(api_group)
        key_frame.pack(fill="x", pady=2)
        ttk.Label(key_frame, text="API Key:", width=10).pack(side="left")
        
        self.api_key_var = tk.StringVar(value=self.config.get("api_key", ""))
        self.api_key_entry = ttk.Entry(key_frame, textvariable=self.api_key_var, show="*")
        self.api_key_entry.pack(side="left", fill="x", expand=True, padx=(0, 6))

        self.show_key_var = tk.BooleanVar(value=False)
        self.show_key_btn = ttk.Checkbutton(key_frame, text="👁", style="Toolbutton", command=self.toggle_show_key)
        self.show_key_btn.pack(side="left", padx=(0, 6))

        ttk.Button(key_frame, text="📋 Вставить", command=self.paste_api_key).pack(side="left", padx=(0, 6))
        ttk.Button(key_frame, text="Сохранить", command=self.save_config).pack(side="left")

        # Привязка Ctrl+V в любой раскладке и ПКМ
        self.api_key_entry.bind("<Control-v>", lambda e: (self.paste_api_key(), "break")[1])
        self.api_key_entry.bind("<Control-V>", lambda e: (self.paste_api_key(), "break")[1])
        self.api_key_entry.bind("<Control-KeyPress>", lambda e: (self.paste_api_key(), "break")[1] if getattr(e, "keycode", 0) == 86 or (e.keysym or "").lower() in ("v", "м") else None)

        def _on_key_rmenu(event):
            m = tk.Menu(self.api_key_entry, tearoff=0)
            m.configure(bg=tc("surface"), fg=tc("label"),
                        activebackground=tc("accent_fill"),
                        activeforeground=tc("on_accent"), relief="flat", bd=0)
            m.add_command(label="Вставить из буфера", command=self.paste_api_key)
            m.add_command(label="Копировать", command=lambda: (self.root.clipboard_clear(), self.root.clipboard_append(self.api_key_var.get())))
            m.add_command(label="Очистить", command=lambda: self.api_key_var.set(""))
            m.tk_popup(event.x_root, event.y_root)

        self.api_key_entry.bind("<Button-3>", _on_key_rmenu)

        # Model & Delay row
        opt_frame = ttk.Frame(api_group)
        opt_frame.pack(fill="x", pady=(6, 2))

        ttk.Label(opt_frame, text="Модель:", width=10).pack(side="left")
        self.model_var = tk.StringVar(value=self.config.get("model", "gemini-2.5-flash"))
        model_combo = ttk.Combobox(opt_frame, textvariable=self.model_var, width=22,
                                   values=["free/gemini-3.8-flash", "gemini-3.8-flash", "claude-sonnet-5.5", "free/claude-sonnet-4.6", "gemini-2.5-flash", "gemini-2.0-flash"])
        model_combo.pack(side="left", padx=(0, 16))

        ttk.Label(opt_frame, text="Пауза (сек):").pack(side="left", padx=(0, 4))
        self.delay_var = tk.DoubleVar(value=self.config.get("delay", 2.5))
        delay_spin = ttk.Spinbox(opt_frame, from_=0.5, to=15.0, increment=0.5, textvariable=self.delay_var, width=5)
        delay_spin.pack(side="left")

        # 2. Секция Глаза (Выделение ROI)
        roi_group = ttk.LabelFrame(main_frame, text="  2. Глаза (Область экрана с тестом)  ", padding=10)
        roi_group.pack(fill="x", pady=(0, 8))

        roi_top_frame = ttk.Frame(roi_group)
        roi_top_frame.pack(fill="x")

        self.select_btn = ttk.Button(roi_top_frame, text="🎯 Выделить область мышкой", command=self.start_roi_selection)
        self.select_btn.pack(side="left", padx=(0, 10))

        self.roi_label_var = tk.StringVar(value="Область не выбрана")
        self.roi_label = ttk.Label(roi_top_frame, textvariable=self.roi_label_var,
                                  font=mono_font(10, True), foreground=tc("tag_dir"))
        self.roi_label.pack(side="left", fill="x", expand=True)

        # Превью кадра
        self.preview_frame = ttk.Frame(roi_group)
        self.preview_frame.pack(fill="x", pady=(8, 0))
        self.preview_canvas = tk.Canvas(self.preview_frame, height=130,
                                       bg=tc("inset"), highlightthickness=1,
                                       highlightbackground=tc("separator_strong"))
        self.preview_canvas.pack(fill="x")
        self.preview_text = self.preview_canvas.create_text(
            280, 65, text="Здесь появится снимок выделенной области",
            fill=tc("label_tertiary"), font=ui_font(10)
        )

        # 3. Секция Руки (Управление)
        ctrl_group = ttk.LabelFrame(main_frame, text="  3. Руки (Управление и клики)  ", padding=10)
        ctrl_group.pack(fill="x", pady=(0, 8))

        ctrl_btns_frame = ttk.Frame(ctrl_group)
        ctrl_btns_frame.pack(fill="x")

        self.start_btn = _apple_tk.flat_button(
            ctrl_btns_frame, "▶ СТАРТ (Авто-прохождение)",
            self.toggle_start, kind="primary"
        )
        self.start_btn.pack(side="left", padx=(0, 10))

        self.step_btn = _apple_tk.flat_button(
            ctrl_btns_frame, "⚡ Шаг (1 клик)",
            self.do_single_step, kind="ghost"
        )
        self.step_btn.pack(side="left", padx=(0, 15))

        # Флаги — отдельным рядом: при 150–200% масштабе Windows четыре
        # элемента в одной строке не помещались и обрезали «Плавный клик».
        ctrl_flags_frame = ttk.Frame(ctrl_group)
        ctrl_flags_frame.pack(fill="x", pady=(8, 0))

        self.always_on_top_var = tk.BooleanVar(value=self.config.get("always_on_top", True))
        top_chk = _apple_tk.check(ctrl_flags_frame, "Поверх всех окон",
                                  self.always_on_top_var,
                                  command=self.apply_always_on_top)
        top_chk.pack(side="left", padx=(0, 16))
        self.apply_always_on_top()

        self.smooth_mouse_var = tk.BooleanVar(value=self.config.get("smooth_mouse", True))
        smooth_chk = _apple_tk.check(ctrl_flags_frame, "Плавный клик",
                                     self.smooth_mouse_var)
        smooth_chk.pack(side="left")

        # Доп. инструкция (опционально)
        inst_frame = ttk.Frame(ctrl_group)
        inst_frame.pack(fill="x", pady=(8, 0))
        ttk.Label(inst_frame, text="Подсказка ИИ:").pack(side="left", padx=(0, 6))
        self.instruction_var = tk.StringVar(value=self.config.get("custom_instruction", ""))
        inst_entry = ttk.Entry(inst_frame, textvariable=self.instruction_var)
        inst_entry.pack(side="left", fill="x", expand=True)

        # 4. Лог и мысли ИИ
        log_group = ttk.LabelFrame(main_frame, text="  4. Мысли ИИ и Журнал действий  ", padding=8)
        log_group.pack(fill="both", expand=True)

        self.log_text = _apple_tk.log_view(log_group, wrap="word", font=mono_font(10))
        log_scroll = ttk.Scrollbar(log_group, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)

        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        # Переключатель темы — внизу, вне плотных рядов управления.
        theme_bar = ttk.Frame(main_frame)
        theme_bar.pack(fill="x", pady=(8, 0))
        self.theme_btn = ttk.Button(theme_bar, text=self._theme_label(),
                                    command=self._cycle_theme)
        self.theme_btn.pack(side="right")

        # Привязка клавиши Esc для экстренной остановки
        self.root.bind("<Escape>", lambda e: self.stop_if_running())

        # Приветственное сообщение
        self.log("🚀 AI Screen Bridge готов к работе.")
        self.log("1. Вставьте ваш Gemini API Key (бесплатно в Google AI Studio).")
        self.log("2. Нажмите 'Выделить область мышкой' и обведите окно с тестом.")
        self.log("3. Нажмите 'СТАРТ' — ИИ будет анализировать экран и нажимать ответы.")
        self.log("⚠️ Аварийная остановка: дерните мышь в угол экрана или нажмите СТОП / Esc.")

    # ─────────────────────────────── тема ────────────────────────────────
    @staticmethod
    def _theme_label() -> str:
        return {"auto": "Тема: авто", "light": "Тема: светлая",
                "dark": "Тема: тёмная"}[_theme.raw_mode()]

    def _cycle_theme(self) -> None:
        order = ("auto", "light", "dark")
        _theme.set_mode(order[(order.index(_theme.raw_mode()) + 1) % len(order)])

    def _apply_theme_mode(self, resolved: str) -> None:
        """Живая смена Light/Dark: ttk-стили → tk-виджеты → кастомные цвета → DWM."""
        if resolved == self._theme_resolved:
            return
        previous = self._theme_resolved
        self._theme_resolved = resolved
        _apple_tk.apply_theme(self.root, resolved)
        _apple_tk.recolor(self.root, previous, resolved)
        _apple_tk.redraw(self.root)
        _apple_tk.titlebar(self.root, resolved)
        self.root.configure(bg=tc("window"))
        self._recolor_custom()
        if hasattr(self, "theme_btn"):
            self.theme_btn.configure(text=self._theme_label())

    def _recolor_custom(self) -> None:
        """Цвета, чей роль generic recolor() не может вывести однозначно."""
        if not hasattr(self, "preview_canvas"):
            return
        self.roi_label.configure(foreground=tc("tag_dir"))
        self.preview_canvas.configure(bg=tc("inset"),
                                      highlightbackground=tc("separator_strong"))
        try:
            self.preview_canvas.itemconfigure(self.preview_text,
                                              fill=tc("label_tertiary"))
        except tk.TclError:  # после показа снимка canvas очищен — текст уже не нужен
            pass
        self.log_text.configure(bg=tc("inset"), fg=tc("log_info"),
                                insertbackground=tc("label"))
        self._set_start_button(self.is_running)

    def _set_start_button(self, running: bool) -> None:
        """СТАРТ = единственное главное действие; СТОП = безопасный danger."""
        if running:
            self.start_btn.config(text="⏹ СТОП", bg=tc("err_bg"),
                                  fg=tc("badge_bad_ink"),
                                  activebackground=tc("err_hover_bg"))
        else:
            self.start_btn.config(text="▶ СТАРТ (Авто)", bg=tc("accent_fill"),
                                  fg=tc("on_accent"),
                                  activebackground=tc("accent_fill_hover"))

    def _theme_tick(self) -> None:
        if not self.root.winfo_exists():
            return
        if _theme.raw_mode() == "auto" and _theme.mode() != self._theme_resolved:
            self._apply_theme_mode(_theme.mode())
        self.root.after(2000, self._theme_tick)

    def toggle_show_key(self):
        if self.show_key_var.get():
            self.api_key_entry.configure(show="")
        else:
            self.api_key_entry.configure(show="*")
        self.show_key_var.set(not self.show_key_var.get())

    def paste_api_key(self):
        try:
            val = self.root.clipboard_get()
            if val:
                val = val.strip().strip('"\' \t\r\n')
                self.api_key_var.set(val)
                self.save_config()
                self.log("✅ API ключ вставлен из буфера обмена и сохранён!")
        except Exception as e:
            self.log(f"⚠️ Ошибка чтения буфера: {e}")

    def setup_clipboard_shortcuts(self):
        def on_ctrl(event):
            w = event.widget
            if not isinstance(w, (tk.Entry, tk.Text, ttk.Entry)):
                return None
            vk = getattr(event, "keycode", 0) or 0
            ks = (event.keysym or "").lower()
            if vk == 86 or ks in ("v", "м"):
                w.event_generate("<<Paste>>")
                return "break"
            if vk == 67 or ks in ("c", "с"):
                w.event_generate("<<Copy>>")
                return "break"
            if vk == 88 or ks in ("x", "ч"):
                w.event_generate("<<Cut>>")
                return "break"
            if vk == 65 or ks in ("a", "а", "ф"):
                if isinstance(w, tk.Text):
                    w.tag_add("sel", "1.0", "end-1c")
                else:
                    w.selection_range(0, "end")
                return "break"
            return None

        self.root.bind_all("<Control-KeyPress>", on_ctrl, add="+")

    def apply_always_on_top(self):
        self.root.attributes("-topmost", self.always_on_top_var.get())

    def update_roi_label(self):
        if self.roi:
            x, y, w, h = self.roi
            self.roi_label_var.set(f"X: {x}, Y: {y} | {w}x{h} px")
            self.capture_and_update_preview()
        else:
            self.roi_label_var.set("Область не выбрана")

    def start_roi_selection(self):
        self.root.withdraw()
        self.root.after(250, self._open_overlay)

    def _open_overlay(self):
        ensure_interactive_desktop()
        ScreenSelector(self.root, self.on_roi_selected)

    def on_roi_selected(self, coords):
        self.root.deiconify()
        if coords:
            self.roi = coords
            self.update_roi_label()
            self.log(f"🎯 Новая область выбрана: X={coords[0]}, Y={coords[1]}, W={coords[2]}, H={coords[3]}")
            self.save_config()
        else:
            self.log("Выделение области отменено.")

    def log(self, message):
        t_str = time.strftime("%H:%M:%S")
        self.log_queue.put(f"[{t_str}] {message}\n")

    def process_log_queue(self):
        while not self.log_queue.empty():
            msg = self.log_queue.get_nowait()
            self.log_text.insert("end", msg)
            self.log_text.see("end")
        self.root.after(100, self.process_log_queue)

    def capture_roi_image(self):
        """Захватывает выбранную область экрана в формате PIL.Image.

        Если исключение окна из захвата (WDA_EXCLUDEFROMCAPTURE) не сработало
        (старые Windows), включается fallback: на время снимка окно прячется
        (withdraw) и возвращается после. Работает только из воркер-потока —
        превью на главном потоке снимает «как есть», не блокируя mainloop.
        Параллельные захваты считают глубину: окно возвращается только после
        последнего. Пояснение в журнал пишется один раз, а не на каждый захват.
        """
        if not self.roi:
            return None
        ensure_interactive_desktop()
        x, y, w, h = self.roi
        bbox = (x, y, x + w, y + h)

        hide_fallback = (
            not agent_window.capture_exclusion_active()
            and threading.current_thread() is not threading.main_thread()
        )
        if hide_fallback:
            with self._capture_hide_lock:
                outermost = self._capture_hide_depth == 0
                self._capture_hide_depth += 1
            if not self._hide_fallback_logged:
                self._hide_fallback_logged = True
                self.log(
                    "⚠️ Исключение окна из захвата не сработало — включаю fallback: "
                    "окно будет кратко скрываться на время каждого снимка."
                )
            if outermost:
                self.root.after(0, self.root.withdraw)
                time.sleep(0.15)  # даём mainloop убрать окно с экрана до захвата
        try:
            try:
                img = ImageGrab.grab(bbox=bbox)
                return img
            except Exception as e:
                self.log(f"Ошибка ImageGrab: {e}. Пробую альтернативный метод...")
                try:
                    from mss import MSS
                    with MSS() as sct:
                        monitor = {"left": x, "top": y, "width": w, "height": h}
                        sct_img = sct.grab(monitor)
                        return Image.frombytes("RGB", sct_img.size, sct_img.bgra, "raw", "BGRX")
                except Exception as e2:
                    self.log(f"Критическая ошибка захвата экрана: {e2}")
                    return None
        finally:
            if hide_fallback:
                with self._capture_hide_lock:
                    self._capture_hide_depth -= 1
                    restore = self._capture_hide_depth == 0
                if restore:
                    self.root.after(0, self.root.deiconify)

    def capture_and_update_preview(self, click_pt=None, drag_pt=None, base_img=None):
        """Обновляет холст превью: точка клика или линия перетаскивания (drag)."""
        img = base_img.copy() if base_img is not None else self.capture_roi_image()
        if not img:
            return

        canvas_w = self.preview_canvas.winfo_width() or 580
        canvas_h = self.preview_canvas.winfo_height() or 130

        # Если передан клик, рисуем яркую точку; для drag — ещё и линию до конечной точки
        if click_pt:
            draw = ImageDraw.Draw(img)
            cx, cy = click_pt
            r = max(5, int(min(img.width, img.height) * 0.02))
            if drag_pt:
                dx, dy = drag_pt
                draw.line((cx, cy, dx, dy), fill=tc("marker_fill"), width=4)
                draw.ellipse((dx - r, dy - r, dx + r, dy + r),
                             fill=tc("marker_fill"), outline=tc("marker_outline"), width=2)
            draw.ellipse((cx - r, cy - r, cx + r, cy + r),
                         fill=tc("marker_fill"), outline=tc("marker_outline"), width=2)

        # Масштабируем превью под холст
        img_thumb = img.copy()
        img_thumb.thumbnail((canvas_w, canvas_h), Image.Resampling.LANCZOS)
        self.last_preview_img = ImageTk.PhotoImage(img_thumb)

        self.preview_canvas.delete("all")
        self.preview_canvas.create_image(
            canvas_w // 2, canvas_h // 2,
            image=self.last_preview_img, anchor="center"
        )

    def call_vision_api(self, img_bytes):
        """Отправляет изображение в Vision API (APInex / OpenAI / Google) и получает координаты."""
        api_key = self._ui_snapshot["api_key"].strip()
        model_name = self._ui_snapshot["model"].strip() or "free/gemini-3.8-flash"
        custom_hint = self._ui_snapshot["custom_hint"].strip()
        base_url = self.config.get("base_url", "").strip()

        if not api_key:
            raise ValueError("Не указан API Key! Введите ключ вверху окна.")

        # Автоопределение провайдера
        is_google_native = api_key.startswith("AIzaSy") or api_key.startswith("AIza")
        is_openai_compat = api_key.startswith("sk-") or (bool(base_url) and not is_google_native)

        if not base_url and not is_google_native:
            if api_key.startswith("sk-apx"):
                base_url = "https://api.apinex.bond/v1"
            elif api_key.startswith("sk-or"):
                base_url = "https://openrouter.ai/api/v1"
            else:
                base_url = "https://api.apinex.bond/v1"

        system_instruction = (
            "You are an autonomous AI test-solving assistant with computer vision. "
            "You are looking at a screenshot of a test, quiz, exam, or web form. "
            "Your tasks:\n"
            "1. Read and understand the question and all answer options.\n"
            "2. Determine the correct answer.\n"
            "3. If an answer option needs to be selected, target the radio button, checkbox, or option text.\n"
            "4. If the answer is already selected or if there is a 'Next' / 'Далее' / 'Submit' / 'Ответить' / 'Продолжить' button required to advance, target that button.\n"
            "5. Matching tasks ('сопоставьте' / 'соедини' / two columns of items to pair): click the radio of an item "
            "in ONE column, then on the next step click the radio of the matching item in the OPPOSITE column. "
            "NEVER pair two items from the same column, and never re-connect a pair that is already linked. "
            "If the interface clearly requires dragging a line between items, use action='drag' with "
            "x1,y1 (start on the left item) and x2,y2 (end on the right item).\n"
            "6. If the test is completely finished (score / results page shown), set action='done'.\n"
            "Coordinates:\n"
            "Return target coordinates 'x' and 'y' as integers normalized from 0 to 1000 relative to this image. "
            "(x=0 is leftmost, x=1000 is rightmost, y=0 is top, y=1000 is bottom). "
            "For action='drag' return 'x1','y1','x2','y2' instead of 'x','y'.\n"
            "Output MUST be valid JSON only."
        )

        prompt = (
            "Analyze the screenshot of the test.\n"
            "Return JSON in this exact structure:\n"
            "{\n"
            '  "thought": "Short explanation: Question is X, correct answer is Y / Clicking Next button",\n'
            '  "action": "click" | "drag" | "done",\n'
            '  "x": <0-1000>, "y": <0-1000>           // for action="click"\n'
            '  "x1": <0-1000>, "y1": <0-1000>,        // for action="drag": line start\n'
            '  "x2": <0-1000>, "y2": <0-1000>         // for action="drag": line end\n'
            "}\n"
        )
        if custom_hint:
            prompt += f"\nUser extra hint: {custom_hint}\n"

        # История последних действий: без неё каждый запрос stateless и модель
        # повторяет уже сделанный клик. Пометка колонки (left/right) важна для
        # заданий-сопоставлений: выбрав элемент слева, модель должна продолжить
        # пару в противоположной колонке, а не в той же.
        if self._action_history:
            hist_lines = [
                "- " + e["text"] + " in 0-1000 image coords — "
                + ("screen CHANGED" if e["changed"] else "screen did NOT change")
                for e in self._action_history
            ]
            prompt += (
                "\nHistory of your recent actions (oldest first):\n"
                + "\n".join(hist_lines) + "\n"
                "RULES:\n"
                "- If your previous click on an answer option did NOT change the screen, that option is "
                "already selected — do NOT click the same point again; advance with 'Далее'/'Next'/'Submit'/'Ответить'.\n"
                "- Matching tasks: if you just selected an item in one column, click its pair in the "
                "OPPOSITE column. NEVER pair two items from the same column; never re-connect a pair "
                "that is already linked.\n"
            )

        b64_img = base64.b64encode(img_bytes).decode("utf-8")

        # 1. Запрос к APInex / OpenRouter / OpenAI-совместимому API
        if is_openai_compat and base_url:
            url = f"{base_url.rstrip('/')}/chat/completions"
            headers = {
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json"
            }
            payload = {
                "model": model_name,
                "messages": [
                    {"role": "system", "content": system_instruction},
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64_img}"}}
                        ]
                    }
                ],
                "response_format": {"type": "json_object"}
            }
            # Общий Session с keep-alive: без DNS+TCP+TLS-handshake на каждый шаг
            res = agent_speed.get_session().post(url, headers=headers, json=payload, timeout=35)
            if res.status_code != 200:
                raise RuntimeError(f"HTTP {res.status_code}: {res.text}")
            res_data = res.json()
            raw_text = res_data["choices"][0]["message"]["content"]
            return self._parse_json_response(raw_text)

        # 2. Запрос к официальному Google Gemini API
        if HAS_GOOGLE_GENAI:
            try:
                client = genai.Client(api_key=api_key)
                part = types.Part.from_bytes(data=img_bytes, mime_type="image/jpeg")
                response = client.models.generate_content(
                    model=model_name,
                    contents=[prompt, part],
                    config=types.GenerateContentConfig(
                        system_instruction=system_instruction,
                        response_mime_type="application/json",
                        temperature=0.1
                    )
                )
                return self._parse_json_response(response.text)
            except Exception as e:
                self.log(f"ℹ️ SDK info: {e}. Переключаюсь на прямой REST запрос...")

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={api_key}"
        payload = {
            "system_instruction": {"parts": [{"text": system_instruction}]},
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {"inline_data": {"mime_type": "image/jpeg", "data": b64_img}}
                    ]
                }
            ],
            "generationConfig": {"response_mime_type": "application/json", "temperature": 0.1}
        }
        # Общий Session с keep-alive и здесь (REST-fallback Google)
        res = agent_speed.get_session().post(url, json=payload, timeout=35)
        if res.status_code != 200:
            raise RuntimeError(f"HTTP {res.status_code}: {res.text}")
        res_data = res.json()
        raw_text = res_data["candidates"][0]["content"]["parts"][0]["text"]
        return self._parse_json_response(raw_text)

    def _parse_json_response(self, raw_text):
        text = raw_text.strip()
        if text.startswith("```json"):
            text = text[7:]
        if text.startswith("```"):
            text = text[3:]
        if text.endswith("```"):
            text = text[:-3]
        text = text.strip()

        data = json.loads(text)
        # Обработка разных форматов координат
        x, y = None, None
        if "x" in data and "y" in data:
            x, y = float(data["x"]), float(data["y"])
        elif "point" in data and isinstance(data["point"], (list, tuple)) and len(data["point"]) == 2:
            y, x = float(data["point"][0]), float(data["point"][1])
        elif "box_2d" in data and isinstance(data["box_2d"], (list, tuple)) and len(data["box_2d"]) == 4:
            ymin, xmin, ymax, xmax = data["box_2d"]
            y = (ymin + ymax) / 2.0
            x = (xmin + xmax) / 2.0

        # Перетаскивание линии (задания-сопоставления): x1,y1 -> x2,y2
        drag = None
        if all(k in data for k in ("x1", "y1", "x2", "y2")):
            try:
                drag = (float(data["x1"]), float(data["y1"]), float(data["x2"]), float(data["y2"]))
            except (TypeError, ValueError):
                drag = None

        if (x is None or y is None) and drag is None and data.get("action") != "done":
            raise ValueError(f"Ответ модели не содержит координат: {data}")

        result = {
            "thought": data.get("thought", "Нет описания"),
            "action": data.get("action", "click"),
            "x": x,
            "y": y,
        }
        if drag is not None:
            result["drag"] = drag
        return result

    def should_abort(self, generation):
        """True, когда воркеру нельзя продолжать: нажат СТОП или стартовало новое поколение.

        СТОП во время висящего API-вызова + быстрый СТАРТ раньше сбрасывали
        stop_requested, старый воркер продолжал цикл, и два воркера кликали
        одновременно; теперь СТАРТ поднимает _worker_generation, и воркер
        предыдущего поколения выходит по несовпадению.
        """
        return self.stop_requested or generation != self._worker_generation

    def execute_one_cycle(self, generation=None):
        """Выполняет один цикл: снимок -> анализ -> клик.

        generation передаётся воркером авто-режима: пока идёт медленный
        API-вызов, режим могут остановить или перезапустить — после ответа
        модели клик делается только если поколение ещё актуально.
        """
        if not self.roi:
            self.log("❌ Сначала выделите область с тестом!")
            return False

        rx, ry, rw, rh = self.roi
        self.log(f"📸 Захват области {rw}x{rh}...")

        prep_t0 = time.time()
        img = self.capture_roi_image()
        if not img:
            self.log("❌ Не удалось захватить снимок экрана.")
            return False

        # Подготовка кадра: даунскейл до 1536 px по большей стороне + JPEG q80 —
        # payload меньше в ~2.4 раза, vision-токенов в ~3.5 раза меньше;
        # модель отвечает в координатах 0-1000 относительно картинки,
        # поэтому пересчёт клика в пиксели ROI не меняется.
        api_img = agent_speed.downscale_for_api(img)
        img_bytes = agent_speed.encode_jpeg(api_img)
        prep_s = time.time() - prep_t0

        self.log(f"🧠 Отправка в {self._ui_snapshot['model']}...")
        start_t = time.time()
        try:
            result = self.call_vision_api(img_bytes)
        except Exception as e:
            self.log(f"❌ Ошибка ИИ: {e}")
            return False

        elapsed = time.time() - start_t

        # API-ответ шёл секундами — за это время режим могли остановить или
        # перезапустить; устаревшему/остановленному воркеру кликать нельзя
        if generation is not None and self.should_abort(generation):
            self.log("⏭️ Ответ ИИ получен, но авто-режим уже остановлен/перезапущен — клик пропущен.")
            return False

        thought = result.get("thought", "")
        action = result.get("action", "click")

        self.log(f"💡 ИИ ({elapsed:.1f}с API, {prep_s:.2f}с подготовка): {thought}")

        if action == "done":
            self.log("🏁 ИИ сообщает: Тест завершен!")
            return "done"

        # Сопоставление пар: модель может тянуть линию (drag) вместо одиночного клика
        drag = result.get("drag") if action == "drag" else None
        if action == "drag" and drag is None:
            self.log("❌ Модель вернула action='drag' без координат x1,y1,x2,y2 — шаг пропущен.")
            return False

        def column_of(local_x):
            return "left" if local_x * 2 < rw else "right"

        if drag is not None:
            # Тянем линию от x1,y1 к x2,y2 (задания-сопоставления с перетаскиванием)
            rel_x1, rel_y1 = resolve_click_scale(drag[0], drag[1])
            rel_x2, rel_y2 = resolve_click_scale(drag[2], drag[3])
            local_from = (int(rel_x1 * rw), int(rel_y1 * rh))
            local_to = (int(rel_x2 * rw), int(rel_y2 * rh))
            screen_pts = []
            for lx, ly in (local_from, local_to):
                sx = max(rx + 2, min(rx + rw - 2, rx + lx))
                sy = max(ry + 2, min(ry + rh - 2, ry + ly))
                screen_pts.append((sx, sy))
            (screen_x1, screen_y1), (screen_x2, screen_y2) = screen_pts
            for sx, sy in screen_pts:
                if agent_window.point_in_tool_window(sx, sy):
                    self.log(
                        f"⚠️ Точка перетаскивания ({sx}, {sy}) попадает в окно программы "
                        f"{agent_window.tool_window_rect()} — шаг пропущен, чтобы не кликнуть в себя."
                    )
                    return False

            # Превью: линия между точками
            self.root.after(0, lambda lf=local_from, lt=local_to, im=img: self.capture_and_update_preview(lf, lt, base_img=im))

            self.log(f"👉 Перетаскивание: ({screen_x1}, {screen_y1}) -> ({screen_x2}, {screen_y2})")
            pyautogui.moveTo(screen_x1, screen_y1)
            time.sleep(0.08)
            pyautogui.mouseDown()
            try:
                if self._ui_snapshot["smooth_mouse"]:
                    pyautogui.moveTo(screen_x2, screen_y2, duration=0.35, tween=pyautogui.easeInOutQuad)
                else:
                    pyautogui.moveTo(screen_x2, screen_y2, duration=0.15)
            finally:
                pyautogui.mouseUp()

            # Общая пост-обработка (сравнение кадров, история, зависание) — по конечной точке
            local_click_x, local_click_y = local_to
            screen_x, screen_y = screen_x2, screen_y2
            hist_text = (
                f"dragged ({drag[0]:.0f}, {drag[1]:.0f}) [{column_of(local_from[0])} column] "
                f"-> ({drag[2]:.0f}, {drag[3]:.0f}) [{column_of(local_to[0])} column]"
            )
        else:
            if result["x"] is None or result["y"] is None:
                self.log("❌ Модель вернула ответ без координат x/y — шаг пропущен.")
                return False
            norm_x, norm_y = result["x"], result["y"]

            # Пересчет координат ответа модели в пиксели экрана (см. resolve_click_scale:
            # прежняя эвристика «обе <= 100 => проценты» ломала валидные ответы 0-1000
            # из левого верхнего угла — (80, 40) читалось как 80%x40%, клик улетал в центр)
            rel_x, rel_y = resolve_click_scale(norm_x, norm_y)

            local_click_x = int(rel_x * rw)
            local_click_y = int(rel_y * rh)

            screen_x = rx + local_click_x
            screen_y = ry + local_click_y

            # Защита от вылета за пределы ROI
            screen_x = max(rx + 2, min(rx + rw - 2, screen_x))
            screen_y = max(ry + 2, min(ry + rh - 2, screen_y))

            # Детект самоклика: модель показала в наше собственное окно —
            # не кликаем (можно было бы нажать собственную кнопку СТОП), шаг пропускаем
            if agent_window.point_in_tool_window(screen_x, screen_y):
                self.log(
                    f"⚠️ Точка ({screen_x}, {screen_y}) попадает в окно программы "
                    f"{agent_window.tool_window_rect()} — шаг пропущен, чтобы не кликнуть в себя."
                )
                return False

            # Обновляем превью с точкой клика
            self.root.after(0, lambda pt=(local_click_x, local_click_y), im=img: self.capture_and_update_preview(pt, base_img=im))

            self.log(f"👉 Клик в точку: ({screen_x}, {screen_y})")

            # Перемещение и клик мышкой
            if self._ui_snapshot["smooth_mouse"]:
                pyautogui.moveTo(screen_x, screen_y, duration=0.35, tween=pyautogui.easeInOutQuad)
            else:
                pyautogui.moveTo(screen_x, screen_y)

            time.sleep(0.08)
            pyautogui.click()
            hist_text = f"clicked ({norm_x:.0f}, {norm_y:.0f}) [{column_of(local_click_x)} column]"

        # Прогресс после клика: сравниваем кадр ДО клика с текущим экраном.
        # ImageGrab курсор не захватывает, так что смещение мыши не мешает.
        time.sleep(0.5)  # даём странице перерисоваться после клика
        screen_changed = False
        try:
            after_img = self.capture_roi_image()
            if after_img is not None:
                changed_px, total_px = agent_progress.diff_stats(img, after_img)
                screen_changed = changed_px >= agent_progress.MIN_CHANGED_PIXELS
                self.log(
                    f"📊 Экран после клика {'изменился' if screen_changed else 'НЕ изменился'} "
                    f"(различающихся пикселей: {changed_px} из {total_px}, порог {agent_progress.MIN_CHANGED_PIXELS})."
                )
        except Exception as e:
            self.log(f"⚠️ Не удалось сравнить кадры до/после клика: {e}")

        # Короткая история для промпта: модель увидит её на следующем шаге
        self._action_history.append({"text": hist_text, "changed": screen_changed})

        # Детект зависания: та же точка + неменяющийся экран N шагов подряд.
        # 'repeat' — только предупреждение (кнопка «Далее» на одном месте —
        # легитимный случай); авто-режим останавливаем исключительно на 'stalled'.
        stagnation_pt = (screen_x1, screen_y1, screen_x2, screen_y2) if drag is not None else (screen_x, screen_y)
        status = self._stagnation.register(stagnation_pt, screen_changed)
        if status == agent_progress.STATUS_STALLED:
            self.log(
                f"🛑 Зависание: {self._stagnation.same_target_count} шага подряд в точку "
                f"({screen_x}, {screen_y}), экран не меняется — останавливаю авто-режим. "
                f"Возможно, ответ уже выбран (нажмите «Далее» вручную) или окно программы "
                f"перекрывает область теста."
            )
            if self.is_running:
                self.root.after(0, self.stop)
        elif status == agent_progress.STATUS_REPEAT:
            self.log(
                f"⚠️ Та же точка {self._stagnation.same_target_count} шаг(ов) подряд: "
                f"({screen_x}, {screen_y}). Модель получит подсказку нажать «Далее»."
            )

        return True

    def do_single_step(self):
        """Выполнить один шаг вручную в отдельном потоке."""
        if self.is_running or self._single_step_running:
            return
        threading.Thread(target=self._run_single_step_thread, daemon=True).start()

    def _run_single_step_thread(self):
        self._single_step_running = True  # СТАРТ блокируется, пока идёт одиночный шаг
        self.step_btn.config(state="disabled")
        try:
            self.execute_one_cycle()
        finally:
            self._single_step_running = False
            self.root.after(0, lambda: self.step_btn.config(state="normal"))

    def toggle_start(self):
        if self.is_running:
            self.stop()
        else:
            self.start()

    def start(self):
        if not self.roi:
            messagebox.showwarning("Внимание", "Сначала выделите область с тестом на экране!")
            return
        if not self.api_key_var.get().strip():
            messagebox.showwarning("Внимание", "Укажите Gemini API Key вверху окна!")
            return
        if self._single_step_running:
            self.log("⏳ Дождитесь окончания одиночного шага — СТАРТ пока заблокирован.")
            return

        self.is_running = True
        self.stop_requested = False
        self._stagnation.reset()       # детект зависания начинает с чистого листа
        self._action_history.clear()   # история кликов для промпта — тоже
        self._worker_generation += 1   # воркеры предыдущего поколения выйдут сами
        self._set_start_button(True)
        self.select_btn.config(state="disabled")
        self.step_btn.config(state="disabled")
        self.log("▶ Авто-режим запущен. Для остановки нажмите СТОП или дерните мышь в угол.")

        generation = self._worker_generation
        self.worker_thread = threading.Thread(target=self._loop_worker, args=(generation,), daemon=True)
        self.worker_thread.start()

    def stop(self):
        self.stop_requested = True
        self.is_running = False
        self._set_start_button(False)
        self.select_btn.config(state="normal")
        self.step_btn.config(state="normal")
        self.log("⏹ Авто-режим остановлен.")

    def stop_if_running(self):
        if self.is_running:
            self.stop()

    def _loop_worker(self, generation):
        step_count = 0
        try:
            while not self.should_abort(generation):
                step_count += 1
                self.log(f"--- Шаг {step_count} ---")
                res = self.execute_one_cycle(generation)

                if res == "done" or self.should_abort(generation):
                    break
                if not res:
                    self.log("⚠️ Шаг не удался, повторная попытка через паузу...")

                # Ожидание задержки
                delay = max(0.5, float(self._ui_snapshot["delay"]))
                for _ in range(int(delay * 10)):
                    if self.stop_requested:
                        break
                    time.sleep(0.1)
        except pyautogui.FailSafeException:
            self.log("🛑 Сработал PyAutoGUI FailSafe (курсор в углу экрана)!")
        except Exception as exc:
            self.log(f"🛑 Критическая ошибка в цикле: {exc}")
        finally:
            # Кнопки сбрасывает только актуальный воркер: устаревший (после быстрого
            # СТОП+СТАРТ) не должен глушить уже новый запущенный режим
            if generation == self._worker_generation:
                self.root.after(0, self.stop)


def main():
    root = tk.Tk()
    app = AIScreenAgentApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
