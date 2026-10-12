"""DBC support: live decoding with cantools for the Live Signal Tracker, and
export of the built-in database to a .dbc file."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Optional

from q50tb.can.frame import CanFrame
from q50tb.core import timebase
from q50tb.signals.model import MessageDef, SignalDatabase, SignalDef

try:
    import cantools
except Exception:
    cantools = None


@dataclass
class LiveSignal:
    message: str
    name: str
    unit: str
    value: object = None
    raw_text: str = ""
    t: Optional[float] = None
    count: int = 0
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    choices: str = ""
    frame_id: int = 0
    direction: str = ""


class DbcLiveDecoder:
    """Decodes frames against a loaded DBC; one row per signal."""

    def __init__(self):
        self.db = None
        self.path = ""
        self.bus_filter: Optional[str] = None
        self.signals: dict[tuple[int, str], LiveSignal] = {}
        self._by_id: dict[int, object] = {}
        self._lock = threading.Lock()
        self.errors = 0

    def load(self, path: str) -> int:
        if cantools is None:
            raise RuntimeError("cantools is not installed")
        db = cantools.database.load_file(path, strict=False)
        sigs = {}
        by_id = {}
        for m in db.messages:
            by_id[m.frame_id] = m
            for s in m.signals:
                sigs[(m.frame_id, s.name)] = LiveSignal(m.name, s.name, s.unit or "", frame_id=m.frame_id)
        with self._lock:
            self.db, self.path, self.signals, self._by_id = db, path, sigs, by_id
        return len(sigs)

    def unload(self) -> None:
        with self._lock:
            self.db = None
            self.signals = {}
            self._by_id = {}

    def on_frame(self, frame: CanFrame) -> None:
        if self.db is None or (self.bus_filter and frame.bus != self.bus_filter):
            return
        m = self._by_id.get(frame.arbitration_id)
        if m is None:
            return
        try:
            values = m.decode(bytes(frame.data), decode_choices=True, allow_truncated=True)
        except Exception:
            self.errors += 1
            return
        t = frame.timestamp
        for name, v in values.items():
            ls = self.signals.get((frame.arbitration_id, name))
            if ls is None:
                continue
            ls.value = v
            ls.t = t
            ls.count += 1
            ls.direction = frame.direction
            if isinstance(v, (int, float)):
                ls.minimum = v if ls.minimum is None else min(ls.minimum, v)
                ls.maximum = v if ls.maximum is None else max(ls.maximum, v)

    def rows(self) -> list[LiveSignal]:
        return list(self.signals.values())


# ── export ────────────────────────────────────────────────────────────
def _motorola_or_intel(s: SignalDef) -> Optional[tuple[int, int, str]]:
    """(start_bit, length, '@0' Motorola | '@1' Intel) if the segments are contiguous."""
    segs = list(s.segments)
    contiguous_be = all(
        segs[i].lsb == 0 and segs[i + 1].msb == 7 and segs[i + 1].byte == segs[i].byte + 1
        for i in range(len(segs) - 1))
    if contiguous_be:
        return segs[0].byte * 8 + segs[0].msb, s.bit_length, "@0"
    rev = list(reversed(segs))
    contiguous_le = all(
        rev[i].msb == 7 and rev[i + 1].lsb == 0 and rev[i + 1].byte == rev[i].byte + 1
        for i in range(len(rev) - 1))
    if contiguous_le:
        return rev[0].byte * 8 + rev[0].lsb, s.bit_length, "@1"
    return None


def _ident(name: str) -> str:
    out = "".join(c if c.isalnum() or c == "_" else "_" for c in name)
    return out if out and not out[0].isdigit() else "_" + out


def export_dbc(db: SignalDatabase, path: str, bus: str = "main") -> tuple[int, list[str]]:
    lines = ['VERSION "q50tb export"', "", "NS_ :", "", "BS_:", "", "BU_: BENCH METER VEHICLE", ""]
    skipped: list[str] = []
    n = 0
    vals = []
    comments = []
    for m in db.by_bus(bus):
        dlc = m.dlc or max((s.min_dlc for s in m.signals), default=8)
        sender = "METER" if m.meter_origin else "VEHICLE"
        lines.append(f"BO_ {m.arbitration_id} {_ident(m.name)}: {dlc} {sender}")
        for s in m.signals:
            pos = _motorola_or_intel(s)
            if pos is None:
                skipped.append(f"{m.key}.{s.name} (non-contiguous)")
                continue
            start, length, order = pos
            sign = "-" if s.signed else "+"
            lo, hi = s.range()
            lines.append(f' SG_ {s.name} : {start}|{length}{order}{sign} ({s.factor:g},{s.offset:g}) '
                         f'[{lo:g}|{hi:g}] "{s.unit}" BENCH')
            n += 1
            if s.enum:
                vals.append(f"VAL_ {m.arbitration_id} {s.name} " +
                            " ".join(f'{k} "{v.replace(chr(34), chr(39))}"' for k, v in sorted(s.enum.items())) + " ;")
            note = f"[{s.confidence}] {s.notes}".strip()
            comments.append(f'CM_ SG_ {m.arbitration_id} {s.name} "{note.replace(chr(34), chr(39))}";')
        lines.append("")
    lines += comments + [""] + vals + [""]
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
    return n, skipped
