"""A minimal software stand-in for the meter's AV side (683/6A8/6A9).

For offline development and tests only: it announces each channel until the
HU answers, completes the handshake, keeps a settings image that accepts
writes, and answers factory reset. It is not a model of the real meter's
firmware - the real meter decides what to accept.
"""

from __future__ import annotations

from typing import Optional

from q50tb.av.protocol import OP_GET, OP_SET, AvMessage
from q50tb.av.transport import AvLink, RxRecord, pad8
from q50tb.can.frame import CanFrame, RX
from q50tb.core.bus_manager import BusManager
from q50tb.core.scheduler import Job, Scheduler

OWNER = "virtual-meter"

CHANNELS = {  # meter tx id: (hu id, caps, heartbeat)
    0x683: (0x603, bytes.fromhex("00010500"), bytes.fromhex("F00C0A01FFFFFFFF")),
    0x6A8: (0x628, bytes.fromhex("00020200"), bytes.fromhex("F1160A01FFFFFFFF")),
    0x6A9: (0x629, bytes.fromhex("00010500"), bytes.fromhex("F0520A01FFFFFFFF")),
}


class VirtualMeter:
    def __init__(self, buses: BusManager, scheduler: Scheduler, bus_role: str = "av"):
        self.buses = buses
        self.sched = scheduler
        self.role = bus_role
        self.links: dict[int, AvLink] = {}
        self.open: dict[int, bool] = {}
        self.items = {0x0: bytes.fromhex("9F000000"), 0x2: bytes.fromhex("55DD5FFDFDC0000000"),
                      0x8: bytes.fromhex("0100"), 0xA: bytes.fromhex("0202F0"),
                      0xC: bytes.fromhex("0101E00000"), 0xE: bytes.fromhex("01")}
        self.received: list[tuple[int, bytes]] = []
        self.reject_page_writes = False
        self._jobs: list[Job] = []

    def start(self) -> None:
        ch = self.buses.get(self.role)
        if ch is None:
            raise RuntimeError("virtual meter: bus not open")
        for tx, (hu, caps, hb) in CHANNELS.items():
            self.links[tx] = AvLink(tx, hu, self._send, self.sched,
                                    on_record=lambda rec, t=tx: self._on_record(t, rec))
            self.open[tx] = False
        ch.subscribe(self._on_frame)
        self._jobs.append(self.sched.every(0.5, self._heartbeat, name="vm-hb"))
        self._jobs.append(self.sched.every(1.5, self._announce, name="vm-announce", phase=0.1))

    def stop(self) -> None:
        for j in self._jobs:
            j.cancel()
        ch = self.buses.channels.get(self.role)
        if ch:
            ch.unsubscribe(self._on_frame)

    def reboot(self) -> None:
        for tx, link in self.links.items():
            link.reset()
            self.open[tx] = False

    def _send(self, can_id: int, data: bytes) -> bool:
        return self.buses.send(self.role, CanFrame(can_id, pad8(data)), OWNER)

    def _heartbeat(self) -> None:
        for tx, (_hu, _caps, hb) in CHANNELS.items():
            self._send(tx, hb)

    def _announce(self) -> None:
        for tx, (_hu, caps, _hb) in CHANNELS.items():
            if not self.open[tx] and not self.links[tx].busy:
                self.links[tx].send_record(b"\x00\x20" + caps, label="announce")

    def _on_frame(self, frame: CanFrame) -> None:
        if frame.direction != RX:
            return
        for tx, (hu, _c, _h) in CHANNELS.items():
            if frame.arbitration_id == hu:
                self.links[tx].on_frame(bytes(frame.data), frame.timestamp)

    def _on_record(self, tx: int, rec: RxRecord) -> None:
        self.received.append((tx, rec.data))
        m = AvMessage.parse(rec.data)
        link = self.links[tx]
        caps = CHANNELS[tx][1]
        if m.group == 0x00 and m.item == 2 and m.op == OP_GET:
            link.send_record(b"\x00\x2C" + caps, label="open confirm")
            if tx == 0x6A8:
                link.send_record(bytes.fromhex("080D0101E0000003 03E00202C00202F0".replace(" ", "")))
            return
        if m.group == 0x00 and m.item == 2 and m.op == 0xC:
            self.open[tx] = True
            return
        if tx == 0x6A8 and m.group == 0xC0:
            if m.op == OP_GET:
                val = self.items.get(m.item)
                link.send_record(bytes([0xC0, (m.item << 4) | 0xC]) + (b"\x00" + val if val is not None else b"\xFF"))
            elif m.op == OP_SET:
                if m.item == 0xA and len(m.payload) >= 2:
                    self.items[0xA] = bytes([0x02, m.payload[1], 0xF0])
                elif m.item == 0x2 and self.reject_page_writes:
                    pass
                elif m.item in self.items:
                    self.items[m.item] = bytes(m.payload)
                link.send_record(bytes([0xC0, (m.item << 4) | 0xC, 0x00]) + self.items.get(m.item, b""))
        elif tx == 0x6A8 and m.group == 0xC4 and m.item == 0 and m.op == OP_SET:
            link.send_record(b"\xC4\x0C")


def install_on(buses: BusManager, scheduler: Scheduler, role: str = "av") -> Optional[VirtualMeter]:
    vm = VirtualMeter(buses, scheduler, role)
    vm.start()
    return vm
