# Sunfield Solar — SAM production model results

Energy-production model of the Sunfield Solar plant in NREL SAM (via **PySAM**), per
`docs/SAM_HANDOFF.md`. Headline metric = **AC capacity factor** (annual AC energy at the
150 MWac POI ÷ 150 MW × 8760 h).

## Headline

| | value |
|---|---|
| **AC capacity factor** | **≈ 24–26 %** (primary 26.3 %) |
| Specific yield | ≈ 1650–1840 kWh/kWdc (primary 1842) |
| Annual AC energy at POI | ≈ 310–345 GWh/yr (primary 345) |
| Performance ratio | ≈ 0.87 (bifacial-inflated; ≈0.83 mono-equivalent) |
| Inverter clipping loss | 0.5–2.2 % (ILR 1.25) |

**Primary** = detailed Pvsamv1 on the PVGIS-NSRDB TMY. The range spans two SAM engines
(PVWatts v8, Pvsamv1) × two weather sources (see below).

## Results grid

| Weather | Engine | AC CF | Yield (kWh/kWdc) | Annual AC (GWh) |
|---|---|---|---|---|
| PVGIS-NSRDB TMY | PVWatts v8 | 25.8 % | 1806 | 338.5 |
| PVGIS-NSRDB TMY | **Pvsamv1** | **26.3 %** | **1842** | **345.4** |
| ERA5-2016 | PVWatts v8 | 23.6 % | 1651 | 309.6 |
| ERA5-2016 | Pvsamv1 | 24.3 % | 1702 | 319.1 |

The two engines agree to within ~0.7 points at each weather source — the model is
internally consistent. The ~2-point spread between weather sources is the dominant
uncertainty (all resource-driven).

## Method & inputs

- **Engines:** `PySAM.Pvwattsv8` (fast AC-CF sanity) and `PySAM.Pvsamv1` (detailed CEC
  single-diode module + datasheet inverter, subarray backtracking, bifacial). PySAM
  7.1.1 in a dedicated Python-3.12 venv (`site-model/.venv-sam`; the repo's 3.14 venv
  has no PySAM wheels).
- **Plant (locked, per handoff):** 187.5 MWdc / 150 MWac (ILR 1.25); 1-axis N-S
  backtracking tracker (±60°), GCR 0.32; bifacial ~660 Wp module (η≈21.2 %, γ_Pmp
  −0.37 %/°C, NOCT 45 °C, bifaciality 0.70); **33 × SMA Sunny Central 4600 UP-US**
  (4.6 MVA, MPP 1003–1325 V, 1500 Vdc, CEC η 98.5 %) → 151.8 MVA installed, POI capped
  at 150 MWac via SAM's interconnection limit. Detailed run: 284,004 modules, 28/string,
  10,143 strings.
- **Losses:** monthly soiling with winter snow-soiling (12 % Dec/Jan → 1.5 % summer),
  DC wiring 2 %, mismatch 1 %, diodes 0.5 %, nameplate 1 %, AC wiring 1 %, transmission
  1 % (GSU + take-off), availability 2.5 %. Monthly ground albedo 0.2 base, 0.6–0.7 in
  snow months. (Physical snow-loss model not run — no snow-depth channel in the TMY —
  so snow is folded into winter soiling instead.)

## Weather provenance (important)

NREL's own NSRDB API (`developer.nrel.gov`) is **firewalled in this build environment**,
so the TMY was pulled two ways and both were run:

1. **PVGIS TMY (primary).** For the Americas PVGIS serves **NREL's NSRDB** satellite
   irradiance, so for this point it is the same underlying record SAM would use.
   Annual GHI 1428, DNI **2049** kWh/m², 961 m. → `build/tmy.csv`.
2. **Open-Meteo ERA5 (lower bracket).** A representative single year (2016, GHI closest
   to the 2013–2022 mean). Annual GHI 1381, DNI **1889** kWh/m². → `build/tmy_era5.csv`.

Both are converted to SAM weather CSVs (UTC→MST, mid-hour, Pa→mbar) by
`scripts/fetch_weather.py`. GHI agrees within 3 %; PVGIS DNI runs ~10 % above ERA5 —
expected, since ERA5 reanalysis tends to under-read DNI in sunny continental climates
while NSRDB (satellite) is the accepted standard. Truth likely sits toward the PVGIS end.

## Sanity check & caveats

- These figures land **above** the handoff's generic 18–22 % prior and above typical
  *operating* Alberta plants (~20–22 % AC CF). Drivers, in order: (a) this specific
  point is exceptionally sunny (DNI ~1900–2050 — among Canada's best); (b) 1-axis
  tracking + bifacial (+albedo) on that resource; (c) the **cold-climate boost** —
  modules run far below STC most of the year, so Pmp sits above nameplate. At ILR 1.25
  clipping is small (≤2.2 %), so little is thrown away.
- **The ERA5 case (~24 % / 1700) is the more conservative bracket.** Before any
  financial use, validate against a **licensed NREL NSRDB TMY** (not reachable here) and
  an independent **PVsyst** run, and produce a proper **P50/P90** — a single TMY is a
  P50-ish point estimate, not a bankable distribution.
- AC CF is **PV-only** by design. The BESS is now **AC-coupled** (handoff update), which
  does **not** change the PV-only AC capacity factor; a dispatch/firming case is a
  separate, deferred task.

## Reproduce

```
cd site-model
.venv-sam/bin/python scripts/fetch_weather.py all      # -> build/tmy.csv, build/tmy_era5.csv
.venv-sam/bin/python scripts/sam_model.py              # -> build/sam_results.json, build/sam_monthly.png
```

Outputs: `build/sam_results.json` (full per-source/per-engine numbers + loss waterfall)
and `build/sam_monthly.png` (monthly AC by weather source).
