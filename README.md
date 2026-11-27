# vehicle-linux-node

Part of [vehicle-platform](https://github.com/vehicle-platform), an SDV-style diagnostics, telemetry and rider-assistance platform for motorcycles (first vehicle: Honda CL250).

The SDV/HPC layer running on a **Raspberry Pi 5 (8 GB)** (mostly Python, C++ where performance is needed). The signal layer is set up first, and everything else sits on top of it:
1. **Kuksa Databroker** (Docker) + **kuksa-can-provider**: reads the platform bus (where rt-core republishes vehicle signals, D-021) and converts it to VSS using `platform.dbc` + the VSS overlay (D-004). The vehicle bus may be tapped strictly listen-only for raw logging; the Raspi never polls the ECU.
2. Applications (each its own process, reading from Kuksa via gRPC): lane departure warning (LDW), combined anomaly model inference, HMI, ride video+telemetry overlay, black-box recorder, node health dashboard, OTA distribution (to MCUs over UDS), offline maps.
3. Calls `vehicle-mcp` as a local subprocess/dependency, feeding its result as context to the cloud LLM.

**Status:** v0.1.0 skeleton (Raspi track L0). `vehicle_linux_node.defs` locates and parses the pinned defs files the Kuksa CAN bridge will read (`dbc/platform.dbc`, `gen/vss/vss_dbc.json`); its tests fail when a defs bump hands the bridge a VSS mapping it cannot resolve on the platform bus. L1 adds Kuksa Databroker + kuksa-can-provider (`docker-compose.yml`, D-004) and a synthetic replay (`vehicle-linux-node-replay`) that CI pushes through the bridge and reads back from the databroker. The LDW offline stage (`vehicle-linux-node-ldw`, extra `ldw`) runs the §5b.5 classic-CV pipeline on images, video or a synthetic lane with a known offset. The black-box recorder (`vehicle-linux-node-blackbox`, extra `kuksa`) keeps the last seconds of every mapped VSS signal and writes the window around a trigger to its own directory. The node-health panel (`vehicle-linux-node-nodehealth`, extra `kuksa`) shows mode, error count, uptime and stale/missing per platform-bus node (D-108). `deploy/` holds the Raspberry Pi systemd units and `install.sh` (see `deploy/README.md`).

## Build and test

```bash
git submodule update --init --recursive   # external/vehicle-defs, pinned to a release tag
uv sync
uv run ruff check .
uv run ruff format --check .
uv run pytest
```

- Architecture and decisions: [vehicle-defs/docs](https://github.com/vehicle-platform/vehicle-defs/tree/main/docs) (`ARCHITECTURE.md`, `DECISIONS.md`)
- Signals, CAN IDs and DIDs come only from [vehicle-defs](https://github.com/vehicle-platform/vehicle-defs) (git submodule pinned to a tag)
- Scope rules for contributors and Claude Code: [`CLAUDE.md`](CLAUDE.md)

## Run on macOS without a Raspberry Pi

The Kuksa replay stack (synthetic platform-bus replay -> kuksa-can-provider -> Databroker) runs on an Apple Silicon Mac with [colima](https://github.com/abiosoft/colima) (no Docker Desktop). The images are multi-arch, so the arm64 VM pulls the pinned digests as is.

```bash
brew install colima docker docker-compose
# let `docker compose` find the Homebrew plugin: add to ~/.docker/config.json
#   "cliPluginsExtraDirs": ["/opt/homebrew/lib/docker/cli-plugins"]
colima start --cpu 2 --memory 2 --disk 20

git submodule update --init
uv sync --locked --extra kuksa
uv run vehicle-linux-node-replay replay/          # replay/ is git-ignored
docker compose -f docker-compose.yml -f docker-compose.replay.yml up -d
```

The services use `network_mode: host`, which on colima is the Linux VM's network, and the Databroker binds `127.0.0.1` there, so the Mac cannot reach `127.0.0.1:55555`. Read the values from a container on the VM host network:

```bash
docker run --rm --network host python:3.12-slim sh -c \
  'pip install -q kuksa-client && python -c "
from kuksa_client.grpc import VSSClient
with VSSClient(\"127.0.0.1\", 55555) as c:
    print(c.get_current_values([\"Vehicle.Speed\", \"Vehicle.Powertrain.CombustionEngine.Speed\"]))"'
```

The synthetic replay is a steady state: `Vehicle.Speed` reads 94.35 and the engine speed 6062 (see `replay/expected.json`). The provider runs receive-only (`--no-val2dbc`). Stop with `docker compose -f docker-compose.yml -f docker-compose.replay.yml down` and free the RAM with `colima stop`.

## License

MIT, see [LICENSE](LICENSE) (D-036).
