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


if __name__ == "__main__":
    unittest.main()
