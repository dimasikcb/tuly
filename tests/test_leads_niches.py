"""Мульти-ниша парсера лидов: список NICHES и дедупликация по id."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from leads2gis import NICHES, dedup_by_id


class NichesTest(unittest.TestCase):
    def test_exactly_twelve_pairs(self) -> None:
        """NICHES содержит ровно 12 пар (подпись, запрос)."""
        self.assertEqual(len(NICHES), 12)
        for pair in NICHES:
            with self.subTest(pair=pair):
                self.assertEqual(len(pair), 2)

    def test_labels_unique(self) -> None:
        """Все подписи уникальны — иначе в UI будут одинаковые пункты."""
        labels = [label for label, _ in NICHES]
        self.assertEqual(len(labels), len(set(labels)))

    def test_queries_unique_lowercase_nonempty(self) -> None:
        """Все запросы уникальны, непустые и в нижнем регистре — так ждёт 2GIS."""
        queries = [query for _, query in NICHES]
        self.assertEqual(len(queries), len(set(queries)))
        for query in queries:
            with self.subTest(query=query):
                self.assertIsInstance(query, str)
                self.assertTrue(query.strip())
                self.assertEqual(query, query.lower())


class DedupByIdTest(unittest.TestCase):
    def test_removes_duplicates_keeping_first_and_order(self) -> None:
        """Дубли по id схлопываются в первое вхождение, порядок сохраняется."""
        leads = [
            {"id": "1", "name": "первый"},
            {"id": "2", "name": "второй"},
            {"id": "1", "name": "дубль первого"},
            {"id": "3", "name": "третий"},
            {"id": "2", "name": "дубль второго"},
        ]
        result = dedup_by_id(leads)
        self.assertEqual([lead["name"] for lead in result],
                         ["первый", "второй", "третий"])

    def test_leads_without_id_are_all_kept(self) -> None:
        """Лиды без ключа id и с пустым id не теряются — считаются уникальными."""
        leads = [
            {"name": "без id"},
            {"id": "", "name": "пустой id"},
            {"name": "тоже без id"},
            {"id": "x", "name": "с id"},
            {"id": "x", "name": "дубль с id"},
        ]
        result = dedup_by_id(leads)
        self.assertEqual(len(result), 4)
        self.assertEqual([lead["name"] for lead in result],
                         ["без id", "пустой id", "тоже без id", "с id"])

    def test_empty_list(self) -> None:
        """Пустой список на входе даёт пустой список на выходе."""
        self.assertEqual(dedup_by_id([]), [])

    def test_does_not_mutate_input(self) -> None:
        """Входной список не мутируется — функция чистая."""
        leads = [{"id": "1"}, {"id": "1"}, {"id": "2"}]
        snapshot = list(leads)
        dedup_by_id(leads)
        self.assertEqual(leads, snapshot)
        self.assertEqual(len(leads), 3)


if __name__ == "__main__":
    unittest.main()
