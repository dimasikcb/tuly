"""Одноразовый сценарий: вклеивает новый движок и панель в tools.pyw."""
from pathlib import Path

ROOT = Path(r"C:\Users\admin\Documents\тулы")
target = ROOT / "tools.pyw"
text = target.read_text(encoding="utf-8")
engine = (ROOT / ".tmp_engine.py").read_text(encoding="utf-8").rstrip()
panel = (ROOT / ".tmp_panel.py").read_text(encoding="utf-8").rstrip()

# 1. import socket
old = "import shutil\nimport stat\n"
assert text.count(old) == 1, "import anchor"
text = text.replace(old, "import shutil\nimport socket\nimport stat\n", 1)

# 2. bypass_cfg
start = text.index("def bypass_cfg() -> dict:")
end = text.index("def bypass_cfg_save(**updates) -> None:")
new_cfg = '''def bypass_cfg() -> dict:
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


'''
text = text[:start] + new_cfg + text[end:]

# 3. bypass_status
start = text.index("def bypass_status(_timeout: float = 1.5) -> dict:")
end = text.index("def bypass_gbd_start() -> dict:")
new_status = '''def bypass_status(_timeout: float = 1.5) -> dict:
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
    result["proxyfyre"] = proxifyre_status()
    result["scope"] = cfg["scope"]
    result["apps"] = cfg["apps"]
    result["mode"] = cfg["mode"]
    return result


'''
text = text[:start] + new_status + text[end:]

# 4. bypass_stop: при выключении Happ снимаем и перенаправление программ
old_stop = '''def bypass_stop(key: str) -> dict:
    if key != "gbd":
        # Happ не закрываем — только отключаем VPN командой happ://disconnect
        return bypass_happ_stop()'''
new_stop = '''def bypass_stop(key: str) -> dict:
    if key != "gbd":
        # Happ не закрываем — отключаем VPN и снимаем перенаправление программ,
        # иначе выбранные приложения останутся с мёртвым SOCKS и без интернета.
        notes: list[str] = []
        try:
            notes.append(proxifyre_stop().get("detail") or "")
        except Exception as exc:  # noqa: BLE001
            notes.append(f"ProxiFyre: {exc}")
        notes.append(bypass_happ_stop().get("detail") or "")
        return {"ok": True, "detail": "\\n".join(filter(None, notes))}'''
assert text.count(old_stop) == 1, "bypass_stop anchor"
text = text.replace(old_stop, new_stop, 1)

# 5. новый движок перед listening_pid
anchor = "def listening_pid(port: int) -> int:"
assert text.count(anchor) == 1, "listening_pid anchor"
text = text.replace(anchor, engine + "\n\n\n" + anchor, 1)

# 6. панель
start = text.index("class BypassPanel(Panel):")
marker = ("# --------------------------------------------------------------------------- #\n"
          "#  Боковое меню и главное окно")
end = text.index(marker)
text = text[:start] + panel + "\n\n\n" + text[end:]

# 7. заголовок вкладки
old_header = '''    "bypass": (
        "Bypass",
        "Обход DPI: запуск Happ или GoodbyeDPI одной кнопкой.",
    ),'''
new_header = '''    "bypass": (
        "Bypass",
        "Обход DPI: весь трафик или только выбранные программы. Happ и GoodbyeDPI.",
    ),'''
assert text.count(old_header) == 1, "header anchor"
text = text.replace(old_header, new_header, 1)

target.write_text(text, encoding="utf-8")
print("ok", len(text.splitlines()), "lines")
