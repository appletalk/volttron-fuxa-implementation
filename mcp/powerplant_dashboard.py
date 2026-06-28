"""
Generate the FUXA dashboard for "Sunfield Solar" — a 100 MWac utility-scale PV +
DC-coupled BESS plant — and merge it into the live project ALONGSIDE the heating
substation (POST /api/project is a full replace, so we keep both devices + all
views).

Three power-plant views, built from the spec in docs/POWERPLANT_SPEC.md section 5:
  v_power_plant  Overview & Day-in-the-Life : html_bag KPI dials, an animated
                 single-line, signature MW-vs-irradiance + BESS-firming trends,
                 annunciators, operator presets + nav.
  v_pp_oneline   Electrical One-Line & POI metering : full single-line, big
                 MW/MVAR/kV/PF/current readouts, a PQ-envelope trend, controls.
  v_pp_bess      BESS detail : SOC donut + bar, signed-power dial, dispatch
                 controls, SOC-vs-power trend.

Widgets (feasibility proven against the FUXA source / verify-one-first gates):
  - svg-ext-html_bag  radial/zones/donut gauges (NgxGauge; runtime-injected into a
    D-BAG_ div; min/max/type/staticZones in property.options; bind variableId)
  - hand-authored svg-ext-proceng electrical symbols recolored by a 0/1/2 status
  - SMIL-animated <line>s for energized "flow" (robust; no fragile pipe ext)
  - svg-ext-value / gauge_semaphore / gauge_progress / html_button / html_chart
    (all proven on the substation) ; html_button events: onSetValue + onpage nav

Run:  ./.venv/bin/python powerplant_dashboard.py        (refreshes substation too)
"""

import os
import subprocess
import sys

import requests

HERE = os.path.dirname(os.path.abspath(__file__))
FUXA = os.environ.get("VF_FUXA_URL", "http://localhost:1881").rstrip("/")
BRIDGE = os.environ.get("VF_BRIDGE_URL_INTERNAL", "http://volttron:8080")

DEV = "power_plant"          # device id
DEVNAME = "SolarPlant"       # device NAME (used by chart lines)
DEVPATH = "campus/solar/power_plant"

USE_BAG = True               # html_bag radial dials (verify-one-first; fallback if broken)

# theme
BG = "#0a1622"
PANEL, EDGE = "#0f2433", "#1f4257"
INK, SUB = "#eaf6ff", "#9fb8d6"
GOLD, GREEN, BLUE, RED, AMBER = "#ffd479", "#27c06a", "#4dabf7", "#e74c3c", "#f0a23e"
DCCOL, ACCOL = "#5fa8ff", "#f0a23e"   # DC blue, AC amber

# every power-plant point (for the device tags)
POINTS = [
    "plant_active_power_mw", "plant_reactive_power_mvar", "poi_voltage_kv",
    "grid_frequency_hz", "power_factor", "poi_current_a", "main_breaker_status",
    "irradiance_wm2", "pv_dc_power_mw", "inverter_ac_power_mw", "clipping_loss_mw",
    "inverter_efficiency_pct", "ambient_temp_c", "panel_temp_c", "tracker_angle_deg",
    "performance_ratio_pct", "inverter1_status", "inverter2_status",
    "inverter3_status", "inverter4_status", "battery_soc_pct", "battery_power_mw",
    "battery_temp_c", "bess_status", "daily_energy_mwh",
    "clock_hour", "clock_min_tens", "clock_min_ones",
    "campus_base_load_mw", "substation_load_mw", "campus_load_mw", "grid_power_mw",
    "solar_to_load_pct",
    "power_setpoint_mw", "mvar_setpoint", "voltage_setpoint_kv", "bess_mode",
    "bess_power_cmd_mw", "breaker_cmd", "tracker_enable", "time_rate", "time_set_hhmm",
    "inverter_fault", "grid_over_voltage", "grid_under_frequency", "breaker_trip",
    "battery_over_temp", "low_soc", "dc_ground_fault", "comms_loss", "curtailment_active",
]

TID = lambda p: "t_pp_" + p          # tag id
VID = lambda p: TID(p)               # binding == plain tag id

_UID = [0]
def uid(pfx):
    # underscore separator is REQUIRED: FUXA matches widget host/child ids by
    # prefixes that include it -- 'D-BAG_' (html_bag), 'D-HXC_' (html_chart),
    # 'A-GXP_'/'B-GXP_'/'H-GXP_' (gauge_progress). Without it processValue finds
    # null children and throws on getAttribute, breaking the signal pipeline.
    _UID[0] += 1
    return f"{pfx}_{_UID[0]:03d}"


class View:
    """Builds one FUXA svg view: collects svg fragments + item bindings."""

    def __init__(self, vid, name, w=1280, h=800, bk=BG):
        self.vid, self.name, self.w, self.h, self.bk = vid, name, w, h, bk
        self.parts, self.items = [], {}

    def add(self, svg, item=None):
        self.parts.append(svg)
        if item:
            self.items[item["id"]] = item

    def _prop(self, p, extra=None):
        d = {"variableId": VID(p), "variableSrc": DEV, "variable": p,
             "alarmId": "", "alarmSrc": "", "alarm": "", "alarmColor": "", "events": []}
        if extra:
            d.update(extra)
        return d

    # --- static decoration ---------------------------------------------------
    def text(self, x, y, s, fs=13, color=SUB, anchor="start", weight="normal"):
        self.add(f'<text x="{x}" y="{y}" font-size="{fs}" font-family="sans-serif" '
                 f'fill="{color}" text-anchor="{anchor}" font-weight="{weight}" '
                 f'stroke-width="0" xml:space="preserve">{s}</text>')

    def box(self, x, y, w, h, fill=PANEL, stroke=EDGE, sw=1.5, rx=8):
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}" '
                 f'stroke="{stroke}" stroke-width="{sw}" rx="{rx}"/>')

    def line(self, x1, y1, x2, y2, color=EDGE, sw=1):
        self.add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{color}" stroke-width="{sw}"/>')

    def flow(self, pts, color, w=6, dur=1.2, reverse=False):
        """A polyline 'pipe' with animated flowing dashes (pure SMIL)."""
        d = "M" + " L".join(f"{x},{y}" for x, y in pts)
        self.add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{w}" '
                 f'stroke-linecap="round" stroke-linejoin="round" opacity="0.30"/>')
        vals = "28;0" if not reverse else "0;28"
        self.add(f'<path d="{d}" fill="none" stroke="{color}" stroke-width="{w}" '
                 f'stroke-linecap="round" stroke-linejoin="round" stroke-dasharray="12 16">'
                 f'<animate attributeName="stroke-dashoffset" values="{vals}" '
                 f'dur="{dur}s" repeatCount="indefinite"/></path>')

    # --- bound widgets -------------------------------------------------------
    def value(self, x, y, p, units="", fs=16, color=INK, anchor="middle", src=None):
        # src="hs" binds to the heat-station device (t_hs_* tags); default = power_plant.
        gid = uid("VAL")
        vid, vsrc = (("t_hs_" + p, "heat_station") if src == "hs" else (VID(p), DEV))
        prop = {"variableId": vid, "variableSrc": vsrc, "variable": p, "alarmId": "",
                "alarmSrc": "", "alarm": "", "alarmColor": "", "events": [],
                "ranges": [{"type": "unit", "min": 0, "max": 999999,
                            "text": (" " + units) if units else ""}]}
        self.add(f'<g id="{gid}" type="svg-ext-value" fill="{color}" stroke="{color}" '
                 f'font-size="{fs}" stroke-width="0" font-family="sans-serif" text-anchor="{anchor}">'
                 f'<text id="{gid}_t" fill="{color}" stroke="{color}" font-size="{fs}" '
                 f'font-family="sans-serif" text-anchor="{anchor}" stroke-width="0" '
                 f'xml:space="preserve" x="{x}" y="{y}">##.##</text></g>',
                 {"id": gid, "type": "svg-ext-value", "name": p, "property": prop, "label": "Value"})

    def bag(self, x, y, w, h, p, gtype, mn, mx, zones=None, labels=None, frac=0, fs=22, pointer=True):
        """html_bag radial gauge. gtype: 'gauge'|'zones'|'donut'."""
        if not USE_BAG:
            return self._bag_fallback(x, y, w, h, p, mn, mx, zones)
        gid = uid("BAG")               # host div 'D-BAG_...' (FUXA prefixD)
        tmap = {"gauge": 0, "donut": 1, "zones": 2}
        opts = {"type": tmap[gtype], "minValue": mn, "maxValue": mx, "animationSpeed": 32,
                "fontSize": fs, "fractionDigits": frac, "ticksEnabled": True,
                "radiusScale": 0.9, "colorStart": BLUE, "colorStop": BLUE,
                "strokeColor": "#16303f",
                "pointer": {"length": 0.55, "strokeWidth": 0.04, "color": INK} if pointer
                else {"length": 0.0, "strokeWidth": 0.0, "color": INK}}
        if zones:
            opts["staticZones"] = zones
        if labels:
            opts["staticLabels"] = {"font": "9px sans-serif", "labels": labels,
                                    "color": SUB, "fractionDigits": 0}
        self.add(f'<g id="{gid}" type="svg-ext-html_bag">'
                 f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="none" stroke="none" id="{gid}_r"/>'
                 f'<foreignObject id="H-{gid}" x="{x}" y="{y}" width="{w}" height="{h}">'
                 f'<div id="D-{gid}" style="width:100%;height:100%;"></div>'
                 f'</foreignObject></g>',
                 {"id": gid, "type": "svg-ext-html_bag", "name": p,
                  "property": self._prop(p, {"options": opts}), "label": "HtmlBag"})

    def _bag_fallback(self, x, y, w, h, p, mn, mx, zones):
        col = (zones[0]["strokeStyle"] if zones else BLUE)
        self.progress(x + 8, y + h - 24, w - 16, 12, p, mn, mx, color=col)
        self.value(x + w / 2, y + h / 2, p, "", 24, INK, "middle")

    def semaphore(self, cx, cy, p, r=10, on=RED, off="#26475c"):
        gid = uid("GSE")
        self.add(f'<g type="svg-ext-gauge_semaphore" fill="#000" font-size="14" '
                 f'stroke="#000" font-family="sans-serif" id="{gid}">'
                 f'<ellipse cx="{cx}" cy="{cy}" rx="{r}" ry="{r}" fill="{off}" '
                 f'stroke="#0a1a24" id="{gid}_e"/></g>',
                 {"id": gid, "type": "svg-ext-gauge_semaphore", "name": p,
                  "property": self._prop(p, {"ranges": [
                      {"type": "range", "min": "1", "max": "1", "color": on},
                      {"type": "range", "min": "0", "max": "0", "color": off}]}),
                  "label": "HtmlSemaphore"})

    def progress(self, x, y, w, h, p, mn, mx, color=BLUE):
        gid = uid("GXP")
        self.add(f'<g font-family="sans-serif" font-size="14" type="svg-ext-gauge_progress" '
                 f'id="{gid}" stroke="null">'
                 f'<rect fill="#16303f" height="{h}" width="{w}" y="{y}" x="{x}" id="A-{gid}" stroke="null"/>'
                 f'<rect fill="{color}" height="{h}" width="{w}" y="{y}" x="{x}" id="B-{gid}" stroke="null"/>'
                 f'<foreignObject font-size="14" id="H-{gid}" width="{w}" height="{h}" y="{y}" x="{x}"/></g>',
                 {"id": gid, "type": "svg-ext-gauge_progress", "name": p,
                  "property": self._prop(p, {"ranges": [{"type": "minmax", "min": mn, "max": mx,
                                            "style": [True, True], "color": color}]}),
                  "label": "HtmlProgress"})

    def button(self, x, y, w, h, label, p, val, color="#2d6cdf", action="onSetValue"):
        gid = uid("HXB")
        self.add(f'<g id="{gid}" type="svg-ext-html_button" fill="#FFFFFF" font-size="14" '
                 f'font-family="sans-serif" text-anchor="right" stroke="#000000">'
                 f'<rect stroke-width="0" x="{x}" y="{y}" width="{w}" height="{h}" fill="{color}" id="{gid}_r" stroke="#fff"/>'
                 f'<foreignObject x="{x}" y="{y}" height="{h}" width="{w}" id="{gid}_h">'
                 f'<BUTTON style="width:100%;height:100%;background-color:{color};color:#fff;'
                 f'border:none;border-radius:5px;font-size:12px;font-weight:600;" '
                 f'class="md-btn md-btn-raised" id="{gid}_b">{label}</BUTTON></foreignObject></g>',
                 {"id": gid, "type": "svg-ext-html_button", "name": label,
                  "property": {"events": [{"type": "click", "action": action, "actparam": str(val),
                                           "actoptions": {}}],
                               "variableId": (VID(p) if p else ""), "variableSrc": DEV,
                               "variable": (p or ""), "alarmId": "", "alarmSrc": "",
                               "alarm": "", "alarmColor": ""},
                  "label": "HtmlButton"})

    def nav(self, x, y, w, h, label, target, color="#16374e"):
        self.button(x, y, w, h, label, None, target, color=color, action="onpage")

    def proceng(self, gid, inner, p, ranges, off="#5b6b78", label=None, lx=0, ly=0):
        """Hand-authored electrical symbol recolored by a status code. FUXA sets
        `fill` on the <g> only; children inherit it, so the recolored shapes must
        carry NO `fill` of their own (an explicit fill would win and never change).
        Detail line-work uses stroke + fill='none' so it stays dark/fixed."""
        self.add(f'<g type="svg-ext-proceng" id="{gid}" fill="{off}">{inner}</g>',
                 {"id": gid, "type": "svg-ext-proceng", "name": p,
                  "property": self._prop(p, {"ranges": ranges}), "label": "Proceng"})
        if label:
            self.text(lx, ly, label, 10, SUB, "middle", "bold")

    def chart(self, x, y, w, h, chart_id, y1=None, y2=None):
        gid = uid("HXC")                # host div must start 'D-HXC_' (FUXA prefixD)
        # legendMode "bottom" = bottom legend ON, floating cursor TOOLTIP OFF (its
        # box clips to ~2 lines so the other series overflow it). The bottom legend
        # still updates with each series' value on hover.
        opts = {"legendMode": "bottom"}
        if y1:
            opts["scaleY1min"], opts["scaleY1max"] = y1
        if y2:
            opts["scaleY2min"], opts["scaleY2max"] = y2
        self.add(f'<g id="{gid}" type="svg-ext-html_chart" fill="#fff" font-size="14" '
                 f'font-family="sans-serif" text-anchor="right" stroke="#000">'
                 f'<rect stroke-width="0" x="{x}" y="{y}" width="{w}" height="{h}" id="{gid}_r" fill="#f4f7fb" stroke="null"/>'
                 f'<foreignObject x="{x}" y="{y}" height="{h}" width="{w}" id="H-{gid}">'
                 f'<DIV style="width:100%;height:100%;background-color:#f4f7fb;border-radius:6px;" id="D-{gid}"></DIV>'
                 f'</foreignObject></g>',
                 {"id": gid, "type": "svg-ext-html_chart", "name": "Trend",
                  "property": {"id": chart_id, "type": "realtime1", "options": opts},
                  "label": "HtmlChart"})

    def clock(self, cx, y, label="LOCAL TIME", fs=20, color=GOLD):
        """Digital H:MM clock. FUXA value tiles can't zero-pad, so the minute is
        drawn from separate tens/ones digit points -> always 2 digits (e.g. 6:06)."""
        if label:
            self.text(cx - 80, y - 1, label, 10, SUB, "end", "bold")
        d = max(8, int(fs * 0.62))
        self.value(cx - 5, y, "clock_hour", "", fs, color, "end")
        self.text(cx, y - 1, ":", fs, color, "middle", "bold")
        self.value(cx + 6, y, "clock_min_tens", "", fs, color, "start")
        self.value(cx + 6 + d, y, "clock_min_ones", "", fs, color, "start")

    # --- electrical symbol shorthands (proceng, status-recolored) -----------
    INV_RANGES = [{"type": "range", "min": "1", "max": "1", "color": GREEN},
                  {"type": "range", "min": "2", "max": "2", "color": RED},
                  {"type": "range", "min": "0", "max": "0", "color": "#5b6b78"}]
    BRK_RANGES = [{"type": "range", "min": "1", "max": "1", "color": GREEN},
                  {"type": "range", "min": "0", "max": "0", "color": RED}]
    BESS_RANGES = [{"type": "range", "min": "1", "max": "1", "color": GREEN},   # discharge
                   {"type": "range", "min": "2", "max": "2", "color": BLUE},    # charge
                   {"type": "range", "min": "3", "max": "3", "color": RED},     # fault
                   {"type": "range", "min": "0", "max": "0", "color": "#5b6b78"}]

    def inverter(self, cx, cy, p, s=30, label=None):
        gid = uid("INV")
        inner = (f'<rect x="{cx-s/2}" y="{cy-s/2}" width="{s}" height="{s}" rx="4" '
                 f'stroke="#0a1a24" stroke-width="2" id="{gid}_r"/>'
                 f'<path d="M{cx-9},{cy+4} q4.5,-9 9,0 t9,0" fill="none" stroke="#0a1a24" '
                 f'stroke-width="1.8" id="{gid}_s"/>'
                 f'<line x1="{cx-9}" y1="{cy-7}" x2="{cx+9}" y2="{cy-7}" stroke="#0a1a24" stroke-width="1.6" id="{gid}_l"/>')
        self.proceng(gid, inner, p, self.INV_RANGES, "#5b6b78", label, cx, cy + s / 2 + 13)

    def breaker(self, cx, cy, p, s=26, label=None):
        gid = uid("BRK")
        inner = (f'<rect x="{cx-s/2}" y="{cy-s/2}" width="{s}" height="{s}" rx="3" '
                 f'stroke="#0a1a24" stroke-width="2" id="{gid}_r"/>')
        self.proceng(gid, inner, p, self.BRK_RANGES, "#5b6b78", label, cx, cy + s / 2 + 13)
        # fixed "CB" contacts on top (not recolored)
        self.add(f'<line x1="{cx-7}" y1="{cy+6}" x2="{cx+7}" y2="{cy-6}" stroke="#0a1a24" stroke-width="2.4"/>')
        self.add(f'<circle cx="{cx-7}" cy="{cy+6}" r="2.2" fill="#0a1a24"/>')
        self.add(f'<circle cx="{cx+7}" cy="{cy-6}" r="2.2" fill="#0a1a24"/>')

    def battery(self, cx, cy, p, w=46, h=30, label=None):
        gid = uid("BAT")
        inner = (f'<rect x="{cx-w/2}" y="{cy-h/2}" width="{w}" height="{h}" rx="3" '
                 f'stroke="#0a1a24" stroke-width="2" id="{gid}_r"/>'
                 f'<rect x="{cx+w/2}" y="{cy-5}" width="4" height="10" stroke="#0a1a24" stroke-width="1.5" id="{gid}_t"/>')
        self.proceng(gid, inner, p, self.BESS_RANGES, "#5b6b78", label, cx, cy + h / 2 + 14)
        # fixed +/- marks on top
        self.add(f'<text x="{cx-w/2+9}" y="{cy+4}" font-size="13" fill="#0a1a24" font-weight="bold">+</text>')
        self.add(f'<text x="{cx+w/2-12}" y="{cy+4}" font-size="13" fill="#0a1a24" font-weight="bold">−</text>')

    def transformer(self, cx, cy, label=None):
        # static (un-bound) two-coil GSU symbol
        self.add(f'<circle cx="{cx}" cy="{cy-7}" r="12" fill="none" stroke="{SUB}" stroke-width="2"/>')
        self.add(f'<circle cx="{cx}" cy="{cy+7}" r="12" fill="none" stroke="{SUB}" stroke-width="2"/>')
        if label:
            self.text(cx, cy + 32, label, 10, SUB, "middle", "bold")

    def sun(self, cx, cy, p, r=18, label=None):
        gid = uid("SUN")
        rays = "".join(
            f'<line x1="{cx+(r+3)*__import__("math").cos(a)}" y1="{cy+(r+3)*__import__("math").sin(a)}" '
            f'x2="{cx+(r+9)*__import__("math").cos(a)}" y2="{cy+(r+9)*__import__("math").sin(a)}" '
            f'stroke="#0a1a24" stroke-width="2" id="{gid}_r{i}"/>'
            for i, a in enumerate([j * 0.7853981 for j in range(8)]))
        inner = (f'<circle cx="{cx}" cy="{cy}" r="{r}" stroke="#0a1a24" stroke-width="2" id="{gid}_c"/>' + rays)
        # recolor by irradiance band: bright gold in sun, dim at night
        ranges = [{"type": "range", "min": 1, "max": 250, "color": "#6b5b2e"},
                  {"type": "range", "min": 250, "max": 650, "color": AMBER},
                  {"type": "range", "min": 650, "max": 1200, "color": GOLD},
                  {"type": "range", "min": 0, "max": 0, "color": "#3a3f48"}]
        self.proceng(gid, inner, p, ranges, GOLD, label, cx, cy + r + 22)

    def gridtower(self, cx, cy, label=None):
        self.add(f'<path d="M{cx-12},{cy+18} L{cx-6},{cy-18} L{cx+6},{cy-18} L{cx+12},{cy+18} '
                 f'M{cx-9},{cy} L{cx+9},{cy} M{cx-10.5},{cy+9} L{cx+10.5},{cy+9} '
                 f'M{cx-7},{cy-9} L{cx+7},{cy-9}" fill="none" stroke="{SUB}" stroke-width="2"/>')
        if label:
            self.text(cx, cy + 34, label, 10, SUB, "middle", "bold")

    def pvarray(self, x, y, w, h, label=None):
        # static PV panel block
        self.box(x, y, w, h, "#11324a", "#2f6f9c", 2, 6)
        for i in range(3):
            for j in range(2):
                self.add(f'<rect x="{x+8+i*(w-16)/3}" y="{y+8+j*(h-16)/2}" '
                         f'width="{(w-16)/3-5}" height="{(h-16)/2-5}" fill="#10456e" stroke="#5fa8ff" stroke-width="1"/>')
        if label:
            self.text(x + w / 2, y + h + 14, label, 10, SUB, "middle", "bold")

    # --- assemble ------------------------------------------------------------
    # Hide the redundant uPlot title (we draw our own bold label above each chart)
    # to free vertical space so the legend isn't crammed at the clipped bottom
    # edge; hide only the idle x-axis "1969" row; KEEP the value cells so hovering
    # shows each series' value at the cursor.
    LEGEND_CSS = ('<style>.u-title{display:none !important;}'
                  '.u-legend .u-series:first-child{display:none !important;}'
                  '.u-legend{color:#1b2a36;font-size:12px;}'
                  '.u-legend .u-value{font-weight:600;margin-left:2px;}</style>')

    def svgcontent(self):
        return ('<svg width="{w}" height="{h}" xmlns="http://www.w3.org/2000/svg" '
                'xmlns:svg="http://www.w3.org/2000/svg" xmlns:html="http://www.w3.org/1999/xhtml">'
                .format(w=self.w, h=self.h)
                + self.LEGEND_CSS
                + f'<rect x="0" y="0" width="{self.w}" height="{self.h}" fill="{self.bk}"/>'
                + "".join(self.parts) + "</svg>")

    def to_view(self):
        return {"id": self.vid, "name": self.name, "type": "svg",
                "profile": {"width": self.w, "height": self.h, "bkcolor": self.bk + "ff"},
                "items": self.items, "variables": {}, "svgcontent": self.svgcontent(),
                "property": {"events": []}}


# chart ids
C_MWIRR, C_FIRM, C_PQ, C_SOC, C_CAMPUS = "pp_mw_irr", "pp_firm", "pp_pq", "pp_soc", "pp_campus"


# ===========================================================================
# VIEW 0 — Site / Campus Overview (the landing page; microgrid energy balance)
# ===========================================================================
def build_site():
    v = View("v_site", "SunfieldCampus")
    v.text(28, 32, "SUNFIELD CAMPUS", 22, "#7CE0A0", "start", "bold")
    v.text(248, 32, "solar + BESS microgrid  ·  district heating load  ·  115 kV grid tie", 13, SUB, "start")
    v.line(28, 44, 1252, 44, EDGE, 1)
    v.clock(800, 33)
    view_nav(v, "v_site")

    # --- KPI dial band: the campus energy balance at a glance ---
    dials = [
        ("plant_active_power_mw", "gauge", 0, 110, "SOLAR GEN  MW",
         [{"strokeStyle": GREEN, "min": 0, "max": 100}, {"strokeStyle": RED, "min": 100, "max": 110}],
         [0, 50, 100]),
        ("campus_load_mw", "gauge", 0, 50, "CAMPUS LOAD  MW",
         [{"strokeStyle": AMBER, "min": 0, "max": 50}], [0, 25, 50]),
        ("grid_power_mw", "zones", 0, 200, "GRID  ◄ IMPORT · EXPORT ►",
         [{"strokeStyle": "#e08a3a", "min": 0, "max": 100}, {"strokeStyle": GREEN, "min": 100, "max": 200}],
         [0, 100, 200]),
        ("solar_to_load_pct", "donut", 0, 100, "SOLAR SHARE  %",
         [{"strokeStyle": AMBER, "min": 0, "max": 60}, {"strokeStyle": GREEN, "min": 60, "max": 100}],
         [0, 50, 100]),
        ("battery_soc_pct", "donut", 0, 100, "BESS  SOC  %",
         [{"strokeStyle": RED, "min": 0, "max": 10}, {"strokeStyle": GREEN, "min": 10, "max": 90},
          {"strokeStyle": AMBER, "min": 90, "max": 100}], [0, 50, 100]),
    ]
    dw, gap = 224, 12
    for i, (p, gt, mn, mx, lab, zones, labels) in enumerate(dials):
        x = 28 + i * (dw + gap)
        v.box(x, 56, dw, 150, "#0c1f2e", "#1c3f57", 1.2, 8)
        v.bag(x + 18, 66, dw - 36, 110, p, gt, mn, mx, zones=zones, labels=labels)
        v.text(x + dw / 2, 198, lab, 11, SUB, "middle", "bold")

    # --- campus microgrid energy-flow diagram ---
    v.text(28, 248, "CAMPUS MICROGRID  ·  generation → bus → loads, with grid tie", 12, "#9fd0ff", "start", "bold")
    v.box(20, 258, 1240, 230, "#0b1d2b", "#19384e", 1.2, 8)
    yb = 380
    # solar + BESS source (left)
    v.box(44, yb - 56, 196, 112, "#10243a", "#2f6f9c", 1.6, 8)
    v.sun(86, yb - 24, "irradiance_wm2", 13)
    v.text(170, yb - 30, "SOLAR + BESS", 12, "#cfe9ff", "middle", "bold")
    v.text(140, yb + 2, "GEN", 10, SUB, "start")
    v.value(232, yb + 4, "plant_active_power_mw", "MW", 18, GREEN, "end")
    v.text(140, yb + 28, "SOC", 10, SUB, "start")
    v.value(232, yb + 30, "battery_soc_pct", "%", 15, INK, "end")
    v.battery(110, yb + 30, "bess_status", 36, 20)
    # generation flow to the bus
    v.flow([(240, yb), (548, yb)], GREEN, 7)
    v.value(394, yb - 14, "plant_active_power_mw", "MW", 13, GREEN, "middle")
    # campus bus (vertical bar)
    v.add(f'<rect x="554" y="295" width="12" height="170" rx="3" fill="#2b7fb8"/>')
    v.text(560, 285, "CAMPUS BUS", 10, "#9fd0ff", "middle", "bold")
    # grid tie (up from the bus top)
    v.flow([(560, 300), (560, 256)], BLUE, 6, dur=1.6)
    v.gridtower(560, 238, "115 kV GRID")
    v.box(610, 232, 150, 40, "#0e2433", "#2f6f9c", 1.2, 6)
    v.text(622, 248, "GRID", 10, SUB, "start", "bold")
    v.value(748, 256, "grid_power_mw", "", 16, GOLD, "end")
    v.text(685, 268, "(value−100 = MW; + export / − import)", 8, SUB, "middle")
    # bus -> campus base load
    v.flow([(566, 335), (812, 335)], AMBER, 7)
    v.box(812, yb - 70, 200, 60, "#10283a", "#2f6f9c", 1.6, 8)
    v.text(912, yb - 48, "CAMPUS BUILDINGS", 11, "#cfe9ff", "middle", "bold")
    v.value(912, yb - 24, "campus_base_load_mw", "MW base load", 15, AMBER, "middle")
    # bus -> district heating (the substation, click through)
    v.flow([(566, 425), (812, 425)], "#e0533a", 7)
    v.box(812, yb + 14, 200, 60, "#1a1320", "#7a4dd6", 1.6, 8)
    v.text(912, yb + 36, "DISTRICT HEATING", 11, "#cfe9ff", "middle", "bold")
    v.value(912, yb + 60, "substation_load_mw", "MW pump load", 15, "#c79bff", "middle")
    v.nav(1024, yb - 64, 64, 22, "OPEN ▶", "v_heat_station")
    v.nav(1024, yb + 18, 64, 22, "OPEN ▶", "v_power_plant")

    # --- compact summary strip (both plants, dual-device) ---
    tiles = [("SOLAR GEN", "plant_active_power_mw", "MW", GREEN, None),
             ("BESS SOC", "battery_soc_pct", "%", INK, None),
             ("IRRADIANCE", "irradiance_wm2", "W/m²", GOLD, None),
             ("HEAT OUTPUT", "instant_heat", "GJ/h", "#ff9a6a", "hs"),
             ("LOOP FLOW", "secondary_flow", "m³/h", "#7CE0A0", "hs"),
             ("SUPPLY TEMP", "secondary_supply_temp", "°C", "#ff9a6a", "hs")]
    for i, (lab, p, u, c, src) in enumerate(tiles):
        x = 24 + i * 206
        v.box(x, 497, 196, 52, "#0e2030", "#21506e", 1.2, 6)
        v.text(x + 14, 516, lab, 10, SUB, "start", "bold")
        v.value(x + 182, 538, p, u, 17, c, "end", src=src)
    # --- big full-width trend: generation vs campus load (+ grid exchange) ---
    v.text(28, 568, "GENERATION vs CAMPUS LOAD  ·  surplus exported / deficit imported", 12, "#9fd0ff", "start", "bold")
    v.chart(24, 576, 1228, 212, C_CAMPUS, y1=(0, 110), y2=(0, 200))
    return v


NAV_VIEWS = [("CAMPUS", "v_site"), ("SOLAR", "v_power_plant"), ("1-LINE", "v_pp_oneline"),
             ("BESS", "v_pp_bess"), ("SUBSTN", "v_heat_station")]


def view_nav(v, here):
    """Standard nav button row (top-right) linking all five views."""
    x = v.w - 4
    for label, target in reversed(NAV_VIEWS):
        if target == here:
            continue
        wbtn = 92
        x -= wbtn + 5
        v.nav(x, 12, wbtn, 24, label, target)


def inject_nav(view, here):
    """Splice the standard onpage nav row into an EXISTING view dict (built
    elsewhere, e.g. the substation) so it can jump to the other views. Inserts the
    button SVG before </svg> and merges the items; global uid() keeps ids unique."""
    w = (view.get("profile") or {}).get("width", 1280)
    tmp = View(view["id"], view.get("name", "view"), w=w)
    x = w - 4
    for label, target in reversed(NAV_VIEWS):
        if target == here:
            continue
        wbtn = 92
        x -= wbtn + 5
        tmp.nav(x, 12, wbtn, 24, label, target)
    view["svgcontent"] = view["svgcontent"].replace("</svg>", "".join(tmp.parts) + "</svg>")
    view.setdefault("items", {}).update(tmp.items)


# ===========================================================================
# VIEW 1 — Overview & Day-in-the-Life
# ===========================================================================
def build_overview():
    v = View("v_power_plant", "SolarPlant")
    v.text(28, 32, "SUNFIELD SOLAR", 22, "#ffd479", "start", "bold")
    v.text(232, 32, "100 MWac PV + 25 MW / 100 MWh DC-coupled BESS  ·  115 kV POI", 13, SUB, "start")
    v.line(28, 44, 1252, 44, EDGE, 1)
    v.clock(800, 33)                              # live local time of day
    view_nav(v, "v_power_plant")

    # --- KPI dial band ---
    dials = [
        ("plant_active_power_mw", "gauge", 0, 110, "PLANT  MW",
         [{"strokeStyle": GREEN, "min": 0, "max": 100}, {"strokeStyle": RED, "min": 100, "max": 110}],
         [0, 50, 100]),
        ("grid_frequency_hz", "zones", 590, 610, "GRID  Hz  (x10)",
         [{"strokeStyle": RED, "min": 590, "max": 595}, {"strokeStyle": AMBER, "min": 595, "max": 598},
          {"strokeStyle": GREEN, "min": 598, "max": 602}, {"strokeStyle": AMBER, "min": 602, "max": 605},
          {"strokeStyle": RED, "min": 605, "max": 610}], [590, 600, 610]),
        ("poi_voltage_kv", "zones", 1090, 1210, "POI  kV  (x10)",
         [{"strokeStyle": RED, "min": 1090, "max": 1120}, {"strokeStyle": GREEN, "min": 1120, "max": 1180},
          {"strokeStyle": RED, "min": 1180, "max": 1210}], [1090, 1150, 1210]),
        ("irradiance_wm2", "gauge", 0, 1200, "IRRADIANCE  W/m²",
         [{"strokeStyle": "#6b5b2e", "min": 0, "max": 250}, {"strokeStyle": AMBER, "min": 250, "max": 650},
          {"strokeStyle": GOLD, "min": 650, "max": 1200}], [0, 600, 1200]),
        ("battery_soc_pct", "donut", 0, 100, "BESS  SOC  %",
         [{"strokeStyle": RED, "min": 0, "max": 10}, {"strokeStyle": GREEN, "min": 10, "max": 90},
          {"strokeStyle": AMBER, "min": 90, "max": 100}], [0, 50, 100]),
    ]
    dw, gap = 224, 12
    x0 = 28
    for i, (p, gt, mn, mx, lab, zones, labels) in enumerate(dials):
        x = x0 + i * (dw + gap)
        v.box(x, 56, dw, 150, "#0c1f2e", "#1c3f57", 1.2, 8)
        v.bag(x + 18, 66, dw - 36, 110, p, gt, mn, mx, zones=zones, labels=labels)
        v.text(x + dw / 2, 198, lab, 11, SUB, "middle", "bold")

    # --- small value strip ---
    strip = [("power_factor", "PF x100", ""), ("daily_energy_mwh", "ENERGY", "MWh"),
             ("performance_ratio_pct", "PR", "%"), ("clipping_loss_mw", "CLIP", "MW"),
             ("panel_temp_c", "PANEL", "°C")]
    sw = 232
    for i, (p, lab, u) in enumerate(strip):
        x = 28 + i * (sw + 12)
        v.box(x, 214, sw, 40, "#0e2030", "#21506e", 1, 6)
        v.text(x + 12, 239, lab, 10, SUB, "start", "bold")
        v.value(x + sw - 12, 240, p, u, 16, GOLD, "end")

    # --- single-line (center band) ---
    yb = 330
    v.text(28, 282, "SINGLE-LINE  ·  PV → INVERTERS → GSU → POI → GRID", 12, "#9fd0ff", "start", "bold")
    v.box(20, 292, 1240, 150, "#0b1d2b", "#19384e", 1.2, 8)
    v.sun(70, yb - 6, "irradiance_wm2", 16, "SUN")
    v.flow([(92, yb), (150, yb)], DCCOL, 6)                       # sun -> PV (light)
    v.pvarray(152, yb - 28, 96, 56, "PV ARRAY 125 MWdc")
    v.flow([(248, yb), (320, yb)], DCCOL, 7)                      # PV DC bus
    # DC bus node + battery branch (DC-coupled)
    v.add(f'<circle cx="320" cy="{yb}" r="4" fill="{DCCOL}"/>')
    v.text(320, yb - 30, "DC BUS", 9, SUB, "middle")
    v.flow([(320, yb), (320, yb + 52)], BLUE, 6, dur=1.6)        # DC/DC down to battery
    v.battery(320, yb + 74, "bess_status", 46, 28, "BESS")
    v.value(320, yb + 112, "battery_power_mw", "", 12, INK, "middle")  # raw +50; see BESS view for signed
    v.flow([(320, yb), (392, yb)], DCCOL, 7)                      # DC to inverters
    # inverter bank (4)
    for i in range(4):
        v.inverter(392 + 70 + i * 64, yb, f"inverter{i+1}_status", 28)
    v.text(392 + 70 + 1.5 * 64, yb - 28, "INVERTERS  4 x 25 MW", 9, SUB, "middle")
    xinv_end = 392 + 70 + 3 * 64 + 30
    v.flow([(xinv_end, yb), (xinv_end + 60, yb)], ACCOL, 7)       # AC collector
    v.transformer(xinv_end + 84, yb, "GSU 34.5/115kV")
    v.flow([(xinv_end + 108, yb), (xinv_end + 168, yb)], ACCOL, 7)
    v.breaker(xinv_end + 192, yb, "main_breaker_status", 26, "POI CB")
    v.flow([(xinv_end + 210, yb), (xinv_end + 268, yb)], ACCOL, 7)
    v.gridtower(xinv_end + 292, yb, "GRID 115kV")
    # POI MW chip
    v.box(xinv_end + 150, yb + 40, 150, 34, "#0e2433", "#2f6f9c", 1.2, 6)
    v.text(xinv_end + 162, yb + 54, "EXPORT", 9, SUB, "start", "bold")
    v.value(xinv_end + 290, yb + 62, "plant_active_power_mw", "MW", 18, GREEN, "end")

    # --- annunciator (right) ---
    ax, ay = 1062, 452
    v.box(ax, ay, 198, 196, "#1a0f12", "#5a2230", 1.5, 8)
    v.text(ax + 14, ay + 22, "ANNUNCIATOR", 12, "#ff8a8a", "start", "bold")
    alarms = [("Inverter Fault", "inverter_fault"), ("Grid Over-Voltage", "grid_over_voltage"),
              ("Grid Under-Freq", "grid_under_frequency"), ("Breaker Trip", "breaker_trip"),
              ("Battery Over-Temp", "battery_over_temp"), ("Low SOC", "low_soc"),
              ("DC Ground Fault", "dc_ground_fault"), ("Comms Loss", "comms_loss"),
              ("Curtailment", "curtailment_active")]
    for i, (lab, p) in enumerate(alarms):
        yy = ay + 44 + i * 17
        v.semaphore(ax + 16, yy - 4, p, 6)
        v.text(ax + 30, yy, lab, 10, INK, "start")

    # --- signature trends (bottom) ---
    v.text(28, 460, "PLANT MW vs IRRADIANCE  (DC climbs · AC clips flat at 100)", 11, "#9fd0ff", "start", "bold")
    v.chart(24, 468, 510, 202, C_MWIRR, y1=(0, 120), y2=(0, 1200))
    v.text(556, 460, "BESS FIRMING  ·  SOC (%) & Battery Power (MW +50)", 11, "#9fd0ff", "start", "bold")
    v.chart(552, 468, 500, 202, C_FIRM, y1=(0, 100), y2=(25, 75))

    # --- operator presets (bottom strip) ---
    py = 672
    v.box(24, py, 1028, 118, PANEL, EDGE, 1.5, 8)
    v.text(40, py + 22, "OPERATOR CONTROLS", 12, "#9fd0ff", "start", "bold")
    v.text(40, py + 50, "Export Setpoint", 11, INK, "start")
    for i, val in enumerate((40, 60, 80, 100)):
        v.button(150 + i * 48, py + 38, 44, 22, f"{val}", "power_setpoint_mw", val,
                 "#2e9e5b" if val == 100 else "#2d6cdf")
    v.text(40, py + 82, "BESS Mode", 11, INK, "start")
    for i, (lab, val, col) in enumerate([("AUTO", 0, "#2e9e5b"), ("CHARGE", 1, "#2d6cdf"),
                                         ("DISCHG", 2, "#c0392b")]):
        v.button(150 + i * 64, py + 70, 60, 22, lab, "bess_mode", val, col)
    v.text(372, py + 50, "Breaker", 11, INK, "start")
    v.button(430, py + 38, 56, 22, "CLOSE", "breaker_cmd", 1, "#2e9e5b")
    v.button(490, py + 38, 56, 22, "OPEN", "breaker_cmd", 0, "#c0392b")
    v.text(372, py + 82, "Tracker", 11, INK, "start")
    v.button(430, py + 70, 56, 22, "TRACK", "tracker_enable", 1, "#2e9e5b")
    v.button(490, py + 70, 56, 22, "STOW", "tracker_enable", 0, "#7a5230")
    # time-of-day controls: jump to a time, or play / pause / fast-forward the day
    v.line(566, py + 14, 566, py + 106, EDGE, 1)
    v.text(584, py + 24, "TIME OF DAY", 12, "#ffd479", "start", "bold")
    v.clock(700, py + 24, label="", fs=18, color=GOLD)
    v.text(584, py + 52, "Clock", 11, INK, "start")
    v.button(648, py + 40, 64, 22, "PAUSE", "time_rate", 0, "#7a5230")
    v.button(716, py + 40, 64, 22, "PLAY", "time_rate", 1, "#2e9e5b")
    v.button(784, py + 40, 64, 22, "FAST 4×", "time_rate", 4, "#2d6cdf")
    v.text(584, py + 84, "Jump", 11, INK, "start")
    for i, (lab, hhmm) in enumerate([("00:00", 0), ("04:00", 400), ("08:00", 800),
                                     ("12:00", 1200), ("16:00", 1600), ("20:00", 2000)]):
        v.button(640 + i * 66, py + 72, 60, 22, lab, "time_set_hhmm", hhmm, "#3a6ea5")
    return v


# ===========================================================================
# VIEW 2 — Electrical One-Line & POI Metering
# ===========================================================================
def build_oneline():
    v = View("v_pp_oneline", "SolarPlant_OneLine")
    v.text(28, 32, "ELECTRICAL ONE-LINE & POI METERING", 20, "#9fd0ff", "start", "bold")
    v.line(28, 44, 1252, 44, EDGE, 1)
    view_nav(v, "v_pp_oneline")

    # full single-line strip
    yb = 150
    v.box(20, 70, 1240, 150, "#0b1d2b", "#19384e", 1.2, 8)
    v.pvarray(40, yb - 30, 92, 60, "PV 125 MWdc")
    v.battery(186, yb, "bess_status", 44, 28, "DC BESS 25MW")
    v.flow([(132, yb), (320, yb)], DCCOL, 7)
    v.add(f'<circle cx="186" cy="{yb-30}" r="3" fill="{DCCOL}"/>')
    v.flow([(186, yb - 14), (186, yb - 30), (320, yb - 30)], BLUE, 5, dur=1.6)
    # inverter 2x2
    for i in range(4):
        cx, cy = 360 + (i % 2) * 56, yb - 18 + (i // 2) * 36
        v.inverter(cx, cy, f"inverter{i+1}_status", 26)
    v.text(388, yb - 44, "INVERTERS", 9, SUB, "middle")
    v.flow([(320, yb), (340, yb)], DCCOL, 7)
    v.text(470, yb + 30, "34.5 kV COLLECTOR", 9, SUB, "middle")
    v.flow([(444, yb), (560, yb)], ACCOL, 7)
    v.transformer(590, yb, "GSU 34.5/115")
    v.flow([(614, yb), (700, yb)], ACCOL, 7)
    v.breaker(728, yb, "main_breaker_status", 28, "POI CB")
    v.flow([(748, yb), (840, yb)], ACCOL, 7)
    v.add(f'<circle cx="860" cy="{yb}" r="5" fill="{ACCOL}"/>')
    v.text(860, yb - 22, "POI 115 kV", 9, SUB, "middle")
    v.flow([(860, yb), (980, yb)], ACCOL, 7)
    v.gridtower(1010, yb, "GRID")

    # big metering readouts
    mets = [("plant_active_power_mw", "ACTIVE POWER", "MW", GREEN, 34),
            ("plant_reactive_power_mvar", "REACTIVE (+50)", "MVAR", GOLD, 22),
            ("poi_current_a", "LINE CURRENT", "A", INK, 22),
            ("clipping_loss_mw", "CLIPPING LOSS", "MW", AMBER, 22)]
    for i, (p, lab, u, c, fs) in enumerate(mets):
        x = 28 + i * 304
        v.box(x, 244, 290, 92, "#0e2030", "#21506e", 1.5, 8)
        v.text(x + 16, 268, lab, 11, SUB, "start", "bold")
        v.value(x + 145, 314, p, u, fs, c, "middle")

    # center-zero dials: MVAR, kV, PF
    v.box(28, 352, 600, 196, "#0c1f2e", "#1c3f57", 1.2, 8)
    v.text(44, 376, "POI ELECTRICAL", 12, "#9fd0ff", "start", "bold")
    v.bag(40, 388, 180, 130, "plant_reactive_power_mvar", "zones", 17, 83,
          zones=[{"strokeStyle": BLUE, "min": 17, "max": 50}, {"strokeStyle": GREEN, "min": 50, "max": 83}],
          labels=[17, 50, 83])
    v.text(130, 532, "MVAR  absorb ← 50 → export", 10, SUB, "middle")
    v.bag(230, 388, 180, 130, "poi_voltage_kv", "zones", 1090, 1210,
          zones=[{"strokeStyle": RED, "min": 1090, "max": 1120}, {"strokeStyle": GREEN, "min": 1120, "max": 1180},
                 {"strokeStyle": RED, "min": 1180, "max": 1210}], labels=[1090, 1150, 1210])
    v.text(320, 532, "POI kV (x10)", 10, SUB, "middle")
    v.bag(420, 388, 180, 130, "power_factor", "gauge", 80, 100,
          zones=[{"strokeStyle": AMBER, "min": 80, "max": 95}, {"strokeStyle": GREEN, "min": 95, "max": 100}],
          labels=[80, 90, 100])
    v.text(510, 532, "Power Factor (x100)", 10, SUB, "middle")

    # PQ-envelope chart
    v.text(648, 376, "PQ ENVELOPE  ·  POI Voltage & Reactive", 11, "#9fd0ff", "start", "bold")
    v.chart(644, 384, 608, 202, C_PQ, y1=(1090, 1210), y2=(17, 83))

    # controls + alarms
    py = 596
    v.box(28, py, 600, 194, PANEL, EDGE, 1.5, 8)
    v.text(44, py + 24, "DISPATCH CONTROLS", 12, "#9fd0ff", "start", "bold")
    v.text(44, py + 54, "Export MW", 11, INK, "start")
    for i, val in enumerate((40, 60, 80, 100)):
        v.button(150 + i * 50, py + 42, 46, 22, f"{val}", "power_setpoint_mw", val,
                 "#2e9e5b" if val == 100 else "#2d6cdf")
    v.text(44, py + 90, "MVAR Set", 11, INK, "start")
    for i, (lab, raw) in enumerate([("-30", 20), ("-15", 35), ("0", 50), ("+15", 65), ("+30", 80)]):
        v.button(150 + i * 50, py + 78, 46, 22, lab, "mvar_setpoint", raw, "#7a4dd6")
    v.text(44, py + 126, "Breaker", 11, INK, "start")
    v.button(150, py + 114, 70, 22, "CLOSE", "breaker_cmd", 1, "#2e9e5b")
    v.button(224, py + 114, 70, 22, "OPEN", "breaker_cmd", 0, "#c0392b")
    v.text(330, py + 126, "Tracker", 11, INK, "start")
    v.button(404, py + 114, 70, 22, "TRACK", "tracker_enable", 1, "#2e9e5b")
    v.button(478, py + 114, 70, 22, "STOW", "tracker_enable", 0, "#7a5230")
    v.text(44, py + 162, "Inverter η", 11, SUB, "start")
    v.value(150, py + 163, "inverter_efficiency_pct", "%", 15, INK, "start")
    v.text(330, py + 162, "Grid Hz (x10)", 11, SUB, "start")
    v.value(470, py + 163, "grid_frequency_hz", "", 15, INK, "start")

    v.box(644, py, 608, 194, "#1a0f12", "#5a2230", 1.5, 8)
    v.text(660, py + 24, "PROTECTION & ALARMS", 12, "#ff8a8a", "start", "bold")
    pal = [("Breaker Trip", "breaker_trip"), ("Grid Over-Voltage", "grid_over_voltage"),
           ("Grid Under-Freq", "grid_under_frequency"), ("Inverter Fault", "inverter_fault"),
           ("DC Ground Fault", "dc_ground_fault"), ("Comms Loss", "comms_loss"),
           ("Curtailment Active", "curtailment_active"), ("Low SOC", "low_soc")]
    for i, (lab, p) in enumerate(pal):
        col, row = i % 2, i // 2
        xx = 664 + col * 300
        yy = py + 56 + row * 34
        v.semaphore(xx, yy - 4, p, 7)
        v.text(xx + 16, yy, lab, 11, INK, "start")
    return v


# ===========================================================================
# VIEW 3 — BESS detail
# ===========================================================================
def build_bess():
    v = View("v_pp_bess", "SolarPlant_BESS")
    v.text(28, 32, "BESS DETAIL  ·  25 MW / 100 MWh DC-COUPLED", 20, "#9fd0ff", "start", "bold")
    v.line(28, 44, 1252, 44, EDGE, 1)
    view_nav(v, "v_pp_bess")

    # big SOC donut
    v.box(28, 70, 380, 360, "#0c1f2e", "#1c3f57", 1.2, 8)
    v.text(44, 96, "STATE OF CHARGE", 13, "#9fd0ff", "start", "bold")
    v.bag(70, 110, 300, 230, "battery_soc_pct", "donut", 0, 100,
          zones=[{"strokeStyle": RED, "min": 0, "max": 10}, {"strokeStyle": GREEN, "min": 10, "max": 90},
                 {"strokeStyle": AMBER, "min": 90, "max": 100}], labels=[0, 50, 100], fs=34)
    v.text(218, 360, "usable window 10 - 90 %", 11, SUB, "middle")
    v.progress(70, 384, 296, 16, "battery_soc_pct", 0, 100, color=GREEN)
    v.text(218, 418, "SOC bar", 10, SUB, "middle")

    # power dial + status
    v.box(428, 70, 410, 360, "#0c1f2e", "#1c3f57", 1.2, 8)
    v.text(444, 96, "BATTERY POWER  ·  charge ← 0 → discharge", 13, "#9fd0ff", "start", "bold")
    v.bag(498, 116, 270, 200, "battery_power_mw", "zones", 25, 75,
          zones=[{"strokeStyle": BLUE, "min": 25, "max": 50}, {"strokeStyle": GREEN, "min": 50, "max": 75}],
          labels=[25, 50, 75], fs=26)
    v.text(633, 332, "MW  (stored +50 :  25=−25  50=0  75=+25)", 10, SUB, "middle")
    v.battery(560, 380, "bess_status", 60, 34)
    v.semaphore(660, 380, "low_soc", 9)
    v.text(678, 384, "LOW SOC", 10, INK, "start")
    v.semaphore(660, 408, "battery_over_temp", 9)
    v.text(678, 412, "OVER-TEMP", 10, INK, "start")

    # status tiles
    tiles = [("battery_temp_c", "BATTERY TEMP", "°C", INK),
             ("bess_status", "BESS STATUS", "", GOLD),
             ("daily_energy_mwh", "ENERGY TODAY", "MWh", GREEN)]
    for i, (p, lab, u, c) in enumerate(tiles):
        x = 858 + 0
        y = 70 + i * 120
        v.box(858, y, 394, 108, "#0e2030", "#21506e", 1.5, 8)
        v.text(874, y + 26, lab, 11, SUB, "start", "bold")
        v.value(858 + 197, y + 74, p, u, 30, c, "middle")

    # SOC vs power trend
    v.text(28, 436, "SOC vs BATTERY POWER  ·  charges from the clip, discharges into a cloud", 12, "#9fd0ff", "start", "bold")
    v.chart(24, 444, 1228, 234, C_SOC, y1=(0, 100), y2=(25, 75))

    # dispatch controls
    py = 686
    v.box(24, py, 1228, 92, PANEL, EDGE, 1.5, 8)
    v.text(40, py + 24, "BESS DISPATCH", 12, "#9fd0ff", "start", "bold")
    v.text(40, py + 56, "Mode", 11, INK, "start")
    for i, (lab, val, col) in enumerate([("AUTO-SMOOTH", 0, "#2e9e5b"), ("FORCE CHARGE", 1, "#2d6cdf"),
                                         ("FORCE DISCHARGE", 2, "#c0392b")]):
        v.button(100 + i * 130, py + 44, 124, 24, lab, "bess_mode", val, col)
    v.text(520, py + 56, "Power Cmd MW", 11, INK, "start")
    for i, (lab, raw) in enumerate([("-25", 25), ("-10", 40), ("0", 50), ("+10", 60), ("+25", 75)]):
        v.button(640 + i * 56, py + 44, 52, 24, lab, "bess_power_cmd_mw", raw, "#7a4dd6")
    v.text(980, py + 56, "Live MW (+50)", 11, SUB, "start")
    v.value(1130, py + 57, "battery_power_mw", "", 18, INK, "start")
    return v


# ===========================================================================
# DEVICE + CHARTS + ASSEMBLE
# ===========================================================================
def build_device():
    tags = {}
    for p in POINTS:
        tags[TID(p)] = {"id": TID(p), "name": p, "type": "Real",
                        "address": f"{DEVPATH}/{p}",
                        "daq": {"enabled": False, "interval": 60, "changed": False, "restored": False}}
    return {"id": DEV, "name": DEVNAME, "enabled": True, "type": "Volttron",
            "property": {"address": BRIDGE, "port": None, "slot": None, "rack": None,
                         "baudrate": 9600, "databits": 8, "stopbits": 1, "parity": "None",
                         "delay": 10, "forceFC16": False},
            "polling": 1000, "tags": tags}


def _line(point, label, color, yaxis=1, lw=2):
    return {"id": TID(point), "name": point, "label": label, "yaxis": yaxis,
            "device": DEVNAME, "color": color, "lineWidth": lw}


def build_charts():
    return [
        {"id": C_MWIRR, "name": "Plant MW vs Irradiance", "type": "realtime1", "lines": [
            _line("pv_dc_power_mw", "DC MW", "#5fa8ff", 1),
            _line("inverter_ac_power_mw", "AC MW", "#f0a23e", 1),
            _line("plant_active_power_mw", "Export MW", "#27c06a", 1),
            _line("irradiance_wm2", "Irradiance", "#ffd479", 2)]},
        {"id": C_FIRM, "name": "BESS Firming", "type": "realtime1", "lines": [
            _line("battery_soc_pct", "SOC %", "#27c06a", 1),
            _line("battery_power_mw", "Batt MW (+50)", "#7a4dd6", 2)]},
        {"id": C_PQ, "name": "PQ Envelope", "type": "realtime1", "lines": [
            _line("poi_voltage_kv", "POI kV (x10)", "#4dabf7", 1),
            _line("plant_reactive_power_mvar", "MVAR (+50)", "#ffd479", 2)]},
        {"id": C_SOC, "name": "SOC vs Power", "type": "realtime1", "lines": [
            _line("battery_soc_pct", "SOC %", "#27c06a", 1),
            _line("battery_power_mw", "Batt MW (+50)", "#7a4dd6", 2)]},
        {"id": C_CAMPUS, "name": "Generation vs Load", "type": "realtime1", "lines": [
            _line("plant_active_power_mw", "Solar Gen MW", "#27c06a", 1),
            _line("campus_load_mw", "Campus Load MW", "#f0a23e", 1),
            _line("grid_power_mw", "Grid (+100)", "#4dabf7", 2)]},
    ]


def main():
    # 1) refresh the substation (its generator POSTs a substation-only project)
    print(">>> refreshing substation via build_dashboard.py ...")
    subprocess.run([sys.executable, os.path.join(HERE, "build_dashboard.py")], check=True)

    # 2) GET that project and AUGMENT it with the solar plant (full-replace POST)
    prj = requests.get(f"{FUXA}/api/project", timeout=20).json()
    prj = prj.get("data") or prj
    prj.setdefault("devices", {})[DEV] = build_device()

    views = list(prj.get("hmi", {}).get("views", []))
    plant_ids = ("v_site", "v_power_plant", "v_pp_oneline", "v_pp_bess")
    views = [vw for vw in views if vw.get("id") not in plant_ids]
    views += [build_site().to_view(), build_overview().to_view(),
              build_oneline().to_view(), build_bess().to_view()]
    # give the substation view the same nav row back into the campus + plant views
    for vw in views:
        if vw.get("id") == "v_heat_station":
            inject_nav(vw, "v_heat_station")
    prj["hmi"]["views"] = views

    # nav menu (hamburger) + start on the campus landing page
    layout = prj["hmi"].setdefault("layout", {})
    layout["start"] = "v_site"
    layout["navigation"] = {"type": "block", "bkcolor": "#0a1622",
                            "items": [
                                {"text": "Campus Overview", "view": "v_site", "link": "", "image": "", "icon": "hub", "permission": 0},
                                {"text": "Solar Plant", "view": "v_power_plant", "link": "", "image": "", "icon": "solar_power", "permission": 0},
                                {"text": "Electrical One-Line", "view": "v_pp_oneline", "link": "", "image": "", "icon": "electrical_services", "permission": 0},
                                {"text": "BESS Detail", "view": "v_pp_bess", "link": "", "image": "", "icon": "battery_charging_full", "permission": 0},
                                {"text": "Heat Substation", "view": "v_heat_station", "link": "", "image": "", "icon": "device_thermostat", "permission": 0},
                            ]}

    existing = {c["id"]: c for c in prj.get("charts", [])}
    for c in build_charts():
        existing[c["id"]] = c
    prj["charts"] = list(existing.values())

    r = requests.post(f"{FUXA}/api/project", json=prj, timeout=30)
    print("POST /api/project ->", r.status_code, r.text[:200])
    print(f"devices={list(prj['devices'])} views={[v['id'] for v in views]} charts={[c['id'] for c in prj['charts']]}")


if __name__ == "__main__":
    main()
