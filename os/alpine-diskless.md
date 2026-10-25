# Alpine Linux diskless mode

This puts the **entire root filesystem in RAM**. The SD card is read at boot
and written only when you explicitly commit configuration with `lbu`. It is
the closest thing to "the OS runs in RAM".

Costs: OpenRC instead of systemd, musl instead of glibc, and no Debian
packages. Budget an afternoon the first time.

## 1. Image and first boot

1. Download the **Alpine Linux aarch64 Raspberry Pi** image.
2. Write it to the SD card and boot the Pi with a serial console or HDMI
   keyboard.
3. Log in as `root` (no password on a fresh image).

## 2. Diskless install

```sh
setup-alpine
```

Suggested answers:

| Prompt | Answer |
| --- | --- |
| keyboard / hostname | your locale / `piais` |
| network | `wlan0` with `wpa_supplicant`, or a USB Ethernet adapter |
| timezone / proxy / mirror | your choice |
| SSH | `openssh` |
| disk | **`none`** (this is diskless mode) |
| swap | none |

`setup-alpine` with disk `none` leaves the root in tmpfs. Persist changes with:

```sh
lbu commit -d
```

That writes `localhost.apkovl.tar.gz` to the boot media. **A commit writes to
the SD card**, so do it deliberately and not while the supply is marginal.

## 3. Packages

```sh
apk update
apk add python3 py3-pip py3-virtualenv iproute2 can-utils tzdata watchdog
apk add --virtual .build-deps build-base cmake git linux-headers
```

`nmea2000` publishes a musl ARM64 wheel, so it should install without Rust:

```sh
python3 -m venv /opt/piais/venv
/opt/piais/venv/bin/pip install --upgrade pip
/opt/piais/venv/bin/pip install /path/to/piais
```

If pip tries to build `nmea2000` from source, you need a Rust toolchain
(`apk add rust cargo`) — or use a Raspberry Pi OS host instead.

## 4. AIS-catcher

There is no Alpine package. Two options:

**A. Build from source without N2K support** (recommended; piais does the N2K):

```sh
git clone --depth 1 https://github.com/jvde-github/AIS-catcher.git
cd AIS-catcher
mkdir build && cd build
cmake ..
make -j4
install -m755 AIS-catcher /usr/local/bin/
```

Do **not** run `scripts/build-NMEA2000.sh`; that pulls in Timo Lappalainen's
NMEA2000 library, which piais replaces.

**B. Run AIS-catcher on another host** and point piais at its UDP output over
the network. This is often the pragmatic choice: AIS-catcher can run on a Pi
with Raspberry Pi OS or on the boat's existing computer, while the
read-only Alpine box only runs the N2K bridge.

## 5. can0

Append to `/boot/config.txt` (Alpine's boot partition is usually mounted at
`/boot`):

```
dtparam=spi=on
dtoverlay=mcp2515-can0,oscillator=16000000,interrupt=25
```

Reboot, then bring the interface up at 250 kbit/s with a small OpenRC
service (so `piais-aisd` can depend on it):

```sh
cat >/etc/init.d/can0 <<'EOF'
#!/sbin/openrc-run

name="can0"
description="Configure can0 for NMEA 2000 at 250 kbit/s"

start() {
    ebegin "Bringing up can0"
    ip link set can0 up type can bitrate 250000
    eend $?
}

stop() {
    ebegin "Bringing down can0"
    ip link set can0 down
    eend $?
}

depend() {
    after localmount
}
EOF
chmod +x /etc/init.d/can0
rc-update add can0 default
rc-service can0 start
```

## 6. piais service

Create `/etc/init.d/piais-aisd`:

```sh
#!/sbin/openrc-run

name="piais-aisd"
description="AIS to NMEA 2000 bridge"
command="/opt/piais/venv/bin/piais-aisd"
command_args="--config /etc/piais.toml"
command_user="piais:piais"
command_background=true
pidfile="/run/${RC_SVCNAME}.pid"
output_log="/var/log/piais-aisd.log"
error_log="/var/log/piais-aisd.log"

depend() {
    need can0
    after firewall
}
```

```sh
adduser -S -H -h /opt/piais -s /sbin/nologin piais
chown -R piais:piais /opt/piais
chmod +x /etc/init.d/piais-aisd
rc-update add piais-aisd default
rc-service piais-aisd start
```

`/var/log` is in RAM in diskless mode, so the log disappears on reboot. That
is consistent with the design; use `logread` or a remote syslog if you need
history.

Running as a non-root user with SocketCAN: the `can` raw socket needs
`CAP_NET_RAW`. In OpenRC, either run as root or grant the capability to the
Python binary with `setcap cap_net_raw+ep`. The simplest reliable option on
Alpine is to run the service as root and keep the rest of the system locked
down.

## 7. Watchdog

```sh
cat >/etc/watchdog.conf <<'EOF'
watchdog-device = /dev/watchdog
watchdog-timeout = 15
max-load-1 = 24
EOF
rc-update add watchdog default
rc-service watchdog start
```

## 8. Persist and verify

```sh
lbu status
lbu commit -d
reboot
```

After reboot:

```sh
mount | grep ' on / '        # tmpfs
rc-service piais-aisd status
ip -details link show can0
```

Then run the same 50-power-cut test as in `raspios-overlayfs.md`. Remember
that `lbu commit` is the only thing that should ever write to the SD card.

## 9. Updating

```sh
# after installing or editing anything:
lbu commit -d
```

If you break the configuration, boot with the previous `apkovl` or mount the
boot partition elsewhere and remove the overlay tarball.
