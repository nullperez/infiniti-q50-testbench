"""Adapter abstraction: one physical/virtual CAN channel.

An adapter only moves frames. Safety policy (monitor mode, protected IDs,
TX ownership) lives one layer up in ``core.bus_manager`` so every backend
gets the same rules.
"""

from __future__ import annotations

import abc
import threading
from dataclasses import dataclass, field
from typing import Callable, Optional

from q50tb.can.frame import CanFrame

RxCallback = Callable[[CanFrame], None]
StatusCallback = Callable[[str, str], None]  # (level, message)


class AdapterError(RuntimeError):
    pass


class ListenOnlyUnsupported(AdapterError):
    """The hardware/driver cannot guarantee a silent (no-ACK) receiver."""


@dataclass(frozen=True)
class AdapterInfo:
    backend: str                 # "kvaser", "socketcan", "virtual", "pythoncan"
    channel: str                 # backend-specific channel id ("0", "can0", ...)
    name: str                    # human label
    serial: str = ""
    supports_listen_only: Optional[bool] = None   # None = unknown
    supports_fd: bool = False
    is_virtual: bool = False
    extra: tuple = ()            # backend-specific key/value pairs

    @property
    def key(self) -> str:
        return f"{self.backend}:{self.channel}"

    def describe(self) -> str:
        lo = {True: "listen-only", False: "no listen-only", None: "listen-only ?"}[self.supports_listen_only]
        fd = "FD" if self.supports_fd else "classic"
        virt = " virtual" if self.is_virtual else ""
        return f"{self.name} [{self.key}] ({fd}, {lo}{virt})"


@dataclass
class BusConfig:
    bitrate: int = 500_000
    fd: bool = False
    data_bitrate: int = 2_000_000
    listen_only: bool = False
    # socketcan only: try to (re)configure the link with `ip` (needs privileges)
    configure_link: bool = False
    extra: dict = field(default_factory=dict)


@dataclass
class AdapterStats:
    rx: int = 0
    tx: int = 0
    tx_errors: int = 0
    rx_errors: int = 0
    bus_state: str = "unknown"
    last_error: str = ""


class Adapter(abc.ABC):
    """Base class. Subclasses implement ``_open``, ``_close``, ``_send``
    and ``_read(timeout)``; this class owns the RX thread."""

    def __init__(self, info: AdapterInfo):
        self.info = info
        self.config: Optional[BusConfig] = None
        self.stats = AdapterStats()
        self._rx_cb: Optional[RxCallback] = None
        self._status_cb: Optional[StatusCallback] = None
        self._thread: Optional[threading.Thread] = None
        self._running = False
        self._send_lock = threading.Lock()

    # ── public ──────────────────────────────────────────────────────
    @property
    def is_open(self) -> bool:
        return self._running

    def open(self, config: BusConfig, on_rx: RxCallback,
             on_status: Optional[StatusCallback] = None) -> None:
        if self._running:
            raise AdapterError(f"{self.info.key} already open")
        self.config = config
        self._rx_cb = on_rx
        self._status_cb = on_status
        self._open(config)
        self._running = True
        self._thread = threading.Thread(target=self._rx_loop, name=f"rx-{self.info.key}", daemon=True)
        self._thread.start()

    def close(self) -> None:
        self._running = False
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)
        self._thread = None
        try:
            self._close()
        except Exception as exc:  # closing must never raise into the UI
            self._status("warning", f"close error: {exc}")

    def send(self, frame: CanFrame) -> None:
        if not self._running:
            raise AdapterError(f"{self.info.key} is not open")
        if self.config and self.config.listen_only:
            # Last line of defence; BusChannel already refuses monitor-mode TX.
            raise AdapterError("adapter opened listen-only; transmit refused")
        with self._send_lock:
            self._send(frame)
        self.stats.tx += 1

    def bus_state(self) -> str:
        return self.stats.bus_state

    # ── subclass hooks ──────────────────────────────────────────────
    @abc.abstractmethod
    def _open(self, config: BusConfig) -> None: ...

    @abc.abstractmethod
    def _close(self) -> None: ...

    @abc.abstractmethod
    def _send(self, frame: CanFrame) -> None: ...

    @abc.abstractmethod
    def _read(self, timeout: float) -> Optional[CanFrame]: ...

    # ── internals ───────────────────────────────────────────────────
    def _status(self, level: str, msg: str) -> None:
        if level == "error":
            self.stats.last_error = msg
        if self._status_cb:
            try:
                self._status_cb(level, f"{self.info.key}: {msg}")
            except Exception:
                pass

    def _rx_loop(self) -> None:
        while self._running:
            try:
                frame = self._read(0.1)
            except Exception as exc:
                if not self._running:
                    break
                self.stats.rx_errors += 1
                self._status("error", f"read failed: {exc}")
                threading.Event().wait(0.2)
                continue
            if frame is None:
                continue
            self.stats.rx += 1
            cb = self._rx_cb
            if cb:
                try:
                    cb(frame)
                except Exception as exc:
                    self._status("error", f"rx handler: {exc!r}")
