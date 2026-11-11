"""Event storage: one directory per event, JSON Lines samples, bounded count."""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

from vehicle_linux_node.blackbox.core import Event

SAMPLES_FILE = "samples.jsonl"
META_FILE = "event.json"


def event_dir_name(event: Event, wall_utc: datetime | None = None) -> str:
    stamp = (wall_utc or datetime.now(UTC)).strftime("%Y%m%dT%H%M%SZ")
    reason = "".join(c if c.isalnum() else "-" for c in event.reasons[0])[:32]
    return f"{stamp}_{event.event_id:04d}_{reason}"


def write_event(
    event: Event, root: Path, *, defs_version: str, wall_utc: datetime | None = None
) -> Path:
    """Write ``event.json`` + ``samples.jsonl`` under ``root``; returns the directory."""
    out = root / event_dir_name(event, wall_utc)
    out.mkdir(parents=True, exist_ok=False)
    with (out / SAMPLES_FILE).open("w", encoding="utf-8") as fh:
        for s in event.samples:
            fh.write(json.dumps({"ts_ms": s.ts_ms, "path": s.path, "value": s.value}) + "\n")
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
    (out / META_FILE).write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")
    return out


def prune(root: Path, max_events: int) -> list[Path]:
    """Delete the oldest event directories beyond ``max_events``; returns what was removed."""
    if not root.is_dir():
        return []
    dirs = sorted(p for p in root.iterdir() if p.is_dir() and (p / META_FILE).is_file())
    removed = dirs[: max(0, len(dirs) - max_events)]
    for p in removed:
        shutil.rmtree(p)
    return removed
