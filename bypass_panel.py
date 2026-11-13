#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Панель «Bypass»: обход DPI — Happ, GoodbyeDPI, ProxiFyre.

Вынесено из tools.pyw без изменения логики: контролы Switch / Segmented /
AppRow / ScrollList и сама BypassPanel. Логика обхода живёт в bypass_core,
каталог программ — в apps_catalog: панель обращается к ним только через
модуль-хозяина (``bypass_core.*`` / ``apps_catalog.*``), чтобы тесты патчили
хозяина, а не копию имён в модуле панели.
"""

from __future__ import annotations

import os
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, ttk

import apps_catalog
import bypass_core
from scroll_settings import steps_per_wheel as _scroll_step

from uibase import (
    ACCENT,
    AVATAR_COLORS,
    BG,
    BORDER,
    CARD,
    CARD_HOVER,
    FG,
    FIELD,
    KNOB,
    MUTED,
    OK,
    ON_AVATAR,
    Panel,
    ROW_SELECTED,
    TRACK_DISABLED,
    TRACK_OFF,
    WARN,
    flat_button,
    messagebox,
    px,
    register_palette_consumer,
    tc,
    ui_font,
)

# Токены приходят из uibase; регистрируем глобалы этого модуля потребителем
# палитры, чтобы живая смена темы обновляла и их (контролы читают голые имена).
register_palette_consumer(globals())

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

    def __init__(self, parent, entry: apps_catalog.AppEntry, selected: bool, on_toggle, *,
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
        cfg = bypass_core.bypass_cfg()
        self.mode = cfg.get("mode", "happ")
        self.scope = cfg.get("scope", "all")
        self.selected_apps: set[str] = {name.lower() for name in cfg.get("apps", [])}
        self.catalog: list[apps_catalog.AppEntry] = []
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
        self.hub.run_bg(apps_catalog.app_catalog, self._catalog_done, self._catalog_fail)

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
        matched = apps_catalog.filter_apps(self.catalog, self.query, only_running=self.only_running)
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

    def _on_app_toggle(self, entry: apps_catalog.AppEntry, selected: bool) -> None:
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
            self._run(bypass_core.proxifyre_stop, "Программы: список пуст, выключаю обход…",
                      "ProxiFyre: программ не осталось — выключаю перенаправление")
            return
        self._run(lambda: bypass_core.proxifyre_start(apps), "Программы: применяю список…",
                  f"ProxiFyre: перенастраиваю на {len(apps)} программ…")

    def _selected_exe(self) -> list[str]:
        result = []
        for entry in self.catalog:
            if entry.exe.lower() in self.selected_apps:
                result.append(entry.exe)
        known = {name.lower() for name in result}
        for name in bypass_core.bypass_cfg()["apps"]:
            if name.lower() in self.selected_apps and name.lower() not in known:
                result.append(name)
        return apps_catalog.normalize_apps(result)

    def _save_cfg(self) -> None:
        bypass_core.bypass_cfg_save(mode=self.mode, scope=self.scope,
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
                bypass_core.happ_mode_write(tun=False, system_proxy=False)
                bypass_core.set_windows_system_proxy(False)
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
        self._run(lambda: bypass_core.bypass_apply(self.scope, mode),
                  f"{label}: включаю ({scope_text})…",
                  f"Включаю {label} · {scope_text}" + (
                      " · потребуется подтверждение администратора (UAC)"
                      if False else ""))

    def _stop_mode(self, mode: str) -> None:
        label = "Happ" if mode == "happ" else "GoodbyeDPI"
        self._run(lambda: bypass_core.bypass_stop(mode), f"{label}: выключаю…",
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
            self._run(bypass_core.bypass_apps_route_enable, "включаю обход по программам…",
                      "Включаю обход по программам (ProxiFyre)")
        else:
            self._run(bypass_core.bypass_apps_route_disable, "выключаю обход по программам…",
                      "Выключаю обход по программам")

    # -- статус -------------------------------------------------------------- #
    def _poll_state(self, scope: str) -> dict:
        """Опрос состояния + автоисцеление в ОДНОМ фоновом потоке.

        Раньше `bypass_auto_heal_if_leak` вызывался прямо из `_tick`, то есть в
        главном потоке tkinter: tasklist / netsh / taskkill замораживали окно
        каждые 3 секунды. Теперь всё уходит в `run_bg`.
        """
        state = bypass_core.bypass_status()
        try:
            state["_heal"] = bypass_core.bypass_auto_heal_if_leak(scope)
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
        bypass_core.happ_mode_write(tun=False, system_proxy=False)
        bypass_core.set_windows_system_proxy(False)
        bypass_core.happ_tun_disable()
        if bypass_core.happ_connected():
            return bypass_core.happ_tunnel_restart()
        return {"ok": True, "detail": "TUN отключён в Happ"}

    def check_ip_now(self) -> None:
        """Полная проверка обхода: прямой IP vs IP через SOCKS Happ → вердикт."""
        if self.ip_checking or self.busy or not self.winfo_exists():
            return
        self.ip_checking = True
        self.btn_check_ip.configure(state="disabled")
        self.hub.run_bg(
            lambda: bypass_core.bypass_compare_connections(timeout=4.0),
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
            f"Обход SOCKS {proxy.get('proxy_port', bypass_core.HAPP_SOCKS_DEFAULT)} -> "
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
        self._run(bypass_core.proxifyre_restart, "ProxiFyre: подхватываю запущенные программы…",
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
        port = happ.get("socks_port") or bypass_core.HAPP_SOCKS_DEFAULT
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
        self._run(bypass_core.bypass_happ_show, "Открываю окно Happ…", "Открываю окно Happ…")

    def download(self, kind: str) -> None:
        if self.busy:
            return
        label = "GoodbyeDPI" if kind == "gbd" else kind
        self._run(lambda: bypass_core.bypass_download(kind), f"Скачиваю {label}…",
                  f"Скачиваю {label} с GitHub (может занять пару минут)…")

    def configure_dialog(self) -> None:
        if self.busy:
            return
        cfg = bypass_core.bypass_cfg()

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
            path_var.set(str(bypass_core._find_happ()))

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
            bypass_core.bypass_cfg_save(happ_path=value)
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
