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


def test_trigger_appended_during_read_is_not_lost(tmp_path: Path, monkeypatch):
    service.fire_trigger(tmp_path, "first")
    real_read = Path.read_text

    def read_then_race(self, *a, **k):
        text = real_read(self, *a, **k)
        service.fire_trigger(tmp_path, "late")  # arrives between read and unlink
        return text

    monkeypatch.setattr(Path, "read_text", read_then_race)
    assert service.read_triggers(tmp_path) == ["first"]
    monkeypatch.undo()
    assert service.read_triggers(tmp_path) == ["late"]


def test_leftover_processing_file_is_read_after_a_crash(tmp_path: Path):
    (tmp_path / "trigger.processing").write_text("crashed\n")
    assert service.read_triggers(tmp_path) == ["crashed"]


def test_trigger_lines_are_capped(tmp_path: Path):
    for _ in range(service.MAX_TRIGGER_LINES + 20):
        service.fire_trigger(tmp_path, "x" * 1000)
    got = service.read_triggers(tmp_path)
    assert len(got) == service.MAX_TRIGGER_LINES
    assert all(len(r) == service.MAX_REASON_CHARS for r in got)


def test_poll_loop_survives_exceptions_with_bounded_backoff():
    import threading

    stop = threading.Event()
    waits: list[float] = []
    calls = {"n": 0}

    class Flaky:
        def poll(self):
            calls["n"] += 1
            if calls["n"] <= 6:
                raise FileExistsError("boom")
            stop.set()

    real_wait = stop.wait
    stop.wait = lambda t=None: waits.append(t) or real_wait(0)  # type: ignore[method-assign]
    service.poll_loop(Flaky(), stop, 0.2, backoff_max_s=1.0)  # type: ignore[arg-type]
    assert calls["n"] == 7
    assert waits[:6] == [0.4, 0.8, 1.0, 1.0, 1.0, 1.0]
    assert waits[6] == 0.2


def test_write_failure_does_not_kill_the_recorder(tmp_path: Path, monkeypatch):
    now = {"ms": 0}
    rec = service.Recorder(
        tmp_path, pre_ms=500, post_ms=100, max_events=5, clock_ms=lambda: now["ms"]
    )
    rec.ingest({"Vehicle.Speed": 1.0})
    rec.box.trigger("t", 0)

    def boom(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(service, "write_event", boom)
    now["ms"] = 200
    rec.ingest({"Vehicle.Speed": 2.0})  # closes the event, write fails, no exception
    assert rec.written == []


def test_event_name_clash_gets_a_suffix_and_leaves_no_temp_dir(tmp_path: Path):
    box = BlackBox(pre_ms=100, post_ms=10)
    box.add(Sample(0, "Vehicle.Speed", 1.0))
    box.trigger("same", 0)
    ev = box.step(10)
    when = datetime(2026, 1, 1, tzinfo=UTC)
    a = write_event(ev, tmp_path, defs_version="v", wall_utc=when)
    b = write_event(ev, tmp_path, defs_version="v", wall_utc=when)
    assert a != b and (b / META_FILE).is_file()
    assert [p.name for p in tmp_path.iterdir() if p.name.startswith(".tmp-")] == []


def test_blackbox_files_and_dirs_are_owner_only(tmp_path: Path):
    import os
    import stat

    old = os.umask(0)  # hostile umask: modes must still be restrictive
    try:
        root = tmp_path / "bb"
        service.fire_trigger(root, "x")
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
        assert stat.S_IMODE((root / service.TRIGGER_FILE).stat().st_mode) == 0o600
        box = BlackBox(pre_ms=100, post_ms=10)
        box.add(Sample(0, "Vehicle.Speed", 1.0))
        box.trigger("r", 0)
        out = write_event(box.step(10), root, defs_version="v")
        assert stat.S_IMODE(out.stat().st_mode) == 0o700
        for f in out.iterdir():
            assert stat.S_IMODE(f.stat().st_mode) == 0o600
    finally:
        os.umask(old)


@pytest.mark.parametrize(
    "path",
    ["derived.altitude_change", "Vehicle.Altitude.Change", "Vehicle.X.alt_change"],
)
def test_relative_altitude_change_stays_refused_locally(path):
    """Deliberately stricter than the D-076 floor of vehicle-defs."""
    assert is_position_path(path)


@pytest.mark.position_vectors
def test_defs_position_vectors():
    import json

    from vehicle_linux_node.defs import DEFS_ROOT

    vectors = json.loads((DEFS_ROOT / "privacy" / "position_vectors.json").read_text())
    assert vectors["is_position_key"]
    for case in vectors["is_position_key"]:
        if case["expected"]:
            assert is_position_path(case["name"]), case["name"]  # never looser than the floor
