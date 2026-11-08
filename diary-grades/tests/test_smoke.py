"""Офлайн-смоук-тесты сборки проекта: импорты, файлы конфигурации, запись настроек.

Запуск из папки проекта:

    python -X utf8 -m unittest tests.test_smoke -v
    python -X utf8 -m unittest discover -s tests -v

НИКАКОЙ сети, НИКАКОГО запуска браузера и НИКАКОГО открытия GUI-окна:
модули только импортируются/компилируются, Playwright в ``diary_client``
импортируется лениво (внутри методов), а ``app.py`` проверяется через
``compile()``, но НЕ импортируется.
"""
from __future__ import annotations

import inspect
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

ROOT = Path(__file__).resolve().parents[1]

import config
import demo_data
import diary_client
import grade_math
import models
import parsers


class TestImports(unittest.TestCase):
    """Все нижние слои импортируются без Playwright/браузера/GUI."""

    def test_all_modules_import_cleanly(self):
        modules = {
            "models": models,
            "config": config,
            "grade_math": grade_math,
            "demo_data": demo_data,
            "parsers": parsers,
            "diary_client": diary_client,
        }
        for name, module in modules.items():
            with self.subTest(module=name):
                self.assertIsInstance(module, ModuleType)
                self.assertTrue(module.__file__)

        # Ключевые публичные объекты на месте.
        for attr in ("SubjectGrades", "SubjectReport", "FetchResult", "Period",
                     "DiaryError", "ParseError", "SessionExpired"):
            self.assertTrue(hasattr(models, attr), f"models.{attr}")
        for attr in ("DEFAULTS", "current_period", "atomic_write_json",
                     "load_settings", "save_settings", "load_selectors"):
            self.assertTrue(hasattr(config, attr), f"config.{attr}")
        for attr in ("is_on_five", "fives_needed", "safe_streak", "badge_for",
                     "build_report", "sort_reports", "validate_marks"):
            self.assertTrue(hasattr(grade_math, attr), f"grade_math.{attr}")
        self.assertTrue(callable(demo_data.demo_result))
        for attr in ("parse_grades_payload", "parse_grades_html", "parse_file",
                     "merge_subjects", "validate_subjects"):
            self.assertTrue(hasattr(parsers, attr), f"parsers.{attr}")
        for attr in ("DiaryClient", "fetch_grades", "interactive_login"):
            self.assertTrue(hasattr(diary_client, attr), f"diary_client.{attr}")

    def test_no_browser_is_launched_by_import(self):
        # Полностью изолированный клиент: без чтения settings.json/selectors.json.
        client = diary_client.DiaryClient(settings={}, selectors={})
        self.assertIsNone(client._pw)
        self.assertIsNone(client._browser)
        self.assertIsNone(client._context)
        self.assertIsNone(client._page)
        # На уровне модуля браузерная автоматизация не привязана ни к чему.
        self.assertNotIn("sync_playwright", vars(diary_client))
        self.assertNotIn("tkinter", vars(diary_client))
        self.assertNotIn("customtkinter", vars(diary_client))

    def test_playwright_import_is_lazy(self):
        """`from playwright.sync_api import ...` спрятан ВНУТРИ функции."""
        source = inspect.getsource(diary_client)
        lazy_lines = [
            line for line in source.splitlines()
            if "from playwright.sync_api import" in line
        ]
        self.assertTrue(lazy_lines, "должен существовать ленивый импорт Playwright")
        for line in lazy_lines:
            self.assertTrue(
                line[:1].isspace(),
                f"Playwright импортируется на уровне модуля (откроет браузер): {line!r}",
            )
        self.assertTrue(callable(diary_client._start_playwright))

    def test_lower_layers_do_not_bind_gui_or_browser(self):
        for module in (models, config, grade_math, demo_data, parsers):
            with self.subTest(module=module.__name__):
                for forbidden in ("playwright", "sync_playwright", "tkinter",
                                  "customtkinter", "PIL", "Image"):
                    self.assertNotIn(forbidden, vars(module))


class TestProjectFiles(unittest.TestCase):
    """Статическая проверка файлов сборки (только чтение)."""

    def test_selectors_json_is_valid_and_complete(self):
        path = ROOT / "selectors.json"
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)                      # валидный JSON
        self.assertIsInstance(data, dict)

        required = (
            "start_url", "logged_in_markers", "expired_url_hints",
            "grades_nav_texts", "period_nav_texts", "json_url_keywords",
            "json_subject_keys", "json_marks_keys", "json_mark_keys",
            "subject_row_selectors", "mark_selectors", "skip_subject_words",
        )
        for key in required:
            with self.subTest(key=key):
                self.assertIn(key, data)
        self.assertIsInstance(data["start_url"], str)
        for key in required[1:]:
            self.assertIsInstance(data[key], list, key)
            self.assertTrue(data[key], key)
        # config.load_selectors() читает тот же самый файл.
        self.assertEqual(config.load_selectors(), data)

    def test_requirements_txt(self):
        text = (ROOT / "requirements.txt").read_text(encoding="utf-8").casefold()
        for token in ("customtkinter", "playwright", "pillow",
                      "pip install", "playwright install chromium"):
            with self.subTest(token=token):
                self.assertIn(token, text)

    def test_run_bat(self):
        text = (ROOT / "run.bat").read_text(encoding="utf-8").casefold()
        self.assertIn("pythonw.exe", text)
        self.assertIn("playwright install chromium", text)

    def test_run_sh(self):
        text = (ROOT / "run.sh").read_text(encoding="utf-8")
        self.assertTrue(text.startswith("#!"), "нет shebang")
        self.assertIn("exec", text)
        self.assertIn(".venv/bin/python", text)
        self.assertIn("exec .venv/bin/python", text)

    def test_desktop_entry(self):
        text = (ROOT / "diary.desktop").read_text(encoding="utf-8")
        self.assertIn("Terminal=false", text)
        self.assertIn("Exec=", text)
        self.assertIn("Type=Application", text)

    def test_app_pyw_exists(self):
        self.assertTrue((ROOT / "app.pyw").is_file())


class TestConfigIO(unittest.TestCase):
    """Запись настроек только во временную папку через patch путей."""

    def test_atomic_write_json_writes_and_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "nested" / "settings.json"
            config.atomic_write_json(path, {"a": 1, "name": "Оценки"})
            self.assertTrue(path.is_file())
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"a": 1, "name": "Оценки"},
            )
            # ensure_ascii=False: кириллица сохраняется как есть.
            self.assertIn("Оценки", path.read_text(encoding="utf-8"))

            # Повторная запись перезаписывает, а не дописывает.
            config.atomic_write_json(path, {"a": 2, "b": [1, 2, 3]})
            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {"a": 2, "b": [1, 2, 3]},
            )
            # Временных .tmp-файлов после os.replace не остаётся.
            self.assertEqual(list(Path(tmp).rglob("*.tmp")), [])

    def test_load_and_save_settings_preserve_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            settings_file = tmp_path / "settings.json"
            with patch.object(config, "SETTINGS_FILE", settings_file), \
                    patch.object(config, "DATA_DIR", tmp_path / "data"), \
                    patch.object(config, "DEBUG_DIR", tmp_path / "debug"):
                snapshot = dict(config.DEFAULTS)

                # Нет файла -> чистые DEFAULTS.
                self.assertEqual(config.load_settings(), config.DEFAULTS)

                merged = config.save_settings({"headless": False})
                self.assertIs(merged["headless"], False)
                self.assertEqual(merged["login_timeout_s"],
                                 snapshot["login_timeout_s"])

                # DEFAULTS не mutated ни прямо, ни через shallow-copy.
                self.assertEqual(config.DEFAULTS, snapshot)
                self.assertIs(config.DEFAULTS["headless"], True)

                # Настройка реально сохранилась и читается обратно.
                loaded = config.load_settings()
                self.assertIs(loaded["headless"], False)
                for key, value in snapshot.items():
                    if key != "headless":
                        self.assertEqual(loaded[key], value, key)

                # Произвольный ключ мержится, но не попадает в DEFAULTS.
                config.save_settings({"custom_key": 42})
                self.assertEqual(config.load_settings()["custom_key"], 42)
                self.assertNotIn("custom_key", config.DEFAULTS)


class TestGuiSyntax(unittest.TestCase):
    """Проверка синтаксиса GUI через compile() — НЕ импорт (не открываем окно)."""

    @unittest.skipUnless(
        (ROOT / "app.py").exists(),
        "app.py ещё пишет главный агент — проверка синтаксиса пропущена",
    )
    def test_app_py_compiles(self):
        path = ROOT / "app.py"
        source = path.read_text(encoding="utf-8")
        compile(source, str(path), "exec")  # только синтаксис, без выполнения

    def test_app_pyw_compiles(self):
        path = ROOT / "app.pyw"
        self.assertTrue(path.is_file())
        source = path.read_text(encoding="utf-8")
        compile(source, str(path), "exec")  # `from app import main` не выполнится


if __name__ == "__main__":
    unittest.main(verbosity=2)
