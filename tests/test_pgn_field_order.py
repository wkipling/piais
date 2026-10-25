"""The PGN field lists must match canboat exactly, in order.

nmea2000 encodes fields positionally.  A canboat update that inserts a field
would otherwise shift every following value into the wrong bits without any
error, so this is the most important regression test in the repository.
"""

from __future__ import annotations

import pytest
from nmea2000._canboat import Database

from piais.ais import AisTarget
from piais.pgn import to_nmea2000

CASES = [
    (1, None, 129038),
    (18, None, 129039),
    (19, None, 129040),
    (21, None, 129041),
    (5, None, 129794),
    (24, 0, 129809),
    (24, 1, 129810),
]


@pytest.mark.parametrize("msg_type,partno,pgn", CASES)
def test_field_order_matches_canboat(msg_type: int, partno: int | None, pgn: int) -> None:
    target = AisTarget(
        msg_type=msg_type,
        partno=partno,
        mmsi=244009864,
        lat=51.3,
        lon=6.7,
        dim_bow=10,
        dim_stern=2,
        dim_port=2,
        dim_starboard=2,
    )
    message = to_nmea2000(target)
    assert message is not None
    assert message.PGN == pgn

    expected = [f.id for f in Database("si").pgn_variants(pgn)[0].fields]
    actual = [f.id for f in message.fields]
    assert actual == expected
