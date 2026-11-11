# Deploying vehicle-linux-node on the Raspberry Pi 5

Plain systemd services, no Kanto (CLAUDE.md). Everything runs from a user checkout in
`$HOME/vehicle-linux-node`; `deploy/install.sh` syncs the environment and installs the units.

| Unit | Role | Notes |
|---|---|---|
| `vehicle-can-platform@can0` | platform CAN bus up | bitrate from the defs; normal mode (OTA client transmits later) |
| `vehicle-can-vehicle-tap@can1` | optional CL250 vehicle-bus tap | `listen-only on` always (invariant 1); raw logging only |
| `vehicle-kuksa` | Kuksa Databroker + kuksa-can-provider | `docker compose`, `CAN_PORT=can0`, databroker on 127.0.0.1 only |
| `vehicle-blackbox` | black-box recorder | `VEHICLE_BLACKBOX_DIR=$HOME/vehicle-data/blackbox`, `vehicle-linux-node-blackbox trigger <reason>` fires an event |

CAN HAT: enable the overlay for your board in `/boot/firmware/config.txt`
(e.g. `dtoverlay=mcp251xfd,spi0-0,interrupt=25` for an MCP2518FD HAT) and reboot; the
units bind to `sys-subsystem-net-devices-<if>.device`.

Until the defs carry the platform bus bitrate as a DBC attribute, `vehicle-linux-node-can platform`
needs `--platform-bitrate` (D-009 states 500 kbps); the unit is updated when the defs attribute lands.
