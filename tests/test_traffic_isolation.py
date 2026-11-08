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
             mock.patch.object(app, "happ_tun_active", lambda: False), \
             mock.patch.object(app, "happ_tun_enable", lambda: True), \
             mock.patch.object(app, "bypass_happ_start", lambda: {"ok": True}), \
             mock.patch.object(app, "happ_socks_port", lambda: 10808), \
             mock.patch.object(app, "port_open", lambda *a, **k: True), \
             mock.patch.object(app, "proxifyre_start", lambda *a, **k: {"ok": True}):
            res = app.bypass_apply("apps", "happ")
            self.assertTrue(res["ok"])
            self.assertIn("proxy:False", calls)
            self.assertIn("tun_disable", calls)


    def test_leak_diagnosis_no_false_positive_when_happ_connected(self) -> None:
        with mock.patch.object(app, "happ_connected", lambda: True), \
             mock.patch.object(app, "_image_pids", lambda name: [1234]), \
             mock.patch.object(app, "happ_tun_active", lambda: False), \
             mock.patch.object(app, "happ_system_proxy", lambda: (False, "")):
            leak = app._bypass_leak_diagnosis("apps")
            self.assertFalse(leak["cores"])
            self.assertFalse(leak["tun"])
            self.assertFalse(leak["proxy"])

    def test_leak_diagnosis_detects_orphan_cores_when_happ_disconnected(self) -> None:
        with mock.patch.object(app, "happ_connected", lambda: False), \
             mock.patch.object(app, "_image_pids", lambda name: [1234] if name == "xray.exe" else []), \
             mock.patch.object(app, "happ_tun_active", lambda: False), \
             mock.patch.object(app, "happ_system_proxy", lambda: (False, "")):
            leak = app._bypass_leak_diagnosis("apps")
            self.assertTrue(leak["cores"])

    def test_happ_config_tun_ensure_toggles_auto_route(self) -> None:
        import tempfile, json
        with tempfile.TemporaryDirectory() as tmp:
            cfg_file = Path(tmp) / "config.json"
            cfg_file.write_text(json.dumps({
                "inbounds": [{"type": "tun", "auto_route": True}]
            }), encoding="utf-8")
            with mock.patch.object(app, "HAPP_CONFIG_FILE", cfg_file):
                self.assertTrue(app.happ_config_tun_ensure(False))
                data = json.loads(cfg_file.read_text("utf-8"))
                self.assertFalse(data["inbounds"][0]["auto_route"])

if __name__ == "__main__":
    unittest.main()
