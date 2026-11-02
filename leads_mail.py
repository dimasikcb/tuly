"""Рассылка по горячим лидам 2GIS: ИИ-тексты, очередь каналов, 5-дневный follow-up.

Логика отделена от GUI: модуль ничего не знает про Tkinter. GUI передаёт сюда
настройки (словарь), коллбэки лога/прогресса и Event остановки.

Каналы по приоритету: MAX -> Telegram -> VK -> Email. WhatsApp не используется.
Одно сообщение в минуту. Через 5 дней без ответа — следующий канал (с пометкой
«уже писал»), ещё через 5 — лид закрывается. Ответы пересылаются в MAX.

Telegram — user-аккаунт (Telethon). Каждая операция идёт под общим lock'ом,
клиент подключается и отключается в одной и той же нити: файл сессии SQLite
никогда не открыт двумя клиентами одновременно, event loop создаётся на поток.
"""

from __future__ import annotations

import asyncio
import csv
import imaplib
import json
import random
import re
import smtplib
import threading
import time
from datetime import datetime, timedelta
from email import message_from_bytes
from email.message import EmailMessage
from pathlib import Path

PRIORITY = ("max", "telegram", "vk", "email")
CHANNEL_TITLE = {"max": "MAX", "telegram": "Telegram", "vk": "ВК", "email": "Почта"}
DAY = 86400.0
STATUSES = ("replied", "stopped", "read", "seen", "closed", "failed")

# Один lock на весь Telegram: сессия SQLite и один аккаунт.
TG_LOCK = threading.Lock()


# --------------------------------------------------------------------------- #
#  Настройки
# --------------------------------------------------------------------------- #

DEFAULT_SETTINGS = {
    "ai_url": "https://openrouter.ai/api/v1",
    "ai_model": "meta-llama/llama-3.1-8b-instruct:free",
    "ai_key": "",
    "style": "Коротко, по делу. Без приветствий и шаблонных фраз. Конкретная выгода, один вопрос в конце.",
    "idea": "",
    "api_id": "",
    "api_hash": "",
    "tg_session_file": "session_telegram",
    "tg_phone": "",
    "max_url": "",
    "max_token": "",
    "max_self": "",
    "vk_token": "",
    "smtp_host": "",
    "smtp_port": "465",
    "smtp_user": "",
    "smtp_pass": "",
    "smtp_from": "",
    "mail_subject": "Предложение по сайту",
    "followup_days": "5",
    "pool_days": "5",
    "pacing": "60",
    "dry_run": False,
}


def load_settings(path) -> dict:
    data = dict(DEFAULT_SETTINGS)
    try:
        data.update(json.loads(Path(path).read_text(encoding="utf-8")))
    except (OSError, ValueError):
        pass
    return data


def save_settings(path, settings: dict) -> None:
    try:
        Path(path).write_text(json.dumps(settings, ensure_ascii=False, indent=1), encoding="utf-8")
    except OSError:
        pass


# --------------------------------------------------------------------------- #
#  Лиды и каналы
# --------------------------------------------------------------------------- #

# --------------------------------------------------------------------------- #
#  Пул горячих лидов
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
)
POOL_KEEP_STATUS = ("replied", "seen", "awaiting", "manual")

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


def _parse_dt(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(str(value).strip())
    except (TypeError, ValueError):
        return None


def _to_link(channel: str, value) -> str:
    """Ник/e-mail → готовая ссылка. Уже ссылку возвращает как есть."""
    value = str(value or "").strip()
    if not value:
        return ""
    if value.lower().startswith(("http://", "https://", "mailto:")):
        return value
    if channel == "telegram":
        digits = re.sub(r"\D", "", value)
        if re.fullmatch(r"[\d\s\-()+]+", value) and len(digits) >= 7:
            return "https://t.me/+" + digits  # диплинк по номеру, как у 2ГИС
        return "https://t.me/" + value.lstrip("@").strip("/")
    if channel == "max":
        return "https://max.ru/" + value.lstrip("@").strip("/")
    if channel == "vk":
        return "https://vk.com/" + value.strip("/")
    if channel == "email":
        return "mailto:" + value
    return ""


def _from_link(channel: str, value) -> str:
    """Ссылка → ник/e-mail, с которым работают отправка и поиск в TG/ВК."""
    value = str(value or "").strip()
    if not value:
        return ""
    low = value.lower()
    if channel == "telegram" and "t.me/" in low:
        return value[low.index("t.me/") + 5:].strip("/")
    if channel == "max" and "max.ru/" in low:
        return value[low.index("max.ru/") + 7:].strip("/")
    if channel == "vk" and "vk.com/" in low:
        return value[low.index("vk.com/") + 6:].strip("/")
    if channel == "email" and low.startswith("mailto:"):
        return value[7:].strip()
    return value


def _pool_value(channel: str, value) -> str:
    """Значение для колонки пула: ссылка, если её можно построить, иначе сырое."""
    raw = str(value or "").strip()
    if not raw:
        return ""
    return _to_link(channel, raw) or raw


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
    path = Path(pool_file)
    rows: list[dict] = []
    try:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            for row in csv.DictReader(fh):
                item = {key: str(row.get(key) or "").strip() for key in POOL_COLUMNS}
                if not item["name"] and not item["phone"]:
                    continue
                for channel in ("telegram", "max", "vk", "email"):
                    item[channel] = _pool_value(channel, item[channel])
                rows.append(item)
    except (OSError, csv.Error):
        return []
    return rows


def _pool_save(pool_file, rows: list[dict]) -> None:
    """Пишет пул через временный файл — занятый Excel не портит данные.

    Если файл открыт в таблице, Windows не даёт его заменить: тогда честно
    сообщаем об этом, иначе новые горячие лиды молча теряются.
    """
    path = Path(pool_file)
    tmp = path.with_name(path.name + ".tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with tmp.open("w", encoding="utf-8-sig", newline="") as fh:
            writer = csv.DictWriter(fh, fieldnames=POOL_COLUMNS)
            writer.writeheader()
            for row in rows:
                writer.writerow({key: row.get(key, "") for key in POOL_COLUMNS})
    except OSError as exc:
        raise PoolBusy(f"не записался {path.name}: {exc}") from exc

    for attempt in range(10):
        try:
            tmp.replace(path)
            return
        except OSError:
            if attempt < 9:
                time.sleep(0.2)
    # файл занят: не теряем пачку, кладём рядом и говорим, куда смотреть
    spare = path.with_name(f"{path.stem}_new{path.suffix}")
    try:
        tmp.replace(spare)
        where = f", свежие записи — в {spare.name}"
    except OSError:
        where = ""
    raise PoolBusy(
        f"{path.name} открыт в другой программе — закрой его и нажми «Обновить»{where}")


def _lead_to_pool_row(lead: dict, found_at: str) -> dict:
    return {
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
        for channel in ("telegram", "max", "vk", "email"):
            if not (row.get(channel) or "").strip():
                value = _pool_value(channel, _pick_contact(src, channel))
                if value:
                    row[channel] = value
                    updated += 1
        # Дозаполнить тему и карточку 2ГИС
        if not (row.get("topic") or "").strip() and (src.get("query") or src.get("topic")):
            row["topic"] = str(src.get("query") or src.get("topic")).strip()
            updated += 1
        if not (row.get("card") or "").strip() and (src.get("url") or src.get("card")):
            row["card"] = str(src.get("url") or src.get("card")).strip()
            updated += 1
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
    added = 0
    by_key = {pool_key(row): row for row in rows}
    for lead in hot:
        key = pool_key(lead)
        if not key:
            continue
        if key in by_key:
            existing = by_key[key]
            # Дозаполняем card, topic или контакты, если в существующей записи они пустые
            new_card = str(lead.get("url") or lead.get("card") or "").strip()
            if not (existing.get("card") or "").strip() and new_card:
                existing["card"] = new_card
                added += 1
            new_topic = str(lead.get("topic") or lead.get("query") or "").strip()
            if not (existing.get("topic") or "").strip() and new_topic:
                existing["topic"] = new_topic
            for ch in ("telegram", "max", "vk", "email"):
                if not (existing.get(ch) or "").strip():
                    val = _pool_value(ch, _pick_contact(lead, ch))
                    if val:
                        existing[ch] = val
            continue
        new_row = _lead_to_pool_row(lead, stamp)
        rows.append(new_row)
        by_key[key] = new_row
        added += 1
    if added:
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
    try:
        with open(path, encoding="utf-8-sig", newline="") as fh:
            return list(csv.DictReader(fh))
    except (OSError, csv.Error):
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
        for channel in ("telegram", "max", "vk", "email"):
            if not (row.get(channel) or "").strip():
                value = _pool_value(channel, _pick_contact(src, channel))
                if value:
                    row[channel] = value
                    out["filled"] += 1
        # Перенести тему и ссылку на карточку 2ГИС из leads.csv
        if not (row.get("topic") or "").strip():
            val = str(src.get("query") or src.get("topic") or "").strip()
            if val:
                row["topic"] = val
                out["filled"] += 1
        if not (row.get("card") or "").strip():
            val = str(src.get("url") or src.get("card") or "").strip()
            if val:
                row["card"] = val
                out["filled"] += 1
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


def _num(value, default: float = 0.0) -> float:
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


def _phone_to_e164(phone: str) -> str:
    digits = re.sub(r"[^\d+]", "", str(phone).strip())
    if digits.startswith("+"):
        return digits
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:]
    if len(digits) == 10:
        return "+7" + digits
    return "+" + digits


def _pick_contact(row: dict, channel: str) -> str:
    """Контакт канала: прямое поле, иначе запись вида «telegram:ник» в messengers."""
    direct = str(row.get(channel) or "").strip()
    if direct:
        return direct
    raw = row.get("messengers") or ""
    items = raw if isinstance(raw, (list, tuple)) else str(raw).split(";")
    for item in items:
        text = str(item or "").strip()
        if text.startswith(channel + ":"):
            return text.split(":", 1)[1].strip()
    return ""


def channels_of(row: dict) -> dict:
    """Доступные у лида каналы: {канал: (contact, kind)}.

    Контакты могут лежать и ником, и готовой ссылкой (в пуле — ссылкой),
    поэтому значение сначала приводится к нику: отправка работает с ним.
    """
    out: dict[str, tuple[str, str]] = {}
    mx = _from_link("max", _pick_contact(row, "max"))
    if mx:
        out["max"] = (mx, "username")
    tg = _from_link("telegram", _pick_contact(row, "telegram"))
    if tg:
        t = tg.strip()
        digits = re.sub(r"\D", "", t)
        if t and re.fullmatch(r"[\d\s\-()+]+", t) and len(digits) >= 7:
            out["telegram"] = (_phone_to_e164(t), "phone")
        elif t:
            out["telegram"] = (t.lstrip("@"), "username")
    vk = _from_link("vk", _pick_contact(row, "vk"))
    if vk:
        out["vk"] = (vk, "screen")
    em = _from_link("email", _pick_contact(row, "email"))
    if em:
        out["email"] = (em, "email")
    return out


def contact_display(ch, contact: tuple[str, str]) -> str:
    c, kind = contact
    if ch == "telegram" and kind == "username":
        return "@" + c
    return c


def contact_link(ch: str, contact: tuple[str, str]) -> str:
    """Кликабельная ссылка на контакт; "" если ссылку не построить."""
    value, kind = contact
    value = str(value).strip()
    if not value:
        return ""
    if value.lower().startswith(("http://", "https://", "mailto:")):
        return value
    if ch == "telegram":
        if kind == "phone":
            digits = re.sub(r"\D", "", value)
            return "https://t.me/+" + digits if digits else ""
        return "https://t.me/" + value.lstrip("@").strip("/")
    if ch == "max":
        return "https://max.ru/" + value.lstrip("@").strip("/")
    if ch == "vk":
        return "https://vk.com/" + value.strip("/")
    if ch == "email":
        return "mailto:" + value
    return ""


def channel_label(ch: str) -> str:
    return CHANNEL_TITLE.get(ch, ch)


# --- как показывать контакты в таблице -------------------------------------- #

TARGET_TITLE = {**CHANNEL_TITLE, "phone": "Телефон"}


def format_phone(phone) -> str:
    """+7 906 026-34-84; «—», если цифр нет."""
    digits = re.sub(r"\D", "", str(phone or ""))
    if len(digits) == 11 and digits[0] in "78":
        return f"+7 {digits[1:4]} {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    return str(phone or "—") if digits else "—"


def _short(url: str) -> str:
    """Ссылка без схемы — влезает в колонку и всё равно читается как ссылка."""
    text = str(url or "").strip()
    if text.lower().startswith("mailto:"):
        return text[7:]
    return re.sub(r"^https?://", "", text).rstrip("/")


def _same_number(a, b) -> bool:
    da, db = re.sub(r"\D", "", str(a or "")), re.sub(r"\D", "", str(b or ""))
    return bool(da) and bool(db) and da[-10:] == db[-10:]


def targets_of(row: dict) -> list[tuple[str, str, str]]:
    """Все точки контакта по приоритету: (канал, что показать, куда вести).

    Telegram-диплинк на тот же номер (t.me/+79060263484) — это телефон, а не
    отдельный канал: показываем номер один раз, а ссылка ведёт в чат.
    """
    out: list[tuple[str, str, str]] = []
    phone = format_phone(row.get("phone"))
    tg = _to_link("telegram", row.get("telegram"))
    tg_is_phone = bool(tg) and phone != "—" and _same_number(tg, row.get("phone"))
    if tg and not tg_is_phone:
        out.append(("telegram", _short(tg), tg))
    for channel in ("vk", "max", "email"):
        link = _to_link(channel, row.get(channel))
        if link:
            out.append((channel, _short(link), link))
    if phone != "—":
        out.append(("phone", phone, tg if tg_is_phone else ""))
    return out


def target_of(row: dict) -> tuple[str, str, str]:
    """Куда писать: TG → VK → MAX → почта → телефон. Нет контактов — («», «—», «»)."""
    items = targets_of(row)
    return items[0] if items else ("", "—", "")


def is_hot(lead) -> bool:
    """Горячий лид: поле tier, иначе префикс reason, иначе score ≥ 70."""
    return _tier_of(lead) == "hot"


# --------------------------------------------------------------------------- #
#  Состояние
# --------------------------------------------------------------------------- #

class MailState:
    """Журнал по каждому лиду: канал, sent_id, статус, история попыток."""

    def __init__(self, path) -> None:
        self.path = Path(path)
        try:
            self.data: dict = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self.data = {}

    def get(self, lead_id) -> dict:
        return self.data.setdefault(str(lead_id), {})

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except OSError:
            pass

    def reset_failed(self) -> int:
        count = 0
        for item in self.data.values():
            if item.get("status") == "failed":
                item["status"] = ""
                item["attempts"] = []
                count += 1
        if count:
            self.save()
        return count


# --------------------------------------------------------------------------- #
#  ИИ
# --------------------------------------------------------------------------- #

def fallback_text(row: dict, idea: str) -> str:
    """Текст без ИИ — чтобы предпросмотр работал до настройки ключа."""
    name = (row.get("name") or "вашей компании").strip()
    idea = (idea or "").strip() or "сделаю простой сайт под ваши услуги"
    return (
        f"{name}: {idea}. "
        "У вас пока нет сайта, а клиенты ищут в интернете — могу показать, как это выглядит. "
        "Ответьте, если интересно."
    )


def ai_generate(settings: dict, row: dict, idea: str) -> str:
    """Один запрос к OpenAI-совместимому API (OpenRouter / Groq / Ollama и т.п.)."""
    import urllib.request

    base = (settings.get("ai_url") or "").rstrip("/")
    key = settings.get("ai_key") or ""
    model = settings.get("ai_model") or ""
    if not base or not model:
        return fallback_text(row, idea)

    system = (
        "Ты пишешь короткое деловое сообщение потенциальному клиенту. "
        "Строго без приветствий, без «здравствуйте», без воды и шаблонов. "
        "2-4 предложения: конкретная выгода и один вопрос в конце. Пиши на русском."
    )
    style = (settings.get("style") or "").strip()
    if style:
        system += "\nПравила пользователя:\n" + style

    user = (
        f"Организация: {row.get('name', '')}\n"
        f"Ниша: {row.get('rubric', '')}\n"
        f"Город: {row.get('city', '')}\n"
        f"Адрес: {row.get('address', '')}\n"
        f"Телефон: {row.get('phone', '')}\n"
        f"Идея предложения: {idea}"
    )
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": 0.7,
        "max_tokens": 400,
    }
    req = urllib.request.Request(
        base + "/chat/completions",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        data = json.loads(resp.read().decode("utf-8", "ignore"))
    return (data["choices"][0]["message"]["content"] or "").strip()


# --------------------------------------------------------------------------- #
#  Почта
# --------------------------------------------------------------------------- #

def _send_email(settings: dict, to: str, text: str) -> None:
    host = settings.get("smtp_host") or ""
    user = settings.get("smtp_user") or ""
    pwd = settings.get("smtp_pass") or ""
    if not (host and user and pwd and to):
        raise RuntimeError("SMTP не настроен (host/user/pass)")
    port = int(settings.get("smtp_port") or 465)
    frm = settings.get("smtp_from") or user
    msg = EmailMessage()
    msg["From"] = frm
    msg["To"] = to
    msg["Subject"] = settings.get("mail_subject") or "Предложение"
    msg.set_content(text)
    if port == 465:
        with smtplib.SMTP_SSL(host, port, timeout=30) as srv:
            srv.login(user, pwd)
            srv.send_message(msg)
    else:
        with smtplib.SMTP(host, port, timeout=30) as srv:
            srv.starttls()
            srv.login(user, pwd)
            srv.send_message(msg)


# --------------------------------------------------------------------------- #
#  Telegram (user-аккаунт, Telethon.sync)
# --------------------------------------------------------------------------- #

def _ensure_loop() -> None:
    """В фоновой нити event loop нет — Telethon.sync упадёт без него."""
    try:
        asyncio.get_event_loop()
    except RuntimeError:
        asyncio.set_event_loop(asyncio.new_event_loop())


def _tg_session_path(settings: dict) -> str:
    name = (settings.get("tg_session_file") or "").strip() or "session_telegram"
    path = Path(name)
    return str(path if path.is_absolute() else path.resolve())


def _tg_call(settings: dict, fn):
    """Единая точка входа в Telegram: loop на поток, lock на сессию, connect/disconnect."""
    from telethon.sync import TelegramClient

    api_id = int(str(settings.get("api_id") or "0").strip() or 0)
    api_hash = (settings.get("api_hash") or "").strip()
    if not (api_id and api_hash):
        raise RuntimeError("Telegram: заполни api_id и api_hash")

    with TG_LOCK:
        _ensure_loop()
        client = TelegramClient(_tg_session_path(settings), api_id, api_hash)
        client.connect()
        try:
            if not client.is_user_authorized():
                raise RuntimeError("Telegram: сначала нажми «Войти в TG» в настройках")
            return fn(client)
        finally:
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass


def _tg_send(client, contact: str, kind: str, text: str) -> dict:
    from telethon.tl.functions.contacts import ImportContactsRequest
    from telethon.tl.types import InputPhoneContact

    if kind == "phone":
        res = client(ImportContactsRequest([
            InputPhoneContact(client_id=0, phone=contact, first_name="lead", last_name=""),
        ]))
        users = [u for u in (getattr(res, "users", None) or []) if getattr(u, "id", None)]
        if not users:
            raise RuntimeError(f"TG: телефон {contact} не зарегистрирован в Telegram")
        entity = users[0]
    else:
        entity = client.get_entity(contact)

    sent = client.send_message(entity, text)
    if isinstance(sent, (list, tuple)):
        sent = sent[0]
    msg_id = getattr(sent, "id", None)
    if msg_id is None:
        raise RuntimeError("TG: сообщение не отправлено")
    return {"msg_id": int(msg_id), "peer": int(getattr(entity, "id", 0) or 0)}


def tg_send(settings: dict, contact: str, kind: str, text: str) -> dict:
    return _tg_call(settings, lambda c: _tg_send(c, contact, kind, text))


def tg_authorized(settings: dict) -> bool:
    try:
        return bool(_tg_call(settings, lambda c: True))
    except Exception:  # noqa: BLE001
        return False


def gui_auth(settings: dict, phone: str, parent=None, on_log=None) -> None:
    """Вход в TG из главного потока Tk: код и 2FA спрашиваются через simpledialog."""
    from telethon.errors import SessionPasswordNeededError
    from telethon.sync import TelegramClient
    from tkinter import simpledialog

    api_id = int(str(settings.get("api_id") or "0").strip() or 0)
    api_hash = (settings.get("api_hash") or "").strip()
    phone = (phone or "").strip()
    if not (api_id and api_hash and phone):
        raise RuntimeError("нужны api_id, api_hash и номер телефона")

    with TG_LOCK:
        _ensure_loop()
        client = TelegramClient(_tg_session_path(settings), api_id, api_hash)
        client.connect()
        try:
            if client.is_user_authorized():
                if on_log:
                    on_log("TG: аккаунт уже привязан", "ok")
                return
            client.send_code_request(phone)
            code = simpledialog.askstring(
                "Telegram — код", f"Код из Telegram для {phone}:", parent=parent)
            if not code:
                raise RuntimeError("код не введён")
            try:
                client.sign_in(phone=phone, code=code.strip())
            except SessionPasswordNeededError:
                pwd = simpledialog.askstring(
                    "Telegram — 2FA", "Облачный пароль:", parent=parent, show="*")
                if not pwd:
                    raise RuntimeError("включён 2FA, пароль не введён")
                client.sign_in(password=pwd)
            if not client.is_user_authorized():
                raise RuntimeError("вход не завершён — попробуй ещё раз")
        finally:
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass


def tg_logout(settings: dict) -> str:
    """Удаляет файл сессии. Возвращает строку для лога."""
    from telethon.sync import TelegramClient

    api_id = int(str(settings.get("api_id") or "0").strip() or 0)
    api_hash = (settings.get("api_hash") or "").strip()
    if not (api_id and api_hash):
        return "TG: нет api_id/api_hash"
    with TG_LOCK:
        _ensure_loop()
        client = TelegramClient(_tg_session_path(settings), api_id, api_hash)
        try:
            client.connect()
            client.log_out()
        except Exception:  # noqa: BLE001
            pass
        finally:
            try:
                client.disconnect()
            except Exception:  # noqa: BLE001
                pass
        try:
            Path(_tg_session_path(settings) + ".session").unlink(missing_ok=True)
        except OSError:
            pass
    return "TG: сессия удалена"


# --------------------------------------------------------------------------- #
#  VK (user-токен, vk_api, v=5.199)
# --------------------------------------------------------------------------- #

class VKClient:
    """VK через vk_api с правами user: пишет первым по ID/домену, видит out_read/in_read."""

    def __init__(self, token: str) -> None:
        try:
            import vk_api
        except ImportError:
            raise RuntimeError("нет vk-api: pip install vk-api>=11.9.9")
        if not token:
            raise RuntimeError("нет VK token")
        self.vk = vk_api.VkApi(token=token).get_api()

    def peer_id(self, screen: str) -> int:
        """'company' -> -123456 для группы, 'id123' -> 123 для профиля."""
        screen = str(screen).strip()
        if re.fullmatch(r"-?\d+", screen):
            return int(screen)
        res = self.vk.utils.resolveScreenName(screen_name=screen)
        if not res:
            raise RuntimeError(f"VK: не найден {screen}")
        oid = int(res["object_id"])
        return -oid if res.get("type") == "group" else oid

    def send(self, screen: str, text: str) -> dict:
        pid = self.peer_id(screen)
        resp = self.vk.messages.send(
            peer_id=pid, message=text, random_id=random.randint(1, 2**31))
        msg_id = resp if isinstance(resp, int) else (resp or {}).get("last_message_id")
        if msg_id is None:
            raise RuntimeError("VK: не получил id отправленного сообщения")
        return {"msg_id": int(msg_id), "peer": pid}

    def status(self, pid: int, sent_id: int) -> dict:
        """{'seen': bool, 'replied': bool} по out_read/in_read."""
        try:
            res = self.vk.messages.getConversationsById(peer_ids=pid)
        except Exception as exc:  # noqa: BLE001
            raise RuntimeError(f"VK status: {exc}") from exc
        items = (res or {}).get("items") or []
        if not items:
            return {"seen": False, "replied": False}
        conv = items[0]
        out_read = int(conv.get("out_read") or 0)
        in_read = int(conv.get("in_read") or 0)
        last = conv.get("last_message") or {}
        last_id = int(last.get("id") or 0)
        # ответ = последнее сообщение в диалоге новее нашего и пришло не от нас
        from_peer = last.get("from_id") in (pid, -pid)
        replied = last_id > int(sent_id or 0) and (from_peer or in_read > out_read)
        seen = bool(sent_id) and out_read >= int(sent_id)
        return {"seen": seen, "replied": replied}

    def last_incoming(self, pid: int) -> str:
        try:
            hist = self.vk.messages.getHistory(peer_id=pid, count=1)
        except Exception:  # noqa: BLE001
            return ""
        items = (hist or {}).get("items") or []
        if not items:
            return ""
        msg = items[0]
        if msg.get("from_id") in (pid, -pid):
            return str(msg.get("text") or "")
        return ""


# --------------------------------------------------------------------------- #
#  MAX (url + token)
# --------------------------------------------------------------------------- #

def _send_max(settings: dict, target: str, text: str) -> None:
    import urllib.request

    url = settings.get("max_url") or ""
    token = settings.get("max_token") or ""
    if not (url and token):
        raise RuntimeError("MAX API не настроен (url/token)")
    payload = json.dumps({"to": target, "text": text}).encode()
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        resp.read()


# --------------------------------------------------------------------------- #
#  Отправка: единая точка
# --------------------------------------------------------------------------- #

_CLIENTS: dict = {}
_CLIENTS_LOCK = threading.Lock()


def _vk_client(settings: dict) -> VKClient:
    token = settings.get("vk_token") or ""
    with _CLIENTS_LOCK:
        client = _CLIENTS.get(("vk", token))
        if client is None:
            client = VKClient(token)
            _CLIENTS[("vk", token)] = client
        return client


def dispatch(channel: str, settings: dict, contact: str, kind: str, text: str) -> dict:
    """Отправляет в один канал. Возвращает {'msg_id', 'peer'} для проверки прочтения."""
    if settings.get("dry_run"):
        return {"msg_id": None, "peer": None}
    if channel == "email":
        _send_email(settings, contact, text)
        return {"msg_id": None, "peer": None}
    if channel == "telegram":
        return tg_send(settings, contact, kind, text)
    if channel == "vk":
        return _vk_client(settings).send(contact, text)
    if channel == "max":
        _send_max(settings, contact, text)
        return {"msg_id": None, "peer": None}
    raise RuntimeError(f"неизвестный канал: {channel}")


# --------------------------------------------------------------------------- #
#  Генерация текстов
# --------------------------------------------------------------------------- #

def generate_texts(leads: list[dict], settings: dict, state: MailState,
                   on_log, on_progress, stop_event) -> int:
    idea = settings.get("idea") or ""
    done = 0
    for index, row in enumerate(leads, 1):
        if stop_event.is_set():
            break
        lead_id = row_id(row)
        st = state.get(lead_id)
        if st.get("status") in STATUSES:
            continue
        try:
            text = ai_generate(settings, row, idea)
        except Exception as exc:  # noqa: BLE001 - один лид не рушит прогон
            text = fallback_text(row, idea)
            on_log(f"ИИ: {row.get('name', '')} — {exc}; взят шаблон", "warn")
        st["text"] = text
        state.save()
        done += 1
        on_progress(f"тексты {index}/{len(leads)}")
    return done


# --------------------------------------------------------------------------- #
#  Основной цикл отправки
# --------------------------------------------------------------------------- #

class Mailer:
    def __init__(self, leads: list[dict], settings: dict, state: MailState,
                 on_log, on_event, pool_file=None) -> None:
        self.leads = leads
        self.settings = settings
        self.state = state
        self.on_log = on_log
        self.on_event = on_event
        self.pool_file = pool_file

    # -- один шаг: максимум одно сообщение -------------------------------- #
    def step(self) -> bool:
        followup = timedelta(days=_num(self.settings.get("followup_days"), 5))
        now = datetime.now()

        for row in self.leads:
            lead_id = row_id(row)
            st = self.state.get(lead_id)
            status = st.get("status") or ""
            if status in STATUSES:
                continue

            avail = channels_of(row)
            if not avail:
                st["status"] = "closed"
                st["closed_at"] = now.isoformat(timespec="seconds")
                self.state.save()
                self.on_log(f"закрыт (нет каналов): {row.get('name', '')}", "warn")
                continue

            attempts = st.get("attempts") or []
            used = {a.get("channel") for a in attempts}

            if not attempts:
                for ch in PRIORITY:
                    if ch not in avail:
                        continue
                    text = st.get("text") or fallback_text(row, self.settings.get("idea") or "")
                    if self._try_send(row, st, ch, avail[ch], text):
                        return True
                st["status"] = "failed"
                self.state.save()
                continue

            if status == "awaiting":
                sent_at = _parse_dt(st.get("sent_at"))
                if sent_at is None or now - sent_at < followup:
                    continue
                nxt = next((ch for ch in PRIORITY if ch in avail and ch not in used), None)
                if nxt is None:
                    st["status"] = "closed"
                    st["closed_at"] = now.isoformat(timespec="seconds")
                    self.state.save()
                    self.on_log(f"закрыт (5+5 дней): {row.get('name', '')}", "warn")
                    continue
                prev = attempts[-1].get("channel", "")
                prefix = f"Уже писал вам в {channel_label(prev)}. "
                text = prefix + (st.get("text") or "")
                if self._try_send(row, st, nxt, avail[nxt], text):
                    return True
                continue

        return False

    def _try_send(self, row, st, ch, contact, text) -> bool:
        lead_id = row_id(row)
        try:
            info = dispatch(ch, self.settings, contact[0], contact[1], text)
        except Exception as exc:  # noqa: BLE001
            self.on_log(f"{row.get('name', '')} / {channel_label(ch)}: {exc}", "error")
            st.setdefault("attempts", []).append({
                "channel": ch, "target": contact[0],
                "at": datetime.now().isoformat(timespec="seconds"), "ok": False,
            })
            self.state.save()
            return False

        attempts = st.setdefault("attempts", [])
        attempts.append({
            "channel": ch, "target": contact[0],
            "at": datetime.now().isoformat(timespec="seconds"), "ok": True,
        })
        st.update({
            "channel": ch,
            "target": contact[0],
            "sent_id": info.get("msg_id"),
            "peer": info.get("peer"),
            "sent_at": datetime.now().isoformat(timespec="seconds"),
            "status": "awaiting",
        })
        st.pop("seen_at", None)
        self.state.save()
        if self.pool_file:
            try:
                pool_mark_sent(self.pool_file, lead_id, ch)
            except PoolBusy as exc:
                self.on_log(f"пул: {exc}", "warn")
        self.on_log(f"→ {row.get('name', '')} · {channel_label(ch)} · {contact[0]}", "ok")
        self.on_event({"lead": lead_id, "channel": ch, "target": contact[0], "text": text})
        return True

    # -- бесконечный цикл -------------------------------------------------- #
    def run_forever(self, stop_event) -> None:
        pacing = max(5.0, _num(self.settings.get("pacing"), 60))
        self.on_log("рассылка запущена", "ok")
        while not stop_event.is_set():
            try:
                sent = self.step()
            except Exception as exc:  # noqa: BLE001
                self.on_log(f"шаг: {exc}", "error")
                sent = False
            if sent:
                stop_event.wait(pacing)
            else:
                stop_event.wait(20)
        self.on_log("рассылка остановлена", "warn")


# --------------------------------------------------------------------------- #
#  Ответы клиентов + статус прочтения -> пересылка в MAX
# --------------------------------------------------------------------------- #

def _parse_dt(value) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


class Poller:
    """Слушает входящие и статусы прочтения: TG (dialogs), VK, IMAP.

    Порядок проверки для каждого лида: СНАЧАЛА ответ (входящее после нашего
    сообщения), только потом прочтение. Иначе ответ никогда не поймать —
    ответив, человек по определению прочитал.
    """

    def __init__(self, leads: list[dict], settings: dict, state: MailState,
                 on_log, stop_event, pool_file=None) -> None:
        self.leads = leads
        self.settings = settings
        self.state = state
        self.on_log = on_log
        self.stop_event = stop_event
        self.pool_file = pool_file

    # -- пометки ----------------------------------------------------------- #
    def _mark_seen(self, row: dict) -> None:
        lead_id = row_id(row)
        st = self.state.get(lead_id)
        if st.get("status") != "awaiting":
            return  # уже replied/stopped/closed — не понижаем
        st["status"] = "seen"
        st["seen_at"] = datetime.now().isoformat(timespec="seconds")
        self.state.save()
        if self.pool_file:
            try:
                pool_mark_status(self.pool_file, lead_id, "seen")
            except PoolBusy as exc:
                self.on_log(f"пул: {exc}", "warn")
        self.on_log(f"прочитано: {row.get('name', '')}", "warn")

    def _mark_replied(self, row: dict, text: str) -> None:
        lead_id = row_id(row)
        st = self.state.get(lead_id)
        if st.get("status") in ("replied", "stopped", "closed"):
            return
        st["status"] = "replied"
        st["replied_at"] = datetime.now().isoformat(timespec="seconds")
        st["reply"] = text[:500]
        self.state.save()
        if self.pool_file:
            try:
                pool_mark_status(self.pool_file, lead_id, "replied")
            except PoolBusy as exc:
                self.on_log(f"пул: {exc}", "warn")
        self.on_log(f"ответ: {row.get('name', '')} — {text[:80]}", "ok")
        self._forward(row, text)

    def _forward(self, row: dict, text: str) -> None:
        target = self.settings.get("max_self") or ""
        if not target:
            return
        try:
            dispatch("max", self.settings, target, "username",
                     f"Ответ от {row.get('name', '')}: {text[:1500]}")
        except Exception as exc:  # noqa: BLE001
            self.on_log(f"пересылка в MAX: {exc}", "error")

    # -- выборка лидов в работе ------------------------------------------- #
    def _jobs(self, channel: str) -> dict:
        """{peer: (row, sent_id, status)} для лидов с отправленным сообщением."""
        jobs: dict = {}
        for row in self.leads:
            st = self.state.get(row_id(row))
            if st.get("channel") != channel:
                continue
            if st.get("status") not in ("awaiting", "seen"):
                continue
            sent_id = int(st.get("sent_id") or 0)
            peer = int(st.get("peer") or 0)
            if not sent_id:
                continue
            jobs[peer] = (row, sent_id, st.get("status"))
        return jobs

    # -- Telegram ---------------------------------------------------------- #
    def _poll_telegram(self) -> None:
        jobs = self._jobs("telegram")
        if not jobs:
            return

        def work(client):
            events = []
            dialogs = {}
            try:
                for d in client.get_dialogs(limit=300):
                    entity = getattr(d, "entity", None)
                    if entity is not None:
                        dialogs[int(entity.id)] = d
            except Exception as exc:  # noqa: BLE001
                self.on_log(f"TG dialogs: {exc}", "warn")

            for peer, (row, sent_id, status) in jobs.items():
                # 1) ответ — приоритетная проверка
                reply = ""
                try:
                    for msg in client.get_messages(peer, limit=5) or []:
                        if getattr(msg, "out", True):
                            continue
                        if int(getattr(msg, "id", 0) or 0) > sent_id:
                            reply = (msg.message or "").strip()
                            break
                except Exception as exc:  # noqa: BLE001
                    self.on_log(f"TG messages {peer}: {exc}", "warn")
                if reply:
                    events.append(("replied", row, reply))
                    continue
                # 2) только если ответа нет — прочтение
                if status != "awaiting":
                    continue
                d = dialogs.get(peer)
                if d is None:
                    continue
                if int(getattr(d, "read_outbox_max_id", 0) or 0) >= sent_id:
                    events.append(("seen", row, ""))
            return events

        try:
            events = _tg_call(self.settings, work)
        except Exception as exc:  # noqa: BLE001
            self.on_log(f"ответы TG: {exc}", "warn")
            return
        for kind, row, text in events:
            if kind == "replied":
                self._mark_replied(row, text)
            else:
                self._mark_seen(row)

    # -- VK ---------------------------------------------------------------- #
    def _poll_vk(self) -> None:
        jobs = self._jobs("vk")
        if not jobs:
            return
        try:
            vk = _vk_client(self.settings)
        except Exception as exc:  # noqa: BLE001
            self.on_log(f"VK poll: {exc}", "warn")
            return

        for pid, (row, sent_id, status) in jobs.items():
            try:
                st = vk.status(pid, sent_id)
            except Exception as exc:  # noqa: BLE001
                self.on_log(f"VK status {pid}: {exc}", "warn")
                continue
            # 1) ответ
            if st.get("replied"):
                self._mark_replied(row, vk.last_incoming(pid))
                continue
            # 2) прочтение
            if status == "awaiting" and st.get("seen"):
                self._mark_seen(row)

    # -- Почта ------------------------------------------------------------- #
    def _poll_email(self) -> None:
        host = self.settings.get("smtp_host") or ""
        user = self.settings.get("smtp_user") or ""
        pwd = self.settings.get("smtp_pass") or ""
        if not (host and user and pwd):
            return
        conn = None
        try:
            conn = imaplib.IMAP4_SSL(host)
            conn.login(user, pwd)
            conn.select("INBOX")
            _, ids = conn.search(None, "UNSEEN")
            for num in (ids[0].split() if ids and ids[0] else []):
                _, parts = conn.fetch(num, "(RFC822)")
                if not parts or not parts[0]:
                    continue
                msg = message_from_bytes(parts[0][1])
                sender = str(msg.get("From") or "")
                match = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", sender)
                if not match:
                    continue
                row = next((r for r in self.leads
                            if str(r.get("email") or "").lower() == match.group(0).lower()), None)
                if not row:
                    continue
                body = ""
                if msg.is_multipart():
                    for part in msg.walk():
                        if part.get_content_type() == "text/plain":
                            body = part.get_payload(decode=True).decode("utf-8", "ignore")
                            break
                else:
                    body = msg.get_payload(decode=True).decode("utf-8", "ignore")
                self._mark_replied(row, body.strip()[:1500])
        except Exception as exc:  # noqa: BLE001
            self.on_log(f"почта: {exc}", "warn")
        finally:
            if conn is not None:
                try:
                    conn.logout()
                except Exception:  # noqa: BLE001
                    pass

    # -- цикл -------------------------------------------------------------- #
    def run_forever(self) -> None:
        while not self.stop_event.is_set():
            for name, fn in (("TG", self._poll_telegram),
                             ("VK", self._poll_vk),
                             ("почта", self._poll_email)):
                if self.stop_event.is_set():
                    break
                try:
                    fn()
                except Exception as exc:  # noqa: BLE001
                    self.on_log(f"ответы {name}: {exc}", "warn")
            self.stop_event.wait(60)
