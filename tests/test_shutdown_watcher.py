"""Offline-тесты shutdown_watcher (без системы, без tkinter)."""
from __future__ import annotations

import importlib
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import shutdown_watcher as W


def _proc(exe: str, pid: int, cpu_ms: int) -> dict:
    return {"exe": exe.lower(), "pid": pid, "cpu_ms": cpu_ms}


class _Agent(W.Agent):
    name = "тест"
    mode = "process"
    exe = "workbuddyaI.exe"


class WatcherTests(unittest.TestCase):
    def setUp(self):
        self.w = W.Watcher([_Agent()], interval=60.0)

    def tearDown(self):
        self.w.stop()

    def test_not_seen_never_counts_done(self):
        """Пока процесс не увиден ни раз — страж не должен гасить ПК."""
        self.w._procs = []
        ok, results = self.w.can_stop_pc()
        self.assertFalse(ok)
        self.assertEqual(results[0].status, W.Status.NOT_SEEN)

    def test_process_gone_after_seen_finished(self):
        self.w._procs = [_proc("workbuddyaI.exe", 1, 10_000)]
        r1 = self.w.check_agent(_Agent())   # записывает seen
        self.assertEqual(r1.status, W.Status.WORKING)
        self.w._procs = []
        r2 = self.w.check_agent(_Agent())
        self.assertEqual(r2.status, W.Status.DONE)
        self.assertFalse(r2.hold_ok)

    def test_idle_busy_stays_working(self):
        """CPU растёт за интервал — агент работает, даже если idle_min дальше."""
        self.w._last_cpu = {7: 900}
        self.w._procs = [_proc("idleapp.exe", 7, 4_000)]
        agent = W.Agent("гаджет", mode="idle", exe="idleapp.exe", idle_min=5, hold_min=1)
        r = self.w.check_agent(agent)
        self.assertEqual(r.status, W.Status.WORKING)
        self.assertIn("занят", r.note)

    def test_hold_min_gate_delays_shutdown(self):
        """DONE держит hold_min, прежде чем переголосовать на выключение."""
        self.w._procs = [_proc("workbuddyaI.exe", 1, 999)]
        r_active = self.w.check_agent(_Agent())
        self.assertEqual(r_active.status, W.Status.WORKING)
        self.w._procs = []
        r_done = self.w.check_agent(_Agent())
        self.assertTrue(r_done.status == W.Status.DONE)
        self.assertFalse(r_done.hold_ok)  # hold 2 мин ещё не прошло
        ok, _ = self.w.can_stop_pc()
        self.assertFalse(ok)
        # симулируем прошедшие hold_min через мок времени
        with mock.patch.object(W.time, "monotonic", return_value=time.time() + 180):
            ok2, res2 = self.w.can_stop_pc()
        self.assertTrue(ok2)
        self.assertTrue(res2[0].hold_ok)

    def test_idle_min_gate(self):
        """Простой короче idle_min — всё ещё WORKING, а не DONE."""
        agent = W.Agent("gui-app", mode="idle", exe="idleapp.exe", idle_min=5, hold_min=0)
        self.w._last_cpu = {7: 1_000}
        self.w._procs = [_proc("idleapp.exe", 7, 1_000)]
        r = self.w.check_agent(agent)
        self.assertEqual(r.status, W.Status.WORKING)
        self.assertIn("простаивает 0/300", r.note)

    def test_marker_file(self):
        with tempfile.TemporaryDirectory() as d:
            marker = str(Path(d) / "ready.txt")
            agent = W.Agent("телеграм-бот", mode="marker", marker=marker)
            self.w._procs = []
            before = self.w.check_agent(agent)
            self.assertEqual(before.status, W.Status.WORKING)
            Path(marker).write_text("ok")
            after = self.w.check_agent(agent)
            self.assertEqual(after.status, W.Status.DONE)

    def test_empty_agents_never_shuts(self):
        empty = W.Watcher([], interval=60.0)
        empty._procs = []
        ok, _ = empty.can_stop_pc()
        self.assertFalse(ok)

    def test_shutdown_command_armed(self):
        w = W.Watcher([], interval=60.0)
        fake = mock.Mock(returncode=0)
        with mock.patch.object(W.subprocess, "run", return_value=fake):
            armed = w.order_shutdown(seconds=42)
        self.assertTrue(armed)
        self.assertTrue(w.armed)
        self.assertGreater(w.pending_seconds(), 0)

    def test_cancel_shutdown_dearms(self):
        w = W.Watcher([], interval=60.0)
        fake = mock.Mock(returncode=0)
        with mock.patch.object(W.subprocess, "run", return_value=fake):
            w.order_shutdown(seconds=50)
            self.assertTrue(w.armed)
            w.cancel_shutdown()
        self.assertFalse(w.armed)
        self.assertEqual(w.pending_seconds(), 0)

    def test_secs_clamp(self):
        self.assertEqual(W._secs_clamp(5), 5)
        self.assertEqual(W._secs_clamp(-3), 0)
        self.assertEqual(W._secs_clamp(10**9), 315360000)

    def test_watcher_blocks_shutdown_when_chats_active(self):
        agent = W.Agent("тест", mode="process", exe="workbuddyaI.exe", hold_min=0)
        self.w.agents = [agent]
        self.w._procs = [_proc("workbuddyaI.exe", 1, 999)]
        self.w.check_agent(agent)  # seen
        self.w._procs = []  # gone

        mock_tracker = mock.MagicMock(spec=W.WorkBuddyChatTracker)
        mock_tracker.has_active_chats.return_value = True
        self.w.chat_tracker = mock_tracker

        ok, results = self.w.can_stop_pc()
        self.assertFalse(ok)  # blocked by active chat!
        self.assertEqual(results[0].status, W.Status.DONE)

        mock_tracker.has_active_chats.return_value = False
        ok, results = self.w.can_stop_pc()
        self.assertTrue(ok)  # allowed!


class TelegramNotifierTests(unittest.TestCase):
    def test_disabled_if_empty_credentials(self):
        tn1 = W.TelegramNotifier("", "")
        self.assertFalse(tn1.enabled)
        self.assertFalse(tn1.send("привет"))

        tn2 = W.TelegramNotifier("123", "456", enabled=False)
        self.assertFalse(tn2.enabled)
        self.assertFalse(tn2.send("привет"))

    def test_send_sync_success(self):
        tn = W.TelegramNotifier("123:token", "999", enabled=True)
        fake_resp = mock.MagicMock()
        fake_resp.status = 200
        fake_resp.__enter__.return_value = fake_resp

        with mock.patch("urllib.request.urlopen", return_value=fake_resp) as mock_urlopen:
            ok = tn.send("тестовое сообщение", sync=True)
            self.assertTrue(ok)
            self.assertTrue(mock_urlopen.called)
            req = mock_urlopen.call_args[0][0]
            self.assertIn("123:token", req.full_url)
            self.assertIn(b"999", req.data)

    def test_send_sync_failure_does_not_raise(self):
        tn = W.TelegramNotifier("123:token", "999", enabled=True)
        with mock.patch("urllib.request.urlopen", side_effect=Exception("network down")):
            ok = tn.send("сообщение", sync=True)
            self.assertFalse(ok)


class WorkBuddyChatTrackerTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "workbuddy.db"
        conn = sqlite3.connect(self.db_path)
        conn.execute("""
            CREATE TABLE sessions (
                id TEXT PRIMARY KEY,
                title TEXT,
                status TEXT,
                updated_at INTEGER
            )
        """)
        conn.commit()
        conn.close()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _insert_session(self, sid: str, title: str, status: str, updated_at: int):
        conn = sqlite3.connect(self.db_path)
        conn.execute(
            "INSERT OR REPLACE INTO sessions (id, title, status, updated_at) VALUES (?, ?, ?, ?)",
            (sid, title, status, updated_at),
        )
        conn.commit()
        conn.close()

    def test_initial_poll_seeds_baseline(self):
        self._insert_session("s1", "Поиск багов", "working", 100)
        tracker = W.WorkBuddyChatTracker(self.db_path)
        transitions = tracker.poll()
        self.assertEqual(transitions, [])
        self.assertTrue(tracker._initialized)

    def test_poll_detects_completed_and_error(self):
        self._insert_session("s1", "Поиск багов", "working", 100)
        self._insert_session("s2", "Написание тестов", "working", 100)
        tracker = W.WorkBuddyChatTracker(self.db_path)
        tracker.poll()  # baseline

        # Переводим s1 в completed, s2 в error
        self._insert_session("s1", "Поиск багов", "completed", 200)
        self._insert_session("s2", "Написание тестов", "error", 200)

        transitions = tracker.poll()
        self.assertEqual(len(transitions), 2)
        trans_dict = {sid: (title, status) for sid, title, status in transitions}
        self.assertEqual(trans_dict["s1"], ("Поиск багов", "completed"))
        self.assertEqual(trans_dict["s2"], ("Написание тестов", "error"))

        # Повторный вызов без изменений не должен генерировать события
        self.assertEqual(tracker.poll(), [])

    def test_has_active_chats(self):
        tracker = W.WorkBuddyChatTracker(self.db_path)
        self.assertFalse(tracker.has_active_chats())

        self._insert_session("s1", "В работе", "working", 100)
        self.assertTrue(tracker.has_active_chats())

        self._insert_session("s1", "В работе", "completed", 200)
        self.assertFalse(tracker.has_active_chats())

    def test_unavailable_when_db_missing(self):
        tracker = W.WorkBuddyChatTracker(Path(self.temp_dir.name) / "non_existent.db")
        self.assertFalse(tracker.is_available())
        self.assertEqual(tracker.poll(), [])
        self.assertFalse(tracker.has_active_chats())

    def test_new_pid_does_not_spike_cpu(self):
        """Новый подпроцесс (prev_cpu is None) не должен давать ложный всплеск CPU."""
        w = W.Watcher([], interval=5.0)
        w._dt = 5.0
        w._last_cpu = {}  # PID 10 ещё не было в базе
        w._procs = [_proc("zcode.exe", 10, 500_000)]
        pct = w._any_busy_percent("zcode.exe")
        self.assertEqual(pct, 0.0)

    def test_low_idle_cpu_not_considered_busy(self):
        """Фоновый шум Electron (1% CPU) не считается 'занят' при пороге 3%."""
        w = W.Watcher([], interval=5.0, busy_threshold=3.0)
        w._dt = 5.0
        w._last_cpu = {10: 1000}
        # 50 мс за 5 секунд = 1.0% CPU
        w._procs = [_proc("zcode.exe", 10, 1050)]
        agent = W.Agent("ZCode", mode="idle", exe="zcode.exe", idle_min=5)
        r = w.check_agent(agent)
        self.assertNotIn("занят", r.note)

    def test_unstarted_agent_skipped_hold_immediately(self):
        """Не запускавшийся агент пропускается и сразу получает hold_ok=True."""
        w = W.Watcher([], interval=5.0)
        agent = W.Agent("Тест", mode="idle", exe="test_unstarted.exe")
        w._procs = []
        w._started = time.monotonic() - 300  # grace прошёл
        r = w.check_agent(agent)
        self.assertEqual(r.status, W.Status.DONE)
        self.assertTrue(r.hold_ok)
        self.assertIn("не запускался — пропущен", r.note)

    def test_no_cursor_in_default_agents(self):
        """Cursor не должен присутствовать в дефолтных агентах."""
        default_names = [a["name"] for a in W.DEFAULT_AGENTS]
        self.assertNotIn("Cursor", default_names)
        self.assertNotIn("cursor.exe", [a.get("exe", "") for a in W.DEFAULT_AGENTS])


if __name__ == "__main__":
    unittest.main(verbosity=2)
