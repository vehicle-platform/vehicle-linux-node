"""The systemd units wire the services in the right order and call the right entry points."""

from __future__ import annotations

import configparser
from pathlib import Path

import pytest

UNITS = Path(__file__).resolve().parents[1] / "deploy" / "systemd"


def _unit(name: str) -> configparser.ConfigParser:
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str  # type: ignore[assignment]
    parser.read(UNITS / name)
    return parser


@pytest.mark.parametrize("name", [p.name for p in UNITS.glob("*.service")])
def test_every_unit_parses_and_installs(name):
    unit = _unit(name)
    assert unit.has_section("Unit") and unit.has_section("Service")
    assert unit.get("Install", "WantedBy") == "multi-user.target"


def test_vehicle_tap_unit_uses_the_listen_only_role():
    exec_start = _unit("vehicle-can-vehicle-tap@.service").get("Service", "ExecStart")
    assert exec_start.endswith("vehicle-linux-node-can vehicle-tap %i")


def test_platform_unit_uses_the_platform_role():
    exec_start = _unit("vehicle-can-platform@.service").get("Service", "ExecStart")
    assert exec_start.endswith("vehicle-linux-node-can platform %i")


def test_kuksa_waits_for_docker_and_the_platform_bus():
    unit = _unit("vehicle-kuksa.service")
    assert "docker.service" in unit.get("Unit", "Requires")
    assert "vehicle-can-platform@can0.service" in unit.get("Unit", "After")
    assert unit.get("Service", "Environment") == "CAN_PORT=can0"
    assert "docker-compose.replay.yml" not in unit.get("Service", "ExecStart")


def test_blackbox_waits_for_kuksa_and_restarts():
    unit = _unit("vehicle-blackbox.service")
    assert "vehicle-kuksa.service" in unit.get("Unit", "After")
    assert unit.get("Service", "Restart") == "on-failure"
    assert unit.get("Service", "ExecStart").endswith("vehicle-linux-node-blackbox run")


def test_live_unit_is_installed_but_never_enabled_by_the_installer():
    unit = _unit("vehicle-live.service")
    assert unit.get("Service", "ExecStart").endswith("vehicle-linux-node-live")
    assert unit.get("Service", "RestartPreventExitStatus") == "2"
    script = (UNITS.parent / "install.sh").read_text()
    code = "\n".join(ln for ln in script.splitlines() if not ln.lstrip().startswith("#"))
    assert "systemctl enable" not in code
    assert "--extra live" in code


def test_install_script_syncs_the_kuksa_extra():
    script = (UNITS.parent / "install.sh").read_text()
    assert "uv sync --locked --extra kuksa" in script
    assert "git submodule update --init --recursive" in script
