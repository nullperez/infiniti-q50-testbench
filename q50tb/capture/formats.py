"""Capture file readers/writers.

Supported:
* pcapng with LINKTYPE_CAN_SOCKETCAN (227), as written by Wireshark/dumpcap
  on Linux and by this app. Multiple interfaces map to ``CanFrame.bus``;
  the EPB flags option carries direction (inbound/outbound).
* CSV written by this app (Raw Log export).
* Wireshark "Export Packet Dissections -> CSV" (columns No., Time, ..., Data, Info).
* candump logs: ``(1690000000.123456) can0 123#DEADBEEF`` (-L) and
  ``can0  123   [4]  DE AD BE EF`` (default output).
* Anything python-can's LogReader understands (.asc, .blf, .trc, .mf4) as fallback.

Readers return a ``Capture`` with frames sorted by time, timestamps made
relative to the first frame (``t0`` keeps the original absolute start).
"""

from __future__ import annotations

import csv
import io
import os
import re
import struct
from dataclasses import dataclass, field
from typing import Iterable, Optional

from q50tb.can.frame import CanFrame, RX, TX

LINKTYPE_CAN_SOCKETCAN = 227
CAN_EFF_FLAG = 0x80000000
CAN_RTR_FLAG = 0x40000000
CAN_ERR_FLAG = 0x20000000
CANFD_BRS = 0x01
CANFD_ESI = 0x02
CANFD_FDF = 0x04


@dataclass
class Capture:
    path: str
    frames: list[CanFrame] = field(default_factory=list)
    interfaces: list[str] = field(default_factory=list)
    t0: float = 0.0          # absolute timestamp of the first frame (source clock)
    fmt: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def duration(self) -> float:
        return self.frames[-1].timestamp if self.frames else 0.0

    def ids(self) -> list[int]:
        return sorted({f.arbitration_id for f in self.frames})


def _finish(cap: Capture) -> Capture:
    cap.frames.sort(key=lambda f: f.timestamp)
    if cap.frames:
        cap.t0 = cap.frames[0].timestamp
        for f in cap.frames:
            f.timestamp -= cap.t0
    if not cap.interfaces:
        cap.interfaces = sorted({f.bus for f in cap.frames})
    for i, f in enumerate(cap.frames):
        f.seq = i
    return cap


# ── pcapng ───────────────────────────────────────────────────────────
def read_pcapng(path: str) -> Capture:
    with open(path, "rb") as fh:
        buf = fh.read()
    cap = Capture(path=path, fmt="pcapng")
    off = 0
    endian = "<"
    ifaces: list[tuple[int, str, float]] = []
    while off + 12 <= len(buf):
        btype, blen = struct.unpack_from(endian + "II", buf, off)
        if btype == 0x0A0D0D0A:  # section header
            bom = struct.unpack_from("<I", buf, off + 8)[0]
            endian = "<" if bom == 0x1A2B3C4D else ">"
            blen = struct.unpack_from(endian + "I", buf, off + 4)[0]
            ifaces = []
        if blen < 12 or off + blen > len(buf):
            cap.warnings.append(f"truncated block at offset {off}")
            break
        if btype == 1:  # interface description
            link = struct.unpack_from(endian + "H", buf, off + 8)[0]
            name, tsres = f"if{len(ifaces)}", 6
            o, end = off + 16, off + blen - 4
            while o + 4 <= end:
                code, ln = struct.unpack_from(endian + "HH", buf, o)
                if code == 0:
                    break
                val = buf[o + 4:o + 4 + ln]
                if code == 2:
                    name = val.rstrip(b"\0").decode(errors="replace")
                elif code == 9 and val:
                    tsres = val[0]
                o += 4 + ((ln + 3) & ~3)
            div = float(2 ** (tsres & 0x7F)) if tsres & 0x80 else float(10 ** tsres)
            ifaces.append((link, name, div))
            if name not in cap.interfaces:
                cap.interfaces.append(name)
        elif btype == 6:  # enhanced packet
            iid, th, tl, caplen, _orig = struct.unpack_from(endian + "IIIII", buf, off + 8)
            if iid >= len(ifaces):
                off += blen
                continue
            link, name, div = ifaces[iid]
            pkt = buf[off + 28: off + 28 + caplen]
            direction = RX
            o = off + 28 + ((caplen + 3) & ~3)
            end = off + blen - 4
            while o + 4 <= end:
                code, ln = struct.unpack_from(endian + "HH", buf, o)
                if code == 0:
                    break
                if code == 2 and ln >= 4:
                    flags = struct.unpack_from(endian + "I", buf, o + 4)[0]
                    if flags & 0x3 == 2:
                        direction = TX
                o += 4 + ((ln + 3) & ~3)
            if link == LINKTYPE_CAN_SOCKETCAN and len(pkt) >= 8:
                raw_id = struct.unpack_from(">I", pkt, 0)[0]
                length = pkt[4]
                fdflags = pkt[5]
                ext = bool(raw_id & CAN_EFF_FLAG)
                is_fd = len(pkt) > 16 or bool(fdflags & CANFD_FDF)
                cap.frames.append(CanFrame(
                    arbitration_id=raw_id & (0x1FFFFFFF if ext else 0x7FF),
                    data=bytes(pkt[8:8 + length]), is_extended=ext, is_fd=is_fd,
                    bitrate_switch=is_fd and bool(fdflags & CANFD_BRS),
                    error_state_indicator=is_fd and bool(fdflags & CANFD_ESI),
                    is_remote=bool(raw_id & CAN_RTR_FLAG), is_error=bool(raw_id & CAN_ERR_FLAG),
                    timestamp=((th << 32) | tl) / div, bus=name, direction=direction))
        elif btype == 3:  # simple packet block: no interface info / timestamp
            cap.warnings.append("simple packet blocks ignored")
        off += blen
    return _finish(cap)


class PcapngWriter:
    """Streams frames to pcapng; one interface per bus name, ns timestamps."""

    def __init__(self, path: str, base_unix_time: float = 0.0):
        self._fh = open(path, "wb")
        self._ifaces: dict[str, int] = {}
        self._base = base_unix_time
        self.count = 0
        shb_opts = self._opt(4, b"q50tb Q50 TestBench") + self._opt_end()
        body = struct.pack("<IHHq", 0x1A2B3C4D, 1, 0, -1) + shb_opts
        self._block(0x0A0D0D0A, body)

    @staticmethod
    def _opt(code: int, val: bytes) -> bytes:
        pad = (-len(val)) % 4
        return struct.pack("<HH", code, len(val)) + val + b"\0" * pad

    @staticmethod
    def _opt_end() -> bytes:
        return struct.pack("<HH", 0, 0)

    def _block(self, btype: int, body: bytes) -> None:
        blen = 12 + len(body)
        self._fh.write(struct.pack("<II", btype, blen) + body + struct.pack("<I", blen))

    def _iface(self, name: str) -> int:
        if name not in self._ifaces:
            opts = self._opt(2, name.encode()) + self._opt(9, bytes([9])) + self._opt_end()
            self._block(1, struct.pack("<HHI", LINKTYPE_CAN_SOCKETCAN, 0, 0) + opts)
            self._ifaces[name] = len(self._ifaces)
        return self._ifaces[name]

    def write(self, frame: CanFrame, unix_time: Optional[float] = None) -> None:
        iid = self._iface(frame.bus or "can")
        t = unix_time if unix_time is not None else self._base + frame.timestamp
        ns = int(round(t * 1e9))
        raw_id = frame.arbitration_id | (CAN_EFF_FLAG if frame.is_extended else 0)
        if frame.is_remote:
            raw_id |= CAN_RTR_FLAG
        if frame.is_error:
            raw_id |= CAN_ERR_FLAG
        data = bytes(frame.data)
        if frame.is_fd:
            flags = CANFD_FDF | (CANFD_BRS if frame.bitrate_switch else 0) | (CANFD_ESI if frame.error_state_indicator else 0)
            pkt = struct.pack(">IBBBB", raw_id, len(data), flags, 0, 0) + data.ljust(64, b"\0")
        else:
            pkt = struct.pack(">IBBBB", raw_id, len(data), 0, 0, 0) + data.ljust(8, b"\0")
        epb_flags = 2 if frame.direction == TX else 1
        opts = self._opt(2, struct.pack("<I", epb_flags)) + self._opt_end()
        pad = (-len(pkt)) % 4
        body = struct.pack("<IIIII", iid, (ns >> 32) & 0xFFFFFFFF, ns & 0xFFFFFFFF, len(pkt), len(pkt)) \
            + pkt + b"\0" * pad + opts
        self._block(6, body)
        self.count += 1

    def flush(self) -> None:
        self._fh.flush()

    def close(self) -> None:
        if not self._fh.closed:
            self._fh.close()


def write_pcapng(path: str, frames: Iterable[CanFrame], base_unix_time: float = 0.0) -> int:
    w = PcapngWriter(path, base_unix_time)
    try:
        for f in frames:
            w.write(f)
    finally:
        w.close()
    return w.count


# ── CSV ──────────────────────────────────────────────────────────────
APP_CSV_HEADER = ["Time (s)", "Bus", "Direction", "ID", "Extended", "FD", "BRS", "DLC", "Data"]


def write_app_csv(path: str, frames: Iterable[CanFrame]) -> int:
    n = 0
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(APP_CSV_HEADER)
        for f in frames:
            w.writerow([f"{f.timestamp:.6f}", f.bus, f.direction, f.id_text, int(f.is_extended),
                        int(f.is_fd), int(f.bitrate_switch), f.dlc, f.data_text])
            n += 1
    return n


_WS_INFO = re.compile(r"ID:\s*\d+\s*\(0x([0-9a-fA-F]+)\)")


def read_csv(path: str) -> Capture:
    with open(path, newline="", encoding="utf-8-sig") as fh:
        sample = fh.read(4096)
        fh.seek(0)
        reader = csv.reader(fh)
        header = next(reader, [])
        cols = {h.strip().lower(): i for i, h in enumerate(header)}
        cap = Capture(path=path)
        if "direction" in cols and "id" in cols:
            cap.fmt = "csv-app"
            for row in reader:
                if len(row) < len(APP_CSV_HEADER):
                    continue
                cap.frames.append(CanFrame(
                    arbitration_id=int(row[cols["id"]], 16), data=bytes.fromhex(row[cols["data"]]),
                    is_extended=row[cols["extended"]] == "1", is_fd=row[cols["fd"]] == "1",
                    bitrate_switch=row[cols["brs"]] == "1", timestamp=float(row[cols["time (s)"]]),
                    bus=row[cols["bus"]] or "can", direction=TX if row[cols["direction"]].upper() == "TX" else RX))
        elif "info" in cols and "data" in cols and "time" in cols:
            cap.fmt = "csv-wireshark"
            for row in reader:
                try:
                    m = _WS_INFO.search(row[cols["info"]])
                    if not m:
                        continue
                    cid = int(m.group(1), 16)
                    data = bytes.fromhex(row[cols["data"]].replace(":", "").replace(" ", ""))
                    cap.frames.append(CanFrame(arbitration_id=cid & 0x1FFFFFFF, data=data,
                                               is_extended=cid > 0x7FF, is_fd=len(data) > 8,
                                               timestamp=float(row[cols["time"]]), bus="can"))
                except (ValueError, IndexError):
                    continue
        else:
            raise ValueError(f"unrecognised CSV columns: {header[:10]} ({sample[:80]!r})")
    return _finish(cap)


# ── candump ──────────────────────────────────────────────────────────
_CANDUMP_L = re.compile(r"^\((\d+\.\d+)\)\s+(\S+)\s+([0-9A-Fa-f]+)(##?)([0-9A-Fa-f.]*)\s*([RT])?")
_CANDUMP_STD = re.compile(r"^\s*(?:\((\d+\.\d+)\)\s+)?(\S+)\s+([0-9A-Fa-f]+)\s+\[(\d+)\]\s*([0-9A-Fa-f ]*)")


def read_candump(path: str) -> Capture:
    cap = Capture(path=path, fmt="candump")
    with open(path, encoding="utf-8", errors="replace") as fh:
        for n, line in enumerate(fh):
            m = _CANDUMP_L.match(line)
            if m:
                ts, iface, cid, sep, data, direction = m.groups()
                is_fd = sep == "##"
                payload = data[1:] if is_fd else data
                flags = int(data[0], 16) if is_fd and data else 0
                if payload.upper() == "R":
                    payload = ""
                cap.frames.append(CanFrame(
                    arbitration_id=int(cid, 16), data=bytes.fromhex(payload.replace(".", "")),
                    is_extended=len(cid) > 3, is_fd=is_fd, bitrate_switch=bool(flags & 1),
                    timestamp=float(ts), bus=iface, direction=TX if direction == "T" else RX))
                continue
            m = _CANDUMP_STD.match(line)
            if m:
                ts, iface, cid, _dlc, data = m.groups()
                cap.frames.append(CanFrame(
                    arbitration_id=int(cid, 16), data=bytes.fromhex(data.replace(" ", "")),
                    is_extended=len(cid) > 3, timestamp=float(ts) if ts else n * 0.001, bus=iface))
    return _finish(cap)


def write_candump(path: str, frames: Iterable[CanFrame], base_unix_time: float = 0.0) -> int:
    n = 0
    with open(path, "w", encoding="utf-8") as fh:
        for f in frames:
            sep = "##" + ("1" if f.bitrate_switch else "0") if f.is_fd else "#"
            fh.write(f"({base_unix_time + f.timestamp:.6f}) {f.bus or 'can0'} {f.id_text}{sep}"
                     f"{bytes(f.data).hex().upper()} {'T' if f.direction == TX else 'R'}\n")
            n += 1
    return n


def _read_python_can(path: str) -> Capture:
    import can
    cap = Capture(path=path, fmt="python-can:" + os.path.splitext(path)[1].lower())
    for msg in can.LogReader(path):
        if msg.is_error_frame:
            continue
        cap.frames.append(CanFrame(
            arbitration_id=msg.arbitration_id, data=bytes(msg.data), is_extended=msg.is_extended_id,
            is_fd=msg.is_fd, bitrate_switch=msg.bitrate_switch, timestamp=msg.timestamp,
            bus=str(msg.channel) if msg.channel is not None else "can",
            direction=RX if msg.is_rx else TX))
    return _finish(cap)


def load_capture(path: str) -> Capture:
    ext = os.path.splitext(path)[1].lower()
    with open(path, "rb") as fh:
        magic = fh.read(4)
    if magic == b"\x0a\x0d\x0d\x0a":
        return read_pcapng(path)
    if ext == ".csv":
        return read_csv(path)
    if ext in (".log", ".candump", ".txt"):
        cap = read_candump(path)
        if cap.frames:
            return cap
    if ext in (".asc", ".blf", ".trc", ".mf4"):
        return _read_python_can(path)
    raise ValueError(f"unsupported capture format: {path}")


def save_capture(path: str, frames: list[CanFrame], base_unix_time: float = 0.0) -> int:
    ext = os.path.splitext(path)[1].lower()
    if ext == ".csv":
        return write_app_csv(path, frames)
    if ext in (".log", ".candump"):
        return write_candump(path, frames, base_unix_time)
    return write_pcapng(path, frames, base_unix_time)


CAPTURE_FILTER = ("Captures (*.pcapng *.csv *.log *.candump *.asc *.blf *.trc);;"
                  "pcapng (*.pcapng);;CSV (*.csv);;candump (*.log *.candump);;All files (*)")
