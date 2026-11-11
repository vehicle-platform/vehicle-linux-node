"""End to end: synthetic platform-bus replay -> kuksa-can-provider -> Kuksa Databroker.

Runs only when ``VEHICLE_KUKSA_ADDR`` (``host:port``) and ``VEHICLE_REPLAY_DIR``
are set, i.e. after ``docker compose -f docker-compose.yml -f
docker-compose.replay.yml up -d`` (the CI ``kuksa-replay`` job). The expected
values come from ``expected.json`` written by ``vehicle-linux-node-replay``.
"""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

import pytest

ADDR = os.environ.get("VEHICLE_KUKSA_ADDR")
REPLAY_DIR = os.environ.get("VEHICLE_REPLAY_DIR")
TIMEOUT_S = float(os.environ.get("VEHICLE_KUKSA_TIMEOUT_S", "90"))

pytestmark = pytest.mark.skipif(
    not (ADDR and REPLAY_DIR), reason="needs a running databroker + replay provider"
)


def _matches(actual, expected) -> bool:
    if isinstance(expected, bool) or isinstance(expected, str):
        return actual == expected
    return actual is not None and math.isclose(actual, expected, rel_tol=1e-5, abs_tol=1e-4)


def test_databroker_holds_every_replayed_value():
    from kuksa_client.grpc import VSSClient

    expected = json.loads((Path(REPLAY_DIR) / "expected.json").read_text())
    host, port = ADDR.rsplit(":", 1)
    deadline = time.monotonic() + TIMEOUT_S
    missing = dict(expected)
    actual: dict = {}
    with VSSClient(host, int(port)) as client:
        while missing and time.monotonic() < deadline:
            current = client.get_current_values(list(expected))
            actual = {path: (dp.value if dp is not None else None) for path, dp in current.items()}
            missing = {p: v for p, v in expected.items() if not _matches(actual.get(p), v)}
            if missing:
                time.sleep(1.0)
    assert not missing, {p: (v, actual.get(p)) for p, v in missing.items()}
