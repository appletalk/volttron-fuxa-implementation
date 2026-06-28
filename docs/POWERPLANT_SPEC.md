# Solar PV + BESS Plant POC — Build-Ready Specification (v2.0, review-reconciled)

This is the single spec to build from. It supersedes the three design proposals **and** v1.0; every conflict and every adversarial-review blocker/major is resolved below with a concrete decision. It plugs into the existing two-device stack (substation = Modbus slave 1, kept; solar plant = Modbus slave 2, new) and the five-file pipeline in `docs/POWERPLANT_HANDOFF.md`.

**Top-level architecture decision (resolves the central blocker shared by all three reviews):** the BESS is **DC-coupled** (battery on the array DC bus via a DC/DC converter, sharing the four inverter blocks), **not** AC-coupled. This is the only spec change that simultaneously (a) lets the battery charge from DC **clipping** energy so SOC genuinely rises at peak, (b) makes the auto-firming law coherent, and (c) is itself a real, increasingly-common utility topology. The single-line, dispatch law, and clipping math below all reflect DC-coupling.

Files touched (per handoff §2):
- `modbus-sim/sim.py` — add `SolarPlant` as slave 2 alongside the substation; per-plant control API.
- `volttron/config/power_plant.registry.csv` + `device.power_plant.json` (slave_id 2).
- `volttron/entrypoint.sh` — second `vctl config store`.
- `mcp/powerplant_dashboard.py` + merge into `build_dashboard.py` (one project, two devices, three plant views).
- `mcp/server.py` — `plant` arg + per-plant routes.

---

## 1. Overview

**Plant identity.** `Sunfield Solar — 100 MWac utility-scale PV + DC-coupled BESS`, single-axis tracked, with a 25 MW / 100 MWh (4-hour) lithium battery on the DC bus.

| Property | Value |
|---|---|
| AC rating (POI) | **100 MWac** |
| DC array (STC) | **125 MWdc** (ILR = 1.25) |
| Inverter blocks | **4 × 25 MWac** (31.25 MWdc each) |
| BESS | **25 MW / 100 MWh**, **DC-coupled**, usable SOC 10–90 %, RTE 0.88 (η_ow 0.938) |
| Collector bus | 34.5 kV (LV→MV via inverter pad-mounts, MV→115 kV via main GSU) |
| Step-up / POI | **115 kV**, 60.0 Hz (US) |
| Reactive envelope | **±33 MVAR**, MVA-limited D-curve `S_max = 105.3 MVA` (inverter headroom **+ 25 MVA BESS PCS**); STATCOM at night |
| Trackers | single-axis ±60° |

**Headline electrical story (what an EE reads in two seconds).** Net export `plant_active_power_mw` at the 115 kV POI tracks the sun and **clips flat at 100 MW** while DC keeps climbing (ILR 1.25). At peak, the **DC-coupled battery charges from the energy that would otherwise be clipped, so SOC rises**; on a passing cloud, the **battery discharges through the shared inverters to firm export.** Grid Hz, POI kV, MVAR/PF, and the POI breaker are all live and protection-grade, with **IEEE 1547-2018 / PRC-024 ride-through** (no nuisance trips).

**Honest firming claim (resolves the "oversold money-moment" blocker).** A 25 MW battery can fully firm a shortfall of **≤25 MW**. Therefore:
- The **signature `day_in_the_life` cloud is a shallow dip to ~720 W/m²** (PV stays ≥ ~80 MW; the ~20 MW gap is fully covered → **Plant MW genuinely holds flat at 100**).
- The **deep G→300 W/m² cloud is kept for `cloud_passing` and `low_soc`**, reframed honestly as **dip-shaving** (Plant MW sags 100 → ~75 instead of 100 → ~35), with `low_soc` as the negative-space proof (no battery → sag follows the sun).

**Framing decision.** We graft Proposal 2's *operator-cockpit information design* onto Proposal 1's *electrical-one-line rigor* and stage it with Proposal 3's *side-by-side signature trends*. The entry view leads with the four numbers a plant engineer checks first (Plant MW, Grid Hz, POI kV, SOC) as big radial dials **and** carries a live electrical single-line — electrical-led AND tells the day-in-the-life story on one screen. EE drill-downs (full one-line / PQ envelope, BESS energy bookkeeping) are one nav button away.

**View set.** Stable entry id `v_power_plant` (required), plus two drill-downs. The "TWO views" platform line is the floor (one per device); the handoff explicitly invites multiple plant views, and all three proposals used three — we build three:

| id | name | role |
|---|---|---|
| `v_power_plant` | Solar Plant — Overview & Day-in-the-Life | **ENTRY.** KPI dials + animated single-line + signature trends + annunciators |
| `v_pp_oneline` | Electrical One-Line & POI Metering | EE drill-down: full one-line, PQ envelope, reactive/breaker controls |
| `v_pp_bess` | BESS Detail | SOC energy bookkeeping, signed power, dispatch controls |

(`v_heat_station` stays as-is.)

---

## 2. Point list / register map

Internally consistent across `sim.py` (`INPUTS`/`HOLDING`/`DISCRETE`), `power_plant.registry.csv`, and the FUXA tags. Indices are per-space, 0..N. Electrical leads. **Scaling/sign conventions** are summarized after the tables; read the **Display convention** note in §5 — scaled/offset points live on dials/charts, integer-native points on numeric tiles.

### 2a. INPUT registers (FC4, read-only measurements) — registry `>H`, FALSE

| idx | name | units | scaling | note |
|---|---|---|---|---|
| 0 | `plant_active_power_mw` | MW | x1, 0..110 | **Headline.** Net real export at POI = clamp(inverter_ac_power, 0, P_setpoint). |
| 1 | `plant_reactive_power_mvar` | MVAR | **+50 offset** (store Q+50; 17..83 → −33..+33) | + export VARs / − absorb. Clamped to the D-curve (§3-9). |
| 2 | `poi_voltage_kv` | kV | **x10** (1150 = 115.0) | 115 kV nominal; moves ±~2 kV with MVAR. |
| 3 | `grid_frequency_hz` | Hz | **x10** (600 = 60.0) | Grid-driven; dial band 59.0–61.0 (see §5). |
| 4 | `power_factor` | — | **x100** (98 = 0.98) | magnitude P/√(P²+Q²); **guarded to 100 (PF 1.00) when S < 0.5 MVA** (night). |
| 5 | `poi_current_a` | A | x1, 0..600 | I = S·10⁶/(√3·V_poi). ~500 A at full real export, ~529 A at full Q. |
| 6 | `main_breaker_status` | code | x1 (0 open / 1 closed) | **In INPUT (not discrete)** so the proceng breaker symbol recolors and the AC pipe animation stops when open. |
| 7 | `irradiance_wm2` | W/m² | x1, 0..1200 | The "fuel" / external driver. |
| 8 | `pv_dc_power_mw` | MW | x1, 0..125 | **PV-array DC only** (after temp derate). Climbs past 100 = the clip story. |
| 9 | `inverter_ac_power_mw` | MW | x1, 0..100 | Aggregate AC at inverter terminals (PV **+ DC-coupled battery**); **clips flat at 100**. |
| 10 | `clipping_loss_mw` | MW | x1, 0..30 | max(0, P_dc_net·η_inv − cap). **DC-coupled battery suppresses this at peak until SOC≥90.** |
| 11 | `inverter_efficiency_pct` | % | x1, ~96..99 | η from a **real part-load curve** (~98.5 mid/high; sags only below ~15–20 % load). **Not** P_ac/P_dc — clipping lives in `clipping_loss`. |
| 12 | `ambient_temp_c` | °C | x1 | Diurnal driver for NOCT (25 + 8·sin → peaks ~33 °C at solar noon). |
| 13 | `panel_temp_c` | °C | x1, 0..80 | T_amb + 0.03125·G; **~56 °C at the (G=1000,T_amb=25) reference, ~66 °C at signature solar noon (G=1050,T_amb=33).** |
| 14 | `tracker_angle_deg` | deg | **+60 offset** (0..120 → −60..+60) | E(−)→noon(0)→W(+). Cosmetic + small gain. |
| 15 | `performance_ratio_pct` | % | x1, **~80..93** | PR_inst = P_ac/((G/1000)·125)·100; **~80 % at clip, rises to ~90–93 % at cool part load.** |
| 16 | `inverter1_status` | code | x1 (0 off/1 run/2 fault) | Drives proceng symbol #1. |
| 17 | `inverter2_status` | code | x1 | Symbol #2. |
| 18 | `inverter3_status` | code | x1 | Symbol #3. |
| 19 | `inverter4_status` | code | x1 | Symbol #4. Trip one → AC cap −25 MW. |
| 20 | `battery_soc_pct` | % | x1, 0..100 (usable 10..90) | Big Donut; **rises at peak (clip capture), falls on cloud discharge**. |
| 21 | `battery_power_mw` | MW | **+50 offset** (25..75 → −25..+25) | + discharge / − charge. Drives pipe via `bess_status`. |
| 22 | `battery_temp_c` | °C | x1, 0..60 | First-order thermal; settles ~36 °C at full 0.25C, never false-trips. |
| 23 | `bess_status` | code | x1 (0 idle/1 discharge/2 charge/3 fault) | Drives battery symbol + charge/discharge lamp **+ pipe direction**. |
| 24 | `daily_energy_mwh` | MWh | x1 | Σ P_plant·dt·K_comp/3600 (compression-scaled, §3-8), reset at sunrise. |

### 2b. HOLDING registers (FC3, writable setpoints) — registry `>H`, TRUE

| idx | name | units | scaling | default | note |
|---|---|---|---|---|---|
| 0 | `power_setpoint_mw` | MW | x1, 0..100 | **100** | Export cap / curtailment. |
| 1 | `mvar_setpoint` | MVAR | **+50 offset** (17..83 → −33..+33) | **50** (0 MVAR) | Reactive dispatch; clamped to D-curve. |
| 2 | `voltage_setpoint_kv` | kV | **x10** | **1150** | Optional closed-loop V mode (plant trims Q toward target). |
| 3 | `bess_mode` | code | x1 (0 auto/1 force-charge/2 force-discharge) | **0** | Dispatch mode. |
| 4 | `bess_power_cmd_mw` | MW | **+50 offset** (25..75 → −25..+25) | **50** (0) | Manual battery cmd; honored only in force modes. |
| 5 | `breaker_cmd` | code | x1 (0 open / 1 close) | **1** | POI breaker command → `main_breaker_status`. |
| 6 | `tracker_enable` | code | x1 (0 stow / 1 track) | **1** | 0 stows panels (angle→0), small yield loss. |

### 2c. DISCRETE INPUTS (FC2, read-only alarm bits) — registry `BOOL`, FALSE

| idx | name | note |
|---|---|---|
| 0 | `inverter_fault` | any `inverterN_status == 2`. |
| 1 | `grid_over_voltage` | poi_voltage > 1.05·115 kV (store > 1207); reachable only via `grid_fault`. |
| 2 | `grid_under_frequency` | grid_frequency < 59.5 Hz (store < 595). **Ride-through indication, not a trip.** |
| 3 | `breaker_trip` | protective trip on **sustained/severe** excursion only (§3-10); forces `main_breaker_status=0`, export→0. |
| 4 | `battery_over_temp` | battery_temp > 45 °C. |
| 5 | `low_soc` | SOC ≤ 12 % (early-warning). |
| 6 | `dc_ground_fault` | DC-side insulation fault (fault-injected). |
| 7 | `comms_loss` | plant-controller watchdog (fault-injected). |
| 8 | `curtailment_active` | real PV thrown away after the battery is full/limited. |

**Scaling conventions (apply on write in `sim.py`):** signed-via-`+50` → `mvar`, `battery_power`, `bess_power_cmd`; `tracker_angle` via `+60`; `x10` → `poi_voltage`, `grid_frequency`, `voltage_setpoint`; `x100` → `power_factor`; everything else `x1`. Decode the same offsets when reading holding regs back in `step()`.

Total: 25 inputs + 7 holding + 9 discrete = 41 points.

---

## 3. Physics model (1 Hz)

`step()` runs every 1 s. Measurements lag toward computed targets with a first-order filter `x += (target − x)·k`, **k = 0.2** (τ ≈ 5 s) so dials glide; status/alarm bits and the clip are instantaneous.

**Validated constants (PVWatts/NREL-grade — literals to put in `sim.py`):**
```
P_AC_RATED = 100.0   # MW
P_DC_STC   = 125.0   # MW  (ILR 1.25)
N_INV      = 4       # blocks, 25 MWac / 31.25 MWdc each
ETA_INV    = 0.985
GAMMA_P    = -0.0037 # /degC  (-0.37%/degC)
NOCT_K     = 0.03125 # (NOCT-20)/800, NOCT=45
E_CAP      = 100.0   # MWh
P_BATT_MAX = 25.0    # MW (0.25C)
SOC_LO, SOC_HI = 10.0, 90.0   # usable window %
ETA_OW     = 0.938   # one-way = sqrt(RTE 0.88)
S_MAX      = 105.3   # MVA  (inverter headroom + 25 MVA BESS PCS)
Q_MAX      = 33.0    # MVAR (0.95 PF at 100 MW)
V_NOM      = 115.0   # kV
KV_PER_MVAR= 0.06    # kV/MVAR grid stiffness (SCR ~19)
F_NOM      = 60.0    # Hz
DT         = 1.0     # s
K_SLOW     = 0.01    # EMA gain for the slow PV baseline (~100 s tau)  -- ramp-smoothing auto law
K_COMP     = 180.0   # demo time-compression (sim-s -> real-h equiv) for daily_energy
TH_GAIN    = ...     # battery thermal: set so 0.25C steady-state settles ~36 C (see step 7)
TH_DECAY   = ...
```

**Coupled update law (strict order):**

**(1) Drivers.** `G = irradiance_wm2`, `T_amb`, baseline `f`, baseline `V` come from the active scenario (daily curve / cloud dip / fault). Clamp `G` to 0..1200.

**(2) Tracker.** `tracker_angle = tracker_enable ? clamp(-60 + 120·(day_t/T_day), -60, 60) : 0`. Tracking adds a small gain `g_track = tracker_enable ? 1.0 : 0.97` applied to G_eff.

**(3) Cell temp (NOCT).** `T_cell = T_amb + 0.03125·G`. (Reference G=1000, T_amb=25 → **56.25 °C**; signature solar noon G=1050, T_amb≈33 → **~66 °C**.)

**(4) PV DC power w/ derate.** `n_ok = count(inverterK_status == 1)`.
`P_dc_pv = (G·g_track/1000)·125·(1 + (−0.0037)·(T_cell − 25))·(n_ok/4)`, clamp ≥ 0.
(Reference: derate = 1 − 0.0037·31.25 = **0.884** → P_dc_pv = **110.5 MW**. Signature noon: derate ≈ 0.849 → **~111 MW**.)

**(5) DC-coupled BESS dispatch (auto = ramp-rate smoothing + clip-capture).** This replaces the degenerate "fill-to-nameplate" law that all three reviews flagged.

Maintain a **slow EMA baseline** of the available (unclipped) PV AC:
```
P_ac_pv_unclipped = P_dc_pv · ETA_INV
P_base += (P_ac_pv_unclipped − P_base) · K_SLOW      # tracks the diurnal trend, ignores fast clouds
```
Auto firming target = the slow baseline, capped at nameplate:
```
target_export = min(P_base, P_setpoint)              # auto holds the slow trend, not the instantaneous value
P_dc_needed   = target_export / ETA_INV
P_batt_dc     = clamp(P_dc_needed − P_dc_pv, −P_BATT_MAX, +P_BATT_MAX)   # + discharge, − charge
```
Behavior this produces (all honest):
- **Slow diurnal ramp:** P_ac_pv ≈ P_base ⇒ `P_batt_dc ≈ 0` (battery idles, SOC preserved for the demo).
- **Peak / clip:** P_ac_pv_unclipped (≈109) > cap (100) ⇒ `P_batt_dc` **negative (charges ~9 MW from the DC that would be clipped)** while SOC<90 ⇒ **SOC rises, clipping_loss → ~0**.
- **Fast cloud:** P_dc_pv collapses but P_base is slow ⇒ `P_batt_dc` jumps **positive (discharge)** to firm export.

Force modes (override auto):
- `bess_mode==1` (force-charge): `P_batt_dc = −min(P_BATT_MAX, charge_headroom)`.
- `bess_mode==2` (force-discharge): `P_batt_dc = bess_power_cmd_mw` (signed, clamped ±25).

Limits: if `SOC ≤ 10` block discharge (`P_batt_dc>0 → 0`); if `SOC ≥ 90` block charge (`P_batt_dc<0 → 0`); if `battery_over_temp`, derate `|P_batt_dc|` to 60 %. Optional gentle SOC-restoration term pulls SOC toward 50 % only when `|P_batt_dc|≈0`.
`bess_status`: 3 if fault, else 1/2/0 by sign of `P_batt_dc`. `battery_power_mw = P_batt_dc` (store +50).

**(6) Inverter + hard clip (shared by PV and battery on the DC bus).** If `main_breaker_status == 1`:
```
P_dc_net = P_dc_pv + P_batt_dc            # battery charge (−) subtracts DC, discharge (+) adds DC
P_ac_cap = 100 · (n_ok/4)                 # losing a block drops the AC cap
inverter_ac_power = clamp(min(P_dc_net·ETA_INV, P_ac_cap), 0, 100)
clipping_loss     = max(0, P_dc_net·ETA_INV − P_ac_cap)
```
Else (breaker open) `inverter_ac_power = 0`.
(Reference peak, SOC<90: P_batt_dc≈−9 ⇒ P_dc_net≈101.5 ⇒ AC=100, **clipping_loss≈0**; once SOC≥90, P_batt_dc=0 ⇒ P_dc_net=110.5 ⇒ AC=100, **clipping_loss≈8.8 MW**.)
`inverter_efficiency_pct` = a **part-load curve of its own AC loading** (≈98.5 % for load ≥ ~20 %, drooping to ~96 % below ~15 % load), stored x1, clamped 96..99 — **independent of clipping**.

**(7) SOC integration with RTE — `/3600` PRESERVED (the unanimous blocker fix).**
```
discharge (P_batt_dc>0): SOC -= (P_batt_dc / ETA_OW) · DT / (3600 · E_CAP) · 100
charge   (P_batt_dc<0): SOC += (|P_batt_dc| · ETA_OW) · DT / (3600 · E_CAP) · 100
```
Sanity: at 25 MW discharge, `25/0.938/3600 = 0.0074 %/s` ⇒ 80 % usable / 0.0074 ≈ **10,800 s = 3.0 h**. (The v1.0 `…/E_CAP·100` shorthand that dropped `/3600` was wrong by 3600× and is deleted.)
**Battery thermal (first-order, calibrated):** `battery_temp += TH_GAIN·|P_batt_dc| − TH_DECAY·(battery_temp − (T_amb+5))`, with `TH_GAIN`/`TH_DECAY` chosen so the **steady state at full 0.25C settles ~36 °C** (comfortably below the 45 °C trip) — not the per-tick `+0.02·|P|` that false-tripped in ~40 s.

**(8) Plant export / curtailment.**
```
P_plant = clamp(inverter_ac_power, 0, P_setpoint)
# curtailment: PV surplus the battery can't absorb (SOC≥90) and the cap can't pass
curtailment_active = (clipping_loss > 0.5) and (SOC >= 90 or P_setpoint < P_ac_pv_unclipped)
daily_energy_mwh  += P_plant · DT · K_COMP / 3600      # K_COMP maps compressed sim-time to real MWh
```
`daily_energy` resets to 0 at the sunrise event. (K_COMP makes the counter read realistic ~hundreds of MWh/day instead of a few MWh over the compressed demo day; label the tile "MWh (demo-scaled)".)

**(9) Reactive / voltage / PF — MVA-limited D-curve (replaces the `0.329·P` clamp).**
```
Q_limit = min(Q_MAX, sqrt(S_MAX² − P_plant²))     # full ±33 down to low P; STATCOM at night
Q = clamp(mvar_setpoint, −Q_limit, +Q_limit)
# optional V-mode: if enabled, trim Q toward voltage_setpoint_kv
poi_voltage_kv = 115 + 0.06·Q                      # store ×10
S = sqrt(P_plant² + Q²)
power_factor = (S < 0.5) ? 100 : round(100 · P_plant / S)   # night guard -> PF 1.00
poi_current_a = round(S·10⁶ / (√3 · poi_voltage_kv·1000))
performance_ratio_pct = round(100 · inverter_ac_power / max((G/1000)·125, 1))   # ~80 at clip, up to ~93 cool part-load
```
(At P=100, Q=0, V=115 → **502 A**; at full Q=33, S=105.3 → **529 A**. At P=0 at night the D-curve still allows ±33 MVAR STATCOM — credited to the BESS PCS + inverters in reactive mode.)

**(10) Frequency + ride-through (IEEE 1547-2018 / PRC-024 — replaces the instantaneous nuisance trip).**
```
f = 60.0 + noise(±0.02)        # store ×10
```
- **Continuous operation:** 59.5–60.5 Hz (and 0.9–1.1 pu V).
- **Mandatory ride-through:** below 59.5 Hz the plant **must ride through** — `grid_under_frequency` lamp lights as an *indication*, export continues; for deeper excursions the plant uses **momentary cessation** (briefly curtails P, then restores) rather than opening the breaker.
- **Trip only on sustained/severe:** `breaker_trip` latches **only** on a sustained excursion beyond the ride-through clock or a severe limit (e.g. `f < 58.5` or `> 61.2`, or V > 1.1 pu sustained). On trip, `main_breaker_status=0`, export→0.
`grid_fault` moderate variant drives `f→59.3` (rides through, lamp on, no trip); severe variant drives a sustained/deep excursion that latches `breaker_trip`.

**(11) Alarms (recompute each tick from thresholds).** `inverter_fault`, `grid_over_voltage`, `grid_under_frequency`, `battery_over_temp`, `low_soc`, `curtailment_active` per their definitions in §2c; `breaker_trip`, `dc_ground_fault`, `comms_loss` set by fault flags / the ride-through logic. `breaker_trip` (or `breaker_cmd==0`) forces `main_breaker_status=0`.

**Daily irradiance curve (driver).** Half-sine over the day clock `day_t ∈ [0, T_day]`:
`G_base(t) = max(0, 1050·sin(π·day_t/T_day))`, with `T_amb = 25 + 8·sin(...)` (peaks ~33 °C at solar noon). The `day_in_the_life` scenario advances `day_t` (compressed, T_day ≈ 4 min real) and overlays the **shallow signature cloud** at ~60 % of day (**G → ~720 for ~30 s** then recover) — a ~20 MW shortfall the 25 MW battery fully firms. Standalone beats (`sunrise`/`peak_sun`/`evening`/`cloud_passing`) set G targets/ramps directly.

---

## 4. Scenarios

Each = holding overrides + fault flags (+ irradiance driver), applied over defaults. Implemented in `SCENARIOS` exactly like the substation pattern, with an added scripted irradiance/temperature driver.

| name | overrides / faults | operator sees |
|---|---|---|
| **`day_in_the_life`** (signature) | scripted G + T_amb daily curve incl. **shallow cloud (G→~720)**; `bess_mode=0`, `power_setpoint=100`, `breaker_cmd=1`, `tracker_enable=1`; no faults | The whole arc on the entry view: **sunrise** (G 0→1050, trackers swing −60→0, Plant MW climbs, battery idle on the smooth ramp, AC pipe energizes, daily_energy counts); **peak** (DC trend overtakes 100, **AC pins flat at 100**, the **DC-coupled battery charges from the clip → SOC donut rises, clipping_loss≈0**, panel_temp ~66 °C, charge lamp/pipe); **shallow cloud** (G→~720, PV sags ~20 MW but **Plant MW holds flat at 100 — battery discharges ~20 MW**, SOC donut dips, discharge lamp/pipe); **evening** (G→0, trackers →+60 then stow, battery covers the shoulder then idles). |
| `sunrise` | G ramp 0→1000; `tracker_enable=1`, `breaker_cmd=1`, `bess_mode=0` | Plant MW and AC climb from 0; inverters off→run (grey→green); breaker closed/green; battery near-idle. |
| `peak_sun` | G≈1050, T_amb high, `power_setpoint=100`, `bess_mode=0` | DC>100, AC clipped flat at 100; **battery captures the clip (SOC rising, clipping_loss≈0) until SOC≥90, then clipping_loss climbs to ~10 MW and `curtailment_active` lights**; PR ~80 %, panel_temp ~66 °C. |
| `cloud_passing` | **deep** G dip 1000→300→1000 (~35 s), `bess_mode=0`, setpoint held | **Dip-shaving:** PV/AC sag hard, **battery saturates at +25 MW so Plant MW sags 100→~75 (not →~35) then recovers** — the honest "battery sharply softens a deep dip" beat; discharge lamp/pipe. |
| `curtailment` | `power_setpoint=60`, `bess_mode=0` | `curtailment_active` lit; surplus first charges BESS (SOC rises) then real PV is curtailed; Plant MW pins at 60 under full sun. |
| `evening` | G down-ramp 1000→0; `bess_mode=0` | PV fades, trackers →+60 then stow, battery covers the shoulder (discharge, SOC declines) then idles. |
| `inverter_trip` | fault `inverter2` → `inverter2_status=2` | symbol #2 red, `inverter_fault` lamp; **AC cap drops to 75 MW (DC-coupling shares the inverters, so the battery cannot push export past the reduced cap)** → **Plant MW steps down to ~75 (visible step), surplus DC charges the battery**; the −25 MW block loss is real and shown on both Plant MW and the inverter_ac trend. |
| `grid_fault` | moderate: perturb `grid_frequency`→59.3 (rides through); severe: sustained/deep excursion → `breaker_trip` | Moderate: Hz dial leaves the green band, `grid_under_frequency`/`grid_over_voltage` indicate, **plant rides through (momentary cessation), export continues**. Severe: **breaker auto-opens (red gap), export → 0**. |
| `bess_dispatch` | `bess_mode=2`, `bess_power_cmd=+25` (store 75) | Battery injects +25 through the inverters (clipped to the 100 MW cap), SOC falls steadily, discharge lamp/pipe. |
| `low_soc` | pre-drain (force-discharge / repeated deep clouds) until **`SOC≤10`**, then a deep cloud | `low_soc` lamp; **discharge blocked at the 10 % floor → next deep cloud sags Plant MW with the sun** (negative-space proof of the battery's worth). |

---

## 5. FUXA views

**Display convention (critical, applies to all views).** FUXA's `svg-ext-value` shows the **raw stored register** with no arithmetic, so scaled/offset points (×10, ×100, +offset) must NOT go in bare numeric tiles as engineering values. Decision:
- **Numeric value tiles** are reserved for integer-native points (Plant MW, irradiance, PV DC, AC, clipping loss, currents, temps, SOC, daily MWh, η, PR).
- **Scaled/offset points** (Grid Hz ×10, POI kV ×10, MVAR/battery-power offset, PF ×100) are shown on **html_bag dials** (needle position is exact; zone bands/tick labels carry the scaling, e.g. axis labeled "Hz ×10 — green 598–602") and via **graphical cues** (pipe direction, charge/discharge lamp). Charts plot raw stored values with **axes pinned to raw ranges** and labeled with the scaling.
- *(Risk/optional, verify-first:* if true signed numerics in tiles are essential, add a Scaling/Offset to the VOLTTRON modbus registry — unproven in this stack, do not rely on it without testing.)*

**Widget-proving gates (resolves the platform reviewer's "unproven widgets" blockers).** Four widget classes used here were **not exercised by the substation POC** and MUST each be proven end-to-end on ONE instance before mass-generation, with a proven fallback if they fail:

| widget | first used for | verify-one-first | proven fallback |
|---|---|---|---|
| `svg-ext-html_bag` (radial dials) | all KPI dials | emit one Plant MW dial, screenshot | numeric `svg-ext-value` tile + `svg-ext-gauge_progress` bar |
| `svg-ext-pipe` (animated flow) | single-line + charge/discharge direction | emit one DC pipe with `{clockwise,stop}`, confirm it animates | **recolor a proceng/path by flow state** (static, proven) |
| `svg-ext-html_slider/_switch/_select/_input` (controls) | all setpoints on drill-downs | emit one slider, confirm it writes via the binding | **`svg-ext-html_button` presets/increments** (proven `onSetValue`): setpoint −5/+5, mode A/B/C, CLOSE/OPEN, STOW/TRACK |
| hand-authored `svg-ext-proceng` recolor + `onpage` nav button | electrical symbols + view switching | one symbol recolors on 0/1/2, one button switches view | library proceng / hamburger `navigation.items` |

All radial gauges use **`svg-ext-html_bag`** (NgxGauge, runtime-injected): emit only the outer skeleton `g[type=svg-ext-html_bag] > rect + foreignObject id="H-BAG_xxx" > div id="D-BAG_xxx"`; put min/max/type/staticZones/pointer in `property.options`; bind `property.variableId = tag.id`. **Pipe direction is bound to `bess_status` (1=discharge clockwise, 2=charge anticlockwise, 0=idle stop)** — NOT the sign of the +50-offset `battery_power_mw` (whose raw value is never negative). Pipes use `svg-ext-pipe` (3 paths `bPIE_/pPIE_/cPIE_`). Electrical symbols are **hand-authored SVG** inside `svg-ext-proceng` groups recolored by a 0/1/2 status range (the bundled library has no electrical symbols). Charts: host `<DIV id="D-...">` in `<foreignObject id="H-...">`, project-level `charts[]`, **pin scaleY1/Y2 min/max**, ~200 px tall, `daq.enabled=false`, inject legend-cleanup CSS.

### 5.1 `v_power_plant` — Overview & Day-in-the-Life (ENTRY)
Canvas **1280 × 800**, dark bkcolor.

**TOP KPI dial band** (`svg-ext-html_bag`, y≈10–180), electrical-led, left→right:
1. **Plant MW** (huge, x≈20–230) — Zones type, min 0 max 110, staticZones green 0–100 / red 100–110, → `plant_active_power_mw`.
2. **Grid Hz** (x≈250–430) — Zones, **min 590 max 610 (×10)**, green 598–602 / amber ride-through shoulders / red beyond, → `grid_frequency_hz`. Axis label "Hz ×10". (Min 590 so the 59.3 Hz fault reads on-scale below the green band.)
3. **POI kV** (x≈450–630) — Zones, min 1090 max 1210 (×10), green 1120–1180, → `poi_voltage_kv`. Label "kV ×10".
4. **Irradiance** (x≈650–850, ~1.2× size, the "fuel") — Gauge type, min 0 max 1200, amber→yellow, → `irradiance_wm2`.
5. **BESS SOC** (x≈870–1050) — Donut, min 0 max 100, green 10–90, → `battery_soc_pct`.
6. Small `svg-ext-value` strip under the band (y≈185): `power_factor` (label "PF ×100"), `daily_energy_mwh` (label "MWh demo-scaled"), `performance_ratio_pct` (%), `clipping_loss_mw` (MW).

**CENTER animated single-line** (y≈220–470), left→right, hand-authored proceng symbols + animated pipes (with two-stage step-up so it reads correctly to an EE):
`☀ SUN` (recolored by irradiance band) → **blue DC pipe** (animated) → `PV ARRAY (125 MWdc)` block → **DC bus node** with the **`BATTERY` symbol on a DC/DC branch** (→ `bess_status`; pipe **clockwise=discharge / anticlockwise=charge**, bound to `bess_status`) → **4-up INVERTER BANK w/ pad-mount step-up (0.6/34.5 kV)** (proceng symbols → `inverter1..4_status`, 0 grey/1 green/2 red) → **amber AC pipe — 34.5 kV collector** → `MAIN GSU (34.5/115 kV)` (two-coil) → **POI BREAKER** (proceng → `main_breaker_status`, closed green / open red gap; the AC pipe `stop`s animating when `main_breaker_status=0`) → `GRID TOWER (115 kV)`. Overlay `svg-ext-value` chips: Plant MW at the POI node, SOC at the battery. (Battery is on the **DC** side — this is the DC-coupled topology that lets it capture clipping.)

**RIGHT annunciator stack** (x≈1080–1270, y≈220–620): nine `svg-ext-gauge_semaphore` lamps + `svg-ext-value` labels for the 9 discrete alarms (red on raise).

**BOTTOM signature trends** (y≈630–790), two `svg-ext-html_chart`, ~200 px tall, side by side:
- **LEFT "Plant MW vs Irradiance"** (x≈10–630): **Y1 pinned 0–120 MW** lines `pv_dc_power_mw`, `inverter_ac_power_mw`, `plant_active_power_mw`; **Y2 pinned 0–1200 W/m²** line `irradiance_wm2`. → DC climbs, **AC flat-tops** while irradiance still rises.
- **RIGHT "BESS Firming"** (x≈650–1270): **Y1 pinned 0–100 %** line `battery_soc_pct`; **Y2 pinned 25–75 raw (= −25..+25 MW)** line `battery_power_mw` (axis labeled "MW +50"). → at peak battery charges (SOC up); on the shallow cloud battery_power spikes + and SOC dips while left-chart Plant MW stays flat at 100.

**NAV** (`svg-ext-html_button`, top-right corner): "ELECTRICAL ONE-LINE →" (`onpage` `v_pp_oneline`), "BESS DETAIL →" (`onpage` `v_pp_bess`), "→ SUBSTATION" (`onpage` `v_heat_station`). Plus `svg-ext-html_switch` (or button fallback) for `tracker_enable`.

### 5.2 `v_pp_oneline` — Electrical One-Line & POI Metering
Canvas **1280 × 800**.

**Full single-line strip** (top, left→right, proceng + animated pipe): `PV ARRAY + DC BESS aggregate` → `INVERTER ARRAY w/ pad-mounts (0.6/34.5 kV)` (4 proceng symbols in a 2×2, each with a small semaphore → `inverterN_status`) → `34.5 kV COLLECTOR` → `MAIN GSU (34.5/115 kV)` (two-coil) → **animated BREAKER** (→ `main_breaker_status`; pipe halts on open) → `POI metering node (115 kV)` → `GRID tower`.

**Big metering readouts** (`svg-ext-value`, large): `plant_active_power_mw` (huge), `clipping_loss_mw`, `poi_current_a`, `inverter_efficiency_pct` (integer-native tiles). A **center-zero `svg-ext-html_bag` Gauge** → `plant_reactive_power_mvar` (min 17 max 83, 0-mark at 50, left=absorb / right=export) shows lead/lag at a glance; flanked by dials for `poi_voltage_kv` and `power_factor`.

**PQ-envelope chart** (bottom dual-axis `svg-ext-html_chart`): **Y1 pinned 1090–1210 raw (=109..121 kV)** line `poi_voltage_kv`; **Y2 pinned 17–83 raw (=−33..+33 MVAR)** line `plant_reactive_power_mvar`. → POI voltage tracks reactive dispatch.

**RIGHT controls column** (sliders/switches/input — verify-one-first, else button fallback): `power_setpoint_mw` (0–100); `mvar_setpoint` (display −33..+33, write +50 offset → 17..83); `voltage_setpoint_kv` (typed, ×10); `breaker_cmd` (CLOSE/OPEN → 1/0); `tracker_enable`. Annunciator lamps for `grid_over_voltage`, `grid_under_frequency`, `breaker_trip`, `inverter_fault`, `dc_ground_fault`, `comms_loss`, `curtailment_active`.

**NAV:** "← OVERVIEW" (`v_power_plant`), "BESS →" (`v_pp_bess`).

### 5.3 `v_pp_bess` — BESS Detail
Canvas **1280 × 800**.

**LEFT:** large `svg-ext-html_bag` **Donut SOC** → `battery_soc_pct` (green 10–90) + a vertical `svg-ext-gauge_progress` SOC bar (child rects id-prefixed `A-/B-/H-`, minmax 0–100, bands red <10 / green 10–90 / amber >90).

**CENTER:** a center-zero `svg-ext-html_bag` **power dial** → `battery_power_mw` (min 25 max 75, 0 at 50; charge band left / discharge band right) + a `svg-ext-gauge_semaphore` charge/discharge/fault lamp → `bess_status`; integer-native `svg-ext-value` tile `battery_temp_c`; battery proceng symbol → `bess_status`.

**RIGHT controls** (verify-one-first, else button fallback): `bess_mode` select (auto-smooth / force-charge / force-discharge); `bess_power_cmd_mw` slider (display −25..+25, write +50 offset); lamps `low_soc`, `battery_over_temp`.

**BOTTOM chart "SOC vs Battery Power"** (`svg-ext-html_chart`): **Y1 pinned 0–100 %** line `battery_soc_pct`; **Y2 pinned 25–75 raw (=−25..+25 MW)** line `battery_power_mw`. → SOC rises while charging from the clip, drains as it discharges through a cloud, recovers on charge.

**NAV:** "← OVERVIEW" (`v_power_plant`), "ELECTRICAL →" (`v_pp_oneline`).

### 5.4 Navigation menu
`project.hmi.layout.navigation.items` = one `NaviItem` per view (`v_heat_station`, `v_power_plant`, `v_pp_oneline`, `v_pp_bess`). Set `project.hmi.layout.start = "v_power_plant"` (the money view). On-dashboard `onpage` buttons (above) are the primary mechanism (verify one switches views first); the hamburger is the backup.

---

## 6. MCP changes (`mcp/server.py`)

Add a `plant` argument (string, default `"heat_station"`) to the scenario/fault/control tools; it selects the namespaced route `POST /api/sim/<plant>/{scenario,fault,point,reset}` and `GET /api/sim/<plant>/state`, with `<plant> ∈ {"heat_station","power_plant"}`. `read_point`/`write_point`/`query_history`/`write_and_verify` already address points by full VOLTTRON path and work unchanged for either device.

Update tool docstrings to enumerate the power-plant scenario names (`day_in_the_life`, `sunrise`, `peak_sun`, `cloud_passing`, `curtailment`, `evening`, `inverter_trip`, `grid_fault`, `bess_dispatch`, `low_soc`) and fault keys (`inverter1..4`, `grid` (with moderate/severe variants), `dc_ground_fault`, `comms_loss`, `battery_over_temp`). `load_scenario`/`inject_fault`/`set_sim_point`/`reset_sim` gain the `plant` param. Keep substation routes working by refactoring the current flat `/api/sim/*` into the per-plant form for both plants.

---

## 7. Build order + risks

**Build order (serial, verify each before the next):**
1. **Sim model** — add `SolarPlant` (INPUTS/HOLDING/DISCRETE per §2, constants + `step()` per §3 incl. the DC-coupled dispatch, ramp-smoothing baseline, `/3600` SOC, D-curve reactive, ride-through, calibrated thermal; `SCENARIOS` per §4) as Modbus slave 2 in `ModbusServerContext(slaves={1:…,2:…})`; step both each tick; refactor control API to `/api/sim/<plant>/…`. Verify with `GET /api/sim/power_plant/state` and a `day_in_the_life` run via curl. **Assert the physics sanity numbers (below) in-state before touching FUXA.**
2. **Registry + device** — `power_plant.registry.csv` (rows exactly matching §2 indices/spaces) + `device.power_plant.json` (slave_id 2) + second `vctl config store` in `entrypoint.sh`. `docker compose down && up` (never plain restart). Confirm VOLTTRON publishes all 41 points.
3. **Verify-one-first gates (do these BEFORE mass-generation, in order):** (a) one `svg-ext-html_bag` Plant MW dial renders; (b) one `svg-ext-pipe` animates with `{clockwise,stop}`; (c) one hand-authored `svg-ext-proceng` recolors on 0/1/2 and one `onpage` button switches views; (d) one `svg-ext-html_slider`/`_switch`/`_select`/`_input` writes via the binding. **Any that fail → use the §5 proven fallback (numeric+progress / static recolor / `html_button` presets) and proceed.**
4. **Generator** — `powerplant_dashboard.py` building device + the three views + combined `charts[]`; merge into the single `POST /api/project` (full replace — must include the substation device/view too). Stable ids `v_power_plant`/`v_pp_oneline`/`v_pp_bess`.
5. **Verify live** — Playwright screenshot each view; drive `day_in_the_life`, `cloud_passing`, `inverter_trip`, `grid_fault` via MCP; confirm the clip flat-top, **SOC rising at peak then dipping on the cloud**, the breaker animation, and that the deep `cloud_passing` shows the honest 100→~75 dip-shave (not a false flat hold).

**Known gotchas (do not relearn):**
- **VOLTTRON entrypoint is not idempotent** on `docker restart` (stale `VOLTTRON_HOME` → conflicting identity). Always `docker compose down && up` or `--force-recreate volttron`.
- **`POST /api/project` is a full replace** — always emit BOTH devices + ALL views + combined charts, or the substation vanishes.
- **Binding:** `property.variableId === plain tag.id` (not a `deviceId^~^tagId` composite). Chart line `id` = tag id, `device` = device **NAME**.
- **html_bag is runtime-injected** — emit only the skeleton (rect + `H-BAG_` foreignObject + `D-BAG_` div), config in `property.options`; verify one before scaling out.
- **Unproven-in-this-stack widgets are gated** (step 3): `html_bag`, `svg-ext-pipe`, the `html_slider/switch/select/input` controls, hand-authored proceng recolor, and `onpage` nav. Each has a proven fallback (§5).
- **Pipe direction binds to `bess_status` (0/1/2), not the +50-offset `battery_power_mw`** (whose raw value never goes negative, so a sign-vs-0 test is always "positive").
- **Charts:** pin scaleY1/Y2 min/max (stops uPlot pruning flat multi-line series), host div id `D-`, foreignObject `H-`, ~200 px tall, `daq.enabled=false`, inject legend-cleanup CSS. Chart axes plot **raw stored values** — label offset/×10 axes accordingly (§5).
- **Signed/scaled display:** never put offset/×10/×100 points in bare numeric tiles as engineering values; use dials + pipe direction + lamps (§5 Display convention). The cloud-firming sign is read from pipe direction + lamp, not a number.
- **proceng recolors EVERY child node** by range — keep child ids stable; draw any fixed-color detail as a separate un-grouped element on top.
- **gauge_progress child rects MUST be id-prefixed `A-/B-/H-`** or the signal pipeline throws.
- **Modbus space discipline:** input→FC4 (measurements + `main_breaker_status`), holding→FC3 (setpoints), discrete→FC2 (alarms). `main_breaker_status` is deliberately an INPUT register (drives the symbol/pipe), distinct from the `breaker_trip` discrete alarm and the `breaker_cmd` holding command.
- **SOC math: keep the `/3600`.** `SOC%/step = (P_batt/ETA_OW)·DT/(3600·E_CAP)·100` (discharge); the no-`/3600` shorthand drains the pack in ~3 s and is the single most dangerous typo in this spec.
- **Numbers to sanity-check on screen** (an EE will): peak PV DC ≈ **110.5 MW** at the reference / **~111 MW** at signature solar noon; AC clipped **100**; **clipping_loss ≈ 0 while the battery captures it, climbing to ~8.8–10 MW only at SOC≥90**; panel_temp **~56 °C reference / ~66 °C signature noon**; PR **~80 % at clip, up to ~90–93 % at cool part load**; POI current **~502 A** (unity) to **~529 A** (full Q); inverter_efficiency **~98.5 %** (not derived from P_ac/P_dc); 25 MW discharge drains usable SOC in **~3 h**; reactive **±33 MVAR available across the operating range incl. night STATCOM**; deep `cloud_passing` sags Plant MW to **~75**, not flat.

---

## Review reconciliation

Key changes made in response to the three adversarial reviews:

1. **SOC `/3600` bug (unanimous blocker):** restored the seconds→hours factor — `SOC ±= (P_batt[/·]ETA_OW)·DT/(3600·E_CAP)·100`. Deleted the wrong `…/E_CAP·100` shorthand. Verified to 0.0074 %/s → 3.0 h.
2. **Degenerate auto-dispatch + "SOC rises at peak" impossibility (unanimous blocker):** re-spec the BESS as **DC-coupled** (my call among the reviewers' options a/b/c — it resolves the most issues at once and is a real topology) so it charges from DC **clipping** at peak (SOC genuinely rises, clipping_loss → 0), and replaced "fill-to-nameplate" with a **ramp-rate-smoothing auto law** (slow EMA baseline + clip-capture) so the battery no longer drains all morning and is available for the demo cloud.
3. **Oversold cloud-firming (blocker, two reviews):** signature `day_in_the_life` cloud shallowed to **G→~720** so Plant MW genuinely holds flat at 100; the deep **G→300** cloud is retained in `cloud_passing`/`low_soc` and reframed honestly as **dip-shaving (100→~75)** and negative-space proof.
4. **Reactive capability (major):** replaced `|Q|≤0.329·P` with the MVA-limited D-curve `Q_limit=min(33, √(105.3²−P²))` — full ±33 MVAR down to low P and **night STATCOM**; the `S_max=105.3 MVA` over-100-MVA headroom is explicitly credited to the **25 MVA BESS PCS** (platform reviewer's catch).
5. **PF night guard (physics):** PF forced to 1.00 when S<0.5 MVA (no 0/0 garbage at night).
6. **inverter_efficiency (major, two reviews):** now from a **real part-load curve** (~98.5 %, sagging only below ~15–20 % load), not `P_ac/P_dc`; clipping stays exclusively in `clipping_loss_mw`.
7. **Ride-through (physics):** modeled IEEE 1547-2018 / PRC-024 — `grid_under_frequency` is an indication, momentary cessation for deep dips, `breaker_trip` only on sustained/severe; Grid Hz dial **min lowered to 590** so the 59.3 Hz fault is visible.
8. **Battery thermal (minor):** first-order model calibrated to settle ~36 °C at full 0.25C — no more false over-temp trip in ~40 s.
9. **Sanity-number reconciliation (minor, all three):** panel_temp/P_dc/clip stated for both the (G=1000,T_amb=25) reference and the (G=1050,T_amb≈33) signature noon; PR band widened to ~80–93 %.
10. **daily_energy (minor):** compression-scaled by `K_COMP` and labeled "demo-scaled" so it reads realistic MWh.
11. **low_soc (minor):** scenario pre-drains to **SOC≤10** (the discharge-block floor) so the negative-space proof actually triggers; lamp stays an early-warning at ≤12.
12. **Pipe direction (minor):** bound to `bess_status` (0/1/2), not the always-positive +50-offset `battery_power_mw`.
13. **Unproven widgets (major, platform):** added explicit **verify-one-first gates** with proven `html_button`/static-recolor fallbacks for `svg-ext-pipe`, the control widgets, hand-authored proceng recolor, and `onpage` nav (previously only `html_bag` was gated).
14. **One-line realism (minor):** added the **two-stage step-up** (inverter pad-mounts 0.6/34.5 kV → 34.5 kV collector → 34.5/115 kV main GSU) and moved the battery onto the **DC bus**.
15. **inverter_trip narrative (minor, two reviews):** with DC-coupling, losing a block drops the **AC cap to 75 MW**, so the −25 MW step is genuinely visible on Plant MW (the battery can't exceed the reduced cap) — story is now honest, not self-cancelling.

Praised elements kept unchanged: the PVWatts chain (NOCT 0.03125, γ_P −0.0037, derate 0.884, P_dc 110.5, clip to 100), RTE-on-both-legs bookkeeping (η_ow=√0.88), 0.25C / 3 h duration, register-space discipline, the +50/×10/×100 scaling conventions, `main_breaker_status` in INPUT, the integer-native-vs-scaled display split, full-replace project POST, chart Y-axis pinning, and the `variableId === plain tag.id` binding.

---

## Addendum — as-built (changes since v2.0)

The v2.0 spec above is accurate for the core PV/BESS physics, scenarios, and the three plant views. This addendum records what was **added or changed during the build** and supersedes the spec where they differ. Live point counts are now **78 total = 27 substation + 51 solar** (33 input / 9 holding / 9 discrete).

### A. Full 24 h day + operator time-of-day control
- **`day_in_the_life` is a full 24 h day model** (was daylight-only), so it runs through a real **night** (G=0, inverters off, battery idle, trackers stowed) and wraps at midnight. The daylight arc is a 06:00–18:00 sine; a signature passing cloud sits at ~13:00. `T_DAY = 300 s` (a ~5-min demo day).
- **Two new HOLDING regs** (writable): `time_rate` (idx 7: 0 pause / 1 play / N fast-forward — scales the day-clock advance) and `time_set_hhmm` (idx 8: write `0000–2359` to **jump** the day clock; **`9999` = no-op sentinel** so `0000` = midnight is settable). A jump calls `_seed_from_driver()` to **snap** the physics to that hour (no glide transient — a night jump goes dark immediately) and forces the plant into the day model.
- **`sunrise` / `evening` are now thin presets** over the day model (jump to ~05:00 / ~16:30 and play) — they no longer have their own clock-based ramp logic.
- **Clock points** (INPUT): `clock_hour` (25) + `clock_min_tens` (26) + `clock_min_ones` (27). The minute is split into tens/ones **digit** points because FUXA `svg-ext-value` tiles can't zero-pad (so `6:06`, not `6:6`); render as `hour : tens ones` with a static colon.
- Dashboard: a `clock()` helper + a **TIME OF DAY** control group (PAUSE / PLAY / FAST 4× + jump presets 00:00/04:00/08:00/12:00/16:00/20:00).

### B. BESS daily-charge balance (so SOC cycles, low_soc doesn't stick)
Replacing the v2.0 auto law's slow EMA baseline with a **fast-attack / slow-decay firming envelope** `P_ref` (idle on the smooth ramp, charge from the clip at peak, discharge to firm a *fast* cloud). The envelope target is capped by `min(P_ref, setpoint, live_inverter_AC_cap)` so **losing an inverter makes surplus PV charge the battery** instead of discharging into the reduced cap. To stop SOC ratcheting to the floor (the battery over-firming the slow sunset): `K_DECAY = 0.11` (τ≈9 s — tracks the sunset, only firms fast clouds), a **discharge taper below 30 % SOC**, and a daytime **SOC-restoration bias toward 55 %**. Result: SOC cycles a healthy ~39–47 % and `low_soc` no longer lights in normal operation.

### C. Campus microgrid coupling (ties the two plants together)
The solar+BESS plant supplies a **campus bus**; the district-heating substation's circulation pumps are an **electrical load** on it. `SolarPlant.step()` reads the substation's pump Hz **in-process** (both plants share one `ModbusServerContext`: `self.ctx[1].getValues(FC_INPUT, INPUTS["circ_pump1_hz"]...)`) and computes pump load via the affinity law (P ∝ Hz³; ~3 MW/circ pump + 1 MW makeup), plus a campus **base load** on a daily curve (10 MW night → 28 MW midday, driven by the day clock). Five new SOLAR INPUT regs (28–32): `campus_base_load_mw`, `substation_load_mw`, `campus_load_mw`, `grid_power_mw` (**+100 offset**; >100 export / <100 import = `plant_active_power − campus_load`), `solar_to_load_pct`. **A substation pump trip drops campus load → the plant's grid export rises; at night solar=0 so the campus imports from the grid.** No change to the substation's own sim/registry.

### D. Views & nav (now FIVE views)
A new **`v_site` Campus Overview landing page** (the home/start view): energy-balance dials (solar gen / campus load / grid import-export / solar-share% / SOC), an animated microgrid one-line (SOLAR+BESS → CAMPUS BUS → {buildings, district heating} + grid tie), a generation-vs-load trend, and **dual-device summary tiles**. All five views (`v_site`, `v_power_plant`, `v_pp_oneline`, `v_pp_bess`, `v_heat_station`) carry a consistent top-right nav row (`NAV_VIEWS` + `view_nav`); the substation gets it spliced in by `inject_nav` (it's built nav-less by `build_dashboard.py`).

### E. New hard-won FUXA/stack facts (beyond §5)
- **`svg-ext-html_bag` = the radial/zones/donut gauge** (NgxGauge; `Gauge`/`Donut` from `assets/lib/gauge/gauge.js`). Emit only a skeleton `<g type=svg-ext-html_bag><rect/><foreignObject id="H-BAG_x"><div id="D-BAG_x"></div></foreignObject></g>`; put `type`(0 gauge/1 donut/2 zones)/`minValue`/`maxValue`/`staticZones`/`staticLabels`/`pointer` in `property.options`; bind `variableId = tag.id`.
- **ID-prefix underscore is mandatory.** FUXA finds widget hosts/children by prefixes that include `_`: `D-BAG_`, `D-HXC_` (chart host div), `A-GXP_`/`B-GXP_`/`H-GXP_` (progress child rects). Omit it and the widget never instantiates / `processValue` throws on null every tick. Make `uid` emit `f"{pfx}_{n:03d}"`.
- **proc-eng recolor works by INHERITANCE:** `walkTreeNodeToSetAttribute` sets `fill` on the `<g>` and only recurses into `SHE`/`svg_`-prefixed children, so hand-authored symbols put the base fill on `<g type=svg-ext-proceng fill=...>` and give recolored shapes **no `fill` of their own**; detail line-work is stroke + `fill="none"`.
- **`onpage` nav button needs `actoptions`:** event `{type:"click",action:"onpage",actparam:"<viewId>",actoptions:{}}` — without `actoptions` (even `{}`), `loadPage` reads `options.sourceDeviceId` on undefined and throws.
- **Dual-device binding:** to show another device's tag, set `property.variableId` to that device's tag id (`t_hs_<point>`) and `property.variableSrc` to the device id (`heat_station`).
- **Animated flows:** plain `<path>` + SMIL `<animate attributeName="stroke-dashoffset">` in the svgcontent (robust; skip the fragile `svg-ext-pipe` editor-extension).
- **Entrypoint idempotency (fixed):** `volttron/entrypoint.sh` now installs each agent only if absent, else starts it **by parsed UUID** (`vctl start` takes a UUID, not an identity) — a plain `docker restart` no longer loops on "Identity already exists". NOTE: editing a registry CSV requires **rebuilding the volttron image** (the config dir is `COPY`d in) before the entrypoint re-stores it; `docker compose down && up` (or `--force-recreate volttron`) is still the clean path.