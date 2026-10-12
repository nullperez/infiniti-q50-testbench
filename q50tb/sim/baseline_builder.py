"""Build a background-message profile from a capture.

For every ID on one interface: median period, most common DLC, a
representative payload (the frame agreeing with the most per-byte modes),
rolling counters (bit fields stepping by a constant), checksums
(sum8 / xor8 / xor-of-nibbles over the other bytes plus a constant) and,
for IDs whose remaining bytes still vary a lot (e.g. rolling codes), a
captured sequence that is replayed in a loop.
"""

from __future__ import annotations

import collections
from functools import reduce
from typing import Iterable, Optional

from q50tb.capture.formats import Capture, load_capture
from q50tb.sim.background import BackgroundMessage, ChecksumRule, CounterRule
from q50tb.signals.model import SignalDatabase

def _counter_candidates():
    for width in (8, 4, 3, 2, 1):
        for lsb in range(0, 9 - width):
            yield lsb, width


def _detect_counters(payloads: list[bytes], n: int, skip: set[int]) -> list[CounterRule]:
    """Bit fields that step by a constant every frame and visit every value."""
    rules: list[CounterRule] = []
    for i in range(n):
        if i in skip:
            continue
        used = 0
        for lsb, width in _counter_candidates():
            mask = (1 << width) - 1
            if (mask << lsb) & used:
                continue
            seq = [(p[i] >> lsb) & mask for p in payloads]
            distinct = len(set(seq))
            full = distinct > 32 if width == 8 else distinct == (1 << width)
            if not full:
                continue
            if width == 1:
                # a toggling bit is only a counter if the rest of the byte is quiet;
                # otherwise it is part of a multiplexed pattern (handled later)
                rest = ~(1 << lsb) & 0xFF
                changes = sum((payloads[k + 1][i] & rest) != (payloads[k][i] & rest) for k in range(len(payloads) - 1))
                if changes / max(1, len(payloads) - 1) > 0.05:
                    continue
            diffs = collections.Counter((seq[k + 1] - seq[k]) % (1 << width) for k in range(len(seq) - 1))
            step, cnt = diffs.most_common(1)[0]
            if step != 0 and cnt / max(1, len(seq) - 1) > 0.97:
                rules.append(CounterRule(byte=i, lsb=lsb, width=width, step=step))
                used |= mask << lsb
    return rules


def _residual(p: bytes, counters: list[CounterRule], checksum: Optional[ChecksumRule]) -> bytes:
    b = bytearray(p)
    for c in counters:
        c.apply(b, 0)
    if checksum:
        b[checksum.byte] = 0
    return bytes(b)


def _detect_pattern(payloads: list[bytes], counters, checksum, max_k: int = 8) -> Optional[list[bytes]]:
    """Short repeating payload pattern (e.g. a 2-frame multiplex) once counters are masked."""
    res = [_residual(p, counters, checksum) for p in payloads]
    if len(set(res)) < 2:
        return None
    for k in range(2, max_k + 1):
        hits = sum(res[j] == res[j - k] for j in range(k, len(res)))
        if hits / max(1, len(res) - k) > 0.97:
            # align so the pattern starts at a frame that begins a cycle
            return [payloads[j] for j in range(k)]
    return None


def _detect_checksum(payloads: list[bytes], n: int, skip: set[int]) -> Optional[ChecksumRule]:
    for tgt in reversed(range(n)):
        vals = [p[tgt] for p in payloads]
        if len(set(vals)) < 2:
            continue
        over = [i for i in range(n) if i != tgt]
        for kind in ("sum8", "xor8", "xor_nibbles"):
            consts = collections.Counter()
            for p in payloads:
                if kind == "sum8":
                    consts[(p[tgt] - sum(p[i] for i in over)) & 0xFF] += 1
                elif kind == "xor8":
                    consts[p[tgt] ^ reduce(lambda a, b: a ^ b, (p[i] for i in over), 0)] += 1
                else:
                    x = reduce(lambda a, b: a ^ b, ((p[i] >> 4) ^ (p[i] & 0xF) for i in over), 0)
                    consts[(p[tgt] ^ x) if p[tgt] <= 0xF else -1] += 1
            const, cnt = consts.most_common(1)[0]
            if const >= 0 and cnt / len(payloads) > 0.98:
                return ChecksumRule(kind=kind, byte=tgt, over=over, add=const)
    return None


def build_messages(cap: Capture, interface: Optional[str] = None, bus: str = "main",
                   exclude: Iterable[int] = (), db: Optional[SignalDatabase] = None,
                   min_frames: int = 20, max_period_ms: float = 2000.0,
                   window: Optional[tuple[float, float]] = None) -> list[BackgroundMessage]:
    excl = set(exclude)
    by_id: dict[int, list] = collections.defaultdict(list)
    for f in cap.frames:
        if interface and f.bus != interface:
            continue
        if f.direction == "TX" and interface is None:
            pass
        if window and not (window[0] <= f.timestamp <= window[1]):
            continue
        if f.arbitration_id in excl or f.is_error or f.is_remote:
            continue
        by_id[(f.arbitration_id, f.is_extended)].append(f)
    out: list[BackgroundMessage] = []
    for (cid, ext), frames in sorted(by_id.items()):
        if len(frames) < min_frames:
            continue
        dts = sorted(frames[k + 1].timestamp - frames[k].timestamp for k in range(len(frames) - 1))
        period = dts[len(dts) // 2] * 1000.0 if dts else 100.0
        if period <= 0 or period > max_period_ms:
            continue
        period = _round_period(period)
        dlc = collections.Counter(len(f.data) for f in frames).most_common(1)[0][0]
        payloads = [bytes(f.data) for f in frames if len(f.data) == dlc][:5000]
        checksum = _detect_checksum(payloads, dlc, set())
        counters = _detect_counters(payloads, dlc, {checksum.byte} if checksum else set())
        counter_bytes = {c.byte for c in counters}
        modes = [collections.Counter(p[i] for p in payloads).most_common(1)[0][0] for i in range(dlc)]
        base = max(payloads[len(payloads) // 4: len(payloads) // 4 * 3 or None] or payloads,
                   key=lambda p: sum(p[i] == modes[i] for i in range(dlc)))
        explained = counter_bytes | ({checksum.byte} if checksum else set())
        noisy = [i for i in range(dlc) if i not in explained and len({p[i] for p in payloads}) > 64]
        sequence: list[bytes] = []
        notes = []
        if noisy and len(noisy) >= max(2, dlc // 2):
            sequence = payloads[:256]
            notes.append(f"bytes {noisy} vary like a rolling code; replaying {len(sequence)} captured frames")
            counters, checksum = [], None
        else:
            pattern = _detect_pattern(payloads, counters, checksum)
            if pattern:
                sequence = pattern
                notes.append(f"{len(pattern)}-frame repeating pattern (multiplexed payload)")
        mdef = db.get(bus, cid) if db else None
        name = mdef.name if mdef else f"Unknown 0x{cid:03X}"
        out.append(BackgroundMessage(
            arbitration_id=cid, name=name, bus=bus, period_ms=period, base=base, is_extended=ext,
            counters=counters, checksums=[checksum] if checksum else [], sequence=sequence,
            notes="; ".join(notes)))
    return out


def _round_period(ms: float) -> float:
    for nominal in (10, 20, 40, 50, 60, 100, 200, 500, 1000):
        if abs(ms - nominal) / nominal < 0.08:
            return float(nominal)
    return round(ms, 1)


def build_from_file(path: str, **kw) -> list[BackgroundMessage]:
    return build_messages(load_capture(path), **kw)
