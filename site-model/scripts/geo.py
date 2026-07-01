#!/usr/bin/env python
"""Site geo layer for the Sunfield Solar model.

Fetches OSM roads + watercourses around the plant anchor, projects them to a
local ENU metric frame (X=east, Y=north, metres) centred on the anchor -- the
SAME frame the Blender/COLLADA model uses, so exclusion polygons are directly a
placement mask -- buffers them as setbacks, and derives the open-field polygons
the plant may occupy.  Renders a plan-view QA PNG.
"""
import json
import math
import os
import sys
import urllib.request

from shapely.geometry import LineString, Polygon, box, Point
from shapely.ops import unary_union
from shapely import wkt as shapely_wkt
from PIL import Image, ImageDraw

ANCHOR_LAT, ANCHOR_LON = 51.18978, -113.66769
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(HERE, "build")
OSM_CACHE = os.path.join(BUILD, "osm.json")

# half-footprint we consider for the plant (metres from anchor)
HALF = 1300.0

# setback (metres) applied as a buffer around each feature centreline/edge
ROAD_SETBACK = {
    "trunk": 30.0, "primary": 25.0, "secondary": 22.0, "tertiary": 18.0,
    "unclassified": 14.0, "residential": 14.0, "service": 10.0, "track": 10.0,
}
DEFAULT_ROAD_SETBACK = 14.0
WATER_SETBACK = 25.0     # rivers / streams / canals
WATERBODY_SETBACK = 20.0

M_PER_DEG_LAT = 111132.0
M_PER_DEG_LON = 111320.0 * math.cos(math.radians(ANCHOR_LAT))


def to_local(lat, lon):
    return ((lon - ANCHOR_LON) * M_PER_DEG_LON,
            (lat - ANCHOR_LAT) * M_PER_DEG_LAT)


def fetch_osm():
    if os.path.exists(OSM_CACHE):
        return json.load(open(OSM_CACHE))
    dlat = (HALF + 400) / M_PER_DEG_LAT
    dlon = (HALF + 400) / M_PER_DEG_LON
    s, w = ANCHOR_LAT - dlat, ANCHOR_LON - dlon
    n, e = ANCHOR_LAT + dlat, ANCHOR_LON + dlon
    bbox = "%f,%f,%f,%f" % (s, w, n, e)
    q = ("[out:json][timeout:60];("
         'way["highway"](%s);'
         'way["waterway"](%s);'
         'way["natural"="water"](%s);'
         'way["landuse"](%s);'
         ");out geom;") % (bbox, bbox, bbox, bbox)
    req = urllib.request.Request(
        "https://overpass-api.de/api/interpreter",
        data=q.encode(), headers={"User-Agent": "sunfield-site-model/1.0"})
    data = json.loads(urllib.request.urlopen(req, timeout=90).read())
    json.dump(data, open(OSM_CACHE, "w"))
    return data


def build():
    data = fetch_osm()
    roads, waters, waterbodies, farmland = [], [], [], []
    for el in data.get("elements", []):
        if el.get("type") != "way" or "geometry" not in el:
            continue
        t = el.get("tags", {})
        pts = [to_local(g["lat"], g["lon"]) for g in el["geometry"]]
        if len(pts) < 2:
            continue
        if "highway" in t:
            sb = ROAD_SETBACK.get(t["highway"], DEFAULT_ROAD_SETBACK)
            roads.append(LineString(pts).buffer(sb, cap_style=2))
        elif "waterway" in t:
            waters.append(LineString(pts).buffer(WATER_SETBACK, cap_style=2))
        elif t.get("natural") == "water" and len(pts) >= 3:
            waterbodies.append(Polygon(pts).buffer(WATERBODY_SETBACK))
        elif "landuse" in t and len(pts) >= 3:
            farmland.append((t["landuse"], Polygon(pts)))

    exclusion = unary_union(roads + waters + waterbodies) if (roads or waters or waterbodies) else Polygon()
    footprint = box(-HALF, -HALF, HALF, HALF)
    open_area = footprint.difference(exclusion)

    # split into field cells; keep sizeable ones, mark the anchor's cell
    cells = list(open_area.geoms) if open_area.geom_type == "MultiPolygon" else [open_area]
    cells = [c for c in cells if c.area > 5000.0]      # drop slivers < 0.5 ha
    cells.sort(key=lambda c: c.area, reverse=True)
    anchor_pt = Point(0, 0)
    return dict(exclusion=exclusion, footprint=footprint, cells=cells,
                roads=roads, waters=waters + waterbodies, farmland=farmland,
                anchor_cell=next((c for c in cells if c.contains(anchor_pt)), cells[0] if cells else None))


def render(model, png):
    W = H = 1100
    pad = 40
    scale = (W - 2 * pad) / (2 * HALF)

    def px(x, y):
        return (pad + (x + HALF) * scale, H - (pad + (y + HALF) * scale))

    img = Image.new("RGB", (W, H), (54, 62, 34))       # farmland backdrop
    d = ImageDraw.Draw(img, "RGBA")

    def poly(geom, fill=None, outline=None, width=1):
        if geom.is_empty:
            return
        gs = geom.geoms if geom.geom_type.startswith("Multi") else [geom]
        for g in gs:
            if g.geom_type != "Polygon":
                continue
            d.polygon([px(*c) for c in g.exterior.coords], fill=fill, outline=outline, width=width)
            for ring in g.interiors:
                d.polygon([px(*c) for c in ring.coords], fill=(54, 62, 34))

    # open field cells (green), largest = brightest
    for i, c in enumerate(model["cells"]):
        poly(c, fill=(70, 120, 55, 255) if c is model["anchor_cell"] else (60, 96, 48, 255))
    # exclusions on top: roads gray, water blue
    for r in model["roads"]:
        poly(r, fill=(90, 90, 96, 255))
    for wgeom in model["waters"]:
        poly(wgeom, fill=(60, 110, 180, 255))
    # anchor
    ax, ay = px(0, 0)
    d.ellipse([ax - 6, ay - 6, ax + 6, ay + 6], fill=(230, 60, 60), outline=(255, 255, 255))
    # scale bar 500 m
    x0, y0 = pad, H - 18
    d.line([x0, y0, x0 + 500 * scale, y0], fill=(255, 255, 255), width=3)
    d.text((x0, y0 - 14), "500 m", fill=(255, 255, 255))
    img.save(png)


if __name__ == "__main__":
    m = build()
    total = sum(c.area for c in m["cells"])
    print("open-field cells (>0.5 ha): %d, total open area %.1f ha (%.0f acres)"
          % (len(m["cells"]), total / 1e4, total / 4046.86))
    for i, c in enumerate(m["cells"][:6]):
        b = c.bounds
        tag = "  <- anchor cell" if c is m["anchor_cell"] else ""
        print("  cell %d: %.1f ha, extent %.0f x %.0f m%s"
              % (i, c.area / 1e4, b[2] - b[0], b[3] - b[1], tag))
    open(os.path.join(BUILD, "anchor_cell.wkt"), "w").write(m["anchor_cell"].wkt if m["anchor_cell"] else "")
    render(m, os.path.join(BUILD, "plan_view.png"))
    print("wrote build/plan_view.png and build/anchor_cell.wkt")
