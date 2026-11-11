"""``vehicle-linux-node-blackbox``: run the recorder or fire a trigger."""

from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from vehicle_linux_node.blackbox import service

DEFAULT_DIR = Path(os.environ.get("VEHICLE_BLACKBOX_DIR", "blackbox"))
DEFAULT_ADDR = os.environ.get("VEHICLE_KUKSA_ADDR", "127.0.0.1:55555")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vehicle-linux-node-blackbox", description=__doc__)
    parser.add_argument("--dir", type=Path, default=DEFAULT_DIR, help="event storage directory")
    sub = parser.add_subparsers(dest="cmd", required=True)
    run = sub.add_parser("run", help="subscribe to the databroker and record events")
    run.add_argument("--address", default=DEFAULT_ADDR, help="databroker host:port")
    run.add_argument("--pre-s", type=float, default=10.0)
    run.add_argument("--post-s", type=float, default=5.0)
    run.add_argument("--max-events", type=int, default=50)
    trig = sub.add_parser("trigger", help="fire a trigger for a running recorder")
    trig.add_argument("reason")
    args = parser.parse_args(argv)

    if args.cmd == "trigger":
        print(service.fire_trigger(args.dir, args.reason))
        return 0
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    service.run(
        args.address,
        args.dir,
        pre_ms=int(args.pre_s * 1000),
        post_ms=int(args.post_s * 1000),
        max_events=args.max_events,
    )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
