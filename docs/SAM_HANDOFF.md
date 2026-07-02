# Handoff — Sunfield Solar production model in SAM (PySAM)

**Objective (from the engineer):** build a detailed energy-production model of the
Sunfield Solar plant in NREL's **System Advisor Model (SAM)** and estimate annual
production. **Headline metric wanted: the AC capacity factor.**

We are at ~80% context; this doc is the clean-context handoff. The 3D geometry model
is DONE and committed (see `site-model/`, KMZ at `site-model/out/sunfield_solar.kmz`).
SAM is a NEW, separate sub-task.

---

## Key decision: PySAM, not the C++ source

`https://github.com/NatLabRockies/SAM` is a mirror of NREL's SAM C++ source — building
it is heavy and unnecessary. Use **PySAM** (`pip install NREL-PySAM`) in the existing
venv (`site-model/.venv`). PySAM exposes SAM's exact models headless:
- **`PySAM.Pvwattsv8`** — quick, robust capacity-factor estimate.
- **`PySAM.Pvsamv1`** — detailed PV (CEC module + inverter DB, subarray tracking,
  backtracking, bifacial, self-shading). The authoritative production number.
- **`PySAM.Battery` / `Pvsamv1` + battery** — optional DC-coupled BESS co-sim.

SAM is **parametric** — it does NOT import the 3D/KMZ geometry. "Building the model"
means feeding the plant's parameters (below) into SAM.

---

## Authoritative plant parameters (SAM inputs)

Source of truth: `docs/POWERPLANT_SPEC.md` (physics), `site-model/scripts/layout.py`
(geometry), `modbus-sim/sim.py` (constants). Consolidated for SAM:

| Parameter | Value | SAM field (PVWatts / Pvsamv1) |
|---|---|---|
| Location | **51.18978 N, -113.66769 W** (Wheatland County, AB) | lat/lon (weather) |
| Elevation | ~900 m (prairie; confirm from NSRDB) | from weather file |
| AC rating (POI) | **150 MWac** | — (derived from DC / DCAC) |
| DC array (STC) | **187.5 MWdc** | `system_capacity = 187500` kW |
| ILR (DC:AC) | **1.25** | `dc_ac_ratio = 1.25` |
| Inverters | 42 x ~4 MVA central, **η = 98.5%** | `inv_eff = 98.5` / CEC inverter |
| Tracker | **1P single-axis, N-S axis, ±60°, BACKTRACKING** | `array_type = 3` (1-axis backtrack) |
| Axis tilt / azimuth | 0° axis tilt, 180° axis azimuth (N-S) | `tilt = 0`, `azimuth = 180` |
| GCR | **0.32** (7.2 m pitch, ~2.3 m module) | `gcr = 0.32` |
| Module | **bifacial**, ~21-22% eff, ~660 Wp | `module_type` / CEC bifacial module |
| Temp coeff (Pmp) | **-0.37 %/°C** (γ_P = -0.0037) | CEC module param |
| Cell temp model | NOCT 45 °C (T_cell = T_amb + 0.03125·G) | NOCT model |
| Bifaciality | ~0.70 (snow albedo boosts winter) | `bifaciality = 0.7` |
| Ground albedo | 0.2 base; **raise for snow months** (~0.6-0.8 Dec-Mar) | monthly albedo |
| System losses | soiling/mismatch/DC+AC wiring/availability (~14% default; snow-soiling notable) | `losses` / detailed loss tree |
| BESS (optional) | **37.5 MW / 150 MWh, DC-coupled, RTE 0.88** | Battery model (DC-connected) |

**Note on AC capacity factor + the battery:** AC CF = annual AC energy at POI /
(150 MW × 8760). Standard AC CF is a **PV-only** metric; the DC-coupled battery
time-shifts energy and adds ~RTE losses but doesn't materially change annual PV
production. Report PV-only AC CF as the headline; run the battery case separately if
we want the delivered/firmed profile.

---

## Build plan

1. **Env:** `site-model/.venv/bin/pip install NREL-PySAM`. Confirm `import PySAM`.
2. **Weather:** pull **NSRDB PSM3 TMY** for 51.18978, -113.66769 via the NREL API
   (`developer.nrel.gov` — free key, or `DEMO_KEY` rate-limited). Save the TMY CSV to
   `site-model/build/`. (Fallback: SAM GUI download, or a nearby PSM3 point.)
3. **Model A — PVWatts v8 (quick CF):** system_capacity 187500, dc_ac_ratio 1.25,
   array_type 3, gcr 0.32, tilt 0, azimuth 180, inv_eff 98.5, bifaciality 0.7, losses
   ~14%, monthly albedo (snow). Run → `annual_energy`, `capacity_factor` (AC).
3b. **Model B — Pvsamv1 (detailed, authoritative):** pick a bifacial CEC module
    (~660 Wp) + central inverter (~4 MVA) from the CEC DB; subarray1 = 1-axis with
    backtracking, gcr 0.32, bifacial on; module count / string sizing to ~187.5 MWdc;
    inverter count to 150 MWac. Run → annual AC energy, AC capacity factor, specific
    yield (kWh/kWp), PR, clipping/curtailment loss, monthly production.
4. **(Optional) Battery case:** add the DC-coupled 37.5 MW / 150 MWh battery, dispatch
   for peak-shaving/firming; report delivered profile + round-trip losses.
5. **Report:** annual AC energy (GWh/yr), **AC capacity factor (%)**, specific yield,
   PR, clipping loss, monthly bar chart. Sanity-check against the sim's headline
   numbers and typical AB single-axis plants.

Write it as `site-model/scripts/sam_model.py` (PySAM) + save results to
`site-model/build/sam_results.json` and a small chart.

---

## Decisions (LOCKED 2026-07-01)

1. **Fidelity: BOTH.** Run PVWatts v8 first for a fast AC-CF sanity number, then the
   detailed Pvsamv1 as the authoritative result (cross-check the two).
2. **Battery: PV-ONLY AC CF** is the headline. (Battery case optional/later.)
3. **Weather: try `DEMO_KEY` first** for the NSRDB PSM3 pull; if throttled, fall back
   to a user-provided free NREL key or a manually downloaded PSM3 TMY.
4. **Module/inverter (Pvsamv1):** pick a representative bifacial CEC module (~660 Wp)
   + a central inverter (~4 MVA) from the CEC DB; refine only if the CF looks off.

### First actions on resume
1. `site-model/.venv/bin/pip install NREL-PySAM`; `python -c "import PySAM; print(PySAM.__version__)"`.
2. Pull NSRDB PSM3 TMY for 51.18978,-113.66769 with DEMO_KEY -> `site-model/build/tmy.csv`.
3. Write `site-model/scripts/sam_model.py`: PVWatts v8 run (params table above) -> print AC CF.
4. Then Pvsamv1 detailed run -> AC CF, specific yield, PR, clipping, monthly -> `build/sam_results.json`.

**Expected ballpark (to be confirmed by SAM):** AC capacity factor ~18-22% for
single-axis + bifacial at 51.2°N with ILR 1.25 (clipping raises AC CF; high latitude +
winter limits it). Specific yield likely ~1300-1500 kWh/kWp/yr.

---

## Pointers
- Physics constants + sanity numbers: `docs/POWERPLANT_SPEC.md` (§3, and the "numbers
  to sanity-check" bullet).
- Geometry (GCR, pitch, tilt, tracker): `site-model/scripts/layout.py` (constants top).
- Sim constants (ILR, η, γ, NOCT, RTE): `modbus-sim/sim.py` (~lines 330-360).
- Site + terrain: `site-model/SPEC_ALIGNMENT.md`, `site-model/scripts/geo.py`.
- venv (has trimesh/shapely/numpy/scipy/Pillow; add NREL-PySAM): `site-model/.venv`.
