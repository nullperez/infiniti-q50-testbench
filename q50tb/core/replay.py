"""Replay one or more captures, each onto its own bus, from a common start.

All tracks share one timeline on one thread, so a main-bus capture and an
AV-bus capture recorded together stay aligned. Each track excludes chosen IDs
(and meter-originated/protected IDs by default) and claims the IDs it sends at
replay priority, which pauses background messages with the same IDs for the
duration of the replay.
"""

from __future__ import annotations

import heapq
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from q50tb.capture.formats import Capture, load_capture
from q50tb.can.frame import CanFrame
from q50tb.core.bus_manager import PRIO_REPLAY, BusManager

OWNER = "replay"


@dataclass
class ReplayTrack:
    name: str
    capture: Optional[Capture] = None
    target_bus: str = "main"
    interfaces: Optional[set[str]] = None      # capture interfaces to use (None = all)
    excluded: set[int] = field(default_factory=set)
    exclude_protected: bool = True
    include_tx: bool = True                     # frames marked TX in the capture
    start_s: float = 0.0
    end_s: Optional[float] = None
    enabled: bool = True
    # runtime
    sent: int = 0
    skipped: int = 0
    position_s: float = 0.0

    def load(self, path: str) -> None:
        self.capture = load_capture(path)
        self.interfaces = None

    def frame_included(self, f: CanFrame, protected: set[int]) -> tuple[bool, str]:
        if self.interfaces and f.bus not in self.interfaces:
            return False, "other interface"
        if f.arbitration_id in self.excluded:
            return False, "excluded"
        if self.exclude_protected and f.arbitration_id in protected:
            return False, "meter-origin/protected"
        if not self.include_tx and f.direction == "TX":
            return False, "TX in capture"
        if f.is_error or f.is_remote:
            return False, "error/remote"
        return True, ""

    def frames_in_window(self) -> list[CanFrame]:
        if not self.capture:
            return []
        end = self.end_s if self.end_s is not None else float("inf")
        return [f for f in self.capture.frames if self.start_s <= f.timestamp <= end]


@dataclass
class ReplayStatus:
    running: bool = False
    paused: bool = False
    elapsed_s: float = 0.0
    duration_s: float = 0.0
    loops: int = 0
    message: str = ""


class ReplayEngine:
    def __init__(self, buses: BusManager, protected_lookup: Callable[[str], set[int]]):
        self.buses = buses
        self.protected_lookup = protected_lookup
        self.tracks: list[ReplayTrack] = [ReplayTrack("Track A", target_bus="main"),
                                          ReplayTrack("Track B", target_bus="av")]
        self.status = ReplayStatus()
        self.speed = 1.0
        self.loop = False
        self.lead_in_s = 0.2
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._pause = threading.Event()
        self.listeners: list[Callable[[], None]] = []

    def start(self) -> None:
        if self.status.running:
            raise RuntimeError("replay already running")
        active = [t for t in self.tracks if t.enabled and t.capture and t.capture.frames]
        if not active:
            raise RuntimeError("no enabled track with a loaded capture")
        for t in active:
            ch = self.buses.get(t.target_bus)
            if ch is None:
                raise RuntimeError(f"{t.name}: bus '{t.target_bus}' is not open")
            if not ch.can_transmit():
                raise RuntimeError(f"{t.name}: bus '{t.target_bus}' is in monitor mode")
        if len({t.target_bus for t in active}) != len(active):
            raise RuntimeError("two tracks target the same bus; give each track its own bus")
        plans = []
        for t in active:
            prot = self.protected_lookup(t.target_bus)
            frames = [f for f in t.frames_in_window() if t.frame_included(f, prot)[0]]
            t.sent = t.skipped = 0
            plans.append((t, frames))
            ids = {f.arbitration_id for f in frames}
            self.buses.channels[t.target_bus].ownership.claim(f"{OWNER}:{t.name}", ids, PRIO_REPLAY)
        longest = max(((fr[-1].timestamp - t.start_s) for t, fr in plans if fr), default=0.0)
        if longest <= 0 and not any(fr for _t, fr in plans):
            for t, _fr in plans:
                self.buses.channels[t.target_bus].ownership.release(f"{OWNER}:{t.name}")
            raise RuntimeError("nothing to replay: every frame is excluded or outside the window")
        self.status = ReplayStatus(running=True, duration_s=longest / max(self.speed, 1e-6))
        self._stop.clear()
        self._pause.clear()
        self._thread = threading.Thread(target=self._run, args=(plans,), name="replay", daemon=True)
        self._thread.start()
        self._changed()

    def stop(self) -> None:
        self._stop.set()
        self._pause.clear()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)
        self._release()

    def pause(self, on: bool) -> None:
        if on:
            self._pause.set()
        else:
            self._pause.clear()
        self.status.paused = on
        self._changed()

    def _release(self) -> None:
        for t in self.tracks:
            for ch in self.buses.channels.values():
                ch.ownership.release(f"{OWNER}:{t.name}")
        if self.status.running:
            self.status.running = False
        self._changed()

    def _run(self, plans) -> None:
        try:
            while True:
                if not self._play_once(plans):
                    self.status.message = "stopped"
                    return
                self.status.loops += 1
                if not self.loop:
                    self.status.message = "finished"
                    return
        except Exception as exc:
            self.status.message = f"error: {exc!r}"
        finally:
            self._release()

    def _play_once(self, plans) -> bool:
        heap = []
        for ti, (t, frames) in enumerate(plans):
            if frames:
                heap.append((frames[0].timestamp - t.start_s, ti, 0))
        heapq.heapify(heap)
        t0 = time.perf_counter() + self.lead_in_s
        paused_total = 0.0
        while heap:
            if self._stop.is_set():
                return False
            if self._pause.is_set():
                ps = time.perf_counter()
                while self._pause.is_set() and not self._stop.is_set():
                    time.sleep(0.02)
                paused_total += time.perf_counter() - ps
            rel, ti, idx = heap[0]
            due = t0 + paused_total + rel / max(self.speed, 1e-6)
            delay = due - time.perf_counter()
            if delay > 0.002:
                time.sleep(min(delay - 0.001, 0.05))
                continue
            while delay > 0:
                delay = due - time.perf_counter()
            heapq.heappop(heap)
            track, frames = plans[ti]
            src = frames[idx]
            frame = CanFrame(src.arbitration_id, bytes(src.data), is_extended=src.is_extended, is_fd=src.is_fd,
                             bitrate_switch=src.bitrate_switch, bus=track.target_bus)
            ch = self.buses.channels.get(track.target_bus)
            if ch is not None and ch.send(frame, f"{OWNER}:{track.name}"):
                track.sent += 1
            else:
                track.skipped += 1
            track.position_s = src.timestamp
            self.status.elapsed_s = rel / max(self.speed, 1e-6)
            if idx + 1 < len(frames):
                heapq.heappush(heap, (frames[idx + 1].timestamp - track.start_s, ti, idx + 1))
        return True

    def _changed(self) -> None:
        for fn in list(self.listeners):
            try:
                fn()
            except Exception:
                pass
