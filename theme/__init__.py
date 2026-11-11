"""Единая тема проекта: токены + адаптеры (tk/ttk, CustomTkinter, CSS).

Импорт адаптеров ленивый: генератор CSS и тесты контраста не требуют tkinter.
"""
from __future__ import annotations

import os
import sys
from typing import Callable

from .tokens import (
    ALIASES,
    AVATARS,
    CONTRAST_PAIRS,
    DARK,
    FONTS,
    LIGHT,
    MOTION,
    RADIUS,
    SPACE,
    TOKENS,
    TYPE_SCALE,
)

__all__ = [
    "ALIASES", "AVATARS", "CONTRAST_PAIRS", "DARK", "FONTS", "LIGHT",
    "MOTION", "RADIUS", "SPACE", "TOKENS", "TYPE_SCALE",
    "mode", "set_mode", "raw_mode", "palette", "c", "alias", "on_change",
    "apple_tk", "apple_ctk",
]

DEFAULT_MODE = "auto"
_VALID = {"light", "dark", "auto"}
_MODE = DEFAULT_MODE
_LISTENERS: list[Callable[[str], None]] = []


def _system_mode() -> str:
    """Следуем системной теме Windows (HIG: не делать свой переключатель)."""
    if sys.platform == "win32":
        try:
            import winreg  # noqa: PLC0415

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            try:
                value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            finally:
                winreg.CloseKey(key)
            return "light" if int(value) else "dark"
        except (OSError, ValueError, TypeError):
            pass
    return "dark"  # проект исторически тёмный


def raw_mode() -> str:
    """Что задано: light / dark / auto (с учётом TULY_THEME для тестов)."""
    override = os.environ.get("TULY_THEME", "").strip().lower()
    if override in _VALID:
        return override
    return _MODE


def mode() -> str:
    """Разрешённая тема: всегда 'light' или 'dark'."""
    value = raw_mode()
    return _system_mode() if value == "auto" else value


def set_mode(value: str) -> str:
    """Установить тему и уведомить подписчиков (живая перерисовка виджетов)."""
    global _MODE
    if value not in _VALID:
        raise ValueError(f"тема должна быть одной из {sorted(_VALID)}, получено {value!r}")
    _MODE = value
    resolved = mode()
    for callback in list(_LISTENERS):
        try:
            callback(resolved)
        except Exception:  # noqa: BLE001 — смена темы не должна ронять приложение
            pass
    return resolved


def on_change(callback: Callable[[str], None]) -> None:
    """Подписаться на смену темы. Подписчик получает 'light' или 'dark'."""
    if callback not in _LISTENERS:
        _LISTENERS.append(callback)


def palette(resolved: str | None = None) -> dict[str, str]:
    return TOKENS[resolved or mode()]


def c(role: str, resolved: str | None = None) -> str:
    """Цвет по семантической роли, например c('label_secondary')."""
    try:
        return palette(resolved)[role]
    except KeyError as exc:
        raise KeyError(f"нет токена {role!r}; доступные: {sorted(palette(resolved))}") from exc


def alias(name: str, resolved: str | None = None) -> str:
    """Цвет по старому имени константы (BG, ACCENT, CARD...)."""
    return c(ALIASES.get(name, name), resolved)


def __getattr__(name: str):
    """Ленивая загрузка адаптеров: theme.apple_tk / theme.apple_ctk.

    Через importlib, а не `from . import X`: последний снова вызывает
    __getattr__ и уходит в бесконечную рекурсию при `from theme import apple_tk`.
    """
    if name in ("apple_tk", "apple_ctk"):
        import importlib  # noqa: PLC0415

        module = importlib.import_module(f".{name}", __name__)
        globals()[name] = module
        return module
    raise AttributeError(name)
