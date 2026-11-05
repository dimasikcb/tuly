#!/usr/bin/env python3
"""leads2gis — парсер лидов из 2GIS одним файлом.

Собирает организации из поисковой выдачи 2GIS, вытаскивает контакты и ранжирует
по приоритету для аутрича по сайтам/приложениям/автоматизации.

Запуск:
    python leads2gis.py -c moscow -q "барбершоп" --no-website -o leads.csv
    python leads2gis.py -u "https://2gis.ru/kazan/search/кофейни" --no-website

Зависимости: requests, selectolax. Playwright — только для --browser.
"""

from __future__ import annotations

import argparse
import base64
import csv
import json
import os
import random
import re
import subprocess
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import quote, unquote, urlparse

from leads_contacts import messenger_links as _contact_links


def _load_deps(install: bool = False) -> bool:
    """Импортирует зависимости; при install=True доставляет их в текущий Python."""
    global requests, HTTPAdapter, Retry, LexborHTMLParser
    try:
        import requests
        from requests.adapters import HTTPAdapter
        from urllib3.util.retry import Retry
        from selectolax.lexbor import LexborHTMLParser
        return True
    except ImportError:
        if not install:
            return False

    print("Первый запуск: ставлю зависимости (requests, selectolax)...")
    try:
        subprocess.check_call(
            [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
             "requests", "selectolax"]
        )
    except (subprocess.CalledProcessError, OSError):
        sys.exit("Не смог поставить зависимости. Вручную: pip install requests selectolax")

    import requests
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    from selectolax.lexbor import LexborHTMLParser
    return True


DEPS_OK = _load_deps(install=False)

BASE = "https://2gis.ru"
RATINGS_API = "https://public-api.reviews.2gis.com/3.0/objects/ratings/light"
RATINGS_KEY = "6e7e1929-4ea9-4a5d-8c05-d601860389bd"

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/141.0.0.0 Safari/537.36",
]

FIRM_RE = re.compile(r"/([a-z0-9_-]+)/firm/(\d+)")
PAGE_RE = re.compile(r"/page/(\d+)")
CAPTCHA_HINT = ("captcha.2gis", "2GIS Captcha", "Проверка на робота")

# 2ГИС отдаёт 12 организаций на страницу выдачи — в интерфейсе просим карточки,
# а не страницы, и считаем страницы сами.
CARDS_PER_PAGE = 12


def pages_for(cards) -> int:
    """Сколько страниц выдачи нужно, чтобы проверить N карточек (ceil(N / 12))."""
    try:
        count = max(1, int(cards))
    except (TypeError, ValueError):
        count = CARDS_PER_PAGE
    return max(1, -(-count // CARDS_PER_PAGE))

# --- извлечение полей -------------------------------------------------------

_LABEL = r"[a-z0-9\u0400-\u04ff](?:[a-z0-9\u0400-\u04ff-]{0,61}[a-z0-9\u0400-\u04ff])?"
_TLD = r"[a-z\u0400-\u04ff]{2,24}"
DOMAIN_RE = re.compile(rf"^(?:https?://)?(?:www\.)?({_LABEL}(?:\.{_LABEL})*\.{_TLD})(?:/.*)?$", re.I)
EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
COORD_RE = re.compile(r"\|(-?\d{2,3}\.\d+),(-?\d{2,3}\.\d+)")
REVIEWS_LINE_RE = re.compile(r"^(\d[\d\s]*)\s*(оценок|оценки|оценка|отзыв\w*)", re.I)
RATING_LINE_RE = re.compile(r"^([1-5](?:[.,]\d)?)$")
TIME_RE = re.compile(r"\d{1,2}:\d{2}")
STREET_RE = re.compile(
    r"(ул\.|улица|проспект|пр-т|переулок|набережная|бульвар|шоссе|проезд|площадь|микрорайон|квартал|д\.\s*\d|\d+\s*ст\d)",
    re.I,
)
NOISE_EMAIL_DOMAINS = ("2gis.ru", "2gis.com", "example.com")
SOCIAL_DOMAINS = ("vk.com", "t.me", "wa.me", "max.ru", "ok.ru", "youtube.com", "instagram.com", "tiktok.com", "dzen.ru")
SITE_LINK_HINTS = ("перейти на сайт", "на сайт", "сайт компании", "официальный сайт")
HEADER_NOISE = {"сохранить", "отправить", "маршрут", "реклама", "контакты", "инфо"}
SOCIAL_HINTS = {
    "вконтакте": "vk",
    "telegram": "telegram",
    "телеграм": "telegram",
    "instagram": "instagram",
    "youtube": "youtube",
    "одноклассники": "ok",
    "tiktok": "tiktok",
    "dzen": "dzen",
    "дзен": "dzen",
}

# --- ссылки на контакты ------------------------------------------------------


def messenger_links(lead: dict) -> dict[str, str]:
    """Кликабельные ссылки на контакты лида: telegram / max / vk / email.

    Тонкая обёртка: правила построения ссылок живут в `leads_contacts`, чтобы
    парсер и рассылка не расходились в трактовке телефона/ника.
    """
    return _contact_links(lead)



COLUMNS = [
    "score", "tier", "name", "rubric", "phone", "phones", "email", "emails",
    "website", "websites", "has_website",     "socials", "messengers", "telegram",
    "max", "vk", "telegram_url", "max_url", "vk_url", "email_url", "address",
    "city", "rating",
    "reviews", "status", "hours", "lat", "lon", "description", "query", "url",
    "id", "reason",
]

HIGH_VALUE = (
    "стоматолог", "клиник", "медицин", "салон", "барбершоп", "парикмахер",
    "автосервис", "шиномонтаж", "автошкол", "ремонт", "строитель", "мебел",
    "недвижим", "агентств", "юрист", "бухгалтер", "косметолог", "фитнес",
    "спорт", "ресторан", "кафе", "доставка", "пекарн", "кондитер", "гостиниц",
    "отель", "туризм", "турагент", "цвет", "аптек", "детск", "образова",
    "курсы", "школ", "фотостуди", "свадеб", "груминг", "ветеринар",
)
CHAIN_HINTS = ("сеть", "франшиза", "филиал", "холдинг", "гипермаркет", "тц ", "торговый центр")


class BlockedError(RuntimeError):
    """2GIS отдал captcha вместо выдачи."""


def _clean(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", value.replace("\xa0", " ")).strip()


def _decode_2gis_link(href: str) -> str | None:
    """Разворачивает base64-хвост ссылок вида link.2gis.ru/4.2/<HEX>/<base64>."""
    match = re.search(r"/4\.\d+/[0-9A-Fa-f]+/([A-Za-z0-9+/=_-]+)", href)
    if not match:
        return None
    payload = match.group(1).replace("-", "+").replace("_", "/")
    payload += "=" * (-len(payload) % 4)
    try:
        text = base64.b64decode(payload).decode("utf-8", "ignore")
    except (ValueError, TypeError):
        return None
    return next((line.strip() for line in text.splitlines() if line.strip()), None)


def _as_domain(text: str) -> str | None:
    match = DOMAIN_RE.match(text.strip())
    if not match:
        return None
    domain = match.group(1).lower()
    if domain.endswith(NOISE_EMAIL_DOMAINS):
        return None
    if any(domain == d or domain.endswith("." + d) for d in SOCIAL_DOMAINS):
        return None
    return domain


def _social_of(text: str) -> str | None:
    low = text.lower()
    for hint, tag in SOCIAL_HINTS.items():
        if hint in low:
            return tag
    return None


def _messenger_from_url(url: str) -> tuple[str, str] | None:
    """Из внешнего URL вытаскивает (платформа, контакт) для Telegram / WhatsApp / VK."""
    if not url:
        return None
    low = url.lower()
    if "t.me" in low or "telegram.me" in low or low.startswith("tg://"):
        # t.me/+7916… — диплинк на номер: 2ГИС так отдаёт Telegram без ника
        m = re.search(r"(?:t\.me/|telegram\.me/|tg://resolve\?domain=)(\+?\d{7,15})", low)
        if m:
            return ("telegram", "+" + m.group(1).lstrip("+"))
        m = re.search(r"(?:t\.me/|telegram\.me/|tg://resolve\?domain=)([A-Za-z0-9_]{3,32})", low)
        if m and not m.group(1).isdigit():
            return ("telegram", m.group(1))
        return None
    if "max.ru" in low:
        m = re.search(r"max\.ru/([A-Za-z0-9_.\-]{2,64})", low)
        if m:
            return ("max", m.group(1))
        return None
    if "vk.com" in low or "vk.ru" in low or "vkontakte" in low:
        m = re.search(r"vk\.(?:com|ru)/(?!feed|id$)([A-Za-z0-9_.\-]{2,60})", low)
        if m and m.group(1) not in ("feed", "id", "away", "apps"):
            return ("vk", m.group(1))
        return None
    return None


def _split_messengers(messengers: list[str]) -> dict:
    out = {"max": "", "telegram": "", "vk": ""}
    for item in messengers:
        if ":" in item:
            plat, handle = item.split(":", 1)
            if plat in out and not out[plat]:
                out[plat] = handle
    return out


# Контакты в JSON внутри страницы: у 2ГИС кнопки мессенджеров есть не в вёрстке,
# а в данных — {"type":"telegram","value":"https://t.me/+79168573362"}. Без этого
# карточки, где Telegram заведён на номер, оставались без контактов.
CONTACT_JSON_RE = re.compile(r'\{[^{}]*"type":"(telegram|whatsapp|vk|max|email)"[^{}]*\}')
JSON_VALUE_RE = re.compile(r'"value":"([^"]*)"')


def _json_contacts(html: str) -> list[tuple[str, str]]:
    """[(тип, значение)] из JSON страницы: telegram, whatsapp, vk, max, email."""
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for obj in CONTACT_JSON_RE.finditer(html):
        found = JSON_VALUE_RE.search(obj.group(0))
        if not found:
            continue
        value = found.group(1).strip()
        if not value:
            continue
        item = (obj.group(1), value)
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _header_lines(tree: LexborHTMLParser) -> list[str]:
    """Шапка карточки: 2 родителя над h1 дают плоский список строк."""
    node = tree.css_first("h1")
    if node is None:
        return []
    parent = node.parent
    for _ in range(2):
        if parent is None:
            break
        parent = parent.parent
    if parent is None:
        return [_clean(node.text(separator=" "))]
    raw = parent.text(separator="\n") or ""
    return [_clean(line) for line in raw.split("\n") if _clean(line)]


def _parse_header(lines: list[str]) -> dict:
    out = {"name": "", "rubric": "", "rating": None, "reviews": 0, "description": ""}
    start = 0
    for idx, line in enumerate(lines[:6]):
        if line.lower() not in HEADER_NOISE:
            start = idx
            out["name"] = line
            break
    tail = lines[start + 1 : start + 6]
    for idx, line in enumerate(tail):
        if not out["rubric"] and len(line) <= 45 and not RATING_LINE_RE.match(line) and not REVIEWS_LINE_RE.match(line):
            out["rubric"] = line
            continue
        rating_match = RATING_LINE_RE.match(line)
        if rating_match and out["rating"] is None:
            out["rating"] = float(rating_match.group(1).replace(",", "."))
            continue
        reviews_match = REVIEWS_LINE_RE.match(line)
        if reviews_match and not out["reviews"]:
            out["reviews"] = int(re.sub(r"\D", "", reviews_match.group(1)) or 0)
            nxt = tail[idx + 1] if idx + 1 < len(tail) else ""
            if nxt and len(nxt) <= 120 and nxt.lower() not in HEADER_NOISE:
                out["description"] = nxt
            continue
    return out


def _address(tree: LexborHTMLParser, texts: list[str]) -> str:
    for node in tree.css('a[href*="/geo/"]'):
        value = _clean(node.text(separator=" "))
        if value:
            return value
    for text in texts:
        if STREET_RE.search(text) and len(text) <= 90:
            return text
    for text in texts:
        if re.search(r",\s*[А-ЯЁ][а-яё-]+,\s*\d{6}$", text) and len(text) <= 70:
            return text
    return ""


def parse_firm(html: str, city: str, firm_id: str, query: str = "") -> dict:
    tree = LexborHTMLParser(html)
    header = _parse_header(_header_lines(tree))

    texts = []
    for node in tree.css("div, span, a, p"):
        text = _clean(node.text(separator=" "))
        if text and len(text) <= 140:
            texts.append(text)

    address = _address(tree, texts)

    phones: list[str] = []
    for node in tree.css('a[href^="tel:"]'):
        number = re.sub(r"[^\d+]", "", node.attributes.get("href", "")[4:])
        if len(number) >= 6 and number not in phones:
            phones.append(number)

    websites: list[str] = []
    socials: list[str] = []
    messengers: list[str] = []
    emails: list[str] = []
    site_link = False

    for node in tree.css("a[href], span, div"):
        text = _clean(node.text(separator=" "))
        if not text or len(text) > 90:
            continue
        for email in EMAIL_RE.findall(text):
            if not email.lower().endswith(NOISE_EMAIL_DOMAINS) and email not in emails:
                emails.append(email)

    for node in tree.css("a[href]"):
        text = _clean(node.text(separator=" "))
        label = _clean(node.attributes.get("aria-label"))
        href = node.attributes.get("href", "")

        domain = _as_domain(text)
        if domain:
            if domain not in websites:
                websites.append(domain)
            continue

        if any(hint in text.lower() for hint in SITE_LINK_HINTS):
            site_link = True
            continue

        target = href if href.startswith("http") and "2gis" not in href else (_decode_2gis_link(href) or "")
        social = _social_of(text) or _social_of(label) or _social_of(target)
        if social and social not in socials:
            socials.append(social)
        messenger = _messenger_from_url(target) or _messenger_from_url(text)
        if messenger:
            key = f"{messenger[0]}:{messenger[1]}"
            if key not in messengers:
                messengers.append(key)

    # кнопки мессенджеров 2ГИС держит в JSON страницы, а не в ссылках вёрстки
    for kind, value in _json_contacts(html):
        if kind == "whatsapp":
            continue  # WhatsApp не используем
        if kind == "email":
            email = value.replace("mailto:", "").strip()
            if email and "@" in email and email not in emails:
                emails.append(email)
            continue
        messenger = _messenger_from_url(value)
        if messenger:
            key = f"{messenger[0]}:{messenger[1]}"
            if key not in messengers:
                messengers.append(key)

    lat = lon = None
    coord_match = COORD_RE.search(unquote(html))
    if coord_match:
        lon, lat = float(coord_match.group(1)), float(coord_match.group(2))

    # запасной проход по тексту карточки: t.me/... , wa.me/... , vk.com/... голым текстом
    for t in texts:
        low = t.lower()
        m = re.search(r"t\.me/([A-Za-z0-9_]{3,32})", low)
        if m and not m.group(1).isdigit() and f"telegram:{m.group(1)}" not in messengers:
            messengers.append(f"telegram:{m.group(1)}")
            continue
        m = re.search(r"max\.ru/([A-Za-z0-9_.\-]{2,64})", low)
        if m and f"max:{m.group(1)}" not in messengers:
            messengers.append(f"max:{m.group(1)}")
            continue
        m = re.search(r"vk\.com/([A-Za-z0-9_.\-]{2,60})", low)
        if m and m.group(1) not in ("feed", "id", "away", "apps") and f"vk:{m.group(1)}" not in messengers:
            messengers.append(f"vk:{m.group(1)}")

    status = ""
    hours = ""
    for text in texts:
        low = text.lower()
        if not status and low.startswith(("открыто", "закрыто", "круглосуточно", "откроется", "закроется")):
            status = re.split(r"(?:Закроется|Откроется|Закрывается|\u200b|\s{2,})", text)[0].strip()[:40]
        if not hours and TIME_RE.search(text) and len(text) <= 90:
            if any(day in text for day in ("Пн", "Вт", "Ежедневно", "Круглосуточно")):
                hours = text

    return {
        "id": firm_id,
        "name": header["name"],
        "rubric": header["rubric"],
        "description": header["description"],
        "address": address,
        "city": city,
        "phones": phones,
        "phone": phones[0] if phones else "",
        "emails": emails,
        "email": emails[0] if emails else "",
        "websites": websites,
        "website": websites[0] if websites else "",
        "socials": socials,
        "messengers": messengers,
        **_split_messengers(messengers),
        "has_website": bool(websites) or site_link,
        "site_link": site_link,
        "rating": header["rating"],
        "reviews": header["reviews"],
        "status": status,
        "hours": hours,
        "lat": lat,
        "lon": lon,
        "query": query,
        "url": f"{BASE}/{city}/firm/{firm_id}",
    }


# --- HTTP-клиент ------------------------------------------------------------


class TwoGisClient:
    """Поиск, карточка и рейтинги через обычные GET-запросы (SSR-страницы)."""

    def __init__(self, timeout: int = 30, delay: float = 0.4, proxies: dict | None = None) -> None:
        self.timeout = timeout
        self.delay = delay
        self._session = self._build_session(proxies)

    def _build_session(self, proxies: dict | None) -> requests.Session:
        session = requests.Session()
        session.headers.update(
            {
                "User-Agent": random.choice(USER_AGENTS),
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
                "Cache-Control": "no-cache",
            }
        )
        retry = Retry(total=3, backoff_factor=0.6, status_forcelist=(429, 500, 502, 503, 504), allowed_methods=("GET",))
        adapter = HTTPAdapter(max_retries=retry, pool_maxsize=16)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        if proxies:
            session.proxies.update(proxies)
        return session

    def _get(self, url: str, referer: str | None = None) -> str:
        if self.delay:
            time.sleep(self.delay * random.uniform(0.6, 1.4))
        headers = {"Referer": referer} if referer else {}
        resp = self._session.get(url, timeout=self.timeout, headers=headers)
        resp.raise_for_status()
        if any(hint in resp.url or hint in resp.text[:4000] for hint in CAPTCHA_HINT):
            raise BlockedError(f"2GIS вернул captcha: {url}")
        return resp.text

    @staticmethod
    def search_url(city: str, query: str, page: int | None = None) -> str:
        url = f"{BASE}/{city}/search/{quote(query)}"
        return f"{url}/page/{page}" if page and page > 1 else url

    @staticmethod
    def parse_search(html: str, city: str | None = None) -> tuple[list[dict], int]:
        cards: list[dict] = []
        seen: set[str] = set()
        for match in FIRM_RE.finditer(html):
            slug, firm_id = match.group(1), match.group(2)
            if firm_id in seen:
                continue
            seen.add(firm_id)
            cards.append({"id": firm_id, "city": city or slug})
        pages = [int(p) for p in PAGE_RE.findall(html)]
        return cards, (max(pages) if pages else 1)

    def search_page(self, city: str, query: str, page: int = 1) -> tuple[list[dict], int]:
        url = self.search_url(city, query, page)
        return self.parse_search(self._get(url, referer=f"{BASE}/{city}"), city)

    def collect_firm_ids(self, city: str, query: str, max_pages: int = 10, max_leads: int | None = None, on_page=None):
        found: list[dict] = []
        seen: set[str] = set()
        last_page = 1
        for page in range(1, max_pages + 1):
            if page > last_page:
                break
            try:
                cards, last_page = self.search_page(city, query, page)
            except BlockedError:
                raise
            except requests.RequestException:
                break
            fresh = [c for c in cards if c["id"] not in seen]
            for card in fresh:
                seen.add(card["id"])
                card["query"] = query
                found.append(card)
            if on_page:
                on_page(page, last_page, len(found))
            if not fresh:
                break
            if max_leads and len(found) >= max_leads:
                return found[:max_leads]
        return found

    @staticmethod
    def firm_url(city: str, firm_id: str) -> str:
        return f"{BASE}/{city}/firm/{firm_id}"

    def firm_html(self, city: str, firm_id: str) -> str:
        return self._get(self.firm_url(city, firm_id), referer=f"{BASE}/{city}/search")

    def ratings(self, ids) -> dict[str, float]:
        """Рейтинг пачкой до 50 id за запрос."""
        out: dict[str, float] = {}
        batch: list[str] = []

        def flush() -> None:
            if not batch:
                return
            try:
                resp = self._session.get(
                    RATINGS_API,
                    params={"key": RATINGS_KEY, "object_ids": ",".join(batch), "locale": "ru_RU"},
                    timeout=self.timeout,
                )
                for item in resp.json().get("objects", []):
                    if item.get("rating") is not None:
                        out[str(item["object_id"])] = float(item["rating"])
            except (requests.RequestException, ValueError):
                pass
            batch.clear()

        for firm_id in ids:
            batch.append(str(firm_id))
            if len(batch) >= 50:
                flush()
        flush()
        return out


class BrowserClient(TwoGisClient):
    """Резервный режим: тот же HTML, но через Chromium (обход captcha)."""

    STEALTH_JS = """
    Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
    Object.defineProperty(navigator, 'languages', {get: () => ['ru-RU', 'ru']});
    Object.defineProperty(navigator, 'plugins', {get: () => [1, 2, 3, 4, 5]});
    window.chrome = window.chrome || {runtime: {}};
    """

    def __init__(self, timeout: int = 45, delay: float = 0.2, proxies: dict | None = None) -> None:
        super().__init__(timeout=timeout, delay=delay, proxies=proxies)
        try:
            from playwright.sync_api import sync_playwright
        except ImportError:
            sys.exit("Нужен playwright: pip install playwright && python -m playwright install chromium")
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(
            headless=True,
            args=["--headless=new", "--disable-blink-features=AutomationControlled"],
        )
        self._ctx = self._browser.new_context(locale="ru-RU", viewport={"width": 1440, "height": 900})
        self._ctx.add_init_script(self.STEALTH_JS)

    def _get(self, url: str, referer: str | None = None) -> str:
        page = self._ctx.new_page()
        try:
            page.goto(url, wait_until="domcontentloaded", timeout=self.timeout * 1000)
            return page.content()
        finally:
            page.close()


# --- фильтры и скоринг ------------------------------------------------------


class Filters:
    def __init__(
        self,
        no_website: bool = False,
        has_phone: bool = True,
        has_social: bool = False,
        min_rating: float | None = None,
        max_rating: float | None = None,
        min_reviews: int | None = None,
        max_reviews: int | None = None,
        rubric_match: str | None = None,
        exclude_chains: bool = False,
    ) -> None:
        self.no_website = no_website
        self.has_phone = has_phone
        self.has_social = has_social
        self.min_rating = min_rating
        self.max_rating = max_rating
        self.min_reviews = min_reviews
        self.max_reviews = max_reviews
        self.exclude_chains = exclude_chains
        self._rubric_re = re.compile(rubric_match, re.I) if rubric_match else None

    def accepts(self, lead: dict) -> bool:
        if self.no_website and lead.get("has_website"):
            return False
        if self.has_phone and not lead.get("phone"):
            return False
        if self.has_social and not lead.get("socials"):
            return False
        rating = lead.get("rating")
        if self.min_rating is not None and (rating is None or rating < self.min_rating):
            return False
        if self.max_rating is not None and (rating is None or rating > self.max_rating):
            return False
        reviews = lead.get("reviews") or 0
        if self.min_reviews is not None and reviews < self.min_reviews:
            return False
        if self.max_reviews is not None and reviews > self.max_reviews:
            return False
        if self._rubric_re and not self._rubric_re.search(lead.get("rubric") or ""):
            return False
        if self.exclude_chains and any(h in (lead.get("name") or "").lower() for h in CHAIN_HINTS):
            return False
        return True


def score(lead: dict) -> tuple[int, str]:
    """Приоритет лида 0..100 и расшифровка."""
    points = 0
    reasons: list[str] = []

    if not lead.get("has_website"):
        points += 45
        reasons.append("нет сайта")
    if lead.get("phone"):
        points += 20
        reasons.append("есть телефон")
    if lead.get("email"):
        points += 10
        reasons.append("есть e-mail")
    if lead.get("socials"):
        points += 8
        reasons.append("ведёт соцсети")
    if lead.get("messengers"):
        points += 6
        reasons.append("есть мессенджер")
    if lead.get("has_website"):
        points -= 25

    reviews = lead.get("reviews") or 0
    rating = lead.get("rating")

    if reviews >= 30:
        points += 12
        reasons.append(f"{reviews} отзывов")
    elif reviews >= 10:
        points += 6
    elif reviews == 0:
        points -= 8
        reasons.append("нет отзывов")

    if rating is not None:
        if rating >= 4.5:
            points += 10
            reasons.append(f"рейтинг {rating}")
        elif rating < 4.0:
            points += 4
            reasons.append(f"слабый рейтинг {rating}")

    rubric = (lead.get("rubric") or "").lower()
    if any(word in rubric for word in HIGH_VALUE):
        points += 12
        reasons.append("целевая ниша")

    points = max(0, min(100, points))
    tier = "hot" if points >= 70 else "warm" if points >= 45 else "cold"
    return points, f"{tier}: " + ", ".join(reasons)


def drop_boilerplate_domains(leads: list[dict], ratio: float = 0.35, minimum: int = 4) -> int:
    """Убирает домены, встречающиеся почти у всех: это футер/реклама 2GIS, а не сайт фирмы."""
    counts = Counter(domain for lead in leads for domain in lead["websites"])
    total = len(leads)
    boiler = {d for d, c in counts.items() if c >= minimum and c / total >= ratio}
    if not boiler:
        return 0
    for lead in leads:
        lead["websites"] = [d for d in lead["websites"] if d not in boiler]
        lead["website"] = lead["websites"][0] if lead["websites"] else ""
        lead["has_website"] = bool(lead["websites"]) or lead.get("site_link", False)
    return len(boiler)


# --- запись -----------------------------------------------------------------


class LeadsFileBusy(RuntimeError):
    """CSV/JSONL занят другой программой (Excel, LibreOffice) — записать нельзя."""


def _replace_with_retry(tmp: Path, target: Path, attempts: int = 10, delay: float = 0.3) -> bool:
    """Подменяет target содержимым tmp. Windows не даёт заменить занятый файл."""
    for attempt in range(attempts):
        try:
            os.replace(tmp, target)
            return True
        except OSError:
            if attempt == attempts - 1:
                return False
            time.sleep(delay)
    return False


def _spare_name(target: Path) -> Path:
    return target.with_name(f"{target.stem}_new{target.suffix}")


class Store:
    """Пишет лиды сразу на диск: CSV для Excel + JSONL для кода.

    Пишем в *.tmp и подменяем файл на выходе: если CSV открыт в Excel/LibreOffice,
    старые данные не портятся, а результат остаётся в <имя>_new.csv.
    """

    def __init__(self, path: Path, append: bool = False) -> None:
        self.path = Path(path)
        self.jsonl = self.path.with_suffix(".jsonl")
        self.append = append
        self._csv_file = None
        self._writer = None
        self._jsonl_file = None
        self._csv_tmp: Path | None = None
        self._jsonl_tmp: Path | None = None
        self._seen: set[str] = set()
        if append and self.path.exists():
            self._seen = self._load_ids()

    def _load_ids(self) -> set[str]:
        ids: set[str] = set()
        for attempt in range(5):
            try:
                with self.path.open(encoding="utf-8-sig", newline="") as fh:
                    for row in csv.DictReader(fh):
                        if row.get("id"):
                            ids.add(row["id"])
                break
            except (PermissionError, OSError):
                if attempt < 4:
                    time.sleep(0.15)
            except csv.Error:
                break
        return ids

    def __enter__(self) -> "Store":
        self.path.parent.mkdir(parents=True, exist_ok=True)
        appending = self.append and self.path.exists()

        if appending:
            # дописываем в конец: tmp не нужен, но файл должен быть доступен
            try:
                self._csv_file = self.path.open("a", encoding="utf-8-sig", newline="")
            except OSError as exc:
                raise LeadsFileBusy(self._busy_text(exc)) from exc
        else:
            self._csv_tmp = self.path.with_name(self.path.name + ".tmp")
            try:
                self._csv_file = self._csv_tmp.open("w", encoding="utf-8-sig", newline="")
            except OSError as exc:
                raise LeadsFileBusy(self._busy_text(exc)) from exc

        self._writer = csv.DictWriter(self._csv_file, fieldnames=COLUMNS)
        if not appending:
            self._writer.writeheader()

        if appending:
            try:
                self._jsonl_file = self.jsonl.open("a", encoding="utf-8")
            except OSError as exc:
                self._csv_file.close()
                raise LeadsFileBusy(self._busy_text(exc)) from exc
        else:
            self._jsonl_tmp = self.jsonl.with_name(self.jsonl.name + ".tmp")
            try:
                self._jsonl_file = self._jsonl_tmp.open("w", encoding="utf-8")
            except OSError as exc:
                self._csv_file.close()
                raise LeadsFileBusy(self._busy_text(exc)) from exc
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        for handle in (self._csv_file, self._jsonl_file):
            if handle:
                handle.close()
        if exc_type is not None:
            return  # при ошибке целевые файлы не трогаем

        saved: list[str] = []
        csv_busy = ""
        if self._csv_tmp:
            csv_busy = self._commit(self._csv_tmp, self.path)
            if csv_busy:
                saved.append(csv_busy)
        if self._jsonl_tmp:
            # если CSV не заменился, JSONL тоже откладываем в _new: иначе файлы
            # разойдутся по содержимому
            saved.append(self._commit(self._jsonl_tmp, self.jsonl, spare_only=bool(csv_busy)))
        busy = [name for name in saved if name]
        if busy:
            raise LeadsFileBusy(
                f"{', '.join(busy)} занят другой программой (Excel/LibreOffice).\n"
                f"Закрой файл и запусти сбор ещё раз.\n"
                f"Свежие данные сохранены в {_spare_name(self.path).name}"
            )

    def _commit(self, tmp: Path, target: Path, spare_only: bool = False) -> str:
        """Подменяет target. Возвращает "" при успехе, иначе имя занятого файла."""
        if not spare_only and _replace_with_retry(tmp, target):
            return ""
        spare = _spare_name(target)
        try:
            os.replace(tmp, spare)
        except OSError:
            pass
        return target.name

    @staticmethod
    def _busy_text(exc: OSError) -> str:
        return (
            "Не удалось открыть файл результатов: он занят другой программой "
            f"(Excel/LibreOffice). Закрой файл и повтори.\n{exc}"
        )

    def add(self, lead: dict) -> bool:
        if lead["id"] in self._seen:
            return False
        self._seen.add(lead["id"])
        self._writer.writerow(self._row(lead))
        self._csv_file.flush()
        self._jsonl_file.write(json.dumps(lead, ensure_ascii=False) + "\n")
        self._jsonl_file.flush()
        return True

    @staticmethod
    def _row(lead: dict) -> dict:
        links = messenger_links(lead)
        return {
            "score": lead.get("score", 0),
            "tier": (lead.get("reason") or "").split(":")[0],
            "name": lead.get("name", ""),
            "rubric": lead.get("rubric", ""),
            "phone": lead.get("phone", ""),
            "phones": "; ".join(lead.get("phones") or []),
            "email": lead.get("email", ""),
            "emails": "; ".join(lead.get("emails") or []),
            "website": lead.get("website", ""),
            "websites": "; ".join(lead.get("websites") or []),
            "has_website": "да" if lead.get("websites") else ("да (ссылка)" if lead.get("site_link") else "нет"),
            "socials": "; ".join(lead.get("socials") or []),
            "messengers": "; ".join(lead.get("messengers") or []),
            "telegram": lead.get("telegram", ""),
            "max": lead.get("max", ""),
            "vk": lead.get("vk", ""),
            "telegram_url": links.get("telegram", ""),
            "max_url": links.get("max", ""),
            "vk_url": links.get("vk", ""),
            "email_url": links.get("email", ""),
            "address": lead.get("address", ""),
            "city": lead.get("city", ""),
            "rating": lead.get("rating") if lead.get("rating") is not None else "",
            "reviews": lead.get("reviews", 0),
            "status": lead.get("status", ""),
            "hours": lead.get("hours", ""),
            "lat": lead.get("lat") if lead.get("lat") is not None else "",
            "lon": lead.get("lon") if lead.get("lon") is not None else "",
            "description": lead.get("description", ""),
            "query": lead.get("query", ""),
            "url": lead.get("url", ""),
            "id": lead.get("id", ""),
            "reason": lead.get("reason", ""),
        }


# --- CLI --------------------------------------------------------------------

_local = threading.local()


def _client_factory(args) -> TwoGisClient:
    if getattr(_local, "client", None) is None:
        if args.browser:
            _local.client = BrowserClient(timeout=args.timeout, delay=args.delay)
        else:
            _local.client = TwoGisClient(timeout=args.timeout, delay=args.delay)
    return _local.client


def _parse_search_url(url: str) -> tuple[str, str] | None:
    """/moscow/search/кофейни -> (moscow, кофейни)."""
    parts = [p for p in urlparse(url).path.split("/") if p]
    if len(parts) >= 3 and parts[1] == "search":
        return parts[0], unquote("/".join(parts[2:]))
    return None


def _ask(prompt: str, default: str = "") -> str:
    hint = f" [{default}]" if default else ""
    try:
        value = input(f"{prompt}{hint}: ").strip()
    except EOFError:
        return default
    return value or default


def wizard() -> list[str]:
    """Диалог для запуска двойным кликом: без аргументов командной строки."""
    print("=" * 60)
    print("  Сбор лидов из 2GIS")
    print("=" * 60)
    print("Города: moscow, spb, kazan, ekb, nsk, novosibirsk, rostov-na-donu ...\n")

    city = _ask("Город", "moscow")
    query = _ask("Что искать (например: барбершоп)")
    if not query:
        print("Без запроса искать нечего.")
        return []

    only_no_site = _ask("Только организации без сайта? (д/н)", "д").lower()
    pages = _ask("Сколько страниц выдачи", "5")
    output = _ask("Файл результата", "leads.csv")

    argv = ["-c", city, "-q", query, "--max-pages", pages, "-o", output, "--open"]
    if only_no_site not in ("н", "n", "no", "нет", "0"):
        argv.append("--no-website")
    return argv


def build_targets(args) -> list[tuple[str, str]]:
    targets: list[tuple[str, str]] = []
    for raw in args.url or []:
        parsed = _parse_search_url(raw)
        if parsed:
            targets.append(parsed)
        else:
            print(f"[!] Не разобрал URL: {raw}", file=sys.stderr)

    for city in [c.strip() for c in (args.city or "").split(",") if c.strip()]:
        for query in args.query or []:
            targets.append((city, query))

    if not targets:
        sys.exit("Нужен -c/--city + -q/--query или -u/--url")
    return targets


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="leads2gis",
        description="Сбор лидов из 2GIS: контакты организаций + приоритет под аутрич.",
    )
    src = parser.add_argument_group("Источник")
    src.add_argument("-c", "--city", help="Город-слаг 2GIS: moscow, spb, kazan (можно через запятую)")
    src.add_argument("-q", "--query", nargs="+", help="Поисковые запросы: 'кофейни' 'стоматология'")
    src.add_argument("-u", "--url", nargs="+", help="Готовые URL выдачи 2GIS")

    out = parser.add_argument_group("Вывод")
    out.add_argument("-o", "--output", default="leads.csv", help="CSV-файл (рядом пишется .jsonl)")
    out.add_argument("--resume", action="store_true", help="Дописывать в существующий файл, пропуская дубли")
    out.add_argument("--top", type=int, default=10, help="Сколько лидов показать в консоли")
    out.add_argument("--open", action="store_true", help="Открыть CSV после завершения")

    flt = parser.add_argument_group("Фильтры")
    flt.add_argument("--no-website", action="store_true", help="Только организации без сайта")
    flt.add_argument("--no-phone", action="store_true", help="Разрешить лиды без телефона")
    flt.add_argument("--has-social", action="store_true", help="Только те, у кого есть соцсети")
    flt.add_argument("--min-rating", type=float, help="Минимальный рейтинг")
    flt.add_argument("--max-rating", type=float, help="Максимальный рейтинг")
    flt.add_argument("--min-reviews", type=int, help="Минимум отзывов")
    flt.add_argument("--max-reviews", type=int, help="Максимум отзывов")
    flt.add_argument("--rubric", help="Regex по рубрике: 'стомат|клиник'")
    flt.add_argument("--exclude-chains", action="store_true", help="Выкинуть сети и франшизы")
    flt.add_argument("--min-score", type=int, default=0, help="Минимальный score лида")

    run = parser.add_argument_group("Скорость и обход")
    run.add_argument("--max-pages", type=int, default=10, help="Страниц выдачи на запрос (по умолчанию 10)")
    run.add_argument("--max-leads", type=int, help="Стоп после N карточек на запрос")
    run.add_argument("--workers", type=int, default=6, help="Параллельных запросов карточек")
    run.add_argument("--delay", type=float, default=0.4, help="Пауза между запросами, сек")
    run.add_argument("--timeout", type=int, default=30, help="Таймаут запроса, сек")
    run.add_argument("--proxy", help="Прокси: http://user:pass@host:port")
    run.add_argument("--browser", action="store_true", help="Тянуть HTML через Chromium (если ловит captcha)")

    wizard_mode = argv is None and len(sys.argv) == 1
    if wizard_mode:
        argv = wizard()
        if not argv:
            return 1

    args = parser.parse_args(argv)
    if not args.query and not args.url:
        parser.error("нужен -q/--query или -u/--url")

    if not DEPS_OK:
        _load_deps(install=True)

    proxies = {"http": args.proxy, "https": args.proxy} if args.proxy else None
    targets = build_targets(args)

    filters = Filters(
        no_website=args.no_website,
        has_phone=not args.no_phone,
        has_social=args.has_social,
        min_rating=args.min_rating,
        max_rating=args.max_rating,
        min_reviews=args.min_reviews,
        max_reviews=args.max_reviews,
        rubric_match=args.rubric,
        exclude_chains=args.exclude_chains,
    )

    client = TwoGisClient(timeout=args.timeout, delay=args.delay, proxies=proxies)
    started = time.time()
    parsed: list[dict] = []

    for city, query in targets:
        print(f"\n=== {city} / {query}")
        try:
            cards = client.collect_firm_ids(
                city,
                query,
                max_pages=args.max_pages,
                max_leads=args.max_leads,
                on_page=lambda p, last, total: print(
                    f"\r  страница {p}/{last}, карточек: {total}", end="", flush=True
                ),
            )
        except BlockedError:
            print("\n[!] captcha — повтори с --browser или смени прокси", file=sys.stderr)
            continue
        print(f"\n  найдено карточек: {len(cards)}")

        def load(card: dict, city: str = city, query: str = query) -> dict | None:
            worker = _client_factory(args)
            try:
                html = worker.firm_html(city, card["id"])
            except BlockedError:
                raise
            except Exception:  # noqa: BLE001 - одна битая карточка не рушит прогон
                return None
            return parse_firm(html, city, card["id"], query)

        done = 0
        with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
            futures = [pool.submit(load, card) for card in cards]
            for future in as_completed(futures):
                done += 1
                try:
                    lead = future.result()
                except BlockedError:
                    print("\n[!] captcha на карточке — включи --browser", file=sys.stderr)
                    continue
                if lead:
                    parsed.append(lead)
                print(f"\r  карточки {done}/{len(cards)}", end="", flush=True)
        print()

    unique: dict[str, dict] = {}
    for lead in parsed:
        unique.setdefault(lead["id"], lead)
    leads = list(unique.values())

    dropped = drop_boilerplate_domains(leads)
    if dropped:
        print(f"Убрано общих доменов-заглушек: {dropped}")

    ratings = client.ratings(lead["id"] for lead in leads)
    for lead in leads:
        if ratings.get(lead["id"]) is not None:
            lead["rating"] = ratings[lead["id"]]
        lead["score"], lead["reason"] = score(lead)

    leads.sort(key=lambda x: x["score"], reverse=True)
    selected = [lead for lead in leads if filters.accepts(lead) and lead["score"] >= args.min_score]

    saved = 0
    with Store(Path(args.output), append=args.resume) as store:
        for lead in selected:
            if store.add(lead):
                saved += 1

    print(f"\nГотово: карточек {len(parsed)}, уникальных {len(leads)}, в файл {saved}, {time.time() - started:.0f} с")
    print(f"  {args.output} + {Path(args.output).with_suffix('.jsonl')}")

    if selected:
        print(f"\nТоп-{min(args.top, len(selected))}:")
        for lead in selected[: args.top]:
            print(
                f"  {lead['score']:>3}  {lead['name'][:34]:<34} {lead['phone'] or '—':<15} "
                f"{lead['rubric'][:22]:<22} {lead['reason']}"
            )

    if args.open:
        try:
            os.startfile(str(Path(args.output).resolve()))  # noqa: S606 - Windows: открыть CSV
        except (AttributeError, OSError):
            pass

    if wizard_mode and not os.environ.get("LEADS_BAT"):
        # Запуск двойным кликом по .py: держим окно, чтобы успеть прочитать результат.
        try:
            input("\nEnter — закрыть окно...")
        except EOFError:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
