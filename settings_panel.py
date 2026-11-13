#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Вкладка «Настройки» хаба: панель SettingsPanel — чувствительность скролла.

Переехала из tools.pyw verbatim, но починен известный баг: тело панели
вызывает scroll_settings.save_settings(value), а tools.pyw импортировал
только «from scroll_settings import steps_per_wheel as _scroll_step» —
при нажатии «Сохранить» падал NameError: scroll_settings. Здесь модуль
scroll_settings импортируется целиком, поэтому вызов разрешается; тело
класса при этом не менялось. Алиас _scroll_step сохранён, чтобы оставить
verbatim-ссылки _scroll_step() в __init__/on_show.

Модуль знает только uibase/scroll_settings; конкретный хаб ему не нужен —
всё общение через hub-мост базовой Panel (run_bg/set_status/toast/post).
"""

from __future__ import annotations

import sys
from pathlib import Path

import tkinter as tk
from tkinter import ttk

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from uibase import (  # noqa: E402
    BG,
    DANGER,
    FG,
    MUTED,
    OK,
    Panel,
    flat_button,
    px,
    register_palette_consumer,
    ui_font,
)

# Токены приходят реэкспортом; регистрируем глобалы этого модуля потребителем
# палитры, чтобы живая смена темы обновляла и их (панель читает голые имена).
register_palette_consumer(globals())

import scroll_settings  # noqa: E402  (починка NameError: save_settings)
from scroll_settings import steps_per_wheel as _scroll_step  # noqa: E402,F401


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
