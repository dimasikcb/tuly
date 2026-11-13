#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Вкладка «AI-кликер» хаба: панель AutoClickerPanel и оверлей OverlaySelector.

Переехала из tools.pyw verbatim, логика не менялась:
  * OverlaySelector — полупрозрачный слой на весь экран: клик = точка,
    протяжка = область;
  * CV-движок (ленивые зависимости, шаги, поиск по картинке) — в clicker_core;
    мутируемые глобалы движка читаются ТОЛЬКО через атрибут модуля
    (clicker_core.HAS_DEPS / clicker_core.pyautogui / …), чтобы после
    clicker_core.load_autoclicker_deps() панель всегда видела свежие значения;
  * кнопка «🤖 AI Агент» открывает соседнюю панель через hub.show("ai_agent").

Модуль знает только uibase/clicker_core/winbase; конкретный хаб ему не нужен —
общение через hub-мост базовой Panel.
"""

from __future__ import annotations

import json
import queue
import random
import sys
import threading
import time
import traceback
from dataclasses import replace
from pathlib import Path

import tkinter as tk
from tkinter import ttk

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from uibase import (  # noqa: E402
    BG,
    OVERLAY_BACKDROP,
    Panel,
    enable_dark_titlebar,
    flat_button,
    messagebox,
    mono_font,
    px,
    register_palette_consumer,
    tc,
    ui_font,
)

# Токены приходят реэкспортом; регистрируем глобалы этого модуля потребителем
# палитры, чтобы живая смена темы обновляла и их (панель читает голые имена).
register_palette_consumer(globals())

from winbase import APP_DIR, CONFIG_PATH, TARGETS_DIR, config_read, config_write  # noqa: E402
import clicker_core  # noqa: E402
from clicker_core import (  # noqa: E402
    BUTTONS,
    KIND_NAMES,
    Step,
    find_template,
    grab_screen,
    virtual_screen,
)


class OverlaySelector:
    """Полупрозрачный слой на весь экран: клик = точка, протяжка = область."""

    def __init__(self, master: tk.Misc, mode: str, on_done, on_cancel):
        self.mode = mode
        self.on_done = on_done
        self.on_cancel = on_cancel
        self.vx, self.vy, self.vw, self.vh = virtual_screen()
        self.start: tuple[int, int] | None = None
        self.closed = False

        self.win = tk.Toplevel(master)
        self.win.overrideredirect(True)
        self.win.geometry(f"{self.vw}x{self.vh}+{self.vx}+{self.vy}")
        self.win.configure(bg=OVERLAY_BACKDROP)
        self.win.attributes("-topmost", True)
        try:
            self.win.attributes("-alpha", 0.35)
        except tk.TclError:
            pass

        self.canvas = tk.Canvas(self.win, bg=OVERLAY_BACKDROP, highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)

        hint = "Кликните, чтобы поставить точку" if mode == "point" \
            else "Протяните мышь, чтобы выделить область"
        self.canvas.create_text(
            self.vw // 2, px(46), text=f"{hint}   ·   Esc / ПКМ — отмена",
            fill=tc("roi"), font=ui_font(15, "bold"),
        )
        self.rect = self.canvas.create_rectangle(
            0, 0, 0, 0, outline=tc("roi"), width=3, state="hidden"
        )
        self.size_text = self.canvas.create_text(
            0, 0, text="", fill=tc("roi"), font=ui_font(11, "bold"), state="hidden"
        )
        self.info = self.canvas.create_text(
            0, 0, text="", fill=tc("overlay_timer"), font=mono_font(11, "bold"), anchor="nw"
        )
        self.cross_h = self.canvas.create_line(
            0, 0, 0, 0, fill=tc("roi"), dash=(4, 4), state="hidden"
        )
        self.cross_v = self.canvas.create_line(
            0, 0, 0, 0, fill=tc("roi"), dash=(4, 4), state="hidden"
        )

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Motion>", self._on_move)
        self.canvas.bind("<ButtonPress-3>", lambda _e: self._cancel())
        self.win.bind("<Escape>", lambda _e: self._cancel())

        self.win.focus_force()
        self.win.lift()

    def _on_move(self, event) -> None:
        ax, ay = event.x + self.vx, event.y + self.vy
        self.canvas.coords(self.info, event.x + 16, event.y + 16)
        self.canvas.itemconfigure(self.info, text=f"{ax}, {ay}")
        if self.mode == "point" and self.start is None:
            self.canvas.coords(self.cross_h, 0, event.y, self.vw, event.y)
            self.canvas.coords(self.cross_v, event.x, 0, event.x, self.vh)
            self.canvas.itemconfigure(self.cross_h, state="normal")
            self.canvas.itemconfigure(self.cross_v, state="normal")

    def _on_press(self, event) -> None:
        if self.mode == "point":
            self._finish(event.x + self.vx, event.y + self.vy, 0, 0)
            return
        self.start = (event.x, event.y)
        self.canvas.itemconfigure(self.rect, state="normal")
        self.canvas.itemconfigure(self.size_text, state="normal")
        self.canvas.coords(self.rect, event.x, event.y, event.x, event.y)

    def _on_drag(self, event) -> None:
        if self.start is None:
            return
        x0, y0 = self.start
        x1, y1 = event.x, event.y
        self.canvas.coords(self.rect, x0, y0, x1, y1)
        self.canvas.coords(self.size_text, min(x0, x1) + 6, min(y0, y1) - 16)
        self.canvas.itemconfigure(self.size_text, text=f"{abs(x1 - x0)} × {abs(y1 - y0)}")

    def _on_release(self, event) -> None:
        if self.start is None:
            return
        x0, y0 = self.start
        x1, y1 = event.x, event.y
        if abs(x1 - x0) < 6 or abs(y1 - y0) < 6:
            self._finish(event.x + self.vx, event.y + self.vy, 0, 0)
            return
        self._finish(
            min(x0, x1) + self.vx, min(y0, y1) + self.vy, abs(x1 - x0), abs(y1 - y0)
        )

    def _finish(self, x: int, y: int, w: int, h: int) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.win.destroy()
        finally:
            self.on_done(x, y, w, h)

    def _cancel(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.win.destroy()
        finally:
            self.on_cancel()

# --------------------------------------------------------------------------- #
#  AutoClicker
# --------------------------------------------------------------------------- #
class AutoClickerPanel(Panel):
    def __init__(self, master: tk.Misc, hub) -> None:
        clicker_core.load_autoclicker_deps()  # тяжёлый CV-стек — только при открытии этой вкладки
        super().__init__(master, hub)
        self.steps: list[Step] = []
        self.running = False
        self.stop_evt = threading.Event()
        self.events: queue.Queue = queue.Queue()
        self.tpl_cache: dict = {}

        self._build_ui()
        self.load_config()
        self._setup_hotkeys()
        self.after(80, self._poll)
        self.log("Готов к работе. Добавьте шаг: точку, область или картинку.")
        if not clicker_core.HAS_DEPS:
            self.log("Не установлены зависимости — запуск недоступен.")
        elif not clicker_core.HAS_KEYBOARD:
            self.log("Горячие клавиши недоступны (pip install keyboard).")

    # ------------------------------------------------------------------ UI --
    def _build_ui(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        for text, command, width in (
            ("+ Точка", self.add_point, 11),
            ("+ Область", self.add_area, 12),
            ("+ Картинка", self.add_image, 12),
        ):
            ttk.Button(bar, text=text, command=command, width=width).pack(side="left", padx=(0, 6))
        ttk.Button(bar, text="Удалить", command=self.delete_selected, width=10).pack(side="left", padx=(12, 0))
        ttk.Button(bar, text="↑", command=lambda: self.move_selected(-1), width=3).pack(side="left", padx=(px(6), 0))
        ttk.Button(bar, text="↓", command=lambda: self.move_selected(1), width=3).pack(side="left", padx=(4, 0))
        ttk.Button(bar, text="Очистить", command=self.clear_steps, width=10).pack(side="left", padx=(px(6), 0))
        flat_button(bar, "🤖 AI Агент", lambda: self.hub.show("ai_agent"), kind="ghost").pack(side="left", padx=(12, 0))

        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill="both", expand=True, pady=(px(10), 0))
        columns = ("num", "kind", "target", "delay")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=8, selectmode="browse")
        self.tree.heading("num", text="№")
        self.tree.heading("kind", text="Тип")
        self.tree.heading("target", text="Куда кликать")
        self.tree.heading("delay", text="Задержка, мс")
        self.tree.column("num", width=px(44), anchor="center", stretch=False)
        self.tree.column("kind", width=px(90), anchor="center", stretch=False)
        self.tree.column("target", width=px(430), anchor="w")
        self.tree.column("delay", width=px(110), anchor="center", stretch=False)
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self.edit_selected)
        self.tree.bind("<Delete>", lambda _e: self.delete_selected())

        settings = ttk.LabelFrame(self, text="Настройки", padding=px(10))
        settings.pack(fill="x", pady=(px(10), 0))

        self.var_interval = tk.StringVar(value="500")
        self.var_jitter = tk.StringVar(value="0")
        self.var_repeats = tk.StringVar(value="0")
        self.var_wait = tk.StringVar(value="0")
        self.var_button = tk.StringVar(value="ЛКМ")

        ttk.Label(settings, text="Интервал между шагами, мс:").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(settings, from_=0, to=600000, width=9, textvariable=self.var_interval).grid(
            row=0, column=1, sticky="w", padx=(px(6), px(18)))
        ttk.Label(settings, text="Разброс, ±мс:").grid(row=0, column=2, sticky="w")
        ttk.Spinbox(settings, from_=0, to=60000, width=7, textvariable=self.var_jitter).grid(
            row=0, column=3, sticky="w", padx=(px(6), px(18)))
        ttk.Label(settings, text="Повтор цикла (0 = ∞):").grid(row=0, column=4, sticky="w")
        ttk.Spinbox(settings, from_=0, to=1000000, width=7, textvariable=self.var_repeats).grid(
            row=0, column=5, sticky="w", padx=(px(6), 0))
        ttk.Label(settings, text="Кнопка мыши:").grid(row=1, column=0, sticky="w", pady=(px(8), 0))
        ttk.Combobox(settings, values=list(BUTTONS), width=7, state="readonly",
                     textvariable=self.var_button).grid(
            row=1, column=1, sticky="w", padx=(px(6), px(18)), pady=(px(8), 0))
        ttk.Label(settings, text="Ждать картинку, сек:").grid(row=1, column=2, sticky="w", pady=(px(8), 0))
        ttk.Spinbox(settings, from_=0, to=3600, width=7, textvariable=self.var_wait).grid(
            row=1, column=3, sticky="w", padx=(px(6), px(18)), pady=(px(8), 0))
        ttk.Label(settings, text="Двойной клик по шагу — свои задержка/порог",
                  style="Muted.TLabel").grid(row=1, column=4, columnspan=2, sticky="w", pady=(px(8), 0))

        run_bar = ttk.Frame(self)
        run_bar.pack(fill="x", pady=(px(12), 0))
        self.btn_start = ttk.Button(run_bar, text="▶  Старт (F6)", command=self.toggle_run, width=16)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(run_bar, text="⏹  Стоп", command=self.stop_run,
                                   width=10, state="disabled")
        self.btn_stop.pack(side="left", padx=(px(8), 0))
        self.status_var = tk.StringVar(value="Готов")
        ttk.Label(run_bar, textvariable=self.status_var).pack(side="left", padx=px(16))

        log_frame, self.log_box = self.make_log(self, height=6)
        log_frame.pack(fill="both", expand=True, pady=(px(10), 0))

    # ------------------------------------------------------------- журнал --
    def log(self, text: str) -> None:
        self.events.put(("log", text))

    def _append_log(self, text: str) -> None:
        self.append_log(self.log_box, text)

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "status":
                    self.status_var.set(payload)
                elif kind == "done":
                    self._on_run_finished()
                elif kind == "toggle":
                    self.toggle_run()
                elif kind == "point_at_cursor":
                    self.add_point_at_cursor()
        except queue.Empty:
            pass
        self.after(80, self._poll)

    # -------------------------------------------------------------- шаги ----
    def refresh_tree(self) -> None:
        selected = self._selected_index()
        self.tree.delete(*self.tree.get_children())
        for i, step in enumerate(self.steps):
            delay = f"{step.delay}" if step.delay else "—"
            self.tree.insert("", "end", iid=str(i),
                             values=(i + 1, KIND_NAMES.get(step.kind, step.kind),
                                     self._describe(step), delay))
        if selected is not None and 0 <= selected < len(self.steps):
            self.tree.selection_set(str(selected))
            self.tree.focus(str(selected))

    @staticmethod
    def _describe(step: Step) -> str:
        if step.kind == "point":
            return f"({step.x}, {step.y})"
        if step.kind == "area":
            return f"({step.x}, {step.y})   {step.w}×{step.h}"
        name = Path(step.path).name or "?"
        return f"{name}   ·   порог {step.conf:.0%}"

    def _selected_index(self) -> int | None:
        selection = self.tree.selection()
        if not selection:
            return None
        try:
            return int(selection[0])
        except ValueError:
            return None

    def _pick(self, mode: str, handler) -> None:
        if not clicker_core.HAS_DEPS:
            messagebox.showerror("AutoClicker", "Не установлены зависимости.\n\n" + clicker_core.DEPS_ERROR)
            return
        top = self.winfo_toplevel()
        top.iconify()

        def start() -> None:
            top.update_idletasks()
            try:
                shot = grab_screen()
            except Exception as exc:  # noqa: BLE001
                top.deiconify()
                self.log(f"Не удалось снять экран: {exc}")
                return

            def done(x: int, y: int, w: int, h: int) -> None:
                top.deiconify()
                top.lift()
                handler(x, y, w, h, shot)

            def cancel() -> None:
                top.deiconify()
                top.lift()

            OverlaySelector(self, mode, done, cancel)

        self.after(300, start)

    # -- добавление шагов ------------------------------------------------- #
    def add_point(self) -> None:
        self._pick("point", self._add_point_done)

    def _add_point_done(self, x: int, y: int, _w: int, _h: int, _shot) -> None:
        self.steps.append(Step(kind="point", x=x, y=y))
        self.refresh_tree()
        self.save_config()
        self.log(f"Добавлена точка ({x}, {y})")

    def add_point_at_cursor(self) -> None:
        if not clicker_core.HAS_DEPS:
            return
        try:
            x, y = clicker_core.pyautogui.position()
        except Exception:  # noqa: BLE001
            return
        self.steps.append(Step(kind="point", x=int(x), y=int(y)))
        self.refresh_tree()
        self.save_config()
        self.log(f"Добавлена точка под курсором ({int(x)}, {int(y)})")

    def add_area(self) -> None:
        self._pick("rect", self._add_area_done)

    def _add_area_done(self, x: int, y: int, w: int, h: int, _shot) -> None:
        if w < 6 or h < 6:
            self.log("Область слишком маленькая — попробуйте ещё раз.")
            return
        self.steps.append(Step(kind="area", x=x, y=y, w=w, h=h))
        self.refresh_tree()
        self.save_config()
        self.log(f"Добавлена область {w}×{h} в ({x}, {y})")

    def add_image(self) -> None:
        self._pick("rect", self._add_image_done)

    def _add_image_done(self, x: int, y: int, w: int, h: int, shot) -> None:
        if w < 6 or h < 6:
            self.log("Слишком маленький фрагмент — выделите кнопку/иконку целиком.")
            return
        TARGETS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"target_{time.strftime('%Y%m%d_%H%M%S')}.png"
        path = TARGETS_DIR / name
        shot.crop((x - self._vx(), y - self._vy(),
                   x - self._vx() + w, y - self._vy() + h)).save(path)
        self.steps.append(Step(kind="image", x=x, y=y, w=w, h=h,
                               path=str(path), conf=0.80))
        self.refresh_tree()
        self.save_config()
        self.log(f"Добавлена картинка {name} ({w}×{h})")

    @staticmethod
    def _vx() -> int:
        return virtual_screen()[0]

    @staticmethod
    def _vy() -> int:
        return virtual_screen()[1]

    # -- правка ----------------------------------------------------------- #
    def edit_selected(self, _event=None) -> None:
        idx = self._selected_index()
        if idx is None:
            return
        step = self.steps[idx]
        win = tk.Toplevel(self)
        win.title("Параметры шага")
        win.configure(bg=BG)
        win.transient(self.winfo_toplevel())
        win.resizable(False, False)

        pad = {"padx": px(10), "pady": px(6)}
        ttk.Label(win, text="Задержка после шага, мс (0 — общий интервал):").grid(
            row=0, column=0, sticky="w", **pad)
        var_delay = tk.StringVar(value=str(step.delay))
        ttk.Entry(win, textvariable=var_delay, width=12).grid(row=0, column=1, **pad)

        var_conf = var_wait = None
        if step.kind == "image":
            ttk.Label(win, text="Порог совпадения, %:").grid(row=1, column=0, sticky="w", **pad)
            var_conf = tk.StringVar(value=str(int(step.conf * 100)))
            ttk.Spinbox(win, from_=50, to=100, textvariable=var_conf,
                        width=10).grid(row=1, column=1, sticky="w", **pad)
            ttk.Label(win, text="Ждать появления, сек (0 — общее):").grid(
                row=2, column=0, sticky="w", **pad)
            var_wait = tk.StringVar(value=str(step.wait))
            ttk.Entry(win, textvariable=var_wait, width=12).grid(row=2, column=1, sticky="w", **pad)

        def save() -> None:
            try:
                step.delay = max(0, int(float(var_delay.get())))
            except ValueError:
                pass
            if var_conf is not None:
                try:
                    step.conf = min(1.0, max(0.30, int(float(var_conf.get())) / 100))
                except ValueError:
                    pass
            if var_wait is not None:
                try:
                    step.wait = max(0.0, float(var_wait.get()))
                except ValueError:
                    pass
            win.destroy()
            self.refresh_tree()
            self.save_config()

        buttons = ttk.Frame(win)
        buttons.grid(row=10, column=0, columnspan=2, sticky="e", padx=px(10), pady=(px(10), px(10)))
        flat_button(buttons, "Сохранить", save).pack(side="left")
        flat_button(buttons, "Отмена", win.destroy, kind="ghost").pack(side="left", padx=(px(8), 0))
        win.bind("<Return>", lambda _e: save())
        win.bind("<Escape>", lambda _e: win.destroy())
        win.update_idletasks()
        enable_dark_titlebar(win)
        win.grab_set()

    def delete_selected(self) -> None:
        idx = self._selected_index()
        if idx is None:
            return
        del self.steps[idx]
        self.refresh_tree()
        self.save_config()

    def clear_steps(self) -> None:
        if not self.steps:
            return
        if messagebox.askyesno("AutoClicker", "Удалить все шаги?"):
            self.steps.clear()
            self.refresh_tree()
            self.save_config()

    def move_selected(self, delta: int) -> None:
        idx = self._selected_index()
        if idx is None:
            return
        new = idx + delta
        if not 0 <= new < len(self.steps):
            return
        self.steps[idx], self.steps[new] = self.steps[new], self.steps[idx]
        self.refresh_tree()
        self.tree.selection_set(str(new))
        self.tree.focus(str(new))
        self.save_config()

    # --------------------------------------------------------- настройки ----
    @staticmethod
    def _to_int(value: str, default: int, low: int, high: int) -> int:
        try:
            return max(low, min(high, int(float(value))))
        except (TypeError, ValueError):
            return default

    def read_settings(self) -> dict:
        try:
            wait = max(0.0, float(self.var_wait.get()))
        except ValueError:
            wait = 0.0
        return {
            "interval": self._to_int(self.var_interval.get(), 500, 0, 3_600_000),
            "jitter": self._to_int(self.var_jitter.get(), 0, 0, 600_000),
            "repeats": self._to_int(self.var_repeats.get(), 0, 0, 10_000_000),
            "wait": wait,
            "button": BUTTONS.get(self.var_button.get(), "left"),
        }

    # --------------------------------------------------------- конфиг ------ #
    def save_config(self) -> None:
        data = config_read()
        data["settings"] = {
            "interval": self.var_interval.get(),
            "jitter": self.var_jitter.get(),
            "repeats": self.var_repeats.get(),
            "wait": self.var_wait.get(),
            "button": self.var_button.get(),
        }
        data["steps"] = [self._step_to_dict(s) for s in self.steps]
        try:
            config_write(data)
        except OSError as exc:
            self.log(f"Не удалось сохранить настройки: {exc}")

    def _step_to_dict(self, step: Step) -> dict:
        data = {
            "kind": step.kind, "x": step.x, "y": step.y, "w": step.w, "h": step.h,
            "conf": step.conf, "delay": step.delay, "wait": step.wait,
        }
        if step.path:
            path = Path(step.path)
            try:
                data["path"] = str(path.resolve().relative_to(APP_DIR))
            except (ValueError, OSError):
                data["path"] = str(path)
        return data

    def load_config(self) -> None:
        if not CONFIG_PATH.exists():
            return
        try:
            data = json.loads(CONFIG_PATH.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            self.log(f"Настройки не загружены: {exc}")
            return
        settings = data.get("settings", {}) or {}
        self.var_interval.set(str(settings.get("interval", "500")))
        self.var_jitter.set(str(settings.get("jitter", "0")))
        self.var_repeats.set(str(settings.get("repeats", "0")))
        self.var_wait.set(str(settings.get("wait", "0")))
        button = str(settings.get("button", "ЛКМ"))
        self.var_button.set(button if button in BUTTONS else "ЛКМ")

        for raw in data.get("steps", []) or []:
            try:
                kind = raw.get("kind")
                if kind not in KIND_NAMES:
                    continue
                path = str(raw.get("path", "") or "")
                if path and not Path(path).is_absolute():
                    path = str((APP_DIR / path).resolve())
                self.steps.append(Step(
                    kind=kind,
                    x=int(raw.get("x", 0)), y=int(raw.get("y", 0)),
                    w=int(raw.get("w", 0)), h=int(raw.get("h", 0)),
                    path=path,
                    conf=float(raw.get("conf", 0.80)),
                    delay=int(raw.get("delay", 0)),
                    wait=float(raw.get("wait", 0)),
                ))
            except (TypeError, ValueError):
                continue
        self.refresh_tree()

    # ------------------------------------------------------- горячие клавиши -
    def _setup_hotkeys(self) -> None:
        if not clicker_core.HAS_KEYBOARD or not clicker_core.HAS_DEPS:
            return
        try:
            clicker_core.keyboard.add_hotkey("f6", lambda: self.events.put(("toggle", None)))
            clicker_core.keyboard.add_hotkey("f7", lambda: self.events.put(("point_at_cursor", None)))
            self.log("F6 — старт/стоп, F7 — добавить точку под курсором.")
        except Exception as exc:  # noqa: BLE001
            self.log(f"Горячие клавиши не включились: {exc}")

    # ------------------------------------------------------------- запуск ---
    def toggle_run(self) -> None:
        if self.running:
            self.stop_run()
        else:
            self.start_run()

    def start_run(self) -> None:
        if self.running:
            return
        if not clicker_core.HAS_DEPS:
            messagebox.showerror(
                "AutoClicker",
                "Не установлены зависимости.\n\nВыполните в папке программы:\n\n"
                "pip install -r requirements.txt\n\n" + clicker_core.DEPS_ERROR,
            )
            return
        if not self.steps:
            messagebox.showinfo("AutoClicker", "Добавьте хотя бы один шаг: точку, область или картинку.")
            return
        missing = [s for s in self.steps if s.kind == "image" and not Path(s.path).exists()]
        if missing:
            messagebox.showerror(
                "AutoClicker",
                "Не найден файл картинки:\n" + "\n".join(Path(s.path).name for s in missing)
                + "\n\nУдалите этот шаг или добавьте картинку заново.",
            )
            return

        cfg = self.read_settings()
        self.stop_evt.clear()
        self.running = True
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.status_var.set("Работаю…")
        self.log("▶ старт")
        steps = [replace(s) for s in self.steps]
        threading.Thread(target=self._run, args=(steps, cfg), daemon=True).start()

    def stop_run(self) -> None:
        if not self.running:
            return
        self.stop_evt.set()
        self.status_var.set("Останавливаюсь…")
        self.log("⏹ запрошена остановка")

    def _on_run_finished(self) -> None:
        self.running = False
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.status_var.set("Готов")

    def _run(self, steps: list[Step], cfg: dict) -> None:
        loop = 0
        total = len(steps)
        try:
            while not self.stop_evt.is_set():
                loop += 1
                for idx, step in enumerate(steps, 1):
                    if self.stop_evt.is_set():
                        break
                    self.events.put(("status", f"Цикл {loop} · шаг {idx}/{total}"))
                    try:
                        self._do_step(step, cfg)
                    except clicker_core.pyautogui.FailSafeException:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        self.log(f"Ошибка на шаге {idx}: {exc}")
                    if self.stop_evt.is_set():
                        break
                    delay = step.delay if step.delay > 0 else cfg["interval"]
                    if cfg["jitter"] > 0:
                        delay += random.randint(-cfg["jitter"], cfg["jitter"])
                    if delay > 0:
                        self.stop_evt.wait(delay / 1000.0)
                if cfg["repeats"] and loop >= cfg["repeats"]:
                    self.log(f"Готово: выполнено циклов — {loop}")
                    break
        except clicker_core.pyautogui.FailSafeException:
            self.log("Аварийная остановка: курсор уведён в левый верхний угол.")
        except Exception:  # noqa: BLE001
            self.log("Сбой: " + traceback.format_exc(limit=2).replace("\n", " | "))
        finally:
            self.events.put(("done", None))

    def _do_step(self, step: Step, cfg: dict) -> None:
        button = cfg["button"]
        if step.kind in ("point", "area"):
            x, y = step.x, step.y
            if step.kind == "area" and step.w > 1 and step.h > 1:
                x = random.randint(step.x, step.x + step.w - 1)
                y = random.randint(step.y, step.y + step.h - 1)
            clicker_core.pyautogui.click(x, y, button=button)
            self.log(f"клик ({x}, {y})")
            return

        template = self._template(step.path)
        if template is None:
            self.log(f"картинка недоступна: {Path(step.path).name}")
            return

        wait = step.wait if step.wait > 0 else cfg["wait"]
        deadline = time.time() + wait
        vx, vy = self._vx(), self._vy()
        while not self.stop_evt.is_set():
            screen = clicker_core.np.array(grab_screen())
            found = find_template(screen, template, step.conf)
            if found is not None:
                cx, cy, _w, _h, score = found
                if abs(vx) or abs(vy):
                    cx, cy = cx + vx, cy + vy
                clicker_core.pyautogui.click(cx, cy, button=button)
                self.log(f"найдено {Path(step.path).name} ({score:.0%}) → клик ({cx}, {cy})")
                return
            if time.time() >= deadline:
                self.log(f"не найдено: {Path(step.path).name}")
                return
            if self.stop_evt.wait(0.25):
                return

    def _template(self, path: str) -> "clicker_core.np.ndarray | None":
        file = Path(path)
        try:
            mtime = file.stat().st_mtime
        except OSError:
            return None
        key = (str(file), mtime)
        cached = self.tpl_cache.get(key)
        if cached is not None:
            return cached
        try:
            image = clicker_core.Image.open(file).convert("RGB")
        except Exception:  # noqa: BLE001
            return None
        array = clicker_core.np.array(image)
        self.tpl_cache = {key: array}
        return array

    # ------------------------------------------------------------- выход ----
    def shutdown(self) -> None:
        self.save_config()
        self.stop_evt.set()
        if clicker_core.HAS_KEYBOARD:
            try:
                clicker_core.keyboard.unhook_all_hotkeys()
            except Exception:  # noqa: BLE001
                pass
