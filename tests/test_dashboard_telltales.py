"""Dashboard telltale icons: signal paths exist, lit / blinking / no-data logic."""

from q50tb.can.frame import CanFrame
from q50tb.core import timebase
from q50tb.signals.dashboard import TELLTALES, Dashboard
from q50tb.signals.q50_main import build_database
from q50tb.signals.vehicle_state import VehicleState

BY_KEY = {t.key: t for t in TELLTALES}


def _setup():
    db = build_database()
    vs = VehicleState(db)
    return db, vs, Dashboard(vs)


def _feed(db, vs, cid: int, **raw) -> None:
    msg = db.get("main", cid)
    data = bytearray(max(msg.dlc or 8, 8))
    for name, value in raw.items():
        msg.signal(name).insert_raw(data, value)
    vs.on_frame(CanFrame(cid, bytes(data), timestamp=timebase.now(), bus="main"))


def test_every_telltale_path_and_icon_exists():
    from q50tb.ui.widgets.telltales import ICONS
    db = build_database()
    for t in TELLTALES:
        assert t.key in ICONS, t.key
        for path, _v in t.on + t.blink:
            db.signal(path)  # raises KeyError if the path is wrong


def test_no_data_is_not_off():
    _db, _vs, d = _setup()
    assert d.telltale(BY_KEY["abs"]) == (None, False)
    assert d.telltale(BY_KEY["airbag"]) == (None, False)  # no known signal


def test_lit_and_off():
    db, vs, d = _setup()
    _feed(db, vs, 0x60D, LEFT_TURN=1)
    assert d.telltale(BY_KEY["left_turn"]) == (True, False)
    assert d.telltale(BY_KEY["right_turn"]) == (False, False)
    _feed(db, vs, 0x354, ABS_LATCH=1)
    assert d.telltale(BY_KEY["abs"]) == (True, False)


def test_shared_brake_lamp_and_drl():
    db, vs, d = _setup()
    _feed(db, vs, 0x5C5, LOW_BRAKE_FLUID=1)
    assert d.telltale(BY_KEY["parking_brake"]) == (True, False)
    _feed(db, vs, 0x5C5)
    _feed(db, vs, 0x354, BRAKE_LATCH=1)
    assert d.telltale(BY_KEY["parking_brake"]) == (True, False)
    _feed(db, vs, 0x216, DRL_ON=1)
    assert d.telltale(BY_KEY["parking_lights"]) == (True, False)


def test_hold_bits_freeze_lamp():
    # bench 2026-10-12: 0A on, 1E stays on, 14 stays on (held), 00 off, 14 stays off, 1E stays off, 0A on
    db, vs, d = _setup()
    abs_t = BY_KEY["abs"]
    for b4, expect in ((0x0A, True), (0x1E, True), (0x14, True), (0x00, False), (0x14, False), (0x1E, False),
                       (0x0A, True)):
        _feed(db, vs, 0x354, ABS=b4 >> 4 & 1, ABS_LATCH=b4 >> 3 & 1, BRAKE=b4 >> 2 & 1, BRAKE_LATCH=b4 >> 1 & 1)
        assert d.telltale(abs_t)[0] is expect, hex(b4)
        assert d.telltale(BY_KEY["parking_brake"])[0] is expect, hex(b4)


def test_feb_blink():
    db, vs, d = _setup()
    _feed(db, vs, 0x2B1, FEB_LIGHT=1)
    assert d.telltale(BY_KEY["feb"]) == (True, False)
    _feed(db, vs, 0x2B1, FEB_LIGHT_BLINK=1)
    assert d.telltale(BY_KEY["feb"]) == (True, True)


def test_tcs_state_enum_and_blink():
    db, vs, d = _setup()
    _feed(db, vs, 0x354, TCS_STATE=2)
    assert d.telltale(BY_KEY["tcs_off"]) == (True, False)
    assert d.telltale(BY_KEY["slip"]) == (False, False)
    _feed(db, vs, 0x354, TCS_STATE=4)
    assert d.telltale(BY_KEY["slip"]) == (True, True)
    assert d.telltale(BY_KEY["tcs_off"]) == (False, False)
    _feed(db, vs, 0x551, MIL_FLASH=1)
    assert d.telltale(BY_KEY["mil"]) == (True, True)


# ── centre display (LCD) messages ───────────────────────────────────
def _texts(d):
    return [(m.text, m.color) for m in d.lcd_messages()]


def test_lcd_door_open_red_only_when_moving():
    from q50tb.signals.dashboard import AMBER, RED
    db, vs, d = _setup()
    _feed(db, vs, 0x60D, DRIVER_DOOR=1)
    assert _texts(d) == [("Driver Door Open", AMBER)]
    _feed(db, vs, 0x354, VEHICLE_SPEED=db.signal("main.354.VEHICLE_SPEED").phys_to_raw(30))
    assert _texts(d) == [("Driver Door Open", RED)]
    _feed(db, vs, 0x60D, DRIVER_DOOR=1, TRUNK=1)
    m = d.lcd_messages()[0]
    assert (m.text, m.doors) == ("2 Doors Open", ("Driver", "Trunk"))


def test_lcd_at_error_needs_rpm_and_red_sorts_first():
    from q50tb.signals.dashboard import AMBER, RED
    db, vs, d = _setup()
    _feed(db, vs, 0x421, AT_ERROR_B=1)
    assert _texts(d) == []
    _feed(db, vs, 0x180, ENGINE_RPM=db.signal("main.180.ENGINE_RPM").phys_to_raw(800))
    _feed(db, vs, 0x56C, CHASSIS_ERROR=1)
    _feed(db, vs, 0x351, KEY_PROMPT=0x5)
    assert _texts(d) == [("Shift to Park", RED), ("AT Error", AMBER), ("Chassis Control System Error", AMBER)]


def test_bench_input_telltales():
    _db, _vs, d = _setup()
    assert d.telltale(BY_KEY["airbag"]) == (None, False)  # no bench
    bench = {"AIRBAG": True, "ALTERNATOR": True}
    d.bench_inputs = lambda: bench
    assert d.telltale(BY_KEY["airbag"]) == (False, False)  # checked = lamp off
    assert d.telltale(BY_KEY["charge"]) == (True, False)   # checked = lamp on
    bench.update(AIRBAG=False, ALTERNATOR=False)
    assert d.telltale(BY_KEY["airbag"]) == (True, False)
    assert d.telltale(BY_KEY["charge"]) == (False, False)
