# --------------------------------------------------------------------------- #
#  Bypass: обход по выбранным программам (каталог, режим Happ, ProxiFyre)
# --------------------------------------------------------------------------- #
BYPASS_SCOPES = ("all", "apps")
HAPP_SOCKS_DEFAULT = 10808
HAPP_PREFS_KEY = r"Software\Happ\OrganizationDefaults\Preferences\AdvancedSettings"
HAPP_CONFIG_FILE = (Path(os.environ.get("LOCALAPPDATA") or str(Path.home()))
                    / "Happ" / "config.json")
PROXIFYRE_DIR = (Path(os.environ.get("LOCALAPPDATA") or str(Path.home()))
                 / "BypassManager" / "proxifyre")
PROXIFYRE_EXE = PROXIFYRE_DIR / "ProxiFyre.exe"
PROXIFYRE_CONFIG = PROXIFYRE_DIR / "app-config.json"
PROXIFYRE_LOG_DIR = PROXIFYRE_DIR / "logs"
PROXIFYRE_SERVICE = "ProxiFyre"
PACKET_FILTER_DRIVER = "ndisrd"   # Windows Packet Filter — нужен ProxiFyre

SVC_STOPPED = 1
SVC_RUNNING = 4

AVATAR_COLORS = ("#4c8dff", "#4fd18b", "#f0b429", "#c084fc",
                 "#f97362", "#38bdf8", "#f472b6", "#a3e635")

APP_SKIP_PREFIXES = ("unins", "setup", "install", "update", "vcredist", "dxsetup")


@dataclass(frozen=True)
class AppEntry:
    """Строка каталога программ: одна запись = один exe."""

    exe: str
    name: str
    path: str = ""
    running: bool = False
    source: str = ""


def pretty_app_name(exe: str) -> str:
    """discord-desktop.exe → «Discord Desktop»."""
    stem = Path(str(exe or "")).stem
    stem = re.sub(r"(?i)([-_](x64|x86|amd64|setup|portable|bin))$", "", stem)
    words = [word for word in re.split(r"[-_\s]+", stem) if word]
    if not words:
        return Path(str(exe)).name or str(exe or "")
    return " ".join(word[:1].upper() + word[1:] for word in words)


def normalize_app(value: str) -> str:
    """Путь или имя → имя exe. Пустое/не-exe → ''."""
    text = str(value or "").strip().strip('"')
    if not text:
        return ""
    name = Path(text.replace("/", "\\")).name if ("\\" in text or "/" in text) else text
    name = name.strip()
    return name if name.lower().endswith(".exe") else ""


def normalize_apps(values) -> list[str]:
    """Список программ без дублей и мусора, в порядке появления."""
    seen: set[str] = set()
    result: list[str] = []
    for value in values or []:
        name = normalize_app(value)
        if not name:
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(name)
    return result


def app_score(entry: AppEntry, query: str) -> int:
    """Насколько запись подходит под запрос. 0 — не подходит."""
    text = (query or "").strip().lower()
    if not text:
        return 1
    name = entry.name.lower()
    exe = entry.exe.lower()
    path = (entry.path or "").lower()
    total = 0
    for token in text.split():
        if name.startswith(token):
            weight = 5
        elif token in name:
            weight = 4
        elif exe.startswith(token):
            weight = 3
        elif token in exe:
            weight = 2
        elif token in path:
            weight = 1
        else:
            return 0
        total += weight
    return total


def filter_apps(entries, query: str = "", *, only_running: bool = False,
                limit: int = 0) -> list[AppEntry]:
    """Поиск по каталогу: точное совпадение выше, запущенные выше остальных."""
    scored: list[tuple[int, AppEntry]] = []
    for entry in entries or []:
        if only_running and not entry.running:
            continue
        score = app_score(entry, query)
        if score:
            scored.append((score, entry))
    scored.sort(key=lambda pair: (-pair[0], not pair[1].running, pair[1].name.lower()))
    result = [entry for _score, entry in scored]
    return result[:limit] if limit else result


def _usable_exe(path: str) -> bool:
    text = str(path or "").strip().strip('"')
    if not text.lower().endswith(".exe"):
        return False
    low = text.lower()
    if "\\windows\\" in low:          # системные утилиты не нужны в списке
        return False
    name = Path(text).name.lower()
    if name.startswith(APP_SKIP_PREFIXES):
        return False
    return True


def _running_exe_paths() -> list[str]:
    """Пути запущенных процессов через ctypes — без PowerShell и без задержек."""
    if sys.platform != "win32":
        return []
    try:
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        count = 2048
        buffer = (ctypes.c_ulong * count)()
        needed = ctypes.c_ulong()
        if not kernel32.EnumProcesses(buffer, ctypes.sizeof(buffer), ctypes.byref(needed)):
            return []
        total = min(count, needed.value // ctypes.sizeof(ctypes.c_ulong))
        paths: list[str] = []
        for index in range(total):
            pid = int(buffer[index])
            if not pid:
                continue
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                continue
            try:
                name_buffer = ctypes.create_unicode_buffer(32768)
                size = ctypes.c_ulong(len(name_buffer))
                if kernel32.QueryFullProcessImageNameW(
                    handle, 0, name_buffer, ctypes.byref(size)
                ):
                    if name_buffer.value:
                        paths.append(name_buffer.value)
            finally:
                kernel32.CloseHandle(handle)
        return paths
    except Exception:  # noqa: BLE001
        return []


def _registry_app_paths() -> list[str]:
    """«App Paths» из реестра: большинство установленных программ."""
    try:
        import winreg
    except ImportError:
        return []
    paths: list[str] = []
    sub = r"Software\Microsoft\Windows\CurrentVersion\App Paths"
    views = (0, getattr(winreg, "KEY_WOW64_64KEY", 0), getattr(winreg, "KEY_WOW64_32KEY", 0))
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        for view in views:
            try:
                with winreg.OpenKey(hive, sub, 0, winreg.KEY_READ | view) as key:
                    index = 0
                    while True:
                        try:
                            child = winreg.EnumKey(key, index)
                        except OSError:
                            break
                        index += 1
                        try:
                            with winreg.OpenKey(key, child) as app_key:
                                value, _kind = winreg.QueryValueEx(app_key, "")
                        except OSError:
                            continue
                        value = str(value or "").strip().strip('"')
                        if value:
                            paths.append(value)
            except OSError:
                continue
    return paths


def _start_menu_apps() -> list[tuple[str, str]]:
    """Ярлыки меню «Пуск»: (подпись, путь к exe). Один вызов PowerShell."""
    script = (
        "$shell = New-Object -ComObject WScript.Shell\n"
        "$dirs = @(\"$env:APPDATA\\Microsoft\\Windows\\Start Menu\\Programs\", "
        "\"$env:ProgramData\\Microsoft\\Windows\\Start Menu\\Programs\")\n"
        "foreach ($dir in $dirs) {\n"
        "  if (-not (Test-Path $dir)) { continue }\n"
        "  Get-ChildItem -Path $dir -Recurse -Filter *.lnk -ErrorAction SilentlyContinue | "
        "ForEach-Object {\n"
        "    try {\n"
        "      $target = $shell.CreateShortcut($_.FullName).TargetPath\n"
        "      if ($target -and $target.ToLower().EndsWith('.exe')) {\n"
        "        \"{0}`t{1}\" -f $_.BaseName, $target\n"
        "      }\n"
        "    } catch { }\n"
        "  }\n"
        "}\n"
    )
    try:
        output = ps_run(script, timeout=90)
    except Exception:  # noqa: BLE001
        return []
    result: list[tuple[str, str]] = []
    for line in output.splitlines():
        if "\t" not in line:
            continue
        label, _, target = line.partition("\t")
        label = label.strip()
        target = target.strip()
        if target:
            result.append((label, target))
    return result


def app_catalog(running_paths=None, registry_paths=None,
                shortcuts=None) -> list[AppEntry]:
    """Каталог программ: запущенные + реестр + меню «Пуск», без дублей по exe."""
    if running_paths is None:
        running_paths = _running_exe_paths()
    if registry_paths is None:
        registry_paths = _registry_app_paths()
    if shortcuts is None:
        shortcuts = _start_menu_apps()

    entries: dict[str, AppEntry] = {}

    def key_of(path: str) -> str:
        return Path(path).name.lower()

    for path in running_paths:
        if not _usable_exe(path):
            continue
        entries[key_of(path)] = AppEntry(
            exe=Path(path).name, name=pretty_app_name(Path(path).name),
            path=path, running=True, source="running",
        )

    for path in registry_paths:
        if not _usable_exe(path):
            continue
        name = Path(path).name
        key = key_of(path)
        if key in entries:
            if not entries[key].path:
                entries[key] = replace(entries[key], path=path)
            continue
        entries[key] = AppEntry(exe=name, name=pretty_app_name(name),
                                path=path, source="registry")

    for label, path in shortcuts:
        if not _usable_exe(path):
            continue
        name = Path(path).name
        key = key_of(path)
        title = label.strip() or pretty_app_name(name)
        previous = entries.get(key)
        if previous is None:
            entries[key] = AppEntry(exe=name, name=title, path=path, source="startmenu")
        elif previous.source != "running":
            entries[key] = replace(previous, name=title, path=previous.path or path,
                                   source="startmenu")

    items = list(entries.values())
    items.sort(key=lambda item: (not item.running, item.name.lower()))
    return items


# -- Happ: режим туннеля и локальный SOCKS ---------------------------------- #
def happ_socks_port_from(data) -> int:
    """Порт локального SOCKS Happ из его конфига (sing-box)."""
    if not isinstance(data, dict):
        return HAPP_SOCKS_DEFAULT
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


def happ_mode_read() -> dict:
    """Как Happ сейчас пускает трафик: TUN-адаптер и системный прокси."""
    return {
        "tun": bool(_reg_get_int(HAPP_PREFS_KEY, "tun", 1)),
        "system_proxy": bool(_reg_get_int(HAPP_PREFS_KEY, "system_proxy", 1)),
    }


def happ_mode_write(tun: bool | None = None, system_proxy: bool | None = None) -> dict:
    if tun is not None and not _reg_set_int(HAPP_PREFS_KEY, "tun", 1 if tun else 0):
        raise RuntimeError(
            "Не удалось изменить настройку Happ (tun) в реестре HKCU\\Software\\Happ"
        )
    if system_proxy is not None and not _reg_set_int(
        HAPP_PREFS_KEY, "system_proxy", 1 if system_proxy else 0
    ):
        raise RuntimeError(
            "Не удалось изменить настройку Happ (systemProxy) в реестре HKCU\\Software\\Happ"
        )
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


# -- ProxiFyre: перенаправление только выбранных процессов ------------------ #
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


_ADVAPI_READY = False


def _advapi():
    global _ADVAPI_READY
    api = ctypes.windll.advapi32
    if not _ADVAPI_READY:
        api.OpenSCManagerW.restype = ctypes.c_void_p
        api.OpenSCManagerW.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32]
        api.OpenServiceW.restype = ctypes.c_void_p
        api.OpenServiceW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint32]
        api.QueryServiceStatus.argtypes = [ctypes.c_void_p, ctypes.POINTER(SERVICE_STATUS)]
        api.StartServiceW.restype = ctypes.c_int
        api.StartServiceW.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                      ctypes.POINTER(ctypes.c_wchar_p)]
        api.ControlService.restype = ctypes.c_int
        api.ControlService.argtypes = [ctypes.c_void_p, ctypes.c_uint32,
                                       ctypes.POINTER(SERVICE_STATUS)]
        api.CloseServiceHandle.argtypes = [ctypes.c_void_p]
        _ADVAPI_READY = True
    return api


def _svc_state(name: str) -> int | None:
    """Состояние службы/драйвера: 4 — работает, 1 — остановлена, None — нет такой."""
    if sys.platform != "win32":
        return None
    try:
        api = _advapi()
        scm = api.OpenSCManagerW(None, None, 0x0001)  # SC_MANAGER_CONNECT
        if not scm:
            return None
        try:
            handle = api.OpenServiceW(scm, name, 0x0004)  # SERVICE_QUERY_STATUS
            if not handle:
                return None
            try:
                status = SERVICE_STATUS()
                if not api.QueryServiceStatus(handle, ctypes.byref(status)):
                    return None
                return int(status.dwCurrentState)
            finally:
                api.CloseServiceHandle(handle)
        finally:
            api.CloseServiceHandle(scm)
    except Exception:  # noqa: BLE001
        return None


def _svc_start(name: str, wait: float = 12.0) -> bool:
    if sys.platform != "win32":
        return False
    try:
        api = _advapi()
        scm = api.OpenSCManagerW(None, None, 0x0001)
        if not scm:
            return False
        try:
            handle = api.OpenServiceW(scm, name, 0x0010 | 0x0004)
            if not handle:
                return False
            try:
                api.StartServiceW(handle, 0, None)
            finally:
                api.CloseServiceHandle(handle)
        finally:
            api.CloseServiceHandle(scm)
    except Exception:  # noqa: BLE001
        return False
    deadline = time.time() + wait
    while time.time() < deadline:
        if _svc_state(name) == SVC_RUNNING:
            return True
        time.sleep(0.3)
    return _svc_state(name) == SVC_RUNNING


def _svc_stop(name: str, wait: float = 12.0) -> bool:
    if sys.platform != "win32":
        return False
    try:
        api = _advapi()
        scm = api.OpenSCManagerW(None, None, 0x0001)
        if not scm:
            return False
        try:
            handle = api.OpenServiceW(scm, name, 0x0020 | 0x0004)
            if not handle:
                return False
            try:
                status = SERVICE_STATUS()
                api.ControlService(handle, 1, ctypes.byref(status))  # SERVICE_CONTROL_STOP
            finally:
                api.CloseServiceHandle(handle)
        finally:
            api.CloseServiceHandle(scm)
    except Exception:  # noqa: BLE001
        return False
    deadline = time.time() + wait
    while time.time() < deadline:
        if _svc_state(name) == SVC_STOPPED:
            return True
        time.sleep(0.3)
    return _svc_state(name) == SVC_STOPPED


def proxifyre_config_data(apps, port: int) -> dict:
    return {
        "logLevel": "INFO",
        "proxies": [{
            "appNames": normalize_apps(apps),
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


def proxifyre_driver_ok() -> bool:
    return _svc_state(PACKET_FILTER_DRIVER) == SVC_RUNNING


def proxifyre_status() -> dict:
    state = _svc_state(PROXIFYRE_SERVICE)
    apps: list[str] = []
    try:
        data = json.loads(PROXIFYRE_CONFIG.read_text("utf-8"))
        for proxy in data.get("proxies") or []:
            if isinstance(proxy, dict):
                apps.extend(normalize_apps(proxy.get("appNames")))
    except (OSError, ValueError, AttributeError):
        pass
    return {
        "installed": PROXIFYRE_EXE.exists(),
        "driver": proxifyre_driver_ok(),
        "service": state,
        "running": state == SVC_RUNNING,
        "apps": normalize_apps(apps),
    }


def _proxifyre_install() -> bool:
    if not PROXIFYRE_EXE.exists():
        return False
    if is_admin():
        run_tool([str(PROXIFYRE_EXE), "install"], cwd=str(PROXIFYRE_DIR), timeout=120)
    else:
        try:
            code = int(ctypes.windll.shell32.ShellExecuteW(
                None, "runas", str(PROXIFYRE_EXE), "install", str(PROXIFYRE_DIR), 0,
            ))
        except Exception:  # noqa: BLE001
            return False
        if code <= 32:
            return False
    for _ in range(40):
        time.sleep(0.5)
        if _svc_state(PROXIFYRE_SERVICE) is not None:
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
        raise RuntimeError(
            "Драйвер Windows Packet Filter (ndisrd) не запущен — "
            "обход по программам без него невозможен"
        )
    changed = proxifyre_write_config(names, port)
    state = _svc_state(PROXIFYRE_SERVICE)
    if state is None:
        if not _proxifyre_install():
            raise RuntimeError(
                "Служба ProxiFyre не установлена: подтвердите запрос прав администратора"
            )
        state = _svc_state(PROXIFYRE_SERVICE)
    if state == SVC_RUNNING and not changed:
        return {"ok": True, "detail": f"Обход уже включён для {len(names)} программ"}
    if state == SVC_RUNNING:
        _svc_stop(PROXIFYRE_SERVICE)
    if not _svc_start(PROXIFYRE_SERVICE):
        raise RuntimeError(
            "Не удалось запустить службу ProxiFyre — нужны права администратора"
        )
    return {
        "ok": True,
        "detail": f"Обход включён для {len(names)} программ через SOCKS 127.0.0.1:{port}",
    }


def proxifyre_stop() -> dict:
    state = _svc_state(PROXIFYRE_SERVICE)
    if state is None:
        return {"ok": True, "detail": ""}
    if state != SVC_RUNNING:
        return {"ok": True, "detail": "Обход по программам уже выключен"}
    if not _svc_stop(PROXIFYRE_SERVICE):
        raise RuntimeError("Не удалось остановить службу ProxiFyre")
    return {"ok": True, "detail": "Обход по программам выключен"}


def bypass_apply(scope: str | None = None, mode: str | None = None) -> dict:
    """Единая точка применения: весь трафик или только выбранные программы."""
    cfg = bypass_cfg()
    scope = str(scope or cfg["scope"]).lower()
    mode = str(mode or cfg["mode"]).lower()
    if scope not in BYPASS_SCOPES:
        raise RuntimeError(f"неизвестная область обхода {scope!r}")
    if mode not in ("happ", "gbd"):
        raise RuntimeError(f"неизвестный режим {mode!r}")
    if scope == "apps" and mode != "happ":
        raise RuntimeError(
            "Обход по программам работает только через Happ: "
            "GoodbyeDPI правит пакеты и не различает процессы"
        )
    if scope == "apps" and not normalize_apps(cfg["apps"]):
        raise RuntimeError("Не выбрано ни одной программы")

    notes: list[str] = []

    def add(result) -> None:
        if isinstance(result, dict) and result.get("detail"):
            notes.append(str(result["detail"]))

    # 1. Гасим то, что противоречит выбранному режиму
    if mode == "gbd" and (happ_connected() or _image_pids("xray.exe")):
        add(bypass_happ_stop())
    if mode == "happ" and _image_pids("goodbyedpi.exe"):
        add(bypass_stop("gbd"))

    # 2. Настройки Happ: TUN нужен только для «всего трафика»
    if mode == "happ":
        want_tun = scope == "all"
        current = happ_mode_read()
        if current["tun"] != want_tun or current["system_proxy"] != want_tun:
            happ_mode_write(tun=want_tun, system_proxy=want_tun)
            notes.append(
                "Happ: весь трафик (TUN)" if want_tun
                else "Happ: без TUN — только локальный SOCKS для выбранных программ"
            )
            if _image_pids(Path(cfg["happ_path"]).name):
                add(happ_tunnel_restart())

    # 3. Включаем движок
    if mode == "happ":
        add(bypass_happ_start())
        if scope == "apps":
            port = happ_socks_port()
            if not port_open(port):
                raise RuntimeError(
                    f"Локальный SOCKS Happ не отвечает на 127.0.0.1:{port} — "
                    "обход по программам не заработает. Проверьте, что Happ подключён."
                )
            add(proxifyre_start(cfg["apps"], port))
        else:
            add(proxifyre_stop())
    else:
        add(bypass_gbd_start())

    bypass_cfg_save(mode=mode, scope=scope)
    return {"ok": True, "detail": "\n".join(filter(None, notes))}
