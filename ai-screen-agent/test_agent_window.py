"""
Тесты для agent_window.py (AI Screen Bridge).

Обычный запуск — headless, без открытия окон, без сети:
    python test_agent_window.py

Живая проверка с настоящим Tk-окном (на 1-2 секунды появится magenta-окно;
проверяет WDA_EXCLUDEFROMCAPTURE и GetWindowRect на реальных API):
    python test_agent_window.py --live
"""

import os
import subprocess
import sys
import time
import unittest
from unittest import mock

from PIL import ImageChops

REPO_DIR = os.path.dirname(os.path.abspath(__file__))
if REPO_DIR not in sys.path:
    sys.path.insert(0, REPO_DIR)

import agent_window as aw

LIVE = "--live" in sys.argv
if LIVE:
    sys.argv.remove("--live")

# Пакеты, которые импорт agent_window тянуть НЕ имеет права
# (GUI / сеть / сторонние зависимости).
FORBIDDEN_NEW_PACKAGES = {
    "tkinter", "socket", "ssl", "urllib", "http", "requests",
    "PIL", "mss", "pyautogui", "google",
}


def _run_py(code):
    """Запускает код в чистом интерпретаторе, возвращает CompletedProcess."""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=REPO_DIR,
        env=env,
        timeout=60,
    )


class AgentWindowTestBase(unittest.TestCase):
    """Изолирует внутреннее состояние модуля между тестами."""

    def setUp(self):
        self._saved_state = dict(aw._STATE)
        self._saved_is_windows = aw.IS_WINDOWS

    def tearDown(self):
        aw._STATE.clear()
        aw._STATE.update(self._saved_state)
        aw.IS_WINDOWS = self._saved_is_windows


class TestModuleContract(AgentWindowTestBase):
    """Контракт модуля: константы, чистый импорт, не-Windows guard."""

    def test_wda_constant(self):
        self.assertEqual(aw.WDA_EXCLUDEFROMCAPTURE, 0x00000011)
        self.assertEqual(aw.WDA_NONE, 0x0)

    def test_import_adds_no_gui_or_network_modules(self):
        # Импорт agent_window не должен тянуть tkinter, сеть или сторонние пакеты.
        probe = (
            "import sys; sys.path.insert(0, r'{d}'); {imp}\n"
            "print('\\n'.join(sorted(sys.modules)))"
        )
        base = _run_py(probe.format(d=REPO_DIR, imp=""))
        after = _run_py(probe.format(d=REPO_DIR, imp="import agent_window"))
        self.assertEqual(base.returncode, 0, base.stderr)
        self.assertEqual(after.returncode, 0, after.stderr)

        base_mods = set(base.stdout.splitlines())
        after_mods = set(after.stdout.splitlines())
        new_mods = after_mods - base_mods
        bad = {m for m in new_mods if m.split(".")[0] in FORBIDDEN_NEW_PACKAGES}
        self.assertEqual(bad, set(), f"импорт agent_window тянет лишнее: {sorted(bad)}")
        # Ожидаемая новая зависимость — только ctypes (стандартная библиотека).
        self.assertIn("ctypes", after_mods)

    @unittest.skipUnless(aw.IS_WINDOWS, "live-проверка актуальна только на Windows")
    def test_user32_prototypes_ready_after_import(self):
        # Импорт не бросает исключений и настраивает прототипы user32
        # (wintypes.BOOL разворачивается в ctypes.c_long).
        import ctypes.wintypes
        self.assertIsNotNone(aw._user32)
        self.assertEqual(aw._user32.SetWindowDisplayAffinity.restype, ctypes.wintypes.BOOL)
        self.assertEqual(aw._user32.GetWindowRect.restype, ctypes.wintypes.BOOL)

    def test_non_windows_guard(self):
        aw.IS_WINDOWS = False
        logs = []
        status = aw.exclude_from_capture(object(), logs.append)
        self.assertIn("Windows", status)
        self.assertTrue(logs, "log_fn должен получить сообщение")
        self.assertEqual(logs[-1], status)
        self.assertFalse(aw.capture_exclusion_active())
        self.assertIsNone(aw.tool_window_rect())
        self.assertFalse(aw.point_in_tool_window(100, 100))

    def test_log_fn_exceptions_are_swallowed(self):
        # Кривой log_fn не должен ронять функцию.
        def broken_log(_msg):
            raise RuntimeError("log is broken")

        status = aw.exclude_from_capture(object(), broken_log)
        self.assertIsInstance(status, str)
        self.assertTrue(status)


class TestWithoutRealWindow(AgentWindowTestBase):
    """Поведение на фейковых/битых HWND — реальный user32, но без окна."""

    def test_exclude_with_broken_winfo_id(self):
        class FakeRoot:
            def winfo_id(self):
                raise RuntimeError("no window yet")

        logs = []
        status = aw.exclude_from_capture(FakeRoot(), logs.append)
        self.assertIn("HWND", status)
        self.assertTrue(status.startswith("⚠️"))
        self.assertTrue(logs)
        self.assertIsNone(aw._STATE.get("hwnd"))
        self.assertFalse(aw.capture_exclusion_active())

    def test_exclude_with_invalid_hwnd_fails_cleanly(self):
        class FakeRoot:
            def winfo_id(self):
                return 0xDEADBEEF  # практически наверняка не существующий HWND

        logs = []
        status = aw.exclude_from_capture(FakeRoot(), logs.append)
        self.assertIn("GetLastError=", status)
        self.assertTrue(status.startswith("⚠️"))
        self.assertEqual(logs[-1], status)
        # HWND биндится независимо от успеха скрытия — детект самоклика важнее.
        self.assertEqual(aw._STATE.get("hwnd"), 0xDEADBEEF)
        self.assertFalse(aw.capture_exclusion_active())
        # GetWindowRect на битом HWND -> None, точка не считается попаданием.
        self.assertIsNone(aw.tool_window_rect())
        self.assertFalse(aw.point_in_tool_window(0xDEADBEEF, 0xDEADBEEF))

    @unittest.skipUnless(aw.IS_WINDOWS, "нужен реальный user32")
    def test_get_window_rect_invalid_hwnd_returns_none(self):
        self.assertIsNone(aw._get_window_rect(0xDEADBEEF))

    def test_exclude_is_idempotent(self):
        class FakeRoot:
            def winfo_id(self):
                return 0xDEADBEEF

        s1 = aw.exclude_from_capture(FakeRoot(), None)
        s2 = aw.exclude_from_capture(FakeRoot(), None)
        self.assertIsInstance(s1, str)
        self.assertIsInstance(s2, str)


class TestRectLogic(AgentWindowTestBase):
    """Чистая логика попадания точки — на подменённом прямоугольнике."""

    RECT = (100, 200, 700, 900)  # left, top, right, bottom

    def setUp(self):
        super().setUp()
        aw._STATE["hwnd"] = 0x1
        aw._STATE["exclusion_active"] = False
        patcher = mock.patch.object(aw, "_get_window_rect", return_value=self.RECT)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_tool_window_rect_passthrough(self):
        self.assertEqual(aw.tool_window_rect(), self.RECT)

    def test_corners_and_edges(self):
        l, t, r, b = self.RECT
        self.assertTrue(aw.point_in_tool_window(l, t))        # левый-верхний включён
        self.assertTrue(aw.point_in_tool_window(r - 1, b - 1))
        self.assertFalse(aw.point_in_tool_window(r, b))       # правая/нижняя исключены
        self.assertFalse(aw.point_in_tool_window(r - 1, b))
        self.assertFalse(aw.point_in_tool_window(r, b - 1))

    def test_inside_and_outside(self):
        self.assertTrue(aw.point_in_tool_window(400, 500))    # центр
        self.assertFalse(aw.point_in_tool_window(50, 500))    # левее
        self.assertFalse(aw.point_in_tool_window(750, 500))   # правее
        self.assertFalse(aw.point_in_tool_window(400, 100))   # выше
        self.assertFalse(aw.point_in_tool_window(400, 950))   # ниже

    def test_unbound_window(self):
        aw._STATE["hwnd"] = None
        self.assertIsNone(aw.tool_window_rect())
        self.assertFalse(aw.point_in_tool_window(400, 500))

    def test_get_window_rect_failure_is_not_a_hit(self):
        # Окно привязано, но GetWindowRect не даёт данных (уничтожено/битое).
        with mock.patch.object(aw, "_get_window_rect", return_value=None):
            self.assertIsNone(aw.tool_window_rect())
            self.assertFalse(aw.point_in_tool_window(400, 500))


@unittest.skipUnless(LIVE, "живая проверка: python test_agent_window.py --live")
class TestLiveRealWindow(AgentWindowTestBase):
    """Живая проверка на настоящем Tk-окне: rect, попадание точки, WDA-скрытие.

    Наследует AgentWindowTestBase: live-класс выполняется первым (алфавитный
    порядок загрузки unittest) и мутирует глобальный _STATE модуля — без
    сохранения/восстановления состояния он ломает последующие тесты.
    """

    MAGENTA = "#ff00ff"

    @staticmethod
    def _magenta_ratio(img):
        # tobytes() вместо deprecated getdata() (Pillow 12.3+ warns, 14 removes).
        rgb = img.convert("RGB")
        data = rgb.tobytes()
        total = max(rgb.width * rgb.height, 1)
        hits = sum(
            1
            for i in range(0, len(data), 3)
            if data[i] > 200 and data[i + 2] > 200 and data[i + 1] < 100
        )
        return hits / total

    def test_real_window_rect_and_capture_exclusion(self):
        import tkinter as tk
        from PIL import ImageGrab

        # DPI-awareness как в screen_agent.py:19-27, чтобы координаты совпали
        # с физическими пикселями ImageGrab/pyautogui на масштабированном экране.
        if sys.platform == "win32":
            import ctypes
            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(2)
            except Exception:
                try:
                    ctypes.windll.user32.SetProcessDPIAware()
                except Exception:
                    pass

        root = tk.Tk()
        root.title("agent_window live test")
        root.attributes("-topmost", True)
        root.geometry("240x120+60+60")
        root.configure(bg=self.MAGENTA)
        for _ in range(3):
            root.update()
        root.lift()
        root.update()
        time.sleep(0.3)

        try:
            user32 = aw._user32
            logs = []

            # 1) bind_window: обёртка top-level != внутренний winfo_id
            hwnd = aw.bind_window(root, logs.append)
            self.assertTrue(hwnd, f"HWND не определён, логи: {logs}")
            wrapper = user32.GetParent(root.winfo_id())
            self.assertTrue(wrapper, "GetParent(winfo_id) должен вернуть обёртку top-level")
            self.assertEqual(int(wrapper), hwnd)
            self.assertNotEqual(hwnd, root.winfo_id())

            # 2) GetWindowRect и попадание точки
            rect = aw.tool_window_rect()
            self.assertIsNotNone(rect, "tool_window_rect должен вернуть прямоугольник")
            l, t, r, b = rect
            self.assertLess(l, r)
            self.assertLess(t, b)
            cx, cy = (l + r) // 2, (t + b) // 2
            self.assertTrue(aw.point_in_tool_window(cx, cy),
                            f"центр окна ({cx},{cy}) должен попадать в rect={rect}")
            self.assertFalse(aw.point_in_tool_window(r + 500, cy))
            self.assertFalse(aw.point_in_tool_window(cx, b + 500))

            # 3) захват ДО скрытия: окно видно в снимке
            x, y = root.winfo_rootx(), root.winfo_rooty()
            w, h = root.winfo_width(), root.winfo_height()
            bbox = (x, y, x + w, y + h)
            ratio_before = self._magenta_ratio(ImageGrab.grab(bbox=bbox))
            self.assertGreater(ratio_before, 0.9,
                               f"до скрытия окно должно быть видно в снимке (ratio={ratio_before:.3f})")

            # 4) скрытие из захвата
            status = aw.exclude_from_capture(root, logs.append)
            root.update()
            time.sleep(0.3)
            self.assertIn("✅", status, f"ожидали успех скрытия, статус: {status}")
            self.assertTrue(aw.capture_exclusion_active())

            ratio_after = self._magenta_ratio(ImageGrab.grab(bbox=bbox))
            self.assertLess(ratio_after, 0.05,
                            f"после WDA_EXCLUDEFROMCAPTURE окно не должно попадать в снимок "
                            f"(ratio={ratio_after:.3f})")

            # 5) окно остаётся видимым человеку
            self.assertTrue(user32.IsWindowVisible(hwnd),
                            "окно должно оставаться видимым для пользователя")
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main(verbosity=2)
