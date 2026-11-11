# DESIGN.md — единый визуальный язык Apple для `тулы`

Цель: **весь проект выглядит как одно приложение**. Один источник правды — `theme/tokens.py`; всё остальное (tkinter/ttk, CustomTkinter, веб-панель) берёт цвета, шрифты и размеры оттуда.

## Правила (обязательные)

1. **HEX только в `theme/tokens.py`.** В UI-файлах — `theme.c("role")` / `var(--role)` / `theme.alias("BG")` / `apple_ctk.tint("role")`.
2. Цвет называется **ролью**, а не внешним видом: `label_secondary`, а не «серый»; `separator`, а не «рамка».
3. **Обе темы** (Light/Dark) и Auto по системной теме Windows. По умолчанию — Auto; компактный переключатель «Тема: авто/светлая/тёмная» нужен как запасной вход на Windows, где опрос реестра в RDP/сервисных сессиях может не сработать. Для тестов — `TULY_THEME=light|dark|auto`.
4. Контраст текста **≥ 4.5:1**, цель 7:1 на мелком тексте. Проверяется `theme/contrast.py`, плюс реальный обход дерева виджетов в `tests/test_tools_hub_theme.py`, `tests/test_agent_theme.py` (включая ttk-стили) и `diary-grades/tests/test_app_theme.py`.
5. **Акцент экономно**: цветная заливка только у главного действия; остальные кнопки нейтральные. Статус = цвет + текст + форма, никогда только цвет.
6. Шрифты: системный стек (SF Pro → Segoe UI Variable → Inter) и моно-стек (SF Mono → Cascadia Mono → Consolas). **Системные шрифты Apple не встраивать** (лицензия). В tkinter/CTk семейство проверяется через `tkinter.font.families()` — не запрашиваем отсутствующий шрифт.
7. Шкала: macOS 13 px по умолчанию, минимум 10 px; веса 400/500/600/700, без Light/Thin.
8. Сетка 4/8/12/16/20/24/32, радиусы: карточка 14, контрол 8, tag 6, log 10, модалка 16, pill 999. В tkinter все размеры через `px()` (DPI). **Шрифты Tk — в пунктах, `geometry` — в физических пикселях**: при масштабе Windows 150–200% окно с жёсткими `620x760` становится тесным и обрезает ряды. Масштабируем и геометрию: `SCALE = winfo_fpixels("1i") / 96`, `px(v) = round(v * SCALE)`, с clamp по размеру экрана.
9. Материалы: плоские семантические цвета + тонкие сепараторы + мягкая тень. Liquid Glass — **только на функциональном слое веб-панели** (`header`), через `backdrop-filter`, с `@supports`- и `prefers-reduced-transparency`-фолбэками. Нативный Acrylic на Windows не используем: недокументированный API ломается на Win11 22H2+/RDP.
10. Анимации 150–250 ms `ease-out`, только opacity/transform/background; `prefers-reduced-motion` → 0.
11. Иконки — самописные outline SVG (`theme/icons.svg`), stroke 1.5, `currentColor`. SF Symbols на Windows недоступны юридически.
12. Артефакты генерируются: `py -3 -m theme.generate_css` создаёт `theme/apple.css`, его точную копию для панели, `static/icons.svg` и `theme/apple_ctk.json`. Все помечены GENERATED и коммитятся; править только `theme/tokens.py`.

## Токены

| Роль HIG | Light | Dark |
|---|---|---|
| window | `#f5f5f7` | `#14161b` |
| surface | `#ffffff` | `#1c1f26` |
| surface_raised | `#e9e9ee` | `#242833` |
| sidebar | `#ececf1` | `#0f1115` |
| inset (лог/поле) | `#e8e8ed` | `#0d0f13` |
| label | `#1d1d1f` | `#e7ebf1` |
| label_secondary | `#5b5f66` | `#a1a9b8` |
| label_tertiary | `#646973` | `#8a93a2` |
| separator | `#d2d2d7` | `#2b303a` |
| accent | `#007aff` | `#0a84ff` |
| accent_fill (кнопка) | `#0062cc` | `#0a63d2` |
| selected | `#0067d6` | `#2f5c9e` |
| ok / warn / err | `#1a7f37` / `#a15c00` / `#c9252d` | `#30d158` / `#ff9f0a` / `#ff453a` |

`accent_fill` отделён от `accent`: чистый системный синий даёт белому тексту ~4.0:1, что ниже порога HIG. `ok/warn/err` — это акценты на `surface`; для **текста** на приподнятой поверхности используем «чернила» бейджей (`badge_good_ink` / `badge_warn_ink` / `badge_bad_ink`), у них запас контраста ≥ 4.5:1. Полный список ролей — `theme/tokens.py`.

## Архитектура

| Файл | Роль |
|---|---|
| `theme/tokens.py` | токены, `TYPE_SCALE`, `SPACE`, `RADIUS`, `MOTION`, `ALIASES` старых констант, `CONTRAST_PAIRS` |
| `theme/__init__.py` | `mode()`, `set_mode()`, `raw_mode()`, `c(role)`, `alias(name)`, `on_change()` |
| `theme/generate_css.py` → `apple.css` | CSS-зеркало токенов + компоненты, копия в `static/` |
| `theme/generate_ctk.py` → `apple_ctk.json` | полная тема CustomTkinter: каждая пара `[light, dark]` из токенов |
| `theme/apple_tk.py` | ttk-стили, фабрики виджетов, живая перекраска Tk-дерева, DWM-шапка |
| `theme/apple_ctk.py` | `tint()`, `configure()`, `font()`, готовые kwargs кнопок/фреймов |
| `theme/contrast.py` | проверка WCAG |
| `theme/icons.svg` | outline-иконки |

Старые имена (`BG`, `CARD`, `ACCENT`, `PANEL`, `FG`, `MUTED`…) остаются **алиасами** — правим только точки определения стиля, не тысячи упоминаний.

**CustomTkinter:** цвет задаётся парой `(light, dark)` из `tint()`. CTk хранит пару в виджете и сам перекрашивает уже созданные элементы при `set_appearance_mode()`, поэтому обход дерева для смены темы не нужен. Тема JSON задаётся ДО создания виджетов.

## Поверхности

| Поверхность | Файл | Статус |
|---|---|---|
| Главный хаб (11 вкладок) | `tools.pyw` | ✅ фаза 4 |
| Веб-панель Shorts | `shorts-autopilot/app/static/index.html` | ✅ фаза 3 |
| Нативное окно Shorts | `shorts-autopilot/app/gui.py` | ✅ фаза 5 |
| Дневник МО | `diary-grades/app.py` | ✅ фаза 6 |
| AI Screen Agent | `ai-screen-agent/screen_agent.py` | ✅ фаза 7 |
| `ai-screen-agent/app.pyw` | дубликат, не запускается ни одним `.bat` | **не трогаем** |

Консольные утилиты (`leads2gis.py`, `shutdown_watcher.py`, `winbase.py`, `apps_catalog.py`) видимого UI не имеют и стилизации не подлежат. Сборка EXE запрещена документами проекта.

## Контракт, который нельзя ломать

`shorts-autopilot/tests/check_panel_ui.py` фиксирует ~60 DOM-id (`botUrl` … `toast`), проверяет отсутствие console/pageerror и неизменность `config.json` при «Сохранить». Редизайн панели меняет только классы и CSS.

## Проверка

```bash
py -3 -m theme.generate_css                     # CSS + CTk JSON, два прогона → одинаковые файлы
py -3 theme/contrast.py                         # OK: все пары >= 4.5:1
py -3 -m unittest discover -s tests -p "test_*.py" -q
shorts-autopilot/run_tests.bat                  # 958 тестов
diary-grades\.venv\Scripts\python.exe -X utf8 -m unittest discover -s diary-grades\tests -v
py -3 -X utf8 -m unittest discover -s tests -p "test_agent_theme.py" -q   # окно AI Screen Agent
py -3 -X utf8 -m unittest discover -s ai-screen-agent -p "test_*.py" -q   # 65 тестов агента
python tests/check_panel_ui.py --click          # при запущенной панели
TULY_THEME=light diary-grades\.venv\Scripts\python.exe -X utf8 diary-grades\app.py --smoke-shot debug/theme-after/diary-light.png
```

Скриншоты «до/после»: `debug/theme-after/` — веб-панель (`panel-*.png`), окно дневника (`diary-*.png`) и AI Screen Agent (`agent-before.png`, `agent-dark.png`, `agent-light.png`), обе темы. У агента снимки сделаны `PrintWindow(PW_RENDERFULLCONTENT)`: `ImageGrab` в этой сессии даёт чёрный кадр, а само окно скрыто из захвата через `WDA_EXCLUDEFROMCAPTURE`.

`tests/test_agent_theme.py` лежит в корневом `tests/`, а не рядом с агентом: `ai-screen-agent/test_agent_speed.py` проверяет чистоту `sys.modules` и падает, если в том же прогоне импортировался `tkinter`.

## Референсы (Apple HIG)

- [Color](https://developer.apple.com/design/human-interface-guidelines/color) — семантические системные цвета, light/dark/increased-contrast варианты
- [Dark Mode](https://developer.apple.com/design/human-interface-guidelines/dark-mode) — Auto, 4.5:1 / цель 7:1
- [Typography](https://developer.apple.com/design/human-interface-guidelines/typography) — SF Pro/SF Mono, 13 pt / 10 pt
- [Materials](https://developer.apple.com/design/human-interface-guidelines/materials) — Liquid Glass только на функциональном слое, dimming 35 %
- [Layout](https://developer.apple.com/design/human-interface-guidelines/layout) — иерархия, негативное пространство
- [Designing for macOS](https://developer.apple.com/design/human-interface-guidelines/designing-for-macos) — тулбар/сайдбар, меньше модалок
- [SF Symbols](https://developer.apple.com/design/human-interface-guidelines/sf-symbols) — 3 scale / 9 весов / outline для тулбаров

**Честно:** это Windows, не macOS. Делаем визуальный язык Apple, а не байт-в-байт копию: `NSColor`, `NSVisualEffectView`, SF Pro и SF Symbols здесь недоступны.
