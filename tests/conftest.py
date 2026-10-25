"""Shared fixtures and the encode/decode round-trip helper."""

from __future__ import annotations

import math

import pytest
from nmea2000 import backend
from nmea2000._canboat import Reassembler, can_id_decompose
from nmea2000.encoder import create_encoder
from nmea2000.input_formats import N2KFormat
from nmea2000.message import NMEA2000Message

# Generated with pyais.encode_dict(..., sentence_type="VDM") so the checksums
# are real.  Kept as literals so a future pyais encoder change cannot quietly
# change what is being tested.
AIVDM = {
    "type1": "!AIVDM,1,1,,B,13`e;R001`PNcD2MH48KQq>P0@;5,0*63",
    "type18": "!AIVDM,1,1,,B,B3`l7@00D`59Uh7LwF0p@d?0P000,0*7B",
    "type19": "!AIVDM,1,1,,B,C3`l7@@0D`59Uh7LwF0p@d?0VJ2HI04N2`0000000000BPP11120,0*2C",
    "type21": "!AIVDM,1,1,,B,E>jMkTPb2ab@1:WdP0000000000@:Qm0>skp05088;V00000000000000000,4*21",
    "type24A": "!AIVDM,1,1,,B,H3`l7@1<l4hj08t5@00000000000,0*4B",
    "type24B": "!AIVDM,1,1,,B,H3`l7@4U1230000@4qpon0102220,0*33",
    "type5a": "!AIVDM,2,1,0,B,53`e;R02Fe3u0C7;?@1@E=B1HE=<Dh0000000016Bhj??5MdN@DSm51DQ0C@,0*4C",
    "type5b": "!AIVDM,2,2,0,B,00000000000,2*27",
    # Same type 5, but with the ETA sentinels hour=24 and minute=60.
    "type5_eta_na_a": "!AIVDM,2,1,0,B,53`e;R02Fe3u0C7;?@1@E=B1HE=<Dh0000000016Bhj??5Mpt@DSm51DQ0C@,0*62",
    "type5_eta_na_b": "!AIVDM,2,2,0,B,00000000000,2*27",
}


def round_trip(message: NMEA2000Message) -> tuple[int, NMEA2000Message]:
    """Encode with canboat, frame it for CAN, reassemble and decode.

    Both sides use SI units: the encoder is the one the daemon uses, and the
    decoder must interpret the same quantity system or canboat's native-unit
    table would rescale fields that differ between unit systems.
    """
    frames = create_encoder(N2KFormat.PYTHON_CAN, units="si").encode(message)
    reassembler = Reassembler()
    payload: bytes | None = None
    pgn = 0
    for frame in frames:
        priority, pgn, src, dst = can_id_decompose(frame.arbitration_id)
        payload = reassembler.push(pgn, bytes(frame.data), src, dst, priority)
    assert payload is not None, "no complete PGN was produced"
    return pgn, backend.decode(pgn, payload, units="si")


def field(message: NMEA2000Message, field_id: str):
    for candidate in message.fields:
        if candidate.id == field_id:
            return candidate
    raise KeyError(f"PGN {message.PGN} has no field {field_id!r}")


def approx_deg(degrees: float) -> float:
    return pytest.approx(math.radians(degrees), abs=1e-3)


def approx_mps(knots: float) -> float:
    return pytest.approx(knots * 0.514444, abs=1e-2)
