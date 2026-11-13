#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Панель «Страж выключения»: следит за ИИ-агентами и выключает ПК, когда все завершились.

Вынесено из tools.pyw без изменения логики (класс ShutdownWatcherPanel —
verbatim):
  * таблица отслеживаемых агентов (config.json → watcher.agents: режимы
    process / idle / marker) и их состояний;
  * запуск/остановка стража: import shutdown_watcher as wsh, создаются
    wsh.Watcher(agents) и wsh.load_agents() — сама логика стража живёт в
    shutdown_watcher.py и здесь не дублируется;
  * опрос по after-таймеру (2.5 с), таймер «выключение через N с», отмена
    выключения, строки лога стража через watcher.take_lines().

Модуль знает только uibase; конкретный хаб ему не нужен — общение через
hub-мост базовой Panel (post). tkinter разрешён: это GUI-слой.
"""

from __future__ import annotations

import tkinter as tk
from tkinter import ttk

import shutdown_watcher as wsh

from uibase import (
    ACCENT,
    BG,
    DANGER,
    FG,
    LOG_BG,
    LOG_FG,
    MUTED,
    OK,
    Panel,
    WARN,
    flat_button,
    mono_font,
    px,
    register_palette_consumer,
    ui_font,
)

# Токены приходят из uibase; регистрируем глобалы этого модуля потребителем
# палитры, чтобы живая смена темы обновляла и их (панель читает голые имена).
register_palette_consumer(globals())


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
        col_defs = (
            ("mode", "Режим", px(80), px(60), False),
            ("target", "Цель", px(130), px(100), False),
            ("status", "Состояние", px(95), px(80), False),
            ("note", "Примечание", px(320), px(200), True),
        )
        for col, head, width, minwidth, stretch in col_defs:
            self.tree.heading(col, text=head)
            self.tree.column(col, width=width, minwidth=minwidth, stretch=stretch)
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
        cfg = wsh.load_config()
        watcher_cfg = cfg.get("watcher", {})
        tg_cfg = cfg.get("telegram", {})

        agents = self._watchers()
        if not agents:
            self.log_lines(["Список агентов пуст — добавь их в config.json → watcher.agents"])
            return

        notifier = wsh.TelegramNotifier(
            bot_token=tg_cfg.get("bot_token", ""),
            chat_id=tg_cfg.get("chat_id", ""),
            enabled=tg_cfg.get("enabled", True),
        )
        chat_tracker = None
        if watcher_cfg.get("track_workbuddy_chats", True):
            chat_tracker = wsh.WorkBuddyChatTracker()

        self.watcher = wsh.Watcher(
            agents=agents,
            interval=float(watcher_cfg.get("interval", 5.0)),
            notifier=notifier,
            chat_tracker=chat_tracker,
            grace_min=float(watcher_cfg.get("grace_min", 1.5)),
            hold_min=float(watcher_cfg.get("hold_min", 1.0)),
            shutdown_delay=int(watcher_cfg.get("shutdown_delay", 60)),
            busy_threshold=float(watcher_cfg.get("busy_threshold", 3.0)),
        )
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
