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
| Inverters | **33 × SMA Sunny Central 4600 UP-US** (4600 kVA @ 35 °C, 690 V AC, 1500 Vdc, I_DC,max 4750 A, **CEC η 98.5 %**), one per MVPS-S2 station → 34.5 kV | `inv_eff = 98.5`, or model the datasheet inverter (Paco 4.6 MW, Vdcmax 1500, Vdcmp ~1200) |
| Tracker | **1P single-axis, N-S axis, ±60°, BACKTRACKING** | `array_type = 3` (1-axis backtrack) |
| Axis tilt / azimuth | 0° axis tilt, 180° axis azimuth (N-S) | `tilt = 0`, `azimuth = 180` |
| GCR | **0.32** (7.2 m pitch, ~2.3 m module) | `gcr = 0.32` |
| Module | **bifacial**, ~21-22% eff, ~660 Wp | `module_type` / CEC bifacial module |
| Temp coeff (Pmp) | **-0.37 %/°C** (γ_P = -0.0037) | CEC module param |
| Cell temp model | NOCT 45 °C (T_cell = T_amb + 0.03125·G) | NOCT model |
| Bifaciality | ~0.70 (snow albedo boosts winter) | `bifaciality = 0.7` |
| Ground albedo | 0.2 base; **raise for snow months** (~0.6-0.8 Dec-Mar) | monthly albedo |
| System losses | soiling/mismatch/DC+AC wiring/availability (~14% default; snow-soiling notable) | `losses` / detailed loss tree |
| BESS (optional) | **37.5 MW / 150 MWh, RTE 0.88** — coupling under review, **AC-coupled preferred** (AESO ancillary-service revenue » DC clip recovery); model as AC-connected | Battery model (AC-connected) |

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
    (~660 Wp) + the **SMA Sunny Central 4600 UP-US** inverter (4.6 MVA, from the CEC DB
    if present, else the Inverter Datasheet model: Paco 4.6e6 W, Vdcmax 1500, η 98.5 %);
    subarray1 = 1-axis with backtracking, gcr 0.32, bifacial on; module count / string
    sizing to ~187.5 MWdc; **33 inverters** → 150 MWac. Run → annual AC energy, AC CF, specific
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
   + the **SMA Sunny Central 4600 UP-US** (4.6 MVA, 33 units; CEC DB or datasheet model);
   refine only if the CF looks off.

### First actions on resume
1. `site-model/.venv/bin/pip install NREL-PySAM`; `python -c "import PySAM; print(PySAM.__version__)"`.
2. Pull NSRDB PSM3 TMY for 51.18978,-113.66769 with DEMO_KEY -> `site-model/build/tmy.csv`.
3. Write `site-model/scripts/sam_model.py`: PVWatts v8 run (params table above) -> print AC CF.
4. Then Pvsamv1 detailed run -> AC CF, specific yield, PR, clipping, monthly -> `build/sam_results.json`.

**Expected ballpark (to be confirmed by SAM):** AC capacity factor ~18-22% for
single-axis + bifacial at 51.2°N with ILR 1.25 (clipping raises AC CF; high latitude +
winter limits it). Specific yield likely ~1300-1500 kWh/kWp/yr.

---

## Inverter update (2026-07-01): SMA Sunny Central 4600 UP-US on MVPS-S2

The plant now specifies a **real inverter**: the **SMA Sunny Central 4600 UP-US**, one
per **MV Power Station (MVPS-S2)** skid (inverter + 690 V/34.5 kV MV transformer + MV
vacuum breaker). Datasheets:
`SC4xxxUP-DS-en_us-39.pdf`, `MVPS-S2-SC40-46-UP-US-DS-en_us-30.pdf`.

**Inverter facts that matter for SAM:**
- Nominal AC **4600 kVA @ 35 °C** (4140 kVA @ 50 °C — hot-day derate), **690 V** AC
  (matches the plant's 690 V LV), I_AC,nom 3850 A.
- DC: **max 1500 V**, MPP 1003–1325 V (top clamps 1050 V @ 50 °C), **I_DC,max 4750 A**,
  I_SC 8400 A. **CEC efficiency 98.5 %** (max 98.9 %). PF 1.0, adj. 0.8 OE↔0.8 UE.
- **Count: 33 stations** → 151.8 MVA installed (POI capped at 150 MWac). At 187.5 MWdc
  that's **5.68 MWdc/inverter ≈ 4735 A @ 1200 V MPP — right at the 4750 A I_DC,max**, so
  187.5 MWdc is about the max DC these 33 units should carry. ILR 1.25 at the 150 MW POI
  cap (1.235 on installed inverters).
- SAM: model as the datasheet inverter (Paco 4.6e6 W, Vdcmax 1500, Vdcmp ~1200, η 98.5 %),
  33 units → 150 MWac; DC 187.5 MWdc unchanged. If Pvsamv1's inverter DB lacks the exact
  SC4600 UP-US, use the Inverter Datasheet model with those numbers.

**DC input / combiner design (determined via cable takeoff):** single-pole DC fusing is
permitted (CEC 2024; long-standing IEC/EU practice), so all **32 single-pole inputs** are
usable, and with **AC-coupled** storage (below) the SC4600 UPs are **PV-only** (no battery
DC inputs). Design point: **populate 28 of 32 single-pole PV inputs, 4 spare** (~203 kW/input,
~169 A op, 315 A fuse, ~300 kcmil Al aerial trunk) — clear of the 630 A fuse / 2×800 kcmil
limits, with O&M spares at negligible copper penalty. **A DC-cable takeoff (2026-07-01, in
`site-model/SPEC_ALIGNMENT.md`) reversed an earlier "24 of 32 / 8 spare" call:** embiggening
combiners does NOT save money on this plant — the collection is **aerial trunk-bus (no
trench)**, so the usual reason to embiggen is absent, and copper string homeruns dominate and
*lengthen* as combiners sparsen. 18-input is always the most expensive; 24 vs 28–32 is within
±1–4% (estimating noise); more/smaller/closer combiners minimize copper. Hence ~28–32, not 24.

**Storage coupling (flagged, not yet propagated):** DC-coupled storage is a weak economic
play here — clipping recovery on an ILR-1.25 plant is a few % of annual energy, whereas an
**AC-coupled** BESS earns AESO ancillary services (contingency/regulating reserve) **plus**
arbitrage and dispatches independent of PV. **Preferred: AC-coupled** 37.5 MW / 150 MWh on
the 34.5 kV bus (SMA offers the SCS-UP-US storage variant of this same platform). This does
**not** change the PV-only AC capacity factor SAM reports; it only changes how the battery
case is modeled (AC-connected). **NOTE:** the running SCADA sim, `docs/POWERPLANT_SPEC.md`,
and the 3D model are still **DC-coupled** — re-architecting them to AC-coupled is a separate,
larger task (see the open question in the session that logged this).

## Pointers
- Physics constants + sanity numbers: `docs/POWERPLANT_SPEC.md` (§3, and the "numbers
  to sanity-check" bullet).
- Geometry (GCR, pitch, tilt, tracker): `site-model/scripts/layout.py` (constants top).
- Sim constants (ILR, η, γ, NOCT, RTE): `modbus-sim/sim.py` (~lines 330-360).
- Site + terrain: `site-model/SPEC_ALIGNMENT.md`, `site-model/scripts/geo.py`.
- venv (has trimesh/shapely/numpy/scipy/Pillow; add NREL-PySAM): `site-model/.venv`.
