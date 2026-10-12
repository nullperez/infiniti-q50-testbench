"""Monitor-mode safety, ownership rules, Kvaser path, codecs and capture formats."""

import os
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from q50tb.app_context import AppContext  # noqa: E402
from q50tb.av.services.common import (decode_distance4, decode_freq3, encode_distance4,  # noqa: E402
                                      encode_freq3)
from q50tb.av.transport import AvReassembler, segment  # noqa: E402
from q50tb.can.adapters import kvaser  # noqa: E402
from q50tb.can.adapters.base import AdapterInfo, BusConfig  # noqa: E402
from q50tb.can.frame import CanFrame  # noqa: E402
from q50tb.capture.formats import load_capture, save_capture  # noqa: E402
from q50tb.control.api import ApiError, BenchAPI  # noqa: E402
from q50tb.core.bus_manager import BusManager  # noqa: E402
from q50tb.core.config import AppSettings  # noqa: E402
from q50tb.core.log_buffer import IdFilter  # noqa: E402

ROOT = os.path.join(os.path.dirname(__file__), "..")


def test_monitor_mode_never_transmits():
    s = AppSettings()
    ctx = AppContext(s)
    api = BenchAPI(ctx)
    try:
        api.start_buses(main="virtual:mon-main", av="virtual:mon-av", monitor=True)
        # someone else is on the bus
        other = BusManager()
        other.start({"main": AdapterInfo("virtual", "mon-main", "v", supports_listen_only=True, is_virtual=True)},
                    BusConfig(), monitor_only=False, protected={})
        other.send("main", CanFrame(0x180, bytes.fromhex("8340000000000000")), "test")
        time.sleep(1.0)
        for ch in ctx.buses.channels.values():
            assert ch.monitor_only and ch.config.listen_only
            assert ch.stats.tx == 0, "monitor mode transmitted"
        assert all(f.direction == "RX" for f in ctx.log.snapshot())
        assert api.read_signal("main.180.ENGINE_RPM")["value"] == 4200
        for call in (lambda: api.send_frame("0x123", "00"), lambda: api.av_start(),
                     lambda: api.queue_add("0x123", "00", 100)):
            try:
                call()
            except ApiError:
                pass
        time.sleep(0.3)
        assert all(ch.stats.tx == 0 for ch in ctx.buses.channels.values())
        other.stop()
    finally:
        ctx.shutdown()


def test_monitor_mode_gates_background_messages():
    ctx = AppContext(AppSettings())
    api = BenchAPI(ctx)
    bg = ctx.background
    try:
        before = {k: m.enabled for k, m in bg.messages.items()}
        assert any(before.values())
        api.start_buses(main="virtual:gate-main", monitor=True)
        assert bg.gated and not any(m.enabled for m in bg.messages.values())
        for call in (lambda: api.background_enable_all(True), lambda: api.background_enable("0x180", True)):
            try:
                call()
                raise AssertionError("enabling a background message in monitoring mode was not refused")
            except Exception as exc:
                assert "monitoring mode" in str(exc)
        api.background_enable("0x180", False)  # turning off is still allowed
        assert not any(m.enabled for m in bg.messages.values())
        api.stop_buses()
        assert not bg.gated and {k: m.enabled for k, m in bg.messages.items()} == before
    finally:
        ctx.shutdown()


def test_ownership_and_protected():
    bm = BusManager()
    bm.start({"main": AdapterInfo("virtual", "own", "v", supports_listen_only=True, is_virtual=True)},
             BusConfig(), monitor_only=False, protected={"main": {0x280}})
    try:
        ch = bm.channels["main"]
        assert not ch.send(CanFrame(0x280, b"\0"), "x")
        ch.ownership.claim("bg", [0x100], 10)
        ch.ownership.claim("replay", [0x100], 30)
        assert not ch.send(CanFrame(0x100, b"\0"), "bg")
        assert ch.send(CanFrame(0x100, b"\0"), "replay")
        ch.ownership.release("replay")
        assert ch.send(CanFrame(0x100, b"\0"), "bg")
        assert not ch.send(CanFrame(0x100, bytes(12), is_fd=True), "bg")  # FD on classic bus
    finally:
        bm.stop()


def test_kvaser_virtual_channels():
    if not kvaser.available():
        pytest.skip("Kvaser CANlib/driver not installed")
    infos = [i for i in kvaser.discover() if i.is_virtual]
    if len(infos) < 2:
        pytest.skip("no Kvaser virtual channel pair")
    a, b = BusManager(), BusManager()
    a.start({"main": infos[0]}, BusConfig(), monitor_only=False, protected={})
    b.start({"main": infos[1]}, BusConfig(), monitor_only=True, protected={})
    try:
        got = []
        b.subscribe_all(got.append)
        for i in range(5):
            a.send("main", CanFrame(0x321, bytes([i] * 8)), "t")
        time.sleep(0.5)
        assert [f.data[0] for f in got if f.arbitration_id == 0x321] == [0, 1, 2, 3, 4]
        assert not b.send("main", CanFrame(0x321, b"\0"), "t")
    finally:
        a.stop()
        b.stop()


def test_nibble_codecs():
    assert encode_freq3(103.3, True) == bytes.fromhex("F10313")
    assert encode_freq3(96.7, True) == bytes.fromhex("FF9617")
    assert encode_freq3(530, False) == bytes.fromhex("F5300F")
    assert encode_freq3(1, False) == bytes.fromhex("FFF10F")
    assert decode_freq3(bytes.fromhex("F10313")) == "103.3"
    assert encode_distance4(8.9, True) == bytes.fromhex("FFFF819F")
    assert encode_distance4(500, False) == bytes.fromhex("FF5000FF")
    assert encode_distance4(0, False) == bytes.fromhex("FFFF00FF")
    assert decode_distance4(bytes.fromhex("FFFF719F")) == "7.9"


def test_transport_roundtrip():
    rec = bytes(range(57))
    frames = []
    from q50tb.av.transport import split_fragments
    for i, frag in enumerate(split_fragments(rec)):
        more = i < len(split_fragments(rec)) - 1
        frames += segment(0x20 | i | (0x08 if more else 0), frag)
    r = AvReassembler(0x628)
    out = None
    for f in frames:
        _m, recd, _k = r.feed(f, 0.0)
        out = recd or out
    assert out is not None and out.data == rec and out.fragments == 3
    # real capture bytes: 61D clock record
    r = AvReassembler(0x61D)
    r.feed(bytes.fromhex("100B10244D202609"), 0)
    _m, recd, _k = r.feed(bytes.fromhex("2129203603 02FFFF".replace(" ", "")), 0)
    assert recd.data == bytes.fromhex("244D2026092920360302")


def test_id_filter():
    f = IdFilter("359, 35")
    assert f.matches_id(0x359) and f.matches_id(0x35A) and not f.matches_id(0x360)
    f = IdFilter("0x2")
    assert f.matches_id(0x002) and not f.matches_id(0x200)
    f = IdFilter("-6A8")
    assert not f.matches_id(0x6A8) and f.matches_id(0x628)


def test_capture_roundtrip():
    path = os.path.join(ROOT, "reference_logs", "Q50_Dumps", "AV_STARTUP_2.pcapng")
    if not os.path.exists(path):
        pytest.skip("reference_logs not present")
    cap = load_capture(path)
    assert cap.frames and cap.interfaces == ["can1"]
    frames = cap.frames[:500]
    frames[3].direction = "TX"
    with tempfile.TemporaryDirectory() as tmp:
        for ext in (".pcapng", ".csv", ".log"):
            p = os.path.join(tmp, "x" + ext)
            save_capture(p, frames)
            back = load_capture(p)
            assert len(back.frames) == len(frames), ext
            assert back.frames[10].data == frames[10].data and back.frames[10].arbitration_id == frames[10].arbitration_id
            assert back.frames[3].direction == "TX", ext


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)


def test_rpm_mirrors_to_every_rpm_message():
    """The meter's tach follows 0x3EC when present, so ENGINE_RPM must reach every copy."""
    from q50tb.core.scheduler import Scheduler
    from q50tb.sim.background import BackgroundManager
    from q50tb.signals.q50_main import build_database

    db = build_database()
    bg = BackgroundManager(BusManager(), Scheduler(), db)
    bg.load_profile(os.path.join(ROOT, "profiles", "main_idle_baseline.json"))
    payload = lambda cid: bg.get("main", cid).next_payload(db)  # noqa: E731

    bg.set_signal("main", 0x180, "ENGINE_RPM", 3000)
    assert payload(0x180)[0:2] == bytes.fromhex("5DC0")
    assert payload(0x1F9)[2:4] == bytes.fromhex("5DC0")
    assert payload(0x3EC)[0:2] == bytes.fromhex("5DC0")
    assert payload(0x176)[2:4] == (3000).to_bytes(2, "big")

    bg.clear_signal("main", 0x180, "ENGINE_RPM")
    for cid in (0x1F9, 0x3EC, 0x176):
        assert not bg.get("main", cid).overlays


def test_media_status_record_only_without_metadata():
    """The 2A status record overlays a message ('not connected'), so it must not follow real metadata."""
    from q50tb.av.services.audio import AudioState, build_records, load_sources

    usb = {s.key: s for s in load_sources()}["USB"]
    with_meta = build_records(usb, AudioState(title="T", artist="A"))
    assert len(with_meta) == 1 and with_meta[0][0] == 0x22
    without = build_records(usb, AudioState(playing=False))
    assert [r[0] for r in without] == [0x22, 0x2A]


def test_clock_announces_12_24h_change():
    """The meter only switches 12/24 h after a record with mode bit1 clear (01/00)."""
    from q50tb.av.services.clock import ClockService

    class Host:
        sent = []
        def send(self, ch, rec, **kw):
            self.sent.append(rec)

    clk = ClockService(Host())
    clk.state.hour24 = True
    clk.send_now()
    assert [r[2] for r in Host.sent] == [0x01, 0x03]
    Host.sent.clear()
    clk.send_now()
    assert [r[2] for r in Host.sent] == [0x03]


def test_media_sources_cover_every_media_type():
    """Each media type the meter labels (bench 2026-10-12) has a selectable source."""
    from q50tb.av.services.audio import load_sources

    types = {s.key: s.media_type for s in load_sources() if s.kind == "media"}
    assert types == {"CD": 1, "MUSICBOX": 2, "IPOD": 3, "USB": 4, "BT": 5, "DVD": 6, "IAS": 7, "DISC": 8}


def test_bench_notes_point_at_real_signals():
    """build_database() skips unknown paths, so a typo in the bench table would vanish silently."""
    from q50tb.signals.q50_main import BENCH_2026_10_12, build_database

    db = build_database()
    for path in BENCH_2026_10_12:
        assert "bench 2026-10-12" in db.signal(path).notes, path


def test_engine_off_sets_stopped_flags_and_back():
    """Engine off = the ECU flags seen with IG on / engine stopped; running = captured idle again."""
    s = AppSettings()
    ctx = AppContext(s)
    try:
        bg = ctx.background
        payload = lambda cid: bg.get("main", cid).next_payload(ctx.db)  # noqa: E731
        assert ctx.engine_running()
        idle = {cid: payload(cid) for cid in (0x160, 0x182, 0x300, 0x580, 0x551)}
        ctx.set_engine_running(False)
        assert not ctx.engine_running()
        assert payload(0x160)[0] & 0x08 and not payload(0x160)[6] & 0x08
        assert payload(0x182)[5] & 0x20 and not payload(0x300)[0] & 0x02 and not payload(0x580)[7] & 0x20
        assert payload(0x180)[0:2] == bytes(2) and payload(0x3EC)[0:2] == bytes(2)
        assert payload(0x551)[3] == idle[0x551][3]          # ignition-ready bit untouched
        ctx.set_engine_running(True)
        assert ctx.engine_running()
        for cid, byte, mask in ((0x160, 0, 0x08), (0x160, 6, 0x08), (0x182, 5, 0x20), (0x300, 0, 0x02), (0x580, 7, 0x20)):
            assert payload(cid)[byte] & mask == idle[cid][byte] & mask
    finally:
        ctx.shutdown()


def test_fuel_economy_counter_rate():
    """0x551 byte 1 must advance at 13.1 * mph / mpg counts per second (CANSim bench fit)."""
    from q50tb.sim import fuel_economy as fe

    assert abs(fe.counts_per_second(60, 30, 0.3) - 26.2) < 1e-9
    assert abs(fe.counts_per_second(0, 30, 0.3) - 0.3 * fe.COUNTS_PER_GALLON / 3600) < 1e-9   # stopped: idle flow
    ctx = AppContext(AppSettings())
    try:
        ctx.background.set_signal("main", 0x284, "FR_WHEEL_SPEED", 60 * fe.KMH_PER_MPH)
        sim = fe.FuelEconomySim(ctx.background, ctx.scheduler)   # driven by hand, no scheduler job
        sim.set(target_mpg=30)
        sim._total, sim._last_t = 0.0, fe.timebase.now() - 0.4  # 0.4 s since the last tick
        sim._tick()
        assert abs(sim._total - 26.2 * 0.4) < 0.2
        assert "FUEL_USAGE_COUNTER" in ctx.background.get("main", 0x551).overlays
        sim = ctx.fuel
        sim.set(enabled=True, target_mpg=30)
        ctx.set_engine_running(False)
        assert sim.rate() == 0.0                # no fuel use with the engine off
        sim.set(enabled=False)
        assert "FUEL_USAGE_COUNTER" not in ctx.background.get("main", 0x551).overlays
    finally:
        ctx.shutdown()
