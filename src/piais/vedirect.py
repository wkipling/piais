"""Minimal Victron VE.Direct text-protocol reader.

VE.Direct is a 19,200 baud text stream of ``KEY<TAB>VALUE`` lines followed by
a ``Checksum`` line.  The checksum byte is chosen so that the sum of every
byte in the frame (including the leading CR/LF, all tabs, and the checksum
byte itself) is zero modulo 256.  The checksum byte is raw and may be any
value, including LF, so this parser is byte-oriented rather than line-based.

Only the fields needed for NMEA 2000 PGN 127508 (battery status) are mapped
so far.  Solar-specific fields (PVA, PPV, yield today, error state) have no
clean standard PGN and are left for later; see docs/SOFTWARE.md.
"""

from __future__ import annotations

from dataclasses import dataclass, field
import math

from nmea2000.message import NMEA2000Field, NMEA2000Message

_FRAME_START, _IN_KEY, _IN_VALUE, _IN_CHECKSUM, _LINE_END = range(5)


@dataclass(slots=True)
class VeDirectFrame:
    values: dict[str, str] = field(default_factory=dict)

    def get_float(self, key: str) -> float | None:
        raw = self.values.get(key)
        if raw is None:
            return None
        try:
            value = float(raw)
        except ValueError:
            return None
        return value if math.isfinite(value) else None

    @property
    def battery_voltage_v(self) -> float | None:
        mv = self.get_float("V")
        return None if mv is None else mv / 1000.0

    @property
    def battery_current_a(self) -> float | None:
        ma = self.get_float("I")
        # VE.Direct: positive while charging.  NMEA 2000 PGN 127508 uses the
        # same sign convention.
        return None if ma is None else ma / 1000.0

    @property
    def battery_temperature_c(self) -> float | None:
        centi = self.get_float("T")
        return None if centi is None else centi / 100.0

    @property
    def pid(self) -> str | None:
        return self.values.get("PID")


class VeDirectParser:
    """Incremental VE.Direct frame parser."""

    def __init__(self) -> None:
        self._state = _FRAME_START
        self._checksum = 0
        self._prev = 0
        self._key = bytearray()
        self._value = bytearray()
        self._values: dict[str, str] = {}

    def feed(self, data: bytes) -> list[VeDirectFrame]:
        frames: list[VeDirectFrame] = []
        for byte in data:
            frame = self._input(byte)
            if frame is not None:
                frames.append(frame)
        return frames

    def _reset(self) -> None:
        self._state = _FRAME_START
        self._checksum = 0
        self._key.clear()
        self._value.clear()
        self._values = {}

    def _input(self, byte: int) -> VeDirectFrame | None:
        if self._state == _FRAME_START:
            # A frame starts at LF.  Include an immediately preceding CR in
            # the checksum, as the protocol definition does.  Everything
            # before that LF is discarded, which is what makes the parser
            # resynchronise after a half-frame or a reboot.
            if byte == ord("\n"):
                self._checksum = ((ord("\r") if self._prev == ord("\r") else 0) + byte) & 0xFF
                self._key.clear()
                self._value.clear()
                self._values = {}
                self._state = _IN_KEY
            self._prev = byte
            return None

        if self._state == _IN_KEY:
            self._checksum = (self._checksum + byte) & 0xFF
            if byte == ord("\t"):
                self._state = _IN_CHECKSUM if self._key == b"Checksum" else _IN_VALUE
            else:
                self._key.append(byte)
            self._prev = byte
            return None

        if self._state == _IN_VALUE:
            self._checksum = (self._checksum + byte) & 0xFF
            if byte == ord("\r"):
                self._values[self._key.decode("ascii", "replace")] = self._value.decode(
                    "ascii", "replace"
                )
                self._key.clear()
                self._value.clear()
                self._state = _LINE_END
            else:
                self._value.append(byte)
            self._prev = byte
            return None

        if self._state == _LINE_END:
            # The LF after a value line is part of the same frame.
            self._checksum = (self._checksum + byte) & 0xFF
            self._state = _IN_KEY
            self._prev = byte
            return None

        if self._state == _IN_CHECKSUM:
            self._checksum = (self._checksum + byte) & 0xFF
            ok = self._checksum == 0
            values = self._values
            self._reset()
            self._prev = byte
            if ok:
                return VeDirectFrame(values=values)
            return None

        return None


def encode_frame(values: dict[str, str]) -> bytes:
    """Build a valid VE.Direct frame.  Used by tests and the bench simulator."""
    body = b"\r\n" + b"".join(
        f"{key}\t{value}\r\n".encode("ascii") for key, value in values.items()
    )
    body += b"Checksum\t"
    checksum = (-sum(body)) & 0xFF
    return body + bytes([checksum]) + b"\r\n"


def to_nmea2000(
    frame: VeDirectFrame,
    instance: int = 0,
    sid: int = 0,
) -> NMEA2000Message | None:
    """Map a VE.Direct frame to PGN 127508 (battery status).

    Returns ``None`` when the frame has no battery voltage, which is the one
    field the PGN is useless without.
    """
    voltage = frame.battery_voltage_v
    if voltage is None:
        return None
    current = frame.battery_current_a
    temperature = frame.battery_temperature_c
    # ``current`` is a signed 16-bit field and ``temperature`` is unsigned.
    # A raw 0xFFFF sentinel is outside the signed current range, so absent
    # values must be passed as ``value=None``; the encoder then writes the
    # correct all-ones sentinel without the range check rejecting it.
    fields = [
        NMEA2000Field(id="instance", raw_value=instance & 0xFF),
        NMEA2000Field(id="voltage", value=voltage),
        NMEA2000Field(id="current", value=current),
        NMEA2000Field(
            id="temperature",
            value=None if temperature is None else temperature + 273.15,
        ),
        NMEA2000Field(id="sid", raw_value=sid & 0xFF),
    ]
    return NMEA2000Message(
        PGN=127508,
        id="batteryStatus",
        priority=6,
        source=0,
        destination=255,
        fields=fields,
    )
