"""The bus path must use the same SI encoder as the dry-run path."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from piais.config import N2KConfig
from piais.n2k import N2KSender


class FakeDevice:
    """Stands in for N2KDevice without opening SocketCAN."""

    def __init__(self) -> None:
        self.client = SimpleNamespace(encoder=None)
        self.address = 25

    async def start(self) -> None:
        pass

    async def wait_ready(self, timeout: float | None = None) -> bool:
        return True

    async def close(self) -> None:
        pass


def test_bus_path_uses_si_encoder(monkeypatch) -> None:
    fake = FakeDevice()
    monkeypatch.setattr("piais.n2k.N2KDevice.for_python_can", lambda **kwargs: fake)

    sender = N2KSender(N2KConfig())
    asyncio.run(sender.start())
    try:
        assert fake.client.encoder is not None
        assert fake.client.encoder.units == "si"
    finally:
        asyncio.run(sender.close())
