"""Monitor-mode safety, ownership rules, Kvaser path, codecs and capture formats."""

import os
import sys
import tempfile
import time

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
        print("kvaser not available - skipped")
        return
    infos = [i for i in kvaser.discover() if i.is_virtual]
    if len(infos) < 2:
        print("no kvaser virtual pair - skipped")
        return
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
    cap = load_capture(os.path.join(ROOT, "reference_logs", "Q50_Dumps", "AV_STARTUP_2.pcapng"))
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
