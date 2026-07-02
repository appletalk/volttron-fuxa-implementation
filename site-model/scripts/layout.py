#!/usr/bin/env python
"""Phase-2 accurate plant layout for Sunfield Solar (engineer-reviewed).

Electrical architecture matches the FUXA spec's single-line:
  PV 187.5 MWdc -> 42 x 4 MVA central inverters (690 V), 6 power blocks of 7,
  DC-COUPLED BESS (37.5 MW / 150 MWh) as battery containers + DC/DC converters
  on each block's inverter DC bus (NOT an AC-coupled yard), each inverter with a
  690 V/34.5 kV pad transformer -> 6 x 34.5 kV collector feeders (~469 A) ->
  main substation: 34.5 kV switchgear -> 2 x GSU 34.5/100 kV -> 100 kV bus,
  breakers, dead-end gantry -> POI -> 100 kV transmission take-off line.

Plus the real-site elements: perimeter security fence, access road to the public
road with a gatehouse, O&M compound + parking, met mast, stormwater pond.

PV tables clipped to the OSM open-field mask (geo.py). Panel tilt +30 deg = the
running sim's tracker angle at 15:00 (SUNFIELD_TILT overrides). X=east, Y=north,
metres = model coords.
"""
import json
import math
import os

from shapely.geometry import box as sbox, Point
from shapely.ops import unary_union
import geo
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(HERE, "build")

PITCH = 7.2
CHORD = 2.3
TILT = math.radians(float(os.environ.get("SUNFIELD_TILT", "30")))
PANEL_Z = 2.0
FENCE_SETBACK = 18.0
TABLE_LEN = 52.0
TABLE_GAP = 2.5

MATS = {
    "panel":       [0.055, 0.085, 0.20],
    "inverter":    [0.16, 0.52, 0.34],
    "transformer": [0.40, 0.42, 0.45],
    "bess":        [0.86, 0.87, 0.90],
    "dcdc":        [0.28, 0.45, 0.62],
    "building":    [0.76, 0.72, 0.63],
    "roof":        [0.52, 0.54, 0.57],
    "steel":       [0.63, 0.65, 0.69],
    "insulator":   [0.78, 0.53, 0.28],
    "bus":         [0.80, 0.82, 0.85],
    "switchgear":  [0.58, 0.60, 0.63],
    "road":        [0.54, 0.50, 0.44],
    "gravel":      [0.47, 0.44, 0.39],
    "fence":       [0.33, 0.34, 0.37],
    "water":       [0.20, 0.34, 0.47],
    "conductor":   [0.35, 0.36, 0.38],
    "car1":        [0.85, 0.85, 0.87],
    "car2":        [0.55, 0.12, 0.12],
    "car3":        [0.15, 0.22, 0.40],
}


def bx(name, dims, pos, rot=(0, 0, 0), mat="panel"):
    return {"kind": "box", "name": name, "dims": list(dims), "pos": list(pos), "rot": list(rot), "mat": mat}


def cy(name, r, h, pos, rot=(0, 0, 0), mat="steel"):
    return {"kind": "cyl", "name": name, "r": r, "h": h, "pos": list(pos), "rot": list(rot), "mat": mat}


def parts(geom):
    if geom.is_empty:
        return []
    return list(geom.geoms) if geom.geom_type.startswith("Multi") else [geom]


def fence_along(polygon, P, height=2.1, thick=0.1, mat="fence", simplify=4.0):
    coords = list(polygon.exterior.simplify(simplify).coords)
    for a, b in zip(coords, coords[1:]):
        dx, dy = b[0]-a[0], b[1]-a[1]
        L = math.hypot(dx, dy)
        if L < 1.0:
            continue
        P.append(bx("fence", (L, thick, height), ((a[0]+b[0])/2, (a[1]+b[1])/2, height/2),
                    (0, 0, math.atan2(dy, dx)), mat))


# ----------------------------------------------------------- power block unit
def inverter_skid(x, y, P):
    """4 MVA central inverter (690 V) + its 690 V/34.5 kV pad transformer."""
    P.append(bx("inverter", (5.0, 2.5, 2.6), (x, y, 1.3), (0, 0, 0), "inverter"))
    P.append(bx("inv_tx", (2.6, 2.2, 2.4), (x + 4.2, y, 1.2), (0, 0, 0), "transformer"))
    P.append(bx("inv_pad", (10, 4, 0.1), (x + 1.5, y, 0.05), (0, 0, 0), "gravel"))


def battery_cluster(x, y, P, n_cont=6):
    """DC-coupled BESS for one block: battery containers on a pad + DC/DC skids
    (ties to the block inverters' DC bus -- no separate AC grid transformer)."""
    P.append(bx("bess_pad", (n_cont*4.4 + 8, 20, 0.12), (x + n_cont*2.2, y + 6, 0.06), (0, 0, 0), "gravel"))
    for i in range(n_cont):
        P.append(bx("bess", (2.6, 12.2, 2.9), (x + i*4.4, y + 6, 1.45), (0, 0, 0), "bess"))
    for i in range(2):                      # DC/DC converter skids
        P.append(bx("dcdc", (3.0, 2.6, 2.6), (x + 4 + i*8, y - 5, 1.3), (0, 0, 0), "dcdc"))


def power_block(x0, x1, y0, y1, pv_area, P):
    """One 25 MWac block: 7 distributed inverter skids + a DC-coupled battery cluster."""
    for k in range(7):
        ix = x0 + 20 + (k + 0.5) * (x1 - x0 - 40) / 7.0
        iy = y0 + (0.40 if k % 2 else 0.62) * (y1 - y0)
        if not pv_area.contains(Point(ix, iy)):
            iy = (y0 + y1) / 2
        inverter_skid(ix, iy, P)
    battery_cluster((x0 + x1)/2 - 14, (y0 + y1)/2, P)   # block battery near its centre


# ------------------------------------------------------------- 100 kV station
def gsu(x, y, P):
    P.append(bx("gsu_tank", (8, 5, 5.2), (x, y, 2.6), (0, 0, 0), "transformer"))
    for i in range(6):
        P.append(bx("gsu_rad", (0.4, 5.4, 4.2), (x - 4.2 - i*0.6, y, 2.3), (0, 0, 0), "steel"))
    for o in (-1.7, 0, 1.7):
        P.append(cy("gsu_hv", 0.18, 2.8, (x + 3.2, y + o, 6.0), (0, 0, 0), "insulator"))   # 100 kV
    for o in (-1.2, 1.2):
        P.append(cy("gsu_lv", 0.14, 1.6, (x - 3.2, y + o, 5.4), (0, 0, 0), "insulator"))   # 34.5 kV
    P.append(bx("gsu_pad", (14, 9, 0.15), (x - 1, y, 0.07), (0, 0, 0), "gravel"))


def substation(x0, y0, w, h, P):
    cxs = x0 + w/2
    P.append(bx("sub_pad", (w, h, 0.12), (cxs, y0 + h/2, 0.06), (0, 0, 0), "gravel"))
    # 34.5 kV metal-clad switchgear lineup (6 feeder bays + main)
    for i in range(7):
        P.append(bx("switchgear", (2.2, 3.0, 2.6), (x0 + 14 + i*2.4, y0 + 18, 1.3), (0, 0, 0), "switchgear"))
    P.append(bx("sg_house", (20, 6, 3.4), (x0 + 26, y0 + 18, 1.7), (0, 0, 0), "building"))
    # two GSUs 34.5/100 kV
    gsu(x0 + 30, y0 + 52, P)
    gsu(x0 + 30, y0 + 82, P)
    # station service transformer
    P.append(bx("station_svc", (3, 2.4, 2.6), (x0 + 12, y0 + 66, 1.3), (0, 0, 0), "transformer"))
    # 100 kV bus (tubular Al) on support insulators, two runs
    for by in (y0 + 108, y0 + 118):
        P.append(bx("bus100", (58, 0.24, 0.24), (x0 + 62, by, 6.5), (0, 0, 0), "bus"))
        for sx in range(7):
            P.append(cy("bus_ins", 0.16, 6.0, (x0 + 36 + sx*9, by, 3.2), (0, 0, 0), "insulator"))
    # 100 kV breakers (SF6 dead-tank) + column insulators, one per line/bus tie
    for i in range(2):
        bxp = x0 + 60 + i*22
        P.append(bx("brk_tank", (3.0, 1.8, 1.8), (bxp, y0 + 96, 2.4), (0, 0, 0), "steel"))
        for c in (-0.9, 0, 0.9):
            P.append(cy("brk_col", 0.2, 3.4, (bxp + c, y0 + 96, 4.7), (0, 0, 0), "insulator"))
    # dead-end lattice gantry for the outgoing 100 kV line
    gy = y0 + h - 16
    for gx in (x0 + 55, x0 + 120):
        P.append(bx("gantry_col", (1.3, 1.3, 14), (gx, gy, 7), (0, 0, 0), "steel"))
        P.append(bx("gantry_brace", (0.9, 0.9, 15.5), (gx, gy, 7.2), (0, math.radians(8), 0), "steel"))
    P.append(bx("gantry_beam", (70, 1.0, 1.5), (x0 + 87, gy, 13.6), (0, 0, 0), "steel"))
    for bxp in (x0 + 67, x0 + 87, x0 + 107):     # 3-phase dead-end insulator strings
        P.append(cy("deadend_ins", 0.13, 1.8, (bxp, gy, 12.4), (0, 0, 0), "insulator"))
    # relay/control building
    P.append(bx("relay_bldg", (16, 10, 4.5), (x0 + 14, y0 + h - 20, 2.25), (0, 0, 0), "building"))
    P.append(bx("relay_roof", (16.6, 10.6, 0.5), (x0 + 14, y0 + h - 20, 4.6), (0, 0, 0), "roof"))
    fence_along(sbox(x0, y0, x0 + w, y0 + h), P, height=2.4, simplify=1.0)
    return (cxs, gy)   # gantry point for the transmission line


def transmission_line(start, direction, P, spans=4, span=70, height=17):
    """100 kV single-circuit take-off: H-frame steel towers + 3 conductors."""
    ux, uy = direction
    prev = None
    for i in range(spans + 1):
        tx = start[0] + ux * span * i
        ty = start[1] + uy * span * i
        if i > 0:                                   # H-frame: 2 poles + cross-arm
            for c in (-3.5, 3.5):
                px = tx + (-uy) * c
                py = ty + (ux) * c
                P.append(cy("tl_pole", 0.35, height, (px, py, height/2), (0, 0, 0), "steel"))
            P.append(bx("tl_arm", (9, 0.4, 0.5), (tx, ty, height - 0.8),
                        (0, 0, math.atan2(uy, ux) + math.pi/2), "steel"))
            for c in (-3.2, 0, 3.2):                # 3 phase insulators
                px = tx + (-uy) * c
                py = ty + (ux) * c
                P.append(cy("tl_ins", 0.1, 1.4, (px, py, height - 1.6), (0, 0, 0), "insulator"))
        if prev is not None:                        # conductor spans (3 phases)
            for c in (-3.2, 0, 3.2):
                ax = prev[0] + (-uy) * c; ay = prev[1] + (ux) * c
                bx_ = tx + (-uy) * c;    by_ = ty + (ux) * c
                mx, my = (ax + bx_)/2, (ay + by_)/2
                L = math.hypot(bx_ - ax, by_ - ay)
                P.append(bx("tl_cond", (L, 0.06, 0.06), (mx, my, height - 2.4),
                            (0, 0, math.atan2(by_ - ay, bx_ - ax)), "conductor"))
        prev = (tx, ty)


def gatehouse(x, y, P):
    P.append(bx("gate_gravel", (24, 20, 0.08), (x, y, 0.04), (0, 0, 0), "gravel"))
    P.append(bx("gatehouse", (5, 4, 3), (x - 6, y, 1.5), (0, 0, 0), "building"))
    P.append(bx("gate_arm", (6, 0.3, 0.3), (x + 2, y + 3, 1.2), (0, 0, 0), "fence"))


def om_compound(x0, y0, P):
    P.append(bx("om_pad", (52, 46, 0.1), (x0 + 26, y0 + 23, 0.05), (0, 0, 0), "gravel"))
    P.append(bx("om_bldg", (26, 15, 6.5), (x0 + 16, y0 + 30, 3.25), (0, 0, 0), "building"))
    P.append(bx("om_roof", (26.6, 15.6, 0.5), (x0 + 16, y0 + 30, 6.6), (0, 0, 0), "roof"))
    P.append(bx("warehouse", (18, 12, 5.5), (x0 + 38, y0 + 32, 2.75), (0, 0, 0), "building"))
    P.append(bx("parking", (30, 16, 0.06), (x0 + 22, y0 + 8, 0.03), (0, 0, 0), "road"))
    for i, m in enumerate(["car1", "car2", "car3", "car1", "car3", "car2"]):
        P.append(bx("car", (2.0, 4.6, 1.5), (x0 + 10 + i*3.4, y0 + 8, 0.85), (0, 0, 0), m))


def met_mast(x, y, P, h=62):
    P.append(cy("mast", 0.35, h, (x, y, h/2), (0, 0, 0), "steel"))
    for zz in (h*0.55, h*0.8, h-2):
        P.append(bx("mast_arm", (4.5, 0.2, 0.2), (x, y, zz), (0, 0, 0), "steel"))
    P.append(bx("mast_sensor", (0.6, 0.6, 0.6), (x, y + 2.2, h-1), (0, 0, 0), "bus"))


# ------------------------------------------------------------------- layout
def main():
    cell = geo.build()["anchor_cell"]
    plantable = cell.buffer(-FENCE_SETBACK)
    b = plantable.bounds
    cx = (b[0] + b[2]) / 2.0
    cym = (b[1] + b[3]) / 2.0
    ymin = b[1]

    comp = sbox(cx - 120, ymin + 6, cx + 130, ymin + 240)        # substation + O&M (no AC BESS yard now)
    pond = Point(b[2] - 70, b[1] + 70).buffer(55).intersection(plantable)
    xr1, xr2 = b[0] + (b[2]-b[0])/3.0, b[0] + 2*(b[2]-b[0])/3.0
    roads_geom = unary_union([sbox(xr1-4, b[1], xr1+4, b[3]), sbox(xr2-4, b[1], xr2+4, b[3]),
                              sbox(b[0], cym-4, b[2], cym+4), sbox(cx-4, b[1], cx+4, b[3])])
    pv_area = plantable.difference(comp.buffer(12)).difference(roads_geom.buffer(2)).difference(pond.buffer(10))

    P = []

    # PV field
    n_tab = 0
    x = b[0] + CHORD
    while x < b[2]:
        strip = sbox(x - CHORD/2, b[1], x + CHORD/2, b[3]).intersection(pv_area)
        for p in parts(strip):
            if p.area < CHORD * 15:
                continue
            pb = p.bounds
            y, ye = pb[1], pb[3]
            while y < ye - 12:
                tlen = min(TABLE_LEN, ye - y)
                if tlen < 12:
                    break
                P.append(bx("pv", (CHORD, tlen-0.6, 0.05), (x, y+tlen/2, PANEL_Z), (0, TILT, 0), "panel"))
                n_tab += 1
                y += tlen + TABLE_GAP
        x += PITCH

    # 6 power blocks: distributed inverters + DC-coupled battery clusters
    xedges = [b[0], xr1, xr2, b[2]]
    yedges = [b[1], cym, b[3]]
    for ci in range(3):
        for ri in range(2):
            power_block(xedges[ci], xedges[ci+1], yedges[ri], yedges[ri+1], pv_area, P)

    # substation + transmission take-off (line exits south to the public road)
    _, gantry = substation(cx - 95, ymin + 14, 200, 210, P)
    transmission_line((cx - 8, gantry - 2), (0.0, -1.0), P, spans=3, span=60)

    # O&M, gatehouse (site entrance from the public road), met mast, pond
    om_compound(cx + 45, ymin + 20, P)
    gatehouse(cx, cell.bounds[1] + 20, P)
    met_mast(b[0] + 55, b[3] - 55, P)
    for pg in parts(pond):
        if pg.geom_type == "Polygon":
            pbnd = pg.bounds
            P.append(bx("pond", (pbnd[2]-pbnd[0], pbnd[3]-pbnd[1], 0.1),
                        ((pbnd[0]+pbnd[2])/2, (pbnd[1]+pbnd[3])/2, 0.05), (0, 0, 0), "water"))

    # roads
    P.append(bx("road_ns", (7, b[3]-b[1], 0.08), (cx, cym, 0.04), (0, 0, 0), "road"))
    P.append(bx("road_ew", (b[2]-b[0], 7, 0.08), (cx, cym, 0.04), (0, 0, 0), "road"))
    for xr in (xr1, xr2):
        P.append(bx("road_feeder", (7, b[3]-b[1], 0.08), (xr, cym, 0.04), (0, 0, 0), "road"))
    P.append(bx("road_entrance", (8, 90, 0.08), (cx, cell.bounds[1] + 40, 0.04), (0, 0, 0), "road"))

    fence_along(plantable, P)

    out = {"anchor": [geo.ANCHOR_LAT, geo.ANCHOR_LON], "mats": MATS, "prims": P,
           "tilt_deg": round(math.degrees(TILT), 1)}
    json.dump(out, open(os.path.join(BUILD, "plan.json"), "w"))
    render(cell, plantable, comp, P, os.path.join(BUILD, "plan_layout.png"))
    print("PV tables: %d | total prims: %d | tilt %.0f | DC-coupled BESS distributed to 6 blocks"
          % (n_tab, len(P), math.degrees(TILT)))
    print("wrote build/plan.json and build/plan_layout.png")


def render(cell, plantable, comp, prims, png):
    b = cell.bounds
    W, H, pad = 1200, 1300, 30
    s = min((W - 2*pad) / (b[2]-b[0]), (H - 2*pad) / (b[3]-b[1]))

    def px(x, y):
        return (pad + (x - b[0]) * s, H - (pad + (y - b[1]) * s))

    img = Image.new("RGB", (W, H), (40, 46, 28))
    d = ImageDraw.Draw(img, "RGBA")

    def outline(g, col, w=2):
        for gg in (g.geoms if g.geom_type.startswith("Multi") else [g]):
            if gg.geom_type == "Polygon":
                d.line([px(*c) for c in gg.exterior.coords], fill=col, width=w)

    outline(cell, (255, 255, 255, 255), 2)
    outline(comp, (200, 200, 120, 220), 2)

    def col(mat):
        r, g, bl = MATS[mat]
        return (int(r*255), int(g*255), int(bl*255), 255)

    for p in prims:
        if p["kind"] == "box":
            if p["dims"][0] == 0:
                continue
            w2, h2 = p["dims"][0]/2, p["dims"][1]/2
            x0, y0 = px(p["pos"][0]-w2, p["pos"][1]-h2)
            x1, y1 = px(p["pos"][0]+w2, p["pos"][1]+h2)
            d.rectangle([min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)], fill=col(p["mat"]))
        else:
            cxp, cyp = px(p["pos"][0], p["pos"][1])
            rr = max(1.5, p["r"] * s)
            d.ellipse([cxp-rr, cyp-rr, cxp+rr, cyp+rr], fill=col(p["mat"]))

    x0, y0 = pad, H - 14
    d.line([x0, y0, x0 + 500*s, y0], fill=(255, 255, 255), width=3)
    d.text((x0, y0-13), "500 m", fill=(255, 255, 255))
    img.save(png)


if __name__ == "__main__":
    main()
