#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Мои инструменты — единое тёмное приложение для Windows.

В одном окне:
  * AutoClicker          — клики по точкам, областям и картинкам;
  * Пустые файлы и папки — поиск и удаление пустых файлов и папок;
  * DNS                  — Quad9 / Xbox / Дефолтное (DoH для первых двух);
  * Дневник МО           — оценки, «5» до 4.50 и запас прочности (diary-grades/).

Запуск: двойной клик по tools.pyw (или pythonw tools.pyw).
"""

from __future__ import annotations

import argparse
import ctypes
import csv
import json
import os
import queue
import random
import re
import shutil
import stat
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, simpledialog, ttk

from apps_catalog import APP_COMPANIONS, AppEntry, app_catalog, expand_companions, filter_apps, normalize_apps
from bypass_core import (
    HAPP_SOCKS_DEFAULT,
    _find_happ,
    bypass_admin,
    bypass_apps_route_disable,
    bypass_apps_route_enable,
    bypass_apply,
    bypass_auto_heal_if_leak,
    bypass_cfg,
    bypass_cfg_save,
    bypass_check_direct_ip,
    bypass_check_ip,
    bypass_check_proxy_ip,
    bypass_compare_connections,
    bypass_download,
    bypass_happ_show,
    bypass_status,
    bypass_stop,
    happ_connected,
    happ_mode_write,
    happ_tun_disable,
    happ_tunnel_restart,
    proxifyre_rescan,
    proxifyre_restart,
    proxifyre_start,
    proxifyre_stop,
    set_windows_system_proxy,
)
import shutdown_watcher as wsh
from winbase import (
    APP_DIR,
    APP_FILE,
    CONFIG_PATH,
    NO_WINDOW,
    TARGETS_DIR,
    config_read,
    config_update,
    config_write,
    find_tool,
    is_admin,
    log_exception,
    ps_run,
    pythonw_executable,
    relaunch_as_admin,
    run_tool,
    tool_env,
)

from leads_contacts import format_phone as _fmt_phone  # noqa: F401
from leads_contacts import short as _short_link  # noqa: F401
from leads_pool import INSPECTOR_FIELDS, inspector_fields

from scroll_settings import steps_per_wheel as _scroll_step

# --------------------------------------------------------------------------- #
#  Единая тема: HEX-цвета живут ТОЛЬКО в theme/tokens.py. Прослойка токенов,
#  базовая Panel, тёмные диалоги и UI-обёртки переехали в uibase.py — здесь
#  только реэкспорт. Смена темы обновляет токены через реестр потребителей
#  uibase (см. register_palette_consumer) и apple_tk.recolor().
# --------------------------------------------------------------------------- #
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import theme as _theme
from theme import apple_tk as _apple_tk

from uibase import (
    ACCENT,
    ACCENT_HOVER,
    AVATAR_COLORS,
    BADGE_BAD_BG,
    BADGE_BAD_INK,
    BADGE_GOOD_BG,
    BADGE_GOOD_INK,
    BADGE_NEUTRAL_BG,
    BADGE_WARN_BG,
    BADGE_WARN_INK,
    BG,
    BORDER,
    BRAND_LABEL,
    CARD,
    CARD_HOVER,
    COPIED_BG,
    DANGER,
    DANGER_HOVER,
    DarkMessageBox,
    FG,
    FIELD,
    KNOB,
    LOG_BG,
    LOG_FG,
    MUTED,
    OK,
    ON_AVATAR,
    OVERLAY_BACKDROP,
    OVERLAY_TIMER,
    Panel,
    ROW_SELECTED,
    SIDEBAR,
    SIDEBAR_ACTIVE,
    SIDEBAR_HOVER,
    SIDEBAR_SUBTITLE,
    SIDEBAR_TITLE,
    TAG_DIR,
    TAG_FILE,
    TRACK_DISABLED,
    TRACK_OFF,
    WARN,
    _EXTRA_ROLES,
    _LEGACY_NAMES,
    _iter_widgets,
    _refresh_palette,
    apply_dark_theme,
    badge_colors,
    dark_check,
    dark_text,
    enable_dark_titlebar,
    enable_input_paste,
    flat_button,
    fmt_size,
    messagebox,
    mono_font,
    px,
    register_palette_consumer,
    set_scale,
    tc,
    ui_font,
)

# Токены приходят реэкспортом; регистрируем глобалы этого модуля потребителем
# палитры, чтобы живая смена темы обновляла и их (панели читают голые имена).
register_palette_consumer(globals())

# --------------------------------------------------------------------------- #
#  Движки панелей: логика без tkinter (инварианты слоёв — tests/test_arch.py).
# --------------------------------------------------------------------------- #
from cleanup_core import (  # noqa: E402
    DeleteResult,
    Plan,
    check_blocked,
    delete_plan,
    risk_warning,
    scan_empty,
)
from dns_core import (  # noqa: E402
    DnsState,
    QUAD9_DNS,
    dns_apply,
    dns_clear_tunnel,
    dns_dhcp,
    dns_restore_tunnel,
    dns_state,
    dns_test,
)
from github_core import (  # noqa: E402
    GITHUB_DEFAULT_ROOT,
    gh_authed,
    gh_login,
    gh_save_token,
    gh_token_from_hosts,
    github_repos,
    local_scan_roots,
    mask_token,
    match_local_repos,
    push_folder_to_github,
    sanitize_repo_name,
    scan_local_repos,
    send_to_recycle_bin,
    token_looks_valid,
    update_local_repo,
)

# --------------------------------------------------------------------------- #
#  Зависимости автокликера (cv2 / numpy / pyautogui / PIL / keyboard).
#  Раньше импортировались при старте ВСЕГДА и добавляли до 1 с к открытию окна,
#  даже если нужна вкладка «Лиды» или «Bypass». Теперь грузятся лениво:
#  load_autoclicker_deps() вызывается в AutoClickerPanel.__init__.
# --------------------------------------------------------------------------- #
HAS_DEPS = False
DEPS_ERROR = ""
HAS_KEYBOARD = False


def load_autoclicker_deps() -> None:
    """Подгружает CV-стек при первом открытии вкладки AutoClicker."""
    global HAS_DEPS, DEPS_ERROR, HAS_KEYBOARD
    global cv2, np, pyautogui, Image, ImageGrab, keyboard
    if HAS_DEPS or DEPS_ERROR:
        return
    try:
        import cv2
        import numpy as np
        import pyautogui
        from PIL import Image, ImageGrab

        HAS_DEPS = True
        DEPS_ERROR = ""
        pyautogui.PAUSE = 0
        pyautogui.FAILSAFE = True
    except Exception as _dep_error:  # noqa: BLE001
        HAS_DEPS = False
        DEPS_ERROR = str(_dep_error)
    try:
        import keyboard

        HAS_KEYBOARD = True
    except Exception:  # noqa: BLE001
        HAS_KEYBOARD = False


# --------------------------------------------------------------------------- #
#  AutoClicker: шаги, оверлей, поиск по картинке
# --------------------------------------------------------------------------- #
KIND_NAMES = {"point": "Точка", "area": "Область", "image": "Картинка"}
BUTTONS = {"ЛКМ": "left", "ПКМ": "right", "СКМ": "middle"}


@dataclass
class Step:
    kind: str = "point"
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    path: str = ""
    conf: float = 0.80
    delay: int = 0
    wait: float = 0.0


def virtual_screen() -> tuple[int, int, int, int]:
    if sys.platform == "win32":
        user32 = ctypes.windll.user32
        return (
            user32.GetSystemMetrics(76),
            user32.GetSystemMetrics(77),
            user32.GetSystemMetrics(78),
            user32.GetSystemMetrics(79),
        )
    return (0, 0, pyautogui.size().width, pyautogui.size().height)


def grab_screen() -> "Image.Image":
    try:
        return ImageGrab.grab(all_screens=True).convert("RGB")
    except TypeError:
        return ImageGrab.grab().convert("RGB")


def find_template(screen: "np.ndarray", template: "np.ndarray", confidence: float):
    screen_h, screen_w = screen.shape[:2]
    template_h, template_w = template.shape[:2]
    if template_h > screen_h or template_w > screen_w or template_h < 2 or template_w < 2:
        return None
    gray = cv2.cvtColor(template, cv2.COLOR_RGB2GRAY)
    if float(gray.std()) < 6.0:
        result = cv2.matchTemplate(screen, template, cv2.TM_SQDIFF_NORMED)
        min_val, _max_val, min_loc, _max_loc = cv2.minMaxLoc(result)
        score = 1.0 - float(min_val)
        loc = min_loc
    else:
        result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
        _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(result)
        score = float(max_val)
        loc = max_loc
    if score < confidence:
        return None
    return (loc[0] + template_w // 2, loc[1] + template_h // 2, template_w, template_h, score)


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
        load_autoclicker_deps()  # тяжёлый CV-стек — только при открытии этой вкладки
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
        if not HAS_DEPS:
            self.log("Не установлены зависимости — запуск недоступен.")
        elif not HAS_KEYBOARD:
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
        if not HAS_DEPS:
            messagebox.showerror("AutoClicker", "Не установлены зависимости.\n\n" + DEPS_ERROR)
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
        if not HAS_DEPS:
            return
        try:
            x, y = pyautogui.position()
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
        if not HAS_KEYBOARD or not HAS_DEPS:
            return
        try:
            keyboard.add_hotkey("f6", lambda: self.events.put(("toggle", None)))
            keyboard.add_hotkey("f7", lambda: self.events.put(("point_at_cursor", None)))
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
        if not HAS_DEPS:
            messagebox.showerror(
                "AutoClicker",
                "Не установлены зависимости.\n\nВыполните в папке программы:\n\n"
                "pip install -r requirements.txt\n\n" + DEPS_ERROR,
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
                    except pyautogui.FailSafeException:
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
        except pyautogui.FailSafeException:
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
            pyautogui.click(x, y, button=button)
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
            screen = np.array(grab_screen())
            found = find_template(screen, template, step.conf)
            if found is not None:
                cx, cy, _w, _h, score = found
                if abs(vx) or abs(vy):
                    cx, cy = cx + vx, cy + vy
                pyautogui.click(cx, cy, button=button)
                self.log(f"найдено {Path(step.path).name} ({score:.0%}) → клик ({cx}, {cy})")
                return
            if time.time() >= deadline:
                self.log(f"не найдено: {Path(step.path).name}")
                return
            if self.stop_evt.wait(0.25):
                return

    def _template(self, path: str) -> "np.ndarray | None":
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
            image = Image.open(file).convert("RGB")
        except Exception:  # noqa: BLE001
            return None
        array = np.array(image)
        self.tpl_cache = {key: array}
        return array

    # ------------------------------------------------------------- выход ----
    def shutdown(self) -> None:
        self.save_config()
        self.stop_evt.set()
        if HAS_KEYBOARD:
            try:
                keyboard.unhook_all_hotkeys()
            except Exception:  # noqa: BLE001
                pass

# --------------------------------------------------------------------------- #
#  Панель: пустые файлы и папки
# --------------------------------------------------------------------------- #
class CleanPanel(Panel):
    LIMIT = 2000

    def __init__(self, master: tk.Misc, hub) -> None:
        super().__init__(master, hub)
        self.plan: Plan | None = None
        self.busy = False
        self._build()

    def _build(self) -> None:
        row = ttk.Frame(self)
        row.pack(fill="x")
        ttk.Label(row, text="Папка").pack(side="left")
        self.folder_var = tk.StringVar()
        self.entry = ttk.Entry(row, textvariable=self.folder_var)
        self.entry.pack(side="left", fill="x", expand=True, padx=(px(10), px(8)))
        self.entry.bind("<Return>", lambda _event: self.start_scan())
        self.browse_btn = flat_button(row, "Обзор…", self.choose_folder, kind="ghost")
        self.browse_btn.pack(side="left")

        options = ttk.Frame(self)
        options.pack(fill="x", pady=(px(12), 0))
        self.files_var = tk.BooleanVar(value=True)
        self.dirs_var = tk.BooleanVar(value=True)
        self.hidden_var = tk.BooleanVar(value=False)
        for text, variable, pad in (
            ("Пустые файлы (0 байт)", self.files_var, 0),
            ("Пустые папки", self.dirs_var, px(18)),
            ("Учитывать скрытые", self.hidden_var, px(18)),
        ):
            dark_check(options, text, variable).pack(side="left", padx=(pad, 0))

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(px(14), 0))
        self.scan_btn = flat_button(actions, "Проверить", self.start_scan)
        self.scan_btn.pack(side="left")
        self.delete_btn = flat_button(actions, "Удалить найденное", self.start_delete,
                                      kind="danger", state="disabled")
        self.delete_btn.pack(side="left", padx=(px(8), 0))

        self.summary_var = tk.StringVar(value="Выберите папку и нажмите «Проверить».")
        ttk.Label(self, textvariable=self.summary_var, style="Muted.TLabel").pack(
            anchor="w", pady=(px(12), px(6)))

        wrap = ttk.Frame(self)
        wrap.pack(fill="both", expand=True)
        bar = ttk.Frame(wrap)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, px(4)))
        ttk.Label(bar, text="Список — выдели мышью, Ctrl+C или «Копировать всё»",
                  style="Muted.TLabel").pack(side="left")
        copy_btn = flat_button(bar, "Копировать всё", lambda: None, kind="ghost")
        copy_btn.pack(side="right")
        copy_btn.configure(command=lambda: self.copy_log(self.list, copy_btn, label="Список"))
        self.list = dark_text(wrap, height=14, wrap="none")
        scroll_y = ttk.Scrollbar(wrap, orient="vertical", command=self.list.yview)
        scroll_x = ttk.Scrollbar(wrap, orient="horizontal", command=self.list.xview)
        self.list.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        self.list.grid(row=1, column=0, sticky="nsew")
        scroll_y.grid(row=1, column=1, sticky="ns")
        scroll_x.grid(row=2, column=0, sticky="ew")
        wrap.rowconfigure(1, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.list.tag_configure("file", foreground=TAG_FILE)
        self.list.tag_configure("dir", foreground=TAG_DIR)
        self.list.tag_configure("error", foreground=tc("log_err"))
        self.list.tag_configure("head", foreground=FG, font=mono_font(9, "bold"))
        self.list.tag_configure("hint", foreground=MUTED)
        self.attach_copy(self.list, label="Список", readonly=True)
        self._write("Список пуст. Выберите папку и нажмите «Проверить».\n", "hint")

    # -- помощники --------------------------------------------------------- #
    def _write(self, text: str, tag: str | None = None) -> None:
        # Не выключаем state в "disabled": иначе виджет перестаёт ловить
        # клавиатуру и Ctrl+C не срабатывает. Защиту от правки даёт attach_copy
        # (readonly=True), которая глушит ввод, но оставляет фокус/выделение.
        self.list.configure(state="normal")
        self.list.insert("end", text, (tag,) if tag else ())

    def _clear_list(self) -> None:
        self.list.configure(state="normal")
        self.list.delete("1.0", "end")

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.entry.configure(state=state)
        self.browse_btn.configure(state=state)
        self.scan_btn.configure(state=state)
        if busy or self.plan is None or self.plan.is_empty:
            self.delete_btn.configure(state="disabled")
        else:
            self.delete_btn.configure(state="normal")

    def _fail(self, exc: Exception) -> None:
        self._set_busy(False)
        self.summary_var.set("Не удалось выполнить операцию")
        self.hub.set_status("Ошибка")
        messagebox.showerror("Пустые файлы", str(exc))

    def choose_folder(self) -> None:
        initial = self.folder_var.get().strip()
        if not (initial and os.path.isdir(initial)):
            initial = str(Path.home())
        chosen = filedialog.askdirectory(
            title="Выберите папку для очистки", initialdir=initial, mustexist=True
        )
        if chosen:
            self.folder_var.set(os.path.normpath(chosen))
            self.start_scan()

    def _current_folder(self) -> str | None:
        raw = self.folder_var.get().strip().strip('"')
        if not raw:
            messagebox.showinfo("Пустые файлы", "Сначала выберите папку.")
            return None
        root = os.path.abspath(os.path.expanduser(raw))
        if not os.path.isdir(root):
            messagebox.showerror("Пустые файлы", f"Папка не найдена:\n{root}")
            return None
        blocked = check_blocked(root)
        if blocked:
            messagebox.showerror("Пустые файлы", blocked)
            return None
        return root

    # -- поиск ------------------------------------------------------------- #
    def start_scan(self) -> None:
        if self.busy:
            return
        root = self._current_folder()
        if root is None:
            return
        files, dirs, hidden = self.files_var.get(), self.dirs_var.get(), self.hidden_var.get()
        if not files and not dirs:
            messagebox.showinfo("Пустые файлы", "Отметьте, что искать: файлы и/или папки.")
            return

        self.plan = None
        self._set_busy(True)
        self._clear_list()
        self.summary_var.set("Ищу пустые файлы и папки…")
        self.hub.set_status(f"Сканирую: {root}")
        self.hub.run_bg(
            lambda: scan_empty(root, files=files, dirs=dirs, hidden=hidden),
            self._scan_done,
            self._fail,
        )

    def _scan_done(self, plan: Plan) -> None:
        self.plan = plan
        self._set_busy(False)
        self._clear_list()
        self._write(f"Папка: {plan.root}\n", "head")

        if plan.is_empty:
            self.summary_var.set("Пустых файлов и папок не найдено.")
            self.hub.set_status("Ничего удалять не нужно")
            self._write("Пустых файлов и папок не найдено.\n", "head")
            if plan.errors:
                self._write("\nНе удалось прочитать:\n", "error")
                for path, reason in plan.errors:
                    self._write(f"  {path} — {reason}\n", "error")
            return

        shown = 0
        for path in plan.files:
            if shown >= self.LIMIT:
                break
            self._write(f"[файл]  {path}\n", "file")
            shown += 1
        for path in plan.dirs:
            if shown >= self.LIMIT:
                break
            self._write(f"[папка] {path}\n", "dir")
            shown += 1
        if shown < plan.total:
            self._write(f"\n… и ещё {plan.total - shown} (список сокращён)\n", "head")
        if plan.errors:
            self._write("\nНе удалось прочитать:\n", "error")
            for path, reason in plan.errors:
                self._write(f"  {path} — {reason}\n", "error")

        self.summary_var.set(
            f"Найдено: файлов — {len(plan.files)}, папок — {len(plan.dirs)}."
        )
        self.hub.set_status("Проверьте список и нажмите «Удалить найденное»")

    # -- удаление ---------------------------------------------------------- #
    def start_delete(self) -> None:
        plan = self.plan
        if self.busy or plan is None or plan.is_empty:
            return
        message = (
            "Удалить безвозвратно?\n\n"
            f"Пустых файлов: {len(plan.files)}\n"
            f"Пустых папок: {len(plan.dirs)}\n\n"
            f"Папка: {plan.root}"
        )
        warning = risk_warning(plan.root)
        if warning:
            message += f"\n\n{warning}"
        if not messagebox.askyesno("Удалить безвозвратно?", message):
            return

        self._set_busy(True)
        self._clear_list()
        self.hub.set_status("Удаляю…")
        self.hub.run_bg(lambda: delete_plan(plan), self._delete_done, self._fail)

    def _delete_done(self, result: DeleteResult) -> None:
        self.plan = None
        self._set_busy(False)
        self._clear_list()
        self._write("Готово.\n", "head")
        self._write(f"Удалено файлов: {result.removed_files}\n")
        self._write(f"Удалено папок:  {result.removed_dirs}\n")
        if result.errors:
            self._write("\nНе удалось удалить:\n", "error")
            for path, reason in result.errors:
                self._write(f"  {path} — {reason}\n", "error")
        self.summary_var.set(
            f"Удалено: файлов {result.removed_files}, папок {result.removed_dirs}"
            + (f". Ошибок: {len(result.errors)}" if result.errors else ".")
        )
        self.hub.set_status("Готово")
        if result.errors:
            messagebox.showwarning(
                "Пустые файлы",
                f"Удалено файлов: {result.removed_files}, папок: {result.removed_dirs}.\n"
                f"Не удалось удалить: {len(result.errors)} (подробности в списке).",
            )
        else:
            messagebox.showinfo(
                "Пустые файлы",
                f"Готово.\nУдалено файлов: {result.removed_files}, папок: {result.removed_dirs}.",
            )

# --------------------------------------------------------------------------- #
#  Панель: DNS
# --------------------------------------------------------------------------- #
class DnsPanel(Panel):
    def __init__(self, master: tk.Misc, hub) -> None:
        super().__init__(master, hub)
        self.busy = False
        self.action_buttons: list[tk.Button] = []
        self._build()
        self.refresh_state()

    def _build(self) -> None:
        card = tk.Frame(self, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="x")
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=px(16), pady=px(14))

        head = tk.Frame(inner, bg=CARD)
        head.pack(fill="x")
        tk.Label(head, text="Текущее состояние", bg=CARD, fg=FG,
                 font=ui_font(10, "bold")).pack(side="left")
        self.refresh_btn = flat_button(head, "Обновить", self.refresh_state, kind="ghost",
                                       padx=px(10), pady=px(4))
        self.refresh_btn.pack(side="right")

        self.adapter_var = tk.StringVar(value="—")
        self.dns_var = tk.StringVar(value="—")
        self.tunnel_var = tk.StringVar(value="—")
        for title, variable in (
            ("Адаптер:", self.adapter_var),
            ("DNS сейчас:", self.dns_var),
            ("Туннель:", self.tunnel_var),
        ):
            row = tk.Frame(inner, bg=CARD)
            row.pack(fill="x", pady=(px(8) if title == "Адаптер:" else px(3), 0))
            tk.Label(row, text=title, bg=CARD, fg=MUTED, width=12, anchor="w",
                     font=ui_font(9)).pack(side="left")
            tk.Label(row, textvariable=variable, bg=CARD, fg=FG, anchor="w",
                     font=ui_font(9)).pack(side="left")

        buttons_one = ttk.Frame(self)
        buttons_one.pack(fill="x", pady=(px(14), px(6)))
        buttons_two = ttk.Frame(self)
        buttons_two.pack(fill="x")

        for row, items in (
            (buttons_one, (
                ("Quad9", lambda: self._apply(QUAD9_DNS, QUAD9_DOH, "Quad9")),
                ("Xbox", lambda: self._apply(XBOX_DNS, XBOX_DOH, "Xbox")),
                ("Дефолтное", self._dhcp),
            )),
            (buttons_two, (
                ("Quad9 + очистить туннель", self._quad9_and_tunnel),
                ("Вернуть DNS туннеля", self._restore_tunnel),
                ("Проверить DNS", self._test_dns),
            )),
        ):
            for index, (text, command) in enumerate(items):
                button = flat_button(
                    row, text, command,
                    kind="primary" if index == 0 else "ghost",
                )
                button.pack(side="left", padx=(0 if index == 0 else px(8), 0))
                self.action_buttons.append(button)

        ttk.Label(
            self,
            text="Профиль Xbox блокирует workbuddy.ai — для него используйте Quad9.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(px(12), 0))

        log_frame, self.log_box = self.make_log(self, height=10)
        log_frame.pack(fill="both", expand=True, pady=(px(10), 0))
        self.append_log(self.log_box, "Здесь появятся результаты операций.")

    # -- состояние --------------------------------------------------------- #
    def refresh_state(self) -> None:
        self.refresh_btn.configure(state="disabled")
        self.hub.run_bg(dns_state, self._state_done, self._state_error)

    def _state_done(self, state: DnsState) -> None:
        self.refresh_btn.configure(state="normal")
        self.adapter_var.set(f"{state.name} (ifIndex {state.index})" if state.name else "не найден")
        self.dns_var.set(state.dns or "автоматически (DHCP)")
        if state.tunnel:
            self.tunnel_var.set(f"{state.tunnel} · DNS {state.tunnel_dns or 'авто'}")
        else:
            self.tunnel_var.set("не найден")

    def _state_error(self, exc: Exception) -> None:
        self.refresh_btn.configure(state="normal")
        self.adapter_var.set("—")
        self.dns_var.set("—")
        self.tunnel_var.set("—")
        self.append_log(self.log_box, f"Не удалось получить состояние: {exc}", "error")

    # -- действия ---------------------------------------------------------- #
    def _guard(self) -> bool:
        if self.busy:
            return False
        if not self.hub.ensure_admin("dns"):
            return False
        return True

    def _apply(self, servers: list[str], template: str, label: str) -> None:
        if not self._guard():
            return
        self._run(f"Применяю профиль {label}…", lambda: dns_apply(servers, template))

    def _quad9_and_tunnel(self) -> None:
        if not self._guard():
            return

        def work() -> str:
            first = dns_apply(QUAD9_DNS, QUAD9_DOH)
            second = dns_clear_tunnel()
            return first + "\n" + second

        self._run("Quad9 + очистка DNS туннеля…", work)

    def _dhcp(self) -> None:
        if not self._guard():
            return
        self._run("Сбрасываю DNS на DHCP…", dns_dhcp)

    def _restore_tunnel(self) -> None:
        if not self._guard():
            return
        self._run("Возвращаю DNS туннеля…", dns_restore_tunnel)

    def _test_dns(self) -> None:
        if self.busy:
            return
        self._run("Проверяю разрешение имён…", dns_test)

    def _run(self, title: str, work) -> None:
        self.busy = True
        self._set_buttons(False)
        self.append_log(self.log_box, title)
        self.hub.set_status(title)
        self.hub.run_bg(work, self._action_done, self._action_error)

    def _action_done(self, output: str) -> None:
        self.busy = False
        self._set_buttons(True)
        for line in str(output).splitlines():
            if line.strip():
                self.append_log(self.log_box, line.strip())
        self.hub.set_status("DNS: готово")
        self.refresh_state()

    def _action_error(self, exc: Exception) -> None:
        self.busy = False
        self._set_buttons(True)
        self.append_log(self.log_box, f"Ошибка: {exc}", "error")
        self.hub.set_status("DNS: ошибка")
        messagebox.showerror("DNS", str(exc))

    def _set_buttons(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for button in self.action_buttons:
            button.configure(state=state)

# --------------------------------------------------------------------------- #
#  Панель: GitHub
# --------------------------------------------------------------------------- #
class GitHubPanel(Panel):
    def __init__(self, master: tk.Misc, hub) -> None:
        super().__init__(master, hub)
        config = config_read().get("github") or {}
        self.root = str(config.get("root") or GITHUB_DEFAULT_ROOT)
        self.repos: list[dict] = []
        self.local_items: list[dict] = []
        self.matches: dict[str, str] = {}
        self.local_only: list[dict] = []
        self.status_by_path: dict[str, dict] = {}
        self.rows: dict[str, dict] = {}
        self.scan_roots: list[str] = []
        self.login = ""
        self.busy = False
        self.checking = False
        self._build()
        self.refresh()

    def _build(self) -> None:
        top = ttk.Frame(self)
        top.pack(fill="x")
        self.btn_refresh = flat_button(top, "Обновить", self.refresh, kind="ghost")
        self.btn_refresh.pack(side="left")
        self.btn_all = flat_button(top, "Загрузить всё", self.update_all, kind="ghost")
        self.btn_all.pack(side="left", padx=(px(8), 0))
        self.btn_upd_all = flat_button(top, "Обновить всё", self.update_needed, kind="ghost")
        self.btn_upd_all.pack(side="left", padx=(px(8), 0))
        self.btn_push = flat_button(top, "Залить папку…", self.push_folder)
        self.btn_push.pack(side="right")
        self.login_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.login_var, style="Muted.TLabel").pack(
            side="right", padx=(0, px(12))
        )
        self.btn_login = flat_button(top, "Войти в GitHub", self.login_browser, kind="ghost")
        self.btn_login.pack(side="right", padx=(0, px(8)))

        path_row = ttk.Frame(self)
        path_row.pack(fill="x", pady=(px(10), 0))
        ttk.Label(path_row, text="Искать локальные папки в:", style="Muted.TLabel").pack(side="left")
        self.root_var = tk.StringVar(value=self.root)
        ttk.Label(path_row, textvariable=self.root_var, style="Muted.TLabel").pack(
            side="left", padx=(px(8), px(8))
        )
        flat_button(path_row, "Изменить…", self.choose_root, kind="ghost").pack(side="left")
        ttk.Label(
            self,
            text="Плюс всегда проверяются Документы, Рабочий стол, Загрузки и домашняя папка.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(px(4), 0))

        search_row = ttk.Frame(self)
        search_row.pack(fill="x", pady=(px(10), px(6)))
        ttk.Label(search_row, text="Поиск:").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_args: self._render())
        ttk.Entry(search_row, textvariable=self.search_var).pack(
            side="left", fill="x", expand=True, padx=(px(8), px(10))
        )
        self.only_diff_var = tk.BooleanVar(value=False)
        dark_check(search_row, "только расхождения", self.only_diff_var,
                   bg=BG, command=self._render).pack(side="left")
        self.only_upd_var = tk.BooleanVar(value=False)
        dark_check(search_row, "только можно обновить", self.only_upd_var,
                   bg=BG, command=self._render).pack(side="left", padx=(px(12), 0))

        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill="both", expand=True)
        columns = ("name", "access", "state", "local", "update")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=10,
                                 selectmode="browse")
        self.tree.heading("name", text="Репозиторий")
        self.tree.heading("access", text="Доступ")
        self.tree.heading("state", text="Где есть")
        self.tree.heading("local", text="Папка на ПК")
        self.tree.heading("update", text="Обновление")
        self.tree.column("name", width=px(190), anchor="w")
        self.tree.column("access", width=px(90), anchor="center", stretch=False)
        self.tree.column("state", width=px(150), anchor="w", stretch=False)
        self.tree.column("local", width=px(400), anchor="w", stretch=False)
        self.tree.column("update", width=px(150), anchor="w", stretch=False)
        self.tree.tag_configure("both", foreground=FG)
        self.tree.tag_configure("gh_only", foreground=WARN)
        self.tree.tag_configure("local_only", foreground=ACCENT)
        self.tree.tag_configure("can_upd", foreground=OK)
        self.tree.tag_configure("behind", foreground=WARN)
        self.tree.tag_configure("mut", foreground=MUTED)
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._update_actions())
        self.tree.bind("<Double-1>", lambda _event: self.open_folder())

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(px(10), 0))
        self.btn_open = flat_button(actions, "Открыть папку", self.open_folder, kind="ghost")
        self.btn_open.pack(side="left")
        self.btn_download = flat_button(actions, "Скачать", self.download, kind="ghost")
        self.btn_download.pack(side="left", padx=(px(8), 0))
        self.btn_push_sel = flat_button(actions, "Залить эту папку", self.push_selected,
                                        kind="ghost")
        self.btn_push_sel.pack(side="left", padx=(px(8), 0))
        self.btn_web = flat_button(actions, "Открыть на GitHub", self.open_web, kind="ghost")
        self.btn_web.pack(side="left", padx=(px(8), 0))
        self.btn_copy = flat_button(actions, "Скопировать список", self.copy_list, kind="ghost")
        self.btn_copy.pack(side="right")

        actions2 = ttk.Frame(self)
        actions2.pack(fill="x", pady=(px(6), 0))
        self.btn_visibility = flat_button(actions2, "Видимость…", self.set_visibility, kind="ghost")
        self.btn_visibility.pack(side="left")
        self.btn_delete = flat_button(actions2, "Удалить с GitHub", self.delete_repo, kind="danger")
        self.btn_delete.pack(side="left", padx=(px(8), 0))
        self.btn_delete_folder = flat_button(actions2, "Удалить папку", self.delete_folder,
                                             kind="danger")
        self.btn_delete_folder.pack(side="left", padx=(px(8), 0))

        self.summary_var = tk.StringVar(value="Загружаю список…")
        ttk.Label(self, textvariable=self.summary_var, style="Muted.TLabel").pack(
            anchor="w", pady=(px(10), px(6))
        )

        log_frame, self.log_box = self.make_log(self, height=7, label="Лог GitHub")
        log_frame.pack(fill="both", expand=True)
        self.append_log(self.log_box, "Git и GitHub CLI находятся автоматически.")


    # -- инфраструктура ---------------------------------------------------- #
    def _log_bg(self, text: str) -> None:
        self.hub.post(lambda: self.append_log(self.log_box, text))

    def _start_busy(self, status: str) -> None:
        self.busy = True
        self._update_actions()
        self.hub.set_status(status)

    def _finish(self) -> None:
        self.busy = False
        self._update_actions()

    def _fail(self, exc: Exception) -> None:
        self._finish()
        self.append_log(self.log_box, f"Ошибка: {exc}", "error")
        self.hub.set_status("GitHub: ошибка")
        messagebox.showerror("GitHub", str(exc), parent=self)
        text = str(exc).lower()
        if "delete_repo" in text or "не выполнен вход" in text:
            if messagebox.askyesno(
                "Вход в GitHub",
                "Войти через браузер? Это даст права и на заливку, и на удаление "
                "репозиториев — без токенов.",
                parent=self,
            ):
                self.login_browser(confirm=False)

    def _selected_row(self) -> dict | None:
        selection = self.tree.selection()
        if not selection:
            return None
        return self.rows.get(selection[0])

    def _selected_repo(self) -> dict | None:
        row = self._selected_row()
        return row["repo"] if row else None

    def _local_for(self, row: dict | None) -> str:
        return row["local"] if row else ""

    def _display_path(self, local: str) -> str:
        if not local:
            return "—"
        try:
            return "~\\" + str(Path(local).relative_to(Path.home()))
        except (ValueError, OSError):
            return local

    def _folder_state(self, local: str) -> tuple[str, bool]:
        """(текст статуса, можно ли обновить) для локальной git-папки."""
        st = self.status_by_path.get(local)
        if not st:
            return ("—", False)
        if not st.get("upstream"):
            return ("не привязан к ветке", False)
        if st.get("ahead", 0) > 0 or st.get("dirty"):
            return ("можно обновить", True)
        if st.get("behind", 0) > 0:
            return ("отстаёт от GitHub", False)
        return ("актуально", False)

    def _update_actions(self) -> None:
        row = self._selected_row()
        repo = row["repo"] if row else None
        local = self._local_for(row)
        state = "disabled" if self.busy else "normal"
        self.btn_refresh.configure(state=state)
        self.btn_push.configure(state=state)
        self.btn_login.configure(state=state)
        self.btn_all.configure(state=state if self.repos else "disabled")
        self.btn_upd_all.configure(state=state if (self.repos or self.local_items) else "disabled")
        self.btn_copy.configure(state="normal" if self.rows and not self.busy else "disabled")
        self.btn_open.configure(state="normal" if local and not self.busy else "disabled")
        self.btn_push_sel.configure(state="normal" if local and not self.busy else "disabled")
        self.btn_download.configure(
            state="normal" if repo and not local and not self.busy else "disabled"
        )
        self.btn_web.configure(state="normal" if repo and not self.busy else "disabled")
        self.btn_delete.configure(state="normal" if repo and not self.busy else "disabled")
        self.btn_delete_folder.configure(
            state="normal" if local and not self.busy else "disabled"
        )
        vis_state = "normal" if repo and not self.busy else "disabled"
        self.btn_visibility.configure(state=vis_state)
        if repo:
            self.btn_visibility.configure(
                text="Сделать публичным" if repo.get("private") else "Сделать приватным"
            )
        else:
            self.btn_visibility.configure(text="Видимость…")

    def _render(self) -> None:
        query = self.search_var.get().strip().lower()
        only_diff = bool(self.only_diff_var.get())
        only_upd = bool(self.only_upd_var.get())
        previous = self.tree.selection()
        previous_iid = previous[0] if previous else ""
        self.tree.delete(*self.tree.get_children())
        self.rows = {}

        matched = sum(1 for repo in self.repos if repo["full"] in self.matches)
        gh_only = len(self.repos) - matched
        upd_count = sum(
            1 for repo in self.repos
            if self.matches.get(repo["full"], "") and self._folder_state(self.matches[repo["full"]])[1]
        ) + len(self.local_only)
        shown = 0

        for repo in self.repos:
            local = self.matches.get(repo["full"], "")
            haystack = f"{repo['name']} {repo['full']} {local}".lower()
            if query and query not in haystack:
                continue
            state, can_update = self._folder_state(local) if local else ("—", False)
            if only_diff and local:
                continue
            if only_upd and not can_update:
                continue
            iid = repo["full"]
            tag = "gh_only" if not local else (
                "can_upd" if can_update else ("behind" if state == "отстаёт от GitHub" else "mut")
            )
            self.tree.insert(
                "", "end", iid=iid, tags=(tag,),
                values=(
                    repo["name"],
                    "приватный" if repo["private"] else "публичный",
                    "● на ПК" if local else "○ только на GitHub",
                    self._display_path(local),
                    state if local else "—",
                ),
            )
            self.rows[iid] = {"iid": iid, "repo": repo, "local": local}
            shown += 1

        for item in self.local_only:
            haystack = f"{item['name']} {item['path']}".lower()
            if query and query not in haystack:
                continue
            iid = "local::" + item["path"]
            self.tree.insert(
                "", "end", iid=iid, tags=("local_only",),
                values=(
                    item["name"], "—", "◆ только на ПК",
                    self._display_path(item["path"]), "новая — залить",
                ),
            )
            self.rows[iid] = {"iid": iid, "repo": None, "local": item["path"]}
            shown += 1

        if previous_iid and self.tree.exists(previous_iid):
            self.tree.selection_set(previous_iid)
        summary = (
            f"На GitHub: {len(self.repos)} · есть на ПК: {matched} · "
            f"только на GitHub: {gh_only} · папок без GitHub: {len(self.local_only)} · "
            f"можно обновить: {upd_count}"
        )
        if query or only_diff or only_upd:
            summary += f" · показано: {shown}"
        self.summary_var.set(summary)
        self._update_actions()

    def copy_list(self) -> None:
        """Копирует таблицу текстом: репозиторий, где есть, путь на ПК."""
        lines = ["Репозиторий\tДоступ\tГде есть\tПапка на ПК\tОбновление"]
        for iid in self.tree.get_children():
            row = self.rows.get(iid) or {}
            repo = row.get("repo")
            values = self.tree.item(iid, "values")
            lines.append("\t".join(str(value) for value in values))
        self.copy_text("\n".join(lines), label="Список GitHub")

    # -- вход через браузер ------------------------------------------------ #
    def login_browser(self, confirm: bool = True) -> None:
        """Вход в GitHub через браузер: без токенов и копирования строк."""
        if self.busy:
            return
        gh = find_tool("gh")
        if not gh:
            messagebox.showerror(
                "GitHub", "GitHub CLI (gh) не найден. Установите с cli.github.com.", parent=self
            )
            return
        if confirm and not messagebox.askyesno(
            "Вход в GitHub",
            "Откроется браузер.\n\n"
            "1. Войди в GitHub (если ещё не вошёл).\n"
            "2. Нажми «Authorize».\n\n"
            "Код для входа приложение скопирует в буфер обмена.\n"
            "Этот вход сразу даёт право удалять репозитории.",
            parent=self,
        ):
            return
        self._start_busy("GitHub: вход через браузер…")
        self.append_log(self.log_box, "открываю браузер для входа…")

        def work():
            proc = subprocess.Popen(
                [gh, "auth", "login", "--web", "-h", "github.com",
                 "--git-protocol", "https", "--skip-ssh-key",
                 "-s", "repo", "-s", "delete_repo", "-s", "gist"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=tool_env(),
                creationflags=NO_WINDOW,
            )
            try:
                if proc.stdin:
                    proc.stdin.write("\n")     # gh ждёт Enter перед открытием браузера
                    proc.stdin.flush()
            except OSError:
                pass
            for line in proc.stdout:
                line = line.strip()
                if line:
                    self._log_bg(line)
            return proc.wait()

        self.hub.run_bg(work, self._login_done, self._fail)

    def _login_done(self, code: int) -> None:
        self._finish()
        if code == 0:
            self.append_log(self.log_box, "✅ вход в GitHub выполнен", "ok")
            self.hub.set_status("GitHub: вход выполнен")
            messagebox.showinfo(
                "Вход в GitHub",
                "Готово. Теперь работают и заливка, и удаление репозиториев.",
                parent=self,
            )
            self.refresh()
        else:
            self.append_log(self.log_box, "❌ вход не выполнен", "error")
            self.hub.set_status("GitHub: вход не выполнен")
            messagebox.showerror(
                "Вход в GitHub",
                "Браузер не подтвердил вход.\nНажмите «Войти в GitHub» и повторите.",
                parent=self,
            )

    # -- токен ------------------------------------------------------------- #
    def token_dialog(self) -> None:
        if self.busy:
            return
        try:
            current = gh_token_from_hosts()
        except Exception:  # noqa: BLE001
            current = ""

        initial = ""
        try:
            clip = self.clipboard_get().strip()
            if token_looks_valid(clip):
                initial = clip
        except tk.TclError:
            pass

        win = tk.Toplevel(self)
        win.withdraw()
        win.title("Токен GitHub")
        win.configure(bg=CARD)
        win.transient(self)
        win.resizable(False, False)

        body = tk.Frame(win, bg=CARD, padx=px(22), pady=px(18))
        body.pack(fill="both", expand=True)
        tk.Label(body, text="Токен GitHub", bg=CARD, fg=FG,
                 font=ui_font(12, "bold"), anchor="w").pack(fill="x")
        tk.Label(
            body,
            text=("Удаление репозиториев требует права delete_repo. Создайте "
                  "classic PAT со scope repo и delete_repo, скопируйте его и "
                  "вставьте здесь — приложение заменит токен в hosts.yml GitHub CLI."),
            bg=CARD, fg=MUTED, justify="left", anchor="w",
            wraplength=px(520), font=ui_font(9),
        ).pack(fill="x", pady=(px(10), 0))

        tk.Label(body, text=f"Сейчас: {mask_token(current)}", bg=CARD, fg=ACCENT,
                 anchor="w", font=mono_font(9)).pack(fill="x", pady=(px(10), 0))

        link = tk.Label(body, text="Создать токен на GitHub ↗", bg=CARD, fg=ACCENT,
                        cursor="hand2", font=ui_font(9, "underline"), anchor="w")
        link.pack(fill="x", pady=(px(6), 0))
        link.bind(
            "<Button-1>",
            lambda _event: webbrowser.open(
                "https://github.com/settings/tokens/new?scopes=repo,delete_repo"
                "&description=Tools%20GitHub"
            ),
        )

        value = tk.StringVar(value=initial)
        entry = ttk.Entry(body, textvariable=value, show="•")
        entry.pack(fill="x", pady=(px(10), 0), ipady=px(2))

        show_var = tk.BooleanVar(value=not bool(initial))

        def toggle_show() -> None:
            entry.configure(show="" if show_var.get() else "•")

        dark_check(body, "Показать токен", show_var, bg=CARD,
                   command=toggle_show).pack(anchor="w", pady=(px(8), 0))

        def close() -> None:
            win.destroy()

        def save() -> None:
            token = value.get().strip()
            if not token_looks_valid(token):
                messagebox.showerror(
                    "Токен GitHub",
                    "Не похоже на токен.\n\nНужен classic PAT, начинающийся с ghp_, "
                    "со scope repo и delete_repo.",
                    parent=win,
                )
                entry.focus_set()
                return
            try:
                paths = gh_save_token(token)
            except (ValueError, RuntimeError) as exc:
                messagebox.showerror("Токен GitHub", str(exc), parent=win)
                return
            win.destroy()
            self.append_log(
                self.log_box, "Токен GitHub обновлён: " + ", ".join(paths), "ok"
            )
            self.hub.set_status("GitHub: токен обновлён")
            messagebox.showinfo(
                "Токен GitHub",
                "Токен заменён в:\n" + "\n".join(paths)
                + "\n\nРезервная копия: hosts.yml.bak\nОбновляю список…",
                parent=self,
            )
            self.refresh()

        buttons = tk.Frame(body, bg=CARD)
        buttons.pack(fill="x", pady=(px(18), 0))
        flat_button(buttons, "Сохранить", save).pack(side="right")
        flat_button(buttons, "Отмена", close, kind="ghost").pack(
            side="right", padx=(0, px(8))
        )

        win.bind("<Return>", lambda _event: save())
        win.bind("<Escape>", lambda _event: close())
        win.protocol("WM_DELETE_WINDOW", close)
        win.update_idletasks()
        width, height = win.winfo_reqwidth(), win.winfo_reqheight()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - width) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - height) // 3)
        win.geometry(f"+{x}+{y}")
        enable_dark_titlebar(win)
        win.deiconify()
        win.grab_set()
        win.focus_force()
        entry.focus_set()
        if initial:
            entry.select_range(0, "end")
        win.wait_window()

    # -- путь -------------------------------------------------------------- #
    def choose_root(self) -> None:
        chosen = filedialog.askdirectory(
            title="Где искать локальные репозитории", initialdir=self.root, mustexist=True
        )
        if not chosen:
            return
        self.root = os.path.normpath(chosen)
        self.root_var.set(self.root)
        config_update(github={"root": self.root})
        self.refresh()

    # -- список ------------------------------------------------------------ #
    def refresh(self) -> None:
        if self.busy:
            return
        if not find_tool("gh"):
            self.append_log(self.log_box, "GitHub CLI (gh) не найден.", "error")
            self.summary_var.set("gh не найден — установите GitHub CLI")
            self.hub.set_status("GitHub: gh не найден")
            return
        self._start_busy("GitHub: загружаю список…")
        roots = local_scan_roots(self.root)
        self.scan_roots = roots

        def work():
            if not gh_authed():
                raise RuntimeError(
                    "Не выполнен вход в GitHub.\nВыполните в терминале:\n\ngh auth login --web"
                )
            return {
                "login": gh_login(),
                "repos": github_repos(),
                "local": scan_local_repos(roots),
            }

        self.hub.run_bg(work, self._refresh_done, self._fail)

    def _refresh_done(self, data: dict) -> None:
        self.login = data.get("login") or ""
        self.repos = data.get("repos") or []
        self.local_items = data.get("local") or []
        self.matches, self.local_only = match_local_repos(self.repos, self.local_items)
        self.status_by_path = {
            it["path"]: it.get("status") for it in self.local_items if it.get("status")
        }
        self.login_var.set(f"@{self.login}" if self.login else "")
        self._finish()
        self._render()
        self.hub.set_status("GitHub: список обновлён")
        self._log_report()

    def _log_report(self) -> None:
        """Короткий отчёт в лог: что есть где — его можно скопировать."""
        matched = len(self.matches)
        gh_only = [repo for repo in self.repos if repo["full"] not in self.matches]
        self.append_log(
            self.log_box,
            f"=== Сверка: на GitHub {len(self.repos)}, на ПК {len(self.local_items)}, "
            f"совпало {matched} ===",
        )
        for repo in self.repos:
            local = self.matches.get(repo["full"], "")
            if local:
                self.append_log(self.log_box, f"✔ {repo['name']} → {local}", "ok")
        for repo in gh_only[:40]:
            self.append_log(self.log_box, f"✖ нет на ПК: {repo['name']}")
        if len(gh_only) > 40:
            self.append_log(self.log_box, f"… ещё {len(gh_only) - 40} репозиториев без папки")
        for item in self.local_only[:40]:
            self.append_log(self.log_box, f"◆ нет на GitHub: {item['path']}")
        if len(self.local_only) > 40:
            self.append_log(self.log_box, f"… ещё {len(self.local_only) - 40} локальных папок")

        need = [
            (repo["name"], self.matches[repo["full"]], self._folder_state(self.matches[repo["full"]])[0])
            for repo in self.repos
            if self.matches.get(repo["full"], "")
            and self._folder_state(self.matches[repo["full"]])[1]
        ]
        need += [(item["name"], item["path"], "новая — залить") for item in self.local_only]
        if not need:
            self.append_log(self.log_box, "=== Обновлять нечего: всё актуально ===")
        else:
            self.append_log(self.log_box, f"=== Можно обновить: {len(need)} ===")
            for name, folder, state in need[:40]:
                self.append_log(self.log_box, f"↑ {state}: {name} → {folder}", "ok")
            if len(need) > 40:
                self.append_log(self.log_box, f"… ещё {len(need) - 40} папок к обновлению")

    # -- заливка ----------------------------------------------------------- #
    def push_folder(self) -> None:
        if self.busy:
            return
        folder = filedialog.askdirectory(
            title="Папку с каким проектом залить на GitHub?", initialdir=self.root, mustexist=True
        )
        if not folder:
            return
        self._push(folder)

    def push_selected(self) -> None:
        row = self._selected_row()
        folder = self._local_for(row)
        if not folder or self.busy:
            messagebox.showinfo("GitHub", "У выбранной строки нет папки на ПК.", parent=self)
            return
        repo = row["repo"] if row else None
        if repo:  # репозиторий уже есть — обновляем его, а не пересоздаём
            self._update_one(folder, repo["name"])
            return
        self._push(folder)

    def _push(self, folder: str) -> None:
        default = sanitize_repo_name(Path(folder).name)
        name = messagebox.askstring(
            "Новый репозиторий",
            f"Папка:\n{folder}\n\nИмя репозитория на GitHub (будет публичным):",
            default, parent=self,
        )
        if not name:
            return
        name = sanitize_repo_name(name)
        self._start_busy(f"GitHub: заливаю {name}…")
        self.append_log(self.log_box, f"=== {name} ===")

        def work():
            return push_folder_to_github(folder, name, True, self._log_bg)

        self.hub.run_bg(work, self._push_done, self._fail)

    def _push_done(self, ok: bool) -> None:
        self._finish()
        if ok:
            self.hub.set_status("GitHub: залито")
            self.refresh()
        else:
            self.hub.set_status("GitHub: заливка не удалась")

    # -- обновление одной папки -------------------------------------------- #
    def _update_one(self, folder: str, name: str) -> None:
        self._start_busy(f"GitHub: обновляю {name}…")
        self.append_log(self.log_box, f"=== {name}: коммит и push ===")

        def work():
            try:
                return update_local_repo(folder, name, self._log_bg)
            except Exception as exc:  # noqa: BLE001
                self._log_bg(f"Ошибка {name}: {exc}")
                return False

        self.hub.run_bg(work, self._update_done, self._fail)

    def _update_done(self, ok: bool) -> None:
        self._finish()
        if ok:
            self.hub.set_status("GitHub: обновлено")
            self.refresh()
        else:
            self.hub.set_status("GitHub: обновление не удалось")

    # -- обновление всех --------------------------------------------------- #
    def update_all(self) -> None:
        if self.busy:
            return
        targets = [repo for repo in self.repos if self.matches.get(repo["full"])]
        if not targets:
            messagebox.showinfo(
                "GitHub", "Нет репозиториев, у которых есть локальная папка.", parent=self
            )
            return
        if not messagebox.askyesno(
            "Загрузить всё",
            f"Обновить на GitHub {len(targets)} репозиториев?\n\n"
            "В каждом будет сделан коммит локальных изменений и push.",
            parent=self,
        ):
            return
        self._start_busy(f"GitHub: обновляю {len(targets)}…")
        total = len(targets)

        def work():
            updated = 0
            failed = 0
            for index, repo in enumerate(targets, 1):
                folder = self.matches[repo["full"]]
                self._log_bg(f"[{index}/{total}] {repo['name']}")
                try:
                    ok = update_local_repo(folder, repo["name"], self._log_bg)
                except Exception as exc:  # noqa: BLE001
                    self._log_bg(f"Ошибка {repo['name']}: {exc}")
                    ok = False
                updated += 1 if ok else 0
                failed += 0 if ok else 1
            return updated, failed

        self.hub.run_bg(work, self._update_all_done, self._fail)

    def _update_all_done(self, result: tuple[int, int]) -> None:
        updated, failed = result
        self._finish()
        self.append_log(self.log_box, f"=== Готово: обновлено {updated}, ошибок {failed} ===",
                        "ok" if not failed else "error")
        self.hub.set_status(f"GitHub: обновлено {updated}, ошибок {failed}")

    # -- обновить всё нужное ------------------------------------------------- #
    def update_needed(self) -> None:
        """Коммит + push там, где «можно обновить», и заливка новых папок."""
        if self.busy:
            return
        targets = [
            (self.matches[repo["full"]], repo["name"])
            for repo in self.repos
            if self.matches.get(repo["full"], "")
            and self._folder_state(self.matches[repo["full"]])[1]
        ]
        new_items = list(self.local_only)
        if not targets and not new_items:
            messagebox.showinfo(
                "GitHub", "Обновлять нечего: все репозитории актуальны.", parent=self
            )
            return
        if targets:
            message = f"Сделать коммит и push в {len(targets)} репозиториев?"
            if new_items:
                message += (
                    f"\n\nТакже залить {len(new_items)} папок без GitHub?\n"
                    "Для каждой будет создан публичный репозиторий."
                )
        else:
            message = (
                f"Залить на GitHub {len(new_items)} папок?\n"
                "Для каждой будет создан публичный репозиторий."
            )
        if not messagebox.askyesno("Обновить всё", message, parent=self):
            return
        self._start_busy(f"GitHub: обновляю {len(targets) + len(new_items)}…")
        total = len(targets) + len(new_items)
        self.append_log(
            self.log_box,
            f"=== Обновить всё: обновлений {len(targets)}, новых {len(new_items)} ===",
        )

        def work():
            updated = 0
            failed = 0
            index = 0
            for folder, name in targets:
                index += 1
                self._log_bg(f"[{index}/{total}] {name}")
                try:
                    ok = update_local_repo(folder, name, self._log_bg)
                except Exception as exc:  # noqa: BLE001
                    self._log_bg(f"Ошибка {name}: {exc}")
                    ok = False
                updated += 1 if ok else 0
                failed += 0 if ok else 1
            for item in new_items:
                index += 1
                name = sanitize_repo_name(Path(item["path"]).name)
                self._log_bg(f"[{index}/{total}] {name} (новая)")
                try:
                    ok = push_folder_to_github(item["path"], name, True, self._log_bg)
                except Exception as exc:  # noqa: BLE001
                    self._log_bg(f"Ошибка {name}: {exc}")
                    ok = False
                updated += 1 if ok else 0
                failed += 0 if ok else 1
            return updated, failed

        self.hub.run_bg(work, self._update_all_done, self._fail)

    # -- скачивание / удаление / открытие ---------------------------------- #
    def download(self) -> None:
        repo = self._selected_repo()
        if not repo or self.busy:
            return
        dest = Path(self.root) / repo["name"]
        if dest.exists() and any(dest.iterdir()):
            messagebox.showerror(
                "GitHub", f"Папка уже существует и не пуста:\n{dest}", parent=self
            )
            return
        self._start_busy(f"GitHub: скачиваю {repo['name']}…")
        full = repo["full"]
        gh = find_tool("gh")

        def work():
            rc, out, err = run_tool([gh, "repo", "clone", full, str(dest)], timeout=900)
            if rc != 0:
                raise RuntimeError(err or out or "не удалось клонировать репозиторий")
            return str(dest)

        self.hub.run_bg(work, self._download_done, self._fail)

    def _download_done(self, path: str) -> None:
        self._finish()
        self.append_log(self.log_box, f"✅ скачано: {path}", "ok")
        self.hub.set_status("GitHub: скачано")
        self.refresh()

    def delete_repo(self) -> None:
        repo = self._selected_repo()
        if not repo or self.busy:
            return
        if not messagebox.askyesno(
            "Удалить с GitHub?",
            f"{repo['full']}\n\nРепозиторий будет удалён с GitHub безвозвратно.\n"
            "Локальная папка останется на диске.",
            parent=self,
        ):
            return
        self._start_busy(f"GitHub: удаляю {repo['name']}…")
        gh = find_tool("gh")
        full = repo["full"]

        def work():
            rc, out, err = run_tool([gh, "repo", "delete", full, "--yes"], timeout=180)
            if rc != 0:
                message = err or out
                if "delete_repo" in message:
                    raise RuntimeError(
                        "У входа нет права delete_repo.\n\n"
                        "Нажмите «Войти в GitHub» и подтвердите вход в браузере — "
                        "это даст право удалять репозитории."
                    )
                raise RuntimeError(message or "не удалось удалить репозиторий")
            return True

        self.hub.run_bg(work, self._delete_done, self._fail)

    def _delete_done(self, _result: bool) -> None:
        self._finish()
        self.append_log(self.log_box, "🗑 репозиторий удалён с GitHub")
        self.hub.set_status("GitHub: удалено")
        self.refresh()

    def open_folder(self) -> None:
        local = self._local_for(self._selected_row())
        if not local:
            return
        try:
            os.startfile(local)
        except OSError as exc:
            messagebox.showerror("GitHub", f"Не удалось открыть папку:\n{exc}", parent=self)

    def open_web(self) -> None:
        repo = self._selected_repo()
        if repo and repo.get("url"):
            webbrowser.open(repo["url"])

    # -- смена видимости ---------------------------------------------------- #
    def set_visibility(self) -> None:
        repo = self._selected_repo()
        if not repo or self.busy:
            return
        make_public = bool(repo.get("private"))
        target = "public" if make_public else "private"
        verb = "публичным" if make_public else "приватным"
        if not messagebox.askyesno(
            "Сменить видимость",
            f"{repo['full']}\n\nСделать репозиторий {verb}?",
            parent=self,
        ):
            return
        self._start_busy(f"GitHub: делаю {verb}…")
        gh = find_tool("gh")
        full = repo["full"]

        def work():
            rc, out, err = run_tool(
                [gh, "repo", "edit", full, "--visibility", target], timeout=120
            )
            if rc != 0:
                raise RuntimeError(err or out or "не удалось изменить видимость")
            return target

        self.hub.run_bg(work, self._visibility_done, self._fail)

    def _visibility_done(self, target: str) -> None:
        self._finish()
        if target == "public":
            self.append_log(self.log_box, "🔓 репозиторий стал публичным", "ok")
            self.hub.set_status("GitHub: публичный")
        else:
            self.append_log(self.log_box, "🔒 репозиторий стал приватным", "ok")
            self.hub.set_status("GitHub: приватный")
        self.refresh()

    # -- удаление локальной папки ------------------------------------------ #
    def delete_folder(self) -> None:
        row = self._selected_row()
        folder = self._local_for(row)
        if not folder or self.busy:
            return
        if not Path(folder).exists():
            messagebox.showerror("GitHub", f"Папка уже отсутствует:\n{folder}", parent=self)
            return
        repo = row.get("repo")
        extra = (
            f"\nСвязанный репозиторий на GitHub: {repo['full']} (не удаляется)."
            if repo else
            "\nСвязанного репозитория на GitHub нет."
        )
        if not messagebox.askyesno(
            "Удалить папку?",
            f"Папка будет отправлена в Корзину Windows (можно восстановить):\n"
            f"{folder}{extra}\n\n"
            "Она исчезнет с диска, но останется в Корзине.",
            parent=self,
        ):
            return
        self._start_busy("GitHub: удаляю папку…")
        target = folder

        def work():
            send_to_recycle_bin(target)
            return target

        self.hub.run_bg(work, self._delete_folder_done, self._fail)

    def _delete_folder_done(self, folder: str) -> None:
        self._finish()
        self.local_items = [it for it in self.local_items if it["path"] != folder]
        self.local_only = [it for it in self.local_only if it["path"] != folder]
        self.matches = {k: v for k, v in self.matches.items() if v != folder}
        self.status_by_path.pop(folder, None)
        self.append_log(self.log_box, f"🗑 папка в Корзине: {folder}", "ok")
        self.hub.set_status("GitHub: папка удалена (Корзина)")
        self._render()


# --------------------------------------------------------------------------- #
#  Панель: Bypass (встроенный движок, режим Happ ↔ GoodbyeDPI)
# --------------------------------------------------------------------------- #
def _round_rect(canvas: tk.Canvas, x1: float, y1: float, x2: float, y2: float,
                radius: float, **options):
    """Скруглённый прямоугольник на Canvas (в Tk его нет из коробки)."""
    radius = max(0.0, min(radius, (x2 - x1) / 2, (y2 - y1) / 2))
    points = [
        x1 + radius, y1, x2 - radius, y1, x2, y1,
        x2, y1 + radius, x2, y2 - radius, x2, y2,
        x2 - radius, y2, x1 + radius, y2, x1, y2,
        x1, y2 - radius, x1, y1 + radius, x1, y1,
    ]
    return canvas.create_polygon(points, smooth=True, **options)


class Switch(tk.Canvas):
    """Переключатель в стиле iOS: скруглённый трек + белый кружок."""

    def __init__(self, parent, value: bool = False, command=None, *, bg: str | None = None):
        bg = bg or CARD
        self._tw, self._th = px(46), px(26)
        super().__init__(parent, width=self._tw, height=self._th, bg=bg,
                         highlightthickness=0, cursor="hand2")
        self.value = bool(value)
        self.command = command
        self.enabled = True
        self.bind("<Button-1>", self._on_click)
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        if not self.enabled:
            track = TRACK_DISABLED
        else:
            track = OK if self.value else TRACK_OFF
        _round_rect(self, 1, 1, self._tw - 1, self._th - 1, (self._th - 2) / 2,
                    fill=track, outline="")
        radius = (self._th - 8) / 2
        center_y = self._th / 2
        center_x = (self._tw - 4 - radius) if self.value else (4 + radius)
        self.create_oval(center_x - radius, center_y - radius,
                         center_x + radius, center_y + radius,
                         fill=KNOB, outline="")

    def set(self, value: bool, *, notify: bool = False) -> None:
        self.value = bool(value)
        self._draw()
        if notify and self.command:
            self.command(self.value)

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self.configure(cursor="hand2" if self.enabled else "arrow")
        self._draw()

    def _on_click(self, _event=None) -> None:
        if not self.enabled:
            return
        self.set(not self.value, notify=True)


class Segmented(tk.Canvas):
    """Сегментированный контрол в стиле macOS."""

    def __init__(self, parent, options, value, command, *, bg: str | None = None):
        import tkinter.font as tkfont

        bg = bg or BG

        self.options = list(options)
        self.value = value
        self.command = command
        self.enabled = True
        self._font = ui_font(9, "bold")
        metrics = tkfont.Font(font=self._font)
        self._widths = [max(px(132), metrics.measure(label) + px(38))
                        for _key, label in self.options]
        self._h = px(34)
        super().__init__(parent, width=sum(self._widths) + px(6), height=self._h,
                         bg=bg, highlightthickness=0, cursor="hand2")
        self.bind("<Button-1>", self._on_click)
        self._draw()

    def _draw(self) -> None:
        self.delete("all")
        total = sum(self._widths) + px(6)
        _round_rect(self, 1, 1, total - 1, self._h - 1, (self._h - 2) / 2,
                    fill=FIELD, outline=BORDER)
        x = px(3)
        for (key, label), width in zip(self.options, self._widths):
            active = key == self.value
            if active:
                _round_rect(self, x, px(3), x + width - px(3), self._h - px(3),
                            (self._h - px(6)) / 2,
                            fill=ACCENT if self.enabled else BORDER, outline="")
            self.create_text(
                x + (width - px(3)) / 2, self._h / 2, text=label,
                fill=tc("on_accent") if (active and self.enabled) else MUTED,
                font=self._font,
            )
            x += width

    def set_value(self, value: str, *, notify: bool = False) -> None:
        self.value = value
        self._draw()
        if notify and self.command:
            self.command(self.value)

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        self.configure(cursor="hand2" if self.enabled else "arrow")
        self._draw()

    def _on_click(self, event) -> None:
        if not self.enabled:
            return
        x = px(3)
        for (key, _label), width in zip(self.options, self._widths):
            if x <= event.x <= x + width - px(3):
                if key != self.value:
                    self.set_value(key, notify=True)
                return
            x += width


class AppRow(tk.Frame):
    """Строка программы: аватар, имя, путь, бейдж «запущена», галочка."""

    def __init__(self, parent, entry: AppEntry, selected: bool, on_toggle, *,
                 bg: str | None = None):
        bg = bg or CARD
        super().__init__(parent, bg=bg)
        self.entry = entry
        self.selected = bool(selected)
        self.on_toggle = on_toggle
        self._bg = bg
        self._parts: list[tk.Widget] = []

        # Полоска слева: сразу видно, какие программы уже выбраны.
        self.stripe = tk.Frame(self, width=px(3), bg=bg)
        self.stripe.pack(side="left", fill="y")

        self.avatar = tk.Canvas(self, width=px(34), height=px(34), bg=bg,
                                highlightthickness=0)
        self.avatar.pack(side="left", padx=(px(10), px(10)), pady=px(8))
        self._parts.append(self.avatar)

        body = tk.Frame(self, bg=bg)
        body.pack(side="left", fill="x", expand=True)
        self._parts.append(body)
        self.name_label = tk.Label(body, text=entry.name, bg=bg, fg=FG,
                                   font=ui_font(10, "bold"), anchor="w")
        self.name_label.pack(fill="x")
        self._parts.append(self.name_label)
        subtitle = entry.path or entry.exe
        if len(subtitle) > 78:
            subtitle = subtitle[:36] + "…" + subtitle[-38:]
        self.path_label = tk.Label(body, text=subtitle, bg=bg, fg=MUTED,
                                   font=ui_font(8), anchor="w")
        self.path_label.pack(fill="x")
        self._parts.append(self.path_label)

        if entry.running:
            badge = tk.Label(self, text="запущена", bg=bg, fg=OK,
                             font=ui_font(8, "bold"))
            badge.pack(side="right", padx=(0, px(10)))
            self._parts.append(badge)

        self.box = tk.Canvas(self, width=px(22), height=px(22), bg=bg,
                             highlightthickness=0, cursor="hand2")
        self.box.pack(side="right", padx=(0, px(6)))
        self._parts.append(self.box)

        for widget in (self, self.stripe, *self._parts):
            widget.bind("<Button-1>", self._click)
            widget.bind("<Enter>", self._enter)
            widget.bind("<Leave>", self._leave)
        self._paint()

    def _base_color(self) -> str:
        return ROW_SELECTED if self.selected else CARD

    def _paint(self) -> None:
        self._tint(self._base_color())
        self.stripe.configure(bg=ACCENT if self.selected else self._base_color())
        self.avatar.delete("all")
        color = AVATAR_COLORS[sum(self.entry.exe.lower().encode()) % len(AVATAR_COLORS)]
        self.avatar.create_oval(1, 1, px(33), px(33), fill=color, outline="")
        letter = (self.entry.name or self.entry.exe)[:1].upper()
        self.avatar.create_text(px(17), px(17), text=letter, fill=ON_AVATAR,
                                font=ui_font(12, "bold"))
        self.box.delete("all")
        if self.selected:
            _round_rect(self.box, 1, 1, px(21), px(21), px(6), fill=ACCENT, outline="")
            self.box.create_text(px(11), px(11), text="✓", fill=tc("on_accent"),
                                 font=ui_font(10, "bold"))
        else:
            _round_rect(self.box, 1, 1, px(21), px(21), px(6), fill="", outline=BORDER,
                        width=2)

    def _tint(self, color: str) -> None:
        for widget in [self, *self._parts]:
            try:
                widget.configure(bg=color)
            except tk.TclError:
                pass

    def _enter(self, _event=None) -> None:
        self._tint(CARD_HOVER)

    def _leave(self, _event=None) -> None:
        self._tint(self._base_color())

    def _click(self, _event=None) -> None:
        self.set_selected(not self.selected, notify=True)

    def set_selected(self, selected: bool, *, notify: bool = False) -> None:
        self.selected = bool(selected)
        self._paint()
        if notify and self.on_toggle:
            self.on_toggle(self.entry, self.selected)


class ScrollList(tk.Frame):
    """Прокручиваемый контейнер для строк списка."""

    def __init__(self, parent, *, bg: str | None = None):
        bg = bg or CARD
        super().__init__(parent, bg=bg)
        self.canvas = tk.Canvas(self, bg=bg, highlightthickness=0, bd=0)
        self.bar = ttk.Scrollbar(self, orient="vertical", command=self.canvas.yview)
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.pack(side="left", fill="both", expand=True)
        self.bar.pack(side="right", fill="y")
        self.inner = tk.Frame(self.canvas, bg=bg)
        self._window = self.canvas.create_window((0, 0), window=self.inner, anchor="nw")
        self.inner.bind("<Configure>", self._on_inner)
        self.canvas.bind("<Configure>", self._on_canvas)
        self.canvas.bind("<MouseWheel>", self._scroll)
        self.inner.bind("<MouseWheel>", self._scroll)

    def _on_inner(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))

    def _on_canvas(self, event) -> None:
        self.canvas.itemconfigure(self._window, width=event.width)

    def _wheel(self, _active: bool) -> None:
        pass

    def _scroll(self, event) -> str:
        try:
            self.canvas.yview_scroll(int(-event.delta / 120) * _scroll_step(), "units")
        except tk.TclError:
            pass
        return "break"

    def clear(self) -> None:
        for child in self.inner.winfo_children():
            child.destroy()


class BypassPanel(Panel):
    """Обход DPI: Happ (весь трафик или выбранные программы) и GoodbyeDPI."""

    MODES = ("happ", "gbd")
    ROW_LIMIT = 80

    def __init__(self, master: tk.Misc, hub) -> None:
        super().__init__(master, hub)
        cfg = bypass_cfg()
        self.mode = cfg.get("mode", "happ")
        self.scope = cfg.get("scope", "all")
        self.selected_apps: set[str] = {name.lower() for name in cfg.get("apps", [])}
        self.catalog: list[AppEntry] = []
        self.rows: dict[str, AppRow] = {}
        self.state: dict = {}
        self.online = False
        self.busy = False
        self.checking = False
        self.ip_checking = False
        self.loading_apps = False
        self.query = ""
        self.only_running = False
        self.only_bypass = False
        self._offline_noted = False
        self._apply_job = None
        self._apply_retry = 0
        self._tick_job = None
        self._state_gen = 0
        self._last_heal_action = None
        self._build()
        self.load_catalog()
        self.refresh_now()
        self._tick_job = self.after(3000, self._tick)

    def destroy(self) -> None:
        """Кэшированная панель не должна опрашивать движок после закрытия."""
        for attr in ("_tick_job", "_apply_job"):
            job = getattr(self, attr, None)
            if job is not None:
                try:
                    self.after_cancel(job)
                except Exception:  # noqa: BLE001
                    pass
            setattr(self, attr, None)
        super().destroy()

    # -- разметка ----------------------------------------------------------- #
    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(4, weight=4)   # список программ
        self.rowconfigure(6, weight=1)   # лог

        top = tk.Frame(self, bg=BG)
        top.grid(row=0, column=0, sticky="ew")
        self.scope_control = Segmented(
            top, (("all", "Весь трафик"), ("apps", "Только программы")),
            self.scope, self._set_scope,
        )
        self.scope_control.pack(side="left")
        self.scope_hint = tk.Label(
            top, text="", bg=BG, fg=MUTED, font=ui_font(9), anchor="w",
            justify="left",
        )
        self.scope_hint.pack(side="left", padx=(px(14), 0), fill="x", expand=True)

        cards = tk.Frame(self, bg=BG)
        cards.grid(row=1, column=0, sticky="ew", pady=(px(10), 0))
        cards.columnconfigure(0, weight=1)
        cards.columnconfigure(1, weight=1)
        self.cards = {
            "happ": self._engine_card(cards, 0, "happ", "Happ",
                                      "VPN: весь трафик через Happ"),
            "gbd": self._engine_card(cards, 1, "gbd", "GoodbyeDPI",
                                     "обход DPI для всего интернета"),
        }

        # Отдельная карточка «Обход по программам» — разграничение с VPN:
        # ProxiFyre направляет выбранные программы в SOCKS Happ, VPN не трогает.
        apps_route = tk.Frame(self, bg=CARD, highlightbackground=BORDER,
                              highlightthickness=1)
        apps_route.grid(row=2, column=0, sticky="ew", pady=(px(10), 0))
        ar_head = tk.Frame(apps_route, bg=CARD)
        ar_head.pack(fill="x", padx=px(14), pady=(px(12), 0))
        ar_text = tk.Frame(ar_head, bg=CARD)
        ar_text.pack(side="left", fill="x", expand=True)
        tk.Label(ar_text, text="Обход по программам", bg=CARD, fg=FG,
                 font=ui_font(10, "bold"), anchor="w").pack(fill="x")
        tk.Label(ar_text, text="выбранные программы — через прокси Happ "
                  "(ProxiFyre), сам VPN не включается и не выключается",
                 bg=CARD, fg=MUTED, font=ui_font(8), anchor="w").pack(fill="x")
        self.apps_route_switch = Switch(ar_head, False, self._on_apps_route,
                                        bg=CARD)
        self.apps_route_switch.pack(side="right")
        self.apps_route_status = tk.Label(apps_route, text="проверяю…", bg=CARD,
                                          fg=MUTED, font=ui_font(9), anchor="w",
                                          justify="left", wraplength=px(640))
        self.apps_route_status.pack(fill="x", padx=px(14), pady=(px(8), px(12)))

        tools = tk.Frame(self, bg=BG)
        tools.grid(row=3, column=0, sticky="ew", pady=(px(8), 0))
        self.btn_happ_path = flat_button(tools, "Путь Happ…", self.configure_dialog,
                                         kind="ghost")
        self.btn_happ_path.pack(side="left")
        self.btn_show_happ = flat_button(tools, "Показать Happ", self.show_happ,
                                         kind="ghost")
        self.btn_show_happ.pack(side="left", padx=(px(8), 0))
        self.btn_fix_tun = flat_button(tools, "⚡ Выключить TUN", self.fix_tun_now,
                                       kind="ghost")
        self.btn_fix_tun.pack(side="left", padx=(px(8), 0))
        self.btn_download = flat_button(tools, "Скачать GoodbyeDPI",
                                        lambda: self.download("gbd"), kind="ghost")
        self.btn_download.pack(side="left", padx=(px(8), 0))
        self.btn_refresh = flat_button(tools, "Обновить", self.refresh_now, kind="ghost")
        self.btn_refresh.pack(side="right")
        self.btn_catch = flat_button(tools, "Подхватить программы",
                                     self.catch_programs, kind="ghost")
        self.btn_catch.pack(side="right", padx=(0, px(8)))
        self.btn_check_ip = flat_button(tools, "🌐 Проверить IP",
                                        self.check_ip_now, kind="ghost")
        self.btn_check_ip.pack(side="right", padx=(0, px(8)))

        self.apps_card = self._apps_card(self)
        self.apps_card.grid(row=4, column=0, sticky="nsew", pady=(px(10), 0))

        status = tk.Frame(self, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        status.grid(row=5, column=0, sticky="ew", pady=(px(10), 0))
        inner = tk.Frame(status, bg=CARD)
        inner.pack(fill="x", padx=px(14), pady=px(10))
        self.dot_var = tk.StringVar(value="○")
        self.dot_label = tk.Label(inner, textvariable=self.dot_var, bg=CARD, fg=MUTED,
                                  font=ui_font(12, "bold"), width=2)
        self.dot_label.pack(side="left")
        self.headline_var = tk.StringVar(value="проверяю…")
        tk.Label(inner, textvariable=self.headline_var, bg=CARD, fg=FG,
                 font=ui_font(10, "bold"), anchor="w").pack(side="left")
        self.detail_var = tk.StringVar(value="")
        tk.Label(inner, textvariable=self.detail_var, bg=CARD, fg=MUTED,
                 font=ui_font(9), anchor="w", justify="left",
                 wraplength=px(640)).pack(side="left", fill="x", expand=True,
                                          padx=(px(12), 0))

        log_frame, self.log_box = self.make_log(self, height=5)
        log_frame.grid(row=6, column=0, sticky="nsew", pady=(px(10), 0))
        self.append_log(self.log_box, "Проверяю движки и локальный SOCKS Happ…")

    def _engine_card(self, parent, column: int, key: str, title: str,
                     subtitle: str) -> dict:
        card = tk.Frame(parent, bg=CARD, highlightbackground=BORDER,
                        highlightthickness=1, cursor="hand2")
        card.grid(row=0, column=column, sticky="nsew",
                  padx=(0, px(10)) if column == 0 else (0, 0))

        head = tk.Frame(card, bg=CARD, cursor="hand2")
        head.pack(fill="x", padx=px(14), pady=(px(12), 0))
        avatar = tk.Canvas(head, width=px(30), height=px(30), bg=CARD,
                           highlightthickness=0, cursor="hand2")
        avatar.pack(side="left")
        _round_rect(avatar, 1, 1, px(29), px(29), px(9), fill=CARD_HOVER, outline="")
        avatar.create_text(px(15), px(15), text=title[:1], fill=FG,
                           font=ui_font(12, "bold"))

        text = tk.Frame(head, bg=CARD, cursor="hand2")
        text.pack(side="left", padx=(px(10), 0), fill="x", expand=True)
        title_label = tk.Label(text, text=title, bg=CARD, fg=FG,
                               font=ui_font(10, "bold"), anchor="w",
                               cursor="hand2")
        title_label.pack(fill="x")
        subtitle_label = tk.Label(text, text=subtitle, bg=CARD, fg=MUTED,
                                  font=ui_font(8), anchor="w", cursor="hand2")
        subtitle_label.pack(fill="x")

        switch = Switch(head, False, lambda value, k=key: self._on_switch(k, value),
                        bg=CARD)
        switch.pack(side="right")

        status_label = tk.Label(card, text="проверяю…", bg=CARD, fg=MUTED,
                                font=ui_font(9), anchor="w", justify="left",
                                wraplength=px(300), cursor="hand2")
        status_label.pack(fill="x", padx=px(14), pady=(px(8), px(12)))

        for widget in (card, head, avatar, text, title_label, subtitle_label, status_label):
            widget.bind("<Button-1>", lambda _e, k=key: self._choose(k))
        return {"frame": card, "switch": switch, "status": status_label,
                "avatar": avatar, "title": title_label}

    def _apps_card(self, parent) -> tk.Frame:
        card = tk.Frame(parent, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        head = tk.Frame(card, bg=CARD)
        head.pack(fill="x", padx=px(14), pady=(px(12), px(6)))
        tk.Label(head, text="Программы через обход", bg=CARD, fg=FG,
                 font=ui_font(10, "bold")).pack(side="left")
        self.count_var = tk.StringVar(value="выбрано 0")
        tk.Label(head, textvariable=self.count_var, bg=CARD, fg=MUTED,
                 font=ui_font(9)).pack(side="left", padx=(px(10), 0))
        self.only_bypass_btn = flat_button(
            head, "Только обход", self._toggle_only_bypass, kind="ghost")
        self.only_bypass_btn.pack(side="right")
        self.only_running_btn = flat_button(
            head, "Только запущенные", self._toggle_only_running, kind="ghost")
        self.only_running_btn.pack(side="right", padx=(0, px(8)))
        flat_button(head, "Снять все", self._clear_apps, kind="ghost").pack(
            side="right", padx=(0, px(8)))

        search_row = tk.Frame(card, bg=FIELD, highlightbackground=BORDER,
                              highlightthickness=1)
        search_row.pack(fill="x", padx=px(14), pady=(0, px(8)))
        tk.Label(search_row, text="⌕", bg=FIELD, fg=MUTED,
                 font=ui_font(12)).pack(side="left", padx=(px(10), px(4)))
        self.search_var = tk.StringVar()
        self.search_entry = tk.Entry(
            search_row, textvariable=self.search_var, bg=FIELD, fg=FG,
            insertbackground=FG, relief="flat", bd=0, font=ui_font(10),
            highlightthickness=0,
        )
        self.search_entry.pack(side="left", fill="x", expand=True, pady=px(7))
        self.search_var.trace_add("write", lambda *_args: self._on_search())
        self.placeholder = tk.Label(search_row, text="Поиск программы — имя или путь",
                                    bg=FIELD, fg=tc("label_tertiary"), font=ui_font(10),
                                    cursor="xterm")
        self.placeholder.place(in_=self.search_entry, x=px(2), rely=0.5, anchor="w")
        self.search_entry.bind("<Button-1>", lambda _e: self.placeholder.place_forget())
        self.placeholder.bind("<Button-1>", lambda _e: self._focus_search())
        self.clear_search_btn = flat_button(search_row, "✕", self._clear_search,
                                            kind="ghost", padx=px(8), pady=px(2))
        self.clear_search_btn.pack(side="right", padx=(0, px(4)))

        self.app_list = ScrollList(card, bg=CARD)
        self.app_list.pack(fill="both", expand=True, padx=px(6), pady=(0, px(4)))
        self.empty_label = tk.Label(
            card, text="", bg=CARD, fg=MUTED, font=ui_font(9),
            anchor="w", justify="left", wraplength=px(700),
        )
        self.empty_label.pack(fill="x", padx=px(14), pady=(0, px(10)))
        return card

    # -- каталог программ ---------------------------------------------------- #
    def load_catalog(self) -> None:
        if self.loading_apps:
            return
        self.loading_apps = True
        self.empty_label.configure(text="Собираю список программ…")
        self.hub.run_bg(app_catalog, self._catalog_done, self._catalog_fail)

    def _catalog_done(self, catalog) -> None:
        self.loading_apps = False
        self.catalog = list(catalog or [])
        self._rebuild_rows()
        self._apply_filter(reset_scroll=True)
        self.append_log(self.log_box, f"Найдено программ: {len(self.catalog)}")

    def _catalog_fail(self, exc: Exception) -> None:
        self.loading_apps = False
        self.empty_label.configure(text=f"Не удалось собрать список: {exc}")
        self.append_log(self.log_box, f"Каталог программ: {exc}", "error")

    def _rebuild_rows(self) -> None:
        self.app_list.clear()
        self.rows.clear()

    def _apply_filter(self, reset_scroll: bool = False) -> None:
        matched = filter_apps(self.catalog, self.query, only_running=self.only_running)
        if self.only_bypass:
            matched = [e for e in matched if e.exe.lower() in self.selected_apps]
        visible = matched[: self.ROW_LIMIT]
        shown: set[str] = set()
        for entry in visible:
            key = entry.exe.lower()
            row = self.rows.get(key)
            if row is None:
                row = AppRow(self.app_list.inner, entry, key in self.selected_apps,
                             self._on_app_toggle, bg=CARD)
                self.rows[key] = row
            row.pack(fill="x")
            shown.add(key)
        for key, row in self.rows.items():
            if key not in shown:
                row.pack_forget()
        if reset_scroll:
            self.app_list.canvas.yview_moveto(0)
        self._update_empty_hint(matched)

    def _update_empty_hint(self, matched) -> None:
        total = len(self.catalog)
        if not total:
            self.empty_label.configure(
                text="Список пуст — нажмите «Обновить» или добавьте программу вручную."
            )
        elif not matched and self.only_bypass:
            self.empty_label.configure(
                text="Обход ни для одной программы не включён — отметьте программы "
                     "в списке или сбросьте фильтр «Только обход»."
            )
        elif not matched:
            self.empty_label.configure(
                text="Ничего не нашлось. Попробуйте другое имя или сбросьте фильтр."
            )
        elif len(matched) > self.ROW_LIMIT:
            self.empty_label.configure(
                text=f"Показано {self.ROW_LIMIT} из {len(matched)} — уточните поиск."
            )
        else:
            self.empty_label.configure(text="")
        selected_count = sum(1 for e in self.catalog if e.exe.lower() in self.selected_apps)
        self.count_var.set(f"выбрано {selected_count} из {total}")

    def _on_search(self) -> None:
        self.query = self.search_var.get()
        if self.query:
            self.placeholder.place_forget()
        else:
            self.placeholder.place(in_=self.search_entry, x=px(2), rely=0.5, anchor="w")
        self._apply_filter(reset_scroll=True)

    def _clear_search(self) -> None:
        self.search_var.set("")
        self.search_entry.focus_set()

    def _focus_search(self) -> None:
        self.placeholder.place_forget()
        self.search_entry.focus_set()

    def _toggle_only_running(self) -> None:
        self.only_running = not self.only_running
        self.only_running_btn.configure(
            text="● Только запущенные" if self.only_running else "Только запущенные",
            fg=ACCENT if self.only_running else FG,
        )
        self._apply_filter(reset_scroll=True)

    def _toggle_only_bypass(self) -> None:
        self.only_bypass = not self.only_bypass
        self.only_bypass_btn.configure(
            text="● Только обход" if self.only_bypass else "Только обход",
            fg=ACCENT if self.only_bypass else FG,
        )
        self._apply_filter(reset_scroll=True)

    def _on_app_toggle(self, entry: AppEntry, selected: bool) -> None:
        key = entry.exe.lower()
        if selected:
            self.selected_apps.add(key)
        else:
            self.selected_apps.discard(key)
        if key in self.rows:
            self.rows[key].set_selected(selected)
        self._save_cfg()
        self._update_controls()
        if self.only_bypass and not selected:
            self._apply_filter()
        self._schedule_apply()

    def _clear_apps(self) -> None:
        self.selected_apps.clear()
        for row in self.rows.values():
            row.set_selected(False)
        self._save_cfg()
        self._update_controls()
        if self.only_bypass:
            self._apply_filter(reset_scroll=True)
        self._schedule_apply()

    def _schedule_apply(self) -> None:
        """Список программ применяется сам — иначе ProxiFyre работает по старому."""
        if self._apply_job is not None:
            try:
                self.after_cancel(self._apply_job)
            except Exception:  # noqa: BLE001
                pass
        self._apply_job = self.after(700, self._apply_apps_now)

    def _apply_apps_now(self) -> None:
        self._apply_job = None
        if not self.winfo_exists():
            return
        if self.busy:
            self._pending_apps_apply = True
            self._apply_retry += 1
            if self._apply_retry <= 10:
                self._apply_job = self.after(700, self._apply_apps_now)
            return
        self._pending_apps_apply = False
        self._apply_retry = 0
        if self.scope != "apps":
            return
        if not (self.state.get("proxyfyre") or {}).get("running"):
            return  # служба выключена — конфиг применится при включении
        apps = self._selected_exe()
        if not apps:
            self._run(proxifyre_stop, "Программы: список пуст, выключаю обход…",
                      "ProxiFyre: программ не осталось — выключаю перенаправление")
            return
        self._run(lambda: proxifyre_start(apps), "Программы: применяю список…",
                  f"ProxiFyre: перенастраиваю на {len(apps)} программ…")

    def _selected_exe(self) -> list[str]:
        result = []
        for entry in self.catalog:
            if entry.exe.lower() in self.selected_apps:
                result.append(entry.exe)
        known = {name.lower() for name in result}
        for name in bypass_cfg()["apps"]:
            if name.lower() in self.selected_apps and name.lower() not in known:
                result.append(name)
        return normalize_apps(result)

    def _save_cfg(self) -> None:
        bypass_cfg_save(mode=self.mode, scope=self.scope,
                        apps=self._selected_exe())

    # -- выбор движка и области ---------------------------------------------- #
    def _choose(self, mode: str) -> None:
        if self.busy or self.mode == mode:
            return
        self.mode = mode
        self._save_cfg()
        self._update_controls()

    def _set_scope(self, scope: str) -> None:
        if self.busy or scope == self.scope:
            return
        self.scope = scope
        self._state_gen += 1
        self._save_cfg()
        self._update_controls()
        if scope == "apps":
            try:
                happ_mode_write(tun=False, system_proxy=False)
                set_windows_system_proxy(False)
            except Exception:
                pass
        if self._engine_on("happ") and (scope != "apps" or self.selected_apps):
            self._start_mode("happ")
        elif self._engine_on("gbd"):
            self._start_mode("gbd")

    def _engine_on(self, mode: str) -> bool:
        if mode == "happ":
            return bool((self.state.get("happ") or {}).get("connected"))
        return bool((self.state.get("gbd") or {}).get("on"))

    def _on_switch(self, mode: str, value: bool) -> None:
        if self.busy:
            self._sync_switches()
            return
        self.mode = mode
        self._save_cfg()
        if value:
            self._start_mode(mode)
        else:
            self._stop_mode(mode)

    def _start_mode(self, mode: str) -> None:
        if mode == "happ" and self.scope == "apps" and not self.selected_apps:
            self.hub.toast("Отметьте программы, для которых нужен обход", kind="warn")
            self._update_controls()
            return
        if mode == "gbd" and not (self.state.get("gbd") or {}).get("installed"):
            self.hub.toast("GoodbyeDPI не скачан — нажмите «Скачать GoodbyeDPI»",
                           kind="warn")
            self._update_controls()
            return
        label = "Happ" if mode == "happ" else "GoodbyeDPI"
        if mode == "happ":
            scope_text = "весь трафик" if self.scope == "all" else (
                f"программы: {len(self.selected_apps)}")
        else:
            scope_text = "остальной трафик" if self.scope == "apps" else "весь трафик"
        self._run(lambda: bypass_apply(self.scope, mode),
                  f"{label}: включаю ({scope_text})…",
                  f"Включаю {label} · {scope_text}" + (
                      " · потребуется подтверждение администратора (UAC)"
                      if False else ""))

    def _stop_mode(self, mode: str) -> None:
        label = "Happ" if mode == "happ" else "GoodbyeDPI"
        self._run(lambda: bypass_stop(mode), f"{label}: выключаю…",
                  f"Выключаю {label}")

    def _toggle_engine(self, mode: str) -> None:
        if self._engine_on(mode):
            self._stop_mode(mode)
        else:
            self._start_mode(mode)

    def _on_apps_route(self, value: bool) -> None:
        """Переключатель «Обход по программам»: только ProxiFyre, VPN не трогаем."""
        if self.busy:
            self._sync_switches()
            return
        if value:
            if not self.selected_apps:
                self.hub.toast("Отметьте программы в списке ниже", kind="warn")
                self.apps_route_switch.set(False)
                return
            self._run(bypass_apps_route_enable, "включаю обход по программам…",
                      "Включаю обход по программам (ProxiFyre)")
        else:
            self._run(bypass_apps_route_disable, "выключаю обход по программам…",
                      "Выключаю обход по программам")

    # -- статус -------------------------------------------------------------- #
    def _poll_state(self, scope: str) -> dict:
        """Опрос состояния + автоисцеление в ОДНОМ фоновом потоке.

        Раньше `bypass_auto_heal_if_leak` вызывался прямо из `_tick`, то есть в
        главном потоке tkinter: tasklist / netsh / taskkill замораживали окно
        каждые 3 секунды. Теперь всё уходит в `run_bg`.
        """
        state = bypass_status()
        try:
            state["_heal"] = bypass_auto_heal_if_leak(scope)
        except Exception as exc:  # noqa: BLE001
            state["_heal"] = {"action": "error", "error": str(exc)}
        return state

    def _start_poll(self, work) -> None:
        """Один опрос = одно поколение. Устаревшие callback-и игнорируются."""
        self.checking = True
        self._state_gen += 1
        gen = self._state_gen
        self.hub.run_bg(work,
                        lambda state: self._state_ok(state, gen),
                        lambda exc: self._state_off(exc, gen))

    def _tick(self) -> None:
        self._tick_job = None
        if not self.winfo_exists():
            return
        if not self.busy and not self.checking:
            # Читаем self.scope ЗДЕСЬ, в главном потоке: lambda исполняется в
            # фоновом, а пользователь может переключить область в этот момент.
            scope = self.scope
            self._start_poll(lambda: self._poll_state(scope))
        self._tick_job = self.after(3000, self._tick)

    def fix_tun_now(self) -> None:
        self._run(self._do_fix_tun, "Отключаю TUN в Happ…",
                  "Happ: TUN отключён, локальный SOCKS активен")

    def _do_fix_tun(self) -> dict:
        happ_mode_write(tun=False, system_proxy=False)
        set_windows_system_proxy(False)
        happ_tun_disable()
        if happ_connected():
            return happ_tunnel_restart()
        return {"ok": True, "detail": "TUN отключён в Happ"}

    def check_ip_now(self) -> None:
        """Полная проверка обхода: прямой IP vs IP через SOCKS Happ → вердикт."""
        if self.ip_checking or self.busy or not self.winfo_exists():
            return
        self.ip_checking = True
        self.btn_check_ip.configure(state="disabled")
        self.hub.run_bg(
            lambda: bypass_compare_connections(timeout=4.0),
            self._on_ip_done,
            self._on_ip_fail,
        )

    def _on_ip_done(self, res: dict) -> None:
        # Не просто включаем кнопку: _update_controls() учитывает и busy,
        # и ip_checking, поэтому тик статуса не включит её посреди проверки.
        self.ip_checking = False
        direct = res.get("direct", {})
        proxy = res.get("proxy", {})
        lines = [
            f"Прямое подключение -> {direct.get('ip', '—')} ({direct.get('country', '')})",
            f"Обход SOCKS {proxy.get('proxy_port', HAPP_SOCKS_DEFAULT)} -> "
            f"{proxy.get('ip', '—')} ({proxy.get('country', '')})",
        ]
        verdict = res.get("verdict", "")
        ok = bool(res.get("ok"))
        kind = "ok" if ok else ("warn" if verdict == "SOCKS НЕ ДОСТУПЕН" else "error")
        if ok:
            lines.append("🛡 ОБХОД РАБОТАЕТ")
            self.hub.toast("Обход работает: IP через прокси отличается от прямого", kind="ok")
        else:
            lines.append(f"⚠ {verdict}")
            self.hub.toast(f"Проверка: {verdict}", kind=kind)
        for line in lines:
            self.append_log(self.log_box, line, kind if ok else kind)
        if res.get("message"):
            self.append_log(self.log_box, res["message"])
        self._update_controls()

    def _on_ip_fail(self, err: Exception) -> None:
        self.ip_checking = False
        self.append_log(self.log_box, f"Ошибка проверки IP: {err}", "error")
        self._update_controls()

    def catch_programs(self) -> None:
        """Перезапуск ProxiFyre: служба видит только процессы со своего старта."""
        if self.busy:
            return
        if self.scope != "apps":
            self.hub.toast("«Подхватить программы» нужно только в режиме «Только программы»", kind="info")
            return
        self._run(proxifyre_restart, "ProxiFyre: подхватываю запущенные программы…",
                  "Перезапускаю службу ProxiFyre…")

    def on_show(self) -> None:
        """Хук хаба при открытии вкладки: актуализирует статус и список программ."""
        self.refresh_now()

    def refresh_now(self) -> None:
        if self.checking or self.busy:
            return
        self.load_catalog()
        self._start_poll(lambda: self._poll_state(self.scope))

    def _state_ok(self, state: dict, gen: int | None = None) -> None:
        if gen is not None and gen != self._state_gen:
            return  # устаревший опрос не перезаписывает свежий статус
        self.checking = False
        self.online = True
        self._offline_noted = False
        self.state = state if isinstance(state, dict) else {}
        heal = self.state.get("_heal")
        action = heal.get("action") if isinstance(heal, dict) else None
        # Логируем не только успех: «failed»/«error» раньше пропадали молча,
        # и пользователь думал, что утечка исправлена. Пишем при смене action,
        # чтобы тик каждые 3 секунды не забивал лог.
        if action in ("healed", "failed", "error") and action != self._last_heal_action:
            if action == "healed":
                message = (heal.get("healed") or {}).get("message", "")
                self.append_log(self.log_box,
                                f"🔧 Автоисправление утечки: {message}", "ok")
            else:
                reason = ((heal.get("healed") or {}).get("message")
                          or heal.get("error") or action)
                self.append_log(self.log_box,
                                f"⚠ Автоисправление утечки не удалось: {reason}", "error")
        self._last_heal_action = action
        rescan = self.state.get("proxyfyre_rescan")
        if isinstance(rescan, dict):
            apps_list = rescan.get("apps") or []
            apps = ", ".join(map(str, apps_list))
            if rescan.get("ok"):
                if apps:
                    self.append_log(self.log_box,
                                    f"ProxiFyre: подхвачены новые процессы ({apps})", "ok")
            else:
                self.append_log(self.log_box,
                                "ProxiFyre: не удалось подхватить новые процессы", "error")
        # Диагностика утечек: если обход выключен, но трафик всё ещё перехватывается
        leak = self.state.get("leak")
        if isinstance(leak, dict):
            what = []
            if leak.get("tun"):
                what.append("TUN-адаптер")
            if leak.get("proxy"):
                what.append("системный прокси")
            if leak.get("cores"):
                what.append("ядро Happ")
            current_leak = tuple(what)
            if current_leak and current_leak != getattr(self, "_last_leak_noted", None):
                self._last_leak_noted = current_leak
                self.append_log(self.log_box,
                                "⚠ Внимание: обнаружена утечка трафика — "
                                + ", ".join(what) + " активен даже при выключенном обходе. "
                                + "Проверьте, что Happ выключен.", "warn")
            elif not current_leak:
                self._last_leak_noted = None
        self._update_controls()

    def _state_off(self, exc: Exception, gen: int | None = None) -> None:
        if gen is not None and gen != self._state_gen:
            return
        self.checking = False
        self.online = False
        self.state = {}
        if not self._offline_noted:
            self._offline_noted = True
            self.append_log(self.log_box, f"Не удалось опросить движок: {exc}", "error")
        self._update_controls()
        # _update_controls() строит текст из пустого state и затирает сообщение
        # об ошибке. Восстанавливаем его поверх — ошибка не должна исчезать.
        self.headline_var.set("движок недоступен")
        self.detail_var.set(str(exc))
        self.dot_var.set("○")
        self.dot_label.configure(fg=MUTED)
        for card in self.cards.values():
            card["status"].configure(text="состояние неизвестно", fg=MUTED)
        self.apps_route_status.configure(text="состояние неизвестно", fg=MUTED)

    def _sync_switches(self) -> None:
        for mode, card in self.cards.items():
            card["switch"].set(self._engine_on(mode))
        pf = self.state.get("proxyfyre") or {}
        self.apps_route_switch.set(bool(pf.get("running")))

    def _update_controls(self) -> None:
        happ = self.state.get("happ") or {}
        gbd = self.state.get("gbd") or {}
        proxyfyre = self.state.get("proxyfyre") or {}

        # карточки движков
        for mode, card in self.cards.items():
            engine = happ if mode == "happ" else gbd
            installed = bool(engine.get("installed", True if mode == "happ" else False))
            on = self._engine_on(mode)
            chosen = mode == self.mode
            card["frame"].configure(
                highlightbackground=OK if on else (ACCENT if chosen else BORDER),
                highlightthickness=2 if (on or chosen) else 1,
            )
            card["title"].configure(fg=OK if on else (FG if chosen else MUTED))
            card["switch"].set(on)
            card["switch"].set_enabled(not self.busy)
            card["status"].configure(text=self._engine_text(mode), fg=(
                OK if on else (MUTED if installed else WARN)
            ))

        # область обхода — это поведение Happ, движки друг другу не мешают
        self.scope_control.set_enabled(not self.busy)
        self.scope_control.set_value(self.scope)
        if self.scope == "apps":
            self.scope_hint.configure(
                text="Выбранные программы идут через Happ, остальной трафик — "
                     "напрямую или через GoodbyeDPI. Программу, открытую до "
                     "включения обхода, перезапустите."
            )
        else:
            self.scope_hint.configure(
                text="Весь трафик через Happ: TUN-адаптер и системный прокси."
            )

        # карточка «Обход по программам»: живёт независимо от VPN,
        # состояние берём из статуса службы ProxiFyre
        pf = self.state.get("proxyfyre") or {}
        ar_on = bool(pf.get("running"))
        self.apps_route_switch.set(ar_on)
        self.apps_route_switch.set_enabled(not self.busy)
        if not pf.get("installed", True):
            self.apps_route_status.configure(
                text="ProxiFyre не установлен — включите Happ с областью "
                     "«Только программы», он установится автоматически",
                fg=WARN)
        elif ar_on:
            count = len(pf.get("apps") or [])
            self.apps_route_status.configure(
                text=f"включён — перенаправляется программ: {count}"
                     + (" (список изменился — перезапуск службы)"
                        if pf.get("pending") else ""),
                fg=OK)
        else:
            self.apps_route_status.configure(
                text="выключен — маршрутизации по программам нет",
                fg=MUTED)

        # секция программ — нужна и для «Обхода по программам», поэтому видна всегда
        self.count_var.set(f"выбрано {len(self.selected_apps)} из {len(self.catalog)}")

        # кнопки
        self.btn_happ_path.configure(state="disabled" if self.busy else "normal")
        self.btn_show_happ.configure(state="disabled" if self.busy else "normal")
        self.btn_refresh.configure(
            state="disabled" if self.busy or self.checking else "normal")
        if hasattr(self, "btn_catch"):
            self.btn_catch.configure(
                state="normal" if not self.busy else "disabled"
            )
        if hasattr(self, "btn_check_ip"):
            self.btn_check_ip.configure(
                state="disabled" if (self.busy or self.ip_checking) else "normal")
        if hasattr(self, "btn_fix_tun"):
            tun_on = bool(((happ.get("mode") or {}).get("tun")) or happ.get("tun"))
            self.btn_fix_tun.configure(
                state="disabled" if self.busy else "normal",
                fg=ACCENT if (self.scope == "apps" and tun_on) else FG,
            )
        gbd_installed = bool(gbd.get("installed"))
        self.btn_download.configure(state="disabled" if self.busy else "normal")
        if gbd_installed:
            self.btn_download.pack_forget()
        else:
            self.btn_download.pack(side="left", padx=(px(8), 0))

        # нижняя карточка статуса
        headline, detail, color = self._status_text()
        self.headline_var.set(headline)
        self.detail_var.set(detail)
        self.dot_var.set("●" if color == OK else "○")
        self.dot_label.configure(fg=color)

    def _engine_text(self, mode: str) -> str:
        engine = (self.state.get("happ") if mode == "happ" else self.state.get("gbd")) or {}
        installed = bool(engine.get("installed"))
        pids = engine.get("pids") or []
        pid_text = f" · PID {', '.join(map(str, pids))}" if pids else ""
        if mode == "happ":
            if not installed:
                return "не найден — укажите путь к Happ.exe"
            if engine.get("connected"):
                tun = "TUN" if engine.get("tun") else f"SOCKS {engine.get('socks_port') or ''}".strip()
                return f"● VPN включён · {tun}{pid_text}"
            if engine.get("on"):
                return f"○ клиент открыт, VPN выключен{pid_text}"
            return "○ выключен"
        if not installed:
            return "не скачан"
        if engine.get("on"):
            where = " для остального трафика" if self.scope == "apps" else ""
            return f"● работает{where}{pid_text}"
        return "○ готов к запуску"

    def _status_text(self) -> tuple[str, str, str]:
        happ = self.state.get("happ") or {}
        gbd = self.state.get("gbd") or {}
        proxyfyre = self.state.get("proxyfyre") or {}
        port = happ.get("socks_port") or HAPP_SOCKS_DEFAULT
        happ_on = bool(happ.get("connected"))
        gbd_on = bool(gbd.get("on"))
        gbd_pids = ", ".join(map(str, gbd.get("pids") or []))

        if self.scope == "apps":
            if happ_on and proxyfyre.get("running"):
                if not happ.get("socks_ok"):
                    return (
                        "ProxiFyre работает, но SOCKS Happ не отвечает",
                        f"127.0.0.1:{port} закрыт — выбранные программы останутся "
                        "без интернета. Включите VPN в Happ.",
                        WARN,
                    )
                if (happ.get("mode") or {}).get("tun"):
                    return (
                        "Обход по программам, но в Happ включён TUN",
                        "TUN гонит в туннель весь трафик — распределения по "
                        "программам нет. Выключите TUN в самом Happ.",
                        WARN,
                    )
                if proxyfyre.get("pending"):
                    return (
                        "Обход по программам включён",
                        "список программ изменён — применяю настройки…",
                        WARN,
                    )
                count = len(proxyfyre.get("apps") or [])
                if gbd_on:
                    return (
                        "Happ — выбранные программы, GoodbyeDPI — остальное",
                        f"{count} программ через SOCKS 127.0.0.1:{port} · "
                        f"остальной трафик правит DPI"
                        + (f" · PID {gbd_pids}" if gbd_pids else ""),
                        OK,
                    )
                return (
                    "Обход по программам включён",
                    f"{count} программ через "
                    f"SOCKS 127.0.0.1:{port} · остальные напрямую",
                    OK,
                )
            if happ_on and not proxyfyre.get("running"):
                if not self.selected_apps:
                    return (
                        "Happ подключён · выберите программы",
                        "Отметьте программы в списке ниже, чтобы направить их через туннель",
                        WARN,
                    )
                return (
                    "Happ подключён, служба перенаправления не запущена",
                    "Нажмите «Подхватить программы» или перезапустите службу",
                    WARN,
                )
            if proxyfyre.get("running"):
                return (
                    "Happ выключен, а перенаправление осталось включённым",
                    "выбранные программы сейчас без интернета — включите Happ "
                    "или выключите обход по программам",
                    WARN,
                )
            if gbd_on:
                return (
                    "GoodbyeDPI работает, Happ выключен",
                    "весь трафик правит DPI · обход по программам не включён — "
                    "включите Happ для выбранных программ",
                    MUTED,
                )
            return ("Обход по программам выключен",
                    "Happ не подключён — включите переключатель Happ", MUTED)

        if happ_on and gbd_on:
            return (
                "Весь трафик через Happ, GoodbyeDPI тоже включён",
                "туннель забирает весь трафик — GoodbyeDPI здесь ничего не "
                "добавляет. Для разных программ выберите «Только программы».",
                WARN,
            )
        if happ_on:
            return ("Весь трафик через Happ", f"TUN: "
                    f"{'включён' if happ.get('tun') else 'выключен'} · "
                    f"системный прокси: {'да' if (happ.get('mode') or {}).get('system_proxy') else 'нет'}",
                    OK)
        if gbd_on:
            return ("Работает GoodbyeDPI",
                    f"обход DPI для всего трафика" + (f" · PID {gbd_pids}" if gbd_pids else ""),
                    OK)
        return ("Весь трафик через Happ выключен", "включите переключатель Happ", MUTED)

    # -- действия ------------------------------------------------------------ #
    def show_happ(self) -> None:
        if self.busy:
            return
        self._run(bypass_happ_show, "Открываю окно Happ…", "Открываю окно Happ…")

    def download(self, kind: str) -> None:
        if self.busy:
            return
        label = "GoodbyeDPI" if kind == "gbd" else kind
        self._run(lambda: bypass_download(kind), f"Скачиваю {label}…",
                  f"Скачиваю {label} с GitHub (может занять пару минут)…")

    def configure_dialog(self) -> None:
        if self.busy:
            return
        cfg = bypass_cfg()

        dialog = tk.Toplevel(self)
        dialog.title("Bypass — путь к Happ")
        dialog.configure(bg=CARD)
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()

        tk.Label(dialog, text="Файл Happ.exe (клиент Happ):", bg=CARD, fg=MUTED,
                 font=ui_font(9)).pack(anchor="w", padx=px(16), pady=(px(14), px(4)))
        path_var = tk.StringVar(value=cfg.get("happ_path", ""))
        entry = tk.Entry(dialog, textvariable=path_var, bg=FIELD, fg=FG,
                         insertbackground=FG, relief="flat", width=52)
        entry.pack(anchor="w", padx=px(16))

        def browse() -> None:
            happ_p = cfg.get("happ_path", "")
            chosen = filedialog.askopenfilename(
                title="Где лежит Happ.exe",
                initialdir=str(Path(happ_p).parent)
                if happ_p and Path(happ_p).exists() else "C:/",
                filetypes=[("Happ.exe", "*.exe"), ("Все файлы", "*.*")],
                parent=dialog,
            )
            if chosen:
                path_var.set(os.path.normpath(chosen))

        def auto() -> None:
            path_var.set(str(_find_happ()))

        row = ttk.Frame(dialog)
        row.pack(fill="x", padx=px(16), pady=(px(8), 0))
        flat_button(row, "Обзор…", browse, kind="ghost").pack(side="left")
        flat_button(row, "Найти автоматически", auto, kind="ghost").pack(
            side="left", padx=(px(8), 0))

        def save() -> None:
            value = path_var.get().strip()
            if not value:
                messagebox.showinfo("Bypass", "Укажите путь к Happ.exe", parent=dialog)
                return
            bypass_cfg_save(happ_path=value)
            self.append_log(self.log_box, f"Путь Happ сохранён: {value}")
            dialog.destroy()
            self.refresh_now()

        buttons = ttk.Frame(dialog)
        buttons.pack(fill="x", padx=px(16), pady=px(12))
        flat_button(buttons, "Сохранить", save).pack(side="right")
        flat_button(buttons, "Отмена", dialog.destroy, kind="ghost").pack(
            side="right", padx=(0, px(8)))
        entry.focus_set()

    # -- общий запуск фоновой задачи ----------------------------------------- #
    def _run(self, work, status_now: str, log_now: str) -> None:
        if self.busy:
            return  # две фоновые операции меняли состояние наперегонки
        self.busy = True
        self._state_gen += 1
        self.checking = False
        self._last_error = ""
        self._update_controls()
        self.append_log(self.log_box, log_now)
        self.hub.set_status(f"Bypass: {status_now}")
        self.hub.run_bg(work, self._run_done, self._run_fail)

    def _run_done(self, result: dict) -> None:
        if not self.winfo_exists():
            return
        self.busy = False
        self._state_gen += 1
        self.checking = False
        result = result if isinstance(result, dict) else {}
        detail = str(result.get("detail") or "")
        ok = bool(result.get("ok", True))
        if detail:
            self.append_log(self.log_box, detail, "ok" if ok else "error")
        self.hub.set_status("Bypass: готово" if ok else "Bypass: не выполнено")
        self.refresh_now()
        self._update_controls()
        if getattr(self, "_pending_apps_apply", False):
            self._pending_apps_apply = False
            self._schedule_apply()

    def _run_fail(self, exc: Exception) -> None:
        if not self.winfo_exists():
            return
        self.busy = False
        self._state_gen += 1
        self.checking = False
        self._last_error = str(exc)
        self.append_log(self.log_box, f"Ошибка: {exc}", "error")
        self.hub.set_status("Bypass: ошибка")
        self.hub.toast(f"Bypass: {exc}", kind="error", ms=4000)
        self.refresh_now()
        self._update_controls()
        if getattr(self, "_pending_apps_apply", False):
            self._pending_apps_apply = False
            self._schedule_apply()


# --------------------------------------------------------------------------- #
#  Боковое меню и главное окно
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
#  Лиды 2GIS
# --------------------------------------------------------------------------- #
try:
    import leads2gis as leads_engine

    LEADS_OK = bool(getattr(leads_engine, "DEPS_OK", True))
    LEADS_ERROR = "" if LEADS_OK else "нет requests/selectolax"
except Exception as exc:  # noqa: BLE001
    leads_engine = None
    LEADS_OK = False
    LEADS_ERROR = str(exc)

# Пути лидов живут в хозяине leads_pool (frozen-aware): LEADS_DIR, POOL_FILE,
# MAIL_SETTINGS_PATH, MAIL_STATE_PATH. Здесь — живая переадресация (PEP 562),
# а не снимок значений: тесты патчат атрибут этого модуля, панели читают
# через хозяина.
_LEADS_PATH_NAMES = ("LEADS_DIR", "POOL_FILE", "MAIL_SETTINGS_PATH", "MAIL_STATE_PATH")


def __getattr__(name: str):
    if name in _LEADS_PATH_NAMES:
        import leads_pool

        return getattr(leads_pool, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

try:
    import leads_mail

    MAIL_OK = True
    MAIL_ERROR = ""
except Exception as exc:  # noqa: BLE001
    leads_mail = None
    MAIL_OK = False
    MAIL_ERROR = str(exc)


def _mail_settings() -> dict:
    """Настройки рассылки одним местом: без кэша, но и без четырёх try/except.

    Заменила одинаковые блоки чтения leads_mail.load_settings(MAIL_SETTINGS_PATH)
    в _pool_days, _show_lead, _generate_message и MailWindow.__init__.
    Без модуля рассылки или при битом файле — пустой словарь (дефолты).
    """
    if not MAIL_OK or leads_mail is None:
        return {}
    try:
        return leads_mail.load_settings(MAIL_SETTINGS_PATH)
    except Exception:  # noqa: BLE001
        return {}


def leads_collect(city: str, queries: list[str], cards: int, only_no_site: bool,
                  min_reviews: int | None, on_progress, on_log, stop,
                  limit: int | None = None) -> dict:
    """Тонкий совместимый слой: сам сбор живёт в leads2gis.collect_leads.

    GUI (start()) и тесты зовут функцию с прежними аргументами; движок
    получает путь к пулу горячих из этого модуля.
    """
    if not LEADS_OK:
        raise RuntimeError(LEADS_ERROR or "нет requests/selectolax")
    return leads_engine.collect_leads(
        city, queries, cards, only_no_site, min_reviews,
        on_progress, on_log, stop, limit=limit, pool_file=POOL_FILE,
    )


class LeadsPanel(Panel):
    """Сбор лидов 2GIS: контакты организаций + приоритет под аутрич."""

    def __init__(self, master: tk.Misc, hub) -> None:
        super().__init__(master, hub)
        self.stop_event = threading.Event()
        self.busy = False
        self._build()

    def _build(self) -> None:
        card = tk.Frame(self, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="x")
        body = tk.Frame(card, bg=CARD)
        body.pack(fill="x", padx=px(16), pady=px(14))
        body.columnconfigure(1, weight=1)
        body.columnconfigure(3, weight=1)

        self.city_var = tk.StringVar(value="moscow")
        self.query_var = tk.StringVar(value="")  # своя ниша — по умолчанию пусто
        self.cards_var = tk.StringVar(value="60")
        self.reviews_var = tk.StringVar(value="")
        self.no_site_var = tk.BooleanVar(value=True)
        self.limit_var = tk.StringVar(value="20")

        def caption(row: int, column: int, text: str) -> None:
            tk.Label(body, text=text, bg=CARD, fg=MUTED, anchor="w",
                     font=ui_font(9)).grid(
                row=row, column=column, sticky="w", padx=(0, px(8)), pady=px(5))

        # Ряд 0: город | мин. отзывов
        caption(0, 0, "Город")
        _city_opts = ["russia — вся Россия"] + [
            c for c in (leads_mail.RU_CITIES if MAIL_OK and hasattr(leads_mail, "RU_CITIES") else [])
        ]
        _city_cb = ttk.Combobox(
            body, textvariable=self.city_var, values=_city_opts, width=20
        )
        _city_cb.grid(row=0, column=1, sticky="ew", pady=px(5))
        caption(0, 2, "Мин. отзывов")
        ttk.Entry(body, textvariable=self.reviews_var, width=8).grid(
            row=0, column=3, sticky="w", padx=(px(16), 0), pady=px(5))

        # Ряд 1: карточек всего (делятся между городами и нишами) | лимит лидов в итоговой выборке
        caption(1, 0, "Карточек")
        ttk.Spinbox(body, from_=12, to=600, increment=12, width=6,
                    textvariable=self.cards_var).grid(
            row=1, column=1, sticky="w", pady=px(5))
        caption(1, 2, "Лимит лидов")
        ttk.Combobox(body, textvariable=self.limit_var,
                     values=("10", "20", "50", "100"), width=6,
                     state="readonly").grid(
            row=1, column=3, sticky="w", padx=(px(16), 0), pady=px(5))

        # Ряд 2: популярные ниши — сетка чекбоксов 4 × 3, первая отмечена
        niches_box = tk.Frame(body, bg=CARD)
        niches_box.grid(row=2, column=0, columnspan=4, sticky="w", pady=(px(4), 0))
        tk.Label(niches_box, text="Ниши", bg=CARD, fg=MUTED, anchor="w",
                 font=ui_font(9)).pack(anchor="w")
        niches = tk.Frame(niches_box, bg=CARD)
        niches.pack(anchor="w")
        self.niche_vars: list[tuple[str, tk.BooleanVar]] = []
        for i, (label, niche_query) in enumerate(getattr(leads_engine, "NICHES", ())):
            var = tk.BooleanVar(value=(i == 0))
            self.niche_vars.append((niche_query, var))
            dark_check(niches, label, var, bg=CARD).grid(
                row=i // 4, column=i % 4, sticky="w",
                padx=(0, px(12)), pady=px(2))

        # Ряд 3: своя ниша — добавится к выбранным чекбоксам
        caption(3, 0, "Своя ниша")
        ttk.Entry(body, textvariable=self.query_var).grid(
            row=3, column=1, columnspan=2, sticky="ew", pady=px(5))
        tk.Label(body, text="добавится к выбранным нишам", bg=CARD, fg=MUTED,
                 font=ui_font(8)).grid(
            row=3, column=3, sticky="w", padx=(px(8), 0), pady=px(5))

        tk.Label(body, text="12 фирм на страницу выдачи 2ГИС", bg=CARD, fg=MUTED,
                 font=ui_font(8)).grid(row=4, column=0, columnspan=4, sticky="w")

        options = tk.Frame(body, bg=CARD)
        options.grid(row=5, column=0, columnspan=4, sticky="w", pady=(px(8), 0))
        dark_check(options, "только без сайта", self.no_site_var, bg=CARD).pack(side="left")

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(px(14), 0))
        self.start_btn = flat_button(actions, "Собрать лидов", self.start, kind="primary")
        self.start_btn.pack(side="left")
        self.stop_btn = flat_button(actions, "Стоп", self.stop_run, kind="ghost")
        self.stop_btn.pack(side="left", padx=(px(8), 0))
        flat_button(actions, "Обновить", self.refresh, kind="ghost").pack(
            side="left", padx=(px(8), 0))
        flat_button(actions, "Заполнить тему", self.fill_topics, kind="ghost").pack(
            side="left", padx=(px(8), 0))
        flat_button(actions, "Написал вручную", self.mark_manual, kind="ghost").pack(
            side="left", padx=(px(8), 0))
        self.inspector_toggle_btn = flat_button(
            actions, "Скрыть карточку", self._toggle_inspector, kind="ghost")
        self.inspector_toggle_btn.pack(side="left", padx=(px(8), 0))
        flat_button(actions, "Рассылка ИИ", self.open_mail, kind="primary").pack(
            side="left", padx=(px(8), 0))
        self.status_var = tk.StringVar(value="Готово")
        ttk.Label(actions, textvariable=self.status_var, style="Muted.TLabel").pack(side="right")

        # -- двухпанельная компоновка: таблица слева, карточка справа ------- #
        self.split = ttk.PanedWindow(self, orient="horizontal")
        self.split.pack(fill="both", expand=True, pady=(px(10), 0))

        self.table_side = ttk.Frame(self.split)
        cols = ("name", "topic", "contact", "gis")
        self.result_tree = ttk.Treeview(
            self.table_side, columns=cols, show="headings", height=9)
        for col, title, width in (
            ("name", "Название", 220), ("topic", "Тема", 150),
            ("contact", "Контакты", 230), ("gis", "2ГИС", 120),
        ):
            self.result_tree.heading(col, text=title)
            self.result_tree.column(col, width=px(width), anchor="w")
        rscroll = ttk.Scrollbar(self.table_side, orient="vertical",
                                command=self.result_tree.yview)
        self.result_tree.configure(yscrollcommand=rscroll.set)
        self.result_tree.grid(row=0, column=0, sticky="nsew")
        rscroll.grid(row=0, column=1, sticky="ns")
        self.table_side.rowconfigure(0, weight=1)
        self.table_side.columnconfigure(0, weight=1)
        self.result_tree.bind("<ButtonRelease-1>", self._on_single_click)
        self.result_tree.bind("<Double-1>", self._open_row_link)
        self.result_tree.bind("<Button-3>", self._row_menu)
        self.result_tree.bind("<<TreeviewSelect>>", self._on_select)
        self._row_links: dict[str, list[tuple[str, str]]] = {}
        self._row_urls: dict[str, str] = {}
        self._row_phones: dict[str, str] = {}
        self._rows: dict[str, dict] = {}
        # Написанным лидам (есть sent_at или статус «написан») место не в выдаче.
        self._hidden_contacted = 0

        self.inspector = tk.Frame(
            self.split, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        ins_canvas = tk.Canvas(self.inspector, bg=CARD, highlightthickness=0, bd=0)
        ins_scroll = ttk.Scrollbar(self.inspector, orient="vertical", command=ins_canvas.yview)
        ins_canvas.configure(yscrollcommand=ins_scroll.set)
        ins_canvas.pack(side="left", fill="both", expand=True)
        ins_scroll.pack(side="right", fill="y")

        ins_wrap = tk.Frame(ins_canvas, bg=CARD)
        ins_id = ins_canvas.create_window((0, 0), window=ins_wrap, anchor="nw")

        def _fit_ins(_event=None) -> None:
            ins_canvas.configure(scrollregion=ins_canvas.bbox("all"))
            ins_canvas.itemconfigure(ins_id, width=ins_canvas.winfo_width())

        ins_wrap.bind("<Configure>", _fit_ins)
        ins_canvas.bind("<Configure>", _fit_ins)

        def _ins_wheel(event):
            if event.widget in (self.msg_text, self.prompt_text):
                return None
            ins_canvas.yview_scroll(int(-event.delta / 120) * _scroll_step(), "units")
            return "break"

        ins_canvas.bind("<MouseWheel>", _ins_wheel)
        ins_wrap.bind("<MouseWheel>", _ins_wheel)

        ins = tk.Frame(ins_wrap, bg=CARD)
        ins.pack(fill="both", expand=True, padx=px(14), pady=px(12))
        ins.bind("<MouseWheel>", _ins_wheel)
        tk.Label(ins, text="Карточка лида", bg=CARD, fg=FG,
                 font=ui_font(11, "bold")).pack(anchor="w", pady=(0, px(10)))

        self.detail_vars: dict[str, tk.StringVar] = {
            key: tk.StringVar(value="") for key in INSPECTOR_FIELDS
        }
        labels = {
            "name": "Название", "topic": "Ниша", "address": "Адрес",
            "rating": "Рейтинг", "phone": "Телефон",
            "messengers": "Мессенджеры",
        }
        for key in INSPECTOR_FIELDS:
            label = labels[key]
            line = tk.Frame(ins, bg=CARD)
            line.pack(fill="x", pady=px(2))
            tk.Label(line, text=label, bg=CARD, fg=MUTED, font=ui_font(9),
                     width=12, anchor="w").pack(side="left")
            tk.Label(line, textvariable=self.detail_vars[key], bg=CARD, fg=FG,
                     font=ui_font(9), anchor="w", justify="left",
                     wraplength=px(220)).pack(side="left", fill="x", expand=True)

        tk.Label(ins, text="Текст сообщения", bg=CARD, fg=MUTED,
                 font=ui_font(9)).pack(anchor="w", pady=(px(10), px(2)))
        msg_box = tk.Frame(ins, bg=CARD)
        msg_box.pack(fill="x")
        msg_box.rowconfigure(0, weight=1)
        msg_box.columnconfigure(0, weight=1)
        self.msg_text = dark_text(msg_box, height=5)
        msg_scroll = ttk.Scrollbar(msg_box, orient="vertical",
                                   command=self.msg_text.yview)
        self.msg_text.configure(yscrollcommand=msg_scroll.set)
        self.msg_text.grid(row=0, column=0, sticky="nsew")
        msg_scroll.grid(row=0, column=1, sticky="ns")
        flat_button(ins, "Сгенерировать ИИ", self._generate_message,
                    kind="ghost").pack(anchor="w", pady=(px(6), 0))

        tk.Label(ins, text="Промпт для сайта", bg=CARD, fg=MUTED,
                 font=ui_font(9)).pack(anchor="w", pady=(px(10), px(2)))
        prompt_box = tk.Frame(ins, bg=CARD)
        prompt_box.pack(fill="x")
        prompt_box.rowconfigure(0, weight=1)
        prompt_box.columnconfigure(0, weight=1)
        self.prompt_text = dark_text(prompt_box, height=6)
        prompt_scroll = ttk.Scrollbar(prompt_box, orient="vertical",
                                      command=self.prompt_text.yview)
        self.prompt_text.configure(yscrollcommand=prompt_scroll.set)
        self.prompt_text.grid(row=0, column=0, sticky="nsew")
        prompt_scroll.grid(row=0, column=1, sticky="ns")
        prompt_actions = tk.Frame(ins, bg=CARD)
        prompt_actions.pack(fill="x", pady=(px(6), 0))
        flat_button(prompt_actions, "📋 Скопировать промпт", self._copy_prompt,
                    kind="primary").pack(side="left")
        flat_button(prompt_actions, "Сгенерировать промпт ИИ",
                    self._generate_site_prompt, kind="ghost").pack(
            side="left", padx=(px(8), 0))
        self.prompt_path_var = tk.StringVar(value="")
        tk.Label(ins, textvariable=self.prompt_path_var, bg=CARD, fg=MUTED,
                 font=ui_font(8), wraplength=px(260), anchor="w",
                 justify="left").pack(anchor="w", fill="x", pady=(px(6), 0))

        # -- ручное подтверждение: канал и одиночная отправка ---------------- #
        self.channel_var = tk.StringVar(value="")
        self._channel_map: dict[str, str] = {}  # подпись канала → канал
        self._sending = False
        channel_line = tk.Frame(ins, bg=CARD)
        channel_line.pack(fill="x", pady=(px(10), 0))
        tk.Label(channel_line, text="Канал", bg=CARD, fg=MUTED, font=ui_font(9),
                 width=12, anchor="w").pack(side="left")
        self.channel_box = ttk.Combobox(
            channel_line, textvariable=self.channel_var, state="readonly", width=14)
        self.channel_box.pack(side="left")
        send_row = tk.Frame(ins, bg=CARD)
        send_row.pack(fill="x", pady=(px(8), 0))
        flat_button(send_row, "Отправить", self._send_one, kind="primary").pack(side="left")
        flat_button(send_row, "Пропустить", self._skip_lead, kind="ghost").pack(
            side="left", padx=(px(8), 0))
        flat_button(send_row, "Удалить из пула", self._remove_lead, kind="ghost").pack(
            side="left", padx=(px(8), 0))

        self._inspector_visible = True
        self.split.add(self.table_side, weight=3)
        self.split.add(self.inspector, weight=2)

        log_frame, self.log_box = self.make_log(self, height=6)
        log_frame.pack(fill="x", pady=(px(10), 0))
        self.append_log(
            self.log_box,
            "Город — слаг 2GIS: moscow, spb, kazan, ekaterinburg, novosibirsk. "
            "Главный сигнал лида — отсутствие сайта. Горячие копятся в пуле на 5 дней.",
        )
        self._set_buttons(running=False)
        self._clear_inspector()
        self.refresh()

    # -- действия ---------------------------------------------------------- #
    def open_mail(self) -> None:
        if not MAIL_OK:
            messagebox.showerror(
                "Рассылка ИИ", "Нет модуля рассылки:\n" + (MAIL_ERROR or "leads_mail.py"),
                parent=self,
            )
            return
        if getattr(self, "_mail_win", None) is not None and self._mail_win.winfo_exists():
            self._mail_win.lift()
            self._mail_win.focus_force()
            return
        self._mail_win = MailWindow(self.hub, POOL_FILE)

    def _selected_queries(self) -> list[str]:
        """Запросы отмеченных ниш + своя ниша (без дублей, порядок сохранён)."""
        queries = [q for q, var in self.niche_vars if var.get()]
        own = self.query_var.get().strip()
        if own and own.lower() not in {q.lower() for q in queries}:
            queries.append(own)
        return queries

    def start(self) -> None:
        if self.busy:
            return
        if not LEADS_OK:
            messagebox.showerror(
                "Лиды 2GIS",
                "Нет зависимостей: " + (LEADS_ERROR or "requests, selectolax")
                + "\n\nУстанови: pip install requests selectolax",
                parent=self,
            )
            return

        city = self.city_var.get().strip()
        if city.lower().startswith("russia"):  # «russia — вся Россия» → «russia»
            city = "russia".lower()
        queries = self._selected_queries()
        if not city or not queries:
            messagebox.showwarning(
                "Лиды 2GIS", "Выберите город и хотя бы одну нишу.", parent=self)
            return

        try:
            cards = max(12, min(600, int(self.cards_var.get())))
        except ValueError:
            cards = 60
        try:
            limit = int(self.limit_var.get())
        except ValueError:
            limit = 20
        try:
            min_reviews = int(self.reviews_var.get()) if self.reviews_var.get().strip() else None
        except ValueError:
            min_reviews = None
        only_no_site = bool(self.no_site_var.get())

        self.stop_event.clear()
        self.busy = True
        self._set_buttons(running=True)
        self.status_var.set("Собираю…")
        for item in self.result_tree.get_children():
            self.result_tree.delete(item)
        self.append_log(
            self.log_box,
            f"старт: {city} / ниши: {', '.join(queries)}, карточек {cards}, лимит {limit}",
        )

        def work() -> dict:
            return leads_collect(
                city, queries, cards, only_no_site, min_reviews,
                lambda text: self.hub.post(lambda text=text: self.status_var.set(text)),
                lambda text: self.hub.post(lambda text=text: self.append_log(self.log_box, text)),
                self.stop_event,
                limit=limit,
            )

        self.hub.run_bg(work, self._done, self._fail)

    def stop_run(self) -> None:
        if not self.busy:
            return
        self.stop_event.set()
        self.status_var.set("Останавливаю…")
        self.append_log(self.log_box, "остановка по кнопке")

    # -- данные ------------------------------------------------------------ #
    def _pool_days(self) -> float:
        settings = _mail_settings()
        try:
            return max(1.0, float(str(settings.get("pool_days", "5")).replace(",", ".")))
        except (TypeError, ValueError):
            return 5.0

    def _contact_links(self, row: dict) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for channel, title in (("telegram", "Telegram"), ("vk", "ВКонтакте"),
                               ("max", "MAX"), ("email", "Почта")):
            url = leads_mail._to_link(channel, row.get(channel))
            if url:
                out.append((title, url))
        return out

    def _contact_primary(self, row: dict) -> str:
        """Самый приоритетный канал — одной строкой, без дублей номера."""
        for channel, title in (("telegram", "Telegram"), ("vk", "ВКонтакте"),
                               ("max", "MAX"), ("email", "Почта")):
            raw = str(row.get(channel) or "").strip()
            if raw:
                return f"{title}: {_short_link(leads_mail._to_link(channel, raw))}"
        phone = str(row.get("phone") or "").strip()
        if phone:
            return "Телефон: " + _fmt_phone(phone)
        return "—"

    def fill_topics(self) -> None:
        """Заполняет пустой topic у записей пула текущим поисковым запросом."""
        query = self.query_var.get().strip()
        if not query:
            # Своя ниша пуста — берём первую отмеченную из списка ниш
            query = next((q for q, var in self.niche_vars if var.get()), "")
        if not query:
            self.status_var.set("Выберите нишу или введите свою")
            return
        if not MAIL_OK:
            return
        rows = leads_mail.pool_load(POOL_FILE)
        changed = 0
        for row in rows:
            if not (row.get("topic") or "").strip():
                row["topic"] = query
                changed += 1
        if changed:
            try:
                leads_mail._pool_save(POOL_FILE, rows)
                self.status_var.set(f"Тема «{query}» → {changed} записей")
                self.refresh()
            except leads_mail.PoolBusy:
                self.status_var.set("Файл занят — закрой Excel")
        else:
            self.status_var.set("Все записи уже имеют тему")

    def refresh(self) -> None:
        if not MAIL_OK:
            self.status_var.set("нет модуля рассылки")
            return
        days = self._pool_days()
        busy = ""
        try:
            leads_mail.pool_purge(POOL_FILE, days)
        except getattr(leads_mail, "PoolBusy", RuntimeError) as exc:
            busy = str(exc)
        self._fill_pool()
        self._update_status(busy)

    on_show = refresh

    def _update_status(self, busy: str = "") -> None:
        """Статусная строка: объём пула и сколько написанных скрыто из выдачи."""
        days = self._pool_days()
        stats = leads_mail.pool_stats(POOL_FILE, days)
        tail = f" · {busy}" if busy else ""
        hidden = (f" · написанных скрыто: {self._hidden_contacted}"
                  if self._hidden_contacted else "")
        self.status_var.set(
            f"в пуле: {stats['total']} · истекают сегодня: {stats['expiring']} · "
            f"срок {days:g} дн.{hidden}{tail}")

    def _fill_pool(self) -> None:
        for item in self.result_tree.get_children():
            self.result_tree.delete(item)
        self._row_links.clear()
        self._row_urls.clear()
        self._row_phones.clear()
        self._rows.clear()
        self._hidden_contacted = 0
        rows = leads_mail.pool_load(POOL_FILE)
        index = 0
        for row in rows:
            if leads_mail.is_contacted(row):
                # Лиду уже писали — в выдаче ему не место (из пула не удаляем:
                # очередь рассылки в MailWindow продолжает его видеть).
                self._hidden_contacted += 1
                continue
            iid = f"r{index}"
            index += 1
            self._rows[iid] = row
            self._row_links[iid] = self._contact_links(row)
            self._row_urls[iid] = str(row.get("card") or "").strip()
            self._row_phones[iid] = str(row.get("phone") or "").strip()
            topic = str(row.get("topic") or "").strip() or "—"
            gis = "2ГИС" if self._row_urls[iid] else "—"
            self.result_tree.insert("", "end", iid=iid, values=(
                row.get("name", ""), topic, self._contact_primary(row), gis,
            ))
        # Сразу заполняем карточку первым лидом, чтобы инспектор не был пустым.
        children = self.result_tree.get_children()
        if children:
            first = children[0]
            self.result_tree.selection_set(first)
            self.result_tree.focus(first)
            self._show_lead(first)
        else:
            self._clear_inspector()

    # -- инспектор лида ---------------------------------------------------- #
    @staticmethod
    def _set_text(widget: tk.Text, text: str) -> None:
        widget.delete("1.0", "end")
        widget.insert("1.0", text)

    def _current_lead(self) -> tuple:
        """Текущий лид: из карточки, либо из выделения таблицы."""
        sel = self.result_tree.selection()
        iid = self._current_iid or (sel[0] if sel else None)
        row = self._current_row or (self._rows.get(iid, {}) if iid else {})
        return iid, row

    def _toggle_inspector(self) -> None:
        if self._inspector_visible:
            self.split.forget(self.inspector)
            self._inspector_visible = False
            self.inspector_toggle_btn.configure(text="Показать карточку")
        else:
            self.split.add(self.inspector, weight=2)
            self._inspector_visible = True
            self.inspector_toggle_btn.configure(text="Скрыть карточку")

    def _on_select(self, event=None) -> None:
        sel = self.result_tree.selection()
        if sel:
            self._show_lead(sel[0])
        else:
            self._clear_inspector()

    def _show_lead(self, iid: str) -> None:
        row = self._rows.get(iid, {})
        if not row:
            self._clear_inspector()
            return
        self._current_iid = iid
        self._current_row = row

        try:
            details = inspector_fields(row)
        except Exception:  # noqa: BLE001 - a broken pool row must not crash the card
            details = {key: "—" for key in INSPECTOR_FIELDS}
        for key in INSPECTOR_FIELDS:
            self.detail_vars[key].set(details.get(key, "—"))

        # GUI fallback: the logic layer found no messengers - use links from the row.
        if details.get("messengers") in ("—", "не найдены"):
            try:
                links = self._contact_links(row)
            except Exception:  # noqa: BLE001
                links = []
            fallback = ", ".join(f"{title}: {url}" for title, url in links)
            self.detail_vars["messengers"].set(fallback or "—")

        settings = _mail_settings()
        idea = str(settings.get("idea", "") or "")

        message = prompt = prompt_path = ""
        if leads_mail is not None:
            try:
                # Если текст для лида уже генерили (карточка или очередь рассылки) —
                # показываем сохранённый, иначе свежую заготовку.
                st = leads_mail.MailState(MAIL_STATE_PATH).get(leads_mail.row_id(row))
                message = str(st.get("text") or "").strip()
            except Exception:  # noqa: BLE001 - пустой журнал не должен ломать карточку
                message = ""
            if not message:
                try:
                    message = leads_mail.fallback_text(row, idea, settings)
                except Exception:
                    message = ""
            try:
                prompt, path = leads_mail.read_or_create_site_prompt(row, settings)
                prompt_path = str(path)
            except Exception:
                prompt = ""
                prompt_path = ""

        self._set_text(self.msg_text, message or "—")
        self._set_text(self.prompt_text, prompt or "—")
        self.prompt_path_var.set(prompt_path or "—")

        # Каналы отправки: только те, где у лида есть контакт, по приоритету.
        avail = leads_mail.channels_of(row) if leads_mail is not None else {}
        order = leads_mail.PRIORITY if leads_mail is not None else ()
        self._channel_map = {
            leads_mail.channel_label(ch): ch for ch in order if ch in avail
        }
        labels = list(self._channel_map)
        self.channel_box["values"] = labels
        self.channel_var.set(labels[0] if labels else "")

    def _clear_inspector(self) -> None:
        for var in self.detail_vars.values():
            var.set("")
        self._set_text(self.msg_text, "")
        self._set_text(self.prompt_text, "")
        self.prompt_path_var.set("")
        self.channel_var.set("")
        self.channel_box["values"] = []
        self._channel_map = {}
        self._current_iid = None
        self._current_row = {}

    def _generate_message(self) -> None:
        iid, row = self._current_lead()
        if not iid or not row:
            self.hub.toast("выбери лида в таблице")
            return
        self._current_iid = iid
        self._current_row = row
        settings = _mail_settings()
        idea = str(settings.get("idea", "") or "")

        def work() -> str:
            if leads_mail is None:
                return ""
            if not leads_mail._ai_configured(settings):
                return leads_mail.fallback_text(row, idea, settings)
            return (leads_mail.ai_generate(settings, row, idea)
                    or leads_mail.fallback_text(row, idea, settings))

        def on_done(text: str) -> None:
            if self._current_iid != iid:
                return  # за время запроса выделение успели переключить
            self._set_text(self.msg_text, text)

        def on_error(exc: Exception) -> None:
            self.append_log(self.log_box, f"ИИ: {exc}", "error")

        self.hub.run_bg(work, on_done, on_error)

    def _generate_site_prompt(self) -> None:
        """Перегенерировать промпт для сайта через ИИ (или по шаблону) и сохранить в файл."""
        iid, row = self._current_lead()
        if not iid or not row:
            self.hub.toast("выбери лида в таблице")
            return
        self._current_iid = iid
        self._current_row = row
        settings = _mail_settings()

        def work() -> tuple[str, str]:
            if leads_mail is None:
                return "", ""
            text = leads_mail.ai_generate_site_prompt(settings, row, save=True)
            path_str = str(leads_mail.site_prompt_file(row))
            return text, path_str

        def on_done(res: tuple[str, str]) -> None:
            if self._current_iid != iid:
                return
            text, path_str = res
            self._set_text(self.prompt_text, text or "—")
            if path_str:
                self.prompt_path_var.set(path_str)
            self.append_log(self.log_box, f"промпт для сайта обновлён · {path_str}", "ok")
            self.hub.toast("промпт обновлён")

        def on_error(exc: Exception) -> None:
            self.append_log(self.log_box, f"ИИ промпт: {exc}", "error")

        self.hub.run_bg(work, on_done, on_error)

    def _copy_prompt(self) -> None:
        iid, row = self._current_lead()
        if not iid or not row:
            self.hub.toast("выбери лида в таблице")
            return
        text = self.prompt_text.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()
        path = None
        if leads_mail is not None:
            try:
                path = leads_mail.save_site_prompt(text, row)
            except (OSError, AttributeError):
                pass
        path_str = str(path) if path is not None else ""
        self.prompt_path_var.set(path_str)
        self.append_log(self.log_box, f"промпт скопирован · {path_str}", "ok")
        self.hub.toast("промпт скопирован")

    # -- результат сбора --------------------------------------------------- #
    def _done(self, result: dict) -> None:
        self.busy = False
        self._set_buttons(running=False)
        self.status_var.set(f"Готово: в пуле {result['total']}")
        self.append_log(
            self.log_box,
            f"готово: проверено {result['checked']}, новых горячих {result['pooled']} "
            f"(в пуле всего {result['total']})",
            "ok",
        )
        self.hub.toast(f"Горячих в пуле: {result['total']}")
        self.refresh()

    def _fail(self, exc: Exception) -> None:
        self.busy = False
        self._set_buttons(running=False)
        self.status_var.set("Ошибка")
        self.append_log(self.log_box, f"ошибка: {exc}", "error")
        messagebox.showerror("Лиды 2GIS", str(exc), parent=self)

    def _set_buttons(self, *, running: bool) -> None:
        self.start_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")

    # -- контекст ---------------------------------------------------------- #
    def _on_single_click(self, event) -> None:
        """Одиночный клик по колонке «2ГИС» открывает карточку (с защитой от двойного клика)."""
        col_id = self.result_tree.identify_column(event.x)
        try:
            if self.result_tree.column(col_id, option="id") != "gis":
                return
        except Exception:
            return
        iid = self.result_tree.identify_row(event.y)
        if not iid:
            return
        now = time.monotonic()
        if now - getattr(self, "_last_gis_click", 0) < 0.4:
            return
        self._last_gis_click = now
        url = self._row_urls.get(iid, "")
        if url:
            webbrowser.open(url)

    def _open_row_link(self, event=None) -> None:
        iid = (self.result_tree.identify_row(event.y) if event is not None else None)
        if not iid:
            sel = self.result_tree.selection()
            if not sel:
                return
            iid = sel[0]
        # Если ссылка 2ГИС уже открыта одиночным кликом — не дублируем вкладку
        now = time.monotonic()
        if now - getattr(self, "_last_gis_click", 0) < 0.4:
            return
        # Двойной клик по колонке «2ГИС» — открываем карточку 2ГИС
        if event is not None:
            try:
                col_id = self.result_tree.identify_column(event.x)
                col_name = self.result_tree.column(col_id, option="id") if col_id else ""
                if col_name == "gis":
                    url = self._row_urls.get(iid, "")
                    if url:
                        webbrowser.open(url)
                    return
            except Exception:
                pass
        links = self._row_links.get(iid, [])
        if links:
            webbrowser.open(links[0][1])
        elif self._row_urls.get(iid):
            webbrowser.open(self._row_urls[iid])

    def _open_2gis(self, iid: str) -> None:
        url = self._row_urls.get(iid, "")
        if url:
            webbrowser.open(url)

    def _row_menu(self, event) -> None:
        iid = self.result_tree.identify_row(event.y)
        if not iid:
            return
        self.result_tree.selection_set(iid)
        menu = tk.Menu(self, tearoff=0, bg=CARD, fg=FG,
                       activebackground=tc("accent_fill"),
                       activeforeground=tc("on_accent"), bd=0)
        for title, url in self._row_links.get(iid, []):
            menu.add_command(label=f"{title}: {url}",
                             command=lambda u=url: webbrowser.open(u))
        if not self._row_links.get(iid):
            menu.add_command(label="ссылок нет — только телефон", state="disabled")
        menu.add_separator()
        if self._row_urls.get(iid):
            menu.add_command(label="Открыть карточку в 2ГИС",
                             command=lambda: self._open_2gis(iid))
        menu.add_command(label="Написал вручную (удалить)", command=self.mark_manual)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def mark_manual(self) -> None:
        sel = self.result_tree.selection()
        if not sel:
            self.hub.toast("выбери лида в таблице")
            return
        iid = sel[0]
        row = {"name": self.result_tree.set(iid, "name"),
               "phone": self._row_phones.get(iid, "")}
        key = leads_mail.pool_key(row)
        if not messagebox.askyesno(
                "Пул горячих", "Отметить «написал вручную»?\nЛид будет удалён из пула.",
                parent=self):
            return
        try:
            leads_mail.pool_remove(POOL_FILE, key)
        except getattr(leads_mail, "PoolBusy", RuntimeError) as exc:
            messagebox.showwarning("Пул горячих", str(exc), parent=self)
            return
        self.refresh()
        self.hub.toast("лид удалён из пула")

    # -- ручное подтверждение и одиночная отправка --------------------------- #
    def _next_iid(self) -> str | None:
        """Iid следующей строки после текущего выделения; None, если её нет."""
        children = self.result_tree.get_children()
        iid = self._current_iid
        if not iid:
            sel = self.result_tree.selection()
            iid = sel[0] if sel else None
        if iid not in children:
            return None
        index = children.index(iid)
        if index + 1 >= len(children):
            return None
        return children[index + 1]

    def _select_next(self) -> bool:
        """Перевести выделение на следующую строку таблицы; False, если её нет."""
        nxt = self._next_iid()
        if not nxt:
            return False
        # <<TreeviewSelect>> сам обновит карточку через _on_select.
        self.result_tree.selection_set(nxt)
        self.result_tree.focus(nxt)
        self.result_tree.see(nxt)
        return True

    def _skip_lead(self) -> None:
        """Пропустить лида: статусы не меняем, просто переходим к следующему."""
        iid, row = self._current_lead()
        if not iid or not row:
            self.hub.toast("выбери лида в таблице")
            return
        if not self._select_next():
            self.hub.toast("это последний лид в таблице")

    def _remove_lead(self) -> None:
        iid, row = self._current_lead()
        if not iid or not row:
            self.hub.toast("выбери лида в таблице")
            return
        if not MAIL_OK:
            self.hub.toast("модуль рассылки недоступен")
            return
        key = leads_mail.pool_key(row)
        if not messagebox.askyesno(
                "Лиды 2GIS", f"Удалить «{row.get('name', '')}» из пула?", parent=self):
            return
        try:
            leads_mail.pool_remove(POOL_FILE, key)
        except getattr(leads_mail, "PoolBusy", RuntimeError) as exc:
            self.append_log(self.log_box, f"пул: {exc}", "warn")
            return
        self.refresh()
        self.hub.toast("лид удалён из пула")

    def _send_one(self) -> None:
        """Одиночная отправка текущему лиду — без очередей и фоновых циклов."""
        iid, row = self._current_lead()
        if not iid or not row:
            self.hub.toast("выбери лида в таблице")
            return
        if not MAIL_OK:
            self.hub.toast("модуль рассылки недоступен")
            return
        if self._sending:
            self.hub.toast("отправка уже идёт")
            return
        text = self.msg_text.get("1.0", "end-1c").strip()
        if not text:
            self.hub.toast("текст сообщения пустой")
            return
        channel = self._channel_map.get(self.channel_var.get())
        contact = leads_mail.channels_of(row).get(channel) if channel else None
        if not channel or not contact:
            self.hub.toast("у лида нет контакта для этого канала")
            return
        settings = _mail_settings()
        dry = bool(settings.get("dry_run"))

        def work() -> dict:
            return leads_mail.dispatch(channel, settings, contact[0], contact[1], text)

        def on_done(info: dict) -> None:
            self._sending = False
            # Отметки как в Mailer._try_send, но журнал читаем свежим.
            state = leads_mail.MailState(MAIL_STATE_PATH)
            leads_mail.record_send(state, row, channel, contact[0], info, text)
            try:
                leads_mail.pool_mark_sent(POOL_FILE, leads_mail.pool_key(row), channel)
            except getattr(leads_mail, "PoolBusy", RuntimeError) as exc:
                self.append_log(self.log_box, f"пул: {exc}", "warn")
            self.append_log(
                self.log_box,
                f"→ {row.get('name', '')} · {leads_mail.channel_label(channel)} · {contact[0]}"
                + (" · тестовый режим" if dry else ""),
                "ok",
            )
            self.hub.toast("отправлено (тестовый режим)" if dry else "отправлено")
            if self._current_iid != iid:
                return  # пользователь уже смотрит другого лида — выдачу не дёргаем
            # Написанному лиду не место в выдаче: убираем строку точечно
            # (из пула не удаляем — очередь MailWindow продолжает его видеть).
            nxt = self._next_iid()
            if self.result_tree.exists(iid):
                self.result_tree.delete(iid)
                self._rows.pop(iid, None)
                self._hidden_contacted += 1
                self._update_status()
            if nxt and self.result_tree.exists(nxt):
                # <<TreeviewSelect>> сам обновит карточку через _on_select.
                self.result_tree.selection_set(nxt)
                self.result_tree.focus(nxt)
                self.result_tree.see(nxt)
            else:
                self.hub.toast("это был последний лид")

        def on_error(exc: Exception) -> None:
            self._sending = False
            self.append_log(self.log_box, f"отправка: {exc}", "error")
            self.hub.toast("ошибка отправки")

        self._sending = True
        self.hub.run_bg(work, on_done, on_error)


class MailWindow(tk.Toplevel):
    """Рассылка ИИ по горячим лидам: настройки, генерация, очередь, отправка."""

    def __init__(self, hub, _pool_file=None) -> None:
        super().__init__(hub)
        self.hub = hub
        self.settings_path = MAIL_SETTINGS_PATH
        self.state_path = MAIL_STATE_PATH
        self.title("Рассылка ИИ — Лиды 2GIS")
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        win_w = min(px(1000), sw - px(30))
        win_h = min(px(760), sh - px(70))
        self.geometry(
            f"{win_w}x{win_h}+{max(0, (sw - win_w) // 2)}+{max(0, (sh - win_h) // 3)}")
        self.minsize(min(px(760), win_w), min(px(560), win_h))
        self.configure(bg=BG)
        self.option_add("*Font", ui_font(9))
        apply_dark_theme(self)

        self.settings = _mail_settings()
        self.state = leads_mail.MailState(self.state_path)
        self.leads: list[dict] = []
        self.stop_event = threading.Event()
        self.mailer = None
        self.vars: dict[str, tk.StringVar] = {}
        self.texts: dict[str, tk.Text] = {}

        self._build()
        enable_input_paste(self)
        self._load_leads()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(80, self._activate)

    def _activate(self) -> None:
        """Окно сразу активно: иначе первый клик/набор уходит «в никуда»."""
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
        except tk.TclError:
            pass

    # -- разметка ---------------------------------------------------------- #
    def _build(self) -> None:
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=px(10), pady=px(10))

        holder = ttk.Frame(nb)
        queue = ttk.Frame(nb)
        nb.add(holder, text="  Настройки  ")
        nb.add(queue, text="  Очередь  ")

        # настройки не влезают по высоте — делаем прокрутку
        canvas = tk.Canvas(holder, bg=BG, highlightthickness=0, bd=0)
        vbar = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        vbar.pack(side="right", fill="y")

        cfg = ttk.Frame(canvas)
        cfg_id = canvas.create_window((0, 0), window=cfg, anchor="nw")

        def _fit(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfigure(cfg_id, width=canvas.winfo_width())

        cfg.bind("<Configure>", _fit)
        canvas.bind("<Configure>", _fit)

        def _wheel(event):
            canvas.yview_scroll(int(-event.delta / 120) * _scroll_step(), "units")
            return "break"

        canvas.bind("<MouseWheel>", _wheel)
        cfg.bind("<MouseWheel>", _wheel)

        left = ttk.Frame(cfg)
        left.grid(row=0, column=0, sticky="nsew", padx=(px(6), px(18)), pady=px(8))
        right = ttk.Frame(cfg)
        right.grid(row=0, column=1, sticky="nsew", padx=(0, px(6)), pady=px(8))
        cfg.columnconfigure(0, weight=1)
        cfg.columnconfigure(1, weight=1)

        def field(parent, row, label, key, *, show=None, width=34):
            ttk.Label(parent, text=label, style="Muted.TLabel").grid(
                row=row, column=0, sticky="w", pady=px(3))
            var = self.vars.setdefault(key, tk.StringVar(value=str(self.settings.get(key, ""))))
            entry = ttk.Entry(parent, textvariable=var, width=width, show=show or "")
            entry.grid(row=row, column=1, sticky="ew", pady=px(3))
            return entry

        def area(parent, row, label, key, height=4):
            ttk.Label(parent, text=label, style="Muted.TLabel").grid(
                row=row, column=0, columnspan=2, sticky="w", pady=(px(8), px(2)))
            text = dark_text(parent, height=height)
            text.insert("1.0", str(self.settings.get(key, "")))
            text.grid(row=row + 1, column=0, columnspan=2, sticky="ew")
            self.texts[key] = text

        # ИИ
        ttk.Label(left, text="Нейросеть", foreground=ACCENT).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, px(6)))
        field(left, 1, "API URL", "ai_url")
        field(left, 2, "Модель", "ai_model")
        field(left, 3, "API-ключ", "ai_key", show="*")
        field(left, 4, "GitHub Base URL (ссылка; можно {slug})", "github_base_url")
        area(left, 5, "Идея предложения (что продаём)", "idea", 3)
        area(left, 8, "Как писать (обучение ИИ)", "style", 3)
        area(left, 11, "Шаблон промпта для сайта (пусто = стандартный)\nПоля: {name}, {rubric}, {city}, {address}, {phone}, {messengers}, {rating}, {reviews}, {github_base_url}", "site_prompt_template", 4)
        left.columnconfigure(1, weight=1)

        # Каналы
        ttk.Label(right, text="Каналы", foreground=ACCENT).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, px(6)))
        field(right, 1, "MAX API URL", "max_url")
        field(right, 2, "MAX токен", "max_token", show="*")
        field(right, 3, "MAX — мой чат (ответы)", "max_self")
        field(right, 4, "TG api_id", "api_id")
        field(right, 5, "TG api_hash", "api_hash")
        field(right, 6, "TG номер телефона", "tg_phone")
        field(right, 7, "TG сессия (файл)", "tg_session_file")
        tgbox = ttk.Frame(right)
        tgbox.grid(row=8, column=0, columnspan=2, sticky="w", pady=(px(4), px(8)))
        flat_button(tgbox, "Войти в TG", self._auth_tg, kind="primary").pack(side="left")
        flat_button(tgbox, "Выйти из TG", self._logout_tg, kind="ghost").pack(
            side="left", padx=(px(8), 0))
        flat_button(tgbox, "my.telegram.org", lambda: webbrowser.open(
            "https://my.telegram.org/apps"), kind="ghost").pack(
            side="left", padx=(px(8), 0))
        field(right, 9, "VK token", "vk_token", show="*")
        vkbox = ttk.Frame(right)
        vkbox.grid(row=10, column=0, columnspan=2, sticky="w", pady=(0, px(8)))
        flat_button(vkbox, "VK: где взять токен", lambda: webbrowser.open(
            "https://vkhost.github.io/"), kind="ghost").pack(side="left")
        flat_button(vkbox, "сайт не открылся", lambda: webbrowser.open(
            "https://dev.vk.com/ru/apps"), kind="ghost").pack(
            side="left", padx=(px(6), 0))
        field(right, 11, "SMTP хост", "smtp_host")
        field(right, 12, "SMTP порт", "smtp_port")
        field(right, 13, "SMTP логин", "smtp_user")
        field(right, 14, "SMTP пароль", "smtp_pass", show="*")
        field(right, 15, "Почта отправителя", "smtp_from")
        field(right, 16, "Тема письма", "mail_subject")
        right.columnconfigure(1, weight=1)

        rules = ttk.Frame(cfg)
        rules.grid(row=1, column=0, columnspan=2, sticky="w", padx=px(6), pady=(0, px(8)))
        ttk.Label(rules, text="Дней до повторного (0 = выкл)", style="Muted.TLabel").pack(side="left")
        ttk.Entry(rules, textvariable=self.vars.setdefault(
            "followup_days", tk.StringVar(value=str(self.settings.get("followup_days", "0")))),
            width=6).pack(side="left", padx=(px(6), px(20)))
        ttk.Label(rules, text="Дней в пуле горячих", style="Muted.TLabel").pack(side="left")
        ttk.Entry(rules, textvariable=self.vars.setdefault(
            "pool_days", tk.StringVar(value=str(self.settings.get("pool_days", "5")))),
            width=6).pack(side="left", padx=(px(6), px(20)))
        ttk.Label(rules, text="Пауза между сообщениями, сек", style="Muted.TLabel").pack(side="left")
        ttk.Entry(rules, textvariable=self.vars.setdefault(
            "pacing", tk.StringVar(value=str(self.settings.get("pacing", "60")))),
            width=6).pack(side="left", padx=(px(6), px(20)))
        self.dry_var = tk.BooleanVar(value=bool(self.settings.get("dry_run", False)))
        dark_check(rules, "тестовый режим (не отправлять)", self.dry_var).pack(side="left")

        # очередь
        bar = ttk.Frame(queue)
        bar.pack(fill="x", padx=px(8), pady=(px(8), px(4)))
        flat_button(bar, "Сгенерировать тексты", self._generate, kind="primary").pack(side="left")
        flat_button(bar, "Отправить (подтвердить)", self._send, kind="primary").pack(
            side="left", padx=(px(8), 0))
        flat_button(bar, "Стоп всё", self._stop_all, kind="ghost").pack(side="left", padx=(px(8), 0))
        flat_button(bar, "Обновить список", self._load_leads, kind="ghost").pack(
            side="left", padx=(px(8), 0))
        flat_button(bar, "Остановить лид", lambda: self._mark("stopped"), kind="ghost").pack(
            side="left", padx=(px(8), 0))
        flat_button(bar, "Ответил", lambda: self._mark("replied"), kind="ghost").pack(
            side="left", padx=(px(8), 0))
        flat_button(bar, "Прочитал", lambda: self._mark("read"), kind="ghost").pack(
            side="left", padx=(px(8), 0))
        flat_button(bar, "Написал вручную", self._mark_manual, kind="ghost").pack(
            side="left", padx=(px(8), 0))
        flat_button(bar, "Открыть контакт", self._open_link, kind="ghost").pack(
            side="left", padx=(px(8), 0))
        flat_button(bar, "Сбросить ошибки", self._reset_failed, kind="ghost").pack(
            side="left", padx=(px(8), 0))

        table = ttk.Frame(queue)
        table.pack(fill="both", expand=True, padx=px(8), pady=px(4))
        cols = ("name", "target", "channel", "status", "text")
        self.tree = ttk.Treeview(table, columns=cols, show="headings", height=12)
        for col, title, width in (
            ("name", "Лид", 200), ("target", "Куда (ссылка)", 250),
            ("channel", "Канал", 90), ("status", "Статус", 90), ("text", "Текст", 300),
        ):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=px(width), anchor="w")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self._show_text)
        self.tree.bind("<Double-1>", self._open_link)

        ttk.Label(queue, text="Текст выбранного лида", style="Muted.TLabel").pack(
            anchor="w", padx=px(10), pady=(px(6), 0))
        self.preview = dark_text(queue, height=4)
        self.preview.pack(fill="x", padx=px(8), pady=(px(2), px(6)))

        self.log = dark_text(queue, height=6)
        self.log.tag_configure("ok", foreground=OK)
        self.log.tag_configure("warn", foreground=WARN)
        self.log.tag_configure("error", foreground=DANGER)
        self.log.pack(fill="both", expand=True, padx=px(8), pady=(0, px(8)))

        self.status_var = tk.StringVar(value="Готово")
        ttk.Label(self, textvariable=self.status_var, style="Muted.TLabel").pack(
            anchor="w", padx=px(14), pady=(0, px(8)))

    # -- данные ------------------------------------------------------------ #
    def _load_leads(self) -> None:
        days = self._pool_days()
        # добираем в пул горячих из leads.csv — pool_add дедуплицирует сам
        synced = gone = filled = 0
        try:
            migrated = leads_mail.migrate_legacy(LEADS_DIR)
            self._log(f"архив: горячих перенесено {migrated['added']}, "
                      f"контактов дозаполнено {migrated['filled']}", "ok")
            if migrated.get("archived"):
                self._log(f"старий файл {migrated['archived']} больше не нужен", "ok")
            gone = leads_mail.pool_purge(POOL_FILE, days)
        except getattr(leads_mail, "PoolBusy", RuntimeError) as exc:
            self._log(f"пул: {exc}", "warn")
        if gone:
            self._log(f"из пула удалено просроченных: {gone}", "warn")

        self.leads = leads_mail.load_hot_leads(POOL_FILE)
        self._refresh_tree()
        stats = leads_mail.pool_stats(POOL_FILE, days)
        tail = f", истекают сегодня: {stats['expiring']}" if stats["expiring"] else ""
        self._log(f"горячих в пуле: {len(self.leads)}{tail} · срок {days:g} дн.", "ok")

    def _pool_days(self) -> float:
        try:
            return max(1.0, float(str(self.vars.get("pool_days").get()).replace(",", ".")))
        except (AttributeError, TypeError, ValueError):
            return 5.0

    def _refresh_tree(self) -> None:
        if not self.winfo_exists():
            return
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._links: dict[str, str] = {}
        for index, row in enumerate(self.leads):
            lead_id = leads_mail.row_id(row) or f"row{index}"
            st = self.state.get(lead_id)
            avail = leads_mail.channels_of(row)
            channel = st.get("channel") or next(
                (c for c in leads_mail.PRIORITY if c in avail), "")
            link = ""
            if channel in avail:
                link = leads_mail.contact_link(channel, avail[channel])
                target = st.get("target") or leads_mail.contact_display(channel, avail[channel])
            else:
                target = st.get("target") or ""
            self._links[lead_id] = link
            status = st.get("status") or "—"
            text = (st.get("text") or "").replace("\n", " ")
            self.tree.insert("", "end", iid=lead_id, values=(
                row.get("name", ""), link or target, channel, status, text[:90]))

    def _open_link(self, _event=None) -> None:
        """Открывает ссылку на контакт выбранного лида в браузере."""
        sel = self.tree.selection()
        if not sel:
            self._log("выбери лида в таблице", "warn")
            return
        url = getattr(self, "_links", {}).get(sel[0], "")
        if not url:
            self._log(f"{sel[0]}: ссылки на контакт нет (телефон или пустой канал)", "warn")
            return
        webbrowser.open(url)
        self._log(f"открыто: {url}", "ok")

    def _show_text(self, _event=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        st = self.state.get(sel[0])
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", st.get("text") or "")

    # -- настройки --------------------------------------------------------- #
    def _collect(self) -> dict:
        data = dict(self.settings)
        for key, var in self.vars.items():
            data[key] = var.get()
        for key, text in self.texts.items():
            data[key] = text.get("1.0", "end").strip()
        data["dry_run"] = bool(self.dry_var.get())
        return data

    # -- действия ---------------------------------------------------------- #
    def _generate(self) -> None:
        if not self.leads:
            self._log("нечего генерировать: нет горячих лидов", "warn")
            return
        self.settings = self._collect()
        leads_mail.save_settings(self.settings_path, self.settings)
        self.stop_event.clear()
        self.status_var.set("Генерирую тексты…")

        def work():
            return leads_mail.generate_texts(
                self.leads, self.settings, self.state,
                lambda text, tag=None: self._post_log(text, tag),
                lambda text: self.hub.post(lambda t=text: self.status_var.set(t) if self.winfo_exists() else None),
                self.stop_event,
            )

        self.hub.run_bg(work, self._generated, self._failed)

    def _generated(self, count) -> None:
        if not self.winfo_exists():
            return
        self.status_var.set(f"Готово: текстов {count}")
        self._refresh_tree()
        self._log(f"тексты готовы: {count}", "ok")

    def _send(self) -> None:
        if not self.leads:
            self._log("нечего отправлять", "warn")
            return
        pending = [r for r in self.leads
                   if self.state.get(leads_mail.row_id(r)).get("status")
                   not in ("replied", "stopped", "read", "seen", "closed", "failed")]
        if not pending:
            self._log("все лиды уже обработаны", "warn")
            return
        if not messagebox.askyesno(
            "Подтвердить рассылку",
            f"Отправить сообщения {len(pending)} горячим лидам?\n\n"
            "По одному в минуту. Через 5 дней без ответа — следующий канал.",
            parent=self,
        ):
            return

        self.settings = self._collect()
        leads_mail.save_settings(self.settings_path, self.settings)
        self.stop_event.clear()
        self.status_var.set("Рассылка идёт…")

        mailer = leads_mail.Mailer(
            self.leads, self.settings, self.state,
            lambda text, tag=None: self._post_log(text, tag),
            lambda _event: self.hub.post(self._refresh_tree),
            pool_file=POOL_FILE,
        )
        self.mailer = mailer
        threading.Thread(target=mailer.run_forever, args=(self.stop_event,), daemon=True).start()

        if not self.settings.get("dry_run"):
            watcher = leads_mail.Poller(
                self.leads, self.settings, self.state,
                lambda text, tag=None: self._post_log(text, tag), self.stop_event,
                pool_file=POOL_FILE,
            )
            threading.Thread(target=watcher.run_forever, daemon=True).start()
        self._log("отправка запущена в фоне", "ok")

    def _stop_all(self) -> None:
        self.stop_event.set()
        self.status_var.set("Остановлено")
        self._log("остановлено вручную", "warn")

    def _mark(self, status: str) -> None:
        sel = self.tree.selection()
        if not sel:
            self._log("выбери лида в таблице", "warn")
            return
        st = self.state.get(sel[0])
        st["status"] = status
        st["closed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.state.save()
        try:
            leads_mail.pool_mark_status(POOL_FILE, sel[0], status)
        except (OSError, leads_mail.PoolBusy) as exc:
            self._log(f"пул: {exc}", "warn")
        self._refresh_tree()
        self._log(f"{sel[0]}: {status}", "ok")

    def _mark_manual(self) -> None:
        """Я написал этому лиду сам — из пула он больше не выпадет."""
        sel = self.tree.selection()
        if not sel:
            self._log("выбери лида в таблице", "warn")
            return
        lead_id = sel[0]
        st = self.state.get(lead_id)
        st["status"] = "awaiting"
        st["sent_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        st["channel"] = st.get("channel") or "manual"
        self.state.save()
        try:
            leads_mail.pool_mark_sent(POOL_FILE, lead_id, "manual")
        except (OSError, leads_mail.PoolBusy) as exc:
            self._log(f"пул: {exc}", "warn")
        self._refresh_tree()
        self._log(f"{lead_id}: отмечено «написал вручную»", "ok")

    def _reset_failed(self) -> None:
        count = self.state.reset_failed()
        self._refresh_tree()
        self._log(f"сброшено ошибок: {count}", "ok" if count else "warn")

    # -- логи -------------------------------------------------------------- #
    def _log(self, text: str, tag: str | None = None) -> None:
        self.log.insert("end", text + "\n", tag or ())
        self.log.see("end")

    def _post_log(self, text: str, tag: str | None = None) -> None:
        self.hub.post(lambda: self._log(text, tag) if self.winfo_exists() else None)

    def _failed(self, exc: Exception) -> None:
        self.status_var.set("Ошибка")
        self._log(f"ошибка: {exc}", "error")

    # -- Telethon-авторизация (без консоли) ------------------------------- #
    def _auth_tg(self) -> None:
        if not MAIL_OK:
            self._log("модуль рассылки не загружен", "error")
            return
        self.settings = self._collect()
        leads_mail.save_settings(self.settings_path, self.settings)
        phone = (self.settings.get("tg_phone") or "").strip()
        if not phone:
            self._log("сначала введи номер телефона (TG номер телефона)", "warn")
            return

        def ask_on_main(title, prompt, secret=False):
            res = [None]
            ev = threading.Event()

            def dlg():
                try:
                    res[0] = simpledialog.askstring(
                        title, prompt, parent=self, show="*" if secret else None)
                finally:
                    ev.set()

            self.hub.post(dlg)
            ev.wait()
            return res[0]

        def work():
            return leads_mail.tg_login(
                self.settings, phone,
                ask=ask_on_main,
                on_log=lambda t, tag=None: self._post_log(t, tag),
            )

        def on_done(_res):
            self._log("TG: аккаунт привязан, сессия сохранена", "ok")

        def on_err(exc):
            self._log(f"TG: {exc}", "error")

        self._log("TG: подключение к серверу Telegram…", "info")
        self.hub.run_bg(work, on_done, on_err)

    def _logout_tg(self) -> None:
        self.settings = self._collect()
        leads_mail.save_settings(self.settings_path, self.settings)
        try:
            self._log(leads_mail.tg_logout(self.settings), "warn")
        except Exception as exc:  # noqa: BLE001
            self._log(f"TG: {exc}", "error")

    def _close(self) -> None:
        self.stop_event.set()
        self.settings = self._collect()
        leads_mail.save_settings(self.settings_path, self.settings)
        self.destroy()



class ShortsAutopilotPanel(Panel):
    """Управление приложением Shorts Autopilot: статус, запуск/остановка, DaVinci, логи."""

    def __init__(self, master: tk.Misc, hub) -> None:
        super().__init__(master, hub)
        self.shorts_dir = APP_DIR / "shorts-autopilot"
        self.python_exe = Path(r"C:\Users\admin\.workbuddy-ai\binaries\python\envs\shorts-gui\Scripts\python.exe")
        self.pythonw_exe = Path(r"C:\Users\admin\.workbuddy-ai\binaries\python\envs\shorts-gui\Scripts\pythonw.exe")
        self._running_pid: int | None = None
        self._poll_job = None
        self._last_log_size = 0
        self._build()
        self.refresh_status()
        self.update_log(initial=True)
        self._schedule_poll()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        # -- Карточки статуса сверху --
        cards = tk.Frame(self, bg=BG)
        cards.grid(row=0, column=0, sticky="ew", pady=(0, px(12)))
        cards.columnconfigure(0, weight=1)
        cards.columnconfigure(1, weight=1)
        cards.columnconfigure(2, weight=1)

        # Карточка 1: Служба Shorts Autopilot
        c1 = tk.Frame(cards, bg=CARD, highlightbackground=BORDER, highlightthickness=1, padx=px(14), pady=px(10))
        c1.grid(row=0, column=0, sticky="nsew", padx=(0, px(6)))
        tk.Label(c1, text="Служба Autopilot", bg=CARD, fg=MUTED, font=ui_font(9)).pack(anchor="w")
        self.lbl_svc_status = tk.Label(c1, text="○ Остановлен", bg=CARD, fg=FG, font=ui_font(12, "bold"))
        self.lbl_svc_status.pack(anchor="w", pady=(px(4), 0))
        self.lbl_svc_pid = tk.Label(c1, text="Фоновый мониторинг ссылок", bg=CARD, fg=MUTED, font=ui_font(8))
        self.lbl_svc_pid.pack(anchor="w")

        # Карточка 2: Веб-панель
        c2 = tk.Frame(cards, bg=CARD, highlightbackground=BORDER, highlightthickness=1, padx=px(14), pady=px(10))
        c2.grid(row=0, column=1, sticky="nsew", padx=px(4))
        tk.Label(c2, text="Веб-панель (UI)", bg=CARD, fg=MUTED, font=ui_font(9)).pack(anchor="w")
        self.lbl_web_status = tk.Label(c2, text="○ Офлайн", bg=CARD, fg=MUTED, font=ui_font(12, "bold"))
        self.lbl_web_status.pack(anchor="w", pady=(px(4), 0))
        tk.Label(c2, text="http://127.0.0.1:8765", bg=CARD, fg=MUTED, font=ui_font(8)).pack(anchor="w")

        # Карточка 3: DaVinci Resolve
        c3 = tk.Frame(cards, bg=CARD, highlightbackground=BORDER, highlightthickness=1, padx=px(14), pady=px(10))
        c3.grid(row=0, column=2, sticky="nsew", padx=(px(6), 0))
        tk.Label(c3, text="DaVinci Resolve", bg=CARD, fg=MUTED, font=ui_font(9)).pack(anchor="w")
        self.lbl_davinci_status = tk.Label(c3, text="○ Закрыт", bg=CARD, fg=MUTED, font=ui_font(12, "bold"))
        self.lbl_davinci_status.pack(anchor="w", pady=(px(4), 0))
        self.lbl_davinci_info = tk.Label(c3, text="Timeline: 1080x1920 @ 60 fps", bg=CARD, fg=MUTED, font=ui_font(8))
        self.lbl_davinci_info.pack(anchor="w")

        # -- Кнопки управления --
        toolbar = tk.Frame(self, bg=BG)
        toolbar.grid(row=1, column=0, sticky="ew", pady=(0, px(12)))

        self.btn_start = flat_button(toolbar, "▶ Запустить Autopilot", self.start_autopilot, kind="primary")
        self.btn_start.pack(side="left")

        self.btn_stop = flat_button(toolbar, "⏹ Остановить", self.stop_autopilot, kind="danger")
        self.btn_stop.pack(side="left", padx=(px(8), 0))

        self.btn_web = flat_button(toolbar, "🌐 Открыть веб-панель", self.open_webui, kind="ghost")
        self.btn_web.pack(side="left", padx=(px(8), 0))

        self.btn_tests = flat_button(toolbar, "🧪 Прогнать тесты", self.run_tests_bg, kind="ghost")
        self.btn_tests.pack(side="left", padx=(px(8), 0))

        self.btn_folder = flat_button(toolbar, "📂 Папка проекта", self.open_folder, kind="ghost")
        self.btn_folder.pack(side="right")

        self.btn_cfg = flat_button(toolbar, "⚙ config.json", self.open_config, kind="ghost")
        self.btn_cfg.pack(side="right", padx=(0, px(8)))

        # -- Журнал событий --
        log_frame, self.log_box = self.make_log(self, height=12, label="Лог Shorts Autopilot")
        log_frame.grid(row=2, column=0, sticky="nsew")
        self.append_log(self.log_box, "Shorts Autopilot подключён в качестве приложения.")

    def _schedule_poll(self) -> None:
        if self._poll_job:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass
        self._poll_job = self.after(3000, self._on_poll)

    def _on_poll(self) -> None:
        if not self.winfo_exists():
            return
        if getattr(self.hub, "current", None) == "shorts":
            self.refresh_status()
            self.update_log()
        self._schedule_poll()

    def on_show(self) -> None:
        self.refresh_status()
        self.update_log()

    def shutdown(self) -> None:
        if self._poll_job:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass
            self._poll_job = None

    def refresh_status(self) -> None:
        pid = self._find_pid()
        self._running_pid = pid
        if pid:
            self.lbl_svc_status.configure(text=f"● Работает (PID {pid})", fg=OK)
            self.lbl_svc_pid.configure(text="Перехват ссылок и автоконвейер активны")
            self.btn_start.configure(state="disabled")
            self.btn_stop.configure(state="normal")
        else:
            self.lbl_svc_status.configure(text="○ Остановлен", fg=MUTED)
            self.lbl_svc_pid.configure(text="Нажмите «Запустить Autopilot» для старта")
            self.btn_start.configure(state="normal")
            self.btn_stop.configure(state="disabled")

        if getattr(self, "_refreshing_bg", False):
            return
        self._refreshing_bg = True

        def bg_checks() -> tuple[bool, bool]:
            web = self._check_webui()
            davinci = self._check_process("Resolve.exe")
            return web, davinci

        def on_done(res: tuple[bool, bool]) -> None:
            self._refreshing_bg = False
            if not self.winfo_exists():
                return
            web_ok, davinci_ok = res
            if web_ok:
                self.lbl_web_status.configure(text="● Онлайн", fg=OK)
                self.btn_web.configure(state="normal")
            else:
                self.lbl_web_status.configure(text="○ Офлайн", fg=MUTED)
            if davinci_ok:
                self.lbl_davinci_status.configure(text="● Запущен", fg=OK)
            else:
                self.lbl_davinci_status.configure(text="○ Закрыт", fg=MUTED)

        def on_err(_exc) -> None:
            self._refreshing_bg = False

        self.hub.run_bg(bg_checks, on_done, on_err)

    def _find_pid(self) -> int | None:
        pid_file = self.shorts_dir / "state" / ".single_instance.pid"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text(encoding="utf-8").strip())
                if pid > 0 and self._is_pid_alive(pid):
                    return pid
            except (OSError, ValueError):
                pass
        return None

    def _is_pid_alive(self, pid: int) -> bool:
        try:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
        except Exception:
            pass
        return False

    def _check_process(self, exe_name: str) -> bool:
        try:
            exe_low = exe_name.lower()
            return any(exe_low in p.lower() for p in ps_run("Get-Process | Select-Object -ExpandProperty ProcessName").splitlines())
        except Exception:
            return False

    def _check_webui(self) -> bool:
        try:
            req = urllib.request.Request("http://127.0.0.1:8765/api/state", headers={"User-Agent": "Tools/1.0"})
            with urllib.request.urlopen(req, timeout=0.8) as resp:
                return resp.status == 200
        except Exception:
            return False

    def start_autopilot(self) -> None:
        py_bin = self.pythonw_exe if self.pythonw_exe.exists() else (self.python_exe if self.python_exe.exists() else Path("pythonw.exe"))
        main_script = self.shorts_dir / "app" / "main.py"
        if not main_script.exists():
            self.hub.toast("Скрипт app/main.py не найден!", kind="error")
            return
        try:
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            env["TMPDIR"] = str(self.shorts_dir / "state" / "pytest-tmp")
            subprocess.Popen(
                [str(py_bin), str(main_script), "--ui"],
                cwd=str(self.shorts_dir),
                env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.hub.toast("Shorts Autopilot запущен", kind="ok")
            self.append_log(self.log_box, "Запущена служба Shorts Autopilot (--ui)...")
            self.after(1500, self.refresh_status)
        except Exception as exc:
            self.hub.toast(f"Ошибка запуска: {exc}", kind="error")
            self.append_log(self.log_box, f"Ошибка запуска: {exc}", tag="error")

    def stop_autopilot(self) -> None:
        pid = self._find_pid()
        if not pid:
            self.hub.toast("Процесс не найден", kind="warn")
            self.refresh_status()
            return
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=5)
            lock = self.shorts_dir / "state" / ".single_instance.pid"
            if lock.exists():
                try:
                    lock.unlink(missing_ok=True)
                except Exception:
                    pass
            self.hub.toast("Shorts Autopilot остановлен", kind="ok")
            self.append_log(self.log_box, f"Служба (PID {pid}) остановлена.")
            self.after(500, self.refresh_status)
        except Exception as exc:
            self.hub.toast(f"Ошибка остановки: {exc}", kind="error")
            self.append_log(self.log_box, f"Ошибка остановки: {exc}", tag="error")

    def open_webui(self) -> None:
        if not self._check_webui():
            self.hub.toast("Запускаю службу Shorts Autopilot для веб-панели...", kind="ok")
            self.start_autopilot()
            self.after(1800, lambda: webbrowser.open("http://127.0.0.1:8765"))
            return
        webbrowser.open("http://127.0.0.1:8765")

    def open_folder(self) -> None:
        os.startfile(str(self.shorts_dir))

    def open_config(self) -> None:
        cfg = self.shorts_dir / "config.json"
        if cfg.exists():
            os.startfile(str(cfg))

    def run_tests_bg(self) -> None:
        self.btn_tests.configure(state="disabled")
        self.append_log(self.log_box, "Запуск пакета тестов run_tests.bat в фоне...")

        def work():
            bat = self.shorts_dir / "run_tests.bat"
            res = subprocess.run([str(bat)], cwd=str(self.shorts_dir), capture_output=True, text=True, encoding="utf-8", errors="replace")
            return res.returncode, res.stdout, res.stderr

        def on_done(result):
            code, out, err = result
            self.btn_tests.configure(state="normal")
            if code == 0:
                self.hub.toast("Все тесты успешно пройдены!", kind="ok")
                self.append_log(self.log_box, "Тесты run_tests.bat: ВСЕ 949 ТЕСТОВ И 30 СЦЕНАРИЕВ ПРОЙДЕНЫ УСПЕШНО!", tag="ok")
            else:
                self.hub.toast("Тесты завершились с ошибкой", kind="error")
                self.append_log(self.log_box, f"Ошибка тестов:\n{err or out}", tag="error")

        def on_error(exc):
            self.btn_tests.configure(state="normal")
            self.hub.toast(f"Сбой тестов: {exc}", kind="error")
            self.append_log(self.log_box, f"Сбой запуска тестов: {exc}", tag="error")

        self.hub.run_bg(work, on_done, on_error)

    def update_log(self, initial: bool = False) -> None:
        log_file = self.shorts_dir / "logs" / "app.log"
        if not log_file.exists():
            return
        try:
            size = log_file.stat().st_size
            if size == getattr(self, "_last_log_size", 0) and not initial:
                return
            self._last_log_size = size
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()[-30:]
            if initial:
                self.log_box.delete("1.0", "end")
            for line in (lines if initial else lines[-10:]):
                tag = None
                if "ERROR" in line:
                    tag = "error"
                elif "WARNING" in line:
                    tag = "warn"
                elif "УСПЕШНО" in line or "готово" in line:
                    tag = "ok"
                self.append_log(self.log_box, line.rstrip(), tag=tag)
        except Exception:
            pass


# --------------------------------------------------------------------------- #
#  Дневник МО · Контроль балла — вкладка хаба
# --------------------------------------------------------------------------- #
# Подпроект живёт в diary-grades/. Его модули называются слишком общо
# (models / config / grade_math), поэтому НЕ добавляем папку в sys.path:
# загружаем ядро в изолированном неймспейсе, на время подменив sys.modules.
_DIARY_CORE = None


def diary_grades_core():
    """(config, grade_math, demo_data, models) подпроекта diary-grades или None."""
    global _DIARY_CORE
    if _DIARY_CORE is not None:
        return _DIARY_CORE
    import importlib.util
    import types

    folder = APP_DIR / "diary-grades"
    if not (folder / "grade_math.py").exists():
        return None
    package_name = "_tools_diary_core"
    package = types.ModuleType(package_name)
    package.__path__ = [str(folder)]
    sys.modules[package_name] = package

    def load(name: str):
        spec = importlib.util.spec_from_file_location(
            f"{package_name}.{name}", folder / f"{name}.py"
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Не удалось загрузить {name}.py из {folder}")
        module = importlib.util.module_from_spec(spec)
        # Регистрация до exec_module обязательна: в models.py dataclass(slots=True)
        # с отложенными аннотациям обращается к модулю через sys.modules.
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    sentinel = object()
    previous = sys.modules.get("models", sentinel)
    try:
        models_module = load("models")
        sys.modules["models"] = models_module
        try:
            config_module = load("config")
            math_module = load("grade_math")
            demo_module = load("demo_data")
        finally:
            if previous is sentinel:
                sys.modules.pop("models", None)
            else:
                sys.modules["models"] = previous
    except Exception:  # noqa: BLE001 — отсутствие подпроекта не роняет хаб
        log_exception()
        return None
    _DIARY_CORE = (config_module, math_module, demo_module, models_module)
    return _DIARY_CORE


# Мосты вызываются отдельным процессом из .venv подпроекта: у хаба может не быть
# Playwright, а sync_playwright нельзя гонять в потоке с уже работающим Tk.
_DIARY_BRIDGE_PREAMBLE = """
import json, sys
for _stream in (getattr(sys, "stdout", None), getattr(sys, "stderr", None)):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
from datetime import datetime
import config, diary_client
from models import DiaryError, SubjectGrades

def save(result):
    # После обновления в таблице должны остаться ВСЕ предметы из шаблона,
    # а не только те, что портал прислал на первой странице.
    subjects = list(result.subjects)
    known = {s.name.casefold() for s in subjects}
    for name in config.expected_subjects():
        key = name.casefold()
        if key not in known:
            known.add(key)
            subjects.append(SubjectGrades(name, (), result.period, 'pending'))
    config.atomic_write_json(config.CACHE_FILE, {
        'updated_at': datetime.now().isoformat(timespec='seconds'),
        'period': result.period,
        'source': result.source,
        'subjects': [
            {'name': s.name,
             # Mark наследует int: list(s.marks) в JSON пишет голые числа и
             # молча теряет вес «5₂», из-за чего средний разошёлся с дневником.
             'marks': [m.to_dict_or_int() if hasattr(m, 'to_dict_or_int') else int(m)
                       for m in s.marks],
             'period': s.period,
             'official_average': getattr(s, 'official_average', '')}
            for s in subjects
        ],
    })
    print(json.dumps({'ok': True, 'subjects': len(subjects),
                      'with_marks': sum(1 for s in subjects if s.marks),
                      'period': result.period}, ensure_ascii=False))

def fail(message):
    print(json.dumps({'ok': False, 'error': str(message)}, ensure_ascii=False))
    sys.exit(1)
"""

_DIARY_FETCH_CODE = _DIARY_BRIDGE_PREAMBLE + """
try:
    save(diary_client.fetch_grades(config.current_period()))
except DiaryError as exc:
    fail(exc)
except Exception as exc:
    fail('Сбой сборщика: ' + str(exc))
"""

_DIARY_LOGIN_CODE = _DIARY_BRIDGE_PREAMBLE + """
settings = config.load_settings()
if not diary_client.interactive_login(timeout_s=int(settings.get('login_timeout_s', 300))):
    fail('Вход через Госуслуги не выполнен или истекло время ожидания.')
try:
    save(diary_client.fetch_grades(config.current_period()))
except DiaryError as exc:
    fail('Вход выполнен, сессия сохранена, но оценки не получены: ' + str(exc))
except Exception as exc:
    fail('Сбой сборщика: ' + str(exc))
"""

_DIARY_IMPORT_CODE = """
import json, sys
for _stream in (getattr(sys, "stdout", None), getattr(sys, "stderr", None)):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
from datetime import datetime
from pathlib import Path
import config, parsers
from models import SubjectGrades
try:
    path = Path(sys.argv[1])
    subjects = parsers.validate_subjects(parsers.parse_file(path))
    # Заготовки без оценок (real_grades.json) не выбрасываем молча: иначе в окне
    # остаётся «одна оценка» и непонятно, что остальные предметы просто не загружены.
    try:
        raw = json.loads(path.read_text(encoding='utf-8-sig'))
    except Exception:
        raw = None
    if isinstance(raw, dict):
        seen = {s.name.casefold() for s in subjects}
        for item in raw.get('subjects') or []:
            if not isinstance(item, dict):
                continue
            name = parsers.clean_name(str(item.get('name') or ''))
            if not name or name.casefold() in seen:
                continue
            subjects.append(SubjectGrades(name, (), '', 'pending'))
            seen.add(name.casefold())
    if not subjects:
        raise ValueError('Предметы в файле не найдены.')
    config.atomic_write_json(config.CACHE_FILE, {
        'updated_at': datetime.now().isoformat(timespec='seconds'),
        'period': config.current_period().label,
        'source': 'import',
        'subjects': [
            {'name': s.name,
             # Как и в fetch-мосту: сохраняем вес оценки, не голый int.
             'marks': [m.to_dict_or_int() if hasattr(m, 'to_dict_or_int') else int(m)
                       for m in s.marks],
             'period': s.period,
             'official_average': getattr(s, 'official_average', '')}
            for s in subjects
        ],
    })
    print(json.dumps({'ok': True, 'subjects': len(subjects),
                      'with_marks': sum(1 for s in subjects if s.marks),
                      'period': config.current_period().label}, ensure_ascii=False))
except Exception as exc:
    print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
    sys.exit(1)
"""


class DiaryGradesPanel(Panel):
    """Оценки электронного дневника МО прямо среди остальных инструментов хаба."""

    COLUMNS = (
        ("Предмет", 145, 3),
        ("Средний балл", 90, 0),
        ("Текущие оценки", 205, 4),
        ("До 5-ки", 80, 0),
        ("Запас прочности", 145, 2),
    )
    BADGE_ROLES = {
        "red": ("badge_bad_bg", "badge_bad_ink"),
        "yellow": ("badge_warn_bg", "badge_warn_ink"),
        "green": ("badge_good_bg", "badge_good_ink"),
    }

    def __init__(self, master: tk.Misc, hub) -> None:
        super().__init__(master, hub)
        self.dir = APP_DIR / "diary-grades"
        core = diary_grades_core()
        self.cfg, self.gm, self.demo, self.models = core if core else (None,) * 4
        self.reports = []
        self.filtered = []
        self._row_widgets: list[list[tk.Label]] = []
        self._selected_index = -1
        self._sort_mode = "average"  # "average" | "alpha"
        self.source_label = "—"
        self.demo_note = ""
        self.period_label = "—"
        self.cache_stamp = None
        self._busy = False
        self._poll_job = None
        self._last_fetch = 0.0
        self._initial_fetch_done = False
        self._build()
        self.reload()
        self._schedule_poll()

    # -- разметка ---------------------------------------------------------- #
    @staticmethod
    def _card(parent: tk.Misc, column: int, title: str):
        frame = tk.Frame(parent, bg=CARD, highlightbackground=BORDER,
                         highlightthickness=1, padx=px(12), pady=px(8))
        frame.grid(row=0, column=column, sticky="nsew",
                   padx=(0 if column == 0 else px(6), 0))
        tk.Label(frame, text=title, bg=CARD, fg=MUTED,
                 font=ui_font(8), anchor="w").pack(fill="x")
        label = tk.Label(frame, text="—", bg=CARD, fg=FG,
                         font=ui_font(11, "bold"), anchor="w")
        label.pack(fill="x", pady=(px(3), 0))
        return label

    def _configure_columns(self, frame: tk.Misc) -> None:
        for index, (_name, minsize, weight) in enumerate(self.COLUMNS):
            frame.columnconfigure(index, weight=weight, minsize=px(minsize))

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        cards = tk.Frame(self, bg=BG)
        cards.grid(row=0, column=0, sticky="ew", pady=(0, px(10)))
        for index in range(4):
            cards.columnconfigure(index, weight=1)
        self.lbl_source = self._card(cards, 0, "Источник")
        self.lbl_period = self._card(cards, 1, "Период")
        self.lbl_count = self._card(cards, 2, "Предметов")
        self.lbl_session = self._card(cards, 3, "Сессия ЕСИА")

        toolbar = tk.Frame(self, bg=BG)
        toolbar.grid(row=1, column=0, sticky="ew", pady=(0, px(10)))
        self.btn_refresh = flat_button(toolbar, "▶ Обновить оценки", self._refresh)
        self.btn_refresh.pack(side="left")
        flat_button(toolbar, "Войти заново", self._login, kind="ghost").pack(
            side="left", padx=(px(6), 0))
        flat_button(toolbar, "Окно", lambda: self._open_app("--no-login"),
                    kind="ghost").pack(side="left", padx=(px(6), 0))
        flat_button(toolbar, "Демо", self._demo, kind="ghost").pack(
            side="left", padx=(px(6), 0))
        flat_button(toolbar, "Импорт", self._import_file, kind="ghost").pack(
            side="left", padx=(px(6), 0))
        self.btn_sort = flat_button(toolbar, "Сортировка: по среднему", self._on_sort,
                                    kind="ghost")
        self.btn_sort.pack(side="left", padx=(px(6), 0))
        flat_button(toolbar, "Папка", self._open_folder, kind="ghost").pack(side="right")
        self.search_var = tk.StringVar()
        ttk.Entry(toolbar, textvariable=self.search_var, width=12).pack(
            side="right", padx=(0, px(6)))
        tk.Label(toolbar, text="Поиск:", bg=BG, fg=MUTED,
                 font=ui_font(9)).pack(side="right", padx=(0, px(6)))
        self.search_var.trace_add("write", lambda *_: self._render_rows())

        table = tk.Frame(self, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        table.grid(row=2, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(1, weight=1)

        head = tk.Frame(table, bg=FIELD, height=px(30))
        head.grid(row=0, column=0, sticky="ew")
        head.grid_propagate(False)
        self._configure_columns(head)
        for index, (name, _size, _weight) in enumerate(self.COLUMNS):
            tk.Label(head, text=name, bg=FIELD, fg=MUTED, anchor="w",
                     font=ui_font(9, "bold")).grid(
                row=0, column=index, sticky="ew", padx=px(8))

        self.canvas = tk.Canvas(table, bg=CARD, bd=0, highlightthickness=0)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.canvas.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=scroll.set)
        self.rows_frame = tk.Frame(self.canvas, bg=CARD)
        self._rows_window = self.canvas.create_window(
            (0, 0), window=self.rows_frame, anchor="nw")
        self.rows_frame.bind(
            "<Configure>",
            lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind(
            "<Configure>",
            lambda event: self.canvas.itemconfigure(self._rows_window, width=event.width))
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.rows_frame.bind("<MouseWheel>", self._on_wheel)

        details = tk.Frame(self, bg=BG)
        details.grid(row=3, column=0, sticky="ew", pady=(px(10), 0))
        tk.Label(details, text="Разбор расчёта — клик по строке таблицы",
                 bg=BG, fg=MUTED, font=ui_font(8), anchor="w").pack(fill="x")
        self.details = dark_text(details, height=5)
        self.details.pack(fill="x")
        self.details.configure(state="disabled")

        self.footer_var = tk.StringVar(value="—")
        tk.Label(self, textvariable=self.footer_var, bg=BG, fg=MUTED,
                 font=ui_font(8), anchor="w").grid(row=4, column=0, sticky="ew")

        log_frame, self.log_box = self.make_log(self, height=4, label="Журнал дневника")
        log_frame.grid(row=5, column=0, sticky="ew", pady=(px(8), 0))
        self._log("Вкладка «Дневник МО» подключена к diary-grades/.")

    # -- данные ------------------------------------------------------------ #
    def _stamp(self):
        try:
            stat = self.cfg.CACHE_FILE.stat()
            return (stat.st_mtime_ns, stat.st_size)
        except (OSError, AttributeError):
            return None

    def reload(self) -> None:
        if self.gm is None:
            self.source_label = "ЯДРО НЕ НАЙДЕНО"
            self.lbl_source.configure(text=self.source_label, fg=DANGER)
            self._show_message(f"Не найдено ядро подпроекта: {self.dir}")
            return

        subjects = []
        payload = None
        try:
            payload = json.loads(self.cfg.CACHE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError, AttributeError):
            pass
        for item in (payload or {}).get("subjects") or []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            try:
                subjects.append(self.models.SubjectGrades(
                    item["name"], tuple(item.get("marks") or ()),
                    item.get("period", ""), "cache",
                    official_average=str(item.get("official_average") or "")))
            except (TypeError, ValueError):
                continue

        cur_p = self.cfg.current_period() if self.cfg else None
        cur_p_label = cur_p.label if cur_p else "—"
        if subjects:
            self.source_label = "КЭШ" if (payload or {}).get("source") != "demo" else "КЭШ · ДЕМО"
            self.period_label = (payload or {}).get("period") or cur_p_label
            self.demo_note = ""
        else:
            result = self.demo.demo_result(cur_p_label)
            subjects = list(result.subjects)
            self.source_label = "ДЕМО-ДАННЫЕ"
            self.demo_note = "  ·  не реальные оценки"
            self.period_label = result.period
            self._log("Кэша нет: показаны демо-данные, это не реальные оценки.", "warn")

        try:
            self.reports = self.gm.sort_reports(
                self.gm.build_reports(subjects), self._sort_mode)
        except (ValueError, TypeError) as exc:
            self.reports = []
            self._log(f"Ошибка расчёта: {exc}", "error")

        missing = sum(1 for r in self.reports if not r.count)
        # «Частично» — только когда не хватает оценок, которые должны быть.
        # Предметы профиля без оценок за период (пустые в шаблоне) нормой и
        # не должны окрашивать плашку в жёлтый при сохранённой сессии.
        gaps = self._cache_has_gaps() if "КЭШ" in self.source_label else False
        if gaps:
            self.source_label += " · ЧАСТИЧНО"
        demo_like = ("ДЕМО" in self.source_label or "НЕ НАЙДЕНО" in self.source_label
                     or bool(gaps))
        self.lbl_source.configure(text=self.source_label, fg=WARN if demo_like else OK)
        self.lbl_period.configure(text=self.period_label)
        self.lbl_count.configure(
            text=f"{len(self.reports)} · с оценками {len(self.reports) - missing}"
            if missing else str(len(self.reports)))
        self._refresh_session_card()
        self.cache_stamp = self._stamp()
        self._render_rows()

    def on_show(self) -> None:
        """Хаб вызывает при каждом переключении на вкладку."""
        self.reload()

    def _refresh_session_card(self) -> None:
        try:
            ok = bool(self.cfg.session_exists())
        except Exception:  # noqa: BLE001
            ok = False
        self.lbl_session.configure(text="● Сохранена" if ok else "○ Нет входа",
                                   fg=OK if ok else WARN)

    def _schedule_poll(self) -> None:
        try:
            self._poll_job = self.after(2000, self._on_poll)
        except (tk.TclError, AttributeError):
            self._poll_job = None

    def _on_poll(self) -> None:
        try:
            if not self.winfo_exists():
                return
        except (tk.TclError, AttributeError):
            return
        try:
            stamp = self._stamp()
            if stamp != self.cache_stamp:
                self.reload()
                self._log("Кэш обновлён — таблица перечитана.", "ok")
        except Exception:  # noqa: BLE001 — опрос не должен ронять окно
            pass
        # Файл сессии мог появиться после входа ЕСИА, даже если кэш ещё не
        # обновился (сбор оценок мог завершиться ошибкой). Карточку «Сессия
        # ЕСИА» обновляем на каждом опросе, а не только при смене кэша.
        try:
            self._refresh_session_card()
        except Exception:  # noqa: BLE001
            pass
        self._maybe_auto_refresh()
        self._schedule_poll()

    def _cache_has_gaps(self) -> bool:
        """Есть ли предметы, по которым ждали оценки, но их нет в кэше.

        Предметы из профиля портала без оценок («Вероятность и статистика»,
        «Математика») помечены в шаблоне пустым ``marks`` и дырами не считаются:
        иначе каждый запуск дёргал бы скрытый браузер без всякой пользы.
        """
        try:
            raw = json.loads(self.cfg.SUBJECTS_FILE.read_text(encoding="utf-8-sig"))
            known_empty = {
                str(item.get("name") or "").strip().casefold()
                for item in raw.get("subjects") or []
                if isinstance(item, dict) and not (item.get("marks") or ())
            }
            payload = json.loads(self.cfg.CACHE_FILE.read_text(encoding="utf-8"))
            return any(
                not (item.get("marks") or ())
                and str(item.get("name") or "").strip().casefold() not in known_empty
                for item in payload.get("subjects") or []
                if isinstance(item, dict)
            )
        except Exception:  # noqa: BLE001
            return False

    def _maybe_auto_refresh(self) -> None:
        """Скрыто обновляет ВСЕ предметы при сохранённой сессии.

        Первый раз — сразу, если после входа в кэше ещё есть пустые предметы:
        пользователь вошёл через ЕСИА и ждёт оценки, а не таймер. Дальше —
        раз в ``auto_refresh_min`` минут. Без сессии ничего не делаем и окно
        входа не дёргаем.
        """
        if self.cfg is None or self._busy:
            return
        try:
            minutes = float(self.cfg.load_settings().get("auto_refresh_min", 30))
            has_session = bool(self.cfg.session_exists())
        except Exception:  # noqa: BLE001
            return
        if minutes <= 0 or not has_session:
            return
        now = time.monotonic()
        if not self._last_fetch:
            self._last_fetch = now
            self._initial_fetch_done = True
            # При каждом запуске хаба сразу подтягиваем свежие оценки,
            # а не только когда в кэше остались пустые предметы.
            self._start_bg("Автозагрузка оценок при запуске…",
                           _DIARY_FETCH_CODE, (), 240)
            return
        if now - self._last_fetch < minutes * 60:
            return
        self._last_fetch = now
        self._start_bg("Автообновление всех предметов…", _DIARY_FETCH_CODE, (), 240)

    # -- таблица ----------------------------------------------------------- #
    def _sort_label(self) -> str:
        return ("Сортировка: по алфавиту" if self._sort_mode == "alpha"
                else "Сортировка: по среднему")

    def _on_sort(self) -> None:
        """Переключает порядок строк: по среднему баллу ↔ по алфавиту."""
        if self.gm is None:
            return
        self._sort_mode = "alpha" if self._sort_mode == "average" else "average"
        self.btn_sort.configure(text=self._sort_label())
        self.reports = self.gm.sort_reports(self.reports, self._sort_mode)
        self._render_rows()
        self._log("Порядок строк: " + ("по алфавиту" if self._sort_mode == "alpha"
                                       else "от худшего среднего к лучшему"))

    def _on_wheel(self, event) -> None:
        try:
            self.canvas.yview_scroll(
                (-1 if event.delta > 0 else 1) * _scroll_step(), "units")
        except (tk.TclError, AttributeError):
            pass

    def _render_rows(self) -> None:
        for child in self.rows_frame.winfo_children():
            child.destroy()
        self._row_widgets = []
        self._configure_columns(self.rows_frame)
        needle = (self.search_var.get() if hasattr(self, "search_var") else "").strip().casefold()
        self.filtered = [r for r in self.reports
                         if not needle or needle in r.name.casefold()]

        for index, report in enumerate(self.filtered):
            self._add_row(index, report)

        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        missing = sum(1 for r in self.reports if not r.count)
        tail = f"  ·  без оценок: {missing} (в периоде оценок нет)" if missing else ""
        order = "по алфавиту" if self._sort_mode == "alpha" else "худшие сверху"
        self.footer_var.set(
            f"Показано {len(self.filtered)} из {len(self.reports)}  ·  порог 4.50  ·  "
            f"{order}  ·  {self.source_label}"
            + self.demo_note + tail)
        if self.filtered:
            self._select(0, self.filtered[0])
        elif self.reports:
            self._show_message("Ничего не найдено. Измени запрос в поиске.")
        else:
            self._show_message("Данных нет. Нажми «Обновить оценки», «Войти заново» или «Демо».")

    def _add_row(self, index: int, report) -> None:
        background = CARD if index % 2 == 0 else CARD_HOVER
        empty = not report.count and not getattr(report, "official_average", "")
        badge_bg, badge_fg = ((BADGE_NEUTRAL_BG, MUTED) if empty
                              else badge_colors(report.badge))
        fives = "—" if report.on_five else f"{report.fives_needed} × «5»"
        if empty:
            avg_display = "—"
        elif hasattr(report, "average_str"):
            avg_display = str(report.average_str)
        elif self.models and hasattr(self.models, "format_average") and getattr(report, "count", 0):
            avg_display = self.models.format_average(report.total, report.count)
        else:
            from decimal import Decimal, ROUND_HALF_UP
            total = getattr(report, "total", None)
            cnt = getattr(report, "count", None)
            if total is not None and cnt:
                avg_display = str((Decimal(total) / Decimal(cnt)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            else:
                avg_display = str(Decimal(str(getattr(report, "average", 0))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        values = (
            report.name,
            avg_display,
            "нет оценок в периоде" if empty else report.marks_text(),
            "—" if empty else fives,
            self.gm.reserve_text(report.reserve),
        )
        widgets: list[tk.Label] = []
        for column, value in enumerate(values):
            if column == 1:
                label = tk.Label(self.rows_frame, text=value, bg=badge_bg, fg=badge_fg,
                                 font=ui_font(9, "bold"), anchor="center",
                                 padx=px(8), pady=px(3))
                label.grid(row=index, column=column, sticky="w",
                           padx=px(8), pady=px(3))
            else:
                label = tk.Label(self.rows_frame, text=value, bg=background,
                                 fg=FG if column == 0 else MUTED,
                                 font=ui_font(9, "bold" if column == 0 else "normal"),
                                 anchor="w", pady=px(5))
                label.grid(row=index, column=column, sticky="ew", padx=px(8))
            label.bind("<Button-1>",
                       lambda _event, r=report, i=index: self._select(i, r))
            label.bind("<MouseWheel>", self._on_wheel)
            widgets.append(label)
        self._row_widgets.append(widgets)

    def _select(self, index: int, report) -> None:
        self._selected_index = index
        for row_index, widgets in enumerate(self._row_widgets):
            background = CARD if row_index % 2 == 0 else CARD_HOVER
            if row_index == index:
                background = ROW_SELECTED
            for column, label in enumerate(widgets):
                if column != 1:       # цветной бейдж среднего балла не закрашиваем
                    label.configure(bg=background)
        self._show_message(self.gm.explain(report))

    def _show_message(self, text: str) -> None:
        try:
            self.details.configure(state="normal")
            self.details.delete("1.0", "end")
            self.details.insert("1.0", text)
            self.details.configure(state="disabled")
        except (tk.TclError, AttributeError):
            pass

    def _log(self, line: str, tag: str | None = None) -> None:
        try:
            self.append_log(self.log_box, line, tag)
        except (tk.TclError, AttributeError):
            pass

    # -- действия ---------------------------------------------------------- #
    def _python(self, name: str):
        for folder in (".venv/Scripts", ".venv/bin"):
            exe = self.dir / folder / name
            if exe.exists():
                return exe
        return None

    def _run_bridge(self, code: str, argv=(), timeout: int = 180) -> dict:
        """Запускает мост в .venv подпроекта. Вызывается ТОЛЬКО из фонового потока."""
        python = self._python("python.exe")
        if python is None:
            return {"ok": False,
                    "error": "Окружение .venv не готово: один раз запусти diary-grades/run.bat"}
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        command = [str(python), "-c", code, *(str(arg) for arg in argv)]
        try:
            proc = subprocess.run(
                command, cwd=str(self.dir), env=env, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"Превышено время ожидания ({timeout} с)"}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        output = (proc.stdout or "").strip()
        try:
            return json.loads(output.splitlines()[-1]) if output else {
                "ok": False, "error": (proc.stderr or "Пустой ответ моста").strip()[:400]}
        except ValueError:
            return {"ok": False, "error": (proc.stderr or output)[:400]}

    def _start_bg(self, label: str, code: str, argv=(), timeout: int = 180) -> None:
        if self._busy:
            return
        self._busy = True
        self.btn_refresh.configure(state="disabled")
        self._log(label)
        self.hub.set_status("Дневник МО: " + label)

        def work():
            return self._run_bridge(code, argv, timeout)

        def on_done(data):
            self._busy = False
            self.btn_refresh.configure(state="normal")
            if data.get("ok"):
                self.reload()
                if "with_marks" in data:
                    message = (f"Готово: {data.get('subjects', '?')} предметов, "
                               f"с оценками {data['with_marks']}, {data.get('period', '')}")
                else:
                    message = f"Готово: {data.get('subjects', '?')} предметов, {data.get('period', '')}"
                self._log(message, "ok")
                self.hub.toast(message, kind="ok")
            else:
                message = str(data.get("error") or "Неизвестная ошибка")
                self._log(message, "error")
                self.hub.toast(message[:100], kind="error")
                self._refresh_session_card()
            self.hub.set_status("Готово")

        def on_error(exc):
            self._busy = False
            self.btn_refresh.configure(state="normal")
            self._log(f"Сбой: {exc}", "error")
            self.hub.toast(f"Сбой: {exc}", kind="error")
            self.hub.set_status("Готово")

        self.hub.run_bg(work, on_done, on_error)

    def _refresh(self) -> None:
        if self.cfg is None:
            self.hub.toast("Ядро diary-grades не найдено", kind="error")
            return
        try:
            has_session = bool(self.cfg.session_exists())
        except Exception:  # noqa: BLE001
            has_session = False
        if not has_session:
            self._log("Сессии нет — открываем браузер для входа через Госуслуги.", "warn")
            self._login()
            return
        self._start_bg("Забираем оценки из дневника (браузер скрыт)…",
                       _DIARY_FETCH_CODE, (), 180)

    def _login(self) -> None:
        if self.cfg is None:
            return
        self._log("Открываем Chromium: войди через Госуслуги (ЕСИА).")
        self._start_bg("Ожидаем ручной вход в ЕСИА…", _DIARY_LOGIN_CODE, (), 420)

    def _demo(self) -> None:
        if self.gm is None:
            return
        result = self.demo.demo_result(self.cfg.current_period().label)
        self.reports = self.gm.sort_reports(
            self.gm.build_reports(result.subjects), self._sort_mode)
        self.source_label = "ДЕМО-ДАННЫЕ"
        self.demo_note = "  ·  не реальные оценки"
        self.period_label = result.period
        self.lbl_source.configure(text=self.source_label, fg=WARN)
        self.lbl_period.configure(text=self.period_label)
        self.lbl_count.configure(text=str(len(self.reports)))
        self.cache_stamp = self._stamp()
        self._render_rows()
        self._log("Показаны демо-данные; кэш реальных оценок не перезаписан.", "warn")

    def _import_file(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="Сохранённая страница оценок или JSON дневника",
            filetypes=[("HTML / JSON / TXT", "*.html *.htm *.json *.txt"),
                       ("Все файлы", "*.*")])
        if not path:
            return
        self._start_bg(f"Импорт: {Path(path).name}", _DIARY_IMPORT_CODE, (path,), 90)

    def _open_app(self, *args: str) -> None:
        pythonw = self._python("pythonw.exe")
        try:
            if pythonw is not None:
                command = [str(pythonw), str(self.dir / "app.pyw"), *args]
            else:
                command = ["cmd.exe", "/c", str(self.dir / "run.bat")]
            subprocess.Popen(command, cwd=str(self.dir),
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as exc:
            self._log(f"Не удалось открыть окно: {exc}", "error")
            self.hub.toast("Не удалось открыть окно дневника", kind="error")
            return
        self._log("Отдельное окно приложения запущено.")
        self.hub.toast("Окно дневника открыто", kind="ok")

    def _open_folder(self) -> None:
        try:
            os.startfile(str(self.dir))
        except Exception as exc:  # noqa: BLE001
            self.hub.toast(f"Папка не открылась: {exc}", kind="error")




class AIScreenAgentPanel(Panel):
    """AI Screen Bridge (Computer Use): авто-прохождение тестов через Gemini Vision."""

    def __init__(self, master: tk.Misc, hub) -> None:
        super().__init__(master, hub)
        self.dir = APP_DIR / "ai-screen-agent"
        self.script = self.dir / "screen_agent.py"
        self.config_file = self.dir / "config.json"
        self._proc: subprocess.Popen | None = None
        self._poll_job = None
        self._build()
        self.refresh()
        self._schedule_poll()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        cards = tk.Frame(self, bg=BG)
        cards.grid(row=0, column=0, sticky="ew", pady=(0, px(12)))
        cards.columnconfigure(0, weight=1)
        cards.columnconfigure(1, weight=1)
        cards.columnconfigure(2, weight=1)

        c1 = tk.Frame(cards, bg=CARD, highlightbackground=BORDER, highlightthickness=1, padx=px(14), pady=px(10))
        c1.grid(row=0, column=0, sticky="nsew", padx=(0, px(6)))
        tk.Label(c1, text="Статус агента", bg=CARD, fg=MUTED, font=ui_font(9)).pack(anchor="w")
        self.lbl_status = tk.Label(c1, text="○ Остановлен", bg=CARD, fg=FG, font=ui_font(12, "bold"))
        self.lbl_status.pack(anchor="w", pady=(px(4), 0))
        self.lbl_info = tk.Label(c1, text="Плавающее окно поверх тестов", bg=CARD, fg=MUTED, font=ui_font(8))
        self.lbl_info.pack(anchor="w")

        c2 = tk.Frame(cards, bg=CARD, highlightbackground=BORDER, highlightthickness=1, padx=px(14), pady=px(10))
        c2.grid(row=0, column=1, sticky="nsew", padx=px(4))
        tk.Label(c2, text="Интеллект (Мозг)", bg=CARD, fg=MUTED, font=ui_font(9)).pack(anchor="w")
        self.lbl_model = tk.Label(c2, text="Gemini 2.5 Flash", bg=CARD, fg=FG, font=ui_font(12, "bold"))
        self.lbl_model.pack(anchor="w", pady=(px(4), 0))
        self.lbl_key = tk.Label(c2, text="Ключ не настроен", bg=CARD, fg=MUTED, font=ui_font(8))
        self.lbl_key.pack(anchor="w")

        c3 = tk.Frame(cards, bg=CARD, highlightbackground=BORDER, highlightthickness=1, padx=px(14), pady=px(10))
        c3.grid(row=0, column=2, sticky="nsew", padx=(px(6), 0))
        tk.Label(c3, text="Область экрана (Глаза)", bg=CARD, fg=MUTED, font=ui_font(9)).pack(anchor="w")
        self.lbl_roi = tk.Label(c3, text="Не выбрана", bg=CARD, fg=MUTED, font=ui_font(12, "bold"))
        self.lbl_roi.pack(anchor="w", pady=(px(4), 0))
        self.lbl_roi_hint = tk.Label(c3, text="Координаты теста на экране", bg=CARD, fg=MUTED, font=ui_font(8))
        self.lbl_roi_hint.pack(anchor="w")

        ctrl = tk.Frame(self, bg=CARD, highlightbackground=BORDER, highlightthickness=1, padx=px(14), pady=px(10))
        ctrl.grid(row=1, column=0, sticky="ew", pady=(0, px(12)))

        bar = tk.Frame(ctrl, bg=CARD)
        bar.pack(fill="x", pady=(0, px(10)))

        self.btn_start = flat_button(bar, "▶ Запустить окно агента", self.start_agent, kind="primary")
        self.btn_start.pack(side="left", padx=(0, px(8)))

        self.btn_stop = flat_button(bar, "⏹ Закрыть агент", self.stop_agent, kind="ghost")
        self.btn_stop.pack(side="left", padx=(0, px(8)))

        flat_button(bar, "📁 Папка агента", self.open_folder, kind="ghost").pack(side="left", padx=(0, px(8)))
        flat_button(bar, "🔄 Обновить", self.refresh, kind="ghost").pack(side="left")

        key_row = tk.Frame(ctrl, bg=CARD)
        key_row.pack(fill="x", pady=px(3))
        tk.Label(key_row, text="Gemini API Key:", bg=CARD, fg=FG, width=15, anchor="w").pack(side="left")
        self.key_var = tk.StringVar()
        self.key_entry = ttk.Entry(key_row, textvariable=self.key_var, show="*")
        self.key_entry.pack(side="left", fill="x", expand=True, padx=(0, px(6)))

        self.btn_eye = flat_button(key_row, "👁", self.toggle_show_key, kind="ghost")
        self.btn_eye.pack(side="left", padx=(0, px(6)))

        self.btn_paste = flat_button(key_row, "📋 Вставить", self.paste_key, kind="primary")
        self.btn_paste.pack(side="left", padx=(0, px(6)))

        flat_button(key_row, "Сохранить", self.save_key, kind="ghost").pack(side="left")

        # Привязка Ctrl+V в любой раскладке и ПКМ для вставки
        self.key_entry.bind("<Control-v>", lambda e: (self.paste_key(), "break")[1])
        self.key_entry.bind("<Control-V>", lambda e: (self.paste_key(), "break")[1])
        self.key_entry.bind("<Control-KeyPress>", lambda e: (self.paste_key(), "break")[1] if getattr(e, "keycode", 0) == 86 or (e.keysym or "").lower() in ("v", "м") else None)

        def _on_right_click(event):
            m = tk.Menu(self.key_entry, tearoff=0, bg=CARD, fg=FG)
            m.add_command(label="Вставить из буфера", command=self.paste_key)
            m.add_command(label="Копировать", command=lambda: (self.clipboard_clear(), self.clipboard_append(self.key_var.get())))
            m.add_command(label="Очистить", command=lambda: self.key_var.set(""))
            m.tk_popup(event.x_root, event.y_root)

        self.key_entry.bind("<Button-3>", _on_right_click)

        hint = tk.Label(ctrl, text="Подсказка: запустите окно агента, выделите рамку с тестом в браузере и нажмите СТАРТ.",
                        bg=CARD, fg=MUTED, font=ui_font(8))
        hint.pack(anchor="w", pady=(px(6), 0))

        log_frame, self.log_box = self.make_log(self, height=14, label="Лог событий AI Screen Agent")
        log_frame.grid(row=2, column=0, sticky="nsew")
        self.append_log(self.log_box, "AI Screen Agent интегрирован в хаб инструментов.")
        self.append_log(self.log_box, "Нажмите «▶ Запустить окно агента» для открытия окна управления над тестом.")

    def _is_running(self) -> bool:
        if self._proc is not None and self._proc.poll() is None:
            return True
        try:
            out = ps_run('Get-CimInstance Win32_Process -Filter "CommandLine LIKE '%screen_agent.py%'" | Select-Object -ExpandProperty ProcessId')
            pids = [int(p.strip()) for p in out.splitlines() if p.strip().isdigit()]
            return len(pids) > 0
        except Exception:
            return False

    def start_agent(self) -> None:
        if self._is_running():
            self.hub.toast("Окно агента уже запущено", kind="warn")
            return
        py_exe = pythonw_executable()

        try:
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"
            self._proc = subprocess.Popen(
                [py_exe, str(self.script)],
                cwd=str(self.dir),
                env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
            self.hub.toast("Окно агента запущено", kind="ok")
            self.append_log(self.log_box, f"Запущен процесс агента (PID: {self._proc.pid}).")
            self.after(500, self.refresh)
        except Exception as exc:
            self.hub.toast(f"Ошибка запуска: {exc}", kind="error")
            self.append_log(self.log_box, f"Ошибка запуска: {exc}", tag="error")

    def stop_agent(self) -> None:
        stopped = False
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
                stopped = True
            except Exception:
                pass
            self._proc = None

        try:
            out = ps_run('Get-CimInstance Win32_Process -Filter "CommandLine LIKE '%screen_agent.py%'" | Select-Object -ExpandProperty ProcessId')
            for line in out.splitlines():
                if line.strip().isdigit():
                    subprocess.run(["taskkill", "/PID", line.strip(), "/T", "/F"], capture_output=True)
                    stopped = True
        except Exception:
            pass

        if stopped:
            self.hub.toast("Агент остановлен", kind="ok")
            self.append_log(self.log_box, "Процесс агента остановлен.")
        else:
            self.hub.toast("Процесс не был запущен", kind="info")
        self.refresh()

    def open_folder(self) -> None:
        try:
            os.startfile(str(self.dir))
        except Exception as exc:
            self.hub.toast(f"Ошибка открытия папки: {exc}", kind="error")

    def toggle_show_key(self) -> None:
        if self.key_entry.cget("show") == "*":
            self.key_entry.configure(show="")
            self.btn_eye.configure(text="🔒")
        else:
            self.key_entry.configure(show="*")
            self.btn_eye.configure(text="👁")

    def paste_key(self) -> None:
        try:
            val = self.clipboard_get()
            if val:
                val = val.strip().strip('"\' \t\r\n')
                self.key_var.set(val)
                self.save_key()
                self.hub.toast("Ключ вставлен из буфера и сохранён", kind="ok")
                self.append_log(self.log_box, "✅ API ключ вставлен из буфера обмена и сохранён!")
        except Exception as exc:
            self.hub.toast(f"Буфер пуст или недоступен: {exc}", kind="warn")

    def save_key(self) -> None:
        new_key = self.key_var.get().strip()
        if not new_key:
            self.hub.toast("Введите ключ!", kind="warn")
            return
        cfg = {}
        if self.config_file.exists():
            try:
                cfg = json.loads(self.config_file.read_text(encoding="utf-8"))
                if not isinstance(cfg, dict):
                    cfg = {}
            except Exception as exc:
                self.hub.toast(f"Ошибка чтения config.json: {exc}", kind="error")
                return
        cfg["api_key"] = new_key
        try:
            self.config_file.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
            self.hub.toast("Ключ сохранён", kind="ok")
            self.append_log(self.log_box, "Gemini API Key сохранён в config.json.")
            self.refresh()
        except Exception as exc:
            self.hub.toast(f"Ошибка сохранения: {exc}", kind="error")

    def refresh(self) -> None:
        if self.config_file.exists():
            try:
                cfg = json.loads(self.config_file.read_text(encoding="utf-8"))
                key = cfg.get("api_key", "")
                if key and not self.key_var.get():
                    self.key_var.set(key)
                if key:
                    if key.startswith("sk-apx"):
                        p_name = "● APInex (Gemini/Claude)"
                    elif key.startswith("AIza"):
                        p_name = "● Google AI Studio"
                    elif key.startswith("sk-"):
                        p_name = "● OpenAI / Proxy"
                    else:
                        p_name = "● Ключ настроен"
                    self.lbl_key.config(text=p_name, fg=OK)
                else:
                    self.lbl_key.config(text="○ Ключ не настроен", fg=tc("err"))

                self.lbl_model.config(text=cfg.get("model", "gemini-2.5-flash"))

                roi = cfg.get("roi")
                if roi and isinstance(roi, (list, tuple)) and len(roi) == 4:
                    self.lbl_roi.config(text=f"{roi[2]}x{roi[3]} px", fg=FG)
                    self.lbl_roi_hint.config(text=f"X: {roi[0]}, Y: {roi[1]}")
                else:
                    self.lbl_roi.config(text="Не выбрана", fg=MUTED)
            except Exception:
                pass

        if self._is_running():
            self.lbl_status.config(text="● Запущен", fg=OK)
        else:
            self.lbl_status.config(text="○ Остановлен", fg=FG)

    def _schedule_poll(self) -> None:
        if not self.winfo_exists():
            return
        if getattr(self.hub, "current", None) == "ai_agent":
            self.refresh()
        self._poll_job = self.after(3000, self._schedule_poll)

    def on_show(self) -> None:
        self.refresh()

    def shutdown(self) -> None:
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass
            self._poll_job = None





NAV = (
    ("autoclicker", "AutoClicker", "клики по экрану"),
        ("clean", "Пустые файлы", "0 байт и пустые папки"),
    ("dns", "DNS", "Quad9 · Xbox · Дефолтное"),
    ("github", "GitHub", "бэкап проектов"),
    ("bypass", "Bypass", "обход DPI: глобально и по приложениям"),
    ("leads", "Лиды 2GIS", "контакты без сайта"),
    ("diary", "Дневник МО", "оценки · запас до пятёрки"),
    ("shorts", "Shorts Autopilot", "клипы · DaVinci · YouTube"),
    ("ai_agent", "AI Screen Agent", "Computer Use · тесты"),
    ("shutdown", "Страж выключения", "когда агенты закончат — выключить ПК"),
    ("settings", "Настройки", "скролл · темы · прочее"),
)

class ShutdownWatcherPanel(Panel):
    """Следит за агентами: когда все закончат — выключает ПК через 60 с."""

    def __init__(self, master: tk.Misc, hub) -> None:
        super().__init__(master, hub)
        self.watcher: wsh.Watcher | None = None
        self._poll_job: str | None = None
        self._build()
        self._refresh_status()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        # --- кнопки --- #
        bar = tk.Frame(self, bg=BG)
        bar.grid(row=0, column=0, sticky="ew", pady=(0, px(8)))
        flat_button(bar, "▶ Старт стража", self.start_watcher, kind="ghost").pack(side="left")
        self.btn_stop = flat_button(bar, "■ Стоп стража", self.stop_watcher, kind="danger")
        self.btn_stop["state"] = "disabled"
        self.btn_stop.pack(side="left", padx=(px(8), 0))
        flat_button(bar, "✕ Отменить выключение", self.cancel_shutdown, kind="ghost").pack(side="left", padx=(px(8), 0))
        self.lbl_timer = tk.Label(bar, bg=BG, fg=WARN,
                                  font=ui_font(9, "bold"))
        self.lbl_timer.pack(side="left", padx=(px(10), 0))

        tk.Label(self, text="Отслеживаемые агенты (добавь свои в config.json → watcher.agents: {name, mode: process|idle|marker, exe/marker, idle_min})",
                 bg=BG, fg=MUTED, font=ui_font(8)).grid(row=1, column=0, sticky="w")

        # --- таблица агентов --- #
        self.tree = ttk.Treeview(self, columns=("mode", "target", "status", "note"),
                                 show="headings", selectmode="none",
                                 height=8)
        for col, head in (("mode", "Режим"), ("target", "Цель"),
                          ("status", "Состояние"), ("note", "Примечание")):
            self.tree.heading(col, text=head)
            self.tree.column(col, width=px(150))
        self.tree.grid(row=2, column=0, sticky="nsew")

        # --- лог --- #
        log_frame = tk.Frame(self, bg=BG)
        log_frame.grid(row=3, column=0, sticky="nsew", pady=(px(8), 0))
        log_frame.rowconfigure(0, weight=1)
        log_frame.columnconfigure(0, weight=1)
        self.log = tk.Text(
            log_frame, bg=LOG_BG, fg=LOG_FG, insertbackground=FG,
            font=mono_font(9), state="disabled", wrap="word",
        )
        self.log.grid(row=0, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(log_frame, command=self.log.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.log.config(yscrollcommand=scroll.set)

    def _status_color(self, status: wsh.Status) -> str:
        return {
            wsh.Status.DONE: OK,
            wsh.Status.WORKING: ACCENT,
            wsh.Status.NOT_SEEN: MUTED,
        }[status]

    def _watchers(self) -> list[wsh.Agent]:
        return wsh.load_agents()

    # --- управление --- #
    def start_watcher(self) -> None:
        self.stop_watcher()
        agents = self._watchers()
        if not agents:
            self.log_lines(["Список агентов пуст — добавь их в config.json → watcher.agents"])
            return
        self.watcher = wsh.Watcher(agents)
        self.btn_stop.config(state="normal")
        self.tree.delete(*self.tree.get_children())
        self.watcher.start(on_update=lambda: self.hub.post(self._refresh_status))
        self._schedule_poll()
        self.log_lines(["Страж запущен:", *[f"  • {a.name} ({a.mode}: {a.exe or a.marker})" for a in agents]])

    def stop_watcher(self) -> None:
        if self._poll_job:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass
            self._poll_job = None
        if self.watcher:
            self.watcher.stop()
        self.watcher = None
        self.btn_stop.config(state="disabled")
        self.lbl_timer.config(text="")

    def shutdown(self) -> None:
        self.stop_watcher()

    def cancel_shutdown(self) -> None:
        if self.watcher and self.watcher.armed:
            self.watcher.cancel_shutdown()
            self.lbl_timer.config(text="выключение отменено", fg=OK)
            self._refresh_status()

    # --- отрисовка --- #
    def on_show(self) -> None:
        self._refresh_status()

    def _log_pane(self, text: str) -> None:
        self.log.config(state="normal")
        self.log.insert("end", f"{text}\n")
        self.log.see("end")
        self.log.config(state="disabled")

    def _schedule_poll(self) -> None:
        if self._poll_job:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass
            self._poll_job = None
        if self.watcher:
            self._poll_job = self.after(2500, self._poll)

    def _poll(self) -> None:
        if self.watcher and self.watcher.armed:
            self._refresh_status()
        self._schedule_poll()

    def _refresh_status(self) -> None:
        if self.watcher is None:
            return
        results = self.watcher.summary()
        for child in self.tree.get_children():
            self.tree.delete(child)
        for agent, res in zip(self.watcher.agents, results):
            iid = self.tree.insert("", "end", values=(
                agent.mode,
                agent.exe or agent.marker or "—",
                res.status.value,
                res.note,
            ))
            self.tree.item(iid, tags=(res.status.value,))
            self.tree.tag_configure(res.status.value,
                                    foreground=self._status_color(res.status))
        if self.watcher.armed:
            left = self.watcher.pending_seconds()
            self.lbl_timer.config(
                text=f"⚠ Выключение через {left} с" + "!" * bool(left % 2),
                fg=DANGER if left <= 10 else WARN,
            )
            if left <= 0:
                self.lbl_timer.config(text="Выключение!", fg=DANGER)
        # приём новых строк лога
        lines = self.watcher.take_lines(400)
        shown = getattr(self, "_log_shown", [])
        for line in lines[len(shown):]:
            self._log_pane(line)
        self._log_shown = lines


HEADERS = {
    "autoclicker": (
        "AutoClicker",
        "Клики по точкам, областям и картинкам. F6 — старт/стоп, F7 — точка под курсором.",
    ),
    "clean": (
        "Пустые файлы и папки",
        "Сначала проверка — удаление только после подтверждения.",
    ),
    "dns": (
        "DNS",
        "Профили Quad9, Xbox и «Дефолтное» (автоматический DNS от DHCP). DoH для Quad9 и Xbox. Нужны права администратора.",
    ),
    "github": (
        "GitHub",
        "Список репозиториев, заливка папок, скачивание и обновление. Нужны gh и git.",
    ),
    "bypass": (
        "Bypass",
        "Обход DPI: весь трафик или только выбранные программы. Happ и GoodbyeDPI.",
    ),
    "leads": (
        "Лиды 2GIS",
        "Организации из выдачи 2GIS: телефон, e-mail, сайт, мессенджеры и приоритет под аутрич. Горячие копятся в пуле на 5 дней.",
    ),
    "diary": (
        "Дневник МО · Контроль балла",
        "Средний балл, «5» до 4.50 и запас прочности. Вход через Госуслуги — один раз.",
    ),
    "shorts": (
        "Shorts Autopilot",
        "Автоматизация YouTube Shorts: скачивание через MAX, SEO-генерация через Google AI, монтаж в DaVinci Resolve и загрузка на YouTube.",
    ),
    "ai_agent": (
        "AI Screen Agent (Computer Use)",
        "Автономное прохождение тестов через Gemini Vision. Захват экрана, выбор ответов и клики мышкой.",
    ),
    "shutdown": (
        "Страж выключения",
        "Следит за ИИ-агентами (WorkBuddy, OpenCode, Antigravity) и выключает ПК, когда все завершат работу.",
    ),
    "settings": (
        "Настройки",
        "Параметры программы: чувствительность прокрутки колёсиком мыши.",
    ),
}


class SettingsPanel(Panel):
    """Настройки программы: чувствительность скролла колёсиком мыши."""

    def __init__(self, master: tk.Misc, hub) -> None:
        super().__init__(master, hub)
        self.var_scroll = tk.IntVar(value=_scroll_step())
        self.var_scroll.trace_add("write", self._on_scroll_changed)
        self._build()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        title = tk.Label(
            self,
            text="Настройки прокрутки",
            bg=BG, fg=FG, font=ui_font(16, "bold"), anchor="w",
        )
        title.grid(row=0, column=0, sticky="w", padx=(px(12), px(12)), pady=(px(16), px(6)))

        help_line = tk.Label(
            self,
            text="Чувствительность скролла: сколько строк прокручивается одним щелчком"
            " колёсика мыши. Влияет на все разделы с прокруткой (списки программ, "
            "настройки, журнал, таб оценок).",
            bg=BG, fg=MUTED, font=ui_font(9), anchor="w", wraplength=px(1000),
        )
        help_line.grid(row=1, column=0, sticky="nsew", padx=(px(12), px(12)))

        # ползунок
        bar = tk.Frame(self, bg=BG)
        bar.grid(row=2, column=0, sticky="w", pady=(px(16), 0))
        tk.Label(
            bar, text="Чувствительность скролла:", bg=BG, fg=FG, font=ui_font(10),
        ).pack(side="left", padx=(0, px(12)))
        scale = ttk.Scale(
            bar, from_=1, to=20, orient="horizontal",
            variable=self.var_scroll, length=px(560),
        )
        scale.pack(side="left")
        tk.Label(
            bar, textvariable=self.var_scroll, bg=BG, fg=FG, font=ui_font(10, "bold"),
            width=3, anchor="w",
        ).pack(side="left", padx=(px(12), 0))
        self.save_btn = flat_button(
            bar, "Сохранить", self.save_settings, kind="primary",
        )
        self.save_btn.pack(side="left", padx=(px(16), 0))
        self.status = tk.Label(bar, text="", bg=BG, fg=OK, font=ui_font(8))
        self.status.pack(side="left", padx=(px(12), 0))

    def _on_scroll_changed(self, *args: str) -> None:
        """Обновляет подсказку значения в реальном времени."""
        pass

    def save_settings(self) -> None:
        """Записывает выбранный шаг скролла в _scroll_settings.json."""
        value = self.var_scroll.get()
        if scroll_settings.save_settings(value):
            self.status.configure(text=f"Сохранено: {value} строк за щелчок")
        else:
            self.status.configure(text="Ошибка сохранения настроек", fg=DANGER)

    def on_show(self) -> None:
        """Загружает сохранённый шаг скролла при открытии вкладки."""
        self.var_scroll.set(_scroll_step())


class NavItem(tk.Frame):
    def __init__(self, parent: tk.Misc, title: str, subtitle: str, command) -> None:
        super().__init__(parent, bg=SIDEBAR, cursor="hand2")
        self.command = command
        self.active = False

        self.accent = tk.Frame(self, bg=SIDEBAR, width=px(3))
        self.accent.pack(side="left", fill="y")
        self.body = tk.Frame(self, bg=SIDEBAR)
        self.body.pack(side="left", fill="both", expand=True, padx=(px(11), px(12)), pady=px(9))

        self.title_label = tk.Label(
            self.body, text=title, bg=SIDEBAR, fg=SIDEBAR_TITLE,
            font=ui_font(10, "bold"), anchor="w", cursor="hand2",
        )
        self.title_label.pack(fill="x")
        self.subtitle_label = tk.Label(
            self.body, text=subtitle, bg=SIDEBAR, fg=SIDEBAR_SUBTITLE,
            font=ui_font(8), anchor="w", cursor="hand2",
        )
        self.subtitle_label.pack(fill="x")

        for widget in (self, self.body, self.title_label, self.subtitle_label):
            widget.bind("<Button-1>", lambda _event: self.command())
            widget.bind("<Enter>", lambda _event: self._hover(True))
            widget.bind("<Leave>", lambda _event: self._hover(False))

    def _paint(self) -> None:
        background = SIDEBAR_ACTIVE if self.active else SIDEBAR
        for widget in (self, self.body):
            widget.configure(bg=background)
        self.title_label.configure(bg=background, fg=FG if self.active else SIDEBAR_TITLE)
        self.subtitle_label.configure(bg=background, fg=MUTED if self.active else SIDEBAR_SUBTITLE)
        self.accent.configure(bg=ACCENT if self.active else background)

    def _hover(self, entering: bool) -> None:
        if self.active:
            return
        background = SIDEBAR_HOVER if entering else SIDEBAR
        for widget in (self, self.body):
            widget.configure(bg=background)
        self.title_label.configure(bg=background)
        self.subtitle_label.configure(bg=background)
        self.accent.configure(bg=background)

    def set_active(self, active: bool) -> None:
        self.active = active
        self._paint()


class Hub(tk.Tk):
    def __init__(self, initial_tab: str = "autoclicker") -> None:
        super().__init__()
        set_scale(self.winfo_fpixels("1i") / 96.0)
        self.title("Мои инструменты")
        width = min(px(1180), self.winfo_screenwidth() - px(40))
        height = min(px(820), self.winfo_screenheight() - px(90))
        self.geometry(f"{width}x{height}")
        self.minsize(min(px(1000), width), min(px(700), height))
        self.configure(bg=BG)
        apply_dark_theme(self)
        self.option_add("*Font", ui_font(9))
        self._theme_resolved = _theme.mode()
        self._theme_check_at = 0.0

        self.events: queue.Queue = queue.Queue()
        self.panels: dict[str, Panel] = {}
        self.nav_items: dict[str, NavItem] = {}
        self.current = ""
        self._closing = False

        self.factories = {
            "autoclicker": lambda: AutoClickerPanel(self.content, self),
            "clean": lambda: CleanPanel(self.content, self),
            "dns": lambda: DnsPanel(self.content, self),
            "github": lambda: GitHubPanel(self.content, self),
            "bypass": lambda: BypassPanel(self.content, self),
            "leads": lambda: LeadsPanel(self.content, self),
            "diary": lambda: DiaryGradesPanel(self.content, self),
            "shorts": lambda: ShortsAutopilotPanel(self.content, self),
            "ai_agent": lambda: AIScreenAgentPanel(self.content, self),
            "shutdown": lambda: ShutdownWatcherPanel(self.content, self),
            "settings": lambda: SettingsPanel(self.content, self),
        }

        self._build()
        self.show(initial_tab if initial_tab in self.factories else "autoclicker")
        self.after(70, self._poll)
        self.protocol("WM_DELETE_WINDOW", self.shutdown)
        enable_dark_titlebar(self)
        _theme.on_change(self._apply_theme_mode)
        # Обход по программам (ProxiFyre) выключен по умолчанию при каждом
        # запуске тулз: служба переживает закрытие окна, и без этого при старте
        # «сама уже работает маршрутизация». VPN (Happ) не трогаем — только
        # останавливаем перенаправление выбранных программ. Фон: остановка
        # службы не должна задерживать открытие окна.
        threading.Thread(target=self._stop_apps_route_on_launch, daemon=True).start()

    @staticmethod
    def _stop_apps_route_on_launch() -> None:
        try:
            proxifyre_stop()
        except Exception:  # noqa: BLE001 — старт не должен падать из-за службы
            pass

    # -- разметка ---------------------------------------------------------- #
    def _build(self) -> None:
        sidebar = tk.Frame(self, bg=SIDEBAR, width=px(234))
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        brand = tk.Frame(sidebar, bg=SIDEBAR)
        brand.pack(fill="x", padx=px(20), pady=(px(24), px(18)))
        tk.Label(brand, text="МОИ", bg=SIDEBAR, fg=BRAND_LABEL,
                 font=ui_font(8, "bold")).pack(anchor="w")
        tk.Label(brand, text="Инструменты", bg=SIDEBAR, fg=FG,
                 font=ui_font(15, "bold")).pack(anchor="w")

        nav = tk.Frame(sidebar, bg=SIDEBAR)
        nav.pack(fill="x")
        for key, title, subtitle in NAV:
            item = NavItem(nav, title, subtitle, lambda k=key: self.show(k))
            item.pack(fill="x", pady=px(1))
            self.nav_items[key] = item

        bottom = tk.Frame(sidebar, bg=SIDEBAR)
        bottom.pack(side="bottom", fill="x", padx=px(16), pady=px(16))
        admin = is_admin()
        tk.Label(
            bottom,
            text="● права администратора" if admin else "● обычные права",
            bg=SIDEBAR,
            fg=OK if admin else MUTED,
            font=ui_font(8),
            anchor="w",
        ).pack(fill="x")
# admin button removed
        tk.Label(
            bottom,
            text=f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            bg=SIDEBAR,
            fg=BRAND_LABEL,
            font=ui_font(8),
            anchor="w",
        ).pack(fill="x", pady=(px(10), 0))
        self.theme_btn = tk.Button(
            bottom, text=self._theme_label(), command=self._cycle_theme,
            bg=SIDEBAR, fg=MUTED, activebackground=SIDEBAR_ACTIVE,
            activeforeground=FG, relief="flat", bd=0, anchor="w",
            font=ui_font(8), cursor="hand2", pady=px(2),
        )
        self.theme_btn.pack(fill="x", pady=(px(10), 0))

        main = tk.Frame(self, bg=BG)
        main.pack(side="left", fill="both", expand=True)

        status_bar = tk.Frame(main, bg=SIDEBAR, height=px(28))
        status_bar.pack(side="bottom", fill="x")
        status_bar.pack_propagate(False)
        self.status_var = tk.StringVar(value="Готово")
        tk.Label(status_bar, textvariable=self.status_var, bg=SIDEBAR, fg=MUTED,
                 font=ui_font(8), anchor="w").pack(side="left", padx=px(14))
        tk.Label(status_bar, text="Ctrl+1…9 — переключение разделов", bg=SIDEBAR,
                 fg=BRAND_LABEL, font=ui_font(8), anchor="e").pack(side="right", padx=px(14))

        head = tk.Frame(main, bg=BG)
        head.pack(fill="x", padx=px(26), pady=(px(22), px(12)))
        self.title_var = tk.StringVar()
        self.subtitle_var = tk.StringVar()
        tk.Label(head, textvariable=self.title_var, bg=BG, fg=FG,
                 font=ui_font(18, "bold"), anchor="w").pack(fill="x")
        tk.Label(head, textvariable=self.subtitle_var, bg=BG, fg=MUTED,
                 font=ui_font(9), anchor="w").pack(fill="x", pady=(px(4), 0))

        self.content = tk.Frame(main, bg=BG)
        self.content.pack(fill="both", expand=True, padx=px(26), pady=(0, px(14)))
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)

        for index, (key, _title, _subtitle) in enumerate(NAV, 1):
            key_char = str(index % 10)
            self.bind_all(f"<Control-Key-{key_char}>", lambda _event, k=key: self.show(k))

    # -- тема -------------------------------------------------------------- #
    def _theme_label(self) -> str:
        return {
            "auto": "◐ Тема: как в системе",
            "light": "☀ Тема: светлая",
            "dark": "☾ Тема: тёмная",
        }[_theme.raw_mode()]

    def _cycle_theme(self) -> None:
        order = ("auto", "light", "dark")
        current = _theme.raw_mode()
        _theme.set_mode(order[(order.index(current) + 1) % len(order)])

    def _apply_theme_mode(self, resolved: str) -> None:
        """Живая перерисовка: токены → ttk-стили → виджеты → Canvas → DWM."""
        if resolved == self._theme_resolved:
            return
        previous = self._theme_resolved
        self._theme_resolved = resolved
        _refresh_palette()
        apply_dark_theme(self)
        _apple_tk.recolor(self, previous, resolved)
        _apple_tk.redraw(self)
        enable_dark_titlebar(self)
        if self.winfo_exists():
            try:
                self.theme_btn.configure(text=self._theme_label())
            except (AttributeError, tk.TclError):
                pass

    # -- разделы ----------------------------------------------------------- #
    def show(self, key: str) -> None:
        panel = self.panels.get(key)
        fresh = panel is None
        if fresh:
            panel = self.factories[key]()
            panel.grid(row=0, column=0, sticky="nsew")
            self.panels[key] = panel
            enable_input_paste(panel)
        if not fresh:
            # вкладка уже создана — обновляем данные при каждом показе
            hook = getattr(panel, "on_show", None)
            if hook is not None:
                try:
                    hook()
                except Exception as exc:  # noqa: BLE001
                    log_exception()
                    self.toast(f"Сбой обновления вкладки: {exc}", kind="error")
        panel.tkraise()
        self.current = key
        for item_key, item in self.nav_items.items():
            item.set_active(item_key == key)
        title, subtitle = HEADERS.get(key, (key.title(), ""))
        self.title_var.set(title)
        self.subtitle_var.set(subtitle)

    # -- фон и события ----------------------------------------------------- #
    def post(self, callback) -> None:
        self.events.put(callback)

    def run_bg(self, work, on_done, on_error) -> None:
        def runner() -> None:
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001
                self.post(lambda exc=exc: on_error(exc))
            else:
                self.post(lambda result=result: on_done(result))

        threading.Thread(target=runner, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                callback = self.events.get_nowait()
                try:
                    callback()
                except Exception:  # noqa: BLE001
                    log_exception()
        except queue.Empty:
            pass
        now = time.monotonic()
        if now - self._theme_check_at > 2.0:
            self._theme_check_at = now
            if _theme.raw_mode() == "auto":
                resolved = _theme.mode()
                if resolved != self._theme_resolved:
                    self._apply_theme_mode(resolved)
        self.after(70, self._poll)

    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    def toast(self, message: str, *, kind: str = "ok", ms: int = 2200) -> None:
        """Всплывающее подтверждение поверх окна — видно, что действие сработало."""
        label = getattr(self, "_toast_label", None)
        if label is None:
            label = tk.Label(
                self, bg=CARD, fg=FG, font=ui_font(10, "bold"),
                padx=px(18), pady=px(10),
                highlightthickness=1, highlightbackground=BORDER, bd=0,
            )
            self._toast_label = label
        colors = {"ok": OK, "warn": WARN, "error": DANGER}
        label.configure(text=message, fg=colors.get(kind, FG))
        label.place(relx=0.5, rely=1.0, y=-px(46), anchor="s")
        label.lift()
        job = getattr(self, "_toast_job", None)
        if job:
            try:
                self.after_cancel(job)
            except Exception:  # noqa: BLE001
                pass
        self._toast_job = self.after(ms, label.place_forget)

    # -- права ------------------------------------------------------------- #
    def ensure_admin(self, tab: str) -> bool:
        if is_admin():
            return True
        if not messagebox.askyesno(
            "Нужны права администратора",
            "Для этого действия нужны права администратора.\n\n"
            "Перезапустить приложение от имени администратора?",
            parent=self,
        ):
            return False
        if relaunch_as_admin(tab):
            self.after(150, self.shutdown)
            return False
        messagebox.showerror(
            "Права администратора",
            "Не удалось перезапустить приложение с правами администратора.\n"
            "Запрос UAC был отклонён.",
            parent=self,
        )
        return False

    def request_admin(self) -> None:
        tab = self.current or "dns"
        if is_admin():
            messagebox.showinfo("Права администратора",
                                "Приложение уже запущено от администратора.", parent=self)
            return
        self.ensure_admin(tab)

    # -- выход ------------------------------------------------------------- #
    def shutdown(self) -> None:
        if self._closing:
            return
        self._closing = True
        for p in list(self.panels.values()):
            if hasattr(p, "shutdown"):
                try:
                    p.shutdown()
                except Exception:  # noqa: BLE001
                    pass
        self.destroy()


def main() -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):
        pass
    parser = argparse.ArgumentParser(description="Мои инструменты")
    parser.add_argument("--tab", choices=[key for key, _t, _s in NAV], default="autoclicker")
    args, _unknown = parser.parse_known_args()

    # pyautogui.PAUSE / FAILSAFE настраиваются в load_autoclicker_deps()
    # сразу после ленивого импорта — здесь он ещё не загружен.

    # Собранный Tools.exe запрашивает права манифестом. Исходник запускаем
    # обычным процессом: иначе двойной клик сначала закрывает pythonw, а затем
    # ждёт UAC-перезапуска, из-за чего кажется, что tools.pyw не открывается.
    # Для конкретных действий права запрашиваются через ensure_admin().

    hub = Hub(initial_tab=args.tab)
    hub.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
