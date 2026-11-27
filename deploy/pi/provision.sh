#!/usr/bin/env sh
# Provision a fresh Raspberry Pi 5 (Pi OS trixie) as a vehicle-platform edge node (D-121, D-122).
# Run ON the Pi as the unprivileged `vehicle` user (sudo is used where needed). Idempotent: every
# step checks first and is skipped when already done, so a re-run only pulls and repairs.
#
#   sh provision.sh [--with-llama] [--model NAME] [--dry-run]
#
#   --with-llama   build llama.cpp (pinned) into /usr/local/bin/llama-server if it is missing
#   --model NAME   fetch that model with deploy/llama/fetch-model.sh and write /etc/vehicle/llama.env
#                  (names: sh deploy/llama/fetch-model.sh --list; default: none)
#   --dry-run      print the plan only; changes nothing
#
# The vehicle-server repo is private: it is cloned over SSH with a read-only deploy key
# (~/.ssh/github_vehicle_server). Without the key the script prints how to create it and goes on
# with the public repos. Never prints the API token it generates (stored in /etc/vehicle/server.env).
set -eu

ORG="vehicle-platform"
BASE="$HOME/vehicle-platform"
KEY="$HOME/.ssh/github_vehicle_server"
ALIAS="github-vehicle-server"
DEFS_URL="https://github.com/$ORG/vehicle-defs"
ME=$(id -un)
with_llama=0
model=""
dry=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --with-llama) with_llama=1 ;;
    --model) [ "$#" -ge 2 ] || { echo "--model needs a name"; exit 2; }; model="$2"; shift ;;
    --dry-run) dry=1 ;;
    *) echo "usage: sh provision.sh [--with-llama] [--model NAME] [--dry-run]"; exit 2 ;;
  esac
  shift
done

[ "$(id -u)" -ne 0 ] || { echo "run as the vehicle user, not root (sudo is called where needed)"; exit 1; }
PATH="$HOME/.local/bin:$PATH"
export PATH

say() { printf '%s\n' "$*"; }
# run CMD ARGS...: executes, or only prints it with --dry-run
run() {
  if [ "$dry" = 1 ]; then say "  + $*"; else "$@"; fi
}
have() { command -v "$1" >/dev/null 2>&1; }

step_apt() {
  say "== apt packages"
  missing=""
  for pkg in git cmake build-essential python3-dev rsync curl ca-certificates avahi-daemon; do
    dpkg -s "$pkg" >/dev/null 2>&1 || missing="$missing $pkg"
  done
  if [ -z "$missing" ]; then say "  skip: all installed"; return 0; fi
  run sudo apt-get update
  # shellcheck disable=SC2086  # word splitting of the package list is intended
  run sudo apt-get install -y $missing
}

step_uv() {
  say "== uv"
  if have uv; then say "  skip: $(uv --version)"; return 0; fi
  if [ "$dry" = 1 ]; then say "  + curl -LsSf https://astral.sh/uv/install.sh | sh"; return 0; fi
  curl -LsSf https://astral.sh/uv/install.sh | sh
}

# sync_repo NAME URL: clone or fast-forward ~/vehicle-platform/NAME, then fetch the defs submodule
sync_repo() {
  name="$1"; url="$2"; dir="$BASE/$name"
  say "== repo $name"
  if [ -d "$dir/.git" ]; then
    run git -C "$dir" pull --ff-only
  else
    run mkdir -p "$BASE"
    run git clone "$url" "$dir"
  fi
  if [ "$dry" = 1 ]; then return 0; fi
  if [ -f "$dir/.gitmodules" ] && grep -q 'external/vehicle-defs' "$dir/.gitmodules"; then
    # .gitmodules holds a relative URL; the public https one works without credentials.
    git -C "$dir" config submodule.external/vehicle-defs.url "$DEFS_URL"
    git -C "$dir" submodule update --init
  fi
}

print_deploy_key_help() {
  cat <<HELP
  vehicle-server is private and $KEY does not exist: skipping it. To enable it:
  1. on the Pi:   ssh-keygen -t ed25519 -N '' -C "vehiclepi deploy key" -f $KEY
  2. on the Pi:   cat >> ~/.ssh/config <<'CFG'
                  Host $ALIAS
                    HostName github.com
                    User git
                    IdentityFile $KEY
                    IdentitiesOnly yes
                  CFG
                  chmod 600 ~/.ssh/config
  3. on the Pi:   GitHub's host keys, verified through the API on a maintainer machine:
                  gh api meta --jq '.ssh_keys[]' | sed 's/^/github.com /'   # copy into ~/.ssh/known_hosts
  4. on a maintainer machine (read-only key, no --allow-write):
                  gh repo deploy-key add $KEY.pub -R $ORG/vehicle-server -t vehiclepi
                  (copy the .pub from the Pi first)
  then run this script again.
HELP
}

ensure_ssh_alias() {
  grep -q "^Host $ALIAS\$" "$HOME/.ssh/config" 2>/dev/null && return 0
  say "  adding Host $ALIAS to ~/.ssh/config"
  [ "$dry" = 1 ] && return 0
  mkdir -p "$HOME/.ssh"; chmod 700 "$HOME/.ssh"
  printf '\nHost %s\n  HostName github.com\n  User git\n  IdentityFile %s\n  IdentitiesOnly yes\n' \
    "$ALIAS" "$KEY" >> "$HOME/.ssh/config"
  chmod 600 "$HOME/.ssh/config"
}

have_server=0
step_repos() {
  sync_repo vehicle-linux-node "https://github.com/$ORG/vehicle-linux-node"
  sync_repo vehicle-mcp "https://github.com/$ORG/vehicle-mcp"
  if [ -f "$KEY" ]; then
    ensure_ssh_alias
    sync_repo vehicle-server "git@$ALIAS:$ORG/vehicle-server.git"
    have_server=1
  else
    say "== repo vehicle-server"
    print_deploy_key_help
  fi
}

step_sync_envs() {
  say "== python environments (uv sync)"
  if [ "$have_server" = 1 ]; then
    run uv sync --project "$BASE/vehicle-server" --extra chat
  fi
  run uv sync --project "$BASE/vehicle-mcp"
  run uv sync --project "$BASE/vehicle-linux-node"
}

step_llama_build() {
  say "== llama.cpp"
  if [ -x /usr/local/bin/llama-server ]; then say "  skip: /usr/local/bin/llama-server exists"; return 0; fi
  if [ "$with_llama" = 0 ]; then say "  skip: not requested (--with-llama)"; return 0; fi
  run sh "$BASE/vehicle-linux-node/deploy/llama/build-llama.sh"
}

step_model() {
  say "== model"
  if [ -z "$model" ]; then say "  skip: no --model given"; return 0; fi
  fetch="$BASE/vehicle-linux-node/deploy/llama/fetch-model.sh"
  if [ "$dry" = 1 ]; then
    say "  + sudo sh $fetch $model"
    say "  + write /etc/vehicle/llama.env (0600) if missing"
    return 0
  fi
  file=$(sh "$fetch" --list | awk -v n="$model" '$1 == n { sub(".*/", "", $2); print $2 }')
  [ -n "$file" ] || { echo "unknown model '$model' (sh $fetch --list)"; exit 2; }
  [ -f "/var/lib/vehicle/models/$file" ] || sudo sh "$fetch" "$model"
  if [ -f /etc/vehicle/llama.env ]; then say "  skip: /etc/vehicle/llama.env exists"; return 0; fi
  sudo install -d -m 0755 /etc/vehicle
  printf 'LLAMA_MODEL=/var/lib/vehicle/models/%s\nLLAMA_THREADS=2\nLLAMA_CTX=2048\n' "$file" \
    | sudo install -m 0600 /dev/stdin /etc/vehicle/llama.env
}

step_server_env() {
  say "== vehicle-server data dir and environment"
  run sudo install -d -m 0700 -o "$ME" -g "$ME" /var/lib/vehicle/data
  if [ -f /etc/vehicle/server.env ]; then say "  skip: /etc/vehicle/server.env exists"; return 0; fi
  if [ "$dry" = 1 ]; then say "  + write /etc/vehicle/server.env (0600, fresh VEHICLE_API_TOKEN)"; return 0; fi
  token=$(head -c 32 /dev/urandom | od -An -tx1 | tr -d ' \n')
  sudo install -d -m 0755 /etc/vehicle
  {
    printf 'VEHICLE_API_TOKEN=%s\n' "$token"
    printf 'VEHICLE_DATA_DIR=/var/lib/vehicle/data\n'
    printf 'VEHICLE_CHAT_ENABLED=1\n'
    printf 'VEHICLE_LLM_URL=http://127.0.0.1:8081/v1\n'
    printf 'VEHICLE_MCP_COMMAND=%s/vehicle-mcp/.venv/bin/vehicle-mcp\n' "$BASE"
    printf 'VEHICLE_CHAT_SELF_URL=http://127.0.0.1:8000\n'
    printf 'VEHICLE_WEB_APP_DIR=%s/web-app\n' "$HOME"
  } | sudo install -m 0600 /dev/stdin /etc/vehicle/server.env
  say "  wrote /etc/vehicle/server.env (token not shown: sudo cat it when you need to log in)"
}

# Same %h and User= substitution as deploy/install.sh, for the two units this node needs here
# (install.sh itself also wants docker for the CAN/Kuksa units).
step_units() {
  say "== systemd units"
  units="vehicle-llama.service"
  [ "$have_server" = 1 ] && units="$units vehicle-server.service"
  if [ "$dry" = 1 ]; then
    say "  + install $units into /etc/systemd/system, daemon-reload"
  else
    tmp=$(mktemp -d)
    for unit in $units; do
      src="$BASE/vehicle-linux-node/deploy/systemd/$unit"
      if grep -q '^DynamicUser=yes$' "$src"; then
        sed -e "s|%h|$HOME|g" "$src" > "$tmp/$unit"
      else
        sed -e "s|%h|$HOME|g" -e "/^\[Service\]/a User=$ME" "$src" > "$tmp/$unit"
      fi
      sudo install -m 0644 "$tmp/$unit" /etc/systemd/system/
    done
    rm -rf "$tmp"
    sudo systemctl daemon-reload
  fi
  if [ -f /etc/vehicle/llama.env ] && [ -x /usr/local/bin/llama-server ]; then
    run sudo systemctl enable --now vehicle-llama
  else
    say "  vehicle-llama not enabled (needs /usr/local/bin/llama-server and /etc/vehicle/llama.env)"
  fi
  if [ "$have_server" = 1 ]; then run sudo systemctl enable --now vehicle-server; fi
}

next_steps() {
  cat <<NEXT

== next steps
  1. Flutter web build -> ~/web-app:  on your dev machine run the vehicle-mobile web build, then
       rsync -a build/web/ $ME@$(hostname).local:web-app/
  2. Register a vehicle and device (the data dir is all the CLI needs; no token on a command line):
       cd $BASE/vehicle-server && VEHICLE_DATA_DIR=/var/lib/vehicle/data \\
         .venv/bin/vehicle-server vehicle add --name <name> --device <device-id>
  3. In the app, add the server http://$(hostname).local:8000, create the account and claim the
     vehicle with the claim code step 2 printed (operator token: sudo cat /etc/vehicle/server.env).
     On a shared hotspot other users see the login page:
     keep the token secret and the hotspot password strong.
  4. Power: use the 27 W supply; check 'vcgencmd get_throttled' is 0x0 under load.
NEXT
}

[ "$dry" = 1 ] && say "DRY RUN: nothing is changed"
step_apt
step_uv
step_repos
step_sync_envs
step_llama_build
step_model
if [ "$have_server" = 1 ]; then step_server_env; fi
step_units
next_steps
