# ЗАДАЧА 4 — открыть 2ГИС по клику на колонку «2ГИС»

**Исполнитель:** агент по `tools.pyw`.
**Файл:** `tools.pyw`, класс `LeadsPanel`.
**Статус на 2026-11-02:** ✅ работает по **двойному** клику. Одиночный клик
намеренно не привязан (см. «Почему не одиночный»).

---

## Что должно быть

Двойной клик по ячейке колонки **«2ГИС»** открывает в браузере
`https://2gis.ru/<city>/firm/<id>`.
Двойной клик по любой другой колонке — открывает первый доступный контакт
(Telegram / ВК / MAX / почта), как раньше.

---

## ШАГ 4.1 — обработчик в `_open_row_link`

Сейчас вся логика в одном методе: он получает `event`, определяет колонку и
при `gis` уходит в 2ГИС. Правильный вид:

```python
    def _open_row_link(self, event=None) -> None:
        sel = self.result_tree.selection()
        if not sel:
            return
        iid = sel[0]
        # Двойной клик по колонке «2ГИС» — открываем карточку 2ГИС
        if event is not None:
            try:
                col_id = self.result_tree.identify_column(event.x)
                col_name = self.result_tree.column(col_id, option="id") if col_id else ""
                if col_name == "gis":
                    url = self._row_urls.get(iid, "")
                    if url:
                        webbrowser.open(url)
                    return
            except Exception:
                pass
        links = self._row_links.get(iid, [])
        if links:
            webbrowser.open(links[0][1])
        elif self._row_urls.get(iid):
            webbrowser.open(self._row_urls[iid])
```

Важно: `identify_column` возвращает `#1`, `#2`, ... — сравнивать нужно с
`self.result_tree.column(col_id, option="id")`, а не с индексом вручную.
Порядок колонок в таблице: `name, topic, contact, gis` → «2ГИС» это `#4`,
но опираться на номер нельзя: колонки могут поменять местами.

---

## ШАГ 4.2 — привязка события

В `_build`, где вешаются обработчики таблицы:

```python
        self.result_tree.bind("<Double-1>", self._open_row_link)
        self.result_tree.bind("<Button-3>", self._row_menu)
```

`<Button-3>` — правый клик с меню, там есть пункт «Открыть карточку в 2ГИС»
(`_open_2gis`). Это дублирующий, но полезный путь: работает и по строке целиком.

---

## Почему не одиночный клик

ТЗ допускало `<ButtonRelease-1>`. Я этого не делал, и вот причина: одиночный
клик срабатывает **на каждое** выделение строки. При двойном клике прилетят
оба события — браузер откроет две вкладки. Плюс невозможно просто выделить
строку в колонке «2ГИС», не запустив браузер.

Если всё же нужен одиночный — минимальная защита от двойного срабатывания:

```python
    def _on_single_click(self, event) -> None:
        """Одиночный клик по колонке «2ГИС» открывает карточку (без двойного открытия)."""
        col_id = self.result_tree.identify_column(event.x)
        try:
            if self.result_tree.column(col_id, option="id") != "gis":
                return
        except Exception:
            return
        iid = self.result_tree.identify_row(event.y)
        if not iid:
            return
        now = time.monotonic()
        if now - getattr(self, "_last_gis_click", 0) < 0.4:
            return
        self._last_gis_click = now
        url = self._row_urls.get(iid, "")
        if url:
            webbrowser.open(url)
```

и `self.result_tree.bind("<ButtonRelease-1>", self._on_single_click)`.
`time` в `tools.pyw` уже импортирован.

---

## Проверка

```bat
python -m py_compile tools.pyw
python -X utf8 tests/test_leads_pool.py
```

Глазами:
1. Открыть «Лиды 2GIS».
2. Двойной клик по ячейке «2ГИС» → открылась карточка организации.
3. Двойной клик по «Контакты» → открылся Telegram/ВК/почта.
4. Двойной клик по строке без ссылки (`card` пуст) → ничего не падает.

---

## Критерий готовности

- Клик по «2ГИС» открывает именно карточку, а не контакт.
- Клик по другим колонкам ведёт себя как раньше.
- Одна вкладка на одно действие.
