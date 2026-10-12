"""Clock records.

0x603 (meter channel): ``40 0D <mode> <HH> <MM>`` - once a minute and on change.
  mode bit0 = 24-hour display (03 seen after the owner set 24 h, 02 at 9:25 AM),
  bit1 = normally set; clear it (01) to announce a 12/24 h change. Bench 2026-10-12: 02 -> 03 alone
  left the meter in 12 h; 01 then 03 switched it to 24 h.
  Hours below 10 use a blank tens digit: 9 -> F9 (seen in 12 h mode).
0x61D (car peer channel): ``24 4D <YYYY> <MM> <DD> <hh> <mm> <ss> <dow>`` packed BCD,
  once a second; dow 0=Sunday (Fri=05, Tue=02, Wed=03 in captures).
"""

from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional

from q50tb.av.services.common import bcd, bcd_blank_tens
from q50tb.core.scheduler import Job


@dataclass
class ClockState:
    use_pc_time: bool = True
    offset: dt.timedelta = dt.timedelta(0)    # applied to PC time when a custom time is set
    hour24: bool = False
    flag_bit1: bool = True
    blank_tens: bool = True
    auto_send: bool = True                    # resend 0x603 each minute
    send_datetime_61d: bool = False           # 0x61D date/time stream (car peer channel)


class ClockService:
    def __init__(self, host):
        self.host = host
        self.state = ClockState()
        self._job: Optional[Job] = None
        self._last_minute: Optional[tuple] = None
        self._sent_hour24: Optional[bool] = None   # mode the meter was last given (None = not yet)

    def now(self) -> dt.datetime:
        return dt.datetime.now() + self.state.offset

    def set_time(self, when: dt.datetime) -> None:
        self.state.offset = when - dt.datetime.now()
        self.state.use_pc_time = abs(self.state.offset.total_seconds()) < 1
        self.send_now()

    def use_pc_time(self) -> None:
        self.state.offset = dt.timedelta(0)
        self.state.use_pc_time = True
        self.send_now()

    def clock_record(self, t: Optional[dt.datetime] = None, announce: bool = False) -> bytes:
        """The hour is always the 24 h value (car: 00:29 -> F0, 09:25 -> F9 in 12 h
        mode); the mode bit tells the meter how to display it."""
        t = t or self.now()
        mode = (0x01 if self.state.hour24 else 0x00) | (0x02 if self.state.flag_bit1 and not announce else 0x00)
        hh = bcd_blank_tens(t.hour) if self.state.blank_tens else bcd(t.hour)
        return bytes([0x40, 0x0D, mode, hh, bcd(t.minute)])

    def datetime_record(self, t: Optional[dt.datetime] = None) -> bytes:
        t = t or self.now()
        dow = (t.weekday() + 1) % 7
        return bytes([0x24, 0x4D, bcd(t.year // 100), bcd(t.year % 100), bcd(t.month), bcd(t.day),
                      bcd(t.hour), bcd(t.minute), bcd(t.second), dow])

    def _send_clock(self, t: Optional[dt.datetime], label: str) -> None:
        if self.state.hour24 != self._sent_hour24:
            self.host.send("status", self.clock_record(t, announce=True), label="clock (12/24 h change)")
            self._sent_hour24 = self.state.hour24
        self.host.send("status", self.clock_record(t), label=label)

    def send_now(self) -> None:
        self._send_clock(None, "clock")
        if self.state.send_datetime_61d:
            self.host.send("source", self.datetime_record(), label="date/time",
                           expect_ack=self.host.channel_has_peer("source"))
        t = self.now()
        self._last_minute = (t.hour, t.minute)

    def start(self) -> None:
        self.stop()
        self._sent_hour24 = None     # new session: announce the mode again
        self._job = self.host.scheduler.every(1.0, self._tick, name="av-clock")

    def stop(self) -> None:
        if self._job:
            self._job.cancel()
            self._job = None

    def _tick(self) -> None:
        if not self.host.running:
            return
        t = self.now()
        if self.state.send_datetime_61d and self.host.channel_enabled("source"):
            self.host.send("source", self.datetime_record(t), label="date/time",
                           expect_ack=self.host.channel_has_peer("source"))
        if self.state.auto_send and self.host.channel_ready("status") and (t.hour, t.minute) != self._last_minute:
            self._last_minute = (t.hour, t.minute)
            self._send_clock(t, "clock (minute)")
