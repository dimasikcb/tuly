"""Общие помощники для тестов: загрузка модулей проекта."""
from __future__ import annotations

import importlib
import importlib.machinery
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_FILE = ROOT / "tools.pyw"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_module(name: str):
    """Импортирует модуль логики (winbase, apps_catalog, bypass_core, ...).

    GUI (tools.pyw) тянет tkinter, поэтому тесты логики грузят слой, в котором
    живёт проверяемая функция, — без графической библиотеки.
    """
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    return importlib.import_module(name)


def load_app():
    """Импортирует tools.pyw целиком. Нужен только тестам самого GUI."""
    if "tools" in sys.modules:
        return sys.modules["tools"]
    loader = importlib.machinery.SourceFileLoader("tools", str(APP_FILE))
    spec = importlib.util.spec_from_loader("tools", loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules["tools"] = module
    loader.exec_module(module)
    return module


class TempConfig:
    """Подменяет CONFIG_PATH на временный файл.

    Настройки читает `winbase`, поэтому путь подменяем именно там — иначе
    тест писал бы в настоящий config.json рядом с приложением.
    """

    def __init__(self, app, testcase):
        import json
        import tempfile

        self.app = app
        self.base = load_module("winbase")
        self._json = json
        self._tmp = tempfile.TemporaryDirectory()
        testcase.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "config.json"
        self._original = self.base.CONFIG_PATH
        self.base.CONFIG_PATH = self.path
        testcase.addCleanup(self.restore)

    def restore(self) -> None:
        self.base.CONFIG_PATH = self._original

    def write(self, data: dict) -> None:
        self.path.write_text(self._json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def read(self) -> dict:
        return self._json.loads(self.path.read_text(encoding="utf-8"))
