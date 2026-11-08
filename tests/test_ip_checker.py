"""Тесты функции проверки внешнего IP (Task 13)."""
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


class IpCheckerTest(unittest.TestCase):
    def test_bypass_check_ip_success(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self):
                return b'{"ip": "185.220.101.5"}'

        with mock.patch("urllib.request.urlopen", return_value=FakeResponse()):
            res = app.bypass_check_ip()
            self.assertTrue(res["ok"])
            self.assertEqual(res["ip"], "185.220.101.5")

    def test_bypass_check_ip_fallback_failure(self) -> None:
        with mock.patch("urllib.request.urlopen", side_effect=Exception("network timeout")):
            res = app.bypass_check_ip(timeout=0.1)
            self.assertFalse(res["ok"])
            self.assertEqual(res["ip"], "")

    def test_bypass_check_direct_ip_with_geo(self) -> None:
        class FakeResponse:
            def __init__(self, body: bytes):
                self.body = body
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self):
                return self.body

        def fake_urlopen(req, timeout=3.0):
            url = req.full_url or ""
            if "ip-api.com" in url:
                body = b'{"status":"success","country":"Russia","org":"Test ISP","city":"Moscow"}'
            else:
                body = b'{"ip": "95.161.100.55"}'
            return FakeResponse(body)

        class FakeOpener:
            def open(self, req, timeout=3.0):
                return fake_urlopen(req, timeout=timeout)

        with mock.patch("urllib.request.build_opener", return_value=FakeOpener()):
            res = app.bypass_check_direct_ip()
        self.assertTrue(res["ok"])
        self.assertEqual(res["ip"], "95.161.100.55")
        self.assertEqual(res["country"], "Russia")
        self.assertTrue(res.get("direct", False))

    def test_bypass_check_proxy_ip_no_listener(self) -> None:
        import socket
        with mock.patch("socket.socket.connect", side_effect=OSError("connection refused")):
            res = app.bypass_check_proxy_ip(10808)
            self.assertFalse(res["ok"])

    def test_bypass_compare_socks_unavailable(self) -> None:
        direct = {"ok": True, "ip": "95.161.100.55", "country": "RU", "direct": True}
        proxy = {"ok": False, "error": "SOCKS не слушается"}
        res = app.bypass_compare_connections(direct, proxy)
        self.assertFalse(res["ok"])
        self.assertEqual(res["verdict"], "SOCKS НЕ ДОСТУПЕН")

    def test_bypass_compare_ip_match_says_leak(self) -> None:
        direct = {"ok": True, "ip": "95.161.100.55", "country": "RU", "direct": True}
        proxy = {"ok": True, "ip": "95.161.100.55", "proxy_port": 10808}
        with mock.patch.object(app, "happ_tun_active", return_value=False):
            res = app.bypass_compare_connections(direct, proxy)
            self.assertFalse(res["ok"])
            self.assertEqual(res["verdict"], "ОБХОД НЕ РАБОТАЕТ")

    def test_bypass_compare_ip_match_tun_active_says_ok(self) -> None:
        direct = {"ok": True, "ip": "185.220.101.5", "country": "NL", "direct": True}
        proxy = {"ok": True, "ip": "185.220.101.5", "proxy_port": 10808}
        with mock.patch.object(app, "happ_tun_active", return_value=True):
            res = app.bypass_compare_connections(direct, proxy)
            self.assertTrue(res["ok"])
            self.assertEqual(res["verdict"], "ОБХОД РАБОТАЕТ (TUN)")

    def test_bypass_compare_ip_different_says_ok(self) -> None:
        direct = {"ok": True, "ip": "95.161.100.55", "country": "RU", "direct": True}
        proxy = {"ok": True, "ip": "185.220.101.5", "country": "NL", "proxy_port": 10808}
        res = app.bypass_compare_connections(direct, proxy)
        self.assertTrue(res["ok"])
        self.assertEqual(res["verdict"], "ОБХОД РАБОТАЕТ")
        self.assertIn("Обход SOCKS", res["message"])


if __name__ == "__main__":
    unittest.main()
