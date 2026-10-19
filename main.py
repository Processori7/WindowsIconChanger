# -*- coding: utf-8 -*-
"""
WinIcons — накладывает выбранную картинку поверх кнопки «Пуск» в Windows.

Принцип работы: поверх кнопки «Пуск» на панели задач отображается маленькое
прозрачное окно-оверлей с вашим изображением. Клики проходят «сквозь» него.
Реестр НЕ изменяется: ни значок системы, ни автозагрузка (используется ярлык
в папке «Автозагрузка»).

Запуск:
    python main.py             — тихий старт, значок в трее
    python main.py --autostart — то же самое (используется ярлыком автозагрузки)
    python main.py --selftest  — самопроверка: скриншоты + отчёт, затем выход
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from ctypes import wintypes
from string import Template

from PySide6.QtCore import (Qt, QTimer, QRect, QRectF, QPoint, QSize, QObject, QEvent,
                            QAbstractNativeEventFilter)
from PySide6.QtGui import (QIcon, QImage, QPixmap, QPainter, QPainterPath, QColor,
                           QPen, QFont, QAction)
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (QApplication, QWidget, QLabel, QPushButton, QCheckBox,
                               QSlider, QVBoxLayout, QHBoxLayout, QGridLayout, QFrame,
                               QFileDialog, QSystemTrayIcon, QMenu, QMessageBox, QScrollArea,
                               QGraphicsDropShadowEffect, QSizePolicy, QStyle, QComboBox)

try:
    import uiautomation as auto
    _HAS_UIA = True
except Exception:
    auto = None
    _HAS_UIA = False

APP_NAME = "IconsPo"
APP_TITLE = "WinIcons"
APP_VERSION = "1.1"

# ---------------------------------------------------------------- пути и файлы
APPDATA_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
CONFIG_PATH = os.path.join(APPDATA_DIR, "config.json")
LOG_PATH = os.path.join(APPDATA_DIR, "iconspo.log")
LOG_MAX_BYTES = 512 * 1024      # при перерастании старый журнал сохраняется как .old

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
ASSETS_DIR = os.path.join(_SCRIPT_DIR, "assets")
if not os.path.isdir(ASSETS_DIR):
    _alt = os.path.join(os.path.dirname(os.path.abspath(sys.argv[0])), "assets")
    if os.path.isdir(_alt):
        ASSETS_DIR = _alt
DEFAULT_SVG = os.path.join(ASSETS_DIR, "start.svg")
ICON_ICO = os.path.join(ASSETS_DIR, "icon.ico")
PRESETS_DIR = os.path.join(ASSETS_DIR, "icons")
# красивые названия для файлов пресетов (иначе — из имени файла)
PRESET_TITLES = {
    "star_wars": "Star Wars (Асока)",
}
PRESET_TITLES_EN = {
    "star_wars": "Star Wars (Ahsoka)",
}

# ------------------------------------------------------------------ язык (i18n)
_LANG = "ru"                    # текущий язык интерфейса: ru | en


def set_lang(lang: str) -> None:
    """Переключает язык интерфейса (выбор сохраняется в конфиге)."""
    global _LANG
    _LANG = "en" if str(lang or "").lower().startswith("en") else "ru"


def tr(key: str) -> str:
    """Строка интерфейса на текущем языке (с откатом на русскую)."""
    table = STRINGS.get(_LANG) or STRINGS["ru"]
    return table.get(key) or STRINGS["ru"].get(key) or key


def preset_title(key: str) -> str:
    base = str(key).replace("_", " ").title()
    if _LANG == "en":
        return PRESET_TITLES_EN.get(key, base)
    return PRESET_TITLES.get(key, base)


def init_lang_from_config() -> None:
    """Читает язык из конфига заранее — до первых окон и диалогов."""
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
            set_lang((json.load(f) or {}).get("lang", "ru"))
    except Exception:
        set_lang("ru")


def preset_files() -> list:
    """[(ключ, название)] — все SVG-пресеты из assets/icons."""
    out = []
    try:
        for name in sorted(os.listdir(PRESETS_DIR)):
            if name.lower().endswith(".svg"):
                key = os.path.splitext(name)[0]
                out.append((key, preset_title(key)))
    except Exception:
        pass
    return out

# ------------------------------------------------------------------- строки UI
STRINGS = {
    "ru": {
        "app_sub": "Значок «Пуск» поверх панели задач",
        "settings_title": "Настройки",
        "tray_settings": "Настройки…",
        "tray_quit": "Выход",
        "status_init": "Инициализация…",
        "status_disabled": "Значок выключен в настройках",
        "status_calibration": "Калибровка: перетащите значок мышью на нужное место",
        "status_fullscreen": "Скрыто: активно полноэкранное приложение",
        "status_no_taskbar": "Панель задач не найдена",
        "status_taskbar_hidden": "Панель задач скрыта (автоскрытие)",
        "status_found": "Кнопка «Пуск» найдена автоматически · {w}×{h} px",
        "status_found_approx": "Кнопка «Пуск» найдена приблизительно (нужна калибровка) · {w}×{h} px",
        "status_found_multi": "Кнопка «Пуск» найдена автоматически на всех панелях задач ({n}) · {w}×{h} px",
        "status_found_approx_multi": "Кнопка «Пуск» найдена приблизительно на всех панелях задач ({n}) · {w}×{h} px",
        "status_partial": "Кнопка «Пуск» найдена на части панелей задач ({n} из {total})",
        "close_tip": "Свернуть в трей",
        "card_image": "Изображение значка",
        "btn_pick": "Выбрать изображение…",
        "btn_pick_tip": "Любая картинка или фото — они автоматически подгонятся под значок",
        "btn_default": "Стандартный значок",
        "default_icon_tip": "Стандартный значок",
        "cap_presets": "Пресеты:",
        "preset_tip_suffix": " — поставить этот значок",
        "card_size": "Размер значка",
        "auto": "Авто",
        "card_shape": "Форма (для фото и картинок)",
        "shape_rounded": "Скруглённый квадрат",
        "shape_circle": "Круг",
        "shape_square": "Квадрат",
        "card_offset": "Положение",
        "btn_reset": "Сброс",
        "btn_calib": "Калибровка",
        "btn_calib_tip": "Перетащите значок мышью на нужное место",
        "card_behaviour": "Поведение",
        "chk_enabled": "Показывать значок поверх кнопки «Пуск»",
        "chk_autostart": "Запускать при входе в Windows",
        "autostart_note_none": "Без реестра: задача Планировщика или ярлык «Автозагрузки»",
        "autostart_note_task": "Способ: задача в Планировщике (надёжнее). Реестр не изменялся.",
        "autostart_note_shortcut": "Способ: ярлык в папке «Автозагрузка». Реестр не изменялся.",
        "chk_fullscreen": "Скрывать в полноэкранных приложениях",
        "chk_log": "Вести журнал работы (для диагностики)",
        "chk_log_tip": "Пишет события программы в %APPDATA%\\IconsPo\\iconspo.log",
        "card_interface": "Интерфейс",
        "theme_dark": "Тема: Тёмная",
        "theme_light": "Тема: Светлая",
        "lang_ru": "Язык: Русский",
        "lang_en": "Язык: English",
        "offset_label": "Сдвиг: {x}, {y}",
        "err_autostart": "Не удалось изменить автозагрузку.\n",
        "ask_autostart_text": "Как включить автозагрузку?",
        "ask_autostart_info": (
            "Надёжный способ — задача в Планировщике Windows: она переживает «чистки»\n"
            "автозагрузки и стартует даже если ярлыки отключены.\n\n"
            "Для создания задачи нужны права администратора — сейчас появится запрос UAC.\n\n"
            "Можно и без прав: тогда будет использован ярлык в папке «Автозагрузка»."),
        "ask_autostart_task_btn": "Через Планировщик (надёжнее)",
        "ask_autostart_lnk_btn": "Обычный ярлык",
        "dlg_pick_title": "Выберите изображение для значка",
        "dlg_pick_filter": "Изображения (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.svg);;Все файлы (*.*)",
        "err_file_not_found": "Файл не найден.",
        "already_running": "WinIcons уже запущен.\nЗначок программы находится в трее (возле часов).",
    },
    "en": {
        "app_sub": "Start icon over the taskbar",
        "settings_title": "Settings",
        "tray_settings": "Settings…",
        "tray_quit": "Exit",
        "status_init": "Starting…",
        "status_disabled": "Icon is disabled in settings",
        "status_calibration": "Calibration: drag the icon to the desired position",
        "status_fullscreen": "Hidden: a fullscreen app is active",
        "status_no_taskbar": "Taskbar not found",
        "status_taskbar_hidden": "Taskbar is hidden (auto-hide)",
        "status_found": "Start button found automatically · {w}×{h} px",
        "status_found_approx": "Start button found approximately (calibration may be needed) · {w}×{h} px",
        "status_found_multi": "Start button found automatically on all taskbars ({n}) · {w}×{h} px",
        "status_found_approx_multi": "Start button found approximately on all taskbars ({n}) · {w}×{h} px",
        "status_partial": "Start button found on some taskbars ({n} of {total})",
        "close_tip": "Minimize to tray",
        "card_image": "Icon image",
        "btn_pick": "Choose image…",
        "btn_pick_tip": "Any picture or photo — it will be fitted to the icon automatically",
        "btn_default": "Default icon",
        "default_icon_tip": "Default icon",
        "cap_presets": "Presets:",
        "preset_tip_suffix": " — set this icon",
        "card_size": "Icon size",
        "auto": "Auto",
        "card_shape": "Shape (for photos and images)",
        "shape_rounded": "Rounded square",
        "shape_circle": "Circle",
        "shape_square": "Square",
        "card_offset": "Position",
        "btn_reset": "Reset",
        "btn_calib": "Calibrate",
        "btn_calib_tip": "Drag the icon to the desired position",
        "card_behaviour": "Behaviour",
        "chk_enabled": "Show the icon over the Start button",
        "chk_autostart": "Run at Windows logon",
        "autostart_note_none": "Registry-free: a Task Scheduler task or a Startup shortcut",
        "autostart_note_task": "Method: Task Scheduler task (more reliable). The registry was not modified.",
        "autostart_note_shortcut": "Method: a shortcut in the Startup folder. The registry was not modified.",
        "chk_fullscreen": "Hide in fullscreen apps",
        "chk_log": "Keep a log (for diagnostics)",
        "chk_log_tip": "Writes app events to %APPDATA%\\IconsPo\\iconspo.log",
        "card_interface": "Interface",
        "theme_dark": "Theme: Dark",
        "theme_light": "Theme: Light",
        "lang_ru": "Language: Russian",
        "lang_en": "Language: English",
        "offset_label": "Offset: {x}, {y}",
        "err_autostart": "Could not change the autostart.\n",
        "ask_autostart_text": "How to enable autostart?",
        "ask_autostart_info": (
            "The reliable way is a Windows Task Scheduler task: it survives startup-cleanup\n"
            "tools and starts even when shortcuts are disabled.\n\n"
            "Creating the task needs administrator rights — a UAC prompt will appear now.\n\n"
            "You can also go without rights: a shortcut in the Startup folder will be used."),
        "ask_autostart_task_btn": "Via Task Scheduler (more reliable)",
        "ask_autostart_lnk_btn": "Plain shortcut",
        "dlg_pick_title": "Choose an image for the icon",
        "dlg_pick_filter": "Images (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.svg);;All files (*.*)",
        "err_file_not_found": "File not found.",
        "already_running": "WinIcons is already running.\nThe app icon is in the system tray (near the clock).",
    },
}


def offset_text(x, y) -> str:
    return tr("offset_label").format(x=int(x), y=int(y))


DEFAULT_CFG = {
    "image": "",            # путь к выбранной картинке ("" = стандартный SVG)
    "size": 0,              # 0 = автоматически под высоту кнопки
    "shape": "rounded",     # rounded | circle | square
    "offset_x": 0,
    "offset_y": 0,
    "enabled": True,
    "autostart": False,
    "hide_fullscreen": True,
    "log": False,           # вести журнал работы (по умолчанию выключен)
    "theme": "dark",        # оформление: dark | light
    "lang": "ru",           # язык интерфейса: ru | en
    "history": [],          # галерея выбранных значков (свежие впереди)
}


def ensure_appdata() -> None:
    os.makedirs(APPDATA_DIR, exist_ok=True)


_LOG_ON = False


def set_logging(on: bool) -> None:
    """Включает/выключает запись журнала (галочка в настройках)."""
    global _LOG_ON
    _LOG_ON = bool(on)


def app_log(text: str) -> None:
    """Пишет строку в %APPDATA%\\IconsPo\\iconspo.log, если журнал включён."""
    if not _LOG_ON:
        return
    try:
        ensure_appdata()
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > LOG_MAX_BYTES:
            try:
                os.remove(LOG_PATH + ".old")
            except Exception:
                pass
            try:
                os.replace(LOG_PATH, LOG_PATH + ".old")
            except Exception:
                pass
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S") + "  " + str(text) + "\n")
    except Exception:
        pass


def load_cfg() -> dict:
    cfg = dict(DEFAULT_CFG)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8-sig") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k in DEFAULT_CFG:
                if k in data:
                    cfg[k] = data[k]
    except Exception:
        pass
    if cfg["image"] and not resolve_media(cfg["image"]):
        cfg["image"] = ""
    hist = cfg.get("history")
    if not isinstance(hist, list):
        hist = []
    cfg["history"] = [p for p in hist
                      if isinstance(p, str) and (not p or resolve_media(p))]
    return cfg


def save_cfg(cfg: dict) -> None:
    try:
        ensure_appdata()
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, CONFIG_PATH)
    except Exception:
        pass


PRESET_PREFIX = "preset:"


def preset_ref(key: str) -> str:
    """Логическая ссылка на пресет: сам файл в AppData НЕ копируется."""
    return PRESET_PREFIX + key


def preset_asset(key: str) -> str:
    return os.path.join(PRESETS_DIR, key + ".svg")


def resolve_media(p: str) -> str:
    """Запись конфига (путь к файлу или preset:<ключ>) — в реальный файл.

    Возвращает путь к существующему файлу или "" (не найден).
    """
    p = str(p or "")
    if p.startswith(PRESET_PREFIX):
        asset = preset_asset(p[len(PRESET_PREFIX):])
        return asset if os.path.exists(asset) else ""
    return p if p and os.path.exists(p) else ""


def current_source(cfg: dict) -> str:
    """Текущий источник картинки: пользовательская, пресет или стандартный SVG."""
    p = resolve_media(cfg.get("image") or "")
    if p:
        return p
    return DEFAULT_SVG


# ---------------------------------------------------------------- win32 helpers
user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
MONITOR_DEFAULTTONEAREST = 2
HWND_TOP = 0
HWND_TOPMOST = -1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOACTIVATE = 0x0010

# Явные типы! Без этого ctypes передаёт HWND_TOPMOST (-1) как 32-битное число,
# и подъём окна поверх панели задач молча не срабатывает.
user32.SetWindowPos.restype = wintypes.BOOL
user32.SetWindowPos.argtypes = (wintypes.HWND, wintypes.HWND,
                                ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                wintypes.UINT)


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", RECT),
                ("rcWork", RECT), ("dwFlags", wintypes.DWORD)]


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT),
                ("showCmd", wintypes.UINT), ("ptMinPosition", wintypes.POINT),
                ("ptMaxPosition", wintypes.POINT), ("rcNormalPosition", RECT)]


def get_taskbar_hwnd() -> int:
    try:
        return int(user32.FindWindowW("Shell_TrayWnd", None) or 0)
    except Exception:
        return 0


def get_window_rect(hwnd: int):
    r = RECT()
    if user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(r)):
        return r
    return None


def get_monitor_info(hwnd: int) -> MONITORINFO:
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    hmon = user32.MonitorFromWindow(wintypes.HWND(hwnd), MONITOR_DEFAULTTONEAREST)
    user32.GetMonitorInfoW(hmon, ctypes.byref(mi))
    return mi


# Явные типы: HMONITOR — это дескриптор-указатель, без restype ctypes обрежет
# его до 32 бит.
user32.MonitorFromWindow.restype = wintypes.HANDLE
user32.MonitorFromWindow.argtypes = (wintypes.HWND, wintypes.DWORD)
user32.GetMonitorInfoW.restype = wintypes.BOOL
user32.GetMonitorInfoW.argtypes = (wintypes.HANDLE, ctypes.POINTER(MONITORINFO))


def get_monitor_info_hmon(hmon) -> MONITORINFO:
    """Информация о мониторе по его дескриптору (HMONITOR)."""
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    user32.GetMonitorInfoW(wintypes.HANDLE(int(hmon)), ctypes.byref(mi))
    return mi


def enum_taskbars() -> list:
    """Панели задач: основная (Shell_TrayWnd) и дополнительные — по одной
    на каждый монитор (Shell_SecondaryTrayWnd)."""
    hwnds = []
    try:
        h = int(user32.FindWindowW("Shell_TrayWnd", None) or 0)
        if h:
            hwnds.append(h)
    except Exception:
        pass
    try:
        user32.FindWindowExW.restype = wintypes.HWND
        prev = 0
        while True:
            h = int(user32.FindWindowExW(wintypes.HWND(0), wintypes.HWND(prev),
                                         "Shell_SecondaryTrayWnd", None) or 0)
            if not h:
                break
            hwnds.append(h)
            prev = h
    except Exception:
        pass
    return hwnds


def monitor_rect_for(hwnd: int) -> QRect:
    """Физический прямоугольник монитора, на котором находится окно."""
    try:
        mi = get_monitor_info(hwnd)
        m = mi.rcMonitor
        return QRect(int(m.left), int(m.top),
                     int(m.right - m.left), int(m.bottom - m.top))
    except Exception:
        return QRect()


# Окна оболочки Windows: могут быть во весь экран, но значок поверх них нужен
SHELL_WINDOW_CLASSES = {"Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Progman", "WorkerW",
                        "XamlExplorerHostIslandWindow", "Windows.UI.Core.CoreWindow"}


def get_window_class(hwnd: int) -> str:
    try:
        buf = ctypes.create_unicode_buffer(256)
        user32.GetClassNameW(wintypes.HWND(hwnd), buf, 256)
        return buf.value
    except Exception:
        return ""


def foreground_is_fullscreen() -> bool:
    """True, только если активное окно действительно на весь экран (игра/F11).

    Развёрнутые (maximized) окна не считаются: их GetWindowRect тоже покрывает
    весь монитор, но значок поверх них выглядит нормально. Окна оболочки
    («Пуск», поиск, рабочий стол) полноэкранными не считаются никогда.
    """
    h = int(user32.GetForegroundWindow() or 0)
    if not h or h in enum_taskbars():
        return False
    if get_window_class(h) in SHELL_WINDOW_CLASSES:
        return False
    try:
        wp = WINDOWPLACEMENT()
        wp.length = ctypes.sizeof(WINDOWPLACEMENT)
        if user32.GetWindowPlacement(wintypes.HWND(h), ctypes.byref(wp)):
            if wp.showCmd != 1:      # SW_SHOWNORMAL — свернуть/развернуть не считаем
                return False
    except Exception:
        pass
    r = get_window_rect(h)
    if not r:
        return False
    mon = get_monitor_info(h).rcMonitor
    return (r.left <= mon.left + 2 and r.top <= mon.top + 2 and
            r.right >= mon.right - 2 and r.bottom >= mon.bottom - 2)


def exstyle_flags(hwnd: int) -> int:
    try:
        return int(user32.GetWindowLongW(wintypes.HWND(hwnd), GWL_EXSTYLE))
    except Exception:
        return 0


# ---------------------------------------------------------- поиск кнопки «Пуск»
START_NAMES = {
    "start", "пуск", "начало", "iniciar", "démarrer", "demarrer", "starten",
    "startmenü", "开始", "スタート", "시작", "avvio", "começar", "початок",
}


class StartLocator:
    """Ищет кнопку «Пуск» на каждой панели задач (по одной на монитор).

    Основной способ — UIAutomation; запасной — анализ пикселей панели.
    Результаты кэшируются отдельно для каждой панели (по её дескриптору).
    """

    def __init__(self):
        self._btns = {}        # hwnd панели -> элемент UIA кнопки «Пуск»
        self._fails = {}       # hwnd панели -> подряд неудачных попыток UIA
        self._heur = {}        # hwnd панели -> (QRect панели, QRect кнопки)
        self.source = "none"   # uia | heuristic | none (по последнему поиску)
        self.last_error = ""
        self.hide_overlays = None   # callable(): спрятать значки перед снимком

    # -- публичный интерфейс ---------------------------------------------------
    def invalidate(self) -> None:
        """Сбросить кэши (например, при смене экранов)."""
        self._heur.clear()

    def wants_pixels(self) -> bool:
        """True, если для поиска понадобится снимок экрана (эвристика)."""
        for hwnd in enum_taskbars():
            if _HAS_UIA and self._fails.get(hwnd, 0) < 3:
                continue
            r = get_window_rect(hwnd)
            if not r:
                continue
            tb = QRect(int(r.left), int(r.top), int(r.right - r.left), int(r.bottom - r.top))
            cached = self._heur.get(hwnd)
            if cached is None or cached[0] != tb:
                return True
        return False

    def locate_all(self) -> list:
        """Список словарей: hwnd, rect (физ. пиксели), offscreen, source."""
        found = []
        for hwnd in enum_taskbars():
            r = get_window_rect(hwnd)
            if not r:
                continue
            tb = QRect(int(r.left), int(r.top), int(r.right - r.left), int(r.bottom - r.top))
            if tb.width() < 20 or tb.height() < 20:
                continue
            item = self._locate_one(hwnd, tb)
            if item is not None:
                found.append(item)
        if not found:
            self.source = "none"
        elif any(i["source"] == "heuristic" for i in found):
            self.source = "heuristic"
        else:
            self.source = "uia"
        return found

    # -- одна панель -----------------------------------------------------------
    def _locate_one(self, hwnd: int, tb: QRect):
        if _HAS_UIA and self._fails.get(hwnd, 0) < 3:
            rect = self._via_uia(hwnd)
            if rect is not None:
                return {"hwnd": hwnd, "rect": rect[0], "offscreen": rect[1], "source": "uia"}
        rect = self._fallback(hwnd, tb)
        if rect is None:
            return None
        return {"hwnd": hwnd, "rect": rect, "offscreen": False, "source": "heuristic"}

    # -- UIAutomation ----------------------------------------------------------
    def _via_uia(self, hwnd: int):
        try:
            btn = self._btns.get(hwnd)
            if btn is None:
                btn = self._find_via_uia(hwnd)
                if btn is None:      # кнопка не нашлась — тоже считаем попытку
                    self._fails[hwnd] = self._fails.get(hwnd, 0) + 1
                self._btns[hwnd] = btn
            if btn is None:
                return None
            r = btn.BoundingRectangle
            w = int(r.right - r.left)
            h = int(r.bottom - r.top)
            if w <= 8 or h <= 8:
                return None
            return QRect(int(r.left), int(r.top), w, h), bool(btn.IsOffscreen)
        except Exception as ex:
            # UIAutomation недоступна (например, в сборке нет comtypes) —
            # после нескольких попыток переходим на эвристику, без спама
            self._btns[hwnd] = None
            self._fails[hwnd] = self._fails.get(hwnd, 0) + 1
            self.last_error = f"{type(ex).__name__}: {ex}"[:200]
            return None

    def _find_via_uia(self, hwnd: int):
        try:
            auto.SetGlobalSearchTimeout(1.0)
        except Exception:
            pass
        taskbar = auto.ControlFromHandle(hwnd)
        if taskbar is None:
            return None
        for ctl, _depth in auto.WalkControl(taskbar, maxDepth=10):
            try:
                if ctl.ControlTypeName != "ButtonControl":
                    continue
                r = ctl.BoundingRectangle
                if (r.right - r.left) <= 8 or (r.bottom - r.top) <= 8:
                    continue
                name = (ctl.Name or "").strip().lower()
                aid = ctl.AutomationId or ""
                if aid == "StartButton" or name in START_NAMES:
                    return ctl
            except Exception:
                continue
        return None

    # -- эвристика -------------------------------------------------------------
    def _fallback(self, hwnd: int, tb: QRect):
        """Пиксельный поиск кнопки «Пуск» на панели задач. Кэшируется, пока
        панель не переехала."""
        cached = self._heur.get(hwnd)
        if cached is not None and cached[0] == tb:
            return cached[1]
        if tb.height() >= tb.width():  # панель задач сбоку
            h = tb.width()
            rect = QRect(tb.left(), tb.top(), h, int(h * 1.15))
            self._heur[hwnd] = (tb, rect)
            return rect
        h = tb.height()
        rect = None
        if self.hide_overlays:      # значки не должны попасть в снимок
            try:
                self.hide_overlays()
            except Exception:
                pass
        grab = ScreenGrab.capture(hwnd)
        if grab is not None:
            try:
                found = find_start_icon(grab.img, grab.to_img(tb))
            except Exception:
                found = None
            if found is not None:
                p, _bg = found
                pc = grab.to_phys_point(p)
                rect = QRect(pc.x() - h // 2, tb.top(), h, h)
        if rect is None:
            # последний резерв: системная кнопка «Пуск» на этом мониторе
            mon = monitor_rect_for(hwnd)
            if mon.width() < 16:
                mon = tb
            win11 = sys.getwindowsversion().build >= 22000
            if win11:
                cx = max(tb.left() + h, min(tb.right() - h, mon.center().x() - 2 * h))
                rect = QRect(int(cx - h / 2), tb.top(), h, h)
            else:
                rect = QRect(tb.left(), tb.top(), int(h * 1.15), h)
        self._heur[hwnd] = (tb, rect)
        return rect


# ------------------------------------------------- масштаб и карта мониторов
def screen_native_handle(screen) -> int:
    """HMONITOR экрана Qt (через QWindowsScreen); 0 — если недоступен."""
    try:
        ni = screen.nativeInterface()
        return int(ni.handle()) if ni is not None else 0
    except Exception:
        return 0


_monitor_map_cache = None


def monitor_map_refresh() -> None:
    """Сбрасывает кэш соответствия «экран Qt ↔ монитор Windows»."""
    global _monitor_map_cache
    _monitor_map_cache = None


def monitor_map() -> list:
    """[(QScreen, HMONITOR, QRect — физический прямоугольник монитора)]."""
    global _monitor_map_cache
    if _monitor_map_cache is not None:
        return _monitor_map_cache
    items = []
    try:
        for s in QApplication.screens():
            h = screen_native_handle(s)
            if not h:
                continue
            m = get_monitor_info_hmon(h).rcMonitor
            items.append((s, h, QRect(int(m.left), int(m.top),
                                      int(m.right - m.left), int(m.bottom - m.top))))
    except Exception:
        items = []
    _monitor_map_cache = items
    return items


def screen_at_physical(pt: QPoint, retry: bool = True):
    """(QScreen, QRect физического монитора) для точки; None — если не найдено."""
    for scr, _h, mon in monitor_map():
        if mon.contains(pt):
            return scr, mon
    if retry:                    # дисплеи могли поменяться — пересобрать карту
        monitor_map_refresh()
        return screen_at_physical(pt, retry=False)
    return None


def screen_for_monitor(mon: QRect):
    """QScreen, чей монитор совпадает с данным физическим прямоугольником."""
    for scr, _h, m in monitor_map():
        if m == mon:
            return scr
    monitor_map_refresh()
    for scr, _h, m in monitor_map():
        if m == mon:
            return scr
    return None


class ScreenGrab:
    """Снимок одного монитора + пересчёт «физические пиксели ↔ пиксели картинки».

    grabWindow() отдаёт картинку своего монитора; её размер может совпадать
    с физическим (обычно) или с логическим (при масштабировании) — коэффициент
    kx/ky учитывает оба случая.
    """

    def __init__(self, mon: QRect, img: QImage):
        self.mon = mon
        self.img = img
        self.kx = mon.width() / max(1, img.width())
        self.ky = mon.height() / max(1, img.height())

    @classmethod
    def capture(cls, hwnd: int):
        """Снимок монитора, на котором находится окно (панель задач)."""
        mon = monitor_rect_for(hwnd)
        if mon.width() < 16 or mon.height() < 16:
            return None
        scr = screen_for_monitor(mon)
        if scr is None:
            return None
        try:
            img = scr.grabWindow(0).toImage()
        except Exception:
            return None
        if img is None or img.isNull():
            return None
        return cls(mon, img)

    def to_img(self, phys: QRect) -> QRect:
        """Глобальный физический прямоугольник → пиксели картинки."""
        return QRect(round((phys.x() - self.mon.x()) / self.kx),
                     round((phys.y() - self.mon.y()) / self.ky),
                     max(1, round(phys.width() / self.kx)),
                     max(1, round(phys.height() / self.ky)))

    def to_phys(self, img_rect: QRect) -> QRect:
        """Пиксели картинки → глобальный физический прямоугольник."""
        return QRect(round(self.mon.x() + img_rect.x() * self.kx),
                     round(self.mon.y() + img_rect.y() * self.ky),
                     max(1, round(img_rect.width() * self.kx)),
                     max(1, round(img_rect.height() * self.ky)))

    def to_phys_point(self, img_pt: QPoint) -> QPoint:
        return QPoint(round(self.mon.x() + img_pt.x() * self.kx),
                      round(self.mon.y() + img_pt.y() * self.ky))


def physical_point_to_logical(pt: QPoint) -> QPoint | None:
    """Физические пиксели (UIA/WinAPI) → логические координаты Qt.

    У каждого монитора может быть свой масштаб (DPR), поэтому точку считаем
    от начала своего монитора.
    """
    hit = screen_at_physical(pt)
    if hit is not None:
        scr, mon = hit
        dpr = float(scr.devicePixelRatio() or 1.0)
        g = scr.geometry()
        return QPoint(round(g.x() + (pt.x() - mon.x()) / dpr),
                      round(g.y() + (pt.y() - mon.y()) / dpr))
    # запасной вариант: масштаб основного экрана (как было раньше)
    scr = QApplication.primaryScreen()
    if scr is None:
        return None
    dpr = float(scr.devicePixelRatio() or 1.0)
    if abs(dpr - 1.0) < 1e-6:
        return pt
    return QPoint(round(pt.x() / dpr), round(pt.y() / dpr))


def physical_to_logical(rect: QRect) -> QRect | None:
    """Перевод прямоугольника по двум точкам — на стыке мониторов не поедет."""
    tl = physical_point_to_logical(rect.topLeft())
    br = physical_point_to_logical(QPoint(rect.x() + rect.width(),
                                          rect.y() + rect.height()))
    if tl is None or br is None:
        return None
    return QRect(tl.x(), tl.y(), max(1, br.x() - tl.x()), max(1, br.y() - tl.y()))


def analyze_button_region(img: QImage, phys_rect: QRect):
    """Анализирует пиксели кнопки «Пуск» на скриншоте.

    Возвращает (QPoint — реальный центр значка, (r, g, b) — цвет фона панели
    задач под кнопкой) либо None. Кнопка в UIA-разметке часто шире значка,
    поэтому берём пиксели панели и находим область, отличающуюся от фона.
    """
    if img is None or img.isNull():
        return None
    r = phys_rect.intersected(QRect(0, 0, img.width(), img.height()))
    if r.width() < 16 or r.height() < 16:
        return None

    # фон — медианный цвет пикселей по периметру кнопки
    samples = []
    sx = max(2, r.width() // 24)
    sy = max(2, r.height() // 12)
    for x in range(r.left() + 2, r.right() - 1, sx):
        c = img.pixelColor(x, r.top() + 2)
        samples.append((c.red(), c.green(), c.blue()))
        c = img.pixelColor(x, r.bottom() - 3)
        samples.append((c.red(), c.green(), c.blue()))
    for y in range(r.top() + 2, r.bottom() - 1, sy):
        c = img.pixelColor(r.left() + 2, y)
        samples.append((c.red(), c.green(), c.blue()))
        c = img.pixelColor(r.right() - 3, y)
        samples.append((c.red(), c.green(), c.blue()))
    if not samples:
        return None
    samples.sort(key=lambda c: c[0] + c[1] + c[2])
    bg = samples[len(samples) // 2]

    xs, ys = [], []
    inset = 4
    for y in range(r.top() + inset, r.bottom() - inset):
        for x in range(r.left() + inset, r.right() - inset):
            c = img.pixelColor(x, y)
            d = abs(c.red() - bg[0]) + abs(c.green() - bg[1]) + abs(c.blue() - bg[2])
            if d > 96:
                xs.append(x)
                ys.append(y)
    if len(xs) < 20:
        return None
    minx, maxx = min(xs), max(xs)
    miny, maxy = min(ys), max(ys)
    bw, bh = maxx - minx + 1, maxy - miny + 1
    if bw < 8 or bh < 8 or bw > r.width() or bh > r.height():
        return None
    return QPoint((minx + maxx) // 2, (miny + maxy) // 2), bg


def find_start_icon(img: QImage, tb_img: QRect):
    """Эвристика без UIA: центр значка «Пуск» по пикселям панели задач.

    tb_img — прямоугольник панели в пикселях картинки. Возвращает
    (QPoint — центр значка в пикселях картинки, (r, g, b) — цвет панели)
    или None.

    Панель выровнена по левому краю (тогда «Пуск» — первый значок) или по
    центру, как в Windows 11 (тогда «Пуск» — первый значок центральной
    группы). Одинокий виджет в левом углу (погода) пропускается.
    """
    if img is None or img.isNull():
        return None
    r = tb_img.intersected(QRect(0, 0, img.width(), img.height()))
    if r.width() < 32 or r.height() < 16:
        return None

    # фон — медианный цвет по верхнему/нижнему краю панели
    samples = []
    step_x = max(2, r.width() // 60)
    for x in range(r.left() + 2, r.right() - 1, step_x):
        for yy in (r.top() + 2, r.bottom() - 3):
            c = img.pixelColor(x, yy)
            samples.append((c.red(), c.green(), c.blue()))
    if not samples:
        return None
    samples.sort(key=lambda c: c[0] + c[1] + c[2])
    bg = samples[len(samples) // 2]

    # столбцы, где есть пиксели, заметно отличающиеся от фона
    inset_x = max(3, r.height() // 12)
    inset_y = max(4, r.height() // 8)
    step = 2
    need = max(3, ((r.bottom() - inset_y) - (r.top() + inset_y)) // (8 * step))
    columns = []
    for x in range(r.left() + inset_x, r.right() - inset_x, step):
        cnt = 0
        for y in range(r.top() + inset_y, r.bottom() - inset_y, step):
            c = img.pixelColor(x, y)
            d = abs(c.red() - bg[0]) + abs(c.green() - bg[1]) + abs(c.blue() - bg[2])
            if d > 96:
                cnt += 1
                if cnt >= need:
                    break
        columns.append(cnt >= need)

    # склеиваем столбцы в группы — значки и кнопки (разрывы до ~3 шагов внутри)
    clusters = []
    i, n = 0, len(columns)
    while i < n:
        if not columns[i]:
            i += 1
            continue
        j = i
        while True:
            nxt = None
            for k in (1, 2, 3):
                if j + k < n and columns[j + k]:
                    nxt = j + k
                    break
            if nxt is None:
                break
            j = nxt
        clusters.append([r.left() + inset_x + i * step, r.left() + inset_x + j * step])
        i = j + 1
    if not clusters:
        return None
    clusters = [c for c in clusters if c[1] - c[0] >= 2]   # одиночные столбцы — шум
    if not clusters:
        return None

    # группы значков: разрыв больше ~1.5 высоты панели разделяет группы
    big_gap = r.height() * 1.5
    groups = [[clusters[0]]]
    for prev, cur in zip(clusters, clusters[1:]):
        if cur[0] - prev[1] > big_gap:
            groups.append([cur])
        else:
            groups[-1].append(cur)

    # у Windows 11 слева может одиноко стоять виджет погоды — отдельная
    # маленькая группа у самого края; «Пуск» — первая группа после неё
    if len(groups) >= 2:
        g0, g1 = groups[0], groups[1]
        near_left = (g0[0][0] - (r.left() + inset_x)) <= r.height()
        small = len(g0) <= 5 and (g0[-1][1] - g0[0][0]) <= r.height() * 4.5
        g1_near_right = (r.right() - inset_x - g1[-1][1]) <= r.height() * 2
        if near_left and small and not g1_near_right:
            groups = groups[1:]

    start_x, end_x = groups[0][0]
    max_w = int(r.height() * 1.6)       # ширина кнопки «Пуск» ~ высота панели
    if end_x - start_x > max_w:
        end_x = start_x + max_w
    cx = (start_x + end_x) // 2
    cy = (r.top() + r.bottom()) // 2
    return QPoint(cx, cy), bg


# ------------------------------------------------------------ отрисовка картинки
def render_icon(path: str, size_px: int, bg=None, shape: str = "rounded") -> QImage | None:
    """Загружает картинку (в т.ч. SVG) в квадрат size_px с выбранной формой.

    bg — цвет подложки (панели задач): заполняет прозрачные области картинки,
    чтобы сквозь них не просвечивал настоящий значок «Пуск».
    shape: rounded — скруглённый квадрат, circle — круг, square — квадрат.
    """
    size_px = max(8, int(size_px))
    if not path or not os.path.exists(path):
        path = DEFAULT_SVG
    try:
        if path.lower().endswith(".svg"):
            renderer = QSvgRenderer(path)
            if not renderer.isValid():
                return None
            src = QImage(size_px, size_px, QImage.Format.Format_ARGB32_Premultiplied)
            src.fill(Qt.GlobalColor.transparent)
            p = QPainter(src)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            ds = renderer.defaultSize()
            if ds.isValid() and ds.width() > 0 and ds.height() > 0:
                k = min(size_px / ds.width(), size_px / ds.height())
                w = max(1, round(ds.width() * k))
                h = max(1, round(ds.height() * k))
                renderer.render(p, QRectF((size_px - w) / 2, (size_px - h) / 2, w, h))
            else:
                renderer.render(p, QRectF(0, 0, size_px, size_px))
            p.end()
        else:
            src = QImage(path)
            if src.isNull():
                return None
            src = src.convertToFormat(QImage.Format.Format_ARGB32)
            src = src.scaled(size_px, size_px,
                             Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                             Qt.TransformationMode.SmoothTransformation)
            x = (src.width() - size_px) // 2
            y = (src.height() - size_px) // 2
            src = src.copy(x, y, size_px, size_px)
    except Exception:
        return None

    out = QImage(size_px, size_px, QImage.Format.Format_ARGB32_Premultiplied)
    if bg is not None:
        out.fill(QColor(int(bg[0]), int(bg[1]), int(bg[2])))
    else:
        out.fill(Qt.GlobalColor.transparent)
    p = QPainter(out)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    clip = QPainterPath()
    if shape == "circle":
        clip.addEllipse(QRectF(0, 0, size_px, size_px))
    elif shape == "square":
        clip.addRect(QRectF(0, 0, size_px, size_px))
    else:
        r = size_px * 0.22
        clip.addRoundedRect(QRectF(0, 0, size_px, size_px), r, r)
    p.setClipPath(clip)
    p.drawImage(0, 0, src)
    p.end()
    return out


def icon_pixmap(path: str, logical_size: int, dpr: float | None = None, bg=None,
                shape: str = "rounded") -> QPixmap | None:
    if dpr is None:
        scr = QApplication.primaryScreen()
        dpr = float(scr.devicePixelRatio() if scr else 1.0) or 1.0
    dev = max(8, int(round(logical_size * dpr)))
    img = render_icon(path, dev, bg, shape)
    if img is None:
        return None
    pm = QPixmap.fromImage(img)
    pm.setDevicePixelRatio(dpr)
    return pm


# --------------------------------------------------------------------- оверлей
BASE_FLAGS = (Qt.WindowType.FramelessWindowHint | Qt.WindowType.Tool |
              Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.WindowTransparentForInput |
              Qt.WindowType.WindowDoesNotAcceptFocus)


class Overlay(QWidget):
    """Прозрачное окно поверх кнопки «Пуск». Клики проходят сквозь него."""

    def __init__(self, on_calibrated=None):
        super().__init__(None, BASE_FLAGS)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setWindowTitle("IconsPo Overlay")
        self._pm: QPixmap | None = None
        self._key = None
        self.calibration = False
        self._drag_from = None
        self.on_calibrated = on_calibrated   # callback(widget, QRect) — новый прямоугольник

    # -- картинка --------------------------------------------------------------
    def set_icon_source(self, path: str, logical_size: int, bg=None, shape: str = "rounded",
                        dpr: float | None = None) -> None:
        """dpr — масштаб монитора, на котором будет показан значок. Раньше
        брали devicePixelRatioF() самого оверлея, но до показа он равен dpr
        основного экрана — на втором мониторе с другим масштабом получалось
        мыло."""
        if dpr is None:
            dpr = float(self.devicePixelRatioF() or 1.0)
        dpr = float(dpr or 1.0)
        key = (path, int(logical_size), dpr, tuple(bg) if bg else None, shape)
        if key == self._key and self._pm is not None:
            return
        pm = icon_pixmap(path, logical_size, dpr, bg, shape)
        if pm is None:
            return
        self._pm = pm
        self._key = key
        self.update()

    # -- режим калибровки (перетаскивание мышью) -------------------------------
    def set_calibration(self, on: bool) -> None:
        if on == self.calibration:
            return
        self.calibration = on
        visible = self.isVisible()
        flags = BASE_FLAGS
        if on:
            flags &= ~Qt.WindowType.WindowTransparentForInput
        self.setWindowFlags(flags | Qt.WindowType.WindowStaysOnTopHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, not on)
        if visible:
            self.show()
            self.raise_()

    def _interactive_topmost(self):
        """Поднять окно поверх панели задач (без активации)."""
        try:
            user32.SetWindowPos(wintypes.HWND(int(self.winId())),
                                wintypes.HWND(HWND_TOPMOST),
                                0, 0, 0, 0, SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        except Exception:
            pass

    def _raise_now(self) -> bool:
        """Ставит окно на самый верх (панель задач любит залезать при кликах).

        HWND_TOPMOST сам по себе порядок среди topmost-окон не меняет, поэтому
        сначала ставим HWND_TOP, затем подтверждаем «поверх всех».
        """
        if not self.isVisible():
            return False
        try:
            h = wintypes.HWND(int(self.winId()))
            flags = SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE
            ok1 = user32.SetWindowPos(h, wintypes.HWND(HWND_TOP), 0, 0, 0, 0, flags)
            ok2 = user32.SetWindowPos(h, wintypes.HWND(HWND_TOPMOST), 0, 0, 0, 0, flags)
            if not ok1 and not ok2:
                app_log("окно не удалось поднять (код %d)" % ctypes.get_last_error())
            return True
        except Exception as ex:
            app_log("ошибка подъёма окна: %r" % (ex,))
            return False

    # -- события ---------------------------------------------------------------
    def showEvent(self, e):
        super().showEvent(e)
        self.raise_()
        self._interactive_topmost()

    def paintEvent(self, _e):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if self._pm is not None:
            p.drawPixmap(0, 0, self.width(), self.height(), self._pm)
        if self.calibration:
            pen = QPen(QColor(255, 255, 255, 170))
            pen.setWidth(2)
            pen.setStyle(Qt.PenStyle.DashLine)
            p.setPen(pen)
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(1, 1, self.width() - 3, self.height() - 3)

    def mousePressEvent(self, e):
        if self.calibration and e.button() == Qt.MouseButton.LeftButton:
            self._drag_from = e.globalPosition().toPoint() - self.frameGeometry().topLeft()
            e.accept()
        else:
            e.ignore()

    def mouseMoveEvent(self, e):
        if self.calibration and self._drag_from is not None:
            self.move(e.globalPosition().toPoint() - self._drag_from)
            e.accept()

    def mouseReleaseEvent(self, e):
        if self.calibration and self._drag_from is not None:
            self._drag_from = None
            if self.on_calibrated:
                self.on_calibrated(self, self.frameGeometry())
            e.accept()


class OverlayHost:
    """Значок на одной панели задач: окно-оверлей + кэш найденной кнопки.

    Приложение держит по одному «хозяину» на каждую панель задач (то есть на
    каждый монитор), чтобы значок был на всех кнопках «Пуск» сразу.
    """

    def __init__(self, hwnd: int, overlay: "Overlay"):
        self.hwnd = hwnd
        self.overlay = overlay
        self.base_center = None        # чистая середина кнопки (логические пиксели)
        self.last_geo = None           # геометрия оверлея с учётом сдвига
        self._btn_key = None           # (x, y, w, h) кнопки в физ. пикселях
        self._icon_center_phys = None  # реальный центр значка на кнопке
        self._taskbar_bg = None        # цвет панели задач под значком
        self._shown_geo = None         # что реально выставлено у окна

    def close(self) -> None:
        try:
            self.overlay.close()
            self.overlay.deleteLater()
        except Exception:
            pass


# ------------------------------------------------------------------- оформление
_PALETTES = {
    "dark": {
        "label": "#E8EBF1", "title": "#F4F6FB", "subtitle": "#8A92A6",
        "caption": "#7C8497", "value": "#B9C1D4",
        "root_bg": "#14151A", "root_border": "#272A33",
        "card_bg": "#191B21", "card_border": "#242732",
        "preview_bg": "#0D0F13", "preview_border": "#23262F",
        "gal_bg": "#121419", "gal_border": "#262A34",
        "gal_hover_bg": "#1A1E27", "gal_hover_border": "#363B48",
        "gal_checked_bg": "#202A44",
        "scroll_handle": "#2E3340", "scroll_hover": "#3A4050",
        "btn_bg": "#1E2129", "btn_border": "#2C303B", "btn_text": "#E8EBF1",
        "btn_hover_bg": "#252932", "btn_hover_border": "#363B48",
        "btn_pressed": "#1A1D24", "btn_disabled": "#5C6373",
        "accent": "#4C7DFF", "accent_hover": "#5D8BFF", "accent_pressed": "#3F6EE6",
        "winbtn_text": "#9AA3B5", "winbtn_hover_bg": "#23262E", "winbtn_hover_text": "#FFFFFF",
        "danger": "#E5484D",
        "nudge_text": "#C6CDDB", "nudge_hover_bg": "#272B35",
        "nudge_checked_bg": "#2A3350", "nudge_checked_text": "#AFC6FF",
        "preset_checked_bg": "#232C44",
        "combo_bg": "#1E2129", "combo_border": "#2C303B", "combo_text": "#E8EBF1",
        "combo_view_bg": "#17191F", "combo_view_border": "#2A2E38", "combo_sel": "#262B36",
        "slider_groove": "#30353F", "slider_handle_bg": "#E8EBF1", "slider_handle_hover": "#FFFFFF",
        "slider_sub_disabled": "#3A3F4B", "slider_handle_disabled": "#6A7180",
        "slider_handle_disabled_border": "#454B59",
        "check_text": "#C9CFDB", "check_ind_bg": "#1A1D23", "check_ind_border": "#333845",
        "menu_bg": "#17191F", "menu_border": "#2A2E38", "menu_text": "#E8EBF1",
        "menu_sel": "#262B36", "menu_sep": "#262B36",
        "tooltip_bg": "#17191F", "tooltip_text": "#E8EBF1", "tooltip_border": "#2A2E38",
    },
    "light": {
        "label": "#2A2F3A", "title": "#14161B", "subtitle": "#6E7686",
        "caption": "#79808F", "value": "#4E5563",
        "root_bg": "#F4F5F8", "root_border": "#DCDFE6",
        "card_bg": "#FBFCFE", "card_border": "#E4E7ED",
        "preview_bg": "#EDEFF3", "preview_border": "#E1E5EB",
        "gal_bg": "#F0F2F6", "gal_border": "#E2E5EB",
        "gal_hover_bg": "#E7EAF0", "gal_hover_border": "#C9CFDA",
        "gal_checked_bg": "#E3EAFF",
        "scroll_handle": "#C6CBD4", "scroll_hover": "#AEB5C2",
        "btn_bg": "#FFFFFF", "btn_border": "#D8DCE3", "btn_text": "#2A2F3A",
        "btn_hover_bg": "#F3F5F9", "btn_hover_border": "#C6CCD6",
        "btn_pressed": "#EAEDF2", "btn_disabled": "#A6ACB8",
        "accent": "#4C7DFF", "accent_hover": "#5D8BFF", "accent_pressed": "#3F6EE6",
        "winbtn_text": "#5A6172", "winbtn_hover_bg": "#E8EAEF", "winbtn_hover_text": "#14161B",
        "danger": "#E5484D",
        "nudge_text": "#3A404C", "nudge_hover_bg": "#EEF1F5",
        "nudge_checked_bg": "#E1E9FF", "nudge_checked_text": "#3558C9",
        "preset_checked_bg": "#E6EDFF",
        "combo_bg": "#FFFFFF", "combo_border": "#D8DCE3", "combo_text": "#2A2F3A",
        "combo_view_bg": "#FFFFFF", "combo_view_border": "#D8DCE3", "combo_sel": "#EEF1F5",
        "slider_groove": "#D7DBE2", "slider_handle_bg": "#FFFFFF", "slider_handle_hover": "#EEF3FF",
        "slider_sub_disabled": "#C9CED8", "slider_handle_disabled": "#C6CBD4",
        "slider_handle_disabled_border": "#B9C0CC",
        "check_text": "#3A404C", "check_ind_bg": "#FFFFFF", "check_ind_border": "#C9CFDA",
        "menu_bg": "#FFFFFF", "menu_border": "#D8DCE3", "menu_text": "#2A2F3A",
        "menu_sel": "#EEF1F5", "menu_sep": "#ECEEF2",
        "tooltip_bg": "#FFFFFF", "tooltip_text": "#2A2F3A", "tooltip_border": "#D8DCE3",
    },
}

_QSS_TEMPLATE = Template("""
* { font-family: "Segoe UI", "Segoe UI Variable Text", sans-serif; }
QLabel { color: $label; }
QLabel#Title { font-size: 15px; font-weight: 600; color: $title; }
QLabel#Subtitle { font-size: 11px; color: $subtitle; }
QLabel#CardCaption { font-size: 11px; color: $caption; }
QLabel#Value { font-size: 11px; color: $value; }
QWidget#TitleBar { background: transparent; }
QFrame#Root {
    background: $root_bg;
    border: 1px solid $root_border;
    border-radius: 14px;
}
QFrame#Card {
    background: $card_bg;
    border: 1px solid $card_border;
    border-radius: 12px;
}
QFrame#Preview {
    background: $preview_bg;
    border: 1px solid $preview_border;
    border-radius: 12px;
}
QPushButton#Gallery {
    background: $gal_bg;
    border: 1px solid $gal_border;
    border-radius: 11px;
    padding: 4px;
}
QPushButton#Gallery:hover { background: $gal_hover_bg; border-color: $gal_hover_border; }
QPushButton#Gallery:checked { border: 1px solid $accent; background: $gal_checked_bg; }
QScrollArea#Gallery, QScrollArea#Gallery > QWidget > QWidget { background: transparent; border: none; }
QScrollBar:horizontal { background: transparent; height: 6px; margin: 0 1px; }
QScrollBar::handle:horizontal { background: $scroll_handle; border-radius: 3px; min-width: 28px; }
QScrollBar::handle:horizontal:hover { background: $scroll_hover; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; height: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
QPushButton {
    background: $btn_bg;
    border: 1px solid $btn_border;
    color: $btn_text;
    border-radius: 9px;
    padding: 8px 14px;
    font-size: 12px;
}
QPushButton:hover { background: $btn_hover_bg; border-color: $btn_hover_border; }
QPushButton:pressed { background: $btn_pressed; }
QPushButton:disabled { color: $btn_disabled; }
QPushButton#Accent {
    background: $accent; border: 1px solid $accent; color: #FFFFFF; font-weight: 600;
}
QPushButton#Accent:hover { background: $accent_hover; border-color: $accent_hover; }
QPushButton#Accent:pressed { background: $accent_pressed; }
QPushButton#WinBtn {
    background: transparent; border: none; color: $winbtn_text;
    border-radius: 7px; font-size: 13px; padding: 2px 10px;
}
QPushButton#WinBtn:hover { background: $winbtn_hover_bg; color: $winbtn_hover_text; }
QPushButton#WinBtnClose:hover { background: $danger; color: #FFFFFF; }
QPushButton#Nudge {
    background: $btn_bg; border: 1px solid $btn_border; border-radius: 8px;
    padding: 0px; font-size: 13px; color: $nudge_text; min-width: 34px; min-height: 26px;
}
QPushButton#Nudge:hover { background: $nudge_hover_bg; }
QPushButton#Nudge:checked { background: $nudge_checked_bg; border-color: $accent; color: $nudge_checked_text; }
QPushButton#Small { padding: 5px 10px; font-size: 11px; }
QPushButton#Preset { padding: 5px; border-radius: 10px; }
QPushButton#Preset:checked { border-color: $accent; background: $preset_checked_bg; }
QComboBox {
    background: $combo_bg; border: 1px solid $combo_border; border-radius: 8px;
    padding: 5px 10px; color: $combo_text; font-size: 12px; min-width: 150px;
}
QComboBox:hover { border-color: $btn_hover_border; }
QComboBox QAbstractItemView {
    background: $combo_view_bg; border: 1px solid $combo_view_border; color: $combo_text;
    selection-background-color: $combo_sel; outline: 0;
}
QSlider::groove:horizontal { height: 4px; background: $slider_groove; border-radius: 2px; }
QSlider::sub-page:horizontal { background: $accent; border-radius: 2px; }
QSlider::handle:horizontal {
    width: 16px; height: 16px; margin: -7px 0; border-radius: 8px;
    background: $slider_handle_bg; border: 2px solid $accent;
}
QSlider::handle:horizontal:hover { background: $slider_handle_hover; }
QSlider::sub-page:horizontal:disabled { background: $slider_sub_disabled; }
QSlider::handle:horizontal:disabled { background: $slider_handle_disabled; border-color: $slider_handle_disabled_border; }
QCheckBox { color: $check_text; font-size: 12px; spacing: 9px; }
QCheckBox::indicator {
    width: 18px; height: 18px; border-radius: 6px;
    background: $check_ind_bg; border: 1px solid $check_ind_border;
}
QCheckBox::indicator:hover { border-color: $accent; }
QCheckBox::indicator:checked { background: $accent; border-color: $accent; }
QMenu {
    background: $menu_bg; border: 1px solid $menu_border; border-radius: 8px;
    padding: 6px; color: $menu_text; font-size: 12px;
}
QMenu::item { padding: 7px 26px 7px 12px; border-radius: 6px; }
QMenu::item:selected { background: $menu_sel; }
QMenu::separator { height: 1px; background: $menu_sep; margin: 6px 8px; }
QMessageBox { background: $root_bg; }
QMessageBox QLabel { color: $label; }
QToolTip {
    background: $tooltip_bg; color: $tooltip_text; border: 1px solid $tooltip_border; padding: 4px 8px;
}
""")


def build_qss(theme: str) -> str:
    pal = _PALETTES.get(str(theme or "dark")) or _PALETTES["dark"]
    return _QSS_TEMPLATE.substitute(**pal)


def apply_qss(qt_app: QApplication, theme: str) -> None:
    qt_app.setStyleSheet(build_qss(theme))


# ---------------------------------------------------------------- окно настроек
class _TitleBar(QWidget):
    """Полоса заголовка с перетаскиванием окна."""

    def __init__(self, win: QWidget):
        super().__init__(win)
        self.setObjectName("TitleBar")
        self.win = win
        self._press = None

    def mousePressEvent(self, e):
        if e.button() == Qt.MouseButton.LeftButton:
            self._press = e.globalPosition().toPoint() - self.win.frameGeometry().topLeft()
            e.accept()

    def mouseMoveEvent(self, e):
        if self._press is not None and (e.buttons() & Qt.MouseButton.LeftButton):
            self.win.move(e.globalPosition().toPoint() - self._press)
            e.accept()

    def mouseReleaseEvent(self, e):
        self._press = None
        e.accept()


class SettingsWindow(QWidget):
    """Тёмное окно настроек."""

    def __init__(self, app):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.Window)
        self.app = app
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle(APP_TITLE + " — " + tr("settings_title"))
        self.setFixedWidth(470)
        if os.path.exists(ICON_ICO):
            self.setWindowIcon(QIcon(ICON_ICO))

        root = QFrame(self)
        root.setObjectName("Root")
        self._shadow = QGraphicsDropShadowEffect(self)
        self._shadow.setOffset(0, 8)
        root.setGraphicsEffect(self._shadow)
        self._apply_theme_ui()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(20, 20, 20, 20)
        outer.addWidget(root)

        v = QVBoxLayout(root)
        v.setContentsMargins(0, 0, 0, 0)
        v.setSpacing(0)
        v.addWidget(self._build_titlebar())

        body = QVBoxLayout()
        body.setContentsMargins(18, 2, 18, 16)
        body.setSpacing(10)
        v.addLayout(body)

        body.addWidget(self._build_preview())
        body.addWidget(self._build_image_card())
        body.addWidget(self._build_size_card())
        body.addWidget(self._build_offset_card())
        body.addWidget(self._build_switches())
        body.addWidget(self._build_interface_card())
        body.addStretch(1)

        self.retranslate()
        self.refresh_preview()
        self.adjustSize()
        self.setFixedHeight(self.height())

    # -- шапка -----------------------------------------------------------------
    def _build_titlebar(self) -> _TitleBar:
        tb = _TitleBar(self)
        h = QHBoxLayout(tb)
        h.setContentsMargins(18, 14, 10, 8)
        h.setSpacing(10)

        logo = QLabel()
        pm = icon_pixmap(DEFAULT_SVG, 22)
        if pm is not None:
            logo.setPixmap(pm)
        h.addWidget(logo)

        titles = QVBoxLayout()
        titles.setSpacing(0)
        t = QLabel(APP_TITLE)
        t.setObjectName("Title")
        self.lbl_sub = QLabel(tr("app_sub"))
        self.lbl_sub.setObjectName("Subtitle")
        titles.addWidget(t)
        titles.addWidget(self.lbl_sub)
        h.addLayout(titles)
        h.addStretch(1)

        lbl_ver = QLabel("v" + APP_VERSION)
        lbl_ver.setObjectName("Subtitle")
        h.addWidget(lbl_ver)
        h.addSpacing(4)

        btn_close = QPushButton("✕")
        btn_close.setObjectName("WinBtn")
        btn_close.setProperty("class", "close")
        btn_close.setFixedSize(30, 26)
        btn_close.setToolTip(tr("close_tip"))
        btn_close.clicked.connect(self.hide)
        h.addWidget(btn_close)
        self.btn_close = btn_close
        return tb

    # -- предпросмотр ------------------------------------------------------------
    def _build_preview(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Preview")
        card.setFixedHeight(88)

        v = QVBoxLayout(card)
        v.setContentsMargins(10, 8, 10, 6)
        v.setSpacing(0)

        self.gal_scroll = QScrollArea()
        self.gal_scroll.setObjectName("Gallery")
        self.gal_scroll.setWidgetResizable(True)
        self.gal_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.gal_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.gal_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.gal_scroll.viewport().installEventFilter(self)

        inner = QWidget()
        self.gal_layout = QHBoxLayout(inner)
        self.gal_layout.setContentsMargins(2, 2, 2, 2)
        self.gal_layout.setSpacing(8)
        self.gal_layout.addStretch(1)
        self.gal_scroll.setWidget(inner)
        v.addWidget(self.gal_scroll)
        return card

    def eventFilter(self, obj, ev):
        """Колесо мыши над галереей прокручивает её вбок."""
        if obj is self.gal_scroll.viewport() and ev.type() == QEvent.Type.Wheel:
            sb = self.gal_scroll.horizontalScrollBar()
            sb.setValue(sb.value() - ev.angleDelta().y())
            return True
        return super().eventFilter(obj, ev)

    def _rebuild_gallery(self):
        """Перерисовывает галерею: стандартный значок + все выбранные."""
        if not hasattr(self, "gal_layout"):
            return
        while self.gal_layout.count() > 1:          # остаётся только stretch
            item = self.gal_layout.takeAt(0)
            w = item.widget()
            if w is not None:
                w.deleteLater()

        paths = [""] + [p for p in (self.app.cfg.get("history") or []) if p]
        current = str(self.app.cfg.get("image") or "")
        seen = set()
        for p in paths:
            if p in seen:
                continue
            seen.add(p)
            if p and not resolve_media(p):
                continue
            b = QPushButton()
            b.setObjectName("Gallery")
            b.setCheckable(True)
            b.setFixedSize(46, 46)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setChecked(p == current)
            if not p:
                tip = tr("default_icon_tip")
            elif p.startswith(PRESET_PREFIX):
                tip = preset_title(p[len(PRESET_PREFIX):])
            else:
                base = os.path.splitext(os.path.basename(p))[0]
                if base.startswith("preset_"):
                    tip = preset_title(base[7:])
                else:
                    tip = os.path.basename(p)
            b.setToolTip(tip)
            pm = icon_pixmap(resolve_media(p) or DEFAULT_SVG, 34,
                             shape=self.app.cfg.get("shape", "rounded"))
            if pm is not None:
                b.setIcon(QIcon(pm))
                b.setIconSize(QSize(34, 34))
            b.clicked.connect(lambda _=False, path=p: self.app.set_image_from_history(path))
            self.gal_layout.insertWidget(self.gal_layout.count() - 1, b)

    # -- карточки ----------------------------------------------------------------
    def _build_image_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(9)

        self.cap_image = QLabel(tr("card_image"))
        self.cap_image.setObjectName("CardCaption")
        v.addWidget(self.cap_image)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_pick = QPushButton(tr("btn_pick"))
        self.btn_pick.setObjectName("Accent")
        self.btn_pick.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_pick.setToolTip(tr("btn_pick_tip"))
        self.btn_pick.clicked.connect(lambda: self.app.pick_image())
        self.btn_reset = QPushButton(tr("btn_default"))
        self.btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reset.clicked.connect(lambda: self.app.reset_image())
        row.addWidget(self.btn_pick, 1)
        row.addWidget(self.btn_reset)
        v.addLayout(row)

        prow = QHBoxLayout()
        prow.setSpacing(8)
        self.cap_presets = QLabel(tr("cap_presets"))
        self.cap_presets.setObjectName("CardCaption")
        prow.addWidget(self.cap_presets)
        self.preset_buttons = {}
        for key, tip in preset_files():
            b = QPushButton()
            b.setObjectName("Preset")
            b.setCheckable(True)
            b.setFixedSize(44, 44)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(tip + tr("preset_tip_suffix"))
            pm = icon_pixmap(os.path.join(PRESETS_DIR, key + ".svg"), 30)
            if pm is not None:
                b.setIcon(QIcon(pm))
                b.setIconSize(QSize(30, 30))
            b.clicked.connect(lambda _=False, k=key: self.app.apply_preset(k))
            self.preset_buttons[key] = b
            prow.addWidget(b)
        prow.addStretch(1)
        v.addLayout(prow)
        return card

    def _build_size_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 12, 14, 14)
        v.setSpacing(9)

        row = QHBoxLayout()
        self.cap_size = QLabel(tr("card_size"))
        self.cap_size.setObjectName("CardCaption")
        row.addWidget(self.cap_size)
        row.addStretch(1)
        self.lbl_size = QLabel(tr("auto"))
        self.lbl_size.setObjectName("Value")
        row.addWidget(self.lbl_size)
        v.addLayout(row)

        row2 = QHBoxLayout()
        row2.setSpacing(10)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(16, 56)
        self.slider.setPageStep(2)
        self.slider.valueChanged.connect(self._on_slider)
        row2.addWidget(self.slider, 1)
        self.chk_auto = QCheckBox(tr("auto"))
        self.chk_auto.toggled.connect(self._on_auto)
        row2.addWidget(self.chk_auto)
        v.addLayout(row2)

        row3 = QHBoxLayout()
        row3.setSpacing(8)
        self.cap_shape = QLabel(tr("card_shape"))
        self.cap_shape.setObjectName("CardCaption")
        row3.addWidget(self.cap_shape)
        row3.addStretch(1)
        self.cmb_shape = QComboBox()
        self.cmb_shape.addItem(tr("shape_rounded"), "rounded")
        self.cmb_shape.addItem(tr("shape_circle"), "circle")
        self.cmb_shape.addItem(tr("shape_square"), "square")
        self.cmb_shape.currentIndexChanged.connect(self._on_shape)
        row3.addWidget(self.cmb_shape)
        v.addLayout(row3)
        return card

    def _on_shape(self, _idx: int):
        self.app.set_shape(str(self.cmb_shape.currentData() or "rounded"))

    def _build_offset_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        h = QHBoxLayout(card)
        h.setContentsMargins(14, 12, 14, 12)
        h.setSpacing(14)

        left = QVBoxLayout()
        left.setSpacing(3)
        self.cap_offset = QLabel(tr("card_offset"))
        self.cap_offset.setObjectName("CardCaption")
        left.addWidget(self.cap_offset)
        self.lbl_off = QLabel(offset_text(0, 0))
        self.lbl_off.setObjectName("Value")
        left.addWidget(self.lbl_off)
        left.addStretch(1)
        h.addLayout(left, 1)

        grid = QGridLayout()
        grid.setSpacing(4)
        for text, dx, dy, r, c in (("↑", 0, -1, 0, 1), ("←", -1, 0, 1, 0),
                                   ("→", 1, 0, 1, 2), ("↓", 0, 1, 2, 1)):
            b = QPushButton(text)
            b.setObjectName("Nudge")
            b.setAutoRepeat(True)
            b.setAutoRepeatDelay(400)
            b.setAutoRepeatInterval(60)
            b.clicked.connect(lambda _=False, x=dx, y=dy: self.app.nudge(x, y))
            grid.addWidget(b, r, c)
        center = QLabel("◎")
        center.setAlignment(Qt.AlignmentFlag.AlignCenter)
        center.setStyleSheet("color:#4C7DFF; font-size:14px;")
        grid.addWidget(center, 1, 1)
        h.addLayout(grid)

        col = QVBoxLayout()
        col.setSpacing(6)
        self.btn_off_reset = QPushButton(tr("btn_reset"))
        self.btn_off_reset.setObjectName("Small")
        self.btn_off_reset.clicked.connect(lambda: self.app.reset_offset())
        self.btn_calib = QPushButton(tr("btn_calib"))
        self.btn_calib.setObjectName("Small")
        self.btn_calib.setCheckable(True)
        self.btn_calib.setToolTip(tr("btn_calib_tip"))
        self.btn_calib.toggled.connect(lambda on: self.app.set_calibration(on))
        col.addWidget(self.btn_off_reset)
        col.addWidget(self.btn_calib)
        col.addStretch(1)
        h.addLayout(col)
        return card

    def _build_switches(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        card.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(10)

        self.cap_behaviour = QLabel(tr("card_behaviour"))
        self.cap_behaviour.setObjectName("CardCaption")
        v.addWidget(self.cap_behaviour)

        self.chk_enabled = QCheckBox(tr("chk_enabled"))
        self.chk_enabled.toggled.connect(lambda b: self.app.set_enabled(b))
        v.addWidget(self.chk_enabled)

        self.chk_autostart = QCheckBox(tr("chk_autostart"))
        self.chk_autostart.toggled.connect(self._on_autostart)
        v.addWidget(self.chk_autostart)
        self.lbl_autostart_note = QLabel(tr("autostart_note_none"))
        self.lbl_autostart_note.setObjectName("Hint")
        v.addWidget(self.lbl_autostart_note)

        self.chk_fullscreen = QCheckBox(tr("chk_fullscreen"))
        self.chk_fullscreen.toggled.connect(lambda b: self.app.set_hide_fullscreen(b))
        v.addWidget(self.chk_fullscreen)

        self.chk_log = QCheckBox(tr("chk_log"))
        self.chk_log.setToolTip(tr("chk_log_tip"))
        self.chk_log.toggled.connect(lambda b: self.app.set_log_enabled(b))
        v.addWidget(self.chk_log)
        return card

    def _build_interface_card(self) -> QFrame:
        card = QFrame()
        card.setObjectName("Card")
        v = QVBoxLayout(card)
        v.setContentsMargins(14, 12, 14, 12)
        v.setSpacing(9)

        self.cap_interface = QLabel(tr("card_interface"))
        self.cap_interface.setObjectName("CardCaption")
        v.addWidget(self.cap_interface)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_theme = QPushButton()
        self.btn_theme.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_theme.clicked.connect(self._on_theme)
        self.btn_lang = QPushButton()
        self.btn_lang.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_lang.clicked.connect(self._on_lang)
        row.addWidget(self.btn_theme)
        row.addWidget(self.btn_lang)
        row.addStretch(1)
        v.addLayout(row)
        return card

    def _apply_theme_ui(self):
        """Тень окна подбирается под текущую тему."""
        try:
            if str(self.app.cfg.get("theme") or "dark") == "light":
                self._shadow.setBlurRadius(30)
                self._shadow.setColor(QColor(0, 0, 0, 70))
            else:
                self._shadow.setBlurRadius(38)
                self._shadow.setColor(QColor(0, 0, 0, 170))
        except Exception:
            pass

    def _on_theme(self):
        cur = str(self.app.cfg.get("theme") or "dark")
        self.app.set_theme("light" if cur != "light" else "dark")

    def _on_lang(self):
        cur = str(self.app.cfg.get("lang") or "ru")
        self.app.set_language("ru" if cur != "ru" else "en")

    def retranslate(self):
        """Переводит все подписи окна на текущий язык (без перезапуска)."""
        self.setWindowTitle(APP_TITLE + " — " + tr("settings_title"))
        self.lbl_sub.setText(tr("app_sub"))
        self.btn_close.setToolTip(tr("close_tip"))
        self.cap_image.setText(tr("card_image"))
        self.cap_presets.setText(tr("cap_presets"))
        self.cap_size.setText(tr("card_size"))
        self.cap_shape.setText(tr("card_shape"))
        self.cap_offset.setText(tr("card_offset"))
        self.cap_behaviour.setText(tr("card_behaviour"))
        self.cap_interface.setText(tr("card_interface"))
        self.btn_pick.setText(tr("btn_pick"))
        self.btn_pick.setToolTip(tr("btn_pick_tip"))
        self.btn_reset.setText(tr("btn_default"))
        self.btn_off_reset.setText(tr("btn_reset"))
        self.btn_calib.setText(tr("btn_calib"))
        self.btn_calib.setToolTip(tr("btn_calib_tip"))
        self.chk_auto.setText(tr("auto"))
        self.cmb_shape.setItemText(0, tr("shape_rounded"))
        self.cmb_shape.setItemText(1, tr("shape_circle"))
        self.cmb_shape.setItemText(2, tr("shape_square"))
        self.chk_enabled.setText(tr("chk_enabled"))
        self.chk_autostart.setText(tr("chk_autostart"))
        self.chk_fullscreen.setText(tr("chk_fullscreen"))
        self.chk_log.setText(tr("chk_log"))
        self.chk_log.setToolTip(tr("chk_log_tip"))
        theme = str(self.app.cfg.get("theme") or "dark")
        self.btn_theme.setText(tr("theme_light") if theme == "light" else tr("theme_dark"))
        lang = str(self.app.cfg.get("lang") or "ru")
        self.btn_lang.setText(tr("lang_en") if lang == "en" else tr("lang_ru"))
        for key, tip in preset_files():
            if key in self.preset_buttons:
                self.preset_buttons[key].setToolTip(tip + tr("preset_tip_suffix"))
        self.sync_from_cfg()
        self.refresh_preview()
        self._update_autostart_note()

    # -- обработчики --------------------------------------------------------------
    def _on_slider(self, v: int):
        if self.chk_auto.isChecked():
            self.chk_auto.blockSignals(True)
            self.chk_auto.setChecked(False)
            self.chk_auto.blockSignals(False)
        self.lbl_size.setText(f"{v} px")
        self.app.set_size(v)

    def _on_auto(self, checked: bool):
        if checked:
            self.slider.blockSignals(True)
            self.slider.setValue(self.app.auto_size())
            self.slider.blockSignals(False)
            self.lbl_size.setText(tr("auto"))
            self.app.set_size(0)
        else:
            v = self.slider.value()
            self.lbl_size.setText(f"{v} px")
            self.app.set_size(v)

    def _on_autostart(self, checked: bool):
        ok, err = self.app.set_autostart(checked)
        if not ok:
            self.chk_autostart.blockSignals(True)
            self.chk_autostart.setChecked(not checked)
            self.chk_autostart.blockSignals(False)
            QMessageBox.warning(self, APP_TITLE, tr("err_autostart") + err)
        self._update_autostart_note()

    def _update_autostart_note(self):
        state = autostart_state()
        if state == "task":
            self.lbl_autostart_note.setText(tr("autostart_note_task"))
        elif state == "shortcut":
            self.lbl_autostart_note.setText(tr("autostart_note_shortcut"))
        else:
            self.lbl_autostart_note.setText(tr("autostart_note_none"))

    # -- синхронизация с конфигом ---------------------------------------------------
    def refresh_preview(self):
        self._rebuild_gallery()
        self.app.update_tray_icon()

    def sync_from_cfg(self):
        cfg = self.app.cfg
        widgets = (self.chk_enabled, self.chk_autostart, self.chk_fullscreen,
                   self.chk_log, self.chk_auto, self.slider, self.cmb_shape)
        for w in widgets:
            w.blockSignals(True)
        self.chk_enabled.setChecked(bool(cfg["enabled"]))
        self.chk_autostart.setChecked(bool(cfg["autostart"]))
        self.chk_fullscreen.setChecked(bool(cfg["hide_fullscreen"]))
        self.chk_log.setChecked(bool(cfg.get("log")))
        auto = int(cfg["size"] or 0) == 0
        self.slider.setValue(self.app.auto_size() if auto else int(cfg["size"]))
        self.chk_auto.setChecked(auto)
        self.lbl_size.setText(tr("auto") if auto else f"{int(cfg['size'])} px")
        idx = self.cmb_shape.findData(str(cfg.get("shape") or "rounded"))
        if idx >= 0:
            self.cmb_shape.setCurrentIndex(idx)
        img = str(cfg.get("image") or "")
        for key, btn in self.preset_buttons.items():
            btn.setChecked(img == preset_ref(key))
        self.lbl_off.setText(offset_text(cfg["offset_x"], cfg["offset_y"]))
        for w in widgets:
            w.blockSignals(False)
        self._update_autostart_note()

    def set_calibration_checked(self, on: bool):
        self.btn_calib.blockSignals(True)
        self.btn_calib.setChecked(on)
        self.btn_calib.blockSignals(False)

    def closeEvent(self, e):
        e.ignore()
        self.hide()


# ---------------------------------------------------------------- автозагрузка
TASK_NAME = "WinIcons"
TASK_NAME_LEGACY = "IconsPo"          # имя задачи из версии 1.0 — удаляем при переключении
TASK_RESULT_FILE = os.path.join(APPDATA_DIR, "last_task_result.txt")


def _ps_quote(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def startup_lnk_path(legacy: bool = False) -> str:
    """Путь ярлыка в «Автозагрузке» (legacy=True — имя из версии 1.0)."""
    startup = os.path.join(os.environ["APPDATA"], "Microsoft", "Windows",
                           "Start Menu", "Programs", "Startup")
    return os.path.join(startup, "IconsPo.lnk" if legacy else "WinIcons.lnk")


def _parent_exe_path() -> str:
    """Путь к процессу-лаунчеру onefile-сборки: Nuitka кладёт его PID в окружение."""
    try:
        pid = int(os.environ.get("NUITKA_ONEFILE_PARENT") or 0)
        if pid <= 0:
            return ""
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        k32 = ctypes.windll.kernel32
        handle = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return ""
        try:
            size = ctypes.c_uint32(4096)
            buf = ctypes.create_unicode_buffer(4096)
            if k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return buf.value
        finally:
            k32.CloseHandle(handle)
    except Exception:
        return ""
    return ""


def app_exe_path() -> str:
    """Путь к настоящему exe. В onefile-сборке sys.executable — это временная распаковка,
    поэтому сначала спрашиваем у процесса-лаунчера, затем пробуем argv[0]."""
    parent = _parent_exe_path()
    if parent and os.path.isfile(parent):
        return parent
    arg0 = (sys.argv[0] if sys.argv else "") or ""
    if arg0:
        cand = os.path.abspath(arg0)
        if os.path.isfile(cand):
            return cand
        containing = getattr(globals().get("__compiled__"), "containing_dir", "") or ""
        if containing:
            cand = os.path.join(containing, os.path.basename(arg0))
            if os.path.isfile(cand):
                return cand
    return os.path.abspath(sys.executable)


def autostart_cmdline() -> tuple[str, str]:
    """(target, аргументы) для ярлыка или задачи Планировщика."""
    if "__compiled__" in globals() or getattr(sys, "frozen", False):
        return app_exe_path(), "--autostart"
    py = sys.executable
    pyw = py[: -len("python.exe")] + "pythonw.exe" if py.lower().endswith("python.exe") else py
    if not os.path.exists(pyw):
        pyw = py
    return pyw, f'"{os.path.abspath(__file__)}" --autostart'


def is_admin() -> bool:
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def _run_hidden(cmd: list) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, creationflags=0x08000000)


def _decode(p: subprocess.CompletedProcess) -> str:
    return (p.stderr or p.stdout or b"").decode("cp866", "replace").strip()[:300]


def _task_exists_named(name: str) -> bool:
    try:
        return _run_hidden(["schtasks", "/Query", "/TN", name]).returncode == 0
    except Exception:
        return False


def task_exists() -> bool:
    """Задача с новым именем или оставшаяся от версии 1.0."""
    return _task_exists_named(TASK_NAME) or _task_exists_named(TASK_NAME_LEGACY)


def task_set(enabled: bool) -> tuple[bool, str]:
    """Задача Планировщика (запуск при входе в систему, без реестра)."""
    try:
        if enabled:
            target, args = autostart_cmdline()
            p = _run_hidden(["schtasks", "/Create", "/F", "/TN", TASK_NAME, "/SC", "ONLOGON",
                             "/TR", f'"{target}" {args}', "/IT"])
            ok = _task_exists_named(TASK_NAME)
            if ok:
                # перенос с 1.0: старая задача IconsPo больше не нужна
                _run_hidden(["schtasks", "/Delete", "/F", "/TN", TASK_NAME_LEGACY])
        else:
            p = _run_hidden(["schtasks", "/Delete", "/F", "/TN", TASK_NAME])
            _run_hidden(["schtasks", "/Delete", "/F", "/TN", TASK_NAME_LEGACY])
            ok = not task_exists()
        if ok:
            return True, ""
        return False, _decode(p) or f"код {p.returncode}"
    except Exception as ex:
        return False, str(ex)


def task_set_elevated(enabled: bool) -> tuple[bool, str]:
    """Создаёт/удаляет задачу через копию программы, запущенную с правами админа (UAC)."""
    flag = "--install-task" if enabled else "--remove-task"
    if "__compiled__" in globals() or getattr(sys, "frozen", False):
        exe, params = app_exe_path(), flag
    else:
        exe, params = sys.executable, f'"{os.path.abspath(__file__)}" {flag}'
    try:
        rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
        if rc <= 32:
            return False, "запрос UAC отклонён"
    except Exception as ex:
        return False, str(ex)
    deadline = time.time() + 30
    while time.time() < deadline:
        if task_exists() == enabled:
            return True, ""
        time.sleep(0.5)
    err = ""
    try:
        with open(TASK_RESULT_FILE, "r", encoding="utf-8") as f:
            err = f.read().strip()
    except Exception:
        pass
    return False, err or "не удалось подтвердить результат"


def install_task_cli(enable: bool) -> int:
    """Режим администратора: --install-task / --remove-task (без GUI)."""
    ok, err = task_set(enable)
    try:
        ensure_appdata()
        with open(TASK_RESULT_FILE, "w", encoding="utf-8") as f:
            f.write("ok" if ok else (err or "ошибка"))
    except Exception:
        pass
    return 0 if ok else 1


def shortcut_set(enabled: bool) -> tuple[bool, str]:
    """Запасной вариант: ярлык в папке «Автозагрузка» (не требует прав админа)."""
    lnk = startup_lnk_path()
    lnk_legacy = startup_lnk_path(legacy=True)
    try:
        if not enabled:
            for path_ in (lnk, lnk_legacy):
                if os.path.exists(path_):
                    os.remove(path_)
            return True, ""
        target, args = autostart_cmdline()
        workdir = (os.path.dirname(os.path.abspath(target)) if args == "--autostart"
                   else os.path.dirname(os.path.abspath(__file__)))
        parts = [
            "$ws = New-Object -ComObject WScript.Shell;",
            f"$s = $ws.CreateShortcut({_ps_quote(lnk)});",
            f"$s.TargetPath = {_ps_quote(target)};",
            f"$s.Arguments = {_ps_quote(args)};",
            f"$s.WorkingDirectory = {_ps_quote(workdir)};",
        ]
        if os.path.exists(ICON_ICO):
            parts.append(f"$s.IconLocation = {_ps_quote(ICON_ICO)};")
        parts.append("$s.Save()")
        p = _run_hidden(["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy",
                         "Bypass", "-Command", " ".join(parts)])
        if p.returncode != 0 or not os.path.exists(lnk):
            msg = (p.stderr or p.stdout or b"").decode("utf-8", "replace").strip()
            return False, msg[:300] or "не удалось создать ярлык"
        # перенос с 1.0: старый ярлык IconsPo.lnk больше не нужен
        try:
            if os.path.exists(lnk_legacy):
                os.remove(lnk_legacy)
        except OSError:
            pass
        return True, ""
    except Exception as ex:
        return False, str(ex)


def shortcut_exists() -> bool:
    try:
        return os.path.exists(startup_lnk_path()) or os.path.exists(startup_lnk_path(legacy=True))
    except Exception:
        return False


def autostart_state() -> str:
    """'task' — задача Планировщика, 'shortcut' — ярлык, 'none' — выключено."""
    if task_exists():
        return "task"
    if shortcut_exists():
        return "shortcut"
    return "none"


class ForegroundWatcher:
    """Мгновенно возвращает оверлей наверх при смене переднего окна.

    Когда пользователь кликает «Пуск» или панель задач, Windows поднимает панель
    над нашим topmost-окном. Это событие приходит сразу — поднимаемся следом.
    """

    EVENT_SYSTEM_FOREGROUND = 0x0003
    WINEVENT_OUTOFCONTEXT = 0x0000

    def __init__(self, on_foreground):
        self.on_foreground = on_foreground   # callable() — поднять значки наверх
        self._hook = None
        self._cb = None

    def start(self) -> None:
        try:
            proto = ctypes.WINFUNCTYPE(None, wintypes.HANDLE, wintypes.DWORD,
                                       wintypes.HWND, wintypes.LONG, wintypes.LONG,
                                       wintypes.DWORD, wintypes.DWORD)
            self._cb = proto(self._on_event)
            user32.SetWinEventHook.restype = wintypes.HANDLE
            user32.SetWinEventHook.argtypes = (wintypes.DWORD, wintypes.DWORD,
                                               wintypes.HMODULE, proto,
                                               wintypes.DWORD, wintypes.DWORD,
                                               wintypes.DWORD)
            self._hook = user32.SetWinEventHook(
                self.EVENT_SYSTEM_FOREGROUND, self.EVENT_SYSTEM_FOREGROUND,
                0, self._cb, 0, 0, self.WINEVENT_OUTOFCONTEXT)
        except Exception:
            self._hook = None

    def stop(self) -> None:
        try:
            if self._hook:
                user32.UnhookWinEvent(wintypes.HANDLE(self._hook))
        except Exception:
            pass
        self._hook = None
        self._cb = None

    def _on_event(self, _hook, _event, _hwnd, _id_obj, _id_child, _thread, _time) -> None:
        try:
            self.on_foreground()
        except Exception:
            pass


class SessionEndFilter(QAbstractNativeEventFilter):
    """Штатно закрывает программу при выключении, перезагрузке или выходе
    из системы Windows.

    Система сначала присылает WM_QUERYENDSESSION (можно ли завершать сеанс),
    а затем WM_ENDSESSION (сеанс действительно завершается). На первом этапе
    сохраняем настройки, на втором — сразу выходим: Qt на это же сообщение
    сам останавливает цикл событий, поэтому отложенная очистка уже не успеет
    выполниться.
    """

    WM_QUERYENDSESSION = 0x0011
    WM_ENDSESSION = 0x0016

    def __init__(self, on_session_end, on_session_ending=None):
        super().__init__()
        self._on_session_end = on_session_end          # callable() — обычный выход
        self._on_session_ending = on_session_ending    # callable() — сохранить состояние
        self._prepared = False
        self._done = False
        self.ended = False

    def nativeEventFilter(self, event_type, message):
        try:
            if bytes(event_type) == b"windows_generic_MSG":
                msg = wintypes.MSG.from_address(int(message))
                if msg.message == self.WM_QUERYENDSESSION:
                    # Сообщение приходит каждому окну — сохраняем один раз.
                    if not self._prepared and self._on_session_ending is not None:
                        self._prepared = True
                        self._on_session_ending()
                elif msg.message == self.WM_ENDSESSION:
                    if not msg.wParam:
                        # Завершение сеанса отменили — следующий раз сохраним снова.
                        self._prepared = False
                    elif not self._done:
                        self._done = True
                        self.ended = True
                        self._on_session_end()
        except Exception:
            pass
        return False, 0


# ---------------------------------------------------------------- приложение
class App(QObject):
    DEF_SIZE_MIN, DEF_SIZE_MAX = 20, 32

    def __init__(self, qt_app: QApplication):
        super().__init__()
        self.qt_app = qt_app
        self.first_run = not os.path.exists(CONFIG_PATH)
        self.cfg = load_cfg()
        set_lang(self.cfg.get("lang") or "ru")
        self.cfg["autostart"] = autostart_state() != "none"
        save_cfg(self.cfg)
        set_logging(bool(self.cfg.get("log")))
        app_log("запуск: %s | image=%s" % (
            app_exe_path(),
            os.path.basename(str(self.cfg.get("image") or "")) or "(стандартный)"))
        if self.cfg.get("image"):          # старый конфиг: картинка попадает в галерею
            self._remember(self.cfg["image"])

        self.locator = StartLocator()
        self.locator.hide_overlays = self._hide_all
        self.hosts: dict[int, OverlayHost] = {}   # панель задач -> значок на ней
        self._screens_connected: set = set()
        self.settings: SettingsWindow | None = None
        self.status = tr("status_init")
        self._last_btn_h = 48
        self._quitting = False

        self.tray = QSystemTrayIcon(self._build_tray_icon(), self)
        self.tray.setToolTip(APP_TITLE + " — " + tr("app_sub"))
        menu = QMenu()
        self.act_set = menu.addAction(tr("tray_settings"))
        self.act_set.triggered.connect(self.open_settings)
        menu.addSeparator()
        self.act_quit = menu.addAction(tr("tray_quit"))
        self.act_quit.triggered.connect(self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray)
        self.tray.show()

        self.timer = QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        QTimer.singleShot(50, self.tick)

        # Сторож z-порядка: панель задач при кликах залезает поверх оверлея —
        # возвращаем значки наверх не позднее чем через 0,3 с.
        self.top_timer = QTimer(self)
        self.top_timer.setInterval(300)
        self.top_timer.timeout.connect(self._raise_all)
        self.top_timer.start()

        # Мгновенная реакция на смену переднего окна (клик по «Пуску», панели задач).
        self._fg_watch = ForegroundWatcher(self._raise_all)
        self._fg_watch.start()

        # Смена экранов (подключение/отключение монитора, смена масштаба) —
        # пересчитываем карту мониторов и заново расставляем значки.
        try:
            qt_app.screenAdded.connect(self._on_screens_changed)
            qt_app.screenRemoved.connect(self._on_screens_changed)
        except Exception:
            pass
        self._connect_screen_signals()

    # -- значки на панелях задач -------------------------------------------------
    def _hide_all(self):
        """Прячет все значки (перед снимком экрана)."""
        for host in self.hosts.values():
            try:
                if host.overlay.isVisible():
                    host.overlay.hide()
            except Exception:
                pass

    def _raise_all(self):
        """Поднимает все значки поверх панели задач."""
        for host in self.hosts.values():
            try:
                if host.overlay.isVisible():
                    host.overlay._raise_now()
            except Exception:
                pass

    def _invalidate_icons(self):
        """Сбрасывает кэш картинки у всех значков (после смены файла/формы)."""
        for host in self.hosts.values():
            host.overlay._key = None

    def _drop_hosts(self, alive: set):
        """Закрывает значки панелей задач, которые исчезли (например, монитор
        отключили или перезапустился проводник)."""
        for hwnd in [h for h in self.hosts if h not in alive]:
            host = self.hosts.pop(hwnd)
            app_log("панель задач %d исчезла — убираю значок" % hwnd)
            host.close()

    def _connect_screen_signals(self):
        """Подписывается на изменение геометрии каждого экрана."""
        app = QApplication.instance()
        if app is None:
            return
        for s in app.screens():
            if s in self._screens_connected:
                continue
            try:
                s.geometryChanged.connect(self._on_screens_changed)
            except Exception:
                pass
            self._screens_connected.add(s)

    def _on_screens_changed(self, *args):
        """Монитор подключили/отключили или изменили масштаб."""
        monitor_map_refresh()
        self.locator.invalidate()
        self._connect_screen_signals()
        self.tick()

    # -- трей ------------------------------------------------------------------
    def _build_tray_icon(self) -> QIcon:
        pm = icon_pixmap(current_source(self.cfg), 64)
        if pm is not None:
            return QIcon(pm)
        if os.path.exists(ICON_ICO):
            return QIcon(ICON_ICO)
        return self.qt_app.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)

    def update_tray_icon(self):
        try:
            self.tray.setIcon(self._build_tray_icon())
        except Exception:
            pass

    def set_theme(self, theme: str):
        """Переключает тёмное/светлое оформление, выбор сохраняется."""
        theme = "light" if str(theme) == "light" else "dark"
        self.cfg["theme"] = theme
        save_cfg(self.cfg)
        app_log("тема: " + theme)
        apply_qss(self.qt_app, theme)
        if self.settings is not None:
            self.settings._apply_theme_ui()
            self.settings.retranslate()

    def set_language(self, lang: str):
        """Переключает язык интерфейса, выбор сохраняется."""
        set_lang(lang)
        self.cfg["lang"] = _LANG
        save_cfg(self.cfg)
        app_log("язык: " + _LANG)
        self.retranslate()
        self.tick()

    def retranslate(self):
        """Обновляет тексты трея и настроек после смены языка."""
        try:
            self.act_set.setText(tr("tray_settings"))
            self.act_quit.setText(tr("tray_quit"))
            self.tray.setToolTip(APP_TITLE + " — " + (self.status or tr("app_sub")))
        except Exception:
            pass
        if self.settings is not None:
            self.settings.retranslate()

    def _on_tray(self, reason):
        if reason in (QSystemTrayIcon.ActivationReason.Trigger,
                      QSystemTrayIcon.ActivationReason.DoubleClick):
            self.open_settings()

    def open_settings(self):
        if self.settings is None:
            self.settings = SettingsWindow(self)
        self.settings.sync_from_cfg()
        self.settings.refresh_preview()
        self.settings.show()
        self.settings.raise_()
        self.settings.activateWindow()
        return self.settings

    # -- действия из интерфейса --------------------------------------------------
    def pick_image(self):
        parent = self.settings
        path, _ = QFileDialog.getOpenFileName(
            parent, tr("dlg_pick_title"), "", tr("dlg_pick_filter"))
        if not path:
            return
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            QMessageBox.warning(parent, APP_TITLE, tr("err_file_not_found"))
            return
        # Картинка не копируется — используется прямо из своей папки
        self.cfg["image"] = path
        self._remember(path)
        app_log("значок из файла: " + path)
        self._invalidate_icons()
        if self.settings is not None:
            self.settings.refresh_preview()
        self.tick()

    def reset_image(self):
        self.cfg["image"] = ""
        self._remember("")
        app_log("значок: стандартный")
        self._invalidate_icons()
        if self.settings is not None:
            self.settings.refresh_preview()
        self.tick()

    def apply_preset(self, key: str):
        """Ставит значок-пресет. В AppData ничего не копируется: в конфиге
        хранится логическая ссылка preset:<ключ>, файл берётся из assets."""
        if not os.path.exists(preset_asset(key)):
            return
        ref = preset_ref(key)
        self.cfg["image"] = ref
        self._remember(ref)
        app_log("пресет: " + key)
        self._invalidate_icons()
        if self.settings is not None:
            self.settings.refresh_preview()
            self.settings.sync_from_cfg()
        self.tick()

    # -- галерея выбранных значков ------------------------------------------------
    def _remember(self, path: str):
        """Двигает картинку в начало галереи и сохраняет конфиг."""
        hist = [p for p in (self.cfg.get("history") or []) if p != path]
        hist.insert(0, path)
        self.cfg["history"] = hist           # без лимита: храним все выбранные значки
        save_cfg(self.cfg)
        keep = set(hist)
        try:
            for f in os.listdir(APPDATA_DIR):
                full = os.path.join(APPDATA_DIR, f)
                if f.startswith("picked_") and full not in keep:
                    try:
                        os.remove(full)
                    except Exception:
                        pass
        except Exception:
            pass

    def set_image_from_history(self, path: str):
        """Переключает значок на картинку, выбранную ранее."""
        if path and not resolve_media(path):
            return
        self.cfg["image"] = path
        self._remember(path)
        app_log("значок из галереи: " + (os.path.basename(path) if path else "стандартный"))
        self._invalidate_icons()
        if self.settings is not None:
            self.settings.refresh_preview()
        self.tick()

    def set_shape(self, shape: str):
        if shape not in ("rounded", "circle", "square"):
            shape = "rounded"
        self.cfg["shape"] = shape
        save_cfg(self.cfg)
        app_log("форма: " + shape)
        self._invalidate_icons()
        if self.settings is not None:
            self.settings.refresh_preview()
        self.tick()

    def auto_size(self) -> int:
        return max(self.DEF_SIZE_MIN, min(self.DEF_SIZE_MAX, round(self._last_btn_h * 0.55)))

    def set_size(self, value: int):
        self.cfg["size"] = int(value)
        save_cfg(self.cfg)
        app_log("размер: %d px" % int(value) if int(value) > 0 else "размер: авто")
        if self.settings is not None:
            self.settings.lbl_off.setText(offset_text(self.cfg['offset_x'], self.cfg['offset_y']))
        self.tick()

    def nudge(self, dx: int, dy: int):
        self.cfg["offset_x"] = int(self.cfg["offset_x"]) + dx
        self.cfg["offset_y"] = int(self.cfg["offset_y"]) + dy
        save_cfg(self.cfg)
        app_log("сдвиг: %d, %d" % (int(self.cfg["offset_x"]), int(self.cfg["offset_y"])))
        if self.settings is not None:
            self.settings.lbl_off.setText(offset_text(self.cfg['offset_x'], self.cfg['offset_y']))
        self.tick()

    def reset_offset(self):
        self.cfg["offset_x"] = 0
        self.cfg["offset_y"] = 0
        save_cfg(self.cfg)
        app_log("сдвиг: 0, 0")
        if self.settings is not None:
            self.settings.lbl_off.setText(offset_text(0, 0))
        self.tick()

    def set_enabled(self, enabled: bool):
        self.cfg["enabled"] = bool(enabled)
        save_cfg(self.cfg)
        app_log("показ значка: " + ("включён" if enabled else "выключен"))
        self.tick()

    def set_hide_fullscreen(self, value: bool):
        self.cfg["hide_fullscreen"] = bool(value)
        save_cfg(self.cfg)
        app_log("скрытие в полноэкранных: " + ("вкл" if value else "выкл"))
        self.tick()

    def set_log_enabled(self, value: bool):
        """Галочка «Вести журнал работы» (по умолчанию выключена)."""
        on = bool(value)
        if on:
            set_logging(True)
            app_log("журнал включён (%s)" % LOG_PATH)
        else:
            app_log("журнал выключен")
            set_logging(False)
        self.cfg["log"] = on
        save_cfg(self.cfg)

    def set_autostart(self, enabled: bool) -> tuple[bool, str]:
        if not enabled:
            shortcut_set(False)
            task_set(False)
            if task_exists() or shortcut_exists():
                return False, "не удалось удалить запись автозагрузки"
            self.cfg["autostart"] = False
            save_cfg(self.cfg)
            app_log("автозагрузка выключена")
            return True, ""
        ok, err = task_set(True)                      # сработает, если есть права админа
        if not ok and not is_admin():
            if self._ask_elevated_task():
                ok, err = task_set_elevated(True)     # UAC: копия программы с правами админа
        if not ok:
            ok, err2 = shortcut_set(True)             # запасной вариант — ярлык
            if not ok:
                return False, err or err2
        self.cfg["autostart"] = True
        save_cfg(self.cfg)
        app_log("автозагрузка включена")
        return True, ""

    def _ask_elevated_task(self) -> bool:
        box = QMessageBox(self.settings)
        box.setWindowTitle(APP_TITLE)
        box.setIcon(QMessageBox.Icon.Question)
        box.setText(tr("ask_autostart_text"))
        box.setInformativeText(tr("ask_autostart_info"))
        b_task = box.addButton(tr("ask_autostart_task_btn"), QMessageBox.ButtonRole.AcceptRole)
        b_lnk = box.addButton(tr("ask_autostart_lnk_btn"), QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(b_task)
        box.exec()
        return box.clickedButton() is b_task

    def set_calibration(self, on: bool):
        if on:
            # Сначала обычная расстановка: значки должны стоять на кнопках,
            # иначе при включении перетаскивания окно прыгнет.
            self.tick()
        for host in self.hosts.values():
            host.overlay.set_calibration(on)
        self.tick()

    def _on_calibrated(self, widget, rect: QRect):
        """Пользователь перетащил значок: сохраняем сдвиг относительно кнопки."""
        host = None
        for h in self.hosts.values():
            if h.overlay is widget:
                host = h
                break
        if host is None or host.base_center is None:
            return
        c = rect.center()
        self.cfg["offset_x"] = int(c.x() - host.base_center.x())
        self.cfg["offset_y"] = int(c.y() - host.base_center.y())
        save_cfg(self.cfg)
        app_log("калибровка: сдвиг %d, %d" % (self.cfg["offset_x"], self.cfg["offset_y"]))
        if self.settings is not None:
            self.settings.lbl_off.setText(offset_text(self.cfg['offset_x'], self.cfg['offset_y']))

    # -- основной цикл -------------------------------------------------------------
    def tick(self):
        """Цикл обновления; ошибка не должна останавливать программу."""
        try:
            self._tick_impl()
        except Exception as ex:
            app_log("ошибка в цикле: %r" % (ex,))

    def _tick_impl(self):
        if not self.cfg["enabled"]:
            self._hide_all()
            self._set_status(tr("status_disabled"))
            return
        if any(h.overlay.calibration for h in self.hosts.values()):
            for host in self.hosts.values():
                host.overlay.show()
            self._raise_all()
            self._set_status(tr("status_calibration"))
            return
        if self.cfg["hide_fullscreen"] and foreground_is_fullscreen():
            self._hide_all()
            self._set_status(tr("status_fullscreen"))
            return
        # Эвристике нужен снимок панели задач — значки не должны в него попасть.
        if self.locator.wants_pixels():
            self._hide_all()
            self.qt_app.processEvents()
        results = self.locator.locate_all()
        self._drop_hosts({r["hwnd"] for r in results})
        if not results:
            self._hide_all()
            self._set_status(tr("status_no_taskbar"))
            return

        # Раскладываем результаты по значкам; для новых/переехавших кнопок
        # запоминаем, что нужно уточнить центр.
        placed = []                             # (host, кнопка в логических, результат)
        to_measure = []                         # (host, кнопка в физ. пикселях)
        for r in results:
            hwnd = r["hwnd"]
            if r["offscreen"]:
                host = self.hosts.get(hwnd)
                if host is not None and host.overlay.isVisible():
                    host.overlay.hide()
                continue
            log = physical_to_logical(r["rect"])
            if log is None:
                continue
            host = self.hosts.get(hwnd)
            if host is None:
                host = OverlayHost(hwnd, Overlay(on_calibrated=self._on_calibrated))
                self.hosts[hwnd] = host
            key = (r["rect"].x(), r["rect"].y(), r["rect"].width(), r["rect"].height())
            if key != host._btn_key:
                host._btn_key = key
                host._icon_center_phys = None
                host._taskbar_bg = None
                to_measure.append((host, r["rect"]))
            placed.append((host, log, r))
        if not placed:
            self._hide_all()
            self._set_status(tr("status_taskbar_hidden"))
            return
        self._last_btn_h = int(placed[0][1].height())

        # Уточняем реальный центр значка и фон панели под ним: один снимок на
        # панель, при этом все значки разом прячем (иначе попадут в снимок).
        if to_measure:
            self._hide_all()
            self.qt_app.processEvents()
            for host, phys in to_measure:
                info = self._measure_icon_center(phys, host.hwnd)
                if info is not None:
                    host._icon_center_phys, host._taskbar_bg = info

        src = current_source(self.cfg)
        size = int(self.cfg["size"] or 0)
        if size <= 0:
            size = self.auto_size()
        shape = str(self.cfg.get("shape") or "rounded")
        ox = int(self.cfg["offset_x"])
        oy = int(self.cfg["offset_y"])
        for host, log, _r in placed:
            center = None
            if host._icon_center_phys is not None:
                center = physical_point_to_logical(host._icon_center_phys)
            if center is None:
                center = log.center()
            host.base_center = center
            geo = QRect(round(center.x() - size / 2 + ox),
                        round(center.y() - size / 2 + oy), size, size)
            host.last_geo = geo
            scr = QApplication.screenAt(geo.center()) or QApplication.primaryScreen()
            dpr = float(scr.devicePixelRatio() if scr else 1.0) or 1.0
            host.overlay.set_icon_source(src, size, host._taskbar_bg, shape, dpr)
            if geo != host._shown_geo:
                host.overlay.setGeometry(geo)
                host._shown_geo = geo
            if not host.overlay.isVisible():
                host.overlay.show()            # showEvent сам поднимет поверх всех
            else:
                host.overlay._raise_now()      # панель задач любит залезать при кликах

        n = len(placed)
        w = placed[0][1].width()
        h = placed[0][1].height()
        if n < len(results):
            self._set_status(tr("status_partial").format(n=n, total=len(results)))
        elif self.locator.source == "uia":
            key = "status_found_multi" if n > 1 else "status_found"
            self._set_status(tr(key).format(n=n, w=w, h=h))
        else:
            key = "status_found_approx_multi" if n > 1 else "status_found_approx"
            self._set_status(tr(key).format(n=n, w=w, h=h))

    def _measure_icon_center(self, phys: QRect, hwnd: int):
        """Уточняет центр значка и цвет панели под кнопкой (кэш до смены геометрии).

        Снимок берётся с того монитора, где находится панель задач; возвращает
        точку в физических пикселях виртуального рабочего стола.
        """
        grab = ScreenGrab.capture(hwnd)
        if grab is None:
            return None
        try:
            info = analyze_button_region(grab.img, grab.to_img(phys))
        except Exception:
            return None
        if info is None:
            return None
        p, bg = info
        return grab.to_phys_point(p), bg

    def _set_status(self, text: str):
        """Состояние показывается в подсказке значка трея (в окне настроек текста нет)."""
        if text == self.status:
            return
        self.status = text
        app_log("состояние: " + text)
        try:
            self.tray.setToolTip(APP_TITLE + " — " + text)
        except Exception:
            pass

    def prepare_session_end(self):
        """WM_QUERYENDSESSION: сохраняем состояние ещё до того, как Windows
        начнёт закрывать приложение. Ошибки здесь не должны мешать завершению
        сеанса, поэтому всё глушим."""
        if self._quitting:
            return
        try:
            app_log("Windows завершает сеанс — сохраняю настройки")
            save_cfg(self.cfg)
        except Exception:
            pass

    def quit(self):
        if self._quitting:              # выход уже начался — не повторяем
            return
        self._quitting = True
        try:
            chain = " <- ".join("%s:%d" % (f.name, f.lineno)
                                for f in traceback.extract_stack()[-4:-1])
            app_log("выход; вызов из " + chain)
        except Exception:
            pass
        app_log("выход")
        # Сначала останавливаем всё, что может сработать во время закрытия
        # окон: таймеры, сторож переднего плана и т.п.
        for timer in (getattr(self, "timer", None),
                      getattr(self, "top_timer", None)):
            try:
                if timer is not None:
                    timer.stop()
            except Exception:
                pass
        try:
            self._fg_watch.stop()
        except Exception:
            pass
        save_cfg(self.cfg)
        try:
            self.tray.hide()
        except Exception:
            pass
        try:
            if self.settings is not None:
                self.settings.hide()
        except Exception:
            pass
        try:
            self._hide_all()
            for host in list(self.hosts.values()):
                host.close()
            self.hosts.clear()
        except Exception:
            pass
        # Закрываем и диалоги/окна настроек, если они открыты: quit() должен
        # выйти из всех вложенных циклов событий, а не ждать пользователя.
        try:
            self.qt_app.closeAllWindows()
        except Exception:
            pass
        self.qt_app.quit()


# ---------------------------------------------------------------- самопроверка
def _grab_region(screen, log_rect: QRect, path: str):
    try:
        img = screen.grabWindow(0).toImage()
        scale = float(screen.devicePixelRatio() or 1.0)
        g = screen.geometry()
        # Картинка может быть в физических или в логических пикселях — коэффициент
        # kx/ky переводит логические координаты экрана в пиксели картинки.
        kx = (g.width() * scale) / max(1, img.width())
        ky = (g.height() * scale) / max(1, img.height())
        crop = QRect(int((log_rect.x() - g.x()) * scale / kx),
                     int((log_rect.y() - g.y()) * scale / ky),
                     int(log_rect.width() * scale / kx),
                     int(log_rect.height() * scale / ky))
        crop = crop.intersected(QRect(0, 0, img.width(), img.height()))
        if crop.width() > 0 and crop.height() > 0:
            img.copy(crop).save(path, "PNG")
    except Exception:
        pass


def _selftest_region(host) -> QRect:
    """Область вокруг значка для контрольного снимка."""
    g = host.last_geo
    pad = 130
    return QRect(g.x() - pad, g.y() - 70, g.width() + 2 * pad, g.height() + 140)


def _selftest_shot(host, out_dir: str, tag: str, index: int):
    """Снимок панели задач вокруг значка этого хоста."""
    if host.last_geo is None:
        return
    scr = QApplication.screenAt(host.last_geo.center()) or QApplication.primaryScreen()
    _grab_region(scr, _selftest_region(host),
                 os.path.join(out_dir, "selftest_%s_%d.png" % (tag, index)))


def run_selftest(qt_app: QApplication, ctrl: App, out_dir: str):
    report = {
        "python": sys.version.split()[0],
        "version": APP_VERSION,
        "windows_build": sys.getwindowsversion().build,
        "uia_available": _HAS_UIA,
        "exe": app_exe_path(),
        "argv0": (sys.argv[0] if sys.argv else ""),
        "onefile_parent": _parent_exe_path(),
        "autostart_cmd": '"%s" %s' % autostart_cmdline(),
    }
    scr = QApplication.primaryScreen()
    report["dpr"] = float(scr.devicePixelRatio() or 1.0)
    report["screen_logical"] = [scr.geometry().x(), scr.geometry().y(),
                                scr.geometry().width(), scr.geometry().height()]
    report["screens"] = []
    for s in QApplication.screens():
        g = s.geometry()
        report["screens"].append({
            "name": s.name(),
            "dpr": float(s.devicePixelRatio() or 1.0),
            "logical": [g.x(), g.y(), g.width(), g.height()],
            "hmon": screen_native_handle(s),
        })
    report["taskbars"] = []
    for hw in enum_taskbars():
        rr = get_window_rect(hw)
        if rr:
            report["taskbars"].append({
                "hwnd": hw,
                "rect": [int(rr.left), int(rr.top),
                         int(rr.right - rr.left), int(rr.bottom - rr.top)],
            })

    ctrl.tick()                     # штатная расстановка всех значков
    report["locator_source"] = ctrl.locator.source
    if ctrl.locator.last_error:
        report["locator_error"] = ctrl.locator.last_error
    report["status"] = ctrl.status
    if not ctrl.hosts:
        report["error"] = "кнопка «Пуск» не найдена"
        QTimer.singleShot(100, lambda: _selftest_report_only(qt_app, ctrl, out_dir, report))
        return

    hosts = [h for h in ctrl.hosts.values() if h.last_geo is not None]
    report["hosts"] = []
    for i, host in enumerate(hosts):
        item = {"index": i, "hwnd": host.hwnd}
        if host.base_center is not None:
            item["center_logical"] = [host.base_center.x(), host.base_center.y()]
        g = host.last_geo
        item["overlay_geometry"] = [g.x(), g.y(), g.width(), g.height()]
        if host._btn_key:
            item["btn_physical"] = list(host._btn_key)
        if host._icon_center_phys is not None:
            item["icon_center_physical"] = [host._icon_center_phys.x(),
                                            host._icon_center_phys.y()]
        try:
            ex = exstyle_flags(int(host.overlay.winId()))
            item["overlay_ws_ex_transparent"] = bool(ex & WS_EX_TRANSPARENT)
            item["overlay_ws_ex_layered"] = bool(ex & WS_EX_LAYERED)
            item["overlay_ws_ex_toolwindow"] = bool(ex & WS_EX_TOOLWINDOW)
        except Exception:
            pass
        report["hosts"].append(item)

    ctrl._hide_all()
    qt_app.processEvents()
    for i, host in enumerate(hosts):
        _selftest_shot(host, out_dir, "before", i)
    ctrl.tick()
    qt_app.processEvents()
    QTimer.singleShot(1000, lambda: _selftest_after(qt_app, ctrl, out_dir, report))


def _selftest_report_only(qt_app: QApplication, ctrl: App, out_dir: str, report: dict):
    try:
        with open(os.path.join(out_dir, "selftest_report.json"), "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)
    except Exception:
        pass
    ctrl.quit()


def _selftest_gallery(ctrl: App, win, count: int = 8) -> list:
    """Имитирует выбор картинок, чтобы наполнить галерею. Возвращает тестовые файлы."""
    files = []
    try:
        ensure_appdata()
        src = os.path.join(PRESETS_DIR, "star_wars.svg")
        if not os.path.exists(src):
            src = DEFAULT_SVG
        for i in range(count):
            dest = os.path.join(tempfile.gettempdir(), f"iconspo_selftest_{i}.svg")
            shutil.copyfile(src, dest)
            files.append(dest)
            ctrl.set_image_from_history(dest)
    except Exception:
        pass
    try:
        win.refresh_preview()
        QApplication.processEvents()
    except Exception:
        pass
    return files


def _gallery_report(ctrl: App, win) -> dict:
    out = {
        "history_len": len(ctrl.cfg.get("history") or []),
        "current": os.path.basename(str(ctrl.cfg.get("image") or "")) or "(стандартный)",
        "tiles": 0,
    }
    try:
        out["tiles"] = sum(1 for i in range(win.gal_layout.count())
                           if win.gal_layout.itemAt(i).widget() is not None)
    except Exception as ex:
        out["error"] = str(ex)
    return out


def _selftest_gallery_restore(ctrl: App, prev_image: str, files: list) -> None:
    """Убирает тестовые картинки и возвращает прежний значок."""
    try:
        ctrl.cfg["history"] = [p for p in (ctrl.cfg.get("history") or [])
                               if p not in set(files)]
        ctrl.cfg["image"] = prev_image
        save_cfg(ctrl.cfg)
        for p in files:
            try:
                os.remove(p)
            except Exception:
                pass
    except Exception:
        pass


def _selftest_after(qt_app: QApplication, ctrl: App, out_dir: str, report: dict):
    for i, host in enumerate([h for h in ctrl.hosts.values() if h.last_geo is not None]):
        _selftest_shot(host, out_dir, "after", i)
    win = ctrl.open_settings()
    ctrl.tick()
    prev_image = str(ctrl.cfg.get("image") or "")
    test_files = _selftest_gallery(ctrl, win)
    report["gallery"] = _gallery_report(ctrl, win)

    def done():
        try:
            win.grab().save(os.path.join(out_dir, "selftest_settings.png"), "PNG")
        except Exception:
            pass
        _selftest_gallery_restore(ctrl, prev_image, test_files)
        try:
            with open(os.path.join(out_dir, "selftest_report.json"), "w", encoding="utf-8") as f:
                json.dump(report, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
        ctrl.quit()

    QTimer.singleShot(800, done)


# ---------------------------------------------------------------- точка входа
_MUTEX_HANDLE = None


def single_instance_acquire() -> bool:
    """True, если это первый запуск. Держим мьютекс до конца процесса."""
    global _MUTEX_HANDLE
    try:
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        ctypes.set_last_error(0)
        _MUTEX_HANDLE = kernel32.CreateMutexW(None, False, "Local\\IconsPo_SingleInstance")
        if not _MUTEX_HANDLE:
            return True
        return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS
    except Exception:
        return True


def main() -> int:
    args = set(sys.argv[1:])
    if "--install-task" in args or "--remove-task" in args:
        # запуск копии программы с правами администратора (для Планировщика задач)
        return install_task_cli("--install-task" in args)
    selftest = "--selftest" in args
    autostart_run = "--autostart" in args

    init_lang_from_config()
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

    qt_app = QApplication(sys.argv)
    qt_app.setQuitOnLastWindowClosed(False)
    qt_app.setApplicationName(APP_NAME)
    qt_app.setStyle("Fusion")
    qt_app.setFont(QFont("Segoe UI", 9))
    if os.path.exists(ICON_ICO):
        qt_app.setWindowIcon(QIcon(ICON_ICO))

    if not selftest and not single_instance_acquire():
        if not autostart_run:
            QMessageBox.information(None, APP_TITLE, tr("already_running"))
        return 0

    ctrl = App(qt_app)
    # Выключение/перезагрузка Windows: на WM_QUERYENDSESSION сохраняем
    # настройки, на WM_ENDSESSION штатно закрываемся, чтобы не задерживать
    # завершение сеанса.
    session_filter = SessionEndFilter(ctrl.quit, ctrl.prepare_session_end)
    qt_app.installNativeEventFilter(session_filter)
    apply_qss(qt_app, ctrl.cfg.get("theme", "dark"))
    if selftest:
        out_dir = os.getcwd()
        QTimer.singleShot(150, lambda: run_selftest(qt_app, ctrl, out_dir))
    elif ctrl.first_run and not autostart_run:
        # Первый запуск — показываем настройки, дальше программа живёт в трее
        QTimer.singleShot(300, ctrl.open_settings)
    rc = qt_app.exec()
    if session_filter.ended:
        # Сеанс Windows уже завершается. Не запускаем финализацию Python/Qt:
        # в этот момент конструкторы/деструкторы PySide6 могут вывести
        # traceback или показать системный диалог об ошибке. Настройки уже
        # сохранены, а завершить процесс можно сразу.
        os._exit(0)
    return rc


if __name__ == "__main__":
    sys.exit(main())
