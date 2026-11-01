"""Офлайн-тесты пула горячих лидов и атомарной записи CSV."""

from __future__ import annotations

import csv
import os
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import leads_mail as M
from leads2gis import LeadsFileBusy, Store

HOT = {
    "id": "1", "name": "Барбершоп", "rubric": "барбершоп", "phone": "+79001112233",
    "email": "a@x.ru", "telegram": "barber_a", "max": "", "vk": "club1",
    "city": "moscow", "address": "ул 1", "score": "88", "tier": "hot",
}
WARM = {**HOT, "id": "2", "name": "Кафе", "phone": "+79002223344",
        "tier": "warm", "score": "50"}


def _csv(tmp: Path) -> Path:
    path = tmp / "leads.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["id", "name", "tier", "score"])
        writer.writeheader()
        for lead in (HOT, WARM):
            writer.writerow({"id": lead["id"], "name": lead["name"],
                             "tier": lead["tier"], "score": lead["score"]})
    return path


def test_pool_add_only_hot_and_dedup() -> None:
    tmp = Path(tempfile.mkdtemp())
    path = _csv(tmp)

    assert M.pool_add(path, [HOT, WARM]) == 1
    rows = M.pool_load(path)
    assert [r["name"] for r in rows] == ["Барбершоп"], rows

    first = rows[0]["found_at"]
    assert M.pool_add(path, [HOT]) == 0
    assert M.pool_load(path)[0]["found_at"] == first, "дата находки не должна сдвигаться"


def test_pool_stores_links_not_nicks() -> None:
    """В файле пула лежат готовые ссылки — их видно и можно кликнуть."""
    tmp = Path(tempfile.mkdtemp())
    path = _csv(tmp)
    M.pool_add(path, [HOT])

    row = M.pool_load(path)[0]
    assert row["telegram"] == "https://t.me/barber_a", row
    assert row["vk"] == "https://vk.com/club1", row
    assert row["email"] == "mailto:a@x.ru", row
    assert "id" not in M.POOL_COLUMNS, "числовой id в пуле не храним"

    with M.pool_path(path).open(encoding="utf-8-sig", newline="") as fh:
        header = next(csv.reader(fh))
    assert "https://t.me/barber_a" in M.pool_path(path).read_text(encoding="utf-8-sig")
    assert "id" not in header, header


def test_pool_add_accepts_parser_lead() -> None:
    """Лид из parse_firm без поля tier (только reason) тоже попадает в пул."""
    tmp = Path(tempfile.mkdtemp())
    path = _csv(tmp)
    parser_lead = {k: v for k, v in HOT.items() if k != "tier"}
    parser_lead["reason"] = "hot: нет сайта, есть телефон"

    assert M.pool_add(path, [parser_lead]) == 1
    assert M.pool_load(path)[0]["name"] == "Барбершоп"


def test_tier_of_from_reason_and_score() -> None:
    assert M._tier_of({"reason": "hot: нет сайта"}) == "hot"
    assert M._tier_of({"reason": "warm: есть сайт"}) == "warm"
    assert M._tier_of({"score": "80"}) == "hot"
    assert M._tier_of({"score": "50"}) == ""
    assert M._tier_of({"tier": "hot", "score": "10"}) == "hot"


def test_pool_sync_pulls_hot_from_csv() -> None:
    tmp = Path(tempfile.mkdtemp())
    path = _csv(tmp)

    assert M.pool_sync(path) == 1, "из CSV должен приехать только горячий"
    assert M.pool_sync(path) == 0, "повторный синк ничего не дублирует"
    assert [r["name"] for r in M.pool_load(path)] == ["Барбершоп"]


def test_load_hot_leads_without_pool_uses_reason() -> None:
    tmp = Path(tempfile.mkdtemp())
    path = tmp / "leads.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["name", "phone", "score", "reason"])
        writer.writeheader()
        writer.writerow({"name": "A", "phone": "+79001112233", "score": "90",
                         "reason": "hot: нет сайта"})
        writer.writerow({"name": "B", "phone": "+79002223344", "score": "50",
                         "reason": "warm: есть сайт"})

    rows = M.load_hot_leads(path)
    assert [r["name"] for r in rows] == ["A"], rows


def test_pool_purge_keeps_sent_and_fresh() -> None:
    tmp = Path(tempfile.mkdtemp())
    path = _csv(tmp)
    made = [
        {**HOT, "name": "old", "phone": "+79000000001"},
        {**HOT, "name": "sent", "phone": "+79000000002"},
        {**HOT, "name": "fresh", "phone": "+79000000003"},
    ]
    M.pool_add(path, made)

    rows = M.pool_load(path)
    old = datetime.now() - timedelta(days=6)
    for row in rows:
        if row["name"] in ("old", "sent"):
            row["found_at"] = old.isoformat(timespec="seconds")
    M._pool_save(path, rows)
    M.pool_mark_sent(path, M.pool_key(made[1]), "telegram")

    assert M.pool_purge(path, 5) == 1
    assert sorted(r["name"] for r in M.pool_load(path)) == ["fresh", "sent"]


def test_pool_marks_block_purge() -> None:
    tmp = Path(tempfile.mkdtemp())
    path = _csv(tmp)
    M.pool_add(path, [{**HOT, "name": "x", "phone": "+79000000009"}])
    rows = M.pool_load(path)
    rows[0]["found_at"] = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
    M._pool_save(path, rows)

    assert M.pool_mark_status(path, M.pool_key(rows[0]), "replied") is True
    assert M.pool_purge(path, 5) == 0
    assert M.load_hot_leads(path)[0]["name"] == "x"


def test_pool_stats_counts_expiring() -> None:
    tmp = Path(tempfile.mkdtemp())
    path = _csv(tmp)
    M.pool_add(path, [
        {**HOT, "name": "soon", "phone": "+79000000011"},
        {**HOT, "name": "later", "phone": "+79000000012"},
    ])
    rows = M.pool_load(path)
    for row in rows:
        days = 4.6 if row["name"] == "soon" else 1
        row["found_at"] = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    M._pool_save(path, rows)

    stats = M.pool_stats(path, 5)
    assert stats["total"] == 2 and stats["expiring"] == 1, stats


def test_load_hot_leads_prefers_pool() -> None:
    tmp = Path(tempfile.mkdtemp())
    path = _csv(tmp)
    assert len(M.load_hot_leads(path)) == 1  # пула нет — читаем CSV

    M.pool_add(path, [{**HOT, "name": "Из пула", "phone": "+79000000021"}])
    rows = M.load_hot_leads(path)
    assert [r["name"] for r in rows] == ["Из пула"], rows
    assert all(r["tier"] == "hot" for r in rows)


def test_store_atomic_write_and_busy_file() -> None:
    tmp = Path(tempfile.mkdtemp())
    path = tmp / "leads.csv"
    lead = {
        "id": "1", "name": "Тест", "rubric": "барбершоп", "phone": "+7", "phones": ["+7"],
        "email": "", "emails": [], "website": "", "websites": [], "has_website": False,
        "socials": [], "messengers": [], "telegram": "", "max": "", "vk": "",
        "address": "ул", "city": "moscow", "rating": 4.6, "reviews": 12, "status": "",
        "hours": "", "lat": None, "lon": None, "description": "", "query": "q",
        "url": "u", "score": 80, "reason": "hot: нет сайта",
    }

    with Store(path) as store:
        assert store.add(lead) is True
    assert path.exists() and not (tmp / "leads.csv.tmp").exists()
    assert len(list(csv.DictReader(path.open(encoding="utf-8-sig")))) == 1
    jsonl_before = path.with_suffix(".jsonl").read_text(encoding="utf-8")

    real = os.replace

    def busy(source, target):
        if str(target).endswith("leads.csv"):
            raise PermissionError(13, "Permission denied")
        return real(source, target)

    os.replace = busy
    try:
        try:
            with Store(path) as store:
                store.add({**lead, "id": "2"})
        except LeadsFileBusy as exc:
            assert "занят" in str(exc)
        else:
            raise AssertionError("ожидали LeadsFileBusy")
    finally:
        os.replace = real

    assert (tmp / "leads_new.csv").exists(), "свежие данные должны сохраниться рядом"
    assert len(list(csv.DictReader(path.open(encoding="utf-8-sig")))) == 1, "старый CSV не тронут"
    assert path.with_suffix(".jsonl").read_text(encoding="utf-8") == jsonl_before, \
        "JSONL не должен расходиться с CSV"


def test_messenger_links_from_messengers() -> None:
    from leads2gis import messenger_links

    lead = {"telegram": "", "max": "", "vk": "", "email": "a@b.ru",
            "messengers": "telegram:barber_a; vk:club1; max:shop2"}
    links = messenger_links(lead)
    assert links["telegram"] == "https://t.me/barber_a", links
    assert links["vk"] == "https://vk.com/club1", links
    assert links["max"] == "https://max.ru/shop2", links
    assert links["email"] == "mailto:a@b.ru"

    phone_only = {"telegram": "+7 900 111-22-33", "messengers": [], "email": ""}
    assert messenger_links(phone_only) == {}, "по номеру ссылку не строим"


def test_contact_link_and_channels_fallback() -> None:
    row = {"telegram": "", "max": "", "vk": "", "email": "",
           "messengers": "telegram:barber_a; max:shop2; vk:club1"}
    channels = M.channels_of(row)
    assert set(channels) == {"telegram", "max", "vk"}, channels
    assert M.contact_link("telegram", channels["telegram"]) == "https://t.me/barber_a"
    assert M.contact_link("max", channels["max"]) == "https://max.ru/shop2"
    assert M.contact_link("vk", channels["vk"]) == "https://vk.com/club1"
    assert M.contact_link("telegram", ("+79001112233", "phone")) == ""


def test_channels_of_accepts_links() -> None:
    """В пуле контакты лежат ссылками — отправка должна получить ник."""
    row = {"telegram": "https://t.me/barber_a", "max": "https://max.ru/shop2",
           "vk": "https://vk.com/club1", "email": "mailto:a@b.ru"}
    channels = M.channels_of(row)
    assert channels["telegram"] == ("barber_a", "username"), channels
    assert channels["vk"] == ("club1", "screen"), channels
    assert channels["email"] == ("a@b.ru", "email"), channels
    assert M.contact_link("telegram", channels["telegram"]) == "https://t.me/barber_a"


def test_pool_refresh_fills_empty_contacts() -> None:
    tmp = Path(tempfile.mkdtemp())
    path = tmp / "leads.csv"
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["id", "name", "tier", "score", "phone", "telegram", "vk",
                            "messengers", "email"])
        writer.writeheader()
        writer.writerow({"id": "1", "name": "Барбершоп", "tier": "hot", "score": "80",
                         "phone": "+79001112233", "telegram": "", "vk": "",
                         "email": "a@b.ru", "messengers": "telegram:barber_a; vk:club1"})
    M.pool_add(path, [{**HOT, "telegram": "", "vk": "", "max": "", "email": ""}])
    assert M.pool_load(path)[0]["telegram"] == "", "в пуле пусто до обновления"

    assert M.pool_refresh(path) >= 2
    row = M.pool_load(path)[0]
    assert row["telegram"] == "https://t.me/barber_a", row
    assert row["vk"] == "https://vk.com/club1", row


if __name__ == "__main__":
    for name, func in list(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
            print(f"ok  {name}")
    print("все тесты прошли")
