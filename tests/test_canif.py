"""SocketCAN bring-up commands: vehicle tap always listen-only, bitrates from the defs."""

from __future__ import annotations

import pytest

from vehicle_linux_node import canif, defs


def test_vehicle_tap_is_always_listen_only():
    cmds = canif.vehicle_tap_commands("can1", 500000)
    setup = next(c for c in cmds if "type" in c)
    assert setup[setup.index("listen-only") + 1] == "on"
    assert "bitrate" in setup


def test_vehicle_tap_bitrate_comes_from_the_defs(capsys):
    assert canif.main(["vehicle-tap", "can1", "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert f"bitrate {defs.vehicle_bus_bitrate()} listen-only on" in out
    assert out.count("ip link set can1") == 3


def test_platform_needs_a_bitrate_until_the_defs_have_one():
    if defs.platform_bus_bitrate() is None:
        with pytest.raises(SystemExit, match="platform-bitrate"):
            canif.commands_for("platform", "can0")
    cmds = canif.commands_for("platform", "can0", platform_bitrate=500000)
    setup = next(c for c in cmds if "type" in c)
    assert "listen-only" not in setup
    assert "500000" in setup


def test_no_role_allows_dropping_listen_only_on_the_vehicle_bus():
    with pytest.raises(SystemExit):
        canif.main(["vehicle-tap", "can1", "--dry-run", "--no-listen-only"])


def test_unknown_role_rejected():
    with pytest.raises(SystemExit):
        canif.commands_for("tester", "can0")
