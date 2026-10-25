"""Control page, SDR power control and the Wi-Fi idle watchdog.

Standard library only.  The target is a Zero 2 W, so the HTTP server is a
deliberately small HTTP/1.1 responder: one request per connection, no
keep-alive, no JavaScript, no auto-refresh.

The no-auto-refresh rule is not cosmetic.  The Wi-Fi watchdog turns the radio
off after a period with no client, and a page that polls itself would keep
that timer alive forever.

Commands are argv arrays and are executed without a shell.  An empty command
means "not configured", never "success": if piais cannot actually do
something, the page says so instead of pretending.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import collections
import dataclasses
import enum
import hmac
import html
import json
import logging
import os
import secrets
import socket
import struct
import time
import urllib.parse
from http import HTTPStatus
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

from .config import LOOPBACK_HOSTS, SdrConfig, WebConfig, WifiConfig

log = logging.getLogger("piais.web")

MAX_LINE_BYTES = 8 * 1024
MAX_HEADER_BYTES = 16 * 1024
MAX_BODY_BYTES = 8 * 1024
MAX_FORM_FIELDS = 64
OUTPUT_LIMIT = 4000
STATUS_OUTPUT_LIMIT = 500
TOKEN_COOKIE = "piais_token"
TOKEN_GRACE_S = 60.0
REQUEST_TIMEOUT_S = 15.0
SIOCGIFADDR = 0x8915


def is_loopback(host: str) -> bool:
    return host in LOOPBACK_HOSTS


# ---------------------------------------------------------------------------
# small parsing helpers (pure functions, easy to test)
# ---------------------------------------------------------------------------


def summarise_http(url: str) -> str:
    """Return a log-safe form of *url*: no credentials, query or fragment."""
    try:
        parts = urllib.parse.urlsplit(url)
        port = parts.port
    except ValueError:
        return "<unparsable>"
    if not parts.scheme or not parts.hostname:
        return "<unparsable>"
    host = parts.hostname
    if ":" in host:
        host = f"[{host}]"
    netloc = host if port is None else f"{host}:{port}"
    return urllib.parse.urlunsplit((parts.scheme, netloc, parts.path or "/", "", ""))


def parse_basic_auth(header: str | None, username: str, password: str) -> bool:
    """Check an HTTP Basic header.  An empty configured password never matches."""
    if not header or not password:
        return False
    scheme, _, payload = header.partition(" ")
    if scheme.lower() != "basic" or not payload.strip():
        return False
    try:
        decoded = base64.b64decode(payload.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError):
        return False
    user, separator, secret = decoded.partition(":")
    if not separator:
        return False
    # Both comparisons run; do not short-circuit on the username.
    return hmac.compare_digest(user, username) & hmac.compare_digest(secret, password)


def parse_form(body: bytes) -> dict[str, str]:
    """Parse an urlencoded form body; the first value for a key wins."""
    try:
        text = body.decode("utf-8")
        values = urllib.parse.parse_qs(
            text, keep_blank_values=True, max_num_fields=MAX_FORM_FIELDS
        )
    except (UnicodeDecodeError, ValueError):
        return {}
    return {key: items[0] for key, items in values.items() if items}


def wifi_addresses() -> frozenset[str]:
    """IPv4 addresses of interfaces the kernel marks as wireless.

    Linux only, and deliberately best-effort: an empty set means "cannot
    tell", which makes the watchdog conservative rather than cutting off a
    client it failed to classify.
    """
    try:
        import fcntl
    except ImportError:  # pragma: no cover - piais targets Linux
        return frozenset()

    found: set[str] = set()
    try:
        interfaces = socket.if_nameindex()
    except OSError:
        return frozenset()
    for _, name in interfaces:
        if name == "lo" or not os.path.exists(f"/sys/class/net/{name}/wireless"):
            continue
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            request = struct.pack("256s", name[:15].encode("ascii", "replace"))
            info = fcntl.ioctl(sock.fileno(), SIOCGIFADDR, request)
            found.add(socket.inet_ntoa(info[20:24]))
        except OSError:
            continue
        finally:
            sock.close()
    return frozenset(found)


def _tail(raw: bytes, limit: int = OUTPUT_LIMIT) -> str:
    text = raw.decode("utf-8", "replace").strip()
    if len(text) > limit:
        return "…" + text[-limit:]
    return text


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------


@dataclasses.dataclass(slots=True)
class CommandResult:
    argv: tuple[str, ...] = ()
    returncode: int | None = None
    output: str = ""
    timed_out: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and not self.timed_out and self.returncode == 0

    def summary(self) -> str:
        if self.error:
            return self.error
        if self.timed_out:
            return "timed out"
        text = f"exit {self.returncode}"
        if self.output:
            last = self.output.strip().splitlines()[-1][:200]
            text = f"{text}: {last}"
        return text


class CommandRunner:
    """Run one argv array without a shell and capture the output."""

    async def run(self, argv: Sequence[str], timeout: float) -> CommandResult:
        argv = tuple(argv)
        if not argv:
            return CommandResult(error="no command configured")
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
        except (OSError, ValueError) as exc:
            return CommandResult(argv=argv, error=f"cannot run {argv[0]}: {exc}")
        try:
            raw, _ = await asyncio.wait_for(process.communicate(), timeout=timeout)
        except TimeoutError:
            process.kill()
            try:
                await process.wait()
            except ProcessLookupError:  # pragma: no cover - already reaped
                pass
            return CommandResult(
                argv=argv, timed_out=True, error=f"timed out after {timeout:g} s"
            )
        return CommandResult(argv=argv, returncode=process.returncode, output=_tail(raw))


# ---------------------------------------------------------------------------
# session tokens
# ---------------------------------------------------------------------------


class TokenStore:
    """One session token, optionally persisted to a runtime file.

    The file lives on tmpfs, so it does not survive a reboot and does not
    touch the read-only root.  A failure to write is a warning, not a startup
    failure: the token is still valid for this process.
    """

    def __init__(self, path: Path | None = None, *, token: str | None = None) -> None:
        self.path = path
        self._token = token or self._load() or secrets.token_hex(16)
        self._previous: str | None = None
        self._previous_until = 0.0
        if path is not None and not path.exists():
            self._save()

    @property
    def token(self) -> str:
        return self._token

    def accepts(self, candidate: str | None) -> bool:
        if not candidate:
            return False
        if hmac.compare_digest(candidate, self._token):
            return True
        if self._previous and time.monotonic() < self._previous_until:
            return hmac.compare_digest(candidate, self._previous)
        return False

    def rotate(self) -> str:
        self._previous = self._token
        self._previous_until = time.monotonic() + TOKEN_GRACE_S
        self._token = secrets.token_hex(16)
        self._save()
        return self._token

    def _load(self) -> str | None:
        if self.path is None:
            return None
        try:
            value = self.path.read_text(encoding="ascii").strip()
        except OSError:
            return None
        return value or None

    def _save(self) -> None:
        if self.path is None:
            return
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self.path.with_name(self.path.name + ".tmp")
            temporary.write_text(self._token, encoding="ascii")
            os.chmod(temporary, 0o600)
            os.replace(temporary, self.path)
        except OSError as exc:
            log.warning("cannot persist the control token to %s: %s", self.path, exc)


# ---------------------------------------------------------------------------
# log capture
# ---------------------------------------------------------------------------


class RingLogHandler(logging.Handler):
    """Keep the last N formatted records in memory for /debug."""

    name = "piais-ring"

    def __init__(self, capacity: int, level: int = logging.INFO) -> None:
        super().__init__(level)
        self._lines: collections.deque[str] = collections.deque(maxlen=max(1, capacity))
        self.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self._lines.append(self.format(record))
        except Exception:  # pragma: no cover - formatting must never break logging
            self.handleError(record)

    def text(self, lines: int | None = None) -> str:
        if lines is None:
            selected: Iterable[str] = self._lines
        else:
            selected = list(self._lines)[-max(1, lines) :]
        return "\n".join(selected)

    def snapshot(self) -> list[str]:
        return list(self._lines)


def install_log_capture(capacity: int, level: str | int = "INFO") -> RingLogHandler:
    """Attach a ring handler to the root logger, replacing a previous one."""
    root = logging.getLogger()
    for existing in list(root.handlers):
        if isinstance(existing, RingLogHandler):
            root.removeHandler(existing)
    if isinstance(level, str):
        resolved = getattr(logging, level.upper(), logging.INFO)
    else:
        resolved = level
    handler = RingLogHandler(capacity, resolved)
    root.addHandler(handler)
    return handler


def remove_log_capture(handler: RingLogHandler | None) -> None:
    if handler is not None:
        logging.getLogger().removeHandler(handler)


# ---------------------------------------------------------------------------
# SDR control
# ---------------------------------------------------------------------------


class SdrState(str, enum.Enum):
    RUNNING = "running"
    STOPPING = "stopping"
    STOPPED = "stopped"
    STARTING = "starting"
    UNKNOWN = "unknown"


class SdrController:
    """Stop/start the AIS decoder, and cut USB power where the hardware allows.

    ``off_command`` stops the decoder process.  That saves CPU, not the
    dongle's idle current.  ``power_off_command`` is the real switch
    (uhubctl, a GPIO-driven load switch, ...).  If power was cut, starting
    again needs ``power_on_command``: there is no way to fake that.
    """

    def __init__(
        self,
        config: SdrConfig,
        *,
        runner: CommandRunner | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.config = config
        self.runner = runner or CommandRunner()
        self._sleep = sleep
        self._state = SdrState.UNKNOWN
        self._powered_off = False
        self._error: str | None = None
        self._last: CommandResult | None = None
        self._lock = asyncio.Lock()

    # -- capabilities ---------------------------------------------------

    @property
    def can_stop(self) -> bool:
        return bool(self.config.off_command or self.config.power_off_command)

    @property
    def can_start(self) -> bool:
        if self._powered_off:
            return bool(self.config.power_on_command)
        return bool(self.config.on_command)

    def status(self) -> dict[str, Any]:
        return {
            "state": self._state.value,
            "error": self._error,
            "power_cut": self._powered_off,
            "can_stop": self.can_stop,
            "can_start": self.can_start,
            "configured": {
                "on": bool(self.config.on_command),
                "off": bool(self.config.off_command),
                "restart": bool(self.config.restart_command),
                "power_on": bool(self.config.power_on_command),
                "power_off": bool(self.config.power_off_command),
            },
            "last": self._last_status(),
        }

    def _last_status(self) -> dict[str, Any] | None:
        if self._last is None:
            return None
        return {
            "argv": list(self._last.argv),
            "ok": self._last.ok,
            "returncode": self._last.returncode,
            "summary": self._last.summary()[:STATUS_OUTPUT_LIMIT],
        }

    # -- operations -----------------------------------------------------

    async def stop(self) -> CommandResult:
        return await self._exclusive(self._stop)

    async def start(self) -> CommandResult:
        return await self._exclusive(self._start)

    async def restart(self) -> CommandResult:
        return await self._exclusive(self._restart)

    async def _exclusive(self, action: Callable[[], Awaitable[CommandResult]]) -> CommandResult:
        if self._lock.locked():
            return CommandResult(error="another SDR command is already running")
        async with self._lock:
            return await action()

    async def _stop(self) -> CommandResult:
        if not self.can_stop:
            return self._fail(
                "no off_command or power_off_command configured; "
                "piais cannot stop the receiver"
            )
        self._state = SdrState.STOPPING
        self._error = None
        result = CommandResult(returncode=0)

        if self.config.off_command:
            result = await self.runner.run(self.config.off_command, self.config.timeout_s)
            if not result.ok:
                return self._fail("decoder stop failed", result)

        if self.config.power_off_command:
            await self._sleep(self.config.off_delay_s)
            result = await self.runner.run(
                self.config.power_off_command, self.config.timeout_s
            )
            if not result.ok:
                # The decoder is down but the dongle still has power.  That
                # is a real, useful state; report it rather than calling the
                # whole operation unknown.
                self._state = SdrState.STOPPED
                self._last = result
                self._error = f"decoder stopped, but USB power off failed: {result.summary()}"
                log.warning("SDR: %s", self._error)
                return result
            self._powered_off = True

        self._state = SdrState.STOPPED
        self._last = result
        log.info("SDR stopped%s", " (USB power cut)" if self._powered_off else "")
        return result

    async def _start(self) -> CommandResult:
        if self._powered_off and not self.config.power_on_command:
            return self._fail(
                "USB power was cut and no power_on_command is configured; "
                "piais cannot switch the receiver back on"
            )
        if not self._powered_off and not self.config.on_command:
            return self._fail(
                "no on_command configured; piais cannot start the decoder"
            )

        self._state = SdrState.STARTING
        self._error = None

        # Run power_on whenever it is configured, not only after a stop in
        # this process.  A daemon restart loses the flag, and the port may
        # still be off from the previous run.  power_on must be idempotent.
        if self.config.power_on_command:
            result = await self.runner.run(
                self.config.power_on_command, self.config.timeout_s
            )
            if not result.ok:
                return self._fail("USB power on failed", result)
            self._powered_off = False
            await self._sleep(self.config.on_delay_s)

        if not self.config.on_command:
            # Power is back but nothing starts the decoder.  Do not claim it
            # is running.
            self._state = SdrState.UNKNOWN
            self._error = (
                "USB power restored, but no on_command is configured; "
                "the decoder was not started by piais"
            )
            log.warning("SDR: %s", self._error)
            return CommandResult(error=self._error)

        result = await self.runner.run(self.config.on_command, self.config.timeout_s)
        if not result.ok:
            return self._fail("decoder start failed", result)
        self._state = SdrState.RUNNING
        self._last = result
        log.info("SDR started")
        return result

    async def _restart(self) -> CommandResult:
        if self.config.restart_command:
            self._state = SdrState.STARTING
            self._error = None
            result = await self.runner.run(
                self.config.restart_command, self.config.timeout_s
            )
            if not result.ok:
                return self._fail("decoder restart failed", result)
            self._state = SdrState.RUNNING
            self._last = result
            log.info("SDR restarted")
            return result

        stopped = await self._stop()
        if not stopped.ok:
            return stopped
        return await self._start()

    def _fail(self, message: str, result: CommandResult | None = None) -> CommandResult:
        self._state = SdrState.UNKNOWN
        self._error = f"{message}: {result.summary()}" if result is not None else message
        self._last = result
        log.warning("SDR: %s", self._error)
        return result or CommandResult(error=self._error)


# ---------------------------------------------------------------------------
# Wi-Fi watchdog
# ---------------------------------------------------------------------------


class WifiController:
    """Turn the radio off when no client has arrived over Wi-Fi.

    The clock starts at boot, not at the first connection: the requirement is
    "no device connects within a minute", so a Pi that nobody ever reaches
    must still power the radio down.

    Classification is by the *local* address of the HTTP connection, against
    the addresses of interfaces the kernel marks wireless.  A loopback or
    Ethernet request does not extend the window.  If the interfaces cannot be
    determined, any non-loopback request is treated as Wi-Fi: refusing to
    classify is a reason to keep the radio up, not to cut a client off.
    """

    def __init__(
        self,
        config: WifiConfig,
        *,
        runner: CommandRunner | None = None,
        addresses: Callable[[], frozenset[str]] = wifi_addresses,
        state_path: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
        wallclock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self.runner = runner or CommandRunner()
        self._addresses = addresses
        self._state_path = state_path
        self._clock = clock
        self._wallclock = wallclock

        self._armed = bool(config.enabled and config.off_after_s > 0)
        self._last_wifi_activity = clock()
        self._off_since: float | None = None
        self._state = "unknown"
        self._error: str | None = None
        self._last_kind: str | None = None
        self._last_on_at: float | None = None
        self._last_off_at: float | None = None
        self._backoff_until = 0.0
        self._address_cache: tuple[float, frozenset[str]] | None = None

        self._wake = asyncio.Event()
        self._lock = asyncio.Lock()
        self._task: asyncio.Task[None] | None = None

        self.off_count = 0
        self.on_count = 0

    @property
    def can_turn_off(self) -> bool:
        return bool(self.config.off_command)

    @property
    def can_turn_on(self) -> bool:
        return bool(self.config.on_command)

    def status(self) -> dict[str, Any]:
        remaining = self._delay()
        return {
            "enabled": self.config.enabled,
            "state": self._state,
            "armed": self._armed,
            "can_turn_off": self.can_turn_off,
            "can_turn_on": self.can_turn_on,
            "off_after_s": self.config.off_after_s,
            "reopen_interval_s": self.config.reopen_interval_s,
            "seconds_until_next_action": None if remaining is None else round(remaining, 1),
            "last_client": self._last_kind,
            "off_count": self.off_count,
            "on_count": self.on_count,
            "error": self._error,
            "watching": self._task is not None and not self._task.done(),
        }

    # -- lifecycle ------------------------------------------------------

    async def start(self) -> None:
        if not self.config.enabled:
            return
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._watchdog(), name="piais-wifi-watchdog")
        if self.config.off_after_s > 0:
            log.info(
                "Wi-Fi watchdog: off after %.0f s without a Wi-Fi client",
                self.config.off_after_s,
            )

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)

    # -- requests -------------------------------------------------------

    def touch(self, remote: str | None, local: tuple | None) -> str:
        """Record a control-page request and return how it arrived."""
        kind = self._classify(remote, local)
        self._last_kind = kind
        if kind in {"wifi", "unknown"}:
            self._last_wifi_activity = self._clock()
            self._wake.set()
        return kind

    def _classify(self, remote: str | None, local: tuple | None) -> str:
        if not local:
            return "unknown"
        address = str(local[0])
        if is_loopback(address):
            return "loopback"
        try:
            known = self._wifi_addresses()
        except Exception:  # pragma: no cover - defensive
            return "unknown"
        if not known:
            return "unknown"
        return "wifi" if address in known else "other"

    def _wifi_addresses(self) -> frozenset[str]:
        now = self._clock()
        cached = self._address_cache
        if cached is not None and now - cached[0] < 5.0:
            return cached[1]
        value = self._addresses()
        self._address_cache = (now, value)
        return value

    # -- commands -------------------------------------------------------

    async def open_window(self, reason: str = "manual") -> CommandResult:
        """Turn the radio on (if needed) and restart the idle timer.

        This is the single entry point for the web button, a future GPIO
        helper and a future N2K switch-bank listener.
        """
        if not self.config.enabled:
            return self._fail("Wi-Fi control is disabled in the config")
        if self._lock.locked():
            return CommandResult(error="another Wi-Fi command is already running")
        async with self._lock:
            if self._state != "on":
                if not self.config.on_command:
                    return self._fail("Wi-Fi is off and no on_command is configured")
                result = await self.runner.run(
                    self.config.on_command, self.config.timeout_s
                )
                if not result.ok:
                    return self._fail(f"Wi-Fi on failed ({reason})", result)
                self._state = "on"
                self.on_count += 1
                self._last_on_at = self._wallclock()
                log.info("Wi-Fi on (%s)", reason)
            self._armed = True
            self._off_since = None
            self._last_wifi_activity = self._clock()
            self._backoff_until = 0.0
            self._error = None
            self._wake.set()
            self._write_state()
            return CommandResult(returncode=0)

    async def turn_off(self, reason: str = "manual") -> CommandResult:
        if self._lock.locked():
            return CommandResult(error="another Wi-Fi command is already running")
        async with self._lock:
            return await self._turn_off(reason)

    async def _turn_off(self, reason: str) -> CommandResult:
        if not self.config.off_command:
            return self._fail("Wi-Fi off requested but no off_command is configured")
        if self._state == "off":
            self._armed = False
            return CommandResult(returncode=0, output="already off")

        result = await self.runner.run(self.config.off_command, self.config.timeout_s)
        if not result.ok:
            self._state = "unknown"
            self._error = f"Wi-Fi off failed: {result.summary()}"
            self._backoff_until = self._clock() + max(30.0, self.config.off_after_s)
            log.warning("Wi-Fi: %s", self._error)
            return result

        self._state = "off"
        self._armed = False
        self._off_since = self._clock()
        self._last_off_at = self._wallclock()
        self.off_count += 1
        self._error = None
        log.info("Wi-Fi off (%s)", reason)
        self._write_state()
        return result

    def _fail(self, message: str, result: CommandResult | None = None) -> CommandResult:
        self._error = f"{message}: {result.summary()}" if result is not None else message
        log.warning("Wi-Fi: %s", self._error)
        return result or CommandResult(error=self._error)

    # -- watchdog -------------------------------------------------------

    def _delay(self) -> float | None:
        """Seconds until the next automatic action, or None to wait."""
        if not self.config.enabled or self.config.off_after_s <= 0:
            return None
        now = self._clock()
        if self._backoff_until > now:
            return self._backoff_until - now
        if self._armed:
            return max(0.0, self._last_wifi_activity + self.config.off_after_s - now)
        if self.config.reopen_interval_s > 0:
            base = self._off_since if self._off_since is not None else now
            return max(0.0, base + self.config.reopen_interval_s - now)
        return None

    async def _tick(self) -> None:
        # A manual command holds the lock; retry shortly instead of spinning.
        if self._lock.locked():
            self._backoff_until = self._clock() + 1.0
            return
        if self._armed:
            await self.turn_off("idle")
        else:
            await self.open_window("reopen")

    async def _watchdog(self) -> None:
        while True:
            delay = self._delay()
            if delay is None:
                await self._wake.wait()
                self._wake.clear()
                continue
            if delay > 0:
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=delay)
                except TimeoutError:
                    pass
                else:
                    self._wake.clear()
                    continue
            await self._tick()

    # -- state file -----------------------------------------------------

    def _write_state(self) -> None:
        if self._state_path is None:
            return
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._state_path.with_name(self._state_path.name + ".tmp")
            temporary.write_text(
                "state={}\nlast_on={:.0f}\nlast_off={:.0f}\n".format(
                    self._state,
                    self._last_on_at or 0.0,
                    self._last_off_at or 0.0,
                ),
                encoding="ascii",
            )
            os.chmod(temporary, 0o600)
            os.replace(temporary, self._state_path)
        except OSError as exc:
            log.debug("cannot write %s: %s", self._state_path, exc)


# ---------------------------------------------------------------------------
# runtime paths
# ---------------------------------------------------------------------------


def default_runtime_dir() -> Path:
    """Where the token and Wi-Fi state live: tmpfs, never the root filesystem."""
    runtime = os.environ.get("RUNTIME_DIRECTORY")
    if runtime:
        return Path(runtime)
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if xdg:
        return Path(xdg) / "piais"
    return Path("/run/piais")


def default_token_path() -> Path:
    return default_runtime_dir() / "token"


def default_wifi_state_path() -> Path:
    return default_runtime_dir() / "wifi.state"


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


@dataclasses.dataclass(slots=True)
class Request:
    method: str
    target: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]
    body: bytes

    def first(self, name: str) -> str | None:
        values = self.query.get(name)
        return values[0] if values else None


class WebServer:
    """The control page.  One request per connection, no keep-alive."""

    def __init__(
        self,
        config: WebConfig,
        *,
        tokens: TokenStore,
        sdr: SdrController | None = None,
        wifi: WifiController | None = None,
        status: Callable[[], Mapping[str, Any]] | None = None,
        logs: RingLogHandler | None = None,
    ) -> None:
        self.config = config
        self.tokens = tokens
        self.sdr = sdr
        self.wifi = wifi
        self.logs = logs
        self._status = status or (lambda: {"running": False})
        self._server: asyncio.AbstractServer | None = None
        self._bound_port: int | None = None

    @property
    def port(self) -> int:
        return self._bound_port or self.config.port

    def token_url(self, host: str | None = None) -> str:
        shown = host or self.config.host
        if shown in {"0.0.0.0", "::"}:
            shown = socket.gethostname()
        if ":" in shown and not shown.startswith("["):
            shown = f"[{shown}]"
        return f"http://{shown}:{self.port}/?token={self.tokens.token}"

    # -- lifecycle ------------------------------------------------------

    async def start(self) -> None:
        self._server = await asyncio.start_server(
            self._handle, self.config.host, self.config.port
        )
        sockets = self._server.sockets or []
        self._bound_port = sockets[0].getsockname()[1] if sockets else self.config.port
        log.info(
            "control page on http://%s:%d/", self.config.host, self._bound_port
        )
        if is_loopback(self.config.host) and not self.config.password:
            log.info("control page one-time link: %s", self.token_url(self.config.host))
        elif self.config.password:
            log.info(
                "control page requires HTTP basic auth for user %r",
                self.config.username,
            )
        elif not is_loopback(self.config.host):
            # The daemon refuses to reach this branch (see config validation),
            # but never print a token for a network-exposed page if it does.
            log.warning("control page is exposed without a password")

    async def close(self) -> None:
        server, self._server = self._server, None
        if server is not None:
            server.close()
            await server.wait_closed()

    # -- request handling -----------------------------------------------

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            await asyncio.wait_for(self._serve(reader, writer), REQUEST_TIMEOUT_S)
        except TimeoutError:
            await self._try_send(writer, 408, b"request timed out\n")
        except (ConnectionError, asyncio.IncompleteReadError):
            pass
        except Exception:
            log.exception("control page request failed")
            await self._try_send(writer, 500, b"internal error\n")
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except (ConnectionError, OSError):
                pass

    async def _serve(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request = await self._read_request(reader, writer)
        if request is None:
            return

        peer = writer.get_extra_info("peername")
        remote = str(peer[0]) if peer else None
        log.debug("%s %s from %s", request.method, request.path, remote)

        if not self._authorised(request):
            return await self._unauthorised(writer)

        if self.wifi is not None:
            try:
                self.wifi.touch(remote, writer.get_extra_info("sockname"))
            except Exception:  # pragma: no cover - never break a request on this
                log.exception("Wi-Fi activity tracking failed")

        # A valid ?token= becomes a cookie, so it leaves the URL and history.
        raw_token = request.first("token")
        if (
            request.method == "GET"
            and request.path == "/"
            and raw_token
            and self.tokens.accepts(raw_token)
            and not self._cookie(request.headers)
        ):
            remaining = [
                (key, value)
                for key, values in request.query.items()
                if key != "token"
                for value in values
            ]
            location = "/"
            if remaining:
                location = "/?" + urllib.parse.urlencode(remaining)
            return await self._send(
                writer,
                303,
                b"",
                None,
                [
                    ("Location", location),
                    (
                        "Set-Cookie",
                        f"{TOKEN_COOKIE}={raw_token}; Path=/; HttpOnly; SameSite=Strict",
                    ),
                ],
            )

        if request.method == "GET":
            if request.path == "/":
                return await self._page(writer, request)
            if request.path == "/debug":
                return await self._debug_page(writer, request)
            if request.path == "/api/status":
                return await self._json(writer, 200, self.status_document())
            if request.path == "/api/log":
                return await self._api_log(writer, request)
            return await self._not_found(writer)

        if request.method == "POST":
            if request.headers.get("sec-fetch-site") == "cross-site":
                return await self._send(writer, 403, b"cross-site form post refused\n")
            form = parse_form(request.body)
            if request.path == "/api/sdr/stop":
                return await self._sdr_action(writer, request, form, "stop")
            if request.path == "/api/sdr/start":
                return await self._sdr_action(writer, request, form, "start")
            if request.path == "/api/sdr/restart":
                return await self._sdr_action(writer, request, form, "restart")
            if request.path == "/api/wifi/off":
                return await self._wifi_action(writer, request, form, "off")
            if request.path == "/api/wifi/on":
                return await self._wifi_action(writer, request, form, "on")
            if request.path == "/api/token/rotate":
                return await self._rotate(writer, request)
            return await self._not_found(writer)

        await self._send(
            writer,
            405,
            b"method not allowed\n",
            headers=[("Allow", "GET, POST")],
        )

    async def _read_request(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> Request | None:
        line = await reader.readline()
        if not line:
            return None
        if len(line) > MAX_LINE_BYTES:
            await self._send(writer, 414, b"request line too long\n")
            return None
        try:
            method, target, _version = line.decode("latin-1").strip().split(" ")
        except ValueError:
            await self._send(writer, 400, b"malformed request line\n")
            return None
        if not target.startswith("/"):
            await self._send(writer, 400, b"malformed request target\n")
            return None

        headers: dict[str, str] = {}
        size = 0
        while True:
            raw = await reader.readline()
            if not raw or raw in {b"\r\n", b"\n"}:
                break
            size += len(raw)
            if size > MAX_HEADER_BYTES:
                await self._send(writer, 431, b"request headers too large\n")
                return None
            name, separator, value = raw.decode("latin-1").partition(":")
            if not separator:
                await self._send(writer, 400, b"malformed header\n")
                return None
            key = name.strip().lower()
            cleaned = value.strip()
            headers[key] = f"{headers[key]}, {cleaned}" if key in headers else cleaned

        body = b""
        length = headers.get("content-length")
        if length is not None:
            if not length.isdigit():
                await self._send(writer, 400, b"malformed Content-Length\n")
                return None
            if int(length) > MAX_BODY_BYTES:
                await self._send(writer, 413, b"request body too large\n")
                return None
            body = await reader.readexactly(int(length))
        elif "transfer-encoding" in headers:
            await self._send(writer, 411, b"chunked requests are not supported\n")
            return None

        parts = urllib.parse.urlsplit(target)
        try:
            query = urllib.parse.parse_qs(
                parts.query, keep_blank_values=True, max_num_fields=MAX_FORM_FIELDS
            )
        except ValueError:
            await self._send(writer, 400, b"malformed query string\n")
            return None
        return Request(
            method=method,
            target=target,
            path=parts.path,
            query=query,
            headers=headers,
            body=body,
        )

    # -- authorisation --------------------------------------------------

    def _authorised(self, request: Request) -> bool:
        token = request.first("token") or self._cookie(request.headers)
        if token and self.tokens.accepts(token):
            return True
        if self.config.password:
            return parse_basic_auth(
                request.headers.get("authorization"),
                self.config.username,
                self.config.password,
            )
        return False

    @staticmethod
    def _cookie(headers: Mapping[str, str]) -> str | None:
        raw = headers.get("cookie")
        if not raw:
            return None
        for part in raw.split(";"):
            name, _, value = part.strip().partition("=")
            if name == TOKEN_COOKIE:
                return value
        return None

    async def _unauthorised(self, writer: asyncio.StreamWriter) -> None:
        headers = []
        if self.config.password:
            headers.append(("WWW-Authenticate", 'Basic realm="piais", charset="UTF-8"'))
        body = (
            "authentication required\n\n"
            "Open the one-time link printed in the piais boot log, or use the\n"
            "username and password from [web] in piais.toml.\n"
        )
        await self._send(writer, 401, body.encode("utf-8"), headers=headers)

    # -- actions --------------------------------------------------------

    async def _sdr_action(
        self,
        writer: asyncio.StreamWriter,
        request: Request,
        form: dict[str, str],
        action: str,
    ) -> None:
        if self.sdr is None:
            return await self._result(writer, request, form, False, "no SDR controller")
        method = getattr(self.sdr, action)
        result = await method()
        await self._result(
            writer,
            request,
            form,
            result.ok,
            f"SDR {action}: {result.summary() if result.argv or result.error else 'ok'}",
        )

    async def _wifi_action(
        self,
        writer: asyncio.StreamWriter,
        request: Request,
        form: dict[str, str],
        action: str,
    ) -> None:
        if self.wifi is None:
            return await self._result(writer, request, form, False, "no Wi-Fi controller")
        if action == "on":
            result = await self.wifi.open_window("control page")
        else:
            result = await self.wifi.turn_off("control page")
        await self._result(
            writer,
            request,
            form,
            result.ok,
            f"Wi-Fi {action}: {result.summary() if result.argv or result.error else 'ok'}",
        )

    async def _rotate(self, writer: asyncio.StreamWriter, request: Request) -> None:
        token = self.tokens.rotate()
        log.info("control token rotated")
        if self._wants_json(request):
            return await self._json(writer, 200, {"token": token})
        await self._send(
            writer,
            303,
            b"",
            None,
            [
                ("Location", "/?ok=1&msg=" + urllib.parse.quote("token rotated")),
                (
                    "Set-Cookie",
                    f"{TOKEN_COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict",
                ),
            ],
        )

    async def _result(
        self,
        writer: asyncio.StreamWriter,
        request: Request,
        form: dict[str, str],
        ok: bool,
        message: str,
    ) -> None:
        if self._wants_json(request):
            return await self._json(
                writer, 200 if ok else 500, {"ok": ok, "detail": message}
            )
        query = urllib.parse.urlencode(
            {"ok": "1" if ok else "0", "msg": message[:300]}
        )
        await self._send(writer, 303, b"", None, [("Location", "/?" + query)])

    @staticmethod
    def _wants_json(request: Request) -> bool:
        return "application/json" in request.headers.get("accept", "").lower()

    # -- pages ----------------------------------------------------------

    def status_document(self) -> dict[str, Any]:
        try:
            pipeline: Mapping[str, Any] = self._status()
        except Exception as exc:  # pragma: no cover - defensive
            pipeline = {"error": f"{type(exc).__name__}: {exc}"}
        document: dict[str, Any] = {
            "pipeline": pipeline,
            "web": {
                "host": self.config.host,
                "port": self.port,
                "loopback": is_loopback(self.config.host),
            },
        }
        if self.sdr is not None:
            document["sdr"] = self.sdr.status()
        if self.wifi is not None:
            document["wifi"] = self.wifi.status()
        return document

    async def _json(self, writer: asyncio.StreamWriter, status: int, payload: Any) -> None:
        body = json.dumps(payload, indent=2, sort_keys=True).encode("utf-8")
        await self._send(writer, status, body, "application/json; charset=utf-8")

    async def _api_log(self, writer: asyncio.StreamWriter, request: Request) -> None:
        if self.logs is None:
            return await self._send(writer, 404, b"log capture is not enabled\n")
        lines = self.config.log_lines
        requested = request.first("lines")
        if requested and requested.isdigit():
            lines = max(1, min(lines, int(requested)))
        text = self.logs.text(lines)
        await self._send(writer, 200, (text + "\n").encode("utf-8"))

    async def _page(self, writer: asyncio.StreamWriter, request: Request) -> None:
        document = self.status_document()
        body = _render_page(document, request.first("msg"), request.first("ok"))
        await self._send(writer, 200, body.encode("utf-8"), "text/html; charset=utf-8")

    async def _debug_page(self, writer: asyncio.StreamWriter, request: Request) -> None:
        document = self.status_document()
        body = _render_debug(document, self.logs)
        await self._send(writer, 200, body.encode("utf-8"), "text/html; charset=utf-8")

    async def _not_found(self, writer: asyncio.StreamWriter) -> None:
        await self._send(writer, 404, b"not found\n")

    # -- responses ------------------------------------------------------

    async def _send(
        self,
        writer: asyncio.StreamWriter,
        status: int,
        body: bytes = b"",
        content_type: str | None = "text/plain; charset=utf-8",
        headers: Sequence[tuple[str, str]] = (),
    ) -> None:
        reason = HTTPStatus(status).phrase
        lines = [f"HTTP/1.1 {status} {reason}"]
        if content_type:
            lines.append(f"Content-Type: {content_type}")
        lines.append(f"Content-Length: {len(body)}")
        lines.append("Connection: close")
        lines.append("Cache-Control: no-store")
        lines.append("X-Content-Type-Options: nosniff")
        lines.append("Referrer-Policy: no-referrer")
        lines.append(
            "Content-Security-Policy: default-src 'none'; style-src 'unsafe-inline'; "
            "form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
        )
        for name, value in headers:
            lines.append(f"{name}: {value}")
        head = ("\r\n".join(lines) + "\r\n\r\n").encode("latin-1")
        writer.write(head + body)
        await writer.drain()

    async def _try_send(
        self, writer: asyncio.StreamWriter, status: int, body: bytes
    ) -> None:
        try:
            await self._send(writer, status, body)
        except (ConnectionError, OSError, RuntimeError):
            pass


# ---------------------------------------------------------------------------
# HTML
# ---------------------------------------------------------------------------

_STYLE = """
:root { color-scheme: light dark; }
body { font: 16px/1.45 system-ui, sans-serif; max-width: 46rem; margin: 2rem auto;
       padding: 0 1rem; }
h1 { font-size: 1.5rem; margin-bottom: .2rem; }
h2 { font-size: 1.1rem; margin-top: 2rem; border-bottom: 1px solid #8884;
     padding-bottom: .2rem; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: .25rem .5rem; vertical-align: top; }
th { font-weight: 600; color: #888; width: 12rem; }
form { display: inline-block; margin: 0 .4rem .4rem 0; }
button { font: inherit; padding: .45rem .9rem; border-radius: .35rem;
         border: 1px solid #8888; background: #8881; cursor: pointer; }
button:disabled { opacity: .45; cursor: not-allowed; }
.banner { padding: .6rem .8rem; border-radius: .35rem; margin: 1rem 0;
          border: 1px solid; }
.ok { border-color: #2a7; background: #2a72; }
.bad { border-color: #c44; background: #c442; }
.note { color: #888; }
pre { background: #8881; padding: .8rem; overflow-x: auto; font-size: .85rem;
      line-height: 1.35; }
a { color: inherit; }
"""


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _banner(message: str | None, ok: str | None) -> str:
    if not message:
        return ""
    css = "ok" if ok != "0" else "bad"
    return f'<p class="banner {css}">{_e(message[:300])}</p>'


def _row(name: str, value: Any) -> str:
    return f"<tr><th>{_e(name)}</th><td>{_e(value)}</td></tr>"


def _render_page(
    document: Mapping[str, Any], message: str | None, ok: str | None
) -> str:
    pipeline = document.get("pipeline", {})
    sdr = document.get("sdr", {})
    wifi = document.get("wifi", {})
    web = document.get("web", {})

    pipeline_rows = "".join(
        _row(name, value)
        for name, value in (
            ("Mode", pipeline.get("mode", "bridge")),
            ("Uptime", f"{pipeline.get('uptime_s', 0)} s"),
            ("State", pipeline.get("error") or ("running" if pipeline.get("running") else "not running")),
            ("Decoded", pipeline.get("ais", {}).get("decoded", 0)),
            ("Unsupported", pipeline.get("ais", {}).get("unsupported", 0)),
            ("Last AIS input", _age(pipeline.get("ais", {}).get("last_datagram_age_s"))),
        )
    )

    sdr_state = sdr.get("state", "unknown")
    sdr_error = sdr.get("error")
    sdr_note = ""
    if not sdr.get("can_stop"):
        sdr_note = (
            "No stop command is configured. piais can only stop the decoder "
            "process or cut USB power if [sdr] says how."
        )
    elif sdr.get("configured", {}).get("power_off"):
        sdr_note = "USB power control is configured, so stop can cut the dongle."
    else:
        sdr_note = (
            "No USB power switch is configured: stop saves CPU but the dongle "
            "keeps drawing power."
        )

    sdr_buttons = []
    for action, label in (("stop", "Stop"), ("start", "Start"), ("restart", "Restart")):
        allowed = sdr.get("can_stop" if action == "stop" else "can_start", False)
        sdr_buttons.append(
            f'<form method="post" action="/api/sdr/{action}">'
            f'<button type="submit"{" disabled" if not allowed else ""}>{label}</button>'
            "</form>"
        )

    wifi_state = wifi.get("state", "unknown")
    wifi_rows = "".join(
        _row(name, value)
        for name, value in (
            ("State", wifi_state),
            ("Last client", wifi.get("last_client") or "none yet"),
            ("Off after", f"{wifi.get('off_after_s', 0)} s without a Wi-Fi client"),
            ("Next action", _age(wifi.get("seconds_until_next_action"))),
            ("Last error", wifi.get("error") or "none"),
        )
    )
    wifi_buttons = (
        '<form method="post" action="/api/wifi/on">'
        f'<button type="submit"{" disabled" if not wifi.get("can_turn_on") else ""}>'
        "Open Wi-Fi window</button></form>"
        '<form method="post" action="/api/wifi/off">'
        f'<button type="submit"{" disabled" if not wifi.get("can_turn_off") else ""}>'
        "Turn Wi-Fi off</button></form>"
    )

    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>piais</title><style>{_STYLE}</style></head><body>
<h1>piais</h1>
<p class="note">AIS to NMEA 2000. This page does not refresh itself: a polling
page would keep the Wi-Fi watchdog from ever expiring.</p>
{_banner(message, ok)}
<h2>Pipeline</h2>
<table>{pipeline_rows}</table>

<h2>AIS receiver</h2>
<table>
{_row("State", sdr_state)}
{_row("Last error", sdr_error or "none")}
</table>
<p class="note">{_e(sdr_note)}</p>
{''.join(sdr_buttons)}

<h2>Wi-Fi</h2>
<table>{wifi_rows}</table>
{wifi_buttons}

<h2>Control</h2>
<table>
{_row("Listening on", f"{web.get('host')}:{web.get('port')}")}
{_row("Loopback only", "yes" if web.get("loopback") else "no")}
</table>
<p><a href="/debug">Debug log</a> · <a href="/">Refresh</a></p>
</body></html>
"""


def _render_debug(document: Mapping[str, Any], logs: RingLogHandler | None) -> str:
    if logs is None:
        log_text = "(log capture is not enabled)"
    else:
        log_text = logs.text() or "(no log lines yet)"
    status = json.dumps(document, indent=2, sort_keys=True)
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>piais debug</title><style>{_STYLE}</style></head><body>
<h1>piais debug</h1>
<p class="note">This page does not refresh itself, for the same reason the
control page does not.</p>
<h2>Status</h2>
<pre>{_e(status)}</pre>
<h2>Log</h2>
<pre>{_e(log_text)}</pre>
<h2>Token</h2>
<form method="post" action="/api/token/rotate">
<button type="submit">Rotate session token</button>
</form>
<p class="note">The old token stops working after a short grace period.</p>
<p><a href="/">Back to controls</a></p>
</body></html>
"""


def _age(seconds: Any) -> str:
    if seconds is None:
        return "—"
    try:
        value = float(seconds)
    except (TypeError, ValueError):
        return str(seconds)
    if value < 0:
        return "—"
    if value < 60:
        return f"{value:.0f} s"
    if value < 3600:
        return f"{value / 60:.0f} min"
    return f"{value / 3600:.1f} h"
