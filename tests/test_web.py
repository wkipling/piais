from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import logging
import stat
from pathlib import Path

import pytest

from piais import web
from piais.config import SdrConfig, WebConfig, WifiConfig


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeRunner:
    def __init__(self, results: dict[tuple[str, ...], web.CommandResult] | None = None) -> None:
        self.calls: list[tuple[tuple[str, ...], float]] = []
        self.results = results or {}

    async def run(self, argv, timeout: float) -> web.CommandResult:
        key = tuple(argv)
        self.calls.append((key, timeout))
        return self.results.get(key, web.CommandResult(argv=key, returncode=0))

    @property
    def argv(self) -> list[tuple[str, ...]]:
        return [call[0] for call in self.calls]


class FakeSleep:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, delay: float) -> None:
        self.delays.append(delay)


def split_response(raw: bytes) -> tuple[int, dict[str, str], bytes]:
    head, _, body = raw.partition(b"\r\n\r\n")
    lines = head.decode("latin-1").split("\r\n")
    status = int(lines[0].split(" ")[1])
    headers: dict[str, str] = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    return status, headers, body


def build_request(
    path: str,
    *,
    token: str | None = None,
    method: str = "GET",
    body: bytes = b"",
    headers: tuple[tuple[str, str], ...] = (),
) -> bytes:
    lines = [f"{method} {path} HTTP/1.1", "Host: 127.0.0.1"]
    if token:
        lines.append(f"Cookie: {web.TOKEN_COOKIE}={token}")
    for name, value in headers:
        lines.append(f"{name}: {value}")
    if body:
        lines.append(f"Content-Length: {len(body)}")
        lines.append("Content-Type: application/x-www-form-urlencoded")
    return ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1") + body


async def send(server: web.WebServer, request: bytes) -> bytes:
    reader, writer = await asyncio.open_connection("127.0.0.1", server.port)
    writer.write(request)
    await writer.drain()
    data = await reader.read()
    writer.close()
    with contextlib.suppress(ConnectionError):
        await writer.wait_closed()
    return data


@contextlib.asynccontextmanager
async def control_server(**overrides):
    config = overrides.pop(
        "config",
        WebConfig(
            host="127.0.0.1", port=0, username="piais", password="", log_lines=50
        ),
    )
    tokens = web.TokenStore()
    runner = overrides.pop("runner", FakeRunner())
    sdr = overrides.pop(
        "sdr",
        web.SdrController(
            SdrConfig(off_command=["decoder-stop"], on_command=["decoder-start"]),
            runner=runner,
        ),
    )
    wifi = overrides.pop("wifi", None)
    logs = overrides.pop("logs", None)
    status = overrides.pop("status", None) or (
        lambda: {
            "mode": "bridge",
            "running": True,
            "uptime_s": 5,
            "ais": {"decoded": 3, "unsupported": 1, "last_datagram_age_s": 2.0},
        }
    )
    server = web.WebServer(
        config, tokens=tokens, sdr=sdr, wifi=wifi, status=status, logs=logs
    )
    await server.start()
    try:
        yield server, tokens, sdr, runner
    finally:
        await server.close()


# ---------------------------------------------------------------------------
# pure helpers
# ---------------------------------------------------------------------------


def test_summarise_http_hides_credentials_and_query() -> None:
    assert (
        web.summarise_http("https://user:secret@example.test:8443/a/b?token=abc#frag")
        == "https://example.test:8443/a/b"
    )


def test_summarise_http_keeps_ipv6_brackets() -> None:
    assert web.summarise_http("http://[::1]:8080/") == "http://[::1]:8080/"


def test_summarise_http_rejects_nonsense() -> None:
    assert web.summarise_http("not a url") == "<unparsable>"
    assert web.summarise_http("http://example.test:bad/") == "<unparsable>"


def test_parse_basic_auth_accepts_correct_credentials() -> None:
    header = "Basic " + base64.b64encode(b"piais:s3cret").decode()
    assert web.parse_basic_auth(header, "piais", "s3cret") is True


@pytest.mark.parametrize(
    "header",
    [
        None,
        "",
        "Basic",
        "Bearer abc",
        "Basic !!!not-base64!!!",
        "Basic " + base64.b64encode(b"piais").decode(),
        "Basic " + base64.b64encode(b"piais:wrong").decode(),
        "Basic " + base64.b64encode(b"other:s3cret").decode(),
    ],
)
def test_parse_basic_auth_rejects_bad_input(header: str | None) -> None:
    assert web.parse_basic_auth(header, "piais", "s3cret") is False


def test_parse_basic_auth_never_matches_an_empty_password() -> None:
    header = "Basic " + base64.b64encode(b"piais:").decode()
    assert web.parse_basic_auth(header, "piais", "") is False


def test_parse_form_keeps_first_value_and_blanks() -> None:
    assert web.parse_form(b"a=1&b=&a=3") == {"a": "1", "b": ""}


def test_parse_form_rejects_invalid_utf8() -> None:
    assert web.parse_form(b"a=\xff") == {}


# ---------------------------------------------------------------------------
# token store
# ---------------------------------------------------------------------------


def test_token_store_persists_and_reloads(tmp_path: Path) -> None:
    path = tmp_path / "piais" / "token"
    first = web.TokenStore(path)
    assert path.read_text() == first.token
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert web.TokenStore(path).token == first.token


def test_token_store_rotates_and_accepts_the_old_token_briefly() -> None:
    store = web.TokenStore()
    old = store.token
    new = store.rotate()
    assert new != old
    assert store.accepts(new) is True
    assert store.accepts(old) is True
    assert store.accepts("nope") is False


def test_token_store_without_a_path_stays_in_memory() -> None:
    assert len(web.TokenStore().token) == 32


# ---------------------------------------------------------------------------
# SDR controller
# ---------------------------------------------------------------------------


def run(coro):
    return asyncio.run(coro)


def test_sdr_stop_runs_decoder_then_power_after_a_delay() -> None:
    runner = FakeRunner()
    sleep = FakeSleep()
    controller = web.SdrController(
        SdrConfig(
            off_command=["decoder-stop"],
            power_off_command=["usb-off"],
            off_delay_s=0.5,
        ),
        runner=runner,
        sleep=sleep,
    )

    result = run(controller.stop())

    assert result.ok is True
    assert runner.argv == [("decoder-stop",), ("usb-off",)]
    assert sleep.delays == [0.5]
    assert controller.status()["state"] == "stopped"
    assert controller.status()["power_cut"] is True


def test_sdr_stop_without_any_command_reports_instead_of_pretending() -> None:
    controller = web.SdrController(SdrConfig(), runner=FakeRunner())
    result = run(controller.stop())
    assert result.ok is False
    assert "no off_command" in result.error
    assert controller.status()["state"] == "unknown"


def test_sdr_start_after_a_power_cut_requires_power_on() -> None:
    controller = web.SdrController(
        SdrConfig(off_command=["stop"], power_off_command=["usb-off"]),
        runner=FakeRunner(),
    )
    run(controller.stop())
    result = run(controller.start())
    assert result.ok is False
    assert "power_on_command" in result.error


def test_sdr_start_restores_power_then_delays_then_starts_the_decoder() -> None:
    runner = FakeRunner()
    sleep = FakeSleep()
    controller = web.SdrController(
        SdrConfig(
            power_on_command=["usb-on"],
            on_command=["decoder-start"],
            on_delay_s=2.0,
            off_command=["decoder-stop"],
            power_off_command=["usb-off"],
        ),
        runner=runner,
        sleep=sleep,
    )
    run(controller.stop())
    runner.calls.clear()
    sleep.delays.clear()

    result = run(controller.start())

    assert result.ok is True
    assert runner.argv == [("usb-on",), ("decoder-start",)]
    assert sleep.delays == [2.0]
    assert controller.status()["power_cut"] is False
    assert controller.status()["state"] == "running"


def test_sdr_start_after_power_restore_without_on_command_is_unknown() -> None:
    controller = web.SdrController(
        SdrConfig(
            off_command=["stop"],
            power_off_command=["usb-off"],
            power_on_command=["usb-on"],
        ),
        runner=FakeRunner(),
    )
    run(controller.stop())
    result = run(controller.start())
    assert result.ok is False
    assert "no on_command" in result.error
    assert controller.status()["state"] == "unknown"


def test_sdr_restart_prefers_the_restart_command() -> None:
    runner = FakeRunner()
    controller = web.SdrController(
        SdrConfig(restart_command=["restart"], off_command=["stop"], on_command=["start"]),
        runner=runner,
    )
    result = run(controller.restart())
    assert result.ok is True
    assert runner.argv == [("restart",)]


def test_sdr_failure_is_visible_as_unknown() -> None:
    runner = FakeRunner(
        {("stop",): web.CommandResult(argv=("stop",), returncode=1, output="access denied")}
    )
    controller = web.SdrController(SdrConfig(off_command=["stop"]), runner=runner)
    result = run(controller.stop())
    assert result.ok is False
    status = controller.status()
    assert status["state"] == "unknown"
    assert "access denied" in status["error"]


# ---------------------------------------------------------------------------
# Wi-Fi watchdog
# ---------------------------------------------------------------------------


def wifi_config(**overrides) -> WifiConfig:
    values = dict(
        enabled=True,
        off_after_s=60.0,
        reopen_interval_s=0.0,
        off_command=["wifi-off"],
        on_command=["wifi-on"],
    )
    values.update(overrides)
    return WifiConfig(**values)


def make_wifi(**overrides):
    clock = overrides.pop("clock", FakeClock())
    runner = overrides.pop("runner", FakeRunner())
    addresses = overrides.pop("addresses", lambda: frozenset({"192.168.1.50"}))
    controller = web.WifiController(
        wifi_config(**overrides),
        runner=runner,
        addresses=addresses,
        clock=clock,
        state_path=None,
    )
    return controller, clock, runner


def test_wifi_timer_starts_at_boot() -> None:
    controller, clock, _runner = make_wifi()
    assert controller._delay() == 60.0
    clock.advance(59)
    assert controller._delay() == pytest.approx(1.0)
    clock.advance(2)
    assert controller._delay() == 0.0


def test_wifi_client_resets_the_timer() -> None:
    controller, clock, _runner = make_wifi()
    clock.advance(50)
    assert controller.touch("192.168.1.9", ("192.168.1.50", 40000)) == "wifi"
    assert controller._delay() == 60.0


def test_loopback_request_does_not_reset_the_timer() -> None:
    controller, clock, _runner = make_wifi()
    clock.advance(50)
    assert controller.touch("127.0.0.1", ("127.0.0.1", 40000)) == "loopback"
    assert controller._delay() == pytest.approx(10.0)


def test_ethernet_request_does_not_reset_the_timer() -> None:
    controller, clock, _runner = make_wifi()
    clock.advance(50)
    assert controller.touch("192.168.1.9", ("192.168.1.10", 40000)) == "other"
    assert controller._delay() == pytest.approx(10.0)


def test_unknown_interface_is_treated_as_wifi_not_cut_off() -> None:
    controller, clock, _runner = make_wifi(addresses=lambda: frozenset())
    clock.advance(50)
    assert controller.touch("192.168.1.9", ("192.168.1.10", 40000)) == "unknown"
    assert controller._delay() == 60.0


def test_wifi_disabled_never_expires() -> None:
    controller, _clock, _runner = make_wifi(enabled=False)
    assert controller._delay() is None


def test_wifi_tick_turns_off_then_reopens() -> None:
    controller, clock, runner = make_wifi(reopen_interval_s=300.0)
    run(controller._tick())
    assert runner.argv == [("wifi-off",)]
    assert controller.status()["state"] == "off"
    assert controller.status()["armed"] is False

    clock.advance(299)
    assert controller._delay() == pytest.approx(1.0)
    clock.advance(1)
    run(controller._tick())
    assert runner.argv == [("wifi-off",), ("wifi-on",)]
    assert controller.status()["state"] == "on"
    assert controller.status()["armed"] is True


def test_wifi_off_failure_backs_off_instead_of_hammering() -> None:
    runner = FakeRunner(
        {("wifi-off",): web.CommandResult(argv=("wifi-off",), returncode=1)}
    )
    controller, _clock, _runner = make_wifi(runner=runner)
    run(controller._tick())
    assert controller.status()["state"] == "unknown"
    assert "failed" in controller.status()["error"]
    assert controller._delay() >= 30.0


def test_wifi_without_off_command_reports_instead_of_succeeding() -> None:
    controller, _clock, _runner = make_wifi(off_command=[])
    result = run(controller.turn_off("test"))
    assert result.ok is False
    assert "no off_command" in result.error


def test_wifi_open_window_is_idempotent_when_already_on() -> None:
    controller, clock, runner = make_wifi()
    assert run(controller.open_window("first")).ok is True
    assert runner.argv == [("wifi-on",)]
    clock.advance(30)
    assert run(controller.open_window("second")).ok is True
    assert runner.argv == [("wifi-on",)]
    assert controller._delay() == 60.0


def test_wifi_watchdog_task_starts_and_stops() -> None:
    async def scenario() -> None:
        controller = web.WifiController(
            wifi_config(off_after_s=3600.0),
            runner=FakeRunner(),
            addresses=lambda: frozenset(),
            clock=FakeClock(),
            state_path=None,
        )
        await controller.start()
        assert controller.status()["watching"] is True
        await controller.stop()
        assert controller.status()["watching"] is False

    run(scenario())


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


def test_request_without_a_token_is_401() -> None:
    async def scenario() -> None:
        async with control_server() as (server, tokens, _sdr, _runner):
            status, _headers, body = split_response(
                await send(server, build_request("/"))
            )
        assert status == 401
        assert b"authentication required" in body

    run(scenario())


def test_token_in_the_url_becomes_a_cookie_and_leaves_the_url() -> None:
    async def scenario() -> None:
        async with control_server() as (server, tokens, _sdr, _runner):
            status, headers, _body = split_response(
                await send(server, build_request(f"/?token={tokens.token}&msg=hi"))
            )
        assert status == 303
        assert headers["location"] == "/?msg=hi"
        assert f"{web.TOKEN_COOKIE}={tokens.token}" in headers["set-cookie"]
        assert "HttpOnly" in headers["set-cookie"]

    run(scenario())


def test_cookie_authorises_and_renders_the_page() -> None:
    async def scenario() -> None:
        async with control_server() as (server, tokens, _sdr, _runner):
            status, _headers, body = split_response(
                await send(server, build_request("/", token=tokens.token))
            )
        assert status == 200
        assert b"AIS receiver" in body
        assert b"does not refresh itself" in body

    run(scenario())


def test_basic_auth_works_when_a_password_is_configured() -> None:
    config = WebConfig(
        host="0.0.0.0", port=0, username="piais", password="s3cret", log_lines=50
    )

    async def scenario() -> None:
        async with control_server(config=config) as (server, _tokens, _sdr, _runner):
            good = "Basic " + base64.b64encode(b"piais:s3cret").decode()
            status, _headers, _body = split_response(
                await send(
                    server,
                    build_request("/", headers=(("Authorization", good),)),
                )
            )
            bad_status, _headers, _body = split_response(
                await send(server, build_request("/"))
            )
        assert status == 200
        assert bad_status == 401

    run(scenario())


def test_cross_site_form_post_is_refused() -> None:
    async def scenario() -> None:
        async with control_server() as (server, tokens, _sdr, runner):
            status, _headers, _body = split_response(
                await send(
                    server,
                    build_request(
                        "/api/sdr/stop",
                        token=tokens.token,
                        method="POST",
                        headers=(("Sec-Fetch-Site", "cross-site"),),
                    ),
                )
            )
        assert status == 403
        assert runner.argv == []

    run(scenario())


def test_stop_button_runs_the_configured_command() -> None:
    async def scenario() -> None:
        async with control_server() as (server, tokens, _sdr, runner):
            status, headers, _body = split_response(
                await send(
                    server,
                    build_request("/api/sdr/stop", token=tokens.token, method="POST"),
                )
            )
        assert status == 303
        assert headers["location"].startswith("/?")
        assert runner.argv == [("decoder-stop",)]

    run(scenario())


def test_api_status_returns_json() -> None:
    async def scenario() -> None:
        async with control_server() as (server, tokens, _sdr, _runner):
            status, headers, body = split_response(
                await send(server, build_request("/api/status", token=tokens.token))
            )
        assert status == 200
        assert headers["content-type"].startswith("application/json")
        document = json.loads(body)
        assert document["pipeline"]["ais"]["decoded"] == 3
        assert document["sdr"]["state"] == "unknown"
        assert document["web"]["loopback"] is True

    run(scenario())


def test_debug_page_shows_captured_log_lines() -> None:
    logs = web.RingLogHandler(10)
    logger = logging.getLogger("piais-test")

    async def scenario() -> None:
        async with control_server(logs=logs) as (server, tokens, _sdr, _runner):
            status, _headers, body = split_response(
                await send(server, build_request("/debug", token=tokens.token))
            )
        assert status == 200
        assert b"a very specific log line" in body
        assert b"Rotate session token" in body

    logger.addHandler(logs)
    logger.setLevel(logging.INFO)
    try:
        logger.info("a very specific log line")
        run(scenario())
    finally:
        logger.removeHandler(logs)
        logger.setLevel(logging.NOTSET)


def test_oversized_body_is_refused_before_it_is_read() -> None:
    async def scenario() -> None:
        async with control_server() as (server, tokens, _sdr, _runner):
            status, _headers, _body = split_response(
                await send(
                    server,
                    build_request(
                        "/api/sdr/stop",
                        token=tokens.token,
                        method="POST",
                        headers=(
                            ("Content-Length", str(web.MAX_BODY_BYTES + 1)),
                            ("Content-Type", "application/x-www-form-urlencoded"),
                        ),
                    ),
                )
            )
        assert status == 413

    run(scenario())


def test_unknown_path_is_404_and_bad_method_is_405() -> None:
    async def scenario() -> None:
        async with control_server() as (server, tokens, _sdr, _runner):
            missing, _headers, _body = split_response(
                await send(server, build_request("/nope", token=tokens.token))
            )
            wrong_method, headers, _body = split_response(
                await send(
                    server,
                    build_request("/", token=tokens.token, method="PUT"),
                )
            )
        assert missing == 404
        assert wrong_method == 405
        assert headers["allow"] == "GET, POST"

    run(scenario())


def test_token_rotation_returns_the_new_token_and_keeps_the_old_one_working() -> None:
    async def scenario() -> None:
        async with control_server() as (server, tokens, _sdr, _runner):
            old = tokens.token
            status, _headers, body = split_response(
                await send(
                    server,
                    build_request(
                        "/api/token/rotate",
                        token=old,
                        method="POST",
                        headers=(("Accept", "application/json"),),
                    ),
                )
            )
            assert status == 200
            new = json.loads(body)["token"]
            assert new != old
            assert tokens.accepts(old) is True
            assert tokens.accepts(new) is True
            page_status, _headers, _body = split_response(
                await send(server, build_request("/", token=new))
            )
        assert page_status == 200

    run(scenario())


def test_web_page_never_asks_the_browser_to_poll() -> None:
    async def scenario() -> None:
        async with control_server() as (server, tokens, _sdr, _runner):
            _status, headers, body = split_response(
                await send(server, build_request("/", token=tokens.token))
            )
        assert "refresh" not in headers
        assert b"http-equiv" not in body
        assert b"setInterval" not in body

    run(scenario())
