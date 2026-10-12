"""Raw frame log: a bounded ring buffer with sequence numbers so the UI can
fetch only frames it has not seen yet."""

from __future__ import annotations

import threading
from collections import deque
from typing import Iterable, Optional

from q50tb.can.frame import CanFrame


class IdFilter:
    """Comma separated hex IDs.

    * A token as long as the ID text (3 digits standard, 8 extended) or
      written with ``0x`` matches that exact ID: "359", "0x2".
    * A shorter token is a prefix of the ID text: "35" matches 359, 35A, 35D.
    * Leading ``-`` or ``!`` excludes: "-6A8".
    Empty filter matches everything.
    """

    def __init__(self, text: str = ""):
        self.text = text
        self.include: list[tuple[str, bool]] = []   # (token, exact)
        self.exclude: list[tuple[str, bool]] = []
        for tok in text.replace(";", ",").replace(" ", ",").split(","):
            tok = tok.strip().upper()
            if not tok:
                continue
            neg = tok[0] in "-!"
            tok = tok.lstrip("-!")
            exact = tok.startswith("0X")
            if exact:
                tok = tok[2:]
            if not tok or any(c not in "0123456789ABCDEF" for c in tok):
                continue
            (self.exclude if neg else self.include).append((tok, exact))

    @property
    def empty(self) -> bool:
        return not self.include and not self.exclude

    @staticmethod
    def _match(tok: str, exact: bool, id_text: str, value: int) -> bool:
        if exact or len(tok) >= len(id_text):
            return int(tok, 16) == value
        return id_text.startswith(tok)

    def matches_id(self, cid: int, extended: bool = False) -> bool:
        if self.empty:
            return True
        id_text = f"{cid:08X}" if extended else f"{cid:03X}"
        if any(self._match(t, e, id_text, cid) for t, e in self.exclude):
            return False
        if not self.include:
            return True
        return any(self._match(t, e, id_text, cid) for t, e in self.include)

    def matches(self, frame: CanFrame) -> bool:
        return self.matches_id(frame.arbitration_id, frame.is_extended)


class LogBuffer:
    def __init__(self, maxlen: int = 200_000):
        self._buf: deque[CanFrame] = deque(maxlen=maxlen)
        self._lock = threading.Lock()
        self._seq = 0
        self.paused = False
        self.dropped_while_paused = 0

    def set_maxlen(self, n: int) -> None:
        with self._lock:
            self._buf = deque(self._buf, maxlen=max(1000, n))

    def append(self, frame: CanFrame) -> None:
        if self.paused:
            self.dropped_while_paused += 1
            return
        with self._lock:
            self._seq += 1
            frame.seq = self._seq
            self._buf.append(frame)

    def clear(self) -> None:
        with self._lock:
            self._buf.clear()

    @property
    def last_seq(self) -> int:
        return self._seq

    def __len__(self) -> int:
        return len(self._buf)

    def since(self, seq: int, limit: Optional[int] = None) -> list[CanFrame]:
        """Frames with seq > ``seq`` (oldest first)."""
        out = []
        with self._lock:
            for f in reversed(self._buf):      # only walk the new tail, not the whole buffer
                if f.seq <= seq or (limit is not None and len(out) >= limit):
                    break
                out.append(f)
        out.reverse()
        return out

    def snapshot(self) -> list[CanFrame]:
        with self._lock:
            return list(self._buf)

    def filtered(self, id_filter: IdFilter, directions: Iterable[str], buses: Optional[Iterable[str]] = None
                 ) -> list[CanFrame]:
        dirs = set(directions)
        bset = set(buses) if buses else None
        return [f for f in self.snapshot()
                if f.direction in dirs and (bset is None or f.bus in bset) and id_filter.matches(f)]
