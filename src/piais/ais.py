"""Decode AIS NMEA 0183 into one small, normalised target view.

The heavy lifting (bit decoding, multipart payloads, 6-bit ASCII, ROT curve)
is done by ``pyais``.  This module only:

* reassembles multipart AIVDM sentences from a datagram stream,
* normalises the handful of fields the NMEA 2000 PGNs need,
* converts AIS "not available" sentinels to ``None`` so the PGN layer can
  write the NMEA 2000 sentinel instead of a plausible-looking 91 degree
  latitude.

The canonical reference for AIVDM sentence structure and field sentinels is
the GPSD AIVDM document (see docs/REFERENCES.md).
"""

from __future__ import annotations

from dataclasses import dataclass
import time

import pyais

# AIS sentinels (ITU-R M.1371 / GPSD AIVDM).
NA_SPEED_KN = 102.3
NA_COURSE_DEG = 360.0
NA_HEADING_DEG = 511
NA_TURN = -128
NA_LAT_DEG = 91.0
NA_LON_DEG = 181.0


@dataclass(slots=True)
class AisTarget:
    """A type-agnostic view of one AIS message."""

    msg_type: int
    mmsi: int
    repeat: int = 0
    partno: int | None = None
    channel: str | None = None

    lat: float | None = None
    lon: float | None = None
    sog_kn: float | None = None
    cog_deg: float | None = None
    heading_deg: float | None = None
    rot_deg_min: float | None = None

    accuracy: bool | None = None
    raim: bool | None = None
    second: int | None = None
    nav_status: int | None = None
    maneuver: int | None = None

    name: str | None = None
    name_ext: str | None = None
    callsign: str | None = None
    ship_type: int | None = None
    imo: int | None = None
    destination: str | None = None
    draft_m: float | None = None
    vendor_id: str | None = None

    dim_bow: int | None = None
    dim_stern: int | None = None
    dim_port: int | None = None
    dim_starboard: int | None = None

    epfd: int | None = None
    dte: int | None = None
    ais_version: int | None = None

    eta_month: int | None = None
    eta_day: int | None = None
    eta_hour: int | None = None
    eta_minute: int | None = None

    aid_type: int | None = None
    off_position: bool | None = None
    virtual_aid: bool | None = None
    assigned: bool | None = None

    # Class B (type 18) flags carried into PGN 129039.
    cs: bool | None = None
    display: bool | None = None
    dsc: bool | None = None
    band: bool | None = None
    msg22: bool | None = None


def _clean_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).replace("@", " ").strip()
    return text or None


def _num(value: object) -> float | None:
    try:
        if value is None:
            return None
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _int(value: object) -> int | None:
    try:
        if value is None:
            return None
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _flag(value: object) -> bool | None:
    if value is None:
        return None
    return bool(value)


def _lat(value: object) -> float | None:
    v = _num(value)
    if v is None or abs(v) >= NA_LAT_DEG:
        return None
    return v


def _lon(value: object) -> float | None:
    v = _num(value)
    if v is None or abs(v) >= NA_LON_DEG:
        return None
    return v


def _speed(value: object) -> float | None:
    v = _num(value)
    if v is None or v >= NA_SPEED_KN - 0.05:
        return None
    return v


def _course(value: object) -> float | None:
    v = _num(value)
    if v is None or v >= NA_COURSE_DEG - 0.05:
        return None
    return v


def _heading(value: object) -> float | None:
    v = _num(value)
    if v is None or v >= NA_HEADING_DEG:
        return None
    return v


def _turn(value: object) -> float | None:
    v = _num(value)
    if v is None or v <= NA_TURN:
        return None
    return v


def _second(value: object) -> int | None:
    v = _int(value)
    if v is None or not 0 <= v <= 63:
        return None
    return v


def normalise(msg: object, channel: str | None = None) -> AisTarget | None:
    """Convert a pyais message object into :class:`AisTarget`."""
    get = lambda key: getattr(msg, key, None)
    msg_type = _int(get("msg_type"))
    mmsi = _int(get("mmsi"))
    if msg_type is None or not mmsi:
        return None

    target = AisTarget(
        msg_type=msg_type,
        mmsi=mmsi,
        repeat=_int(get("repeat")) or 0,
        partno=_int(get("partno")),
        channel=channel,
        lat=_lat(get("lat")),
        lon=_lon(get("lon")),
        sog_kn=_speed(get("speed")),
        cog_deg=_course(get("course")),
        heading_deg=_heading(get("heading")),
        rot_deg_min=_turn(get("turn")),
        accuracy=_flag(get("accuracy")),
        raim=_flag(get("raim")),
        second=_second(get("second")),
        nav_status=_int(get("status")),
        maneuver=_int(get("maneuver")),
        # Type 21 calls it "name"; types 5 and 24 call it "shipname".
        name=_clean_str(get("shipname")) or _clean_str(get("name")),
        name_ext=_clean_str(get("name_ext")),
        callsign=_clean_str(get("callsign")),
        ship_type=_int(get("ship_type")),
        imo=_int(get("imo")),
        destination=_clean_str(get("destination")),
        draft_m=_num(get("draught")),
        vendor_id=_clean_str(get("vendorid")),
        dim_bow=_int(get("to_bow")),
        dim_stern=_int(get("to_stern")),
        dim_port=_int(get("to_port")),
        dim_starboard=_int(get("to_starboard")),
        epfd=_int(get("epfd")),
        dte=_int(get("dte")),
        ais_version=_int(get("ais_version")),
        eta_month=_int(get("month")),
        eta_day=_int(get("day")),
        eta_hour=_int(get("hour")),
        eta_minute=_int(get("minute")),
        aid_type=_int(get("aid_type")),
        off_position=_flag(get("off_position")),
        virtual_aid=_flag(get("virtual_aid")),
        assigned=_flag(get("assigned")),
        cs=_flag(get("cs")),
        display=_flag(get("display")),
        dsc=_flag(get("dsc")),
        band=_flag(get("band")),
        msg22=_flag(get("msg22")),
    )
    if msg_type == 21:
        full = _clean_str(get("full_name"))
        if full:
            target.name = full
        elif target.name_ext:
            target.name = " ".join(filter(None, (target.name, target.name_ext)))
    return target


def checksum_ok(sentence: str) -> bool:
    """Validate the NMEA 0183 XOR checksum, when the sentence carries one."""
    if "*" not in sentence:
        # Some feeders omit it.  Be lenient rather than silently dropping a
        # usable message; AIS-catcher always supplies it.
        return True
    body, _, given = sentence.rpartition("*")
    if len(given) < 2 or not body or body[0] not in "!$":
        return False
    try:
        expected = int(given[:2], 16)
    except ValueError:
        return False
    actual = 0
    for char in body[1:]:
        actual ^= ord(char)
    return actual == expected


def _split_sentence(line: bytes) -> list[bytes] | None:
    try:
        text = line.decode("ascii", "strict").strip()
    except UnicodeDecodeError:
        return None
    if not text.startswith(("!AIVDM", "!AIVDO", "$AIVDM", "$AIVDO")):
        return None
    if not checksum_ok(text):
        return None
    return text.encode("ascii").split(b",")


class SentenceAssembler:
    """Reassemble multipart AIVDM sentences.

    AIS type 5 and 24 messages arrive as two or three NMEA sentences that may
    interleave with other traffic.  Fragments are keyed the way the AIS
    standard intends: sequence id, radio channel and fragment count.
    Incomplete groups are dropped after ``max_age_s`` so a lossy receiver does
    not grow the buffer forever.
    """

    def __init__(self, max_age_s: float = 10.0) -> None:
        self.max_age_s = max_age_s
        self._groups: dict[tuple[bytes, bytes, int], dict[int, bytes]] = {}
        self._seen: dict[tuple[bytes, bytes, int], float] = {}

    def feed(self, line: bytes | str) -> list[bytes] | None:
        """Return a complete message's sentence list, or ``None``."""
        raw = line.encode("ascii", "ignore") if isinstance(line, str) else line
        parts = _split_sentence(raw)
        if not parts or len(parts) < 7:
            return None
        try:
            frag_count = int(parts[1])
            frag_num = int(parts[2])
        except ValueError:
            return None
        if frag_count <= 1:
            return [raw.strip()]
        if not 1 <= frag_num <= frag_count:
            return None

        key = (parts[3], parts[4], frag_count)
        now = time.monotonic()
        self._expire(now)
        group = self._groups.setdefault(key, {})
        group[frag_num] = raw.strip()
        self._seen[key] = now
        if len(group) != frag_count:
            return None
        lines = [group[i] for i in range(1, frag_count + 1)]
        del self._groups[key]
        self._seen.pop(key, None)
        return lines

    def _expire(self, now: float) -> None:
        stale = [k for k, t in self._seen.items() if now - t > self.max_age_s]
        for key in stale:
            self._groups.pop(key, None)
            self._seen.pop(key, None)


def decode(lines: list[bytes] | list[str]) -> AisTarget | None:
    """Decode one complete AIS message (one or more NMEA sentences)."""
    if not lines:
        return None
    channel = None
    for line in lines:
        raw = line.encode("ascii", "ignore") if isinstance(line, str) else line
        if _split_sentence(raw) is None:
            return None
    try:
        parts = _split_sentence(lines[-1] if isinstance(lines[-1], bytes) else lines[-1].encode())
        if parts and len(parts) > 4:
            channel = parts[4].decode("ascii", "ignore") or None
        msg = pyais.decode(*lines)
    except Exception:
        # A truncated sentence is normal on a noisy VHF channel.  Dropping it
        # is the correct behaviour.
        return None
    return normalise(msg, channel)
