"""Реальная проверка миграции главного хаба tools.pyw на единую тему.

Запуск:
  C:\\Users\\admin\\.workbuddy-ai\\binaries\\python\\envs\\shorts-gui\\Scripts\\python.exe
      -m unittest discover -s tests -p "test_tools_hub_theme.py" -v

Нужен доступный Tk (на Windows есть всегда). Окно создаётся скрытым.
"""
from __future__ import annotations

import importlib.machinery
import importlib.util
import os
import re
import sys
import tkinter as tk
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import theme  # noqa: E402

TOOLS_PATH = ROOT / "tools.pyw"


def _load_tools():
    """Импортировать .pyw как обычный модуль, чтобы видеть живые глобалки."""
    loader = importlib.machinery.SourceFileLoader("tuly_tools_under_test", str(TOOLS_PATH))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules[loader.name] = module
    loader.exec_module(module)
    return module


class ToolsHubThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._env_theme = os.environ.pop("TULY_THEME", None)
        cls.tools = _load_tools()
        theme.set_mode("auto")

    @classmethod
    def tearDownClass(cls) -> None:
        theme.set_mode("auto")
        if cls._env_theme is not None:
            os.environ["TULY_THEME"] = cls._env_theme

    def test_source_has_no_hex_colors(self) -> None:
        source = TOOLS_PATH.read_text(encoding="utf-8")
        leftovers = sorted(set(re.findall(r"#[0-9a-fA-F]{6}\b", source)))
        self.assertEqual(leftovers, [], f"HEX должен жить только в theme/tokens.py: {leftovers}")

    def test_source_has_no_hardcoded_font_families(self) -> None:
        source = TOOLS_PATH.read_text(encoding="utf-8")
        self.assertNotIn('("Segoe UI"', source)
        self.assertNotIn('("Consolas"', source)

    def test_legacy_constants_come_from_tokens(self) -> None:
        for name in self.tools._LEGACY_NAMES:
            self.assertEqual(getattr(self.tools, name), theme.alias(name), name)
        self.assertEqual(tuple(self.tools.AVATAR_COLORS), theme.AVATARS)

    def test_widgets_use_theme_colors(self) -> None:
        root = tk.Tk()
        root.withdraw()
        try:
            self.tools.apply_dark_theme(root)
            primary = self.tools.flat_button(root, "Пуск", None, kind="primary")
            self.assertEqual(primary.cget("bg"), theme.c("accent_fill"))
            self.assertEqual(primary.cget("fg"), theme.c("on_accent"))
            check = self.tools.dark_check(root, "Флаг", tk.BooleanVar(value=False))
            self.assertEqual(check.cget("bg"), theme.c("window"))
            log = self.tools.dark_text(root, height=3)
            self.assertEqual(log.cget("bg"), theme.c("inset"))
            switch = self.tools.Switch(root)
            self.assertEqual(switch.cget("bg"), theme.c("surface"))
            segmented = self.tools.Segmented(root, [("a", "A")], "a", None)
            self.assertEqual(segmented.cget("bg"), theme.c("window"))
            self.assertEqual(self.tools.ui_font(9)[0], theme.apple_tk.ui_family())
            primary.destroy(); check.destroy(); log.destroy()
            switch.destroy(); segmented.destroy()
        finally:
            root.destroy()

    def test_every_hub_widget_is_themed_and_legible(self) -> None:
        """Реальный обход дерева виджетов: ни одного чужого HEX, контраст >= 4.5."""
        from theme.contrast import ratio  # noqa: PLC0415

        def walk(widget):
            yield widget
            for child in widget.winfo_children():
                yield from walk(child)

        for mode_name in ("dark", "light"):
            theme.set_mode(mode_name)
            hub = self.tools.Hub(initial_tab="clean")
            hub.withdraw()
            hub.update_idletasks()
            try:
                known = {v.lower() for v in theme.palette(mode_name).values()
                         if v.startswith("#")}
                pairs = 0
                for widget in walk(hub):
                    try:
                        config = widget.configure()
                    except tk.TclError:
                        continue
                    # 'bg'/'fg' в configure() — псевдонимы ('-background').
                    bg = config.get("background", (None, ""))[-1]
                    fg = config.get("foreground", (None, ""))[-1]
                    for option, value in (("background", bg), ("foreground", fg)):
                        if isinstance(value, str) and value.startswith("#"):
                            self.assertIn(value.lower(), known,
                                          f"{mode_name}: {widget.winfo_class()}.{option}")
                    if all(isinstance(v, str) and v.startswith("#") for v in (bg, fg)):
                        pairs += 1
                        self.assertGreaterEqual(
                            ratio(fg, bg), 4.5,
                            f"{mode_name}: {widget.winfo_class()} {fg} на {bg}")
                self.assertGreater(pairs, 20, "проверки контраста не должны быть пустыми")
            finally:
                theme._LISTENERS[:] = [
                    cb for cb in theme._LISTENERS
                    if getattr(cb, "__self__", None) is not hub
                ]
                for job in hub.tk.call("after", "info"):
                    try:
                        hub.after_cancel(job)
                    except tk.TclError:
                        pass
                hub.destroy()
        theme.set_mode("auto")

    def test_hub_switches_theme_live(self) -> None:
        theme.set_mode("auto")  # предыдущий тест мог оставить light/dark
        hub = self.tools.Hub(initial_tab="clean")
        hub.withdraw()
        try:
            self.assertEqual(theme.raw_mode(), "auto")
            self.assertEqual(hub._theme_resolved, theme.mode())
            dark_bg = hub.theme_btn.cget("bg")

            hub._cycle_theme()  # auto → light
            hub.update_idletasks()
            self.assertEqual(theme.raw_mode(), "light")
            self.assertEqual(hub._theme_resolved, "light")
            self.assertEqual(self.tools.BG, theme.c("window"))
            self.assertEqual(hub.theme_btn.cget("bg"), theme.c("sidebar"))
            self.assertEqual(hub.theme_btn.cget("text"), "☀ Тема: светлая")
            self.assertNotEqual(hub.theme_btn.cget("bg"), dark_bg)

            hub._cycle_theme()  # light → dark
            hub.update_idletasks()
            self.assertEqual(hub._theme_resolved, "dark")
            self.assertEqual(hub.theme_btn.cget("bg"), dark_bg)

            hub._cycle_theme()  # dark → auto
            hub.update_idletasks()
            self.assertEqual(theme.raw_mode(), "auto")
        finally:
            theme._LISTENERS[:] = [
                cb for cb in theme._LISTENERS
                if getattr(cb, "__self__", None) is not hub
            ]
            hub.destroy()


if __name__ == "__main__":
    unittest.main(verbosity=2)
