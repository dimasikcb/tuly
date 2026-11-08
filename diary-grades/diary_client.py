"""Клиент электронного дневника Московской области (authedu.mosreg.ru).

Слой загрузки: между «голыми» парсерами (parsers.py) и GUI. Умеет два сценария:

* :meth:`DiaryClient.login` — первый запуск с видимым окном Chromium, чтобы
  пользователь вручную вошёл через Госуслуги/ЕСИА; сохранённую сессию кладём в
  ``config.SESSION_FILE``;
* :meth:`DiaryClient.fetch` — скрытый браузер с этой сессией, сбор JSON-ответов
  SPA и (при необходимости) HTML, разбор в ``SubjectGrades``.

ВАЖНО: Playwright импортируется ТОЛЬКО внутри методов (ленивый импорт).
``import diary_client`` безопасно работает в офлайн-тестах и на машинах без
установленного браузера. Модуль не знает о tkinter/customtkinter и не трогает
сетевые настройки (VPN/прокси) системы.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Callable

import config
import parsers
from config import DEBUG_DIR, SESSION_FILE
from models import (
    DiaryError, FetchResult, ParseError, Period, SessionExpired,
    SubjectGrades, clean_name,
)

__all__ = ["DiaryClient", "fetch_grades", "interactive_login"]

StatusCallback = Callable[[str], None]

# ВАЖНО: user_agent НЕ переопределяем. Портал «Моя школа» привязывает сессию
# к браузерному отпечатку: с кастомным UA скрытый контекст получает 403 на
# /api/family/web/v1/marks, хотя login() сохранял cookies под дефолтным UA.

# Разумные значения по умолчанию, если selectors.json отсутствует/повреждён
# или в конструктор передали неполный набор селекторов.
_SELECTOR_DEFAULTS: dict = {
    "logged_in_markers": [
        "дневник", "успеваемость", "оценки", "журнал", "выход", "профиль",
    ],
    "expired_url_hints": [
        "esia.gosuslugi.ru", "/login", "/auth", "passport", "oauth",
    ],
    "grades_nav_texts": [
        "оценки", "успеваемость", "дневник", "табель", "отчёты", "журнал", "баллы",
    ],
    "period_nav_texts": ["четверть", "триместр", "период", "полугодие"],
    "json_url_keywords": [
        "mark", "grade", "score", "diary", "journal", "progress", "subject",
        "lesson", "period", "estimate", "performance",
    ],
}


# --------------------------------------------------------------------------- #
# Вспомогательные функции (без Playwright)
# --------------------------------------------------------------------------- #

def _start_playwright():
    """Ленивый импорт + запуск Playwright. Возвращает объект с ``.chromium`` и ``.stop()``.

    Импорт намеренно спрятан внутрь функции: на уровне модуля ``playwright``
    не импортируется вообще, поэтому ``import diary_client`` не требует ни
    пакета, ни скачанного браузера.
    """
    from playwright.sync_api import sync_playwright
    return sync_playwright().start()


def _emit(on_status: StatusCallback | None, message: str) -> None:
    """Безопасно сообщает прогресс в GUI (колбэк never должен ронять сбор)."""
    if on_status is None:
        return
    try:
        on_status(message)
    except Exception:
        pass


def _active_login_hints(hints: Any) -> tuple[str, ...]:
    """Фильтрует подсказки, которые уже встречаются в основном домене дневника.

    Хост стартовой страницы ``authedu.mosreg.ru`` сам содержит подстроку
    ``auth``. Если оставить её в списке, успешный вход (страница вернулась на
    тот же хост) никогда не будет распознан. Поэтому подсказки, являющиеся
    частью ``START_URL``, из проверки URL исключаются, а «чужие» хосты входа
    (ЕСИА, passport, /login, oauth) остаются.
    """
    base = str(config.START_URL).casefold()
    active: list[str] = []
    for raw in hints or ():
        hint = str(raw).casefold()
        if hint and hint not in base and hint not in active:
            active.append(hint)
    return tuple(active)


def _is_login_url(url: Any, hints: tuple[str, ...]) -> bool:
    lowered = str(url or "").casefold()
    return any(hint in lowered for hint in hints)


def _merge_best(*groups) -> list[SubjectGrades]:
    """Объединяет предметы, беря по каждому САМЫЙ ПОЛНЫЙ набор оценок.

    В отличие от ``parsers.merge_subjects`` (он складывает marks), здесь одно
    и то же значение не удваивается: при обходе предметов страница и JSON
    часто отдают одни и те же оценки повторно.
    """
    best: dict[str, SubjectGrades] = {}
    order: list[str] = []
    for group in groups:
        for subject in group or ():
            name = clean_name(subject.name)
            key = name.casefold()
            if not name:
                continue
            marks = tuple(subject.marks or ())
            official_avg = getattr(subject, "official_average", "")
            if key not in best:
                order.append(key)
                best[key] = SubjectGrades(name, marks, subject.period, subject.source,
                                          official_average=official_avg)
            else:
                existing = best[key]
                merged_avg = official_avg or existing.official_average
                if len(marks) > len(existing.marks):
                    best[key] = SubjectGrades(
                        name, marks, subject.period or existing.period, subject.source,
                        official_average=merged_avg,
                    )
                elif merged_avg != existing.official_average:
                    best[key] = SubjectGrades(
                        existing.name, existing.marks, existing.period, existing.source,
                        official_average=merged_avg,
                    )
    return [best[key] for key in order]


def complete_subjects(subjects, period: Period) -> list[SubjectGrades]:
    """Добавляет предметы из шаблона, по которым портал не прислал оценок.

    Чтобы после обновления таблица не схлопывалась до одного предмета: все
    остальные остаются строками «нет оценок · нужен вход» в конце.
    """
    result = _merge_best(subjects)
    known = {s.name.casefold() for s in result}
    for name in config.expected_subjects():
        if name.casefold() not in known:
            known.add(name.casefold())
            result.append(SubjectGrades(name, (), period.label, "pending"))
    return result


def _marks_from_responses(responses, period_label: str,
                          api_path: str) -> list[SubjectGrades]:
    """Предметы из ответа ``/api/family/web/v1/marks`` — сразу за весь период.

    Портал «Моя школа» отдаёт ВСЕ предметы одним плоским списком, поэтому
    обход карточек предметов здесь не нужен. Если ответов несколько (неделя +
    период), берём самый полный набор оценок по каждому предмету.
    """
    found: list[SubjectGrades] = []
    for url, payload in responses:
        if api_path not in str(url).casefold():
            continue
        found = _merge_best(found, parsers.parse_marks_api(payload, period_label))
    return found


class DiaryClient:
    """Одна инкапсулированная работа с браузером. Не потокобезопасен: GUI
    создаёт по экземпляру на фоновый поток через :func:`fetch_grades`.
    """

    def __init__(self, settings: dict | None = None,
                 selectors: dict | None = None) -> None:
        base_settings = dict(config.DEFAULTS)
        base_settings.update(settings if settings is not None else config.load_settings())
        self.settings = base_settings

        loaded = selectors if selectors is not None else (config.load_selectors() or {})
        self.selectors: dict = {**_SELECTOR_DEFAULTS, **loaded}

        raw_hints = list(config.LOGIN_HOST_HINTS) + list(
            self.selectors.get("expired_url_hints") or []
        )
        self._login_hints = _active_login_hints(raw_hints)

        self._pw = None
        self._browser = None
        self._context = None
        self._page = None

    # -- сессия ------------------------------------------------------------- #

    def has_session(self) -> bool:
        return bool(config.session_exists())

    def clear_session(self) -> None:
        """Безопасно удаляет файл сессии (отсутствие файла — не ошибка)."""
        try:
            Path(SESSION_FILE).unlink(missing_ok=True)
        except OSError:
            pass

    # -- первый вход -------------------------------------------------------- #

    def login(self, on_status: StatusCallback | None = None,
              timeout_s: int | None = None) -> bool:
        """Открывает видимый Chromium и ждёт ручного входа через ЕСИА.

        Возвращает ``True`` и сохраняет ``storage_state`` при успехе, ``False``
        при таймауте/любой ошибке (GUI не должен падать). Браузер и Playwright
        гарантированно закрываются в ``finally``.
        """
        timeout = float(
            timeout_s if timeout_s is not None
            else self.settings.get("login_timeout_s", 300)
        )
        markers = [
            str(m).casefold()
            for m in self.selectors.get("logged_in_markers", []) if m
        ]
        try:
            self._pw = _start_playwright()
            self._browser = self._pw.chromium.launch(headless=False)
            self._context = self._browser.new_context(
                viewport={"width": 1280, "height": 900},
                locale="ru-RU",
            )
            self._page = self._context.new_page()
            page = self._page

            _emit(on_status, "Открылось окно браузера. Войдите через Госуслуги (ЕСИА).")
            page.goto(
                config.START_URL,
                wait_until="domcontentloaded",
                timeout=int(self.settings.get("nav_timeout_s", 35)) * 1000,
            )

            deadline = time.monotonic() + max(0.0, timeout)
            while True:
                try:
                    url = str(page.url or "")
                    text = str(page.content() or "").casefold()
                except Exception:
                    url, text = "", ""

                on_login_page = _is_login_url(url, self._login_hints)
                if not on_login_page and markers and any(m in text for m in markers):
                    state = self._context.storage_state()
                    config.atomic_write_json(SESSION_FILE, state)
                    _emit(on_status, "Вход выполнен, сессия сохранена.")
                    return True

                if time.monotonic() >= deadline:
                    break
                _emit(on_status, "Ожидаем завершения входа в ЕСИА...")
                time.sleep(0.6)

            _emit(on_status, "Время ожидания входа истекло.")
            return False
        except Exception as exc:  # не роняем GUI, но и не глотаем молча
            _emit(on_status, f"Не удалось выполнить вход: {exc}")
            return False
        finally:
            self.close()

    # -- загрузка оценок ---------------------------------------------------- #

    def _fetch_via_api(self, period: Period,
                       on_status: StatusCallback | None = None) -> list[SubjectGrades]:
        """Прямой запрос к API «Моей школы» за ВЕСЬ учебный период.

        Надёжнее перехвата запросов SPA: страница оценок по умолчанию просит
        только текущую неделю и может вообще не сделать запрос из кэша. Здесь
        сами читаем токен из сохранённой сессии, узнаём ``student_id`` через
        профиль и запрашиваем оценки с ``from``/``to`` границами периода.
        """
        try:
            state = json.loads(Path(SESSION_FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError, TypeError):
            return []
        token = ""
        student_hint = None
        for cookie in state.get("cookies") or []:
            if not isinstance(cookie, dict):
                continue
            if cookie.get("name") == "aupd_token":
                token = str(cookie.get("value") or "")
            elif cookie.get("name") == "student_person_id":
                student_hint = cookie.get("value")
        if not token:
            return []

        root = str(self.selectors.get("api_root") or config.API_ROOT).rstrip("/")
        headers = {
            "accept": "application/json, text/plain, */*",
            "authorization": "Bearer " + token,
            "content-type": "application/json;charset=UTF-8",
            "profile-type": "student",
            "x-mes-roleid": "1",
            "x-mes-subsystem": "familyweb",
            "referer": str(self.selectors.get("marks_referer")
                           or config.MARKS_REFERER),
        }
        api = getattr(self._context, "request", None)
        if api is None:
            return []
        timeout = int(self.settings.get("nav_timeout_s", 35)) * 1000

        student_id = self.selectors.get("student_id")
        profile_subjects: tuple[str, ...] = ()
        # Профиль нужен не только ради student_id: в children[].groups лежит
        # ПОЛНЫЙ список предметов класса, включая те, по которым в периоде ещё
        # нет оценок («Вероятность и статистика», «Математика»). Ответ /marks
        # таких предметов не содержит, поэтому без профиля они пропадают.
        try:
            response = api.get(root + "/profile", headers=headers, timeout=timeout)
            if getattr(response, "status", None) == 200:
                body = response.json()
                if isinstance(body, dict):
                    if not student_id:
                        student_id = (body.get("profile") or {}).get("id")
                    profile_subjects = parsers.parse_profile_subjects(body)
        except Exception:  # noqa: BLE001 — профиль не обязателен
            profile_subjects = ()
        student_id = student_id or student_hint
        if not student_id:
            return []

        date_from, date_to = config.period_date_range(period)
        url = (f"{root}/marks?student_id={student_id}"
               f"&from={date_from.isoformat()}&to={date_to.isoformat()}")
        try:
            response = api.get(url, headers={**headers, "profile-id": str(student_id)},
                               timeout=timeout)
            if getattr(response, "status", None) != 200:
                return []
            payload = response.json()
        except Exception as exc:  # noqa: BLE001
            _emit(on_status, f"Прямой API оценок недоступен: {exc}")
            return []
        subjects = parsers.parse_marks_api(payload, period.label)
        if not subjects:
            # Пустой ответ оценок — не повод выдавать «все предметы без оценок»:
            # даём шанс резервному пути (страница оценок / HTML).
            return []
        known = {s.name.casefold() for s in subjects}
        for name in profile_subjects:
            if name.casefold() not in known:
                known.add(name.casefold())
                subjects.append(SubjectGrades(name, (), period.label, "pending"))
        return subjects

    def fetch(self, period: Period | None = None,
              on_status: StatusCallback | None = None) -> FetchResult:
        """Загружает оценки в скрытом браузере с сохранённой сессией.

        Все ошибки Playwright превращаются в ``DiaryError``/``SessionExpired``/
        ``ParseError`` с русским текстом. Ресурсы закрываются в ``finally``.
        """
        responses: list[tuple[str, Any]] = []
        debug_files: list[str] = []
        html_text = ""
        try:
            if not self.has_session():
                raise SessionExpired(
                    "Сессия не найдена. Выполните вход через Госуслуги (ЕСИА)."
                )
            if period is None:
                period = config.current_period()

            self._pw = _start_playwright()
            self._browser = self._pw.chromium.launch(
                headless=bool(self.settings.get("headless", True))
            )
            self._context = self._browser.new_context(
                storage_state=str(SESSION_FILE),
                viewport={"width": 1366, "height": 900},
                locale="ru-RU",
            )
            self._page = self._context.new_page()
            page = self._page

            keywords = [
                str(k).casefold()
                for k in self.selectors.get("json_url_keywords", []) if k
            ]

            api_path = str(self.selectors.get("marks_api_path")
                           or config.MARKS_API_PATH).casefold()
            marks_page = str(self.selectors.get("marks_page_url")
                             or config.MARKS_PAGE_URL)

            auth_error_seen = False

            def on_response(response) -> None:
                """Собирает JSON-ответы ручек оценок. Ошибки чтения глушим."""
                nonlocal auth_error_seen
                try:
                    status = getattr(response, "status", None)
                    url = str(response.url or "")
                    if status in (401, 403) and (api_path in url.casefold() or "auth" in url.casefold()):
                        auth_error_seen = True
                    headers = response.headers or {}
                    if isinstance(headers, dict):
                        ct = str(
                            headers.get("content-type")
                            or headers.get("Content-Type") or ""
                        )
                    else:
                        ct = str(headers.get("content-type", ""))
                    if "json" not in ct.casefold():
                        return
                    if (keywords and not any(k in url.casefold() for k in keywords)
                            and api_path not in url.casefold()):
                        return
                    payload = response.json()
                    responses.append((url, payload))
                except Exception:
                    return

            page.on("response", on_response)

            def check_session_expired() -> None:
                current_url = str(getattr(page, "url", "") or "")
                if _is_login_url(current_url, self._login_hints):
                    raise SessionExpired(
                        "Сессия ЕСИА истекла: страница перенаправила на вход. "
                        "Выполните вход заново."
                    )

            # Основной путь: прямой запрос к API за весь период. Он не зависит
            # от того, успела ли SPA сделать свой недельный запрос.
            direct = self._fetch_via_api(period, on_status)
            if direct:
                subjects = complete_subjects(direct, period)
                with_marks = sum(1 for s in subjects if s.marks)
                note = (f"Загружено из дневника: предметов — {len(subjects)}, "
                        f"с оценками — {with_marks}.")
                graded = sum(1 for s in direct if s.marks)
                _emit(on_status,
                      f"Портал отдал {graded} предметов с оценками; "
                      f"всего предметов в профиле — {len(direct)}.")
                _emit(on_status, note)
                return FetchResult(subjects=tuple(subjects),
                                   period=period.label, source="live", note=note)

            # Резервный путь: открыть страницу оценок и перехватить запрос SPA.
            # SPA по умолчанию просит оценки только за текущую неделю. Мягко
            # подменяем from/to на весь учебный период — заголовки и токен
            # остаются родными, поэтому запрос проходит авторизацию.
            date_from, date_to = config.period_date_range(period)

            def widen_marks_range(route) -> None:
                try:
                    url = str(route.request.url)
                    url = re.sub(r"([?&])from=[^&]*",
                                 lambda m: m.group(1) + "from=" + date_from.isoformat(),
                                 url)
                    url = re.sub(r"([?&])to=[^&]*",
                                 lambda m: m.group(1) + "to=" + date_to.isoformat(),
                                 url)
                    route.continue_(url=url)
                except Exception:
                    try:
                        route.continue_()
                    except Exception:
                        pass

            page.route(f"**{api_path}*", widen_marks_range)

            _emit(on_status, f"Открываем оценки за {period.label}...")
            page.goto(
                marks_page,
                wait_until="domcontentloaded",
                timeout=int(self.settings.get("nav_timeout_s", 35)) * 1000,
            )

            check_session_expired()

            try:
                page.wait_for_load_state("networkidle", timeout=20000)
            except Exception:
                pass  # короткий таймаут networkidle — не ошибка
            if not any(api_path in url.casefold() for url, _ in responses):
                try:
                    page.wait_for_response(
                        lambda response: api_path in response.url.casefold(),
                        timeout=10000)
                    page.wait_for_timeout(800)
                except Exception:
                    pass

            check_session_expired()

            _emit(on_status, "Собираем оценки...")
            subjects = _marks_from_responses(responses, period.label, api_path)
            if subjects:
                _emit(on_status,
                      f"Портал отдал {len(subjects)} предметов за весь период.")
                subjects = complete_subjects(subjects, period)
            else:
                # Универсальный фолбэк, если у школы другая вёрстка/другой API.
                _emit(on_status, "Прямой API оценок не ответил — разбираем страницу...")
                self._soft_navigate(page, period, on_status)
                check_session_expired()
                subjects = parsers.parse_json_responses(responses, self.selectors)
                if not subjects:
                    html_text = str(page.content() or "")
                    subjects = parsers.parse_grades_html(html_text, self.selectors)
                if not subjects:
                    check_session_expired()
                    if auth_error_seen:
                        raise SessionExpired(
                            "Сессия ЕСИА истекла (код 401/403). Выполните вход заново."
                        )
                    if html_text and any(marker in html_text.casefold() for marker in (
                        "esia.gosuslugi.ru", "войти через госуслуги",
                        "авторизация через есиа", "срок действия сессии истек"
                    )):
                        raise SessionExpired(
                            "Сессия ЕСИА истекла: обнаружена страница авторизации. "
                            "Выполните вход заново."
                        )
                    debug_files = self._save_debug(html_text, responses)
                    listed = ", ".join(debug_files) if debug_files else "(дампы не записаны)"
                    raise ParseError(
                        "Не удалось найти ни одного предмета с оценками ни в "
                        "JSON-ответах, ни в HTML страницы. Диагностика сохранена: "
                        + listed
                    )
                # Портал может отдавать оценки только для открытого предмета.
                subjects = self._scan_subjects(page, responses, subjects, on_status)
                subjects = complete_subjects(subjects, period)

            urls = tuple(url for url, _ in responses)
            with_marks = sum(1 for s in subjects if s.marks)
            note = (f"Загружено из дневника: предметов — {len(subjects)}, "
                    f"с оценками — {with_marks}.")
            _emit(on_status, note)
            return FetchResult(
                subjects=tuple(subjects),
                period=period.label,
                source="live",
                urls=urls,
                note=note,
                debug_files=tuple(debug_files),
            )
        except (SessionExpired, ParseError):
            raise
        except Exception as exc:  # любые ошибки Playwright -> DiaryError
            raise DiaryError(f"Ошибка при загрузке дневника: {exc}") from exc
        finally:
            self.close()

    def _scan_subjects(self, page, responses: list, subjects: list,
                       on_status: StatusCallback | None = None) -> list[SubjectGrades]:
        """Дообирает оценки по предметам, которых не было на общей странице.

        Для каждого ожидаемого предмета без оценок кликаем по его названию,
        собираем новые JSON-ответы и HTML открытой карточки, затем возвращаемся
        к списку. Все таймауты короткие: порталы у школ разные, отсутствие
        элемента не считается ошибкой — предмет просто останется без оценок.
        """
        expected = config.expected_subjects()
        if not expected:
            return subjects
        have = {s.name.casefold(): s for s in subjects}
        todo = [name for name in expected
                if name.casefold() not in have or not have[name.casefold()].marks]
        if not todo:
            return subjects

        _emit(on_status, f"Обходим предметы без оценок: {len(todo)}...")
        collected = _merge_best(subjects)
        deadline = time.monotonic() + float(
            self.settings.get("subject_scan_timeout_s", 30)
        )
        for name in todo:
            if time.monotonic() >= deadline:
                break
            before = len(responses)
            try:
                page.get_by_text(name, exact=False).first.click(timeout=700)
            except Exception:
                continue
            try:
                page.wait_for_load_state("networkidle", timeout=900)
            except Exception:
                pass

            fresh = parsers.parse_json_responses(responses[before:], self.selectors)
            try:
                html = str(page.content() or "")
            except Exception:
                html = ""
            if html:
                fresh += parsers.parse_grades_html(html, self.selectors)
            if fresh:
                collected = _merge_best(collected, fresh)
            try:
                page.go_back(timeout=1200, wait_until="domcontentloaded")
            except Exception:
                pass

        got = sum(1 for s in collected if s.marks)
        _emit(on_status,
              f"После обхода предметов с оценками: {got} из {len(expected)}.")
        return collected

    def _soft_navigate(self, page, period: Period,
                       on_status: StatusCallback | None = None) -> None:
        """Мягко проходит в раздел оценок и выбирает период.

        Отсутствие элемента/короткий таймаут — не ошибка: у школ разная вёрстка,
        а данные часто приходят сразу через JSON.
        """
        for text in self.selectors.get("grades_nav_texts", []):
            try:
                page.get_by_text(text, exact=False).first.click(timeout=1500)
                break
            except Exception:
                continue

        for label in self.selectors.get("period_nav_texts", []):
            try:
                page.get_by_text(label, exact=False).first.click(timeout=800)
                break
            except Exception:
                continue

        for alias in config.period_aliases(period):
            try:
                page.get_by_text(alias, exact=False).first.click(timeout=800)
                break
            except Exception:
                continue

    def _save_debug(self, html_text: str,
                    responses: list[tuple[str, Any]]) -> list[str]:
        """Пишет диагностику в ``DEBUG_DIR``: HTML, список URL и каждый JSON-ответ.

        Возвращает пути записанных файлов (для сообщения ParseError). Ошибки
        записи не должны маскировать настоящую проблему парсинга.
        """
        debug_dir = Path(DEBUG_DIR)
        written: list[str] = []
        try:
            debug_dir.mkdir(parents=True, exist_ok=True)

            page_path = debug_dir / "page.html"
            page_path.write_text(str(html_text or ""), encoding="utf-8")
            written.append(str(page_path))

            urls_path = debug_dir / "urls.txt"
            urls_path.write_text(
                "\n".join(url for url, _ in responses) + "\n",
                encoding="utf-8",
            )
            written.append(str(urls_path))

            resp_dir = debug_dir / "responses"
            resp_dir.mkdir(parents=True, exist_ok=True)
            for index, (url, payload) in enumerate(responses, 1):
                path = resp_dir / f"{index:02d}.json"
                try:
                    config.atomic_write_json(path, {"url": url, "payload": payload})
                except Exception:
                    path.write_text(str(payload), encoding="utf-8")
                written.append(str(path))
        except Exception:
            pass
        return written

    # -- жизненный цикл ----------------------------------------------------- #

    def close(self) -> None:
        """Закрывает page/context/browser и останавливает Playwright, без ошибок."""
        for attr in ("_page", "_context", "_browser"):
            obj = getattr(self, attr, None)
            if obj is not None:
                try:
                    obj.close()
                except Exception:
                    pass
                setattr(self, attr, None)
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:
                pass
            self._pw = None

    def __enter__(self) -> "DiaryClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.close()
        return False


# --------------------------------------------------------------------------- #
# Тонкие обёртки для GUI (вызываются в фоновом потоке)
# --------------------------------------------------------------------------- #

def fetch_grades(period: Period | None = None,
                 on_status: StatusCallback | None = None) -> FetchResult:
    client = DiaryClient()
    try:
        return client.fetch(period=period, on_status=on_status)
    finally:
        client.close()


def interactive_login(on_status: StatusCallback | None = None,
                      timeout_s: int | None = None) -> bool:
    client = DiaryClient()
    try:
        return client.login(on_status=on_status, timeout_s=timeout_s)
    finally:
        client.close()
