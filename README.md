# piais

**AIS → NMEA 2000 on a headless, read-only Raspberry Pi Zero 2 W.**

An RTL-SDR listens to the two AIS VHF channels. A PiCAN 2 puts the decoded
targets onto the boat's NMEA 2000 backbone as standard AIS PGNs. The Pi runs
from RAM with no writable root filesystem, so a power cut cannot corrupt the
SD card. The same box is designed to grow into VE.Direct telemetry, sensors,
BLE and LoRa.

> **Status: working core, no boat hardware yet.** The AIS decode → PGN →
> CAN-frame path is implemented and covered by 58 tests that use the real
> canboat PGN database. Nothing here has been run against a PiCAN 2, an
> RTL-SDR or a live NMEA 2000 bus. Treat it as a bench-ready prototype.

---

## Short answer: has someone written the code?

**Yes — most of it, and one project already does almost exactly this.** The
honest map:

| Job | Existing code | Licence | Verdict |
| --- | --- | --- | --- |
| AIS demodulation from RTL-SDR | [AIS-catcher](https://github.com/jvde-github/AIS-catcher) | GPL-3.0 | **Use it.** Mature, dual-channel, NMEA over UDP/TCP, web UI. |
| AIS → NMEA 2000 directly | AIS-catcher `-I can0` (experimental) | GPL-3.0 | **Try this first on the bench.** Requires a build with the NMEA2000 library; upstream says it is *not* intended for a real boat network. |
| AIS payload decode | [pyais](https://github.com/M0r13n/pyais) | MIT | **Used here.** Small, pure Python, MIT. |
| N2K PGN encode + address claim | [canboat](https://github.com/canboat/canboat) + [nmea2000](https://github.com/tomer-w/nmea2000) | Apache-2.0 | **Used here.** Prebuilt wheels for Pi Zero 2 W; address claiming included. |
| AIS 0183 → N2K reference | [AK-Homberger gateway](https://github.com/AK-Homberger/NMEA2000-AIS-Gateway) | GPL-3.0 | Read it for PGN mapping; don't link it. |
| Full marine data server | [Signal K](https://signalk.org/) + [signalk-to-nmea2000](https://github.com/SignalK/signalk-to-nmea2000) | Apache-2.0 | Works, but a Node server is a lot of RAM and moving parts for a Zero 2 W appliance. |
| VE.Direct | [karioja/vedirect](https://github.com/karioja/vedirect) | MIT | Protocol reference; this repo has its own small, tested parser. |
| AIS over LoRa | [beinnlora/AIS-repeater](https://github.com/beinnlora/AIS-repeater) | GPL-3.0 | Prior art from 2016. Raw AIS does not fit LoRa; send summaries. |
| Read-only / RAM OS | Raspberry Pi OS overlayfs, Alpine diskless | — | Documented in [`os/`](os/). |

So the gap is not "decode AIS" or "speak N2K". The gap is a **small,
deterministic appliance** that:

- owns the PGN field mapping explicitly (and tests it against canboat),
- rate-limits duplicate AIS reports so a busy harbour does not saturate the
  bus,
- can add VE.Direct and sensor sources to the same N2K identity,
- runs from a read-only OS and survives unplugging,
- does not require a Node/Signal K server on a 512 MB Pi.

That is what this repository is for. If you only ever want AIS, AIS-catcher's
`-I can0` may be enough and you can stop after Phase 0.

---

## Architecture

```mermaid
flowchart LR
    ANT[162 MHz antenna] --> SDR[RTL-SDR]
    SDR --> CAT[AIS-catcher<br/>dual channel]
    CAT -->|AIVDM over UDP| DAEMON[piais-aisd]
    DAEMON -->|PGN 129038/129039/<br/>129040/129041/129794/<br/>129809/129810| CAN[SocketCAN can0]
    CAN --> N2K[NMEA 2000 backbone]
    VIC[Victron VE.Direct] -.->|optional| DAEMON
    N2K --> PLOT[Chart plotter / MFD]
    DAEMON -.->|optional summaries| LORA[LoRa]
    DAEMON -.->|optional NMEA| BLE[BLE]
```

The Pi is not a router. AIS-catcher does the DSP; `piais-aisd` does the
protocol conversion; `nmea2000` does address claiming and fast-packet framing.

## Quick start (no hardware)

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'

# Decode one real AIVDM sentence and print the CAN frames it would send:
.venv/bin/python -m piais --dry-run \
  --sentence '!AIVDM,1,1,,B,13`e;R001`PNcD2MH48KQq>P0@;5,0*63'
```

Output (one five-frame fast packet for PGN 129038):

```
INFO piais.n2k: can0 11F80E00 [8] 00 1C 01 88 4B 8B 0E 14
INFO piais.n2k: can0 11F80E00 [8] 01 8F FE 03 26 9A 99 1E
...
```

Run the daemon against a UDP feed:

```bash
.venv/bin/python -m piais --dry-run --log-level INFO
# in another shell, replay a capture (or a single sentence) at 10 msg/s:
./scripts/replay_udp.py my-capture.nmea --rate 10
```

Tests:

```bash
.venv/bin/python -m pytest -q      # 58 passed
```

## On the boat

1. **PiCAN 2**: enable SPI and the MCP2515 overlay, bring `can0` up at
   **250 kbit/s** (not 500 kbit/s), and fit the 120 Ω terminator only if this
   node is at an end of the trunk. See [`docs/HARDWARE.md`](docs/HARDWARE.md).
2. **AIS-catcher**: run it with a UDP output to `127.0.0.1:10110`.
3. **piais**: `piais-aisd --config /etc/piais.toml`, supervised by systemd.
4. **OS**: Raspberry Pi OS Lite + overlayfs, or Alpine diskless. See
   [`os/README.md`](os/README.md).

```toml
# /etc/piais.toml
[ais]
udp_host = "127.0.0.1"
udp_port = 10110
own_mmsi = 244000000        # optional; own ship is not rebroadcast as a target
min_update_interval_s = 2.0
static_update_interval_s = 60.0

[n2k]
interface = "socketcan"
channel = "can0"
preferred_address = 25
dry_run = false
```

## What is implemented

| Area | State |
| --- | --- |
| AIS decode (types 1/2/3, 5, 18, 19, 21, 24) | ✅ tested |
| N2K PGNs 129038, 129039, 129040, 129041, 129794, 129809, 129810 | ✅ tested against canboat 9.0.0 |
| Multipart AIVDM reassembly + NMEA checksum validation | ✅ tested |
| Duplicate/rate limiting, own-MMSI filter | ✅ tested |
| SocketCAN transport, address claim, fast packets | ✅ code path, ⬜ not on hardware |
| VE.Direct parser + PGN 127508 battery status | ✅ tested |
| Read-only OS recipes, systemd units | ✅ documented, ⬜ not booted |
| Sensors, BLE, LoRa | ⬜ design only |
| AIS types 4, 9, 27 | ⬜ not mapped |

## Repository layout

| Path | Contents |
| --- | --- |
| [`PLAN.md`](PLAN.md) | Living plan, decisions, work log, next actions |
| [`docs/RESEARCH.md`](docs/RESEARCH.md) | Prior art, feasibility, bandwidth and power budget |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Data flow, failure modes, read-only constraints |
| [`docs/HARDWARE.md`](docs/HARDWARE.md) | PiCAN 2, termination, isolation, power, RF |
| [`docs/SOFTWARE.md`](docs/SOFTWARE.md) | PGN mapping, unit traps, extensions |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Architecture decision records |
| [`docs/REFERENCES.md`](docs/REFERENCES.md) | Links and datasheets |
| [`os/`](os/) | RAM/read-only OS recipes and systemd units |
| `src/piais/` | The daemon and protocol code |
| `tests/` | 58 tests, including a canboat field-order guard |
| `scripts/replay_udp.py` | Replay AIVDM over UDP without a radio |

## Hardware warnings

- **NMEA 2000 is not a power bus for a Pi.** Power the Pi from a proper 5 V
  supply or buck with headroom for the RTL-SDR (plan for ~1 A peak).
- **The PiCAN 2 is not galvanically isolated.** Check your board revision. A
  ground loop through the Pi's supply and the N2K shield is a real risk; an
  isolated transceiver or isolated DC-DC is worth the money on a boat.
- **120 Ω termination**: exactly two terminators on the trunk. The PiCAN 2
  jumper must be off if this node is a drop.
- **A read-only root does not make the hardware immune to power loss.** It
  protects the SD card. Use a UPS/supercapacitor and a hardware watchdog if
  the supply is unreliable.
- AIS-catcher's prebuilt packages are not compatible with the original Pi
  Zero/Pi 1 because of floating-point support. The Zero 2 W (Cortex-A53) is
  fine.

## Licences and disclaimer

- This repository: MIT (see [`LICENSE`](LICENSE)).
- AIS-catcher is GPL-3.0 and is used as a **separate program**, not linked.
- `nmea2000` and canboat are Apache-2.0; `pyais` is MIT.
- NMEA 2000 is a proprietary standard of the National Marine Electronics
  Association. This project is not NMEA certified and is not affiliated with
  NMEA.
- Not for navigation or safety of life. AIS reception is not guaranteed and
  this software has not been validated on water.
