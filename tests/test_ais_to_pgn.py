"""AIS -> PGN -> CAN frame -> PGN decode, the whole path without hardware."""

from __future__ import annotations

import datetime as dt

import pytest

from piais.ais import AisTarget, SentenceAssembler, decode
from piais.pgn import to_nmea2000

from tests.conftest import AIVDM, approx_deg, approx_mps, field, round_trip


def _target(lines: list[str]):
    target = decode(lines)
    assert target is not None
    return target


def test_class_a_position_round_trip() -> None:
    target = _target([AIVDM["type1"]])
    message = to_nmea2000(target)
    assert message is not None
    pgn, decoded = round_trip(message)
    assert pgn == 129038
    assert field(decoded, "userId").value == 244009864
    assert field(decoded, "latitude").value == pytest.approx(51.338295, abs=1e-6)
    assert field(decoded, "longitude").value == pytest.approx(6.701442, abs=1e-6)
    assert field(decoded, "cog").value == approx_deg(295.1)
    assert field(decoded, "sog").value == approx_mps(10.4)
    assert field(decoded, "heading").value == approx_deg(295)
    assert field(decoded, "navStatus").value == "Under way using engine"
    assert field(decoded, "messageId").raw_value == 1
    assert field(decoded, "aisTransceiverInformation").value == "Channel B VDL reception"


def test_class_b_position_round_trip() -> None:
    target = _target([AIVDM["type18"]])
    message = to_nmea2000(target)
    assert message is not None
    pgn, decoded = round_trip(message)
    assert pgn == 129039
    assert field(decoded, "userId").value == 244123456
    assert field(decoded, "cog").value == approx_deg(90.0)
    assert field(decoded, "heading").value == approx_deg(88)
    assert field(decoded, "messageId").raw_value == 18


def test_class_b_extended_round_trip() -> None:
    target = _target([AIVDM["type19"]])
    message = to_nmea2000(target)
    assert message is not None
    pgn, decoded = round_trip(message)
    assert pgn == 129040
    assert field(decoded, "name").value == "SMALL BOAT"
    assert field(decoded, "length").value == pytest.approx(10.0)
    assert field(decoded, "beam").value == pytest.approx(4.0)
    assert field(decoded, "typeOfShip").value == "Pleasure"


def test_aton_round_trip() -> None:
    target = _target([AIVDM["type21"]])
    message = to_nmea2000(target)
    assert message is not None
    pgn, decoded = round_trip(message)
    assert pgn == 129041
    assert field(decoded, "atonName").value.startswith("TEST BUOY")
    assert field(decoded, "userId").value == 992441234


def test_class_a_static_round_trip() -> None:
    assembler = SentenceAssembler()
    assert assembler.feed(AIVDM["type5a"]) is None
    complete = assembler.feed(AIVDM["type5b"])
    assert complete is not None
    target = _target(complete)
    message = to_nmea2000(target)
    assert message is not None
    pgn, decoded = round_trip(message)
    assert pgn == 129794
    assert field(decoded, "name").value == "TEST VESSEL"
    assert field(decoded, "callsign").value == "PD1234"
    assert field(decoded, "destination").value == "ROTTERDAM"
    assert field(decoded, "imoNumber").value == 9876543
    assert field(decoded, "length").value == pytest.approx(200.0)
    assert field(decoded, "beam").value == pytest.approx(30.0)
    assert field(decoded, "draft").value == pytest.approx(6.5)
    eta_date = field(decoded, "etaDate").value
    assert eta_date.month == 5 and eta_date.day == 27
    assert field(decoded, "etaTime").value == dt.time(12, 30)


def test_eta_not_available_is_not_invented() -> None:
    """AIS hour=24 / minute=60 means unknown, not 24:00 or 01:00."""
    assembler = SentenceAssembler()
    assert assembler.feed(AIVDM["type5_eta_na_a"]) is None
    complete = assembler.feed(AIVDM["type5_eta_na_b"])
    target = _target(complete)
    assert target.eta_hour == 24
    assert target.eta_minute == 60

    message = to_nmea2000(target)
    assert message is not None
    _, decoded = round_trip(message)
    # The date is still known and is kept; only the time is unavailable.
    eta_date = field(decoded, "etaDate").value
    assert eta_date is not None
    assert eta_date.month == 5 and eta_date.day == 27
    assert field(decoded, "etaTime").value is None


def test_eta_midnight_is_valid_not_missing() -> None:
    target = AisTarget(
        msg_type=5, mmsi=244009864, eta_month=5, eta_day=27, eta_hour=0, eta_minute=0
    )
    message = to_nmea2000(target)
    assert message is not None
    _, decoded = round_trip(message)
    assert field(decoded, "etaTime").value == dt.time(0, 0)


def test_eta_date_sentinel_does_not_blank_the_time() -> None:
    target = AisTarget(
        msg_type=5, mmsi=244009864, eta_month=0, eta_day=0, eta_hour=12, eta_minute=30
    )
    message = to_nmea2000(target)
    assert message is not None
    _, decoded = round_trip(message)
    assert field(decoded, "etaDate").value is None
    assert field(decoded, "etaTime").value == dt.time(12, 30)


def test_invalid_eta_date_is_not_invented() -> None:
    target = AisTarget(
        msg_type=5, mmsi=244009864, eta_month=2, eta_day=30, eta_hour=12, eta_minute=30
    )
    message = to_nmea2000(target)
    assert message is not None
    _, decoded = round_trip(message)
    assert field(decoded, "etaDate").value is None
    assert field(decoded, "etaTime").value == dt.time(12, 30)


def test_class_b_static_parts_round_trip() -> None:
    part_a = _target([AIVDM["type24A"]])
    part_b = _target([AIVDM["type24B"]])

    msg_a = to_nmea2000(part_a)
    msg_b = to_nmea2000(part_b)
    assert msg_a is not None and msg_a.PGN == 129809
    assert msg_b is not None and msg_b.PGN == 129810

    pgn_a, decoded_a = round_trip(msg_a)
    pgn_b, decoded_b = round_trip(msg_b)
    assert pgn_a == 129809
    assert pgn_b == 129810
    assert field(decoded_a, "name").value == "SMALL BOAT"
    assert field(decoded_b, "callsign").value == "PD9876"
    assert field(decoded_b, "length").value == pytest.approx(10.0)


def test_unsupported_type_is_ignored() -> None:
    target = _target([AIVDM["type1"]])
    target.msg_type = 27  # long-range report has no standard N2K PGN here
    assert to_nmea2000(target) is None


def test_missing_motion_and_position_are_not_available() -> None:
    """A target with only an MMSI must still encode.

    Raw sentinels are rejected by the encoder's range checks, so every absent
    numeric field is a potential crash; this is the regression guard.
    """
    target = AisTarget(msg_type=1, mmsi=244009864)
    message = to_nmea2000(target)
    assert message is not None
    _, decoded = round_trip(message)
    for name in ("longitude", "latitude", "cog", "sog", "heading", "rateOfTurn"):
        assert field(decoded, name).value is None


def test_zero_dimensions_and_draft_are_not_available() -> None:
    """AIS encodes 0 for each dimension and for draught as "not available"."""
    target = AisTarget(
        msg_type=5,
        mmsi=244009864,
        dim_bow=0,
        dim_stern=0,
        dim_port=0,
        dim_starboard=0,
        draft_m=0.0,
        imo=0,
    )
    message = to_nmea2000(target)
    assert message is not None
    _, decoded = round_trip(message)
    for name in (
        "length",
        "beam",
        "positionReferenceFromBow",
        "positionReferenceFromStarboard",
        "draft",
        "imoNumber",
    ):
        assert field(decoded, name).value is None


def test_partial_dimensions_do_not_invent_a_total() -> None:
    target = AisTarget(
        msg_type=5,
        mmsi=244009864,
        dim_bow=10,
        dim_stern=0,
        dim_port=2,
        dim_starboard=2,
    )
    message = to_nmea2000(target)
    assert message is not None
    _, decoded = round_trip(message)
    assert field(decoded, "length").value is None
    assert field(decoded, "beam").value == pytest.approx(4.0)
    assert field(decoded, "positionReferenceFromBow").value == pytest.approx(10.0)


def test_class_b_unit_type_follows_the_cs_flag() -> None:
    """N2K unitType is 0 = SOTDMA, 1 = CS; do not hardcode one."""
    target = AisTarget(msg_type=18, mmsi=244123456, cs=True)
    message = to_nmea2000(target)
    assert message is not None
    _, decoded = round_trip(message)
    assert field(decoded, "unitType").value == "CS"

    target.cs = False
    message = to_nmea2000(target)
    assert message is not None
    _, decoded = round_trip(message)
    assert field(decoded, "unitType").value == "SOTDMA"


@pytest.mark.parametrize(
    ("epfd", "expected"),
    [
        (0, "Default: undefined"),
        (1, "GPS"),
        (3, "Combined GPS/GLONASS"),
        (8, "Galileo"),
        (9, "Default: undefined"),
        (15, "Default: undefined"),
    ],
)
def test_gnss_type_does_not_invent_a_receiver(epfd: int, expected: str) -> None:
    """AIS EPFD 1..8 map exactly; everything else is "undefined".

    AIS 15 means "internal GNSS", which canboat does not define.  Writing it
    raw would decode to ``None``; canboat's defined "Default: undefined" is a
    truthful alternative.
    """
    target = AisTarget(msg_type=19, mmsi=244009864, lat=51.3, lon=6.7, epfd=epfd)
    message = to_nmea2000(target)
    assert message is not None
    _, decoded = round_trip(message)
    assert field(decoded, "gnssType").value == expected


def test_class_b_part_b_has_no_gnss_type() -> None:
    target = _target([AIVDM["type24B"]])
    message = to_nmea2000(target)
    assert message is not None
    _, decoded = round_trip(message)
    assert field(decoded, "gnssType").value == "Default: undefined"


def test_auxiliary_craft_mothership_mmsi_is_carried() -> None:
    target = AisTarget(msg_type=24, partno=1, mmsi=982441234, mothership_mmsi=244123456)
    message = to_nmea2000(target)
    assert message is not None
    _, decoded = round_trip(message)
    assert field(decoded, "mothershipUserId").value == 244123456


@pytest.mark.parametrize(
    ("aid_type", "expected"),
    [
        (0, "Default: Type of AtoN not specified"),
        (5, "Fixed light: without sectors"),
        (25, "Floating AtoN: starboard hand mark"),
    ],
)
def test_aton_type_matches_canboat(aid_type: int, expected: str) -> None:
    """AIS aid_type 0..31 is a 1:1 match for canboat's atonType."""
    target = AisTarget(
        msg_type=21, mmsi=992441234, lat=51.3, lon=6.7, aid_type=aid_type, name="BUOY"
    )
    message = to_nmea2000(target)
    assert message is not None
    _, decoded = round_trip(message)
    assert field(decoded, "atonType").value == expected
