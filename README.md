# vehicle-linux-node

Part of [vehicle-platform](https://github.com/vehicle-platform), an SDV-style diagnostics, telemetry and rider-assistance platform for motorcycles (first vehicle: Honda CL250).

The SDV/HPC layer running on a **Raspberry Pi 5 (8 GB)** (mostly Python, C++ where performance is needed). The signal layer is set up first, and everything else sits on top of it:
1. **Kuksa Databroker** (Docker) + **kuksa-can-provider**: reads the platform bus (where rt-core republishes vehicle signals, D-021) and converts it to VSS using `platform.dbc` + the VSS overlay (D-004). The vehicle bus may be tapped strictly listen-only for raw logging; the Raspi never polls the ECU.
2. Applications (each its own process, reading from Kuksa via gRPC): lane departure warning (LDW), combined anomaly model inference, HMI, ride video+telemetry overlay, black-box recorder, node health dashboard, OTA distribution (to MCUs over UDS), offline maps.
3. Calls `vehicle-mcp` as a local subprocess/dependency, feeding its result as context to the cloud LLM.

**Status:** skeleton, no code yet. The build system, tests and CI are added by `/repo-bootstrap vehicle-linux-node` when work on this repo starts (setup order: `vehicle-defs/docs/ARCHITECTURE.md` §9).

- Architecture and decisions: [vehicle-defs/docs](https://github.com/vehicle-platform/vehicle-defs/tree/main/docs) (`ARCHITECTURE.md`, `DECISIONS.md`)
- Signals, CAN IDs and DIDs come only from [vehicle-defs](https://github.com/vehicle-platform/vehicle-defs) (git submodule pinned to a tag)
- Scope rules for contributors and Claude Code: [`CLAUDE.md`](CLAUDE.md)

## License

MIT, see [LICENSE](LICENSE) (D-036).
