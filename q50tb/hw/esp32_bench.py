"""
esp32_bench.py — client for the esp32-bench firmware (firmware/esp32_bench/
main/bench_main.c in this repo, protocol-compatible with the original
esp32-bench firmware), which switches the meter's B+/IG/ACC relays and drives
its hard-wired warning inputs and buttons.

Line protocol over the ESP32's USB serial console (115200 8N1):

    PING / LIST / STATUS / SET <NAME> <0|1> / PRESS <NAME> <ms> / ALLOFF
    replies start with "OK" or "ERR"; unsolicited lines start with "EVT";
    anything else is ESP-IDF log output and is ignored.

The firmware emits "EVT STATE <NAME> <0|1>" for every output change
(including its own auto-release of a press), so this client tracks output
state from events only. "EVT READY" means the ESP32 (re)booted with every
output OFF. After a disconnect every state is None (unknown), never False.

A background reader thread owns serial reads. command() blocks the calling
thread for up to `timeout`, so callers on the Tk thread must hand it to a
worker. Listener callbacks run on the reader thread.
"""

from __future__ import annotations

import queue
import threading
import time
from dataclasses import dataclass
from typing import Callable, Optional

PROTO_VERSION = 1
DEFAULT_BAUD = 115200
DEFAULT_TIMEOUT_S = 1.0

POWER = "power"
INPUT = "input"
BUTTON = "button"

# Mirrors s_channels[] in bench_main.c; LIST from the device is authoritative
# for pin assignment.
CHANNELS: tuple[tuple[str, str, str], ...] = (
    ("BPLUS", POWER, "B+"),
    ("IG", POWER, "IG"),
    ("ACC", POWER, "ACC"),          # new in this repo's firmware; older firmware omits it
    ("SECURITY", INPUT, "Security"),
    ("ALTERNATOR", INPUT, "Alternator"),
    ("AIRBAG", INPUT, "Air bag"),
    ("WASHER_FLUID", INPUT, "Washer fluid"),
    ("BRAKE_FLUID", INPUT, "Brake fluid"),
    ("PKB", INPUT, "Parking brake"),
    ("PSGR_BELT", INPUT, "Passenger belt"),
    ("DRVR_BELT", INPUT, "Driver belt"),
    ("HEADLIGHT_LH", INPUT, "LH headlight"),
    ("HEADLIGHT_RH", INPUT, "RH headlight"),
    ("TRIP_RESET", BUTTON, "Trip reset"),
    ("PADDLE_UP", BUTTON, "Paddle up"),
    ("PADDLE_DOWN", BUTTON, "Paddle down"),
    ("BRIGHT_UP", BUTTON, "Brightness up"),
    ("BRIGHT_DOWN", BUTTON, "Brightness down"),
    ("STSW_B_LEFT", BUTTON, "STSW B Left"),
    ("STSW_B_RIGHT", BUTTON, "STSW B Right"),
)
# Channels driving one resistor-ladder wire; the firmware refuses to assert
# two of the same group at once (ERR INTERLOCK <other>).
LADDER_GROUPS: tuple[tuple[str, ...], ...] = (("STSW_B_LEFT", "STSW_B_RIGHT"),)
CHANNEL_NAMES = tuple(name for name, _kind, _label in CHANNELS)

# Expected second token of an OK reply for each command keyword.
_OK_TAG = {"PING": "PONG", "LIST": "LIST", "STATUS": "STATUS", "SET": "SET",
           "PRESS": "PRESS", "ALLOFF": "ALLOFF"}


class BenchError(RuntimeError):
    """The device answered ERR, or the link is not usable."""


class BenchTimeout(BenchError):
    """No matching reply within the timeout."""


@dataclass(frozen=True)
class ChannelInfo:
    name: str
    kind: str
    pin: int          # -1 = not wired

    @property
    def wired(self) -> bool:
        return self.pin >= 0


def parse_list(tokens: list[str]) -> dict[str, ChannelInfo]:
    """['BPLUS:power:25', ...] -> {name: ChannelInfo}."""
    out = {}
    for tok in tokens:
        name, kind, pin = tok.split(":")
        out[name] = ChannelInfo(name, kind, int(pin))
    return out


def parse_status(tokens: list[str]) -> dict[str, Optional[bool]]:
    """['BPLUS=0', 'IG=1', 'PKB=-'] -> {name: False/True/None (not wired)}."""
    out: dict[str, Optional[bool]] = {}
    for tok in tokens:
        name, _, value = tok.partition("=")
        out[name] = None if value == "-" else value == "1"
    return out


def _comports() -> list:
    try:
        from serial.tools import list_ports
    except ImportError:
        return []
    return list(list_ports.comports())


def list_serial_ports() -> list[str]:
    return sorted(p.device for p in _comports())


# USB-UART bridges found on ESP32 dev boards (CP210x, CH340, CH9102) plus
# Espressif's native-USB vendor ID. Auto-detect only probes these, so CAN
# adapters and other serial devices never receive a stray PING. FTDI is left
# out on purpose: it is common on non-ESP32 bench gear.
ESP32_USB_IDS = frozenset({(0x10C4, 0xEA60), (0x1A86, 0x7523), (0x1A86, 0x55D4)})
ESPRESSIF_VID = 0x303A


def find_bench_candidates(ports=None) -> list[str]:
    """Serial ports whose USB IDs look like an ESP32 board, sorted."""
    found = []
    for p in (_comports() if ports is None else ports):
        vid, pid = getattr(p, "vid", None), getattr(p, "pid", None)
        if vid == ESPRESSIF_VID or (vid, pid) in ESP32_USB_IDS:
            found.append(p.device)
    return sorted(found)


def _open_serial_without_reset(port: str, baudrate: int):
    import serial  # pyserial; imported lazily so tests need no hardware

    ser = serial.Serial()
    ser.port = port
    ser.baudrate = baudrate
    ser.timeout = 0.05
    # Dev-board auto-reset circuits pull EN low (rebooting the ESP32 and
    # dropping B+/IG) while DTR is deasserted and RTS asserted. Linux asserts
    # both on open, and pyserial's own preset order (DTR before RTS) passes
    # through that reset state. Open with both asserted (harmless), then drop
    # RTS before DTR. Bench-verified on the CP210x board: presetting reset
    # it every time, this order never did. If an adapter still resets, the
    # firmware's EVT READY reports it.
    ser.open()
    ser.rts = False
    ser.dtr = False
    return ser


Listener = Callable[[str, tuple], None]


class Esp32Bench:
    """Thread-safe client. Listener events (called on the reader thread):

        ("state", (name, bool))      an output changed
        ("connected", (port,))       connect() succeeded (from any caller)
        ("ready", (proto,))          ESP32 booted; all outputs OFF
        ("disconnected", (reason,))  link lost; all states now unknown
        ("log", (line,))             non-protocol line from the device
    """

    def __init__(self, serial_factory=_open_serial_without_reset):
        self._serial_factory = serial_factory
        self._ser = None
        self._reader: Optional[threading.Thread] = None
        self._running = False
        self._replies: "queue.Queue[str]" = queue.Queue()
        self._cmd_lock = threading.Lock()
        # Serializes connect/disconnect: the Bench I/O tab's auto-connect and
        # the control server (MCP) may both try to open the port.
        self._conn_lock = threading.RLock()
        self._state_lock = threading.Lock()
        self._states: dict[str, Optional[bool]] = {n: None for n in CHANNEL_NAMES}
        self.channels: dict[str, ChannelInfo] = {}
        self.port: Optional[str] = None
        self._listeners: list[Listener] = []

    # ── lifecycle ────────────────────────────────────────────────────
    @property
    def connected(self) -> bool:
        return self._running and self._ser is not None

    def add_listener(self, fn: Listener) -> None:
        self._listeners.append(fn)

    def connect(self, port: str, baudrate: int = DEFAULT_BAUD) -> None:
        """Open the port and sync state. Does not change any output."""
        with self._conn_lock:
            self._connect_locked(port, baudrate)
        self._emit("connected", (port,))

    def _connect_locked(self, port: str, baudrate: int) -> None:
        if self.connected:
            self.disconnect()
        self._ser = self._serial_factory(port, baudrate)
        self.port = port
        self._running = True
        self._drain_replies()
        self._reader = threading.Thread(target=self._read_loop, name="Esp32BenchReader",
                                        daemon=True)
        self._reader.start()
        try:
            pong = self._ping_until_booted()
            if pong[1:] != ["bench", str(PROTO_VERSION)]:
                raise BenchError(f"unsupported firmware protocol: {' '.join(pong)}")
            self.channels = parse_list(self.command("LIST")[1:])
            self.refresh_status()
        except Exception:
            # A failed probe was never a usable link: tear down without a
            # "disconnected" event so listeners do not report a lost link.
            self.disconnect(notify=False)
            raise

    def _ping_until_booted(self, attempts: int = 6, timeout: float = 0.5) -> list[str]:
        """An adapter that resets the ESP32 on open leaves it in the
        bootloader for ~0.3 s, dropping anything sent meanwhile; retry."""
        for attempt in range(attempts):
            try:
                return self.command("PING", timeout=timeout)
            except BenchTimeout:
                if attempt == attempts - 1:
                    raise

    def disconnect(self, notify: bool = True) -> None:
        with self._conn_lock:
            self._disconnect_locked(notify)

    def _disconnect_locked(self, notify: bool) -> None:
        was_running = self._running
        self._running = False
        ser, self._ser = self._ser, None
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass
        if self._reader is not None and self._reader is not threading.current_thread():
            self._reader.join(timeout=1.0)
        self._reader = None
        if was_running:
            self._mark_unknown()
            if notify:
                self._emit("disconnected", ("closed",))

    # ── commands ─────────────────────────────────────────────────────
    def command(self, text: str, timeout: float = DEFAULT_TIMEOUT_S) -> list[str]:
        """Send one command line; return the OK reply's tokens after "OK".
        Raises BenchError on ERR, BenchTimeout on no reply."""
        keyword = text.split()[0].upper()
        expected = _OK_TAG.get(keyword)
        with self._cmd_lock:
            ser = self._ser
            if ser is None or not self._running:
                raise BenchError("ESP32 bench not connected")
            self._drain_replies()
            try:
                ser.write((text + "\n").encode("ascii"))
            except Exception as e:
                self._lost(f"write failed: {e}")
                raise BenchError(f"ESP32 write failed: {e}") from e
            deadline = time.monotonic() + timeout
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BenchTimeout(f"ESP32 did not answer {text!r}")
                try:
                    reply = self._replies.get(timeout=remaining)
                except queue.Empty:
                    continue
                tokens = reply.split()
                if tokens[0] == "ERR":
                    raise BenchError(f"ESP32 rejected {text!r}: {' '.join(tokens[1:])}")
                if expected is None or (len(tokens) > 1 and tokens[1] == expected):
                    return tokens[1:]
                # A late reply to an earlier, timed-out command: skip it.

    def set(self, name: str, on: bool) -> None:
        self.command(f"SET {name} {1 if on else 0}")

    def press(self, name: str, ms: int) -> None:
        self.command(f"PRESS {name} {int(ms)}")

    def all_off(self) -> None:
        self.command("ALLOFF")

    def refresh_status(self) -> dict[str, Optional[bool]]:
        status = parse_status(self.command("STATUS")[1:])
        with self._state_lock:
            self._states.update(status)
        return status

    def states(self) -> dict[str, Optional[bool]]:
        with self._state_lock:
            return dict(self._states)

    # ── reader thread ────────────────────────────────────────────────
    def _read_loop(self) -> None:
        buf = b""
        while self._running:
            ser = self._ser
            if ser is None:
                break
            try:
                chunk = ser.read(256)
            except Exception as e:
                self._lost(f"read failed: {e}")
                return
            if not chunk:
                continue
            buf += chunk
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                self._handle_line(raw.decode("ascii", errors="replace").strip())

    def _handle_line(self, line: str) -> None:
        if not line:
            return
        tokens = line.split()
        head = tokens[0]
        if head in ("OK", "ERR"):
            self._replies.put(line)
        elif head == "EVT" and len(tokens) >= 2:
            if tokens[1] == "STATE" and len(tokens) == 4:
                name, on = tokens[2], tokens[3] == "1"
                with self._state_lock:
                    self._states[name] = on
                self._emit("state", (name, on))
            elif tokens[1] == "READY":
                with self._state_lock:
                    for name in self._states:
                        info = self.channels.get(name)
                        self._states[name] = False if info is None or info.wired else None
                self._emit("ready", tuple(tokens[2:]))
        else:
            self._emit("log", (line,))

    # ── helpers ──────────────────────────────────────────────────────
    def _lost(self, reason: str) -> None:
        if not self._running:
            return
        self._running = False
        ser, self._ser = self._ser, None
        if ser is not None:
            try:
                ser.close()
            except Exception:
                pass
        self._mark_unknown()
        self._emit("disconnected", (reason,))

    def _mark_unknown(self) -> None:
        with self._state_lock:
            for name in self._states:
                self._states[name] = None

    def _drain_replies(self) -> None:
        while True:
            try:
                self._replies.get_nowait()
            except queue.Empty:
                return

    def _emit(self, kind: str, payload: tuple) -> None:
        for fn in list(self._listeners):
            try:
                fn(kind, payload)
            except Exception:
                pass
