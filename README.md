# piais

**AIS from an RTL-SDR to NMEA 2000, on a Raspberry Pi.**

An RTL-SDR receives the two AIS VHF channels. AIS-catcher decodes the
sentences; `piais-aisd` converts the targets to standard NMEA 2000 PGNs and
puts them on the bus over SocketCAN. The Pi can run without a writable root,
so a power cut cannot corrupt the SD card. Runs on any 64-bit Raspberry Pi;
a Zero 2 W is the minimum.

> **Status: AIS core implemented, no boat hardware yet.** The decode → PGN →
> CAN-frame path is covered by 119 tests against the real canboat 9.0.0
> database. Nothing has been run against a PiCAN 2, an RTL-SDR or a live
> NMEA 2000 bus. Electrical and BLE telemetry remain feasibility notes
> ([`docs/ELECTRICAL.md`](docs/ELECTRICAL.md)).

## Why another project

AIS-catcher can transmit AIS to N2K (`-I can0`), but it is AIS-only and
experimental. Victron GX devices publish electrical data, but they are a
second computer with no AIS receiver. Signal K works, with a Node server and
plugins to keep running. The gap is a small, open, single N2K node whose PGN
mappings are owned and tested here, on an OS that survives power loss.

## Architecture

```mermaid
flowchart LR
    ANT[162 MHz antenna] --> SDR[RTL-SDR]
    SDR --> CAT[AIS-catcher]
    CAT -->|AIVDM over UDP| DAEMON[piais-aisd]
    DAEMON -->|AIS PGNs| CAN[SocketCAN can0]
    CAN --> N2K[NMEA 2000]
    N2K --> PLOT[Chart plotter]
```

The Pi is not a router. AIS-catcher does the DSP, `piais-aisd` does the
protocol conversion, and `nmea2000` does address claiming and fast-packet
framing.

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
.venv/bin/python -m pytest -q      # 119 passed
```

## On the boat

1. **PiCAN 2**: enable SPI and the MCP2515 overlay, bring `can0` up at
   **250 kbit/s**, and fit the 120 Ω terminator only if this node is at an end
   of the trunk. See [`docs/HARDWARE.md`](docs/HARDWARE.md).
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

[n2k]
interface = "socketcan"
channel = "can0"
preferred_address = 25
dry_run = false

[web]
enabled = true              # loopback-only until host/password are set
```

Every other option, including receiver power control and the Wi-Fi watchdog,
is in [`config/piais.example.toml`](config/piais.example.toml).

## Control page

`piais-aisd` can serve a small status, control and debug page. It starts
before the bus and survives a CAN failure, so it is available when the bus is
the thing that is broken. It is loopback-only and token-protected by default.
It can also stop the AIS decoder, cut power to the receiver where the hardware
supports it, and turn the Wi-Fi radio off when nobody connects. Privileges,
rules and the HTTP API are in [`docs/CONTROL.md`](docs/CONTROL.md).

## What is implemented

| Area | State |
| --- | --- |
| AIS decode (types 1/2/3, 5, 18, 19, 21, 24) | ✅ tested |
| PGNs 129038, 129039, 129040, 129041, 129794, 129809, 129810 | ✅ tested against canboat 9.0.0 |
| Multipart AIVDM reassembly + checksum validation | ✅ tested |
| Duplicate/rate limiting, own-MMSI filter | ✅ tested |
| SocketCAN transport, address claim, fast packets | ✅ code path, ⬜ not on hardware |
| Control page, receiver power, Wi-Fi watchdog | ✅ tested, ⬜ not on hardware |
| Read-only OS recipes and systemd units | ✅ documented, ⬜ not booted |
| VE.Direct parser + PGN 127508 | ✅ tested |
| Shunt/MPPT PGNs, Victron BLE | ⬜ feasibility only |
| Sensors, BLE NMEA output, LoRa | ⬜ design only |
| AIS types 4, 9, 27 | ⬜ not mapped |

## Repository layout

| Path | Contents |
| --- | --- |
| [`PLAN.md`](PLAN.md) | Living plan, decisions, work log, next actions |
| [`docs/RESEARCH.md`](docs/RESEARCH.md) | Prior art, feasibility, bandwidth and power budget |
| [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) | Data flow, failure modes, read-only constraints |
| [`docs/HARDWARE.md`](docs/HARDWARE.md) | PiCAN 2, termination, isolation, power, RF |
| [`docs/SOFTWARE.md`](docs/SOFTWARE.md) | PGN mapping, unit traps, extensions |
| [`docs/CONTROL.md`](docs/CONTROL.md) | Control page, receiver power, Wi-Fi watchdog, privileges |
| [`docs/ELECTRICAL.md`](docs/ELECTRICAL.md) | Electrical telemetry feasibility and candidate PGNs |
| [`docs/DECISIONS.md`](docs/DECISIONS.md) | Architecture decision records |
| [`docs/REFERENCES.md`](docs/REFERENCES.md) | Links and datasheets |
| [`os/`](os/) | RAM/read-only OS recipes and systemd units |
| `src/piais/` | The daemon and protocol code |
| `tests/` | 119 tests, including a canboat field-order guard |
| `scripts/replay_udp.py` | Replay AIVDM over UDP without a radio |

## Hardware warnings

- **Budget the Pi's power.** N2K provides a 12 V pair for the bus, not an
  unlimited supply. A Zero 2 W plus RTL-SDR peaks around 1 A at 5 V; allow
  headroom in the buck and check the voltage at the Pi's drop. See
  [`docs/ELECTRICAL.md`](docs/ELECTRICAL.md) §8.
- **The PiCAN 2 is not galvanically isolated.** Check the board revision. A
  ground loop through the Pi supply and the N2K shield is a real risk; an
  isolated transceiver or isolated DC-DC is worth the money on a boat.
- **Exactly two 120 Ω terminators** on the trunk. The PiCAN 2 jumper must be
  off if this node is a drop.
- **A read-only root protects the SD card, not the running system.** Use a
  hardware watchdog and a supply that tolerates brownouts.
- AIS-catcher's prebuilt packages do not run on the original Pi Zero/Pi 1;
  the Zero 2 W and every larger 64-bit Pi are fine.

## Licence

- This repository: **GPL-3.0-or-later** (see [`LICENSE`](LICENSE)). You can
  use, study, share and modify it; if you distribute a modified version, it
  must stay open under the same terms.
- AIS-catcher is GPL-3.0 and is used as a separate program. Both licences are
  compatible; the split is operational, not a licence firewall.
- `nmea2000`/canboat are Apache-2.0; `pyais` is MIT; `victron-ble` is
  Unlicense. All are compatible with GPL-3.0.
- NMEA 2000 is a proprietary standard of the National Marine Electronics
  Association. This project is not NMEA certified and is not affiliated with
  NMEA.
- Not for navigation or safety of life. AIS reception is not guaranteed and
  this software has not been validated on water.
