"""Офлайн-тесты парсеров оценок. Запуск из папки проекта:

    python -X utf8 -m unittest discover -s tests -v
    python -X utf8 -m unittest tests.test_parsers -v
"""
from __future__ import annotations

import inspect
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import parsers
from models import ParseError, SubjectGrades
from parsers import (
    merge_subjects,
    parse_file,
    parse_grades_html,
    parse_grades_payload,
    parse_json_responses,
    parse_marks_api,
    validate_subjects,
)


def _names(subjects):
    return [s.name for s in subjects]


def _by_name(subjects):
    return {s.name: tuple(s.marks) for s in subjects}


class TestHtmlTable(unittest.TestCase):
    """Табличная вёрстка и отсев среднего балла/дат/чисел 6..10."""

    def test_table_average_4_50_is_not_a_mark(self):
        html = """
        <table>
          <tr><th>Предмет</th><th>Оценки</th><th>Средний балл</th></tr>
          <tr><td>Математика</td><td>5</td><td>4</td><td>4,50</td></tr>
          <tr><td>Русский язык</td><td>3</td><td>4</td><td>3,50</td></tr>
        </table>
        """
        subjects = parse_grades_html(html)
        self.assertEqual(_names(subjects), ["Математика", "Русский язык"])
        self.assertEqual(_by_name(subjects), {"Математика": (5, 4), "Русский язык": (3, 4)})

    def test_fractional_average_forms_are_ignored(self):
        html = (
            "<table><tr><td>Физика</td><td>4,50</td><td>4.5</td>"
            "<td>4,5</td><td>4.50</td><td>5</td></tr></table>"
        )
        subjects = parse_grades_html(html)
        self.assertEqual(_by_name(subjects), {"Физика": (5,)})

    def test_dates_and_6_to_10_are_not_marks(self):
        html = (
            "<table><tr><td>История</td><td>05.11</td><td>6</td>"
            "<td>10</td><td>0</td><td>9</td><td>3</td></tr></table>"
        )
        subjects = parse_grades_html(html)
        self.assertEqual(_by_name(subjects), {"История": (3,)})

    def test_row_without_subject_or_marks_is_dropped(self):
        html = """
        <nav><a href="/">Дневник</a><a href="/marks">Оценки</a></nav>
        <div class="menu">Главная</div>
        <table>
          <tr><td>Биология</td><td>10</td><td>05.11</td></tr>
          <tr><td>География</td><td>5</td></tr>
        </table>
        """
        subjects = parse_grades_html(html)
        self.assertEqual(_by_name(subjects), {"География": (5,)})

    def test_html_source_is_html(self):
        subjects = parse_grades_html(
            "<table><tr><td>Химия</td><td>5</td><td>4</td></tr></table>"
        )
        self.assertEqual(len(subjects), 1)
        self.assertEqual(subjects[0].source, "html")


class TestHtmlDivCards(unittest.TestCase):
    """Карточки с data-атрибутами и class mark/grade/score."""

    def test_div_cards_with_data_attributes(self):
        html = """
        <div data-subject="Русский язык">
          <span data-mark="5">5</span>
          <span class="mark">4</span>
        </div>
        <div data-subject="Литература">
          <span data-grade="5">5</span>
          <span class="score">4</span>
        </div>
        """
        subjects = parse_grades_html(html)
        self.assertEqual(_names(subjects), ["Русский язык", "Литература"])
        self.assertEqual(
            _by_name(subjects),
            {"Русский язык": (5, 4), "Литература": (5, 4)},
        )

    def test_data_subject_attribute_has_priority(self):
        html = """
        <div data-subject="Информатика">
          <span class="title">Не предмет</span>
          <span class="mark">5</span><span class="grade">3</span>
        </div>
        """
        subjects = parse_grades_html(html)
        self.assertEqual(_by_name(subjects), {"Информатика": (5, 3)})

    def test_html_navigation_only_returns_nothing(self):
        html = "<html><body><nav>Оценки Дневник</nav><ul><li>Меню</li></ul></body></html>"
        self.assertEqual(parse_grades_html(html), [])


class TestJsonPayload(unittest.TestCase):
    """Разные формы JSON-ответов SPA."""

    def test_nested_data_items_wrapper(self):
        payload = {
            "data": {
                "items": [
                    {"subjectName": "Физика", "grades": [5, 4, 5]},
                    {"discipline": "Химия", "marks": ["4", "5"]},
                ],
                "total": 2,
            }
        }
        subjects = parse_grades_payload(payload)
        self.assertEqual(_names(subjects), ["Физика", "Химия"])
        self.assertEqual(_by_name(subjects), {"Физика": (5, 4, 5), "Химия": (4, 5)})

    def test_list_of_mark_dicts_with_dates(self):
        payload = {
            "subject": {"name": "Математика"},
            "marks": [
                {"value": 5, "date": "05.11"},
                {"value": 4, "date": "06.11"},
            ],
        }
        subjects = parse_grades_payload(payload)
        self.assertEqual(_by_name(subjects), {"Математика": (5, 4)})

    def test_plain_subject_name_and_average_ignored(self):
        payload = {
            "subjectName": "Физика",
            "grades": [5, 4, 5],
            "average": 4.67,
            "period": "2 четверть",
        }
        subjects = parse_grades_payload(payload)
        self.assertEqual(_by_name(subjects), {"Физика": (5, 4, 5)})

    def test_discipline_with_average_and_string_marks(self):
        payload = {"discipline": "Химия", "average": 4.5, "marks": ["4", "5"]}
        subjects = parse_grades_payload(payload)
        self.assertEqual(_by_name(subjects), {"Химия": (4, 5)})

    def test_deduplicates_subjects_by_normalized_name(self):
        payload = {"result": [
            {"subject": "Математика", "grades": [5]},
            {"subject": "математика", "grades": [4]},
            {"subject": "  Математика  ", "grades": [3]},
        ]}
        subjects = parse_grades_payload(payload)
        self.assertEqual(_names(subjects), ["Математика"])
        self.assertEqual(subjects[0].marks, (5, 4, 3))

    def test_ignores_dates_out_of_range_and_na(self):
        payload = {
            "subject": "История",
            "grades": [5, 10, 6, 0, 4.5, "05.11", "н/а", 3, "5"],
        }
        subjects = parse_grades_payload(payload)
        self.assertEqual(_by_name(subjects), {"История": (5, 3, 5)})

    def test_nested_marks_dict(self):
        payload = {"subject": "Информатика", "marks": {"1": 5, "2": 4, "3": 3}}
        subjects = parse_grades_payload(payload)
        self.assertEqual(_by_name(subjects), {"Информатика": (5, 4, 3)})

    def test_json_source_is_json(self):
        subjects = parse_grades_payload({"subject": "Химия", "grades": [5]})
        self.assertEqual(subjects[0].source, "json")

    def test_json_string_payload_is_accepted(self):
        subjects = parse_grades_payload('{"subject":"Биология","grades":[5,4]}')
        self.assertEqual(_by_name(subjects), {"Биология": (5, 4)})


class TestMergeAndResponses(unittest.TestCase):
    """Слияние групп и мягкий фильтр URL."""

    def test_merge_subjects_preserves_first_appearance_order(self):
        first = [SubjectGrades("Математика", (5,)), SubjectGrades("Физика", (4,))]
        second = [SubjectGrades("Математика", (3, 4)), SubjectGrades("Химия", (5,))]
        merged = merge_subjects(first, second)
        self.assertEqual(_names(merged), ["Математика", "Физика", "Химия"])
        self.assertEqual(
            _by_name(merged),
            {"Математика": (5, 3, 4), "Физика": (4,), "Химия": (5,)},
        )

    def test_merge_subjects_with_no_groups(self):
        self.assertEqual(merge_subjects(), [])

    def test_parse_json_responses_soft_url_filter(self):
        responses = [
            ("https://x/api/diary/marks", {"subject": "Математика", "grades": [5, 4]}),
            ("https://x/static/menu.json", {"subject": "Физика", "grades": [3]}),
            ("https://x/static/menu.json", {"menu": ["оценки"]}),
        ]
        subjects = parse_json_responses(
            responses, {"json_url_keywords": ["mark", "diary"]}
        )
        self.assertEqual(
            _by_name(subjects), {"Математика": (5, 4), "Физика": (3,)}
        )

    def test_parse_json_responses_merges_same_subject(self):
        responses = [
            ("https://x/api/marks", {"subject": "Математика", "grades": [5]}),
            ("https://x/api/grades", {"subject": "Математика", "grades": [4, 3]}),
        ]
        subjects = parse_json_responses(responses)
        self.assertEqual(_by_name(subjects), {"Математика": (5, 4, 3)})


class TestParseFile(unittest.TestCase):
    """Файловый импорт через tempfile."""

    def _write(self, directory, name, content):
        path = Path(directory) / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_parse_file_html(self):
        html = (
            "<table><tr><td>Биология</td><td>5</td><td>4</td>"
            "<td>4,50</td></tr></table>"
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "grades.html", html)
            subjects = parse_file(path)
        self.assertEqual(_by_name(subjects), {"Биология": (5, 4)})
        self.assertEqual(subjects[0].source, "html")

    def test_parse_file_json(self):
        payload = {
            "data": {"items": [{"subjectName": "География", "grades": [5, 5]}]}
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "grades.json", json.dumps(payload, ensure_ascii=False))
            subjects = parse_file(path)
        self.assertEqual(_by_name(subjects), {"География": (5, 5)})

    def test_parse_file_txt_with_json_content(self):
        content = '{"subject":"Литература","grades":[4,5]}'
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "dump.txt", content)
            subjects = parse_file(path)
        self.assertEqual(_by_name(subjects), {"Литература": (4, 5)})

    def test_parse_file_json_array_of_responses(self):
        payload = [
            {"url": "https://x/api/marks", "payload": {"subject": "География", "grades": [5, 5]}},
            {"url": "https://x/api/menu", "payload": {"menu": []}},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "responses.json", json.dumps(payload, ensure_ascii=False))
            subjects = parse_file(path)
        self.assertEqual(_by_name(subjects), {"География": (5, 5)})

    def test_parse_file_raises_parse_error_on_garbage(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "junk.txt", "ничего полезного здесь нет")
            with self.assertRaises(ParseError) as ctx:
                parse_file(path)
        self.assertIn("junk.txt", str(ctx.exception))

    def test_parse_file_raises_parse_error_on_empty_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "empty.json", '{"menu": ["оценки", "дневник"]}')
            with self.assertRaises(ParseError):
                parse_file(path)

    def test_parse_file_raises_parse_error_on_navigation_only_html(self):
        html = "<html><body><nav>Оценки</nav><div class='menu'>Главная</div></body></html>"
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write(tmp, "nav.htm", html)
            with self.assertRaises(ParseError):
                parse_file(path)


class TestValidationAndImports(unittest.TestCase):
    """Нормализация и ограничения на зависимости."""

    def test_validate_subjects_drops_empty_and_invalid(self):
        raw = [
            SubjectGrades("  Математика  ", ("5", 4, 99)),
            SubjectGrades("   ", (5,)),
            SubjectGrades("Физика", ()),
        ]
        result = validate_subjects(raw)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].name, "Математика")
        self.assertEqual(result[0].marks, (5, 4))

    def test_validate_subjects_keeps_period_and_source(self):
        raw = [SubjectGrades("Химия", (5,), "2 четверть", "import")]
        result = validate_subjects(raw)
        self.assertEqual(result[0].period, "2 четверть")
        self.assertEqual(result[0].source, "import")

    def test_no_forbidden_imports(self):
        source = inspect.getsource(parsers)
        bad = re.findall(
            r"^\s*(?:from|import)\s+(bs4|lxml|playwright|tkinter|customtkinter)\b",
            source,
            flags=re.MULTILINE | re.IGNORECASE,
        )
        self.assertEqual(bad, [])

    def test_public_functions_have_docstrings_and_annotations(self):
        for name in parsers.__all__:
            func = getattr(parsers, name)
            self.assertTrue(func.__doc__, f"{name} без docstring")
            self.assertTrue(func.__annotations__, f"{name} без аннотаций")


class TestRealDiaryMarkup(unittest.TestCase):
    """Карточка предмета из реального дневника: чипы оценок и «средний балл»."""

    CARD = """
    <div data-subject="Обществознание" class="subject-card">
      <h3>Обществознание</h3>
      <div class="period">1 триместр 01 сент – 15 ноя</div>
      <div class="average">4.00</div><span class="average-label">средний балл</span>
      <span class="chip">4</span><span class="chip">4</span><span class="chip">4</span>
      <span class="date">15 ноя</span>
    </div>
    """

    def test_chips_without_mark_classes(self):
        subjects = parse_grades_html(self.CARD)
        self.assertEqual(len(subjects), 1)
        self.assertEqual(subjects[0].name, "Обществознание")
        self.assertEqual(subjects[0].marks, (4, 4, 4))

    def test_average_and_period_are_not_marks(self):
        subjects = parse_grades_html(self.CARD)
        self.assertNotIn(1, subjects[0].marks)
        self.assertEqual(len(subjects[0].marks), 3)

    def test_real_grades_template_import(self):
        path = Path(__file__).resolve().parents[1] / "real_grades.json"
        raw = json.loads(path.read_text(encoding="utf-8-sig"))
        expected = [
            (str(item["name"]), tuple(item.get("marks") or ()))
            for item in raw["subjects"] if item.get("marks")
        ]
        self.assertGreater(len(expected), 1,
                           "в шаблоне должны быть реальные оценки по всем предметам")
        self.assertEqual([(s.name, s.marks) for s in parse_file(path)], expected)
        self.assertEqual(dict(expected)["Обществознание"], (4, 4, 4))


class TestMarksApi(unittest.TestCase):
    """Реальный формат /api/family/web/v1/marks: плоский список, одна строка = оценка."""

    def test_groups_by_subject_and_sorts_by_date(self):
        payload = {"payload": [
            {"subject_name": "Алгебра", "value": "4", "date": "2026-09-05",
             "created_at": "2026-09-05T10:00:00", "id": 2},
            {"subject_name": "Химия", "value": 5, "date": "2026-09-01", "id": 1},
            {"subject_name": "Алгебра", "value": "5", "date": "2026-09-02",
             "created_at": "2026-09-02T09:00:00", "id": 3},
        ]}
        subjects = parse_marks_api(payload, "1 триместр")
        self.assertEqual(_by_name(subjects),
                         {"Алгебра": (5, 4), "Химия": (5,)})
        self.assertTrue(all(s.source == "live" for s in subjects))
        self.assertTrue(all(s.period == "1 триместр" for s in subjects))

    def test_drops_non_numeric_and_out_of_range(self):
        payload = {"payload": [
            {"subject_name": "Химия", "value": "НВ", "date": "2026-09-01"},
            {"subject_name": "Химия", "value": "4,50", "date": "2026-09-02"},
            {"subject_name": "Химия", "value": 0, "date": "2026-09-03"},
            {"subject_name": "Химия", "value": 6, "date": "2026-09-04"},
            {"subject_name": "Химия", "value": "3", "date": "2026-09-05"},
            {"subject_name": "", "value": "5", "date": "2026-09-06"},
            "мусор",
        ]}
        self.assertEqual(_by_name(parse_marks_api(payload)), {"Химия": (3,)})

    def test_accepts_bare_list_and_rejects_garbage(self):
        rows = [{"subject_name": "Информатика", "value": "5", "date": "2026-11-02"}]
        self.assertEqual(_by_name(parse_marks_api(rows)), {"Информатика": (5,)})
        for bad in (None, {}, {"payload": "не список"}, 42, "text"):
            with self.subTest(bad=bad):
                self.assertEqual(parse_marks_api(bad), [])

    def test_real_trimester_dump_matches_cache(self):
        """Живой дамп портала даёт 16 предметов и 124 числовые оценки."""
        path = (Path(__file__).resolve().parents[1]
                / "debug" / "probe_marks" / "trimester_marks.json")
        if not path.is_file():
            self.skipTest("дамп живого портала не сохранён")
        subjects = parse_marks_api(json.loads(path.read_text(encoding="utf-8")),
                                   "1 триместр")
        self.assertEqual(len(subjects), 16)
        self.assertEqual(sum(len(s.marks) for s in subjects), 124)
        self.assertEqual(_by_name(subjects)["Обществознание"], (4, 4, 4))

    def test_trimester_dump_extracts_weights(self):
        path = (Path(__file__).resolve().parents[1]
                / "debug" / "probe_marks" / "trimester_marks.json")
        if not path.is_file():
            self.skipTest("дамп живого портала не сохранён")
        subjects = parse_marks_api(json.loads(path.read_text(encoding="utf-8")), "1 триместр")
        eng = next(s for s in subjects if s.name == "Иностранный (английский) язык")
        self.assertEqual(eng.total, 90)
        self.assertEqual(eng.total_weight, 19)
        self.assertEqual(eng.count, 18)
        self.assertTrue(any(getattr(m, "weight", 1) == 2 for m in eng.marks))


class TestProfileSubjects(unittest.TestCase):
    """Профиль портала задаёт ПОЛНЫЙ список предметов, а /marks — только с оценками."""

    PROFILE = Path(__file__).resolve().parents[1] / "debug" / "probe_marks" / "001.json"
    MARKS = (Path(__file__).resolve().parents[1]
             / "debug" / "probe_marks" / "trimester_marks.json")

    def _profile_names(self):
        if not self.PROFILE.is_file():
            self.skipTest("дамп профиля портала не сохранён")
        return parsers.parse_profile_subjects(
            json.loads(self.PROFILE.read_text(encoding="utf-8")))

    def test_real_profile_has_18_subjects(self):
        names = self._profile_names()
        self.assertEqual(len(names), 18)
        self.assertIn("Вероятность и статистика", names)
        self.assertIn("Математика", names)
        self.assertIn("Иностранный (английский) язык", names)
        # Хвост «9 А УП ООО 2026-2027 уч. год, 9 кл.» отрезан.
        self.assertTrue(all("уч. год" not in n and "УП ООО" not in n for n in names))

    def test_subjects_missing_from_marks_are_detected(self):
        if not self.MARKS.is_file():
            self.skipTest("дамп оценок портала не сохранён")
        names = self._profile_names()
        marks = parse_marks_api(json.loads(self.MARKS.read_text(encoding="utf-8")),
                                "1 триместр")
        known = {s.name.casefold() for s in marks}
        missing = [n for n in names if n.casefold() not in known]
        self.assertEqual(missing, ["Вероятность и статистика", "Математика"])

    def test_template_covers_every_profile_subject(self):
        """Регрессия: предметы без оценок не должны пропадать из таблицы."""
        names = self._profile_names()
        raw = json.loads((Path(__file__).resolve().parents[1]
                          / "real_grades.json").read_text(encoding="utf-8-sig"))
        template = {str(item["name"]).casefold() for item in raw["subjects"]}
        self.assertEqual(template, {n.casefold() for n in names})

    def test_strips_class_suffix_and_skips_fakes(self):
        payload = {"children": [{"groups": [
            {"name": "Химия 9 А УП ООО 2026-2027 уч. год, 9 кл.",
             "subject_id": 1, "is_fake": False},
            {"name": "Физика 9 А УП ООО 2026-2027 уч. год, 9 кл.",
             "subject_id": 2, "is_fake": False},
            {"name": "Химия 9 Б УП ООО 2026-2027 уч. год, 9 кл.",
             "subject_id": 1, "is_fake": False},
            {"name": "Фантом 8 В УП ООО 2026-2027 уч. год, 8 кл.",
             "subject_id": 3, "is_fake": True},
        ]}]}
        self.assertEqual(parsers.parse_profile_subjects(payload), ("Физика", "Химия"))
        for bad in (None, {}, {"children": []}, 42, "мусор"):
            with self.subTest(bad=bad):
                self.assertEqual(parsers.parse_profile_subjects(bad), ())


if __name__ == "__main__":
    unittest.main(verbosity=2)
