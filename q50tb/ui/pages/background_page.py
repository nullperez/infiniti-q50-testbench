"""Bench Testing > Default (background) messages."""

from __future__ import annotations

import os

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFileDialog,
                               QFormLayout, QGridLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QSpinBox,
                               QTableWidget, QTableWidgetItem, QVBoxLayout)

from q50tb.capture.formats import load_capture
from q50tb.sim.background import BackgroundMessage
from q50tb.sim.baseline_builder import build_messages
from q50tb.ui.pages.base import BasePage
from q50tb.ui.widgets.common import button, confirm, error_box, mono_font, row
from q50tb.ui.widgets.hex_edit import HexBytesEdit

COLS = ["On", "Bus", "ID", "Name", "Period ms", "Payload (live)", "Overrides", "Generators", "TX", "Status"]


class OverrideDialog(QDialog):
    """Per-byte override editor; counter/checksum bytes are marked."""

    def __init__(self, msg: BackgroundMessage, parent=None):
        super().__init__(parent)
        self.msg = msg
        self.setWindowTitle(f"Override 0x{msg.arbitration_id:03X} {msg.name}")
        lay = QVBoxLayout(self)
        lay.addWidget(QLabel("Tick a byte to override it. Counter/checksum bytes keep running unless "
                             "'override generators' is ticked."))
        grid = QGridLayout()
        self.checks, self.spins = [], []
        prot = msg.protected_bytes()
        payload = msg.last_payload or msg.base
        for i in range(msg.dlc):
            cb = QCheckBox(f"B{i}" + (" (gen)" if i in prot else ""))
            sp = QSpinBox()
            sp.setRange(0, 255)
            sp.setDisplayIntegerBase(16)
            sp.setPrefix("0x")
            cur = msg.overrides[i] if i < len(msg.overrides) else None
            cb.setChecked(cur is not None)
            sp.setValue(cur if cur is not None else payload[i] if i < len(payload) else 0)
            grid.addWidget(cb, 0, i)
            grid.addWidget(sp, 1, i)
            self.checks.append(cb)
            self.spins.append(sp)
        lay.addLayout(grid)
        self.gen = QCheckBox("Override generators too (freeze counters/checksums on overridden bytes)")
        self.gen.setChecked(msg.override_counters)
        lay.addWidget(self.gen)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel | QDialogButtonBox.Reset)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        bb.button(QDialogButtonBox.Reset).clicked.connect(lambda: [c.setChecked(False) for c in self.checks])
        lay.addWidget(bb)

    def apply(self) -> None:
        for i, (cb, sp) in enumerate(zip(self.checks, self.spins)):
            self.msg.set_override(i, sp.value() if cb.isChecked() else None)
        self.msg.override_counters = self.gen.isChecked()


class AddDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add background message")
        f = QFormLayout(self)
        self.bus = QLineEdit("main")
        self.cid = QLineEdit()
        self.name = QLineEdit()
        self.period = QDoubleSpinBox()
        self.period.setRange(1, 60000)
        self.period.setValue(100)
        self.payload = HexBytesEdit(8)
        self.payload.set_data(bytes(8))
        f.addRow("Bus", self.bus)
        f.addRow("ID (hex)", self.cid)
        f.addRow("Name", self.name)
        f.addRow("Period (ms)", self.period)
        f.addRow("Payload", self.payload)
        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        f.addRow(bb)


class BackgroundPage(BasePage):
    title = "Background Messages"
    tx_page = True

    def __init__(self, ctx, bridge, parent=None):
        super().__init__(ctx, bridge, parent)
        bg = ctx.background
        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(0, 0, 0, 0)
        self.profile_lbl = QLabel()
        lay.addWidget(self.profile_lbl)
        self.pause = QCheckBox("Pause all background traffic")
        self.pause.toggled.connect(bg.set_paused)
        lay.addWidget(row(button("Enable all", lambda: bg.set_all_enabled(True)),
                          button("Disable all", lambda: bg.set_all_enabled(False)), self.pause,
                          button("Override selected…", self._override), button("Clear overrides", self._clear_ov),
                          button("Add…", self._add), button("Remove selected", self._remove)))
        lay.addWidget(row(button("Load profile…", self._load), button("Save profile as…", self._save),
                          button("Build profile from capture…", self._build),
                          button("Reload default profile", self._reload_default)))
        self.filter = QLineEdit()
        self.filter.setPlaceholderText("filter by ID or name…")
        lay.addWidget(self.filter)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(3, QHeaderView.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setFont(mono_font(9))
        self.table.verticalHeader().setDefaultSectionSize(18)
        self.table.cellClicked.connect(self._clicked)
        self.table.cellDoubleClicked.connect(self._double)
        self.table.itemChanged.connect(self._edited)
        lay.addWidget(self.table, 1)
        note = QLabel("Double-click a payload to override bytes; edit the period cell to change the rate. "
                      "Controls (Controls page) change signal fields on these messages; IDs used by the send "
                      "queue or a replay are paused here automatically (one transmitter per ID).")
        note.setWordWrap(True)
        note.setStyleSheet("color: #9aa5ae;")
        lay.addWidget(note)
        self._keys: list = []
        self._updating = False

    def _selected(self) -> list[BackgroundMessage]:
        rows = sorted({i.row() for i in self.table.selectedIndexes()})
        return [self.ctx.background.messages[self._keys[r]] for r in rows if r < len(self._keys)
                and self._keys[r] in self.ctx.background.messages]

    def _clicked(self, r: int, c: int) -> None:
        if c == 0 and r < len(self._keys):
            m = self.ctx.background.messages.get(self._keys[r])
            if m:
                self.ctx.background.set_enabled(m.bus, m.arbitration_id, not m.enabled)

    def _double(self, r: int, c: int) -> None:
        if c in (5, 6):
            self.table.selectRow(r)
            self._override()

    def _edited(self, item: QTableWidgetItem) -> None:
        if self._updating or item.column() != 4:
            return
        r = item.row()
        m = self.ctx.background.messages.get(self._keys[r])
        try:
            self.ctx.background.set_period(m.bus, m.arbitration_id, float(item.text()))
        except ValueError:
            pass

    def _override(self) -> None:
        sel = self._selected()
        if not sel:
            return
        dlg = OverrideDialog(sel[0], self)
        if dlg.exec():
            dlg.apply()

    def _clear_ov(self) -> None:
        for m in self._selected() or list(self.ctx.background.messages.values()):
            m.clear_overrides()

    def _add(self) -> None:
        dlg = AddDialog(self)
        if not dlg.exec():
            return
        try:
            m = BackgroundMessage(int(dlg.cid.text(), 16), dlg.name.text() or f"0x{dlg.cid.text()}", dlg.bus.text(),
                                  dlg.period.value(), dlg.payload.data())
            self.ctx.background.add(m)
        except ValueError as exc:
            error_box(self, "Add", str(exc))

    def _remove(self) -> None:
        for m in self._selected():
            self.ctx.background.remove(m.bus, m.arbitration_id)

    def _load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load background profile", os.path.join(self.ctx.root, "profiles"),
                                              "Profiles (*.json)")
        if path:
            try:
                self.ctx.background.load_profile(path)
                self.ctx.settings.background_profile = path
            except Exception as exc:
                error_box(self, "Load profile", str(exc))

    def _reload_default(self) -> None:
        from q50tb.core.config import AppSettings
        self.ctx.background.load_profile(AppSettings().background_profile)

    def _save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save background profile", os.path.join(self.ctx.root, "profiles"),
                                              "Profiles (*.json)")
        if path:
            name, _ = QInputDialog.getText(self, "Profile name", "Name:", text=os.path.splitext(os.path.basename(path))[0])
            self.ctx.background.save_profile(path, name)

    def _build(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Capture to learn from", "", "Captures (*.pcapng *.csv *.log)")
        if not path:
            return
        try:
            cap = load_capture(path)
            iface = None
            if len(cap.interfaces) > 1:
                iface, ok = QInputDialog.getItem(self, "Interface", "Which interface is the main bus?",
                                                 cap.interfaces, 0, False)
                if not ok:
                    return
            excl = set(self.ctx.settings.protected("main")) | set(range(0x700, 0x800))
            msgs = build_messages(cap, interface=iface, bus="main", exclude=excl, db=self.ctx.db)
        except Exception as exc:
            error_box(self, "Build profile", str(exc))
            return
        if not confirm(self, "Replace profile", f"Learned {len(msgs)} messages from {os.path.basename(path)}.\n"
                       "Replace the current background messages (save it first if needed)?"):
            return
        bg = self.ctx.background
        for key in list(bg.messages):
            bg.remove(*key)
        for m in msgs:
            bg.add(m)
        bg.profile_name = f"learned from {os.path.basename(path)}"

    def refresh(self) -> None:
        bg = self.ctx.background
        self.profile_lbl.setText(f"Profile: <b>{bg.profile_name}</b>  ({len(bg.messages)} messages, "
                                 f"{sum(m.enabled for m in bg.messages.values())} enabled)"
                                 f"{'  — PAUSED' if bg.paused else ''}")
        flt = self.filter.text().strip().lower()
        msgs = [m for m in sorted(bg.messages.values(), key=lambda m: (m.bus, m.arbitration_id))
                if not flt or flt in f"{m.arbitration_id:03x}" or flt in m.name.lower()]
        self._keys = [m.key for m in msgs]
        self._updating = True
        try:
            self.table.setRowCount(len(msgs))
            for r, m in enumerate(msgs):
                gens = []
                if m.counters:
                    gens.append("ctr " + ",".join(f"B{c.byte}[{c.lsb + c.width - 1}:{c.lsb}]" for c in m.counters))
                if m.checksums:
                    gens.append("cs " + ",".join(f"B{c.byte} {c.kind}" for c in m.checksums))
                if m.sequence:
                    gens.append(f"seq×{len(m.sequence)}")
                if m.overlays:
                    gens.append("signals: " + ",".join(m.overlays))
                ov = " ".join("--" if v is None else f"{v:02X}" for v in m.overrides) if m.has_override else ""
                vals = ["●" if m.enabled else "○", m.bus, f"{m.arbitration_id:03X}", m.name, f"{m.period_ms:g}",
                        (m.last_payload or m.base).hex(" ").upper(), ov, "; ".join(gens), str(m.tx_count),
                        m.status or ("" if m.enabled else "disabled")]
                for c, v in enumerate(vals):
                    it = self.table.item(r, c)
                    if it is None:
                        it = QTableWidgetItem()
                        flags = Qt.ItemIsSelectable | Qt.ItemIsEnabled
                        if c == 4:
                            flags |= Qt.ItemIsEditable
                        it.setFlags(flags)
                        self.table.setItem(r, c, it)
                    if c == 4 and self.table.currentItem() is it and self.table.state() == QAbstractItemView.EditingState:
                        continue
                    if it.text() != v:
                        it.setText(v)
                    if c == 0:
                        it.setForeground(QColor("#66bb6a") if m.enabled else QColor("#777"))
                    if c == 6 and m.has_override:
                        it.setForeground(QColor("#ffb300"))
        finally:
            self._updating = False
