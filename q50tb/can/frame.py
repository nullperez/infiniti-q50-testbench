"""CAN frame value object shared by every layer of the app.

Timestamps use the app timebase (seconds from ``timebase.now()``), so frames
from different adapters, TX loopback and replays line up on one clock.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace

# Valid CAN FD payload lengths (ISO 11898-1:2015). Classical CAN is 0..8.
FD_LENGTHS = (0, 1, 2, 3, 4, 5, 6, 7, 8, 12, 16, 20, 24, 32, 48, 64)

RX = "RX"
TX = "TX"


def fd_padded_length(n: int) -> int:
    """Smallest valid CAN FD length that holds ``n`` bytes."""
    for length in FD_LENGTHS:
        if length >= n:
            return length
    raise ValueError(f"CAN FD payload too long: {n} bytes (max 64)")


def dlc_to_len(dlc: int) -> int:
    table = {9: 12, 10: 16, 11: 20, 12: 24, 13: 32, 14: 48, 15: 64}
    return dlc if dlc <= 8 else table[dlc]


def len_to_dlc(n: int) -> int:
    table = {12: 9, 16: 10, 20: 11, 24: 12, 32: 13, 48: 14, 64: 15}
    return n if n <= 8 else table[n]


@dataclass(slots=True)
class CanFrame:
    arbitration_id: int
    data: bytes = b""
    is_extended: bool = False
    is_fd: bool = False
    bitrate_switch: bool = False
    error_state_indicator: bool = False
    is_remote: bool = False
    is_error: bool = False
    timestamp: float = 0.0
    bus: str = ""          # logical bus role ("main", "av", ...)
    direction: str = RX    # RX or TX (TX = sent by this app)
    source: str = ""       # owner that sent it (TX) or adapter label (RX)
    seq: int = field(default=0, compare=False)  # set by the log buffer

    @property
    def dlc(self) -> int:
        return len(self.data)

    @property
    def id_text(self) -> str:
        return f"{self.arbitration_id:08X}" if self.is_extended else f"{self.arbitration_id:03X}"

    @property
    def data_text(self) -> str:
        return self.data.hex(" ").upper()

    def copy(self, **changes) -> "CanFrame":
        return replace(self, **changes)

    def validate(self) -> None:
        """Raise ValueError if the frame violates CAN / CAN FD framing rules."""
        limit = 0x1FFFFFFF if self.is_extended else 0x7FF
        if not 0 <= self.arbitration_id <= limit:
            raise ValueError(f"ID 0x{self.arbitration_id:X} out of range for "
                             f"{'extended' if self.is_extended else 'standard'} frame")
        n = len(self.data)
        if self.is_fd:
            if self.is_remote:
                raise ValueError("CAN FD has no remote frames")
            if n not in FD_LENGTHS:
                raise ValueError(f"CAN FD length {n} is not one of {FD_LENGTHS}")
        else:
            if self.bitrate_switch or self.error_state_indicator:
                raise ValueError("BRS/ESI flags only exist on CAN FD frames")
            if n > 8:
                raise ValueError(f"classical CAN frame carries at most 8 bytes (got {n})")

    def __str__(self) -> str:
        flags = "FD " if self.is_fd else ""
        return f"{self.bus}:{self.direction} {self.id_text} [{self.dlc}] {flags}{self.data_text}"
