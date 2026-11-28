# Draft: Running Eclipse KUKSA Databroker and `kuksa-can-provider` Replay on macOS without CAN Hardware

> **Status:** **Proposed draft only** (card T74 item 2). Nothing has been submitted or published upstream. Open questions for the user are at the end.
>
> **Provenance:** Derived from `vehicle-linux-node` PR #35 (`README.md`, "Run on macOS without a Raspberry Pi"), `docker-compose.yml`, `docker-compose.replay.yml`, `config/dbc_feeder.ini`, and `src/vehicle_linux_node/replay.py`. Verified locally on an Apple Silicon Mac in PR #35; commands below were not re-executed during this documentation draft pass (**CONFIRM** on a clean Mac before submitting upstream). No vehicle-specific CAN IDs or DIDs appear in this document (D-135).

---

## Why this guide exists

Developers on macOS do not have Linux SocketCAN (`vcan0` / `can0`), and many contributors prefer [colima](https://github.com/abiosoft/colima) over Docker Desktop. Because `kuksa-can-provider` supports replaying a `candump` log file via `--dumpfile` (using `python-can`'s virtual bus inside the container) and both official Eclipse KUKSA images publish multi-arch (`amd64` + `arm64`) manifests, the full **CAN replay → `kuksa-can-provider` → KUKSA Databroker → `kuksa-client`** pipeline runs unmodified on Apple Silicon macOS with no CAN hardware and no Raspberry Pi.

There is one macOS/colima networking subtlety that trips up first-time users: containers started with `network_mode: host` share the **colima Linux VM's** network namespace, not the macOS host's `127.0.0.1`. This draft documents the exact working setup and how to query the Databroker from a container on the VM host network.

---

## Tested software and image versions

Exact pinned versions from `vehicle-linux-node/docker-compose.yml` (D-004, EDGE-2):

| Component | Version / Digest | Source in `vehicle-linux-node` |
|---|---|---|
| Eclipse KUKSA Databroker | `ghcr.io/eclipse-kuksa/kuksa-databroker:0.7.2@sha256:27a134f15e3caf79156c72434123babb26e9b80bd5423434c85061d074f74658` | `docker-compose.yml:24` |
| Eclipse KUKSA CAN Provider | `ghcr.io/eclipse-kuksa/kuksa-can-provider/can-provider:0.5.1@sha256:41f5abffe30be4dd0bf6d82f28ca149d327e2d6de8619b562a12035a30da0c8e` | `docker-compose.yml:44` |
| Client container (read-back check) | `python:3.12-slim` + `kuksa-client` | `README.md:45-49` |
| Container VM runtime | `colima` + Homebrew `docker` & `docker-compose` (**CONFIRM** exact Homebrew formula versions on a clean Mac) | `README.md:31-34` |
| Platform | Apple Silicon macOS (`arm64` VM pulls the multi-arch manifests directly) | `README.md:28` |

---

## Architecture of the replay stack

1. **`databroker`** (`docker-compose.yml:23-41`):
   - Runs with `network_mode: host`, `no-new-privileges:true`, `cap_drop: [ALL]`, `read_only: true`.
   - Loads a VSS JSON mapping file (`--vss /vss/vss_dbc.json`) mounted read-only, binding loopback `--address 127.0.0.1 --port 55555 --insecure` (acceptable only on loopback for local development; never expose `--insecure` off-device).
2. **`can-provider`** (`docker-compose.yml:43-68`, overridden by `docker-compose.replay.yml:3-16`):
   - Connects to `grpc://127.0.0.1:55555` (`config/dbc_feeder.ini:6`).
   - Runs strictly receive-only (`dbc2val = True`, `val2dbc = False` in `config/dbc_feeder.ini:11-12` and `--no-val2dbc` in `docker-compose.replay.yml:13`) so it never transmits onto a CAN bus.
   - Mounts a DBC file, a VSS `dbc2vss` mapping JSON, and a synthetic `candump` log (`platform_replay.log`), replaying it in a loop with `--dumpfile /replay/platform_replay.log --infinite` (`docker-compose.replay.yml:14-16`). No SocketCAN device is opened.
   - Needs `tmpfs: [/tmp:exec]` (`docker-compose.yml:51-53`) because the `dbcfeeder` binary inside the image is a PyInstaller bundle that unpacks shared libraries into `/tmp` and maps them executable.
3. **Synthetic replay input** (`src/vehicle_linux_node/replay.py`):
   - Rather than committing recorded vehicle captures or vehicle-specific CAN IDs/DIDs, the generator reads the project's DBC and `vss_dbc.json` mapping and encodes 20 cycles at 0.1 s intervals (`CYCLES = 20`, `CYCLE_S = 0.1`) of every mapped frame at 37 % of each numeric signal's DBC `[min, max]` range (and the second mapped entry for value-table signals), writing both `replay/platform_replay.log` (standard `can-utils` candump format) and `replay/expected.json` (expected VSS leaf values).
   - For a generic upstream Eclipse KUKSA guide, readers can substitute any small synthetic DBC + VSS overlay + `candump` log file (`(timestamp) vcan0 <frame_id>#<payload_hex>`).

---

## Step-by-step instructions (clean Apple Silicon Mac)

All commands below match `vehicle-linux-node/README.md` ("Run on macOS without a Raspberry Pi", lines 30–52) and `docker-compose.yml` / `docker-compose.replay.yml`. Steps marked **CONFIRM** were verified in PR #35 on the author's Mac and should be re-run on a clean Mac before upstream submission.

### 1. Install Docker CLI, Compose plugin, and colima (**CONFIRM** on a clean Mac)

```bash
brew install colima docker docker-compose
```

Configure Docker CLI to discover the Homebrew Compose plugin by adding the plugin directory to `~/.docker/config.json`:

```json
{
  "cliPluginsExtraDirs": ["/opt/homebrew/lib/docker/cli-plugins"]
}
```

Start the colima Linux VM:

```bash
colima start --cpu 2 --memory 2 --disk 20
```

### 2. Generate the synthetic replay dump and start the stack (**CONFIRM** on a clean Mac)

From the `vehicle-linux-node` repository root (or using your own synthetic DBC, VSS mapping, and `candump` log):

```bash
git submodule update --init
uv sync --locked --extra kuksa
uv run vehicle-linux-node-replay replay/          # writes replay/platform_replay.log and replay/expected.json
docker compose -f docker-compose.yml -f docker-compose.replay.yml up -d
```

### 3. Read VSS values back (important `network_mode: host` note on macOS)

Because both `databroker` and `can-provider` use `network_mode: host`, on macOS with colima "host" means the **colima Linux VM's network namespace**, not macOS itself. Since the Databroker binds `--address 127.0.0.1 --port 55555` inside that VM, **a client running directly on macOS cannot reach `127.0.0.1:55555`**.

To verify or interact with the Databroker without widening its bind address off loopback, run the client inside a container attached to the same VM host network (`--network host`):

```bash
docker run --rm --network host python:3.12-slim sh -c \
  'pip install -q kuksa-client && python -c "
from kuksa_client.grpc import VSSClient
with VSSClient(\"127.0.0.1\", 55555) as c:
    print(c.get_current_values([\"Vehicle.Speed\", \"Vehicle.Powertrain.CombustionEngine.Speed\"]))"'
```

With `vehicle-linux-node`'s steady-state synthetic replay (`README.md:52`), `Vehicle.Speed` reports `94.35` and `Vehicle.Powertrain.CombustionEngine.Speed` reports `6062` (matching `replay/expected.json`).

> **Note for upstream readers who want macOS-native client access (**CONFIRM**):** If a local development setup specifically needs a macOS-native process to reach port 55555, an alternative compose override can replace `network_mode: host` with a user-defined Docker bridge network, set Databroker `--address 0.0.0.0`, publish `127.0.0.1:55555:55555`, and point `dbc_feeder.ini` at `grpc://databroker:55555`. This alternative is **not** part of `vehicle-linux-node/docker-compose.yml` (which keeps `network_mode: host` for Linux SocketCAN parity and binds loopback only) and must be verified (**CONFIRM**) before recommending upstream.

### 4. Tear down

```bash
docker compose -f docker-compose.yml -f docker-compose.replay.yml down
colima stop
```

---

## Troubleshooting notes observed in `vehicle-linux-node`

- **PyInstaller `read_only: true` failure:** When running `kuksa-can-provider:0.5.1` with a read-only root filesystem (`read_only: true`), omitting `tmpfs: [/tmp:exec]` causes `dbcfeeder` to fail on startup because it extracts libraries into `/tmp` and executes them (`docker-compose.yml:51-53`).
- **`docker compose` not found after `brew install docker-compose`:** Homebrew installs the Compose v2 plugin under `/opt/homebrew/lib/docker/cli-plugins`, which Docker CLI only loads once `cliPluginsExtraDirs` is added to `~/.docker/config.json` (`README.md:32-33`).
- **Connection refused on macOS `127.0.0.1:55555`:** Expected with `network_mode: host` under colima; query from a `--network host` container as shown in step 3 (`README.md:42-50`).

---

## Open questions for the user before any upstream submission

1. **Target repository and document location:** Should this contribution target `eclipse-kuksa/kuksa-can-provider` (e.g., `docs/` or `README.md` replay section), `eclipse-kuksa/kuksa-databroker`, or the central Eclipse KUKSA docs site?
2. **GitHub account and ECA:** Which GitHub account should open the upstream PR, and is the Eclipse Contributor Agreement (ECA) signed for that account (Eclipse Foundation repositories are understood to require a signed ECA and a `Signed-off-by` trailer; not checked against the Eclipse contributor guide for this draft, **re-check before use**)?
3. **Generic vs. repo-linked example:** Before submitting upstream, should we replace the `vehicle-linux-node-replay` step with a self-contained 10-line synthetic DBC + `vss_dbc.json` + `candump` snippet so upstream readers do not need to clone `vehicle-linux-node`?
4. **Bridge-network alternative:** Should the upstream guide show only the `--network host` colima workflow verified in PR #35, or should we first test and verify (**CONFIRM**) the bridge-network + `127.0.0.1:55555:55555` port-forwarding variant for macOS-native clients?
