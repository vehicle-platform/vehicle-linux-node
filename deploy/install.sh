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
# Rider-assistant LLM (D-121, off by default): `sh deploy/install.sh --with-llama` also builds
# llama.cpp from the pinned release tag into /usr/local/bin (needs cmake, a C++ compiler, git);
# the model is fetched separately by deploy/llama/fetch-model.sh. See deploy/README.md.
set -eu
with_llama=0
for arg in "$@"; do
  case "$arg" in
    --with-llama) with_llama=1 ;;
    *) echo "usage: sh deploy/install.sh [--with-llama]"; exit 2 ;;
  esac
done
here=$(cd "$(dirname "$0")/.." && pwd)
command -v uv >/dev/null 2>&1 || { echo "install uv first: https://docs.astral.sh/uv/"; exit 1; }
command -v docker >/dev/null 2>&1 || { echo "install docker + compose plugin first"; exit 1; }
cd "$here"
git submodule update --init --recursive
uv sync --locked --extra kuksa --extra live
mkdir -p "$HOME/vehicle-data/blackbox"
chmod 700 "$HOME/vehicle-data" "$HOME/vehicle-data/blackbox"
# User= goes into every unit. The two vehicle-can-* units get CAP_NET_ADMIN (and only
# they) from their unit files, so `ip link set` works without root.
# %h in the units resolves to the invoking user's home only for user units; for system
# units we substitute it here so the services run as this user from this checkout.
tmp=$(mktemp -d)
for unit in deploy/systemd/*.service; do
  if grep -q '^DynamicUser=yes$' "$unit"; then
    sed -e "s|%h|$HOME|g" "$unit" > "$tmp/$(basename "$unit")"   # own user, no User= (vehicle-llama)
  else
    sed -e "s|%h|$HOME|g" -e "/^\[Service\]/a User=$(id -un)" "$unit" > "$tmp/$(basename "$unit")"
  fi
done
sudo install -m 0644 "$tmp"/*.service /etc/systemd/system/
rm -rf "$tmp"
sudo systemctl daemon-reload
if [ "$with_llama" = 1 ]; then
  sh "$here/deploy/llama/build-llama.sh"
fi
echo "installed: $(ls deploy/systemd | tr '\n' ' ')"
