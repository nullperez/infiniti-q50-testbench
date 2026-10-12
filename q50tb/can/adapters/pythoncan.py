"""python-can backend: SocketCAN on Linux, the in-process virtual bus, and any
other python-can interface (PCAN, slcan, ...).

SocketCAN listen-only is a property of the network link, not the socket, so
monitor mode checks ``ip -details link`` and refuses to open a link that is
not in LISTEN-ONLY mode unless the user lets the app reconfigure it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from typing import Optional

from q50tb.can.adapters.base import (Adapter, AdapterError, AdapterInfo, BusConfig,
                                      ListenOnlyUnsupported)
from q50tb.can.frame import CanFrame, RX
from q50tb.core import timebase

try:
    import can
except Exception:
    can = None


# ── SocketCAN link helpers ──────────────────────────────────────────────
def socketcan_links() -> list[str]:
    if not sys.platform.startswith("linux"):
        return []
    out = []
    base = "/sys/class/net"
    try:
        for name in sorted(os.listdir(base)):
            try:
                with open(os.path.join(base, name, "type")) as f:
                    if f.read().strip() == "280":  # ARPHRD_CAN
                        out.append(name)
            except OSError:
                continue
    except OSError:
        pass
    return out


def socketcan_link_info(name: str) -> dict:
    """{'up': bool, 'listen_only': bool, 'bitrate': int|None, 'fd': bool, 'virtual': bool}"""
    info = {"up": False, "listen_only": False, "bitrate": None, "fd": False, "virtual": False, "raw": ""}
    if not shutil.which("ip"):
        return info
    try:
        res = subprocess.run(["ip", "-details", "-json", "link", "show", name],
                             capture_output=True, text=True, timeout=2)
        data = json.loads(res.stdout)[0]
    except Exception:
        return info
    info["raw"] = json.dumps(data)
    info["up"] = "UP" in data.get("flags", [])
    linkinfo = data.get("linkinfo", {})
    kind = linkinfo.get("info_kind", "")
    info["virtual"] = kind == "vcan"
    idata = linkinfo.get("info_data", {})
    ctrl = idata.get("ctrlmode", [])
    info["listen_only"] = "LISTEN-ONLY" in ctrl
    info["fd"] = "FD" in ctrl
    info["bitrate"] = idata.get("bittiming", {}).get("bitrate")
    return info


def socketcan_configure_commands(name: str, config: BusConfig) -> list[list[str]]:
    args = ["ip", "link", "set", name, "type", "can", "bitrate", str(config.bitrate),
            "listen-only", "on" if config.listen_only else "off"]
    if config.fd:
        args += ["dbitrate", str(config.data_bitrate), "fd", "on"]
    else:
        args += ["fd", "off"]
    return [["ip", "link", "set", name, "down"], args, ["ip", "link", "set", name, "up"]]


def socketcan_configure(name: str, config: BusConfig) -> str:
    """Reconfigure a link; tries direct, then `sudo -n`, then `pkexec`."""
    cmds = socketcan_configure_commands(name, config)
    shell = " && ".join(" ".join(c) for c in cmds)
    for prefix in ([], ["sudo", "-n"], ["pkexec"]):
        if prefix and not shutil.which(prefix[0]):
            continue
        try:
            res = subprocess.run(prefix + ["sh", "-c", shell], capture_output=True, text=True, timeout=60)
        except Exception as exc:
            last = str(exc)
            continue
        if res.returncode == 0:
            return "ok"
        last = res.stderr.strip() or res.stdout.strip()
    raise AdapterError(f"could not configure {name} (run manually: sudo sh -c '{shell}'): {last}")


# ── discovery ───────────────────────────────────────────────────────────
def discover() -> list[AdapterInfo]:
    out: list[AdapterInfo] = []
    if can is None:
        return out
    for link in socketcan_links():
        li = socketcan_link_info(link)
        state = "up" if li["up"] else "down"
        rate = f" {li['bitrate']}bps" if li["bitrate"] else ""
        lo = " LISTEN-ONLY" if li["listen_only"] else ""
        out.append(AdapterInfo(
            backend="socketcan", channel=link,
            name=f"SocketCAN {link} ({state}{rate}{lo})",
            supports_listen_only=True if li["virtual"] or li["listen_only"] else None,
            supports_fd=True, is_virtual=li["virtual"]))
    for i in range(2):
        out.append(AdapterInfo(backend="virtual", channel=f"q50tb-virtual-{i}",
                               name=f"Software virtual bus {i}", supports_listen_only=True,
                               supports_fd=True, is_virtual=True))
    return out


class PythonCanAdapter(Adapter):
    """``info.backend`` is the python-can interface name; ``extra`` may carry kwargs."""

    def __init__(self, info: AdapterInfo):
        super().__init__(info)
        self._bus = None

    def _open(self, config: BusConfig) -> None:
        if can is None:
            raise AdapterError("python-can is not installed")
        backend = self.info.backend
        kwargs = dict(self.info.extra)
        if backend == "socketcan":
            li = socketcan_link_info(self.info.channel)
            needs = (config.listen_only != li["listen_only"] or
                     (li["bitrate"] and li["bitrate"] != config.bitrate) or config.fd != li["fd"])
            if not li["virtual"] and needs:
                if config.configure_link:
                    socketcan_configure(self.info.channel, config)
                    li = socketcan_link_info(self.info.channel)
                elif config.listen_only:
                    cmd = " && ".join(" ".join(c) for c in socketcan_configure_commands(self.info.channel, config))
                    raise ListenOnlyUnsupported(
                        f"{self.info.channel} is not in LISTEN-ONLY mode. Enable 'Configure SocketCAN link' "
                        f"in Settings or run:\n  sudo sh -c '{cmd}'")
            if config.listen_only and not (li["listen_only"] or li["virtual"]):
                raise ListenOnlyUnsupported(f"{self.info.channel} still not LISTEN-ONLY after configuration")
            self._bus = can.Bus(interface="socketcan", channel=self.info.channel,
                                fd=config.fd, receive_own_messages=False, **kwargs)
        elif backend == "virtual":
            self._bus = can.Bus(interface="virtual", channel=self.info.channel,
                                receive_own_messages=False, **kwargs)
        else:
            if config.fd:
                kwargs.setdefault("fd", True)
                kwargs.setdefault("data_bitrate", config.data_bitrate)
            self._bus = can.Bus(interface=backend, channel=self.info.channel,
                                bitrate=config.bitrate, **kwargs)
            if config.listen_only:
                try:
                    self._bus.state = can.BusState.PASSIVE
                except Exception as exc:
                    self._bus.shutdown()
                    self._bus = None
                    raise ListenOnlyUnsupported(f"{backend} cannot enter passive (listen-only) state: {exc}")
        self.stats.bus_state = "listen-only" if config.listen_only else "error-active"

    def _close(self) -> None:
        bus, self._bus = self._bus, None
        if bus is not None:
            bus.shutdown()

    def _send(self, frame: CanFrame) -> None:
        msg = can.Message(arbitration_id=frame.arbitration_id, data=bytes(frame.data),
                          is_extended_id=frame.is_extended, is_fd=frame.is_fd,
                          bitrate_switch=frame.bitrate_switch, is_remote_frame=frame.is_remote)
        try:
            self._bus.send(msg, timeout=0.05)
        except can.CanError as exc:
            self.stats.tx_errors += 1
            raise AdapterError(f"send failed: {exc}") from exc

    def _read(self, timeout: float) -> Optional[CanFrame]:
        bus = self._bus
        if bus is None:
            return None
        msg = bus.recv(timeout=timeout)
        if msg is None:
            return None
        if msg.is_error_frame:
            self.stats.rx_errors += 1
        ts = timebase.from_wall(msg.timestamp) if msg.timestamp else timebase.now()
        # virtual bus timestamps are wall clock too; guard against nonsense
        app_now = timebase.now()
        if abs(ts - app_now) > 5:
            ts = app_now
        return CanFrame(
            arbitration_id=msg.arbitration_id, data=bytes(msg.data),
            is_extended=msg.is_extended_id, is_fd=msg.is_fd, bitrate_switch=msg.bitrate_switch,
            error_state_indicator=msg.error_state_indicator, is_remote=msg.is_remote_frame,
            is_error=msg.is_error_frame, timestamp=ts, direction=RX, source=self.info.key)
