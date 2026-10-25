"""Map normalised AIS targets onto NMEA 2000 PGNs.

The PGN field order here is **positional**: ``nmea2000``'s encoder writes the
fields in the order they are supplied, so every field defined by canboat must
be present, in canboat order, including reserved and spare fields.  The test
suite compares these lists against the installed canboat database so a canboat
update cannot silently shift a value into the wrong bits.

Values are given in SI units (radians, m/s, metres).  The encoder is always
created with ``units="si"``; the default ("native") interprets some canboat
fields differently and silently scales lengths by ten.

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

# NMEA 2000 "not available" sentinels, by field width.
NA_U16 = 0xFFFF
NA_U32 = 0xFFFFFFFF
NA_LATLON = 0x7FFFFFFF
NA_I16 = 0x7FFF
NA_U8 = 0xFF

KNOT_TO_MPS = 0.514444
DEG_TO_RAD = math.pi / 180.0


def _f(field_id: str, value: object) -> NMEA2000Field:
    return NMEA2000Field(id=field_id, value=value)  # type: ignore[arg-type]


def _r(field_id: str, raw: int) -> NMEA2000Field:
    return NMEA2000Field(id=field_id, raw_value=raw)


def _angle(field_id: str, degrees: float | None) -> NMEA2000Field:
    if degrees is None:
        return _r(field_id, NA_U16)
    return _f(field_id, degrees * DEG_TO_RAD)


def _position(field_id: str, degrees: float | None) -> NMEA2000Field:
    if degrees is None:
        return _r(field_id, NA_LATLON)
    return _f(field_id, degrees)


def _sog(knots: float | None) -> NMEA2000Field:
    if knots is None:
        return _r("sog", NA_U16)
    return _f("sog", knots * KNOT_TO_MPS)


def _rot(deg_per_min: float | None) -> NMEA2000Field:
    if deg_per_min is None:
        return _r("rateOfTurn", NA_I16)
    rad_per_s = deg_per_min * DEG_TO_RAD / 60.0
    if not -1.0 < rad_per_s < 1.0:
        return _r("rateOfTurn", NA_I16)
    return _f("rateOfTurn", rad_per_s)


def _channel_field(target: AisTarget) -> NMEA2000Field:
    # 0 = Channel A VDL reception, 1 = Channel B VDL reception, 15 = unknown.
    channel = (target.channel or "").upper()
    raw = {"A": 0, "B": 1}.get(channel, 15)
    return _r("aisTransceiverInformation", raw)


def _text(value: str | None) -> str:
    return value or ""


def _flag(value: bool | None) -> int:
    if value is None:
        return 0
    return 1 if value else 0


def _time_stamp(second: int | None) -> NMEA2000Field:
    if second is None or not 0 <= second <= 63:
        return _r("timeStamp", 60)  # not available
    return _r("timeStamp", second)


def _eta_fields(target: AisTarget) -> tuple[NMEA2000Field, NMEA2000Field]:
    """AIS carries month/day/hour/minute only; N2K wants a date and a time.

    The year is inferred: the current year, or next year if that date is more
    than a month in the past.  AIS does not carry a year, so this is a
    deliberately conservative guess and is documented in docs/SOFTWARE.md.
    """
    if not target.eta_month or not target.eta_day:
        return _r("etaDate", NA_U16), _r("etaTime", NA_U32)
    now = datetime.now(timezone.utc)
    try:
        eta = date(now.year, target.eta_month, target.eta_day)
        if eta < (now - timedelta(days=31)).date():
            eta = date(now.year + 1, target.eta_month, target.eta_day)
    except ValueError:
        return _r("etaDate", NA_U16), _r("etaTime", NA_U32)
    epoch = date(1970, 1, 1)
    days = (eta - epoch).days
    seconds = (target.eta_hour or 0) * 3600 + (target.eta_minute or 0) * 60
    return _r("etaDate", days), _r("etaTime", seconds)


class SequenceCounter:
    """N2K sequenceId, one 8-bit counter per PGN."""

    def __init__(self) -> None:
        self._counters: dict[int, int] = {}

    def take(self, pgn: int) -> int:
        value = self._counters.get(pgn, 0)
        self._counters[pgn] = (value + 1) & 0xFF
        return value


def _class_a_position(t: AisTarget, seq: int) -> NMEA2000Message:
    return NMEA2000Message(
        PGN=129038,
        id="aisClassAPositionReport",
        priority=4,
        source=0,
        destination=255,
        fields=[
            _r("messageId", t.msg_type),
            _r("repeatIndicator", t.repeat & 0x03),
            _r("userId", t.mmsi),
            _position("longitude", t.lon),
            _position("latitude", t.lat),
            _r("positionAccuracy", _flag(t.accuracy)),
            _r("raim", _flag(t.raim)),
            _time_stamp(t.second),
            _angle("cog", t.cog_deg),
            _sog(t.sog_kn),
            _f("communicationState", b"\x00\x00\x00"),
            _channel_field(t),
            _angle("heading", t.heading_deg),
            _rot(t.rot_deg_min),
            _r("navStatus", t.nav_status if t.nav_status is not None and 0 <= t.nav_status <= 15 else 15),
            _r("specialManeuverIndicator", t.maneuver if t.maneuver is not None and 0 <= t.maneuver <= 2 else 0),
            _r("reserved", 0),
            _r("spare18", 0),
            _r("reserved19", 0),
            _r("sequenceId", seq),
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
            _r("messageId", 18),
            _r("repeatIndicator", t.repeat & 0x03),
            _r("userId", t.mmsi),
            _position("longitude", t.lon),
            _position("latitude", t.lat),
            _r("positionAccuracy", _flag(t.accuracy)),
            _r("raim", _flag(t.raim)),
            _time_stamp(t.second),
            _angle("cog", t.cog_deg),
            _sog(t.sog_kn),
            _f("communicationState", b"\x00\x00\x00"),
            _channel_field(t),
            _angle("heading", t.heading_deg),
            _r("regionalApplication", 0),
            _r("regionalApplicationB", 0),
            _r("unitType", 0),
            _r("integratedDisplay", _flag(t.display)),
            _r("dsc", _flag(t.dsc)),
            _r("band", _flag(t.band)),
            _r("canHandleMsg22", _flag(t.msg22)),
            _r("aisMode", _flag(t.assigned)),
            _r("aisCommunicationState", 0),
            _r("reserved", 0),
            _r("sequenceId", seq),
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
            _r("messageId", 19),
            _r("repeatIndicator", t.repeat & 0x03),
            _r("userId", t.mmsi),
            _position("longitude", t.lon),
            _position("latitude", t.lat),
            _r("positionAccuracy", _flag(t.accuracy)),
            _r("raim", _flag(t.raim)),
            _time_stamp(t.second),
            _angle("cog", t.cog_deg),
            _sog(t.sog_kn),
            _r("regionalApplication", 0),
            _r("regionalApplicationB", 0),
            _r("reserved", 0),
            _r("typeOfShip", t.ship_type or 0),
            _angle("trueHeading", t.heading_deg),
            _r("reserved16", 0),
            _r("gnssType", t.epfd or 0),
            _f("length", float((t.dim_bow or 0) + (t.dim_stern or 0))),
            _f("beam", float((t.dim_port or 0) + (t.dim_starboard or 0))),
            _f("positionReferenceFromStarboard", float(t.dim_starboard or 0)),
            _f("positionReferenceFromBow", float(t.dim_bow or 0)),
            _f("name", _text(t.name)),
            _r("dte", _flag(t.dte)),
            _r("aisMode", _flag(t.assigned)),
            _r("spare25", 0),
            _channel_field(t),
            _r("reserved27", 0),
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
            _r("messageId", 21),
            _r("repeatIndicator", t.repeat & 0x03),
            _r("userId", t.mmsi),
            _position("longitude", t.lon),
            _position("latitude", t.lat),
            _r("positionAccuracy", _flag(t.accuracy)),
            _r("raim", _flag(t.raim)),
            _time_stamp(t.second),
            _f("lengthDiameter", float((t.dim_bow or 0) + (t.dim_stern or 0))),
            _f("beamDiameter", float((t.dim_port or 0) + (t.dim_starboard or 0))),
            _f("positionReferenceFromStarboardEdge", float(t.dim_starboard or 0)),
            _f("positionReferenceFromTrueNorthFacingEdge", float(t.dim_bow or 0)),
            _r("atonType", t.aid_type or 0),
            _r("offPositionIndicator", _flag(t.off_position)),
            _r("virtualAtonFlag", _flag(t.virtual_aid)),
            _r("assignedModeFlag", _flag(t.assigned)),
            _r("spare", 0),
            _r("positionFixingDeviceType", t.epfd or 0),
            _r("reserved19", 0),
            _f("atonStatus", b"\x00"),
            _channel_field(t),
            _r("reserved22", 0),
            _f("atonName", _text(t.name)),
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
            _r("messageId", 5),
            _r("repeatIndicator", t.repeat & 0x03),
            _r("userId", t.mmsi),
            _r("imoNumber", t.imo or 0),
            _f("callsign", _text(t.callsign)),
            _f("name", _text(t.name)),
            _r("typeOfShip", t.ship_type or 0),
            _f("length", float((t.dim_bow or 0) + (t.dim_stern or 0))),
            _f("beam", float((t.dim_port or 0) + (t.dim_starboard or 0))),
            _f("positionReferenceFromStarboard", float(t.dim_starboard or 0)),
            _f("positionReferenceFromBow", float(t.dim_bow or 0)),
            eta_date,
            eta_time,
            _f("draft", float(t.draft_m or 0.0)),
            _f("destination", _text(t.destination)),
            _r("aisVersionIndicator", t.ais_version or 0),
            _r("gnssType", t.epfd or 0),
            _r("dte", _flag(t.dte)),
            _r("reserved", 0),
            _channel_field(t),
            _r("reserved21", 0),
            _r("sequenceId", seq),
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
            _r("messageId", 24),
            _r("repeatIndicator", t.repeat & 0x03),
            _r("userId", t.mmsi),
            _f("name", _text(t.name)),
            _channel_field(t),
            _r("reserved", 0),
            _r("sequenceId", seq),
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
            _r("messageId", 24),
            _r("repeatIndicator", t.repeat & 0x03),
            _r("userId", t.mmsi),
            _r("typeOfShip", t.ship_type or 0),
            _f("vendorId", _text(t.vendor_id)),
            _f("callsign", _text(t.callsign)),
            _f("length", float((t.dim_bow or 0) + (t.dim_stern or 0))),
            _f("beam", float((t.dim_port or 0) + (t.dim_starboard or 0))),
            _f("positionReferenceFromStarboard", float(t.dim_starboard or 0)),
            _f("positionReferenceFromBow", float(t.dim_bow or 0)),
            _r("mothershipUserId", 0),
            _r("reserved", 0),
            _r("spare13", 0),
            _r("gnssType", 15),
            _channel_field(t),
            _r("reserved16", 0),
            _r("sequenceId", seq),
        ],
    )


def to_nmea2000(target: AisTarget, sequence: SequenceCounter | None = None) -> NMEA2000Message | None:
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
