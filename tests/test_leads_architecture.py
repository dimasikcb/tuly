"""Архитектурные инварианты лидов: тесты под НОВЫЕ API слоёв.

Обычный unittest, БЕЗ tkinter и БЕЗ сети. Все файлы создаются в
tempfile.TemporaryDirectory() из setUp (addCleanup(tmp.cleanup)). Чужие
модули импортируются лениво — незавершённая правка соседнего агента ломает
только свой тест, а не сбор всего модуля. Проверки не ослабляются: тесты
фиксируют контракт, даже если API ещё не готов (тогда тест падает — это
ожидаемо до финального прогона).
"""
from __future__ import annotations

import ast
import csv
import importlib
import re
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _module(name: str):
    """Ленивый импорт: незавершённый модуль ломает только свой тест."""
    return importlib.import_module(name)


NAME = "Барбершоп Борода"          # _translit -> "barbershop-boroda"
PHONE = "+79001112233"
FULL_ROW = {
    "name": NAME,
    "topic": "барбершоп",
    "address": "ул. Ленина, 1",
    "rating": "4.9",
    "reviews": "128",
    "phone": PHONE,
    "telegram": "https://t.me/barber_boroda",
    "vk": "https://vk.com/club_boroda",
    # и прямые поля-ссылки, и messengers со ссылками: любой путь даёт t.me/vk.com
    "messengers": "telegram:https://t.me/barber_boroda; vk:https://vk.com/club_boroda",
}
EXPECTED_FIELDS = ("name", "topic", "address", "rating", "phone", "messengers")

# «Старая» 13-колоночная схема пула (до ШАГА 3): без address/rating/reviews/rubric.
OLD_POOL_COLUMNS = (
    "name", "telegram", "max", "vk", "email", "phone", "city", "topic",
    "card", "found_at", "sent_at", "channel", "status",
)
LEGACY_COLUMNS = (
    "name", "phone", "tier", "score", "query", "url",
    "address", "rating", "reviews", "rubric",
)
EXPECTED_DETAILS = {
    "address": "Ленина",
    "rating": "4.9",
    "reviews": "128",
    "rubric": "барбершоп",
}


class _TempDirTestCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)

    def _write_csv(self, filename: str, fieldnames, row: dict) -> Path:
        path = self.dir / filename
        with path.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(fieldnames))
            writer.writeheader()
            writer.writerow(row)
        return path


class InspectorFieldsTest(_TempDirTestCase):
    def test_fields_constant(self) -> None:
        pool = _module("leads_pool")
        self.assertEqual(tuple(pool.INSPECTOR_FIELDS), EXPECTED_FIELDS)

    def test_full_row(self) -> None:
        pool = _module("leads_pool")
        result = pool.inspector_fields(FULL_ROW)
        self.assertEqual(set(result), set(EXPECTED_FIELDS))
        self.assertEqual(result["name"], NAME)
        self.assertEqual(result["address"], "ул. Ленина, 1")
        self.assertEqual(result["topic"], "барбершоп")
        self.assertIn("4.9", str(result["rating"]))
        self.assertIn("128", str(result["rating"]))
        digits = re.sub(r"\D", "", str(result["phone"]))
        self.assertGreaterEqual(len(digits), 10)
        for chunk in ("900", "111", "22", "33"):
            self.assertIn(chunk, digits)
        self.assertIn("t.me/", str(result["messengers"]))
        self.assertIn("vk.com/", str(result["messengers"]))

    def test_empty_row_all_dashes(self) -> None:
        pool = _module("leads_pool")
        result = pool.inspector_fields({})
        self.assertEqual(set(result), set(EXPECTED_FIELDS))
        for field in EXPECTED_FIELDS:
            with self.subTest(field=field):
                self.assertEqual(result[field], "—")


class MessengerSummaryTest(_TempDirTestCase):
    def _assert_works(self, contacts, row) -> str:
        result = contacts.messenger_summary(row)
        self.assertIsInstance(result, str)
        self.assertTrue(result.strip(), "непустой ввод должен давать непустую строку")
        self.assertNotEqual(result.strip(), "не найдены")
        return result

    def test_with_links(self) -> None:
        contacts = _module("leads_contacts")
        self._assert_works(contacts, {
            "telegram": "https://t.me/barber_boroda",
            "vk": "https://vk.com/club_boroda",
        })

    def test_messengers_as_string_and_list(self) -> None:
        contacts = _module("leads_contacts")
        self._assert_works(
            contacts, {"messengers": "telegram:barber_boroda; vk:club_boroda"})
        self._assert_works(
            contacts, {"messengers": ["telegram:barber_boroda", "vk:club_boroda"]})

    def test_empty_returns_not_found(self) -> None:
        contacts = _module("leads_contacts")
        cases = (
            ("пустой dict", {}),
            ("пустая строка", {"messengers": ""}),
            ("пустой список", {"messengers": []}),
        )
        for label, row in cases:
            with self.subTest(kind=label):
                self.assertEqual(contacts.messenger_summary(row), "не найдены")


class SitePromptFileTest(_TempDirTestCase):
    def test_city_paths_differ(self) -> None:
        mail = _module("leads_mail")
        base = {"name": NAME}
        mos = mail.site_prompt_file({**base, "city": "Москва"}, out_dir=self.dir)
        kaz = mail.site_prompt_file({**base, "city": "Казань"}, out_dir=self.dir)
        self.assertNotEqual(mos, kaz, "одноимённые фирмы из разных городов не должны коллизить")
        for path in (mos, kaz):
            with self.subTest(path=path.name):
                self.assertIsInstance(path, Path)
                self.assertEqual(path.suffix, ".txt")
                self.assertIn("barbershop-boroda", path.name)


class SitePromptPersistenceTest(_TempDirTestCase):
    @staticmethod
    def _row() -> dict:
        return {
            "name": NAME, "city": "Москва", "rubric": "барбершоп",
            "phone": PHONE, "rating": "4.9", "reviews": "128",
        }

    @staticmethod
    def _text_of(result):
        # read_or_create_site_prompt может вернуть str или (text, path).
        return result[0] if isinstance(result, (tuple, list)) else result

    def test_read_or_create_keeps_manual_edit(self) -> None:
        mail = _module("leads_mail")
        row = self._row()
        first = mail.read_or_create_site_prompt(row, {}, out_dir=self.dir)
        self.assertIsInstance(self._text_of(first), str)
        path = mail.site_prompt_file(row, out_dir=self.dir)
        self.assertTrue(path.exists(), "read_or_create_site_prompt должен создать файл")

        path.write_text("МОЯ ПРАВКА", encoding="utf-8")
        second = mail.read_or_create_site_prompt(row, {}, out_dir=self.dir)
        self.assertEqual(self._text_of(second), "МОЯ ПРАВКА")
        self.assertEqual(path.read_text(encoding="utf-8"), "МОЯ ПРАВКА")

    def test_save_site_prompt_overwrites_utf8(self) -> None:
        mail = _module("leads_mail")
        row = self._row()
        path = mail.site_prompt_file(row, out_dir=self.dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("СТАРЫЙ ТЕКСТ", encoding="utf-8")
        mail.save_site_prompt("новый", row, out_dir=self.dir)
        self.assertEqual(path.read_text(encoding="utf-8"), "новый")


class BackfillIntegrationTest(_TempDirTestCase):
    def _seed(self) -> Path:
        legacy = {
            "name": NAME, "phone": PHONE, "tier": "hot", "score": "90",
            "query": "барбершоп", "url": "https://2gis.ru/moscow/firm/1",
            "address": "ул. Ленина, 1", "rating": "4.9",
            "reviews": "128", "rubric": "барбершоп",
        }
        pool_row = {
            "name": NAME, "phone": PHONE,
            "telegram": "https://t.me/barber_boroda",
            "max": "", "vk": "https://vk.com/club_boroda", "email": "",
            "city": "Москва", "topic": "", "card": "",
            "found_at": "2026-01-01T00:00:00", "sent_at": "",
            "channel": "", "status": "",
        }
        self._write_csv("leads.csv", LEGACY_COLUMNS, legacy)
        return self._write_csv("hot_pool.csv", OLD_POOL_COLUMNS, pool_row)

    def _assert_details_filled(self, pool, pool_file) -> None:
        rows = pool.pool_load(pool_file)
        self.assertTrue(rows, "пул пуст")
        row = rows[0]
        self.assertEqual(pool.pool_key(row), pool.pool_key({"name": NAME, "phone": PHONE}))
        for field, needle in EXPECTED_DETAILS.items():
            with self.subTest(field=field):
                value = str(row.get(field) or "")
                self.assertTrue(value.strip(), f"{field} не дозаполнен")
                self.assertIn(needle, value)

    def test_pool_refresh_backfills_card_fields(self) -> None:
        pool = _module("leads_pool")
        pool_file = self._seed()
        pool.pool_refresh(pool_file)
        self._assert_details_filled(pool, pool_file)

    def test_migrate_legacy_backfills_card_fields(self) -> None:
        pool = _module("leads_pool")
        pool_file = self._seed()
        pool.migrate_legacy(self.dir)
        self._assert_details_filled(pool, pool_file)


class InspectorIndependenceTest(_TempDirTestCase):
    def test_pool_does_not_import_mail(self) -> None:
        """inspector_fields не зависит от leads_mail: AST, а не текстовый grep."""
        tree = ast.parse((ROOT / "leads_pool.py").read_text(encoding="utf-8"))
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                imported.add(node.module.split(".")[0])
        self.assertNotIn("leads_mail", imported,
                         "leads_pool.py не должен импортировать leads_mail")


if __name__ == "__main__":
    unittest.main()
