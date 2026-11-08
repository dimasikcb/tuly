"""
Офлайн-тесты модуля agent_speed.py (AI Screen Bridge).

Проверяется:
  * downscale_for_api уменьшает большую картинку до max_side (1536),
    сохраняя пропорции, и НЕ трогает маленькие кадры;
  * encode_jpeg возвращает непустые байты корректного JPEG;
  * кадр после даунскейла весит заметно меньше (смысл оптимизации);
  * get_session() кэшируется, монтирует адаптеры с Retry и не выполняет
    сетевых операций;
  * импорт agent_speed не тянет GUI (tkinter/pyautogui) и не лезет в сеть.

Запуск (без GUI и сети):  python -B test_agent_speed.py -v
"""

import io
import os
import subprocess
import sys
import textwrap
import time
import unittest
from unittest import mock

import requests
from PIL import Image, ImageDraw

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import agent_speed
from agent_speed import downscale_for_api, encode_jpeg, get_session


def _make_test_frame(w=2880, h=1800):
    """Детальная градиентная картинка — имитация скриншота ROI."""
    img = Image.new("RGB", (w, h))
    d = ImageDraw.Draw(img)
    for y in range(h):
        d.line([(0, y), (w, y)], fill=(y % 256, (y * 2) % 256, (y * 3) % 256))
    for x in range(0, w, 60):
        d.line([(x, 0), (x, h)], fill=(255, 255, 0), width=2)
    return img


class TestDownscaleForApi(unittest.TestCase):
    def test_big_image_is_reduced(self):
        """Большой кадр уменьшается до max_side=1536 с сохранением пропорций."""
        frame = _make_test_frame(2880, 1800)
        small = downscale_for_api(frame)  # max_side по умолчанию
        self.assertEqual(small.size, (1536, 960))
        self.assertLessEqual(max(small.size), agent_speed.DEFAULT_MAX_SIDE)
        # Исходник не мутирован
        self.assertEqual(frame.size, (2880, 1800))

    def test_small_image_untouched(self):
        """Мелкий ROI не пересэмплируется (thumbnail уменьшает только вниз)."""
        small_roi = _make_test_frame(800, 600)
        result = downscale_for_api(small_roi)
        self.assertIs(result, small_roi)
        self.assertEqual(result.size, (800, 600))

    def test_square_image(self):
        frame = _make_test_frame(2000, 2000)
        self.assertEqual(downscale_for_api(frame).size, (1536, 1536))

    def test_custom_max_side(self):
        frame = _make_test_frame(1000, 500)
        self.assertEqual(downscale_for_api(frame, max_side=400).size, (400, 200))

    def test_none_raises(self):
        with self.assertRaises(ValueError):
            downscale_for_api(None)

    def test_invalid_max_side_raises(self):
        frame = _make_test_frame(100, 100)
        with self.assertRaises(ValueError):
            downscale_for_api(frame, max_side=0)


class TestEncodeJpeg(unittest.TestCase):
    def test_returns_nonempty_bytes(self):
        """Главное требование: encode_jpeg возвращает непустые байты."""
        data = encode_jpeg(_make_test_frame(640, 480))
        self.assertIsInstance(data, bytes)
        self.assertGreater(len(data), 0)

    def test_jpeg_magic_bytes(self):
        data = encode_jpeg(_make_test_frame(320, 240))
        self.assertEqual(data[:2], b"\xff\xd8")  # SOI маркер JPEG
        self.assertTrue(data.startswith(b"\xff\xd8\xff"))

    def test_roundtrip_size(self):
        frame = _make_test_frame(640, 400)
        back = Image.open(io.BytesIO(encode_jpeg(frame)))
        self.assertEqual(back.size, (640, 400))
        self.assertEqual(back.format, "JPEG")

    def test_default_quality_is_80(self):
        frame = _make_test_frame(800, 600)
        self.assertEqual(encode_jpeg(frame), encode_jpeg(frame, quality=80))
        self.assertEqual(agent_speed.DEFAULT_JPEG_QUALITY, 80)

    def test_rgba_is_converted(self):
        rgba = Image.new("RGBA", (300, 200), (10, 20, 30, 128))
        data = encode_jpeg(rgba)
        self.assertGreater(len(data), 0)
        back = Image.open(io.BytesIO(data))
        self.assertEqual(back.mode, "RGB")

    def test_invalid_quality_raises(self):
        frame = _make_test_frame(100, 100)
        for bad in (0, 96, -1):
            with self.assertRaises(ValueError):
                encode_jpeg(frame, quality=bad)


class TestSpeedupRationale(unittest.TestCase):
    """Смысл оптимизации: даунскейл+q80 даёт кратный выигрыш payload."""

    def test_downscaled_payload_is_smaller(self):
        frame = _make_test_frame(2879, 1629)  # реальный ROI из прогона 2026-11-08
        old_bytes = encode_jpeg(frame, quality=85)  # как в screen_agent.py:628
        new_bytes = encode_jpeg(downscale_for_api(frame), quality=80)
        self.assertLess(len(new_bytes), len(old_bytes) / 2)
        print(
            f"\n[i] payload JPEG: старый q85 {len(old_bytes) / 1024:.0f} KB -> "
            f"новый q80+1536 {len(new_bytes) / 1024:.0f} KB "
            f"(x{len(old_bytes) / len(new_bytes):.1f})"
        )

    def test_local_prep_is_fast(self):
        """Информационно: локальная подготовка ~0.1 с, без жёсткого порога."""
        frame = _make_test_frame(2879, 1629)
        t0 = time.perf_counter()
        _ = encode_jpeg(downscale_for_api(frame))
        prep = time.perf_counter() - t0
        print(f"[i] downscale+encode на кадре 2879x1629: {prep * 1000:.0f} мс")


class TestGetSession(unittest.TestCase):
    def test_returns_session(self):
        self.assertIsInstance(get_session(), requests.Session)

    def test_cached_same_object(self):
        self.assertIs(get_session(), get_session())

    def test_retry_adapter_mounted(self):
        s = get_session()
        for url in ("https://api.apinex.bond/v1/chat/completions",
                    "https://openrouter.ai/api/v1/chat/completions",
                    "http://localhost/v1"):
            adapter = s.get_adapter(url)
            self.assertIsNotNone(adapter, url)
            # Только connect-повторы: read/status выключены, чтобы не задваивать POST
            self.assertEqual(adapter.max_retries.total, 2)
            self.assertEqual(adapter.max_retries.connect, 2)
            self.assertEqual(adapter.max_retries.read, 0)
            self.assertEqual(adapter.max_retries.status, 0)

    def test_no_network_on_call(self):
        """get_session() не выполняет сетевых операций."""
        with mock.patch("socket.create_connection", side_effect=AssertionError("network!")):
            s = get_session()
        self.assertIsInstance(s, requests.Session)


class TestImportPurity(unittest.TestCase):
    """Импорт модуля не требует GUI и сети (требование задачи)."""

    def test_no_gui_imports_in_this_process(self):
        self.assertNotIn("tkinter", sys.modules)
        self.assertNotIn("pyautogui", sys.modules)

    def test_isolated_subprocess_import(self):
        """В изолированном процессе (-I) с заблокированными сокетами импорт
        agent_speed проходит, GUI не подтягивается, get_session() работает."""
        code = textwrap.dedent(
            """
            import socket, sys

            def _blocked(*args, **kwargs):
                raise AssertionError("network access attempted")

            socket.create_connection = _blocked
            socket.socket.connect = _blocked

            sys.path.insert(0, sys.argv[1])
            import agent_speed

            assert "tkinter" not in sys.modules, "tkinter imported"
            assert "pyautogui" not in sys.modules, "pyautogui imported"

            s = agent_speed.get_session()
            assert s is agent_speed.get_session()
            print("OK")
            """
        )
        proc = subprocess.run(
            [sys.executable, "-I", "-B", "-c", code, _HERE],
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(
            proc.returncode, 0,
            f"stderr:\n{proc.stderr}\nstdout:\n{proc.stdout}",
        )
        self.assertIn("OK", proc.stdout)


if __name__ == "__main__":
    unittest.main(verbosity=2)
