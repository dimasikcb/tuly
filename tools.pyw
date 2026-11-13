#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Мои инструменты — единое тёмное приложение для Windows.

В одном окне:
  * AutoClicker          — клики по точкам, областям и картинкам;
  * Пустые файлы и папки — поиск и удаление пустых файлов и папок;
  * DNS                  — Quad9 / Xbox / Дефолтное (DoH для первых двух);
  * Дневник МО           — оценки, «5» до 4.50 и запас прочности (diary-grades/).

Запуск: двойной клик по tools.pyw (или pythonw tools.pyw).
"""

from __future__ import annotations

import argparse
import ctypes
import json
import queue
import random
import stat
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, simpledialog, ttk

from apps_catalog import APP_COMPANIONS, AppEntry, app_catalog, expand_companions, filter_apps, normalize_apps
from bypass_core import (
    HAPP_SOCKS_DEFAULT,
    _find_happ,
    bypass_admin,
    bypass_apps_route_disable,
    bypass_apps_route_enable,
    bypass_apply,
    bypass_auto_heal_if_leak,
    bypass_cfg,
    bypass_cfg_save,
    bypass_check_direct_ip,
    bypass_check_ip,
    bypass_check_proxy_ip,
    bypass_compare_connections,
    bypass_download,
    bypass_happ_show,
    bypass_status,
    bypass_stop,
    happ_connected,
    happ_mode_write,
    happ_tun_disable,
    happ_tunnel_restart,
    proxifyre_rescan,
    proxifyre_restart,
    proxifyre_start,
    proxifyre_stop,
    set_windows_system_proxy,
)
import shutdown_watcher as wsh
from winbase import (
    APP_DIR,
    APP_FILE,
    CONFIG_PATH,
    NO_WINDOW,
    TARGETS_DIR,
    config_read,
    config_update,
    config_write,
    find_tool,
    is_admin,
    log_exception,
    ps_run,
    pythonw_executable,
    relaunch_as_admin,
    run_tool,
    tool_env,
)


from scroll_settings import steps_per_wheel as _scroll_step

# --------------------------------------------------------------------------- #
#  Единая тема: HEX-цвета живут ТОЛЬКО в theme/tokens.py. Прослойка токенов,
#  базовая Panel, тёмные диалоги и UI-обёртки переехали в uibase.py — здесь
#  только реэкспорт. Смена темы обновляет токены через реестр потребителей
#  uibase (см. register_palette_consumer) и apple_tk.recolor().
# --------------------------------------------------------------------------- #
_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import theme as _theme
from theme import apple_tk as _apple_tk

from uibase import (
    ACCENT,
    ACCENT_HOVER,
    AVATAR_COLORS,
    BADGE_BAD_BG,
    BADGE_BAD_INK,
    BADGE_GOOD_BG,
    BADGE_GOOD_INK,
    BADGE_NEUTRAL_BG,
    BADGE_WARN_BG,
    BADGE_WARN_INK,
    BG,
    BORDER,
    BRAND_LABEL,
    CARD,
    CARD_HOVER,
    COPIED_BG,
    DANGER,
    DANGER_HOVER,
    DarkMessageBox,
    FG,
    FIELD,
    KNOB,
    LOG_BG,
    LOG_FG,
    MUTED,
    OK,
    ON_AVATAR,
    OVERLAY_BACKDROP,
    OVERLAY_TIMER,
    Panel,
    ROW_SELECTED,
    SIDEBAR,
    SIDEBAR_ACTIVE,
    SIDEBAR_HOVER,
    SIDEBAR_SUBTITLE,
    SIDEBAR_TITLE,
    TAG_DIR,
    TAG_FILE,
    TRACK_DISABLED,
    TRACK_OFF,
    WARN,
    _EXTRA_ROLES,
    _LEGACY_NAMES,
    _iter_widgets,
    _refresh_palette,
    apply_dark_theme,
    badge_colors,
    dark_check,
    dark_text,
    enable_dark_titlebar,
    enable_input_paste,
    flat_button,
    fmt_size,
    messagebox,
    mono_font,
    px,
    register_palette_consumer,
    set_scale,
    tc,
    ui_font,
)

# Токены приходят реэкспортом; регистрируем глобалы этого модуля потребителем
# палитры, чтобы живая смена темы обновляла и их (панели читают голые имена).
register_palette_consumer(globals())

# --------------------------------------------------------------------------- #
#  Движки панелей: логика без tkinter (инварианты слоёв — tests/test_arch.py).
# --------------------------------------------------------------------------- #
from cleanup_core import (  # noqa: E402
    DeleteResult,
    Plan,
    check_blocked,
    delete_plan,
    risk_warning,
    scan_empty,
)
from dns_core import (  # noqa: E402
    DnsState,
    QUAD9_DNS,
    dns_apply,
    dns_clear_tunnel,
    dns_dhcp,
    dns_restore_tunnel,
    dns_state,
    dns_test,
)
from github_core import (  # noqa: E402
    GITHUB_DEFAULT_ROOT,
    gh_authed,
    gh_login,
    gh_save_token,
    gh_token_from_hosts,
    github_repos,
    local_scan_roots,
    mask_token,
    match_local_repos,
    push_folder_to_github,
    sanitize_repo_name,
    scan_local_repos,
    send_to_recycle_bin,
    token_looks_valid,
    update_local_repo,
)

# --------------------------------------------------------------------------- #
#  Панели: каждая в своём модуле (tools.pyw — только Hub и реэкспорты).
# --------------------------------------------------------------------------- #
from aiscreen_panel import AIScreenAgentPanel  # noqa: E402
from autoclicker_panel import AutoClickerPanel  # noqa: E402
from bypass_panel import BypassPanel, Segmented, Switch  # noqa: E402
from clean_panel import CleanPanel  # noqa: E402
from diary_panel import DiaryGradesPanel  # noqa: E402
from dns_panel import DnsPanel  # noqa: E402
from github_panel import GitHubPanel  # noqa: E402
from leads_panel import LeadsPanel, MailWindow  # noqa: E402
from settings_panel import SettingsPanel  # noqa: E402
from shorts_panel import ShortsAutopilotPanel  # noqa: E402
from watcher_panel import ShutdownWatcherPanel  # noqa: E402



NAV = (
    ("autoclicker", "AutoClicker", "клики по экрану"),
        ("clean", "Пустые файлы", "0 байт и пустые папки"),
    ("dns", "DNS", "Quad9 · Xbox · Дефолтное"),
    ("github", "GitHub", "бэкап проектов"),
    ("bypass", "Bypass", "обход DPI: глобально и по приложениям"),
    ("leads", "Лиды 2GIS", "контакты без сайта"),
    ("diary", "Дневник МО", "оценки · запас до пятёрки"),
    ("shorts", "Shorts Autopilot", "клипы · DaVinci · YouTube"),
    ("ai_agent", "AI Screen Agent", "Computer Use · тесты"),
    ("shutdown", "Страж выключения", "когда агенты закончат — выключить ПК"),
    ("settings", "Настройки", "скролл · темы · прочее"),
)

HEADERS = {
    "autoclicker": (
        "AutoClicker",
        "Клики по точкам, областям и картинкам. F6 — старт/стоп, F7 — точка под курсором.",
    ),
    "clean": (
        "Пустые файлы и папки",
        "Сначала проверка — удаление только после подтверждения.",
    ),
    "dns": (
        "DNS",
        "Профили Quad9, Xbox и «Дефолтное» (автоматический DNS от DHCP). DoH для Quad9 и Xbox. Нужны права администратора.",
    ),
    "github": (
        "GitHub",
        "Список репозиториев, заливка папок, скачивание и обновление. Нужны gh и git.",
    ),
    "bypass": (
        "Bypass",
        "Обход DPI: весь трафик или только выбранные программы. Happ и GoodbyeDPI.",
    ),
    "leads": (
        "Лиды 2GIS",
        "Организации из выдачи 2GIS: телефон, e-mail, сайт, мессенджеры и приоритет под аутрич. Горячие копятся в пуле на 5 дней.",
    ),
    "diary": (
        "Дневник МО · Контроль балла",
        "Средний балл, «5» до 4.50 и запас прочности. Вход через Госуслуги — один раз.",
    ),
    "shorts": (
        "Shorts Autopilot",
        "Автоматизация YouTube Shorts: скачивание через MAX, SEO-генерация через Google AI, монтаж в DaVinci Resolve и загрузка на YouTube.",
    ),
    "ai_agent": (
        "AI Screen Agent (Computer Use)",
        "Автономное прохождение тестов через Gemini Vision. Захват экрана, выбор ответов и клики мышкой.",
    ),
    "shutdown": (
        "Страж выключения",
        "Следит за ИИ-агентами (WorkBuddy, OpenCode, Antigravity) и выключает ПК, когда все завершат работу.",
    ),
    "settings": (
        "Настройки",
        "Параметры программы: чувствительность прокрутки колёсиком мыши.",
    ),
}


class NavItem(tk.Frame):
    def __init__(self, parent: tk.Misc, title: str, subtitle: str, command) -> None:
        super().__init__(parent, bg=SIDEBAR, cursor="hand2")
        self.command = command
        self.active = False

        self.accent = tk.Frame(self, bg=SIDEBAR, width=px(3))
        self.accent.pack(side="left", fill="y")
        self.body = tk.Frame(self, bg=SIDEBAR)
        self.body.pack(side="left", fill="both", expand=True, padx=(px(11), px(12)), pady=px(9))

        self.title_label = tk.Label(
            self.body, text=title, bg=SIDEBAR, fg=SIDEBAR_TITLE,
            font=ui_font(10, "bold"), anchor="w", cursor="hand2",
        )
        self.title_label.pack(fill="x")
        self.subtitle_label = tk.Label(
            self.body, text=subtitle, bg=SIDEBAR, fg=SIDEBAR_SUBTITLE,
            font=ui_font(8), anchor="w", cursor="hand2",
        )
        self.subtitle_label.pack(fill="x")

        for widget in (self, self.body, self.title_label, self.subtitle_label):
            widget.bind("<Button-1>", lambda _event: self.command())
            widget.bind("<Enter>", lambda _event: self._hover(True))
            widget.bind("<Leave>", lambda _event: self._hover(False))

    def _paint(self) -> None:
        background = SIDEBAR_ACTIVE if self.active else SIDEBAR
        for widget in (self, self.body):
            widget.configure(bg=background)
        self.title_label.configure(bg=background, fg=FG if self.active else SIDEBAR_TITLE)
        self.subtitle_label.configure(bg=background, fg=MUTED if self.active else SIDEBAR_SUBTITLE)
        self.accent.configure(bg=ACCENT if self.active else background)

    def _hover(self, entering: bool) -> None:
        if self.active:
            return
        background = SIDEBAR_HOVER if entering else SIDEBAR
        for widget in (self, self.body):
            widget.configure(bg=background)
        self.title_label.configure(bg=background)
        self.subtitle_label.configure(bg=background)
        self.accent.configure(bg=background)

    def set_active(self, active: bool) -> None:
        self.active = active
        self._paint()


class Hub(tk.Tk):
    def __init__(self, initial_tab: str = "autoclicker") -> None:
        super().__init__()
        set_scale(self.winfo_fpixels("1i") / 96.0)
        self.title("Мои инструменты")
        width = min(px(1180), self.winfo_screenwidth() - px(40))
        height = min(px(820), self.winfo_screenheight() - px(90))
        self.geometry(f"{width}x{height}")
        self.minsize(min(px(1000), width), min(px(700), height))
        self.configure(bg=BG)
        apply_dark_theme(self)
        self.option_add("*Font", ui_font(9))
        self._theme_resolved = _theme.mode()
        self._theme_check_at = 0.0

        self.events: queue.Queue = queue.Queue()
        self.panels: dict[str, Panel] = {}
        self.nav_items: dict[str, NavItem] = {}
        self.current = ""
        self._closing = False

        self.factories = {
            "autoclicker": lambda: AutoClickerPanel(self.content, self),
            "clean": lambda: CleanPanel(self.content, self),
            "dns": lambda: DnsPanel(self.content, self),
            "github": lambda: GitHubPanel(self.content, self),
            "bypass": lambda: BypassPanel(self.content, self),
            "leads": lambda: LeadsPanel(self.content, self),
            "diary": lambda: DiaryGradesPanel(self.content, self),
            "shorts": lambda: ShortsAutopilotPanel(self.content, self),
            "ai_agent": lambda: AIScreenAgentPanel(self.content, self),
            "shutdown": lambda: ShutdownWatcherPanel(self.content, self),
            "settings": lambda: SettingsPanel(self.content, self),
        }

        self._build()
        self.show(initial_tab if initial_tab in self.factories else "autoclicker")
        self.after(70, self._poll)
        self.protocol("WM_DELETE_WINDOW", self.shutdown)
        enable_dark_titlebar(self)
        _theme.on_change(self._apply_theme_mode)
        # Обход по программам (ProxiFyre) выключен по умолчанию при каждом
        # запуске тулз: служба переживает закрытие окна, и без этого при старте
        # «сама уже работает маршрутизация». VPN (Happ) не трогаем — только
        # останавливаем перенаправление выбранных программ. Фон: остановка
        # службы не должна задерживать открытие окна.
        threading.Thread(target=self._stop_apps_route_on_launch, daemon=True).start()

    @staticmethod
    def _stop_apps_route_on_launch() -> None:
        try:
            proxifyre_stop()
        except Exception:  # noqa: BLE001 — старт не должен падать из-за службы
            pass

    # -- разметка ---------------------------------------------------------- #
    def _build(self) -> None:
        sidebar = tk.Frame(self, bg=SIDEBAR, width=px(234))
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        brand = tk.Frame(sidebar, bg=SIDEBAR)
        brand.pack(fill="x", padx=px(20), pady=(px(24), px(18)))
        tk.Label(brand, text="МОИ", bg=SIDEBAR, fg=BRAND_LABEL,
                 font=ui_font(8, "bold")).pack(anchor="w")
        tk.Label(brand, text="Инструменты", bg=SIDEBAR, fg=FG,
                 font=ui_font(15, "bold")).pack(anchor="w")

        nav = tk.Frame(sidebar, bg=SIDEBAR)
        nav.pack(fill="x")
        for key, title, subtitle in NAV:
            item = NavItem(nav, title, subtitle, lambda k=key: self.show(k))
            item.pack(fill="x", pady=px(1))
            self.nav_items[key] = item

        bottom = tk.Frame(sidebar, bg=SIDEBAR)
        bottom.pack(side="bottom", fill="x", padx=px(16), pady=px(16))
        admin = is_admin()
        tk.Label(
            bottom,
            text="● права администратора" if admin else "● обычные права",
            bg=SIDEBAR,
            fg=OK if admin else MUTED,
            font=ui_font(8),
            anchor="w",
        ).pack(fill="x")
# admin button removed
        tk.Label(
            bottom,
            text=f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            bg=SIDEBAR,
            fg=BRAND_LABEL,
            font=ui_font(8),
            anchor="w",
        ).pack(fill="x", pady=(px(10), 0))
        self.theme_btn = tk.Button(
            bottom, text=self._theme_label(), command=self._cycle_theme,
            bg=SIDEBAR, fg=MUTED, activebackground=SIDEBAR_ACTIVE,
            activeforeground=FG, relief="flat", bd=0, anchor="w",
            font=ui_font(8), cursor="hand2", pady=px(2),
        )
        self.theme_btn.pack(fill="x", pady=(px(10), 0))

        main = tk.Frame(self, bg=BG)
        main.pack(side="left", fill="both", expand=True)

        status_bar = tk.Frame(main, bg=SIDEBAR, height=px(28))
        status_bar.pack(side="bottom", fill="x")
        status_bar.pack_propagate(False)
        self.status_var = tk.StringVar(value="Готово")
        tk.Label(status_bar, textvariable=self.status_var, bg=SIDEBAR, fg=MUTED,
                 font=ui_font(8), anchor="w").pack(side="left", padx=px(14))
        tk.Label(status_bar, text="Ctrl+1…9 — переключение разделов", bg=SIDEBAR,
                 fg=BRAND_LABEL, font=ui_font(8), anchor="e").pack(side="right", padx=px(14))

        head = tk.Frame(main, bg=BG)
        head.pack(fill="x", padx=px(26), pady=(px(22), px(12)))
        self.title_var = tk.StringVar()
        self.subtitle_var = tk.StringVar()
        tk.Label(head, textvariable=self.title_var, bg=BG, fg=FG,
                 font=ui_font(18, "bold"), anchor="w").pack(fill="x")
        tk.Label(head, textvariable=self.subtitle_var, bg=BG, fg=MUTED,
                 font=ui_font(9), anchor="w").pack(fill="x", pady=(px(4), 0))

        self.content = tk.Frame(main, bg=BG)
        self.content.pack(fill="both", expand=True, padx=px(26), pady=(0, px(14)))
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)

        for index, (key, _title, _subtitle) in enumerate(NAV, 1):
            key_char = str(index % 10)
            self.bind_all(f"<Control-Key-{key_char}>", lambda _event, k=key: self.show(k))

    # -- тема -------------------------------------------------------------- #
    def _theme_label(self) -> str:
        return {
            "auto": "◐ Тема: как в системе",
            "light": "☀ Тема: светлая",
            "dark": "☾ Тема: тёмная",
        }[_theme.raw_mode()]

    def _cycle_theme(self) -> None:
        order = ("auto", "light", "dark")
        current = _theme.raw_mode()
        _theme.set_mode(order[(order.index(current) + 1) % len(order)])

    def _apply_theme_mode(self, resolved: str) -> None:
        """Живая перерисовка: токены → ttk-стили → виджеты → Canvas → DWM."""
        if resolved == self._theme_resolved:
            return
        previous = self._theme_resolved
        self._theme_resolved = resolved
        _refresh_palette()
        apply_dark_theme(self)
        _apple_tk.recolor(self, previous, resolved)
        _apple_tk.redraw(self)
        enable_dark_titlebar(self)
        if self.winfo_exists():
            try:
                self.theme_btn.configure(text=self._theme_label())
            except (AttributeError, tk.TclError):
                pass

    # -- разделы ----------------------------------------------------------- #
    def show(self, key: str) -> None:
        panel = self.panels.get(key)
        fresh = panel is None
        if fresh:
            panel = self.factories[key]()
            panel.grid(row=0, column=0, sticky="nsew")
            self.panels[key] = panel
            enable_input_paste(panel)
        if not fresh:
            # вкладка уже создана — обновляем данные при каждом показе
            hook = getattr(panel, "on_show", None)
            if hook is not None:
                try:
                    hook()
                except Exception as exc:  # noqa: BLE001
                    log_exception()
                    self.toast(f"Сбой обновления вкладки: {exc}", kind="error")
        panel.tkraise()
        self.current = key
        for item_key, item in self.nav_items.items():
            item.set_active(item_key == key)
        title, subtitle = HEADERS.get(key, (key.title(), ""))
        self.title_var.set(title)
        self.subtitle_var.set(subtitle)

    # -- фон и события ----------------------------------------------------- #
    def post(self, callback) -> None:
        self.events.put(callback)

    def run_bg(self, work, on_done, on_error) -> None:
        def runner() -> None:
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001
                self.post(lambda exc=exc: on_error(exc))
            else:
                self.post(lambda result=result: on_done(result))

        threading.Thread(target=runner, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                callback = self.events.get_nowait()
                try:
                    callback()
                except Exception:  # noqa: BLE001
                    log_exception()
        except queue.Empty:
            pass
        now = time.monotonic()
        if now - self._theme_check_at > 2.0:
            self._theme_check_at = now
            if _theme.raw_mode() == "auto":
                resolved = _theme.mode()
                if resolved != self._theme_resolved:
                    self._apply_theme_mode(resolved)
        self.after(70, self._poll)

    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    def toast(self, message: str, *, kind: str = "ok", ms: int = 2200) -> None:
        """Всплывающее подтверждение поверх окна — видно, что действие сработало."""
        label = getattr(self, "_toast_label", None)
        if label is None:
            label = tk.Label(
                self, bg=CARD, fg=FG, font=ui_font(10, "bold"),
                padx=px(18), pady=px(10),
                highlightthickness=1, highlightbackground=BORDER, bd=0,
            )
            self._toast_label = label
        colors = {"ok": OK, "warn": WARN, "error": DANGER}
        label.configure(text=message, fg=colors.get(kind, FG))
        label.place(relx=0.5, rely=1.0, y=-px(46), anchor="s")
        label.lift()
        job = getattr(self, "_toast_job", None)
        if job:
            try:
                self.after_cancel(job)
            except Exception:  # noqa: BLE001
                pass
        self._toast_job = self.after(ms, label.place_forget)

    # -- права ------------------------------------------------------------- #
    def ensure_admin(self, tab: str) -> bool:
        if is_admin():
            return True
        if not messagebox.askyesno(
            "Нужны права администратора",
            "Для этого действия нужны права администратора.\n\n"
            "Перезапустить приложение от имени администратора?",
            parent=self,
        ):
            return False
        if relaunch_as_admin(tab):
            self.after(150, self.shutdown)
            return False
        messagebox.showerror(
            "Права администратора",
            "Не удалось перезапустить приложение с правами администратора.\n"
            "Запрос UAC был отклонён.",
            parent=self,
        )
        return False

    def request_admin(self) -> None:
        tab = self.current or "dns"
        if is_admin():
            messagebox.showinfo("Права администратора",
                                "Приложение уже запущено от администратора.", parent=self)
            return
        self.ensure_admin(tab)

    # -- выход ------------------------------------------------------------- #
    def shutdown(self) -> None:
        if self._closing:
            return
        self._closing = True
        for p in list(self.panels.values()):
            if hasattr(p, "shutdown"):
                try:
                    p.shutdown()
                except Exception:  # noqa: BLE001
                    pass
        self.destroy()


def main() -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):
        pass
    parser = argparse.ArgumentParser(description="Мои инструменты")
    parser.add_argument("--tab", choices=[key for key, _t, _s in NAV], default="autoclicker")
    args, _unknown = parser.parse_known_args()

    # pyautogui.PAUSE / FAILSAFE настраиваются в load_autoclicker_deps()
    # сразу после ленивого импорта — здесь он ещё не загружен.

    # Собранный Tools.exe запрашивает права манифестом. Исходник запускаем
    # обычным процессом: иначе двойной клик сначала закрывает pythonw, а затем
    # ждёт UAC-перезапуска, из-за чего кажется, что tools.pyw не открывается.
    # Для конкретных действий права запрашиваются через ensure_admin().

    hub = Hub(initial_tab=args.tab)
    hub.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
