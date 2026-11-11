"""Проверки единой темы: токены, контраст, детерминизм CSS.

Запуск: py -3 -m unittest discover -s tests -p "test_theme.py" -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from theme import ALIASES, TOKENS, c, mode, set_mode  # noqa: E402
from theme.contrast import check, ratio  # noqa: E402
from theme.generate_css import OUTPUTS, build_css  # noqa: E402


class ThemeTests(unittest.TestCase):
    def test_palettes_have_identical_keys(self) -> None:
        self.assertEqual(set(TOKENS["light"]), set(TOKENS["dark"]))
        self.assertIn("label_secondary", TOKENS["dark"])
        self.assertIn("accent_fill", TOKENS["light"])

    def test_every_text_pair_meets_hig_minimum(self) -> None:
        self.assertEqual(check(4.5), [], "текстовые пары должны давать >= 4.5:1")

    def test_primary_action_has_legible_label(self) -> None:
        for theme_name, palette in TOKENS.items():
            value = ratio(palette["on_accent"], palette["accent_fill"])
            self.assertGreaterEqual(value, 4.5, f"{theme_name}: белый текст на primary")

    def test_aliases_resolve(self) -> None:
        from theme import alias  # noqa: PLC0415

        for old_name, role in ALIASES.items():
            self.assertEqual(alias(old_name, "dark"), c(role, "dark"), old_name)

    def test_css_is_deterministic(self) -> None:
        self.assertEqual(build_css(), build_css())
        self.assertIn("GENERATED", build_css())

    def test_css_has_both_appearances_and_accessibility(self) -> None:
        css = build_css()
        for marker in (
            'prefers-color-scheme: light', 'prefers-color-scheme: dark',
            'data-theme="light"', 'data-theme="dark"',
            'prefers-reduced-motion', 'prefers-reduced-transparency',
            ':focus-visible', '--accent-fill:', '--font-mono:',
        ):
            self.assertIn(marker, css, marker)

    def test_generated_files_match_tokens(self) -> None:
        css = build_css()
        for path in OUTPUTS:
            self.assertTrue(path.exists(), f"не запущен генератор: {path}")
            self.assertEqual(path.read_text(encoding="utf-8"), css)

    def test_adapters_import_without_recursion(self) -> None:
        """`from theme import apple_tk` не должен уходить в __getattr__ рекурсией."""
        import theme  # noqa: PLC0415

        from theme import apple_ctk, apple_tk  # noqa: PLC0415

        self.assertTrue(callable(apple_tk.apply_theme))
        self.assertTrue(callable(apple_ctk.configure))
        self.assertIs(theme.apple_tk, apple_tk)

    def test_both_palettes_cover_every_alias(self) -> None:
        for theme_name, palette in TOKENS.items():
            for old_name, role in ALIASES.items():
                self.assertIn(role, palette, f"{theme_name}: {old_name} → {role}")

    def test_ctk_theme_is_generated_from_tokens(self) -> None:
        from theme import generate_ctk  # noqa: PLC0415

        theme_dict = generate_ctk.build_theme()
        self.assertEqual(generate_ctk.build_json(), generate_ctk.build_json())
        self.assertEqual(generate_ctk.OUTPUT.read_text(encoding="utf-8"),
                         generate_ctk.build_json())
        self.assertIn("CTkEntry", theme_dict)
        self.assertEqual(theme_dict["CTk"]["fg_color"],
                         [c("window", "light"), c("window", "dark")])
        # Ни одного цвета вне tokens.py: каждая пара [light, dark] из палитр.
        allowed = set(TOKENS["light"].values()) | set(TOKENS["dark"].values())
        for cls, options in theme_dict.items():
            for key, value in options.items():
                if isinstance(value, list):
                    with self.subTest(cls=cls, key=key):
                        self.assertEqual(len(value), 2)
                        self.assertIn(value[0], allowed)
                        self.assertIn(value[1], allowed)

    def test_tint_matches_both_palettes(self) -> None:
        from theme import apple_ctk  # noqa: PLC0415

        for role in ("window", "label", "accent_fill", "badge_info_bg"):
            pair = apple_ctk.tint(role)
            self.assertEqual(apple_ctk.resolve(pair, "light"), c(role, "light"))
            self.assertEqual(apple_ctk.resolve(pair, "dark"), c(role, "dark"))

    def test_mode_switch_and_override(self) -> None:
        previous = set_mode("dark")
        self.assertEqual(previous, "dark")
        self.assertEqual(mode(), "dark")
        try:
            set_mode("light")
            self.assertEqual(c("window"), TOKENS["light"]["window"])
        finally:
            set_mode("dark")

    def test_auto_follows_windows_theme_and_notifies_listeners(self) -> None:
        """DEFAULT_MODE=auto, реестр подменяем mock'ом, on_change получает resolved."""
        import os  # noqa: PLC0415
        from unittest import mock  # noqa: PLC0415

        import theme  # noqa: PLC0415

        self.assertEqual(theme.DEFAULT_MODE, "auto")
        seen: list[str] = []
        theme.on_change(seen.append)
        try:
            with mock.patch.dict(os.environ, {"TULY_THEME": ""}):
                with mock.patch.object(theme, "_system_mode", return_value="light"):
                    self.assertEqual(theme.set_mode("auto"), "light")
                    self.assertEqual(theme.mode(), "light")
                with mock.patch.object(theme, "_system_mode", return_value="dark"):
                    self.assertEqual(theme.mode(), "dark")
                self.assertEqual(seen, ["light"])
                self.assertEqual(theme.set_mode("dark"), "dark")
                self.assertEqual(seen, ["light", "dark"])
        finally:
            theme._LISTENERS.remove(seen.append)
            theme.set_mode("auto")

    def test_all_six_surfaces_have_no_hex_or_hardcoded_fonts(self) -> None:
        """План §6: HEX живёт только в theme/tokens.py на всех шести поверхностях."""
        import re  # noqa: PLC0415

        surfaces = (
            "tools.pyw",
            "shorts-autopilot/app/static/index.html",
            "shorts-autopilot/app/gui.py",
            "shorts-autopilot/app/webui.py",
            "diary-grades/app.py",
            "ai-screen-agent/screen_agent.py",
        )
        for relative in surfaces:
            with self.subTest(surface=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                self.assertEqual(sorted(set(re.findall(r"#[0-9a-fA-F]{6}\b", source))),
                                 [], f"{relative}: HEX вне tokens.py")
        for relative in ("tools.pyw", "shorts-autopilot/app/gui.py",
                         "diary-grades/app.py", "ai-screen-agent/screen_agent.py"):
            with self.subTest(surface=relative):
                source = (ROOT / relative).read_text(encoding="utf-8")
                for family in ('"Segoe UI"', '"Consolas"', '"Arial"'):
                    self.assertNotIn(family, source, f"{relative}: {family}")

    def test_css_has_increase_contrast_and_forced_colors(self) -> None:
        css = build_css()
        self.assertIn("prefers-contrast: more", css)
        self.assertIn("forced-colors: active", css)
        self.assertIn("forced-colors: active", OUTPUTS[0].read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main(verbosity=2)
