"""Kuksa-fed node-health panel: subscribe, keep ``NodeHealth``, print the panel."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import datetime

from vehicle_linux_node.nodehealth.core import NodeHealth, all_paths, render


def _ts_ms(ts: datetime | None, fallback_ms: int) -> int:
    return int(ts.timestamp() * 1000) if ts is not None else fallback_ms


def run(
    address: str,
    *,
    refresh_s: float = 1.0,
    out: Callable[[str], None] = print,
    stop: threading.Event | None = None,
) -> NodeHealth:
    """Print the panel every ``refresh_s`` until ``stop`` (staleness by value timestamp)."""
    from kuksa_client.grpc import VSSClient

    health = NodeHealth()
    stop = stop or threading.Event()
    lock = threading.Lock()

    def show() -> None:
        while not stop.wait(refresh_s):
            with lock:
                out(render(health.rows(int(time.time() * 1000))))

    threading.Thread(target=show, name="nodehealth-show", daemon=True).start()
    host, port = address.rsplit(":", 1)
    with VSSClient(host, int(port)) as client:
        for updates in client.subscribe_current_values(all_paths(health.specs)):
            if stop.is_set():
                break
            now = int(time.time() * 1000)
            with lock:
                for path, dp in updates.items():
                    if dp is not None and dp.value is not None:
                        health.ingest(path, dp.value, _ts_ms(dp.timestamp, now))
    return health
