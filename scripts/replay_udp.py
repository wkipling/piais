#!/usr/bin/env python3
"""Replay AIVDM/AIVDO lines over UDP, for bench testing without a radio.

Usage:
    ./scripts/replay_udp.py capture.nmea --rate 10
    ./scripts/replay_udp.py capture.nmea --loop

The file may contain any NMEA sentences; non-AIS lines are skipped.  Lines may
be separated by CRLF or LF.
"""

from __future__ import annotations

import argparse
import socket
import time
from pathlib import Path


def ais_lines(path: Path) -> list[str]:
    lines = []
    for raw in path.read_text(encoding="ascii", errors="ignore").splitlines():
        line = raw.strip()
        if line.startswith(("!AIVDM", "!AIVDO", "$AIVDM", "$AIVDO")):
            lines.append(line)
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("file", type=Path, help="file containing AIVDM sentences")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=10110)
    parser.add_argument(
        "--rate",
        type=float,
        default=10.0,
        help="messages per second; 0 sends as fast as possible",
    )
    parser.add_argument("--loop", action="store_true", help="repeat forever")
    args = parser.parse_args()

    lines = ais_lines(args.file)
    if not lines:
        parser.error(f"{args.file} contains no AIVDM/AIVDO sentences")
    delay = 1.0 / args.rate if args.rate > 0 else 0.0
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    print(f"replaying {len(lines)} sentences to {args.host}:{args.port}")
    try:
        while True:
            for line in lines:
                sock.sendto((line + "\r\n").encode("ascii"), (args.host, args.port))
                if delay:
                    time.sleep(delay)
            if not args.loop:
                break
    except KeyboardInterrupt:
        pass
    finally:
        sock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
