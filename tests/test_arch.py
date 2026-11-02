"""Границы слоёв: логика без tkinter, GUI не тянет несуществующие имена."""
from __future__ import annotations

import ast
import importlib
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


if __name__ == "__main__":
    unittest.main()
