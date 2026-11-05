# ⚠️ КРИТИЧЕСКИ ВАЖНОЕ ПРАВИЛО ДЛЯ ВСЕХ НЕЙРОСЕТЕЙ И ИИ-АГЕНТОВ ⚠️
> **СТРОГО ЗАПРЕЩЕНО ВЫКЛЮЧАТЬ, ОБРЫВАТЬ, ПЕРЕЗАПУСКАТЬ ИЛИ ГАСИТЬ АКТИВНЫЙ СИСТЕМНЫЙ VPN / ТУННЕЛЬ (HAPP, XRAY, SING-BOX).**
> 
> Сессия работы ИИ подключена через этот туннель. Любое отключение системного VPN немедленно разрывает сетевое соединение с облачным сервером ИИ, убивает текущую сессию и блокирует работу.
> 
> Правила тестирования сети:
> 1. При тестировании модуля `bypass_core.py` ВСЕГДА используйте моки (`unittest.mock`), изоляцию и офлайн-тесты.
> 2. НЕ вызывайте `bypass_stop()`, `bypass_happ_stop()` или `taskkill` по отношению к системным ядрам вживую.
> 3. НЕ отключайте сетевые интерфейсы через `netsh interface set interface ... disable`.

---

# AI_GUIDE.md — руководство для ИИ по проекту «Лиды 2GIS»

Живой документ. Написан для того, чтобы **любой ИИ-агент мог продолжить работу
с любого шага без потери контекста**. Прочитай целиком, прежде чем что-то менять.

---

## 1. Что это за проект

Одно окно Tkinter (`tools.pyw`) с несколькими разделами. Нас интересует только
раздел **«Лиды 2GIS»** (вкладка `leads`). Он:

1. собирает организации из поисковой выдачи 2ГИС (парсер `leads2gis.py`);
2. ранжирует их: «горячие» (нет сайта, есть телефон) попадают в **пул горячих
   лидов** — один файл `hot_pool.csv`;
3. показывает пул в таблице: **Название · Тема · Контакты · 2ГИС**;
4. умеет запускать ИИ-рассылку (отдельное окно `MailWindow`).

**Остальные разделы (DNS, кликер, очистка Windows, GitHub, Bypass) трогать
запрещено.** Все правки — только в этих файлах:

| Файл            | Слой | Роль                                                              |
| --------------- | ---- | ----------------------------------------------------------------- |
| `leads_contacts.py` | 1 | Чистые функции: ник ↔ ссылка, каналы, формат телефона. Без сети, файлов и GUI. |
| `leads_pool.py` | 2 | CSV пула: загрузка/сохранение, дедуп, срок жизни, миграция, статистика. |
| `leads_mail.py` | 3 | Рассылка: ИИ-тексты, Telegram/MAX/VK/Email, журнал, поллер ответов. Реэкспортирует имена пула и контактов. |
| `leads2gis.py`  | 3 | Парсер 2ГИС: поиск, карточки фирм, контакты, скоринг. Без GUI.   |
| `tools.pyw`     | 4 | GUI + оркестрация: панель лидов, сбор в потоке, таблица пула.    |
| `tests/test_leads_pool.py` | — | Офлайн-тесты (запуск без сети).                        |

**Правило слоёв:** нижний слой не импортирует верхний.
`contacts` ← `pool` ← `mail` ← `tools.pyw`; `leads2gis` использует только
`contacts`. Если появляется обратный импорт — это ошибка проектирования.

**Остальная часть приложения (DNS, кликер, очистка, GitHub, Bypass)** вынесена
из `tools.pyw` в три модуля без GUI — их можно проверять без tkinter:

| Файл | Роль |
| ---- | ---- |
| `winbase.py` | пути приложения, `config.json`, `ps_run`, `run_tool`, `is_admin` — нижний слой |
| `apps_catalog.py` | каталог программ: процессы, реестр, ярлыки, `app_catalog`, `filter_apps` |
| `bypass_core.py` | Happ/GoodbyeDPI/ProxiFyre: `bypass_status`, `bypass_apply`, `bypass_stop`, реестр, службы |

`tools.pyw` импортирует их и не содержит собственных копий этих функций
(это проверяет `tests/test_arch.py`). Если правишь логику обхода или каталога —
правь модуль, а не GUI.

Имена уровня пула и контактов доступны и через `leads_mail` (реэкспорт),
поэтому `leads_mail.pool_add(...)` продолжает работать — старый код и тесты
переписывать не нужно. Новый код должен импортировать из того модуля, где
функция реально живёт.


---

## 2. Поток данных (важно понять до правок)

```
2ГИС-выдача (SSR HTML)
   │  TwoGisClient.search_page(city, query, page) — id фирм
   ▼
leads_collect() в tools.pyw (стр. ~6317)
   │  на каждую фирму: TwoGisClient.firm_html → parse_firm(...)
   │  parse_firm возвращает dict с полями: id, name, rubric, phones,
   │  emails, messengers, website, url, rating, review, query, city, ...
   ▼
   lead["topic"] = query        ← ТЕМА ставится здесь (tools.pyw ~6378)
   lead["score"], reason = score(lead)
   ▼
   Filters(no_website=..., has_phone=True, min_reviews=...) — отбор
   ▼
leads_mail.pool_add(POOL_FILE, selected)
   │  - фильтрует только hot (is_hot)
   │  - дедуп по pool_key (name + digits phone)
   │  - каждая запись → _lead_to_pool_row(lead, found_at)
   ▼
hot_pool.csv  ← единственный источник истины для таблицы
   │
   ▼
LeadsPanel.refresh() → pool_load(POOL_FILE) → таблица (tools.pyw ~6623)
```

Ключевой факт: **таблица пула рисуется только из `hot_pool.csv`**. Если в
CSV колонки `topic` и `card` пустые — в интерфейсе будет «—», даже если
исходный лид их имел.

---

## 3. Формат данных hot_pool.csv

### Колонки (`POOL_COLUMNS` в leads_mail.py)

```
name, telegram, max, vk, email, phone, city,
topic, card, found_at, sent_at, channel, status
```

- `telegram/max/vk` — **готовые ссылки** (`https://t.me/...`,
  `https://max.ru/...`, `https://vk.com/...`), а НЕ ники. Так файл кликабелен
  в Excel и Excel не портит числа.
- `email` — `mailto:...`.
- `topic` — поисковый запрос, по которому найден лид (строка).
- `card` — прямая ссылка на карточку 2ГИС (`https://2gis.ru/<city>/firm/<id>`).
- `found_at` — ISO-время находки; от него считается срок жизни (5 дней по
  умолчанию, `pool_days` в настройках).
- `sent_at` / `channel` / `status` — про рассылку; `status` в
  `POOL_KEEP_STATUS = ("replied","seen","awaiting","manual")` защищает от чистки.
- **Числового `id` 2ГИС в пуле нет** — Excel превращал его в
  `7.0000001087E+16`, а для дедупа хватает названия+телефона.

### Дедупликация

`pool_key(row) = <name, lower, сжатые пробелы>|<digits(phone)>`.
Два лида с одним `name|phone` — одна запись. Пустой `name` и пустой `phone`
дают пустой ключ → лид не добавляется.

### Вспомогательные функции leads_mail.py

| Функция | Назначение |
| ------- | ---------- |
| `_to_link(channel, value)` | ник/e-mail → ссылка (уже ссылку не трогает) |
| `_from_link(channel, value)` | ссылка → ник (для отправки) |
| `_pool_value(channel, value)` | значение для колонки пула |
| `_pick_contact(lead, channel)` | контакт лида по приоритету (из парсера) |
| `pool_load(pool_file)` | читает CSV пула, возвращает list[dict] |
| `_pool_save(pool_file, rows)` | атомарная запись (tmp + replace, retry ×10) |
| `pool_add(pool_file, leads)` | добавить горячих, вернуть число новых |
| `pool_sync(csv_file)` | перелить горячих из старого leads.csv в пул |
| `pool_refresh(pool_file)` | дозаполнить пустые контакты в пуле из leads.csv |
| `pool_mark_sent(pool_file, key, channel)` | пометить «написал» |
| `pool_mark_status(pool_file, key, status)` | пометить статус |
| `pool_purge(pool_file, days)` | удалить просроченные (не защищённые статусом) |
| `pool_stats(pool_file, days)` | total / expiring |
| `migrate_legacy(dir_path)` | разовый переезд leads.csv → пул + архив |

---

## 4. ПРАВИЛА РАБОТЫ (нарушать нельзя)

1. **Правки только в 4 файлах** (см. §1). `Tools.exe` — это сборка, не править.
2. **Кодировка CSV — `utf-8-sig`** при чтении и записи, `newline=""`.
3. **В пуле — ссылки, а не ники** (§3). Никогда не пиши голые логины в CSV.
4. **Атомарность**: запись пула — через `_pool_save` (tmp+replace с retry),
   не через прямое `open(path,"w")` поверх живого файла.
5. **Не ронять сбор из-за одной битой карточки**: парсинг каждой фирмы
   обёрнут в try/except в `leads_collect`.
6. **`PoolBusy`** — файл открыт в Excel; перехватывай и показывай в статусе,
   а не роняй приложение.
7. **Не ломать интерфейс остальных разделов** `tools.pyw`: менять только
   класс `LeadsPanel`, функцию `leads_collect` и их непосредственные помощники.
8. После правок — **прогнать тесты** (`python tests/test_leads_pool.py`) и
   синтаксис (`python -m py_compile tools.pyw leads_mail.py leads2gis.py`).
9. Реальные правки в `tools.pyw` **проверяются глазами + py_compile**: GUI в
   консоли не запускается и не тестируется автоматически.
10. Отчёт о работе — **краткий список шагов с результатами каждого**, без
    воды. Формат: `Шаг N из M: <что сделал> → <результат>`.

---

## 5. ЗАДАЧА 1 — «темы и 2ГИС не отображаются в программе»

### 5.1 Диагноз (проверен 2026-11-01)

В `hot_pool.csv` у ВСЕХ записей пустые `topic` и `card`, хотя в старом
`leads.csv` у тех же фирм заполнены `query` (="салон красоты") и
`url` (=`https://2gis.ru/moscow/firm/...`).

Причина: пул был наполнен через `migrate_legacy()` из старого `leads.csv`,
а `migrate_legacy` дозаполняет только контакты
(telegram/max/vk/email) и НЕ переносит `query→topic` и `url→card`.
Плюс `_lead_to_pool_row` не имеет фолбэка `query`/`url`, если в лиде нет
поля `topic`.

### 5.2 Как чинить (по шагам)

**A. `leads_mail.py` — `_lead_to_pool_row`:** добавить фолбэки:

```python
"topic": str(lead.get("topic") or lead.get("query") or "").strip(),
"card": str(lead.get("url") or lead.get("card") or "").strip(),
```

**B. `leads_mail.py` — `migrate_legacy`:** в цикле дозаполнения, помимо
контактов, переносить `src["query"]→row["topic"]` и `src["url"]→row["card"]`,
если они пустые. Считать их в `out["filled"]`.

**C. `leads_mail.py` — `pool_refresh`:** расширить на тему/карточку, чтобы
уже существующие записи можно было дозаполнить без миграции:

```python
if not (row.get("topic") or "").strip() and (src.get("query") or src.get("topic")):
    row["topic"] = str(src.get("query") or src.get("topic")).strip(); updated += 1
if not (row.get("card") or "").strip() and (src.get("url") or src.get("card")):
    row["card"] = str(src.get("url") or src.get("card")).strip(); updated += 1
```

**D. Прогнать миграцию/дозаполнение над реальными файлами**, чтобы таблица
сразу показала темы и 2ГИС (файлы: `hot_pool.csv` + `leads.csv` лежат рядом;
после успешной миграции `leads.csv` переименуется в `leads.csv.migrated` —
это нормальное поведение).

**E. Тесты.** Добавить в `tests/test_leads_pool.py`:
- лид с `query` и `url` → в пуле `topic`/`card` заполнены;
- `pool_refresh` дозаполняет пустые `topic`/`card` из leads.csv;
- `migrate_legacy` переносит `query→topic` и `url→card`.

### 5.3 Критерий готовности

Каждая строка таблицы показывает тему (напр. «салон красоты») и ссылку
«2ГИС», которая открывает карточку. Старые записи тоже заполнены после
миграции.

---

## 6. ЗАДАЧА 2 — «умный поиск: несколько городов или вся Россия»

### 6.1 Как сейчас

- Поле «Город» — одно текстовое поле, слаги 2ГИС (`moscow`, `spb`, ...).
- `leads_collect(city, query, cards, ...)` ходит по одному городу.
- Советы в логе: `moscow, spb, kazan, ekb, nsk`.

### 6.2 Цель

- Можно указать **несколько городов**: `moscow, spb, kazan` — сбор идёт по
  каждому, лимит карточек распределяется.
- Можно выбрать **«Вся Россия»**: флаг/вариант, который превращается в
  фиксированный список слагов крупных городов (константа `RU_CITIES`).
- Старый сценарий (один город) не ломается.

### 6.3 Как чинить (проект)

**A. `leads_mail.py` (или рядом):** константа слагов крупных городов:

```python
# 2ГИС-слаги крупных городов для режима «вся Россия».
RU_CITIES = (
    "moscow", "spb", "ekb", "nsk", "kazan", "nnovgorod",
    "samara", "omsk", "chelyabinsk", "rostov-na-donu",
    "ufa", "krasnoyarsk", "voronezh", "perm", "volgograd",
)
```

**B. `tools.pyw` — `leads_collect`:** на входе city-строка; разобрать:

```python
def _city_list(city: str) -> list[str]:
    if city.strip().lower() in ("russia", "вся россия", "всяроссия"):
        return list(RU_CITIES)
    return [c.strip() for c in city.split(",") if c.strip()]
```

Внутри `leads_collect`:
- `cities = _city_list(city)`;
- `want_share = max(1, want // len(cities))` — лимит на город;
- обойти города циклом, для каждого: страницы выдачи → карточки → парсинг
  (нотификация: `город i/N: ...`);
- агрегировать `parsed` по всем городам, потом один общий фильтр/сорт/пул;
- `on_log` перечисляет города: `moscow, spb / барбершоп: карточек N`.
- кнопка «Стоп» должна прерывать между городами (проверять `stop.is_set()`).

**C. `tools.pyw` — `LeadsPanel._build`:**

- Поле «Город» — `ttk.Combobox` (можно вводить самим) со значениями:
  `"russia — вся Россия"` + `RU_CITIES`. Первый элемент — «вся Россия».
- В `start()` нормализовать: если выбран `"russia — вся Россия"` →
  передать `"russia"`. Иначе передать строку как есть (поддерживается
  `moscow, spb`).

**D. Тесты:** `_city_list("") → []`, `_city_list("moscow") → ["moscow"]`,
`_city_list("moscow, spb") → ["moscow","spb"]`,
`_city_list("russia") → RU_CITIES`.

### 6.4 Критерий готовности

- Ввод `moscow, spb` в поле города собирает по обоим городам;
- выбор «вся Россия» запускает сбор по списку крупных городов с прогрессом;
- одиночный город работает как раньше.

---

## 7. Как проверить результат

```bat
python -m py_compile tools.pyw leads_mail.py leads2gis.py
python tests/test_leads_pool.py
```

- Тест-раннер — простой `if __name__ == "__main__"`: выполнит все
  `test_*`-функции по порядку, печатает `ok <имя>`; в конце — `все тесты прошли`.
- **Тесты не ходят в сеть**: только tempfile + локальные CSV.
- Если тест падает из-за «чужих» данных — каждый тест обязан начинаться с
  очистки своего `hot_pool.csv`:
  `if M.pool_path(path).exists(): M.pool_path(path).unlink()`
- **Никогда не пиши в реальные `hot_pool.csv`/`leads.csv` из тестов** —
  только через `tempfile.mkdtemp()`.

---

## 8. Отчёт о работе

В конце работы ответить так (кратко, по-русски):

```
Шаг N из M: <действие> → <результат>
...
Готово. Тесты: 32/32 ok. Файлы: leads_mail.py, tools.pyw, AI_GUIDE.md.
```

Пункт «что дальше» — одна строка, если есть реальное продолжение.

---

## 9. ПОШАГОВЫЙ ФАЙЛ ДЛЯ ИИ-АГЕНТОВ

> Если ты слабый ИИ или не хочешь читать весь этот документ —
> **прочитай `AGENT_STEPS.md`** в этой же папке.
> Там все шаги расписаны максимально конкретно: что найти, что написать, как проверить.

---

## 10. ТЕКУЩЕЕ СОСТОЯНИЕ КОДА (2026-11-01)

| Что | Статус |
|-----|--------|
| `_lead_to_pool_row`: фолбэки `query->topic`, `url->card` | OK |
| `pool_refresh`: дозаполнение `topic`/`card` | OK |
| `migrate_legacy`: перенос `topic`/`card` | OK |
| `RU_CITIES` в `leads_mail.py` | OK |
| `_city_list` в `tools.pyw` | OK |
| Мультигородной `leads_collect` | OK |
| Combobox для поля Город | OK |
| Нормализация russia в `start()` | OK |
| Тесты: 40/40 | OK |

---

## 11. ИСТОРИЯ ПРАВОК

| Дата | Что изменено |
|------|-------------|
| 2026-11-01 | Задача 1: `_lead_to_pool_row` фолбэки, `pool_refresh` + `migrate_legacy` topic+card. Задача 2: `RU_CITIES`, `_city_list`, мультигородной `leads_collect`, Combobox. Тесты: +10 новых (итого 40). Создан `AGENT_STEPS.md`. |
| 2026-11-02 | Исправлены слаги: `ekb`→`ekaterinburg`, `nsk`→`novosibirsk`, `nnovgorod`→`nn`, `rostov-na-donu`→`rostovnadonu`. Spinbox стрелки: `arrowsize=22`. |
