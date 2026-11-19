"""Pure node-health model: no Kuksa, no clock of its own.

Specs are derived from the defs VSS tree. A node is STALE when its newest leaf value
timestamp is older than ``stale_cycles`` heartbeat periods (both read from the defs:
``interval_ms`` of the ``dbc2vss`` entry and the "stale after N frame cycles" text of
the leaf description) and MISSING when no value was ever seen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from vehicle_linux_node import defs

NODES_ROOT = "Vehicle.Motorcycle.Nodes"
LEAVES = ("Mode", "ErrorCount", "UptimeSeconds")
_STALE_RE = re.compile(r"stale after (\d+) frame cycles")

OK, STALE, MISSING = "OK", "STALE", "MISSING"


@dataclass(frozen=True)
class NodeSpec:
    name: str
    mode_path: str
    errors_path: str
    uptime_path: str
    modes: tuple[str, ...]  # value names from the defs ``allowed`` list
    stale_after_ms: int

    @property
    def paths(self) -> tuple[str, str, str]:
        return (self.mode_path, self.errors_path, self.uptime_path)


def node_specs() -> list[NodeSpec]:
    """One spec per child branch of ``Vehicle.Motorcycle.Nodes`` (defs tree order)."""
    specs = []
    for name in defs.vss_node(NODES_ROOT)["children"]:
        base = f"{NODES_ROOT}.{name}"
        mode, errors, uptime = (defs.vss_node(f"{base}.{leaf}") for leaf in LEAVES)
        match = _STALE_RE.search(mode["description"])
        if match is None:
            raise ValueError(f"{base}.Mode: defs description has no stale rule")
        period = int(mode["dbc2vss"]["interval_ms"])
        specs.append(
            NodeSpec(
                name=name,
                mode_path=f"{base}.Mode",
                errors_path=f"{base}.ErrorCount",
                uptime_path=f"{base}.UptimeSeconds",
                modes=tuple(mode["allowed"]),
                stale_after_ms=int(match.group(1)) * period,
            )
        )
    return specs


def all_paths(specs: list[NodeSpec] | None = None) -> list[str]:
    return [p for s in specs or node_specs() for p in s.paths]


@dataclass(frozen=True)
class Row:
    node: str
    state: str  # OK | STALE | MISSING
    mode: str | None
    errors: int | None
    uptime_s: int | None
    restarted: bool  # uptime went backwards: the node restarted (defs description)
    age_ms: int | None


@dataclass
class _Seen:
    mode: str | None = None
    errors: int | None = None
    uptime: int | None = None
    last_ts_ms: int | None = None
    restarted: bool = False


@dataclass
class NodeHealth:
    specs: list[NodeSpec] = field(default_factory=node_specs)

    def __post_init__(self) -> None:
        self._seen = {s.name: _Seen() for s in self.specs}
        self._owner = {p: s for s in self.specs for p in s.paths}

    def ingest(self, path: str, value: object, ts_ms: int) -> None:
        """Record one leaf value with its value timestamp (epoch ms). Unknown paths are ignored."""
        spec = self._owner.get(path)
        if spec is None or value is None:
            return
        seen = self._seen[spec.name]
        if path == spec.mode_path:
            seen.mode = str(value) if str(value) in spec.modes else None
        elif path == spec.errors_path:
            seen.errors = int(value)  # type: ignore[call-overload]
        else:
            uptime = int(value)  # type: ignore[call-overload]
            if seen.uptime is not None and uptime < seen.uptime:
                seen.restarted = True
            seen.uptime = uptime
        if seen.last_ts_ms is None or ts_ms > seen.last_ts_ms:
            seen.last_ts_ms = ts_ms

    def rows(self, now_ms: int) -> list[Row]:
        out = []
        for spec in self.specs:
            s = self._seen[spec.name]
            if s.last_ts_ms is None:
                out.append(Row(spec.name, MISSING, None, None, None, False, None))
                continue
            age = max(0, now_ms - s.last_ts_ms)
            state = STALE if age > spec.stale_after_ms else OK
            out.append(Row(spec.name, state, s.mode, s.errors, s.uptime, s.restarted, age))
        return out


def render(rows: list[Row]) -> str:
    """Fixed-width text panel; a stale or missing node shows no numbers as current."""
    lines = [f"{'NODE':<10} {'STATE':<8} {'MODE':<11} {'ERRORS':>6} {'UPTIME s':>9}"]
    for r in rows:
        live = r.state == OK
        mode = (r.mode or "?") if live else "-"
        errors = "-" if not live or r.errors is None else str(r.errors)
        uptime = "-" if not live or r.uptime_s is None else str(r.uptime_s)
        flag = " restarted" if live and r.restarted else ""
        lines.append(f"{r.node:<10} {r.state:<8} {mode:<11} {errors:>6} {uptime:>9}{flag}")
    return "\n".join(lines)
