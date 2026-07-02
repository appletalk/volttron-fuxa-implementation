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

from shapely.geometry import box as sbox, Point, LineString
from shapely.ops import unary_union
import geo

# The creek/coulee runs E-W across the north of the section -- georeferenced from
# the Google pin overview + the ESRI extent to local y ~ +700..+1100 m (right where
# the north township line sits: Township Rd 260 is at y=-710, +1 mile ~= +900). OSM
# has no waterway for it, and the field's OSM "north edge" (y=+1300) was just the
# footprint box, over-extending past the section into the creek. So instead of
# threading panels through a meander we can't detect reliably, cap the plant's north
# boundary with a realistic watercourse setback and fill the section SOUTH of it.
NORTH_CAP = 600.0
# Two-parcel plant: the creek-capped north section + a band of the section south of
# Township Rd 260 (y=-710), sized to reach ~150 MWac total without hitting the south
# wetland/farmsteads. Roads: RR272/Hwy9 x=-813 (W), RR271 x=+800 (E), south township
# line ~ -2319. Corridor left open across Township Rd 260.
TR260_Y = -710.0
SOUTH_TOP = -820.0        # north edge of south array (100 m setback S of Township Rd 260)
SOUTH_CAP = -1520.0       # south edge of south array (tops the plant up to ~150 MWac)
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
    "combiner":    [0.30, 0.36, 0.44],
    "dc_cable":    [0.12, 0.12, 0.14],
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
def fbox(fb):
    return sbox(fb[0], fb[2], fb[1], fb[3])              # (x0,x1,y0,y1) -> shapely box


def feeder_roads(fb, P):
    x0, x1, y0, y1 = fb
    xr1, xr2 = x0 + (x1-x0)/3.0, x0 + 2*(x1-x0)/3.0
    yc = (y0 + y1)/2.0
    for xr in (xr1, xr2):
        P.append(bx("road_feeder", (7, y1-y0, 0.08), (xr, yc, 0.04), (0, 0, 0), "road"))
    P.append(bx("road_ew", (x1-x0, 7, 0.08), ((x0+x1)/2, yc, 0.04), (0, 0, 0), "road"))
    return unary_union([sbox(xr1-4, y0, xr1+4, y1), sbox(xr2-4, y0, xr2+4, y1),
                        sbox(x0, yc-4, x1, yc+4)])


def fill_field(fb, ncols, nrows, P, comp=None, pond=None):
    """Fill one road-bounded parcel with a full internal ACCESS ROAD network so
    every inverter/battery skid has service access:
      - N-S collector roads on the block-column boundaries,
      - an E-W service road along each block row's inverter line (skids sit on it),
      - a short spur to each block's DC-coupled battery cluster.
    PV tracker rows are then clipped to everything (roads, comp, pond)."""
    x0, x1, y0, y1 = fb
    W, H = x1 - x0, y1 - y0
    rg = []
    # N-S collector roads (internal column boundaries)
    for ci in range(1, ncols):
        xc = x0 + ci * W / ncols
        P.append(bx("road_ns", (6, H, 0.08), (xc, (y0+y1)/2, 0.04), (0, 0, 0), "road"))
        rg.append(sbox(xc-3, y0, xc+3, y1))
    # E-W service road along each block-row inverter line
    ays = []
    for ri in range(nrows):
        ay = y0 + (ri + 0.5) * H / nrows
        ays.append(ay)
        P.append(bx("road_svc", (W, 5, 0.07), ((x0+x1)/2, ay, 0.035), (0, 0, 0), "road"))
        rg.append(sbox(x0, ay-8, x1, ay+11))                 # corridor for the skids on it
    # battery-cluster access spurs (one per block)
    for ci in range(ncols):
        for ri in range(nrows):
            scx = x0 + (ci + 0.5) * W / ncols
            ay = ays[ri]
            P.append(bx("road_spur", (5, 52, 0.07), (scx, ay-28, 0.035), (0, 0, 0), "road"))
            rg.append(sbox(scx-3, ay-54, scx+3, ay))
            rg.append(sbox(scx-22, ay-64, scx+22, ay-40))     # battery pad clearing
    roads = unary_union(rg)
    pv = fbox(fb).difference(roads.buffer(2))
    if pond is not None:
        pv = pv.difference(pond.buffer(10))
    if comp is not None:
        pv = pv.difference(comp.buffer(12))
    # PV rows
    n = 0
    x = x0 + CHORD
    while x < x1:
        strip = sbox(x - CHORD/2, y0, x + CHORD/2, y1).intersection(pv)
        for p in parts(strip):
            if p.area < CHORD * 15:
                continue
            pb = p.bounds
            yy, ye = pb[1], pb[3]
            while yy < ye - 12:
                tlen = min(TABLE_LEN, ye - yy)
                if tlen < 12:
                    break
                P.append(bx("pv", (CHORD, tlen-0.6, 0.05), (x, yy+tlen/2, PANEL_Z), (0, TILT, 0), "panel"))
                n += 1
                yy += tlen + TABLE_GAP
        x += PITCH
    # inverter skids ON the service roads + battery clusters on their spurs
    for ci in range(ncols):
        for ri in range(nrows):
            bx0 = x0 + ci * W / ncols
            bx1 = x0 + (ci+1) * W / ncols
            ay = ays[ri]
            for k in range(7):
                ix = bx0 + 22 + (k + 0.5) * (bx1 - bx0 - 44) / 7.0
                inverter_skid(ix, ay + 7, P)                  # skid right beside the E-W service road
            battery_cluster((bx0+bx1)/2 - 14, ay - 52, P)     # battery on the spur
            # DC collection: AERIAL TRUNK BUS (not buried -- poor prairie-soil ampacity;
            # trunk-bus tap connectors must stay accessible) + recombiner boxes -> the
            # block's central inverters, where the DC-coupled battery's DC/DC also ties in.
            for k in range(4):                                # recombiners, S side of the service road
                P.append(bx("combiner", (2.2, 1.6, 2.2),
                            (bx0 + 70 + k*(bx1-bx0-140)/3.0, ay - 8, 1.1), (0, 0, 0), "combiner"))
            postx = bx0 + 30                                  # messenger posts along the row
            while postx < bx1 - 30:
                P.append(bx("dc_post", (0.12, 0.12, 3.4), (postx, ay - 4, 1.7), (0, 0, 0), "steel"))
                postx += 45
            for zc in (3.0, 3.15, 3.3):                       # 3-conductor aerial DC trunk bus
                P.append(bx("dc_trunk", (bx1 - bx0 - 40, 0.06, 0.06),
                            ((bx0+bx1)/2, ay - 4, zc), (0, 0, 0), "dc_cable"))
    return n


def main():
    W_x, E_x = -785.0, 780.0                             # RR272/Hwy9 (W) .. RR271 (E), inside setback
    NF = (W_x, E_x, TR260_Y + 22, NORTH_CAP)             # north parcel (Township Rd 260 -> creek cap)
    SF = (W_x, E_x, SOUTH_CAP, SOUTH_TOP)                # south parcel (band S of Township Rd 260)
    ncx = (W_x + E_x) / 2.0

    # shared substation at the north edge of the south parcel (adjacent to Township
    # Rd 260 for line access; both parcels' 34.5 kV feeders collect here)
    comp = sbox(ncx - 150, SOUTH_TOP - 250, ncx + 150, SOUTH_TOP)
    pond = Point(E_x - 70, NF[2] + 80).buffer(55)

    P = []
    n_tab = fill_field(NF, 2, 2, P, pond=pond)           # 4 blocks / 28 inverters
    n_tab += fill_field(SF, 2, 1, P, comp=comp)          # 2 blocks / 14 inverters -> 42 total

    _, gantry = substation(ncx - 100, SOUTH_TOP - 235, 200, 225, P)
    # 100 kV line exits WEST along the Township Rd 260 corridor to the Hwy 9 grid tie
    transmission_line((ncx + 40, TR260_Y - 35), (-1.0, 0.0), P, spans=4, span=185)
    om_compound(ncx + 70, SOUTH_TOP - 210, P)
    gatehouse(ncx, TR260_Y + 4, P)                       # entrance off Township Rd 260
    met_mast(W_x + 55, NORTH_CAP - 55, P)
    pb = pond.bounds
    P.append(bx("pond", (pb[2]-pb[0], pb[3]-pb[1], 0.1),
                ((pb[0]+pb[2])/2, (pb[1]+pb[3])/2, 0.05), (0, 0, 0), "water"))
    # N-S spine road through both parcels (crosses the Township Rd 260 corridor)
    P.append(bx("road_spine", (7, NORTH_CAP - SOUTH_CAP, 0.08),
                (ncx, (SOUTH_CAP + NORTH_CAP)/2, 0.04), (0, 0, 0), "road"))

    fence_along(fbox(NF), P)
    fence_along(fbox(SF), P)

    out = {"anchor": [geo.ANCHOR_LAT, geo.ANCHOR_LON], "mats": MATS, "prims": P,
           "tilt_deg": round(math.degrees(TILT), 1)}
    json.dump(out, open(os.path.join(BUILD, "plan.json"), "w"))
    render((W_x - 40, SOUTH_CAP - 40, E_x + 40, NORTH_CAP + 40), comp, P,
           os.path.join(BUILD, "plan_layout.png"))
    print("PV tables: %d | total prims: %d | tilt %.0f | two parcels (N creek-capped + S band)"
          % (n_tab, len(P), math.degrees(TILT)))
    print("wrote build/plan.json and build/plan_layout.png")


def render(bounds, comp, prims, png):
    b = bounds                                          # (x0, y0, x1, y1)
    W, pad = 1000, 30
    s = (W - 2*pad) / (b[2]-b[0])
    H = int((b[3]-b[1]) * s) + 2*pad

    def px(x, y):
        return (pad + (x - b[0]) * s, H - (pad + (y - b[1]) * s))

    img = Image.new("RGB", (W, H), (40, 46, 28))
    d = ImageDraw.Draw(img, "RGBA")

    def outline(g, col, w=2):
        for gg in (g.geoms if g.geom_type.startswith("Multi") else [g]):
            if gg.geom_type == "Polygon":
                d.line([px(*c) for c in gg.exterior.coords], fill=col, width=w)

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
