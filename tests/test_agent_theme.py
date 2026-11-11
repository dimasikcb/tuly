"""Реальная проверка миграции AI Screen Bridge на единую тему проекта.

Запуск (из корня проекта):
  py -3 -X utf8 -m unittest discover -s tests -p "test_agent_theme.py" -v

Окно создаётся скрытым, без сети и без вызовов Gemini: проверяется только UI.
"""
from __future__ import annotations

import os
import re
import sys
import tkinter as tk
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
AGENT_ROOT = PROJECT_ROOT / "ai-screen-agent"
for path in (str(PROJECT_ROOT), str(AGENT_ROOT)):
    if path not in sys.path:
        sys.path.insert(0, path)

import theme  # noqa: E402
import screen_agent  # noqa: E402

SOURCE = AGENT_ROOT / "screen_agent.py"


def _walk(widget):
    yield widget
    for child in widget.winfo_children():
        yield from _walk(child)


def _cancel_after(root: tk.Misc) -> None:
    """Гасим _theme_tick / process_log_queue, иначе destroy() даёт TclError."""
    try:
        jobs = root.tk.call("after", "info")
    except tk.TclError:
        return
    for job in jobs:
        try:
            root.after_cancel(job)
        except tk.TclError:
            pass


def _drop_listener(app) -> None:
    theme._LISTENERS[:] = [
        cb for cb in theme._LISTENERS
        if getattr(cb, "__self__", None) is not app
    ]


class AgentThemeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls._env_theme = os.environ.pop("TULY_THEME", None)
        theme.set_mode("auto")

    @classmethod
    def tearDownClass(cls) -> None:
        theme.set_mode("auto")
        if cls._env_theme is not None:
            os.environ["TULY_THEME"] = cls._env_theme

    def _make_app(self, mode_name: str):
        theme.set_mode(mode_name)
        root = tk.Tk()
        root.withdraw()
        app = screen_agent.AIScreenAgentApp(root)
        root.update_idletasks()
        return root, app

    def _close(self, root, app) -> None:
        _drop_listener(app)
        _cancel_after(root)
        root.destroy()

    def test_source_has_no_hex_colors(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        leftovers = sorted(set(re.findall(r"#[0-9a-fA-F]{6}\b", source)))
        self.assertEqual(leftovers, [], f"HEX должен жить только в theme/tokens.py: {leftovers}")

    def test_source_has_no_hardcoded_font_families(self) -> None:
        source = SOURCE.read_text(encoding="utf-8")
        for family in ("Segoe UI", "Consolas", "Arial"):
            self.assertNotIn(f'"{family}"', source, family)

    def test_geometry_scales_with_windows_dpi(self) -> None:
        """Иначе 13pt при 200% масштабе не помещается в жёсткие 620x760."""
        source = SOURCE.read_text(encoding="utf-8")
        self.assertIn("winfo_fpixels", source)
        self.assertNotIn('self.root.geometry("620x760")', source)

    def test_helpers_resolve_tokens_and_fallback_fonts(self) -> None:
        self.assertEqual(screen_agent.tc("accent_fill"), theme.c("accent_fill"))
        self.assertEqual(screen_agent.ui_font(10)[0], theme.apple_tk.ui_family())
        self.assertEqual(screen_agent.mono_font(10)[0], theme.apple_tk.mono_family())

    def test_overlay_markers_are_theme_invariant(self) -> None:
        """Маркеры рисуются поверх чужого скриншота — одинаковы в обеих темах."""
        for role in ("marker_fill", "marker_outline", "overlay_backdrop", "roi"):
            self.assertEqual(theme.c(role, "light"), theme.c(role, "dark"), role)

    def test_every_agent_widget_is_themed_and_legible(self) -> None:
        """Обход реального дерева: tk-виджеты + ttk-стили, контраст >= 4.5."""
        from tkinter import ttk  # noqa: PLC0415
        from theme.contrast import ratio  # noqa: PLC0415

        def color(value) -> str:
            """ttk возвращает Tcl_Obj <color object: '#xxx'>, tk — обычный str."""
            text = value if isinstance(value, str) else str(value)
            return text if text.startswith("#") else ""

        for mode_name in ("dark", "light"):
            root, app = self._make_app(mode_name)
            try:
                known = {v.lower() for v in theme.palette(mode_name).values()
                         if v.startswith("#")}
                style = ttk.Style(root)
                pairs = 0
                for widget in _walk(root):
                    try:
                        config = widget.configure()
                    except tk.TclError:
                        continue
                    bg = color(config.get("background", (None, ""))[-1])
                    fg = color(config.get("foreground", (None, ""))[-1])
                    for option, value in (("background", bg), ("foreground", fg)):
                        if value:
                            self.assertIn(value.lower(), known,
                                          f"{mode_name}: {widget.winfo_class()}.{option}")
                    if bg and fg:
                        pairs += 1
                        self.assertGreaterEqual(
                            ratio(fg, bg), 4.5,
                            f"{mode_name}: {widget.winfo_class()} {fg} на {bg}")

                    # ttk хранит цвета в стиле, а не в configure(): проверяем и их.
                    if not widget.winfo_class().startswith("T"):
                        continue
                    try:
                        name = str(widget.cget("style")) or widget.winfo_class()
                    except tk.TclError:
                        name = widget.winfo_class()
                    s_fg = color(style.lookup(name, "foreground"))
                    s_bg = color(style.lookup(name, "background"))
                    s_field = color(style.lookup(name, "fieldbackground"))
                    for value in (s_fg, s_bg, s_field):
                        if value:
                            self.assertIn(value.lower(), known,
                                          f"{mode_name}: style {name}: {value}")
                    for surface in (s_bg, s_field):
                        if s_fg and surface:
                            pairs += 1
                            self.assertGreaterEqual(
                                ratio(s_fg, surface), 4.5,
                                f"{mode_name}: style {name} {s_fg} на {surface}")
                self.assertGreater(pairs, 40, "проверки контраста не должны быть пустыми")
                self.assertEqual(root.cget("bg"), theme.c("window"))
                self.assertEqual(app.log_text.cget("bg"), theme.c("inset"))
                self.assertEqual(app.preview_canvas.cget("bg"), theme.c("inset"))
                self.assertEqual(str(app.roi_label.cget("foreground")), theme.c("tag_dir"))
            finally:
                self._close(root, app)
        theme.set_mode("auto")

    def test_agent_switches_theme_live(self) -> None:
        root, app = self._make_app("dark")
        try:
            self.assertEqual(app._theme_resolved, "dark")
            dark_bg = root.cget("bg")

            theme.set_mode("light")  # подписчик on_change перекрашивает окно
            root.update_idletasks()
            self.assertEqual(app._theme_resolved, "light")
            self.assertEqual(root.cget("bg"), theme.c("window"))
            self.assertNotEqual(root.cget("bg"), dark_bg)
            self.assertEqual(app.log_text.cget("bg"), theme.c("inset"))
            self.assertEqual(app.log_text.cget("fg"), theme.c("log_info"))
            self.assertEqual(app.start_btn.cget("bg"), theme.c("accent_fill"))
            self.assertEqual(app.start_btn.cget("fg"), theme.c("on_accent"))
            self.assertEqual(str(app.roi_label.cget("foreground")), theme.c("tag_dir"))
            self.assertEqual(app.theme_btn.cget("text"), "Тема: светлая")
            # Живая перекраска не должна оставить ни одного цвета из тёмной палитры.
            known = {v.lower() for v in theme.palette("light").values()
                     if v.startswith("#")}
            for widget in _walk(root):
                try:
                    config = widget.configure()
                except tk.TclError:
                    continue
                for option in ("background", "foreground", "selectcolor",
                               "activebackground", "insertbackground"):
                    value = config.get(option, (None, ""))[-1]
                    text = value if isinstance(value, str) else str(value)
                    if text.startswith("#"):
                        self.assertIn(text.lower(), known,
                                      f"live light: {widget.winfo_class()}.{option}={text}")

            theme.set_mode("dark")
            root.update_idletasks()
            self.assertEqual(root.cget("bg"), dark_bg)
            self.assertEqual(app.start_btn.cget("bg"), theme.c("accent_fill"))
            self.assertEqual(app.theme_btn.cget("text"), "Тема: тёмная")

            app._set_start_button(True)
            self.assertEqual(app.start_btn.cget("bg"), theme.c("err_bg"))
            self.assertEqual(app.start_btn.cget("fg"), theme.c("badge_bad_ink"))
            app._set_start_button(False)
            self.assertEqual(app.start_btn.cget("bg"), theme.c("accent_fill"))
        finally:
            self._close(root, app)
        theme.set_mode("auto")

    def test_cycle_button_walks_auto_light_dark(self) -> None:
        root, app = self._make_app("auto")
        try:
            self.assertEqual(app.theme_btn.cget("text"), "Тема: авто")
            seen = []
            for _ in range(3):
                app._cycle_theme()
                seen.append(theme.raw_mode())
            self.assertEqual(seen, ["light", "dark", "auto"])
        finally:
            self._close(root, app)
        theme.set_mode("auto")


if __name__ == "__main__":
    unittest.main(verbosity=2)
