"""Автособирание утечек (Task 16): bypass_auto_heal, bypass_diagnose."""
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


class DiagnoseTest(unittest.TestCase):
    def test_all_clear_when_happ_connected(self) -> None:
        with mock.patch.object(app, "happ_connected", lambda: True), \
             mock.patch.object(app, "_image_pids", lambda name: [1234]), \
             mock.patch.object(app, "happ_tun_active", lambda: False), \
             mock.patch.object(app, "happ_system_proxy", lambda: (False, "")):
            diag = app.bypass_diagnose("apps")
            self.assertTrue(diag["all_clear"])
            self.assertEqual(diag["what"], [])

    def test_tun_leak_detected(self) -> None:
        with mock.patch.object(app, "happ_connected", lambda: False), \
             mock.patch.object(app, "_image_pids", lambda name: []), \
             mock.patch.object(app, "happ_tun_active", lambda: True), \
             mock.patch.object(app, "happ_system_proxy", lambda: (False, "")):
            diag = app.bypass_diagnose("apps")
            self.assertTrue(diag["tun"])
            self.assertEqual(diag["what"], ["TUN-адаптер"])
            self.assertFalse(diag["proxy"])
            self.assertFalse(diag["cores"])

    def test_proxy_leak_detected(self) -> None:
        with mock.patch.object(app, "happ_connected", lambda: False), \
             mock.patch.object(app, "_image_pids", lambda name: []), \
             mock.patch.object(app, "happ_tun_active", lambda: False), \
             mock.patch.object(app, "happ_system_proxy", lambda: (True, "127.0.0.1:10809")):
            diag = app.bypass_diagnose("apps")
            self.assertTrue(diag["proxy"])
            self.assertIn("системный прокси", diag["what"][0])
            self.assertFalse(diag["tun"])
            self.assertFalse(diag["cores"])

    def test_cores_leak_detected(self) -> None:
        with mock.patch.object(app, "happ_connected", lambda: False), \
             mock.patch.object(app, "_image_pids", lambda name: [1234] if name == "xray.exe" else []), \
             mock.patch.object(app, "happ_tun_active", lambda: False), \
             mock.patch.object(app, "happ_system_proxy", lambda: (False, "")):
            diag = app.bypass_diagnose("apps")
            self.assertTrue(diag["cores"])
            self.assertIn("ядро Happ", diag["what"][0])
            self.assertFalse(diag["tun"])
            self.assertFalse(diag["proxy"])


class HealTest(unittest.TestCase):
    def test_heal_tun(self) -> None:
        with mock.patch.object(app, "bypass_diagnose",
                               lambda scope: {"tun": True, "proxy": False, "cores": False,
                                              "what": ["TUN-адаптер"], "all_clear": False}), \
             mock.patch.object(app, "happ_tun_disable", lambda: None):
            res = app.bypass_auto_heal("apps")
            self.assertTrue(res["ok"])
            self.assertEqual(res["healed"], ["tun"])

    def test_heal_proxy(self) -> None:
        with mock.patch.object(app, "bypass_diagnose",
                               lambda scope: {"tun": False, "proxy": True, "cores": False,
                                              "what": ["системный прокси"], "all_clear": False}), \
             mock.patch.object(app, "set_windows_system_proxy", lambda en: True):
            res = app.bypass_auto_heal("apps")
            self.assertTrue(res["ok"])
            self.assertEqual(res["healed"], ["proxy"])

    def test_heal_cores_kills_processes(self) -> None:
        with mock.patch.object(app, "bypass_diagnose",
                               lambda scope: {"tun": False, "proxy": False, "cores": True,
                                              "what": ["ядра Happ"], "all_clear": False}), \
             mock.patch.object(app, "_image_pids", lambda name: [1234] if name == "xray.exe" else []), \
             mock.patch("subprocess.run"):
            res = app.bypass_auto_heal("apps")
            self.assertTrue(res["ok"])
            self.assertEqual(res["healed"], ["cores"])

    def test_heal_noop_when_all_clear(self) -> None:
        with mock.patch.object(app, "bypass_diagnose",
                               lambda scope: {"all_clear": True, "what": [],
                                              "tun": False, "proxy": False, "cores": False}):
            res = app.bypass_auto_heal("apps")
            self.assertEqual(res["healed"], [])
            self.assertEqual(res["message"], "Утечек не обнаружено")

    def test_auto_heal_if_leak_action_nothing(self) -> None:
        with mock.patch.object(app, "_bypass_active", lambda: False), \
             mock.patch.object(app, "bypass_diagnose",
                               lambda scope: {"all_clear": True, "what": []}):
            res = app.bypass_auto_heal_if_leak("apps")
            self.assertEqual(res["action"], "nothing")

    def test_auto_heal_if_leak_action_healed(self) -> None:
        with mock.patch.object(app, "_bypass_active", lambda: False), \
             mock.patch.object(app, "bypass_diagnose",
                               lambda scope: {"tun": True, "proxy": False, "cores": False,
                                              "all_clear": False, "what": ["TUN"]}), \
             mock.patch.object(app, "happ_tun_disable", lambda: None):
            res = app.bypass_auto_heal_if_leak("apps")
            self.assertEqual(res["action"], "healed")
            self.assertTrue(res["ok"])

    def test_happ_auto_disconnect(self) -> None:
        written = {}
        with mock.patch.object(app, "happ_mode_write",
                               lambda tun=None, system_proxy=None: (written.update({"tun": tun, "system_proxy": system_proxy}) or True)), \
             mock.patch.object(app, "set_windows_system_proxy", lambda en: True):
            ok = app.happ_auto_disconnect()
            self.assertTrue(ok)
            self.assertFalse(written.get("tun"))
            self.assertFalse(written.get("system_proxy"))


class StatusContractTest(unittest.TestCase):
    def test_status_keeps_proxyfyre_keys(self) -> None:
        """bypass_status не должен перезаписывать состояние службы опечаткой."""
        status = {"running": True, "apps": ["chrome.exe"]}
        with mock.patch.object(app, "bypass_cfg",
                               lambda: {"scope": "apps", "apps": ["chrome.exe"],
                                        "mode": "happ", "happ_path": r"C:\Happ\Happ.exe",
                                        "gbd_args": "-5"}), \
             mock.patch.object(app, "_bypass_track", lambda: {}), \
             mock.patch.object(app, "bypass_admin", lambda: True), \
             mock.patch.object(app, "_running_images", lambda: set()), \
             mock.patch.object(app, "happ_socks_port", lambda: 10808), \
             mock.patch.object(app, "_image_pids", lambda name: []), \
             mock.patch.object(app, "happ_connected", lambda *a, **k: False), \
             mock.patch.object(app, "port_open", lambda *a, **k: False), \
             mock.patch.object(app, "happ_mode_read",
                               lambda: {"tun": False, "system_proxy": False}), \
             mock.patch.object(app, "proxifyre_rescan",
                               lambda: {"ok": True, "apps": ["chrome.exe"]}), \
             mock.patch.object(app, "proxifyre_status", lambda: dict(status)), \
             mock.patch.object(app, "_bypass_leak_diagnosis", lambda scope: {}):
            state = app.bypass_status()
        self.assertEqual(state["proxyfyre"], status)
        self.assertEqual(state["proxyfyre_rescan"],
                         {"ok": True, "apps": ["chrome.exe"]})
        self.assertNotIn("proxifyre", state)


if __name__ == "__main__":
    unittest.main()
