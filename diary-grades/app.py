"""Дневник МО · Контроль балла — оконное приложение (CustomTkinter, тёмная тема).

Запуск:
    python app.py                     обычный режим
    pythonw app.pyw                   Windows без консоли (делает run.bat)
    python app.py --demo              демо-данные без браузера
    python app.py --smoke-shot a.png  отрисовать демо, снять окно и выйти

Слои: GUI не знает про Playwright напрямую — всё через diary_client и parsers.
Сетевая работа идёт в фоновом потоке, в главный поток данные попадают через
queue.Queue + after(), поэтому окно не «замирает».
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import queue
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import tkinter as tk
from tkinter import filedialog, messagebox

import customtkinter as ctk

import config
import demo_data
import diary_client
import grade_math as gm
import parsers
from models import DiaryError, FetchResult, ParseError, SessionExpired, SubjectGrades, SubjectReport

# ──────────────────────────────── палитра ────────────────────────────────
BG = "#0f1115"
PANEL = "#171a21"
CARD = "#1d222c"
CARD_ALT = "#191d26"
BORDER = "#2a3040"
TEXT = "#e7eaf0"
MUTED = "#8b93a5"
ACCENT = "#3b82f6"
ACCENT_HOVER = "#2563eb"
OK = "#22c55e"
WARN = "#f59e0b"
BAD = "#ef4444"
BADGE = {
    "red": ("#5b1b1b", "#ffb4b4"),
    "yellow": ("#5a4211", "#ffe08a"),
    "green": ("#14432c", "#a7f3c4"),
}
COLUMNS = (
    ("Предмет", 230, 0),
    ("Средний балл", 140, 0),
    ("Текущие оценки", 275, 0),
    ("До 5-ки", 110, 0),
    ("Запас прочности", 210, 1),
)

ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")


def _font(size: int, bold: bool = False) -> ctk.CTkFont:
    return ctk.CTkFont(family="Segoe UI" if sys.platform == "win32" else "DejaVu Sans",
                       size=size, weight="bold" if bold else "normal")


class GradesTable(ctk.CTkScrollableFrame):
    """Таблица на фреймах: бейдж красит только ячейку, а не всю строку."""

    def __init__(self, master, on_select: Callable[[SubjectReport], None]):
        super().__init__(master, fg_color=BG, corner_radius=10,
                         border_width=1, border_color=BORDER,
                         scrollbar_button_color=BORDER,
                         scrollbar_button_hover_color=MUTED)
        self._on_select = on_select
        self._rows: list[tuple[ctk.CTkFrame, SubjectReport]] = []
        self._selected: ctk.CTkFrame | None = None
        self.grid_columnconfigure(0, weight=1)

    def show(self, reports: list[SubjectReport]) -> None:
        for child in self.winfo_children():
            child.destroy()
        self._rows.clear()
        self._selected = None
        self._headers()
        for index, report in enumerate(reports):
            self._add_row(index, report)

    def _headers(self) -> None:
        head = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=8, height=36)
        head.grid(row=0, column=0, sticky="ew", padx=6, pady=(6, 4))
        head.grid_columnconfigure(0, weight=1)
        inner = ctk.CTkFrame(head, fg_color="transparent")
        inner.grid(row=0, column=0, sticky="ew", padx=10, pady=7)
        for col, (title, width, weight) in enumerate(COLUMNS):
            inner.grid_columnconfigure(col, weight=weight, minsize=width)
            ctk.CTkLabel(inner, text=title, font=_font(12, True), text_color=MUTED,
                         anchor="w").grid(row=0, column=col, sticky="w", padx=6)

    def _add_row(self, index: int, report: SubjectReport) -> None:
        base = CARD if index % 2 == 0 else CARD_ALT
        row = ctk.CTkFrame(self, fg_color=base, corner_radius=8, height=42,
                           border_width=1, border_color=base, cursor="hand2")
        row._base_border = base  # цвет рамки, сливающийся с фоном строки
        row.grid(row=index + 1, column=0, sticky="ew", padx=6, pady=2)
        row.grid_columnconfigure(0, weight=1)
        inner = ctk.CTkFrame(row, fg_color="transparent")
        inner.grid(row=0, column=0, sticky="ew", padx=10, pady=7)
        for col, (_title, width, weight) in enumerate(COLUMNS):
            inner.grid_columnconfigure(col, weight=weight, minsize=width)

        ctk.CTkLabel(inner, text=report.name or "—", font=_font(13, True),
                     text_color=TEXT, anchor="w").grid(row=0, column=0, sticky="w", padx=6)

        empty = not report.count and not report.official_average
        bg, fg = ("#2a3040", MUTED) if empty else BADGE[report.badge]
        ctk.CTkLabel(inner, text=" — " if empty else f" {report.average_str} ",
                     font=_font(13, True),
                     text_color=fg, fg_color=bg, corner_radius=7, width=66,
                     height=26).grid(row=0, column=1, sticky="w", padx=6, pady=2)

        marks = "нет оценок в периоде" if not report.count else report.marks_text()
        if len(marks) > 42:
            marks = marks[:41] + "…"
        ctk.CTkLabel(inner, text=marks, font=_font(12), text_color=TEXT,
                     anchor="w").grid(row=0, column=2, sticky="w", padx=6)

        if empty:
            to_five = ctk.CTkLabel(inner, text="—", font=_font(13, True),
                                   text_color=MUTED, anchor="w")
        elif report.on_five:
            to_five = ctk.CTkLabel(inner, text="✓ на 5", font=_font(12, True),
                                   text_color=OK, anchor="w")
        else:
            to_five = ctk.CTkLabel(inner, text=f"{report.fives_needed} × «5»",
                                   font=_font(13, True), text_color=WARN, anchor="w")
        to_five.grid(row=0, column=3, sticky="w", padx=6)

        ctk.CTkLabel(inner, text=gm.reserve_text(report.reserve), font=_font(12),
                     text_color=OK if report.on_five else MUTED,
                     anchor="w").grid(row=0, column=4, sticky="w", padx=6)

        pick = lambda _event, r=report, w=row: self._select(w, r)  # noqa: E731
        for widget in (row, inner, *inner.winfo_children()):
            widget.bind("<Button-1>", pick)
        row.bind("<Enter>", lambda _e, w=row: self._hover(w, True))
        row.bind("<Leave>", lambda _e, w=row: self._hover(w, False))
        self._rows.append((row, report))

    def _hover(self, row: ctk.CTkFrame, entering: bool) -> None:
        if row is self._selected:
            return
        row.configure(border_color=ACCENT if entering else row._base_border)

    def _select(self, row: ctk.CTkFrame, report: SubjectReport) -> None:
        if self._selected is not None:
            self._selected.configure(border_color=self._selected._base_border)
        row.configure(border_color=ACCENT)
        self._selected = row
        self._on_select(report)

    def select_first(self) -> None:
        if self._rows:
            self._select(*self._rows[0])

    @property
    def visible_count(self) -> int:
        return len(self._rows)


class App(ctk.CTk):
    def __init__(self, *, auto_login: bool = True, start_demo: bool = False):
        super().__init__()
        self.title(config.APP_NAME)
        self.geometry("1120x760")
        self.minsize(940, 640)
        self.configure(fg_color=BG)
        self.grid_rowconfigure(3, weight=1)
        self.grid_columnconfigure(0, weight=1)

        self._queue: queue.Queue = queue.Queue()
        self._busy = False
        self._reports: list[SubjectReport] = []
        self._result: FetchResult | None = None
        self._auto_login = auto_login and not start_demo
        self._start_demo = start_demo
        self.settings = config.load_settings()
        # Скрытое автообновление: сразу после входа догружаем пустые предметы,
        # дальше — раз в auto_refresh_min минут, пока окно открыто.
        self._last_fetch_at = time.monotonic()
        self._auto_initial_done = False

        self._build_header()
        self._build_toolbar()
        self._build_filter()
        self.table = GradesTable(self, self._show_details)
        self.table.grid(row=3, column=0, sticky="nsew", padx=16, pady=(4, 6))
        self._build_details()
        self._build_footer()

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self._set_status("Загрузка…")
        self._set_busy(True)
        self.after(80, self._poll)
        self.after(180, self._initial_load)
        self.after(1500, self._maybe_auto_refresh)
        self.after(30_000, self._auto_refresh_tick)

    # ─────────────────────────── сборка окна ────────────────────────────
    def _build_header(self) -> None:
        head = ctk.CTkFrame(self, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=16, pady=(14, 4))
        head.grid_columnconfigure(0, weight=1)
        left = ctk.CTkFrame(head, fg_color="transparent")
        left.grid(row=0, column=0, sticky="w")
        ctk.CTkLabel(left, text="Дневник МО", font=_font(22, True),
                     text_color=TEXT).pack(side="left")
        ctk.CTkLabel(left, text="· Контроль балла", font=_font(22),
                     text_color=MUTED).pack(side="left", padx=(6, 0))
        self.period_label = ctk.CTkLabel(head, text="", font=_font(12),
                                         text_color=MUTED)
        self.period_label.grid(row=1, column=0, sticky="w", pady=(2, 0))
        self.source_chip = ctk.CTkLabel(head, text="", font=_font(12, True),
                                        fg_color=CARD, corner_radius=8,
                                        text_color=MUTED, padx=10, pady=4)
        self.source_chip.grid(row=0, column=1, rowspan=2, sticky="e", padx=(8, 0))

    def _build_toolbar(self) -> None:
        bar = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=10)
        bar.grid(row=1, column=0, sticky="ew", padx=16, pady=6)
        bar.grid_columnconfigure(7, weight=1)
        self.refresh_btn = ctk.CTkButton(
            bar, text="⟳  Обновить оценки", font=_font(14, True), height=40,
            fg_color=ACCENT, hover_color=ACCENT_HOVER, command=self._on_refresh)
        self.refresh_btn.grid(row=0, column=0, padx=(10, 6), pady=10)
        self.login_btn = ctk.CTkButton(
            bar, text="Войти заново", font=_font(12), height=36, width=130,
            fg_color=CARD, hover_color=BORDER, command=lambda: self._start_login(False))
        self.login_btn.grid(row=0, column=1, padx=4, pady=10)
        self.import_btn = ctk.CTkButton(
            bar, text="Импорт HTML/JSON", font=_font(12), height=36, width=150,
            fg_color=CARD, hover_color=BORDER, command=self._on_import)
        self.import_btn.grid(row=0, column=2, padx=4, pady=10)
        self.demo_btn = ctk.CTkButton(
            bar, text="Демо-данные", font=_font(12), height=36, width=115,
            fg_color=CARD, hover_color=BORDER, command=self._on_demo)
        self.demo_btn.grid(row=0, column=3, padx=4, pady=10)
        ctk.CTkButton(bar, text="Настройки", font=_font(12), height=36, width=100,
                      fg_color=CARD, hover_color=BORDER,
                      command=self._on_settings).grid(row=0, column=4, padx=4, pady=10)
        ctk.CTkButton(bar, text="?", font=_font(14, True), height=36, width=38,
                      fg_color=CARD, hover_color=BORDER,
                      command=self._on_help).grid(row=0, column=5, padx=4, pady=10)
        ctk.CTkButton(bar, text="Папка debug", font=_font(12), height=36, width=115,
                      fg_color=CARD, hover_color=BORDER,
                      command=self._on_open_debug).grid(row=0, column=6, padx=4, pady=10)
        ctk.CTkLabel(bar, text="", fg_color="transparent").grid(row=0, column=7)

    def _build_filter(self) -> None:
        line = ctk.CTkFrame(self, fg_color="transparent")
        line.grid(row=2, column=0, sticky="ew", padx=16, pady=(4, 2))
        line.grid_columnconfigure(1, weight=1)
        self.status_label = ctk.CTkLabel(line, text="Загрузка…", font=_font(13),
                                         text_color=MUTED, anchor="w")
        self.status_label.grid(row=0, column=0, sticky="w")
        self.progress = ctk.CTkProgressBar(line, height=6, mode="indeterminate",
                                           progress_color=ACCENT)
        self.progress.grid(row=0, column=1, sticky="ew", padx=14)
        self.progress.set(0)
        # CTk 6.0 не показывает placeholder, если задан textvariable,
        # поэтому фильтр читаем через .get() по KeyRelease.
        self.search = ctk.CTkEntry(line, width=220,
                                   placeholder_text="Поиск предмета…", font=_font(12))
        self.search.grid(row=0, column=2, sticky="e")
        self.search.bind("<KeyRelease>", lambda _e: self._apply_filter())

    def _build_details(self) -> None:
        box = ctk.CTkFrame(self, fg_color=PANEL, corner_radius=10)
        box.grid(row=4, column=0, sticky="ew", padx=16, pady=(2, 4))
        box.grid_columnconfigure(0, weight=1)
        ctk.CTkLabel(box, text="РАСЧЁТ ПО ВЫБРАННОМУ ПРЕДМЕТУ", font=_font(11, True),
                     text_color=MUTED).grid(row=0, column=0, sticky="w", padx=12, pady=(8, 2))
        self.details = ctk.CTkTextbox(box, height=92, fg_color=CARD,
                                      text_color=TEXT, font=_font(12),
                                      corner_radius=8, wrap="word")
        self.details.grid(row=1, column=0, sticky="ew", padx=10, pady=(0, 10))
        self._set_details("Выбери предмет в таблице — здесь появится разбор формулы.")

    def _build_footer(self) -> None:
        self.footer = ctk.CTkLabel(self, text="", font=_font(11), text_color=MUTED,
                                   anchor="w")
        self.footer.grid(row=5, column=0, sticky="ew", padx=18, pady=(0, 10))

    # ────────────────────────────── утилиты ─────────────────────────────
    def _set_details(self, text: str) -> None:
        self.details.configure(state="normal")
        self.details.delete("1.0", "end")
        self.details.insert("1.0", text)
        self.details.configure(state="disabled")

    def _show_details(self, report: SubjectReport) -> None:
        self._set_details(gm.explain(report))

    def _set_status(self, text: str, kind: str = "info") -> None:
        color = {"info": MUTED, "ok": OK, "warn": WARN, "bad": BAD}.get(kind, MUTED)
        self.status_label.configure(text=text, text_color=color)

    def _set_busy(self, value: bool) -> None:
        self._busy = value
        state = "disabled" if value else "normal"
        for btn in (self.refresh_btn, self.login_btn, self.import_btn, self.demo_btn):
            btn.configure(state=state)
        self.refresh_btn.configure(text="Загрузка…" if value else "⟳  Обновить оценки")
        if value:
            self.progress.grid(row=0, column=1, sticky="ew", padx=14)
            self.progress.configure(mode="indeterminate")
            self.progress.start()
        else:
            self.progress.stop()
            self.progress.configure(mode="determinate")
            self.progress.set(0)
            self.progress.grid_remove()  # CTk 6.0 оставляет синюю полосу после stop()
        self.update_idletasks()

    def _enqueue(self, kind: str, payload=None) -> None:
        self._queue.put((kind, payload))

    def _threaded(self, fn, *args) -> None:
        def runner():
            try:
                fn(*args)
            except Exception as exc:  # noqa: BLE001 — в GUI уходит текстом ошибки
                self._enqueue("error", f"{type(exc).__name__}: {exc}")
                self._enqueue("trace", traceback.format_exc())
            finally:
                self._enqueue("busy", False)

        threading.Thread(target=runner, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self._queue.get_nowait()
                if kind == "status":
                    self._set_status(payload, "info")
                elif kind == "error":
                    self._set_status(payload, "bad")
                elif kind == "busy":
                    self._set_busy(payload)
                elif kind == "data":
                    self._render(payload)
                elif kind == "done":
                    self._set_status(payload, "ok")
                elif kind == "trace":
                    self._write_trace(payload)
                elif kind == "autologin":
                    self.after(900, lambda: self._start_login(True))
        except queue.Empty:
            pass
        self.after(90, self._poll)

    # ─────────────────────── скрытое автообновление ────────────────────
    def _auto_refresh_tick(self) -> None:
        """Проверяем таймер каждые 30 секунд, пока окно открыто."""
        self._maybe_auto_refresh()
        self.after(30_000, self._auto_refresh_tick)

    def _cache_has_gaps(self) -> bool:
        """Предметы, по которым ждали оценки, но в кэше их нет.

        Пустые в шаблоне предметы профиля («Вероятность и статистика»,
        «Математика») не считаем дырами: у них в принципе нет оценок за период,
        и дёргать скрытый браузер при каждом запуске незачем.
        """
        try:
            raw = json.loads(config.SUBJECTS_FILE.read_text(encoding="utf-8-sig"))
            known_empty = {
                str(item.get("name") or "").strip().casefold()
                for item in raw.get("subjects") or []
                if isinstance(item, dict) and not (item.get("marks") or ())
            }
            payload = json.loads(config.CACHE_FILE.read_text(encoding="utf-8"))
            return any(
                not (item.get("marks") or ())
                and str(item.get("name") or "").strip().casefold() not in known_empty
                for item in payload.get("subjects") or []
                if isinstance(item, dict)
            )
        except (OSError, ValueError, TypeError, AttributeError):
            return False

    def _maybe_auto_refresh(self) -> None:
        """Обновляет ВСЕ предметы при сохранённой сессии, без окна браузера.

        Первый запуск: если после входа в кэше остались пустые предметы —
        догружаем сразу. Дальше — раз в ``auto_refresh_min`` минут.
        Без сессии ничего не делаем и окно входа не дёргаем.
        """
        if self._busy or self._start_demo:
            return
        try:
            minutes = float(self.settings.get("auto_refresh_min", 30))
        except (TypeError, ValueError):
            return
        if minutes <= 0 or not config.session_exists():
            return
        now = time.monotonic()
        if not self._auto_initial_done:
            self._auto_initial_done = True
            self._last_fetch_at = now
            if not self._cache_has_gaps():
                return
            self._set_busy(True)
            self._set_status("Автообновление: догружаем все предметы после входа…")
            self._threaded(self._fetch_worker)
            return
        if now - self._last_fetch_at < minutes * 60:
            return
        self._last_fetch_at = now
        self._set_busy(True)
        self._set_status("Автообновление всех предметов (браузер скрыт)…")
        self._threaded(self._fetch_worker)

    def _write_trace(self, text: str) -> None:
        try:
            config.ensure_dirs()
            with (config.DEBUG_DIR / "app_errors.log").open("a", encoding="utf-8") as fh:
                fh.write(f"\n--- {datetime.now():%Y-%m-%d %H:%M:%S} ---\n{text}\n")
        except OSError:
            pass

    # ───────────────────────────── данные ───────────────────────────────
    def _initial_load(self) -> None:
        def work():
            cached = None if self._start_demo else self._load_cache()
            has_session = config.session_exists()
            mode, need_login = config.startup_plan(
                self._start_demo, cached is not None, has_session, self._auto_login)
            if mode == "cache":
                self._enqueue("data", cached)
                self._enqueue("status", "Загружены сохранённые оценки. Нажми «Обновить оценки».")
                if not has_session:
                    self._enqueue("status", "Сессия отсутствует — для обновления нужен вход через Госуслуги.")
            elif mode == "fetch":
                self._set_status_text("Сессия найдена. Забираем оценки…")
                self._fetch_worker()
                return
            else:
                self._enqueue("data", demo_data.demo_result(config.current_period().label))
                self._enqueue("status",
                              "Демо-режим: браузер не запускается." if self._start_demo else
                              "Первый запуск: сейчас откроется браузер для входа через Госуслуги.")
            if need_login:
                self._enqueue("autologin", True)  # after() только из главного потока

        self._threaded(work)

    def _set_status_text(self, text: str) -> None:
        self._enqueue("status", text)

    def _load_cache(self) -> FetchResult | None:
        try:
            data = json.loads(config.CACHE_FILE.read_text(encoding="utf-8"))
            subjects = tuple(
                SubjectGrades(item["name"], tuple(item.get("marks", ())),
                              item.get("period", ""), "cache",
                              official_average=str(item.get("official_average") or ""))
                for item in data.get("subjects", []) if item.get("name")
            )
            if not subjects:
                return None
            return FetchResult(subjects, data.get("period", ""), "cache",
                               note="Сохранённые данные", urls=())
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def _save_cache(self, result: FetchResult) -> None:
        payload = {
            "updated_at": datetime.now().isoformat(timespec="seconds"),
            "period": result.period,
            "source": result.source,
            "subjects": [
                {
                    "name": s.name,
                    "marks": [m.to_dict_or_int() if hasattr(m, "to_dict_or_int") else int(m) for m in s.marks],
                    "period": s.period,
                    "official_average": s.official_average,
                }
                for s in result.subjects
            ],
        }
        config.atomic_write_json(config.CACHE_FILE, payload)

    def _render(self, result: FetchResult) -> None:
        self._result = result
        self._reports = gm.sort_reports(gm.build_reports(result.subjects))
        self.period_label.configure(text=f"Период: {result.period or config.current_period().label}"
                                         "  ·  порог пятёрки 4.50  ·  сортировка: от худшего к лучшему")
        if result.source == "demo":
            self.source_chip.configure(text="ДЕМО-ДАННЫЕ · не реальные оценки",
                                       fg_color="#5a4211", text_color="#ffe08a")
        elif result.source == "cache":
            self.source_chip.configure(text="ДАННЫЕ ИЗ КЭША", fg_color=CARD,
                                       text_color=MUTED)
        elif result.source == "import":
            self.source_chip.configure(text="ИМПОРТ ФАЙЛА", fg_color="#1e3a5f",
                                       text_color="#bfdbfe")
        else:
            self.source_chip.configure(text="ДАННЫЕ ИЗ ДНЕВНИКА", fg_color="#14432c",
                                       text_color="#a7f3c4")
        self._apply_filter()
        now = datetime.now().strftime("%H:%M:%S")
        self.footer.configure(
            text=f"Предметов: {len(self._reports)}  ·  Обновлено: {now}  ·  "
                 f"Источник: {result.source}  ·  Файлы: data/, debug/  ·  "
                 "Клик по строке — разбор формулы"
        )

    def _apply_filter(self) -> None:
        needle = (self.search.get() if hasattr(self, "search") else "").strip().casefold()
        if needle == "Поиск предмета…":
            needle = ""
        shown = [r for r in self._reports if not needle or needle in r.name.casefold()]
        self.table.show(shown)
        self.table.select_first()
        if self._reports and not shown:
            self._set_details("Ничего не найдено. Измени запрос в поле поиска.")
        self.footer.configure(
            text=f"Показано: {len(shown)} из {len(self._reports)} предметов  ·  "
                 "Сортировка: от худшего среднего к лучшему  ·  "
                 "Клик по строке — разбор формулы"
        )

    # ────────────────────────── действия кнопок ─────────────────────────
    def _on_refresh(self) -> None:
        if self._busy:
            return
        if not config.session_exists():
            self._set_status("Нет сохранённой сессии — открываем окно входа.", "warn")
            self._start_login(False, fetch_after=True)
            return
        self._set_busy(True)
        self._set_status("Забираем оценки из дневника (браузер скрыт)…")
        self._threaded(self._fetch_worker)

    def _fetch_worker(self) -> None:
        self._last_fetch_at = time.monotonic()  # ручное обновление сбрасывает таймер
        period = config.current_period(scheme=self.settings.get("period_scheme"))
        try:
            result = diary_client.fetch_grades(period, on_status=lambda m: self._enqueue("status", m))
        except SessionExpired:
            diary_client.DiaryClient().clear_session()
            self._enqueue("error", "Сессия истекла — нажми «Войти заново».")
            return
        except ParseError as exc:
            self._enqueue("error", str(exc))
            return
        except DiaryError as exc:
            self._enqueue("error", str(exc))
            return
        self._save_cache(result)
        self._enqueue("data", result)
        self._enqueue("done", f"Готово: {len(result.subjects)} предметов, {result.period}.")

    def _start_login(self, auto: bool = False, fetch_after: bool = True) -> None:
        if self._busy:
            return
        if importlib.util.find_spec("playwright") is None:
            self._set_status("Playwright не установлен: выполни разовую установку из requirements.txt", "bad")
            return
        self._set_busy(True)
        self._set_status("Открываем браузер. Войди через Госуслуги (ЕСИА) — окно закроется само.", "warn")

        def work():
            ok = diary_client.interactive_login(
                on_status=lambda m: self._enqueue("status", m),
                timeout_s=int(self.settings.get("login_timeout_s", 300)),
            )
            if not ok:
                self._enqueue("error", "Вход не выполнен. Можно работать в демо-режиме или импортировать файл.")
                return
            self._enqueue("done", "Вход выполнен, сессия сохранена. Забираем оценки…")
            if fetch_after or auto:
                period = config.current_period(scheme=self.settings.get("period_scheme"))
                try:
                    result = diary_client.fetch_grades(
                        period, on_status=lambda m: self._enqueue("status", m))
                except SessionExpired:
                    diary_client.DiaryClient().clear_session()
                    self._enqueue("error", "Сессия истекла — нажми «Войти заново».")
                    return
                except (ParseError, DiaryError) as exc:
                    self._enqueue("error", str(exc))
                    return
                self._save_cache(result)
                self._enqueue("data", result)
                self._enqueue("done", f"Готово: {len(result.subjects)} предметов, {result.period}.")
            else:
                self._enqueue("done", "Сессия сохранена. Нажми «Обновить оценки».")

        self._threaded(work)

    def _on_import(self) -> None:
        path = filedialog.askopenfilename(
            title="Выбери сохранённую страницу оценок или JSON дневника",
            filetypes=[("HTML / JSON / TXT", "*.html *.htm *.json *.txt"), ("Все файлы", "*.*")],
        )
        if not path:
            return
        try:
            subjects = parsers.validate_subjects(parsers.parse_file(path))
            if not subjects:
                raise ParseError("В файле не найдено ни одного предмета с оценками.")
        except (ParseError, OSError, ValueError) as exc:
            messagebox.showerror("Импорт", f"Не удалось разобрать файл:\n{exc}")
            return
        period = config.current_period(scheme=self.settings.get("period_scheme")).label
        result = FetchResult(tuple(subjects), period, "import", note=Path(path).name)
        self._save_cache(result)
        self._render(result)
        self._set_status(f"Импортировано предметов: {len(subjects)} из {Path(path).name}", "ok")

    def _on_demo(self) -> None:
        self._render(demo_data.demo_result(config.current_period().label))
        self._set_status("Показаны демо-данные — это не реальные оценки.", "warn")

    def _on_open_debug(self) -> None:
        config.ensure_dirs()
        path = str(config.DEBUG_DIR)
        try:
            if sys.platform == "win32":
                os.startfile(path)  # noqa: S606 — только локальная папка проекта
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except OSError as exc:
            self._set_status(f"Папка: {path} ({exc})", "warn")
        else:
            self._set_status(f"Открыта папка диагностики: {path}", "ok")

    def _on_settings(self) -> None:
        win = ctk.CTkToplevel(self)
        win.title("Настройки")
        win.geometry("420x370")
        win.configure(fg_color=BG)
        win.transient(self)
        ctk.CTkLabel(win, text="Учебные периоды", font=_font(16, True),
                     text_color=TEXT).pack(padx=20, pady=(18, 4), anchor="w")
        scheme = ctk.StringVar(value=self.settings.get("period_scheme", "trimesters"))
        ctk.CTkOptionMenu(win, values=["quarters", "trimesters"], variable=scheme,
                          fg_color=CARD, button_color=BORDER).pack(padx=20, anchor="w")
        ctk.CTkLabel(win, text="quarters — 4 четверти, trimesters — 3 триместра",
                     font=_font(11), text_color=MUTED).pack(padx=20, pady=(4, 14), anchor="w")
        headless = ctk.BooleanVar(value=bool(self.settings.get("headless", True)))
        ctk.CTkSwitch(win, text="Скрытый браузер при обновлении (headless)",
                      variable=headless, progress_color=ACCENT).pack(padx=20, anchor="w", pady=(0, 8))
        demo = ctk.BooleanVar(value=bool(self.settings.get("demo_on_empty", True)))
        ctk.CTkSwitch(win, text="Показывать демо, если нет данных", variable=demo,
                      progress_color=ACCENT).pack(padx=20, anchor="w")
        ctk.CTkLabel(win, text="Автообновление, минут (0 = выключить)", font=_font(12),
                     text_color=TEXT).pack(padx=20, pady=(14, 2), anchor="w")
        interval = ctk.CTkEntry(win, width=90, font=_font(12))
        interval.insert(0, str(self.settings.get("auto_refresh_min", 30)))
        interval.pack(padx=20, anchor="w")

        def save():
            try:
                auto_min = max(0.0, float(interval.get().replace(",", ".")))
            except ValueError:
                auto_min = float(config.DEFAULTS["auto_refresh_min"])
            self.settings = config.save_settings({
                "period_scheme": scheme.get(), "headless": headless.get(),
                "demo_on_empty": demo.get(), "auto_refresh_min": auto_min,
            })
            win.destroy()
            self._set_status("Настройки сохранены.", "ok")

        ctk.CTkButton(win, text="Сохранить", command=save, fg_color=ACCENT,
                      hover_color=ACCENT_HOVER).pack(padx=20, pady=18, anchor="w")

    def _on_help(self) -> None:
        win = ctk.CTkToplevel(self)
        win.title("Справка")
        win.geometry("660x520")
        win.configure(fg_color=BG)
        text = (
            "КАК РАБОТАЕТ\n\n"
            "1. Первый запуск: открывается окно Chromium — вручную войди через Госуслуги (ЕСИА).\n"
            "   После входа cookies (storage_state) сохраняются в data/storage_state.json,\n"
            "   браузер закрывается. Пароль приложение не хранит и не видит.\n"
            "2. Дальше «Обновить оценки» работает скрытно (headless) и закрывает браузер сам.\n"
            "   Если сессия истекла — нажми «Войти заново».\n"
            "   Пока окно открыто и сессия сохранена, оценки обновляются сами:\n"
            "   сразу после входа (если предметы без оценок), затем раз в N минут\n"
            "   (N задаётся в «Настройки», 0 = выключить). Без сессии автообновления нет.\n"
            "3. Если портал изменил вёрстку: сохрани страницу оценок как HTML или JSON\n"
            "   и нажми «Импорт HTML/JSON». Диагностика (дамп страницы и JSON-ответов)\n"
            "   лежит в папке debug/, селекторы правятся в selectors.json без правки кода.\n\n"
            "МАТЕМАТИКА (простое среднее, порог 4.50)\n\n"
            "  S — сумма оценок, N — их количество, средний = S/N.\n"
            "  Если S/N < 4.50:  k = ceil((4.5·N − S)/0.5) = 9·N − 2·S  пятёрок подряд.\n"
            "  Если S/N ≥ 4.50:  запас D = 2·S − 9·N. Для оценки v (2, 3, 4) можно\n"
            "  получить подряд floor(D / (9 − 2·v)) таких оценок без падения НИЖЕ 4.50.\n"
            "  Ровно 4.50 падением не считается. Пятёрки не роняют балл никогда.\n"
            "  В колонке «Запас прочности»: 4→2 · 3→0 · 2→0 означает, что выдержатся\n"
            "  две четвёрки, но первая же тройка или двойка уронит средний ниже 4.50.\n\n"
            "ЦВЕТА СРЕДНЕГО: красный < 3.50 · жёлтый 3.50–4.49 · зелёный ≥ 4.50.\n"
            "Сортировка всегда от худшего среднего к лучшему.\n\n"
            "Данные лежат только локально: data/ (кэш и сессия), debug/ (диагностика).\n"
        )
        box = ctk.CTkTextbox(win, fg_color=CARD, text_color=TEXT, font=_font(12),
                             wrap="word", corner_radius=8)
        box.pack(fill="both", expand=True, padx=16, pady=16)
        box.insert("1.0", text)
        box.configure(state="disabled")

    def _on_close(self) -> None:
        try:
            self.destroy()
        except tk.TclError:
            pass


def _capture_window(app: App, path: Path) -> str | None:
    """Скриншот окна. Сначала ImageGrab, если рабочий стол чёрный — PrintWindow.

    В RDP/сервисных сессиях ImageGrab возвращает полностью чёрный кадр;
    PrintWindow(hwnd, ..., PW_RENDERFULLCONTENT) рисует само окно через GDI.
    Возвращает None при успехе либо строку с причиной отсутствия картинки.
    """
    from PIL import Image, ImageGrab, ImageStat

    path.parent.mkdir(parents=True, exist_ok=True)
    x, y = app.winfo_rootx(), app.winfo_rooty()
    w, h = app.winfo_width(), app.winfo_height()
    try:
        img = ImageGrab.grab(bbox=(max(0, x - 8), max(0, y - 34), x + w + 8, y + h + 8))
        if float(ImageStat.Stat(img.convert("L")).mean[0]) > 2:
            img.save(path)
            return None
    except Exception:  # noqa: BLE001
        pass

    if sys.platform != "win32":
        return "рабочий стол недоступен, PrintWindow только на Windows"
    try:
        import ctypes
        from ctypes import wintypes

        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG),
                        ("biHeight", wintypes.LONG), ("biPlanes", wintypes.WORD),
                        ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                        ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                        ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD),
                        ("biClrImportant", wintypes.DWORD)]

        user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
        # 64-битные HWND/HDC: без argtypes ctypes обрезает указатель до c_int.
        H = ctypes.c_void_p
        user32.GetAncestor.argtypes = [H, ctypes.c_uint]
        user32.GetAncestor.restype = H
        user32.GetWindowRect.argtypes = [H, ctypes.POINTER(wintypes.RECT)]
        user32.GetWindowDC.argtypes = [H]
        user32.GetWindowDC.restype = H
        user32.PrintWindow.argtypes = [H, H, ctypes.c_uint]
        user32.ReleaseDC.argtypes = [H, H]
        gdi32.CreateCompatibleDC.argtypes = [H]
        gdi32.CreateCompatibleDC.restype = H
        gdi32.CreateCompatibleBitmap.argtypes = [H, ctypes.c_int, ctypes.c_int]
        gdi32.CreateCompatibleBitmap.restype = H
        gdi32.SelectObject.argtypes = [H, H]
        gdi32.SelectObject.restype = H
        gdi32.GetDIBits.argtypes = [H, H, ctypes.c_uint, ctypes.c_uint,
                                    ctypes.c_void_p, ctypes.c_void_p, ctypes.c_uint]
        gdi32.DeleteObject.argtypes = [H]
        gdi32.DeleteDC.argtypes = [H]

        hwnd = user32.GetAncestor(H(app.winfo_id()), 2) or H(app.winfo_id())
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        cw, ch = rect.right - rect.left, rect.bottom - rect.top
        if cw <= 0 or ch <= 0:
            return "не удалось получить размер окна"
        dbg = bool(os.environ.get("DIARY_CAPTURE_DEBUG"))
        def step(label: str) -> None:
            if dbg:
                print(f"[capture] {label}", flush=True)

        step(f"rect {cw}x{ch}")
        src_dc = user32.GetWindowDC(hwnd)
        mem_dc = gdi32.CreateCompatibleDC(src_dc)
        bitmap = gdi32.CreateCompatibleBitmap(src_dc, cw, ch)
        gdi32.SelectObject(mem_dc, bitmap)
        step("before PrintWindow")
        user32.PrintWindow(hwnd, mem_dc, 2)
        step("after PrintWindow")
        info = BITMAPINFOHEADER()
        info.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        info.biWidth, info.biHeight = cw, -ch
        info.biPlanes, info.biBitCount = 1, 32
        buffer = ctypes.create_string_buffer(cw * ch * 4)
        step("before GetDIBits")
        gdi32.GetDIBits(mem_dc, bitmap, 0, ch, buffer, ctypes.byref(info), 0)
        step("after GetDIBits")
        img = Image.frombuffer("RGB", (cw, ch), buffer, "raw", "BGRX", 0, 1)
        img.save(path)
        step("saved")
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(mem_dc)
        user32.ReleaseDC(hwnd, src_dc)
        if float(ImageStat.Stat(img.convert("L")).mean[0]) < 2:
            return "PrintWindow вернул чёрный кадр (нет активного рабочего стола)"
        return None
    except Exception as exc:  # noqa: BLE001
        return f"{type(exc).__name__}: {exc}"


def _widget_dump(app: App) -> list[dict]:
    """Программная проверка вёрстки: текст и геометрия всех виджетов окна."""
    out: list[dict] = []

    def walk(widget) -> None:
        for child in widget.winfo_children():
            try:
                text = str(child.cget("text") or "")
            except Exception:  # noqa: BLE001 — у CTkFrame нет опции text
                text = ""
            if text.strip():
                out.append({"class": type(child).__name__, "text": text[:50],
                            "w": child.winfo_width(), "h": child.winfo_height()})
            walk(child)

    walk(app.table)
    return out


def _smoke_shot(app: App, path: Path) -> None:
    """Проверяемый автопрогон GUI: демо → отрисовка → скриншот → отчёт → выход."""
    if os.environ.get("DIARY_CAPTURE_DEBUG"):
        print("[smoke] start", flush=True)
    app._render(demo_data.demo_result(config.current_period().label))
    app._set_status("Smoke-проверка: демо-данные отрисованы.", "ok")
    app._set_busy(False)
    app._auto_login = False
    app.update()
    app.update_idletasks()
    report = {
        "ok": True,
        "rows": app.table.visible_count,
        "first": app._reports[0].name if app._reports else None,
        "first_average": round(app._reports[0].average, 2) if app._reports else None,
        "last": app._reports[-1].name if app._reports else None,
        "badges": {b: sum(1 for r in app._reports if r.badge == b)
                   for b in ("red", "yellow", "green")},
        "title": app.title(),
        "geometry": app.geometry(),
        "window_width": app.winfo_width(),
        "window_height": app.winfo_height(),
        "details_text": app.details.get("1.0", "end").strip()[:100],
        "status_text": app.status_label.cget("text"),
        "table_widgets": 0,
        "screenshot": None,
    }
    config.ensure_dirs()
    report["table_widgets"] = len(_widget_dump(app))
    reason = _capture_window(app, path)
    if reason:
        report["screenshot_error"] = reason
    else:
        report["screenshot"] = str(path)
    out = config.DEBUG_DIR / "smoke_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False))
    app.after(250, app.destroy)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=config.APP_NAME)
    parser.add_argument("--demo", action="store_true", help="демо-данные, без браузера")
    parser.add_argument("--no-login", action="store_true", help="не открывать вход при первом запуске")
    parser.add_argument("--smoke-shot", type=Path, default=None,
                        help="отрисовать демо, сохранить PNG и выйти")
    args = parser.parse_args(argv)

    config.ensure_dirs()
    app = App(auto_login=not args.no_login, start_demo=args.demo or bool(args.smoke_shot))
    if args.smoke_shot:
        app.after(350, lambda: _smoke_shot(app, args.smoke_shot.resolve()))
    app.mainloop()


if __name__ == "__main__":
    main()
