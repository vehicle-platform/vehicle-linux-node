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

`uv` + `ruff` + `pytest` (D-007), Python 3.11+. Kuksa runs via `docker compose` (`docker-compose.yml`: databroker 0.7.1 + kuksa-can-provider 0.5.1, host networking, `config/dbc_feeder.ini`, receive-only: `val2dbc` off). In development, recorded data is replayed with `vcan0/vcan1` + `canplayer`, so it works without a Raspi too.

```bash
git submodule update --init --recursive   # external/vehicle-defs, pinned to v0.27.0
uv sync
uv run ruff check . && uv run ruff format --check .
uv run pytest
# end to end (Linux + Docker; the CI kuksa-replay job):
uv sync --group integration && uv run vehicle-linux-node-replay replay/
docker compose -f docker-compose.yml -f docker-compose.replay.yml up -d
VEHICLE_KUKSA_ADDR=127.0.0.1:55555 VEHICLE_REPLAY_DIR=replay uv run pytest tests/integration
```

As built: `src/vehicle_linux_node/defs.py` (paths into the pinned defs, `load_platform_dbc()`, `dbc2vss_mappings()`), `tests/test_defs.py` (every `dbc2vss` signal exists once on `platform.dbc`, value maps use DBC value names); `replay.py` (`vehicle-linux-node-replay`: synthetic candump of every mapped platform message from the defs + `expected.json`), `tests/test_bridge_config.py` (provider receive-only, reads the pinned defs, images pinned), `tests/integration/` (values read back from the databroker, skipped without `VEHICLE_KUKSA_ADDR`). CI also fails unless the defs pin is a `vX.Y.Z` tag.

## Black-box recorder (as built, L4)

`src/vehicle_linux_node/blackbox/` (extra `kuksa` for the service): `core.py` = pure ring of the last `pre_ms` per subscribed VSS leaf, `trigger()` opens an event (pre snapshot + post window, a repeat trigger extends up to `max_post_ms`), position paths refused by rule (D-076, invariant 7); `store.py` = one directory per event (`event.json` + `samples.jsonl`, `defs_version`), `prune()` keeps the newest `max_events`; `service.py` = `subscribe_current_values` on the `dbc2vss` leaves, triggers from `<dir>/trigger` (one reason per line, consumed) until an rt-core event path exists on VSS; CLI `vehicle-linux-node-blackbox [--dir] run|trigger <reason>` (`VEHICLE_BLACKBOX_DIR`, `VEHICLE_KUKSA_ADDR`). Integration test in the CI `kuksa-replay` job. Never touches CAN.

## LDW offline stage (as built)

`src/vehicle_linux_node/ldw/` (optional extra `ldw`: `uv sync --extra ldw`): `pipeline.py` = §5b.5 steps on one BGR frame (lean derotation, optional undistort, HLS + Sobel threshold, bird's-eye warp, sliding-window fit, offset in metres, `departure()` with indicator suppression), `synthetic.py` renders lanes with a known offset through the inverse warp (no recorded video in the repo, D-033), `cli.py` = `vehicle-linux-node-ldw <image|video|synthetic> [--lean-deg] [--indicator-on]`. `LdwConfig` holds camera geometry and provisional tuning (departure threshold 0.45 m, looser than a car); nothing opens a camera, CAN or Kuksa yet. Next: calibrate `src_quad` to the real mount, then lean from `Vehicle.Motorcycle.LeanAngle` and the indicator state from Kuksa.

## Context

ARCHITECTURE §3, §5, §7 · `../vehicle-defs/docs/hardware-architecture.md` §5b.5, §5b.7-5b.9.
