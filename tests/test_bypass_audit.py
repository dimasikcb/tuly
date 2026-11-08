"""Регрессионные тесты аудита логики обхода (task-19).

Каждый тест фиксирует конкретный найденный дефект, чтобы он не вернулся:
SOCKS5-хендшейк, «прямой» IP без системного прокси, защита активного обхода
от автоисцеления, осиротевший TUN, CloseHandle(pid), мёртвый SOCKS при GBD.
"""
from __future__ import annotations

import ast
import json
import socket
import struct
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

TESTS_DIR = Path(__file__).resolve().parent
ROOT_DIR = TESTS_DIR.parent
for path in (TESTS_DIR, ROOT_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from helpers import load_module

app = load_module("bypass_core")
ROOT_FILES = {name: (ROOT_DIR / name).read_text(encoding="utf-8")
              for name in ("bypass_core.py", "tools.pyw")}


class Socks5Server:
    """Минимальный SOCKS5-сервер: greeting -> CONNECT -> HTTP с JSON."""

    def __init__(self, reply_ip: str = "185.220.101.5"):
        self.reply_ip = reply_ip
        self.seen: list[tuple] = []
        self.connections = 0
        self.stop = threading.Event()
        self.srv = socket.socket()
        self.srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.srv.bind(("127.0.0.1", 0))
        self.port = self.srv.getsockname()[1]
        self.srv.listen(5)
        self.srv.settimeout(0.2)
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    def _loop(self) -> None:
        while not self.stop.is_set():
            try:
                conn, _ = self.srv.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            self.connections += 1
            threading.Thread(target=self._handle, args=(conn,), daemon=True).start()

    def _handle(self, conn: socket.socket) -> None:
        conn.settimeout(2.0)
        try:
            greeting = conn.recv(3)
            self.seen.append(("greeting", greeting))
            if greeting != b"\x05\x01\x00":
                return
            conn.sendall(b"\x05\x00")
            request = conn.recv(512)
            self.seen.append(("connect", request))
            if len(request) < 5 or request[0] != 5:
                return
            host = b"api.ipify.org"
            conn.sendall(b"\x05\x00\x00\x03" + bytes([len(host)]) + host
                         + struct.pack(">H", 80))
            http = conn.recv(4096)
            self.seen.append(("http", http))
            body = json.dumps({"ip": self.reply_ip}).encode()
            conn.sendall(
                b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
                b"Content-Length: " + str(len(body)).encode() +
                b"\r\nConnection: close\r\n\r\n" + body
            )
        except OSError:
            pass
        finally:
            conn.close()

    def close(self) -> None:
        self.stop.set()
        self.srv.close()
        self.thread.join(timeout=2)


class SocksCheckTest(unittest.TestCase):
    def test_full_socks5_handshake_returns_ip(self) -> None:
        server = Socks5Server()
        try:
            res = app.bypass_check_proxy_ip(port=server.port, timeout=2.0)
        finally:
            server.close()
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["ip"], "185.220.101.5")
        self.assertEqual(res["via"], "socks5")
        self.assertEqual(res["proxy_port"], server.port)
        self.assertEqual(server.connections, 1, "handshake и HTTP должны идти в одном сокете")
        kinds = [item[0] for item in server.seen]
        self.assertEqual(kinds, ["greeting", "connect", "http"])
        self.assertEqual(server.seen[0][1], b"\x05\x01\x00")
        self.assertIn(b"api.ipify.org", server.seen[1][1])
        self.assertIn(b"Host: api.ipify.org", server.seen[2][1])

    def test_error_always_reports_real_port(self) -> None:
        res = app.bypass_check_proxy_ip(port=1, timeout=0.5)
        self.assertFalse(res["ok"])
        self.assertEqual(res["proxy_port"], 1)

    def test_default_port_taken_from_happ_config(self) -> None:
        with mock.patch.object(app, "happ_socks_port", lambda: 23456):
            res = app.bypass_check_proxy_ip(timeout=0.5)
        self.assertFalse(res["ok"])
        self.assertEqual(res["proxy_port"], 23456)

    def test_compare_shows_real_port_and_reason(self) -> None:
        direct = {"ok": True, "ip": "95.1.1.1", "country": "RU"}
        proxy = {"ok": False, "error": "CONNECT отклонён", "proxy_port": 23456}
        res = app.bypass_compare_connections(direct, proxy)
        self.assertEqual(res["verdict"], "SOCKS НЕ ДОСТУПЕН")
        self.assertIn("23456", res["message"])
        self.assertIn("CONNECT отклонён", res["message"])

    def test_equal_ips_explains_tun_mode(self) -> None:
        direct = {"ok": True, "ip": "95.1.1.1", "country": "RU"}
        proxy = {"ok": True, "ip": "95.1.1.1", "proxy_port": 10808}
        res = app.bypass_compare_connections(direct, proxy)
        self.assertIn("Весь трафик", res["message"])


class DirectCheckTest(unittest.TestCase):
    def test_direct_ip_opens_proxy_free_opener(self) -> None:
        import urllib.request

        class FakeOpener:
            def open(self, req, timeout=3.0):
                raise RuntimeError("no network in test")

        with mock.patch("urllib.request.build_opener",
                        return_value=FakeOpener()) as build:
            app.bypass_check_direct_ip(timeout=0.1)
        args = build.call_args[0]
        self.assertEqual(len(args), 1)
        self.assertIsInstance(args[0], urllib.request.ProxyHandler)
        self.assertEqual(args[0].proxies, {})

    def test_direct_ip_ignores_env_proxy(self) -> None:
        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                pass

            def read(self):
                return b'{"ip": "95.161.100.55"}'

        class FakeOpener:
            def open(self, req, timeout=3.0):
                return FakeResponse()

        with mock.patch.dict("os.environ", {"https_proxy": "http://127.0.0.1:9"}), \
             mock.patch("urllib.request.build_opener", return_value=FakeOpener()):
            res = app.bypass_check_direct_ip(timeout=0.1)
        self.assertEqual(res["ip"], "95.161.100.55")


class AutoHealSafetyTest(unittest.TestCase):
    def test_active_bypass_is_not_healed(self) -> None:
        tun_disable = mock.Mock()
        diag = {"tun": True, "proxy": True, "cores": False,
                "what": ["TUN"], "all_clear": False}
        with mock.patch.object(app, "_bypass_active", lambda: True), \
             mock.patch.object(app, "bypass_diagnose", lambda scope: diag), \
             mock.patch.object(app, "happ_tun_disable", tun_disable), \
             mock.patch.object(app, "set_windows_system_proxy", mock.Mock()):
            res = app.bypass_auto_heal_if_leak("apps")
        self.assertEqual(res["action"], "skipped_active")
        tun_disable.assert_not_called()

    def test_inactive_bypass_is_healed(self) -> None:
        diag = {"tun": True, "proxy": False, "cores": False,
                "what": ["TUN"], "all_clear": False}
        with mock.patch.object(app, "_bypass_active", lambda: False), \
             mock.patch.object(app, "bypass_diagnose", lambda scope: diag), \
             mock.patch.object(app, "happ_tun_disable", lambda: True):
            res = app.bypass_auto_heal_if_leak("apps")
        self.assertEqual(res["action"], "healed")

    def test_bypass_active_detects_happ(self) -> None:
        with mock.patch.object(app, "happ_connected", lambda: True), \
             mock.patch.object(app, "_svc_state", lambda name: None):
            self.assertTrue(app._bypass_active())

    def test_bypass_active_detects_running_proxyfyre(self) -> None:
        with mock.patch.object(app, "happ_connected", lambda: False), \
             mock.patch.object(app, "_svc_state", lambda name: app.SVC_RUNNING), \
             mock.patch.object(app, "proxifyre_service_name", lambda: "ProxiFyreService"):
            self.assertTrue(app._bypass_active())

    def test_no_closehandle_on_pid(self) -> None:
        """PID — не хэндл; CloseHandle в автоисцелении быть не должно."""
        tree = ast.parse(ROOT_FILES["bypass_core.py"])
        fn = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "bypass_auto_heal")
        attrs = [n.func.attr for n in ast.walk(fn)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]
        self.assertNotIn("CloseHandle", attrs)

    def test_gui_runs_heal_in_background_only(self) -> None:
        tree = ast.parse(ROOT_FILES["tools.pyw"])
        cls = next(n for n in tree.body
                   if isinstance(n, ast.ClassDef) and n.name == "BypassPanel")
        methods = {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}
        self.assertIn("_poll_state", methods)

        def calls(node):
            return {n.func.id for n in ast.walk(node)
                    if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}

        self.assertNotIn("bypass_auto_heal_if_leak", calls(methods["_tick"]))
        self.assertIn("bypass_auto_heal_if_leak", calls(methods["_poll_state"]))
        # Тик сам не трогает сеть: он ставит задачу через _start_poll,
        # а run_bg живёт уже в _start_poll.
        self.assertIn("_start_poll", [
            n.func.attr for n in ast.walk(methods["_tick"])
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        ])
        self.assertIn("run_bg", [
            n.func.attr for n in ast.walk(methods["_start_poll"])
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        ])


class TunDetectionTest(unittest.TestCase):
    def test_orphan_tun_detected_when_happ_off(self) -> None:
        def fake_run(cmd, **kw):
            if cmd[:2] == ["route", "print"]:
                return 0, "0.0.0.0/0 через happ-xray 172.19.0.1", ""
            return 1, "", ""

        with mock.patch.object(app, "run_tool", fake_run), \
             mock.patch.object(app, "happ_connected", lambda: False), \
             mock.patch.object(app, "happ_mode_read",
                               lambda: {"tun": True, "system_proxy": True}):
            self.assertTrue(app.happ_tun_active())

    def test_connected_adapter_detected_by_netsh(self) -> None:
        def fake_run(cmd, **kw):
            if cmd[:2] == ["route", "print"]:
                return 0, "нет маршрутов", ""
            if cmd[:3] == ["netsh", "interface", "show"]:
                return 0, "Админ. состояние: Подключен", ""
            return 1, "", ""

        with mock.patch.object(app, "run_tool", fake_run), \
             mock.patch.object(app, "happ_connected", lambda: False), \
             mock.patch.object(app, "happ_mode_read",
                               lambda: {"tun": False, "system_proxy": False}):
            self.assertTrue(app.happ_tun_active())

    def test_clean_machine_reports_no_tun(self) -> None:
        with mock.patch.object(app, "run_tool", lambda *a, **k: (1, "", "")), \
             mock.patch.object(app, "happ_connected", lambda: False), \
             mock.patch.object(app, "happ_mode_read",
                               lambda: {"tun": False, "system_proxy": False}):
            self.assertFalse(app.happ_tun_active())


class GbdModeTest(unittest.TestCase):
    def test_gbd_stops_proxyfyre_when_happ_is_off(self) -> None:
        calls: list[str] = []
        cfg = {"mode": "gbd", "scope": "apps", "apps": ["chrome.exe"],
               "happ_path": r"C:\Happ\Happ.exe", "gbd_args": "-5",
               "socks_port": 10808}
        with mock.patch.object(app, "bypass_cfg", lambda: dict(cfg)), \
             mock.patch.object(app, "bypass_cfg_save", lambda **kw: None), \
             mock.patch.object(app, "happ_connected", lambda *a, **k: False), \
             mock.patch.object(app, "bypass_gbd_start", lambda: {"ok": True}), \
             mock.patch.object(app, "proxifyre_stop",
                               lambda: calls.append("pf_stop") or {"detail": ""}):
            app.bypass_apply("apps", "gbd")
        self.assertIn("pf_stop", calls)

    def test_gbd_keeps_proxyfyre_when_happ_is_on(self) -> None:
        calls: list[str] = []
        cfg = {"mode": "gbd", "scope": "apps", "apps": ["chrome.exe"],
               "happ_path": r"C:\Happ\Happ.exe", "gbd_args": "-5",
               "socks_port": 10808}
        with mock.patch.object(app, "bypass_cfg", lambda: dict(cfg)), \
             mock.patch.object(app, "bypass_cfg_save", lambda **kw: None), \
             mock.patch.object(app, "happ_connected", lambda *a, **k: True), \
             mock.patch.object(app, "bypass_gbd_start", lambda: {"ok": True}), \
             mock.patch.object(app, "proxifyre_stop",
                               lambda: calls.append("pf_stop") or {"detail": ""}):
            app.bypass_apply("apps", "gbd")
        self.assertNotIn("pf_stop", calls)


class AgentAuditRegressionTest(unittest.TestCase):
    """Находки независимого аудита тремя субагентами (task-20)."""

    def test_disconnected_is_not_connected(self) -> None:
        # "Disconnected" содержит "connected" — старый substring-чек врал.
        self.assertFalse(app._iface_connected("State: Disconnected"))
        self.assertFalse(app._iface_connected("Состояние: Отключено"))
        self.assertTrue(app._iface_connected("State: Connected"))
        self.assertTrue(app._iface_connected("Состояние: Подключен"))

    def test_netsh_disconnected_not_reported_as_active_tun(self) -> None:
        def fake_run(cmd, **kw):
            if cmd[:2] == ["route", "print"]:
                return 0, "нет маршрутов", ""
            if cmd[:3] == ["netsh", "interface", "show"]:
                return 0, "Administrative state: Enabled\nState: Disconnected", ""
            return 1, "", ""

        with mock.patch.object(app, "run_tool", fake_run), \
             mock.patch.object(app, "happ_connected", lambda: False), \
             mock.patch.object(app, "happ_mode_read",
                               lambda: {"tun": False, "system_proxy": False}):
            self.assertFalse(app.happ_tun_active())

    def test_tun_disable_exits_on_disconnected_adapter(self) -> None:
        def fake_run(cmd, **kw):
            if cmd[:3] == ["netsh", "interface", "set"]:
                return 0, "", ""
            if cmd[:3] == ["netsh", "interface", "show"]:
                return 0, "State: Disconnected", ""
            if cmd[:3] == ["netsh", "interface", "delete"]:
                raise AssertionError("удалять маршрут у отключённого адаптера не нужно")
            return 1, "", ""

        with mock.patch.object(app, "run_tool", fake_run):
            self.assertTrue(app.happ_tun_disable(wait=0.01))

    def test_running_images_survives_tasklist_failure(self) -> None:
        with mock.patch.object(app, "run_tool",
                               mock.Mock(side_effect=OSError("нет tasklist"))):
            self.assertEqual(app._running_images(), set())

    def test_kill_happ_cores_includes_tun2proxy_and_happd(self) -> None:
        killed: list[str] = []

        def fake_run(cmd, **kw):
            killed.append(cmd[-1])
            return 0, "", ""

        with mock.patch.object(app, "run_tool", fake_run):
            app._kill_happ_cores()
        for image in (*app.HAPP_CORE_IMAGES, "happd.exe"):
            self.assertIn(image, killed)

    def test_stop_kills_happd_before_disabling_tun(self) -> None:
        order: list[str] = []
        with mock.patch.object(app, "proxifyre_stop", lambda: {"detail": ""}), \
             mock.patch.object(app, "bypass_happ_stop", lambda: {"detail": ""}), \
             mock.patch.object(app, "happ_auto_disconnect", lambda: True), \
             mock.patch.object(app, "_kill_happ_cores",
                               lambda: order.append("kill") or 0), \
             mock.patch.object(app, "happ_tun_disable",
                               lambda: order.append("tun_disable") or True), \
             mock.patch.object(app, "set_windows_system_proxy", lambda en: True), \
             mock.patch.object(app, "reset_tcp_connections", lambda port: 0), \
             mock.patch.object(app, "run_tool", lambda *a, **k: (1, "", "")), \
             mock.patch.object(app, "happ_socks_port", lambda: 10808):
            app.bypass_stop("happ")
        self.assertEqual(order, ["kill", "tun_disable"])


class GuiAuditRegressionTest(unittest.TestCase):
    """Находки gui-auditor: tkinter в этой сборке недоступен, проверяем AST."""

    @staticmethod
    def methods() -> dict:
        tree = ast.parse(ROOT_FILES["tools.pyw"])
        cls = next(n for n in tree.body
                   if isinstance(n, ast.ClassDef) and n.name == "BypassPanel")
        return {n.name: n for n in cls.body if isinstance(n, ast.FunctionDef)}

    @staticmethod
    def attrs(node) -> list[str]:
        return [n.attr for n in ast.walk(node) if isinstance(n, ast.Attribute)]

    @staticmethod
    def called(node) -> list[str]:
        return [n.func.attr for n in ast.walk(node)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)]

    def test_scope_is_snapshotted_in_main_thread(self) -> None:
        fn = self.methods()["_tick"]
        lambdas = [n for n in ast.walk(fn) if isinstance(n, ast.Lambda)]
        self.assertEqual(len(lambdas), 1)
        self.assertNotIn("scope", self.attrs(lambdas[0]),
                         "self.scope нельзя читать в фоновом потоке")
        self.assertIn("scope", [n.id for n in ast.walk(fn) if isinstance(n, ast.Name)])

    def test_poll_callbacks_carry_generation(self) -> None:
        methods = self.methods()
        self.assertIn("run_bg", self.called(methods["_start_poll"]))
        self.assertIn("_state_gen", self.attrs(methods["_start_poll"]))
        for name in ("_state_ok", "_state_off"):
            self.assertIn("_state_gen", self.attrs(methods[name]))

    def test_ip_check_flag_is_respected_by_controls(self) -> None:
        methods = self.methods()
        self.assertIn("ip_checking", self.attrs(methods["check_ip_now"]))
        self.assertIn("ip_checking", self.attrs(methods["_update_controls"]))

    def test_run_guards_busy(self) -> None:
        fn = self.methods()["_run"]
        self.assertIsInstance(fn.body[0], ast.If)
        self.assertIsInstance(fn.body[0].body[0], ast.Return)

    def test_apply_retries_when_busy(self) -> None:
        fn = self.methods()["_apply_apps_now"]
        self.assertIn("_apply_retry", self.attrs(fn))
        self.assertIn("after", self.called(fn))

    def test_destroy_cancels_timers(self) -> None:
        self.assertIn("after_cancel", self.called(self.methods()["destroy"]))

    def test_heal_failure_is_logged(self) -> None:
        fn = self.methods()["_state_ok"]
        source = ast.get_source_segment(ROOT_FILES["tools.pyw"], fn) or ""
        self.assertIn("failed", source)
        self.assertIn("error", source)

    def test_offline_message_is_restored_after_controls(self) -> None:
        fn = self.methods()["_state_off"]
        calls = [n.lineno for n in ast.walk(fn)
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "_update_controls"]
        sets = [n.lineno for n in ast.walk(fn)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "set"]
        self.assertTrue(calls and sets and min(sets) > min(calls),
                        "сообщение об ошибке должно ставиться ПОСЛЕ _update_controls")


if __name__ == "__main__":
    unittest.main()
