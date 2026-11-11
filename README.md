# vehicle-linux-node

Part of [vehicle-platform](https://github.com/vehicle-platform), an SDV-style diagnostics, telemetry and rider-assistance platform for motorcycles (first vehicle: Honda CL250).

The SDV/HPC layer running on a **Raspberry Pi 5 (8 GB)** (mostly Python, C++ where performance is needed). The signal layer is set up first, and everything else sits on top of it:
1. **Kuksa Databroker** (Docker) + **kuksa-can-provider**: reads the platform bus (where rt-core republishes vehicle signals, D-021) and converts it to VSS using `platform.dbc` + the VSS overlay (D-004). The vehicle bus may be tapped strictly listen-only for raw logging; the Raspi never polls the ECU.
2. Applications (each its own process, reading from Kuksa via gRPC): lane departure warning (LDW), combined anomaly model inference, HMI, ride video+telemetry overlay, black-box recorder, node health dashboard, OTA distribution (to MCUs over UDS), offline maps.
3. Calls `vehicle-mcp` as a local subprocess/dependency, feeding its result as context to the cloud LLM.

**Status:** v0.1.0 skeleton (Raspi track L0). `vehicle_linux_node.defs` locates and parses the pinned defs files the Kuksa CAN bridge will read (`dbc/platform.dbc`, `gen/vss/vss_dbc.json`); its tests fail when a defs bump hands the bridge a VSS mapping it cannot resolve on the platform bus. L1 adds Kuksa Databroker + kuksa-can-provider (`docker-compose.yml`, D-004) and a synthetic replay (`vehicle-linux-node-replay`) that CI pushes through the bridge and reads back from the databroker. The LDW offline stage (`vehicle-linux-node-ldw`, extra `ldw`) runs the §5b.5 classic-CV pipeline on images, video or a synthetic lane with a known offset. The black-box recorder (`vehicle-linux-node-blackbox`, extra `kuksa`) keeps the last seconds of every mapped VSS signal and writes the window around a trigger to its own directory.

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

## License

MIT, see [LICENSE](LICENSE) (D-036).
