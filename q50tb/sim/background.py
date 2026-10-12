"""Background (default) messages: the periodic traffic a real car keeps on the
bus, with counters and checksums regenerated every frame.

Payload pipeline for each tick, in order:

    sequence[i] or base  ->  control overlays (signal fields only)
                         ->  per-byte overrides (user-edited bytes)
                         ->  counters  ->  checksums

So a user override never freezes a counter or breaks a checksum unless the
override itself is on that byte and "override counters" is chosen.
"""

from __future__ import annotations

import json
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

from q50tb.can.frame import CanFrame
from q50tb.core.bus_manager import PRIO_BACKGROUND, BusManager
from q50tb.core.scheduler import Job, Scheduler
from q50tb.signals.model import SignalDatabase

OWNER = "background"


@dataclass
class CounterRule:
    byte: int
    lsb: int = 0
    width: int = 4
    step: int = 1

    def apply(self, data: bytearray, state: int) -> None:
        mask = ((1 << self.width) - 1) << self.lsb
        data[self.byte] = (data[self.byte] & ~mask & 0xFF) | ((state << self.lsb) & mask)

    def read(self, data: bytes) -> int:
        return (data[self.byte] >> self.lsb) & ((1 << self.width) - 1)

    def to_json(self) -> dict:
        return {"byte": self.byte, "lsb": self.lsb, "width": self.width, "step": self.step}


@dataclass
class ChecksumRule:
    kind: str          # "sum8" | "xor8" | "xor_nibbles"
    byte: int
    over: list[int]
    add: int = 0

    def compute(self, data: bytes) -> int:
        if self.kind == "sum8":
            return (sum(data[i] for i in self.over) + self.add) & 0xFF
        if self.kind == "xor8":
            v = self.add
            for i in self.over:
                v ^= data[i]
            return v & 0xFF
        if self.kind == "xor_nibbles":
            v = self.add
            for i in self.over:
                v ^= (data[i] >> 4) ^ (data[i] & 0xF)
            return v & 0xF
        raise ValueError(f"unknown checksum kind {self.kind}")

    def apply(self, data: bytearray) -> None:
        if self.byte < len(data):
            data[self.byte] = self.compute(data)

    def to_json(self) -> dict:
        return {"kind": self.kind, "byte": self.byte, "over": self.over, "add": self.add}


@dataclass
class BackgroundMessage:
    arbitration_id: int
    name: str
    bus: str
    period_ms: float
    base: bytes
    enabled: bool = True
    is_extended: bool = False
    counters: list[CounterRule] = field(default_factory=list)
    checksums: list[ChecksumRule] = field(default_factory=list)
    sequence: list[bytes] = field(default_factory=list)
    notes: str = ""
    # runtime ----------------------------------------------------------
    overrides: list[Optional[int]] = field(default_factory=list)
    override_counters: bool = False
    overlays: dict[str, int] = field(default_factory=dict)     # signal name -> raw
    tx_count: int = 0
    suppressed_count: int = 0
    last_payload: bytes = b""
    status: str = ""
    _counter_state: list[int] = field(default_factory=list, repr=False)
    _seq_index: int = field(default=0, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def __post_init__(self):
        if not self.overrides:
            self.overrides = [None] * len(self.base)
        self._counter_state = [c.read(self.base) if c.byte < len(self.base) else 0 for c in self.counters]

    @property
    def dlc(self) -> int:
        return len(self.base)

    @property
    def key(self) -> tuple[str, int]:
        return (self.bus, self.arbitration_id)

    def set_override(self, index: int, value: Optional[int]) -> None:
        with self._lock:
            if index >= len(self.overrides):
                self.overrides.extend([None] * (index + 1 - len(self.overrides)))
            self.overrides[index] = None if value is None else value & 0xFF

    def clear_overrides(self) -> None:
        with self._lock:
            self.overrides = [None] * len(self.base)

    @property
    def has_override(self) -> bool:
        return any(v is not None for v in self.overrides)

    def protected_bytes(self) -> set[int]:
        out = {c.byte for c in self.counters} | {c.byte for c in self.checksums}
        return out

    def next_payload(self, db: Optional[SignalDatabase]) -> bytes:
        with self._lock:
            if self.sequence:
                data = bytearray(self.sequence[self._seq_index % len(self.sequence)])
                self._seq_index += 1
            else:
                data = bytearray(self.base)
            if self.overlays and db is not None:
                mdef = db.get(self.bus, self.arbitration_id)
                if mdef is not None:
                    for name, raw in self.overlays.items():
                        try:
                            mdef.signal(name).insert_raw(data, raw)
                        except KeyError:
                            continue
            protected = set() if self.override_counters else self.protected_bytes()
            for i, v in enumerate(self.overrides):
                if v is not None and i < len(data) and i not in protected:
                    data[i] = v
            for k, rule in enumerate(self.counters):
                if rule.byte >= len(data):
                    continue
                if self.override_counters and self.overrides[rule.byte] is not None:
                    continue
                self._counter_state[k] = (self._counter_state[k] + rule.step) & ((1 << rule.width) - 1)
                rule.apply(data, self._counter_state[k])
            for rule in self.checksums:
                if not (self.override_counters and rule.byte < len(self.overrides)
                        and self.overrides[rule.byte] is not None):
                    rule.apply(data)
            out = bytes(data)
            self.last_payload = out
            return out

    def to_json(self) -> dict:
        d = {"id": f"0x{self.arbitration_id:03X}", "name": self.name, "bus": self.bus,
             "period_ms": self.period_ms, "payload": self.base.hex(" ").upper(), "enabled": self.enabled}
        if self.is_extended:
            d["extended"] = True
        if self.counters:
            d["counters"] = [c.to_json() for c in self.counters]
        if self.checksums:
            d["checksums"] = [c.to_json() for c in self.checksums]
        if self.sequence:
            d["sequence"] = [s.hex() for s in self.sequence]
        if self.notes:
            d["notes"] = self.notes
        if self.has_override:
            d["overrides"] = [None if v is None else f"{v:02X}" for v in self.overrides]
        return d

    @classmethod
    def from_json(cls, d: dict, default_bus: str = "main") -> "BackgroundMessage":
        msg = cls(
            arbitration_id=int(str(d["id"]), 16) if isinstance(d["id"], str) else int(d["id"]),
            name=d.get("name", ""), bus=d.get("bus", default_bus), period_ms=float(d.get("period_ms", 100)),
            base=bytes.fromhex(d.get("payload", "")), enabled=bool(d.get("enabled", True)),
            is_extended=bool(d.get("extended", False)),
            counters=[CounterRule(**c) for c in d.get("counters", [])],
            checksums=[ChecksumRule(**c) for c in d.get("checksums", [])],
            sequence=[bytes.fromhex(s) for s in d.get("sequence", [])],
            notes=d.get("notes", ""))
        ov = d.get("overrides")
        if ov:
            msg.overrides = [None if v is None else int(v, 16) for v in ov] + [None] * max(0, msg.dlc - len(ov))
        return msg


class BackgroundManager:
    """Schedules every enabled background message on its bus."""

    def __init__(self, buses: BusManager, scheduler: Scheduler, db: SignalDatabase):
        self.buses = buses
        self.scheduler = scheduler
        self.db = db
        self.messages: dict[tuple[str, int], BackgroundMessage] = {}
        self.profile_name = ""
        self.profile_path = ""
        self._jobs: dict[tuple[str, int], Job] = {}
        self._lock = threading.RLock()
        self.paused = False
        self.version = 0          # bumps on every signal/profile change (UI resync)
        self.listeners: list[Callable[[], None]] = []
        buses.add_state_listener(self._on_bus_state)

    # ── profile I/O ─────────────────────────────────────────────────
    def load_profile(self, path: str, replace: bool = True) -> int:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        msgs = [BackgroundMessage.from_json(d, data.get("bus", "main")) for d in data.get("messages", [])]
        with self._lock:
            if replace:
                self._stop_all()
                self.messages = {}
            for m in msgs:
                self.messages[m.key] = m
            self.profile_name = data.get("name", path)
            self.profile_path = path
        self._sync()
        self._changed()
        return len(msgs)

    def save_profile(self, path: str, name: Optional[str] = None) -> None:
        with self._lock:
            data = {"name": name or self.profile_name or "profile", "format": "q50tb-background-1",
                    "messages": [m.to_json() for m in sorted(self.messages.values(), key=lambda m: (m.bus, m.arbitration_id))]}
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh, indent=1)

    # ── edits ───────────────────────────────────────────────────────
    def get(self, bus: str, cid: int) -> Optional[BackgroundMessage]:
        return self.messages.get((bus, cid))

    def ensure(self, bus: str, cid: int) -> BackgroundMessage:
        """Return the background message, creating it from the database if missing."""
        with self._lock:
            msg = self.messages.get((bus, cid))
            if msg is None:
                mdef = self.db.get(bus, cid)
                dlc = (mdef.dlc if mdef and mdef.dlc else 8)
                period = (mdef.period_ms if mdef and mdef.period_ms else 100)
                msg = BackgroundMessage(cid, mdef.name if mdef else f"0x{cid:03X}", bus, period, bytes(dlc),
                                        notes="created from signal database (no captured template)")
                self.messages[(bus, cid)] = msg
                self._sync()
                self._changed()
            return msg

    def add(self, msg: BackgroundMessage) -> None:
        with self._lock:
            self.messages[msg.key] = msg
        self._sync()
        self._changed()

    def remove(self, bus: str, cid: int) -> None:
        with self._lock:
            self.messages.pop((bus, cid), None)
        self._sync()
        self._changed()

    def set_enabled(self, bus: str, cid: int, on: bool) -> None:
        msg = self.messages.get((bus, cid))
        if msg:
            msg.enabled = on
            self._sync()
            self._changed()

    def set_all_enabled(self, on: bool, bus: Optional[str] = None) -> None:
        for m in self.messages.values():
            if bus is None or m.bus == bus:
                m.enabled = on
        self._sync()
        self._changed()

    def set_period(self, bus: str, cid: int, period_ms: float) -> None:
        msg = self.messages.get((bus, cid))
        if not msg:
            return
        msg.period_ms = max(1.0, period_ms)
        job = self._jobs.get(msg.key)
        if job:
            job.set_period(msg.period_ms / 1000.0)
        self._changed()

    def set_signal(self, bus: str, cid: int, signal: str, value: float) -> int:
        """Set a decoded signal on a background message; returns the raw value."""
        mdef = self.db.get(bus, cid)
        if mdef is None:
            raise KeyError(f"no signal definition for {bus} 0x{cid:03X}")
        sdef = mdef.signal(signal)
        raw = sdef.phys_to_raw(value)
        msg = self.ensure(bus, cid)
        with msg._lock:
            msg.overlays[signal] = raw
        self.version += 1
        return raw

    def set_signal_raw(self, bus: str, cid: int, signal: str, raw: int) -> None:
        mdef = self.db.get(bus, cid)
        if mdef is None:
            raise KeyError(f"no signal definition for {bus} 0x{cid:03X}")
        mdef.signal(signal)
        msg = self.ensure(bus, cid)
        with msg._lock:
            msg.overlays[signal] = int(raw)
        self.version += 1

    def clear_signal(self, bus: str, cid: int, signal: Optional[str] = None) -> None:
        msg = self.messages.get((bus, cid))
        if msg:
            with msg._lock:
                if signal is None:
                    msg.overlays.clear()
                else:
                    msg.overlays.pop(signal, None)
            self.version += 1

    def signal_value(self, bus: str, cid: int, signal: str) -> Optional[float]:
        """Current commanded value (overlay, else base payload)."""
        msg = self.messages.get((bus, cid))
        mdef = self.db.get(bus, cid)
        if mdef is None:
            return None
        sdef = mdef.signal(signal)
        if msg is None:
            return None
        if signal in msg.overlays:
            raw = msg.overlays[signal]
            return sdef.raw_to_phys(raw)
        return sdef.decode(msg.last_payload or msg.base)

    def set_paused(self, paused: bool) -> None:
        self.paused = paused
        self._changed()

    # ── scheduling ──────────────────────────────────────────────────
    def _on_bus_state(self) -> None:
        self._sync()

    def _stop_all(self) -> None:
        for job in self._jobs.values():
            job.cancel()
        self._jobs.clear()
        for ch in self.buses.channels.values():
            ch.ownership.release(OWNER)

    def _sync(self) -> None:
        """Start/stop jobs and ownership claims to match enabled state and open buses."""
        with self._lock:
            wanted = {}
            for key, m in self.messages.items():
                ch = self.buses.get(m.bus)
                if m.enabled and ch is not None and ch.can_transmit():
                    wanted[key] = m
            for key in list(self._jobs):
                if key not in wanted:
                    self._jobs.pop(key).cancel()
            by_bus: dict[str, list[int]] = {}
            for key, m in wanted.items():
                by_bus.setdefault(m.bus, []).append(m.arbitration_id)
                if key not in self._jobs:
                    phase = (hash(key) % 997) / 997.0 * min(m.period_ms, 50) / 1000.0
                    self._jobs[key] = self.scheduler.every(m.period_ms / 1000.0, self._tick_factory(m),
                                                           name=f"bg {m.bus} {m.arbitration_id:03X}", phase=phase)
            for role, ch in self.buses.channels.items():
                ch.ownership.release(OWNER)
                if by_bus.get(role):
                    ch.ownership.claim(OWNER, by_bus[role], PRIO_BACKGROUND)

    def _tick_factory(self, msg: BackgroundMessage):
        def tick():
            if self.paused or not msg.enabled:
                return
            ch = self.buses.channels.get(msg.bus)
            if ch is None:
                return
            if not ch.ownership.may_send(OWNER, msg.arbitration_id):
                msg.suppressed_count += 1
                top = ch.ownership.owner_of(msg.arbitration_id)
                msg.status = f"suppressed by {top.owner}" if top else ""
                return
            payload = msg.next_payload(self.db)
            frame = CanFrame(msg.arbitration_id, payload, is_extended=msg.is_extended, bus=msg.bus)
            if ch.send(frame, OWNER):
                msg.tx_count += 1
                msg.status = ""
            else:
                msg.status = "send refused"
        return tick

    def _changed(self) -> None:
        self.version += 1
        for fn in list(self.listeners):
            try:
                fn()
            except Exception:
                pass
