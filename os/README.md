# Operating system

The goal is that a power cut cannot corrupt the SD card. Two recipes achieve
that; they make different trade-offs.

| | Raspberry Pi OS + overlayfs | Alpine diskless |
| --- | --- | --- |
| Root filesystem after boot | read-only SD + copy-on-write RAM overlay | tmpfs, loaded from SD at boot |
| Writes to SD | none (if boot partition is read-only) | none during normal operation; `lbu commit` writes explicitly |
| Config changes | disable overlay, reboot, edit, re-enable | edit, then `lbu commit -d` |
| Init system | systemd | OpenRC |
| libc | glibc | musl |
| AIS-catcher | upstream installer works | must build/package for musl, or run it on another host |
| `nmea2000` wheel | glibc ARM64 | musl ARM64 wheel exists |
| RAM footprint | lower (files paged from SD) | higher (whole root in RAM) |
| Friction | low | medium |
| Recommended for v1 | ✅ | later / purist |

**Neither option protects the running process or the hardware.** A read-only
root means the next boot is clean; it does not mean the current one survives a
brown-out. Use a UPS/supercapacitor and a hardware watchdog.

## Which to start with

Use **Raspberry Pi OS Lite 64-bit + overlayfs** first. It lets you validate
the AIS → N2K path with the least yak-shaving. Move to Alpine diskless once
the hardware works and you want the whole OS in RAM.

If AIS-catcher on Alpine is the blocker, run AIS-catcher on a different Pi or
on the boat's existing computer and point piais at its UDP output. The two
processes do not have to live on the same host.

## Files in this directory

| File | Purpose |
| --- | --- |
| `raspios-overlayfs.md` | Step-by-step for Raspberry Pi OS |
| `alpine-diskless.md` | Step-by-step for Alpine |
| `boot/config.txt` | SPI + MCP2515 overlay fragment |
| `systemd/can0.service` | Bring `can0` up at 250 kbit/s |
| `systemd/piais-aisd.service` | Run the daemon with minimal privileges |

The daemon writes nothing to the persistent filesystem. The control-page
token and the Wi-Fi marker live in `/run/piais` (tmpfs, created by
`RuntimeDirectory=`), and the log ring is memory only. See
[`../docs/CONTROL.md`](../docs/CONTROL.md).

## Watchdog

A read-only root does not recover a hung process. Enable the hardware
watchdog as well:

- Raspberry Pi OS: `apt install watchdog`, configure `/etc/watchdog.conf`,
  `systemctl enable watchdog`.
- Alpine: `apk add watchdog`, configure `/etc/watchdog.conf`, `rc-update add
  watchdog default`.

Set `watchdog-timeout` well above the worst-case AIS-catcher stall (15–30 s)
so a slow decode does not reboot the boat.
