"""Small shared widgets and helpers for the pages."""

from __future__ import annotations

from typing import Callable, Optional

from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtGui import QColor, QFont, QPainter
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFrame, QHBoxLayout, QLabel,
                               QMessageBox, QPushButton, QSizePolicy, QSpinBox, QWidget)

CONF_COLORS = {"confirmed": "#2e7d32", "documented": "#1565c0", "probable": "#6a1b9a", "candidate": "#ef6c00",
               "observed": "#00838f"}


class Bridge(QObject):
    """Thread-safe 'something changed' notifications from core listeners to the GUI thread."""
    changed = Signal(str)


def confidence_badge(conf: str) -> QLabel:
    lab = QLabel(conf)
    color = CONF_COLORS.get(conf, "#555")
    lab.setStyleSheet(f"QLabel {{ color: white; background: {color}; border-radius: 6px; padding: 0px 5px; "
                      f"font-size: 9px; }}")
    lab.setToolTip(f"Evidence level: {conf}")
    lab.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
    return lab


def banner(text: str, color: str = "#b71c1c") -> QLabel:
    lab = QLabel(text)
    lab.setWordWrap(True)
    lab.setStyleSheet(f"QLabel {{ background: {color}; color: white; padding: 6px; font-weight: bold; "
                      f"border-radius: 4px; }}")
    return lab


def hline() -> QFrame:
    f = QFrame()
    f.setFrameShape(QFrame.HLine)
    f.setFrameShadow(QFrame.Sunken)
    return f


def error_box(parent, title: str, text: str) -> None:
    QMessageBox.critical(parent, title, text)


def info_box(parent, title: str, text: str) -> None:
    QMessageBox.information(parent, title, text)


def confirm(parent, title: str, text: str, danger: bool = False) -> bool:
    box = QMessageBox(QMessageBox.Warning if danger else QMessageBox.Question, title, text,
                      QMessageBox.Yes | QMessageBox.No, parent)
    box.setDefaultButton(QMessageBox.No)
    return box.exec() == QMessageBox.Yes


class Led(QWidget):
    """Round indicator: on / off / unknown(stale)."""

    def __init__(self, color: str = "#20d020", size: int = 14, parent=None):
        super().__init__(parent)
        self.color = QColor(color)
        self.state: Optional[bool] = None
        self.setFixedSize(size, size)

    def set_state(self, on: Optional[bool]) -> None:
        if on != self.state:
            self.state = on
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        if self.state is None:
            c = QColor("#555555")
        elif self.state:
            c = self.color
        else:
            c = QColor(self.color).darker(450)
        p.setBrush(c)
        p.setPen(QColor("#222"))
        p.drawEllipse(1, 1, self.width() - 2, self.height() - 2)


def spin(lo: float, hi: float, value: float = 0, step: float = 1, decimals: int = 0, suffix: str = "",
         on_change: Optional[Callable] = None):
    if decimals:
        w = QDoubleSpinBox()
        w.setDecimals(decimals)
    else:
        w = QSpinBox()
        lo, hi, value, step = int(lo), int(hi), int(value), int(step) or 1
    w.setRange(lo, hi)
    w.setSingleStep(step)
    w.setValue(value)
    if suffix:
        w.setSuffix(suffix)
    w.setKeyboardTracking(False)
    if on_change:
        w.valueChanged.connect(on_change)
    return w


def combo(items: list[tuple[str, object]], on_change: Optional[Callable] = None) -> QComboBox:
    w = QComboBox()
    for text, data in items:
        w.addItem(text, data)
    if on_change:
        w.currentIndexChanged.connect(lambda _i: on_change(w.currentData()))
    return w


def check(text: str, on_change: Optional[Callable] = None, checked: bool = False) -> QCheckBox:
    w = QCheckBox(text)
    w.setChecked(checked)
    if on_change:
        w.toggled.connect(on_change)
    return w


def button(text: str, on_click: Optional[Callable] = None, tip: str = "") -> QPushButton:
    w = QPushButton(text)
    if on_click:
        w.clicked.connect(lambda _=False: on_click())
    if tip:
        w.setToolTip(tip)
    return w


def row(*widgets, stretch: bool = True) -> QWidget:
    w = QWidget()
    lay = QHBoxLayout(w)
    lay.setContentsMargins(0, 0, 0, 0)
    for x in widgets:
        if isinstance(x, str):
            lay.addWidget(QLabel(x))
        elif isinstance(x, int):
            lay.addSpacing(x)
        else:
            lay.addWidget(x)
    if stretch:
        lay.addStretch(1)
    return w


def mono_font(size: int = 10) -> QFont:
    f = QFont("Consolas" if QFont("Consolas").exactMatch() else "Monospace")
    f.setStyleHint(QFont.Monospace)
    f.setPointSize(size)
    return f


def blocked(widget):
    """Context manager: set widget values without firing change handlers."""
    class _B:
        def __enter__(self_):
            self_.prev = widget.blockSignals(True)
            return widget

        def __exit__(self_, *a):
            widget.blockSignals(self_.prev)
    return _B()
