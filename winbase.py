"""Windows-обвязка без GUI: пути приложения, config.json, запуск процессов.

Раньше это лежало в начале `tools.pyw`, и любой тест логики тянул за собой
tkinter. Теперь чистые функции живут здесь и импортируются и GUI, и модулями
bypass/apps — тесты грузят их без графической библиотеки.
"""

from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import traceback
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
APP_FILE = Path(__file__).resolve()
if getattr(sys, "frozen", False):
    # В собранном .exe __file__ указывает во временную папку распаковки,
    # поэтому настройки и шаблоны храним рядом с самим .exe.
    APP_DIR = Path(sys.executable).resolve().parent
    APP_FILE = Path(sys.executable).resolve()
CONFIG_PATH = APP_DIR / "config.json"
TARGETS_DIR = APP_DIR / "targets"

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_PS_HEADER = "[Console]::OutputEncoding=[Text.Encoding]::UTF8\n$ErrorActionPreference = 'Stop'\n"


def log_exception() -> None:
    """print_exc безопасен и в pythonw/.exe, где stderr отсутствует."""
    if sys.stderr is not None:
        traceback.print_exc()


def is_admin() -> bool:
    """Запущено ли приложение с правами администратора."""
    try:
        shell32 = ctypes.windll.shell32
        shell32.IsUserAnAdmin.restype = ctypes.c_int
        shell32.IsUserAnAdmin.argtypes = []
        return bool(shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def read_json_dict(path: str | Path, default: dict | None = None) -> dict:
    """Читает JSON-словарь; при ошибке или не-dict возвращает default (или {})."""
    fallback = default if default is not None else {}
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return fallback
    return data if isinstance(data, dict) else fallback


def write_json_dict(path: str | Path, data: dict) -> None:
    """Атомарно записывает JSON-словарь (utf-8, ensure_ascii=False, indent=2)."""
    path = Path(path)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def config_read() -> dict:
    return read_json_dict(CONFIG_PATH)


def config_write(data: dict) -> None:
    try:
        write_json_dict(CONFIG_PATH, data)
    except OSError:
        CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


def config_update(**sections) -> None:
    """Обновляет только свои разделы, не затирая настройки других вкладок."""
    data = config_read()
    data.update(sections)
    try:
        config_write(data)
    except OSError:
        pass


def ps_run(script: str, timeout: int = 120) -> str:
    """Выполняет PowerShell без окна консоли и возвращает stdout."""
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", _PS_HEADER + script],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=NO_WINDOW,
    )
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    if proc.returncode != 0:
        raise RuntimeError(err or out or f"PowerShell завершился с кодом {proc.returncode}")
    return out


def _tool_candidates(name: str) -> list[str]:
    local = os.environ.get("LOCALAPPDATA", "")
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    if name == "git":
        return [
            os.path.join(pf, "Git", "cmd", "git.exe"),
            os.path.join(pf86, "Git", "cmd", "git.exe"),
            os.path.join(local, "Programs", "Git", "cmd", "git.exe"),
            os.path.join(local, "Programs", "WorkBuddyAI", "resources", "vendor",
                         "PortableGit", "cmd", "git.exe"),
            r"C:\Users\admin\WorkBuddyAI\resources\vendor\PortableGit\cmd\git.exe",
            r"C:\Users\admin\WorkBuddy AI\WorkBuddyAI\resources\vendor\PortableGit\cmd\git.exe",
        ]
    return [
        os.path.join(pf, "GitHub CLI", "gh.exe"),
        os.path.join(local, "Programs", "GitHub CLI", "gh.exe"),
        r"C:\Users\admin\WorkBuddyAI\resources\vendor\gh\bin\gh.exe",
    ]


def find_tool(name: str) -> str:
    """Ищет git/gh в PATH и по стандартным путям установки."""
    found = shutil.which(name)
    if found:
        return found
    for candidate in _tool_candidates(name):
        if candidate and Path(candidate).exists():
            return candidate
    return ""


def tool_env() -> dict:
    env = os.environ.copy()
    dirs = [str(Path(path).parent) for path in (find_tool("git"), find_tool("gh")) if path]
    if dirs:
        env["PATH"] = os.pathsep.join(dirs + [env.get("PATH", "")])
    env["GH_PROMPT_DISABLED"] = "1"
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env


def run_tool(args: list[str], cwd: str | None = None, timeout: int = 300) -> tuple[int, str, str]:
    proc = subprocess.run(
        args,
        cwd=cwd,
        env=tool_env(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        creationflags=NO_WINDOW,
    )
    return proc.returncode, (proc.stdout or "").strip(), (proc.stderr or "").strip()
