#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Вкладка «Дневник МО · Контроль балла» хаба: панель оценок DiaryGradesPanel.

Переехала из tools.pyw без изменения логики:
  * diary_grades_core() — ядро подпроекта diary-grades/ грузится в изолированном
    неймспейсе (его модули называются слишком общо: models / config /
    grade_math), поэтому папка в sys.path НЕ добавляется;
  * _DIARY_*_CODE — строковые «мостки»: код исполняется отдельным процессом
    python.exe из .venv подпроекта (у хаба может не быть Playwright, а
    sync_playwright нельзя гонять в потоке с уже работающим Tk);
  * DiaryGradesPanel — сама панель вкладки.

Модуль знает только uibase/winbase; конкретный хаб ему не нужен — всё общение
через hub-мост базовой Panel (run_bg/set_status/toast/post).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, ttk

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from uibase import (  # noqa: E402
    BADGE_NEUTRAL_BG,
    BG,
    BORDER,
    CARD,
    CARD_HOVER,
    DANGER,
    FG,
    FIELD,
    MUTED,
    OK,
    Panel,
    ROW_SELECTED,
    WARN,
    badge_colors,
    dark_text,
    flat_button,
    px,
    register_palette_consumer,
    ui_font,
)

# Токены приходят реэкспортом; регистрируем глобалы этого модуля потребителем
# палитры, чтобы живая смена темы обновляла и их (панель читает голые имена).
register_palette_consumer(globals())

from scroll_settings import steps_per_wheel as _scroll_step  # noqa: E402
from winbase import APP_DIR, log_exception  # noqa: E402

# --------------------------------------------------------------------------- #
#  Дневник МО · Контроль балла — вкладка хаба
# --------------------------------------------------------------------------- #
# Подпроект живёт в diary-grades/. Его модули называются слишком общо
# (models / config / grade_math), поэтому НЕ добавляем папку в sys.path:
# загружаем ядро в изолированном неймспейсе, на время подменив sys.modules.
_DIARY_CORE = None


def diary_grades_core():
    """(config, grade_math, demo_data, models) подпроекта diary-grades или None."""
    global _DIARY_CORE
    if _DIARY_CORE is not None:
        return _DIARY_CORE
    import importlib.util
    import types

    folder = APP_DIR / "diary-grades"
    if not (folder / "grade_math.py").exists():
        return None
    package_name = "_tools_diary_core"
    package = types.ModuleType(package_name)
    package.__path__ = [str(folder)]
    sys.modules[package_name] = package

    def load(name: str):
        spec = importlib.util.spec_from_file_location(
            f"{package_name}.{name}", folder / f"{name}.py"
        )
        if spec is None or spec.loader is None:
            raise ImportError(f"Не удалось загрузить {name}.py из {folder}")
        module = importlib.util.module_from_spec(spec)
        # Регистрация до exec_module обязательна: в models.py dataclass(slots=True)
        # с отложенными аннотациям обращается к модулю через sys.modules.
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        return module

    sentinel = object()
    previous = sys.modules.get("models", sentinel)
    try:
        models_module = load("models")
        sys.modules["models"] = models_module
        try:
            config_module = load("config")
            math_module = load("grade_math")
            demo_module = load("demo_data")
        finally:
            if previous is sentinel:
                sys.modules.pop("models", None)
            else:
                sys.modules["models"] = previous
    except Exception:  # noqa: BLE001 — отсутствие подпроекта не роняет хаб
        log_exception()
        return None
    _DIARY_CORE = (config_module, math_module, demo_module, models_module)
    return _DIARY_CORE


# Мосты вызываются отдельным процессом из .venv подпроекта: у хаба может не быть
# Playwright, а sync_playwright нельзя гонять в потоке с уже работающим Tk.
_DIARY_BRIDGE_PREAMBLE = """
import json, sys
for _stream in (getattr(sys, "stdout", None), getattr(sys, "stderr", None)):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
from datetime import datetime
import config, diary_client
from models import DiaryError, SubjectGrades

def save(result):
    # После обновления в таблице должны остаться ВСЕ предметы из шаблона,
    # а не только те, что портал прислал на первой странице.
    subjects = list(result.subjects)
    known = {s.name.casefold() for s in subjects}
    for name in config.expected_subjects():
        key = name.casefold()
        if key not in known:
            known.add(key)
            subjects.append(SubjectGrades(name, (), result.period, 'pending'))
    config.atomic_write_json(config.CACHE_FILE, {
        'updated_at': datetime.now().isoformat(timespec='seconds'),
        'period': result.period,
        'source': result.source,
        'subjects': [
            {'name': s.name,
             # Mark наследует int: list(s.marks) в JSON пишет голые числа и
             # молча теряет вес «5₂», из-за чего средний разошёлся с дневником.
             'marks': [m.to_dict_or_int() if hasattr(m, 'to_dict_or_int') else int(m)
                       for m in s.marks],
             'period': s.period,
             'official_average': getattr(s, 'official_average', '')}
            for s in subjects
        ],
    })
    print(json.dumps({'ok': True, 'subjects': len(subjects),
                      'with_marks': sum(1 for s in subjects if s.marks),
                      'period': result.period}, ensure_ascii=False))

def fail(message):
    print(json.dumps({'ok': False, 'error': str(message)}, ensure_ascii=False))
    sys.exit(1)
"""

_DIARY_FETCH_CODE = _DIARY_BRIDGE_PREAMBLE + """
try:
    save(diary_client.fetch_grades(config.current_period()))
except DiaryError as exc:
    fail(exc)
except Exception as exc:
    fail('Сбой сборщика: ' + str(exc))
"""

_DIARY_LOGIN_CODE = _DIARY_BRIDGE_PREAMBLE + """
settings = config.load_settings()
if not diary_client.interactive_login(timeout_s=int(settings.get('login_timeout_s', 300))):
    fail('Вход через Госуслуги не выполнен или истекло время ожидания.')
try:
    save(diary_client.fetch_grades(config.current_period()))
except DiaryError as exc:
    fail('Вход выполнен, сессия сохранена, но оценки не получены: ' + str(exc))
except Exception as exc:
    fail('Сбой сборщика: ' + str(exc))
"""

_DIARY_IMPORT_CODE = """
import json, sys
for _stream in (getattr(sys, "stdout", None), getattr(sys, "stderr", None)):
    if _stream is not None and hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
from datetime import datetime
from pathlib import Path
import config, parsers
from models import SubjectGrades
try:
    path = Path(sys.argv[1])
    subjects = parsers.validate_subjects(parsers.parse_file(path))
    # Заготовки без оценок (real_grades.json) не выбрасываем молча: иначе в окне
    # остаётся «одна оценка» и непонятно, что остальные предметы просто не загружены.
    try:
        raw = json.loads(path.read_text(encoding='utf-8-sig'))
    except Exception:
        raw = None
    if isinstance(raw, dict):
        seen = {s.name.casefold() for s in subjects}
        for item in raw.get('subjects') or []:
            if not isinstance(item, dict):
                continue
            name = parsers.clean_name(str(item.get('name') or ''))
            if not name or name.casefold() in seen:
                continue
            subjects.append(SubjectGrades(name, (), '', 'pending'))
            seen.add(name.casefold())
    if not subjects:
        raise ValueError('Предметы в файле не найдены.')
    config.atomic_write_json(config.CACHE_FILE, {
        'updated_at': datetime.now().isoformat(timespec='seconds'),
        'period': config.current_period().label,
        'source': 'import',
        'subjects': [
            {'name': s.name,
             # Как и в fetch-мосту: сохраняем вес оценки, не голый int.
             'marks': [m.to_dict_or_int() if hasattr(m, 'to_dict_or_int') else int(m)
                       for m in s.marks],
             'period': s.period,
             'official_average': getattr(s, 'official_average', '')}
            for s in subjects
        ],
    })
    print(json.dumps({'ok': True, 'subjects': len(subjects),
                      'with_marks': sum(1 for s in subjects if s.marks),
                      'period': config.current_period().label}, ensure_ascii=False))
except Exception as exc:
    print(json.dumps({'ok': False, 'error': str(exc)}, ensure_ascii=False))
    sys.exit(1)
"""


class DiaryGradesPanel(Panel):
    """Оценки электронного дневника МО прямо среди остальных инструментов хаба."""

    COLUMNS = (
        ("Предмет", 145, 3),
        ("Средний балл", 90, 0),
        ("Текущие оценки", 205, 4),
        ("До 5-ки", 80, 0),
        ("Запас прочности", 145, 2),
    )
    BADGE_ROLES = {
        "red": ("badge_bad_bg", "badge_bad_ink"),
        "yellow": ("badge_warn_bg", "badge_warn_ink"),
        "green": ("badge_good_bg", "badge_good_ink"),
    }

    def __init__(self, master: tk.Misc, hub) -> None:
        super().__init__(master, hub)
        self.dir = APP_DIR / "diary-grades"
        core = diary_grades_core()
        self.cfg, self.gm, self.demo, self.models = core if core else (None,) * 4
        self.reports = []
        self.filtered = []
        self._row_widgets: list[list[tk.Label]] = []
        self._selected_index = -1
        self._sort_mode = "average"  # "average" | "alpha"
        self.source_label = "—"
        self.demo_note = ""
        self.period_label = "—"
        self.cache_stamp = None
        self._busy = False
        self._poll_job = None
        self._last_fetch = 0.0
        self._initial_fetch_done = False
        self._build()
        self.reload()
        self._schedule_poll()

    # -- разметка ---------------------------------------------------------- #
    @staticmethod
    def _card(parent: tk.Misc, column: int, title: str):
        frame = tk.Frame(parent, bg=CARD, highlightbackground=BORDER,
                         highlightthickness=1, padx=px(12), pady=px(8))
        frame.grid(row=0, column=column, sticky="nsew",
                   padx=(0 if column == 0 else px(6), 0))
        tk.Label(frame, text=title, bg=CARD, fg=MUTED,
                 font=ui_font(8), anchor="w").pack(fill="x")
        label = tk.Label(frame, text="—", bg=CARD, fg=FG,
                         font=ui_font(11, "bold"), anchor="w")
        label.pack(fill="x", pady=(px(3), 0))
        return label

    def _configure_columns(self, frame: tk.Misc) -> None:
        for index, (_name, minsize, weight) in enumerate(self.COLUMNS):
            frame.columnconfigure(index, weight=weight, minsize=px(minsize))

    def _build(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(2, weight=1)

        cards = tk.Frame(self, bg=BG)
        cards.grid(row=0, column=0, sticky="ew", pady=(0, px(10)))
        for index in range(4):
            cards.columnconfigure(index, weight=1)
        self.lbl_source = self._card(cards, 0, "Источник")
        self.lbl_period = self._card(cards, 1, "Период")
        self.lbl_count = self._card(cards, 2, "Предметов")
        self.lbl_session = self._card(cards, 3, "Сессия ЕСИА")

        toolbar = tk.Frame(self, bg=BG)
        toolbar.grid(row=1, column=0, sticky="ew", pady=(0, px(10)))
        self.btn_refresh = flat_button(toolbar, "▶ Обновить оценки", self._refresh)
        self.btn_refresh.pack(side="left")
        flat_button(toolbar, "Войти заново", self._login, kind="ghost").pack(
            side="left", padx=(px(6), 0))
        flat_button(toolbar, "Окно", lambda: self._open_app("--no-login"),
                    kind="ghost").pack(side="left", padx=(px(6), 0))
        flat_button(toolbar, "Демо", self._demo, kind="ghost").pack(
            side="left", padx=(px(6), 0))
        flat_button(toolbar, "Импорт", self._import_file, kind="ghost").pack(
            side="left", padx=(px(6), 0))
        self.btn_sort = flat_button(toolbar, "Сортировка: по среднему", self._on_sort,
                                    kind="ghost")
        self.btn_sort.pack(side="left", padx=(px(6), 0))
        flat_button(toolbar, "Папка", self._open_folder, kind="ghost").pack(side="right")
        self.search_var = tk.StringVar()
        ttk.Entry(toolbar, textvariable=self.search_var, width=12).pack(
            side="right", padx=(0, px(6)))
        tk.Label(toolbar, text="Поиск:", bg=BG, fg=MUTED,
                 font=ui_font(9)).pack(side="right", padx=(0, px(6)))
        self.search_var.trace_add("write", lambda *_: self._render_rows())

        table = tk.Frame(self, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        table.grid(row=2, column=0, sticky="nsew")
        table.columnconfigure(0, weight=1)
        table.rowconfigure(1, weight=1)

        head = tk.Frame(table, bg=FIELD, height=px(30))
        head.grid(row=0, column=0, sticky="ew")
        head.grid_propagate(False)
        self._configure_columns(head)
        for index, (name, _size, _weight) in enumerate(self.COLUMNS):
            tk.Label(head, text=name, bg=FIELD, fg=MUTED, anchor="w",
                     font=ui_font(9, "bold")).grid(
                row=0, column=index, sticky="ew", padx=px(8))

        self.canvas = tk.Canvas(table, bg=CARD, bd=0, highlightthickness=0)
        self.canvas.grid(row=1, column=0, sticky="nsew")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.canvas.yview)
        scroll.grid(row=1, column=1, sticky="ns")
        self.canvas.configure(yscrollcommand=scroll.set)
        self.rows_frame = tk.Frame(self.canvas, bg=CARD)
        self._rows_window = self.canvas.create_window(
            (0, 0), window=self.rows_frame, anchor="nw")
        self.rows_frame.bind(
            "<Configure>",
            lambda _event: self.canvas.configure(scrollregion=self.canvas.bbox("all")))
        self.canvas.bind(
            "<Configure>",
            lambda event: self.canvas.itemconfigure(self._rows_window, width=event.width))
        self.canvas.bind("<MouseWheel>", self._on_wheel)
        self.rows_frame.bind("<MouseWheel>", self._on_wheel)

        details = tk.Frame(self, bg=BG)
        details.grid(row=3, column=0, sticky="ew", pady=(px(10), 0))
        tk.Label(details, text="Разбор расчёта — клик по строке таблицы",
                 bg=BG, fg=MUTED, font=ui_font(8), anchor="w").pack(fill="x")
        self.details = dark_text(details, height=5)
        self.details.pack(fill="x")
        self.details.configure(state="disabled")

        self.footer_var = tk.StringVar(value="—")
        tk.Label(self, textvariable=self.footer_var, bg=BG, fg=MUTED,
                 font=ui_font(8), anchor="w").grid(row=4, column=0, sticky="ew")

        log_frame, self.log_box = self.make_log(self, height=4, label="Журнал дневника")
        log_frame.grid(row=5, column=0, sticky="ew", pady=(px(8), 0))
        self._log("Вкладка «Дневник МО» подключена к diary-grades/.")

    # -- данные ------------------------------------------------------------ #
    def _stamp(self):
        try:
            stat = self.cfg.CACHE_FILE.stat()
            return (stat.st_mtime_ns, stat.st_size)
        except (OSError, AttributeError):
            return None

    def reload(self) -> None:
        if self.gm is None:
            self.source_label = "ЯДРО НЕ НАЙДЕНО"
            self.lbl_source.configure(text=self.source_label, fg=DANGER)
            self._show_message(f"Не найдено ядро подпроекта: {self.dir}")
            return

        subjects = []
        payload = None
        try:
            payload = json.loads(self.cfg.CACHE_FILE.read_text(encoding="utf-8"))
        except (OSError, ValueError, AttributeError):
            pass
        for item in (payload or {}).get("subjects") or []:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            try:
                subjects.append(self.models.SubjectGrades(
                    item["name"], tuple(item.get("marks") or ()),
                    item.get("period", ""), "cache",
                    official_average=str(item.get("official_average") or "")))
            except (TypeError, ValueError):
                continue

        cur_p = self.cfg.current_period() if self.cfg else None
        cur_p_label = cur_p.label if cur_p else "—"
        if subjects:
            self.source_label = "КЭШ" if (payload or {}).get("source") != "demo" else "КЭШ · ДЕМО"
            self.period_label = (payload or {}).get("period") or cur_p_label
            self.demo_note = ""
        else:
            result = self.demo.demo_result(cur_p_label)
            subjects = list(result.subjects)
            self.source_label = "ДЕМО-ДАННЫЕ"
            self.demo_note = "  ·  не реальные оценки"
            self.period_label = result.period
            self._log("Кэша нет: показаны демо-данные, это не реальные оценки.", "warn")

        try:
            self.reports = self.gm.sort_reports(
                self.gm.build_reports(subjects), self._sort_mode)
        except (ValueError, TypeError) as exc:
            self.reports = []
            self._log(f"Ошибка расчёта: {exc}", "error")

        missing = sum(1 for r in self.reports if not r.count)
        # «Частично» — только когда не хватает оценок, которые должны быть.
        # Предметы профиля без оценок за период (пустые в шаблоне) нормой и
        # не должны окрашивать плашку в жёлтый при сохранённой сессии.
        gaps = self._cache_has_gaps() if "КЭШ" in self.source_label else False
        if gaps:
            self.source_label += " · ЧАСТИЧНО"
        demo_like = ("ДЕМО" in self.source_label or "НЕ НАЙДЕНО" in self.source_label
                     or bool(gaps))
        self.lbl_source.configure(text=self.source_label, fg=WARN if demo_like else OK)
        self.lbl_period.configure(text=self.period_label)
        self.lbl_count.configure(
            text=f"{len(self.reports)} · с оценками {len(self.reports) - missing}"
            if missing else str(len(self.reports)))
        self._refresh_session_card()
        self.cache_stamp = self._stamp()
        self._render_rows()

    def on_show(self) -> None:
        """Хаб вызывает при каждом переключении на вкладку."""
        self.reload()

    def _refresh_session_card(self) -> None:
        try:
            ok = bool(self.cfg.session_exists())
        except Exception:  # noqa: BLE001
            ok = False
        self.lbl_session.configure(text="● Сохранена" if ok else "○ Нет входа",
                                   fg=OK if ok else WARN)

    def _schedule_poll(self) -> None:
        try:
            self._poll_job = self.after(2000, self._on_poll)
        except (tk.TclError, AttributeError):
            self._poll_job = None

    def _on_poll(self) -> None:
        try:
            if not self.winfo_exists():
                return
        except (tk.TclError, AttributeError):
            return
        try:
            stamp = self._stamp()
            if stamp != self.cache_stamp:
                self.reload()
                self._log("Кэш обновлён — таблица перечитана.", "ok")
        except Exception:  # noqa: BLE001 — опрос не должен ронять окно
            pass
        # Файл сессии мог появиться после входа ЕСИА, даже если кэш ещё не
        # обновился (сбор оценок мог завершиться ошибкой). Карточку «Сессия
        # ЕСИА» обновляем на каждом опросе, а не только при смене кэша.
        try:
            self._refresh_session_card()
        except Exception:  # noqa: BLE001
            pass
        self._maybe_auto_refresh()
        self._schedule_poll()

    def _cache_has_gaps(self) -> bool:
        """Есть ли предметы, по которым ждали оценки, но их нет в кэше.

        Предметы из профиля портала без оценок («Вероятность и статистика»,
        «Математика») помечены в шаблоне пустым ``marks`` и дырами не считаются:
        иначе каждый запуск дёргал бы скрытый браузер без всякой пользы.
        """
        try:
            raw = json.loads(self.cfg.SUBJECTS_FILE.read_text(encoding="utf-8-sig"))
            known_empty = {
                str(item.get("name") or "").strip().casefold()
                for item in raw.get("subjects") or []
                if isinstance(item, dict) and not (item.get("marks") or ())
            }
            payload = json.loads(self.cfg.CACHE_FILE.read_text(encoding="utf-8"))
            return any(
                not (item.get("marks") or ())
                and str(item.get("name") or "").strip().casefold() not in known_empty
                for item in payload.get("subjects") or []
                if isinstance(item, dict)
            )
        except Exception:  # noqa: BLE001
            return False

    def _maybe_auto_refresh(self) -> None:
        """Скрыто обновляет ВСЕ предметы при сохранённой сессии.

        Первый раз — сразу, если после входа в кэше ещё есть пустые предметы:
        пользователь вошёл через ЕСИА и ждёт оценки, а не таймер. Дальше —
        раз в ``auto_refresh_min`` минут. Без сессии ничего не делаем и окно
        входа не дёргаем.
        """
        if self.cfg is None or self._busy:
            return
        try:
            minutes = float(self.cfg.load_settings().get("auto_refresh_min", 30))
            has_session = bool(self.cfg.session_exists())
        except Exception:  # noqa: BLE001
            return
        if minutes <= 0 or not has_session:
            return
        now = time.monotonic()
        if not self._last_fetch:
            self._last_fetch = now
            self._initial_fetch_done = True
            # При каждом запуске хаба сразу подтягиваем свежие оценки,
            # а не только когда в кэше остались пустые предметы.
            self._start_bg("Автозагрузка оценок при запуске…",
                           _DIARY_FETCH_CODE, (), 240)
            return
        if now - self._last_fetch < minutes * 60:
            return
        self._last_fetch = now
        self._start_bg("Автообновление всех предметов…", _DIARY_FETCH_CODE, (), 240)

    # -- таблица ----------------------------------------------------------- #
    def _sort_label(self) -> str:
        return ("Сортировка: по алфавиту" if self._sort_mode == "alpha"
                else "Сортировка: по среднему")

    def _on_sort(self) -> None:
        """Переключает порядок строк: по среднему баллу ↔ по алфавиту."""
        if self.gm is None:
            return
        self._sort_mode = "alpha" if self._sort_mode == "average" else "average"
        self.btn_sort.configure(text=self._sort_label())
        self.reports = self.gm.sort_reports(self.reports, self._sort_mode)
        self._render_rows()
        self._log("Порядок строк: " + ("по алфавиту" if self._sort_mode == "alpha"
                                       else "от худшего среднего к лучшему"))

    def _on_wheel(self, event) -> None:
        try:
            self.canvas.yview_scroll(
                (-1 if event.delta > 0 else 1) * _scroll_step(), "units")
        except (tk.TclError, AttributeError):
            pass

    def _render_rows(self) -> None:
        for child in self.rows_frame.winfo_children():
            child.destroy()
        self._row_widgets = []
        self._configure_columns(self.rows_frame)
        needle = (self.search_var.get() if hasattr(self, "search_var") else "").strip().casefold()
        self.filtered = [r for r in self.reports
                         if not needle or needle in r.name.casefold()]

        for index, report in enumerate(self.filtered):
            self._add_row(index, report)

        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        missing = sum(1 for r in self.reports if not r.count)
        tail = f"  ·  без оценок: {missing} (в периоде оценок нет)" if missing else ""
        order = "по алфавиту" if self._sort_mode == "alpha" else "худшие сверху"
        self.footer_var.set(
            f"Показано {len(self.filtered)} из {len(self.reports)}  ·  порог 4.50  ·  "
            f"{order}  ·  {self.source_label}"
            + self.demo_note + tail)
        if self.filtered:
            self._select(0, self.filtered[0])
        elif self.reports:
            self._show_message("Ничего не найдено. Измени запрос в поиске.")
        else:
            self._show_message("Данных нет. Нажми «Обновить оценки», «Войти заново» или «Демо».")

    def _add_row(self, index: int, report) -> None:
        background = CARD if index % 2 == 0 else CARD_HOVER
        empty = not report.count and not getattr(report, "official_average", "")
        badge_bg, badge_fg = ((BADGE_NEUTRAL_BG, MUTED) if empty
                              else badge_colors(report.badge))
        fives = "—" if report.on_five else f"{report.fives_needed} × «5»"
        if empty:
            avg_display = "—"
        elif hasattr(report, "average_str"):
            avg_display = str(report.average_str)
        elif self.models and hasattr(self.models, "format_average") and getattr(report, "count", 0):
            avg_display = self.models.format_average(report.total, report.count)
        else:
            from decimal import Decimal, ROUND_HALF_UP
            total = getattr(report, "total", None)
            cnt = getattr(report, "count", None)
            if total is not None and cnt:
                avg_display = str((Decimal(total) / Decimal(cnt)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
            else:
                avg_display = str(Decimal(str(getattr(report, "average", 0))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))
        values = (
            report.name,
            avg_display,
            "нет оценок в периоде" if empty else report.marks_text(),
            "—" if empty else fives,
            self.gm.reserve_text(report.reserve),
        )
        widgets: list[tk.Label] = []
        for column, value in enumerate(values):
            if column == 1:
                label = tk.Label(self.rows_frame, text=value, bg=badge_bg, fg=badge_fg,
                                 font=ui_font(9, "bold"), anchor="center",
                                 padx=px(8), pady=px(3))
                label.grid(row=index, column=column, sticky="w",
                           padx=px(8), pady=px(3))
            else:
                label = tk.Label(self.rows_frame, text=value, bg=background,
                                 fg=FG if column == 0 else MUTED,
                                 font=ui_font(9, "bold" if column == 0 else "normal"),
                                 anchor="w", pady=px(5))
                label.grid(row=index, column=column, sticky="ew", padx=px(8))
            label.bind("<Button-1>",
                       lambda _event, r=report, i=index: self._select(i, r))
            label.bind("<MouseWheel>", self._on_wheel)
            widgets.append(label)
        self._row_widgets.append(widgets)

    def _select(self, index: int, report) -> None:
        self._selected_index = index
        for row_index, widgets in enumerate(self._row_widgets):
            background = CARD if row_index % 2 == 0 else CARD_HOVER
            if row_index == index:
                background = ROW_SELECTED
            for column, label in enumerate(widgets):
                if column != 1:       # цветной бейдж среднего балла не закрашиваем
                    label.configure(bg=background)
        self._show_message(self.gm.explain(report))

    def _show_message(self, text: str) -> None:
        try:
            self.details.configure(state="normal")
            self.details.delete("1.0", "end")
            self.details.insert("1.0", text)
            self.details.configure(state="disabled")
        except (tk.TclError, AttributeError):
            pass

    def _log(self, line: str, tag: str | None = None) -> None:
        try:
            self.append_log(self.log_box, line, tag)
        except (tk.TclError, AttributeError):
            pass

    # -- действия ---------------------------------------------------------- #
    def _python(self, name: str):
        for folder in (".venv/Scripts", ".venv/bin"):
            exe = self.dir / folder / name
            if exe.exists():
                return exe
        return None

    def _run_bridge(self, code: str, argv=(), timeout: int = 180) -> dict:
        """Запускает мост в .venv подпроекта. Вызывается ТОЛЬКО из фонового потока."""
        python = self._python("python.exe")
        if python is None:
            return {"ok": False,
                    "error": "Окружение .venv не готово: один раз запусти diary-grades/run.bat"}
        env = os.environ.copy()
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        command = [str(python), "-c", code, *(str(arg) for arg in argv)]
        try:
            proc = subprocess.run(
                command, cwd=str(self.dir), env=env, capture_output=True,
                text=True, encoding="utf-8", errors="replace", timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except subprocess.TimeoutExpired:
            return {"ok": False, "error": f"Превышено время ожидания ({timeout} с)"}
        except OSError as exc:
            return {"ok": False, "error": str(exc)}
        output = (proc.stdout or "").strip()
        try:
            return json.loads(output.splitlines()[-1]) if output else {
                "ok": False, "error": (proc.stderr or "Пустой ответ моста").strip()[:400]}
        except ValueError:
            return {"ok": False, "error": (proc.stderr or output)[:400]}

    def _start_bg(self, label: str, code: str, argv=(), timeout: int = 180) -> None:
        if self._busy:
            return
        self._busy = True
        self.btn_refresh.configure(state="disabled")
        self._log(label)
        self.hub.set_status("Дневник МО: " + label)

        def work():
            return self._run_bridge(code, argv, timeout)

        def on_done(data):
            self._busy = False
            self.btn_refresh.configure(state="normal")
            if data.get("ok"):
                self.reload()
                if "with_marks" in data:
                    message = (f"Готово: {data.get('subjects', '?')} предметов, "
                               f"с оценками {data['with_marks']}, {data.get('period', '')}")
                else:
                    message = f"Готово: {data.get('subjects', '?')} предметов, {data.get('period', '')}"
                self._log(message, "ok")
                self.hub.toast(message, kind="ok")
            else:
                message = str(data.get("error") or "Неизвестная ошибка")
                self._log(message, "error")
                self.hub.toast(message[:100], kind="error")
                self._refresh_session_card()
            self.hub.set_status("Готово")

        def on_error(exc):
            self._busy = False
            self.btn_refresh.configure(state="normal")
            self._log(f"Сбой: {exc}", "error")
            self.hub.toast(f"Сбой: {exc}", kind="error")
            self.hub.set_status("Готово")

        self.hub.run_bg(work, on_done, on_error)

    def _refresh(self) -> None:
        if self.cfg is None:
            self.hub.toast("Ядро diary-grades не найдено", kind="error")
            return
        try:
            has_session = bool(self.cfg.session_exists())
        except Exception:  # noqa: BLE001
            has_session = False
        if not has_session:
            self._log("Сессии нет — открываем браузер для входа через Госуслуги.", "warn")
            self._login()
            return
        self._start_bg("Забираем оценки из дневника (браузер скрыт)…",
                       _DIARY_FETCH_CODE, (), 180)

    def _login(self) -> None:
        if self.cfg is None:
            return
        self._log("Открываем Chromium: войди через Госуслуги (ЕСИА).")
        self._start_bg("Ожидаем ручной вход в ЕСИА…", _DIARY_LOGIN_CODE, (), 420)

    def _demo(self) -> None:
        if self.gm is None:
            return
        result = self.demo.demo_result(self.cfg.current_period().label)
        self.reports = self.gm.sort_reports(
            self.gm.build_reports(result.subjects), self._sort_mode)
        self.source_label = "ДЕМО-ДАННЫЕ"
        self.demo_note = "  ·  не реальные оценки"
        self.period_label = result.period
        self.lbl_source.configure(text=self.source_label, fg=WARN)
        self.lbl_period.configure(text=self.period_label)
        self.lbl_count.configure(text=str(len(self.reports)))
        self.cache_stamp = self._stamp()
        self._render_rows()
        self._log("Показаны демо-данные; кэш реальных оценок не перезаписан.", "warn")

    def _import_file(self) -> None:
        path = filedialog.askopenfilename(
            parent=self, title="Сохранённая страница оценок или JSON дневника",
            filetypes=[("HTML / JSON / TXT", "*.html *.htm *.json *.txt"),
                       ("Все файлы", "*.*")])
        if not path:
            return
        self._start_bg(f"Импорт: {Path(path).name}", _DIARY_IMPORT_CODE, (path,), 90)

    def _open_app(self, *args: str) -> None:
        pythonw = self._python("pythonw.exe")
        try:
            if pythonw is not None:
                command = [str(pythonw), str(self.dir / "app.pyw"), *args]
            else:
                command = ["cmd.exe", "/c", str(self.dir / "run.bat")]
            subprocess.Popen(command, cwd=str(self.dir),
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        except OSError as exc:
            self._log(f"Не удалось открыть окно: {exc}", "error")
            self.hub.toast("Не удалось открыть окно дневника", kind="error")
            return
        self._log("Отдельное окно приложения запущено.")
        self.hub.toast("Окно дневника открыто", kind="ok")

    def _open_folder(self) -> None:
        try:
            os.startfile(str(self.dir))
        except Exception as exc:  # noqa: BLE001
            self.hub.toast(f"Папка не открылась: {exc}", kind="error")


