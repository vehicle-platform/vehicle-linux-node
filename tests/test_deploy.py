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


CAN_UNITS = ("vehicle-can-platform@.service", "vehicle-can-vehicle-tap@.service")


def test_only_the_can_units_get_net_admin():
    for path in UNITS.glob("*.service"):
        unit = _unit(path.name)
        caps = unit.get("Service", "AmbientCapabilities", fallback="")
        bound = unit.get("Service", "CapabilityBoundingSet", fallback=None)
        if path.name in CAN_UNITS:
            assert caps == "CAP_NET_ADMIN" and bound == "CAP_NET_ADMIN"
        else:
            assert caps == "" and bound != "CAP_NET_ADMIN"


@pytest.mark.parametrize("name", [p.name for p in UNITS.glob("*.service")])
def test_every_unit_is_sandboxed(name):
    svc = _unit(name)["Service"]
    assert svc["NoNewPrivileges"] == "yes"
    assert svc["PrivateTmp"] == "yes"
    if name != "vehicle-kuksa.service":  # docker compose: documented exception in the unit
        assert svc["ProtectSystem"] == "strict"
        # vehicle-llama needs nothing from $HOME at all
        assert svc["ProtectHome"] == ("yes" if name == "vehicle-llama.service" else "read-only")
        assert "AF_CAN" not in svc["RestrictAddressFamilies"]


def test_every_unit_has_an_exposure_threshold_and_the_ratchet_targets_hold():
    """EDGE-1: `scripts/check_systemd_exposure.py` (CI job systemd-exposure) enforces these."""
    import json

    limits = json.loads((UNITS.parent / "exposure-thresholds.json").read_text())
    limits.pop("_comment")
    assert set(limits) == {p.name for p in UNITS.glob("*.service")}
    assert limits["vehicle-blackbox.service"] <= 3.0
    assert limits["vehicle-live.service"] <= 3.0
    for name in CAN_UNITS:
        assert limits[name] <= 5.0


@pytest.mark.parametrize("name", ["vehicle-blackbox.service", "vehicle-live.service", *CAN_UNITS])
def test_sandboxed_units_carry_the_extended_hardening(name):
    svc = _unit(name)["Service"]
    for key, value in {
        "ProtectClock": "yes",
        "ProtectHostname": "yes",
        "ProtectProc": "invisible",
        "ProcSubset": "pid",
        "RestrictNamespaces": "yes",
        "RestrictRealtime": "yes",
        "SystemCallArchitectures": "native",
        "SystemCallFilter": "@system-service",
        "PrivateDevices": "yes",
    }.items():
        assert svc[key] == value, f"{name}: {key}"


def test_llama_unit_is_loopback_only_low_priority_and_capped():
    """D-121: the LLM must not hurt lane keeping (invariant 4) nor leave the Pi."""
    svc = _unit("vehicle-llama.service")["Service"]
    assert "--host 127.0.0.1" in svc["ExecStart"] and "--port 8081" in svc["ExecStart"]
    assert svc["ExecStart"].startswith("/usr/local/bin/llama-server ")
    assert svc["EnvironmentFile"] == "/etc/vehicle/llama.env"
    assert svc["DynamicUser"] == "yes" and "User" not in svc
    assert svc["IPAddressDeny"] == "any" and svc["IPAddressAllow"] == "localhost"
    assert svc["RestrictAddressFamilies"] == "AF_INET AF_INET6 AF_UNIX"
    assert int(svc["CPUQuota"].rstrip("%")) <= 200 and int(svc["Nice"]) >= 10
    assert svc["CPUSchedulingPolicy"] in ("batch", "idle") and svc["IOSchedulingClass"] == "idle"
    assert svc["MemoryMax"] == "3G" and svc["Restart"] == "on-failure"


def test_llama_is_installed_but_never_enabled_and_the_user_is_not_injected():
    script = (UNITS.parent / "install.sh").read_text()
    code = "\n".join(ln for ln in script.splitlines() if not ln.lstrip().startswith("#"))
    assert "systemctl enable" not in code
    assert "--with-llama" in code and "DynamicUser=yes" in code
    assert (UNITS.parent / "llama" / "build-llama.sh").is_file()


def test_exposure_script_does_not_add_user_to_a_dynamic_user_unit():
    import importlib.util

    path = UNITS.parents[1] / "scripts" / "check_systemd_exposure.py"
    spec = importlib.util.spec_from_file_location("check_exposure", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert "User=pi" not in mod.installed_form("[Service]\nDynamicUser=yes\n")
    assert "User=pi" in mod.installed_form("[Service]\nExecStart=/bin/true\n")


def _models() -> list[list[str]]:
    script = (UNITS.parent / "llama" / "fetch-model.sh").read_text()
    body = script.split("<<'TABLE'\n")[1].split("\nTABLE")[0]
    return [row.split("|") for row in body.splitlines()]


def test_every_model_hash_is_pinned_or_an_explicit_verify_placeholder():
    import re

    rows = _models()
    assert {r[0] for r in rows} >= {"qwen2.5-1.5b", "gemma3-1b", "gemma3-4b"}
    for name, repo, rev, file, sha, licence in rows:
        assert re.fullmatch(r"[0-9a-f]{40}", rev), name  # a commit, never "main"
        assert file.endswith("Q4_K_M.gguf") or file.endswith("q4_k_m.gguf"), name
        assert re.fullmatch(r"[0-9a-f]{64}", sha) or sha == "VERIFY", name
        assert repo.count("/") == 1 and licence, name


def test_fetch_model_refuses_unknown_and_unverified_models(tmp_path):
    import shutil
    import subprocess

    sh = shutil.which("sh")
    assert sh
    script = str(UNITS.parent / "llama" / "fetch-model.sh")
    unknown = subprocess.run([sh, script, "nope"], capture_output=True, text=True, check=False)
    assert unknown.returncode == 2
    listing = subprocess.run([sh, script, "--list"], capture_output=True, text=True, check=False)
    assert listing.returncode == 0 and "gemma3-1b" in listing.stdout
    # a VERIFY entry must be refused before any download (copy the script with one swapped)
    text = Path(script).read_text()
    first = _models()[0]
    patched = tmp_path / "fetch.sh"
    patched.write_text(text.replace(first[4], "VERIFY"))
    refused = subprocess.run(
        [sh, str(patched), first[0]],
        capture_output=True,
        text=True,
        check=False,
        env={"PATH": "/usr/bin:/bin", "MODELS_DIR": str(tmp_path / "models")},
    )
    assert refused.returncode == 1 and "not pinned" in refused.stdout
    assert not (tmp_path / "models").exists()
