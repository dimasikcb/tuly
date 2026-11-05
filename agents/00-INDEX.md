# agents/00-INDEX.md — карта задач и агентов

Каждый файл в этой папке — **самодостаточное задание для одного ИИ-агента**.
Его можно открыть и отдать любому агенту целиком, без остального контекста.

Правило №0: **один агент — один файл кода.** Два агента не правят один файл
одновременно, иначе правки затрут друг друга.

---

## Задачи

| № | Файл задания | Кто правит | Статус |
|---|--------------|-----------|--------|
| 1 | `task-1-topic-card.md` | `leads_mail.py` | ✅ выполнено (35/35 записей с темой и ссылкой) |
| 2 | `task-2-smart-search.md` | `leads_mail.py` + `tools.pyw` | ✅ выполнено |
| 3 | `task-3-fill-topic-button.md` | `tools.pyw` | ✅ выполнено |
| 4 | `task-4-open-2gis-click.md` | `tools.pyw` | ⚠️ работает по двойному клику |
| 5 | `task-5-old-links.md` | данные + `leads_mail.py` | ✅ выполнено |
| 6 | `task-6-tests.md` | `tests/test_leads_pool.py` | ✅ 40/40 ok |
| 7 | `task-7-bypass-routing.md` | `bypass_core.py` + `tools.pyw` | ✅ подхват процессов ProxiFyre |
| 8 | `task-8-traffic-isolation.md` | `bypass_core.py` | ✅ выполнено (отключение ProxyEnable и TUN) |
| 9 | `task-9-app-connection-reset.md` | `bypass_core.py` + `tools.pyw` | ✅ выполнено (чистый сброс и перезапуск правил) |
| 10 | `task-10-auto-heal-and-diagnostics.md` | `bypass_core.py` + `tools.pyw` | ✅ выполнено (самодиагностика утечек и кнопка «⚡ Выключить TUN») |
| 11 | `task-11-leads-interaction.md` | `tools.pyw` | ✅ выполнено (одиночный клик 2ГИС в LeadsPanel) |
| 12 | `task-12-bundle-apps-catalog.md` | `apps_catalog.py` | ✅ выполнено (WorkBuddy AI как 1 программа вместо 4) |
| 13 | `task-13-live-ip-and-verification.md` | `bypass_core.py` + `tools.pyw` | ✅ выполнено (кнопка «🌐 Проверить IP» в панели) |
| 14 | `task-14-clean-disconnect-flush.md` | `bypass_core.py` | ✅ выполнено (гарантированный kill ядра и flushdns) |
| 15 | `task-15-youtube-leak-on-disable.md` | `bypass_core.py` + `tools.pyw` + `tests/` | ✅ выполнено (устранение утечек, изоляция трафика, сброс ProxyEnable) |

---

## Волны параллельного запуска агентов

```
Волна A (Каталог и объединение процессов):
  Агент A → task-12 (apps_catalog.py) [ВЫПОЛНЕНО] — WorkBuddy объединён в 1 программу

Волна B (Сброс соединений и остановка ядра):
  Агент B → task-14 (bypass_core.py) [ВЫПОЛНЕНО] — сброс DNS и жесткий kill при отключении

Волна C (Проверка внешнего IP и отображение):
  Агент C → task-13 (bypass_core.py + tools.pyw) [ВЫПОЛНЕНО] — кнопка «🌐 Проверить IP» в тулбаре
```

---

## Общий контекст (нужен всем)

**Проект:** Python 3.10+ / Tkinter. Одно окно `tools.pyw`.
Разделы:
- «Лиды 2ГИС»: сбор лидов, дедупликация, карточки, рассылка.
- «Bypass»: обход DPI через GoodbyeDPI и выборочный обход по программам через Happ (SOCKS) + ProxiFyre.

**Файлы, которые можно менять:**

| Файл | Роль |
|------|------|
| `apps_catalog.py` | каталог программ, объединение связанных процессов, поиск |
| `bypass_core.py` | маршрутизация, остановка служб, проверка IP, flush DNS |
| `tools.pyw` | GUI + плашка внешнего IP |
| `tests/` | модульные тесты |
