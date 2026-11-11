"""The Kuksa bridge configuration stays receive-only and reads the pinned defs."""

from __future__ import annotations

import configparser
from pathlib import Path

import pytest
import yaml

from vehicle_linux_node import defs

ROOT = Path(__file__).resolve().parents[1]
COMPOSE_FILES = ["docker-compose.yml", "docker-compose.replay.yml"]


def _container_to_host(path: str) -> Path:
    """Map a path under the /defs or /vss mounts back to the submodule."""
    if path.startswith("/defs/"):
        return defs.DEFS_ROOT / path.removeprefix("/defs/")
    if path.startswith("/vss/"):
        return defs.DEFS_ROOT / "gen" / "vss" / path.removeprefix("/vss/")
    raise AssertionError(f"{path} is not under a defs mount")


@pytest.fixture(scope="module")
def ini():
    parser = configparser.ConfigParser()
    parser.read(ROOT / "config" / "dbc_feeder.ini")
    return parser


@pytest.fixture(scope="module", params=COMPOSE_FILES)
def compose(request):
    return yaml.safe_load((ROOT / request.param).read_text())


def test_provider_is_can_to_vss_only(ini):
    assert ini.getboolean("general", "dbc2val") is True
    assert ini.getboolean("general", "val2dbc") is False


def test_provider_reads_the_pinned_defs_files(ini):
    assert _container_to_host(ini.get("general", "mapping")) == defs.VSS_DBC_JSON
    assert _container_to_host(ini.get("can", "dbcfile")) == defs.PLATFORM_DBC
    assert defs.VSS_DBC_JSON.is_file() and defs.PLATFORM_DBC.is_file()


def test_provider_commands_disable_val2dbc(compose):
    command = compose["services"]["can-provider"]["command"]
    assert "--no-val2dbc" in command
    assert "--val2dbc" not in command


def test_images_are_pinned(compose):
    for name, service in compose["services"].items():
        image = service.get("image")
        if image is None:
            continue
        tag = image.rsplit(":", 1)[-1]
        assert tag[0].isdigit(), f"{name}: {image} is not pinned to a version"


def test_databroker_loads_the_same_vss_json():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    command = compose["services"]["databroker"]["command"]
    vss = command[command.index("--vss") + 1]
    assert _container_to_host(vss) == defs.VSS_DBC_JSON
    assert command[command.index("--address") + 1] == "127.0.0.1"
