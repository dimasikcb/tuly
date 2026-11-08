# agents/00-INDEX.md — карта агентов проекта «Дневник МО · Контроль балла»

Каждый `task-*.md` — **самодостаточное задание для одного ИИ-агента**: открывай
файл и отдавай целиком, без остального контекста.

**Правило №0: один агент — один файл кода.** Два агента не правят один файл
одновременно: каждый читает файл заново и пишет целиком, вторая правка молча
затирает первую. Проверять результат чтением, а не сообщением инструмента.

**Общий контекст (нужен всем):** проект `diary-grades/`, Python 3.10+,
Windows 11 + Fedora Linux, GUI CustomTkinter (тёмная тема), браузер Playwright.
Портал: `https://authedu.mosreg.ru`, вход через Госуслуги (ЕСИА) вручную.
Слои: `models` ← `config` ← `grade_math` / `parsers` ← `diary_client` ← `app`.
Нижний слой не импортирует верхний; в парсеры и математику — никакого GUI/сети.

---

## Задачи

| № | Файл задания | Файл кода | Статус |
|---|--------------|-----------|--------|
| 1 | `task-01-math.md` | `grade_math.py` | ✅ 34 теста, перебор всех S,N до N=30 |
| 2 | `task-02-parsers.md` | `parsers.py` | ✅ 32 теста, JSON + HTML, только stdlib |
| 3 | `task-03-playwright.md` | `diary_client.py` | ✅ 14 тестов на моках, ленивый импорт Playwright |
| 4 | `task-04-gui.md` | `app.py` | ✅ тёмный UI, 5 колонок, бейджи, фильтр, потоки |
| 5 | `task-05-launchers.md` | `run.bat`, `run.sh`, `diary.desktop`, `requirements.txt`, `app.pyw` | ✅ один клик, без терминала |
| 6 | `task-06-tests.md` | `tests/` | ✅ 94 офлайн-теста |
| 7 | `task-07-docs.md` | `README.md`, `selectors.json` | ✅ инструкция и калибровка |
| 8 | `task-08-live-calibration.md` | `selectors.json` (+ при необходимости `parsers.py`) | ⏳ ждёт живой сессии дневника |
| 9 | `task-09-math-rounding-weights.md` | `grade_math.py`, `models.py`, `parsers.py` | ✅ 113 тестов, математическое округление ROUND_HALF_UP (4.13) и веса (5₂) |
| 10 | `task-10-robustness-audit.md` | `models.py`, `grade_math.py`, `parsers.py`, `diary_client.py`, `app.py`, `test_robustness.py` | ✅ 141 тест, стресс-аудит, краевые случаи, official_average, сессия ЕСИА |

---

## Волны параллельного запуска

```
Волна A (ядро, можно одновременно — файлы не пересекаются):
  Агент A → task-01 (grade_math.py)
  Агент B → task-02 (parsers.py)
  Агент C → task-06 (tests/) — только после A и B

Волна B (браузер и окно, после A+B):
  Агент D → task-03 (diary_client.py)
  Агент E → task-04 (app.py)            [D и E могут идти параллельно: разные файлы]

Волна C (упаковка и документация):
  Агент F → task-05 (лаунчеры)
  Агент G → task-07 (README/selectors.json)

Волна D (после реального входа в дневник):
  Агент H → task-08 (калибровка селекторов по dump'ам из debug/)
```

---

## Как проверять любую задачу

```bash
cd diary-grades
python -m py_compile *.py app.pyw
python -X utf8 -m unittest discover -s tests -v      # ожидается 94 ok
python app.py --smoke-shot debug/gui.png              # реальный запуск окна
```

Тесты не ходят в сеть и не открывают браузер. Живой сайт без сессии не проверяем.
