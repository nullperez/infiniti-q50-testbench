"""Navigation records on 0x629 and a simple route player.

Record layout (captures navboth / navprompt / drivework / cap3):

    20 0D <state> <maneuver> <bar> <progress%> 00 00 <distance x4> <unit> <heading> 06 <len> <street UTF-16LE>

* state: 00 idle (compass), 02/03 guidance far, 10 approach start, 11 approaching
  (progress bar), 12 at maneuver. The first non-idle state is sent twice.
* bar: 01 while the approach bar is shown (states 10-12).
* progress: 0..100 (0x64), only shown while approaching.
* distance: nibble BCD, e.g. FF FF 81 9F = 8.9, FF 50 00 FF = 500; unit 03 mi, 08 ft.
* heading: 0x01..0x20 (32 steps, wraps 0x20 -> 0x01). Which value is north is
  a candidate (0x01 assumed).
"""

from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

from q50tb.av.services.common import decode_distance4, encode_distance4, utf16_field

REGISTRY = os.path.join(os.path.dirname(__file__), "..", "..", "..", "registry", "nav_maneuvers.json")

COMPASS16 = ["N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE", "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW"]
UNIT_MI, UNIT_FT = 0x03, 0x08


def heading_from_compass(name: str) -> int:
    return 1 + 2 * COMPASS16.index(name.upper())


def compass_from_heading(h: int) -> str:
    return COMPASS16[((max(1, h) - 1) // 2) % 16]


def load_registry(path: str = REGISTRY) -> dict:
    with open(os.path.abspath(path), encoding="utf-8") as fh:
        return json.load(fh)


@dataclass
class NavState:
    state: int = 0x00
    maneuver: int = 0
    street: str = ""
    distance: Optional[float] = None
    unit: int = UNIT_MI
    progress: int = 0
    show_bar: Optional[bool] = None      # None = automatic (states 0x10-0x12)
    heading: int = 0x01

    def record(self) -> bytes:
        bar = self.show_bar if self.show_bar is not None else self.state in (0x10, 0x11, 0x12)
        decimal = self.unit == UNIT_MI
        dist = encode_distance4(self.distance, decimal) if self.state != 0x00 else bytes([0xFF, 0xFF, 0xF0, 0xFF])
        unit = self.unit if self.state != 0x00 else 0x00
        hdr = bytes([0x20, 0x0D, self.state & 0xFF, self.maneuver & 0xFF, 0x01 if bar else 0x00,
                     max(0, min(100, int(self.progress))) if bar else 0x00, 0x00, 0x00])
        return hdr + dist + bytes([unit, self.heading & 0xFF, 0x06]) + utf16_field(self.street)


def describe_record(b: bytes) -> str:
    if len(b) < 16:
        return b.hex(" ")
    n = b[15]
    street = b[16:16 + n].decode("utf-16-le", "replace")
    unit = {0: "", 3: "mi", 8: "ft"}.get(b[12], f"u{b[12]:02X}")
    return (f"state {b[2]:02X} maneuver {b[3]:02X} bar {b[4]} prog {b[5]}% "
            f"dist {decode_distance4(b[8:12])}{unit} heading {b[13]:02X}({compass_from_heading(b[13])}) {street!r}")


@dataclass
class RouteStep:
    maneuver: int
    street: str
    distance: float               # starting distance for this step
    unit: int = UNIT_MI
    heading: int = 0x01
    duration_s: float = 20.0      # time to count the distance down to zero
    approach_at: float = 0.3      # start the approach bar at this distance (miles; ft unit uses feet)
    hold_s: float = 2.0           # time spent in "at maneuver" before the next step

    def to_json(self) -> dict:
        return asdict(self)


@dataclass
class RoutePlayerState:
    running: bool = False
    step_index: int = -1
    phase: str = ""
    loop: bool = False
    steps: list[RouteStep] = field(default_factory=list)


class NavigationService:
    def __init__(self, host):
        self.host = host
        self.state = NavState()
        self.registry = load_registry()
        self._sent_nonidle = False
        self.player = RoutePlayerState()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.on_player_change: Optional[Callable[[], None]] = None
        self.update_interval = 1.0

    def maneuvers(self) -> list[dict]:
        return self.registry.get("maneuvers", [])

    def states(self) -> list[dict]:
        return self.registry.get("states", [])

    def reload(self) -> None:
        self.registry = load_registry()

    def update(self, **fields) -> None:
        for k, v in fields.items():
            if not hasattr(self.state, k):
                raise KeyError(f"unknown nav field {k}")
            setattr(self.state, k, v)
        self.push()

    def push(self) -> None:
        rec = self.state.record()
        first_nonidle = self.state.state != 0x00 and not self._sent_nonidle
        self.host.send("nav", rec, label="nav")
        if first_nonidle:
            self.host.send("nav", rec, label="nav (first non-idle repeat)")
        self._sent_nonidle = self.state.state != 0x00

    def idle(self, heading: Optional[int] = None) -> None:
        if heading is not None:
            self.state.heading = heading
        self.state.state = 0x00
        self.state.maneuver = 0
        self.state.street = ""
        self.state.progress = 0
        self.push()

    # ── route player ────────────────────────────────────────────────
    def play(self, steps: list[RouteStep], loop: bool = False) -> None:
        self.stop()
        self.player = RoutePlayerState(running=True, loop=loop, steps=list(steps))
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="nav-route", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
        self._thread = None
        if self.player.running:
            self.player.running = False
            self._player_changed()

    def _sleep(self, s: float) -> bool:
        return not self._stop.wait(s)

    def _player_changed(self) -> None:
        if self.on_player_change:
            try:
                self.on_player_change()
            except Exception:
                pass

    def _run(self) -> None:
        try:
            while True:
                for i, step in enumerate(self.player.steps):
                    self.player.step_index = i
                    if not self._play_step(step):
                        return
                if not self.player.loop:
                    break
            self.player.phase = "arrived"
            self._player_changed()
            self.idle(self.player.steps[-1].heading if self.player.steps else None)
        finally:
            self.player.running = False
            self._player_changed()

    def _play_step(self, step: RouteStep) -> bool:
        st = self.state
        st.maneuver, st.street, st.unit, st.heading = step.maneuver, step.street, step.unit, step.heading
        st.progress = 0
        total = max(0.1, step.distance)
        approach = min(step.approach_at, total)
        rate = total / max(0.5, step.duration_s)
        dist = total
        approach_started = False
        self.player.phase = "far"
        self._player_changed()
        st.state, st.distance = 0x02, dist
        self.push()
        last = time.monotonic()
        while dist > 0:
            if not self._sleep(self.update_interval):
                return False
            now = time.monotonic()
            dist = max(0.0, dist - rate * (now - last))
            last = now
            st.distance = round(dist, 1) if st.unit == UNIT_MI else round(dist / 10) * 10
            if dist <= approach:
                if not approach_started:
                    approach_started = True
                    self.player.phase = "approach"
                    self._player_changed()
                    st.state = 0x10
                    st.progress = 0
                    self.push()
                    st.state = 0x11
                st.progress = int(round(100 * (1 - dist / approach))) if approach > 0 else 100
            self.push()
        st.state, st.progress, st.distance = 0x12, 100, 0
        self.player.phase = "at maneuver"
        self._player_changed()
        self.push()
        return self._sleep(step.hold_s)
