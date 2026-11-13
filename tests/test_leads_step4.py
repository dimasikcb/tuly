"""ШАГ 4 LEADS_PLAN.md: ручное подтверждение и одиночная отправка лида.

Проверяются: combobox каналов в карточке-инспекторе, _send_one() (реальный
dispatch + отметка в журнале MailState и в пуле), dry_run без сети, _skip_lead(),
_remove_lead(), а также новые нерегрессионные точки leads_mail: публичная
record_send() и Mailer._try_send() после её появления.

Тесты пишутся под контракт UI, который реализует параллельный агент в
tools.pyw. Здесь НЕ редактируются tools.pyw / leads2gis.py. Паттерн и изоляция
скопированы из tests/test_leads_step3.py: настоящий Tk-рут без mainloop,
фейковый hub, все пути (POOL_FILE, MAIL_STATE_PATH, MAIL_SETTINGS_PATH, файл
промпта) ведут в tempfile.TemporaryDirectory() — реальные hot_pool.csv /
leads_mail_state.json / leads_mail_settings.json / leads_prompts/ не трогаются.
"""
from __future__ import annotations

import json
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

from helpers import load_module, new_tk

leads_panel = load_module("leads_panel")
import leads_pool  # хозяин путей: тесты патчат атрибуты модуля, а не снимок имён
import uibase

from leads_panel import LeadsPanel

import leads_mail as real_mail  # реальный модуль (sys.modules совпадает с leads_panel.leads_mail)

if not leads_panel.LEADS_OK:
    raise unittest.SkipTest(f"leads2gis недоступен: {leads_panel.LEADS_ERROR}")


class FakeHub:
    """Как в test_leads_step3.py: синхронный run_bg, мгновенный post."""

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


# Полная строка лида: все поля из контракта ШАГА 3 + tier=hot, чтобы работал
# pool_add() (в пул принимаются только горячие). topic и rubric совпадают, чтобы
# не зависеть от того, какое из полей UI берёт для строки «ниша».
ROW = {
    "name": "Барбершоп Борода",
    "topic": "Барбершоп",
    "address": "ул. Ленина, 1",
    "rating": "4.9",
    "reviews": "128",
    "phone": "+7 999 111-22-33",
    "telegram": "https://t.me/boroda",
    "vk": "https://vk.com/boroda",
    "city": "Москва",
    "rubric": "Барбершоп",
    "tier": "hot",
}
# Второй лид — для переходов «следующий» и пула на две строки.
ROW_B = {
    "name": "Кафе Ромашка",
    "topic": "Кафе",
    "address": "ул. Садовая, 2",
    "rating": "4.7",
    "reviews": "82",
    "phone": "+79002223344",
    "telegram": "https://t.me/romashka",
    "vk": "",
    "city": "Москва",
    "rubric": "Кафе",
    "tier": "hot",
}
MSG = "Тестовое сообщение"


class LeadsStep4PanelTest(unittest.TestCase):
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

        p_pool = mock.patch.object(leads_pool, "POOL_FILE", tmp_path / "hot_pool.csv")
        p_pool.start()
        self.addCleanup(p_pool.stop)

        p_state = mock.patch.object(
            leads_pool, "MAIL_STATE_PATH", tmp_path / "leads_mail_state.json")
        p_state.start()
        self.addCleanup(p_state.stop)

        p_settings = mock.patch.object(
            leads_pool, "MAIL_SETTINGS_PATH", tmp_path / "leads_mail_settings.json")
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
        p_mail_ok = mock.patch.object(leads_panel, "MAIL_OK", False)
        p_mail_ok.start()
        self.addCleanup(p_mail_ok.stop)

        self.hub = FakeHub()
        self.panel = LeadsPanel(self.root, self.hub)

    # -- helpers ----------------------------------------------------------- #
    def _enable_mail(self) -> None:
        """После создания панели включаем MAIL_OK и реальный модуль рассылки."""
        p_ok = mock.patch.object(leads_panel, "MAIL_OK", True)
        p_ok.start()
        self.addCleanup(p_ok.stop)
        p_mod = mock.patch.object(leads_panel, "leads_mail", real_mail)
        p_mod.start()
        self.addCleanup(p_mod.stop)

    @staticmethod
    def _text(widget: tk.Text) -> str:
        return widget.get("1.0", "end")

    def _seed_row(self, iid: str, row: dict) -> None:
        """Строка в self._rows + соответствующий iid в result_tree."""
        self.panel._rows[iid] = dict(row)
        tree = self.panel.result_tree
        if iid not in tree.get_children():
            tree.insert("", "end", iid=iid, values=(
                row["name"], row["topic"], row["phone"], "2ГИС"))

    def _journal(self) -> dict:
        """Свежий журнал отправок с диска (tmp MAIL_STATE_PATH)."""
        path = Path(leads_pool.MAIL_STATE_PATH)
        self.assertTrue(path.is_file(), f"журнал не создан: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    # -- 1. combobox каналов ------------------------------------------------ #
    def test_channel_combobox_filled_from_row(self) -> None:
        """_show_lead: подписи доступных каналов по PRIORITY, первая выбрана."""
        self._enable_mail()
        panel = self.panel
        self._seed_row("r0", ROW)
        panel._show_lead("r0")

        avail = real_mail.channels_of(ROW)
        expected = [real_mail.channel_label(ch)
                    for ch in real_mail.PRIORITY if ch in avail]
        # Санитарная проверка фикстуры: MAX у лида нет → только Telegram и ВК.
        self.assertEqual(expected, ["Telegram", "ВК"])

        self.assertEqual(list(panel.channel_box["values"]), expected,
                         "combobox должен перечислять каналы лида по приоритету")
        self.assertEqual(panel.channel_var.get(), expected[0],
                         "по умолчанию выбирается первый по приоритету канал")

        self.assertEqual(list(panel._channel_map), expected)
        # Подпись → канал обратимо: подписи уникальны и совпадают с channel_label.
        self.assertEqual(len(set(panel._channel_map.values())),
                         len(panel._channel_map))
        for label, channel in panel._channel_map.items():
            with self.subTest(label=label):
                self.assertEqual(real_mail.channel_label(channel), label)
                self.assertIn(channel, avail)

    # -- 2. одиночная отправка ---------------------------------------------- #
    def test_send_one_dispatches_edited_text_once(self) -> None:
        """_send_one: dispatch один раз с правленым текстом; журнал, пул, переход."""
        self._enable_mail()
        panel = self.panel
        self._seed_row("r0", ROW)
        self._seed_row("r1", ROW_B)
        # Лид заранее в пуле — после отправки проверяем sent_at/channel в CSV.
        real_mail.pool_add(leads_pool.POOL_FILE, [ROW])
        panel._show_lead("r0")

        panel.msg_text.delete("1.0", "end")
        panel.msg_text.insert("1.0", MSG)

        contact = real_mail.channels_of(ROW)["telegram"]  # («boroda», «username»)
        with mock.patch.object(real_mail, "dispatch",
                               return_value={"msg_id": 7, "peer": 9}) as captured:
            panel._send_one()

        self.assertEqual(captured.call_count, 1, "dispatch должен вызываться ровно один раз")
        args = captured.call_args.args
        self.assertEqual(args[0], "telegram")
        self.assertIsInstance(args[1], dict, "вторым аргументом идут настройки")
        self.assertEqual(args[2], "boroda")
        self.assertEqual(args[2], contact[0])
        self.assertEqual(args[3], contact[1])
        self.assertEqual(args[4], MSG, "в канал должен уйти ОТРЕДАКТИРОВАННЫЙ текст")

        # Журнал: отметка record_send видна на диске.
        st = self._journal()[real_mail.pool_key(ROW)]
        self.assertEqual(st["status"], "awaiting")
        self.assertEqual(st["sent_id"], 7)
        self.assertEqual(st["peer"], 9)
        self.assertEqual(st["channel"], "telegram")
        self.assertEqual(st["target"], "boroda")
        self.assertEqual(st["text"], MSG)
        self.assertTrue(st["sent_at"])
        self.assertIs(st["attempts"][-1]["ok"], True)
        self.assertEqual(st["attempts"][-1]["channel"], "telegram")

        # Пул: строка помечена отправленной.
        key = real_mail.pool_key(ROW)
        pool_row = next(r for r in real_mail.pool_load(leads_pool.POOL_FILE)
                        if real_mail.pool_key(r) == key)
        self.assertNotEqual(pool_row["sent_at"], "")
        self.assertEqual(pool_row["channel"], "telegram")

        # Выделение перешло на следующую строку, в логе отметка «→».
        self.assertEqual(tuple(panel.result_tree.selection()), ("r1",))
        self.assertIn("→", self._text(panel.log_box))
        self.assertTrue(any("отправлено" in t for t in self.hub.toasts))

    # -- 3. dry_run без сети ------------------------------------------------- #
    def test_send_one_dry_run_no_network(self) -> None:
        """dry_run=true: настоящий dispatch выходит рано, отметка и лог на месте."""
        real_mail.save_settings(leads_pool.MAIL_SETTINGS_PATH,
                                {**real_mail.DEFAULT_SETTINGS, "dry_run": True})
        self._enable_mail()
        panel = self.panel
        self._seed_row("r0", ROW)
        panel._show_lead("r0")
        self.assertTrue(self._text(panel.msg_text).strip(),
                        "карточка должна предложить текст до отправки")

        # dispatch НЕ патчим: при dry_run он обязан вернуться до сети,
        # даже когда Telegram не настроен (api_id пуст).
        panel._send_one()

        st = self._journal()[real_mail.pool_key(ROW)]
        self.assertEqual(st["status"], "awaiting")
        self.assertEqual(st["channel"], "telegram")
        self.assertIsNone(st["sent_id"], "в тестовом режиме msg_id нет")
        self.assertIs(st["attempts"][-1]["ok"], True)

        log = self._text(panel.log_box)
        self.assertIn("тестовый режим", log)
        self.assertTrue(any("тестовый режим" in t for t in self.hub.toasts))

    # -- 4. отправка без выделения ------------------------------------------- #
    def test_send_one_without_selection_toasts(self) -> None:
        """Пустая панель: _send_one() даёт toast и не дёргает dispatch."""
        self._enable_mail()
        panel = self.panel
        self.assertEqual(panel.result_tree.get_children(), (),
                         "панель должна собраться пустой (MAIL_OK=False)")

        with mock.patch.object(real_mail, "dispatch") as captured:
            panel._send_one()

        self.assertTrue(self.hub.toasts, "без выбранного лида должен появиться toast")
        self.assertFalse(captured.called, "dispatch без лида вызывать нельзя")

    # -- 5. пропуск лида ------------------------------------------------------ #
    def test_skip_moves_selection_and_last_row_toast(self) -> None:
        """Пропустить: выделение на следующей строке; на последней — toast."""
        panel = self.panel
        self._seed_row("r0", ROW)
        self._seed_row("r1", ROW_B)
        panel.result_tree.selection_set("r0")

        panel._skip_lead()
        self.assertEqual(tuple(panel.result_tree.selection()), ("r1",),
                         "пропуск должен перевести выделение на следующую строку")

        self.hub.toasts.clear()
        panel._skip_lead()
        self.assertEqual(tuple(panel.result_tree.selection()), ("r1",),
                         "на последней строке выделение не меняется")
        self.assertTrue(any("последний" in t for t in self.hub.toasts),
                        f"нужен toast про последний лид, получено: {self.hub.toasts}")

    # -- 6. удаление из пула --------------------------------------------------- #
    def test_remove_lead_asks_and_removes(self) -> None:
        """Удаление лида: «Да» чистит таблицу и пул, «Нет» оставляет строку."""
        self._enable_mail()
        panel = self.panel
        real_mail.pool_add(leads_pool.POOL_FILE, [ROW])
        self._seed_row("r0", ROW)
        panel.result_tree.selection_set("r0")

        with mock.patch.object(uibase.messagebox, "askyesno", return_value=True) as ask:
            panel._remove_lead()
        self.assertTrue(ask.called, "удаление должно спрашивать подтверждение")
        self.assertNotIn("r0", panel.result_tree.get_children(),
                         "после подтверждения строка уходит из таблицы")
        self.assertEqual(real_mail.pool_load(leads_pool.POOL_FILE), [],
                         "после подтверждения пул пуст")

        # Вариант «Нет»: строка остаётся и в таблице, и в пуле.
        real_mail.pool_add(leads_pool.POOL_FILE, [ROW])
        panel.refresh()  # перечитывает tmp-пул и снова выделяет r0
        self.assertIn("r0", panel.result_tree.get_children())
        with mock.patch.object(uibase.messagebox, "askyesno", return_value=False):
            panel._remove_lead()
        self.assertIn("r0", panel.result_tree.get_children(),
                      "без подтверждения строка должна остаться")
        self.assertEqual(len(real_mail.pool_load(leads_pool.POOL_FILE)), 1)

    # -- 6b. скрытие написанных лидов из выдачи -------------------------------- #
    def test_sent_lead_disappears_from_table(self) -> None:
        """После «Отправить» лид исчезает из выдачи, но остаётся в пуле."""
        self._enable_mail()
        panel = self.panel
        real_mail.pool_add(leads_pool.POOL_FILE, [ROW, ROW_B])
        panel.refresh()
        tree = panel.result_tree

        def iid_by_name(name: str) -> str | None:
            for iid in tree.get_children():
                if tree.set(iid, "name") == name:
                    return iid
            return None

        iid_row, iid_b = iid_by_name(ROW["name"]), iid_by_name(ROW_B["name"])
        self.assertIsNotNone(iid_row, "лид должен быть виден в выдаче до отправки")
        self.assertIsNotNone(iid_b)
        panel._show_lead(iid_row)
        panel.msg_text.delete("1.0", "end")
        panel.msg_text.insert("1.0", MSG)

        with mock.patch.object(real_mail, "dispatch",
                               return_value={"msg_id": 7, "peer": 9}):
            panel._send_one()

        self.assertEqual(iid_by_name(ROW["name"]), None,
                         "написанный лид должен исчезнуть из выдачи")
        self.assertEqual([tree.set(i, "name") for i in tree.get_children()],
                         [ROW_B["name"]], "в выдаче остаётся только ненаписанный лид")
        self.assertTrue(all(r.get("name") != ROW["name"]
                            for r in panel._rows.values()),
                        "self._rows не должен содержать отправленного")
        self.assertEqual(panel._hidden_contacted, 1)
        self.assertIn("написанных скрыто", panel.status_var.get())

        # Выделение и карточка переехали на следующего лида. В живом GUI карточку
        # обновляет <<TreeviewSelect>>; без mainloop доставляем событие вручную
        # (фолбэк как в test_leads_step3.test_treeview_select_updates_card).
        new_iid_b = iid_by_name(ROW_B["name"])
        self.assertEqual(tuple(tree.selection()), (new_iid_b,))
        panel._on_select()
        self.assertEqual(panel._current_row.get("name"), ROW_B["name"])

        # Скрытие ≠ удаление: в пуле обе строки, у написанного sent_at проставлен.
        rows = real_mail.pool_load(leads_pool.POOL_FILE)
        self.assertEqual(len(rows), 2, "скрытие не должно удалять лид из пула")
        sent = next(r for r in rows if r["name"] == ROW["name"])
        self.assertTrue(sent["sent_at"])
        self.assertEqual(sent["channel"], "telegram")

        # Журнал: отметка awaiting с сохранённым текстом сообщения.
        st = self._journal()[real_mail.pool_key(ROW)]
        self.assertEqual(st["status"], "awaiting")
        self.assertEqual(st["text"], MSG)

    def test_stopped_lead_still_visible(self) -> None:
        """«Остановить лид» — это не «написал»: строка остаётся в выдаче."""
        self._enable_mail()
        panel = self.panel
        real_mail.pool_add(leads_pool.POOL_FILE, [ROW])
        panel.refresh()
        tree = panel.result_tree
        self.assertTrue(any(tree.set(i, "name") == ROW["name"]
                            for i in tree.get_children()))

        real_mail.pool_mark_status(
            leads_pool.POOL_FILE, real_mail.pool_key(ROW), "stopped")
        panel.refresh()
        self.assertTrue(any(tree.set(i, "name") == ROW["name"]
                            for i in tree.get_children()),
                        "stopped без отправки не должен скрываться из выдачи")
        self.assertEqual(panel._hidden_contacted, 0)


class LeadsStep4LogicTest(unittest.TestCase):
    """Тесты 7-8: record_send() и регрессия Mailer._try_send() — без GUI."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        tmp_path = Path(self.tmp.name)
        self.state_path = tmp_path / "leads_mail_state.json"
        self.pool_file = tmp_path / "hot_pool.csv"

    def _journal(self) -> dict:
        return json.loads(self.state_path.read_text(encoding="utf-8"))

    # -- 9. is_contacted --------------------------------------------------------- #
    def test_is_contacted_unit(self) -> None:
        """is_contacted: sent_at или статус «написан» — да; stopped/пустые — нет."""
        base = {"name": "Тест", "phone": "+7 999 000-00-00"}
        self.assertFalse(real_mail.is_contacted(dict(base)))
        self.assertTrue(real_mail.is_contacted(
            {**base, "sent_at": "2026-11-12T10:00:00"}))
        for status in real_mail.CONTACTED_STATUSES:
            with self.subTest(status=status):
                self.assertTrue(real_mail.is_contacted({**base, "status": status}))
        self.assertFalse(real_mail.is_contacted({**base, "status": "stopped"}),
                         "«Остановить» без отправки — не контакт")
        self.assertTrue(real_mail.is_contacted({**base, "status": " Awaiting "}),
                        "регистр и пробелы не ломают проверку")
        self.assertFalse(real_mail.is_contacted({**base, "sent_at": "   "}),
                         "пробельный sent_at считается пустым")

    # -- 7. record_send -------------------------------------------------------- #
    def test_record_send_unit(self) -> None:
        """record_send: поля журнала как при одиночной отправке; text не стирается."""
        state = real_mail.MailState(self.state_path)
        lead_id = real_mail.row_id(ROW)
        self.assertEqual(lead_id, real_mail.pool_key(ROW), "у ROW нет id 2GIS")
        state.get(lead_id)["seen_at"] = "2026-11-01T00:00:00"

        real_mail.record_send(state, ROW, "telegram", "boroda",
                              {"msg_id": 1, "peer": 2}, text="привет")

        st = self._journal()[lead_id]
        self.assertEqual(st["status"], "awaiting")
        self.assertEqual(st["channel"], "telegram")
        self.assertEqual(st["target"], "boroda")
        self.assertEqual(st["sent_id"], 1)
        self.assertEqual(st["peer"], 2)
        self.assertEqual(st["text"], "привет")
        self.assertTrue(st["sent_at"])
        self.assertEqual(st["sent_at"], st["attempts"][-1]["at"],
                         "sent_at и время попытки — одна отметка")
        self.assertIs(st["attempts"][-1]["ok"], True)
        self.assertNotIn("seen_at", st, "новая отправка снимает отметку прочтения")

        # Повторный вызов без text: текст остаётся, история растёт.
        real_mail.record_send(state, ROW, "vk", "boroda", {"msg_id": 3, "peer": 4})
        st = self._journal()[lead_id]
        self.assertEqual(st["text"], "привет", "text=None не должен стирать сообщение")
        self.assertEqual(st["channel"], "vk")
        self.assertEqual(st["sent_id"], 3)
        self.assertEqual(st["status"], "awaiting")
        self.assertEqual(len(st["attempts"]), 2)
        self.assertIs(st["attempts"][-1]["ok"], True)

    # -- 8. регрессия Mailer._try_send ------------------------------------------- #
    def test_try_send_still_marks(self) -> None:
        """Mailer после рефакторинга: step() отправляет и отмечает журнал и пул."""
        logs: list[tuple[str, str]] = []
        events: list[dict] = []
        real_mail.pool_add(self.pool_file, [ROW])
        state = real_mail.MailState(self.state_path)
        settings = {**real_mail.DEFAULT_SETTINGS, "dry_run": True}
        mailer = real_mail.Mailer(
            [dict(ROW)], settings, state,
            on_log=lambda msg, kind="": logs.append((msg, kind)),
            on_event=events.append,
            pool_file=self.pool_file,
        )

        with mock.patch.object(real_mail, "dispatch",
                               return_value={"msg_id": None, "peer": None}) as captured:
            sent = mailer.step()

        self.assertTrue(sent, "step() должен отправить первому доступному лиду")
        self.assertTrue(captured.called, "очередь обязана вызывать dispatch")
        self.assertEqual(captured.call_args.args[0], "telegram",
                         "первый по приоритету канал лида — telegram")

        lead_id = real_mail.row_id(ROW)
        st = self._journal()[lead_id]
        self.assertEqual(st["status"], "awaiting")
        self.assertEqual(st["channel"], "telegram")
        self.assertEqual(st["target"], "boroda")
        self.assertIs(st["attempts"][-1]["ok"], True)

        pool_row = next(r for r in real_mail.pool_load(self.pool_file)
                        if real_mail.pool_key(r) == lead_id)
        self.assertNotEqual(pool_row["sent_at"], "")
        self.assertEqual(pool_row["channel"], "telegram")

        self.assertTrue(any("→" in msg for msg, _ in logs),
                        f"в логе нет отметки отправки: {logs}")
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["lead"], lead_id)
        self.assertEqual(events[0]["channel"], "telegram")
        self.assertEqual(events[0]["target"], "boroda")
        self.assertTrue(events[0]["text"].strip(), "в событие уходит непустой текст")


if __name__ == "__main__":
    unittest.main()
