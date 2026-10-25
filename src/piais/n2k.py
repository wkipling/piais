"""NMEA 2000 transport.

On the boat this is SocketCAN on ``can0`` via the PiCAN2 (MCP2515) HAT.  The
``nmea2000`` library handles ISO 11783-5 address claiming and fast-packet
fragmentation, so this module stays deliberately thin.

``dry_run`` encodes the same frames and logs them instead of touching the
bus, which is how the test suite and bench work without a HAT.
"""

from __future__ import annotations

import logging

from nmea2000.device import N2KDevice
from nmea2000.encoder import create_encoder
from nmea2000.input_formats import N2KFormat
from nmea2000.message import NMEA2000Message

from .config import N2KConfig

log = logging.getLogger(__name__)


class N2KSender:
    def __init__(self, config: N2KConfig) -> None:
        self.config = config
        self._device: N2KDevice | None = None
        self._encoder = create_encoder(N2KFormat.PYTHON_CAN, units="si")

    async def start(self) -> None:
        if self.config.dry_run:
            log.info("N2K dry-run: frames will be logged, not transmitted")
            return
        self._device = N2KDevice.for_python_can(
            interface=self.config.interface,
            channel=self.config.channel,
            preferred_address=self.config.preferred_address,
            model_id=self.config.model_id,
            manufacturer_information=self.config.manufacturer_information,
        )
        await self._device.start()
        await self._device.wait_ready(timeout=10)
        log.info(
            "N2K ready on %s/%s as address %s",
            self.config.interface,
            self.config.channel,
            getattr(self._device, "address", "?"),
        )

    async def send(self, message: NMEA2000Message) -> None:
        if self.config.dry_run:
            for frame in self._encoder.encode(message):
                log.info(
                    "%s %08X [%d] %s",
                    self.config.channel,
                    frame.arbitration_id,
                    frame.dlc,
                    " ".join(f"{b:02X}" for b in frame.data),
                )
            return
        if self._device is None:
            raise RuntimeError("N2KSender.start() was not called")
        await self._device.send(message)

    async def close(self) -> None:
        if self._device is not None:
            await self._device.close()
            self._device = None
