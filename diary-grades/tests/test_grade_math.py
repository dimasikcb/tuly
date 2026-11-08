"""Исчерпывающие офлайн-тесты математики среднего балла (ядро приложения).

Запуск из папки проекта:

    python -X utf8 -m unittest tests.test_grade_math -v
    python -X utf8 -m unittest discover -s tests -v

Порог пятёрки = 4.50 = 9/2. Все проверки границ и свойств сделаны точными
целочисленными сравнениями (``2*S >= 9*N`` / ``2*S < 9*N``), чтобы ошибка
округления float никогда не дала школьнику неверный совет.
"""
from __future__ import annotations

import sys
import unittest
from dataclasses import replace
from datetime import date
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import demo_data
import grade_math
from grade_math import (
    MAX_MARK,
    MIN_MARK,
    RESERVE_MARKS,
    TARGET_DEN,
    TARGET_FLOAT,
    TARGET_NUM,
    badge_for,
    build_report,
    build_reports,
    explain,
    fives_word,
    first_drop,
    fives_needed,
    inf,
    is_on_five,
    reserve_map,
    reserve_text,
    safe_streak,
    sort_reports,
    validate_marks,
)
from models import SubjectGrades


def _avg_ge_45(s: int, n: int) -> bool:
    """Среднее >= 4.50 строго целыми: 2*S >= 9*N (без float)."""
    return TARGET_DEN * s >= TARGET_NUM * n


def _avg_lt_45(s: int, n: int) -> bool:
    """Среднее < 4.50 строго целыми: 2*S < 9*N (без float)."""
    return TARGET_DEN * s < TARGET_NUM * n


class TestThresholdAndFivesNeeded(unittest.TestCase):
    """Порог 4.50 = 9/2, границы is_on_five / fives_needed, N = 0."""

    def test_threshold_is_exactly_nine_halves(self):
        self.assertEqual((TARGET_NUM, TARGET_DEN), (9, 2))
        self.assertEqual(Fraction(TARGET_NUM, TARGET_DEN), Fraction(9, 2))
        self.assertEqual(TARGET_FLOAT, 4.5)
        self.assertEqual(RESERVE_MARKS, (4, 3, 2))
        self.assertEqual((MIN_MARK, MAX_MARK), (1, 5))

    def test_boundary_cases(self):
        # (S, N, ожидаемый is_on_five, ожидаемый fives_needed)
        cases = (
            (45, 10, True, 0),    # ровно 4.50 -> уже на пятёрке
            (44, 10, False, 2),   # не хватает двух пятёрок
            (17, 4, False, 2),    # 4.25 -> k = 2
            (9, 2, True, 0),      # ровно 4.50 при N = 2
            (4, 1, False, 1),     # нужна одна пятёрка
            (5, 1, True, 0),      # одна пятёрка -> запас, k = 0
            (25, 5, True, 0),     # пять пятёрок -> среднее 5.00
            (40, 10, False, 10),
            (50, 10, True, 0),
        )
        for s, n, on_five, k in cases:
            with self.subTest(S=s, N=n):
                self.assertIs(is_on_five(s, n), on_five)
                self.assertEqual(fives_needed(s, n), k)

    def test_zero_count(self):
        self.assertIs(is_on_five(0, 0), False)
        self.assertEqual(fives_needed(0, 0), 0)
        # Нет оценок -> советов не даём, даже если S «большой».
        self.assertIs(is_on_five(100, 0), False)
        self.assertEqual(fives_needed(100, 0), 0)

    def test_k_is_consistent_with_on_five_everywhere(self):
        for n in range(1, 31):
            for s in range(n, 5 * n + 1):
                with self.subTest(S=s, N=n):
                    k = fives_needed(s, n)
                    self.assertGreaterEqual(k, 0)
                    self.assertIs(is_on_five(s, n), (k == 0))

    def test_minimal_k_property_all_valid_sums(self):
        """ГЛАВНЫЙ тест (а): k минимально, при котором среднее станет >= 4.50.

        Перебираем все N = 1..30 и все допустимые суммы S = N..5N.
        Точные целые сравнения вместо float:
            (S + 5k)/(N + k) >= 4.5   <=>   2*(S + 5k) >= 9*(N + k)
            (S + 5(k-1))/(N + k-1) < 4.5 <=> 2*(S + 5(k-1)) < 9*(N + k-1)
        """
        for n in range(1, 31):
            for s in range(n, 5 * n + 1):
                k = fives_needed(s, n)
                with self.subTest(S=s, N=n, k=k):
                    self.assertTrue(
                        _avg_ge_45(s + 5 * k, n + k),
                        f"с k={k} среднее не дотянуло до 4.50",
                    )
                    if k > 0:
                        self.assertTrue(
                            _avg_lt_45(s + 5 * (k - 1), n + k - 1),
                            f"k={k} не минимально: k-1 уже даёт >= 4.50",
                        )
                    else:
                        # k == 0 означает, что исходное среднее уже >= 4.50.
                        self.assertTrue(_avg_ge_45(s, n))

    def test_minimal_k_matches_closed_formula(self):
        for n in range(1, 31):
            for s in range(n, 5 * n + 1):
                with self.subTest(S=s, N=n):
                    self.assertEqual(
                        fives_needed(s, n),
                        max(0, TARGET_NUM * n - TARGET_DEN * s),
                    )


class TestSafeStreak(unittest.TestCase):
    """Запас прочности для оценок 4/3/2 и «бесконечная» пятёрка."""

    def test_explicit_values(self):
        self.assertEqual(safe_streak(46, 10, 4), 2)
        self.assertEqual(safe_streak(46, 10, 3), 0)
        self.assertEqual(safe_streak(45, 10, 4), 0)
        self.assertEqual(first_drop(46, 10, 4), 3)
        self.assertEqual(safe_streak(47, 10, 4), 4)
        self.assertEqual(safe_streak(47, 10, 3), 1)
        self.assertEqual(safe_streak(47, 10, 2), 0)

    def test_mark_five_is_infinite(self):
        for s, n in ((45, 10), (50, 10), (0, 0), (30, 10), (5, 1)):
            with self.subTest(S=s, N=n):
                self.assertEqual(safe_streak(s, n, 5), -1)
                self.assertEqual(first_drop(s, n, 5), -1)
        self.assertEqual(inf(-1), "∞")
        self.assertEqual(inf(3), "3")

    def test_not_on_five_has_no_reserve(self):
        for s, n in ((44, 10), (36, 10), (17, 4), (4, 1)):
            with self.subTest(S=s, N=n):
                for mark in RESERVE_MARKS:
                    self.assertEqual(safe_streak(s, n, mark), 0)
                    self.assertEqual(first_drop(s, n, mark), 1)
                self.assertEqual(reserve_map(s, n), {})

    def test_invalid_mark_raises_value_error(self):
        for mark in (1, 0, -1, 6, 10, 7):
            with self.subTest(mark=mark), self.assertRaises(ValueError):
                safe_streak(45, 10, mark)

    def test_reserve_map_on_five(self):
        self.assertEqual(reserve_map(46, 10), {4: 2, 3: 0, 2: 0})
        self.assertEqual(reserve_map(47, 10), {4: 4, 3: 1, 2: 0})
        self.assertEqual(reserve_map(45, 10), {4: 0, 3: 0, 2: 0})

    def test_safe_streak_property_all_valid_sums(self):
        """ГЛАВНЫЙ тест (б): границы safe / safe+1 для всех допустимых (S, N).

        Для каждого on-five состояния и mark in (4, 3, 2):
            после `safe` оценок среднее >= 4.5  ->  2*(S + safe*m) >= 9*(N + safe)
            после `safe+1` оценок строго < 4.5 ->  2*(S + (safe+1)*m) < 9*(N + safe+1)
        Сравнения исключительно целочисленные, НЕ float.
        """
        for n in range(1, 31):
            for s in range(n, 5 * n + 1):
                if not is_on_five(s, n):
                    continue
                for mark in RESERVE_MARKS:
                    safe = safe_streak(s, n, mark)
                    with self.subTest(S=s, N=n, mark=mark, safe=safe):
                        self.assertGreaterEqual(safe, 0)
                        self.assertEqual(first_drop(s, n, mark), safe + 1)
                        self.assertTrue(
                            _avg_ge_45(s + safe * mark, n + safe),
                            "после safe оценок среднее должно быть >= 4.50",
                        )
                        self.assertTrue(
                            _avg_lt_45(s + (safe + 1) * mark, n + safe + 1),
                            "после safe+1 оценок среднее должно быть строго < 4.50",
                        )


class TestBadge(unittest.TestCase):
    """Границы бейджа: 3.5 = 7/2 и 4.5 = 9/2."""

    def test_boundaries(self):
        cases = (
            (34, 10, "red"),     # 3.40 < 3.5
            (35, 10, "yellow"),  # ровно 3.50 -> уже жёлтый
            (44, 10, "yellow"),  # 4.40 -> всё ещё жёлтый
            (45, 10, "green"),   # ровно 4.50 -> зелёный
            (7, 2, "yellow"),    # 3.5 = 7/2 при N = 2
            (13, 4, "red"),      # 3.25
            (9, 2, "green"),     # 4.5 = 9/2 при N = 2
            (50, 10, "green"),
        )
        for s, n, expected in cases:
            with self.subTest(S=s, N=n):
                self.assertEqual(badge_for(s, n), expected)

    def test_zero_count_is_red(self):
        self.assertEqual(badge_for(0, 0), "red")
        self.assertEqual(badge_for(100, 0), "red")

    def test_badge_matches_on_five_for_all_sums(self):
        for n in range(1, 31):
            for s in range(n, 5 * n + 1):
                with self.subTest(S=s, N=n):
                    badge = badge_for(s, n)
                    self.assertIn(badge, ("red", "yellow", "green"))
                    if is_on_five(s, n):
                        self.assertEqual(badge, "green")
                    else:
                        self.assertIn(badge, ("red", "yellow"))


class TestValidateMarks(unittest.TestCase):
    """Нормализация оценок и жёсткий отказ от мусора."""

    def test_valid_marks_are_normalized(self):
        self.assertEqual(validate_marks([5, 4, 3, 2, 1]), (5, 4, 3, 2, 1))
        self.assertEqual(validate_marks([" 5 ", "4", "3"]), (5, 4, 3))
        self.assertEqual(validate_marks([5.0, 4.0, 3.0]), (5, 4, 3))
        self.assertEqual(validate_marks([]), ())
        self.assertEqual(validate_marks(None), ())
        self.assertEqual(validate_marks((m for m in (2, 3))), (2, 3))
        # Пустые/пробельные строки отфильтровываются слоем парсера, не ошибокой.
        self.assertEqual(validate_marks(["", "   ", 5]), (5,))

    def test_invalid_marks_raise_value_error(self):
        # bool, мусорные строки, строки с запятой/пробелами внутри, дробные,
        # значения вне 1..5 -> ValueError.
        bad_inputs = (
            True, False,                        # bool
            "н/а", "пять", "abc", "5.0", "4,5", " 4 5 ", "4 . 0",  # строки
            4.5, 2.25, 3.99, -0.5,            # дробные float
            0, 6, 7, -1, 10, 100,             # int вне 1..5
            "0", "6", "-1", "10",             # строки вне 1..5
        )
        for raw in bad_inputs:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                validate_marks([raw])

    def test_invalid_mark_inside_valid_sequence(self):
        with self.assertRaises(ValueError):
            validate_marks([5, 4, "н/а", 3])
        with self.assertRaises(ValueError):
            validate_marks([5, 4, True, 3])


class TestBuildReport(unittest.TestCase):
    """Поля SubjectReport, включая пустой предмет."""

    def test_build_report_fields(self):
        report = build_report(
            SubjectGrades("  Физика  ", (5, 5, 5, 5, 4, 4, 4, 4, 4, 4), "2 четверть", "demo")
        )
        self.assertEqual(report.name, "Физика")  # clean_name
        self.assertEqual(report.marks, (5, 5, 5, 5, 4, 4, 4, 4, 4, 4))
        self.assertEqual((report.total, report.count), (44, 10))
        self.assertAlmostEqual(report.average, 4.4)
        self.assertEqual(report.badge, "yellow")
        self.assertIs(report.on_five, False)
        self.assertEqual(report.fives_needed, 2)
        self.assertEqual(report.reserve, {})
        self.assertEqual(report.first_drop, {})
        self.assertEqual(report.period, "2 четверть")
        self.assertEqual(report.marks_text(), "5, 5, 5, 5, 4, 4, 4, 4, 4, 4")

    def test_build_report_on_five(self):
        report = build_report(SubjectGrades("Литература", (5, 5, 4, 5, 4, 5, 5, 4, 5, 5)))
        self.assertIs(report.on_five, True)
        self.assertEqual(report.badge, "green")
        self.assertEqual(report.fives_needed, 0)
        self.assertEqual(report.reserve, {4: 4, 3: 1, 2: 0})
        self.assertEqual(report.first_drop, {4: 5, 3: 2, 2: 1})

    def test_empty_report(self):
        report = build_report(SubjectGrades("Пустой", ()))
        self.assertEqual((report.total, report.count), (0, 0))
        self.assertEqual(report.average, 0.0)
        self.assertEqual(report.badge, "red")
        self.assertIs(report.on_five, False)
        self.assertEqual(report.fives_needed, 0)
        self.assertEqual(report.reserve, {})
        self.assertEqual(report.first_drop, {})
        self.assertEqual(report.marks_text(), "—")

    def test_build_report_rejects_bad_marks(self):
        with self.assertRaises(ValueError):
            build_report(SubjectGrades("Брак", (5, 4, "н/а")))
        with self.assertRaises(ValueError):
            build_report(SubjectGrades("Брак", (5, 4, 6)))


class TestSortReports(unittest.TestCase):
    """Строгая сортировка от худшего среднего к лучшему."""

    def test_demo_data_is_sorted_worst_to_best(self):
        reports = build_reports(demo_data.demo_result().subjects)
        ordered = sort_reports(reports)

        expected = (
            "Обществознание", "Математика", "Русский язык", "Физика", "Химия",
            "История", "Английский язык", "Литература", "Биология",
            "Физкультура", "География", "Информатика",
        )
        self.assertEqual(tuple(r.name for r in ordered), expected)

        # Монотонность по ТОЧНОМУ среднему (Fraction), не по float.
        exact = [Fraction(r.total, r.count) for r in ordered]
        self.assertEqual(exact, sorted(exact))
        self.assertLess(exact[0], exact[-1])
        self.assertEqual(exact[0], Fraction(30, 9))     # худший 3.33
        self.assertEqual(exact[-1], Fraction(50, 10))   # лучший 5.00
        # Физкультура 39/8 = 4.875 идет между Биологией 4.8 и Географией 4.9.
        self.assertEqual(exact[8], Fraction(48, 10))
        self.assertEqual(exact[9], Fraction(39, 8))
        self.assertEqual(exact[10], Fraction(49, 10))

        # При равном среднем (Физика/Химия = 43/10) порядок по алфавиту.
        self.assertEqual(Fraction(ordered[3].total, ordered[3].count),
                         Fraction(ordered[4].total, ordered[4].count))
        self.assertEqual((ordered[3].name, ordered[4].name), ("Физика", "Химия"))

    def test_equal_average_larger_fives_needed_comes_first(self):
        """Ручной пример: одинаковое среднее 4.0, разное число нужных пятёрок.

        A: 10 четвёрок -> S=40, N=10, k=10
        B: 20 четвёрок -> S=80, N=20, k=20  (то же среднее, пятёрок нужно больше)
        Худшим считается B, поэтому он идёт первым.
        """
        a = build_report(SubjectGrades("A", (4,) * 10))
        b = build_report(SubjectGrades("B", (4,) * 20))
        self.assertEqual(Fraction(a.total, a.count), Fraction(b.total, b.count))
        self.assertEqual((a.fives_needed, b.fives_needed), (10, 20))

        ordered = sort_reports([a, b])
        self.assertEqual([r.name for r in ordered], ["B", "A"])
        # Порядок не зависит от порядка подачи на вход.
        self.assertEqual([r.name for r in sort_reports([b, a])], ["B", "A"])

    def test_sort_uses_exact_fraction_not_float_average(self):
        """Точность сортировки через Fraction(total, count), а не float average.

        Намеренно выставляем обоим отчётам ОДИНАКОВЫЙ float `average` (= 4.4).
        Если бы сортировка сравнивала float, они бы «слиплись» и тай-брейк по
        -fives_needed поставил бы первым B (неверно). Реальная сортировка
        использует точные дроби 44/10 < 441/100, поэтому первым идёт W.
        """
        worse = build_report(SubjectGrades("W", (5, 5, 5, 5) + (4,) * 6))   # 44/10
        better = build_report(SubjectGrades("B", (5,) * 41 + (4,) * 59))    # 441/100
        self.assertEqual((worse.total, worse.count, worse.fives_needed), (44, 10, 2))
        self.assertEqual((better.total, better.count, better.fives_needed), (441, 100, 18))
        self.assertLess(Fraction(worse.total, worse.count),
                        Fraction(better.total, better.count))

        worse = replace(worse, average=4.4)
        better = replace(better, average=4.4)
        self.assertEqual(worse.average, better.average)

        ordered = sort_reports([better, worse])
        self.assertEqual([r.name for r in ordered], ["W", "B"])

    def test_sort_accepts_any_iterable(self):
        reports = build_reports(demo_data.demo_result().subjects[:3])
        self.assertEqual(
            [r.name for r in sort_reports(iter(reports))],
            [r.name for r in sort_reports(list(reports))],
        )


class TestTextHelpers(unittest.TestCase):
    """reserve_text / explain не падают и содержат ключевые числа."""

    def test_reserve_text(self):
        self.assertEqual(reserve_text({}), "—")
        text = reserve_text({4: 2, 3: 0, 2: 0})
        self.assertEqual(text, "4→2 · 3→0 · 2→0")
        for part in ("4→2", "3→0", "2→0"):
            self.assertIn(part, text)
        self.assertIn("∞", reserve_text({4: -1}))

    def test_explain_contains_key_numbers_for_all_demo_reports(self):
        for report in build_reports(demo_data.demo_result().subjects):
            with self.subTest(name=report.name):
                text = explain(report)
                self.assertIsInstance(text, str)
                self.assertTrue(text)
                self.assertIn(f"S={report.total}", text)
                self.assertIn(f"N={report.count}", text)
                self.assertIn("4.50", text)
                if not report.on_five:
                    self.assertIn(
                        f"Нужно {report.fives_needed} {fives_word(report.fives_needed)} подряд",
                        text,
                    )
                    self.assertIn(str(report.fives_needed), text)
                else:
                    self.assertIn("Уже на пятёрке", text)
                # И запас прочности форматируется без исключений.
                reserve = reserve_text(report.reserve)
                self.assertIsInstance(reserve, str)

    def test_explain_on_five_with_reserve(self):
        report = build_report(SubjectGrades("Литература", (5, 5, 4, 5, 4, 5, 5, 4, 5, 5)))
        text = explain(report)
        self.assertIn("S=47", text)
        self.assertIn("N=10", text)
        self.assertIn("4", text)           # «4» — 4 подряд
        self.assertIn("на 5-й упадёт", text)

    def test_explain_empty_report(self):
        text = explain(build_report(SubjectGrades("Пусто", ())))
        self.assertIn("S=0", text)
        self.assertIn("N=0", text)
        self.assertIn("Оценок за период нет", text)


class TestCurrentPeriod(unittest.TestCase):
    """config.current_period на конкретных датах (явная схема, без чтения data/)."""

    def test_quarters(self):
        cases = (
            (date(2026, 9, 5), "1 четверть"),
            (date(2026, 11, 5), "2 четверть"),
            (date(2026, 2, 1), "3 четверть"),
            (date(2026, 4, 15), "4 четверть"),
            (date(2026, 7, 1), "4 четверть"),   # лето -> последний завершённый
        )
        for today, label in cases:
            with self.subTest(today=today):
                period = config.current_period(today=today, scheme="quarters")
                self.assertEqual(period.label, label)
                self.assertEqual(period.scheme, "quarters")
                self.assertIn(period.label, config.period_aliases(period))

    def test_trimesters(self):
        cases = (
            (date(2026, 9, 1), "1 триместр"),    # начало года
            (date(2026, 10, 10), "1 триместр"),
            (date(2026, 11, 6), "1 триместр"),   # «01 сент – 15 ноя» из дневника
            (date(2026, 11, 15), "1 триместр"),
            (date(2026, 11, 16), "2 триместр"),
            (date(2026, 12, 20), "2 триместр"),
            (date(2027, 1, 10), "2 триместр"),   # период через Новый год
            (date(2027, 2, 15), "2 триместр"),
            (date(2027, 2, 16), "3 триместр"),
            (date(2026, 3, 20), "3 триместр"),
            (date(2026, 7, 1), "3 триместр"),    # лето
        )
        for today, label in cases:
            with self.subTest(today=today):
                period = config.current_period(today=today, scheme="trimesters")
                self.assertEqual(period.label, label)
                self.assertEqual(period.scheme, "trimesters")

    def test_default_scheme_matches_real_diary(self):
        """В дневнике МО «1 триместр», а не «2 четверть»."""
        scheme = config.DEFAULTS["period_scheme"]
        self.assertEqual(scheme, "trimesters")
        self.assertEqual(config.current_period(date(2026, 11, 6), scheme=scheme).label,
                         "1 триместр")

    def test_winter_gap_quarters(self):
        """Новогодние каникулы между 2-й и 3-й четвертью."""
        self.assertEqual(
            config.current_period(date(2027, 1, 5), scheme="quarters").label,
            "2 четверть")

    def test_period_metadata(self):
        p = config.current_period(today=date(2026, 11, 5), scheme="quarters")
        self.assertEqual(p.index, 2)
        self.assertIn("2 четверть", p.aliases)
        self.assertEqual(config.period_aliases(p)[0], p.label)
        t = config.current_period(today=date(2026, 3, 20), scheme="trimesters")
        self.assertEqual(t.index, 3)

    def test_api_date_range_trimesters(self):
        """Даты периода — точно в границы для /api/family/web/v1/marks?from=...&to=..."""
        cases = [
            (config.Period("1 триместр", 1, (), "trimesters"),
             date(2026, 11, 6), (date(2026, 9, 1), date(2026, 11, 15))),
            (config.Period("2 триместр", 2, (), "trimesters"),
             date(2027, 1, 10), (date(2026, 11, 16), date(2027, 2, 15))),
            (config.Period("2 триместр", 2, (), "trimesters"),
             date(2026, 12, 20), (date(2026, 11, 16), date(2027, 2, 15))),
            (config.Period("3 триместр", 3, (), "trimesters"),
             date(2027, 3, 20), (date(2027, 2, 16), date(2027, 5, 31))),
            (config.Period("1 триместр", 1, (), "quarters"),
             date(2026, 11, 6), (date(2026, 9, 1), date(2026, 10, 31))),
            (config.Period("3 четверть", 3, (), "quarters"),
             date(2027, 1, 10), (date(2027, 1, 10), date(2027, 3, 25))),
        ]
        for period, today, expected in cases:
            with self.subTest(period=period.label, today=today):
                self.assertEqual(config.period_date_range(period, today=today),
                                 expected)
        p = config.current_period(today=date(2026, 11, 5), scheme="quarters")
        self.assertEqual(p.index, 2)
        self.assertIn("2 четверть", p.aliases)
        self.assertEqual(config.period_aliases(p)[0], p.label)
        t = config.current_period(today=date(2026, 3, 20), scheme="trimesters")
        self.assertEqual(t.index, 3)


class TestDemoData(unittest.TestCase):
    """Демо-результат помечен как demo и содержит только валидные оценки."""

    def test_demo_result(self):
        result = demo_data.demo_result()
        self.assertEqual(result.source, "demo")
        self.assertTrue(result.is_demo)
        self.assertGreater(len(result.subjects), 0)
        self.assertTrue(result.period)
        for subject in result.subjects:
            with self.subTest(name=subject.name):
                self.assertTrue(subject.name)
                self.assertEqual(subject.source, "demo")
                self.assertGreater(subject.count, 0)
                marks = validate_marks(subject.marks)
                self.assertEqual(marks, subject.marks)
                for m in marks:
                    self.assertGreaterEqual(m, MIN_MARK)
                    self.assertLessEqual(m, MAX_MARK)
        # Отчёты по демо строятся без исключений.
        self.assertEqual(len(build_reports(result.subjects)), len(result.subjects))


class TestStartupPlan(unittest.TestCase):
    """При наличии кэша окно входа ЕСИА не должно открываться само."""

    def test_all_combinations(self):
        cases = (
            (True,  False, False, True,  "demo", False),   # --demo
            (True,  True,  True,  True,  "demo", False),
            (False, True,  False, True,  "cache", False),  # импорт вручную
            (False, True,  True,  True,  "cache", False),
            (False, False, True,  True,  "fetch", False),  # сессия есть
            (False, False, False, True,  "demo", True),    # первый запуск
            (False, False, False, False, "demo", False),   # --no-login
        )
        for demo, cache, session, auto, mode, login in cases:
            with self.subTest(demo=demo, cache=cache, session=session, auto=auto):
                self.assertEqual(config.startup_plan(demo, cache, session, auto),
                                 (mode, login))


class TestEmptySubjects(unittest.TestCase):
    """Предметы-заготовки без оценок: не ломают расчёт и уходят в конец таблицы."""

    def test_report_without_marks(self):
        report = build_report(SubjectGrades("Математика", (), "1 триместр", "pending"))
        self.assertEqual(report.count, 0)
        self.assertEqual(report.average, 0.0)
        self.assertFalse(report.on_five)
        self.assertEqual(report.reserve, {})
        self.assertEqual(reserve_text(report.reserve), "—")
        self.assertIn("расчёт невозможен", explain(report))

    def test_empty_go_last(self):
        reports = build_reports([
            SubjectGrades("Математика", (), "1 триместр", "pending"),
            SubjectGrades("Обществознание", (4, 4, 4)),
            SubjectGrades("Информатика", (5, 5)),
            SubjectGrades("Физика", (), "1 триместр", "pending"),
        ])
        names = [r.name for r in sort_reports(reports)]
        self.assertEqual(names[0], "Обществознание")
        self.assertEqual(names[-2:], ["Математика", "Физика"])


class TestMathRoundingAndWeights(unittest.TestCase):
    """Школьное математическое округление (ROUND_HALF_UP) и веса оценок."""

    def test_algebra_exact_rounding_matches_journal(self):
        # 8 оценок: 5, 4, 4, 4, 5, 4, 4, 3 -> S=33, N=8, 33/8 = 4.125 -> 4.13
        algebra = SubjectGrades("Алгебра", (5, 4, 4, 4, 5, 4, 4, 3))
        rep = build_report(algebra)
        self.assertEqual(rep.total, 33)
        self.assertEqual(rep.count, 8)
        self.assertEqual(rep.average_str, "4.13")
        self.assertIn("средний=4.13", explain(rep))

    def test_english_weighted_average_matches_journal(self):
        # 18 оценок: 12 пятерок (вес 1), одна пятерка (вес 2), 5 четверок (вес 1)
        # S = 12*5 + 10 + 20 = 90, N = 19 -> 90/19 = 4.7368... -> 4.74
        marks = [5, 5, 5, 5, 5, 4, {"value": 5, "weight": 2}, 5, 4, 5, 5, 5, 4, 4, 5, 5, 5, 4]
        english = SubjectGrades("Иностранный (английский) язык", marks)
        rep = build_report(english)
        self.assertEqual(rep.total, 90)
        self.assertEqual(rep.count, 19)
        self.assertEqual(rep.marks_count, 18)
        self.assertEqual(rep.average_str, "4.74")
        self.assertIn("5₂", rep.marks_text())
        self.assertIn("средний=4.74", explain(rep))

    def test_official_average_precedence_and_fallback(self):
        # 1. Если передан официальный средний балл — он приоритетен в average_str
        sub_with_official = SubjectGrades(
            "Алгебра",
            (5, 4, 4, 4, 5, 4, 4, 3),  # расчётный 4.13
            official_average="4.15",
        )
        rep = build_report(sub_with_official)
        self.assertEqual(rep.official_average, "4.15")
        self.assertEqual(rep.average_str, "4.15")
        self.assertEqual(rep.average, 4.13)  # числовой расчёт сохраняется для сортировок

        # 2. Если официального балла нет — вычисляется школьное округление
        sub_without_official = SubjectGrades("Алгебра", (5, 4, 4, 4, 5, 4, 4, 3))
        rep_without = build_report(sub_without_official)
        self.assertEqual(rep_without.official_average, "")
        self.assertEqual(rep_without.average_str, "4.13")

        # 3. Пустой предмет с официальным баллом
        sub_empty_with_official = SubjectGrades("Алгебра", (), official_average="4.50")
        rep_empty = build_report(sub_empty_with_official)
        self.assertEqual(rep_empty.average_str, "4.50")

        # 4. Пустой предмет без официального балла
        sub_empty = SubjectGrades("Алгебра", ())
        rep_empty_no = build_report(sub_empty)
        self.assertEqual(rep_empty_no.average_str, "—")



if __name__ == "__main__":
    unittest.main(verbosity=2)
