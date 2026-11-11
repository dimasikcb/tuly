"""Расчёт контраста WCAG 2.x для проверки токенов (HIG Dark Mode: >= 4.5:1)."""
from __future__ import annotations

import sys
from pathlib import Path

if __package__ in (None, ""):  # прямой запуск: py -3 theme/contrast.py
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from theme.tokens import CONTRAST_PAIRS, TOKENS  # noqa: E402


def _luminance(color: str) -> float:
    value = color.lstrip("#")
    if len(value) != 6:
        raise ValueError(f"ожидался HEX #rrggbb, получено {color!r}")
    channels = []
    for index in (0, 2, 4):
        component = int(value[index:index + 2], 16) / 255.0
        channels.append(
            component / 12.92 if component <= 0.04045
            else ((component + 0.055) / 1.055) ** 2.4
        )
    return 0.2126 * channels[0] + 0.7152 * channels[1] + 0.0722 * channels[2]


def ratio(foreground: str, background: str) -> float:
    """Отношение контраста от 1.0 до 21.0."""
    first, second = sorted((_luminance(foreground), _luminance(background)), reverse=True)
    return (first + 0.05) / (second + 0.05)


def check(threshold: float = 4.5) -> list[tuple[str, str, str, float]]:
    """Вернуть пары, не прошедшие порог: (тема, текст, фон, отношение)."""
    failures = []
    for theme_name, palette in TOKENS.items():
        for text_role, background_role in CONTRAST_PAIRS:
            value = ratio(palette[text_role], palette[background_role])
            if value < threshold:
                failures.append((theme_name, text_role, background_role, round(value, 2)))
    return failures


def report() -> str:
    lines = []
    for theme_name, palette in TOKENS.items():
        for text_role, background_role in CONTRAST_PAIRS:
            value = ratio(palette[text_role], palette[background_role])
            lines.append(f"{theme_name:5s}  {text_role:18s} / {background_role:14s}  {value:5.2f}:1")
    return "\n".join(lines)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    print(report())
    failures = check()
    if failures:
        print("FAIL:", failures)
        raise SystemExit(1)
    print("OK: все текстовые пары >= 4.5:1")
