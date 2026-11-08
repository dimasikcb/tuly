# ЗАДАЧА 6 — тесты

**Исполнитель:** агент по `tests/test_leads_pool.py`.
**Файл:** `tests/test_leads_pool.py` (единственный, который он правит).
**Статус:** ✅ 44 теста, все проходят (проверено 2026-11-06).

Важно: это **свой раннер**, а не `unittest`. В файле 44 функции `def test_*`
и ни одного `unittest.TestCase`, поэтому `python -m unittest discover -s tests`
собирает из этого модуля **0 тестов**. Цифра «40/40» в старом INDEX была
неверной; общий прогон (159 OK) этих тестов не покрывает — их надо запускать
отдельно командой ниже.

---

## Как устроен раннер

Никакого pytest — свой цикл в конце файла:

```python
if __name__ == "__main__":
    for name, func in list(globals().items()):
        if name.startswith("test_") and callable(func):
            func()
            print(f"ok  {name}")
    print("все тесты прошли")
```

Запуск (обязательно с UTF-8, иначе `UnicodeEncodeError` на русских сообщениях):

```bat
cd C:\Users\admin\Documents\тулы
python -X utf8 tests/test_leads_pool.py
```

Успех — последняя строка `все тесты прошли`. Упавший тест печатает traceback с
номером строки и текстом `AssertionError`.

---

## Железные правила

1. **Никакой сети.** Только `tempfile.mkdtemp()`, только локальные CSV.
2. **Никогда** не писать в реальный `hot_pool.csv` проекта.
3. Каждый тест начинает с чистого пула:
   ```python
   pool_file = tmp / "hot_pool.csv"
   if pool_file.exists():
       pool_file.unlink()
   ```
   Без этого тесты протекают друг в друга: `pool_add` дедуплицирует по
   `name|phone`, и второй тест увидит записи первого.
4. Пути: `pool_path(csv)` — от файла CSV, `default_pool_path(dir)` — от папки.
   Не путать: передача файла туда, где ждут папку, даёт
   `FileExistsError: WinError 183`.
5. Сообщения в `assert` — по-русски и с фактическим значением:
   `assert row["topic"] == "барбершоп", row`.

---

## Что покрыто (44 теста, группы)

| Группа | Тесты |
|--------|-------|
| Пул: добавление, дедуп | `test_pool_add_only_hot_and_dedup`, `test_pool_add_accepts_parser_lead`, `test_pool_add_no_duplicate_same_key` |
| Формат данных | `test_pool_stores_links_not_nicks`, `test_pool_key_generation`, `test_email_link_formatting` |
| Тема и карточка 2ГИС | `test_pool_add_topic_and_card_from_query_url`, `test_pool_add_topic_from_topic_field`, `test_pool_refresh_fills_topic_and_card`, `test_migrate_legacy_transfers_topic_and_card` |
| Каналы и ссылки | `test_messenger_links_from_messengers`, `test_contact_link_and_channels_fallback`, `test_channels_of_accepts_links`, `test_channels_with_empty_values`, `test_channel_priority_order`, `test_contact_picking_logic`, `test_messenger_only_lead`, `test_lead_with_multiple_contacts` |
| Срок жизни и статусы | `test_pool_purge_keeps_sent_and_fresh`, `test_pool_marks_block_purge`, `test_pool_purge_empty`, `test_pool_mark_sent_and_channel`, `test_mark_status_with_no_pool`, `test_pool_stats_counts_expiring`, `test_stats_edge_cases` |
| Пустой пул / края | `test_pool_save_load_empty`, `test_empty_pool_operations` |
| Атомарная запись | `test_store_atomic_write_and_busy_file` |
| Города | `test_city_list_empty`, `test_city_list_single`, `test_city_list_multiple`, `test_city_list_russia`, `test_city_list_russia_variants`, `test_ru_cities_constant_exists` |

---

## Шаблон нового теста

```python
def test_что_проверяем() -> None:
    """Одно предложение: что должна делать функция."""
    tmp = Path(tempfile.mkdtemp())
    pool_file = tmp / "hot_pool.csv"
    if pool_file.exists():
        pool_file.unlink()

    lead = {"name": "Тест", "phone": "+79001112233", "tier": "hot",
            "query": "барбершоп", "url": "https://2gis.ru/moscow/firm/1"}
    M.pool_add(pool_file, [lead])

    row = M.pool_load(pool_file)[0]
    assert row["topic"] == "барбершоп", row
    assert row["card"].endswith("/firm/1"), row
```

Тесты городов живут в `tests/` и импортируют `tools.pyw` через
`importlib` (Tkinter-модуль нельзя импортировать как обычный). Если
добавляешь такой тест — не открывай окно, только вызывай `_city_list`.

---

## Проверка перед сдачей

```bat
python -m py_compile leads_mail.py leads2gis.py tools.pyw
python -X utf8 tests/test_leads_pool.py
```

Ожидание: компиляция молча, тесты — `все тесты прошли`.
Если падает один тест — **не удалять его и не ослаблять assert**. Сначала
понять, что именно сломалось: тест фиксирует контракт, а не мешает.
