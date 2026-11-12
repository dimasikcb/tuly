"""ШАГ 3: пул обязан хранить детали карточки (адрес, рейтинг, отзывы, рубрику).

Без этих колонок инспектор в tools.pyw показывал бы «—» на реальных лидах:
leads_collect -> pool_add -> hot_pool.csv -> pool_load -> LeadsPanel._rows.
"""
from __future__ import annotations

import csv
import tempfile
import unittest
from pathlib import Path

import leads_pool as P

LEAD = {
    "name": "Барбершоп Борода",
    "phone": "+79001112233",
    "city": "moscow",
    "topic": "барбершоп",
    "rubric": "Барбершоп",
    "url": "https://2gis.ru/moscow/firm/1",
    "address": "ул. Ленина, 1",
    "rating": 4.9,
    "reviews": 128,
    "score": 90,
    "tier": "hot",
    "telegram": "https://t.me/boroda",
}
FIELDS = ("address", "rating", "reviews", "rubric")


class Step3PoolDetailsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pool = Path(self.tmp.name) / "hot_pool.csv"

    def test_columns_declared(self) -> None:
        for field in FIELDS:
            with self.subTest(field=field):
                self.assertIn(field, P.POOL_COLUMNS)

    def test_pool_add_keeps_details(self) -> None:
        """Свежий лид: детали доезжают до pool_load без потерь."""
        self.assertEqual(P.pool_add(self.pool, [LEAD]), 1)
        row = P.pool_load(self.pool)[0]
        self.assertEqual(row["address"], "ул. Ленина, 1")
        self.assertEqual(row["rating"], "4.9")
        self.assertEqual(row["reviews"], "128")
        self.assertEqual(row["rubric"], "Барбершоп")

    def test_old_pool_is_backfilled(self) -> None:
        """CSV со старой схемой: при повторном сборе детали дозаполняются."""
        with self.pool.open("w", encoding="utf-8-sig", newline="") as fh:
            old = tuple(c for c in P.POOL_COLUMNS if c not in FIELDS)
            writer = csv.DictWriter(fh, fieldnames=old)
            writer.writeheader()
            writer.writerow({"name": LEAD["name"], "phone": LEAD["phone"],
                             "city": LEAD["city"], "topic": LEAD["topic"],
                             "found_at": "2026-11-11T20:00:00"})
        row = P.pool_load(self.pool)[0]
        self.assertEqual(row["address"], "")
        P.pool_add(self.pool, [LEAD])
        row = P.pool_load(self.pool)[0]
        self.assertEqual(row["address"], "ул. Ленина, 1")
        self.assertEqual(row["rating"], "4.9")

    def test_legacy_csv_without_new_columns_still_reads(self) -> None:
        """Старый файл без новых колонок не ломает чтение."""
        with self.pool.open("w", encoding="utf-8-sig", newline="") as fh:
            fh.write("name,phone,city,topic,found_at\n")
            fh.write("Кафе Ромашка,+79990001122,kazan,кафе,2026-11-11T20:00:00\n")
        rows = P.pool_load(self.pool)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["address"], "")
        self.assertEqual(rows[0]["rating"], "")


if __name__ == "__main__":
    unittest.main()
