"""Painted cluster widgets: dial gauge, steering wheel, car top view (doors + tires)."""

from __future__ import annotations

import math
from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QSizePolicy, QWidget

BG = QColor("#101418")
FG = QColor("#e8eef2")
DIM = QColor("#56616b")
ACCENT = QColor("#ff6a00")
STALE = QColor("#6b6b6b")


def _pen(color: QColor, width: float, cap=Qt.SquareCap) -> QPen:
    pen = QPen(color, width)
    pen.setCapStyle(cap)
    return pen


class Gauge(QWidget):
    """Analog dial. ``value=None`` shows no needle and a 'no data' caption."""

    def __init__(self, title: str, lo: float, hi: float, major: float, minor: float = 0, unit: str = "",
                 redline: Optional[float] = None, label_scale: float = 1.0, label_fmt: str = "{:.0f}",
                 sweep: float = 240.0, parent=None):
        super().__init__(parent)
        self.title, self.lo, self.hi, self.major, self.minor = title, lo, hi, major, minor
        self.unit, self.redline, self.label_scale, self.label_fmt = unit, redline, label_scale, label_fmt
        self.sweep = sweep
        self.value: Optional[float] = None
        self.stale = False
        self.caption = ""
        self.end_labels: Optional[tuple[str, str]] = None
        self.setMinimumSize(160, 160)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_value(self, v: Optional[float], stale: bool = False, caption: str = "") -> None:
        self.value, self.stale, self.caption = v, stale, caption
        self.update()

    def _angle(self, v: float) -> float:
        frac = (min(max(v, self.lo), self.hi) - self.lo) / (self.hi - self.lo)
        start = 90 + self.sweep / 2
        return math.radians(start - frac * self.sweep)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        side = min(self.width(), self.height())
        p.translate(self.width() / 2, self.height() / 2)
        p.scale(side / 220.0, side / 220.0)
        p.setPen(Qt.NoPen)
        p.setBrush(BG)
        p.drawEllipse(QPointF(0, 0), 105, 105)
        p.setPen(QPen(QColor("#2a3540"), 3))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(0, 0), 104, 104)
        if self.redline is not None:
            p.setPen(_pen(QColor("#c62828"), 7, Qt.FlatCap))
            a0 = math.degrees(self._angle(self.redline))
            a1 = math.degrees(self._angle(self.hi))
            p.drawArc(QRectF(-88, -88, 176, 176), int(a1 * 16), int((a0 - a1) * 16))
        # ticks
        if self.minor:
            n = int(round((self.hi - self.lo) / self.minor))
            p.setPen(QPen(DIM, 1.5))
            for i in range(n + 1):
                a = self._angle(self.lo + i * self.minor)
                p.drawLine(QPointF(92 * math.cos(a), -92 * math.sin(a)), QPointF(86 * math.cos(a), -86 * math.sin(a)))
        n = int(round((self.hi - self.lo) / self.major))
        f = QFont()
        f.setPointSizeF(10)
        f.setBold(True)
        p.setFont(f)
        for i in range(n + 1):
            v = self.lo + i * self.major
            a = self._angle(v)
            p.setPen(QPen(FG, 3))
            p.drawLine(QPointF(94 * math.cos(a), -94 * math.sin(a)), QPointF(80 * math.cos(a), -80 * math.sin(a)))
            txt = self.label_fmt.format(v * self.label_scale)
            r = QRectF(66 * math.cos(a) - 18, -66 * math.sin(a) - 9, 36, 18)
            p.drawText(r, Qt.AlignCenter, txt)
        if self.end_labels:
            p.setPen(FG)
            for v, txt in ((self.lo, self.end_labels[0]), (self.hi, self.end_labels[1])):
                a = self._angle(v)
                p.drawText(QRectF(60 * math.cos(a) - 16, -60 * math.sin(a) - 9, 32, 18), Qt.AlignCenter, txt)
        # titles
        f.setPointSizeF(8)
        f.setBold(False)
        p.setFont(f)
        p.setPen(DIM)
        p.drawText(QRectF(-60, 28, 120, 16), Qt.AlignCenter, self.title)
        p.drawText(QRectF(-60, -48, 120, 14), Qt.AlignCenter, self.unit)
        f.setPointSizeF(14)
        f.setBold(True)
        p.setFont(f)
        if self.value is None:
            p.setPen(STALE)
            p.drawText(QRectF(-60, 44, 120, 24), Qt.AlignCenter, "no data")
        else:
            p.setPen(STALE if self.stale else FG)
            p.drawText(QRectF(-70, 44, 140, 24), Qt.AlignCenter, self.caption or self.label_fmt.format(self.value))
            a = self._angle(self.value)
            p.setPen(_pen(STALE if self.stale else ACCENT, 4, Qt.RoundCap))
            p.drawLine(QPointF(-12 * math.cos(a), 12 * math.sin(a)), QPointF(88 * math.cos(a), -88 * math.sin(a)))
        p.setPen(Qt.NoPen)
        p.setBrush(QColor("#30363c"))
        p.drawEllipse(QPointF(0, 0), 9, 9)
        if self.stale and self.value is not None:
            f.setPointSizeF(8)
            p.setFont(f)
            p.setPen(QColor("#ffb300"))
            p.drawText(QRectF(-40, 70, 80, 14), Qt.AlignCenter, "STALE")


class SteeringWheel(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.angle: Optional[float] = None
        self.stale = False
        self.setMinimumSize(90, 90)

    def set_angle(self, a: Optional[float], stale: bool = False) -> None:
        self.angle, self.stale = a, stale
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        side = min(self.width(), self.height())
        p.translate(self.width() / 2, self.height() / 2)
        p.scale(side / 100.0, side / 100.0)
        color = STALE if (self.angle is None or self.stale) else FG
        p.rotate(self.angle or 0.0)
        p.setPen(QPen(color, 7))
        p.setBrush(Qt.NoBrush)
        p.drawEllipse(QPointF(0, 0), 40, 40)
        p.setPen(QPen(color, 6))
        p.drawLine(QPointF(-38, 4), QPointF(-10, 8))
        p.drawLine(QPointF(38, 4), QPointF(10, 8))
        p.drawLine(QPointF(0, 14), QPointF(0, 38))
        p.setBrush(color)
        p.drawEllipse(QPointF(0, 6), 11, 9)
        p.setPen(QPen(ACCENT, 5))
        p.drawLine(QPointF(0, -44), QPointF(0, -36))


class CarTopView(QWidget):
    """Doors (open = red) and tires (pressure text; warning yellow, flat red, invalid grey)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.doors: dict[str, Optional[bool]] = {}
        self.tires: dict[str, dict] = {}
        self.setMinimumSize(170, 230)

    def set_state(self, doors: dict, tires: dict) -> None:
        self.doors, self.tires = doors, tires
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        s = min(w / 170.0, h / 230.0)
        p.translate((w - 170 * s) / 2, (h - 230 * s) / 2)
        p.scale(s, s)
        body = QPainterPath()
        body.addRoundedRect(QRectF(50, 20, 70, 190), 22, 22)
        p.setPen(QPen(DIM, 2))
        p.setBrush(QColor("#1b232b"))
        p.drawPath(body)
        p.setBrush(QColor("#2b3a48"))
        p.drawRoundedRect(QRectF(58, 60, 54, 30), 6, 6)
        p.drawRoundedRect(QRectF(58, 150, 54, 22), 6, 6)

        def door(name, rect):
            st = self.doors.get(name)
            col = QColor("#555") if st is None else (QColor("#e53935") if st else QColor("#2e7d32"))
            p.setPen(QPen(col, 4))
            p.drawLine(rect[0], rect[1])
        door("Driver", (QPointF(50, 95), QPointF(50, 128)))
        door("Passenger", (QPointF(120, 95), QPointF(120, 128)))
        door("Rear driver", (QPointF(50, 132), QPointF(50, 160)))
        door("Rear passenger", (QPointF(120, 132), QPointF(120, 160)))
        door("Trunk", (QPointF(65, 210), QPointF(105, 210)))
        f = QFont()
        f.setPointSizeF(8)
        p.setFont(f)
        for pos, (x, y) in {"FL": (8, 40), "FR": (124, 40), "RL": (8, 150), "RR": (124, 150)}.items():
            t = self.tires.get(pos, {})
            psi = t.get("psi")
            col = QColor("#555")
            if psi is not None and t.get("valid", True):
                col = QColor("#2e7d32")
            if t.get("warning"):
                col = QColor("#f9a825")
            if t.get("flat"):
                col = QColor("#e53935")
            p.setPen(QPen(col, 2))
            p.setBrush(QColor(col).darker(300))
            p.drawRoundedRect(QRectF(x + 26 if pos[1] == "L" else x - 6, y, 16, 34), 4, 4)
            p.setPen(FG)
            txt = "—" if psi is None else f"{psi:.1f}"
            p.drawText(QRectF(x - 6 if pos[1] == "L" else x + 6, y + 36, 40, 14), Qt.AlignCenter, txt)
            p.drawText(QRectF(x - 6 if pos[1] == "L" else x + 6, y - 14, 40, 14), Qt.AlignCenter, pos)


class Bar(QWidget):
    """Thin horizontal progress bar (0-100). Painted directly; safe inside styled parents."""

    def __init__(self, color: str = "#4fc3f7", parent=None):
        super().__init__(parent)
        self.color = QColor(color)
        self.value = 0
        self.setFixedHeight(10)

    def set_value(self, v: int) -> None:
        v = max(0, min(100, int(v)))
        if v != self.value:
            self.value = v
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#263038"))
        w = int(self.width() * self.value / 100)
        if w:
            p.fillRect(0, 0, w, self.height(), self.color)
