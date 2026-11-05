"""Каталог установленных программ: слияние источников, дедупликация, поиск.

Источники: запущенные процессы (ctypes), реестр Uninstall, ярлыки меню «Пуск».
Чистый модуль без tkinter — GUI только рисует то, что вернул `app_catalog`.
"""

from __future__ import annotations

import ctypes
import re
import sys
from dataclasses import dataclass, replace
from pathlib import Path

from winbase import ps_run

APP_SKIP_PREFIXES = ("unins", "setup", "install", "update", "vcredist", "dxsetup", "repair")


INTERNAL_CHILD_PROCESSES = {
    "language_server.exe",
    "editor_sdk.exe",
    "betterleaks.exe",
    "sandbox-center.exe",
    "sandbox-cli.exe",
    "workbuddy-editor-sdk-supervisor.exe",
    "webm_encoder.exe",
    "cursor-server.exe",
}


def is_internal_child_process(exe: str, path: str = "") -> bool:
    """Определяет, является ли процесс скрытым бэкендом (например, editor_sdk у WorkBuddy)."""
    exe_low = str(exe or "").lower()
    path_low = str(path or "").lower()
    if exe_low in ("workbuddyai.exe", "antigravity.exe"):
        return False
    if "shorts-autopilot" in path_low or "shorts-gui" in path_low:
        return False
    if "workbuddy" in path_low or ".workbuddy-ai" in path_low:
        return True
    if "antigravity" in path_low:
        return True
    if exe_low in INTERNAL_CHILD_PROCESSES:
        return True
    return False


def parent_app_for(exe: str, path: str = "") -> str | None:
    """Возвращает имя главного приложения для служебного процесса."""
    exe_low = str(exe or "").lower()
    path_low = str(path or "").lower()
    if (
        exe_low in (
            "editor_sdk.exe", "betterleaks.exe", "sandbox-center.exe",
            "sandbox-cli.exe", "workbuddy-editor-sdk-supervisor.exe",
        )
        or ("workbuddy" in path_low or ".workbuddy-ai" in path_low)
    ):
        return "WorkBuddyAI.exe"
    if (
        exe_low in ("language_server.exe", "webm_encoder.exe")
        or ("antigravity" in path_low and exe_low != "antigravity.exe")
    ):
        return "Antigravity.exe"
    return None

APP_COMPANIONS: dict[str, list[str]] = {
    "antigravity.exe": ["language_server.exe"],
    "workbuddyai.exe": [
        "editor_sdk.exe",
        "betterleaks.exe",
        "sandbox-center.exe",
        "sandbox-cli.exe",
        "workbuddy-editor-sdk-supervisor.exe",
        "node.exe",
    ],
    "workbuddy.exe": [
        "editor_sdk.exe",
        "betterleaks.exe",
        "sandbox-center.exe",
        "sandbox-cli.exe",
        "workbuddy-editor-sdk-supervisor.exe",
        "node.exe",
    ],
    "cursor.exe": ["cursor-server.exe", "node.exe"],
    "code.exe": ["node.exe"],
}


def expand_companions(apps) -> list[str]:
    """Дополняет список программ зависимыми процессами (бэкендами).

    Например, для Antigravity.exe автоматически добавляется language_server.exe,
    а для WorkBuddyAI.exe — editor_sdk.exe, node.exe и betterleaks.exe.
    """
    result = list(normalize_apps(apps))
    existing_lower = {Path(a).name.lower() for a in result}
    for item in list(result):
        key = Path(item).name.lower()
        for comp in APP_COMPANIONS.get(key) or []:
            if comp.lower() not in existing_lower:
                result.append(comp)
                existing_lower.add(comp.lower())
    return result


@dataclass(frozen=True)
class AppEntry:
    """Строка каталога программ: одна запись = один exe."""

    exe: str
    name: str
    path: str = ""
    running: bool = False
    source: str = ""


def pretty_app_name(exe: str, path: str = "") -> str:
    """discord-desktop.exe → «Discord Desktop»."""
    path_low = str(path or "").lower()
    exe_low = str(exe or "").lower()
    if exe_low == "language_server.exe" and "antigravity" in path_low:
        return "Antigravity (Language Server)"
    if exe_low == "editor_sdk.exe" and "workbuddy" in path_low:
        return "WorkBuddy AI"
    if exe_low == "betterleaks.exe" and "workbuddy" in path_low:
        return "WorkBuddy (Sandbox)"
    if exe_low == "node.exe" and "workbuddy" in path_low:
        return "WorkBuddy (Node Backend)"

    exe_low = str(exe or "").lower()
    if "shorts-autopilot" in path_low or "shorts-gui" in path_low:
        return "Shorts Autopilot"
    if exe_low == "workbuddyai.exe":
        return "WorkBuddy AI"
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
    return [path for _pid, path in running_processes()]


def running_processes() -> list[tuple[int, str]]:
    """(pid, путь) для всех доступных процессов — один проход по psapi."""
    if sys.platform != "win32":
        return []
    try:
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        # Без argtypes/restype ctypes режет 64-битные HANDLE до int — OpenProcess
        # возвращал «мусорный» handle, QueryFullProcessImageNameW падал, и список
        # запущенных программ всегда был пустым.
        kernel32.OpenProcess.restype = ctypes.c_void_p
        kernel32.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32]
        kernel32.CloseHandle.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.QueryFullProcessImageNameW.restype = ctypes.c_int
        kernel32.QueryFullProcessImageNameW.argtypes = [
            ctypes.c_void_p, ctypes.c_uint32, ctypes.c_wchar_p,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        # EnumProcesses живёт в psapi.dll (в kernel32 его нет — вызов падал).
        psapi = ctypes.windll.psapi
        psapi.EnumProcesses.restype = ctypes.c_int
        psapi.EnumProcesses.argtypes = [
            ctypes.POINTER(ctypes.c_ulong), ctypes.c_ulong,
            ctypes.POINTER(ctypes.c_ulong),
        ]
        count = 2048
        buffer = (ctypes.c_ulong * count)()
        needed = ctypes.c_ulong()
        if not psapi.EnumProcesses(buffer, ctypes.sizeof(buffer), ctypes.byref(needed)):
            return []
        total = min(count, needed.value // ctypes.sizeof(ctypes.c_ulong))
        paths: list[tuple[int, str]] = []
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
                        paths.append((pid, name_buffer.value))
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
        name = Path(path).name
        if is_internal_child_process(name, path):
            parent = parent_app_for(name, path)
            if parent:
                parent_key = key_of(parent)
                prev = entries.get(parent_key)
                if prev:
                    entries[parent_key] = replace(prev, running=True)
                else:
                    entries[parent_key] = AppEntry(
                        exe=parent, name=pretty_app_name(parent, path),
                        path="", running=True, source="running",
                    )
            continue
        entries[key_of(path)] = AppEntry(
            exe=name, name=pretty_app_name(name, path),
            path=path, running=True, source="running",
        )

    for path in registry_paths:
        if not _usable_exe(path):
            continue
        name = Path(path).name
        if is_internal_child_process(name, path):
            continue
        key = key_of(path)
        if key in entries:
            if not entries[key].path:
                entries[key] = replace(entries[key], path=path)
            continue
        entries[key] = AppEntry(exe=name, name=pretty_app_name(name, path),
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
