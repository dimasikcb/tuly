"""Совместимый запуск исходника приложения.

Основной файл проекта — ``tools.pyw`` (без консольного окна). Этот небольшой
мост позволяет запускать приложение и командой ``python tools.py``.
"""

from pathlib import Path
import runpy


runpy.run_path(str(Path(__file__).with_name("tools.pyw")), run_name="__main__")
