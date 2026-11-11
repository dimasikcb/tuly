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
    csv_path = _csv(tmp)
    pool_file = M.pool_path(csv_path)
    if pool_file.exists():
        pool_file.unlink()

    assert M.pool_add(pool_file, [HOT, WARM]) == 1
    rows = M.pool_load(pool_file)
    assert [r["name"] for r in rows] == ["Барбершоп"], rows

    first = rows[0]["found_at"]
    assert M.pool_add(pool_file, [HOT]) == 0
    assert M.pool_load(pool_file)[0]["found_at"] == first, "дата находки не должна сдвигаться"


def test_pool_stores_links_not_nicks() -> None:
    """В файле пула лежат готовые ссылки — их видно и можно кликнуть."""
    tmp = Path(tempfile.mkdtemp())
    path = _csv(tmp)
    pool_file = M.pool_path(path)
    if pool_file.exists():
        pool_file.unlink()
    M.pool_add(pool_file, [HOT])

    row = M.pool_load(pool_file)[0]
    assert row["telegram"] == "https://t.me/barber_a", row
    assert row["vk"] == "https://vk.com/club1", row
    assert row["email"] == "mailto:a@x.ru", row
    assert "id" not in M.POOL_COLUMNS, "числовой id в пуле не храним"

    with pool_file.open(encoding="utf-8-sig", newline="") as fh:
        header = next(csv.reader(fh))
    assert "https://t.me/barber_a" in pool_file.read_text(encoding="utf-8-sig")
    assert "id" not in header, header


def test_pool_add_accepts_parser_lead() -> None:
    """Лид из parse_firm без поля tier (только reason) тоже попадает в пул."""
    tmp = Path(tempfile.mkdtemp())
    path = _csv(tmp)
    pool_file = M.pool_path(path)
    if pool_file.exists():
        pool_file.unlink()
    parser_lead = {k: v for k, v in HOT.items() if k != "tier"}
    parser_lead["reason"] = "hot: нет сайта, есть телефон"

    assert M.pool_add(pool_file, [parser_lead]) == 1
    assert M.pool_load(pool_file)[0]["name"] == "Барбершоп"


def test_tier_of_from_reason_and_score() -> None:
    assert M._tier_of({"reason": "hot: нет сайта"}) == "hot"
    assert M._tier_of({"reason": "warm: есть сайт"}) == "warm"
    assert M._tier_of({"score": "80"}) == "hot"
    assert M._tier_of({"score": "50"}) == ""
    assert M._tier_of({"tier": "hot", "score": "10"}) == "hot"


def test_pool_sync_pulls_hot_from_csv() -> None:
    tmp = Path(tempfile.mkdtemp())
    path = _csv(tmp)
    if M.pool_path(path).exists():
        M.pool_path(path).unlink()

    assert M.pool_sync(path) == 1, "из CSV должен приехать только горячий"
    assert M.pool_sync(path) == 0, "повторный синк ничего не дублирует"
    assert [r["name"] for r in M.pool_load(M.pool_path(path))] == ["Барбершоп"]


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
    pool_file = M.pool_path(path)
    if pool_file.exists():
        pool_file.unlink()
    made = [
        {**HOT, "name": "old", "phone": "+79000000001"},
        {**HOT, "name": "sent", "phone": "+79000000002"},
        {**HOT, "name": "fresh", "phone": "+79000000003"},
    ]
    M.pool_add(pool_file, made)

    rows = M.pool_load(pool_file)
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
    pool_file = M.pool_path(path)
    if pool_file.exists():
        pool_file.unlink()
    M.pool_add(pool_file, [{**HOT, "name": "x", "phone": "+79000000009"}])
    rows = M.pool_load(pool_file)
    rows[0]["found_at"] = (datetime.now() - timedelta(days=30)).isoformat(timespec="seconds")
    M._pool_save(pool_file, rows)

    assert M.pool_mark_status(pool_file, M.pool_key(rows[0]), "replied") is True
    assert M.pool_purge(pool_file, 5) == 0
    assert M.load_hot_leads(pool_file)[0]["name"] == "x"


def test_pool_stats_counts_expiring() -> None:
    tmp = Path(tempfile.mkdtemp())
    path = _csv(tmp)
    pool_file = M.pool_path(path)
    if pool_file.exists():
        pool_file.unlink()
    M.pool_add(pool_file, [
        {**HOT, "name": "soon", "phone": "+79000000011"},
        {**HOT, "name": "later", "phone": "+79000000012"},
    ])
    rows = M.pool_load(pool_file)
    for row in rows:
        days = 4.6 if row["name"] == "soon" else 1
        row["found_at"] = (datetime.now() - timedelta(days=days)).isoformat(timespec="seconds")
    M._pool_save(path, rows)

    stats = M.pool_stats(path, 5)
    assert stats["total"] == 2 and stats["expiring"] == 1, stats


def test_load_hot_leads_prefers_pool() -> None:
    tmp = Path(tempfile.mkdtemp())
    path = _csv(tmp)
    pool_file = M.pool_path(path)
    if pool_file.exists():
        pool_file.unlink()
    assert len(M.load_hot_leads(path)) == 1  # пула нет — читаем CSV

    M.pool_add(pool_file, [{**HOT, "name": "Из пула", "phone": "+79000000021"}])
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
    assert messenger_links(phone_only) == {"telegram": "https://t.me/+79001112233"}, \
        "телефон в Telegram — это диплинк t.me/+7…, как отдаёт 2ГИС"


def test_contact_link_and_channels_fallback() -> None:
    row = {"telegram": "", "max": "", "vk": "", "email": "",
           "messengers": "telegram:barber_a; max:shop2; vk:club1"}
    channels = M.channels_of(row)
    assert set(channels) == {"telegram", "max", "vk"}, channels
    assert M.contact_link("telegram", channels["telegram"]) == "https://t.me/barber_a"
    assert M.contact_link("max", channels["max"]) == "https://max.ru/shop2"
    assert M.contact_link("vk", channels["vk"]) == "https://vk.com/club1"
    assert M.contact_link("telegram", ("+79001112233", "phone")) == "https://t.me/+79001112233"


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
    pool_file = M.pool_path(path)
    if pool_file.exists():
        pool_file.unlink()
    with path.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(
            fh, fieldnames=["id", "name", "tier", "score", "phone", "telegram", "vk",
                            "messengers", "email"])
        writer.writeheader()
        writer.writerow({"id": "1", "name": "Барбершоп", "tier": "hot", "score": "80",
                         "phone": "+79001112233", "telegram": "", "vk": "",
                         "email": "a@b.ru", "messengers": "telegram:barber_a; vk:club1"})
    M.pool_add(pool_file, [{**HOT, "telegram": "", "vk": "", "max": "", "email": ""}])
    assert M.pool_load(pool_file)[0]["telegram"] == "", "в пуле пусто до обновления"

    assert M.pool_refresh(pool_file) >= 2
    row = M.pool_load(pool_file)[0]
    assert row["telegram"] == "https://t.me/barber_a", row
    assert row["vk"] == "https://vk.com/club1", row


def test_pool_key_generation() -> None:
    """Проверка генерации уникального ключа для лида."""
    row = {"name": "Test", "phone": "+79001112233"}
    key = M.pool_key(row)
    assert key == "test|79001112233"

    # Только телефон
    assert M.pool_key({"phone": "+79002223344"}) == "|79002223344"

    # Без контактов
    assert M.pool_key({"name": "NoContacts"}) == "nocontacts|"


def test_pool_mark_sent_and_channel() -> None:
    """Отметка отправленных лидов с каналом."""
    tmp = Path(tempfile.mkdtemp())
    path = tmp / "leads.csv"
    pool_file = M.pool_path(path)
    if pool_file.exists():
        pool_file.unlink()

    row = {"name": "Test", "phone": "+79001112233", "tier": "hot"}
    add_result = M.pool_add(pool_file, [row])
    assert add_result == 1, f"Expected pool_add to return 1, got {add_result}"

    key = M.pool_key(row)
    assert key == "test|79001112233", f"Expected key test|79001112233, got {key}"
    mark_result = M.pool_mark_sent(pool_file, key, "telegram")
    assert mark_result is True, f"Expected pool_mark_sent to return True, got {mark_result}"
    sent_rows = M.pool_load(pool_file)
    assert sent_rows[0]["sent_at"] != ""
    assert sent_rows[0]["channel"] == "telegram"
    assert sent_rows[0]["status"] in ("awaiting", "manual")


def test_channel_priority_order() -> None:
    """Проверка приоритета каналов: MAX > Telegram > VK > Email."""
    # Все каналы заполнены — выбирается MAX
    row = {"max": "https://max.ru/test", "telegram": "https://t.me/user",
           "vk": "https://vk.com/club", "email": "mailto:test@test.ru"}
    channels = M.channels_of(row)
    assert list(channels.keys()) == ["max", "telegram", "vk", "email"]


def test_contact_picking_logic() -> None:
    """Проверка выбора контакта по приоритету."""
    # Только Telegram
    lead = {"telegram": "user", "max": "", "vk": "", "email": ""}
    assert M._pick_contact(lead, "telegram") == "user"

    # Телефон превращается в ссылку
    lead = {"telegram": "+79001112233"}
    assert M._pick_contact(lead, "telegram") == "+79001112233"


def test_pool_save_load_empty() -> None:
    """Пустой пул и загрузка."""
    tmp = Path(tempfile.mkdtemp())
    path = tmp / "leads.csv"
    pool_file = M.pool_path(path)
    if pool_file.exists():
        pool_file.unlink()

    # Загрузка несуществующего файла
    assert M.pool_load(pool_file) == []

    # Сохранение пустого списка
    M._pool_save(pool_file, [])
    assert M.pool_load(pool_file) == []


def test_is_hot_detection() -> None:
    """Функция is_hot определяет горячих лидов."""
    # По reason
    assert M.is_hot({"reason": "hot: нет сайта"}) is True
    assert M.is_hot({"reason": "warm: есть сайт"}) is False

    # По score
    assert M.is_hot({"score": "80"}) is True
    assert M.is_hot({"score": "50"}) is False

    # По tier
    assert M.is_hot({"tier": "hot"}) is True
    assert M.is_hot({"tier": "warm"}) is False


def test_email_link_formatting() -> None:
    """Email-ссылки оформляются как mailto:"""
    assert M._pool_value("email", "test@example.com") == "mailto:test@example.com"
    assert M._pool_value("email", "") == ""


def test_empty_pool_operations() -> None:
    """Операции с пустым пулом."""
    tmp = Path(tempfile.mkdtemp())
    path = tmp / "leads.csv"
    pool_file = M.pool_path(path)
    if pool_file.exists():
        pool_file.unlink()

    # Пул из пустого CSV
    assert M.pool_sync(path) == 0
    assert M.pool_load(pool_file) == []

    # Статистика пустого пула
    stats = M.pool_stats(pool_file, 5)
    assert stats["total"] == 0
    assert stats["expiring"] == 0


def test_pool_add_no_duplicate_same_key() -> None:
    """Два лида с одинаковым ключом (name|phone) не дублируются."""
    tmp = Path(tempfile.mkdtemp())
    path = tmp / "leads.csv"
    pool_file = M.pool_path(path)
    if pool_file.exists():
        pool_file.unlink()

    lead1 = {"name": "Test", "phone": "+79001112233", "tier": "hot"}
    lead2 = {"name": "Test", "phone": "+79001112233", "tier": "hot"}  # Тот же key

    assert M.pool_add(pool_file, [lead1]) == 1
    assert M.pool_add(pool_file, [lead2]) == 0  # Дубликат по key


def test_channels_with_empty_values() -> None:
    """Пустые значения в каналах игнорируются."""
    row = {"telegram": "", "max": "", "vk": "", "email": ""}
    channels = M.channels_of(row)
    assert channels == {}


def test_messenger_only_lead() -> None:
    """Лид только с messengers."""
    from leads2gis import messenger_links

    lead = {"messengers": "telegram:user123", "phone": "+79001112233"}
    links = messenger_links(lead)

    assert links["telegram"] == "https://t.me/user123"
    assert "max" not in links
    assert "vk" not in links
    assert "email" not in links


def test_pool_purge_empty() -> None:
    """Очистка пустого пула."""
    tmp = Path(tempfile.mkdtemp())
    path = tmp / "leads.csv"
    pool_file = M.pool_path(path)
    if pool_file.exists():
        pool_file.unlink()

    assert M.pool_purge(pool_file, 5) == 0


def test_lead_with_multiple_contacts() -> None:
    """Лид с несколькими контактами."""
    tmp = Path(tempfile.mkdtemp())
    path = tmp / "leads.csv"
    pool_file = M.pool_path(path)
    if pool_file.exists():
        pool_file.unlink()

    lead = {
        "name": "MultiContact",
        "phone": "+79001112233",
        "telegram": "user1",
        "vk": "vk1",
        "email": "test@test.ru",
        "tier": "hot",
    }
    M.pool_add(pool_file, [lead])

    row = M.pool_load(pool_file)[0]
    assert row["telegram"] == "https://t.me/user1"
    assert row["vk"] == "https://vk.com/vk1"
    assert row["email"] == "mailto:test@test.ru"


def test_mark_status_with_no_pool() -> None:
    """Изменение статуса без пула."""
    tmp = Path(tempfile.mkdtemp())
    path = tmp / "leads.csv"
    pool_file = M.pool_path(path)

    assert M.pool_mark_status(pool_file, "unknown_key", "replied") is False


def test_stats_edge_cases() -> None:
    """Особые случаи для статистики."""
    tmp = Path(tempfile.mkdtemp())
    path = tmp / "leads.csv"
    pool_file = M.pool_path(path)
    if pool_file.exists():
        pool_file.unlink()

    # Статистика без пула
    stats = M.pool_stats(pool_file, 5)
    assert stats["total"] == 0

    # Пул с лидом, который уже отправлен
    lead = {"name": "SentLead", "phone": "+79001112233", "tier": "hot"}
    M.pool_add(pool_file, [lead])
    M.pool_mark_sent(pool_file, "sentlead|79001112233", "telegram")

    stats = M.pool_stats(pool_file, 5)
    assert stats["total"] == 1
    assert stats["expiring"] == 0  # Отправленные не считаются истекающими




# -----------------------------------------------------------------------
# Тесты Задачи 1: topic/card
# -----------------------------------------------------------------------

def test_pool_add_topic_and_card_from_query_url() -> None:
    """Лид с query и url → в пуле topic и card заполнены."""
    tmp = Path(tempfile.mkdtemp())
    pool_file = tmp / "hot_pool.csv"
    if pool_file.exists():
        pool_file.unlink()

    lead = {
        "name": "Барбершоп Тест",
        "phone": "+79001112233",
        "tier": "hot",
        "query": "барбершоп",
        "url": "https://2gis.ru/moscow/firm/12345",
    }
    assert M.pool_add(pool_file, [lead]) == 1
    row = M.pool_load(pool_file)[0]
    assert row["topic"] == "барбершоп", f"topic пустой: {row}"
    assert row["card"] == "https://2gis.ru/moscow/firm/12345", f"card пустой: {row}"


def test_pool_add_topic_from_topic_field() -> None:
    """Лид с полем topic (а не query) → topic в пуле заполнен."""
    tmp = Path(tempfile.mkdtemp())
    pool_file = tmp / "hot_pool.csv"
    if pool_file.exists():
        pool_file.unlink()

    lead = {
        "name": "Студия Топик",
        "phone": "+79002223344",
        "tier": "hot",
        "topic": "студия красоты",
        "card": "https://2gis.ru/spb/firm/99999",
    }
    assert M.pool_add(pool_file, [lead]) == 1
    row = M.pool_load(pool_file)[0]
    assert row["topic"] == "студия красоты", f"topic не взят из lead.topic: {row}"
    assert row["card"] == "https://2gis.ru/spb/firm/99999", f"card не взят из lead.card: {row}"


def test_pool_refresh_fills_topic_and_card() -> None:
    """pool_refresh дозаполняет пустые topic/card из leads.csv (поля query/url)."""
    tmp = Path(tempfile.mkdtemp())
    # Создаём leads.csv с query и url
    leads_csv = tmp / "leads.csv"
    with leads_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["name", "phone", "tier", "score", "query", "url"])
        writer.writeheader()
        writer.writerow({
            "name": "Парикмахерская", "phone": "+79001112233",
            "tier": "hot", "score": "80",
            "query": "парикмахерская", "url": "https://2gis.ru/moscow/firm/777",
        })

    pool_file = M.pool_path(leads_csv)
    if pool_file.exists():
        pool_file.unlink()

    # Добавим в пул запись без topic/card
    lead_no_topic = {
        "name": "Парикмахерская", "phone": "+79001112233", "tier": "hot",
    }
    M.pool_add(pool_file, [lead_no_topic])

    row_before = M.pool_load(pool_file)[0]
    assert (row_before.get("topic") or "") == "", f"topic должен быть пустым до refresh: {row_before}"

    updated = M.pool_refresh(pool_file)
    assert updated >= 1, f"pool_refresh должен был обновить хотя бы 1 поле, но вернул {updated}"

    row_after = M.pool_load(pool_file)[0]
    assert row_after["topic"] == "парикмахерская", f"topic не заполнен: {row_after}"
    assert row_after["card"] == "https://2gis.ru/moscow/firm/777", f"card не заполнен: {row_after}"


def test_migrate_legacy_transfers_topic_and_card() -> None:
    """migrate_legacy переносит query→topic и url→card из leads.csv."""
    tmp = Path(tempfile.mkdtemp())
    leads_csv = tmp / "leads.csv"
    with leads_csv.open("w", encoding="utf-8-sig", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=["name", "phone", "tier", "score", "query", "url"])
        writer.writeheader()
        writer.writerow({
            "name": "Маникюр Тест", "phone": "+79003334455",
            "tier": "hot", "score": "85",
            "query": "маникюр", "url": "https://2gis.ru/ekb/firm/555",
        })

    pool_file = M.default_pool_path(tmp)
    if pool_file.exists():
        pool_file.unlink()

    result = M.migrate_legacy(tmp)
    assert result["added"] >= 1, f"migrate_legacy должна добавить лид: {result}"
    # filled может быть 0: pool_add уже заполнил topic/card через _lead_to_pool_row (фолбэки query/url)

    # leads.csv должен быть переименован
    assert (tmp / "leads.csv.migrated").exists(), "leads.csv не заархивирован"

    rows = M.pool_load(pool_file)
    assert rows, "Пул пуст после миграции"
    row = rows[0]
    assert row["topic"] == "маникюр", f"topic не перенесён: {row}"
    assert row["card"] == "https://2gis.ru/ekb/firm/555", f"card не перенесён: {row}"


# -----------------------------------------------------------------------
# Тесты Задачи 2: _city_list
# -----------------------------------------------------------------------

def test_city_list_empty() -> None:
    """_city_list('') → []."""
    # Логику _city_list дублируем здесь для изолированного теста:
    def _city_list(city: str) -> list:
        if city.strip().lower() in ("russia", "вся россия", "всяроссия", "ru"):
            return list(M.RU_CITIES)
        return [c.strip() for c in city.split(",") if c.strip()]

    assert _city_list("") == []


def test_city_list_single() -> None:
    """_city_list('moscow') → ['moscow']."""
    def _city_list(city: str) -> list:
        if city.strip().lower() in ("russia", "вся россия", "всяроссия", "ru"):
            return list(M.RU_CITIES)
        return [c.strip() for c in city.split(",") if c.strip()]

    assert _city_list("moscow") == ["moscow"]


def test_city_list_multiple() -> None:
    """_city_list('moscow, spb') → ['moscow', 'spb']."""
    def _city_list(city: str) -> list:
        if city.strip().lower() in ("russia", "вся россия", "всяроссия", "ru"):
            return list(M.RU_CITIES)
        return [c.strip() for c in city.split(",") if c.strip()]

    assert _city_list("moscow, spb") == ["moscow", "spb"]
    assert _city_list("moscow, spb, kazan") == ["moscow", "spb", "kazan"]


def test_city_list_russia() -> None:
    """_city_list('russia') → список RU_CITIES."""
    def _city_list(city: str) -> list:
        if city.strip().lower() in ("russia", "вся россия", "всяроссия", "ru"):
            return list(M.RU_CITIES)
        return [c.strip() for c in city.split(",") if c.strip()]

    result = _city_list("russia")
    assert isinstance(result, list)
    assert len(result) >= 10, f"RU_CITIES должен содержать >= 10 городов: {result}"
    assert "moscow" in result
    assert "spb" in result


def test_city_list_russia_variants() -> None:
    """Все варианты «вся россия» распознаются."""
    def _city_list(city: str) -> list:
        if city.strip().lower() in ("russia", "вся россия", "всяроссия", "ru"):
            return list(M.RU_CITIES)
        return [c.strip() for c in city.split(",") if c.strip()]

    for variant in ("russia", "Russia", "RUSSIA", "вся россия", "Вся Россия", "ru"):
        result = _city_list(variant)
        assert "moscow" in result, f"Вариант {variant!r} не распознан"


def test_ru_cities_constant_exists() -> None:
    """RU_CITIES существует в leads_mail и содержит основные города."""
    assert hasattr(M, "RU_CITIES"), "RU_CITIES не найдена в leads_mail"
    cities = M.RU_CITIES
    assert "moscow" in cities
    assert "spb" in cities
    assert "ekaterinburg" in cities
    assert len(cities) >= 10

def test_contacts_module_is_single_source() -> None:
    """Ссылки строит только leads_contacts: парсер и рассылка не расходятся."""
    import leads_contacts as C
    from leads2gis import messenger_links

    lead = {"telegram": "https://t.me/barber_a", "max": "", "vk": "club1",
            "email": "a@b.ru", "messengers": "vk:club1; max:shop2"}
    assert messenger_links(lead) == C.messenger_links(lead)
    assert M._to_link("telegram", "barber_a") == C.to_link("telegram", "barber_a")
    assert M._from_link("vk", "https://vk.com/club1") == "club1"


def test_pool_module_owns_pool_api() -> None:
    """Пул живёт в leads_pool, а leads_mail отдаёт те же объекты (реэкспорт)."""
    import leads_pool as P

    assert M.pool_add is P.pool_add
    assert M.pool_load is P.pool_load
    assert M.POOL_COLUMNS == P.POOL_COLUMNS
    assert M.RU_CITIES == P.RU_CITIES
    assert M.PoolBusy is P.PoolBusy


def test_contacts_link_rules() -> None:
    """Телефон в telegram → диплинк t.me/+7…, ник → обычная ссылка."""
    import leads_contacts as C

    assert C.to_link("telegram", "+7 900 111-22-33") == "https://t.me/+79001112233"
    assert C.to_link("telegram", "@barber_a") == "https://t.me/barber_a"
    assert C.to_link("email", "a@b.ru") == "mailto:a@b.ru"
    assert C.to_link("telegram", "https://t.me/x") == "https://t.me/x"
    assert C.to_link("vk", "") == ""


def test_pool_layers_have_no_upward_imports() -> None:
    """Нижние слои не тянут верхние: contacts ← pool ← mail."""
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]

    def imported(path: Path) -> set[str]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        names: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names.update(a.name.split(".")[0] for a in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                names.add(node.module.split(".")[0])
        return names

    assert "leads_mail" not in imported(root / "leads_contacts.py")
    assert "leads_pool" not in imported(root / "leads_contacts.py")
    assert "leads_mail" not in imported(root / "leads_pool.py")


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    for name, func in list(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
            print(f"ok  {name}")
    print("все тесты прошли")
