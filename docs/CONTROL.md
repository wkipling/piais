# Control page, SDR power and Wi-Fi watchdog

The daemon can serve a small control page, switch the AIS receiver off, and
turn the Wi-Fi radio off when nobody is using it. All three are off by
default in `piais.example.toml` except the page, which is enabled but bound to
loopback.

There is no JavaScript, no auto-refresh and no external asset. A polling page
would keep the Wi-Fi watchdog from ever expiring, so the page deliberately
does not poll. Use the **Refresh** link.

## 1. Access

Two mechanisms, chosen by config:

| `[web] host` | Credential |
| --- | --- |
| loopback (`127.0.0.1`, `::1`) | a random token, printed in the boot log |
| anything else | HTTP Basic auth; a password is required or the daemon refuses to start |

The token is 32 hex characters, stored in `/run/piais/token` (mode `0600`,
tmpfs). On boot:

```bash
journalctl -u piais-aisd | grep 'one-time link'
# control page one-time link: http://127.0.0.1:8080/?token=...
```

Open that URL once. The token moves into an `HttpOnly`, `SameSite=Strict`
cookie and the redirect removes it from the address bar and history. The
token is accepted from `?token=` or the cookie.

Rotate from `/debug`, or:

```bash
piais-aisd --rotate-token
# restart the daemon for a running process to use the new token
```

`--rotate-token` rewrites `/run/piais/token`; a daemon already running keeps
the old token in memory until restarted. Rotating from the page updates the
live process and sets the new cookie in the same response, so it cannot lock
you out.

**This is plain HTTP.** Do not expose it to the internet. A `SameSite=Strict`
cookie plus the `Sec-Fetch-Site: cross-site` check on every POST blocks
cross-site form posts, but that is not a substitute for a trusted LAN.

## 2. Modes

```bash
piais-aisd                      # bridge; serves the page if [web] enabled
piais-aisd --web                # force the page on
piais-aisd --open-web           # same, and print the one-time link at startup
piais-aisd --web-only           # page + SDR/Wi-Fi controls, no CAN, no AIS port
piais-aisd --rotate-token       # rotate the token and exit
```

`--web-only` is for debugging when CAN or the UDP port is the problem. It
does not claim a NMEA 2000 address and does not listen for AIS, so it can run
next to the bridge only if it uses a different `web.port`.

The page is started **before** the bus. If address claiming or `can0` fails,
the process stays up with the error on the page instead of exiting. That is
the failure mode you want when the bus is the thing you are debugging.

## 3. SDR control: what the buttons really do

`[sdr]` has two independent layers. Be honest with yourself about which one
you have.

| Layer | Config | What it saves |
| --- | --- | --- |
| decoder stop | `off_command` / `on_command` | CPU and a little memory. The dongle still draws its idle current. |
| USB power cut | `power_off_command` / `power_on_command` | The dongle's current, roughly 1–1.7 W depending on the stick. |

A true power cut needs hardware that supports it:

- a USB hub with per-port switching (`uhubctl`),
- a GPIO-driven load switch or MOSFET on the 5 V feed,
- or a hub whose port power the kernel can actually turn off.

Many hubs advertise ganged power switching and cannot switch a single port.
Check with `uhubctl -l <location> -p <port>` before believing a config file.

Ordering is fixed:

1. `off_command` stops the decoder.
2. `off_delay_s` lets it release the USB device.
3. `power_off_command` cuts the port.

Starting reverses it: `power_on_command`, then `on_delay_s` for enumeration,
then `on_command`.

Failure is reported, never smoothed over:

- no commands configured → the button is disabled and the page says why;
- power was cut but no `power_on_command` → start fails with that reason;
- `off_command` succeeds but `power_off_command` fails → state is `stopped`
  with `decoder stopped, but USB power off failed: ...`;
- `on_command` is empty after restoring power → state is `unknown`; piais did
  not start the decoder and will not pretend it did.

The command output (last 4 KiB) is kept for the page and `/debug`.

## 4. Wi-Fi watchdog

`[wifi] enabled = true` turns the radio off after `off_after_s` with no client
that arrived **over Wi-Fi**.

Rules, exactly:

- The clock starts at boot, not at the first connection. A Pi nobody reaches
  still powers the radio down.
- A request is classified by the **local** address of the HTTP connection
  (`sockname`), compared against interfaces the kernel marks wireless
  (`/sys/class/net/*/wireless`).
- A loopback or Ethernet request does **not** extend the window. Only a
  Wi-Fi client does.
- If the wireless interfaces cannot be determined, a non-loopback request is
  treated as Wi-Fi. Refusing to classify is a reason to keep the radio up,
  not to cut a client off.
- `off_after_s = 0` disables the watchdog entirely.
- `reopen_interval_s > 0` re-enables the radio that often, and waits up to
  `off_after_s` for someone to connect before turning it off again. This is
  the only way back in if the page is the only trigger.
- A failed `off_command` backs off for at least 30 s instead of retrying every
  loop tick.

`nmcli radio wifi off` is the usual command on Raspberry Pi OS. It needs
privilege; see below. The watchdog writes a small marker to
`/run/piais/wifi.state` (tmpfs) for diagnostics only.

**Read-only root caveat.** If the root is an overlayfs and you toggle a bound
network interface, the kernel resets the overlay's "dirty" flag and changes
made after overlayfs was enabled are lost. Either persist elsewhere, or treat
the offline radio mode as "reboot to leave it".

## 5. Privileges

The service runs as `piais`. `systemctl stop`, `nmcli radio` and `uhubctl`
all need root. Pick one:

**Narrow sudo** (preferred). `/etc/sudoers.d/piais`:

```
piais ALL=(root) NOPASSWD: /usr/bin/systemctl stop ais-catcher.service, \
                           /usr/bin/systemctl start ais-catcher.service, \
                           /usr/bin/nmcli radio wifi off, \
                           /usr/bin/nmcli radio wifi on
```

Then put `sudo -n` in the argv arrays:

```toml
[sdr]
off_command = ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", "stop", "ais-catcher.service"]
on_command  = ["/usr/bin/sudo", "-n", "/usr/bin/systemctl", "start", "ais-catcher.service"]
```

**Polkit** for NetworkManager, `/etc/polkit-1/rules.d/50-piais-wifi.rules`:

```js
polkit.addRule(function(action, subject) {
    if (action.id == "org.freedesktop.NetworkManager.network-control" &&
        subject.user == "piais") {
        return polkit.Result.YES;
    }
});
```

That grants full NetworkManager control, not just the radio. Acceptable on a
dedicated appliance, broad on a shared machine.

Running the unit as root also works and is the least code. It is not the
default because a single-purpose daemon does not need it for anything else.

## 6. HTTP API

Everything the page does is a form post; scripts can use JSON.

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/` | status and controls |
| GET | `/debug` | status, log ring, token rotation |
| GET | `/api/status` | full status document |
| GET | `/api/log?lines=N` | log tail as `text/plain` |
| POST | `/api/sdr/{stop,start,restart}` | SDR control |
| POST | `/api/wifi/{on,off}` | Wi-Fi window / cut |
| POST | `/api/token/rotate` | new token |

With `Accept: application/json`, a POST returns `{"ok": bool, "detail": "..."}`
and uses 500 for a failed action. Without it, the browser gets a 303 back to
the page with the message.

```bash
TOKEN=$(cat /run/piais/token)
curl -s -H "Cookie: piais_token=$TOKEN" http://127.0.0.1:8080/api/status
curl -s -X POST -H "Cookie: piais_token=$TOKEN" \
     -H 'Accept: application/json' \
     http://127.0.0.1:8080/api/sdr/stop
```

`/api/status` polling counts as activity if it arrives over Wi-Fi. That is
intended: something is using the link. A monitor on loopback does not.

## 7. Turning Wi-Fi on from the plotter (future)

NMEA 2000 has a standard switch-bank pair:

| PGN | Direction | Use |
| --- | --- | --- |
| 127501 `binarySwitchBankStatus` | piais → bus | show the window state |
| 127502 `switchBankControl` | plotter → piais | open the window |

Both exist in canboat 9.0.0 and the installed `nmea2000` library can encode and
decode them. Each is an `instance` plus 28 two-bit LOOKUP fields, so all 28
must be supplied in order — the same positional rule as every other PGN here.

Whether a Garmin ECHOMAP renders a **generic third-party** bank is unverified.
Garmin's documented switching integrations are with named partners, and its
route for third-party UI (OneHelm) is a licensed programme. Test on the bench
before designing around it:

```bash
nmea2000-cli can -i socketcan -c can0 --json
# then press the plotter's switching/breaker UI and see whether 127502 appears
```

127502 is unauthenticated broadcast. On a live bus, an instance that collides
with a real switch bank will operate it. Bench first, use an unused instance,
and remember that anything on the bus can open the window (the page still
requires its password).

The receiver side is a contained addition: `N2KDevice.set_receive_callback`
exists, and the listener must ignore its own source address.
`WifiController.open_window()` is already the single entry point a GPIO button
or an N2K listener would call.
