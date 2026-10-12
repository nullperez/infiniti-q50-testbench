# Handoff: AV bus and Bench I/O — what the Q50 meter needs

Last updated: 2026-10-11. A short orientation for an agent working on the
Infiniti Q50 combination meter bench (`248RL-6HH0E`). It says what the meter
requires and where the details live. Detailed evidence: `AGENTS.md`,
`docs/AV_PROTOCOL.md`, `docs/AV_CONTROL_ARCHITECTURE.md`,
`docs/ESP32_BENCH_API.md`.

Confidence words keep their project meaning: **confirmed** (isolated on the
bench or clean capture correlation), **probable**, **candidate**. Do not
upgrade them.

## 1. Physical setup the meter needs

| Need | Detail |
|---|---|
| Power | **B+** and **IG** (switched by the ESP32), plus **ACC** (M57 pin 14, *not* on the ESP32). Without ACC the meter runs but AV communication and AV-backed pages stay dead |
| Main CAN | `can0`, 500 kbit/s classical CAN, driven by CANSim's main-bus simulator |
| AV CAN | `can1`, 500 kbit/s classical CAN, a separate bus. Needs correct termination; a lone unacknowledged transmitter only shows its own TX echo |
| Bench I/O | ESP32 on `/dev/ttyUSB0`: B+/IG relays, hard-wired warning inputs, physical buttons |

Verify which interface is which before transmitting; it has changed before.

## 2. Bench I/O (ESP32) in brief

Full API: `docs/ESP32_BENCH_API.md`.

- **Power behaviour:**
  - Either B+ or IG alone keeps the meter powered. A real reboot needs both off for about 5 s.
  - A full B+ cycle clears page registration and latched faults (for example the Headlight System Error triangle, or the tach stuck at 0). An IG-only cycle does not.
  - Some AV startup announcements happen only once after the meter boots (see §4).
- **Inputs (latched):** security, alternator, airbag, washer/brake fluid,
  parking brake, both belts, and LH/RH headlight feedback.
  - The ULN2803 drivers only pull lines to ground.
  - A headlight input that is off while main-bus 0x60D byte 0 bit 1 (headlight on) is set raises a latched "Headlight System Error". It clears only with that bit set and both inputs on, or with a B+ cycle.
- **Buttons (momentary):** trip reset, paddles, brightness up/down, and steering switch B Left/Right.
  - **STSW B Right/Left page the centre display.** This is how to change pages; page changes are not a CAN command.
  - OK, Back, steering switch A and ACC are not available.
  - Paddle presses appear as meter-origin main-bus 0x2DE byte 0 (`0x10` up, `0x20` down).
- **Ownership:** CANSim's Bench I/O tab holds the serial port. Other code
  must go through CANSim's control server (`bench_set`, `bench_press`, …) or
  run with CANSim closed.
- `EVT READY` from the ESP32 means it rebooted with everything off, so the meter lost power.

## 3. AV bus roles and IDs

The emulator plays the **head unit (HU)**. The meter answers on the paired
higher ID. Never transmit meter-owned IDs.

| HU sends | Meter sends | Role |
|---|---|---|
| `0x628` | `0x6A8` | Page settings, preferences, audio display records (main working channel) |
| `0x629` | `0x6A9` | Navigation instruction records (confirmed); startup status |
| `0x603` | `0x683` | Time/general status (12/24 h clock record `40 0D MM HH mm`) |
| `0x61D` | `0x69D` | Source selection and clock (`24 4D` packed-BCD date/time) |
| `0x606` | `0x686` | Capability/session prerequisite |
| `0x62C` | `0x6AC` | Identity/status startup prerequisite |
| `0x601` | `0x681` | Steering/panel buttons: meter reports events on `0x681` |
| `0x62B` | `0x6AB` | Touch/UI family (not decoded) |

Do not assume every pair works the same way. Direction comes from captures or bench tests.

## 4. Transport and session behaviour the meter expects

- **Framing:** ISO-TP-like with an extra **transaction token** byte.
  - **First frame:** `10 LL TT …`. LL counts the token. Example: `10 0C 50 C0 20 …` carries token `50`.
  - **Flow control:** the receiver answers `30 00 0A FF…`.
  - **Consecutive frames:** `21 …`, `22 …`, and so on.
  - **Completion ACK:** `E0 TT FF…`, sent by the receiver for every valid complete message.
  - **Single frame:** `0L TT …`.
  - The sender must honour flow control. Tokens roll (bench HU: `10 → 70 → 10`) and are independent per direction.
  - Larger records, such as audio, add a second layer: 20-byte application fragments whose token low nibble is the fragment index, with bit 3 set while more fragments follow.
- **Presence:** each open HU channel sends `F0 80 0A 01 FF FF FF FF` about
  every 500 ms. The meter's heartbeat is `F1 xx 0A 01 …`.
- **Opening a channel:**
  1. The meter announces with `00 20 …`.
  2. The HU ACKs it and answers with `00 21`, then runs that channel's captured startup chain.
  3. Channel states: unseen → announced → acknowledged → negotiating → ready.
- **One-shot announcements:** `0x686`, `0x69D` and `0x6AC` announce **only in a short window after the meter boots**. Starting the HU after that window leaves those channels silent.
  - Fix: keep the HU emulation running and cycle the meter's power.
  - CANSim also starts the real HU's background streams (606 `26 4D 00`, 1 Hz 62C, 61D clock) itself after 2 s of meter heartbeat with no announcement (`av_headunit/baseline.py`, not yet tested on the meter).
- **Timing:** real HUs answered announcements within 50–130 ms in the 092926 captures. One earlier bench capture used delays of several seconds. CANSim now uses the short values; that is untested on the meter.
- **Clock on 0x61D:** send it only in the receive window just after a meter `F1 F4 0A 01` heartbeat. An unsolicited transfer stalled the channel.

## 5. Meter page settings (the core feature)

- Main-bus prerequisite: **0x51C + 0x4F6** (500 ms each) make AV-backed page
  slots exist. They are availability, not the preference bitmap. TPMS content
  also needs a valid 0x385 progression.
- Read: HU `C0 21` → meter `C0 2C 00 S0…S8` (a 9-byte image; typical
  `55 DD 5F FD FD C0 00 00 00`).
- Write: HU `C0 20 S0…S8` → meter returns `C0 2C 00 <image>`.
  - **Success = the returned image equals the requested one.** An `E0` ACK or a sent frame is not success.
  - An unchanged image means the write was rejected. Check session readiness before touching the bitmap.
- Always read the live image, change **one mask**, and keep every other bit.
  Confirmed masks:

  | Page | Byte | Mask |
  |---|---:|---:|
  | Range | S0 | 0x01 |
  | Trip Distance | S0 | 0x04 |
  | Average Speed | S0 | 0x10 |
  | Driving Aids | S0 | 0x40 |
  | Tire Pressure | S1 | 0x01 |
  | Fuel Economy | S1 | 0x10 |
  | Navigation | S2 | 0x10 |
  | Audio | S2 | 0x40 |
  | Blank | S3 | 0x01 |
  | Chassis Control | S4 | 0x01 |

- Preferences on the same channel: outside-temp alert (`C0 81`/`C0 80`),
  display effect (`C0 E1`/`C0 E0`), language (`C0 C1`/`C0 C0 01 vv…`),
  pressure units (`C0 A1`/`C0 A0 02 vv 00`), factory reset `C4 00` → `C4 0C`
  (needs an explicit user confirmation).
- After a meter B+ cycle, page registration is gone. Re-read, then re-apply
  the desired pages one at a time, waiting for each exact echo.

## 6. Content channels (summary)

- **Audio (0x628):** send full snapshots on source or metadata changes. Text
  is UTF-16LE, headers are capture templates. Exception: AUX is
  `61D 20 20 32 FF FF` plus the short `628 26 0D 00 00 02`, queued
  immediately. The meter generates the track number itself. The rich-source
  page shows slot 1 (title) and slot 3 (artist); slot 2 is hidden.
- **Navigation (0x629):** structured instruction records. Maneuver codes,
  distance (packed BCD, confirmed 0.1–999.9 mi) and street name are in
  `av_headunit/navigation.py` and `maneuver_registry.json`. The first
  non-idle state must be sent twice.
- **Buttons:** the meter reports steering/panel events on `0x681`, for
  example seek-forward `0x92` press / `0x12` release.

## 7. Rules

1. One transmitter per CAN ID and bus. The HU emulator and replay never send
   the same ID at the same time.
2. Never transmit meter-origin IDs: AV `0x6xx` meter side; main `0x2DE`,
   `0x280`, `0x355`, `0x5C5`.
3. Report success only from the meter's response. Report timeouts and
   rejections as failures.
4. Missing or stale data must not silently become zero, false or healthy.
5. Keep simulated speed at 0 unless asked (it moves the odometer; allowed on
   this bench when requested). Never run any of this against a real vehicle.

## 8. Where the code is

| Concern | Path |
|---|---|
| AV transport, sessions, startup chains | `av_headunit/av_transport.py`, `transport.py`, `transaction_manager.py`, `session.py`, `baseline.py`, `can_worker.py` |
| ISO-TP-like framing | `q50_av_meter_tool/isotp.py` |
| Page codec, preferences, view model | `av_headunit/settings_codec.py`, `preference_service.py`, `view_model.py` |
| Audio / navigation / clock / steering | `av_headunit/audio_codec.py`, `radio.py`, `tuner_presets.py`, `navigation.py`, `clock.py`, `steering.py` |
| ESP32 firmware / client / UI | `esp32-bench/`, `transport/esp32_bench.py`, `cansim/bench_io_tab.py` |
| Control server / MCP | `control/control_server.py`, `control/mcp_server.py` (`av_*`, `bench_*` tools) |

## 9. Open questions

- Mapping of every 0x61D/0x69D source enum; meaning of audio modes 0x03 vs 0x05.
- Unmapped page-image bits in S1–S8; persistence across IG/ACC/B+ per page.
- Compass/heading payload; 0x62B/0x6AB touch family.
- Whether the short HU startup delays work on the meter (untested).
