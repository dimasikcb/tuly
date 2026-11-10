#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Настройки чувствительности скролла колёсиком мыши.

Параметр сохраняется в отдельном файле рядом с исполняемым модулем, чтобы не
трогать конфиги других разделов программы.
"""
from __future__ import annotations

import json
from pathlib import Path

SCROLL_SETTINGS_FILE = Path(__file__).with_name("_scroll_settings.json")

DEFAULT_STEPS_PER_WHEEL = 3  # шагов на одно щелчок колёсика (по умолчанию 3)
MIN_STEPS = 1
MAX_STEPS = 20


def load_settings() -> dict:
    """Загружает сохранённые настройки, если файл существует."""
    try:
        if SCROLL_SETTINGS_FILE.exists():
            with open(SCROLL_SETTINGS_FILE, encoding="utf-8") as fh:
                data = json.load(fh)
            if isinstance(data, dict):
                value = data.get("steps_per_wheel", DEFAULT_STEPS_PER_WHEEL)
                if str(value).isdigit():
                    return {"steps_per_wheel": max(MIN_STEPS, int(value))}
    except Exception:  # noqa: BLE001 — если файл битый, возвращаем дефолт
        pass
    return {"steps_per_wheel": DEFAULT_STEPS_PER_WHEEL}


def save_settings(steps_per_wheel: int) -> bool:
    """Сохраняет выбранный шаг скролла в JSON-файл."""
    try:
        with open(SCROLL_SETTINGS_FILE, "w", encoding="utf-8") as fh:
            json.dump({"steps_per_wheel": max(MIN_STEPS, min(MAX_STEPS, steps_per_wheel))}, fh)
        return True
    except Exception:  # noqa: BLE001
        return False


def steps_per_wheel() -> int:
    """Текущий шаг скролла (единиц прокрутки на одно щелчок колёсика)."""
    return load_settings().get("steps_per_wheel", DEFAULT_STEPS_PER_WHEEL)
