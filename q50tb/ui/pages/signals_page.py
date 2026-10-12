"""Monitoring > Live Signal Tracker.

Source is either an uploaded .dbc (decoded with cantools) or the built-in Q50
database. Rows show value, unit, min/max, update count and age; tick 'Plot'
on a few numeric signals to trend them.
"""

from __future__ import annotations

import os
from collections import deque

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QPainter, QPen
from PySide6.QtWidgets import (QAbstractItemView, QComboBox, QFileDialog, QHeaderView, QLabel, QLineEdit, QSplitter,
                               QTableWidget, QTableWidgetItem, QVBoxLayout, QWidget)

from q50tb.core import timebase
from q50tb.signals.dbc import export_dbc
from q50tb.ui.pages.base import BasePage
from q50tb.ui.widgets.common import button, error_box, mono_font, row

PALETTE = ["#4fc3f7", "#ffb74d", "#81c784", "#e57373", "#ba68c8", "#fff176", "#4db6ac", "#f06292"]
COLS = ["Plot", "Message", "Signal", "Value", "Unit", "Min", "Max", "Count", "Age", "Dir"]


class TrendPlot(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.series: dict[str, deque] = {}
        self.window_s = 30.0
        self.setMinimumHeight(160)

    def add_point(self, name: str, t: float, v: float) -> None:
        d = self.series.setdefault(name, deque(maxlen=3000))
        if not d or d[-1][0] != t:
            d.append((t, v))

    def paintEvent(self, _):
        p = QPainter(self)
        p.fillRect(self.rect(), QColor("#0e1216"))
        p.setRenderHint(QPainter.Antialiasing)
        if not self.series:
            p.setPen(QColor("#666"))
            p.drawText(self.rect(), Qt.AlignCenter, "tick 'Plot' on numeric signals to trend them")
            return
        now = timebase.now()
        t0 = now - self.window_s
        w, h = self.width() - 10, self.height() - 20
        for i, (name, d) in enumerate(self.series.items()):
            pts = [(t, v) for t, v in d if t >= t0]
            col = QColor(PALETTE[i % len(PALETTE)])
            p.setPen(col)
            p.drawText(8, 14 + 14 * i, name)
            if len(pts) < 2:
                continue
            lo = min(v for _, v in pts)
            hi = max(v for _, v in pts)
            span = (hi - lo) or 1.0
            p.setPen(QPen(col, 1.5))
            prev = None
            for t, v in pts:
                pt = QPointF(5 + (t - t0) / self.window_s * w, 15 + h - (v - lo) / span * (h - 10))
                if prev is not None:
                    p.drawLine(prev, pt)
                prev = pt
            p.drawText(self.width() - 140, 14 + 14 * i, f"{lo:g} … {hi:g}")


class SignalsPage(BasePage):
    title = "Signal Tracker"

    def __init__(self, ctx, bridge, parent=None):
        super().__init__(ctx, bridge, parent)
        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(0, 0, 0, 0)
        self.source = QComboBox()
        self.source.addItem("Built-in Q50 database", "builtin")
        self.source.addItem("Uploaded DBC", "dbc")
        self.source.currentIndexChanged.connect(lambda _i: self._rebuild())
        self.bus = QComboBox()
        for text, val in (("main", "main"), ("av", "av"), ("any bus (DBC)", None)):
            self.bus.addItem(text, val)
        self.bus.currentIndexChanged.connect(self._bus_changed)
        self.dbc_lbl = QLabel("no DBC loaded")
        lay.addWidget(row(self.source, self.bus, button("Upload .dbc…", self._load_dbc),
                          button("Export built-in DB as .dbc…", self._export), self.dbc_lbl))
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("filter signals…")
        self.filter.textChanged.connect(lambda _t: self._rebuild())
        lay.addWidget(self.filter)
        split = QSplitter(Qt.Vertical)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setFont(mono_font(9))
        self.table.verticalHeader().setDefaultSectionSize(18)
        self.table.cellClicked.connect(self._clicked)
        split.addWidget(self.table)
        self.plot = TrendPlot()
        split.addWidget(self.plot)
        split.setSizes([500, 200])
        lay.addWidget(split, 1)
        self._rows: list[tuple] = []
        self._plotted: set[str] = set()
        self._rebuild()

    def _bus_changed(self, _i) -> None:
        self.ctx.dbc.bus_filter = self.bus.currentData()
        self._rebuild()

    def _load_dbc(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load DBC", self.ctx.root, "CAN database (*.dbc *.kcd *.sym *.arxml)")
        if not path:
            return
        try:
            n = self.ctx.dbc.load(path)
        except Exception as exc:
            error_box(self, "DBC", str(exc))
            return
        self.ctx.dbc.bus_filter = self.bus.currentData()
        self.dbc_lbl.setText(f"{os.path.basename(path)}: {n} signals")
        self.source.setCurrentIndex(1)
        self._rebuild()

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export DBC", os.path.join(self.ctx.root, "q50_main.dbc"), "DBC (*.dbc)")
        if path:
            n, skipped = export_dbc(self.ctx.db, path)
            self.status(f"exported {n} signals; skipped {len(skipped)} non-contiguous: {', '.join(skipped[:5])}")

    def _rebuild(self) -> None:
        flt = self.filter.text().strip().lower()
        rows = []
        if self.source.currentData() == "dbc":
            for (fid, name), ls in sorted(self.ctx.dbc.signals.items()):
                if flt and flt not in name.lower() and flt not in ls.message.lower() and flt not in f"{fid:x}":
                    continue
                rows.append(("dbc", (fid, name), f"0x{fid:03X} {ls.message}", name))
        else:
            bus = self.bus.currentData() or "main"
            for m in self.ctx.db.by_bus(bus):
                for s in m.signals:
                    if flt and flt not in s.name.lower() and flt not in m.name.lower() and flt not in f"{m.arbitration_id:x}":
                        continue
                    rows.append(("builtin", s.full_name, f"0x{m.arbitration_id:03X} {m.name}", s.name))
        self._rows = rows
        self.table.setRowCount(len(rows))
        for r, (_k, key, msg, name) in enumerate(rows):
            for c, v in ((0, "☑" if str(key) in self._plotted else "☐"), (1, msg), (2, name)):
                self.table.setItem(r, c, QTableWidgetItem(v))

    def _clicked(self, r: int, c: int) -> None:
        if c != 0 or r >= len(self._rows):
            return
        key = str(self._rows[r][1])
        if key in self._plotted:
            self._plotted.discard(key)
            self.plot.series.pop(self._rows[r][3], None)
        else:
            self._plotted.add(key)
        self.table.item(r, 0).setText("☑" if key in self._plotted else "☐")

    def _set(self, r: int, c: int, text: str) -> None:
        it = self.table.item(r, c)
        if it is None:
            it = QTableWidgetItem()
            self.table.setItem(r, c, it)
        if it.text() != text:
            it.setText(text)

    def refresh(self) -> None:
        now = timebase.now()
        first = self.table.rowAt(0)
        last = self.table.rowAt(self.table.viewport().height())
        visible = range(max(0, first), (last if last >= 0 else len(self._rows) - 1) + 1)
        for r in visible:
            if r >= len(self._rows):
                break
            kind, key, _msg, name = self._rows[r]
            if kind == "dbc":
                ls = self.ctx.dbc.signals.get(key)
                if ls is None:
                    continue
                val = "—" if ls.value is None else str(ls.value)
                vals = [val, ls.unit, "" if ls.minimum is None else f"{ls.minimum:g}",
                        "" if ls.maximum is None else f"{ls.maximum:g}", str(ls.count),
                        "—" if ls.t is None else f"{now - ls.t:.2f}s", ls.direction]
            else:
                rd = self.ctx.vehicle.read(key)
                s = self.ctx.db.signal(key)
                vals = [rd.text if rd.value is not None else "—", s.unit, "", "", "",
                        "—" if rd.age is None else f"{rd.age:.2f}s" + (" stale" if rd.stale else ""), rd.direction]
            for i, v in enumerate(vals):
                self._set(r, 3 + i, v)
        for kind, key, _msg, name in self._rows:
            if str(key) not in self._plotted:
                continue
            if kind == "dbc":
                ls = self.ctx.dbc.signals.get(key)
                if ls and isinstance(ls.value, (int, float)) and ls.t is not None:
                    self.plot.add_point(name, ls.t, float(ls.value))
            else:
                rd = self.ctx.vehicle.read(key)
                if rd.value is not None and rd.age is not None:
                    self.plot.add_point(name, now - rd.age, rd.value)
        self.plot.update()
