"""Map normalised AIS targets onto NMEA 2000 PGNs.

The PGN field order here is **positional**: ``nmea2000``'s encoder writes the
fields in the order they are supplied, so every field defined by canboat must
be present, in canboat order, including reserved and spare fields.  The test
suite compares these lists against the installed canboat database so a canboat
update cannot silently shift a value into the wrong bits.

Values are given in SI units (radians, m/s, metres).  The encoder is always
created with ``units="si"``; the default ("native") interprets some canboat
fields differently and silently scales lengths by ten.

Missing data is written as NMEA 2000 "not available", never as a plausible
number.  There are two kinds of field and they are handled differently:

* **Numeric** fields are scaled physical quantities.  Pass ``value=None`` and
  the encoder writes the correct all-ones sentinel for the field's width.
  Passing the raw sentinel (``0xFFFF`` and friends) is a bug: the encoder
  range-checks the number first and raises ``ValueError``.
* **Exact** fields are lookups, bitfields, MMSIs, dates and reserved bits.
  Pass ``raw_value``.  Their "not available" code is field-specific, so it is
  always written explicitly (``timeStamp`` 60, ``navStatus`` 15, ...).

PGNs implemented:

======  ======  ==================================================
PGN     AIS     Meaning
======  ======  ==================================================
129038  1,2,3   Class A position report
129039  18      Class B position report
129040  19      Class B extended position report
129041  21      Aid-to-navigation report
129794  5       Class A static and voyage related data
129809  24A     Class B static data, part A
129810  24B     Class B static data, part B
======  ======  ==================================================

Not yet mapped: type 4 (base station report, PGN 129793), type 9 (SAR
aircraft, PGN 129798) and type 27 (long-range, no standard N2K PGN).  See
docs/SOFTWARE.md.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
import math

from nmea2000.message import NMEA2000Field, NMEA2000Message

from .ais import AisTarget

KNOT_TO_MPS = 0.514444
DEG_TO_RAD = math.pi / 180.0

#: canboat's "Default: undefined" code for the GNSS / position-fixing lookup.
#: Used when the AIS value has no exact canboat equivalent; it is a defined
#: code, so consumers do not see a decode failure.
UNDEFINED = 0


# ---- field helpers ----------------------------------------------------


def _code(field_id: str, code: int) -> NMEA2000Field:
    """Write exact bits: lookups, bitfields, MMSIs, dates, reserved fields."""
    return NMEA2000Field(id=field_id, raw_value=code)


def _number(field_id: str, value: float | int | None) -> NMEA2000Field:
    """Write a scaled numeric field; ``None`` becomes "not available"."""
    return NMEA2000Field(id=field_id, value=value)


def _string(field_id: str, value: str | None) -> NMEA2000Field:
    return NMEA2000Field(id=field_id, value=value or "")


def _binary(field_id: str, value: bytes) -> NMEA2000Field:
    return NMEA2000Field(id=field_id, value=value)


# ---- unit and sentinel conversion -------------------------------------


def _radians(degrees: float | None) -> float | None:
    return None if degrees is None else degrees * DEG_TO_RAD


def _metres_per_second(knots: float | None) -> float | None:
    return None if knots is None else knots * KNOT_TO_MPS


def _rate_of_turn(deg_per_min: float | None) -> float | None:
    if deg_per_min is None:
        return None
    rad_per_s = deg_per_min * DEG_TO_RAD / 60.0
    # N2K rateOfTurn is -1.024..1.023875 rad/s.  AIS cannot exceed that, but
    # a malformed sentence must not reach the encoder's range check.
    if not -1.0 <= rad_per_s <= 1.0:
        return None
    return rad_per_s


def _total(forward: int | None, aft: int | None) -> float | None:
    """Length or beam from an AIS reference-point pair.

    AIS encodes 0 as "not available" for each distance, so a partial pair
    cannot produce a total: reporting ``aft`` alone would invent a length.
    """
    if not forward or not aft:
        return None
    return float(forward + aft)


def _offset(value: int | None) -> float | None:
    """One AIS distance from the reference point; 0 means not available."""
    if not value:
        return None
    return float(value)


def _draft(value: float | None) -> float | None:
    """AIS draught; 0.00 m is the "not available" sentinel, not a draught."""
    if value is None or value <= 0:
        return None
    return value


def _position_fixing(epfd: int | None) -> int:
    """Map AIS EPFD to canboat's ``gnssType``/``positionFixingDeviceType``.

    AIS 1..8 are the same eight receiver types canboat defines.  AIS 0 is
    "undefined" and maps to canboat 0, "Default: undefined".  AIS 9..14 are
    reserved and AIS 15 is "internal GNSS", which canboat does not define;
    both become 0 rather than inventing a receiver type.
    """
    if epfd is not None and 1 <= epfd <= 8:
        return epfd
    return UNDEFINED


def _unit_type(target: AisTarget) -> int:
    """N2K ``unitType``: 0 = SOTDMA, 1 = CS.

    Type 18 carries the transmitter's own carrier-sense flag.  If it was not
    decoded, CS is the honest default: type 18 is the Class B CS report.
    """
    if target.cs is None:
        return 1
    return 1 if target.cs else 0


def _transceiver(target: AisTarget) -> NMEA2000Field:
    # 0 = Channel A VDL reception, 1 = Channel B VDL reception, 15 = undefined.
    channel = (target.channel or "").upper()
    return _code("aisTransceiverInformation", {"A": 0, "B": 1}.get(channel, 15))


def _flag(value: bool | int | None) -> int:
    return 1 if value else 0


def _bounded_code(value: int | None, maximum: int, default: int) -> int:
    """Clamp an optional AIS code to a lookup's defined range."""
    if value is None or not 0 <= value <= maximum:
        return default
    return value


def _time_stamp(second: int | None) -> NMEA2000Field:
    # 0..59 are the UTC second and 60..63 have the same meaning in both
    # protocols (not available, manual, dead reckoning, inoperative).
    if second is None or not 0 <= second <= 63:
        return _code("timeStamp", 60)
    return _code("timeStamp", second)


def _eta_fields(target: AisTarget) -> tuple[NMEA2000Field, NMEA2000Field]:
    """AIS carries month/day/hour/minute only; N2K wants a date and a time.

    AIS sentinels are month 0, day 0, hour 24 and minute 60.  The N2K fields
    are independent, so a known date with an unknown time keeps the date and
    writes the time as not available.  AIS has no year: the year is inferred
    as the current year, or next year if the date is more than a month in the
    past.  Treat the year as a guess and document it as such.

    ``value=None`` (rather than the raw 0xFFFFFFFF) is required here: the
    encoder range-checks TIME against 0..86401 before packing.
    """
    eta_date = _number("etaDate", None)
    if target.eta_month and target.eta_day:
        now = datetime.now(timezone.utc)
        try:
            eta = date(now.year, target.eta_month, target.eta_day)
            if eta < (now - timedelta(days=31)).date():
                eta = date(now.year + 1, target.eta_month, target.eta_day)
            eta_date = _code("etaDate", (eta - date(1970, 1, 1)).days)
        except ValueError:
            # e.g. 30 February: AIS can carry it, N2K cannot represent it.
            pass

    eta_time = _number("etaTime", None)
    if (
        target.eta_hour is not None
        and target.eta_minute is not None
        and 0 <= target.eta_hour <= 23
        and 0 <= target.eta_minute <= 59
    ):
        eta_time = _code("etaTime", target.eta_hour * 3600 + target.eta_minute * 60)
    return eta_date, eta_time


# ---- shared field groups ----------------------------------------------


def _identity(target: AisTarget, message_id: int) -> list[NMEA2000Field]:
    return [
        _code("messageId", message_id),
        _code("repeatIndicator", target.repeat & 0x03),
        _code("userId", target.mmsi),
    ]


def _position(target: AisTarget) -> list[NMEA2000Field]:
    return [
        _number("longitude", target.lon),
        _number("latitude", target.lat),
        _code("positionAccuracy", _flag(target.accuracy)),
        _code("raim", _flag(target.raim)),
    ]


def _motion(target: AisTarget) -> list[NMEA2000Field]:
    return [
        _time_stamp(target.second),
        _number("cog", _radians(target.cog_deg)),
        _number("sog", _metres_per_second(target.sog_kn)),
    ]


def _dimensions(
    target: AisTarget,
    length_id: str = "length",
    beam_id: str = "beam",
    starboard_id: str = "positionReferenceFromStarboard",
    bow_id: str = "positionReferenceFromBow",
) -> list[NMEA2000Field]:
    return [
        _number(length_id, _total(target.dim_bow, target.dim_stern)),
        _number(beam_id, _total(target.dim_port, target.dim_starboard)),
        _number(starboard_id, _offset(target.dim_starboard)),
        _number(bow_id, _offset(target.dim_bow)),
    ]


class SequenceCounter:
    """N2K sequenceId, one 8-bit counter per PGN."""

    def __init__(self) -> None:
        self._counters: dict[int, int] = {}

    def take(self, pgn: int) -> int:
        value = self._counters.get(pgn, 0)
        self._counters[pgn] = (value + 1) & 0xFF
        return value


# ---- PGN builders ------------------------------------------------------


def _class_a_position(t: AisTarget, seq: int) -> NMEA2000Message:
    return NMEA2000Message(
        PGN=129038,
        id="aisClassAPositionReport",
        priority=4,
        source=0,
        destination=255,
        fields=[
            *_identity(t, t.msg_type),
            *_position(t),
            *_motion(t),
            _binary("communicationState", b"\x00\x00\x00"),
            _transceiver(t),
            _number("heading", _radians(t.heading_deg)),
            _number("rateOfTurn", _rate_of_turn(t.rot_deg_min)),
            _code("navStatus", _bounded_code(t.nav_status, 15, 15)),
            _code("specialManeuverIndicator", _bounded_code(t.maneuver, 2, 0)),
            _code("reserved", 0),
            _code("spare18", 0),
            _code("reserved19", 0),
            _code("sequenceId", seq),
        ],
    )


def _class_b_position(t: AisTarget, seq: int) -> NMEA2000Message:
    return NMEA2000Message(
        PGN=129039,
        id="aisClassBPositionReport",
        priority=4,
        source=0,
        destination=255,
        fields=[
            *_identity(t, 18),
            *_position(t),
            *_motion(t),
            _binary("communicationState", b"\x00\x00\x00"),
            _transceiver(t),
            _number("heading", _radians(t.heading_deg)),
            _code("regionalApplication", 0),
            _code("regionalApplicationB", 0),
            _code("unitType", _unit_type(t)),
            _code("integratedDisplay", _flag(t.display)),
            _code("dsc", _flag(t.dsc)),
            _code("band", _flag(t.band)),
            _code("canHandleMsg22", _flag(t.msg22)),
            _code("aisMode", _flag(t.assigned)),
            # AIS types 18/19 have no N2K equivalent for CSTDMA; canboat only
            # defines SOTDMA/ITDMA here.  Zero is the conventional value and
            # is documented rather than invented per-unit.
            _code("aisCommunicationState", 0),
            _code("reserved", 0),
            _code("sequenceId", seq),
        ],
    )


def _class_b_extended(t: AisTarget, seq: int) -> NMEA2000Message | None:
    if t.lat is None or t.lon is None:
        # PGN 129040 has no useful meaning without a position; AIS type 19
        # always carries one, so this is defensive only.
        return None
    return NMEA2000Message(
        PGN=129040,
        id="aisClassBExtendedPositionReport",
        priority=4,
        source=0,
        destination=255,
        fields=[
            *_identity(t, 19),
            *_position(t),
            *_motion(t),
            _code("regionalApplication", 0),
            _code("regionalApplicationB", 0),
            _code("reserved", 0),
            _code("typeOfShip", t.ship_type or 0),
            _number("trueHeading", _radians(t.heading_deg)),
            _code("reserved16", 0),
            _code("gnssType", _position_fixing(t.epfd)),
            *_dimensions(t),
            _string("name", t.name),
            _code("dte", _flag(t.dte)),
            _code("aisMode", _flag(t.assigned)),
            _code("spare25", 0),
            _transceiver(t),
            _code("reserved27", 0),
        ],
    )


def _aton(t: AisTarget, seq: int) -> NMEA2000Message | None:
    if t.lat is None or t.lon is None:
        return None
    return NMEA2000Message(
        PGN=129041,
        id="aisAidsToNavigationAtonReport",
        priority=4,
        source=0,
        destination=255,
        fields=[
            *_identity(t, 21),
            *_position(t),
            _time_stamp(t.second),
            *_dimensions(
                t,
                length_id="lengthDiameter",
                beam_id="beamDiameter",
                starboard_id="positionReferenceFromStarboardEdge",
                bow_id="positionReferenceFromTrueNorthFacingEdge",
            ),
            # AIS aid_type 0..31 matches canboat's atonType 1:1 (verified).
            _code("atonType", _bounded_code(t.aid_type, 31, 0)),
            _code("offPositionIndicator", _flag(t.off_position)),
            _code("virtualAtonFlag", _flag(t.virtual_aid)),
            _code("assignedModeFlag", _flag(t.assigned)),
            _code("spare", 0),
            _code("positionFixingDeviceType", _position_fixing(t.epfd)),
            _code("reserved19", 0),
            # AIS type 21 carries no N2K atonStatus bits; zero is the only
            # non-inventing value for this BINARY field.
            _binary("atonStatus", b"\x00"),
            _transceiver(t),
            _code("reserved22", 0),
            _string("atonName", t.name),
        ],
    )


def _class_a_static(t: AisTarget, seq: int) -> NMEA2000Message:
    eta_date, eta_time = _eta_fields(t)
    return NMEA2000Message(
        PGN=129794,
        id="aisClassAStaticAndVoyageRelatedData",
        priority=6,
        source=0,
        destination=255,
        fields=[
            *_identity(t, 5),
            _number("imoNumber", t.imo or None),
            _string("callsign", t.callsign),
            _string("name", t.name),
            _code("typeOfShip", t.ship_type or 0),
            *_dimensions(t),
            eta_date,
            eta_time,
            _number("draft", _draft(t.draft_m)),
            _string("destination", t.destination),
            _code("aisVersionIndicator", _bounded_code(t.ais_version, 3, 0)),
            _code("gnssType", _position_fixing(t.epfd)),
            _code("dte", _flag(t.dte)),
            _code("reserved", 0),
            _transceiver(t),
            _code("reserved21", 0),
            _code("sequenceId", seq),
        ],
    )


def _class_b_static_a(t: AisTarget, seq: int) -> NMEA2000Message:
    return NMEA2000Message(
        PGN=129809,
        id="aisClassBStaticDataMsg24PartA",
        priority=6,
        source=0,
        destination=255,
        fields=[
            *_identity(t, 24),
            _string("name", t.name),
            _transceiver(t),
            _code("reserved", 0),
            _code("sequenceId", seq),
        ],
    )


def _class_b_static_b(t: AisTarget, seq: int) -> NMEA2000Message:
    return NMEA2000Message(
        PGN=129810,
        id="aisClassBStaticDataMsg24PartB",
        priority=6,
        source=0,
        destination=255,
        fields=[
            *_identity(t, 24),
            _code("typeOfShip", t.ship_type or 0),
            _string("vendorId", t.vendor_id),
            _string("callsign", t.callsign),
            *_dimensions(t),
            # AIS 24B for an auxiliary craft carries the mothership MMSI in
            # place of the dimensions; 0 means there is none.
            _code("mothershipUserId", t.mothership_mmsi or 0),
            _code("reserved", 0),
            _code("spare13", 0),
            # AIS 24B carries no EPFD; canboat's "Default: undefined" is the
            # honest value (there is no "not available" lookup code).
            _code("gnssType", UNDEFINED),
            _transceiver(t),
            _code("reserved16", 0),
            _code("sequenceId", seq),
        ],
    )


def to_nmea2000(
    target: AisTarget, sequence: SequenceCounter | None = None
) -> NMEA2000Message | None:
    """Return the NMEA 2000 message for an AIS target, or ``None`` if unsupported."""
    counter = sequence or SequenceCounter()

    if target.msg_type in (1, 2, 3):
        return _class_a_position(target, counter.take(129038))
    if target.msg_type == 18:
        return _class_b_position(target, counter.take(129039))
    if target.msg_type == 19:
        return _class_b_extended(target, counter.take(129040))
    if target.msg_type == 21:
        return _aton(target, counter.take(129041))
    if target.msg_type == 5:
        return _class_a_static(target, counter.take(129794))
    if target.msg_type == 24:
        if target.partno == 0:
            return _class_b_static_a(target, counter.take(129809))
        if target.partno == 1:
            return _class_b_static_b(target, counter.take(129810))
    return None
