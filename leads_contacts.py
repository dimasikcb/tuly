"""Контакты лида: ник ↔ ссылка, каналы, как показывать в таблице.

Один источник правды для всех преобразований контактов. Раньше логика
дублировалась: `leads2gis.messenger_links` собирал ссылки сам, `leads_mail`
делал то же через `_to_link`/`channels_of`, и правила расходились.

Модуль ничего не знает про сеть, GUI и файлы — только чистые функции над dict.
"""

from __future__ import annotations

import re

CHANNEL_TITLE = {"max": "MAX", "telegram": "Telegram", "vk": "ВК", "email": "Почта"}
TARGET_TITLE = {**CHANNEL_TITLE, "phone": "Телефон"}
CHANNELS = ("telegram", "max", "vk", "email")


def num(value, default: float = 0.0) -> float:
    """Число из строки с запятой; мусор → default."""
    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return default


def phone_to_e164(phone: str) -> str:
    digits = re.sub(r"[^\d+]", "", str(phone).strip())
    if digits.startswith("+"):
        return digits
    if len(digits) == 11 and digits[0] in "78":
        return "+7" + digits[1:]
    if len(digits) == 10:
        return "+7" + digits
    return "+" + digits


def is_phone_like(value: str) -> bool:
    """«+7 900 111-22-33» — телефон, а не ник."""
    text = str(value or "").strip()
    digits = re.sub(r"\D", "", text)
    return bool(text) and bool(re.fullmatch(r"[\d\s\-()+]+", text)) and len(digits) >= 7


def pick_contact(row: dict, channel: str) -> str:
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


def to_link(channel: str, value) -> str:
    """Ник/e-mail → готовая ссылка. Уже ссылку возвращает как есть."""
    value = str(value or "").strip()
    if not value:
        return ""
    if value.lower().startswith(("http://", "https://", "mailto:")):
        return value
    if channel == "telegram":
        if is_phone_like(value):
            return "https://t.me/+" + re.sub(r"\D", "", value)  # диплинк по номеру
        return "https://t.me/" + value.lstrip("@").strip("/")
    if channel == "max":
        return "https://max.ru/" + value.lstrip("@").strip("/")
    if channel == "vk":
        return "https://vk.com/" + value.strip("/")
    if channel == "email":
        return "mailto:" + value
    return ""


def from_link(channel: str, value) -> str:
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


def channels_of(row: dict) -> dict:
    """Доступные у лида каналы: {канал: (contact, kind)}.

    В пуле контакты лежат ссылками, а отправка работает с ником — поэтому
    значение сначала приводится к нику.
    """
    out: dict[str, tuple[str, str]] = {}
    mx = from_link("max", pick_contact(row, "max"))
    if mx:
        out["max"] = (mx, "username")
    tg = from_link("telegram", pick_contact(row, "telegram"))
    if tg:
        if is_phone_like(tg):
            out["telegram"] = (phone_to_e164(tg), "phone")
        else:
            out["telegram"] = (tg.lstrip("@"), "username")
    vk = from_link("vk", pick_contact(row, "vk"))
    if vk:
        out["vk"] = (vk, "screen")
    em = from_link("email", pick_contact(row, "email"))
    if em:
        out["email"] = (em, "email")
    return out


def contact_display(channel: str, contact: tuple[str, str]) -> str:
    value, kind = contact
    if channel == "telegram" and kind == "username":
        return "@" + value
    return value


def channel_label(channel: str) -> str:
    """Человеческое имя канала для логов и статусов."""
    return CHANNEL_TITLE.get(channel, channel)


def contact_link(channel: str, contact: tuple[str, str]) -> str:
    """Кликабельная ссылка на контакт; "" если ссылку не построить."""
    value, kind = contact
    value = str(value).strip()
    if not value:
        return ""
    if value.lower().startswith(("http://", "https://", "mailto:")):
        return value
    if channel == "telegram":
        if kind == "phone":
            digits = re.sub(r"\D", "", value)
            return "https://t.me/+" + digits if digits else ""
        return "https://t.me/" + value.lstrip("@").strip("/")
    if channel == "max":
        return "https://max.ru/" + value.lstrip("@").strip("/")
    if channel == "vk":
        return "https://vk.com/" + value.strip("/")
    if channel == "email":
        return "mailto:" + value
    return ""


def messenger_links(lead: dict) -> dict[str, str]:
    """Кликабельные ссылки на контакты лида: telegram / max / vk / email.

    Заменяет прежнюю копию в `leads2gis`: правила теперь одни на проект.
    """
    out: dict[str, str] = {}
    for channel in CHANNELS:
        link = to_link(channel, pick_contact(lead, channel))
        if link:
            out[channel] = link
    return out


def format_phone(phone) -> str:
    """+7 906 026-34-84; «—», если цифр нет."""
    digits = re.sub(r"\D", "", str(phone or ""))
    if len(digits) == 11 and digits[0] in "78":
        return f"+7 {digits[1:4]} {digits[4:7]}-{digits[7:9]}-{digits[9:11]}"
    return str(phone or "—") if digits else "—"


def short(url: str) -> str:
    """Ссылка без схемы — влезает в колонку и всё равно читается как ссылка."""
    text = str(url or "").strip()
    if text.lower().startswith("mailto:"):
        return text[7:]
    return re.sub(r"^https?://", "", text).rstrip("/")


def same_number(a, b) -> bool:
    da, db = re.sub(r"\D", "", str(a or "")), re.sub(r"\D", "", str(b or ""))
    return bool(da) and bool(db) and da[-10:] == db[-10:]


def targets_of(row: dict) -> list[tuple[str, str, str]]:
    """Все точки контакта по приоритету: (канал, что показать, куда вести).

    Telegram-диплинк на тот же номер (t.me/+79060263484) — это телефон, а не
    отдельный канал: показываем номер один раз, а ссылка ведёт в чат.
    """
    out: list[tuple[str, str, str]] = []
    phone = format_phone(row.get("phone"))
    tg = to_link("telegram", row.get("telegram"))
    tg_is_phone = bool(tg) and phone != "—" and same_number(tg, row.get("phone"))
    if tg and not tg_is_phone:
        out.append(("telegram", short(tg), tg))
    for channel in ("vk", "max", "email"):
        link = to_link(channel, row.get(channel))
        if link:
            out.append((channel, short(link), link))
    if phone != "—":
        out.append(("phone", phone, tg if tg_is_phone else ""))
    return out
