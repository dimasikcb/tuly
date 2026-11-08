"""Модели данных. Нижний слой: ничего не знает о GUI, сети и Playwright."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Mapping


def clean_name(value: str) -> str:
    """Нормализует название предмета: обрезает и схлопывает пробелы."""
    return " ".join(str(value or "").split())


def normalize_weight(weight: Any) -> int:
    """Нормализует вес оценки к целому числу >= 1. Безопасен к null, str, float."""
    if weight is None or isinstance(weight, bool):
        return 1
    if isinstance(weight, int):
        return max(1, weight)
    try:
        if isinstance(weight, str):
            cleaned = weight.strip().replace(",", ".")
            if not cleaned:
                return 1
            w = float(cleaned)
        elif isinstance(weight, (float, int)):
            w = float(weight)
        else:
            w = float(weight)
        if math.isnan(w) or math.isinf(w):
            return 1
        return max(1, int(round(w)))
    except (ValueError, TypeError, OverflowError):
        return 1


class Mark(int):
    """Оценка (1..5) с опциональным весом (коэффициентом).

    Наследуется от int, поэтому полностью взаимозаменяема с обычным int:
    m == 5, isinstance(m, int), hash(m) — всё работает стандартно.
    """

    weight: int

    def __new__(cls, value: Any, weight: Any = 1):
        if isinstance(value, Mark) and (weight is None or weight == 1):
            weight = value.weight
        if isinstance(value, str):
            cleaned = value.strip().replace(",", ".")
            try:
                val = int(cleaned)
            except ValueError:
                val = int(round(float(cleaned)))
        elif isinstance(value, float):
            val = int(round(value))
        else:
            val = int(value)
        obj = super().__new__(cls, val)
        obj.weight = normalize_weight(weight)
        return obj

    @property
    def value(self) -> int:
        return int(self)

    @classmethod
    def from_raw(cls, raw: Any) -> Mark:
        if isinstance(raw, Mark):
            return raw
        if isinstance(raw, dict):
            val = raw.get("value") if "value" in raw else raw.get("mark", 0)
            weight = 1
            for wk in ("weight", "coef", "coefficient", "вес"):
                if wk in raw and raw[wk] is not None:
                    weight = raw[wk]
                    break
            return cls(val, weight)
        if isinstance(raw, (tuple, list)) and len(raw) == 2 and not isinstance(raw, str):
            return cls(raw[0], raw[1])
        return cls(raw, getattr(raw, "weight", 1))

    def to_dict_or_int(self) -> int | dict[str, int]:
        if self.weight > 1:
            return {"value": int(self), "weight": self.weight}
        return int(self)

    def __eq__(self, other: Any) -> bool:
        if isinstance(other, Mark):
            return int(self) == int(other) and self.weight == other.weight
        if isinstance(other, dict):
            val = other.get("value") if "value" in other else other.get("mark")
            w = 1
            for wk in ("weight", "coef", "coefficient", "вес"):
                if wk in other and other[wk] is not None:
                    w = normalize_weight(other[wk])
                    break
            return int(self) == val and self.weight == w
        if isinstance(other, (int, float)):
            return int(self) == other
        return False

    def __ne__(self, other: Any) -> bool:
        return not self.__eq__(other)

    def __hash__(self) -> int:
        return hash((int(self), self.weight))

    def __str__(self) -> str:
        if self.weight > 1:
            subscripts = str.maketrans("0123456789", "₀₁₂₃₄₅₆₇₈₉")
            return f"{int(self)}{str(self.weight).translate(subscripts)}"
        return str(int(self))

    def __repr__(self) -> str:
        if self.weight > 1:
            return f"Mark({int(self)}, weight={self.weight})"
        return f"Mark({int(self)})"


def format_average(s: int, n: int) -> str:
    """Форматирует средний балл с математическим округлением (ROUND_HALF_UP).

    Школьные журналы округляют строго по математике (0.005 и выше -> вверх):
        33 / 8  = 4.125 -> 4.13
        90 / 19 = 4.7368... -> 4.74
    """
    if n <= 0:
        return "0.00"
    dec = (Decimal(s) / Decimal(n)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    return str(dec)


@dataclass(frozen=True, slots=True)
class SubjectGrades:
    """Один предмет и все его оценки за период."""

    name: str
    marks: tuple[Mark, ...] = ()
    period: str = ""
    source: str = ""
    official_average: str = ""

    def __post_init__(self) -> None:
        object.__setattr__(self, "name", clean_name(self.name))
        normalized = tuple(Mark.from_raw(m) for m in (self.marks or ()))
        object.__setattr__(self, "marks", normalized)
        avg = str(self.official_average or "").strip().replace(",", ".")
        object.__setattr__(self, "official_average", avg)

    @property
    def total(self) -> int:
        """S — взвешенная сумма оценок: sum(mark * weight)."""
        return sum(int(m) * getattr(m, "weight", 1) for m in self.marks)

    @property
    def total_weight(self) -> int:
        """N — сумма весов оценок: sum(weight)."""
        return sum(getattr(m, "weight", 1) for m in self.marks)

    @property
    def count(self) -> int:
        """Количество отметок (штук)."""
        return len(self.marks)


@dataclass(frozen=True, slots=True)
class SubjectReport:
    """Предмет + всё, что показывает таблица."""

    name: str
    marks: tuple[Mark, ...]
    total: int                      # S = sum(mark * weight)
    count: int                      # N = sum(weight) для расчёта среднего
    average: float                  # S / N, только для отображения
    badge: str                      # red | yellow | green
    on_five: bool                   # 2*S >= 9*N
    fives_needed: int               # k = ceil((4.5*N - S) / 0.5)
    reserve: Mapping[int, int] = field(default_factory=dict)      # {4: n, 3: n, 2: n}
    first_drop: Mapping[int, int] = field(default_factory=dict)   # на какой по счёту оценке упадёт
    period: str = ""
    marks_count: int = 0            # физическое количество оценок
    official_average: str = ""      # официальный балл от журнала, если передан

    @property
    def average_str(self) -> str:
        """Средний балл со школьным математическим округлением до 2 знаков."""
        if self.official_average:
            return self.official_average
        if not self.count:
            return "—"
        return format_average(self.total, self.count)

    def marks_text(self) -> str:
        return ", ".join(str(m) for m in self.marks) if self.marks else "—"


@dataclass(frozen=True, slots=True)
class FetchResult:
    """Результат одного обновления. Источник важен: не выдаём демо за реальные оценки."""

    subjects: tuple[SubjectGrades, ...]
    period: str = ""
    source: str = "unknown"       # live | import | cache | demo
    urls: tuple[str, ...] = ()
    note: str = ""
    debug_files: tuple[str, ...] = ()

    @property
    def is_demo(self) -> bool:
        return self.source == "demo"


@dataclass(frozen=True, slots=True)
class Period:
    """Текущий учебный период."""

    label: str                    # «2 четверть» / «2 триместр»
    index: int
    aliases: tuple[str, ...]      # варианты текста для поиска в DOM
    scheme: str = "quarters"


class DiaryError(RuntimeError):
    """Базовая ошибка приложения (показывается в статусе, не роняет окно)."""


class SessionExpired(DiaryError):
    """Сессия ЕСИА протухла — нужен повторный ручной вход."""


class ParseError(DiaryError):
    """Не удалось найти оценки ни в JSON-ответах, ни в DOM."""
