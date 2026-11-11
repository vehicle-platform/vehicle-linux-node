"""``vehicle-linux-node-ldw``: run the offline lane pipeline on an image or video.

Prints one JSON line per frame: offset (m), lane width (m) and the departure
flag. Lean angle and indicator state are command-line inputs for now; the
on-vehicle service will read them from Kuksa.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2

from vehicle_linux_node.ldw.pipeline import LdwConfig, departure, process_frame
from vehicle_linux_node.ldw.synthetic import default_config, render_frame

_IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp"}


def _emit(index: int, result, cfg: LdwConfig, indicator_on: bool) -> None:
    line = {
        "frame": index,
        "lanes_found": result is not None,
        "offset_m": round(result.offset_m, 3) if result else None,
        "lane_width_m": round(result.lane_width_m, 3) if result else None,
        "departure": departure(result, cfg, indicator_on),
    }
    print(json.dumps(line))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="vehicle-linux-node-ldw", description=__doc__)
    parser.add_argument("source", help="image or video file, or 'synthetic'")
    parser.add_argument("--lean-deg", type=float, default=0.0, help="lean angle, positive = right")
    parser.add_argument("--indicator-on", action="store_true", help="suppress the warning")
    parser.add_argument("--offset-m", type=float, default=0.0, help="synthetic: true offset")
    parser.add_argument("--save", type=Path, help="synthetic: also write the rendered frame")
    args = parser.parse_args(argv)
    cfg = default_config()

    if args.source == "synthetic":
        frame = render_frame(cfg, args.offset_m, lean_deg=args.lean_deg)
        if args.save:
            cv2.imwrite(str(args.save), frame)
        _emit(0, process_frame(frame, cfg, args.lean_deg), cfg, args.indicator_on)
        return 0

    path = Path(args.source)
    if path.suffix.lower() in _IMAGE_SUFFIXES:
        frame = cv2.imread(str(path))
        if frame is None:
            print(f"cannot read {path}", file=sys.stderr)
            return 2
        _emit(0, process_frame(frame, cfg, args.lean_deg), cfg, args.indicator_on)
        return 0

    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        print(f"cannot open {path}", file=sys.stderr)
        return 2
    index = 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        _emit(index, process_frame(frame, cfg, args.lean_deg), cfg, args.indicator_on)
        index += 1
    capture.release()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
