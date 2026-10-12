"""Manual transmit queue (CAN Message Send page).

Each entry is a frame with an optional period, rolling counter, checksum and
byte sweep. Enabled periodic entries claim their ID at manual priority, so a
background message with the same ID pauses while the entry is active and
resumes when it is removed (one transmitter per ID).
"""

from __future__ import annotations

import itertools
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

from q50tb.can.frame import CanFrame
from q50tb.core.bus_manager import PRIO_MANUAL, BusManager
from q50tb.core.scheduler import Job, Scheduler
from q50tb.sim.background import ChecksumRule, CounterRule

OWNER = "manual"
_uid = itertools.count(1)


@dataclass
class SweepSpec:
    mode: str = "bit"              # "bit" walking one, "counter" start..end step, "custom" list
    bytes_: list[int] = field(default_factory=lambda: [0])   # target bytes, swept one after another
    start: int = 0
    end: int = 255
    step: int = 1
    custom: list[int] = field(default_factory=list)
    include_zero: bool = False     # bit mode: also send 0x00 first
    dwell_ms: float = 1000.0       # time each value is held
    loop: bool = False

    def values(self) -> list[int]:
        if self.mode == "bit":
            vals = [1 << b for b in range(8)]
            return ([0] if self.include_zero else []) + vals
        if self.mode == "counter":
            step = self.step or 1
            if (self.end - self.start) * step < 0:
                step = -step
            return list(range(self.start, self.end + (1 if step > 0 else -1), step))
        return [v & 0xFF for v in self.custom]

    def plan(self) -> list[tuple[int, int]]:
        """[(byte index, value), ...] in transmit order."""
        vals = self.values()
        return [(b, v) for b in self.bytes_ for v in vals]

    def describe(self) -> str:
        what = {"bit": "walking bit", "counter": f"{self.start:02X}..{self.end:02X} step {self.step}",
                "custom": f"custom[{len(self.custom)}]"}[self.mode]
        return f"sweep B{','.join(map(str, self.bytes_))} {what} {self.dwell_ms:.0f}ms{' loop' if self.loop else ''}"


@dataclass
class SendEntry:
    bus: str
    arbitration_id: int
    data: bytes
    period_ms: float = 0.0         # 0 = send once
    is_extended: bool = False
    is_fd: bool = False
    bitrate_switch: bool = False
    name: str = ""
    count_limit: int = 0           # stop after N frames (0 = unlimited)
    counter: Optional[CounterRule] = None
    checksum: Optional[ChecksumRule] = None
    sweep: Optional[SweepSpec] = None
    enabled: bool = True
    uid: int = field(default_factory=lambda: next(_uid))
    # runtime
    tx_count: int = 0
    last_data: bytes = b""
    status: str = ""
    _ctr: int = 0
    _sweep_pos: int = 0
    _sweep_started: float = 0.0
    _job: Optional[Job] = field(default=None, repr=False)

    def describe(self) -> str:
        parts = []
        if self.counter:
            c = self.counter
            parts.append(f"ctr B{c.byte}[{c.lsb + c.width - 1}:{c.lsb}]+{c.step}")
        if self.checksum:
            parts.append(f"{self.checksum.kind} B{self.checksum.byte}")
        if self.sweep:
            parts.append(self.sweep.describe())
        if self.count_limit:
            parts.append(f"x{self.count_limit}")
        return ", ".join(parts)

    def next_payload(self, now: float) -> Optional[bytes]:
        data = bytearray(self.data)
        if self.sweep:
            plan = self.sweep.plan()
            if not plan:
                return None
            if self._sweep_started == 0.0:
                self._sweep_started = now
            pos = int((now - self._sweep_started) * 1000.0 // max(1.0, self.sweep.dwell_ms))
            if pos >= len(plan):
                if not self.sweep.loop:
                    return None
                pos %= len(plan)
            self._sweep_pos = pos
            b, v = plan[pos]
            if b < len(data):
                data[b] = v
        if self.counter and self.counter.byte < len(data):
            self.counter.apply(data, self._ctr)
            self._ctr = (self._ctr + self.counter.step) & ((1 << self.counter.width) - 1)
        if self.checksum:
            self.checksum.apply(data)
        return bytes(data)

    @property
    def sweep_progress(self) -> str:
        if not self.sweep:
            return ""
        plan = self.sweep.plan()
        if not plan:
            return ""
        b, v = plan[min(self._sweep_pos, len(plan) - 1)]
        return f"{self._sweep_pos + 1}/{len(plan)} B{b}={v:02X}"


class SendQueue:
    def __init__(self, buses: BusManager, scheduler: Scheduler):
        self.buses = buses
        self.scheduler = scheduler
        self.entries: dict[int, SendEntry] = {}
        self._lock = threading.RLock()
        self.listeners: list[Callable[[], None]] = []
        buses.add_state_listener(self._resync)

    def send_once(self, frame: CanFrame, raise_on_refuse: bool = True) -> bool:
        return self.buses.send(frame.bus, frame, OWNER, raise_on_refuse=raise_on_refuse)

    def add(self, entry: SendEntry) -> SendEntry:
        CanFrame(entry.arbitration_id, entry.data, is_extended=entry.is_extended, is_fd=entry.is_fd,
                 bitrate_switch=entry.bitrate_switch).validate()
        with self._lock:
            self.entries[entry.uid] = entry
            self._start(entry)
        self._changed()
        return entry

    def remove(self, uid: int) -> None:
        with self._lock:
            e = self.entries.pop(uid, None)
            if e:
                self._stop(e)
        self._changed()

    def clear(self) -> None:
        with self._lock:
            for e in list(self.entries.values()):
                self._stop(e)
            self.entries.clear()
        self._changed()

    def set_enabled(self, uid: int, on: bool) -> None:
        with self._lock:
            e = self.entries.get(uid)
            if not e:
                return
            e.enabled = on
            self._stop(e)
            if on:
                e._sweep_started = 0.0
                self._start(e)
        self._changed()

    def update(self, uid: int, **fields) -> None:
        with self._lock:
            e = self.entries.get(uid)
            if not e:
                return
            self._stop(e)
            for k, v in fields.items():
                setattr(e, k, v)
            e._sweep_started = 0.0
            self._start(e)
        self._changed()

    def _start(self, e: SendEntry) -> None:
        if not e.enabled:
            return
        ch = self.buses.get(e.bus)
        if ch is None or not ch.can_transmit():
            e.status = "bus not open for TX"
            return
        if e.period_ms <= 0:
            payload = e.next_payload(self.scheduler_now())
            if payload is not None:
                ok = ch.send(CanFrame(e.arbitration_id, payload, is_extended=e.is_extended, is_fd=e.is_fd,
                                      bitrate_switch=e.bitrate_switch, bus=e.bus), OWNER)
                e.tx_count += int(ok)
                e.last_data = payload
                e.status = "sent" if ok else f"refused ({ch.check_send(OWNER, CanFrame(e.arbitration_id, payload, is_fd=e.is_fd))})"
            e.enabled = False
            return
        ch.ownership.claim(f"{OWNER}:{e.uid}", [e.arbitration_id], PRIO_MANUAL)
        e._job = self.scheduler.every(e.period_ms / 1000.0, lambda: self._tick(e), name=f"manual {e.uid}")
        e.status = "running"

    def _stop(self, e: SendEntry) -> None:
        if e._job:
            e._job.cancel()
            e._job = None
        for ch in self.buses.channels.values():
            ch.ownership.release(f"{OWNER}:{e.uid}")
        if e.status == "running":
            e.status = "stopped"

    @staticmethod
    def scheduler_now() -> float:
        import time
        return time.monotonic()

    def _tick(self, e: SendEntry) -> None:
        ch = self.buses.channels.get(e.bus)
        if ch is None:
            return
        payload = e.next_payload(self.scheduler_now())
        if payload is None:
            e.status = "sweep finished"
            with self._lock:
                e.enabled = False
                self._stop(e)
            self._changed()
            return
        frame = CanFrame(e.arbitration_id, payload, is_extended=e.is_extended, is_fd=e.is_fd,
                         bitrate_switch=e.bitrate_switch, bus=e.bus)
        if ch.send(frame, f"{OWNER}:{e.uid}"):
            e.tx_count += 1
            e.last_data = payload
        else:
            e.status = f"refused: {ch.check_send(f'{OWNER}:{e.uid}', frame)}"
        if e.count_limit and e.tx_count >= e.count_limit:
            with self._lock:
                e.enabled = False
                self._stop(e)
                e.status = f"done ({e.tx_count} frames)"
            self._changed()

    def _resync(self) -> None:
        with self._lock:
            for e in self.entries.values():
                self._stop(e)
                if e.enabled and e.period_ms > 0:
                    self._start(e)
        self._changed()

    def _changed(self) -> None:
        for fn in list(self.listeners):
            try:
                fn()
            except Exception:
                pass
