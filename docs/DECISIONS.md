# Architecture decision records

Short records of decisions that were not obvious. Each one says what was
decided, why, and what it costs.

## ADR-001 — Use AIS-catcher as an external decoder, not a library

**Status:** accepted.

**Decision:** run AIS-catcher as a separate process and consume its NMEA 0183
output over UDP. Do not link `aiscat` or vendor AIS-catcher's C++ into piais.

**Why:** AIS-catcher owns the RTL-SDR and does the DSP. Keeping it external
lets the SDR be swapped for a serial receiver, lets either process be
restarted or updated independently, and keeps the demodulator's crash blast
radius away from the N2K writer. The licence boundary was the original
motivation; after ADR-012 both projects are GPL-3.0, so this now stands on
operational grounds.

**Cost:** an extra process, an extra UDP hop, and a second service to
supervise. Acceptable on a 512 MB Pi.

**Alternatives:** `aiscat` (licence-compatible after ADR-012; a decoder only, so
it would not replace AIS-catcher's SDR handling), direct SDR DSP in piais
(too much work, worse sensitivity).

## ADR-002 — pyais for payload decoding

**Status:** accepted.

**Decision:** use `pyais` (MIT).

**Why:** pure Python, no build step, supports all message types and multipart,
fast enough for a single station. `aiscat` is about 14× faster and ships
aarch64/musllinux wheels, but it would replace pyais's objects with
AIS-catcher's JSON schema and move the decode layer onto AIS-catcher's release
cycle. At ~50 msg/s the throughput is irrelevant; keeping the layer in Python
means piais can be tuned without a native toolchain or an upstream schema.
Licence compatibility is no longer a tie-breaker after ADR-012.

**Cost:** slower than `aiscat` or libais; irrelevant at ~50 msg/s.

## ADR-003 — canboat + nmea2000 for N2K encoding and address claim

**Status:** accepted.

**Decision:** encode with `nmea2000`, which wraps canboat's Rust PGN database,
and use its `N2KDevice` for ISO 11783-5 address claiming and fast packets.

**Why:** canboat is the de-facto open N2K definition and is actively
maintained. `nmea2000` publishes ARM64 wheels for Raspberry Pi OS (Zero 2 W and
larger), so deployment does not need a Rust toolchain. Address claiming is
non-trivial; reusing a tested implementation is safer than writing one.

**Cost:** a dependency on canboat's field definitions. Mitigated by the
field-order test and a pinned major version.

## ADR-004 — piais owns rate limiting and the PGN subset

**Status:** accepted.

**Decision:** do not forward every AIS report. Rate-limit per MMSI and per
report class; do not forward own ship.

**Why:** a busy harbour produces duplicates from both channels and can
saturate a 250 kbit/s bus with five-frame PGNs. An MFD target list also does
not need 10 Hz updates.

**Cost:** a target may appear up to 2 s late. Configurable.

## ADR-005 — Do not depend on AIS-catcher's built-in N2K output

**Status:** accepted, with a bench exception.

**Decision:** AIS-catcher's experimental `-I can0` is a Phase 2 validation
tool, not the production path.

**Why:** upstream explicitly says it is experimental, not NMEA certified and
"not intended for connecting to a NMEA2000 network on a boat". It has no
VE.Direct or sensor inputs, no piais rate limiting, and its field mapping is
not covered by this repository's tests. piais exists to own those concerns.

**Cost:** one more process than the absolute minimum. If AIS is the only
requirement and `-I` proves reliable, Phase 2 may show that piais is
unnecessary — that is a valid outcome and should be recorded here.

**Revisited 2026-10-25:** relicensing piais (ADR-012) removes the licence
obstacle but not the reason for this decision. Upstream still calls the N2K
output experimental, and the two writers must not share the bus.

## ADR-006 — Raspberry Pi OS + overlayfs first, Alpine diskless later

**Status:** accepted.

**Decision:** the default recipe is Raspberry Pi OS Lite 64-bit with the
overlay file system enabled. Alpine diskless is documented as the fully-RAM
alternative.

**Why:** overlayfs gives the power-cut protection (no root writes) while
keeping systemd, apt, AIS-catcher's installer and the nmea2000 glibc wheels
working. Alpine needs OpenRC units and a musl build of AIS-catcher, which is
a project in itself.

**Cost:** the OS is still read from SD at boot, and the overlay uses RAM. That
is acceptable; the SD card is not written.

## ADR-007 — No persistent storage on the Pi

**Status:** accepted.

**Decision:** no database, no log files, no cache on the root filesystem.

**Why:** the primary requirement is surviving power cuts without SD-card
corruption. Persistent capture, if needed, goes to a separate writable device.

**Cost:** logs are lost on reboot and AIS history is not retained. AIS is a
live picture; this is acceptable.

## ADR-008 — LoRa carries summaries, not AIS

**Status:** accepted.

**Decision:** any LoRa output is a low-rate digest or telemetry, never a
transparent relay of NMEA.

**Why:** raw AIS at a busy station exceeds LoRa's practical throughput by an
order of magnitude. See `docs/RESEARCH.md` §2.6.

**Cost:** a remote receiver sees a filtered picture, not every message.

## ADR-009 — VE.Direct maps only to PGN 127508 for now

**Status:** accepted.

**Decision:** publish battery voltage, current and temperature. Do not invent
PGNs for solar yield, MPPT state or error codes.

**Why:** 127508 is standard and well understood. Solar-specific data has no
clean standard PGN; a proprietary mapping should be a deliberate, documented
choice, not an accident.

**Cost:** less telemetry on the bus in v1.

## ADR-010 — VE.Direct first, BLE for Bluetooth-only devices

**Status:** accepted for the electrical phase (not implemented).

**Decision:** read the shunt and MPPT over VE.Direct wherever a cable is
practical; use BLE (`victron-ble`, Unlicense) only for devices with no
VE.Direct port, such as a Bluetooth-only Orion-Tr Smart.

**Why:** VE.Direct exposes more fields, needs no per-device encryption key,
and has deterministic timing. BLE Instant Readout is advertisement-based and
convenient (one adapter can hear many devices), but it is an unofficial
interface, needs a secret key per device, and shares the 2.4 GHz radio with
Wi-Fi on the Zero 2 W.

**Cost:** a cable and a serial port per VE.Direct device. On a Zero 2 W that
means USB-serial adapters and a hub, because the single PL011 UART is normally
used by Bluetooth; a larger Pi can expose extra UARTs or simply has more USB
ports.

**Alternatives:** BLE for everything (fewer wires, fewer fields, more secrets);
Victron GX device (most capable, expensive, no AIS).

## ADR-011 — Publish measured electrical values only

**Status:** accepted.

**Decision:** do not derive N2K values that the source did not measure. In
particular, do not compute PV current from `PPV / VPV`, do not publish
remaining Ah without a configured battery capacity, and do not label an MPPT
internal temperature as battery temperature.

**Why:** this is the same rule as the AIS sentinels: a plausible-looking
number is worse than “not available”. Derived values can also be wrong in
ways the user cannot see, especially when the source is in a transient state.

**Cost:** fewer fields on the bus until the source exposes them or the user
configures the missing constant.

## ADR-012 — Relicense piais to GPL-3.0-or-later

**Status:** accepted, 2026-10-25.

**Decision:** distribute piais under GPL-3.0-or-later (previously MIT).

**Why:** the MIT/Apache boundary existed only to keep AIS-catcher's GPL-3.0
decoder at arm's length. That constrained what could be linked or adapted for
a benefit this project does not need. There is no requirement to let
commercial users close their changes. GPL-3.0-or-later is compatible with
AIS-catcher (GPL-3.0) and canboat (Apache-2.0), and it keeps distributed
derivatives open.

**Cost:** anyone distributing a modified piais must publish the source under
the same terms. Code already published under MIT (commits before this change)
remains available under MIT; the relicense applies from this version onward.

**What does not change:** the process split. AIS-catcher still owns the SDR
and piais still owns the bus, for operational reasons rather than licensing.
