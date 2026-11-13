#!/usr/bin/env sh
# Install vehicle-linux-node on a Raspberry Pi OS host (user-level checkout in $HOME).
#
#   git clone --recursive https://github.com/vehicle-platform/vehicle-linux-node ~/vehicle-linux-node
#   cd ~/vehicle-linux-node && sh deploy/install.sh
#
# Then: sudo systemctl enable --now vehicle-can-platform@can0 vehicle-kuksa vehicle-blackbox
# Live uplink (D-109, off by default): configure ~/.config/vehicle-live.env, then
#   sudo systemctl enable --now vehicle-live
# Optional vehicle tap:  sudo systemctl enable --now vehicle-can-vehicle-tap@can1
set -eu
here=$(cd "$(dirname "$0")/.." && pwd)
command -v uv >/dev/null 2>&1 || { echo "install uv first: https://docs.astral.sh/uv/"; exit 1; }
command -v docker >/dev/null 2>&1 || { echo "install docker + compose plugin first"; exit 1; }
cd "$here"
git submodule update --init --recursive
uv sync --locked --extra kuksa --extra live
mkdir -p "$HOME/vehicle-data/blackbox"
# %h in the units resolves to the invoking user's home only for user units; for system
# units we substitute it here so the services run as this user from this checkout.
tmp=$(mktemp -d)
for unit in deploy/systemd/*.service; do
  sed -e "s|%h|$HOME|g" -e "/^\[Service\]/a User=$(id -un)" "$unit" > "$tmp/$(basename "$unit")"
done
sudo install -m 0644 "$tmp"/*.service /etc/systemd/system/
rm -rf "$tmp"
sudo systemctl daemon-reload
echo "installed: $(ls deploy/systemd | tr '\n' ' ')"
