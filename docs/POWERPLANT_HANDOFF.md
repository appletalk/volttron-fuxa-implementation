# Power Plant POC — Build Handoff

**Mission:** build an *exceptional* power-plant SCADA dashboard POC on the
already-proven VOLTTRON ↔ FUXA ↔ MCP pipeline. The heating-substation POC
(already built and verified) is the reference implementation — the power plant
is built the **same way**, just with a richer process model and a more ambitious
dashboard. The audience is an electrical engineer who builds power plants, so
lean into the **electrical generation + grid** side as much as the thermal side.

> This doc is self-contained, but four auto-memory files also persist across
> context clears and carry the gory details: `project-state`, `mcp-server`,
> `heat-station-poc`, `powerplant-next`. Read `heat-station-poc` especially — it
> has the hard-won FUXA format knowledge. This handoff recaps the essentials so
> you don't have to re-derive anything.

> **Session shape:** open with an **`ultracode` design workflow** — fan out
> parallel power-plant design proposals + research (real plant HMIs, credible
> physics, the electrical details an EE will scrutinize, FUXA's gauge set) + a
> realism review, then synthesize one strong spec. After that, **build
> interactively** (normal session): the build/verify loop is serial, stateful,
> single-live-stack and visually iterative — multi-agent can't parallelize it and
> can't judge the aesthetics, so don't keep ultracode on for the build. Optionally
> close with a quality/completeness review workflow. (Token cost is higher for the
> workflow phases; they're worth it for the spec, not for the mechanical build.)

---

## 0. The one-paragraph mental model

A Python **simulator** (`modbus-sim`) runs a coupled physics model and serves it
over **Modbus TCP** + a small **control API**. **VOLTTRON** polls the Modbus
device and republishes every point on its bus. The **fuxa-bridge** (a VOLTTRON
agent) exposes those points as REST/WebSocket. **FUXA** (a forked SCADA UI) reads
them through a custom `Volttron` device connector and renders a dashboard. A
local **MCP server** lets Claude drive the whole thing conversationally
(read/write points, run scenarios, inject faults). Everything is Docker
Compose. A fault injected in the sim cascades all the way to the dashboard.

```
modbus-sim ──Modbus──> VOLTTRON ──bus──> fuxa-bridge ──WS/REST──> FUXA dashboard
   ▲  control API :5021                      :8080                    :1881
   └──────────────────────── MCP server (Claude) ──────────────────────┘
```

---

## 1. Quickstart (verify the existing stack first)

```bash
cd /home/keith/development/volttron-fuxa-implementation
docker compose up -d            # modbus-sim, volttron, fuxa
# wait ~50s for VOLTTRON to boot the platform-driver + historian + bridge
curl -s localhost:8080/api/platform | python3 -m json.tool   # health
```

- FUXA dashboard: <http://localhost:1881/home>
- Bridge gateway: `:8080` (`/api/points`, `/api/devices`, `/api/platform`, `/api/history`, `PUT /api/points`)
- Sim control API: `:5021` (`/api/sim/state`, `/scenario`, `/fault`, `/point`, `/reset`)
- MCP: registered in `.mcp.json` (server `volttron-fuxa`, preauthorized in `.claude/settings.local.json`). It's already enabled; just call its tools. If a fresh clone: `cd mcp && python3 -m venv .venv && .venv/bin/pip install -r requirements.txt`.

**Regenerate the FUXA dashboard after editing the generator:**
```bash
cd mcp && ./.venv/bin/python build_dashboard.py     # POSTs a whole project to FUXA
```
**Verify visually** with the Playwright MCP tools: `browser_navigate` to
`http://localhost:1881/home`, wait ~30s for the device to reconnect + charts to
settle, `browser_take_screenshot`, then `Read` the PNG. Drive the plant live with
the `volttron-fuxa` MCP tools (`load_scenario`, `inject_fault`, `set_control`).

**Gotcha:** the VOLTTRON container entrypoint is NOT idempotent on a plain
`docker restart` (stale `VOLTTRON_HOME` → `conflicting identity` errors). Always
`docker compose down && up` (or `docker compose up -d --force-recreate volttron`)
for a clean platform. A host reboot breaks it until a clean recreate. **Consider
fixing this early** (clear `$VOLTTRON_HOME/run` + auth/keystore on boot, or
guard the agent installs) — it'll bite during iteration.

---

## 2. The five files you touch (the whole pipeline)

| Concern | File | What to do for the power plant |
|---|---|---|
| Process model + control API | `modbus-sim/sim.py` | **Add** the power-plant `Plant` as a *second* slave alongside the substation (see §3b). Per-plant control API. |
| VOLTTRON point map | `volttron/config/power_plant.registry.csv` + `device.power_plant.json` | New registry (one row per point) + device config, `slave_id: 2`. |
| VOLTTRON wiring | `volttron/entrypoint.sh` | **Add** a second `vctl config store` for the power-plant device + csv (keep the substation one). |
| FUXA dashboard generator | `mcp/build_dashboard.py` (+ `powerplant_dashboard.py`) | The big one — emit BOTH devices + BOTH views + combined charts in one project (§3b). Reuse the widget helpers. |
| MCP tools | `mcp/server.py` | Add a `plant` arg to the scenario/fault/control tools (→ `/api/sim/<plant>/…`). |

Rebuild images after editing: `docker compose build modbus-sim` / `build volttron`
then `up -d --force-recreate <svc>`. The dashboard generator needs no rebuild
(it just POSTs to FUXA).

---

## 3. The recipe (copy the substation, change the domain)

1. **Model** → add a second `Plant`-style class in `sim.py` *alongside* the
   substation (§3b — two slaves, don't delete the substation): define `INPUTS`
   (read-only measurements → Modbus *input* registers), `HOLDING` (writable
   setpoints/commands → *holding* registers, with defaults), `DISCRETE`
   (alarm/status bits → *discrete inputs*). Implement `step()` (coupled physics,
   first-order lag toward targets, 1 Hz). Define `SCENARIOS` (named presets of
   controls + faults). Move the control endpoints to per-plant routes.
2. **Registry** → one CSV row per point: `Volttron Point Name, Modbus Register,
   Writable, Point Address, Units, Notes`. Read-only analog = `>H`,`FALSE`;
   writable analog = `>H`,`TRUE`; bool = `BOOL`. The address is the register
   index *within its space* (input/holding/discrete each start at 0).
3. **Entrypoint** → store the device under `devices/campus/<site>/<device>` and
   the csv as `<device>.csv`.
4. **Generator** → build the FUXA device (Volttron type, `property.address =
   http://volttron:8080`, one tag per point) + the schematic view + `charts`,
   then `POST /api/project`.
5. **MCP** → update the scenario/fault/control tool docstrings + payloads to the
   plant.
6. **Verify live** via Playwright + the MCP, exactly like the substation.

**Units rule (important):** keep everything **integer-native** (°C, bar, MW,
MVAR, kV, Hz, rpm, t/h, %). Modbus registers are 16-bit ints and FUXA tag scaling
is fiddly — avoid decimals. For values needing one decimal, store ×10 only if you
*must*, and prefer a coarser unit (e.g. kPa not MPa, rpm not krpm). The
substation proved integer-native reads cleanly everywhere.

---

## 3b. Run BOTH plants at once — two devices, two views (DO THIS)

**Don't replace the substation — add the power plant alongside it.** One Docker
stack hosts both, both stay live, and you switch between them in FUXA's nav. The
substation also stays as a working reference. Changes vs §3:

- **Sim** (`modbus-sim/sim.py`): run two `Plant` instances on two Modbus slaves —
  `ModbusServerContext(slaves={1: substation_ctx, 2: powerplant_ctx})`. Step both
  each 1 Hz tick. **Namespace the control API per plant:**
  `POST /api/sim/<plant>/{scenario,fault,point,reset}` + `GET /api/sim/<plant>/state`
  where `<plant>` ∈ {`heat_station`, `power_plant`}. (Refactor the current flat
  `/api/sim/*` into this; update both the substation routes and the MCP.)
- **VOLTTRON**: add a second device — `volttron/config/device.power_plant.json`
  (`driver_config.slave_id: 2`, `registry_config: config://power_plant.csv`) +
  `power_plant.registry.csv`, and a second `vctl config store … platform.driver
  devices/campus/<site>/power_plant …` (+ the csv) line in `entrypoint.sh`. One
  `platform.driver` agent serves both devices — both poll `modbus-sim:5020`,
  distinguished by `slave_id`.
- **FUXA**: ONE project with **two devices** (`heat_station`, `power_plant`) and
  **two views**. `POST /api/project` is a *full replace*, so the generator must
  emit *both* devices + *both* views + the combined `charts[]` in a single
  project. Refactor `mcp/build_dashboard.py` into a shared assembler — e.g.
  `build_project([heat_station_spec, power_plant_spec])` that returns
  `{devices, views, charts}` and POSTs once — or keep `build_dashboard.py`
  (substation) + add `powerplant_dashboard.py`, and a tiny top-level script that
  merges their `(device, view, charts)` and POSTs. Use **stable view ids**
  (`v_heat_station`, `v_power_plant`) so nav references don't break.
- **MCP** (`mcp/server.py`): add a `plant` arg (default `"heat_station"`) to the
  scenario/fault/control tools that selects the `/api/sim/<plant>/…` route.
  read/write/history already work for either plant (points are addressed by full
  path).

**Switching between the two views — two mechanisms (do at least the button):**
1. **On-dashboard nav button (recommended, explicit):** an `svg-ext-html_button`
   whose event is `{type:"click", action:"onpage", actparam:"<targetViewId>"}`
   jumps to that view. Put a "→ POWER PLANT" button on the substation view and a
   "→ SUBSTATION" button on the plant view. (Action key string is `"onpage"`,
   exactly like `"onSetValue"` — the enum key, not its `shapes.event-…` value.)
2. **FUXA left-nav (hamburger) menu:** set
   `project.hmi.layout.navigation.items = [{text, view:"<viewId>", link:"",
   icon, image, permission:0}, …]` — one `NaviItem` per view (`view` = the view
   id). Set `project.hmi.layout.start` to the home view. The hamburger then lists
   both views.

---

## 4. Power-plant DESIGN (make this part exceptional)

A conventional **thermal (steam) power plant** is the richest, most legible
choice and showcases the electrical side the audience cares about:

```
 fuel ─> BOILER ═steam═> TURBINE ─shaft─> GENERATOR ─> XFMR ─[breaker]─> GRID
            ^                                 |excitation
            │                                 v
       feedwater <── CONDENSER <══════════════╯ (exhaust steam)
            ^                                        ^
       feed pump                                cooling water
```

### Suggested point list (~30; tune as you like)

**Electrical (lead with these — the headline story):**
- `generator_mw` (active power, MW) — the product
- `generator_mvar` (reactive power, MVAR)
- `generator_voltage_kv` (terminal voltage, kV; ~13–22 kV)
- `generator_frequency_hz` (×10 if you want 0.1 Hz resolution, else integer; ~50/60)
- `grid_frequency_hz`
- `excitation_current_a` (field current)
- `power_factor` (×100)
- `breaker_status` (0 open / 1 closed) — *status, read-only*
- `sync_status` (0 not-ready / 1 in-sync / 2 synced/closed)

**Thermal / mechanical:**
- `boiler_pressure_bar` (~160)
- `boiler_drum_level_pct`
- `main_steam_temp_c` (~540)
- `main_steam_flow_th` (t/h)
- `turbine_speed_rpm` (×0.1? prefer integer rpm; 3000/3600)
- `turbine_inlet_pressure_bar`
- `condenser_vacuum_kpa`
- `condenser_temp_c`
- `feedwater_flow_th`
- `feedwater_temp_c`
- `furnace_temp_c`
- `cooling_water_temp_c`

**Controls (writable holding regs):**
- `load_setpoint_mw` (governor/MW demand)
- `excitation_setpoint_kv` (AVR voltage target)
- `fuel_demand_pct` (firing rate)
- `feedwater_pump_cmd` (0/1) + `feedwater_pump_speed_pct`
- `breaker_cmd` (0 open / 1 close — operator close-to-grid)
- `turbine_trip_cmd` (0/1)

**Alarms (discrete inputs):**
- `high_boiler_pressure`, `low_drum_level`, `high_steam_temp`,
  `turbine_overspeed`, `gen_over_voltage`, `gen_under_frequency`,
  `loss_of_excitation`, `breaker_trip`, `condenser_vacuum_low`

### Coupled dynamics (what makes it feel real)

- `fuel_demand` → `furnace_temp` → `boiler_pressure` & `main_steam_flow` (lagged).
- steam flow + governor valve (driven by `load_setpoint_mw`) → turbine mechanical
  power → `generator_mw` **only when `breaker_status==1`** (synced). Before sync,
  turbine power spins the rotor → `turbine_speed_rpm` toward nominal.
- `generator_mw` vs grid: when synced, MW exports to grid (frequency held at grid
  value). When **islanded / breaker open**, a mismatch between mechanical power
  and load swings `generator_frequency_hz` (over-speed on load rejection).
- `excitation_setpoint` → `generator_voltage_kv` & `generator_mvar` (AVR).
- `feedwater_flow` must balance `main_steam_flow` or `boiler_drum_level` drifts →
  low-drum-level trip if it empties.
- `condenser_vacuum` affects efficiency; vacuum loss raises back-pressure → trip.
- A **turbine trip** drops mechanical power → load rejection → breaker opens →
  generator over-frequency spike → MW to 0.

### Scenarios (the demo gold — script a startup!)

- `cold_start` — boiler cold; fire up. A great **sequenced** demo: fuel on →
  pressure builds → steam → roll turbine to 3000 rpm → match voltage/freq →
  `breaker_cmd=1` to synchronize → ramp `load_setpoint_mw`. Each step visibly
  cascades.
- `normal` / `base_load` — steady at e.g. 250 MW.
- `load_ramp_up` / `peak` — push to max MW, watch fuel/steam/feedwater follow.
- `turbine_trip` — overspeed, breaker opens, MW→0, alarms.
- `loss_of_grid` (islanding) — breaker opens under load → frequency excursion.
- `loss_of_feedwater` — drum level falls → low-level alarm → trip.
- `loss_of_excitation` — voltage/MVAR collapse, alarm.
- `condenser_vacuum_loss` — back-pressure rises → trip.

### Dashboard ideas (aim higher than the substation)

The substation is one clean SVG view with readouts, animated proc-eng pumps,
semaphore alarms, single-line trends, and operator buttons. For the plant:

- **A real plant schematic**: boiler, superheater, turbine, generator,
  transformer, condenser, feedwater + cooling loops as colored pipes (steam =
  orange/red, water = blue), animated pumps/valves (proc-eng), the generator and
  breaker.
- **An electrical one-line strip**: GEN → breaker (animated open/closed) → bus →
  XFMR → GRID, with **big MW / MVAR / kV / Hz** numbers. This is the money shot
  for an electrical engineer.
- **Headline KPI tiles**: MW (huge), Frequency Hz, Terminal kV, Boiler bar.
- **A sync indicator**: a simple "IN SYNC / SYNCED / OFFLINE" lamp driven by
  `sync_status` ranges (grey/amber/green) — or get fancy with a synchroscope.
- **Trends**: MW output, frequency, boiler pressure, drum level (single-line each
  for reliability — see §5 chart notes).
- **Annunciator alarm panel** (the discrete alarms as semaphores).
- **Operator controls**: load setpoint buttons, excitation, **CLOSE BREAKER** /
  **TRIP** buttons (onSetValue), feedwater pump start/stop.
- **Consider multiple views** (FUXA supports many views in `hmi.views`): an
  *Overview*, an *Electrical one-line*, a *Boiler/Thermal* detail. Add a nav
  header. (Substation used a single view; the plant earns more.)

**To make it "exceptional," push the visuals:** explore FUXA's gauge library
beyond what the substation used — there are radial/dial gauges, switches, the
full proc-eng symbol set (valves, tanks, motors, etc.). Use real **radial gauges
for MW / Hz / kV** instead of plain text. Script the `cold_start` sequence as the
signature demo and capture it.

---

## 5. CRITICAL hard-won knowledge (do NOT re-derive — it cost a lot)

### FUXA project / view format (the dashboard generator)
- Push a whole project with `POST http://localhost:1881/api/project` (full
  replace). `GET /api/project` returns the current one to merge into.
- A **view** = `{id, name, type:"svg", profile:{width,height,bkcolor},
  items:{<id>:GaugeSettings}, variables:{} (may be empty), svgcontent}`.
  `svgcontent` is one `<svg width h xmlns... xmlns:svg xmlns:html>…</svg>`; every
  bound SVG element's `id` matches an `items` key.
- **Binding: `gauge.property.variableId` MUST equal the plain `tag.id`** — NOT a
  `deviceId^~^tagId` composite (that's a legacy placeholder form that only
  resolves for chart/graph lines). FUXA stores live values in
  `variables[tag.id]` and value/motor/semaphore `getSignals()` return
  `property.variableId` as-is.
- **Templates live in `FUXA/server/project.demo.fuxap`** (read with
  `encoding="utf-8-sig"` — it has a BOM). It contains real examples of every
  widget; copy their structure.
- **Widget types that work as-is** (set `item.type`):
  - `svg-ext-value` — numeric readout. `<g id type=…><text>##.##</text></g>`;
    `property.ranges:[{type:"unit",min,max,text:" units"}]`.
  - `svg-ext-proceng` — **pumps/valves/motors**. FUXA fills *every child node*
    with the matching range color, so put a status code (0/1/2) tag on it and
    `ranges:[{range,1,1,green},{range,2,2,red},{range,0,0,grey}]`. (The demo's
    `svg-ext-motor`/`svg-ext-valve` types are OLD — current FUXA only
    color-processes `svg-ext-proceng`/`svg-ext-shapes`/`svg-ext-ape`.) Draw a
    static (un-grouped) vane/detail *on top* if you want it to keep a fixed color.
  - `svg-ext-gauge_semaphore` — alarm lamp. `<g><ellipse…/></g>`;
    ranges color by 0/1.
  - `svg-ext-gauge_progress` — bar (tank level). **Child rects MUST be
    id-prefixed `A-`/`B-`/`H-`** or `processValue` throws on null and breaks the
    whole signal pipeline. `ranges:[{type:"minmax",min,max,style:[true,true],color}]`.
  - `svg-ext-html_button` — operator control. `event {type:"click",
    action:"onSetValue", actparam:"<value>"}` writes `property.variableId`.
    Inner `<BUTTON>` + `<rect>`.
  - `svg-ext-html_chart` — trend. **Host `<DIV>` id MUST start `D-`**, the
    `<foreignObject>` `H-`, or the chart never instantiates. `property =
    {id:<chartId>, type:"realtime1"}` → a project-level `charts[]` entry
    `{id, name, type, lines:[{id:tagId, name, label, device:<deviceNAME>, color,
    lineWidth}]}`. **`device` is the device NAME**, line `id` is the tag id.
- **Device/tag JSON shape** (template off the existing one): device
  `{id, name, enabled, type:"Volttron", property:{address:bridgeURL, port:null,…},
  polling, tags:{<tagId>:{id, name, type:"Real", address:"<pointkey>",
  daq:{enabled:false,…}}}}`. Tags keyed by `tag.id`. **Keep `daq.enabled=false`**
  for chart tags (DAQ-on makes realtime charts mix in 8h of UTC historian data →
  wrong-looking x-axis).
- **FUXA realtime chart (v1.3.3) — the knobs that make it work well:**
  - **PIN the y-axis range** via the chart `property.options`:
    `scaleY1min/scaleY1max` (left axis), `scaleY2min/scaleY2max` (right axis).
    FUXA reads these into uPlot's scales. This is essential — it gives **defined,
    labelled axes** AND stops uPlot's auto-range from collapsing on flat data,
    which is what otherwise **prunes multi-line series**. (Without pinning the
    line clips off the top and the y-scale won't render.)
  - **Multi-line / dual-axis works** once pinned: set each line's `yaxis` (1 =
    left, 2 = right). Lines with `yaxis>1` plot against scale 2 and FUXA prefixes
    the legend label with `Y1 -`/`Y2 -`. e.g. supply/return temp on Y1, flow on
    Y2 in one chart.
  - **Make the chart tall** (~180px+). uPlot's title + x-axis + legend eat a
    short chart, leaving no plot area (line clips). Put trends in a bottom band
    with real height, not a 150px slot.
  - **Kill the idle-cursor legend junk** (epoch-0 "1969" time row + "--" values)
    by injecting CSS in the svgcontent:
    `<style>.u-legend .u-series:first-child{display:none!important}
    .u-legend .u-value{display:none!important}</style>`. Legend label = line's
    `label`.
  - Keep `daq.enabled=false` on chart tags (DAQ pulls 8h of UTC history → wrong
    x-axis). Exact instantaneous numbers still belong in readout tiles.
- Server resolves a tag by searching all devices' `getTagProperty(tagId)`; the
  Volttron connector keys `data.tags` by `tag.id`. Writes go via socket
  `device-values` `{cmd:'set', var:{source:deviceId, id:tagId, value}}` — the MCP
  uses `PUT /api/points` on the bridge instead, which is simpler.

### VOLTTRON (modular v10 — all RC packaging)
- The coherent pinned set is in `volttron/requirements.txt` (volttron 10.0.5rc4,
  platform-driver 0.2.1rc2, base-driver 0.2.1rc2, modbus-driver 0.2.1rc0,
  sqlite-historian 0.2.1rc1). Don't "upgrade" — there are two incompatible lines
  and only this one has a working Modbus driver.
- `vctl install` agents by **bare name** (no `==version`) and **no `--force`**
  (it maps to `pip --force-reinstall` → pulls the incompatible 2.0 line). The
  right versions are pre-installed via `requirements.txt`; bare `pip install`
  is then a no-op.
- Modbus read mapping: read-only analog ← *input registers* (FC4); writable
  analog ← *holding* (FC3); read-only bool ← *discrete inputs* (FC2); writable
  bool ← *coils* (FC1). The sim must place each point in the matching space.
- Container runs as user `volttron` (uid 1000); refuses root. Historian sqlite at
  `/home/volttron/.volttron/data/historian.sqlite` on the `volttron-data` named
  volume.

### MCP server
- `mcp/server.py` (FastMCP). Tools across ops (`read_point`/`write_point`/
  `query_history`/`platform_status`/`list_*`), commissioning (`write_and_verify`,
  `checkout_point`, `correlate`), and the plant tier (`sim_state`,
  `inject_fault`, `set_control`, `load_scenario`, `reset_sim`). It talks to the
  bridge `:8080`, FUXA `:1881`, sim `:5021`. Write safety scaffold via env
  (`VF_ALLOW_WRITES`, `VF_WRITE_ALLOWLIST`, `VF_DRY_RUN`) — permissive by default.
- A new MCP server / changed tool *signatures* only take effect after Claude Code
  reconnects. Editing tool *bodies* is picked up by relaunching the server
  process, but in practice: regenerate, and if you changed tool defs, tell the
  user to reconnect.

### The generator's structure (reuse it)
`mcp/build_dashboard.py` has tidy widget builders — `value`, `motor` (proc-eng),
`semaphore`, `progress`, `button`, `chart`, `kpi`, plus static `text`/`box`/`pipe`
— each returning `(svg, item)` and appending to global `svg_parts`/`items`. A
`uid()` counter makes unique ids. The `LAYOUT` section places everything on a
1280×800 dark canvas; `main()` assembles svg + items + charts + device and POSTs.
Copy this file as the power-plant generator and swap the layout + points.

---

## 6. Definition of "done / exceptional"

- `docker compose up` → a power-plant dashboard at `:1881` that is **live**
  (values stream), **animated** (pumps/valves/breaker change state/color), and
  **MCP-driven** (a `cold_start` or `turbine_trip` scenario visibly cascades).
- The electrical story is front and center: MW/MVAR/kV/Hz, breaker, sync.
- A signature demo: *"Claude, cold-start the plant"* → boiler fires, turbine
  rolls, syncs to grid, breaker closes, load ramps — captured in screenshots.
- Honest trade-offs documented (the FUXA chart limits, the entrypoint restart
  gotcha).

Build it on these rails and it'll come together fast. Good luck — make it sing.
