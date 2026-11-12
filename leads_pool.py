"""Пул горячих лидов: единственный файл данных и все операции над ним.

Слой данных без сети и без GUI. Ссылки на контакты приходят из
`leads_contacts`; рассылка живёт в `leads_mail` и использует этот модуль.

Почему отдельный файл: раньше пул, преобразование контактов и отправка
лежали в одном модуле на 1400 строк, и любая правка формата CSV рисковала
сломать рассылку. Теперь граница явная: здесь — только CSV и правила пула.
"""

from __future__ import annotations

import csv
import os
import re
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

_POOL_LOCK = threading.RLock()

from leads_contacts import (
    format_phone as _format_phone,
    from_link as _from_link,
    messenger_summary,
    num as _num,
    pick_contact as _pick_contact,
    to_link as _to_link,
)

# --------------------------------------------------------------------------- #
#  Схема файла
# --------------------------------------------------------------------------- #
#
#  leads.csv перезаписывается при каждом сборе, поэтому горячие лиды копятся
#  отдельно — в hot_pool.csv рядом с CSV. У каждой записи есть found_at: если
#  за pool_days (по умолчанию 5) ни ты, ни скрипт не написали — запись удаляется.

POOL_NAME = "hot_pool.csv"
# В файле лежат готовые ссылки на контакты, а не ники: так таблицу можно открыть
# в Excel и сразу кликнуть. Числового id 2GIS в пуле нет — Excel превращал его
# в 7.0000001087E+16, а для дедупликации хватает названия и телефона.
POOL_COLUMNS = (
    "name", "telegram", "max", "vk", "email", "phone", "city",
    "topic", "card", "found_at", "sent_at", "channel", "status",
    # Карточка-инспектор (ШАГ 3): без этих полей в UI были бы одни «—».
    # Старые CSV без колонок читаются как пустые значения — схема расширяемая.
    "address", "rating", "reviews", "rubric",
)
POOL_KEEP_STATUS = ("replied", "seen", "awaiting", "manual")
# Статусы «лиду уже писали»: такие строки не показываются в выдаче вкладки «Лиды».
CONTACTED_STATUSES = ("awaiting", "manual", "replied", "seen")

# Детали карточки 2ГИС, дозаполняемые в пуле из свежего сбора/leads.csv.
POOL_DETAIL_FIELDS = ("address", "rating", "reviews", "rubric")
# Поля карточки-инспектора в GUI: ровно эти ключи отдаёт inspector_fields.
INSPECTOR_FIELDS = ("name", "topic", "address", "rating", "phone", "messengers")

# 2ГИС-слаги крупных городов для режима «вся Россия».
RU_CITIES = (
    "moscow", "spb", "ekaterinburg", "novosibirsk", "kazan", "n_novgorod",
    "samara", "omsk", "chelyabinsk", "rostov",
    "ufa", "krasnoyarsk", "voronezh", "perm", "volgograd",
)

# Старое хозяйство: GUI раньше писал все лиды в leads.csv. Файл больше не нужен,
# но у пользователя он уже есть — при первом запуске переливаем его в пул и
# убираем в архив, чтобы не мешал.
LEGACY_NAME = "leads.csv"
LEGACY_EXTRAS = ("leads.jsonl", "leads_new.csv", "leads_new.jsonl")
LEGACY_SUFFIX = ".migrated"


class PoolBusy(RuntimeError):
    """hot_pool.csv занят другой программой (Excel, LibreOffice)."""


def default_pool_path(dir_path) -> Path:
    """Единственный файл данных: <папка приложения>/hot_pool.csv."""
    return Path(dir_path) / POOL_NAME


def pool_path(csv_file) -> Path:
    """По умолчанию файла пула по пути к CSV."""
    return Path(csv_file).parent / POOL_NAME


def _now() -> datetime:
    return datetime.now()


def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _pool_value(channel: str, value) -> str:
    """Значение для колонки пула: ссылка, если её можно построить, иначе сырое."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    return _to_link(channel, raw) or raw


def inspector_fields(row: dict) -> dict[str, str]:
    """Поля карточки-инспектора: ровно INSPECTOR_FIELDS, строки, «—» если пусто.

    Чистая функция без tkinter/файлов/сети: логика прежнего tools.pyw._show_lead.
    """
    def text(key: str) -> str:
        return str(row.get(key) or "").strip() or "—"

    rating = str(row.get("rating") or "").strip()
    reviews = str(row.get("reviews") or "").strip()
    if rating and reviews:
        rating_text = f"{rating} · {reviews} отзывов"
    elif rating:
        rating_text = rating
    elif reviews:
        rating_text = f"{reviews} отзывов"
    else:
        rating_text = "—"

    phone = _format_phone(row.get("phone"))
    messengers = messenger_summary(row)
    return {
        "name": text("name"),
        "topic": str(row.get("topic") or row.get("rubric") or "").strip() or "—",
        "address": text("address"),
        "rating": rating_text,
        "phone": phone or "—",
        "messengers": "—" if messengers == "не найдены" else messengers,
    }


def backfill_fields(target: dict, source: dict) -> int:
    """Дозаполняет ТОЛЬКО пустые поля target из source; возвращает число правок.

    source — строка старого leads.csv (значения str) либо dict парсера 2ГИС
    (rating/reviews бывают числами). Непустое в target не перезаписывается.
    """
    changed = 0
    for ch in ("telegram", "max", "vk", "email"):
        if (target.get(ch) or "").strip():
            continue
        value = _pool_value(ch, _pick_contact(source, ch))
        if value:
            target[ch] = value
            changed += 1
    if not (target.get("topic") or "").strip():
        value = str(source.get("query") or source.get("topic") or "").strip()
        if value:
            target["topic"] = value
            changed += 1
    if not (target.get("card") or "").strip():
        value = str(source.get("url") or source.get("card") or "").strip()
        if value:
            target["card"] = value
            changed += 1
    for field in POOL_DETAIL_FIELDS:
        if (target.get(field) or "").strip():
            continue
        raw = source.get(field)
        value = "" if raw is None else str(raw).strip()
        if value:
            target[field] = value
            changed += 1
    return changed


def pool_key(row: dict) -> str:
    """Ключ записи для дедупликации и правок: название + цифры телефона."""
    name = re.sub(r"\s+", " ", str(row.get("name") or "")).strip().lower()
    digits = re.sub(r"\D", "", str(row.get("phone") or ""))
    if not name and not digits:
        return ""
    return f"{name}|{digits}"


def row_id(row: dict) -> str:
    """Ключ лида для журнала отправок: id 2GIS, если есть, иначе название+телефон."""
    return str(row.get("id") or "").strip() or pool_key(row)


def _tier_of(lead: dict) -> str:
    """hot/warm/cold: поле tier → префикс reason → score."""
    tier = str(lead.get("tier") or "").strip().lower()
    if tier:
        return tier
    reason = str(lead.get("reason") or "").strip().lower()
    for name in ("hot", "warm", "cold"):
        if reason.startswith(name):
            return name
    return "hot" if _num(lead.get("score")) >= 70 else ""


def pool_load(pool_file) -> list[dict]:
    """Все записи пула (в порядке добавления). Старый формат приводится к новому."""
    with _POOL_LOCK:
        path = Path(pool_file)
        spare = path.with_name(f"{path.stem}_new{path.suffix}")

        target = path
        if not path.exists():
            if spare.exists():
                target = spare
            else:
                return []

        rows: list[dict] = []
        last_exc = None
        for attempt in range(5):
            try:
                with target.open(encoding="utf-8-sig", newline="") as fh:
                    for row in csv.DictReader(fh):
                        item = {key: str(row.get(key) or "").strip() for key in POOL_COLUMNS}
                        if not item["name"] and not item["phone"]:
                            continue
                        for channel in ("telegram", "max", "vk", "email"):
                            item[channel] = _pool_value(channel, item[channel])
                        rows.append(item)
                last_exc = None
                break
            except (PermissionError, OSError) as exc:
                last_exc = exc
                rows.clear()
                if attempt < 4:
                    time.sleep(0.15)
            except csv.Error as exc:
                raise PoolBusy(f"Файл {target.name} повреждён: {exc}") from exc

        if last_exc is not None:
            raise PoolBusy(f"{target.name} открыт в другой программе — не удалось прочитать: {last_exc}")

        # Если рядом есть файл _new (сохранённый при блокировке основного),
        # догружаем из него свежие записи без дублей
        if spare.exists() and spare != target:
            try:
                with spare.open(encoding="utf-8-sig", newline="") as fh:
                    known = {pool_key(r) for r in rows}
                    for row in csv.DictReader(fh):
                        item = {key: str(row.get(key) or "").strip() for key in POOL_COLUMNS}
                        if not item["name"] and not item["phone"]:
                            continue
                        key = pool_key(item)
                        if key and key not in known:
                            for channel in ("telegram", "max", "vk", "email"):
                                item[channel] = _pool_value(channel, item[channel])
                            rows.append(item)
                            known.add(key)
            except (OSError, csv.Error):
                pass

        return rows


def _pool_save(pool_file, rows: list[dict]) -> None:
    """Пишет пул через временный файл — занятый Excel не портит данные.

    Если файл открыт в таблице, Windows не даёт его заменить: тогда честно
    сообщаем об этом, иначе новые горячие лиды молча теряются.
    """
    with _POOL_LOCK:
        path = Path(pool_file)
        tmp = path.with_name(f"{path.name}.{os.getpid()}_{threading.get_ident()}_{time.time_ns()}.tmp")
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with tmp.open("w", encoding="utf-8-sig", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=POOL_COLUMNS)
                writer.writeheader()
                for row in rows:
                    writer.writerow({key: row.get(key, "") for key in POOL_COLUMNS})
        except OSError as exc:
            if tmp.exists():
                try:
                    tmp.unlink()
                except OSError:
                    pass
            raise PoolBusy(f"не записался {path.name}: {exc}") from exc

        spare = path.with_name(f"{path.stem}_new{path.suffix}")
        for attempt in range(10):
            try:
                tmp.replace(path)
                if spare.exists():
                    try:
                        spare.unlink()
                    except OSError:
                        pass
                return
            except OSError:
                if attempt < 9:
                    time.sleep(0.2)

        # файл занят: не теряем пачку, кладём рядом и говорим, куда смотреть
        try:
            tmp.replace(spare)
            where = f", свежие записи — в {spare.name}"
        except OSError:
            where = ""
        raise PoolBusy(
            f"{path.name} открыт в другой программе — закрой его и нажми «Обновить»{where}")


def _lead_to_pool_row(lead: dict, found_at: str) -> dict:
    row = {
        "name": lead.get("name", ""),
        "telegram": _pool_value("telegram", _pick_contact(lead, "telegram")),
        "max": _pool_value("max", _pick_contact(lead, "max")),
        "vk": _pool_value("vk", _pick_contact(lead, "vk")),
        "email": _pool_value("email", _pick_contact(lead, "email")),
        "phone": lead.get("phone", ""),
        "city": lead.get("city", ""),
        "topic": str(lead.get("topic") or lead.get("query") or "").strip(),
        "card": str(lead.get("url") or lead.get("card") or "").strip(),
        "found_at": found_at,
        "sent_at": "",
        "channel": "",
        "status": "",
    }
    for field in POOL_DETAIL_FIELDS:
        value = lead.get(field)
        row[field] = "" if value is None else str(value).strip()
    return row


def pool_sync(csv_file) -> int:
    """Синхронизирует горячих лидов из CSV в пул. Возвращает число добавленных."""
    pool_file = pool_path(csv_file)
    rows = _read_csv(csv_file)
    return pool_add(pool_file, rows)


def pool_refresh(pool_file) -> int:
    """Обновляет контакты в пуле из CSV. Возвращает число обновлённых."""
    csv_file = Path(pool_file).parent / "leads.csv"
    pool_rows = pool_load(pool_file)
    csv_rows = {pool_key(r): r for r in _read_csv(csv_file)}
    updated = 0
    for row in pool_rows:
        key = pool_key(row)
        src = csv_rows.get(key)
        if not src:
            continue
        updated += backfill_fields(row, src)
    if updated:
        _pool_save(pool_file, pool_rows)
    return updated


def pool_add(pool_file, leads: list[dict]) -> int:
    """Добавляет горячих лидов в пул. Возвращает число новых."""
    hot = [l for l in leads if is_hot(l)]
    if not hot:
        return 0
    rows = pool_load(pool_file)
    known = {pool_key(row) for row in rows}
    stamp = _now().isoformat(timespec="seconds")
    added = 0       # сколько реально НОВЫХ записей (возвращаемое значение)
    changed = 0     # были ли вообще изменения — по ним решаем сохранять файл
    by_key = {pool_key(row): row for row in rows}
    for lead in hot:
        key = pool_key(lead)
        if not key:
            continue
        if key in by_key:
            # Дозаполняем только пустые поля: контакты, тему, карточку, детали.
            changed += backfill_fields(by_key[key], lead)
            continue
        new_row = _lead_to_pool_row(lead, stamp)
        rows.append(new_row)
        by_key[key] = new_row
        added += 1
    if added or changed:
        _pool_save(pool_file, rows)
    return added


def pool_purge(pool_file, days: float = 5) -> int:
    """Удаляет записи, которым больше `days` и по которым не было отправки."""
    rows = pool_load(pool_file)
    if not rows:
        return 0
    limit = _now() - timedelta(days=days)
    kept: list[dict] = []
    removed = 0
    for row in rows:
        found = _parse_dt(row.get("found_at"))
        status = (row.get("status") or "").strip().lower()
        touched = bool((row.get("sent_at") or "").strip()) or status in POOL_KEEP_STATUS
        if found is not None and found < limit and not touched:
            removed += 1
            continue
        kept.append(row)
    if removed:
        _pool_save(pool_file, kept)
    return removed


def pool_mark_sent(pool_file, key, channel: str, when: datetime | None = None) -> bool:
    """Отмечает, что по лиду написали: срок жизни больше не действует."""
    rows = pool_load(pool_file)
    stamp = (when or _now()).isoformat(timespec="seconds")
    changed = False
    for row in rows:
        if pool_key(row) == str(key):
            row["sent_at"] = stamp
            row["channel"] = channel
            row["status"] = "manual" if channel == "manual" else "awaiting"
            changed = True
            break
    if changed:
        _pool_save(pool_file, rows)
    return changed


def pool_remove(pool_file, key) -> bool:
    """Убирает лида из пула совсем."""
    rows = pool_load(pool_file)
    kept = [row for row in rows if pool_key(row) != str(key)]
    if len(kept) == len(rows):
        return False
    _pool_save(pool_file, kept)
    return True


def pool_mark_status(pool_file, key, status: str) -> bool:
    rows = pool_load(pool_file)
    changed = False
    for row in rows:
        if pool_key(row) == str(key):
            row["status"] = status
            changed = True
            break
    if changed:
        _pool_save(pool_file, rows)
    return changed


def _read_csv(path) -> list[dict]:
    for attempt in range(3):
        try:
            with open(path, encoding="utf-8-sig", newline="") as fh:
                return list(csv.DictReader(fh))
        except (PermissionError, OSError):
            if attempt < 2:
                time.sleep(0.15)
        except csv.Error:
            return []
    return []


def migrate_legacy(dir_path) -> dict:
    """Разовый переезд: старый leads.csv доливается в пул и уезжает в архив.

    GUI раньше писал все лиды в leads.csv; теперь единственный файл — пул.
    Повторный вызов ничего не делает: файла уже нет (он переименован).
    Возвращает {"added": int, "filled": int, "archived": str}.
    """
    base = Path(dir_path)
    out = {"added": 0, "filled": 0, "archived": ""}
    legacy = base / LEGACY_NAME
    if not legacy.exists():
        return out

    pool_file = default_pool_path(base)
    rows = _read_csv(legacy)
    out["added"] = pool_add(pool_file, rows)          # только hot, дедуп по pool_key

    source = {pool_key(r): r for r in rows}
    current = pool_load(pool_file)
    for row in current:
        src = source.get(pool_key(row))
        if not src:
            continue
        out["filled"] += backfill_fields(row, src)
    if out["filled"]:
        _pool_save(pool_file, current)

    for name in (LEGACY_NAME,) + LEGACY_EXTRAS:
        src = base / name
        if not src.exists():
            continue
        try:
            src.replace(base / (name + LEGACY_SUFFIX))
            if name == LEGACY_NAME:
                out["archived"] = LEGACY_NAME + LEGACY_SUFFIX
        except OSError:
            pass  # файл открыт в Excel — переедет при следующем запуске
    return out


def pool_stats(pool_file, days: float = 5) -> dict:
    """Сколько в пуле всего и сколько истекает в ближайшие сутки."""
    rows = pool_load(pool_file)
    now = _now()
    expiring = 0
    for row in rows:
        if (row.get("sent_at") or "").strip():
            continue
        if (row.get("status") or "").strip().lower() in POOL_KEEP_STATUS:
            continue
        found = _parse_dt(row.get("found_at"))
        if found is None:
            continue
        left = (found + timedelta(days=days)) - now
        if left <= timedelta(days=1):
            expiring += 1
    return {"total": len(rows), "expiring": expiring}


def load_hot_leads(csv_file) -> list[dict]:
    """Горячие лиды из пула, свежие сверху. Пула нет — читаем CSV и фильтруем по reason."""
    pool_file = pool_path(csv_file)
    rows: list[dict] = []
    if pool_file.exists():
        for row in pool_load(pool_file):
            item = dict(row)
            item["tier"] = "hot"
            rows.append(item)
    else:
        for row in _read_csv(csv_file):
            if is_hot(row):
                item = dict(row)
                item["tier"] = "hot"
                rows.append(item)
    rows.sort(key=lambda r: str(r.get("found_at") or ""), reverse=True)
    return rows


def is_contacted(row: dict) -> bool:
    """Лид уже получил сообщение: была отправка (sent_at) или статус «написан/отвечен».

    Статус "stopped" («Остановить лид») контактом не считается — сообщение
    могло и не уйти. Именно такие строки скрывает выдача вкладки «Лиды».
    """
    if str(row.get("sent_at") or "").strip():
        return True
    return str(row.get("status") or "").strip().lower() in CONTACTED_STATUSES


def is_hot(lead) -> bool:
    """Горячий лид: поле tier, иначе префикс reason, иначе score ≥ 70."""
    return _tier_of(lead) == "hot"
