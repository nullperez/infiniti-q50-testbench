"""Painted centre display (the meter's LCD): one message at a time, rotating when several are active."""

from __future__ import annotations

import time

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QPainter, QPen, QPolygonF
from PySide6.QtWidgets import QSizePolicy, QWidget

from q50tb.signals.dashboard import LcdMessage
from q50tb.ui.widgets.telltales import ICONS

BG = QColor("#05080b")
BORDER = QColor("#2a3540")
BODY = QColor("#9aa5ae")
DIM = QColor("#56616b")
ROTATE_S = 3.0

# open-door line (hinge, free end) on a 100x100 top view, front at the top, driver on the left
_DOOR_LINES = {
    "Driver": ((32, 34), (14, 50)), "Rear driver": ((32, 56), (14, 72)),
    "Passenger": ((68, 34), (86, 50)), "Rear passenger": ((68, 56), (86, 72)),
}


def _warning(p: QPainter, color: QColor) -> None:
    pen = QPen(color, 6)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(color)
    p.drawPolygon(QPolygonF([QPointF(50, 10), QPointF(92, 86), QPointF(8, 86)]))
    ink = QPen(BG, 8)
    ink.setCapStyle(Qt.RoundCap)
    p.setPen(ink)
    p.drawLine(QPointF(50, 38), QPointF(50, 60))
    p.setPen(Qt.NoPen)
    p.setBrush(BG)
    p.drawEllipse(QPointF(50, 73), 4.5, 4.5)


def _doors(p: QPainter, color: QColor, open_doors: tuple) -> None:
    pen = QPen(BODY, 4)
    pen.setJoinStyle(Qt.RoundJoin)
    p.setPen(pen)
    p.setBrush(Qt.NoBrush)
    p.drawRoundedRect(QRectF(32, 8, 36, 84), 12, 12)
    p.drawLine(QPointF(36, 30), QPointF(64, 30))   # windscreen
    p.drawLine(QPointF(37, 76), QPointF(63, 76))   # rear window
    door = QPen(color, 6)
    door.setCapStyle(Qt.RoundCap)
    p.setPen(door)
    for name in open_doors:
        if name in _DOOR_LINES:
            a, b = _DOOR_LINES[name]
            p.drawLine(QPointF(*a), QPointF(*b))
        elif name == "Trunk":
            p.drawLine(QPointF(36, 98), QPointF(64, 98))
            p.drawLine(QPointF(36, 98), QPointF(36, 92))
            p.drawLine(QPointF(64, 98), QPointF(64, 92))


class LcdDisplay(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.messages: list[LcdMessage] = []
        self.index = 0
        self._since = time.monotonic()
        self.setMinimumSize(260, 170)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)

    def set_messages(self, messages: list[LcdMessage]) -> None:
        now = time.monotonic()
        if messages != self.messages:
            current = self.messages[self.index] if self.messages else None
            self.messages = list(messages)
            # keep showing the same message if it is still active, else start over
            self.index = self.messages.index(current) if current in self.messages else 0
            self._since = now
            self.setToolTip("\n".join(f"{m.text} — {m.source}" for m in self.messages) or "no messages")
            self.update()
        elif len(self.messages) > 1 and now - self._since >= ROTATE_S:
            self.index = (self.index + 1) % len(self.messages)
            self._since = now
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        w, h = self.width(), self.height()
        p.setPen(QPen(BORDER, 2))
        p.setBrush(BG)
        p.drawRoundedRect(QRectF(1, 1, w - 2, h - 2), 10, 10)
        f = QFont()
        if not self.messages:
            f.setPixelSize(max(11, int(h * 0.07)))
            p.setFont(f)
            p.setPen(DIM)
            p.drawText(QRectF(0, 0, w, h), Qt.AlignCenter, "No messages")
            return
        m = self.messages[self.index]
        color = QColor(m.color)
        icon = min(h * 0.38, w * 0.4)
        p.save()
        p.translate((w - icon) / 2, h * 0.07)
        p.scale(icon / 100.0, icon / 100.0)
        if m.icon == "doors":
            _doors(p, color, m.doors)
        elif m.icon in ICONS:
            pen = QPen(color, 6)
            pen.setCapStyle(Qt.RoundCap)
            pen.setJoinStyle(Qt.RoundJoin)
            p.setPen(pen)
            p.setBrush(color)
            ICONS[m.icon](p)
        else:
            _warning(p, color)
        p.restore()
        y = h * 0.07 + icon + h * 0.04
        f.setPixelSize(max(12, int(h * 0.1)))
        f.setBold(True)
        p.setFont(f)
        p.setPen(color)
        title = QRectF(10, y, w - 20, h * 0.26)
        used = p.boundingRect(title, Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, m.text)
        p.drawText(title, Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, m.text)
        if m.sub:
            f.setPixelSize(max(11, int(h * 0.075)))
            f.setBold(False)
            p.setFont(f)
            p.setPen(QColor("#e8eef2"))
            p.drawText(QRectF(10, used.bottom() + 2, w - 20, h * 0.18),
                       Qt.AlignHCenter | Qt.AlignTop | Qt.TextWordWrap, m.sub)
        n = len(self.messages)
        if n > 1:
            r, gap = 3.5, 12
            x0 = w / 2 - (n - 1) * gap / 2
            p.setPen(Qt.NoPen)
            for i in range(n):
                p.setBrush(QColor("#e8eef2") if i == self.index else DIM)
                p.drawEllipse(QPointF(x0 + i * gap, h - 10), r, r)
