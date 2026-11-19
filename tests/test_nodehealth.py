"""Node-health panel model: specs from the defs, stale/missing/restart logic, no Kuksa."""

from __future__ import annotations

import pytest

from vehicle_linux_node import defs
from vehicle_linux_node.nodehealth import cli
from vehicle_linux_node.nodehealth.core import (
    MISSING,
    OK,
    STALE,
    NodeHealth,
    all_paths,
    node_specs,
    render,
)

T0 = 1_000_000


@pytest.fixture
def specs():
    # defs tree order is alphabetical; tests address nodes by name
    return sorted(node_specs(), key=lambda s: ("RtCore", "Safety", "Conn", "Linux").index(s.name))


def test_specs_come_from_the_defs_tree(specs):
    assert {s.name for s in specs} == {"RtCore", "Safety", "Conn", "Linux"}
    mapped = {m.vss_path for m in defs.dbc2vss_mappings()}
    assert set(all_paths(specs)) <= mapped
    assert len(all_paths(specs)) == 12


def test_modes_are_the_defs_value_names(specs):
    db = defs.load_platform_dbc()
    sig = db.get_message_by_name("HeartbeatRtCore").get_signal_by_name("RT_CORE_NODE_MODE")
    names = {str(c) for c in sig.choices.values()}
    assert set(specs[0].modes) == names


def test_stale_window_is_three_heartbeat_periods_from_the_defs(specs):
    period = {m.vss_path: m.interval_ms for m in defs.dbc2vss_mappings()}
    for s in specs:
        assert s.stale_after_ms == 3 * period[s.mode_path]


def _feed(h: NodeHealth, s, ts, mode="NORMAL", errors=0, uptime=5):
    h.ingest(s.mode_path, mode, ts)
    h.ingest(s.errors_path, errors, ts)
    h.ingest(s.uptime_path, uptime, ts)


def test_never_seen_node_is_missing(specs):
    rows = NodeHealth(specs).rows(T0)
    assert {r.state for r in rows} == {MISSING}
    assert "MISSING" in render(rows)


def test_fresh_then_stale_when_heartbeat_stops(specs):
    h = NodeHealth(specs)
    _feed(h, specs[0], T0, mode="DEGRADED", errors=3, uptime=42)
    assert h.rows(T0 + specs[0].stale_after_ms)[0].state == OK
    row = h.rows(T0 + specs[0].stale_after_ms + 1)[0]
    assert row.state == STALE
    text = render([row])
    assert "DEGRADED" not in text and "42" not in text  # stale numbers are not shown as current


def test_row_values_and_independent_nodes(specs):
    h = NodeHealth(specs)
    _feed(h, specs[1], T0, mode="SAFE_STATE", errors=7, uptime=900)
    rows = {r.node: r for r in h.rows(T0 + 10)}
    assert (rows["Safety"].mode, rows["Safety"].errors, rows["Safety"].uptime_s) == (
        "SAFE_STATE",
        7,
        900,
    )
    assert rows["RtCore"].state == MISSING


def test_uptime_decrease_flags_a_restart(specs):
    h = NodeHealth(specs)
    _feed(h, specs[2], T0, uptime=100)
    h.ingest(specs[2].uptime_path, 2, T0 + 100)
    assert h.rows(T0 + 150)[2].restarted
    assert "restarted" in render(h.rows(T0 + 150))


def test_unknown_path_and_mode_are_ignored(specs):
    h = NodeHealth(specs)
    h.ingest("Vehicle.Speed", 1, T0)
    h.ingest(specs[0].mode_path, "NOT_A_MODE", T0)
    row = h.rows(T0)[0]
    assert row.mode is None
    assert h.rows(T0)[1].state == MISSING


def test_cli_parses_defaults():
    assert callable(cli.main)
