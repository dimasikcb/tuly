"""Перезапуск ProxiFyre, когда отмеченная программа стартовала заново."""
from __future__ import annotations

import unittest
from unittest import mock

from helpers import load_module

app = load_module("bypass_core")


class RescanTest(unittest.TestCase):
    def setUp(self) -> None:
        self.pids: list[tuple[int, str]] = []
        self.calls: list[str] = []
        app._PROXIFYRE_PIDS = None
        app._PROXIFYRE_LAST_RESTART = 0.0
        self.patches = [
            mock.patch.object(app, "bypass_cfg",
                              lambda: {"apps": ["browser.exe"], "socks_port": 10808}),
            mock.patch.object(app, "running_processes", lambda: list(self.pids)),
            mock.patch.object(app, "_svc_state", lambda name: app.SVC_RUNNING),
            mock.patch.object(app, "proxifyre_service_name", lambda: "ProxiFyreService"),
            mock.patch.object(app, "_svc_stop",
                              lambda name: self.calls.append("stop") or True),
            mock.patch.object(app, "_svc_start",
                              lambda name, wait=12.0: self.calls.append("start") or True),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(setattr, app, "_PROXIFYRE_PIDS", None)

    def test_no_selected_process_no_restart(self) -> None:
        self.pids = [(1, r"C:\other\other.exe")]
        app.proxifyre_snapshot()
        self.assertIsNone(app.proxifyre_rescan())
        self.assertEqual(self.calls, [])

    def test_new_process_restarts_service(self) -> None:
        self.pids = [(10, r"C:\Apps\browser.exe")]
        app.proxifyre_snapshot()
        self.pids.append((11, r"C:\Apps\browser.exe"))
        result = app.proxifyre_rescan()
        self.assertTrue(result["ok"])
        self.assertEqual(result["apps"], [11])
        self.assertEqual(self.calls, ["stop", "start"])

    def test_cooldown_blocks_second_restart(self) -> None:
        self.pids = [(10, r"C:\Apps\browser.exe")]
        app.proxifyre_snapshot()
        self.pids.append((11, r"C:\Apps\browser.exe"))
        app.proxifyre_rescan()
        self.calls.clear()
        self.pids.append((12, r"C:\Apps\browser.exe"))
        self.assertIsNone(app.proxifyre_rescan())
        self.assertEqual(self.calls, [])

    def test_service_stopped_does_nothing(self) -> None:
        self.pids = [(10, r"C:\Apps\browser.exe")]
        with mock.patch.object(app, "_svc_state", lambda name: app.SVC_STOPPED):
            self.assertIsNone(app.proxifyre_rescan())
        self.assertEqual(self.calls, [])


if __name__ == "__main__":
    unittest.main()
