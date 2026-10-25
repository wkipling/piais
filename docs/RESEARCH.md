# Research and prior-art survey

All links, versions and licences below were checked on 2026-10-25. Where a
statement is an inference rather than something a project or datasheet says,
it is marked **inference**.

## 1. What AIS actually is

AIS is a VHF data link, not a broadband signal:

| Property | Value |
| --- | --- |
| Channels | A: 161.975 MHz (87B), B: 162.025 MHz (88B) |
| Channel spacing | 25 kHz |
| Modulation | GMSK, 9600 bit/s |
| Slot | 26.04 ms (256 bits) |
| Access | SOTDMA (Class A) / CSTDMA (Class B) |
| Position report size | type 1 = 168 bits; type 5 is multi-sentence |

Two 25 kHz channels fit comfortably inside one RTL-SDR capture; AIS-catcher
demodulates both from a single dongle. The hard part is not bandwidth, it is
demodulation and clock recovery at low SNR.

**Consequence for this project:** an RTL-SDR is a perfectly reasonable AIS
front end, but a dedicated receiver (dAISy, or a hardware AIS receiver) will
usually hear more weak packets. The ESP-AIS sibling project exists because
SX127x-based receivers are marginal; the RTL-SDR is much better at this.

## 2. Prior art, by layer

### 2.1 AIS demodulation and NMEA output

**[AIS-catcher](https://github.com/jvde-github/AIS-catcher)** — GPL-3.0,
v0.68-era, actively maintained.

- Inputs: RTL-SDR, AirSpy, HackRF, HydraSDR, SDRPlay, SoapySDR, ZMQ/RTL-TCP,
  files, serial, and NMEA 2000 input.
- Outputs: NMEA 0183 over UDP/TCP/HTTP, JSON, web viewer, community feed,
  PostgreSQL, **and experimental NMEA 2000 over SocketCAN**.
- Install script for Debian/Ubuntu/Raspberry Pi. It installs a service and
  builds the NMEA2000 library by default on Linux.

Performance tuning (from upstream docs):

- Default RTL-SDR sample rate is 1536 kS/s.
- `-F` enables fixed-point downsampling. On a 700 MHz Pi Model B it cut decode
  time from 17.3 s to 7.7 s; upstream reports **~40% CPU on a Pi Zero W** with
  it. The Zero 2 W is a quad Cortex-A53 and substantially faster, so CPU
  headroom is likely. **Not measured on the Zero 2 W here.** The Zero 2 W is
  the minimum target; every larger Pi has more CPU headroom.
- Upstream warns that prebuilt packages are not compatible with the original
  Pi Zero / Pi 1 due to floating-point support. The Zero 2 W and every larger
  64-bit Pi are fine.
- `-s` sets sample rate; supported internal rates include 96k, 192k, 288k,
  384k, 768k, 1536k, 3072k, 6144k, 12288k.
- `-gr` sets RTL-SDR gain/AGC (`AIS-catcher -gr tuner 33.3 rtlagc ON`).
- `freqoffset` corrects PPM error; a TCXO dongle is strongly preferable.

**Built-in NMEA 2000 (the important discovery):**

```
AIS-catcher -I can0          # write AIS PGNs to SocketCAN
AIS-catcher -i can0          # read AIS PGNs from SocketCAN
```

It requires a build with Timo Lappalainen's NMEA2000 library
(`./scripts/build-NMEA2000.sh`), which the Linux install script includes by
default. Upstream's own documentation says:

> "NMEA2000 support in AIS-catcher is experimental ... The program is not
> certified by NMEA and is not intended for connecting to a NMEA2000 network
> on a boat. It is for experimenters ... Use at your own risk."

and that the implementation handles AIS messages **1–5, 9, 11, 14, 18, 19, 21
and 24**, tested at a high level with canboat and a virtual network.

Why upstream is cautious, beyond the quote:

- The N2K output is an **optional build** (Timo Lappalainen's C++ library), so
  a package that starts and decodes AIS may still have no `-I` support.
- Validation was against canboat and a **virtual network**, not a boat's
  backbone with real address claiming, ISO requests and other devices.
- NMEA 2000 is a proprietary standard. Address claim, fast-packet sequencing
  and PGN field layouts are easy to get subtly wrong, and a bad N2K writer can
  disturb the whole bus in a way a bad NMEA 0183 sentence cannot.
- AIS-catcher's core is DSP; N2K is a side output, not the project's main
  field-tested path.

The last two points are an assessment, not upstream wording.

This means **the shortest path to "AIS on N2K" may be one program**. It is
also the best possible reference for validating piais. But it is explicitly
not a production boat path, and it does not give you VE.Direct, sensors,
rate limiting or a tested field contract. Hence ADR-005. The licence is no
longer an obstacle after ADR-012; the experimental status is.

**[rtl_ais](https://github.com/dgiardini/rtl-ais)** — small dual-channel
receiver with UDP/TCP NMEA output. Older and less sensitive than AIS-catcher.

**[dAISy](https://github.com/astuder/dAISy)** — open hardware/firmware AIS
receiver (not SDR). Useful as a low-power, low-CPU alternative if the Pi's USB
and CPU budget becomes a problem.

### 2.2 AIS payload decoding

| Library | Licence | Notes |
| --- | --- | --- |
| [pyais](https://github.com/M0r13n/pyais) | MIT | Pure Python, all message types, multipart helpers. **Used here.** |
| [libais](https://github.com/schwehr/libais) | Apache-2.0 | C++, fast, older. |
| [aiscat](https://pypi.org/project/aiscat/) | GPL-3.0 | Python binding to AIS-catcher's decoder. Very fast; wheels for manylinux/musllinux aarch64. A decoder only: it does not read the SDR, and its dicts are AIS-catcher's JSON schema. Not used. |

`aiscat` is a **decoder**, not an SDR front end. It consumes NMEA AIVDM/AIVDO
(or AIS-catcher JSON or 0xac packets) and returns dicts. It cannot replace
AIS-catcher's job of owning the RTL-SDR and doing the DSP; it would replace
`pyais` plus this repo's sentence assembler. It does multipart reassembly
internally and adds AIS-catcher's lookup text and binary ASM interpretation
(types 6/8), none of which piais maps to PGNs today.

pyais is the right trade-off for a Pi appliance: pure Python, no compile step,
fast enough for ~50 msg/s. `aiscat` is roughly 14× faster and would broaden
decoding, but at this workload that buys nothing and costs control over the
decode layer.

Checked against this repo's own sentences: `aiscat` 0.68.16 decodes the type 1
and multipart type 5 fixtures, returns the `channel` field, and raises
`ValueError` on garbage. An adapter would work; it is still not worth taking
the dependency.

### 2.3 AIS → NMEA 2000

This is the layer people ask about, and there are several answers:

**[AIS-catcher `-I`](https://jvde-github.github.io/AIS-catcher-docs/configuration/output/NMEA2000/)**
— experimental, GPL-3.0, C++, requires NMEA2000 library. See above.

**[AK-Homberger/NMEA2000-AIS-Gateway](https://github.com/AK-Homberger/NMEA2000-AIS-Gateway)**
— GPL-3.0, ESP32, reads NMEA 0183 AIS and emits N2K PGNs. This is the
closest conceptual match to piais. It uses Timo Lappalainen's NMEA2000 library
and `aduvenhage/ais-decoder`. Good reference for PGN selection; GPL-3.0, so
compatible with piais after ADR-012, though it is not vendored.

**[Signal K](https://signalk.org/) + [signalk-to-nmea2000](https://github.com/SignalK/signalk-to-nmea2000)**
— Apache-2.0. A full marine data server, plugin converts Signal K paths to
N2K PGNs. This is the "batteries included" route: you get a web UI, multiple
sources, history and conversions. On a Zero 2 W it is a much larger footprint
than this daemon, and it is another moving part. Good choice if you already
run Signal K; overkill for a single-purpose AIS bridge.

**[canboat](https://github.com/canboat/canboat)** — Apache-2.0. The PGN
database and analyser. Its `canboat.json` is the authoritative open
reverse-engineered definition piais encodes against.

**[nmea2000](https://github.com/tomer-w/nmea2000)** — Apache-2.0. Python
encoder/decoder built on canboat's Rust crate, with `N2KDevice` address
claiming, fast-packet handling and SocketCAN support. Prebuilt wheels for
Raspberry Pi OS (including Zero 2 W, 32- and 64-bit) and Linux ARM64 musl.

### 2.4 NMEA 2000 PGNs used

| AIS type | PGN | canboat id |
| --- | --- | --- |
| 1, 2, 3 | 129038 | `aisClassAPositionReport` |
| 18 | 129039 | `aisClassBPositionReport` |
| 19 | 129040 | `aisClassBExtendedPositionReport` |
| 21 | 129041 | `aisAidsToNavigationAtonReport` |
| 5 | 129794 | `aisClassAStaticAndVoyageRelatedData` |
| 24A | 129809 | `aisClassBStaticDataMsg24PartA` |
| 24B | 129810 | `aisClassBStaticDataMsg24PartB` |

All are Fast Packet PGNs. canboat 9.0.0 was used for the field definitions in
this repository. The exact field order is enforced by a test.

Not mapped yet: type 4 (PGN 129793 UTC/date), type 9 (PGN 129798 SAR
aircraft), type 27 (long-range; no standard N2K PGN).

### 2.5 VE.Direct

Victron's VE.Direct is a 19,200 baud text protocol: `KEY<TAB>VALUE` lines,
terminated by a `Checksum` line. The checksum is chosen so the sum of **all**
frame bytes, including the leading CR/LF, tabs, and the checksum byte itself,
is zero modulo 256.

- [karioja/vedirect](https://github.com/karioja/vedirect) — MIT, Python. The
  canonical small reference. Its state machine is the basis for the parser
  here.
- Common fields: `PID`, `V` (mV), `I` (mA, positive = charging), `VPV`, `PPV`,
  `CS` (charge state), `MPPT`, `ERR`, `LOAD`, `T` (0.01 °C, not on all units).
  The MPPT `T` field is not always battery temperature; do not publish it as
  127508 battery temperature without checking the model.
- No clean standard N2K PGN exists for solar-specific data. Battery voltage
  and current map well to PGN 127508 (`batteryStatus`); charger state fits
  127507 (`chargerStatus`); PV voltage may fit 127751 (`dcVoltageCurrent`).
  PV power and yield have no standard PGN and stay off the bus. piais only
  claims 127508 for now. See [`ELECTRICAL.md`](ELECTRICAL.md) for the candidate
  mapping and the open questions.

### 2.6 LoRa

Prior art: [beinnlora/AIS-repeater](https://github.com/beinnlora/AIS-repeater)
— GPL-3.0, 2016, Teensy 3.1, described as complementary to a `lora-gateway`
repository. Old but real.

**Does raw AIS fit LoRa?** No. AIS is 9600 bit/s per channel. NMEA sentences
for a busy station run to tens of messages per second; even 50 msg/s × 40
bytes = 16 kbit/s, well above LoRa's typical ~5.5 kbit/s at SF7/BW125 and far
above what you get at useful range. A 168-bit position report per target per
minute, on the other hand, is trivial: 50 targets × ~20 bytes/min ≈ 130 bit/s.
**Send summaries, not the stream.**

LoRa is therefore a good fit for:
- a low-rate "targets in range" digest,
- own-ship/telemetry beacons,
- an emergency/safety message path,
and a bad fit for a transparent AIS relay. This project treats LoRa as an
optional summary output, not a transport for NMEA.

### 2.7 BLE

BLE has two separate roles:

- **NMEA 0183 out** to a phone chart plotter (NUS/SPP-like). A small,
  well-understood output; not implemented yet.
- **Victron Instant Readout in.** `victron-ble` (Unlicense) parses the
  encrypted advertisements from SmartShunt/BMV, MPPT, Orion-Tr Smart and
  Orion XS. It is advertisement-based, so one adapter can receive many
  devices; each device needs an advertisement key from VictronConnect.
  See [`ELECTRICAL.md`](ELECTRICAL.md).

### 2.8 Read-only / RAM-resident OS

Two credible approaches:

**Raspberry Pi OS Lite + overlayfs.** `raspi-config` → Performance → Overlay
File System makes `/` copy-on-write in RAM; writes never touch the SD card.
Also make the boot partition read-only. systemd, apt, AIS-catcher's installer
and the nmea2000 wheels all work. This is the low-friction path.

**Alpine Linux diskless.** The root filesystem is a tmpfs; the OS is unpacked
into RAM at boot and configuration is persisted explicitly with `lbu commit`.
This is closer to "the OS runs in RAM". Costs: OpenRC instead of systemd,
musl instead of glibc, and AIS-catcher's Debian installer will not work; you
must build or package it. nmea2000 does publish musl ARM64 wheels, so the
Python side is viable.

Neither makes the hardware immune to a power cut. They protect the SD card.
A UPS/supercapacitor and a hardware watchdog are still required.

### 2.9 Power

Estimates, not measurements:

| Load | Typical | Peak |
| --- | --- | --- |
| Pi Zero 2 W | 0.5–1.5 W | ~2.5 W |
| RTL-SDR | 1.0–1.5 W | ~1.7 W |
| PiCAN 2 | ~0.2 W | ~0.3 W |
| **Total at 5 V** | **~0.4–0.6 A** | **~0.9–1.0 A** |

Plan for a 5 V supply or buck rated **2 A** with bulk capacitance. NMEA 2000
backbone power is not intended to power a Pi plus SDR; use a separate supply
or a properly rated buck from the boat's 12 V, fused.

The table is the Zero 2 W baseline. A Pi 4/5 adds several watts and needs a
3 A+ supply and more cooling; measure the actual board before choosing a buck.

## 3. What is still unknown

- Real CPU load and USB stability of AIS-catcher on a Zero 2 W (the minimum
  target).
- Real sensitivity of the chosen RTL-SDR + antenna compared with the boat's
  existing receiver.
- Whether the PiCAN 2 can be powered and grounded without noise on a real
  boat; isolation is the likely fix.
- Address-claim behaviour of `nmea2000` against the specific plotter.
- Long-term SD-card and filesystem behaviour after repeated power cuts.
- Whether AIS-catcher's `-I` output is good enough to skip piais entirely.

These are exactly the Phase 2–4 bench tasks in `PLAN.md`.
