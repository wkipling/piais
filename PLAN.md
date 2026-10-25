# piais — Living Plan and Work Log

Read this first when resuming. It is deliberately blunt about what is proven
and what is still an assumption.

## 1. Goal

A headless Raspberry Pi (Zero 2 W minimum) that:

1. receives AIS on an RTL-SDR,
2. publishes it on a NMEA 2000 bus via a PiCAN 2,
3. can also publish VE.Direct and sensor telemetry,
4. survives abrupt power loss because it has no writable root filesystem,
5. has room for BLE and LoRa outputs without becoming a server.

Primary success criterion: a chart plotter shows live AIS targets from this
box, on a real N2K bus, after a week of unattended power-cycling.

## 2. Success criteria

| # | Criterion | How it is judged |
| --- | --- | --- |
| 1 | AIS targets appear on an MFD | PGN 129038/129039 seen by the plotter, correct MMSI/position |
| 2 | No SD card corruption | 50 abrupt power cuts, filesystem clean, boot succeeds |
| 3 | Unattended | 7 days, no manual restart, memory flat |
| 4 | Graceful on a busy bus | No bus errors; rate limit keeps PGN traffic bounded |
| 5 | Honest data | No invented positions: AIS "not available" becomes N2K "not available" |

## 3. Scope

### In scope for v1

- RTL-SDR + AIS-catcher + PiCAN 2 + SocketCAN.
- AIS types 1/2/3, 5, 18, 19, 21, 24 → standard N2K PGNs.
- Read-only root and a watchdog.
- VE.Direct battery/charger telemetry as a second source.
- systemd supervision and a runbook.

### Out of scope for v1

- Transmitting AIS. This is receive-only.
- Certified NMEA 2000 compliance.
- Routing N2K between networks, or acting as a gateway/bridge.
- A web UI. AIS-catcher already has one; piais is headless.
- LoRa and BLE. Designed for, not built.
- Solar-specific Victron PGNs (no clean standard mapping).

## 4. Phases and status

| Phase | State |
| --- | --- |
| 0. Prior-art survey and feasibility | ✅ complete |
| 1. AIS → PGN core, tested without hardware | ✅ complete |
| 2. Bench-validate AIS-catcher's own `-I can0` output | ⬜ next |
| 3. PiCAN 2 on a real bus, address claim, MFD sees targets | ⬜ |
| 4. Read-only OS + watchdog + power-cut soak | ⬜ |
| 5. VE.Direct on the same N2K identity | 🟡 parser done, hardware not tested |
| 6. Sensors / BLE / LoRa | ⬜ design only |

## 5. Work log

### 2026-10-25 — project kick-off, research, working core

- Surveyed prior art and verified licences. Key finding: AIS-catcher already
  has **experimental** NMEA 2000 output via SocketCAN (`-I can0`), built on
  Timo Lappalainen's NMEA2000 library. It handles AIS 1–5, 9, 11, 14, 18, 19,
  21, 24. Upstream explicitly says it is not intended for a real boat network.
  Decision: use it for the Phase 2 smoke test, but do not make it the
  production path (see ADR-005).
- Chose the stack: AIS-catcher (external, GPL-3.0) → pyais (MIT) →
  canboat/nmea2000 (Apache-2.0) → SocketCAN.
- Implemented `src/piais/`: AIS normalisation, multipart reassembly, NMEA
  checksum validation, PGN mapping, rate limiting, SocketCAN/dry-run sender,
  VE.Direct parser, systemd/OS recipes.
- Found and fixed two non-obvious traps:
  - `nmea2000`'s encoder is **positional** and its default `units="native"`
    silently scales some length fields by ten. All encoding now uses
    `units="si"`.
  - canboat's SI angle fields are radians and speeds are m/s. AIS gives
    degrees and knots; the PGN layer converts explicitly.
- Wrote 32 tests. The most important one compares every PGN field list to the
  installed canboat database, because a canboat update that inserts a field
  would otherwise shift values into wrong bits silently.
- Verified the daemon end-to-end over UDP in dry-run: one AIVDM datagram
  produces the expected five CAN frames.

### 2026-10-25 — ETA sentinel bug

- Found while reviewing `etaTime`: AIS uses `hour=24` and `minute=60` for
  "not available", and the mapping was turning those into 24:00 and 01:00.
  `(eta_hour or 0)` also conflated a genuine midnight with a missing value.
- Fixed: hour/minute are only copied when `0 <= hour <= 23` and
  `0 <= minute <= 59`; otherwise the N2K TIME field is `None`.
- The date and time are now independent: a known date with an unknown time
  keeps the date and writes only the time as unavailable.
- Also learned that the encoder range-checks TIME against 0..86401 and
  **rejects** a raw `0xFFFFFFFF`; `value=None` is the correct way to write the
  N2K "not available" sentinel for a TIME field.
- Added four tests (36 total): normal ETA, sentinel ETA, midnight, sentinel
  date, and an invalid calendar date.

### 2026-10-25 — Sentinel audit, four more bugs, refactor

- The ETA fix exposed the general rule: `value=None` is the only correct way
  to write N2K "not available" for a scaled NUMBER field. Raw sentinels are
  range-checked and rejected. That made every `_angle`/`_sog`/`_position`/
  `_rot` helper a latent crash: any target with a missing COG, SOG, heading,
  rate of turn or position raised `ValueError` at encode time. The two
  VE.Direct crashes (signed battery current, temperature) had the same cause.
- Fixed: absent numeric AIS fields and battery fields are now `None`.
- Fixed: zero AIS dimensions and draught became 0.0 m instead of "not
  available"; a partial reference-point pair no longer invents a total.
- Fixed: 129039 `unitType` was hardcoded to SOTDMA; it now follows the AIS
  CS flag (0 = SOTDMA, 1 = CS).
- Fixed: 24A and 24B shared one rate-limit key, so the second part was
  suppressed for 60 s. They now have independent keys.
- Fixed: headings 360–510 passed the old sentinel check and produced an
  out-of-range gyro angle; only 0–359 is accepted.
- Fixed: 24B hardcoded `gnssType=15`, an undefined lookup. `gnssType` now maps
  AIS EPFD 1–8 and uses canboat's defined "Default: undefined" otherwise.
- Added: NMEA 4.10 tag-block stripping, 24B auxiliary-craft mothership MMSI,
  a finite-value guard in the VE.Direct parser, and a done callback that logs
  failed send tasks instead of losing them at garbage collection.
- Refactored `pgn.py` around `_number` (scaled; `None` = not available) and
  `_code` (exact bits), with shared identity/position/motion/dimension
  helpers. The LOOKUP/NUMBER distinction is now explicit at every call site.
- 58 tests (22 new): absent numerics, zero and partial dimensions, unitType,
  gnssType, AtoN type mapping, 24B mothership, negative battery current,
  24A/24B rate limiting, tag blocks, invalid headings, failed-send logging,
  and the bus-path SI encoder.
- Found while refactoring: `PythonCanAsyncIOClient` creates its encoder with
  the library default `units="native"` while the dry-run path used SI. The
  wire bytes are identical for the AIS PGNs today, but the bus path now reuses
  the SI encoder so that cannot change silently.

### 2026-10-25 — Electrical feasibility (veKonvert, VE.Direct, BLE)

- Looked at Digital Yacht veKonvert: commercial, one VE.Direct input,
  SmartShunt/BMV/MPPT, no AIS and no open code. It confirms the expected
  battery/solar PGNs but is not a code source.
- VE.Direct shunt/MPPT is feasible: V/I/T → 127508, SOC/time-to-go → 127506,
  charger state → 127507, PV voltage → 127751 (candidate). PV power and yield
  have no standard PGN and stay off the bus.
- BT-only Victron gear is feasible with `victron-ble` (Unlicense) using
  Instant Readout advertisements; one adapter can hear many devices. Keys come
  from VictronConnect. Orion-Tr Smart exposes voltages but no current; Orion XS
  exposes both.
- Hardware constraints: one PL011 UART shared with Bluetooth, so two VE.Direct
  devices need USB-serial adapters; the 5 V budget with SDR + PiCAN + hub is
  unmeasured.
- Wrote `docs/ELECTRICAL.md`; README reframed around the real gap: no cheap,
  open, single-box N2K node for AIS + electrical. No code changes.

### 2026-10-25 — Hardware baseline

- The Zero 2 W is the **minimum** supported board, not the only target. Any
  larger 64-bit Pi with a 40-pin header works and is preferable once multiple
  VE.Direct devices are added: more USB ports, and extra hardware UARTs on
  Pi 4/CM4-class boards (`uart2`–`uart5`).
- Updated the README, HARDWARE, ELECTRICAL, ARCHITECTURE, DECISIONS, research
  notes and package description to state the baseline instead of implying the
  Zero 2 W is the only platform.

### 2026-10-25 — Control page, SDR power, Wi-Fi watchdog

- Added `src/piais/web.py`: a stdlib-only HTTP control page with status,
  debug log, token rotation, SDR stop/start/restart and Wi-Fi on/off. No new
  dependencies; the package still installs four libraries.
- Access model: loopback binds use a 32-hex token printed in the boot log and
  stored in `/run/piais/token` (0600, tmpfs). A non-loopback bind requires
  HTTP Basic auth and a password, enforced in config validation.
- SDR control distinguishes two capabilities: stopping the decoder (saves CPU)
  and cutting USB power (saves the dongle's current). `uhubctl`/GPIO switches
  are argv arrays. An unconfigured action returns an explicit error; a power
  cut without `power_on_command` cannot be undone from software and says so.
- Wi-Fi watchdog turns the radio off after `off_after_s` with no client that
  arrived over Wi-Fi. Classification uses the connection's local address
  against `/sys/class/net/*/wireless`. Loopback and Ethernet do not extend the
  window; an unclassifiable request does (conservative). Optional reopen
  interval. Failed off commands back off for 30 s.
- The page is started before the bus and now survives an N2K failure: if
  address claiming or `can0` fails, the process stays up and shows the error.
- The control page never auto-refreshes. A polling page would keep the Wi-Fi
  watchdog from ever firing; this is asserted in the tests.
- Found while updating the unit: `PrivateDevices=yes` hides `/dev/ttyUSB*`
  (VE.Direct) and the USB nodes `uhubctl` needs, and `Requires=can0.service`
  meant a missing CAN interface took the control page down with it. Changed to
  `Wants=` and removed the device sandbox, with the reason recorded in place.
- Found while checking the plotter question: canboat 9.0.0 does define
  127501/127502 (switch bank status/control), and `N2KDevice` can receive via
  `set_receive_callback`. Whether an ECHOMAP renders a generic third-party
  bank is **unverified**; the bench test is in `docs/CONTROL.md` §7.
- 119 tests (61 new): auth, token rotation and grace, cross-site refusal,
  real HTTP round trips, SDR ordering/failure states, Wi-Fi deadline and
  classification, config validation.
- Not run on a Pi, a PiCAN 2, a dongle or a switchable hub. The power figures
  and `uhubctl` behaviour are hardware claims that remain untested here.

### 2026-10-25 — README cleanup

- Rewrote the README around the AIS path: 236 → 173 lines. Removed the
  board-selection essay, the electrical/BLE roadmap detail and the prior-art
  table (now one paragraph), and cut the control-page config block to a
  pointer at `config/piais.example.toml`.
- VE.Direct is now one row in the status table plus one warning, not a
  headline feature. The hardware baseline stays as a single sentence: any
  64-bit Pi, Zero 2 W minimum.
- No code changes; test count unchanged.

## 6. Next actions

1. **Bench Phase 2 (cheapest, highest information):** build/install
   AIS-catcher with NMEA 2000 support, run `AIS-catcher -I vcan0`, and compare
   its output with `piais --dry-run` for the same recorded AIS feed. If it
   works, you have a reference implementation to validate against.
2. **Wire the PiCAN 2**: `dtparam=spi=on`,
   `dtoverlay=mcp2515-can0,oscillator=16000000,interrupt=25`, `can0` at
   250 kbit/s. Check termination before connecting to the boat.
3. **Address claim**: watch the bus with `candump can0`; confirm piais claims
   an address and that an ISO Request gets an answer. Test with no other
   device first, then with the plotter.
4. **Prove the PGNs**: use `canboat analyzer` or `candump -L` and confirm the
   MFD lists targets. Check one target against MarineTraffic/AIS-catcher's
   own web view.
5. **Power-cut soak**: overlayfs or Alpine diskless, watchdog enabled, 50
   power cuts, then `fsck` the boot partition and boot again.
6. **Electrical E0 (after AIS is on the bus):** read the shunt, then the
   MPPT, over VE.Direct and compare parsed frames with the Victron app.
   Publish 127508/127506/127507 only after the bench comparison; see
   [`docs/ELECTRICAL.md`](docs/ELECTRICAL.md).
7. **BLE E3:** on a laptop or the Pi, run `victron-ble read` for the DC-DC
   charger and confirm which fields the model exposes before integrating.
8. **Control plane on hardware:** confirm the one-time token link, then check
   whether the hub can really switch one port (`uhubctl -l ... -p ...`). If it
   cannot, the page must keep saying "no USB power switch". Test the Wi-Fi
   watchdog with the chosen privilege route (`sudo -n` or polkit) and confirm
   the radio comes back on a reopen interval.

## 7. Open questions and risks

| Risk | Why it matters | Mitigation |
| --- | --- | --- |
| RTL-SDR load on Zero 2 W | USB + DSP could overrun buffers | AIS-catcher `-F` fixed-point downsampling; measure CPU; a larger Pi is the fallback |
| RTL-SDR frequency drift | AIS is 25 kHz channels; cheap dongles drift | Use a TCXO dongle; `freqoffset` in AIS-catcher |
| PiCAN 2 not isolated | Ground loops, noise, damage | Check board; consider isolated transceiver or isolated DC-DC |
| N2K bus saturation | Many targets × fast packets | Per-MMSI rate limit; measure with `candump`; raise interval |
| Address claim collision | Preferred address 25 may be taken | The library negotiates; verify on a real bus |
| canboat field changes | Silent bit shifts | Field-order test must stay green; pin canboat version |
| MPPT PV power/yield | No standard PGN | Leave off the bus; never derive current from power/voltage |
| Orion-Tr Smart current | Instant Readout does not expose it | Publish voltage/state only; Orion XS has current |
| One UART shared with BLE | PL011 serves Bluetooth by default | USB-serial adapters for VE.Direct, or validate `miniuart-bt` |
| Pi 5 V budget | SDR + PiCAN + USB hub could brown out | Powered hub; measure before installing |
| LoRa bandwidth | Raw AIS does not fit | Send summaries, not the NMEA stream |
| Read-only root hides problems | Logs vanish on reboot | Optional RAM log ring + persistent journal only when diagnosing |

## 8. File map

| Path | Purpose |
| --- | --- |
| `src/piais/ais.py` | AIVDM reassembly, checksum, pyais normalisation |
| `src/piais/pgn.py` | AIS → NMEA 2000 PGN mapping; the field-order contract |
| `src/piais/n2k.py` | SocketCAN sender / dry-run frame logger |
| `src/piais/vedirect.py` | VE.Direct parser and PGN 127508 |
| `src/piais/service.py` | asyncio UDP daemon, rate limiting, CLI |
| `src/piais/web.py` | control page, SDR/Wi-Fi controllers, token store |
| `src/piais/config.py` | TOML config |
| `tests/` | 119 tests, including canboat field-order guard |
| `os/` | OS recipes, boot config, systemd units |
| `docs/CONTROL.md` | control page, SDR power, Wi-Fi watchdog, privileges |
| `docs/ELECTRICAL.md` | Victron shunt/MPPT/DC-DC feasibility and candidate PGNs |
| `docs/` | Research, architecture, hardware, software, ADRs |

## 9. How to resume after a context loss

1. Read this file and `git log --oneline`.
2. Run `python -m pytest -q` — it must be green.
3. Run the dry-run sentence in the README and confirm five frames.
4. Check the "Next actions" list; start at the first unchecked item.
5. Do not trust any claim about hardware until it appears in the work log.
