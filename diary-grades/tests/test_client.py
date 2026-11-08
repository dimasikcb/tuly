"""Офлайн-тесты ``diary_client``: unittest + unittest.mock, без браузера и сети.

Запуск из папки проекта:

    python -X utf8 -m unittest tests.test_client -v
    python -X utf8 -m unittest discover -s tests -v

Playwright нигде не запускается по-настоящему: ``sync_playwright`` подменяется
цепочкой моков (chromium.launch -> browser -> context -> page). Импорт
Playwright в ``diary_client`` ленивый, поэтому тест импорта без установленного
пакета тоже проходит.
"""
from __future__ import annotations

import importlib
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import diary_client
from models import DiaryError, ParseError, Period, SessionExpired, SubjectGrades

# Полностью контролируемые настройки/селекторы: тесты не зависят от содержимого
# selectors.json и settings.json, которые параллельно правят другие агенты.
SETTINGS = {
    "login_timeout_s": 300,
    "headless": True,
    "nav_timeout_s": 35,
    "save_debug": True,
}
SELECTORS = {
    "logged_in_markers": ["дневник", "оценки", "успеваемость"],
    "expired_url_hints": [
        "esia.gosuslugi.ru", "/login", "/auth", "passport", "oauth",
    ],
    "grades_nav_texts": ["оценки", "дневник"],
    "period_nav_texts": ["четверть", "триместр"],
    "json_url_keywords": [
        "grade", "mark", "diary", "journal", "score", "subject", "period",
    ],
}
PERIOD = Period("2 четверть", 2, ("2 четверть", "четверть 2"), "quarters")

# Payload, который гарантированно разбирается реальными парсерами.
GRADES_PAYLOAD = {
    "data": [
        {"subject": "Математика", "marks": [5, 4, 3]},
        {"subject": "Физика", "grades": [4, 5]},
    ]
}
GRADES_URL = "https://authedu.mosreg.ru/api/grades"
CLEAN_URL = "https://authedu.mosreg.ru/diary"      # без подсказок входа
LOGGED_IN_URL = "https://dnevnik.mosreg.ru/journal"  # явно «не ЕСИА»
ESIA_URL = "https://esia.gosuslugi.ru/idp/login"


def _client():
    return diary_client.DiaryClient(settings=dict(SETTINGS), selectors=dict(SELECTORS))


def _configure(sp):
    """Настраивает цепочку моков под ``patch('playwright.sync_api.sync_playwright')``.

    ``sync_playwright().start()`` -> ``pw``; ``pw.chromium.launch(...)`` -> browser.
    """
    pw = sp.return_value.start.return_value
    browser = pw.chromium.launch.return_value
    context = browser.new_context.return_value
    page = context.new_page.return_value
    page.url = CLEAN_URL
    page.content.return_value = "<html><body>Дневник оценок</body></html>"
    context.storage_state.return_value = {
        "cookies": [{"name": "sid", "value": "1"}],
        "origins": [],
    }
    return pw, browser, context, page


def _json_response(url, payload, content_type="application/json"):
    resp = MagicMock()
    resp.url = url
    resp.headers = {"content-type": content_type}
    resp.json.return_value = payload
    return resp


def _emit_responses(page, responses):
    """Заставляет ``page.goto`` дёрнуть зарегистрированный response-обработчик."""
    handlers = {}

    def on(event, handler, *args, **kwargs):
        handlers[event] = handler

    def goto(url, *args, **kwargs):
        handler = handlers.get("response")
        if handler is not None:
            for resp in responses:
                handler(resp)

    page.on.side_effect = on
    page.goto.side_effect = goto


class TestLogin(unittest.TestCase):
    """Первый запуск: видимое окно, сохранение сессии, таймаут."""

    def test_login_success_saves_session(self):
        with tempfile.TemporaryDirectory() as tmp:
            session_path = Path(tmp) / "storage_state.json"
            statuses = []
            with patch("playwright.sync_api.sync_playwright") as sp, \
                    patch("diary_client.SESSION_FILE", session_path):
                pw, browser, context, page = _configure(sp)
                page.url = LOGGED_IN_URL
                page.content.return_value = "<html>Дневник оценок</html>"

                result = _client().login(on_status=statuses.append, timeout_s=5)

            self.assertTrue(result)
            self.assertTrue(session_path.exists())
            saved = json.loads(session_path.read_text(encoding="utf-8"))
            self.assertEqual(
                saved,
                {"cookies": [{"name": "sid", "value": "1"}], "origins": []},
            )
            # headless=False: окно должно быть видимым для ручного входа.
            self.assertIs(pw.chromium.launch.call_args.kwargs.get("headless"), False)
            context.storage_state.assert_called_once()
            browser.close.assert_called_once()
            pw.stop.assert_called_once()
            self.assertTrue(statuses)

    def test_login_timeout_returns_false(self):
        with patch("playwright.sync_api.sync_playwright") as sp:
            pw, browser, context, page = _configure(sp)
            page.url = ESIA_URL
            page.content.return_value = "<html>Вход через Госуслуги</html>"

            result = _client().login(timeout_s=0)

            self.assertFalse(result)
            browser.close.assert_called_once()
            pw.stop.assert_called_once()


class TestFetch(unittest.TestCase):
    """Скрытая загрузка оценок и все ветки ошибок."""

    def test_fetch_headless_and_storage_state(self):
        with patch("config.session_exists", return_value=True), \
                patch("playwright.sync_api.sync_playwright") as sp:
            pw, browser, context, page = _configure(sp)
            page.url = CLEAN_URL
            _emit_responses(page, [_json_response(GRADES_URL, GRADES_PAYLOAD)])

            result = _client().fetch(period=PERIOD, on_status=lambda m: None)

            self.assertEqual(result.source, "live")
            self.assertTrue(result.subjects)
            self.assertIs(pw.chromium.launch.call_args.kwargs.get("headless"), True)
            ctx_kwargs = browser.new_context.call_args.kwargs
            self.assertEqual(
                ctx_kwargs.get("storage_state"), str(diary_client.SESSION_FILE)
            )
            self.assertEqual(ctx_kwargs.get("locale"), "ru-RU")
            # Кастомный UA ломает привязанную к браузеру сессию «Моей школы»:
            # портал перестаёт отдавать /api/family/web/v1/marks. Оставляем
            # дефолтный Playwright user agent.
            self.assertNotIn("user_agent", ctx_kwargs)
            self.assertIn("viewport", ctx_kwargs)
            browser.close.assert_called_once()
            pw.stop.assert_called_once()

    def test_fetch_without_session_raises(self):
        with patch("config.session_exists", return_value=False), \
                patch("playwright.sync_api.sync_playwright") as sp:
            with self.assertRaises(SessionExpired):
                _client().fetch(period=PERIOD)
            # Браузер даже не запускается: проверка сессии раньше старта PW.
            sp.assert_not_called()

    def test_fetch_esia_redirect_raises(self):
        with patch("config.session_exists", return_value=True), \
                patch("playwright.sync_api.sync_playwright") as sp:
            pw, browser, context, page = _configure(sp)
            page.url = ESIA_URL

            with self.assertRaises(SessionExpired):
                _client().fetch(period=PERIOD)

            browser.close.assert_called_once()
            pw.stop.assert_called_once()

    def test_fetch_parses_json_responses(self):
        with patch("config.session_exists", return_value=True), \
                patch("playwright.sync_api.sync_playwright") as sp:
            pw, browser, context, page = _configure(sp)
            page.url = CLEAN_URL
            _emit_responses(page, [_json_response(GRADES_URL, GRADES_PAYLOAD)])

            result = _client().fetch(period=PERIOD)

            self.assertEqual(result.source, "live")
            names = {s.name for s in result.subjects}
            self.assertIn("Математика", names)
            self.assertIn("Физика", names)
            self.assertEqual(result.period, "2 четверть")
            self.assertIn(GRADES_URL, result.urls)
            browser.close.assert_called_once()

    def test_fetch_empty_parse_writes_debug(self):
        with tempfile.TemporaryDirectory() as tmp:
            debug_dir = Path(tmp) / "debug"
            with patch("config.session_exists", return_value=True), \
                    patch("playwright.sync_api.sync_playwright") as sp, \
                    patch("diary_client.DEBUG_DIR", debug_dir):
                pw, browser, context, page = _configure(sp)
                page.url = CLEAN_URL
                page.content.return_value = "<html><body>пусто</body></html>"
                _emit_responses(page, [_json_response(GRADES_URL, {"ok": True})])

                with self.assertRaises(ParseError) as ctx:
                    _client().fetch(period=PERIOD)

                self.assertTrue((debug_dir / "page.html").exists())
                self.assertTrue((debug_dir / "urls.txt").exists())
                self.assertTrue((debug_dir / "responses" / "01.json").exists())
                self.assertIn("page.html", str(ctx.exception))
                browser.close.assert_called_once()
                pw.stop.assert_called_once()

    def test_browser_closed_on_exception(self):
        with patch("config.session_exists", return_value=True), \
                patch("playwright.sync_api.sync_playwright") as sp:
            pw, browser, context, page = _configure(sp)
            page.url = CLEAN_URL
            page.goto.side_effect = RuntimeError("boom")

            with self.assertRaises(DiaryError):
                _client().fetch(period=PERIOD)

            browser.close.assert_called_once()
            context.close.assert_called_once()
            pw.stop.assert_called_once()


class TestAllSubjects(unittest.TestCase):
    """Обновление по ВСЕМ предметам, а не только по первому на странице."""

    def test_merge_best_does_not_duplicate_marks(self):
        merged = diary_client._merge_best(
            [SubjectGrades("Физика", (4, 5))],
            [SubjectGrades("Физика", (4, 5)), SubjectGrades("Химия", (5,))],
            [SubjectGrades("Физика", (4, 5, 5, 4))],
        )
        by_name = {s.name: s.marks for s in merged}
        self.assertEqual(by_name["Физика"], (4, 5, 5, 4))
        self.assertEqual(by_name["Химия"], (5,))
        self.assertEqual(len(merged), 2)

    def test_complete_subjects_keeps_all_expected(self):
        completed = diary_client.complete_subjects(
            [SubjectGrades("Обществознание", (4, 4, 4))], PERIOD
        )
        names = {s.name.casefold() for s in completed}
        expected = {n.casefold() for n in config.expected_subjects()}
        self.assertTrue(expected.issubset(names))
        self.assertEqual(sum(1 for s in completed if s.marks), 1)
        self.assertTrue(all(s.source == "pending" for s in completed if not s.marks))

    def test_fetch_scans_every_subject(self):
        """Клик по каждому предмету приносит оценки — в кэш идут ВСЕ предметы."""
        handlers: dict = {}

        def on(event, handler, *args, **kwargs):
            handlers[event] = handler

        def goto(url, *args, **kwargs):
            if "response" in handlers:
                handlers["response"](_json_response(GRADES_URL, GRADES_PAYLOAD))

        def get_by_text(text, exact=False):
            locator = MagicMock()

            def click(*args, **kwargs):
                if "response" in handlers:
                    handlers["response"](_json_response(
                        f"https://authedu.mosreg.ru/api/grades/{text}",
                        {"data": [{"subject": text, "marks": [5, 4]}]},
                    ))

            locator.first.click.side_effect = click
            return locator

        with patch("config.session_exists", return_value=True), \
                patch("playwright.sync_api.sync_playwright") as sp:
            pw, browser, context, page = _configure(sp)
            page.url = CLEAN_URL
            page.on.side_effect = on
            page.goto.side_effect = goto
            page.get_by_text.side_effect = get_by_text
            page.content.return_value = "<html><body>Дневник оценок</body></html>"

            result = _client().fetch(period=PERIOD)

        expected = len(config.expected_subjects())
        self.assertGreater(expected, 1)
        self.assertEqual(result.source, "live")
        # Раньше сверх списка был +1: «Математика» приходила из демо-ответа,
        # но отсутствовала в шаблоне. Теперь она есть в профиле портала и
        # входит в expected — лишней строки больше нет.
        self.assertEqual(len(result.subjects), expected)
        self.assertEqual(sum(1 for s in result.subjects if s.marks), expected)
        self.assertIn("с оценками — %d" % expected, result.note)
        browser.close.assert_called_once()
        pw.stop.assert_called_once()


class TestSessionHelpers(unittest.TestCase):
    def test_has_session_delegates_to_config(self):
        with patch("config.session_exists", return_value=True):
            self.assertTrue(_client().has_session())
        with patch("config.session_exists", return_value=False):
            self.assertFalse(_client().has_session())

    def test_clear_session_removes_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text("{}", encoding="utf-8")
            with patch("diary_client.SESSION_FILE", path):
                _client().clear_session()
            self.assertFalse(path.exists())

    def test_clear_session_missing_is_safe(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "missing.json"
            with patch("diary_client.SESSION_FILE", path):
                _client().clear_session()  # не бросает


class TestModuleFunctions(unittest.TestCase):
    """Тонкие обёртки, которые дергает GUI из фонового потока."""

    def test_fetch_grades_wrapper(self):
        with patch("config.session_exists", return_value=True), \
                patch("playwright.sync_api.sync_playwright") as sp:
            pw, browser, context, page = _configure(sp)
            page.url = CLEAN_URL
            _emit_responses(page, [_json_response(GRADES_URL, GRADES_PAYLOAD)])

            result = diary_client.fetch_grades(period=PERIOD)

            self.assertEqual(result.source, "live")
            browser.close.assert_called_once()
            pw.stop.assert_called_once()

    def test_interactive_login_wrapper(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            with patch("playwright.sync_api.sync_playwright") as sp, \
                    patch("diary_client.SESSION_FILE", path):
                pw, browser, context, page = _configure(sp)
                page.url = LOGGED_IN_URL
                page.content.return_value = "<html>Дневник оценок</html>"

                self.assertTrue(diary_client.interactive_login(timeout_s=5))
                self.assertTrue(path.exists())


class TestZImportWithoutPlaywright(unittest.TestCase):
    """Должен идти последним (имя класса с Z): reload меняет объект модуля."""

    def test_import_works_when_playwright_unavailable(self):
        with patch.dict(
            sys.modules, {"playwright": None, "playwright.sync_api": None}
        ):
            reloaded = importlib.reload(diary_client)
        self.assertTrue(hasattr(reloaded, "DiaryClient"))
        self.assertTrue(callable(reloaded.fetch_grades))
        self.assertTrue(callable(reloaded.interactive_login))


if __name__ == "__main__":
    unittest.main(verbosity=2)
