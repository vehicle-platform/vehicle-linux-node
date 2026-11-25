#!/usr/bin/env python3
"""Headless first-boot setup for a vehicle-platform Raspberry Pi 5 (cloud-init, Pi OS trixie).

Write Raspberry Pi OS Lite 64-bit (trixie) to the SSD/SD card, leave it plugged in so the boot
partition is mounted, then run this on your own machine (stdlib only):

    python3 deploy/pi/setup-bootfs.py                       # macOS: /Volumes/bootfs
    python3 deploy/pi/setup-bootfs.py --boot /media/$USER/bootfs

It asks for the hotspot SSID and password and for the Pi user's password with getpass: secrets
are never taken from argv and never printed; they only land on the boot partition. Written:

  user-data       hostname, user, hashed password (`openssl passwd -6`), NOPASSWD sudo, your SSH
                  public key, SSH enabled
  network-config  netplan v2 on NetworkManager: the hotspot as a Wi-Fi client, regulatory domain
  ssh             empty legacy switch that also enables SSH
  config.txt      only with --usb-max-current: usb_max_current_enable=1. A USB SSD can brown out
                  on a supply that cannot give the Pi 5 its full 5 A; the flag lifts the USB
                  current limit. Use the official 27 W supply instead when you can.
"""

from __future__ import annotations

import argparse
import getpass
import json
import subprocess
import sys
from pathlib import Path

DEFAULT_BOOT = Path("/Volumes/bootfs")
DEFAULT_PUBKEY = Path.home() / ".ssh" / "vehiclepi_ed25519.pub"
USB_LINE = "usb_max_current_enable=1"
MIN_PASSWORD = 8


def q(text: str) -> str:
    """A YAML double-quoted scalar (a JSON string is valid YAML)."""
    return json.dumps(text, ensure_ascii=False)


def build_user_data(*, hostname: str, user: str, pw_hash: str, pubkey: str) -> str:
    """The cloud-init `user-data` document."""
    return f"""#cloud-config
hostname: {hostname}
manage_etc_hosts: true
timezone: Europe/Istanbul
packages:
  - avahi-daemon
users:
  - name: {user}
    groups: users,adm,dialout,audio,netdev,video,plugdev,input,gpio,spi,i2c,render,sudo
    shell: /bin/bash
    lock_passwd: false
    passwd: {q(pw_hash)}
    sudo: ALL=(ALL) NOPASSWD:ALL
    ssh_authorized_keys:
      - {q(pubkey.strip())}
enable_ssh: true
ssh_pwauth: true
runcmd:
  - [systemctl, enable, --now, ssh]
"""


def build_network_config(*, ssid: str, password: str, country: str) -> str:
    """The netplan v2 `network-config` document (NetworkManager renderer)."""
    return f"""network:
  version: 2
  wifis:
    renderer: NetworkManager
    wlan0:
      dhcp4: true
      optional: true
      regulatory-domain: {q(country)}
      access-points:
        {q(ssid)}:
          password: {q(password)}
"""


def with_usb_max_current(config_txt: str) -> str:
    """config.txt with usb_max_current_enable=1 appended once (idempotent)."""
    if USB_LINE in config_txt:
        return config_txt
    return config_txt.rstrip("\n") + f"\n\n[all]\n{USB_LINE}\n"


def hash_password(password: str) -> str:
    """SHA-512 crypt through openssl; the password goes over stdin, never argv."""
    proc = subprocess.run(
        ["openssl", "passwd", "-6", "-stdin"],
        input=password,
        capture_output=True,
        text=True,
        check=True,
    )
    return proc.stdout.strip()


def parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--boot", type=Path, default=DEFAULT_BOOT, help="boot partition mount")
    parser.add_argument("--hostname", default="vehiclepi")
    parser.add_argument("--user", default="vehicle")
    parser.add_argument("--pubkey", type=Path, default=DEFAULT_PUBKEY, help="SSH public key file")
    parser.add_argument("--country", default="TR", help="Wi-Fi regulatory domain (ISO 3166)")
    parser.add_argument(
        "--usb-max-current",
        action="store_true",
        help="add usb_max_current_enable=1 to config.txt (USB SSD on a non-5 A supply)",
    )
    return parser.parse_args(argv)


def ask_secrets(user: str) -> tuple[str, str, str] | None:
    ssid = input("Hotspot name (SSID), exactly as on the phone: ").strip()
    wifi = getpass.getpass("Hotspot password (hidden): ")
    if not ssid or len(wifi) < MIN_PASSWORD:
        print(f"SSID empty or hotspot password shorter than {MIN_PASSWORD} characters.")
        return None
    pw1 = getpass.getpass(f"New password for Pi user '{user}' (hidden): ")
    pw2 = getpass.getpass("Repeat it: ")
    if pw1 != pw2 or len(pw1) < MIN_PASSWORD:
        print(f"Passwords differ or are shorter than {MIN_PASSWORD} characters.")
        return None
    return ssid, wifi, pw1


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    boot: Path = args.boot
    if not (boot / "config.txt").is_file():
        print(f"boot partition not found at {boot}: re-plug the SSD or pass --boot <mount>.")
        return 1
    if not args.pubkey.is_file():
        print(f"missing {args.pubkey}; create it with:")
        print(f"  ssh-keygen -t ed25519 -f {args.pubkey.with_suffix('')} -C vehiclepi")
        return 1
    secrets = ask_secrets(args.user)
    if secrets is None:
        return 1
    ssid, wifi, password = secrets
    (boot / "user-data").write_text(
        build_user_data(
            hostname=args.hostname,
            user=args.user,
            pw_hash=hash_password(password),
            pubkey=args.pubkey.read_text(),
        )
    )
    (boot / "network-config").write_text(
        build_network_config(ssid=ssid, password=wifi, country=args.country)
    )
    (boot / "ssh").write_text("")
    written = "user-data, network-config, ssh"
    if args.usb_max_current:
        cfg = boot / "config.txt"
        cfg.write_text(with_usb_max_current(cfg.read_text()))
        written += ", config.txt"
    print(f"Done: {written} written to {boot}.")
    print("Eject the disk, plug it into the Pi (USB 3 port for an SSD), turn the hotspot on,")
    print(f"power the Pi, then: ssh {args.user}@{args.hostname}.local")
    return 0


if __name__ == "__main__":
    sys.exit(main())
