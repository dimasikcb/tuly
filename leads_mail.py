"""Рассылка по горячим лидам 2GIS: ИИ-тексты, очередь каналов, 5-дневный follow-up.

Логика отделена от GUI: модуль ничего не знает про Tkinter. GUI передаёт сюда
настройки (словарь), коллбэки лога/прогресса и Event остановки.

Каналы по приоритету: MAX -> Telegram -> VK -> Email. WhatsApp не используется.
Одно сообщение в минуту. Через 5 дней без ответа — следующий канал (с пометкой
«уже писал»), ещё через 5 — лид закрывается. Ответы пересылаются в MAX.

Telegram — user-аккаунт (Telethon). Каждая операция идёт под общим lock'ом,
клиент подключается и отключается в одной и той же нити: файл сессии SQLite
никогда не открыт двумя клиентами одновременно, event loop создаётся на поток.

Устройство проекта (слои снизу вверх):

    leads_contacts  — чистые функции: ник ↔ ссылка, каналы, формат телефона
    leads_pool      — CSV пула: загрузка, дедуп, срок жизни, миграция
    leads_mail      — этот файл: ИИ-тексты и отправка по каналам
    leads2gis       — парсер 2ГИС
    tools.pyw       — GUI

Имена пула и контактов реэкспортируются отсюда: `leads_mail.pool_add` и т.п.
продолжают работать, поэтому GUI и тесты не пришлось переписывать.
"""

from __future__ import annotations

import asyncio
import imaplib
import json
import os
import random
import re
import smtplib
import socket
import threading
import time
from datetime import datetime, timedelta
from email import message_from_bytes
from email.message import EmailMessage
from pathlib import Path

from leads_contacts import (  # noqa: F401 - часть публичного API модуля
    CHANNEL_TITLE,
    TARGET_TITLE,
    channel_label,
    channels_of,
    contact_display,
    contact_link,
    format_phone,
    messenger_links,
    targets_of,
)
from leads_contacts import from_link as _from_link
from leads_contacts import num as _num
from leads_contacts import pick_contact as _pick_contact
from leads_contacts import short as _short
from leads_contacts import to_link as _to_link
from leads_pool import (  # noqa: F401 - реэкспорт: GUI и тесты зовут leads_mail.*
    LEGACY_EXTRAS,
    LEGACY_NAME,
    LEGACY_SUFFIX,
    POOL_COLUMNS,
    POOL_KEEP_STATUS,
    POOL_NAME,
    RU_CITIES,
    PoolBusy,
    _lead_to_pool_row,
    _now,
    _parse_dt,
    _pool_save,
    _pool_value,
    _read_csv,
    _tier_of,
    default_pool_path,
    is_hot,
    load_hot_leads,
    migrate_legacy,
    pool_add,
    pool_key,
    pool_load,
    pool_mark_sent,
    pool_mark_status,
    pool_path,
    pool_purge,
    pool_refresh,
    pool_remove,
    pool_stats,
    pool_sync,
    row_id,
)

PRIORITY = ("max", "telegram", "vk", "email")
DAY = 86400.0
STATUSES = ("replied", "stopped", "read", "seen", "closed", "failed")

# Один lock на весь Telegram: сессия SQLite и один аккаунт.
TG_LOCK = threading.Lock()

# Клиенты каналов (VK и пр.) создаются один раз на процесс.
_CLIENTS: dict = {}
_CLIENTS_LOCK = threading.Lock()


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
    p = Path(path)
    if not p.exists():
        return data
    content = ""
    for attempt in range(3):
        try:
            content = p.read_text(encoding="utf-8")
            break
        except OSError:
            if attempt < 2:
                time.sleep(0.1)
    if content:
        try:
            data.update(json.loads(content))
        except (ValueError, json.JSONDecodeError):
            pass
    return data


def save_settings(path, settings: dict) -> None:
    p = Path(path)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_name(f"{p.name}.{os.getpid()}.tmp")
        text = json.dumps(settings, ensure_ascii=False, indent=1)
        tmp.write_text(text, encoding="utf-8")
        for attempt in range(5):
            try:
                tmp.replace(p)
                return
            except OSError:
                if attempt < 4:
                    time.sleep(0.1)
        p.write_text(text, encoding="utf-8")
    except OSError:
        pass


class MailState:
    """Журнал по каждому лиду: канал, sent_id, статус, история попыток."""

    def __init__(self, path) -> None:
        self.path = Path(path)
        self.data: dict = {}
        if not self.path.exists():
            return
        content = ""
        for attempt in range(3):
            try:
                content = self.path.read_text(encoding="utf-8")
                break
            except OSError:
                if attempt < 2:
                    time.sleep(0.1)
        if content:
            try:
                self.data = json.loads(content)
                return
            except (ValueError, json.JSONDecodeError):
                pass
        # Восстановление из .tmp при повреждении основного файла
        tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
        if not tmp.exists():
            tmp = self.path.with_suffix(".tmp")
        if tmp.exists():
            try:
                self.data = json.loads(tmp.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                self.data = {}

    def get(self, lead_id) -> dict:
        return self.data.setdefault(str(lead_id), {})

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(f"{self.path.name}.{os.getpid()}.tmp")
            text = json.dumps(self.data, ensure_ascii=False, indent=1)
            tmp.write_text(text, encoding="utf-8")
            for attempt in range(5):
                try:
                    tmp.replace(self.path)
                    return
                except OSError:
                    if attempt < 4:
                        time.sleep(0.1)
            self.path.write_text(text, encoding="utf-8")
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
    import urllib.error
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
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read().decode("utf-8", "ignore"))
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"ИИ API ошибка {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"ИИ API недоступен: {exc.reason}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise RuntimeError("ИИ API: таймаут запроса") from exc

    choices = data.get("choices")
    if not choices or not isinstance(choices, list):
        msg = data.get("error", {}).get("message") if isinstance(data.get("error"), dict) else "пустой ответ модели"
        raise RuntimeError(f"ИИ API: {msg}")
    return (choices[0].get("message", {}).get("content") or "").strip()


def _send_email(settings: dict, to: str, text: str) -> None:
    host = (settings.get("smtp_host") or "").strip()
    user = (settings.get("smtp_user") or "").strip()
    pwd = settings.get("smtp_pass") or ""
    to_clean = str(to or "").strip()
    if to_clean.lower().startswith("mailto:"):
        to_clean = to_clean[7:].strip()
    if not (host and user and pwd and to_clean):
        raise RuntimeError("SMTP не настроен (host/user/pass или пустой email)")
    if "@" not in to_clean or "." not in to_clean.split("@")[-1]:
        raise RuntimeError(f"SMTP: некорректный email получателя '{to_clean}'")

    try:
        port = int(str(settings.get("smtp_port") or "465").strip() or 465)
    except (TypeError, ValueError):
        port = 465

    frm = (settings.get("smtp_from") or "").strip() or user
    msg = EmailMessage()
    msg["From"] = frm
    msg["To"] = to_clean
    msg["Subject"] = settings.get("mail_subject") or "Предложение"
    msg.set_content(text)

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=30) as srv:
                srv.login(user, pwd)
                srv.send_message(msg)
        else:
            with smtplib.SMTP(host, port, timeout=30) as srv:
                try:
                    srv.starttls()
                except smtplib.SMTPNotSupportedError:
                    pass
                srv.login(user, pwd)
                srv.send_message(msg)
    except smtplib.SMTPAuthenticationError as exc:
        raise RuntimeError(f"SMTP: ошибка авторизации ({exc.smtp_code}: {exc.smtp_error})") from exc
    except smtplib.SMTPConnectError as exc:
        raise RuntimeError(f"SMTP: не удалось подключиться к серверу {host}:{port}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise RuntimeError(f"SMTP: таймаут подключения к {host}:{port}") from exc
    except smtplib.SMTPException as exc:
        raise RuntimeError(f"SMTP ошибка отправки: {exc}") from exc
    except OSError as exc:
        raise RuntimeError(f"SMTP сетевая ошибка: {exc}") from exc


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
    try:
        from telethon.sync import TelegramClient
    except ImportError:
        raise RuntimeError("Telegram: библиотека telethon не установлена (pip install telethon)")

    try:
        api_id = int(str(settings.get("api_id") or "0").strip() or 0)
    except ValueError:
        api_id = 0
    api_hash = (settings.get("api_hash") or "").strip()
    if not (api_id and api_hash):
        raise RuntimeError("Telegram: заполни api_id и api_hash")

    with TG_LOCK:
        _ensure_loop()
        client = TelegramClient(_tg_session_path(settings), api_id, api_hash)
        try:
            client.connect()
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

    contact_clean = str(contact or "").strip()
    if not contact_clean:
        raise RuntimeError("TG: пустой контакт")

    if kind == "phone":
        res = client(ImportContactsRequest([
            InputPhoneContact(client_id=0, phone=contact_clean, first_name="lead", last_name=""),
        ]))
        users = [u for u in (getattr(res, "users", None) or []) if getattr(u, "id", None)]
        if not users:
            raise RuntimeError(f"TG: телефон {contact_clean} не зарегистрирован в Telegram")
        entity = users[0]
    else:
        entity = client.get_entity(contact_clean.lstrip("@"))

    sent = client.send_message(entity, text)
    if isinstance(sent, (list, tuple)):
        sent = sent[0]
    msg_id = getattr(sent, "id", None)
    if msg_id is None:
        raise RuntimeError("TG: сообщение не отправлено")
    return {"msg_id": int(msg_id), "peer": int(getattr(entity, "id", 0) or 0)}


def tg_send(settings: dict, contact: str, kind: str, text: str) -> dict:
    return _tg_call(settings, lambda c: _tg_send(c, contact, kind, text))


def tg_login(settings: dict, phone: str, ask=None, on_log=None) -> None:
    """Вход в TG. `ask(заголовок, вопрос, secret=False)` спрашивает код и 2FA."""
    try:
        from telethon.errors import SessionPasswordNeededError
        from telethon.sync import TelegramClient
    except ImportError:
        raise RuntimeError("Telegram: библиотека telethon не установлена (pip install telethon)")

    try:
        api_id = int(str(settings.get("api_id") or "0").strip() or 0)
    except ValueError:
        api_id = 0
    api_hash = (settings.get("api_hash") or "").strip()
    phone = (phone or "").strip()
    if not (api_id and api_hash and phone):
        raise RuntimeError("нужны api_id, api_hash и номер телефона")
    if ask is None:
        raise RuntimeError("не передан диалог ввода кода")

    with TG_LOCK:
        _ensure_loop()
        client = TelegramClient(_tg_session_path(settings), api_id, api_hash)
        try:
            client.connect()
            if client.is_user_authorized():
                if on_log:
                    on_log("TG: аккаунт уже привязан", "ok")
                return
            client.send_code_request(phone)
            code = ask("Telegram — код", f"Код из Telegram для {phone}:")
            if not code:
                raise RuntimeError("код не введён")
            try:
                client.sign_in(phone=phone, code=code.strip())
            except SessionPasswordNeededError:
                pwd = ask("Telegram — 2FA", "Облачный пароль:", True)
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
    try:
        from telethon.sync import TelegramClient
    except ImportError:
        return "TG: telethon не установлен"

    try:
        api_id = int(str(settings.get("api_id") or "0").strip() or 0)
    except ValueError:
        api_id = 0
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


class VKClient:
    """VK через vk_api с правами user: пишет первым по ID/домену, видит out_read/in_read."""

    def __init__(self, token: str) -> None:
        try:
            import vk_api
        except ImportError:
            raise RuntimeError("нет vk-api: pip install vk-api>=11.9.9")
        token_clean = (token or "").strip()
        if not token_clean:
            raise RuntimeError("нет VK token")
        self.vk = vk_api.VkApi(token=token_clean).get_api()

    def peer_id(self, screen: str) -> int:
        """'company' -> -123456 для группы, 'id123' -> 123 для профиля."""
        screen = str(screen).strip().lstrip("@")
        if not screen:
            raise RuntimeError("VK: пустой контакт")
        if re.fullmatch(r"-?\d+", screen):
            return int(screen)
        if screen.startswith("id") and screen[2:].isdigit():
            return int(screen[2:])
        if screen.startswith("club") and screen[4:].isdigit():
            return -int(screen[4:])
        if screen.startswith("public") and screen[6:].isdigit():
            return -int(screen[6:])
        res = self.vk.utils.resolveScreenName(screen_name=screen)
        if not res:
            raise RuntimeError(f"VK: не найден {screen}")
        oid = int(res["object_id"])
        return -oid if res.get("type") == "group" else oid

    def send(self, screen: str, text: str) -> dict:
        import vk_api.exceptions
        pid = self.peer_id(screen)
        try:
            resp = self.vk.messages.send(
                peer_id=pid, message=text, random_id=random.randint(1, 2**31))
        except vk_api.exceptions.ApiError as exc:
            if exc.code in (901, 902):
                raise RuntimeError(f"VK: закрыты личные сообщения у {screen} (ошибка {exc.code})") from exc
            if exc.code == 5:
                raise RuntimeError("VK: неверный или истекший токен (ошибка 5)") from exc
            raise RuntimeError(f"VK API ошибка {exc.code}: {getattr(exc, 'error', {}).get('error_msg', exc)}") from exc
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


def _send_max(settings: dict, target: str, text: str) -> None:
    import urllib.error
    import urllib.request

    url = (settings.get("max_url") or "").strip()
    token = (settings.get("max_token") or "").strip()
    target_clean = str(target or "").strip()
    if not (url and token):
        raise RuntimeError("MAX API не настроен (url/token)")
    if not target_clean:
        raise RuntimeError("MAX: пустой получатель")
    payload = json.dumps({"to": target_clean, "text": text}).encode()
    req = urllib.request.Request(
        url, data=payload,
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {token}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            resp.read()
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"MAX API ошибка {exc.code}: {exc.reason}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"MAX API недоступен: {exc.reason}") from exc
    except (TimeoutError, socket.timeout) as exc:
        raise RuntimeError("MAX API: таймаут запроса") from exc


def _vk_client(settings: dict) -> VKClient:
    token = (settings.get("vk_token") or "").strip()
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
        host = (self.settings.get("smtp_host") or "").strip()
        user = (self.settings.get("smtp_user") or "").strip()
        pwd = self.settings.get("smtp_pass") or ""
        if not (host and user and pwd):
            return

        imap_host = (self.settings.get("imap_host") or "").strip()
        if not imap_host:
            imap_host = host.replace("smtp.", "imap.", 1) if "smtp." in host else host

        conn = None
        try:
            conn = imaplib.IMAP4_SSL(imap_host, timeout=20)
            conn.login(user, pwd)
            conn.select("INBOX")
            _, ids = conn.search(None, "UNSEEN")
            id_list = ids[0].split() if ids and ids[0] else []
            for num in id_list[-30:]:
                _, parts = conn.fetch(num, "(RFC822)")
                if not parts or not parts[0]:
                    continue
                msg = message_from_bytes(parts[0][1])
                sender = str(msg.get("From") or "")
                match = re.search(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", sender)
                if not match:
                    continue
                sender_email = match.group(0).lower()
                row = next((
                    r for r in self.leads
                    if str(r.get("email") or "").lower().replace("mailto:", "").strip() == sender_email
                ), None)
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
