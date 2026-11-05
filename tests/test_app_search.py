"""Поиск по программам: токены, регистр, кириллица, ранжирование."""
from __future__ import annotations

import unittest

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
ROOT_DIR = TESTS_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from helpers import load_module

app = load_module("apps_catalog")

CATALOG = [
    app.AppEntry(exe="chrome.exe", name="Google Chrome",
                 path=r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                 running=True),
    app.AppEntry(exe="discord.exe", name="Discord", path=r"C:\Discord\discord.exe"),
    app.AppEntry(exe="telegram.exe", name="Telegram", path=r"C:\Telegram\telegram.exe"),
    app.AppEntry(exe="yandexmusic.exe", name="Яндекс Музыка",
                 path=r"C:\Yandex\Music\yandexmusic.exe"),
]


class AppSearchTest(unittest.TestCase):
    def names(self, query: str, **kwargs):
        return [entry.name for entry in app.filter_apps(CATALOG, query, **kwargs)]

    def test_empty_query_returns_everything_running_first(self) -> None:
        self.assertEqual(self.names(""),
                         ["Google Chrome", "Discord", "Telegram", "Яндекс Музыка"])

    def test_substring(self) -> None:
        self.assertEqual(self.names("tele"), ["Telegram"])

    def test_case_insensitive(self) -> None:
        self.assertEqual(self.names("CHROME"), ["Google Chrome"])

    def test_search_by_exe_name(self) -> None:
        self.assertEqual(self.names("yandexmusic"), ["Яндекс Музыка"])

    def test_search_by_path(self) -> None:
        self.assertEqual(self.names(r"program files"), ["Google Chrome"])

    def test_two_tokens_must_both_match(self) -> None:
        self.assertEqual(self.names("google chrome"), ["Google Chrome"])
        self.assertEqual(self.names("google telegram"), [])

    def test_cyrillic(self) -> None:
        self.assertEqual(self.names("музыка"), ["Яндекс Музыка"])
        self.assertEqual(self.names("ЯНДЕКС"), ["Яндекс Музыка"])

    def test_prefix_ranks_above_substring(self) -> None:
        catalog = [
            app.AppEntry(exe="mytelegram.exe", name="MyTelegram"),
            app.AppEntry(exe="telegram.exe", name="Telegram"),
        ]
        self.assertEqual([item.name for item in app.filter_apps(catalog, "telegram")],
                         ["Telegram", "MyTelegram"])

    def test_no_matches(self) -> None:
        self.assertEqual(self.names("нет такой программы"), [])

    def test_only_running_filter(self) -> None:
        self.assertEqual(self.names("", only_running=True), ["Google Chrome"])

    def test_limit(self) -> None:
        self.assertEqual(len(app.filter_apps(CATALOG, "", limit=2)), 2)

    def test_score_zero_for_missing_token(self) -> None:
        entry = CATALOG[1]
        self.assertEqual(app.app_score(entry, "discord"), 5)
        self.assertEqual(app.app_score(entry, "zzz"), 0)
        self.assertEqual(app.app_score(entry, ""), 1)


if __name__ == "__main__":
    unittest.main()


class RunningProcessesTest(unittest.TestCase):
    """Регрессия: EnumProcesses вызывался из kernel32 и молча падал."""

    def test_running_paths_detected(self) -> None:
        import sys
        if sys.platform != "win32":
            self.skipTest("только Windows")
        paths = app._running_exe_paths()
        self.assertTrue(paths, "список запущенных процессов пуст")
        self.assertTrue(all(path.lower().endswith(".exe") for path in paths))
