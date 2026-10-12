"""Nissan AV-bus transport (ISO-TP-like with a transaction token).

Frame formats (8 bytes, padded with 0xFF):

    SF   0L TT d..         L = 1 + payload length (token counted), L <= 7
    FF   1L LL TT d d d d d  12-bit length counts the token
    CF   2n d d d d d d d    n = 1..F, 0, 1 ...
    FC   30 BS ST           receiver -> sender after an FF (bench/meter: 30 00 0A)
    ACK  E0 TT             receiver -> sender after every complete message
    HB   F0/F1 xx 0A 01 ...  presence heartbeat (~500 ms)

Token: high nibble is a per-direction transaction counter (0 for the first
message after a channel opens, then 1..7 rolling, 0 skipped). Low nibble is
the application-fragment index; bit 3 set means more fragments follow.
Records longer than 20 bytes are split into 20-byte fragments, each sent and
ACKed as its own transport message, all with the same high nibble.

``AvReassembler`` decodes one CAN ID passively (used by the sniffer and by
links). ``AvLink`` is one active channel pair: it ACKs and flow-controls what
the peer sends and transmits our records one transaction at a time.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

from q50tb.core import timebase
from q50tb.core.scheduler import Job, Scheduler

PAD = 0xFF
FRAG_SIZE = 20
FC_DEFAULT = bytes([0x30, 0x00, 0x0A]) + bytes([PAD] * 5)
HB_HU = bytes([0xF0, 0x80, 0x0A, 0x01, PAD, PAD, PAD, PAD])


def pad8(b: bytes) -> bytes:
    return bytes(b) + bytes([PAD] * (8 - len(b))) if len(b) < 8 else bytes(b[:8])


def frame_kind(data: bytes) -> str:
    if not data:
        return "?"
    hi = data[0] >> 4
    if data[0] == 0xE0:
        return "ACK"
    return {0x0: "SF", 0x1: "FF", 0x2: "CF", 0x3: "FC", 0xF: "HB"}.get(hi, "?")


def segment(token: int, payload: bytes) -> list[bytes]:
    """Transport frames for one message (token + payload)."""
    body = bytes([token]) + bytes(payload)
    n = len(body)
    if n <= 7:
        return [pad8(bytes([n]) + body)]
    if n > 0xFFF:
        raise ValueError("AV transport message too long")
    frames = [bytes([0x10 | (n >> 8), n & 0xFF]) + body[:6]]
    rest = body[6:]
    sn = 1
    while rest:
        frames.append(pad8(bytes([0x20 | sn]) + rest[:7]))
        rest = rest[7:]
        sn = (sn + 1) & 0xF
    return frames


def split_fragments(record: bytes) -> list[bytes]:
    if len(record) <= FRAG_SIZE:
        return [bytes(record)]
    return [bytes(record[i:i + FRAG_SIZE]) for i in range(0, len(record), FRAG_SIZE)]


@dataclass
class RxMessage:
    can_id: int
    token: int
    data: bytes
    t: float
    frames: int = 1


@dataclass
class RxRecord:
    """Application record reassembled from one or more fragments."""
    can_id: int
    token_hi: int
    data: bytes
    t: float
    fragments: int = 1


class AvReassembler:
    """Passive decoder for one CAN ID: transport + fragment layer."""

    def __init__(self, can_id: int):
        self.can_id = can_id
        self._ff: Optional[dict] = None
        self._frag: Optional[dict] = None
        self.errors = 0

    def feed(self, data: bytes, t: float) -> tuple[Optional[RxMessage], Optional[RxRecord], str]:
        """Returns (completed transport message, completed record, frame kind)."""
        kind = frame_kind(data)
        msg = None
        if kind == "SF":
            n = data[0] & 0xF
            if 1 <= n <= 7 and len(data) >= n + 1:
                msg = RxMessage(self.can_id, data[1], bytes(data[2:1 + n]), t)
            else:
                self.errors += 1
        elif kind == "FF":
            n = ((data[0] & 0xF) << 8) | data[1]
            self._ff = {"n": n, "buf": bytearray(data[2:8]), "sn": 1, "t": t, "frames": 1}
            if len(self._ff["buf"]) >= n:
                msg = self._finish_ff()
        elif kind == "CF":
            st = self._ff
            if st is None:
                self.errors += 1
            else:
                sn = data[0] & 0xF
                if sn != st["sn"]:
                    self.errors += 1
                    self._ff = None
                else:
                    st["buf"] += data[1:8]
                    st["sn"] = (st["sn"] + 1) & 0xF
                    st["frames"] += 1
                    if len(st["buf"]) >= st["n"]:
                        msg = self._finish_ff()
        rec = self._fragment(msg) if msg else None
        return msg, rec, kind

    def _finish_ff(self) -> RxMessage:
        st, self._ff = self._ff, None
        body = bytes(st["buf"][:st["n"]])
        return RxMessage(self.can_id, body[0], body[1:], st["t"], st["frames"])

    def _fragment(self, msg: RxMessage) -> Optional[RxRecord]:
        tok = msg.token
        idx, more, hi = tok & 0x7, bool(tok & 0x8), tok >> 4
        if idx == 0 and not more:
            self._frag = None
            return RxRecord(self.can_id, hi, msg.data, msg.t, 1)
        if idx == 0:
            self._frag = {"hi": hi, "next": 1, "buf": bytearray(msg.data), "t": msg.t}
            return None
        st = self._frag
        if st is None or st["hi"] != hi or st["next"] != idx:
            self.errors += 1
            self._frag = None
            return None
        st["buf"] += msg.data
        st["next"] += 1
        if more:
            return None
        self._frag = None
        return RxRecord(self.can_id, hi, bytes(st["buf"]), st["t"], idx + 1)

    def reset(self) -> None:
        self._ff = None
        self._frag = None


# ── active link ────────────────────────────────────────────────────────
@dataclass
class TxRecord:
    data: bytes
    expect_ack: bool = True
    label: str = ""
    on_done: Optional[Callable[[bool, str], None]] = None
    attempts: int = 0
    queued_at: float = field(default_factory=timebase.now)


@dataclass
class LinkConfig:
    ack_timeout: float = 1.0
    fc_timeout: float = 0.5
    retries: int = 1
    st_min_override: Optional[float] = None   # seconds between CFs; None = use peer FC
    blind_without_fc: bool = False            # send CFs even if no FC arrives
    fc_st_min: int = 0x0A                     # what we advertise in our FC
    ack_delay: float = 0.0
    # Minimum time between our last frame and the first frame of the next record. The meter
    # drops an ACK that is followed by another frame within ~0.3 ms and then re-sends its
    # reply ~515 ms later (bench 2026-10-12); the real HU never sends that fast.
    min_record_gap: float = 0.01


class AvLink:
    """One HU<->peer channel pair. Thread-safe; RX arrives on the CAN thread."""

    def __init__(self, hu_id: int, peer_id: int, send_frame: Callable[[int, bytes], bool],
                 scheduler: Scheduler, config: Optional[LinkConfig] = None,
                 on_record: Optional[Callable[[RxRecord], None]] = None,
                 on_event: Optional[Callable[[dict], None]] = None):
        self.hu_id = hu_id
        self.peer_id = peer_id
        self.config = config or LinkConfig()
        self._send_frame = send_frame
        self._sched = scheduler
        self._on_record = on_record
        self._on_event = on_event
        self._rx = AvReassembler(peer_id)
        self._lock = threading.RLock()
        self._queue: deque[TxRecord] = deque()
        self._current: Optional[TxRecord] = None
        self._frags: list[bytes] = []
        self._frag_idx = 0
        self._token_hi = 0
        self._first = True
        self._pending_token: Optional[int] = None
        self._pending_frames: list[bytes] = []
        self._state = "idle"     # idle | wait_fc | sending_cf | wait_ack
        self._timer: Optional[Job] = None
        self._last_tx = 0.0
        self._gap_pending = False
        self.peer_last_seen: Optional[float] = None
        self.peer_heartbeat: bytes = b""
        self.stats = {"tx_msgs": 0, "rx_msgs": 0, "timeouts": 0, "retries": 0, "failed": 0}

    # ── control ─────────────────────────────────────────────────────
    def reset(self) -> None:
        """Forget tokens and queue (channel re-opened)."""
        with self._lock:
            self._cancel_timer()
            failed = ([self._current] if self._current else []) + list(self._queue)
            self._queue.clear()
            self._current = None
            self._state = "idle"
            self._token_hi = 0
            self._first = True
            self._rx.reset()
        for rec in failed:
            self._done(rec, False, "link reset")

    def queue_len(self) -> int:
        return len(self._queue) + (1 if self._current else 0)

    @property
    def busy(self) -> bool:
        return self._current is not None

    def send_record(self, data: bytes, label: str = "", expect_ack: bool = True,
                    on_done: Optional[Callable[[bool, str], None]] = None, front: bool = False) -> None:
        rec = TxRecord(bytes(data), expect_ack, label, on_done)
        with self._lock:
            if front:
                self._queue.appendleft(rec)
            else:
                self._queue.append(rec)
            if self._current is None:
                self._next_record()

    def send_heartbeat(self, data: bytes = HB_HU) -> None:
        self._raw(pad8(data), "HB")

    # ── RX path ─────────────────────────────────────────────────────
    def on_frame(self, data: bytes, t: float) -> None:
        kind = frame_kind(data)
        with self._lock:
            self.peer_last_seen = t
            if kind == "HB":
                self.peer_heartbeat = bytes(data)
                return
            if kind == "ACK":
                self._on_ack(data[1], t)
                return
            if kind == "FC":
                self._on_fc(data, t)
                return
            msg, rec, kind = self._rx.feed(data, t)
            if kind == "FF":
                fc = bytes([0x30, 0x00, self.config.fc_st_min]) + bytes([PAD] * 5)
                self._raw(fc, "FC")
            if msg is not None:
                self.stats["rx_msgs"] += 1
                self._emit("rx", token=msg.token, data=msg.data, frames=msg.frames)
                self._ack(msg.token)
        if rec is not None and self._on_record:
            self._on_record(rec)

    def _ack(self, token: int) -> None:
        frame = pad8(bytes([0xE0, token]))
        if self.config.ack_delay > 0:
            self._sched.after(self.config.ack_delay, lambda: self._raw(frame, "ACK"))
        else:
            self._raw(frame, "ACK")

    # ── TX state machine (caller holds the lock) ───────────────────
    def _next_token_hi(self) -> int:
        if self._first:
            self._first = False
            self._token_hi = 0
        else:
            self._token_hi = self._token_hi + 1 if self._token_hi < 7 else 1
        return self._token_hi

    def _next_record(self) -> None:
        if self._current is not None or not self._queue or self._gap_pending:
            return
        wait = self.config.min_record_gap - (timebase.now() - self._last_tx)
        if wait > 0:
            self._gap_pending = True
            self._sched.after(wait, self._gap_elapsed)
            return
        self._current = self._queue.popleft()
        self._frags = split_fragments(self._current.data)
        self._frag_idx = 0
        hi = self._next_token_hi()
        self._current_hi = hi
        self._send_fragment()

    def _send_fragment(self) -> None:
        rec = self._current
        idx = self._frag_idx
        more = idx < len(self._frags) - 1
        token = (self._current_hi << 4) | (idx & 0x7) | (0x08 if more else 0)
        self._pending_token = token
        frames = segment(token, self._frags[idx])
        rec.attempts += 1
        self._raw(frames[0], "SF" if len(frames) == 1 else "FF")
        self._emit("tx", token=token, data=self._frags[idx], label=rec.label,
                   fragment=f"{idx + 1}/{len(self._frags)}" if len(self._frags) > 1 else "")
        if len(frames) == 1:
            self._after_message_sent()
        else:
            self._pending_frames = frames[1:]
            self._state = "wait_fc"
            self._arm(self.config.fc_timeout, self._fc_timeout)

    def _on_fc(self, data: bytes, t: float) -> None:
        if self._state != "wait_fc":
            return
        flow = data[0] & 0xF
        if flow == 1:          # wait
            self._arm(self.config.fc_timeout, self._fc_timeout)
            return
        if flow == 2:          # overflow / abort
            self._fail("peer flow-control overflow/abort")
            return
        st = data[2] if len(data) > 2 else 0
        st_s = st / 1000.0 if st <= 0x7F else (st - 0xF0) / 10000.0 if 0xF1 <= st <= 0xF9 else 0.0
        if self.config.st_min_override is not None:
            st_s = self.config.st_min_override
        self._cancel_timer()
        self._state = "sending_cf"
        self._st = st_s
        self._send_next_cf()

    def _send_next_cf(self) -> None:
        with self._lock:
            if self._state != "sending_cf" or not self._pending_frames:
                return
            self._raw(self._pending_frames.pop(0), "CF")
            if self._pending_frames:
                if self._st > 0.0005:
                    self._timer = self._sched.after(self._st, self._send_next_cf, name="av-cf")
                else:
                    self._send_next_cf()
            else:
                self._after_message_sent()

    def _fc_timeout(self) -> None:
        with self._lock:
            if self._state != "wait_fc":
                return
            if self.config.blind_without_fc:
                self._emit("warn", text="no flow control; sending blind")
                self._state = "sending_cf"
                self._st = self.config.st_min_override or 0.01
                self._send_next_cf()
                return
            self._retry_or_fail("flow-control timeout")

    def _after_message_sent(self) -> None:
        rec = self._current
        if rec is None:
            return
        if not rec.expect_ack:
            self._state = "idle"
            self._fragment_acked()
            return
        self._state = "wait_ack"
        self._arm(self.config.ack_timeout, self._ack_timeout)

    def _on_ack(self, token: int, t: float) -> None:
        if self._state != "wait_ack" or token != self._pending_token:
            self._emit("ack-unexpected", token=token)
            return
        self._cancel_timer()
        self._state = "idle"
        self._emit("acked", token=token)
        self._fragment_acked()

    def _fragment_acked(self) -> None:
        rec = self._current
        self._frag_idx += 1
        if rec is not None and self._frag_idx < len(self._frags):
            self._send_fragment()
            return
        self._current = None
        self.stats["tx_msgs"] += 1
        if rec is not None:
            self._sched.after(0, lambda r=rec: self._done(r, True, "acked" if r.expect_ack else "sent"))
        self._next_record()

    def _ack_timeout(self) -> None:
        with self._lock:
            if self._state != "wait_ack":
                return
            self._retry_or_fail("ACK timeout")

    def _retry_or_fail(self, reason: str) -> None:
        rec = self._current
        self.stats["timeouts"] += 1
        if rec is not None and rec.attempts <= self.config.retries:
            self.stats["retries"] += 1
            self._emit("retry", text=reason, token=self._pending_token)
            self._send_fragment()
            return
        self._fail(reason)

    def _fail(self, reason: str) -> None:
        rec = self._current
        self._cancel_timer()
        self._current = None
        self._state = "idle"
        self.stats["failed"] += 1
        self._emit("fail", text=reason, label=rec.label if rec else "")
        if rec is not None:
            self._sched.after(0, lambda r=rec: self._done(r, False, reason))
        self._next_record()

    # ── helpers ─────────────────────────────────────────────────────
    def _arm(self, delay: float, fn: Callable[[], None]) -> None:
        self._cancel_timer()
        self._timer = self._sched.after(delay, fn, name="av-timer")

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _gap_elapsed(self) -> None:
        with self._lock:
            self._gap_pending = False
            self._next_record()

    def _raw(self, frame: bytes, kind: str) -> None:
        self._last_tx = timebase.now()
        ok = self._send_frame(self.hu_id, pad8(frame))
        if not ok and kind not in ("HB",):
            self._emit("tx-refused", kind=kind)

    def _done(self, rec: TxRecord, ok: bool, reason: str) -> None:
        if rec.on_done:
            try:
                rec.on_done(ok, reason)
            except Exception:
                pass

    def _emit(self, kind: str, **kw) -> None:
        if self._on_event:
            try:
                self._on_event({"t": timebase.now(), "kind": kind, "hu": self.hu_id, "peer": self.peer_id, **kw})
            except Exception:
                pass
