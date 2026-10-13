# AV bus protocol — what the app implements and why

Derived on 2026-10-12 from the captures in `reference_logs/Q50_Dumps` (car and bench), cross-checked with
`HANDOFF_AV_BUS_AND_BENCH.md`. Confidence words keep the project meaning: **confirmed** (bench-isolated or
clean capture correlation), **probable**, **candidate**. Code: `q50tb/av/`.

## 1. Who is on the AV bus

| HU sends | Peer answers | Peer | Evidence |
|---|---|---|---|
| 0x603 | 0x683 | **meter** | meter-only capture `avbus_startup_meter` shows only 683/6A8/6A9 (confirmed) |
| 0x628 | 0x6A8 | **meter** | same |
| 0x629 | 0x6A9 | **meter** | same |
| 0x601 | 0x681 | car peer (panel/display) | present in `hurunning_meterplugin` before the meter is plugged in |
| 0x606 | 0x686 | car peer (TCU/display?) | same; 0x6AC sends the VIN (`46 6C …JN1EV7…`) |
| 0x61D | 0x69D | car peer | same |
| 0x62B | 0x6AB | touch panel | 0x6AB carries touch coordinates |
| 0x62C | 0x6AC | car peer | GPS/identity bundle |

On the bench only the meter answers. The emulator therefore runs 603/628/629 with the full handshake and
0x61D as a **send-only** channel (the meter listens to source selection there; handoff: AUX needs
`61D 20 20 32 FF FF`). Other car-peer channels exist but are disabled by default (AV Debug → Link & channels).

## 2. Transport (confirmed against every capture)

Frames are always 8 bytes, padded with `FF`.

| Frame | Bytes | Notes |
|---|---|---|
| SF | `0L TT d…` | L = 1 + payload length (token counted), L ≤ 7 |
| FF | `1L LL TT d d d d d` | 12-bit length counts the token |
| CF | `2n d×7` | n = 1…F, 0, … |
| FC | `30 00 0A FF…` | receiver → sender after an FF; STmin 10 ms |
| ACK | `E0 TT FF…` | receiver → sender for **every** complete message |
| HB | `F0 80 0A 01 FF FF FF FF` | HU, ~500 ms per channel (603/628/629 run even before the meter appears). Meter: 683 `F0 0C`, 6A8 `F1 16`, 6A9 `F0 52` |

**Token**: high nibble = per-direction transaction counter; `0` for the first message after a channel
(re)opens, then 1…7 rolling (0 skipped). Low nibble = application fragment index, bit 3 set while more
fragments follow. Records longer than **20 bytes** are split into 20-byte fragments, each sent as its own
transport message and ACKed before the next (`628 tok 28, 29, 2A, 2B, 2C, 25`).

The real HU sends one transaction at a time per channel and waits for the meter's *response* before the
next settings request; the emulator does the same.

## 3. Application records

Byte 0 = function **group**, byte 1 = `(item << 4) | op`.

| op | meaning | example |
|---|---|---|
| 0 | SET | `C0 20 <image>` write page image |
| 1 | GET | `C0 21` read page image |
| C | RESP (status first: 00 ok, FF not ready) | `C0 2C 00 55 DD 5F FD FD C0 00 00 00` |
| D | NOTIFY | audio/nav `20 0D …`, clock `40 0D …`, alert `C4 2D` |
| F | unsupported | `41 61` → `41 6F` |

When a peer SETs an item on the HU (`G I0 v`), the real HU echoes `G IC 00 v`; the emulator does too.

### Channel open (confirmed, `hurunning_meterplugin`, `AV_STARTUP_1`)

```
meter  683  00 20 00 01 05 00     announce (tok 00), repeated ~1.5 s until answered
HU     603  E0 00                 ACK
HU     603  00 21                 open answer (tok 00)      628 uses "00 21 E0"
meter  683  00 2C 00 01 05 00     open confirm (tok 10)
HU     603  00 2C                 (tok 10)                  628 uses "00 2C E0"
HU     603  40 0D 03 20 36        post-open chain: clock / 628: audio state + settings reads / 629: nav idle
```
6A8 also sends a capability record `08 0D 01 01 E0 00 00 03 03 E0 02 02 C0 02 02 F0` after opening.
Real HUs answered within 50–130 ms. A meter re-announce while open means it rebooted → channel re-opens.

## 4. Settings on 0x628/0x6A8 (group C0)

| Item | Read → value | Write | Status |
|---|---|---|---|
| 0 capabilities | `C0 01` → `9F 00 00 00` | — | observed |
| 2 page image | `C0 21` → 9 bytes S0…S8 | `C0 20 S0…S8` | confirmed; success only if echoed |
| 8 alerts | `C0 81` → `aa bb` | `C0 80 aa bb` | byte0 outside-temp alert (candidate), byte1 nav alert (candidate) — `…30_0_30min_nav_outsidetemp` toggles byte1 then byte0 |
| A tire pressure unit | `C0 A1` → `02 vv F0` | `C0 A0 02 vv 00` | **confirmed**: 01 kPa, 02 PSI, 03 bar (`kpa_psi_AVBUS`) |
| C language | `C0 C1` → `01 01 E0 00 00` | `C0 C0 01 vv …` | 01 English, 02 French, 03 Spanish all confirmed on the bench 2026-10-12 (meter echo + translated page titles); E0 looks like a 3-language mask |
| E display effect | `C0 E1` → `01` | `C0 E0 vv` (assumed) | bench 2026-10-12: emulator write 00 then 01 confirmed by echo and read-back (visual effect only at startup) |

Page masks (handoff, confirmed): Range S0 0x01, Trip Distance S0 0x04, Average Speed S0 0x10, Driving Aids
S0 0x40, Tire Pressure S1 0x01, Fuel Economy S1 0x10, Navigation S2 0x10, Audio S2 0x40, Blank S3 0x01,
Chassis Control S4 0x01. S2 0x20 is also seen toggling (unknown page).

Group C4: `C4 00` → `C4 0C` factory reset (`AVBUS_SPARE_IN_CAR`); **`C4 2D` = driver break alert**
(notify; seen in `DRIVERBREAK` and `MAYBE_break_warning`).

## 5. Audio (0x61D select + 0x628 records)

Source select on 0x61D: `20 20 <code>` (peer echoes and sets items 8/A/C back).

| Source | 61D code | 628 record | Status |
|---|---|---|---|
| FM | `02 01 00` (`EF 01 nn` = preset nn) | `20 0D` band 01 | probable |
| AM | `01 01 00` | `20 0D` band 10 | probable |
| SXM | `15 01 00` | `20 0D` band 23 | probable (bench shows SXM, channel, station) |
| iPod | `53 FF FF` | `22 0D` type 03 + `2A 0D 00 12 2B 00 00` | probable (bench: "iPod is not connected") |
| USB | `47 FF FF` | `22 0D` type 04 + `2A 0D 00 13 2C 00 00` | probable (bench: "No USB memory") |
| BT audio | `51 FF FF` | `22 0D` type 05 | probable (bench: "Bluetooth Audio" + title/artist) |
| DVD | `55 FF FF` | `22 0D` type 06 | probable (bench: "DVD" + title/artist) |
| IAS | unknown | `22 0D` type 07 | candidate (bench: title "IAS", left-aligned, with metadata; meaning unknown) |
| Disc | `20 FF 00` | `22 0D` type 08 (+ `2A 0D 00 17 20 00 00` when empty) | probable (bench: "DISC" + track/title/artist; "No disc" from the 2A record) |
| CD / Music Box | unknown | `22 0D` type 01 / 02 | candidate (bench: titles "CD" / "Music Box" with metadata) |
| AUX | `32 FF FF` | `26 0D 00 00 02` | confirmed (handoff) |

Bench 2026-10-12: the earlier USB/iPod guesses were swapped (the meter labels `53`/type 03 as iPod and
`47`/type 04 as USB). The media header value the emulator sends as "elapsed" is shown as **Track N**.
The media type byte alone sets the page title (01 CD, 02 Music Box, 03 iPod, 04 USB, 05 BT, 06 DVD, 07 "IAS", 08 DISC).
`2A 0D 00 <dev> <msg> 00 00` overlays a message: 00 blank, 01 "Audio OFF", 2B/2C "iPod is not connected",
2D "Check USB memory or iPod", 30 "Please eject disc". It hides metadata, so send it only for an empty source.

Nav maneuver codes (0x629 byte 3): see §6 (36 codes, bench sweep 2026-10-13).

Radio record: `20 0D 00 00 <band> 00 <valid> <preset> <freq×3> FF <slot> 06 <len> <text1 UTF-16LE>` then
`00 02 06 <len> <text2>` or `00 00 00 00`. Slot byte: FM 01, AM 02, SXM 07.
Frequency nibbles: 4 integer digits right-aligned (F = blank), decimal flag, tenths:
`F1 03 13` = 103.3, `FF 96 17` = 96.7, `F5 30 0F` = 530, `FF F1 0F` = channel 1.
This reproduces the car's `KVDT / Rel_Talk` record byte-for-byte.

Media record: `22 0D 00 00 <type> 00 <playing> 00 00 <elapsed?>` `06 <len> <title>` `00 02 00 00`
`06 <len> <album>` `00 01` `06 <len> <artist>`; empty/no-media form is the header plus zeros.
Title and artist show on the meter; album is hidden (handoff). Texts up to 32 characters.

## 6. Navigation (0x629)

`20 0D <state> <maneuver> <bar> <progress> 00 00 <distance×4> <unit> <heading> 06 <len> <street UTF-16LE>`

* state: 00 idle (compass), 02 new instruction (far, no bar), 03 guidance update (far; with maneuver 00 =
  between instructions), 10 / 11 / 12 approach with bar. The car's HU (LONGDRIVE, cap3, drivework) sends 10 once
  when the bar starts, 11 about once a second while progress climbs, and 12 once at the maneuver (progress 100,
  distance 0). On the meter 10, 11 and 12 look identical (owner, bench 2026-10-12): the meter keys the bar view
  off the bar byte, not the state. The first non-idle record is sent twice (handoff).
* progress 0…0x64 while the bar is shown.
* distance nibbles: 5 integer digits, decimal flag, tenths, F: `FF FF 81 9F` = 8.9, `FF 50 00 FF` = 500,
  `FF FF F0 FF` = blank. unit 03 = mi, 08 = ft (`drivework`: 8.9 → 0.1 mi, then 500 → 100 ft → 0).
* heading 0x01…0x20 wrapping (32 steps, `drivework` shows 1C→1D→…→20→01). Which value is north is a
  **candidate** (0x01 assumed).
* maneuver (bench sweep of 0x00-0xFF with the camera, 2026-10-13; same set as CANSim's 2026-09-29 sweep).
  Only these 36 codes draw an arrow; any other code is ignored and the meter keeps the previous arrow.
  Labels live in `registry/nav_maneuvers.json`; picture of every icon: `img/nav_maneuvers_0x629.jpg`.
  * 00 stem only, 01 straight, 02/03 slight left/right, 04/05 left/right, 06/07 sharp left/right,
    08/09 U-turn left/right.
  * 10/11 sharp left/right, 12/13 and 14/15 slight left/right: same arrows with the other road drawn faintly.
  * 20-2F roundabouts. Even = clockwise (left-hand traffic), odd = counter-clockwise (US); exit angle rises
    pairwise: 20/21 sharp, 22/23 first quarter (left / right), 24/25 bear, 26/27 straight, 28/29 bear,
    2A/2B three quarters (right / left), 2C/2D sharp, 2E/2F U-turn. 2A-2F draw the whole ring.
  * 50/51 keep left/right (S-bend), 60 straight with a dashed line, 61 destination (checkered flag).
  * The car's HU used 01-05 and 08 in the captures (LONGDRIVE, cap3, drivework, navboth, navprompt).

## 7. Clock

* 0x603 (meter): `40 0D <mode> <HH> <MM>` once a minute and after changes. Hours are the 24 h value with a
  blank tens digit below 10 (`F9 25` = 09:25, `F0 29` = 00:29). mode bit0 = 24 h display (03 after "set
  24 hr time", 02 in 12 h); bit1 normally set (01 appears right after a change).
* 0x61D (car peer): `24 4D YY YY MM DD hh mm ss dow` packed BCD each second, dow 0 = Sunday.

## 8. Open questions

* S1–S8 unmapped bits (S2 0x20); whether the display-effect write changes the startup animation.
* Media header byte 9 (shown as the track number on the bench), the `2A 0D` record.
* Which heading value is north; units other than mi/ft.
* Whether the meter needs 0x61D date/time on the bench, and the handoff note about sending it only after a
  meter heartbeat.
