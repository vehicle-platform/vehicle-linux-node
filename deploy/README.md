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

## Verifying the sandbox (EDGE-1)

The hardening options in the units are scored, not just present. CI job `systemd-exposure`
runs `scripts/check_systemd_exposure.py`, which feeds every unit (as `install.sh` rewrites it)
to `systemd-analyze security --offline=true` and fails when a score exceeds the ceiling in
`deploy/exposure-thresholds.json` (measured 1.6 OK for blackbox, live and both CAN units, ceiling 1.8; `vehicle-kuksa` 8.6 EXPOSED, ceiling 8.8, the
documented docker-compose exception). Ratchet a ceiling down when a unit improves; never raise
one without a reason in the PR. Not enabled in the units on purpose: `MemoryDenyWriteExecute`
(grpc/numpy/OpenCV wheels may map W+X pages; try it on the Pi first).

On the Pi (once, after `install.sh`; needs systemd 252+ for `--offline`):

```bash
python3 scripts/check_systemd_exposure.py --verbose   # same gate as CI, per-check detail
systemd-analyze security vehicle-blackbox.service vehicle-live.service \
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
