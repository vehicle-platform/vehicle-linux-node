"""Offline LDW pipeline on synthetic frames with a known ground truth."""

from __future__ import annotations

import json

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2", reason="install the `ldw` extra")

from vehicle_linux_node.ldw import cli  # noqa: E402
from vehicle_linux_node.ldw.pipeline import LdwConfig, departure, process_frame  # noqa: E402
from vehicle_linux_node.ldw.synthetic import default_config, render_frame  # noqa: E402

TOLERANCE_M = 0.06


@pytest.fixture(scope="module")
def cfg() -> LdwConfig:
    return default_config()


@pytest.mark.parametrize("offset", [-0.7, -0.3, 0.0, 0.25, 0.6])
def test_recovers_the_lateral_offset(cfg, offset):
    result = process_frame(render_frame(cfg, offset), cfg)
    assert result is not None
    assert result.offset_m == pytest.approx(offset, abs=TOLERANCE_M)
    assert result.lane_width_m == pytest.approx(3.5, abs=0.15)


@pytest.mark.parametrize("lean", [-25.0, -10.0, 12.0, 30.0])
def test_lean_correction_restores_the_offset(cfg, lean):
    frame = render_frame(cfg, 0.3, lean_deg=lean)
    corrected = process_frame(frame, cfg, lean_deg=lean)
    assert corrected is not None
    assert corrected.offset_m == pytest.approx(0.3, abs=TOLERANCE_M)


def test_an_uncorrected_lean_biases_or_loses_the_lane(cfg):
    frame = render_frame(cfg, 0.3, lean_deg=30.0)
    raw = process_frame(frame, cfg)
    assert raw is None or abs(raw.offset_m - 0.3) > TOLERANCE_M


def test_a_curved_lane_still_gives_the_bottom_row_offset(cfg):
    result = process_frame(render_frame(cfg, -0.2, curvature_px=80), cfg)
    assert result is not None
    assert result.offset_m == pytest.approx(-0.2, abs=TOLERANCE_M)


def test_no_lanes_on_a_blank_road(cfg):
    blank = np.full((720, 1280, 3), 70, dtype=np.uint8)
    assert process_frame(blank, cfg) is None
    assert departure(None, cfg) is False


def test_departure_threshold_and_indicator_suppression(cfg):
    inside = process_frame(render_frame(cfg, 0.2), cfg)
    outside = process_frame(render_frame(cfg, 0.7), cfg)
    assert departure(inside, cfg) is False
    assert departure(outside, cfg) is True
    assert departure(outside, cfg, indicator_on=True) is False


def test_cli_synthetic_prints_one_json_line(capsys, tmp_path):
    out = tmp_path / "frame.png"
    assert cli.main(["synthetic", "--offset-m", "0.5", "--save", str(out)]) == 0
    line = json.loads(capsys.readouterr().out.strip())
    assert line["lanes_found"] is True
    assert line["offset_m"] == pytest.approx(0.5, abs=TOLERANCE_M)
    assert line["departure"] is True
    assert out.is_file()
    assert cli.main([str(out), "--indicator-on"]) == 0
    assert json.loads(capsys.readouterr().out.strip())["departure"] is False


def test_cli_rejects_an_unreadable_image(tmp_path):
    bad = tmp_path / "x.png"
    bad.write_bytes(b"not an image")
    assert cli.main([str(bad)]) == 2
