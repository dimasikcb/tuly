"""CV-движок автокликера: ленивые зависимости, шаги и поиск по картинке.

Вынесено из `tools.pyw`, чтобы логика кликера жила без GUI-модуля. Здесь нет
tkinter — панель в tools.pyw импортирует эти имена и рисует поверх них.

Главное — ЛЕНИВАЯ загрузка тяжёлого CV-стека (cv2 / numpy / pyautogui / PIL /
keyboard): раньше он импортировался при старте ВСЕГДА и добавлял до 1 с к
открытию окна, даже если нужна вкладка «Лиды» или «Bypass». Поэтому cv2 и
компания импортируются ТОЛЬКО внутри load_autoclicker_deps(), а не на верхнем
уровне модуля — это фича быстрого старта окна, не убирать.
"""

from __future__ import annotations

import ctypes
import sys
from dataclasses import dataclass

# --------------------------------------------------------------------------- #
#  Зависимости автокликера (cv2 / numpy / pyautogui / PIL / keyboard).
#  Раньше импортировались при старте ВСЕГДА и добавляли до 1 с к открытию окна,
#  даже если нужна вкладка «Лиды» или «Bypass». Теперь грузятся лениво:
#  load_autoclicker_deps() вызывается в AutoClickerPanel.__init__.
# --------------------------------------------------------------------------- #
HAS_DEPS = False
DEPS_ERROR = ""
HAS_KEYBOARD = False


def load_autoclicker_deps() -> None:
    """Подгружает CV-стек при первом открытии вкладки AutoClicker."""
    global HAS_DEPS, DEPS_ERROR, HAS_KEYBOARD
    global cv2, np, pyautogui, Image, ImageGrab, keyboard
    if HAS_DEPS or DEPS_ERROR:
        return
    try:
        import cv2
        import numpy as np
        import pyautogui
        from PIL import Image, ImageGrab

        HAS_DEPS = True
        DEPS_ERROR = ""
        pyautogui.PAUSE = 0
        pyautogui.FAILSAFE = True
    except Exception as _dep_error:  # noqa: BLE001
        HAS_DEPS = False
        DEPS_ERROR = str(_dep_error)
    try:
        import keyboard

        HAS_KEYBOARD = True
    except Exception:  # noqa: BLE001
        HAS_KEYBOARD = False


# --------------------------------------------------------------------------- #
#  AutoClicker: шаги, оверлей, поиск по картинке
# --------------------------------------------------------------------------- #
KIND_NAMES = {"point": "Точка", "area": "Область", "image": "Картинка"}
BUTTONS = {"ЛКМ": "left", "ПКМ": "right", "СКМ": "middle"}


@dataclass
class Step:
    kind: str = "point"
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    path: str = ""
    conf: float = 0.80
    delay: int = 0
    wait: float = 0.0


def virtual_screen() -> tuple[int, int, int, int]:
    if sys.platform == "win32":
        user32 = ctypes.windll.user32
        return (
            user32.GetSystemMetrics(76),
            user32.GetSystemMetrics(77),
            user32.GetSystemMetrics(78),
            user32.GetSystemMetrics(79),
        )
    return (0, 0, pyautogui.size().width, pyautogui.size().height)


def grab_screen() -> "Image.Image":
    try:
        return ImageGrab.grab(all_screens=True).convert("RGB")
    except TypeError:
        return ImageGrab.grab().convert("RGB")


def find_template(screen: "np.ndarray", template: "np.ndarray", confidence: float):
    screen_h, screen_w = screen.shape[:2]
    template_h, template_w = template.shape[:2]
    if template_h > screen_h or template_w > screen_w or template_h < 2 or template_w < 2:
        return None
    gray = cv2.cvtColor(template, cv2.COLOR_RGB2GRAY)
    if float(gray.std()) < 6.0:
        result = cv2.matchTemplate(screen, template, cv2.TM_SQDIFF_NORMED)
        min_val, _max_val, min_loc, _max_loc = cv2.minMaxLoc(result)
        score = 1.0 - float(min_val)
        loc = min_loc
    else:
        result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
        _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(result)
        score = float(max_val)
        loc = max_loc
    if score < confidence:
        return None
    return (loc[0] + template_w // 2, loc[1] + template_h // 2, template_w, template_h, score)
