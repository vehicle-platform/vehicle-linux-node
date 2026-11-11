"""Black-box recorder against the replaying databroker (CI ``kuksa-replay`` job)."""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import pytest

ADDR = os.environ.get("VEHICLE_KUKSA_ADDR")
REPLAY_DIR = os.environ.get("VEHICLE_REPLAY_DIR")

pytestmark = pytest.mark.skipif(
    not (ADDR and REPLAY_DIR), reason="needs a running databroker + replay provider"
)


def test_recorder_writes_an_event_with_pre_and_post_samples(tmp_path: Path):
    from vehicle_linux_node.blackbox import service
    from vehicle_linux_node.blackbox.store import META_FILE

    expected = json.loads((Path(REPLAY_DIR) / "expected.json").read_text())
    stop = threading.Event()
    result: dict = {}

    def worker() -> None:
        result["rec"] = service.run(
            ADDR, tmp_path, pre_ms=3000, post_ms=1000, max_events=5, poll_s=0.1, stop=stop
        )

    thread = threading.Thread(target=worker, daemon=True)
    thread.start()
    time.sleep(3.0)  # fill the ring from the replay
    service.fire_trigger(tmp_path, "integration")
    deadline = time.monotonic() + 20
    written = []
    while time.monotonic() < deadline and not written:
        written = [p for p in tmp_path.iterdir() if p.is_dir() and (p / META_FILE).is_file()]
        time.sleep(0.2)
    stop.set()
    assert written, "no event directory written"
    meta = json.loads((written[0] / META_FILE).read_text())
    assert meta["reasons"] == ["integration"]
    assert meta["pre_samples"] > 0 and meta["post_samples"] > 0
    assert set(meta["paths"]) <= set(expected)
    assert "Vehicle.Speed" in meta["paths"]
