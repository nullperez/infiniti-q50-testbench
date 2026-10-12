"""Signal definitions in the same notation as the owner's CAN README.

A signal is a list of bit segments ``(byte, msb, lsb)`` concatenated
most-significant first. ``Bytes 0-1`` big-endian is ``[(0,7,0),(1,7,0)]``;
``Byte 1 + Byte 2 [7:6]`` is ``[(1,7,0),(2,7,6)]``; a little-endian 16-bit
field is ``[(1,7,0),(0,7,0)]``. Encoding only touches the segment bits, so
unknown bits keep whatever the base payload had.

Confidence words keep the project meaning: confirmed / documented /
probable / candidate. They are shown in the UI next to every control.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

CONFIRMED = "confirmed"
DOCUMENTED = "documented"
PROBABLE = "probable"
CANDIDATE = "candidate"


@dataclass(frozen=True)
class Seg:
    byte: int
    msb: int = 7
    lsb: int = 0

    @property
    def width(self) -> int:
        return self.msb - self.lsb + 1


def seg(byte: int, msb: int = 7, lsb: Optional[int] = None) -> Seg:
    return Seg(byte, msb, msb if lsb is None else lsb)


def be16(first: int) -> tuple[Seg, Seg]:
    return (Seg(first), Seg(first + 1))


def le16(first: int) -> tuple[Seg, Seg]:
    return (Seg(first + 1), Seg(first))


@dataclass
class SignalDef:
    name: str
    segments: tuple[Seg, ...]
    factor: float = 1.0
    offset: float = 0.0
    signed: bool = False
    unit: str = ""
    enum: Optional[dict[int, str]] = None
    minimum: Optional[float] = None
    maximum: Optional[float] = None
    confidence: str = DOCUMENTED
    notes: str = ""
    label: str = ""
    control: Optional[str] = None   # "slider", "spin", "check", "combo", "readonly"
    message: Optional["MessageDef"] = field(default=None, repr=False, compare=False)

    def __post_init__(self):
        self.segments = tuple(self.segments)
        if not self.label:
            self.label = self.name.replace("_", " ").title()

    # ── bit access ───────────────────────────────────────────────
    @property
    def bit_length(self) -> int:
        return sum(s.width for s in self.segments)

    @property
    def min_dlc(self) -> int:
        return max(s.byte for s in self.segments) + 1

    @property
    def raw_max(self) -> int:
        return (1 << self.bit_length) - 1

    def byte_masks(self) -> dict[int, int]:
        out: dict[int, int] = {}
        for s in self.segments:
            out[s.byte] = out.get(s.byte, 0) | (((1 << s.width) - 1) << s.lsb)
        return out

    def extract_raw(self, data: bytes) -> Optional[int]:
        if len(data) < self.min_dlc:
            return None
        raw = 0
        for s in self.segments:
            raw = (raw << s.width) | ((data[s.byte] >> s.lsb) & ((1 << s.width) - 1))
        if self.signed and raw & (1 << (self.bit_length - 1)):
            raw -= 1 << self.bit_length
        return raw

    def insert_raw(self, data: bytearray, raw: int) -> None:
        n = self.bit_length
        raw &= (1 << n) - 1
        shift = n
        for s in self.segments:
            shift -= s.width
            part = (raw >> shift) & ((1 << s.width) - 1)
            mask = ((1 << s.width) - 1) << s.lsb
            if s.byte >= len(data):
                data.extend(b"\x00" * (s.byte + 1 - len(data)))
            data[s.byte] = (data[s.byte] & ~mask & 0xFF) | (part << s.lsb)

    # ── physical conversion ──────────────────────────────────────
    def raw_to_phys(self, raw: int) -> float:
        return raw * self.factor + self.offset

    def phys_to_raw(self, value: float) -> int:
        raw = int(round((value - self.offset) / self.factor)) if self.factor else int(value)
        lo = -(1 << (self.bit_length - 1)) if self.signed else 0
        hi = (1 << (self.bit_length - 1)) - 1 if self.signed else self.raw_max
        return max(lo, min(hi, raw))

    def decode(self, data: bytes):
        raw = self.extract_raw(data)
        if raw is None:
            return None
        return self.raw_to_phys(raw)

    def text(self, data: bytes) -> str:
        raw = self.extract_raw(data)
        if raw is None:
            return "—"
        if self.enum is not None:
            return self.enum.get(raw, f"0x{raw:X} (unlisted)")
        if self.bit_length == 1 and self.factor == 1 and self.offset == 0:
            return "1" if raw else "0"
        val = self.raw_to_phys(raw)
        if float(self.factor).is_integer() and float(self.offset).is_integer():
            return f"{int(val)}{(' ' + self.unit) if self.unit else ''}"
        return f"{val:.2f}{(' ' + self.unit) if self.unit else ''}"

    def range(self) -> tuple[float, float]:
        if self.minimum is not None and self.maximum is not None:
            return self.minimum, self.maximum
        lo = -(1 << (self.bit_length - 1)) if self.signed else 0
        hi = (1 << (self.bit_length - 1)) - 1 if self.signed else self.raw_max
        a, b = self.raw_to_phys(lo), self.raw_to_phys(hi)
        return (min(a, b), max(a, b))

    @property
    def full_name(self) -> str:
        if self.message is None:
            return self.name
        return f"{self.message.key}.{self.name}"

    def position_text(self) -> str:
        parts = []
        for s in self.segments:
            parts.append(f"B{s.byte}" if s.width == 8 else f"B{s.byte}[{s.msb}:{s.lsb}]" if s.width > 1
                         else f"B{s.byte}[{s.msb}]")
        return " + ".join(parts)


@dataclass
class MessageDef:
    arbitration_id: int
    name: str
    bus: str = "main"
    dlc: Optional[int] = 8
    period_ms: Optional[float] = None
    sender: str = "vehicle"          # "meter" = meter-originated (never transmitted)
    signals: list[SignalDef] = field(default_factory=list)
    notes: str = ""

    def __post_init__(self):
        for s in self.signals:
            s.message = self

    @property
    def key(self) -> str:
        return f"{self.bus}.{self.arbitration_id:03X}"

    @property
    def meter_origin(self) -> bool:
        return self.sender == "meter"

    def signal(self, name: str) -> SignalDef:
        for s in self.signals:
            if s.name == name:
                return s
        raise KeyError(f"{self.key} has no signal {name!r}")

    def decode(self, data: bytes) -> dict[str, Optional[float]]:
        return {s.name: s.decode(data) for s in self.signals}


class SignalDatabase:
    def __init__(self, messages: list[MessageDef], links: Optional[dict[str, tuple[str, ...]]] = None):
        self.messages: dict[tuple[str, int], MessageDef] = {}
        for m in messages:
            self.add(m)
        self.links: dict[tuple[str, int, str], list[SignalDef]] = {}
        for src, dsts in (links or {}).items():
            s = self.signal(src)
            self.links[(s.message.bus, s.message.arbitration_id, s.name)] = [self.signal(d) for d in dsts]

    def linked(self, bus: str, cid: int, name: str) -> list[SignalDef]:
        """Other signals that mirror this one (see LINKED_SIGNALS)."""
        return self.links.get((bus, cid, name), [])

    def add(self, m: MessageDef) -> None:
        self.messages[(m.bus, m.arbitration_id)] = m

    def get(self, bus: str, cid: int) -> Optional[MessageDef]:
        return self.messages.get((bus, cid))

    def by_bus(self, bus: str) -> list[MessageDef]:
        return sorted((m for (b, _), m in self.messages.items() if b == bus), key=lambda m: m.arbitration_id)

    def signal(self, path: str) -> SignalDef:
        """'main.180.ENGINE_RPM' or '180.ENGINE_RPM' (main assumed)."""
        parts = path.split(".")
        if len(parts) == 2:
            bus, (cid, name) = "main", parts
        elif len(parts) == 3:
            bus, cid, name = parts
        else:
            raise KeyError(path)
        msg = self.get(bus, int(cid, 16))
        if msg is None:
            raise KeyError(f"unknown message {bus}.{cid}")
        return msg.signal(name)

    def all_signals(self):
        for m in self.messages.values():
            yield from m.signals
