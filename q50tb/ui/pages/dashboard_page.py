"""Monitoring > Visual Dashboard: virtual cluster from bus traffic.

Monitor mode decodes received frames only. Bench mode also decodes the
bench's own transmissions (software loopback), so the dashboard shows what
is being commanded even when the meter does not echo it.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QComboBox, QFormLayout, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QSlider,
                               QVBoxLayout, QWidget)

from q50tb.signals.dashboard import INDICATORS, Dashboard
from q50tb.ui.pages.base import BasePage
from q50tb.ui.widgets.common import Led, row
from q50tb.ui.widgets.gauge import Bar, CarTopView, Gauge, SteeringWheel

DARK = "QWidget#cluster { background: #0b0f13; } QLabel { color: #e8eef2; } QGroupBox { color: #9aa5ae; }"


def _big(text: str = "—", size: int = 28) -> QLabel:
    lab = QLabel(text)
    lab.setAlignment(Qt.AlignCenter)
    lab.setStyleSheet(f"font-size: {size}px; font-weight: bold;")
    return lab


class DashboardPage(BasePage):
    title = "Dashboard"

    def __init__(self, ctx, bridge, parent=None):
        super().__init__(ctx, bridge, parent)
        self.dash = Dashboard(ctx.vehicle, ctx.sniffer, ctx.settings.dashboard_units)
        lay = QVBoxLayout(self.body)
        lay.setContentsMargins(0, 0, 0, 0)
        self.source = QComboBox()
        for text, val in (("Auto (monitor: RX, bench: RX+TX)", "auto"), ("RX only", "rx"), ("RX + own TX", "tx+rx")):
            self.source.addItem(text, val)
        self.source.setCurrentIndex(max(0, self.source.findData(ctx.settings.dashboard_source)))
        self.source.currentIndexChanged.connect(self._source_changed)
        self.units = QComboBox()
        self.units.addItem("mph", "mph")
        self.units.addItem("km/h", "kmh")
        self.units.setCurrentIndex(self.units.findData(ctx.settings.dashboard_units))
        self.units.currentIndexChanged.connect(self._units_changed)
        self.mode_lbl = QLabel()
        lay.addWidget(row("Data source:", self.source, "Units:", self.units, self.mode_lbl))

        cluster = QWidget()
        cluster.setObjectName("cluster")
        cluster.setStyleSheet(DARK)
        cl = QHBoxLayout(cluster)
        left = QVBoxLayout()
        self.tach = Gauge("x1000 r/min", 0, 8000, 1000, 250, redline=6750, label_scale=0.001)
        self.coolant = Gauge("COOLANT °C", 40, 130, 30, 10, unit="", sweep=120)
        self.coolant.end_labels = ("C", "H")
        left.addWidget(self.tach, 3)
        left.addWidget(self.coolant, 1)
        cl.addLayout(left, 3)

        center = QVBoxLayout()
        self.gear = _big("—", 54)
        self.drive_mode = _big("", 16)
        self.key_prompt = QLabel("")
        self.key_prompt.setAlignment(Qt.AlignCenter)
        self.key_prompt.setWordWrap(True)
        self.key_prompt.setStyleSheet("font-size: 14px; color: #ffcc80;")
        center.addWidget(self.gear)
        center.addWidget(self.drive_mode)
        center.addWidget(self.key_prompt)
        info = QGroupBox("Driver information")
        fi = QFormLayout(info)
        self.cruise = QLabel()
        self.adas = QLabel()
        self.adas.setWordWrap(True)
        self.tcs = QLabel()
        self.chime = QLabel()
        self.dte = QLabel()
        for name, w in (("Cruise", self.cruise), ("ADAS", self.adas), ("TCS/slip", self.tcs), ("Chime", self.chime),
                        ("Range / odo", self.dte)):
            fi.addRow(name, w)
        center.addWidget(info)
        navg = QGroupBox("AV (navigation / audio / clock)")
        fn = QFormLayout(navg)
        self.nav = QLabel("—")
        self.nav.setWordWrap(True)
        self.nav_bar = Bar()
        self.audio = QLabel("—")
        self.audio.setWordWrap(True)
        self.clock = QLabel("—")
        self.pages = QLabel("—")
        fn.addRow("Nav", self.nav)
        fn.addRow("", self.nav_bar)
        fn.addRow("Audio", self.audio)
        fn.addRow("Clock", self.clock)
        fn.addRow("Pages", self.pages)
        center.addWidget(navg)
        center.addStretch(1)
        cl.addLayout(center, 3)

        right = QVBoxLayout()
        self.speed = Gauge("SPEED", 0, 180, 20, 10, unit="mph")
        self.fuel = Gauge("FUEL", 0, 100, 25, 12.5, sweep=120)
        self.fuel.end_labels = ("E", "F")
        right.addWidget(self.speed, 3)
        right.addWidget(self.fuel, 1)
        cl.addLayout(right, 3)

        side = QVBoxLayout()
        self.car = CarTopView()
        side.addWidget(self.car, 2)
        self.wheel = SteeringWheel()
        side.addWidget(self.wheel, 1)
        self.steer_slider = QSlider(Qt.Horizontal)
        self.steer_slider.setRange(-7200, 7200)
        self.steer_slider.setEnabled(False)
        self.steer_lbl = QLabel("—")
        self.steer_lbl.setAlignment(Qt.AlignCenter)
        side.addWidget(self.steer_slider)
        side.addWidget(self.steer_lbl)
        cl.addLayout(side, 2)
        lay.addWidget(cluster, 1)

        tt = QGroupBox("Telltales / warnings (grey = no data / stale)")
        tg = QGridLayout(tt)
        self.leds = {}
        for i, (key, path, label, color) in enumerate(INDICATORS):
            led = Led(color, 16)
            lab = QLabel(label)
            lab.setToolTip(path)
            tg.addWidget(led, i // 6, (i % 6) * 2)
            tg.addWidget(lab, i // 6, (i % 6) * 2 + 1)
            self.leds[key] = led
        lay.addWidget(tt)

    def _source_changed(self, _i) -> None:
        v = self.source.currentData()
        self.ctx.settings.dashboard_source = v
        self._apply_source()

    def _apply_source(self) -> None:
        v = self.ctx.settings.dashboard_source
        if v == "rx" or (v == "auto" and self.ctx.buses.monitor_only):
            self.ctx.vehicle.directions = ("RX",)
        else:
            self.ctx.vehicle.directions = ("RX", "TX")

    def _units_changed(self, _i) -> None:
        u = self.units.currentData()
        self.ctx.settings.dashboard_units = u
        self.dash.units = u
        self.speed.hi, self.speed.major, self.speed.unit = (180, 20, "mph") if u == "mph" else (280, 40, "km/h")
        self.speed.update()

    def on_bus_state(self) -> None:
        super().on_bus_state()
        self._apply_source()

    def refresh(self) -> None:
        d = self.dash
        mon = self.ctx.buses.monitor_only
        self.mode_lbl.setText(("MONITOR (RX only)" if mon else "BENCH") + f" • decoding {'+'.join(self.ctx.vehicle.directions)}")
        r = d.r("main.180.ENGINE_RPM")
        self.tach.set_value(r.value, r.stale, f"{r.value:.0f}" if r.value is not None else "")
        sp, src = d.speed()
        self.speed.set_value(sp.value, sp.stale, f"{sp.value:.0f}" if sp.value is not None else "")
        self.speed.title = "SPEED" + {"main.355.SPEED_DISPLAYED": " (meter)", "main.354.VEHICLE_SPEED": " (0x354)"}.get(src, "")
        c = d.r("main.551.COOLANT_TEMP")
        self.coolant.set_value(c.value, c.stale, f"{c.value:.0f}°C" if c.value is not None else "")
        f = d.fuel_percent()
        self.fuel.set_value(f.value, f.stale, f"{f.value:.0f}%" if f.value is not None else "")
        g = d.r("main.421.GEAR")
        self.gear.setText(g.text if g.value is not None else "—")
        self.gear.setStyleSheet(f"font-size: 54px; font-weight: bold; color: {'#6b6b6b' if g.stale else '#e8eef2'};")
        dm = d.r("main.56C.DRIVE_MODE")
        self.drive_mode.setText(dm.text if dm.value is not None else "")
        kp = d.r("main.351.KEY_PROMPT")
        self.key_prompt.setText("" if kp.value in (None, 0) else f"🔑 {kp.text}")
        cc = d.r("main.2B1.CRUISE_CONTROL")
        ccs = d.r("main.2B1.CC_SPEED")
        cce = d.r("main.2B1.CC_ENABLED")
        ci = d.r("main.551.CRUISE_ICON")
        cs = d.r("main.551.CRUISE_SET_SPEED")
        self.cruise.setText(f"2B1: {cc.text}, {'green' if cce.value else 'white'}, set {ccs.text}  •  551: {ci.text}, {cs.text}")
        parts = []
        for name in ("CAR_AHEAD", "LANE_ASSIST", "BLIND_SPOT", "EMG_BRAKE", "DISTANCE_BARS"):
            rr = d.r(f"main.2B1.{name}")
            if rr.raw:
                parts.append(f"{name.replace('_', ' ').title()}: {rr.text}")
        for name in ("LANE_WARN_LEFT", "LANE_WARN_RIGHT", "BSW_LEFT_BLINK", "BSW_RIGHT_BLINK", "EMERGENCY_BRAKE_WARNING"):
            if d.r(f"main.2B1.{name}").raw:
                parts.append(name.replace("_", " ").title())
        self.adas.setText(", ".join(parts) or "—")
        self.tcs.setText(d.r("main.354.TCS_STATE").text)
        self.chime.setText(d.r("main.60D.CHIMES").text)
        dte = d.r("main.2DE.DISTANCE_TO_EMPTY")
        odo = d.r("main.5C5.ODOMETER_RAW")
        self.dte.setText(f"DTE {dte.text}  •  odometer raw {odo.text}")
        doors = {name: d.r(p).value for name, p in (("Driver", "main.60D.DRIVER_DOOR"),
                                                    ("Passenger", "main.60D.PASSENGER_DOOR"),
                                                    ("Rear driver", "main.60D.REAR_DRIVER_DOOR"),
                                                    ("Rear passenger", "main.60D.REAR_PASSENGER_DOOR"),
                                                    ("Trunk", "main.60D.TRUNK"))}
        doors = {k: (None if v is None else bool(v)) for k, v in doors.items()}
        tires = {}
        for pos in ("FL", "FR", "RL", "RR"):
            p = d.r(f"main.385.{pos}_PRESSURE")
            tires[pos] = {"psi": p.value if not p.stale else None, "warning": bool(d.r(f"main.385.{pos}_WARNING").value),
                          "flat": bool(d.r(f"main.385.{pos}_FLAT").value),
                          "valid": bool(d.r(f"main.385.{pos}_VALID").value)}
        self.car.set_state(doors, tires)
        st = d.r("main.002.STEERING_ANGLE")
        self.wheel.set_angle(st.value, st.stale)
        if st.value is not None:
            self.steer_slider.setValue(int(st.value * 10))
            self.steer_lbl.setText(f"{st.value:+.1f}°{' (stale)' if st.stale else ''}")
        else:
            self.steer_lbl.setText("steering: no data")
        for key, path, _l, _c in INDICATORS:
            rr = d.r(path)
            self.leds[key].set_state(None if rr.value is None or rr.stale else bool(rr.value))
        av = d.av()
        if "nav" in av:
            n = av["nav"]
            self.nav.setText(f"state {n['state']:02X} maneuver {n['maneuver']:02X}  {n['distance']} {n['unit']}  "
                             f"'{n['street']}'  heading {n['compass']}  ({n['age_s']}s ago)")
            self.nav_bar.set_value(n["progress"] if n["bar"] else 0)
        if "audio" in av:
            self.audio.setText(f"{av['audio']['text']}  ({av['audio']['age_s']}s ago)")
        if "clock" in av:
            k = av["clock"]
            self.clock.setText(f"{k['hh']}:{k['mm']} mode {k['mode']:02X} ({k['age_s']}s ago)")
        if "page_image" in av:
            self.pages.setText(av["page_image"].upper())
