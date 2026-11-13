"""LeadsPanel: мульти-выбор ниш, «Лимит лидов» и сборка запросов (Tk без mainloop).

Смоук GUI по паттерну tests/test_ui_bypass.py: настоящий Tk-рут, настоящая
LeadsPanel, фейковый hub. Требует импортируемого leads2gis — там живут NICHES.
"""
from __future__ import annotations

import unittest
from unittest import mock

try:
    import tkinter as tk
    from tkinter import ttk
except ImportError as exc:  # GUI-тесты требуют tkinter (есть не во всех сборках Python)
    raise unittest.SkipTest(f"tkinter недоступен: {exc}") from exc

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
ROOT_DIR = TESTS_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from helpers import load_app, load_module, new_tk

app = load_app()  # для app._iter_widgets (общий обходчик виджетов из uibase)

leads_panel = load_module("leads_panel")
import leads2gis  # хозяин NICHES: панель читает ниши по атрибуту движка

from leads_panel import LeadsPanel

if not leads_panel.LEADS_OK:
    raise unittest.SkipTest(f"leads2gis недоступен: {leads_panel.LEADS_ERROR}")


class FakeHub:
    """Как в test_ui_bypass: синхронный run_bg, мгновенный post."""

    def __init__(self) -> None:
        self.status: list[str] = []
        self.toasts: list[str] = []

    def run_bg(self, work, on_done, on_error) -> None:
        try:
            result = work()
        except Exception as exc:  # noqa: BLE001
            on_error(exc)
        else:
            on_done(result)

    def set_status(self, text: str) -> None:
        self.status.append(text)

    def toast(self, message: str, **_kwargs) -> None:
        self.toasts.append(message)

    def post(self, callback) -> None:
        callback()


class LeadsNichePanelTest(unittest.TestCase):
    def setUp(self) -> None:
        try:
            self.root = new_tk(tk.Tk)
        except tk.TclError as exc:  # нет дисплея — GUI-тест не запустить
            raise unittest.SkipTest(f"Tk недоступен: {exc}") from exc
        self.root.withdraw()
        self.addCleanup(self.root.destroy)
        # MAIL_OK=False: refresh() выходит рано и не трогает настоящий hot_pool.csv
        patcher = mock.patch.object(leads_panel, "MAIL_OK", False)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.hub = FakeHub()
        self.panel = LeadsPanel(self.root, self.hub)

    def _checkbuttons(self) -> list[tk.Checkbutton]:
        return [w for w in app._iter_widgets(self.panel)
                if isinstance(w, tk.Checkbutton)]

    def test_twelve_niche_checkboxes_first_checked(self) -> None:
        """12 чекбоксов ниш, первый («Барбершоп») отмечен, остальные нет."""
        niches = list(leads2gis.NICHES)
        self.assertEqual(len(niches), 12)
        self.assertEqual(len(self.panel.niche_vars), 12)
        # niche_vars хранит (query, var) в том же порядке, что и NICHES
        self.assertEqual([q for q, _ in self.panel.niche_vars],
                         [q for _, q in niches])
        states = [bool(var.get()) for _, var in self.panel.niche_vars]
        self.assertTrue(states[0], "первая ниша должна быть отмечена по умолчанию")
        self.assertEqual(sum(states), 1, "остальные ниши по умолчанию выключены")
        # 12 чекбоксов ниш + «только без сайта» = 13 (больше можно, меньше нельзя)
        boxes = self._checkbuttons()
        self.assertGreaterEqual(len(boxes), 13)
        texts = {box.cget("text") for box in boxes}
        self.assertIn(niches[0][0], texts)
        self.assertIn("только без сайта", texts)

    def test_limit_combobox_default_and_values(self) -> None:
        """«Лимит лидов»: default 20, значения 10/20/50/100, readonly."""
        self.assertEqual(self.panel.limit_var.get(), "20")
        limit_cb = next(
            (w for w in app._iter_widgets(self.panel)
             if isinstance(w, ttk.Combobox)
             and str(w.cget("textvariable")) == str(self.panel.limit_var)),
            None,
        )
        self.assertIsNotNone(limit_cb, "Combobox для limit_var не найден")
        self.assertEqual(tuple(str(v) for v in limit_cb.cget("values")),
                         ("10", "20", "50", "100"))
        self.assertEqual(str(limit_cb.cget("state")), "readonly")

    def test_query_var_defaults_empty(self) -> None:
        """«Своя ниша» по умолчанию пустая — старый 'барбершоп' теперь чекбокс."""
        self.assertEqual(self.panel.query_var.get(), "")

    def test_selected_queries_from_checkboxes_and_own(self) -> None:
        """_selected_queries: отмеченные ниши + своя, без дублей, порядок сохранён."""
        (q1, v1), (q2, v2), (q3, v3) = self.panel.niche_vars[:3]
        v1.set(False)
        v2.set(True)
        v3.set(True)
        self.assertEqual(self.panel._selected_queries(), [q2, q3])

        # своя ниша добавляется в конец (пробелы обрезаются)
        self.panel.query_var.set("  пиццерия  ")
        self.assertEqual(self.panel._selected_queries(), [q2, q3, "пиццерия"])

        # своя ниша совпадает с отмеченной (без учёта регистра) — дубля нет
        self.panel.query_var.set(q2.upper())
        self.assertEqual(self.panel._selected_queries(), [q2, q3])

        # совпадает с НЕотмеченной — добавляется как введённая
        self.panel.query_var.set(q1.capitalize())
        self.assertEqual(self.panel._selected_queries(), [q2, q3, q1.capitalize()])

    def test_selected_queries_empty_when_nothing_picked(self) -> None:
        """Ничего не отмечено и своя ниша пуста — пустой список (start() завернёт)."""
        for _, var in self.panel.niche_vars:
            var.set(False)
        self.panel.query_var.set("")
        self.assertEqual(self.panel._selected_queries(), [])

    def test_fill_topics_warns_when_no_niche_picked(self) -> None:
        """fill_topics без ниш и своей не трогает пул, только статус."""
        for _, var in self.panel.niche_vars:
            var.set(False)
        self.panel.query_var.set("")
        self.panel.fill_topics()
        self.assertEqual(self.panel.status_var.get(),
                         "Выберите нишу или введите свою")


if __name__ == "__main__":
    unittest.main()
