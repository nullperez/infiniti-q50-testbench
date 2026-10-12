"""Whole stack through BenchAPI on python-can virtual buses (no hardware)."""

import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from q50tb.app_context import AppContext  # noqa: E402
from q50tb.av.virtual_meter import VirtualMeter  # noqa: E402
from q50tb.can.adapters.base import AdapterInfo, BusConfig  # noqa: E402
from q50tb.control.api import TOOLS, ApiError, BenchAPI  # noqa: E402
from q50tb.core.bus_manager import BusManager  # noqa: E402
from q50tb.core.config import AppSettings  # noqa: E402
from q50tb.core.scheduler import Scheduler  # noqa: E402


def wait(pred, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.05)
    return False


def test_stack():
    s = AppSettings()
    s.control_server = False
    ctx = AppContext(s)
    api = BenchAPI(ctx)
    meter_bus = BusManager()
    msched = Scheduler()
    msched.start()
    try:
        api.start_buses(main="virtual:api-main", av="virtual:api-av")
        info = AdapterInfo("virtual", "api-av", "virt", supports_listen_only=True, is_virtual=True)
        meter_bus.start({"av": info}, BusConfig(), monitor_only=False, protected={})
        vm = VirtualMeter(meter_bus, msched)
        vm.start()

        st = api.status()
        assert st["running"] and st["background"]["enabled"] > 50, st
        assert wait(lambda: api.status()["buses"]["main"]["tx"] > 500)

        api.set_signal("main.180.ENGINE_RPM", 3000)
        api.set_signal("main.421.GEAR", 4)
        assert wait(lambda: abs((api.read_signal("main.180.ENGINE_RPM")["value"] or 0) - 3000) < 1)
        dash = api.get_dashboard()
        assert dash["gear"]["text"] == "D", dash["gear"]
        # counters keep rolling on overridden messages
        frames = [f for f in ctx.log.snapshot()[-3000:] if f.arbitration_id == 0x180]
        assert len({f.data[6] & 0xF for f in frames}) > 8

        # meter-originated IDs are refused
        try:
            api.send_frame("0x280", "00 00 00 00 00 00 00 00")
            raise AssertionError("protected ID was sent")
        except ApiError:
            pass

        # manual queue entry takes ownership of an ID from background
        q = api.queue_add("0x60D", "08 00 00 00 00 00 00 00", 50)
        assert wait(lambda: any(f.data[0] == 0x08 and f.source.startswith("manual")
                                for f in ctx.log.snapshot()[-400:] if f.arbitration_id == 0x60D))
        api.queue_remove(q["uid"])

        sw = api.queue_sweep("0x123", "00 00 00 00", [1], mode="bit", dwell_ms=20, period_ms=10)
        assert sw["steps"] == 8
        assert wait(lambda: any(f.arbitration_id == 0x123 and f.data[1] == 0x80 for f in ctx.log.snapshot()[-2000:]))

        # AV
        api.av_start()
        assert api.av_wait_ready(10)["ready"], api.av_status()
        assert wait(lambda: ctx.hu.settings.page_image is not None)
        r = api.av_set_page("Navigation", False)
        assert r["ok"], r
        r = api.av_set_units("kPa")
        assert r["ok"], r
        try:
            api.av_factory_reset()
            raise AssertionError("factory reset without confirm")
        except ApiError:
            pass
        assert api.av_factory_reset(confirm=True)["ok"]
        api.av_audio("FM", {"frequency": 96.7, "station": "KXYZ", "radio_text": "hello"})
        api.av_nav(17, maneuver=4, street="CUSTER ST", distance=500, unit="ft", progress=50)
        api.av_clock(time="09:25", hour24=False)
        assert wait(lambda: any(d[:3] == bytes([0x40, 0x0D, 0x02]) and d[3] == 0xF9 for _t, d in vm.received))
        assert len(api.av_log(50)) > 10

        # record + replay
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "rec.pcapng")
            api.record_start(path)
            time.sleep(1.0)
            api.record_stop()
            info = api.replay_load(0, path, "main", excluded="0x2B1")
            assert info["frames"] > 100 and "main" in info["interfaces"]
            ctx.replay.tracks[0].interfaces = {"main"}
            api.replay_start(tracks=[0])
            assert wait(lambda: ctx.replay.tracks[0].sent > 50)
            api.replay_stop()

        assert "set_signal" in TOOLS and TOOLS["set_signal"]["input_schema"]["required"] == ["path", "value"]
        api.av_stop()
        vm.stop()
    finally:
        ctx.shutdown()
        meter_bus.stop()
        msched.stop()


if __name__ == "__main__":
    test_stack()
    print("ok")
