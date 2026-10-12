"""Bench Testing > CAN Raw Message Log."""

from __future__ import annotations

import os

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
                               QLineEdit, QPushButton, QSpinBox, QTableView, QVBoxLayout)

from q50tb.capture.formats import save_capture
from q50tb.core import timebase
from q50tb.core.log_buffer import IdFilter
from q50tb.ui.pages.base import BasePage
from q50tb.ui.widgets.common import button, error_box, mono_font, row

COLUMNS = ["Time (s)", "Bus", "Dir", "ID", "DLC", "Data"]


class FrameModel(QAbstractTableModel):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.rows = []
        self.max_rows = 50_000
        self.font = mono_font(9)
        self.tx_color = QColor("#4fc3f7")

    def rowCount(self, parent=QModelIndex()):
        return len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(COLUMNS)

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role == Qt.DisplayRole and orientation == Qt.Horizontal:
            return COLUMNS[section]
        return None

    def data(self, index, role=Qt.DisplayRole):
        f = self.rows[index.row()]
        c = index.column()
        if role == Qt.DisplayRole:
            if c == 0:
                return f"{f.timestamp:.6f}"
            if c == 1:
                return f.bus
            if c == 2:
                return f.direction
            if c == 3:
                return f.id_text + (" FD" if f.is_fd else "") + (" ERR" if f.is_error else "")
            if c == 4:
                return str(f.dlc)
            return f.data_text
        if role == Qt.FontRole:
            return self.font
        if role == Qt.ForegroundRole and f.direction == "TX":
            return self.tx_color
        return None

    def reset(self, frames):
        self.beginResetModel()
        self.rows = frames[-self.max_rows:]
        self.endResetModel()

    def append(self, frames):
        if not frames:
            return
        overflow = len(self.rows) + len(frames) - self.max_rows
        if overflow > 0:
            if overflow >= len(self.rows):
                self.reset(frames)
                return
            self.beginRemoveRows(QModelIndex(), 0, overflow - 1)
            del self.rows[:overflow]
            self.endRemoveRows()
        n = len(self.rows)
        self.beginInsertRows(QModelIndex(), n, n + len(frames) - 1)
        self.rows.extend(frames)
        self.endInsertRows()


class LogPage(BasePage):
    title = "Raw Log"

    def __init__(self, ctx, bridge, parent=None):
        super().__init__(ctx, bridge, parent)
        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(0, 0, 0, 0)
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.setCheckable(True)
        self.pause_btn.toggled.connect(self._pause)
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("ID filter: 359, 300   •   35 = prefix (359, 35A…)   •   -6A8 excludes")
        self.filter.textChanged.connect(self._rebuild)
        self.dir = QComboBox()
        for text, val in (("TX + RX", ("RX", "TX")), ("RX only", ("RX",)), ("TX only", ("TX",))):
            self.dir.addItem(text, val)
        self.dir.currentIndexChanged.connect(self._rebuild)
        self.bus = QComboBox()
        for text, val in (("All buses", None), ("main", ("main",)), ("av", ("av",))):
            self.bus.addItem(text, val)
        self.bus.currentIndexChanged.connect(self._rebuild)
        self.autoscroll = QCheckBox("Auto-scroll")
        self.autoscroll.setChecked(True)
        self.max_rows = QSpinBox()
        self.max_rows.setRange(1000, 500_000)
        self.max_rows.setSingleStep(10_000)
        self.max_rows.setValue(50_000)
        self.max_rows.valueChanged.connect(self._set_max)
        lay.addWidget(row(self.pause_btn, button("Clear", self._clear), button("Export…", self._export),
                          QLabel("Direction"), self.dir, self.bus, self.autoscroll, QLabel("Max rows"), self.max_rows))
        lay.addWidget(self.filter)
        self.rec_btn = QPushButton("● Record to file…")
        self.rec_btn.setCheckable(True)
        self.rec_btn.toggled.connect(self._record)
        self.rec_lbl = QLabel("")
        lay.addWidget(row(self.rec_btn, button("Add marker…", self._marker), self.rec_lbl))
        self.model = FrameModel(self)
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.verticalHeader().setVisible(False)
        self.view.verticalHeader().setDefaultSectionSize(18)
        self.view.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.view.horizontalHeader().setStretchLastSection(True)
        self.view.setSelectionBehavior(QTableView.SelectRows)
        lay.addWidget(self.view, 1)
        self.count_lbl = QLabel()
        lay.addWidget(self.count_lbl)
        self._seq = 0
        self._flt = IdFilter("")

    def _matches(self, f) -> bool:
        dirs = self.dir.currentData()
        buses = self.bus.currentData()
        return f.direction in dirs and (buses is None or f.bus in buses) and self._flt.matches(f)

    def _rebuild(self, *_):
        self._flt = IdFilter(self.filter.text())
        frames = self.ctx.log.snapshot()
        self._seq = frames[-1].seq if frames else self.ctx.log.last_seq
        self.model.reset([f for f in frames if self._matches(f)])
        if self.autoscroll.isChecked():
            self.view.scrollToBottom()

    def _set_max(self, n: int) -> None:
        self.model.max_rows = n
        self._rebuild()

    def _pause(self, on: bool) -> None:
        self.ctx.log.paused = on
        self.pause_btn.setText("Resume" if on else "Pause")

    def _clear(self) -> None:
        self.ctx.log.clear()
        self.model.reset([])

    def _export(self) -> None:
        path, flt = QFileDialog.getSaveFileName(self, "Export log", "can_log.csv",
                                                "CSV (*.csv);;pcapng (*.pcapng);;candump (*.log)")
        if not path:
            return
        frames = list(self.model.rows)
        try:
            n = save_capture(path, frames, base_unix_time=timebase.wall(0.0))
            self.status(f"exported {n} frames to {path}")
        except Exception as exc:
            error_box(self, "Export", str(exc))

    def _record(self, on: bool) -> None:
        if on:
            path, _ = QFileDialog.getSaveFileName(self, "Record to", "bench_capture.pcapng", "pcapng (*.pcapng)")
            if not path:
                self.rec_btn.setChecked(False)
                return
            self.ctx.recorder.start(path)
            self.rec_btn.setText("■ Stop recording")
        else:
            self.ctx.recorder.stop()
            self.rec_btn.setText("● Record to file…")

    def _marker(self) -> None:
        text, ok = QInputDialog.getText(self, "Marker", "Annotation (saved next to the recording):")
        if ok and text:
            self.ctx.recorder.marker(text)
            self.ctx.buses.status("info", f"marker: {text}")

    def refresh(self) -> None:
        new = self.ctx.log.since(self._seq, limit=self.model.max_rows)
        if new:
            self._seq = new[-1].seq
            self.model.append([f for f in new if self._matches(f)])
            if self.autoscroll.isChecked():
                self.view.scrollToBottom()
        self.count_lbl.setText(f"showing {len(self.model.rows)} rows • buffer {len(self.ctx.log)} frames"
                               f"{' • PAUSED (' + str(self.ctx.log.dropped_while_paused) + ' not logged)' if self.ctx.log.paused else ''}")
        if self.ctx.recorder.active:
            self.rec_lbl.setText(f"recording {os.path.basename(self.ctx.recorder.path)}: {self.ctx.recorder.count} frames"
                                 f"{', dropped ' + str(self.ctx.recorder.dropped) if self.ctx.recorder.dropped else ''}")
        else:
            self.rec_lbl.setText("")
