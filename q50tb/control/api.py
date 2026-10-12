"""High-level, JSON-friendly bench API.

Every public method decorated with ``@tool`` is exposed by the local
JSON-RPC control server and, through it, as an MCP tool. Parameter types and
the first docstring paragraph become the tool schema/description, so adding a
method here is all it takes to give agents a new capability.

Conventions: CAN IDs and payloads are hex strings ("0x180", "83 40 00");
signal paths are "<bus>.<id>.<SIGNAL>" ("main.180.ENGINE_RPM"); results are
dicts; failures raise ``ApiError`` with a readable message.
"""

import datetime as dt
import inspect
import threading
import time
import typing
from typing import Any, Optional

from q50tb.av.services.navigation import RouteStep, heading_from_compass
from q50tb.av.services.settings import LANGUAGES, PAGES, UNITS
from q50tb.can.frame import CanFrame
from q50tb.core import timebase
from q50tb.core.bus_manager import SendRefused
from q50tb.core.log_buffer import IdFilter
from q50tb.core.send_queue import SendEntry, SweepSpec
from q50tb.hw.bench_io import UI_CONTROLS
from q50tb.signals.dashboard import Dashboard
from q50tb.sim.background import CounterRule

TOOLS: dict[str, dict] = {}


class ApiError(Exception):
    pass


def _schema_for(tp) -> dict:
    origin = typing.get_origin(tp)
    if origin is typing.Union:
        args = [a for a in typing.get_args(tp) if a is not type(None)]
        return _schema_for(args[0]) if args else {}
    if tp is bool:
        return {"type": "boolean"}
    if tp is int:
        return {"type": "integer"}
    if tp is float:
        return {"type": "number"}
    if tp is str:
        return {"type": "string"}
    if origin in (list, tuple) or tp in (list, tuple):
        args = typing.get_args(tp)
        return {"type": "array", "items": _schema_for(args[0]) if args else {}}
    if origin is dict or tp is dict:
        return {"type": "object"}
    return {}


def tool(fn):
    hints = typing.get_type_hints(fn)
    sig = inspect.signature(fn)
    props, required = {}, []
    for name, p in sig.parameters.items():
        if name == "self":
            continue
        sch = _schema_for(hints.get(name, str))
        if p.default is inspect.Parameter.empty:
            required.append(name)
        else:
            sch["default"] = p.default
        props[name] = sch
    doc = inspect.getdoc(fn) or ""
    TOOLS[fn.__name__] = {"name": fn.__name__, "description": doc,
                          "input_schema": {"type": "object", "properties": props, "required": required}}
    fn._is_tool = True
    return fn


def _hex_id(v) -> int:
    if isinstance(v, int):
        return v
    return int(str(v).strip().lower().replace("0x", ""), 16)


def _hex_bytes(v) -> bytes:
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    if isinstance(v, list):
        return bytes(int(x, 16) if isinstance(x, str) else int(x) for x in v)
    s = str(v).replace("0x", "").replace(",", " ").replace(":", " ").replace("-", " ")
    s = "".join(s.split())
    if len(s) % 2:
        raise ApiError(f"odd number of hex digits in {v!r}")
    return bytes.fromhex(s)


def _wait_done(start, timeout: float = 5.0) -> dict:
    ev = threading.Event()
    res: dict = {}

    def done(ok, why):
        res.update(ok=bool(ok), result=why)
        ev.set()
    start(done)
    if not ev.wait(timeout):
        return {"ok": False, "result": "timeout waiting for the meter"}
    return res


class BenchAPI:
    def __init__(self, ctx):
        self.ctx = ctx

    def call(self, name: str, args: Optional[dict] = None) -> Any:
        if name not in TOOLS:
            raise ApiError(f"unknown method {name!r}")
        return getattr(self, name)(**(args or {}))

    # ── overview ────────────────────────────────────────────────────
    @tool
    def help(self) -> dict:
        """Quick-start guide for agents: what this bench is and the usual call order."""
        return {
            "about": "Infiniti Q50 combination-meter test bench. Main CAN bus (500k) drives the meter; "
                     "a second 'av' bus emulates the head unit; an ESP32 switches B+, IG (+ACC, tied together) and hard-wired inputs.",
            "safety": ["Never run bench (TX) mode against a real vehicle - use monitor=true there.",
                       "Meter-originated IDs (main 280/2DE/355/5C5, AV 683/6A8/6A9...) are never transmitted.",
                       "Keep simulated speed at 0 unless asked (it advances the odometer).",
                       "Report success only from the meter's response (av_* results say 'confirmed')."],
            "typical_flow": ["list_adapters", "start_buses(main='socketcan:can0', av='socketcan:can1')  (Windows/CANlib: kvaser:0, kvaser:1)",
                             "bench_connect / bench_set('BPLUS', true), bench_set('IG', true)",
                             "set_signal('main.180.ENGINE_RPM', 2000)", "get_dashboard",
                             "av_start", "av_status (wait for channels 'ready')", "av_set_page('Audio', true)"],
            "signal_paths": "use list_signals(filter) to discover; values are physical units",
        }

    @tool
    def status(self) -> dict:
        """Overall state: buses, monitor mode, rates, background, HU emulation, ESP32 and replay."""
        c = self.ctx
        c.buses.update_rates()
        buses = {}
        for role, ch in c.buses.channels.items():
            buses[role] = {"adapter": ch.adapter.info.key, "name": ch.adapter.info.name, "monitor_only": ch.monitor_only,
                           "listen_only_hw": ch.config.listen_only, "bitrate": ch.config.bitrate, "fd": ch.config.fd,
                           "bus_state": ch.adapter.stats.bus_state, "rx": ch.stats.rx, "tx": ch.stats.tx,
                           "rx_fps": round(ch.stats.rx_fps, 1), "tx_fps": round(ch.stats.tx_fps, 1),
                           "refused": ch.stats.refused, "suppressed": ch.stats.suppressed,
                           "tx_errors": ch.stats.tx_errors, "last_error": ch.stats.last_error or ch.adapter.stats.last_error}
        bg = c.background
        return {"running": c.buses.running, "monitor_mode": c.buses.monitor_only, "buses": buses,
                "background": {"profile": bg.profile_name, "paused": bg.paused, "messages": len(bg.messages),
                               "enabled": sum(m.enabled for m in bg.messages.values())},
                "av": {"running": c.hu.running, "channels": {k: v["state"] for k, v in c.hu.status()["channels"].items()}},
                "bench": {"connected": c.bench.connected, "port": c.bench.port},
                "replay": vars(c.replay.status) | {}, "recording": c.recorder.path if c.recorder.active else ""}

    @tool
    def recent_events(self, count: int = 30) -> list:
        """Recent status/warning/error messages from the bus layer."""
        return [{"t": round(t, 3), "level": lvl, "text": msg} for t, lvl, msg in list(self.ctx.buses.status_log)[-count:]]

    # ── adapters & buses ────────────────────────────────────────────
    @tool
    def list_adapters(self) -> list:
        """CAN adapters found on this computer (Kvaser CANlib, SocketCAN, software virtual)."""
        return [{"key": a.key, "name": a.name, "listen_only": a.supports_listen_only, "fd": a.supports_fd,
                 "virtual": a.is_virtual} for a in self.ctx.refresh_adapters()]

    @tool
    def start_buses(self, main: str, av: Optional[str] = None, monitor: bool = False,
                    bitrate: int = 500000, fd: bool = False) -> dict:
        """Open the main bus (and optionally the AV bus) on adapter keys from list_adapters.
        monitor=true opens listen-only: nothing is transmitted or ACKed (required on a real car)."""
        try:
            self.ctx.start_buses(main, av, monitor=monitor, bitrate=bitrate, fd=fd)
        except Exception as exc:
            raise ApiError(str(exc))
        return self.status()

    @tool
    def stop_buses(self) -> dict:
        """Close all buses (stops background traffic, HU emulation and replay)."""
        self.ctx.stop_buses()
        return {"ok": True}

    # ── signals ─────────────────────────────────────────────────────
    @tool
    def list_signals(self, filter: str = "", bus: str = "main") -> list:
        """Signal definitions (path, unit, range, enum, confidence). filter matches path/label text."""
        out = []
        f = filter.lower()
        for m in self.ctx.db.by_bus(bus):
            for s in m.signals:
                path = s.full_name
                if f and f not in path.lower() and f not in s.label.lower() and f not in m.name.lower():
                    continue
                lo, hi = s.range()
                out.append({"path": path, "message": m.name, "label": s.label, "unit": s.unit,
                            "position": s.position_text(), "min": lo, "max": hi, "enum": s.enum,
                            "confidence": s.confidence, "meter_origin": m.meter_origin, "notes": s.notes})
        return out

    @tool
    def set_signal(self, path: str, value: float) -> dict:
        """Command a signal in its physical unit (e.g. 'main.180.ENGINE_RPM', 2500). Enums take the
        numeric code. The background message carrying it keeps its counters/checksums."""
        try:
            sdef = self.ctx.db.signal(path)
        except KeyError as exc:
            raise ApiError(f"unknown signal {path}: {exc}")
        msg = sdef.message
        if msg.meter_origin:
            raise ApiError(f"{path} is meter-originated; the bench never transmits it")
        raw = self.ctx.background.set_signal(msg.bus, msg.arbitration_id, sdef.name, value)
        bg = self.ctx.background.get(msg.bus, msg.arbitration_id)
        return {"path": path, "raw": raw, "message_enabled": bool(bg and bg.enabled),
                "note": "" if bg and bg.enabled else "background message is disabled - enable it to transmit"}

    @tool
    def set_signals(self, values: dict) -> list:
        """Set several signals at once: {"main.180.ENGINE_RPM": 3000, "main.421.GEAR": 4}."""
        return [self.set_signal(k, float(v)) for k, v in values.items()]

    @tool
    def clear_signal(self, path: str) -> dict:
        """Stop overriding a signal (the message falls back to its captured template value)."""
        sdef = self.ctx.db.signal(path)
        self.ctx.background.clear_signal(sdef.message.bus, sdef.message.arbitration_id, sdef.name)
        return {"ok": True}

    @tool
    def set_fuel_economy(self, enabled: bool = True, target_mpg: Optional[float] = None,
                         idle_gph: Optional[float] = None) -> dict:
        """Instantaneous fuel economy: advances the 0x551 byte-1 fuel counter so the meter computes target_mpg
        at the current speed (MPG = 13.1 * mph / counts per s). Below 0.5 mph the counter follows idle_gph
        (US gal/h). The meter calculates average economy itself. Needs speed > 0 to show a value."""
        self.ctx.fuel.set(enabled, target_mpg, idle_gph)
        return self.ctx.fuel.status()

    @tool
    def set_engine_running(self, running: bool) -> dict:
        """Engine on/off as the ECUs report it: off sets the engine flags in 0x160/0x182/0x300/0x580 to their
        stopped values and RPM to 0 (RPM-gated warnings such as low oil and AT error go out); running returns
        to the captured idle. The ignition-ready bits (0x551 B3.7, 0x239 B4.6) are not touched."""
        self.ctx.set_engine_running(running)
        return {"engine_running": self.ctx.engine_running()}

    @tool
    def blink_signal(self, path: str, on: bool, half_period_s: float = 0.4) -> dict:
        """Flash a 1-bit signal like the BCM flasher (e.g. 'main.60D.LEFT_TURN'; both turn bits = hazards).
        set_signal alone holds the bit steady, which the meter shows as a solid arrow. on=false stops and clears it."""
        try:
            sdef = self.ctx.db.signal(path)
        except KeyError as exc:
            raise ApiError(f"unknown signal {path}: {exc}")
        if sdef.bit_length != 1:
            raise ApiError(f"{path} is not a 1-bit signal")
        if sdef.message.meter_origin:
            raise ApiError(f"{path} is meter-originated; the bench never transmits it")
        if on:
            self.ctx.effects.blink(path, max(0.1, half_period_s))
        else:
            self.ctx.effects.stop(path)
        return {"path": path, "blinking": self.ctx.effects.blinking(path)}

    @tool
    def read_signal(self, path: str) -> dict:
        """Latest decoded value of a signal from bus traffic (RX, plus own TX in bench mode)."""
        r = self.ctx.vehicle.read(path)
        return {"value": r.value, "text": r.text, "raw": r.raw, "stale": r.stale,
                "age_s": None if r.age is None else round(r.age, 3), "direction": r.direction}

    @tool
    def get_dashboard(self) -> dict:
        """Everything the virtual cluster shows (gauges, telltales, doors, TPMS, ADAS, AV nav/audio)."""
        return Dashboard(self.ctx.vehicle, self.ctx.sniffer, self.ctx.settings.dashboard_units).summary()

    # ── background messages ─────────────────────────────────────────
    @tool
    def background_list(self, bus: str = "") -> list:
        """Background (default) messages with enable state, period, current payload and overrides."""
        out = []
        for m in sorted(self.ctx.background.messages.values(), key=lambda m: (m.bus, m.arbitration_id)):
            if bus and m.bus != bus:
                continue
            out.append({"bus": m.bus, "id": f"0x{m.arbitration_id:03X}", "name": m.name, "enabled": m.enabled,
                        "period_ms": m.period_ms, "payload": (m.last_payload or m.base).hex(" ").upper(),
                        "overrides": [None if v is None else f"{v:02X}" for v in m.overrides],
                        "signals": dict(m.overlays), "tx": m.tx_count, "status": m.status})
        return out

    @tool
    def background_enable(self, id: str, enabled: bool, bus: str = "main") -> dict:
        """Enable/disable one background message."""
        self.ctx.background.set_enabled(bus, _hex_id(id), enabled)
        return {"ok": True}

    @tool
    def background_enable_all(self, enabled: bool, bus: str = "") -> dict:
        """Enable or disable all background messages (optionally one bus)."""
        self.ctx.background.set_all_enabled(enabled, bus or None)
        return {"ok": True}

    @tool
    def background_override(self, id: str, payload: str, bus: str = "main") -> dict:
        """Override payload bytes of a background message. Use 'XX' or '--' to leave a byte alone,
        e.g. '-- -- 41 -- -- -- -- --'. Counters/checksums keep running."""
        m = self.ctx.background.get(bus, _hex_id(id))
        if m is None:
            raise ApiError(f"no background message {bus} {id}")
        toks = payload.replace(",", " ").split()
        for i, tok in enumerate(toks):
            m.set_override(i, None if tok.upper() in ("XX", "--", "..") else int(tok, 16))
        return {"ok": True, "overrides": [None if v is None else f"{v:02X}" for v in m.overrides]}

    @tool
    def background_clear_override(self, id: str, bus: str = "main") -> dict:
        """Remove all byte overrides from a background message."""
        m = self.ctx.background.get(bus, _hex_id(id))
        if m:
            m.clear_overrides()
        return {"ok": True}

    @tool
    def background_pause(self, paused: bool) -> dict:
        """Pause/resume all background traffic without changing enable states."""
        self.ctx.background.set_paused(paused)
        return {"ok": True}

    # ── raw send ────────────────────────────────────────────────────
    @tool
    def send_frame(self, id: str, data: str, bus: str = "main", extended: bool = False, fd: bool = False,
                   brs: bool = False) -> dict:
        """Send one frame now. data is hex ('01 02 03'). FD frames only on a bus opened as CAN FD."""
        frame = CanFrame(_hex_id(id), _hex_bytes(data), is_extended=extended, is_fd=fd, bitrate_switch=brs, bus=bus)
        try:
            self.ctx.send_queue.send_once(frame)
        except (SendRefused, ValueError) as exc:
            raise ApiError(str(exc))
        return {"ok": True}

    @tool
    def queue_add(self, id: str, data: str, period_ms: float, bus: str = "main", name: str = "",
                  counter_byte: int = -1, counter_lsb: int = 0, counter_width: int = 4, count_limit: int = 0,
                  fd: bool = False, extended: bool = False) -> dict:
        """Add a periodic frame to the send queue (optionally with a rolling counter). Returns its uid."""
        ctr = CounterRule(counter_byte, counter_lsb, counter_width) if counter_byte >= 0 else None
        e = SendEntry(bus, _hex_id(id), _hex_bytes(data), period_ms=period_ms, name=name, counter=ctr,
                      count_limit=count_limit, is_fd=fd, is_extended=extended)
        try:
            self.ctx.send_queue.add(e)
        except ValueError as exc:
            raise ApiError(str(exc))
        return {"uid": e.uid, "status": e.status}

    @tool
    def queue_sweep(self, id: str, baseline: str, bytes_: list, mode: str = "bit", start: int = 0, end: int = 255,
                    step: int = 1, custom: Optional[list] = None, dwell_ms: float = 1000, period_ms: float = 100,
                    loop: bool = False, bus: str = "main") -> dict:
        """Sweep bytes of a frame: mode 'bit' (walking one), 'counter' (start..end step) or 'custom'
        (list of values). Other bytes stay at baseline. Each value is held dwell_ms; frames every period_ms."""
        sw = SweepSpec(mode=mode, bytes_=[int(b) for b in bytes_], start=start, end=end, step=step,
                       custom=[int(str(x), 16) if isinstance(x, str) else int(x) for x in (custom or [])],
                       dwell_ms=dwell_ms, loop=loop)
        e = SendEntry(bus, _hex_id(id), _hex_bytes(baseline), period_ms=period_ms, sweep=sw, name="sweep")
        self.ctx.send_queue.add(e)
        return {"uid": e.uid, "steps": len(sw.plan())}

    @tool
    def queue_list(self) -> list:
        """Entries in the send queue."""
        return [{"uid": e.uid, "bus": e.bus, "id": f"0x{e.arbitration_id:03X}", "data": e.data.hex(" ").upper(),
                 "period_ms": e.period_ms, "enabled": e.enabled, "tx": e.tx_count, "status": e.status,
                 "detail": e.describe(), "sweep": e.sweep_progress} for e in self.ctx.send_queue.entries.values()]

    @tool
    def queue_remove(self, uid: int) -> dict:
        """Remove one send-queue entry."""
        self.ctx.send_queue.remove(uid)
        return {"ok": True}

    @tool
    def queue_clear(self) -> dict:
        """Remove every send-queue entry."""
        self.ctx.send_queue.clear()
        return {"ok": True}

    # ── log / tracker ───────────────────────────────────────────────
    @tool
    def log_tail(self, count: int = 50, filter: str = "", direction: str = "both", bus: str = "") -> list:
        """Last frames from the raw log. filter: comma-separated hex IDs/prefixes ('359, 30'),
        direction 'rx' | 'tx' | 'both'."""
        dirs = {"rx": ("RX",), "tx": ("TX",)}.get(direction.lower(), ("RX", "TX"))
        flt = IdFilter(filter)
        frames = [f for f in self.ctx.log.snapshot()[-max(count * 50, 5000):]
                  if f.direction in dirs and flt.matches(f) and (not bus or f.bus == bus)][-count:]
        return [{"t": round(f.timestamp, 4), "bus": f.bus, "dir": f.direction, "id": f.id_text, "dlc": f.dlc,
                 "data": f.data_text} for f in frames]

    @tool
    def watch_ids(self, ids: str, bus: str = "") -> dict:
        """Add IDs to the CAN message tracker (comma separated hex)."""
        added = []
        for tok in ids.replace(" ", ",").split(","):
            if tok.strip():
                self.ctx.tracker.add(_hex_id(tok), bus or None)
                added.append(tok.strip())
        return {"watching": added}

    @tool
    def tracker_snapshot(self) -> list:
        """Tracked IDs with bytes, per-byte change state, age and stale flag."""
        out = []
        for w in self.ctx.tracker.watches.values():
            out.append({"id": f"0x{w.arbitration_id:03X}", "bus": w.bus or "any", "count": w.count,
                        "age_s": None if w.age() is None else round(w.age(), 3),
                        "stale": self.ctx.tracker.is_stale(w), "waiting": w.data is None,
                        "bytes": [{"value": None if v is None else f"{v:02X}", "state": st} for v, st in w.byte_states()]})
        return out

    @tool
    def tracker_reset(self) -> dict:
        """Reset change highlights for all tracked IDs."""
        self.ctx.tracker.reset_all()
        return {"ok": True}

    # ── recording / replay ─────────────────────────────────────────
    @tool
    def record_start(self, path: str) -> dict:
        """Record all frames (both buses, RX+TX) to a pcapng file with a markers sidecar."""
        self.ctx.recorder.start(path)
        return {"ok": True, "path": path}

    @tool
    def record_marker(self, text: str) -> dict:
        """Write an annotation into the recording's markers file (same timebase as frames)."""
        self.ctx.recorder.marker(text)
        return {"ok": self.ctx.recorder.active}

    @tool
    def record_stop(self) -> dict:
        """Stop recording."""
        n = self.ctx.recorder.count
        self.ctx.recorder.stop()
        return {"ok": True, "frames": n}

    @tool
    def replay_load(self, track: int, path: str, bus: str, excluded: str = "", interface: str = "") -> dict:
        """Load a capture into replay track 0 or 1, targeting bus 'main' or 'av'. excluded: hex IDs.
        interface: pcapng interface name to use (blank = all)."""
        t = self.ctx.replay.tracks[track]
        try:
            t.load(path)
        except Exception as exc:
            raise ApiError(f"load failed: {exc}")
        t.target_bus = bus
        t.excluded = {_hex_id(x) for x in excluded.replace(" ", ",").split(",") if x.strip()}
        t.interfaces = {interface} if interface else None
        return {"frames": len(t.capture.frames), "duration_s": round(t.capture.duration, 3),
                "interfaces": t.capture.interfaces, "ids": [f"{i:03X}" for i in t.capture.ids()]}

    @tool
    def replay_start(self, speed: float = 1.0, loop: bool = False, tracks: Optional[list] = None) -> dict:
        """Start replay of loaded tracks simultaneously (tracks: list of indices, default all loaded)."""
        r = self.ctx.replay
        for i, t in enumerate(r.tracks):
            t.enabled = (tracks is None and t.capture is not None) or (tracks is not None and i in tracks)
        r.speed, r.loop = speed, loop
        try:
            r.start()
        except Exception as exc:
            raise ApiError(str(exc))
        return vars(r.status)

    @tool
    def replay_stop(self) -> dict:
        """Stop replay."""
        self.ctx.replay.stop()
        return vars(self.ctx.replay.status)

    # ── AV / head unit ─────────────────────────────────────────────
    @tool
    def av_start(self) -> dict:
        """Start head-unit emulation on the AV bus (bench mode only). Channels open when the meter announces."""
        try:
            self.ctx.hu.start("av")
        except Exception as exc:
            raise ApiError(str(exc))
        return self.ctx.hu.status()

    @tool
    def av_stop(self) -> dict:
        """Stop head-unit emulation."""
        self.ctx.hu.stop()
        return {"ok": True}

    @tool
    def av_status(self) -> dict:
        """Per-channel state (waiting/announced/negotiating/ready/lost), peer caps, queue and link stats,
        plus the last settings values read from the meter."""
        st = self.ctx.hu.status()
        items = {}
        for k, it in self.ctx.hu.settings.state.items.items():
            items[f"item_{k:X}"] = {"value": None if it.value is None else it.value.hex(" "),
                                    "status": it.status, "last_result": it.last_result}
        st["settings"] = items
        st["pages"] = {p: self.ctx.hu.settings.page_enabled(p) for p in PAGES}
        return st

    @tool
    def av_wait_ready(self, timeout_s: float = 10.0) -> dict:
        """Wait until the meter channels (status/settings/nav) are ready or timeout."""
        end = time.time() + timeout_s
        while time.time() < end:
            if all(self.ctx.hu.channel_ready(n) for n in ("status", "settings", "nav")):
                return {"ready": True}
            time.sleep(0.1)
        return {"ready": False, "channels": {k: v["state"] for k, v in self.ctx.hu.status()["channels"].items()}}

    @tool
    def av_read_settings(self) -> dict:
        """Read capabilities, language, page image, alerts, units and display effect from the meter."""
        self.ctx.hu.settings.read_all()
        time.sleep(1.5)
        return self.av_status()

    @tool
    def av_set_page(self, page: str, enabled: bool) -> dict:
        """Enable/disable a meter page: Range, Trip Distance, Average Speed, Driving Aids, Tire Pressure,
        Fuel Economy, Navigation, Audio, Blank Page, Chassis Control. Success only if the meter echoes it."""
        if page not in PAGES:
            raise ApiError(f"page must be one of {list(PAGES)}")
        return _wait_done(lambda d: self.ctx.hu.settings.set_page(page, enabled, d), 6)

    @tool
    def av_set_units(self, unit: str) -> dict:
        """Tire pressure unit: 'kPa', 'PSI' or 'bar'."""
        code = {v.lower(): k for k, v in UNITS.items()}.get(unit.lower())
        if code is None:
            raise ApiError("unit must be kPa, PSI or bar")
        return _wait_done(lambda d: self.ctx.hu.settings.set_units(code, d))

    @tool
    def av_set_language(self, language: str) -> dict:
        """Meter language: 'English', 'French' or 'Spanish' (all confirmed on the meter)."""
        code = next((k for k, v in LANGUAGES.items() if v.lower().startswith(language.lower())), None)
        if code is None:
            raise ApiError("language must be English, French or Spanish")
        return _wait_done(lambda d: self.ctx.hu.settings.set_language(code, d))

    @tool
    def av_set_alerts(self, outside_temp: bool, nav_alert: Optional[bool] = None) -> dict:
        """Alert preferences (item 8): outside temperature alert (byte 0) and candidate nav alert (byte 1)."""
        return _wait_done(lambda d: self.ctx.hu.settings.set_alerts(outside_temp, nav_alert, d))

    @tool
    def av_set_display_effect(self, enabled: bool) -> dict:
        """Startup animation ('Display Effect'). The write is confirmed by the meter (echo + read-back); the visible effect only shows at power-up."""
        return _wait_done(lambda d: self.ctx.hu.settings.set_display_effect(enabled, d))

    @tool
    def av_factory_reset(self, confirm: bool = False) -> dict:
        """Factory-reset meter settings (C4 00). Requires confirm=true - this clears the meter's settings."""
        if not confirm:
            raise ApiError("refused: pass confirm=true after the user explicitly asked for a factory reset")
        return _wait_done(lambda d: self.ctx.hu.settings.factory_reset(d))

    @tool
    def av_driver_break_alert(self) -> dict:
        """Show the driver break alert on the meter (628 C4 2D)."""
        self.ctx.hu.settings.driver_break_alert()
        return {"ok": True}

    @tool
    def av_audio(self, source: str, fields: Optional[dict] = None) -> dict:
        """Select an audio source and set metadata. Radio: FM, AM, SXM (fields frequency, preset, station,
        radio_text). Media: CD, MUSICBOX, IPOD, USB, BT, DVD, IAS, DISC (fields title, album, artist, playing,
        elapsed = the track number the meter shows). AUX has no fields. A media source with no metadata shows
        its 'not connected / no disc' message. Keys come from registry/av_sources.json."""
        try:
            self.ctx.hu.audio.select(source.upper(), **(fields or {}))
        except KeyError as exc:
            raise ApiError(str(exc))
        return {"ok": True, "sources": [s.key for s in self.ctx.hu.audio.sources]}

    @tool
    def av_nav(self, state: int, maneuver: int = 0, street: str = "", distance: Optional[float] = None,
               unit: str = "mi", progress: int = 0, heading: str = "N") -> dict:
        """Send a navigation record. state: 0 idle(compass), 2/3 guidance, 16 approach start,
        17 approaching (progress bar), 18 at maneuver. heading: compass name (N, NNE, ...) or 1-32."""
        h = int(heading) if str(heading).isdigit() else heading_from_compass(heading)
        self.ctx.hu.nav.update(state=state, maneuver=maneuver, street=street, distance=distance,
                               unit=3 if unit == "mi" else 8 if unit == "ft" else int(unit), progress=progress, heading=h)
        return {"ok": True, "record": self.ctx.hu.nav.state.record().hex(" ")}

    @tool
    def av_nav_route(self, steps: list, loop: bool = False) -> dict:
        """Play a simulated route. steps: [{maneuver, street, distance, unit ('mi'|'ft'), heading,
        duration_s, approach_at, hold_s}, ...]."""
        rs = []
        for s in steps:
            unit = s.get("unit", "mi")
            h = s.get("heading", "N")
            rs.append(RouteStep(maneuver=int(s.get("maneuver", 0)), street=s.get("street", ""),
                                distance=float(s.get("distance", 1.0)), unit=3 if unit == "mi" else 8,
                                heading=int(h) if str(h).isdigit() else heading_from_compass(h),
                                duration_s=float(s.get("duration_s", 20)), approach_at=float(s.get("approach_at", 0.3)),
                                hold_s=float(s.get("hold_s", 2))))
        self.ctx.hu.nav.play(rs, loop)
        return {"ok": True, "steps": len(rs)}

    @tool
    def av_nav_stop(self) -> dict:
        """Stop the simulated route."""
        self.ctx.hu.nav.stop()
        return {"ok": True}

    @tool
    def av_clock(self, time: str = "", hour24: Optional[bool] = None, use_pc_time: bool = False) -> dict:
        """Set the meter clock. time: 'YYYY-MM-DD HH:MM[:SS]' or 'HH:MM'; use_pc_time=true syncs to this PC.
        hour24 switches 12/24 h display."""
        clk = self.ctx.hu.clock
        if hour24 is not None:
            clk.state.hour24 = hour24
        if use_pc_time:
            clk.use_pc_time()
        elif time:
            now = dt.datetime.now()
            try:
                when = dt.datetime.fromisoformat(time) if "-" in time else dt.datetime.combine(
                    now.date(), dt.time.fromisoformat(time))
            except ValueError:
                raise ApiError("time must be 'YYYY-MM-DD HH:MM' or 'HH:MM'")
            clk.set_time(when)
        else:
            clk.send_now()
        return {"ok": True, "record": clk.clock_record().hex(" ")}

    @tool
    def av_send_record(self, channel: str, record: str, expect_ack: bool = True, force: bool = False) -> dict:
        """Debug: send a raw application record (hex, e.g. 'C0 21') on an AV channel
        (status, settings, nav, source, capability, buttons, touch, identity)."""
        res = _wait_done(lambda d: self.ctx.hu.send(channel, _hex_bytes(record), label="api raw",
                                                    expect_ack=expect_ack, on_done=d, force=force) or None, 4)
        return res

    @tool
    def av_log(self, count: int = 40) -> list:
        """Recent AV transactions (reassembled records with decoded descriptions and HU events)."""
        return [dict(zip(["t", "id", "channel", "side", "bus_dir", "kind", "token", "data", "text"], e.row()))
                for e in list(self.ctx.sniffer.log)[-count:]]

    # ── ESP32 bench I/O ────────────────────────────────────────────
    @tool
    def bench_ports(self) -> dict:
        """Serial ports, and the ones that look like an ESP32 board."""
        return {"ports": self.ctx.bench.ports(), "esp32_candidates": self.ctx.bench.candidates()}

    @tool
    def bench_connect(self, port: str = "") -> dict:
        """Connect to the ESP32 bench controller (blank port = first ESP32-looking port)."""
        port = port or (self.ctx.bench.candidates() or [""])[0]
        if not port:
            raise ApiError("no ESP32 serial port found")
        try:
            self.ctx.bench.connect(port, wait=True)
        except Exception as exc:
            raise ApiError(f"connect failed: {exc}")
        return self.bench_status()

    @tool
    def bench_status(self) -> dict:
        """ESP32 outputs: state per channel (true/false, null = unknown/not wired) and pins."""
        info = self.ctx.bench.channel_info()
        st = self.ctx.bench.states()
        return {"connected": self.ctx.bench.connected, "port": self.ctx.bench.port,
                "channels": {name: {"label": label, "kind": kind, "state": st.get(name),
                                    "pin": info[name].pin if name in info else None}
                             for name, label, kind in UI_CONTROLS}}

    @tool
    def bench_set(self, name: str, on: bool) -> dict:
        """Set a latched output: BPLUS, IG (also switches ACC, which is tied to IG; 'ACC' is an alias), AIRBAG, DRVR_BELT, PSGR_BELT, WASHER_FLUID, BRAKE_FLUID,
        PKB, HEADLIGHT_LH, HEADLIGHT_RH, ALTERNATOR, SECURITY."""
        try:
            self.ctx.bench.set(name.upper(), on, wait=True)
        except Exception as exc:
            raise ApiError(str(exc))
        return {"ok": True}

    @tool
    def bench_press(self, name: str, ms: int = 200) -> dict:
        """Momentary press: TRIP_RESET, STSW_B_LEFT, STSW_B_RIGHT (page the centre display), PADDLE_UP,
        PADDLE_DOWN, BRIGHT_UP, BRIGHT_DOWN."""
        try:
            self.ctx.bench.press(name.upper(), ms, wait=True)
        except Exception as exc:
            raise ApiError(str(exc))
        return {"ok": True}

    @tool
    def bench_all_off(self) -> dict:
        """Turn every ESP32 output off (meter loses power)."""
        self.ctx.bench.all_off(wait=True)
        return {"ok": True}

    @tool
    def bench_power_cycle(self, off_s: float = 5.0) -> dict:
        """Reboot the meter: B+ and IG (+ACC) off for off_s seconds (>=5 for a real reboot), then restored."""
        self.ctx.bench.power_cycle(off_s, wait=True)
        return {"ok": True}

    @tool
    def wait(self, seconds: float) -> dict:
        """Sleep (max 60 s) - useful between steps in an automated test."""
        time.sleep(max(0.0, min(60.0, seconds)))
        return {"t": round(timebase.now(), 3)}
