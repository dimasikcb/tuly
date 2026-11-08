"""
agent_window.py — служебный модуль AI Screen Bridge для работы с СОБСТВЕННЫМ окном.

Зачем: ImageGrab.grab снимает всё видимое в ROI, включая тёмное окно самой
программы, и модель видит "active tool window" вместо вариантов ответа; клики
без проверки могут уходить в само окно, экран не меняется и автоцикл повторяет
одну и ту же точку. Модуль даёт два независимых средства:

1. exclude_from_capture(tk_root, log_fn) — исключает окно из захвата экрана
   через Win32 SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE = 0x11).
   Работает и для ImageGrab.grab (screen_agent.py:421), и для mss-fallback
   (screen_agent.py:426-430), а также убирает окно из OBS/шаринга экрана.
   Требуется Windows 10 2004 (build 19041) или новее.

2. tool_window_rect() / point_in_tool_window(x, y) — прямоугольник окна через
   GetWindowRect и проверка попадания точки, чтобы execute_one_cycle
   (screen_agent.py:612-683) пропускал клик, когда модель указала в наше окно.

Гарантии:
- только стандартная библиотека (sys, ctypes); новых зависимостей нет;
- импорт модуля НЕ создаёт окон, НЕ требует GUI и НЕ трогает сеть
  (tkinter не импортируется; tk_root используется утиной типизацией —
  достаточно метода winfo_id());
- на не-Windows все функции возвращают нейтральный результат;
- публичные функции не бросают исключений (всё в try/except);
- потокобезопасность: GetWindowRect допустимо вызывать из worker-потока
  (_loop_worker / execute_one_cycle); exclude_from_capture вызывать ОДИН раз
  на UI-потоке после создания окна (например, в AIScreenAgentApp.__init__
  сразу после self.setup_ui() — screen_agent.py:142, до mainloop).
  Аффинити сохраняется при withdraw/deiconify (screen_agent.py:385,393)
  и переключении topmost (screen_agent.py:373-374).

Интеграция (для Сборщика; сам screen_agent.py модуль не правит):
- в __init__ после setup_ui:      agent_window.exclude_from_capture(self.root, self.log)
  и по returned-статусу / capture_exclusion_active() выбирать fallback
  «прятать на время захвата» (root.withdraw() -> захват -> root.deiconify()).
  WDA_MONITOR=1 как fallback НЕ использовать — вместо окна в кадре будет
  чёрный прямоугольник, варианты всё равно перекрыты.
- в execute_one_cycle между клампом координат (screen_agent.py:666-667) и
  pyautogui.moveTo/click (screen_agent.py:675-682):
      if agent_window.point_in_tool_window(screen_x, screen_y):
          self.log(f"⚠️ Точка ({screen_x},{screen_y}) попадает в окно программы "
                   f"{agent_window.tool_window_rect()} — шаг пропущен")
          return False
- координаты: модуль ничего не меняет в DPI-awareness процесса. screen_agent
  включает PerMonitorV2 (screen_agent.py:19-27), поэтому GetWindowRect
  возвращает физические пиксели — те же, что используют pyautogui и ROI.
"""

import sys
import ctypes

__all__ = [
    "WDA_NONE",
    "WDA_MONITOR",
    "WDA_EXCLUDEFROMCAPTURE",
    "exclude_from_capture",
    "bind_window",
    "capture_exclusion_active",
    "tool_window_rect",
    "point_in_tool_window",
]

IS_WINDOWS = sys.platform == "win32"

# Режимы SetWindowDisplayAffinity
WDA_NONE = 0x00000000
WDA_MONITOR = 0x00000001              # не использовать как fallback: чёрный прямоугольник
WDA_EXCLUDEFROMCAPTURE = 0x00000011   # Windows 10 2004 (build 19041)+

# Состояние: HWND обёртки top-level Tk и признак успешно включённого скрытия.
# hwnd нужен независимо от скрытия — детект самоклика работает и без WDA.
_STATE = {"hwnd": None, "exclusion_active": False}

# Короткие подсказки к GetLastError в человекочитаемом статусе.
_ERROR_HINTS = {
    50: "нужен Windows 10 2004 (build 19041)+",
    5: "нет доступа к окну (другой процесс/повышенные права)",
    1400: "неверный дескриптор окна",
}

if IS_WINDOWS:
    try:
        import ctypes.wintypes as wintypes

        _user32 = ctypes.WinDLL("user32", use_last_error=True)
        # winfo_id() у Tk-корня — это внутреннее дочернее окно ("TkChild"),
        # настоящему top-level ("TkTopLevel", с заголовком) соответствует
        # GetParent(inner). Аффинити ставим именно на обёртку — она покрывает
        # всё дерево Tk.
        _user32.GetParent.argtypes = [wintypes.HWND]
        _user32.GetParent.restype = wintypes.HWND
        _user32.SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
        _user32.SetWindowDisplayAffinity.restype = wintypes.BOOL
        _user32.GetWindowDisplayAffinity.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
        _user32.GetWindowDisplayAffinity.restype = wintypes.BOOL
        _user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
        _user32.GetWindowRect.restype = wintypes.BOOL
    except Exception:  # pragma: no cover — экзотические среды без user32
        _user32 = None
else:
    _user32 = None


def _log_safe(log_fn, message):
    """Вызывает log_fn(message), глотая любые ошибки колбэка."""
    if callable(log_fn):
        try:
            log_fn(message)
        except Exception:
            pass


def _resolve_hwnd(tk_root):
    """HWND обёртки top-level (GetParent от winfo_id) или None.

    Если обёртки нет (GetParent вернул 0), возвращаем сам winfo_id.
    """
    if not IS_WINDOWS or _user32 is None:
        return None
    try:
        inner = int(tk_root.winfo_id())
    except Exception:
        return None
    if not inner:
        return None
    try:
        wrapper = _user32.GetParent(inner)
    except Exception:
        wrapper = None
    return int(wrapper) if wrapper else inner


def bind_window(tk_root, log_fn=None):
    """Запоминает HWND окна инструмента для tool_window_rect()/point_in_tool_window().

    Нужен, когда скрытие из скриншотов отключено (чекбокс/конфиг), а детект
    самоклика всё равно должен работать. exclude_from_capture() вызывает это
    сам. Возвращает HWND (int) или None; не бросает исключений.
    """
    try:
        hwnd = _resolve_hwnd(tk_root)
        if hwnd:
            _STATE["hwnd"] = hwnd
            _log_safe(log_fn, f"Окно инструмента привязано: hwnd=0x{hwnd:X}")
            return hwnd
        _log_safe(log_fn, "Не удалось определить HWND окна (winfo_id/GetParent).")
        return None
    except Exception as e:
        _log_safe(log_fn, f"Ошибка привязки окна: {e}")
        return None


def exclude_from_capture(tk_root, log_fn=None):
    """Исключает окно tk_root из захвата экрана (WDA_EXCLUDEFROMCAPTURE).

    Вызывать ОДИН раз на UI-потоке после создания окна (после setup_ui, до
    mainloop); повторный вызов безвреден. После успеха окно пропадает из
    ImageGrab/mss/записи экрана, но остаётся видимым пользователю.

    Возвращает человекочитаемый статус (строка; тот же текст уходит в log_fn).
    Исключений не бросает.
    """
    try:
        if not IS_WINDOWS:
            status = "ℹ️ Скрытие окна из скриншотов доступно только на Windows."
            _log_safe(log_fn, status)
            return status

        if _user32 is None:
            status = "⚠️ user32 недоступен — скрытие окна из скриншотов невозможно."
            _log_safe(log_fn, status)
            return status

        hwnd = _resolve_hwnd(tk_root)
        if not hwnd:
            status = "⚠️ Не удалось определить HWND окна — скрытие из скриншотов не применено."
            _log_safe(log_fn, status)
            return status

        # Биндим HWND в любом случае: детект самоклика не зависит от скрытия.
        _STATE["hwnd"] = hwnd

        if not _user32.SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE):
            err = ctypes.get_last_error()
            hint = _ERROR_HINTS.get(err, "")
            suffix = f" ({hint})" if hint else ""
            status = (
                f"⚠️ Скрытие окна из скриншотов не удалось (GetLastError={err}){suffix} "
                f"— окно будет попадать в скриншоты."
            )
            _log_safe(log_fn, status)
            return status

        # Читаем режим назад, чтобы подтвердить, что он реально установлен.
        affinity = wintypes.DWORD(0)
        if _user32.GetWindowDisplayAffinity(hwnd, ctypes.byref(affinity)) and \
                affinity.value == WDA_EXCLUDEFROMCAPTURE:
            _STATE["exclusion_active"] = True
            status = f"✅ Окно скрыто из скриншотов (WDA_EXCLUDEFROMCAPTURE, hwnd=0x{hwnd:X})."
        else:
            status = (
                f"⚠️ SetWindowDisplayAffinity вернул успех, но подтверждение не прошло "
                f"(affinity=0x{affinity.value:X}) — окно может попадать в скриншоты."
            )
        _log_safe(log_fn, status)
        return status
    except Exception as e:
        status = f"⚠️ Ошибка скрытия окна из скриншотов: {e}"
        _log_safe(log_fn, status)
        return status


def capture_exclusion_active():
    """True, если скрытие окна из скриншотов успешно включено.

    Для выбора fallback: False -> режим «прятать окно на время захвата»
    (withdraw перед ImageGrab.grab, deiconify после).
    """
    try:
        return bool(_STATE.get("exclusion_active"))
    except Exception:
        return False


def tool_window_rect():
    """(left, top, right, bottom) окна инструмента через GetWindowRect или None.

    None, если окно не привязано (bind_window/exclude_from_capture не звались),
    платформа не Windows или окно уже уничтожено. Безопасно из worker-потока.
    """
    try:
        if not IS_WINDOWS or _user32 is None:
            return None
        hwnd = _STATE.get("hwnd")
        if not hwnd:
            return None
        return _get_window_rect(hwnd)
    except Exception:
        return None


def _get_window_rect(hwnd):
    """GetWindowRect -> кортеж (l, t, r, b); None при неудаче."""
    try:
        rect = wintypes.RECT()
        if not _user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return None
        return (int(rect.left), int(rect.top), int(rect.right), int(rect.bottom))
    except Exception:
        return None


def point_in_tool_window(x, y):
    """True, если точка (x, y) в экранных пикселях попадает в окно инструмента.

    Границы: левая/верхняя включены, правая/нижняя исключены — как у range().
    При отсутствии данных (окно не привязано, не Windows) возвращает False,
    чтобы не блокировать клики по ошибке.
    """
    try:
        rect = tool_window_rect()
        if not rect:
            return False
        left, top, right, bottom = rect
        return left <= x < right and top <= y < bottom
    except Exception:
        return False
