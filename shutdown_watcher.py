"""Страж выключения ПК: ждёт, пока завершат работу ИИ-агенты и чаты.

Логика без tkinter и без сторонних зависимостей — только stdlib.
Снятие процессов и CPU — через Win32 API (ctypes), psutil не нужен.
Уведомления в Telegram — через Bot API (urllib.request).
Мониторинг сессий WorkBuddy AI — через SQLite workbuddy.db в режиме read-only.

Режимы агента:
  idle    — процесс существует, но CPU ≈ 0 в течение idle_min → завершён
  process — процесс завершился (exe пропал из списка) → завершён
  marker  — агент пишет файл-маркер; появился файл → завершён
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes
import json
import os
import sqlite3
import ssl
import subprocess
import sys
import threading
import time
import urllib.request
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path

SNAPSHOT_ALL = 0x00000002 | 0x00000001
PROCESS_QUERY_LIMITED = 0x00001000
SYNCHRONIZE = 0x00100000


class Status(str, Enum):
    NOT_SEEN = "not_seen"   # не обнаружен ни разу за страж — ещё не стартовал
    WORKING = "working"     # работает либо простоит не дольше idle_min
    DONE = "done"           # завершён и держится hold_min


class Agent:
    """Один отслеживаемый агент."""
    name: str = ""
    mode: str = "idle"       # idle | process | marker
    exe: str = ""            # имя exe для idle/process
    marker: str = ""         # путь к файлу-маркеру (для mode=marker)
    idle_min: float = 15.0   # сколько минут ждать «простоя» в режиме idle
    hold_min: float = 2.0    # сколько держать DONE до голосования

    def __init__(
        self,
        name: str = None,
        mode: str = None,
        exe: str = None,
        marker: str = None,
        idle_min: float = None,
        hold_min: float = None,
    ) -> None:
        self.name = name if name is not None else getattr(self.__class__, "name", "")
        self.mode = mode if mode is not None else getattr(self.__class__, "mode", "idle")
        self.exe = exe if exe is not None else getattr(self.__class__, "exe", "")
        self.marker = marker if marker is not None else getattr(self.__class__, "marker", "")
        self.idle_min = float(idle_min) if idle_min is not None else getattr(self.__class__, "idle_min", 15.0)
        self.hold_min = float(hold_min) if hold_min is not None else getattr(self.__class__, "hold_min", 2.0)


@dataclass
class AgentResult:
    name: str
    status: Status
    note: str
    hold_ok: bool


class _ProcessEntry(ctypes.Structure):
    _fields_ = [
        ("dwSize", ctypes.wintypes.DWORD),
        ("cntUsage", ctypes.wintypes.DWORD),
        ("th32ProcessID", ctypes.wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", ctypes.wintypes.DWORD),
        ("cntThreads", ctypes.wintypes.DWORD),
        ("th32ParentProcessID", ctypes.wintypes.DWORD),
        ("pcPriClassBase", ctypes.wintypes.LONG),
        ("dwFlags", ctypes.wintypes.DWORD),
        ("szExeFile", ctypes.c_char * 260),
    ]


def _snapshot_processes() -> list[dict]:
    """Текущие процессы: [{'exe': lower, 'pid': int, 'cpu_ms': int}]."""
    out: list[dict] = []
    h = ctypes.windll.kernel32.CreateToolhelp32Snapshot(SNAPSHOT_ALL, 0)
    if h == -1:
        return out
    try:
        entry = _ProcessEntry()
        entry.dwSize = ctypes.sizeof(_ProcessEntry)
        ok = ctypes.windll.kernel32.Process32First(h, ctypes.byref(entry))
        while ok:
            pid = entry.th32ProcessID
            exe = entry.szExeFile.decode("utf-8", "ignore").lower()
            out.append({"exe": exe, "pid": pid, "cpu_ms": _process_cpu_ms(pid)})
            ok = ctypes.windll.kernel32.Process32Next(h, ctypes.byref(entry))
    finally:
        ctypes.windll.kernel32.CloseHandle(h)
    return out


def _process_cpu_ms(pid: int) -> int:
    """Время CPU процесса за всю жизнь, мс (0 — недоступен)."""
    h = ctypes.windll.kernel32.OpenProcess(
        PROCESS_QUERY_LIMITED | SYNCHRONIZE, False, pid)
    if not h:
        return 0
    try:
        create = ctypes.c_uint(0)
        exit = ctypes.c_uint(0)
        kernel = ctypes.c_uint(0)
        user = ctypes.c_uint(0)
        ok = ctypes.windll.kernel32.GetProcessTimes(
            h, ctypes.byref(create), ctypes.byref(exit),
            ctypes.byref(kernel), ctypes.byref(user))
        return int(kernel.value + user.value) if ok else 0
    finally:
        ctypes.windll.kernel32.CloseHandle(h)


def _secs_clamp(seconds: int) -> int:
    """Windows принимает только целые 0..315360000."""
    return int(max(0, min(int(seconds), 315360000)))


class TelegramNotifier:
    """Отправка уведомлений в Telegram через Bot API (только stdlib urllib)."""

    def __init__(self, bot_token: str = "", chat_id: int | str = "", enabled: bool = True) -> None:
        self.bot_token = str(bot_token).strip()
        self.chat_id = str(chat_id).strip()
        self.enabled = bool(enabled and self.bot_token and self.chat_id)

    def send(self, text: str, sync: bool = False) -> bool:
        """Отправка сообщения. По умолчанию выполняется в фоне, чтобы не задерживать цикл."""
        if not self.enabled:
            return False
        if sync:
            return self._send_sync(text)
        thread = threading.Thread(target=self._send_sync, args=(text,), daemon=True)
        thread.start()
        return True

    def _send_sync(self, text: str) -> bool:
        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        payload = json.dumps({
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
        }).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        ctx = ssl._create_unverified_context()
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=8) as resp:
                return resp.status == 200
        except Exception:
            return False


class WorkBuddyChatTracker:
    """Мониторинг статусов сессий WorkBuddy AI через workbuddy.db (read-only)."""

    def __init__(self, db_path: Path | str | None = None) -> None:
        if db_path is None:
            self.db_path = Path.home() / ".workbuddy-ai" / "workbuddy.db"
        else:
            self.db_path = Path(db_path)
        self._statuses: dict[str, str] = {}
        self._titles: dict[str, str] = {}
        self._initialized = False

    def is_available(self) -> bool:
        return self.db_path.exists()

    def poll(self) -> list[tuple[str, str, str]]:
        """Возвращает список переходов статусов: [(session_id, title, new_status), ...]."""
        if not self.is_available():
            return []
        try:
            uri = f"file:{self.db_path.as_posix()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=3.0)
            conn.execute("PRAGMA query_only = ON;")
            cursor = conn.cursor()
            cursor.execute("SELECT id, title, status, updated_at FROM sessions ORDER BY updated_at DESC LIMIT 50")
            rows = cursor.fetchall()
            conn.close()
        except Exception:
            return []

        transitions = []
        if not self._initialized:
            for session_id, title, status, _ in rows:
                self._statuses[session_id] = status
                self._titles[session_id] = title or "Без названия"
            self._initialized = True
            return []

        for session_id, title, status, _ in rows:
            clean_title = title or self._titles.get(session_id, "Без названия")
            self._titles[session_id] = clean_title
            prev_status = self._statuses.get(session_id)
            if prev_status == "working" and status in ("completed", "error"):
                transitions.append((session_id, clean_title, status))
            self._statuses[session_id] = status

        return transitions

    def has_active_chats(self) -> bool:
        """True, если хотя бы один чат сейчас находится в статусе 'working'."""
        if not self.is_available():
            return False
        try:
            uri = f"file:{self.db_path.as_posix()}?mode=ro"
            conn = sqlite3.connect(uri, uri=True, timeout=3.0)
            conn.execute("PRAGMA query_only = ON;")
            cursor = conn.cursor()
            cursor.execute("SELECT count(id) FROM sessions WHERE status = 'working'")
            row = cursor.fetchone()
            conn.close()
            return bool(row and row[0] > 0)
        except Exception:
            return False


class Watcher:
    """Снимает состояние агентов и решает, можно ли гасить ПК."""

    def __init__(
        self,
        agents: list[Agent] = None,
        interval: float = 5.0,
        notifier: TelegramNotifier | None = None,
        chat_tracker: WorkBuddyChatTracker | None = None,
    ) -> None:
        self.agents: list[Agent] = list(agents) if agents else []
        self.interval = max(2.0, interval)
        self.notifier = notifier
        self.chat_tracker = chat_tracker
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._seen: dict[str, bool] = {}
        self._idle_since: dict[str, float] = {}
        self._done_since: dict[str, float] = {}
        self._notified_agents: set[str] = set()
        self._procs: list[dict] = []
        self._last_cpu: dict[int, int] = {}
        self.lines: list[str] = []
        self.armed = False
        self.armed_until = 0.0
        self._lock = threading.Lock()

    # -- журналирование ---------------------------------------------------- #
    def _log(self, text: str) -> None:
        with self._lock:
            self.lines.append(f"{datetime.now():%H:%M:%S} | {text}")

    # -- разовые проверки -------------------------------------------------- #
    def _done(self, agent: Agent, note: str) -> AgentResult:
        now = time.monotonic()
        if agent.name not in self._done_since:
            self._done_since[agent.name] = now
        held_sec = now - self._done_since[agent.name]
        hold_target_sec = max(0.0, agent.hold_min * 60.0)
        hold_ok = held_sec >= hold_target_sec
        return AgentResult(agent.name, Status.DONE, note, hold_ok)

    def check_agent(self, agent: Agent) -> AgentResult:
        exe = agent.exe.strip().lower()
        if agent.mode == "marker":
            path = Path(agent.marker)
            if path.exists():
                return self._done(agent, "маркер появился")
            self._seen[agent.name] = True
            self._idle_since.pop(agent.name, None)
            self._done_since.pop(agent.name, None)
            return AgentResult(agent.name, Status.WORKING, "ждём маркер", False)
        if exe not in {p["exe"] for p in self._procs}:
            self._idle_since.pop(agent.name, None)
            if not self._seen.get(agent.name):
                return AgentResult(agent.name, Status.NOT_SEEN,
                                   f"{exe} не видно", False)
            return self._done(agent, "процесс завершился")
        if agent.mode == "process":
            self._seen[agent.name] = True
            self._idle_since.pop(agent.name, None)
            self._done_since.pop(agent.name, None)
            return AgentResult(agent.name, Status.WORKING, exe, False)
        # idle: считаем активность по приращению CPU за интервал
        self._seen[agent.name] = True
        busy = self._any_busy_percent(exe)
        if busy >= 0.3:
            self._idle_since.pop(agent.name, None)
            self._done_since.pop(agent.name, None)
            return AgentResult(agent.name, Status.WORKING,
                               f"{exe} занят ({busy:.0f}%)", False)
        now = time.monotonic()
        if agent.name not in self._idle_since:
            self._idle_since[agent.name] = now
        idle_sec = int(now - self._idle_since[agent.name])
        idle_target = int(agent.idle_min * 60.0)
        if idle_sec < idle_target:
            return AgentResult(agent.name, Status.WORKING,
                               f"{exe} простаивает {idle_sec}/{idle_target} с", False)
        return self._done(agent, f"{exe} простаивает")

    def _any_busy_percent(self, exe: str) -> float:
        max_pct = 0.0
        for proc in self._procs:
            if proc["exe"] != exe:
                continue
            prev_cpu = self._last_cpu.get(proc["pid"])
            if prev_cpu is None:
                delta = proc["cpu_ms"]
            else:
                delta = proc["cpu_ms"] - prev_cpu
            pct = (delta / (self.interval * 1000.0)) * 100.0
            max_pct = max(max_pct, pct)
        return max_pct

    def can_stop_pc(self) -> tuple[bool, list[AgentResult]]:
        """True, если все агенты в DONE, держат hold_min и нет активных чатов."""
        if not self.agents:
            return False, []
        if self.chat_tracker and self.chat_tracker.has_active_chats():
            results = [self.check_agent(a) for a in self.agents]
            return False, results
        results = [self.check_agent(a) for a in self.agents]
        if not results or any(r.status != Status.DONE or not r.hold_ok for r in results):
            return False, results
        return True, results

    # -- главный цикл ------------------------------------------------------- #
    def start(self, on_update=None) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.stop()
        self._seen.clear()
        self._idle_since.clear()
        self._done_since.clear()
        self._notified_agents.clear()
        self.armed = False
        self.armed_until = 0.0
        self._stop.clear()
        self._snap()
        self._log(f"страж запущен, наблюдаем {len(self.agents)} агента(ов)")
        self._thread = threading.Thread(target=self._loop, args=(on_update,),
                                        daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=2.0)

    def _loop(self, on_update):
        try:
            while not self._stop.is_set():
                time.sleep(self.interval)
                self._snap()

                # 1. Проверяем переходы чатов WorkBuddy
                if self.chat_tracker:
                    for _sid, title, new_status in self.chat_tracker.poll():
                        if new_status == "completed":
                            msg = f"✅ [WorkBuddy] Чат «{title}» успешно завершил работу!"
                            self._log(msg)
                            if self.notifier:
                                self.notifier.send(msg)
                        elif new_status == "error":
                            msg = f"⚠️ [WorkBuddy] Чат «{title}» завершился с ошибкой!"
                            self._log(msg)
                            if self.notifier:
                                self.notifier.send(msg)

                # 2. Проверяем состояние процессов агентов
                ok, results = self.can_stop_pc()
                for r in results:
                    if r.status == Status.NOT_SEEN:
                        self._log(f"{r.name}: {r.note}")
                    elif r.status == Status.DONE and not r.hold_ok:
                        self._log(f"{r.name}: завершено, ждём стабилизации")
                    elif r.status == Status.DONE and r.hold_ok:
                        if r.name not in self._notified_agents:
                            self._notified_agents.add(r.name)
                            msg = f"🔔 Агент «{r.name}» завершил работу! ({r.note})"
                            self._log(msg)
                            if self.notifier:
                                self.notifier.send(msg)

                # 3. Если все агенты и чаты готовы — выключаем ПК
                if ok and not self.armed:
                    self.order_shutdown(seconds=60)
                    msg = "🔌 Все агенты и чаты завершили работу! Компьютер выключится через 60 с."
                    self._log(msg)
                    if self.notifier:
                        self.notifier.send(msg)

                if on_update:
                    try:
                        on_update()
                    except Exception:  # noqa: BLE001
                        pass
        finally:
            self._log("страж остановлен")

    def _snap(self) -> None:
        self._last_cpu = {p["pid"]: p["cpu_ms"] for p in self._procs}
        self._procs = _snapshot_processes()

    # -- выключение --------------------------------------------------------- #
    def order_shutdown(self, seconds: int = 60) -> bool:
        """Запланирует выключение. False — если команда отклонена."""
        sec = _secs_clamp(seconds)
        try:
            proc = subprocess.run(
                ["shutdown", "/s", "/t", str(sec)],
                capture_output=True, text=True, timeout=10)
        except Exception:  # noqa: BLE001
            return False
        if proc.returncode == 0:
            self.armed = True
            self.armed_until = time.monotonic() + sec
            return True
        return False

    def cancel_shutdown(self) -> bool:
        """Отменяет запланированное выключение."""
        try:
            proc = subprocess.run(
                ["shutdown", "/a"], capture_output=True, text=True, timeout=10)
        except Exception:  # noqa: BLE001
            return False
        if proc.returncode == 0:
            self.armed = False
            self.armed_until = 0.0
            self._log("выключение отменено")
            if self.notifier:
                self.notifier.send("🛑 Выключение ПК отменено пользователем.")
            return True
        return False

    # -- чтение для GUI ------------------------------------------------------ #
    def summary(self) -> list[AgentResult]:
        return [self.check_agent(a) for a in self.agents]

    def pending_seconds(self) -> int:
        if not self.armed:
            return 0
        return max(0, int(self.armed_until - time.monotonic()))

    def take_lines(self, count: int = 400) -> list[str]:
        with self._lock:
            return list(self.lines[-count:])


class WatcherError(Exception):
    """Неконсистентная конфигурация стража (например, нет агентов)."""


def load_config() -> dict:
    """Загружает параметры из config.json."""
    config_path = Path(__file__).resolve().parent / "config.json"
    if config_path.exists():
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {}


def main() -> None:
    """Точка входа для запуска стража из консоли или ярлыка."""
    import argparse
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    cfg = load_config()
    tg_cfg = cfg.get("telegram", {})
    watcher_cfg = cfg.get("watcher", {})

    parser = argparse.ArgumentParser(description="Страж выключения ПК: ждёт завершения ИИ-агентов и чатов.")
    parser.add_argument("--exe", action="append", default=[], help="Имя exe процесса агента (по умолчанию: workbuddyai.exe)")
    parser.add_argument("--dry-run", action="store_true", help="Режим симуляции (без реального вызова shutdown)")
    parser.add_argument("--interval", type=float, default=5.0, help="Интервал проверки в секундах (мин 2)")
    parser.add_argument("--hold", type=float, default=watcher_cfg.get("hold_min", 2.0), help="Минут стабилизации после завершения (по умолчанию 2.0)")
    parser.add_argument("--shutdown-delay", type=int, default=watcher_cfg.get("shutdown_delay", 60), help="Таймаут до выключения в секундах (по умолчанию 60)")
    parser.add_argument("--tg-token", default=tg_cfg.get("bot_token", ""), help="Токен Telegram бота")
    parser.add_argument("--tg-chat-id", default=str(tg_cfg.get("chat_id", "")), help="Chat ID в Telegram")
    parser.add_argument("--no-tg", action="store_true", help="Отключить отправку в Telegram")
    parser.add_argument("--no-chats", action="store_true", help="Отключить мониторинг чатов WorkBuddy")
    parser.add_argument("--test-tg", action="store_true", help="Отправить тестовое сообщение в Telegram и выйти")

    args = parser.parse_args()

    tg_enabled = not args.no_tg and tg_cfg.get("enabled", True)
    notifier = TelegramNotifier(
        bot_token=args.tg_token,
        chat_id=args.tg_chat_id,
        enabled=tg_enabled,
    )

    if args.test_tg:
        print(f"Отправка тестового сообщения в Telegram (chat_id: {notifier.chat_id})...")
        ok = notifier.send("🤖 <b>Тест стража выключения ПК:</b> Telegram-уведомления успешно подключены!", sync=True)
        if ok:
            print("Тестовое сообщение успешно доставлено в Telegram!")
            return
        else:
            print("Ошибка при отправке в Telegram. Проверьте токен, chat_id и подключение к интернету.")
            sys.exit(1)

    targets = args.exe if args.exe else ["workbuddyai.exe"]
    agents = [Agent(name=t, mode="process", exe=t, hold_min=args.hold) for t in targets]

    chat_tracker = None
    if not args.no_chats and watcher_cfg.get("track_workbuddy_chats", True):
        chat_tracker = WorkBuddyChatTracker()

    watcher = Watcher(
        agents=agents,
        interval=args.interval,
        notifier=notifier,
        chat_tracker=chat_tracker,
    )

    print(f"=== Страж выключения ПК запущен ===")
    print(f"Отслеживаемые процессы: {', '.join(targets)}")
    if chat_tracker and chat_tracker.is_available():
        print(f"Мониторинг сессий WorkBuddy: ВКЛЮЧЁН ({chat_tracker.db_path})")
    if notifier.enabled:
        print(f"Telegram-уведомления: ВКЛЮЧЕНЫ (chat_id: {notifier.chat_id})")
        notifier.send("🛡️ <b>Страж выключения ПК запущен</b> и ожидает завершения агентов.")
    print(f"Ожидание стабилизации: {args.hold} мин, задержка выключения: {args.shutdown_delay} сек")
    if args.dry_run:
        print("[DRY-RUN] Реальное выключение отключено")

    try:
        watcher._snap()
        if chat_tracker:
            chat_tracker.poll()  # инициализируем снимок сессий
        while True:
            time.sleep(watcher.interval)
            watcher._snap()

            if chat_tracker:
                for _sid, title, status in chat_tracker.poll():
                    if status == "completed":
                        msg = f"✅ [WorkBuddy] Чат «{title}» успешно завершил работу!"
                        print(f"[{datetime.now():%H:%M:%S}] {msg}")
                        notifier.send(msg)
                    elif status == "error":
                        msg = f"⚠️ [WorkBuddy] Чат «{title}» завершился с ошибкой!"
                        print(f"[{datetime.now():%H:%M:%S}] {msg}")
                        notifier.send(msg)

            ok, results = watcher.can_stop_pc()
            status_parts = [f"{r.name}: {r.status.value} ({r.note})" for r in results]
            if chat_tracker and chat_tracker.has_active_chats():
                status_parts.append("WorkBuddy чаты: работают")
            print(f"[{datetime.now():%H:%M:%S}] " + " | ".join(status_parts))

            for r in results:
                if r.status == Status.DONE and r.hold_ok and r.name not in watcher._notified_agents:
                    watcher._notified_agents.add(r.name)
                    msg = f"🔔 Агент «{r.name}» завершил работу! ({r.note})"
                    notifier.send(msg)

            if ok and not watcher.armed:
                if args.dry_run:
                    print(">>> Все агенты и чаты завершены! [DRY-RUN] Выключение не вызывается.")
                    notifier.send("✅ Все агенты и чаты завершены! [DRY-RUN] ПК не выключается.")
                    break
                print(f">>> Все агенты и чаты завершены! Выключение ПК через {args.shutdown_delay} с...")
                notifier.send(f"🔌 Все агенты и чаты завершили работу! Компьютер будет выключен через {args.shutdown_delay} с.")
                watcher.order_shutdown(seconds=args.shutdown_delay)
                break
    except KeyboardInterrupt:
        print("\nОстановлено пользователем.")
        if watcher.armed:
            watcher.cancel_shutdown()


if __name__ == "__main__":
    main()
