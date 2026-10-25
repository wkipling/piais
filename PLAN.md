# piais — Living Plan and Work Log

Read this first when resuming. It is deliberately blunt about what is proven
and what is still an assumption.

## 1. Goal

A headless Pi Zero 2 W that:

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
6. Then: VE.Direct on `/dev/ttyUSB0`, one battery instance, compare with the
   Victron app.

## 7. Open questions and risks

| Risk | Why it matters | Mitigation |
| --- | --- | --- |
| RTL-SDR load on Zero 2 W | USB + DSP could overrun buffers | AIS-catcher `-F` fixed-point downsampling; measure CPU; heatsink |
| RTL-SDR frequency drift | AIS is 25 kHz channels; cheap dongles drift | Use a TCXO dongle; `freqoffset` in AIS-catcher |
| PiCAN 2 not isolated | Ground loops, noise, damage | Check board; consider isolated transceiver or isolated DC-DC |
| N2K bus saturation | Many targets × fast packets | Per-MMSI rate limit; measure with `candump`; raise interval |
| Address claim collision | Preferred address 25 may be taken | The library negotiates; verify on a real bus |
| canboat field changes | Silent bit shifts | Field-order test must stay green; pin canboat version |
| VE.Direct solar fields | No clean standard PGN | Only map battery V/I/T for now |
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
| `src/piais/config.py` | TOML config |
| `tests/` | 32 tests, including canboat field-order guard |
| `os/` | OS recipes, boot config, systemd units |
| `docs/` | Research, architecture, hardware, software, ADRs |

## 9. How to resume after a context loss

1. Read this file and `git log --oneline`.
2. Run `python -m pytest -q` — it must be green.
3. Run the dry-run sentence in the README and confirm five frames.
4. Check the "Next actions" list; start at the first unchecked item.
5. Do not trust any claim about hardware until it appears in the work log.
