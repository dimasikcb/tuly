"""Конфиг обхода: дефолты, миграция старого формата, нормализация списка программ."""
from __future__ import annotations

import unittest

from helpers import TempConfig, load_app

app = load_app()


class BypassConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = TempConfig(app, self)

    def test_defaults(self) -> None:
        cfg = app.bypass_cfg()
        self.assertEqual(cfg["mode"], "happ")
        self.assertEqual(cfg["scope"], "all")
        self.assertEqual(cfg["apps"], [])
        self.assertEqual(cfg["gbd_args"], "-5")
        self.assertEqual(cfg["socks_port"], app.HAPP_SOCKS_DEFAULT)
        self.assertTrue(cfg["happ_path"])

    def test_old_config_migrates_to_global_scope(self) -> None:
        self.cfg.write({"bypass": {"mode": "gbd", "gbd_args": "-9",
                                   "happ_path": "C:/x/Happ.exe"}})
        cfg = app.bypass_cfg()
        self.assertEqual(cfg["mode"], "gbd")
        self.assertEqual(cfg["scope"], "all")
        self.assertEqual(cfg["apps"], [])
        self.assertEqual(cfg["gbd_args"], "-9")
        self.assertEqual(cfg["happ_path"], "C:/x/Happ.exe")

    def test_unknown_scope_falls_back_to_all(self) -> None:
        self.cfg.write({"bypass": {"scope": "everything"}})
        self.assertEqual(app.bypass_cfg()["scope"], "all")

    def test_unknown_mode_falls_back_to_happ(self) -> None:
        self.cfg.write({"bypass": {"mode": "openvpn"}})
        self.assertEqual(app.bypass_cfg()["mode"], "happ")

    def test_apps_normalized_from_paths(self) -> None:
        self.cfg.write({"bypass": {"apps": [
            r"C:\Program Files\Discord\Discord.exe",
            "discord.EXE",
            "chrome.exe",
            "",
            "notes.txt",
        ]}})
        self.assertEqual(app.bypass_cfg()["apps"], ["Discord.exe", "chrome.exe"])

    def test_broken_apps_value_ignored(self) -> None:
        self.cfg.write({"bypass": {"apps": "chrome.exe"}})
        self.assertEqual(app.bypass_cfg()["apps"], [])

    def test_bad_socks_port(self) -> None:
        for value in ("abc", 0, 70000, None):
            with self.subTest(value=value):
                self.cfg.write({"bypass": {"socks_port": value}})
                self.assertEqual(app.bypass_cfg()["socks_port"], app.HAPP_SOCKS_DEFAULT)

    def test_custom_socks_port_kept(self) -> None:
        self.cfg.write({"bypass": {"socks_port": 2080}})
        self.assertEqual(app.bypass_cfg()["socks_port"], 2080)

    def test_save_keeps_other_bypass_fields(self) -> None:
        app.bypass_cfg_save(mode="gbd", scope="all")
        app.bypass_cfg_save(scope="apps", apps=["chrome.exe"])
        cfg = app.bypass_cfg()
        self.assertEqual(cfg["mode"], "gbd")
        self.assertEqual(cfg["scope"], "apps")
        self.assertEqual(cfg["apps"], ["chrome.exe"])
        self.assertEqual(cfg["gbd_args"], "-5")

    def test_save_does_not_touch_other_sections(self) -> None:
        self.cfg.write({"settings": {"interval": "5"}, "steps": [{"kind": "point"}]})
        app.bypass_cfg_save(scope="apps")
        data = self.cfg.read()
        self.assertEqual(data["settings"]["interval"], "5")
        self.assertEqual(data["steps"], [{"kind": "point"}])
        self.assertEqual(data["bypass"]["scope"], "apps")


class NormalizeTest(unittest.TestCase):
    def test_normalize_app(self) -> None:
        self.assertEqual(app.normalize_app(r"C:\Games\Steam\steam.exe"), "steam.exe")
        self.assertEqual(app.normalize_app("  chrome.exe  "), "chrome.exe")
        self.assertEqual(app.normalize_app('"C:/Program Files/App/app.exe"'), "app.exe")
        self.assertEqual(app.normalize_app("readme.txt"), "")
        self.assertEqual(app.normalize_app(""), "")
        self.assertEqual(app.normalize_app(None), "")

    def test_normalize_apps_dedupes_case_insensitive(self) -> None:
        result = app.normalize_apps(["Chrome.exe", "chrome.exe", "CHROME.EXE",
                                     r"C:\x\Firefox.exe"])
        self.assertEqual(result, ["Chrome.exe", "Firefox.exe"])

    def test_pretty_app_name(self) -> None:
        self.assertEqual(app.pretty_app_name("discord-desktop.exe"), "Discord Desktop")
        self.assertEqual(app.pretty_app_name("chrome.exe"), "Chrome")
        self.assertEqual(app.pretty_app_name("Code_x64.exe"), "Code")
        self.assertEqual(app.pretty_app_name("telegram.exe"), "Telegram")


if __name__ == "__main__":
    unittest.main()
