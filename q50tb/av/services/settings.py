"""Meter settings on 0x628/0x6A8: page image, preferences, factory reset, alerts.

Requests are ``C0 <item>1`` (read) and ``C0 <item>0 <value>`` (write); the
meter answers ``C0 <item>C <status> <value>`` with status 00 = ok, FF = not
ready. A write succeeds only when the returned value equals the request
(handoff rule 3) - an ACK or a sent frame is not success.

Page image (item 2, 9 bytes S0..S8): always read the live image, change one
mask, keep every other bit. Confirmed masks below.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

from q50tb.av.protocol import OP_RESP, AvMessage
from q50tb.core import timebase

PAGES = {  # name: (byte, mask)
    "Range": (0, 0x01),
    "Trip Distance": (0, 0x04),
    "Average Speed": (0, 0x10),
    "Driving Aids": (0, 0x40),
    "Tire Pressure": (1, 0x01),
    "Fuel Economy": (1, 0x10),
    "Navigation": (2, 0x10),
    "Audio": (2, 0x40),
    "Blank Page": (3, 0x01),
    "Chassis Control": (4, 0x01),
}

ITEM_CAPS, ITEM_PAGES, ITEM_ALERTS, ITEM_UNITS, ITEM_LANGUAGE, ITEM_EFFECT = 0x0, 0x2, 0x8, 0xA, 0xC, 0xE

UNITS = {0x01: "kPa", 0x02: "PSI", 0x03: "bar"}                     # confirmed by kpa_psi_AVBUS capture
LANGUAGES = {0x01: "English", 0x02: "French", 0x03: "Spanish"}   # all three confirmed on the bench 2026-10-12


@dataclass
class ItemState:
    value: Optional[bytes] = None      # last value reported by the meter
    status: Optional[int] = None
    t: Optional[float] = None
    pending: Optional[bytes] = None    # value we asked to write
    last_result: str = ""


@dataclass
class SettingsState:
    items: dict[int, ItemState] = field(default_factory=lambda: {i: ItemState() for i in
                                                                 (0, 2, 8, 0xA, 0xC, 0xE)})
    reset_result: str = ""


@dataclass
class _Request:
    key: int                      # settings item, or 0x100 for factory reset
    record: bytes
    label: str
    on_response: Optional[Callable[[Optional[ItemState]], None]]   # None arg = timeout / send failure


class SettingsService:
    """Requests are serialised like the real HU: the next request goes out only
    after the meter answered the previous one (or it timed out)."""

    def __init__(self, host):
        self.host = host
        self.state = SettingsState()
        self._lock = threading.Lock()
        self._queue: list[_Request] = []
        self._inflight: Optional[_Request] = None
        self._timer = None
        self.response_timeout = 1.5
        self.listeners: list[Callable[[], None]] = []
        self._page_jobs: list[tuple[dict[str, bool], Optional[Callable[[bool, str], None]]]] = []
        self._page_busy = False

    # ── request queue ───────────────────────────────────────────────
    def _submit(self, req: _Request) -> None:
        with self._lock:
            self._queue.append(req)
        self._pump()

    def _pump(self) -> None:
        with self._lock:
            if self._inflight is not None or not self._queue:
                return
            req = self._inflight = self._queue.pop(0)
        sent = self.host.send("settings", req.record, label=req.label,
                              on_done=lambda ok, why, r=req: (not ok) and self._resolve(r, None))
        if sent:
            self._timer = self.host.scheduler.after(self.response_timeout, lambda r=req: self._resolve(r, None),
                                                    name="settings-timeout")
        else:
            self._resolve(req, None)

    def _resolve(self, req: _Request, st: Optional[ItemState]) -> None:
        with self._lock:
            if self._inflight is not req:
                return
            self._inflight = None
            if self._timer is not None:
                self._timer.cancel()
                self._timer = None
        if req.on_response:
            try:
                req.on_response(st)
            except Exception:
                pass
        self._pump()

    def clear_queue(self) -> None:
        with self._lock:
            self._queue.clear()
            self._inflight = None

    # ── incoming ────────────────────────────────────────────────────
    def on_record(self, msg: AvMessage) -> None:
        if msg.group == 0xC0 and msg.op == OP_RESP and msg.payload:
            it = self.state.items.setdefault(msg.item, ItemState())
            it.status = msg.payload[0]
            if it.status == 0x00:
                it.value = bytes(msg.payload[1:])
            it.t = timebase.now()
            self._changed()
            inflight = self._inflight
            if inflight is not None and inflight.key == msg.item:
                self._resolve(inflight, it)
        elif msg.group == 0xC4 and msg.item == 0 and msg.op == OP_RESP:
            self.state.reset_result = f"meter acknowledged factory reset ({timebase.now():.1f}s)"
            self._changed()
            inflight = self._inflight
            if inflight is not None and inflight.key == 0x100:
                self._resolve(inflight, ItemState(value=b"", status=0))

    # ── requests ────────────────────────────────────────────────────
    def read(self, item: int, done: Optional[Callable[[ItemState], None]] = None) -> None:
        def on_response(st: Optional[ItemState]):
            it = self.state.items.setdefault(item, ItemState())
            if st is None:
                it.last_result = "read timeout: no response from meter"
                st = ItemState(status=None)
            elif st.status != 0:
                it.last_result = f"read: meter status {st.status:02X}"
            else:
                it.last_result = "read ok"
            self._changed()
            if done:
                done(st)
        self._submit(_Request(item, bytes([0xC0, (item << 4) | 0x1]), f"read item {item:X}", on_response))

    def read_all(self) -> None:
        for item in (ITEM_CAPS, ITEM_LANGUAGE, ITEM_PAGES, ITEM_ALERTS, ITEM_UNITS, ITEM_EFFECT):
            self.read(item)

    def write(self, item: int, value: bytes, done: Optional[Callable[[bool, str], None]] = None,
              verify: Optional[Callable[[bytes, bytes], bool]] = None) -> None:
        it = self.state.items.setdefault(item, ItemState())
        it.pending = bytes(value)
        check = verify or (lambda req, got: got == req)

        def on_response(st: Optional[ItemState]):
            if st is None:
                ok, res = False, "timeout: no response from meter"
            elif st.status != 0:
                ok, res = False, f"rejected: meter status {st.status:02X} (not ready)"
            elif st.value is None:
                ok, res = False, "no value returned"
            else:
                ok = check(value, st.value)
                res = "confirmed" if ok else f"rejected: meter kept {st.value.hex(' ')}"
            it.last_result = res
            it.pending = None
            self._changed()
            if done:
                done(ok, res)
        self._submit(_Request(item, bytes([0xC0, (item << 4) | 0x0]) + bytes(value), f"write item {item:X}",
                              on_response))

    # ── page image ──────────────────────────────────────────────────
    @property
    def page_image(self) -> Optional[bytes]:
        return self.state.items[ITEM_PAGES].value

    def page_enabled(self, name: str) -> Optional[bool]:
        img = self.page_image
        if img is None:
            return None
        b, m = PAGES[name]
        return bool(img[b] & m) if b < len(img) else None

    def set_page(self, name: str, on: bool, done: Optional[Callable[[bool, str], None]] = None) -> None:
        """Read the live image, flip one mask, write it, and require the exact echo."""
        self.set_pages({name: on}, done)

    def set_pages(self, changes: dict[str, bool], done: Optional[Callable[[bool, str], None]] = None) -> None:
        """Several masks in one read-modify-write. Requests made while a cycle is running are
        merged into the next cycle, so quick successive calls cannot overwrite each other's change
        (each write would otherwise carry the image read before the previous write)."""
        for name in changes:
            PAGES[name]                      # KeyError for an unknown page, before anything is queued
        with self._lock:
            self._page_jobs.append((dict(changes), done))
            if self._page_busy:
                return
            self._page_busy = True
        self._page_cycle()

    def _page_cycle(self) -> None:
        with self._lock:
            jobs, self._page_jobs = self._page_jobs, []
            if not jobs:
                self._page_busy = False
                return

        def finish(ok: bool, why: str) -> None:
            for _changes, d in jobs:
                if d:
                    try:
                        d(ok, why)
                    except Exception:
                        pass
            self._page_cycle()

        def with_image(st: ItemState):
            if st.status != 0 or st.value is None:
                finish(False, "page image not readable (session not ready?)")
                return
            img = bytearray(st.value)
            for changes, _d in jobs:         # in request order: a later request for a page wins
                for name, on in changes.items():
                    b, m = PAGES[name]
                    if b < len(img):
                        img[b] = (img[b] | m) if on else (img[b] & ~m & 0xFF)
            if bytes(img) == st.value:
                finish(True, "already in requested state")
                return
            self.write(ITEM_PAGES, bytes(img), finish)
        self.read(ITEM_PAGES, with_image)

    # ── preferences ─────────────────────────────────────────────────
    def set_units(self, code: int, done=None) -> None:
        self.write(ITEM_UNITS, bytes([0x02, code, 0x00]), done,
                   verify=lambda req, got: len(got) >= 2 and got[1] == code)

    def set_language(self, code: int, done=None) -> None:
        live = self.state.items[ITEM_LANGUAGE].value
        value = bytearray(live) if live and len(live) >= 2 else bytearray([0x01, code, 0x00, 0x00, 0x00])
        value[1] = code
        self.write(ITEM_LANGUAGE, bytes(value), done, verify=lambda req, got: len(got) >= 2 and got[1] == code)

    def set_alerts(self, outside_temp: bool, nav_alert: Optional[bool] = None, done=None) -> None:
        """Item 8 = [byte0 candidate outside-temp alert, byte1 candidate nav alert]."""
        live = self.state.items[ITEM_ALERTS].value or b"\x01\x00"
        b1 = live[1] if nav_alert is None and len(live) > 1 else int(bool(nav_alert))
        self.write(ITEM_ALERTS, bytes([int(outside_temp), b1]), done)

    def set_display_effect(self, on: bool, done=None) -> None:
        self.write(ITEM_EFFECT, bytes([int(on)]), done)

    def factory_reset(self, done: Optional[Callable[[bool, str], None]] = None) -> None:
        """C4 00 -> meter C4 0C. Callers must have an explicit user confirmation."""
        def on_response(st: Optional[ItemState]):
            ok = st is not None
            self.state.reset_result = "acknowledged (C4 0C)" if ok else "timeout: no C4 0C from meter"
            self._changed()
            if done:
                done(ok, self.state.reset_result)
        self._submit(_Request(0x100, bytes([0xC4, 0x00]), "FACTORY RESET", on_response))

    def driver_break_alert(self) -> None:
        self.host.send("settings", bytes([0xC4, 0x2D]), label="driver break alert")

    def _changed(self) -> None:
        for fn in list(self.listeners):
            try:
                fn()
            except Exception:
                pass
