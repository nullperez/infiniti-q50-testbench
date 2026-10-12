"""Render every page offscreen with simulated buses + a virtual meter and save PNGs.

    python tools/screenshot_pages.py <out_dir>
"""

from __future__ import annotations

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
if sys.platform.startswith("win"):
    os.environ.setdefault("QT_QPA_FONTDIR", os.path.join(os.environ.get("WINDIR", "C:/Windows"), "Fonts"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from PySide6.QtWidgets import QApplication  # noqa: E402

from q50tb.app_context import AppContext  # noqa: E402
from q50tb.av.virtual_meter import VirtualMeter  # noqa: E402
from q50tb.can.adapters.base import AdapterInfo, BusConfig  # noqa: E402
from q50tb.core.bus_manager import BusManager  # noqa: E402
from q50tb.core.config import AppSettings  # noqa: E402
from q50tb.core.scheduler import Scheduler  # noqa: E402
from q50tb.ui.main_window import MainWindow  # noqa: E402
from q50tb.ui.theme import apply_theme  # noqa: E402


def pump(app, seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.02)


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "screens"
    os.makedirs(out, exist_ok=True)
    app = QApplication(sys.argv)
    apply_theme(app)
    s = AppSettings()
    s.control_server = False
    ctx = AppContext(s)
    win = MainWindow(ctx)
    win.resize(1600, 1000)
    win.show()
    ctx.start_buses("virtual:shot-main", "virtual:shot-av")
    mb = BusManager()
    ms = Scheduler()
    ms.start()
    mb.start({"av": AdapterInfo("virtual", "shot-av", "v", supports_listen_only=True, is_virtual=True)},
             BusConfig(), monitor_only=False, protected={})
    vm = VirtualMeter(mb, ms)
    vm.start()
    ctx.hu.start("av")
    pump(app, 2.5)
    for path, v in (("main.180.ENGINE_RPM", 3200), ("main.354.VEHICLE_SPEED", 0), ("main.421.GEAR", 4),
                    ("main.551.COOLANT_TEMP", 92), ("main.60D.DRIVER_DOOR", 1), ("main.385.FL_PRESSURE", 33.5),
                    ("main.385.RR_WARNING", 1), ("main.002.STEERING_ANGLE", -45), ("main.351.KEY_PROMPT", 1),
                    ("main.551.MIL_SOLID", 1), ("main.56C.DRIVE_MODE", 1)):
        ctx.background.set_signal(*path.split(".")[0:1], int(path.split(".")[1], 16), path.split(".")[2], v)
    ctx.hu.audio.select("FM", frequency=103.3, preset=2, station="KVDT", radio_text="Rel_Talk")
    ctx.hu.nav.update(state=0x11, maneuver=4, street="THOMAS CHAPEL DR", distance=300, unit=8, progress=60, heading=5)
    ctx.tracker.add(0x180)
    ctx.tracker.add(0x60D)
    ctx.tracker.add(0x123)
    pump(app, 1.5)
    for i, page in enumerate(win.pages):
        for r in range(win.nav.count()):
            if win.nav.item(r).data(256) == i:
                win.nav.setCurrentRow(r)
        pump(app, 0.6)
        name = page.title.replace(" ", "_").lower()
        win.grab().save(os.path.join(out, f"{i:02d}_{name}.png"))
        print("saved", name)
    ctrl = win.pages[0]
    win.nav.setCurrentRow(1)
    for t in range(ctrl.tabs.count()):
        ctrl.tabs.setCurrentIndex(t)
        pump(app, 0.4)
        win.grab().save(os.path.join(out, f"ctrl_{t:02d}.png"))
    ctx.hu.stop()
    vm.stop()
    mb.stop()
    ms.stop()
    win.close()


if __name__ == "__main__":
    main()
