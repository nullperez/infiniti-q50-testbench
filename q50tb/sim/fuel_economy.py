"""Instantaneous fuel economy: drives the 0x551 byte-1 fuel counter.

The meter computes instantaneous economy itself from vehicle speed and the rate of
0x551 byte 1, a modulo-256 cumulative fuel counter (bench-confirmed 2026-10-06 in the
owner's CANSim, captures/fuel_economy_2026-10-06):

    MPG = K * speed_mph / C        C = byte-1 increments per second, K = 13.1

So a target MPG is reached by advancing the counter at C = K * speed / MPG. Below
MOVING_MPH the economy is undefined and the counter follows an idle fuel flow. The
counter is derived from elapsed time (not per tick), so scheduler jitter does not
change the rate the meter measures. Average economy is the meter's own calculation.
"""

from __future__ import annotations

from typing import Optional

from q50tb.core import timebase
from q50tb.core.scheduler import Job, Scheduler
from q50tb.sim.background import BackgroundManager

FUEL_K = 13.1                       # MPG * counts_per_s / mph (CANSim bench fit, CV 4 %)
COUNTS_PER_GALLON = 3600 * FUEL_K   # ~47,160 counts per US gallon
MOVING_MPH = 0.5
KMH_PER_MPH = 1.609344
MAX_COUNTS_PER_S = 1000             # 100 per 0x551 frame: well clear of the mod-256 wrap
TICK_S = 0.02


def counts_per_second(speed_mph: float, target_mpg: float, idle_gph: float) -> float:
    if speed_mph >= MOVING_MPH and target_mpg > 0:
        return min(MAX_COUNTS_PER_S, FUEL_K * speed_mph / target_mpg)
    return max(0.0, idle_gph) * COUNTS_PER_GALLON / 3600.0


class FuelEconomySim:
    def __init__(self, background: BackgroundManager, scheduler: Scheduler):
        self.bg = background
        self.sched = scheduler
        self.target_mpg = 25.0
        self.idle_gph = 0.30
        self._job: Optional[Job] = None
        self._total = 0.0
        self._last_t: Optional[float] = None

    @property
    def enabled(self) -> bool:
        return self._job is not None

    def speed_mph(self) -> float:
        """Speed the meter sees (0x284 front-wheel speed, as commanded)."""
        kmh = self.bg.signal_value("main", 0x284, "FR_WHEEL_SPEED") or 0.0
        return kmh / KMH_PER_MPH

    def engine_stopped(self) -> bool:
        msg = self.bg.messages.get(("main", 0x182))
        return bool(msg and msg.overlays.get("ENGINE_STOPPED"))

    def rate(self) -> float:
        if self.engine_stopped():
            return 0.0
        return counts_per_second(self.speed_mph(), self.target_mpg, self.idle_gph)

    def set(self, enabled: Optional[bool] = None, target_mpg: Optional[float] = None,
            idle_gph: Optional[float] = None) -> None:
        if target_mpg is not None:
            self.target_mpg = max(0.1, float(target_mpg))
        if idle_gph is not None:
            self.idle_gph = max(0.0, float(idle_gph))
        if enabled is True and self._job is None:
            msg = self.bg.ensure("main", 0x551)
            self._total = float(msg.base[1]) if len(msg.base) > 1 else 0.0
            self._last_t = timebase.now()
            self._job = self.sched.every(TICK_S, self._tick, name="fuel economy")
        elif enabled is False and self._job is not None:
            self._job.cancel()
            self._job = None
            self.bg.clear_signal("main", 0x551, "FUEL_USAGE_COUNTER")

    def _tick(self) -> None:
        now = timebase.now()
        dt = max(0.0, min(now - (self._last_t or now), 0.5))     # ignore stalls
        self._last_t = now
        self._total += self.rate() * dt
        self.bg.set_signal_raw("main", 0x551, "FUEL_USAGE_COUNTER", int(self._total) & 0xFF, quiet=True)

    def status(self) -> dict:
        speed = self.speed_mph()
        moving = speed >= MOVING_MPH
        return {"enabled": self.enabled, "target_mpg": self.target_mpg, "idle_gph": self.idle_gph,
                "speed_mph": round(speed, 2), "counts_per_s": round(self.rate(), 3),
                "expected_display_mpg": round(min(60.0, self.target_mpg), 1) if moving else None}
