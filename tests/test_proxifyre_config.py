"""ProxiFyre: генерация app-config.json, идемпотентность, чтение статуса."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from helpers import load_app

app = load_app()


class ProxiFyreConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name) / "proxifyre"
        self.config = self.dir / "app-config.json"
        self.exe = self.dir / "ProxiFyre.exe"
        self._patches = [
            mock.patch.object(app, "PROXIFYRE_DIR", self.dir),
            mock.patch.object(app, "PROXIFYRE_CONFIG", self.config),
            mock.patch.object(app, "PROXIFYRE_EXE", self.exe),
        ]
        for patch in self._patches:
            patch.start()
            self.addCleanup(patch.stop)

    def test_config_data(self) -> None:
        data = app.proxifyre_config_data(["Chrome.exe", "chrome.exe"], 10808)
        self.assertEqual(data["logLevel"], "INFO")
        proxy = data["proxies"][0]
        self.assertEqual(proxy["appNames"], ["Chrome.exe"])
        self.assertEqual(proxy["socks5ProxyEndpoint"], "127.0.0.1:10808")
        self.assertEqual(proxy["supportedProtocols"], ["TCP", "UDP"])

    def test_write_creates_file_and_reports_change(self) -> None:
        self.assertTrue(app.proxifyre_write_config(["chrome.exe"], 10808))
        data = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(data["proxies"][0]["appNames"], ["chrome.exe"])

    def test_second_write_is_noop(self) -> None:
        app.proxifyre_write_config(["chrome.exe"], 10808)
        self.assertFalse(app.proxifyre_write_config(["chrome.exe"], 10808))

    def test_port_change_rewrites(self) -> None:
        app.proxifyre_write_config(["chrome.exe"], 10808)
        self.assertTrue(app.proxifyre_write_config(["chrome.exe"], 2080))

    def test_apps_change_rewrites(self) -> None:
        app.proxifyre_write_config(["chrome.exe"], 10808)
        self.assertTrue(app.proxifyre_write_config(["chrome.exe", "discord.exe"], 10808))

    def test_status_reads_apps_and_installed(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.exe.write_bytes(b"")
        app.proxifyre_write_config(["Chrome.exe", "Discord.exe"], 10808)
        with mock.patch.object(app, "_svc_state", lambda name: app.SVC_RUNNING):
            status = app.proxifyre_status()
        self.assertTrue(status["installed"])
        self.assertTrue(status["running"])
        self.assertTrue(status["driver"])
        self.assertEqual(status["apps"], ["Chrome.exe", "Discord.exe"])

    def test_status_without_config(self) -> None:
        with mock.patch.object(app, "_svc_state", lambda name: None):
            status = app.proxifyre_status()
        self.assertEqual(status["apps"], [])
        self.assertFalse(status["running"])
        self.assertFalse(status["installed"])
        self.assertFalse(status["driver"])

    def test_status_ignores_broken_config(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.config.write_text("{broken", encoding="utf-8")
        with mock.patch.object(app, "_svc_state", lambda name: app.SVC_STOPPED):
            status = app.proxifyre_status()
        self.assertEqual(status["apps"], [])

    def test_start_requires_apps(self) -> None:
        with self.assertRaises(RuntimeError):
            app.proxifyre_start([], 10808)

    def test_start_requires_exe(self) -> None:
        with mock.patch.object(app, "bypass_cfg",
                               lambda: {"apps": ["chrome.exe"], "socks_port": 10808}):
            with self.assertRaises(RuntimeError) as ctx:
                app.proxifyre_start(["chrome.exe"], 10808)
        self.assertIn("ProxiFyre", str(ctx.exception))

    def test_start_requires_driver(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        self.exe.write_bytes(b"")
        with mock.patch.object(app, "proxifyre_driver_ok", lambda: False), \
                mock.patch.object(app, "bypass_cfg",
                                  lambda: {"apps": ["chrome.exe"], "socks_port": 10808}):
            with self.assertRaises(RuntimeError) as ctx:
                app.proxifyre_start(["chrome.exe"], 10808)
        self.assertIn("ndisrd", str(ctx.exception))

    def test_stop_without_service(self) -> None:
        with mock.patch.object(app, "_svc_state", lambda name: None):
            self.assertEqual(app.proxifyre_stop()["detail"], "")

    def test_stop_when_already_stopped(self) -> None:
        with mock.patch.object(app, "_svc_state", lambda name: app.SVC_STOPPED):
            self.assertIn("уже выключен", app.proxifyre_stop()["detail"])


if __name__ == "__main__":
    unittest.main()
