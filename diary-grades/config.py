"""Пути, настройки и определение текущего учебного периода. Без GUI и сети."""
from __future__ import annotations

import json
import os
import tempfile
from datetime import date
from pathlib import Path

from models import Period, clean_name

APP_NAME = "Дневник МО · Контроль балла"
APP_ID = "diary-grades"
APP_DIR = Path(__file__).resolve().parent
DATA_DIR = APP_DIR / "data"
DEBUG_DIR = APP_DIR / "debug"
SESSION_FILE = DATA_DIR / "storage_state.json"
CACHE_FILE = DATA_DIR / "grades_cache.json"
SETTINGS_FILE = DATA_DIR / "settings.json"
SELECTORS_FILE = APP_DIR / "selectors.json"
SUBJECTS_FILE = APP_DIR / "real_grades.json"

START_URL = "https://authedu.mosreg.ru"
# Корень портала — всегда лендинг «Войти», даже с сохранённой сессией. Оценки
# живут на отдельной странице; именно её открываем при скрытом обновлении.
MARKS_PAGE_URL = "https://authedu.mosreg.ru/diary/marks/"
MARKS_REFERER = "https://authedu.mosreg.ru/diary/marks/current-marks"
MARKS_API_PATH = "/api/family/web/v1/marks"
API_ROOT = "https://authedu.mosreg.ru/api/family/web/v1"
LOGIN_HOST_HINTS = ("esia.gosuslugi.ru", "login", "auth", "passport")

DEFAULTS: dict = {
    # Реальный дневник МО показывает «1 триместр 01 сент – 15 ноя» — по умолчанию
    # считаем триместры, а не четверти. Для четвертей выстави "quarters" в
    # data/settings.json или в окне «Настройки».
    "period_scheme": "trimesters",  # quarters | trimesters
    "headless": True,              # скрытый браузер после первого входа
    "login_timeout_s": 300,        # ожидание ручного входа через ЕСИА
    "nav_timeout_s": 35,
    "subject_scan_timeout_s": 30,    # обход предметов, у которых нет оценок
    "auto_refresh_min": 5,           # скрытое автообновление, 0 = выключить
    "demo_on_empty": True,         # показывать демо, если нет ни сессии, ни кэша
    "save_debug": True,            # дамп HTML/JSON при неудачном парсинге
}

# (месяц, день) границы четвертей. Учебный год считаем с 1 сентября.
QUARTERS = (
    (1, "1 четверть", (9, 1), (10, 31)),
    (2, "2 четверть", (11, 1), (12, 30)),
    (3, "3 четверть", (1, 10), (3, 25)),
    (4, "4 четверть", (4, 1), (5, 31)),
)

# Границы триместров взяты из реального дневника: «1 триместр 01 сент – 15 ноя».
# 2-й и 3-й — по типовому календарю МО (каникулы с 16 ноября и с 16 февраля).
# Если у школы другие даты — поправь таблицу здесь, код менять не нужно.
# ВАЖНО: период 2 пересекает Новый год, поэтому сравнение (месяц, день) делается
# через _in_period(), а не простым start <= md <= end.
TRIMESTERS = (
    (1, "1 триместр", (9, 1), (11, 15)),
    (2, "2 триместр", (11, 16), (2, 15)),
    (3, "3 триместр", (2, 16), (5, 31)),
)


def ensure_dirs() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)


def load_settings() -> dict:
    ensure_dirs()
    data = dict(DEFAULTS)
    if SETTINGS_FILE.exists():
        try:
            data.update(json.loads(SETTINGS_FILE.read_text(encoding="utf-8")) or {})
        except (OSError, ValueError):
            pass
    return data


def save_settings(patch: dict) -> dict:
    """Мержит настройки и пишет атомарно (tmp + replace)."""
    data = load_settings()
    data.update(patch)
    atomic_write_json(SETTINGS_FILE, data)
    return data


def load_selectors() -> dict:
    try:
        return json.loads(SELECTORS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def expected_subjects() -> tuple[str, ...]:
    """Все предметы из шаблона real_grades.json в порядке файла.

    Сборщик использует список, чтобы обойти КАЖДЫЙ предмет, а не только тот,
    что оказался на первой странице, и чтобы после обновления в кэше не
    осталась одна строка. Файла нет / он повреждён — возвращаем пустой кортеж,
    это не ошибка: тогда работаем только по данным портала.
    """
    try:
        raw = json.loads(SUBJECTS_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return ()
    if not isinstance(raw, dict):
        return ()
    out: list[str] = []
    seen: set[str] = set()
    for item in raw.get("subjects") or []:
        if not isinstance(item, dict):
            continue
        name = clean_name(str(item.get("name") or ""))
        key = name.casefold()
        if name and key not in seen:
            seen.add(key)
            out.append(name)
    return tuple(out)


def atomic_write_json(path: Path, payload) -> None:
    """Атомарная запись JSON. На POSIX выставляет права 0600 для сессии."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        if os.name == "posix":
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def session_exists() -> bool:
    try:
        payload = json.loads(SESSION_FILE.read_text(encoding="utf-8"))
        return bool(payload.get("cookies")) or bool(payload.get("origins"))
    except (OSError, ValueError, AttributeError):
        return False


def _aliases(index: int, kind: str) -> tuple[str, ...]:
    roman = {1: "I", 2: "II", 3: "III", 4: "IV"}.get(index, str(index))
    return (
        f"{index} {kind}",
        f"{kind} {index}",
        f"{roman} {kind}",
        f"{index}-й {kind}",
        f"{index}-{kind[0]}",
    )


def _year_key(md: tuple[int, int]) -> tuple[int, int]:
    """Номер месяца внутри учебного года (сентябрь = 0) и день.

    Без этого прямое сравнение кортежей (месяц, день) ломается на периодах,
    пересекающих Новый год: (11, 16) <= (1, 10) <= (2, 15) ложно, хотя
    10 января — самый разгар второго триместра.
    """
    return ((md[0] - 9) % 12, md[1])


def current_period(today: date | None = None, scheme: str | None = None) -> Period:
    """Возвращает текущий период по календарю; летом — последний завершённый."""
    today = today or date.today()
    scheme = scheme or load_settings().get("period_scheme", "trimesters")
    table = TRIMESTERS if scheme == "trimesters" else QUARTERS
    kind = "триместр" if scheme == "trimesters" else "четверть"
    md = _year_key((today.month, today.day))

    for index, label, start, end in table:
        if _year_key(start) <= md <= _year_key(end):
            return Period(label, index, _aliases(index, kind), scheme)

    # Каникулы (лето, новогодние, мартовские): берём ближайший завершившийся период.
    past = [row for row in table if _year_key(row[3]) < md]
    index, label, _start, _end = past[-1] if past else table[-1]
    return Period(label, index, _aliases(index, kind), scheme)


def period_date_range(period: Period,
                      today: date | None = None) -> tuple[date, date]:
    """Календарные даты периода для ``/api/family/web/v1/marks?from=...&to=...``.

    Учебный год начинается 1 сентября: месяцы 9..12 относятся к году ``Y``,
    месяцы 1..8 — к ``Y+1``. Это корректно и для периодов через Новый год,
    и для летнего показа последнего завершённого периода.
    """
    today = today or date.today()
    table = TRIMESTERS if period.scheme == "trimesters" else QUARTERS
    row = next((r for r in table if r[0] == period.index), table[0])
    (start_month, start_day), (end_month, end_day) = row[2], row[3]
    year0 = today.year if today.month >= 9 else today.year - 1
    start_year = year0 if start_month >= 9 else year0 + 1
    end_year = year0 if end_month >= 9 else year0 + 1
    return date(start_year, start_month, start_day), date(end_year, end_month, end_day)


def period_aliases(period: Period) -> tuple[str, ...]:
    return tuple(dict.fromkeys((period.label,) + period.aliases))


def startup_plan(start_demo: bool, has_cache: bool, has_session: bool,
                 auto_login: bool = True) -> tuple[str, bool]:
    """Что делать при старте окна: ``(режим, открывать ли вход ЕСИА)``.

    Режимы: ``demo`` — показать демо-набор, ``cache`` — сохранённые оценки,
    ``fetch`` — сессия есть, сразу забрать оценки скрытым браузером.

    Вход навязываем только на самом первом запуске (нет ни кэша, ни сессии).
    Если оценки импортированы вручную, кэш уже есть: окно входа не открываем,
    пользователь сам нажмёт «Обновить оценки», когда захочет подтянуть остальные.
    """
    if start_demo:
        return "demo", False
    if has_cache:
        return "cache", False
    if has_session:
        return "fetch", False
    return "demo", bool(auto_login)
