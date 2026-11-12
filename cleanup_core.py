#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Пустые файлы и папки: движок — скан и удаление.

Модуль без tkinter: только os/stat и dataclasses, поэтому скан и удаление
можно проверять тестами без графической библиотеки.

Что здесь живёт (перенесено verbatim из tools.pyw, секция
«Пустые файлы и папки: движок»):
  * константы атрибутов Windows `REPARSE_POINT` / `HIDDEN_ATTR` —
    служат `_is_reparse` и `_is_hidden` (symlink/junction и скрытые файлы);
  * `Plan` — план скана (файлы, папки, ошибки, свойства total/is_empty);
  * `DeleteResult` — итог удаления (removed_files, removed_dirs, errors);
  * `_is_hidden` / `_is_reparse` / `_scan_dir` — внутренности рекурсивного
    обхода: репарс-точки не считаются пустыми, скрытое учитывается флагом;
  * `scan_empty` — обход дерева и сбор плана;
  * `delete_plan` — удаление найденного (os.remove / os.rmdir);
  * `check_blocked` / `risk_warning` — защита от корня диска, системных
    и личных папок Windows.

Дубль кода в tools.pyw остаётся до интеграции — вычистит интегратор.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field

REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
HIDDEN_ATTR = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0x2)


@dataclass
class Plan:
    root: str
    files: list[str] = field(default_factory=list)
    dirs: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.files) + len(self.dirs)

    @property
    def is_empty(self) -> bool:
        return self.total == 0


@dataclass
class DeleteResult:
    removed_files: int = 0
    removed_dirs: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)


def _is_hidden(path: str, st: os.stat_result) -> bool:
    if os.path.basename(path).startswith("."):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & HIDDEN_ATTR)


def _is_reparse(st: os.stat_result) -> bool:
    return bool(getattr(st, "st_file_attributes", 0) & REPARSE_POINT)


def _scan_dir(path: str, plan: Plan, files: bool, dirs: bool, hidden: bool) -> bool:
    """True — папка опустеет после удаления найденного."""
    try:
        with os.scandir(path) as iterator:
            entries = list(iterator)
    except OSError as exc:
        plan.errors.append((path, str(exc)))
        return False

    files_here: list[str] = []
    dirs_here: list[str] = []
    empty_after = True

    for entry in entries:
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError as exc:
            plan.errors.append((entry.path, str(exc)))
            empty_after = False
            continue
        if entry.is_symlink() or _is_reparse(st):
            empty_after = False
        elif entry.is_dir(follow_symlinks=False):
            if dirs and _scan_dir(entry.path, plan, files, dirs, hidden):
                dirs_here.append(entry.path)
            else:
                empty_after = False
        elif entry.is_file(follow_symlinks=False):
            if files and st.st_size == 0 and (hidden or not _is_hidden(entry.path, st)):
                files_here.append(entry.path)
            else:
                empty_after = False
        else:
            empty_after = False

    plan.files.extend(files_here)
    plan.dirs.extend(dirs_here)

    if not dirs:
        return False
    if not entries:
        return True
    return empty_after and bool(files_here or dirs_here)


def scan_empty(root: str, *, files: bool = True, dirs: bool = True, hidden: bool = False) -> Plan:
    plan = Plan(root=root)
    _scan_dir(root, plan, files, dirs, hidden)
    return plan


def delete_plan(plan: Plan) -> DeleteResult:
    result = DeleteResult()
    for path in plan.files:
        try:
            os.remove(path)
            result.removed_files += 1
        except OSError as exc:
            result.errors.append((path, str(exc)))
    for path in plan.dirs:
        try:
            os.rmdir(path)
            result.removed_dirs += 1
        except OSError as exc:
            result.errors.append((path, str(exc)))
    return result


def check_blocked(path: str) -> str | None:
    full = os.path.abspath(path)
    if os.path.dirname(full) == full:
        return "Это корень диска. Выберите папку внутри него."
    normalized = os.path.normcase(full)
    for variable in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        system_path = os.environ.get(variable)
        if system_path and os.path.normcase(os.path.abspath(system_path)) == normalized:
            return "Это системная папка Windows. Выберите другую."
    return None


def risk_warning(path: str) -> str | None:
    full = os.path.normcase(os.path.abspath(path))
    for variable in ("USERPROFILE", "Desktop", "Documents", "Downloads"):
        known = os.environ.get(variable)
        if known and os.path.normcase(os.path.abspath(known)) == full:
            return "Это ваша личная папка. Проверьте список перед удалением."
    return None
