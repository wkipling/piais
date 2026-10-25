# References

## AIS protocol and decoding

- GPSD, *AIVDM/AIVDO protocol decoding*, v1.58 — <https://gpsd.gitlab.io/gpsd/AIVDM.html>
- ITU-R M.1371 (AIS technical characteristics) — <https://www.itu.int/rec/R-REC-M.1371/en>
- `pyais` (MIT) — <https://github.com/M0r13n/pyais>
- `libais` (Apache-2.0) — <https://github.com/schwehr/libais>
- `aiscat` Python binding (GPL-3.0) — <https://pypi.org/project/aiscat/>

## AIS receivers and gateways

- AIS-catcher (GPL-3.0) — <https://github.com/jvde-github/AIS-catcher>
- AIS-catcher docs — <https://jvde-github.github.io/AIS-catcher-docs/>
- AIS-catcher NMEA 2000 output (experimental) — <https://jvde-github.github.io/AIS-catcher-docs/configuration/output/NMEA2000/>
- AIS-catcher RTL-SDR settings — <https://jvde-github.github.io/AIS-catcher-docs/configuration/input/rtlsdr/>
- AIS-catcher low-spec/performance notes — <https://jvde-github.github.io/AIS-catcher-docs/advanced/Low-spec-devices/>
- AIS-catcher sample rates — <https://jvde-github.github.io/AIS-catcher-docs/advanced/samplerate/>
- `rtl_ais` — <https://github.com/dgiardini/rtl-ais>
- dAISy receiver — <https://github.com/astuder/dAISy>
- Maiana open AIS transponder (GPL-3.0) — <https://github.com/peterantypas/maiana>

## NMEA 2000

- CANboat (Apache-2.0) — <https://github.com/canboat/canboat>
- CANboat documentation — <https://canboat.github.io/canboat>
- `nmea2000` Python library (Apache-2.0) — <https://github.com/tomer-w/nmea2000>
- `canboatjs` (Apache-2.0) — <https://github.com/canboat/canboatjs>
- NMEA2000 C++ library, Timo Lappalainen (MIT) — <https://github.com/ttlappalainen/NMEA2000>
- NMEA2000_esp32 backend — <https://github.com/ttlappalainen/NMEA2000_esp32>
- AK-Homberger NMEA2000-AIS-Gateway (GPL-3.0) — <https://github.com/AK-Homberger/NMEA2000-AIS-Gateway>
- `aduvenhage/ais-decoder` — <https://github.com/aduvenhage/ais-decoder>
- NMEA 2000 class/function codes (NMEA PDF) — <https://www.nmea.org/Assets/20120726%20nmea%202000%20class%20%26%20function%20codes%20v%202.00.pdf>

## Signal K

- Signal K — <https://signalk.org/>
- `signalk-to-nmea2000` (Apache-2.0) — <https://github.com/SignalK/signalk-to-nmea2000>

## Hardware

- Raspberry Pi Zero 2 W product page (minimum target) — <https://www.raspberrypi.com/products/raspberry-pi-zero-2-w/>
- Raspberry Pi 4 / 5 product pages (larger boards, extra UARTs/USB) — <https://www.raspberrypi.com/products/>
- PiCAN 2 / PiCAN 3 driver instructions (SK Pang) — <https://www.skpang.co.uk/pages/install>
- PiCAN 2 product information — <https://www.skpang.co.uk/>
- MCP2515 datasheet — Microchip
- NMEA 2000 M12 connector pinout — see SH-ESP32 documentation and NMEA 2000 physical-layer references
- SH-ESP32 open marine hardware — <https://github.com/hatlabs/SH-ESP32>

## VE.Direct and Victron

- `karioja/vedirect` (MIT) — <https://github.com/karioja/vedirect>
- Victron VE.Direct protocol documentation — Victron Energy
- Victron community discussions on VE.Direct checksums and field units
- Victron Marine Integration Guide / NMEA 2000 — <https://www.victronenergy.com/live/ve.can:nmea-2000:start>
- Victron *Datacommunication* whitepaper (authoritative PGN list; not transcribed here)
- Digital Yacht veKonvert (commercial reference, one VE.Direct input) — <https://digitalyacht.support/product/vekonvert-victron-ve-direct-to-nmea-2000-gateway/>
- `victron-ble` (Unlicense) — <https://github.com/keshavdv/victron-ble>
- Victron Instant Readout protocol discussion — <https://community.victronenergy.com/questions/187303/victron-bluetooth-advertising-protocol.html>
- `bleak` BLE client (MIT) — <https://github.com/hbldh/bleak>

## LoRa and BLE

- `beinnlora/AIS-repeater` (GPL-3.0) — <https://github.com/beinnlora/AIS-repeater>
- Semtech LoRa modem datasheets (SX127x / SX126x) — Semtech
- Meshtastic — <https://meshtastic.org/>
- LoRa APRS — <https://github.com/lora-aprs>

## Operating systems

- Raspberry Pi OS overlay file system — `raspi-config` → Performance Options
- Alpine Linux diskless mode — <https://wiki.alpinelinux.org/wiki/Diskless_Mode>
- Alpine Linux Raspberry Pi — <https://wiki.alpinelinux.org/wiki/Raspberry_Pi>
- `watchdog` — <https://linux.die.net/man/8/watchdog>

## Sibling project

- ESP-AIS (the SX127x/N2K hardware receiver) — local sibling repository
