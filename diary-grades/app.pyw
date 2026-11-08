"""Точка входа БЕЗ консоли для Windows (запускается через pythonw.exe из run.bat).

Любая ошибка старта пишется в debug/launch_error.log и показывается системным
окном, иначе pythonw просто молча исчезнет.
"""
from __future__ import annotations

import sys
import traceback
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _report(exc: BaseException) -> None:
    text = f"{datetime.now():%Y-%m-%d %H:%M:%S}\n{traceback.format_exc()}"
    try:
        log = ROOT / "debug" / "launch_error.log"
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("a", encoding="utf-8") as fh:
            fh.write(text + "\n" + "-" * 60 + "\n")
    except OSError:
        pass
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                0,
                f"Не удалось запустить приложение:\n{exc}\n\nПодробности: debug/launch_error.log",
                "Дневник МО · Контроль балла",
                0x10,
            )
        except Exception:  # noqa: BLE001
            pass


if __name__ == "__main__":
    try:
        from app import main

        main()
    except BaseException as error:  # noqa: BLE001 — pythonw не показывает traceback
        _report(error)
        raise SystemExit(1)
