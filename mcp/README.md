# volttron-fuxa MCP server

A local [Model Context Protocol](https://modelcontextprotocol.io) server that lets
Claude drive this VOLTTRON + FUXA dev stack directly — read/write SCADA points,
query the historian, inspect platform health, run test scenarios, inject faults,
and build devices/dashboards.

It's a thin tool layer over the running stack:

| Target | URL | Used for |
|---|---|---|
| fuxa-bridge gateway | `http://localhost:8080` | points / history / devices / platform |
| FUXA REST API | `http://localhost:1881` | project / device / dashboard automation |
| modbus-sim control | `http://localhost:5021` | scenario / fault injection |

## Setup

```bash
cd mcp
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

The stack must be running (`docker compose up -d` in the repo root) for the tools
to return data.

## Enabling it in Claude Code

The repo ships a project-scoped `../.mcp.json` registering this server (via
`mcp/run.sh`, which resolves the venv relative to itself). **Reconnect / restart
Claude Code** in this directory to pick it up; approve the `volttron-fuxa` server
when prompted. Then ask Claude things like *"what's the temperature trend?"* or
*"set pump_speed to 60"*.

## Tools

### Tier 1 — ops & analysis
| Tool | Purpose |
|---|---|
| `list_points` | every scraped point with latest value + timestamp |
| `read_point(point)` | latest value of one point |
| `write_point(point, value)` | write a setpoint (VOLTTRON `set_point`) |
| `query_history(point, minutes, limit)` | recent historian samples for trends |
| `list_devices` | devices and their points, grouped |
| `platform_status` | agents, counts, data freshness, MCP config |

### Tier 3 — commissioning / testing
| Tool | Purpose |
|---|---|
| `write_and_verify(point, value, timeout_s, tolerance)` | write, then poll the live value until the full chain reflects it |
| `checkout_point(point, test_values, timeout_s)` | round-trip a writable point through values, verify each, restore original |
| `correlate(output_point, input_point, values, settle_s)` | ramp an output, record an input — check control linkages |

### Tier 4 — scenario / fault injection (heating-substation digital twin)
| Tool | Purpose |
|---|---|
| `sim_state` | live measurements, controls, alarms, active faults, scenarios |
| `inject_fault(component, on)` | fault/clear `circ_pump1`/`circ_pump2`/`makeup_pump`/`primary`; cascades to the dashboard |
| `set_control(name, value)` | set an operator setpoint/command (e.g. `building_load`, `supply_setpoint`, `circ_pump1_hz_sp`) |
| `load_scenario(name)` | `normal`, `morning_startup`, `peak_load`, `circ_pump_trip`, `makeup_vfd_fault`, `loss_of_primary` |
| `reset_sim` | reset to the `normal` scenario |

## FUXA dashboard generator (`build_dashboard.py`)

`python build_dashboard.py` generates a complete heating-substation HMI (schematic
with animated pumps, heat exchanger, pipes, live readouts, makeup tank, alarm
panel, real-time trend, operator buttons) and pushes it to FUXA via
`POST /api/project`, bound to the live `heat_station` device tags. This is the
Tier-2 build-automation capability (programmatic VOLTTRON device + FUXA dashboard).

## Write safety scaffold

Permissive by default (this is a simulator, not real hardware), but structured so
writes can be locked down later via env vars on the server process:

| Env var | Default | Effect |
|---|---|---|
| `VF_ALLOW_WRITES` | `1` | `0` = read-only mode |
| `VF_WRITE_ALLOWLIST` | *(empty)* | comma-separated point-key globs; empty = allow all |
| `VF_DRY_RUN` | `0` | `1` = log writes but don't send them |
| `VF_BRIDGE_URL` / `VF_FUXA_URL` / `VF_SIM_URL` | localhost defaults | service endpoints |
