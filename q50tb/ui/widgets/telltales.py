"""Painted cluster telltale icons (drawn on a 100x100 grid, scaled to the widget).

Lit = the telltale's colour, off = dark grey, no data / stale = darker grey.
"""

from __future__ import annotations

import math
import time
from typing import Callable, Optional

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

OFF = QColor("#3c434a")
NO_DATA = QColor("#22272c")
BLINK_HZ = 1.5


def _poly(*pts) -> QPolygonF:
    return QPolygonF([QPointF(x, y) for x, y in pts])


def _lamp(p: QPainter, facing_left: bool, fill: bool = True) -> None:
    """Headlamp 'D': flat lens edge towards the beams."""
    path = QPainterPath()
    if facing_left:
        path.moveTo(58, 22)
        path.cubicTo(98, 22, 98, 78, 58, 78)
    else:
        path.moveTo(42, 22)
        path.cubicTo(2, 22, 2, 78, 42, 78)
    path.closeSubpath()
    if not fill:
        p.setBrush(Qt.NoBrush)
    p.drawPath(path)


def _wave(p: QPainter, x: float) -> None:
    path = QPainterPath(QPointF(x, 16))
    path.cubicTo(x - 10, 34, x + 10, 50, x, 66)
    path.cubicTo(x - 6, 76, x + 4, 82, x, 88)
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)


def _brackets(p: QPainter, text: str, h: float, pen: float) -> None:
    """The ( O ) family: circle between two arcs, text inside."""
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QPointF(50, 50), 30, 30)
    r = QRectF(6, 10, 88, 80)
    p.drawArc(r, 125 * 16, 110 * 16)
    p.drawArc(r, -55 * 16, 110 * 16)
    _text(p, 50, 50, text, h, pen)


def _glyph(p: QPainter, ch: str, x: float, y: float, w: float, h: float) -> None:
    """Stroked capital letter in the box (x, y, w, h); no fonts needed."""
    path = QPainterPath()
    if ch in "EF":
        path.moveTo(x + w, y)
        path.lineTo(x, y)
        path.lineTo(x, y + h)
        if ch == "E":
            path.lineTo(x + w, y + h)
        path.moveTo(x, y + h / 2)
        path.lineTo(x + w * 0.8, y + h / 2)
    elif ch == "C":
        path.arcMoveTo(QRectF(x, y, w, h), 45)
        path.arcTo(QRectF(x, y, w, h), 45, 270)
    elif ch == "O":
        path.addEllipse(QRectF(x, y, w, h))
    elif ch == "A":
        path.moveTo(x, y + h)
        path.lineTo(x + w / 2, y)
        path.lineTo(x + w, y + h)
        path.moveTo(x + w * 0.2, y + h * 0.62)
        path.lineTo(x + w * 0.8, y + h * 0.62)
    elif ch in "BP":
        path.moveTo(x, y + h)
        path.lineTo(x, y)
        path.lineTo(x + w * 0.5, y)
        mid = h * (0.5 if ch == "B" else 0.55)
        path.cubicTo(x + w * 1.05, y, x + w * 1.05, y + mid, x + w * 0.5, y + mid)
        path.lineTo(x, y + mid)
        if ch == "B":
            path.moveTo(x + w * 0.5, y + mid)
            path.cubicTo(x + w * 1.1, y + mid, x + w * 1.1, y + h, x + w * 0.5, y + h)
            path.lineTo(x, y + h)
    elif ch == "S":
        path.moveTo(x + w * 0.95, y + h * 0.15)
        path.cubicTo(x + w * 0.8, y - h * 0.05, x, y, x, y + h * 0.27)
        path.cubicTo(x, y + h * 0.5, x + w, y + h * 0.45, x + w, y + h * 0.73)
        path.cubicTo(x + w, y + h * 1.0, x + w * 0.2, y + h * 1.05, x, y + h * 0.85)
    elif ch == "!":
        path.moveTo(x + w / 2, y)
        path.lineTo(x + w / 2, y + h * 0.66)
    else:
        raise ValueError(f"no glyph for {ch!r}")
    p.save()
    p.setBrush(Qt.NoBrush)
    p.drawPath(path)
    if ch == "!":
        p.setBrush(p.pen().color())
        p.drawEllipse(QPointF(x + w / 2, y + h * 0.92), h * 0.07, h * 0.07)
    p.restore()


def _text(p: QPainter, cx: float, cy: float, text: str, h: float, pen: float = 6) -> None:
    """Stroked text centred on (cx, cy) with letter height h."""
    w, gap = h * 0.62, h * 0.22
    x = cx - (len(text) * w + (len(text) - 1) * gap) / 2
    p.save()
    q = QPen(p.pen())
    q.setWidthF(pen)
    p.setPen(q)
    for ch in text:
        _glyph(p, ch, x, cy - h / 2, w, h)
        x += w + gap
    p.restore()


def _turn(p: QPainter, left: bool) -> None:
    pts = [(8, 50), (44, 16), (44, 36), (92, 36), (92, 64), (44, 64), (44, 84)]
    p.drawPolygon(_poly(*[(x if left else 100 - x, y) for x, y in pts]))


def _high_beam(p: QPainter) -> None:
    _lamp(p, True)
    for y in (28, 42, 58, 72):
        p.drawLine(QPointF(10, y), QPointF(48, y))


def _headlights(p: QPainter) -> None:
    _lamp(p, True)
    for y in (26, 42, 58, 74):
        p.drawLine(QPointF(48, y), QPointF(12, y + 12))


def _parking(p: QPainter) -> None:
    for sign in (-1, 1):
        flat = 50 + sign * 16
        path = QPainterPath(QPointF(flat, 32))
        path.cubicTo(50 - sign * 4, 32, 50 - sign * 4, 68, flat, 68)
        path.closeSubpath()
        p.drawPath(path)
        for y in (34, 50, 66):
            p.drawLine(QPointF(50 + sign * 24, y), QPointF(50 + sign * 44, y))


def _fog(p: QPainter) -> None:
    _lamp(p, True)
    for y in (28, 46, 64):
        p.drawLine(QPointF(48, y), QPointF(10, y + 10))
    _wave(p, 30)


def _rear_fog(p: QPainter) -> None:
    _lamp(p, False)
    for y in (30, 44, 58, 72):
        p.drawLine(QPointF(52, y), QPointF(92, y))
    _wave(p, 72)


def _auto_high_beam(p: QPainter) -> None:
    _lamp(p, True, fill=False)
    for y in (28, 42, 58, 72):
        p.drawLine(QPointF(10, y), QPointF(44, y))
    _text(p, 72, 50, "A", 30, 5)


def _eco(p: QPainter) -> None:
    _text(p, 50, 50, "ECO", 34, 7)


def _engine(p: QPainter) -> None:
    p.setBrush(Qt.NoBrush)
    p.drawPolygon(_poly((24, 40), (34, 40), (40, 34), (66, 34), (70, 40), (78, 40), (78, 48), (86, 48), (86, 40),
                        (92, 40), (92, 76), (86, 76), (86, 66), (78, 66), (78, 74), (42, 74), (34, 66), (24, 66)))
    p.drawLine(QPointF(24, 53), QPointF(12, 53))
    p.drawLine(QPointF(10, 42), QPointF(10, 64))
    p.drawLine(QPointF(46, 34), QPointF(46, 24))
    p.drawLine(QPointF(60, 34), QPointF(60, 24))
    p.drawLine(QPointF(38, 24), QPointF(68, 24))


def _battery(p: QPainter) -> None:
    p.setBrush(Qt.NoBrush)
    p.drawRect(QRectF(12, 32, 76, 48))
    p.setBrush(p.pen().color())
    p.drawRect(QRectF(22, 22, 14, 8))
    p.drawRect(QRectF(64, 22, 14, 8))
    p.drawLine(QPointF(24, 54), QPointF(40, 54))
    p.drawLine(QPointF(60, 54), QPointF(76, 54))
    p.drawLine(QPointF(68, 46), QPointF(68, 62))


def _airbag(p: QPainter) -> None:
    c = p.pen().color()
    p.setBrush(c)
    p.drawEllipse(QPointF(30, 20), 9, 9)
    p.setBrush(Qt.NoBrush)
    p.drawPolyline(_poly((28, 34), (36, 64), (62, 64), (64, 88)))
    p.drawLine(QPointF(34, 46), QPointF(52, 52))
    p.setBrush(c)
    p.drawEllipse(QPointF(70, 34), 16, 16)


def _seat_belt(p: QPainter) -> None:
    c = p.pen().color()
    p.setBrush(c)
    p.drawEllipse(QPointF(50, 17), 10, 10)
    p.setBrush(Qt.NoBrush)
    body = QPainterPath(QPointF(28, 90))
    body.lineTo(28, 50)
    body.quadTo(28, 32, 50, 32)
    body.quadTo(72, 32, 72, 50)
    body.lineTo(72, 90)
    p.drawPath(body)
    p.drawLine(QPointF(66, 36), QPointF(38, 76))
    p.setBrush(c)
    p.drawRect(QRectF(32, 74, 12, 10))


def _car_tracks(p: QPainter) -> None:
    p.setBrush(Qt.NoBrush)
    body = QPainterPath(QPointF(20, 60))
    body.lineTo(20, 46)
    body.lineTo(32, 26)
    body.lineTo(68, 26)
    body.lineTo(80, 46)
    body.lineTo(80, 60)
    body.closeSubpath()
    p.drawPath(body)
    p.drawLine(QPointF(20, 46), QPointF(80, 46))
    p.setBrush(p.pen().color())
    p.drawRect(QRectF(22, 60, 12, 8))
    p.drawRect(QRectF(66, 60, 12, 8))
    p.setBrush(Qt.NoBrush)
    for x, d in ((30, 1), (70, -1)):
        s = QPainterPath(QPointF(x - 4 * d, 96))
        s.cubicTo(x - 14 * d, 88, x + 10 * d, 82, x, 74)
        p.drawPath(s)


def _slip(p: QPainter) -> None:
    _car_tracks(p)


def _tcs_off(p: QPainter) -> None:
    p.save()
    p.translate(18, 0)
    p.scale(0.64, 0.64)
    _car_tracks(p)
    p.restore()
    _text(p, 50, 84, "OFF", 20, 5)


def _tpms(p: QPainter) -> None:
    p.setBrush(Qt.NoBrush)
    cx, cy, r = 50, 52, 36
    p.drawArc(QRectF(cx - r, cy - r, 2 * r, 2 * r), 125 * 16, 290 * 16)
    for x in (32, 44, 56, 68):
        y = cy + math.sqrt(r * r - (x - cx) ** 2)
        p.drawLine(QPointF(x, y), QPointF(x, y + 7))
    p.drawLine(QPointF(50, 32), QPointF(50, 58))
    p.setBrush(p.pen().color())
    p.drawEllipse(QPointF(50, 69), 3.5, 3.5)


def _oil(p: QPainter) -> None:
    p.setBrush(Qt.NoBrush)
    p.drawPolygon(_poly((30, 44), (58, 44), (68, 52), (90, 42), (70, 72), (30, 72)))
    p.drawPolyline(_poly((30, 50), (14, 44), (12, 58), (30, 64)))
    p.drawLine(QPointF(42, 44), QPointF(42, 36))
    p.drawLine(QPointF(34, 36), QPointF(50, 36))
    p.setBrush(p.pen().color())
    p.drawEllipse(QPointF(90, 62), 3, 4)


def _fuel(p: QPainter) -> None:
    p.setBrush(Qt.NoBrush)
    p.drawRect(QRectF(22, 20, 36, 64))
    p.drawLine(QPointF(14, 84), QPointF(66, 84))
    p.drawPolyline(_poly((58, 42), (70, 42), (76, 50), (76, 70), (84, 70), (84, 32), (74, 22)))
    p.setBrush(p.pen().color())
    p.drawRect(QRectF(28, 28, 24, 16))


def _power_steering(p: QPainter) -> None:
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QPointF(42, 50), 30, 30)
    p.drawLine(QPointF(14, 46), QPointF(34, 50))
    p.drawLine(QPointF(70, 46), QPointF(50, 50))
    p.drawLine(QPointF(42, 58), QPointF(42, 80))
    p.drawEllipse(QPointF(42, 52), 8, 6)
    p.drawLine(QPointF(88, 24), QPointF(88, 62))
    p.setBrush(p.pen().color())
    p.drawEllipse(QPointF(88, 74), 3.5, 3.5)


def _feb(p: QPainter) -> None:
    """Rear of a car (left) and front of a car (right) closing in, '!' and impact burst between them."""
    p.setBrush(Qt.NoBrush)
    for m in (1, -1):  # m = -1 mirrors about x = 50
        def X(x: float) -> float:
            return x if m == 1 else 100 - x
        body = QPainterPath(QPointF(X(4), 44))
        body.cubicTo(X(22), 40, X(32), 46, X(38), 58)
        body.lineTo(X(40), 72)
        body.lineTo(X(24), 72)
        p.drawPath(body)
        p.drawEllipse(QPointF(X(16), 72), 7, 7)
        p.drawLine(QPointF(X(9), 72), QPointF(X(4), 72))
        q = QPen(p.pen())
        q.setWidthF(4)
        p.save()
        p.setPen(q)
        p.drawLine(QPointF(X(40), 34), QPointF(X(32), 27))
        p.drawLine(QPointF(X(43), 22), QPointF(X(39), 12))
        p.restore()
    p.drawLine(QPointF(50, 22), QPointF(50, 50))
    p.setBrush(p.pen().color())
    p.drawEllipse(QPointF(50, 61), 3.5, 3.5)


def _key(p: QPainter) -> None:
    p.setBrush(Qt.NoBrush)
    p.drawEllipse(QPointF(28, 50), 15, 15)
    p.drawLine(QPointF(43, 50), QPointF(90, 50))
    p.drawLine(QPointF(74, 50), QPointF(74, 64))
    p.drawLine(QPointF(86, 50), QPointF(86, 60))


ICONS: dict[str, Callable[[QPainter], None]] = {
    "left_turn": lambda p: _turn(p, True),
    "right_turn": lambda p: _turn(p, False),
    "high_beam": _high_beam,
    "headlights": _headlights,
    "parking_lights": _parking,
    "fog": _fog,
    "rear_fog": _rear_fog,
    "auto_high_beam": _auto_high_beam,
    "eco": _eco,
    "parking_brake": lambda p: _brackets(p, "P", 30, 7),
    "abs": lambda p: _brackets(p, "ABS", 18, 4.5),
    "slip": _slip,
    "tcs_off": _tcs_off,
    "tpms": _tpms,
    "mil": _engine,
    "oil": _oil,
    "charge": _battery,
    "airbag": _airbag,
    "seat_belt": _seat_belt,
    "low_fuel": _fuel,
    "power_steering": _power_steering,
    "key": _key,
    "feb": _feb,
}


class TelltaleIcon(QWidget):
    """One telltale: ``set_state(None)`` = no data, False = off, True = lit (optionally blinking)."""

    def __init__(self, icon: str, color: str, label: str = "", size: int = 40, parent=None):
        super().__init__(parent)
        self.draw = ICONS[icon]
        self.color = QColor(color)
        self.label = label
        self.state: Optional[bool] = None
        self.blinking = False
        self.setFixedSize(size, size)
        self.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self._tooltip()

    def _tooltip(self) -> None:
        st = {None: "no data", False: "off", True: "BLINKING" if self.blinking else "ON"}[self.state]
        self.setToolTip(f"{self.label}: {st}")

    def set_state(self, on: Optional[bool], blinking: bool = False) -> None:
        if (on, blinking) != (self.state, self.blinking):
            self.state, self.blinking = on, blinking
            self._tooltip()
            self.update()
        elif blinking:
            self.update()

    def current_color(self) -> QColor:
        if self.state is None:
            return NO_DATA
        if not self.state or (self.blinking and int(time.monotonic() * BLINK_HZ * 2) % 2):
            return OFF
        return self.color

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        s = min(self.width(), self.height()) / 100.0
        p.scale(s, s)
        c = self.current_color()
        pen = QPen(c, 6)
        pen.setCapStyle(Qt.RoundCap)
        pen.setJoinStyle(Qt.RoundJoin)
        p.setPen(pen)
        p.setBrush(c)
        self.draw(p)
