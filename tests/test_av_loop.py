"""End-to-end: HU emulator <-> virtual meter over a python-can virtual bus."""

import os
import sys
import threading
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from q50tb.av.headunit import READY, HeadUnitEmulator  # noqa: E402
from q50tb.av.sniffer import AvSniffer  # noqa: E402
from q50tb.av.virtual_meter import VirtualMeter  # noqa: E402
from q50tb.can.adapters.base import AdapterInfo, BusConfig  # noqa: E402
from q50tb.core.bus_manager import BusManager  # noqa: E402
from q50tb.core.scheduler import Scheduler  # noqa: E402


def wait(pred, timeout=8.0):
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(0.02)
    return False


def make(channel):
    bm = BusManager()
    sched = Scheduler()
    sched.start()
    info = AdapterInfo("virtual", channel, "virt", supports_listen_only=True, is_virtual=True)
    bm.start({"av": info}, BusConfig(), monitor_only=False, protected={})
    return bm, sched


def test_handshake_and_settings():
    app_bus, app_sched = make("t-av-1")
    m_bus, m_sched = make("t-av-1")
    try:
        sniffer = AvSniffer()
        app_bus.subscribe_all(sniffer.on_frame)
        hu = HeadUnitEmulator(app_bus, app_sched, sniffer)
        hu.start("av")
        vm = VirtualMeter(m_bus, m_sched)
        vm.start()
        assert wait(lambda: all(hu.channel_ready(n) for n in ("status", "settings", "nav"))), hu.status()
        # settings read_all is scheduled after open
        assert wait(lambda: hu.settings.page_image is not None)
        assert hu.settings.page_image == bytes.fromhex("55DD5FFDFDC0000000")

        res = {}
        ev = threading.Event()
        hu.settings.set_page("Tire Pressure", False, lambda ok, why: (res.update(ok=ok, why=why), ev.set()))
        assert ev.wait(5), "no page result"
        assert res["ok"], res
        assert vm.items[0x2][1] == 0xDC

        ev.clear()
        vm.reject_page_writes = True
        hu.settings.set_page("Tire Pressure", True, lambda ok, why: (res.update(ok=ok, why=why), ev.set()))
        assert ev.wait(5)
        assert not res["ok"] and "rejected" in res["why"], res

        ev.clear()
        hu.settings.set_units(0x03, lambda ok, why: (res.update(ok=ok, why=why), ev.set()))
        assert ev.wait(5) and res["ok"], res

        # long audio record goes out as 20-byte fragments and arrives intact
        hu.audio.select("USB", title="A very long track title for fragmentation", album="Album", artist="Artist")
        assert wait(lambda: any(d[:2] == b"\x22\x0D" for _t, d in vm.received)), "no media record"
        rec = [d for _t, d in vm.received if d[:2] == b"\x22\x0D"][-1]
        assert "A very long track title".encode("utf-16-le") in rec

        hu.nav.update(state=0x11, maneuver=4, street="CUSTER ST", distance=0.3, progress=40)
        assert wait(lambda: any(d[:3] == b"\x20\x0D\x11" for t, d in vm.received if t == 0x6A9))

        # meter reboot -> re-announce -> channels re-open
        vm.reboot()
        assert wait(lambda: all(hu.channel_ready(n) for n in ("status", "settings", "nav")), 10)
        hu.stop()
        vm.stop()
    finally:
        app_bus.stop()
        m_bus.stop()
        app_sched.stop()
        m_sched.stop()


if __name__ == "__main__":
    test_handshake_and_settings()
    print("ok")
