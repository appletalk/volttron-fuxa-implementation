# VOLTTRON ↔ FUXA Integration

Integrate the [FUXA](https://github.com/frangoteam/FUXA) SCADA/HMI UI with an
[Eclipse VOLTTRON](https://github.com/eclipse-volttron) platform so FUXA can
read and write SCADA points/registers that VOLTTRON collects from field
devices (Modbus, DNP3, BACnet, …).

## Goals

1. **Primary — Integrate FUXA with VOLTTRON** *(no FUXA rewrite required).*
   FUXA's backend already has a clean device-connector plugin layer
   (`FUXA/server/runtime/devices/`). We add **one** new `volttron` connector,
   so the whole FUXA UI (tags, gauges, alarms, historian, read/write widgets)
   works against VOLTTRON points.
2. **Secondary / medium-term — Port FUXA's backend from Node.js to Python.**
   Tracked separately. The integration is designed so the Python-side work
   (the `fuxa-bridge` agent) is reused verbatim after that rewrite.

## Architecture

```
modbus-sim ──Modbus TCP──> VOLTTRON ──WebSocket(reads)/REST(writes)──> FUXA
 (fake device)              platform-driver + Modbus driver            UI + new
                            + historian + fuxa-bridge agent            `volttron`
                            (devices/# pub-sub, get/set_point RPC)     connector
```

The **fuxa-bridge** is a VOLTTRON agent (Python) on the ZMQ/VIP bus. It
subscribes to `devices/#` for real-time reads and calls the platform driver's
`get_point` / `set_point` / `set_multiple_points` RPC for writes, exposing a
WebSocket + REST surface tailored to FUXA's connector contract.

### Why a bridge agent (not a FUXA rewrite, not raw MQTT)

- FUXA (Node) can't speak VOLTTRON's ZMQ/VIP protocol directly.
- The bridge keeps all VOLTTRON-specific logic in Python on the bus, where it
  belongs — and where it stays valid after the FUXA→Python rewrite.
- Reads stream over WebSocket (true real-time); writes are request/response
  over REST and map 1:1 to `set_point`, which MQTT can't model cleanly.

## Repos this depends on (cloned as siblings, in `../`)

| Path | Role |
|---|---|
| `../FUXA` | The UI/HMI. We add a `volttron` device connector to its server. |
| `../volttron-core` | The ZMQ/VIP message-bus platform (`volttron` 10.1). |
| `../volttron-platform-driver` | Polls field devices, publishes `devices/#`, exposes `get_point`/`set_point` RPC. |

## Dev environment

Everything runs in Docker — nothing is installed on the host. One stack brings
up the fake device, the platform, and the UI.

> **Status:** `modbus-sim` is built and verified. VOLTTRON platform, bridge
> agent, and FUXA connector are in progress (see the task list / phases below).

```bash
docker compose up modbus-sim      # fake field device only (works today)
docker compose up                 # full stack (once Phase 3–5 land)
```

Then open FUXA at <http://localhost:1881>.

### Components

| Service | Dir | Status |
|---|---|---|
| `modbus-sim` | `modbus-sim/` | ✅ built & verified |
| `volttron` (platform + driver + historian + bridge) | `volttron/` | ⏳ Phase 3–4 |
| `fuxa` | uses `frangoteam/fuxa:latest`, then local `../FUXA` build | ⏳ Phase 5 |

### modbus-sim register map (unit/slave id 1)

| Address | Point | Scaling | Behaviour |
|---|---|---|---|
| HR[0] | temperature | ×10 (215 → 21.5 °C) | wanders |
| HR[1] | humidity | ×10 | wanders |
| HR[2] | flow_rate | ×10 | wanders |
| HR[3] | pump_speed | raw 0–100 | writable, holds |
| HR[4] | valve_open | raw 0–100 | writable, holds |
| HR[5] | run_command | 0/1 | writable, holds |
| CO[0] | pump_enable | 0/1 | writable, holds |

## Build phases

1. ✅ Scaffold repo + compose skeleton
2. ✅ Modbus simulator (verified: sensors wander, setpoints writable)
3. ✅ VOLTTRON platform: platform-driver + Modbus driver + sqlite historian
   (verified: live scrape of all 7 points on `devices/campus/building/modbus_sim/all`,
   recorded to the sqlite historian; correct values, e.g. temperature 22.2 °C)
4. ✅ `fuxa-bridge` agent — dynamic VOLTTRON agent + gevent HTTP/WebSocket on
   :8080. Verified: WS snapshot + live update pushes (reads), and
   `PUT /api/points` → `set_point` RPC → Modbus write reaching the simulator.
5. ✅ FUXA `volttron` device connector + UI (in the [appletalk/FUXA fork](https://github.com/appletalk/FUXA/tree/volttron-connector)).
   Verified end-to-end in the FUXA UI: `Volttron` device type selectable, bridge-URL
   address field, point browser lists all 7 VOLTTRON points, imported as tags, live
   reads stream into the editor, and a write (`valve_open=77`) round-trips to the device.

**The integration is complete and verified end-to-end.** The `fuxa` compose
service builds the fork locally (`build: ../FUXA`).

### fuxa-bridge API (port 8080)

| Method / path | Purpose |
|---|---|
| `GET /api/health` | `{"status":"ok","points":N}` |
| `GET /api/points` | snapshot `{ "<campus>/<building>/<device>/<point>": {"value","ts"} }` |
| `PUT /api/points` | body `{"path","point","value"}` → `platform.driver` `set_point`; returns written value |
| `WS  /ws` | on connect `{"type":"snapshot","points":{...}}`, then `{"type":"update","points":{...}}` per scrape |
| `GET /api/devices` | devices grouped by path → `{point: value}` |
| `GET /api/platform` | health: agents on the bus, point/device counts, freshness |
| `GET /api/history?point=&minutes=&limit=` | historian time series for a point |

## Claude MCP server (`mcp/`)

A local [MCP](https://modelcontextprotocol.io) server (`mcp/server.py`) lets Claude
drive this stack directly — read/write points, query history, inspect health, and
(incrementally) run tests, inject faults, and build devices/dashboards. It's a thin
tool layer over the bridge + FUXA + sim. Setup and tool list: [`mcp/README.md`](mcp/README.md).
Registered via project-scoped `.mcp.json`; reconnect Claude Code in this dir to enable it.

Runs inside the `volttron` container (launched by the entrypoint) as a dynamic
agent — `build_agent()` connects over IPC via `VOLTTRON_HOME`, no extra auth.

### VOLTTRON packaging notes (hard-won — don't regress)

The modular VOLTTRON ecosystem ships **only pre-releases**, in two incompatible
lines: the v10 `volttron` line (has a working Modbus driver) and the v2.0
`volttron-core` line (no Modbus driver yet). We use the **v10 line**; the
coherent set is pinned in `volttron/requirements.txt`.

`vctl install <name>` runs `pip install [--pre] <name>` into the platform venv
and records the *exact install string* as the agent's package id (used later to
start the agent). Therefore:
- Install agents by **bare name** (no `==version`) — a version spec makes
  `start_agent` fail (`PackageNotFoundError`).
- **No `--force`** — it maps to `pip --force-reinstall`, which re-resolves to
  the latest 2.0 line and breaks the Modbus driver's base-driver pin.
- The correct versions are pre-installed via `requirements.txt`, and bare
  `pip install` does not upgrade an already-satisfied requirement, so the
  pinned v10 versions are kept.

Also: the Modbus interface reads **read-only** analog points from *input*
registers (FC4) and **writable** ones from *holding* registers (FC3), so the
simulator must place sensors in input registers — see `modbus-sim/sim.py`.
Rebuild `modbus-sim` after editing `sim.py` (`docker compose build modbus-sim`).
