# Задача 02 — Парсер оценок (JSON + HTML)

**Исполнитель:** агент по `parsers.py` (+ `tests/test_parsers.py`).
**Статус:** ✅ выполнено, 32 офлайн-теста.
**Правишь только:** `parsers.py`. Не трогай `diary_client.py`, `app.py`, математику.

## Контекст
`authedu.mosreg.ru` — SPA за ЕСИА. Точные API/DOM без живой сессии неизвестны,
поэтому парсер **адаптивный**: сначала перехваченные JSON-ответы, потом DOM.
Используется `models.SubjectGrades`, `config.load_selectors()`, `selectors.json`.
Зависимости — **только stdlib** (`json`, `re`, `html.parser`, `pathlib`).
Никаких bs4/lxml/Playwright/tkinter.

## Обязательные функции
`parse_grades_payload(payload, selectors=None)`, `parse_grades_html(html, selectors=None)`,
`parse_json_responses(responses, selectors=None)`, `merge_subjects(*groups)`,
`parse_file(path)`, `validate_subjects(subjects)`, `save_debug_dump(text, suffix)`.

## Требования
- Рекурсивный обход JSON: название предмета из `json_subject_keys` (в т.ч.
  `{"subject": {"name": ...}}`), оценки из `json_marks_keys` / `json_mark_keys`
  (`[5,4]`, `[{"value":5}]`, `"4"`, обёртки `{"data":{"items":[...]}}`).
- В оценки берутся **только целые 1..5**. Средний балл, даты, «н/а», числа 6..10 — нет.
- HTML: таблицы (первая осмысленная ячейка = предмет, остальные целые 1..5 = оценки)
  и div-карточки с `data-subject` / `data-mark` / `data-grade`, class `*mark*/*grade*/*score*`.
  `4,50` и `4.5` — НЕ оценки. Строка без названия или без оценок отбрасывается.
- Дедуп предметов по имени с объединением оценок; `ParseError` на пустом результате.
- `parse_file` сам определяет HTML/JSON по расширению и содержимому, понимает
  массив `[{"url": ..., "payload": {...}}]`.

## Проверка
```bash
python -X utf8 -m unittest tests.test_parsers -v
```
Обязательные фикстуры: `<td>4,50</td>` не становится оценкой; даты `05.11` и `10`
не становятся оценками; вложенный `data.items`; список словарей оценок.

## Критерий готовности
`import parsers` работает без Playwright/браузера; все 32 теста зелёные;
на реальном дампе из `debug/` разбор даёт предметы с оценками.
