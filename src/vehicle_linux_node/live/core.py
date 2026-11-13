"""Pure live-uplink logic: topics, payloads, per-signal rate limit, bounded offline queue.

No Kuksa and no MQTT import here; the transport is any object with
``connected`` and ``publish(topic, payload, qos, retain) -> bool`` (see ``Transport``).
Signal names are VSS paths handed in by the caller (taken from the defs, invariant 2);
they are used verbatim as the last topic level.
"""

from __future__ import annotations

import json
import math
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Protocol

QOS = 1


class Transport(Protocol):
    @property
    def connected(self) -> bool: ...

    def publish(self, topic: str, payload: bytes, qos: int, retain: bool) -> bool:
        """Hand one message to the transport; False if it was not accepted."""


def live_topic(device_id: str, signal: str) -> str:
    return f"vehicle/{device_id}/live/{signal}"


def health_topic(device_id: str) -> str:
    return f"vehicle/{device_id}/health"


def state_topic(device_id: str) -> str:
    return f"vehicle/{device_id}/state"


def encode_live(ts_ms: int, value: object) -> bytes:
    return json.dumps({"ts": ts_ms, "value": value}, separators=(",", ":")).encode()


def publishable(value: object) -> bool:
    """JSON scalars only; NaN/inf are not valid JSON and mean 'no value'."""
    if isinstance(value, bool | str):
        return True
    if isinstance(value, int):
        return True
    return isinstance(value, float) and math.isfinite(value)


@dataclass
class Stats:
    published: int = 0
    queued: int = 0
    dropped: int = 0  # oldest message evicted from a full offline queue
    rate_limited: int = 0
    rejected: int = 0  # value not representable in JSON


@dataclass
class LivePublisher:
    device_id: str
    transport: Transport
    rate_hz: float = 10.0
    queue_max: int = 10_000
    stats: Stats = field(default_factory=Stats)

    def __post_init__(self) -> None:
        if self.rate_hz <= 0:
            raise ValueError("rate_hz must be > 0")
        if self.queue_max < 1:
            raise ValueError("queue_max must be >= 1")
        self._min_interval_ms = 1000.0 / self.rate_hz
        self._last_sent: dict[str, int] = {}
        self._state: dict[str, object] = {}
        self._state_dirty = False
        self._queue: deque[tuple[str, bytes]] = deque()

    @property
    def backlog(self) -> int:
        return len(self._queue)

    def ingest(self, updates: Mapping[str, object], now_ms: int) -> None:
        """Take one batch of Kuksa updates. The state map always follows; live is rate-limited."""
        for signal, value in updates.items():
            if not publishable(value):
                self.stats.rejected += 1
                continue
            if self._state.get(signal, _MISSING) != value:
                self._state[signal] = value
                self._state_dirty = True
            last = self._last_sent.get(signal)
            if last is not None and now_ms - last < self._min_interval_ms:
                self.stats.rate_limited += 1
                continue
            self._last_sent[signal] = now_ms
            self._send(live_topic(self.device_id, signal), encode_live(now_ms, value))
        self.flush()

    def publish_health(self, summary: Mapping[str, object]) -> None:
        self._send(
            health_topic(self.device_id), json.dumps(summary, separators=(",", ":")).encode()
        )
        self.flush()

    def flush(self) -> None:
        """Drain the offline queue in order, then the retained state if it changed."""
        while self._queue and self.transport.connected:
            topic, payload = self._queue[0]
            if not self.transport.publish(topic, payload, QOS, False):
                return
            self._queue.popleft()
            self.stats.published += 1
        if self._state_dirty and not self._queue and self.transport.connected:
            payload = json.dumps(self._state, separators=(",", ":"), sort_keys=True).encode()
            if self.transport.publish(state_topic(self.device_id), payload, QOS, True):
                self._state_dirty = False

    def _send(self, topic: str, payload: bytes) -> None:
        if len(self._queue) >= self.queue_max:
            self._queue.popleft()
            self.stats.dropped += 1
        self._queue.append((topic, payload))
        self.stats.queued += 1


_MISSING = object()

Clock = Callable[[], int]
