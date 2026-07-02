#!/usr/bin/env python
"""Fetch ESRI World Imagery for the site and stitch it into a ground image, with
its extent in the model's local metric frame -- so the plant can be reviewed on
real aerial imagery inside Blender (a Google-Earth-like check, no GE needed).

Writes build/satellite.png + build/satellite.json {xL,xR,yB,yT} (metres, X=east
Y=north, anchor at origin).
"""
import io
import json
import math
import os
import urllib.request

from PIL import Image
import geo

Z = 16                     # ~1.5 m/px at this latitude
HALF = 1350.0              # metres each side of the anchor
BUILD = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "build")
URL = "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/%d/%d/%d"


def deg2tile(lat, lon, z):
    n = 2 ** z
    return ((lon + 180) / 360 * n,
            (1 - math.asinh(math.tan(math.radians(lat))) / math.pi) / 2 * n)


def tile2deg(xt, yt, z):
    n = 2 ** z
    return (math.degrees(math.atan(math.sinh(math.pi * (1 - 2 * yt / n)))),
            xt / n * 360 - 180)


def main():
    dlat = HALF / geo.M_PER_DEG_LAT
    dlon = HALF / geo.M_PER_DEG_LON
    x0, y0 = deg2tile(geo.ANCHOR_LAT + dlat, geo.ANCHOR_LON - dlon, Z)   # top-left
    x1, y1 = deg2tile(geo.ANCHOR_LAT - dlat, geo.ANCHOR_LON + dlon, Z)   # bottom-right
    xt0, yt0 = int(math.floor(x0)), int(math.floor(y0))
    xt1, yt1 = int(math.floor(x1)), int(math.floor(y1))

    W = (xt1 - xt0 + 1) * 256
    H = (yt1 - yt0 + 1) * 256
    img = Image.new("RGB", (W, H))
    n = 0
    for tx in range(xt0, xt1 + 1):
        for ty in range(yt0, yt1 + 1):
            req = urllib.request.Request(URL % (Z, ty, tx),
                                         headers={"User-Agent": "sunfield-site-model/1.0"})
            data = urllib.request.urlopen(req, timeout=30).read()
            img.paste(Image.open(io.BytesIO(data)).convert("RGB"),
                      ((tx - xt0) * 256, (ty - yt0) * 256))
            n += 1
    img.save(os.path.join(BUILD, "satellite.png"))

    lat_top, lon_left = tile2deg(xt0, yt0, Z)
    lat_bot, lon_right = tile2deg(xt1 + 1, yt1 + 1, Z)
    ext = {"xL": geo.to_local(geo.ANCHOR_LAT, lon_left)[0],
           "xR": geo.to_local(geo.ANCHOR_LAT, lon_right)[0],
           "yB": geo.to_local(lat_bot, geo.ANCHOR_LON)[1],
           "yT": geo.to_local(lat_top, geo.ANCHOR_LON)[1]}
    json.dump(ext, open(os.path.join(BUILD, "satellite.json"), "w"))
    print("stitched %d tiles -> %dx%d px | extent m: X[%.0f,%.0f] Y[%.0f,%.0f]"
          % (n, W, H, ext["xL"], ext["xR"], ext["yB"], ext["yT"]))


if __name__ == "__main__":
    main()
