"""Audio display service: source selection on 0x61D and display records on 0x628.

Records (captured templates, fields filled in):

radio  20 0D 00 00 <band> 00 <valid> <preset> <freq x3> FF  <slot> 06 <len> <text1>
       then  00 02 06 <len> <text2>   or   00 00 00 00 when there is no second text
media  22 0D 00 00 <type> 00 <playing> 00 00 <track>
       06 <len> <title>  00 02 00 00  06 <len> <album>  00 01  06 <len> <artist>
       <type> sets the page title (bench 2026-10-12): 01 CD, 02 Music Box, 03 iPod, 04 USB,
       05 Bluetooth Audio, 06 DVD, 07 "IAS", 08 DISC. Byte 9 is shown as "Track N".
aux    26 0D 00 00 02
status 2A 0D 00 <dev> <msg> 00 00  message over the audio page: 00 blank, 01 "Audio OFF",
       2B/2C "iPod is not connected", 2D "Check USB memory or iPod", 30 "Please eject disc".
       Sent only when there is no metadata, otherwise it hides the track info.

Slot 1 (title) and slot 3 (artist) show on the meter's rich-source page;
slot 2 (album) is hidden. The meter generates track numbers itself.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Optional

from q50tb.av.services.common import decode_freq3, encode_freq3, parse_hex, utf16_field

REGISTRY = os.path.join(os.path.dirname(__file__), "..", "..", "..", "registry", "av_sources.json")


@dataclass
class Source:
    key: str
    label: str
    select: bytes
    kind: str                       # radio | media | aux | extra_only
    band: int = 0
    slot: int = 1
    decimal: bool = False
    default_freq: float = 0
    media_type: int = 0
    record: bytes = b""
    extra: bytes = b""
    confidence: str = "candidate"
    evidence: str = ""


def load_sources(path: str = REGISTRY) -> list[Source]:
    with open(os.path.abspath(path), encoding="utf-8") as fh:
        data = json.load(fh)
    out = []
    for d in data["sources"]:
        out.append(Source(
            key=d["key"], label=d.get("label", d["key"]), select=parse_hex(d["select"]), kind=d["kind"],
            band=int(d.get("band", "0"), 16), slot=int(d.get("slot", "1"), 16), decimal=bool(d.get("decimal")),
            default_freq=float(d.get("default_freq", 0)), media_type=int(d.get("media_type", "0"), 16),
            record=parse_hex(d.get("record", "")), extra=parse_hex(d.get("extra", "")),
            confidence=d.get("confidence", "candidate"), evidence=d.get("evidence", "")))
    return out


@dataclass
class AudioState:
    source: str = "AUX"
    # radio
    frequency: Optional[float] = None
    preset: int = 0
    tuned: bool = True
    station: str = ""          # PS name / channel name (text 1)
    radio_text: str = ""       # text 2
    # media
    title: str = ""
    album: str = ""
    artist: str = ""
    playing: bool = True
    elapsed: int = 0
    extra: dict = field(default_factory=dict)


def radio_record(src: Source, st: AudioState) -> bytes:
    freq = st.frequency if st.frequency is not None else (src.default_freq or None)
    hdr = bytes([0x20, 0x0D, 0x00, 0x00, src.band, 0x00, 0x01 if st.tuned else 0x00, st.preset & 0xFF])
    hdr += encode_freq3(freq if st.tuned else None, src.decimal) + b"\xFF"
    station = st.station
    if src.key == "FM" and station:
        station = station[:8].ljust(8)      # RDS PS names are 8 characters, space padded
    body = bytes([src.slot, 0x06]) + utf16_field(station)
    if st.radio_text:
        body += bytes([0x00, 0x02, 0x06]) + utf16_field(st.radio_text)
    else:
        body += b"\x00\x00\x00\x00"
    return hdr + body


def media_record(src: Source, st: AudioState) -> bytes:
    hdr = bytes([0x22, 0x0D, 0x00, 0x00, src.media_type, 0x00, 0x01 if st.playing else 0x00,
                 0x00, 0x00, st.elapsed & 0xFF])
    if not st.playing and not (st.title or st.album or st.artist):
        return hdr[:6] + bytes(16)      # "no media" form seen in captures (all zero)
    return (hdr + b"\x06" + utf16_field(st.title) + b"\x00\x02\x00\x00\x06" + utf16_field(st.album)
            + b"\x00\x01\x06" + utf16_field(st.artist))


def build_records(src: Source, st: AudioState) -> list[bytes]:
    if src.kind == "radio":
        recs = [radio_record(src, st)]
    elif src.kind == "media":
        recs = [media_record(src, st)]
        if st.title or st.album or st.artist:
            return recs     # the status record would replace the track info with a message
    elif src.kind == "aux":
        recs = [src.record or bytes.fromhex("260D000002")]
    else:
        recs = []
    if src.extra:
        recs.append(src.extra)
    return recs


def _texts(b: bytes, start: int) -> list[str]:
    out, j = [], start
    while j < len(b):
        if b[j] == 0x06 and j + 1 < len(b):
            n = b[j + 1]
            out.append(b[j + 2:j + 2 + n].decode("utf-16-le", "replace").strip("\x00"))
            j += 2 + n
        else:
            j += 1
    return out


def describe_record(b: bytes) -> str:
    if len(b) < 3:
        return b.hex(" ")
    if b[0] == 0x20 and len(b) >= 12:
        band = {0x01: "FM", 0x10: "AM", 0x23: "SXM"}.get(b[4], f"band {b[4]:02X}")
        texts = _texts(b, 13)
        return f"{band} {decode_freq3(b[8:11])} preset {b[7]} " + " | ".join(repr(t) for t in texts)
    if b[0] == 0x22 and len(b) >= 10:
        texts = _texts(b, 10)
        return f"media type {b[4]:02X} {'playing' if b[6] else 'stopped'} t={b[9]} " + " | ".join(repr(t) for t in texts)
    if b[0] == 0x26:
        return f"AUX/short {b.hex(' ')}"
    if b[0] == 0x2A:
        return f"source extra {b.hex(' ')}"
    return b.hex(" ")


class AudioService:
    def __init__(self, host):
        self.host = host
        self.sources = load_sources()
        self.state = AudioState()
        self.send_source_select = True     # 0x61D 20 20 <code> (no peer on the bench: sent without ACK)

    def source(self, key: Optional[str] = None) -> Source:
        key = key or self.state.source
        for s in self.sources:
            if s.key == key:
                return s
        raise KeyError(f"unknown audio source {key!r}")

    def reload(self) -> None:
        self.sources = load_sources()

    def select(self, key: str, **fields) -> None:
        src = self.source(key)
        self.state.source = key
        for k, v in fields.items():
            if hasattr(self.state, k):
                setattr(self.state, k, v)
        if self.state.frequency is None and src.kind == "radio":
            self.state.frequency = src.default_freq
        if self.send_source_select:
            self.host.send("source", bytes([0x20, 0x20]) + src.select, label=f"source {key}",
                           expect_ack=self.host.channel_has_peer("source"))
        self.push()

    def update(self, **fields) -> None:
        for k, v in fields.items():
            if not hasattr(self.state, k):
                raise KeyError(f"unknown audio field {k}")
            setattr(self.state, k, v)
        self.push()

    def push(self) -> None:
        src = self.source()
        for rec in build_records(src, self.state):
            self.host.send("settings", rec, label=f"audio {src.key}")
