# Raspberry Pi OS Lite + overlayfs

This is the recommended first recipe. The root filesystem becomes copy-on-write
in RAM; the SD card is not written during normal operation. It uses systemd, so
AIS-catcher's upstream installer and the `nmea2000` glibc wheels work.

## 1. Flash and first boot

1. Use Raspberry Pi Imager with **Raspberry Pi OS Lite (64-bit)**.
2. In the Imager settings, set hostname `piais`, a user, SSH, and Wi-Fi.
3. Boot, then:

```bash
sudo apt update
sudo apt full-upgrade -y
sudo reboot
```

## 2. PiCAN 2

Append to `/boot/firmware/config.txt` (or `/boot/config.txt` on older images):

```
dtparam=spi=on
dtoverlay=mcp2515-can0,oscillator=16000000,interrupt=25
```

Reboot, then confirm:

```bash
ip -details link show can0
```

Install the unit files:

```bash
sudo cp os/systemd/can0.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now can0.service
ip -details link show can0     # should say bitrate 250000
```

## 3. AIS-catcher

Use the upstream installer in manual mode so it does not install a web
dashboard you do not need:

```bash
sudo bash -c "$(curl -fsSL https://raw.githubusercontent.com/jvde-github/AIS-catcher/main/scripts/aiscatcher-install) -p"
```

Then configure its UDP output to `127.0.0.1:10110`. Upstream's service
configuration lives under `/etc/AIS-catcher/`; exact file names change between
releases, so use its documented configuration rather than copying a unit from
this repo.

Enable `-F` for the RTL-SDR fixed-point downsampler on a Zero 2 W (the
minimum target); on a larger Pi, measure before enabling it. Measure CPU with
`top`; if it is still too high, use a lower supported sample rate (`-s 768K`,
`-s 384K`).

**Do not enable `-I can0` at the same time as piais.** Two processes writing
AIS PGNs will duplicate targets and fight over address claiming.

## 4. piais

```bash
sudo apt install -y python3-venv python3-pip
sudo useradd --system --home /opt/piais --shell /usr/sbin/nologin piais
sudo mkdir -p /opt/piais
sudo python3 -m venv /opt/piais/venv
sudo /opt/piais/venv/bin/pip install /path/to/piais
sudo cp config/piais.example.toml /etc/piais.toml
sudo cp os/systemd/piais-aisd.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now piais-aisd
journalctl -u piais-aisd -f
```

The unit runs as the `piais` user with only `CAP_NET_RAW`.

## 5. Bound the journal

The overlay lives in RAM. Cap the journal so it cannot fill it:

```bash
sudo mkdir -p /etc/systemd/journald.conf.d
printf '[Journal]\nSystemMaxUse=32M\nRuntimeMaxUse=32M\n' \
  | sudo tee /etc/systemd/journald.conf.d/size.conf
sudo systemctl restart systemd-journald
```

Disable swap:

```bash
sudo systemctl disable --now dphys-swapfile
```

## 6. Enable the overlay

```bash
sudo raspi-config
```

Go to **4 Performance Options → P6 Overlay File System**, enable it, and
answer **Yes** to write-protecting the boot partition. Reboot.

Verify:

```bash
mount | grep ' on / '
# should show overlay
touch /tmp/ok
```

A write anywhere now lands in RAM and disappears on reboot. That is the point.

## 7. Changing the configuration later

Because `/etc` is in the overlay, edits are lost on reboot. To make a
permanent change:

```bash
sudo raspi-config          # disable the overlay
sudo reboot
# edit files, install packages
sudo raspi-config          # re-enable the overlay
sudo reboot
```

Do not leave the overlay disabled in normal operation.

## 8. Watchdog

```bash
sudo apt install -y watchdog
sudo tee /etc/watchdog.conf >/dev/null <<'EOF'
watchdog-device = /dev/watchdog
watchdog-timeout = 15
max-load-1 = 24
EOF
sudo systemctl enable --now watchdog
```

15 s is a starting point. Raise it if a legitimate AIS-catcher decode stall
exceeds it.

## 9. Power-cut test

1. Confirm `mount` shows overlay and `/` has no write access to the SD.
2. Run normal traffic for an hour.
3. Cut power at the wall 50 times, with a few seconds of runtime between cuts.
4. Boot; run `sudo fsck -n /dev/mmcblk0p1` (boot partition) and check the root
   filesystem is clean.
5. Confirm `piais-aisd` and AIS-catcher came back without intervention.

Record the result in `PLAN.md`; do not claim it until it is done.
