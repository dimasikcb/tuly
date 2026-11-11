"""Фаза 6: единая Apple-тема в окне «Дневник МО» (CustomTkinter).

Проверяется НЕ «должно работать», а реальное окно: создаётся App с демо-данными,
обходится дерево из ~300 виджетов, каждый цвет сверяется с theme/tokens.py,
каждая пара текст/фон — с WCAG >= 4.5:1, и тема переключается на живом окне.

Запуск (в venv проекта нет pytest, поэтому unittest):
    diary-grades\\.venv\\Scripts\\python.exe -X utf8 -m unittest discover -s tests -v
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

DIARY_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = DIARY_ROOT.parent
for _path in (DIARY_ROOT, PROJECT_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

import customtkinter as ctk  # noqa: E402
import theme  # noqa: E402
from theme import apple_ctk  # noqa: E402
from theme.contrast import ratio  # noqa: E402

import app  # noqa: E402
import config  # noqa: E402
import demo_data  # noqa: E402

APP_PATH = DIARY_ROOT / "app.py"

COLOR_OPTIONS = (
    "fg_color", "hover_color", "border_color", "text_color", "text_color_disabled",
    "progress_color", "button_color", "button_hover_color",
    "scrollbar_button_color", "scrollbar_button_hover_color",
    "placeholder_text_color", "selected_color", "selected_hover_color",
    "unselected_color", "unselected_hover_color", "checkmark_color",
    "top_fg_color", "label_fg_color",
)


def _walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


def _colors(widget) -> dict:
    out = {}
    for option in COLOR_OPTIONS:
        try:
            value = widget.cget(option)
        except Exception:  # noqa: BLE001 — у части виджетов такой опции нет
            continue
        if value is not None and value != "":
            out[option] = value
    return out


def _effective_bg(widget, cfg: dict):
    bg = cfg.get("fg_color")
    if bg not in (None, "transparent"):
        return bg
    parent = getattr(widget, "master", None)
    while parent is not None:
        parent_bg = _colors(parent).get("fg_color")
        if parent_bg not in (None, "transparent"):
            return parent_bg
        parent = getattr(parent, "master", None)
    return None


class TestSource(unittest.TestCase):
    """Исходник не содержит цветов и шрифтов вне tokens.py."""

    def test_no_hex_colors(self) -> None:
        source = APP_PATH.read_text(encoding="utf-8")
        self.assertEqual(sorted(set(re.findall(r"#[0-9a-fA-F]{6}\b", source))), [])

    def test_no_hardcoded_fonts(self) -> None:
        source = APP_PATH.read_text(encoding="utf-8")
        for literal in ('"Segoe UI"', '"Consolas"', '"DejaVu Sans"', "family=",
                        "ctk.CTkFont("):
            self.assertNotIn(literal, source, literal)

    def test_no_hardcoded_ctk_theme(self) -> None:
        source = APP_PATH.read_text(encoding="utf-8")
        self.assertNotIn('set_appearance_mode("dark")', source)
        self.assertNotIn('set_default_color_theme("blue")', source)
        self.assertIn("_apple_ctk.configure()", source)
        self.assertIn("_theme.on_change(self._apply_theme_mode)", source)


class TestPalette(unittest.TestCase):
    def test_constants_are_token_pairs(self) -> None:
        for name, role in (("BG", "window"), ("PANEL", "surface"),
                           ("CARD", "surface"), ("CARD_ALT", "surface_raised"),
                           ("BORDER", "separator"), ("TEXT", "label"),
                           ("MUTED", "label_secondary"),
                           ("ACCENT", "accent_fill")):
            with self.subTest(name=name):
                self.assertEqual(getattr(app, name), apple_ctk.tint(role))

    def test_status_colors_are_text_safe_inks(self) -> None:
        """OK/WARN/BAD — это текст на surface_raised, контраст >= 4.5:1."""
        for name, role in (("OK", "badge_good_ink"), ("WARN", "badge_warn_ink"),
                           ("BAD", "badge_bad_ink")):
            with self.subTest(name=name):
                self.assertEqual(getattr(app, name), apple_ctk.tint(role))
                for resolved in ("light", "dark"):
                    self.assertGreaterEqual(
                        ratio(theme.c(role, resolved), theme.c("surface_raised", resolved)),
                        4.5, f"{name}/{resolved}",
                    )

    def test_button_kwargs_come_from_adapter(self) -> None:
        self.assertEqual(app.GHOST, apple_ctk.button_colors("ghost"))
        self.assertEqual(app.PRIMARY, apple_ctk.button_colors("primary"))
        self.assertIn("text_color", app.GHOST)

    def test_generated_ctk_theme_is_loaded(self) -> None:
        self.assertTrue(apple_ctk.THEME_JSON.is_file())
        self.assertEqual(ctk.ThemeManager._currently_loaded_theme,
                         str(apple_ctk.THEME_JSON))
        self.assertEqual(ctk.ThemeManager.theme["CTk"]["fg_color"],
                         [theme.c("window", "light"), theme.c("window", "dark")])


class TestLiveWindow(unittest.TestCase):
    """Реальное окно: цвета, контраст и живая смена Light/Dark/Auto."""

    @classmethod
    def setUpClass(cls) -> None:
        theme.set_mode("dark")
        cls.window = app.App(start_demo=True)
        cls.window._render(demo_data.demo_result(config.current_period().label))
        cls.window.update()
        cls.widgets = list(_walk(cls.window))

    @classmethod
    def tearDownClass(cls) -> None:
        cls.window.destroy()
        theme.set_mode("auto")

    def setUp(self) -> None:
        theme.set_mode("dark")
        self.window.update()

    def test_window_is_built(self) -> None:
        self.assertGreater(len(self.widgets), 100, "дерево виджетов пустое")
        self.assertGreater(self.window.table.visible_count, 0, "нет строк таблицы")

    def _audit(self, resolved: str) -> tuple[int, list, list]:
        theme.set_mode(resolved)
        self.window.update()
        allowed = set(theme.palette(resolved).values())
        foreign: list = []
        pairs: list = []
        for widget in self.widgets:
            cfg = _colors(widget)
            if not cfg:
                continue
            for option, value in cfg.items():
                color = apple_ctk.resolve(value, resolved)
                if color in (None, "transparent"):
                    continue
                if color not in allowed:
                    foreign.append((type(widget).__name__, option, color))
            text = cfg.get("text_color")
            bg = _effective_bg(widget, cfg)
            if text is not None and bg not in (None, "transparent"):
                fg = apple_ctk.resolve(text, resolved)
                back = apple_ctk.resolve(bg, resolved)
                pairs.append((type(widget).__name__, fg, back, ratio(fg, back)))
        return len(pairs), foreign, [p for p in pairs if p[3] < 4.5]

    def test_dark_widget_tree_is_themed_and_legible(self) -> None:
        count, foreign, low = self._audit("dark")
        self.assertGreater(count, 40, "не найдено пар текст/фон")
        self.assertEqual(foreign, [], f"цвета вне tokens.py: {foreign[:5]}")
        self.assertEqual(low, [], f"контраст < 4.5:1: {low[:5]}")

    def test_light_widget_tree_is_themed_and_legible(self) -> None:
        count, foreign, low = self._audit("light")
        self.assertGreater(count, 40, "не найдено пар текст/фон")
        self.assertEqual(foreign, [], f"цвета вне tokens.py: {foreign[:5]}")
        self.assertEqual(low, [], f"контраст < 4.5:1: {low[:5]}")

    def test_live_switch_recolors_existing_widgets(self) -> None:
        for resolved, label in (("light", "Тема: светлая"), ("dark", "Тема: тёмная")):
            with self.subTest(resolved=resolved):
                theme.set_mode(resolved)
                self.assertEqual(ctk.get_appearance_mode(), resolved.capitalize())
                self.assertEqual(
                    self.window._apply_appearance_mode(self.window.cget("fg_color")),
                    theme.c("window", resolved),
                )
                self.assertEqual(self.window.theme_btn.cget("text"), label)
                self.assertEqual(
                    apple_ctk.resolve(self.window.refresh_btn.cget("fg_color"), resolved),
                    theme.c("accent_fill", resolved),
                )
                self.assertEqual(
                    apple_ctk.resolve(self.window.login_btn.cget("text_color"), resolved),
                    theme.c("label", resolved),
                )

    def test_cycle_button_walks_auto_light_dark(self) -> None:
        theme.set_mode("auto")
        self.assertEqual(app.App._theme_label(), "Тема: авто")
        seen = []
        for _ in range(3):
            self.window._cycle_theme()
            seen.append(theme.raw_mode())
        self.assertEqual(seen, ["light", "dark", "auto"])
        self.assertEqual(ctk.get_appearance_mode(), theme.mode().capitalize())


if __name__ == "__main__":
    unittest.main(verbosity=2)
