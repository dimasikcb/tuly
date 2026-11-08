"""
AutoCopy - Утилита для автоматического копирования выделенного текста в Windows.
Работает в фоновом режиме, показывает всплывающее уведомление около курсора со скопированным текстом,
отображает иконку в системном трее, поддерживает автозапуск и синхронизируется с config.json.
"""

import os
import sys
import time
import math
import json
import ctypes
from ctypes import wintypes
import threading
import winreg
from PIL import Image, ImageDraw
import pystray
from pynput import mouse
import pyperclip

# Директория приложения и логирование
if getattr(sys, 'frozen', False):
    BASE_DIR = os.path.dirname(sys.executable)
else:
    BASE_DIR = os.path.dirname(os.path.abspath(__file__))

LOG_FILE = os.path.join(BASE_DIR, "autocopy.log")
CONFIG_FILE = os.path.join(BASE_DIR, "config.json")

def log(msg):
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}\n")
    except Exception:
        pass

# Win32 API
user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
kernel32 = ctypes.windll.kernel32

VK_CONTROL = 0x11
VK_C = 0x43
VK_V = 0x56
KEYEVENTF_KEYUP = 0x0002

SCAN_CTRL = user32.MapVirtualKeyW(VK_CONTROL, 0)
SCAN_C = user32.MapVirtualKeyW(VK_C, 0)
SCAN_V = user32.MapVirtualKeyW(VK_V, 0)

WM_USER_SHOW = 0x0400 + 1
WM_USER_HIDE = 0x0400 + 2

REG_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "AutoCopyUtility"


class TooltipWorker(threading.Thread):
    """
    Отдельный UI-поток для всплывающего окна подсказки.
    Имеет собственный цикл обработки сообщений Win32, что исключает зависания.
    """
    def __init__(self):
        super().__init__(daemon=True)
        self.ready_event = threading.Event()
        self.hwnd = None
        self.thread_id = None
        self.font = None
        self.hide_timer = None

    def run(self):
        try:
            self.thread_id = kernel32.GetCurrentThreadId()
            ex_style = 0x00000008 | 0x00000080 | 0x08000000  # WS_EX_TOPMOST | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE
            style = 0x80000000 | 0x00800000 | 0x00000001     # WS_POPUP | WS_BORDER | SS_CENTER

            self.hwnd = user32.CreateWindowExW(
                ex_style, "STATIC", "", style,
                0, 0, 100, 30, 0, 0, 0, 0
            )

            # Шрифт Segoe UI (размер 16)
            self.font = gdi32.CreateFontW(16, 0, 0, 0, 600, 0, 0, 0, 1, 0, 0, 0, 0, "Segoe UI")
            user32.SendMessageW(self.hwnd, 0x0030, self.font, 1)

            self.ready_event.set()

            msg = wintypes.MSG()
            while user32.GetMessageW(ctypes.byref(msg), 0, 0, 0) > 0:
                if msg.message == WM_USER_SHOW:
                    user32.ShowWindow(self.hwnd, 4)  # SW_SHOWNOACTIVATE
                    user32.UpdateWindow(self.hwnd)
                elif msg.message == WM_USER_HIDE:
                    user32.ShowWindow(self.hwnd, 0)  # SW_HIDE
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
        except Exception as e:
            log(f"Ошибка в TooltipWorker: {e}")

    def show(self, text: str, x: int, y: int):
        try:
            if not self.ready_event.wait(1.5):
                return

            cleaned = " ".join(text.split())
            if len(cleaned) > 40:
                preview = cleaned[:37] + "..."
            else:
                preview = cleaned

            display_text = f" Скопировано: «{preview}» "

            w = max(180, min(550, int(len(display_text) * 8.2) + 20))
            h = 30

            sw = user32.GetSystemMetrics(0)
            sh = user32.GetSystemMetrics(1)
            px = min(x + 14, sw - w - 10)
            py = min(y + 16, sh - h - 10)
            if py < 0:
                py = max(0, y - h - 10)

            user32.SetWindowTextW(self.hwnd, display_text)
            user32.SetWindowPos(self.hwnd, -1, int(px), int(py), int(w), int(h), 0x0010 | 0x0040)
            user32.PostThreadMessageW(self.thread_id, WM_USER_SHOW, 0, 0)

            if self.hide_timer:
                self.hide_timer.cancel()
            self.hide_timer = threading.Timer(1.4, self.hide)
            self.hide_timer.start()
        except Exception as e:
            log(f"Ошибка при вызове TooltipWorker.show: {e}")

    def hide(self):
        try:
            if self.thread_id:
                user32.PostThreadMessageW(self.thread_id, WM_USER_HIDE, 0, 0)
        except Exception:
            pass


class AutoCopyApp:
    def __init__(self):
        self.load_config()
        
        self.press_pos = None
        self.press_time = 0
        self.last_release_time = 0
        self.last_release_pos = (0, 0)
        self.click_count = 0
        
        self.icon = None
        self.mouse_listener = None
        
        self.tooltip = TooltipWorker()
        self.tooltip.start()

    def load_config(self):
        self.enabled = True
        self.show_notifications = True
        self.middle_click_paste = False
        try:
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    ac = data.get("autocopy", {})
                    self.enabled = ac.get("enabled", True)
                    self.show_notifications = ac.get("show_notifications", True)
                    self.middle_click_paste = ac.get("middle_click_paste", False)
        except Exception as e:
            log(f"Ошибка чтения config.json: {e}")

    def save_config(self):
        try:
            data = {}
            if os.path.exists(CONFIG_FILE):
                with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
            data["autocopy"] = {
                "enabled": self.enabled,
                "show_notifications": self.show_notifications,
                "middle_click_paste": self.middle_click_paste,
                "autostart": self.is_autostart_active()
            }
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            log(f"Ошибка записи config.json: {e}")

    def is_autostart_active(self):
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REG_KEY, 0, winreg.KEY_READ) as key:
                val, _ = winreg.QueryValueEx(key, APP_NAME)
                return bool(val)
        except Exception:
            return False

    def toggle_autostart(self, icon=None, item=None):
        current_state = self.is_autostart_active()
        new_state = not current_state
        try:
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REG_KEY) as key:
                if new_state:
                    if getattr(sys, 'frozen', False):
                        app_path = f'"{sys.executable}"'
                    else:
                        pythonw = os.path.join(os.path.dirname(sys.executable), "pythonw.exe")
                        if not os.path.exists(pythonw):
                            pythonw = sys.executable
                        script_path = os.path.abspath(__file__)
                        app_path = f'"{pythonw}" "{script_path}"'
                    winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, app_path)
                    log(f"Автозагрузка включена: {app_path}")
                else:
                    try:
                        winreg.DeleteValue(key, APP_NAME)
                        log("Автозагрузка отключена")
                    except FileNotFoundError:
                        pass
        except Exception as e:
            log(f"Ошибка настройки автозагрузки: {e}")
        self.save_config()

    def create_icon_image(self, active: bool):
        img = Image.new('RGBA', (64, 64), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Задний лист
        draw.rectangle([12, 8, 38, 42], fill=(210, 215, 220, 255), outline=(100, 110, 120, 255), width=2)
        # Передний лист
        draw.rectangle([22, 18, 48, 52], fill=(255, 255, 255, 255), outline=(50, 60, 70, 255), width=2)
        draw.line([27, 26, 43, 26], fill=(130, 140, 150, 255), width=2)
        draw.line([27, 34, 43, 34], fill=(130, 140, 150, 255), width=2)
        draw.line([27, 42, 38, 42], fill=(130, 140, 150, 255), width=2)

        # Индикатор статуса
        dot_color = (39, 174, 96, 255) if active else (192, 57, 43, 255)
        draw.ellipse([42, 42, 58, 58], fill=dot_color, outline=(255, 255, 255, 255), width=2)
        return img

    def copy_selection(self, x: int, y: int):
        try:
            # 120 мс задержки для того, чтобы приложение финализировало выделение
            time.sleep(0.12)
            seq_before = user32.GetClipboardSequenceNumber()

            # Отправка Ctrl+C с правильными скан-кодами оборудования
            user32.keybd_event(VK_CONTROL, SCAN_CTRL, 0, 0)
            user32.keybd_event(VK_C, SCAN_C, 0, 0)
            time.sleep(0.02)
            user32.keybd_event(VK_C, SCAN_C, KEYEVENTF_KEYUP, 0)
            user32.keybd_event(VK_CONTROL, SCAN_CTRL, KEYEVENTF_KEYUP, 0)

            if not self.show_notifications:
                return

            # Ожидание изменения буфера обмена (до 300 мс)
            changed = False
            for _ in range(8):
                time.sleep(0.04)
                if user32.GetClipboardSequenceNumber() != seq_before:
                    changed = True
                    break

            if changed:
                copied_text = None
                for _ in range(5):
                    try:
                        copied_text = pyperclip.paste()
                        if copied_text:
                            break
                    except Exception:
                        time.sleep(0.04)

                if copied_text and copied_text.strip():
                    self.tooltip.show(copied_text, x, y)
        except Exception as e:
            log(f"Ошибка в copy_selection: {e}")

    def paste_selection(self):
        try:
            time.sleep(0.05)
            user32.keybd_event(VK_CONTROL, SCAN_CTRL, 0, 0)
            user32.keybd_event(VK_V, SCAN_V, 0, 0)
            time.sleep(0.02)
            user32.keybd_event(VK_V, SCAN_V, KEYEVENTF_KEYUP, 0)
            user32.keybd_event(VK_CONTROL, SCAN_CTRL, KEYEVENTF_KEYUP, 0)
        except Exception as e:
            log(f"Ошибка в paste_selection: {e}")

    def on_click(self, x, y, button, pressed):
        if not self.enabled:
            return

        now = time.time()

        # Вставка средней кнопкой (колесиком)
        if button == mouse.Button.middle:
            if pressed and self.middle_click_paste:
                threading.Thread(target=self.paste_selection, daemon=True).start()
            return

        # Обработка левой кнопки мыши
        if button == mouse.Button.left:
            if pressed:
                self.press_pos = (x, y)
                self.press_time = now
            else:
                if self.press_pos is None:
                    return

                dist = math.hypot(x - self.press_pos[0], y - self.press_pos[1])
                
                # 1. Выделение протяжкой мыши (drag selection)
                if dist > 6:
                    threading.Thread(target=self.copy_selection, args=(x, y), daemon=True).start()
                    self.click_count = 0
                else:
                    # 2. Двойной или тройной клик (выделение слова или абзаца)
                    dist_to_last = math.hypot(x - self.last_release_pos[0], y - self.last_release_pos[1])
                    if (now - self.last_release_time < 0.4) and (dist_to_last < 8):
                        self.click_count += 1
                        if self.click_count >= 2:
                            threading.Thread(target=self.copy_selection, args=(x, y), daemon=True).start()
                    else:
                        self.click_count = 1

                self.last_release_time = now
                self.last_release_pos = (x, y)
                self.press_pos = None

    def toggle_enable(self, icon=None, item=None):
        self.enabled = not self.enabled
        if icon:
            icon.icon = self.create_icon_image(self.enabled)
            icon.title = f"AutoCopy: {'Активен' if self.enabled else 'На паузе'}"
        log(f"Статус изменен: enabled={self.enabled}")
        self.save_config()

    def toggle_notifications(self, icon=None, item=None):
        self.show_notifications = not self.show_notifications
        log(f"Уведомления: {self.show_notifications}")
        self.save_config()

    def toggle_middle_paste(self, icon=None, item=None):
        self.middle_click_paste = not self.middle_click_paste
        log(f"Вставка на колесико: {self.middle_click_paste}")
        self.save_config()

    def quit_app(self, icon=None, item=None):
        log("Завершение работы...")
        if self.mouse_listener:
            self.mouse_listener.stop()
        if self.tooltip:
            self.tooltip.hide()
        if icon:
            icon.stop()

    def run(self):
        log("Запуск AutoCopy...")
        if not self.is_autostart_active():
            self.toggle_autostart()

        self.mouse_listener = mouse.Listener(on_click=self.on_click)
        self.mouse_listener.start()
        log("Перехватчик мыши успешно запущен")

        menu = pystray.Menu(
            pystray.MenuItem(
                lambda text: "Включено (копировать при выделении)" if self.enabled else "На паузе (кликните, чтобы включить)",
                self.toggle_enable,
                checked=lambda item: self.enabled
            ),
            pystray.MenuItem(
                "Показывать всплывающую подсказку",
                self.toggle_notifications,
                checked=lambda item: self.show_notifications
            ),
            pystray.MenuItem(
                "Вставлять по клику на колесико",
                self.toggle_middle_paste,
                checked=lambda item: self.middle_click_paste
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Запускать вместе с Windows",
                self.toggle_autostart,
                checked=lambda item: self.is_autostart_active()
            ),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Выход", self.quit_app)
        )

        self.icon = pystray.Icon(
            "AutoCopy",
            self.create_icon_image(self.enabled),
            "AutoCopy: Активен (выделите текст для копирования)",
            menu
        )

        log("Запуск трея...")
        self.icon.run()


if __name__ == "__main__":
    try:
        app = AutoCopyApp()
        app.run()
    except Exception as e:
        import traceback
        log(f"Критическая ошибка: {e}\n{traceback.format_exc()}")
