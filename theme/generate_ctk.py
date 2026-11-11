"""Генератор темы CustomTkinter из tokens.py.

Аналог generate_css.py: HEX живёт только в tokens.py, а CTk получает пары
[light, dark] и сам перекрашивает уже созданные виджеты при смене темы.
Запуск: py -3 -m theme.generate_ctk (или через generate_css.main()).

Структура повторяет ключи blue.json из CustomTkinter 6.0: ThemeManager
читает тему целиком, поэтому отсутствие ключа вызвало бы KeyError.
"""
from __future__ import annotations

import json
from pathlib import Path

from .tokens import DARK, FONTS, LIGHT

OUTPUT = Path(__file__).with_name("apple_ctk.json")

# Строка-роль из tokens.py → [light, dark]; int и "transparent" остаются как есть.
# Полный набор классов виджетов CTk 6.0 (иначе ThemeManager упадёт на отсутствии).
THEME: dict[str, dict] = {
    "CTk": {"fg_color": "window"},
    "CTkToplevel": {"fg_color": "window"},
    "CTkFrame": {
        "corner_radius": 14, "border_width": 0,
        "fg_color": "surface", "top_fg_color": "surface_raised",
        "border_color": "separator",
    },
    "CTkButton": {
        "corner_radius": 8, "border_width": 0,
        "fg_color": "accent_fill", "hover_color": "accent_fill_hover",
        "border_color": "separator", "text_color": "on_accent",
        "text_color_disabled": "disabled_label",
    },
    "CTkLabel": {
        "corner_radius": 0, "border_width": 0, "fg_color": "transparent",
        "border_color": "separator", "text_color": "label",
    },
    "CTkEntry": {
        "corner_radius": 8, "border_width": 1,
        "fg_color": "inset", "border_color": "separator",
        "text_color": "label", "placeholder_text_color": "label_tertiary",
    },
    "CTkCheckBox": {
        "corner_radius": 6, "border_width": 2,
        "fg_color": "accent_fill", "border_color": "separator_strong",
        "hover_color": "accent_fill_hover", "checkmark_color": "on_accent",
        "text_color": "label", "text_color_disabled": "disabled_label",
    },
    "CTkSwitch": {
        "corner_radius": 999, "border_width": 0, "button_length": 0,
        "fg_color": "track_off", "progress_color": "accent_fill",
        "button_color": "knob", "button_hover_color": "knob",
        "text_color": "label", "text_color_disabled": "disabled_label",
    },
    "CTkRadioButton": {
        "corner_radius": 999, "border_width_checked": 6,
        "border_width_unchecked": 3,
        "fg_color": "accent_fill", "border_color": "separator_strong",
        "hover_color": "accent_fill_hover", "text_color": "label",
        "text_color_disabled": "disabled_label",
    },
    "CTkProgressBar": {
        "corner_radius": 999, "border_width": 0,
        "fg_color": "track_off", "progress_color": "accent_fill",
        "border_color": "separator",
    },
    "CTkSlider": {
        "corner_radius": 999, "button_corner_radius": 999,
        "border_width": 6, "button_length": 0,
        "fg_color": "track_off", "progress_color": "accent_fill",
        "button_color": "knob", "button_hover_color": "knob",
    },
    "CTkOptionMenu": {
        "corner_radius": 8, "fg_color": "surface_raised",
        "button_color": "separator", "button_hover_color": "separator_strong",
        "text_color": "label", "text_color_disabled": "disabled_label",
    },
    "CTkComboBox": {
        "corner_radius": 8, "border_width": 1,
        "fg_color": "inset", "border_color": "separator",
        "button_color": "separator_strong", "button_hover_color": "accent",
        "text_color": "label", "text_color_disabled": "disabled_label",
    },
    "CTkScrollbar": {
        "corner_radius": 999, "border_spacing": 4, "fg_color": "transparent",
        "button_color": "separator_strong",
        "button_hover_color": "label_tertiary",
    },
    "CTkSegmentedButton": {
        "corner_radius": 8, "border_width": 1,
        "fg_color": "surface_raised", "selected_color": "accent_fill",
        "selected_hover_color": "accent_fill_hover",
        "unselected_color": "surface_raised",
        "unselected_hover_color": "separator_strong",
        "text_color": "label", "text_color_disabled": "disabled_label",
    },
    "CTkTextbox": {
        "corner_radius": 8, "border_width": 0,
        "fg_color": "inset", "border_color": "separator",
        "text_color": "label",
        "scrollbar_button_color": "separator_strong",
        "scrollbar_button_hover_color": "label_tertiary",
    },
    "CTkScrollableFrame": {"label_fg_color": "surface_raised"},
    "DropdownMenu": {
        "fg_color": "surface_raised", "hover_color": "separator",
        "text_color": "label",
    },
    "CTkFont": {
        "macOS": {"family": "-apple-system", "size": 13, "weight": "normal"},
        "Windows": {"family": FONTS["ui_tk_fallback"], "size": 13, "weight": "normal"},
        "Linux": {"family": "DejaVu Sans", "size": 13, "weight": "normal"},
    },
}


def _pair(value):
    """Роль → [light, dark]; остальные значения (int, 'transparent') не трогаем."""
    if isinstance(value, str) and value in LIGHT and value in DARK:
        return [LIGHT[value], DARK[value]]
    return value


def build_theme() -> dict:
    """Детерминированный словарь темы; два вызова дают одинаковый результат."""
    return {
        cls: {key: _pair(value) for key, value in options.items()}
        for cls, options in THEME.items()
    }


def build_json() -> str:
    return json.dumps(build_theme(), ensure_ascii=False, indent=2) + "\n"


def main() -> int:
    OUTPUT.write_text(build_json(), encoding="utf-8", newline="\n")
    print(f"written: {OUTPUT} ({len(build_json())} chars)")
    return 0


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
