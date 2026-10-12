"""Persistent application settings (JSON in the user's config directory)."""

from __future__ import annotations

import json
import os
import sys
from dataclasses import asdict, dataclass, field, fields

from q50tb.signals.q50_main import AV_PEER_IDS, METER_ORIGIN_AV, METER_ORIGIN_MAIN

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def config_dir() -> str:
    if sys.platform.startswith("win"):
        base = os.environ.get("APPDATA", os.path.expanduser("~"))
    else:
        base = os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config"))
    path = os.path.join(base, "q50tb")
    os.makedirs(path, exist_ok=True)
    return path


def _hexset(ids) -> list[str]:
    return [f"0x{i:03X}" for i in sorted(ids)]


@dataclass
class AppSettings:
    # buses
    bitrate: int = 500_000
    fd: bool = False
    data_bitrate: int = 2_000_000
    main_adapter: str = ""
    av_adapter: str = ""
    av_enabled: bool = True
    monitor_mode: bool = False
    allow_monitor_without_hw_listen_only: bool = False
    socketcan_configure_link: bool = False
    protected_main: list[str] = field(default_factory=lambda: _hexset(METER_ORIGIN_MAIN))
    protected_av: list[str] = field(default_factory=lambda: _hexset(METER_ORIGIN_AV | AV_PEER_IDS))
    allow_protected_tx: bool = False
    # background
    background_profile: str = os.path.join(ROOT, "profiles", "main_idle_baseline.json")
    background_autostart: bool = True
    # logging / ui
    log_max_frames: int = 200_000
    ui_refresh_ms: int = 100
    tracker_stale_ms: int = 1000
    dashboard_units: str = "mph"
    dashboard_source: str = "auto"          # auto | rx | tx+rx
    # AV
    av_auto_start: bool = False
    av_answer_delay_ms: int = 50
    av_ack_timeout_ms: int = 1000
    av_retries: int = 1
    av_peer_timeout_s: float = 3.0
    av_auto_read_settings: bool = True
    # hardware
    esp32_port: str = ""
    esp32_autoconnect: bool = False
    press_ms: int = 200
    # control server
    control_server: bool = True
    control_port: int = 8765
    control_token: str = ""

    def protected(self, bus: str) -> set[int]:
        vals = self.protected_main if bus == "main" else self.protected_av if bus == "av" else []
        out = set()
        for v in vals:
            try:
                out.add(int(str(v), 16))
            except ValueError:
                pass
        return out

    @classmethod
    def path(cls) -> str:
        return os.path.join(config_dir(), "settings.json")

    # Only settings read from disk are written back, so tests/tools that build
    # AppSettings() directly never overwrite the user's file.
    _persist = False

    @classmethod
    def load(cls) -> "AppSettings":
        s = cls()
        s._persist = True
        try:
            with open(cls.path(), encoding="utf-8") as fh:
                data = json.load(fh)
            names = {f.name for f in fields(cls)}
            for k, v in data.items():
                if k in names:
                    setattr(s, k, v)
        except (OSError, ValueError):
            pass
        if not os.path.exists(s.background_profile):
            s.background_profile = cls().background_profile
        return s

    def save(self) -> None:
        if not self._persist:
            return
        with open(self.path(), "w", encoding="utf-8") as fh:
            json.dump(asdict(self), fh, indent=1)
