"""SocketCAN link checks (link info is faked, so no CAN hardware or root is needed)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from q50tb.can.adapters import pythoncan  # noqa: E402
from q50tb.can.adapters.base import AdapterError, AdapterInfo, BusConfig, ListenOnlyUnsupported  # noqa: E402

INFO = AdapterInfo("socketcan", "can0", "SocketCAN can0")


def _link(**kw):
    li = {"up": True, "listen_only": False, "bitrate": 500_000, "fd": False, "virtual": False, "supported": None, "raw": ""}
    li.update(kw)
    return li


@pytest.fixture
def fake(monkeypatch):
    state = {"link": _link(), "configured": [], "opened": []}
    monkeypatch.setattr(pythoncan, "socketcan_link_info", lambda name: state["link"])

    def configure(name, config):
        state["configured"].append(config)
        state["link"] = _link(listen_only=config.listen_only, bitrate=config.bitrate, fd=config.fd)

    monkeypatch.setattr(pythoncan, "socketcan_configure", configure)
    monkeypatch.setattr(pythoncan.can, "Bus", lambda **kw: state["opened"].append(kw) or object())
    return state


def test_matching_link_opens(fake):
    pythoncan.PythonCanAdapter(INFO)._open(BusConfig())
    assert fake["opened"] and not fake["configured"]


@pytest.mark.parametrize("link", [_link(up=False), _link(bitrate=250_000), _link(bitrate=None),
                                  _link(listen_only=True)])
def test_mismatched_link_refused_in_bench_mode(fake, link):
    fake["link"] = link
    with pytest.raises(AdapterError, match="sudo sh -c"):
        pythoncan.PythonCanAdapter(INFO)._open(BusConfig())
    assert not fake["opened"]


def test_monitor_refused_without_listen_only(fake):
    with pytest.raises(ListenOnlyUnsupported):
        pythoncan.PythonCanAdapter(INFO)._open(BusConfig(listen_only=True))
    assert not fake["opened"]


def test_configure_link_fixes_down_link(fake):
    fake["link"] = _link(up=False, bitrate=None)
    pythoncan.PythonCanAdapter(INFO)._open(BusConfig(listen_only=True, configure_link=True))
    assert fake["configured"] and fake["opened"]


def test_vcan_down_refused(fake):
    fake["link"] = _link(up=False, virtual=True)
    with pytest.raises(AdapterError, match="down"):
        pythoncan.PythonCanAdapter(AdapterInfo("socketcan", "vcan0", "v", is_virtual=True))._open(BusConfig())
