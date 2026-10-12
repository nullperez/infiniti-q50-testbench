"""SocketCAN backend on Linux virtual CAN (vcan0 + vcan1). Skipped elsewhere.

Create the interfaces first:  sudo tools/setup_vcan.sh
"""

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from q50tb.can.adapters import pythoncan  # noqa: E402
from q50tb.can.adapters.base import BusConfig  # noqa: E402
from q50tb.can.frame import CanFrame  # noqa: E402
from q50tb.core.bus_manager import BusManager  # noqa: E402

LINKS = pythoncan.socketcan_links()
pytestmark = pytest.mark.skipif(not {"vcan0", "vcan1"} <= set(LINKS),
                                reason="needs Linux vcan0 and vcan1 (sudo tools/setup_vcan.sh)")


def _info(name):
    return next(i for i in pythoncan.discover() if i.key == f"socketcan:{name}")


def test_discovery_reports_vcan():
    info = _info("vcan0")
    assert info.is_virtual and info.supports_listen_only


def test_tx_rx_and_monitor_mode_on_vcan():
    sender, monitor = BusManager(), BusManager()
    sender.start({"main": _info("vcan0")}, BusConfig(), monitor_only=False, protected={})
    monitor.start({"main": _info("vcan0")}, BusConfig(), monitor_only=True, protected={})
    try:
        got = []
        monitor.subscribe_all(got.append)
        for i in range(5):
            assert sender.send("main", CanFrame(0x180, bytes([i] * 8)), "t")
        time.sleep(0.5)
        assert [f.data[0] for f in got if f.arbitration_id == 0x180] == [0, 1, 2, 3, 4]
        assert all(f.direction == "RX" for f in got)
        assert not monitor.send("main", CanFrame(0x180, b"\0"), "t")
        assert monitor.channels["main"].stats.tx == 0
    finally:
        sender.stop()
        monitor.stop()


def test_full_stack_on_vcan():
    """Main bus on vcan0, AV on vcan1, HU emulator against the virtual meter."""
    from q50tb.app_context import AppContext
    from q50tb.av.virtual_meter import VirtualMeter
    from q50tb.control.api import BenchAPI
    from q50tb.core.config import AppSettings
    from q50tb.core.scheduler import Scheduler

    ctx = AppContext(AppSettings())
    api = BenchAPI(ctx)
    meter_bus, msched = BusManager(), Scheduler()
    msched.start()
    try:
        api.start_buses(main="socketcan:vcan0", av="socketcan:vcan1")
        meter_bus.start({"av": _info("vcan1")}, BusConfig(), monitor_only=False, protected={})
        vm = VirtualMeter(meter_bus, msched)
        vm.start()
        api.set_signal("main.180.ENGINE_RPM", 2000)
        api.av_start()
        assert api.av_wait_ready(10)["ready"], api.av_status()
        assert api.av_set_units("bar")["ok"]
        time.sleep(0.3)
        assert abs(api.read_signal("main.180.ENGINE_RPM")["value"] - 2000) < 1
        vm.stop()
    finally:
        ctx.shutdown()
        meter_bus.stop()
        msched.stop()
