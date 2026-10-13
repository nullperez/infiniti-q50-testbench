"""Main-bus signal database for the 2019 Q50 combination meter bench.

Source: owner's README ("Infiniti Q50 CAN Bus Decoding") plus the correction
register in Q50_Observed_Signals_and_Behaviors.md and capture analysis done
for this app (marked ``observed``/``candidate`` in notes). Bytes are
zero-based, bit 0 is the LSB. Where the README and captures disagree the
note says so; nothing here is an OEM specification.

To add a signal: append a SignalDef to the message (or a new MessageDef) and
it appears automatically in the Controls "All signals" tab, the dashboard
decoder, the background-message overlay and the DBC export.
"""

from __future__ import annotations

from q50tb.signals.model import (CANDIDATE, CONFIRMED, DOCUMENTED, PROBABLE, MessageDef, Seg,
                                 SignalDatabase, SignalDef, be16, le16, seg)

S = SignalDef


def bit(name, byte, b, **kw):
    return S(name, (Seg(byte, b, b),), **kw)


def field(name, byte, msb, lsb, **kw):
    return S(name, (Seg(byte, msb, lsb),), **kw)


def byte_(name, byte, **kw):
    return S(name, (Seg(byte),), **kw)


ADAS_STATE = {0: "Disabled", 1: "Enabled", 2: "White blink (warning icon)", 3: "Yellow solid (warning icon)"}
ADAS_STATE2 = {0: "Disabled", 1: "Green enabled", 2: "Green blink (warning icon)", 3: "Yellow solid (warning icon)"}

KEY_PROMPTS = {
    0x0: "None", 0x1: "Brake + Push Start", 0x2: "No visible response (0x2)",
    0x3: "Key ID Incorrect", 0x4: "Turn Steering Wheel + Push Button", 0x5: "Shift to Park",
    0x6: "Power turned off to save battery", 0x7: "Key Battery Low", 0x8: "No key detected",
    0x9: "Power will turn off to save battery", 0xA: "Push Ignition to OFF",
    0xB: "Clutch + Push Start (shared firmware)", 0xC: "No visible response (0xC)",
    0xD: "No visible response (0xD)", 0xE: "Touch Key to Start Button",
    0xF: "Push Brake and Start Button to Drive",
}

GEARS = {
    0b00000: "None", 0b00001: "P", 0b00010: "R", 0b00011: "N", 0b00100: "D", 0b00101: "D (S)",
    0b00110: "L", 0b01000: "1", 0b01001: "2", 0b01010: "3", 0b01011: "4", 0b01100: "5",
    0b01101: "6", 0b10000: "M1", 0b10001: "M2", 0b10010: "M3", 0b10011: "M4", 0b10100: "M5",
    0b10101: "M6", 0b10110: "M7", 0b10111: "M8",
}

TCS_STATE = {0: "None", 1: "TCS OFF solid + slip solid", 2: "TCS OFF solid", 3: "Slip blinking (not seen on bench)",
             4: "Slip blinking", 5: "Slip solid", 6: "TCS OFF + slip blinking", 7: "TCS OFF + slip blinking"}

CHIMES = {0b00000: "None", 0b00001: "Headlight reminder", 0b10000: "Door open chime",
          0b10010: "Single short high-pitch beep", 0b10011: "Periodic quad beep",
          0b10101: "Startup beep", 0b11000: "Long constant beep", 0b11001: "Long constant beep (0x19)"}

DRIVE_MODES = {0: "Standard", 1: "Sport", 2: "Eco", 3: "Snow", 4: "Personal", 5: "Sport Plus"}

TONES_2B1 = {0: "None", 1: "Constant beep", 2: "Quick beep", 3: "Triple fast beep",
             4: "Fast single beep", 5: "Quad beep", 6: "Single beep", 7: "Double beep"}

CRUISE_ICON_551 = {0x0: "None", 0x1: "Green up-arrow CC", 0x2: "White up-arrow CC",
                   0x3: "Green up-arrow blink / no speed", 0x4: "Green speed-set CC",
                   0x5: "White speed-set CC", 0x6: "Green speed-set blink / no speed",
                   0x9: "Green up-arrow, numeric speed blinking",
                   0xC: "Green speed-set, numeric speed blinking"}


def _messages() -> list[MessageDef]:
    m: list[MessageDef] = []

    m.append(MessageDef(0x002, "Steering Angle", period_ms=10, signals=[
        S("STEERING_ANGLE", le16(0), factor=-0.1, signed=True, unit="deg", minimum=-720, maximum=720,
          label="Steering angle", notes="signed little-endian, factor -0.1 deg (README)."),
        byte_("STEERING_RATE", 2, factor=4, unit="deg/s", label="Steering rate", confidence=PROBABLE,
              notes="absolute rate, factor ~4 deg/s"),
        field("COUNTER", 4, 3, 0, control="readonly", confidence=PROBABLE,
              notes="README says byte 3 [3:0]; idle capture shows the rolling nibble in byte 4 [3:0]."),
        byte_("CHECKSUM", 7, control="readonly", confidence=PROBABLE,
              notes="observed: B7 = XOR of all nibbles of B0..B6 (captures)."),
    ]))

    m.append(MessageDef(0x02A, "SCM status", dlc=6, period_ms=100, signals=[
        bit("SCM_SYSTEM_ERROR", 4, 0, confidence=CONFIRMED, label="SCM system error",
            notes="bench sweep 2026-10-12 (camera): '! Warning / SCM system error / Visit dealer' + amber master "
                  "warning; clears with the bit. Other bits of byte 4 and 0xFF/0x00 in bytes 0-3, 5 showed "
                  "nothing; stopping 0x02A for 10 s showed nothing."),
    ]))

    m.append(MessageDef(0x160, "Accelerator Pedal", period_ms=10, signals=[
        bit("PEDAL_FULLY_PRESSED", 0, 6, confidence=CANDIDATE),
        bit("ENGINE_STOPPED", 0, 3, confidence=PROBABLE, notes="engine-state flag (all real-car captures incl. 60 s IG-on/engine-off: 1 off, 0 running with the engine, one change at start and stop)"),
        bit("ENGINE_RUNNING", 6, 3, confidence=PROBABLE, notes="engine-state flag (all real-car captures incl. 60 s IG-on/engine-off: 0 off, 1 running with the engine, one change at start and stop)"),
        byte_("ACCEL_POSITION", 3, factor=0.5, unit="%", maximum=100, minimum=0, label="Accelerator position"),
        byte_("ACCEL_POSITION_INV", 5, notes="0xFF - byte 3", control="readonly"),
    ]))

    m.append(MessageDef(0x180, "Engine RPM", period_ms=10, signals=[
        S("ENGINE_RPM", be16(0), factor=0.125, unit="rpm", minimum=0, maximum=8000, confidence=CONFIRMED,
          label="Engine RPM", notes="raw = rpm * 8, big-endian (bench confirmed). Setting it also sets the "
          "other RPM copies (0x1F9, 0x3EC, 0x176); 0x3EC wins on the meter when present (bench 2026-10-12)."),
        byte_("ACCEL_POSITION", 5, factor=0.5, unit="%", minimum=0, maximum=100),
        field("COUNTER", 6, 3, 0, control="readonly",
              notes="README: byte 6 counter; only the low nibble rolls in captures (upper nibble kept)."),
    ]))

    m.append(MessageDef(0x182, "Engine 2", period_ms=10, signals=[
        bit("COAST_CAND", 1, 7, confidence=CANDIDATE, label="Coasting (pedal released while moving)",
            notes="captures: set only while moving with the accelerator released (2931 of 2983 samples moving); "
                  "deceleration fuel cut?"),
        byte_("THROTTLE_CAND", 4, confidence=CANDIDATE, label="Throttle-like value",
              notes="captures: follows the accelerator (r 0.94 vs 0x180 B5), 0xFF at full throttle; not calibrated"),
        field("COUNTER", 5, 3, 0, control="readonly"),
        bit("ENGINE_STOPPED", 5, 5, confidence=PROBABLE, notes="engine-state flag (all real-car captures incl. 60 s IG-on/engine-off: 1 off, 0 running with the engine, one change at start and stop)"),
        bit("BRAKE_PRESSED", 6, 6, label="Brake pressed"),
    ]))

    m.append(MessageDef(0x174, "Transmission (gear / shift)", period_ms=10, signals=[
        byte_("SHIFT_TORQUE_CAND", 0, confidence=CANDIDATE, label="Shift request value",
              notes="captures: 0xFF except during gear changes (100% of activity within 0.5 s of a shift); "
                    "torque reduction request?"),
        field("GEAR_ACTUAL", 3, 7, 4, enum={**{g: str(g) for g in range(1, 8)}, 9: "R", 10: "P/N"},
              confidence=PROBABLE, label="Transmission gear (actual)",
              notes="captures: P/N -> 0xA, R -> 0x9, D -> 1..7 (7-speed AT), M2..M5 -> 2..5"),
        field("GEAR_TARGET", 3, 3, 0, enum={**{g: str(g) for g in range(1, 8)}, 9: "R", 10: "P/N"},
              confidence=PROBABLE, label="Transmission gear (target)",
              notes="captures: differs from GEAR_ACTUAL only during a shift (e.g. 0x34 = 3 -> 4)"),
        field("COUNTER", 4, 3, 0, control="readonly", confidence=PROBABLE),
        field("SHIFT_MODE", 5, 3, 2, enum={0: "Standard", 1: "Eco", 2: "Sport", 3: "Snow"}, confidence=PROBABLE,
              notes="captures: follows the 0x56C drive mode (Personal shows its configured mode)"),
        bit("LOCKUP_CAND", 5, 7, confidence=CANDIDATE, label="Torque-converter lock-up?",
            notes="captures: set mostly when engine rpm and 0x176 turbine speed are within 30 rpm"),
    ], notes="Capture analysis 2026-10-12. No visible meter effect (bench, B3 and B5 overridden)."))

    m.append(MessageDef(0x176, "Transmission speeds", dlc=7, signals=[
        S("OUTPUT_SPEED", (Seg(0, 3, 0), Seg(1)), confidence=PROBABLE, label="Output-shaft speed (raw)",
          notes="captures: 23.77 x vehicle km/h (r 1.000), i.e. ~driveshaft rpm; 12-bit"),
        S("ENGINE_RPM", be16(2), unit="rpm", minimum=0, maximum=8000, confidence=PROBABLE,
          label="Engine RPM (0x176)", notes="tracks 0x180 through engine start in mainbus_startup; "
          "changing it alone did not move the tach (bench 2026-10-12). Linked to 0x180. Driving captures: "
          "reads 0 while stopped in gear at idle and below engine rpm at low speed, so it behaves as the "
          "transmission input (turbine) speed."),
        field("COUNTER", 6, 3, 0, control="readonly", confidence=PROBABLE),
    ]))

    m.append(MessageDef(0x177, "Transmission status", period_ms=10, signals=[
        bit("PARK", 1, 4, confidence=PROBABLE, label="In P", notes="captures: 1 in P, 0 in R/D/M"),
        bit("MANUAL_MODE", 1, 7, confidence=PROBABLE, label="Manual mode", notes="captures: 1 exactly in M1..M7"),
        byte_("SHIFT_ACTIVE_CAND", 4, confidence=CANDIDATE,
              notes="captures: 0 except during gear changes (100% within 0.5 s of a shift)"),
    ], notes="Capture analysis 2026-10-12. B1 overridden on the bench: no visible meter effect."))

    m.append(MessageDef(0x1C3, "Unresolved 0x1C3", period_ms=10, signals=[
        field("COUNTER", 5, 1, 0, control="readonly", confidence=PROBABLE),
        byte_("CHECKSUM", 7, control="readonly", confidence=PROBABLE,
              notes="captures: B7 = (sum(B0..B6) + 0xC4) & 0xFF (100%)"),
    ]))

    m.append(MessageDef(0x1F9, "Engine RPM copy", period_ms=10, signals=[
        field("ENGINE_STATE", 0, 5, 3, enum={0: "Off (IG off)", 2: "IG on, stopped", 4: "Running",
                                            5: "Running (settled)", 6: "Cranking"},
              confidence=PROBABLE, notes="captures: same states as 0x551 B5[1:0]; 6 matches START_STATE cranking"),
        S("ENGINE_RPM", be16(2), factor=0.125, unit="rpm", minimum=0, maximum=8000, confidence=DOCUMENTED,
          label="Engine RPM (0x1F9)", notes="same raw as 0x180 bytes 0-1 frame for frame in captures. Linked to 0x180."),
    ]))

    m.append(MessageDef(0x3EC, "Engine RPM (meter tach source)", dlc=2, period_ms=20, signals=[
        S("ENGINE_RPM", be16(0), factor=0.125, unit="rpm", minimum=0, maximum=8000, confidence=CONFIRMED,
          label="Engine RPM (0x3EC)", notes="raw = rpm * 8. Bench 2026-10-12: while 0x3EC is sent the tach follows "
          "it, not 0x180; removing it leaves the tach at 0 until a B+ cycle. Linked to 0x180."),
    ]))

    m.append(MessageDef(0x1A5, "AWD", dlc=None, notes=(
        "Adding then removing this message triggers an AWD error until B+ is cycled "
        "(car forgets it is AWD). Payload not decoded.")))

    m.append(MessageDef(0x216, "BCM lights", dlc=3, period_ms=20, signals=[
        field("WIPER_STATE", 0, 7, 6, confidence=CANDIDATE,
              notes="was B0[7:5]; bit 5 is the hood (captures). Real car: bit 6 always 1, bit 7 always 0"),
        bit("HOOD_OPEN", 0, 5, confidence=CANDIDATE, label="Hood open",
            notes="captures: set 5.6-23.7 s in '5seconds_open_hood_25_close', never elsewhere; "
                  "no visible meter effect (bench)"),
        field("OP_STATE", 0, 2, 0, confidence=CANDIDATE),
        bit("DRL_ON", 1, 6, label="DRL on"),
        bit("HEADLIGHTS", 1, 5),
        bit("FOG_LIGHTS", 1, 3, confidence=CANDIDATE,
            notes="README: fog lights. Real-car captures: 1 in P and 0 in R/D/M in all 42 captures with P, "
                  "so on the car it behaves like a P/N position signal"),
        byte_("CHECKSUM", 2, control="readonly", confidence=CANDIDATE,
              notes="observed: last byte = (B0+B1+0x72) & 0xFF in idle capture"),
    ]))

    m.append(MessageDef(0x239, "Pedal / cruise switches", period_ms=20, signals=[
        byte_("PEDAL", 0, confidence=CANDIDATE, notes="~100*raw/255 %, not calibrated"),
        byte_("PEDAL_INV", 1, confidence=CANDIDATE, notes="~0xFF - byte 0"),
        bit("PEDAL_RELEASED", 2, 0, confidence=CANDIDATE, notes="0x81 released, 0x80 pressed"),
        bit("CRUISE_MASTER", 2, 1, confidence=PROBABLE, notes="correlates with cruise enabled on meter"),
        byte_("CRUISE_SWITCH", 3, confidence=CANDIDATE, notes="idle 0x20; 0x08/0x10 during SET-/RES+ presses"),
        field("COUNTER", 4, 1, 0, control="readonly"),
        bit("IG_READY", 4, 6, confidence=CANDIDATE,
            notes="follows the ignition, not the engine: on for the whole IG-on/engine-off capture "
                  "(BENCHMETER_MAINBUS_STARTUP), cleared right after IG off; already set in remote-start captures"),
        byte_("ACCEL_POSITION", 5, factor=0.5, unit="%", minimum=0, maximum=100, confidence=PROBABLE,
              label="Accelerator position (0x239)", notes="captures: equals 0x180 B5 raw (r 0.9997)"),
    ]))

    m.append(MessageDef(0x245, "Unresolved 0x245", period_ms=20, signals=[
        bit("TCS_OFF", 3, 0, confidence=PROBABLE, label="TCS/VDC off",
            notes="captures: 1 exactly while 0x354 TCS_STATE = 2 (TCS OFF), 5 captures; no visible meter effect"),
        byte_("COUNTER", 4, control="readonly", confidence=PROBABLE),
        field("COUNTER2", 7, 1, 0, control="readonly", confidence=PROBABLE),
    ], notes="B1/B2 go to 0xFF while cranking (sensor data invalid)."))

    m.append(MessageDef(0x280, "Cluster Status 1 (meter)", sender="meter", period_ms=20, signals=[
        bit("DRIVER_UNBUCKLED", 0, 1, label="Driver unbuckled"),
        S("FUEL_LEVEL_NORM", (Seg(1), Seg(2, 7, 6)), label="Fuel level (normalised raw)",
          notes="packed 10-bit fuel candidate; meter filters the resistive sender input"),
        S("SPEED_PROCESSED", be16(4), factor=0.01, unit="km/h", label="Processed speed"),
        S("FUEL_ADC", (Seg(6), Seg(7, 7, 6)), label="Fuel sender ADC raw",
          notes="changes with sender resistance AND supply voltage"),
    ]))

    m.append(MessageDef(0x284, "Front wheel speeds", period_ms=20, signals=[
        S("FR_WHEEL_SPEED", be16(0), factor=0.005, unit="km/h", minimum=0, maximum=300),
        S("FL_WHEEL_SPEED", be16(2), factor=0.005, unit="km/h", minimum=0, maximum=300),
        S("VEHICLE_SPEED", be16(4), factor=0.01, unit="km/h", confidence=CANDIDATE, minimum=0, maximum=300,
          notes="observed in driving capture: equals 0x354 vehicle speed (km/h x 100)"),
        byte_("COUNTER", 6, control="readonly"),
        byte_("CHECKSUM", 7, control="readonly", confidence=PROBABLE,
              notes="observed: B7 = (sum(B0..B6) + 0x86) & 0xFF"),
    ]))

    m.append(MessageDef(0x285, "Rear wheel speeds", period_ms=20, signals=[
        S("RR_WHEEL_SPEED", be16(0), factor=0.005, unit="km/h", minimum=0, maximum=300),
        S("RL_WHEEL_SPEED", be16(2), factor=0.005, unit="km/h", minimum=0, maximum=300),
        byte_("VEHICLE_SPEED_KMH", 4, unit="km/h", confidence=PROBABLE, label="Vehicle speed (whole km/h)",
              notes="captures: floor of 0x354 vehicle speed (r 0.9999, 94% exact)"),
        byte_("COUNTER", 6, control="readonly"),
        byte_("CHECKSUM", 7, control="readonly", confidence=PROBABLE,
              notes="observed: B7 = (sum(B0..B6) + 0x87) & 0xFF"),
    ]))

    m.append(MessageDef(0x286, "Unresolved 0x286", dlc=2, period_ms=20, signals=[
        byte_("SPEED_DEPENDENT_CAND", 0, confidence=CANDIDATE,
              notes="captures: rises with speed (~3 x km/h + 32) and saturates at ~0xED above ~65 km/h"),
        field("COUNTER", 1, 7, 6, control="readonly", confidence=PROBABLE),
    ]))

    m.append(MessageDef(0x292, "Forces", period_ms=20, signals=[
        S("LONG_ACCEL", (Seg(0), Seg(1, 7, 4)), factor=0.001, offset=-2.048, unit="g", minimum=-2.048, maximum=2.047,
          confidence=PROBABLE, notes="captures: offset binary (0x800 = 0), ~0.001 g/bit (fit vs wheel-speed "
          "derivative r 0.93); + = accelerating; 0xFFF before the sensor is ready"),
        S("LAT_ACCEL", (Seg(1, 3, 0), Seg(2)), factor=0.001, offset=-2.048, unit="g", minimum=-2.048, maximum=2.047,
          confidence=PROBABLE, notes="captures: offset binary (0x800 = 0), ~0.001 g/bit (fit vs speed x yaw "
          "r 0.92); + = turning left"),
        S("YAW_RATE", (Seg(3), Seg(4, 7, 4)), factor=0.1, offset=-204.8, unit="deg/s", minimum=-204.8,
          maximum=204.7, confidence=CANDIDATE, notes="captures: offset binary (0x800 = 0), ~0.1 deg/s/bit, "
          "+ = left; noisier than 0x2AB YAW_RATE (r 0.74 vs rear-wheel yaw)"),
        byte_("BRAKE_PRESSURE", 6, notes="raw, not calibrated"),
        field("COUNTER", 7, 1, 0, control="readonly"),
    ]))

    m.append(MessageDef(0x2AA, "Unresolved 0x2AA", period_ms=20, signals=[
        field("COUNTER", 6, 3, 0, control="readonly", confidence=PROBABLE),
        byte_("CHECKSUM", 7, control="readonly", confidence=PROBABLE,
              notes="captures: B7 = (sum(B0..B6) + 0xAC) & 0xFF (100%)"),
    ], notes="B1-B3 change ~1.1 s after IG on (module ready)."))

    m.append(MessageDef(0x2B0, "Unresolved 0x2B0", period_ms=10, signals=[
        bit("BRAKE_SW_ON", 3, 6, confidence=PROBABLE, label="Brake switch (pressed)",
            notes="captures: equals 0x182 BRAKE_PRESSED; no visible meter effect"),
        bit("BRAKE_SW_OFF", 3, 7, confidence=PROBABLE, label="Brake switch (released)",
            notes="captures: inverse of BRAKE_SW_ON (two-switch brake input; both 0 or 1 briefly at edges)"),
        field("COUNTER", 6, 7, 4, control="readonly", confidence=PROBABLE),
    ]))

    m.append(MessageDef(0x2B1, "ADAS", dlc=6, period_ms=20, signals=[
        field("CRUISE_CONTROL", 0, 7, 6, enum={0: "None", 1: "CC icon", 2: "CC blink", 3: "Yellow CC (warning)"}),
        bit("CC_ENABLED", 0, 5, label="CC enabled (green)"),
        bit("CC_BIT4", 0, 4, confidence=CANDIDATE, label="Byte 0 bit 4 (unknown)",
            notes="appears in older enum tables; do not treat as a confirmed icon bit"),
        field("DISTANCE_BARS", 0, 3, 2, enum={0: "No bars", 1: "1 bar", 2: "2 bars", 3: "3 bars"}),
        bit("DISPLAY_MPH", 0, 1),
        bit("ADAPTIVE_CC", 0, 0),
        field("CAR_AHEAD", 1, 7, 6, enum={0: "None", 1: "White car ahead", 2: "Yellow blinking car", 3: "Crash alert"}),
        bit("LANE_WARN_LEFT", 1, 5), bit("LANE_WARN_RIGHT", 1, 4),
        bit("BSW_LEFT_BLINK", 1, 3), bit("BSW_RIGHT_BLINK", 1, 2),
        bit("EMERGENCY_BRAKE_WARNING", 1, 1), bit("WINDING_ROAD", 1, 0),
        field("EMG_BRAKE", 2, 7, 6, enum=ADAS_STATE), field("LANE_ASSIST", 2, 5, 4, enum=ADAS_STATE),
        field("BLIND_SPOT", 2, 3, 2, enum=ADAS_STATE), bit("FEB_LIGHT_BLINK", 2, 1), bit("FEB_LIGHT", 2, 0),
        field("EMG_BRAKE_2", 3, 7, 6, enum=ADAS_STATE2), field("LANE_ASSIST_2", 3, 5, 4, enum=ADAS_STATE2),
        field("BLIND_SPOT_2", 3, 3, 2, enum=ADAS_STATE2),
        byte_("CC_SPEED", 4, offset=-1, minimum=-1, maximum=253, label="CC set speed",
              notes="0x00 blank, 0x01-0xFE = speed + 1 (shown as raw-1), 0xFF '- - -'"),
        field("TONES", 5, 7, 5, enum=TONES_2B1),
        bit("SWITCH_PAGE", 5, 4, notes="sometimes switches page without beep; retrigger rules unresolved"),
        bit("BLINK_SPEED", 5, 3),
        bit("PROMPT_PAGE", 5, 2, notes="prompts ADAS page + beep"),
    ], notes="Missing 0x2B1 can light the collision-warning indicator."))

    m.append(MessageDef(0x2DE, "Cluster Status 2 (meter)", sender="meter", period_ms=10, signals=[
        bit("PADDLE_DOWN_REQ", 0, 5), bit("PADDLE_UP_REQ", 0, 4),
        bit("STICK_DOWN_REQ", 0, 3), bit("STICK_UP_REQ", 0, 2),
        bit("AUTO_MODE", 0, 1, confidence=CANDIDATE), bit("MANUAL_MODE", 0, 0),
        S("AVG_FUEL_ECONOMY", (Seg(3), Seg(4, 7, 4)), factor=0.01, unit="km/L",
          notes="x 2.3521458 = US mpg; low nibble of B4 not part of it"),
        S("DISTANCE_TO_EMPTY", be16(6), factor=0.1, unit="km"),
    ]))

    m.append(MessageDef(0x351, "Intelligent Key", period_ms=100, signals=[
        bit("UNLOCK", 1, 7), bit("LOCK", 1, 6),
        bit("STEERING_LOCK_REG_COMPLETE", 5, 7), bit("KEY_SYSTEM_ERROR", 5, 6),
        bit("DOUBLE_BEEP", 5, 5), bit("FAST_BEEP", 5, 4),
        field("KEY_PROMPT", 5, 3, 0, enum=KEY_PROMPTS, label="Key prompt",
              notes="high bits of byte 5 show priority/suppression when combined"),
        bit("BEEP", 6, 7), bit("AUTO_HIGH_BEAM", 6, 4),
        byte_("CHECKSUM", 7, control="readonly", confidence=CANDIDATE,
              notes="observed: B7 = sum(B0..B6) & 0xFF in idle capture"),
    ]))

    m.append(MessageDef(0x354, "Chassis Control (ABS)", period_ms=40, signals=[
        S("VEHICLE_SPEED", be16(0), factor=0.01, unit="km/h", minimum=0, maximum=300, label="Vehicle speed"),
        field("TCS_STATE", 4, 7, 5, enum=TCS_STATE),
        bit("ABS", 4, 4, label="ABS hold", confidence=CONFIRMED,
            notes="freezes the ABS lamp in its current state while set; never lights it by itself"),
        bit("ABS_LATCH", 4, 3, label="ABS lamp", confidence=CONFIRMED,
            notes="lights ABS while set (only when bit 4 is clear; with bit 4 set the lamp keeps its state)"),
        bit("BRAKE", 4, 2, label="BRAKE hold", confidence=CONFIRMED,
            notes="freezes the BRAKE lamp in its current state while set; never lights it by itself"),
        bit("BRAKE_LATCH", 4, 1, label="BRAKE lamp", confidence=CONFIRMED,
            notes="lights BRAKE while set (only when bit 2 is clear). BRAKE is shared with parking brake "
                  "and low brake fluid inputs"),
        field("COUNTER", 5, 4, 3, control="readonly", notes="2-bit counter (00,08,10,18)"),
        bit("BRAKE_PRESSED", 6, 4),
    ], notes="Byte 4 (bench 2026-10-12): bits 3/1 request the ABS/BRAKE lamps, bits 4/2 hold each lamp in its "
             "current state. 00 off, 0A both on, 1E stays on, 14 stays on (held), 00 off, 14 stays off, "
             "1E stays off (held), 0A on. Names ABS/ABS_LATCH/BRAKE/BRAKE_LATCH kept for compatibility."))

    m.append(MessageDef(0x355, "Cluster Status 3 (meter)", sender="meter", period_ms=40, signals=[
        S("SPEED_PROCESSED", be16(0), factor=0.01, unit="km/h"),
        S("SPEED_DISPLAYED", be16(2), factor=0.01, unit="km/h"),
    ]))

    m.append(MessageDef(0x358, "Body / lamps", period_ms=100, signals=[
        bit("DIM_BACKLIGHT", 1, 7),
        bit("RIGHT_BLINKER_REQ", 2, 2, confidence=CANDIDATE, notes="no visible effect on the bench"),
        bit("LEFT_BLINKER_REQ", 2, 1, confidence=CANDIDATE, notes="no visible effect on the bench"),
        bit("TRUNK_OPEN", 2, 0),
        bit("LOW_OIL_PRESSURE", 3, 5, confidence=CONFIRMED, notes="lights red oil lamp + 'Low Oil Pressure / Stop Vehicle' (bench 2026-10-12, 3000 rpm); off at RPM 0"),
        bit("TPMS_LIGHT", 4, 7),
        bit("BULB_CHECK_CAND", 1, 0, confidence=CANDIDATE,
            notes="captures: 1 s pulse at IG on together with TPMS_LIGHT; no visible meter effect (bench)"),
        bit("UNLOCK_EVENT_CAND", 3, 7, confidence=CANDIDATE,
            notes="captures: 0.3 s pulse at each unlock (with 0x351 UNLOCK and the hazard flash); no visible effect"),
        field("REMOTE_START_STATE", 5, 7, 5, confidence=CANDIDATE,
              notes="captures: 0 in normal use; 1-4 only in the two remote-start captures (4 -> 3 at remote "
                    "start, 0 when the driver takes over); no visible meter effect"),
    ], notes="Missing 0x358 dims illumination and flashes the TPMS lamp."))

    m.append(MessageDef(0x35D, "Brake signal", period_ms=100, signals=[
        bit("IG_ON", 0, 7, confidence=PROBABLE, label="Ignition on",
            notes="captures: equals 0x60D IG_RUN (2 mismatches in ~55k samples)"),
        bit("CRANKING", 0, 6, confidence=PROBABLE, label="Starter cranking",
            notes="captures: set exactly while 0x625 START_STATE = 3 (cranking); no visible meter effect"),
        bit("UNKNOWN_B4_6", 4, 6, confidence=CANDIDATE), bit("BRAKE_PRESSED", 4, 4),
        field("REMOTE_START_CAND", 7, 7, 4, confidence=CANDIDATE,
              notes="captures: 5 normally, 0xA while the engine runs on remote start"),
    ]))

    tire = []
    for pos, flat, warn, b, valid in (("FR", 7, 3, 2, 7), ("FL", 6, 2, 3, 6), ("RR", 5, 1, 4, 5), ("RL", 4, 0, 5, 4)):
        tire += [bit(f"{pos}_FLAT", 1, flat), bit(f"{pos}_WARNING", 1, warn),
                 byte_(f"{pos}_PRESSURE", b, factor=0.25, unit="psi", minimum=0, maximum=63.75),
                 bit(f"{pos}_VALID", 6, valid)]
    m.append(MessageDef(0x385, "TPMS", period_ms=100, signals=[
        bit("PRESSURES_WHILE_DRIVING", 0, 1, confidence=CONFIRMED,
            notes="with every tire invalid: 0 shows 'Tire pressures appear while driving.', 1 shows dashes"),
        *tire,
        field("TARGET_FRONT", 7, 7, 4, factor=10, offset=190, unit="kPa", confidence=CONFIRMED,
              notes="kPa = 190 + 10 x nibble; nibble 0 hides the target (bench: 1 -> 29 psi, 15 -> 49 psi)"),
        field("TARGET_REAR", 7, 3, 0, factor=10, offset=190, unit="kPa", confidence=CONFIRMED,
              notes="kPa = 190 + 10 x nibble; nibble 0 hides the target (bench: 10 -> 42 psi)"),
    ], notes="Page inclusion, low/flat warning, pressure data and the TPMS telltale are separate mechanisms. "
             "Bench 2026-10-12: WARNING -> 'Tire Pressure Low / Add Air' popup + amber value; FLAT -> "
             "'Flat Tire / Visit dealer' popup + red value and tire; VALID=0 -> '---'; pressure in psi (0.25 steps)."))

    m.append(MessageDef(0x3AF, "Hazard switch", dlc=1, period_ms=100, signals=[bit("HAZARD_BUTTON", 0, 7)]))

    m.append(MessageDef(0x421, "Automatic Transmission", dlc=3, period_ms=60, signals=[
        field("GEAR", 0, 7, 3, enum=GEARS, label="Gear"),
        bit("AT_ERROR_A", 0, 2), bit("AT_ERROR_B", 0, 0),
        bit("FLASH_GEAR", 1, 2), bit("BEEP", 2, 6),
    ]))

    adas_err = [
        (0, 2, "UNAVAIL_HIGH_ACCEL_TEMP_DCA"), (0, 1, "UNAVAIL_HIGH_ACCEL_TEMP_BCI"), (0, 0, "UNAVAIL_HIGH_ACCEL_TEMP_FEB"),
        (1, 7, "UNAVAIL_CLEAN_REAR_CAM_LDW"), (1, 6, "UNAVAIL_CLEAN_REAR_CAM_BSW"), (1, 5, "MALFUNCTION_PFCW"),
        (1, 4, "MALFUNCTION_LDW"), (1, 3, "MALFUNCTION_BSW"), (1, 2, "UNAVAIL_HIGH_CABIN_TEMP_LDW"),
        (1, 1, "UNAVAIL_HIGH_CABIN_TEMP_LDP"), (1, 0, "UNAVAIL_HIGH_CABIN_TEMP_BSI"),
        (2, 7, "UNAVAIL_FRONT_RADAR_ICC"), (2, 6, "UNAVAIL_FRONT_RADAR_PFCW"), (2, 5, "UNAVAIL_FRONT_RADAR_DCA"),
        (2, 4, "UNAVAIL_FRONT_RADAR_FEB"), (2, 3, "UNAVAIL_SELECT_DRIVING_AIDS_A"), (2, 2, "UNAVAIL_SELECT_DRIVING_AIDS_B"),
        (2, 1, "CURRENTLY_UNAVAIL_ICC_A"), (2, 0, "CURRENTLY_UNAVAIL_ICC_B"),
        (3, 3, "UNAVAIL_SIDE_RADAR_BSW"), (3, 2, "UNAVAIL_SIDE_RADAR_BSI"), (3, 1, "UNAVAIL_SIDE_RADAR_BCI"),
        (4, 7, "MALFUNCTION_DCA"), (4, 6, "MALFUNCTION_LDP"), (4, 5, "MALFUNCTION_BSI"),
        (4, 4, "MALFUNCTION_FEB"), (4, 3, "MALFUNCTION_BCI"), (4, 2, "SYSTEM_OFF_BCI"),
    ]
    m.append(MessageDef(0x4CC, "ADAS errors", dlc=5, period_ms=500,
                        signals=[bit(n, b, i) for b, i, n in adas_err]))

    m.append(MessageDef(0x4F6, "Page-slot prerequisite B", period_ms=500, notes=(
        "With 0x51C, makes AV-backed page slots exist (availability, not the preference bitmap). "
        "Payload not decoded; keep the captured template.")))

    m.append(MessageDef(0x519, "Drive-mode switch / startup (raw)", period_ms=500, signals=[
        byte_("STATE", 0, confidence=CANDIDATE, notes="startup E0 -> 80 -> 00; switch sequences 14/06/04/16"),
    ], notes="Competing interpretations; kept as a raw experimental view."))

    m.append(MessageDef(0x51B, "Service distance (wrong ID?)", dlc=None, signals=[
        S("SERVICE_DISTANCE", (Seg(4, 5, 0), Seg(5)), factor=5, unit="km", confidence=CANDIDATE,
          notes="candidate: raw*5 km; captured message displayed 4060 mi. Bench 2026-10-12: sending 0x51B "
                "changed nothing; the same layout in 0x553 drives the Engine Oil page"),
    ]))

    m.append(MessageDef(0x5E4, "Power steering", dlc=3, period_ms=100, signals=[
        bit("POWER_STEERING_WARNING", 0, 2, confidence=CONFIRMED, label="Power steering warning",
            notes="red steering-wheel telltale (bench 2026-10-12, byte 0 = 0x04); part of the startup lamp check"),
    ]))

    m.append(MessageDef(0x553, "Oil service", dlc=6, period_ms=100, signals=[
        S("SERVICE_DISTANCE", (Seg(4, 5, 0), Seg(5)), factor=5, unit="km", minimum=0, maximum=81915,
          confidence=CONFIRMED, label="Service due in",
          notes="Engine Oil page 'Service due in': raw*5 km (bench 2026-10-12: 0x0535 -> 4141 mi, "
                "0x0100 -> 795 mi, 0x0010 -> 49 mi). Byte 4 bits 7:6 not exercised."),
    ]))

    m.append(MessageDef(0x51C, "Page-slot prerequisite A", period_ms=500, notes=(
        "With 0x4F6, makes AV-backed page slots exist. Payload not decoded; keep the captured template.")))

    m.append(MessageDef(0x54A, "HVAC 1", period_ms=100, signals=[
        byte_("DRIVER_TEMP", 4), byte_("PASSENGER_TEMP", 5)]))

    m.append(MessageDef(0x54B, "HVAC 2", period_ms=100, signals=[
        field("AIR_DIRECTION", 2, 5, 3, enum={1: "Upper", 2: "Upper + Lower", 3: "Lower",
                                               4: "Defogger + Lower", 5: "Defogger"}),
        bit("DUAL", 3, 6), bit("RECIRCULATION", 3, 1),
        field("BLOWER_SPEED", 4, 5, 2, enum={3: "Level 1", 5: "Level 2", 7: "Level 3", 9: "Level 4",
                                              11: "Level 5", 13: "Level 6", 15: "Level 7"},
              notes="README masks 0x0C..0x3C are whole-byte; enum values here are the extracted field"),
        bit("STEERING_HEATER", 5, 6),
    ]))

    m.append(MessageDef(0x544, "Vehicle speed (0x544)", dlc=4, period_ms=100, signals=[
        byte_("VEHICLE_SPEED_KMH", 0, unit="km/h", confidence=PROBABLE, label="Vehicle speed (whole km/h)",
              notes="captures: tracks 0x354 speed in whole km/h; 0xFF for the first ~10 s (not available)"),
    ], notes="Frame nibbles XOR to 0 (nibble checksum, 100% of frames). B1 bit 6 / B3 bit 3 change at IG on/off."))

    m.append(MessageDef(0x54C, "HVAC 3 (outside temperature)", period_ms=100, signals=[
        byte_("TEMP_B0_CAND", 0, confidence=CANDIDATE, label="HVAC temperature-like (B0)",
              notes="captures: 0xFF at start, settles ~100 with the A/C running, higher after hot soak; scale unknown"),
        field("BLOWER_OUTPUT", 5, 6, 3, confidence=PROBABLE, label="Blower output level",
              notes="captures: ramps in 0.5 s steps and rises with 0x54B BLOWER_SPEED (L1 7, L2 8, L3 10-12, "
                    "L4 13, off 0)"),
        byte_("TEMP_B7_CAND", 7, confidence=CANDIDATE, label="HVAC temperature-like (B7)",
              notes="captures: 0xFE until ready, ~180 at idle, drops to ~95-120 driving with A/C; scale unknown"),
        byte_("OUTSIDE_TEMP", 6, factor=0.5, offset=-41, unit="degC", minimum=-41, maximum=86.5,
              confidence=CONFIRMED, label="Outside (ambient) temperature",
              notes="raw = (degC + 41) * 2. Bench 2026-10-12 (camera): B1 -> 118 degF (idle baseline value), "
                    "20 degC -> 68 degF, -10 degC -> 14 degF, 0xFF -> '--- degF' (not available; also the first "
                    "frame after engine start in engineonq50). B1-B4 undecoded. Owner 2026-10-12: the "
                    "meter filters the reading - a decrease shows almost at once, an increase lags (does not "
                    "follow in real time); rate/delay not measured."),
    ]))

    m.append(MessageDef(0x54D, "HVAC 4 (seat heat)", period_ms=100, signals=[
        bit("DRIVER_SEAT_AUTO", 1, 1), bit("PASSENGER_SEAT_AUTO", 1, 0),
        bit("DRIVER_SEAT_OFF", 5, 2), bit("DRIVER_SEAT_LOW", 5, 1), bit("DRIVER_SEAT_MID", 5, 0),
        bit("DRIVER_SEAT_HIGH", 6, 7), bit("PASSENGER_SEAT_OFF", 6, 2), bit("PASSENGER_SEAT_LOW", 6, 1),
        bit("PASSENGER_SEAT_MID", 6, 0), bit("PASSENGER_SEAT_HIGH", 7, 7),
    ]))

    m.append(MessageDef(0x551, "Engine (coolant / MIL / cruise)", period_ms=100, signals=[
        byte_("COOLANT_TEMP", 0, offset=-40, unit="degC", minimum=-40, maximum=215, label="Coolant temperature",
              confidence=PROBABLE, notes="bench encoding raw = degC + 40"),
        byte_("FUEL_USAGE_COUNTER", 1, notes="cumulative fuel use; omitting 0x551 while moving spikes economy"),
        bit("MIL_SOLID", 3, 5, label="Check engine (solid)"),
        bit("MIL_FLASH", 3, 6, label="Check engine (flashing)"),
        bit("IG_READY", 3, 7, confidence=PROBABLE,
            notes="ignition on/ready, not engine running: set the whole IG-on/engine-off capture "
                  "(BENCHMETER_MAINBUS_STARTUP, RPM 0 for 60 s); cleared 0.1 s after IG off (engineonq50)"),
        byte_("CRUISE_SET_SPEED", 4, unit="mph", label="Cruise set speed", notes="0x33 displayed 51 mph"),
        field("CRUISE_ICON", 5, 7, 4, enum=CRUISE_ICON_551, label="Cruise icon",
              notes="upper-nibble whole-byte masks; interaction with 0x2B1 not isolated"),
        field("ENGINE_STATE", 5, 1, 0, enum={0: "Off", 1: "IG on, stopped", 2: "Running", 3: "Cranking"},
              confidence=PROBABLE, notes="captures: same states as 0x1F9 B0[5:3]"),
        bit("ECO_MODE", 6, 6, confidence=PROBABLE, label="ECO mode active",
            notes="captures: 1 in Eco (and Personal set to Eco), with 0x174 SHIFT_MODE = Eco; no visible meter effect"),
    ]))

    m.append(MessageDef(0x56C, "Chassis control / drive mode", dlc=6, period_ms=100, signals=[
        bit("TRACE_BLUE_SQUARES", 0, 6),
        field("TRACE_SQUARE_LINES", 0, 5, 4, enum={0: "None", 1: "1 line", 2: "2 lines", 3: "3 lines"},
              confidence=CONFIRMED, label="Trace: lines of blue squares",
              notes="number of blue-square lines beside the car (bench 2026-10-12: 10 -> 1, 20 -> 2, 30 -> 3); "
                    "formerly two flags TRACE_BLUE_LINES_WHEELS (bit 4) and TRACE_2_SQUARES_WHEELS (bit 5)"),
        bit("CHASSIS_ERROR", 0, 1, confidence=CANDIDATE, notes="mask 0x02 associated with chassis-system error"),
        bit("TRACE_SADDLE_CURVE", 1, 6),
        field("DRIVE_MODE", 1, 5, 3, enum=DRIVE_MODES, label="Drive mode"),
        bit("QUAD_BEEP", 1, 2), bit("CONTINUOUS_BEEP", 1, 1),
        bit("FL_WHEEL_HIGHLIGHT", 2, 6), bit("FR_WHEEL_HIGHLIGHT", 2, 4),
        bit("RL_WHEEL_HIGHLIGHT", 2, 2), bit("RR_WHEEL_HIGHLIGHT", 2, 0),
        field("COUNTER", 5, 1, 0, control="readonly", confidence=PROBABLE),
    ], notes="Disabling 0x56C causes chassis-system error. README DLC 5 unverified; idle capture DLC 6."))

    m.append(MessageDef(0x56E, "Telematics", dlc=4, period_ms=100, signals=[
        bit("UNKNOWN_B0_7", 0, 7, confidence=CANDIDATE), bit("UNKNOWN_B0_6", 0, 6, confidence=CANDIDATE),
        bit("UNLOCK_CMD", 0, 0), bit("LOCK_CMD", 1, 7),
    ]))

    m.append(MessageDef(0x580, "Engine 3", period_ms=100, signals=[
        S("ENGINE_TORQUE", be16(0), factor=1 / 16, unit="Nm"),
        bit("LOW_OIL_PRESSURE", 2, 0, label="Low oil pressure",
            notes="owner: lights the oil warning while RPM > 0 and goes off at RPM 0 (one camera test at "
                  "3000 rpm on 2026-10-12 did not catch it)"),
        bit("LOOSE_FUEL_CAP", 3, 0),
        byte_("OIL_TEMP", 4, offset=-40, unit="degC", confidence=CANDIDATE,
              notes="README 'raw degC + 40' is ambiguous (+40 vs -40)"),
        bit("ECO_LIGHT", 5, 6, label="ECO telltale"),
        bit("ENGINE_RUNNING", 7, 5, confidence=CANDIDATE, notes="engine-state flag (all real-car captures incl. 60 s IG-on/engine-off: 0 off, 1 running with the engine, one change at start and stop). Driving captures: B7 is a numeric, load-like value (~0x19 engine off, ~0x3C idle, 0x4B-0x50 cruising, ~0x76 full throttle), so bit 5 only tracks the engine at idle; 0 most of the time while driving"),
    ]))

    m.append(MessageDef(0x5B0, "Drive mode (0x5B0)", dlc=2, period_ms=100, signals=[
        byte_("DRIVE_MODE_FLAGS", 0, enum={1: "Sport", 4: "Standard", 16: "Eco", 64: "Snow"}, confidence=PROBABLE,
              notes="captures: one-hot, follows the 0x56C drive mode (Personal shows its configured mode); "
                    "no visible meter effect"),
        field("COUNTER", 1, 7, 6, control="readonly", confidence=PROBABLE),
    ]))

    m.append(MessageDef(0x5C5, "Cluster Status 4 (meter)", sender="meter", period_ms=100, signals=[
        bit("METER_OFF", 0, 7), bit("METER_ON", 0, 6), bit("GAS_LIGHT", 0, 5),
        bit("LOW_BRAKE_FLUID", 0, 3, notes="latest README bit 3; early note said bit 4"),
        bit("PARKING_BRAKE", 0, 2), bit("RUNNING", 0, 1, confidence=CANDIDATE),
        S("ODOMETER_RAW", (Seg(1), Seg(2), Seg(3)), notes="byte order / unit not established"),
    ]))

    m.append(MessageDef(0x60D, "BCM", period_ms=100, signals=[
        bit("TRUNK", 0, 7), bit("REAR_PASSENGER_DOOR", 0, 6), bit("REAR_DRIVER_DOOR", 0, 5),
        bit("PASSENGER_DOOR", 0, 4), bit("DRIVER_DOOR", 0, 3), bit("PARKING_LIGHTS", 0, 2),
        bit("HEADLIGHTS", 0, 1, notes="with LH/RH headlight inputs off this latches 'Headlight System Error'"),
        bit("RIGHT_TURN", 1, 6), bit("LEFT_TURN", 1, 5), bit("HIGH_BEAM", 1, 3),
        bit("IG_RUN", 1, 2), bit("ACC", 1, 1), bit("FOG_LIGHTS", 1, 0),
        bit("ALL_DOORS_LOCKED", 2, 4), bit("REAR_FOG", 2, 2),
        field("CHIMES", 3, 5, 1, enum=CHIMES, notes="startup byte 0x2A = field 0x15"),
    ]))

    m.append(MessageDef(0x625, "BCM / IPDM", dlc=6, period_ms=100, signals=[
        field("START_STATE", 0, 5, 4, enum={0: "Normal", 1: "? (1)", 2: "? (2)", 3: "Cranking"}),
        bit("WIPER_LOW", 0, 2), bit("WIPERS_NOT_AT_TOP", 0, 1),
        bit("AC_COMPRESSOR_OR_FAN", 1, 7), bit("PARKING_LIGHTS", 1, 6), bit("HEADLIGHT", 1, 5),
        bit("HIGH_BEAM", 1, 4), bit("FOG_LIGHTS", 1, 3), bit("IG_RUN", 3, 4),
        field("AWAKE_STATE", 3, 1, 0, enum={1: "Awake", 2: "Standby"}, confidence=CANDIDATE,
              notes="captures: 1 while IG is on and for 4.9 s after each wake event (door, button, unlock), "
                    "otherwise 2"),
    ]))

    m.append(MessageDef(0x6E2, "Unresolved 0x6E2 (tach gate)", period_ms=100, signals=[
        field("PAGE", 2, 2, 0, control="readonly", confidence=PROBABLE,
              notes="captures: B2 = 0x38 + page; pages 0-3 with the engine off, 4-7 running; each page ~400 ms"),
        byte_("RPM_DIV64_CAND", 3, confidence=CANDIDATE,
              notes="captures: follows engine rpm (~0x0B idle, 0x43 at 4300 rpm) on pages 4-7"),
        bit("TACH_ZERO_FLAG", 6, 6, confidence=PROBABLE, label="Tach forced to 0 (meaning unknown)",
            notes="bench sweep 2026-10-12 (camera): bit set -> tach needle drops from ~700 rpm idle to 0, "
                  "returns when cleared (5 of 5); no telltale. Other bits of byte 6 do nothing. Meaning "
                  "(engine-stopped / RPM-invalid?) not established."),
    ], notes="Multiplexed: B0 = 0x12 and B1 = 0x16 fixed, B5[7:2] steps by 1 per frame (wraps)."))

    # Unresolved IDs seen in captures; kept so the UI can label them.
    m.append(MessageDef(0x2AB, "Chassis 0x2AB (yaw rate)", signals=[
        S("YAW_RATE", (Seg(0), Seg(1, 7, 4)), factor=-0.0191, signed=True, unit="deg/s", minimum=-39, maximum=39,
          confidence=PROBABLE, label="Yaw rate (0x2AB)",
          notes="captures: signed 12-bit, r -0.990 vs yaw from rear wheel speeds (track 1.55 m assumed, so "
                "factor +-5%); + = turning left"),
        S("WHEEL_ANGLE_CAND", be16(2), confidence=CANDIDATE,
          notes="suggested wheel angle; driving captures: B2-B5 look like noise, no correlation with steering")]))
    m.append(MessageDef(0x300, "Power steering assist", dlc=1, signals=[
        byte_("STEER_EFFORT_CAND", 0, confidence=PROBABLE, label="Steering assist effort",
              notes="captures 2026-10-12: unsigned power-steering assist magnitude, uncalibrated. Rises while the "
                    "wheel turns (~120 at full lock parked), back near 0 when it stops, same sign left and right; "
                    "falls with speed (mean 46 parked, 16 at 2-10 km/h, 7 at 10-40, ~3 above 40 km/h while "
                    "steering). 0-1 with the engine off, ~2-3 at idle. Bit 1 was formerly ENGINE_RUNNING: it only "
                    "looked like a flag in idle captures (while driving 0 in 28940 samples, 1 in 17547)."),
    ]))
    return m


# Engine on/off: every flag that follows the engine in the engineonq50 capture, with its value
# while the engine is off (the idle profile was captured running, so "running" = clear overrides).
# Checked against every real-car capture: these hold their engine-off value with IG on and the
# engine stopped (BENCHMETER_MAINBUS_STARTUP: 60 s IG on, RPM 0) and flip only when the engine runs.
# 0x300 is the steering-assist value (0 with the engine off, ~2-3 at idle); 0x580 B7.5 is part of a
# load-like value that only behaves as a flag between engine off and idle, which is all the bench needs.
# 0x551 B3.7 and 0x239 B4.6 follow the ignition instead (IG_READY) and are left alone.
ENGINE_OFF_STATE = {
    "main.160.ENGINE_STOPPED": 1, "main.160.ENGINE_RUNNING": 0, "main.182.ENGINE_STOPPED": 1,
    "main.300.STEER_EFFORT_CAND": 0, "main.580.ENGINE_RUNNING": 0, "main.180.ENGINE_RPM": 0,
}

# Signals that carry the same physical value in several messages. Setting or clearing
# the first one does the same to the others (in physical units).
LINKED_SIGNALS = {
    "main.180.ENGINE_RPM": ("main.1F9.ENGINE_RPM", "main.3EC.ENGINE_RPM", "main.176.ENGINE_RPM"),
    # the speedometer and the fuel-economy calculation use the 0x284/0x285 wheel speeds
    "main.354.VEHICLE_SPEED": ("main.284.VEHICLE_SPEED", "main.284.FR_WHEEL_SPEED", "main.284.FL_WHEEL_SPEED",
                               "main.285.RR_WHEEL_SPEED", "main.285.RL_WHEEL_SPEED"),
}


# Bench results 2026-10-12 (camera on the real meter), applied on top of the definitions above:
# path -> (note appended, new confidence or None to keep it).
NO_EFFECT = "no visible change on the bench 2026-10-12"
BENCH_2026_10_12 = {
    "main.351.KEY_PROMPT": ("all 15 codes shown as listed; 2/12/13 show nothing; 15 also turns the gauge "
                            "backlight off", CONFIRMED),
    "main.351.STEERING_LOCK_REG_COMPLETE": ("'Steering Lock Registration Complete'", CONFIRMED),
    "main.351.KEY_SYSTEM_ERROR": ("'Key System Error / See Owner's Manual'", CONFIRMED),
    "main.351.AUTO_HIGH_BEAM": ("green auto-high-beam telltale", CONFIRMED),
    "main.216.DRL_ON": ("meter shows the green parking-light telltale (owner)", None),
    "main.2B1.FEB_LIGHT": ("amber FEB telltale: two cars with '!' between (owner photo)", None),
    "main.351.LOCK": (NO_EFFECT, None), "main.351.UNLOCK": (NO_EFFECT, None),
    "main.56E.LOCK_CMD": (NO_EFFECT, None), "main.56E.UNLOCK_CMD": (NO_EFFECT, None),
    "main.56E.UNKNOWN_B0_7": (NO_EFFECT, None), "main.56E.UNKNOWN_B0_6": (NO_EFFECT, None),
    "main.354.TCS_STATE": ("1, 2, 4-7 as listed (4: slip blinks; 6/7: TCS OFF solid + slip blinks); "
                           "3 showed nothing", CONFIRMED),
    "main.421.GEAR": ("every listed code shown (N boxed, Ds, L, 1-6, M1-M8 as nM)", CONFIRMED),
    "main.421.FLASH_GEAR": ("gear digit blinks", CONFIRMED),
    "main.421.AT_ERROR_A": ("'AT Error' like AT_ERROR_B, but only while RPM > 0 (owner)", CONFIRMED),
    "main.421.AT_ERROR_B": ("'AT Error / See Owner's Manual' + master warning; needs RPM > 0 (owner)", CONFIRMED),
    "main.56C.DRIVE_MODE": ("all six modes shown (ECO green, SPORT/SPORT+ red)", CONFIRMED),
    "main.56C.CHASSIS_ERROR": ("'Chassis Control System Error'; clears when the bit clears", CONFIRMED),
    "main.56C.TRACE_BLUE_SQUARES": ("Chassis Control page grid graphic", CONFIRMED),
    "main.56C.TRACE_SADDLE_CURVE": ("Chassis Control page grid graphic", CONFIRMED),
    "main.56C.FL_WHEEL_HIGHLIGHT": ("wheel highlighted on Chassis Control page", CONFIRMED),
    "main.56C.FR_WHEEL_HIGHLIGHT": ("wheel highlighted on Chassis Control page", CONFIRMED),
    "main.56C.RL_WHEEL_HIGHLIGHT": ("wheel highlighted on Chassis Control page", CONFIRMED),
    "main.56C.RR_WHEEL_HIGHLIGHT": ("wheel highlighted on Chassis Control page", CONFIRMED),
    "main.551.MIL_FLASH": ("MIL blinks", CONFIRMED),
    "main.551.CRUISE_ICON": ("1-6, 9, 12 shown as listed with CRUISE_SET_SPEED", CONFIRMED),
    "main.358.TRUNK_OPEN": ("Warning + trunk shown open", CONFIRMED),
    "main.358.DIM_BACKLIGHT": ("whole meter dims", CONFIRMED),
    "main.358.LEFT_BLINKER_REQ": (NO_EFFECT, None), "main.358.RIGHT_BLINKER_REQ": (NO_EFFECT, None),
    "main.2B1.CAR_AHEAD": ("2 orange car bar; 3 red crash-alert banner", CONFIRMED),
    "main.2B1.CRUISE_CONTROL": ("3 turns the cruise icon orange", CONFIRMED),
    "main.2B1.EMG_BRAKE": ("1 white, 2 warning triangle on Driving Aids page, 3 orange, 0 hidden", CONFIRMED),
    "main.2B1.LANE_ASSIST": ("1 white, 2 warning triangle on Driving Aids page, 3 orange, 0 hidden", CONFIRMED),
    "main.2B1.BLIND_SPOT": ("2 warning triangle on Driving Aids page, 3 orange rear zones", CONFIRMED),
    "main.2B1.EMG_BRAKE_2": ("1/2 green, 3 orange, 0 hidden", CONFIRMED),
    "main.2B1.LANE_ASSIST_2": ("1/2 green, 3 orange, 0 hidden", CONFIRMED),
    "main.2B1.BLIND_SPOT_2": ("shown in the top bar", None),
    "main.2B1.EMERGENCY_BRAKE_WARNING": ("orange forward zone on Driving Aids page, blinking arc in top bar", CONFIRMED),
    "main.2B1.LANE_WARN_LEFT": ("lane icon blinks orange (side not distinguishable on camera)", None),
    "main.2B1.LANE_WARN_RIGHT": ("lane icon blinks orange (side not distinguishable on camera)", None),
    "main.2B1.BSW_RIGHT_BLINK": ("orange marks blink at the rear of the car icon", None),
    "main.2B1.WINDING_ROAD": ("shown only while CAR_AHEAD is 1 (white) or 2 (yellow) (owner, bench)", CONFIRMED),
    "main.2B1.PROMPT_PAGE": ("shows the Driving Aids page, which reverts after a few seconds", CONFIRMED),
    "main.2B1.CC_BIT4": (NO_EFFECT, None),
    "main.284.FR_WHEEL_SPEED": ("48.28 km/h commanded (raw 9656, 0.005 km/h/bit) -> needle just over 30 mph, "
                                "meter 0x355 reports 50.07 km/h; 0.01 km/h/bit would be 60 mph", CONFIRMED),
    "main.551.FUEL_USAGE_COUNTER": ("instantaneous MPG = 13.1 x mph / counts per s: 30 mph at 19.65 and "
                                    "9.83 counts/s showed 20 and 40 mpg (q50tb/sim/fuel_economy.py)", CONFIRMED),
    "main.35D.UNKNOWN_B4_6": (NO_EFFECT, None),
    "main.216.WIPER_STATE": (NO_EFFECT, None), "main.216.OP_STATE": (NO_EFFECT, None),
    "main.216.HEADLIGHTS": ("no telltale (lamps follow 0x60D)", None), "main.216.FOG_LIGHTS": ("no telltale (lamps follow 0x60D)", None),
    "main.625.START_STATE": ("1 and 2: no visible change", None),
    "main.160.PEDAL_FULLY_PRESSED": (NO_EFFECT, None),
    "main.580.OIL_TEMP": (NO_EFFECT + " (150 degC)", None),
    "main.002.STEERING_ANGLE": ("Chassis Control page does not move with +-180 deg", None),
    "main.519.STATE": (NO_EFFECT + " (0xFF)", None),
}


def _apply_bench_notes(db: SignalDatabase) -> None:
    for path, (note, conf) in BENCH_2026_10_12.items():
        try:
            s = db.signal(path)
        except (KeyError, ValueError):
            continue
        if note:
            s.notes = (s.notes + "; " if s.notes else "") + "bench 2026-10-12: " + note
        if conf:
            s.confidence = conf


def build_database() -> SignalDatabase:
    db = SignalDatabase(_messages(), links=LINKED_SIGNALS)
    _apply_bench_notes(db)
    return db


# Meter-originated IDs: never transmitted by the bench (handoff rule 2).
METER_ORIGIN_MAIN = {0x280, 0x2DE, 0x355, 0x5C5}
METER_ORIGIN_AV = {0x683, 0x6A8, 0x6A9}
# Other AV-bus peers seen in car captures (TCU / display); also never sent by the HU emulator.
AV_PEER_IDS = {0x681, 0x686, 0x69D, 0x6AB, 0x6AC}
