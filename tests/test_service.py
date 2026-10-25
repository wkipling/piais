from __future__ import annotations

import asyncio
from types import SimpleNamespace

from piais.config import Config
from piais.service import AisService

from tests.conftest import AIVDM


def make_service() -> tuple[AisService, list[tuple[int, int]]]:
    config = Config()
    config.n2k.dry_run = True
    service = AisService(config)
    sent: list[tuple[int, int]] = []
    service._send = lambda message, mmsi: sent.append((message.PGN, mmsi))  # type: ignore[method-assign]
    return service, sent


def test_position_report_is_sent_once_per_interval() -> None:
    service, sent = make_service()
    service.feed_sentence(AIVDM["type1"])
    service.feed_sentence(AIVDM["type1"])
    assert sent == [(129038, 244009864)]
    assert service.suppressed == 1


def test_multiple_sentences_in_one_datagram() -> None:
    service, sent = make_service()
    service.feed_datagram(
        (AIVDM["type1"] + "\r\n" + AIVDM["type18"] + "\r\n").encode()
    )
    assert [pgn for pgn, _ in sent] == [129038, 129039]


def test_own_mmsi_is_dropped() -> None:
    service, sent = make_service()
    service.config.ais.own_mmsi = 244009864
    service.feed_sentence(AIVDM["type1"])
    assert sent == []
    assert service.suppressed == 1


def test_unsupported_type_is_counted() -> None:
    service, sent = make_service()
    # Type 27 (long range) is valid AIS but not mapped to a PGN here.
    service.feed_sentence(b"!AIVDM,1,1,,A,K3`e;R00?e3hN55`,0*1D")
    assert service.decoded == 1
    assert sent == []
    assert service.unsupported == 1


def test_static_data_uses_longer_interval() -> None:
    service, sent = make_service()
    service.feed_sentence(AIVDM["type5a"])
    service.feed_sentence(AIVDM["type5b"])
    service.feed_sentence(AIVDM["type24A"])
    assert [pgn for pgn, _ in sent] == [129794, 129809]


def test_assembler_ignores_non_ais_lines() -> None:
    service, sent = make_service()
    service.feed_sentence(b"$GPGGA,123519,4807.038,N,01131.000,E,1,08,0.9,545.4,M,46.9,M,,*47")
    assert sent == []
    assert service.sentences == 1
    assert service.decoded == 0


def test_type24_parts_are_rate_limited_independently() -> None:
    """24A (name) and 24B (dimensions) are separate messages; one must not
    suppress the other for the whole static interval."""
    service, sent = make_service()
    service.feed_sentence(AIVDM["type24A"])
    service.feed_sentence(AIVDM["type24B"])
    assert [pgn for pgn, _ in sent] == [129809, 129810]
    assert service.suppressed == 0


def test_send_failure_is_logged(caplog) -> None:
    """A failed encode or bus write must not disappear into a bare task."""

    class Boom:
        async def send(self, message) -> None:
            raise ValueError("boom")

        def log_frames(self, message) -> None:
            raise AssertionError("log_frames used outside bench mode")

    service = AisService(Config(), Boom())  # type: ignore[arg-type]

    async def go() -> None:
        service._send(SimpleNamespace(PGN=129038), 244009864)
        await asyncio.sleep(0.01)

    with caplog.at_level("ERROR", logger="piais"):
        asyncio.run(go())
    assert "N2K send failed" in caplog.text
