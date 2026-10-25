"""AIS -> PGN -> CAN frame -> PGN decode, the whole path without hardware."""

from __future__ import annotations

import pytest

from piais.ais import SentenceAssembler, decode
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
