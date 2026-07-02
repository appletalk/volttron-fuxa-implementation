# Model ↔ FUXA-spec alignment / backlog

Tracking how the 3D site model relates to the FUXA/powerplant spec, and what (if
anything) should be back-ported to the docs. Model is the current focus; these
are backlog.

## Model MATCHES the spec (no doc change needed)
- **DC-coupled BESS** — battery containers + DC/DC converters distributed on each
  of the 6 power blocks' inverter DC buses (not an AC-coupled yard). Matches the
  spec's single-line. (Earlier drafts of the model had an AC-coupled central BESS
  yard — corrected.)
- **33 × SMA Sunny Central 4600 UP-US** (4.6 MVA, 690 V) on **MVPS-S2** stations
  (inverter + 690 V/34.5 kV MV transformer + MV vacuum breaker), 6 feeders of 5–6
  (≈462 A < 600 A) → 2× GSU 34.5/100 kV → 100 kV POI. 151.8 MVA installed (POI cap 150).
- **1P single-axis trackers, GCR 0.32 (7.2 m pitch)**, N–S torque tubes.
- **Panel tilt +30°** = the running sim's `tracker_angle_deg` at 15:00.
- Plant footprint 150 MWac / ~254 ha of array in the road-bounded field.

## Physical site elements ADDED beyond the FUXA SCADA scope (no point-model impact)
These are civil/site-realism additions; they add no SCADA points, so no spec/registry
change is required. Optionally document them in a future "site/civil" section:
- 100 kV transmission take-off line (H-frame towers + conductors) to the public road.
- Stormwater retention pond, perimeter security fence, gatehouse + site entrance road.
- O&M compound (offices, warehouse, parking), station-service transformer, met mast.

## Divergences / liberties to revisit (backlog)
- **GCR 0.32 is a pre-sweep engineering estimate** (latitude + industry LCOE band),
  not a simulated optimum. A PVsyst/SAM yield-vs-LCOE sweep would confirm the value.
- **Creek/coulee (RESOLVED):** OSM has no waterway here; the coulee runs E-W across
  the north of the section at local y ~ +700..+1100 m (georeferenced from the Google
  pin overview + ESRI extent; verified on the satellite underlay). The OSM field's
  "north edge" (y=+1300) was just the footprint box, over-extending past the section
  into the creek. FIX: cap the plant's north boundary at y=+600 m (watercourse
  setback) so it fills the section SOUTH of the creek. ~150-200 m open buffer to the
  channel. Colour-based auto-detection failed (ESRI is a green-season capture with
  almost no creek/field contrast).
- **CAPACITY (RESOLVED -> two parcels):** one section south of the creek holds only
  ~92 MWac at GCR 0.32, so the plant now spans TWO parcels for the full ~150 MWac:
  the creek-capped north section (Township Rd 260 -> y=+600) + a band of the section
  south of Township Rd 260 (y=-820 -> -1520), sharing one substation on the corridor.
  ~7810 tables ~= 150 MWac. Real 150 MW plants routinely span multiple parcels.
- **SATELLITE ALIGNMENT (bug found + fixed):** an apparent "we cut the west creek"
  turned out to be a STALE-CACHED underlay -- Blender's image `load(check_existing=
  True)` kept the old single-parcel satellite after a re-fetch (same filename) and
  stretched it onto the new-extent plane, shifting the creek ~200 m east into the
  panels. Force-reload fixed it; on the correct imagery the plant clears the north
  creek (cap), the west creek (it's west of Hwy 9, adjacent section), the corridor,
  and the south wetland. Verified by overlaying exact OSM road centrelines (they land
  on the satellite roads). NOTE for future rebuilds: always force-reload the image.
- **Inverter service roads (added per engineer review):** every inverter/transformer
  skid now sits on an E-W service road (with N-S collector roads on block boundaries),
  and each DC-coupled battery cluster has an access spur -- full maintenance access.
- Equipment counts/dimensions are representative massing, not a stamped GA drawing.
- **Inverter locked to a real unit (2026-07-01): SMA Sunny Central 4600 UP-US on MVPS-S2.**
  Layout went from 42 × ~4 MVA to **33 × SC4600 UP-US** (4.6 MVA, 690 V) distributed
  [6,6,6,5]/[5,5] over the 6 feeders (151.8 MVA installed). `inverter_skid` now models the
  MVPS station (inverter + MV transformer + MV breaker). Datasheets:
  `SC4xxxUP-DS-en_us-39.pdf`, `MVPS-S2-SC40-46-UP-US-DS-en_us-30.pdf`.
- **DC input / combiner determination (settled by a cable takeoff):** the SC4600 UP has
  **32 single-pole (or 24 double-pole) fused inputs**, I_DC,max 4750 A, PV fuses 200–630 A.
  Single-pole fusing is permitted (CEC 2024 / IEC practice), and AC-coupled storage frees
  all inputs for PV. **Design: populate 28 of 32 single-pole inputs, 4 spare** (~203 kW/input,
  ~169 A op, 315 A fuse, ~300 kcmil Al aerial trunk). At 33 inverters the DC loading (5.68
  MWdc/inv) sits right at the 4750 A input limit, so 187.5 MWdc ≈ max these units should carry.
- **Cable takeoff (2026-07-01) — why NOT to embiggen combiners here.** Proposed conductors:
  string homeruns **#10 AWG copper RPVU90** (~21 A req), combiner→inverter feeders **aluminum
  RPVU** sized to fuse (4/0 @ 32-input / 350 kcmil @ 24 / 600 kcmil @ 18). Pricing (Jul 2026):
  Cu $6.11/lb, Al $1.46/lb; #10 Cu RPVU CA$2.50–3.00/m (Frankensolar); 350 MCM Al PV-2kV
  ~$7.13/ft (WCYW). Takeoff over 10,150 strings / 272 ha / **aerial** collection: 32-input
  ≈ $2.87M, 24 ≈ $2.91M, 18 ≈ $3.06M DC-collection cable+box+term. **Embiggening does NOT
  pay:** collection is aerial trunk-bus (no trench — the usual reason to embiggen is absent),
  so copper string homeruns dominate and *lengthen* as combiners sparsen (34→46 m avg), and
  the Al size-premium (4/0→600 kcmil) outpaces the fewer-boxes savings. **18-input is always
  worst; 24 vs 32 is ±1–4% (noise)** — 24 wins only if copper is cheap AND termination labor
  is high; at Canadian RPVU copper prices 32 wins in every case. → **more/smaller/closer
  combiners; populate ~28, keep a few spare. Do NOT go to 18/24 for a cable saving.**
- **DEFERRED — AC-couple the BESS.** DC-coupled storage is a weak economic play (clip
  recovery ≈ a few % of annual energy); an **AC-coupled** 37.5 MW / 150 MWh yard on the
  34.5 kV bus (SMA SCS-UP-US) earns AESO ancillary services + arbitrage and dispatches
  independent of PV. Re-architecture (battery yard in layout + 3D model, sim.py physics,
  `POWERPLANT_SPEC.md` signature story) is a separate task; the model/sim/spec stay
  DC-coupled until then. The PV-only AC capacity factor SAM reports is unaffected.
- **KMZ NOT yet rebuilt for the 33-station layout.** `plan.json` + `plan_layout.png` are
  regenerated, but the Google-Earth KMZ needs the Blender (BlenderMCP) → glTF → `.dae` →
  `make_kmz.py` pipeline re-run to pick up the new station count/geometry.

## Doc updates to consider (none required yet)
- If the physical site elements above should appear in the powerplant narrative,
  add a short civil/site-layout note to `docs/POWERPLANT_SPEC.md`. Not needed for
  SCADA/physics accuracy.
