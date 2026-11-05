"""Тесты объединения служебных процессов WorkBuddy и Antigravity (Task 12)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
ROOT_DIR = TESTS_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from helpers import load_module

app = load_module("apps_catalog")


class BundleAppsCatalogTest(unittest.TestCase):
    def catalog(self, **kwargs):
        return app.app_catalog(
            running_paths=kwargs.get("running", []),
            registry_paths=kwargs.get("registry", []),
            shortcuts=kwargs.get("shortcuts", []),
        )

    def test_workbuddy_internal_processes_hidden(self) -> None:
        """Служебные процессы WorkBuddy (editor_sdk, betterleaks, node) не должны быть отдельными записями."""
        running = [
            r"C:\Users\admin\AppData\Local\Programs\WorkBuddyAI\WorkBuddyAI.exe",
            r"C:\Users\admin\AppData\Local\Programs\WorkBuddyAI\resources\app.asar.unpacked\cli\vendor\sandbox\5.9.5\betterleaks.exe",
            r"C:\Users\admin\AppData\Local\Programs\WorkBuddyAI\resources\workbuddy-editor-sdk-supervisor.exe",
            r"C:\Users\admin\.workbuddy-ai\binaries\node\versions\22.22.2-6\node.exe",
        ]
        items = self.catalog(running=running)
        exes = [item.exe.lower() for item in items]
        self.assertIn("workbuddyai.exe", exes)
        self.assertNotIn("betterleaks.exe", exes)
        self.assertNotIn("workbuddy-editor-sdk-supervisor.exe", exes)
        self.assertNotIn("node.exe", exes)
        self.assertEqual(len(exes), 1)
        self.assertTrue(items[0].running)
        self.assertEqual(items[0].name, "WorkBuddy AI")

    def test_antigravity_language_server_hidden(self) -> None:
        """Служебный language_server.exe Antigravity не должен быть отдельной записью."""
        running = [
            r"C:\Users\admin\AppData\Local\Programs\antigravity\Antigravity.exe",
            r"C:\Users\admin\AppData\Local\Programs\antigravity\resources\bin\language_server.exe",
        ]
        items = self.catalog(running=running)
        exes = [item.exe.lower() for item in items]
        self.assertIn("antigravity.exe", exes)
        self.assertNotIn("language_server.exe", exes)
        self.assertEqual(len(exes), 1)
        self.assertTrue(items[0].running)

    def test_child_process_bubbles_running_status_to_parent(self) -> None:
        """Если запущен только фоновый процесс WorkBuddy (node.exe), WorkBuddy AI считается запущенным."""
        running = [
            r"C:\Users\admin\.workbuddy-ai\binaries\node\versions\22.22.2-6\node.exe",
        ]
        items = self.catalog(running=running)
        exes = [item.exe.lower() for item in items]
        self.assertEqual(exes, ["workbuddyai.exe"])
        self.assertTrue(items[0].running)
        self.assertEqual(items[0].name, "WorkBuddy AI")


if __name__ == "__main__":
    unittest.main()
