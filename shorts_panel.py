#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Панель «Shorts Autopilot»: статус службы, запуск/остановка, DaVinci, логи.

Вынесено из tools.pyw без изменения логики (класс ShortsAutopilotPanel —
verbatim):
  * статус службы по PID-файлу shorts-autopilot/state/.single_instance.pid
    (живость процесса — через ctypes/kernel32 OpenProcess);
  * запуск app/main.py под окружением shorts-gui (--ui) и остановка через
    taskkill; веб-панель — http://127.0.0.1:8765;
  * фоновые проверки веб-панели (urllib) и DaVinci Resolve (ps_run) через
    hub.run_bg;
  * хвост logs/app.log в журнал панели, пакет тестов run_tests.bat в фоне.

Модуль знает только uibase/winbase; конкретный хаб ему не нужен — общение
через hub-мост базовой Panel (run_bg/toast) и getattr(self.hub, "current").
tkinter разрешён: это GUI-слой.
"""

from __future__ import annotations

import ctypes
import os
import subprocess
import urllib.request
import webbrowser
from pathlib import Path

import tkinter as tk

from uibase import (
    BG,
    BORDER,
    CARD,
    FG,
    MUTED,
    OK,
    Panel,
    flat_button,
    px,
    register_palette_consumer,
    ui_font,
)
from winbase import APP_DIR, ps_run

# Токены приходят из uibase; регистрируем глобалы этого модуля потребителем
# палитры, чтобы живая смена темы обновляла и их (панель читает голые имена).
register_palette_consumer(globals())


class ShortsAutopilotPanel(Panel):
    """Управление приложением Shorts Autopilot: статус, запуск/остановка, DaVinci, логи."""

    def __init__(self, master: tk.Misc, hub) -> None:
        super().__init__(master, hub)
        self.shorts_dir = APP_DIR / "shorts-autopilot"
        self.python_exe = Path(r"C:\Users\admin\.workbuddy-ai\binaries\python\envs\shorts-gui\Scripts\python.exe")
        self.pythonw_exe = Path(r"C:\Users\admin\.workbuddy-ai\binaries\python\envs\shorts-gui\Scripts\pythonw.exe")
        self._running_pid: int | None = None
        self._poll_job = None
        self._last_log_size = 0
        self._build()
        self.refresh_status()
        self.update_log(initial=True)
        self._schedule_poll()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        # -- Карточки статуса сверху --
        cards = tk.Frame(self, bg=BG)
        cards.grid(row=0, column=0, sticky="ew", pady=(0, px(12)))
        cards.columnconfigure(0, weight=1)
        cards.columnconfigure(1, weight=1)
        cards.columnconfigure(2, weight=1)

        # Карточка 1: Служба Shorts Autopilot
        c1 = tk.Frame(cards, bg=CARD, highlightbackground=BORDER, highlightthickness=1, padx=px(14), pady=px(10))
        c1.grid(row=0, column=0, sticky="nsew", padx=(0, px(6)))
        tk.Label(c1, text="Служба Autopilot", bg=CARD, fg=MUTED, font=ui_font(9)).pack(anchor="w")
        self.lbl_svc_status = tk.Label(c1, text="○ Остановлен", bg=CARD, fg=FG, font=ui_font(12, "bold"))
        self.lbl_svc_status.pack(anchor="w", pady=(px(4), 0))
        self.lbl_svc_pid = tk.Label(c1, text="Фоновый мониторинг ссылок", bg=CARD, fg=MUTED, font=ui_font(8))
        self.lbl_svc_pid.pack(anchor="w")

        # Карточка 2: Веб-панель
        c2 = tk.Frame(cards, bg=CARD, highlightbackground=BORDER, highlightthickness=1, padx=px(14), pady=px(10))
        c2.grid(row=0, column=1, sticky="nsew", padx=px(4))
        tk.Label(c2, text="Веб-панель (UI)", bg=CARD, fg=MUTED, font=ui_font(9)).pack(anchor="w")
        self.lbl_web_status = tk.Label(c2, text="○ Офлайн", bg=CARD, fg=MUTED, font=ui_font(12, "bold"))
        self.lbl_web_status.pack(anchor="w", pady=(px(4), 0))
        tk.Label(c2, text="http://127.0.0.1:8765", bg=CARD, fg=MUTED, font=ui_font(8)).pack(anchor="w")

        # Карточка 3: DaVinci Resolve
        c3 = tk.Frame(cards, bg=CARD, highlightbackground=BORDER, highlightthickness=1, padx=px(14), pady=px(10))
        c3.grid(row=0, column=2, sticky="nsew", padx=(px(6), 0))
        tk.Label(c3, text="DaVinci Resolve", bg=CARD, fg=MUTED, font=ui_font(9)).pack(anchor="w")
        self.lbl_davinci_status = tk.Label(c3, text="○ Закрыт", bg=CARD, fg=MUTED, font=ui_font(12, "bold"))
        self.lbl_davinci_status.pack(anchor="w", pady=(px(4), 0))
        self.lbl_davinci_info = tk.Label(c3, text="Timeline: 1080x1920 @ 60 fps", bg=CARD, fg=MUTED, font=ui_font(8))
        self.lbl_davinci_info.pack(anchor="w")

        # -- Кнопки управления --
        toolbar = tk.Frame(self, bg=BG)
        toolbar.grid(row=1, column=0, sticky="ew", pady=(0, px(12)))

        self.btn_start = flat_button(toolbar, "▶ Запустить Autopilot", self.start_autopilot, kind="primary")
        self.btn_start.pack(side="left")

        self.btn_stop = flat_button(toolbar, "⏹ Остановить", self.stop_autopilot, kind="danger")
        self.btn_stop.pack(side="left", padx=(px(8), 0))

        self.btn_web = flat_button(toolbar, "🌐 Открыть веб-панель", self.open_webui, kind="ghost")
        self.btn_web.pack(side="left", padx=(px(8), 0))

        self.btn_tests = flat_button(toolbar, "🧪 Прогнать тесты", self.run_tests_bg, kind="ghost")
        self.btn_tests.pack(side="left", padx=(px(8), 0))

        self.btn_folder = flat_button(toolbar, "📂 Папка проекта", self.open_folder, kind="ghost")
        self.btn_folder.pack(side="right")

        self.btn_cfg = flat_button(toolbar, "⚙ config.json", self.open_config, kind="ghost")
        self.btn_cfg.pack(side="right", padx=(0, px(8)))

        # -- Журнал событий --
        log_frame, self.log_box = self.make_log(self, height=12, label="Лог Shorts Autopilot")
        log_frame.grid(row=2, column=0, sticky="nsew")
        self.append_log(self.log_box, "Shorts Autopilot подключён в качестве приложения.")

    def _schedule_poll(self) -> None:
        if self._poll_job:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass
        self._poll_job = self.after(3000, self._on_poll)

    def _on_poll(self) -> None:
        if not self.winfo_exists():
            return
        if getattr(self.hub, "current", None) == "shorts":
            self.refresh_status()
            self.update_log()
        self._schedule_poll()

    def on_show(self) -> None:
        self.refresh_status()
        self.update_log()

    def shutdown(self) -> None:
        if self._poll_job:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass
            self._poll_job = None

    def refresh_status(self) -> None:
        pid = self._find_pid()
        self._running_pid = pid
        if pid:
            self.lbl_svc_status.configure(text=f"● Работает (PID {pid})", fg=OK)
            self.lbl_svc_pid.configure(text="Перехват ссылок и автоконвейер активны")
            self.btn_start.configure(state="disabled")
            self.btn_stop.configure(state="normal")
        else:
            self.lbl_svc_status.configure(text="○ Остановлен", fg=MUTED)
            self.lbl_svc_pid.configure(text="Нажмите «Запустить Autopilot» для старта")
            self.btn_start.configure(state="normal")
            self.btn_stop.configure(state="disabled")

        if getattr(self, "_refreshing_bg", False):
            return
        self._refreshing_bg = True

        def bg_checks() -> tuple[bool, bool]:
            web = self._check_webui()
            davinci = self._check_process("Resolve.exe")
            return web, davinci

        def on_done(res: tuple[bool, bool]) -> None:
            self._refreshing_bg = False
            if not self.winfo_exists():
                return
            web_ok, davinci_ok = res
            if web_ok:
                self.lbl_web_status.configure(text="● Онлайн", fg=OK)
                self.btn_web.configure(state="normal")
            else:
                self.lbl_web_status.configure(text="○ Офлайн", fg=MUTED)
            if davinci_ok:
                self.lbl_davinci_status.configure(text="● Запущен", fg=OK)
            else:
                self.lbl_davinci_status.configure(text="○ Закрыт", fg=MUTED)

        def on_err(_exc) -> None:
            self._refreshing_bg = False

        self.hub.run_bg(bg_checks, on_done, on_err)

    def _find_pid(self) -> int | None:
        pid_file = self.shorts_dir / "state" / ".single_instance.pid"
        if pid_file.exists():
            try:
                pid = int(pid_file.read_text(encoding="utf-8").strip())
                if pid > 0 and self._is_pid_alive(pid):
                    return pid
            except (OSError, ValueError):
                pass
        return None

    def _is_pid_alive(self, pid: int) -> bool:
        try:
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(0x1000, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
        except Exception:
            pass
        return False

    def _check_process(self, exe_name: str) -> bool:
        try:
            exe_low = exe_name.lower()
            return any(exe_low in p.lower() for p in ps_run("Get-Process | Select-Object -ExpandProperty ProcessName").splitlines())
        except Exception:
            return False

    def _check_webui(self) -> bool:
        try:
            req = urllib.request.Request("http://127.0.0.1:8765/api/state", headers={"User-Agent": "Tools/1.0"})
            with urllib.request.urlopen(req, timeout=0.8) as resp:
                return resp.status == 200
        except Exception:
            return False

    def start_autopilot(self) -> None:
        py_bin = self.pythonw_exe if self.pythonw_exe.exists() else (self.python_exe if self.python_exe.exists() else Path("pythonw.exe"))
        main_script = self.shorts_dir / "app" / "main.py"
        if not main_script.exists():
            self.hub.toast("Скрипт app/main.py не найден!", kind="error")
            return
        try:
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"
            env["PYTHONIOENCODING"] = "utf-8"
            env["TMPDIR"] = str(self.shorts_dir / "state" / "pytest-tmp")
            subprocess.Popen(
                [str(py_bin), str(main_script), "--ui"],
                cwd=str(self.shorts_dir),
                env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            self.hub.toast("Shorts Autopilot запущен", kind="ok")
            self.append_log(self.log_box, "Запущена служба Shorts Autopilot (--ui)...")
            self.after(1500, self.refresh_status)
        except Exception as exc:
            self.hub.toast(f"Ошибка запуска: {exc}", kind="error")
            self.append_log(self.log_box, f"Ошибка запуска: {exc}", tag="error")

    def stop_autopilot(self) -> None:
        pid = self._find_pid()
        if not pid:
            self.hub.toast("Процесс не найден", kind="warn")
            self.refresh_status()
            return
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, timeout=5)
            lock = self.shorts_dir / "state" / ".single_instance.pid"
            if lock.exists():
                try:
                    lock.unlink(missing_ok=True)
                except Exception:
                    pass
            self.hub.toast("Shorts Autopilot остановлен", kind="ok")
            self.append_log(self.log_box, f"Служба (PID {pid}) остановлена.")
            self.after(500, self.refresh_status)
        except Exception as exc:
            self.hub.toast(f"Ошибка остановки: {exc}", kind="error")
            self.append_log(self.log_box, f"Ошибка остановки: {exc}", tag="error")

    def open_webui(self) -> None:
        if not self._check_webui():
            self.hub.toast("Запускаю службу Shorts Autopilot для веб-панели...", kind="ok")
            self.start_autopilot()
            self.after(1800, lambda: webbrowser.open("http://127.0.0.1:8765"))
            return
        webbrowser.open("http://127.0.0.1:8765")

    def open_folder(self) -> None:
        os.startfile(str(self.shorts_dir))

    def open_config(self) -> None:
        cfg = self.shorts_dir / "config.json"
        if cfg.exists():
            os.startfile(str(cfg))

    def run_tests_bg(self) -> None:
        self.btn_tests.configure(state="disabled")
        self.append_log(self.log_box, "Запуск пакета тестов run_tests.bat в фоне...")

        def work():
            bat = self.shorts_dir / "run_tests.bat"
            res = subprocess.run([str(bat)], cwd=str(self.shorts_dir), capture_output=True, text=True, encoding="utf-8", errors="replace")
            return res.returncode, res.stdout, res.stderr

        def on_done(result):
            code, out, err = result
            self.btn_tests.configure(state="normal")
            if code == 0:
                self.hub.toast("Все тесты успешно пройдены!", kind="ok")
                self.append_log(self.log_box, "Тесты run_tests.bat: ВСЕ 949 ТЕСТОВ И 30 СЦЕНАРИЕВ ПРОЙДЕНЫ УСПЕШНО!", tag="ok")
            else:
                self.hub.toast("Тесты завершились с ошибкой", kind="error")
                self.append_log(self.log_box, f"Ошибка тестов:\n{err or out}", tag="error")

        def on_error(exc):
            self.btn_tests.configure(state="normal")
            self.hub.toast(f"Сбой тестов: {exc}", kind="error")
            self.append_log(self.log_box, f"Сбой запуска тестов: {exc}", tag="error")

        self.hub.run_bg(work, on_done, on_error)

    def update_log(self, initial: bool = False) -> None:
        log_file = self.shorts_dir / "logs" / "app.log"
        if not log_file.exists():
            return
        try:
            size = log_file.stat().st_size
            if size == getattr(self, "_last_log_size", 0) and not initial:
                return
            self._last_log_size = size
            with open(log_file, "r", encoding="utf-8", errors="replace") as f:
                lines = f.readlines()[-30:]
            if initial:
                self.log_box.delete("1.0", "end")
            for line in (lines if initial else lines[-10:]):
                tag = None
                if "ERROR" in line:
                    tag = "error"
                elif "WARNING" in line:
                    tag = "warn"
                elif "УСПЕШНО" in line or "готово" in line:
                    tag = "ok"
                self.append_log(self.log_box, line.rstrip(), tag=tag)
        except Exception:
            pass
