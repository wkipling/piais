"""TOML configuration for the piais daemon."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import tomllib


@dataclass(slots=True)
class AisConfig:
    udp_host: str = "127.0.0.1"
    udp_port: int = 10110
    #: Drop our own vessel; an AIS target list should not contain own ship.
    own_mmsi: int = 0
    #: Minimum seconds between position reports for the same MMSI and type.
    min_update_interval_s: float = 2.0
    #: Static data (type 5/24) repeats rarely and can be held back longer.
    static_update_interval_s: float = 60.0


@dataclass(slots=True)
class N2KConfig:
    interface: str = "socketcan"
    channel: str = "can0"
    preferred_address: int = 25
    #: Log encoded CAN frames instead of opening a socket.
    dry_run: bool = False
    #: ISO NAME identity.  Keep these stable across reboots.
    model_id: str = "piais AIS gateway"
    manufacturer_information: str = "piais"


@dataclass(slots=True)
class VeDirectConfig:
    enabled: bool = False
    port: str = "/dev/ttyUSB0"
    baudrate: int = 19200
    battery_instance: int = 0


@dataclass(slots=True)
class LogConfig:
    level: str = "INFO"


@dataclass(slots=True)
class Config:
    ais: AisConfig = field(default_factory=AisConfig)
    n2k: N2KConfig = field(default_factory=N2KConfig)
    vedirect: VeDirectConfig = field(default_factory=VeDirectConfig)
    log: LogConfig = field(default_factory=LogConfig)


def load_config(path: str | Path | None = None) -> Config:
    """Load a TOML file; an absent path returns defaults."""
    if path is None:
        return Config()
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))
    return Config(
        ais=AisConfig(**_section(raw, "ais", AisConfig)),
        n2k=N2KConfig(**_section(raw, "n2k", N2KConfig)),
        vedirect=VeDirectConfig(**_section(raw, "vedirect", VeDirectConfig)),
        log=LogConfig(**_section(raw, "log", LogConfig)),
    )


def _section(raw: dict[str, object], name: str, cls: type) -> dict[str, object]:
    values = raw.get(name, {})
    if not isinstance(values, dict):
        raise ValueError(f"config section [{name}] must be a table")
    known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
    unknown = set(values) - known
    if unknown:
        raise ValueError(f"unknown keys in [{name}]: {', '.join(sorted(unknown))}")
    return values
