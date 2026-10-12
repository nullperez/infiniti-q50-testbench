"""Find CAN adapters on this computer and build Adapter objects for them."""

from __future__ import annotations

from q50tb.can.adapters import kvaser, pythoncan
from q50tb.can.adapters.base import Adapter, AdapterError, AdapterInfo


def discover_adapters() -> list[AdapterInfo]:
    found: list[AdapterInfo] = []
    for fn in (kvaser.discover, pythoncan.discover):
        try:
            found.extend(fn())
        except Exception:
            continue
    return found


def create_adapter(info: AdapterInfo) -> Adapter:
    if info.backend == "kvaser":
        return kvaser.KvaserAdapter(info)
    return pythoncan.PythonCanAdapter(info)


def adapter_from_key(key: str, known: list[AdapterInfo] | None = None) -> AdapterInfo:
    """'kvaser:0' / 'socketcan:can0' / 'virtual:x' / 'pcan:PCAN_USBBUS1' -> AdapterInfo."""
    for info in known or discover_adapters():
        if info.key == key:
            return info
    backend, _, channel = key.partition(":")
    if not channel:
        raise AdapterError(f"bad adapter key {key!r} (expected backend:channel)")
    return AdapterInfo(backend=backend, channel=channel, name=key,
                       is_virtual=backend == "virtual",
                       supports_listen_only=True if backend == "virtual" else None)
