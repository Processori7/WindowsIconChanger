# -*- coding: utf-8 -*-
"""
IconsPo — накладывает выбранную картинку поверх кнопки «Пуск» в Windows.

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
import time
import traceback
from ctypes import wintypes

from PySide6.QtCore import Qt, QTimer, QRect, QRectF, QPoint, QSize, QObject, QEvent
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
APP_TITLE = "IconsPo"
APP_SUB = "Значок «Пуск» поверх панели задач"

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


def preset_files() -> list:
    """[(ключ, название)] — все SVG-пресеты из assets/icons."""
    out = []
    try:
        for name in sorted(os.listdir(PRESETS_DIR)):
            if name.lower().endswith(".svg"):
                key = os.path.splitext(name)[0]
                out.append((key, PRESET_TITLES.get(key, key.replace("_", " ").title())))
    except Exception:
        pass
    return out

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
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            for k in DEFAULT_CFG:
                if k in data:
                    cfg[k] = data[k]
    except Exception:
        pass
    if cfg["image"] and not os.path.exists(cfg["image"]):
        cfg["image"] = ""
    hist = cfg.get("history")
    if not isinstance(hist, list):
        hist = []
    cfg["history"] = [p for p in hist
                      if isinstance(p, str) and (not p or os.path.exists(p))]
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


def current_source(cfg: dict) -> str:
    """Текущий источник картинки: пользовательская или стандартный SVG."""
    p = cfg.get("image") or ""
    if p and os.path.exists(p):
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
    if not h or h == get_taskbar_hwnd():
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
    """Ищет кнопку «Пуск» (UIAutomation), есть эвристический запасной вариант."""

    def __init__(self):
        self._btn = None
        self.source = "none"   # uia | heuristic | none
        self.last_error = ""
        self._uia_fails = 0

    # -- публичный интерфейс ---------------------------------------------------
    def locate(self):
        """Возвращает (QRect в физических пикселях, offscreen: bool) или None."""
        if _HAS_UIA and self._uia_fails < 3:
            try:
                if self._btn is None:
                    self._btn = self._find_via_uia()
                if self._btn is not None:
                    r = self._btn.BoundingRectangle
                    w = int(r.right - r.left)
                    h = int(r.bottom - r.top)
                    if w > 8 and h > 8:
                        self.source = "uia"
                        return QRect(int(r.left), int(r.top), w, h), bool(self._btn.IsOffscreen)
            except Exception as ex:
                # UIAutomation недоступна (например, в сборке нет comtypes) —
                # после нескольких попыток переходим на эвристику, без спама
                self._btn = None
                self._uia_fails += 1
                self.last_error = f"{type(ex).__name__}: {ex}"[:200]
        return self._fallback()

    # -- UIAutomation ----------------------------------------------------------
    def _find_via_uia(self):
        try:
            auto.SetGlobalSearchTimeout(1.0)
        except Exception:
            pass
        hwnd = get_taskbar_hwnd()
        if not hwnd:
            return None
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
    def _fallback(self):
        hwnd = get_taskbar_hwnd()
        if not hwnd:
            return None
        r = get_window_rect(hwnd)
        if not r:
            return None
        tb = QRect(int(r.left), int(r.top), int(r.right - r.left), int(r.bottom - r.top))
        if tb.width() < 20 or tb.height() < 20:
            return None
        self.source = "heuristic"
        if tb.height() >= tb.width():  # панель задач сбоку
            h = tb.width()
            return QRect(tb.left(), tb.top(), h, int(h * 1.15)), False
        h = tb.height()
        # ищем первый значок слева по пикселям — обычно это и есть «Пуск»
        scr = QApplication.primaryScreen()
        if scr is not None:
            try:
                img = scr.grabWindow(0).toImage()
                found = find_first_taskbar_icon(img, tb)
            except Exception:
                found = None
            if found is not None:
                p, _bg = found
                return QRect(p.x() - h // 2, tb.top(), h, h), False
        # последний резерв: системная кнопка «Пуск»
        win11 = sys.getwindowsversion().build >= 22000
        if win11:
            sw = int(user32.GetSystemMetrics(0))   # ширина основного экрана, физ. пиксели
            cx = max(tb.left() + h, min(tb.right() - h, sw // 2 - 96))
            return QRect(int(cx - h / 2), tb.top(), h, h), False
        return QRect(tb.left(), tb.top(), int(h * 1.15), h), False


def physical_to_logical(rect: QRect) -> QRect | None:
    """Перевод физических координат (UIA/WinAPI) в логические координаты Qt."""
    scr = QApplication.primaryScreen()
    if scr is None:
        return None
    dpr = float(scr.devicePixelRatio() or 1.0)
    if abs(dpr - 1.0) < 1e-6:
        return rect
    return QRect(round(rect.x() / dpr), round(rect.y() / dpr),
                 round(rect.width() / dpr), round(rect.height() / dpr))


def physical_point_to_logical(pt: QPoint) -> QPoint | None:
    scr = QApplication.primaryScreen()
    if scr is None:
        return None
    dpr = float(scr.devicePixelRatio() or 1.0)
    if abs(dpr - 1.0) < 1e-6:
        return pt
    return QPoint(round(pt.x() / dpr), round(pt.y() / dpr))


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


def find_first_taskbar_icon(img: QImage, tb: QRect):
    """Эвристика без UIA: первый значок слева на панели задач (обычно это «Пуск»).

    Возвращает (QPoint — центр в физических пикселях, (r, g, b) — цвет панели)
    или None. На панели с центрированными значками первый значок группы — тоже
    кнопка «Пуск».
    """
    if img is None or img.isNull():
        return None
    r = tb.intersected(QRect(0, 0, img.width(), img.height()))
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

    inset_x = max(3, r.height() // 12)
    inset_y = max(4, r.height() // 8)
    step = 2
    need = max(3, ((r.bottom() - inset_y) - (r.top() + inset_y)) // (8 * step))
    max_w = max(24, int(r.height() * 1.5))     # ширина значка ~ высота панели
    run_start = run_end = None
    gap = 0
    for x in range(r.left() + inset_x, r.right() - inset_x, step):
        cnt = 0
        for y in range(r.top() + inset_y, r.bottom() - inset_y, step):
            c = img.pixelColor(x, y)
            d = abs(c.red() - bg[0]) + abs(c.green() - bg[1]) + abs(c.blue() - bg[2])
            if d > 96:
                cnt += 1
                if cnt >= need:
                    break
        if cnt >= need:
            if run_start is None:
                run_start = x
            run_end = x
            gap = 0
        elif run_start is not None:
            gap += step
            if gap > 8:
                break
        if run_start is not None and (x - run_start) > max_w:
            break
    if run_start is None or run_end is None:
        return None
    cx = (run_start + run_end) // 2
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
        self.on_calibrated = on_calibrated   # callback(QRect) — новый прямоугольник

    # -- картинка --------------------------------------------------------------
    def set_icon_source(self, path: str, logical_size: int, bg=None, shape: str = "rounded") -> None:
        dpr = float(self.devicePixelRatioF() or 1.0)
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
                self.on_calibrated(self.frameGeometry())
            e.accept()


# ------------------------------------------------------------------- оформление
QSS = """
* { font-family: "Segoe UI", "Segoe UI Variable Text", sans-serif; }
QLabel { color: #E8EBF1; }
QLabel#Title { font-size: 15px; font-weight: 600; color: #F4F6FB; }
QLabel#Subtitle { font-size: 11px; color: #8A92A6; }
QLabel#CardCaption { font-size: 11px; color: #7C8497; }
QLabel#Value { font-size: 11px; color: #B9C1D4; }
QWidget#TitleBar { background: transparent; }
QFrame#Root {
    background: #14151A;
    border: 1px solid #272A33;
    border-radius: 14px;
}
QFrame#Card {
    background: #191B21;
    border: 1px solid #242732;
    border-radius: 12px;
}
QFrame#Preview {
    background: #0D0F13;
    border: 1px solid #23262F;
    border-radius: 12px;
}
QPushButton#Gallery {
    background: #121419;
    border: 1px solid #262A34;
    border-radius: 11px;
    padding: 4px;
}
QPushButton#Gallery:hover { background: #1A1E27; border-color: #363B48; }
QPushButton#Gallery:checked { border: 1px solid #4C7DFF; background: #202A44; }
QScrollArea#Gallery, QScrollArea#Gallery > QWidget > QWidget { background: transparent; border: none; }
QScrollBar:horizontal { background: transparent; height: 6px; margin: 0 1px; }
QScrollBar::handle:horizontal { background: #2E3340; border-radius: 3px; min-width: 28px; }
QScrollBar::handle:horizontal:hover { background: #3A4050; }
QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal { width: 0; height: 0; }
QScrollBar::add-page:horizontal, QScrollBar::sub-page:horizontal { background: transparent; }
QPushButton {
    background: #1E2129;
    border: 1px solid #2C303B;
    color: #E8EBF1;
    border-radius: 9px;
    padding: 8px 14px;
    font-size: 12px;
}
QPushButton:hover { background: #252932; border-color: #363B48; }
QPushButton:pressed { background: #1A1D24; }
QPushButton:disabled { color: #5C6373; }
QPushButton#Accent {
    background: #4C7DFF; border: 1px solid #4C7DFF; color: #FFFFFF; font-weight: 600;
}
QPushButton#Accent:hover { background: #5D8BFF; border-color: #5D8BFF; }
QPushButton#Accent:pressed { background: #3F6EE6; }
QPushButton#WinBtn {
    background: transparent; border: none; color: #9AA3B5;
    border-radius: 7px; font-size: 13px; padding: 2px 10px;
}
QPushButton#WinBtn:hover { background: #23262E; color: #FFFFFF; }
QPushButton#WinBtnClose:hover { background: #E5484D; color: #FFFFFF; }
QPushButton#Nudge {
    background: #1E2129; border: 1px solid #2C303B; border-radius: 8px;
    padding: 0px; font-size: 13px; color: #C6CDDB; min-width: 34px; min-height: 26px;
}
QPushButton#Nudge:hover { background: #272B35; }
QPushButton#Nudge:checked { background: #2A3350; border-color: #4C7DFF; color: #AFC6FF; }
QPushButton#Small { padding: 5px 10px; font-size: 11px; }
QPushButton#Preset { padding: 5px; border-radius: 10px; }
QPushButton#Preset:checked { border-color: #4C7DFF; background: #232C44; }
QComboBox {
    background: #1E2129; border: 1px solid #2C303B; border-radius: 8px;
    padding: 5px 10px; color: #E8EBF1; font-size: 12px; min-width: 150px;
}
QComboBox:hover { border-color: #363B48; }
QComboBox QAbstractItemView {
    background: #17191F; border: 1px solid #2A2E38; color: #E8EBF1;
    selection-background-color: #262B36; outline: 0;
}
QSlider::groove:horizontal { height: 4px; background: #30353F; border-radius: 2px; }
QSlider::sub-page:horizontal { background: #4C7DFF; border-radius: 2px; }
QSlider::handle:horizontal {
    width: 16px; height: 16px; margin: -7px 0; border-radius: 8px;
    background: #E8EBF1; border: 2px solid #4C7DFF;
}
QSlider::handle:horizontal:hover { background: #FFFFFF; }
QSlider::sub-page:horizontal:disabled { background: #3A3F4B; }
QSlider::handle:horizontal:disabled { background: #6A7180; border-color: #454B59; }
QCheckBox { color: #C9CFDB; font-size: 12px; spacing: 9px; }
QCheckBox::indicator {
    width: 18px; height: 18px; border-radius: 6px;
    background: #1A1D23; border: 1px solid #333845;
}
QCheckBox::indicator:hover { border-color: #4C7DFF; }
QCheckBox::indicator:checked { background: #4C7DFF; border-color: #4C7DFF; }
QMenu {
    background: #17191F; border: 1px solid #2A2E38; border-radius: 8px;
    padding: 6px; color: #E8EBF1; font-size: 12px;
}
QMenu::item { padding: 7px 26px 7px 12px; border-radius: 6px; }
QMenu::item:selected { background: #262B36; }
QMenu::separator { height: 1px; background: #262B36; margin: 6px 8px; }
QMessageBox { background: #14151A; }
QMessageBox QLabel { color: #E8EBF1; }
QToolTip {
    background: #17191F; color: #E8EBF1; border: 1px solid #2A2E38; padding: 4px 8px;
}
"""


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
        self.setWindowTitle(APP_TITLE + " — Настройки")
        self.setFixedWidth(470)
        if os.path.exists(ICON_ICO):
            self.setWindowIcon(QIcon(ICON_ICO))

        root = QFrame(self)
        root.setObjectName("Root")
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(38)
        shadow.setOffset(0, 8)
        shadow.setColor(QColor(0, 0, 0, 170))
        root.setGraphicsEffect(shadow)

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
        body.addStretch(1)

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
        s = QLabel(APP_SUB)
        s.setObjectName("Subtitle")
        titles.addWidget(t)
        titles.addWidget(s)
        h.addLayout(titles)
        h.addStretch(1)

        btn_close = QPushButton("✕")
        btn_close.setObjectName("WinBtn")
        btn_close.setProperty("class", "close")
        btn_close.setFixedSize(30, 26)
        btn_close.setToolTip("Свернуть в трей")
        btn_close.clicked.connect(self.hide)
        h.addWidget(btn_close)
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
            if p and not os.path.exists(p):
                continue
            b = QPushButton()
            b.setObjectName("Gallery")
            b.setCheckable(True)
            b.setFixedSize(46, 46)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setChecked(p == current)
            if not p:
                tip = "Стандартный значок"
            else:
                base = os.path.splitext(os.path.basename(p))[0]
                if base.startswith("preset_"):
                    tip = PRESET_TITLES.get(base[7:], base[7:].replace("_", " ").title())
                else:
                    tip = os.path.basename(p)
            b.setToolTip(tip)
            pm = icon_pixmap(p or DEFAULT_SVG, 34,
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

        cap = QLabel("Изображение значка")
        cap.setObjectName("CardCaption")
        v.addWidget(cap)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_pick = QPushButton("Выбрать изображение…")
        self.btn_pick.setObjectName("Accent")
        self.btn_pick.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_pick.setToolTip("Любая картинка или фото — они автоматически подгонятся под значок")
        self.btn_pick.clicked.connect(lambda: self.app.pick_image())
        self.btn_reset = QPushButton("Стандартный значок")
        self.btn_reset.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_reset.clicked.connect(lambda: self.app.reset_image())
        row.addWidget(self.btn_pick, 1)
        row.addWidget(self.btn_reset)
        v.addLayout(row)

        prow = QHBoxLayout()
        prow.setSpacing(8)
        cap2 = QLabel("Пресеты:")
        cap2.setObjectName("CardCaption")
        prow.addWidget(cap2)
        self.preset_buttons = {}
        for key, tip in preset_files():
            b = QPushButton()
            b.setObjectName("Preset")
            b.setCheckable(True)
            b.setFixedSize(44, 44)
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(tip + " — поставить этот значок")
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
        cap = QLabel("Размер значка")
        cap.setObjectName("CardCaption")
        row.addWidget(cap)
        row.addStretch(1)
        self.lbl_size = QLabel("Авто")
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
        self.chk_auto = QCheckBox("Авто")
        self.chk_auto.toggled.connect(self._on_auto)
        row2.addWidget(self.chk_auto)
        v.addLayout(row2)

        row3 = QHBoxLayout()
        row3.setSpacing(8)
        cap3 = QLabel("Форма (для фото и картинок)")
        cap3.setObjectName("CardCaption")
        row3.addWidget(cap3)
        row3.addStretch(1)
        self.cmb_shape = QComboBox()
        self.cmb_shape.addItem("Скруглённый квадрат", "rounded")
        self.cmb_shape.addItem("Круг", "circle")
        self.cmb_shape.addItem("Квадрат", "square")
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
        cap = QLabel("Положение")
        cap.setObjectName("CardCaption")
        left.addWidget(cap)
        self.lbl_off = QLabel("Сдвиг: 0, 0")
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
        self.btn_off_reset = QPushButton("Сброс")
        self.btn_off_reset.setObjectName("Small")
        self.btn_off_reset.clicked.connect(lambda: self.app.reset_offset())
        self.btn_calib = QPushButton("Калибровка")
        self.btn_calib.setObjectName("Small")
        self.btn_calib.setCheckable(True)
        self.btn_calib.setToolTip("Перетащите значок мышью на нужное место")
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

        cap = QLabel("Поведение")
        cap.setObjectName("CardCaption")
        v.addWidget(cap)

        self.chk_enabled = QCheckBox("Показывать значок поверх кнопки «Пуск»")
        self.chk_enabled.toggled.connect(lambda b: self.app.set_enabled(b))
        v.addWidget(self.chk_enabled)

        self.chk_autostart = QCheckBox("Запускать при входе в Windows")
        self.chk_autostart.toggled.connect(self._on_autostart)
        v.addWidget(self.chk_autostart)
        self.lbl_autostart_note = QLabel("Без реестра: задача Планировщика или ярлык «Автозагрузки»")
        self.lbl_autostart_note.setObjectName("Hint")
        v.addWidget(self.lbl_autostart_note)

        self.chk_fullscreen = QCheckBox("Скрывать в полноэкранных приложениях")
        self.chk_fullscreen.toggled.connect(lambda b: self.app.set_hide_fullscreen(b))
        v.addWidget(self.chk_fullscreen)

        self.chk_log = QCheckBox("Вести журнал работы (для диагностики)")
        self.chk_log.setToolTip("Пишет события программы в %APPDATA%\\IconsPo\\iconspo.log")
        self.chk_log.toggled.connect(lambda b: self.app.set_log_enabled(b))
        v.addWidget(self.chk_log)
        return card

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
            self.lbl_size.setText("Авто")
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
            QMessageBox.warning(self, APP_TITLE, "Не удалось изменить автозагрузку.\n" + err)
        self._update_autostart_note()

    def _update_autostart_note(self):
        state = autostart_state()
        if state == "task":
            self.lbl_autostart_note.setText(
                "Способ: задача в Планировщике (надёжнее). Реестр не изменялся.")
        elif state == "shortcut":
            self.lbl_autostart_note.setText(
                "Способ: ярлык в папке «Автозагрузка». Реестр не изменялся.")
        else:
            self.lbl_autostart_note.setText(
                "Без реестра: задача Планировщика или ярлык «Автозагрузки»")

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
        self.lbl_size.setText("Авто" if auto else f"{int(cfg['size'])} px")
        idx = self.cmb_shape.findData(str(cfg.get("shape") or "rounded"))
        if idx >= 0:
            self.cmb_shape.setCurrentIndex(idx)
        img = str(cfg.get("image") or "")
        for key, btn in self.preset_buttons.items():
            btn.setChecked(img == self.app.preset_path(key))
        self.lbl_off.setText(f"Сдвиг: {int(cfg['offset_x'])}, {int(cfg['offset_y'])}")
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
TASK_NAME = "IconsPo"
TASK_RESULT_FILE = os.path.join(APPDATA_DIR, "last_task_result.txt")


def _ps_quote(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def startup_lnk_path() -> str:
    startup = os.path.join(os.environ["APPDATA"], "Microsoft", "Windows",
                           "Start Menu", "Programs", "Startup")
    return os.path.join(startup, "IconsPo.lnk")


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


def task_exists() -> bool:
    try:
        return _run_hidden(["schtasks", "/Query", "/TN", TASK_NAME]).returncode == 0
    except Exception:
        return False


def task_set(enabled: bool) -> tuple[bool, str]:
    """Задача Планировщика (запуск при входе в систему, без реестра)."""
    try:
        if enabled:
            target, args = autostart_cmdline()
            p = _run_hidden(["schtasks", "/Create", "/F", "/TN", TASK_NAME, "/SC", "ONLOGON",
                             "/TR", f'"{target}" {args}', "/IT"])
        else:
            p = _run_hidden(["schtasks", "/Delete", "/F", "/TN", TASK_NAME])
        if task_exists() == enabled:
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
    try:
        if not enabled:
            if os.path.exists(lnk):
                os.remove(lnk)
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
        return True, ""
    except Exception as ex:
        return False, str(ex)


def shortcut_exists() -> bool:
    try:
        return os.path.exists(startup_lnk_path())
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

    def __init__(self, overlay: "Overlay"):
        self.overlay = overlay
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
            self.overlay._raise_now()
        except Exception:
            pass


# ---------------------------------------------------------------- приложение
class App(QObject):
    DEF_SIZE_MIN, DEF_SIZE_MAX = 20, 32

    def __init__(self, qt_app: QApplication):
        super().__init__()
        self.qt_app = qt_app
        self.first_run = not os.path.exists(CONFIG_PATH)
        self.cfg = load_cfg()
        self.cfg["autostart"] = autostart_state() != "none"
        save_cfg(self.cfg)
        set_logging(bool(self.cfg.get("log")))
        app_log("запуск: %s | image=%s" % (
            app_exe_path(),
            os.path.basename(str(self.cfg.get("image") or "")) or "(стандартный)"))
        if self.cfg.get("image"):          # старый конфиг: картинка попадает в галерею
            self._remember(self.cfg["image"])

        self.overlay = Overlay(on_calibrated=self._on_calibrated)
        self.locator = StartLocator()
        self.settings: SettingsWindow | None = None
        self.status = "Инициализация…"
        self.base_center: QPoint | None = None
        self.last_geo: QRect | None = None
        self._btn_key = None
        self._icon_center_phys: QPoint | None = None
        self._taskbar_bg = None
        self._last_btn_h = 48
        self._shown_geo: QRect | None = None   # чтобы зря не дёргать setGeometry

        self.tray = QSystemTrayIcon(self._build_tray_icon(), self)
        self.tray.setToolTip(APP_TITLE + " — " + APP_SUB)
        menu = QMenu()
        act_set = menu.addAction("Настройки…")
        act_set.triggered.connect(self.open_settings)
        menu.addSeparator()
        act_quit = menu.addAction("Выход")
        act_quit.triggered.connect(self.quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._on_tray)
        self.tray.show()

        self.timer = QTimer(self)
        self.timer.setInterval(1500)
        self.timer.timeout.connect(self.tick)
        self.timer.start()
        QTimer.singleShot(50, self.tick)

        # Сторож z-порядка: панель задач при кликах залезает поверх оверлея —
        # возвращаем значок наверх не позднее чем через 0,3 с.
        self.top_timer = QTimer(self)
        self.top_timer.setInterval(300)
        self.top_timer.timeout.connect(self.overlay._raise_now)
        self.top_timer.start()

        # Мгновенная реакция на смену переднего окна (клик по «Пуску», панели задач).
        self._fg_watch = ForegroundWatcher(self.overlay)
        self._fg_watch.start()

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
            parent, "Выберите изображение для значка", "",
            "Изображения (*.png *.jpg *.jpeg *.webp *.bmp *.gif *.svg);;Все файлы (*.*)")
        if not path:
            return
        path = os.path.abspath(path)
        if not os.path.isfile(path):
            QMessageBox.warning(parent, APP_TITLE, "Файл не найден.")
            return
        # Картинка не копируется — используется прямо из своей папки
        self.cfg["image"] = path
        self._remember(path)
        app_log("значок из файла: " + path)
        self.overlay._key = None
        if self.settings is not None:
            self.settings.refresh_preview()
        self.tick()

    def reset_image(self):
        self.cfg["image"] = ""
        self._remember("")
        app_log("значок: стандартный")
        self.overlay._key = None
        if self.settings is not None:
            self.settings.refresh_preview()
        self.tick()

    def preset_path(self, key: str) -> str:
        return os.path.join(APPDATA_DIR, f"preset_{key}.svg")

    def apply_preset(self, key: str):
        src = os.path.join(PRESETS_DIR, key + ".svg")
        if not os.path.exists(src):
            return
        ensure_appdata()
        dest = self.preset_path(key)
        try:
            shutil.copyfile(src, dest)
        except Exception as ex:
            QMessageBox.warning(self.settings, APP_TITLE, f"Не удалось скопировать значок:\n{ex}")
            return
        self.cfg["image"] = dest
        self._remember(dest)
        app_log("пресет: " + key)
        self.overlay._key = None
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
        if path and not os.path.exists(path):
            return
        self.cfg["image"] = path
        self._remember(path)
        app_log("значок из галереи: " + (os.path.basename(path) if path else "стандартный"))
        self.overlay._key = None
        if self.settings is not None:
            self.settings.refresh_preview()
        self.tick()

    def set_shape(self, shape: str):
        if shape not in ("rounded", "circle", "square"):
            shape = "rounded"
        self.cfg["shape"] = shape
        save_cfg(self.cfg)
        app_log("форма: " + shape)
        self.overlay._key = None
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
            self.settings.lbl_off.setText(f"Сдвиг: {self.cfg['offset_x']}, {self.cfg['offset_y']}")
        self.tick()

    def nudge(self, dx: int, dy: int):
        self.cfg["offset_x"] = int(self.cfg["offset_x"]) + dx
        self.cfg["offset_y"] = int(self.cfg["offset_y"]) + dy
        save_cfg(self.cfg)
        app_log("сдвиг: %d, %d" % (int(self.cfg["offset_x"]), int(self.cfg["offset_y"])))
        if self.settings is not None:
            self.settings.lbl_off.setText(f"Сдвиг: {self.cfg['offset_x']}, {self.cfg['offset_y']}")
        self.tick()

    def reset_offset(self):
        self.cfg["offset_x"] = 0
        self.cfg["offset_y"] = 0
        save_cfg(self.cfg)
        app_log("сдвиг: 0, 0")
        if self.settings is not None:
            self.settings.lbl_off.setText("Сдвиг: 0, 0")
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
        box.setText("Как включить автозагрузку?")
        box.setInformativeText(
            "Надёжный способ — задача в Планировщике Windows: она переживает «чистки»\n"
            "автозагрузки и стартует даже если ярлыки отключены.\n\n"
            "Для создания задачи нужны права администратора — сейчас появится запрос UAC.\n\n"
            "Можно и без прав: тогда будет использован ярлык в папке «Автозагрузка».")
        b_task = box.addButton("Через Планировщик (надёжнее)", QMessageBox.ButtonRole.AcceptRole)
        b_lnk = box.addButton("Обычный ярлык", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(b_task)
        box.exec()
        return box.clickedButton() is b_task

    def set_calibration(self, on: bool):
        if on and self.base_center is None:
            self.base_center = self.overlay.geometry().center()
        self.overlay.set_calibration(on)
        self.tick()

    def _on_calibrated(self, rect: QRect):
        if self.base_center is None:
            return
        c = rect.center()
        self.cfg["offset_x"] = int(c.x() - self.base_center.x())
        self.cfg["offset_y"] = int(c.y() - self.base_center.y())
        save_cfg(self.cfg)
        app_log("калибровка: сдвиг %d, %d" % (self.cfg["offset_x"], self.cfg["offset_y"]))
        if self.settings is not None:
            self.settings.lbl_off.setText(f"Сдвиг: {self.cfg['offset_x']}, {self.cfg['offset_y']}")

    # -- основной цикл -------------------------------------------------------------
    def tick(self):
        """Цикл обновления; ошибка не должна останавливать программу."""
        try:
            self._tick_impl()
        except Exception as ex:
            app_log("ошибка в цикле: %r" % (ex,))

    def _tick_impl(self):
        if not self.cfg["enabled"]:
            self.overlay.hide()
            self._set_status("Значок выключен в настройках")
            return
        if self.overlay.calibration:
            self.overlay.show()
            self.overlay._interactive_topmost()
            self._set_status("Калибровка: перетащите значок мышью на нужное место")
            return
        if self.cfg["hide_fullscreen"] and foreground_is_fullscreen():
            self.overlay.hide()
            self._set_status("Скрыто: активно полноэкранное приложение")
            return
        res = self.locator.locate()
        if res is None:
            self.overlay.hide()
            self._set_status("Панель задач не найдена")
            return
        phys, offscreen = res
        if offscreen:
            self.overlay.hide()
            self._set_status("Панель задач скрыта (автоскрытие)")
            return
        log = physical_to_logical(phys)
        if log is None:
            self.overlay.hide()
            return
        self._last_btn_h = int(log.height())
        # Уточняем реальный центр значка и фон панели (кэш до смены геометрии)
        key = (phys.x(), phys.y(), phys.width(), phys.height())
        if key != self._btn_key:
            self._btn_key = key
            info = self._measure_icon_center(phys)
            self._icon_center_phys = info[0] if info else None
            self._taskbar_bg = info[1] if info else None
        center_phys = self._icon_center_phys or phys.center()
        center = physical_point_to_logical(center_phys) or log.center()

        src = current_source(self.cfg)
        size = int(self.cfg["size"] or 0)
        if size <= 0:
            size = self.auto_size()
        self.base_center = center
        geo = QRect(round(self.base_center.x() - size / 2 + self.cfg["offset_x"]),
                    round(self.base_center.y() - size / 2 + self.cfg["offset_y"]),
                    size, size)
        self.last_geo = geo
        self.overlay.set_icon_source(src, size, self._taskbar_bg,
                                     str(self.cfg.get("shape") or "rounded"))
        if geo != self._shown_geo:
            self.overlay.setGeometry(geo)
            self._shown_geo = geo
        if not self.overlay.isVisible():
            self.overlay.show()                    # showEvent сам поднимет поверх всех
        else:
            self.overlay._raise_now()              # панель задач любит залезать при кликах
        src_txt = "автоматически" if self.locator.source == "uia" else "приблизительно (нужна калибровка)"
        self._set_status(f"Кнопка «Пуск» найдена {src_txt} · {log.width()}×{log.height()} px")

    def _measure_icon_center(self, phys: QRect):
        """Уточняет центр значка и цвет панели под кнопкой (кэш до смены геометрии)."""
        scr = QApplication.primaryScreen()
        if scr is None:
            return None
        try:
            if self.overlay.isVisible():
                self.overlay.hide()
                self.qt_app.processEvents()
            img = scr.grabWindow(0).toImage()
        except Exception:
            return None
        return analyze_button_region(img, phys)

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

    def quit(self):
        try:
            chain = " <- ".join("%s:%d" % (f.name, f.lineno)
                                for f in traceback.extract_stack()[-4:-1])
            app_log("выход; вызов из " + chain)
        except Exception:
            pass
        app_log("выход")
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
            self.overlay.close()
        except Exception:
            pass
        self.qt_app.quit()


# ---------------------------------------------------------------- самопроверка
def _grab_region(screen, log_rect: QRect, path: str):
    try:
        img = screen.grabWindow(0).toImage()
        scale = float(screen.devicePixelRatio() or 1.0)
        crop = QRect(int(log_rect.x() * scale), int(log_rect.y() * scale),
                     int(log_rect.width() * scale), int(log_rect.height() * scale))
        crop = crop.intersected(QRect(0, 0, img.width(), img.height()))
        if crop.width() > 0 and crop.height() > 0:
            img.copy(crop).save(path, "PNG")
    except Exception:
        pass


def run_selftest(qt_app: QApplication, ctrl: App, out_dir: str):
    report = {
        "python": sys.version.split()[0],
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
    r = get_window_rect(get_taskbar_hwnd())
    if r:
        report["taskbar_rect_physical"] = [int(r.left), int(r.top),
                                           int(r.right - r.left), int(r.bottom - r.top)]
    res = ctrl.locator.locate()
    report["locator_source"] = ctrl.locator.source
    if ctrl.locator.last_error:
        report["locator_error"] = ctrl.locator.last_error
    if res is None:
        report["error"] = "кнопка «Пуск» не найдена"
    else:
        phys, offscreen = res
        report["start_rect_physical"] = [phys.x(), phys.y(), phys.width(), phys.height()]
        report["start_offscreen"] = bool(offscreen)
        log = physical_to_logical(phys)
        report["start_rect_logical"] = [log.x(), log.y(), log.width(), log.height()]
        ctrl.tick()                     # штатное позиционирование (с уточнением центра)
        geo = ctrl.last_geo
        report["status"] = ctrl.status
        if geo is None:
            # Запасной путь: ставим вручную, чтобы получить скриншоты для проверки
            size = 26
            c = log.center()
            geo = QRect(round(c.x() - size / 2), round(c.y() - size / 2), size, size)
            ctrl.overlay.set_icon_source(current_source(ctrl.cfg), size)
            ctrl.overlay.setGeometry(geo)
            ctrl.overlay.show()
            ctrl.overlay.raise_()
            ctrl.overlay._interactive_topmost()
            report["fallback_position"] = True
        if geo is not None:
            if ctrl.base_center is not None:
                report["center_logical"] = [ctrl.base_center.x(), ctrl.base_center.y()]
            report["overlay_geometry"] = [geo.x(), geo.y(), geo.width(), geo.height()]
            pad = 130
            region = QRect(geo.x() - pad, geo.y() - 70, geo.width() + 2 * pad, geo.height() + 140)
            ctrl.overlay.hide()
            qt_app.processEvents()
            _grab_region(scr, region, os.path.join(out_dir, "selftest_before.png"))
            ctrl.overlay.show()
            ctrl.overlay.raise_()
            ctrl.overlay._interactive_topmost()
            ex = exstyle_flags(int(ctrl.overlay.winId()))
            report["overlay_ws_ex_transparent"] = bool(ex & WS_EX_TRANSPARENT)
            report["overlay_ws_ex_layered"] = bool(ex & WS_EX_LAYERED)
            report["overlay_ws_ex_toolwindow"] = bool(ex & WS_EX_TOOLWINDOW)
            QTimer.singleShot(1000, lambda: _selftest_after(qt_app, ctrl, scr, region, out_dir, report))
            return
    # сюда попадаем только при ошибке
    QTimer.singleShot(100, lambda: _selftest_report_only(qt_app, ctrl, out_dir, report))


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
            dest = os.path.join(APPDATA_DIR, f"picked_selftest{i}.svg")
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


def _selftest_after(qt_app: QApplication, ctrl: App, scr, region: QRect, out_dir: str, report: dict):
    _grab_region(scr, region, os.path.join(out_dir, "selftest_after.png"))
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

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        pass

    qt_app = QApplication(sys.argv)
    qt_app.setQuitOnLastWindowClosed(False)
    qt_app.setApplicationName(APP_NAME)
    qt_app.setStyle("Fusion")
    qt_app.setFont(QFont("Segoe UI", 9))
    qt_app.setStyleSheet(QSS)
    if os.path.exists(ICON_ICO):
        qt_app.setWindowIcon(QIcon(ICON_ICO))

    if not selftest and not single_instance_acquire():
        if not autostart_run:
            QMessageBox.information(
                None, APP_TITLE,
                "IconsPo уже запущен.\nЗначок программы находится в трее (возле часов).")
        return 0

    ctrl = App(qt_app)
    if selftest:
        out_dir = os.getcwd()
        QTimer.singleShot(150, lambda: run_selftest(qt_app, ctrl, out_dir))
    elif ctrl.first_run and not autostart_run:
        # Первый запуск — показываем настройки, дальше программа живёт в трее
        QTimer.singleShot(300, ctrl.open_settings)
    return qt_app.exec()


if __name__ == "__main__":
    sys.exit(main())
