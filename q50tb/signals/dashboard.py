"""What the virtual cluster shows, computed from decoded bus traffic.

Every value is a ``SignalReading`` so the UI can grey out stale/missing data
instead of showing a fake zero. Used by the Visual Dashboard page and the
control API (``get_dashboard``) so agents see the same thing as the screen.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

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
    ("abs", "main.354.ABS", "ABS (B4.4)", "#ffa000"),
    ("abs_latch", "main.354.ABS_LATCH", "ABS (B4.3)", "#ffa000"),
    ("brake", "main.354.BRAKE", "BRAKE (B4.2)", "#ff3030"),
    ("brake_latch", "main.354.BRAKE_LATCH", "BRAKE (B4.1)", "#ff3030"),
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

DOORS = [("Driver", "main.60D.DRIVER_DOOR"), ("Passenger", "main.60D.PASSENGER_DOOR"),
         ("Rear driver", "main.60D.REAR_DRIVER_DOOR"), ("Rear passenger", "main.60D.REAR_PASSENGER_DOOR"),
         ("Trunk", "main.60D.TRUNK")]

ADAS_FIELDS = ["CRUISE_CONTROL", "CC_ENABLED", "DISTANCE_BARS", "ADAPTIVE_CC", "CAR_AHEAD", "LANE_WARN_LEFT",
               "LANE_WARN_RIGHT", "BSW_LEFT_BLINK", "BSW_RIGHT_BLINK", "EMERGENCY_BRAKE_WARNING", "WINDING_ROAD",
               "EMG_BRAKE", "LANE_ASSIST", "BLIND_SPOT", "EMG_BRAKE_2", "LANE_ASSIST_2", "BLIND_SPOT_2",
               "CC_SPEED", "TONES", "PROMPT_PAGE"]


@dataclass
class Dashboard:
    vehicle: VehicleState
    sniffer: Optional[AvSniffer] = None
    units: str = "mph"

    def r(self, path: str) -> SignalReading:
        return self.vehicle.read(path)

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
            "steering_deg": val(self.r("main.002.STEERING_ANGLE")),
            "key_prompt": val(self.r("main.351.KEY_PROMPT")),
            "drive_mode": val(self.r("main.56C.DRIVE_MODE")),
            "tcs": val(self.r("main.354.TCS_STATE")),
            "chime": val(self.r("main.60D.CHIMES")),
            "cruise": {"icon_551": val(self.r("main.551.CRUISE_ICON")),
                       "set_speed_551": val(self.r("main.551.CRUISE_SET_SPEED"))},
            "doors": {name: val(self.r(p)) for name, p in DOORS},
            "indicators": {k: val(self.r(p)) for k, p, _l, _c in INDICATORS},
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
