"""Bench I/O service: owns the ESP32 serial link and runs every command on a
worker thread. Results come back through listeners (UI) or ``wait=True``
(control server / MCP)."""

from __future__ import annotations

import queue
import threading
from collections import deque
from concurrent.futures import Future
from typing import Callable, Optional

from q50tb.core import timebase
from q50tb.hw.esp32_bench import (BUTTON, CHANNELS, INPUT, POWER, BenchError, Esp32Bench,
                                  find_bench_candidates, list_serial_ports)

# What the Hardware page shows, in the order requested for the bench.
UI_CONTROLS = [
    ("BPLUS", "B+ (battery)", "check"),
    ("IG", "IG (ignition)", "check"),
    ("ACC", "ACC (accessory)", "check"),
    ("AIRBAG", "Air bag signal", "check"),
    ("TRIP_RESET", "Trip / Reset", "button"),
    ("STSW_B_LEFT", "Steering switch LEFT", "button"),
    ("STSW_B_RIGHT", "Steering switch RIGHT", "button"),
    ("DRVR_BELT", "Driver seat belt", "check"),
    ("PSGR_BELT", "Passenger seat belt", "check"),
    ("WASHER_FLUID", "Washer fluid level", "check"),
    ("BRAKE_FLUID", "Brake fluid level", "check"),
    ("PKB", "Parking brake", "check"),
    ("PADDLE_UP", "Paddle shift UP", "button"),
    ("PADDLE_DOWN", "Paddle shift DOWN", "button"),
    ("BRIGHT_UP", "Brightness UP", "button"),
    ("BRIGHT_DOWN", "Brightness DOWN", "button"),
    ("HEADLIGHT_LH", "LH headlight signal", "check"),
    ("HEADLIGHT_RH", "RH headlight signal", "check"),
    ("ALTERNATOR", "Alternator signal", "check"),
    ("SECURITY", "Security signal", "check"),
]
KIND_OF = {name: kind for name, kind, _ in CHANNELS}


class BenchIO:
    def __init__(self):
        self.client = Esp32Bench()
        self.client.add_listener(self._on_client_event)
        self._q: "queue.Queue[tuple[Callable, Future]]" = queue.Queue()
        self._thread = threading.Thread(target=self._worker, name="bench-io", daemon=True)
        self._thread.start()
        self.listeners: list[Callable[[str, tuple], None]] = []
        self.log: deque = deque(maxlen=500)
        self.press_ms = 200
        self.last_error = ""

    # ── public API (all non-blocking unless wait=True) ─────────────
    @property
    def connected(self) -> bool:
        return self.client.connected

    @property
    def port(self) -> Optional[str]:
        return self.client.port

    def ports(self) -> list[str]:
        return list_serial_ports()

    def candidates(self) -> list[str]:
        return find_bench_candidates()

    def states(self) -> dict:
        return self.client.states()

    def channel_info(self) -> dict:
        return dict(self.client.channels)

    def connect(self, port: str, wait: bool = False, timeout: float = 6.0):
        return self._run(lambda: self.client.connect(port), wait, timeout, f"connect {port}")

    def disconnect(self, wait: bool = False):
        return self._run(lambda: self.client.disconnect(), wait, 3.0, "disconnect")

    def set(self, name: str, on: bool, wait: bool = False):
        if KIND_OF.get(name) == BUTTON:
            raise BenchError(f"{name} is a momentary button; use press()")
        return self._run(lambda: self.client.set(name, on), wait, 3.0, f"SET {name} {int(on)}")

    def press(self, name: str, ms: Optional[int] = None, wait: bool = False):
        ms = int(ms or self.press_ms)
        return self._run(lambda: self.client.press(name, ms), wait, 3.0, f"PRESS {name} {ms}")

    def all_off(self, wait: bool = False):
        return self._run(lambda: self.client.all_off(), wait, 3.0, "ALLOFF")

    def power_cycle(self, off_s: float = 5.0, wait: bool = False):
        """Full meter reboot: B+ and IG (and ACC) off for ``off_s``, then back on."""
        def run():
            before = self.client.states()
            for n in ("IG", "ACC", "BPLUS"):
                if self.client.channels.get(n) and self.client.channels[n].wired:
                    self.client.set(n, False)
            threading.Event().wait(off_s)
            for n in ("BPLUS", "IG", "ACC"):
                info = self.client.channels.get(n)
                if info and info.wired and before.get(n, n != "ACC"):
                    self.client.set(n, True)
        return self._run(run, wait, off_s + 6.0, f"power cycle {off_s:.0f}s")

    def refresh(self, wait: bool = False):
        return self._run(lambda: self.client.refresh_status(), wait, 3.0, "STATUS")

    # ── internals ───────────────────────────────────────────────────
    def _run(self, fn: Callable, wait: bool, timeout: float, label: str):
        fut: Future = Future()
        fut.label = label  # type: ignore[attr-defined]
        self._q.put((fn, fut))
        if wait:
            return fut.result(timeout=timeout)
        return fut

    def _worker(self) -> None:
        while True:
            fn, fut = self._q.get()
            label = getattr(fut, "label", "")
            try:
                res = fn()
                fut.set_result(res)
                self._log("info", label)
            except Exception as exc:
                self.last_error = f"{label}: {exc}"
                self._log("error", self.last_error)
                fut.set_exception(exc)
                self._emit("error", (self.last_error,))

    def _log(self, level: str, text: str) -> None:
        self.log.append((timebase.now(), level, text))

    def _on_client_event(self, kind: str, payload: tuple) -> None:
        if kind == "log":
            self.log.append((timebase.now(), "device", payload[0]))
        elif kind == "ready":
            self._log("warning", "EVT READY: ESP32 rebooted with all outputs OFF (meter lost power)")
        elif kind == "disconnected":
            self._log("warning", f"disconnected: {payload[0]}")
        self._emit(kind, payload)

    def _emit(self, kind: str, payload: tuple) -> None:
        for fn in list(self.listeners):
            try:
                fn(kind, payload)
            except Exception:
                pass
