"""Оркестратор bypass_apply: что включается, что гасится, где падаем."""
from __future__ import annotations

import unittest
from unittest import mock

from helpers import load_app

app = load_app()


class ApplyTest(unittest.TestCase):
    def setUp(self) -> None:
        self.calls: list[str] = []
        self.saved: dict = {}
        self.cfg = {
            "mode": "happ", "scope": "all", "apps": [],
            "happ_path": r"C:\Happ\Happ.exe", "gbd_args": "-5", "socks_port": 10808,
        }
        self.happ_connected = False
        self.gbd_running = False
        self.port_ok = True
        self.mode_state = {"tun": True, "system_proxy": True}
        self.patches = [
            mock.patch.object(app, "bypass_cfg", lambda: dict(self.cfg)),
            mock.patch.object(app, "bypass_cfg_save",
                              lambda **kw: self.saved.update(kw)),
            mock.patch.object(app, "happ_connected", lambda *a, **k: self.happ_connected),
            mock.patch.object(app, "_image_pids", self._image_pids),
            mock.patch.object(app, "bypass_happ_start",
                              lambda *a, **k: self._record("happ_start")),
            mock.patch.object(app, "bypass_happ_stop",
                              lambda *a, **k: self._record("happ_stop")),
            mock.patch.object(app, "bypass_gbd_start",
                              lambda *a, **k: self._record("gbd_start")),
            mock.patch.object(app, "bypass_stop",
                              lambda key: self._record(f"stop:{key}")),
            mock.patch.object(app, "proxifyre_start",
                              lambda apps=None, port=None: self._record(
                                  f"pf_start:{len(apps or [])}:{port}")),
            mock.patch.object(app, "proxifyre_stop",
                              lambda: self._record("pf_stop")),
            mock.patch.object(app, "happ_mode_read",
                              lambda: dict(self.mode_state)),
            mock.patch.object(app, "happ_mode_write",
                              lambda **kw: self._record(
                                  f"mode:{int(bool(kw.get('tun')))}"
                                  f"{int(bool(kw.get('system_proxy')))}")),
            mock.patch.object(app, "happ_tunnel_restart",
                              lambda *a, **k: self._record("restart")),
            mock.patch.object(app, "happ_socks_port", lambda *a, **k: 10808),
            mock.patch.object(app, "port_open", lambda *a, **k: self.port_ok),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)

    def _record(self, name: str) -> dict:
        self.calls.append(name)
        return {"ok": True, "detail": name}

    def _image_pids(self, image: str) -> list[int]:
        name = str(image).lower()
        if name == "goodbyedpi.exe":
            return [1] if self.gbd_running else []
        if name.endswith("happ.exe"):
            return [1] if self.happ_connected else []
        return []

    def test_apps_scope_switches_happ_to_socks_and_starts_proxyfyre(self) -> None:
        self.cfg.update(scope="apps", apps=["chrome.exe", "discord.exe"])
        result = app.bypass_apply("apps", "happ")
        self.assertTrue(result["ok"])
        self.assertIn("mode:00", self.calls)
        self.assertIn("happ_start", self.calls)
        self.assertIn("pf_start:2:10808", self.calls)
        self.assertEqual(self.saved["scope"], "apps")
        self.assertEqual(self.saved["mode"], "happ")

    def test_global_scope_enables_tun_and_stops_proxyfyre(self) -> None:
        self.mode_state = {"tun": False, "system_proxy": False}
        app.bypass_apply("all", "happ")
        self.assertIn("mode:11", self.calls)
        self.assertIn("pf_stop", self.calls)
        self.assertNotIn("pf_start:0:10808", self.calls)

    def test_apps_scope_requires_happ(self) -> None:
        self.cfg.update(scope="apps", apps=["chrome.exe"])
        with self.assertRaises(RuntimeError) as ctx:
            app.bypass_apply("apps", "gbd")
        self.assertIn("GoodbyeDPI", str(ctx.exception))

    def test_apps_scope_requires_apps(self) -> None:
        self.cfg.update(scope="apps", apps=[])
        with self.assertRaises(RuntimeError) as ctx:
            app.bypass_apply("apps", "happ")
        self.assertIn("программы", str(ctx.exception))

    def test_apps_scope_fails_when_socks_not_listening(self) -> None:
        self.cfg.update(scope="apps", apps=["chrome.exe"])
        self.port_ok = False
        with self.assertRaises(RuntimeError) as ctx:
            app.bypass_apply("apps", "happ")
        self.assertIn("SOCKS", str(ctx.exception))
        self.assertNotIn("pf_start:1:10808", self.calls)

    def test_gbd_mode_stops_happ_and_starts_gbd(self) -> None:
        self.happ_connected = True
        self.cfg.update(mode="gbd", scope="all")
        app.bypass_apply("all", "gbd")
        self.assertIn("happ_stop", self.calls)
        self.assertIn("gbd_start", self.calls)
        self.assertEqual(self.saved["mode"], "gbd")

    def test_happ_mode_stops_gbd(self) -> None:
        self.gbd_running = True
        app.bypass_apply("all", "happ")
        self.assertIn("stop:gbd", self.calls)

    def test_tunnel_restarted_only_when_settings_change(self) -> None:
        self.happ_connected = True
        app.bypass_apply("all", "happ")
        self.assertNotIn("restart", self.calls)
        self.cfg.update(scope="apps", apps=["chrome.exe"])
        self.calls.clear()
        app.bypass_apply("apps", "happ")
        self.assertIn("restart", self.calls)

    def test_unknown_scope_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            app.bypass_apply("some", "happ")

    def test_unknown_mode_rejected(self) -> None:
        with self.assertRaises(RuntimeError):
            app.bypass_apply("all", "vpn")

    def test_scope_and_mode_defaults_come_from_config(self) -> None:
        self.cfg.update(scope="apps", apps=["chrome.exe"])
        app.bypass_apply()
        self.assertIn("pf_start:1:10808", self.calls)


class StopTest(unittest.TestCase):
    def test_stop_happ_also_stops_proxyfyre(self) -> None:
        calls: list[str] = []
        with mock.patch.object(app, "proxifyre_stop",
                               lambda: calls.append("pf") or {"detail": "pf"}):
            with mock.patch.object(app, "bypass_happ_stop",
                                   lambda: calls.append("happ") or {"detail": "happ"}):
                result = app.bypass_stop("happ")
        self.assertEqual(calls, ["pf", "happ"])
        self.assertEqual(result["detail"], "pf\nhapp")

    def test_stop_happ_survives_proxyfyre_error(self) -> None:
        with mock.patch.object(app, "proxifyre_stop",
                               mock.Mock(side_effect=RuntimeError("нет прав"))):
            with mock.patch.object(app, "bypass_happ_stop",
                                   lambda: {"detail": "happ off"}):
                result = app.bypass_stop("happ")
        self.assertIn("ProxiFyre: нет прав", result["detail"])
        self.assertIn("happ off", result["detail"])


if __name__ == "__main__":
    unittest.main()
