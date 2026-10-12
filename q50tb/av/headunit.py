"""Head-unit (HU) emulator for the AV bus.

The emulator plays the HU side of each channel pair; the meter (or another
peer) answers on the paired higher ID. Channel life cycle:

    stopped -> waiting   HU heartbeats running, no announce seen yet
            -> announced peer sent ``00 20 <caps>``; we ACK and answer ``00 21``
            -> negotiating peer sent ``00 2C <caps>``; we answer ``00 2C``
            -> ready     post-open chain sent (clock / audio / nav / settings reads)
            -> lost      no peer frame for ``peer_timeout`` (re-announce re-opens)

If the peer re-announces while ready (meter rebooted) the channel re-opens
from scratch. Peer SET records (``G I0 v``) are echoed ``G IC 00 v`` as the
real HU does. Everything is logged through the sniffer and the event log.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

from q50tb.av.protocol import CHANNELS, OP_GET, OP_RESP, OP_SET, AvMessage
from q50tb.av.services.audio import AudioService
from q50tb.av.services.clock import ClockService
from q50tb.av.services.navigation import NavigationService
from q50tb.av.services.settings import SettingsService
from q50tb.av.sniffer import AvSniffer
from q50tb.av.transport import HB_HU, AvLink, LinkConfig, RxRecord
from q50tb.can.frame import CanFrame, RX
from q50tb.core import timebase
from q50tb.core.bus_manager import AV, PRIO_AV, BusManager
from q50tb.core.scheduler import Job, Scheduler

OWNER = "av-hu"

STOPPED, WAITING, ANNOUNCED, NEGOTIATING, READY, LOST = (
    "stopped", "waiting", "announced", "negotiating", "ready", "lost")

METER_CHANNELS = ("status", "settings", "nav")


@dataclass
class ChannelConfig:
    name: str
    hu_id: int
    peer_id: int
    description: str
    enabled: bool = True
    heartbeat: bool = True
    has_peer: bool = True             # a node answers on peer_id (meter channels on the bench)
    open_suffix: bytes = b""          # 628 answers "00 21 E0" / "00 2C E0"
    answer_delay: float = 0.05        # real HUs answered within 50-130 ms
    auto_echo_sets: bool = True
    post_open: list[bytes] = field(default_factory=list)


@dataclass
class ChannelRuntime:
    config: ChannelConfig
    state: str = STOPPED
    link: Optional[AvLink] = None
    peer_caps: bytes = b""
    opened_at: Optional[float] = None
    last_change: float = 0.0
    announces: int = 0


def default_channels() -> list[ChannelConfig]:
    """Bench defaults: the three meter channels with handshake, plus 0x61D as a
    send-only channel (no peer on the bench; the meter listens to source
    selection there). The other car-peer channels are off; enable them and
    ``has_peer`` when the real TCU/display is on the bus."""
    out = []
    for hu, peer, name, desc in CHANNELS:
        meter = name in METER_CHANNELS
        cfg = ChannelConfig(name, hu, peer, desc, enabled=meter or name == "source", has_peer=meter,
                            heartbeat=name in ("status", "settings", "nav"))
        if name == "settings":
            cfg.open_suffix = b"\xE0"
        if name == "capability":
            cfg.post_open = [bytes.fromhex(x) for x in ("266001", "2981", "2A20000000", "0201", "0221", "2941")]
        out.append(cfg)
    return out


CAR_PEER_POST_OPEN = {"source": [bytes.fromhex("C24001")]}


class HeadUnitEmulator:
    def __init__(self, buses: BusManager, scheduler: Scheduler, sniffer: AvSniffer):
        self.buses = buses
        self.scheduler = scheduler
        self.sniffer = sniffer
        self.bus_role = AV
        self.channels: dict[str, ChannelRuntime] = {c.name: ChannelRuntime(c) for c in default_channels()}
        self.link_config = LinkConfig()
        self.running = False
        self.peer_timeout = 3.0
        self.settings_read_delay = 1.0
        self.auto_read_settings = True
        self.heartbeat_period = 0.5
        self._hb_job: Optional[Job] = None
        self._watch_job: Optional[Job] = None
        self._lock = threading.RLock()
        self.listeners: list[Callable[[], None]] = []
        self.settings = SettingsService(self)
        self.audio = AudioService(self)
        self.nav = NavigationService(self)
        self.clock = ClockService(self)
        self.record_hooks: list[Callable[[str, AvMessage, RxRecord], None]] = []

    # ── host interface used by services ─────────────────────────────
    def channel_enabled(self, name: str) -> bool:
        rt = self.channels.get(name)
        return bool(rt and rt.config.enabled and rt.link is not None)

    def channel_ready(self, name: str) -> bool:
        rt = self.channels.get(name)
        return bool(rt and rt.state == READY)

    def channel_has_peer(self, name: str) -> bool:
        rt = self.channels.get(name)
        return bool(rt and rt.config.has_peer)

    def send(self, channel: str, record: bytes, label: str = "", expect_ack: bool = True,
             on_done: Optional[Callable[[bool, str], None]] = None, force: bool = False) -> bool:
        rt = self.channels.get(channel)
        if rt is None:
            raise KeyError(f"unknown AV channel {channel!r}")
        if not self.running or rt.link is None:
            self._event(rt, f"not sent ({label or record.hex(' ')}): HU emulation not running / channel disabled")
            if on_done:
                on_done(False, "HU not running or channel disabled")
            return False
        if rt.config.has_peer and rt.state != READY and not force:
            self._event(rt, f"not sent ({label}): channel {rt.state}, waiting for the meter to open it")
            if on_done:
                on_done(False, f"channel {rt.state}")
            return False
        rt.link.send_record(record, label=label, expect_ack=expect_ack and rt.config.has_peer, on_done=on_done)
        return True

    # ── lifecycle ───────────────────────────────────────────────────
    def start(self, bus_role: Optional[str] = None) -> None:
        with self._lock:
            if self.running:
                return
            role = bus_role or self.bus_role
            ch = self.buses.get(role)
            if ch is None:
                raise RuntimeError(f"AV bus '{role}' is not open")
            if not ch.can_transmit():
                raise RuntimeError("bus is in MONITOR mode; HU emulation transmits and is disabled")
            self.bus_role = role
            self.sniffer.bus_role = role
            ids = []
            for rt in self.channels.values():
                rt.state = STOPPED
                rt.link = None
                if not rt.config.enabled:
                    continue
                lc = self.link_config
                if not rt.config.has_peer:
                    lc = LinkConfig(**{**vars(lc), "blind_without_fc": True, "fc_timeout": 0.05})
                rt.link = AvLink(rt.config.hu_id, rt.config.peer_id, self._send_frame, self.scheduler,
                                 lc, on_record=lambda rec, r=rt: self._on_record(r, rec),
                                 on_event=lambda ev, r=rt: self._on_link_event(r, ev))
                rt.state = WAITING if rt.config.has_peer else READY
                rt.last_change = timebase.now()
                ids.append(rt.config.hu_id)
            ch.ownership.claim(OWNER, ids, PRIO_AV)
            ch.subscribe(self._on_frame)
            self.running = True
            self._hb_job = self.scheduler.every(self.heartbeat_period, self._heartbeat, name="av-hb")
            self._watch_job = self.scheduler.every(0.5, self._watchdog, name="av-watch")
            self.clock.start()
            for rt in self.channels.values():
                if rt.link and not rt.config.has_peer and rt.config.post_open:
                    self._run_post_open(rt)
        self.sniffer.add_event(timebase.now(), 0x628, f"HU emulation started on '{role}' bus")
        self._changed()

    def stop(self) -> None:
        with self._lock:
            if not self.running:
                return
            self.running = False
            for job in (self._hb_job, self._watch_job):
                if job:
                    job.cancel()
            self.clock.stop()
            self.nav.stop()
            ch = self.buses.channels.get(self.bus_role)
            if ch:
                ch.unsubscribe(self._on_frame)
                ch.ownership.release(OWNER)
            for rt in self.channels.values():
                if rt.link:
                    rt.link.reset()
                rt.link = None
                rt.state = STOPPED
        self.sniffer.add_event(timebase.now(), 0x628, "HU emulation stopped")
        self._changed()

    def set_channel_enabled(self, name: str, enabled: bool) -> None:
        if self.running:
            raise RuntimeError("stop the HU emulation before changing channels")
        self.channels[name].config.enabled = enabled
        self._changed()

    def force_open(self, name: str) -> None:
        """Debug: send the open answer without waiting for an announce."""
        rt = self.channels[name]
        if rt.link is None:
            return
        rt.link.reset()
        self._set_state(rt, ANNOUNCED)
        rt.link.send_record(b"\x00\x21" + rt.config.open_suffix, label="open answer (forced)")

    def reset_channel(self, name: str) -> None:
        rt = self.channels[name]
        if rt.link:
            rt.link.reset()
        self._set_state(rt, WAITING if rt.config.has_peer else READY)

    # ── frame / record handling ─────────────────────────────────────
    def _send_frame(self, can_id: int, data: bytes) -> bool:
        return self.buses.send(self.bus_role, CanFrame(can_id, data), OWNER)

    def _on_frame(self, frame: CanFrame) -> None:
        if not self.running or frame.direction != RX or frame.is_extended:
            return
        for rt in self.channels.values():
            if rt.link is not None and frame.arbitration_id == rt.config.peer_id:
                rt.link.on_frame(bytes(frame.data), frame.timestamp)
                if rt.state == LOST:
                    self._set_state(rt, WAITING)
                return

    def _on_record(self, rt: ChannelRuntime, rec: RxRecord) -> None:
        msg = AvMessage.parse(rec.data)
        if msg.group == 0x00 and msg.item == 2:
            self._handshake(rt, msg)
            return
        if rt.config.auto_echo_sets and msg.op == OP_SET:
            rt.link.send_record(bytes([msg.group, (msg.item << 4) | OP_RESP, 0x00]) + msg.payload,
                                label="echo peer SET", expect_ack=rt.config.has_peer)
        if rt.config.name == "settings":
            self.settings.on_record(msg)
        for hook in list(self.record_hooks):
            try:
                hook(rt.config.name, msg, rec)
            except Exception:
                pass
        if msg.op == OP_GET:
            self._event(rt, f"peer GET {msg} (no automatic answer)")

    def _handshake(self, rt: ChannelRuntime, msg: AvMessage) -> None:
        cfg = rt.config
        if msg.op == OP_SET:     # announce 00 20 <caps>
            rt.announces += 1
            rt.peer_caps = msg.payload
            if rt.state in (READY, NEGOTIATING):
                self._event(rt, "peer re-announced (meter rebooted?) - re-opening channel")
            # tokens restart for a new session; the announce itself was already ACKed
            rt.link.reset()
            self._set_state(rt, ANNOUNCED)
            self.scheduler.after(cfg.answer_delay, lambda: rt.link and rt.link.send_record(
                b"\x00\x21" + cfg.open_suffix, label="open answer"))
        elif msg.op == OP_RESP:  # 00 2C <caps>
            self._set_state(rt, NEGOTIATING)

            def opened(ok: bool, reason: str):
                if ok:
                    rt.opened_at = timebase.now()
                    self._set_state(rt, READY)
                    self._run_post_open(rt)
                else:
                    self._event(rt, f"open confirm not acknowledged: {reason}")
            rt.link.send_record(b"\x00\x2C" + cfg.open_suffix, label="open confirm", on_done=opened)

    def _run_post_open(self, rt: ChannelRuntime) -> None:
        name = rt.config.name
        for rec in rt.config.post_open:
            rt.link.send_record(rec, label="post-open", expect_ack=rt.config.has_peer)
        if name == "status":
            self.clock.send_now()
        elif name == "settings":
            self.audio.push()
            if self.auto_read_settings:
                self.scheduler.after(self.settings_read_delay, self.settings.read_all, name="settings-read")
        elif name == "nav":
            self.nav.push()

    # ── periodic ───────────────────────────────────────────────────
    def _heartbeat(self) -> None:
        if not self.running:
            return
        for rt in self.channels.values():
            if rt.link is not None and rt.config.heartbeat:
                rt.link.send_heartbeat(HB_HU)

    def _watchdog(self) -> None:
        now = timebase.now()
        for rt in self.channels.values():
            if rt.link is None or not rt.config.has_peer:
                continue
            seen = rt.link.peer_last_seen
            if rt.state in (READY, NEGOTIATING, ANNOUNCED) and seen is not None and now - seen > self.peer_timeout:
                self._event(rt, f"no frames from 0x{rt.config.peer_id:03X} for {self.peer_timeout:.0f}s")
                self._set_state(rt, LOST)

    # ── helpers ─────────────────────────────────────────────────────
    def _set_state(self, rt: ChannelRuntime, state: str) -> None:
        if rt.state != state:
            rt.state = state
            rt.last_change = timebase.now()
            self._event(rt, f"channel {rt.config.name} -> {state}")
            self._changed()

    def _event(self, rt: ChannelRuntime, text: str) -> None:
        self.sniffer.add_event(timebase.now(), rt.config.hu_id, text)

    def _on_link_event(self, rt: ChannelRuntime, ev: dict) -> None:
        kind = ev.get("kind")
        if kind in ("fail", "retry", "warn", "tx-refused", "ack-unexpected"):
            detail = ev.get("text") or ev.get("kind")
            tok = ev.get("token")
            self._event(rt, f"{kind}: {detail}" + (f" (token {tok:02X})" if isinstance(tok, int) else "")
                        + (f" [{ev.get('label')}]" if ev.get("label") else ""))

    def _changed(self) -> None:
        for fn in list(self.listeners):
            try:
                fn()
            except Exception:
                pass

    def status(self) -> dict:
        out = {"running": self.running, "bus": self.bus_role, "channels": {}}
        now = timebase.now()
        for name, rt in self.channels.items():
            link = rt.link
            out["channels"][name] = {
                "hu_id": f"0x{rt.config.hu_id:03X}", "peer_id": f"0x{rt.config.peer_id:03X}",
                "enabled": rt.config.enabled, "state": rt.state, "has_peer": rt.config.has_peer,
                "peer_caps": rt.peer_caps.hex(" "),
                "peer_seen_s_ago": None if not link or link.peer_last_seen is None else round(now - link.peer_last_seen, 2),
                "queue": link.queue_len() if link else 0,
                "stats": dict(link.stats) if link else {},
            }
        return out
