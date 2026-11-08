"""
agent_progress.py — детект прогресса для AI Screen Bridge.

Самодостаточный модуль БЕЗ GUI и сети: импортирует только PIL
(Pillow уже есть в requirements.txt). Предназначен для интеграции
в screen_agent.py (AIScreenAgentApp.execute_one_cycle / _loop_worker):

1. diff_stats(img_a, img_b) -> (changed, total) и diff_ratio(img_a, img_b) —
   число/доля различающихся пикселей между двумя снимками ROI
   (уменьшенные grayscale-копии шириной SNAPSHOT_WIDTH px).
   Для детекта «экран изменился после клика» используйте АБСОЛЮТНОЕ число
   (diff_stats + MIN_CHANGED_PIXELS): доля ненадёжна, потому что клик по
   радиокнопке/чекбоксу без широкой подсветки строки меняет лишь ~0.016%
   кадра (~6 пикселей снапшота 256x145) и тонет в пороге типа 0.5%.

2. StagnationTracker — счётчик зависания: сколько шагов подряд модель
   кликает одну и ту же точку (в пределах SAME_TARGET_TOLERANCE_PX) и
   сколько шагов подряд экран не меняется. Останавливать авто-режим
   нужно ТОЛЬКО на статусе 'stalled' (комбинация обоих признаков);
   'repeat' — предупреждение, легитимные повторы (кнопка «Далее» на
   одном месте, экран при этом меняется) в stalled не превращаются.
   Сброс состояния — tracker.reset() в start().
"""

from PIL import Image, ImageChops

# --- Пороги модуля ---
SNAPSHOT_WIDTH = 256             # ширина уменьшенной grayscale-копии снимка для сравнения
PIXEL_DIFF_TOLERANCE = 25        # отклонение 0-255: ниже него пиксель «не различается» (шум/сжатие JPEG)
MIN_CHANGED_PIXELS = 4           # абс. порог «экран изменился»: >= 4 пикселей снапшота 256x145 (~0.02%)
STAGNATION_REPEAT_LIMIT = 3      # шагов подряд в одну и ту же точку (участвует в stalled)
STAGNATION_NO_CHANGE_LIMIT = 3   # шагов подряд без изменения экрана (участвует в stalled)
SAME_TARGET_TOLERANCE_PX = 20    # смещение по X и Y, при котором точка считается той же (~1.5% ROI 2879x1629)

# Значения, возвращаемые StagnationTracker.register()
STATUS_OK = "ok"
STATUS_REPEAT = "repeat"
STATUS_STALLED = "stalled"

# Pillow >= 9.1: Image.Resampling.LANCZOS; старые версии: Image.LANCZOS
_RESAMPLE_LANCZOS = getattr(Image, "Resampling", Image).LANCZOS


def _grayscale_copy(img, width):
    """Grayscale-копия снимка, приведённая к фиксированной ширине (пропорция сохраняется)."""
    if width < 1:
        raise ValueError(f"width должен быть >= 1, получено {width}")
    w, h = img.size
    if w <= 0 or h <= 0:
        raise ValueError(f"Некорректный размер изображения: {img.size!r}")
    height = max(1, round(h * width / w))
    return img.convert("L").resize((width, height), _RESAMPLE_LANCZOS)


def diff_stats(img_a, img_b, width=SNAPSHOT_WIDTH, pixel_tolerance=PIXEL_DIFF_TOLERANCE):
    """Число различающихся пикселей между двумя снимками: (changed, total).

    Оба изображения переводятся в grayscale и уменьшаются до `width` px
    по ширине; если после этого размеры всё ещё различаются (разные
    пропорции), вторая копия приводится к размеру первой. Пиксель
    считается различающимся, если его отклонение СТРОГО больше
    `pixel_tolerance` (по умолчанию 25 из 255 — гасит шум JPEG и
    мигающую каретку).

    Абсолютное число (а не доля) — основной сигнал для детекта «экран
    изменился после клика»: отметка радиокнопки меняет лишь несколько
    пикселей снапшота, и в процентах она неотличима от нуля.

    Курсор мыши не учитывается: ImageGrab его не захватывает.
    """
    if img_a is None or img_b is None:
        raise ValueError("diff_stats: оба аргумента должны быть PIL.Image, а не None")
    if pixel_tolerance < 0:
        raise ValueError(f"pixel_tolerance должен быть >= 0, получено {pixel_tolerance}")

    a = _grayscale_copy(img_a, width)
    b = _grayscale_copy(img_b, width)
    if a.size != b.size:
        b = b.resize(a.size, _RESAMPLE_LANCZOS)

    hist = ImageChops.difference(a, b).histogram()  # mode "L" -> 256 значений
    total = a.size[0] * a.size[1]
    differing = sum(hist[pixel_tolerance + 1:])
    return differing, total


def diff_ratio(img_a, img_b, width=SNAPSHOT_WIDTH, pixel_tolerance=PIXEL_DIFF_TOLERANCE):
    """Доля различающихся пикселей между двумя снимками (0.0 .. 1.0)."""
    changed, total = diff_stats(img_a, img_b, width=width, pixel_tolerance=pixel_tolerance)
    return changed / total


class StagnationTracker:
    """Счётчик повторных одинаковых таргетов и шагов без изменения экрана.

    register(point, screen_changed) вызывается один раз за шаг цикла
    (после клика в execute_one_cycle):
      point          — (x, y) в пикселях экрана; None = шага без клика не было;
      screen_changed — False, если diff_ratio текущего и прошлого снимков
                       показал «экран не изменился».

    Возвращает:
      STATUS_OK      'ok'      — прогресс есть;
      STATUS_REPEAT  'repeat'  — модель кликает ту же точку повторно
                                (предупреждение; авто-режим НЕ останавливать);
      STATUS_STALLED 'stalled' — repeat_limit шагов подряд в одну точку И
                                no_change_limit шагов подряд без изменения
                                экрана: зависание, выход из цикла по образцу
                                _loop_worker (screen_agent.py:753).

    Сброс при прогрессе: изменение экрана обнуляет no_change_count,
    уход точки за same_target_tolerance_px — same_target_count.
    """

    def __init__(self, repeat_limit=STAGNATION_REPEAT_LIMIT,
                 no_change_limit=STAGNATION_NO_CHANGE_LIMIT,
                 same_target_tolerance_px=SAME_TARGET_TOLERANCE_PX):
        if repeat_limit < 1 or no_change_limit < 1:
            raise ValueError("Пороги repeat_limit и no_change_limit должны быть >= 1")
        if same_target_tolerance_px < 0:
            raise ValueError("same_target_tolerance_px должен быть >= 0")
        self.repeat_limit = repeat_limit
        self.no_change_limit = no_change_limit
        self.same_target_tolerance_px = same_target_tolerance_px
        self.last_point = None
        self.same_target_count = 0
        self.no_change_count = 0

    def reset(self):
        """Полный сброс состояния (вызывать в start(), screen_agent.py:704)."""
        self.last_point = None
        self.same_target_count = 0
        self.no_change_count = 0

    @staticmethod
    def _is_same_point(prev, cur, tolerance):
        """Та же точка: смещение по КАЖДОЙ оси не больше tolerance (px)."""
        if prev is None or cur is None:
            return False
        try:
            dx = abs(float(cur[0]) - float(prev[0]))
            dy = abs(float(cur[1]) - float(prev[1]))
        except (TypeError, ValueError, IndexError):
            return False
        return dx <= tolerance and dy <= tolerance

    def register(self, point, screen_changed):
        if screen_changed:
            self.no_change_count = 0
        else:
            self.no_change_count += 1

        same = self._is_same_point(self.last_point, point, self.same_target_tolerance_px)
        if same:
            self.same_target_count += 1
        else:
            self.same_target_count = 1 if point is not None else 0
        self.last_point = point

        if (self.same_target_count >= self.repeat_limit
                and self.no_change_count >= self.no_change_limit):
            return STATUS_STALLED
        if same:
            return STATUS_REPEAT
        return STATUS_OK
