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

Until the defs carry the platform bus bitrate as a DBC attribute, `vehicle-linux-node-can platform`
needs `--platform-bitrate` (D-009 states 500 kbps); the unit is updated when the defs attribute lands.

## Live uplink (`vehicle-live`, D-109 provisional)

Subscribes the databroker over gRPC and publishes to the vehicle-server MQTT broker. Off until you
configure it: create `~/.config/vehicle-live.env` (mode 0600, never committed), then
`sudo systemctl enable --now vehicle-live`.

```
VEHICLE_MQTT_URL=mqtts://broker.example:8883   # mqtt:// for the plain local profile
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
