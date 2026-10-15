# Q50 TestBench

Bench-test and monitoring app for my 2019 Infiniti Q50 combination meter (`248RL-6HH0E`).
Python 3.10+ / PySide6, Windows and Linux (Ubuntu). CAN through **Kvaser CANlib** or **SocketCAN**
(and any python-can interface); a software virtual bus works with no hardware.

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
| Hardware | ESP32: B+, IG (+ACC, tied to IG on the bench), latched inputs, momentary buttons, meter power cycle, device log. |
| CAN Send | Classic/FD frames (FD only on an FD bus), periodic queue, rolling counter, checksum, byte sweeps (walking bit / counter / custom, dwell, loop), save/load queue. |
| Raw Log | Live table with pause, clear, export (CSV/pcapng/candump), ID filter (`359, 300`, prefix `35`, exclude `-6A8`), direction and bus filters, recording to pcapng with markers. |
| Background Messages | The periodic traffic of a real idling car (learned from `idle.pcapng`): enable/disable (all on by default), period, per-byte overrides that keep counters/checksums running, load/save profiles, learn a profile from any capture. |
| Replay | Two tracks (e.g. main + AV capture) started together on two buses, per-track excluded IDs, time window, 100-frame preview with excluded frames greyed out, speed and loop. |
| Dashboard | Virtual cluster: tach, speed, coolant, fuel, gear, drive mode, key prompt, cruise/ADAS, doors, TPMS, steering wheel, telltales, AV nav/audio/clock. Grey = no data/stale (never a fake zero). |
| Message Tracker | Watched IDs with per-byte colours (grey waiting, green unchanged, yellow changed last frame, orange changed since reset, slate-blue stale). |
| Signal Tracker | Upload a `.dbc` (or use the built-in database) and watch decoded values; trend plot; export the built-in database as `q50_main.dbc`. |
| AV Debug | Reassembled AV transactions with decoding (works in monitor mode), record composer with templates, raw frame injector, link timing, channel configuration, paste-and-decode. |

## AV head unit in 60 seconds

Protocol details and evidence: [docs/AV_PROTOCOL.md](docs/AV_PROTOCOL.md).

// TODO - finish readme

