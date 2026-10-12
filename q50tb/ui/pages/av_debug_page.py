"""Monitoring > AV Debug: transactions on Nissan's ISO-TP-like AV protocol.

Works in monitor mode too (passive reassembly). In bench mode it can send
raw application records through a channel (token/segmentation/ACK handled)
or inject raw CAN frames, and tune link timing.
"""

from __future__ import annotations

import csv

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPlainTextEdit, QSpinBox, QSplitter,
                               QTableView, QTabWidget, QVBoxLayout, QWidget)

from q50tb.av.protocol import CHANNELS, describe
from q50tb.av.transport import AvReassembler, frame_kind
from q50tb.can.frame import CanFrame
from q50tb.ui.pages.base import BasePage
from q50tb.ui.widgets.common import button, error_box, mono_font, row
from q50tb.ui.widgets.hex_edit import HexBytesEdit

HEAD = ["Time", "ID", "Channel", "Side", "Bus dir", "Kind", "Token", "Description", "Data"]
ORDER = [0, 1, 2, 3, 4, 5, 6, 8, 7]   # AvLogEntry.row() index for each column

TEMPLATES = [
    ("— record templates —", "", ""),
    ("settings: read page image", "settings", "C0 21"),
    ("settings: read capabilities", "settings", "C0 01"),
    ("settings: read language", "settings", "C0 C1"),
    ("settings: read alerts (item 8)", "settings", "C0 81"),
    ("settings: read pressure unit", "settings", "C0 A1"),
    ("settings: read display effect", "settings", "C0 E1"),
    ("settings: write unit PSI", "settings", "C0 A0 02 02 00"),
    ("settings: driver break alert", "settings", "C4 2D"),
    ("settings: AUX audio state", "settings", "26 0D 00 00 02"),
    ("status: clock 12h 09:25", "status", "40 0D 02 F9 25"),
    ("status: open answer (00 21)", "status", "00 21"),
    ("nav: idle heading N", "nav", "20 0D 00 00 00 00 00 00 FF FF F0 FF 00 01 06 00"),
    ("source: select AUX", "source", "20 20 32 FF FF"),
    ("source: select FM", "source", "20 20 02 01 00"),
]


class AvLogModel(QAbstractTableModel):
    def __init__(self):
        super().__init__()
        self.rows = []
        self.font = mono_font(9)
        self.max_rows = 20000

    def rowCount(self, parent=QModelIndex()):
        return len(self.rows)

    def columnCount(self, parent=QModelIndex()):
        return len(HEAD)

    def headerData(self, s, o, role=Qt.DisplayRole):
        return HEAD[s] if role == Qt.DisplayRole and o == Qt.Horizontal else None

    def data(self, idx, role=Qt.DisplayRole):
        e = self.rows[idx.row()]
        if role == Qt.DisplayRole:
            return e.row()[ORDER[idx.column()]]
        if role == Qt.FontRole:
            return self.font
        if role == Qt.ForegroundRole:
            if e.kind == "EVT":
                return QColor("#ffb300") if "fail" in e.text or "not sent" in e.text or "timeout" in e.text.lower() else QColor("#ce93d8")
            if e.direction == "HU>":
                return QColor("#4fc3f7")
        return None

    def append(self, items):
        if not items:
            return
        over = len(self.rows) + len(items) - self.max_rows
        if over > 0:
            self.beginResetModel()
            self.rows = (self.rows + items)[-self.max_rows:]
            self.endResetModel()
            return
        n = len(self.rows)
        self.beginInsertRows(QModelIndex(), n, n + len(items) - 1)
        self.rows.extend(items)
        self.endInsertRows()

    def reset(self, items):
        self.beginResetModel()
        self.rows = items[-self.max_rows:]
        self.endResetModel()


class AvDebugPage(BasePage):
    title = "AV Debug"

    def __init__(self, ctx, bridge, parent=None):
        super().__init__(ctx, bridge, parent)
        sn = ctx.sniffer
        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(0, 0, 0, 0)
        self.chan = QComboBox()
        self.chan.addItem("all channels", None)
        for hu, peer, name, desc in CHANNELS:
            self.chan.addItem(f"{name} ({hu:03X}/{peer:03X})", name)
        self.chan.currentIndexChanged.connect(self._rebuild)
        self.frames = QCheckBox("log every frame (SF/FF/CF/FC/ACK)")
        self.frames.toggled.connect(lambda on: setattr(sn, "log_frames", on))
        self.hb = QCheckBox("log heartbeats")
        self.hb.toggled.connect(lambda on: setattr(sn, "log_heartbeats", on))
        self.paused = QCheckBox("pause view")
        self.text_filter = QLineEdit()
        self.text_filter.setPlaceholderText("text filter (e.g. C0 2C, nav, fail)")
        self.text_filter.textChanged.connect(self._rebuild)
        lay.addWidget(row(self.chan, self.frames, self.hb, self.paused, self.text_filter,
                          button("Clear", self._clear), button("Export CSV…", self._export), stretch=False))
        split = QSplitter(Qt.Vertical)
        self.model = AvLogModel()
        self.view = QTableView()
        self.view.setModel(self.model)
        self.view.verticalHeader().setVisible(False)
        self.view.verticalHeader().setDefaultSectionSize(18)
        hh = self.view.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(7, QHeaderView.Interactive)
        hh.resizeSection(7, 520)
        hh.setStretchLastSection(True)
        split.addWidget(self.view)
        split.addWidget(self._tools())
        split.setSizes([480, 260])
        lay.addWidget(split, 1)
        self._seq = 0

    # ── tools ───────────────────────────────────────────────────────
    def _tools(self) -> QWidget:
        tabs = QTabWidget()
        # record composer
        w = QWidget()
        f = QFormLayout(w)
        self.r_chan = QComboBox()
        for _hu, _peer, name, desc in CHANNELS:
            self.r_chan.addItem(f"{name} — {desc}", name)
        self.r_tmpl = QComboBox()
        for label, ch, rec in TEMPLATES:
            self.r_tmpl.addItem(label, (ch, rec))
        self.r_tmpl.currentIndexChanged.connect(self._template)
        self.r_data = HexBytesEdit(4095)
        self.r_ack = QCheckBox("expect ACK")
        self.r_ack.setChecked(True)
        self.r_force = QCheckBox("send even if channel not ready")
        self.r_result = QLabel("")
        f.addRow("Channel", self.r_chan)
        f.addRow("Template", self.r_tmpl)
        f.addRow("Record (group, item|op, payload…)", self.r_data)
        f.addRow(row(self.r_ack, self.r_force, button("Send record", self._send_record),
                     button("Decode", lambda: self.r_result.setText(describe(self._hu_id(), self.r_data.data())))))
        f.addRow("Result", self.r_result)
        tabs.addTab(w, "Send record")
        # raw frame
        w2 = QWidget()
        f2 = QFormLayout(w2)
        self.f_id = QLineEdit("628")
        self.f_data = HexBytesEdit(8)
        self.f_data.set_data(bytes.fromhex("0330C021FFFFFFFF"))
        f2.addRow("CAN ID (hex)", self.f_id)
        f2.addRow("8 bytes", self.f_data)
        f2.addRow(row(button("Inject frame", self._inject),
                      QLabel("raw frames bypass the transport (tokens/ACK not tracked)")))
        tabs.addTab(w2, "Raw frame")
        # link / channels
        w3 = QWidget()
        g = QHBoxLayout(w3)
        lk = QGroupBox("Link timing (applies on next HU start)")
        lf = QFormLayout(lk)
        lc = self.ctx.hu.link_config
        self.l_ack = QDoubleSpinBox()
        self.l_ack.setRange(0.05, 10)
        self.l_ack.setValue(lc.ack_timeout)
        self.l_fc = QDoubleSpinBox()
        self.l_fc.setRange(0.01, 10)
        self.l_fc.setValue(lc.fc_timeout)
        self.l_retry = QSpinBox()
        self.l_retry.setRange(0, 10)
        self.l_retry.setValue(lc.retries)
        self.l_st = QDoubleSpinBox()
        self.l_st.setRange(-1, 0.255)
        self.l_st.setDecimals(3)
        self.l_st.setValue(-1)
        self.l_st.setSpecialValueText("peer FC")
        self.l_blind = QCheckBox("send CFs without FC (blind)")
        self.l_answer = QDoubleSpinBox()
        self.l_answer.setRange(0, 10)
        self.l_answer.setDecimals(3)
        self.l_answer.setValue(self.ctx.hu.channels["settings"].config.answer_delay)
        lf.addRow("ACK timeout s", self.l_ack)
        lf.addRow("FC timeout s", self.l_fc)
        lf.addRow("Retries", self.l_retry)
        lf.addRow("CF spacing s", self.l_st)
        lf.addRow("", self.l_blind)
        lf.addRow("Answer delay s", self.l_answer)
        lf.addRow(button("Apply", self._apply_link))
        g.addWidget(lk)
        ch = QGroupBox("Channels (edit while HU stopped)")
        cf = QFormLayout(ch)
        self.ch_boxes = {}
        for name, rt in self.ctx.hu.channels.items():
            en = QCheckBox("enabled")
            en.setChecked(rt.config.enabled)
            peer = QCheckBox("has peer (handshake/ACK)")
            peer.setChecked(rt.config.has_peer)
            hb = QCheckBox("heartbeat")
            hb.setChecked(rt.config.heartbeat)
            cf.addRow(f"{name} {rt.config.hu_id:03X}", row(en, peer, hb,
                                                          button("Force open", lambda n=name: self.ctx.hu.force_open(n)),
                                                          button("Reset", lambda n=name: self.ctx.hu.reset_channel(n))))
            self.ch_boxes[name] = (en, peer, hb)
        cf.addRow(button("Apply channel settings", self._apply_channels))
        cf.addRow(row(button("Start virtual meter", self._vm_start), button("Stop virtual meter", self.ctx.stop_virtual_meter),
                      QLabel("offline demo: only when the AV bus is a software virtual adapter")))
        g.addWidget(ch, 1)
        tabs.addTab(w3, "Link && channels")
        # decoder
        w4 = QWidget()
        v4 = QVBoxLayout(w4)
        self.dec_in = QPlainTextEdit()
        self.dec_in.setFont(mono_font(9))
        self.dec_in.setPlaceholderText("Paste frames, one per line: '628 10 0B 70 48 ED 00 00 00' or "
                                       "candump '628#100B7048ED000000'")
        self.dec_out = QPlainTextEdit()
        self.dec_out.setReadOnly(True)
        self.dec_out.setFont(mono_font(9))
        v4.addWidget(self.dec_in)
        v4.addWidget(button("Decode pasted frames", self._decode_paste))
        v4.addWidget(self.dec_out)
        tabs.addTab(w4, "Decode paste")
        return tabs

    def _hu_id(self) -> int:
        name = self.r_chan.currentData()
        return self.ctx.hu.channels[name].config.hu_id

    def _template(self, _i) -> None:
        ch, rec = self.r_tmpl.currentData()
        if ch:
            self.r_chan.setCurrentIndex(self.r_chan.findData(ch))
            self.r_data.set_data(bytes.fromhex(rec.replace(" ", "")))

    def _send_record(self) -> None:
        name = self.r_chan.currentData()
        data = self.r_data.data()
        if not data:
            return
        self.r_result.setText("sending…")
        try:
            self.ctx.hu.send(name, data, label="debug", expect_ack=self.r_ack.isChecked(), force=self.r_force.isChecked(),
                             on_done=lambda ok, why: self.bridge.changed.emit(f"avdbg|{'OK' if ok else 'FAIL'}|{why}"))
        except Exception as exc:
            self.r_result.setText(str(exc))

    def handle_bridge(self, msg: str) -> None:
        if msg.startswith("avdbg|"):
            _, ok, why = msg.split("|", 2)
            self.r_result.setText(f"{'✔' if ok == 'OK' else '✖'} {why}")

    def _inject(self) -> None:
        try:
            cid = int(self.f_id.text().strip().replace("0x", ""), 16)
            data = (self.f_data.data() + b"\xFF" * 8)[:8]
            self.ctx.buses.send("av", CanFrame(cid, data), "av-hu", raise_on_refuse=True)  # HU channel owner
        except Exception as exc:
            error_box(self, "Inject", str(exc))

    def _apply_link(self) -> None:
        lc = self.ctx.hu.link_config
        lc.ack_timeout, lc.fc_timeout, lc.retries = self.l_ack.value(), self.l_fc.value(), self.l_retry.value()
        lc.st_min_override = None if self.l_st.value() < 0 else self.l_st.value()
        lc.blind_without_fc = self.l_blind.isChecked()
        for rt in self.ctx.hu.channels.values():
            rt.config.answer_delay = self.l_answer.value()
        self.status("link settings stored (restart HU emulation to apply)")

    def _apply_channels(self) -> None:
        if self.ctx.hu.running:
            error_box(self, "Channels", "Stop the HU emulation (AV Controls master switch) first.")
            return
        for name, (en, peer, hb) in self.ch_boxes.items():
            c = self.ctx.hu.channels[name].config
            c.enabled, c.has_peer, c.heartbeat = en.isChecked(), peer.isChecked(), hb.isChecked()
        self.status("channel settings applied")

    def _vm_start(self) -> None:
        try:
            self.ctx.start_virtual_meter()
        except Exception as exc:
            error_box(self, "Virtual meter", str(exc))

    def _decode_paste(self) -> None:
        reas: dict[int, AvReassembler] = {}
        out = []
        for n, line in enumerate(self.dec_in.toPlainText().splitlines()):
            line = line.strip()
            if not line:
                continue
            try:
                if "#" in line:
                    cid_s, data_s = line.split()[-1].split("#") if " " in line else line.split("#")
                    cid, data = int(cid_s, 16), bytes.fromhex(data_s)
                else:
                    toks = line.replace(",", " ").split()
                    cid, data = int(toks[0], 16), bytes(int(t, 16) for t in toks[1:9])
            except ValueError:
                out.append(f"{n + 1}: cannot parse {line!r}")
                continue
            r = reas.setdefault(cid, AvReassembler(cid))
            msg, rec, kind = r.feed(data, float(n))
            out.append(f"{cid:03X} {kind:3s} {data.hex(' ').upper()}")
            if rec is not None:
                out.append(f"    => record tok {rec.token_hi:X}0 [{len(rec.data)}] {rec.data.hex(' ').upper()}")
                out.append(f"       {describe(cid, rec.data)}")
        self.dec_out.setPlainText("\n".join(out))

    # ── log view ────────────────────────────────────────────────────
    def _matches(self, e) -> bool:
        ch = self.chan.currentData()
        if ch and e.channel != ch:
            return False
        t = self.text_filter.text().strip().lower()
        if t and t not in " ".join(e.row()).lower():
            return False
        return True

    def _rebuild(self, *_):
        self.model.reset([e for e in list(self.ctx.sniffer.log) if self._matches(e)])
        self._seq = self.ctx.sniffer.seq
        self.view.scrollToBottom()

    def _clear(self) -> None:
        self.ctx.sniffer.clear()
        self.model.reset([])

    def _export(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Export AV log", "av_transactions.csv", "CSV (*.csv)")
        if path:
            with open(path, "w", newline="", encoding="utf-8") as fh:
                w = csv.writer(fh)
                w.writerow(HEAD)
                for e in self.model.rows:
                    r = e.row()
                    w.writerow([r[i] for i in ORDER])

    def refresh(self) -> None:
        if self.paused.isChecked():
            return
        seq, items = self.ctx.sniffer.entries_since(self._seq)
        self._seq = seq
        items = [e for e in items if self._matches(e)]
        if items:
            at_bottom = self.view.verticalScrollBar().value() >= self.view.verticalScrollBar().maximum() - 2
            self.model.append(items)
            if at_bottom:
                self.view.scrollToBottom()
