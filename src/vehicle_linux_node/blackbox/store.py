"""Event storage: one directory per event, JSON Lines samples, bounded count."""

from __future__ import annotations

import json
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

from vehicle_linux_node.blackbox.core import Event

SAMPLES_FILE = "samples.jsonl"
META_FILE = "event.json"
TMP_PREFIX = ".tmp-"
STALE_TMP_S = 3600


def private_mkdir(path: Path) -> None:
    """Create ``path`` (and parents) owner-only: vehicle data must not be world-readable."""
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    path.chmod(0o700)


def write_private(path: Path, text: str, *, append: bool = False) -> None:
    """Write ``text`` to a file created with mode 0600 (independent of the umask)."""
    flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if append else os.O_TRUNC)
    fd = os.open(path, flags, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(text)


def event_dir_name(event: Event, wall_utc: datetime | None = None) -> str:
    stamp = (wall_utc or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    reason = "".join(c if c.isalnum() else "-" for c in event.reasons[0])[:32]
    return f"{stamp}_{event.event_id:04d}_{reason}"


def write_event(
    event: Event, root: Path, *, defs_version: str, wall_utc: datetime | None = None
) -> Path:
    """Write ``event.json`` + ``samples.jsonl`` under ``root``; returns the directory."""
    final = root / event_dir_name(event, wall_utc)
    private_mkdir(root)
    # Build in a hidden temp dir and rename, so a crash never leaves a half-written event
    # that looks like a real one; a name clash (restart reset the id) gets a suffix.
    tmp = root / f"{TMP_PREFIX}{final.name}-{os.getpid()}"
    shutil.rmtree(tmp, ignore_errors=True)
    tmp.mkdir(mode=0o700)
    lines = "".join(
        json.dumps({"ts_ms": s.ts_ms, "path": s.path, "value": s.value}) + "\n"
        for s in event.samples
    )
    write_private(tmp / SAMPLES_FILE, lines)
    meta = {
        "event_id": event.event_id,
        "trigger_ms": event.trigger_ms,
        "close_ms": event.close_ms,
        "reasons": event.reasons,
        "pre_samples": len(event.pre),
        "post_samples": len(event.post),
        "paths": sorted({s.path for s in event.samples}),
        "defs_version": defs_version,
        "written_utc": (wall_utc or datetime.now(UTC)).isoformat(),
    }
    write_private(tmp / META_FILE, json.dumps(meta, indent=2) + "\n")
    out, n = final, 0
    while out.exists():
        n += 1
        out = final.with_name(f"{final.name}-{n}")
    tmp.rename(out)
    return out


def prune(root: Path, max_events: int) -> list[Path]:
    """Delete the oldest event directories beyond ``max_events``; returns what was removed."""
    if not root.is_dir():
        return []
    now = time.time()
    for stale in root.glob(f"{TMP_PREFIX}*"):  # crashed mid-write
        if stale.is_dir() and now - stale.stat().st_mtime > STALE_TMP_S:
            shutil.rmtree(stale, ignore_errors=True)
    dirs = sorted(p for p in root.iterdir() if p.is_dir() and (p / META_FILE).is_file())
    removed = dirs[: max(0, len(dirs) - max_events)]
    for p in removed:
        shutil.rmtree(p)
    return removed
