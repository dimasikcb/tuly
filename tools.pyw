#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Мои инструменты — единое тёмное приложение для Windows.

В одном окне:
  * AutoClicker          — клики по точкам, областям и картинкам;
  * Пустые файлы и папки — поиск и удаление пустых файлов и папок;
  * DNS                  — Quad9 / Xbox / DHCP + DNS-over-HTTPS;
  * Очистка Windows      — Temp, кэши, корзина, Windows.old.

Запуск: двойной клик по tools.pyw (или pythonw tools.pyw).
"""

from __future__ import annotations

import argparse
import ctypes
import json
import os
import queue
import random
import shutil
import stat
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
import webbrowser
from dataclasses import dataclass, field, replace
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, ttk

APP_DIR = Path(__file__).resolve().parent
APP_FILE = Path(__file__).resolve()
if getattr(sys, "frozen", False):
    # В собранном .exe __file__ указывает во временную папку распаковки,
    # поэтому настройки и шаблоны храним рядом с самим .exe.
    APP_DIR = Path(sys.executable).resolve().parent
    APP_FILE = Path(sys.executable).resolve()
CONFIG_PATH = APP_DIR / "config.json"
TARGETS_DIR = APP_DIR / "targets"

# --------------------------------------------------------------------------- #
#  Тёмная палитра
# --------------------------------------------------------------------------- #
BG = "#14161b"
SIDEBAR = "#0f1115"
CARD = "#1c1f26"
CARD_HOVER = "#242833"
FIELD = "#111318"
BORDER = "#2b303a"
FG = "#e7eaf0"
MUTED = "#8a93a2"
ACCENT = "#4c8dff"
ACCENT_HOVER = "#3a7aeb"
DANGER = "#e5484d"
DANGER_HOVER = "#c73b40"
OK = "#4fd18b"
WARN = "#f0b429"
LOG_BG = "#0d0f13"
LOG_FG = "#c7d0de"

SCALE = 1.0


def px(value: float) -> int:
    """Пиксели, независимые от масштаба Windows (96 DPI = 1.0)."""
    return int(round(value * SCALE))


def _dpi_aware() -> None:
    """Чтобы координаты мыши и скриншота совпадали при масштабе Windows != 100 %."""
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


_dpi_aware()

# --------------------------------------------------------------------------- #
#  Зависимости (нужны только автокликеру)
# --------------------------------------------------------------------------- #
try:
    import cv2
    import numpy as np
    import pyautogui
    from PIL import Image, ImageGrab

    HAS_DEPS = True
    DEPS_ERROR = ""
except Exception as _dep_error:  # noqa: BLE001
    HAS_DEPS = False
    DEPS_ERROR = str(_dep_error)

try:
    import keyboard

    HAS_KEYBOARD = True
except Exception:  # noqa: BLE001
    HAS_KEYBOARD = False


# --------------------------------------------------------------------------- #
#  Тема и мелкие помощники
# --------------------------------------------------------------------------- #
def apply_dark_theme(root: tk.Misc) -> None:
    style = ttk.Style(root)
    try:
        style.theme_use("clam")
    except tk.TclError:
        pass
    style.configure(
        ".",
        background=BG,
        foreground=FG,
        fieldbackground=FIELD,
        bordercolor=BORDER,
        lightcolor=BORDER,
        darkcolor=BORDER,
        focuscolor=ACCENT,
        troughcolor=FIELD,
        arrowcolor=FG,
        selectbackground=ACCENT,
        selectforeground="#ffffff",
    )
    style.configure("TFrame", background=BG)
    style.configure("TLabel", background=BG, foreground=FG)
    style.configure("Muted.TLabel", background=BG, foreground=MUTED)
    style.configure("TButton", background=CARD, foreground=FG, padding=(px(12), px(6)), borderwidth=0)
    style.map(
        "TButton",
        background=[("active", CARD_HOVER), ("disabled", "#191c22")],
        foreground=[("disabled", MUTED)],
    )
    style.configure("TEntry", fieldbackground=FIELD, foreground=FG, insertcolor=FG, padding=px(6), bordercolor=BORDER)
    style.map("TEntry", bordercolor=[("focus", ACCENT)], lightcolor=[("focus", ACCENT)])
    style.configure("TSpinbox", fieldbackground=FIELD, foreground=FG, insertcolor=FG, arrowcolor=FG, padding=px(4))
    style.configure("TCombobox", fieldbackground=FIELD, foreground=FG, arrowcolor=FG, padding=px(4))
    style.map("TCombobox", fieldbackground=[("readonly", FIELD)], foreground=[("readonly", FG)])
    root.option_add("*TCombobox*Listbox.background", FIELD)
    root.option_add("*TCombobox*Listbox.foreground", FG)
    root.option_add("*TCombobox*Listbox.selectBackground", ACCENT)
    style.configure("Treeview", background=CARD, fieldbackground=CARD, foreground=FG, bordercolor=BORDER, rowheight=px(26))
    style.map("Treeview", background=[("selected", ACCENT)], foreground=[("selected", "#ffffff")])
    style.configure("Treeview.Heading", background=FIELD, foreground=MUTED, relief="flat", padding=(px(6), px(6)))
    style.map("Treeview.Heading", background=[("active", CARD_HOVER)])
    style.configure("TLabelframe", background=BG, bordercolor=BORDER)
    style.configure("TLabelframe.Label", background=BG, foreground=MUTED)
    style.configure("TCheckbutton", background=BG, foreground=FG)
    style.map("TCheckbutton", background=[("active", BG)], foreground=[("disabled", MUTED)])
    for name in ("Vertical.TScrollbar", "Horizontal.TScrollbar"):
        style.configure(name, background=CARD, troughcolor=BG, bordercolor=BG, arrowcolor=MUTED, width=px(12))
        style.map(name, background=[("active", CARD_HOVER)])


def enable_dark_titlebar(window: tk.Misc) -> None:
    """Тёмный заголовок окна в Windows 10/11."""
    if sys.platform != "win32":
        return
    try:
        window.update_idletasks()
        hwnd = ctypes.windll.user32.GetParent(window.winfo_id())
        value = ctypes.c_int(1)
        for attribute in (20, 19):
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)
            ) == 0:
                break
    except Exception:
        pass


def flat_button(parent: tk.Misc, text: str, command, *, kind: str = "primary", **kwargs) -> tk.Button:
    colors = {
        "primary": (ACCENT, "#ffffff", ACCENT_HOVER),
        "danger": (DANGER, "#ffffff", DANGER_HOVER),
        "ghost": (CARD, FG, CARD_HOVER),
    }
    bg, fg, active = colors.get(kind, colors["primary"])
    options = dict(
        text=text,
        command=command,
        bg=bg,
        fg=fg,
        activebackground=active,
        activeforeground=fg,
        disabledforeground="#7c8697",
        relief="flat",
        bd=0,
        padx=px(14),
        pady=px(7),
        font=("Segoe UI", 9, "bold" if kind != "ghost" else "normal"),
        cursor="hand2",
        highlightthickness=1 if kind == "ghost" else 0,
        highlightbackground=BORDER,
    )
    options.update(kwargs)
    return tk.Button(parent, **options)


def dark_check(parent: tk.Misc, text: str, variable: tk.BooleanVar, *, bg: str = BG, command=None) -> tk.Checkbutton:
    return tk.Checkbutton(
        parent,
        text=text,
        variable=variable,
        command=command,
        bg=bg,
        fg=FG,
        activebackground=bg,
        activeforeground=FG,
        selectcolor=FIELD,
        highlightthickness=0,
        bd=0,
        anchor="w",
        font=("Segoe UI", 9),
        cursor="hand2",
    )


def dark_text(parent: tk.Misc, *, height: int = 10, wrap: str = "word", **kwargs) -> tk.Text:
    options = dict(
        height=height,
        wrap=wrap,
        bg=LOG_BG,
        fg=LOG_FG,
        insertbackground=FG,
        selectbackground="#2f4a7a",
        relief="flat",
        bd=0,
        highlightthickness=1,
        highlightbackground=BORDER,
        highlightcolor=BORDER,
        font=("Consolas", 9),
        padx=px(8),
        pady=px(6),
    )
    options.update(kwargs)
    return tk.Text(parent, **options)


def fmt_size(size: float | None) -> str:
    if size is None:
        return "—"
    for unit, divider in (("ГБ", 1 << 30), ("МБ", 1 << 20), ("КБ", 1 << 10)):
        if size >= divider:
            return f"{size / divider:.1f} {unit}".replace(".", ",")
    return f"{int(size)} Б"


def config_read() -> dict:
    try:
        data = json.loads(CONFIG_PATH.read_text("utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def config_write(data: dict) -> None:
    CONFIG_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")


def config_update(**sections) -> None:
    """Обновляет только свои разделы, не затирая настройки других вкладок."""
    data = config_read()
    data.update(sections)
    try:
        config_write(data)
    except OSError:
        pass


def log_exception() -> None:
    """print_exc безопасен и в pythonw/.exe, где stderr отсутствует."""
    if sys.stderr is not None:
        traceback.print_exc()


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def pythonw_executable() -> str:
    exe = Path(sys.executable)
    if exe.name.lower() == "python.exe":
        candidate = exe.with_name("pythonw.exe")
        if candidate.exists():
            return str(candidate)
    return str(exe)


def relaunch_as_admin(tab: str) -> bool:
    """Перезапускает приложение с правами администратора на нужной вкладке."""
    if getattr(sys, "frozen", False):
        executable = str(APP_FILE)
        params = f"--tab {tab}"
    else:
        executable = pythonw_executable()
        params = '"{}" --tab {}'.format(APP_FILE, tab)
    try:
        result = ctypes.windll.shell32.ShellExecuteW(
            None, "runas", executable, params, str(APP_DIR), 1
        )
    except Exception:  # noqa: BLE001
        return False
    return int(result) > 32


class DarkMessageBox:
    """Тёмные модальные диалоги вместо стандартных светлых messagebox."""

    def _dialog(self, title: str, message: str, *, ask: bool = False, parent=None) -> bool:
        parent = parent or tk._default_root
        if parent is None:
            return False
        win = tk.Toplevel(parent)
        win.withdraw()
        win.title(title)
        win.configure(bg=CARD)
        win.transient(parent)
        win.resizable(False, False)

        body = tk.Frame(win, bg=CARD, padx=px(22), pady=px(18))
        body.pack(fill="both", expand=True)
        tk.Label(
            body, text=title, bg=CARD, fg=FG, font=("Segoe UI", 12, "bold"), anchor="w"
        ).pack(fill="x")
        tk.Label(
            body,
            text=message,
            bg=CARD,
            fg=MUTED,
            justify="left",
            anchor="w",
            wraplength=px(470),
            font=("Segoe UI", 9),
        ).pack(fill="x", pady=(px(10), 0))

        result = {"value": False}
        buttons = tk.Frame(body, bg=CARD)
        buttons.pack(fill="x", pady=(px(18), 0))

        def close(value: bool) -> None:
            result["value"] = value
            win.destroy()

        flat_button(buttons, "Да" if ask else "ОК", lambda: close(True)).pack(side="right")
        if ask:
            flat_button(buttons, "Отмена", lambda: close(False), kind="ghost").pack(
                side="right", padx=(0, px(8))
            )

        win.bind("<Return>", lambda _event: close(True))
        win.bind("<Escape>", lambda _event: close(False))
        win.protocol("WM_DELETE_WINDOW", lambda: close(False))
        win.update_idletasks()
        width, height = win.winfo_reqwidth(), win.winfo_reqheight()
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - width) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 3)
        win.geometry(f"+{x}+{y}")
        enable_dark_titlebar(win)
        win.deiconify()
        win.grab_set()
        win.focus_force()
        win.wait_window()
        return result["value"]

    def showinfo(self, title: str, message: str, **kwargs) -> bool:
        return self._dialog(title, message, parent=kwargs.get("parent"))

    def showwarning(self, title: str, message: str, **kwargs) -> bool:
        return self._dialog(title, message, parent=kwargs.get("parent"))

    def showerror(self, title: str, message: str, **kwargs) -> bool:
        return self._dialog(title, message, parent=kwargs.get("parent"))

    def askyesno(self, title: str, message: str, **kwargs) -> bool:
        return self._dialog(title, message, ask=True, parent=kwargs.get("parent"))

    def askstring(self, title: str, message: str, initial: str = "", parent=None) -> str | None:
        parent = parent or tk._default_root
        if parent is None:
            return None
        win = tk.Toplevel(parent)
        win.withdraw()
        win.title(title)
        win.configure(bg=CARD)
        win.transient(parent)
        win.resizable(False, False)

        body = tk.Frame(win, bg=CARD, padx=px(22), pady=px(18))
        body.pack(fill="both", expand=True)
        tk.Label(body, text=title, bg=CARD, fg=FG,
                 font=("Segoe UI", 12, "bold"), anchor="w").pack(fill="x")
        tk.Label(body, text=message, bg=CARD, fg=MUTED, justify="left", anchor="w",
                 wraplength=px(430), font=("Segoe UI", 9)).pack(fill="x", pady=(px(10), 0))

        value = tk.StringVar(value=initial)
        entry = ttk.Entry(body, textvariable=value)
        entry.pack(fill="x", pady=(px(10), 0), ipady=px(2))

        result = {"value": None}

        def close(ok: bool) -> None:
            if ok:
                text = value.get().strip()
                result["value"] = text or None
            win.destroy()

        buttons = tk.Frame(body, bg=CARD)
        buttons.pack(fill="x", pady=(px(18), 0))
        flat_button(buttons, "ОК", lambda: close(True)).pack(side="right")
        flat_button(buttons, "Отмена", lambda: close(False), kind="ghost").pack(
            side="right", padx=(0, px(8))
        )

        win.bind("<Return>", lambda _event: close(True))
        win.bind("<Escape>", lambda _event: close(False))
        win.protocol("WM_DELETE_WINDOW", lambda: close(False))
        win.update_idletasks()
        width, height = win.winfo_reqwidth(), win.winfo_reqheight()
        x = parent.winfo_rootx() + max(0, (parent.winfo_width() - width) // 2)
        y = parent.winfo_rooty() + max(0, (parent.winfo_height() - height) // 3)
        win.geometry(f"+{x}+{y}")
        enable_dark_titlebar(win)
        win.deiconify()
        win.grab_set()
        win.focus_force()
        entry.focus_set()
        entry.select_range(0, "end")
        win.wait_window()
        return result["value"]


messagebox = DarkMessageBox()

# --------------------------------------------------------------------------- #
#  DNS: движок
# --------------------------------------------------------------------------- #
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

_PS_HEADER = "[Console]::OutputEncoding=[Text.Encoding]::UTF8\n$ErrorActionPreference = 'Stop'\n"

_PS_ADAPTER = """
$a = Get-NetAdapter -Physical -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'Up' } | Select-Object -First 1
if (-not $a) {
    $a = Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { $_.Status -eq 'Up' -and $_.InterfaceDescription -notmatch 'Tunnel|Loopback' } | Select-Object -First 1
}
if (-not $a) { throw 'Не найден активный сетевой адаптер' }
"""

_PS_TUNNEL = """
$t = Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { $_.Name -eq 'happ-xray' -or $_.InterfaceDescription -like '*Happ*' } | Select-Object -First 1
if (-not $t) { throw 'Туннель happ-xray не найден' }
"""

_PS_APPLY = _PS_ADAPTER + """
Set-DnsClientServerAddress -InterfaceIndex $a.ifIndex -ServerAddresses @(__SERVERS__)
foreach ($s in @(__SERVERS__)) {
    $ok = $false
    if (Get-Command Set-DnsClientDohServerAddress -ErrorAction SilentlyContinue) {
        try {
            Set-DnsClientDohServerAddress -ServerAddress $s -DohTemplate '__TEMPLATE__' -AutoUpgrade $true -AllowFallbackToUdp $true -ErrorAction Stop
            $ok = $true
        } catch { }
    }
    if (-not $ok) {
        netsh dns add encryption server=$s dohtemplate=__TEMPLATE__ autoupgrade=yes udpfallback=yes | Out-Null
        if ($LASTEXITCODE -ne 0) {
            netsh dns set encryption server=$s dohtemplate=__TEMPLATE__ autoupgrade=yes udpfallback=yes | Out-Null
        }
    }
}
Clear-DnsClientCache
Write-Output ('Адаптер [' + $a.Name + '] · DNS: ' + ((Get-DnsClientServerAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4).ServerAddresses -join ', '))
"""

_PS_STATE = _PS_ADAPTER + """
$dns = (Get-DnsClientServerAddress -InterfaceIndex $a.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue).ServerAddresses -join ', '
$t = Get-NetAdapter -ErrorAction SilentlyContinue | Where-Object { $_.Name -eq 'happ-xray' -or $_.InterfaceDescription -like '*Happ*' } | Select-Object -First 1
$tn = ''
$td = ''
if ($t) {
    $tn = $t.Name
    $td = (Get-DnsClientServerAddress -InterfaceIndex $t.ifIndex -AddressFamily IPv4 -ErrorAction SilentlyContinue).ServerAddresses -join ', '
}
[pscustomobject]@{ name = $a.Name; index = $a.ifIndex; dns = $dns; tunnel = $tn; tunnel_dns = $td } | ConvertTo-Json -Compress
"""

QUAD9_DNS = ["9.9.9.9", "149.112.112.112"]
QUAD9_DOH = "https://dns.quad9.net/dns-query"
XBOX_DNS = ["111.88.96.50", "111.88.96.51"]
XBOX_DOH = "https://xbox-dns.ru/dns-query"
TUNNEL_DNS = "172.19.0.2"
DNS_TEST_NAME = "www.workbuddy.ai"


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


@dataclass
class DnsState:
    name: str = ""
    index: int = 0
    dns: str = ""
    tunnel: str = ""
    tunnel_dns: str = ""


def dns_state() -> DnsState:
    out = ps_run(_PS_STATE)
    start, end = out.find("{"), out.rfind("}")
    if start < 0 or end < start:
        raise RuntimeError(out or "PowerShell не вернул состояние DNS")
    data = json.loads(out[start:end + 1])
    return DnsState(
        name=str(data.get("name") or ""),
        index=int(data.get("index") or 0),
        dns=str(data.get("dns") or ""),
        tunnel=str(data.get("tunnel") or ""),
        tunnel_dns=str(data.get("tunnel_dns") or ""),
    )


def dns_apply(servers: list[str], template: str) -> str:
    joined = ",".join(f"'{s}'" for s in servers)
    script = _PS_APPLY.replace("__SERVERS__", joined).replace("__TEMPLATE__", template)
    return ps_run(script)


def dns_dhcp() -> str:
    return ps_run(_PS_ADAPTER + """
Set-DnsClientServerAddress -InterfaceIndex $a.ifIndex -ResetServerAddresses
Clear-DnsClientCache
Write-Output ('DNS сброшен на DHCP для [' + $a.Name + ']')
""")


def dns_clear_tunnel() -> str:
    return ps_run(_PS_TUNNEL + """
Set-DnsClientServerAddress -InterfaceIndex $t.ifIndex -ResetServerAddresses
Clear-DnsClientCache
Write-Output ('DNS туннеля [' + $t.Name + '] очищен')
""")


def dns_restore_tunnel() -> str:
    return ps_run(_PS_TUNNEL + """
Set-DnsClientServerAddress -InterfaceIndex $t.ifIndex -ServerAddresses @('__DNS__')
Write-Output ('DNS туннеля [' + $t.Name + '] = __DNS__')
""".replace("__DNS__", TUNNEL_DNS))


def dns_test() -> str:
    return ps_run("""
try {
    $r = Resolve-DnsName -Name '__NAME__' -ErrorAction Stop
    $rows = @($r | Where-Object { $_.Type -eq 'A' -or $_.Type -eq 'CNAME' } | ForEach-Object {
        if ($_.Type -eq 'A') { '  A      ' + $_.Name + ' -> ' + $_.IPAddress }
        else { '  CNAME  ' + $_.Name + ' -> ' + $_.NameHost }
    })
    if ($rows.Count -eq 0) { Write-Output '  записей A и CNAME нет' } else { Write-Output ($rows -join \"`n\") }
} catch {
    Write-Output ('  ошибка разрешения: ' + $_.Exception.Message)
}
""".replace("__NAME__", DNS_TEST_NAME))

# --------------------------------------------------------------------------- #
#  Пустые файлы и папки: движок
# --------------------------------------------------------------------------- #
REPARSE_POINT = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
HIDDEN_ATTR = getattr(stat, "FILE_ATTRIBUTE_HIDDEN", 0x2)


@dataclass
class Plan:
    root: str
    files: list[str] = field(default_factory=list)
    dirs: list[str] = field(default_factory=list)
    errors: list[tuple[str, str]] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.files) + len(self.dirs)

    @property
    def is_empty(self) -> bool:
        return self.total == 0


@dataclass
class DeleteResult:
    removed_files: int = 0
    removed_dirs: int = 0
    errors: list[tuple[str, str]] = field(default_factory=list)


def _is_hidden(path: str, st: os.stat_result) -> bool:
    if os.path.basename(path).startswith("."):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & HIDDEN_ATTR)


def _is_reparse(st: os.stat_result) -> bool:
    return bool(getattr(st, "st_file_attributes", 0) & REPARSE_POINT)


def _scan_dir(path: str, plan: Plan, files: bool, dirs: bool, hidden: bool) -> bool:
    """True — папка опустеет после удаления найденного."""
    try:
        with os.scandir(path) as iterator:
            entries = list(iterator)
    except OSError as exc:
        plan.errors.append((path, str(exc)))
        return False

    files_here: list[str] = []
    dirs_here: list[str] = []
    empty_after = True

    for entry in entries:
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError as exc:
            plan.errors.append((entry.path, str(exc)))
            empty_after = False
            continue
        if entry.is_symlink() or _is_reparse(st):
            empty_after = False
        elif entry.is_dir(follow_symlinks=False):
            if dirs and _scan_dir(entry.path, plan, files, dirs, hidden):
                dirs_here.append(entry.path)
            else:
                empty_after = False
        elif entry.is_file(follow_symlinks=False):
            if files and st.st_size == 0 and (hidden or not _is_hidden(entry.path, st)):
                files_here.append(entry.path)
            else:
                empty_after = False
        else:
            empty_after = False

    plan.files.extend(files_here)
    plan.dirs.extend(dirs_here)

    if not dirs:
        return False
    if not entries:
        return True
    return empty_after and bool(files_here or dirs_here)


def scan_empty(root: str, *, files: bool = True, dirs: bool = True, hidden: bool = False) -> Plan:
    plan = Plan(root=root)
    _scan_dir(root, plan, files, dirs, hidden)
    return plan


def delete_plan(plan: Plan) -> DeleteResult:
    result = DeleteResult()
    for path in plan.files:
        try:
            os.remove(path)
            result.removed_files += 1
        except OSError as exc:
            result.errors.append((path, str(exc)))
    for path in plan.dirs:
        try:
            os.rmdir(path)
            result.removed_dirs += 1
        except OSError as exc:
            result.errors.append((path, str(exc)))
    return result


def check_blocked(path: str) -> str | None:
    full = os.path.abspath(path)
    if os.path.dirname(full) == full:
        return "Это корень диска. Выберите папку внутри него."
    normalized = os.path.normcase(full)
    for variable in ("SystemRoot", "ProgramFiles", "ProgramFiles(x86)", "ProgramData"):
        system_path = os.environ.get(variable)
        if system_path and os.path.normcase(os.path.abspath(system_path)) == normalized:
            return "Это системная папка Windows. Выберите другую."
    return None


def risk_warning(path: str) -> str | None:
    full = os.path.normcase(os.path.abspath(path))
    for variable in ("USERPROFILE", "Desktop", "Documents", "Downloads"):
        known = os.environ.get(variable)
        if known and os.path.normcase(os.path.abspath(known)) == full:
            return "Это ваша личная папка. Проверьте список перед удалением."
    return None


# --------------------------------------------------------------------------- #
#  Очистка Windows: движок
# --------------------------------------------------------------------------- #
@dataclass
class CleanupTarget:
    key: str
    label: str
    paths: list[Path] = field(default_factory=list)
    special: str = ""  # "" | "recycle" | "old"


def build_cleanup_targets() -> list[CleanupTarget]:
    windows = Path(os.environ.get("SystemRoot", r"C:\Windows"))
    local = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData" / "Local"))
    program_data = Path(os.environ.get("ProgramData", r"C:\ProgramData"))
    home = Path.home()
    user_temp = os.environ.get("TEMP", "")
    return [
        CleanupTarget("win_temp", "Временные файлы Windows", [windows / "Temp"]),
        CleanupTarget("user_temp", "Временные файлы пользователя", [Path(user_temp)] if user_temp else []),
        CleanupTarget("prefetch", "Prefetch", [windows / "Prefetch"]),
        CleanupTarget("update", "Кэш обновлений Windows", [windows / "SoftwareDistribution" / "Download"]),
        CleanupTarget("wer", "Отчёты об ошибках", [program_data / "Microsoft" / "Windows" / "WER"]),
        CleanupTarget("dumps", "Дампы падений", [local / "CrashDumps"]),
        CleanupTarget("d3d", "Кэш шейдеров DirectX", [local / "D3DCache"]),
        CleanupTarget("chrome", "Кэш Chrome", [
            local / "Google" / "Chrome" / "User Data" / "Default" / "Cache",
            local / "Google" / "Chrome" / "User Data" / "Default" / "Code Cache",
        ]),
        CleanupTarget("edge", "Кэш Edge", [
            local / "Microsoft" / "Edge" / "User Data" / "Default" / "Cache",
            local / "Microsoft" / "Edge" / "User Data" / "Default" / "Code Cache",
        ]),
        CleanupTarget("npm", "Кэш npm", [home / ".npm" / "_cacache", local / "npm" / "_cacache"]),
        CleanupTarget("pip", "Кэш pip", [local / "pip" / "cache", home / ".cache" / "pip"]),
        CleanupTarget("recycle", "Корзина", special="recycle"),
        CleanupTarget("old", "Windows.old", [windows.parent / "Windows.old"], special="old"),
    ]


def dir_size(path: Path) -> int:
    """Размер папки без перехода по ссылкам и junction-папкам."""
    total = 0
    try:
        entries = list(os.scandir(path))
    except OSError:
        return 0
    for entry in entries:
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            continue
        if entry.is_symlink() or _is_reparse(st):
            continue
        if entry.is_dir(follow_symlinks=False):
            total += dir_size(Path(entry.path))
        else:
            total += int(st.st_size)
    return total


def _purge_dir(path: Path) -> tuple[int, int]:
    """Удаляет содержимое папки, саму папку оставляет. Возвращает (байты, ошибки)."""
    freed = 0
    errors = 0
    try:
        entries = list(os.scandir(path))
    except OSError:
        return 0, 1
    for entry in entries:
        try:
            st = entry.stat(follow_symlinks=False)
        except OSError:
            errors += 1
            continue
        if entry.is_symlink() or _is_reparse(st):
            continue
        if entry.is_dir(follow_symlinks=False):
            sub_freed, sub_errors = _purge_dir(Path(entry.path))
            freed += sub_freed
            errors += sub_errors
            try:
                os.rmdir(entry.path)
            except OSError:
                errors += 1
        else:
            size = int(st.st_size)
            try:
                os.remove(entry.path)
                freed += size
            except OSError:
                errors += 1
    return freed, errors


def purge_paths(paths: list[Path]) -> tuple[int, int]:
    freed = 0
    errors = 0
    for path in paths:
        if not path.is_dir():
            continue
        part_freed, part_errors = _purge_dir(path)
        freed += part_freed
        errors += part_errors
    return freed, errors


def empty_recycle_bin() -> bool:
    try:
        result = ctypes.windll.shell32.SHEmptyRecycleBinW(None, None, 0x1 | 0x2 | 0x4)
    except Exception:  # noqa: BLE001
        return False
    return int(result) in (0, 1)


def disk_space() -> tuple[int, int] | None:
    """(свободно, всего) на системном диске."""
    try:
        usage = shutil.disk_usage(Path(os.environ.get("SystemDrive", "C:") + os.sep))
    except OSError:
        return None
    return usage.free, usage.total


def delete_windows_old() -> bool:
    old = Path(os.environ.get("SystemRoot", r"C:\Windows")).parent / "Windows.old"
    if not old.exists():
        return True
    shutil.rmtree(old, ignore_errors=True)
    return not old.exists()

# --------------------------------------------------------------------------- #
#  AutoClicker: шаги, оверлей, поиск по картинке
# --------------------------------------------------------------------------- #
KIND_NAMES = {"point": "Точка", "area": "Область", "image": "Картинка"}
BUTTONS = {"ЛКМ": "left", "ПКМ": "right", "СКМ": "middle"}


@dataclass
class Step:
    kind: str = "point"
    x: int = 0
    y: int = 0
    w: int = 0
    h: int = 0
    path: str = ""
    conf: float = 0.80
    delay: int = 0
    wait: float = 0.0


def virtual_screen() -> tuple[int, int, int, int]:
    if sys.platform == "win32":
        user32 = ctypes.windll.user32
        return (
            user32.GetSystemMetrics(76),
            user32.GetSystemMetrics(77),
            user32.GetSystemMetrics(78),
            user32.GetSystemMetrics(79),
        )
    return (0, 0, pyautogui.size().width, pyautogui.size().height)


def grab_screen() -> "Image.Image":
    try:
        return ImageGrab.grab(all_screens=True).convert("RGB")
    except TypeError:
        return ImageGrab.grab().convert("RGB")


def find_template(screen: "np.ndarray", template: "np.ndarray", confidence: float):
    screen_h, screen_w = screen.shape[:2]
    template_h, template_w = template.shape[:2]
    if template_h > screen_h or template_w > screen_w or template_h < 2 or template_w < 2:
        return None
    gray = cv2.cvtColor(template, cv2.COLOR_RGB2GRAY)
    if float(gray.std()) < 6.0:
        result = cv2.matchTemplate(screen, template, cv2.TM_SQDIFF_NORMED)
        min_val, _max_val, min_loc, _max_loc = cv2.minMaxLoc(result)
        score = 1.0 - float(min_val)
        loc = min_loc
    else:
        result = cv2.matchTemplate(screen, template, cv2.TM_CCOEFF_NORMED)
        _min_val, max_val, _min_loc, max_loc = cv2.minMaxLoc(result)
        score = float(max_val)
        loc = max_loc
    if score < confidence:
        return None
    return (loc[0] + template_w // 2, loc[1] + template_h // 2, template_w, template_h, score)


class OverlaySelector:
    """Полупрозрачный слой на весь экран: клик = точка, протяжка = область."""

    def __init__(self, master: tk.Misc, mode: str, on_done, on_cancel):
        self.mode = mode
        self.on_done = on_done
        self.on_cancel = on_cancel
        self.vx, self.vy, self.vw, self.vh = virtual_screen()
        self.start: tuple[int, int] | None = None
        self.closed = False

        self.win = tk.Toplevel(master)
        self.win.overrideredirect(True)
        self.win.geometry(f"{self.vw}x{self.vh}+{self.vx}+{self.vy}")
        self.win.configure(bg="black")
        self.win.attributes("-topmost", True)
        try:
            self.win.attributes("-alpha", 0.35)
        except tk.TclError:
            pass

        self.canvas = tk.Canvas(self.win, bg="black", highlightthickness=0, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True)

        hint = "Кликните, чтобы поставить точку" if mode == "point" \
            else "Протяните мышь, чтобы выделить область"
        self.canvas.create_text(
            self.vw // 2, px(46), text=f"{hint}   ·   Esc / ПКМ — отмена",
            fill="#7CFC00", font=("Segoe UI", 15, "bold"),
        )
        self.rect = self.canvas.create_rectangle(
            0, 0, 0, 0, outline="#7CFC00", width=3, state="hidden"
        )
        self.size_text = self.canvas.create_text(
            0, 0, text="", fill="#7CFC00", font=("Segoe UI", 11, "bold"), state="hidden"
        )
        self.info = self.canvas.create_text(
            0, 0, text="", fill="#FFD24A", font=("Consolas", 11, "bold"), anchor="nw"
        )
        self.cross_h = self.canvas.create_line(
            0, 0, 0, 0, fill="#7CFC00", dash=(4, 4), state="hidden"
        )
        self.cross_v = self.canvas.create_line(
            0, 0, 0, 0, fill="#7CFC00", dash=(4, 4), state="hidden"
        )

        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Motion>", self._on_move)
        self.canvas.bind("<ButtonPress-3>", lambda _e: self._cancel())
        self.win.bind("<Escape>", lambda _e: self._cancel())

        self.win.focus_force()
        self.win.lift()

    def _on_move(self, event) -> None:
        ax, ay = event.x + self.vx, event.y + self.vy
        self.canvas.coords(self.info, event.x + 16, event.y + 16)
        self.canvas.itemconfigure(self.info, text=f"{ax}, {ay}")
        if self.mode == "point" and self.start is None:
            self.canvas.coords(self.cross_h, 0, event.y, self.vw, event.y)
            self.canvas.coords(self.cross_v, event.x, 0, event.x, self.vh)
            self.canvas.itemconfigure(self.cross_h, state="normal")
            self.canvas.itemconfigure(self.cross_v, state="normal")

    def _on_press(self, event) -> None:
        if self.mode == "point":
            self._finish(event.x + self.vx, event.y + self.vy, 0, 0)
            return
        self.start = (event.x, event.y)
        self.canvas.itemconfigure(self.rect, state="normal")
        self.canvas.itemconfigure(self.size_text, state="normal")
        self.canvas.coords(self.rect, event.x, event.y, event.x, event.y)

    def _on_drag(self, event) -> None:
        if self.start is None:
            return
        x0, y0 = self.start
        x1, y1 = event.x, event.y
        self.canvas.coords(self.rect, x0, y0, x1, y1)
        self.canvas.coords(self.size_text, min(x0, x1) + 6, min(y0, y1) - 16)
        self.canvas.itemconfigure(self.size_text, text=f"{abs(x1 - x0)} × {abs(y1 - y0)}")

    def _on_release(self, event) -> None:
        if self.start is None:
            return
        x0, y0 = self.start
        x1, y1 = event.x, event.y
        if abs(x1 - x0) < 6 or abs(y1 - y0) < 6:
            self._finish(event.x + self.vx, event.y + self.vy, 0, 0)
            return
        self._finish(
            min(x0, x1) + self.vx, min(y0, y1) + self.vy, abs(x1 - x0), abs(y1 - y0)
        )

    def _finish(self, x: int, y: int, w: int, h: int) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.win.destroy()
        finally:
            self.on_done(x, y, w, h)

    def _cancel(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.win.destroy()
        finally:
            self.on_cancel()

# --------------------------------------------------------------------------- #
#  Базовый класс панели
# --------------------------------------------------------------------------- #
class Panel(ttk.Frame):
    def __init__(self, master: tk.Misc, hub) -> None:
        super().__init__(master)
        self.hub = hub

    def make_log(self, parent: tk.Misc, *, height: int = 10, wrap: str = "word"):
        frame = ttk.Frame(parent)
        text = dark_text(frame, height=height, wrap=wrap)
        text.tag_configure("error", foreground="#ff6b6b")
        text.tag_configure("ok", foreground=OK)
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.grid(row=0, column=0, sticky="nsew")
        scroll.grid(row=0, column=1, sticky="ns")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        return frame, text

    @staticmethod
    def append_log(text: tk.Text, line: str, tag: str | None = None) -> None:
        text.configure(state="normal")
        text.insert("end", f"{time.strftime('%H:%M:%S')}  {line}\n", (tag,) if tag else ())
        lines = int(text.index("end-1c").split(".")[0])
        if lines > 500:
            text.delete("1.0", f"{lines - 500}.0")
        text.see("end")
        text.configure(state="disabled")


# --------------------------------------------------------------------------- #
#  AutoClicker
# --------------------------------------------------------------------------- #
class AutoClickerPanel(Panel):
    def __init__(self, master: tk.Misc, hub) -> None:
        super().__init__(master, hub)
        self.steps: list[Step] = []
        self.running = False
        self.stop_evt = threading.Event()
        self.events: queue.Queue = queue.Queue()
        self.tpl_cache: dict = {}

        self._build_ui()
        self.load_config()
        self._setup_hotkeys()
        self.after(80, self._poll)
        self.log("Готов к работе. Добавьте шаг: точку, область или картинку.")
        if not HAS_DEPS:
            self.log("Не установлены зависимости — запуск недоступен.")
        elif not HAS_KEYBOARD:
            self.log("Горячие клавиши недоступны (pip install keyboard).")

    # ------------------------------------------------------------------ UI --
    def _build_ui(self) -> None:
        bar = ttk.Frame(self)
        bar.pack(fill="x")
        for text, command, width in (
            ("+ Точка", self.add_point, 11),
            ("+ Область", self.add_area, 12),
            ("+ Картинка", self.add_image, 12),
        ):
            ttk.Button(bar, text=text, command=command, width=width).pack(side="left", padx=(0, 6))
        ttk.Button(bar, text="Удалить", command=self.delete_selected, width=10).pack(side="left", padx=(12, 0))
        ttk.Button(bar, text="↑", command=lambda: self.move_selected(-1), width=3).pack(side="left", padx=(px(6), 0))
        ttk.Button(bar, text="↓", command=lambda: self.move_selected(1), width=3).pack(side="left", padx=(4, 0))
        ttk.Button(bar, text="Очистить", command=self.clear_steps, width=10).pack(side="left", padx=(px(6), 0))

        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill="both", expand=True, pady=(px(10), 0))
        columns = ("num", "kind", "target", "delay")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=8, selectmode="browse")
        self.tree.heading("num", text="№")
        self.tree.heading("kind", text="Тип")
        self.tree.heading("target", text="Куда кликать")
        self.tree.heading("delay", text="Задержка, мс")
        self.tree.column("num", width=px(44), anchor="center", stretch=False)
        self.tree.column("kind", width=px(90), anchor="center", stretch=False)
        self.tree.column("target", width=px(430), anchor="w")
        self.tree.column("delay", width=px(110), anchor="center", stretch=False)
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", self.edit_selected)
        self.tree.bind("<Delete>", lambda _e: self.delete_selected())

        settings = ttk.LabelFrame(self, text="Настройки", padding=px(10))
        settings.pack(fill="x", pady=(px(10), 0))

        self.var_interval = tk.StringVar(value="500")
        self.var_jitter = tk.StringVar(value="0")
        self.var_repeats = tk.StringVar(value="0")
        self.var_wait = tk.StringVar(value="0")
        self.var_button = tk.StringVar(value="ЛКМ")

        ttk.Label(settings, text="Интервал между шагами, мс:").grid(row=0, column=0, sticky="w")
        ttk.Spinbox(settings, from_=0, to=600000, width=9, textvariable=self.var_interval).grid(
            row=0, column=1, sticky="w", padx=(px(6), px(18)))
        ttk.Label(settings, text="Разброс, ±мс:").grid(row=0, column=2, sticky="w")
        ttk.Spinbox(settings, from_=0, to=60000, width=7, textvariable=self.var_jitter).grid(
            row=0, column=3, sticky="w", padx=(px(6), px(18)))
        ttk.Label(settings, text="Повтор цикла (0 = ∞):").grid(row=0, column=4, sticky="w")
        ttk.Spinbox(settings, from_=0, to=1000000, width=7, textvariable=self.var_repeats).grid(
            row=0, column=5, sticky="w", padx=(px(6), 0))
        ttk.Label(settings, text="Кнопка мыши:").grid(row=1, column=0, sticky="w", pady=(px(8), 0))
        ttk.Combobox(settings, values=list(BUTTONS), width=7, state="readonly",
                     textvariable=self.var_button).grid(
            row=1, column=1, sticky="w", padx=(px(6), px(18)), pady=(px(8), 0))
        ttk.Label(settings, text="Ждать картинку, сек:").grid(row=1, column=2, sticky="w", pady=(px(8), 0))
        ttk.Spinbox(settings, from_=0, to=3600, width=7, textvariable=self.var_wait).grid(
            row=1, column=3, sticky="w", padx=(px(6), px(18)), pady=(px(8), 0))
        ttk.Label(settings, text="Двойной клик по шагу — свои задержка/порог",
                  style="Muted.TLabel").grid(row=1, column=4, columnspan=2, sticky="w", pady=(px(8), 0))

        run_bar = ttk.Frame(self)
        run_bar.pack(fill="x", pady=(px(12), 0))
        self.btn_start = ttk.Button(run_bar, text="▶  Старт (F6)", command=self.toggle_run, width=16)
        self.btn_start.pack(side="left")
        self.btn_stop = ttk.Button(run_bar, text="⏹  Стоп", command=self.stop_run,
                                   width=10, state="disabled")
        self.btn_stop.pack(side="left", padx=(px(8), 0))
        self.status_var = tk.StringVar(value="Готов")
        ttk.Label(run_bar, textvariable=self.status_var).pack(side="left", padx=px(16))

        log_frame, self.log_box = self.make_log(self, height=6)
        log_frame.pack(fill="both", expand=True, pady=(px(10), 0))

    # ------------------------------------------------------------- журнал --
    def log(self, text: str) -> None:
        self.events.put(("log", text))

    def _append_log(self, text: str) -> None:
        self.append_log(self.log_box, text)

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "status":
                    self.status_var.set(payload)
                elif kind == "done":
                    self._on_run_finished()
                elif kind == "toggle":
                    self.toggle_run()
                elif kind == "point_at_cursor":
                    self.add_point_at_cursor()
        except queue.Empty:
            pass
        self.after(80, self._poll)

    # -------------------------------------------------------------- шаги ----
    def refresh_tree(self) -> None:
        selected = self._selected_index()
        self.tree.delete(*self.tree.get_children())
        for i, step in enumerate(self.steps):
            delay = f"{step.delay}" if step.delay else "—"
            self.tree.insert("", "end", iid=str(i),
                             values=(i + 1, KIND_NAMES.get(step.kind, step.kind),
                                     self._describe(step), delay))
        if selected is not None and 0 <= selected < len(self.steps):
            self.tree.selection_set(str(selected))
            self.tree.focus(str(selected))

    @staticmethod
    def _describe(step: Step) -> str:
        if step.kind == "point":
            return f"({step.x}, {step.y})"
        if step.kind == "area":
            return f"({step.x}, {step.y})   {step.w}×{step.h}"
        name = Path(step.path).name or "?"
        return f"{name}   ·   порог {step.conf:.0%}"

    def _selected_index(self) -> int | None:
        selection = self.tree.selection()
        if not selection:
            return None
        try:
            return int(selection[0])
        except ValueError:
            return None

    def _pick(self, mode: str, handler) -> None:
        if not HAS_DEPS:
            messagebox.showerror("AutoClicker", "Не установлены зависимости.\n\n" + DEPS_ERROR)
            return
        top = self.winfo_toplevel()
        top.iconify()

        def start() -> None:
            top.update_idletasks()
            try:
                shot = grab_screen()
            except Exception as exc:  # noqa: BLE001
                top.deiconify()
                self.log(f"Не удалось снять экран: {exc}")
                return

            def done(x: int, y: int, w: int, h: int) -> None:
                top.deiconify()
                top.lift()
                handler(x, y, w, h, shot)

            def cancel() -> None:
                top.deiconify()
                top.lift()

            OverlaySelector(self, mode, done, cancel)

        self.after(300, start)

    # -- добавление шагов ------------------------------------------------- #
    def add_point(self) -> None:
        self._pick("point", self._add_point_done)

    def _add_point_done(self, x: int, y: int, _w: int, _h: int, _shot) -> None:
        self.steps.append(Step(kind="point", x=x, y=y))
        self.refresh_tree()
        self.save_config()
        self.log(f"Добавлена точка ({x}, {y})")

    def add_point_at_cursor(self) -> None:
        if not HAS_DEPS:
            return
        try:
            x, y = pyautogui.position()
        except Exception:  # noqa: BLE001
            return
        self.steps.append(Step(kind="point", x=int(x), y=int(y)))
        self.refresh_tree()
        self.save_config()
        self.log(f"Добавлена точка под курсором ({int(x)}, {int(y)})")

    def add_area(self) -> None:
        self._pick("rect", self._add_area_done)

    def _add_area_done(self, x: int, y: int, w: int, h: int, _shot) -> None:
        if w < 6 or h < 6:
            self.log("Область слишком маленькая — попробуйте ещё раз.")
            return
        self.steps.append(Step(kind="area", x=x, y=y, w=w, h=h))
        self.refresh_tree()
        self.save_config()
        self.log(f"Добавлена область {w}×{h} в ({x}, {y})")

    def add_image(self) -> None:
        self._pick("rect", self._add_image_done)

    def _add_image_done(self, x: int, y: int, w: int, h: int, shot) -> None:
        if w < 6 or h < 6:
            self.log("Слишком маленький фрагмент — выделите кнопку/иконку целиком.")
            return
        TARGETS_DIR.mkdir(parents=True, exist_ok=True)
        name = f"target_{time.strftime('%Y%m%d_%H%M%S')}.png"
        path = TARGETS_DIR / name
        shot.crop((x - self._vx(), y - self._vy(),
                   x - self._vx() + w, y - self._vy() + h)).save(path)
        self.steps.append(Step(kind="image", x=x, y=y, w=w, h=h,
                               path=str(path), conf=0.80))
        self.refresh_tree()
        self.save_config()
        self.log(f"Добавлена картинка {name} ({w}×{h})")

    @staticmethod
    def _vx() -> int:
        return virtual_screen()[0]

    @staticmethod
    def _vy() -> int:
        return virtual_screen()[1]

    # -- правка ----------------------------------------------------------- #
    def edit_selected(self, _event=None) -> None:
        idx = self._selected_index()
        if idx is None:
            return
        step = self.steps[idx]
        win = tk.Toplevel(self)
        win.title("Параметры шага")
        win.configure(bg=BG)
        win.transient(self.winfo_toplevel())
        win.resizable(False, False)

        pad = {"padx": px(10), "pady": px(6)}
        ttk.Label(win, text="Задержка после шага, мс (0 — общий интервал):").grid(
            row=0, column=0, sticky="w", **pad)
        var_delay = tk.StringVar(value=str(step.delay))
        ttk.Entry(win, textvariable=var_delay, width=12).grid(row=0, column=1, **pad)

        var_conf = var_wait = None
        if step.kind == "image":
            ttk.Label(win, text="Порог совпадения, %:").grid(row=1, column=0, sticky="w", **pad)
            var_conf = tk.StringVar(value=str(int(step.conf * 100)))
            ttk.Spinbox(win, from_=50, to=100, textvariable=var_conf,
                        width=10).grid(row=1, column=1, sticky="w", **pad)
            ttk.Label(win, text="Ждать появления, сек (0 — общее):").grid(
                row=2, column=0, sticky="w", **pad)
            var_wait = tk.StringVar(value=str(step.wait))
            ttk.Entry(win, textvariable=var_wait, width=12).grid(row=2, column=1, sticky="w", **pad)

        def save() -> None:
            try:
                step.delay = max(0, int(float(var_delay.get())))
            except ValueError:
                pass
            if var_conf is not None:
                try:
                    step.conf = min(1.0, max(0.30, int(float(var_conf.get())) / 100))
                except ValueError:
                    pass
            if var_wait is not None:
                try:
                    step.wait = max(0.0, float(var_wait.get()))
                except ValueError:
                    pass
            win.destroy()
            self.refresh_tree()
            self.save_config()

        buttons = ttk.Frame(win)
        buttons.grid(row=10, column=0, columnspan=2, sticky="e", padx=px(10), pady=(px(10), px(10)))
        flat_button(buttons, "Сохранить", save).pack(side="left")
        flat_button(buttons, "Отмена", win.destroy, kind="ghost").pack(side="left", padx=(px(8), 0))
        win.bind("<Return>", lambda _e: save())
        win.bind("<Escape>", lambda _e: win.destroy())
        win.update_idletasks()
        enable_dark_titlebar(win)
        win.grab_set()

    def delete_selected(self) -> None:
        idx = self._selected_index()
        if idx is None:
            return
        del self.steps[idx]
        self.refresh_tree()
        self.save_config()

    def clear_steps(self) -> None:
        if not self.steps:
            return
        if messagebox.askyesno("AutoClicker", "Удалить все шаги?"):
            self.steps.clear()
            self.refresh_tree()
            self.save_config()

    def move_selected(self, delta: int) -> None:
        idx = self._selected_index()
        if idx is None:
            return
        new = idx + delta
        if not 0 <= new < len(self.steps):
            return
        self.steps[idx], self.steps[new] = self.steps[new], self.steps[idx]
        self.refresh_tree()
        self.tree.selection_set(str(new))
        self.tree.focus(str(new))
        self.save_config()

    # --------------------------------------------------------- настройки ----
    @staticmethod
    def _to_int(value: str, default: int, low: int, high: int) -> int:
        try:
            return max(low, min(high, int(float(value))))
        except (TypeError, ValueError):
            return default

    def read_settings(self) -> dict:
        try:
            wait = max(0.0, float(self.var_wait.get()))
        except ValueError:
            wait = 0.0
        return {
            "interval": self._to_int(self.var_interval.get(), 500, 0, 3_600_000),
            "jitter": self._to_int(self.var_jitter.get(), 0, 0, 600_000),
            "repeats": self._to_int(self.var_repeats.get(), 0, 0, 10_000_000),
            "wait": wait,
            "button": BUTTONS.get(self.var_button.get(), "left"),
        }

    # --------------------------------------------------------- конфиг ------ #
    def save_config(self) -> None:
        data = config_read()
        data["settings"] = {
            "interval": self.var_interval.get(),
            "jitter": self.var_jitter.get(),
            "repeats": self.var_repeats.get(),
            "wait": self.var_wait.get(),
            "button": self.var_button.get(),
        }
        data["steps"] = [self._step_to_dict(s) for s in self.steps]
        try:
            config_write(data)
        except OSError as exc:
            self.log(f"Не удалось сохранить настройки: {exc}")

    def _step_to_dict(self, step: Step) -> dict:
        data = {
            "kind": step.kind, "x": step.x, "y": step.y, "w": step.w, "h": step.h,
            "conf": step.conf, "delay": step.delay, "wait": step.wait,
        }
        if step.path:
            path = Path(step.path)
            try:
                data["path"] = str(path.resolve().relative_to(APP_DIR))
            except (ValueError, OSError):
                data["path"] = str(path)
        return data

    def load_config(self) -> None:
        if not CONFIG_PATH.exists():
            return
        try:
            data = json.loads(CONFIG_PATH.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            self.log(f"Настройки не загружены: {exc}")
            return
        settings = data.get("settings", {}) or {}
        self.var_interval.set(str(settings.get("interval", "500")))
        self.var_jitter.set(str(settings.get("jitter", "0")))
        self.var_repeats.set(str(settings.get("repeats", "0")))
        self.var_wait.set(str(settings.get("wait", "0")))
        button = str(settings.get("button", "ЛКМ"))
        self.var_button.set(button if button in BUTTONS else "ЛКМ")

        for raw in data.get("steps", []) or []:
            try:
                kind = raw.get("kind")
                if kind not in KIND_NAMES:
                    continue
                path = str(raw.get("path", "") or "")
                if path and not Path(path).is_absolute():
                    path = str((APP_DIR / path).resolve())
                self.steps.append(Step(
                    kind=kind,
                    x=int(raw.get("x", 0)), y=int(raw.get("y", 0)),
                    w=int(raw.get("w", 0)), h=int(raw.get("h", 0)),
                    path=path,
                    conf=float(raw.get("conf", 0.80)),
                    delay=int(raw.get("delay", 0)),
                    wait=float(raw.get("wait", 0)),
                ))
            except (TypeError, ValueError):
                continue
        self.refresh_tree()

    # ------------------------------------------------------- горячие клавиши -
    def _setup_hotkeys(self) -> None:
        if not HAS_KEYBOARD or not HAS_DEPS:
            return
        try:
            keyboard.add_hotkey("f6", lambda: self.events.put(("toggle", None)))
            keyboard.add_hotkey("f7", lambda: self.events.put(("point_at_cursor", None)))
            self.log("F6 — старт/стоп, F7 — добавить точку под курсором.")
        except Exception as exc:  # noqa: BLE001
            self.log(f"Горячие клавиши не включились: {exc}")

    # ------------------------------------------------------------- запуск ---
    def toggle_run(self) -> None:
        if self.running:
            self.stop_run()
        else:
            self.start_run()

    def start_run(self) -> None:
        if self.running:
            return
        if not HAS_DEPS:
            messagebox.showerror(
                "AutoClicker",
                "Не установлены зависимости.\n\nВыполните в папке программы:\n\n"
                "pip install -r requirements.txt\n\n" + DEPS_ERROR,
            )
            return
        if not self.steps:
            messagebox.showinfo("AutoClicker", "Добавьте хотя бы один шаг: точку, область или картинку.")
            return
        missing = [s for s in self.steps if s.kind == "image" and not Path(s.path).exists()]
        if missing:
            messagebox.showerror(
                "AutoClicker",
                "Не найден файл картинки:\n" + "\n".join(Path(s.path).name for s in missing)
                + "\n\nУдалите этот шаг или добавьте картинку заново.",
            )
            return

        cfg = self.read_settings()
        self.stop_evt.clear()
        self.running = True
        self.btn_start.configure(state="disabled")
        self.btn_stop.configure(state="normal")
        self.status_var.set("Работаю…")
        self.log("▶ старт")
        steps = [replace(s) for s in self.steps]
        threading.Thread(target=self._run, args=(steps, cfg), daemon=True).start()

    def stop_run(self) -> None:
        if not self.running:
            return
        self.stop_evt.set()
        self.status_var.set("Останавливаюсь…")
        self.log("⏹ запрошена остановка")

    def _on_run_finished(self) -> None:
        self.running = False
        self.btn_start.configure(state="normal")
        self.btn_stop.configure(state="disabled")
        self.status_var.set("Готов")

    def _run(self, steps: list[Step], cfg: dict) -> None:
        loop = 0
        total = len(steps)
        try:
            while not self.stop_evt.is_set():
                loop += 1
                for idx, step in enumerate(steps, 1):
                    if self.stop_evt.is_set():
                        break
                    self.events.put(("status", f"Цикл {loop} · шаг {idx}/{total}"))
                    try:
                        self._do_step(step, cfg)
                    except pyautogui.FailSafeException:
                        raise
                    except Exception as exc:  # noqa: BLE001
                        self.log(f"Ошибка на шаге {idx}: {exc}")
                    if self.stop_evt.is_set():
                        break
                    delay = step.delay if step.delay > 0 else cfg["interval"]
                    if cfg["jitter"] > 0:
                        delay += random.randint(-cfg["jitter"], cfg["jitter"])
                    if delay > 0:
                        self.stop_evt.wait(delay / 1000.0)
                if cfg["repeats"] and loop >= cfg["repeats"]:
                    self.log(f"Готово: выполнено циклов — {loop}")
                    break
        except pyautogui.FailSafeException:
            self.log("Аварийная остановка: курсор уведён в левый верхний угол.")
        except Exception:  # noqa: BLE001
            self.log("Сбой: " + traceback.format_exc(limit=2).replace("\n", " | "))
        finally:
            self.events.put(("done", None))

    def _do_step(self, step: Step, cfg: dict) -> None:
        button = cfg["button"]
        if step.kind in ("point", "area"):
            x, y = step.x, step.y
            if step.kind == "area" and step.w > 1 and step.h > 1:
                x = random.randint(step.x, step.x + step.w - 1)
                y = random.randint(step.y, step.y + step.h - 1)
            pyautogui.click(x, y, button=button)
            self.log(f"клик ({x}, {y})")
            return

        template = self._template(step.path)
        if template is None:
            self.log(f"картинка недоступна: {Path(step.path).name}")
            return

        wait = step.wait if step.wait > 0 else cfg["wait"]
        deadline = time.time() + wait
        vx, vy = self._vx(), self._vy()
        while not self.stop_evt.is_set():
            screen = np.array(grab_screen())
            found = find_template(screen, template, step.conf)
            if found is not None:
                cx, cy, _w, _h, score = found
                if abs(vx) or abs(vy):
                    cx, cy = cx + vx, cy + vy
                pyautogui.click(cx, cy, button=button)
                self.log(f"найдено {Path(step.path).name} ({score:.0%}) → клик ({cx}, {cy})")
                return
            if time.time() >= deadline:
                self.log(f"не найдено: {Path(step.path).name}")
                return
            if self.stop_evt.wait(0.25):
                return

    def _template(self, path: str) -> "np.ndarray | None":
        file = Path(path)
        try:
            mtime = file.stat().st_mtime
        except OSError:
            return None
        key = (str(file), mtime)
        cached = self.tpl_cache.get(key)
        if cached is not None:
            return cached
        try:
            image = Image.open(file).convert("RGB")
        except Exception:  # noqa: BLE001
            return None
        array = np.array(image)
        self.tpl_cache = {key: array}
        return array

    # ------------------------------------------------------------- выход ----
    def shutdown(self) -> None:
        self.save_config()
        self.stop_evt.set()
        if HAS_KEYBOARD:
            try:
                keyboard.unhook_all_hotkeys()
            except Exception:  # noqa: BLE001
                pass

# --------------------------------------------------------------------------- #
#  Панель: пустые файлы и папки
# --------------------------------------------------------------------------- #
class CleanPanel(Panel):
    LIMIT = 2000

    def __init__(self, master: tk.Misc, hub) -> None:
        super().__init__(master, hub)
        self.plan: Plan | None = None
        self.busy = False
        self._build()

    def _build(self) -> None:
        row = ttk.Frame(self)
        row.pack(fill="x")
        ttk.Label(row, text="Папка").pack(side="left")
        self.folder_var = tk.StringVar()
        self.entry = ttk.Entry(row, textvariable=self.folder_var)
        self.entry.pack(side="left", fill="x", expand=True, padx=(px(10), px(8)))
        self.entry.bind("<Return>", lambda _event: self.start_scan())
        self.browse_btn = flat_button(row, "Обзор…", self.choose_folder, kind="ghost")
        self.browse_btn.pack(side="left")

        options = ttk.Frame(self)
        options.pack(fill="x", pady=(px(12), 0))
        self.files_var = tk.BooleanVar(value=True)
        self.dirs_var = tk.BooleanVar(value=True)
        self.hidden_var = tk.BooleanVar(value=False)
        for text, variable, pad in (
            ("Пустые файлы (0 байт)", self.files_var, 0),
            ("Пустые папки", self.dirs_var, px(18)),
            ("Учитывать скрытые", self.hidden_var, px(18)),
        ):
            dark_check(options, text, variable).pack(side="left", padx=(pad, 0))

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(px(14), 0))
        self.scan_btn = flat_button(actions, "Проверить", self.start_scan)
        self.scan_btn.pack(side="left")
        self.delete_btn = flat_button(actions, "Удалить найденное", self.start_delete,
                                      kind="danger", state="disabled")
        self.delete_btn.pack(side="left", padx=(px(8), 0))

        self.summary_var = tk.StringVar(value="Выберите папку и нажмите «Проверить».")
        ttk.Label(self, textvariable=self.summary_var, style="Muted.TLabel").pack(
            anchor="w", pady=(px(12), px(6)))

        wrap = ttk.Frame(self)
        wrap.pack(fill="both", expand=True)
        self.list = dark_text(wrap, height=14, wrap="none")
        scroll_y = ttk.Scrollbar(wrap, orient="vertical", command=self.list.yview)
        scroll_x = ttk.Scrollbar(wrap, orient="horizontal", command=self.list.xview)
        self.list.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        self.list.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")
        wrap.rowconfigure(0, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.list.tag_configure("file", foreground="#9aa4b2")
        self.list.tag_configure("dir", foreground="#7aa2ff")
        self.list.tag_configure("error", foreground="#ff6b6b")
        self.list.tag_configure("head", foreground=FG, font=("Consolas", 9, "bold"))
        self.list.tag_configure("hint", foreground=MUTED)
        self._write("Список пуст. Выберите папку и нажмите «Проверить».\n", "hint")

    # -- помощники --------------------------------------------------------- #
    def _write(self, text: str, tag: str | None = None) -> None:
        self.list.configure(state="normal")
        self.list.insert("end", text, (tag,) if tag else ())
        self.list.configure(state="disabled")

    def _clear_list(self) -> None:
        self.list.configure(state="normal")
        self.list.delete("1.0", "end")
        self.list.configure(state="disabled")

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        self.entry.configure(state=state)
        self.browse_btn.configure(state=state)
        self.scan_btn.configure(state=state)
        if busy or self.plan is None or self.plan.is_empty:
            self.delete_btn.configure(state="disabled")
        else:
            self.delete_btn.configure(state="normal")

    def _fail(self, exc: Exception) -> None:
        self._set_busy(False)
        self.summary_var.set("Не удалось выполнить операцию")
        self.hub.set_status("Ошибка")
        messagebox.showerror("Пустые файлы", str(exc))

    def choose_folder(self) -> None:
        initial = self.folder_var.get().strip()
        if not (initial and os.path.isdir(initial)):
            initial = str(Path.home())
        chosen = filedialog.askdirectory(
            title="Выберите папку для очистки", initialdir=initial, mustexist=True
        )
        if chosen:
            self.folder_var.set(os.path.normpath(chosen))
            self.start_scan()

    def _current_folder(self) -> str | None:
        raw = self.folder_var.get().strip().strip('"')
        if not raw:
            messagebox.showinfo("Пустые файлы", "Сначала выберите папку.")
            return None
        root = os.path.abspath(os.path.expanduser(raw))
        if not os.path.isdir(root):
            messagebox.showerror("Пустые файлы", f"Папка не найдена:\n{root}")
            return None
        blocked = check_blocked(root)
        if blocked:
            messagebox.showerror("Пустые файлы", blocked)
            return None
        return root

    # -- поиск ------------------------------------------------------------- #
    def start_scan(self) -> None:
        if self.busy:
            return
        root = self._current_folder()
        if root is None:
            return
        files, dirs, hidden = self.files_var.get(), self.dirs_var.get(), self.hidden_var.get()
        if not files and not dirs:
            messagebox.showinfo("Пустые файлы", "Отметьте, что искать: файлы и/или папки.")
            return

        self.plan = None
        self._set_busy(True)
        self._clear_list()
        self.summary_var.set("Ищу пустые файлы и папки…")
        self.hub.set_status(f"Сканирую: {root}")
        self.hub.run_bg(
            lambda: scan_empty(root, files=files, dirs=dirs, hidden=hidden),
            self._scan_done,
            self._fail,
        )

    def _scan_done(self, plan: Plan) -> None:
        self.plan = plan
        self._set_busy(False)
        self._clear_list()
        self._write(f"Папка: {plan.root}\n", "head")

        if plan.is_empty:
            self.summary_var.set("Пустых файлов и папок не найдено.")
            self.hub.set_status("Ничего удалять не нужно")
            self._write("Пустых файлов и папок не найдено.\n", "head")
            if plan.errors:
                self._write("\nНе удалось прочитать:\n", "error")
                for path, reason in plan.errors:
                    self._write(f"  {path} — {reason}\n", "error")
            return

        shown = 0
        for path in plan.files:
            if shown >= self.LIMIT:
                break
            self._write(f"[файл]  {path}\n", "file")
            shown += 1
        for path in plan.dirs:
            if shown >= self.LIMIT:
                break
            self._write(f"[папка] {path}\n", "dir")
            shown += 1
        if shown < plan.total:
            self._write(f"\n… и ещё {plan.total - shown} (список сокращён)\n", "head")
        if plan.errors:
            self._write("\nНе удалось прочитать:\n", "error")
            for path, reason in plan.errors:
                self._write(f"  {path} — {reason}\n", "error")

        self.summary_var.set(
            f"Найдено: файлов — {len(plan.files)}, папок — {len(plan.dirs)}."
        )
        self.hub.set_status("Проверьте список и нажмите «Удалить найденное»")

    # -- удаление ---------------------------------------------------------- #
    def start_delete(self) -> None:
        plan = self.plan
        if self.busy or plan is None or plan.is_empty:
            return
        message = (
            "Удалить безвозвратно?\n\n"
            f"Пустых файлов: {len(plan.files)}\n"
            f"Пустых папок: {len(plan.dirs)}\n\n"
            f"Папка: {plan.root}"
        )
        warning = risk_warning(plan.root)
        if warning:
            message += f"\n\n{warning}"
        if not messagebox.askyesno("Удалить безвозвратно?", message):
            return

        self._set_busy(True)
        self._clear_list()
        self.hub.set_status("Удаляю…")
        self.hub.run_bg(lambda: delete_plan(plan), self._delete_done, self._fail)

    def _delete_done(self, result: DeleteResult) -> None:
        self.plan = None
        self._set_busy(False)
        self._clear_list()
        self._write("Готово.\n", "head")
        self._write(f"Удалено файлов: {result.removed_files}\n")
        self._write(f"Удалено папок:  {result.removed_dirs}\n")
        if result.errors:
            self._write("\nНе удалось удалить:\n", "error")
            for path, reason in result.errors:
                self._write(f"  {path} — {reason}\n", "error")
        self.summary_var.set(
            f"Удалено: файлов {result.removed_files}, папок {result.removed_dirs}"
            + (f". Ошибок: {len(result.errors)}" if result.errors else ".")
        )
        self.hub.set_status("Готово")
        if result.errors:
            messagebox.showwarning(
                "Пустые файлы",
                f"Удалено файлов: {result.removed_files}, папок: {result.removed_dirs}.\n"
                f"Не удалось удалить: {len(result.errors)} (подробности в списке).",
            )
        else:
            messagebox.showinfo(
                "Пустые файлы",
                f"Готово.\nУдалено файлов: {result.removed_files}, папок: {result.removed_dirs}.",
            )

# --------------------------------------------------------------------------- #
#  Панель: DNS
# --------------------------------------------------------------------------- #
class DnsPanel(Panel):
    def __init__(self, master: tk.Misc, hub) -> None:
        super().__init__(master, hub)
        self.busy = False
        self.action_buttons: list[tk.Button] = []
        self._build()
        self.refresh_state()

    def _build(self) -> None:
        card = tk.Frame(self, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="x")
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=px(16), pady=px(14))

        head = tk.Frame(inner, bg=CARD)
        head.pack(fill="x")
        tk.Label(head, text="Текущее состояние", bg=CARD, fg=FG,
                 font=("Segoe UI", 10, "bold")).pack(side="left")
        self.refresh_btn = flat_button(head, "Обновить", self.refresh_state, kind="ghost",
                                       padx=px(10), pady=px(4))
        self.refresh_btn.pack(side="right")

        self.adapter_var = tk.StringVar(value="—")
        self.dns_var = tk.StringVar(value="—")
        self.tunnel_var = tk.StringVar(value="—")
        for title, variable in (
            ("Адаптер:", self.adapter_var),
            ("DNS сейчас:", self.dns_var),
            ("Туннель:", self.tunnel_var),
        ):
            row = tk.Frame(inner, bg=CARD)
            row.pack(fill="x", pady=(px(8) if title == "Адаптер:" else px(3), 0))
            tk.Label(row, text=title, bg=CARD, fg=MUTED, width=12, anchor="w",
                     font=("Segoe UI", 9)).pack(side="left")
            tk.Label(row, textvariable=variable, bg=CARD, fg=FG, anchor="w",
                     font=("Segoe UI", 9)).pack(side="left")

        buttons_one = ttk.Frame(self)
        buttons_one.pack(fill="x", pady=(px(14), px(6)))
        buttons_two = ttk.Frame(self)
        buttons_two.pack(fill="x")

        for row, items in (
            (buttons_one, (
                ("Quad9 + DoH", lambda: self._apply(QUAD9_DNS, QUAD9_DOH, "Quad9")),
                ("Xbox + DoH", lambda: self._apply(XBOX_DNS, XBOX_DOH, "Xbox")),
                ("DHCP", self._dhcp),
            )),
            (buttons_two, (
                ("Quad9 + очистить туннель", self._quad9_and_tunnel),
                ("Вернуть DNS туннеля", self._restore_tunnel),
                ("Проверить DNS", self._test_dns),
            )),
        ):
            for index, (text, command) in enumerate(items):
                button = flat_button(
                    row, text, command,
                    kind="primary" if index == 0 else "ghost",
                )
                button.pack(side="left", padx=(0 if index == 0 else px(8), 0))
                self.action_buttons.append(button)

        ttk.Label(
            self,
            text="Профиль Xbox блокирует workbuddy.ai — для него используйте Quad9.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(px(12), 0))

        log_frame, self.log_box = self.make_log(self, height=10)
        log_frame.pack(fill="both", expand=True, pady=(px(10), 0))
        self.append_log(self.log_box, "Здесь появятся результаты операций.")

    # -- состояние --------------------------------------------------------- #
    def refresh_state(self) -> None:
        self.refresh_btn.configure(state="disabled")
        self.hub.run_bg(dns_state, self._state_done, self._state_error)

    def _state_done(self, state: DnsState) -> None:
        self.refresh_btn.configure(state="normal")
        self.adapter_var.set(f"{state.name} (ifIndex {state.index})" if state.name else "не найден")
        self.dns_var.set(state.dns or "автоматически (DHCP)")
        if state.tunnel:
            self.tunnel_var.set(f"{state.tunnel} · DNS {state.tunnel_dns or 'авто'}")
        else:
            self.tunnel_var.set("не найден")

    def _state_error(self, exc: Exception) -> None:
        self.refresh_btn.configure(state="normal")
        self.adapter_var.set("—")
        self.dns_var.set("—")
        self.tunnel_var.set("—")
        self.append_log(self.log_box, f"Не удалось получить состояние: {exc}", "error")

    # -- действия ---------------------------------------------------------- #
    def _guard(self) -> bool:
        if self.busy:
            return False
        if not self.hub.ensure_admin("dns"):
            return False
        return True

    def _apply(self, servers: list[str], template: str, label: str) -> None:
        if not self._guard():
            return
        self._run(f"Применяю профиль {label}…", lambda: dns_apply(servers, template))

    def _quad9_and_tunnel(self) -> None:
        if not self._guard():
            return

        def work() -> str:
            first = dns_apply(QUAD9_DNS, QUAD9_DOH)
            second = dns_clear_tunnel()
            return first + "\n" + second

        self._run("Quad9 + очистка DNS туннеля…", work)

    def _dhcp(self) -> None:
        if not self._guard():
            return
        self._run("Сбрасываю DNS на DHCP…", dns_dhcp)

    def _restore_tunnel(self) -> None:
        if not self._guard():
            return
        self._run("Возвращаю DNS туннеля…", dns_restore_tunnel)

    def _test_dns(self) -> None:
        if self.busy:
            return
        self._run("Проверяю разрешение имён…", dns_test)

    def _run(self, title: str, work) -> None:
        self.busy = True
        self._set_buttons(False)
        self.append_log(self.log_box, title)
        self.hub.set_status(title)
        self.hub.run_bg(work, self._action_done, self._action_error)

    def _action_done(self, output: str) -> None:
        self.busy = False
        self._set_buttons(True)
        for line in str(output).splitlines():
            if line.strip():
                self.append_log(self.log_box, line.strip())
        self.hub.set_status("DNS: готово")
        self.refresh_state()

    def _action_error(self, exc: Exception) -> None:
        self.busy = False
        self._set_buttons(True)
        self.append_log(self.log_box, f"Ошибка: {exc}", "error")
        self.hub.set_status("DNS: ошибка")
        messagebox.showerror("DNS", str(exc))

    def _set_buttons(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        for button in self.action_buttons:
            button.configure(state=state)

# --------------------------------------------------------------------------- #
#  Панель: очистка Windows
# --------------------------------------------------------------------------- #
class CleanupPanel(Panel):
    def __init__(self, master: tk.Misc, hub) -> None:
        super().__init__(master, hub)
        self.targets = build_cleanup_targets()
        self.vars: dict[str, tk.BooleanVar] = {}
        self.size_vars: dict[str, tk.StringVar] = {}
        self.checkbuttons: dict[str, tk.Checkbutton] = {}
        self.sizes: dict[str, int | None] = {}
        self.busy = False
        self._build()
        self.refresh_sizes()

    def _build(self) -> None:
        card = tk.Frame(self, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="x")
        grid = tk.Frame(card, bg=CARD)
        grid.pack(fill="x", padx=px(16), pady=px(12))
        for column in range(2):
            grid.columnconfigure(column, weight=1)

        for index, target in enumerate(self.targets):
            row, column = index % 7, index // 7
            cell = tk.Frame(grid, bg=CARD)
            cell.grid(row=row, column=column, sticky="ew", padx=(0, px(26)), pady=px(2))
            variable = tk.BooleanVar(value=target.special not in ("recycle", "old"))
            self.vars[target.key] = variable
            check = dark_check(cell, target.label, variable, bg=CARD, command=self._update_total)
            check.pack(side="left")
            self.checkbuttons[target.key] = check
            size_var = tk.StringVar(value="…")
            self.size_vars[target.key] = size_var
            tk.Label(cell, textvariable=size_var, bg=CARD, fg=MUTED,
                     font=("Segoe UI", 8)).pack(side="right")

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(px(14), 0))
        self.refresh_btn = flat_button(actions, "Пересчитать размеры", self.refresh_sizes,
                                       kind="ghost")
        self.refresh_btn.pack(side="left")
        self.clean_btn = flat_button(actions, "Очистить выбранное", self.start_clean,
                                     kind="danger")
        self.clean_btn.pack(side="left", padx=(px(8), 0))
        self.total_var = tk.StringVar(value="Выбрано: —")
        ttk.Label(actions, textvariable=self.total_var, style="Muted.TLabel").pack(side="right")

        ttk.Label(
            self,
            text="Личные файлы не затрагиваются. Корзина и Windows.old удаляются безвозвратно.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(px(12), 0))

        log_frame, self.log_box = self.make_log(self, height=9)
        log_frame.pack(fill="both", expand=True, pady=(px(10), 0))
        self.append_log(self.log_box, "Здесь появится журнал очистки.")

    # -- размеры ----------------------------------------------------------- #
    def _available(self, target: CleanupTarget) -> bool:
        if target.special == "recycle":
            return True
        return any(path.exists() for path in target.paths)

    def refresh_sizes(self) -> None:
        if self.busy:
            return
        self.busy = True
        self._set_buttons(False)
        for variable in self.size_vars.values():
            variable.set("…")

        def work() -> dict[str, int | None]:
            sizes: dict[str, int | None] = {}
            for target in self.targets:
                if target.special == "recycle":
                    sizes[target.key] = None
                elif not self._available(target):
                    sizes[target.key] = -1
                else:
                    sizes[target.key] = sum(dir_size(path) for path in target.paths if path.exists())
            return sizes

        self.hub.run_bg(work, self._sizes_done, self._fail)

    def _sizes_done(self, sizes: dict[str, int | None]) -> None:
        self.busy = False
        self.sizes = sizes
        for target in self.targets:
            size = sizes.get(target.key)
            if size == -1:
                self.size_vars[target.key].set("нет")
            else:
                self.size_vars[target.key].set(fmt_size(size))
        self._set_buttons(True)
        self._update_total()
        self.hub.set_status("Размеры обновлены")

    def _set_buttons(self, enabled: bool) -> None:
        state = "normal" if enabled else "disabled"
        self.refresh_btn.configure(state=state)
        self.clean_btn.configure(state=state)
        for target in self.targets:
            check = self.checkbuttons[target.key]
            if self._available(target):
                check.configure(state=state, fg=FG if enabled else MUTED)
            else:
                check.configure(state="disabled", fg=MUTED)

    def _selected(self) -> list[CleanupTarget]:
        return [
            target for target in self.targets
            if self.vars[target.key].get() and self._available(target)
        ]

    def _update_total(self) -> None:
        if self.busy:
            return
        total = 0
        recycle = False
        for target in self._selected():
            if target.special == "recycle":
                recycle = True
                continue
            size = self.sizes.get(target.key)
            if size and size > 0:
                total += size
        text = f"Выбрано: {fmt_size(total)}"
        if recycle:
            text += " + корзина"
        self.total_var.set(text)

    # -- очистка ----------------------------------------------------------- #
    def start_clean(self) -> None:
        if self.busy:
            return
        if not self.hub.ensure_admin("cleanup"):
            return
        selected = self._selected()
        if not selected:
            messagebox.showinfo("Очистка Windows", "Отметьте, что очищать.")
            return
        total = 0
        recycle = False
        lines = []
        for target in selected:
            lines.append(f"• {target.label}")
            if target.special == "recycle":
                recycle = True
            else:
                size = self.sizes.get(target.key)
                if size and size > 0:
                    total += size
        message = (
            "Будет очищено:\n\n" + "\n".join(lines)
            + f"\n\nПримерно {fmt_size(total)}" + (" + корзина" if recycle else "")
            + "\n\nУдаление безвозвратно. Продолжить?"
        )
        if not messagebox.askyesno("Очистка Windows", message):
            return

        self.busy = True
        self._set_buttons(False)
        self.append_log(self.log_box, "Начинаю очистку…")
        self.hub.set_status("Очистка Windows…")
        self.hub.run_bg(lambda: self._clean_worker(selected), self._clean_done, self._fail)

    def _clean_worker(self, selected: list[CleanupTarget]) -> tuple[int, int]:
        freed_total = 0
        failures = 0
        for target in selected:
            self.hub.post(lambda label=target.label: self.append_log(self.log_box, f"• {label}"))

            if target.special == "recycle":
                ok = empty_recycle_bin()
                if not ok:
                    failures += 1
                self.hub.post(lambda ok=ok: self.append_log(
                    self.log_box,
                    "Корзина очищена" if ok else "Не удалось очистить корзину",
                    None if ok else "error",
                ))
                continue

            if target.special == "old":
                size = dir_size(target.paths[0])
                ok = delete_windows_old()
                if ok:
                    freed_total += size
                else:
                    failures += 1
                self.hub.post(lambda ok=ok, size=size: self.append_log(
                    self.log_box,
                    f"Windows.old: {fmt_size(size)}" if ok else "Windows.old: удалить не удалось",
                    None if ok else "error",
                ))
                continue

            freed, errors = purge_paths(target.paths)
            freed_total += freed
            failures += 1 if errors else 0
            suffix = f", ошибок: {errors}" if errors else ""
            self.hub.post(lambda freed=freed, suffix=suffix: self.append_log(
                self.log_box, f"Освобождено: {fmt_size(freed)}{suffix}"
            ))

        return freed_total, failures

    def _clean_done(self, result: tuple[int, int]) -> None:
        freed, failures = result
        self.busy = False
        self.append_log(self.log_box, f"Готово. Освобождено: {fmt_size(freed)}")
        if failures:
            self.append_log(self.log_box, f"Замечаний: {failures} (файлы заняты или нет прав)", "error")
        space = disk_space()
        if space is not None:
            free, total = space
            self.append_log(self.log_box, f"Системный диск: свободно {fmt_size(free)} из {fmt_size(total)}")
        self.hub.set_status(f"Очистка Windows: освобождено {fmt_size(freed)}")
        self.refresh_sizes()

    def _fail(self, exc: Exception) -> None:
        self.busy = False
        self._set_buttons(True)
        self.append_log(self.log_box, f"Ошибка: {exc}", "error")
        self.hub.set_status("Очистка Windows: ошибка")
        messagebox.showerror("Очистка Windows", str(exc))

# --------------------------------------------------------------------------- #
#  GitHub: движок (gh + git) и Bypass: HTTP-клиент
# --------------------------------------------------------------------------- #
GITHUB_DEFAULT_ROOT = r"C:\Users\admin\WorkBuddy AI"
BYPASS_DEFAULT_ROOT = r"C:\Users\admin\WorkBuddy AI\2026-10-17-10-12-49\bypass-manager"
BYPASS_URL = "http://127.0.0.1:8790"

GITHUB_IGNORE_BLOCK = """\
# --- добавлено «Мои инструменты» ---
.workbuddy-ai/
.env
*.env
node_modules/
venv/
.venv/
__pycache__/
*.pyc
*.bin
*.onnx
*.pt
*.ckpt
*.pth
models/
**/models--*/
*.log
.DS_Store
output/
*.wav
*.mp4
*.mov
*.avi
*.mkv
*.exe
*.dll
*.so
*.dylib
ffmpeg*/
"""


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


def gh_authed() -> bool:
    gh = find_tool("gh")
    if not gh:
        return False
    return run_tool([gh, "auth", "status"], timeout=30)[0] == 0


def gh_login() -> str:
    gh = find_tool("gh")
    if not gh:
        return ""
    rc, out, _ = run_tool([gh, "api", "user", "-q", ".login"], timeout=30)
    return out if rc == 0 else ""


def gh_token() -> str:
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""
    if token:
        return token
    gh = find_tool("gh")
    if not gh:
        return ""
    rc, out, _ = run_tool([gh, "auth", "token"], timeout=30)
    return out if rc == 0 else ""


def github_repos() -> list[dict]:
    gh = find_tool("gh")
    if not gh:
        raise RuntimeError("GitHub CLI (gh) не найден. Установите с cli.github.com.")
    rc, out, err = run_tool(
        [gh, "repo", "list", "--limit", "300", "--json", "name,nameWithOwner,url,isPrivate"],
        timeout=120,
    )
    if rc != 0:
        raise RuntimeError(err or out or "не удалось получить список репозиториев")
    try:
        data = json.loads(out)
    except ValueError:
        raise RuntimeError("gh вернул неожиданный ответ")
    repos = []
    for item in data:
        name = str(item.get("name") or "")
        full = str(item.get("nameWithOwner") or "")
        if not name or not full:
            continue
        repos.append({
            "name": name,
            "full": full,
            "url": str(item.get("url") or ""),
            "private": bool(item.get("isPrivate")),
        })
    repos.sort(key=lambda item: item["name"].lower())
    return repos


def repo_name_from_url(url: str) -> str:
    if not url:
        return ""
    text = url.split("?")[0].rstrip("/")
    if text.endswith(".git"):
        text = text[:-4]
    if "@" in text and ":" in text:
        text = text.split(":", 1)[1]
    elif "://" in text:
        rest = text.split("://", 1)[1]
        text = rest.split("/", 1)[1] if "/" in rest else ""
    return text.split("/")[-1]


def git_remote_url(folder: str) -> str:
    git = find_tool("git")
    if not git:
        return ""
    rc, out, _ = run_tool([git, "config", "--get", "remote.origin.url"], cwd=folder, timeout=30)
    return out if rc == 0 else ""


def scan_local_repos(root: str, max_depth: int = 4) -> dict[str, str]:
    """repo_name (в нижнем регистре) -> локальная папка."""
    found: dict[str, str] = {}
    start = Path(root)
    if not start.is_dir():
        return found
    skip = {".git", "node_modules", "__pycache__", ".venv", "venv", ".workbuddy-ai"}
    stack: list[tuple[Path, int]] = [(start, 0)]
    while stack:
        folder, depth = stack.pop()
        try:
            has_git = (folder / ".git").exists()
        except OSError:
            has_git = False
        if has_git:
            name = repo_name_from_url(git_remote_url(str(folder)))
            if name:
                found.setdefault(name.lower(), str(folder))
        if depth >= max_depth:
            continue
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False) and entry.name not in skip:
                    stack.append((Path(entry.path), depth + 1))
            except OSError:
                continue
    return found


def sanitize_repo_name(name: str) -> str:
    allowed = "-_. "
    cleaned = "".join(ch for ch in name if ch.isalnum() or ch in allowed)
    cleaned = "-".join(part for part in cleaned.split() if part)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-._") or "repo"


def ensure_gitignore(folder: str, log) -> None:
    path = Path(folder) / ".gitignore"
    try:
        existing = path.read_text("utf-8", errors="ignore") if path.exists() else ""
    except OSError:
        existing = ""
    needed = []
    for line in GITHUB_IGNORE_BLOCK.splitlines():
        text = line.strip()
        if text and not text.startswith("#") and text not in existing:
            needed.append(line)
    if needed:
        try:
            with path.open("a", encoding="utf-8") as handle:
                handle.write("\n" + "\n".join(needed) + "\n")
            log(f".gitignore дополнен ({len(needed)} правил)")
        except OSError as exc:
            log(f"не удалось обновить .gitignore: {exc}")


def ensure_git_identity(log) -> bool:
    git = find_tool("git")
    gh = find_tool("gh")
    if not git:
        log("git не найден")
        return False
    rc, name, _ = run_tool([git, "config", "--global", "--get", "user.name"], timeout=30)
    rc2, email, _ = run_tool([git, "config", "--global", "--get", "user.email"], timeout=30)
    if rc == 0 and rc2 == 0 and name and email:
        return True
    if not gh:
        log("git identity не задан. Выполните: git config --global user.name / user.email")
        return False
    login = gh_login()
    if not login:
        log("не удалось получить логин GitHub — проверьте gh auth login")
        return False
    rc, full_name, _ = run_tool([gh, "api", "user", "-q", ".name"], timeout=30)
    display = full_name if rc == 0 and full_name else login
    run_tool([git, "config", "--global", "user.name", display], timeout=30)
    run_tool([git, "config", "--global", "user.email", f"{login}@users.noreply.github.com"], timeout=30)
    log(f"git identity: {display} <{login}@users.noreply.github.com>")
    return True


def _push_with_retry(folder: str, args: list[str], log, retries: int = 3) -> tuple[int, str, str]:
    git = find_tool("git")
    result = (1, "", "")
    for attempt in range(1, retries + 1):
        result = run_tool([git, "push", *args], cwd=folder, timeout=600)
        if result[0] == 0:
            return result
        message = (result[2] or result[1]).lower()
        if not any(key in message for key in (
            "timeout", "408", "disconnect", "hung up", "timed out", "reset",
            "connection", "rpc failed",
        )):
            return result
        log(f"↻ повтор пуша ({attempt}/{retries}) из-за сети…")
    return result


def push_folder_to_github(folder: str, repo: str, public: bool, log) -> bool:
    git = find_tool("git")
    gh = find_tool("gh")
    if not git:
        log("git не найден — установите Git for Windows")
        return False
    if not gh:
        log("gh не найден — установите GitHub CLI")
        return False
    if not gh_authed():
        log("не выполнен вход: gh auth login")
        return False
    if not Path(folder).is_dir():
        log(f"папка не найдена: {folder}")
        return False
    if not ensure_git_identity(log):
        return False

    log(f"=== {Path(folder).name} → github.com/{repo} ===")
    inside = run_tool([git, "rev-parse", "--is-inside-work-tree"], cwd=folder, timeout=30)[0] == 0
    if not inside:
        rc, _out, err = run_tool([git, "init"], cwd=folder, timeout=60)
        if rc != 0:
            log(f"ОШИБКА git init: {err}")
            return False
        run_tool([git, "branch", "-M", "main"], cwd=folder, timeout=30)
        log("git init (main)")

    ensure_gitignore(folder, log)
    rc, _out, err = run_tool([git, "add", "-A"], cwd=folder, timeout=600)
    if rc != 0:
        log(f"ОШИБКА git add: {err}")
        return False
    rc, status, _err = run_tool([git, "status", "--porcelain"], cwd=folder, timeout=120)
    if rc == 0 and status:
        rc, out, err = run_tool(
            [git, "commit", "-m", f"backup: {Path(folder).name} (Мои инструменты)"],
            cwd=folder, timeout=600,
        )
        if rc != 0:
            log(f"ОШИБКА git commit: {err or out}")
            return False
        log("коммит создан")
    else:
        log("изменений для коммита нет")

    login = gh_login()
    token = gh_token()
    visibility = "--public" if public else "--private"

    rc, _out, _err = run_tool([gh, "repo", "view", repo, "--json", "url"], cwd=folder, timeout=60)
    if rc == 0:
        log(f"репозиторий {repo} уже есть — обновляю")
        url = f"https://github.com/{login}/{repo}.git"
        push_url = f"https://{token}@github.com/{login}/{repo}.git" if token else url
        run_tool([git, "remote", "remove", "origin"], cwd=folder, timeout=30)
        run_tool([git, "remote", "add", "origin", push_url], cwd=folder, timeout=30)
    else:
        run_tool([git, "remote", "remove", "origin"], cwd=folder, timeout=30)
        created = False
        last_error = ""
        for attempt in range(1, 4):
            rc, out, err = run_tool(
                [gh, "repo", "create", repo, visibility, "--source", ".", "--remote", "origin",
                 "--push", "--description", f"Backup of {Path(folder).name}"],
                cwd=folder, timeout=600,
            )
            if rc == 0:
                log("репозиторий создан и запушен")
                return True
            last_error = err or out
            if "already exists" in last_error.lower():
                created = True
                break
            if any(key in last_error.lower() for key in (
                "timeout", "408", "disconnect", "hung up", "timed out", "reset",
                "connection", "rpc failed",
            )):
                log(f"↻ повтор создания ({attempt}/3) из-за сети…")
                continue
            break
        if not created:
            log(f"ОШИБКА gh repo create: {last_error}")
            return False
        url = f"https://{token}@github.com/{login}/{repo}.git" if token else f"https://github.com/{login}/{repo}.git"
        run_tool([git, "remote", "remove", "origin"], cwd=folder, timeout=30)
        run_tool([git, "remote", "add", "origin", url], cwd=folder, timeout=30)

    rc, out, err = _push_with_retry(folder, ["-u", "origin", "main"], log)
    if rc != 0:
        rc, out, err = _push_with_retry(folder, ["-u", "origin", "--all"], log)
    if rc != 0:
        log(f"ОШИБКА git push: {err or out}")
        return False
    log("✅ запушено")
    return True


def update_local_repo(folder: str, name: str, log) -> bool:
    git = find_tool("git")
    if not git:
        log("git не найден")
        return False
    rc, _out, err = run_tool([git, "add", "-A"], cwd=folder, timeout=600)
    if rc != 0:
        log(f"ОШИБКА git add: {err}")
        return False
    rc, status, _err = run_tool([git, "status", "--porcelain"], cwd=folder, timeout=120)
    if rc == 0 and status:
        rc, out, err = run_tool(
            [git, "commit", "-m", f"backup: {name} (Мои инструменты)"], cwd=folder, timeout=600,
        )
        if rc != 0:
            log(f"ОШИБКА git commit: {err or out}")
            return False
    rc, out, err = _push_with_retry(folder, ["-u", "origin", "main"], log)
    if rc != 0:
        rc, out, err = _push_with_retry(folder, ["-u", "origin", "--all"], log)
    if rc != 0:
        log(f"ОШИБКА git push: {err or out}")
        return False
    log("✅ обновлено")
    return True


def http_json(url: str, payload: dict | None = None, timeout: float = 3.0):
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    request = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def bypass_state(timeout: float = 2.0) -> dict:
    return http_json(BYPASS_URL + "/api/state", timeout=timeout)


def listening_pid(port: int) -> int:
    script = (
        f"$c = Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue "
        "| Select-Object -First 1 -ExpandProperty OwningProcess; if ($c) { $c }"
    )
    try:
        rc, out, _err = run_tool(
            ["powershell", "-NoProfile", "-Command", script], timeout=30,
        )
    except Exception:  # noqa: BLE001
        return 0
    if rc != 0:
        return 0
    try:
        return int(out.strip().splitlines()[0])
    except (ValueError, IndexError):
        return 0


# --------------------------------------------------------------------------- #
#  Панель: GitHub
# --------------------------------------------------------------------------- #
class GitHubPanel(Panel):
    def __init__(self, master: tk.Misc, hub) -> None:
        super().__init__(master, hub)
        config = config_read().get("github") or {}
        self.root = str(config.get("root") or GITHUB_DEFAULT_ROOT)
        self.repos: list[dict] = []
        self.local: dict[str, str] = {}
        self.login = ""
        self.busy = False
        self.checking = False
        self._build()
        self.refresh()

    def _build(self) -> None:
        top = ttk.Frame(self)
        top.pack(fill="x")
        self.btn_refresh = flat_button(top, "Обновить", self.refresh, kind="ghost")
        self.btn_refresh.pack(side="left")
        self.btn_all = flat_button(top, "Загрузить всё", self.update_all, kind="ghost")
        self.btn_all.pack(side="left", padx=(px(8), 0))
        self.btn_push = flat_button(top, "Залить папку…", self.push_folder)
        self.btn_push.pack(side="right")
        self.login_var = tk.StringVar(value="")
        ttk.Label(top, textvariable=self.login_var, style="Muted.TLabel").pack(
            side="right", padx=(0, px(12))
        )

        path_row = ttk.Frame(self)
        path_row.pack(fill="x", pady=(px(10), 0))
        ttk.Label(path_row, text="Поиск локальных папок:", style="Muted.TLabel").pack(side="left")
        self.root_var = tk.StringVar(value=self.root)
        ttk.Label(path_row, textvariable=self.root_var, style="Muted.TLabel").pack(
            side="left", padx=(px(8), px(8))
        )
        flat_button(path_row, "Изменить…", self.choose_root, kind="ghost").pack(side="left")

        search_row = ttk.Frame(self)
        search_row.pack(fill="x", pady=(px(10), px(6)))
        ttk.Label(search_row, text="Поиск:").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_args: self._render())
        ttk.Entry(search_row, textvariable=self.search_var).pack(
            side="left", fill="x", expand=True, padx=(px(8), 0)
        )

        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill="both", expand=True)
        columns = ("name", "access", "local")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=10,
                                 selectmode="browse")
        self.tree.heading("name", text="Репозиторий")
        self.tree.heading("access", text="Доступ")
        self.tree.heading("local", text="Локальная папка")
        self.tree.column("name", width=px(240), anchor="w")
        self.tree.column("access", width=px(110), anchor="center", stretch=False)
        self.tree.column("local", width=px(430), anchor="w")
        scroll = ttk.Scrollbar(tree_frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda _event: self._update_actions())
        self.tree.bind("<Double-1>", lambda _event: self.open_folder())

        actions = ttk.Frame(self)
        actions.pack(fill="x", pady=(px(10), 0))
        self.btn_open = flat_button(actions, "Открыть папку", self.open_folder, kind="ghost")
        self.btn_open.pack(side="left")
        self.btn_download = flat_button(actions, "Скачать", self.download, kind="ghost")
        self.btn_download.pack(side="left", padx=(px(8), 0))
        self.btn_web = flat_button(actions, "Открыть на GitHub", self.open_web, kind="ghost")
        self.btn_web.pack(side="left", padx=(px(8), 0))
        self.btn_delete = flat_button(actions, "Удалить с GitHub", self.delete_repo, kind="danger")
        self.btn_delete.pack(side="left", padx=(px(8), 0))

        self.summary_var = tk.StringVar(value="Загружаю список…")
        ttk.Label(self, textvariable=self.summary_var, style="Muted.TLabel").pack(
            anchor="w", pady=(px(10), px(6))
        )

        log_frame, self.log_box = self.make_log(self, height=7)
        log_frame.pack(fill="both", expand=True)
        self.append_log(self.log_box, "Git и GitHub CLI находятся автоматически.")

    # -- инфраструктура ---------------------------------------------------- #
    def _log_bg(self, text: str) -> None:
        self.hub.post(lambda: self.append_log(self.log_box, text))

    def _start_busy(self, status: str) -> None:
        self.busy = True
        self._update_actions()
        self.hub.set_status(status)

    def _finish(self) -> None:
        self.busy = False
        self._update_actions()

    def _fail(self, exc: Exception) -> None:
        self._finish()
        self.append_log(self.log_box, f"Ошибка: {exc}", "error")
        self.hub.set_status("GitHub: ошибка")
        messagebox.showerror("GitHub", str(exc), parent=self)

    def _selected_repo(self) -> dict | None:
        selection = self.tree.selection()
        if not selection:
            return None
        full = selection[0]
        for repo in self.repos:
            if repo["full"] == full:
                return repo
        return None

    def _local_for(self, repo: dict | None) -> str:
        if not repo:
            return ""
        return self.local.get(repo["name"].lower(), "")

    def _update_actions(self) -> None:
        selected = self._selected_repo()
        local = self._local_for(selected)
        state = "disabled" if self.busy else "normal"
        self.btn_refresh.configure(state=state)
        self.btn_push.configure(state=state)
        self.btn_all.configure(state=state if self.repos else "disabled")
        self.btn_open.configure(state="normal" if selected and local and not self.busy else "disabled")
        self.btn_download.configure(
            state="normal" if selected and not local and not self.busy else "disabled"
        )
        self.btn_web.configure(state="normal" if selected and not self.busy else "disabled")
        self.btn_delete.configure(state="normal" if selected and not self.busy else "disabled")

    def _render(self) -> None:
        query = self.search_var.get().strip().lower()
        selected = self._selected_repo()
        selected_full = selected["full"] if selected else ""
        self.tree.delete(*self.tree.get_children())
        shown = 0
        for repo in self.repos:
            haystack = f"{repo['name']} {repo['full']}".lower()
            if query and query not in haystack:
                continue
            local = self.local.get(repo["name"].lower(), "")
            display = "—"
            if local:
                try:
                    display = str(Path(local).relative_to(self.root))
                except (ValueError, OSError):
                    display = local
            self.tree.insert(
                "", "end", iid=repo["full"],
                values=(repo["name"], "приватный" if repo["private"] else "публичный", display),
            )
            shown += 1
        if selected_full and self.tree.exists(selected_full):
            self.tree.selection_set(selected_full)
        found = sum(1 for repo in self.repos if repo["name"].lower() in self.local)
        self.summary_var.set(
            f"Репозиториев: {len(self.repos)} · найдено на ПК: {found}"
            + (f" · показано: {shown}" if query else "")
        )
        self._update_actions()

    # -- путь -------------------------------------------------------------- #
    def choose_root(self) -> None:
        chosen = filedialog.askdirectory(
            title="Где искать локальные репозитории", initialdir=self.root, mustexist=True
        )
        if not chosen:
            return
        self.root = os.path.normpath(chosen)
        self.root_var.set(self.root)
        config_update(github={"root": self.root})
        self.refresh()

    # -- список ------------------------------------------------------------ #
    def refresh(self) -> None:
        if self.busy:
            return
        if not find_tool("gh"):
            self.append_log(self.log_box, "GitHub CLI (gh) не найден.", "error")
            self.summary_var.set("gh не найден — установите GitHub CLI")
            self.hub.set_status("GitHub: gh не найден")
            return
        self._start_busy("GitHub: загружаю список…")
        root = self.root

        def work():
            if not gh_authed():
                raise RuntimeError(
                    "Не выполнен вход в GitHub.\nВыполните в терминале:\n\ngh auth login --web"
                )
            return {
                "login": gh_login(),
                "repos": github_repos(),
                "local": scan_local_repos(root),
            }

        self.hub.run_bg(work, self._refresh_done, self._fail)

    def _refresh_done(self, data: dict) -> None:
        self.login = data.get("login") or ""
        self.repos = data.get("repos") or []
        self.local = data.get("local") or {}
        self.login_var.set(f"@{self.login}" if self.login else "")
        self._finish()
        self._render()
        self.hub.set_status("GitHub: список обновлён")
        self.append_log(self.log_box, f"Загружено репозиториев: {len(self.repos)}")

    # -- заливка ----------------------------------------------------------- #
    def push_folder(self) -> None:
        if self.busy:
            return
        folder = filedialog.askdirectory(
            title="Папку с каким проектом залить на GitHub?", initialdir=self.root, mustexist=True
        )
        if not folder:
            return
        default = sanitize_repo_name(Path(folder).name)
        name = messagebox.askstring(
            "Новый репозиторий",
            f"Папка:\n{folder}\n\nИмя репозитория на GitHub (будет публичным):",
            default, parent=self,
        )
        if not name:
            return
        name = sanitize_repo_name(name)
        self._start_busy(f"GitHub: заливаю {name}…")
        self.append_log(self.log_box, f"=== {name} ===")

        def work():
            return push_folder_to_github(folder, name, True, self._log_bg)

        self.hub.run_bg(work, self._push_done, self._fail)

    def _push_done(self, ok: bool) -> None:
        self._finish()
        if ok:
            self.hub.set_status("GitHub: залито")
            self.refresh()
        else:
            self.hub.set_status("GitHub: заливка не удалась")

    # -- обновление всех --------------------------------------------------- #
    def update_all(self) -> None:
        if self.busy:
            return
        targets = [repo for repo in self.repos if self.local.get(repo["name"].lower())]
        if not targets:
            messagebox.showinfo(
                "GitHub", "Нет репозиториев, у которых есть локальная папка.", parent=self
            )
            return
        if not messagebox.askyesno(
            "Загрузить всё",
            f"Обновить на GitHub {len(targets)} репозиториев?\n\n"
            "В каждом будет сделан коммит локальных изменений и push.",
            parent=self,
        ):
            return
        self._start_busy(f"GitHub: обновляю {len(targets)}…")
        total = len(targets)

        def work():
            updated = 0
            failed = 0
            for index, repo in enumerate(targets, 1):
                folder = self.local[repo["name"].lower()]
                self._log_bg(f"[{index}/{total}] {repo['name']}")
                try:
                    ok = update_local_repo(folder, repo["name"], self._log_bg)
                except Exception as exc:  # noqa: BLE001
                    self._log_bg(f"Ошибка {repo['name']}: {exc}")
                    ok = False
                updated += 1 if ok else 0
                failed += 0 if ok else 1
            return updated, failed

        self.hub.run_bg(work, self._update_all_done, self._fail)

    def _update_all_done(self, result: tuple[int, int]) -> None:
        updated, failed = result
        self._finish()
        self.append_log(self.log_box, f"=== Готово: обновлено {updated}, ошибок {failed} ===",
                        "ok" if not failed else "error")
        self.hub.set_status(f"GitHub: обновлено {updated}, ошибок {failed}")

    # -- скачивание / удаление / открытие ---------------------------------- #
    def download(self) -> None:
        repo = self._selected_repo()
        if not repo or self.busy:
            return
        dest = Path(self.root) / repo["name"]
        if dest.exists() and any(dest.iterdir()):
            messagebox.showerror(
                "GitHub", f"Папка уже существует и не пуста:\n{dest}", parent=self
            )
            return
        self._start_busy(f"GitHub: скачиваю {repo['name']}…")
        full = repo["full"]
        gh = find_tool("gh")

        def work():
            rc, out, err = run_tool([gh, "repo", "clone", full, str(dest)], timeout=900)
            if rc != 0:
                raise RuntimeError(err or out or "не удалось клонировать репозиторий")
            return str(dest)

        self.hub.run_bg(work, self._download_done, self._fail)

    def _download_done(self, path: str) -> None:
        self._finish()
        self.append_log(self.log_box, f"✅ скачано: {path}", "ok")
        self.hub.set_status("GitHub: скачано")
        self.refresh()

    def delete_repo(self) -> None:
        repo = self._selected_repo()
        if not repo or self.busy:
            return
        if not messagebox.askyesno(
            "Удалить с GitHub?",
            f"{repo['full']}\n\nРепозиторий будет удалён с GitHub безвозвратно.\n"
            "Локальная папка останется на диске.",
            parent=self,
        ):
            return
        self._start_busy(f"GitHub: удаляю {repo['name']}…")
        gh = find_tool("gh")
        full = repo["full"]

        def work():
            rc, out, err = run_tool([gh, "repo", "delete", full, "--yes"], timeout=180)
            if rc != 0:
                message = err or out
                if "delete_repo" in message:
                    raise RuntimeError(
                        "У токена нет права delete_repo.\n\n"
                        "GitHub → Settings → Developer settings → Personal access tokens → "
                        "ваш токен → галочка delete_repo → Update token."
                    )
                raise RuntimeError(message or "не удалось удалить репозиторий")
            return True

        self.hub.run_bg(work, self._delete_done, self._fail)

    def _delete_done(self, _result: bool) -> None:
        self._finish()
        self.append_log(self.log_box, "🗑 репозиторий удалён с GitHub")
        self.hub.set_status("GitHub: удалено")
        self.refresh()

    def open_folder(self) -> None:
        local = self._local_for(self._selected_repo())
        if not local:
            return
        try:
            os.startfile(local)
        except OSError as exc:
            messagebox.showerror("GitHub", f"Не удалось открыть папку:\n{exc}", parent=self)

    def open_web(self) -> None:
        repo = self._selected_repo()
        if repo and repo.get("url"):
            webbrowser.open(repo["url"])


# --------------------------------------------------------------------------- #
#  Панель: Bypass Manager (пульт)
# --------------------------------------------------------------------------- #
class BypassPanel(Panel):
    def __init__(self, master: tk.Misc, hub) -> None:
        super().__init__(master, hub)
        config = config_read().get("bypass") or {}
        self.root = str(config.get("root") or BYPASS_DEFAULT_ROOT)
        self.online = False
        self.busy = False
        self.checking = False
        self.state: dict = {}
        self.proc: subprocess.Popen | None = None
        self._offline_noted = False
        self._build()
        self._tick()

    def _build(self) -> None:
        path_row = ttk.Frame(self)
        path_row.pack(fill="x")
        self.root_var = tk.StringVar(value=self.root)
        ttk.Label(path_row, textvariable=self.root_var, style="Muted.TLabel").pack(side="left")
        flat_button(path_row, "Изменить путь…", self.choose_root, kind="ghost").pack(
            side="left", padx=(px(10), 0)
        )

        card = tk.Frame(self, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="x", pady=(px(12), 0))
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=px(16), pady=px(14))

        self.panel_var = tk.StringVar(value="проверяю…")
        self.apps_var = tk.StringVar(value="—")
        self.happ_var = tk.StringVar(value="—")
        self.gbd_var = tk.StringVar(value="—")
        for title, variable in (
            ("Панель:", self.panel_var),
            ("Приложения:", self.apps_var),
            ("Happ:", self.happ_var),
            ("GoodbyeDPI:", self.gbd_var),
        ):
            row = tk.Frame(inner, bg=CARD)
            row.pack(fill="x", pady=(px(6) if title != "Панель:" else 0, 0))
            tk.Label(row, text=title, bg=CARD, fg=MUTED, width=12, anchor="w",
                     font=("Segoe UI", 9)).pack(side="left")
            tk.Label(row, textvariable=variable, bg=CARD, fg=FG, anchor="w",
                     justify="left", wraplength=px(760), font=("Segoe UI", 9)).pack(
                side="left", fill="x", expand=True
            )

        controls = ttk.Frame(self)
        controls.pack(fill="x", pady=(px(14), px(6)))
        self.btn_start = flat_button(controls, "Запустить панель", self.start_panel)
        self.btn_start.pack(side="left")
        self.btn_stop = flat_button(controls, "Остановить", self.stop_panel, kind="ghost")
        self.btn_stop.pack(side="left", padx=(px(8), 0))
        self.btn_open = flat_button(controls, "Открыть в браузере", self.open_panel, kind="ghost")
        self.btn_open.pack(side="left", padx=(px(8), 0))
        self.btn_refresh = flat_button(controls, "Обновить", self.refresh_now, kind="ghost")
        self.btn_refresh.pack(side="right")

        toggles = ttk.Frame(self)
        toggles.pack(fill="x", pady=(px(4), 0))
        self.btn_happ_on = flat_button(toggles, "Happ вкл", lambda: self.toggle("happ", True),
                                       kind="ghost")
        self.btn_happ_on.pack(side="left")
        self.btn_happ_off = flat_button(toggles, "Happ выкл", lambda: self.toggle("happ", False),
                                        kind="ghost")
        self.btn_happ_off.pack(side="left", padx=(px(6), px(18)))
        self.btn_gbd_on = flat_button(toggles, "DPI вкл", lambda: self.toggle("gbd", True),
                                      kind="ghost")
        self.btn_gbd_on.pack(side="left")
        self.btn_gbd_off = flat_button(toggles, "DPI выкл", lambda: self.toggle("gbd", False),
                                       kind="ghost")
        self.btn_gbd_off.pack(side="left", padx=(px(6), px(18)))
        self.btn_install = flat_button(toggles, "Установить ProxiFyre", self.install,
                                       kind="ghost")
        self.btn_install.pack(side="left")

        ttk.Label(
            self,
            text="Панель — это отдельный движок bypass-manager. Здесь только управление: "
                 "запуск, статусы и переключатели.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(px(12), 0))

        log_frame, self.log_box = self.make_log(self, height=8)
        log_frame.pack(fill="both", expand=True, pady=(px(10), 0))
        self.append_log(self.log_box, "Проверяю панель на 127.0.0.1:8790…")

    # -- статус ------------------------------------------------------------ #
    def _tick(self) -> None:
        if not self.winfo_exists():
            return
        if not self.busy and not self.checking:
            self.checking = True
            self.hub.run_bg(bypass_state, self._state_ok, self._state_off)
        self.after(3000, self._tick)

    def refresh_now(self) -> None:
        if self.checking or self.busy:
            return
        self.checking = True
        self.hub.run_bg(bypass_state, self._state_ok, self._state_off)

    def _state_ok(self, state: dict) -> None:
        self.checking = False
        self.online = True
        self._offline_noted = False
        self.state = state if isinstance(state, dict) else {}
        apps = self.state.get("apps") or []
        self.panel_var.set("работает · 127.0.0.1:8790")
        self.apps_var.set(", ".join(apps) if apps else "не выбраны")
        happ = self.state.get("happ") or {}
        if happ.get("on"):
            detail = "включён"
            if happ.get("pending"):
                detail += " · ждёт «Подключить» в Happ"
            if happ.get("tunnel"):
                detail += " · туннель поднят"
            self.happ_var.set(detail)
        else:
            self.happ_var.set("выключен" + (" · ждёт подключения" if happ.get("pending") else ""))
        gbd = self.state.get("gbd") or {}
        if gbd.get("on"):
            detail = f"включён · порт {gbd.get('port', '—')}"
        else:
            detail = "выключен"
        detail += " · ProxiFyre установлен" if gbd.get("installed") else " · ProxiFyre не установлен"
        if gbd.get("on") and gbd.get("admin") is False:
            detail += " · нет прав администратора"
        self.gbd_var.set(detail)
        self._update_controls()

    def _state_off(self, _exc: Exception) -> None:
        self.checking = False
        was_online = self.online
        self.online = False
        self.state = {}
        self.panel_var.set("не запущена")
        self.apps_var.set("—")
        self.happ_var.set("—")
        self.gbd_var.set("—")
        if not self._offline_noted:
            self._offline_noted = True
            text = ("Панель не запущена. Нажмите «Запустить панель» — "
                    "движок поднимется на 127.0.0.1:8790.")
            self.append_log(self.log_box, text)
        if was_online:
            self.hub.set_status("Bypass: панель не отвечает")
        self._update_controls()

    def _update_controls(self) -> None:
        busy = self.busy
        online = self.online
        self.btn_start.configure(state="disabled" if busy or online else "normal")
        self.btn_stop.configure(state="disabled" if busy or not online else "normal")
        self.btn_open.configure(state="disabled" if busy or not online else "normal")
        self.btn_refresh.configure(state="disabled" if busy or self.checking else "normal")
        for button in (self.btn_happ_on, self.btn_happ_off, self.btn_gbd_on,
                       self.btn_gbd_off, self.btn_install):
            button.configure(state="disabled" if busy or not online else "normal")

    # -- путь -------------------------------------------------------------- #
    def choose_root(self) -> None:
        chosen = filedialog.askdirectory(
            title="Папка bypass-manager", initialdir=self.root, mustexist=True
        )
        if not chosen:
            return
        self.root = os.path.normpath(chosen)
        self.root_var.set(self.root)
        config_update(bypass={"root": self.root})
        self.append_log(self.log_box, f"Путь сохранён: {self.root}")
        self.refresh_now()

    # -- запуск / остановка ------------------------------------------------ #
    def _venv_python(self) -> Path | None:
        scripts = Path(self.root) / ".venv" / "Scripts"
        for name in ("pythonw.exe", "python.exe"):
            candidate = scripts / name
            if candidate.exists():
                return candidate
        return None

    def start_panel(self) -> None:
        if self.busy or self.online:
            return
        if not Path(self.root).is_dir():
            messagebox.showerror("Bypass", f"Папка не найдена:\n{self.root}", parent=self)
            return
        python = self._venv_python()
        if python is None:
            messagebox.showerror(
                "Bypass",
                "Не найдено окружение .venv.\n\n"
                f"Один раз запустите run.bat в папке:\n{self.root}\n"
                "он создаст окружение и поставит Flask, после этого запуск будет из этого окна.",
                parent=self,
            )
            return
        self.busy = True
        self._update_controls()
        self.append_log(self.log_box, "Запускаю панель…")
        self.hub.set_status("Bypass: запускаю панель…")
        try:
            subprocess.run(
                [str(python), "-m", "app.panel_guard"],
                cwd=self.root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=NO_WINDOW,
                timeout=30,
            )
            self.proc = subprocess.Popen(
                [str(python), "-m", "app.main"],
                cwd=self.root,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=NO_WINDOW,
            )
        except OSError as exc:
            self.busy = False
            self._update_controls()
            self.append_log(self.log_box, f"Не удалось запустить: {exc}", "error")
            messagebox.showerror("Bypass", str(exc), parent=self)
            return
        self.busy = False
        self.append_log(self.log_box, "Процесс запущен, жду ответа панели…")
        self.after(2500, self.refresh_now)

    def stop_panel(self) -> None:
        if self.busy:
            return
        self.busy = True
        self._update_controls()
        self.append_log(self.log_box, "Останавливаю панель…")
        proc = self.proc

        def work():
            pid = listening_pid(8790)
            if not pid and proc is not None and proc.poll() is None:
                pid = proc.pid
            if pid:
                run_tool(["taskkill", "/PID", str(pid), "/F"], timeout=30)
                return pid
            if proc is not None and proc.poll() is None:
                proc.terminate()
                return proc.pid
            return 0

        self.hub.run_bg(work, self._stop_done, self._stop_fail)

    def _stop_done(self, pid: int) -> None:
        self.busy = False
        self.proc = None
        self.online = False
        self._update_controls()
        if pid:
            self.append_log(self.log_box, f"Панель остановлена (PID {pid})")
            self.hub.set_status("Bypass: панель остановлена")
        else:
            self.append_log(self.log_box, "Панель не была запущена")
        self.after(600, self.refresh_now)

    def _stop_fail(self, exc: Exception) -> None:
        self.busy = False
        self._update_controls()
        self.append_log(self.log_box, f"Не удалось остановить: {exc}", "error")

    def open_panel(self) -> None:
        webbrowser.open(BYPASS_URL)

    # -- действия ---------------------------------------------------------- #
    def toggle(self, kind: str, on: bool) -> None:
        if self.busy or not self.online:
            return
        label = "Happ" if kind == "happ" else "GoodbyeDPI"
        self._post(f"/api/{kind}", {"on": on}, f"{label}: {'включаю' if on else 'выключаю'}…", 180)

    def install(self) -> None:
        if self.busy or not self.online:
            return
        if not messagebox.askyesno(
            "Установка ProxiFyre",
            "Скачать и установить ProxiFyre и драйвер?\n\n"
            "Может потребоваться подтверждение администратора Windows.\n"
            "Панель должна быть запущена от администратора.",
            parent=self,
        ):
            return
        self._post("/api/gbd-install", {}, "Устанавливаю ProxiFyre…", 600)

    def _post(self, path: str, payload: dict, title: str, timeout: int) -> None:
        self.busy = True
        self._update_controls()
        self.append_log(self.log_box, title)
        self.hub.set_status(f"Bypass: {title}")

        def work():
            return http_json(BYPASS_URL + path, payload, timeout=timeout)

        self.hub.run_bg(work, self._post_done, self._post_fail)

    def _post_done(self, result: dict) -> None:
        self.busy = False
        detail = str((result or {}).get("detail") or "")
        ok = bool((result or {}).get("ok"))
        if detail:
            self.append_log(self.log_box, detail, "ok" if ok else "error")
        self.hub.set_status("Bypass: готово" if ok else "Bypass: не выполнено")
        self.refresh_now()

    def _post_fail(self, exc: Exception) -> None:
        self.busy = False
        self.append_log(self.log_box, f"Ошибка: {exc}", "error")
        self.hub.set_status("Bypass: ошибка")
        self.refresh_now()


# --------------------------------------------------------------------------- #
#  Боковое меню и главное окно
# --------------------------------------------------------------------------- #
NAV = (
    ("autoclicker", "AutoClicker", "клики по экрану"),
    ("clean", "Пустые файлы", "0 байт и пустые папки"),
    ("dns", "DNS", "Quad9 · Xbox · DoH"),
    ("cleanup", "Очистка Windows", "кэши · корзина"),
    ("github", "GitHub", "бэкап проектов"),
    ("bypass", "Bypass", "обход DPI по приложениям"),
)

HEADERS = {
    "autoclicker": (
        "AutoClicker",
        "Клики по точкам, областям и картинкам. F6 — старт/стоп, F7 — точка под курсором.",
    ),
    "clean": (
        "Пустые файлы и папки",
        "Сначала проверка — удаление только после подтверждения.",
    ),
    "dns": (
        "DNS",
        "Профили Quad9 и Xbox, DHCP и DNS-over-HTTPS. Нужны права администратора.",
    ),
    "cleanup": (
        "Очистка Windows",
        "Temp, кэши, корзина и Windows.old. Личные файлы не затрагиваются.",
    ),
    "github": (
        "GitHub",
        "Список репозиториев, заливка папок, скачивание и обновление. Нужны gh и git.",
    ),
    "bypass": (
        "Bypass Manager",
        "Обход DPI для выбранных приложений: Happ и GoodbyeDPI. Пульт над панелью.",
    ),
}


class NavItem(tk.Frame):
    def __init__(self, parent: tk.Misc, title: str, subtitle: str, command) -> None:
        super().__init__(parent, bg=SIDEBAR, cursor="hand2")
        self.command = command
        self.active = False

        self.accent = tk.Frame(self, bg=SIDEBAR, width=px(3))
        self.accent.pack(side="left", fill="y")
        self.body = tk.Frame(self, bg=SIDEBAR)
        self.body.pack(side="left", fill="both", expand=True, padx=(px(11), px(12)), pady=px(9))

        self.title_label = tk.Label(
            self.body, text=title, bg=SIDEBAR, fg="#b9c1cd",
            font=("Segoe UI", 10, "bold"), anchor="w", cursor="hand2",
        )
        self.title_label.pack(fill="x")
        self.subtitle_label = tk.Label(
            self.body, text=subtitle, bg=SIDEBAR, fg="#6c7482",
            font=("Segoe UI", 8), anchor="w", cursor="hand2",
        )
        self.subtitle_label.pack(fill="x")

        for widget in (self, self.body, self.title_label, self.subtitle_label):
            widget.bind("<Button-1>", lambda _event: self.command())
            widget.bind("<Enter>", lambda _event: self._hover(True))
            widget.bind("<Leave>", lambda _event: self._hover(False))

    def _paint(self) -> None:
        background = "#1b1f27" if self.active else SIDEBAR
        for widget in (self, self.body):
            widget.configure(bg=background)
        self.title_label.configure(bg=background, fg=FG if self.active else "#b9c1cd")
        self.subtitle_label.configure(bg=background, fg=MUTED if self.active else "#6c7482")
        self.accent.configure(bg=ACCENT if self.active else background)

    def _hover(self, entering: bool) -> None:
        if self.active:
            return
        background = "#161a21" if entering else SIDEBAR
        for widget in (self, self.body):
            widget.configure(bg=background)
        self.title_label.configure(bg=background)
        self.subtitle_label.configure(bg=background)
        self.accent.configure(bg=background)

    def set_active(self, active: bool) -> None:
        self.active = active
        self._paint()


class Hub(tk.Tk):
    def __init__(self, initial_tab: str = "autoclicker") -> None:
        super().__init__()
        global SCALE
        SCALE = max(1.0, self.winfo_fpixels("1i") / 96.0)
        self.title("Мои инструменты")
        width = min(px(1180), self.winfo_screenwidth() - px(40))
        height = min(px(820), self.winfo_screenheight() - px(90))
        self.geometry(f"{width}x{height}")
        self.minsize(min(px(1000), width), min(px(700), height))
        self.configure(bg=BG)
        apply_dark_theme(self)
        self.option_add("*Font", ("Segoe UI", 9))

        self.events: queue.Queue = queue.Queue()
        self.panels: dict[str, Panel] = {}
        self.nav_items: dict[str, NavItem] = {}
        self.current = ""
        self._closing = False

        self.factories = {
            "autoclicker": lambda: AutoClickerPanel(self.content, self),
            "clean": lambda: CleanPanel(self.content, self),
            "dns": lambda: DnsPanel(self.content, self),
            "cleanup": lambda: CleanupPanel(self.content, self),
            "github": lambda: GitHubPanel(self.content, self),
            "bypass": lambda: BypassPanel(self.content, self),
        }

        self._build()
        self.show(initial_tab if initial_tab in self.factories else "autoclicker")
        self.after(70, self._poll)
        self.protocol("WM_DELETE_WINDOW", self.shutdown)
        enable_dark_titlebar(self)

    # -- разметка ---------------------------------------------------------- #
    def _build(self) -> None:
        sidebar = tk.Frame(self, bg=SIDEBAR, width=px(234))
        sidebar.pack(side="left", fill="y")
        sidebar.pack_propagate(False)

        brand = tk.Frame(sidebar, bg=SIDEBAR)
        brand.pack(fill="x", padx=px(20), pady=(px(24), px(18)))
        tk.Label(brand, text="МОИ", bg=SIDEBAR, fg="#5f6773",
                 font=("Segoe UI", 8, "bold")).pack(anchor="w")
        tk.Label(brand, text="Инструменты", bg=SIDEBAR, fg=FG,
                 font=("Segoe UI", 15, "bold")).pack(anchor="w")

        nav = tk.Frame(sidebar, bg=SIDEBAR)
        nav.pack(fill="x")
        for key, title, subtitle in NAV:
            item = NavItem(nav, title, subtitle, lambda k=key: self.show(k))
            item.pack(fill="x", pady=px(1))
            self.nav_items[key] = item

        bottom = tk.Frame(sidebar, bg=SIDEBAR)
        bottom.pack(side="bottom", fill="x", padx=px(16), pady=px(16))
        admin = is_admin()
        tk.Label(
            bottom,
            text="● права администратора" if admin else "● обычные права",
            bg=SIDEBAR,
            fg=OK if admin else MUTED,
            font=("Segoe UI", 8),
            anchor="w",
        ).pack(fill="x")
        if not admin:
            flat_button(
                bottom, "Запустить от администратора",
                lambda: self.request_admin(), kind="ghost", padx=px(8), pady=px(6),
            ).pack(fill="x", pady=(px(8), 0))
        tk.Label(
            bottom,
            text=f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
            bg=SIDEBAR,
            fg="#5f6773",
            font=("Segoe UI", 8),
            anchor="w",
        ).pack(fill="x", pady=(px(10), 0))

        main = tk.Frame(self, bg=BG)
        main.pack(side="left", fill="both", expand=True)

        status_bar = tk.Frame(main, bg="#0f1115", height=px(28))
        status_bar.pack(side="bottom", fill="x")
        status_bar.pack_propagate(False)
        self.status_var = tk.StringVar(value="Готово")
        tk.Label(status_bar, textvariable=self.status_var, bg="#0f1115", fg=MUTED,
                 font=("Segoe UI", 8), anchor="w").pack(side="left", padx=px(14))
        tk.Label(status_bar, text="Ctrl+1…6 — переключение разделов", bg="#0f1115",
                 fg="#5f6773", font=("Segoe UI", 8), anchor="e").pack(side="right", padx=px(14))

        head = tk.Frame(main, bg=BG)
        head.pack(fill="x", padx=px(26), pady=(px(22), px(12)))
        self.title_var = tk.StringVar()
        self.subtitle_var = tk.StringVar()
        tk.Label(head, textvariable=self.title_var, bg=BG, fg=FG,
                 font=("Segoe UI", 18, "bold"), anchor="w").pack(fill="x")
        tk.Label(head, textvariable=self.subtitle_var, bg=BG, fg=MUTED,
                 font=("Segoe UI", 9), anchor="w").pack(fill="x", pady=(px(4), 0))

        self.content = tk.Frame(main, bg=BG)
        self.content.pack(fill="both", expand=True, padx=px(26), pady=(0, px(14)))
        self.content.rowconfigure(0, weight=1)
        self.content.columnconfigure(0, weight=1)

        for index, (key, _title, _subtitle) in enumerate(NAV, 1):
            self.bind_all(f"<Control-Key-{index}>", lambda _event, k=key: self.show(k))

    # -- разделы ----------------------------------------------------------- #
    def show(self, key: str) -> None:
        panel = self.panels.get(key)
        if panel is None:
            panel = self.factories[key]()
            panel.grid(row=0, column=0, sticky="nsew")
            self.panels[key] = panel
        panel.tkraise()
        self.current = key
        for item_key, item in self.nav_items.items():
            item.set_active(item_key == key)
        title, subtitle = HEADERS[key]
        self.title_var.set(title)
        self.subtitle_var.set(subtitle)

    # -- фон и события ----------------------------------------------------- #
    def post(self, callback) -> None:
        self.events.put(callback)

    def run_bg(self, work, on_done, on_error) -> None:
        def runner() -> None:
            try:
                result = work()
            except Exception as exc:  # noqa: BLE001
                self.post(lambda exc=exc: on_error(exc))
            else:
                self.post(lambda result=result: on_done(result))

        threading.Thread(target=runner, daemon=True).start()

    def _poll(self) -> None:
        try:
            while True:
                callback = self.events.get_nowait()
                try:
                    callback()
                except Exception:  # noqa: BLE001
                    log_exception()
        except queue.Empty:
            pass
        self.after(70, self._poll)

    def set_status(self, text: str) -> None:
        self.status_var.set(text)

    # -- права ------------------------------------------------------------- #
    def ensure_admin(self, tab: str) -> bool:
        if is_admin():
            return True
        if not messagebox.askyesno(
            "Нужны права администратора",
            "Для этого действия нужны права администратора.\n\n"
            "Перезапустить приложение от имени администратора?",
            parent=self,
        ):
            return False
        if relaunch_as_admin(tab):
            self.after(150, self.shutdown)
            return False
        messagebox.showerror(
            "Права администратора",
            "Не удалось перезапустить приложение с правами администратора.\n"
            "Запрос UAC был отклонён.",
            parent=self,
        )
        return False

    def request_admin(self) -> None:
        tab = self.current or "dns"
        if is_admin():
            messagebox.showinfo("Права администратора",
                                "Приложение уже запущено от администратора.", parent=self)
            return
        self.ensure_admin(tab)

    # -- выход ------------------------------------------------------------- #
    def shutdown(self) -> None:
        if self._closing:
            return
        self._closing = True
        panel = self.panels.get("autoclicker")
        if panel is not None:
            try:
                panel.shutdown()
            except Exception:  # noqa: BLE001
                pass
        self.destroy()


def main() -> int:
    try:
        sys.stdout.reconfigure(errors="replace")
    except (AttributeError, OSError):
        pass
    parser = argparse.ArgumentParser(description="Мои инструменты")
    parser.add_argument("--tab", choices=[key for key, _t, _s in NAV], default="autoclicker")
    args, _unknown = parser.parse_known_args()

    if HAS_DEPS:
        pyautogui.PAUSE = 0
        pyautogui.FAILSAFE = True

    hub = Hub(initial_tab=args.tab)
    hub.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
