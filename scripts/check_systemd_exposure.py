#!/usr/bin/env python3
"""Fail when a deployed systemd unit is more exposed than its recorded threshold (EDGE-1).

Runs ``systemd-analyze security --offline=true`` on every ``deploy/systemd/*.service``
after the same rewrite ``deploy/install.sh`` applies (``%h`` -> a home, ``User=`` added),
so the score is that of the installed unit. Template units get an instance name.
Thresholds live in ``deploy/exposure-thresholds.json``; a unit without one fails.

    python3 scripts/check_systemd_exposure.py            # needs systemd 252+
    python3 scripts/check_systemd_exposure.py --markdown # table for the CI job summary
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
UNITS = ROOT / "deploy" / "systemd"
THRESHOLDS = ROOT / "deploy" / "exposure-thresholds.json"
SCORE = re.compile(r"Overall exposure level for \S+:\s*([0-9.]+)\s+(\w+)")


def installed_form(text: str) -> str:
    """Mimic deploy/install.sh: substitute %h and add User= under [Service]."""
    text = text.replace("%h", "/home/pi")
    if re.search(r"^DynamicUser=yes$", text, flags=re.M):
        return text  # own unprivileged user: install.sh adds no User=
    return re.sub(r"^\[Service\]$", "[Service]\nUser=pi", text, count=1, flags=re.M)


def analyze(name: str, text: str) -> tuple[float, str, str]:
    """Return (score, rating, raw output) for one unit."""
    instance = name.replace("@.", "@inst.")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / instance
        path.write_text(installed_form(text))
        proc = subprocess.run(
            ["systemd-analyze", "security", "--offline=true", "--no-pager", str(path)],
            capture_output=True,
            text=True,
            check=False,
        )
    out = proc.stdout + proc.stderr
    match = SCORE.search(out)
    if match is None:
        raise SystemExit(f"{name}: no exposure score in systemd-analyze output:\n{out}")
    return float(match.group(1)), match.group(2), out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--markdown", action="store_true", help="print a markdown table")
    parser.add_argument("--verbose", action="store_true", help="print the full per-check output")
    args = parser.parse_args()
    limits = {k: v for k, v in json.loads(THRESHOLDS.read_text()).items() if not k.startswith("_")}
    failed: list[str] = []
    rows: list[tuple[str, float, float, str]] = []
    for unit in sorted(UNITS.glob("*.service")):
        if unit.name not in limits:
            failed.append(f"{unit.name}: no threshold in {THRESHOLDS.name}")
            continue
        score, rating, out = analyze(unit.name, unit.read_text())
        rows.append((unit.name, score, limits[unit.name], rating))
        if args.verbose:
            print(out)
        if score > limits[unit.name]:
            failed.append(f"{unit.name}: exposure {score} {rating} > threshold {limits[unit.name]}")
    for name in limits.keys() - {p.name for p in UNITS.glob("*.service")}:
        failed.append(f"{name}: threshold without a unit file")
    if args.markdown:
        print("| unit | score | max | rating |\n|---|---|---|---|")
    for name, score, limit, rating in rows:
        if args.markdown:
            print(f"| {name} | {score} | {limit} | {rating} |")
        else:
            print(f"{name}: {score} {rating} (max {limit})")
    for line in failed:
        print(f"::error::{line}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
