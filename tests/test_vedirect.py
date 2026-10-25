from __future__ import annotations

import pytest

from piais.vedirect import VeDirectParser, encode_frame, to_nmea2000

from tests.conftest import field, round_trip

VALUES = {
    "PID": "0xA042",
    "V": "13210",
    "I": "1500",
    "T": "2350",
    "CS": "0",
    "MPPT": "2",
    "OR": "0x00000000",
    "ERR": "0",
    "LOAD": "ON",
}


def test_frame_round_trip() -> None:
    parser = VeDirectParser()
    frames = parser.feed(encode_frame(VALUES))
    assert len(frames) == 1
    frame = frames[0]
    assert frame.values["PID"] == "0xA042"
    assert frame.battery_voltage_v == pytest.approx(13.21)
    assert frame.battery_current_a == pytest.approx(1.5)
    assert frame.battery_temperature_c == pytest.approx(23.5)


def test_parser_survives_single_byte_feeds() -> None:
    data = encode_frame(VALUES)
    parser = VeDirectParser()
    frames = []
    for byte in data:
        frames.extend(parser.feed(bytes([byte])))
    assert len(frames) == 1


def test_bad_checksum_is_rejected() -> None:
    data = bytearray(encode_frame(VALUES))
    data[-3] ^= 0xFF  # corrupt the checksum byte
    assert VeDirectParser().feed(bytes(data)) == []


def test_battery_status_pgn_round_trip() -> None:
    frame = VeDirectParser().feed(encode_frame(VALUES))[0]
    message = to_nmea2000(frame, instance=1)
    assert message is not None
    pgn, decoded = round_trip(message)
    assert pgn == 127508
    assert field(decoded, "instance").value == 1
    assert field(decoded, "voltage").value == pytest.approx(13.21, abs=0.01)
    assert field(decoded, "current").value == pytest.approx(1.5, abs=0.11)
    assert field(decoded, "temperature").value == pytest.approx(296.65, abs=0.02)


def test_frame_without_voltage_is_skipped() -> None:
    values = {k: v for k, v in VALUES.items() if k != "V"}
    frame = VeDirectParser().feed(encode_frame(values))[0]
    assert to_nmea2000(frame) is None


def test_garbage_then_frame_resynchronises() -> None:
    parser = VeDirectParser()
    assert parser.feed(b"garbage from a half-open serial port") == []
    frames = parser.feed(encode_frame(VALUES))
    assert len(frames) == 1
    assert frames[0].values["PID"] == "0xA042"
