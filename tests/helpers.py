"""Общие помощники для тестов: загрузка tools.pyw как модуля."""
from __future__ import annotations

import importlib.machinery
import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_FILE = ROOT / "tools.pyw"


def load_app():
    """Импортирует tools.pyw (расширение .pyw, поэтому через SourceFileLoader)."""
    if "tools" in sys.modules:
        return sys.modules["tools"]
    loader = importlib.machinery.SourceFileLoader("tools", str(APP_FILE))
    spec = importlib.util.spec_from_loader("tools", loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules["tools"] = module
    loader.exec_module(module)
    return module


class TempConfig:
    """Подменяет CONFIG_PATH модуля на временный файл."""

    def __init__(self, app, testcase):
        import json
        import tempfile

        self.app = app
        self._json = json
        self._tmp = tempfile.TemporaryDirectory()
        testcase.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "config.json"
        self._original = app.CONFIG_PATH
        app.CONFIG_PATH = self.path
        testcase.addCleanup(self.restore)

    def restore(self) -> None:
        self.app.CONFIG_PATH = self._original

    def write(self, data: dict) -> None:
        self.path.write_text(self._json.dumps(data, ensure_ascii=False), encoding="utf-8")

    def read(self) -> dict:
        return self._json.loads(self.path.read_text(encoding="utf-8"))
