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
| `service.py` | asyncio UDP daemon, rate limit, own-ship filter, stats, CLI, control-page lifecycle |
| `web.py` | stdlib control page, SDR/Wi-Fi controllers, token store, log ring |
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
Every encoder is created with `units="si"`.

**Do not use the default `units="native"`.** It uses canboat.json's own
units for the fields listed in `nmea2000/native_units.py` (for example `%`,
`rpm`, `L`, `Ah`, `kWh`). None of the AIS PGNs implemented here are in that
table today, so both systems happen to produce identical bytes — but that is
not a property to rely on. In particular `PythonCanAsyncIOClient` hardcodes
`create_encoder(...)` with the default, so `N2KSender.start()` replaces the
client's encoder with the SI one. `backend.decode` also defaults to
`"native"`; the round-trip test helper decodes with `units="si"` to match
the encoder.

## 3. AIS → PGN mapping

### 3.1 Common fields

| N2K field | Source | Notes |
| --- | --- | --- |
| `messageId` | AIS type | lookup code |
| `repeatIndicator` | AIS repeat | 0–3 |
| `userId` | MMSI | exact |
| `longitude`/`latitude` | AIS position | `None` when unavailable |
| `positionAccuracy` | AIS accuracy | 0/1 |
| `raim` | AIS RAIM | 0/1 |
| `timeStamp` | AIS UTC second | 0–63; 60 if unknown |
| `cog` | AIS COG | degrees → radians; `None` if 360° |
| `sog` | AIS SOG | knots → m/s; `None` if 102.3 kn |
| `heading` | AIS heading | degrees → radians; `None` outside 0–359 |
| `rateOfTurn` | AIS ROT | deg/min → rad/s; `None` if unavailable |
| `navStatus` | AIS status | same 0–15 code space |
| `specialManeuverIndicator` | AIS maneuver | 0–2 |
| `aisTransceiverInformation` | receive channel | A→0, B→1, unknown→15 |
| `communicationState` | — | zeroed (AIS does not carry the N2K state) |
| `sequenceId` | internal | per-PGN 8-bit counter |

### 3.2 Per-type fields

**129038 (Class A).** The common fields plus `rateOfTurn`, `navStatus`,
`specialManeuverIndicator`.

**129039 (Class B).** `integratedDisplay` ← AIS `display`, `dsc` ← `dsc`,
`band` ← `band`, `canHandleMsg22` ← `msg22`, `aisMode` ← `assigned`.
`unitType` ← the message's CS flag (0 = SOTDMA, 1 = CS); it is not hardcoded.
`aisCommunicationState` is 0 because canboat only defines SOTDMA/ITDMA there
and a CSTDMA unit is neither; this is a documented limitation, not a claim
about the transmitter.

**129040 (Class B extended).** `typeOfShip` ← `ship_type`, `name` ←
`shipname`, `dte` ← `dte`, `aisMode` ← `assigned`, `gnssType` ← `epfd`.
Dimensions use the AIS reference-point pairs: the total is written only when
both distances are non-zero, and each reference offset is written only when
its own AIS value is non-zero (AIS encodes 0 as "not available").

**129041 (AtoN).** `atonType` ← `aid_type` (AIS types 0–31 map 1:1 to
canboat's `atonType`, verified), `atonName` ← `name` + `name_ext`,
`offPositionIndicator`, `virtualAtonFlag`, `assignedModeFlag`,
`positionFixingDeviceType` ← `epfd`. Dimensions use the same pair rule as
129040.

**129794 (Class A static).** IMO, callsign, name, ship type, dimensions,
draught, destination, AIS version, GNSS type, DTE. IMO 0 and draught 0.00 m
are AIS "not available" and become N2K "not available". ETA is month/day/
hour/minute only. AIS sentinels are month 0, day 0, hour 24 and minute 60;
those become N2K "not available" (`None`), never 24:00 or 01:00. The date and
time are independent fields, so a known date with an unknown time keeps the
date. The year is inferred as the current year, or next year if the date is
more than a month in the past. Treat the year as a guess.

**129809/129810 (Class B static, 24A/24B).** Name in part A; ship type,
vendor ID, callsign and dimensions in part B. `mothershipUserId` comes from
the auxiliary-craft variant of 24B when present, otherwise 0. 24B carries no
EPFD, so `gnssType` is written as canboat's "Default: undefined" (**0**).

### 3.3 Sentinels

AIS and N2K both have "not available" values, but they are not the same
numbers. Writing AIS's 91° latitude into N2K would put a target at the North
Pole. There are two kinds of canboat field and they need different treatment:

| Field kind | Helper | Missing value |
| --- | --- | --- |
| scaled NUMBER, DATE, TIME | `_number` | `value=None` |
| lookup, bitfield, MMSI, reserved | `_code` | an explicit code |

`value=None` writes the all-ones wire sentinel for the field's width. Raw
constants (`0xFFFF`, `0x7FFFFFFF`, `0x7FFF`, ...) must **not** be passed to a
NUMBER field: the encoder range-checks the number before packing and raises
`ValueError`. This was a real bug for battery current (a signed 16-bit field),
temperature, COG, SOG, heading, rate of turn and position — any AIS target
with a missing field could take down the send path.

LOOKUP fields have field-specific "not available" codes and are always
explicit: `timeStamp` 60, `navStatus` 15, `aisTransceiverInformation` 15.
canboat's `gnssType`/`positionFixingDeviceType` has no NA code, so the
*defined* "Default: undefined" value (0) is used; AIS EPFD 1–8 map through,
and AIS 0/9–15 become 0. Do not write 15: the decoder reports it as `None`,
which is indistinguishable from a decode failure.

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
- **Control plane**: token and Basic auth, cross-site POST refusal, SDR
  stop/start ordering and failure reporting, Wi-Fi deadline classification
  and reopen, real HTTP round trips on an ephemeral port.
- **Hardware**: deliberately absent from CI; see `PLAN.md` Phase 2–5.

Run:

```bash
python -m pytest -q
python -m piais --dry-run --sentence '<AIVDM>'
```

## 9. Control plane

The daemon can serve a status page, stop or power down the receiver, and turn
the Wi-Fi radio off when nobody is connected. It is stdlib-only and off by
default except for a loopback bind.

Two invariants matter when changing it:

1. **An unconfigured capability is not success.** Empty command arrays are
   reported as "cannot", never as a completed action. A failed power cut after
   a successful decoder stop leaves the state `stopped` with an explicit error.
2. **The page must not poll itself.** The Wi-Fi watchdog exists to turn the
   radio off; a meta-refresh or JavaScript timer would keep it on forever.

See [`CONTROL.md`](CONTROL.md) for the access model, the exact Wi-Fi rules,
privilege setup and the HTTP API.
