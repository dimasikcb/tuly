#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Смоук-сборка всех панелей хаба без mainloop (dev-инструмент, не unittest).

Ловит NameError/AttributeError при построении панелей после выноса кода
в модули: каждый класс инстанцируется с фейковым hub (синхронный run_bg)
и тут же уничтожается. Сеть замокана: bypass_status/app_catalog подменяются,
чтобы __init__ панелей не дёргал PowerShell/файлы.

Запуск из корня проекта:  py -3 -I tests/_smoke_panels.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from helpers import load_app, new_tk  # noqa: E402


class FakeHub:
    """Тот же контракт, что в UI-тестах: run_bg/set_status/toast/post."""

    def run_bg(self, work, on_done, on_error):
        try:
            on_done(work())
        except Exception as exc:  # noqa: BLE001
            on_error(exc)

    def set_status(self, text):
        pass

    def toast(self, message, **kwargs):
        pass

    def post(self, callback):
        callback()


PANELS = (
    "AutoClickerPanel",
    "CleanPanel",
    "DnsPanel",
    "GitHubPanel",
    "BypassPanel",
    "LeadsPanel",
    "ShortsAutopilotPanel",
    "DiaryGradesPanel",
    "AIScreenAgentPanel",
    "ShutdownWatcherPanel",
    "SettingsPanel",
)


def main() -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):
        pass
    app = load_app()
    # мокаем сетевые/процессные вызовы, которые панели делают в __init__
    app.bypass_status = lambda *a, **k: {}
    app.bypass_cfg = lambda *a, **k: {}
    app.app_catalog = lambda *a, **k: []
    app.bypass_auto_heal_if_leak = lambda *a, **k: None

    failures = 0
    import tkinter as tk

    root = new_tk(tk.Tk)
    root.withdraw()
    hub = FakeHub()
    for name in PANELS:
        cls = getattr(app, name, None)
        if cls is None:
            print(f"FAIL {name}: класса нет в модуле tools")
            failures += 1
            continue
        try:
            widget = cls(root, hub)
            widget.destroy()
            print(f"OK   {name}")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"FAIL {name}: {exc!r}")
    root.destroy()
    print("ИТОГ:", "все панели собрались" if not failures else f"провалов: {failures}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
