"""Адаптер единой темы для CustomTkinter (diary-grades/app.py).

CustomTkinter импортируется лениво: генератор CSS и тесты токенов работают
в интерпретаторах без customtkinter.

Ключевая идея: CTk умеет хранить цвет как пару (light, dark) и сам
перекрашивает УЖЕ созданные виджеты при set_appearance_mode(). Поэтому
достаточно один раз задать tint(role) и не писать свой обход дерева.
"""
from __future__ import annotations

import sys
from pathlib import Path

from . import c as _color
from . import mode as _mode
from .tokens import FONTS, TYPE_SCALE

THEME_JSON = Path(__file__).with_name("apple_ctk.json")
_WEIGHT = {400: "normal", 500: "normal", 600: "bold", 700: "bold"}


def configure(resolved: str | None = None) -> str:
    """Вызвать ДО создания виджетов (CustomTkinter кэширует цвета темы)."""
    import customtkinter as ctk  # noqa: PLC0415

    if not THEME_JSON.is_file():
        from . import generate_ctk  # noqa: PLC0415

        generate_ctk.main()
    resolved = resolved or _mode()
    ctk.set_default_color_theme(str(THEME_JSON))
    ctk.set_appearance_mode(resolved)
    return resolved


def tint(role: str) -> tuple[str, str]:
    """Пара (light, dark) для kwargs CTk: живая смена темы без пересборки UI."""
    return (_color(role, "light"), _color(role, "dark"))


def resolve(value, resolved: str | None = None) -> str:
    """Развернуть tint(role) в конкретный HEX текущей темы (для проверок)."""
    if isinstance(value, (tuple, list)) and len(value) == 2:
        return value[0] if (resolved or _mode()) == "light" else value[1]
    return value


def c(role: str, resolved: str | None = None) -> str:
    """Одиночный цвет по роли — для кода, которому нужна конкретная тема."""
    return _color(role, resolved)


def ui_family() -> str:
    """Не запрашивать отсутствующий в системе шрифт (Win10 нет Segoe UI Variable)."""
    if sys.platform != "win32":
        return "DejaVu Sans"
    try:
        import tkinter as tk  # noqa: PLC0415
        import tkinter.font as tkfont  # noqa: PLC0415

        root = tk._default_root
        if root is not None and FONTS["ui_tk"] in tkfont.families(root):
            return FONTS["ui_tk"]
    except Exception:  # noqa: BLE001 — fallback всегда доступен
        pass
    return FONTS["ui_tk_fallback"]


def font(size: int | None = None, bold: bool = False, style: str = "body"):
    """CTkFont из TYPE_SCALE; size/bold — для унаследованных вызовов app.py."""
    import customtkinter as ctk  # noqa: PLC0415

    base, _leading, weight = TYPE_SCALE.get(style, TYPE_SCALE["body"])
    if size is None:
        size = base
        bold = bold or weight >= 600
    return ctk.CTkFont(family=ui_family(), size=size, weight="bold" if bold else "normal")


def titlebar(window, resolved: str | None = None) -> None:
    """Синхронизировать шапку DWM Windows с темой окна."""
    from . import apple_tk  # noqa: PLC0415

    apple_tk.titlebar(window, resolved)


def frame_colors(role: str = "surface", *, border: bool = False) -> dict:
    """Готовые kwargs для CTkFrame; цвета — пары (light, dark)."""
    out = {"fg_color": tint(role)}
    if border:
        out["border_width"] = 1
        out["border_color"] = tint("separator")
    return out


def button_colors(kind: str = "ghost") -> dict:
    """Готовые kwargs для CTkButton; цвета — пары (light, dark)."""
    if kind == "primary":
        return {"fg_color": tint("accent_fill"),
                "hover_color": tint("accent_fill_hover"),
                "text_color": tint("on_accent")}
    if kind == "danger":
        return {"fg_color": tint("err_bg"),
                "hover_color": tint("err_hover_bg"),
                "text_color": tint("badge_bad_ink")}
    return {"fg_color": tint("surface_raised"),
            "hover_color": tint("separator"),
            "text_color": tint("label")}
