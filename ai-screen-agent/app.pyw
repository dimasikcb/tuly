"""
AI Screen Bridge (Computer Use) - Autonomous Test Solver
Скрипт-мост: дает Gemini глаза (захват области экрана) и руки (клики мышкой).
"""

import sys
import os
import json
import time
import io
import threading
import queue
import base64
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

CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")


def ensure_interactive_desktop():
    """Переключает поток на интерактивный рабочий стол Windows, если запущен в изолированной среде."""
    if sys.platform == "win32":
        try:
            user32 = ctypes.windll.user32
            h_def = user32.OpenDesktopW("Default", 0, False, 0x01FF)
            if h_def:
                user32.SetThreadDesktop(h_def)
        except Exception:
            pass


class ScreenSelector(tk.Toplevel):
    """Полноэкранный полупрозрачный оверлей для выделения области мышкой."""
    def __init__(self, parent, callback):
        super().__init__(parent)
        self.callback = callback
        self.attributes("-fullscreen", True)
        self.attributes("-topmost", True)
        self.attributes("-alpha", 0.35)
        self.configure(bg="#111111")
        self.config(cursor="cross")

        self.start_x = None
        self.start_y = None
        self.current_rect = None

        self.canvas = tk.Canvas(self, cursor="cross", bg="#111111", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)

        # Текст подсказки
        screen_w = self.winfo_screenwidth()
        self.hint = self.canvas.create_text(
            screen_w // 2, 40,
            text="Зажмите ЛКМ и выделите рамку с тестом. Нажмите Esc для отмены.",
            fill="yellow", font=("Arial", 16, "bold")
        )

        self.canvas.bind("<ButtonPress-1>", self.on_press)
        self.canvas.bind("<B1-Motion>", self.on_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_release)
        self.bind("<Escape>", lambda e: self.cancel())

    def on_press(self, event):
        self.start_x = event.x
        self.start_y = event.y
        if self.current_rect:
            self.canvas.delete(self.current_rect)
        self.current_rect = self.canvas.create_rectangle(
            self.start_x, self.start_y, self.start_x, self.start_y,
            outline="#00ff66", width=3, fill="#00ff66"
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
        self.root.geometry("620x760")
        self.root.minsize(560, 680)

        # Переменные состояния
        self.roi = None  # (x, y, w, h)
        self.is_running = False
        self.stop_requested = False
        self.worker_thread = None
        self.log_queue = queue.Queue()
        self.last_preview_img = None

        # Загрузка конфига
        self.config = self.load_config()

        # Создание интерфейса
        self.setup_ui()

        # Восстановление сохраненного ROI
        roi_saved = self.config.get("roi")
        if roi_saved and isinstance(roi_saved, (list, tuple)) and len(roi_saved) == 4:
            self.roi = tuple(roi_saved)
            self.update_roi_label()

        # Поддержка горячих клавиш буфера в любой раскладке
        self.setup_clipboard_shortcuts()

        # Запуск цикла обработки очереди логов
        self.root.after(100, self.process_log_queue)

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
        style = ttk.Style()
        style.theme_use("clam")

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
        self.roi_label = ttk.Label(roi_top_frame, textvariable=self.roi_label_var, font=("Consolas", 10, "bold"), foreground="#0066cc")
        self.roi_label.pack(side="left", fill="x", expand=True)

        # Превью кадра
        self.preview_frame = ttk.Frame(roi_group)
        self.preview_frame.pack(fill="x", pady=(8, 0))
        self.preview_canvas = tk.Canvas(self.preview_frame, height=130, bg="#222222", highlightthickness=1, highlightbackground="#888")
        self.preview_canvas.pack(fill="x")
        self.preview_text = self.preview_canvas.create_text(
            280, 65, text="Здесь появится снимок выделенной области", fill="#aaaaaa", font=("Arial", 10)
        )

        # 3. Секция Руки (Управление)
        ctrl_group = ttk.LabelFrame(main_frame, text="  3. Руки (Управление и клики)  ", padding=10)
        ctrl_group.pack(fill="x", pady=(0, 8))

        ctrl_btns_frame = ttk.Frame(ctrl_group)
        ctrl_btns_frame.pack(fill="x")

        self.start_btn = tk.Button(
            ctrl_btns_frame, text="▶ СТАРТ (Авто-прохождение)", bg="#28a745", fg="white",
            font=("Arial", 11, "bold"), padx=12, pady=6, command=self.toggle_start
        )
        self.start_btn.pack(side="left", padx=(0, 10))

        self.step_btn = tk.Button(
            ctrl_btns_frame, text="⚡ Шаг (1 клик)", bg="#007bff", fg="white",
            font=("Arial", 10, "bold"), padx=10, pady=6, command=self.do_single_step
        )
        self.step_btn.pack(side="left", padx=(0, 15))

        self.always_on_top_var = tk.BooleanVar(value=self.config.get("always_on_top", True))
        top_chk = ttk.Checkbutton(ctrl_btns_frame, text="Поверх всех окон", variable=self.always_on_top_var, command=self.apply_always_on_top)
        top_chk.pack(side="left", padx=(0, 8))
        self.apply_always_on_top()

        self.smooth_mouse_var = tk.BooleanVar(value=self.config.get("smooth_mouse", True))
        smooth_chk = ttk.Checkbutton(ctrl_btns_frame, text="Плавный клик", variable=self.smooth_mouse_var)
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

        self.log_text = tk.Text(log_group, wrap="word", font=("Consolas", 9), bg="#1e1e1e", fg="#e0e0e0", insertbackground="white")
        log_scroll = ttk.Scrollbar(log_group, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)

        self.log_text.pack(side="left", fill="both", expand=True)
        log_scroll.pack(side="right", fill="y")

        # Привязка клавиши Esc для экстренной остановки
        self.root.bind("<Escape>", lambda e: self.stop_if_running())

        # Приветственное сообщение
        self.log("🚀 AI Screen Bridge готов к работе.")
        self.log("1. Вставьте ваш Gemini API Key (бесплатно в Google AI Studio).")
        self.log("2. Нажмите 'Выделить область мышкой' и обведите окно с тестом.")
        self.log("3. Нажмите 'СТАРТ' — ИИ будет анализировать экран и нажимать ответы.")
        self.log("⚠️ Аварийная остановка: дерните мышь в угол экрана или нажмите СТОП / Esc.")

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
        """Захватывает выбранную область экрана в формате PIL.Image."""
        if not self.roi:
            return None
        ensure_interactive_desktop()
        x, y, w, h = self.roi
        bbox = (x, y, x + w, y + h)
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

    def capture_and_update_preview(self, click_pt=None):
        """Обновляет холст превью с опциональной красной точкой клика."""
        img = self.capture_roi_image()
        if not img:
            return

        canvas_w = self.preview_canvas.winfo_width() or 580
        canvas_h = self.preview_canvas.winfo_height() or 130

        # Если передан клик, рисуем яркую точку
        if click_pt:
            draw = ImageDraw.Draw(img)
            cx, cy = click_pt
            r = max(5, int(min(img.width, img.height) * 0.02))
            draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill="#ff0000", outline="#ffffff", width=2)

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
        api_key = self.api_key_var.get().strip()
        model_name = self.model_var.get().strip() or "free/gemini-3.8-flash"
        custom_hint = self.instruction_var.get().strip()
        base_url = self.config.get("base_url", "").strip()

        if not api_key:
            raise ValueError("Не указан API Key! Введите ключ вверху окна.")

        # Автоопределение провайдера
        is_google_native = api_key.startswith("AIzaSy") or api_key.startswith("AIza")
        is_openai_compat = api_key.startswith("sk-") or bool(base_url)

        if not base_url:
            if api_key.startswith("sk-apx"):
                base_url = "https://api.apinex.bond/v1"
            elif api_key.startswith("sk-or"):
                base_url = "https://openrouter.ai/api/v1"
            elif not is_google_native:
                base_url = "https://api.apinex.bond/v1"

        system_instruction = (
            "You are an autonomous AI test-solving assistant with computer vision. "
            "You are looking at a screenshot of a test, quiz, exam, or web form. "
            "Your tasks:\n"
            "1. Read and understand the question and all answer options.\n"
            "2. Determine the correct answer.\n"
            "3. If an answer option needs to be selected, target the radio button, checkbox, or option text.\n"
            "4. If the answer is already selected or if there is a 'Next' / 'Далее' / 'Submit' / 'Ответить' / 'Продолжить' button required to advance, target that button.\n"
            "5. If the test is completely finished (score / results page shown), set action='done'.\n"
            "Coordinates:\n"
            "Return target coordinates 'x' and 'y' as integers normalized from 0 to 1000 relative to this image. "
            "(x=0 is leftmost, x=1000 is rightmost, y=0 is top, y=1000 is bottom).\n"
            "Output MUST be valid JSON only."
        )

        prompt = (
            "Analyze the screenshot of the test.\n"
            "Return JSON in this exact structure:\n"
            "{\n"
            '  "thought": "Short explanation: Question is X, correct answer is Y / Clicking Next button",\n'
            '  "action": "click",\n'
            '  "x": <0-1000>,\n'
            '  "y": <0-1000>\n'
            "}\n"
        )
        if custom_hint:
            prompt += f"\nUser extra hint: {custom_hint}\n"

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
            res = requests.post(url, headers=headers, json=payload, timeout=35)
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
        res = requests.post(url, json=payload, timeout=35)
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

        if x is None or y is None:
            raise ValueError(f"Ответ модели не содержит координат: {data}")

        return {
            "thought": data.get("thought", "Нет описания"),
            "action": data.get("action", "click"),
            "x": x,
            "y": y
        }

    def execute_one_cycle(self):
        """Выполняет один цикл: снимок -> анализ -> клик."""
        if not self.roi:
            self.log("❌ Сначала выделите область с тестом!")
            return False

        rx, ry, rw, rh = self.roi
        self.log(f"📸 Захват области {rw}x{rh}...")

        img = self.capture_roi_image()
        if not img:
            self.log("❌ Не удалось захватить снимок экрана.")
            return False

        # Конвертация в JPEG bytes
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        img_bytes = buf.getvalue()

        self.log(f"🧠 Отправка в {self.model_var.get()}...")
        start_t = time.time()
        try:
            result = self.call_vision_api(img_bytes)
        except Exception as e:
            self.log(f"❌ Ошибка ИИ: {e}")
            return False

        elapsed = time.time() - start_t
        thought = result.get("thought", "")
        action = result.get("action", "click")
        norm_x = result["x"]
        norm_y = result["y"]

        self.log(f"💡 ИИ ({elapsed:.1f}с): {thought}")

        if action == "done":
            self.log("🏁 ИИ сообщает: Тест завершен!")
            return "done"

        # Пересчет координат из 0-1000 в пиксели экрана
        if norm_x <= 1.0 and norm_y <= 1.0:
            rel_x, rel_y = norm_x, norm_y
        elif norm_x <= 100.0 and norm_y <= 100.0 and (rw > 100 or rh > 100):
            rel_x, rel_y = norm_x / 100.0, norm_y / 100.0
        else:
            rel_x, rel_y = norm_x / 1000.0, norm_y / 1000.0

        local_click_x = int(rel_x * rw)
        local_click_y = int(rel_y * rh)

        screen_x = rx + local_click_x
        screen_y = ry + local_click_y

        # Защита от вылета за пределы ROI
        screen_x = max(rx + 2, min(rx + rw - 2, screen_x))
        screen_y = max(ry + 2, min(ry + rh - 2, screen_y))

        # Обновляем превью с точкой клика
        self.root.after(0, lambda: self.capture_and_update_preview((local_click_x, local_click_y)))

        self.log(f"👉 Клик в точку: ({screen_x}, {screen_y})")

        # Перемещение и клик мышкой
        smooth = self.smooth_mouse_var.get()
        if smooth:
            pyautogui.moveTo(screen_x, screen_y, duration=0.35, tween=pyautogui.easeInOutQuad)
        else:
            pyautogui.moveTo(screen_x, screen_y)

        time.sleep(0.08)
        pyautogui.click()
        return True

    def do_single_step(self):
        """Выполнить один шаг вручную в отдельном потоке."""
        if self.is_running:
            return
        threading.Thread(target=self._run_single_step_thread, daemon=True).start()

    def _run_single_step_thread(self):
        self.step_btn.config(state="disabled")
        try:
            self.execute_one_cycle()
        finally:
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

        self.is_running = True
        self.stop_requested = False
        self.start_btn.config(text="⏹ СТОП", bg="#dc3545")
        self.select_btn.config(state="disabled")
        self.step_btn.config(state="disabled")
        self.log("▶ Авто-режим запущен. Для остановки нажмите СТОП или дерните мышь в угол.")

        self.worker_thread = threading.Thread(target=self._loop_worker, daemon=True)
        self.worker_thread.start()

    def stop(self):
        self.stop_requested = True
        self.is_running = False
        self.start_btn.config(text="▶ СТАРТ (Авто)", bg="#28a745")
        self.select_btn.config(state="normal")
        self.step_btn.config(state="normal")
        self.log("⏹ Авто-режим остановлен.")

    def stop_if_running(self):
        if self.is_running:
            self.stop()

    def _loop_worker(self):
        step_count = 0
        while not self.stop_requested:
            step_count += 1
            self.log(f"--- Шаг {step_count} ---")
            res = self.execute_one_cycle()

            if res == "done" or self.stop_requested:
                break
            if not res:
                self.log("⚠️ Шаг не удался, повторная попытка через паузу...")

            # Ожидание задержки
            delay = max(0.5, float(self.delay_var.get()))
            for _ in range(int(delay * 10)):
                if self.stop_requested:
                    break
                time.sleep(0.1)

        self.root.after(0, self.stop)


def main():
    root = tk.Tk()
    app = AIScreenAgentApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
