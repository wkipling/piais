"""TOML configuration for the piais daemon."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import tomllib

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


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
class WebConfig:
    #: Off by default so development and tests do not open a socket.
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 8080
    username: str = "piais"
    #: Required when host is not loopback.  The daemon refuses to expose the
    #: control page on a LAN without it.
    password: str = ""
    #: Log lines kept in RAM for the debug view.
    log_lines: int = 500


@dataclass(slots=True)
class SdrConfig:
    """Commands used by the control page.

    Each command is an argv array and is executed without a shell.  Empty
    commands are treated as "not configured", never as success.
    """

    #: Stop/start the AIS decoder (for example a systemd unit).
    on_command: list[str] = field(default_factory=list)
    off_command: list[str] = field(default_factory=list)
    restart_command: list[str] = field(default_factory=list)
    #: Optional true power switching (uhubctl, GPIO helper, ...).
    power_on_command: list[str] = field(default_factory=list)
    power_off_command: list[str] = field(default_factory=list)
    timeout_s: float = 15.0
    #: Wait for the USB device to enumerate before starting the decoder.
    on_delay_s: float = 2.0
    #: Wait for the decoder to release the device before cutting power.
    off_delay_s: float = 0.5


@dataclass(slots=True)
class WifiConfig:
    enabled: bool = False
    #: Turn Wi-Fi off after this many seconds with no control-page request.
    #: 0 disables the watchdog.
    off_after_s: float = 60.0
    #: If > 0, briefly re-enable Wi-Fi at this interval to allow reconnection.
    reopen_interval_s: float = 0.0
    on_command: list[str] = field(default_factory=list)
    off_command: list[str] = field(default_factory=list)
    timeout_s: float = 15.0


@dataclass(slots=True)
class LogConfig:
    level: str = "INFO"


@dataclass(slots=True)
class Config:
    ais: AisConfig = field(default_factory=AisConfig)
    n2k: N2KConfig = field(default_factory=N2KConfig)
    vedirect: VeDirectConfig = field(default_factory=VeDirectConfig)
    web: WebConfig = field(default_factory=WebConfig)
    sdr: SdrConfig = field(default_factory=SdrConfig)
    wifi: WifiConfig = field(default_factory=WifiConfig)
    log: LogConfig = field(default_factory=LogConfig)


def load_config(path: str | Path | None = None) -> Config:
    """Load a TOML file; an absent path returns defaults."""
    if path is None:
        return Config()
    raw = tomllib.loads(Path(path).read_text(encoding="utf-8"))

    web_values = _section(raw, "web", WebConfig)
    sdr_values = _section(raw, "sdr", SdrConfig)
    wifi_values = _section(raw, "wifi", WifiConfig)
    _commands(
        sdr_values,
        "sdr",
        ("on_command", "off_command", "restart_command", "power_on_command", "power_off_command"),
    )
    _commands(wifi_values, "wifi", ("on_command", "off_command"))

    config = Config(
        ais=AisConfig(**_section(raw, "ais", AisConfig)),
        n2k=N2KConfig(**_section(raw, "n2k", N2KConfig)),
        vedirect=VeDirectConfig(**_section(raw, "vedirect", VeDirectConfig)),
        web=WebConfig(**web_values),
        sdr=SdrConfig(**sdr_values),
        wifi=WifiConfig(**wifi_values),
        log=LogConfig(**_section(raw, "log", LogConfig)),
    )
    _validate(config)
    return config


def _section(raw: dict[str, object], name: str, cls: type) -> dict[str, object]:
    values = raw.get(name, {})
    if not isinstance(values, dict):
        raise ValueError(f"config section [{name}] must be a table")
    known = set(cls.__dataclass_fields__)  # type: ignore[attr-defined]
    unknown = set(values) - known
    if unknown:
        raise ValueError(f"unknown keys in [{name}]: {', '.join(sorted(unknown))}")
    return values


def _commands(values: dict[str, object], section: str, keys: tuple[str, ...]) -> None:
    for key in keys:
        value = values.get(key, [])
        if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
            raise ValueError(f"[{section}] {key} must be an array of strings")


def _validate(config: Config) -> None:
    web = config.web
    if not 1 <= web.port <= 65535:
        raise ValueError("web.port must be between 1 and 65535")
    if web.log_lines < 1:
        raise ValueError("web.log_lines must be at least 1")
    if web.enabled:
        if not web.username.strip():
            raise ValueError("web.username must not be empty")
        if web.host not in LOOPBACK_HOSTS and not web.password:
            raise ValueError(
                "web.password must be set when web.host is not loopback; "
                "otherwise the control page would be open to the network"
            )

    sdr = config.sdr
    if sdr.timeout_s <= 0:
        raise ValueError("sdr.timeout_s must be positive")
    if sdr.on_delay_s < 0 or sdr.off_delay_s < 0:
        raise ValueError("sdr delays must not be negative")

    wifi = config.wifi
    if wifi.timeout_s <= 0:
        raise ValueError("wifi.timeout_s must be positive")
    if wifi.off_after_s < 0:
        raise ValueError("wifi.off_after_s must not be negative")
    if wifi.reopen_interval_s < 0:
        raise ValueError("wifi.reopen_interval_s must not be negative")
    if wifi.enabled and wifi.off_after_s > 0 and not wifi.off_command:
        raise ValueError("wifi.off_command must be set when wifi.enabled")
    if wifi.enabled and wifi.reopen_interval_s > 0 and not wifi.on_command:
        raise ValueError("wifi.on_command must be set when wifi.reopen_interval_s > 0")
