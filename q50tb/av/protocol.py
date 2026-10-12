"""Application-layer model for the Nissan AV bus.

Every reassembled record starts with two bytes: ``group`` and
``(item << 4) | op``. Ops seen in captures:

    0 SET     write an item          (HU ``C0 20 <image>``)
    1 GET     read an item           (HU ``C0 21``)
    C RESP    answer, status first   (meter ``C0 2C 00 <image>``; status 00 ok, FF not ready)
    D NOTIFY  unsolicited record     (audio/nav ``20 0D``, clock ``40 0D``, alert ``C4 2D``)
    F ERROR   unsupported item       (``41 61`` -> ``41 6F``)

The channel handshake uses group 00: peer ``00 20 <caps>`` announce, HU
``00 21`` answer, peer ``00 2C <caps>``, HU ``00 2C``. Inferred from the
2026-09 captures; descriptions here are for the debug log, not a spec.
"""

from __future__ import annotations

from dataclasses import dataclass

OP_SET, OP_GET, OP_RESP, OP_NOTIFY, OP_ERROR = 0x0, 0x1, 0xC, 0xD, 0xF
OP_NAMES = {0x0: "SET", 0x1: "GET", 0xC: "RESP", 0xD: "NOTIFY", 0xF: "ERR"}

# Channel pairs: (HU id, peer id, short name, description)
CHANNELS = [
    (0x603, 0x683, "status", "Clock / general status (meter)"),
    (0x628, 0x6A8, "settings", "Page settings, preferences, audio display (meter)"),
    (0x629, 0x6A9, "nav", "Navigation instructions (meter)"),
    (0x601, 0x681, "buttons", "Panel/steering button events (car peer)"),
    (0x606, 0x686, "capability", "Capability / vehicle info (car peer)"),
    (0x61D, 0x69D, "source", "Source selection + date/time (car peer)"),
    (0x62B, 0x6AB, "touch", "Touch/UI family (car peer)"),
    (0x62C, 0x6AC, "identity", "Identity / GPS / VIN (car peer)"),
]
CHANNEL_BY_HU = {hu: (hu, peer, name, desc) for hu, peer, name, desc in CHANNELS}
CHANNEL_BY_PEER = {peer: (hu, peer, name, desc) for hu, peer, name, desc in CHANNELS}
AV_IDS = {hu for hu, *_ in CHANNELS} | {peer for _, peer, *_ in CHANNELS}

SETTINGS_ITEMS = {0x0: "capabilities", 0x2: "page image", 0x8: "alerts (outside temp / nav)",
                  0xA: "tire pressure unit", 0xC: "meter language", 0xE: "display effect"}


@dataclass(frozen=True)
class AvMessage:
    group: int
    item: int
    op: int
    payload: bytes = b""

    @property
    def raw(self) -> bytes:
        return bytes([self.group & 0xFF, ((self.item & 0xF) << 4) | (self.op & 0xF)]) + bytes(self.payload)

    @classmethod
    def parse(cls, data: bytes) -> "AvMessage":
        if len(data) < 2:
            return cls(data[0] if data else 0, 0, 0, b"")
        return cls(data[0], data[1] >> 4, data[1] & 0xF, bytes(data[2:]))

    @property
    def op_name(self) -> str:
        return OP_NAMES.get(self.op, f"op{self.op:X}")

    def key(self) -> tuple[int, int]:
        return (self.group, self.item)

    def __str__(self) -> str:
        return f"{self.group:02X}.{self.item:X} {self.op_name} {self.payload.hex(' ').upper()}"


def describe(can_id: int, data: bytes) -> str:
    """Best-effort human description of an application record for the debug log."""
    if not data:
        return ""
    m = AvMessage.parse(data)
    hu = CHANNEL_BY_HU.get(can_id) or CHANNEL_BY_PEER.get(can_id)
    chan = hu[2] if hu else ""
    if m.group == 0x00:
        if m.item == 2 and m.op == OP_SET:
            return f"announce (channel open request) caps={m.payload.hex(' ')}"
        if m.item == 2 and m.op == OP_GET:
            return "open answer (00 21)"
        if m.item == 2 and m.op == OP_RESP:
            return f"open confirm caps={m.payload.hex(' ')}"
    if chan == "settings" and m.group == 0xC0:
        name = SETTINGS_ITEMS.get(m.item, f"item {m.item:X}")
        if m.op == OP_RESP and m.payload:
            status = "ok" if m.payload[0] == 0 else f"status {m.payload[0]:02X}"
            return f"settings {name} = {m.payload[1:].hex(' ')} ({status})"
        return f"settings {m.op_name} {name} {m.payload.hex(' ')}"
    if chan == "settings" and m.group == 0xC4:
        if m.item == 0 and m.op == OP_SET:
            return "FACTORY RESET request"
        if m.item == 0 and m.op == OP_RESP:
            return "factory reset acknowledged"
        if m.item == 2 and m.op == OP_NOTIFY:
            return "driver break alert"
    if chan == "settings" and m.group in (0x20, 0x22, 0x26, 0x2A) and m.op == OP_NOTIFY:
        from q50tb.av.services.audio import describe_record
        return "audio " + describe_record(data)
    if chan == "nav" and m.group == 0x20 and m.op == OP_NOTIFY:
        from q50tb.av.services.navigation import describe_record
        return "nav " + describe_record(data)
    if chan == "status" and m.group == 0x40 and m.op == OP_NOTIFY and len(m.payload) >= 3:
        mode, hh, mm = m.payload[:3]
        return f"clock mode {mode:02X} {hh:02X}:{mm:02X}"
    if chan == "source" and m.group == 0x24 and m.item == 4 and len(m.payload) >= 8:
        p = m.payload.hex()
        return f"date/time {p[0:4]}-{p[4:6]}-{p[6:8]} {p[8:10]}:{p[10:12]}:{p[12:14]} dow {p[14:16]}"
    if chan == "source" and m.group == 0x20 and m.item == 2 and m.op == OP_SET:
        return f"source select {m.payload.hex(' ')}"
    if m.op == OP_RESP and m.payload:
        return f"{m.group:02X}.{m.item:X} RESP status {m.payload[0]:02X} {m.payload[1:].hex(' ')}"
    return str(m)
