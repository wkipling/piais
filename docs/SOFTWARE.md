# Software design

## 1. Pipeline

```
UDP AIVDM ──► SentenceAssembler ──► pyais ──► AisTarget ──► to_nmea2000 ──► N2KSender
              (multipart,           (decode)   (normalised)   (PGN fields)    (SocketCAN /
               checksum)                                                        dry-run)
```

| Module | Responsibility |
| --- | --- |
| `ais.py` | NMEA checksum, multipart reassembly, pyais normalisation, sentinel handling |
| `pgn.py` | AIS type → PGN, exact canboat field order, SI units, N2K sentinels |
| `n2k.py` | `N2KDevice` lifecycle, address claim, dry-run frame logging |
| `vedirect.py` | VE.Direct byte state machine, PGN 127508 |
| `service.py` | asyncio UDP daemon, rate limit, own-ship filter, stats, CLI |
| `config.py` | TOML loading with unknown-key rejection |

## 2. The two traps that matter

### 2.1 Encoding is positional

`nmea2000` writes the fields **in the order supplied**. It does not look up
bit offsets. If you omit a reserved field, every following value moves into
the wrong bits, and the decoder may still produce plausible-looking numbers
(a heading of 6.37 rad instead of 295°).

Rules:

1. Supply **every** field defined by canboat, in order, including `reserved`
   and `spare`.
2. Never reorder for readability.
3. Keep `tests/test_pgn_field_order.py` green. It compares each list to
   `Database("si").pgn_variants(pgn)[0].fields`, so a canboat update fails
   loudly instead of silently corrupting data.

### 2.2 Units

canboat's SI fields are:

| Quantity | Unit |
| --- | --- |
| longitude, latitude | degrees |
| COG, heading | **radians** |
| SOG | **m/s** |
| rate of turn | rad/s |
| length, beam, draft | metres |
| temperature | kelvin |

AIS gives degrees, knots and degrees/minute. `pgn.py` converts explicitly.
The encoder is always created with `units="si"`.

**Do not use the default `units="native"`.** It interprets some canboat fields
differently and silently divided length/beam by ten during development.

## 3. AIS → PGN mapping

### 3.1 Common fields

| N2K field | Source | Notes |
| --- | --- | --- |
| `messageId` | AIS type (1/2/3/5/18/19/21/24) | lookup code |
| `repeatIndicator` | AIS repeat | 0–3 |
| `userId` | MMSI | |
| `longitude`/`latitude` | AIS position | 0x7FFFFFFF if unavailable |
| `positionAccuracy` | AIS accuracy | 0/1 |
| `raim` | AIS RAIM | 0/1 |
| `timeStamp` | AIS UTC second | 0–63; 60 if unknown |
| `cog` | AIS COG | degrees → radians; 0xFFFF if 360° |
| `sog` | AIS SOG | knots → m/s; 0xFFFF if 102.3 kn |
| `heading` | AIS heading | degrees → radians; 0xFFFF if 511 |
| `rateOfTurn` | AIS ROT | deg/min → rad/s; 0x7FFF if unavailable |
| `navStatus` | AIS status | same 0–15 code space |
| `specialManeuverIndicator` | AIS maneuver | 0–2 |
| `aisTransceiverInformation` | receive channel | A→0, B→1, unknown→15 |
| `communicationState` | — | zeroed (informational; not reconstructed) |
| `sequenceId` | internal | per-PGN 8-bit counter |

### 3.2 Per-type fields

**129038 (Class A).** The common fields plus `rateOfTurn`, `navStatus`,
`specialManeuverIndicator`.

**129039 (Class B).** `integratedDisplay` ← AIS `display`, `dsc` ← `dsc`,
`band` ← `band`, `canHandleMsg22` ← `msg22`, `aisMode` ← `assigned`.
`unitType` and `aisCommunicationState` are set to 0: AIS type 18's carrier-
sense flag is not the same thing as N2K's `unitType`, so inventing a mapping
would be worse than leaving it neutral.

**129040 (Class B extended).** `typeOfShip` ← `ship_type`, `length` =
bow+stern, `beam` = port+starboard, `positionReferenceFromBow` = bow,
`positionReferenceFromStarboard` = starboard, `name` ← `shipname`,
`gnssType` ← `epfd`, `dte` ← `dte`, `aisMode` ← `assigned`.

**129041 (AtoN).** `atonType` ← `aid_type`, `atonName` ← `name` + `name_ext`,
`offPositionIndicator`, `virtualAtonFlag`, `assignedModeFlag`,
`positionFixingDeviceType` ← `epfd`. Dimensions are mapped as length/diameter
and beam/diameter; the AtoN reference-edge semantics are approximate and
documented as such.

**129794 (Class A static).** IMO, callsign, name, ship type, dimensions,
draught, destination, AIS version, GNSS type, DTE. ETA is month/day/hour/
minute only: the year is inferred as the current year, or next year if the
date is more than a month in the past. Treat the year as a guess.

**129809/129810 (Class B static, 24A/24B).** Name in part A; ship type,
vendor ID, callsign and dimensions in part B. `mothershipUserId` is 0.

### 3.3 Sentinels

AIS and N2K both have "not available" values, but they are not the same
numbers. Writing AIS's 91° latitude into N2K would put a target at the North
Pole. `pgn.py` maps:

| Field | Sentinel |
| --- | --- |
| longitude/latitude (32-bit) | `0x7FFFFFFF` |
| COG, SOG, heading, lengths (16-bit) | `0xFFFF` |
| rate of turn (signed 16-bit) | `0x7FFF` |
| date | `0xFFFF` |
| time | `0xFFFFFFFF` |

Note: canboat's decoder reports raw `timeStamp` seconds as `None` for values
that are not named lookup entries, even though the raw bits are correct. The
tests assert on `raw_value` for that field.

## 4. Rate limiting

See `docs/ARCHITECTURE.md` §4. Defaults:

- position: 2 s per MMSI,
- static: 60 s per MMSI,
- own MMSI: dropped.

The table is pruned after 10 minutes. There is no persistence.

## 5. Unsupported AIS types

| Type | Reason |
| --- | --- |
| 4 | PGN 129793 exists but is not mapped yet |
| 9 | PGN 129798 exists but is not mapped yet |
| 27 | no standard N2K PGN for long-range reports |
| 6, 8, 12, 14 | binary/application or safety messages, not target reports |
| 10, 11, 13, 15–17, 20, 22, 23, 25, 26 | not target-position messages |

`unsupported` is counted and logged, not treated as an error.

## 6. VE.Direct

The parser is byte-oriented because the checksum byte can be any value,
including LF. It resynchronises at a CR/LF frame boundary, which means a
half-frame after a reboot is discarded and the next full frame is accepted.

Mapped to **PGN 127508** (`batteryStatus`):

| VE.Direct | N2K field | Conversion |
| --- | --- | --- |
| `V` | `voltage` | mV → V |
| `I` | `current` | mA → A; positive = charging |
| `T` | `temperature` | 0.01 °C → K |
| — | `instance` | config |
| — | `sid` | 0 (could be a counter) |

Solar-specific fields (`VPV`, `PPV`, `CS`, `MPPT`, `ERR`, yield) are parsed
but not published. There is no clean standard PGN; adding proprietary PGNs
would be a separate decision.

## 7. Adding a source or output

1. Decode the source into a small, typed value object.
2. Add a `to_nmea2000` function with the **complete ordered field list**.
3. Add the PGN to `tests/test_pgn_field_order.py`.
4. Add a round-trip test: encode → CAN frame → reassemble → canboat decode.
5. Add a rate limit or debounce if the source can repeat.
6. Document the sentinel values.

For an output (BLE, LoRa), keep it downstream of the same normalised
`AisTarget`; do not decode AIS twice.

## 8. Testing strategy

- **Unit**: AIVDM checksum, multipart, sentinels, VE.Direct frame boundaries.
- **Contract**: PGN field order against canboat.
- **Round trip**: encode, frame, reassemble, decode, assert values. This
  catches unit and bit-layout errors that a plain "encode succeeds" test does
  not.
- **Service**: rate limiting, own-MMSI, multiple sentences per datagram.
- **Hardware**: deliberately absent from CI; see `PLAN.md` Phase 2–5.

Run:

```bash
python -m pytest -q
python -m piais --dry-run --sentence '<AIVDM>'
```
