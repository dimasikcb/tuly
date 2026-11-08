# Задача 03 — Playwright: вход ЕСИА и скрытый сбор

**Исполнитель:** агент по `diary_client.py` (+ `tests/test_client.py`).
**Статус:** ✅ выполнено, 14 тестов на моках (браузер не запускается).
**Правишь только:** `diary_client.py`. Не трогай GUI и парсеры.

## Контекст
`app.py` вызывает две тонкие обёртки в фоновом потоке:
`fetch_grades(period, on_status) -> FetchResult` и
`interactive_login(on_status, timeout_s) -> bool`.
Контракты: `models.py` (`FetchResult`, `Period`, `SessionExpired`, `ParseError`, `DiaryError`),
`config.py` (`START_URL`, `SESSION_FILE`, `DEBUG_DIR`, `atomic_write_json`, `load_settings`),
`parsers.py`, `selectors.json`.

## Поведение
- **Первый запуск:** `login()` → Chromium `headless=False`, `goto(START_URL)`,
  пользователь вручную входит через Госуслуги. Успех определяем по URL (нет
  `esia.gosuslugi.ru` / `/login` / `oauth`; подсказка `auth`, входящая в сам
  `authedu.mosreg.ru`, исключается!) + маркеру из `logged_in_markers`.
  Таймаут `login_timeout_s` (300 с). Сохраняем `context.storage_state()` в
  `SESSION_FILE` атомарно, браузер закрывается в `finally`. Пароль не храним.
- **Последующие обновления:** `fetch()` → `headless=True`, контекст с `storage_state`,
  `ru-RU`, перехват JSON-ответов (`page.on("response")` + `json_url_keywords`),
  мягкая навигация в раздел оценок и выбор периода (`grades_nav_texts`,
  `period_aliases`), короткий `networkidle`. Редирект на ЕСИА → `SessionExpired`.
- Парсинг: JSON-ответы → HTML страницы. Если пусто — дампы `debug/page.html`,
  `debug/urls.txt`, `debug/responses/NN.json` и `ParseError` с путями.
- **Ленивый импорт** `from playwright.sync_api import sync_playwright` строго внутри
  `_start_playwright()`: модуль импортируется даже без установленного Playwright.
- Все ресурсы закрываются в `finally`; ошибки Playwright → `DiaryError` с русским текстом.
- НЕ трогать системный VPN/прокси и сетевые настройки.

## Проверка
```bash
python -X utf8 -m unittest tests.test_client -v
```
Моками покрыть: `headless=False` при входе, сохранение `storage_state`,
`headless=True` + `storage_state` при сборе, таймаут входа, отсутствие сессии,
редирект ЕСИА, успешный парсинг, дампы при пустом результате, закрытие при исключении,
импорт модуля при заблокированном `playwright`.

## Критерий готовности
Тесты зелёные без сети; после реального входа `data/storage_state.json` появляется,
браузер закрывается; повторное обновление не показывает окно браузера.
