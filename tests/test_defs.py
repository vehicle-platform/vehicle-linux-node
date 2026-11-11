"""The pinned defs files the Kuksa CAN bridge will read are consistent.

These checks run on the consumer side: they fail when a defs bump would
hand kuksa-can-provider a VSS mapping it cannot resolve on the platform bus.
"""

from __future__ import annotations

import pytest

from vehicle_linux_node import defs


@pytest.fixture(scope="module")
def platform_db():
    return defs.load_platform_dbc()


@pytest.fixture(scope="module")
def mappings():
    return defs.dbc2vss_mappings()


def _signals_by_name(db):
    found: dict[str, list] = {}
    for message in db.messages:
        for signal in message.signals:
            found.setdefault(signal.name, []).append((message, signal))
    return found


def test_there_are_mappings(mappings):
    assert mappings, "gen/vss/vss_dbc.json has no dbc2vss entries"


def test_vss_paths_are_unique(mappings):
    paths = [m.vss_path for m in mappings]
    assert len(paths) == len(set(paths))


def test_every_mapped_signal_is_on_the_platform_bus_exactly_once(platform_db, mappings):
    by_name = _signals_by_name(platform_db)
    for m in mappings:
        hits = by_name.get(m.signal, [])
        assert len(hits) == 1, f"{m.vss_path}: signal {m.signal} found {len(hits)}x in platform.dbc"


def test_intervals_are_positive(mappings):
    for m in mappings:
        assert m.interval_ms > 0, m.vss_path


def test_value_maps_use_dbc_value_names(platform_db, mappings):
    by_name = _signals_by_name(platform_db)
    for m in mappings:
        if not m.value_map:
            continue
        (_, signal) = by_name[m.signal][0]
        assert signal.choices, f"{m.vss_path}: transform on {m.signal} without a DBC value table"
        names = {str(v) for v in signal.choices.values()}
        for source, _ in m.value_map:
            assert source in names, f"{m.vss_path}: {source!r} is not a value of {m.signal}"


def test_missing_submodule_gives_a_clear_error(tmp_path):
    with pytest.raises(FileNotFoundError, match="submodule update"):
        defs.dbc2vss_mappings(tmp_path / "vss_dbc.json")
