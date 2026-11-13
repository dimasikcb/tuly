#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Вкладка «Пустые файлы и папки» хаба: панель CleanPanel.

Переехала из tools.pyw verbatim, логика не менялась:
  * скан и удаление — в cleanup_core (scan_empty / delete_plan, защита
    check_blocked / risk_warning); панель только запускает план через
    hub.run_bg и рисует найденное в текстовом списке;
  * защита лога от правки — attach_copy(readonly=True) базовой Panel
    (Ctrl+C работает, ввод заглушён).

Модуль знает только uibase/cleanup_core; конкретный хаб ему не нужен — всё
общение через hub-мост базовой Panel (run_bg/set_status/toast/post).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, ttk

_HERE = str(Path(__file__).resolve().parent)
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

from uibase import (  # noqa: E402
    FG,
    MUTED,
    TAG_DIR,
    TAG_FILE,
    Panel,
    dark_check,
    dark_text,
    flat_button,
    messagebox,
    mono_font,
    px,
    register_palette_consumer,
    tc,
)

# Токены приходят реэкспортом; регистрируем глобалы этого модуля потребителем
# палитры, чтобы живая смена темы обновляла и их (панель читает голые имена).
register_palette_consumer(globals())

from cleanup_core import (  # noqa: E402
    DeleteResult,
    Plan,
    check_blocked,
    delete_plan,
    risk_warning,
    scan_empty,
)


class CleanPanel(Panel):
    LIMIT = 2000

    def __init__(self, master: tk.Misc, hub) -> None:
        super().__init__(master, hub)
        self.plan: Plan | None = None
        self.busy = False
        self._build()

    def _build(self) -> None:
        row = ttk.Frame(self)
        row.pack(fill="x")
        ttk.Label(row, text="Папка").pack(side="left")
        self.folder_var = tk.StringVar()
        self.entry = ttk.Entry(row, textvariable=self.folder_var)
        self.entry.pack(side="left", fill="x", expand=True, padx=(px(10), px(8)))
        self.entry.bind("<Return>", lambda _event: self.start_scan())
        self.browse_btn = flat_button(row, "Обзор…", self.choose_folder, kind="ghost")
        self.browse_btn.pack(side="left")

        options = ttk.Frame(self)
        options.pack(fill="x", pady=(px(12), 0))
        self.files_var = tk.BooleanVar(value=True)
        self.dirs_var = tk.BooleanVar(value=True)
        self.hidden_var = tk.BooleanVar(value=False)
        for text, variable, pad in (
            ("Пустые файлы (0 байт)", self.files_var, 0),
            ("Пустые папки", self.dirs_var, px(18)),
            ("Учитывать скрытые", self.hidden_var, px(18)),
        ):
            dark_check(options, text, variable).pack(side="left", padx=(pad, 0))

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(px(14), 0))
        self.scan_btn = flat_button(actions, "Проверить", self.start_scan)
        self.scan_btn.pack(side="left")
        self.delete_btn = flat_button(actions, "Удалить найденное", self.start_delete,
                                      kind="danger", state="disabled")
        self.delete_btn.pack(side="left", padx=(px(8), 0))

        self.summary_var = tk.StringVar(value="Выберите папку и нажмите «Проверить».")
        ttk.Label(self, textvariable=self.summary_var, style="Muted.TLabel").pack(
            anchor="w", pady=(px(12), px(6)))

        wrap = ttk.Frame(self)
        wrap.pack(fill="both", expand=True)
        bar = ttk.Frame(wrap)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, px(4)))
        ttk.Label(bar, text="Список — выдели мышью, Ctrl+C или «Копировать всё»",
                  style="Muted.TLabel").pack(side="left")
        copy_btn = flat_button(bar, "Копировать всё", lambda: None, kind="ghost")
        copy_btn.pack(side="right")
        copy_btn.configure(command=lambda: self.copy_log(self.list, copy_btn, label="Список"))
        self.list = dark_text(wrap, height=14, wrap="none")
        scroll_y = ttk.Scrollbar(wrap, orient="vertical", command=self.list.yview)
        scroll_x = ttk.Scrollbar(wrap, orient="horizontal", command=self.list.xview)
        self.list.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        self.list.grid(row=1, column=0, sticky="nsew")
        scroll_y.grid(row=1, column=1, sticky="ns")
        scroll_x.grid(row=2, column=0, sticky="ew")
        wrap.rowconfigure(1, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.list.tag_configure("file", foreground=TAG_FILE)
        self.list.tag_configure("dir", foreground=TAG_DIR)
        self.list.tag_configure("error", foreground=tc("log_err"))
        self.list.tag_configure("head", foreground=FG, font=mono_font(9, "bold"))
        self.list.tag_configure("hint", foreground=MUTED)
        self.attach_copy(self.list, label="Список", readonly=True)
        self._write("Список пуст. Выберите папку и нажмите «Проверить».\n", "hint")

    # -- помощники --------------------------------------------------------- #
    def _write(self, text: str, tag: str | None = None) -> None:
        # Не выключаем state в "disabled": иначе виджет перестаёт ловить
        # клавиатуру и Ctrl+C не срабатывает. Защиту от правки даёт attach_copy
        # (readonly=True), которая глушит ввод, но оставляет фокус/выделение.
        self.list.configure(state="normal")
        self.list.insert("end", text, (tag,) if tag else ())

    def _clear_list(self) -> None:
        self.list.configure(state="normal")
        self.list.delete("1.0", "end")

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.entry.configure(state=state)
        self.browse_btn.configure(state=state)
        self.scan_btn.configure(state=state)
        if busy or self.plan is None or self.plan.is_empty:
            self.delete_btn.configure(state="disabled")
        else:
            self.delete_btn.configure(state="normal")

    def _fail(self, exc: Exception) -> None:
        self._set_busy(False)
        self.summary_var.set("Не удалось выполнить операцию")
        self.hub.set_status("Ошибка")
        messagebox.showerror("Пустые файлы", str(exc))

    def choose_folder(self) -> None:
        initial = self.folder_var.get().strip()
        if not (initial and os.path.isdir(initial)):
            initial = str(Path.home())
        chosen = filedialog.askdirectory(
            title="Выберите папку для очистки", initialdir=initial, mustexist=True
        )
        if chosen:
            self.folder_var.set(os.path.normpath(chosen))
            self.start_scan()

    def _current_folder(self) -> str | None:
        raw = self.folder_var.get().strip().strip('"')
        if not raw:
            messagebox.showinfo("Пустые файлы", "Сначала выберите папку.")
            return None
        root = os.path.abspath(os.path.expanduser(raw))
        if not os.path.isdir(root):
            messagebox.showerror("Пустые файлы", f"Папка не найдена:\n{root}")
            return None
        blocked = check_blocked(root)
        if blocked:
            messagebox.showerror("Пустые файлы", blocked)
            return None
        return root

    # -- поиск ------------------------------------------------------------- #
    def start_scan(self) -> None:
        if self.busy:
            return
        root = self._current_folder()
        if root is None:
            return
        files, dirs, hidden = self.files_var.get(), self.dirs_var.get(), self.hidden_var.get()
        if not files and not dirs:
            messagebox.showinfo("Пустые файлы", "Отметьте, что искать: файлы и/или папки.")
            return

        self.plan = None
        self._set_busy(True)
        self._clear_list()
        self.summary_var.set("Ищу пустые файлы и папки…")
        self.hub.set_status(f"Сканирую: {root}")
        self.hub.run_bg(
            lambda: scan_empty(root, files=files, dirs=dirs, hidden=hidden),
            self._scan_done,
            self._fail,
        )

    def _scan_done(self, plan: Plan) -> None:
        self.plan = plan
        self._set_busy(False)
        self._clear_list()
        self._write(f"Папка: {plan.root}\n", "head")

        if plan.is_empty:
            self.summary_var.set("Пустых файлов и папок не найдено.")
            self.hub.set_status("Ничего удалять не нужно")
            self._write("Пустых файлов и папок не найдено.\n", "head")
            if plan.errors:
                self._write("\nНе удалось прочитать:\n", "error")
                for path, reason in plan.errors:
                    self._write(f"  {path} — {reason}\n", "error")
            return

        shown = 0
        for path in plan.files:
            if shown >= self.LIMIT:
                break
            self._write(f"[файл]  {path}\n", "file")
            shown += 1
        for path in plan.dirs:
            if shown >= self.LIMIT:
                break
            self._write(f"[папка] {path}\n", "dir")
            shown += 1
        if shown < plan.total:
            self._write(f"\n… и ещё {plan.total - shown} (список сокращён)\n", "head")
        if plan.errors:
            self._write("\nНе удалось прочитать:\n", "error")
            for path, reason in plan.errors:
                self._write(f"  {path} — {reason}\n", "error")

        self.summary_var.set(
            f"Найдено: файлов — {len(plan.files)}, папок — {len(plan.dirs)}."
        )
        self.hub.set_status("Проверьте список и нажмите «Удалить найденное»")

    # -- удаление ---------------------------------------------------------- #
    def start_delete(self) -> None:
        plan = self.plan
        if self.busy or plan is None or plan.is_empty:
            return
        message = (
            "Удалить безвозвратно?\n\n"
            f"Пустых файлов: {len(plan.files)}\n"
            f"Пустых папок: {len(plan.dirs)}\n\n"
            f"Папка: {plan.root}"
        )
        warning = risk_warning(plan.root)
        if warning:
            message += f"\n\n{warning}"
        if not messagebox.askyesno("Удалить безвозвратно?", message):
            return

        self._set_busy(True)
        self._clear_list()
        self.hub.set_status("Удаляю…")
        self.hub.run_bg(lambda: delete_plan(plan), self._delete_done, self._fail)

    def _delete_done(self, result: DeleteResult) -> None:
        self.plan = None
        self._set_busy(False)
        self._clear_list()
        self._write("Готово.\n", "head")
        self._write(f"Удалено файлов: {result.removed_files}\n")
        self._write(f"Удалено папок:  {result.removed_dirs}\n")
        if result.errors:
            self._write("\nНе удалось удалить:\n", "error")
            for path, reason in result.errors:
                self._write(f"  {path} — {reason}\n", "error")
        self.summary_var.set(
            f"Удалено: файлов {result.removed_files}, папок {result.removed_dirs}"
            + (f". Ошибок: {len(result.errors)}" if result.errors else ".")
        )
        self.hub.set_status("Готово")
        if result.errors:
            messagebox.showwarning(
                "Пустые файлы",
                f"Удалено файлов: {result.removed_files}, папок: {result.removed_dirs}.\n"
                f"Не удалось удалить: {len(result.errors)} (подробности в списке).",
            )
        else:
            messagebox.showinfo(
                "Пустые файлы",
                f"Готово.\nУдалено файлов: {result.removed_files}, папок: {result.removed_dirs}.",
            )
