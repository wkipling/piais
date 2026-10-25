# Electrical telemetry: feasibility

**Status: design only.** The only electrical mapping implemented today is
VE.Direct battery voltage/current/temperature → PGN 127508. Nothing in this
document has been run against a Victron device.

**Verdict.** Shunt and MPPT over VE.Direct are straightforward: the data is
there and the PGNs are standard. The work is field mapping, instance
management and bench-testing what the MFD does with the result. A BT-only
DC-DC charger is feasible with `victron-ble`; the Orion-Tr Smart exposes
voltage and state but **not current**, while the Orion XS exposes both. BLE
can also read the shunt and MPPT, but VE.Direct is the better input wherever a
cable fits.

Scope: house shunt (SmartShunt/BMV), MPPT solar charger, and Bluetooth-only
DC-DC chargers, all published on the same NMEA 2000 identity as AIS.

The PGNs below are standard canboat definitions. Victron publishes its own
PGN list in the *Datacommunication* whitepaper; that PDF has not been
transcribed here, so the Victron-specific choices are marked **candidate** and
must be confirmed on a bench.

## 1. What already exists

### veKonvert (Digital Yacht)

A commercial, closed appliance made with Victron. It is the closest thing to
the electrical half of this project, and it is a useful statement of what an
MFD expects.

- **One VE.Direct device per unit.** Multiple batteries need multiple units.
- Auto-detects the device; compatible with BMV-700/702/700H/712, SmartShunt
  500/1000/2000 A, and all MPPT charge controllers.
- Not compatible with Victron chargers or inverters, and must not be plugged
  into the VE.Direct port of a MultiPlus, Quattro or Cerbo.
- Publishes battery voltage, charge/discharge current, state of charge and
  time to depletion, plus solar data.
- No AIS, no BLE, no sensor inputs, no open code.

Sources: <https://digitalyacht.support/product/vekonvert-victron-ve-direct-to-nmea-2000-gateway/>
and <https://www.victronenergy.com/blog/2025/01/08/digital-yachts-vekonvert/>.

### Victron GX devices

A Cerbo/Ekrano with the NMEA 2000-out feature can bridge all connected Victron
products to N2K. It is the most capable option, but it is a second computer,
costs more than the Pi, and still does not provide an AIS receiver. Victron's
own VE.Direct-to-NMEA 2000 interface is deprecated and was BMV-only.

Source: <https://www.victronenergy.com/live/ve.can:nmea-2000:start>.

### Signal K

`signalk-to-nmea2000` can publish both AIS and electrical data, but it needs a
Node server and a larger machine than this appliance should require.

### The gap

One cheap, open box that publishes AIS **and** electrical telemetry on one N2K
identity, with PGN mappings owned and tested here, and a read-only OS.

## 2. VE.Direct: shunt and MPPT

VE.Direct is 19,200 baud, 8N1, 3.3 V logic, `KEY<TAB>VALUE` text frames ending
in a checksum byte. The parser in `src/piais/vedirect.py` already handles
resynchronisation and checksum validation.

Typical fields (verify against a dump from your exact model and firmware):

| Device | Fields | Notes |
| --- | --- | --- |
| SmartShunt / BMV | `V`, `I`, `T`, `P`, `CE`, `SOC`, `TTG`, alarms, history | `T` only with a temperature sensor; `CE` is consumed Ah, not remaining capacity |
| MPPT | `V`, `I`, `VPV`, `PPV`, `CS`, `MPPT`, `OR`, `ERR`, `LOAD`, `IL`, `H19`/`H20`, `H21`/`H22`, `H23` | `PPV` is power; PV current is not always measured |

### Ports and wiring

This is the main hardware constraint, not the protocol:

- The Pi Zero 2 W has **one** PL011 UART on GPIO14/15, and Bluetooth normally
  uses it. Freeing it (`dtoverlay=disable-bt`) loses the on-board BLE needed
  for the DC-DC charger. `miniuart-bt` moves Bluetooth to the mini UART and is
  an option, but needs validation.
- Two VE.Direct devices therefore mean **USB-serial adapters** (and a hub), or
  one UART plus one USB adapter, or reading one device over BLE instead.
- A powered hub is likely on a Zero 2 W: it must also power the RTL-SDR and the
  PiCAN 2. A larger Pi has more 5 V headroom but still needs a properly sized
  supply. Measure the budget before committing.
- The official Victron VE.Direct-to-USB cable removes the level-shifting
  question and is the safest starting point; check whether the specific
  device's VE.Direct port is isolated. A bare 3-wire UART connection needs
  care; do not connect the VE.Direct port of a Victron "consumer" (MultiPlus,
  Quattro, Cerbo).
- VE.Direct is a point-to-point port. Do not parallel two devices onto one
  UART.

The Zero 2 W is the worst case, not a requirement. On a larger Pi this
constraint goes away: Pi 4/CM4-class boards can expose `uart2`–`uart5`, and
every larger board has more USB ports, so two or three VE.Direct devices can
have their own port. The software does not change; only the device paths.

## 3. BLE: Bluetooth-only Victron devices

`victron-ble` (Unlicense / public domain) parses Victron **Instant Readout**
advertisements. It is not an official Victron interface and is provided
as-is, but it is small, pure Python and runs on a Pi.

- <https://github.com/keshavdv/victron-ble>
- Uses `bleak` (MIT) over BlueZ; no GATT connection is needed for supported
  devices, because the data is broadcast.
- Relevant supported parsers: `BatteryMonitor` (SmartShunt/BMV),
  `SolarCharger`, `DcDcConverter` (Orion-Tr Smart), `OrionXS`,
  `AcCharger`, `Inverter`, `LynxSmartBMS`, `MultiRS`, `VEBus`,
  `SmartLithium`, `SmartBatteryProtect`, `BatterySense`, `DcEnergyMeter`.
- **One adapter can receive many devices at once.** Advertisements are
  broadcast; there is no per-device connection limit.
- Instant Readout must be enabled in VictronConnect.
- Each device needs its **advertisement key** from VictronConnect → Product
  Info → Instant Readout Details. The key is a secret: keep it in
  `/etc/piais.toml` with `0600` permissions, never in git. A replacement
  device has a different key.
- BLE is not a general sensor standard. Non-Victron BLE sensors need their own
  parser; treat BLE as a narrow Victron input, not a universal bus.
- If a DC-DC model also has a VE.Direct port, use that instead of BLE: it is
  richer and needs no key.

Fields differ by device family:

| BLE parser | Data | Limit |
| --- | --- | --- |
| `BatteryMonitor` | voltage, current, SOC, remaining minutes, consumed Ah, temperature, alarms | good match for 127508 + 127506 |
| `SolarCharger` | battery V/I, yield today, solar power, load current, state, error | no PV voltage; fewer fields than VE.Direct |
| `DcDcConverter` (Orion-Tr Smart) | input/output voltage, state, error, off reason | **no current** |
| `OrionXS` | input/output voltage **and current**, state, error | full set; record format is reverse-engineered, not documented by Victron |

Radio caveat: the Zero 2 W shares one 2.4 GHz radio between Wi-Fi and BLE.
Passive scanning is low duty, but range and packet loss at the installation
point need measuring. If Wi-Fi is required, prefer Ethernet over USB or accept
the coexistence trade-off.

## 4. Candidate PGN mapping

Derived from canboat 9.0.0 field definitions, not from Victron's whitepaper.

| Source | Data | PGN | Fields | Confidence |
| --- | --- | --- | --- | --- |
| Shunt | battery V, current, temperature | 127508 `batteryStatus` | voltage, current, temperature | high (temperature only with a sensor) |
| Shunt | state of charge | 127506 `dcDetailedStatus` | `stateOfCharge` | high |
| Shunt | time to go | 127506 | `timeRemaining` | high (minutes → seconds) |
| Shunt | consumed Ah | 127506 | `remainingCapacity` | **no**: needs the configured capacity to compute remaining |
| Shunt | capacity/chemistry | 127513 `batteryConfigurationStatus` | capacity, chemistry | only from explicit config, not inferred |
| MPPT | battery V/I | 127508 | voltage, current | high |
| MPPT | charger state (`CS`) | 127507 `chargerStatus` | `operatingState` | high; CS 0→Not charging, CS 3→Bulk (N2K 1), CS 4→Absorption (2), CS 5→Float (5), CS 2→Fault (9) |
| MPPT | PV voltage (`VPV`) | 127751 `dcVoltageCurrent` | `dcVoltage` | candidate |
| MPPT | PV current | 127751 | `dcCurrent` | only if the model measures it; do **not** derive it from `PPV/VPV` |
| MPPT | PV power (`PPV`) | — | — | no standard PGN; leave it off the bus |
| MPPT | yield today/total (`H19`/`H20`) | — | — | no standard PGN; leave it off the bus |
| DC-DC (BLE) | input/output voltage | 127751 | `dcVoltage` with separate `connectionNumber` | candidate |
| DC-DC (BLE) | input/output current | 127751 / 127508 | `dcCurrent` | Orion XS only |
| DC-DC (BLE) | charger state | 127507 | `operatingState` | candidate |
| Any | temperature with a known source | 130316 `temperatureExtendedRange` | `temperature`, `source` | candidate |

Open mapping questions:

- **MPPT `T`**: some models report battery temperature, others charger
  temperature. Do not put it in 127508's battery `temperature` until the
  source is confirmed on the specific model.
- **`dcType`**: 127506 has `dcType = Solar cell` (3) and `Convertor` (2), but
  127506 is a battery/DC-source *status* PGN. Publishing it for an MPPT without
  SOC data may confuse an MFD; confirm on the bench before adding it.
- **`connectionNumber`**: using 0/1 for input/output on 127751 is a convention,
  not verified against an MFD.
- **Instances**: charger, battery and DC instances must be unique and
  configurable. This is the same problem Victron's guide describes for
  multiple battery monitors.

## 5. Proven, assumed, unknown

| Item | Status |
| --- | --- |
| VE.Direct framing, checksum, resync | **proven** by unit tests |
| 127508 encode/decode with SI units | **proven** by round-trip tests |
| `victron-ble` device coverage and advertisement model | **observed** in its source and README |
| canboat PGN/field definitions above | **proven** from the installed database |
| MFDs display 127506/127507/127751 for these sources | **assumed** — bench test |
| Victron's exact PGN choices match this table | **assumed** — whitepaper not read |
| MPPT `T` is battery temperature | **unknown** |
| Specific DC-DC model exposes current over BLE | **unknown** — Orion-Tr Smart does not, Orion XS does |
| BLE range and Wi-Fi coexistence on the Zero 2 W | **unknown** — measure |
| 5 V power budget with SDR + PiCAN + USB hub | **unknown** — measure |

## 6. Phased plan

1. **E0 — read only.** Connect the shunt and MPPT one at a time and log parsed
   VE.Direct frames. Compare against the Victron app. No N2K yet.
2. **E1 — shunt.** Publish 127508 + 127506. Confirm the MFD shows voltage,
   current, SOC and time remaining with the right battery instance.
3. **E2 — MPPT.** Publish 127508 + 127507. Add 127751 only if the MFD displays
   PV voltage sensibly; never derive PV current.
4. **E3 — DC-DC over BLE.** Run `victron-ble read` on the Pi, then integrate
   the parser behind the same electrical-source interface. Publish only what
   the device measures.
5. **E4 — unify.** One `ElectricalSource` abstraction with configured
   instances; then add sensors and LoRa summaries.

Do not start with BLE for everything. VE.Direct is richer, needs no key
management and has deterministic timing; use BLE only where a cable is not
possible.

## 7. Decisions recorded

- **ADR-010**: VE.Direct first, BLE for Bluetooth-only devices.
- **ADR-011**: publish measured values only; no derived PV current, no
  remaining capacity without a configured capacity, no guessed temperature
  source.

## 8. Powering the Pi from the N2K bus

Recorded because it is the chosen arrangement, not because it is endorsed.
NMEA 2000 provides a 12 V pair for the bus, not a general-purpose load bus:
the standard expects a single power insertion point and a defined backbone
current budget. Adding a Raspberry Pi and an SDR is outside that intent and
depends entirely on the installed backbone and its supply headroom.

Working figures, all approximate and **unmeasured** on this build:

| Load | Typical |
| --- | --- |
| Zero 2 W, Wi-Fi on, light CPU | 0.8–1.2 W |
| Zero 2 W, Wi-Fi off | 0.6–0.9 W |
| RTL-SDR dongle | 1.0–1.7 W |
| PiCAN 2 (MCP2515) | ~0.3 W |
| 12 V → 5 V buck, 85 % efficiency | +15 % of the above |

So roughly 3–4 W, or 0.25–0.35 A at 12 V, with higher peaks when the radio
transmits or the dongle enumerates. Confirm the backbone supply and the
voltage at the Pi's drop under load; a long backbone with many drops sags.

Practical rules:

1. **One insertion point.** Do not add a second 12 V feed at the Pi's drop
   unless the backbone is designed for it. Feed the buck from the existing
   power pair with a fuse, and keep the drop short.
2. **The SDR switch must not switch the Pi.** `power_off_command` may cut the
   dongle's USB port only. Cutting the Pi's 5 V rail from software is not a
   supported configuration and would take the control page with it.
3. **Expect brownouts.** Engine cranking sags the whole bus. Use a buck with
   under-voltage lockout and enough bulk capacitance to ride out the dip, and
   keep the hardware watchdog enabled. The RAM-resident OS protects the SD
   card from an abrupt loss; it does not keep the current boot alive.
4. **Bus off means Pi off.** The control page, Wi-Fi window and SDR switch are
   only reachable while the bus is powered. A device that needs to be
   reachable with the instruments off needs a separate supply.
5. **Wi-Fi is a real load.** Turning the radio off saves roughly 0.2–0.4 W at
   the 5 V rail. That is the point of the watchdog, beyond the security one.
