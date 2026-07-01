#!/usr/bin/env python
"""Phase-1 plant layout for Sunfield Solar.

Conforms the real plant into the open-field mask (from geo.py) in the local
metric frame (X=east, Y=north, metres = the model's own coordinates):
  - PV field: 1P single-axis rows, N-S torque tube, GCR 0.32 (7.2 m pitch),
    tilted panel slabs, CLIPPED to the field polygon so nothing lands on a
    road, the river-side, or the reserved compound.
  - 6 inverter/transformer block stations, a substation compound (2x GSU,
    100 kV POI gantry, control building), a BESS yard (37.5 MW/150 MWh -> 36
    containers), the coupled district-heating substation + campus buildings,
    a met mast, and main access roads.

Emits build/plan.json (consumed by the Blender builder) and build/plan_layout.png.
"""
import json
import math
import os

from shapely.geometry import box as sbox, Point
from shapely.ops import unary_union
from shapely import wkt as shapely_wkt
from PIL import Image, ImageDraw

import geo

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(HERE, "build")

PITCH = 7.2          # row pitch, m  (GCR 0.32, 1P chord 2.3 m)
CHORD = 2.3          # collector width, m
TILT = math.radians(30)
PANEL_Z = 2.0        # torque-tube height, m
FENCE_SETBACK = 18.0

MATS = {
    "panel":       [0.09, 0.11, 0.24],
    "tube":        [0.50, 0.50, 0.53],
    "inverter":    [0.15, 0.55, 0.32],
    "transformer": [0.42, 0.44, 0.47],
    "bess":        [0.88, 0.89, 0.92],
    "building":    [0.72, 0.69, 0.60],
    "steel":       [0.60, 0.62, 0.66],
    "road":        [0.34, 0.30, 0.24],
}


def bx(name, dims, pos, rot=(0, 0, 0), mat="panel"):
    return {"kind": "box", "name": name, "dims": list(dims),
            "pos": list(pos), "rot": list(rot), "mat": mat}


def cy(name, r, h, pos, rot=(0, 0, 0), mat="steel"):
    return {"kind": "cyl", "name": name, "r": r, "h": h,
            "pos": list(pos), "rot": list(rot), "mat": mat}


def parts(geom):
    if geom.is_empty:
        return []
    return list(geom.geoms) if geom.geom_type.startswith("Multi") else [geom]


def main():
    cell = geo.build()["anchor_cell"]
    plantable = cell.buffer(-FENCE_SETBACK)
    b = plantable.bounds
    cx = (b[0] + b[2]) / 2.0
    ymin = b[1]

    # reserved compound (substation + BESS + campus) along the south edge
    comp = sbox(cx - 170, ymin + 6, cx + 170, ymin + 235)
    # main internal roads: N-S spine + E-W mid
    ycen = (b[1] + b[3]) / 2.0
    roads_geom = unary_union([sbox(cx - 4, b[1], cx + 4, b[3]),
                              sbox(b[0], ycen - 4, b[2], ycen + 4)])
    pv_area = plantable.difference(comp.buffer(10)).difference(roads_geom.buffer(2))

    prims = []

    # ---- PV field: rows clipped to the open area --------------------------
    n_slabs = 0
    x = b[0] + CHORD
    while x < b[2]:
        strip = sbox(x - CHORD / 2, b[1], x + CHORD / 2, b[3]).intersection(pv_area)
        for p in parts(strip):
            if p.area < CHORD * 15:
                continue
            pb = p.bounds
            length = (pb[3] - pb[1]) - 1.0
            if length < 15:
                continue
            yc = (pb[1] + pb[3]) / 2.0
            prims.append(bx("pv", (CHORD, length, 0.05), (x, yc, PANEL_Z), (0, TILT, 0), "panel"))
            n_slabs += 1
        x += PITCH

    # ---- 6 block inverter/transformer stations (3 x 2 over the field) -----
    for ix in range(3):
        for iy in range(2):
            sx = b[0] + (ix + 0.5) * (b[2] - b[0]) / 3.0
            sy = ycen + (18 if iy else -18)          # just off the E-W road
            prims.append(bx("inv_stn", (6, 3, 3), (sx - 4, sy, 1.5), (0, 0, 0), "inverter"))
            prims.append(bx("blk_xfmr", (4, 3, 3.5), (sx + 3, sy, 1.75), (0, 0, 0), "transformer"))

    # ---- substation compound ---------------------------------------------
    sub_x, sub_y = cx + 55, ymin + 90
    prims.append(bx("gsu_1", (7, 4.5, 5), (sub_x, sub_y, 2.5), (0, 0, 0), "transformer"))
    prims.append(bx("gsu_2", (7, 4.5, 5), (sub_x + 16, sub_y, 5.0 / 2), (0, 0, 0), "transformer"))
    prims.append(bx("control_bldg", (16, 9, 5), (cx + 30, ymin + 40, 2.5), (0, 0, 0), "building"))
    # 100 kV POI gantry: columns + bus
    for gx in (sub_x + 30, sub_x + 45, sub_x + 60):
        prims.append(bx("poi_col", (0.6, 8, 9), (gx, sub_y + 30, 4.5), (0, 0, 0), "steel"))
    prims.append(bx("poi_bus", (34, 0.6, 0.6), (sub_x + 45, sub_y + 30, 9), (0, 0, 0), "steel"))

    # ---- BESS yard: 36 containers (6 x 6), 37.5 MW / 150 MWh --------------
    bxx, byy = cx - 150, ymin + 40
    for i in range(6):
        for j in range(6):
            prims.append(bx("bess", (12, 2.6, 2.9), (bxx + i * 4.2, byy + j * 8.5, 1.45), (0, 0, 0), "bess"))

    # ---- coupled district-heating substation + campus buildings ----------
    prims.append(bx("dh_substation", (22, 15, 7), (cx - 30, ymin + 160, 3.5), (0, 0, 0), "steel"))
    for k, (dx, dy) in enumerate([(35, 0), (70, 5), (35, 45)]):
        prims.append(bx("campus", (26, 17, 9), (cx - 30 + dx, ymin + 160 + dy, 4.5), (0, 0, 0), "building"))

    # ---- met mast + main roads -------------------------------------------
    prims.append(cy("met_mast", 0.3, 60, (b[0] + 45, b[3] - 45, 30), (0, 0, 0), "steel"))
    prims.append(bx("road_ns", (7, b[3] - b[1], 0.08), (cx, ycen, 0.04), (0, 0, 0), "road"))
    prims.append(bx("road_ew", (b[2] - b[0], 7, 0.08), (cx, ycen, 0.04), (0, 0, 0), "road"))

    out = {"anchor": [geo.ANCHOR_LAT, geo.ANCHOR_LON], "mats": MATS, "prims": prims}
    json.dump(out, open(os.path.join(BUILD, "plan.json"), "w"))

    # ---- plan-view QA -----------------------------------------------------
    render(cell, plantable, comp, prims, os.path.join(BUILD, "plan_layout.png"))
    mw_note = n_slabs * (PITCH * CHORD) / 1e4   # very rough active ha
    print("PV row-slabs: %d  | total prims: %d  | ~%.0f ha of rows"
          % (n_slabs, len(prims), pv_area.area / 1e4))
    print("wrote build/plan.json and build/plan_layout.png")


def render(cell, plantable, comp, prims, png):
    b = cell.bounds
    W, H, pad = 1200, 1300, 30
    sx = (W - 2 * pad) / (b[2] - b[0])
    sy = (H - 2 * pad) / (b[3] - b[1])
    s = min(sx, sy)

    def px(x, y):
        return (pad + (x - b[0]) * s, H - (pad + (y - b[1]) * s))

    img = Image.new("RGB", (W, H), (40, 46, 28))
    d = ImageDraw.Draw(img, "RGBA")

    def outline(geom, col, w=2):
        for g in (geom.geoms if geom.geom_type.startswith("Multi") else [geom]):
            if g.geom_type == "Polygon":
                d.line([px(*c) for c in g.exterior.coords], fill=col, width=w)

    outline(cell, (255, 255, 255, 255), 2)
    outline(plantable.difference(comp) if plantable.geom_type == "Polygon" else plantable, (120, 200, 120, 160), 1)
    outline(comp, (200, 200, 120, 220), 2)

    def col(mat):
        r, g, bl = MATS[mat]
        return (int(r * 255), int(g * 255), int(bl * 255), 255)

    for p in prims:
        if p["kind"] == "box":
            w2 = p["dims"][0] / 2.0
            h2 = p["dims"][1] / 2.0
            x0, y0 = px(p["pos"][0] - w2, p["pos"][1] - h2)
            x1, y1 = px(p["pos"][0] + w2, p["pos"][1] + h2)
            d.rectangle([min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)], fill=col(p["mat"]))
        else:
            cxp, cyp = px(p["pos"][0], p["pos"][1])
            rr = max(2, p["r"] * s)
            d.ellipse([cxp - rr, cyp - rr, cxp + rr, cyp + rr], fill=col(p["mat"]))

    ax, ay = px(0, 0)
    d.ellipse([ax - 5, ay - 5, ax + 5, ay + 5], outline=(255, 60, 60), width=2)
    x0, y0 = pad, H - 14
    d.line([x0, y0, x0 + 500 * s, y0], fill=(255, 255, 255), width=3)
    d.text((x0, y0 - 13), "500 m", fill=(255, 255, 255))
    img.save(png)


if __name__ == "__main__":
    main()
