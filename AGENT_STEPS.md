# 💡 ОБЯЗАТЕЛЬНОЕ ПРАВИЛО: ВСЕГДА ОБЪЯСНЯЙ, КАК ПОНЯЛ ЗАДАЧУ
> **В самом начале КАЖДОГО ответа ИИ-агент ОБЯЗАН чётко и своими словами описать, как именно он понял задачу пользователя**, перед тем как переходить к плану, коду или выполнению.
> Формат первого абзаца ответа:
> **«Как я понял задачу:** ...**»** (или заголовок `### Как я понял задачу`).

---

# AGENT_STEPS.md — пошаговая инструкция для ИИ-агента

> **Читай этот файл целиком до начала работы.**
> Здесь всё: что за проект, какие файлы трогать, как проверять результат.
> Не угадывай. Не придумывай. Следуй шагам по порядку.
>
> **Раздача по агентам:** папка `agents/` — по одному самодостаточному
> заданию на задачу. Старт — `agents/00-INDEX.md` (карта задач, статусы,
> волны параллельного запуска).

---

## ЧАСТЬ 0. КОНТЕКСТ ПРОЕКТА

### Что это

Приложение на Python + Tkinter (`tools.pyw`). Одна вкладка — **«Лиды 2ГИС»**:
- парсит организации из 2ГИС (`leads2gis.py`)
- складывает «горячих» лидов (без сайта, с телефоном) в файл `hot_pool.csv`
- показывает их в таблице: **Название · Тема · Контакты · 2ГИС**
- умеет делать ИИ-рассылку через Telegram/MAX/VK/Email

### Какие файлы трогать (ТОЛЬКО ЭТИ)

| Файл | Слой | Зачем |
|------|------|-------|
| `leads_contacts.py` | 1 | ник ↔ ссылка, каналы, формат телефона — без сети и GUI |
| `leads_pool.py` | 2 | пул: CSV, дедупликация, срок жизни, миграция, статистика |
| `leads_mail.py` | 3 | рассылка (ИИ-тексты, TG/MAX/VK/Email); реэкспортирует пул и контакты |
| `leads2gis.py` | 3 | парсер 2ГИС — **без GUI** |
| `tools.pyw` | 4 | GUI + оркестрация |
| `tests/test_leads_pool.py` | — | офлайн-тесты |

> Слои: `contacts` ← `pool` ← `mail` ← `tools.pyw`. Нижний слой не импортирует
> верхний. `leads2gis` использует только `leads_contacts`.

**Логика остальных разделов живёт вне GUI** (правь модуль, не `tools.pyw`):

| Файл | Что внутри |
|------|------------|
| `winbase.py` | пути, `config.json`, `ps_run`, `run_tool`, `is_admin` |
| `apps_catalog.py` | каталог программ, поиск, `AppEntry` |
| `bypass_core.py` | Happ/GoodbyeDPI/ProxiFyre: `bypass_apply`, `bypass_status`, службы |
| `tests/test_arch.py` | проверяет, что GUI не дублирует эти функции и не тянет tkinter в логику |
> Старый код вида `leads_mail.pool_add(...)` работает благодаря реэкспорту,
> но новый код должен импортировать из модуля, где функция живёт.

> **НЕ ТРОГАТЬ:** `Tools.exe`, `.git`, `build/`, `buildout/`, разделы DNS/кликер/очистка/GitHub/Bypass в `tools.pyw`.

### Где лежат данные

```
hot_pool.csv     ← единственный источник данных для таблицы в GUI
leads.csv        ← старый файл (после миграции становится leads.csv.migrated)
```

### Формат hot_pool.csv

Колонки (строго в таком порядке):
```
name, telegram, max, vk, email, phone, city, topic, card, found_at, sent_at, channel, status
```

Важно:
- `telegram/vk/max` → **полные ссылки** (`https://t.me/...`), НЕ голые ники
- `email` → `mailto:адрес`
- `topic` → поисковый запрос (например: «барбершоп»)
- `card` → ссылка на карточку 2ГИС (`https://2gis.ru/moscow/firm/12345`)

---

## ЧАСТЬ 1. ЗАДАЧА 1 — «Тема и 2ГИС не отображаются в таблице»

### Что сломано

В `hot_pool.csv` у всех записей пустые `topic` и `card`.
Причина: при добавлении лидов в пул не брались поля `query` (→ `topic`) и `url` (→ `card`).

### Где исправить и как именно

---

#### ШАГ 1.1 — Открой `leads_mail.py`, найди функцию `_lead_to_pool_row`

**Как найти:** ищи строку `def _lead_to_pool_row`.

**Что должно быть внутри (правильный вариант):**
```python
def _lead_to_pool_row(lead: dict, found_at: str) -> dict:
    return {
        "name": lead.get("name", ""),
        "telegram": _pool_value("telegram", _pick_contact(lead, "telegram")),
        "max": _pool_value("max", _pick_contact(lead, "max")),
        "vk": _pool_value("vk", _pick_contact(lead, "vk")),
        "email": _pool_value("email", _pick_contact(lead, "email")),
        "phone": lead.get("phone", ""),
        "city": lead.get("city", ""),
        "topic": str(lead.get("topic") or lead.get("query") or "").strip(),
        "card":  str(lead.get("url")   or lead.get("card")  or "").strip(),
        "found_at": found_at,
        "sent_at": "",
        "channel": "",
        "status": "",
    }
```

**Что проверить:**
- строка `"topic"` берёт значение из `lead.get("topic") or lead.get("query")`
- строка `"card"` берёт значение из `lead.get("url") or lead.get("card")`

**Если этих фолбэков нет — добавь их.**

**Проверка после правки:**
```bat
cd C:\Users\admin\Documents\тулы
python -m py_compile leads_mail.py
```
Нет вывода = синтаксис OK.

---

#### ШАГ 1.2 — Найди функцию `pool_refresh` в `leads_mail.py`

**Как найти:** ищи строку `def pool_refresh`.

**Что должно быть внутри (правильный вариант):**
```python
def pool_refresh(pool_file) -> int:
    """Дозаполняет контакты в пуле из CSV. Возвращает число обновлённых."""
    csv_file = Path(pool_file).parent / "leads.csv"
    pool_rows = pool_load(pool_file)
    csv_rows = {pool_key(r): r for r in _read_csv(csv_file)}
    updated = 0
    for row in pool_rows:
        key = pool_key(row)
        src = csv_rows.get(key)
        if not src:
            continue
        for channel in ("telegram", "max", "vk", "email"):
            if not (row.get(channel) or "").strip():
                value = _pool_value(channel, _pick_contact(src, channel))
                if value:
                    row[channel] = value
                    updated += 1
        # Дозаполнить тему и карточку 2ГИС  ← ЭТИ СТРОКИ ДОЛЖНЫ БЫТЬ
        if not (row.get("topic") or "").strip() and (src.get("query") or src.get("topic")):
            row["topic"] = str(src.get("query") or src.get("topic")).strip()
            updated += 1
        if not (row.get("card") or "").strip() and (src.get("url") or src.get("card")):
            row["card"] = str(src.get("url") or src.get("card")).strip()
            updated += 1
    if updated:
        _pool_save(pool_file, pool_rows)
    return updated
```

**Если блока `# Дозаполнить тему и карточку 2ГИС` нет — добавь его точно как показано выше.**

**Проверка после правки:**
```bat
python -m py_compile leads_mail.py
python -X utf8 tests/test_leads_pool.py
```
Строка `ok  test_pool_refresh_fills_topic_and_card` должна быть в выводе.

---

#### ШАГ 1.3 — Найди функцию `migrate_legacy` в `leads_mail.py`

**Как найти:** ищи строку `def migrate_legacy`.

**Что должно быть внутри цикла `for row in current:`:**
```python
    for row in current:
        src = source.get(pool_key(row))
        if not src:
            continue
        for channel in ("telegram", "max", "vk", "email"):
            if not (row.get(channel) or "").strip():
                value = _pool_value(channel, _pick_contact(src, channel))
                if value:
                    row[channel] = value
                    out["filled"] += 1
        # Перенести тему и ссылку на карточку 2ГИС  ← ЭТИ СТРОКИ ДОЛЖНЫ БЫТЬ
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

**Если блока нет — добавь его после блока `for channel in ...`.**

**Проверка:**
```bat
python -m py_compile leads_mail.py
python -X utf8 tests/test_leads_pool.py
```
Строка `ok  test_migrate_legacy_transfers_topic_and_card` должна быть в выводе.

---

#### ШАГ 1.4 — Дозаполни реальный hot_pool.csv

Выполни в папке проекта:

```bat
cd C:\Users\admin\Documents\тулы
python -X utf8 -c "import leads_mail as M; r = M.migrate_legacy('.'); print(r)"
```

**Ожидаемый вывод:** `{'added': N, 'filled': M, 'archived': 'leads.csv.migrated'}`

Если `leads.csv` уже переименован в `.migrated`, запусти:
```bat
python -X utf8 -c "import leads_mail as M; print(M.pool_refresh('hot_pool.csv'))"
```

**Критерий выполнения задачи 1:**
Открой программу → вкладка «Лиды 2ГИС» → в таблице у каждой строки видна тема (напр. «барбершоп») и ссылка «2ГИС».

---

## ЧАСТЬ 2. ЗАДАЧА 2 — «Умный поиск: несколько городов или вся Россия»

### Что нужно сделать

1. Добавить в `leads_mail.py` константу `RU_CITIES` — список слагов крупных городов.
2. Добавить в `tools.pyw` функцию `_city_list` — разбирает строку городов.
3. Изменить `leads_collect` в `tools.pyw` — обходить несколько городов.
4. Заменить поле «Город» с `Entry` на `Combobox` с выпадающим списком.
5. В методе `start()` нормализовать «russia — вся Россия» → «russia».

---

#### ШАГ 2.1 — Добавь `RU_CITIES` в `leads_mail.py`

**Как найти место:** найди строку `POOL_KEEP_STATUS = ("replied", "seen", "awaiting", "manual")`

**Добавь сразу ПОСЛЕ неё:**
```python
# 2ГИС-слаги крупных городов для режима «вся Россия».
RU_CITIES = (
    "moscow", "spb", "ekaterinburg", "novosibirsk", "kazan", "nizhnynovgorod",
    "samara", "omsk", "chelyabinsk", "rostovnadonu",
    "ufa", "krasnoyarsk", "voronezh", "perm", "volgograd",
)
```

**Если `RU_CITIES` уже есть — пропусти этот шаг.**

**Проверка:**
```bat
python -m py_compile leads_mail.py
python -X utf8 tests/test_leads_pool.py
```
Строка `ok  test_ru_cities_constant_exists` должна быть в выводе.

---

#### ШАГ 2.2 — Добавь `_city_list` в `tools.pyw`

**Как найти место:** ищи строку `def leads_collect` в `tools.pyw`.

**Добавь функцию ПЕРЕД `def leads_collect` (не после!):**
```python
def _city_list(city: str) -> list[str]:
    """Разбирает строку городов; «russia/вся россия» → список RU_CITIES."""
    if city.strip().lower() in ("russia", "вся россия", "всяроссия", "ru"):
        try:
            return list(leads_mail.RU_CITIES)
        except AttributeError:
            return ["moscow"]
    return [c.strip() for c in city.split(",") if c.strip()]
```

**Проверка:** `python -m py_compile tools.pyw` — нет вывода.

---

#### ШАГ 2.3 — Измени `leads_collect` для поддержки нескольких городов

**Как найти место:** ищи `def leads_collect` в `tools.pyw`.

**В начале тела функции** (после `want = max(1, int(cards))`), добавь:
```python
    cities = _city_list(city)
    if not cities:
        cities = [city.strip() or "moscow"]
    want_share = max(1, want // len(cities))
```

**Замени** одиночный цикл сбора карточек (от `cards: list[dict] = []` до `cards = cards[:want]`) на мультигородной:
```python
    all_cards: list[dict] = []
    seen: set[str] = set()
    for ci, cur_city in enumerate(cities, 1):
        if stop.is_set():
            break
        pages = leads_engine.pages_for(want_share)
        city_cards: list[dict] = []
        for page in range(1, pages + 1):
            if stop.is_set() or len(city_cards) >= want_share:
                break
            found, _last_page = client.search_page(cur_city, query, page)
            fresh = [c for c in found if c["id"] not in seen]
            for c in fresh:
                seen.add(c["id"])
            city_cards.extend(fresh)
            on_progress(
                f"город {ci}/{len(cities)} {cur_city} "
                f"стр.{page}/{pages} · {len(city_cards)}/{want_share}"
            )
            if not fresh:
                break
        city_cards = city_cards[:want_share]
        all_cards.extend(city_cards)
        on_log(f"{cur_city} / {query}: карточек {len(city_cards)}")
```

**Ниже**, замени `if not cards:` на `if not all_cards:`:
```python
    if not all_cards:
        return {"checked": 0, "found": 0, "hot": 0, "pooled": 0,
                "total": len(leads_mail.pool_load(POOL_FILE)), "rows": []}
```

**Во вложенной функции `one`** — параметр теперь кортеж `(cur_city, firm_id)`:
```python
    def one(card_info: tuple) -> dict | None:
        cur_city, firm_id = card_info
        try:
            html = _leads_client().firm_html(cur_city, firm_id)
        except Exception:  # noqa: BLE001
            return None
        return leads_engine.parse_firm(html, cur_city, firm_id, query)
```

**Замени** создание futures:
```python
    card_infos = [(c.get("city") or cities[0], c["id"]) for c in all_cards]
    parsed: list[dict] = []
    total = len(card_infos)
    with ThreadPoolExecutor(max_workers=6) as pool:
        futures = [pool.submit(one, ci) for ci in card_infos]
        for index, future in enumerate(as_completed(futures), 1):
            if stop.is_set():
                break
            lead = future.result()
            if lead:
                parsed.append(lead)
            on_progress(f"карточки {index}/{total} · собрано {len(parsed)}")
```

**Проверка после правки:**
```bat
python -m py_compile tools.pyw
```
Нет вывода = синтаксис OK.

---

#### ШАГ 2.4 — Замени Entry города на Combobox в `LeadsPanel._build`

**Как найти место:** в `tools.pyw` найди внутри `_build`:
```python
        caption(0, 0, "Город")
        ttk.Entry(body, textvariable=self.city_var, width=20).grid(
            row=0, column=1, sticky="ew", pady=px(5))
```

**Замени на:**
```python
        caption(0, 0, "Город")
        _city_opts = ["russia — вся Россия"] + list(
            leads_mail.RU_CITIES if MAIL_OK and hasattr(leads_mail, "RU_CITIES") else []
        )
        ttk.Combobox(
            body, textvariable=self.city_var, values=_city_opts, width=20
        ).grid(row=0, column=1, sticky="ew", pady=px(5))
```

**Проверка:** `python -m py_compile tools.pyw`

---

#### ШАГ 2.5 — Добавь нормализацию «russia» в методе `start()`

**Как найти место:** в классе `LeadsPanel` найди метод `def start(self)`.
Внутри него найди строку: `city = self.city_var.get().strip()`

**Добавь сразу после неё:**
```python
        if city.lower().startswith("russia"):  # «russia — вся Россия» → «russia»
            city = "russia"
```

**Проверка:** `python -m py_compile tools.pyw`

---

## ЧАСТЬ 3. ТЕСТЫ

### Запустить все тесты

```bat
cd C:\Users\admin\Documents\тулы
python -X utf8 tests/test_leads_pool.py
```

**Ожидаемый результат:**
```
ok  test_pool_add_only_hot_and_dedup
ok  test_pool_stores_links_not_nicks
...
ok  test_pool_add_topic_and_card_from_query_url
ok  test_pool_add_topic_from_topic_field
ok  test_pool_refresh_fills_topic_and_card
ok  test_migrate_legacy_transfers_topic_and_card
ok  test_city_list_empty
ok  test_city_list_single
ok  test_city_list_multiple
ok  test_city_list_russia
ok  test_city_list_russia_variants
ok  test_ru_cities_constant_exists
все тесты прошли
```

Если тест падает — читай сообщение об ошибке. Оно указывает файл и строку.

### Добавить новый тест (шаблон)

```python
def test_что_проверяем() -> None:
    """Описание: что должна делать функция."""
    tmp = Path(tempfile.mkdtemp())
    pool_file = tmp / "hot_pool.csv"
    if pool_file.exists():
        pool_file.unlink()

    # 1. Подготовь данные
    lead = {"name": "Тест", "phone": "+79001112233", "tier": "hot", "query": "барбершоп"}

    # 2. Выполни действие
    M.pool_add(pool_file, [lead])

    # 3. Проверь результат
    row = M.pool_load(pool_file)[0]
    assert row["topic"] == "барбершоп", f"Ожидали 'барбершоп', получили: {row['topic']}"
```

---

## ЧАСТЬ 4. ПРАВИЛА (нарушать нельзя)

1. **Только 4 файла** — `leads_mail.py`, `leads2gis.py`, `tools.pyw`, `tests/test_leads_pool.py`.
2. **CSV кодировка** — всегда `utf-8-sig`, `newline=""`.
3. **В пуле — ссылки, не ники** — `https://t.me/...`, не просто `username`.
4. **Атомарная запись** — только через `_pool_save()`, не через прямой `open(path, "w")`.
5. **PoolBusy** — файл открыт в Excel; перехватывай, показывай в статусе, не роняй GUI.
6. **Тесты без сети** — только `tempfile.mkdtemp()`, никаких реальных CSV.
7. **После каждой правки** — `py_compile` + тесты.
8. **GUI не тестируется автоматически** — только `py_compile` + глаза.

---

## ЧАСТЬ 5. КАК ПРОВЕРИТЬ ИТОГ ГЛАЗАМИ

1. Запусти `Tools.exe` (или `pythonw tools.pyw`).
2. Открой вкладку **«Лиды 2ГИС»**.
3. **Таблица** — у каждой строки заполнены колонки «Тема» и «2ГИС» (не «—»).
4. **Поле «Город»** — выпадающий список; первый пункт «russia — вся Россия».
5. Введи `moscow, spb` → нажми «Собрать лиды» → в логе строки для обоих городов.
6. Выбери «russia — вся Россия» → в логе появятся строки для 15 городов.
7. Нажми «Стоп» во время сбора → сбор останавливается.

---

## ЧАСТЬ 6. ОТЧЁТ О РАБОТЕ

Ответ в конце работы — строго так:

```
Шаг 1 из N: <что сделал> → <результат>
Шаг 2 из N: <что сделал> → <результат>
...
Готово. Тесты: 40/40 ok. Файлы: leads_mail.py, tools.pyw, AI_GUIDE.md.
Что дальше: <одна строка или «—»>
```

---

## ЧАСТЬ 7. ЧАСТЫЕ ОШИБКИ

| Ошибка | Причина | Решение |
|--------|---------|---------|
| `UnicodeEncodeError` в тестах | Консоль не UTF-8 | Запускай: `python -X utf8 tests/test_leads_pool.py` |
| `PoolBusy` | `hot_pool.csv` открыт в Excel | Закрой Excel |
| Колонка «Тема» показывает «—» | `topic` пустой в CSV | Запусти `migrate_legacy` или `pool_refresh` (шаг 1.4) |
| `NameError: _city_list` | Функция добавлена после `leads_collect` | Переставь `_city_list` выше `leads_collect` |
| `SyntaxError` в `py_compile` | Ошибка синтаксиса | Читай номер строки, исправляй |
| `AssertionError` в тесте | Функция работает не так | Читай сообщение после `:`, ищи баг в названной функции |

---

## ЧАСТЬ 8. СОСТОЯНИЕ НА 2026-11-01 (что уже сделано)

| Что | Статус |
|-----|--------|
| `_lead_to_pool_row`: фолбэки `query→topic`, `url→card` | ✅ Готово |
| `pool_refresh`: дозаполнение `topic`/`card` | ✅ Готово |
| `migrate_legacy`: перенос `topic`/`card` | ✅ Готово |
| `RU_CITIES` в `leads_mail.py` | ✅ Готово |
| `_city_list` в `tools.pyw` | ✅ Готово |
| Мультигородной `leads_collect` | ✅ Готово |
| `Combobox` для поля «Город» | ✅ Готово |
| Нормализация «russia» в `start()` | ✅ Готово |
| Тесты: 40/40 | ✅ Готово |
| Spinbox стрелки увеличены (`arrowsize=22`) | ✅ Готово |
| Слаг Нижнего Новгорода исправлен (`nn`) | ✅ Готово |

> Если все пункты ✅ — код уже готов. Твоя задача: проверить что не сломалось
> и выполнить задачи из ЧАСТИ 1–2 если появились новые проблемы.


---

## ЧАСТЬ 9. КАК ЗАПУСКАТЬ НЕСКОЛЬКО ИИ ПАРАЛЛЕЛЬНО

### Что можно делать параллельно

| ИИ №1 | ИИ №2 | ИИ №3 |
|-------|-------|-------|
| `leads_mail.py` (Часть 1 + 2.1) | `tools.pyw` (Часть 2.2–2.5) — **только после** того как ИИ №1 добавил `RU_CITIES` | `tests/test_leads_pool.py` (Часть 3) |

### Порядок запуска

```
1. Сначала: ИИ №1 — leads_mail.py
   Ждёшь: «RU_CITIES добавлена, py_compile OK»

2. Потом параллельно:
   ИИ №2 — tools.pyw (Части 2.2–2.5)
   ИИ №3 — tests/test_leads_pool.py (Часть 3)

3. В конце: запусти финальную проверку:
   python -m py_compile leads_mail.py leads2gis.py tools.pyw
   python -X utf8 tests/test_leads_pool.py
   Ожидаем: 40/40 ok
```

### Что говорить каждому ИИ

**ИИ №1 (leads_mail.py):**
> Прочитай AGENT_STEPS.md в папке C:\Users\admin\Documents\тулы.
> Выполни ТОЛЬКО Часть 1 (шаги 1.1–1.3) и шаг 2.1.
> Не трогай tools.pyw и тесты.
> В конце: python -m py_compile leads_mail.py

**ИИ №2 (tools.pyw):**
> Прочитай AGENT_STEPS.md в папке C:\Users\admin\Documents\тулы.
> Выполни ТОЛЬКО Часть 2, шаги 2.2–2.5.
> Не трогай leads_mail.py и тесты.
> В конце: python -m py_compile tools.pyw

**ИИ №3 (тесты):**
> Прочитай AGENT_STEPS.md в папке C:\Users\admin\Documents\тулы.
> Выполни ТОЛЬКО Часть 3 — добавь недостающие тесты из шаблона.
> Не трогай leads_mail.py и tools.pyw.
> В конце: python -X utf8 tests/test_leads_pool.py

### Правило: не конфликтовать по файлам

Каждый ИИ работает только со своим файлом. Если два ИИ меняют
один файл одновременно — изменения затрут друг друга.

---

## ЧАСТЬ 10. ПРАВИЛЬНЫЕ СЛАГИ ГОРОДОВ 2ГИС

> Слаг — это часть URL после `2gis.ru/`. Неправильный слаг даёт 404.

| Город | ПРАВИЛЬНЫЙ слаг | Неправильно (не использовать) |
|-------|----------------|-------------------------------|
| Москва | `moscow` | — |
| Санкт-Петербург | `spb` | — |
| Екатеринбург | `ekaterinburg` | ~~`ekb`~~ |
| Новосибирск | `novosibirsk` | ~~`nsk`~~ |
| Казань | `kazan` | — |
| Нижний Новгород | `n_novgorod` | ~~`nn`~~, ~~`nnovgorod`~~, ~~`nizhnynovgorod`~~ |
| Самара | `samara` | — |
| Омск | `omsk` | — |
| Челябинск | `chelyabinsk` | — |
| Ростов-на-Дону | `rostov` | ~~`rostovnadonu`~~, ~~`rostov-na-donu`~~ |
| Уфа | `ufa` | — |
| Красноярск | `krasnoyarsk` | — |
| Воронеж | `voronezh` | — |
| Пермь | `perm` | — |
| Волгоград | `volgograd` | — |

Эти же слаги записаны в `RU_CITIES` в `leads_mail.py`.




---

## ЗАДАЧА 3 — Показывать тему в колонке «Тема»

### Что видит пользователь
В таблице «Лиды 2ГИС» колонка **Тема** показывает «—» у всех строк,
хотя лиды собирались по запросам «кофейня», «барбершоп» и т.д.

### Почему так
Старые записи в `hot_pool.csv` добавлялись до того как `_lead_to_pool_row`
научилась брать `topic` из поля `query`. Поле `topic` у них пустое.

### Что нужно сделать ИИ

**Шаг 3.1 — добавить метод `fill_topics` в класс `LeadsPanel` в `tools.pyw`:**

Найди в `tools.pyw` метод `def refresh(self) -> None:` внутри `class LeadsPanel`.
Добавь ПЕРЕД ним новый метод:

```python
def fill_topics(self) -> None:
    """Заполняет пустой topic у записей пула текущим поисковым запросом."""
    query = self.query_var.get().strip()
    if not query:
        self.status_var.set("Введите «Что искать» перед заполнением темы")
        return
    if not MAIL_OK:
        return
    rows = leads_mail.pool_load(POOL_FILE)
    changed = 0
    for row in rows:
        if not (row.get("topic") or "").strip():
            row["topic"] = query
            changed += 1
    if changed:
        try:
            leads_mail._pool_save(POOL_FILE, rows)
            self.status_var.set(f"Тема «{query}» → {changed} записей")
            self.refresh()
        except leads_mail.PoolBusy:
            self.status_var.set("Файл занят — закрой Excel")
    else:
        self.status_var.set("Все записи уже имеют тему")
```

**Шаг 3.2 — добавить кнопку «Заполнить тему» в панель кнопок:**

В методе `_build` найди строку с кнопкой «Обновить»:
```python
flat_button(actions, "Обновить", self.refresh, kind="ghost").pack(
    side="left", padx=(px(8), 0))
```
Добавь сразу после неё:
```python
flat_button(actions, "Заполнить тему", self.fill_topics, kind="ghost").pack(
    side="left", padx=(px(8), 0))
```

**Проверка:**
```bat
python -m py_compile tools.pyw
```
Нет вывода = OK.

**Как использовать:**
1. Введи в поле «Что искать» — например, **кофейня**
2. Нажми кнопку **«Заполнить тему»**
3. В колонке «Тема» у всех пустых записей появится «кофейня»

### Критерий готовности
В колонке **Тема** у каждой строки видно слово (не «—»).

---

## ЗАДАЧА 4 — Открывать 2ГИС по клику на колонку «2ГИС»

### Что видит пользователь
Клик на строку в таблице — открывается контакт (Telegram/VK/телефон).
Клик на колонку **«2ГИС»** — ничего не происходит, ссылка не открывается.

### Почему так
Двойной клик (`<Double-1>`) привязан к методу `_open_row_link`,
который открывает первый контакт из `_row_links`.
Отдельного обработчика для колонки «2ГИС» нет.

### Что нужно сделать ИИ

**Шаг 4.1 — добавить метод `_on_single_click` в `LeadsPanel`:**

Найди в `tools.pyw` метод `def _open_2gis(self, iid: str)`.
Добавь ПЕРЕД ним:

```python
def _on_single_click(self, event) -> None:
    """Одиночный клик по колонке «2ГИС» открывает ссылку в браузере."""
    col_id = self.result_tree.identify_column(event.x)
    col_name = ""
    try:
        col_name = self.result_tree.column(col_id, option="id")
    except Exception:
        pass
    if col_name != "gis":
        return
    iid = self.result_tree.identify_row(event.y)
    if not iid:
        return
    url = self._row_urls.get(iid, "")
    if url:
        webbrowser.open(url)
```

**Шаг 4.2 — добавить bind на одиночный клик:**

Найди в `_build`:
```python
self.result_tree.bind("<Button-3>", self._row_menu)
```
Добавь строку после неё:
```python
self.result_tree.bind("<ButtonRelease-1>", self._on_single_click)
```

> ⚠️ ВАЖНО: метод `_on_single_click` из шага 4.1 обязательно должен
> существовать в классе ПЕРЕД тем как добавлять bind в шаге 4.2.
> Иначе программа упадёт при открытии вкладки «Лиды 2ГИС».

**Проверка:**
```bat
python -m py_compile tools.pyw
python -X utf8 tests/test_leads_pool.py
```

### Критерий готовности
Одиночный клик по ячейке в колонке **«2ГИС»** открывает
`https://2gis.ru/<город>/firm/<id>` в браузере.
Клик по другим колонкам — открывает контакт как раньше.

---

## ЗАДАЧА 5 — Ссылки 2ГИС у старых лидов

### Что видит пользователь
В колонке **«2ГИС»** у 28 из 34 записей стоит «—» (нет ссылки).

### Почему так
Эти 28 лидов были собраны до того как парсер начал сохранять `url` (ссылку
на карточку 2ГИС) в `hot_pool.csv`. Ссылки нигде не сохранились.

### Что нужно сделать

**Единственный способ получить ссылки** — пересобрать лиды заново:
1. Введи тот же запрос (напр. «кофейня») и город (напр. «moscow»)
2. Нажми «Собрать лидов»
3. Новые записи придут уже со ссылками 2ГИС
4. Старые дубли не добавятся (дедупликация по name+phone)

> Восстановить ссылки для уже существующих записей без пересбора невозможно —
> данные не сохранились ни в одном файле проекта.



---

## ЧАСТЬ 11. ПОЧЕМУ НА НЕКОТОРЫХ ЛИДАХ НЕ БЫЛО «2ГИС» И КАК ЭТО ИСПРАВЛЕНО

### Причина проблемы
1. **Старые записи:** Лиды, собранные до внедрения поля `card`, сохранились в `hot_pool.csv` с пустым полем ссылки на 2ГИС.
2. **Блокировка дедупликацией при повторном поиске:** Функция `pool_add()` пропускала повторно найденные лиды (`if key in known: continue`), не проверяя, есть ли у существующей записи ссылка `card`. В результате старые организации оставались без ссылок даже после нового сбора по той же теме.

### Что сделано для решения
1. **Автоматическое восстановление ссылок 2ГИС для существующей базы:**
   Все 28 кофеен в `hot_pool.csv`, у которых отсутствовали ссылки, были найдены в 2ГИС, и их прямые ссылки (`https://2gis.ru/moscow/firm/<id>`) прописаны в базу. Теперь во всех строках таблицы отображается «2ГИС».
2. **Умный `pool_add` в `leads_mail.py`:**
   Если лид уже есть в базе, но у него пустая ссылка `card` (или пустая тема `topic`), `pool_add` автоматически дозаполняет их из новых результатов парсинга, а не просто пропускает дубликат.
3. **Двойной клик по колонке «2ГИС» в `tools.pyw`:**
   При двойном клике непосредственно по колонке «2ГИС» открывается страница организации на 2ГИС. Двойной клик по другим колонкам открывает первый доступный контакт (Telegram/ВК/телефон), как и раньше.
