# Задача 05 — Один клик без терминала

**Исполнитель:** агент по лаунчерам.
**Статус:** ✅ выполнено.
**Правишь только:** `run.bat`, `run.sh`, `diary.desktop`, `requirements.txt`, `app.pyw`, `.gitignore`.

## Требования
- `run.bat` (Windows 11): ищет `py -3` (системный Python **с tkinter**), при первом
  запуске создаёт `.venv`, ставит `requirements.txt`, скачивает Chromium
  (`playwright install chromium`), ставит маркер `.venv/.diary-ready`, затем запускает
  **`.venv\Scripts\pythonw.exe app.pyw`** через `start ""` — без консоли.
  Все сообщения — латиницей (cmd/CP1251 не портит), CRLF, `errorlevel` → `goto fail`.
- `run.sh` (Fedora): shebang bash, `set -euo pipefail`, `cd` через `readlink -f "$0"`,
  та же разовая подготовка + маркер, `exec .venv/bin/python app.py "$@"`. Файл `+x`, LF.
- `diary.desktop`: `Type=Application`, `Terminal=false`,
  `Exec=/usr/bin/env bash -c 'cd "$(dirname "$(readlink -f "%k")")" && exec ./run.sh'`.
  Ярлык ставится **симлинком** в `~/.local/share/applications/`, иначе `%k` укажет
  не в папку проекта. `Categories=Education;Utility;`.
- `requirements.txt`: `customtkinter>=5.2.2`, `playwright>=1.48`, `pillow>=10.0`
  + в комментариях готовая разовая установка для Windows и Fedora
  (`python3-tkinter`, `python3-virtualenv`, `playwright install-deps`).
- `app.pyw`: `pythonw` не показывает traceback — писать ошибки старта в
  `debug/launch_error.log` и показывать `MessageBoxW`.

## Проверка
```bash
bash -n run.sh && chmod +x run.sh diary.desktop
python -m py_compile app.pyw
# Windows: cmd /c run.bat  (первый раз подготовит .venv, дальше откроет окно без консоли)
```
Проверить, что при отсутствии маркера установка повторяется (не «python.exe есть — значит готово»),
а при наличии — браузер не запускается, консоли нет.

## Критерий готовности
Двойной клик по `run.bat` / ярлык в меню Fedora открывает окно программы,
а не терминал; второй запуск мгновенный.
