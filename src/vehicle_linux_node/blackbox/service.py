"""Kuksa-fed black-box service.

Subscribes to the mapped VSS leaves (``dbc2vss`` entries of the pinned defs,
position paths excluded) over gRPC, buffers them in ``BlackBox`` and writes
an event when a trigger arrives. Triggers come from a trigger file for now
(``<out_dir>/trigger``, one reason per line, consumed on read) so other local
processes and the CLI can fire one; an rt-core event path on VSS replaces
it once the defs map one. Never touches CAN.
"""

from __future__ import annotations

import logging
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from vehicle_linux_node import defs
from vehicle_linux_node.blackbox.core import BlackBox, Sample, is_position_path
from vehicle_linux_node.blackbox.store import prune, write_event

log = logging.getLogger(__name__)
TRIGGER_FILE = "trigger"


def default_paths() -> list[str]:
    return [m.vss_path for m in defs.dbc2vss_mappings() if not is_position_path(m.vss_path)]


def defs_version() -> str:
    try:
        out = subprocess.run(
            ["git", "-C", str(defs.DEFS_ROOT), "describe", "--tags", "--always"],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
        ).stdout.strip()
        return out or "unknown"
    except (OSError, subprocess.CalledProcessError):
        return "unknown"


def read_triggers(out_dir: Path) -> list[str]:
    """Consume the trigger file; one non-empty line = one reason."""
    path = out_dir / TRIGGER_FILE
    if not path.is_file():
        return []
    try:
        lines = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()]
        path.unlink()
    except OSError:
        return []
    return [ln for ln in lines if ln]


def fire_trigger(out_dir: Path, reason: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / TRIGGER_FILE
    with path.open("a", encoding="utf-8") as fh:
        fh.write(reason.replace("\n", " ") + "\n")
    return path


class Recorder:
    """Drives a ``BlackBox`` from any update source; thread-safe for one producer."""

    def __init__(
        self,
        out_dir: Path,
        *,
        pre_ms: int,
        post_ms: int,
        max_events: int,
        clock_ms: Callable[[], int] = lambda: int(time.monotonic() * 1000),
    ):
        self.out_dir = out_dir
        self.box = BlackBox(pre_ms=pre_ms, post_ms=post_ms)
        self.max_events = max_events
        self.clock_ms = clock_ms
        self._lock = threading.Lock()
        self.written: list[Path] = []
        self._defs_version = defs_version()

    def ingest(self, updates: dict[str, object]) -> None:
        now = self.clock_ms()
        with self._lock:
            for path, value in updates.items():
                self.box.add(Sample(now, path, value))
            self._close_if_due(now)

    def poll(self) -> None:
        """Consume pending triggers and close a due event; called periodically."""
        now = self.clock_ms()
        with self._lock:
            for reason in read_triggers(self.out_dir):
                log.info("trigger: %s", reason)
                self.box.trigger(reason, now)
            self._close_if_due(now)

    def _close_if_due(self, now: int) -> None:
        event = self.box.step(now)
        if event is None:
            return
        written = write_event(event, self.out_dir, defs_version=self._defs_version)
        self.written.append(written)
        prune(self.out_dir, self.max_events)
        log.info("event %d written to %s (%d samples)", event.event_id, written, len(event.samples))


def run(
    address: str,
    out_dir: Path,
    *,
    paths: Iterable[str] | None = None,
    pre_ms: int = 10_000,
    post_ms: int = 5_000,
    max_events: int = 50,
    poll_s: float = 0.2,
    stop: threading.Event | None = None,
) -> Recorder:
    """Subscribe to the databroker and record until ``stop`` is set."""
    from kuksa_client.grpc import VSSClient

    paths = [p for p in (paths or default_paths()) if not is_position_path(p)]
    host, port = address.rsplit(":", 1)
    recorder = Recorder(out_dir, pre_ms=pre_ms, post_ms=post_ms, max_events=max_events)
    stop = stop or threading.Event()

    def poller() -> None:
        while not stop.is_set():
            recorder.poll()
            stop.wait(poll_s)

    threading.Thread(target=poller, name="blackbox-poll", daemon=True).start()
    with VSSClient(host, int(port)) as client:
        for updates in client.subscribe_current_values(paths):
            if stop.is_set():
                break
            recorder.ingest(
                {
                    p: dp.value
                    for p, dp in updates.items()
                    if dp is not None and dp.value is not None
                }
            )
    return recorder
