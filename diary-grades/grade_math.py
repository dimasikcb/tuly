"""Математика среднего балла. Чистые функции, без float в решениях.

Порог пятёрки = 4.50 = 9/2. Все сравнения и расчёты — целочисленные:
    на пятёрке  <=>  2*S >= 9*N
    нужно пятёрок k = ceil((4.5*N - S) / 0.5) = 9*N - 2*S
    запас D2 = 2*S - 9*N >= 0

Запас прочности для оценки v (2, 3, 4):
После m таких оценок подряд среднее < 4.5 <=>
    2*(S + m*v) < 9*(N + m)  <=>  D2 < m*(9 - 2*v)
Значит безопасных оценок подряд = floor(D2 / (9 - 2*v)),
а первая «роняющая» — следующая по счёту (safe + 1).
Ровно 4.50 падением НЕ считается (условие строго «ниже 4.50»).
"""
from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from fractions import Fraction
from typing import Iterable, Mapping, Sequence

from models import Mark, SubjectGrades, SubjectReport, format_average

TARGET_NUM, TARGET_DEN = 9, 2          # 4.5
TARGET_FLOAT = 4.5
RESERVE_MARKS = (4, 3, 2)
MIN_MARK, MAX_MARK = 1, 5


def validate_marks(marks: Iterable) -> tuple[Mark, ...]:
    """Приводит оценки к кортежу Mark(1..5, weight). Дробные/мусор — явная ошибка.

    Поддерживает:
      * целые числа 1..5;
      * объекты Mark;
      * словари вида {"value": 5, "weight": 2} или {"mark": 5, "weight": 2};
      * кортежи/списки (значение, вес);
      * строки с целыми числами "5" или "5.0".
    В дневниках МО оценки целые (2..5); «н/а», прочерки и пустоты
    отфильтровываются парсером, а не здесь.
    """
    out: list[Mark] = []
    for raw in marks or ():
        if isinstance(raw, bool):
            raise ValueError(f"Некорректная оценка: {raw!r}")
        weight: Any = 1
        if isinstance(raw, Mark):
            value = int(raw)
            weight = raw.weight
        elif isinstance(raw, dict):
            val = raw.get("value") if "value" in raw else raw.get("mark")
            weight = 1
            for wk in ("weight", "coef", "coefficient", "вес"):
                if wk in raw and raw[wk] is not None:
                    weight = raw[wk]
                    break
            raw = val
        elif isinstance(raw, (tuple, list)) and len(raw) == 2 and not isinstance(raw, str):
            raw, weight = raw[0], raw[1]
        elif isinstance(raw, str):
            raw = raw.strip().replace(",", ".")
            if not raw:
                continue
            try:
                raw = int(raw)
            except ValueError as exc:
                raise ValueError(f"Некорректная оценка: {raw!r}") from exc
        if isinstance(raw, float) and not raw.is_integer():
            raise ValueError(f"Дробная оценка не поддерживается: {raw!r}")
        try:
            value = int(raw)
        except (ValueError, TypeError) as exc:
            raise ValueError(f"Некорректная оценка: {raw!r}") from exc
        if not MIN_MARK <= value <= MAX_MARK:
            raise ValueError(f"Оценка вне диапазона 1..5: {value}")
        out.append(Mark(value, weight=weight))
    return tuple(out)


def total(marks: Sequence[int]) -> int:
    return sum(marks)


def is_on_five(s: int, n: int) -> bool:
    """Средний >= 4.50 (точное сравнение, без ошибок округления float)."""
    if n <= 0:
        return False
    return TARGET_DEN * s >= TARGET_NUM * n


def fives_needed(s: int, n: int) -> int:
    """k = ceil((4.5*N - S)/0.5) = 9*N - 2*S. Для уже пятёрочника — 0."""
    if n <= 0:
        return 0
    return max(0, TARGET_NUM * n - TARGET_DEN * s)


def average(s: int, n: int) -> float:
    """Средний балл со школьным математическим округлением (ROUND_HALF_UP) до сотых.

    Гарантирует, что 33 / 8 = 4.125 вернёт 4.13, а не 4.12 при форматировании.
    """
    if n <= 0:
        return 0.0
    return float((Decimal(s) / Decimal(n)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def badge_for(s: int, n: int) -> str:
    """red < 3.5, yellow 3.5..4.49, green >= 4.5."""
    if n <= 0 or 2 * s < 7 * n:
        return "red"
    if TARGET_DEN * s < TARGET_NUM * n:
        return "yellow"
    return "green"


def safe_streak(s: int, n: int, mark: int) -> int:
    """Сколько оценок `mark` подряд можно получить, не упав НИЖЕ 4.50.

    Для mark == 5 возвращает -1 (бесконечность — пятёрки никогда не роняют).
    """
    if mark not in RESERVE_MARKS and mark != MAX_MARK:
        raise ValueError(f"Запас считается для оценок 2,3,4 (и 5): {mark}")
    if mark == MAX_MARK:
        return -1
    if not is_on_five(s, n):
        return 0
    d2 = TARGET_DEN * s - TARGET_NUM * n        # >= 0
    den = TARGET_NUM - TARGET_DEN * mark        # 1 / 3 / 5
    return d2 // den


def first_drop(s: int, n: int, mark: int) -> int:
    """Номер оценки подряд, на которой балл впервые станет < 4.50. -1 = никогда."""
    safe = safe_streak(s, n, mark)
    return -1 if safe < 0 else safe + 1


def reserve_map(s: int, n: int) -> dict[int, int]:
    if not is_on_five(s, n):
        return {}
    return {m: safe_streak(s, n, m) for m in RESERVE_MARKS}


def build_report(subject: SubjectGrades) -> SubjectReport:
    marks = validate_marks(subject.marks)
    s = sum(int(m) * getattr(m, "weight", 1) for m in marks)
    n = sum(getattr(m, "weight", 1) for m in marks)
    on_five = is_on_five(s, n)
    reserve = reserve_map(s, n)
    badge = badge_for(s, n)
    if subject.official_average and n <= 0:
        try:
            val = float(subject.official_average)
            on_five = val >= TARGET_FLOAT
            if val >= TARGET_FLOAT:
                badge = "green"
            elif val >= 3.5:
                badge = "yellow"
            else:
                badge = "red"
        except (ValueError, TypeError):
            pass
    return SubjectReport(
        name=subject.name,
        marks=marks,
        total=s,
        count=n,
        average=average(s, n),
        badge=badge,
        on_five=on_five,
        fives_needed=fives_needed(s, n),
        reserve=reserve,
        first_drop={m: first_drop(s, n, m) for m in RESERVE_MARKS} if on_five else {},
        period=subject.period,
        marks_count=len(marks),
        official_average=subject.official_average,
    )


def build_reports(subjects: Iterable[SubjectGrades]) -> list[SubjectReport]:
    return [build_report(s) for s in subjects]


def sort_reports(reports: Iterable[SubjectReport],
                 by: str = "average") -> list[SubjectReport]:
    """Сортировка отчётов.

    by == "average" — строго от худшего среднего к лучшему (точное сравнение
    через Fraction). При равном среднем худшим считается предмет, которому
    нужно больше пятёрок; затем по алфавиту.

    by == "alpha" — по названию предмета (по алфавиту).
    """
    if by == "alpha":
        return sorted(reports, key=lambda r: r.name.casefold())

    def key(r: SubjectReport):
        if r.count:
            exact = Fraction(r.total, r.count)
        elif r.official_average:
            try:
                exact = Fraction(Decimal(r.official_average))
            except Exception:
                exact = Fraction(10 ** 9)
        else:
            exact = Fraction(10 ** 9)
        return (exact, -r.fives_needed, r.name.casefold())

    return sorted(reports, key=key)


def reserve_text(reserve: Mapping[int, int]) -> str:
    """Компактная форма для ячейки «Запас прочности»: «4→2 · 3→0 · 2→0»."""
    if not reserve:
        return "—"
    return " · ".join(f"{m}→{inf(v)}" for m, v in reserve.items())


def inf(value: int) -> str:
    return "∞" if value < 0 else str(value)


def fives_word(k: int) -> str:
    """Склонение: 1 пятёрку, 2 пятёрки, 5 пятёрок, 21 пятёрку, 11 пятёрок."""
    if 11 <= k % 100 <= 14:
        return "пятёрок"
    last = k % 10
    if last == 1:
        return "пятёрку"
    if 2 <= last <= 4:
        return "пятёрки"
    return "пятёрок"


def explain(report: SubjectReport) -> str:
    """Расшифровка расчёта для нижней панели окна."""
    head = (
        f"{report.name}: S={report.total}, N={report.count}, "
        f"средний={report.average_str} (порог 4.50)"
    )
    if not report.count:
        return head + "\nОценок за период нет — расчёт невозможен."
    if not report.on_five:
        k = report.fives_needed
        after = format_average(report.total + 5 * k, report.count + k)
        return (
            f"{head}\nНужно {k} {fives_word(k)} подряд: "
            f"k = ceil((4.5·{report.count} − {report.total}) / 0.5) = {k}. "
            f"После них средний = {after}."
        )
    lines = [
        head,
        "Уже на пятёрке. Сколько оценок подряд выдержит балл, не упав ниже 4.50:",
    ]
    for mark in RESERVE_MARKS:
        safe = report.reserve.get(mark, 0)
        drop = report.first_drop.get(mark, 1)
        if safe == 0:
            lines.append(f"  «{mark}» — ни одной: первая же даёт средний ниже 4.50.")
        else:
            after = format_average(report.total + mark * safe, report.count + safe)
            lines.append(
                f"  «{mark}» — {safe} подряд (балл {after}); "
                f"на {drop}-й упадёт ниже 4.50."
            )
    return "\n".join(lines)
