"""Time-based signal effects driven by the scheduler (e.g. turn-signal blinking)."""

from __future__ import annotations

from typing import Optional

from q50tb.core import timebase
from q50tb.core.scheduler import Job, Scheduler
from q50tb.sim.background import BackgroundManager
from q50tb.signals.model import SignalDatabase


class Effects:
    def __init__(self, background: BackgroundManager, scheduler: Scheduler, db: SignalDatabase):
        self.bg = background
        self.sched = scheduler
        self.db = db
        self._blink: dict[str, Job] = {}

    def blink(self, path: str, half_period_s: float = 0.4) -> None:
        """Toggle a 1-bit signal on/off (BCM-style flasher ~1.25 Hz by default)."""
        self.stop(path)
        s = self.db.signal(path)
        m = s.message
        state = {"on": None}

        def tick():
            # Phase comes from the clock, so every blinker with the same period stays in step
            # (hazards: both turn bits flash together).
            on = int(timebase.now() / half_period_s) % 2 == 0
            if on != state["on"]:
                state["on"] = on
                self.bg.set_signal_raw(m.bus, m.arbitration_id, s.name, 1 if on else 0)
        tick()
        self._blink[path] = self.sched.every(min(0.05, half_period_s / 4), tick, name=f"blink {path}")

    def stop(self, path: str, final: Optional[int] = 0) -> None:
        job = self._blink.pop(path, None)
        if job:
            job.cancel()
            if final is not None:
                s = self.db.signal(path)
                self.bg.set_signal_raw(s.message.bus, s.message.arbitration_id, s.name, final)

    def blinking(self, path: str) -> bool:
        return path in self._blink

    def stop_all(self) -> None:
        for p in list(self._blink):
            self.stop(p)
