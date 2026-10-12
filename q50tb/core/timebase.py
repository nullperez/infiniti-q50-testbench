"""One monotonic clock for the whole app.

``now()`` is seconds since the app started. ``wall(t)`` converts an app
timestamp to Unix time for exports.
"""

from __future__ import annotations

import time

_T0_MONO = time.monotonic()
_T0_WALL = time.time()


def now() -> float:
    return time.monotonic() - _T0_MONO


def wall(t: float) -> float:
    return _T0_WALL + t


def from_wall(unix_time: float) -> float:
    """Map a Unix timestamp (e.g. a kernel RX timestamp) into the app timebase."""
    return unix_time - _T0_WALL


def mono_to_app(mono: float) -> float:
    return mono - _T0_MONO
