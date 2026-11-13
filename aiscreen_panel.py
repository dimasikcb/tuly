#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Панель «AI Screen Agent» (Computer Use): окно агента над тестами, Gemini Vision.

Вынесено из tools.pyw без изменения логики (класс AIScreenAgentPanel —
verbatim):
  * запуск подпроцесса ai-screen-agent/screen_agent.py через subprocess.Popen
    (pythonw_executable() + CREATE_NO_WINDOW), остановка через terminate и
    taskkill по PID из WMI-опроса;
  * опрос живых процессов агента через PowerShell (Get-CimInstance
    Win32_Process по CommandLine LIKE '%screen_agent.py%') — механизм
    сохранён как есть;
  * Gemini API Key / модель / ROI хранятся в ai-screen-agent/config.json,
    ключ вставляется из буфера обмена и сохраняется кнопкой.

Модуль знает только uibase/winbase; конкретный хаб ему не нужен — общение
через hub-мост базовой Panel (toast) и getattr(self.hub, "current").
tkinter разрешён: это GUI-слой.
"""

from __future__ import annotations

import json
import os
import subprocess

import tkinter as tk
from tkinter import ttk

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
    tc,
    ui_font,
)
from winbase import APP_DIR, ps_run, pythonw_executable

# Токены приходят из uibase; регистрируем глобалы этого модуля потребителем
# палитры, чтобы живая смена темы обновляла и их (панель читает голые имена).
register_palette_consumer(globals())


class AIScreenAgentPanel(Panel):
    """AI Screen Bridge (Computer Use): авто-прохождение тестов через Gemini Vision."""

    def __init__(self, master: tk.Misc, hub) -> None:
        super().__init__(master, hub)
        self.dir = APP_DIR / "ai-screen-agent"
        self.script = self.dir / "screen_agent.py"
        self.config_file = self.dir / "config.json"
        self._proc: subprocess.Popen | None = None
        self._poll_job = None
        self._build()
        self.refresh()
        self._schedule_poll()

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        cards = tk.Frame(self, bg=BG)
        cards.grid(row=0, column=0, sticky="ew", pady=(0, px(12)))
        cards.columnconfigure(0, weight=1)
        cards.columnconfigure(1, weight=1)
        cards.columnconfigure(2, weight=1)

        c1 = tk.Frame(cards, bg=CARD, highlightbackground=BORDER, highlightthickness=1, padx=px(14), pady=px(10))
        c1.grid(row=0, column=0, sticky="nsew", padx=(0, px(6)))
        tk.Label(c1, text="Статус агента", bg=CARD, fg=MUTED, font=ui_font(9)).pack(anchor="w")
        self.lbl_status = tk.Label(c1, text="○ Остановлен", bg=CARD, fg=FG, font=ui_font(12, "bold"))
        self.lbl_status.pack(anchor="w", pady=(px(4), 0))
        self.lbl_info = tk.Label(c1, text="Плавающее окно поверх тестов", bg=CARD, fg=MUTED, font=ui_font(8))
        self.lbl_info.pack(anchor="w")

        c2 = tk.Frame(cards, bg=CARD, highlightbackground=BORDER, highlightthickness=1, padx=px(14), pady=px(10))
        c2.grid(row=0, column=1, sticky="nsew", padx=px(4))
        tk.Label(c2, text="Интеллект (Мозг)", bg=CARD, fg=MUTED, font=ui_font(9)).pack(anchor="w")
        self.lbl_model = tk.Label(c2, text="Gemini 2.5 Flash", bg=CARD, fg=FG, font=ui_font(12, "bold"))
        self.lbl_model.pack(anchor="w", pady=(px(4), 0))
        self.lbl_key = tk.Label(c2, text="Ключ не настроен", bg=CARD, fg=MUTED, font=ui_font(8))
        self.lbl_key.pack(anchor="w")

        c3 = tk.Frame(cards, bg=CARD, highlightbackground=BORDER, highlightthickness=1, padx=px(14), pady=px(10))
        c3.grid(row=0, column=2, sticky="nsew", padx=(px(6), 0))
        tk.Label(c3, text="Область экрана (Глаза)", bg=CARD, fg=MUTED, font=ui_font(9)).pack(anchor="w")
        self.lbl_roi = tk.Label(c3, text="Не выбрана", bg=CARD, fg=MUTED, font=ui_font(12, "bold"))
        self.lbl_roi.pack(anchor="w", pady=(px(4), 0))
        self.lbl_roi_hint = tk.Label(c3, text="Координаты теста на экране", bg=CARD, fg=MUTED, font=ui_font(8))
        self.lbl_roi_hint.pack(anchor="w")

        ctrl = tk.Frame(self, bg=CARD, highlightbackground=BORDER, highlightthickness=1, padx=px(14), pady=px(10))
        ctrl.grid(row=1, column=0, sticky="ew", pady=(0, px(12)))

        bar = tk.Frame(ctrl, bg=CARD)
        bar.pack(fill="x", pady=(0, px(10)))

        self.btn_start = flat_button(bar, "▶ Запустить окно агента", self.start_agent, kind="primary")
        self.btn_start.pack(side="left", padx=(0, px(8)))

        self.btn_stop = flat_button(bar, "⏹ Закрыть агент", self.stop_agent, kind="ghost")
        self.btn_stop.pack(side="left", padx=(0, px(8)))

        flat_button(bar, "📁 Папка агента", self.open_folder, kind="ghost").pack(side="left", padx=(0, px(8)))
        flat_button(bar, "🔄 Обновить", self.refresh, kind="ghost").pack(side="left")

        key_row = tk.Frame(ctrl, bg=CARD)
        key_row.pack(fill="x", pady=px(3))
        tk.Label(key_row, text="Gemini API Key:", bg=CARD, fg=FG, width=15, anchor="w").pack(side="left")
        self.key_var = tk.StringVar()
        self.key_entry = ttk.Entry(key_row, textvariable=self.key_var, show="*")
        self.key_entry.pack(side="left", fill="x", expand=True, padx=(0, px(6)))

        self.btn_eye = flat_button(key_row, "👁", self.toggle_show_key, kind="ghost")
        self.btn_eye.pack(side="left", padx=(0, px(6)))

        self.btn_paste = flat_button(key_row, "📋 Вставить", self.paste_key, kind="primary")
        self.btn_paste.pack(side="left", padx=(0, px(6)))

        flat_button(key_row, "Сохранить", self.save_key, kind="ghost").pack(side="left")

        # Привязка Ctrl+V в любой раскладке и ПКМ для вставки
        self.key_entry.bind("<Control-v>", lambda e: (self.paste_key(), "break")[1])
        self.key_entry.bind("<Control-V>", lambda e: (self.paste_key(), "break")[1])
        self.key_entry.bind("<Control-KeyPress>", lambda e: (self.paste_key(), "break")[1] if getattr(e, "keycode", 0) == 86 or (e.keysym or "").lower() in ("v", "м") else None)

        def _on_right_click(event):
            m = tk.Menu(self.key_entry, tearoff=0, bg=CARD, fg=FG)
            m.add_command(label="Вставить из буфера", command=self.paste_key)
            m.add_command(label="Копировать", command=lambda: (self.clipboard_clear(), self.clipboard_append(self.key_var.get())))
            m.add_command(label="Очистить", command=lambda: self.key_var.set(""))
            m.tk_popup(event.x_root, event.y_root)

        self.key_entry.bind("<Button-3>", _on_right_click)

        hint = tk.Label(ctrl, text="Подсказка: запустите окно агента, выделите рамку с тестом в браузере и нажмите СТАРТ.",
                        bg=CARD, fg=MUTED, font=ui_font(8))
        hint.pack(anchor="w", pady=(px(6), 0))

        log_frame, self.log_box = self.make_log(self, height=14, label="Лог событий AI Screen Agent")
        log_frame.grid(row=2, column=0, sticky="nsew")
        self.append_log(self.log_box, "AI Screen Agent интегрирован в хаб инструментов.")
        self.append_log(self.log_box, "Нажмите «▶ Запустить окно агента» для открытия окна управления над тестом.")

    def _is_running(self) -> bool:
        if self._proc is not None and self._proc.poll() is None:
            return True
        try:
            out = ps_run('Get-CimInstance Win32_Process -Filter "CommandLine LIKE '%screen_agent.py%'" | Select-Object -ExpandProperty ProcessId')
            pids = [int(p.strip()) for p in out.splitlines() if p.strip().isdigit()]
            return len(pids) > 0
        except Exception:
            return False

    def start_agent(self) -> None:
        if self._is_running():
            self.hub.toast("Окно агента уже запущено", kind="warn")
            return
        py_exe = pythonw_executable()

        try:
            env = os.environ.copy()
            env["PYTHONUTF8"] = "1"
            self._proc = subprocess.Popen(
                [py_exe, str(self.script)],
                cwd=str(self.dir),
                env=env,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)
            )
            self.hub.toast("Окно агента запущено", kind="ok")
            self.append_log(self.log_box, f"Запущен процесс агента (PID: {self._proc.pid}).")
            self.after(500, self.refresh)
        except Exception as exc:
            self.hub.toast(f"Ошибка запуска: {exc}", kind="error")
            self.append_log(self.log_box, f"Ошибка запуска: {exc}", tag="error")

    def stop_agent(self) -> None:
        stopped = False
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.terminate()
                stopped = True
            except Exception:
                pass
            self._proc = None

        try:
            out = ps_run('Get-CimInstance Win32_Process -Filter "CommandLine LIKE '%screen_agent.py%'" | Select-Object -ExpandProperty ProcessId')
            for line in out.splitlines():
                if line.strip().isdigit():
                    subprocess.run(["taskkill", "/PID", line.strip(), "/T", "/F"], capture_output=True)
                    stopped = True
        except Exception:
            pass

        if stopped:
            self.hub.toast("Агент остановлен", kind="ok")
            self.append_log(self.log_box, "Процесс агента остановлен.")
        else:
            self.hub.toast("Процесс не был запущен", kind="info")
        self.refresh()

    def open_folder(self) -> None:
        try:
            os.startfile(str(self.dir))
        except Exception as exc:
            self.hub.toast(f"Ошибка открытия папки: {exc}", kind="error")

    def toggle_show_key(self) -> None:
        if self.key_entry.cget("show") == "*":
            self.key_entry.configure(show="")
            self.btn_eye.configure(text="🔒")
        else:
            self.key_entry.configure(show="*")
            self.btn_eye.configure(text="👁")

    def paste_key(self) -> None:
        try:
            val = self.clipboard_get()
            if val:
                val = val.strip().strip('"\' \t\r\n')
                self.key_var.set(val)
                self.save_key()
                self.hub.toast("Ключ вставлен из буфера и сохранён", kind="ok")
                self.append_log(self.log_box, "✅ API ключ вставлен из буфера обмена и сохранён!")
        except Exception as exc:
            self.hub.toast(f"Буфер пуст или недоступен: {exc}", kind="warn")

    def save_key(self) -> None:
        new_key = self.key_var.get().strip()
        if not new_key:
            self.hub.toast("Введите ключ!", kind="warn")
            return
        cfg = {}
        if self.config_file.exists():
            try:
                cfg = json.loads(self.config_file.read_text(encoding="utf-8"))
                if not isinstance(cfg, dict):
                    cfg = {}
            except Exception as exc:
                self.hub.toast(f"Ошибка чтения config.json: {exc}", kind="error")
                return
        cfg["api_key"] = new_key
        try:
            self.config_file.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
            self.hub.toast("Ключ сохранён", kind="ok")
            self.append_log(self.log_box, "Gemini API Key сохранён в config.json.")
            self.refresh()
        except Exception as exc:
            self.hub.toast(f"Ошибка сохранения: {exc}", kind="error")

    def refresh(self) -> None:
        if self.config_file.exists():
            try:
                cfg = json.loads(self.config_file.read_text(encoding="utf-8"))
                key = cfg.get("api_key", "")
                if key and not self.key_var.get():
                    self.key_var.set(key)
                if key:
                    if key.startswith("sk-apx"):
                        p_name = "● APInex (Gemini/Claude)"
                    elif key.startswith("AIza"):
                        p_name = "● Google AI Studio"
                    elif key.startswith("sk-"):
                        p_name = "● OpenAI / Proxy"
                    else:
                        p_name = "● Ключ настроен"
                    self.lbl_key.config(text=p_name, fg=OK)
                else:
                    self.lbl_key.config(text="○ Ключ не настроен", fg=tc("err"))

                self.lbl_model.config(text=cfg.get("model", "gemini-2.5-flash"))

                roi = cfg.get("roi")
                if roi and isinstance(roi, (list, tuple)) and len(roi) == 4:
                    self.lbl_roi.config(text=f"{roi[2]}x{roi[3]} px", fg=FG)
                    self.lbl_roi_hint.config(text=f"X: {roi[0]}, Y: {roi[1]}")
                else:
                    self.lbl_roi.config(text="Не выбрана", fg=MUTED)
            except Exception:
                pass

        if self._is_running():
            self.lbl_status.config(text="● Запущен", fg=OK)
        else:
            self.lbl_status.config(text="○ Остановлен", fg=FG)

    def _schedule_poll(self) -> None:
        if not self.winfo_exists():
            return
        if getattr(self.hub, "current", None) == "ai_agent":
            self.refresh()
        self._poll_job = self.after(3000, self._schedule_poll)

    def on_show(self) -> None:
        self.refresh()

    def shutdown(self) -> None:
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except Exception:
                pass
            self._poll_job = None
