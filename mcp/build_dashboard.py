"""
Generate a complete FUXA dashboard for the heating-substation digital twin and
push it into FUXA via POST /api/project.

It builds, programmatically:
  - a Volttron device (reads the bridge) with a tag per heat-station point,
  - a dark-themed process schematic VIEW: heat exchanger, primary/secondary
    pipe loops, animated pump symbols (green run / red fault / grey off),
    live readouts, a makeup tank with a level bar, an alarm panel, a realtime
    trend, and operator control buttons,
all bound to the device tags. Widget templates were learned from FUXA's own
demo project (svg-ext-value / motor / gauge_semaphore / gauge_progress /
html_button / html_chart). Binding is variableId = "<deviceId>^~^<tagId>".

Usage: python build_dashboard.py            (pushes to http://localhost:1881)
"""

import json
import os

import requests

FUXA = os.environ.get("VF_FUXA_URL", "http://localhost:1881").rstrip("/")
BRIDGE = os.environ.get("VF_BRIDGE_URL_INTERNAL", "http://volttron:8080")
DEVICE_ID = "heat_station"
DEVICE_NAME = "HeatStation"
DEVPATH = "campus/building/heat_station"
CHART_ID = "chart_substation"
CHART2_ID = "chart_pressure"
SEP = "^~^"

# point -> (units, modbus-writable?) ; order groups inputs, controls, alarms
POINTS = {
    "primary_supply_temp": "C", "primary_return_temp": "C",
    "secondary_supply_temp": "C", "secondary_return_temp": "C",
    "secondary_flow": "m3/h", "instant_heat": "GJ/h",
    "secondary_supply_pressure": "kPa", "secondary_return_pressure": "kPa",
    "makeup_tank_level": "%", "circ_pump1_hz": "Hz", "circ_pump2_hz": "Hz",
    "makeup_pump_hz": "Hz", "circ_pump1_status": "", "circ_pump2_status": "",
    "makeup_pump_status": "", "circ_pump1_cmd": "", "circ_pump1_hz_sp": "Hz",
    "circ_pump2_cmd": "", "circ_pump2_hz_sp": "Hz", "makeup_pump_cmd": "",
    "supply_setpoint": "C", "building_load": "%", "circ_pump1_fault": "",
    "circ_pump2_fault": "", "makeup_vfd_fault": "", "low_pressure_alarm": "",
    "high_supply_temp_alarm": "",
}

TID = lambda p: "t_hs_" + p                     # tag id
# Gauges bind by the plain tag id: FUXA stores live values in variables[tag.id]
# and value/motor/semaphore getSignals() return property.variableId as-is.
VID = lambda p: TID(p)


# --- id counter -------------------------------------------------------------
_n = [0]
def uid(pfx):
    _n[0] += 1
    return f"{pfx}_{_n[0]:03d}"


# --- widget builders: return (svg_string, item_dict_or_None) ----------------
svg_parts = []
items = {}


def add(svg, item=None):
    svg_parts.append(svg)
    if item:
        items[item["id"]] = item


def _prop(p, extra=None):
    d = {"variableId": VID(p), "variableSrc": DEVICE_ID, "variable": p,
         "alarmId": "", "alarmSrc": "", "alarm": "", "alarmColor": "", "events": []}
    if extra:
        d.update(extra)
    return d


def value(x, y, p, units="", fs=16, color="#eaf6ff", anchor="middle"):
    gid = uid("VAL")
    add(f'<g id="{gid}" type="svg-ext-value" fill="{color}" stroke="{color}" '
        f'font-size="{fs}" stroke-width="0" font-family="sans-serif" text-anchor="{anchor}">'
        f'<text id="{gid}_t" fill="{color}" stroke="{color}" font-size="{fs}" '
        f'font-family="sans-serif" text-anchor="{anchor}" stroke-width="0" '
        f'xml:space="preserve" x="{x}" y="{y}">##.##</text></g>',
        {"id": gid, "type": "svg-ext-value", "name": p,
         "property": _prop(p, {"ranges": [{"type": "unit", "min": 0, "max": 99999,
                                           "text": (" " + units) if units else ""}]}),
         "label": "Value"})


def motor(cx, cy, p, r=22):
    # proc-eng shape (svg-ext-proceng): FUXA fills every child node with the
    # range color -> the disc shows pump status (green/red/grey). A separate
    # static white vane drawn ON TOP (outside the group) keeps the pump look.
    gid = uid("MTR")
    add(f'<g type="svg-ext-proceng" id="{gid}" fill="#5b6b78" font-size="14" '
        f'font-family="sans-serif" text-anchor="middle" stroke="#0a1a24">'
        f'<ellipse cx="{cx}" cy="{cy}" rx="{r}" ry="{r}" stroke="#0a1a24" '
        f'stroke-width="2" id="{gid}_e"/></g>',
        {"id": gid, "type": "svg-ext-proceng", "name": p,
         "property": _prop(p, {"ranges": [
             {"type": "range", "min": "1", "max": "1", "color": "#27c06a"},
             {"type": "range", "min": "2", "max": "2", "color": "#e74c3c"},
             {"type": "range", "min": "0", "max": "0", "color": "#5b6b78"}]}),
         "label": "Motor"})
    # static vane (impeller) on top, not recolored
    add(f'<path d="M{cx-r+6},{cy}L{cx+r-7},{cy-r+8}L{cx+r-7},{cy+r-8}z" '
        f'fill="#f4f8fc" stroke="none" opacity="0.92"/>')


def semaphore(cx, cy, p, r=11):
    gid = uid("GSE")
    add(f'<g type="svg-ext-gauge_semaphore" fill="#000000" font-size="14" '
        f'stroke="#000000" font-family="sans-serif" id="{gid}">'
        f'<ellipse cx="{cx}" cy="{cy}" rx="{r}" ry="{r}" fill="#2ecc71" '
        f'stroke="#0a1a24" id="{gid}_e"/></g>',
        {"id": gid, "type": "svg-ext-gauge_semaphore", "name": p,
         "property": _prop(p, {"ranges": [
             {"type": "range", "min": "1", "max": "1", "color": "#e74c3c"},
             {"type": "range", "min": "0", "max": "0", "color": "#2ecc71"}]}),
         "label": "HtmlSemaphore"})


def progress(x, y, w, h, p, mn, mx):
    # child ids MUST start with A-/B-/H- (gauge-progress finds the background,
    # fill and label rects by that prefix; otherwise processValue throws on null
    # and breaks the whole signal pipeline).
    gid = uid("GXP")
    add(f'<g font-family="sans-serif" font-size="14" type="svg-ext-gauge_progress" '
        f'id="{gid}" stroke="null">'
        f'<rect fill="#16303f" height="{h}" width="{w}" y="{y}" x="{x}" id="A-{gid}" stroke="null"/>'
        f'<rect fill="#3aa0ff" height="{h}" width="{w}" y="{y}" x="{x}" id="B-{gid}" stroke="null"/>'
        f'<foreignObject font-size="14" id="H-{gid}" width="{w}" height="{h}" y="{y}" x="{x}"/></g>',
        {"id": gid, "type": "svg-ext-gauge_progress", "name": p,
         "property": _prop(p, {"ranges": [{"type": "minmax", "min": mn, "max": mx,
                                           "style": [True, True], "color": "#3aa0ff"}]}),
         "label": "HtmlProgress"})


def button(x, y, w, h, label, p, val, color="#2d6cdf"):
    gid = uid("HXB")
    add(f'<g id="{gid}" type="svg-ext-html_button" fill="#FFFFFF" font-size="14" '
        f'font-family="sans-serif" text-anchor="right" stroke="#000000">'
        f'<rect stroke-width="0" x="{x}" y="{y}" width="{w}" height="{h}" fill="{color}" id="{gid}_r" stroke="#ffffff"/>'
        f'<foreignObject x="{x}" y="{y}" height="{h}" width="{w}" id="{gid}_h">'
        f'<BUTTON style="width:100%;height:100%;vector-effect:non-scaling-stroke;'
        f'background-color:{color};color:#fff;border:none;border-radius:4px;font-size:12px;" '
        f'class="md-btn md-btn-raised" id="{gid}_b">{label}</BUTTON></foreignObject></g>',
        {"id": gid, "type": "svg-ext-html_button", "name": label,
         "property": {"events": [{"type": "click", "action": "onSetValue", "actparam": str(val)}],
                      "variableId": VID(p), "variableSrc": DEVICE_ID, "variable": p,
                      "alarmId": "", "alarmSrc": "", "alarm": "", "alarmColor": ""},
         "label": "HtmlButton"})


def chart(x, y, w, h, chart_id, y1=None, y2=None):
    # initElement finds the chart host DIV via the 'D-' prefix; the foreignObject
    # uses 'H-'. Without these the ChartUplot component is never created.
    gid = uid("HXC")
    # FUXA reads scaleY1/Y2 min/max to PIN each y-axis range. Pinning both:
    #  (a) gives defined, labelled left (Y1) and right (Y2) scales, and
    #  (b) stops uPlot's auto-range from collapsing on flat data (which was
    #      pruning multi-line series). legend.live=false -> static legend.
    # y1/y2 are (min, max) tuples.
    opts = {"legend": {"live": False}}
    if y1:
        opts["scaleY1min"], opts["scaleY1max"] = y1
    if y2:
        opts["scaleY2min"], opts["scaleY2max"] = y2
    add(f'<g id="{gid}" type="svg-ext-html_chart" fill="#ffffff" font-size="14" '
        f'font-family="sans-serif" text-anchor="right" stroke="#000000">'
        f'<rect stroke-width="0" x="{x}" y="{y}" width="{w}" height="{h}" id="{gid}_r" fill="#f4f7fb" stroke="null"/>'
        f'<foreignObject x="{x}" y="{y}" height="{h}" width="{w}" id="H-{gid}">'
        f'<DIV style="width:100%;height:100%;vector-effect:non-scaling-stroke;'
        f'background-color:#f4f7fb;border-radius:4px;" id="D-{gid}"></DIV>'
        f'</foreignObject></g>',
        {"id": gid, "type": "svg-ext-html_chart", "name": "Trend",
         "property": {"id": chart_id, "type": "realtime1", "options": opts},
         "label": "HtmlChart"})


def kpi(x, y, w, label, p, units, color):
    """Big-number KPI tile."""
    box(x, y, w, 78, "#0e2030", "#21506e", 1.5, 8)
    text(x + w / 2, y + 22, label, 11, "#8fb8d6", "middle", "bold")
    value(x + w / 2, y + 56, p, units, 28, color, "middle")


# --- static decoration (no binding) -----------------------------------------
def text(x, y, s, fs=13, color="#8fb8d6", anchor="start", weight="normal"):
    add(f'<text x="{x}" y="{y}" font-size="{fs}" font-family="sans-serif" fill="{color}" '
        f'text-anchor="{anchor}" font-weight="{weight}" stroke-width="0" xml:space="preserve">{s}</text>')


def box(x, y, w, h, fill="#0f2433", stroke="#1f4257", sw=1.5, rx=6):
    add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}" stroke="{stroke}" '
        f'stroke-width="{sw}" rx="{rx}"/>')


def pipe(x1, y1, x2, y2, color="#2b7fb8", sw=6):
    add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" '
        f'stroke-width="{sw}" stroke-linecap="round"/>')


# ===========================================================================
# LAYOUT  (canvas 1280 x 800, dark theme)
# ===========================================================================
add('<rect x="0" y="0" width="1280" height="800" fill="#0a1622"/>')
text(640, 34, "HEAT EXCHANGE STATION — MONITORING", 22, "#5fd0ff", "middle", "bold")
add('<line x1="40" y1="48" x2="1240" y2="48" stroke="#1f4257" stroke-width="1"/>')

HOT, COLD, PIPE = "#e0533a", "#2b7fb8", "#2b7fb8"

# ---- Primary loop (city heat main) ----
text(70, 90, "CITY HEAT MAIN (PRIMARY)", 13, "#9fd0ff", "start", "bold")
box(60, 100, 250, 90)
text(80, 128, "Supply", 12, "#9aa")
value(210, 132, "primary_supply_temp", "°C", 18, "#ff8a6a", "end")
text(80, 165, "Return", 12, "#9aa")
value(210, 168, "primary_return_temp", "°C", 18, "#7fc4ff", "end")
pipe(310, 130, 470, 130, HOT)            # primary supply -> HX
pipe(470, 175, 310, 175, COLD)           # HX -> primary return

# ---- Heat exchanger ----
box(470, 95, 150, 110, "#13354a", "#3a86b8", 2, 6)
text(545, 150, "HEAT", 16, "#cfe9ff", "middle", "bold")
text(545, 172, "EXCHANGER", 13, "#9fd0ff", "middle")
pipe(545, 205, 545, 300, HOT)            # HX -> secondary supply header

# ---- Secondary supply header + circulation pumps ----
text(70, 300, "BUILDING LOOP (SECONDARY)", 13, "#9fd0ff", "start", "bold")
pipe(180, 330, 1000, 330, HOT)           # supply header
pipe(180, 560, 1000, 560, COLD)          # return header
pipe(545, 300, 545, 330, HOT)
# pump 1
motor(360, 330, "circ_pump1_status", 22)
text(360, 300, "CIRC PUMP 1", 11, "#9fd0ff", "middle")
value(360, 372, "circ_pump1_hz", "Hz", 13, "#cfe9ff", "middle")
# pump 2
motor(740, 330, "circ_pump2_status", 22)
text(740, 300, "CIRC PUMP 2", 11, "#9fd0ff", "middle")
value(740, 372, "circ_pump2_hz", "Hz", 13, "#cfe9ff", "middle")

# ---- Building block ----
box(1000, 300, 150, 290, "#10283a", "#2f6f9c", 2, 6)
text(1075, 420, "BUILDING", 15, "#cfe9ff", "middle", "bold")
text(1075, 445, "LOAD", 13, "#9fd0ff", "middle")
value(1075, 478, "building_load", "%", 18, "#ffd479", "middle")
pipe(1000, 330, 1075, 330, HOT)
pipe(1075, 330, 1075, 300, HOT)
pipe(1075, 560, 1000, 560, COLD)

# ---- Makeup water system (centre, between the loops) ----
text(440, 398, "MAKEUP WATER", 12, "#9fd0ff", "start", "bold")
box(440, 408, 95, 112, "#0e2233", "#2f6f9c")
progress(450, 418, 75, 92, "makeup_tank_level", 0, 100)
text(487, 536, "Tank Level", 11, "#9aa", "middle")
motor(645, 462, "makeup_pump_status", 22)
text(645, 434, "MAKEUP PUMP", 11, "#9fd0ff", "middle")
value(645, 506, "makeup_pump_hz", "Hz", 13, "#cfe9ff", "middle")
pipe(535, 462, 625, 462, COLD)

# ---- Secondary readouts panel (left) ----
box(60, 360, 250, 210)
text(80, 388, "SECONDARY", 13, "#9fd0ff", "start", "bold")
labels = [("Supply Temp", "secondary_supply_temp", "°C", "#ff8a6a"),
          ("Return Temp", "secondary_return_temp", "°C", "#7fc4ff"),
          ("Flow", "secondary_flow", "m³/h", "#7CE0A0"),
          ("Heat Output", "instant_heat", "GJ/h", "#ffd479"),
          ("Supply Press", "secondary_supply_pressure", "kPa", "#cfe9ff"),
          ("Return Press", "secondary_return_pressure", "kPa", "#cfe9ff")]
for i, (lab, p, u, c) in enumerate(labels):
    yy = 416 + i * 26
    text(80, yy, lab, 12, "#9aa")
    value(290, yy, p, u, 15, c, "end")

# ---- Real-time trend (bottom band, tall enough for a proper plot + y-axis) ----
text(56, 594, "REAL-TIME TREND · Temperature (left axis) & Flow (right axis)", 12, "#9fd0ff", "start", "bold")
chart(50, 600, 600, 188, CHART_ID, y1=(40, 90), y2=(0, 140))

# ---- Operator control panel (bottom) ----
PX = 700
box(PX, 600, 480, 160)
text(PX + 20, 626, "OPERATOR CONTROLS", 13, "#9fd0ff", "start", "bold")
# pump 1
text(PX + 20, 656, "Circ Pump 1", 12, "#cfe9ff")
button(PX + 110, 644, 56, 22, "START", "circ_pump1_cmd", 1, "#2e9e5b")
button(PX + 172, 644, 56, 22, "STOP", "circ_pump1_cmd", 0, "#c0392b")
# pump 2
text(PX + 20, 686, "Circ Pump 2", 12, "#cfe9ff")
button(PX + 110, 674, 56, 22, "START", "circ_pump2_cmd", 1, "#2e9e5b")
button(PX + 172, 674, 56, 22, "STOP", "circ_pump2_cmd", 0, "#c0392b")
# setpoint
text(PX + 20, 716, "Supply SP", 12, "#cfe9ff")
button(PX + 110, 704, 36, 22, "68", "supply_setpoint", 68)
button(PX + 152, 704, 36, 22, "72", "supply_setpoint", 72)
button(PX + 194, 704, 36, 22, "76", "supply_setpoint", 76)
# load
text(PX + 20, 746, "Bldg Load", 12, "#cfe9ff")
button(PX + 110, 734, 36, 22, "30", "building_load", 30)
button(PX + 152, 734, 36, 22, "60", "building_load", 60)
button(PX + 194, 734, 36, 22, "90", "building_load", 90)
value(PX + 300, 660, "supply_setpoint", "°C set", 16, "#ffd479", "start")

# ---- Alarm panel (top right) ----
box(960, 70, 280, 210)
text(980, 96, "ALARMS", 14, "#ff6b6b", "start", "bold")
alarms = [("Circ Pump 1 VFD Fault", "circ_pump1_fault"),
          ("Circ Pump 2 VFD Fault", "circ_pump2_fault"),
          ("Makeup VFD Fault", "makeup_vfd_fault"),
          ("System Low Pressure", "low_pressure_alarm"),
          ("Supply Over-Temp", "high_supply_temp_alarm")]
for i, (lab, p) in enumerate(alarms):
    yy = 126 + i * 30
    semaphore(990, yy - 4, p, 9)
    text(1012, yy, lab, 12, "#cfe9ff")


# ===========================================================================
# ASSEMBLE + PUSH
# ===========================================================================
def build_device():
    tags = {}
    for p in POINTS:
        # DAQ off: charts plot pure realtime (browser-clock) points. With DAQ on,
        # the realtime chart also pulls ~8h of historian data (stored UTC) and the
        # mismatched timestamps make the x-axis look wrong.
        tags[TID(p)] = {"id": TID(p), "name": p, "type": "Real",
                        "address": f"{DEVPATH}/{p}",
                        "daq": {"enabled": False, "interval": 60, "changed": False, "restored": False}}
    return {"id": DEVICE_ID, "name": DEVICE_NAME, "enabled": True, "type": "Volttron",
            "property": {"address": BRIDGE, "port": None, "slot": None, "rack": None,
                         "baudrate": 9600, "databits": 8, "stopbits": 1, "parity": "None",
                         "delay": 10, "forceFC16": False},
            "polling": 1000, "tags": tags}


def main():
    # uPlot's live legend shows an idle "--" value and an epoch-0 (1969) time row
    # when the cursor isn't hovering, and FUXA doesn't pass legend options through.
    # Hide the time row + value cells via CSS, leaving just the colored series
    # labels. (Applies document-wide to the HTML legend inside the chart.)
    style = ('<style>.u-legend .u-series:first-child{display:none !important;}'
             '.u-legend .u-value{display:none !important;}'
             '.u-legend{color:#1b2a36;font-size:11px;}</style>')
    svg = ('<svg width="1280" height="800" xmlns="http://www.w3.org/2000/svg" '
           'xmlns:svg="http://www.w3.org/2000/svg" xmlns:html="http://www.w3.org/1999/xhtml">'
           + style + "".join(svg_parts) + "</svg>")
    view = {"id": "v_heat_station", "name": "MainView", "type": "svg",
            "profile": {"width": 1280, "height": 800, "bkcolor": "#0a1622ff"},
            "items": items, "variables": {}, "svgcontent": svg,
            "property": {"events": []}}
    def line(point, label, color, yaxis=1):
        return {"id": TID(point), "name": point, "label": label, "yaxis": yaxis,
                "device": DEVICE_NAME, "color": color, "lineWidth": 2}
    # Dual-axis: supply/return temperature on Y1 (left), flow on Y2 (right).
    # Both axes pinned (see chart()) so all three lines hold even when flat.
    charts = [
        {"id": CHART_ID, "name": "Temperature (°C, left)  ·  Flow (m³/h, right)",
         "type": "realtime1", "lines": [
            line("secondary_supply_temp", "Supply °C", "#ff6b6b", 1),
            line("secondary_return_temp", "Return °C", "#4dabf7", 1),
            line("secondary_flow", "Flow m³/h", "#51cf66", 2)]},
    ]

    prj = requests.get(f"{FUXA}/api/project", timeout=20).json()
    prj = prj.get("data") or prj
    prj["devices"] = {DEVICE_ID: build_device()}
    prj["hmi"]["views"] = [view]
    if prj.get("hmi", {}).get("layout"):
        prj["hmi"]["layout"]["start"] = view["id"]
    prj["charts"] = charts

    r = requests.post(f"{FUXA}/api/project", json=prj, timeout=30)
    print("POST /api/project ->", r.status_code, r.text[:200])
    print(f"items={len(items)} tags={len(POINTS)}")


if __name__ == "__main__":
    main()
