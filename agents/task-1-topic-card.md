# ЗАДАЧА 1 — «Тема» и «2ГИС» пустые в таблице

**Исполнитель:** агент, работающий с `leads_mail.py`.
**Файлы:** `leads_mail.py` (+ проверка данных `hot_pool.csv`).
**Статус на 2026-11-02:** ✅ выполнено — в пуле 35 записей, у всех заполнены
`topic` и `card`. Ниже — как проверить и как починить, если сломалось снова.

---

## Симптом

В таблице «Лиды 2ГИС» колонки **Тема** и **2ГИС** показывают «—» у всех строк,
хотя лиды собирались по запросам «кофейня», «барбершоп» и т.д.

## Причина

Таблица рисуется **только из `hot_pool.csv`**. Если в CSV `topic`/`card`
пустые — в интерфейсе будет «—». Пул мог быть наполнен тремя путями, и каждый
раньше терял эти поля:

1. `_lead_to_pool_row()` не имела фолбэков: у лида из парсера поле называется
   `query` (тема) и `url` (карточка), а не `topic`/`card`.
2. `migrate_legacy()` переносил только контакты (telegram/max/vk/email),
   а `query → topic` и `url → card` — нет.
3. `pool_add()` при встрече дубликата просто пропускал лид, поэтому старые
   записи никогда не дозаполнялись при повторном сборе.

---

## ШАГ 1.1 — фолбэки в `_lead_to_pool_row`

Найди `def _lead_to_pool_row`. Внутри должно быть ровно так:

```python
"topic": str(lead.get("topic") or lead.get("query") or "").strip(),
"card": str(lead.get("url") or lead.get("card") or "").strip(),
```

Если стоит просто `lead.get("topic")` / `lead.get("url")` — замени.

Проверка:
```bat
python -m py_compile leads_mail.py
```

---

## ШАГ 1.2 — `pool_refresh` дозаполняет тему и карточку

Найди `def pool_refresh`. После цикла по каналам
(`for channel in ("telegram", "max", "vk", "email"):`) должен быть блок:

```python
        # Дозаполнить тему и карточку 2ГИС
        if not (row.get("topic") or "").strip() and (src.get("query") or src.get("topic")):
            row["topic"] = str(src.get("query") or src.get("topic")).strip()
            updated += 1
        if not (row.get("card") or "").strip() and (src.get("url") or src.get("card")):
            row["card"] = str(src.get("url") or src.get("card")).strip()
            updated += 1
```

---

## ШАГ 1.3 — `migrate_legacy` переносит тему и карточку

В цикле `for row in current:` после блока с каналами:

```python
        # Перенести тему и ссылку на карточку 2ГИС
        if not (row.get("topic") or "").strip():
            val = str(src.get("query") or src.get("topic") or "").strip()
            if val:
                row["topic"] = val
                out["filled"] += 1
        if not (row.get("card") or "").strip():
            val = str(src.get("url") or src.get("card") or "").strip()
            if val:
                row["card"] = val
                out["filled"] += 1
```

---

## ШАГ 1.4 — `pool_add` дозаполняет существующие записи

В `pool_add` дубликат не должен просто пропускаться. Правильно:

```python
    by_key = {pool_key(row): row for row in rows}
    for lead in hot:
        key = pool_key(lead)
        if not key:
            continue
        if key in by_key:
            existing = by_key[key]
            new_card = str(lead.get("url") or lead.get("card") or "").strip()
            if not (existing.get("card") or "").strip() and new_card:
                existing["card"] = new_card
                added += 1
            new_topic = str(lead.get("topic") or lead.get("query") or "").strip()
            if not (existing.get("topic") or "").strip() and new_topic:
                existing["topic"] = new_topic
            for ch in ("telegram", "max", "vk", "email"):
                if not (existing.get(ch) or "").strip():
                    val = _pool_value(ch, _pick_contact(lead, ch))
                    if val:
                        existing[ch] = val
            continue
        ...
```

Смысл: повторный сбор по тому же городу/теме **лечит** старые записи, а не
игнорирует их.

---

## ШАГ 1.5 — починить реальные данные

Сначала посмотри, что в файле:

```bat
python -X utf8 -c "import csv;rows=list(csv.DictReader(open('hot_pool.csv',encoding='utf-8-sig')));print(len(rows), sum(1 for r in rows if (r.get('topic') or '').strip()), sum(1 for r in rows if (r.get('card') or '').strip()))"
```

Три числа: всего записей / с темой / со ссылкой 2ГИС.

Если тема/ссылки неполные:

```bat
:: 1) если старый leads.csv ещё на месте — миграция дозаполнит
python -X utf8 -c "import leads_mail as M; print(M.migrate_legacy('.'))"

:: 2) если leads.csv уже переименован в .migrated — дозаполнение из архива
python -X utf8 -c "import leads_mail as M; from pathlib import Path; Path('leads.csv').exists() or Path('leads.csv.migrated').replace('leads.csv'); print(M.pool_refresh('hot_pool.csv'))"
```

Если исходных данных нет вообще (всё пусто и архив отсутствует) — единственный
путь: **повторный сбор** тем же запросом и городом. Благодаря шагу 1.4 ссылки
и темы подтянутся к существующим записям без дублей.

---

## Критерий готовности

```bat
python -X utf8 -c "import csv;rows=list(csv.DictReader(open('hot_pool.csv',encoding='utf-8-sig')));print('topic',sum(1 for r in rows if (r.get('topic') or '').strip()),'card',sum(1 for r in rows if (r.get('card') or '').strip()),'of',len(rows))"
```
Все три числа совпадают. В GUI колонки «Тема» и «2ГИС» заполнены у каждой строки.

**Тесты по этой задаче** (должны быть в `tests/test_leads_pool.py`):
`test_pool_add_topic_and_card_from_query_url`, `test_pool_add_topic_from_topic_field`,
`test_pool_refresh_fills_topic_and_card`, `test_migrate_legacy_transfers_topic_and_card`.
