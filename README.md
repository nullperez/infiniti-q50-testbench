# Q50 TestBench

Bench-test and monitoring app for the 2019 Infiniti Q50 combination meter (`248RL-6HH0E`).
Python 3.10+ / PySide6, Windows and Linux (Ubuntu). CAN through **Kvaser CANlib** or **SocketCAN**
(and any python-can interface); a software virtual bus works with no hardware.

## Start

```
run.bat            (Windows)       ./run.sh           (Linux)
```
Both create `.venv` and install `requirements.txt` on first run, then start `python -m q50tb`.

Linux notes: Kvaser needs the `linuxcan` driver; SocketCAN links are configured with
`ip link set can0 type can bitrate 500000 [listen-only on]` (or let the app do it via Settings →
"Let the app configure SocketCAN links", which uses sudo/pkexec).

## Safety model

* **MONITORING MODE ONLY** (checkbox next to *Start bus*): adapters open in hardware listen-only (Kvaser
  SILENT, SocketCAN `listen-only on`) — nothing is transmitted or ACKed. The channels also have no software
  transmit path and every transmitting page is disabled. If the hardware cannot do listen-only the app
  refuses to start, unless you explicitly allow software-only monitoring in Settings (that still ACKs).
* Bench mode asks for confirmation before it starts transmitting.
* Meter-originated IDs (main 0x280/0x2DE/0x355/0x5C5, AV 0x683/0x6A8/0x6A9 and the other AV peers) are
  never transmitted (Settings → Safety).
* One transmitter per CAN ID per bus: a send-queue entry or a replay takes an ID over from the background
  messages while active, and gives it back afterwards.
* Settings writes on the AV bus succeed only when the meter echoes the requested value.

## Pages

| Page | What it does |
|---|---|
| Controls | Every documented signal: RPM/speed sliders, coolant, gear radio buttons, doors laid out like the car, lights (with blinking turn signals), warnings incl. the ABS/BRAKE sequences, key prompts, TPMS per tire, ADAS/cruise, chassis/steering, HVAC, and an auto-generated **All signals** tab. Hover any control for bit position, confidence and notes. |
| AV Controls | Master switch for head-unit emulation, AV adapter selector, channel states, page enable/disable, preferences (display effect, outside-temp alert, language, pressure unit, factory reset), audio sources with per-source metadata, clock (PC time / set time / 12-24 h), driver break alert, navigation instructions and a route simulator. |
| Hardware | ESP32: B+/IG/ACC, latched inputs, momentary buttons, meter power cycle, device log. |
| CAN Send | Classic/FD frames (FD only on an FD bus), periodic queue, rolling counter, checksum, byte sweeps (walking bit / counter / custom, dwell, loop), save/load queue. |
| Raw Log | Live table with pause, clear, export (CSV/pcapng/candump), ID filter (`359, 300`, prefix `35`, exclude `-6A8`), direction and bus filters, recording to pcapng with markers. |
| Background Messages | The periodic traffic of a real idling car (learned from `idle.pcapng`): enable/disable (all on by default), period, per-byte overrides that keep counters/checksums running, load/save profiles, learn a profile from any capture. |
| Replay | Two tracks (e.g. main + AV capture) started together on two buses, per-track excluded IDs, time window, 100-frame preview with excluded frames greyed out, speed and loop. |
| Dashboard | Virtual cluster: tach, speed, coolant, fuel, gear, drive mode, key prompt, cruise/ADAS, doors, TPMS, steering wheel, telltales, AV nav/audio/clock. Grey = no data/stale (never a fake zero). |
| Message Tracker | Watched IDs with per-byte colours (grey waiting, green unchanged, orange changed last frame, yellow changed since reset, slate-blue stale). |
| Signal Tracker | Upload a `.dbc` (or use the built-in database) and watch decoded values; trend plot; export the built-in database as `q50_main.dbc`. |
| AV Debug | Reassembled AV transactions with decoding (works in monitor mode), record composer with templates, raw frame injector, link timing, channel configuration, paste-and-decode. |

## AV head unit in 60 seconds

1. Power the meter including **ACC** (M57 pin 14) and start the bus with both main and AV adapters.
2. Keep background messages running (0x51C + 0x4F6 make AV page slots exist; 0x385 for TPMS).
3. AV Controls → tick **ENABLE AV CONTROLS**. Channels go `waiting → announced → negotiating → ready`
   when the meter announces; the meter re-announces until answered. If a channel stays `waiting`,
   power-cycle the meter (Hardware page) with the emulation running.
4. Settings are read automatically; change pages/preferences from there.

Protocol details and evidence: [docs/AV_PROTOCOL.md](docs/AV_PROTOCOL.md).

## ESP32 firmware

`firmware/esp32_bench/` is ESP-IDF firmware speaking the same line protocol as the original esp32-bench
(PING/LIST/STATUS/SET/PRESS/ALLOFF, `EVT` lines) plus an **ACC** channel. **Set the GPIO table in
`main/bench_main.c` to your wiring before flashing** — connect the old firmware first and read the pins
from the Hardware page (LIST). ACC needs its own high-side relay. Build: `idf.py set-target esp32 build flash`.
The client is `q50tb/hw/esp32_bench.py` (your original API, extended); older firmware without ACC still works.

## AI agents (MCP)

The GUI runs a local JSON-RPC control server (127.0.0.1:8765). The MCP server exposes every method in
`q50tb/control/api.py` as a tool (61 today: buses, signals, dashboard, background, send queue, log, tracker,
record/replay, AV head unit, ESP32).

* Claude Code in this folder picks up `.mcp.json` automatically (Windows path; on Linux change the command
  to `.venv/bin/python`).
* Other clients: `"command": "<repo>/.venv/Scripts/python", "args": ["-m", "q50tb.control.mcp_server"]`.
* `--headless` runs the bench without the GUI.

Agents should call `help` first; safety rules are in the tool descriptions.

## Code layout

```
q50tb/
  can/        frame, adapters (kvaser, python-can), discovery
  core/       bus_manager (monitor gate, protected IDs, ownership), scheduler, log buffer,
              send_queue, replay, tracker, recorder, config, timebase
  capture/    pcapng / CSV / candump readers & writers
  signals/    model (bit segments), q50_main (database), vehicle_state, dashboard, dbc
  sim/        background messages, baseline builder, effects (blinkers)
  av/         transport, protocol, headunit, sniffer, virtual_meter, services/{settings,audio,navigation,clock}
  hw/         esp32_bench client, bench_io worker
  control/    api (tool registry), server (JSON-RPC), mcp_server
  ui/         main_window, pages/ (one file per page), widgets/
registry/     av_sources.json, nav_maneuvers.json  (editable labels/codes)
profiles/     background-message profiles
tools/        build_baseline.py, screenshot_pages.py
tests/        pytest suite (virtual buses + virtual meter; Kvaser virtual channels if installed)
```

Lower layers never import higher ones, so a change to one page or service stays local.

**Adding things**

* New signal → add a `SignalDef` in `q50tb/signals/q50_main.py`. It appears in Controls → All signals, the
  dashboard decoder, the API (`set_signal`) and the DBC export.
* New curated control → one line in the right tab of `ui/pages/controls_page.py` using `SignalBinder`.
* New audio source / maneuver label → edit the JSON in `registry/`.
* New agent capability → add a method with `@tool` in `control/api.py`.
* New page → subclass `BasePage` in `ui/pages/` and add it to `NAV` in `ui/main_window.py`.

## Tests

```
.venv/Scripts/python -m pip install -r requirements-dev.txt
.venv/Scripts/python -m pytest -q tests
```
Covers the AV handshake/settings/fragmentation against a virtual meter, monitor-mode never transmitting,
ownership/protected IDs, the whole API headless, codecs, and capture round trips.

## Evidence levels

Everything decoded here comes from the owner's README, the handoff notes and capture analysis.
Each signal and AV item carries `confirmed / documented / probable / candidate` and the UI shows it.
Candidates are usable for experiments but are not facts.
