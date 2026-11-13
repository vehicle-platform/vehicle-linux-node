"""``vehicle-linux-node-live``: publish Kuksa signals to the MQTT broker (D-109)."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from vehicle_linux_node.live import mqtt, service

DEFAULT_ADDR = os.environ.get("VEHICLE_KUKSA_ADDR", "127.0.0.1:55555")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vehicle-linux-node-live", description=__doc__)
    parser.add_argument("--address", default=DEFAULT_ADDR, help="databroker host:port")
    parser.add_argument(
        "--rate-hz",
        type=float,
        default=float(os.environ.get("VEHICLE_LIVE_RATE_HZ", "10")),
        help="max publish rate per signal (default 10)",
    )
    parser.add_argument(
        "--queue-max",
        type=int,
        default=int(os.environ.get("VEHICLE_LIVE_QUEUE_MAX", "10000")),
        help="offline queue length; the oldest message is dropped when full",
    )
    parser.add_argument(
        "--health-file",
        type=Path,
        default=Path(os.environ["VEHICLE_LIVE_HEALTH_FILE"])
        if "VEHICLE_LIVE_HEALTH_FILE" in os.environ
        else None,
        help="JSON file with the D-079 window summary; published when it changes",
    )
    parser.add_argument(
        "--include-position",
        action="store_true",
        help="also publish position paths (own server only, D-076)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        cfg = mqtt.config_from_env()
    except mqtt.ConfigError as exc:
        print(f"vehicle-linux-node-live: {exc}", file=sys.stderr)
        return 2
    transport = mqtt.PahoTransport(cfg)
    transport.start()
    try:
        service.run(
            args.address,
            transport,
            cfg.device_id,
            paths=service.signal_paths(args.include_position),
            rate_hz=args.rate_hz,
            queue_max=args.queue_max,
            health_file=args.health_file,
        )
    finally:
        transport.stop()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
