"""Bench Testing > Replay: two captures on two buses, started together."""

from __future__ import annotations

import os

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog, QFormLayout, QGroupBox,
                               QHBoxLayout, QHeaderView, QLabel, QLineEdit, QProgressBar, QSpinBox, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from q50tb.capture.formats import CAPTURE_FILTER
from q50tb.core.replay import ReplayTrack
from q50tb.ui.pages.base import BasePage
from q50tb.ui.widgets.common import blocked, button, error_box, mono_font, row

PREVIEW = 100


class TrackPanel(QGroupBox):
    def __init__(self, page: "ReplayPage", track: ReplayTrack):
        super().__init__(track.name)
        self.page, self.track = page, track
        lay = QVBoxLayout(self)
        self.enabled = QCheckBox("Use this track")
        self.enabled.setChecked(track.enabled)
        self.enabled.toggled.connect(lambda on: setattr(track, "enabled", on))
        self.file_lbl = QLabel("no capture loaded")
        self.file_lbl.setWordWrap(True)
        lay.addWidget(row(self.enabled, button("Load capture…", self._load)))
        lay.addWidget(self.file_lbl)
        f = QFormLayout()
        self.bus = QComboBox()
        for b in ("main", "av"):
            self.bus.addItem(b, b)
        self.bus.setCurrentIndex(self.bus.findData(track.target_bus))
        self.bus.currentIndexChanged.connect(lambda _i: setattr(track, "target_bus", self.bus.currentData()))
        self.iface = QComboBox()
        self.iface.currentIndexChanged.connect(self._iface_changed)
        self.excl = QLineEdit()
        self.excl.setPlaceholderText("exclude IDs (hex, comma separated)")
        self.excl.editingFinished.connect(self._excl_changed)
        self.prot = QCheckBox("Exclude meter-origin / protected IDs")
        self.prot.setChecked(track.exclude_protected)
        self.prot.toggled.connect(self._prot_changed)
        self.start = QDoubleSpinBox()
        self.end = QDoubleSpinBox()
        for sp in (self.start, self.end):
            sp.setRange(0, 1e6)
            sp.setDecimals(3)
            sp.setSuffix(" s")
        self.end.setSpecialValueText("end")
        self.start.valueChanged.connect(lambda v: (setattr(track, "start_s", v), self._preview()))
        self.end.valueChanged.connect(lambda v: (setattr(track, "end_s", v or None), self._preview()))
        f.addRow("Replay onto bus", self.bus)
        f.addRow("Capture interface", self.iface)
        f.addRow("Exclude", self.excl)
        f.addRow("", self.prot)
        f.addRow("Window", row(self.start, QLabel("to"), self.end))
        lay.addLayout(f)
        self.preview = QTableWidget(0, 4)
        self.preview.setHorizontalHeaderLabels(["Time", "ID", "DLC", "Data"])
        self.preview.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.preview.horizontalHeader().setStretchLastSection(True)
        self.preview.setFont(mono_font(9))
        self.preview.verticalHeader().setDefaultSectionSize(17)
        lay.addWidget(QLabel(f"Preview (first {PREVIEW} frames in the window; grey = not replayed)"))
        lay.addWidget(self.preview, 1)
        self.stats = QLabel("")
        lay.addWidget(self.stats)

    def _load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load capture", os.path.join(self.page.ctx.root, "reference_logs"),
                                              CAPTURE_FILTER)
        if not path:
            return
        try:
            self.track.load(path)
        except Exception as exc:
            error_box(self, "Load capture", str(exc))
            return
        cap = self.track.capture
        self.file_lbl.setText(f"{os.path.basename(path)} — {len(cap.frames)} frames, {cap.duration:.1f} s, "
                              f"{len(cap.ids())} IDs, format {cap.fmt}")
        with blocked(self.iface):
            self.iface.clear()
            self.iface.addItem("all interfaces", None)
            for name in cap.interfaces:
                self.iface.addItem(name, name)
        self.end.setValue(0)
        self.start.setValue(0)
        self.track.interfaces = None
        self._preview()

    def _iface_changed(self, _i) -> None:
        name = self.iface.currentData()
        self.track.interfaces = {name} if name else None
        self._preview()

    def _excl_changed(self) -> None:
        ids = set()
        for tok in self.excl.text().replace(" ", ",").split(","):
            tok = tok.strip().lower().replace("0x", "")
            if tok:
                try:
                    ids.add(int(tok, 16))
                except ValueError:
                    pass
        self.track.excluded = ids
        self._preview()

    def _prot_changed(self, on: bool) -> None:
        self.track.exclude_protected = on
        self._preview()

    def _preview(self) -> None:
        t = self.track
        frames = t.frames_in_window()
        prot = self.page.protected(t.target_bus)
        self.preview.setRowCount(0)
        shown = [f for f in frames if not t.interfaces or f.bus in t.interfaces][:PREVIEW]
        self.preview.setRowCount(len(shown))
        grey = QColor("#5f6a72")
        for r, f in enumerate(shown):
            ok, why = t.frame_included(f, prot)
            vals = [f"{f.timestamp:.6f}", f.id_text, str(f.dlc), f.data_text + ("" if ok else f"   ({why})")]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if not ok:
                    it.setForeground(grey)
                self.preview.setItem(r, c, it)
        incl = sum(1 for f in frames if t.frame_included(f, prot)[0])
        self.stats.setText(f"{incl} of {len(frames)} frames in window will be replayed")

    def refresh(self) -> None:
        if self.page.ctx.replay.status.running:
            self.stats.setText(f"sent {self.track.sent}, skipped {self.track.skipped}, at {self.track.position_s:.2f} s")


class ReplayPage(BasePage):
    title = "Replay"
    tx_page = True

    def __init__(self, ctx, bridge, parent=None):
        super().__init__(ctx, bridge, parent)
        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(0, 0, 0, 0)
        panels = QHBoxLayout()
        self.panels = [TrackPanel(self, t) for t in ctx.replay.tracks]
        for p in self.panels:
            panels.addWidget(p, 1)
        lay.addLayout(panels, 1)
        self.speed = QDoubleSpinBox()
        self.speed.setRange(0.01, 100)
        self.speed.setValue(1.0)
        self.speed.setSuffix(" ×")
        self.loop = QCheckBox("Loop")
        self.pause_box = QCheckBox("Pause")
        self.pause_box.toggled.connect(ctx.replay.pause)
        self.progress = QProgressBar()
        self.state = QLabel("")
        lay.addWidget(row(button("▶ Start (all enabled tracks together)", self._start), button("■ Stop", ctx.replay.stop),
                          self.pause_box, QLabel("Speed"), self.speed, self.loop, self.state))
        lay.addWidget(self.progress)
        note = QLabel("Each track sends on its own bus with the original relative timing; both start on the same "
                      "clock. IDs being replayed take priority over background messages and the send queue for "
                      "the duration of the replay. Formats: pcapng, CSV (this app or Wireshark), candump, ASC/BLF.")
        note.setWordWrap(True)
        note.setStyleSheet("color: #9aa5ae;")
        lay.addWidget(note)

    def protected(self, bus: str) -> set[int]:
        ch = self.ctx.buses.channels.get(bus)
        return ch.protected_ids if ch else self.ctx.settings.protected(bus)

    def _start(self) -> None:
        r = self.ctx.replay
        r.speed, r.loop = self.speed.value(), self.loop.isChecked()
        try:
            r.start()
        except Exception as exc:
            error_box(self, "Replay", str(exc))

    def refresh(self) -> None:
        st = self.ctx.replay.status
        self.state.setText(("running" if st.running else "idle") + (f" — {st.message}" if st.message else "")
                           + (f" — loops {st.loops}" if st.loops else ""))
        self.progress.setMaximum(1000)
        self.progress.setValue(int(1000 * st.elapsed_s / st.duration_s) if st.duration_s else 0)
        for p in self.panels:
            p.refresh()
