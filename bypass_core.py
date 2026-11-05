"""Bypass: Happ / GoodbyeDPI / ProxiFyre — состояние, запуск, применение настроек.

Модуль без tkinter: только реестр, процессы, службы и файлы. GUI вызывает
`bypass_status`, `bypass_apply`, `bypass_stop` и рисует ответ.

Почему отдельно от `tools.pyw`: раньше эти строки жили в GUI-файле, и
проверить логику «что включается и в каком порядке» без окна было нельзя.
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path

from apps_catalog import expand_companions, normalize_apps, running_processes
from winbase import NO_WINDOW, config_read, config_update, is_admin, ps_run, run_tool

BYPASS_DIR = Path(os.environ.get("LOCALAPPDATA") or str(Path.home())) / "Tools" / "bypass"
BYPASS_GBD_DIR = BYPASS_DIR / "goodbyedpi"
BYPASS_GBD_EXE = BYPASS_GBD_DIR / "goodbyedpi.exe"
BYPASS_GBD_REPO = "ValdikSS/GoodbyeDPI"
BYPASS_GBD_ASSET = r"goodbyedpi-\d[\w.]*\.zip"
BYPASS_STATE_FILE = BYPASS_DIR / "state.json"
HAPP_CORE_IMAGES = ("xray.exe", "sing-box.exe", "tun2proxy-bin.exe", "tun2proxy.exe")
BYPASS_SCOPES = ("all", "apps")
HAPP_SOCKS_DEFAULT = 10808
HAPP_PREFS_KEY = r"Software\Happ\OrganizationDefaults\Preferences\AdvancedSettings"
HAPP_CONFIG_FILE = (Path(os.environ.get("LOCALAPPDATA") or str(Path.home()))
                    / "Happ" / "config.json")
# Сайты, которые ломаются при выходе с зарубежного IP (Яндекс, Госуслуги,
# банки). Их пускаем напрямую, даже когда включён режим «весь трафик».
HAPP_DIRECT_DOMAINS = (
    "yandex.ru", ".yandex.ru", "yandex.com", ".yandex.com",
    "ya.ru", ".ya.ru", ".yandex.net", ".yandex.kz", ".yandex.by",
    ".yandex.uz", ".kinopoisk.ru", ".yandexcloud.ru",
    ".mail.ru", ".vk.com", ".ok.ru", ".rutube.ru",
    ".sberbank.ru", ".tinkoff.ru", ".gosuslugi.ru",
)
HAPP_DIRECT_CIDR = (
    "127.0.0.0/8", "10.0.0.0/8", "172.16.0.0/12",
    "192.168.0.0/16", "169.254.0.0/16",
)
PROXIFYRE_DIR = (Path(os.environ.get("LOCALAPPDATA") or str(Path.home()))
                 / "BypassManager" / "proxifyre")
PROXIFYRE_EXE = PROXIFYRE_DIR / "ProxiFyre.exe"
PROXIFYRE_CONFIG = PROXIFYRE_DIR / "app-config.json"
PROXIFYRE_LOG_DIR = PROXIFYRE_DIR / "logs"
PROXIFYRE_SERVICE = "ProxiFyreService"
PROXIFYRE_SERVICE_ALIASES = ("ProxiFyreService", "ProxiFyre")
_PROXIFYRE_SERVICE_NAME: str | None = None
PACKET_FILTER_DRIVER = "ndisrd"
PACKET_FILTER_DRIVER_ALIASES = ("ndisrd", "WinpkFilter", "ndisrd_lwf", "ndisrd64")
SVC_STOPPED = 1
SVC_RUNNING = 4
_ADVAPI_READY = False


def _find_happ() -> Path:
    """Самостоятельно ищет установленный Happ."""
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")):
        candidate = Path(base) / "FlyFrogLLC" / "Happ" / "Happ.exe"
        if candidate.exists():
            return candidate
    pf = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    try:
        for item in pf.glob("*/Happ/Happ.exe"):
            return item
    except Exception:  # noqa: BLE001
        pass
    return pf / "FlyFrogLLC" / "Happ" / "Happ.exe"


BYPASS_HAPP_DEFAULT = str(_find_happ())


def bypass_cfg() -> dict:
    """Настройки обхода: движок, область (весь трафик / программы), список программ."""
    base = config_read().get("bypass") or {}
    mode = str(base.get("mode") or "happ").strip().lower()
    if mode not in ("happ", "gbd"):
        mode = "happ"
    scope = str(base.get("scope") or "all").strip().lower()
    if scope not in ("all", "apps"):
        scope = "all"
    happ_path = str(base.get("happ_path") or "").strip() or str(BYPASS_HAPP_DEFAULT)
    try:
        socks_port = int(base.get("socks_port") or HAPP_SOCKS_DEFAULT)
    except (TypeError, ValueError):
        socks_port = HAPP_SOCKS_DEFAULT
    if not 0 < socks_port < 65536:
        socks_port = HAPP_SOCKS_DEFAULT
    return {
        "gbd_args": str(base.get("gbd_args") or "-5").strip() or "-5",
        "mode": mode,
        "scope": scope,
        "apps": normalize_apps(base.get("apps")),
        "socks_port": socks_port,
        "happ_path": happ_path,
    }


def bypass_cfg_save(**updates) -> None:
    config_update(bypass={**bypass_cfg(), **updates})


def bypass_admin() -> bool:
    try:
        shell32 = ctypes.windll.shell32
        shell32.IsUserAnAdmin.restype = ctypes.c_int
        shell32.IsUserAnAdmin.argtypes = []
        return bool(shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def _bypass_track() -> dict:
    try:
        data = json.loads(BYPASS_STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            return data
    except Exception:  # noqa: BLE001
        pass
    return {}


def _track_save(data: dict) -> None:
    BYPASS_DIR.mkdir(parents=True, exist_ok=True)
    BYPASS_STATE_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8",
    )


def _image_pids(image: str) -> list[int]:
    try:
        rc, out, _ = run_tool(
            ["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"], timeout=30,
        )
    except Exception:  # noqa: BLE001
        return []
    if rc != 0 or not out or "INFO:" in out.upper():
        return []
    pids: list[int] = []
    for line in out.splitlines():
        if '"' not in line:
            continue
        fields = line.strip().split('","')
        if len(fields) >= 2:
            try:
                pids.append(int(fields[1]))
            except ValueError:
                continue
    return pids


def _pid_alive(pid: int) -> bool:
    try:
        rc, out, _ = run_tool(
            ["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], timeout=30,
        )
    except Exception:  # noqa: BLE001
        return False
    return rc == 0 and bool(out) and "INFO:" not in out.upper()


def _user32():
    u32 = ctypes.windll.user32
    try:
        u32.GetWindowThreadProcessId.restype = ctypes.c_ulong
        u32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
        u32.IsWindowVisible.restype = ctypes.c_int
        u32.IsWindowVisible.argtypes = [ctypes.c_void_p]
        u32.IsIconic.restype = ctypes.c_int
        u32.IsIconic.argtypes = [ctypes.c_void_p]
        u32.ShowWindow.restype = ctypes.c_int
        u32.ShowWindow.argtypes = [ctypes.c_void_p, ctypes.c_int]
    except Exception:
        pass
    return u32


def _shell_execute(op: str, file: str, params: str = "", directory: str = "", show: int = 0) -> int:
    shell32 = ctypes.windll.shell32
    try:
        shell32.ShellExecuteW.restype = ctypes.c_void_p
        shell32.ShellExecuteW.argtypes = [
            ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
            ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_int,
        ]
    except Exception:
        pass
    code = shell32.ShellExecuteW(None, op, file, params or None, directory or None, show)
    return int(code or 0)


def _pid_windows(pids) -> list[int]:
    """Видимые top-level окна, принадлежащие перечисленным PID."""
    u32 = _user32()
    windows: list[int] = []
    wanted = {int(pid) for pid in pids if pid}
    if not wanted:
        return windows
    proto = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def on_window(hwnd, _lparam):
        try:
            owner = ctypes.c_ulong()
            u32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if owner.value in wanted and u32.IsWindowVisible(hwnd):
                windows.append(hwnd)
        except Exception:  # noqa: BLE001
            pass
        return True

    try:
        u32.EnumWindows(proto(on_window), None)
    except Exception:  # noqa: BLE001
        pass
    return windows


def _minimize_happ_windows(pids, attempts: int = 8, delay: float = 0.4) -> int:
    """Свёртывает окна запущенного Happ, чтобы окно не вылезало при включении VPN."""
    u32 = _user32()
    for _ in range(attempts):
        minimized = 0
        for hwnd in _pid_windows(pids):
            if not u32.IsIconic(hwnd):
                u32.ShowWindow(hwnd, 6)  # SW_MINIMIZE
                minimized += 1
        if minimized:
            return minimized
        time.sleep(delay)
    return 0


def _restore_happ_windows(pids) -> int:
    """Развёртывает свёрнутые окна Happ — доступ нужен для смены сервера."""
    u32 = _user32()
    restored = 0
    for hwnd in _pid_windows(pids):
        if u32.IsIconic(hwnd):
            u32.ShowWindow(hwnd, 9)  # SW_RESTORE
            restored += 1
    return restored


def _running_images() -> set[str]:
    """Имена всех запущенных процессов одним вызовом tasklist."""
    rc, out, _err = run_tool(["tasklist", "/FO", "CSV", "/NH"], timeout=30)
    names: set[str] = set()
    if rc != 0:
        return names
    for line in out.splitlines():
        if '"' in line:
            names.add(line.strip().split('","')[0].strip('"').lower())
    return names



def set_windows_system_proxy(enable: bool, server: str = "") -> bool:
    """Включает или принудительно выключает системный прокси Windows (WinINet).

    Когда enable=False, ProxyEnable выставляется в 0, а WinINet уведомляется
    через InternetSetOptionW, чтобы все программы и браузеры немедленно пошли напрямую.
    """
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
            0,
            winreg.KEY_SET_VALUE | winreg.KEY_QUERY_VALUE,
        ) as key:
            winreg.SetValueEx(key, "ProxyEnable", 0, winreg.REG_DWORD, 1 if enable else 0)
            if server:
                winreg.SetValueEx(key, "ProxyServer", 0, winreg.REG_SZ, str(server))
        wininet = getattr(ctypes.windll, "wininet", None)
        if wininet:
            try:
                wininet.InternetSetOptionW.restype = ctypes.c_int
                wininet.InternetSetOptionW.argtypes = [
                    ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_uint32,
                ]
            except Exception:
                pass
            wininet.InternetSetOptionW(None, 39, None, 0)  # INTERNET_OPTION_SETTINGS_CHANGED
            wininet.InternetSetOptionW(None, 37, None, 0)  # INTERNET_OPTION_REFRESH
        return True
    except Exception:  # noqa: BLE001
        return False


def happ_tun_active() -> bool:
    """Проверяет, перехватывает ли TUN-адаптер Happ (happ-xray / happ-tun) системный трафик."""
    if not happ_connected():
        return False
    try:
        rc, out, _ = run_tool(["route", "print", "0.0.0.0"], timeout=3)
        if rc == 0 and ("172.19." in out or "172.18." in out or "happ-xray" in out or "happ-tun" in out):
            return True
    except Exception:  # noqa: BLE001
        pass
    mode = happ_mode_read()
    return bool(mode.get("tun"))


def happ_tun_disable(wait: float = 4.0) -> bool:
    """Отключает TUN-адаптер Happ в режиме программ, исключая перехват трафика всего ПК.
    
    Ждёт, пока интерфейс реально перестанет быть активным; при неудаче —
    пробует полный сброс маршрута через этот интерфейс.
    """
    any_disabled = False
    for iface in ("happ-xray", "happ-tun"):
        try:
            rc, _, _ = run_tool(["netsh", "interface", "set", "interface", iface, "disable"],
                                timeout=5)
            if rc == 0:
                any_disabled = True
        except Exception:  # noqa: BLE001
            pass
    if not any_disabled:
        return False
    # Ждём, пока интерфейс перейдёт в неактивное состояние.
    deadline = time.time() + wait
    while time.time() < deadline:
        done = True
        for iface in ("happ-xray", "happ-tun"):
            try:
                rc, out, _ = run_tool(["netsh", "interface", "show", "interface", iface],
                                      timeout=3)
                if rc == 0 and ("connected" in out.lower() or "подключен" in out.lower()):
                    done = False
            except Exception:  # noqa: BLE001
                done = False
        if done:
            return True
        time.sleep(0.25)
    # Если интерфейс не отключился — удаляем его маршрут из таблицы.
    for iface in ("happ-xray", "happ-tun"):
        try:
            run_tool(["netsh", "interface", "delete", "route", "interface=" + iface,
                      "purge"], timeout=5)
        except Exception:  # noqa: BLE001
            pass
    return True


def happ_tun_enable() -> bool:
    """Включает TUN-адаптер Happ для режима «весь трафик»."""
    for iface in ("happ-xray", "happ-tun"):
        try:
            run_tool(["netsh", "interface", "set", "interface", iface, "enable"], timeout=5)
        except Exception:  # noqa: BLE001
            pass
    return True

def happ_system_proxy() -> tuple[bool, str]:
    """Системный прокси Windows: (включён, адрес). Happ ставит сюда 127.0.0.1."""
    try:
        import winreg
    except ImportError:
        return False, ""
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            enable = int(winreg.QueryValueEx(key, "ProxyEnable")[0])
            try:
                server = str(winreg.QueryValueEx(key, "ProxyServer")[0])
            except FileNotFoundError:
                server = ""
        return bool(enable), server
    except OSError:
        return False, ""


def happ_connected(images: set[str] | None = None) -> bool:
    """VPN Happ включён: работает core-процесс или системный прокси смотрит в localhost."""
    names = images if images is not None else _running_images()
    if any(name in names for name in HAPP_CORE_IMAGES):
        return True
    enable, server = happ_system_proxy()
    return bool(
        enable and ("127.0.0.1" in server or "localhost" in server or "::1" in server)
    )


class MIB_TCPROW(ctypes.Structure):
    _fields_ = [
        ("dwState", ctypes.c_ulong),
        ("dwLocalAddr", ctypes.c_ulong),
        ("dwLocalPort", ctypes.c_ulong),
        ("dwRemoteAddr", ctypes.c_ulong),
        ("dwRemotePort", ctypes.c_ulong),
    ]


def reset_tcp_connections(target_port: int) -> int:
    """Сбрасывает установленные TCP-соединения с локальным портом (например, 10808).

    Предотвращает сохранение открытых keepalive/QUIC сокетов браузера (YouTube)
    после выключения обхода.
    Корректно работает на 64-битной платформе и устойчив при отсутствии прав администратора.
    """
    if sys.platform != "win32":
        return 0
    try:
        iphlpapi = ctypes.windll.iphlpapi
        try:
            iphlpapi.GetTcpTable.restype = ctypes.c_ulong
            iphlpapi.GetTcpTable.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong), ctypes.c_int]
            iphlpapi.SetTcpEntry.restype = ctypes.c_ulong
            iphlpapi.SetTcpEntry.argtypes = [ctypes.POINTER(MIB_TCPROW)]
        except Exception:
            pass

        size = ctypes.c_ulong(0)
        iphlpapi.GetTcpTable(None, ctypes.byref(size), False)
        if size.value == 0:
            return 0

        # Буфер может вырасти между вызовами из-за новых сокетов: повторяем с запасом
        buf = None
        for _ in range(3):
            alloc_size = size.value + 800
            buf = (ctypes.c_char * alloc_size)()
            actual_size = ctypes.c_ulong(alloc_size)
            if iphlpapi.GetTcpTable(buf, ctypes.byref(actual_size), False) == 0:
                break
            size = actual_size
        else:
            return 0

        import struct
        num_entries = struct.unpack_from("=I", buf, 0)[0]
        row_size = ctypes.sizeof(MIB_TCPROW)
        offset = 4
        closed = 0
        MIB_TCP_STATE_DELETE_TCB = 12
        is_adm = is_admin()

        for _ in range(num_entries):
            if offset + row_size > len(buf):
                break
            row = MIB_TCPROW.from_buffer_copy(buf[offset : offset + row_size])
            local_p = socket.ntohs(row.dwLocalPort & 0xFFFF)
            remote_p = socket.ntohs(row.dwRemotePort & 0xFFFF)
            if local_p == target_port or remote_p == target_port:
                if is_adm:
                    row.dwState = MIB_TCP_STATE_DELETE_TCB
                    if iphlpapi.SetTcpEntry(ctypes.byref(row)) == 0:
                        closed += 1
            offset += row_size
        return closed
    except Exception:  # noqa: BLE001
        return 0


def happ_send(command: str) -> bool:
    """Отправить команду Happ через зарегистрированную схему happ:// (connect/disconnect/open)."""
    url = command if "://" in command else "happ://" + command
    try:
        code = _shell_execute("open", url)
    except Exception:  # noqa: BLE001
        return False
    return code > 32


def _tail(path: Path, limit: int = 400) -> str:
    try:
        return path.read_text(encoding="utf-8", errors="replace").strip()[-limit:]
    except Exception:  # noqa: BLE001
        return ""


def bypass_status(_timeout: float = 1.5) -> dict:
    """Полное состояние: движки, режим Happ, перенаправление по программам."""
    cfg = bypass_cfg()
    track = _bypass_track()
    result: dict = {"admin": bypass_admin()}
    images = _running_images()
    port = happ_socks_port()
    for key, image, exe in (
        ("happ", Path(cfg["happ_path"]).name, Path(cfg["happ_path"])),
        ("gbd", "goodbyedpi.exe", BYPASS_GBD_EXE),
    ):
        pids = _image_pids(image)
        entry = track.get(key) if isinstance(track.get(key), dict) else {}
        my_pid = int(entry.get("pid") or 0)
        if my_pid and my_pid not in pids:  # наш процесс уже умер
            track.pop(key, None)
            _track_save(track)
        result[key] = {
            "on": bool(pids),
            "mine": bool(my_pid and my_pid in pids),
            "installed": exe.exists(),
        }
        if key == "happ":
            result[key]["connected"] = happ_connected(images)
            result[key]["tun"] = any(
                name in images
                for name in ("sing-box.exe", "tun2proxy-bin.exe", "tun2proxy.exe")
            )
            result[key]["socks_port"] = port
            result[key]["socks_ok"] = port_open(port)
            result[key]["mode"] = happ_mode_read()
        if key == "gbd":
            result[key]["args"] = cfg["gbd_args"]
        if pids:
            result[key]["pids"] = pids
    result["track"] = {}
    rescan = proxifyre_rescan()
    # ProxiFyre запоминает процессы при старте службы. Если пользователь
    # запустил выбранную программу позже, перезапускаем службу автоматически,
    # чтобы она тоже попала под перенаправление.
    result["proxyfyre"] = proxifyre_status()
    result["proxifyre"] = result["proxyfyre"]
    result["proxyfyre_rescan"] = rescan
    result["scope"] = cfg["scope"]
    result["apps"] = cfg["apps"]
    result["mode"] = cfg["mode"]
    # Диагностика утечек: если обход программ включён (scope=apps), но что-то
    # из системы продолжает перехватывать трафик — это сигнал.
    result["leak"] = _bypass_leak_diagnosis(cfg["scope"])
    return result


def _bypass_leak_diagnosis(scope: str) -> dict:
    """Проверяет, не остаётся ли после выключения обхода перехват трафика."""
    happ_on = happ_connected()
    happ_app_running = bool(_image_pids(Path(bypass_cfg()["happ_path"]).name))
    cores_leak = bool(not happ_app_running and (_image_pids("xray.exe") or _image_pids("sing-box.exe")))
    tun_leak = bool(happ_tun_active() and (scope == "apps" or not happ_on))
    enable, server = happ_system_proxy()
    proxy_leak = bool(enable and ("127.0.0.1" in server or "localhost" in server or "::1" in server)
                      and (scope == "apps" or not happ_on))
    return {
        "tun": tun_leak,
        "proxy": proxy_leak,
        "cores": cores_leak,
    }


def bypass_gbd_start() -> dict:
    if not BYPASS_GBD_EXE.exists():
        raise RuntimeError("GoodbyeDPI не скачан — кнопка «Скачать GoodbyeDPI»")
    cfg = bypass_cfg()
    track = _bypass_track()
    entry = track.get("gbd") if isinstance(track.get("gbd"), dict) else {}
    my_pid = int(entry.get("pid") or 0)
    pids = _image_pids("goodbyedpi.exe")
    if my_pid and my_pid in pids:
        return {"ok": True, "detail": f"GoodbyeDPI уже запущен (PID {my_pid})"}
    if pids:
        track["gbd"] = {"pid": pids[0], "started": time.time()}
        _track_save(track)
        return {"ok": True, "detail": f"GoodbyeDPI уже запущен (PID {pids[0]})"}
    log_path = BYPASS_DIR / "goodbyedpi.log"
    try:
        log_path.unlink()  # старый лог не должен путать диагностику
    except FileNotFoundError:
        pass
    exe = str(BYPASS_GBD_EXE)
    args = cfg["gbd_args"]
    if bypass_admin():
        handle = open(log_path, "w", encoding="utf-8", errors="replace")
        subprocess.Popen(
            [exe, *args.split()],
            cwd=str(BYPASS_GBD_DIR),
            stdout=handle,
            stderr=subprocess.STDOUT,
            creationflags=NO_WINDOW,
        )
        handle.close()
    else:
        # GoodbyeDPI требует админ-прав: поднимаем cmd через UAC, вывод — в лог.
        # Важно: cmd /s /c ""exe" args > "log" 2>&1" — без внешних кавычек cmd
        # не выполняет перенаправление и лог не создаётся.
        inner = f'""{exe}" {args} > "{log_path}" 2>&1"'
        code = _shell_execute("runas", "cmd.exe", f"/s /c {inner}", str(BYPASS_GBD_DIR), 0)
        if code <= 32:
            raise RuntimeError(
                "GoodbyeDPI не запущен: окно UAC закрыто или отклонено.\n"
                "Нажмите «Включить» ещё раз и подтвердите запрос прав администратора."
            )
    for _ in range(30):  # до ~15 сек: UAC + загрузка драйвера WinDivert
        time.sleep(0.5)
        alive = _image_pids("goodbyedpi.exe")
        if alive:
            track["gbd"] = {"pid": alive[0], "started": time.time()}
            _track_save(track)
            detail = f"GoodbyeDPI запущен (PID {alive[0]})"
            if not bypass_admin():
                detail += " · чтобы убрать окна UAC, нажмите «Перезапустить как админ»"
            return {"ok": True, "detail": detail}
    tail = _tail(log_path)
    if tail:
        raise RuntimeError("goodbyedpi.exe не запускается:\n" + tail)
    raise RuntimeError(
        "goodbyedpi.exe не запустился, лог пуст — скорее всего окно UAC было "
        "закрыто без подтверждения.\nНажмите «Включить» ещё раз и выберите «Да». "
        "Либо перезапустите тулзу кнопкой «Перезапустить как админ»."
    )


def bypass_stop(key: str) -> dict:
    if key != "gbd":
        # Happ не закрываем — отключаем VPN и снимаем перенаправление программ,
        # иначе выбранные приложения останутся с мёртвым SOCKS и без интернета.
        # Порядок важен: сначала выключаем VPN в Happ, ждём, пока он реально
        # отключится (TUN, прокси и ядра), и только потом гасим остальное.
        notes: list[str] = []
        try:
            notes.append(proxifyre_stop().get("detail") or "")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"ProxiFyre: {exc}")
        try:
            detail = bypass_happ_stop().get("detail") or ""
        except Exception as exc:  # noqa: BLE001
            # VPN не отключился штатно — гасим принудительно.
            detail = f"Happ VPN отключён вручную: {exc}"
        notes.append(detail)
        # После отключения Happ прокси и TUN могут остаться активными —
        # выключаем их принудительно, чтобы трафик не утекал через прокси.
        set_windows_system_proxy(False)
        happ_tun_disable()
        # Гасим фоновый процесс Happ (happd.exe) — он сам переподключает VPN,
        # если в реестре остались tun/systemProxy включёнными.
        try:
            run_tool(["taskkill", "/F", "/IM", "happd.exe"], timeout=5)
        except Exception:
            pass
        # Гарантированное завершение фонового ядра и очистка DNS кэша
        for core_proc in ("xray.exe", "sing-box.exe"):
            try:
                run_tool(["taskkill", "/F", "/IM", core_proc], timeout=5)
            except Exception:
                pass
        try:
            reset_tcp_connections(happ_socks_port())
        except Exception:
            pass
        try:
            run_tool(["ipconfig", "/flushdns"], timeout=5)
        except Exception:
            pass
        return {"ok": True, "detail": "\n".join(filter(None, notes))}
    image, label, extra_images = "goodbyedpi.exe", "GoodbyeDPI", []
    track = _bypass_track()
    entry = track.get(key) if isinstance(track.get(key), dict) else {}
    my_pid = int(entry.get("pid") or 0)
    pids = _image_pids(image)
    extras_alive = any(_image_pids(name) for name in extra_images)
    if not pids and not extras_alive and not (my_pid and _pid_alive(my_pid)):
        if my_pid:
            track.pop(key, None)
            _track_save(track)
        return {"ok": True, "detail": f"{label} и так не был запущен"}
    targets = [image, *extra_images]
    # 1) вежливо: приложение само завершится и сохранит настройки
    for name in targets:
        run_tool(["taskkill", "/IM", name], timeout=30)
    for _ in range(10):
        time.sleep(0.4)
        if not any(_image_pids(name) for name in targets):
            break
    # 2) жёстко, если закрыться само не захотело
    if any(_image_pids(name) for name in targets):
        for name in targets:
            run_tool(["taskkill", "/F", "/IM", name], timeout=30)
        for _ in range(5):
            time.sleep(0.2)
            if not any(_image_pids(name) for name in targets):
                break
    if any(_image_pids(name) for name in targets):
        # скорее всего процесс от администратора — taskkill ещё раз через UAC
        arguments = " ".join(f"/IM {name}" for name in targets)
        code = _shell_execute("runas", "taskkill.exe", f"/F {arguments}", "", 0)
        if code <= 32:
            raise RuntimeError(
                f"{label} запущен от администратора — появится окно UAC, "
                "подтвердите его, чтобы остановить."
            )
        for _ in range(20):
            time.sleep(0.5)
            if not any(_image_pids(name) for name in targets):
                break
        else:
            raise RuntimeError(
                f"{label} всё ещё запущен (UAC отклонён?). Закройте его вручную."
            )
    track.pop(key, None)
    _track_save(track)
    return {"ok": True, "detail": f"{label} остановлен"}


def bypass_happ_start(visible: bool = False) -> dict:
    """Включает VPN в Happ командой happ://connect — без открытия/закрытия окна."""
    cfg = bypass_cfg()
    exe = Path(cfg["happ_path"])
    if not exe.exists():
        raise RuntimeError(
            f"Happ не найден: {exe}\nУкажите путь в «Путь Happ…»."
        )
    if happ_connected():
        return {"ok": True, "detail": "VPN уже включён"}
    pids = _image_pids(exe.name)
    if not pids:
        # клиент не запущен: поднимаем его, чтобы было кому выполнить команду
        if visible:
            subprocess.Popen([str(exe)], cwd=str(exe.parent))
        else:
            info = subprocess.STARTUPINFO()
            info.dwFlags = subprocess.STARTF_USESHOWWINDOW
            info.wShowWindow = 7  # SW_SHOWMINNOACTIVE
            subprocess.Popen([str(exe)], cwd=str(exe.parent), startupinfo=info)
        for _ in range(24):
            time.sleep(0.25)
            pids = _image_pids(exe.name)
            if pids:
                break
        if not pids:
            raise RuntimeError("Happ не запустился — проверьте путь в «Путь Happ…»")
        track = _bypass_track()
        track["happ"] = {"pid": pids[0], "started": time.time()}
        _track_save(track)
        if not visible:
            _minimize_happ_windows(pids)
    if not happ_send("connect"):
        raise RuntimeError("Happ не принял команду подключения (happ://connect)")
    for _ in range(30):  # до ~15 сек: выбор сервера по пингу и рукопожатие
        time.sleep(0.5)
        if happ_connected():
            return {"ok": True, "detail": "VPN включён"}
    raise RuntimeError(
        "Happ не подключился за 15 секунд — нажмите «Показать Happ» "
        "и проверьте подписку/сервер в самом Happ"
    )


def bypass_happ_stop() -> dict:
    """Выключает VPN в Happ командой happ://disconnect — программа остаётся открытой.

    Если штатное отключение не сработало, гасит ядра xray/sing-box и TUN
    принудительно — чтобы трафик точно не утекал через прокси Happ.
    """
    exe = Path(bypass_cfg()["happ_path"])
    pids = _image_pids(exe.name)
    if not pids:
        return {"ok": True, "detail": "Happ не запущен — VPN и так выключен"}
    if not happ_connected():
        return {"ok": True, "detail": "VPN уже выключен"}
    if not happ_send("disconnect"):
        raise RuntimeError("Happ не принял команду отключения (happ://disconnect)")
    deadline = time.time() + 25  # больше: даём Happ время сбросить TUN/прокси
    while time.time() < deadline:
        # Ждём, пока Happ сам выключит VPN, TUN и системный прокси
        if not happ_connected() and not happ_tun_active():
            enable, server = happ_system_proxy()
            is_local_proxy = enable and any(h in server for h in ("127.0.0.1", "localhost", "::1"))
            if not is_local_proxy:
                # Ключевое: в реестре Happ tun=true / systemProxy=true — он
                # переподключится через некоторое время. Гасим эти настройки,
                # чтобы трафик точно не утекал.
                happ_mode_write(tun=False, system_proxy=False)
                set_windows_system_proxy(False)
                return {"ok": True, "detail": "VPN, TUN и системный прокси выключены"}
        time.sleep(0.4)
    # Штатное отключение не сработало — гасим всё принудительно.
    detail = (
        "Happ не отключил VPN штатно — ядра и TUN выключены вручную, "
        "трафик должен идти напрямую. Откройте Happ и убедитесь, что VPN выключен."
    )
    for core_proc in ("xray.exe", "sing-box.exe", "happd.exe"):
        try:
            run_tool(["taskkill", "/F", "/IM", core_proc], timeout=5)
        except Exception:
            pass
    happ_tun_disable()
    set_windows_system_proxy(False)
    # Дополнительно гасим настройки Happ в реестре — иначе он сам
    # переподключится при следующей попытке или перезапуске.
    try:
        happ_mode_write(tun=False, system_proxy=False)
    except Exception:
        pass
    return {"ok": True, "detail": detail}


def bypass_happ_show() -> dict:
    """Показать окно Happ (VPN не трогаем)."""
    exe = Path(bypass_cfg()["happ_path"])
    if not exe.exists():
        raise RuntimeError(
            f"Happ не найден: {exe}\nУкажите путь в «Путь Happ…»."
        )
    pids = _image_pids(exe.name)
    if pids:
        accepted = happ_send("open")
        for _ in range(10):
            time.sleep(0.2)
            if sum(_restore_happ_windows([pid]) for pid in pids):
                return {"ok": True, "detail": "Окно Happ показано"}
        return {"ok": True, "detail": (
            "Команда показа окна отправлена" if accepted
            else "Окно не найдено — возможно, свёрнуто в трей"
        )}
    subprocess.Popen([str(exe)], cwd=str(exe.parent))
    for _ in range(15):
        time.sleep(0.2)
        if _image_pids(exe.name):
            return {"ok": True, "detail": "Happ запущен"}
    raise RuntimeError("Happ не запустился — проверьте путь")


def bypass_download(kind: str) -> dict:
    """Скачивает и распаковывает GoodbyeDPI в %LOCALAPPDATA%\\Tools\\bypass."""
    if kind != "gbd":
        raise RuntimeError(f"неизвестный компонент {kind!r}")
    dl_dir = BYPASS_DIR / "downloads"
    dl_dir.mkdir(parents=True, exist_ok=True)
    BYPASS_GBD_DIR.mkdir(parents=True, exist_ok=True)
    zip_path = dl_dir / "gbd.zip"

    script = "\n".join([
        "$ProgressPreference = 'SilentlyContinue'",
        "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12",
        f"$api = 'https://api.github.com/repos/{BYPASS_GBD_REPO}/releases/latest'",
        "$rel = Invoke-RestMethod $api",
        f"$asset = $rel.assets | Where-Object {{ $_.name -match '{BYPASS_GBD_ASSET}' }} "
        "| Select-Object -First 1",
        "if (-not $asset) { throw 'подходящий архив не найден в релизе' }",
        f"Invoke-WebRequest $asset.browser_download_url -OutFile '{zip_path}' -UseBasicParsing",
        "Write-Output $asset.name",
    ])
    name = ps_run(script, timeout=900)
    if not zip_path.exists() or zip_path.stat().st_size == 0:
        raise RuntimeError("архив не скачался")

    tmp = dl_dir / "gbd-unzip"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(tmp)
    found = list(tmp.rglob("goodbyedpi.exe"))
    if not found:
        raise RuntimeError("в архиве нет goodbyedpi.exe")
    exe_dir = found[0].parent
    for item in exe_dir.iterdir():
        if item.is_dir():
            shutil.copytree(item, BYPASS_GBD_DIR / item.name, dirs_exist_ok=True)
        else:
            shutil.copy2(item, BYPASS_GBD_DIR / item.name)
    shutil.rmtree(tmp, ignore_errors=True)
    if not BYPASS_GBD_EXE.exists():
        raise RuntimeError("файлы не распакованы")
    return {"ok": True, "detail": f"Скачан {name.strip() or 'GoodbyeDPI'}"}


def happ_direct_ru_ensure() -> bool:
    """Добавляет в config.json Happ правила direct для российских сайтов.

    Без них при ``route.final: proxy`` Яндекс/Госуслуги/банки уходят на
    зарубежный сервер и не открываются. Возвращает True, если файл меняли —
    тогда ядро Happ надо перезапустить, чтобы правила применились.
    """
    try:
        data = json.loads(HAPP_CONFIG_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    route = data.get("route")
    if not isinstance(route, dict):
        return False
    rules = route.get("rules")
    if not isinstance(rules, list):
        return False

    wanted = [
        {"domain_suffix": list(HAPP_DIRECT_DOMAINS), "outbound": "direct"},
        {"ip_cidr": list(HAPP_DIRECT_CIDR), "outbound": "direct"},
    ]
    # Сравниваем по набору доменов/CIDR, а не по всему объекту: Happ может
    # дополнить правила своими полями, и тогда мы не должны дублировать.
    def present(rule: dict) -> bool:
        key = "domain_suffix" if "domain_suffix" in rule else "ip_cidr"
        needed = set(rule[key])
        for item in rules:
            if not isinstance(item, dict) or item.get("outbound") != "direct":
                continue
            if needed.issubset(set(item.get(key) or [])):
                return True
        return False

    missing = [rule for rule in wanted if not present(rule)]
    if not missing:
        return False

    try:
        backup = HAPP_CONFIG_FILE.with_suffix(".json.tools.bak")
        if not backup.exists():
            backup.write_bytes(HAPP_CONFIG_FILE.read_bytes())
    except OSError:
        pass
    # Ставим после правил process_name, но до sniff/hijack-dns: sniff должен
    # видеть уже размеченный трафик, а hijack-dns не должен перехватить direct.
    insert_at = 0
    for index, item in enumerate(rules):
        if isinstance(item, dict) and item.get("process_name"):
            insert_at = index + 1
            break
    for offset, rule in enumerate(missing):
        rules.insert(insert_at + offset, rule)
    try:
        HAPP_CONFIG_FILE.write_text(
            json.dumps(data, ensure_ascii=False, indent=2), "utf-8"
        )
    except OSError:
        return False
    return True


def happ_config_tun_ensure(enable_auto_route: bool) -> bool:
    """Управляет параметром auto_route в inbounds sing-box config.json.

    При enable_auto_route=False sing-box не перехватывает маршруты 0.0.0.0/0
    всей Windows, позволяя невыбранным программам выходить напрямую в интернет.
    """
    try:
        if not HAPP_CONFIG_FILE.exists():
            return False
        data = json.loads(HAPP_CONFIG_FILE.read_text("utf-8"))
    except (OSError, ValueError):
        return False
    if not isinstance(data, dict):
        return False
    inbounds = data.get("inbounds")
    if not isinstance(inbounds, list):
        return False
    changed = False
    for item in inbounds:
        if isinstance(item, dict) and item.get("type") == "tun":
            if item.get("auto_route") != bool(enable_auto_route):
                item["auto_route"] = bool(enable_auto_route)
                changed = True
    if changed:
        try:
            HAPP_CONFIG_FILE.write_text(
                json.dumps(data, ensure_ascii=False, indent=2), "utf-8"
            )
            return True
        except OSError:
            return False
    return False


def happ_socks_port_from(data) -> int:
    """Порт локального SOCKS Happ из его конфига (sing-box)."""
    if not isinstance(data, dict):
        return HAPP_SOCKS_DEFAULT
    for inbound in data.get("inbounds") or []:
        if not isinstance(inbound, dict):
            continue
        if str(inbound.get("type") or "").lower() not in ("socks", "socks5", "mixed"):
            continue
        server = str(inbound.get("listen") or inbound.get("server") or "").strip()
        if server and server not in ("127.0.0.1", "localhost", "::1", "0.0.0.0", "::"):
            continue
        try:
            port = int(inbound.get("listen_port") or inbound.get("port") or 0)
        except (TypeError, ValueError):
            continue
        if 0 < port < 65536:
            return port
    for outbound in data.get("outbounds") or []:
        if not isinstance(outbound, dict):
            continue
        if str(outbound.get("type") or "").lower() not in ("socks", "socks5"):
            continue
        server = str(outbound.get("server") or "").strip()
        if server not in ("127.0.0.1", "localhost", "::1"):
            continue
        try:
            port = int(outbound.get("server_port") or 0)
        except (TypeError, ValueError):
            continue
        if 0 < port < 65536:
            return port
    return HAPP_SOCKS_DEFAULT


def happ_socks_port(config_file: Path | None = None) -> int:
    path = Path(config_file) if config_file else HAPP_CONFIG_FILE
    try:
        data = json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return HAPP_SOCKS_DEFAULT
    return happ_socks_port_from(data)


def port_open(port: int, host: str = "127.0.0.1", timeout: float = 0.4) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(timeout)
            return sock.connect_ex((host, int(port))) == 0
    except Exception:  # noqa: BLE001
        return False


def _reg_get_int(key_path: str, name: str, default: int) -> int:
    try:
        import winreg
    except ImportError:
        return default
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _kind = winreg.QueryValueEx(key, name)
        return int(value)
    except (OSError, ValueError, TypeError):
        return default


def _reg_set_int(key_path: str, name: str, value: int) -> bool:
    try:
        import winreg
    except ImportError:
        return False
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0,
                                winreg.KEY_SET_VALUE) as key:
            winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, int(value))
        return True
    except OSError:
        return False


def _reg_bool(key_path: str, name: str, default: bool = True) -> bool | None:
    """Читает булево Happ: REG_SZ 'true'/'false' или DWORD 0/1.

    None — ключа нет (тогда вызывающий решает, что считать по умолчанию).
    Happ хранит настройки строками, поэтому обычный int() тут не годится.
    """
    try:
        import winreg
    except ImportError:
        return default
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            value, _kind = winreg.QueryValueEx(key, name)
    except OSError:
        return None
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1", "yes", "on")
    try:
        return bool(int(value))
    except (TypeError, ValueError):
        return default


def _reg_set_bool(key_path: str, name: str, value: bool) -> bool:
    """Пишет булево в том типе, в котором ключ уже существует.

    Happ использует REG_SZ ('true'/'false'); если ключ создан нами как DWORD,
    сохраняем DWORD, чтобы не менять тип под ногами у приложения.
    """
    try:
        import winreg
    except ImportError:
        return False
    kind = winreg.REG_SZ
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key_path) as key:
            _old, kind = winreg.QueryValueEx(key, name)
    except OSError:
        pass
    try:
        with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, key_path, 0,
                                winreg.KEY_SET_VALUE) as key:
            if kind == winreg.REG_SZ:
                winreg.SetValueEx(key, name, 0, winreg.REG_SZ,
                                  "true" if value else "false")
            else:
                winreg.SetValueEx(key, name, 0, winreg.REG_DWORD, 1 if value else 0)
        return True
    except OSError:
        return False


def happ_mode_read() -> dict:
    """Как Happ сейчас пускает трафик: TUN-адаптер и системный прокси.

    Реальный ключ Happ — ``systemProxy`` (REG_SZ). Раньше код читал и писал
    ``system_proxy`` (DWORD), который Happ игнорирует: из-за этого proxies
    оставался включённым и YouTube открывался при выключенном обходе.
    """
    tun = _reg_bool(HAPP_PREFS_KEY, "tun")
    proxy = _reg_bool(HAPP_PREFS_KEY, "systemProxy")
    if proxy is None:  # старые сборки / наш legacy-ключ
        proxy = _reg_bool(HAPP_PREFS_KEY, "system_proxy")
    return {
        "tun": True if tun is None else tun,
        "system_proxy": True if proxy is None else proxy,
    }


def happ_mode_write(tun: bool | None = None, system_proxy: bool | None = None) -> dict:
    if tun is not None and not _reg_set_bool(HAPP_PREFS_KEY, "tun", bool(tun)):
        raise RuntimeError(
            "Не удалось изменить настройку Happ (tun) в реестре HKCU\\Software\\Happ"
        )
    if system_proxy is not None:
        if not _reg_set_bool(HAPP_PREFS_KEY, "systemProxy", bool(system_proxy)):
            raise RuntimeError(
                "Не удалось изменить настройку Happ (systemProxy) в реестре HKCU\\Software\\Happ"
            )
        # Дублируем в legacy-ключ: его читают старые сборки тулзы.
        _reg_set_int(HAPP_PREFS_KEY, "system_proxy", 1 if system_proxy else 0)
    return happ_mode_read()


def happ_tunnel_restart(timeout: float = 25.0) -> dict:
    """Перезапуск туннеля Happ, чтобы он применил новые настройки."""
    exe = Path(bypass_cfg()["happ_path"])
    if not _image_pids(exe.name) or not happ_connected():
        return bypass_happ_start()
    happ_send("disconnect")
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not happ_connected():
            break
        time.sleep(0.4)
    return bypass_happ_start()


class SERVICE_STATUS(ctypes.Structure):
    _fields_ = [
        ("dwServiceType", ctypes.c_ulong),
        ("dwCurrentState", ctypes.c_ulong),
        ("dwControlsAccepted", ctypes.c_ulong),
        ("dwWin32ExitCode", ctypes.c_ulong),
        ("dwServiceSpecificExitCode", ctypes.c_ulong),
        ("dwCheckPoint", ctypes.c_ulong),
        ("dwWaitHint", ctypes.c_ulong),
    ]


def _advapi():
    global _ADVAPI_READY
    api = ctypes.windll.advapi32
    if not _ADVAPI_READY:
        api.OpenSCManagerW.restype = ctypes.c_void_p
        api.OpenSCManagerW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        api.OpenServiceW.restype = ctypes.c_void_p
        api.OpenServiceW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32]
        api.QueryServiceStatus.restype = ctypes.c_int
        api.QueryServiceStatus.argtypes = [ctypes.c_void_p, ctypes.POINTER(SERVICE_STATUS)]
        api.StartServiceW.restype = ctypes.c_int
        api.StartServiceW.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                      ctypes.POINTER(ctypes.c_wchar_p)]
        api.ControlService.restype = ctypes.c_int
        api.ControlService.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                       ctypes.POINTER(SERVICE_STATUS)]
        api.CloseServiceHandle.restype = ctypes.c_int
        api.CloseServiceHandle.argtypes = [ctypes.c_void_p]
        _ADVAPI_READY = True
    return api


def _svc_state(name: str) -> int | None:
    """Состояние службы/драйвера: 4 — работает, 1 — остановлена, None — нет такой."""
    if sys.platform != "win32":
        return None
    api = None
    scm = None
    handle = None
    try:
        api = _advapi()
        scm = api.OpenSCManagerW(None, None, 0x0001)  # SC_MANAGER_CONNECT
        if not scm:
            return None
        handle = api.OpenServiceW(scm, name, 0x0004)  # SERVICE_QUERY_STATUS
        if not handle:
            return None
        status = SERVICE_STATUS()
        if not api.QueryServiceStatus(handle, ctypes.byref(status)):
            return None
        return int(status.dwCurrentState)
    except Exception:  # noqa: BLE001
        return None
    finally:
        if api:
            if handle:
                try:
                    api.CloseServiceHandle(handle)
                except Exception:
                    pass
            if scm:
                try:
                    api.CloseServiceHandle(scm)
                except Exception:
                    pass


def _svc_start(name: str, wait: float = 12.0) -> bool:
    if sys.platform != "win32":
        return False
    if _svc_state(name) == SVC_RUNNING:
        return True
    api = None
    scm = None
    handle = None
    try:
        api = _advapi()
        scm = api.OpenSCManagerW(None, None, 0x0001)
        if scm:
            handle = api.OpenServiceW(scm, name, 0x0010 | 0x0004)
            if handle:
                api.StartServiceW(handle, 0, None)
    except Exception:  # noqa: BLE001
        pass
    finally:
        if api:
            if handle:
                try:
                    api.CloseServiceHandle(handle)
                except Exception:
                    pass
            if scm:
                try:
                    api.CloseServiceHandle(scm)
                except Exception:
                    pass

    if _svc_state(name) == SVC_RUNNING:
        return True

    # Если запуск через Win32 API не прошёл, пробуем sc start / net start
    try:
        run_tool(["sc.exe", "start", name], timeout=10)
    except Exception:  # noqa: BLE001
        pass

    deadline = time.time() + wait
    while time.time() < deadline:
        if _svc_state(name) == SVC_RUNNING:
            return True
        time.sleep(0.3)
    return _svc_state(name) == SVC_RUNNING


def _svc_stop(name: str, wait: float = 12.0) -> bool:
    if sys.platform != "win32":
        return False
    if _svc_state(name) == SVC_STOPPED:
        return True
    api = None
    scm = None
    handle = None
    try:
        api = _advapi()
        scm = api.OpenSCManagerW(None, None, 0x0001)
        if scm:
            handle = api.OpenServiceW(scm, name, 0x0020 | 0x0004)
            if handle:
                status = SERVICE_STATUS()
                api.ControlService(handle, 1, ctypes.byref(status))  # SERVICE_CONTROL_STOP
    except Exception:  # noqa: BLE001
        pass
    finally:
        if api:
            if handle:
                try:
                    api.CloseServiceHandle(handle)
                except Exception:
                    pass
            if scm:
                try:
                    api.CloseServiceHandle(scm)
                except Exception:
                    pass

    if _svc_state(name) == SVC_STOPPED:
        return True

    try:
        run_tool(["sc.exe", "stop", name], timeout=10)
    except Exception:  # noqa: BLE001
        pass

    deadline = time.time() + wait
    while time.time() < deadline:
        if _svc_state(name) == SVC_STOPPED:
            return True
        time.sleep(0.3)
    return _svc_state(name) == SVC_STOPPED


def proxifyre_service_name() -> str:
    """Реальное имя службы ProxiFyre.

    Разные сборки регистрируют её как ProxiFyreService или как ProxiFyre:
    с неверным именем состояние всегда «не установлена», и обход по
    программам молча не включается.
    """
    global _PROXIFYRE_SERVICE_NAME
    if _PROXIFYRE_SERVICE_NAME:
        return _PROXIFYRE_SERVICE_NAME
    for name in PROXIFYRE_SERVICE_ALIASES:
        if _svc_state(name) is not None:
            _PROXIFYRE_SERVICE_NAME = name
            return name
    return PROXIFYRE_SERVICE


def proxifyre_config_data(apps, port: int) -> dict:
    # ProxiFyre сопоставляет имена процессов; вариант без «.exe» понимают все версии.
    expanded = expand_companions(normalize_apps(apps))
    names = [Path(name).stem for name in expanded]
    return {
        "logLevel": "INFO",
        "proxies": [{
            "appNames": names,
            "socks5ProxyEndpoint": f"127.0.0.1:{int(port)}",
            "supportedProtocols": ["TCP", "UDP"],
        }],
    }


def proxifyre_write_config(apps, port: int) -> bool:
    """Пишет app-config.json. True — файл изменился (нужен перезапуск службы)."""
    data = proxifyre_config_data(apps, port)
    try:
        current = json.loads(PROXIFYRE_CONFIG.read_text("utf-8"))
    except (OSError, ValueError):
        current = None
    if current == data:
        return False
    PROXIFYRE_DIR.mkdir(parents=True, exist_ok=True)
    PROXIFYRE_CONFIG.write_text(json.dumps(data, ensure_ascii=False, indent=4), "utf-8")
    return True


def proxifyre_driver_name() -> str:
    """Определяет зарегистрированное имя службы драйвера WinpkFilter/ndisrd."""
    for name in PACKET_FILTER_DRIVER_ALIASES:
        if _svc_state(name) is not None:
            return name
    return PACKET_FILTER_DRIVER


def proxifyre_driver_ok() -> bool:
    """Проверяет состояние драйвера пакетов (ndisrd/WinpkFilter)."""
    driver = proxifyre_driver_name()
    return _svc_state(driver) == SVC_RUNNING


# ProxiFyre 1.0.22 запоминает PID процессов в момент старта службы: программа,
# запущенная позже, в SOCKS не попадает. Держим снимок PID и перезапускаем
# службу, когда выбранная программа запустилась заново.
_PROXIFYRE_PIDS: set[int] | None = None
_PROXIFYRE_LAST_RESTART = 0.0
RESCAN_COOLDOWN = 15.0


def _selected_pids() -> set[int]:
    expanded = expand_companions(bypass_cfg()["apps"])
    wanted = {Path(name).stem.lower() for name in expanded}
    if not wanted:
        return set()
    result: set[int] = set()
    for pid, path in running_processes():
        if Path(path).stem.lower() in wanted:
            result.add(int(pid))
    return result


def proxifyre_snapshot() -> set[int]:
    """Запомнить, какие процессы служба уже видит (после её старта)."""
    global _PROXIFYRE_PIDS
    _PROXIFYRE_PIDS = _selected_pids()
    return _PROXIFYRE_PIDS


def proxifyre_rescan() -> dict | None:
    """Перезапускает службу, если появился новый процесс из списка обхода.

    Возвращает None, когда делать нечего, иначе {ok, apps} — что перезапускали.
    """
    if _svc_state(proxifyre_service_name()) != SVC_RUNNING:
        return None
    wanted = {Path(name).stem.lower() for name in expand_companions(bypass_cfg()["apps"])}
    if not wanted:
        return None
    current = _selected_pids()
    global _PROXIFYRE_PIDS
    if _PROXIFYRE_PIDS is None:
        _PROXIFYRE_PIDS = current
        return None
    fresh = current - _PROXIFYRE_PIDS
    if not fresh:
        _PROXIFYRE_PIDS = current & _PROXIFYRE_PIDS if current else current
        return None
    # Программы плодят процессы (Electron: главный + рендереры), поэтому
    # перезапускаем не чаще, чем раз в RESCAN_COOLDOWN секунд.
    global _PROXIFYRE_LAST_RESTART
    if time.time() - _PROXIFYRE_LAST_RESTART < RESCAN_COOLDOWN:
        return None
    _PROXIFYRE_LAST_RESTART = time.time()
    service = proxifyre_service_name()
    _svc_stop(service)
    ok = _svc_start(service)
    if ok:
        proxifyre_snapshot()
    return {"ok": ok, "apps": sorted(fresh)}


def normalize_app_names(values) -> list[str]:
    """Имена из конфига ProxiFyre («browser» или «browser.exe») → список exe."""
    result: list[str] = []
    for value in values or []:
        text = str(value or "").strip()
        if not text:
            continue
        result.append(text if text.lower().endswith(".exe") else text + ".exe")
    return normalize_apps(result)


def proxifyre_status() -> dict:
    state = _svc_state(proxifyre_service_name())
    apps: list[str] = []
    try:
        data = json.loads(PROXIFYRE_CONFIG.read_text("utf-8"))
        for proxy in data.get("proxies") or []:
            if isinstance(proxy, dict):
                apps.extend(normalize_app_names(proxy.get("appNames")))
    except (OSError, ValueError, AttributeError):
        pass
    configured = normalize_apps(apps)
    wanted = expand_companions(normalize_apps(bypass_cfg()["apps"]))
    configured_keys = {Path(name).stem.lower() for name in configured}
    wanted_keys = {Path(name).stem.lower() for name in wanted}
    return {
        "installed": PROXIFYRE_EXE.exists(),
        "driver": proxifyre_driver_ok(),
        "service": state,
        "running": state == SVC_RUNNING,
        "apps": configured,
        "pending": wanted_keys != configured_keys,
    }


def _proxifyre_install() -> bool:
    if not PROXIFYRE_EXE.exists():
        return False
    if is_admin():
        run_tool([str(PROXIFYRE_EXE), "install"], cwd=str(PROXIFYRE_DIR), timeout=120)
    else:
        try:
            code = _shell_execute("runas", str(PROXIFYRE_EXE), "install", str(PROXIFYRE_DIR), 0)
        except Exception:  # noqa: BLE001
            return False
        if code <= 32:
            return False
    for _ in range(40):
        time.sleep(0.5)
        if _svc_state(proxifyre_service_name()) is not None:
            return True
    return False


def proxifyre_start(apps=None, port: int | None = None) -> dict:
    """Включает перенаправление выбранных программ в SOCKS Happ."""
    cfg = bypass_cfg()
    names = normalize_apps(cfg["apps"] if apps is None else apps)
    if not names:
        raise RuntimeError("Не выбрано ни одной программы — отметьте их в списке")
    port = int(port or cfg.get("socks_port") or happ_socks_port())
    if not PROXIFYRE_EXE.exists():
        raise RuntimeError(f"ProxiFyre не найден: {PROXIFYRE_EXE}")
    if not proxifyre_driver_ok():
        # Если драйвер установлен, но остановлен — пробуем запустить
        driver = proxifyre_driver_name()
        if _svc_state(driver) == SVC_STOPPED:
            _svc_start(driver, wait=4.0)
    if not proxifyre_driver_ok():
        raise RuntimeError(
            "Драйвер Windows Packet Filter (ndisrd) не запущен — "
            "обход по программам без него невозможен"
        )
    changed = proxifyre_write_config(names, port)
    service = proxifyre_service_name()
    state = _svc_state(service)
    if state is None:
        if not _proxifyre_install():
            raise RuntimeError(
                "Служба ProxiFyre не установлена: подтвердите запрос прав администратора"
            )
        service = proxifyre_service_name()
        state = _svc_state(service)
    if state == SVC_RUNNING and not changed:
        proxifyre_snapshot()
        return {"ok": True, "detail": f"Обход уже включён для {len(names)} программ"}
    if state == SVC_RUNNING:
        _svc_stop(service)
    if not _svc_start(service):
        raise RuntimeError(
            "Не удалось запустить службу ProxiFyre"
        )
    proxifyre_snapshot()
    return {
        "ok": True,
        "detail": f"Обход включён для {len(names)} программ через SOCKS 127.0.0.1:{port}",
    }


def proxifyre_restart() -> dict:
    """Перезапуск службы: ProxiFyre 1.0.22 иначе не видит новые процессы."""
    service = proxifyre_service_name()
    if not PROXIFYRE_EXE.exists():
        raise RuntimeError(f"ProxiFyre не найден: {PROXIFYRE_EXE}")
    state = _svc_state(service)
    if state is None:
        raise RuntimeError(
            "Служба ProxiFyre не установлена: подтвердите запрос прав администратора"
        )
    if not proxifyre_driver_ok():
        raise RuntimeError(
            "Драйвер Windows Packet Filter (ndisrd) не запущен — "
            "обход по программам без него невозможен"
        )
    cfg = bypass_cfg()
    proxifyre_write_config(cfg["apps"], happ_socks_port())
    if state == SVC_RUNNING:
        _svc_stop(service)
    if not _svc_start(service):
        raise RuntimeError(
            "Не удалось запустить службу ProxiFyre"
        )
    proxifyre_snapshot()
    global _PROXIFYRE_LAST_RESTART
    _PROXIFYRE_LAST_RESTART = time.time()
    apps = normalize_apps(cfg["apps"])
    return {"ok": True, "detail": f"ProxiFyre перезапущен — подхвачено {len(apps)} программ"}


def proxifyre_stop() -> dict:
    service = proxifyre_service_name()
    state = _svc_state(service)
    if state is None:
        return {"ok": True, "detail": ""}
    if state != SVC_RUNNING:
        return {"ok": True, "detail": "Обход по программам уже выключен"}
    if not _svc_stop(service):
        raise RuntimeError("Не удалось остановить службу ProxiFyre")
    return {"ok": True, "detail": "Обход по программам выключен"}


def _wait_port(port: int, timeout: float = 25.0) -> bool:
    """Ждёт, пока Happ поднимет локальный SOCKS (после connect это не мгновенно)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_open(port):
            return True
        time.sleep(0.5)
    return port_open(port)


def happ_ensure_apps_mode(restart: bool = False) -> bool:
    """Гарантирует, что Happ работает как локальный SOCKS без TUN и без
    системного прокси Windows — только так «обход по программам» имеет смысл.

    Happ в режиме «Прокси» включает WinINet-прокси на весь ПК, и тогда галочки
    в списке программ ничего не значат: браузер идёт через VPN независимо от
    выбора. Возвращает True, если что-то меняли.
    """
    current = happ_mode_read()
    changed = False
    if current["tun"] or current["system_proxy"]:
        happ_mode_write(tun=False, system_proxy=False)
        changed = True
    if happ_config_tun_ensure(False):
        changed = True
    enable, server = happ_system_proxy()
    if enable:
        changed = True
    set_windows_system_proxy(False)
    if changed and restart and _image_pids(Path(bypass_cfg()["happ_path"]).name):
        happ_tunnel_restart()
    return changed


def _force_apps_mode() -> bool:
    """Совместимость: то же, что happ_ensure_apps_mode(restart=True)."""
    return happ_ensure_apps_mode(restart=True)


def bypass_apply(scope: str | None = None, mode: str | None = None) -> dict:
    """Единая точка применения: весь трафик или только выбранные программы.

    Движки независимы: Happ (через ProxiFyre) можно держать для выбранных
    программ, а GoodbyeDPI — включённым для остального трафика.
    """
    cfg = bypass_cfg()
    scope = str(scope or cfg["scope"]).lower()
    mode = str(mode or cfg["mode"]).lower()
    if scope not in BYPASS_SCOPES:
        raise RuntimeError(f"неизвестная область обхода {scope!r}")
    if mode not in ("happ", "gbd"):
        raise RuntimeError(f"неизвестный режим {mode!r}")
    if scope == "apps" and not normalize_apps(cfg["apps"]):
        raise RuntimeError("Не выбрано ни одной программы")

    notes: list[str] = []

    def add(result) -> None:
        if isinstance(result, dict) and result.get("detail"):
            notes.append(str(result["detail"]))

    if mode == "happ":
        want_tun = scope == "all"
        current = happ_mode_read()
        settings_changed = (current["tun"] != want_tun or current["system_proxy"] != want_tun)

        if settings_changed:
            happ_mode_write(tun=want_tun, system_proxy=want_tun)
            notes.append(
                "Happ: весь трафик (TUN)" if want_tun
                else "Happ: без TUN — только локальный SOCKS для выбранных программ"
            )

        happ_config_tun_ensure(want_tun)

        if want_tun:
            happ_tun_enable()
            if happ_direct_ru_ensure():
                notes.append("Happ: Яндекс и российские сайты — напрямую, минуя прокси")
                settings_changed = True
        else:
            set_windows_system_proxy(False)
            happ_tun_disable()

        # Запуск/перезапуск туннеля Happ
        happ_pids = _image_pids(Path(cfg["happ_path"]).name)
        need_restart = settings_changed or (not want_tun and happ_tun_active())
        if not happ_connected():
            add(bypass_happ_start())
        elif need_restart and happ_pids:
            add(happ_tunnel_restart())

        # Управление ProxiFyre
        if scope == "apps":
            port = happ_socks_port()
            if not _wait_port(port):
                raise RuntimeError(
                    f"Локальный SOCKS Happ не отвечает на 127.0.0.1:{port} — "
                    "обход по программам не заработает. Проверьте, что Happ подключён."
                )
            add(proxifyre_start(cfg["apps"], port))
            set_windows_system_proxy(False)
            happ_tun_disable()
        else:
            add(proxifyre_stop())
    else:
        add(bypass_gbd_start())
        if happ_connected():
            notes.append(
                "GoodbyeDPI работает параллельно с Happ: выбранные программы "
                "идут через туннель, остальной трафик правит DPI."
            )

    bypass_cfg_save(mode=mode, scope=scope)
    return {"ok": True, "detail": "\n".join(filter(None, notes))}


def bypass_check_ip(timeout: float = 3.0) -> dict:
    """Определяет текущий внешний IP-адрес для быстрой проверки работы обхода.

    Одновременно делает DNS-leak тест: если ответ с разных сервисов отличается —
    один из них (DNS или прокси) просачивается отдельно.
    """
    import urllib.request
    urls = [
        "https://api.ipify.org?format=json",
        "https://ifconfig.me/all.json",
        "https://api6.ipify.org?format=json",  # IPv6, для сопоставления
    ]
    results: list[str] = []
    for url in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "curl/7.68.0"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                text = resp.read().decode("utf-8", errors="replace").strip()
                if "{" in text:
                    data = json.loads(text)
                    ip = data.get("ip") or data.get("ip_addr")
                else:
                    ip = text
                if ip and len(str(ip).split(".")) == 4:
                    results.append(str(ip).strip())
        except Exception:
            continue
    ip = results[0] if results else ""
    ok = bool(ip)
    leak_dns = ""
    # DNS-leak тест: resolver.info показывает, через какой DNS-сервер был получен ответ.
    try:
        req = urllib.request.Request(
            "https://dnsleaktest.com/api.php",
            headers={"User-Agent": "curl/7.68.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            leak_dns = resp.read().decode("utf-8", errors="replace").strip()
    except Exception:
        pass
    detail = {}
    if len(set(results)) > 1:
        detail["note"] = f"Разные IP у сервисов: {', '.join(results)} — возможно, DNS утекает отдельно"
    if leak_dns:
        detail["dns"] = leak_dns
    return {"ok": ok, "ip": ip, "results": results, "detail": detail,
            "dns_leak": leak_dns}


