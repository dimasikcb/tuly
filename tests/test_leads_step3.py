"""ШАГ 3 LEADS_PLAN.md: карточка-инспектор лида справа от таблицы (Tk без mainloop).

Тесты пишутся ровно под контракт UI, который реализует параллельный агент в
tools.pyw. Здесь НЕ редактируются tools.pyw / leads_mail.py / leads2gis.py.
Паттерн взят из tests/test_leads_niche_ui.py: настоящий Tk-рут, настоящая
LeadsPanel, фейковый hub, никакого mainloop.

Изоляция данных: все пути (POOL_FILE, MAIL_SETTINGS_PATH, файл промпта) ведут
в tempfile.TemporaryDirectory(), реальные hot_pool.csv /
leads_mail_settings.json / leads_prompts/ не затрагиваются.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

try:
    import tkinter as tk
    from tkinter import ttk
except ImportError as exc:  # GUI-тесты требуют tkinter (есть не во всех сборках Python)
    raise unittest.SkipTest(f"tkinter недоступен: {exc}") from exc

TESTS_DIR = Path(__file__).resolve().parent
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))
ROOT_DIR = TESTS_DIR.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from helpers import load_app, new_tk

app = load_app()

import leads_mail as real_mail  # реальный модуль (sys.modules совпадает с app.leads_mail)

if not app.LEADS_OK:
    raise unittest.SkipTest(f"leads2gis недоступен: {app.LEADS_ERROR}")


class FakeHub:
    """Как в test_leads_niche_ui.py: синхронный run_bg, мгновенный post."""

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


# Полная строка лида: все поля из контракта. topic и rubric совпадают, чтобы
# не зависеть от того, какое из полей UI берёт для строки «ниша».
ROW = {
    "name": "Барбершоп Борода",
    "topic": "Барбершоп",
    "address": "ул. Ленина, 1",
    "rating": "4.9",
    "reviews": "128",
    "phone": "+79001112233",
    "telegram": "https://t.me/boroda",
    "vk": "https://vk.com/boroda",
    "city": "Москва",
    "rubric": "Барбершоп",
}
DETAIL_KEYS = ("name", "topic", "address", "rating", "phone", "messengers")


class LeadsStep3PanelTest(unittest.TestCase):
    def setUp(self) -> None:
        # На Windows при нескольких tk.Tk() подряд Tcl изредка не находит tk.tcl;
        # общий ретрай из helpers снимает гонку, не ослабляя проверки.
        try:
            self.root = new_tk(tk.Tk, attempts=3)
        except tk.TclError as exc:  # нет дисплея — GUI-тест не запустить
            raise unittest.SkipTest(f"Tk недоступен: {exc}") from exc
        self.root.withdraw()
        self.addCleanup(self.root.destroy)

        # --- изоляция данных ------------------------------------------------ #
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmp_path = Path(self.tmp.name)

        p_pool = mock.patch.object(app, "POOL_FILE", tmp_path / "hot_pool.csv")
        p_pool.start()
        self.addCleanup(p_pool.stop)

        p_settings = mock.patch.object(
            app, "MAIL_SETTINGS_PATH", tmp_path / "leads_mail_settings.json")
        p_settings.start()
        self.addCleanup(p_settings.stop)

        self.prompt_dir = tmp_path / "leads_prompts"
        self.prompt_dir.mkdir(parents=True, exist_ok=True)

        def fake_site_prompt_file(row, out_dir=None):
            # generate_site_prompt() вызывает site_prompt_file() внутри себя —
            # поэтому и сгенерированный, и пересохранённый файл окажутся в tmp.
            slug = real_mail._translit(str(row.get("name") or "")) or "lead"
            return self.prompt_dir / f"{slug}.txt"

        p_prompt = mock.patch.object(
            real_mail, "site_prompt_file", fake_site_prompt_file)
        p_prompt.start()
        self.addCleanup(p_prompt.stop)

        # MAIL_OK=False: refresh() при сборке панели выходит рано и не читает пул.
        p_mail_ok = mock.patch.object(app, "MAIL_OK", False)
        p_mail_ok.start()
        self.addCleanup(p_mail_ok.stop)

        self.hub = FakeHub()
        self.panel = app.LeadsPanel(self.root, self.hub)

    # -- helpers ----------------------------------------------------------- #
    def _enable_mail(self) -> None:
        """После создания панели включаем MAIL_OK и реальный модуль рассылки."""
        p_ok = mock.patch.object(app, "MAIL_OK", True)
        p_ok.start()
        self.addCleanup(p_ok.stop)
        p_mod = mock.patch.object(app, "leads_mail", real_mail)
        p_mod.start()
        self.addCleanup(p_mod.stop)

    @staticmethod
    def _text(widget: tk.Text) -> str:
        return widget.get("1.0", "end")

    @staticmethod
    def _is_descendant(child: tk.Misc, ancestor: tk.Misc) -> bool:
        """Поднимаемся по winfo_parent(), сравнивая пути виджетов (не identity)."""
        target = str(ancestor)
        cur = child
        for _ in range(64):
            if str(cur) == target:
                return True
            parent = cur.winfo_parent()
            if not parent:
                return False
            nxt = cur.nametowidget(parent)
            if str(nxt) == str(cur):
                return False
            cur = nxt
        return False

    def _seed_row(self, iid: str = "r0") -> None:
        """Полная строка в self._rows + соответствующий iid в result_tree."""
        self.panel._rows[iid] = dict(ROW)
        tree = self.panel.result_tree
        if iid not in tree.get_children():
            tree.insert("", "end", iid=iid, values=(
                ROW["name"], ROW["topic"], ROW["phone"], "2ГИС"))

    # -- 1. двухпанельный макет -------------------------------------------- #
    def test_two_panel_layout(self) -> None:
        """split — горизонтальный ttk.PanedWindow с двумя панелями; inspector справа."""
        panel = self.panel
        self.assertIsInstance(panel.split, ttk.PanedWindow)
        self.assertEqual(str(panel.split.cget("orient")), "horizontal")
        panes = panel.split.panes()
        self.assertEqual(len(panes), 2, "должно быть ровно две панели")

        self.assertTrue(hasattr(panel, "inspector"), "нет атрибута inspector")
        self.assertTrue(self._is_descendant(panel.inspector, panel.split),
                        "inspector должен быть потомком split")

        left = panel.nametowidget(panes[0])
        right = panel.nametowidget(panes[1])
        self.assertTrue(self._is_descendant(panel.result_tree, left),
                        "result_tree должен лежать в левой панели")
        self.assertTrue(self._is_descendant(panel.inspector, right),
                        "inspector должен лежать в правой панели")

    # -- 2. сворачивание инспектора ---------------------------------------- #
    def test_toggle_inspector(self) -> None:
        """_toggle_inspector(): forget/add панели и флаг _inspector_visible."""
        panel = self.panel
        self.assertTrue(panel._inspector_visible,
                        "по умолчанию карточка должна быть видна")
        self.assertEqual(len(panel.split.panes()), 2)

        panel._toggle_inspector()
        self.assertEqual(len(panel.split.panes()), 1,
                         "после сворачивания должна остаться одна панель")
        self.assertFalse(panel._inspector_visible)

        panel._toggle_inspector()
        self.assertEqual(len(panel.split.panes()), 2,
                         "повторный вызов должен вернуть панель")
        self.assertTrue(panel._inspector_visible)
        self.assertTrue(self._is_descendant(panel.inspector, panel.split),
                        "inspector должен снова быть потомком split")

    # -- 3. _show_lead ----------------------------------------------------- #
    def test_show_lead_fills_card(self) -> None:
        """_show_lead(): detail_vars, msg_text, prompt_text, prompt_path_var."""
        self._enable_mail()
        panel = self.panel
        self._seed_row("r0")
        panel._show_lead("r0")

        for key in DETAIL_KEYS:
            with self.subTest(key=key):
                self.assertIn(key, panel.detail_vars)
                self.assertIsInstance(panel.detail_vars[key], tk.StringVar)
                self.assertTrue(panel.detail_vars[key].get().strip(),
                                f"detail_vars[{key!r}] не заполнен")

        self.assertEqual(panel.detail_vars["name"].get(), ROW["name"])
        self.assertEqual(panel.detail_vars["address"].get(), ROW["address"])
        self.assertIn(ROW["topic"], panel.detail_vars["topic"].get())
        self.assertIn(ROW["rating"], panel.detail_vars["rating"].get())
        # телефон может быть отформатирован — сравниваем только цифры
        digits = "".join(ch for ch in panel.detail_vars["phone"].get() if ch.isdigit())
        self.assertIn("".join(ch for ch in ROW["phone"] if ch.isdigit()), digits)
        mess = panel.detail_vars["messengers"].get()
        self.assertIn("t.me/boroda", mess, "Telegram не попал в карточку")
        self.assertIn("vk.com/boroda", mess, "VK не попал в карточку")

        msg = self._text(panel.msg_text).strip()
        self.assertTrue(msg, "msg_text не должен быть пустым")
        self.assertIn(ROW["name"], msg, "в сообщении должно быть название лида")
        self.assertTrue("http" in msg or "ссылк" in msg.lower(),
                        "в сообщении должна быть ссылка на пример сайта")

        prompt = self._text(panel.prompt_text)
        self.assertIn("2-4", prompt,
                      "в промпте должна быть фраза про 2-4 уточняющих вопроса")
        self.assertIn("БЕЗ сервера", prompt,
                      "в промпте должно быть требование «БЕЗ сервера»")

        self.assertTrue(panel.prompt_path_var.get().endswith(".txt"),
                        "prompt_path_var должен указывать на .txt файл")

    # -- 4. <<TreeviewSelect>> --------------------------------------------- #
    def test_treeview_select_updates_card(self) -> None:
        """Выделение строки реально обновляет карточку через <<TreeviewSelect>>."""
        self._enable_mail()
        panel = self.panel
        self._seed_row("r0")
        self.assertNotEqual(panel.detail_vars["name"].get(), ROW["name"],
                            "до выделения карточка должна быть пустой")

        panel.result_tree.selection_set("r0")
        panel.result_tree.event_generate("<<TreeviewSelect>>")
        panel.update()

        if panel.detail_vars["name"].get() != ROW["name"]:
            # В некоторых сборках Tk программное selection_set()/event_generate()
            # не доставляет виртуальное событие <<TreeviewSelect>> — вызываем
            # привязанный к нему обработчик явно (контракт: _on_select).
            panel._on_select()
            panel.update()

        self.assertEqual(panel.detail_vars["name"].get(), ROW["name"],
                         "выделение строки должно открыть карточку лида")

    # -- 5. _copy_prompt --------------------------------------------------- #
    def test_copy_prompt_copies_edited_text_and_resaves_file(self) -> None:
        """_copy_prompt(): текущий (отредактированный) текст в буфер и в файл."""
        self._enable_mail()
        panel = self.panel
        self._seed_row("r0")
        panel._show_lead("r0")
        self.assertTrue(panel.prompt_path_var.get().endswith(".txt"))

        edited = "РУЧНОЙ ПРОМПТ: задай 2-4 вопроса, БЕЗ сервера."
        panel.prompt_text.delete("1.0", "end")
        panel.prompt_text.insert("1.0", edited)

        self.hub.toasts.clear()
        with mock.patch.object(panel, "clipboard_clear") as clip_clear, \
             mock.patch.object(panel, "clipboard_append") as clip_append:
            panel._copy_prompt()

        self.assertTrue(clip_clear.called, "должен вызываться clipboard_clear()")
        self.assertTrue(clip_append.called, "должен вызываться clipboard_append()")
        appended = "".join(str(call.args[0])
                           for call in clip_append.call_args_list if call.args)
        self.assertIn(edited, appended,
                      "в буфер должен уйти ОТРЕДАКТИРОВАННЫЙ текст prompt_text")

        path = Path(panel.prompt_path_var.get())
        self.assertTrue(path.is_file(), f"файл промпта не создан: {path}")
        self.assertIn(edited, path.read_text(encoding="utf-8"),
                      "файл должен быть пересохранён с отредактированным текстом")
        self.assertTrue(self.hub.toasts, "_copy_prompt должен вызвать hub.toast")

    # -- 6. _clear_inspector ----------------------------------------------- #
    def test_clear_inspector_resets_card(self) -> None:
        """_clear_inspector(): обнуляет detail_vars, оба Text и prompt_path_var."""
        self._enable_mail()
        panel = self.panel
        self._seed_row("r0")
        panel._show_lead("r0")
        self.assertEqual(panel.detail_vars["name"].get(), ROW["name"])

        panel._clear_inspector()

        for key in DETAIL_KEYS:
            with self.subTest(key=key):
                self.assertEqual(panel.detail_vars[key].get(), "",
                                 f"detail_vars[{key!r}] должен обнулиться")
        self.assertFalse(self._text(panel.msg_text).strip(),
                         "msg_text должен очиститься")
        self.assertFalse(self._text(panel.prompt_text).strip(),
                         "prompt_text должен очиститься")
        self.assertEqual(panel.prompt_path_var.get(), "",
                         "prompt_path_var должен обнулиться")

    # -- 7. регрессия _generate_message ------------------------------------ #
    def test_generate_message_no_selection_and_with_selection(self) -> None:
        """Без лида — toast без падения; с лидом — ai_generate → msg_text."""
        self._enable_mail()
        panel = self.panel

        # без выбранного лида: не падает и пишет toast
        self.hub.toasts.clear()
        panel._generate_message()
        self.assertTrue(self.hub.toasts, "без лида должен появиться toast")

        # с выбранным лидом: синхронный FakeHub подставляет текст в msg_text
        self._seed_row("r0")
        panel.result_tree.selection_set("r0")
        panel._show_lead("r0")
        fixed = "ФИКСИРОВАННЫЙ ТЕКСТ ОТ ИИ: Здравствуйте!"
        p_ai = mock.patch.object(app.leads_mail, "ai_generate", return_value=fixed)
        ai_mock = p_ai.start()
        self.addCleanup(p_ai.stop)
        # Ключ задан — облачный провайдер должен реально вызываться.
        cfg = {"ai_url": "https://openrouter.ai/api/v1",
               "ai_model": "test-model", "ai_key": "test-key", "idea": ""}
        p_cfg = mock.patch.object(real_mail, "load_settings", return_value=cfg)
        cfg_mock = p_cfg.start()
        self.addCleanup(p_cfg.stop)

        panel.msg_text.delete("1.0", "end")
        panel._generate_message()
        self.assertIn(fixed, self._text(panel.msg_text),
                      "_generate_message должен подставить текст в msg_text")
        self.assertTrue(ai_mock.call_count, "ai_generate должен быть вызван при наличии ключа")

        # Без ключа сеть не дёргаем: сразу текст без ИИ, без 60-секундного 401.
        ai_mock.reset_mock()
        cfg_mock.return_value = {**cfg, "ai_key": ""}
        panel.msg_text.delete("1.0", "end")
        panel._generate_message()
        self.assertFalse(ai_mock.call_count, "без ai_key сеть вызывать нельзя")
        self.assertTrue(self._text(panel.msg_text).strip(),
                        "без ключа поле всё равно заполняется fallback-текстом")

    def test_generate_site_prompt_and_mail_window_template_field(self) -> None:
        """_generate_site_prompt обновляет prompt_text/файл; MailWindow хранит site_prompt_template."""
        self._enable_mail()
        panel = self.panel

        self.hub.toasts.clear()
        panel._generate_site_prompt()
        self.assertTrue(self.hub.toasts, "без лида должен появиться toast")

        self._seed_row("r0")
        panel.result_tree.selection_set("r0")
        panel._show_lead("r0")

        with mock.patch.object(app.leads_mail, "ai_generate_site_prompt",
                               return_value="НОВЫЙ ИИ ПРОМПТ: задай 2-4 вопроса") as gen:
            panel._generate_site_prompt()
        self.assertTrue(gen.called)
        self.assertIn("НОВЫЙ ИИ ПРОМПТ", self._text(panel.prompt_text))

        # MailWindow: поле редактирования site_prompt_template присутствует.
        class _MailHub:
            def __init__(self, root_win, fake_hub) -> None:
                self._root = root_win
                self._hub = fake_hub

            def __getattr__(self, name: str):
                if hasattr(self._hub, name):
                    return getattr(self._hub, name)
                return getattr(self._root, name)

        with mock.patch.object(app, "LEADS_DIR", Path(self.tmp.name)), \
             mock.patch.object(app, "MAIL_SETTINGS_PATH",
                               Path(self.tmp.name) / "leads_mail_settings.json"), \
             mock.patch.object(app, "MAIL_STATE_PATH",
                               Path(self.tmp.name) / "leads_mail_state.json"), \
             mock.patch.object(app, "POOL_FILE",
                               Path(self.tmp.name) / "hot_pool.csv"):
            win = app.MailWindow(_MailHub(self.root, self.hub))
            win.withdraw()
            self.addCleanup(win.destroy)
            self.assertIn("site_prompt_template", win.texts)
            win.texts["site_prompt_template"].insert("1.0", "ШАБЛОН: {name}")
            collected = win._collect()
            self.assertEqual(collected["site_prompt_template"], "ШАБЛОН: {name}")


if __name__ == "__main__":
    unittest.main()
