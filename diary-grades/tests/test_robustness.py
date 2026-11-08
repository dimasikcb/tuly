"""Тесты надежности (робастности) и стресс-тестирование diary-grades.

Проверяет краевые случаи:
1. models.py: Mark с весами null/str/float, clean_name, SubjectGrades, SubjectReport.
2. grade_math.py: validate_marks с краевыми типами весов, деление на 0,
   пустые предметы, official_average без пересчета, сортировка.
3. parsers.py: извлечение official_average из различных схем JSON и HTML таблиц/карточек,
   нестандартные типы данных, пустые предметы.
4. diary_client.py: слияние official_average в _merge_best, обработка протухшей сессии
   ЕСИА (редиректы, коды 401/403, формы авторизации в HTML).
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import config
import diary_client
import grade_math as gm
import models
import parsers
from models import (
    DiaryError,
    Mark,
    ParseError,
    Period,
    SessionExpired,
    SubjectGrades,
    SubjectReport,
    clean_name,
    format_average,
    normalize_weight,
)
from parsers import (
    _extract_official_average,
    _extract_row_average,
    merge_subjects,
    parse_grades_html,
    parse_grades_payload,
    parse_marks_api,
    validate_subjects,
)

PERIOD = Period("2 четверть", 2, ("2 четверть", "четверть 2"), "quarters")


class TestModelsRobustness(unittest.TestCase):
    """Стресс-тестирование models.py на краевые случаи."""

    def test_normalize_weight_all_types(self):
        # Null / None
        self.assertEqual(normalize_weight(None), 1)
        # Булевы
        self.assertEqual(normalize_weight(True), 1)
        self.assertEqual(normalize_weight(False), 1)
        # Целые
        self.assertEqual(normalize_weight(1), 1)
        self.assertEqual(normalize_weight(3), 3)
        self.assertEqual(normalize_weight(0), 1)
        self.assertEqual(normalize_weight(-5), 1)
        # Строки
        self.assertEqual(normalize_weight("2"), 2)
        self.assertEqual(normalize_weight("2.0"), 2)
        self.assertEqual(normalize_weight("2,0"), 2)
        self.assertEqual(normalize_weight(" 3 "), 3)
        self.assertEqual(normalize_weight(""), 1)
        self.assertEqual(normalize_weight("null"), 1)
        self.assertEqual(normalize_weight("abc"), 1)
        # Float
        self.assertEqual(normalize_weight(2.0), 2)
        self.assertEqual(normalize_weight(2.4), 2)
        self.assertEqual(normalize_weight(2.6), 3)
        self.assertEqual(normalize_weight(0.0), 1)
        self.assertEqual(normalize_weight(-1.5), 1)
        self.assertEqual(normalize_weight(float("nan")), 1)
        self.assertEqual(normalize_weight(float("inf")), 1)
        # Мусорные типы
        self.assertEqual(normalize_weight([1, 2]), 1)
        self.assertEqual(normalize_weight({"w": 2}), 1)

    def test_mark_weight_edge_cases(self):
        m1 = Mark(5, weight=None)
        self.assertEqual(m1.weight, 1)

        m2 = Mark(5, weight="2")
        self.assertEqual(m2.weight, 2)

        m3 = Mark(5, weight="2.0")
        self.assertEqual(m3.weight, 2)

        m4 = Mark(5, weight=2.0)
        self.assertEqual(m4.weight, 2)

        m5 = Mark(5, weight="2,0")
        self.assertEqual(m5.weight, 2)

        m6 = Mark(5, weight=0)
        self.assertEqual(m6.weight, 1)

        m7 = Mark(5, weight=-10)
        self.assertEqual(m7.weight, 1)

        m8 = Mark(5, weight="null")
        self.assertEqual(m8.weight, 1)

        # Передача другого Mark с сохранением веса
        m_copy = Mark(m2)
        self.assertEqual(m_copy.weight, 2)

        # Передача другого Mark с явным новым весом
        m_override = Mark(m2, weight=3)
        self.assertEqual(m_override.weight, 3)

    def test_mark_from_raw_edge_cases(self):
        # Словарь с весом None
        m1 = Mark.from_raw({"value": 5, "weight": None})
        self.assertEqual(m1.value, 5)
        self.assertEqual(m1.weight, 1)

        # Словарь с альтернативными ключами веса
        m2 = Mark.from_raw({"mark": 4, "coef": "2"})
        self.assertEqual(m2.value, 4)
        self.assertEqual(m2.weight, 2)

        m3 = Mark.from_raw({"value": 5, "coefficient": 2.0})
        self.assertEqual(m3.value, 5)
        self.assertEqual(m3.weight, 2)

        m4 = Mark.from_raw({"value": 5, "вес": "3.0"})
        self.assertEqual(m4.value, 5)
        self.assertEqual(m4.weight, 3)

        # Кортеж со строковым/дробным весом
        m5 = Mark.from_raw((5, "2.0"))
        self.assertEqual(m5.weight, 2)

        m6 = Mark.from_raw((4, None))
        self.assertEqual(m6.weight, 1)

    def test_mark_equality_with_dict(self):
        m = Mark(5, weight=2)
        self.assertEqual(m, {"value": 5, "weight": 2})
        self.assertEqual(m, {"value": 5, "weight": "2"})
        self.assertEqual(m, {"value": 5, "coef": 2})
        self.assertEqual(m, 5)

        m_single = Mark(5, weight=1)
        self.assertEqual(m_single, {"value": 5, "weight": None})
        self.assertEqual(m_single, 5)

    def test_clean_name(self):
        self.assertEqual(clean_name(None), "")
        self.assertEqual(clean_name(""), "")
        self.assertEqual(clean_name("   "), "")
        self.assertEqual(clean_name("  Геометрия  "), "Геометрия")
        self.assertEqual(clean_name("Русский \t\n  язык"), "Русский язык")
        self.assertEqual(clean_name(123), "123")

    def test_format_average_zero_division(self):
        self.assertEqual(format_average(0, 0), "0.00")
        self.assertEqual(format_average(5, 0), "0.00")
        self.assertEqual(format_average(5, -1), "0.00")
        self.assertEqual(format_average(-5, 0), "0.00")

    def test_subject_grades_empty_marks(self):
        sub1 = SubjectGrades("Информатика", ())
        self.assertEqual(sub1.count, 0)
        self.assertEqual(sub1.total, 0)
        self.assertEqual(sub1.total_weight, 0)

        sub2 = SubjectGrades("Информатика", None)
        self.assertEqual(sub2.count, 0)
        self.assertEqual(sub2.total, 0)

    def test_subject_grades_official_average_normalization(self):
        sub = SubjectGrades("Физика", (5, 4), official_average="  4,67  ")
        self.assertEqual(sub.official_average, "4.67")

    def test_subject_report_official_average_precedence(self):
        # 1. При наличии official_average он отдается без пересчета
        rep = SubjectReport(
            name="Химия",
            marks=(Mark(5), Mark(4)),
            total=9,
            count=2,
            average=4.50,
            badge="green",
            on_five=True,
            fives_needed=0,
            official_average="4.65",
        )
        self.assertEqual(rep.average_str, "4.65")

        # 2. Без official_average вычисляется по total/count
        rep2 = SubjectReport(
            name="Химия",
            marks=(Mark(5), Mark(4)),
            total=9,
            count=2,
            average=4.50,
            badge="green",
            on_five=True,
            fives_needed=0,
            official_average="",
        )
        self.assertEqual(rep2.average_str, "4.50")

        # 3. Пустой предмет без official_average
        rep3 = SubjectReport(
            name="Химия",
            marks=(),
            total=0,
            count=0,
            average=0.0,
            badge="red",
            on_five=False,
            fives_needed=0,
            official_average="",
        )
        self.assertEqual(rep3.average_str, "—")
        self.assertEqual(rep3.marks_text(), "—")


class TestGradeMathRobustness(unittest.TestCase):
    """Стресс-тестирование grade_math.py на краевые случаи."""

    def test_validate_marks_weights_edge_cases(self):
        # Словари с различными представлениями веса
        raw = [
            {"value": 5, "weight": None},
            {"value": 5, "weight": "2"},
            {"value": 5, "weight": 2.0},
            {"value": 5, "weight": "2.0"},
            {"value": 4, "coef": 2},
            {"value": 4, "вес": "3"},
            (5, None),
            (5, "2"),
            (5, 2.0),
        ]
        marks = gm.validate_marks(raw)
        self.assertEqual(len(marks), 9)
        self.assertEqual(marks[0].weight, 1)
        self.assertEqual(marks[1].weight, 2)
        self.assertEqual(marks[2].weight, 2)
        self.assertEqual(marks[3].weight, 2)
        self.assertEqual(marks[4].weight, 2)
        self.assertEqual(marks[5].weight, 3)
        self.assertEqual(marks[6].weight, 1)
        self.assertEqual(marks[7].weight, 2)
        self.assertEqual(marks[8].weight, 2)

    def test_validate_marks_empty(self):
        self.assertEqual(gm.validate_marks([]), ())
        self.assertEqual(gm.validate_marks(None), ())
        self.assertEqual(gm.validate_marks(()), ())

    def test_math_functions_zero_division(self):
        # Ни одна функция не должна падать с ZeroDivisionError при n <= 0
        self.assertEqual(gm.average(0, 0), 0.0)
        self.assertEqual(gm.average(10, 0), 0.0)
        self.assertEqual(gm.average(10, -5), 0.0)

        self.assertEqual(gm.badge_for(0, 0), "red")
        self.assertEqual(gm.badge_for(10, 0), "red")
        self.assertEqual(gm.badge_for(10, -2), "red")

        self.assertFalse(gm.is_on_five(0, 0))
        self.assertFalse(gm.is_on_five(10, 0))
        self.assertFalse(gm.is_on_five(10, -1))

        self.assertEqual(gm.fives_needed(0, 0), 0)
        self.assertEqual(gm.fives_needed(10, 0), 0)
        self.assertEqual(gm.fives_needed(10, -1), 0)

        self.assertEqual(gm.safe_streak(0, 0, 4), 0)
        self.assertEqual(gm.first_drop(0, 0, 4), 1)
        self.assertEqual(gm.reserve_map(0, 0), {})

    def test_build_report_empty_subject(self):
        # Пустой предмет без официального среднего
        sub = SubjectGrades("ОБЖ", ())
        rep = gm.build_report(sub)
        self.assertEqual(rep.name, "ОБЖ")
        self.assertEqual(rep.count, 0)
        self.assertEqual(rep.total, 0)
        self.assertEqual(rep.average, 0.0)
        self.assertEqual(rep.average_str, "—")
        self.assertFalse(rep.on_five)
        self.assertEqual(rep.badge, "red")
        self.assertEqual(rep.fives_needed, 0)
        self.assertEqual(rep.reserve, {})

    def test_build_report_empty_subject_with_official_average(self):
        # Пустой предмет, но с официальным баллом дневника >= 4.5
        sub = SubjectGrades("ОБЖ", (), official_average="4.80")
        rep = gm.build_report(sub)
        self.assertEqual(rep.count, 0)
        self.assertEqual(rep.average_str, "4.80")
        self.assertTrue(rep.on_five)
        self.assertEqual(rep.badge, "green")

        # Пустой предмет с официальным баллом < 3.5
        sub_bad = SubjectGrades("ОБЖ", (), official_average="3.20")
        rep_bad = gm.build_report(sub_bad)
        self.assertEqual(rep_bad.average_str, "3.20")
        self.assertFalse(rep_bad.on_five)
        self.assertEqual(rep_bad.badge, "red")

        # Пустой предмет с официальным баллом 3.5..4.49
        sub_mid = SubjectGrades("ОБЖ", (), official_average="4.10")
        rep_mid = gm.build_report(sub_mid)
        self.assertEqual(rep_mid.average_str, "4.10")
        self.assertFalse(rep_mid.on_five)
        self.assertEqual(rep_mid.badge, "yellow")

    def test_sort_reports_with_empty_and_official(self):
        r1 = gm.build_report(SubjectGrades("Химия", (3, 3)))             # 3.00
        r2 = gm.build_report(SubjectGrades("Физика", (4, 4)))            # 4.00
        r3 = gm.build_report(SubjectGrades("ОБЖ", (), official_average="3.50"))
        r4 = gm.build_report(SubjectGrades("Астрономия", ()))            # без оценок -> в конец

        sorted_reps = gm.sort_reports([r2, r4, r1, r3])
        names = [r.name for r in sorted_reps]
        # Худшие сверху: Химия (3.00), затем ОБЖ (3.50), затем Физика (4.00), Астрономия в конце
        self.assertEqual(names, ["Химия", "ОБЖ", "Физика", "Астрономия"])

    def test_explain_empty_subject(self):
        sub = SubjectGrades("ОБЖ", ())
        rep = gm.build_report(sub)
        text = gm.explain(rep)
        self.assertIn("Оценок за период нет", text)


class TestParsersRobustness(unittest.TestCase):
    """Стресс-тестирование parsers.py."""

    def test_extract_official_average_various_keys_and_types(self):
        # Различные имена ключей
        cases = [
            ({"average": 4.5}, "4.50"),
            ({"average": "4,75"}, "4.75"),
            ({"average": "4.15"}, "4.15"),
            ({"average": 5}, "5.00"),
            ({"average": "5"}, "5.00"),
            ({"avg": 4.2}, "4.20"),
            ({"mean": "3.85"}, "3.85"),
            ({"средний": "4,40"}, "4.40"),
            ({"среднийБалл": "4.65"}, "4.65"),
            ({"averageMark": 4.8}, "4.80"),
            ({"avgMark": 4.1}, "4.10"),
            ({"averageScore": "4.90"}, "4.90"),
            ({"middleMark": 3.75}, "3.75"),
            ({"official_average": "4.55"}, "4.55"),
            ({"rating": 4.3}, "4.30"),
            # Вложенные контейнеры
            ({"statistics": {"average": 4.6}}, "4.60"),
            ({"summary": {"avg": "4.70"}}, "4.70"),
            ({"data": {"average": 4.5}}, "4.50"),
            # Невалидные значения
            ({"average": None}, ""),
            ({"average": "н/а"}, ""),
            ({"average": 0}, ""),
            ({"average": 10}, ""),
            ({"average": "abc"}, ""),
            ({}, ""),
            ("не словарь", ""),
        ]
        for node, expected in cases:
            with self.subTest(node=node):
                self.assertEqual(_extract_official_average(node), expected)

    def test_extract_row_average_html_table(self):
        # Таблица без классов average, но с колонкой среднего
        html = """
        <table>
          <tr><th>Предмет</th><th>Оценки</th><th>Ср. балл</th></tr>
          <tr><td>Биология</td><td>5</td><td>4</td><td>4,50</td></tr>
        </table>
        """
        subjects = parse_grades_html(html)
        self.assertEqual(len(subjects), 1)
        self.assertEqual(subjects[0].name, "Биология")
        self.assertEqual(subjects[0].marks, (5, 4))
        self.assertEqual(subjects[0].official_average, "4.50")

    def test_extract_row_average_html_data_attr(self):
        html = """
        <div data-subject="География" data-average="4.65">
          <span data-mark="5">5</span>
          <span data-mark="4">4</span>
        </div>
        """
        subjects = parse_grades_html(html)
        self.assertEqual(len(subjects), 1)
        self.assertEqual(subjects[0].official_average, "4.65")

    def test_parse_grades_payload_preserves_official_average(self):
        payload = {
            "items": [
                {
                    "subjectName": "Алгебра",
                    "grades": [5, 4, 4],
                    "averageMark": 4.33,
                }
            ]
        }
        subjects = parse_grades_payload(payload)
        self.assertEqual(len(subjects), 1)
        self.assertEqual(subjects[0].name, "Алгебра")
        self.assertEqual(subjects[0].official_average, "4.33")

    def test_parse_marks_api_weights_robustness(self):
        payload = {
            "payload": [
                {"subject_name": "Физика", "value": "5", "weight": None, "date": "2026-10-01"},
                {"subject_name": "Физика", "value": "4", "weight": "2", "date": "2026-10-02"},
                {"subject_name": "Физика", "value": "5", "weight": 2.0, "date": "2026-10-03"},
                {"subject_name": "Физика", "value": "5", "weight": "2.0", "date": "2026-10-04"},
                {"subject_name": "Физика", "value": "5", "weight": "null", "date": "2026-10-05"},
            ]
        }
        subjects = parse_marks_api(payload, "1 триместр")
        self.assertEqual(len(subjects), 1)
        marks = subjects[0].marks
        self.assertEqual(len(marks), 5)
        self.assertEqual(marks[0].weight, 1)
        self.assertEqual(marks[1].weight, 2)
        self.assertEqual(marks[2].weight, 2)
        self.assertEqual(marks[3].weight, 2)
        self.assertEqual(marks[4].weight, 1)

    def test_parse_marks_api_with_subject_averages(self):
        payload = {
            "payload": [
                {"subject_name": "Химия", "value": "5", "date": "2026-10-01"},
            ],
            "averages": {
                "Химия": 4.85,
            },
        }
        subjects = parse_marks_api(payload, "1 триместр")
        self.assertEqual(len(subjects), 1)
        self.assertEqual(subjects[0].official_average, "4.85")

    def test_validate_subjects_keeps_subject_with_official_average(self):
        # Предмет без отметок, но с официальным средним баллом НЕ отбрасывается
        sub = SubjectGrades("Музыка", (), official_average="4.50")
        validated = validate_subjects([sub])
        self.assertEqual(len(validated), 1)
        self.assertEqual(validated[0].name, "Музыка")
        self.assertEqual(validated[0].official_average, "4.50")

        # Предмет без отметок и без официального среднего отбрасывается
        sub_empty = SubjectGrades("Музыка", ())
        self.assertEqual(validate_subjects([sub_empty]), [])

        # Предмет с пустым именем отбрасывается
        sub_noname = SubjectGrades("   ", (5,))
        self.assertEqual(validate_subjects([sub_noname]), [])


class TestDiaryClientRobustness(unittest.TestCase):
    """Тестирование diary_client.py: сессии, слияние и ошибки."""

    def test_merge_best_preserves_official_average(self):
        g1 = [SubjectGrades("Алгебра", (5, 4), official_average="4.50")]
        g2 = [SubjectGrades("Алгебра", (5, 4, 4))]  # больше оценок, но без official_average
        merged = diary_client._merge_best(g1, g2)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].marks, (5, 4, 4))
        self.assertEqual(merged[0].official_average, "4.50")

        # Наоборот: первый без official_average, второй с ним
        g3 = [SubjectGrades("Физика", (5, 5))]
        g4 = [SubjectGrades("Физика", (5,), official_average="4.90")]
        merged2 = diary_client._merge_best(g3, g4)
        self.assertEqual(len(merged2), 1)
        self.assertEqual(merged2[0].marks, (5, 5))
        self.assertEqual(merged2[0].official_average, "4.90")

    def test_fetch_session_expired_on_redirect_during_wait(self):
        with patch("config.session_exists", return_value=True), \
                patch("playwright.sync_api.sync_playwright") as sp:
            pw = sp.return_value.start.return_value
            browser = pw.chromium.launch.return_value
            context = browser.new_context.return_value
            page = context.new_page.return_value

            # Изначально страница загрузилась, но в процессе ожидания URL стал ESIA
            page.url = "https://esia.gosuslugi.ru/idp/login"
            client = diary_client.DiaryClient(
                settings={"headless": True},
                selectors={"expired_url_hints": ["esia.gosuslugi.ru"]},
            )
            with self.assertRaises(SessionExpired):
                client.fetch(period=PERIOD)

    def test_fetch_session_expired_on_auth_401_error(self):
        with patch("config.session_exists", return_value=True), \
                patch("playwright.sync_api.sync_playwright") as sp:
            pw = sp.return_value.start.return_value
            browser = pw.chromium.launch.return_value
            context = browser.new_context.return_value
            page = context.new_page.return_value
            page.url = "https://school.mosreg.ru/marks"
            page.content.return_value = "<html><body></body></html>"

            def goto_handler(url, *args, **kwargs):
                # Эмулируем ответ 401 на API ручку
                resp = MagicMock()
                resp.url = "https://school.mosreg.ru/api/family/web/v1/marks"
                resp.status = 401
                resp.headers = {"content-type": "application/json"}
                # Вызываем response-слушателя
                handler = page.on.call_args[0][1]
                handler(resp)

            page.goto.side_effect = goto_handler
            client = diary_client.DiaryClient(
                settings={"headless": True},
                selectors={"marks_api_path": "/api/family/web/v1/marks"},
            )
            with self.assertRaises(SessionExpired):
                client.fetch(period=PERIOD)

    def test_fetch_session_expired_on_login_form_html(self):
        with patch("config.session_exists", return_value=True), \
                patch("playwright.sync_api.sync_playwright") as sp:
            pw = sp.return_value.start.return_value
            browser = pw.chromium.launch.return_value
            context = browser.new_context.return_value
            page = context.new_page.return_value
            page.url = "https://school.mosreg.ru/welcome"
            page.content.return_value = "<html><body>Войти через Госуслуги (ЕСИА)</body></html>"

            client = diary_client.DiaryClient(settings={"headless": True})
            with self.assertRaises(SessionExpired):
                client.fetch(period=PERIOD)


if __name__ == "__main__":
    unittest.main(verbosity=2)
