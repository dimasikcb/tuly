#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Общий UI-слой хаба: тема, палитра-токены, базовая панель, диалоги.

Что здесь живёт и почему:
  * палитра-токены (BG/CARD/FG/…) — HEX-цвета живут ТОЛЬКО в theme/tokens.py,
    здесь тонкая прослойка старых имён, чтобы не переписывать сотни упоминаний;
  * механизм «потребителей палитры»: смена темы вызывает _refresh_palette(),
    которая обновляет глобалы uibase И всех зарегистрированных модулей
    (см. register_palette_consumer). Панельные модули продолжают читать голые
    имена токенов (BG, CARD, …) — реестр сам держит их актуальными;
  * Panel — базовый класс всех панелей хаба (hub-мост: run_bg/set_status/toast/post);
  * DarkMessageBox — тёмные модальные диалоги вместо светлых messagebox;
  * обёртки над theme.apple_tk (flat_button/dark_check/dark_text) и утилиты
    ввода (Ctrl+V/C/X/A в любой раскладке).

Модуль не знает ни об одной конкретной панели — только tools.pyw (Hub)
собирает их вместе. tkinter разрешён: это GUI-слой.
"""

from __future__ import annotations

import ctypes
import sys
import time
from pathlib import Path

import tkinter as tk
from tkinter import ttk

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import theme as _theme
from theme import apple_tk as _apple_tk

SCALE = 1.0

_LEGACY_NAMES = (
    "BG", "SIDEBAR", "CARD", "CARD_HOVER", "FIELD", "BORDER", "FG", "MUTED",
    "ACCENT", "ACCENT_HOVER", "DANGER", "DANGER_HOVER", "OK", "WARN",
    "LOG_BG", "LOG_FG", "COPIED_BG",
)
_EXTRA_ROLES = (
    "SIDEBAR_ACTIVE", "SIDEBAR_HOVER", "SIDEBAR_TITLE", "SIDEBAR_SUBTITLE",
    "BRAND_LABEL", "TRACK_OFF", "TRACK_DISABLED", "KNOB", "ON_AVATAR",
    "ROW_SELECTED", "BADGE_NEUTRAL_BG", "BADGE_BAD_BG", "BADGE_BAD_INK",
    "BADGE_WARN_BG", "BADGE_WARN_INK", "BADGE_GOOD_BG", "BADGE_GOOD_INK",
    "OVERLAY_TIMER", "TAG_FILE", "TAG_DIR", "OVERLAY_BACKDROP",
)

# Реестр «потребителей палитры»: модульные globals()-словари, которые надо
# обновлять при смене темы. uibase регистрирует свои первым.
_PALETTE_CONSUMERS: list[dict] = []


def tc(role: str) -> str:
    """Цвет по семантической роли из theme/tokens.py."""
    return _theme.c(role)


def ui_font(size: int = 9, weight: str = "normal") -> tuple:
    """Системный UI-шрифт (SF Pro → Segoe UI Variable → Segoe UI на Windows)."""
    return (_apple_tk.ui_family(), size, weight)


def mono_font(size: int = 9, weight: str = "normal") -> tuple:
    return (_apple_tk.mono_family(), size, weight)


def register_palette_consumer(namespace: dict) -> None:
    """Подписывает globals() модуля на обновление при смене темы.

    Панельные модули импортируют токены голыми именами («from uibase import
    BG» или через реэкспорт tools.pyw). Смена темы перезаписывает значения,
    поэтому каждый модуль-потребитель обязан зарегистрировать свой namespace —
    иначе его копии токенов устареют.
    """
    if namespace not in _PALETTE_CONSUMERS:
        _PALETTE_CONSUMERS.append(namespace)


def _refresh_palette() -> None:
    """Пересчитать все константы под текущую тему (в т.ч. при живой смене темы)."""
    values = {name: _theme.alias(name) for name in _LEGACY_NAMES}
    for name in _EXTRA_ROLES:
        values[name] = tc(name.lower())
    values["AVATAR_COLORS"] = _theme.AVATARS
    for namespace in _PALETTE_CONSUMERS:
        namespace.update(values)


register_palette_consumer(globals())
_refresh_palette()
# Модульный слушатель идёт первым: к моменту перерисовки виджетов константы
# уже соответствуют новой теме. Слушатель Hub регистрируется позже.
_theme.on_change(lambda _resolved: _refresh_palette())


def badge_colors(key: str) -> tuple[str, str]:
    """Цвет бейджа среднего балла: (фон, текст) в текущей теме."""
    bg_role, fg_role = {
        "red": ("badge_bad_bg", "badge_bad_ink"),
        "yellow": ("badge_warn_bg", "badge_warn_ink"),
        "green": ("badge_good_bg", "badge_good_ink"),
    }.get(key, ("badge_bad_bg", "badge_bad_ink"))
    return tc(bg_role), tc(fg_role)


def px(value: float) -> int:
    """Пиксели, независимые от масштаба Windows (96 DPI = 1.0)."""
    return int(round(value * SCALE))


def set_scale(value: float) -> None:
    """Масштаб Windows (96 DPI = 1.0) — вызывается один раз из Hub.__init__."""
    global SCALE
    SCALE = max(1.0, value)


def _dpi_aware() -> None:
    """Чтобы координаты мыши и скриншота совпадали при масштабе Windows != 100 %."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


_dpi_aware()


# --------------------------------------------------------------------------- #
#  Тема и мелкие помощники — тонкие обёртки над theme.apple_tk
# --------------------------------------------------------------------------- #
def apply_dark_theme(root: tk.Misc) -> None:
    _apple_tk.apply_theme(root, scale=SCALE)


def enable_dark_titlebar(window: tk.Misc) -> None:
    _apple_tk.titlebar(window)


def flat_button(parent: tk.Misc, text: str, command, *, kind: str = "primary", **kwargs) -> tk.Button:
    return _apple_tk.flat_button(parent, text, command, kind=kind, scale=SCALE, **kwargs)


def dark_check(parent: tk.Misc, text: str, variable: tk.BooleanVar, *,
               bg: str | None = None, command=None) -> tk.Checkbutton:
    return _apple_tk.check(parent, text, variable, bg=bg or BG, command=command)


def dark_text(parent: tk.Misc, *, height: int = 10, wrap: str = "word", **kwargs) -> tk.Text:
    return _apple_tk.log_view(parent, height=height, wrap=wrap, **kwargs)


def _iter_widgets(root: tk.Misc):
    for child in root.winfo_children():
        yield child
        yield from _iter_widgets(child)


def enable_input_paste(root: tk.Misc) -> None:
    """Ctrl+V / C / X / A в любом поле и в любой раскладке.

    Tk ловит Ctrl+V по keysym «v», а при русской раскладке keysym — «м»,
    поэтому штатная вставка молча не срабатывала. Ловим только комбинации
    с Ctrl (обычный набор клавиш не перехватываем) и сверяем физический код
    клавиши — раскладка не важна.
    """
    if getattr(root, "_paste_hooked", False):
        return
    root._paste_hooked = True

    VK_PASTE, VK_COPY, VK_CUT, VK_ALL = 86, 67, 88, 65
    PASTE_KS, COPY_KS = {"v", "м"}, {"c", "с"}
    CUT_KS, ALL_KS = {"x", "ч"}, {"a", "а", "ф"}

    def on_ctrl(event):
        w = event.widget
        if not isinstance(w, (tk.Entry, tk.Text, ttk.Entry)):
            return None
        if event.state & 0x8:  # Alt зажат — не наше
            return None
        vk = getattr(event, "keycode", 0) or 0
        ks = (event.keysym or "").lower()
        if vk == VK_PASTE or ks in PASTE_KS:
            w.event_generate("<<Paste>>")
            return "break"
        if vk == VK_COPY or ks in COPY_KS:
            w.event_generate("<<Copy>>")
            return "break"
        if vk == VK_CUT or ks in CUT_KS:
            w.event_generate("<<Cut>>")
            return "break"
        if vk == VK_ALL or ks in ALL_KS:
            if isinstance(w, tk.Text):
                w.tag_add("sel", "1.0", "end-1c")
            else:
                w.selection_range(0, "end")
            return "break"
        return None

    def on_menu(event):
        w = event.widget
        if not isinstance(w, (tk.Entry, tk.Text, ttk.Entry)):
            return None
        menu = tk.Menu(w, tearoff=0, bg=CARD, fg=FG,
                       activebackground=tc("accent_fill"),
                       activeforeground=tc("on_accent"), bd=0)
        menu.add_command(label="Вставить", command=lambda: w.event_generate("<<Paste>>"))
        menu.add_command(label="Копировать", command=lambda: w.event_generate("<<Copy>>"))
        menu.add_command(label="Вырезать", command=lambda: w.event_generate("<<Cut>>"))
        menu.add_separator()
        if isinstance(w, tk.Text):
            menu.add_command(label="Выделить всё",
                             command=lambda: w.tag_add("sel", "1.0", "end-1c"))
        else:
            menu.add_command(label="Выделить всё",
                             command=lambda: w.selection_range(0, "end"))
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return "break"

    for widget in _iter_widgets(root):
        if not isinstance(widget, (tk.Entry, tk.Text, ttk.Entry)):
            continue
        if getattr(widget, "_paste_hooked", False):
            continue
        widget._paste_hooked = True
        widget.bind("<Control-KeyPress>", on_ctrl, add="+")
        widget.bind("<Button-3>", on_menu, add="+")

        def on_click(event, w=widget):
            # клик по полю всегда ставит фокус — даже если окно было неактивно
            w.focus_set()
            return None

        widget.bind("<Button-1>", on_click, add="+")


def fmt_size(size: float | None) -> str:
    if size is None:
        return "—"
    for unit, divider in (("ГБ", 1 << 30), ("МБ", 1 << 20), ("КБ", 1 << 10)):
        if size >= divider:
            return f"{size / divider:.1f} {unit}".replace(".", ",")
    return f"{int(size)} Б"


# --------------------------------------------------------------------------- #
#  Тёмные модальные диалоги
# --------------------------------------------------------------------------- #
class DarkMessageBox:
    """Тёмные модальные диалоги вместо стандартных светлых messagebox."""

    def _dialog(self, title: str, message: str, *, ask: bool = False, parent=None) -> bool:
        parent = parent or tk._default_root
        if parent is None:
            return False
        win = tk.Toplevel(parent)
        win.withdraw()
        win.title(title)
        win.configure(bg=CARD)
        win.transient(parent)
        win.resizable(False, False)

        body = tk.Frame(win, bg=CARD, padx=px(22), pady=px(18))
        body.pack(fill="both", expand=True)
        tk.Label(
            body, text=title, bg=CARD, fg=FG, font=ui_font(12, "bold"), anchor="w"
        ).pack(fill="x")
        tk.Label(
            body,
            text=message,
            bg=CARD,
            fg=MUTED,
            justify="left",
            anchor="w",
            wraplength=px(470),
            font=ui_font(9),
        ).pack(fill="x", pady=(px(10), 0))

        result = {"value": False}
        buttons = tk.Frame(body, bg=CARD)
        buttons.pack(fill="x", pady=(px(18), 0))

        def close(value: bool) -> None:
            result["value"] = value
            win.destroy()

        flat_button(buttons, "Да" if ask else "ОК", lambda: close(True)).pack(side="right")
        if ask:
            flat_button(buttons, "Отмена", lambda: close(False), kind="ghost").pack(
                side="right", padx=(0, px(8))
            )

        win.bind("<Return>", lambda _event: close(True))
        win.bind("<Escape>", lambda _event: close(False))
        win.protocol("WM_DELETE_WINDOW", lambda: close(False))
        win.update_idletasks()
        width, height = win.winfo_reqwidth(), win.winfo_reqheight()
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - width) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 3)
        win.geometry(f"+{x}+{y}")
        enable_dark_titlebar(win)
        win.deiconify()
        win.grab_set()
        win.focus_force()
        win.wait_window()
        return result["value"]

    def showinfo(self, title: str, message: str, **kwargs) -> bool:
        return self._dialog(title, message, parent=kwargs.get("parent"))

    def showwarning(self, title: str, message: str, **kwargs) -> bool:
        return self._dialog(title, message, parent=kwargs.get("parent"))

    def showerror(self, title: str, message: str, **kwargs) -> bool:
        return self._dialog(title, message, parent=kwargs.get("parent"))

    def askyesno(self, title: str, message: str, **kwargs) -> bool:
        return self._dialog(title, message, ask=True, parent=kwargs.get("parent"))

    def askstring(self, title: str, message: str, initial: str = "", parent=None) -> str | None:
        parent = parent or tk._default_root
        if parent is None:
            return None
        win = tk.Toplevel(parent)
        win.withdraw()
        win.title(title)
        win.configure(bg=CARD)
        win.transient(parent)
        win.resizable(False, False)

        body = tk.Frame(win, bg=CARD, padx=px(22), pady=px(18))
        body.pack(fill="both", expand=True)
        tk.Label(body, text=title, bg=CARD, fg=FG,
                 font=ui_font(12, "bold"), anchor="w").pack(fill="x")
        tk.Label(body, text=message, bg=CARD, fg=MUTED, justify="left", anchor="w",
                 wraplength=px(430), font=ui_font(9)).pack(fill="x", pady=(px(10), 0))

        value = tk.StringVar(value=initial)
        entry = ttk.Entry(body, textvariable=value)
        entry.pack(fill="x", pady=(px(10), 0), ipady=px(2))

        result = {"value": None}

        def close(ok: bool) -> None:
            if ok:
                text = value.get().strip()
                result["value"] = text or None
            win.destroy()

        buttons = tk.Frame(body, bg=CARD)
        buttons.pack(fill="x", pady=(px(18), 0))
        flat_button(buttons, "ОК", lambda: close(True)).pack(side="right")
        flat_button(buttons, "Отмена", lambda: close(False), kind="ghost").pack(
            side="right", padx=(0, px(8))
        )

        win.bind("<Return>", lambda _event: close(True))
        win.bind("<Escape>", lambda _event: close(False))
        win.protocol("WM_DELETE_WINDOW", lambda: close(False))
        win.update_idletasks()
        width, height = win.winfo_reqwidth(), win.winfo_reqheight()
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - width) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 3)
        win.geometry(f"+{x}+{y}")
        enable_dark_titlebar(win)
        win.deiconify()
        win.grab_set()
        win.focus_force()
        entry.focus_set()
        entry.select_range(0, "end")
        win.wait_window()
        return result["value"]


messagebox = DarkMessageBox()


# --------------------------------------------------------------------------- #
#  Базовый класс панели
# --------------------------------------------------------------------------- #
class Panel(ttk.Frame):
    def __init__(self, master: tk.Misc, hub) -> None:
        super().__init__(master)
        self.hub = hub

    def make_log(self, parent: tk.Misc, *, height: int = 10, wrap: str = "word",
                 label: str = "Лог"):
        frame = ttk.Frame(parent)
        head = ttk.Frame(frame)
        head.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, px(4)))
        ttk.Label(head, text="Лог — выдели мышью, Ctrl+C или «Копировать всё»",
                  style="Muted.TLabel").pack(side="left")
        text = dark_text(frame, height=height, wrap=wrap)
        text.tag_configure("error", foreground=tc("log_err"))
        text.tag_configure("ok", foreground=OK)
        copy_btn = flat_button(head, "Копировать всё", lambda: None, kind="ghost")
        copy_btn.pack(side="right")
        copy_btn.configure(command=lambda: self.copy_log(text, copy_btn, label=label))
        flat_button(head, "Очистить", lambda: text.delete("1.0", "end"),
                    kind="ghost").pack(side="right", padx=(0, px(6)))
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.grid(row=1, column=0, sticky="nsew")
        scroll.grid(row=1, column=1, sticky="ns")
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)
        self.attach_copy(text, label=label, readonly=True)
        return frame, text

    def attach_copy(self, text: tk.Text, *, label: str = "текст",
                    readonly: bool = False, clear: bool = True) -> None:
        """Делает любой текстовый блок копируемым: Ctrl+C, Ctrl+A, правый клик.

        Копирование/выделение ловим универсально — по **физическому коду
        клавиши** (``event.keycode``: VK_C=67, VK_A=65, VK_INSERT=45), который
        не зависит от раскладки. Иначе при русской раскладке Ctrl+C даёт keysym
        «с», а не «c», и привязки ``<Control-c>``/``<<Copy>>`` молча не
        срабатывают — копия не происходит. Дополнительно страхуемся проверкой
        keysym (c/с, a/а/ф), чтобы сработать в любой раскладке.
        """

        # Навигационные клавиши, разрешённые даже в режиме «только чтение».
        NAV_KEYS = {
            "Left", "Right", "Up", "Down", "Home", "End",
            "Prior", "Next", "Shift_L", "Shift_R",
            "Control_L", "Control_R", "Tab", "Insert",
        }
        # Буквы-«действия» во всех раскладках.
        COPY_KEYS = {"c", "с"}                 # лат. C и кир. С
        SELECT_KEYS = {"a", "а", "ф"}          # лат. A, кир. А и кир. Ф (Ctrl+A на RU)

        def copy_selection(_event=None):
            try:
                chunk = text.get("sel.first", "sel.last")
            except tk.TclError:
                chunk = text.get("1.0", "end-1c")
            if not chunk:
                return "break"
            self.copy_text(chunk, label=label, widget=text)
            return "break"

        def select_all(_event=None):
            text.tag_add("sel", "1.0", "end-1c")
            return "break"

        def on_key(event):
            ctrl = bool(event.state & 0x4) and not bool(event.state & 0x8)
            if ctrl:
                vk = getattr(event, "keycode", 0) or 0
                ks = (event.keysym or "").lower()
                if vk == 67 or ks in COPY_KEYS:                # C — копировать
                    return copy_selection()
                if vk == 65 or ks in SELECT_KEYS:             # A — выделить всё
                    return select_all()
                if vk == 45 or ks == "insert":                # Insert — копировать
                    return copy_selection()
                # прочие Ctrl-комбинации в «только чтение» глушим
                return "break" if readonly else None
            # без Ctrl
            if readonly and event.keysym not in NAV_KEYS:
                return "break"
            return None

        text.bind("<Key>", on_key)
        text.bind("<<Copy>>", copy_selection)          # запасной путь (Ctrl+Insert и т.п.)
        text.bind("<<SelectAll>>", select_all)
        text.bind("<Control-a>", select_all)
        text.bind("<Control-A>", select_all)

        menu = tk.Menu(text, tearoff=0)
        menu.add_command(label="Копировать выделенное", command=copy_selection)
        menu.add_command(
            label="Копировать всё",
            command=lambda: self.copy_text(text.get("1.0", "end-1c"), label=label),
        )
        if clear:
            menu.add_separator()
            menu.add_command(label="Очистить", command=lambda: text.delete("1.0", "end"))
        text.bind("<Button-3>", lambda event: menu.tk_popup(event.x_root, event.y_root))

    def copy_log(self, text: tk.Text, button: tk.Button, *, label: str = "Лог") -> None:
        try:
            chunk = text.get("sel.first", "sel.last")
        except tk.TclError:
            chunk = text.get("1.0", "end-1c")
        self.copy_text(chunk, label=label, widget=text)
        original = button.cget("text")
        button.configure(text="✅ Скопировано")
        self.after(1800, lambda: button.configure(text=original))

    def copy_text(self, chunk: str, *, label: str = "лог", widget=None) -> None:
        """Кладёт текст в буфер обмена и явно показывает результат.

        При копировании текстовый блок кратко вспыхивает зелёным — видно,
        что именно скопировалось.
        """
        if not chunk.strip():
            self.hub.set_status("Копировать нечего — пусто")
            self.hub.toast("Копировать нечего — блок пуст", kind="warn")
            return
        self.clipboard_clear()
        self.clipboard_append(chunk)
        self.update()          # без update() Windows не фиксирует буфер сразу
        lines = chunk.count("\n") + (0 if chunk.endswith("\n") else 1)
        self.hub.set_status(f"📋 скопировано: {lines} строк, {len(chunk)} символов")
        self.hub.toast(f"📋 {label}: скопировано {lines} стр., {len(chunk)} симв.")
        if widget is not None:
            self.flash_copied(widget)

    def flash_copied(self, widget) -> None:
        """Кратковременная зелёная вспышка + рамка — явное подтверждение копии."""
        try:
            widget.configure(bg=COPIED_BG, highlightbackground=OK, highlightcolor=OK)
        except (tk.TclError, AttributeError):
            return
        self.after(500, lambda: self._unflash(widget))

    @staticmethod
    def _unflash(widget) -> None:
        try:
            widget.configure(bg=LOG_BG, highlightbackground=BORDER, highlightcolor=BORDER)
        except (tk.TclError, AttributeError):
            pass

    @staticmethod
    def append_log(text: tk.Text, line: str, tag: str | None = None) -> None:
        text.insert("end", f"{time.strftime('%H:%M:%S')}  {line}\n", (tag,) if tag else ())
        lines = int(text.index("end-1c").split(".")[0])
        if lines > 500:
            text.delete("1.0", f"{lines - 500}.0")
        text.see("end")
