#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Панель «Лиды 2GIS»: LeadsPanel (сбор + инспектор + отправка) и MailWindow.

Вынесено из tools.pyw без изменения логики: LeadsPanel — вкладка сбора лидов
2GIS (таблица пула, карточка-инспектор, одиночная отправка), MailWindow
(tk.Toplevel «Рассылка ИИ»: настройки, генерация, очередь, Telethon-вход).

Модуль знает только uibase и модулей-хозяев данных; конкретный хаб ему не
нужен — всё общение через hub-мост базовой Panel (run_bg/set_status/toast/post).
Хозяева, к которым панель обращается по атрибуту в момент вызова (чтобы тесты
патчили хозяина, а не снимок имён в этом модуле):
  * leads_pool — пути данных: POOL_FILE / LEADS_DIR / MAIL_SETTINGS_PATH /
    MAIL_STATE_PATH (+ константы INSPECTOR_FIELDS / inspector_fields);
  * leads_mail — рассылка (guard: без telethon MAIL_OK=False, панель жива);
  * leads2gis (под именем leads_engine) — ниши NICHES и сбор leads_collect.
"""

from __future__ import annotations

import sys
import threading
import time
import webbrowser
from pathlib import Path

import tkinter as tk
from tkinter import simpledialog, ttk

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from uibase import (  # noqa: E402
    ACCENT,
    BG,
    BORDER,
    CARD,
    DANGER,
    FG,
    MUTED,
    OK,
    WARN,
    Panel,
    apply_dark_theme,
    dark_check,
    dark_text,
    enable_input_paste,
    flat_button,
    messagebox,
    px,
    register_palette_consumer,
    tc,
    ui_font,
)

# Токены приходят реэкспортом; регистрируем глобалы этого модуля потребителем
# палитры, чтобы живая смена темы обновляла и их (панель читает голые имена).
register_palette_consumer(globals())

# Пути лидов живут в хозяине leads_pool (frozen-aware): панель читает константы
# по атрибуту в момент вызова — тесты патчат модуль, а не копию имён.
import leads_pool  # noqa: E402
from leads_pool import INSPECTOR_FIELDS, inspector_fields  # noqa: E402

# Алиасы контактов — прежние имена вызовов в теле панели (минимальный дифф).
from leads_contacts import format_phone as _fmt_phone  # noqa: E402
from leads_contacts import short as _short_link  # noqa: E402

from scroll_settings import steps_per_wheel as _scroll_step  # noqa: E402

try:
    import leads2gis as leads_engine  # noqa: E402

    LEADS_OK = bool(getattr(leads_engine, "DEPS_OK", True))
    LEADS_ERROR = "" if LEADS_OK else "нет requests/selectolax"
except Exception as exc:  # noqa: BLE001
    leads_engine = None
    LEADS_OK = False
    LEADS_ERROR = str(exc)

try:
    import leads_mail  # noqa: E402

    MAIL_OK = True
    MAIL_ERROR = ""
except Exception as exc:  # noqa: BLE001
    leads_mail = None
    MAIL_OK = False
    MAIL_ERROR = str(exc)


def _mail_settings() -> dict:
    """Настройки рассылки одним местом: без кэша, но и без четырёх try/except.

    Заменила одинаковые блоки чтения leads_mail.load_settings(MAIL_SETTINGS_PATH)
    в _pool_days, _show_lead, _generate_message и MailWindow.__init__.
    Без модуля рассылки или при битом файле — пустой словарь (дефолты).
    """
    if not MAIL_OK or leads_mail is None:
        return {}
    try:
        return leads_mail.load_settings(leads_pool.MAIL_SETTINGS_PATH)
    except Exception:  # noqa: BLE001
        return {}


class LeadsPanel(Panel):
    """Сбор лидов 2GIS: контакты организаций + приоритет под аутрич."""

    def __init__(self, master: tk.Misc, hub) -> None:
        super().__init__(master, hub)
        self.stop_event = threading.Event()
        self.busy = False
        self._build()

    def _build(self) -> None:
        card = tk.Frame(self, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="x")
        body = tk.Frame(card, bg=CARD)
        body.pack(fill="x", padx=px(16), pady=px(14))
        body.columnconfigure(1, weight=1)
        body.columnconfigure(3, weight=1)

        self.city_var = tk.StringVar(value="moscow")
        self.query_var = tk.StringVar(value="")  # своя ниша — по умолчанию пусто
        self.cards_var = tk.StringVar(value="60")
        self.reviews_var = tk.StringVar(value="")
        self.no_site_var = tk.BooleanVar(value=True)
        self.limit_var = tk.StringVar(value="20")

        def caption(row: int, column: int, text: str) -> None:
            tk.Label(body, text=text, bg=CARD, fg=MUTED, anchor="w",
                     font=ui_font(9)).grid(
                row=row, column=column, sticky="w", padx=(0, px(8)), pady=px(5))

        # Ряд 0: город | мин. отзывов
        caption(0, 0, "Город")
        _city_opts = ["russia — вся Россия"] + [
            c for c in (leads_mail.RU_CITIES if MAIL_OK and hasattr(leads_mail, "RU_CITIES") else [])
        ]
        _city_cb = ttk.Combobox(
            body, textvariable=self.city_var, values=_city_opts, width=20
        )
        _city_cb.grid(row=0, column=1, sticky="ew", pady=px(5))
        caption(0, 2, "Мин. отзывов")
        ttk.Entry(body, textvariable=self.reviews_var, width=8).grid(
            row=0, column=3, sticky="w", padx=(px(16), 0), pady=px(5))

        # Ряд 1: карточек всего (делятся между городами и нишами) | лимит лидов в итоговой выборке
        caption(1, 0, "Карточек")
        ttk.Spinbox(body, from_=12, to=600, increment=12, width=6,
                    textvariable=self.cards_var).grid(
            row=1, column=1, sticky="w", pady=px(5))
        caption(1, 2, "Лимит лидов")
        ttk.Combobox(body, textvariable=self.limit_var,
                     values=("10", "20", "50", "100"), width=6,
                     state="readonly").grid(
            row=1, column=3, sticky="w", padx=(px(16), 0), pady=px(5))

        # Ряд 2: популярные ниши — сетка чекбоксов 4 × 3, первая отмечена
        niches_box = tk.Frame(body, bg=CARD)
        niches_box.grid(row=2, column=0, columnspan=4, sticky="w", pady=(px(4), 0))
        tk.Label(niches_box, text="Ниши", bg=CARD, fg=MUTED, anchor="w",
                 font=ui_font(9)).pack(anchor="w")
        niches = tk.Frame(niches_box, bg=CARD)
        niches.pack(anchor="w")
        self.niche_vars: list[tuple[str, tk.BooleanVar]] = []
        for i, (label, niche_query) in enumerate(getattr(leads_engine, "NICHES", ())):
            var = tk.BooleanVar(value=(i == 0))
            self.niche_vars.append((niche_query, var))
            dark_check(niches, label, var, bg=CARD).grid(
                row=i // 4, column=i % 4, sticky="w",
                padx=(0, px(12)), pady=px(2))

        # Ряд 3: своя ниша — добавится к выбранным чекбоксам
        caption(3, 0, "Своя ниша")
        ttk.Entry(body, textvariable=self.query_var).grid(
            row=3, column=1, columnspan=2, sticky="ew", pady=px(5))
        tk.Label(body, text="добавится к выбранным нишам", bg=CARD, fg=MUTED,
                 font=ui_font(8)).grid(
            row=3, column=3, sticky="w", padx=(px(8), 0), pady=px(5))

        tk.Label(body, text="12 фирм на страницу выдачи 2ГИС", bg=CARD, fg=MUTED,
                 font=ui_font(8)).grid(row=4, column=0, columnspan=4, sticky="w")

        options = tk.Frame(body, bg=CARD)
        options.grid(row=5, column=0, columnspan=4, sticky="w", pady=(px(8), 0))
        dark_check(options, "только без сайта", self.no_site_var, bg=CARD).pack(side="left")

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(px(14), 0))
        self.start_btn = flat_button(actions, "Собрать лидов", self.start, kind="primary")
        self.start_btn.pack(side="left")
        self.stop_btn = flat_button(actions, "Стоп", self.stop_run, kind="ghost")
        self.stop_btn.pack(side="left", padx=(px(8), 0))
        flat_button(actions, "Обновить", self.refresh, kind="ghost").pack(
            side="left", padx=(px(8), 0))
        flat_button(actions, "Заполнить тему", self.fill_topics, kind="ghost").pack(
            side="left", padx=(px(8), 0))
        flat_button(actions, "Написал вручную", self.mark_manual, kind="ghost").pack(
            side="left", padx=(px(8), 0))
        self.inspector_toggle_btn = flat_button(
            actions, "Скрыть карточку", self._toggle_inspector, kind="ghost")
        self.inspector_toggle_btn.pack(side="left", padx=(px(8), 0))
        flat_button(actions, "Рассылка ИИ", self.open_mail, kind="primary").pack(
            side="left", padx=(px(8), 0))
        self.status_var = tk.StringVar(value="Готово")
        ttk.Label(actions, textvariable=self.status_var, style="Muted.TLabel").pack(side="right")

        # -- двухпанельная компоновка: таблица слева, карточка справа ------- #
        self.split = ttk.PanedWindow(self, orient="horizontal")
        self.split.pack(fill="both", expand=True, pady=(px(10), 0))

        self.table_side = ttk.Frame(self.split)
        cols = ("name", "topic", "contact", "gis")
        self.result_tree = ttk.Treeview(
            self.table_side, columns=cols, show="headings", height=9)
        for col, title, width in (
            ("name", "Название", 220), ("topic", "Тема", 150),
            ("contact", "Контакты", 230), ("gis", "2ГИС", 120),
        ):
            self.result_tree.heading(col, text=title)
            self.result_tree.column(col, width=px(width), anchor="w")
        rscroll = ttk.Scrollbar(self.table_side, orient="vertical",
                                command=self.result_tree.yview)
        self.result_tree.configure(yscrollcommand=rscroll.set)
        self.result_tree.grid(row=0, column=0, sticky="nsew")
        rscroll.grid(row=0, column=1, sticky="ns")
        self.table_side.rowconfigure(0, weight=1)
        self.table_side.columnconfigure(0, weight=1)
        self.result_tree.bind("<ButtonRelease-1>", self._on_single_click)
        self.result_tree.bind("<Double-1>", self._open_row_link)
        self.result_tree.bind("<Button-3>", self._row_menu)
        self.result_tree.bind("<<TreeviewSelect>>", self._on_select)
        self._row_links: dict[str, list[tuple[str, str]]] = {}
        self._row_urls: dict[str, str] = {}
        self._row_phones: dict[str, str] = {}
        self._rows: dict[str, dict] = {}
        # Написанным лидам (есть sent_at или статус «написан») место не в выдаче.
        self._hidden_contacted = 0

        self.inspector = tk.Frame(
            self.split, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        ins_canvas = tk.Canvas(self.inspector, bg=CARD, highlightthickness=0, bd=0)
        ins_scroll = ttk.Scrollbar(self.inspector, orient="vertical", command=ins_canvas.yview)
        ins_canvas.configure(yscrollcommand=ins_scroll.set)
        ins_canvas.pack(side="left", fill="both", expand=True)
        ins_scroll.pack(side="right", fill="y")

        ins_wrap = tk.Frame(ins_canvas, bg=CARD)
        ins_id = ins_canvas.create_window((0, 0), window=ins_wrap, anchor="nw")

        def _fit_ins(_event=None) -> None:
            ins_canvas.configure(scrollregion=ins_canvas.bbox("all"))
            ins_canvas.itemconfigure(ins_id, width=ins_canvas.winfo_width())

        ins_wrap.bind("<Configure>", _fit_ins)
        ins_canvas.bind("<Configure>", _fit_ins)

        def _ins_wheel(event):
            if event.widget in (self.msg_text, self.prompt_text):
                return None
            ins_canvas.yview_scroll(int(-event.delta / 120) * _scroll_step(), "units")
            return "break"

        ins_canvas.bind("<MouseWheel>", _ins_wheel)
        ins_wrap.bind("<MouseWheel>", _ins_wheel)

        ins = tk.Frame(ins_wrap, bg=CARD)
        ins.pack(fill="both", expand=True, padx=px(14), pady=px(12))
        ins.bind("<MouseWheel>", _ins_wheel)
        tk.Label(ins, text="Карточка лида", bg=CARD, fg=FG,
                 font=ui_font(11, "bold")).pack(anchor="w", pady=(0, px(10)))

        self.detail_vars: dict[str, tk.StringVar] = {
            key: tk.StringVar(value="") for key in INSPECTOR_FIELDS
        }
        labels = {
            "name": "Название", "topic": "Ниша", "address": "Адрес",
            "rating": "Рейтинг", "phone": "Телефон",
            "messengers": "Мессенджеры",
        }
        for key in INSPECTOR_FIELDS:
            label = labels[key]
            line = tk.Frame(ins, bg=CARD)
            line.pack(fill="x", pady=px(2))
            tk.Label(line, text=label, bg=CARD, fg=MUTED, font=ui_font(9),
                     width=12, anchor="w").pack(side="left")
            tk.Label(line, textvariable=self.detail_vars[key], bg=CARD, fg=FG,
                     font=ui_font(9), anchor="w", justify="left",
                     wraplength=px(220)).pack(side="left", fill="x", expand=True)

        tk.Label(ins, text="Текст сообщения", bg=CARD, fg=MUTED,
                 font=ui_font(9)).pack(anchor="w", pady=(px(10), px(2)))
        msg_box = tk.Frame(ins, bg=CARD)
        msg_box.pack(fill="x")
        msg_box.rowconfigure(0, weight=1)
        msg_box.columnconfigure(0, weight=1)
        self.msg_text = dark_text(msg_box, height=5)
        msg_scroll = ttk.Scrollbar(msg_box, orient="vertical",
                                   command=self.msg_text.yview)
        self.msg_text.configure(yscrollcommand=msg_scroll.set)
        self.msg_text.grid(row=0, column=0, sticky="nsew")
        msg_scroll.grid(row=0, column=1, sticky="ns")
        flat_button(ins, "Сгенерировать ИИ", self._generate_message,
                    kind="ghost").pack(anchor="w", pady=(px(6), 0))

        tk.Label(ins, text="Промпт для сайта", bg=CARD, fg=MUTED,
                 font=ui_font(9)).pack(anchor="w", pady=(px(10), px(2)))
        prompt_box = tk.Frame(ins, bg=CARD)
        prompt_box.pack(fill="x")
        prompt_box.rowconfigure(0, weight=1)
        prompt_box.columnconfigure(0, weight=1)
        self.prompt_text = dark_text(prompt_box, height=6)
        prompt_scroll = ttk.Scrollbar(prompt_box, orient="vertical",
                                      command=self.prompt_text.yview)
        self.prompt_text.configure(yscrollcommand=prompt_scroll.set)
        self.prompt_text.grid(row=0, column=0, sticky="nsew")
        prompt_scroll.grid(row=0, column=1, sticky="ns")
        prompt_actions = tk.Frame(ins, bg=CARD)
        prompt_actions.pack(fill="x", pady=(px(6), 0))
        flat_button(prompt_actions, "📋 Скопировать промпт", self._copy_prompt,
                    kind="primary").pack(side="left")
        flat_button(prompt_actions, "Сгенерировать промпт ИИ",
                    self._generate_site_prompt, kind="ghost").pack(
            side="left", padx=(px(8), 0))
        self.prompt_path_var = tk.StringVar(value="")
        tk.Label(ins, textvariable=self.prompt_path_var, bg=CARD, fg=MUTED,
                 font=ui_font(8), wraplength=px(260), anchor="w",
                 justify="left").pack(anchor="w", fill="x", pady=(px(6), 0))

        # -- ручное подтверждение: канал и одиночная отправка ---------------- #
        self.channel_var = tk.StringVar(value="")
        self._channel_map: dict[str, str] = {}  # подпись канала → канал
        self._sending = False
        channel_line = tk.Frame(ins, bg=CARD)
        channel_line.pack(fill="x", pady=(px(10), 0))
        tk.Label(channel_line, text="Канал", bg=CARD, fg=MUTED, font=ui_font(9),
                 width=12, anchor="w").pack(side="left")
        self.channel_box = ttk.Combobox(
            channel_line, textvariable=self.channel_var, state="readonly", width=14)
        self.channel_box.pack(side="left")
        send_row = tk.Frame(ins, bg=CARD)
        send_row.pack(fill="x", pady=(px(8), 0))
        flat_button(send_row, "Отправить", self._send_one, kind="primary").pack(side="left")
        flat_button(send_row, "Пропустить", self._skip_lead, kind="ghost").pack(
            side="left", padx=(px(8), 0))
        flat_button(send_row, "Удалить из пула", self._remove_lead, kind="ghost").pack(
            side="left", padx=(px(8), 0))

        self._inspector_visible = True
        self.split.add(self.table_side, weight=3)
        self.split.add(self.inspector, weight=2)

        log_frame, self.log_box = self.make_log(self, height=6)
        log_frame.pack(fill="x", pady=(px(10), 0))
        self.append_log(
            self.log_box,
            "Город — слаг 2GIS: moscow, spb, kazan, ekaterinburg, novosibirsk. "
            "Главный сигнал лида — отсутствие сайта. Горячие копятся в пуле на 5 дней.",
        )
        self._set_buttons(running=False)
        self._clear_inspector()
        self.refresh()

    # -- действия ---------------------------------------------------------- #
    def open_mail(self) -> None:
        if not MAIL_OK:
            messagebox.showerror(
                "Рассылка ИИ", "Нет модуля рассылки:\n" + (MAIL_ERROR or "leads_mail.py"),
                parent=self,
            )
            return
        if getattr(self, "_mail_win", None) is not None and self._mail_win.winfo_exists():
            self._mail_win.lift()
            self._mail_win.focus_force()
            return
        self._mail_win = MailWindow(self.hub, leads_pool.POOL_FILE)

    def _selected_queries(self) -> list[str]:
        """Запросы отмеченных ниш + своя ниша (без дублей, порядок сохранён)."""
        queries = [q for q, var in self.niche_vars if var.get()]
        own = self.query_var.get().strip()
        if own and own.lower() not in {q.lower() for q in queries}:
            queries.append(own)
        return queries

    def start(self) -> None:
        if self.busy:
            return
        if not LEADS_OK:
            messagebox.showerror(
                "Лиды 2GIS",
                "Нет зависимостей: " + (LEADS_ERROR or "requests, selectolax")
                + "\n\nУстанови: pip install requests selectolax",
                parent=self,
            )
            return

        city = self.city_var.get().strip()
        if city.lower().startswith("russia"):  # «russia — вся Россия» → «russia»
            city = "russia".lower()
        queries = self._selected_queries()
        if not city or not queries:
            messagebox.showwarning(
                "Лиды 2GIS", "Выберите город и хотя бы одну нишу.", parent=self)
            return

        try:
            cards = max(12, min(600, int(self.cards_var.get())))
        except ValueError:
            cards = 60
        try:
            limit = int(self.limit_var.get())
        except ValueError:
            limit = 20
        try:
            min_reviews = int(self.reviews_var.get()) if self.reviews_var.get().strip() else None
        except ValueError:
            min_reviews = None
        only_no_site = bool(self.no_site_var.get())

        self.stop_event.clear()
        self.busy = True
        self._set_buttons(running=True)
        self.status_var.set("Собираю…")
        for item in self.result_tree.get_children():
            self.result_tree.delete(item)
        self.append_log(
            self.log_box,
            f"старт: {city} / ниши: {', '.join(queries)}, карточек {cards}, лимит {limit}",
        )

        def work() -> dict:
            return leads_engine.leads_collect(
                city, queries, cards, only_no_site, min_reviews,
                lambda text: self.hub.post(lambda text=text: self.status_var.set(text)),
                lambda text: self.hub.post(lambda text=text: self.append_log(self.log_box, text)),
                self.stop_event,
                limit=limit,
            )

        self.hub.run_bg(work, self._done, self._fail)

    def stop_run(self) -> None:
        if not self.busy:
            return
        self.stop_event.set()
        self.status_var.set("Останавливаю…")
        self.append_log(self.log_box, "остановка по кнопке")

    # -- данные ------------------------------------------------------------ #
    def _pool_days(self) -> float:
        settings = _mail_settings()
        try:
            return max(1.0, float(str(settings.get("pool_days", "5")).replace(",", ".")))
        except (TypeError, ValueError):
            return 5.0

    def _contact_links(self, row: dict) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        for channel, title in (("telegram", "Telegram"), ("vk", "ВКонтакте"),
                               ("max", "MAX"), ("email", "Почта")):
            url = leads_mail._to_link(channel, row.get(channel))
            if url:
                out.append((title, url))
        return out

    def _contact_primary(self, row: dict) -> str:
        """Самый приоритетный канал — одной строкой, без дублей номера."""
        for channel, title in (("telegram", "Telegram"), ("vk", "ВКонтакте"),
                               ("max", "MAX"), ("email", "Почта")):
            raw = str(row.get(channel) or "").strip()
            if raw:
                return f"{title}: {_short_link(leads_mail._to_link(channel, raw))}"
        phone = str(row.get("phone") or "").strip()
        if phone:
            return "Телефон: " + _fmt_phone(phone)
        return "—"

    def fill_topics(self) -> None:
        """Заполняет пустой topic у записей пула текущим поисковым запросом."""
        query = self.query_var.get().strip()
        if not query:
            # Своя ниша пуста — берём первую отмеченную из списка ниш
            query = next((q for q, var in self.niche_vars if var.get()), "")
        if not query:
            self.status_var.set("Выберите нишу или введите свою")
            return
        if not MAIL_OK:
            return
        rows = leads_mail.pool_load(leads_pool.POOL_FILE)
        changed = 0
        for row in rows:
            if not (row.get("topic") or "").strip():
                row["topic"] = query
                changed += 1
        if changed:
            try:
                leads_mail._pool_save(leads_pool.POOL_FILE, rows)
                self.status_var.set(f"Тема «{query}» → {changed} записей")
                self.refresh()
            except leads_mail.PoolBusy:
                self.status_var.set("Файл занят — закрой Excel")
        else:
            self.status_var.set("Все записи уже имеют тему")

    def refresh(self) -> None:
        if not MAIL_OK:
            self.status_var.set("нет модуля рассылки")
            return
        days = self._pool_days()
        busy = ""
        try:
            leads_mail.pool_purge(leads_pool.POOL_FILE, days)
        except getattr(leads_mail, "PoolBusy", RuntimeError) as exc:
            busy = str(exc)
        self._fill_pool()
        self._update_status(busy)

    on_show = refresh

    def _update_status(self, busy: str = "") -> None:
        """Статусная строка: объём пула и сколько написанных скрыто из выдачи."""
        days = self._pool_days()
        stats = leads_mail.pool_stats(leads_pool.POOL_FILE, days)
        tail = f" · {busy}" if busy else ""
        hidden = (f" · написанных скрыто: {self._hidden_contacted}"
                  if self._hidden_contacted else "")
        self.status_var.set(
            f"в пуле: {stats['total']} · истекают сегодня: {stats['expiring']} · "
            f"срок {days:g} дн.{hidden}{tail}")

    def _fill_pool(self) -> None:
        for item in self.result_tree.get_children():
            self.result_tree.delete(item)
        self._row_links.clear()
        self._row_urls.clear()
        self._row_phones.clear()
        self._rows.clear()
        self._hidden_contacted = 0
        rows = leads_mail.pool_load(leads_pool.POOL_FILE)
        index = 0
        for row in rows:
            if leads_mail.is_contacted(row):
                # Лиду уже писали — в выдаче ему не место (из пула не удаляем:
                # очередь рассылки в MailWindow продолжает его видеть).
                self._hidden_contacted += 1
                continue
            iid = f"r{index}"
            index += 1
            self._rows[iid] = row
            self._row_links[iid] = self._contact_links(row)
            self._row_urls[iid] = str(row.get("card") or "").strip()
            self._row_phones[iid] = str(row.get("phone") or "").strip()
            topic = str(row.get("topic") or "").strip() or "—"
            gis = "2ГИС" if self._row_urls[iid] else "—"
            self.result_tree.insert("", "end", iid=iid, values=(
                row.get("name", ""), topic, self._contact_primary(row), gis,
            ))
        # Сразу заполняем карточку первым лидом, чтобы инспектор не был пустым.
        children = self.result_tree.get_children()
        if children:
            first = children[0]
            self.result_tree.selection_set(first)
            self.result_tree.focus(first)
            self._show_lead(first)
        else:
            self._clear_inspector()

    # -- инспектор лида ---------------------------------------------------- #
    @staticmethod
    def _set_text(widget: tk.Text, text: str) -> None:
        widget.delete("1.0", "end")
        widget.insert("1.0", text)

    def _current_lead(self) -> tuple:
        """Текущий лид: из карточки, либо из выделения таблицы."""
        sel = self.result_tree.selection()
        iid = self._current_iid or (sel[0] if sel else None)
        row = self._current_row or (self._rows.get(iid, {}) if iid else {})
        return iid, row

    def _toggle_inspector(self) -> None:
        if self._inspector_visible:
            self.split.forget(self.inspector)
            self._inspector_visible = False
            self.inspector_toggle_btn.configure(text="Показать карточку")
        else:
            self.split.add(self.inspector, weight=2)
            self._inspector_visible = True
            self.inspector_toggle_btn.configure(text="Скрыть карточку")

    def _on_select(self, event=None) -> None:
        sel = self.result_tree.selection()
        if sel:
            self._show_lead(sel[0])
        else:
            self._clear_inspector()

    def _show_lead(self, iid: str) -> None:
        row = self._rows.get(iid, {})
        if not row:
            self._clear_inspector()
            return
        self._current_iid = iid
        self._current_row = row

        try:
            details = inspector_fields(row)
        except Exception:  # noqa: BLE001 - a broken pool row must not crash the card
            details = {key: "—" for key in INSPECTOR_FIELDS}
        for key in INSPECTOR_FIELDS:
            self.detail_vars[key].set(details.get(key, "—"))

        # GUI fallback: the logic layer found no messengers - use links from the row.
        if details.get("messengers") in ("—", "не найдены"):
            try:
                links = self._contact_links(row)
            except Exception:  # noqa: BLE001
                links = []
            fallback = ", ".join(f"{title}: {url}" for title, url in links)
            self.detail_vars["messengers"].set(fallback or "—")

        settings = _mail_settings()
        idea = str(settings.get("idea", "") or "")

        message = prompt = prompt_path = ""
        if leads_mail is not None:
            try:
                # Если текст для лида уже генерили (карточка или очередь рассылки) —
                # показываем сохранённый, иначе свежую заготовку.
                st = leads_mail.MailState(leads_pool.MAIL_STATE_PATH).get(leads_mail.row_id(row))
                message = str(st.get("text") or "").strip()
            except Exception:  # noqa: BLE001 - пустой журнал не должен ломать карточку
                message = ""
            if not message:
                try:
                    message = leads_mail.fallback_text(row, idea, settings)
                except Exception:
                    message = ""
            try:
                prompt, path = leads_mail.read_or_create_site_prompt(row, settings)
                prompt_path = str(path)
            except Exception:
                prompt = ""
                prompt_path = ""

        self._set_text(self.msg_text, message or "—")
        self._set_text(self.prompt_text, prompt or "—")
        self.prompt_path_var.set(prompt_path or "—")

        # Каналы отправки: только те, где у лида есть контакт, по приоритету.
        avail = leads_mail.channels_of(row) if leads_mail is not None else {}
        order = leads_mail.PRIORITY if leads_mail is not None else ()
        self._channel_map = {
            leads_mail.channel_label(ch): ch for ch in order if ch in avail
        }
        labels = list(self._channel_map)
        self.channel_box["values"] = labels
        self.channel_var.set(labels[0] if labels else "")

    def _clear_inspector(self) -> None:
        for var in self.detail_vars.values():
            var.set("")
        self._set_text(self.msg_text, "")
        self._set_text(self.prompt_text, "")
        self.prompt_path_var.set("")
        self.channel_var.set("")
        self.channel_box["values"] = []
        self._channel_map = {}
        self._current_iid = None
        self._current_row = {}

    def _generate_message(self) -> None:
        iid, row = self._current_lead()
        if not iid or not row:
            self.hub.toast("выбери лида в таблице")
            return
        self._current_iid = iid
        self._current_row = row
        settings = _mail_settings()
        idea = str(settings.get("idea", "") or "")

        def work() -> str:
            if leads_mail is None:
                return ""
            if not leads_mail._ai_configured(settings):
                return leads_mail.fallback_text(row, idea, settings)
            return (leads_mail.ai_generate(settings, row, idea)
                    or leads_mail.fallback_text(row, idea, settings))

        def on_done(text: str) -> None:
            if self._current_iid != iid:
                return  # за время запроса выделение успели переключить
            self._set_text(self.msg_text, text)

        def on_error(exc: Exception) -> None:
            self.append_log(self.log_box, f"ИИ: {exc}", "error")

        self.hub.run_bg(work, on_done, on_error)

    def _generate_site_prompt(self) -> None:
        """Перегенерировать промпт для сайта через ИИ (или по шаблону) и сохранить в файл."""
        iid, row = self._current_lead()
        if not iid or not row:
            self.hub.toast("выбери лида в таблице")
            return
        self._current_iid = iid
        self._current_row = row
        settings = _mail_settings()

        def work() -> tuple[str, str]:
            if leads_mail is None:
                return "", ""
            text = leads_mail.ai_generate_site_prompt(settings, row, save=True)
            path_str = str(leads_mail.site_prompt_file(row))
            return text, path_str

        def on_done(res: tuple[str, str]) -> None:
            if self._current_iid != iid:
                return
            text, path_str = res
            self._set_text(self.prompt_text, text or "—")
            if path_str:
                self.prompt_path_var.set(path_str)
            self.append_log(self.log_box, f"промпт для сайта обновлён · {path_str}", "ok")
            self.hub.toast("промпт обновлён")

        def on_error(exc: Exception) -> None:
            self.append_log(self.log_box, f"ИИ промпт: {exc}", "error")

        self.hub.run_bg(work, on_done, on_error)

    def _copy_prompt(self) -> None:
        iid, row = self._current_lead()
        if not iid or not row:
            self.hub.toast("выбери лида в таблице")
            return
        text = self.prompt_text.get("1.0", "end-1c")
        self.clipboard_clear()
        self.clipboard_append(text)
        self.update()
        path = None
        if leads_mail is not None:
            try:
                path = leads_mail.save_site_prompt(text, row)
            except (OSError, AttributeError):
                pass
        path_str = str(path) if path is not None else ""
        self.prompt_path_var.set(path_str)
        self.append_log(self.log_box, f"промпт скопирован · {path_str}", "ok")
        self.hub.toast("промпт скопирован")

    # -- результат сбора --------------------------------------------------- #
    def _done(self, result: dict) -> None:
        self.busy = False
        self._set_buttons(running=False)
        self.status_var.set(f"Готово: в пуле {result['total']}")
        self.append_log(
            self.log_box,
            f"готово: проверено {result['checked']}, новых горячих {result['pooled']} "
            f"(в пуле всего {result['total']})",
            "ok",
        )
        self.hub.toast(f"Горячих в пуле: {result['total']}")
        self.refresh()

    def _fail(self, exc: Exception) -> None:
        self.busy = False
        self._set_buttons(running=False)
        self.status_var.set("Ошибка")
        self.append_log(self.log_box, f"ошибка: {exc}", "error")
        messagebox.showerror("Лиды 2GIS", str(exc), parent=self)

    def _set_buttons(self, *, running: bool) -> None:
        self.start_btn.configure(state="disabled" if running else "normal")
        self.stop_btn.configure(state="normal" if running else "disabled")

    # -- контекст ---------------------------------------------------------- #
    def _on_single_click(self, event) -> None:
        """Одиночный клик по колонке «2ГИС» открывает карточку (с защитой от двойного клика)."""
        col_id = self.result_tree.identify_column(event.x)
        try:
            if self.result_tree.column(col_id, option="id") != "gis":
                return
        except Exception:
            return
        iid = self.result_tree.identify_row(event.y)
        if not iid:
            return
        now = time.monotonic()
        if now - getattr(self, "_last_gis_click", 0) < 0.4:
            return
        self._last_gis_click = now
        url = self._row_urls.get(iid, "")
        if url:
            webbrowser.open(url)

    def _open_row_link(self, event=None) -> None:
        iid = (self.result_tree.identify_row(event.y) if event is not None else None)
        if not iid:
            sel = self.result_tree.selection()
            if not sel:
                return
            iid = sel[0]
        # Если ссылка 2ГИС уже открыта одиночным кликом — не дублируем вкладку
        now = time.monotonic()
        if now - getattr(self, "_last_gis_click", 0) < 0.4:
            return
        # Двойной клик по колонке «2ГИС» — открываем карточку 2ГИС
        if event is not None:
            try:
                col_id = self.result_tree.identify_column(event.x)
                col_name = self.result_tree.column(col_id, option="id") if col_id else ""
                if col_name == "gis":
                    url = self._row_urls.get(iid, "")
                    if url:
                        webbrowser.open(url)
                    return
            except Exception:
                pass
        links = self._row_links.get(iid, [])
        if links:
            webbrowser.open(links[0][1])
        elif self._row_urls.get(iid):
            webbrowser.open(self._row_urls[iid])

    def _open_2gis(self, iid: str) -> None:
        url = self._row_urls.get(iid, "")
        if url:
            webbrowser.open(url)

    def _row_menu(self, event) -> None:
        iid = self.result_tree.identify_row(event.y)
        if not iid:
            return
        self.result_tree.selection_set(iid)
        menu = tk.Menu(self, tearoff=0, bg=CARD, fg=FG,
                       activebackground=tc("accent_fill"),
                       activeforeground=tc("on_accent"), bd=0)
        for title, url in self._row_links.get(iid, []):
            menu.add_command(label=f"{title}: {url}",
                             command=lambda u=url: webbrowser.open(u))
        if not self._row_links.get(iid):
            menu.add_command(label="ссылок нет — только телефон", state="disabled")
        menu.add_separator()
        if self._row_urls.get(iid):
            menu.add_command(label="Открыть карточку в 2ГИС",
                             command=lambda: self._open_2gis(iid))
        menu.add_command(label="Написал вручную (удалить)", command=self.mark_manual)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def mark_manual(self) -> None:
        sel = self.result_tree.selection()
        if not sel:
            self.hub.toast("выбери лида в таблице")
            return
        iid = sel[0]
        row = {"name": self.result_tree.set(iid, "name"),
               "phone": self._row_phones.get(iid, "")}
        key = leads_mail.pool_key(row)
        if not messagebox.askyesno(
                "Пул горячих", "Отметить «написал вручную»?\nЛид будет удалён из пула.",
                parent=self):
            return
        try:
            leads_mail.pool_remove(leads_pool.POOL_FILE, key)
        except getattr(leads_mail, "PoolBusy", RuntimeError) as exc:
            messagebox.showwarning("Пул горячих", str(exc), parent=self)
            return
        self.refresh()
        self.hub.toast("лид удалён из пула")

    # -- ручное подтверждение и одиночная отправка --------------------------- #
    def _next_iid(self) -> str | None:
        """Iid следующей строки после текущего выделения; None, если её нет."""
        children = self.result_tree.get_children()
        iid = self._current_iid
        if not iid:
            sel = self.result_tree.selection()
            iid = sel[0] if sel else None
        if iid not in children:
            return None
        index = children.index(iid)
        if index + 1 >= len(children):
            return None
        return children[index + 1]

    def _select_next(self) -> bool:
        """Перевести выделение на следующую строку таблицы; False, если её нет."""
        nxt = self._next_iid()
        if not nxt:
            return False
        # <<TreeviewSelect>> сам обновит карточку через _on_select.
        self.result_tree.selection_set(nxt)
        self.result_tree.focus(nxt)
        self.result_tree.see(nxt)
        return True

    def _skip_lead(self) -> None:
        """Пропустить лида: статусы не меняем, просто переходим к следующему."""
        iid, row = self._current_lead()
        if not iid or not row:
            self.hub.toast("выбери лида в таблице")
            return
        if not self._select_next():
            self.hub.toast("это последний лид в таблице")

    def _remove_lead(self) -> None:
        iid, row = self._current_lead()
        if not iid or not row:
            self.hub.toast("выбери лида в таблице")
            return
        if not MAIL_OK:
            self.hub.toast("модуль рассылки недоступен")
            return
        key = leads_mail.pool_key(row)
        if not messagebox.askyesno(
                "Лиды 2GIS", f"Удалить «{row.get('name', '')}» из пула?", parent=self):
            return
        try:
            leads_mail.pool_remove(leads_pool.POOL_FILE, key)
        except getattr(leads_mail, "PoolBusy", RuntimeError) as exc:
            self.append_log(self.log_box, f"пул: {exc}", "warn")
            return
        self.refresh()
        self.hub.toast("лид удалён из пула")

    def _send_one(self) -> None:
        """Одиночная отправка текущему лиду — без очередей и фоновых циклов."""
        iid, row = self._current_lead()
        if not iid or not row:
            self.hub.toast("выбери лида в таблице")
            return
        if not MAIL_OK:
            self.hub.toast("модуль рассылки недоступен")
            return
        if self._sending:
            self.hub.toast("отправка уже идёт")
            return
        text = self.msg_text.get("1.0", "end-1c").strip()
        if not text:
            self.hub.toast("текст сообщения пустой")
            return
        channel = self._channel_map.get(self.channel_var.get())
        contact = leads_mail.channels_of(row).get(channel) if channel else None
        if not channel or not contact:
            self.hub.toast("у лида нет контакта для этого канала")
            return
        settings = _mail_settings()
        dry = bool(settings.get("dry_run"))

        def work() -> dict:
            return leads_mail.dispatch(channel, settings, contact[0], contact[1], text)

        def on_done(info: dict) -> None:
            self._sending = False
            # Отметки как в Mailer._try_send, но журнал читаем свежим.
            state = leads_mail.MailState(leads_pool.MAIL_STATE_PATH)
            leads_mail.record_send(state, row, channel, contact[0], info, text)
            try:
                leads_mail.pool_mark_sent(leads_pool.POOL_FILE, leads_mail.pool_key(row), channel)
            except getattr(leads_mail, "PoolBusy", RuntimeError) as exc:
                self.append_log(self.log_box, f"пул: {exc}", "warn")
            self.append_log(
                self.log_box,
                f"→ {row.get('name', '')} · {leads_mail.channel_label(channel)} · {contact[0]}"
                + (" · тестовый режим" if dry else ""),
                "ok",
            )
            self.hub.toast("отправлено (тестовый режим)" if dry else "отправлено")
            if self._current_iid != iid:
                return  # пользователь уже смотрит другого лида — выдачу не дёргаем
            # Написанному лиду не место в выдаче: убираем строку точечно
            # (из пула не удаляем — очередь MailWindow продолжает его видеть).
            nxt = self._next_iid()
            if self.result_tree.exists(iid):
                self.result_tree.delete(iid)
                self._rows.pop(iid, None)
                self._hidden_contacted += 1
                self._update_status()
            if nxt and self.result_tree.exists(nxt):
                # <<TreeviewSelect>> сам обновит карточку через _on_select.
                self.result_tree.selection_set(nxt)
                self.result_tree.focus(nxt)
                self.result_tree.see(nxt)
            else:
                self.hub.toast("это был последний лид")

        def on_error(exc: Exception) -> None:
            self._sending = False
            self.append_log(self.log_box, f"отправка: {exc}", "error")
            self.hub.toast("ошибка отправки")

        self._sending = True
        self.hub.run_bg(work, on_done, on_error)


class MailWindow(tk.Toplevel):
    """Рассылка ИИ по горячим лидам: настройки, генерация, очередь, отправка."""

    def __init__(self, hub, _pool_file=None) -> None:
        super().__init__(hub)
        self.hub = hub
        self.settings_path = leads_pool.MAIL_SETTINGS_PATH
        self.state_path = leads_pool.MAIL_STATE_PATH
        self.title("Рассылка ИИ — Лиды 2GIS")
        sw, sh = self.winfo_screenwidth(), self.winfo_screenheight()
        win_w = min(px(1000), sw - px(30))
        win_h = min(px(760), sh - px(70))
        self.geometry(
            f"{win_w}x{win_h}+{max(0, (sw - win_w) // 2)}+{max(0, (sh - win_h) // 3)}")
        self.minsize(min(px(760), win_w), min(px(560), win_h))
        self.configure(bg=BG)
        self.option_add("*Font", ui_font(9))
        apply_dark_theme(self)

        self.settings = _mail_settings()
        self.state = leads_mail.MailState(self.state_path)
        self.leads: list[dict] = []
        self.stop_event = threading.Event()
        self.mailer = None
        self.vars: dict[str, tk.StringVar] = {}
        self.texts: dict[str, tk.Text] = {}

        self._build()
        enable_input_paste(self)
        self._load_leads()
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.after(80, self._activate)

    def _activate(self) -> None:
        """Окно сразу активно: иначе первый клик/набор уходит «в никуда»."""
        try:
            self.deiconify()
            self.lift()
            self.focus_force()
        except tk.TclError:
            pass

    # -- разметка ---------------------------------------------------------- #
    def _build(self) -> None:
        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True, padx=px(10), pady=px(10))

        holder = ttk.Frame(nb)
        queue = ttk.Frame(nb)
        nb.add(holder, text="  Настройки  ")
        nb.add(queue, text="  Очередь  ")

        # настройки не влезают по высоте — делаем прокрутку
        canvas = tk.Canvas(holder, bg=BG, highlightthickness=0, bd=0)
        vbar = ttk.Scrollbar(holder, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=vbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        vbar.pack(side="right", fill="y")

        cfg = ttk.Frame(canvas)
        cfg_id = canvas.create_window((0, 0), window=cfg, anchor="nw")

        def _fit(_event=None):
            canvas.configure(scrollregion=canvas.bbox("all"))
            canvas.itemconfigure(cfg_id, width=canvas.winfo_width())

        cfg.bind("<Configure>", _fit)
        canvas.bind("<Configure>", _fit)

        def _wheel(event):
            canvas.yview_scroll(int(-event.delta / 120) * _scroll_step(), "units")
            return "break"

        canvas.bind("<MouseWheel>", _wheel)
        cfg.bind("<MouseWheel>", _wheel)

        left = ttk.Frame(cfg)
        left.grid(row=0, column=0, sticky="nsew", padx=(px(6), px(18)), pady=px(8))
        right = ttk.Frame(cfg)
        right.grid(row=0, column=1, sticky="nsew", padx=(0, px(6)), pady=px(8))
        cfg.columnconfigure(0, weight=1)
        cfg.columnconfigure(1, weight=1)

        def field(parent, row, label, key, *, show=None, width=34):
            ttk.Label(parent, text=label, style="Muted.TLabel").grid(
                row=row, column=0, sticky="w", pady=px(3))
            var = self.vars.setdefault(key, tk.StringVar(value=str(self.settings.get(key, ""))))
            entry = ttk.Entry(parent, textvariable=var, width=width, show=show or "")
            entry.grid(row=row, column=1, sticky="ew", pady=px(3))
            return entry

        def area(parent, row, label, key, height=4):
            ttk.Label(parent, text=label, style="Muted.TLabel").grid(
                row=row, column=0, columnspan=2, sticky="w", pady=(px(8), px(2)))
            text = dark_text(parent, height=height)
            text.insert("1.0", str(self.settings.get(key, "")))
            text.grid(row=row + 1, column=0, columnspan=2, sticky="ew")
            self.texts[key] = text

        # ИИ
        ttk.Label(left, text="Нейросеть", foreground=ACCENT).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, px(6)))
        field(left, 1, "API URL", "ai_url")
        field(left, 2, "Модель", "ai_model")
        field(left, 3, "API-ключ", "ai_key", show="*")
        field(left, 4, "GitHub Base URL (ссылка; можно {slug})", "github_base_url")
        area(left, 5, "Идея предложения (что продаём)", "idea", 3)
        area(left, 8, "Как писать (обучение ИИ)", "style", 3)
        area(left, 11, "Шаблон промпта для сайта (пусто = стандартный)\nПоля: {name}, {rubric}, {city}, {address}, {phone}, {messengers}, {rating}, {reviews}, {github_base_url}", "site_prompt_template", 4)
        left.columnconfigure(1, weight=1)

        # Каналы
        ttk.Label(right, text="Каналы", foreground=ACCENT).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(0, px(6)))
        field(right, 1, "MAX API URL", "max_url")
        field(right, 2, "MAX токен", "max_token", show="*")
        field(right, 3, "MAX — мой чат (ответы)", "max_self")
        field(right, 4, "TG api_id", "api_id")
        field(right, 5, "TG api_hash", "api_hash")
        field(right, 6, "TG номер телефона", "tg_phone")
        field(right, 7, "TG сессия (файл)", "tg_session_file")
        tgbox = ttk.Frame(right)
        tgbox.grid(row=8, column=0, columnspan=2, sticky="w", pady=(px(4), px(8)))
        flat_button(tgbox, "Войти в TG", self._auth_tg, kind="primary").pack(side="left")
        flat_button(tgbox, "Выйти из TG", self._logout_tg, kind="ghost").pack(
            side="left", padx=(px(8), 0))
        flat_button(tgbox, "my.telegram.org", lambda: webbrowser.open(
            "https://my.telegram.org/apps"), kind="ghost").pack(
            side="left", padx=(px(8), 0))
        field(right, 9, "VK token", "vk_token", show="*")
        vkbox = ttk.Frame(right)
        vkbox.grid(row=10, column=0, columnspan=2, sticky="w", pady=(0, px(8)))
        flat_button(vkbox, "VK: где взять токен", lambda: webbrowser.open(
            "https://vkhost.github.io/"), kind="ghost").pack(side="left")
        flat_button(vkbox, "сайт не открылся", lambda: webbrowser.open(
            "https://dev.vk.com/ru/apps"), kind="ghost").pack(
            side="left", padx=(px(6), 0))
        field(right, 11, "SMTP хост", "smtp_host")
        field(right, 12, "SMTP порт", "smtp_port")
        field(right, 13, "SMTP логин", "smtp_user")
        field(right, 14, "SMTP пароль", "smtp_pass", show="*")
        field(right, 15, "Почта отправителя", "smtp_from")
        field(right, 16, "Тема письма", "mail_subject")
        right.columnconfigure(1, weight=1)

        rules = ttk.Frame(cfg)
        rules.grid(row=1, column=0, columnspan=2, sticky="w", padx=px(6), pady=(0, px(8)))
        ttk.Label(rules, text="Дней до повторного (0 = выкл)", style="Muted.TLabel").pack(side="left")
        ttk.Entry(rules, textvariable=self.vars.setdefault(
            "followup_days", tk.StringVar(value=str(self.settings.get("followup_days", "0")))),
            width=6).pack(side="left", padx=(px(6), px(20)))
        ttk.Label(rules, text="Дней в пуле горячих", style="Muted.TLabel").pack(side="left")
        ttk.Entry(rules, textvariable=self.vars.setdefault(
            "pool_days", tk.StringVar(value=str(self.settings.get("pool_days", "5")))),
            width=6).pack(side="left", padx=(px(6), px(20)))
        ttk.Label(rules, text="Пауза между сообщениями, сек", style="Muted.TLabel").pack(side="left")
        ttk.Entry(rules, textvariable=self.vars.setdefault(
            "pacing", tk.StringVar(value=str(self.settings.get("pacing", "60")))),
            width=6).pack(side="left", padx=(px(6), px(20)))
        self.dry_var = tk.BooleanVar(value=bool(self.settings.get("dry_run", False)))
        dark_check(rules, "тестовый режим (не отправлять)", self.dry_var).pack(side="left")

        # очередь
        bar = ttk.Frame(queue)
        bar.pack(fill="x", padx=px(8), pady=(px(8), px(4)))
        flat_button(bar, "Сгенерировать тексты", self._generate, kind="primary").pack(side="left")
        flat_button(bar, "Отправить (подтвердить)", self._send, kind="primary").pack(
            side="left", padx=(px(8), 0))
        flat_button(bar, "Стоп всё", self._stop_all, kind="ghost").pack(side="left", padx=(px(8), 0))
        flat_button(bar, "Обновить список", self._load_leads, kind="ghost").pack(
            side="left", padx=(px(8), 0))
        flat_button(bar, "Остановить лид", lambda: self._mark("stopped"), kind="ghost").pack(
            side="left", padx=(px(8), 0))
        flat_button(bar, "Ответил", lambda: self._mark("replied"), kind="ghost").pack(
            side="left", padx=(px(8), 0))
        flat_button(bar, "Прочитал", lambda: self._mark("read"), kind="ghost").pack(
            side="left", padx=(px(8), 0))
        flat_button(bar, "Написал вручную", self._mark_manual, kind="ghost").pack(
            side="left", padx=(px(8), 0))
        flat_button(bar, "Открыть контакт", self._open_link, kind="ghost").pack(
            side="left", padx=(px(8), 0))
        flat_button(bar, "Сбросить ошибки", self._reset_failed, kind="ghost").pack(
            side="left", padx=(px(8), 0))

        table = ttk.Frame(queue)
        table.pack(fill="both", expand=True, padx=px(8), pady=px(4))
        cols = ("name", "target", "channel", "status", "text")
        self.tree = ttk.Treeview(table, columns=cols, show="headings", height=12)
        for col, title, width in (
            ("name", "Лид", 200), ("target", "Куда (ссылка)", 250),
            ("channel", "Канал", 90), ("status", "Статус", 90), ("text", "Текст", 300),
        ):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=px(width), anchor="w")
        scroll = ttk.Scrollbar(table, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        table.rowconfigure(0, weight=1)
        table.columnconfigure(0, weight=1)
        self.tree.bind("<<TreeviewSelect>>", self._show_text)
        self.tree.bind("<Double-1>", self._open_link)

        ttk.Label(queue, text="Текст выбранного лида", style="Muted.TLabel").pack(
            anchor="w", padx=px(10), pady=(px(6), 0))
        self.preview = dark_text(queue, height=4)
        self.preview.pack(fill="x", padx=px(8), pady=(px(2), px(6)))

        self.log = dark_text(queue, height=6)
        self.log.tag_configure("ok", foreground=OK)
        self.log.tag_configure("warn", foreground=WARN)
        self.log.tag_configure("error", foreground=DANGER)
        self.log.pack(fill="both", expand=True, padx=px(8), pady=(0, px(8)))

        self.status_var = tk.StringVar(value="Готово")
        ttk.Label(self, textvariable=self.status_var, style="Muted.TLabel").pack(
            anchor="w", padx=px(14), pady=(0, px(8)))

    # -- данные ------------------------------------------------------------ #
    def _load_leads(self) -> None:
        days = self._pool_days()
        # добираем в пул горячих из leads.csv — pool_add дедуплицирует сам
        synced = gone = filled = 0
        try:
            migrated = leads_mail.migrate_legacy(leads_pool.LEADS_DIR)
            self._log(f"архив: горячих перенесено {migrated['added']}, "
                      f"контактов дозаполнено {migrated['filled']}", "ok")
            if migrated.get("archived"):
                self._log(f"старий файл {migrated['archived']} больше не нужен", "ok")
            gone = leads_mail.pool_purge(leads_pool.POOL_FILE, days)
        except getattr(leads_mail, "PoolBusy", RuntimeError) as exc:
            self._log(f"пул: {exc}", "warn")
        if gone:
            self._log(f"из пула удалено просроченных: {gone}", "warn")

        self.leads = leads_mail.load_hot_leads(leads_pool.POOL_FILE)
        self._refresh_tree()
        stats = leads_mail.pool_stats(leads_pool.POOL_FILE, days)
        tail = f", истекают сегодня: {stats['expiring']}" if stats["expiring"] else ""
        self._log(f"горячих в пуле: {len(self.leads)}{tail} · срок {days:g} дн.", "ok")

    def _pool_days(self) -> float:
        try:
            return max(1.0, float(str(self.vars.get("pool_days").get()).replace(",", ".")))
        except (AttributeError, TypeError, ValueError):
            return 5.0

    def _refresh_tree(self) -> None:
        if not self.winfo_exists():
            return
        for item in self.tree.get_children():
            self.tree.delete(item)
        self._links: dict[str, str] = {}
        for index, row in enumerate(self.leads):
            lead_id = leads_mail.row_id(row) or f"row{index}"
            st = self.state.get(lead_id)
            avail = leads_mail.channels_of(row)
            channel = st.get("channel") or next(
                (c for c in leads_mail.PRIORITY if c in avail), "")
            link = ""
            if channel in avail:
                link = leads_mail.contact_link(channel, avail[channel])
                target = st.get("target") or leads_mail.contact_display(channel, avail[channel])
            else:
                target = st.get("target") or ""
            self._links[lead_id] = link
            status = st.get("status") or "—"
            text = (st.get("text") or "").replace("\n", " ")
            self.tree.insert("", "end", iid=lead_id, values=(
                row.get("name", ""), link or target, channel, status, text[:90]))

    def _open_link(self, _event=None) -> None:
        """Открывает ссылку на контакт выбранного лида в браузере."""
        sel = self.tree.selection()
        if not sel:
            self._log("выбери лида в таблице", "warn")
            return
        url = getattr(self, "_links", {}).get(sel[0], "")
        if not url:
            self._log(f"{sel[0]}: ссылки на контакт нет (телефон или пустой канал)", "warn")
            return
        webbrowser.open(url)
        self._log(f"открыто: {url}", "ok")

    def _show_text(self, _event=None) -> None:
        sel = self.tree.selection()
        if not sel:
            return
        st = self.state.get(sel[0])
        self.preview.delete("1.0", "end")
        self.preview.insert("1.0", st.get("text") or "")

    # -- настройки --------------------------------------------------------- #
    def _collect(self) -> dict:
        data = dict(self.settings)
        for key, var in self.vars.items():
            data[key] = var.get()
        for key, text in self.texts.items():
            data[key] = text.get("1.0", "end").strip()
        data["dry_run"] = bool(self.dry_var.get())
        return data

    # -- действия ---------------------------------------------------------- #
    def _generate(self) -> None:
        if not self.leads:
            self._log("нечего генерировать: нет горячих лидов", "warn")
            return
        self.settings = self._collect()
        leads_mail.save_settings(self.settings_path, self.settings)
        self.stop_event.clear()
        self.status_var.set("Генерирую тексты…")

        def work():
            return leads_mail.generate_texts(
                self.leads, self.settings, self.state,
                lambda text, tag=None: self._post_log(text, tag),
                lambda text: self.hub.post(lambda t=text: self.status_var.set(t) if self.winfo_exists() else None),
                self.stop_event,
            )

        self.hub.run_bg(work, self._generated, self._failed)

    def _generated(self, count) -> None:
        if not self.winfo_exists():
            return
        self.status_var.set(f"Готово: текстов {count}")
        self._refresh_tree()
        self._log(f"тексты готовы: {count}", "ok")

    def _send(self) -> None:
        if not self.leads:
            self._log("нечего отправлять", "warn")
            return
        pending = [r for r in self.leads
                   if self.state.get(leads_mail.row_id(r)).get("status")
                   not in ("replied", "stopped", "read", "seen", "closed", "failed")]
        if not pending:
            self._log("все лиды уже обработаны", "warn")
            return
        if not messagebox.askyesno(
            "Подтвердить рассылку",
            f"Отправить сообщения {len(pending)} горячим лидам?\n\n"
            "По одному в минуту. Через 5 дней без ответа — следующий канал.",
            parent=self,
        ):
            return

        self.settings = self._collect()
        leads_mail.save_settings(self.settings_path, self.settings)
        self.stop_event.clear()
        self.status_var.set("Рассылка идёт…")

        mailer = leads_mail.Mailer(
            self.leads, self.settings, self.state,
            lambda text, tag=None: self._post_log(text, tag),
            lambda _event: self.hub.post(self._refresh_tree),
            pool_file=leads_pool.POOL_FILE,
        )
        self.mailer = mailer
        threading.Thread(target=mailer.run_forever, args=(self.stop_event,), daemon=True).start()

        if not self.settings.get("dry_run"):
            watcher = leads_mail.Poller(
                self.leads, self.settings, self.state,
                lambda text, tag=None: self._post_log(text, tag), self.stop_event,
                pool_file=leads_pool.POOL_FILE,
            )
            threading.Thread(target=watcher.run_forever, daemon=True).start()
        self._log("отправка запущена в фоне", "ok")

    def _stop_all(self) -> None:
        self.stop_event.set()
        self.status_var.set("Остановлено")
        self._log("остановлено вручную", "warn")

    def _mark(self, status: str) -> None:
        sel = self.tree.selection()
        if not sel:
            self._log("выбери лида в таблице", "warn")
            return
        st = self.state.get(sel[0])
        st["status"] = status
        st["closed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.state.save()
        try:
            leads_mail.pool_mark_status(leads_pool.POOL_FILE, sel[0], status)
        except (OSError, leads_mail.PoolBusy) as exc:
            self._log(f"пул: {exc}", "warn")
        self._refresh_tree()
        self._log(f"{sel[0]}: {status}", "ok")

    def _mark_manual(self) -> None:
        """Я написал этому лиду сам — из пула он больше не выпадет."""
        sel = self.tree.selection()
        if not sel:
            self._log("выбери лида в таблице", "warn")
            return
        lead_id = sel[0]
        st = self.state.get(lead_id)
        st["status"] = "awaiting"
        st["sent_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        st["channel"] = st.get("channel") or "manual"
        self.state.save()
        try:
            leads_mail.pool_mark_sent(leads_pool.POOL_FILE, lead_id, "manual")
        except (OSError, leads_mail.PoolBusy) as exc:
            self._log(f"пул: {exc}", "warn")
        self._refresh_tree()
        self._log(f"{lead_id}: отмечено «написал вручную»", "ok")

    def _reset_failed(self) -> None:
        count = self.state.reset_failed()
        self._refresh_tree()
        self._log(f"сброшено ошибок: {count}", "ok" if count else "warn")

    # -- логи -------------------------------------------------------------- #
    def _log(self, text: str, tag: str | None = None) -> None:
        self.log.insert("end", text + "\n", tag or ())
        self.log.see("end")

    def _post_log(self, text: str, tag: str | None = None) -> None:
        self.hub.post(lambda: self._log(text, tag) if self.winfo_exists() else None)

    def _failed(self, exc: Exception) -> None:
        self.status_var.set("Ошибка")
        self._log(f"ошибка: {exc}", "error")

    # -- Telethon-авторизация (без консоли) ------------------------------- #
    def _auth_tg(self) -> None:
        if not MAIL_OK:
            self._log("модуль рассылки не загружен", "error")
            return
        self.settings = self._collect()
        leads_mail.save_settings(self.settings_path, self.settings)
        phone = (self.settings.get("tg_phone") or "").strip()
        if not phone:
            self._log("сначала введи номер телефона (TG номер телефона)", "warn")
            return

        def ask_on_main(title, prompt, secret=False):
            res = [None]
            ev = threading.Event()

            def dlg():
                try:
                    res[0] = simpledialog.askstring(
                        title, prompt, parent=self, show="*" if secret else None)
                finally:
                    ev.set()

            self.hub.post(dlg)
            ev.wait()
            return res[0]

        def work():
            return leads_mail.tg_login(
                self.settings, phone,
                ask=ask_on_main,
                on_log=lambda t, tag=None: self._post_log(t, tag),
            )

        def on_done(_res):
            self._log("TG: аккаунт привязан, сессия сохранена", "ok")

        def on_err(exc):
            self._log(f"TG: {exc}", "error")

        self._log("TG: подключение к серверу Telegram…", "info")
        self.hub.run_bg(work, on_done, on_err)

    def _logout_tg(self) -> None:
        self.settings = self._collect()
        leads_mail.save_settings(self.settings_path, self.settings)
        try:
            self._log(leads_mail.tg_logout(self.settings), "warn")
        except Exception as exc:  # noqa: BLE001
            self._log(f"TG: {exc}", "error")

    def _close(self) -> None:
        self.stop_event.set()
        self.settings = self._collect()
        leads_mail.save_settings(self.settings_path, self.settings)
        self.destroy()
