"""Monotonic-deadline scheduler on one worker thread.

Periodic jobs keep their phase (next = previous deadline + period) so a
100 ms message stays at 100 ms on average; if the thread falls more than one
period behind, the job re-anchors to now instead of bursting.
"""

from __future__ import annotations

import heapq
import sys
import itertools
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

Callback = Callable[[], None]

_timer_res_set = False


def _windows_timer_resolution() -> None:
    """Ask Windows for 1 ms timer resolution (default 15.6 ms jitters 10 ms frames)."""
    global _timer_res_set
    if _timer_res_set or not sys.platform.startswith("win"):
        return
    try:
        import ctypes
        ctypes.WinDLL("winmm").timeBeginPeriod(1)
        _timer_res_set = True
    except Exception:
        pass


@dataclass(order=True)
class _Entry:
    deadline: float
    seq: int
    job: "Job" = field(compare=False)


class Job:
    def __init__(self, scheduler: "Scheduler", fn: Callback, period: Optional[float], name: str):
        self._sched = scheduler
        self.fn = fn
        self.period = period
        self.name = name
        self.cancelled = False
        self.runs = 0
        self.errors = 0
        self.last_error = ""

    def cancel(self) -> None:
        self.cancelled = True

    def set_period(self, period: float) -> None:
        self.period = max(0.001, period)


class Scheduler:
    def __init__(self, name: str = "scheduler", on_error: Optional[Callable[[str, str], None]] = None):
        self._heap: list[_Entry] = []
        self._cv = threading.Condition()
        self._seq = itertools.count()
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._name = name
        self._on_error = on_error

    def start(self) -> None:
        if self._running:
            return
        _windows_timer_resolution()
        self._running = True
        self._thread = threading.Thread(target=self._loop, name=self._name, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        with self._cv:
            self._running = False
            self._cv.notify_all()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2)

    def every(self, period: float, fn: Callback, name: str = "", phase: float = 0.0) -> Job:
        job = Job(self, fn, max(0.001, period), name)
        self._push(time.monotonic() + phase, job)
        return job

    def after(self, delay: float, fn: Callback, name: str = "") -> Job:
        job = Job(self, fn, None, name)
        self._push(time.monotonic() + max(0.0, delay), job)
        return job

    def _push(self, deadline: float, job: Job) -> None:
        with self._cv:
            heapq.heappush(self._heap, _Entry(deadline, next(self._seq), job))
            self._cv.notify()

    def _loop(self) -> None:
        while True:
            with self._cv:
                while self._running and (not self._heap or self._heap[0].deadline > time.monotonic()):
                    timeout = None if not self._heap else self._heap[0].deadline - time.monotonic()
                    if timeout is not None and timeout <= 0:
                        break
                    # cap the wait so cancelled/new jobs are noticed promptly
                    self._cv.wait(timeout=min(timeout, 0.05) if timeout is not None else 0.05)
                if not self._running:
                    return
                entry = heapq.heappop(self._heap)
            job = entry.job
            if job.cancelled:
                continue
            try:
                job.fn()
                job.runs += 1
            except Exception as exc:
                job.errors += 1
                job.last_error = repr(exc)
                if self._on_error:
                    try:
                        self._on_error("error", f"job {job.name or job.fn}: {exc!r}")
                    except Exception:
                        pass
            if job.period is not None and not job.cancelled:
                nxt = entry.deadline + job.period
                now = time.monotonic()
                if nxt < now - job.period:
                    nxt = now + job.period
                self._push(nxt, job)
