"""Passive AV-bus decoder: reassembles every AV channel in both directions
(RX from the bus and our own TX echo) into application records, keeps a
transaction log for the debug page and the latest decoded AV state for the
dashboard. Never transmits, so it is safe in monitor mode.
"""

from __future__ import annotations

import threading
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

from q50tb.av.protocol import AV_IDS, CHANNEL_BY_HU, CHANNEL_BY_PEER, AvMessage, describe
from q50tb.av.transport import AvReassembler, frame_kind
from q50tb.can.frame import CanFrame


@dataclass
class AvLogEntry:
    t: float
    can_id: int
    direction: str        # "HU>" (head-unit side ID) or "<PEER" (meter/peer side ID)
    kind: str             # SF/FF/CF/FC/ACK/HB/REC/EVT
    token: Optional[int]
    data: bytes
    text: str = ""
    bus_dir: str = "RX"   # RX from bus or TX by this app
    channel: str = ""

    def row(self) -> list[str]:
        return [f"{self.t:10.3f}", f"{self.can_id:03X}", self.channel, self.direction, self.bus_dir, self.kind,
                "" if self.token is None else f"{self.token:02X}", self.data.hex(" ").upper(), self.text]


@dataclass
class AvDecodedState:
    nav: Optional[bytes] = None
    nav_t: Optional[float] = None
    audio: Optional[bytes] = None
    audio_t: Optional[float] = None
    clock: Optional[bytes] = None
    clock_t: Optional[float] = None
    page_image: Optional[bytes] = None
    page_image_t: Optional[float] = None
    source_select: Optional[bytes] = None
    peer_heartbeats: dict = field(default_factory=dict)     # can_id -> (t, bytes)


class AvSniffer:
    def __init__(self, maxlen: int = 20000):
        self.log: deque[AvLogEntry] = deque(maxlen=maxlen)
        self.state = AvDecodedState()
        self._re: dict[tuple[int, str], AvReassembler] = {}
        self._lock = threading.Lock()
        self.log_frames = False          # also log every SF/FF/CF/ACK frame
        self.log_heartbeats = False
        self.seq = 0
        self.listeners: list[Callable[[AvLogEntry], None]] = []
        self.bus_role = "av"

    def clear(self) -> None:
        with self._lock:
            self.log.clear()

    def add_event(self, t: float, can_id: int, text: str, kind: str = "EVT") -> None:
        self._append(AvLogEntry(t, can_id, self._dir(can_id), kind, None, b"", text,
                                "", self._chan(can_id)))

    @staticmethod
    def _dir(can_id: int) -> str:
        return "HU>" if can_id in CHANNEL_BY_HU else "<PEER"

    @staticmethod
    def _chan(can_id: int) -> str:
        c = CHANNEL_BY_HU.get(can_id) or CHANNEL_BY_PEER.get(can_id)
        return c[2] if c else ""

    def _append(self, e: AvLogEntry) -> None:
        with self._lock:
            self.seq += 1
            self.log.append(e)
        for fn in list(self.listeners):
            try:
                fn(e)
            except Exception:
                pass

    def on_frame(self, frame: CanFrame) -> None:
        if frame.bus != self.bus_role or frame.arbitration_id not in AV_IDS or frame.is_extended:
            return
        cid = frame.arbitration_id
        data = bytes(frame.data)
        kind = frame_kind(data)
        if kind == "HB":
            if cid in CHANNEL_BY_PEER:
                self.state.peer_heartbeats[cid] = (frame.timestamp, data)
            if self.log_heartbeats:
                self._append(AvLogEntry(frame.timestamp, cid, self._dir(cid), "HB", None, data, "",
                                        frame.direction, self._chan(cid)))
            return
        key = (cid, frame.direction)
        re_ = self._re.get(key)
        if re_ is None:
            re_ = self._re[key] = AvReassembler(cid)
        msg, rec, kind = re_.feed(data, frame.timestamp)
        if self.log_frames:
            tok = data[1] if kind in ("SF", "ACK") else data[2] if kind == "FF" else None
            self._append(AvLogEntry(frame.timestamp, cid, self._dir(cid), kind, tok, data, "",
                                    frame.direction, self._chan(cid)))
        if rec is not None:
            text = describe(cid, rec.data)
            frag = f" [{rec.fragments} frags]" if rec.fragments > 1 else ""
            self._append(AvLogEntry(rec.t, cid, self._dir(cid), "REC", rec.token_hi << 4, rec.data,
                                    text + frag, frame.direction, self._chan(cid)))
            self._update_state(cid, rec.data, rec.t)

    def _update_state(self, cid: int, data: bytes, t: float) -> None:
        if len(data) < 2:
            return
        m = AvMessage.parse(data)
        chan = self._chan(cid)
        s = self.state
        if chan == "nav" and m.group == 0x20 and m.op == 0xD:
            s.nav, s.nav_t = data, t
        elif chan == "settings" and m.group in (0x20, 0x22, 0x26) and m.op == 0xD:
            s.audio, s.audio_t = data, t
        elif chan == "settings" and m.group == 0xC0 and m.item == 2 and m.op == 0xC and len(data) >= 12 \
                and data[2] == 0:
            s.page_image, s.page_image_t = data[3:12], t
        elif chan == "status" and m.group == 0x40 and m.op == 0xD:
            s.clock, s.clock_t = data, t
        elif chan == "source" and m.group == 0x20 and m.item == 2 and m.op == 0x0:
            s.source_select = data[2:]

    def entries_since(self, seq: int) -> tuple[int, list[AvLogEntry]]:
        with self._lock:
            n = self.seq - seq
            if n <= 0:
                return self.seq, []
            items = list(self.log)[-min(n, len(self.log)):]
            return self.seq, items
