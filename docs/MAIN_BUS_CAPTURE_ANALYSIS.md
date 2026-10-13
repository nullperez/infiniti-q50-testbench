# Main-bus capture analysis (2026-10-12)

Statistical pass over every real-car main-bus capture, looking for fields the signal database
(`q50tb/signals/q50_main.py`) did not have. Everything here is inferred from traffic between other ECUs; none of
it is an OEM specification. Confidence words follow the database: **probable** = strong, consistent across many
captures; **candidate** = one capture, or the meaning is a guess.

## Data and method

* 57 real-car main-bus interfaces (11.1 M frames) from `reference_logs/` and `temp/CANSim/` (pcapng + CANSim jsonl),
  duplicates removed; bench and simulated captures excluded. The main bus is the interface carrying 0x351 and the
  meter's 0x5C5 (or the 13-24 IDs present with the car off).
* Driving set for value fits: LONGDRIVE, drivinglogmainbus, cap3, radar, radar_off_drive, driving_eco_mode,
  ecoatend, MAINBUS_28s_tpms, q50_capture_20260922 (~3500 s, speeds to 130 km/h).
* Per byte: which bits change, rolling counters, sum / XOR / nibble checksums (CRC-8 variants tried, none matched).
* Values: every undefined byte, nibble and 16/12-bit pair (both byte orders, signed and unsigned) correlated
  against RPM, speed, wheel speeds, steering, pedal, torque, coolant, outside temperature, fuel level and
  accelerations/yaw computed from wheel speeds.
* Events: in captures whose names describe an action (hood, TCS, reverse, drive mode, unlock, remote start, ...)
  bytes that change only a few times, matched to the action times; then each hypothesis checked as a
  co-occurrence table over all 57 captures.
* Bench check: the new bits were overridden on the real meter and compared on camera (method as in
  `MAIN_BUS_SWEEP.md`, positive control detected). **None of them changed anything visible** (list below).

## New and corrected fields

| ID | Bits | Name in database | Meaning / evidence | Confidence |
|---|---|---|---|---|
| 0x174 | B3[7:4] / B3[3:0] | GEAR_ACTUAL / GEAR_TARGET | Transmission gear, actual / target: 1-7 in D (7-speed), 9 = R, 0xA = P/N; M2-M5 give 2-5. Differ only during a shift (e.g. 0x34). | probable |
| 0x174 | B5[3:2] | SHIFT_MODE | 0 Standard, 1 Eco, 2 Sport, 3 Snow; follows the 0x56C drive mode. | probable |
| 0x174 | B5[7] | LOCKUP_CAND | Set mostly when engine rpm and turbine speed are within 30 rpm: torque-converter lock-up? | candidate |
| 0x174 | B0 | SHIFT_TORQUE_CAND | 0xFF except during gear changes (all activity within 0.5 s of a shift). | candidate |
| 0x176 | B0[3:0]+B1 | OUTPUT_SPEED | 23.77 x vehicle km/h (r 1.000): output-shaft / driveshaft speed. | probable |
| 0x176 | B2-B3 | ENGINE_RPM (note) | Reads 0 while stopped in gear at idle and below engine rpm at low speed: transmission input (turbine) speed, not engine rpm. Explains why it does not move the tach. | note |
| 0x177 | B1[4] | PARK | 1 in P, 0 in R/D/M. | probable |
| 0x177 | B1[7] | MANUAL_MODE | 1 exactly in M1-M7. | probable |
| 0x177 | B4 | SHIFT_ACTIVE_CAND | 0 except during gear changes. | candidate |
| 0x182 | B1[7] | COAST_CAND | Only while moving with the accelerator released (deceleration fuel cut?). | candidate |
| 0x182 | B4 | THROTTLE_CAND | Follows the accelerator (r 0.94), 0xFF at full throttle. | candidate |
| 0x1F9 | B0[5:3] | ENGINE_STATE | 0 IG off, 2 IG on / stopped, 6 cranking, 4 running, 5 running (settled). | probable |
| 0x216 | B0[5] | HOOD_OPEN | Set 5.6-23.7 s in `5seconds_open_hood_25_close`, never elsewhere. The old WIPER_STATE B0[7:5] is narrowed to B0[7:6] (on the car bit 6 is always 1, bit 7 always 0). | candidate |
| 0x216 | B1[3] | FOG_LIGHTS (doubt) | 1 in P and 0 in R/D/M in all 42 captures with P: behaves like a P/N signal, not fog lamps. Downgraded to candidate. | candidate |
| 0x239 | B5 | ACCEL_POSITION | Equals 0x180 B5 raw (r 0.9997), 0.5 %/bit. | probable |
| 0x245 | B3[0] | TCS_OFF | 1 exactly while 0x354 TCS_STATE = 2 (5 captures). B1/B2 go 0xFF while cranking. | probable |
| 0x285 | B4 | VEHICLE_SPEED_KMH | Whole km/h, floor of 0x354 speed (r 0.9999). | probable |
| 0x286 | B0 | SPEED_DEPENDENT_CAND | ~3 x km/h + 32, saturates at ~0xED above ~65 km/h. | candidate |
| 0x292 | B0+B1[7:4], B1[3:0]+B2, B3+B4[7:4] | LONG_ACCEL, LAT_ACCEL, YAW_RATE | **Offset binary (0x800 = 0), not signed.** Long/lat ~0.001 g/bit (r 0.93 / 0.92 vs wheel-speed estimates), + = accelerating / left. Yaw ~0.1 deg/s/bit, noisier (r 0.74). 0xFFF before the sensor is ready. | probable (yaw candidate) |
| 0x2AB | B0+B1[7:4] | YAW_RATE | Signed 12-bit, r -0.990 vs yaw from rear wheel speeds; about -0.019 deg/s/bit (track 1.55 m assumed, +-5 %), + = left after the sign. The old WHEEL_ANGLE_CAND (B2-B3) looks like noise. | probable |
| 0x300 | B0 | STEER_EFFORT_CAND (ENGINE_RUNNING removed) | One unsigned value: power-steering assist effort. Rises while the wheel turns (~120 at full lock parked), back near 0 when it stops, same sign both ways, falls with speed (mean 46 parked, 7 at 10-40 km/h, ~3 above 40 km/h while steering); 0-1 engine off, ~2-3 at idle. The old ENGINE_RUNNING (bit 1) only looked like a flag at idle: while driving it is 0 in 28940 samples and 1 in 17547. The engine-off state now sets the whole byte to 0. | probable |
| 0x2B0 | B3[6] / B3[7] | BRAKE_SW_ON / BRAKE_SW_OFF | Two brake switches: pressed / released (equals 0x182 BRAKE_PRESSED; both or neither briefly at edges). | probable |
| 0x358 | B1[0] | BULB_CHECK_CAND | 1 s pulse at IG on, with TPMS_LIGHT. | candidate |
| 0x358 | B3[7] | UNLOCK_EVENT_CAND | 0.3 s pulse at each unlock (with 0x351 UNLOCK and the hazard flash). | candidate |
| 0x358 | B5[7:5] | REMOTE_START_STATE | 0 normally; 1-4 only in the two remote-start captures. | candidate |
| 0x35D | B0[7] | IG_ON | Equals 0x60D IG_RUN (2 mismatches in ~55k samples). | probable |
| 0x35D | B0[6] | CRANKING | Set exactly while 0x625 START_STATE = 3. | probable |
| 0x35D | B7[7:4] | REMOTE_START_CAND | 5 normally, 0xA while the engine runs on remote start. | candidate |
| 0x544 | B0 | VEHICLE_SPEED_KMH | Whole km/h; 0xFF for the first ~10 s. Frame nibbles XOR to 0 (checksum). | probable |
| 0x54C | B5[6:3] | BLOWER_OUTPUT | Ramps in 0.5 s steps, rises with 0x54B BLOWER_SPEED (L1 7, L2 8, L3 10-12, L4 13, off 0). | probable |
| 0x54C | B0, B7 | TEMP_B0_CAND, TEMP_B7_CAND | Temperature-like (0xFF/0xFE until ready, slow drift; B7 drops with the A/C); scale unknown. | candidate |
| 0x551 | B5[1:0] | ENGINE_STATE | 0 off, 1 IG on / stopped, 2 running, 3 cranking. | probable |
| 0x551 | B6[6] | ECO_MODE | 1 in Eco (or Personal set to Eco). | probable |
| 0x580 | B7[5] | ENGINE_RUNNING (doubt) | B7 is a numeric, load-like value (~0x19 off, ~0x3C idle, 0x4B-0x50 cruising, ~0x76 full throttle). Bit 5 only tracks the engine at idle; it is 0 most of the time while driving. Downgraded to candidate (still fine for the bench's idle/off states). | candidate |
| 0x5B0 | B0 | DRIVE_MODE_FLAGS | One-hot: 0x01 Sport, 0x04 Standard, 0x10 Eco, 0x40 Snow. | probable |
| 0x625 | B3[1:0] | AWAKE_STATE | 1 while IG on and for 4.9 s after each wake event, else 2. | candidate |
| 0x6E2 | B2[2:0], B3 | PAGE, RPM_DIV64_CAND | Multiplexed: B2 = 0x38 + page (0-3 engine off, 4-7 running), B0/B1 fixed 0x12 0x16, B5[7:2] sequence; B3 follows rpm on pages 4-7. | probable / candidate |

## Counters and checksums found

| ID | Counter | Checksum |
|---|---|---|
| 0x02A | B5[3:2] | - |
| 0x174 | B4[3:0] | none found |
| 0x176 | B6[3:0] | none found |
| 0x1C3 | B5[1:0] | B7 = (sum B0..B6 + 0xC4) & 0xFF |
| 0x245 | B4 (8-bit), B7[1:0] | - |
| 0x286 | B1[7:6] | - |
| 0x2AA | B6[3:0] | B7 = (sum B0..B6 + 0xAC) & 0xFF |
| 0x2B0 | B6[7:4] | - |
| 0x4DF | B3[3:2] | - |
| 0x540, 0x541, 0x542, 0x545 | B7[1:0] | - |
| 0x544, 0x5B4-0x5B7 | - | all nibbles of the frame XOR to 0 |
| 0x560 | B1[1:0] | - |
| 0x5B0 | B1[7:6] | - |

No CRC-8 (polys 1D, 07, 2F, 9B, 31, 39, D5, 4D, 2B; init 00/FF) explains the last bytes of 0x160, 0x177, 0x182,
0x1F9 or 0x2AB.

## Bench check (no visible effect)

Overridden on the meter, camera diff 0 for each (positive control: 0x56C chassis error, detected):
0x216 B0 = 0x62 (hood), 0x216 B1 bit 3 cleared, 0x358 B1 = 0x03 / B3 = 0x80 / B5 = 0x60, 0x80, 0x40,
0x35D B0 = 0xC0, 0x5B0 B0 = 0x01, 0x10, 0x40, 0x551 B6 = 0xC0, 0x245 B3 = 0x19, 0x177 B1 = 0x90, 0x80,
0x174 B3 = 0x33, 0x44, 0x99, 0x2B0 B3 = 0x4C, 0x0C, 0x174 B5 = 0x14, 0x18, 0x1C, 0x90.

## Open leads

* 0x1F9 B7 and 0x215 B1 change together in fixed pairs (0x80/F0, 0xB7/F8, 0xC5/F8, 0xCD/F8, 0xE4/FC, 0xA8/F4):
  one engine-related state in two messages; meaning unknown.
* 0x540/0x541/0x542/0x545 B0 bit 7, 0x2AA B1-B3, 0x54C B1/B2, 0x580 B5 bit 7: change ~1-1.5 s after IG on
  (modules reporting ready).
* 0x351 B0 bit 0: one pulse right after the driver door closed (key search?). B6 bits 7/5/0 follow the power
  state (bit 5 asleep, bit 7 after power off, bit 0 at the button press); bit 7 is defined as BEEP.
* 0x180 B2-B3 and 0x160 B1-B2 follow torque demand; 0x182 B6 bit 0 toggles with pedal presses; not calibrated.
* 0x6E2 B4 (slow sawtooth on some pages), 0x4DF B0, 0x560, 0x174 B1/B2, 0x177 B7, 0x5B4-0x5B6 (data blocks with
  a nibble checksum): unexplained.
* 0x60D B6 bit 6: set in 3 captures with DRL on and headlights off; too little data.

## Other observations

* An OBD-II poller is on the main bus in 22 captures: 0x7DF PID 0x0D (vehicle speed) about once a minute, answered
  on 0x7E8/0x7E9, plus UDS on 0x71D/0x72D and a few 0x7E0 sessions. Too sparse to calibrate with.
* The AV-bus captures carry about 30 main-bus IDs (including 0x180 and 0x60D): the AV bus gets a gatewayed subset
  of the main bus.
