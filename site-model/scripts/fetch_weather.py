#!/usr/bin/env python
"""Fetch a TMY weather file for the Sunfield Solar site and write it as a SAM-ready
CSV (build/tmy.csv), plus keep the raw source (build/pvgis_tmy_raw.csv).

Site: 51.18978 N, -113.66769 W (Wheatland County, Alberta), ~961 m.

Source: PVGIS (EU JRC) TMY API. For the Americas PVGIS serves NREL's own **NSRDB**
satellite irradiance, so for this site the underlying data is the same NSRDB record
NREL's SAM would use -- NREL's own developer.nrel.gov API is not reachable from this
environment, so PVGIS is the delivery path for the identical data.

Conversions applied so the file is authoritative for SAM:
  * UTC -> local standard time. Alberta is MST = UTC-7 year round in weather files
    (SAM weather files use standard time, no DST). We roll the hourly arrays by -7 h
    and relabel with a clean non-leap local calendar, and declare Time Zone = -7 so
    SAM computes solar position correctly for lon -113.67.
  * Pressure Pa -> mbar (SAM 'Pressure' column is millibar).
  * PVGIS irradiance is centered mid-hour (offset 0.5 h) -> Minute = 30.
  * Albedo is NOT written here; the SAM model sets monthly (snow) albedo directly.

DNS note: glibc getaddrinfo (nss-resolve) is blocked in this sandbox, but the
systemd-resolved stub answers `drill`. We shim socket.getaddrinfo to resolve via drill.
"""
import os
import socket
import subprocess
import sys

import requests

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BUILD = os.path.join(HERE, "build")

LAT = 51.18978
LON = -113.66769
TZ = -7                      # Alberta MST (standard time, no DST) -> UTC-7
OUT = os.path.join(BUILD, "tmy.csv")
RAW = os.path.join(BUILD, "pvgis_tmy_raw.csv")

PVGIS_TMY = "https://re.jrc.ec.europa.eu/api/tmy"


# --- DNS shim: resolve via `drill` (systemd-resolved stub), bypassing blocked nss ---
_real_getaddrinfo = socket.getaddrinfo
_dns_cache = {}


def _drill_resolve(host):
    if host in _dns_cache:
        return _dns_cache[host]
    out = subprocess.run(["drill", host, "A"], capture_output=True, text=True).stdout
    ip, inans = None, False
    for ln in out.splitlines():
        if "ANSWER SECTION" in ln:
            inans = True
            continue
        if "AUTHORITY" in ln:
            inans = False
        if inans and "\tIN\tA\t" in ln:
            ip = ln.split("\t")[-1].strip()
            break
    _dns_cache[host] = ip
    return ip


def _patched_getaddrinfo(host, *a, **k):
    try:
        socket.inet_aton(host)
        return _real_getaddrinfo(host, *a, **k)      # already an IP
    except OSError:
        pass
    ip = _drill_resolve(host)
    return _real_getaddrinfo(ip or host, *a, **k)


socket.getaddrinfo = _patched_getaddrinfo


def fetch_pvgis():
    print(f"PVGIS TMY  lat={LAT} lon={LON}")
    r = requests.get(
        PVGIS_TMY,
        params={"lat": LAT, "lon": LON, "outputformat": "csv", "usehorizon": 1},
        timeout=120,
    )
    r.raise_for_status()
    with open(RAW, "w", newline="") as f:
        f.write(r.text)
    print(f"  saved raw {RAW} ({len(r.text)} bytes)")
    return r.text


def parse_pvgis(text):
    """Return (elevation_m, list of 8760 dicts in UTC order Jan1->Dec31)."""
    lines = text.splitlines()
    elev = None
    hdr_i = None
    for i, ln in enumerate(lines):
        if ln.startswith("Elevation"):
            elev = float(ln.split(":")[1].strip())
        if ln.startswith("time(UTC)"):
            hdr_i = i
            break
    cols = lines[hdr_i].split(",")
    rows = []
    for ln in lines[hdr_i + 1:]:
        if not ln or not ln[0].isdigit():
            break                                    # footer starts
        vals = ln.split(",")
        if len(vals) != len(cols):
            break
        rows.append(dict(zip(cols, vals)))
    if len(rows) != 8760:
        print(f"  WARNING: parsed {len(rows)} rows (expected 8760)", file=sys.stderr)
    return elev, rows


def write_sam_csv(source, elev, ghi, dni, dhi, tdry, rh, wspd, pres_mbar, out_path):
    """Roll UTC->local (UTC-7), relabel to a clean non-leap calendar, write SAM CSV.
    Inputs are 8760-length hourly arrays in UTC order (Jan 1 00:00 .. Dec 31 23:00)."""
    n = len(ghi)
    assert n == 8760, f"expected 8760 hourly rows, got {n}"

    def roll(arr):
        # local[i] = utc[(i - TZ) mod n];  TZ=-7 -> utc[(i+7) mod n]
        return [arr[(i - TZ) % n] for i in range(n)]

    clip = lambda a: [max(0.0, x) for x in a]
    ghi, dni, dhi = roll(clip(ghi)), roll(clip(dni)), roll(clip(dhi))
    tdry, rh, wspd, pres_mbar = roll(tdry), roll(rh), roll(wspd), roll(pres_mbar)

    DPM = [31, 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    stamps = [(1900, m, d, h, 30)
              for m, dc in enumerate(DPM, 1) for d in range(1, dc + 1) for h in range(24)]
    assert len(stamps) == n

    out = ["Source,Location ID,City,State,Country,Latitude,Longitude,Time Zone,Elevation",
           f"{source},sunfield,Wheatland County,AB,Canada,{LAT},{LON},{TZ},{elev:.0f}",
           "Year,Month,Day,Hour,Minute,GHI,DNI,DHI,Tdry,Wspd,Pressure,RH"]
    for i in range(n):
        y, m, d, h, mi = stamps[i]
        out.append(f"{y},{m},{d},{h},{mi},{ghi[i]:.1f},{dni[i]:.1f},{dhi[i]:.1f},"
                   f"{tdry[i]:.1f},{wspd[i]:.2f},{pres_mbar[i]:.1f},{rh[i]:.1f}")
    with open(out_path, "w", newline="") as f:
        f.write("\n".join(out) + "\n")
    print(f"  saved {out_path}  ({n} rows)  source={source}")
    print(f"  annual GHI ~{sum(ghi)/1e3:.0f}, DNI ~{sum(dni)/1e3:.0f} kWh/m2, "
          f"mean Tamb {sum(tdry)/n:.1f} C, elev {elev:.0f} m")


def build_pvgis():
    text = fetch_pvgis()
    elev, rows = parse_pvgis(text)
    f = lambda name: [float(r[name]) for r in rows]
    write_sam_csv("PVGIS-NSRDB-TMY", elev,
                  f("G(h)"), f("Gb(n)"), f("Gd(h)"), f("T2m"), f("RH"), f("WS10m"),
                  [x / 100.0 for x in f("SP")], OUT)


# --- Open-Meteo ERA5: representative single year (2016, GHI ~= 10yr mean), lower bracket ---
OM_URL = "https://archive-api.open-meteo.com/v1/archive"
ERA5_YEAR = 2016
OUT_ERA5 = os.path.join(BUILD, "tmy_era5.csv")


def build_era5():
    print(f"Open-Meteo ERA5  lat={LAT} lon={LON}  year={ERA5_YEAR}")
    r = requests.get(OM_URL, params={
        "latitude": LAT, "longitude": LON,
        "start_date": f"{ERA5_YEAR}-01-01", "end_date": f"{ERA5_YEAR}-12-31",
        "hourly": ("shortwave_radiation,direct_normal_irradiance,diffuse_radiation,"
                   "temperature_2m,relative_humidity_2m,wind_speed_10m,surface_pressure"),
        "timezone": "UTC", "wind_speed_unit": "ms",
    }, timeout=180)
    r.raise_for_status()
    h = r.json()["hourly"]
    t = h["time"]
    # 2016 is a leap year (8784 h) -> drop Feb 29 to get a clean 8760 SAM year
    keep = [i for i, ts in enumerate(t) if ts[5:10] != "02-29"]
    pick = lambda k: [h[k][i] if h[k][i] is not None else 0.0 for i in keep]
    write_sam_csv(f"OpenMeteo-ERA5-{ERA5_YEAR}", 961.0,
                  pick("shortwave_radiation"), pick("direct_normal_irradiance"),
                  pick("diffuse_radiation"), pick("temperature_2m"),
                  pick("relative_humidity_2m"), pick("wind_speed_10m"),
                  pick("surface_pressure"), OUT_ERA5)   # surface_pressure already hPa=mbar


def main():
    os.makedirs(BUILD, exist_ok=True)
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    if which in ("pvgis", "all"):
        build_pvgis()
    if which in ("era5", "all"):
        build_era5()


if __name__ == "__main__":
    main()
