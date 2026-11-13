#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Движок вкладки «GitHub»: gh/git-хелперы и локальный скан репозиториев.

Слой логики без GUI: сюда переехала вся не-интерфейсная часть GitHub-вкладки из
tools.pyw (блок «GitHub: движок (gh + git)» — от констант GITHUB_DEFAULT_ROOT /
GITHUB_IGNORE_BLOCK до update_local_repo включительно). GUI (GitHubPanel)
остался в tools.pyw и берёт эти имена отсюда; дубль в tools.pyw вычистит
интегратор при переключении на реэкспорт.

Зависимости: только stdlib и winbase (запуск процессов без окна консоли,
поиск git/gh, путь приложения). Токены читаются/пишутся как раньше —
из hosts.yml GitHub CLI и переменных окружения, поведение не менялось.
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import sys
from pathlib import Path

from winbase import APP_DIR, find_tool, run_tool

GITHUB_DEFAULT_ROOT = str(Path.home())

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
*.session
*.session-journal
"""


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
            try:
                status = local_repo_status(str(folder))
            except Exception:  # noqa: BLE001  — падение git не должно рушить скан
                status = None
            found.append({
                "path": str(folder),
                "name": folder.name,
                "remote": repo_name_from_url(git_remote_url(str(folder))),
                "status": status,
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


def local_repo_status(folder: str) -> dict | None:
    """Статус git-папки: грязные файлы, ahead/behind, привязана ли ветка."""
    git = find_tool("git")
    if not git:
        return None
    rc, out, _err = run_tool([git, "status", "--porcelain", "-b"], cwd=folder, timeout=120)
    if rc != 0:
        return None
    lines = out.splitlines()
    if not lines:
        return {"dirty": False, "ahead": 0, "behind": 0, "upstream": False, "branch": ""}
    head = lines[0]
    dirty = any(ln.strip() for ln in lines[1:])
    ahead = behind = 0
    upstream = "..." in head
    m = re.search(r"ahead (\d+)", head)
    if m:
        ahead = int(m.group(1))
    m = re.search(r"behind (\d+)", head)
    if m:
        behind = int(m.group(1))
    branch = ""
    if head.startswith("## "):
        branch = head[3:].split("...")[0].strip()
    return {"dirty": dirty, "ahead": ahead, "behind": behind, "upstream": upstream,
            "branch": branch}


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
    rc, out, err = run_tool([git, "add", "-A"], cwd=folder, timeout=600)
    # игнорируем «invalid path» — зарезервированные имена Windows (NUL, CON и т.п.)
    if rc != 0:
        bad = "\n".join(l for l in err.splitlines() if "invalid path" not in l)
        if bad:
            log(f"ОШИБКА git add: {bad}")
            return False
        log(f"git add пропущены зарезервированные имена: {err}")
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
