"""Read access to the pinned vehicle-defs submodule (external/vehicle-defs).

vehicle-defs is the single source of truth for CAN IDs, signal names, scales
and VSS paths (platform rule 2). This module only locates and parses its
files; it never copies them. The Kuksa CAN bridge (kuksa-can-provider) reads
the same two files: ``dbc/platform.dbc`` and the generated
``gen/vss/vss_dbc.json``, whose ``dbc2vss`` entries map a platform-bus signal
to a VSS path.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cantools
from cantools.database.can import Database

DEFS_ROOT = Path(__file__).resolve().parents[2] / "external" / "vehicle-defs"
PLATFORM_DBC = DEFS_ROOT / "dbc" / "platform.dbc"
VSS_DBC_JSON = DEFS_ROOT / "gen" / "vss" / "vss_dbc.json"


@dataclass(frozen=True)
class VssMapping:
    """One ``dbc2vss`` entry: a platform-bus signal feeding a VSS leaf."""

    vss_path: str
    signal: str
    interval_ms: int
    # DBC value-table name -> VSS value, empty when the raw value is used.
    value_map: tuple[tuple[str, Any], ...]


def _require(path: Path) -> Path:
    if not path.is_file():
        raise FileNotFoundError(
            f"{path} is missing. Did you run `git submodule update --init --recursive`?"
        )
    return path


def load_platform_dbc(path: Path = PLATFORM_DBC) -> Database:
    """Parse the platform-bus DBC (strict, as the bridge would)."""
    db = cantools.database.load_file(str(_require(path)), strict=True)
    assert isinstance(db, Database)
    return db


def _walk(children: Mapping[str, Any], prefix: tuple[str, ...]) -> Iterator[VssMapping]:
    for name, node in children.items():
        path = (*prefix, name)
        entry = node.get("dbc2vss")
        if entry is not None:
            mapping = entry.get("transform", {}).get("mapping", [])
            yield VssMapping(
                vss_path=".".join(path),
                signal=entry["signal"],
                interval_ms=int(entry["interval_ms"]),
                value_map=tuple((m["from"], m["to"]) for m in mapping),
            )
        if "children" in node:
            yield from _walk(node["children"], path)


def dbc2vss_mappings(path: Path = VSS_DBC_JSON) -> list[VssMapping]:
    """Every ``dbc2vss`` entry of the generated VSS tree, in tree order."""
    tree = json.loads(_require(path).read_text(encoding="utf-8"))
    return list(_walk(tree, ()))
