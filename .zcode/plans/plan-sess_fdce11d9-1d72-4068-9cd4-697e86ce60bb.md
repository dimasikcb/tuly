# Разрезка tools.pyw (8295 строк) на панели-модули

## Context
Правило №0 «один агент — один файл» существует из-за монолита `tools.pyw`: два агента не могут параллельно править хаб, поэтому любой шаг упирается в один файл. Цель — вынести панели и «движки» в отдельные модули, сократив `tools.pyw` до Hub (~700–800 строк), чтобы рой работал параллельно без конфликтов. Все 188 тестов остаются зелёными; заодно чинится найденный баг `SettingsPanel` (NameError на `scroll_settings.save_settings`).

## Целевая структура (плоские модули в корне, стиль проекта)
- **`uibase.py`** (~600) — общий слой: `Panel` (база), `DarkMessageBox`/`messagebox`-синглтон, палитра-токены (`_LEGACY_NAMES/_EXTRA_ROLES/_refresh_palette` + реестр «потребителей» палитры — механизм `globals().update` централизуется, панели продолжают читать голые имена токенов), `tc/ui_font/mono_font/px/badge_colors/flat_button/...`, `enable_input_paste`, `SCALE`.
- **Движки без GUI** (запрет tkinter сохранится для test_arch): `github_core.py` (~570, gh_*+скан репо+recycle), `dns_core.py` (~130), `cleanup_core.py` (~110, сканер+Plan/DeleteResult), `clicker_core.py` (~80, ленивые депы cv2/pyautogui+grab/find_template/Step).
- **Константы — модулям-хозяевам**: `LEADS_DIR/POOL_FILE` → `leads_pool.py`; `MAIL_SETTINGS_PATH/MAIL_STATE_PATH/_mail_settings` → `leads_mail.py`; `leads_collect` → `leads2gis.py`; `relaunch_as_admin/pythonw_executable` → `winbase.py`.
- **Панели** (GUI): `autoclicker_panel.py` (+OverlaySelector), `clean_panel.py`, `dns_panel.py`, `github_panel.py`, `bypass_panel.py` (+Switch/Segmented/AppRow/ScrollList), `leads_panel.py` (+MailWindow), `shorts_panel.py`, `diary_panel.py` (+мостки `_DIARY_*`), `aiscreen_panel.py`, `watcher_panel.py`, `settings_panel.py`.
- **`tools.pyw`** остаётся: импорты, реэкспорты совместимости для тестов, `NAV`, `HEADERS`, `NavItem`, `Hub` (+литерал `self.factories`), `main`, `__main__`-guard. Панели получают hub только через 4 метода (`run_bg/set_status/toast/post`) — контракт FakeHub сохраняется.

## Ключевые решения
1. Патчи тестов переносятся на модули-хозяева: панели делают `import bypass_core` и зовут `bypass_core.bypass_status(...)` в момент вызова; тесты патчат `bypass_core.X` вместо `app.X` (правило «импортируй из хозяина» из README). `messagebox`-синглтон общий объект → патчи `askyesno` переживут переезд без правок.
2. Панели читают константы путей через модуль-хозяина (`leads_pool.POOL_FILE`), тесты патчат хозяина.
3. Запрещён `import tools` из панелей (мост `tools.py` запустит приложение в тестах).
4. Пакет `panels/` НЕ используем — тесты сканируют только корневые файлы (hex-запрет, AST), плоские модули остаются под проверкой.

## Шаги
0. Backup-коммит текущего состояния (`backup: tuly`), по конвенции репо.
1. **Каркас (я, вручную)**: `uibase.py` + реестр палитры; `tools.pyw` регистрирует свои globals потребителем (тест `tools.BG` после смены темы продолжит работать); прогоны theme-тестов.
2. **Волна B — рой ×4 параллельно (каждый владеет только своими новыми файлами)**: (1) `github_core`, (2) `dns_core`+`cleanup_core`+фикс settings-импорта, (3) `clicker_core`+переезд helpers в `winbase`, (4) константы лидов в хозяев+`_mail_settings`+`leads_collect`. `tools.pyw` держит реэкспорты — старые панели работают.
3. **Волна C — рой ×7 параллельно, домен = панель + её тесты**: leads (+step3/step4/niche_ui правки патчей), bypass (+test_bypass_audit AST на новый файл, +test_ui_bypass), github, autoclicker, diary (+test_diary_tab: класс и мостки ищутся в diary_panel.py, NAV/HEADERS/factories остаются в tools.pyw), dns+clean+settings, shorts+aiscreen+watcher.
4. **Интеграция (я, последовательно)**: по мере готовности домена — удаление блока из `tools.pyw`, фабрика из импорта, прогон тестов домена. Конфликтов нет: tools.pyw правит только я.
5. **Документация (агент)**: README (карта файлов, обновление раздела архитектуры), `agents/00-INDEX.md` (новое Правило №0: «один агент — один модуль; tools.pyw — только Hub», чистка дубля №13 и опечаток), правка списков в `test_arch.py` под новые модули.
6. Финал: полный прогон тестов.

## Проверка
- `py -3 -m unittest discover -s tests -v` — все 188+ тестов зелёные.
- `python -X utf8 tests/test_leads_parse.py` и `tests/test_leads_pool.py` (самописные раннеры).
- После каждой интеграции — тесты домена (theme/bypass/leads/diary).
- Смоук: пользователь запускает `tools.pyw`, кликает по вкладкам (тема, лиды, bypass).
- EXE не собираем (запрещено правилами); VPN/туннель не трогаем.