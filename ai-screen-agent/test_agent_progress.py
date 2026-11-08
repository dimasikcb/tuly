"""Тесты agent_progress.py — полностью офлайн: GUI и сеть не используются.

Запуск:
    python test_agent_progress.py -v
или
    python -m unittest discover -s <папка модуля> -p "test_agent_progress.py" -v

PIL-картинки строятся прямо в тестах (Image.new / point / ImageDraw не нужны).
"""

import os
import subprocess
import sys
import unittest

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import agent_progress
from agent_progress import STATUS_OK, STATUS_REPEAT, STATUS_STALLED, StagnationTracker, diff_ratio

MODULE_DIR = os.path.dirname(os.path.abspath(__file__))


def solid(size, color, mode="L"):
    """Однотонная картинка."""
    return Image.new(mode, size, color)


class DiffRatioTests(unittest.TestCase):
    def test_identical_images_zero(self):
        img = solid((320, 200), 128)
        self.assertEqual(diff_ratio(img, img.copy()), 0.0)

    def test_black_vs_white_one(self):
        self.assertEqual(diff_ratio(solid((320, 200), 0), solid((320, 200), 255)), 1.0)

    def test_half_changed_half(self):
        a = solid((256, 256), 0)
        b = a.copy()
        for x in range(0, 128):          # левая половина стала белой
            for y in range(0, 256):
                b.putpixel((x, y), 255)
        self.assertAlmostEqual(diff_ratio(a, b), 0.5)

    def test_noise_below_tolerance_ignored(self):
        a = solid((300, 150), 100)
        b = a.point(lambda p: min(255, p + 10))  # отклонение 10 <= 25 — шум
        self.assertEqual(diff_ratio(a, b), 0.0)

    def test_noise_above_tolerance_counted(self):
        a = solid((300, 150), 100)
        b = a.point(lambda p: min(255, p + 40))  # отклонение 40 > 25 — изменение
        self.assertEqual(diff_ratio(a, b), 1.0)

    def test_different_sizes_and_aspect(self):
        # Реальный кейс: 2880x1800 против мелкого снимка — не падает, даёт 0..1
        a = solid((2880, 1800), 0)
        b = solid((100, 80), 255)
        r = diff_ratio(a, b)
        self.assertIsInstance(r, float)
        self.assertAlmostEqual(r, 1.0)

    def test_rgb_vs_l_modes(self):
        a = Image.new("RGB", (200, 100), (255, 0, 0))
        b = a.convert("L")               # тот же цвет в grayscale
        self.assertEqual(diff_ratio(a, b), 0.0)

    def test_width_parameter(self):
        a = solid((256, 256), 0)
        b = a.copy()
        for x in range(0, 128):
            for y in range(0, 256):
                b.putpixel((x, y), 255)
        self.assertAlmostEqual(diff_ratio(a, b, width=64), 0.5)

    def test_result_is_float_in_range(self):
        a = solid((640, 400), 30)
        b = solid((640, 400), 200)
        for pair in ((a, a), (a, b), (b, a)):
            r = diff_ratio(*pair)
            self.assertIsInstance(r, float)
            self.assertGreaterEqual(r, 0.0)
            self.assertLessEqual(r, 1.0)

    def test_none_raises(self):
        img = solid((100, 100), 0)
        with self.assertRaises(ValueError):
            diff_ratio(None, img)
        with self.assertRaises(ValueError):
            diff_ratio(img, None)

    def test_defaults_from_module_constants(self):
        self.assertEqual(agent_progress.SNAPSHOT_WIDTH, 256)
        self.assertEqual(agent_progress.PIXEL_DIFF_TOLERANCE, 25)


class StagnationTrackerTests(unittest.TestCase):
    def setUp(self):
        self.tracker = StagnationTracker()  # пороги по умолчанию: 3 / 3 / 20 px

    def test_first_step_ok(self):
        self.assertEqual(self.tracker.register((1600, 979), False), STATUS_OK)
        self.assertEqual(self.tracker.same_target_count, 1)
        self.assertEqual(self.tracker.no_change_count, 1)

    def test_incident_scenario_six_same_clicks_frozen_screen(self):
        # Симптом из живого прогона: 6 шагов в одну точку, экран не меняется
        statuses = [self.tracker.register((1600, 979), False) for _ in range(6)]
        self.assertEqual(statuses, ["ok", "repeat", "stalled", "stalled", "stalled", "stalled"])
        self.assertEqual(statuses.index("stalled"), 2)  # стоп на 3-м шаге, а не на 6-м

    def test_screen_change_prevents_stall(self):
        # Легитимный случай: кнопка «Далее» в одном месте, экран меняется
        statuses = [self.tracker.register((1600, 979), True) for _ in range(6)]
        self.assertNotIn("stalled", statuses)
        self.assertEqual(statuses[0], "ok")     # первый шаг — ещё не повтор
        self.assertEqual(statuses[2], "repeat") # дальше — предупреждение о повторе

    def test_screen_change_resets_no_change_counter(self):
        self.tracker.register((1600, 979), False)   # nc=1
        self.tracker.register((1600, 979), False)   # nc=2, repeat
        self.assertEqual(self.tracker.register((1600, 979), True), STATUS_REPEAT)  # nc=0
        self.assertEqual(self.tracker.no_change_count, 0)
        self.assertNotEqual(self.tracker.register((1600, 979), False), STATUS_STALLED)
        self.assertNotEqual(self.tracker.register((1600, 979), False), STATUS_STALLED)
        self.assertEqual(self.tracker.register((1600, 979), False), STATUS_STALLED)  # nc снова 3

    def test_new_target_resets_repeat_counter(self):
        self.tracker.register((100, 100), False)
        self.assertEqual(self.tracker.register((100, 100), False), STATUS_REPEAT)
        # Далёкая точка: счётчик повторов сбрасывается в 1
        self.assertEqual(self.tracker.register((500, 500), False), STATUS_OK)
        self.assertEqual(self.tracker.same_target_count, 1)
        self.assertEqual(self.tracker.register((500, 500), False), STATUS_REPEAT)
        self.assertEqual(self.tracker.register((500, 500), False), STATUS_STALLED)

    def test_tolerance_boundaries(self):
        self.assertEqual(self.tracker.register((100, 100), False), STATUS_OK)
        # Ровно на границе (20 px по оси) — та же точка
        self.assertEqual(self.tracker.register((120, 100), False), STATUS_REPEAT)
        # 21 px по Y — уже другая точка, даже если X в допуске
        self.assertEqual(self.tracker.register((120, 121), False), STATUS_OK)
        self.assertEqual(self.tracker.same_target_count, 1)

    def test_custom_limits(self):
        t = StagnationTracker(repeat_limit=2, no_change_limit=2)
        self.assertEqual(t.register((1, 1), False), STATUS_OK)
        self.assertEqual(t.register((1, 1), False), STATUS_STALLED)

    def test_custom_tolerance(self):
        t = StagnationTracker(same_target_tolerance_px=5)
        self.assertEqual(t.register((100, 100), False), STATUS_OK)
        self.assertEqual(t.register((104, 104), False), STATUS_REPEAT)  # dx=dy=4 <= 5
        self.assertEqual(t.register((110, 110), False), STATUS_OK)      # dx=dy=10 > 5

    def test_reset_clears_state(self):
        self.tracker.register((1600, 979), False)
        self.tracker.register((1600, 979), False)
        self.tracker.reset()
        self.assertIsNone(self.tracker.last_point)
        self.assertEqual(self.tracker.same_target_count, 0)
        self.assertEqual(self.tracker.no_change_count, 0)
        self.assertEqual(self.tracker.register((1600, 979), False), STATUS_OK)

    def test_none_point_is_safe(self):
        self.assertEqual(self.tracker.register(None, False), STATUS_OK)
        self.assertIsNone(self.tracker.last_point)
        self.assertEqual(self.tracker.register((5, 5), False), STATUS_OK)

    def test_invalid_constructor_args(self):
        with self.assertRaises(ValueError):
            StagnationTracker(repeat_limit=0)
        with self.assertRaises(ValueError):
            StagnationTracker(no_change_limit=0)
        with self.assertRaises(ValueError):
            StagnationTracker(same_target_tolerance_px=-1)

    def test_thresholds_default_to_module_constants(self):
        self.assertEqual(self.tracker.repeat_limit, agent_progress.STAGNATION_REPEAT_LIMIT)
        self.assertEqual(self.tracker.no_change_limit, agent_progress.STAGNATION_NO_CHANGE_LIMIT)
        self.assertEqual(self.tracker.same_target_tolerance_px, agent_progress.SAME_TARGET_TOLERANCE_PX)

    def test_status_constants_match_ask(self):
        self.assertEqual((STATUS_OK, STATUS_REPEAT, STATUS_STALLED), ("ok", "repeat", "stalled"))


class ImportPurityTests(unittest.TestCase):
    def test_import_does_not_pull_gui_or_network(self):
        # Свежий интерпретатор в изолированном режиме (-I): импорт модуля
        # не должен тянуть tkinter/pyautogui/requests/mss (GUI и сеть).
        code = (
            "import sys;"
            "sys.path.insert(0, sys.argv[1]);"
            "import agent_progress;"
            "banned = {'tkinter', 'requests', 'pyautogui', 'mss', 'socket', 'urllib'};"
            "loaded = sorted(banned & set(sys.modules));"
            "assert not loaded, 'imported banned modules: %s' % loaded;"
            "r = agent_progress.diff_ratio;"
            "print('clean')"
        )
        proc = subprocess.run(
            [sys.executable, "-I", "-c", code, MODULE_DIR],
            capture_output=True, text=True, timeout=120,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("clean", proc.stdout)


if __name__ == "__main__":
    unittest.main()
