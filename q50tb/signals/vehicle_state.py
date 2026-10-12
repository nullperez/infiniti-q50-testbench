"""Latest frame per (bus, ID, direction) and on-demand decoding for the
dashboard. Decoding happens when the UI asks (10-20 Hz), not per frame.

Staleness: a signal is stale when its message has not been seen for
max(3 x period, ``min_stale_s``). Missing data is ``None`` - never zero.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Iterable, Optional

from q50tb.can.frame import CanFrame
from q50tb.core import timebase
from q50tb.signals.model import SignalDatabase


@dataclass
class SignalReading:
    value: Optional[float]
    raw: Optional[int]
    text: str
    age: Optional[float]
    stale: bool
    direction: str = ""

    @property
    def valid(self) -> bool:
        return self.value is not None and not self.stale


MISSING = SignalReading(None, None, "—", None, True)


class VehicleState:
    def __init__(self, db: SignalDatabase):
        self.db = db
        self._latest: dict[tuple[str, int, str], tuple[float, bytes]] = {}
        self._lock = threading.Lock()
        self.directions: tuple[str, ...] = ("RX", "TX")
        self.min_stale_s = 0.5

    def on_frame(self, frame: CanFrame) -> None:
        if frame.is_error:
            return
        self._latest[(frame.bus, frame.arbitration_id, frame.direction)] = (frame.timestamp, bytes(frame.data))

    def clear(self) -> None:
        self._latest.clear()

    def latest(self, bus: str, cid: int, directions: Optional[Iterable[str]] = None
               ) -> Optional[tuple[float, bytes, str]]:
        best = None
        for d in directions or self.directions:
            v = self._latest.get((bus, cid, d))
            if v and (best is None or v[0] > best[0]):
                best = (v[0], v[1], d)
        return best

    def read(self, path: str, directions: Optional[Iterable[str]] = None) -> SignalReading:
        try:
            sdef = self.db.signal(path)
        except KeyError:
            return MISSING
        msg = sdef.message
        hit = self.latest(msg.bus, msg.arbitration_id, directions)
        if hit is None:
            return MISSING
        t, data, d = hit
        age = timebase.now() - t
        period = (msg.period_ms or 100) / 1000.0
        stale = age > max(3 * period, self.min_stale_s)
        raw = sdef.extract_raw(data)
        if raw is None:
            return SignalReading(None, None, "short frame", age, True, d)
        return SignalReading(sdef.raw_to_phys(raw), raw, sdef.text(data), age, stale, d)

    def snapshot(self, directions: Optional[Iterable[str]] = None) -> dict[str, dict]:
        out = {}
        for msg in self.db.messages.values():
            hit = self.latest(msg.bus, msg.arbitration_id, directions)
            if hit is None:
                continue
            for s in msg.signals:
                r = self.read(s.full_name, directions)
                out[s.full_name] = {"value": r.value, "text": r.text, "raw": r.raw,
                                    "age_s": None if r.age is None else round(r.age, 3), "stale": r.stale,
                                    "dir": r.direction}
        return out
