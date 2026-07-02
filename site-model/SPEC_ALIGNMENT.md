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

## Doc updates to consider (none required yet)
- If the physical site elements above should appear in the powerplant narrative,
  add a short civil/site-layout note to `docs/POWERPLANT_SPEC.md`. Not needed for
  SCADA/physics accuracy.
