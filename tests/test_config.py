from __future__ import annotations

from pathlib import Path

import pytest

from piais.config import Config, load_config


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "piais.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_defaults_leave_the_control_page_off() -> None:
    config = load_config(None)
    assert config.web.enabled is False
    assert config.web.host == "127.0.0.1"
    assert config.sdr.on_command == []
    assert config.wifi.enabled is False


def test_loopback_without_a_password_is_allowed(tmp_path: Path) -> None:
    path = write(tmp_path, "[web]\nenabled = true\nhost = \"127.0.0.1\"\n")
    assert load_config(path).web.enabled is True


@pytest.mark.parametrize("host", ["0.0.0.0", "192.168.1.20", "::"])
def test_network_exposed_page_without_a_password_is_refused(
    tmp_path: Path, host: str
) -> None:
    path = write(
        tmp_path,
        f'[web]\nenabled = true\nhost = "{host}"\n',
    )
    with pytest.raises(ValueError, match="password"):
        load_config(path)


def test_network_exposed_page_with_a_password_is_allowed(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        '[web]\nenabled = true\nhost = "0.0.0.0"\npassword = "s3cret"\n',
    )
    assert load_config(path).web.password == "s3cret"


def test_unknown_keys_are_rejected(tmp_path: Path) -> None:
    path = write(tmp_path, "[web]\nenabled = true\nhostt = \"0.0.0.0\"\n")
    with pytest.raises(ValueError, match="unknown keys in \\[web\\]"):
        load_config(path)


def test_commands_must_be_arrays_of_strings(tmp_path: Path) -> None:
    path = write(tmp_path, '[sdr]\noff_command = "systemctl stop ais-catcher"\n')
    with pytest.raises(ValueError, match="array of strings"):
        load_config(path)


def test_wifi_watchdog_requires_an_off_command(tmp_path: Path) -> None:
    path = write(tmp_path, "[wifi]\nenabled = true\noff_after_s = 60\n")
    with pytest.raises(ValueError, match="wifi.off_command"):
        load_config(path)


def test_wifi_reopen_requires_an_on_command(tmp_path: Path) -> None:
    path = write(
        tmp_path,
        "[wifi]\nenabled = true\nreopen_interval_s = 300\n"
        'off_command = ["off"]\n',
    )
    with pytest.raises(ValueError, match="wifi.on_command"):
        load_config(path)


def test_zero_off_after_disables_the_watchdog_without_an_off_command(
    tmp_path: Path,
) -> None:
    path = write(tmp_path, "[wifi]\nenabled = true\noff_after_s = 0\n")
    config = load_config(path)
    assert config.wifi.off_after_s == 0.0


def test_bad_port_and_negative_delays_are_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="web.port"):
        load_config(write(tmp_path, "[web]\nport = 70000\n"))
    with pytest.raises(ValueError, match="sdr delays"):
        load_config(write(tmp_path, "[sdr]\non_delay_s = -1\n"))


def test_programmatic_defaults_are_constructible() -> None:
    config = Config()
    assert config.web.password == ""
    assert config.n2k.dry_run is False
