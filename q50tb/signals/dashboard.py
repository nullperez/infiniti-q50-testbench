"""What the virtual cluster shows, computed from decoded bus traffic.

Every value is a ``SignalReading`` so the UI can grey out stale/missing data
instead of showing a fake zero. Used by the Visual Dashboard page and the
control API (``get_dashboard``) so agents see the same thing as the screen.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Optional

from q50tb.av.services.audio import describe_record as describe_audio
from q50tb.av.services.navigation import compass_from_heading
from q50tb.av.services.common import decode_distance4
from q50tb.av.sniffer import AvSniffer
from q50tb.core import timebase
from q50tb.signals.vehicle_state import MISSING, SignalReading, VehicleState

KMH_TO_MPH = 0.621371

# indicator name -> (signal path, label, colour)
INDICATORS = [
    ("left_turn", "main.60D.LEFT_TURN", "◀ Turn", "#20d020"),
    ("right_turn", "main.60D.RIGHT_TURN", "Turn ▶", "#20d020"),
    ("high_beam", "main.60D.HIGH_BEAM", "High beam", "#3070ff"),
    ("headlights", "main.60D.HEADLIGHTS", "Headlights", "#20d020"),
    ("parking_lights", "main.60D.PARKING_LIGHTS", "Parking lights", "#20d020"),
    ("fog", "main.60D.FOG_LIGHTS", "Fog", "#20d020"),
    ("rear_fog", "main.60D.REAR_FOG", "Rear fog", "#ffa000"),
    ("drl", "main.216.DRL_ON", "DRL", "#20d020"),
    ("mil", "main.551.MIL_SOLID", "Check engine", "#ffa000"),
    ("mil_flash", "main.551.MIL_FLASH", "Check engine (flash)", "#ffa000"),
    ("low_oil", "main.580.LOW_OIL_PRESSURE", "Oil pressure", "#ff3030"),
    ("low_oil_358", "main.358.LOW_OIL_PRESSURE", "Oil pressure (358)", "#ff3030"),
    ("tpms", "main.358.TPMS_LIGHT", "TPMS", "#ffa000"),
    ("eco", "main.580.ECO_LIGHT", "ECO", "#20d020"),
    ("abs", "main.354.ABS", "ABS hold (B4.4)", "#ffa000"),
    ("abs_latch", "main.354.ABS_LATCH", "ABS lamp (B4.3)", "#ffa000"),
    ("brake", "main.354.BRAKE", "BRAKE hold (B4.2)", "#ff3030"),
    ("brake_latch", "main.354.BRAKE_LATCH", "BRAKE lamp (B4.1)", "#ff3030"),
    ("fuel_cap", "main.580.LOOSE_FUEL_CAP", "Loose fuel cap", "#ffa000"),
    ("key_error", "main.351.KEY_SYSTEM_ERROR", "Key system", "#ffa000"),
    ("at_error", "main.421.AT_ERROR_A", "AT check", "#ffa000"),
    ("chassis_error", "main.56C.CHASSIS_ERROR", "Chassis error", "#ffa000"),
    ("feb", "main.2B1.FEB_LIGHT", "FEB", "#ffa000"),
    ("hazard", "main.3AF.HAZARD_BUTTON", "Hazard btn", "#ff3030"),
    ("trunk", "main.358.TRUNK_OPEN", "Trunk (358)", "#ff3030"),
    ("gas_light", "main.5C5.GAS_LIGHT", "Low fuel (meter)", "#ffa000"),
    ("brake_fluid", "main.5C5.LOW_BRAKE_FLUID", "Brake fluid (meter)", "#ff3030"),
    ("parking_brake", "main.5C5.PARKING_BRAKE", "Parking brake (meter)", "#ff3030"),
    ("driver_unbuckled", "main.280.DRIVER_UNBUCKLED", "Seat belt (meter)", "#ff3030"),
]


@dataclass(frozen=True)
class Telltale:
    """A cluster icon. ``on`` / ``blink`` are (signal path, raw values) pairs: values ``None`` means lit when
    the signal is non-zero, otherwise lit when the raw value is in the set. No paths = no known signal yet.
    ``hold`` maps an ``on`` path to a hold bit: while the hold bit is set, that lamp keeps its previous state.
    ``bench`` is (bench channel, channel state that lights the lamp) for meter inputs wired to the bench."""
    key: str
    label: str
    color: str
    on: tuple = ()
    blink: tuple = ()
    hold: tuple = ()
    bench: Optional[tuple] = None


_TCS_SLIP = frozenset({1, 3, 4, 5, 6, 7})
_TCS_SLIP_BLINK = frozenset({3, 4, 6, 7})
_TCS_OFF = frozenset({1, 2, 6, 7})
GREEN, BLUE, AMBER, RED = "#20d020", "#3070ff", "#ffa000", "#ff3030"

# icon key -> what lights it (keys are also the icon names in ui/widgets/telltales.py)
TELLTALES = [
    Telltale("left_turn", "Left turn", GREEN, (("main.60D.LEFT_TURN", None),)),
    Telltale("high_beam", "High beam", BLUE, (("main.60D.HIGH_BEAM", None),)),
    Telltale("headlights", "Headlights", GREEN, (("main.60D.HEADLIGHTS", None),)),
    Telltale("parking_lights", "Parking lights / DRL", GREEN,
             (("main.60D.PARKING_LIGHTS", None), ("main.216.DRL_ON", None))),
    Telltale("fog", "Front fog", GREEN, (("main.60D.FOG_LIGHTS", None),)),
    Telltale("rear_fog", "Rear fog", AMBER, (("main.60D.REAR_FOG", None),)),
    Telltale("auto_high_beam", "Auto high beam", GREEN, (("main.351.AUTO_HIGH_BEAM", None),)),
    Telltale("eco", "ECO", GREEN, (("main.580.ECO_LIGHT", None),)),
    # one lamp for the parking brake switch, low brake fluid switch (both as the meter reports them) and the
    # 0x354 BRAKE lamp request
    Telltale("parking_brake", "Brake (parking brake / brake fluid / 0x354 BRAKE lamp)", RED,
             (("main.5C5.PARKING_BRAKE", None), ("main.5C5.LOW_BRAKE_FLUID", None), ("main.354.BRAKE_LATCH", None)),
             hold=(("main.354.BRAKE_LATCH", "main.354.BRAKE"),)),
    Telltale("abs", "ABS", AMBER, (("main.354.ABS_LATCH", None),), hold=(("main.354.ABS_LATCH", "main.354.ABS"),)),
    Telltale("slip", "Slip indicator (TCS_STATE)", AMBER, (("main.354.TCS_STATE", _TCS_SLIP),),
             (("main.354.TCS_STATE", _TCS_SLIP_BLINK),)),
    Telltale("tcs_off", "TCS OFF (TCS_STATE)", AMBER, (("main.354.TCS_STATE", _TCS_OFF),)),
    Telltale("tpms", "Low tire pressure", AMBER, (("main.358.TPMS_LIGHT", None),)),
    Telltale("mil", "Check engine (solid/flash)", AMBER,
             (("main.551.MIL_SOLID", None), ("main.551.MIL_FLASH", None)), (("main.551.MIL_FLASH", None),)),
    Telltale("oil", "Oil pressure", RED, (("main.580.LOW_OIL_PRESSURE", None), ("main.358.LOW_OIL_PRESSURE", None))),
    # hardwired meter inputs driven by the bench (owner): ALTERNATOR checked lights the lamp, AIRBAG checked
    # turns it off
    Telltale("charge", "Charge / alternator (bench ALTERNATOR input)", RED, bench=("ALTERNATOR", True)),
    Telltale("airbag", "Airbag (bench AIRBAG input)", RED, bench=("AIRBAG", False)),
    Telltale("seat_belt", "Seat belt (meter)", RED, (("main.280.DRIVER_UNBUCKLED", None),)),
    Telltale("low_fuel", "Low fuel (meter)", AMBER, (("main.5C5.GAS_LIGHT", None),)),
    Telltale("power_steering", "Power steering", AMBER, (("main.5E4.POWER_STEERING_WARNING", None),)),
    Telltale("key", "Key system", AMBER, (("main.351.KEY_SYSTEM_ERROR", None),)),
    Telltale("feb", "Forward emergency braking", AMBER,
             (("main.2B1.FEB_LIGHT", None), ("main.2B1.FEB_LIGHT_BLINK", None)), (("main.2B1.FEB_LIGHT_BLINK", None),)),
    Telltale("right_turn", "Right turn", GREEN, (("main.60D.RIGHT_TURN", None),)),
]
TELLTALE_PATHS = ({path for t in TELLTALES for path, _v in t.on + t.blink}
                  | {hold for t in TELLTALES for _p, hold in t.hold})

DOORS = [("Driver", "main.60D.DRIVER_DOOR"), ("Passenger", "main.60D.PASSENGER_DOOR"),
         ("Rear driver", "main.60D.REAR_DRIVER_DOOR"), ("Rear passenger", "main.60D.REAR_PASSENGER_DOOR"),
         ("Trunk", "main.60D.TRUNK")]
DOOR_TEXT = {"Driver": "Driver Door", "Passenger": "Passenger Door", "Rear driver": "Rear Driver Door",
             "Rear passenger": "Rear Passenger Door", "Trunk": "Trunk"}

WHITE = "#e8eef2"


@dataclass(frozen=True)
class LcdMessage:
    """A message on the meter's centre display. ``icon`` is a telltale icon key, 'warning' or 'doors'."""
    text: str
    color: str
    sub: str = ""
    icon: str = "warning"
    source: str = ""
    doors: tuple = ()


# 0x351 KEY_PROMPT codes the meter shows (bench 2026-10-12; 2, 12, 13 show nothing) -> (text, colour, icon)
KEY_PROMPT_LCD = {
    0x1: ("Brake + Push Start", WHITE, "key"), 0x3: ("Key ID Incorrect", AMBER, "key"),
    0x4: ("Turn Steering Wheel + Push Button", WHITE, "key"), 0x5: ("Shift to Park", RED, "warning"),
    0x6: ("Power turned off to save battery", WHITE, "key"), 0x7: ("Key Battery Low", AMBER, "key"),
    0x8: ("No key detected", AMBER, "key"), 0x9: ("Power will turn off to save battery", WHITE, "key"),
    0xA: ("Push Ignition to OFF", WHITE, "key"), 0xB: ("Clutch + Push Start", WHITE, "key"),
    0xE: ("Touch Key to Start Button", WHITE, "key"), 0xF: ("Push Brake and Start Button to Drive", WHITE, "key"),
}
_COLOR_RANK = {RED: 0, AMBER: 1, WHITE: 2}

ADAS_FIELDS = ["CRUISE_CONTROL", "CC_ENABLED", "DISTANCE_BARS", "ADAPTIVE_CC", "CAR_AHEAD", "LANE_WARN_LEFT",
               "LANE_WARN_RIGHT", "BSW_LEFT_BLINK", "BSW_RIGHT_BLINK", "EMERGENCY_BRAKE_WARNING", "WINDING_ROAD",
               "EMG_BRAKE", "LANE_ASSIST", "BLIND_SPOT", "EMG_BRAKE_2", "LANE_ASSIST_2", "BLIND_SPOT_2",
               "CC_SPEED", "TONES", "PROMPT_PAGE"]


@dataclass
class Dashboard:
    vehicle: VehicleState
    sniffer: Optional[AvSniffer] = None
    units: str = "mph"
    _lamps: dict = field(default_factory=dict, repr=False)  # last lamp state per path, for hold bits
    # bench hardwired-input states (BenchIO.states); None or {} when no bench is connected
    bench_inputs: Optional[Callable[[], dict]] = None

    def r(self, path: str) -> SignalReading:
        return self.vehicle.read(path)

    def telltale(self, t: Telltale) -> tuple[Optional[bool], bool]:
        """(lit, blinking); lit is None when none of its signals has fresh data."""
        if t.bench:
            channel, lit_when = t.bench
            state = self._bench().get(channel)
            return (None if state is None else state == lit_when), False
        holds = dict(t.hold)

        def one(path, values) -> Optional[bool]:
            r = self.r(path)
            if not r.valid:
                return None
            state = bool(r.value) if values is None else r.raw in values
            if path in holds:
                h = self.r(holds[path])
                if h.valid and h.value and path in self._lamps:
                    return self._lamps[path]
                self._lamps[path] = state
            return state

        def lit(pairs) -> Optional[bool]:
            states = [s for s in (one(p, v) for p, v in pairs) if s is not None]
            return any(states) if states else None
        on = lit(t.on)
        return on, bool(on and lit(t.blink))

    def _on(self, path: str) -> bool:
        r = self.r(path)
        return r.valid and bool(r.value)

    def _bench(self) -> dict:
        try:
            return (self.bench_inputs() if self.bench_inputs else None) or {}
        except Exception:
            return {}

    def lcd_messages(self) -> list[LcdMessage]:
        """Messages the meter's centre display shows for the current bus state, red first, then amber, white."""
        out: list[LcdMessage] = []
        rpm = self.r("main.180.ENGINE_RPM")
        running = rpm.valid and rpm.value > 0
        speed, _src = self.speed()
        moving = speed.valid and speed.value > 0

        open_doors = [name for name, p in DOORS if self._on(p)]
        if self._on("main.358.TRUNK_OPEN") and "Trunk" not in open_doors:
            open_doors.append("Trunk")
        if open_doors:
            if len(open_doors) == 1:
                text, sub = f"{DOOR_TEXT[open_doors[0]]} Open", ""
            else:
                text, sub = f"{len(open_doors)} Doors Open", ", ".join(DOOR_TEXT[d] for d in open_doors)
            out.append(LcdMessage(text, RED if moving else AMBER, sub, "doors", "0x60D doors, 0x358 TRUNK_OPEN",
                                  tuple(open_doors)))

        kp = self.r("main.351.KEY_PROMPT")
        if kp.valid and kp.raw in KEY_PROMPT_LCD:
            text, color, icon = KEY_PROMPT_LCD[kp.raw]
            out.append(LcdMessage(text, color, "", icon, f"0x351 KEY_PROMPT {kp.raw:X}"))

        if self._on("main.358.LOW_OIL_PRESSURE"):
            out.append(LcdMessage("Low Oil Pressure", RED, "Stop Vehicle", "oil", "0x358 LOW_OIL_PRESSURE"))
        flat = [pos for pos in ("FL", "FR", "RL", "RR") if self._on(f"main.385.{pos}_FLAT")]
        if flat:
            out.append(LcdMessage("Flat Tire", RED, "Visit dealer", "tpms", f"0x385 FLAT {' '.join(flat)}"))
        low = [pos for pos in ("FL", "FR", "RL", "RR") if self._on(f"main.385.{pos}_WARNING") and pos not in flat]
        if low:
            out.append(LcdMessage("Tire Pressure Low", AMBER, "Add Air", "tpms", f"0x385 WARNING {' '.join(low)}"))

        if running and self._on("main.421.AT_ERROR_B"):
            out.append(LcdMessage("AT Error", AMBER, "See Owner's Manual", "warning", "0x421 AT_ERROR_B (RPM > 0)"))
        elif running and self._on("main.421.AT_ERROR_A"):
            out.append(LcdMessage("AT Error", AMBER, "", "warning", "0x421 AT_ERROR_A (RPM > 0)"))
        if self._on("main.56C.CHASSIS_ERROR"):
            out.append(LcdMessage("Chassis Control System Error", AMBER, "", "warning", "0x56C CHASSIS_ERROR"))
        if self._on("main.351.KEY_SYSTEM_ERROR"):
            out.append(LcdMessage("Key System Error", AMBER, "See Owner's Manual", "key", "0x351 KEY_SYSTEM_ERROR"))
        if self._on("main.351.STEERING_LOCK_REG_COMPLETE"):
            out.append(LcdMessage("Steering Lock Registration Complete", WHITE, "", "key",
                                  "0x351 STEERING_LOCK_REG_COMPLETE"))
        return sorted(out, key=lambda m: _COLOR_RANK.get(m.color, 3))

    def speed(self) -> tuple[SignalReading, str]:
        """Vehicle speed: meter's displayed speed if present (monitor), else 0x354."""
        for path in ("main.355.SPEED_DISPLAYED", "main.354.VEHICLE_SPEED", "main.284.VEHICLE_SPEED"):
            r = self.r(path)
            if r.value is not None and not r.stale:
                break
        else:
            r = self.r("main.354.VEHICLE_SPEED")
            path = "main.354.VEHICLE_SPEED"
        if r.value is not None and self.units == "mph":
            r = SignalReading(r.value * KMH_TO_MPH, r.raw, f"{r.value * KMH_TO_MPH:.0f} mph", r.age, r.stale, r.direction)
        return r, path

    def fuel_percent(self) -> SignalReading:
        r = self.r("main.280.FUEL_LEVEL_NORM")
        if r.value is None:
            return r
        pct = max(0.0, min(100.0, r.value / 1023.0 * 100.0))
        return SignalReading(pct, r.raw, f"{pct:.0f}% (raw {r.raw}, uncalibrated)", r.age, r.stale, r.direction)

    def av(self) -> dict:
        out = {}
        if not self.sniffer:
            return out
        s = self.sniffer.state
        now = timebase.now()
        if s.nav:
            b = s.nav
            n = b[15] if len(b) > 15 else 0
            out["nav"] = {"state": b[2], "maneuver": b[3], "bar": b[4], "progress": b[5],
                          "distance": decode_distance4(b[8:12]), "unit": {3: "mi", 8: "ft"}.get(b[12], ""),
                          "heading": b[13], "compass": compass_from_heading(b[13]),
                          "street": b[16:16 + n].decode("utf-16-le", "replace"), "age_s": round(now - s.nav_t, 1)}
        if s.audio:
            out["audio"] = {"text": describe_audio(s.audio), "age_s": round(now - s.audio_t, 1)}
        if s.clock and len(s.clock) >= 5:
            hh = s.clock[3]
            out["clock"] = {"mode": s.clock[2], "hh": f"{hh & 0xF}" if hh >> 4 == 0xF else f"{hh:02X}",
                            "mm": f"{s.clock[4]:02X}", "hour24": bool(s.clock[2] & 1),
                            "age_s": round(now - s.clock_t, 1)}
        if s.page_image:
            out["page_image"] = s.page_image.hex(" ")
        return out

    def summary(self) -> dict:
        def val(r: SignalReading):
            return {"value": None if r.value is None else round(r.value, 3), "text": r.text, "stale": r.stale,
                    "age_s": None if r.age is None else round(r.age, 2)}
        speed, speed_src = self.speed()
        out = {
            "rpm": val(self.r("main.180.ENGINE_RPM")),
            "speed": {**val(speed), "units": self.units, "source": speed_src},
            "coolant_c": val(self.r("main.551.COOLANT_TEMP")),
            "fuel_pct": val(self.fuel_percent()),
            "gear": val(self.r("main.421.GEAR")),
            "flash_gear": val(self.r("main.421.FLASH_GEAR")),
            "steering_deg": val(self.r("main.002.STEERING_ANGLE")),
            "key_prompt": val(self.r("main.351.KEY_PROMPT")),
            "drive_mode": val(self.r("main.56C.DRIVE_MODE")),
            "tcs": val(self.r("main.354.TCS_STATE")),
            "chime": val(self.r("main.60D.CHIMES")),
            "cruise": {"icon_551": val(self.r("main.551.CRUISE_ICON")),
                       "set_speed_551": val(self.r("main.551.CRUISE_SET_SPEED"))},
            "doors": {name: val(self.r(p)) for name, p in DOORS},
            "indicators": {k: val(self.r(p)) for k, p, _l, _c in INDICATORS},
            "telltales": {t.key: dict(zip(("lit", "blinking"), self.telltale(t))) for t in TELLTALES},
            "lcd_messages": [{"text": m.text, "sub": m.sub, "color": m.color, "source": m.source}
                             for m in self.lcd_messages()],
            "tpms": {pos: {"psi": val(self.r(f"main.385.{pos}_PRESSURE")),
                           "warning": val(self.r(f"main.385.{pos}_WARNING")),
                           "flat": val(self.r(f"main.385.{pos}_FLAT")),
                           "valid": val(self.r(f"main.385.{pos}_VALID"))} for pos in ("FL", "FR", "RL", "RR")},
            "adas": {f: val(self.r(f"main.2B1.{f}")) for f in ADAS_FIELDS},
            "odometer_raw": val(self.r("main.5C5.ODOMETER_RAW")),
            "distance_to_empty_km": val(self.r("main.2DE.DISTANCE_TO_EMPTY")),
            "av": self.av(),
        }
        return out
