# Задача 07 — Документация и калибровочный конфиг

**Исполнитель:** агент по `README.md` и `selectors.json`.
**Статус:** ✅ выполнено.
**Правишь только:** `README.md`, `selectors.json`, `agents/00-INDEX.md`.

## `README.md` должен отвечать на всё
1. Быстрый старт Windows 11 / Fedora (включая `python3-tkinter`, `py -3` с tkinter,
   симлинк `.desktop`, `playwright install-deps`).
2. Первый запуск (ручной вход ЕСИА, сохранение cookies) vs последующие (headless).
3. Столбцы таблицы, цвета, сортировка, поиск, клик по строке.
4. Формулы: `k = 9N − 2S`, запас `floor((2S−9N)/(9−2v))`, ровно 4.50 не падение.
5. Калибровка: JSON-ответы → DOM → дампы `debug/`; какие ключи `selectors.json`
   править при смене вёрстки; импорт сохранённой страницы.
6. Структура файлов, команды тестов и `--smoke-shot`.
7. **Честный раздел ограничений:** живой парсинг без сессии не проверен; что проверено.

## `selectors.json` — обязательные ключи
`start_url`, `logged_in_markers`, `expired_url_hints`, `grades_nav_texts`,
`period_nav_texts`, `json_url_keywords`, `json_subject_keys`, `json_marks_keys`,
`json_mark_keys`, `subject_row_selectors`, `mark_selectors`, `skip_subject_words`.
Значения — списки строк, JSON валиден (это проверяет `tests/test_smoke.py`).

## Проверка
```bash
python -c "import json; json.load(open('selectors.json', encoding='utf-8'))"
python -X utf8 -m unittest tests.test_smoke -v
```

## Критерий готовности
По README можно установить и запустить приложение на обеих ОС, не читая код;
не утверждается, что парсер проверен на живом сайте, если это не так.
