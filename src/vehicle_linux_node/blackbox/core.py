"""Pure black-box logic: a time-bounded ring of samples and trigger windows.

Time is an integer millisecond count supplied by the caller, so the tests
need no clock and the service can feed Kuksa timestamps or a monotonic
clock alike.
"""

from __future__ import annotations

import re
from collections import deque
from dataclasses import dataclass, field
from typing import Any

# Mirrors vehicle-server's position rule (D-076): nothing that names a
# position is ever buffered, whatever the defs map in the future.
_POSITION_RE = re.compile(
    r"(^|\.)(CurrentLocation|Latitude|Longitude|Altitude|Position|GNSS\.Position)(\.|$)|"
    r"(^|[._])(lat|lon|lng|alt)(itude)?([._]|$)",
    re.IGNORECASE,
)


def is_position_path(path: str) -> bool:
    return _POSITION_RE.search(path) is not None


@dataclass(frozen=True)
class Sample:
    ts_ms: int
    path: str
    value: Any


@dataclass
class Event:
    event_id: int
    trigger_ms: int
    reasons: list[str]
    pre: list[Sample]
    post: list[Sample] = field(default_factory=list)
    close_ms: int = 0

    @property
    def samples(self) -> list[Sample]:
        return [*self.pre, *self.post]


class BlackBox:
    """Ring of the last ``pre_ms`` of samples; ``trigger()`` opens an event.

    An event closes ``post_ms`` after its last trigger (a second trigger
    inside the window extends it and adds its reason), capped at
    ``max_post_ms`` after the first trigger so a flapping source cannot hold
    an event open forever.
    """

    def __init__(self, pre_ms: int = 10_000, post_ms: int = 5_000, max_post_ms: int = 30_000):
        if pre_ms <= 0 or post_ms <= 0 or max_post_ms < post_ms:
            raise ValueError("pre_ms and post_ms must be positive, max_post_ms >= post_ms")
        self.pre_ms = pre_ms
        self.post_ms = post_ms
        self.max_post_ms = max_post_ms
        self._ring: deque[Sample] = deque()
        self._open: Event | None = None
        self._next_id = 1
        self.rejected_paths: set[str] = set()

    def add(self, sample: Sample) -> bool:
        """Buffer one sample; a position path is refused and remembered."""
        if is_position_path(sample.path):
            self.rejected_paths.add(sample.path)
            return False
        self._ring.append(sample)
        self._trim(sample.ts_ms)
        if self._open is not None:
            self._open.post.append(sample)
        return True

    def trigger(self, reason: str, now_ms: int) -> Event:
        if self._open is None:
            self._trim(now_ms)
            self._open = Event(
                event_id=self._next_id,
                trigger_ms=now_ms,
                reasons=[reason],
                pre=list(self._ring),
                close_ms=now_ms + self.post_ms,
            )
            self._next_id += 1
        else:
            self._open.reasons.append(reason)
            cap = self._open.trigger_ms + self.max_post_ms
            self._open.close_ms = min(now_ms + self.post_ms, cap)
        return self._open

    def step(self, now_ms: int) -> Event | None:
        """Return the open event once its post window has elapsed."""
        if self._open is not None and now_ms >= self._open.close_ms:
            event, self._open = self._open, None
            return event
        return None

    @property
    def open_event(self) -> Event | None:
        return self._open

    def __len__(self) -> int:
        return len(self._ring)

    def _trim(self, now_ms: int) -> None:
        floor = now_ms - self.pre_ms
        while self._ring and self._ring[0].ts_ms < floor:
            self._ring.popleft()
