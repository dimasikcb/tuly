"""Happ: режим TUN/системный прокси в реестре и определение порта SOCKS."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
ROOT_DIR = TESTS_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from helpers import load_module

app = load_module("bypass_core")


class HappModeTest(unittest.TestCase):
    def test_read_defaults_when_no_key(self) -> None:
        with mock.patch.object(app, "_reg_bool", lambda *a, **k: None):
            self.assertEqual(app.happ_mode_read(),
                             {"tun": True, "system_proxy": True})

    def test_read_respects_zero(self) -> None:
        with mock.patch.object(app, "_reg_bool", lambda *a, **k: False):
            self.assertEqual(app.happ_mode_read(),
                             {"tun": False, "system_proxy": False})

    def test_read_prefers_real_systemproxy_key(self) -> None:
        # Happ хранит настройку в «systemProxy» (REG_SZ); legacy «system_proxy»
        # не должен перебивать реальное значение.
        def fake(_key, name, default=True):
            return {"tun": True, "systemProxy": False, "system_proxy": True}.get(name)

        with mock.patch.object(app, "_reg_bool", fake):
            self.assertEqual(app.happ_mode_read(),
                             {"tun": True, "system_proxy": False})

    def test_write_sets_both_values(self) -> None:
        written = {}

        def fake_set(key, name, value):
            written[name] = value
            return True

        def fake_read(_key, name, default=True):
            return written.get(name, default)

        with mock.patch.object(app, "_reg_set_bool", fake_set), \
                mock.patch.object(app, "_reg_set_int", lambda *a, **k: True), \
                mock.patch.object(app, "_reg_bool", fake_read):
            result = app.happ_mode_write(tun=False, system_proxy=False)
        self.assertEqual(written, {"tun": False, "systemProxy": False})
        self.assertFalse(result["tun"])
        self.assertFalse(result["system_proxy"])

    def test_write_failure_raises(self) -> None:
        with mock.patch.object(app, "_reg_set_bool", lambda *a, **k: False):
            with self.assertRaises(RuntimeError):
                app.happ_mode_write(tun=False)

    def test_write_partial_keeps_other_value(self) -> None:
        written = {"systemProxy": True}

        def fake_set(_key, name, value):
            written[name] = value
            return True

        def fake_read(_key, name, default=True):
            return written.get(name, default)

        with mock.patch.object(app, "_reg_set_bool", fake_set), \
                mock.patch.object(app, "_reg_set_int", lambda *a, **k: True), \
                mock.patch.object(app, "_reg_bool", fake_read):
            app.happ_mode_write(tun=False)
        self.assertEqual(written["tun"], False)
        self.assertEqual(written["systemProxy"], True)


class HappSocksPortTest(unittest.TestCase):
    def test_port_from_socks_inbound(self) -> None:
        data = {"inbounds": [
            {"type": "socks", "listen": "127.0.0.1", "listen_port": 10809}
        ]}
        self.assertEqual(app.happ_socks_port_from(data), 10809)

    def test_port_from_mixed_inbound(self) -> None:
        data = {"inbounds": [
            {"type": "mixed", "listen": "127.0.0.1", "port": 2080}
        ]}
        self.assertEqual(app.happ_socks_port_from(data), 2080)

    def test_port_from_socks_outbound(self) -> None:
        data = {"outbounds": [
            {"type": "direct", "tag": "direct"},
            {"type": "socks", "server": "127.0.0.1", "server_port": 10808},
        ]}
        self.assertEqual(app.happ_socks_port_from(data), 10808)

    def test_localhost_and_socks5(self) -> None:
        data = {"outbounds": [{"type": "socks5", "server": "localhost",
                               "server_port": 2080}]}
        self.assertEqual(app.happ_socks_port_from(data), 2080)

    def test_remote_socks_ignored(self) -> None:
        data = {"outbounds": [{"type": "socks", "server": "10.0.0.5",
                               "server_port": 1080}]}
        self.assertEqual(app.happ_socks_port_from(data), app.HAPP_SOCKS_DEFAULT)

    def test_no_outbounds(self) -> None:
        self.assertEqual(app.happ_socks_port_from({}), app.HAPP_SOCKS_DEFAULT)
        self.assertEqual(app.happ_socks_port_from(None), app.HAPP_SOCKS_DEFAULT)

    def test_broken_port_value(self) -> None:
        data = {"outbounds": [{"type": "socks", "server": "127.0.0.1",
                               "server_port": "много"}]}
        self.assertEqual(app.happ_socks_port_from(data), app.HAPP_SOCKS_DEFAULT)

    def test_reads_real_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text(json.dumps({"outbounds": [
                {"type": "socks", "server": "127.0.0.1", "server_port": 1234}
            ]}), encoding="utf-8")
            self.assertEqual(app.happ_socks_port(path), 1234)

    def test_missing_file(self) -> None:
        self.assertEqual(app.happ_socks_port(Path("Z:/nope/config.json")),
                         app.HAPP_SOCKS_DEFAULT)

    def test_broken_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.json"
            path.write_text("{oops", encoding="utf-8")
            self.assertEqual(app.happ_socks_port(path), app.HAPP_SOCKS_DEFAULT)


if __name__ == "__main__":
    unittest.main()
