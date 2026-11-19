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


def test_platform_bitrate_comes_from_the_baudrate_attribute():
    assert defs.platform_bus_bitrate() == 500000
    cmds = canif.commands_for("platform", "can0")
    setup = next(c for c in cmds if "type" in c)
    assert "listen-only" not in setup
    assert setup[setup.index("bitrate") + 1] == str(defs.platform_bus_bitrate())


def test_platform_without_a_baudrate_attribute_is_refused(monkeypatch):
    monkeypatch.setattr(defs, "platform_bus_bitrate", lambda db=None: None)
    with pytest.raises(SystemExit, match="Baudrate"):
        canif.commands_for("platform", "can0")


def test_platform_bitrate_cli_override_is_gone():
    with pytest.raises(SystemExit):
        canif.main(["platform", "can0", "--platform-bitrate", "250000", "--dry-run"])


def test_no_role_allows_dropping_listen_only_on_the_vehicle_bus():
    with pytest.raises(SystemExit):
        canif.main(["vehicle-tap", "can1", "--dry-run", "--no-listen-only"])


def test_unknown_role_rejected():
    with pytest.raises(SystemExit):
        canif.commands_for("tester", "can0")


def test_platform_role_is_refused_on_the_vehicle_tap_interface(monkeypatch):
    monkeypatch.delenv("VEHICLE_CAN_TAP_IF", raising=False)
    with pytest.raises(SystemExit, match="vehicle-tap"):
        canif.commands_for("platform", "can1")
    monkeypatch.setenv("VEHICLE_CAN_TAP_IF", "can7")
    with pytest.raises(SystemExit, match="vehicle-tap"):
        canif.commands_for("platform", "can7")
    assert canif.commands_for("platform", "can1")
    assert canif.main(["platform", "can1", "--tap-if", "can0", "--dry-run"]) == 0
    with pytest.raises(SystemExit):
        canif.main(["platform", "can0", "--tap-if", "can0", "--dry-run"])


def test_tap_role_still_works_on_the_tap_interface():
    assert canif.commands_for("vehicle-tap", "can1", tap_if="can1")


@pytest.mark.parametrize("bad", ["-h", "can0;x", "", "a" * 16, "../x"])
def test_bad_interface_names_are_rejected(bad):
    with pytest.raises(SystemExit):
        canif.commands_for("vehicle-tap", bad)
