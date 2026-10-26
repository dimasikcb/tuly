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
import re
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
import zipfile
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
COPIED_BG = "#1b7a4b"   # кратковременная вспышка «скопировано» в логах

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

    def make_log(self, parent: tk.Misc, *, height: int = 10, wrap: str = "word",
                 label: str = "Лог"):
        frame = ttk.Frame(parent)
        head = ttk.Frame(frame)
        head.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, px(4)))
        ttk.Label(head, text="Лог — выдели мышью, Ctrl+C или «Копировать всё»",
                  style="Muted.TLabel").pack(side="left")
        text = dark_text(frame, height=height, wrap=wrap)
        text.tag_configure("error", foreground="#ff6b6b")
        text.tag_configure("ok", foreground=OK)
        copy_btn = flat_button(head, "Копировать всё", lambda: None, kind="ghost")
        copy_btn.pack(side="right")
        copy_btn.configure(command=lambda: self.copy_log(text, copy_btn, label=label))
        flat_button(head, "Очистить", lambda: text.delete("1.0", "end"),
                    kind="ghost").pack(side="right", padx=(0, px(6)))
        scroll = ttk.Scrollbar(frame, orient="vertical", command=text.yview)
        text.configure(yscrollcommand=scroll.set)
        text.grid(row=1, column=0, sticky="nsew")
        scroll.grid(row=1, column=1, sticky="ns")
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)
        self.attach_copy(text, label=label, readonly=True)
        return frame, text

    def attach_copy(self, text: tk.Text, *, label: str = "текст",
                    readonly: bool = False, clear: bool = True) -> None:
        """Делает любой текстовый блок копируемым: Ctrl+C, Ctrl+A, правый клик.

        Копирование/выделение ловим универсально — по **физическому коду
        клавиши** (``event.keycode``: VK_C=67, VK_A=65, VK_INSERT=45), который
        не зависит от раскладки. Иначе при русской раскладке Ctrl+C даёт keysym
        «с», а не «c», и привязки ``<Control-c>``/``<<Copy>>`` молча не
        срабатывают — копия не происходит. Дополнительно страхуемся проверкой
        keysym (c/с, a/а/ф), чтобы сработать в любой раскладке.
        """

        # Навигационные клавиши, разрешённые даже в режиме «только чтение».
        NAV_KEYS = {
            "Left", "Right", "Up", "Down", "Home", "End",
            "Prior", "Next", "Shift_L", "Shift_R",
            "Control_L", "Control_R", "Tab", "Insert",
        }
        # Буквы-«действия» во всех раскладках.
        COPY_KEYS = {"c", "с"}                 # лат. C и кир. С
        SELECT_KEYS = {"a", "а", "ф"}          # лат. A, кир. А и кир. Ф (Ctrl+A на RU)

        def copy_selection(_event=None):
            try:
                chunk = text.get("sel.first", "sel.last")
            except tk.TclError:
                chunk = text.get("1.0", "end-1c")
            if not chunk:
                return "break"
            self.copy_text(chunk, label=label, widget=text)
            return "break"

        def select_all(_event=None):
            text.tag_add("sel", "1.0", "end-1c")
            return "break"

        def on_key(event):
            ctrl = bool(event.state & 0x4) and not bool(event.state & 0x8)
            if ctrl:
                vk = getattr(event, "keycode", 0) or 0
                ks = (event.keysym or "").lower()
                if vk == 67 or ks in COPY_KEYS:                # C — копировать
                    return copy_selection()
                if vk == 65 or ks in SELECT_KEYS:             # A — выделить всё
                    return select_all()
                if vk == 45 or ks == "insert":                # Insert — копировать
                    return copy_selection()
                # прочие Ctrl-комбинации в «только чтение» глушим
                return "break" if readonly else None
            # без Ctrl
            if readonly and event.keysym not in NAV_KEYS:
                return "break"
            return None

        text.bind("<Key>", on_key)
        text.bind("<<Copy>>", copy_selection)          # запасной путь (Ctrl+Insert и т.п.)
        text.bind("<<SelectAll>>", select_all)
        text.bind("<Control-a>", select_all)
        text.bind("<Control-A>", select_all)

        menu = tk.Menu(text, tearoff=0)
        menu.add_command(label="Копировать выделенное", command=copy_selection)
        menu.add_command(
            label="Копировать всё",
            command=lambda: self.copy_text(text.get("1.0", "end-1c"), label=label),
        )
        if clear:
            menu.add_separator()
            menu.add_command(label="Очистить", command=lambda: text.delete("1.0", "end"))
        text.bind("<Button-3>", lambda event: menu.tk_popup(event.x_root, event.y_root))

    def copy_log(self, text: tk.Text, button: tk.Button, *, label: str = "Лог") -> None:
        try:
            chunk = text.get("sel.first", "sel.last")
        except tk.TclError:
            chunk = text.get("1.0", "end-1c")
        self.copy_text(chunk, label=label, widget=text)
        original = button.cget("text")
        button.configure(text="✅ Скопировано")
        self.after(1800, lambda: button.configure(text=original))

    def copy_text(self, chunk: str, *, label: str = "лог", widget=None) -> None:
        """Кладёт текст в буфер обмена и явно показывает результат.

        При копировании текстовый блок кратко вспыхивает зелёным — видно,
        что именно скопировалось.
        """
        if not chunk.strip():
            self.hub.set_status("Копировать нечего — пусто")
            self.hub.toast("Копировать нечего — блок пуст", kind="warn")
            return
        self.clipboard_clear()
        self.clipboard_append(chunk)
        self.update()          # без update() Windows не фиксирует буфер сразу
        lines = chunk.count("\n") + (0 if chunk.endswith("\n") else 1)
        self.hub.set_status(f"📋 скопировано: {lines} строк, {len(chunk)} символов")
        self.hub.toast(f"📋 {label}: скопировано {lines} стр., {len(chunk)} симв.")
        if widget is not None:
            self.flash_copied(widget)

    def flash_copied(self, widget) -> None:
        """Кратковременная зелёная вспышка + рамка — явное подтверждение копии."""
        try:
            widget.configure(bg=COPIED_BG, highlightbackground=OK, highlightcolor=OK)
        except (tk.TclError, AttributeError):
            return
        self.after(500, lambda: self._unflash(widget))

    @staticmethod
    def _unflash(widget) -> None:
        try:
            widget.configure(bg=LOG_BG, highlightbackground=BORDER, highlightcolor=BORDER)
        except (tk.TclError, AttributeError):
            pass

    @staticmethod
    def append_log(text: tk.Text, line: str, tag: str | None = None) -> None:
        text.insert("end", f"{time.strftime('%H:%M:%S')}  {line}\n", (tag,) if tag else ())
        lines = int(text.index("end-1c").split(".")[0])
        if lines > 500:
            text.delete("1.0", f"{lines - 500}.0")
        text.see("end")


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
        bar = ttk.Frame(wrap)
        bar.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, px(4)))
        ttk.Label(bar, text="Список — выдели мышью, Ctrl+C или «Копировать всё»",
                  style="Muted.TLabel").pack(side="left")
        copy_btn = flat_button(bar, "Копировать всё", lambda: None, kind="ghost")
        copy_btn.pack(side="right")
        copy_btn.configure(command=lambda: self.copy_log(self.list, copy_btn, label="Список"))
        self.list = dark_text(wrap, height=14, wrap="none")
        scroll_y = ttk.Scrollbar(wrap, orient="vertical", command=self.list.yview)
        scroll_x = ttk.Scrollbar(wrap, orient="horizontal", command=self.list.xview)
        self.list.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        self.list.grid(row=1, column=0, sticky="nsew")
        scroll_y.grid(row=1, column=1, sticky="ns")
        scroll_x.grid(row=2, column=0, sticky="ew")
        wrap.rowconfigure(1, weight=1)
        wrap.columnconfigure(0, weight=1)
        self.list.tag_configure("file", foreground="#9aa4b2")
        self.list.tag_configure("dir", foreground="#7aa2ff")
        self.list.tag_configure("error", foreground="#ff6b6b")
        self.list.tag_configure("head", foreground=FG, font=("Consolas", 9, "bold"))
        self.list.tag_configure("hint", foreground=MUTED)
        self.attach_copy(self.list, label="Список", readonly=True)
        self._write("Список пуст. Выберите папку и нажмите «Проверить».\n", "hint")

    # -- помощники --------------------------------------------------------- #
    def _write(self, text: str, tag: str | None = None) -> None:
        # Не выключаем state в "disabled": иначе виджет перестаёт ловить
        # клавиатуру и Ctrl+C не срабатывает. Защиту от правки даёт attach_copy
        # (readonly=True), которая глушит ввод, но оставляет фокус/выделение.
        self.list.configure(state="normal")
        self.list.insert("end", text, (tag,) if tag else ())

    def _clear_list(self) -> None:
        self.list.configure(state="normal")
        self.list.delete("1.0", "end")

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
GITHUB_DEFAULT_ROOT = str(Path.home())

# --------------------------------------------------------------------------- #
#  Bypass: встроенный движок (GoodbyeDPI + ProxiFyre)
# --------------------------------------------------------------------------- #
BYPASS_DIR = Path(os.environ.get("LOCALAPPDATA") or str(Path.home())) / "Tools" / "bypass"
BYPASS_GBD_DIR = BYPASS_DIR / "goodbyedpi"
BYPASS_PFR_DIR = BYPASS_DIR / "proxifyre"
BYPASS_GBD_EXE = BYPASS_GBD_DIR / "goodbyedpi.exe"
BYPASS_PFR_EXE = BYPASS_PFR_DIR / "ProxiFyre.exe"
# файлы внутри exe — PowerShell-скрипты для скачки (urllib у GitHub ругается на rate-limit реже)
BYPASS_GBD_REPO = "ValdikSS/GoodbyeDPI"
BYPASS_PFR_REPO = "wiresock/proxifyre"
BYPASS_GBD_ASSET = r"goodbyedpi-\d[\w.]*\.zip"
BYPASS_PFR_ASSET = r"ProxiFyre-v\d[\w.]*-x64\.zip"
BYPASS_DEFAULT_ENDPOINT = "socks5://127.0.0.1:1080"

GITHUB_IGNORE_BLOCK = """\
# --- добавлено «Мои инструменты» ---
.workbuddy-ai/
.github/
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


def send_to_recycle_bin(path: str) -> None:
    """Удаляет папку/файл в Корзину Windows (FOF_ALLOWUNDO), а не навсегда.

    Если shell32 недоступен, падает с OSError — вызывающий обработает.
    """
    if sys.platform != "win32":
        if os.path.isdir(path):
            shutil.rmtree(path)
        else:
            os.remove(path)
        return

    class SHFILEOPSTRUCT(ctypes.Structure):
        _fields_ = [
            ("hwnd", ctypes.c_void_p),
            ("wFunc", ctypes.c_uint),
            ("pFrom", ctypes.c_wchar_p),
            ("pTo", ctypes.c_wchar_p),
            ("fFlags", ctypes.c_uint),
            ("fAnyOperationsAborted", ctypes.c_int),
            ("hNameMappings", ctypes.c_void_p),
            ("lpszProgressTitle", ctypes.c_wchar_p),
        ]

    FO_DELETE = 3
    FOF_ALLOWUNDO = 0x40
    FOF_NOCONFIRMATION = 0x10
    FOF_SILENT = 0x4
    FOF_NOERRORUI = 0x400

    buffer = ctypes.create_unicode_buffer(os.path.normpath(path) + "\0\0")
    op = SHFILEOPSTRUCT()
    op.hwnd = 0
    op.wFunc = FO_DELETE
    op.pFrom = ctypes.cast(buffer, ctypes.c_wchar_p)
    op.fFlags = FOF_ALLOWUNDO | FOF_NOCONFIRMATION | FOF_SILENT | FOF_NOERRORUI
    result = ctypes.windll.shell32.SHFileOperationW(ctypes.byref(op))
    if result != 0:
        raise OSError(f"Не удалось отправить в Корзину (код {result})")


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


GH_TOKEN_PREFIXES = ("ghp_", "gho_", "ghu_", "ghs_", "github_pat_")


def gh_hosts_paths() -> list[Path]:
    """Где GitHub CLI хранит токен: %APPDATA% и ~/.config/gh."""
    paths: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        paths.append(Path(appdata) / "GitHub CLI" / "hosts.yml")
    paths.append(Path.home() / ".config" / "gh" / "hosts.yml")
    unique: list[Path] = []
    for path in paths:
        if path not in unique:
            unique.append(path)
    return unique


def token_looks_valid(token: str) -> bool:
    token = token.strip()
    return len(token) >= 20 and token.startswith(GH_TOKEN_PREFIXES)


def mask_token(token: str) -> str:
    token = (token or "").strip()
    if not token:
        return "не найден"
    if len(token) <= 12:
        return token[:4] + "…"
    return f"{token[:6]}…{token[-4:]}"


def gh_token_from_hosts() -> str:
    for path in gh_hosts_paths():
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                text = handle.read()
        except OSError:
            continue
        for line in text.splitlines():
            key, _, value = line.strip().partition(":")
            if key == "oauth_token" and value.strip():
                return value.strip()
    return gh_token()


def gh_save_token(new_token: str) -> list[str]:
    """Меняет oauth_token в hosts.yml, сохраняя резервную копию .bak."""
    new_token = new_token.strip()
    if not token_looks_valid(new_token):
        raise ValueError(
            "Не похоже на токен GitHub.\n"
            "Нужен classic PAT, начинающийся с ghp_ (или github_pat_)."
        )
    updated: list[str] = []
    pattern = re.compile(r"^(\s*)oauth_token:\s*.*$", re.MULTILINE)
    for path in gh_hosts_paths():
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8", newline="") as handle:
                text = handle.read()
        except OSError as exc:
            raise RuntimeError(f"Не удалось прочитать {path}:\n{exc}")
        if not pattern.search(text):
            continue
        new_text = pattern.sub(
            lambda match: f"{match.group(1)}oauth_token: {new_token}", text, count=1
        )
        backup = path.with_name(path.name + ".bak")
        try:
            shutil.copy2(path, backup)
            with path.open("w", encoding="utf-8", newline="") as handle:
                handle.write(new_text)
        except OSError as exc:
            raise RuntimeError(f"Не удалось записать {path}:\n{exc}")
        updated.append(str(path))
    if not updated:
        raise RuntimeError(
            "Не найден hosts.yml GitHub CLI.\n"
            "Сначала выполните в терминале: gh auth login --web --git-protocol https"
        )
    return updated


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


LOCAL_SCAN_SKIP = {
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".workbuddy-ai",
    "AppData", "Windows", "Program Files", "Program Files (x86)", "$Recycle.Bin",
    "System Volume Information", "site-packages", "dist-info", ".cache",
}


def local_scan_roots(primary: str = "") -> list[str]:
    """Где искать локальные репозитории: выбранная папка + обычные места Windows."""
    roots: list[str] = []
    seen: set[str] = set()

    def add(path) -> None:
        if not path:
            return
        try:
            folder = Path(path)
            if not folder.is_dir():
                return
            text = os.path.normpath(str(folder))
        except (OSError, ValueError):
            return
        key = text.lower()
        if key in seen:
            return
        seen.add(key)
        roots.append(text)

    home = Path.home()
    add(primary)
    add(APP_DIR.parent)
    add(home / "Documents")
    add(home / "Desktop")
    add(home / "Downloads")
    add(home)
    return roots


def scan_local_repos(roots, max_depth: int = 4) -> list[dict]:
    """Список локальных git-папок: path, name, remote (имя репозитория из origin)."""
    if isinstance(roots, str):
        roots = [roots]
    found: list[dict] = []
    seen: set[str] = set()
    stack: list[tuple[Path, int]] = [(Path(root), 0) for root in roots]
    while stack:
        folder, depth = stack.pop()
        key = os.path.normcase(str(folder))
        if key in seen:
            continue
        seen.add(key)
        try:
            has_git = (folder / ".git").exists()
        except OSError:
            has_git = False
        if has_git:
            found.append({
                "path": str(folder),
                "name": folder.name,
                "remote": repo_name_from_url(git_remote_url(str(folder))),
            })
        if depth >= max_depth:
            continue
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        for entry in entries:
            try:
                if not entry.is_dir(follow_symlinks=False):
                    continue
                if entry.name in LOCAL_SCAN_SKIP or entry.name.startswith("."):
                    continue
                stack.append((Path(entry.path), depth + 1))
            except OSError:
                continue
    return found


_TRANSLIT = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e",
    "ж": "zh", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "kh", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sch",
    "ъ": "", "ы": "y", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "і": "i", "ї": "yi", "є": "ye", "ґ": "g",
}


def sanitize_repo_name(name: str) -> str:
    # GitHub не принимает не-ASCII имена — кириллицу переводим в латиницу
    transliterated = "".join(_TRANSLIT.get(ch.lower(), ch) for ch in name)
    allowed = "-_. "
    cleaned = "".join(ch for ch in transliterated if ch.isalnum() or ch in allowed)
    cleaned = "-".join(part for part in cleaned.split() if part)
    while "--" in cleaned:
        cleaned = cleaned.replace("--", "-")
    return cleaned.strip("-._") or "repo"


def match_key(text: str) -> str:
    """Ключ сравнения имён: «тулы» и «tuly» дают одно и то же."""
    text = "".join(_TRANSLIT.get(ch.lower(), ch) for ch in (text or ""))
    return "".join(ch for ch in text if ch.isalnum())


def match_local_repos(repos: list[dict], local_items: list[dict]) -> tuple[dict[str, str], list[dict]]:
    """Сопоставляет репозитории GitHub с папками на ПК.

    Сначала по origin-ссылке (надёжно), затем по имени папки с учётом
    транслитерации: репозиторий tuly ↔ папка «тулы».
    """
    matches: dict[str, str] = {}
    used: set[str] = set()
    for repo in repos:
        key = match_key(repo.get("name", ""))
        if not key:
            continue
        for item in local_items:
            if item["path"] in used:
                continue
            if item.get("remote") and match_key(item["remote"]) == key:
                matches[repo["full"]] = item["path"]
                used.add(item["path"])
                break
    for repo in repos:
        if repo["full"] in matches:
            continue
        key = match_key(repo.get("name", ""))
        if not key:
            continue
        for item in local_items:
            if item["path"] in used:
                continue
            if match_key(item["name"]) == key:
                matches[repo["full"]] = item["path"]
                used.add(item["path"])
                break
    rest = [item for item in local_items if item["path"] not in used]
    return matches, rest


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


# --------------------------------------------------------------------------- #
#  Bypass: движок (статусы, админ, скачка, запуск через UAC)
# --------------------------------------------------------------------------- #
def bypass_cfg() -> dict:
    base = config_read().get("bypass") or {}
    apps = base.get("apps") or []
    endpoint = str(base.get("endpoint") or BYPASS_DEFAULT_ENDPOINT).strip()
    if not endpoint:
        endpoint = BYPASS_DEFAULT_ENDPOINT
    return {
        "endpoint": endpoint,
        "apps": [a.strip() for a in apps if str(a).strip()],
        "gbd_args": str(base.get("gbd_args") or "-5").strip(),
    }


def bypass_cfg_save(**updates) -> None:
    config_update(bypass={**bypass_cfg(), **updates})


def bypass_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:  # noqa: BLE001
        return False


def _image_pid(image: str) -> int:
    """PID процесса по имени образа (tasklist), 0 — не найден."""
    try:
        rc, out, _ = run_tool(
            ["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"], timeout=30,
        )
    except Exception:  # noqa: BLE001
        return 0
    if rc != 0 or not out or "INFO:" in out.upper():
        return 0
    try:
        return int(out.splitlines()[0].split(",")[1].strip().strip('"'))
    except (ValueError, IndexError):
        return 0


def _elevate(exe: str, params: str = "", cwd: str = "") -> bool:
    """Запуск с запросом прав администратора (UAC). True — UAC пройден."""
    code = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, cwd or None, 0)
    return code > 32


def _elevate_kill(image: str) -> None:
    # taskkill может быть отказан без прав — поднимаем его же через UAC
    ok = _elevate("taskkill.exe", f"/F /IM {image}")
    if ok:
        time.sleep(1.0)
        if _image_pid(image):
            raise RuntimeError(f"{image} не удалось остановить")
    else:
        raise RuntimeError(f"{image} запущен от администратора — UAC отклонён")


def bypass_status(_: object = None) -> dict:
    cfg = bypass_cfg()
    pfr_on = _image_pid("ProxiFyre.exe")
    gbd_on = _image_pid("goodbyedpi.exe")
    return {
        "admin": bypass_admin(),
        "endpoint": cfg["endpoint"],
        "apps": cfg["apps"],
        "happ": {
            "on": pfr_on > 0,
            "installed": BYPASS_PFR_EXE.exists(),
        },
        "gbd": {
            "on": gbd_on > 0,
            "installed": BYPASS_GBD_EXE.exists(),
            "args": cfg["gbd_args"],
        },
    }


def bypass_download(kind: str) -> dict:
    """Скачивает и распаковывает GoodbyeDPI или ProxiFyre в %LOCALAPPDATA%."""
    if kind == "gbd":
        repo, pattern, target = BYPASS_GBD_REPO, BYPASS_GBD_ASSET, BYPASS_GBD_DIR
    else:
        repo, pattern, target = BYPASS_PFR_REPO, BYPASS_PFR_ASSET, BYPASS_PFR_DIR
    dl_dir = BYPASS_DIR / "downloads"
    dl_dir.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True, exist_ok=True)
    zip_path = dl_dir / f"{kind}.zip"

    script = "\n".join([
        "$ProgressPreference = 'SilentlyContinue'",
        "[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12",
        f"$api = 'https://api.github.com/repos/{repo}/releases/latest'",
        "$rel = Invoke-RestMethod $api",
        f"$asset = $rel.assets | Where-Object {{ $_.name -match '{pattern}' }} | Select-Object -First 1",
        "if (-not $asset) { throw 'подходящий архив не найден в релизе' }",
        f"Invoke-WebRequest $asset.browser_download_url -OutFile '{zip_path}' -UseBasicParsing",
        "Write-Output $asset.name",
    ])
    name = ps_run(script, timeout=900)
    if not zip_path.exists() or zip_path.stat().st_size == 0:
        raise RuntimeError("архив не скачался")

    tmp = dl_dir / f"{kind}-unzip"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    with zipfile.ZipFile(zip_path) as archive:
        archive.extractall(tmp)

    # exe ищем рекурсивно (в архиве бывает вложенная папка)
    exe_name = "goodbyedpi.exe" if kind == "gbd" else "ProxiFyre.exe"
    found = list(tmp.rglob(exe_name))
    if not found:
        raise RuntimeError(f"в архиве нет {exe_name}")
    exe_dir = found[0].parent
    for item in exe_dir.iterdir():
        if item.is_dir():
            shutil.copytree(item, target / item.name, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target / item.name)
    shutil.rmtree(tmp, ignore_errors=True)
    if not (target / exe_name).exists():
        raise RuntimeError("файлы не распакованы")
    return {"ok": True, "detail": f"Скачан {name.strip() or kind}"}


def bypass_gbd_start() -> dict:
    if not BYPASS_GBD_EXE.exists():
        raise RuntimeError("GoodbyeDPI не скачан — нажмите «Скачать GoodbyeDPI»")
    if _image_pid("goodbyedpi.exe"):
        return {"ok": True, "detail": "DPI уже запущен"}
    exe = str(BYPASS_GBD_EXE)
    params = f'"{exe}" {bypass_cfg()["gbd_args"]}'
    if bypass_admin():
        subprocess.Popen([exe, *bypass_cfg()["gbd_args"].split()], cwd=str(BYPASS_GBD_DIR),
                         creationflags=NO_WINDOW)
    elif not _elevate(exe, bypass_cfg()["gbd_args"], str(BYPASS_GBD_DIR)):
        raise RuntimeError("UAC отклонён — без прав администратора DPI не запустить")
    time.sleep(2.0)
    if not _image_pid("goodbyedpi.exe"):
        raise RuntimeError("goodbyedpi.exe не запустился (галочку в UAC, драйвер WinDivert?)")
    return {"ok": True, "detail": "GoodbyeDPI запущен (админ)"}


def bypass_gbd_stop() -> dict:
    if not _image_pid("goodbyedpi.exe"):
        return {"ok": True, "detail": "DPI и так был остановлен"}
    rc, _out, _err = run_tool(["taskkill", "/F", "/IM", "goodbyedpi.exe"], timeout=30)
    if rc != 0 or _image_pid("goodbyedpi.exe"):
        _elevate_kill("goodbyedpi.exe")
    return {"ok": True, "detail": "GoodbyeDPI остановлен"}


def bypass_processes() -> dict:
    """Список запущенных программ (имена .exe, уникальные, отсортированные)."""
    out = ps_run(
        "(Get-Process | Where-Object { $_.Path } | ForEach-Object { Split-Path -Leaf $_.Path } "
        "| Sort-Object -Unique) -join ';'",
        timeout=60,
    )
    names = sorted({p.strip() for p in out.split(";") if p.strip()})
    return {"ok": True, "processes": names}


def bypass_write_app_config(cfg: dict) -> None:
    endpoint = cfg["endpoint"]
    # "socks5://host:port" → "host:port"
    for prefix in ("socks5://", "socks://"):
        if endpoint.lower().startswith(prefix):
            endpoint = endpoint[len(prefix):]
            break
    payload = {
        "logLevel": "Info",
        "bypassLan": True,
        "proxies": [{
            "appNames": cfg["apps"],
            "socks5ProxyEndpoint": endpoint,
            "supportedProtocols": ["TCP"] if not cfg.get("udp", True) else ["TCP", "UDP"],
        }],
    }
    (BYPASS_PFR_DIR / "app-config.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
    )


def bypass_apps_start() -> dict:
    if not BYPASS_PFR_EXE.exists():
        raise RuntimeError("ProxiFyre не скачан — нажмите «Скачать ProxiFyre»")
    cfg = bypass_cfg()
    if not cfg["apps"]:
        raise RuntimeError("Приложения не выбраны — «Настроить» → список exe")
    if _image_pid("ProxiFyre.exe"):
        # перезагружаем настройки
        run_tool(["taskkill", "/F", "/IM", "ProxiFyre.exe"], timeout=30)
        time.sleep(1.0)
    bypass_write_app_config(cfg)
    exe = str(BYPASS_PFR_EXE)
    if bypass_admin():
        subprocess.Popen([exe], cwd=str(BYPASS_PFR_DIR), creationflags=NO_WINDOW)
    elif not _elevate(exe, "", str(BYPASS_PFR_DIR)):
        raise RuntimeError("UAC отклонён — без прав администратора ProxiFyre не работает")
    time.sleep(2.0)
    if not _image_pid("ProxiFyre.exe"):
        raise RuntimeError("ProxiFyre не запустился (нужен драйвер Windows Packet Filter)")
    return {"ok": True, "detail": f"ProxiFyre запущен: {', '.join(cfg['apps'])}"}


def bypass_apps_stop() -> dict:
    if not _image_pid("ProxiFyre.exe"):
        return {"ok": True, "detail": "ProxiFyre и так не был запущен"}
    rc, _out, _err = run_tool(["taskkill", "/F", "/IM", "ProxiFyre.exe"], timeout=30)
    if rc != 0 or _image_pid("ProxiFyre.exe"):
        _elevate_kill("ProxiFyre.exe")
    return {"ok": True, "detail": "ProxiFyre остановлен"}


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
        self.local_items: list[dict] = []
        self.matches: dict[str, str] = {}
        self.local_only: list[dict] = []
        self.rows: dict[str, dict] = {}
        self.scan_roots: list[str] = []
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
        self.btn_login = flat_button(top, "Войти в GitHub", self.login_browser, kind="ghost")
        self.btn_login.pack(side="right", padx=(0, px(8)))

        path_row = ttk.Frame(self)
        path_row.pack(fill="x", pady=(px(10), 0))
        ttk.Label(path_row, text="Искать локальные папки в:", style="Muted.TLabel").pack(side="left")
        self.root_var = tk.StringVar(value=self.root)
        ttk.Label(path_row, textvariable=self.root_var, style="Muted.TLabel").pack(
            side="left", padx=(px(8), px(8))
        )
        flat_button(path_row, "Изменить…", self.choose_root, kind="ghost").pack(side="left")
        ttk.Label(
            self,
            text="Плюс всегда проверяются Документы, Рабочий стол, Загрузки и домашняя папка.",
            style="Muted.TLabel",
        ).pack(anchor="w", pady=(px(4), 0))

        search_row = ttk.Frame(self)
        search_row.pack(fill="x", pady=(px(10), px(6)))
        ttk.Label(search_row, text="Поиск:").pack(side="left")
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_args: self._render())
        ttk.Entry(search_row, textvariable=self.search_var).pack(
            side="left", fill="x", expand=True, padx=(px(8), px(10))
        )
        self.only_diff_var = tk.BooleanVar(value=False)
        dark_check(search_row, "только расхождения", self.only_diff_var,
                   bg=BG, command=self._render).pack(side="left")

        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill="both", expand=True)
        columns = ("name", "access", "state", "local")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=10,
                                 selectmode="browse")
        self.tree.heading("name", text="Репозиторий")
        self.tree.heading("access", text="Доступ")
        self.tree.heading("state", text="Где есть")
        self.tree.heading("local", text="Папка на ПК")
        self.tree.column("name", width=px(190), anchor="w")
        self.tree.column("access", width=px(90), anchor="center", stretch=False)
        self.tree.column("state", width=px(150), anchor="w", stretch=False)
        self.tree.column("local", width=px(420), anchor="w", stretch=False)
        self.tree.tag_configure("both", foreground=FG)
        self.tree.tag_configure("gh_only", foreground=WARN)
        self.tree.tag_configure("local_only", foreground=ACCENT)
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
        self.btn_push_sel = flat_button(actions, "Залить эту папку", self.push_selected,
                                        kind="ghost")
        self.btn_push_sel.pack(side="left", padx=(px(8), 0))
        self.btn_web = flat_button(actions, "Открыть на GitHub", self.open_web, kind="ghost")
        self.btn_web.pack(side="left", padx=(px(8), 0))
        self.btn_copy = flat_button(actions, "Скопировать список", self.copy_list, kind="ghost")
        self.btn_copy.pack(side="right")

        actions2 = ttk.Frame(self)
        actions2.pack(fill="x", pady=(px(6), 0))
        self.btn_visibility = flat_button(actions2, "Видимость…", self.set_visibility, kind="ghost")
        self.btn_visibility.pack(side="left")
        self.btn_delete = flat_button(actions2, "Удалить с GitHub", self.delete_repo, kind="danger")
        self.btn_delete.pack(side="left", padx=(px(8), 0))
        self.btn_delete_folder = flat_button(actions2, "Удалить папку", self.delete_folder,
                                             kind="danger")
        self.btn_delete_folder.pack(side="left", padx=(px(8), 0))

        self.summary_var = tk.StringVar(value="Загружаю список…")
        ttk.Label(self, textvariable=self.summary_var, style="Muted.TLabel").pack(
            anchor="w", pady=(px(10), px(6))
        )

        log_frame, self.log_box = self.make_log(self, height=7, label="Лог GitHub")
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
        text = str(exc).lower()
        if "delete_repo" in text or "не выполнен вход" in text:
            if messagebox.askyesno(
                "Вход в GitHub",
                "Войти через браузер? Это даст права и на заливку, и на удаление "
                "репозиториев — без токенов.",
                parent=self,
            ):
                self.login_browser(confirm=False)

    def _selected_row(self) -> dict | None:
        selection = self.tree.selection()
        if not selection:
            return None
        return self.rows.get(selection[0])

    def _selected_repo(self) -> dict | None:
        row = self._selected_row()
        return row["repo"] if row else None

    def _local_for(self, row: dict | None) -> str:
        return row["local"] if row else ""

    def _display_path(self, local: str) -> str:
        if not local:
            return "—"
        try:
            return "~\\" + str(Path(local).relative_to(Path.home()))
        except (ValueError, OSError):
            return local

    def _update_actions(self) -> None:
        row = self._selected_row()
        repo = row["repo"] if row else None
        local = self._local_for(row)
        state = "disabled" if self.busy else "normal"
        self.btn_refresh.configure(state=state)
        self.btn_push.configure(state=state)
        self.btn_login.configure(state=state)
        self.btn_all.configure(state=state if self.repos else "disabled")
        self.btn_copy.configure(state="normal" if self.rows and not self.busy else "disabled")
        self.btn_open.configure(state="normal" if local and not self.busy else "disabled")
        self.btn_push_sel.configure(state="normal" if local and not self.busy else "disabled")
        self.btn_download.configure(
            state="normal" if repo and not local and not self.busy else "disabled"
        )
        self.btn_web.configure(state="normal" if repo and not self.busy else "disabled")
        self.btn_delete.configure(state="normal" if repo and not self.busy else "disabled")
        self.btn_delete_folder.configure(
            state="normal" if local and not self.busy else "disabled"
        )
        vis_state = "normal" if repo and not self.busy else "disabled"
        self.btn_visibility.configure(state=vis_state)
        if repo:
            self.btn_visibility.configure(
                text="Сделать публичным" if repo.get("private") else "Сделать приватным"
            )
        else:
            self.btn_visibility.configure(text="Видимость…")

    def _render(self) -> None:
        query = self.search_var.get().strip().lower()
        only_diff = bool(self.only_diff_var.get())
        previous = self.tree.selection()
        previous_iid = previous[0] if previous else ""
        self.tree.delete(*self.tree.get_children())
        self.rows = {}

        matched = sum(1 for repo in self.repos if repo["full"] in self.matches)
        gh_only = len(self.repos) - matched
        shown = 0

        for repo in self.repos:
            local = self.matches.get(repo["full"], "")
            haystack = f"{repo['name']} {repo['full']} {local}".lower()
            if query and query not in haystack:
                continue
            if only_diff and local:
                continue
            iid = repo["full"]
            tag = "both" if local else "gh_only"
            self.tree.insert(
                "", "end", iid=iid, tags=(tag,),
                values=(
                    repo["name"],
                    "приватный" if repo["private"] else "публичный",
                    "● на ПК" if local else "○ только на GitHub",
                    self._display_path(local),
                ),
            )
            self.rows[iid] = {"iid": iid, "repo": repo, "local": local}
            shown += 1

        for item in self.local_only:
            haystack = f"{item['name']} {item['path']}".lower()
            if query and query not in haystack:
                continue
            iid = "local::" + item["path"]
            self.tree.insert(
                "", "end", iid=iid, tags=("local_only",),
                values=(item["name"], "—", "◆ только на ПК", self._display_path(item["path"])),
            )
            self.rows[iid] = {"iid": iid, "repo": None, "local": item["path"]}
            shown += 1

        if previous_iid and self.tree.exists(previous_iid):
            self.tree.selection_set(previous_iid)
        summary = (
            f"На GitHub: {len(self.repos)} · есть на ПК: {matched} · "
            f"только на GitHub: {gh_only} · папок без GitHub: {len(self.local_only)}"
        )
        if query or only_diff:
            summary += f" · показано: {shown}"
        self.summary_var.set(summary)
        self._update_actions()

    def copy_list(self) -> None:
        """Копирует таблицу текстом: репозиторий, где есть, путь на ПК."""
        lines = ["Репозиторий\tДоступ\tГде есть\tПапка на ПК"]
        for iid in self.tree.get_children():
            row = self.rows.get(iid) or {}
            repo = row.get("repo")
            values = self.tree.item(iid, "values")
            lines.append("\t".join(str(value) for value in values))
        self.copy_text("\n".join(lines), label="Список GitHub")

    # -- вход через браузер ------------------------------------------------ #
    def login_browser(self, confirm: bool = True) -> None:
        """Вход в GitHub через браузер: без токенов и копирования строк."""
        if self.busy:
            return
        gh = find_tool("gh")
        if not gh:
            messagebox.showerror(
                "GitHub", "GitHub CLI (gh) не найден. Установите с cli.github.com.", parent=self
            )
            return
        if confirm and not messagebox.askyesno(
            "Вход в GitHub",
            "Откроется браузер.\n\n"
            "1. Войди в GitHub (если ещё не вошёл).\n"
            "2. Нажми «Authorize».\n\n"
            "Код для входа приложение скопирует в буфер обмена.\n"
            "Этот вход сразу даёт право удалять репозитории.",
            parent=self,
        ):
            return
        self._start_busy("GitHub: вход через браузер…")
        self.append_log(self.log_box, "открываю браузер для входа…")

        def work():
            proc = subprocess.Popen(
                [gh, "auth", "login", "--web", "-h", "github.com",
                 "--git-protocol", "https", "--skip-ssh-key",
                 "-s", "repo", "-s", "delete_repo", "-s", "gist"],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=tool_env(),
                creationflags=NO_WINDOW,
            )
            try:
                if proc.stdin:
                    proc.stdin.write("\n")     # gh ждёт Enter перед открытием браузера
                    proc.stdin.flush()
            except OSError:
                pass
            for line in proc.stdout:
                line = line.strip()
                if line:
                    self._log_bg(line)
            return proc.wait()

        self.hub.run_bg(work, self._login_done, self._fail)

    def _login_done(self, code: int) -> None:
        self._finish()
        if code == 0:
            self.append_log(self.log_box, "✅ вход в GitHub выполнен", "ok")
            self.hub.set_status("GitHub: вход выполнен")
            messagebox.showinfo(
                "Вход в GitHub",
                "Готово. Теперь работают и заливка, и удаление репозиториев.",
                parent=self,
            )
            self.refresh()
        else:
            self.append_log(self.log_box, "❌ вход не выполнен", "error")
            self.hub.set_status("GitHub: вход не выполнен")
            messagebox.showerror(
                "Вход в GitHub",
                "Браузер не подтвердил вход.\nНажмите «Войти в GitHub» и повторите.",
                parent=self,
            )

    # -- токен ------------------------------------------------------------- #
    def token_dialog(self) -> None:
        if self.busy:
            return
        try:
            current = gh_token_from_hosts()
        except Exception:  # noqa: BLE001
            current = ""

        initial = ""
        try:
            clip = self.clipboard_get().strip()
            if token_looks_valid(clip):
                initial = clip
        except tk.TclError:
            pass

        win = tk.Toplevel(self)
        win.withdraw()
        win.title("Токен GitHub")
        win.configure(bg=CARD)
        win.transient(self)
        win.resizable(False, False)

        body = tk.Frame(win, bg=CARD, padx=px(22), pady=px(18))
        body.pack(fill="both", expand=True)
        tk.Label(body, text="Токен GitHub", bg=CARD, fg=FG,
                 font=("Segoe UI", 12, "bold"), anchor="w").pack(fill="x")
        tk.Label(
            body,
            text=("Удаление репозиториев требует права delete_repo. Создайте "
                  "classic PAT со scope repo и delete_repo, скопируйте его и "
                  "вставьте здесь — приложение заменит токен в hosts.yml GitHub CLI."),
            bg=CARD, fg=MUTED, justify="left", anchor="w",
            wraplength=px(520), font=("Segoe UI", 9),
        ).pack(fill="x", pady=(px(10), 0))

        tk.Label(body, text=f"Сейчас: {mask_token(current)}", bg=CARD, fg=ACCENT,
                 anchor="w", font=("Consolas", 9)).pack(fill="x", pady=(px(10), 0))

        link = tk.Label(body, text="Создать токен на GitHub ↗", bg=CARD, fg=ACCENT,
                        cursor="hand2", font=("Segoe UI", 9, "underline"), anchor="w")
        link.pack(fill="x", pady=(px(6), 0))
        link.bind(
            "<Button-1>",
            lambda _event: webbrowser.open(
                "https://github.com/settings/tokens/new?scopes=repo,delete_repo"
                "&description=Tools%20GitHub"
            ),
        )

        value = tk.StringVar(value=initial)
        entry = ttk.Entry(body, textvariable=value, show="•")
        entry.pack(fill="x", pady=(px(10), 0), ipady=px(2))

        show_var = tk.BooleanVar(value=not bool(initial))

        def toggle_show() -> None:
            entry.configure(show="" if show_var.get() else "•")

        dark_check(body, "Показать токен", show_var, bg=CARD,
                   command=toggle_show).pack(anchor="w", pady=(px(8), 0))

        def close() -> None:
            win.destroy()

        def save() -> None:
            token = value.get().strip()
            if not token_looks_valid(token):
                messagebox.showerror(
                    "Токен GitHub",
                    "Не похоже на токен.\n\nНужен classic PAT, начинающийся с ghp_, "
                    "со scope repo и delete_repo.",
                    parent=win,
                )
                entry.focus_set()
                return
            try:
                paths = gh_save_token(token)
            except (ValueError, RuntimeError) as exc:
                messagebox.showerror("Токен GitHub", str(exc), parent=win)
                return
            win.destroy()
            self.append_log(
                self.log_box, "Токен GitHub обновлён: " + ", ".join(paths), "ok"
            )
            self.hub.set_status("GitHub: токен обновлён")
            messagebox.showinfo(
                "Токен GitHub",
                "Токен заменён в:\n" + "\n".join(paths)
                + "\n\nРезервная копия: hosts.yml.bak\nОбновляю список…",
                parent=self,
            )
            self.refresh()

        buttons = tk.Frame(body, bg=CARD)
        buttons.pack(fill="x", pady=(px(18), 0))
        flat_button(buttons, "Сохранить", save).pack(side="right")
        flat_button(buttons, "Отмена", close, kind="ghost").pack(
            side="right", padx=(0, px(8))
        )

        win.bind("<Return>", lambda _event: save())
        win.bind("<Escape>", lambda _event: close())
        win.protocol("WM_DELETE_WINDOW", close)
        win.update_idletasks()
        width, height = win.winfo_reqwidth(), win.winfo_reqheight()
        x = self.winfo_rootx() + max(0, (self.winfo_width() - width) // 2)
        y = self.winfo_rooty() + max(0, (self.winfo_height() - height) // 3)
        win.geometry(f"+{x}+{y}")
        enable_dark_titlebar(win)
        win.deiconify()
        win.grab_set()
        win.focus_force()
        entry.focus_set()
        if initial:
            entry.select_range(0, "end")
        win.wait_window()

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
        roots = local_scan_roots(self.root)
        self.scan_roots = roots

        def work():
            if not gh_authed():
                raise RuntimeError(
                    "Не выполнен вход в GitHub.\nВыполните в терминале:\n\ngh auth login --web"
                )
            return {
                "login": gh_login(),
                "repos": github_repos(),
                "local": scan_local_repos(roots),
            }

        self.hub.run_bg(work, self._refresh_done, self._fail)

    def _refresh_done(self, data: dict) -> None:
        self.login = data.get("login") or ""
        self.repos = data.get("repos") or []
        self.local_items = data.get("local") or []
        self.matches, self.local_only = match_local_repos(self.repos, self.local_items)
        self.login_var.set(f"@{self.login}" if self.login else "")
        self._finish()
        self._render()
        self.hub.set_status("GitHub: список обновлён")
        self._log_report()

    def _log_report(self) -> None:
        """Короткий отчёт в лог: что есть где — его можно скопировать."""
        matched = len(self.matches)
        gh_only = [repo for repo in self.repos if repo["full"] not in self.matches]
        self.append_log(
            self.log_box,
            f"=== Сверка: на GitHub {len(self.repos)}, на ПК {len(self.local_items)}, "
            f"совпало {matched} ===",
        )
        for repo in self.repos:
            local = self.matches.get(repo["full"], "")
            if local:
                self.append_log(self.log_box, f"✔ {repo['name']} → {local}", "ok")
        for repo in gh_only[:40]:
            self.append_log(self.log_box, f"✖ нет на ПК: {repo['name']}")
        if len(gh_only) > 40:
            self.append_log(self.log_box, f"… ещё {len(gh_only) - 40} репозиториев без папки")
        for item in self.local_only[:40]:
            self.append_log(self.log_box, f"◆ нет на GitHub: {item['path']}")
        if len(self.local_only) > 40:
            self.append_log(self.log_box, f"… ещё {len(self.local_only) - 40} локальных папок")

    # -- заливка ----------------------------------------------------------- #
    def push_folder(self) -> None:
        if self.busy:
            return
        folder = filedialog.askdirectory(
            title="Папку с каким проектом залить на GitHub?", initialdir=self.root, mustexist=True
        )
        if not folder:
            return
        self._push(folder)

    def push_selected(self) -> None:
        row = self._selected_row()
        folder = self._local_for(row)
        if not folder or self.busy:
            messagebox.showinfo("GitHub", "У выбранной строки нет папки на ПК.", parent=self)
            return
        self._push(folder)

    def _push(self, folder: str) -> None:
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
        targets = [repo for repo in self.repos if self.matches.get(repo["full"])]
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
                folder = self.matches[repo["full"]]
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
                        "У входа нет права delete_repo.\n\n"
                        "Нажмите «Войти в GitHub» и подтвердите вход в браузере — "
                        "это даст право удалять репозитории."
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
        local = self._local_for(self._selected_row())
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

    # -- смена видимости ---------------------------------------------------- #
    def set_visibility(self) -> None:
        repo = self._selected_repo()
        if not repo or self.busy:
            return
        make_public = bool(repo.get("private"))
        target = "public" if make_public else "private"
        verb = "публичным" if make_public else "приватным"
        if not messagebox.askyesno(
            "Сменить видимость",
            f"{repo['full']}\n\nСделать репозиторий {verb}?",
            parent=self,
        ):
            return
        self._start_busy(f"GitHub: делаю {verb}…")
        gh = find_tool("gh")
        full = repo["full"]

        def work():
            rc, out, err = run_tool(
                [gh, "repo", "edit", full, "--visibility", target], timeout=120
            )
            if rc != 0:
                raise RuntimeError(err or out or "не удалось изменить видимость")
            return target

        self.hub.run_bg(work, self._visibility_done, self._fail)

    def _visibility_done(self, target: str) -> None:
        self._finish()
        if target == "public":
            self.append_log(self.log_box, "🔓 репозиторий стал публичным", "ok")
            self.hub.set_status("GitHub: публичный")
        else:
            self.append_log(self.log_box, "🔒 репозиторий стал приватным", "ok")
            self.hub.set_status("GitHub: приватный")
        self.refresh()

    # -- удаление локальной папки ------------------------------------------ #
    def delete_folder(self) -> None:
        row = self._selected_row()
        folder = self._local_for(row)
        if not folder or self.busy:
            return
        if not Path(folder).exists():
            messagebox.showerror("GitHub", f"Папка уже отсутствует:\n{folder}", parent=self)
            return
        repo = row.get("repo")
        extra = (
            f"\nСвязанный репозиторий на GitHub: {repo['full']} (не удаляется)."
            if repo else
            "\nСвязанного репозитория на GitHub нет."
        )
        if not messagebox.askyesno(
            "Удалить папку?",
            f"Папка будет отправлена в Корзину Windows (можно восстановить):\n"
            f"{folder}{extra}\n\n"
            "Она исчезнет с диска, но останется в Корзине.",
            parent=self,
        ):
            return
        self._start_busy("GitHub: удаляю папку…")
        target = folder

        def work():
            send_to_recycle_bin(target)
            return target

        self.hub.run_bg(work, self._delete_folder_done, self._fail)

    def _delete_folder_done(self, folder: str) -> None:
        self._finish()
        self.local_items = [it for it in self.local_items if it["path"] != folder]
        self.local_only = [it for it in self.local_only if it["path"] != folder]
        self.matches = {k: v for k, v in self.matches.items() if v != folder}
        self.append_log(self.log_box, f"🗑 папка в Корзине: {folder}", "ok")
        self.hub.set_status("GitHub: папка удалена (Корзина)")
        self._render()


# --------------------------------------------------------------------------- #
#  Панель: Bypass (встроенный движок)
# --------------------------------------------------------------------------- #
class BypassPanel(Panel):
    def __init__(self, master: tk.Misc, hub) -> None:
        super().__init__(master, hub)
        self.online = False
        self.busy = False
        self.checking = False
        self.state: dict = {}
        self._offline_noted = False
        self._build()
        self.refresh_now()
        self.after(3000, self._tick)

    def _build(self) -> None:
        engine_row = ttk.Frame(self)
        engine_row.pack(fill="x")
        ttk.Label(
            engine_row,
            text=f"Движок встроен · {BYPASS_DIR}",
            style="Muted.TLabel",
        ).pack(side="left")

        card = tk.Frame(self, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        card.pack(fill="x", pady=(px(12), 0))
        inner = tk.Frame(card, bg=CARD)
        inner.pack(fill="x", padx=px(16), pady=px(14))

        self.panel_var = tk.StringVar(value="проверяю…")
        self.apps_var = tk.StringVar(value="—")
        self.happ_var = tk.StringVar(value="—")
        self.gbd_var = tk.StringVar(value="—")
        for title, variable in (
            ("Права:", self.panel_var),
            ("Приложения:", self.apps_var),
            ("Прокси:", self.happ_var),
            ("GoodbyeDPI:", self.gbd_var),
        ):
            row = tk.Frame(inner, bg=CARD)
            row.pack(fill="x", pady=(px(6) if title != "Права:" else 0, 0))
            tk.Label(row, text=title, bg=CARD, fg=MUTED, width=12, anchor="w",
                     font=("Segoe UI", 9)).pack(side="left")
            tk.Label(row, textvariable=variable, bg=CARD, fg=FG, anchor="w",
                     justify="left", wraplength=px(760), font=("Segoe UI", 9)).pack(
                side="left", fill="x", expand=True
            )

        controls = ttk.Frame(self)
        controls.pack(fill="x", pady=(px(14), px(6)))
        self.btn_apps_on = flat_button(controls, "По приложениям вкл",
                                       lambda: self.toggle("happ", True))
        self.btn_apps_on.pack(side="left")
        self.btn_apps_off = flat_button(controls, "По приложениям выкл",
                                        lambda: self.toggle("happ", False), kind="ghost")
        self.btn_apps_off.pack(side="left", padx=(px(8), px(18)))
        self.btn_gbd_on = flat_button(controls, "DPI вкл", lambda: self.toggle("gbd", True))
        self.btn_gbd_on.pack(side="left")
        self.btn_gbd_off = flat_button(controls, "DPI выкл", lambda: self.toggle("gbd", False),
                                       kind="ghost")
        self.btn_gbd_off.pack(side="left", padx=(px(8), px(18)))
        self.btn_refresh = flat_button(controls, "Обновить", self.refresh_now, kind="ghost")
        self.btn_refresh.pack(side="right")

        extras = ttk.Frame(self)
        extras.pack(fill="x", pady=(px(4), 0))
        self.btn_settings = flat_button(extras, "Приложения и прокси…", self.configure_dialog,
                                        kind="ghost")
        self.btn_settings.pack(side="left")
        self.btn_dl_gbd = flat_button(extras, "Скачать GoodbyeDPI",
                                      lambda: self.download("gbd"), kind="ghost")
        self.btn_dl_gbd.pack(side="left", padx=(px(8), 0))
        self.btn_dl_pfr = flat_button(extras, "Скачать ProxiFyre",
                                      lambda: self.download("pfr"), kind="ghost")
        self.btn_dl_pfr.pack(side="left", padx=(px(8), 0))

        ttk.Label(
            self,
            text="GoodbyeDPI — обход DPI для всего интернета (нужны права администратора). "
                 "«По приложениям» — ProxiFyre: трафик программ, выбранных в "
                 "«Приложения и прокси…», уходит в SOCKS5-прокси (адрес задаётся там же).",
            style="Muted.TLabel",
            wraplength=px(900),
            justify="left",
        ).pack(anchor="w", pady=(px(12), 0))

        log_frame, self.log_box = self.make_log(self, height=8)
        log_frame.pack(fill="both", expand=True, pady=(px(10), 0))
        self.append_log(self.log_box, "Проверяю статусы движка…")

    # -- статус ------------------------------------------------------------ #
    def _tick(self) -> None:
        if not self.winfo_exists():
            return
        if not self.busy and not self.checking:
            self.checking = True
            self.hub.run_bg(bypass_status, self._state_ok, self._state_off)
        self.after(3000, self._tick)

    def refresh_now(self) -> None:
        if self.checking or self.busy:
            return
        self.checking = True
        self.hub.run_bg(bypass_status, self._state_ok, self._state_off)

    def _state_ok(self, state: dict) -> None:
        self.checking = False
        self.online = True
        self._offline_noted = False
        self.state = state if isinstance(state, dict) else {}
        self.panel_var.set(
            "есть (запущено от администратора)" if self.state.get("admin")
            else "нет — при включении появится окно UAC"
        )
        apps = self.state.get("apps") or []
        self.apps_var.set(", ".join(apps) if apps else "не выбраны · кнопка «Приложения и прокси…»")
        cfg = self.state.get("happ") or {}
        detail = f"{self.state.get('endpoint') or BYPASS_DEFAULT_ENDPOINT} · "
        detail += "ProxiFyre запущен" if cfg.get("on") else "ProxiFyre остановлен"
        detail += "" if cfg.get("installed") else " · ProxiFyre не скачан"
        self.happ_var.set(detail)
        gbd = self.state.get("gbd") or {}
        detail = "запущен" if gbd.get("on") else "остановлен"
        detail += "" if gbd.get("installed") else " · не скачан"
        if gbd.get("args"):
            detail += f" · аргументы {gbd['args']}"
        self.gbd_var.set(detail)
        self._update_controls()

    def _state_off(self, exc: Exception) -> None:
        self.checking = False
        was_online = self.online
        self.online = False
        self.state = {}
        self.panel_var.set(f"ошибка: {exc}")
        self.apps_var.set("—")
        self.happ_var.set("—")
        self.gbd_var.set("—")
        if not self._offline_noted:
            self._offline_noted = True
            self.append_log(self.log_box, f"Не удалось опросить движок: {exc}", "error")
        if was_online:
            self.hub.set_status("Bypass: движок не отвечает")
        self._update_controls()

    def _update_controls(self) -> None:
        busy = self.busy
        online = self.online
        self.btn_settings.configure(state="disabled" if busy else "normal")
        self.btn_refresh.configure(state="disabled" if busy or self.checking else "normal")
        toggles = "normal" if online and not busy else "disabled"
        self.btn_apps_on.configure(state=toggles)
        self.btn_apps_off.configure(state=toggles)
        self.btn_gbd_on.configure(state=toggles)
        self.btn_gbd_off.configure(state=toggles)
        for button in (self.btn_dl_gbd, self.btn_dl_pfr):
            button.configure(state="disabled" if busy else "normal")

    # -- настройки ---------------------------------------------------------- #
    def configure_dialog(self) -> None:
        if self.busy:
            return
        cfg = bypass_cfg()

        dialog = tk.Toplevel(self)
        dialog.title("Bypass — для каких приложений")
        dialog.configure(bg=CARD)
        dialog.transient(self.winfo_toplevel())
        dialog.grab_set()

        tk.Label(dialog, text="SOCKS5-прокси (адрес:порт), например socks5://127.0.0.1:1080",
                 bg=CARD, fg=MUTED, font=("Segoe UI", 9)).pack(
            anchor="w", padx=px(16), pady=(px(14), px(4)))
        endpoint_var = tk.StringVar(value=cfg["endpoint"])
        tk.Entry(dialog, textvariable=endpoint_var, bg=FIELD, fg=FG,
                 insertbackground=FG, relief="flat", width=44).pack(anchor="w", padx=px(16))

        tk.Label(
            dialog,
            text="Приложения — отметь программы, трафик которых пойдёт через прокси:",
            bg=CARD, fg=MUTED, font=("Segoe UI", 9),
        ).pack(anchor="w", padx=px(16), pady=(px(10), px(4)))

        head = ttk.Frame(dialog)
        head.pack(fill="x", padx=px(16))
        search_var = tk.StringVar()
        search = tk.Entry(head, textvariable=search_var, bg=FIELD, fg=FG,
                          insertbackground=FG, relief="flat", width=30)
        search.pack(side="left")
        selected_var = tk.StringVar(value="выбрано: 0")

        listbox = tk.Listbox(
            dialog, bg=FIELD, fg=FG, selectbackground=ACCENT, relief="flat",
            selectmode="multiple", height=12, activestyle="none",
            exportselection=False,
        )
        listbox.pack(fill="x", expand=True, padx=px(16), pady=(px(6), 0))
        ttk.Label(dialog, textvariable=selected_var, style="Muted.TLabel").pack(
            anchor="w", padx=px(16), pady=(px(4), 0)
        )

        def sync(_event=None) -> None:
            self._dlg_sync(listbox, selected_var)

        manual_row = ttk.Frame(dialog)
        manual_row.pack(fill="x", padx=px(16), pady=(px(6), 0))
        manual_var = tk.StringVar()
        manual = tk.Entry(manual_row, textvariable=manual_var, bg=FIELD, fg=FG,
                          insertbackground=FG, relief="flat", width=24)
        manual.pack(side="left")

        def manual_add() -> None:
            name = manual_var.get().strip()
            if name and name.lower() not in {n.lower() for n in self._dlg_procs}:
                self._dlg_procs.append(name)
                self._dlg_procs.sort()
            manual_var.set("")
            self._dlg_fill(listbox, search_var.get())
            sync()

        flat_button(manual_row, "＋ добавить вручную", manual_add, kind="ghost").pack(
            side="left", padx=(px(8), 0)
        )

        search.bind("<KeyRelease>", lambda _e: (self._dlg_fill(listbox, search_var.get()),
                                                sync()))
        listbox.bind("<<ListboxSelect>>", sync)

        def save() -> None:
            endpoint = endpoint_var.get().strip() or BYPASS_DEFAULT_ENDPOINT
            apps = sorted(self._dlg["selected"].values())
            if not apps:
                if not messagebox.askyesno(
                    "Bypass", "Ни одно приложение не выбрано — сохранить всё равно?",
                    parent=dialog,
                ):
                    return
            self._dlg["selected"] = {}
            bypass_cfg_save(endpoint=endpoint, apps=apps)
            names = ", ".join(apps) if apps else "—"
            self.append_log(self.log_box, f"Сохранено: прокси {endpoint}; приложения: {names}")
            was_running = bool((self.state.get("happ") or {}).get("on"))
            dialog.destroy()
            if was_running:
                self.append_log(self.log_box, "Перезапускаю ProxiFyre с новым списком…")
                self.toggle("happ", True)
            else:
                self.refresh_now()

        buttons = ttk.Frame(dialog)
        buttons.pack(fill="x", padx=px(16), pady=px(12))
        flat_button(buttons, "Обновить список программ", self._dlg_reload, kind="ghost").pack(
            side="left"
        )
        flat_button(buttons, "Сохранить и применить", save, kind="primary").pack(
            side="right"
        )
        flat_button(buttons, "Отмена", dialog.destroy, kind="ghost").pack(
            side="right", padx=(0, px(8))
        )

        self._dlg = {
            "window": dialog,
            "listbox": listbox,
            "selected": {a.lower(): a for a in cfg["apps"]},
            "search": search_var,
            "count": selected_var,
        }
        self._dlg_procs = list(cfg["apps"])
        self._dlg_fill(listbox, "")
        self._dlg_reload()
        search.focus_set()

    def _dlg_visible_names(self, pattern: str) -> list[str]:
        needle = pattern.strip().lower()
        if not needle:
            return list(self._dlg_procs)
        return [n for n in self._dlg_procs if needle in n.lower()]

    def _dlg_fill(self, listbox: tk.Listbox, pattern: str) -> None:
        listbox.delete(0, tk.END)
        selected = self._dlg["selected"] if isinstance(getattr(self, "_dlg", None), dict) else {}
        self._dlg_visible = self._dlg_visible_names(pattern)
        for index, name in enumerate(self._dlg_visible):
            listbox.insert(index, name)
            if name.lower() in selected:
                listbox.selection_set(index)

    def _dlg_sync(self, listbox: tk.Listbox, selected_var: tk.StringVar) -> None:
        selected = self._dlg["selected"]
        visible = set(self._dlg_visible)
        chosen = set()
        for index in listbox.curselection():
            try:
                chosen.add(self._dlg_visible[index])
            except IndexError:
                pass
        for name in list(visible - chosen):
            selected.pop(name.lower(), None)
        for name in chosen:
            selected.setdefault(name.lower(), name)
        selected_var.set(f"выбрано: {len(selected)}")

    def _dlg_reload(self) -> None:
        if getattr(self, "_dlg_reloading", False):
            return
        self._dlg_reloading = True

        def fetched(result: dict) -> None:
            self._dlg_reloading = False
            if not isinstance(getattr(self, "_dlg", None), dict):
                return
            names = [str(p) for p in (result or {}).get("processes") or []]
            # вручную добавленное и сохранённое не теряем
            names = sorted(set(names) | set(self._dlg_procs))
            self._dlg_procs = names
            listbox = self._dlg["listbox"]
            self._dlg_fill(listbox, self._dlg["search"].get())
            self._dlg_sync(listbox, self._dlg["count"])

        def failed(exc: Exception) -> None:
            self._dlg_reloading = False
            self.append_log(self.log_box, f"Список программ недоступен: {exc}", "error")

        self.hub.run_bg(bypass_processes, fetched, failed)

    # -- скачивание ---------------------------------------------------------- #
    def download(self, kind: str) -> None:
        if self.busy:
            return
        label = "GoodbyeDPI" if kind == "gbd" else "ProxiFyre"
        self._run(
            lambda: bypass_download(kind),
            f"Скачиваю {label}…",
            f"Скачиваю {label} с GitHub (может занять пару минут)…",
        )

    # -- действия ------------------------------------------------------------ #
    def toggle(self, kind: str, on: bool) -> None:
        if self.busy or not self.online:
            return
        if kind == "gbd":
            work = bypass_gbd_start if on else bypass_gbd_stop
            label = "GoodbyeDPI"
        else:
            work = bypass_apps_start if on else bypass_apps_stop
            label = "ProxiFyre"
        title = f"{label}: {'включаю' if on else 'выключаю'}…"
        extra = ("Запуск потребует подтверждение администратора (UAC)."
                 if on and not bypass_admin() else "")
        self._run(work, title, title + extra)

    # -- общий запуск фоновой задачи ----------------------------------------- #
    def _run(self, work, status_now: str, log_now: str) -> None:
        self.busy = True
        self._update_controls()
        self.append_log(self.log_box, log_now)
        self.hub.set_status(f"Bypass: {status_now}")
        self.hub.run_bg(work, self._run_done, self._run_fail)

    def _run_done(self, result: dict) -> None:
        self.busy = False
        result = result if isinstance(result, dict) else {}
        detail = str(result.get("detail") or "")
        ok = bool(result.get("ok", True))
        if detail:
            self.append_log(self.log_box, detail, "ok" if ok else "error")
        self.hub.set_status("Bypass: готово" if ok else "Bypass: не выполнено")
        self.refresh_now()

    def _run_fail(self, exc: Exception) -> None:
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
    ("bypass", "Bypass", "обход DPI: глобально и по приложениям"),
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
        "Bypass",
        "Обход DPI: GoodbyeDPI для всего интернета, ProxiFyre — по приложениям.",
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

    def toast(self, message: str, *, kind: str = "ok", ms: int = 2200) -> None:
        """Всплывающее подтверждение поверх окна — видно, что действие сработало."""
        label = getattr(self, "_toast_label", None)
        if label is None:
            label = tk.Label(
                self, bg=CARD, fg=FG, font=("Segoe UI", 10, "bold"),
                padx=px(18), pady=px(10),
                highlightthickness=1, highlightbackground=BORDER, bd=0,
            )
            self._toast_label = label
        colors = {"ok": OK, "warn": WARN, "error": DANGER}
        label.configure(text=message, fg=colors.get(kind, FG))
        label.place(relx=0.5, rely=1.0, y=-px(46), anchor="s")
        label.lift()
        job = getattr(self, "_toast_job", None)
        if job:
            try:
                self.after_cancel(job)
            except Exception:  # noqa: BLE001
                pass
        self._toast_job = self.after(ms, label.place_forget)

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
