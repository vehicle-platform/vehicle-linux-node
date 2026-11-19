"""``vehicle-linux-node-nodehealth``: print the node-health panel from Kuksa."""

from __future__ import annotations

import argparse
import os

from vehicle_linux_node.nodehealth import service

DEFAULT_ADDR = os.environ.get("VEHICLE_KUKSA_ADDR", "127.0.0.1:55555")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vehicle-linux-node-nodehealth", description=__doc__)
    parser.add_argument("--address", default=DEFAULT_ADDR, help="databroker host:port")
    parser.add_argument("--refresh-s", type=float, default=1.0)
    args = parser.parse_args(argv)
    service.run(args.address, refresh_s=args.refresh_s)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
