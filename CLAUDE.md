# CLAUDE.md — vehicle-linux-node

@.claude/PLATFORM-RULES.md

## What this repo is

The SDV/HPC layer running on a **Raspberry Pi 5 (8 GB)** (mostly Python, C++ where performance is needed). The signal layer is set up first, and everything else sits on top of it:
1. **Kuksa Databroker** (Docker) + **kuksa-can-provider**: reads the platform bus (where rt-core republishes vehicle signals, D-021) and converts it to VSS using `platform.dbc` + the VSS overlay (D-004). The vehicle bus may be tapped strictly listen-only for raw logging; the Raspi never polls the ECU.
2. Applications (each its own process, reading from Kuksa via gRPC): lane departure warning (LDW), combined anomaly model inference, HMI, ride video+telemetry overlay, black-box recorder, node health dashboard, OTA distribution (to MCUs over UDS), offline maps.
3. Calls `vehicle-mcp` as a local subprocess/dependency, feeding its result as context to the cloud LLM.

## What this repo is NOT

- No safety decisions. Cornering and blind spot live on the MCUs; they keep working even if the Raspi crashes.
- Applications **never touch SocketCAN directly**. Only kuksa-can-provider and the OTA/UDS service access CAN.
- The bootloader/UDS server is not here (rt-core). This is only the UDS client/OTA distributor.
- No model training (`vehicle-ml`). Only inference happens here.
- No CI/CD here (the i7 HIL host). Eclipse Kanto is NOT in the MVP; simple systemd services are enough.

## Rules

- Lane departure warning is done with **classic CV** (OpenCV), NOT deep learning (it doesn't need training data — a deliberate choice). Lean/yaw correction comes from Kuksa's EKF lean angle, not computed separately.
- The anomaly model (early fusion) does **not replace** rt-core's rule-based `anomaly-safety-net` fallback — don't break it.
- Heavy acoustic/anomaly analysis does not run continuously; it runs in periodic windows (Raspi capacity budget, heat).
- Privacy: raw GPS never goes into the general context packet/cloud LLM, only a local summary does. Coordinates are only sent to the specific tool call that requires them.
- The LLM does not do its own math; it narrates an already-computed result.

## Dependencies

`external/vehicle-defs` (tagged): DBCs, `gen/vss/`, `gen/python/`. `vehicle-mcp` (package dependency). Talks to vehicle-server over MQTT/Zenoh.

## Build/run

`uv` + `ruff` + `pytest` (D-007), Python 3.11+. Kuksa runs via `docker compose`. In development, recorded data is replayed with `vcan0/vcan1` + `canplayer`, so it works without a Raspi too.

## Context

ARCHITECTURE §3, §5, §7 · `../vehicle-defs/docs/hardware-architecture.md` §5b.5, §5b.7-5b.9.
