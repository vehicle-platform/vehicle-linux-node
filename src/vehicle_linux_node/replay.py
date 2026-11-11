"""Synthetic platform-bus replay for the Kuksa CAN bridge.

Builds a candump log (``.log``, the format kuksa-can-provider replays with
``--dumpfile``) plus the VSS values the databroker must then hold. Every
frame, signal name, scale and VSS path comes from the pinned defs
(``platform.dbc`` + ``gen/vss/vss_dbc.json``); the values are synthetic, never
ride data (D-033).

Value rule, per mapped signal:
- numeric: 37 % into the DBC [minimum, maximum] range, quantised to the
  signal's scale, so it is non-zero, in range and exactly representable;
- value table with a ``dbc2vss`` transform: the second mapped entry, so the
  raw value is non-zero and the transform is exercised.
Unmapped signals in the same frame (E2E bytes, validity bits) are sent at
their minimum raw value; the bridge ignores them.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import can
from cantools.database.can import Database, Message, Signal

from vehicle_linux_node import defs

CHANNEL = "vcan0"
CYCLES = 20
CYCLE_S = 0.1
_RANGE_FRACTION = 0.37


@dataclass(frozen=True)
class Replay:
    frames: list[can.Message]
    expected: dict[str, Any]  # VSS path -> value the databroker must hold


def _numeric_value(signal: Signal) -> float:
    low = signal.minimum if signal.minimum is not None else signal.offset
    high = signal.maximum if signal.maximum is not None else low + 100 * signal.scale
    target = low + _RANGE_FRACTION * (high - low)
    raw = round((target - signal.offset) / signal.scale)
    return raw * signal.scale + signal.offset


def _min_raw_value(signal: Signal) -> Any:
    if signal.choices:
        return min(signal.choices)
    return signal.minimum if signal.minimum is not None else signal.offset


def _signal_values(
    message: Message, mapped: dict[str, defs.VssMapping], expected: dict[str, Any]
) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for signal in message.signals:
        mapping = mapped.get(signal.name)
        if mapping is None:
            values[signal.name] = _min_raw_value(signal)
        elif mapping.value_map:
            source, target = mapping.value_map[min(1, len(mapping.value_map) - 1)]
            values[signal.name] = source
            expected[mapping.vss_path] = target
        else:
            phys = _numeric_value(signal)
            values[signal.name] = phys
            expected[mapping.vss_path] = phys
    return values


def build_replay(
    db: Database | None = None, mappings: list[defs.VssMapping] | None = None
) -> Replay:
    db = db if db is not None else defs.load_platform_dbc()
    mappings = mappings if mappings is not None else defs.dbc2vss_mappings()
    mapped = {m.signal: m for m in mappings}
    expected: dict[str, Any] = {}
    payloads: list[tuple[Message, bytes]] = []
    for message in db.messages:
        if not any(s.name in mapped for s in message.signals):
            continue
        values = _signal_values(message, mapped, expected)
        payloads.append((message, message.encode(values, scaling=True, strict=True)))

    frames = [
        can.Message(
            timestamp=cycle * CYCLE_S,
            arbitration_id=message.frame_id,
            is_extended_id=message.is_extended_frame,
            data=data,
            channel=CHANNEL,
        )
        for cycle in range(CYCLES)
        for message, data in payloads
    ]
    return Replay(frames=frames, expected=expected)


def write_replay(replay: Replay, out_dir: Path) -> tuple[Path, Path]:
    """Write ``platform_replay.log`` (candump) and ``expected.json``."""
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / "platform_replay.log"
    with can.CanutilsLogWriter(str(log_path), channel=CHANNEL) as writer:
        for frame in replay.frames:
            writer.on_message_received(frame)
    expected_path = out_dir / "expected.json"
    expected_path.write_text(json.dumps(replay.expected, indent=2, sort_keys=True) + "\n")
    return log_path, expected_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="vehicle-linux-node-replay",
        description="Write a synthetic platform-bus candump log and the expected VSS values.",
    )
    parser.add_argument("out_dir", type=Path)
    args = parser.parse_args(argv)
    log_path, expected_path = write_replay(build_replay(), args.out_dir)
    print(f"{log_path}\n{expected_path}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
