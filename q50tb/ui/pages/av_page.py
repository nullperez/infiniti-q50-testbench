"""Bench Testing > AV Controls: head-unit emulation towards the meter."""

from __future__ import annotations

import datetime as dt
import json

from PySide6.QtCore import QDateTime, Qt
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QDateTimeEdit, QDoubleSpinBox,
                               QFileDialog, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QHeaderView, QLabel,
                               QLineEdit, QPushButton, QSlider, QSpinBox, QStackedWidget, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from q50tb.av.headunit import READY
from q50tb.av.services.navigation import COMPASS16, RouteStep, compass_from_heading, heading_from_compass
from q50tb.av.services.settings import LANGUAGES, PAGES, UNITS
from q50tb.core.bus_manager import AV, MAIN
from q50tb.ui.pages.base import BasePage
from q50tb.ui.widgets.common import Led, blocked, button, check, confidence_badge, confirm, error_box, row

STATE_COLORS = {"ready": "#2e7d32", "waiting": "#616161", "announced": "#f9a825", "negotiating": "#fb8c00",
                "lost": "#c62828", "stopped": "#424242"}


class AvPage(BasePage):
    title = "AV Controls"
    tx_page = True

    def __init__(self, ctx, bridge, parent=None):
        super().__init__(ctx, bridge, parent)
        self.hu = ctx.hu
        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(0, 0, 0, 0)

        # master switch + bus selector
        top = QGroupBox("Head-unit emulation")
        tl = QGridLayout(top)
        self.enable = QCheckBox("ENABLE AV CONTROLS (emulate head unit)")
        self.enable.setStyleSheet("QCheckBox { font-weight: bold; font-size: 13px; }")
        self.enable.toggled.connect(self._toggle)
        tl.addWidget(self.enable, 0, 0, 1, 2)
        self.bus_combo = QComboBox()
        self.bus_combo.setMinimumWidth(280)
        tl.addWidget(QLabel("AV bus adapter (not the main bus):"), 1, 0)
        tl.addWidget(self.bus_combo, 1, 1)
        tl.addWidget(button("Open AV bus", self._open_bus), 1, 2)
        tl.addWidget(button("Refresh", self._fill_buses), 1, 3)
        self.bus_label = QLabel()
        tl.addWidget(self.bus_label, 2, 0, 1, 4)
        hint = QLabel("Channels open when the meter announces (00 20). The meter re-announces until answered; "
                      "if a channel stays 'waiting', keep this enabled and cycle the meter power (Hardware page). "
                      "ACC (tied to IG on the bench) must be powered for AV communication.")
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #9aa5ae;")
        tl.addWidget(hint, 3, 0, 1, 4)
        lay.addWidget(top)

        # channel table
        self.chan_table = QTableWidget(0, 6)
        self.chan_table.setHorizontalHeaderLabels(["Channel", "HU → peer", "State", "Peer seen", "Queue", "TX/RX/fail"])
        self.chan_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.chan_table.horizontalHeader().setStretchLastSection(True)
        self.chan_table.setMaximumHeight(170)
        self.chan_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        lay.addWidget(self.chan_table)

        self.gate = QWidget()        # everything below is gated by the master switch
        gl = QVBoxLayout(self.gate)
        gl.setContentsMargins(0, 0, 0, 0)
        tabs = QTabWidget()
        tabs.addTab(self._pages_tab(), "Pages")
        tabs.addTab(self._prefs_tab(), "Preferences")
        tabs.addTab(self._audio_tab(), "Audio")
        tabs.addTab(self._clock_tab(), "Clock")
        tabs.addTab(self._alerts_tab(), "Alerts")
        tabs.addTab(self._nav_tab(), "Navigation")
        gl.addWidget(tabs)
        lay.addWidget(self.gate, 1)
        self.gate.setEnabled(False)
        self.hu.nav.on_player_change = lambda: self.bridge.changed.emit("nav-player")
        self._fill_buses()

    # ── master / bus ────────────────────────────────────────────────
    def _fill_buses(self) -> None:
        keep = self.bus_combo.currentData() or self.ctx.settings.av_adapter
        self.bus_combo.clear()
        main = self.ctx.buses.channels.get(MAIN)
        main_key = main.adapter.info.key if main else self.ctx.settings.main_adapter
        for a in self.ctx.adapters or self.ctx.refresh_adapters():
            if a.key != main_key:
                self.bus_combo.addItem(a.describe(), a.key)
        i = self.bus_combo.findData(keep)
        if i >= 0:
            self.bus_combo.setCurrentIndex(i)

    def _open_bus(self) -> None:
        if not self.ctx.buses.running:
            error_box(self, "AV bus", "Start the main bus first (top bar). Choosing the AV adapter in the top bar "
                                      "before 'Start bus' opens both together.")
            return
        self._fill_buses()
        key = self.bus_combo.currentData()
        if not key:
            error_box(self, "AV bus", "No adapter left for the AV bus (the main bus uses the only one). Press ⟳ in "
                                      "the top bar to look for adapters.")
            return
        try:
            self.ctx.open_av_bus(key)
            self.ctx.settings.av_adapter = key
            if not self.hu.running:
                self.hu.start(AV)       # opening the bus from this page means "talk to the meter"
        except Exception as exc:
            error_box(self, "AV bus", str(exc))

    def _toggle(self, on: bool) -> None:
        try:
            if on:
                if self.ctx.buses.get(AV) is None:
                    raise RuntimeError("AV bus is not open. Choose an adapter and press 'Open AV bus'.")
                self.hu.start(AV)
            else:
                self.hu.stop()
        except Exception as exc:
            with blocked(self.enable):
                self.enable.setChecked(False)
            error_box(self, "AV emulation", str(exc))
        self.gate.setEnabled(self.hu.running)

    # ── pages ───────────────────────────────────────────────────────
    def _pages_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        info = QLabel("The meter's page list is a 9-byte image (C0 21 read → C0 2C 00 S0..S8). Each change reads the "
                      "live image, flips one mask and writes it; success only if the meter echoes the exact image. "
                      "Main-bus 0x51C + 0x4F6 must be running for AV-backed page slots to exist.")
        info.setWordWrap(True)
        v.addWidget(info)
        g = QGridLayout()
        self.page_checks: dict[str, QCheckBox] = {}
        self.page_results: dict[str, QLabel] = {}
        for i, (name, (byte, mask)) in enumerate(PAGES.items()):
            cb = QCheckBox(name)
            cb.setToolTip(f"S{byte} mask 0x{mask:02X}")
            cb.clicked.connect(lambda on, n=name: self._set_page(n, on))
            res = QLabel("—")
            res.setStyleSheet("color: #9aa5ae;")
            self.page_checks[name], self.page_results[name] = cb, res
            g.addWidget(cb, i, 0)
            g.addWidget(QLabel(f"S{byte} & 0x{mask:02X}"), i, 1)
            g.addWidget(res, i, 2)
        v.addLayout(g)
        self.image_label = QLabel("Live image: (not read)")
        self.image_label.setStyleSheet("font-family: monospace;")
        v.addWidget(self.image_label)
        v.addWidget(row(button("Read settings from meter", self.hu.settings.read_all),
                        button("Enable all pages", lambda: self._all_pages(True)),
                        button("Disable all pages", lambda: self._all_pages(False))))
        v.addStretch(1)
        return w

    def _set_page(self, name: str, on: bool) -> None:
        self.page_results[name].setText("pending…")
        self.hu.settings.set_page(name, on, lambda ok, why, n=name: self.bridge.changed.emit(
            f"page|{n}|{'OK' if ok else 'FAIL'}|{why}"))

    def _all_pages(self, on: bool) -> None:
        """One read-modify-write for every page; each page is judged from the image the meter echoes."""
        for name in PAGES:
            self.page_results[name].setText("pending…")
        s = self.hu.settings

        def done(ok: bool, why: str) -> None:
            for n in PAGES:
                actual = s.page_enabled(n)
                if actual == on:
                    self.bridge.changed.emit(f"page|{n}|OK|{'confirmed' if ok else 'matches the meter image'}")
                else:
                    kept = why if actual is None else f"meter kept it {'on' if actual else 'off'}"
                    self.bridge.changed.emit(f"page|{n}|FAIL|{kept}")
        s.set_pages({n: on for n in PAGES}, done)

    # ── preferences ─────────────────────────────────────────────────
    def _prefs_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        s = self.hu.settings
        self.effect = check("Display Effect (startup animation)",
                            lambda on: s.set_display_effect(on, self._pref_done("Display effect")))
        f.addRow(row(self.effect, confidence_badge("candidate"), QLabel("write format unverified")))
        self.temp_alert = check("Outside Temperature Alert (~38 °F)",
                                lambda on: s.set_alerts(on, None, self._pref_done("Outside temp alert")))
        f.addRow(row(self.temp_alert, confidence_badge("candidate"), QLabel("item 8 byte 0")))
        self.nav_alert = check("Navigation alert (item 8 byte 1)",
                               lambda on: s.set_alerts(self.temp_alert.isChecked(), on, self._pref_done("Nav alert")))
        f.addRow(row(self.nav_alert, confidence_badge("candidate")))
        self.lang = QComboBox()
        for code, name in LANGUAGES.items():
            self.lang.addItem(name, code)
        self.lang.activated.connect(lambda _i: s.set_language(self.lang.currentData(), self._pref_done("Language")))
        f.addRow("Meter language", self.lang)
        self.units = QComboBox()
        for code, name in UNITS.items():
            self.units.addItem(name, code)
        self.units.activated.connect(lambda _i: s.set_units(self.units.currentData(), self._pref_done("Pressure unit")))
        f.addRow("Tire pressure unit", row(self.units, confidence_badge("confirmed")))
        reset = QPushButton("Factory reset meter settings…")
        reset.setStyleSheet("QPushButton { background: #8e1b1b; color: white; padding: 6px; }")
        reset.clicked.connect(self._factory_reset)
        f.addRow(reset)
        self.pref_result = QLabel("")
        self.pref_result.setWordWrap(True)
        f.addRow("Result", self.pref_result)
        return w

    def _pref_done(self, what: str):
        return lambda ok, why: self.bridge.changed.emit(f"pref|{what}|{'OK' if ok else 'FAIL'}|{why}")

    def _factory_reset(self) -> None:
        if not confirm(self, "Factory reset", "Reset ALL meter settings to factory defaults?\n\n"
                       "This is the head unit's two-step confirmation in one dialog. It sends C4 00.", danger=True):
            return
        self.hu.settings.factory_reset(self._pref_done("Factory reset"))

    # ── audio ───────────────────────────────────────────────────────
    def _audio_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        a = self.hu.audio
        self.src = QComboBox()
        for s in a.sources:
            self.src.addItem(f"{s.label}  [{s.confidence}]", s.key)
        self.src.currentIndexChanged.connect(self._src_changed)
        v.addWidget(row("Source:", self.src, check("0x61D source select", lambda on: setattr(a, "send_source_select", on),
                                                   True), button("Reload registry", self._reload_sources)))
        self.src_info = QLabel()
        self.src_info.setWordWrap(True)
        self.src_info.setStyleSheet("color: #9aa5ae;")
        v.addWidget(self.src_info)
        self.fields = QStackedWidget()
        # radio
        rw = QWidget()
        rf = QFormLayout(rw)
        self.freq = QDoubleSpinBox()
        self.freq.setRange(0, 9999)
        self.freq.setDecimals(1)
        self.preset = QSpinBox()
        self.preset.setRange(0, 255)
        self.tuned = QCheckBox("Tuned / valid")
        self.tuned.setChecked(True)
        self.station = QLineEdit()
        self.station.setPlaceholderText("FM: 8-char RDS PS name; SXM: channel name")
        self.radio_text = QLineEdit()
        self.radio_text.setPlaceholderText("radio text / genre (second line)")
        rf.addRow("Frequency / channel", self.freq)
        rf.addRow("Preset (0 = none)", self.preset)
        rf.addRow("", self.tuned)
        rf.addRow("Station", self.station)
        rf.addRow("Text 2", self.radio_text)
        # media
        mw = QWidget()
        mf = QFormLayout(mw)
        self.title_e, self.album_e, self.artist_e = QLineEdit(), QLineEdit(), QLineEdit()
        self.playing = QCheckBox("Playing")
        self.playing.setChecked(True)
        self.elapsed = QSpinBox()
        self.elapsed.setRange(0, 255)
        mf.addRow("Title (slot 1, shown)", self.title_e)
        mf.addRow("Album (slot 2, hidden)", self.album_e)
        mf.addRow("Artist (slot 3, shown)", self.artist_e)
        mf.addRow("", self.playing)
        mf.addRow("Elapsed byte", self.elapsed)
        # aux / other
        xw = QLabel("No metadata for this source (AUX sends 628 26 0D 00 00 02).")
        self.fields.addWidget(rw)
        self.fields.addWidget(mw)
        self.fields.addWidget(xw)
        v.addWidget(self.fields)
        self.auto_audio = check("Send automatically on change", checked=False)
        v.addWidget(row(button("Select source + send", self._audio_select), button("Send metadata", self._audio_push),
                        self.auto_audio))
        for wdg in (self.station, self.radio_text, self.title_e, self.album_e, self.artist_e):
            wdg.editingFinished.connect(self._audio_auto)
        for wdg in (self.freq, self.preset, self.elapsed):
            wdg.valueChanged.connect(self._audio_auto)
        for wdg in (self.tuned, self.playing):
            wdg.toggled.connect(self._audio_auto)
        v.addStretch(1)
        self._src_changed()
        return w

    def _reload_sources(self) -> None:
        self.hu.audio.reload()
        self.src.clear()
        for s in self.hu.audio.sources:
            self.src.addItem(f"{s.label}  [{s.confidence}]", s.key)

    def _src_changed(self) -> None:
        key = self.src.currentData()
        if not key:
            return
        s = self.hu.audio.source(key)
        self.src_info.setText(f"61D 20 20 {s.select.hex(' ').upper()}  •  {s.evidence}")
        self.fields.setCurrentIndex({"radio": 0, "media": 1}.get(s.kind, 2))
        if s.kind == "radio":
            with blocked(self.freq):
                self.freq.setDecimals(1 if s.decimal else 0)
                self.freq.setValue(s.default_freq)

    def _audio_fields(self) -> dict:
        s = self.hu.audio.source(self.src.currentData())
        if s.kind == "radio":
            return dict(frequency=self.freq.value(), preset=self.preset.value(), tuned=self.tuned.isChecked(),
                        station=self.station.text(), radio_text=self.radio_text.text())
        if s.kind == "media":
            return dict(title=self.title_e.text(), album=self.album_e.text(), artist=self.artist_e.text(),
                        playing=self.playing.isChecked(), elapsed=self.elapsed.value())
        return {}

    def _audio_select(self) -> None:
        self.hu.audio.select(self.src.currentData(), **self._audio_fields())

    def _audio_push(self) -> None:
        if self.hu.audio.state.source != self.src.currentData():
            self._audio_select()
        else:
            self.hu.audio.update(**self._audio_fields())

    def _audio_auto(self, *_):
        if self.auto_audio.isChecked() and self.hu.running:
            self._audio_push()

    # ── clock ───────────────────────────────────────────────────────
    def _clock_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        c = self.hu.clock
        self.dt_edit = QDateTimeEdit(QDateTime.currentDateTime())
        self.dt_edit.setDisplayFormat("yyyy-MM-dd HH:mm:ss")
        self.dt_edit.setCalendarPopup(True)
        f.addRow("Date / time", row(self.dt_edit, button("Use computer time", self._clock_pc),
                                    button("Set time on meter", self._clock_set)))
        self.h24 = check("24-hour clock", lambda on: setattr(c.state, "hour24", on), c.state.hour24)
        f.addRow(self.h24)
        f.addRow(check("Resend 0x603 clock every minute", lambda on: setattr(c.state, "auto_send", on), c.state.auto_send))
        f.addRow(row(check("Also stream date/time on 0x61D (24 4D, 1 Hz)",
                           lambda on: setattr(c.state, "send_datetime_61d", on), c.state.send_datetime_61d),
                     confidence_badge("candidate")))
        f.addRow(check("Mode bit 1 (normally set)", lambda on: setattr(c.state, "flag_bit1", on), c.state.flag_bit1))
        self.clock_preview = QLabel()
        self.clock_preview.setStyleSheet("font-family: monospace;")
        f.addRow("Next record", self.clock_preview)
        note = QLabel("0x603 40 0D <mode> <HH> <MM>: mode bit0 = 24 h display; hours < 10 use a blank tens digit (F9 = 9).")
        note.setWordWrap(True)
        f.addRow(note)
        return w

    def _clock_pc(self) -> None:
        self.dt_edit.setDateTime(QDateTime.currentDateTime())
        self.hu.clock.use_pc_time()

    def _clock_set(self) -> None:
        self.hu.clock.state.hour24 = self.h24.isChecked()
        self.hu.clock.set_time(self.dt_edit.dateTime().toPython())

    # ── alerts ──────────────────────────────────────────────────────
    def _alerts_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        b = QPushButton("Display DRIVER BREAK ALERT")
        b.setMinimumHeight(40)
        b.clicked.connect(self.hu.settings.driver_break_alert)
        v.addWidget(b)
        note = QLabel("Sends 0x628 'C4 2D' (notify). Seen in DRIVERBREAK.pcapng and MAYBE_break_warning.pcapng.")
        note.setWordWrap(True)
        v.addWidget(note)
        v.addStretch(1)
        return w

    # ── navigation ──────────────────────────────────────────────────
    def _nav_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        nav = self.hu.nav
        g = QGroupBox("Instruction")
        f = QFormLayout(g)
        self.nav_state = QComboBox()
        for s in nav.states():
            self.nav_state.addItem(f"{s['label']}", s["code"])
        self.maneuver = QComboBox()
        self.maneuver.setEditable(False)
        for m in nav.maneuvers():
            self.maneuver.addItem(f"{m['code']:02X}  {m['label']}", m["code"])
        self.man_code = QSpinBox()
        self.man_code.setRange(0, 255)
        self.man_code.setDisplayIntegerBase(16)
        self.man_code.setPrefix("0x")
        self.maneuver.currentIndexChanged.connect(lambda _i: self.man_code.setValue(self.maneuver.currentData() or 0))
        self.street = QLineEdit()
        self.street.setMaxLength(32)
        self.dist = QDoubleSpinBox()
        self.dist.setRange(0, 99999)
        self.dist.setDecimals(1)
        self.dist.setValue(0.5)
        self.dist_unit = QComboBox()
        self.dist_unit.addItem("mi", 3)
        self.dist_unit.addItem("ft", 8)
        self.progress = QSlider(Qt.Horizontal)
        self.progress.setRange(0, 100)
        self.prog_lbl = QLabel("0%")
        self.progress.valueChanged.connect(lambda x: self.prog_lbl.setText(f"{x}%"))
        self.heading = QComboBox()
        for name in COMPASS16:
            self.heading.addItem(name, heading_from_compass(name))
        self.heading_raw = QSpinBox()
        self.heading_raw.setRange(1, 32)
        self.heading.currentIndexChanged.connect(lambda _i: self.heading_raw.setValue(self.heading.currentData()))
        f.addRow("State", self.nav_state)
        f.addRow("Maneuver", row(self.maneuver, QLabel("code"), self.man_code, button("Edit registry…", self._open_registry)))
        f.addRow("Street name", self.street)
        f.addRow("Distance", row(self.dist, self.dist_unit))
        f.addRow("Progress (approaching only)", row(self.progress, self.prog_lbl))
        f.addRow("Heading (idle compass)", row(self.heading, QLabel("raw 1-32"), self.heading_raw, confidence_badge("candidate")))
        f.addRow(row(button("Send instruction", self._nav_send), button("Idle (compass only)", self._nav_idle)))
        v.addWidget(g)

        rg = QGroupBox("Route simulator (queue instructions and play them)")
        rl = QVBoxLayout(rg)
        self.route = QTableWidget(0, 8)
        self.route.setHorizontalHeaderLabels(["Maneuver", "Street", "Distance", "Unit", "Heading", "Duration s",
                                              "Approach at", "Hold s"])
        self.route.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        rl.addWidget(self.route)
        self.route_loop = QCheckBox("Loop")
        self.route_status = QLabel("idle")
        rl.addWidget(row(button("Add current instruction", self._route_add), button("Remove selected", self._route_remove),
                         button("Up", lambda: self._route_move(-1)), button("Down", lambda: self._route_move(1)),
                         button("Load…", self._route_load), button("Save…", self._route_save)))
        rl.addWidget(row(button("▶ Play route", self._route_play), button("■ Stop", self.hu.nav.stop), self.route_loop,
                         self.route_status))
        v.addWidget(rg, 1)
        self._route_seed()
        return w

    def _open_registry(self) -> None:
        import os
        from q50tb.av.services.navigation import REGISTRY
        path = os.path.abspath(REGISTRY)
        error_box(self, "Maneuver registry", f"Edit {path}\nthen restart the app (or reload) to pick up new labels.")

    def _nav_values(self) -> dict:
        return dict(state=self.nav_state.currentData(), maneuver=self.man_code.value(), street=self.street.text(),
                    distance=self.dist.value(), unit=self.dist_unit.currentData(), progress=self.progress.value(),
                    heading=self.heading_raw.value())

    def _nav_send(self) -> None:
        self.hu.nav.update(**self._nav_values())

    def _nav_idle(self) -> None:
        self.hu.nav.idle(self.heading_raw.value())

    def _route_seed(self) -> None:
        for step in (RouteStep(3, "HIGHWAY 121 / TX-121", 1.2, 3, heading_from_compass("N"), 15, 0.3, 2),
                     RouteStep(4, "THOMAS CHAPEL DR", 600, 8, heading_from_compass("E"), 10, 300, 2),
                     RouteStep(5, "CUSTER ST", 0.5, 3, heading_from_compass("S"), 12, 0.2, 2)):
            self._route_append(step)

    def _route_append(self, s: RouteStep) -> None:
        r = self.route.rowCount()
        self.route.insertRow(r)
        vals = [f"{s.maneuver:02X}", s.street, f"{s.distance:g}", "mi" if s.unit == 3 else "ft",
                compass_from_heading(s.heading), f"{s.duration_s:g}", f"{s.approach_at:g}", f"{s.hold_s:g}"]
        for c, val in enumerate(vals):
            self.route.setItem(r, c, QTableWidgetItem(val))

    def _route_steps(self) -> list[RouteStep]:
        out = []
        for r in range(self.route.rowCount()):
            t = lambda c: (self.route.item(r, c).text() if self.route.item(r, c) else "").strip()  # noqa: E731
            h = t(4)
            out.append(RouteStep(int(t(0) or "0", 16), t(1), float(t(2) or 0), 3 if t(3) != "ft" else 8,
                                 int(h) if h.isdigit() else heading_from_compass(h or "N"), float(t(5) or 10),
                                 float(t(6) or 0.3), float(t(7) or 2)))
        return out

    def _route_add(self) -> None:
        v = self._nav_values()
        self._route_append(RouteStep(v["maneuver"], v["street"], v["distance"], v["unit"], v["heading"], 15,
                                     0.3 if v["unit"] == 3 else 300, 2))

    def _route_remove(self) -> None:
        for r in sorted({i.row() for i in self.route.selectedIndexes()}, reverse=True):
            self.route.removeRow(r)

    def _route_move(self, d: int) -> None:
        r = self.route.currentRow()
        steps = self._route_steps()
        if 0 <= r < len(steps) and 0 <= r + d < len(steps):
            steps[r], steps[r + d] = steps[r + d], steps[r]
            self.route.setRowCount(0)
            for s in steps:
                self._route_append(s)
            self.route.selectRow(r + d)

    def _route_save(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "Save route", "route.json", "JSON (*.json)")
        if path:
            with open(path, "w", encoding="utf-8") as fh:
                json.dump([s.to_json() for s in self._route_steps()], fh, indent=1)

    def _route_load(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, "Load route", "", "JSON (*.json)")
        if path:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
            self.route.setRowCount(0)
            for d in data:
                self._route_append(RouteStep(**d))

    def _route_play(self) -> None:
        if not self.hu.channel_ready("nav"):
            error_box(self, "Route", "The navigation channel (629/6A9) is not ready yet.")
            return
        self.hu.nav.play(self._route_steps(), self.route_loop.isChecked())

    # ── refresh ─────────────────────────────────────────────────────
    def on_bus_state(self) -> None:
        super().on_bus_state()
        self._fill_buses()
        if not self.hu.running:
            with blocked(self.enable):
                self.enable.setChecked(False)
            self.gate.setEnabled(False)

    def handle_bridge(self, msg: str) -> None:
        parts = msg.split("|")
        if parts[0] == "page" and len(parts) >= 4:
            lab = self.page_results.get(parts[1])
            if lab:
                lab.setText(f"{'✔' if parts[2] == 'OK' else '✖'} {parts[3]}")
                lab.setStyleSheet(f"color: {'#66bb6a' if parts[2] == 'OK' else '#ef5350'};")
        elif parts[0] == "pref" and len(parts) >= 4:
            self.pref_result.setText(f"{parts[1]}: {'✔' if parts[2] == 'OK' else '✖'} {parts[3]}")

    def refresh(self) -> None:
        if self.enable.isChecked() != self.hu.running:
            with blocked(self.enable):
                self.enable.setChecked(self.hu.running)
        self.gate.setEnabled(self.hu.running)
        st = self.hu.status()
        av = self.ctx.buses.channels.get(AV)
        self.bus_label.setText(f"AV bus: {av.adapter.info.describe() if av else 'not open'}"
                               f"{'  (MONITOR)' if av and av.monitor_only else ''}")
        rows = list(st["channels"].items())
        self.chan_table.setRowCount(len(rows))
        for r, (name, c) in enumerate(rows):
            vals = [name, f"{c['hu_id']} → {c['peer_id']}" + ("" if c["has_peer"] else " (no peer)"),
                    c["state"] if c["enabled"] else "disabled",
                    "—" if c["peer_seen_s_ago"] is None else f"{c['peer_seen_s_ago']:.1f}s ago",
                    str(c["queue"]), "/".join(str(c["stats"].get(k, 0)) for k in ("tx_msgs", "rx_msgs", "failed"))]
            for col, val in enumerate(vals):
                it = self.chan_table.item(r, col)
                if it is None:
                    it = QTableWidgetItem()
                    self.chan_table.setItem(r, col, it)
                it.setText(val)
                if col == 2:
                    it.setForeground(QColor(STATE_COLORS.get(c["state"], "#9e9e9e")) if c["enabled"] else QColor("#555"))
        img = self.hu.settings.page_image
        self.image_label.setText("Live image: " + (img.hex(" ").upper() if img else "(not read)"))
        for name, cb in self.page_checks.items():
            on = self.hu.settings.page_enabled(name)
            with blocked(cb):
                cb.setChecked(bool(on))
            cb.setStyleSheet("" if on is not None else "color: #777;")
        items = self.hu.settings.state.items
        if items[0xA].value and len(items[0xA].value) >= 2:
            i = self.units.findData(items[0xA].value[1])
            if i >= 0 and not self.units.hasFocus():
                with blocked(self.units):
                    self.units.setCurrentIndex(i)
        if items[0xC].value and len(items[0xC].value) >= 2:
            i = self.lang.findData(items[0xC].value[1])
            if i >= 0 and not self.lang.hasFocus():
                with blocked(self.lang):
                    self.lang.setCurrentIndex(i)
        if items[0xE].value:
            with blocked(self.effect):
                self.effect.setChecked(bool(items[0xE].value[0]))
        if items[0x8].value and len(items[0x8].value) >= 2:
            with blocked(self.temp_alert), blocked(self.nav_alert):
                self.temp_alert.setChecked(bool(items[0x8].value[0]))
                self.nav_alert.setChecked(bool(items[0x8].value[1]))
        self.clock_preview.setText(self.hu.clock.clock_record().hex(" ").upper())
        p = self.hu.nav.player
        self.route_status.setText(f"{'playing' if p.running else 'idle'}"
                                  f"{f' step {p.step_index + 1}/{len(p.steps)} ({p.phase})' if p.running else ''}"
                                  f"  record: {self.hu.nav.state.record()[:16].hex(' ').upper()}")
