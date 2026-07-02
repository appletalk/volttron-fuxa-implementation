# Sunfield Solar — 3D site model (→ KMZ for Google Earth)

A terrain-aware 3D massing model of the demo's **Sunfield Solar 150 MWac PV + 37.5 MW/150 MWh BESS** plant,
placed on real Alberta farmland at **51°11′23.2″N 113°40′03.7″W** (`51.18978, −113.66769`,
Wheatland County), exported as a colored KMZ for Google Earth.

## Pipeline

```
geo.py        OSM roads/watercourses → local metric frame → open-field mask (avoids roads + river)
  ↓
layout.py     conform the plant into the field → build/plan.json (490 primitives) + plan_layout.png
  ↓
Blender (MCP) instantiate plan.json, join per material, export Z-up glTF  → build/plant.glb
  ↓
glb_to_dae.py trimesh + pycollada → COLLADA .dae (Z_UP, meters, 0..1 colors) → build/plant.dae
  ↓
make_kmz.py   KML <Model> placement at the anchor + zip → out/sunfield_solar.kmz
```

Blender 5.x dropped native COLLADA, so we export glTF and convert. Everything is authored/verified
through the **BlenderMCP** server (`uvx blender-mcp`, addon on port 9876); the venv (`.venv`, gitignored)
holds the converter + geo toolkit (trimesh, pycollada, shapely, Pillow).

## Regenerate

```sh
.venv/bin/python scripts/geo.py        # mask + plan_view.png   (cached OSM in build/osm.json)
.venv/bin/python scripts/layout.py     # plan.json + plan_layout.png
# → build plant.glb in Blender from build/plan.json (via BlenderMCP), then:
.venv/bin/python scripts/glb_to_dae.py build/plant.glb build/plant.dae
.venv/bin/python scripts/make_kmz.py   build/plant.dae out/sunfield_solar.kmz 51.18978 -113.66769
```

## Contents (parametric off the plant spec)

428 PV rows (1P single-axis, N–S torque tube, GCR 0.32 / 7.2 m pitch, clipped to the field) ·
33 × SMA Sunny Central 4600 UP-US on MVPS-S2 stations (6 feeders of 5–6) · substation compound (2× GSU, 100 kV POI gantry, control building) ·
BESS yard (36 containers) · coupled district-heating substation + campus buildings · met mast · access roads.

Anchor/heading and every plant dimension are parameters — see the constants at the top of `layout.py`.
