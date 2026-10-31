"""Смоук панели Bypass: поиск, выбор программ, переключатели (Tk без mainloop)."""
from __future__ import annotations

import tkinter as tk
import unittest
from unittest import mock

from helpers import TempConfig, load_app

app = load_app()

CATALOG = [
    app.AppEntry(exe="Discord.exe", name="Discord", path=r"C:\Discord\Discord.exe",
                 running=True),
    app.AppEntry(exe="chrome.exe", name="Google Chrome",
                 path=r"C:\Chrome\chrome.exe"),
    app.AppEntry(exe="telegram.exe", name="Telegram",
                 path=r"C:\Telegram\telegram.exe"),
]

STATE = {
    "admin": True,
    "happ": {"on": True, "connected": True, "installed": True, "tun": False,
             "socks_port": 10808, "socks_ok": True, "pids": [4242],
             "mode": {"tun": False, "system_proxy": False}},
    "gbd": {"on": False, "installed": True, "mine": False},
    "proxyfyre": {"installed": True, "driver": True, "service": 4, "running": True,
                  "apps": ["Discord.exe"]},
    "scope": "apps", "apps": ["Discord.exe"], "mode": "happ",
}


class FakeHub:
    def __init__(self) -> None:
        self.status: list[str] = []
        self.toasts: list[str] = []

    def run_bg(self, work, on_done, on_error) -> None:
        try:
            result = work()
        except Exception as exc:  # noqa: BLE001
            on_error(exc)
        else:
            on_done(result)

    def set_status(self, text: str) -> None:
        self.status.append(text)

    def toast(self, message: str, **_kwargs) -> None:
        self.toasts.append(message)

    def post(self, callback) -> None:
        callback()


class BypassPanelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = TempConfig(app, self)
        self.cfg.write({"bypass": {"mode": "happ", "scope": "apps",
                                   "apps": ["Discord.exe"]}})
        self.saved: dict = {}
        self.applied: list[tuple] = []
        self.stopped: list[str] = []

        def fake_apply(scope=None, mode=None):
            self.applied.append((scope, mode))
            return {"ok": True, "detail": f"apply {scope} {mode}"}

        def fake_save(**kwargs):
            self.saved.update(kwargs)

        self.patches = [
            mock.patch.object(app, "bypass_status", lambda *a, **k: dict(STATE)),
            mock.patch.object(app, "app_catalog", lambda *a, **k: list(CATALOG)),
            mock.patch.object(app, "bypass_apply", fake_apply),
            mock.patch.object(app, "bypass_stop",
                              lambda key: self.stopped.append(key) or {"detail": key}),
            mock.patch.object(app, "bypass_cfg_save", fake_save),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

        self.root = tk.Tk()
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        self.hub = FakeHub()
        self.panel = app.BypassPanel(self.root, self.hub)

    def _packed(self) -> list[str]:
        return [row.entry.exe for row in self.panel.rows.values()
                if row.winfo_manager() == "pack"]

    def test_state_loaded(self) -> None:
        self.assertEqual(self.panel.mode, "happ")
        self.assertEqual(self.panel.scope, "apps")
        self.assertEqual(self.panel.selected_apps, {"discord.exe"})
        self.assertEqual(len(self.panel.catalog), 3)
        self.assertEqual(self.panel.count_var.get(), "выбрано 1 из 3")

    def test_apps_section_visible_for_apps_scope(self) -> None:
        self.assertEqual(self.panel.apps_card.winfo_manager(), "grid")

    def test_search_filters_rows(self) -> None:
        self.panel.search_var.set("tele")
        self.assertEqual(self._packed(), ["telegram.exe"])

    def test_search_cleared_shows_all(self) -> None:
        self.panel.search_var.set("tele")
        self.panel.search_var.set("")
        self.assertEqual(sorted(self._packed()),
                         ["Discord.exe", "chrome.exe", "telegram.exe"])

    def test_search_without_matches(self) -> None:
        self.panel.search_var.set("zzz")
        self.assertEqual(self._packed(), [])
        self.assertIn("Ничего не нашлось", self.panel.empty_label.cget("text"))

    def test_toggle_app_off_saves_empty_list(self) -> None:
        entry = next(item for item in CATALOG if item.exe == "Discord.exe")
        self.panel._on_app_toggle(entry, False)
        self.assertEqual(self.panel.selected_apps, set())
        self.assertEqual(self.saved["apps"], [])

    def test_toggle_app_on_saves_exe(self) -> None:
        entry = next(item for item in CATALOG if item.exe == "chrome.exe")
        self.panel._on_app_toggle(entry, True)
        self.assertIn("chrome.exe", self.saved["apps"])

    def test_clear_apps(self) -> None:
        self.panel._clear_apps()
        self.assertEqual(self.panel.selected_apps, set())
        self.assertEqual(self.saved["apps"], [])

    def test_engine_text_shows_socks_in_apps_mode(self) -> None:
        text = self.panel._engine_text("happ")
        self.assertIn("VPN включён", text)
        self.assertIn("SOCKS 10808", text)

    def test_status_text_for_apps_mode(self) -> None:
        headline, detail, _color = self.panel._status_text()
        self.assertIn("по программам", headline)
        self.assertIn("127.0.0.1:10808", detail)

    def test_switch_on_starts_engine(self) -> None:
        self.panel._on_switch("happ", True)
        self.assertIn(("apps", "happ"), self.applied)
        self.assertFalse(self.panel.busy)

    def test_switch_off_stops_engine(self) -> None:
        self.panel._on_switch("happ", False)
        self.assertEqual(self.stopped, ["happ"])

    def test_switch_on_without_apps_does_not_start(self) -> None:
        self.panel.selected_apps.clear()
        self.panel._on_switch("happ", True)
        self.assertEqual(self.applied, [])
        self.assertTrue(any("Отметьте программы" in text for text in self.hub.toasts))

    def test_gbd_switch_forces_global_scope(self) -> None:
        self.panel._on_switch("gbd", True)
        self.assertEqual(self.panel.scope, "all")
        self.assertIn(("all", "gbd"), self.applied)

    def test_scope_switch_reapplies_when_running(self) -> None:
        self.panel._set_scope("all")
        self.assertEqual(self.panel.scope, "all")
        self.assertIn(("all", "happ"), self.applied)

    def test_scope_control_disabled_for_gbd(self) -> None:
        self.panel.mode = "gbd"
        self.panel._update_controls()
        self.assertFalse(self.panel.scope_control.enabled)

    def test_apps_section_hidden_for_global_scope(self) -> None:
        self.panel._set_scope("all")
        self.assertEqual(self.panel.apps_card.winfo_manager(), "")


if __name__ == "__main__":
    unittest.main()
