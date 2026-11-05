"""Тесты изоляции сетевого трафика (Task 8 & 9):
Проверка отключения ProxyEnable и управления TUN в режиме «Только программы».
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
ROOT_DIR = TESTS_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from helpers import load_module

app = load_module("bypass_core")


class TrafficIsolationTest(unittest.TestCase):
    def test_system_proxy_toggle_callable(self) -> None:
        self.assertTrue(hasattr(app, "set_windows_system_proxy"))
        with mock.patch("ctypes.windll.wininet.InternetSetOptionW", create=True):
            result = app.set_windows_system_proxy(False)
            self.assertIsInstance(result, bool)

    def test_happ_tun_helpers_exist(self) -> None:
        self.assertTrue(callable(getattr(app, "happ_tun_active", None)))
        self.assertTrue(callable(getattr(app, "happ_tun_disable", None)))
        self.assertTrue(callable(getattr(app, "happ_tun_enable", None)))

    def test_apps_mode_disables_windows_system_proxy(self) -> None:
        """В режиме «Только программы» системный прокси Windows должен отключаться."""
        calls = []
        with mock.patch.object(app, "set_windows_system_proxy", lambda en, *a: calls.append(f"proxy:{en}")), \
             mock.patch.object(app, "happ_tun_disable", lambda: calls.append("tun_disable")), \
             mock.patch.object(app, "bypass_cfg", lambda: {"mode": "happ", "scope": "apps", "apps": ["chrome.exe"], "happ_path": r"C:\Happ\Happ.exe", "socks_port": 10808}), \
             mock.patch.object(app, "bypass_cfg_save", lambda **kw: None), \
             mock.patch.object(app, "happ_connected", lambda *a, **k: True), \
             mock.patch.object(app, "happ_mode_read", lambda: {"tun": False, "system_proxy": False}), \
             mock.patch.object(app, "happ_mode_write", lambda **kw: None), \
             mock.patch.object(app, "bypass_happ_start", lambda: {"ok": True}), \
             mock.patch.object(app, "happ_socks_port", lambda: 10808), \
             mock.patch.object(app, "port_open", lambda *a, **k: True), \
             mock.patch.object(app, "proxifyre_start", lambda *a, **k: {"ok": True}):
            res = app.bypass_apply("apps", "happ")
            self.assertTrue(res["ok"])
            self.assertIn("proxy:False", calls)
            self.assertIn("tun_disable", calls)


if __name__ == "__main__":
    unittest.main()
