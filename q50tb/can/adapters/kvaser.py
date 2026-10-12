"""Kvaser CANlib backend (Windows and Linux).

Monitor mode uses the driver's SILENT output control: the controller neither
transmits nor drives ACK bits. If the channel lacks SILENT_MODE capability
the open fails with ListenOnlyUnsupported (virtual channels excepted).
"""

from __future__ import annotations

from typing import Optional

from q50tb.can.adapters.base import (Adapter, AdapterError, AdapterInfo, BusConfig,
                                      ListenOnlyUnsupported)
from q50tb.can.frame import CanFrame, RX
from q50tb.core import timebase

try:
    from canlib import canlib, Frame as KvFrame
except Exception:  # canlib or the Kvaser driver is missing
    canlib = None
    KvFrame = None


def available() -> bool:
    if canlib is None:
        return False
    try:
        canlib.getNumberOfChannels()
        return True
    except Exception:
        return False


def discover() -> list[AdapterInfo]:
    if not available():
        return []
    out = []
    for i in range(canlib.getNumberOfChannels()):
        try:
            cd = canlib.ChannelData(i)
            cap = int(cd.channel_cap)
            name = cd.channel_name
            serial = str(cd.card_serial_no)
        except Exception as exc:
            name, cap, serial = f"Kvaser channel {i} ({exc})", 0, ""
        is_virtual = bool(cap & int(canlib.ChannelCap.VIRTUAL))
        out.append(AdapterInfo(
            backend="kvaser", channel=str(i), name=name, serial=serial,
            supports_listen_only=bool(cap & int(canlib.ChannelCap.SILENT_MODE)) or is_virtual,
            supports_fd=bool(cap & int(canlib.ChannelCap.CAN_FD)),
            is_virtual=is_virtual,
        ))
    return out


_CLASSIC_RATES = {
    1_000_000: "BITRATE_1M", 500_000: "BITRATE_500K", 250_000: "BITRATE_250K",
    125_000: "BITRATE_125K", 100_000: "BITRATE_100K", 83_333: "BITRATE_83K",
    62_500: "BITRATE_62K", 50_000: "BITRATE_50K", 10_000: "BITRATE_10K",
}
_FD_ARB = {500_000: "BITRATE_500K_80P", 1_000_000: "BITRATE_1M_80P"}
_FD_DATA = {1_000_000: "BITRATE_1M_80P", 2_000_000: "BITRATE_2M_80P",
            4_000_000: "BITRATE_4M_80P", 8_000_000: "BITRATE_8M_60P"}


class KvaserAdapter(Adapter):
    def __init__(self, info: AdapterInfo):
        super().__init__(info)
        self._ch = None
        self._ts_offset: Optional[float] = None
        self._last_status_poll = 0.0

    def _open(self, config: BusConfig) -> None:
        if canlib is None:
            raise AdapterError("Kvaser CANlib is not installed (pip install canlib + Kvaser drivers)")
        if config.listen_only and self.info.supports_listen_only is False:
            raise ListenOnlyUnsupported(
                f"{self.info.name} has no SILENT mode; monitor mode would ACK frames on the bus")
        flags = canlib.Open.ACCEPT_VIRTUAL
        if config.fd:
            flags |= canlib.Open.CAN_FD
        try:
            ch = canlib.openChannel(int(self.info.channel), flags=flags)
        except canlib.CanError as exc:
            raise AdapterError(f"openChannel({self.info.channel}) failed: {exc}") from exc
        try:
            if config.fd:
                arb = _FD_ARB.get(config.bitrate)
                data = _FD_DATA.get(config.data_bitrate)
                if not arb or not data:
                    raise AdapterError(f"unsupported CAN FD bitrates {config.bitrate}/{config.data_bitrate}")
                ch.setBusParams(getattr(canlib.BitrateFD, arb))
                ch.setBusParamsFd(getattr(canlib.BitrateFD, data), 0, 0, 0)
            else:
                name = _CLASSIC_RATES.get(config.bitrate)
                if name:
                    ch.setBusParams(getattr(canlib.Bitrate, name))
                else:
                    # 16 time quanta: sync 1 + tseg1 12 + tseg2 3 -> sample point 81.25 %
                    ch.setBusParams(config.bitrate, 12, 3, 1, 1)
            if config.listen_only:
                try:
                    ch.setBusOutputControl(canlib.Driver.SILENT)
                except canlib.CanError as exc:
                    if not self.info.is_virtual:
                        raise ListenOnlyUnsupported(f"SILENT mode refused by driver: {exc}") from exc
            else:
                ch.setBusOutputControl(canlib.Driver.NORMAL)
            ch.busOn()
        except Exception:
            try:
                ch.close()
            except Exception:
                pass
            raise
        self._ch = ch
        self._ts_offset = None
        self.stats.bus_state = "listen-only" if config.listen_only else "error-active"

    def _close(self) -> None:
        ch, self._ch = self._ch, None
        if ch is not None:
            try:
                ch.busOff()
            finally:
                ch.close()

    def _send(self, frame: CanFrame) -> None:
        flags = canlib.MessageFlag.EXT if frame.is_extended else canlib.MessageFlag.STD
        if frame.is_fd:
            flags |= canlib.MessageFlag.FDF
            if frame.bitrate_switch:
                flags |= canlib.MessageFlag.BRS
        if frame.is_remote:
            flags |= canlib.MessageFlag.RTR
        try:
            self._ch.write(KvFrame(id_=frame.arbitration_id, data=bytes(frame.data),
                                   dlc=len(frame.data), flags=flags))
        except canlib.CanError as exc:
            self.stats.tx_errors += 1
            raise AdapterError(f"write failed: {exc}") from exc

    def _read(self, timeout: float) -> Optional[CanFrame]:
        ch = self._ch
        if ch is None:
            return None
        try:
            kf = ch.read(timeout=int(timeout * 1000))
        except canlib.CanNoMsg:
            self._poll_status()
            return None
        except canlib.CanError as exc:
            raise AdapterError(str(exc)) from exc
        app_now = timebase.now()
        if app_now - self._last_status_poll > 0.5:      # busy bus: still notice error-passive / bus-off
            self._last_status_poll = app_now
            self._poll_status()
        flags = int(kf.flags)
        mf = canlib.MessageFlag
        if flags & int(mf.TXACK):
            return None
        hw = kf.timestamp / 1000.0 if kf.timestamp is not None else None
        ts = app_now
        if hw is not None:
            offset = app_now - hw
            if self._ts_offset is None or offset < self._ts_offset or offset - self._ts_offset > 0.5:
                self._ts_offset = offset
            ts = hw + self._ts_offset
        is_err = bool(flags & int(mf.ERROR_FRAME))
        if is_err:
            self.stats.rx_errors += 1
        return CanFrame(
            arbitration_id=kf.id, data=bytes(kf.data or b""),
            is_extended=bool(flags & int(mf.EXT)), is_fd=bool(flags & int(mf.FDF)),
            bitrate_switch=bool(flags & int(mf.BRS)), error_state_indicator=bool(flags & int(mf.ESI)),
            is_remote=bool(flags & int(mf.RTR)), is_error=is_err,
            timestamp=ts, direction=RX, source=self.info.key)

    def _poll_status(self) -> None:
        try:
            st = self._ch.readStatus()
        except Exception:
            return
        s = canlib.Stat
        if st & s.BUS_OFF:
            state = "bus-off"
        elif st & s.ERROR_PASSIVE:
            state = "error-passive"
        elif st & s.ERROR_WARNING:
            state = "error-warning"
        else:
            state = "listen-only" if self.config and self.config.listen_only else "error-active"
        if state != self.stats.bus_state:
            self.stats.bus_state = state
            self._status("warning" if state != "error-active" else "info", f"bus state {state}")
