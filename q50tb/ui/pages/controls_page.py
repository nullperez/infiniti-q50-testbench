"""Bench Testing > Controls: drive every documented signal.

Curated tabs group the common cluster functions; the "All signals" tab is
generated from the signal database, so newly documented signals appear
without UI work. Controls only change signal bits of the background message;
the message itself is enabled/disabled on the Background Messages page.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QButtonGroup, QCheckBox, QComboBox, QFormLayout, QGridLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QPushButton, QRadioButton, QScrollArea,
                               QTabWidget, QVBoxLayout, QWidget)

from q50tb.signals.q50_main import GEARS
from q50tb.ui.pages.base import BasePage, SignalBinder, signal_tooltip
from q50tb.ui.widgets.common import button, check, confidence_badge, row

KMH_PER_MPH = 1.609344


def _scroll(w: QWidget) -> QScrollArea:
    sa = QScrollArea()
    sa.setWidgetResizable(True)
    sa.setWidget(w)
    return sa


class ControlsPage(BasePage):
    title = "Controls"
    tx_page = True

    def __init__(self, ctx, bridge, parent=None):
        super().__init__(ctx, bridge, parent)
        self.b = SignalBinder(ctx, on_error=self.status)
        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(0, 0, 0, 0)
        top = QHBoxLayout()
        self.msg_state = QLabel()
        self.msg_state.setWordWrap(True)
        top.addWidget(self.msg_state, 1)
        top.addWidget(button("Re-read values", self.b.sync_all,
                             "Reload the controls from the current background payloads"))
        lay.addLayout(top)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)
        self.tabs.addTab(_scroll(self._engine_tab()), "Engine && Speed")
        self.tabs.addTab(_scroll(self._gear_tab()), "Gear && Drive")
        self.tabs.addTab(_scroll(self._body_tab()), "Doors && Lights")
        self.tabs.addTab(_scroll(self._warnings_tab()), "Warnings")
        self.tabs.addTab(_scroll(self._key_tab()), "Key")
        self.tabs.addTab(_scroll(self._tpms_tab()), "TPMS")
        self.tabs.addTab(_scroll(self._adas_tab()), "ADAS && Cruise")
        self.tabs.addTab(_scroll(self._chassis_tab()), "Chassis && Steering")
        self.tabs.addTab(_scroll(self._hvac_tab()), "HVAC")
        self._all_tab = QWidget()
        self._all_built = False
        self.tabs.addTab(_scroll(self._all_tab), "All signals")
        self.tabs.currentChanged.connect(self._tab_changed)

    # ── tabs ────────────────────────────────────────────────────────
    def _engine_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        b = self.b
        f.addRow("Engine RPM", b.slider("main.180.ENGINE_RPM", 0, 8000, 50, suffix="rpm"))
        speed_note = QLabel("Speed moves the odometer on the meter. Keep it at 0 unless you mean it.")
        speed_note.setStyleSheet("color: #ffb300;")
        to_kmh = lambda mph: mph * KMH_PER_MPH  # noqa: E731
        f.addRow("Vehicle speed", b.slider(
            "main.354.VEHICLE_SPEED", 0, 180, 1, suffix="mph", to_phys=to_kmh, from_phys=lambda k: k / KMH_PER_MPH,
            extra_targets=[("main.284.VEHICLE_SPEED", to_kmh), ("main.284.FR_WHEEL_SPEED", to_kmh),
                           ("main.284.FL_WHEEL_SPEED", to_kmh), ("main.285.RR_WHEEL_SPEED", to_kmh),
                           ("main.285.RL_WHEEL_SPEED", to_kmh)]))
        f.addRow("", speed_note)
        f.addRow("Coolant temperature", row(b.spin("main.551.COOLANT_TEMP", -40, 215, suffix="°C"),
                                             QLabel("(raw = °C + 40)")))
        f.addRow("Accelerator (0x180)", b.slider("main.180.ACCEL_POSITION", 0, 100, 0.5, decimals=1, suffix="%"))
        f.addRow("Accelerator (0x160)", b.slider("main.160.ACCEL_POSITION", 0, 100, 0.5, decimals=1, suffix="%"))
        f.addRow("Engine torque", b.spin("main.580.ENGINE_TORQUE", 0, 1000, decimals=1))
        f.addRow("Oil temperature", row(b.spin("main.580.OIL_TEMP", -40, 215), confidence_badge("candidate")))
        f.addRow("Fuel usage counter", b.spin("main.551.FUEL_USAGE_COUNTER", 0, 255))
        f.addRow("Brake pressed", row(b.checkbox("main.182.BRAKE_PRESSED", "0x182"),
                                      b.checkbox("main.354.BRAKE_PRESSED", "0x354"),
                                      b.checkbox("main.35D.BRAKE_PRESSED", "0x35D")))
        fuel = QLabel("Fuel level is a resistive sender input on M58 pin 11 (not CAN); the meter reports it on "
                      "0x280. Use a resistor/decade box (56 Ω ≈ full).")
        fuel.setWordWrap(True)
        f.addRow("Fuel", fuel)
        return w

    def _gear_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        g = QGroupBox("Gear position (0x421)")
        gl = QHBoxLayout(g)
        self._gear_group = QButtonGroup(g)
        for text, raw in (("P", 1), ("R", 2), ("N", 3), ("D", 4), ("D (S)", 5), ("L", 6), ("None", 0)):
            rb = QRadioButton(text)
            rb.setToolTip(signal_tooltip(self.b.sdef("main.421.GEAR")))
            self._gear_group.addButton(rb, raw)
            gl.addWidget(rb)
        self.manual = QComboBox()
        self.manual.addItem("Manual…", None)
        for raw, name in sorted(GEARS.items()):
            if raw >= 0b01000:
                self.manual.addItem(name, raw)
        gl.addWidget(QLabel("  M:"))
        gl.addWidget(self.manual)
        gl.addStretch(1)
        self._gear_group.idClicked.connect(lambda raw: self.b._set_raw("main.421.GEAR", raw))
        self.manual.currentIndexChanged.connect(
            lambda _i: self.manual.currentData() is not None and self.b._set_raw("main.421.GEAR", self.manual.currentData()))
        v.addWidget(g)
        f = QFormLayout()
        f.addRow("All gear codes", self.b.combo("main.421.GEAR"))
        f.addRow("Flash gear", self.b.checkbox("main.421.FLASH_GEAR"))
        f.addRow("AT beep", self.b.checkbox("main.421.BEEP"))
        f.addRow("AT error", row(self.b.checkbox("main.421.AT_ERROR_A", "bit 2"),
                                 self.b.checkbox("main.421.AT_ERROR_B", "bit 0")))
        f.addRow("Drive mode (0x56C)", self.b.combo("main.56C.DRIVE_MODE"))
        f.addRow("ECO telltale (0x580)", self.b.checkbox("main.580.ECO_LIGHT"))
        f.addRow("Drive-mode switch raw (0x519 B0)", row(self.b.spin("main.519.STATE", 0, 255),
                                                       confidence_badge("candidate")))
        v.addLayout(f)
        v.addStretch(1)
        return w

    def _body_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        doors = QGroupBox("Doors (0x60D) - laid out like the car")
        g = QGridLayout(doors)
        g.addWidget(self.b.checkbox("main.60D.DRIVER_DOOR", "Driver (FL)"), 0, 0)
        g.addWidget(self.b.checkbox("main.60D.PASSENGER_DOOR", "Passenger (FR)"), 0, 1)
        g.addWidget(self.b.checkbox("main.60D.REAR_DRIVER_DOOR", "Rear driver (RL)"), 1, 0)
        g.addWidget(self.b.checkbox("main.60D.REAR_PASSENGER_DOOR", "Rear passenger (RR)"), 1, 1)
        g.addWidget(self.b.checkbox("main.60D.TRUNK", "Trunk"), 2, 0, 1, 2, Qt.AlignHCenter)
        g.addWidget(self.b.checkbox("main.358.TRUNK_OPEN", "Trunk (0x358)"), 3, 0, 1, 2, Qt.AlignHCenter)
        doors.setMaximumWidth(460)
        v.addWidget(doors)
        lights = QGroupBox("Lights")
        f = QFormLayout(lights)
        self.left_blink = check("Blink", lambda on: self._blink("main.60D.LEFT_TURN", on))
        self.right_blink = check("Blink", lambda on: self._blink("main.60D.RIGHT_TURN", on))
        f.addRow("Left turn", row(self.b.checkbox("main.60D.LEFT_TURN", "On"), self.left_blink))
        f.addRow("Right turn", row(self.b.checkbox("main.60D.RIGHT_TURN", "On"), self.right_blink))
        f.addRow("Hazards", row(check("Blink both", self._hazard)))
        f.addRow("Headlights", row(self.b.checkbox("main.60D.HEADLIGHTS", "0x60D"),
                                   self.b.checkbox("main.216.HEADLIGHTS", "0x216"),
                                   self.b.checkbox("main.625.HEADLIGHT", "0x625")))
        hl = QLabel("0x60D headlights with the LH/RH headlight inputs off latches 'Headlight System Error' "
                    "(clears with both inputs on, or a B+ cycle).")
        hl.setWordWrap(True)
        hl.setStyleSheet("color: #ffb300;")
        f.addRow("", hl)
        f.addRow("High beam", row(self.b.checkbox("main.60D.HIGH_BEAM", "0x60D"),
                                  self.b.checkbox("main.625.HIGH_BEAM", "0x625"),
                                  self.b.checkbox("main.351.AUTO_HIGH_BEAM", "Auto HB (0x351)")))
        f.addRow("Parking lights", row(self.b.checkbox("main.60D.PARKING_LIGHTS", "0x60D"),
                                       self.b.checkbox("main.625.PARKING_LIGHTS", "0x625")))
        f.addRow("Fog lights", row(self.b.checkbox("main.60D.FOG_LIGHTS", "Front"),
                                   self.b.checkbox("main.60D.REAR_FOG", "Rear"),
                                   self.b.checkbox("main.216.FOG_LIGHTS", "0x216")))
        f.addRow("DRL", self.b.checkbox("main.216.DRL_ON"))
        f.addRow("Dim backlight (0x358)", self.b.checkbox("main.358.DIM_BACKLIGHT"))
        f.addRow("Ignition bits (0x60D)", row(self.b.checkbox("main.60D.IG_RUN", "IG/RUN"),
                                              self.b.checkbox("main.60D.ACC", "ACC")))
        f.addRow("All doors locked", self.b.checkbox("main.60D.ALL_DOORS_LOCKED"))
        f.addRow("Chimes (0x60D)", self.b.combo("main.60D.CHIMES"))
        f.addRow("Wipers (0x625)", row(self.b.checkbox("main.625.WIPER_LOW", "Low"),
                                       self.b.checkbox("main.625.WIPERS_NOT_AT_TOP", "Not at top")))
        f.addRow("Start state (0x625)", self.b.combo("main.625.START_STATE"))
        v.addWidget(lights)
        v.addStretch(1)
        return w

    def _warnings_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        f = QFormLayout()
        b = self.b
        f.addRow("Check engine (0x551)", row(b.checkbox("main.551.MIL_SOLID", "Solid (0x20)"),
                                             b.checkbox("main.551.MIL_FLASH", "Flashing (0x40)")))
        f.addRow("Low oil pressure", row(b.checkbox("main.580.LOW_OIL_PRESSURE", "0x580 B2.0"),
                                         b.checkbox("main.358.LOW_OIL_PRESSURE", "0x358 B3.5"),
                                         QLabel("(hidden at 0 rpm)")))
        f.addRow("TPMS telltale (0x358)", b.checkbox("main.358.TPMS_LIGHT"))
        f.addRow("Loose fuel cap", b.checkbox("main.580.LOOSE_FUEL_CAP"))
        f.addRow("Key system error", b.checkbox("main.351.KEY_SYSTEM_ERROR"))
        f.addRow("Chassis error (0x56C B0.1)", row(b.checkbox("main.56C.CHASSIS_ERROR"), confidence_badge("candidate")))
        f.addRow("TCS / slip (0x354)", b.combo("main.354.TCS_STATE"))
        v.addLayout(f)
        abs_box = QGroupBox("ABS / BRAKE (0x354 byte 4) - sequence dependent")
        al = QVBoxLayout(abs_box)
        al.addWidget(row(b.checkbox("main.354.ABS", "ABS (bit 4)"), b.checkbox("main.354.ABS_LATCH", "ABS latch (bit 3)"),
                         b.checkbox("main.354.BRAKE", "BRAKE (bit 2)"), b.checkbox("main.354.BRAKE_LATCH", "BRAKE latch (bit 1)")))
        note = QLabel("Observed: 0x10 from clear did not light ABS; 0x08 lit it; 0x18 then 0x10 kept it; 0x00 cleared. "
                      "0x04 lit BRAKE only after 0x02. Parking-brake/brake-fluid inputs light BRAKE independently.")
        note.setWordWrap(True)
        al.addWidget(note)
        al.addWidget(row(button("Light ABS (08 → 18 → 10)", self._abs_seq),
                         button("Light BRAKE (02 → 06 → 04)", self._brake_seq),
                         button("Clear (00)", lambda: [b._set_raw(p, 0) for p in
                                ("main.354.ABS", "main.354.ABS_LATCH", "main.354.BRAKE", "main.354.BRAKE_LATCH")])))
        v.addWidget(abs_box)
        err = QGroupBox("ADAS errors (0x4CC)")
        eg = QGridLayout(err)
        sigs = self.ctx.db.get("main", 0x4CC).signals
        for i, s in enumerate(sigs):
            eg.addWidget(b.checkbox(s.full_name, s.name.replace("_", " ").title()), i // 3, i % 3)
        v.addWidget(err)
        v.addStretch(1)
        return w

    def _abs_seq(self):
        seq = [(0, 1), (1, 1), (1, 0)]   # (ABS bit4, ABS_LATCH bit3): 08, 18, 10
        self._sequence(seq, "main.354.ABS", "main.354.ABS_LATCH")

    def _brake_seq(self):
        seq = [(0, 1), (1, 1), (1, 0)]   # (BRAKE bit2, BRAKE_LATCH bit1): 02, 06, 04
        self._sequence(seq, "main.354.BRAKE", "main.354.BRAKE_LATCH")

    def _sequence(self, seq, hi, lo):
        def step(i=0):
            if i >= len(seq):
                self.b.sync_all()
                return
            self.b._set_raw(hi, seq[i][0])
            self.b._set_raw(lo, seq[i][1])
            self.ctx.scheduler.after(0.3, lambda: step(i + 1))
        step()

    def _key_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        b = self.b
        f.addRow("Key prompt (0x351 B5[3:0])", b.combo("main.351.KEY_PROMPT"))
        f.addRow("Prompt sound bits", row(b.checkbox("main.351.DOUBLE_BEEP", "Double beep (B5.5)"),
                                          b.checkbox("main.351.FAST_BEEP", "Fast beep (B5.4)"),
                                          b.checkbox("main.351.BEEP", "Beep (B6.7)")))
        f.addRow("Steering lock registration", b.checkbox("main.351.STEERING_LOCK_REG_COMPLETE"))
        f.addRow("Lock / unlock", row(b.checkbox("main.351.LOCK", "Lock"), b.checkbox("main.351.UNLOCK", "Unlock")))
        f.addRow("Telematics (0x56E)", row(b.checkbox("main.56E.LOCK_CMD", "Lock cmd"),
                                           b.checkbox("main.56E.UNLOCK_CMD", "Unlock cmd")))
        note = QLabel("High bits of byte 5 combine with priority: 0xC0 hid the prompt, 0x30 double beep, "
                      "0x60 key error + double beep.")
        note.setWordWrap(True)
        f.addRow("", note)
        return w

    def _tpms_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        grid = QGridLayout()
        for pos, (r, c) in {"FL": (0, 0), "FR": (0, 1), "RL": (1, 0), "RR": (1, 1)}.items():
            g = QGroupBox(f"{pos} tire")
            f = QFormLayout(g)
            f.addRow("Pressure", self.b.spin(f"main.385.{pos}_PRESSURE", 0, 63.75, 0.25, decimals=2, suffix="psi"))
            f.addRow("", self.b.checkbox(f"main.385.{pos}_WARNING", "Low pressure warning"))
            f.addRow("", self.b.checkbox(f"main.385.{pos}_FLAT", "Flat tire"))
            f.addRow("", self.b.checkbox(f"main.385.{pos}_VALID", "Valid"))
            grid.addWidget(g, r, c)
        v.addLayout(grid)
        f = QFormLayout()
        f.addRow("Target front", self.b.spin("main.385.TARGET_FRONT", 190, 340, 10, suffix="kPa"))
        f.addRow("Target rear", self.b.spin("main.385.TARGET_REAR", 190, 340, 10, suffix="kPa"))
        f.addRow("'Pressures appear while driving'", self.b.checkbox("main.385.PRESSURES_WHILE_DRIVING", "bit set (0 = message shown)"))
        f.addRow("TPMS telltale (0x358)", self.b.checkbox("main.358.TPMS_LIGHT"))
        v.addLayout(f)
        note = QLabel("Page inclusion is an AV setting (AV Controls > Pages); a low/flat warning can force the page.")
        note.setWordWrap(True)
        v.addWidget(note)
        v.addStretch(1)
        return w

    def _adas_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        b = self.b
        cc = QGroupBox("Cruise control")
        f = QFormLayout(cc)
        f.addRow("Cruise icon (0x2B1)", b.combo("main.2B1.CRUISE_CONTROL"))
        f.addRow("", row(b.checkbox("main.2B1.CC_ENABLED", "CC enabled (green)"),
                         b.checkbox("main.2B1.ADAPTIVE_CC", "Adaptive"),
                         b.checkbox("main.2B1.DISPLAY_MPH", "Display MPH"),
                         b.checkbox("main.2B1.CC_BIT4", "B0 bit 4 (unknown)")))
        f.addRow("Distance bars", b.combo("main.2B1.DISTANCE_BARS"))
        f.addRow("CC set speed (0x2B1)", row(b.spin("main.2B1.CC_SPEED", -1, 253), QLabel("(-1 = blank, 254 = '---')")))
        f.addRow("Cruise icon (0x551)", b.combo("main.551.CRUISE_ICON"))
        f.addRow("Cruise set speed (0x551)", b.spin("main.551.CRUISE_SET_SPEED", 0, 255, suffix="mph"))
        f.addRow("Cruise master (0x239)", row(b.checkbox("main.239.CRUISE_MASTER"), confidence_badge("probable")))
        v.addWidget(cc)
        ad = QGroupBox("Driver assistance (0x2B1)")
        f2 = QFormLayout(ad)
        f2.addRow("Car ahead", b.combo("main.2B1.CAR_AHEAD"))
        f2.addRow("Emergency braking", row(b.combo("main.2B1.EMG_BRAKE"), b.combo("main.2B1.EMG_BRAKE_2")))
        f2.addRow("Lane assist", row(b.combo("main.2B1.LANE_ASSIST"), b.combo("main.2B1.LANE_ASSIST_2")))
        f2.addRow("Blind spot", row(b.combo("main.2B1.BLIND_SPOT"), b.combo("main.2B1.BLIND_SPOT_2")))
        f2.addRow("Warnings", row(b.checkbox("main.2B1.LANE_WARN_LEFT", "Lane L"), b.checkbox("main.2B1.LANE_WARN_RIGHT", "Lane R"),
                                  b.checkbox("main.2B1.BSW_LEFT_BLINK", "BSW L blink"), b.checkbox("main.2B1.BSW_RIGHT_BLINK", "BSW R blink")))
        f2.addRow("", row(b.checkbox("main.2B1.EMERGENCY_BRAKE_WARNING", "Emergency brake warning"),
                          b.checkbox("main.2B1.WINDING_ROAD", "Winding road"),
                          b.checkbox("main.2B1.FEB_LIGHT", "FEB light"), b.checkbox("main.2B1.FEB_LIGHT_BLINK", "FEB blink")))
        f2.addRow("Tones", b.combo("main.2B1.TONES"))
        f2.addRow("Page", row(b.checkbox("main.2B1.PROMPT_PAGE", "Prompt ADAS page (+beep)"),
                              b.checkbox("main.2B1.SWITCH_PAGE", "Switch page"),
                              b.checkbox("main.2B1.BLINK_SPEED", "Blink speed")))
        v.addWidget(ad)
        v.addStretch(1)
        return w

    def _chassis_tab(self) -> QWidget:
        w = QWidget()
        v = QVBoxLayout(w)
        b = self.b
        st = QGroupBox("Steering (0x002)")
        f = QFormLayout(st)
        f.addRow("Steering angle", b.slider("main.002.STEERING_ANGLE", -720, 720, 1, decimals=1, suffix="°"))
        f.addRow("Steering rate", b.spin("main.002.STEERING_RATE", 0, 1020, 4, suffix="°/s"))
        v.addWidget(st)
        ch = QGroupBox("Chassis control display (0x56C)")
        g = QGridLayout(ch)
        g.addWidget(b.checkbox("main.56C.FL_WHEEL_HIGHLIGHT", "FL wheel"), 0, 0)
        g.addWidget(b.checkbox("main.56C.FR_WHEEL_HIGHLIGHT", "FR wheel"), 0, 1)
        g.addWidget(b.checkbox("main.56C.RL_WHEEL_HIGHLIGHT", "RL wheel"), 1, 0)
        g.addWidget(b.checkbox("main.56C.RR_WHEEL_HIGHLIGHT", "RR wheel"), 1, 1)
        g.addWidget(b.checkbox("main.56C.TRACE_BLUE_SQUARES", "Trace: blue squares"), 2, 0)
        g.addWidget(b.checkbox("main.56C.TRACE_2_SQUARES_WHEELS", "Trace: 2 squares wheels"), 2, 1)
        g.addWidget(b.checkbox("main.56C.TRACE_BLUE_LINES_WHEELS", "Trace: blue lines"), 3, 0)
        g.addWidget(b.checkbox("main.56C.TRACE_SADDLE_CURVE", "Trace: saddle curve"), 3, 1)
        g.addWidget(b.checkbox("main.56C.QUAD_BEEP", "Quad beep"), 4, 0)
        g.addWidget(b.checkbox("main.56C.CONTINUOUS_BEEP", "Continuous beep"), 4, 1)
        v.addWidget(ch)
        fo = QGroupBox("Forces (0x292) - units not established")
        f3 = QFormLayout(fo)
        f3.addRow("Brake pressure (raw)", b.spin("main.292.BRAKE_PRESSURE", 0, 255))
        f3.addRow("Service distance (0x51B)", row(b.spin("main.51B.SERVICE_DISTANCE", 0, 81915, 5, suffix="km"),
                                                  confidence_badge("candidate")))
        v.addWidget(fo)
        v.addStretch(1)
        return w

    def _hvac_tab(self) -> QWidget:
        w = QWidget()
        f = QFormLayout(w)
        b = self.b
        f.addRow("Driver temp (raw)", b.spin("main.54A.DRIVER_TEMP", 0, 255))
        f.addRow("Passenger temp (raw)", b.spin("main.54A.PASSENGER_TEMP", 0, 255))
        f.addRow("Air direction", b.combo("main.54B.AIR_DIRECTION"))
        f.addRow("Blower", b.combo("main.54B.BLOWER_SPEED"))
        f.addRow("", row(b.checkbox("main.54B.DUAL", "Dual"), b.checkbox("main.54B.RECIRCULATION", "Recirc"),
                         b.checkbox("main.54B.STEERING_HEATER", "Steering heater")))
        return w

    def _build_all(self) -> None:
        lay = QVBoxLayout(self._all_tab)
        flt = QLineEdit()
        flt.setPlaceholderText("filter (message or signal name)…")
        lay.addWidget(flt)
        boxes = []
        for m in self.ctx.db.by_bus("main"):
            if m.meter_origin or not m.signals:
                continue
            g = QGroupBox(f"0x{m.arbitration_id:03X}  {m.name}")
            f = QFormLayout(g)
            bg = self.ctx.background.get("main", m.arbitration_id)
            en = QCheckBox("transmit (background enabled)")
            en.setChecked(bool(bg and bg.enabled))
            en.toggled.connect(lambda on, cid=m.arbitration_id: (self.ctx.background.ensure("main", cid),
                                                                  self.ctx.background.set_enabled("main", cid, on)))
            f.addRow(row(en, button("Clear signal overrides",
                                    lambda cid=m.arbitration_id: (self.ctx.background.clear_signal("main", cid),
                                                                  self.b.sync_all()))))
            for s in m.signals:
                if s.control == "readonly":
                    continue
                if s.enum:
                    ctl = self.b.combo(s.full_name)
                elif s.bit_length == 1:
                    ctl = self.b.checkbox(s.full_name, "on")
                else:
                    ctl = self.b.spin(s.full_name)
                f.addRow(row(QLabel(s.label), confidence_badge(s.confidence)), ctl)
            lay.addWidget(g)
            boxes.append((g, m.name.lower() + f" {m.arbitration_id:03x} " + " ".join(s.name.lower() for s in m.signals)))
        lay.addStretch(1)
        flt.textChanged.connect(lambda t: [g.setVisible(t.lower() in key) for g, key in boxes])

    # ── behaviour ───────────────────────────────────────────────────
    def _tab_changed(self, i: int) -> None:
        if self.tabs.widget(i) is not None and not self._all_built and self.tabs.tabText(i) == "All signals":
            self._all_built = True
            self._build_all()

    def _blink(self, path: str, on: bool) -> None:
        if on:
            self.ctx.effects.blink(path)
        else:
            self.ctx.effects.stop(path)

    def _hazard(self, on: bool) -> None:
        for p, box in (("main.60D.LEFT_TURN", self.left_blink), ("main.60D.RIGHT_TURN", self.right_blink)):
            box.setChecked(on)

    def refresh(self) -> None:
        bg = self.ctx.background
        if bg.version != getattr(self, "_seen_version", -1):
            self._seen_version = bg.version
            self.b.sync_all(skip_focused=True)
        disabled = [f"{m.arbitration_id:03X}" for m in bg.messages.values() if not m.enabled]
        txt = f"Profile: {bg.profile_name or '—'}. "
        if not self.ctx.buses.running:
            txt += "Bus not started: controls are stored and sent once the bus starts."
        elif bg.paused:
            txt += "Background traffic is PAUSED."
        elif disabled:
            txt += f"Disabled background messages: {', '.join(disabled[:12])}{'…' if len(disabled) > 12 else ''}"
        else:
            txt += "All background messages transmitting."
        self.msg_state.setText(txt)
