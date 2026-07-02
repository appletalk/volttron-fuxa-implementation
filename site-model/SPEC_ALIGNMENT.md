# Model ↔ FUXA-spec alignment / backlog

Tracking how the 3D site model relates to the FUXA/powerplant spec, and what (if
anything) should be back-ported to the docs. Model is the current focus; these
are backlog.

## Model MATCHES the spec (no doc change needed)
- **DC-coupled BESS** — battery containers + DC/DC converters distributed on each
  of the 6 power blocks' inverter DC buses (not an AC-coupled yard). Matches the
  spec's single-line. (Earlier drafts of the model had an AC-coupled central BESS
  yard — corrected.)
- **42 × 4 MVA central inverters** in 6 blocks of 7, each with a 690 V/34.5 kV pad
  transformer → 6 × 34.5 kV collector feeders → 2× GSU 34.5/100 kV → 100 kV POI.
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
- **Unmapped natural drainage/coulee** crosses the north of the field — it is NOT in
  OSM `waterway` data, so the mask doesn't exclude it and PV tables currently overlap
  it. Options: hand-add the drainage polygon to the exclusion, or accept (demo). The
  prominent mapped-scale watercourse to the west IS avoided (adjacent section).
- Equipment counts/dimensions are representative massing, not a stamped GA drawing.

## Doc updates to consider (none required yet)
- If the physical site elements above should appear in the powerplant narrative,
  add a short civil/site-layout note to `docs/POWERPLANT_SPEC.md`. Not needed for
  SCADA/physics accuracy.
