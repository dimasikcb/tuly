"""Границы слоёв: логика без tkinter, GUI не тянет несуществующие имена."""
from __future__ import annotations

import ast
import importlib
import re
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
APP_FILE = ROOT / "tools.pyw"

LOGIC_MODULES = ("winbase", "apps_catalog", "bypass_core",
                 "leads_contacts", "leads_pool", "leads_mail", "leads2gis")


def _tree(path: Path) -> ast.Module:
    return ast.parse(path.read_text(encoding="utf-8"))


def _imported_modules(path: Path) -> set[str]:
    """Все модули, которые файл реально импортирует (по AST, не по тексту)."""
    names: set[str] = set()
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names.add(node.module.split(".")[0])
    return names


def _imported_names(path: Path) -> dict[str, list[str]]:
    """{модуль: [имена]} для всех `from <модуль> import ...` в файле."""
    result: dict[str, list[str]] = {}
    for node in ast.walk(_tree(path)):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            names = [alias.name for alias in node.names if alias.name != "*"]
            result.setdefault(node.module, []).extend(names)
    return result


# --------------------------------------------------------------------------- #
#  Хелперы инвариантов: всё через AST (импорты/вызовы на ЛЮБОЙ глубине,
#  включая функции и try/except), без текстового grep по файлам.
# --------------------------------------------------------------------------- #

_MISSING = object()
_MODE_RE = re.compile(r"[rwxabt+U]+")


def _call_name(call: ast.Call) -> str:
    """Точечное имя вызываемого объекта: open / path.open / tmp.replace / Tk."""
    func = call.func
    parts: list[str] = []
    while isinstance(func, ast.Attribute):
        parts.append(func.attr)
        func = func.value
    if isinstance(func, ast.Name):
        parts.append(func.id)
    return ".".join(reversed(parts))


def _kwarg(call: ast.Call, name: str):
    """Значение именованного аргумента-константы или _MISSING."""
    for kw in call.keywords:
        if kw.arg == name:
            return kw.value.value if isinstance(kw.value, ast.Constant) else _MISSING
    return _MISSING


def _is_open_call(call: ast.Call) -> bool:
    """open(...) и X.open(...) — оба варианта ввода-вывода CSV."""
    return _call_name(call).rsplit(".", 1)[-1] == "open"


def _open_mode(call: ast.Call) -> str:
    """Режим open(): keyword mode= или короткая позиционная константа."""
    for kw in call.keywords:
        if kw.arg == "mode" and isinstance(kw.value, ast.Constant) \
                and isinstance(kw.value.value, str):
            return kw.value.value
    for arg in call.args:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str) \
                and _MODE_RE.fullmatch(arg.value):
            return arg.value
    return ""


def _is_write_mode(mode: str) -> bool:
    return any(ch in mode for ch in "wax+")


def _is_tk_root_call(call: ast.Call) -> bool:
    """Tk(), tk.Tk(), tkinter.Tk() — создание корневого окна."""
    func = call.func
    if isinstance(func, ast.Name):
        return func.id == "Tk"
    if isinstance(func, ast.Attribute) and func.attr == "Tk":
        return isinstance(func.value, ast.Name) and func.value.id in {"tk", "tkinter"}
    return False


class LayersTest(unittest.TestCase):
    def test_logic_modules_do_not_import_tkinter(self) -> None:
        for name in LOGIC_MODULES:
            with self.subTest(module=name):
                self.assertNotIn("tkinter", _imported_modules(ROOT / f"{name}.py"),
                                 f"{name} не должен тянуть GUI")

    def test_gui_imports_existing_names(self) -> None:
        """Каждое имя, которое tools.pyw берёт из модуля, реально существует."""
        for module, names in _imported_names(APP_FILE).items():
            if not (ROOT / f"{module}.py").exists():
                continue
            loaded = importlib.import_module(module)
            for name in names:
                with self.subTest(module=module, name=name):
                    self.assertTrue(hasattr(loaded, name), f"{module}.{name} не найден")

    def test_gui_has_no_own_copy_of_moved_logic(self) -> None:
        """GUI не переопределяет то, что живёт в слоях, — иначе правки расходятся."""
        defined = {node.name for node in _tree(APP_FILE).body
                   if isinstance(node, (ast.FunctionDef, ast.ClassDef))}
        moved = {"bypass_cfg", "bypass_apply", "app_catalog", "ps_run",
                 "config_read", "is_admin", "normalize_apps", "happ_mode_read"}
        self.assertEqual(defined & moved, set())

    def test_winbase_is_bottom_layer(self) -> None:
        imports = _imported_modules(ROOT / "winbase.py")
        self.assertEqual(imports & set(LOGIC_MODULES), set())


class ArchitectureInvariantsTest(unittest.TestCase):
    """Новые инварианты слоёв: направления зависимостей, CSV I/O, цвета, GUI."""

    ALLOWED_LOCAL_DEPS = {
        "winbase": set(),
        "apps_catalog": {"winbase"},
        "bypass_core": {"winbase", "apps_catalog"},
        "leads_contacts": set(),
        "leads_pool": {"leads_contacts"},
        "leads_mail": {"leads_contacts", "leads_pool"},
        "leads2gis": {"leads_contacts", "leads_pool"},
    }

    def test_leads_layers_no_upward_imports(self) -> None:
        """Локальные импорты (на любой глубине) ⊆ разрешённой карты слоёв."""
        local = set(LOGIC_MODULES)
        for name in LOGIC_MODULES:
            with self.subTest(module=name):
                imported = _imported_modules(ROOT / f"{name}.py") & local
                extra = imported - self.ALLOWED_LOCAL_DEPS[name]
                self.assertEqual(
                    extra, set(),
                    f"{name} тянет запрещённые локальные зависимости: {sorted(extra)}",
                )
        # Явные запреты: снизу вверх (leads2gis -> leads_mail) и на GUI (tools).
        self.assertNotIn(
            "leads_mail", _imported_modules(ROOT / "leads2gis.py"),
            "leads2gis не должен лениво импортировать leads_mail",
        )
        for name in LOGIC_MODULES:
            with self.subTest(module=name, forbidden="tools"):
                self.assertNotIn(
                    "tools", _imported_modules(ROOT / f"{name}.py"),
                    f"{name} не должен тянуть GUI-модуль tools",
                )

    def test_pool_csv_io_utf8_sig(self) -> None:
        """README:706 — весь CSV I/O в leads_pool.py: utf-8-sig + newline=''."""
        opens = [n for n in ast.walk(_tree(ROOT / "leads_pool.py"))
                 if isinstance(n, ast.Call) and _is_open_call(n)]
        self.assertTrue(opens, "в leads_pool.py не найдено вызовов open()/Path.open()")
        for call in opens:
            with self.subTest(lineno=call.lineno):
                self.assertEqual(_kwarg(call, "encoding"), "utf-8-sig")
                self.assertEqual(_kwarg(call, "newline"), "")

    def test_pool_writes_go_through_pool_save(self) -> None:
        """README:708 — запись только внутри _pool_save и только через replace."""
        tree = _tree(ROOT / "leads_pool.py")
        save = next(
            (n for n in ast.walk(tree)
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
             and n.name == "_pool_save"),
            None,
        )
        self.assertIsNotNone(save, "в leads_pool.py нет def _pool_save")
        inside = {id(n) for n in ast.walk(save) if isinstance(n, ast.Call)}
        self.assertTrue(
            any(isinstance(n, ast.Call)
                and _call_name(n).rsplit(".", 1)[-1] == "replace"
                for n in ast.walk(save)),
            "в _pool_save нет вызова .replace( — запись не атомарна",
        )
        for call in (n for n in ast.walk(tree)
                     if isinstance(n, ast.Call) and _is_open_call(n)):
            mode = _open_mode(call)
            if _is_write_mode(mode):
                with self.subTest(lineno=call.lineno, mode=mode):
                    self.assertIn(
                        id(call), inside,
                        f"запись open({mode!r}) вне _pool_save — нарушает README:708",
                    )

    HEX_COLOR = re.compile(r"#[0-9a-fA-F]{3,8}\b")

    def test_hex_colors_only_in_theme_tokens(self) -> None:
        """Строковые hex-цвета (не комментарии) живут только в theme/tokens.py."""
        targets = (sorted(ROOT.glob("*.py")) + sorted(ROOT.glob("*.pyw"))
                   + sorted((ROOT / "theme").glob("*.py")))
        tokens = (ROOT / "theme" / "tokens.py").resolve()
        offenders: list[str] = []
        in_tokens = 0
        for path in targets:
            if "tests" in path.relative_to(ROOT).parts:
                continue
            for node in ast.walk(_tree(path)):
                if isinstance(node, ast.Constant) and isinstance(node.value, str) \
                        and self.HEX_COLOR.search(node.value):
                    if path.resolve() == tokens:
                        in_tokens += 1
                    else:
                        offenders.append(
                            f"{path.relative_to(ROOT)}:{node.lineno}: {node.value!r}")
        self.assertEqual(
            offenders, [],
            "hex-цвета вне theme/tokens.py: " + "; ".join(offenders),
        )
        self.assertGreater(in_tokens, 0,
                           "в theme/tokens.py не найдено ни одного hex-цвета")

    def test_logic_modules_have_no_gui_entrypoint(self) -> None:
        """Ни один слой логики не определяет main() с tkinter и не содержит tk.Tk()."""
        tk_roots = {"tk", "tkinter"}
        for name in LOGIC_MODULES:
            tree = _tree(ROOT / f"{name}.py")
            with self.subTest(module=name):
                for node in ast.walk(tree):
                    if isinstance(node, ast.Call) and _is_tk_root_call(node):
                        self.fail(
                            f"{name} создаёт tk.Tk() — GUI-вход в слое логики "
                            f"(строка {node.lineno})")
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) \
                            and node.name == "main":
                        for sub in ast.walk(node):
                            if isinstance(sub, ast.Name) and sub.id in tk_roots:
                                self.fail(f"{name}.main() ссылается на {sub.id}")
                            if isinstance(sub, ast.Call) and _is_tk_root_call(sub):
                                self.fail(f"{name}.main() создаёт Tk()")
                            if isinstance(sub, ast.Import) and any(
                                    a.name.split(".")[0] in tk_roots
                                    for a in sub.names):
                                self.fail(f"{name}.main() импортирует tkinter")
                            if isinstance(sub, ast.ImportFrom) and sub.module \
                                    and sub.module.split(".")[0] in tk_roots:
                                self.fail(f"{name}.main() импортирует tkinter")


if __name__ == "__main__":
    unittest.main()
