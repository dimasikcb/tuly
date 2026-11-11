"""Адаптер темы для tkinter/ttk (tools.pyw, gui.py, screen_agent.py).

Использование:
    from theme import apple_tk
    apple_tk.apply_theme(root)
    btn = apple_tk.flat_button(parent, "Запустить", cmd, kind="primary")
"""
from __future__ import annotations

import ctypes
import sys
import tkinter as tk
from tkinter import ttk

from . import c as _color
from . import mode as _mode
from . import palette as _palette
from .tokens import FONTS, TYPE_SCALE

_WEIGHTS = {400: "normal", 500: "normal", 600: "bold", 700: "bold"}

# Опции tk-виджетов, в которых могут лежать цвета. Перекрашиваются при смене темы.
_COLOR_OPTIONS = (
    "bg", "background", "fg", "foreground", "activebackground",
    "activeforeground", "disabledforeground", "selectcolor",
    "selectbackground", "selectforeground", "highlightbackground",
    "highlightcolor", "insertbackground", "troughcolor", "bordercolor",
    "fieldbackground", "arrowcolor", "readonlybackground",
)


def _family(preferred: str, fallback: str) -> str:
    """Не запрашивать отсутствующий в системе шрифт (Win10 нет Segoe UI Variable)."""
    try:
        import tkinter.font as tkfont  # noqa: PLC0415

        root = tk._default_root
        if root is not None and preferred in tkfont.families(root):
            return preferred
    except Exception:  # noqa: BLE001 —fallback всегда доступен
        pass
    return fallback


def ui_family() -> str:
    return _family(FONTS["ui_tk"], FONTS["ui_tk_fallback"])


def mono_family() -> str:
    return _family(FONTS["mono_tk"], FONTS["mono_tk_fallback"])


def font(style: str = "body") -> tuple:
    """Шрифт tk по имени стиля из TYPE_SCALE."""
    size, _leading, weight = TYPE_SCALE.get(style, TYPE_SCALE["body"])
    return (ui_family(), size, _WEIGHTS.get(weight, "normal"))


def mono_font() -> tuple:
    size, _leading, _weight = TYPE_SCALE["mono"]
    return (mono_family(), size, "normal")


def _px(value: float, scale: float) -> int:
    return int(round(value * scale))


def apply_theme(root: tk.Misc, resolved: str | None = None, scale: float = 1.0) -> ttk.Style:
    """Настроить ttk-стили под текущую тему. Возвращает Style для донастройки."""
    resolved = resolved or _mode()
    p = lambda role: _color(role, resolved)  # noqa: E731
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(
        ".",
        background=p("window"), foreground=p("label"),
        fieldbackground=p("inset"), bordercolor=p("separator"),
        lightcolor=p("separator"), darkcolor=p("separator"),
        focuscolor=p("accent"), troughcolor=p("inset"),
        arrowcolor=p("label"), selectbackground=p("selected"),
        selectforeground=p("on_accent"),
        font=font("body"),
    )
    style.configure("TFrame", background=p("window"))
    style.configure("TLabel", background=p("window"), foreground=p("label"))
    style.configure("Muted.TLabel", background=p("window"), foreground=p("label_secondary"))
    style.configure(
        "TButton", background=p("surface_raised"), foreground=p("label"),
        padding=(_px(12, scale), _px(6, scale)), borderwidth=0,
    )
    style.map(
        "TButton",
        background=[("active", p("separator")), ("disabled", p("surface"))],
        foreground=[("disabled", p("disabled_label"))],
    )
    style.configure(
        "TEntry", fieldbackground=p("inset"), foreground=p("label"),
        insertcolor=p("label"), padding=_px(6, scale), bordercolor=p("inset_border"),
    )
    style.map("TEntry", bordercolor=[("focus", p("accent"))], lightcolor=[("focus", p("accent"))])
    style.configure(
        "TSpinbox", fieldbackground=p("inset"), foreground=p("label"),
        arrowcolor=p("label"), padding=_px(4, scale), arrowsize=22,
    )
    style.configure(
        "TCombobox", fieldbackground=p("inset"), foreground=p("label"),
        arrowcolor=p("label"), padding=_px(4, scale), arrowsize=22,
    )
    style.map("TCombobox", fieldbackground=[("readonly", p("inset"))], foreground=[("readonly", p("label"))])
    root.option_add("*TCombobox*Listbox.background", p("inset"))
    root.option_add("*TCombobox*Listbox.foreground", p("label"))
    root.option_add("*TCombobox*Listbox.selectBackground", p("selected"))
    style.configure(
        "Treeview", background=p("surface"), fieldbackground=p("surface"),
        foreground=p("label"), bordercolor=p("separator"), rowheight=_px(26, scale),
    )
    style.map("Treeview", background=[("selected", p("selected"))], foreground=[("selected", p("on_accent"))])
    style.configure(
        "Treeview.Heading", background=p("inset"), foreground=p("label_secondary"),
        relief="flat", padding=(_px(6, scale), _px(6, scale)),
    )
    style.map("Treeview.Heading", background=[("active", p("surface_raised"))])
    style.configure("TLabelframe", background=p("window"), bordercolor=p("separator"))
    style.configure("TLabelframe.Label", background=p("window"), foreground=p("label_secondary"))
    style.configure("TCheckbutton", background=p("window"), foreground=p("label"))
    style.map("TCheckbutton", background=[("active", p("window"))], foreground=[("disabled", p("disabled_label"))])
    for name in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
        style.configure(
            name, background=p("surface_raised"), troughcolor=p("window"),
            bordercolor=p("window"), arrowcolor=p("label_secondary"), width=_px(12, scale),
        )
        style.map(name, background=[("active", p("separator"))])
    return style


def _walk(root: tk.Misc):
    try:
        children = root.winfo_children()
    except Exception:  # noqa: BLE001 —виджет мог быть уничтожен
        return
    for child in children:
        yield child
        yield from _walk(child)


# В палитрах есть роли с одинаковым HEX (например, в светлой теме sidebar и
# inset раньше были #ececf1). Простой словарь «цвет → цвет» теряет роль и при
# переключении красит сайдбар цветом поля ввода. Поэтому сопоставление строится
# отдельно для текстовых и фоновых опций: сначала роли нужной группы.
_FG_ROLES = (
    "on_accent", "label", "label_secondary", "label_tertiary", "disabled_label",
    "log_info", "log_warn", "log_err", "ok", "warn", "err", "accent",
    "sidebar_title", "sidebar_subtitle", "brand_label", "tag_file", "tag_dir",
    "badge_bad_ink", "badge_warn_ink", "badge_good_ink", "knob", "on_avatar",
    "roi", "overlay_timer",
)
_BG_ROLES = (
    "window", "sidebar", "surface", "surface_raised", "inset",
    "sidebar_active", "sidebar_hover", "row_selected",
    "badge_neutral_bg", "badge_bad_bg", "badge_warn_bg", "badge_good_bg",
    "ok_bg", "warn_bg", "err_bg", "tag_bg", "separator", "separator_strong",
    "inset_border", "tag_border", "track_off", "track_disabled",
    "accent", "accent_hover", "accent_fill", "accent_fill_hover", "selected",
    "overlay_backdrop",
)
_FG_OPTIONS = {
    "fg", "foreground", "activeforeground", "disabledforeground",
    "selectforeground", "insertbackground", "arrowcolor",
}


def _color_lookup(old: dict[str, str], new: dict[str, str],
                  priority: tuple[str, ...]) -> dict[str, str]:
    lookup: dict[str, str] = {}
    ordered = list(priority) + [role for role in old if role not in priority]
    for role in ordered:
        value = old.get(role)
        if not isinstance(value, str) or not value.startswith("#"):
            continue
        if role not in new:
            continue
        lookup.setdefault(value.lower(), new[role])
    return lookup


def recolor(root: tk.Misc, old_resolved: str, new_resolved: str | None = None) -> int:
    """Живая перекраска уже созданных tk-виджетов при смене Light/Dark.

    Возвращает количество изменённых опций. Canvas-элементы перерисовываются
    отдельно через redraw(), т.к. их цвета лежат не в configure().
    """
    old = _palette(old_resolved)
    new = _palette(new_resolved)
    fg_lookup = _color_lookup(old, new, _FG_ROLES + _BG_ROLES)
    bg_lookup = _color_lookup(old, new, _BG_ROLES + _FG_ROLES)
    changed = 0
    for widget in _walk(root):
        try:
            config = widget.configure()
        except Exception:  # noqa: BLE001
            continue
        for option in _COLOR_OPTIONS:
            entry = config.get(option)
            if not isinstance(entry, tuple) or len(entry) < 2:
                continue
            value = entry[-1]
            if not isinstance(value, str):
                continue
            lookup = fg_lookup if option in _FG_OPTIONS else bg_lookup
            replacement = lookup.get(value.lower())
            if replacement is None or replacement.lower() == value.lower():
                continue
            try:
                widget.configure(**{option: replacement})
                changed += 1
            except Exception:  # noqa: BLE001 —не все виджеты поддерживают опцию
                pass
    return changed


def redraw(root: tk.Misc) -> int:
    """Перерисовать кастомные Canvas-контролы (Switch/Segmented/AppRow/NavItem)."""
    count = 0
    for widget in _walk(root):
        for method_name in ("_draw", "_paint"):
            method = getattr(widget, method_name, None)
            if callable(method):
                try:
                    method()
                    count += 1
                except Exception:  # noqa: BLE001
                    pass
                break
    return count


def flat_button(parent: tk.Misc, text: str, command=None, *, kind: str = "ghost",
                scale: float = 1.0, **kwargs) -> tk.Button:
    """Кнопка: primary = единственное главное действие, остальные — нейтральные."""
    resolved = _mode()
    if kind == "primary":
        bg, fg = _color("accent_fill", resolved), _color("on_accent", resolved)
        active, active_fg = _color("accent_fill_hover", resolved), fg
    elif kind == "danger":
        bg, fg = _color("err_bg", resolved), _color("badge_bad_ink", resolved)
        active = _color("err_hover_bg", resolved)
        active_fg = fg
    else:
        bg, fg = _color("surface_raised", resolved), _color("label", resolved)
        active, active_fg = _color("separator", resolved), fg
    options = dict(
        text=text, command=command, bg=bg, fg=fg,
        activebackground=active, activeforeground=active_fg,
        disabledforeground=_color("disabled_label", resolved),
        relief="flat", bd=0,
        padx=_px(14, scale), pady=_px(7, scale),
        font=font("headline" if kind != "ghost" else "body"),
        cursor="hand2",
        highlightthickness=1 if kind == "ghost" else 0,
        highlightbackground=_color("separator", resolved),
    )
    options.update(kwargs)
    return tk.Button(parent, **options)


def check(parent: tk.Misc, text: str, variable, *, bg: str | None = None,
          command=None) -> tk.Checkbutton:
    resolved = _mode()
    return tk.Checkbutton(
        parent, text=text, variable=variable, command=command,
        bg=bg or _color("window", resolved), fg=_color("label", resolved),
        activebackground=bg or _color("window", resolved),
        activeforeground=_color("label", resolved),
        selectcolor=_color("inset", resolved),
        disabledforeground=_color("disabled_label", resolved),
        highlightthickness=0, bd=0, anchor="w", font=font("body"), cursor="hand2",
    )


def log_view(parent: tk.Misc, *, height: int = 10, wrap: str = "word", **kwargs) -> tk.Text:
    resolved = _mode()
    options = dict(
        height=height, wrap=wrap,
        bg=_color("inset", resolved), fg=_color("log_info", resolved),
        insertbackground=_color("label", resolved),
        selectbackground=_color("selected", resolved),
        selectforeground=_color("on_accent", resolved),
        relief="flat", bd=0, highlightthickness=1,
        highlightbackground=_color("inset_border", resolved),
        highlightcolor=_color("inset_border", resolved),
        font=mono_font(), padx=8, pady=6,
    )
    options.update(kwargs)
    return tk.Text(parent, **options)


def card(parent: tk.Misc, **kwargs) -> tk.Frame:
    options = dict(bg=_color("window", _mode()), highlightthickness=1,
                   highlightbackground=_color("separator", _mode()))
    options.update(kwargs)
    return tk.Frame(parent, **options)


def separator(parent: tk.Misc, *, horizontal: bool = True, **kwargs) -> tk.Frame:
    options = dict(bg=_color("separator", _mode()), height=1 if horizontal else 0,
                   width=0 if horizontal else 1)
    options.update(kwargs)
    return tk.Frame(parent, **options)


def titlebar(window: tk.Misc, resolved: str | None = None) -> None:
    """Тёмная/светлая шапка окна Windows через DWM (атрибуты 20/19)."""
    if sys.platform != "win32":
        return
    dark = (resolved or _mode()) == "dark"
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1 if dark else 0)
        for attribute in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value),
            ) == 0:
                break
    except Exception:  # noqa: BLE001 —декоративное, не критично
        pass
