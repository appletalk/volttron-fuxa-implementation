#!/usr/bin/env python
"""Sunfield Solar production model in NREL SAM (via PySAM).

Two runs, per the SAM handoff (docs/SAM_HANDOFF.md):
  A. PVWatts v8      -- fast, robust AC capacity-factor sanity number.
  B. Pvsamv1         -- detailed CEC single-diode + inverter model (authoritative).

Headline metric: AC capacity factor = annual AC energy at POI / (150 MWac * 8760 h).

Plant parameters are the LOCKED values from docs/SAM_HANDOFF.md / POWERPLANT_SPEC.md /
modbus-sim/sim.py:  187.5 MWdc, 150 MWac (ILR 1.25), 1-axis N-S backtracking tracker,
GCR 0.32, bifacial (~660 Wp, bifaciality 0.70), inverter eta 98.5%, NOCT 45 C,
gamma_Pmp -0.37%/C, site 51.18978 N / -113.66769 W, ~961 m (Wheatland County, AB).

Weather (adversarial-review note): a direct NREL NSRDB PSM3 pull was NOT reachable, so
two independent public resources bracket the answer -- PVGIS TMY (build/tmy.csv; runs
WARM here, DNI 2049 kWh/m2/yr) and Open-Meteo ERA5 (build/tmy_era5.csv; COOL, DNI 1889).
The headline is the CENTRAL estimate across both, NOT the warm PVGIS case. Both are
converted to SAM weather files by scripts/fetch_weather.py.

Outputs: build/sam_results.json + build/sam_monthly.png.
Run:  site-model/.venv-sam/bin/python scripts/sam_model.py
"""
import json
import os

import PySAM.Pvwattsv8 as pvwatts
import PySAM.Pvsamv1 as pvsam

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(HERE, "build")

# Two weather sources -> a production RANGE (the dominant uncertainty is the resource):
#   PVGIS TMY        (satellite; DNI ~2049) = WARM bracket (PVGIS runs ~10% hot on DNI
#                                             here vs typical NSRDB for southern AB ~1850)
#   Open-Meteo ERA5  (reanalysis; DNI ~1889) = COOL bracket
# NOTE: this is NOT a direct NSRDB PSM3 pull (NREL API unreachable); label PVGIS honestly.
# Headline = CENTRAL of the two, not the warm PVGIS case (adversarial-review fix).
WEATHER = [("PVGIS-TMY", os.path.join(BUILD, "tmy.csv")),
           ("ERA5-2016", os.path.join(BUILD, "tmy_era5.csv"))]

# ---- locked plant parameters ----
P_DC_KW = 187_500.0          # STC DC array (kW)
P_AC_KW = 150_000.0          # POI AC rating (kW)
ILR = 1.25                   # DC:AC
GCR = 0.32
INV_EFF = 98.5               # %
BIFACIALITY = 0.70
NOCT = 45.0                  # C
GAMMA_PMP = -0.37            # %/C
ROTLIM = 60.0                # tracker +/- deg
HOURS = 8760

# P50 lifetime derate (adversarial-review fix): SAM year-1 energy ignores degradation.
# 0.5%/yr linear over a 25-yr life -> mid-life average factor 1 - 0.005*(25-1)/2 = 0.94.
DEGRADATION_PCT_YR = 0.5
PLANT_LIFE_YR = 25
LIFE_FACTOR = 1.0 - (DEGRADATION_PCT_YR / 100.0) * (PLANT_LIFE_YR - 1) / 2.0

# Monthly ground albedo: prairie base 0.2, snow cover Dec-Mar (0.6-0.8).
ALBEDO_MONTHLY = [0.70, 0.70, 0.60, 0.35, 0.20, 0.20,
                  0.20, 0.20, 0.20, 0.25, 0.45, 0.70]

# Monthly soiling LOSS % -- folds winter SNOW loss in (PVGIS has no snow depth, so SAM's
# physical snow model can't run; captured here instead). Adversarial-review fix: the prior
# 12% Dec/Jan was far too low for southern Alberta -- utility-PV snow losses run 25-40% in
# the deep-winter months (partly offset by tracker stow-to-shed + chinook melt). Dust the
# rest of the year. These are production-weighted monthly losses.
SOILING_MONTHLY = [30.0, 24.0, 11.0, 3.0, 1.5, 1.5,
                   1.5, 1.5, 1.5, 3.0, 11.0, 30.0]


def require_weather():
    for _, wf in WEATHER:
        if not os.path.exists(wf):
            raise SystemExit(f"Missing weather file {wf}. Run scripts/fetch_weather.py first.")


# ---------------------------------------------------------------- Model A: PVWatts
def run_pvwatts(wf):
    m = pvwatts.default("PVWattsNone")
    r = m.SolarResource
    r.solar_resource_file = wf
    r.use_wf_albedo = 0
    r.albedo = ALBEDO_MONTHLY

    s = m.SystemDesign
    s.system_capacity = P_DC_KW
    s.dc_ac_ratio = ILR
    s.array_type = 3            # 1-axis tracking WITH backtracking
    s.tilt = 0.0               # axis tilt (horizontal N-S axis)
    s.azimuth = 180.0          # axis azimuth (N-S -> points array plane E/W)
    s.gcr = GCR
    s.rotlim = ROTLIM
    s.inv_eff = INV_EFF
    s.bifaciality = BIFACIALITY
    s.module_type = 0          # standard module (efficiency handled in Pvsamv1)
    s.losses = 18.0            # aggregate DC+AC losses (%): 14 base + ~2.5 winter snow
                               # + ~1 tracking + LID (adversarial-review fix; PVWatts can't
                               # take monthly snow, so it's folded into the annual aggregate)
    s.en_snowloss = 0          # no snow-depth data; snow folded into losses/soiling

    m.execute(0)
    o = m.Outputs
    return {
        "annual_ac_kwh": o.annual_energy,
        "capacity_factor_ac_pct": o.capacity_factor_ac,
        "capacity_factor_dc_pct": o.capacity_factor,
        "specific_yield_kwh_per_kwdc": o.kwh_per_kw,
        "ac_monthly_kwh": list(o.ac_monthly),
    }


# --------------------------------------------------------------- Model B: Pvsamv1
def string_sizing():
    """~660 Wp module; size strings to ~187.5 MWdc, keep string Voc(cold) < 1500 V."""
    vmp, imp = 38.7, 17.06          # STC -> 660.3 Wp
    wp = vmp * imp
    mps = 28                        # modules/string: Vmp~1084, Voc(cold,-30C)~1465 < 1500
    string_wp = wp * mps
    nstrings = round(P_DC_KW * 1000.0 / string_wp)
    return vmp, imp, wp, mps, nstrings


def run_pvsam(wf):
    m = pvsam.default("FlatPlatePVNone")

    r = m.SolarResource
    r.solar_resource_file = wf
    r.use_wf_albedo = 0
    r.albedo = ALBEDO_MONTHLY

    # ---- Module: CEC user-entered specifications (single-diode), bifacial ~660 Wp ----
    m.Module.module_model = 2       # 2 = CEC performance model, user-entered specs
    c = m.CECPerformanceModelWithUserEnteredSpecifications
    vmp, imp, wp, mps, nstrings = string_sizing()
    voc, isc = 46.0, 18.14
    c.sixpar_vmp = vmp
    c.sixpar_imp = imp
    c.sixpar_voc = voc
    c.sixpar_isc = isc
    c.sixpar_nser = 66              # cells in series (210 mm G12, ~46 V Voc)
    c.sixpar_area = 3.11            # m^2  -> eff = 660/(3.11*1000) = 21.2%
    c.sixpar_celltech = 0          # 0 = monocrystalline Si
    c.sixpar_bvoc = -0.0025 * voc  # Voc temp coeff (V/C)  (-0.25 %/C)
    c.sixpar_aisc = 0.00045 * isc  # Isc temp coeff (A/C)  (+0.045 %/C)
    c.sixpar_gpmp = GAMMA_PMP      # Pmp temp coeff (%/C) = -0.37
    c.sixpar_tnoct = NOCT
    c.sixpar_mounting = 0
    c.sixpar_standoff = 6          # ground/rack, well ventilated
    c.sixpar_is_bifacial = 1
    c.sixpar_bifaciality = BIFACIALITY
    c.sixpar_bifacial_ground_clearance_height = 1.5   # m (1P tracker torque tube)
    c.sixpar_bifacial_transmission_factor = 0.013

    # ---- Inverter: 33 x SMA Sunny Central 4600 UP-US (handoff 2026-07-01) ----
    # 4.6 MVA @ 35 C, 690 V AC, 1500 Vdc, MPP 1003-1325 V, CEC eta 98.5 %. 33 units =
    # 151.8 MVA installed; POI capped at 150 MWac via the interconnection limit below.
    # CRITICAL: inverter_model MUST be 1 (datasheet) -- 0 selects the CEC/Sandia model,
    # which ignores inv_ds_* and (with default coeffs) collapses on this large array.
    m.Inverter.inverter_model = 1   # 1 = inverter datasheet
    d = m.InverterDatasheet
    inv_count = 33
    paco = 4.6e6                    # W, SMA SC4600 UP-US nameplate AC
    d.inv_ds_paco = paco
    d.inv_ds_eff = INV_EFF          # CEC weighted efficiency 98.5 %
    d.inv_ds_vdco = 1200.0          # nominal DC (SMA MPP nominal)
    d.inv_ds_vdcmax = 1500.0
    d.inv_ds_pso = 0.001 * paco     # self-consumption (W)
    d.inv_ds_pnt = 0.0001 * paco    # night tare (W)
    m.Inverter.inverter_count = inv_count
    # MPPT window = SMA spec 1003-1325 V; string Vmp (~1007 hot .. ~1220 cold) fits.
    m.Inverter.mppt_low_inverter = 1003.0
    m.Inverter.mppt_hi_inverter = 1325.0
    m.Inverter.inv_num_mppt = 1

    # ---- POI interconnection limit: 150 MWac (33 x 4.6 = 151.8 MVA installed) ----
    m.GridLimits.enable_interconnection_limit = 1
    m.GridLimits.grid_interconnection_limit_kwac = P_AC_KW

    # ---- System design: 1-axis N-S backtracking tracker, GCR 0.32 ----
    s = m.SystemDesign
    s.inverter_count = inv_count
    s.subarray1_track_mode = 1      # 1 = one-axis
    s.subarray1_backtrack = 1
    s.subarray1_tilt = 0.0          # axis tilt
    s.subarray1_azimuth = 180.0     # axis azimuth (N-S)
    s.subarray1_gcr = GCR
    s.subarray1_rotlim = ROTLIM
    s.subarray1_modules_per_string = mps
    s.subarray1_nstrings = nstrings
    s.subarray1_mppt_input = 1

    # ---- Loss tree (sums ~ the 14% aggregate used in PVWatts) ----
    L = m.Losses
    L.subarray1_soiling = SOILING_MONTHLY
    L.subarray1_dcwiring_loss = 2.0
    L.subarray1_mismatch_loss = 1.0
    L.subarray1_diodeconn_loss = 0.5
    L.subarray1_nameplate_loss = 2.5   # 1.0 nameplate + ~1.5 LID (adversarial-review fix)
    L.subarray1_tracking_loss = 1.0    # tracker pointing inaccuracy (was 0)
    L.acwiring_loss = 1.0
    L.transmission_loss = 1.0       # GSU + take-off line to POI
    L.en_snow_model = 0

    # ---- Availability ~2.5% (grid + O&M downtime) ----
    m.AdjustmentFactors.adjust_constant = 2.5

    m.execute(0)
    o = m.Outputs

    dc_stc_kw = wp * mps * nstrings / 1000.0
    annual_ac = o.annual_energy
    ac_cf = annual_ac / (P_AC_KW * HOURS) * 100.0
    dc_cf = annual_ac / (dc_stc_kw * HOURS) * 100.0

    def out(name, default=None):
        return getattr(o, name, default)

    clip_pct = out("annual_ac_inv_clip_loss_percent")   # AC inverter power-clip loss %
    mppt_clip_pct = out("annual_dc_mppt_clip_loss_percent")
    return {
        "dc_stc_kw": dc_stc_kw,
        "modules_per_string": mps,
        "nstrings": nstrings,
        "module_count": mps * nstrings,
        "module_wp": wp,
        "inverter_count": inv_count,
        "inverter_paco_kw": paco / 1000.0,
        "annual_ac_kwh": annual_ac,
        "capacity_factor_ac_pct": ac_cf,                       # year-1
        "capacity_factor_ac_pct_p50_life": ac_cf * LIFE_FACTOR,  # 25-yr avg w/ degradation
        "capacity_factor_dc_pct": dc_cf,
        "specific_yield_kwh_per_kwdc": annual_ac / dc_stc_kw,
        "performance_ratio": out("performance_ratio"),
        "clipping_loss_pct": clip_pct,
        "mppt_window_clip_pct": mppt_clip_pct,
        "ac_monthly_kwh": list(out("monthly_energy") or []),
    }


def chart(by_source):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import numpy as np
    except Exception as e:
        print("  (chart skipped:", e, ")")
        return
    months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
              "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    x = np.arange(12)
    fig, ax = plt.subplots(figsize=(10, 4.5))
    colors = ["#2c6fbb", "#f4a340"]
    width = 0.8 / len(by_source)
    for k, (src, res) in enumerate(by_source.items()):
        pvs = res["pvsamv1"]
        gwh = [v / 1e6 for v in pvs["ac_monthly_kwh"]]
        ax.bar(x + (k - (len(by_source) - 1) / 2) * width, gwh, width,
               label=f"{src}  (AC CF {pvs['capacity_factor_ac_pct']:.1f}%)",
               color=colors[k % len(colors)])
    ax.set_xticks(x); ax.set_xticklabels(months)
    ax.set_ylabel("AC energy at POI (GWh)")
    ax.set_title("Sunfield Solar monthly AC production (Pvsamv1) by weather source")
    ax.legend(); ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    p = os.path.join(BUILD, "sam_monthly.png")
    fig.savefig(p, dpi=110)
    print(f"  saved {p}")


def main():
    require_weather()
    by_source = {}
    for src, wf in WEATHER:
        print(f"\n#### Weather: {src} ({os.path.basename(wf)}) ####")
        pvw = run_pvwatts(wf)
        print(f"  PVWatts v8 : AC CF {pvw['capacity_factor_ac_pct']:.2f}%   "
              f"yield {pvw['specific_yield_kwh_per_kwdc']:.0f}   "
              f"AC {pvw['annual_ac_kwh']/1e6:.1f} GWh")
        pvs = run_pvsam(wf)
        print(f"  Pvsamv1    : AC CF {pvs['capacity_factor_ac_pct']:.2f}%   "
              f"yield {pvs['specific_yield_kwh_per_kwdc']:.0f}   "
              f"AC {pvs['annual_ac_kwh']/1e6:.1f} GWh   PR {pvs['performance_ratio']:.3f}   "
              f"clip {pvs['clipping_loss_pct']:.1f}%")
        by_source[src] = {"weather_file": os.path.relpath(wf, HERE),
                          "pvwatts_v8": pvw, "pvsamv1": pvs}

    # headline AC-CF: CENTRAL of the two independent resources (not the warm PVGIS case).
    # Range spans all models x weather sources; central = mean of the two Pvsamv1 (year-1).
    cfs = []
    for res in by_source.values():
        cfs += [res["pvwatts_v8"]["capacity_factor_ac_pct"],
                res["pvsamv1"]["capacity_factor_ac_pct"]]
    pvs_cfs = [res["pvsamv1"]["capacity_factor_ac_pct"] for res in by_source.values()]
    central = sum(pvs_cfs) / len(pvs_cfs)
    central_p50 = central * LIFE_FACTOR
    warm = by_source[WEATHER[0][0]]["pvsamv1"]     # PVGIS warm bracket
    cool = by_source[WEATHER[1][0]]["pvsamv1"]     # ERA5 cool bracket
    syield_central = sum(r["pvsamv1"]["specific_yield_kwh_per_kwdc"]
                         for r in by_source.values()) / len(by_source)

    print(f"\n== HEADLINE ==  AC capacity factor CENTRAL {central:.1f}% (year-1), "
          f"{central_p50:.1f}% (25-yr P50)  |  range {min(cfs):.1f}-{max(cfs):.1f}%  "
          f"[PVGIS warm {warm['capacity_factor_ac_pct']:.1f} / ERA5 cool {cool['capacity_factor_ac_pct']:.1f}]")

    results = {
        "site": {"lat": 51.18978, "lon": -113.66769, "elevation_m": 961,
                 "location": "Wheatland County, AB"},
        "plant": {"dc_stc_mw": 187.5, "ac_poi_mw": 150.0, "ilr": ILR, "gcr": GCR,
                  "tracker": "1-axis N-S backtracking +/-60deg",
                  "inverter": "33 x SMA Sunny Central 4600 UP-US",
                  "bifaciality": BIFACIALITY},
        "by_weather_source": by_source,
        "headline": {
            "ac_capacity_factor_pct_central_year1": round(central, 2),
            "ac_capacity_factor_pct_central_p50_life": round(central_p50, 2),
            "ac_capacity_factor_pct_range": [round(min(cfs), 1), round(max(cfs), 1)],
            "brackets_pvsam": {"PVGIS_warm": round(warm["capacity_factor_ac_pct"], 2),
                               "ERA5_cool": round(cool["capacity_factor_ac_pct"], 2)},
            "specific_yield_kwh_per_kwdc_central": round(syield_central),
            "basis": ("CENTRAL of two independent resources (PVGIS TMY warm + Open-Meteo "
                      "ERA5 cool); direct NSRDB PSM3 pull was not reachable. Winter snow, "
                      "tracking, and LID losses modeled; P50 = year-1 x 0.94 (0.5%/yr, 25 yr)."),
        },
    }
    p = os.path.join(BUILD, "sam_results.json")
    with open(p, "w") as f:
        json.dump(results, f, indent=2)
    print(f"  saved {p}")
    chart(by_source)


if __name__ == "__main__":
    main()
