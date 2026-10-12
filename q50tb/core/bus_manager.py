"""Logical buses ("main", "av") on top of adapters.

Every transmit in the app goes through ``BusChannel.send``. That single
choke point enforces, in order:

1. monitor mode: the channel has no transmit path at all;
2. protected IDs: meter-originated IDs are never sent unless allowed;
3. TX ownership: one transmitter per CAN ID per bus (highest-priority claim wins).

Sent frames are looped back to subscribers marked ``direction="TX"`` so the
log, tracker and dashboard can show what the bench itself is driving.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Iterable, Optional

from q50tb.can.adapters.base import Adapter, AdapterError, AdapterInfo, BusConfig
from q50tb.can.discovery import create_adapter
from q50tb.can.frame import CanFrame, RX, TX
from q50tb.core import timebase

Subscriber = Callable[[CanFrame], None]

# Owner priorities. Higher wins an ID while its claim is active.
PRIO_BACKGROUND = 10
PRIO_AV = 20
PRIO_MANUAL = 25
PRIO_REPLAY = 30

MAIN = "main"
AV = "av"


class SendRefused(Exception):
    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


@dataclass
class Claim:
    owner: str
    priority: int


class TxOwnership:
    """Per-bus registry of which owner may transmit each ID."""

    def __init__(self):
        self._lock = threading.Lock()
        self._claims: dict[int, list[Claim]] = {}
        self.listeners: list[Callable[[], None]] = []

    def claim(self, owner: str, ids: Iterable[int], priority: int) -> None:
        with self._lock:
            for cid in ids:
                lst = self._claims.setdefault(cid, [])
                lst[:] = [c for c in lst if c.owner != owner]
                lst.append(Claim(owner, priority))
                lst.sort(key=lambda c: -c.priority)
        self._notify()

    def release(self, owner: str, ids: Optional[Iterable[int]] = None) -> None:
        with self._lock:
            targets = list(self._claims) if ids is None else list(ids)
            for cid in targets:
                lst = self._claims.get(cid)
                if not lst:
                    continue
                lst[:] = [c for c in lst if c.owner != owner]
                if not lst:
                    del self._claims[cid]
        self._notify()

    def owner_of(self, cid: int) -> Optional[Claim]:
        with self._lock:
            lst = self._claims.get(cid)
            return lst[0] if lst else None

    def may_send(self, owner: str, cid: int) -> bool:
        top = self.owner_of(cid)
        return top is None or top.owner == owner

    def snapshot(self) -> dict[int, list[tuple[str, int]]]:
        with self._lock:
            return {cid: [(c.owner, c.priority) for c in lst] for cid, lst in self._claims.items()}

    def _notify(self):
        for fn in list(self.listeners):
            try:
                fn()
            except Exception:
                pass


@dataclass
class ChannelStats:
    rx: int = 0
    tx: int = 0
    refused: int = 0
    suppressed: int = 0
    tx_errors: int = 0
    rx_fps: float = 0.0
    tx_fps: float = 0.0
    last_error: str = ""
    _rx_hist: deque = field(default_factory=lambda: deque(maxlen=64))
    _tx_hist: deque = field(default_factory=lambda: deque(maxlen=64))


class BusChannel:
    def __init__(self, role: str, adapter: Adapter, config: BusConfig, monitor_only: bool,
                 protected_ids: set[int], status_cb: Callable[[str, str], None]):
        self.role = role
        self.adapter = adapter
        self.config = config
        self.monitor_only = monitor_only
        self.protected_ids = set(protected_ids)
        self.allow_protected = False
        self.ownership = TxOwnership()
        self.stats = ChannelStats()
        self._subs: list[Subscriber] = []
        self._subs_lock = threading.Lock()
        self._status = status_cb
        self._last_fps = time.monotonic()
        self._rx_mark = 0
        self._tx_mark = 0

    @property
    def is_fd(self) -> bool:
        return self.config.fd

    @property
    def is_open(self) -> bool:
        return self.adapter.is_open

    def subscribe(self, fn: Subscriber) -> None:
        with self._subs_lock:
            if fn not in self._subs:
                self._subs = self._subs + [fn]

    def unsubscribe(self, fn: Subscriber) -> None:
        with self._subs_lock:
            self._subs = [s for s in self._subs if s is not fn]

    def can_transmit(self) -> bool:
        return not self.monitor_only and self.adapter.is_open

    def check_send(self, owner: str, frame: CanFrame) -> Optional[str]:
        """Return a refusal reason, or None if ``owner`` may send ``frame`` now."""
        if self.monitor_only:
            return "monitor-mode"
        if not self.adapter.is_open:
            return "bus-closed"
        if frame.arbitration_id in self.protected_ids and not self.allow_protected:
            return "protected-id"
        if frame.is_fd and not self.config.fd:
            return "fd-on-classic-bus"
        if not self.ownership.may_send(owner, frame.arbitration_id):
            return "owned-by-other"
        return None

    def send(self, frame: CanFrame, owner: str, raise_on_refuse: bool = False) -> bool:
        reason = self.check_send(owner, frame)
        if reason is None:
            try:
                frame.validate()
            except ValueError as exc:
                reason = f"invalid: {exc}"
        if reason is not None:
            if reason == "owned-by-other":
                self.stats.suppressed += 1
            else:
                self.stats.refused += 1
            if raise_on_refuse:
                raise SendRefused(reason, f"{self.role} 0x{frame.arbitration_id:X} by {owner}")
            return False
        try:
            self.adapter.send(frame)
        except AdapterError as exc:
            self.stats.tx_errors += 1
            self.stats.last_error = str(exc)
            if raise_on_refuse:
                raise SendRefused("adapter-error", str(exc)) from exc
            return False
        self.stats.tx += 1
        echo = frame.copy(timestamp=timebase.now(), bus=self.role, direction=TX, source=owner)
        self._dispatch(echo)
        return True

    # called on the adapter RX thread
    def _on_rx(self, frame: CanFrame) -> None:
        frame.bus = self.role
        frame.direction = RX
        self.stats.rx += 1
        self._dispatch(frame)

    def _dispatch(self, frame: CanFrame) -> None:
        for fn in self._subs:
            try:
                fn(frame)
            except Exception as exc:
                self._status("error", f"{self.role} subscriber {getattr(fn, '__qualname__', fn)}: {exc!r}")

    def update_rates(self) -> None:
        now = time.monotonic()
        dt = now - self._last_fps
        if dt <= 0:
            return
        self.stats.rx_fps = (self.stats.rx - self._rx_mark) / dt
        self.stats.tx_fps = (self.stats.tx - self._tx_mark) / dt
        self._rx_mark, self._tx_mark, self._last_fps = self.stats.rx, self.stats.tx, now


class BusManager:
    """Owns the open channels and fans frames out to global subscribers."""

    def __init__(self):
        self.channels: dict[str, BusChannel] = {}
        self.monitor_only = False
        self._global_subs: list[Subscriber] = []
        self._status_listeners: list[Callable[[str, str], None]] = []
        self._state_listeners: list[Callable[[], None]] = []
        self.status_log: deque = deque(maxlen=500)
        self._lock = threading.RLock()

    # ── listeners ─────────────────────────────────────────────────
    def add_status_listener(self, fn: Callable[[str, str], None]) -> None:
        self._status_listeners.append(fn)

    def add_state_listener(self, fn: Callable[[], None]) -> None:
        """Called after buses start or stop."""
        self._state_listeners.append(fn)

    def status(self, level: str, msg: str) -> None:
        self.status_log.append((timebase.now(), level, msg))
        for fn in list(self._status_listeners):
            try:
                fn(level, msg)
            except Exception:
                pass

    def subscribe_all(self, fn: Subscriber) -> None:
        with self._lock:
            if fn not in self._global_subs:
                self._global_subs.append(fn)
            for ch in self.channels.values():
                ch.subscribe(fn)

    def unsubscribe_all(self, fn: Subscriber) -> None:
        with self._lock:
            self._global_subs = [s for s in self._global_subs if s is not fn]
            for ch in self.channels.values():
                ch.unsubscribe(fn)

    # ── lifecycle ─────────────────────────────────────────────────
    @property
    def running(self) -> bool:
        return any(ch.is_open for ch in self.channels.values())

    def get(self, role: str) -> Optional[BusChannel]:
        ch = self.channels.get(role)
        return ch if ch and ch.is_open else None

    def start(self, assignments: dict[str, AdapterInfo], config: BusConfig, monitor_only: bool,
              protected: dict[str, set[int]], software_monitor_roles: Optional[set[str]] = None) -> None:
        """Open one adapter per role. All-or-nothing: on failure nothing stays open.

        In monitor mode every adapter is opened listen-only (hardware silent
        mode) and has no transmit path. ``software_monitor_roles`` names roles
        whose adapter cannot do silent mode and that the user explicitly
        allowed to monitor anyway: TX stays blocked, but the controller will
        still ACK frames on the wire.
        """
        soft = software_monitor_roles or set()
        with self._lock:
            if self.running:
                raise AdapterError("buses already running; stop first")
            keys = [i.key for i in assignments.values()]
            if len(keys) != len(set(keys)):
                raise AdapterError("each bus needs its own adapter channel")
            opened: list[BusChannel] = []
            try:
                for role, info in assignments.items():
                    cfg = BusConfig(bitrate=config.bitrate, fd=config.fd, data_bitrate=config.data_bitrate,
                                    listen_only=(monitor_only and role not in soft) or config.listen_only,
                                    configure_link=config.configure_link, extra=dict(config.extra))
                    adapter = create_adapter(info)
                    ch = BusChannel(role, adapter, cfg, monitor_only, protected.get(role, set()), self.status)
                    adapter.open(cfg, ch._on_rx, self.status)
                    opened.append(ch)
                    mode = "LISTEN-ONLY" if cfg.listen_only else (
                        "MONITOR (software TX block only - adapter still ACKs)" if monitor_only else "normal")
                    self.status("info", f"{role} bus open on {info.describe()} @ {cfg.bitrate} bps "
                                        f"{'FD ' if cfg.fd else ''}{mode}")
            except Exception:
                for ch in opened:
                    ch.adapter.close()
                raise
            self.monitor_only = monitor_only
            self.channels = {ch.role: ch for ch in opened}
            for ch in opened:
                for fn in self._global_subs:
                    ch.subscribe(fn)
        self._notify_state()

    def open_role(self, role: str, info: AdapterInfo, config: BusConfig, protected: set[int]) -> None:
        """Add one bus while others run (same monitor mode as the running set)."""
        with self._lock:
            if role in self.channels:
                raise AdapterError(f"{role} bus already open")
            if any(ch.adapter.info.key == info.key for ch in self.channels.values()):
                raise AdapterError(f"{info.key} is already used by another bus")
            monitor = self.monitor_only if self.channels else False
            cfg = BusConfig(bitrate=config.bitrate, fd=config.fd, data_bitrate=config.data_bitrate,
                            listen_only=monitor or config.listen_only, configure_link=config.configure_link)
            adapter = create_adapter(info)
            ch = BusChannel(role, adapter, cfg, monitor, protected, self.status)
            adapter.open(cfg, ch._on_rx, self.status)
            self.channels[role] = ch
            for fn in self._global_subs:
                ch.subscribe(fn)
            self.status("info", f"{role} bus open on {info.describe()} @ {cfg.bitrate} bps "
                                f"{'LISTEN-ONLY' if cfg.listen_only else 'normal'}")
        self._notify_state()

    def close_role(self, role: str) -> None:
        with self._lock:
            ch = self.channels.pop(role, None)
            if ch:
                ch.adapter.close()
                self.status("info", f"{role} bus closed")
        self._notify_state()

    def stop(self) -> None:
        with self._lock:
            chans, self.channels = list(self.channels.values()), {}
            for ch in chans:
                ch.adapter.close()
                self.status("info", f"{ch.role} bus closed")
        self._notify_state()

    def _notify_state(self) -> None:
        for fn in list(self._state_listeners):
            try:
                fn()
            except Exception as exc:
                self.status("error", f"state listener: {exc!r}")

    # ── convenience ───────────────────────────────────────────────
    def send(self, role: str, frame: CanFrame, owner: str, raise_on_refuse: bool = False) -> bool:
        ch = self.channels.get(role)
        if ch is None:
            if raise_on_refuse:
                raise SendRefused("bus-closed", role)
            return False
        frame.bus = role
        return ch.send(frame, owner, raise_on_refuse)

    def update_rates(self) -> None:
        for ch in self.channels.values():
            ch.update_rates()
