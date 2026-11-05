# Задача 12 — объединение внутренних процессов WorkBuddy и Antigravity в одну запись

## Проблема

В каталоге программ сейчас отображаются внутренние служебные процессы:
- Для WorkBuddy: `WorkBuddyAI.exe`, `editor_sdk.exe` («Editor Sdk»), `betterleaks.exe` («Betterleaks»), `node.exe` («Node»).
- Для Antigravity: `Antigravity.exe` и `language_server.exe` («Language Server»).

Пользователь видит 4 разные программы для WorkBuddy и 2 для Antigravity, путается, какую из них выбирать, и ожидает видеть **ровно одну программу WorkBuddy**.

## Цель

1. В `apps_catalog.py` служебные процессы (`editor_sdk.exe`, `betterleaks.exe`, `language_server.exe`, а также `node.exe` внутри папок `.workbuddy-ai`) **не должны** выводиться отдельными строками в каталоге.
2. В каталоге должна быть **ровно одна запись**:
   - **WorkBuddy AI** (exe: `WorkBuddyAI.exe`)
   - **Antigravity** (exe: `Antigravity.exe`)
3. Если запущен любой из вспомогательных процессов (например, `editor_sdk.exe` или `node.exe`), главная запись в каталоге автоматически считается `running=True` («запущена»).
4. При выборе пользователем главной записи (`WorkBuddyAI.exe` или `Antigravity.exe`) функция `expand_companions()` автоматически отправляет все внутренние процессы в `app-config.json` службы ProxiFyre.
5. Для пользователя всё выглядит как одна простая программа с одной галочкой.

## Точки реализации

- `apps_catalog.py`:
  - `INTERNAL_CHILD_PROCESSES` — список процессов, которые не выводятся в каталог как самостоятельные программы.
  - В `app_catalog()` фильтровать дочерние процессы, а статус `running` переносить на родительскую программу.
- `tests/test_app_catalog.py` — тесты дедупликации и объединения.
