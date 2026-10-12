"""Monitoring > CAN Message Tracker.

Byte colours: grey = waiting / beyond DLC, green = unchanged since the last
highlight reset, yellow = changed on the last frame, orange = changed since
the last reset. A stale message turns its whole row slate-blue.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QHeaderView, QLabel, QLineEdit, QPushButton,
                               QSpinBox, QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget, QHBoxLayout)

from q50tb.core.tracker import CHANGED, CHANGED_LAST, SAME, WAITING
from q50tb.ui.pages.base import BasePage
from q50tb.ui.widgets.common import button, mono_font, row

COLORS = {WAITING: QColor("#4a4f55"), SAME: QColor("#2e7d32"), CHANGED_LAST: QColor("#c9a400"),
          CHANGED: QColor("#ef6c00")}
STALE = QColor("#3f4f8f")
NBYTES = 8


class TrackerPage(BasePage):
    title = "Message Tracker"

    def __init__(self, ctx, bridge, parent=None):
        super().__init__(ctx, bridge, parent)
        tr = ctx.tracker
        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(0, 0, 0, 0)
        self.ids = QLineEdit()
        self.ids.setPlaceholderText("IDs to watch (hex, comma separated), e.g. 180, 60D, 385")
        self.ids.returnPressed.connect(self._add)
        self.bus = QComboBox()
        for text, val in (("any bus", None), ("main", "main"), ("av", "av")):
            self.bus.addItem(text, val)
        self.stale = QSpinBox()
        self.stale.setRange(50, 60000)
        self.stale.setValue(int(tr.stale_s * 1000))
        self.stale.setSuffix(" ms")
        self.stale.valueChanged.connect(lambda v: setattr(tr, "stale_s", v / 1000.0))
        self.binary = QCheckBox("Binary")
        self.nbytes = QSpinBox()
        self.nbytes.setRange(8, 64)
        self.nbytes.setValue(NBYTES)
        self.nbytes.valueChanged.connect(self._resize_cols)
        lay.addWidget(row(self.ids, self.bus, button("Add", self._add), button("Clear all", self._clear),
                          button("Reset all highlights", tr.reset_all), stretch=False))
        lay.addWidget(row("Stale after", self.stale, "(or 5× the observed period)", self.binary, "Byte columns", self.nbytes))
        legend = QHBoxLayout()
        for text, col in (("waiting / beyond DLC", COLORS[WAITING]), ("unchanged since reset", COLORS[SAME]),
                          ("changed on last frame", COLORS[CHANGED_LAST]), ("changed since reset", COLORS[CHANGED]),
                          ("stale message", STALE)):
            lab = QLabel(f"  {text}  ")
            lab.setStyleSheet(f"background: {col.name()}; color: white; border-radius: 3px;")
            legend.addWidget(lab)
        legend.addStretch(1)
        lay.addLayout(legend)
        self.table = QTableWidget(0, 0)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setFont(mono_font(10))
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(24)
        lay.addWidget(self.table, 1)
        self._keys: list = []
        self._resize_cols()

    def _resize_cols(self, *_):
        n = self.nbytes.value()
        heads = ["ID", "Bus", "DLC"] + [f"B{i}" for i in range(n)] + ["Count", "Period", "Last", "Status", "", ""]
        self.table.setColumnCount(len(heads))
        self.table.setHorizontalHeaderLabels(heads)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        self._keys = []
        self.table.setRowCount(0)

    def _add(self) -> None:
        for tok in self.ids.text().replace(" ", ",").split(","):
            tok = tok.strip().lower().replace("0x", "")
            if not tok:
                continue
            try:
                self.ctx.tracker.add(int(tok, 16), self.bus.currentData())
            except ValueError:
                self.status(f"bad ID {tok!r}")
        self.ids.clear()

    def _clear(self) -> None:
        self.ctx.tracker.clear()

    def _row_buttons(self, r: int, key) -> None:
        n = self.nbytes.value()
        reset = QPushButton("Reset")
        reset.clicked.connect(lambda _=False, k=key: self.ctx.tracker.watches.get(k) and self.ctx.tracker.watches[k].reset_highlights())
        rm = QPushButton("Remove")
        rm.clicked.connect(lambda _=False, k=key: self.ctx.tracker.remove(k[1], k[0]))
        self.table.setCellWidget(r, 3 + n + 4, reset)
        self.table.setCellWidget(r, 3 + n + 5, rm)

    def refresh(self) -> None:
        tr = self.ctx.tracker
        watches = sorted(tr.watches.values(), key=lambda w: (w.bus or "", w.arbitration_id))
        keys = [w.key for w in watches]
        n = self.nbytes.value()
        if keys != self._keys:
            self.table.setRowCount(len(keys))
            for r, k in enumerate(keys):
                self._row_buttons(r, k)
            self._keys = keys
        binary = self.binary.isChecked()
        for r, w in enumerate(watches):
            stale = tr.is_stale(w)
            states = w.byte_states(n)
            age = w.age()
            status = "waiting for frame" if w.data is None else ("STALE" if stale else "live")
            vals = [f"{w.arbitration_id:03X}", w.bus or (w.last_bus or "any"), "—" if w.data is None else str(len(w.data))]
            self._set(r, 0, vals[0])
            self._set(r, 1, vals[1])
            self._set(r, 2, vals[2])
            for i in range(n):
                v, st = states[i] if i < len(states) else (None, WAITING)
                txt = "--" if v is None else (f"{v:08b}" if binary else f"{v:02X}")
                it = self._set(r, 3 + i, txt)
                bg = STALE if (stale and v is not None) else COLORS[st]
                it.setBackground(bg)
                it.setForeground(QColor("white"))
                it.setTextAlignment(Qt.AlignCenter)
            self._set(r, 3 + n, str(w.count))
            self._set(r, 4 + n, "—" if w.period_ms is None else f"{w.period_ms:.1f} ms")
            self._set(r, 5 + n, "—" if age is None else f"{w.last_t:.3f} s ({age:.2f}s ago) {w.last_dir}")
            it = self._set(r, 6 + n, status)
            it.setForeground(QColor("#8c9eff") if stale else QColor("#9e9e9e") if w.data is None else QColor("#66bb6a"))

    def _set(self, r: int, c: int, text: str) -> QTableWidgetItem:
        it = self.table.item(r, c)
        if it is None:
            it = QTableWidgetItem()
            self.table.setItem(r, c, it)
        if it.text() != text:
            it.setText(text)
        return it
