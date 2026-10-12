"""Composition root: builds every service once and wires them together.

The GUI, the control server and the MCP server all talk to this object (the
GUI directly, the others through ``control.api.BenchAPI``), so behaviour is
identical however the bench is driven.

Layering (lower layers never import higher ones):

    can/        adapters, discovery, frame
    core/       bus manager (safety + ownership), scheduler, log, replay, send queue, tracker, recorder
    capture/    file formats
    signals/    database, codec, vehicle state, DBC
    sim/        background messages, baseline builder
    av/         transport, head-unit emulator, services, sniffer
    hw/         ESP32 bench I/O
    control/    API, JSON-RPC server, MCP server
    ui/         Qt pages (one module per page)
"""

from __future__ import annotations

import os
from typing import Optional

from q50tb.av.headunit import HeadUnitEmulator
from q50tb.av.sniffer import AvSniffer
from q50tb.av.transport import LinkConfig
from q50tb.can.adapters.base import AdapterError, AdapterInfo, BusConfig, ListenOnlyUnsupported
from q50tb.can.discovery import adapter_from_key, discover_adapters
from q50tb.core.bus_manager import AV, MAIN, BusManager
from q50tb.core.config import ROOT, AppSettings
from q50tb.core.log_buffer import LogBuffer
from q50tb.core.recorder import Recorder
from q50tb.core.replay import ReplayEngine
from q50tb.core.scheduler import Scheduler
from q50tb.core.send_queue import SendQueue
from q50tb.core.tracker import Tracker
from q50tb.hw.bench_io import BenchIO
from q50tb.signals.dbc import DbcLiveDecoder
from q50tb.signals.q50_main import build_database
from q50tb.signals.vehicle_state import VehicleState
from q50tb.sim.background import BackgroundManager
from q50tb.sim.effects import Effects


class AppContext:
    def __init__(self, settings: Optional[AppSettings] = None):
        self.settings = settings or AppSettings.load()
        self.root = ROOT
        self.db = build_database()
        self.buses = BusManager()
        self.scheduler = Scheduler("tx-scheduler", on_error=self.buses.status)
        self.scheduler.start()
        self.log = LogBuffer(self.settings.log_max_frames)
        self.tracker = Tracker()
        self.tracker.stale_s = self.settings.tracker_stale_ms / 1000.0
        self.vehicle = VehicleState(self.db)
        self.dbc = DbcLiveDecoder()
        self.recorder = Recorder()
        self.sniffer = AvSniffer()
        for fn in (self.log.append, self.tracker.on_frame, self.vehicle.on_frame, self.dbc.on_frame,
                   self.recorder.on_frame, self.sniffer.on_frame):
            self.buses.subscribe_all(fn)
        self.background = BackgroundManager(self.buses, self.scheduler, self.db)
        self.effects = Effects(self.background, self.scheduler, self.db)
        self.send_queue = SendQueue(self.buses, self.scheduler)
        self.replay = ReplayEngine(self.buses, lambda bus: self.buses.channels[bus].protected_ids
                                   if bus in self.buses.channels else self.settings.protected(bus))
        self.hu = HeadUnitEmulator(self.buses, self.scheduler, self.sniffer)
        self.apply_av_settings()
        self.bench = BenchIO()
        self.bench.press_ms = self.settings.press_ms
        self.adapters: list[AdapterInfo] = []
        if self.settings.background_profile and os.path.exists(self.settings.background_profile):
            try:
                self.background.load_profile(self.settings.background_profile)
            except Exception as exc:
                self.buses.status("error", f"background profile: {exc}")
        self.buses.add_state_listener(self._on_bus_state)

    # ── adapters & buses ────────────────────────────────────────────
    def refresh_adapters(self) -> list[AdapterInfo]:
        self.adapters = discover_adapters()
        return self.adapters

    def apply_av_settings(self) -> None:
        s = self.settings
        self.hu.link_config = LinkConfig(ack_timeout=s.av_ack_timeout_ms / 1000.0, retries=s.av_retries)
        self.hu.peer_timeout = s.av_peer_timeout_s
        self.hu.auto_read_settings = s.av_auto_read_settings
        for rt in self.hu.channels.values():
            rt.config.answer_delay = s.av_answer_delay_ms / 1000.0

    def start_buses(self, main_key: str, av_key: Optional[str] = None, monitor: bool = False,
                    bitrate: Optional[int] = None, fd: Optional[bool] = None,
                    data_bitrate: Optional[int] = None) -> None:
        s = self.settings
        known = self.adapters or self.refresh_adapters()
        assignments = {MAIN: adapter_from_key(main_key, known)}
        if av_key:
            if av_key == main_key:
                raise AdapterError("the AV bus must use a different adapter channel than the main bus")
            assignments[AV] = adapter_from_key(av_key, known)
        if monitor:
            for role, info in assignments.items():
                if info.supports_listen_only is False and not s.allow_monitor_without_hw_listen_only:
                    raise ListenOnlyUnsupported(
                        f"{info.describe()} cannot guarantee listen-only (it would ACK frames). "
                        "Refusing MONITOR mode. Use an adapter with silent mode, or explicitly allow "
                        "software-only monitoring in Settings.")
        cfg = BusConfig(bitrate=bitrate or s.bitrate, fd=s.fd if fd is None else fd,
                        data_bitrate=data_bitrate or s.data_bitrate, configure_link=s.socketcan_configure_link)
        soft = set()
        if monitor and s.allow_monitor_without_hw_listen_only:
            soft = {role for role, info in assignments.items() if info.supports_listen_only is False}
        self.buses.start(assignments, cfg, monitor_only=monitor,
                         protected={MAIN: s.protected(MAIN), AV: s.protected(AV)}, software_monitor_roles=soft)
        for ch in self.buses.channels.values():
            ch.allow_protected = s.allow_protected_tx
        s.main_adapter = main_key
        s.av_adapter = av_key or ""
        s.monitor_mode = monitor
        self.vehicle.directions = ("RX",) if monitor else ("RX", "TX")
        if s.dashboard_source == "rx":
            self.vehicle.directions = ("RX",)
        elif s.dashboard_source == "tx+rx":
            self.vehicle.directions = ("RX", "TX")
        try:
            s.save()
        except OSError:
            pass

    def open_av_bus(self, av_key: str) -> None:
        """Open (or move) the AV bus while the main bus keeps running."""
        main = self.buses.channels.get(MAIN)
        if main and main.adapter.info.key == av_key:
            raise AdapterError("the AV bus cannot use the main bus adapter")
        was_running = self.hu.running
        self.hu.stop()
        if AV in self.buses.channels:
            self.buses.close_role(AV)
        s = self.settings
        info = adapter_from_key(av_key, self.adapters or self.refresh_adapters())
        if self.buses.monitor_only and info.supports_listen_only is False and not s.allow_monitor_without_hw_listen_only:
            raise ListenOnlyUnsupported(f"{info.describe()} cannot guarantee listen-only")
        cfg = BusConfig(bitrate=s.bitrate, fd=False, configure_link=s.socketcan_configure_link)
        self.buses.open_role(AV, info, cfg, s.protected(AV))
        self.buses.channels[AV].allow_protected = s.allow_protected_tx
        s.av_adapter = av_key
        if was_running:
            self.hu.start(AV)

    def start_virtual_meter(self) -> None:
        """Offline demo: a software meter on the same python-can virtual bus as the AV bus."""
        from q50tb.av.virtual_meter import VirtualMeter
        ch = self.buses.channels.get(AV)
        if ch is None or ch.adapter.info.backend != "virtual":
            raise AdapterError("the virtual meter needs the AV bus on a software virtual adapter")
        self.stop_virtual_meter()
        self._vm_bus = BusManager()
        self._vm_bus.start({AV: ch.adapter.info}, BusConfig(), monitor_only=False, protected={})
        self.virtual_meter = VirtualMeter(self._vm_bus, self.scheduler, AV)
        self.virtual_meter.start()
        self.buses.status("info", "virtual meter running on the AV bus (offline demo, not the real meter)")

    def stop_virtual_meter(self) -> None:
        vm = getattr(self, "virtual_meter", None)
        if vm:
            vm.stop()
            self._vm_bus.stop()
            self.virtual_meter = None

    def close_av_bus(self) -> None:
        self.hu.stop()
        self.buses.close_role(AV)

    def stop_buses(self) -> None:
        self.stop_virtual_meter()
        self.hu.stop()
        if self.replay.status.running:
            self.replay.stop()
        self.buses.stop()

    def _on_bus_state(self) -> None:
        if self.buses.running and not self.buses.monitor_only:
            if not self.settings.background_autostart:
                self.background.set_paused(True)
            av = self.buses.get(AV)
            if av is not None and self.settings.av_auto_start and not self.hu.running:
                try:
                    self.hu.start(AV)
                except Exception as exc:
                    self.buses.status("error", f"AV auto-start: {exc}")

    def shutdown(self) -> None:
        try:
            self.recorder.stop()
            self.stop_buses()
            if self.bench.connected:
                self.bench.disconnect(wait=True)
        finally:
            self.scheduler.stop()
