#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Панель «GitHub» хаба: список репозиториев, вход, заливка/обновление, удаление.

Сюда переехал из tools.pyw класс GitHubPanel — verbatim, без правок логики.
Движок (gh/git-хелперы, локальный скан репозиториев) живёт в github_core.py,
запуск процессов и config.json — в winbase.py, тема/токены/базовая Panel —
в uibase.py. Модуль не знает о tools.pyw ничего: Hub передаётся мостом
(run_bg/set_status/toast/post) через Panel.hub.

tkinter разрешён: это GUI-слой.
"""

from __future__ import annotations

import os
import subprocess
import webbrowser
from pathlib import Path

import tkinter as tk
from tkinter import filedialog, ttk

from uibase import (
    ACCENT,
    BG,
    CARD,
    FG,
    MUTED,
    OK,
    WARN,
    Panel,
    dark_check,
    enable_dark_titlebar,
    flat_button,
    messagebox,
    mono_font,
    px,
    register_palette_consumer,
    ui_font,
)

# Токены приходят копией в globals() модуля; регистрируем его потребителем
# палитры, чтобы живая смена темы обновляла и эти копии (панель читает
# голые имена — см. uibase.register_palette_consumer).
register_palette_consumer(globals())

from github_core import (  # noqa: E402
    GITHUB_DEFAULT_ROOT,
    gh_authed,
    gh_login,
    gh_save_token,
    gh_token_from_hosts,
    github_repos,
    local_scan_roots,
    mask_token,
    match_local_repos,
    push_folder_to_github,
    sanitize_repo_name,
    scan_local_repos,
    send_to_recycle_bin,
    token_looks_valid,
    update_local_repo,
)
from winbase import (  # noqa: E402
    NO_WINDOW,
    config_read,
    config_update,
    find_tool,
    run_tool,
    tool_env,
)

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
        self.status_by_path: dict[str, dict] = {}
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
        self.btn_upd_all = flat_button(top, "Обновить всё", self.update_needed, kind="ghost")
        self.btn_upd_all.pack(side="left", padx=(px(8), 0))
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
        self.only_upd_var = tk.BooleanVar(value=False)
        dark_check(search_row, "только можно обновить", self.only_upd_var,
                   bg=BG, command=self._render).pack(side="left", padx=(px(12), 0))

        tree_frame = ttk.Frame(self)
        tree_frame.pack(fill="both", expand=True)
        columns = ("name", "access", "state", "local", "update")
        self.tree = ttk.Treeview(tree_frame, columns=columns, show="headings", height=10,
                                 selectmode="browse")
        self.tree.heading("name", text="Репозиторий")
        self.tree.heading("access", text="Доступ")
        self.tree.heading("state", text="Где есть")
        self.tree.heading("local", text="Папка на ПК")
        self.tree.heading("update", text="Обновление")
        self.tree.column("name", width=px(190), anchor="w")
        self.tree.column("access", width=px(90), anchor="center", stretch=False)
        self.tree.column("state", width=px(150), anchor="w", stretch=False)
        self.tree.column("local", width=px(400), anchor="w", stretch=False)
        self.tree.column("update", width=px(150), anchor="w", stretch=False)
        self.tree.tag_configure("both", foreground=FG)
        self.tree.tag_configure("gh_only", foreground=WARN)
        self.tree.tag_configure("local_only", foreground=ACCENT)
        self.tree.tag_configure("can_upd", foreground=OK)
        self.tree.tag_configure("behind", foreground=WARN)
        self.tree.tag_configure("mut", foreground=MUTED)
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

    def _folder_state(self, local: str) -> tuple[str, bool]:
        """(текст статуса, можно ли обновить) для локальной git-папки."""
        st = self.status_by_path.get(local)
        if not st:
            return ("—", False)
        if not st.get("upstream"):
            return ("не привязан к ветке", False)
        if st.get("ahead", 0) > 0 or st.get("dirty"):
            return ("можно обновить", True)
        if st.get("behind", 0) > 0:
            return ("отстаёт от GitHub", False)
        return ("актуально", False)

    def _update_actions(self) -> None:
        row = self._selected_row()
        repo = row["repo"] if row else None
        local = self._local_for(row)
        state = "disabled" if self.busy else "normal"
        self.btn_refresh.configure(state=state)
        self.btn_push.configure(state=state)
        self.btn_login.configure(state=state)
        self.btn_all.configure(state=state if self.repos else "disabled")
        self.btn_upd_all.configure(state=state if (self.repos or self.local_items) else "disabled")
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
        only_upd = bool(self.only_upd_var.get())
        previous = self.tree.selection()
        previous_iid = previous[0] if previous else ""
        self.tree.delete(*self.tree.get_children())
        self.rows = {}

        matched = sum(1 for repo in self.repos if repo["full"] in self.matches)
        gh_only = len(self.repos) - matched
        upd_count = sum(
            1 for repo in self.repos
            if self.matches.get(repo["full"], "") and self._folder_state(self.matches[repo["full"]])[1]
        ) + len(self.local_only)
        shown = 0

        for repo in self.repos:
            local = self.matches.get(repo["full"], "")
            haystack = f"{repo['name']} {repo['full']} {local}".lower()
            if query and query not in haystack:
                continue
            state, can_update = self._folder_state(local) if local else ("—", False)
            if only_diff and local:
                continue
            if only_upd and not can_update:
                continue
            iid = repo["full"]
            tag = "gh_only" if not local else (
                "can_upd" if can_update else ("behind" if state == "отстаёт от GitHub" else "mut")
            )
            self.tree.insert(
                "", "end", iid=iid, tags=(tag,),
                values=(
                    repo["name"],
                    "приватный" if repo["private"] else "публичный",
                    "● на ПК" if local else "○ только на GitHub",
                    self._display_path(local),
                    state if local else "—",
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
                values=(
                    item["name"], "—", "◆ только на ПК",
                    self._display_path(item["path"]), "новая — залить",
                ),
            )
            self.rows[iid] = {"iid": iid, "repo": None, "local": item["path"]}
            shown += 1

        if previous_iid and self.tree.exists(previous_iid):
            self.tree.selection_set(previous_iid)
        summary = (
            f"На GitHub: {len(self.repos)} · есть на ПК: {matched} · "
            f"только на GitHub: {gh_only} · папок без GitHub: {len(self.local_only)} · "
            f"можно обновить: {upd_count}"
        )
        if query or only_diff or only_upd:
            summary += f" · показано: {shown}"
        self.summary_var.set(summary)
        self._update_actions()

    def copy_list(self) -> None:
        """Копирует таблицу текстом: репозиторий, где есть, путь на ПК."""
        lines = ["Репозиторий\tДоступ\tГде есть\tПапка на ПК\tОбновление"]
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
                 font=ui_font(12, "bold"), anchor="w").pack(fill="x")
        tk.Label(
            body,
            text=("Удаление репозиториев требует права delete_repo. Создайте "
                  "classic PAT со scope repo и delete_repo, скопируйте его и "
                  "вставьте здесь — приложение заменит токен в hosts.yml GitHub CLI."),
            bg=CARD, fg=MUTED, justify="left", anchor="w",
            wraplength=px(520), font=ui_font(9),
        ).pack(fill="x", pady=(px(10), 0))

        tk.Label(body, text=f"Сейчас: {mask_token(current)}", bg=CARD, fg=ACCENT,
                 anchor="w", font=mono_font(9)).pack(fill="x", pady=(px(10), 0))

        link = tk.Label(body, text="Создать токен на GitHub ↗", bg=CARD, fg=ACCENT,
                        cursor="hand2", font=ui_font(9, "underline"), anchor="w")
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
        self.status_by_path = {
            it["path"]: it.get("status") for it in self.local_items if it.get("status")
        }
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

        need = [
            (repo["name"], self.matches[repo["full"]], self._folder_state(self.matches[repo["full"]])[0])
            for repo in self.repos
            if self.matches.get(repo["full"], "")
            and self._folder_state(self.matches[repo["full"]])[1]
        ]
        need += [(item["name"], item["path"], "новая — залить") for item in self.local_only]
        if not need:
            self.append_log(self.log_box, "=== Обновлять нечего: всё актуально ===")
        else:
            self.append_log(self.log_box, f"=== Можно обновить: {len(need)} ===")
            for name, folder, state in need[:40]:
                self.append_log(self.log_box, f"↑ {state}: {name} → {folder}", "ok")
            if len(need) > 40:
                self.append_log(self.log_box, f"… ещё {len(need) - 40} папок к обновлению")

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
        repo = row["repo"] if row else None
        if repo:  # репозиторий уже есть — обновляем его, а не пересоздаём
            self._update_one(folder, repo["name"])
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

    # -- обновление одной папки -------------------------------------------- #
    def _update_one(self, folder: str, name: str) -> None:
        self._start_busy(f"GitHub: обновляю {name}…")
        self.append_log(self.log_box, f"=== {name}: коммит и push ===")

        def work():
            try:
                return update_local_repo(folder, name, self._log_bg)
            except Exception as exc:  # noqa: BLE001
                self._log_bg(f"Ошибка {name}: {exc}")
                return False

        self.hub.run_bg(work, self._update_done, self._fail)

    def _update_done(self, ok: bool) -> None:
        self._finish()
        if ok:
            self.hub.set_status("GitHub: обновлено")
            self.refresh()
        else:
            self.hub.set_status("GitHub: обновление не удалось")

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

    # -- обновить всё нужное ------------------------------------------------- #
    def update_needed(self) -> None:
        """Коммит + push там, где «можно обновить», и заливка новых папок."""
        if self.busy:
            return
        targets = [
            (self.matches[repo["full"]], repo["name"])
            for repo in self.repos
            if self.matches.get(repo["full"], "")
            and self._folder_state(self.matches[repo["full"]])[1]
        ]
        new_items = list(self.local_only)
        if not targets and not new_items:
            messagebox.showinfo(
                "GitHub", "Обновлять нечего: все репозитории актуальны.", parent=self
            )
            return
        if targets:
            message = f"Сделать коммит и push в {len(targets)} репозиториев?"
            if new_items:
                message += (
                    f"\n\nТакже залить {len(new_items)} папок без GitHub?\n"
                    "Для каждой будет создан публичный репозиторий."
                )
        else:
            message = (
                f"Залить на GitHub {len(new_items)} папок?\n"
                "Для каждой будет создан публичный репозиторий."
            )
        if not messagebox.askyesno("Обновить всё", message, parent=self):
            return
        self._start_busy(f"GitHub: обновляю {len(targets) + len(new_items)}…")
        total = len(targets) + len(new_items)
        self.append_log(
            self.log_box,
            f"=== Обновить всё: обновлений {len(targets)}, новых {len(new_items)} ===",
        )

        def work():
            updated = 0
            failed = 0
            index = 0
            for folder, name in targets:
                index += 1
                self._log_bg(f"[{index}/{total}] {name}")
                try:
                    ok = update_local_repo(folder, name, self._log_bg)
                except Exception as exc:  # noqa: BLE001
                    self._log_bg(f"Ошибка {name}: {exc}")
                    ok = False
                updated += 1 if ok else 0
                failed += 0 if ok else 1
            for item in new_items:
                index += 1
                name = sanitize_repo_name(Path(item["path"]).name)
                self._log_bg(f"[{index}/{total}] {name} (новая)")
                try:
                    ok = push_folder_to_github(item["path"], name, True, self._log_bg)
                except Exception as exc:  # noqa: BLE001
                    self._log_bg(f"Ошибка {name}: {exc}")
                    ok = False
                updated += 1 if ok else 0
                failed += 0 if ok else 1
            return updated, failed

        self.hub.run_bg(work, self._update_all_done, self._fail)

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
        self.status_by_path.pop(folder, None)
        self.append_log(self.log_box, f"🗑 папка в Корзине: {folder}", "ok")
        self.hub.set_status("GitHub: папка удалена (Корзина)")
        self._render()
