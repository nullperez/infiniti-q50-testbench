# Notes for AI agents

## Project status (2026-10-12)
The whole app (`q50tb/`, PySide6) was written on Windows 11 in one session. Verified there:
`pytest tests` (virtual buses, a software virtual meter, Kvaser *virtual* channels), every GUI page
rendered offscreen, the GUI driven through the control server and the MCP server.

**Never run yet:** Linux/SocketCAN (see `tests/test_socketcan_vcan.py`), a physical Kvaser adapter,
the real meter, the ESP32 firmware in `firmware/esp32_bench/` (its GPIO table is a placeholder - the owner
must copy pins from the old firmware's LIST output; ACC needs a new high-side relay).

Unconfirmed protocol items (labelled *candidate* in code/UI; details in `docs/AV_PROTOCOL.md` §8):
display-effect write format, language codes 02/03, settings item 8 byte meanings, nav maneuver codes 1-5,
which heading value is north, media header byte 9, source codes 0x20/0x55/USB/iPod/BT.

The owner's source documents (`Q50_Observed_Signals_and_Behaviors.md`, `HANDOFF_AV_BUS_AND_BENCH.md`,
`Q50_TestBench_Requirements.txt`) are gitignored; everything derived from them is in
`q50tb/signals/q50_main.py` (signals, with notes/confidence) and `docs/AV_PROTOCOL.md`. Ask the owner
for them if you need the originals.

## Linux setup and tests
```
python3 -m venv .venv && .venv/bin/python -m pip install -r requirements-dev.txt
sudo bash tools/setup_vcan.sh          # vcan0 + vcan1 for the SocketCAN tests (optional)
.venv/bin/python -m pytest -q -rs tests
bash run.sh                            # GUI (run.sh is not executable after a Windows commit)
```
* Python 3.10+ (frame dataclass uses `slots=True`).
* Skips are expected without vcan (`test_socketcan_vcan.py`), without Kvaser linuxcan (`test_kvaser_virtual_channels`)
  or without `reference_logs/` (`test_capture_roundtrip`).
* Qt needs system libs even offscreen, e.g. Ubuntu: `sudo apt install libegl1 libgl1 libxkbcommon0 libfontconfig1
  libdbus-1-3` (plus `libxcb-cursor0` for a real display). `tools/screenshot_pages.py <dir>` renders all pages.
* `.mcp.json` points at `.venv/Scripts/python.exe` (Windows). On Linux change it to `.venv/bin/python`.
* Real SocketCAN monitor mode needs the link in listen-only: `ip link set can0 type can bitrate 500000 listen-only on`
  (the app refuses monitor mode otherwise, or reconfigures it if Settings allows sudo/pkexec).

## Driving the bench
Use the MCP server `q50-bench` (see README → AI agents). Call `help`, then `status`.
Typical: `list_adapters` → `start_buses(main, av)` → `bench_set('BPLUS', true)`, `bench_set('IG', true)` →
`set_signal(...)` → `get_dashboard` / `log_tail` → `av_start` → `av_wait_ready` → `av_set_page(...)`.

Rules (from the owner's handoff, enforced in code where possible):
1. Never run bench (TX) mode on a real vehicle; there use `start_buses(..., monitor=true)`.
2. Never transmit meter-originated IDs (the bus manager refuses them).
3. Report AV success only from the meter's response (`result: confirmed`); timeouts/rejections are failures.
4. Missing/stale data is not zero (`stale: true`, `value: null`).
5. Keep simulated speed at 0 unless asked (it moves the odometer).
6. Factory reset needs the user's explicit confirmation (`av_factory_reset(confirm=true)`).

## Changing the code
* Layers: can → core → capture/signals → sim/av/hw → control → ui. Lower layers never import higher ones.
* Signals: `q50tb/signals/q50_main.py`. Keep confidence words; don't upgrade candidates without evidence.
* AV protocol knowledge and evidence: `docs/AV_PROTOCOL.md`; editable codes in `registry/`.
* Every transmit goes through `BusChannel.send` (monitor gate, protected IDs, ownership) — don't bypass it.
* Tests build `AppSettings()` directly, which never writes the user's settings file; only `AppSettings.load()` persists.
* Run `python -m pytest -q tests` after changes.
