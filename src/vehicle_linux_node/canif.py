"""``vehicle-linux-node-can``: bring a SocketCAN interface up the way the platform allows.

Two roles only (ARCHITECTURE §3, D-021):

- ``platform``: the platform bus, where rt-core republishes vehicle signals
  and kuksa-can-provider listens. Normal mode (the OTA client will transmit
  here later); bitrate from the defs.
- ``vehicle-tap``: an optional tap on the CL250 vehicle bus for raw logging.
  **Always** ``listen-only on`` (invariant 1: the Raspi never transmits
  there, never acknowledges); bitrate from ``vehicle_defs.vehicle_cl250``.

Commands are built as argv lists and run with ``ip``; ``--dry-run`` prints
them, which is what the tests check.
"""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys

from vehicle_linux_node import defs

ROLES = ("platform", "vehicle-tap")

# The interface wired to the CL250 vehicle bus. The platform role (normal mode, ACKs
# frames) must never be brought up on it (invariant 1). Override via env or --tap-if.
DEFAULT_TAP_IF = "can1"
TAP_IF_ENV = "VEHICLE_CAN_TAP_IF"
_IFNAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@]{0,14}")  # no leading '-': never an ip option


def validate_ifname(ifname: str) -> str:
    if not _IFNAME.fullmatch(ifname):
        raise SystemExit(f"invalid interface name {ifname!r}")
    return ifname


def tap_interface() -> str:
    return os.environ.get(TAP_IF_ENV) or DEFAULT_TAP_IF


def platform_commands(ifname: str, bitrate: int) -> list[list[str]]:
    return [
        ["ip", "link", "set", ifname, "down"],
        ["ip", "link", "set", ifname, "type", "can", "bitrate", str(bitrate), "restart-ms", "100"],
        ["ip", "link", "set", ifname, "up"],
    ]


def vehicle_tap_commands(ifname: str, bitrate: int) -> list[list[str]]:
    """Listen-only is not optional here; there is no flag to drop it."""
    return [
        ["ip", "link", "set", ifname, "down"],
        [
            "ip",
            "link",
            "set",
            ifname,
            "type",
            "can",
            "bitrate",
            str(bitrate),
            "listen-only",
            "on",
        ],
        ["ip", "link", "set", ifname, "up"],
    ]


def commands_for(
    role: str,
    ifname: str,
    tap_if: str | None = None,
) -> list[list[str]]:
    validate_ifname(ifname)
    tap_if = tap_if or tap_interface()
    if role == "platform" and ifname == tap_if:
        raise SystemExit(
            f"refusing role 'platform' on {ifname}: it is the configured vehicle-tap "
            f"interface ({TAP_IF_ENV}); the platform role is normal mode and would "
            "acknowledge ECU frames (invariant 1)"
        )
    if role == "vehicle-tap":
        return vehicle_tap_commands(ifname, defs.vehicle_bus_bitrate())
    if role == "platform":
        bitrate = defs.platform_bus_bitrate()
        if bitrate is None:
            raise SystemExit("platform.dbc carries no Baudrate attribute (defs >= v0.28.0)")
        return platform_commands(ifname, bitrate)
    raise SystemExit(f"unknown role {role!r}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vehicle-linux-node-can", description=__doc__)
    parser.add_argument("role", choices=ROLES)
    parser.add_argument("ifname", help="SocketCAN interface, e.g. can0")
    parser.add_argument(
        "--tap-if", help=f"interface wired to the vehicle bus (default ${TAP_IF_ENV} or can1)"
    )
    parser.add_argument("--dry-run", action="store_true", help="print the commands, run nothing")
    args = parser.parse_args(argv)

    commands = commands_for(args.role, args.ifname, args.tap_if)
    for cmd in commands:
        print(" ".join(cmd))
        if args.dry_run:
            continue
        if shutil.which("ip") is None:
            print("ip(8) not found; is this a Linux host?", file=sys.stderr)
            return 2
        subprocess.run(cmd, check=True)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
