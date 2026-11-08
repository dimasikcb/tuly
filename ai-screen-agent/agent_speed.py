"""
agent_speed.py — ускорение подготовки кадра и сети для AI Screen Bridge.

Создан по итогам анализа скорости от 2026-11-08 (см. план аналитика):
из 5-8 с на шаг автоцикла (screen_agent.py: execute_one_cycle -> _loop_worker)
локальная подготовка занимает лишь ~0.1 с, остальное — вызов vision-API,
при этом объём трафика и число соединений задаёт наша сторона:
  * кадр 2879x1629 (~4.7 Мп) кодируется в JPEG q85 (~342 KB, ~457 KB base64)
    и уходит по НОВОМУ TCP+TLS-соединению на каждый шаг (requests.post
    без Session: screen_agent.py:535 и screen_agent.py:573);
  * модель получает в ~3.5 раза больше vision-пикселей, чем нужно
    (модель отвечает в нормализованных координатах 0-1000 относительно
    картинки, поэтому пересчёт клика в пиксели ROI не меняется).

Функции (чистые, без побочных эффектов; сетевых вызовов нет):
  downscale_for_api(img, max_side=1536) -> PIL.Image
      Уменьшает кадр до max_side по большей стороне (thumbnail + LANCZOS).
      thumbnail уменьшает только вниз: кадр, который и так не больше
      max_side, возвращается КАК ЕСТЬ — мелкие ROI не пострадают.
  encode_jpeg(img, quality=80) -> bytes
      Кодирует PIL-изображение в JPEG в памяти. Замена блока
      screen_agent.py:626-629 (там quality=85).
  get_session() -> requests.Session
      Лениво создаёт и кэширует общий Session с keep-alive и безопасным
      Retry (только обрывы на этапе соединения; read/статус-повторы
      отключены, чтобы не задваивать POST-вызовы API). Замена requests.post
      на session.post() в screen_agent.py:535 и screen_agent.py:573.

Импорт модуля не тянет GUI (tkinter/pyautogui не импортируются) и не
выполняет сетевых операций — подключение и тесты работают офлайн.
Session предполагается однопоточным (используется из worker-потока
_loop_worker), поэтому блокировка нужна только на время создания.
"""

from __future__ import annotations

import io
import threading

import requests
from PIL import Image
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

__all__ = [
    "DEFAULT_MAX_SIDE",
    "DEFAULT_JPEG_QUALITY",
    "downscale_for_api",
    "encode_jpeg",
    "get_session",
]

DEFAULT_MAX_SIDE = 1536
DEFAULT_JPEG_QUALITY = 80

_session_lock = threading.Lock()
_session: "requests.Session | None" = None

# Повторяем только если соединение НЕ установлено (обрыв DNS/TCP/TLS):
# запрос ещё не отправлен, поэтому дублей POST-вызова к API не будет.
# read/status-повторы отключены сознательно: повтор после отправки запроса
# может задвоить платный вызов модели.
_RETRY = Retry(
    total=2,
    connect=2,
    read=0,
    status=0,
    backoff_factor=0.3,
)


def downscale_for_api(img: Image.Image, max_side: int = DEFAULT_MAX_SIDE) -> Image.Image:
    """Уменьшает изображение так, чтобы большая сторона стала <= max_side.

    Пропорции сохраняются. Кадр, который и так не больше max_side,
    возвращается без изменений (без копии и пересэмплирования).
    Исходное изображение-аргумент не мутируется.
    """
    if img is None:
        raise ValueError("downscale_for_api: img is None")
    if max_side <= 0:
        raise ValueError("downscale_for_api: max_side must be positive")
    if max(img.size) <= max_side:
        return img
    small = img.copy()
    small.thumbnail((max_side, max_side), Image.Resampling.LANCZOS)
    return small


def encode_jpeg(img: Image.Image, quality: int = DEFAULT_JPEG_QUALITY) -> bytes:
    """Кодирует PIL-изображение в JPEG и возвращает bytes (в памяти).

    Изображения с альфа-каналом или палитрой (RGBA/LA/P и т.п.) предварительно
    конвертируются в RGB, т.к. JPEG альфу не хранит.
    """
    if img is None:
        raise ValueError("encode_jpeg: img is None")
    if not 1 <= quality <= 95:
        raise ValueError("encode_jpeg: quality must be in 1..95")
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue()


def get_session() -> requests.Session:
    """Возвращает общий requests.Session с keep-alive (пул соединений).

    Session создаётся один раз при первом вызове и переиспользуется:
    устраняет DNS+TCP+TLS-handshake на каждый шаг цикла. Запросов при
    создании не выполняется; прокси/окружение учитываются как у обычного
    requests.post (trust_env не менялся).
    """
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                session = requests.Session()
                adapter = HTTPAdapter(max_retries=_RETRY)
                session.mount("https://", adapter)
                session.mount("http://", adapter)
                _session = session
    return _session
