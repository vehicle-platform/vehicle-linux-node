"""The pure text builders of deploy/pi/setup-bootfs.py (dummy values only, no secrets)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import yaml

_SPEC = importlib.util.spec_from_file_location(
    "setup_bootfs", Path(__file__).resolve().parents[1] / "deploy" / "pi" / "setup-bootfs.py"
)
assert _SPEC is not None and _SPEC.loader is not None
mod = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(mod)


def test_user_data_is_valid_cloud_config():
    text = mod.build_user_data(
        hostname="testpi", user="tester", pw_hash="$6$dummy$hash", pubkey="ssh-ed25519 AAAA dummy\n"
    )
    assert text.startswith("#cloud-config\n")
    doc = yaml.safe_load(text)
    assert doc["hostname"] == "testpi"
    (user,) = doc["users"]
    assert user["name"] == "tester"
    assert user["passwd"] == "$6$dummy$hash"
    assert user["sudo"] == "ALL=(ALL) NOPASSWD:ALL"
    assert user["ssh_authorized_keys"] == ["ssh-ed25519 AAAA dummy"]
    assert doc["enable_ssh"] is True and doc["ssh_pwauth"] is True


def test_network_config_quotes_awkward_ssid_and_password():
    text = mod.build_network_config(ssid='My "Phone": #1', password="pa ss:w'rd", country="DE")
    wlan = yaml.safe_load(text)["network"]["wifis"]["wlan0"]
    assert wlan["regulatory-domain"] == "DE"
    assert wlan["access-points"] == {'My "Phone": #1': {"password": "pa ss:w'rd"}}


def test_usb_max_current_is_appended_once():
    base = "dtparam=audio=on\n[cm5]\ndtoverlay=dwc2\n"
    once = mod.with_usb_max_current(base)
    assert once.endswith("[all]\nusb_max_current_enable=1\n")
    assert mod.with_usb_max_current(once) == once


def test_main_requires_pubkey_and_prints_keygen_hint(tmp_path, capsys):
    (tmp_path / "config.txt").write_text("")
    rc = mod.main(["--boot", str(tmp_path), "--pubkey", str(tmp_path / "missing.pub")])
    assert rc == 1
    assert "ssh-keygen -t ed25519" in capsys.readouterr().out


def test_main_rejects_missing_boot(tmp_path, capsys):
    assert mod.main(["--boot", str(tmp_path / "nope")]) == 1
    assert "--boot" in capsys.readouterr().out
