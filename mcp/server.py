"""
volttron-fuxa MCP server.

A local Model Context Protocol server that lets Claude drive the dockerized
VOLTTRON + FUXA dev stack directly: read/write SCADA points, query the
historian, inspect platform health, and (later tiers) run test scenarios,
inject faults, and build devices/dashboards.

It is a thin tool layer over:
  - the fuxa-bridge gateway   (http://localhost:8080)  points/history/devices/platform
  - the FUXA REST API         (http://localhost:1881)  project/device/view automation
  - the modbus-sim control    (http://localhost:5021)  scenario / fault injection

Run standalone for testing:  python server.py            (stdio transport)
Registered with Claude Code via ../.mcp.json.

Safety scaffold (permissive by default; this is a simulator, not real hardware):
  VF_ALLOW_WRITES=1            writes enabled (set 0 for read-only mode)
  VF_WRITE_ALLOWLIST=          comma-separated point-key globs; empty = allow all
  VF_DRY_RUN=0                 1 = log writes but don't send them
"""

import fnmatch
import os
import time

import requests
from mcp.server.fastmcp import FastMCP

BRIDGE_URL = os.environ.get("VF_BRIDGE_URL", "http://localhost:8080").rstrip("/")
FUXA_URL = os.environ.get("VF_FUXA_URL", "http://localhost:1881").rstrip("/")
SIM_URL = os.environ.get("VF_SIM_URL", "http://localhost:5021").rstrip("/")

ALLOW_WRITES = os.environ.get("VF_ALLOW_WRITES", "1") not in ("0", "false", "False")
DRY_RUN = os.environ.get("VF_DRY_RUN", "0") in ("1", "true", "True")
WRITE_ALLOWLIST = [p for p in os.environ.get("VF_WRITE_ALLOWLIST", "").split(",") if p]

HTTP_TIMEOUT = 20

mcp = FastMCP("volttron-fuxa")


# --- helpers ----------------------------------------------------------------
def _get(base, path, **params):
    r = requests.get(f"{base}{path}", params=params or None, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    return r.json()


def _post(base, path, payload):
    r = requests.post(f"{base}{path}", json=payload, timeout=HTTP_TIMEOUT)
    if r.status_code >= 400:
        try:
            return r.json()
        except ValueError:
            r.raise_for_status()
    return r.json()


def _split_point(point):
    """'campus/building/device/temperature' -> ('campus/building/device', 'temperature')."""
    idx = point.rfind("/")
    if idx <= 0:
        raise ValueError(f"point key must be '<path>/<name>', got '{point}'")
    return point[:idx], point[idx + 1:]


def _write_guard(point):
    """Return None if the write is permitted, else a reason string."""
    if not ALLOW_WRITES:
        return "writes are disabled (VF_ALLOW_WRITES=0)"
    if WRITE_ALLOWLIST and not any(fnmatch.fnmatch(point, g) for g in WRITE_ALLOWLIST):
        return f"'{point}' is not in VF_WRITE_ALLOWLIST"
    return None


# --- Tier 1: ops & analysis -------------------------------------------------
@mcp.tool()
def list_points() -> dict:
    """List every VOLTTRON point the platform is currently scraping, with its
    latest value and timestamp. Keys are '<campus>/<building>/<device>/<point>'."""
    return _get(BRIDGE_URL, "/api/points")


@mcp.tool()
def read_point(point: str) -> dict:
    """Read the latest value of a single point.

    point: full point key, e.g. 'campus/building/modbus_sim/temperature'.
    """
    points = _get(BRIDGE_URL, "/api/points")
    if point not in points:
        return {"error": f"unknown point '{point}'", "known": sorted(points)}
    return {"point": point, **points[point]}


@mcp.tool()
def write_point(point: str, value: float) -> dict:
    """Write a value to a writable point (calls VOLTTRON set_point via the bridge).

    point: full point key, e.g. 'campus/building/modbus_sim/pump_speed'.
    value: numeric setpoint. For boolean points use 0 or 1.
    Subject to the write safety scaffold (allowlist / dry-run / enabled).
    """
    reason = _write_guard(point)
    if reason:
        return {"ok": False, "blocked": True, "reason": reason}
    path, name = _split_point(point)
    if DRY_RUN:
        return {"ok": True, "dry_run": True, "point": point, "value": value}
    r = requests.put(
        f"{BRIDGE_URL}/api/points",
        json={"path": path, "point": name, "value": value},
        timeout=HTTP_TIMEOUT,
    )
    r.raise_for_status()
    return r.json()


@mcp.tool()
def query_history(point: str, minutes: int = 60, limit: int = 200) -> dict:
    """Return recent historian samples for a point (newest first) for trend analysis.

    point: full point key. minutes: lookback window. limit: max samples.
    """
    return _get(BRIDGE_URL, "/api/history", point=point, minutes=minutes, limit=limit)


@mcp.tool()
def list_devices() -> dict:
    """List configured devices and their points, grouped by device path."""
    return _get(BRIDGE_URL, "/api/devices")


@mcp.tool()
def platform_status() -> dict:
    """VOLTTRON platform health: agents on the bus, point/device counts, data
    freshness, and connected websocket clients. Plus the MCP's own config."""
    status = _get(BRIDGE_URL, "/api/platform")
    status["mcp"] = {
        "bridge_url": BRIDGE_URL,
        "writes_enabled": ALLOW_WRITES,
        "dry_run": DRY_RUN,
        "write_allowlist": WRITE_ALLOWLIST or "all",
    }
    return status


# --- Tier 3: commissioning / testing harness --------------------------------
@mcp.tool()
def write_and_verify(point: str, value: float, timeout_s: int = 15, tolerance: float = 0.0) -> dict:
    """Write a point, then poll the LIVE value until it reflects the write — a
    full-chain check (FUXA/MCP -> bridge -> VOLTTRON -> Modbus -> device -> back).

    Returns ok=True with the observed value, or ok=False with reason 'timeout'.
    """
    res = write_point(point, value)
    if res.get("blocked"):
        return {"ok": False, **res}
    deadline = time.time() + timeout_s
    observed = None
    while time.time() < deadline:
        observed = read_point(point).get("value")
        if observed is not None and abs(float(observed) - float(value)) <= tolerance:
            return {"ok": True, "point": point, "target": value, "observed": observed}
        time.sleep(1)
    return {"ok": False, "point": point, "target": value, "observed": observed, "reason": "timeout"}


@mcp.tool()
def checkout_point(point: str, test_values: list[float] = None, timeout_s: int = 15) -> dict:
    """Commission a writable point: round-trip it through several test values
    (each verified full-chain), then restore the original value. Reports pass/fail."""
    if not test_values:
        test_values = [25, 50, 75]
    original = read_point(point).get("value")
    results = [write_and_verify(point, v, timeout_s) for v in test_values]
    if original is not None:
        write_point(point, original)
    passed = sum(1 for r in results if r.get("ok"))
    return {
        "point": point, "passed": passed, "total": len(results),
        "results": results, "restored_to": original,
    }


@mcp.tool()
def correlate(output_point: str, input_point: str, values: list[float], settle_s: int = 6) -> dict:
    """Ramp an output point across `values`; after each settle, record the input
    point. Returns the (output, input) pairs so you can judge whether the input
    responds. Useful for checking control linkages (e.g. pump speed -> flow)."""
    pairs = []
    for v in values:
        write_point(output_point, v)
        time.sleep(settle_s)
        pairs.append({"output": v, "input": read_point(input_point).get("value")})
    return {"output_point": output_point, "input_point": input_point, "pairs": pairs}


# --- Tier 4: scenario / fault injection (heating-substation digital twin) ----
@mcp.tool()
def sim_state() -> dict:
    """Full simulator state: live measurements, operator controls (setpoints/
    commands), alarm bits, active faults, and the available named scenarios."""
    return _get(SIM_URL, "/api/sim/state")


@mcp.tool()
def inject_fault(component: str, on: bool = True) -> dict:
    """Inject (or clear) a plant fault; it cascades through VOLTTRON -> bridge ->
    FUXA so the dashboard reacts (pump turns red, alarm fires, flow/heat drop).

    component: 'circ_pump1' | 'circ_pump2' | 'makeup_pump' | 'primary'
               (primary = loss of the city heat main).
    on: True to fault, False to clear.
    """
    return _post(SIM_URL, "/api/sim/fault", {"name": component, "on": on})


@mcp.tool()
def set_control(name: str, value: float) -> dict:
    """Set an operator control / setpoint on the plant (also writable from the
    FUXA buttons). Names: circ_pump1_cmd, circ_pump1_hz_sp, circ_pump2_cmd,
    circ_pump2_hz_sp, makeup_pump_cmd, supply_setpoint, building_load."""
    return _post(SIM_URL, "/api/sim/point", {"name": name, "value": value})


@mcp.tool()
def load_scenario(name: str) -> dict:
    """Load a named plant scenario (clears prior faults first). Options:
    'normal', 'morning_startup', 'peak_load', 'circ_pump_trip',
    'makeup_vfd_fault', 'loss_of_primary'."""
    return _post(SIM_URL, "/api/sim/scenario", {"name": name})


@mcp.tool()
def reset_sim() -> dict:
    """Reset the plant to the 'normal' operating scenario (clears all faults)."""
    return _post(SIM_URL, "/api/sim/reset", {})


if __name__ == "__main__":
    mcp.run()
