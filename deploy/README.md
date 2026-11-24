# Deploying vehicle-linux-node on the Raspberry Pi 5

Plain systemd services, no Kanto (CLAUDE.md). Everything runs from a user checkout in
`$HOME/vehicle-linux-node`; `deploy/install.sh` syncs the environment and installs the units.

| Unit | Role | Notes |
|---|---|---|
| `vehicle-can-platform@can0` | platform CAN bus up | bitrate from the defs; normal mode (OTA client transmits later) |
| `vehicle-can-vehicle-tap@can1` | optional CL250 vehicle-bus tap | `listen-only on` always (invariant 1); raw logging only |
| `vehicle-kuksa` | Kuksa Databroker + kuksa-can-provider | `docker compose`, `CAN_PORT=can0`, databroker on 127.0.0.1 only |
| `vehicle-blackbox` | black-box recorder | `VEHICLE_BLACKBOX_DIR=$HOME/vehicle-data/blackbox`, `vehicle-linux-node-blackbox trigger <reason>` fires an event |
| `vehicle-live` | live telemetry uplink Kuksa -> MQTT 5 (D-109, provisional) | installed, **disabled by default**; see below |
| `vehicle-llama` | rider-assistant LLM, llama.cpp `llama-server` on 127.0.0.1:8081 (D-121, provisional) | installed, **disabled by default**; see "Rider assistant LLM" |

CAN HAT: enable the overlay for your board in `/boot/firmware/config.txt`
(e.g. `dtoverlay=mcp251xfd,spi0-0,interrupt=25` for an MCP2518FD HAT) and reboot; the
units bind to `sys-subsystem-net-devices-<if>.device`.

The platform bus bitrate is read from the `Baudrate` attribute of `platform.dbc` (defs >= v0.28.0); there is no CLI override.

## Live uplink (`vehicle-live`, D-109 provisional)

Subscribes the databroker over gRPC and publishes to the vehicle-server MQTT broker. Off until you
configure it: create `~/.config/vehicle-live.env` (mode 0600, never committed), then
`sudo systemctl enable --now vehicle-live`.

```
VEHICLE_MQTT_URL=mqtts://broker.example:8883   # plain mqtt:// only to loopback/private/.local hosts (else --allow-plaintext); never with position
VEHICLE_DEVICE_ID=<device id>                  # MQTT username and topic prefix
VEHICLE_MQTT_TOKEN_FILE=/home/<user>/.config/vehicle-live.token   # the device token (D-077 item 3)
# optional: VEHICLE_LIVE_RATE_HZ=10  VEHICLE_LIVE_QUEUE_MAX=10000  VEHICLE_LIVE_HEALTH_FILE=<json>
```

- Topics (QoS 1): `vehicle/<device_id>/live/<vss path>` with `{"ts": <unix ms>, "value": ...}`;
  `vehicle/<device_id>/health` (the D-079 window summary dict, published whenever
  `VEHICLE_LIVE_HEALTH_FILE` changes); retained `vehicle/<device_id>/state` with the last value of
  every signal as one JSON object. Signal names are the VSS paths of the defs `dbc2vss` mapping.
- Per-signal rate limit (default 10 Hz; dropped updates still update `state`). While the broker is
  unreachable messages wait in a bounded in-memory queue (default 10000, oldest dropped, drops
  counted and logged at exit); the post-ride bulk upload is unchanged.
- Position paths are not published unless `--include-position` is added (D-076, own server only).
- Missing config exits with status 2 and systemd does not restart-loop it.
- `uv sync --locked --extra kuksa --extra live` (done by `install.sh`) provides paho-mqtt.

## Rider assistant LLM (D-121)

The assistant is a small open-weight model that only phrases results vehicle-mcp already computed
(no cost, no cloud: nothing leaves the Pi). It runs as `vehicle-llama`: llama.cpp `llama-server`
(MIT) bound to 127.0.0.1:8081, a dynamic unprivileged user, loopback-only networking, and caps so
lane keeping keeps its timing (invariant 4): `CPUQuota=150%` of the 4 cores, `CPUWeight=20`,
`Nice=10`, `CPUSchedulingPolicy=batch`, idle IO class, `MemoryMax=3G`, no swap. The model file is
never committed. If the unit is down, vehicle-server's `/chat` falls back to the keyword matcher
and templates.

1. **Build llama.cpp** (once, on the Pi; needs `sudo apt install -y git cmake build-essential`).
   Builds the pinned release tag (tag and commit are in `deploy/llama/build-llama.sh`; the build
   refuses a tag that moved) with `-DGGML_NATIVE=ON` and installs `/usr/local/bin/llama-server`:

   ```bash
   sh deploy/llama/build-llama.sh          # or: sh deploy/install.sh --with-llama
   ```

2. **Fetch a model** (Q4_K_M GGUF, SHA-256 pinned; the download names a pinned Hugging Face
   commit, and an entry whose hash is `VERIFY` is refused):

   | name | file | size | licence |
   |---|---|---|---|
   | `qwen2.5-1.5b` | `qwen2.5-1.5b-instruct-q4_k_m.gguf` | 1.1 GB | Apache-2.0 |
   | `gemma3-1b` | `gemma-3-1b-it-Q4_K_M.gguf` | 0.8 GB | Gemma Terms of Use |
   | `gemma3-4b` | `gemma-3-4b-it-Q4_K_M.gguf` | 2.5 GB | Gemma Terms of Use |

   ```bash
   sudo sh deploy/llama/fetch-model.sh --list
   sudo sh deploy/llama/fetch-model.sh qwen2.5-1.5b    # -> /var/lib/vehicle/models
   ```

   The Gemma models carry Google's terms (use restrictions apply); read them before shipping a
   product on one. Adding a model: a new row in `fetch-model.sh` with the repo, a commit and the
   file's LFS oid from `https://huggingface.co/api/models/<repo>/tree/<commit>`.

3. **Enable the unit.** `install.sh` installs it but never enables it:

   ```bash
   sudo install -d -m 0755 /etc/vehicle
   printf '%s\n' LLAMA_MODEL=/var/lib/vehicle/models/qwen2.5-1.5b-instruct-q4_k_m.gguf \
       LLAMA_THREADS=2 LLAMA_CTX=2048 | sudo tee /etc/vehicle/llama.env >/dev/null
   sudo systemctl enable --now vehicle-llama
   curl -s http://127.0.0.1:8081/health      # {"status":"ok"} once the model is loaded
   ```

4. **Benchmark** each candidate (switch `LLAMA_MODEL`, `sudo systemctl restart vehicle-llama`,
   wait for `/health`). The benchmark sends 10 fixed Turkish rider questions, each with a
   synthetic tool result in the shape vehicle-server's phrasing step sends (the same system
   prompt: short plain Turkish, at most 3 sentences, only numbers from the result). Stdlib
   only, no real ride data:

   ```bash
   uv run vehicle-linux-node-llm-bench --label qwen2.5-1.5b --out bench/
   # -> bench/qwen2.5-1.5b.md and .json; --url, --max-tokens, --timeout, --warmup
   ```

   Run it while lane keeping / Kuksa are running, not on an idle Pi: the CPU cap is part of what
   is measured.

5. **Turn on `/chat`** in vehicle-server's environment (its `chat` extra installed):

   ```
   VEHICLE_CHAT_ENABLED=1
   VEHICLE_LLM_URL=http://127.0.0.1:8081/v1      # the default
   ```

6. **Reading the result.** Per model, compare, in this order:
   - *Number-grounded answers* must be 10 / 10 or close: an ungrounded answer is thrown away by
     the number guard and replaced by a template, so a model that often invents numbers adds
     latency and nothing else. Read the answers too: grammar and tone in Turkish, at most 3
     sentences, `advise_stop` handled (prompts 3 and 4).
   - *Total latency, median / p95*: a spoken answer should start within a few seconds, and p95
     must stay below the vehicle-server request timeout. TTFT shows prompt-processing cost,
     tokens/s the generation speed (about 5 tokens/s or more reads as fluent).
   - Memory (`systemctl status vehicle-llama` shows it against the 3 GB cap) and whether lane
     keeping missed frames during the run.

   Pick the smallest model that passes the first two. Record the choice in `DECISIONS.md` through
   Claude (a model is a config value: the choice is only `LLAMA_MODEL`).

## Verifying the sandbox (EDGE-1)

The hardening options in the units are scored, not just present. CI job `systemd-exposure`
runs `scripts/check_systemd_exposure.py`, which feeds every unit (as `install.sh` rewrites it)
to `systemd-analyze security --offline=true` and fails when a score exceeds the ceiling in
`deploy/exposure-thresholds.json` (measured 1.6 OK for blackbox, live and both CAN units, ceiling 1.8; `vehicle-llama` ceiling 1.8, see the CI summary for its score; `vehicle-kuksa` 8.6 EXPOSED, ceiling 8.8, the
documented docker-compose exception). Ratchet a ceiling down when a unit improves; never raise
one without a reason in the PR. Not enabled in the units on purpose: `MemoryDenyWriteExecute`
(grpc/numpy/OpenCV wheels may map W+X pages; try it on the Pi first). `vehicle-llama` does set
`MemoryDenyWriteExecute=yes` (llama.cpp has no JIT); if it fails to start on the Pi, drop only that line and record why.

On the Pi (once, after `install.sh`; needs systemd 252+ for `--offline`):

```bash
python3 scripts/check_systemd_exposure.py --verbose   # same gate as CI, per-check detail
systemd-analyze security vehicle-blackbox.service vehicle-live.service vehicle-llama.service \
    vehicle-can-platform@can0.service vehicle-kuksa.service   # live, installed units
sudo systemctl restart vehicle-can-platform@can0 vehicle-kuksa vehicle-blackbox
systemctl status vehicle-blackbox --no-pager && journalctl -u vehicle-blackbox -n 30 --no-pager
# a sandbox that starts but kills the process shows up as status=31/SYS (seccomp) or EPERM/EROFS in the journal
```

If a unit fails to start under the filter, loosen only the failing option and record why.

## Kuksa images and auth (EDGE-2)

`docker-compose.yml` pins both images by digest (`image:tag@sha256:...`, the multi-arch index
digest, so CI amd64 and the Pi arm64 get the same release) and starts them with
`no-new-privileges`, `cap_drop: ALL` and a read-only root. Dependabot (docker-compose) bumps the
digests. `--insecure` stays on the databroker **provisionally**: it binds 127.0.0.1, has one
writer (can-provider) and only our own readers. Move to databroker JWT (`--jwt-public-key`,
tokens via systemd `LoadCredential=`) when a second consumer or a non-loopback bind appears.

On the Pi, once: `sudo systemctl restart vehicle-kuksa`, then `docker compose ps` and
`docker compose logs can-provider | tail` must show both services up and frames flowing from
`can0`. If the provider cannot open the CAN socket under `cap_drop: ALL`, add back only the
capability the log names (`cap_add`) and record why.
