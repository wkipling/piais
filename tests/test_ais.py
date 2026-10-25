from __future__ import annotations

from types import SimpleNamespace

import pytest

from piais.ais import SentenceAssembler, decode, normalise
from tests.conftest import AIVDM


def test_decode_class_a_position() -> None:
    target = decode([AIVDM["type1"]])
    assert target is not None
    assert target.msg_type == 1
    assert target.mmsi == 244009864
    assert target.channel == "B"
    assert target.lat == pytest.approx(51.338295)
    assert target.lon == pytest.approx(6.701442)
    assert target.sog_kn == pytest.approx(10.4)
    assert target.cog_deg == pytest.approx(295.1)
    assert target.heading_deg == 295
    assert target.nav_status == 0
    assert target.second == 16


def test_multipart_type5_is_reassembled() -> None:
    assembler = SentenceAssembler()
    assert assembler.feed(AIVDM["type5a"]) is None
    complete = assembler.feed(AIVDM["type5b"])
    assert complete is not None
    target = decode(complete)
    assert target is not None
    assert target.msg_type == 5
    assert target.name == "TEST VESSEL"
    assert target.callsign == "PD1234"
    assert target.destination == "ROTTERDAM"
    assert target.imo == 9876543


def test_incomplete_multipart_never_decodes() -> None:
    assembler = SentenceAssembler()
    assert assembler.feed(AIVDM["type5a"]) is None
    assert assembler.feed(b"!AIVDM,1,1,,B,13`e;R001`PNcD2MH48KQq>P0@;5,0*63") == [
        AIVDM["type1"].encode()
    ]


def test_bad_checksum_is_dropped() -> None:
    bad = AIVDM["type1"][:-2] + "00"
    assert decode([bad]) is None


def test_garbage_is_ignored() -> None:
    assert decode([b"$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47"]) is None


def test_type18_and_type24_are_distinguished() -> None:
    t18 = decode([AIVDM["type18"]])
    t24a = decode([AIVDM["type24A"]])
    t24b = decode([AIVDM["type24B"]])
    assert t18 is not None and t18.msg_type == 18
    assert t24a is not None and t24a.msg_type == 24 and t24a.partno == 0
    assert t24b is not None and t24b.msg_type == 24 and t24b.partno == 1


def test_out_of_range_heading_is_dropped() -> None:
    """Only 0..359 is a heading; 511 is the sentinel and 360..510 are invalid."""
    for heading in (511, 400, -1):
        target = normalise(SimpleNamespace(msg_type=1, mmsi=244009864, heading=heading))
        assert target is not None
        assert target.heading_deg is None


def test_tag_block_is_stripped_before_decoding() -> None:
    """AIS-catcher can prefix an NMEA 4.10 tag block; the sentence is still valid."""
    line = "\\s:TEST,c:1700000000*00\\" + AIVDM["type1"]
    target = decode([line])
    assert target is not None
    assert target.mmsi == 244009864
    assert target.channel == "B"
