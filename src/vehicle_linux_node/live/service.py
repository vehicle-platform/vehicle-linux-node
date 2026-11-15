"""Kuksa-fed live uplink: subscribe the databroker over gRPC, publish through ``LivePublisher``.

Never touches CAN. Position paths are excluded unless ``include_position`` is set
(D-076 provisional relaxation: position may go to the user's own server only).
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections.abc import Iterable
from pathlib import Path

from vehicle_linux_node.blackbox.service import default_paths
from vehicle_linux_node.live.core import Clock, LivePublisher, Transport

log = logging.getLogger(__name__)


def signal_paths(include_position: bool = False) -> list[str]:
    """VSS leaves from the defs ``dbc2vss`` mapping; the only source of signal names."""
    paths = default_paths()
    if include_position:
        from vehicle_linux_node import defs

        paths = [m.vss_path for m in defs.dbc2vss_mappings()]
    return sorted(set(paths))


class HealthFile:
    """Publishes the D-079 window-summary JSON dict whenever the file changes."""

    def __init__(self, path: Path | None):
        self.path = path
        self._mtime_ns = 0

    def poll(self) -> dict | None:
        if self.path is None:
            return None
        try:
            mtime = self.path.stat().st_mtime_ns
            if mtime == self._mtime_ns:
                return None
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
        self._mtime_ns = mtime
        return data if isinstance(data, dict) else None


def run(
    address: str,
    transport: Transport,
    device_id: str,
    *,
    paths: Iterable[str] | None = None,
    rate_hz: float = 10.0,
    queue_max: int = 10_000,
    health_file: Path | None = None,
    poll_s: float = 1.0,
    stop: threading.Event | None = None,
    clock: Clock = lambda: int(time.time() * 1000),
) -> LivePublisher:
    """Subscribe to the databroker and publish until ``stop`` is set."""
    from kuksa_client.grpc import VSSClient

    paths = list(paths or signal_paths())
    live = LivePublisher(device_id, transport, rate_hz=rate_hz, queue_max=queue_max)
    health = HealthFile(health_file)
    stop = stop or threading.Event()
    lock = threading.Lock()

    def housekeeping() -> None:
        while not stop.is_set():
            with lock:
                summary = health.poll()
                if summary is not None:
                    live.publish_health(summary)
                live.flush()  # reconnect: drain the backlog even if Kuksa is quiet
            stop.wait(poll_s)

    threading.Thread(target=housekeeping, name="live-housekeeping", daemon=True).start()
    host, port = address.rsplit(":", 1)
    with VSSClient(host, int(port)) as client:
        for updates in client.subscribe_current_values(paths):
            if stop.is_set():
                break
            batch = {
                p: dp.value for p, dp in updates.items() if dp is not None and dp.value is not None
            }
            with lock:
                live.ingest(batch, clock())
    log.info("live stats: %s", live.stats)
    return live
