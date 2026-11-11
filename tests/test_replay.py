"""The synthetic replay covers every dbc2vss mapping and decodes back exactly."""

from __future__ import annotations

import json
import math

import can
import pytest

from vehicle_linux_node import defs, replay


@pytest.fixture(scope="module")
def db():
    return defs.load_platform_dbc()


@pytest.fixture(scope="module")
def mappings():
    return defs.dbc2vss_mappings()


@pytest.fixture(scope="module")
def built(db, mappings):
    return replay.build_replay(db, mappings)


def test_every_mapping_has_an_expected_value(built, mappings):
    assert set(built.expected) == {m.vss_path for m in mappings}


def test_frames_decode_to_the_expected_vss_values(built, db, mappings):
    by_signal = {m.signal: m for m in mappings}
    seen: set[str] = set()
    for frame in built.frames:
        decoded = db.decode_message(frame.arbitration_id, frame.data, decode_choices=True)
        for name, value in decoded.items():
            mapping = by_signal.get(name)
            if mapping is None:
                continue
            if mapping.value_map:
                value = dict(mapping.value_map)[str(value)]
                assert value == built.expected[mapping.vss_path]
            else:
                assert math.isclose(value, built.expected[mapping.vss_path], abs_tol=1e-9)
            seen.add(mapping.vss_path)
    assert seen == set(built.expected)


def test_numeric_values_are_in_range_and_non_zero(built, db, mappings):
    for m in mappings:
        if m.value_map:
            continue
        signal = db.get_message_by_frame_id(_frame_of(db, m.signal)).get_signal_by_name(m.signal)
        value = built.expected[m.vss_path]
        assert value != 0, m.vss_path
        assert signal.minimum <= value <= signal.maximum, m.vss_path


def test_transformed_values_use_a_non_zero_raw_value(built, db, mappings):
    for m in mappings:
        if not m.value_map:
            continue
        signal = db.get_message_by_frame_id(_frame_of(db, m.signal)).get_signal_by_name(m.signal)
        source = m.value_map[1][0]
        raw = next(k for k, v in signal.choices.items() if str(v) == source)
        assert raw != 0, m.vss_path


def test_every_cycle_repeats_each_message(built):
    ids = [f.arbitration_id for f in built.frames]
    for frame_id in set(ids):
        assert ids.count(frame_id) == replay.CYCLES


def test_written_log_reads_back(built, tmp_path):
    log_path, expected_path = replay.write_replay(built, tmp_path)
    read = list(can.LogReader(str(log_path)))
    assert [(m.arbitration_id, bytes(m.data)) for m in read] == [
        (f.arbitration_id, bytes(f.data)) for f in built.frames
    ]
    assert json.loads(expected_path.read_text()) == built.expected


def test_cli_writes_both_files(tmp_path, capsys):
    assert replay.main([str(tmp_path / "out")]) == 0
    assert (tmp_path / "out" / "platform_replay.log").is_file()
    assert (tmp_path / "out" / "expected.json").is_file()


def _frame_of(db, signal_name):
    for message in db.messages:
        if any(s.name == signal_name for s in message.signals):
            return message.frame_id
    raise KeyError(signal_name)
