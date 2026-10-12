"""Shared encoders for AV records."""

from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP


def nibbles_to_bytes(nibs: list[int]) -> bytes:
    if len(nibs) % 2:
        nibs = nibs + [0xF]
    return bytes((nibs[i] << 4) | nibs[i + 1] for i in range(0, len(nibs), 2))


def bytes_to_nibbles(b: bytes) -> list[int]:
    out = []
    for x in b:
        out += [x >> 4, x & 0xF]
    return out


def _digits(value: float, decimals: int) -> tuple[str, str]:
    q = Decimal(str(value)).quantize(Decimal(1) if decimals == 0 else Decimal("0.1"), rounding=ROUND_HALF_UP)
    text = f"{q:f}"
    whole, _, frac = text.partition(".")
    return whole.lstrip("-") or "0", frac


def encode_freq3(value: float | None, decimal: bool) -> bytes:
    """Radio frequency / channel field (3 bytes): 4 integer nibbles right-aligned
    (F = blank), decimal flag nibble, tenths nibble (F = none).
    103.3 -> F1 03 13, 96.7 -> FF 96 17, 530 -> F5 30 0F, None -> FF FF 0F."""
    if value is None:
        return bytes([0xFF, 0xFF, 0x0F])
    whole, frac = _digits(value, 1 if decimal else 0)
    whole = whole[-4:]
    nibs = [0xF] * (4 - len(whole)) + [int(c) for c in whole]
    nibs += [1, int(frac[0])] if decimal else [0, 0xF]
    return nibbles_to_bytes(nibs)


def decode_freq3(b: bytes) -> str:
    n = bytes_to_nibbles(b[:3])
    whole = "".join(str(d) for d in n[:4] if d <= 9)
    if n[4] == 1 and n[5] <= 9:
        return f"{whole or '0'}.{n[5]}"
    return whole or "—"


def encode_distance4(value: float | None, decimal: bool) -> bytes:
    """Navigation distance (4 bytes): 5 integer nibbles right-aligned (F blank),
    decimal flag, tenths, F. 8.9 -> FF FF 81 9F, 500 -> FF 50 00 FF, None -> FF FF F0 FF."""
    if value is None:
        return bytes([0xFF, 0xFF, 0xF0, 0xFF])
    whole, frac = _digits(value, 1 if decimal else 0)
    whole = whole[-5:]
    nibs = [0xF] * (5 - len(whole)) + [int(c) for c in whole]
    nibs += [1, int(frac[0]), 0xF] if decimal else [0, 0xF, 0xF]
    return nibbles_to_bytes(nibs)


def decode_distance4(b: bytes) -> str:
    n = bytes_to_nibbles(b[:4])
    whole = "".join(str(d) for d in n[:5] if d <= 9)
    if not whole:
        return "—"
    if n[5] == 1 and n[6] <= 9:
        return f"{whole}.{n[6]}"
    return whole


def utf16_field(text: str, max_chars: int = 32) -> bytes:
    """Length byte + UTF-16LE text (length counts bytes)."""
    raw = (text or "")[:max_chars].encode("utf-16-le")
    return bytes([len(raw)]) + raw


def bcd(v: int) -> int:
    return ((v // 10) << 4) | (v % 10)


def bcd_blank_tens(v: int) -> int:
    """Hour with a blank tens digit below 10 (seen in 0x603 records: 9 -> F9)."""
    return (0xF0 | v) if v < 10 else bcd(v)


def parse_hex(text: str) -> bytes:
    return bytes.fromhex(text.replace(",", " ").replace("0x", "").replace("0X", ""))
