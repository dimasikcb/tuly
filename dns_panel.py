#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Вкладка «DNS» хаба: панель DnsPanel — профили Quad9 / Xbox / «Дефолтное».

Переехала из tools.pyw verbatim, логика не менялась:
  * вся сетевая работа осталась в dns_core (PowerShell через ps_run из
    winbase) — панель только показывает состояние и запускает операции
    через hub-мост (run_bg/set_status);
  * константы профилей QUAD9_DNS / QUAD9_DOH / XBOX_DNS / XBOX_DOH берутся
    напрямую из dns_core: в старом tools.pyw их не было в списке импорта
    (панель ссылалась на голые имена — клик по «Quad9»/«Xbox» падал бы
    NameError), здесь единый источник — сам dns_core.

Модуль знает только uibase/dns_core; конкретный хаб ему не нужен — всё
общение через hub-мост базовой Panel (run_bg/set_status/toast/post).
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
    BORDER,
    CARD,
    FG,
    MUTED,
    Panel,
    flat_button,
    messagebox,
    px,
    register_palette_consumer,
    ui_font,
)

# Токены приходят реэкспортом; регистрируем глобалы этого модуля потребителем
# палитры, чтобы живая смена темы обновляла и их (панель читает голые имена).
register_palette_consumer(globals())

from dns_core import (  # noqa: E402
    DnsState,
    QUAD9_DNS,
    QUAD9_DOH,
    XBOX_DNS,
    XBOX_DOH,
    dns_apply,
    dns_clear_tunnel,
    dns_dhcp,
    dns_restore_tunnel,
    dns_state,
    dns_test,
)


class DnsPanel(Panel):
    def __init__(self, master: tk.Misc, hub) -> None:
        super().__init__(master, hub)
        self.busy = False
        self.action_buttons: list[tk.Button] = []
        self._build()
        self.refresh_state()

    def _build(self) -> None:
        card = tk.Frame(self, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="x")
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=px(16), pady=px(14))

        head = tk.Frame(inner, bg=CARD)
        head.pack(fill="x")
        tk.Label(head, text="Текущее состояние", bg=CARD, fg=FG,
                 font=ui_font(10, "bold")).pack(side="left")
        self.refresh_btn = flat_button(head, "Обновить", self.refresh_state, kind="ghost",
                                       padx=px(10), pady=px(4))
        self.refresh_btn.pack(side="right")

        self.adapter_var = tk.StringVar(value="—")
        self.dns_var = tk.StringVar(value="—")
        self.tunnel_var = tk.StringVar(value="—")
        for title, variable in (
            ("Адаптер:", self.adapter_var),
            ("DNS сейчас:", self.dns_var),
            ("Туннель:", self.tunnel_var),
        ):
            row = tk.Frame(inner, bg=CARD)
            row.pack(fill="x", pady=(px(8) if title == "Адаптер:" else px(3), 0))
            tk.Label(row, text=title, bg=CARD, fg=MUTED, width=12, anchor="w",
                     font=ui_font(9)).pack(side="left")
            tk.Label(row, textvariable=variable, bg=CARD, fg=FG, anchor="w",
                     font=ui_font(9)).pack(side="left")

        buttons_one = ttk.Frame(self)
        buttons_one.pack(fill="x", pady=(px(14), px(6)))
        buttons_two = ttk.Frame(self)
        buttons_two.pack(fill="x")

        for row, items in (
            (buttons_one, (
                ("Quad9", lambda: self._apply(QUAD9_DNS, QUAD9_DOH, "Quad9")),
                ("Xbox", lambda: self._apply(XBOX_DNS, XBOX_DOH, "Xbox")),
                ("Дефолтное", self._dhcp),
            )),
            (buttons_two, (
                ("Quad9 + очистить туннель", self._quad9_and_tunnel),
                ("Вернуть DNS туннеля", self._restore_tunnel),
                ("Проверить DNS", self._test_dns),
            )),
        ):
            for index, (text, command) in enumerate(items):
                button = flat_button(
                    row, text, command,
                    kind="primary" if index == 0 else "ghost",
                )
                button.pack(side="left", padx=(0 if index == 0 else px(8), 0))
                self.action_buttons.append(button)

        ttk.Label(
            self,
            text="Профиль Xbox блокирует workbuddy.ai — для него используйте Quad9.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(px(12), 0))

        log_frame, self.log_box = self.make_log(self, height=10)
        log_frame.pack(fill="both", expand=True, pady=(px(10), 0))
        self.append_log(self.log_box, "Здесь появятся результаты операций.")

    # -- состояние --------------------------------------------------------- #
    def refresh_state(self) -> None:
        self.refresh_btn.configure(state="disabled")
        self.hub.run_bg(dns_state, self._state_done, self._state_error)

    def _state_done(self, state: DnsState) -> None:
        self.refresh_btn.configure(state="normal")
        self.adapter_var.set(f"{state.name} (ifIndex {state.index})" if state.name else "не найден")
        self.dns_var.set(state.dns or "автоматически (DHCP)")
        if state.tunnel:
            self.tunnel_var.set(f"{state.tunnel} · DNS {state.tunnel_dns or 'авто'}")
        else:
            self.tunnel_var.set("не найден")

    def _state_error(self, exc: Exception) -> None:
        self.refresh_btn.configure(state="normal")
        self.adapter_var.set("—")
        self.dns_var.set("—")
        self.tunnel_var.set("—")
        self.append_log(self.log_box, f"Не удалось получить состояние: {exc}", "error")

    # -- действия ---------------------------------------------------------- #
    def _guard(self) -> bool:
        if self.busy:
            return False
        if not self.hub.ensure_admin("dns"):
            return False
        return True

    def _apply(self, servers: list[str], template: str, label: str) -> None:
        if not self._guard():
            return
        self._run(f"Применяю профиль {label}…", lambda: dns_apply(servers, template))

    def _quad9_and_tunnel(self) -> None:
        if not self._guard():
            return

        def work() -> str:
            first = dns_apply(QUAD9_DNS, QUAD9_DOH)
            second = dns_clear_tunnel()
            return first + "\n" + second

        self._run("Quad9 + очистка DNS туннеля…", work)

    def _dhcp(self) -> None:
        if not self._guard():
            return
        self._run("Сбрасываю DNS на DHCP…", dns_dhcp)

    def _restore_tunnel(self) -> None:
        if not self._guard():
            return
        self._run("Возвращаю DNS туннеля…", dns_restore_tunnel)

    def _test_dns(self) -> None:
        if self.busy:
            return
        self._run("Проверяю разрешение имён…", dns_test)

    def _run(self, title: str, work) -> None:
        self.busy = True
        self._set_buttons(False)
        self.append_log(self.log_box, title)
        self.hub.set_status(title)
        self.hub.run_bg(work, self._action_done, self._action_error)

    def _action_done(self, output: str) -> None:
        self.busy = False
        self._set_buttons(True)
        for line in str(output).splitlines():
            if line.strip():
                self.append_log(self.log_box, line.strip())
        self.hub.set_status("DNS: готово")
        self.refresh_state()

    def _action_error(self, exc: Exception) -> None:
        self.busy = False
        self._set_buttons(True)
        self.append_log(self.log_box, f"Ошибка: {exc}", "error")
        self.hub.set_status("DNS: ошибка")
        messagebox.showerror("DNS", str(exc))

    def _set_buttons(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for button in self.action_buttons:
            button.configure(state=state)
