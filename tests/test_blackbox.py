"""Black-box core, store and trigger file, without Kuksa."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from vehicle_linux_node.blackbox import cli, service
from vehicle_linux_node.blackbox.core import BlackBox, Sample, is_position_path
from vehicle_linux_node.blackbox.store import META_FILE, SAMPLES_FILE, prune, write_event


def _fill(box: BlackBox, path: str, t0: int, t1: int, step: int = 100) -> None:
    for t in range(t0, t1, step):
        box.add(Sample(t, path, t))


def test_ring_keeps_only_the_pre_window():
    box = BlackBox(pre_ms=1000, post_ms=500)
    _fill(box, "Vehicle.Speed", 0, 5000)
    assert len(box) == 11  # samples exactly pre_ms old are kept
    assert min(s.ts_ms for s in box._ring) >= 4900 - 1000


def test_event_holds_pre_and_post_samples_and_closes():
    box = BlackBox(pre_ms=1000, post_ms=500)
    _fill(box, "Vehicle.Speed", 0, 2000)
    event = box.trigger("test", 2000)
    assert [s.ts_ms for s in event.pre] == list(range(1000, 2000, 100))
    assert box.step(2400) is None
    _fill(box, "Vehicle.Speed", 2000, 2500)
    closed = box.step(2500)
    assert closed is event
    assert [s.ts_ms for s in closed.post] == [2000, 2100, 2200, 2300, 2400]
    assert box.open_event is None


def test_second_trigger_extends_and_is_capped():
    box = BlackBox(pre_ms=1000, post_ms=500, max_post_ms=800)
    box.trigger("a", 1000)
    event = box.trigger("b", 1400)
    assert event.reasons == ["a", "b"]
    assert event.close_ms == 1800  # capped at trigger + max_post, not 1900
    assert box.step(1799) is None
    assert box.step(1800) is event


@pytest.mark.parametrize(
    "path",
    [
        "Vehicle.CurrentLocation.Latitude",
        "Vehicle.CurrentLocation.Longitude",
        "Vehicle.Motorcycle.GNSS.Position.Altitude",
        "sensor.gps_lat",
    ],
)
def test_position_paths_are_refused(path):
    assert is_position_path(path)
    box = BlackBox()
    assert box.add(Sample(0, path, 1.0)) is False
    assert len(box) == 0
    assert path in box.rejected_paths


@pytest.mark.parametrize(
    "path",
    ["Vehicle.Speed", "Vehicle.Motorcycle.LeanAngle", "Vehicle.LowVoltageBattery.CurrentVoltage"],
)
def test_signal_paths_are_accepted(path):
    assert not is_position_path(path)


def test_default_paths_are_the_mapped_leaves_without_position():
    paths = service.default_paths()
    assert "Vehicle.Speed" in paths
    assert not any(is_position_path(p) for p in paths)


def test_invalid_windows_rejected():
    with pytest.raises(ValueError):
        BlackBox(pre_ms=0)
    with pytest.raises(ValueError):
        BlackBox(post_ms=600, max_post_ms=500)


def test_write_event_layout(tmp_path: Path):
    box = BlackBox(pre_ms=1000, post_ms=200)
    _fill(box, "Vehicle.Speed", 0, 1000)
    box.trigger("hard brake", 1000)
    box.add(Sample(1100, "Vehicle.Speed", 1))
    closed = box.step(1200)
    out = write_event(
        closed, tmp_path, defs_version="v0.27.0", wall_utc=datetime(2026, 1, 2, tzinfo=UTC)
    )
    assert out.name == "20260102T000000Z_0001_hard-brake"
    lines = (out / SAMPLES_FILE).read_text().splitlines()
    assert len(lines) == len(closed.samples)
    assert json.loads(lines[-1]) == {"ts_ms": 1100, "path": "Vehicle.Speed", "value": 1}
    meta = json.loads((out / META_FILE).read_text())
    assert meta["reasons"] == ["hard brake"]
    assert meta["pre_samples"] == 10 and meta["post_samples"] == 1
    assert meta["defs_version"] == "v0.27.0"


def test_prune_removes_the_oldest(tmp_path: Path):
    for i in range(4):
        box = BlackBox(pre_ms=100, post_ms=100)
        ev = box.trigger("r", 0)
        write_event(
            box.step(100) or ev,
            tmp_path,
            defs_version="x",
            wall_utc=datetime(2026, 1, 1, 0, 0, i, tzinfo=UTC),
        )
    removed = prune(tmp_path, 2)
    assert len(removed) == 2
    assert len([p for p in tmp_path.iterdir() if p.is_dir()]) == 2
    assert prune(tmp_path / "missing", 2) == []


def test_trigger_file_round_trip(tmp_path: Path):
    assert service.read_triggers(tmp_path) == []
    service.fire_trigger(tmp_path, "one")
    service.fire_trigger(tmp_path, "two\nlines")
    assert service.read_triggers(tmp_path) == ["one", "two lines"]
    assert service.read_triggers(tmp_path) == []


def test_recorder_end_to_end_with_a_fake_clock(tmp_path: Path):
    now = {"ms": 0}
    rec = service.Recorder(
        tmp_path, pre_ms=1000, post_ms=300, max_events=5, clock_ms=lambda: now["ms"]
    )
    for t in range(0, 2000, 100):
        now["ms"] = t
        rec.ingest({"Vehicle.Speed": t / 10, "Vehicle.CurrentLocation.Latitude": 1.0})
    service.fire_trigger(tmp_path, "test")
    rec.poll()
    assert rec.box.open_event is not None
    now["ms"] = 2100
    rec.ingest({"Vehicle.Speed": 1.0})
    now["ms"] = 2300
    rec.poll()
    assert len(rec.written) == 1
    meta = json.loads((rec.written[0] / META_FILE).read_text())
    assert meta["paths"] == ["Vehicle.Speed"]
    assert meta["post_samples"] == 1


def test_cli_trigger(tmp_path: Path, capsys):
    assert cli.main(["--dir", str(tmp_path), "trigger", "manual"]) == 0
    assert (tmp_path / service.TRIGGER_FILE).read_text() == "manual\n"
