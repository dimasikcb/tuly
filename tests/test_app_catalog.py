"""Каталог программ: слияние источников, дедупликация, отсев мусора."""
from __future__ import annotations

import unittest

from helpers import load_app

app = load_app()


class AppCatalogTest(unittest.TestCase):
    def catalog(self, **kwargs):
        return app.app_catalog(
            running_paths=kwargs.get("running", []),
            registry_paths=kwargs.get("registry", []),
            shortcuts=kwargs.get("shortcuts", []),
        )

    def test_running_wins_over_registry(self) -> None:
        items = self.catalog(
            running=[r"C:\Games\Steam\steam.exe"],
            registry=[r"C:\Program Files\Steam\steam.exe"],
            shortcuts=[("Steam", r"C:\Games\Steam\steam.exe")],
        )
        self.assertEqual([item.exe for item in items], ["steam.exe"])
        steam = items[0]
        self.assertTrue(steam.running)
        self.assertEqual(steam.path, r"C:\Games\Steam\steam.exe")

    def test_shortcut_name_beats_generated_name(self) -> None:
        items = self.catalog(
            registry=[r"C:\Program Files\Google\Chrome\Application\chrome.exe"],
            shortcuts=[("Google Chrome",
                        r"C:\Program Files\Google\Chrome\Application\chrome.exe")],
        )
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].name, "Google Chrome")
        self.assertEqual(items[0].source, "startmenu")

    def test_running_name_not_overwritten_by_shortcut(self) -> None:
        items = self.catalog(
            running=[r"C:\Program Files\Google\Chrome\Application\chrome.exe"],
            shortcuts=[("Google Chrome",
                        r"C:\Program Files\Google\Chrome\Application\chrome.exe")],
        )
        self.assertEqual(items[0].name, "Chrome")
        self.assertTrue(items[0].running)

    def test_system_and_installer_exes_skipped(self) -> None:
        items = self.catalog(
            running=[r"C:\Windows\System32\cmd.exe", r"C:\Windows\explorer.exe"],
            registry=[r"C:\Program Files\App\unins000.exe",
                      r"C:\Program Files\App\Setup.exe",
                      r"C:\Program Files\App\app.exe"],
        )
        self.assertEqual([item.exe for item in items], ["app.exe"])

    def test_non_exe_entries_skipped(self) -> None:
        items = self.catalog(
            running=[r"C:\Games\launcher.bat", ""],
            registry=["not-a-path"],
            shortcuts=[("Bad", r"C:\Games\readme.txt")],
        )
        self.assertEqual(items, [])

    def test_running_first_then_alphabetical(self) -> None:
        items = self.catalog(
            running=[r"C:\z\zebra.exe"],
            registry=[r"C:\a\Alpha.exe", r"C:\b\beta.exe"],
        )
        self.assertEqual([item.exe for item in items],
                         ["zebra.exe", "Alpha.exe", "beta.exe"])

    def test_duplicates_inside_one_source_collapse(self) -> None:
        items = self.catalog(
            running=[r"C:\a\app.exe", r"C:\b\app.exe", r"C:\c\app.exe"],
        )
        self.assertEqual(len(items), 1)
        self.assertTrue(items[0].running)

    def test_empty_sources(self) -> None:
        self.assertEqual(self.catalog(), [])


if __name__ == "__main__":
    unittest.main()
