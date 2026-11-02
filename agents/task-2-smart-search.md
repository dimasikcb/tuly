# ЗАДАЧА 2 — Умный поиск: несколько городов и «вся Россия»

**Исполнитель:** агент по `tools.pyw` (+ одна константа в `leads_mail.py`).
**Файлы:** `leads_mail.py` (константа), `tools.pyw` (разбор, сбор, UI).
**Статус на 2026-11-02:** ✅ выполнено — режимы «один город», «`moscow, spb`»,
«вся Россия» работают.

---

## Что должно получиться

| Ввод в поле «Город» | Поведение |
|---------------------|-----------|
| `moscow` | как раньше: один город |
| `moscow, spb` | сбор по каждому городу, лимит карточек делится |
| `russia` / «russia — вся Россия» | сбор по `RU_CITIES` (15 крупных городов) |

Прогресс в статусе: `город 2/3 spb стр.1/2 · 8/20`.
В лог на каждый город: `spb / барбершоп: карточек 20`.

---

## ШАГ 2.1 — константа `RU_CITIES` в `leads_mail.py`

Сразу после `POOL_KEEP_STATUS`:

```python
# 2ГИС-слаги крупных городов для режима «вся Россия».
RU_CITIES = (
    "moscow", "spb", "ekaterinburg", "novosibirsk", "kazan", "n_novgorod",
    "samara", "omsk", "chelyabinsk", "rostov",
    "ufa", "krasnoyarsk", "voronezh", "perm", "volgograd",
)
```

**Слаги проверены, не менять «на глаз»:**

| Город | Слаг | Типичная ошибка |
|-------|------|-----------------|
| Москва | `moscow` | — |
| Санкт-Петербург | `spb` | — |
| Екатеринбург | `ekaterinburg` | `ekb` → 404 |
| Новосибирск | `novosibirsk` | `nsk` → 404 |
| Казань | `kazan` | — |
| Нижний Новгород | `n_novgorod` | `nn`, `nnovgorod`, `nizhnynovgorod` |
| Самара | `samara` | — |
| Омск | `omsk` | — |
| Челябинск | `chelyabinsk` | — |
| Ростов-на-Дону | `rostov` | `rostovnadonu`, `rostov-na-donu` |
| Уфа | `ufa` | — |
| Красноярск | `krasnoyarsk` | — |
| Воронеж | `voronezh` | — |
| Пермь | `perm` | — |
| Волгоград | `volgograd` | — |

Добавить город = дописать слаг в кортеж. Больше ничего править не нужно.

---

## ШАГ 2.2 — `_city_list` в `tools.pyw`

Функция **обязана стоять выше** `leads_collect` (иначе `NameError` в момент
вызова, а не при компиляции):

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

---

## ШАГ 2.3 — мультигородной `leads_collect`

Внутри функции:

```python
    cities = _city_list(city)
    if not cities:
        cities = [city.strip() or "moscow"]
    want_share = max(1, want // len(cities))
```

Дальше цикл по городам: страницы выдачи → карточки → `city_cards[:want_share]`
→ `all_cards.extend(...)` → `on_log(f"{cur_city} / {query}: карточек {n}")`.
Проверка `stop.is_set()` — **на входе в каждый город и на каждой странице**,
иначе «Стоп» не сработает в режиме «вся Россия».

Парсинг карточек идёт пачкой через `ThreadPoolExecutor(max_workers=6)`, где
задача — кортеж `(city, firm_id)`: город нужен, чтобы `parse_firm` собрал
правильный `url` карточки.

### Известный мусор, который стоит убрать

В текущей версии остался мёртвый блок (пустой цикл с `pass` перед построением
`card_infos`). Он ничего не делает, но путает. Правильная и короткая замена
всего построения:

```python
    card_infos = [(c.get("city") or cities[0], c["id"]) for c in all_cards]
```

Причина: `parse_search` в `leads2gis.py` уже кладёт `city` в каждую карточку,
восстанавливать соответствие перебором не нужно.

---

## ШАГ 2.4 — `Combobox` вместо `Entry` для города

В `LeadsPanel._build`:

```python
        caption(0, 0, "Город")
        _city_opts = ["russia — вся Россия"] + [
            c for c in (leads_mail.RU_CITIES if MAIL_OK and hasattr(leads_mail, "RU_CITIES") else [])
        ]
        ttk.Combobox(
            body, textvariable=self.city_var, values=_city_opts, width=20
        ).grid(row=0, column=1, sticky="ew", pady=px(5))
```

`Combobox` без `state="readonly"` — специально: пользователь может вписать
`moscow, spb` руками.

---

## ШАГ 2.5 — нормализация в `start()`

В `LeadsPanel.start`, сразу после `city = self.city_var.get().strip()`:

```python
        if city.lower().startswith("russia"):  # «russia — вся Россия» → «russia»
            city = "russia"
```

Без этого в `_city_list` уйдёт строка `"russia — вся Россия"` и она разобьётся
по запятой в мусор.

---

## Проверка

```bat
python -m py_compile leads_mail.py tools.pyw
python -X utf8 tests/test_leads_pool.py
```

Тесты по задаче: `test_city_list_empty`, `test_city_list_single`,
`test_city_list_multiple`, `test_city_list_russia`,
`test_city_list_russia_variants`, `test_ru_cities_constant_exists`.

GUI проверяется глазами: выбрать «russia — вся Россия», нажать «Собрать лидов»
— в логе должны идти строки по городам; «Стоп» останавливает между городами.

**Риск:** 15 городов × 4 страницы × 6 потоков = много запросов, 2ГИС может
отдать captcha. Защита уже есть: `BlockedError` из `leads2gis.py` попадает в
`_fail` и показывается в статусе. При частых captcha — уменьшить «Карточек»
(например, 60 на всю Россию = 4 карточки на город).
