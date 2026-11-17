"""MCHOSE Battery Widget - standalone desktop widget."""
from __future__ import annotations

import json
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import (
    QEasingCurve, QEvent, QPoint, QPointF, QPropertyAnimation, QRect, QRectF, QSize, Qt, QThread, QTimer, pyqtProperty, pyqtSignal,
)
from PyQt5.QtGui import (
    QColor, QCursor, QFont, QIcon, QPainter, QPainterPath, QPen, QPixmap,
)
from PyQt5.QtWidgets import (
    QAction, QActionGroup, QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QMenu, QPushButton, QSlider, QSystemTrayIcon, QVBoxLayout, QWidget, QWidgetAction,
)

from PyQt5.QtNetwork import QLocalServer, QLocalSocket

import battery

APP_NAME = "MCHOSE 键鼠电量"
APP_DIR = Path(os.environ.get("APPDATA", str(Path.home()))) / "MCHOSEBatteryWidget"
CONFIG_FILE = APP_DIR / "config.json"
LOG_FILE = APP_DIR / "error.log"
SINGLE_KEY = "MCHOSEBatteryWidget-SingleInstance"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
RUN_VALUE = "MCHOSEBatteryWidget"

# Edge auto-hide: the avatar must be within this many pixels of a screen edge.
# Intentionally small: the old magnetic snap used 64px and felt like it grabbed
# the widget far too early.
EDGE_TRIGGER = 14
BASE_W = 420
BASE_H = 340
PANEL_BODY_WIDTH = 315
BUBBLE_TAIL = 10
# The device panel is intentionally half the visual width/height of the
# original bubble; the global size slider still scales it with the avatar.
PANEL_SHRINK = 0.5
PANEL_RAISE = 38
# The old "大" preset (1.15) is now the 100% reference.  The slider
# exposes 50%-300% of that reference size.
SIZE_BASE = 1.15
SIZE_MIN_PERCENT = 50
SIZE_MAX_PERCENT = 300
SIZE_DEFAULT_PERCENT = 100
MIN_SCALE = SIZE_BASE * SIZE_MIN_PERCENT / 100.0
MAX_SCALE = SIZE_BASE * SIZE_MAX_PERCENT / 100.0

DEFAULT_CONFIG = {
    "x": None,
    "y": None,
    "panel_visible": False,
    "panel_side": "left",
    "panel_vertical": "above",
    "auto_start": False,
    "scale": SIZE_BASE,
    "size_percent": SIZE_DEFAULT_PERCENT,
    "collapsed": False,
    "edge": None,
}


def resource_path(relative: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative


def load_config() -> dict:
    cfg = dict(DEFAULT_CONFIG)
    try:
        cfg.update(json.loads(CONFIG_FILE.read_text(encoding="utf-8")))
    except Exception:
        pass
    return cfg


def save_config(cfg: dict) -> None:
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception:
        pass


def hide_console() -> None:
    if os.name != "nt" or not getattr(sys, "frozen", False):
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 0)
    except Exception:
        pass


def trace(message: str) -> None:
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        with (APP_DIR / "startup.log").open("a", encoding="utf-8") as fh:
            fh.write(f"[{datetime.now().isoformat(timespec='seconds')}] {message}\n")
    except Exception:
        pass


def log_exception(exc_type, exc, tb) -> None:
    try:
        APP_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(f"\n[{datetime.now().isoformat(timespec='seconds')}]\n")
            traceback.print_exception(exc_type, exc, tb, file=fh)
    except Exception:
        pass
    sys.__excepthook__(exc_type, exc, tb)


def battery_color(value: int | None, charging: bool = False) -> QColor:
    if charging:
        return QColor("#4D8DFF")
    if value is None:
        return QColor("#C5C8D5")
    if value <= 20:
        return QColor("#F05A67")
    if value <= 40:
        return QColor("#F0A23B")
    return QColor("#35B27C")


def clamp_scale(value: float) -> float:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return SIZE_BASE * SIZE_DEFAULT_PERCENT / 100.0
    return max(MIN_SCALE, min(MAX_SCALE, value))


def clamp_size_percent(value: float) -> int:
    try:
        percent = int(round(float(value)))
    except (TypeError, ValueError):
        percent = SIZE_DEFAULT_PERCENT
    return max(SIZE_MIN_PERCENT, min(SIZE_MAX_PERCENT, percent))


def scale_from_percent(percent: float) -> float:
    return SIZE_BASE * clamp_size_percent(percent) / 100.0


def font(size: float, weight: int = QFont.Normal, scale: float = 1.0, minimum: int = 7) -> QFont:
    return QFont("Microsoft YaHei UI", max(minimum, int(round(size * scale))), weight)


class StatusThread(QThread):
    result = pyqtSignal(dict)

    def run(self) -> None:
        try:
            self.result.emit(battery.get_status())
        except Exception as exc:
            self.result.emit({
                "ok": False,
                "updatedAt": datetime.now().astimezone().isoformat(timespec="seconds"),
                "error": str(exc),
                "devices": [],
                "meta": {},
            })


class BatteryBar(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = None
        self._color = QColor("#C5C8D5")
        self._scale = 1.0
        self.apply_scale(1.0)

    def apply_scale(self, scale: float) -> None:
        self._scale = scale
        self.setFixedHeight(max(4, int(round(5 * scale))))
        self.update()

    def set_value(self, value: int | None, charging: bool = False) -> None:
        self._value = value
        self._color = battery_color(value, charging)
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(self.rect())
        radius = max(2.0, rect.height() / 2)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#EEF0F7"))
        painter.drawRoundedRect(rect, radius, radius)
        if self._value is not None:
            width = max(0.0, min(1.0, self._value / 100.0)) * rect.width()
            if width > 0:
                painter.setBrush(self._color)
                painter.drawRoundedRect(QRectF(0, 0, width, rect.height()), radius, radius)


class DeviceRow(QWidget):
    def __init__(self, kind: str, parent=None):
        super().__init__(parent)
        self.kind = kind
        self.icon = QLabel("键" if kind == "keyboard" else "鼠")
        self.icon.setAlignment(Qt.AlignCenter)
        self.name = QLabel("读取中…")
        self.name.setTextFormat(Qt.PlainText)
        self.status = QLabel("正在读取 MCHOSE HUB 缓存")
        self.status.setTextFormat(Qt.PlainText)
        self.percent = QLabel("--")
        self.percent.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.bar = BatteryBar()
        self.bar_holder = QWidget()
        self.bar_layout = QHBoxLayout(self.bar_holder)
        self.bar_layout.setContentsMargins(0, 0, 0, 0)
        self.bar_layout.setSpacing(0)
        self.bar_layout.addWidget(self.bar, 1)

        self.icon.hide()
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.addWidget(self.name, 0, 0)
        self.grid.addWidget(self.percent, 0, 1, Qt.AlignRight)
        self.grid.addWidget(self.status, 1, 0, 1, 2)
        self.grid.addWidget(self.bar_holder, 2, 0, 1, 2)
        self.grid.setColumnStretch(0, 1)
        self.apply_scale(1.0)

    def apply_scale(self, scale: float) -> None:
        self.setFixedHeight(max(36, int(round(60 * scale))))
        icon_size = max(14, int(round(30 * scale)))
        self.icon.setFixedSize(icon_size, icon_size)
        self.icon.setStyleSheet(
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:1,stop:0 #EEF0FF,stop:1 #F8F9FF);"
            f"border-radius:{max(4, int(round(10 * scale)))}px;"
            f"font-size:{max(7, int(round(12 * scale)))}px;font-weight:700;color:#6470D8;"
        )
        self.name.setFont(font(10, QFont.DemiBold, scale, 10))
        self.name.setFixedHeight(max(16, min(20, int(round(20 * scale)))))
        self.name.setStyleSheet("color:#26356F;")
        self.status.setFont(font(8, QFont.Normal, scale, 8))
        self.status.setFixedHeight(max(14, min(18, int(round(18 * scale)))))
        self.status.setStyleSheet("color:#8A8EA3;")
        self.percent.setFont(font(13, QFont.Bold, scale, 13))
        self.percent.setMinimumWidth(max(30, int(round(46 * scale))))
        self.bar.apply_scale(scale)
        self.bar_layout.setContentsMargins(0, 0, max(8, int(round(26 * scale))), 0)
        self.grid.setHorizontalSpacing(max(3, int(round(10 * scale))))
        self.grid.setVerticalSpacing(1)

    def update_device(self, device: dict) -> None:
        name = str(device.get("name") or ("键盘" if self.kind == "keyboard" else "鼠标"))
        battery_value = device.get("battery")
        charging = bool(device.get("charging"))
        status = str(device.get("status") or "")
        note = str(device.get("note") or "")
        display_name = name[7:] if name.upper().startswith("MCHOSE ") else name
        self.name.setText(display_name)
        self.name.setToolTip(name)
        self.status.setText(status)
        self.status.setToolTip(note or status)
        if battery_value is None:
            self.percent.setText("--")
        else:
            self.percent.setText(f"{int(battery_value)}%")
        self.percent.setStyleSheet(f"color:{battery_color(battery_value, charging).name()};")
        self.bar.set_value(battery_value, charging)


class BatteryPanel(QWidget):
    """White speech-bubble card with a small tail pointing at the avatar."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("batteryPanel")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self._scale = 1.0
        self._tail_side = "right"
        self._tail = BUBBLE_TAIL
        self._trail = 20
        self._body_width = PANEL_BODY_WIDTH

        self.title = QLabel("键鼠电量")
        self.title.setStyleSheet("color:#26356F;")
        self.header_status = QLabel("● 读取中")
        self.header_status.setStyleSheet("color:#8A8EA3; background:#F1F3F8; border-radius:6px; padding:1px 5px;")
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self.title)
        header.addStretch(1)
        header.addWidget(self.header_status)

        self.keyboard = DeviceRow("keyboard")
        self.mouse = DeviceRow("mouse")
        self.updated = QLabel("等待首次刷新")
        self.updated.setStyleSheet("color:#A0A4B8;")
        self.source = QLabel("MCHOSE HUB")
        self.source.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.source.setStyleSheet("color:#A0A4B8;")
        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.addWidget(self.updated)
        footer.addStretch(1)
        footer.addWidget(self.source)

        self.layout = QVBoxLayout(self)
        self.layout.setSpacing(0)
        self.updated.hide()
        self.source.hide()
        self.layout.addLayout(header)
        self.layout.addWidget(self.keyboard)
        self.layout.addWidget(self.mouse)
        self.apply_scale(1.0)

    def apply_scale(self, scale: float) -> None:
        self._scale = scale
        self.title.setFont(font(11, QFont.Bold, scale, 11))
        self.header_status.setFont(font(8, QFont.Normal, scale, 8))
        self.title.setFixedHeight(max(16, min(20, int(round(20 * scale)))))
        self.header_status.setFixedHeight(max(14, min(18, int(round(18 * scale)))))
        self.updated.setFont(font(8, QFont.Normal, scale, 8))
        self.source.setFont(font(8, QFont.Normal, scale, 8))
        self.keyboard.apply_scale(scale)
        self.mouse.apply_scale(scale)
        self._tail = max(4, int(round(BUBBLE_TAIL * scale)))
        self._trail = max(20, int(round(34 * scale)))
        self._cloud_pad = max(12, int(round(16 * scale)))
        self._body_width = max(150, int(round(PANEL_BODY_WIDTH * scale))) + 2 * self._cloud_pad
        self._update_geometry()

    def set_tail_side(self, side: str) -> None:
        if side not in ("left", "right", "top", "bottom"):
            side = "right"
        if side == self._tail_side:
            return
        self._tail_side = side
        self._update_geometry()

    def _update_geometry(self) -> None:
        s = self._scale
        margin = max(6, int(round(14 * s)))
        bottom = max(5, int(round(12 * s)))
        # Leave room for the cloud lobes so text never paints into a
        # transparent notch between two puffs.
        lobe = max(5, int(round(12 * s)))
        left = right = top = margin + lobe + self._cloud_pad
        bottom_margin = bottom + lobe + self._cloud_pad
        if self._tail_side == "left":
            left += self._trail
        elif self._tail_side == "right":
            right += self._trail
        elif self._tail_side == "top":
            top += self._trail
        else:
            bottom_margin += self._trail
        self.layout.setContentsMargins(left, top, right, bottom_margin)
        self.layout.setSpacing(max(2, int(round(8 * s))))
        width = self._body_width
        if self._tail_side in ("left", "right"):
            width += self._tail
        self.setFixedWidth(width)
        self.layout.activate()
        self.setFixedHeight(max(1, self.layout.sizeHint().height()))

    def _bubble_path(self) -> tuple[QPainterPath, QRectF]:
        """Wide smooth speech bubble matching the reference image."""
        w, h = float(self.width()), float(self.height())
        trail = float(self._trail) if self._tail_side in ("left", "right") else 0.0
        if self._tail_side == "right":
            body = QRectF(0, 0, w - trail, h)
        elif self._tail_side == "left":
            body = QRectF(trail, 0, w - trail, h)
        elif self._tail_side == "top":
            body = QRectF(0, trail, w, h - trail)
        else:
            body = QRectF(0, 0, w, h - trail)

        inset = max(2.0, 4.0 * self._scale)
        shape = body.adjusted(inset, inset, -inset, -inset)
        radius = min(shape.width(), shape.height()) * 0.48
        path = QPainterPath()
        path.addRoundedRect(shape, radius, radius)
        return path, body

    def _trail_circles(self) -> list[tuple[QPointF, float]]:
        w, h = float(self.width()), float(self.height())
        trail = float(self._trail) if self._tail_side in ("left", "right") else 0.0
        if self._tail_side == "right":
            body = QRectF(0, 0, w - trail, h)
            x1, x2 = w - 32.0, w - 12.0
        elif self._tail_side == "left":
            body = QRectF(trail, 0, w - trail, h)
            x1, x2 = 17.0, 7.0
        else:
            body = QRectF(0, 0, w, h - trail)
            x1, x2 = w - 32.0, w - 12.0
        r1 = max(12.0, 36.0 * self._scale)
        r2 = max(7.0, 18.0 * self._scale)
        y1 = body.top() + body.height() * 0.78
        y2 = min(h - r2 - 2.0, y1 + r1 + r2 + 2.0)
        return [(QPointF(x1, y1), r1), (QPointF(x2, y2), r2)]

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        path, _body = self._bubble_path()

        circles = self._trail_circles()
        pen = QPen(
            QColor("#26356F"),
            max(3.5, 9.0 * self._scale),
            Qt.SolidLine,
            Qt.RoundCap,
            Qt.RoundJoin,
        )
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(18, 31, 68, 38))
        offset = max(2.0, 3.0 * self._scale)
        shadow = QPainterPath(path)
        shadow.translate(0, offset)
        painter.drawPath(shadow)
        for center, radius in circles:
            circle = QPainterPath()
            circle.addEllipse(center, radius, radius)
            circle_shadow = QPainterPath(circle)
            circle_shadow.translate(0, offset)
            painter.drawPath(circle_shadow)

        painter.setBrush(QColor(255, 255, 255))
        painter.setPen(pen)
        painter.drawPath(path)
        for center, radius in circles:
            circle = QPainterPath()
            circle.addEllipse(center, radius, radius)
            painter.drawPath(circle)

    def update_status(self, data: dict) -> None:
        devices = {str(d.get("type")): d for d in data.get("devices", []) if isinstance(d, dict)}
        self.keyboard.update_device(devices.get("keyboard", {}))
        self.mouse.update_device(devices.get("mouse", {}))

        connected = any(bool(d.get("connected")) for d in devices.values())
        if data.get("ok") and connected:
            self.header_status.setText("● 已连接")
            self.header_status.setStyleSheet("color:#FFFFFF; background:#2FB36D; border-radius:6px; padding:1px 6px;")
        elif data.get("ok"):
            self.header_status.setText("● 未连接")
            self.header_status.setStyleSheet("color:#FFFFFF; background:#E0A23B; border-radius:6px; padding:1px 6px;")
        else:
            self.header_status.setText("● 读取失败")
            self.header_status.setStyleSheet("color:#FFFFFF; background:#E05260; border-radius:6px; padding:1px 6px;")

        updated = str(data.get("updatedAt") or "")
        if "T" in updated:
            updated = updated.split("T", 1)[1][:8]
        error = data.get("error")
        self.updated.setText(f"更新于 {updated}" if updated and not error else (error or "等待首次刷新"))
        meta = data.get("meta") or {}
        files = meta.get("files")
        self.source.setText(f"MCHOSE HUB · {files} 文件" if files else "MCHOSE HUB")


class AvatarWidget(QWidget):
    """Transparent cut-out avatar, like the DSH widget, with a battery badge."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self._pixmap = QPixmap(str(resource_path("assets/avatar.png")))
        self._mouse_battery = None
        self._mouse_charging = False
        self._scale = 1.0
        self._squash = 1.0
        self._squash_anim = None
        self.setToolTip("MCHOSE 键鼠电量")
        self.apply_scale(1.0)

    def get_squash(self) -> float:
        return self._squash

    def set_squash(self, value: float) -> None:
        self._squash = max(0.80, min(1.0, float(value)))
        self.update()

    squash = pyqtProperty(float, fget=get_squash, fset=set_squash)

    def press_squash(self) -> None:
        """Hold the avatar slightly squashed while the mouse is pressed."""
        if self._squash_anim is not None:
            self._squash_anim.stop()
            self._squash_anim = None
        self.set_squash(0.93)

    def release_squash(self) -> None:
        """Spring back when the mouse button is released."""
        if self._squash_anim is not None:
            self._squash_anim.stop()
        anim = QPropertyAnimation(self, b"squash", self)
        anim.setDuration(90)
        anim.setStartValue(self._squash)
        anim.setEndValue(1.0)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start()
        self._squash_anim = anim

    def apply_scale(self, scale: float) -> None:
        self._scale = scale
        size = max(50, int(round(78 * scale)))
        self.setFixedSize(size, size)
        self.update()

    def set_mouse(self, device: dict | None) -> None:
        self._mouse_battery = (device or {}).get("battery")
        self._mouse_charging = bool((device or {}).get("charging"))
        self.update()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        if not self._pixmap.isNull():
            if self._squash < 0.999:
                target_h = max(1, int(round(self.height() * self._squash)))
                target = QRect(0, self.height() - target_h, self.width(), target_h)
                scaled = self._pixmap.scaled(target.size(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
                painter.drawPixmap(target.topLeft(), scaled)
            else:
                scaled = self._pixmap.scaled(self.size(), Qt.KeepAspectRatio, Qt.SmoothTransformation)
                x = (self.width() - scaled.width()) // 2
                y = (self.height() - scaled.height()) // 2
                painter.drawPixmap(x, y, scaled)

        if self._mouse_battery is not None:
            text = f"{int(self._mouse_battery)}%"
            badge_font = font(7, QFont.Bold, self._scale)
            painter.setFont(badge_font)
            metrics = painter.fontMetrics()
            pad = max(6, int(round(12 * self._scale)))
            width = metrics.width(text) + pad
            height = max(13, int(round(18 * self._scale)))
            badge = QRectF(
                self.width() - width + max(1, int(round(2 * self._scale))),
                self.height() - height + max(1, int(round(2 * self._scale))),
                width,
                height,
            )
            painter.setPen(Qt.NoPen)
            painter.setBrush(battery_color(self._mouse_battery, self._mouse_charging))
            painter.drawRoundedRect(badge, height / 2, height / 2)
            painter.setPen(QColor("white"))
            painter.drawText(badge, Qt.AlignCenter, text)


class EdgeTab(QWidget):
    """The little handle left visible when the widget is tucked into an edge."""

    clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setCursor(Qt.PointingHandCursor)
        self._edge = "left"
        self._scale = 1.0
        self._hover = False
        self.configure("left", 1.0)

    def configure(self, edge: str, scale: float) -> None:
        self._edge = edge if edge in ("left", "right", "top", "bottom") else "left"
        self._scale = scale
        short = max(12, int(round(16 * scale)))
        long = max(34, int(round(56 * scale)))
        if self._edge in ("left", "right"):
            self.setFixedSize(short, long)
        else:
            self.setFixedSize(long, short)
        self.update()

    def enterEvent(self, event) -> None:
        self._hover = True
        self.update()
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        self._hover = False
        self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
            event.accept()
            return
        super().mousePressEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        rect = QRectF(0.5, 0.5, self.width() - 1, self.height() - 1)
        radius = min(rect.width(), rect.height()) / 2
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(32, 45, 80, 28))
        painter.drawRoundedRect(rect.translated(0, 2), radius, radius)
        painter.setBrush(QColor(244, 246, 255, 250) if self._hover else QColor(255, 255, 255, 248))
        painter.setPen(QPen(QColor(214, 218, 235), 1))
        painter.drawRoundedRect(rect, radius, radius)

        cx, cy = self.width() / 2, self.height() / 2
        a = max(3.0, 4.0 * self._scale)
        painter.setPen(QPen(QColor("#6470D8"), max(1.5, 2.0 * self._scale), Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        if self._edge == "left":
            points = (QPointF(cx - a, cy - a), QPointF(cx + a, cy), QPointF(cx - a, cy + a))
        elif self._edge == "right":
            points = (QPointF(cx + a, cy - a), QPointF(cx - a, cy), QPointF(cx + a, cy + a))
        elif self._edge == "top":
            points = (QPointF(cx - a, cy - a), QPointF(cx, cy + a), QPointF(cx + a, cy - a))
        else:
            points = (QPointF(cx - a, cy + a), QPointF(cx, cy - a), QPointF(cx + a, cy + a))
        painter.drawPolyline(*points)


class ToggleSwitch(QWidget):
    """Small rounded switch used by the settings popup."""

    toggled = pyqtSignal(bool)

    def __init__(self, checked: bool = False, parent=None):
        super().__init__(parent)
        self.setFixedSize(21, 12)
        self.setCursor(Qt.PointingHandCursor)
        self._checked = bool(checked)
        self._offset = 1.0 if self._checked else 0.0
        self._anim = QPropertyAnimation(self, b"offset", self)
        self._anim.setDuration(140)
        self._anim.setEasingCurve(QEasingCurve.OutCubic)

    def get_offset(self) -> float:
        return self._offset

    def set_offset(self, value: float) -> None:
        self._offset = float(value)
        self.update()

    offset = pyqtProperty(float, fget=get_offset, fset=set_offset)

    def isChecked(self) -> bool:
        return self._checked

    def setChecked(self, checked: bool, animate: bool = True) -> None:
        checked = bool(checked)
        if self._checked == checked:
            if not animate:
                self._offset = 1.0 if checked else 0.0
                self.update()
            return
        self._checked = checked
        target = 1.0 if checked else 0.0
        self._anim.stop()
        if animate:
            self._anim.setStartValue(self._offset)
            self._anim.setEndValue(target)
            self._anim.start()
        else:
            self._offset = target
            self.update()

    def mouseReleaseEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.setChecked(not self._checked)
            self.toggled.emit(self._checked)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        w, h = float(self.width()), float(self.height())
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#6470D8") if self._checked else QColor("#DCE1EE"))
        painter.drawRoundedRect(QRectF(0, 0, w, h), h / 2.0, h / 2.0)
        knob = h - 4.0
        knob_x = 2.0 + self._offset * (w - knob - 4.0)
        painter.setBrush(QColor(255, 255, 255))
        painter.setPen(QPen(QColor(30, 42, 70, 18), 0.8))
        painter.drawEllipse(QRectF(knob_x, 2.0, knob, knob))


class SettingsPopup(QWidget):
    """Rounded settings card opened from the avatar."""

    size_changed = pyqtSignal(int)
    autostart_changed = pyqtSignal(bool)
    hide_requested = pyqtSignal()
    refresh_requested = pyqtSignal()
    quit_requested = pyqtSignal()
    closed = pyqtSignal()

    def __init__(self, percent: int, autostart: bool, parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setFixedWidth(180)
        self._percent = int(percent)
        self._autostart = bool(autostart)
        self._build_ui()
        self.adjustSize()
        self.setFixedHeight(192)

    def _build_ui(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(6, 6, 6, 6)
        outer.setSpacing(0)

        card = QFrame()
        card.setObjectName("settingsCard")
        card.setAttribute(Qt.WA_StyledBackground, True)
        card.setStyleSheet(
            "QFrame#settingsCard{background:transparent;border:none;}"
        )
        outer.addWidget(card)

        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 9, 10, 8)
        layout.setSpacing(3)

        header = QHBoxLayout()
        header.setSpacing(4)
        title_col = QVBoxLayout()
        title_col.setSpacing(0)
        title = QLabel("挂件设置")
        title.setFont(font(6.0, QFont.Bold, 1.0, 7))
        title.setStyleSheet("color:#202437;")
        subtitle = QLabel("MCHOSE 键鼠电量")
        subtitle.setFont(font(4.0, QFont.Normal, 1.0, 6))
        subtitle.setStyleSheet("color:#9297AA;")
        title_col.addWidget(title)
        title_col.addWidget(subtitle)
        header.addLayout(title_col, 1)
        close_btn = QPushButton("×")
        close_btn.setFixedSize(12, 12)
        close_btn.setCursor(Qt.PointingHandCursor)
        close_btn.setFont(font(6.0, QFont.Normal, 1.0, 7))
        close_btn.setStyleSheet(
            "QPushButton{border:none;border-radius:6px;background:#F3F5FA;color:#6E7386;padding:0;}"
            "QPushButton:hover{background:#E9ECF7;color:#3D4358;}"
        )
        close_btn.clicked.connect(self.close)
        header.addWidget(close_btn, 0, Qt.AlignTop)
        layout.addLayout(header)

        line = QFrame()
        line.setFrameShape(QFrame.HLine)
        line.setFixedHeight(1)
        line.setStyleSheet("background:#EEF0F6;border:none;")
        layout.addWidget(line)

        size_head = QHBoxLayout()
        size_head.setContentsMargins(0, 1, 0, 0)
        size_label = QLabel("大小")
        size_label.setFont(font(5.0, QFont.DemiBold, 1.0, 7))
        size_label.setStyleSheet("color:#34394D;")
        self.size_value = QLabel(f"{self._percent}%")
        self.size_value.setAlignment(Qt.AlignCenter)
        self.size_value.setFixedHeight(12)
        self.size_value.setMinimumWidth(24)
        self.size_value.setFont(font(4.5, QFont.DemiBold, 1.0, 7))
        self.size_value.setStyleSheet("background:#F0F2FF;border-radius:4px;color:#6470D8;padding:0 3px;")
        size_head.addWidget(size_label)
        size_head.addStretch(1)
        size_head.addWidget(self.size_value)
        layout.addLayout(size_head)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(SIZE_MIN_PERCENT, SIZE_MAX_PERCENT)
        self.slider.setSingleStep(5)
        self.slider.setPageStep(25)
        self.slider.setValue(self._percent)
        self.slider.setFixedHeight(12)
        self.slider.setCursor(Qt.PointingHandCursor)
        self.slider.setStyleSheet(
            "QSlider::groove:horizontal{height:3px;background:#ECEEF6;border-radius:2px;}"
            "QSlider::sub-page:horizontal{background:#7B86E8;border-radius:2px;}"
            "QSlider::handle:horizontal{width:8px;height:8px;margin:-3px 0;"
            "border-radius:4px;background:#FFFFFF;border:1px solid #6470D8;}"
        )
        self.slider.valueChanged.connect(self._on_size_changed)
        layout.addWidget(self.slider)

        hint = QLabel("50%  ·  100% 为基准  ·  300%")
        hint.setFont(font(4.0, QFont.Normal, 1.0, 6))
        hint.setStyleSheet("color:#A0A5B7;")
        layout.addWidget(hint)

        auto_row = QWidget()
        auto_row.setFixedHeight(21)
        auto_layout = QHBoxLayout(auto_row)
        auto_layout.setContentsMargins(0, 0, 0, 0)
        auto_layout.setSpacing(4)
        auto_text = QVBoxLayout()
        auto_text.setSpacing(0)
        auto_label = QLabel("开机启动")
        auto_label.setFont(font(5.0, QFont.DemiBold, 1.0, 7))
        auto_label.setStyleSheet("color:#34394D;")
        auto_sub = QLabel("登录 Windows 后自动运行")
        auto_sub.setFont(font(4.0, QFont.Normal, 1.0, 6))
        auto_sub.setStyleSheet("color:#9A9FB2;")
        auto_text.addWidget(auto_label)
        auto_text.addWidget(auto_sub)
        auto_layout.addLayout(auto_text, 1)
        self.auto_switch = ToggleSwitch(self._autostart)
        self.auto_switch.toggled.connect(self._on_autostart_changed)
        auto_layout.addWidget(self.auto_switch, 0, Qt.AlignVCenter)
        layout.addWidget(auto_row)

        line2 = QFrame()
        line2.setFrameShape(QFrame.HLine)
        line2.setFixedHeight(1)
        line2.setStyleSheet("background:#EEF0F6;border:none;")
        layout.addWidget(line2)

        actions = QVBoxLayout()
        actions.setSpacing(1)
        for text, signal, danger in (
            ("隐藏挂件", self.hide_requested, False),
            ("立即刷新", self.refresh_requested, False),
            ("退出", self.quit_requested, True),
        ):
            button = QPushButton(text)
            button.setFixedHeight(16)
            button.setCursor(Qt.PointingHandCursor)
            button.setFont(font(4.5, QFont.Normal, 1.0, 7))
            if danger:
                button.setStyleSheet(
                    "QPushButton{border:none;border-radius:5px;text-align:left;padding:0 6px;"
                    "background:transparent;color:#D45B5B;}"
                    "QPushButton:hover{background:#FFF1F1;}"
                    "QPushButton:pressed{background:#FFE8E8;}"
                )
            else:
                button.setStyleSheet(
                    "QPushButton{border:none;border-radius:5px;text-align:left;padding:0 6px;"
                    "background:transparent;color:#34394D;}"
                    "QPushButton:hover{background:#F3F5FA;}"
                    "QPushButton:pressed{background:#EAEDF7;}"
                )
            button.clicked.connect(lambda checked=False, sig=signal: sig.emit())
            actions.addWidget(button)
        layout.addLayout(actions)

        footer = QLabel("MCHOSE Battery Widget  ·  v1.5.7")
        footer.setAlignment(Qt.AlignCenter)
        footer.setFont(font(4.0, QFont.Normal, 1.0, 6))
        footer.setStyleSheet("color:#B4B8C7;")
        layout.addWidget(footer)

    def _on_size_changed(self, value: int) -> None:
        self._percent = int(value)
        self.size_value.setText(f"{value}%")
        self.size_changed.emit(int(value))

    def _on_autostart_changed(self, checked: bool) -> None:
        self._autostart = bool(checked)
        self.autostart_changed.emit(bool(checked))

    def set_size(self, percent: int) -> None:
        percent = clamp_size_percent(percent)
        self._percent = percent
        self.slider.blockSignals(True)
        self.slider.setValue(percent)
        self.slider.blockSignals(False)
        self.size_value.setText(f"{percent}%")

    def set_autostart(self, checked: bool) -> None:
        self._autostart = bool(checked)
        self.auto_switch.blockSignals(True)
        self.auto_switch.setChecked(bool(checked), animate=False)
        self.auto_switch.blockSignals(False)

    def show_at(self, global_pos: QPoint) -> None:
        self.adjustSize()
        self.setFixedHeight(192)
        screen = QApplication.screenAt(global_pos) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        x = global_pos.x() - self.width() + 13
        if x < area.left() + 8:
            x = global_pos.x() + 8
        x = max(area.left() + 8, min(x, area.right() - self.width() - 8))
        y = global_pos.y() - self.height() - 7
        if y < area.top() + 8:
            y = global_pos.y() + 9
        y = max(area.top() + 8, min(y, area.bottom() - self.height() - 8))
        self.move(x, y)
        self.show()
        self.raise_()
        self.activateWindow()

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        card = QRectF(6, 5, self.width() - 12, self.height() - 12)
        for spread, alpha in ((4, 4), (3, 7), (2, 10), (1, 14)):
            shadow = card.adjusted(-spread, -spread + 2, spread, spread + 2)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor(32, 42, 72, alpha))
            painter.drawRoundedRect(shadow, 11 + spread, 11 + spread)
        painter.setPen(QPen(QColor(226, 230, 241), 1))
        painter.setBrush(QColor(255, 255, 255, 253))
        painter.drawRoundedRect(card, 11, 11)

    def hideEvent(self, event) -> None:
        self.closed.emit()
        super().hideEvent(event)


class BatteryWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(APP_NAME)
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)

        self.cfg = load_config()
        # Migrate older configs: the previous "大" size becomes 100%.
        self.size_percent = clamp_size_percent(self.cfg.get("size_percent", SIZE_DEFAULT_PERCENT))
        self.scale = scale_from_percent(self.size_percent)
        self._quitting = False
        self._thread = None
        self._settings_menu = None
        self._settings_popup = None
        self._status = {}
        self._drag_origin = None
        self._drag_start = None
        self._moved = False
        self._snap_anim = None
        self._collapsed = False
        self._edge = None
        self._expanded_pos = QPoint(0, 0)
        self._panel_side = str(self.cfg.get("panel_side") or "left")
        self._panel_vertical = str(self.cfg.get("panel_vertical") or "above")

        self.panel = BatteryPanel(self)
        self.avatar = AvatarWidget(self)
        self.avatar.installEventFilter(self)
        self.edge_tab = EdgeTab(self)
        self.edge_tab.clicked.connect(self.expand_from_edge)
        self.edge_tab.hide()
        self.panel.hide()

        self._apply_scale_assets()
        self._recalc_size()
        self._apply_layout()

        self._save_timer = QTimer(self)
        self._save_timer.setSingleShot(True)
        self._save_timer.timeout.connect(self._save_position)

        self._restore_position()
        self._update_mask()

        self.refresh_timer = QTimer(self)
        self.refresh_timer.setInterval(5000)
        self.refresh_timer.timeout.connect(self.refresh)
        self.refresh_timer.start()
        QTimer.singleShot(80, self.refresh)

    def _apply_scale_assets(self) -> None:
        self.panel.apply_scale(self.scale * PANEL_SHRINK)
        self.avatar.apply_scale(self.scale)
        self.edge_tab.configure(self._edge or "left", self.scale)

    def _recalc_size(self) -> None:
        s = self.scale
        margin = max(10, int(round(16 * s)))
        gap = max(3, int(round(4 * s)))
        width = max(
            int(round(BASE_W * s)),
            margin * 2 + self.panel.width() + gap + self.avatar.width(),
        )
        height = max(
            int(round(BASE_H * s)),
            self.panel.height() + margin * 2,
            self.avatar.height() + margin * 2,
        )
        self.setFixedSize(width, height)

    def _apply_layout(self, side: str | None = None, vertical: str | None = None) -> None:
        if side in ("left", "right"):
            self._panel_side = side
        if vertical in ("above", "below"):
            self._panel_vertical = vertical
        s = self.scale
        margin = max(10, int(round(16 * s)))
        gap = max(3, int(round(4 * s)))
        avatar_y = self.height() - self.avatar.height() - margin
        panel_y = max(
            margin,
            self.height() - self.panel.height() - margin - int(round(PANEL_RAISE * s)),
        )
        if self._panel_side == "right":
            avatar_x = margin
            panel_x = avatar_x + self.avatar.width() + gap
            tail_side = "left"
        else:
            avatar_x = self.width() - self.avatar.width() - margin
            panel_x = avatar_x - self.panel.width() - gap
            tail_side = "right"
        self.panel.set_tail_side(tail_side)
        self.avatar.move(avatar_x, avatar_y)
        self.panel.move(panel_x, panel_y)
        self.avatar.raise_()
        if self.panel.isVisible():
            self.panel.raise_()

    def _flip_panel_if_needed(self) -> None:
        center = self.avatar.mapToGlobal(self.avatar.rect().center())
        screen = QApplication.screenAt(center) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        desired = "right" if center.x() < area.center().x() else "left"
        if desired == self._panel_side:
            return
        old_avatar = self.avatar.mapToGlobal(QPoint(0, 0))
        self._apply_layout(desired)
        new_avatar = self.avatar.mapToGlobal(QPoint(0, 0))
        self.move(self.pos() + old_avatar - new_avatar)

    def _tab_target(self, edge: str) -> QPoint:
        tab = self.edge_tab
        pos = self._expanded_pos
        anchor = QPoint(
            pos.x() + self.avatar.x() + self.avatar.width() // 2,
            pos.y() + self.avatar.y() + self.avatar.height() // 2,
        )
        screen = QApplication.screenAt(anchor) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        if edge in ("left", "right"):
            y = anchor.y() - tab.height() // 2
            y = max(area.top(), min(y, area.bottom() - tab.height() + 1))
            x = area.left() if edge == "left" else area.right() - tab.width() + 1
        else:
            x = anchor.x() - tab.width() // 2
            x = max(area.left(), min(x, area.right() - tab.width() + 1))
            y = area.top() if edge == "top" else area.bottom() - tab.height() + 1
        return QPoint(x, y)

    def _animate_to(self, target: QPoint) -> None:
        if self._snap_anim is not None:
            self._snap_anim.stop()
        anim = QPropertyAnimation(self, b"pos", self)
        anim.setDuration(180)
        anim.setStartValue(self.pos())
        anim.setEndValue(target)
        anim.setEasingCurve(QEasingCurve.OutCubic)
        anim.start()
        self._snap_anim = anim

    def _place_collapsed(self) -> None:
        self.edge_tab.move(0, 0)
        self.edge_tab.raise_()
        self.move(self._tab_target(self._edge or "left"))

    def collapse_to_edge(self, edge: str, animate: bool = True) -> None:
        if self._collapsed:
            return
        self._expanded_pos = self.pos()
        self._collapsed = True
        self._edge = edge
        self.panel.hide()
        self.avatar.hide()
        self.edge_tab.configure(edge, self.scale)
        self.edge_tab.move(0, 0)
        self.edge_tab.show()
        self.edge_tab.raise_()
        target = self._tab_target(edge)
        if animate:
            self._animate_to(target)
        else:
            self.move(target)
        self.cfg["collapsed"] = True
        self.cfg["edge"] = edge
        self.cfg["panel_visible"] = False
        self._save_position()

    def expand_from_edge(self) -> None:
        if not self._collapsed:
            return
        self._collapsed = False
        self._edge = None
        self.edge_tab.hide()
        self.avatar.show()
        self._apply_layout()
        self.panel.show()
        self.panel.raise_()
        old_pos = self.pos()
        target = self._expanded_pos
        self.move(target)
        self._ensure_expanded_visible()
        self._flip_panel_if_needed()
        self._ensure_expanded_visible()
        target = self.pos()
        self.move(old_pos)
        self._expanded_pos = target
        self._animate_to(target)
        self.cfg["collapsed"] = False
        self.cfg["edge"] = None
        self.cfg["panel_visible"] = True
        self._save_position(target)

    def _edge_near(self) -> str | None:
        center = self.avatar.mapToGlobal(self.avatar.rect().center())
        screen = QApplication.screenAt(center) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        rect = QRect(self.avatar.mapToGlobal(QPoint(0, 0)), self.avatar.size())
        distances = {
            "left": rect.left() - area.left(),
            "right": area.right() - rect.right(),
            "top": rect.top() - area.top(),
            "bottom": area.bottom() - rect.bottom(),
        }
        edge, distance = min(distances.items(), key=lambda item: item[1])
        return edge if distance <= EDGE_TRIGGER else None

    def _ensure_expanded_visible(self) -> None:
        center = self.avatar.mapToGlobal(self.avatar.rect().center())
        screen = QApplication.screenAt(center) or QApplication.primaryScreen()
        area = screen.availableGeometry()
        rect = QRect(self.avatar.mapToGlobal(QPoint(0, 0)), self.avatar.size())
        pad = max(4, int(round(8 * self.scale)))
        dx = dy = 0
        if rect.left() < area.left() + pad:
            dx = area.left() + pad - rect.left()
        elif rect.right() > area.right() - pad:
            dx = area.right() - pad - rect.right()
        if rect.top() < area.top() + pad:
            dy = area.top() + pad - rect.top()
        elif rect.bottom() > area.bottom() - pad:
            dy = area.bottom() - pad - rect.bottom()
        if dx or dy:
            self.move(self.pos() + QPoint(dx, dy))

    def _restore_position(self) -> None:
        area = QApplication.primaryScreen().availableGeometry()
        x = self.cfg.get("x")
        y = self.cfg.get("y")
        if not isinstance(x, int) or not isinstance(y, int):
            x = area.right() - self.width() - 18
            y = area.bottom() - self.height() - 12
        self._expanded_pos = QPoint(x, y)
        self.move(self._expanded_pos)
        self._ensure_expanded_visible()
        self._flip_panel_if_needed()
        self._ensure_expanded_visible()
        self._expanded_pos = self.pos()
        edge = self.cfg.get("edge")
        if bool(self.cfg.get("collapsed")) and edge in ("left", "right", "top", "bottom"):
            self.collapse_to_edge(str(edge), animate=False)
        else:
            self.avatar.show()
            self.edge_tab.hide()
            # 常规启动只显示头像；面板由悬停/点击打开。
            self.panel.hide()

    def _update_mask(self) -> None:
        # Per-pixel alpha gives click-through on transparent areas already.
        # setMask() on a translucent top-level clips child widgets on Windows.
        self.clearMask()

    def show_panel(self) -> None:
        if self._collapsed:
            return
        if not self.panel.isVisible():
            self.panel.show()
            self.panel.raise_()
            self.cfg["panel_visible"] = True
            self._save_position()

    def hide_panel(self) -> None:
        if self.panel.isVisible():
            self.panel.hide()
            self.cfg["panel_visible"] = False
            self._save_position()

    def toggle_panel(self) -> None:
        if self._collapsed:
            self.expand_from_edge()
        elif self.panel.isVisible():
            self.hide_panel()
        else:
            self.show_panel()

    def enterEvent(self, event) -> None:
        # Hover must not open the battery bubble; only a left click does.
        super().enterEvent(event)

    def leaveEvent(self, event) -> None:
        QTimer.singleShot(900, self._maybe_hide_panel)
        super().leaveEvent(event)

    def _maybe_hide_panel(self) -> None:
        if not self.isVisible() or not self.panel.isVisible():
            return
        cursor = self.mapFromGlobal(QCursor.pos())
        inside = self.avatar.geometry().contains(cursor) or self.panel.geometry().contains(cursor)
        if not inside:
            self.hide_panel()

    def _start_drag(self, global_pos: QPoint) -> None:
        if self._collapsed:
            return
        self.avatar.press_squash()
        self._drag_origin = global_pos - self.frameGeometry().topLeft()
        self._drag_start = global_pos
        self._expanded_pos = self.pos()
        self._moved = False
        self.grabMouse()

    def _drag_to(self, global_pos: QPoint) -> None:
        if self._drag_origin is None:
            return
        delta = global_pos - self._drag_start
        if delta.manhattanLength() > 4:
            self._moved = True
        self.move(global_pos - self._drag_origin)

    def _finish_drag(self) -> None:
        if self._drag_origin is None:
            return
        moved = self._moved
        self._drag_origin = None
        self.releaseMouse()
        self.avatar.release_squash()
        if moved:
            edge = self._edge_near()
            if edge is not None:
                self.collapse_to_edge(edge, animate=True)
            else:
                self._flip_panel_if_needed()
                self._ensure_expanded_visible()
                self._expanded_pos = self.pos()
                self._save_position()
        else:
            self.toggle_panel()

    def eventFilter(self, obj, event) -> bool:
        if obj is self.avatar:
            etype = event.type()
            if etype == QEvent.Leave:
                QTimer.singleShot(900, self._maybe_hide_panel)
            elif etype == QEvent.MouseButtonPress and event.button() == Qt.RightButton:
                self.show_settings_menu(event.globalPos())
                return True
            elif etype == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                self._start_drag(event.globalPos())
                return True
            elif etype == QEvent.MouseMove and self._drag_origin is not None:
                self._drag_to(event.globalPos())
                return True
            elif etype == QEvent.MouseButtonRelease and self._drag_origin is not None:
                self._finish_drag()
                return True
        return super().eventFilter(obj, event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton and not self._collapsed:
            if self.avatar.geometry().contains(event.pos()):
                self._start_drag(event.globalPos())
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event) -> None:
        if self._drag_origin is not None and event.buttons() & Qt.LeftButton:
            self._drag_to(event.globalPos())
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._drag_origin is not None:
            self._finish_drag()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _save_position(self, pos: QPoint | None = None) -> None:
        if self._collapsed:
            self.cfg["x"] = self._expanded_pos.x()
            self.cfg["y"] = self._expanded_pos.y()
            self.cfg["collapsed"] = True
            self.cfg["edge"] = self._edge
        else:
            p = pos if pos is not None else self.pos()
            self.cfg["x"] = p.x()
            self.cfg["y"] = p.y()
            self.cfg["collapsed"] = False
            self.cfg["edge"] = None
            self._expanded_pos = p
        self.cfg["panel_visible"] = self.panel.isVisible()
        self.cfg["panel_side"] = self._panel_side
        self.cfg["panel_vertical"] = self._panel_vertical
        self.cfg["scale"] = self.scale
        self.cfg["size_percent"] = self.size_percent
        self.cfg["auto_start"] = self._is_autostart_enabled()
        save_config(self.cfg)

    def _sync_size_controls(self) -> None:
        popup = getattr(self, "_settings_popup", None)
        if popup is not None:
            popup.set_size(self.size_percent)
        slider = self.size_slider
        if slider is not None:
            slider.blockSignals(True)
            slider.setValue(self.size_percent)
            slider.blockSignals(False)
        if self.size_value_label is not None:
            self.size_value_label.setText(f"{self.size_percent}%")

    def set_size_percent(self, percent: int) -> None:
        percent = clamp_size_percent(percent)
        self.size_percent = percent
        self.set_scale(scale_from_percent(percent))
        self._sync_size_controls()

    def set_scale(self, scale: float) -> None:
        scale = clamp_scale(scale)
        if abs(scale - self.scale) < 0.001:
            self.size_percent = clamp_size_percent(scale / SIZE_BASE * 100.0)
            self._sync_size_controls()
            return
        was_collapsed = self._collapsed
        self.scale = scale
        self.size_percent = clamp_size_percent(scale / SIZE_BASE * 100.0)
        self._apply_scale_assets()
        self._recalc_size()
        self._apply_layout()
        if was_collapsed:
            self._place_collapsed()
        else:
            self._flip_panel_if_needed()
            self._ensure_expanded_visible()
            self._expanded_pos = self.pos()
        self.cfg["scale"] = scale
        self.cfg["size_percent"] = self.size_percent
        self._sync_size_controls()
        self._save_position()

    def refresh(self) -> None:
        if self._thread is not None and self._thread.isRunning():
            return
        self._thread = StatusThread(self)
        self._thread.result.connect(self.apply_status)
        self._thread.finished.connect(self._thread_finished)
        self._thread.start()

    def _thread_finished(self) -> None:
        thread = self._thread
        self._thread = None
        if thread is not None:
            thread.deleteLater()

    def apply_status(self, data: dict) -> None:
        self._status = data
        self.panel.update_status(data)
        devices = {str(d.get("type")): d for d in data.get("devices", []) if isinstance(d, dict)}
        keyboard = devices.get("keyboard", {})
        mouse = devices.get("mouse", {})
        self.avatar.set_mouse(mouse)
        if self.tray is not None:
            key_text = f"{keyboard.get('battery')}%" if keyboard.get("battery") is not None else "--"
            mouse_text = f"{mouse.get('battery')}%" if mouse.get("battery") is not None else "--"
            self.tray.setToolTip(f"{APP_NAME}\n键盘 {key_text} · 鼠标 {mouse_text}")

    def toggle_visibility(self) -> None:
        if self.isVisible():
            self.hide()
        else:
            self.show()
            self.raise_()
            self.activateWindow()
        self._update_tray_actions()

    def set_autostart(self, enabled: bool) -> None:
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE)
            try:
                if enabled:
                    winreg.SetValueEx(key, RUN_VALUE, 0, winreg.REG_SZ, self._launch_command())
                else:
                    try:
                        winreg.DeleteValue(key, RUN_VALUE)
                    except FileNotFoundError:
                        pass
            finally:
                winreg.CloseKey(key)
        except Exception as exc:
            self.panel.updated.setText(f"开机启动设置失败: {exc}")
            return
        if self.autostart_action is not None:
            self.autostart_action.blockSignals(True)
            self.autostart_action.setChecked(enabled)
            self.autostart_action.blockSignals(False)
        popup = getattr(self, "_settings_popup", None)
        if popup is not None:
            popup.set_autostart(enabled)

    def _is_autostart_enabled(self) -> bool:
        try:
            import winreg
            key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ)
            try:
                winreg.QueryValueEx(key, RUN_VALUE)
                return True
            finally:
                winreg.CloseKey(key)
        except Exception:
            return False

    def _launch_command(self) -> str:
        if getattr(sys, "frozen", False):
            vbs = Path(sys.executable).with_name("launch-hidden.vbs")
            if vbs.exists():
                return f'"C:\Windows\System32\wscript.exe" "{vbs}"'
            return f'"{sys.executable}"'
        pythonw = Path(sys.executable).with_name("pythonw.exe")
        exe = pythonw if pythonw.exists() else Path(sys.executable)
        return f'"{exe}" "{Path(__file__).resolve()}"'

    def closeEvent(self, event) -> None:
        if self._quitting:
            event.accept()
            return
        self.hide()
        event.ignore()

    def quit_app(self) -> None:
        self._quitting = True
        self._save_position()
        if self.tray is not None:
            self.tray.hide()
        if self._thread is not None and self._thread.isRunning():
            self._thread.wait(3000)
        QApplication.quit()

    def _update_tray_actions(self) -> None:
        if self.show_action is not None:
            self.show_action.setText("隐藏挂件" if self.isVisible() else "显示挂件")

    def show_settings_menu(self, global_pos: QPoint) -> None:
        """Open the rounded settings card near the avatar."""
        if self._settings_popup is not None:
            self._settings_popup.close()
            self._settings_popup = None
        popup = SettingsPopup(self.size_percent, self._is_autostart_enabled())
        popup.size_changed.connect(self.set_size_percent)
        popup.autostart_changed.connect(self.set_autostart)
        popup.hide_requested.connect(self.toggle_visibility)
        popup.refresh_requested.connect(self.refresh)
        popup.quit_requested.connect(self.quit_app)
        popup.closed.connect(lambda p=popup: self._on_settings_closed(p))
        self._settings_popup = popup
        popup.show_at(global_pos)

    def _on_settings_closed(self, popup: SettingsPopup) -> None:
        if self._settings_popup is popup:
            self._settings_popup = None

    def setup_tray(self) -> None:
        self.tray = QSystemTrayIcon(QIcon(str(resource_path("assets/app.ico"))), self)
        self.tray.setToolTip(APP_NAME)
        menu = QMenu()
        self.show_action = QAction("隐藏挂件", self)
        self.show_action.triggered.connect(self.toggle_visibility)
        refresh_action = QAction("立即刷新", self)
        refresh_action.triggered.connect(self.refresh)

        settings_action = QAction("设置...", self)
        settings_action.triggered.connect(lambda: self.show_settings_menu(QCursor.pos()))
        menu.addAction(self.show_action)
        menu.addAction(refresh_action)
        menu.addAction(settings_action)
        menu.addSeparator()

        self.autostart_action = QAction("开机启动", self)
        self.autostart_action.setCheckable(True)
        self.autostart_action.setChecked(self._is_autostart_enabled())
        self.autostart_action.toggled.connect(self.set_autostart)
        quit_action = QAction("退出", self)
        quit_action.triggered.connect(self.quit_app)

        menu.addAction(self.autostart_action)
        menu.addSeparator()
        menu.addAction(quit_action)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(self._tray_activated)
        self.tray.show()
        self._update_tray_actions()

    def _tray_activated(self, reason) -> None:
        if reason in (QSystemTrayIcon.Trigger, QSystemTrayIcon.DoubleClick):
            self.toggle_visibility()

    show_action = None
    autostart_action = None
    tray = None
    size_slider = None
    size_value_label = None


class SingleInstance:
    def __init__(self, key: str):
        self.key = key
        self.server = None

    def notify_existing(self) -> bool:
        trace("single-instance: trying existing server")
        socket = QLocalSocket()
        socket.connectToServer(self.key)
        if socket.waitForConnected(300):
            trace("single-instance: existing server responded")
            socket.write(b"show")
            socket.flush()
            socket.waitForBytesWritten(300)
            socket.disconnectFromServer()
            return True
        return False

    def listen(self, on_show) -> bool:
        trace("single-instance: listening")
        self.server = QLocalServer()
        QLocalServer.removeServer(self.key)
        if not self.server.listen(self.key):
            return False

        def handle_connections():
            while self.server.hasPendingConnections():
                socket = self.server.nextPendingConnection()
                socket.readyRead.connect(lambda s=socket: self._read(s, on_show))
                socket.disconnected.connect(socket.deleteLater)

        self.server.newConnection.connect(handle_connections)
        return True

    @staticmethod
    def _read(socket, on_show) -> None:
        data = bytes(socket.readAll())
        if b"show" in data:
            on_show()
        socket.disconnectFromServer()


def main() -> int:
    hide_console()
    trace("main: enter")
    QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
    QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
    sys.excepthook = log_exception
    app = QApplication(sys.argv)
    trace("main: QApplication created")
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_NAME)
    app.setFont(QFont("Microsoft YaHei UI", 9))
    app.setOrganizationName("local")
    app.setWindowIcon(QIcon(str(resource_path("assets/app.ico"))))
    app.setStyle("Fusion")

    instance = SingleInstance(SINGLE_KEY)
    if instance.notify_existing():
        trace("main: exiting because existing instance")
        return 0

    trace("main: creating widget")
    window = BatteryWidget()
    window.setup_tray()

    def show_window() -> None:
        window.show()
        window.raise_()
        if window._collapsed:
            window.expand_from_edge()

    instance.listen(show_window)
    app._single_instance = instance
    app._battery_widget = window
    window.show()
    window.raise_()
    trace("main: event loop")
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())
