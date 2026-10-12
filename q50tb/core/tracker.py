"""CAN message tracker: watched IDs with per-byte change highlighting.

Byte status (what the UI colours):
    WAITING  never received, or the frame is shorter than this byte
    SAME     unchanged since the last highlight reset
    CHANGED_LAST  changed on the most recent frame
    CHANGED  changed since the last highlight reset (but not on the last frame)
A whole message is STALE when nothing arrived for ``stale_s``.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Optional

from q50tb.can.frame import CanFrame
from q50tb.core import timebase

WAITING, SAME, CHANGED_LAST, CHANGED = "waiting", "same", "changed_last", "changed"


@dataclass
class Watch:
    bus: Optional[str]          # None = any bus
    arbitration_id: int
    directions: tuple = ("RX", "TX")
    data: Optional[bytes] = None
    prev: Optional[bytes] = None
    reference: Optional[bytes] = None   # snapshot at the last highlight reset
    changed_mask: list[bool] = field(default_factory=lambda: [False] * 64)
    last_t: Optional[float] = None
    count: int = 0
    period_ms: Optional[float] = None
    last_dir: str = ""
    last_bus: str = ""

    @property
    def key(self) -> tuple:
        return (self.bus, self.arbitration_id)

    def feed(self, frame: CanFrame) -> None:
        data = bytes(frame.data)
        if self.last_t is not None:
            dt = (frame.timestamp - self.last_t) * 1000
            self.period_ms = dt if self.period_ms is None else self.period_ms * 0.9 + dt * 0.1
        self.prev, self.data = self.data, data
        if self.reference is None:
            self.reference = data
        for i in range(len(data)):
            if i >= len(self.reference) or data[i] != self.reference[i]:
                self.changed_mask[i] = True
        self.last_t = frame.timestamp
        self.count += 1
        self.last_dir = frame.direction
        self.last_bus = frame.bus

    def reset_highlights(self) -> None:
        self.reference = self.data
        self.changed_mask = [False] * 64

    def byte_states(self, n: int = 8) -> list[tuple[Optional[int], str]]:
        out = []
        for i in range(max(n, len(self.data or b""))):
            if self.data is None or i >= len(self.data):
                out.append((None, WAITING))
                continue
            v = self.data[i]
            if self.prev is not None and i < len(self.prev) and self.prev[i] != v:
                out.append((v, CHANGED_LAST))
            elif self.prev is not None and i >= len(self.prev):
                out.append((v, CHANGED_LAST))
            elif self.changed_mask[i]:
                out.append((v, CHANGED))
            else:
                out.append((v, SAME))
        return out

    def age(self) -> Optional[float]:
        return None if self.last_t is None else timebase.now() - self.last_t


class Tracker:
    def __init__(self):
        self.watches: dict[tuple, Watch] = {}
        self._lock = threading.Lock()
        self.stale_s = 1.0

    def add(self, cid: int, bus: Optional[str] = None) -> Watch:
        with self._lock:
            w = self.watches.get((bus, cid))
            if w is None:
                w = self.watches[(bus, cid)] = Watch(bus, cid)
            return w

    def remove(self, cid: int, bus: Optional[str] = None) -> None:
        with self._lock:
            self.watches.pop((bus, cid), None)

    def clear(self) -> None:
        with self._lock:
            self.watches.clear()

    def reset_all(self) -> None:
        for w in list(self.watches.values()):
            w.reset_highlights()

    def on_frame(self, frame: CanFrame) -> None:
        if not self.watches:
            return
        for key in ((frame.bus, frame.arbitration_id), (None, frame.arbitration_id)):
            w = self.watches.get(key)
            if w is not None and frame.direction in w.directions:
                w.feed(frame)

    def is_stale(self, w: Watch) -> bool:
        age = w.age()
        if age is None:
            return False
        limit = self.stale_s
        if w.period_ms:
            limit = max(limit, 5 * w.period_ms / 1000.0)
        return age > limit
