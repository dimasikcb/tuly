# -*- coding: utf-8 -*-
"""Offline tests for the 'Дневник МО' (diary) tab integration in the hub.

These tests never import tools.pyw (a .pyw GUI module) and never touch the
network or a browser. Everything is checked by parsing the source with `ast`,
by plain text / regex searches, by filesystem checks, and by loading the
diary-grades math core in an isolated namespace.
"""
import ast
import importlib.util
import re
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

APP_FILE = ROOT / "tools.pyw"
DIARY = ROOT / "diary-grades"
ELLIPSIS = "\u2026"          # U+2026 HORIZONTAL ELLIPSIS
_MISSING = object()

REQUIRED_METHODS = {
    "_build", "reload", "on_show", "_render_rows", "_refresh", "_open_app",
}
COLUMN_HEADERS = (
    "Предмет", "Средний балл", "Текущие оценки", "До 5-ки", "Запас прочности",
)


def _load_source():
    """Read tools.pyw afresh (the main agent may still be editing it)."""
    source = APP_FILE.read_text(encoding="utf-8")
    tree = ast.parse(source, filename=str(APP_FILE))
    return source, tree


def _module_assign(tree, name):
    """Return the module-level Assign/AnnAssign whose target is `name`."""
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id == name:
                return node
    return None


def _find_factories(tree):
    """Return the Assign/AnnAssign whose target attribute is `factories`."""
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Attribute) and target.attr == "factories":
                return node
    return None


def _find_class(tree, name):
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == name:
            return node
    return None


def _node_source(source, node):
    """Source text of a node, with a line-slicing fallback."""
    segment = ast.get_source_segment(source, node)
    if segment is None:
        lines = source.splitlines(keepends=True)
        segment = "".join(lines[node.lineno - 1:node.end_lineno])
    return segment


def _hub_chunk(source):
    """Text of class Hub from its header up to (but not including) def _build."""
    start = re.search(r"^class Hub\b", source, re.M)
    if start is None:
        return ""
    build = re.search(r"^\s*def _build\b", source[start.end():], re.M)
    end = start.end() + build.start() if build else len(source)
    return source[start.start():end]


def _load_module(name, path):
    """Load a file as a module in an isolated namespace.

    The module is registered in sys.modules BEFORE exec_module: dataclasses
    with `from __future__ import annotations` resolve cls.__module__ there.
    """
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


class ToolsPywDiaryTabTest(unittest.TestCase):
    """Checks 1-6 and 9 against tools.pyw (AST + text, no import)."""

    def test_nav_has_diary_between_other_programs(self):
        _, tree = _load_source()
        node = _module_assign(tree, "NAV")
        self.assertIsNotNone(node, "NAV assignment not found at module level")
        nav = ast.literal_eval(node.value)
        self.assertIsInstance(nav, (tuple, list), "NAV must be a tuple/list literal")

        indices = [i for i, entry in enumerate(nav)
                   if isinstance(entry, (tuple, list)) and entry and entry[0] == "diary"]
        self.assertEqual(len(indices), 1, "NAV must contain exactly one 'diary' entry")
        idx = indices[0]
        with self.subTest("header"):
            self.assertIn("Дневник МО", " ".join(str(x) for x in nav[idx]),
                          "diary entry must carry the 'Дневник МО' header")
        with self.subTest("position"):
            self.assertGreater(idx, 0, "diary must not be the first nav item")
            self.assertLess(idx, len(nav) - 1, "diary must not be the last nav item")

    def test_headers_has_diary_key(self):
        _, tree = _load_source()
        node = _module_assign(tree, "HEADERS")
        self.assertIsNotNone(node, "HEADERS assignment not found at module level")
        headers = ast.literal_eval(node.value)
        self.assertIsInstance(headers, dict, "HEADERS must be a dict literal")
        self.assertIn("diary", headers, "HEADERS has no 'diary' key")

    def test_factories_diary_lambda_builds_diary_panel(self):
        source, tree = _load_source()
        node = _find_factories(tree)
        self.assertIsNotNone(node, "self.factories assignment not found")
        self.assertIsInstance(node.value, ast.Dict,
                              "self.factories must be a dict literal")
        entries = {}
        for key, value in zip(node.value.keys, node.value.values):
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                entries[key.value] = value
        self.assertIn("diary", entries, "self.factories has no 'diary' key")
        factory = entries["diary"]
        self.assertIsInstance(factory, ast.Lambda,
                              "the 'diary' factory must be a lambda")
        self.assertIn("DiaryGradesPanel", ast.unparse(factory),
                      "the lambda must create DiaryGradesPanel")
        self.assertIn("DiaryGradesPanel", _hub_chunk(source),
                      "DiaryGradesPanel must appear in class Hub source")

    def test_diary_panel_class_inherits_panel_and_has_methods(self):
        _, tree = _load_source()
        cls = _find_class(tree, "DiaryGradesPanel")
        self.assertIsNotNone(cls, "class DiaryGradesPanel not found in tools.pyw")
        base_names = set()
        for base in cls.bases:
            if isinstance(base, ast.Name):
                base_names.add(base.id)
            elif isinstance(base, ast.Attribute):
                base_names.add(base.attr)
        self.assertIn("Panel", base_names,
                      "DiaryGradesPanel must inherit from Panel")
        methods = {n.name for n in cls.body
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
        missing = REQUIRED_METHODS - methods
        self.assertEqual(missing, set(),
                         "DiaryGradesPanel is missing methods: %s" % sorted(missing))

    def test_diary_panel_has_five_column_headers(self):
        source, tree = _load_source()
        cls = _find_class(tree, "DiaryGradesPanel")
        self.assertIsNotNone(cls, "class DiaryGradesPanel not found in tools.pyw")
        body = _node_source(source, cls)
        for header in COLUMN_HEADERS:
            with self.subTest(header=header):
                self.assertTrue(header in body,
                                "column header %r missing in DiaryGradesPanel" % header)

    def test_status_bar_hint_updated_to_ctrl_1_9(self):
        source, _ = _load_source()
        self.assertTrue(("Ctrl+1" + ELLIPSIS + "9") in source,
                        "status bar must advertise Ctrl+1…9")
        self.assertFalse(("Ctrl+1" + ELLIPSIS + "7") in source,
                         "old Ctrl+1…7 hint must be gone")

    def test_tools_pyw_stays_syntactically_valid(self):
        source = APP_FILE.read_text(encoding="utf-8")
        try:
            ast.parse(source, filename=str(APP_FILE))
        except SyntaxError as exc:
            self.fail("ast.parse(tools.pyw) raised SyntaxError: %s" % exc)
        try:
            code = compile(source, "tools.pyw", "exec")
        except SyntaxError as exc:
            self.fail("compile(tools.pyw) raised SyntaxError: %s" % exc)
        self.assertIsInstance(code, types.CodeType,
                              "compile(source, 'tools.pyw', 'exec') must work")


class DiaryFilesTest(unittest.TestCase):
    """Checks 7-8: subproject layout and hub README."""

    def test_diary_grades_layout(self):
        self.assertTrue(DIARY.is_dir(), "diary-grades/ directory is missing")
        for rel in ("app.pyw", "run.bat", "grade_math.py", "config.py"):
            with self.subTest(rel=rel):
                self.assertTrue((DIARY / rel).is_file(),
                                "diary-grades/%s is missing" % rel)
        self.assertTrue((DIARY / "data").is_dir(),
                        "diary-grades/data/ directory is missing")
        self.assertTrue((DIARY / "agents" / "00-INDEX.md").is_file(),
                        "diary-grades/agents/00-INDEX.md is missing")

    def test_hub_readme_mentions_diary(self):
        readme = ROOT / "README.md"
        self.assertTrue(readme.is_file(), "hub README.md is missing")
        text = readme.read_text(encoding="utf-8")
        self.assertTrue("Дневник МО" in text,
                        "hub README.md does not mention 'Дневник МО'")


class GradeMathCoreTest(unittest.TestCase):
    """Check 10: math core loaded in an isolated namespace."""

    def test_core_formulas(self):
        gm_name = "_diary_grade_math_under_test"
        prev = sys.modules.get("models", _MISSING)
        try:
            _load_module("models", DIARY / "models.py")
            gm = _load_module(gm_name, DIARY / "grade_math.py")
            cases = [
                ("fives_needed(17, 4)", gm.fives_needed(17, 4), 2),
                ("safe_streak(46, 10, 4)", gm.safe_streak(46, 10, 4), 2),
                ("safe_streak(46, 10, 3)", gm.safe_streak(46, 10, 3), 0),
                ("badge_for(34, 10)", gm.badge_for(34, 10), "red"),
                ("badge_for(35, 10)", gm.badge_for(35, 10), "yellow"),
                ("badge_for(45, 10)", gm.badge_for(45, 10), "green"),
            ]
            for label, got, expected in cases:
                with self.subTest(case=label):
                    self.assertEqual(got, expected)
        finally:
            sys.modules.pop(gm_name, None)
            if prev is _MISSING:
                sys.modules.pop("models", None)
            else:
                sys.modules["models"] = prev


class DiaryRefreshAllSubjectsTest(unittest.TestCase):
    """Мост обновления сохраняет ВСЕ предметы и хаб умеет автообновление."""

    def test_bridge_save_keeps_all_subjects(self):
        import json
        import subprocess
        source, tree = _load_source()
        preamble = ast.literal_eval(_module_assign(tree, "_DIARY_BRIDGE_PREAMBLE").value)
        code = preamble + """
import tempfile
from pathlib import Path
from models import FetchResult, SubjectGrades
config.CACHE_FILE = Path(tempfile.mkdtemp()) / 'cache.json'
save(FetchResult((SubjectGrades('Обществознание', (4, 4, 4)),),
                 '1 триместр', 'live', ''))
print(json.dumps({'cache': json.loads(config.CACHE_FILE.read_text(encoding='utf-8'))}))
"""
        compile(code, "_DIARY_BRIDGE_PREAMBLE", "exec")
        res = subprocess.run([sys.executable, "-c", code], cwd=str(DIARY),
                             capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(res.returncode, 0, res.stderr[:300])
        lines = res.stdout.strip().splitlines()
        report = json.loads(lines[-2])
        cache = json.loads(lines[-1])["cache"]
        expected = len(json.loads(
            (DIARY / "real_grades.json").read_text(encoding="utf-8-sig"))["subjects"])
        self.assertEqual(report["subjects"], expected)
        self.assertEqual(report["with_marks"], 1)
        self.assertEqual(len(cache["subjects"]), expected)
        self.assertEqual(sum(1 for s in cache["subjects"] if s["marks"]), 1)

    def test_auto_refresh_is_session_gated(self):
        source, tree = _load_source()
        cls = _find_class(tree, "DiaryGradesPanel")
        body = "".join(source.splitlines(keepends=True)[cls.lineno - 1:cls.end_lineno])
        for marker in ("_maybe_auto_refresh", "auto_refresh_min", "session_exists",
                       "Автообновление всех предметов", "_last_fetch"):
            with self.subTest(marker=marker):
                self.assertIn(marker, body)
        preamble = ast.literal_eval(
            _module_assign(tree, "_DIARY_BRIDGE_PREAMBLE").value)
        self.assertIn("expected_subjects", preamble)
        self.assertIn("with_marks", preamble)


class DiaryPendingSubjectsTest(unittest.TestCase):
    """Пустые предметы не должны молча исчезать и превращать таблицу в «одну оценку»."""

    def test_import_bridge_keeps_empty_subjects(self):
        source, tree = _load_source()
        node = _module_assign(tree, "_DIARY_IMPORT_CODE")
        self.assertIsNotNone(node)
        code = ast.literal_eval(node.value)
        compile(code, "_DIARY_IMPORT_CODE", "exec")
        for marker in ("pending", "with_marks", "read_text", "clean_name"):
            with self.subTest(marker=marker):
                self.assertIn(marker, code)

    def test_panel_marks_empty_rows(self):
        source, tree = _load_source()
        cls = _find_class(tree, "DiaryGradesPanel")
        self.assertIsNotNone(cls)
        body = "".join(source.splitlines(keepends=True)[cls.lineno - 1:cls.end_lineno])
        for marker in ("empty = not report.count", "нет оценок", "без оценок:",
                       "ЧАСТИЧНО", "с оценками"):
            with self.subTest(marker=marker):
                self.assertIn(marker, body)

    def test_empty_reports_go_last(self):
        prev = sys.modules.get("models", _MISSING)
        gm_name = "_diary_tab_pending_math"
        try:
            models = _load_module("models", DIARY / "models.py")
            gm = _load_module(gm_name, DIARY / "grade_math.py")
            reports = gm.build_reports([
                models.SubjectGrades("Математика", (), "", "pending"),
                models.SubjectGrades("Обществознание", (4, 4, 4)),
            ])
            self.assertEqual([r.name for r in gm.sort_reports(reports)],
                             ["Обществознание", "Математика"])
        finally:
            sys.modules.pop(gm_name, None)
            if prev is _MISSING:
                sys.modules.pop("models", None)
            else:
                sys.modules["models"] = prev


class DiaryProfileSubjectsTest(unittest.TestCase):
    """Профиль портала отдаёт 18 предметов; 16 имеют оценки, 2 — пока нет."""

    def test_template_has_all_profile_subjects(self):
        import json
        raw = json.loads((DIARY / "real_grades.json").read_text(encoding="utf-8-sig"))
        names = {str(item["name"]).casefold() for item in raw["subjects"]}
        self.assertEqual(len(raw["subjects"]), 18)
        self.assertIn("вероятность и статистика", names)
        self.assertIn("математика", names)
        empty = {str(item["name"]).casefold()
                 for item in raw["subjects"] if not item.get("marks")}
        self.assertEqual(empty, {"вероятность и статистика", "математика"})

    def test_cache_has_both_empty_subjects(self):
        import json
        cache = json.loads((DIARY / "data" / "grades_cache.json").read_text(encoding="utf-8"))
        self.assertEqual(len(cache["subjects"]), 18)
        empty = [s["name"] for s in cache["subjects"] if not s.get("marks")]
        # Любой предмет без оценок — только из известных пустых предметов профиля
        self.assertTrue(set(empty).issubset({"Вероятность и статистика", "Математика"}))
        self.assertGreaterEqual(sum(1 for s in cache["subjects"] if s.get("marks")), 16)
        self.assertGreaterEqual(sum(len(s["marks"]) for s in cache["subjects"]), 124)
        self.assertEqual(cache["source"], "live")

    def test_gap_detection_ignores_known_empty(self):
        source, tree = _load_source()
        cls = _find_class(tree, "DiaryGradesPanel")
        body = "".join(source.splitlines(keepends=True)[cls.lineno - 1:cls.end_lineno])
        for marker in ("SUBJECTS_FILE", "known_empty", "нет оценок в периоде"):
            with self.subTest(marker=marker):
                self.assertIn(marker, body)


if __name__ == "__main__":
    unittest.main()
