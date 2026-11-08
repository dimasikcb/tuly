"""Парсеры оценок электронного дневника Московской области.

Нижний слой приложения: только стандартная библиотека, без сети, без GUI и без
зависимостей от браузерной автоматизации. Модуль безопасно импортируется даже
если Playwright не установлен.

Поддерживаются два источника данных:
  * JSON-ответы SPA (произвольная вложенность, разные формы оценок);
  * HTML: таблицы и div-карточки с data-атрибутами.

Результат всегда нормализуется через :func:`validate_subjects` перед тем, как
попасть в математику среднего балла.
"""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable

from config import DEBUG_DIR, ensure_dirs, load_selectors
from grade_math import validate_marks
from models import Mark, ParseError, SubjectGrades, clean_name, normalize_weight

__all__ = [
    "parse_grades_payload",
    "parse_grades_html",
    "parse_json_responses",
    "parse_marks_api",
    "merge_subjects",
    "parse_file",
    "validate_subjects",
    "save_debug_dump",
]

# Значения по умолчанию дублируют selectors.json: парсер остаётся рабочим, даже
# если файл настроек отсутствует или повреждён.
_FALLBACK_SELECTORS: dict = {
    "json_subject_keys": [
        "subject", "subject_name", "subjectName", "discipline", "disciplineName",
        "lesson", "lessonName", "name", "title", "предмет", "название",
    ],
    "json_marks_keys": [
        "marks", "grades", "scores", "estimates", "markValues", "values",
        "results", "оценки", "баллы",
    ],
    "json_mark_keys": [
        "mark", "grade", "score", "value", "estimate", "result", "point",
        "балл", "оценка",
    ],
    "json_url_keywords": [
        "mark", "grade", "score", "diary", "journal", "progress", "subject",
        "lesson", "period", "estimate", "performance",
    ],
    "subject_row_selectors": [
        "tr[data-subject]", "tr[data-discipline]", "[class*='subject-row']",
        "[class*='discipline']", "table tbody tr", "[role='row']",
    ],
    "mark_selectors": [
        "[class*='mark']", "[class*='grade']", "[class*='score']",
        "[data-mark]", "[data-grade]", "td", "[role='cell']",
    ],
    "skip_subject_words": [
        "итого", "средний", "всего", "предмет", "период", "итоговый", "оценки",
    ],
}

# Ключи, специфичные для предмета, проверяем раньше общих name/title: так имя
# ученика или заголовок страницы с меньшей вероятностью станет «предметом».
_SUBJECT_PRIORITY = (
    "subject", "subjectname", "discipline", "disciplinename",
    "lesson", "lessonname", "предмет",
)
_GENERIC_SUBJECT_KEYS = ("name", "title", "название")

# Ключи-метаданные внутри контейнера оценок: даты, идентификаторы, средние,
# счётчики. Их целые значения не должны превращаться в оценки.
_META_KEYS = frozenset({
    "date", "dates", "day", "month", "year", "time", "timestamp", "datetime",
    "created", "updated", "id", "ids", "guid", "uuid", "period", "periods",
    "type", "status", "weight", "average", "avg", "mean", "count", "total",
    "sum", "max", "min", "number", "num", "index", "order", "page", "limit",
    "offset", "comment", "note", "description", "lessonid", "subjectid",
})

_MARK_ATTRS = ("data-mark", "data-grade", "data-score")
_SUBJECT_ATTRS = ("data-subject", "data-discipline", "data-name")

_INT_RE = re.compile(r"[+-]?\d+")
_MAX_WALK_DEPTH = 60
_VOID_TAGS = frozenset({
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link",
    "meta", "param", "source", "track", "wbr",
})


# --------------------------------------------------------------------------- #
# Общие утилиты
# --------------------------------------------------------------------------- #

def _norm(key: str) -> str:
    """Приводит ключ к нижнему регистру без разделителей (camelCase → flat)."""
    return re.sub(r"[^0-9a-zа-яё]+", "", str(key).casefold())


def _as_key_set(values: Any) -> frozenset[str]:
    """Нормализует список ключей из selectors.json во множество."""
    if not isinstance(values, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(_norm(v) for v in values if v is not None)


def _skip_words(cfg: dict) -> list[str]:
    """Возвращает список стоп-слов для названий предметов."""
    raw = cfg.get("skip_subject_words")
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return []
    return [str(w).casefold() for w in raw if w]


def _selectors(selectors: dict | None) -> dict:
    """Сливает настройки по умолчанию, selectors.json и явный аргумент."""
    cfg = dict(_FALLBACK_SELECTORS)
    cfg.update(load_selectors() or {})
    if selectors:
        cfg.update(selectors)
    return cfg


_SUBSCRIPTS_TRANS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")


def _as_mark_and_weight(value: Any) -> tuple[int, int] | None:
    """Извлекает пару (оценка 1..5, вес >= 1) или None."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, Mark):
        val = int(value)
        return (val, value.weight) if 1 <= val <= 5 else None
    if isinstance(value, dict):
        m = _as_mark(value.get("value") if "value" in value else value.get("mark"))
        if m is not None:
            w = 1
            for wk in ("weight", "coef", "coefficient", "вес"):
                if wk in value and value[wk] is not None:
                    w = normalize_weight(value[wk])
                    break
            return (m, w)
        return None
    if isinstance(value, int):
        return (value, 1) if 1 <= value <= 5 else None
    if isinstance(value, float):
        return (int(value), 1) if value.is_integer() and 1 <= value <= 5 else None
    if isinstance(value, str):
        text = value.strip()
        # "5₂" с подстрочным индексом
        m = re.fullmatch(r"([1-5])\s*([₀-₉]+)", text)
        if m:
            val = int(m.group(1))
            sub = int(m.group(2).translate(_SUBSCRIPTS_TRANS))
            return (val, max(1, sub))
        # "5(2)" в скобках
        m2 = re.fullmatch(r"([1-5])\s*\(([1-9]\d*)\)", text)
        if m2:
            return (int(m2.group(1)), max(1, int(m2.group(2))))
        if _INT_RE.fullmatch(text):
            number = int(text)
            return (number, 1) if 1 <= number <= 5 else None
    return None


def _as_mark(value: Any) -> int | None:
    """Возвращает оценку 1..5 или None. Дроби, даты и мусор отбрасываются."""
    res = _as_mark_and_weight(value)
    return res[0] if res is not None else None


def _valid_subject(name: Any, skip_words: Iterable[str]) -> bool:
    """Проверяет, что строка похожа на название предмета, а не на заголовок."""
    cleaned = clean_name(name)
    if len(cleaned) < 2:
        return False
    lowered = cleaned.casefold()
    if any(word in lowered for word in skip_words):
        return False
    # Должна быть хотя бы одна буква: «05.11», «4,50», «10» — не предметы.
    return re.search(r"[^\W\d_]", cleaned) is not None


def _collect_marks(value: Any, mark_keys: frozenset[str]) -> list[Mark]:
    """Рекурсивно достаёт оценки из списка, словаря или скаляра."""
    out: list[Mark] = []
    if isinstance(value, list):
        for item in value:
            out.extend(_collect_marks(item, mark_keys))
        return out
    if isinstance(value, dict):
        has_mark_key = any(_norm(str(k)) in mark_keys for k in value)
        mw = _as_mark_and_weight(value)
        if mw is not None:
            out.append(Mark(mw[0], weight=mw[1]))
            return out
        for key, item in value.items():
            nk = _norm(str(key))
            if nk in _META_KEYS and nk not in mark_keys:
                continue
            if nk in mark_keys or not has_mark_key:
                mw_item = _as_mark_and_weight(item)
                if mw_item is not None:
                    out.append(Mark(mw_item[0], weight=mw_item[1]))
                elif isinstance(item, (dict, list)):
                    out.extend(_collect_marks(item, mark_keys))
            elif isinstance(item, (dict, list)):
                out.extend(_collect_marks(item, mark_keys))
        return out
    mw = _as_mark_and_weight(value)
    if mw is not None:
        out.append(Mark(mw[0], weight=mw[1]))
    return out


def _extract_name(node: dict, subject_keys: frozenset[str], depth: int = 0) -> str | None:
    """Ищет название предмета в узле, включая вложенный объект subject."""
    if depth > 4:
        return None
    norm_map = {_norm(str(k)): v for k, v in node.items()}
    priority = [k for k in _SUBJECT_PRIORITY if k in subject_keys]
    generic = [k for k in _GENERIC_SUBJECT_KEYS if k in subject_keys]
    rest = sorted(subject_keys - set(priority) - set(generic))
    for key in (*priority, *rest, *generic):
        if key not in norm_map:
            continue
        value = norm_map[key]
        if isinstance(value, str):
            if value.strip():
                return value
        elif isinstance(value, dict):
            nested = _extract_name(value, subject_keys, depth + 1)
            if nested:
                return nested
    return None


def _extract_marks(node: dict, marks_keys: frozenset[str],
                   mark_keys: frozenset[str]) -> list[Mark]:
    """Собирает оценки из ключей-контейнеров и одиночных ключей узла."""
    out: list[Mark] = []
    for key, value in node.items():
        nk = _norm(str(key))
        if nk in marks_keys:
            out.extend(_collect_marks(value, mark_keys))
        elif nk in mark_keys:
            mw = _as_mark_and_weight(value)
            if mw is not None:
                # Проверим, нет ли веса на уровне родительского узла node
                w = mw[1]
                if w == 1:
                    for wk in ("weight", "coef", "coefficient", "вес"):
                        if wk in node and node[wk] is not None:
                            w = normalize_weight(node[wk])
                            break
                out.append(Mark(mw[0], weight=w))
            elif isinstance(value, (dict, list)):
                out.extend(_collect_marks(value, mark_keys))
    if not out:
        # Оценки иногда лежат во вложенном контейнере:
        # {"subject": "Химия", "data": {"grades": [5, 4]}}
        for value in node.values():
            candidates = value if isinstance(value, list) else [value]
            for item in candidates:
                if isinstance(item, dict) and any(
                    _norm(str(k)) in marks_keys or _norm(str(k)) in mark_keys
                    for k in item
                ):
                    out.extend(_extract_marks(item, marks_keys, mark_keys))
    return out


_AVG_KEYS = frozenset({
    "average", "avg", "mean", "средний", "среднийбалл",
    "averagemark", "avgmark", "averagescore", "avgscore",
    "middlemark", "officialaverage", "rating",
})


def _extract_official_average(node: dict) -> str:
    """Извлекает официальный средний балл из JSON-узла, если он есть."""
    if not isinstance(node, dict):
        return ""
    for key, value in node.items():
        if value is None:
            continue
        nk = _norm(str(key))
        if nk in _AVG_KEYS:
            if isinstance(value, (int, float)):
                if 1 <= value <= 5:
                    return f"{float(value):.2f}"
            elif isinstance(value, str):
                val = value.strip().replace(",", ".")
                m = re.search(r"\b([1-5](?:\.\d+)?)\b", val)
                if m:
                    try:
                        num = float(m.group(1))
                        if 1 <= num <= 5:
                            parts = m.group(1).split(".")
                            if len(parts) == 2 and len(parts[1]) == 2:
                                return m.group(1)
                            return f"{num:.2f}"
                    except ValueError:
                        pass
            elif isinstance(value, dict):
                res = _extract_official_average(value)
                if res:
                    return res
    for subkey in ("statistics", "summary", "data", "stats", "result"):
        if subkey in node and isinstance(node[subkey], dict):
            res = _extract_official_average(node[subkey])
            if res:
                return res
    return ""


def _walk(node: Any, keys: dict, records: list[SubjectGrades], depth: int = 0) -> None:
    """Обходит JSON и накапливает узлы «название + оценки»."""
    if depth > _MAX_WALK_DEPTH:
        return
    if isinstance(node, dict):
        name = _extract_name(node, keys["subject"])
        if name and _valid_subject(name, keys["skip"]):
            marks = _extract_marks(node, keys["marks"], keys["mark"])
            if marks:
                records.append(
                    SubjectGrades(clean_name(name), tuple(marks), source="json",
                                  official_average=_extract_official_average(node))
                )
        for value in node.values():
            if isinstance(value, (dict, list)):
                _walk(value, keys, records, depth + 1)
    elif isinstance(node, list):
        for item in node:
            _walk(item, keys, records, depth + 1)


def _parse_json_document(data: Any, cfg: dict) -> list[SubjectGrades]:
    """Разбирает JSON-документ: либо массив ответов, либо произвольный payload."""
    if isinstance(data, list) and data and all(
        isinstance(item, dict) and "payload" in item for item in data
    ):
        pairs = [(str(item.get("url", "")), item.get("payload")) for item in data]
        return parse_json_responses(pairs, cfg)
    return parse_grades_payload(data, cfg)


# --------------------------------------------------------------------------- #
# Публичные функции
# --------------------------------------------------------------------------- #

def parse_grades_payload(payload: Any,
                         selectors: dict | None = None) -> list[SubjectGrades]:
    """Рекурсивно разбирает произвольный JSON-ответ SPA дневника.

    Находит узлы с названием предмета (ключи ``json_subject_keys``) и оценками
    (``json_marks_keys`` / ``json_mark_keys``). Понимает числа, строки, списки,
    списки словарей ``{"mark": 5, "date": ...}`` и словари со вложенными
    значениями. Средние баллы, даты, строки «н/а» и числа вне 1..5 отбрасываются.
    Предметы дедуплицируются по нормализованному имени с объединением оценок.
    """
    cfg = _selectors(selectors)
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            return []
    if not isinstance(payload, (dict, list)):
        return []
    keys = {
        "subject": _as_key_set(cfg.get("json_subject_keys")),
        "marks": _as_key_set(cfg.get("json_marks_keys")),
        "mark": _as_key_set(cfg.get("json_mark_keys")),
        "skip": _skip_words(cfg),
    }
    records: list[SubjectGrades] = []
    _walk(payload, keys, records)
    return validate_subjects(merge_subjects(*[[r] for r in records]))


def _extract_row_average(row: _Element) -> str:
    """Извлекает готовый средний балл из строки/карточки HTML, если он там есть."""
    # 1. Проверяем data-атрибуты на строке и её потомках
    for attr in ("data-average", "data-avg", "data-mean"):
        if attr in row.attrs:
            val = str(row.attrs[attr]).strip().replace(",", ".")
            m = re.search(r"\b([1-5](?:\.\d{1,2})?)\b", val)
            if m:
                return m.group(1)

    # 2. Проверяем элементы с подходящим class или data-атрибутами
    for element in _iter_elements(row):
        for attr in ("data-average", "data-avg", "data-mean"):
            if attr in element.attrs:
                val = str(element.attrs[attr]).strip().replace(",", ".")
                m = re.search(r"\b([1-5](?:\.\d{1,2})?)\b", val)
                if m:
                    return m.group(1)
        css = element.attrs.get("class", "").lower()
        if any(w in css for w in ("average", "mean", "средн", "avg", "rating")):
            text = element.text().strip().replace(",", ".")
            m = re.search(r"\b([1-5]\.\d{1,2})\b", text)
            if m:
                return m.group(1)

    # 3. Табличная вёрстка: ячейка с дробным баллом (например, 4,50 или 4.13),
    # если класс не указывает на дату/время/оценку.
    for element in _iter_elements(row):
        if element.tag in ("td", "th") or element.attrs.get("role") == "cell":
            css = element.attrs.get("class", "").lower()
            if any(w in css for w in ("date", "day", "time", "id", "num", "lesson", "mark", "grade", "score")):
                continue
            text = element.text().strip().replace(",", ".")
            if re.fullmatch(r"[1-5]\.\d{1,2}", text):
                return text
    return ""


def parse_grades_html(html: str, selectors: dict | None = None) -> list[SubjectGrades]:
    """Разбирает HTML дневника: таблицы и div-карточки с data-атрибутами.

    В таблице первая осмысленная ячейка строки — предмет, остальные ячейки с
    точным целым 1..5 — оценки. Дробные средние («4,50», «4.5»), даты и числа
    6..10 оценками не считаются. В карточках приоритет у ``data-subject``,
    ``data-mark`` / ``data-grade``; классы с mark/grade/score тоже считаются
    ячейками оценки. Строки без названия или без хотя бы одной оценки отбрасываются.
    """
    cfg = _selectors(selectors)
    text = str(html or "")
    if not text.strip():
        return []
    builder = _TreeBuilder()
    try:
        builder.feed(text)
        builder.close()
    except Exception:  # битый HTML не должен ронять импорт
        pass

    row_attrs = _row_subject_attrs(cfg)
    class_kws = _mark_class_keywords(cfg)
    skip = _skip_words(cfg)

    rows = [el for el in _iter_elements(builder.root) if _is_row(el, row_attrs)]
    row_ids = {id(row) for row in rows}
    # Если строка вложена в другую строку, берём самую внутреннюю.
    rows = [row for row in rows if not _has_row_descendant(row, row_ids)]

    records: list[SubjectGrades] = []
    for row in rows:
        name = _row_name(row, row_attrs, class_kws, skip)
        if not name:
            continue
        marks = _collect_row_marks(row, class_kws)
        if not marks:
            continue
        official_avg = _extract_row_average(row)
        records.append(SubjectGrades(name, tuple(marks), source="html",
                                     official_average=official_avg))
    return validate_subjects(merge_subjects(*[[r] for r in records]))


def parse_json_responses(responses: Iterable[tuple[str, Any]],
                         selectors: dict | None = None) -> list[SubjectGrades]:
    """Объединяет оценки из набора пар ``(url, payload)``.

    URL мягко фильтруются по ``json_url_keywords``: пустой список ключевых слов
    означает «брать все», а несовпадение URL не отбрасывает ответ без проверки —
    содержимое сначала всё равно пытаемся распарсить.
    """
    cfg = _selectors(selectors)
    keywords = [str(k).casefold() for k in (cfg.get("json_url_keywords") or []) if k]
    groups: list[list[SubjectGrades]] = []
    for item in responses or ():
        if not isinstance(item, (tuple, list)) or len(item) < 2:
            continue
        url, payload = item[0], item[1]
        parsed = parse_grades_payload(payload, cfg)
        if parsed:
            groups.append(parsed)
            continue
        if keywords and not any(k in str(url or "").casefold() for k in keywords):
            # URL не похож на ручку оценок И содержимое пустое — мягко пропускаем.
            continue
    return validate_subjects(merge_subjects(*groups))


def merge_subjects(*groups: Iterable[SubjectGrades]) -> list[SubjectGrades]:
    """Сливает группы предметов по имени, сохраняя порядок первых появлений."""
    merged: dict[str, dict] = {}
    for group in groups:
        if isinstance(group, SubjectGrades):
            group = [group]
        for subject in group or ():
            name = clean_name(subject.name)
            if not name:
                continue
            key = name.casefold()
            if key not in merged:
                merged[key] = {
                    "name": name,
                    "marks": [],
                    "period": subject.period,
                    "source": subject.source,
                    "official_average": subject.official_average,
                }
            merged[key]["marks"].extend(subject.marks)
            if subject.official_average and not merged[key]["official_average"]:
                merged[key]["official_average"] = subject.official_average
    return [
        SubjectGrades(data["name"], tuple(data["marks"]),
                      data["period"], data["source"],
                      official_average=data.get("official_average", ""))
        for data in merged.values()
    ]


def parse_file(path: str | Path) -> list[SubjectGrades]:
    """Читает файл выгрузки и выбирает парсер по расширению и содержимому.

    Поддерживаются ``.json`` / ``.html`` / ``.htm`` / ``.txt``; содержимое,
    начинающееся с ``{`` или ``[``, считается JSON. JSON может быть массивом
    ответов ``[{"url": "...", "payload": {...}}]``. Если предметов с оценками
    не найдено — возбуждается :class:`~models.ParseError` с понятным сообщением.
    """
    file_path = Path(path)
    try:
        text = file_path.read_text(encoding="utf-8-sig")
    except (OSError, UnicodeError) as exc:
        raise ParseError(
            f"Не удалось прочитать файл «{file_path.name}»: {exc}"
        ) from exc

    cfg = _selectors(None)
    extension = file_path.suffix.lower()
    stripped = text.lstrip()
    subjects: list[SubjectGrades] = []
    data: Any = None
    json_tried = False

    if extension == ".json" or stripped.startswith(("{", "[")):
        json_tried = True
        try:
            data = json.loads(text)
        except ValueError:
            data = None
        if data is not None:
            subjects = _parse_json_document(data, cfg)

    if not subjects and (
        extension in (".html", ".htm") or "<" in text
        or not json_tried or data is None
    ):
        html_subjects = parse_grades_html(text, cfg)
        if html_subjects:
            subjects = html_subjects

    subjects = validate_subjects(subjects)
    if not subjects:
        raise ParseError(
            f"В файле «{file_path.name}» не найдено ни одного предмета с оценками "
            "1..5. Убедитесь, что это выгрузка дневника: HTML-таблица/карточки "
            "или JSON с названиями предметов и оценками."
        )
    return subjects


def validate_subjects(subjects: Iterable[SubjectGrades]) -> list[SubjectGrades]:
    """Финальная нормализация: имя через clean_name, оценки через validate_marks."""
    out: list[SubjectGrades] = []
    for subject in subjects or ():
        name = clean_name(subject.name)
        if not name:
            continue
        clean_marks = [m for m in (subject.marks or ()) if _as_mark(m) is not None]
        marks = validate_marks(clean_marks)
        if not marks and not subject.official_average:
            continue
        out.append(SubjectGrades(name, marks, subject.period, subject.source,
                                 official_average=subject.official_average))
    return out


def save_debug_dump(text: str, suffix: str = ".html") -> Path:
    """Сохраняет сырой ответ в ``DEBUG_DIR`` для слоя загрузки и отладки.

    Ничего не парсит; вызывается снаружи, когда включена настройка ``save_debug``.
    """
    ensure_dirs()
    ext = suffix if suffix.startswith(".") else "." + suffix
    path = DEBUG_DIR / f"last_response{ext}"
    path.write_text(str(text), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- #
# Минимальное DOM-дерево на html.parser
# --------------------------------------------------------------------------- #

class _Element:
    """Узел дерева: тег, атрибуты и дети (элементы или текстовые фрагменты)."""

    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag: str, attrs: Any) -> None:
        self.tag = tag
        self.attrs = {
            str(k).lower(): ("" if v is None else str(v))
            for k, v in (attrs or ())
        }
        self.children: list[Any] = []

    def text(self) -> str:
        """Весь текст поддерева без добавления пробелов."""
        parts = []
        for child in self.children:
            parts.append(child if isinstance(child, str) else child.text())
        return "".join(parts)


class _TreeBuilder(HTMLParser):
    """Строит упрощённое дерево, устойчивое к незакрытым tr/td/th."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = _Element("#document", ())
        self.stack = [self.root]

    def _close_open(self, tags: frozenset[str]) -> None:
        while len(self.stack) > 1 and self.stack[-1].tag in tags:
            self.stack.pop()

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        tag = tag.lower()
        if tag == "tr":
            self._close_open(frozenset({"tr", "td", "th"}))
        elif tag in ("td", "th"):
            self._close_open(frozenset({"td", "th"}))
        element = _Element(tag, attrs)
        self.stack[-1].children.append(element)
        if tag not in _VOID_TAGS:
            self.stack.append(element)

    def handle_startendtag(self, tag: str, attrs: Any) -> None:
        self.stack[-1].children.append(_Element(tag.lower(), attrs))

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        for index in range(len(self.stack) - 1, 0, -1):
            if self.stack[index].tag == tag:
                del self.stack[index:]
                return

    def handle_data(self, data: str) -> None:
        if data:
            self.stack[-1].children.append(data)


def _iter_elements(element: _Element):
    """Обходит элементы дерева в порядке документа."""
    for child in element.children:
        if isinstance(child, _Element):
            yield child
            yield from _iter_elements(child)


def _row_subject_attrs(cfg: dict) -> set[str]:
    """Собирает data-атрибуты строк из subject_row_selectors."""
    attrs = set(_SUBJECT_ATTRS)
    raw = cfg.get("subject_row_selectors")
    if isinstance(raw, (list, tuple)):
        for selector in raw:
            for match in re.finditer(r"\[\s*(data-[a-zA-Z-]+)", str(selector)):
                attrs.add(match.group(1).lower())
    return attrs


def _mark_class_keywords(cfg: dict) -> set[str]:
    """Собирает слова для class-ячеек оценок из mark_selectors."""
    keywords = {"mark", "grade", "score", "оценк", "балл"}
    raw = cfg.get("mark_selectors")
    if isinstance(raw, (list, tuple)):
        for selector in raw:
            match = re.search(r"class\*\s*=\s*['\"]([^'\"]+)", str(selector))
            if match:
                keywords.add(match.group(1).lower())
    return keywords


def _is_row(element: _Element, row_attrs: set[str]) -> bool:
    """Считает строкой tr, role=row и элементы с data-subject/аналогами."""
    if element.tag == "tr":
        return True
    if element.attrs.get("role", "").lower() == "row":
        return True
    return any(attr in element.attrs for attr in row_attrs)


def _is_mark_cell(element: _Element, class_kws: set[str]) -> bool:
    """Проверяет, является ли элемент ячейкой оценки."""
    if any(attr in element.attrs for attr in _MARK_ATTRS):
        return True
    if element.attrs.get("role", "").lower() == "cell":
        return True
    if element.tag in ("td", "th"):
        return True
    css_class = element.attrs.get("class", "").lower()
    return any(word in css_class for word in class_kws)


def _has_row_descendant(element: _Element, row_ids: set[int]) -> bool:
    """Есть ли среди потомков элемента другая строка."""
    for child in element.children:
        if isinstance(child, _Element):
            if id(child) in row_ids or _has_row_descendant(child, row_ids):
                return True
    return False


def _row_name(row: _Element, row_attrs: set[str], class_kws: set[str],
              skip: Iterable[str]) -> str | None:
    """Ищет название предмета: сначала атрибуты, затем ячейки/текст."""
    for attr in dict.fromkeys((*_SUBJECT_ATTRS, *sorted(row_attrs))):
        if attr in row.attrs and _valid_subject(row.attrs[attr], skip):
            return clean_name(row.attrs[attr])

    # Табличная вёрстка: первая осмысленная ячейка строки.
    for child in row.children:
        if isinstance(child, _Element) and child.tag in ("td", "th"):
            candidate = clean_name(child.text())
            if _valid_subject(candidate, skip):
                return candidate

    # Карточки без data-subject: собственный текст и не-оценочные потомки.
    candidates = ["".join(c for c in row.children if isinstance(c, str))]
    for child in row.children:
        if isinstance(child, _Element) and not _is_mark_cell(child, class_kws):
            candidates.append(child.text())
    for raw in candidates:
        candidate = clean_name(raw)
        if _valid_subject(candidate, skip):
            return candidate
    return None


def _collect_row_marks(row: _Element, class_kws: set[str]) -> list[Mark]:
    """Собирает оценки строки: приоритет у data-атрибутов, затем ячейки."""
    marks: list[Mark] = []
    stack = [row]
    while stack:
        element = stack.pop()
        attr_hit = False
        for attr in _MARK_ATTRS:
            if attr in element.attrs:
                attr_hit = True
                mark = _as_mark(element.attrs[attr])
                if mark is not None:
                    w = 1
                    for wa in ("data-weight", "data-coef", "weight"):
                        if wa in element.attrs and element.attrs[wa]:
                            w = normalize_weight(element.attrs[wa])
                            break
                    marks.append(Mark(mark, weight=w))
        if attr_hit:
            continue  # атрибут уже дал оценку, текст элемента не дублируем
        if _is_mark_cell(element, class_kws):
            mw = _as_mark_and_weight(element.text())
            if mw is not None:
                marks.append(Mark(mw[0], weight=mw[1]))
                continue
        for child in reversed(element.children):
            if isinstance(child, _Element):
                stack.append(child)
    if marks:
        return marks

    # Фолбэк для карточек: в реальном дневнике оценки — отдельные «чипы» без
    # data-атрибутов и без говорящего класса. Берём только листовые узлы с
    # ровно одной цифрой 1..5: «4.00», «4,50», «01 сент», «15 ноя» и «1 триместр»
    # под это правило не подходят. Блок среднего балла дополнительно исключаем.
    for element in _iter_elements(row):
        if any(isinstance(child, _Element) for child in element.children):
            continue
        css_class = element.attrs.get("class", "").lower()
        if any(word in css_class for word in
               ("average", "mean", "средн", "итог", "total")):
            continue
        text = element.text().strip()
        mw = _as_mark_and_weight(text)
        if mw is not None:
            marks.append(Mark(mw[0], weight=mw[1]))
    return marks


def parse_marks_api(payload: Any, period: str = "") -> list[SubjectGrades]:
    """Разбирает ответ ``/api/family/web/v1/marks`` портала «Моя школа».

    Реальный формат — плоский список записей, по одной на оценку::

        {"payload": [{"subject_name": "Алгебра", "value": "5", "weight": 1,
                      "date": "2026-11-02", "created_at": "...", "id": ...}, ...]}

    Группируем по предмету, сохраняем хронологический порядок, отбрасываем
    нечисловые значения («НВ», «н/а»), баллы вне 1..5 и служебные поля.
    Возвращает только предметы, у которых есть хотя бы одна оценка.
    """
    rows: Any = None
    if isinstance(payload, dict):
        for key in ("payload", "data", "results", "items", "marks"):
            if isinstance(payload.get(key), list):
                rows = payload[key]
                break
    elif isinstance(payload, list):
        rows = payload
    if not isinstance(rows, list):
        return []

    grouped: dict[str, list[tuple[str, str, str, Mark]]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = clean_name(str(row.get("subject_name")
                             or row.get("subject")
                             or row.get("discipline") or ""))
        if not name:
            continue
        mark = _as_mark(row.get("value"))
        if mark is None:
            continue
        weight = row.get("weight")
        if weight is None:
            for wk in ("coef", "coefficient", "вес"):
                if wk in row and row[wk] is not None:
                    weight = row[wk]
                    break
        w = normalize_weight(weight)
        mark_obj = Mark(mark, weight=w)
        grouped.setdefault(name, []).append((
            str(row.get("date") or ""),
            str(row.get("created_at") or ""),
            str(row.get("id") or ""),
            mark_obj,
        ))

    subject_averages: dict[str, str] = {}
    if isinstance(payload, dict):
        for key in ("averages", "stats", "statistics", "subjects"):
            sub_dict = payload.get(key)
            if isinstance(sub_dict, dict):
                for sname, sdata in sub_dict.items():
                    if isinstance(sdata, dict):
                        avg = _extract_official_average(sdata)
                    else:
                        avg = _extract_official_average({"average": sdata})
                    if avg:
                        subject_averages[clean_name(sname)] = avg
            elif isinstance(sub_dict, list):
                for item in sub_dict:
                    if isinstance(item, dict):
                        sname = clean_name(str(item.get("subject_name") or item.get("name") or ""))
                        avg = _extract_official_average(item)
                        if sname and avg:
                            subject_averages[sname] = avg

    out: list[SubjectGrades] = []
    for name, entries in grouped.items():
        entries.sort()
        official_avg = subject_averages.get(name, "")
        out.append(SubjectGrades(name, tuple(e[3] for e in entries), period, "live",
                                 official_average=official_avg))
    return validate_subjects(out)


# ``/api/family/web/v1/profile`` -> children[].groups[].name имеют вид
# «Вероятность и статистика 9 А УП ООО 2026-2027 уч. год, 9 кл.». Хвост с
# классом/учебным годом отрезаем, иначе получим 18 разных «предметов» на каждый
# класс. Требуем маркер класса в хвосте, чтобы не обрезать названия с цифрами.
_CLASS_MARK = re.compile(r"\s+\d{1,2}\s*[А-Яа-яЁё](?=\s|$)")
_CLASS_TAIL = re.compile(r"УП\b|уч\.?\s*год|\d+\s*кл\.", re.IGNORECASE)


def _clean_group_name(raw: str) -> str:
    name = clean_name(raw)
    match = _CLASS_MARK.search(name)
    if match and _CLASS_TAIL.search(name[match.start():]):
        name = clean_name(name[:match.start()])
    return name


def parse_profile_subjects(payload: Any) -> tuple[str, ...]:
    """Все предметы класса из профиля портала, включая предметы без оценок.

    Ответ ``/marks`` содержит только предметы, по которым уже есть оценки, а
    профиль ``/profile`` содержит полный список групп ученика. Без него
    «Вероятность и статистика» и «Математика» молча исчезают из таблицы.
    """
    found: list[str] = []
    seen: set[str] = set()

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            if ("subject_id" in node and isinstance(node.get("name"), str)
                    and not node.get("is_fake")):
                name = _clean_group_name(str(node["name"]))
                key = name.casefold()
                if name and key not in seen:
                    seen.add(key)
                    found.append(name)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)

    walk(payload)
    return tuple(sorted(found, key=str.casefold))
