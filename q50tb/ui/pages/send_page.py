"""Bench Testing > CAN Message Send.

CAN FD frames are only offered on a bus opened as CAN FD. An FD-configured
controller may also send classical frames (ISO 11898-1:2015 allows mixed
traffic on an FD network); a classical bus never carries FD frames.
"""

from __future__ import annotations

import json

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout,
                               QGroupBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from q50tb.can.frame import FD_LENGTHS, CanFrame
from q50tb.core.bus_manager import SendRefused
from q50tb.core.send_queue import SendEntry, SweepSpec
from q50tb.sim.background import ChecksumRule, CounterRule
from q50tb.ui.pages.base import BasePage
from q50tb.ui.widgets.common import blocked, button, error_box, mono_font, row
from q50tb.ui.widgets.hex_edit import HexBytesEdit, HexIdEdit


class SendPage(BasePage):
    title = "CAN Send"
    tx_page = True

    def __init__(self, ctx, bridge, parent=None):
        super().__init__(ctx, bridge, parent)
        q = ctx.send_queue
        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        lay.addLayout(top)

        fg = QGroupBox("Frame")
        f = QFormLayout(fg)
        self.bus = QComboBox()
        self.bus.currentIndexChanged.connect(self._bus_changed)
        self.cid = HexIdEdit()
        self.ext = QCheckBox("Extended (29-bit)")
        self.ext.toggled.connect(lambda on: setattr(self.cid, "extended", on))
        self.fd = QCheckBox("CAN FD")
        self.brs = QCheckBox("Bit-rate switch (BRS)")
        self.fd.toggled.connect(self._fd_changed)
        self.length = QComboBox()
        self.data = HexBytesEdit(8)
        self.data.bytesChanged.connect(self._data_changed)
        self.length.currentIndexChanged.connect(self._len_changed)
        self.period = QDoubleSpinBox()
        self.period.setRange(0, 600000)
        self.period.setDecimals(1)
        self.period.setSuffix(" ms")
        self.period.setSpecialValueText("once")
        self.period.setValue(100)
        self.limit = QSpinBox()
        self.limit.setRange(0, 10_000_000)
        self.limit.setSpecialValueText("unlimited")
        self.name = QLineEdit()
        self.name.setPlaceholderText("optional label")
        self.fd_note = QLabel()
        self.fd_note.setWordWrap(True)
        self.fd_note.setStyleSheet("color: #9aa5ae;")
        f.addRow("Bus", self.bus)
        f.addRow("CAN ID (hex)", row(self.cid, self.ext))
        f.addRow("Type", row(self.fd, self.brs))
        f.addRow("", self.fd_note)
        f.addRow("Length", self.length)
        f.addRow("Data", self.data)
        f.addRow("Period", self.period)
        f.addRow("Stop after", row(self.limit, QLabel("frames")))
        f.addRow("Label", self.name)
        top.addWidget(fg, 2)

        opts = QVBoxLayout()
        cg = QGroupBox("Rolling counter")
        cg.setCheckable(True)
        cg.setChecked(False)
        cf = QFormLayout(cg)
        self.c_byte, self.c_lsb, self.c_width, self.c_step = (QSpinBox(), QSpinBox(), QSpinBox(), QSpinBox())
        self.c_byte.setRange(0, 63)
        self.c_lsb.setRange(0, 7)
        self.c_width.setRange(1, 8)
        self.c_width.setValue(4)
        self.c_step.setRange(1, 255)
        cf.addRow("Byte", self.c_byte)
        cf.addRow("Low bit / width", row(self.c_lsb, self.c_width))
        cf.addRow("Step", self.c_step)
        self.counter_box = cg
        opts.addWidget(cg)
        kg = QGroupBox("Checksum")
        kg.setCheckable(True)
        kg.setChecked(False)
        kf = QFormLayout(kg)
        self.k_kind = QComboBox()
        for k in ("sum8", "xor8", "xor_nibbles"):
            self.k_kind.addItem(k)
        self.k_byte = QSpinBox()
        self.k_byte.setRange(0, 63)
        self.k_byte.setValue(7)
        self.k_add = QSpinBox()
        self.k_add.setRange(0, 255)
        self.k_add.setDisplayIntegerBase(16)
        self.k_add.setPrefix("0x")
        kf.addRow("Kind", self.k_kind)
        kf.addRow("Result byte", self.k_byte)
        kf.addRow("Constant", self.k_add)
        kf.addRow(QLabel("Computed over every other byte.\n0x284: sum8 +0x86, 0x002: xor_nibbles"))
        self.checksum_box = kg
        opts.addWidget(kg)
        top.addLayout(opts, 1)

        sg = QGroupBox("Byte sweep")
        sg.setCheckable(True)
        sg.setChecked(False)
        sf = QFormLayout(sg)
        self.s_mode = QComboBox()
        self.s_mode.addItem("Walking bit (01,02,04…80)", "bit")
        self.s_mode.addItem("Counter (start..end step)", "counter")
        self.s_mode.addItem("Custom sequence", "custom")
        self.s_bytes = QLineEdit("0")
        self.s_bytes.setToolTip("bytes to sweep one after another, e.g. '0,1,2' or 'all'")
        self.s_start, self.s_end, self.s_step = QSpinBox(), QSpinBox(), QSpinBox()
        for sp in (self.s_start, self.s_end):
            sp.setRange(0, 255)
            sp.setDisplayIntegerBase(16)
            sp.setPrefix("0x")
        self.s_end.setValue(255)
        self.s_step.setRange(1, 255)
        self.s_custom = QLineEdit()
        self.s_custom.setPlaceholderText("hex values, e.g. 00 10 20 FF")
        self.s_zero = QCheckBox("include 0x00 first")
        self.s_dwell = QDoubleSpinBox()
        self.s_dwell.setRange(1, 600000)
        self.s_dwell.setValue(1000)
        self.s_dwell.setSuffix(" ms per value")
        self.s_loop = QCheckBox("Loop")
        sf.addRow("Mode", self.s_mode)
        sf.addRow("Bytes", self.s_bytes)
        sf.addRow("Start / end / step", row(self.s_start, self.s_end, self.s_step))
        sf.addRow("Custom", self.s_custom)
        sf.addRow("", row(self.s_zero, self.s_loop))
        sf.addRow("Dwell", self.s_dwell)
        sf.addRow(QLabel("Baseline = the Data field; other bytes keep the baseline."))
        self.sweep_box = sg
        top.addWidget(sg, 1)

        lay.addWidget(row(button("Send once", self._send_once), button("Add to queue", self._add),
                          button("Load queue…", self._load), button("Save queue…", self._save)))

        self.table = QTableWidget(0, 9)
        self.table.setHorizontalHeaderLabels(["On", "#", "Bus", "ID", "Data (last sent)", "Period", "TX", "Status",
                                              "Details"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setFont(mono_font(9))
        self.table.cellClicked.connect(self._cell_clicked)
        lay.addWidget(self.table, 1)
        lay.addWidget(row(button("Remove selected", self._remove_sel), button("Clear queue", q.clear),
                          button("Start selected", lambda: self._enable_sel(True)),
                          button("Stop selected", lambda: self._enable_sel(False)),
                          button("Load selected into editor", self._edit_sel)))
        self._fill_buses()

    # ── editor state ────────────────────────────────────────────────
    def _fill_buses(self) -> None:
        cur = self.bus.currentData()
        with blocked(self.bus):
            self.bus.clear()
            for role, ch in self.ctx.buses.channels.items():
                self.bus.addItem(f"{role} ({ch.adapter.info.key}{', FD' if ch.is_fd else ''})", role)
            if self.bus.count() == 0:
                self.bus.addItem("main (not open)", "main")
            i = self.bus.findData(cur)
            self.bus.setCurrentIndex(max(0, i))
        self._bus_changed()

    def _bus_is_fd(self) -> bool:
        ch = self.ctx.buses.channels.get(self.bus.currentData())
        return bool(ch and ch.is_fd)

    def _bus_changed(self, *_):
        fd_ok = self._bus_is_fd()
        self.fd.setEnabled(fd_ok)
        if not fd_ok:
            self.fd.setChecked(False)
        self.fd_note.setText("Bus opened as CAN FD: classical and FD frames allowed." if fd_ok else
                             "Bus opened as classical CAN: FD frames are not allowed (max 8 bytes).")
        self._fd_changed(self.fd.isChecked())

    def _fd_changed(self, on: bool) -> None:
        self.brs.setEnabled(on)
        if not on:
            self.brs.setChecked(False)
        with blocked(self.length):
            self.length.clear()
            for n in (FD_LENGTHS if on else range(9)):
                self.length.addItem(str(n), n)
            self.length.setCurrentIndex(self.length.findData(min(len(self.data.data()) or 8, 64 if on else 8)))
        self.data.set_max_bytes(64 if on else 8)
        self._len_changed()

    def _len_changed(self, *_):
        n = self.length.currentData() or 0
        d = self.data.data()
        if len(d) != n:
            self.data.set_data((d + bytes(n))[:n])

    def _data_changed(self, b: bytes) -> None:
        n = len(b)
        if self.fd.isChecked():
            target = next((x for x in FD_LENGTHS if x >= n), 64)
        else:
            target = min(n, 8)
        i = self.length.findData(target)
        if i >= 0:
            with blocked(self.length):
                self.length.setCurrentIndex(i)

    def _build(self) -> SendEntry:
        cid = self.cid.value()
        if cid is None:
            raise ValueError("enter a valid hex CAN ID")
        if not self.data.is_complete():
            raise ValueError("data has an odd number of hex digits")
        data = self.data.data()
        n = self.length.currentData() or len(data)
        data = (data + bytes(n))[:n]
        e = SendEntry(self.bus.currentData() or "main", cid, data, period_ms=self.period.value(),
                      is_extended=self.ext.isChecked(), is_fd=self.fd.isChecked(), bitrate_switch=self.brs.isChecked(),
                      name=self.name.text(), count_limit=self.limit.value())
        if self.counter_box.isChecked():
            e.counter = CounterRule(self.c_byte.value(), self.c_lsb.value(), self.c_width.value(), self.c_step.value())
        if self.checksum_box.isChecked():
            kb = self.k_byte.value()
            e.checksum = ChecksumRule(self.k_kind.currentText(), kb, [i for i in range(len(data)) if i != kb],
                                      self.k_add.value())
        if self.sweep_box.isChecked():
            txt = self.s_bytes.text().strip().lower()
            bl = list(range(len(data))) if txt == "all" else [int(x) for x in txt.replace(" ", ",").split(",") if x]
            custom = [int(x, 16) for x in self.s_custom.text().replace(",", " ").split() if x]
            e.sweep = SweepSpec(self.s_mode.currentData(), bl, self.s_start.value(), self.s_end.value(),
                                self.s_step.value(), custom, self.s_zero.isChecked(), self.s_dwell.value(),
                                self.s_loop.isChecked())
            if e.period_ms <= 0:
                e.period_ms = 100
        return e

    def _send_once(self) -> None:
        try:
            e = self._build()
            payload = e.next_payload(0.0) if (e.counter or e.checksum) else e.data
            self.ctx.send_queue.send_once(CanFrame(e.arbitration_id, payload, is_extended=e.is_extended,
                                                   is_fd=e.is_fd, bitrate_switch=e.bitrate_switch, bus=e.bus))
            self.status(f"sent {e.bus} {e.arbitration_id:03X} {payload.hex(' ').upper()}")
        except (ValueError, SendRefused) as exc:
            error_box(self, "Send", str(exc))

    def _add(self) -> None:
        try:
            self.ctx.send_queue.add(self._build())
        except ValueError as exc:
            error_box(self, "Queue", str(exc))

    # ── queue table ─────────────────────────────────────────────────
    def _selected_uids(self) -> list[int]:
        rows = {i.row() for i in self.table.selectedIndexes()}
        out = []
        for r in rows:
            it = self.table.item(r, 1)
            if it:
                out.append(int(it.text()))
        return out

    def _cell_clicked(self, r: int, c: int) -> None:
        if c == 0:
            uid = int(self.table.item(r, 1).text())
            e = self.ctx.send_queue.entries.get(uid)
            if e:
                self.ctx.send_queue.set_enabled(uid, not e.enabled)

    def _remove_sel(self) -> None:
        for uid in self._selected_uids():
            self.ctx.send_queue.remove(uid)

    def _enable_sel(self, on: bool) -> None:
        for uid in self._selected_uids():
            self.ctx.send_queue.set_enabled(uid, on)

    def _edit_sel(self) -> None:
        uids = self._selected_uids()
        if not uids:
            return
        e = self.ctx.send_queue.entries.get(uids[0])
        if not e:
            return
        self.cid.setText(f"{e.arbitration_id:X}")
        self.ext.setChecked(e.is_extended)
        self.fd.setChecked(e.is_fd)
        self.data.set_data(e.data)
        self.period.setValue(e.period_ms)
        self.name.setText(e.name)

    def _save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save queue", "queue.json", "JSON (*.json)")
        if not path:
            return
        out = []
        for e in self.ctx.send_queue.entries.values():
            d = {"bus": e.bus, "id": f"0x{e.arbitration_id:X}", "data": e.data.hex(" "), "period_ms": e.period_ms,
                 "extended": e.is_extended, "fd": e.is_fd, "brs": e.bitrate_switch, "name": e.name,
                 "count_limit": e.count_limit}
            if e.counter:
                d["counter"] = e.counter.to_json()
            if e.checksum:
                d["checksum"] = e.checksum.to_json()
            if e.sweep:
                d["sweep"] = vars(e.sweep)
            out.append(d)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(out, fh, indent=1)

    def _load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load queue", "", "JSON (*.json)")
        if not path:
            return
        try:
            with open(path, encoding="utf-8") as fh:
                for d in json.load(fh):
                    e = SendEntry(d.get("bus", "main"), int(d["id"], 16), bytes.fromhex(d["data"]),
                                  period_ms=d.get("period_ms", 100), is_extended=d.get("extended", False),
                                  is_fd=d.get("fd", False), bitrate_switch=d.get("brs", False), name=d.get("name", ""),
                                  count_limit=d.get("count_limit", 0), enabled=False)
                    if d.get("counter"):
                        e.counter = CounterRule(**d["counter"])
                    if d.get("checksum"):
                        e.checksum = ChecksumRule(**d["checksum"])
                    if d.get("sweep"):
                        e.sweep = SweepSpec(**d["sweep"])
                    self.ctx.send_queue.add(e)
        except Exception as exc:
            error_box(self, "Load queue", str(exc))

    def on_bus_state(self) -> None:
        super().on_bus_state()
        self._fill_buses()

    def refresh(self) -> None:
        entries = list(self.ctx.send_queue.entries.values())
        self.table.setRowCount(len(entries))
        for r, e in enumerate(entries):
            vals = ["●" if e.enabled else "○", str(e.uid), e.bus,
                    (f"{e.arbitration_id:08X}" if e.is_extended else f"{e.arbitration_id:03X}") + (" FD" if e.is_fd else ""),
                    (e.last_data or e.data).hex(" ").upper(), "once" if e.period_ms <= 0 else f"{e.period_ms:g} ms",
                    str(e.tx_count), e.status, " ".join(x for x in (e.name, e.describe(), e.sweep_progress) if x)]
            for c, v in enumerate(vals):
                it = self.table.item(r, c)
                if it is None:
                    it = QTableWidgetItem()
                    self.table.setItem(r, c, it)
                if it.text() != v:
                    it.setText(v)
