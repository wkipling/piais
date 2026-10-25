"""The piais daemon: AIS NMEA over UDP -> NMEA 2000 on SocketCAN.

AIS-catcher (or any other receiver) sends standard AIVDM sentences to a UDP
port.  This service reassembles and decodes them, rate-limits duplicate
reports, converts them to PGNs and hands the PGNs to the N2K sender.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import time
from typing import Iterable

from .ais import SentenceAssembler, AisTarget, decode
from .config import Config, load_config
from .n2k import N2KSender
from .pgn import SequenceCounter, to_nmea2000
from .vedirect import VeDirectParser, to_nmea2000 as vedirect_to_nmea2000

log = logging.getLogger("piais")

STATIC_TYPES = frozenset({5, 24})
MAX_TRACKED_TARGETS = 2000


class AisProtocol(asyncio.DatagramProtocol):
    def __init__(self, service: "AisService") -> None:
        self.service = service

    def datagram_received(self, data: bytes, addr: tuple[str, int]) -> None:
        self.service.feed_datagram(data)

    def error_received(self, exc: Exception) -> None:
        log.warning("AIS UDP socket error: %s", exc)


class AisService:
    def __init__(
        self,
        config: Config,
        sender: N2KSender | None = None,
        *,
        bench: bool = False,
    ) -> None:
        self.config = config
        self.sender = sender or N2KSender(config.n2k)
        self.assembler = SentenceAssembler()
        self.sequence = SequenceCounter()
        self._last_sent: dict[tuple[int, int], float] = {}
        self._transport: asyncio.DatagramTransport | None = None
        self._stopping = asyncio.Event()
        self._tasks: set[asyncio.Task] = set()
        #: Bench mode encodes synchronously instead of scheduling bus sends.
        self.bench = bench

        self.sentences = 0
        self.decoded = 0
        self.unsupported = 0
        self.suppressed = 0

    # ---- message path -------------------------------------------------

    def feed_datagram(self, data: bytes) -> None:
        for raw in data.replace(b"\r", b"\n").split(b"\n"):
            if len(raw) > 10:
                self.feed_sentence(raw)

    def feed_sentence(self, line: bytes | str) -> None:
        self.sentences += 1
        complete = self.assembler.feed(line)
        if complete is None:
            return
        target = decode(complete)
        if target is None:
            return
        self.decoded += 1
        self.handle_target(target)

    def _interval(self, target: AisTarget) -> float:
        return (
            self.config.ais.static_update_interval_s
            if target.msg_type in STATIC_TYPES
            else self.config.ais.min_update_interval_s
        )

    def _rate_key(self, target: AisTarget) -> tuple[int, int]:
        """Rate-limit key for one target.

        Type 24 arrives as two separate messages (24A carries the name, 24B
        the dimensions and callsign); they must not suppress each other, so
        the part number is part of the key.
        """
        if target.msg_type == 24:
            return (target.mmsi, 2 + (target.partno or 0))
        if target.msg_type in STATIC_TYPES:
            return (target.mmsi, 1)
        return (target.mmsi, 0)

    def _should_send(self, target: AisTarget) -> bool:
        now = time.monotonic()
        key = self._rate_key(target)
        last = self._last_sent.get(key)
        if last is not None and now - last < self._interval(target):
            self.suppressed += 1
            return False
        self._last_sent[key] = now
        if len(self._last_sent) > MAX_TRACKED_TARGETS:
            self._prune(now)
        return True

    def _prune(self, now: float) -> None:
        cutoff = now - 600.0
        for key in [k for k, seen in self._last_sent.items() if seen < cutoff]:
            del self._last_sent[key]
        # A very busy harbour can still exceed the cap inside the cutoff
        # window; evict the oldest entries so the table stays bounded.
        excess = len(self._last_sent) - MAX_TRACKED_TARGETS
        if excess > 0:
            oldest = sorted(self._last_sent.items(), key=lambda item: item[1])[:excess]
            for key, _ in oldest:
                del self._last_sent[key]

    def handle_target(self, target: AisTarget) -> None:
        own = self.config.ais.own_mmsi
        if own and target.mmsi == own:
            self.suppressed += 1
            return
        message = to_nmea2000(target, self.sequence)
        if message is None:
            self.unsupported += 1
            return
        if not self._should_send(target):
            return
        self._send(message, target.mmsi)

    def _send(self, message, mmsi: int) -> None:
        if self.bench:
            self.sender.log_frames(message)
        else:
            task = asyncio.get_running_loop().create_task(self.sender.send(message))
            self._tasks.add(task)
            task.add_done_callback(self._send_done)
        log.debug("PGN %s for MMSI %s", message.PGN, mmsi)

    def _send_done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if task.cancelled():
            return
        error = task.exception()
        if error is not None:
            # Otherwise the exception is only reported when the task is
            # garbage-collected, which hides encoder and bus failures.
            log.error("N2K send failed: %s", error, exc_info=error)

    # ---- lifecycle ----------------------------------------------------

    async def run(self) -> None:
        await self.sender.start()
        loop = asyncio.get_running_loop()
        transport, _ = await loop.create_datagram_endpoint(
            lambda: AisProtocol(self),
            local_addr=(self.config.ais.udp_host, self.config.ais.udp_port),
        )
        self._transport = transport  # type: ignore[assignment]
        log.info(
            "listening for AIS NMEA on udp://%s:%d",
            self.config.ais.udp_host,
            self.config.ais.udp_port,
        )
        vedirect_task = None
        if self.config.vedirect.enabled:
            vedirect_task = asyncio.create_task(self._run_vedirect())
        stats_task = asyncio.create_task(self._stats_loop())
        try:
            await self._stopping.wait()
        finally:
            # Stop reading first so no new send tasks can be scheduled while
            # the existing ones are being drained.
            if self._transport:
                self._transport.close()
            helpers = [stats_task]
            if vedirect_task:
                helpers.append(vedirect_task)
            for task in helpers:
                task.cancel()
            await asyncio.gather(*helpers, return_exceptions=True)
            if self._tasks:
                await asyncio.gather(*self._tasks, return_exceptions=True)
            await self.sender.close()
            log.info(
                "stopped: sentences=%d decoded=%d suppressed=%d unsupported=%d",
                self.sentences,
                self.decoded,
                self.suppressed,
                self.unsupported,
            )

    def stop(self) -> None:
        self._stopping.set()

    async def _stats_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            log.info(
                "AIS udp=%s:%d sentences=%d decoded=%d suppressed=%d unsupported=%d",
                self.config.ais.udp_host,
                self.config.ais.udp_port,
                self.sentences,
                self.decoded,
                self.suppressed,
                self.unsupported,
            )

    # ---- VE.Direct ----------------------------------------------------

    async def _run_vedirect(self) -> None:
        import serial  # imported lazily so the AIS-only path needs no serial port

        cfg = self.config.vedirect
        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[VeDirectFrame | None] = asyncio.Queue(maxsize=64)
        parser = VeDirectParser()

        def offer(item: VeDirectFrame | None) -> None:
            try:
                queue.put_nowait(item)
            except asyncio.QueueFull:
                log.warning("VE.Direct queue full; dropping a frame")

        def reader() -> None:
            try:
                with serial.Serial(cfg.port, cfg.baudrate, timeout=1) as port:
                    while not self._stopping.is_set():
                        chunk = port.read(64)
                        if not chunk:
                            continue
                        for frame in parser.feed(chunk):
                            loop.call_soon_threadsafe(offer, frame)
            except Exception as exc:  # pragma: no cover - hardware path
                log.error("VE.Direct reader on %s failed: %s", cfg.port, exc)
            finally:
                # Wake the consumer so a dead port is reported instead of
                # leaving the task blocked on queue.get() forever.
                loop.call_soon_threadsafe(offer, None)

        reader_task = asyncio.create_task(asyncio.to_thread(reader))
        log.info("VE.Direct on %s at %d baud", cfg.port, cfg.baudrate)
        try:
            while True:
                frame = await queue.get()
                if frame is None:
                    log.error("VE.Direct reader stopped; battery data disabled")
                    return
                message = vedirect_to_nmea2000(frame, instance=cfg.battery_instance)
                if message is not None:
                    await self.sender.send(message)
                    log.debug(
                        "battery %.2f V %.2f A",
                        frame.battery_voltage_v or 0.0,
                        frame.battery_current_a or 0.0,
                    )
        finally:
            reader_task.cancel()


def _setup_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )


def _encode_sentences(sentences: Iterable[str], config: Config) -> int:
    """Bench helper: decode AIVDM on the command line and print CAN frames."""
    config.n2k.dry_run = True
    sender = N2KSender(config.n2k)
    service = AisService(config, sender, bench=True)
    for sentence in sentences:
        for raw in sentence.replace("\r", "\n").split("\n"):
            if raw.strip():
                service.feed_sentence(raw.encode("ascii", "ignore"))
    print(
        f"decoded={service.decoded} suppressed={service.suppressed} "
        f"unsupported={service.unsupported}",
        file=sys.stderr,
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="piais-aisd")
    parser.add_argument("--config", default=None, help="path to piais.toml")
    parser.add_argument("--dry-run", action="store_true", help="log CAN frames, no socket")
    parser.add_argument("--log-level", default=None)
    parser.add_argument(
        "--sentence",
        action="append",
        default=None,
        help="decode AIVDM sentence(s) and exit (bench mode)",
    )
    args = parser.parse_args(argv)

    config = load_config(args.config)
    if args.dry_run:
        config.n2k.dry_run = True
    _setup_logging(args.log_level or config.log.level)

    if args.sentence:
        return _encode_sentences(args.sentence, config)

    service = AisService(config)
    try:
        asyncio.run(service.run())
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
