"""
Two coupled digital twins on one Modbus TCP device + per-plant control API.

  - SLAVE 1  district-heating substation  (the proven reference build)
  - SLAVE 2  "Sunfield Solar" 100 MWac utility-scale PV + DC-coupled BESS plant

Both run a 1 Hz coupled physics model so changes cascade realistically all the
way to the FUXA dashboard. The solar model is the build-ready spec in
docs/POWERPLANT_SPEC.md (v2.0, review-reconciled): NOCT cell temp -> temperature
derate -> DC power -> inverter conversion with hard CLIPPING at the AC rating ->
DC-coupled BESS ramp-smoothing dispatch (charges from clipping at peak,
discharges to firm a passing cloud) -> plant export/curtailment -> MVA-limited
reactive D-curve -> POI voltage/PF -> ride-through. Integer-native registers
(offsets for signed values) so everything reads cleanly through VOLTTRON+FUXA.

Two servers share one asyncio loop:
  - Modbus TCP  :5020   the field device VOLTTRON polls (slaves 1 and 2)
  - Control API :5021   per-plant scenarios / faults / setpoints (driven by MCP)
                        /api/sim/<plant>/{state,scenario,fault,point,reset}
                        <plant> in {heat_station, power_plant}
                        (legacy flat /api/sim/* aliases -> heat_station)

Register map per slave. VOLTTRON reads read-only analog from INPUT registers
(FC4), writable analog from HOLDING (FC3), read-only bits from DISCRETE INPUTS
(FC2). So: computed measurements -> input regs, operator setpoints/commands ->
holding regs, alarms -> discrete inputs.
"""

import asyncio
import logging
import math
import random

from aiohttp import web
from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusServerContext,
    ModbusSlaveContext,
)
from pymodbus.server import StartAsyncTcpServer

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s plant-sim %(levelname)s %(message)s"
)
log = logging.getLogger("plant-sim")

FC_DISCRETE, FC_HOLDING, FC_INPUT = 2, 3, 4


def clamp(x, lo, hi):
    return lo if x < lo else hi if x > hi else x


def lag(cur, target, alpha):
    return cur + (target - cur) * alpha


# ===========================================================================
# SLAVE 1 -- district-heating substation (proven reference; logic unchanged)
# ===========================================================================

# Measured / computed outputs -> INPUT registers (read-only). index, units.
INPUTS = {
    "primary_supply_temp": 0,     # degC
    "primary_return_temp": 1,     # degC
    "secondary_supply_temp": 2,   # degC
    "secondary_return_temp": 3,   # degC
    "secondary_flow": 4,          # m3/h
    "instant_heat": 5,            # GJ/h
    "secondary_supply_pressure": 6,  # kPa
    "secondary_return_pressure": 7,  # kPa
    "makeup_tank_level": 8,       # %
    "circ_pump1_hz": 9,           # Hz
    "circ_pump2_hz": 10,          # Hz
    "makeup_pump_hz": 11,         # Hz
    "circ_pump1_status": 12,      # 0 off / 1 run / 2 fault
    "circ_pump2_status": 13,
    "makeup_pump_status": 14,
}

# Operator setpoints / commands -> HOLDING registers (writable). index, default.
HOLDING = {
    "circ_pump1_cmd": (0, 1),       # 0/1 start
    "circ_pump1_hz_sp": (1, 42),    # Hz setpoint
    "circ_pump2_cmd": (2, 0),       # standby
    "circ_pump2_hz_sp": (3, 42),
    "makeup_pump_cmd": (4, 1),
    "supply_setpoint": (5, 70),     # secondary supply degC target
    "building_load": (6, 55),       # % heat demand
}

# Alarms / status -> DISCRETE INPUTS (read-only bits). index.
DISCRETE = {
    "circ_pump1_fault": 0,
    "circ_pump2_fault": 1,
    "makeup_vfd_fault": 2,
    "low_pressure_alarm": 3,
    "high_supply_temp_alarm": 4,
}

PUMP_MAX_FLOW = 60.0   # m3/h per circulation pump at 50 Hz
HEAT_K = 0.007         # GJ/h per (m3/h * degC)

# Scenarios: control overrides + fault flags. Applied over the defaults.
SCENARIOS = {
    "normal": {"holding": {"circ_pump1_cmd": 1, "circ_pump2_cmd": 0, "building_load": 55,
                           "supply_setpoint": 70}, "faults": {}},
    "morning_startup": {"holding": {"circ_pump1_cmd": 1, "circ_pump2_cmd": 1,
                                    "circ_pump1_hz_sp": 35, "circ_pump2_hz_sp": 35,
                                    "building_load": 70, "supply_setpoint": 72}, "faults": {}},
    "peak_load": {"holding": {"circ_pump1_cmd": 1, "circ_pump2_cmd": 1,
                              "circ_pump1_hz_sp": 48, "circ_pump2_hz_sp": 48,
                              "building_load": 95, "supply_setpoint": 75}, "faults": {}},
    "circ_pump_trip": {"holding": {"circ_pump1_cmd": 1, "circ_pump2_cmd": 0,
                                   "building_load": 80}, "faults": {"circ_pump1": True}},
    "makeup_vfd_fault": {"holding": {}, "faults": {"makeup_pump": True}},
    "loss_of_primary": {"holding": {"building_load": 70}, "faults": {"primary": True}},
}


class Plant:
    """The coupled heating-substation process model (Modbus slave 1)."""

    SLAVE_ID = 1

    def __init__(self, context):
        self.ctx = context
        self.faults = {"circ_pump1": False, "circ_pump2": False,
                       "makeup_pump": False, "primary": False}
        self.s = {
            "primary_supply_temp": 90.0, "primary_return_temp": 60.0,
            "secondary_supply_temp": 70.0, "secondary_return_temp": 50.0,
            "secondary_flow": 0.0, "instant_heat": 0.0,
            "secondary_supply_pressure": 600.0, "secondary_return_pressure": 400.0,
            "makeup_tank_level": 78.0,
            "circ_pump1_hz": 0.0, "circ_pump2_hz": 0.0, "makeup_pump_hz": 0.0,
        }

    def _slave(self):
        return self.ctx[self.SLAVE_ID]

    def _read_h(self, name):
        return self._slave().getValues(FC_HOLDING, HOLDING[name][0], count=1)[0]

    def set_hold(self, name, value):
        self._slave().setValues(FC_HOLDING, HOLDING[name][0], [int(value)])

    @staticmethod
    def _lag(cur, target, alpha):
        return cur + (target - cur) * alpha

    def step(self):
        s = self.s
        h = {k: self._read_h(k) for k in HOLDING}

        # --- pumps: command + fault -> Hz feedback ---
        for n in ("circ_pump1", "circ_pump2"):
            running = h[f"{n}_cmd"] and not self.faults[n]
            target_hz = h[f"{n}_hz_sp"] if running else 0
            s[f"{n}_hz"] = self._lag(s[f"{n}_hz"], target_hz, 0.4)
        mu_running = h["makeup_pump_cmd"] and not self.faults["makeup_pump"]
        s["makeup_pump_hz"] = self._lag(s["makeup_pump_hz"], 45 if mu_running else 0, 0.4)

        # --- secondary circulation flow from running pumps ---
        flow_target = (s["circ_pump1_hz"] / 50.0) * PUMP_MAX_FLOW \
            + (s["circ_pump2_hz"] / 50.0) * PUMP_MAX_FLOW
        s["secondary_flow"] = self._lag(s["secondary_flow"], flow_target, 0.3)

        # --- primary main (lost in loss_of_primary) ---
        prim_target = 40.0 if self.faults["primary"] else 90.0
        s["primary_supply_temp"] = self._lag(s["primary_supply_temp"], prim_target, 0.15)

        # --- secondary supply temp tracks setpoint, capped by available primary ---
        achievable = max(20.0, s["primary_supply_temp"] - 5.0)
        sup_target = min(h["supply_setpoint"], achievable)
        s["secondary_supply_temp"] = self._lag(s["secondary_supply_temp"], sup_target, 0.2)

        # --- delivered heat sets the return temp (energy balance) ---
        demand = (h["building_load"] / 100.0) * 12.0   # GJ/h max ~12
        flow = max(s["secondary_flow"], 0.1)
        dT_possible = demand / (flow * HEAT_K) if flow > 0.5 else 60.0
        dT = min(dT_possible, max(0.0, s["secondary_supply_temp"] - 25.0))
        ret_target = s["secondary_supply_temp"] - dT
        s["secondary_return_temp"] = self._lag(s["secondary_return_temp"], ret_target, 0.25)
        s["instant_heat"] = self._lag(
            s["instant_heat"], flow * dT * HEAT_K if s["secondary_flow"] > 1 else 0.0, 0.3)

        # --- primary return mirrors heat drawn ---
        s["primary_return_temp"] = self._lag(
            s["primary_return_temp"], s["primary_supply_temp"] - dT * 0.9, 0.2)

        # --- system pressure: makeup holds it up; fault/off bleeds it down ---
        press_target = 600.0 if mu_running else 200.0
        s["secondary_supply_pressure"] = self._lag(
            s["secondary_supply_pressure"], press_target, 0.15)
        s["secondary_return_pressure"] = s["secondary_supply_pressure"] - 180.0
        lvl_target = 78.0 if mu_running else 30.0
        s["makeup_tank_level"] = self._lag(s["makeup_tank_level"], lvl_target, 0.05)

        # --- statuses + alarms ---
        def pump_status(n):
            if self.faults[n]:
                return 2
            return 1 if s[f"{n}_hz"] > 1 else 0
        statuses = {
            "circ_pump1_status": pump_status("circ_pump1"),
            "circ_pump2_status": pump_status("circ_pump2"),
            "makeup_pump_status": 2 if self.faults["makeup_pump"] else (1 if mu_running else 0),
        }
        low_press = s["secondary_supply_pressure"] < 350.0
        high_temp = s["secondary_supply_temp"] > h["supply_setpoint"] + 5

        sl = self._slave()
        out = {**{k: s[k] for k in s}, **statuses}
        for name, idx in INPUTS.items():
            sl.setValues(FC_INPUT, idx, [max(0, int(round(out[name])))])
        alarms = {
            "circ_pump1_fault": self.faults["circ_pump1"],
            "circ_pump2_fault": self.faults["circ_pump2"],
            "makeup_vfd_fault": self.faults["makeup_pump"],
            "low_pressure_alarm": low_press,
            "high_supply_temp_alarm": high_temp,
        }
        for name, idx in DISCRETE.items():
            sl.setValues(FC_DISCRETE, idx, [bool(alarms[name])])

    def state(self):
        sl = self._slave()
        meas = {n: sl.getValues(FC_INPUT, i, count=1)[0] for n, i in INPUTS.items()}
        ctrl = {n: sl.getValues(FC_HOLDING, i, count=1)[0] for n, (i, _) in HOLDING.items()}
        alarms = {n: bool(sl.getValues(FC_DISCRETE, i, count=1)[0]) for n, i in DISCRETE.items()}
        return {"measurements": meas, "controls": ctrl, "alarms": alarms,
                "faults": dict(self.faults), "scenarios": list(SCENARIOS)}

    def apply_scenario(self, name):
        sc = SCENARIOS[name]
        self.faults = {k: False for k in self.faults}
        self.faults.update(sc.get("faults", {}))
        for k, v in sc.get("holding", {}).items():
            self.set_hold(k, v)


# ===========================================================================
# SLAVE 2 -- Sunfield Solar: 100 MWac PV + DC-coupled BESS  (spec v2.0)
# ===========================================================================

# Measured / computed outputs -> INPUT registers (read-only). index per spec 2a.
PV_INPUTS = {
    "plant_active_power_mw": 0,       # MW         x1
    "plant_reactive_power_mvar": 1,   # MVAR       +50 offset (17..83 = -33..+33)
    "poi_voltage_kv": 2,              # kV         x10 (1150 = 115.0)
    "grid_frequency_hz": 3,           # Hz         x10 (600 = 60.0)
    "power_factor": 4,                # -          x100 (98 = 0.98)
    "poi_current_a": 5,               # A          x1
    "main_breaker_status": 6,         # 0 open / 1 closed
    "irradiance_wm2": 7,              # W/m2       x1  (the fuel / driver)
    "pv_dc_power_mw": 8,              # MW         x1  (PV DC only, after derate)
    "inverter_ac_power_mw": 9,        # MW         x1  (PV + DC battery; clips at 100)
    "clipping_loss_mw": 10,           # MW         x1
    "inverter_efficiency_pct": 11,    # %          x1
    "ambient_temp_c": 12,             # degC       x1
    "panel_temp_c": 13,               # degC       x1
    "tracker_angle_deg": 14,          # deg        +60 offset (0..120 = -60..+60)
    "performance_ratio_pct": 15,      # %          x1
    "inverter1_status": 16,           # 0 off / 1 run / 2 fault
    "inverter2_status": 17,
    "inverter3_status": 18,
    "inverter4_status": 19,
    "battery_soc_pct": 20,            # %          x1
    "battery_power_mw": 21,           # MW         +50 offset (25..75 = -25..+25; + disch / - chg)
    "battery_temp_c": 22,             # degC       x1
    "bess_status": 23,                # 0 idle / 1 discharge / 2 charge / 3 fault
    "daily_energy_mwh": 24,           # MWh        x1 (demo-scaled)
    "clock_hour": 25,                 # h          local time of day, hour 0-23
    "clock_min_tens": 26,             # -          minute tens digit 0-5 (for a 2-digit display)
    "clock_min_ones": 27,             # -          minute ones digit 0-9
    # --- campus microgrid accounting: the plant supplies a campus bus ---
    "campus_base_load_mw": 28,        # MW         campus electrical base load (buildings)
    "substation_load_mw": 29,         # MW         district-heating substation pump load (from slave 1)
    "campus_load_mw": 30,             # MW         total campus load = base + substation
    "grid_power_mw": 31,              # MW         +100 offset; >100 export to grid / <100 import
    "solar_to_load_pct": 32,          # %          share of campus load met by the solar plant
}

# Operator setpoints / commands -> HOLDING registers (writable). RAW stored value
# (what FUXA buttons + MCP write); engineering meaning in the comment. index, default.
PV_HOLDING = {
    "power_setpoint_mw": (0, 100),     # MW export cap / curtailment
    "mvar_setpoint": (1, 50),          # +50 offset -> default 0 MVAR
    "voltage_setpoint_kv": (2, 1150),  # x10 -> 115.0 kV
    "bess_mode": (3, 0),               # 0 auto / 1 force-charge / 2 force-discharge
    "bess_power_cmd_mw": (4, 50),      # +50 offset -> default 0 MW (force modes only)
    "breaker_cmd": (5, 1),             # 0 open / 1 close
    "tracker_enable": (6, 1),          # 0 stow / 1 track
    "time_rate": (7, 1),               # day clock speed: 0 pause / 1 play / N fast-forward
    "time_set_hhmm": (8, 9999),        # operator: write HHMM (0000-2359) to jump the day clock;
}                                      # 9999 = no-op sentinel so 0000 = midnight is settable

# Decode raw holding register -> engineering value for the physics.
PV_HOLD_DECODE = {
    "power_setpoint_mw": lambda v: float(v),
    "mvar_setpoint": lambda v: float(v) - 50.0,
    "voltage_setpoint_kv": lambda v: float(v) / 10.0,
    "bess_mode": lambda v: int(v),
    "bess_power_cmd_mw": lambda v: float(v) - 50.0,
    "breaker_cmd": lambda v: int(v),
    "tracker_enable": lambda v: int(v),
    "time_rate": lambda v: int(v),
    "time_set_hhmm": lambda v: int(v),
}

# Alarms / status -> DISCRETE INPUTS (read-only bits). index per spec 2c.
PV_DISCRETE = {
    "inverter_fault": 0,
    "grid_over_voltage": 1,
    "grid_under_frequency": 2,
    "breaker_trip": 3,
    "battery_over_temp": 4,
    "low_soc": 5,
    "dc_ground_fault": 6,
    "comms_loss": 7,
    "curtailment_active": 8,
}

# --- validated constants (PVWatts / NREL-grade), spec section 3 ---
P_AC_RATED = 100.0    # MW
P_DC_STC = 125.0      # MW  (ILR 1.25)
N_INV = 4             # 25 MWac / 31.25 MWdc blocks
ETA_INV = 0.985
GAMMA_P = -0.0037     # /degC  (-0.37 %/degC)
NOCT_K = 0.03125      # (NOCT-20)/800, NOCT=45
E_CAP = 100.0         # MWh
P_BATT_MAX = 25.0     # MW (0.25C)
SOC_LO, SOC_HI = 10.0, 90.0
ETA_OW = 0.938        # one-way = sqrt(RTE 0.88)
S_MAX = 105.3         # MVA (inverter headroom + 25 MVA BESS PCS)
Q_MAX = 33.0          # MVAR (0.95 PF at 100 MW)
V_NOM = 115.0         # kV
KV_PER_MVAR = 0.06    # kV/MVAR grid stiffness
F_NOM = 60.0          # Hz
DT = 1.0              # s
K_DECAY = 0.11        # firming-envelope decay (tau ~9 s): fast enough to TRACK the slow diurnal
                      # sunset (so the battery doesn't drain firming it) yet hold a fast passing cloud
SOC_TC = 60.0         # demo time-lapse for SOC + energy: 1 sim-s shows 60 s of real battery
                      # dynamics, so SOC/MWh visibly move in a ~5-min demo day. The underlying
                      # RATE is real (25 MW -> 3 h); divide by SOC_TC to recover it.
TH_GAIN = 0.002       # battery thermal: steady ~ambient+5+1 at full 0.25C
TH_DECAY = 0.05
T_DAY = 300.0         # day_in_the_life day length (s, ~5 min); ramp >> cloud (30 s)
SQRT3 = math.sqrt(3.0)

# faults the solar plant understands (inject_fault keys)
PV_FAULTS = ("inverter1", "inverter2", "inverter3", "inverter4",
             "grid_under_freq", "grid_severe", "dc_ground_fault",
             "comms_loss", "battery_over_temp")

# Scenarios: holding overrides (RAW values) + fault flags + a scripted driver
# mode that evolves irradiance / temperature / tracker over time. Applied over
# the defaults; the driver mode is the scenario name unless given as "driver".
PV_SCENARIOS = {
    "normal": {"holding": {"power_setpoint_mw": 100, "mvar_setpoint": 50,
                           "bess_mode": 0, "breaker_cmd": 1, "tracker_enable": 1},
               "faults": {}},
    "day_in_the_life": {"holding": {"power_setpoint_mw": 100, "mvar_setpoint": 50,
                                    "bess_mode": 0, "breaker_cmd": 1, "tracker_enable": 1,
                                    "time_rate": 1}, "faults": {}},
    "sunrise": {"holding": {"power_setpoint_mw": 100, "bess_mode": 0,
                            "breaker_cmd": 1, "tracker_enable": 1}, "faults": {}},
    "peak_sun": {"holding": {"power_setpoint_mw": 100, "bess_mode": 0,
                             "breaker_cmd": 1}, "faults": {}},
    "cloud_passing": {"holding": {"power_setpoint_mw": 100, "bess_mode": 0,
                                  "breaker_cmd": 1}, "faults": {}},
    "curtailment": {"holding": {"power_setpoint_mw": 60, "bess_mode": 0,
                                "breaker_cmd": 1}, "faults": {}},
    "evening": {"holding": {"power_setpoint_mw": 100, "bess_mode": 0,
                            "breaker_cmd": 1, "tracker_enable": 1}, "faults": {}},
    "inverter_trip": {"holding": {"power_setpoint_mw": 100, "bess_mode": 0,
                                  "breaker_cmd": 1}, "faults": {"inverter2": True}},
    "grid_fault": {"holding": {"breaker_cmd": 1}, "faults": {"grid_under_freq": True}},
    "bess_dispatch": {"holding": {"bess_mode": 2, "bess_power_cmd_mw": 75,
                                  "breaker_cmd": 1}, "faults": {}},
    "low_soc": {"holding": {"power_setpoint_mw": 100, "bess_mode": 0,
                            "breaker_cmd": 1}, "faults": {}},
}


class SolarPlant:
    """Utility-scale PV + DC-coupled BESS plant (Modbus slave 2). Spec v2.0."""

    SLAVE_ID = 2

    def __init__(self, context):
        self.ctx = context
        self.faults = {k: False for k in PV_FAULTS}
        self.mode = "normal"
        self.clock = 0.0          # seconds since the active scenario started
        self.day_t = 0.0          # day_in_the_life day clock
        self.uf_clock = 0.0       # sustained out-of-ride-through timer
        self.trip_latched = False
        self._init_pbase = True
        self.s = {
            "irradiance_wm2": 0.0, "ambient_temp_c": 25.0, "panel_temp_c": 25.0,
            "pv_dc_power_mw": 0.0, "inverter_ac_power_mw": 0.0, "clipping_loss_mw": 0.0,
            "inverter_efficiency_pct": 98.0, "tracker_angle_deg": 0.0,
            "plant_active_power_mw": 0.0, "plant_reactive_power_mvar": 0.0,
            "poi_voltage_kv": 115.0, "grid_frequency_hz": 60.0, "power_factor": 1.0,
            "poi_current_a": 0.0, "performance_ratio_pct": 0.0,
            "battery_soc_pct": 50.0, "battery_power_mw": 0.0, "battery_temp_c": 30.0,
            "daily_energy_mwh": 0.0, "P_ref": 0.0,
            "clock_hour": 10.0, "clock_min_tens": 0.0, "clock_min_ones": 0.0,
            "campus_base_load_mw": 20.0, "substation_load_mw": 5.0, "campus_load_mw": 25.0,
            "grid_power_mw": 0.0, "solar_to_load_pct": 0.0,
        }
        self.statuses = {f"inverter{i}_status": 0 for i in range(1, 5)}
        self.statuses["bess_status"] = 0
        self.main_breaker_status = 1
        self.alarms = {k: False for k in PV_DISCRETE}

    # --- register helpers ---------------------------------------------------
    def _slave(self):
        return self.ctx[self.SLAVE_ID]

    def _read_h_raw(self, name):
        return self._slave().getValues(FC_HOLDING, PV_HOLDING[name][0], count=1)[0]

    def set_hold(self, name, value):
        """Write a RAW register value (FUXA / MCP write raw, offsets baked in)."""
        self._slave().setValues(FC_HOLDING, PV_HOLDING[name][0], [int(value)])

    # --- local time of day (minutes since midnight) -------------------------
    # day_in_the_life maps its day clock onto a real daylight arc (05:30 -> 18:30);
    # the ramp scenarios advance within that window; steady scenarios sit at a
    # representative hour so "time of day" always reads sensibly on the dashboard.
    def _time_minutes(self):
        m, t = self.mode, self.clock
        if m == "day_in_the_life":
            return (self.day_t / T_DAY) * 1440.0                     # full 24 h: 00:00 -> 24:00
        if m == "sunrise":
            return 330.0 + clamp(t / 90.0, 0.0, 1.4) * 120.0         # 05:30 -> ~07:30
        if m == "evening":
            return 1020.0 + clamp(t / 120.0, 0.0, 1.0) * 90.0        # 17:00 -> 18:30
        if m == "peak_sun":
            return 720.0                                             # 12:00
        if m == "curtailment":
            return 750.0                                             # 12:30
        if m in ("cloud_passing", "low_soc"):
            return 780.0                                             # 13:00
        if m == "bess_dispatch":
            return 1080.0                                            # 18:00 (evening grid service)
        if m in ("inverter_trip", "grid_fault"):
            return 720.0                                             # 12:00
        return 600.0                                                 # normal -> 10:00

    # --- scripted environmental driver (G, T_amb, tracker angle) ------------
    def _driver_targets(self):
        m, t = self.mode, self.clock
        if m == "day_in_the_life":
            hour = (self.day_t / T_DAY) * 24.0          # 0..24 over the full day
            if 6.0 <= hour <= 18.0:                     # daylight arc, sun up 06:00-18:00
                day = math.sin(math.pi * (hour - 6.0) / 12.0)   # 0 at 06, 1 at noon, 0 at 18
                G = 1050.0 * day
                angle = clamp(-60.0 + 120.0 * (hour - 6.0) / 12.0, -60.0, 60.0)
            else:                                       # night: dark + trackers stowed
                day, G, angle = 0.0, 0.0, 0.0
            if 12.85 <= hour <= 13.15:                  # signature passing cloud ~13:00 (~20 min)
                G = min(G, 720.0)
            T_amb = 19.0 + 14.0 * day                   # ~19 degC overnight, ~33 midday
            return G, T_amb, angle
        if m == "sunrise":
            G = clamp((t / 90.0) * 1000.0, 0.0, 1000.0)
            angle = clamp(-60.0 + (t / 90.0) * 60.0, -60.0, 0.0)
            return G, 24.0 + 4.0 * clamp(t / 180.0, 0.0, 1.0), angle
        if m == "evening":
            G = clamp(1000.0 * (1.0 - t / 120.0), 0.0, 1000.0)
            angle = clamp((t / 120.0) * 60.0, 0.0, 60.0)
            return G, 30.0 - 6.0 * clamp(t / 120.0, 0.0, 1.0), angle
        if m in ("peak_sun", "curtailment"):
            return 1050.0, 33.0, 0.0
        if m in ("cloud_passing", "low_soc"):    # deep dip 1000->300->1000 over ~38s
            if t < 5:
                G = 1000.0
            elif t < 13:
                G = 1000.0 - 700.0 * ((t - 5) / 8.0)
            elif t < 30:                          # 17 s hold so the lagged measurement settles
                G = 300.0
            elif t < 38:
                G = 300.0 + 700.0 * ((t - 30) / 8.0)
            else:
                G = 1000.0
            return G, 30.0, 0.0
        if m == "bess_dispatch":
            return 150.0, 27.0, 0.0
        if m == "inverter_trip":
            return 1000.0, 31.0, 0.0
        if m == "grid_fault":
            return 950.0, 30.0, 0.0
        return 900.0, 30.0, 0.0     # normal: steady sunny operating point

    # --- 1 Hz coupled physics ----------------------------------------------
    def step(self):
        s = self.s
        h = {k: PV_HOLD_DECODE[k](self._read_h_raw(k)) for k in PV_HOLDING}
        self.clock += DT

        # operator time-of-day control: jumping to a time (0000-2359, 9999=no-op)
        # enters the live day model; the rate (0 pause / 1 play / N fast) advances
        # the day clock continuously across a full 24 h (night included).
        ts = h["time_set_hhmm"]
        if 0 <= ts <= 2359:
            target_min = (ts // 100) * 60 + (ts % 100)
            self.day_t = clamp(T_DAY * target_min / 1440.0, 0.0, T_DAY)
            self.mode = "day_in_the_life"
            self.set_hold("time_set_hhmm", 9999)    # consume the one-shot command
            self._seed_from_driver()                # snap the physics to the new time (no glide
            h["time_set_hhmm"] = 9999               # transient: night jump -> dark immediately)
        if self.mode == "day_in_the_life":
            rate = max(0, h["time_rate"])
            self.day_t += rate * DT
            if self.day_t >= T_DAY:                  # wrap to a new day at midnight
                self.day_t -= T_DAY
                s["daily_energy_mwh"] = 0.0

        # local time of day (2-digit minute via tens/ones digits)
        tod = self._time_minutes()
        s["clock_hour"] = int(tod // 60) % 24
        mn = int(tod % 60)
        s["clock_min_tens"] = mn // 10
        s["clock_min_ones"] = mn % 10

        # (1) drivers
        G_t, Tamb_t, angle_t = self._driver_targets()
        if not h["tracker_enable"]:
            angle_t = 0.0
        s["irradiance_wm2"] = lag(s["irradiance_wm2"], clamp(G_t, 0.0, 1200.0), 0.2)
        s["ambient_temp_c"] = lag(s["ambient_temp_c"], Tamb_t, 0.1)
        s["tracker_angle_deg"] = lag(s["tracker_angle_deg"], angle_t, 0.1)
        G = s["irradiance_wm2"]
        T_amb = s["ambient_temp_c"]
        g_track = 1.0 if h["tracker_enable"] else 0.97

        # (2) grid frequency + voltage-fault offset (driver level, computed early)
        if self.faults["grid_severe"]:
            f_t, v_off = 58.0, 7.0
        elif self.faults["grid_under_freq"]:
            f_t, v_off = 59.3, 0.0
        else:
            f_t, v_off = F_NOM, 0.0
        s["grid_frequency_hz"] = lag(s["grid_frequency_hz"],
                                     f_t + random.uniform(-0.02, 0.02), 0.3)
        f_now = s["grid_frequency_hz"]

        # (3) ride-through clock + breaker trip latch (IEEE 1547-2018 / PRC-024)
        v_pu_prev = s["poi_voltage_kv"] / V_NOM
        out_of_rt = (f_now < 58.5) or (f_now > 61.2) or (v_pu_prev > 1.10)
        self.uf_clock = self.uf_clock + DT if out_of_rt else 0.0
        if self.uf_clock > 3.0:
            self.trip_latched = True
        breaker_closed = (h["breaker_cmd"] == 1) and not self.trip_latched
        self.main_breaker_status = 1 if breaker_closed else 0

        # (4) inverter statuses (need breaker) + cell temp + PV DC w/ derate
        inv_run = breaker_closed and G > 40.0
        n_ok = 0
        for i in range(1, 5):
            if self.faults[f"inverter{i}"]:
                self.statuses[f"inverter{i}_status"] = 2
            elif inv_run:
                self.statuses[f"inverter{i}_status"] = 1
                n_ok += 1
            else:
                self.statuses[f"inverter{i}_status"] = 0

        Tcell_t = T_amb + NOCT_K * G
        s["panel_temp_c"] = lag(s["panel_temp_c"], Tcell_t, 0.2)
        derate = 1.0 + GAMMA_P * (s["panel_temp_c"] - 25.0)
        gf = 0.85 if self.faults["dc_ground_fault"] else 1.0   # DC ground fault derates the array
        P_dc_pv_t = (G * g_track / 1000.0) * P_DC_STC * derate * (n_ok / N_INV) * gf
        P_dc_pv_t = max(0.0, P_dc_pv_t)
        s["pv_dc_power_mw"] = lag(s["pv_dc_power_mw"], P_dc_pv_t, 0.2)
        P_dc_pv = s["pv_dc_power_mw"]

        # (5) DC-coupled BESS dispatch (auto = clip-capture + fast-attack/slow-decay firming).
        # A firming envelope P_ref follows the sun UP instantly (so the battery sits idle on the
        # smooth diurnal ramp and PV alone climbs to clip at 100) but decays DOWN slowly (so it
        # holds the recent export level through a fast cloud -> the battery firms the dip). Above
        # the clip point the battery charges exactly the surplus the cap can't pass (clip capture).
        P_ac_pv_unclipped = P_dc_pv * ETA_INV
        if self._init_pbase:
            s["P_ref"] = P_ac_pv_unclipped
            self._init_pbase = False
        if P_ac_pv_unclipped > s["P_ref"]:
            s["P_ref"] = P_ac_pv_unclipped            # fast attack -> idle on the rising ramp
        else:
            s["P_ref"] += (P_ac_pv_unclipped - s["P_ref"]) * K_DECAY   # slow decay -> hold thru dip
        soc = s["battery_soc_pct"]
        over_temp = s["battery_temp_c"] > 45.0

        if h["bess_mode"] == 1:                       # force charge
            P_batt_dc = -P_BATT_MAX
        elif h["bess_mode"] == 2:                     # force discharge (signed cmd)
            P_batt_dc = clamp(h["bess_power_cmd_mw"], -P_BATT_MAX, P_BATT_MAX)
        else:                                         # auto: hold the firming envelope, capped
            # cap the target by the LIVE inverter AC capacity too, so losing a
            # block makes surplus PV CHARGE the battery (not discharge uselessly
            # against the reduced cap).
            P_ac_cap_now = P_AC_RATED * (n_ok / N_INV)
            firm_target = min(s["P_ref"], h["power_setpoint_mw"], P_ac_cap_now)
            P_dc_needed = firm_target / ETA_INV
            P_batt_dc = clamp(P_dc_needed - P_dc_pv, -P_BATT_MAX, P_BATT_MAX)
            # SOC state-of-charge management: in daylight, gently bias toward 50 %
            # (charge if low / discharge if high) so SOC cycles in a healthy band
            # day-to-day instead of drifting to a rail from sunset firming.
            if P_dc_pv > 5.0:
                P_batt_dc = clamp(P_batt_dc + (soc - 55.0) * 0.22, -P_BATT_MAX, P_BATT_MAX)
        if P_batt_dc > 0 and soc < 30.0:              # reserve mgmt: taper discharge toward the
            P_batt_dc *= clamp((soc - SOC_LO) / 20.0, 0.0, 1.0)   # floor so it doesn't slam to 10%
        if soc <= SOC_LO and P_batt_dc > 0:           # hard block discharge at the floor
            P_batt_dc = 0.0
        if soc >= SOC_HI and P_batt_dc < 0:           # block charge at ceiling
            P_batt_dc = 0.0
        if not breaker_closed:                        # islanded -> battery idles
            P_batt_dc = 0.0
        if over_temp:
            P_batt_dc *= 0.6
        s["battery_power_mw"] = lag(s["battery_power_mw"], P_batt_dc, 0.3)

        # (6) inverter conversion + hard clip (PV + battery share the DC bus)
        if breaker_closed:
            P_dc_net = P_dc_pv + P_batt_dc
            P_ac_cap = P_AC_RATED * (n_ok / N_INV)
            ac_unclamped = P_dc_net * ETA_INV
            inv_ac = clamp(min(ac_unclamped, P_ac_cap), 0.0, P_AC_RATED)
            clip = max(0.0, ac_unclamped - P_ac_cap)
        else:
            inv_ac, clip = 0.0, 0.0
        s["inverter_ac_power_mw"] = lag(s["inverter_ac_power_mw"], inv_ac, 0.2)
        s["clipping_loss_mw"] = lag(s["clipping_loss_mw"], clip, 0.2)
        load_frac = s["inverter_ac_power_mw"] / P_AC_RATED
        eta_pl = 98.5 if load_frac >= 0.2 else (96.0 + (98.5 - 96.0) * (load_frac / 0.2))
        s["inverter_efficiency_pct"] = clamp(eta_pl, 96.0, 99.0)

        # (7) SOC energy balance with RTE -- /3600 PRESERVED, x SOC_TC time-lapse -- + thermal.
        # Real rate (SOC_TC=1): 25 MW discharge = 0.0074 %/s -> 80% usable in 3.0 h. The SOC_TC
        # factor only speeds the DISPLAY so the swing is visible in a short demo; the 3 h is real.
        if P_batt_dc > 0:
            soc -= (P_batt_dc / ETA_OW) * DT * SOC_TC / (3600.0 * E_CAP) * 100.0
        elif P_batt_dc < 0:
            soc += (abs(P_batt_dc) * ETA_OW) * DT * SOC_TC / (3600.0 * E_CAP) * 100.0
        s["battery_soc_pct"] = clamp(soc, 0.0, 100.0)
        bt_target = self.s["battery_temp_c"]
        bt_target += TH_GAIN * abs(P_batt_dc) - TH_DECAY * (bt_target - (T_amb + 5.0))
        if self.faults["battery_over_temp"]:
            bt_target = 50.0
        s["battery_temp_c"] = bt_target

        # (8) plant export / curtailment / energy
        if breaker_closed:
            P_plant = clamp(s["inverter_ac_power_mw"], 0.0, h["power_setpoint_mw"])
        else:
            P_plant = 0.0
        s["plant_active_power_mw"] = lag(s["plant_active_power_mw"], P_plant, 0.2)
        curtailing = (s["clipping_loss_mw"] > 0.5) and \
            (soc >= SOC_HI or h["power_setpoint_mw"] < P_ac_pv_unclipped)
        s["daily_energy_mwh"] += s["plant_active_power_mw"] * DT * SOC_TC / 3600.0

        # (8b) campus microgrid: the solar+BESS plant supplies a campus bus; the
        # district-heating substation's circulation pumps are an electrical load on
        # that bus -- read in-process from slave 1, so a substation pump trip or load
        # swing shifts the plant's grid export. Net to grid = generation - campus load.
        hour = self._time_minutes() / 60.0
        shape = clamp(math.sin(math.pi * (hour - 5.5) / 13.0), 0.0, 1.0)
        base = 10.0 + 18.0 * shape                    # campus base load: 10 MW night .. 28 MW midday
        sl1 = self.ctx[1]
        hz1 = sl1.getValues(FC_INPUT, INPUTS["circ_pump1_hz"], count=1)[0]
        hz2 = sl1.getValues(FC_INPUT, INPUTS["circ_pump2_hz"], count=1)[0]
        hzm = sl1.getValues(FC_INPUT, INPUTS["makeup_pump_hz"], count=1)[0]
        sub = 3.0 * (hz1 / 50.0) ** 3 + 3.0 * (hz2 / 50.0) ** 3 + 1.0 * (hzm / 50.0) ** 3
        s["campus_base_load_mw"] = lag(s["campus_base_load_mw"], base, 0.1)
        s["substation_load_mw"] = lag(s["substation_load_mw"], sub, 0.2)
        campus_load = s["campus_base_load_mw"] + s["substation_load_mw"]
        s["campus_load_mw"] = campus_load
        s["grid_power_mw"] = lag(s["grid_power_mw"], s["plant_active_power_mw"] - campus_load, 0.2)
        s["solar_to_load_pct"] = clamp(
            s["plant_active_power_mw"] / max(campus_load, 1.0) * 100.0, 0.0, 100.0)

        # (9) reactive / voltage / PF / current / PR  (MVA-limited D-curve)
        if breaker_closed:
            Pp = s["plant_active_power_mw"]
            Q_limit = min(Q_MAX, math.sqrt(max(0.0, S_MAX ** 2 - Pp ** 2)))
            Q = clamp(h["mvar_setpoint"], -Q_limit, Q_limit)
            s["plant_reactive_power_mvar"] = lag(s["plant_reactive_power_mvar"], Q, 0.2)
            Vkv = V_NOM + KV_PER_MVAR * s["plant_reactive_power_mvar"] + v_off
            s["poi_voltage_kv"] = lag(s["poi_voltage_kv"], Vkv, 0.2)
            S = math.sqrt(Pp ** 2 + s["plant_reactive_power_mvar"] ** 2)
            s["power_factor"] = 1.0 if S < 0.5 else Pp / S
            s["poi_current_a"] = S * 1e6 / (SQRT3 * s["poi_voltage_kv"] * 1000.0) \
                if s["poi_voltage_kv"] > 1.0 else 0.0
            s["performance_ratio_pct"] = 100.0 * s["inverter_ac_power_mw"] / max((G / 1000.0) * P_DC_STC, 1.0)
        else:
            s["plant_reactive_power_mvar"] = lag(s["plant_reactive_power_mvar"], 0.0, 0.3)
            s["poi_voltage_kv"] = lag(s["poi_voltage_kv"], V_NOM + v_off, 0.2)
            s["power_factor"] = 1.0
            s["poi_current_a"] = 0.0
            s["performance_ratio_pct"] = 0.0

        # bess status
        if over_temp or self.faults["battery_over_temp"]:
            self.statuses["bess_status"] = 3
        elif s["battery_power_mw"] > 0.5:
            self.statuses["bess_status"] = 1     # discharge
        elif s["battery_power_mw"] < -0.5:
            self.statuses["bess_status"] = 2     # charge
        else:
            self.statuses["bess_status"] = 0

        # (10) alarms
        v_pu = s["poi_voltage_kv"] / V_NOM
        self.alarms = {
            "inverter_fault": any(self.statuses[f"inverter{i}_status"] == 2 for i in range(1, 5)),
            "grid_over_voltage": v_pu > 1.05,
            "grid_under_frequency": s["grid_frequency_hz"] < 59.5,
            "breaker_trip": self.trip_latched,
            "battery_over_temp": s["battery_temp_c"] > 45.0,
            "low_soc": s["battery_soc_pct"] <= 12.0,
            "dc_ground_fault": self.faults["dc_ground_fault"],
            "comms_loss": self.faults["comms_loss"],
            "curtailment_active": curtailing,
        }

        self._write_registers()

    def _encode_inputs(self):
        s = self.s
        return {
            "plant_active_power_mw": int(round(s["plant_active_power_mw"])),
            "plant_reactive_power_mvar": int(round(s["plant_reactive_power_mvar"])) + 50,
            "poi_voltage_kv": int(round(s["poi_voltage_kv"] * 10.0)),
            "grid_frequency_hz": int(round(s["grid_frequency_hz"] * 10.0)),
            "power_factor": int(round(s["power_factor"] * 100.0)),
            "poi_current_a": int(round(s["poi_current_a"])),
            "main_breaker_status": int(self.main_breaker_status),
            "irradiance_wm2": int(round(s["irradiance_wm2"])),
            "pv_dc_power_mw": int(round(s["pv_dc_power_mw"])),
            "inverter_ac_power_mw": int(round(s["inverter_ac_power_mw"])),
            "clipping_loss_mw": int(round(s["clipping_loss_mw"])),
            "inverter_efficiency_pct": int(round(s["inverter_efficiency_pct"])),
            "ambient_temp_c": int(round(s["ambient_temp_c"])),
            "panel_temp_c": int(round(s["panel_temp_c"])),
            "tracker_angle_deg": int(round(s["tracker_angle_deg"])) + 60,
            "performance_ratio_pct": int(round(clamp(s["performance_ratio_pct"], 0.0, 100.0))),
            "inverter1_status": self.statuses["inverter1_status"],
            "inverter2_status": self.statuses["inverter2_status"],
            "inverter3_status": self.statuses["inverter3_status"],
            "inverter4_status": self.statuses["inverter4_status"],
            "battery_soc_pct": int(round(s["battery_soc_pct"])),
            "battery_power_mw": int(round(s["battery_power_mw"])) + 50,
            "battery_temp_c": int(round(s["battery_temp_c"])),
            "bess_status": self.statuses["bess_status"],
            "daily_energy_mwh": int(round(s["daily_energy_mwh"])),
            "clock_hour": int(s["clock_hour"]),
            "clock_min_tens": int(s["clock_min_tens"]),
            "clock_min_ones": int(s["clock_min_ones"]),
            "campus_base_load_mw": int(round(s["campus_base_load_mw"])),
            "substation_load_mw": int(round(s["substation_load_mw"])),
            "campus_load_mw": int(round(s["campus_load_mw"])),
            "grid_power_mw": int(round(s["grid_power_mw"])) + 100,
            "solar_to_load_pct": int(round(s["solar_to_load_pct"])),
        }

    def _write_registers(self):
        sl = self._slave()
        out = self._encode_inputs()
        for name, idx in PV_INPUTS.items():
            sl.setValues(FC_INPUT, idx, [int(clamp(out[name], 0, 65535))])
        for name, idx in PV_DISCRETE.items():
            sl.setValues(FC_DISCRETE, idx, [bool(self.alarms[name])])

    # --- control API surface ------------------------------------------------
    def state(self):
        sl = self._slave()
        meas = {n: sl.getValues(FC_INPUT, i, count=1)[0] for n, i in PV_INPUTS.items()}
        ctrl = {n: sl.getValues(FC_HOLDING, i, count=1)[0] for n, (i, _) in PV_HOLDING.items()}
        alarms = {n: bool(sl.getValues(FC_DISCRETE, i, count=1)[0]) for n, i in PV_DISCRETE.items()}
        return {"measurements": meas, "controls": ctrl, "alarms": alarms,
                "faults": dict(self.faults), "mode": self.mode,
                "scenarios": list(PV_SCENARIOS)}

    def _seed_from_driver(self):
        """Warm-start the lagged environment + slow baseline to a consistent
        steady state for the scenario's t=0 conditions, so scenarios that begin
        at full sun (peak_sun etc.) pin the clip immediately instead of waiting
        out the EMA convergence, while ramp scenarios (sunrise/evening/day) that
        start at G=0 still ramp up naturally."""
        G0, T0, ang0 = self._driver_targets()
        track = self._read_h_raw("tracker_enable")
        brk = self._read_h_raw("breaker_cmd")
        self.s["irradiance_wm2"] = clamp(G0, 0.0, 1200.0)
        self.s["ambient_temp_c"] = T0
        self.s["panel_temp_c"] = T0 + NOCT_K * G0
        self.s["tracker_angle_deg"] = ang0 if track else 0.0
        g_track = 1.0 if track else 0.97
        n_ok0 = sum(1 for i in range(1, 5) if not self.faults[f"inverter{i}"]) \
            if (brk == 1 and G0 > 40.0) else 0
        derate0 = 1.0 + GAMMA_P * (self.s["panel_temp_c"] - 25.0)
        gf = 0.85 if self.faults["dc_ground_fault"] else 1.0
        dc0 = max(0.0, (G0 * g_track / 1000.0) * P_DC_STC * derate0 * (n_ok0 / N_INV) * gf)
        self.s["pv_dc_power_mw"] = dc0
        self.s["P_ref"] = dc0 * ETA_INV
        self._init_pbase = False

    def apply_scenario(self, name):
        sc = PV_SCENARIOS[name]
        self.faults = {k: False for k in PV_FAULTS}
        self.faults.update(sc.get("faults", {}))
        for k, v in sc.get("holding", {}).items():
            self.set_hold(k, v)
        self.mode = name
        self.clock = 0.0
        self.trip_latched = False
        self.uf_clock = 0.0
        self.s["daily_energy_mwh"] = 0.0
        if name == "sunrise":
            self.day_t = 0.0
        if name == "day_in_the_life":       # open at dawn (05:30) and run the full 24 h from there;
            self.day_t = T_DAY * 330.0 / 1440.0   # known mid-charge SOC so the day is repeatable
            self.s["battery_soc_pct"] = 45.0
        if name == "low_soc":               # pre-drained below the floor: discharge blocks,
            self.s["battery_soc_pct"] = 9.0     # so the next cloud sags export (negative-space proof)
        self._seed_from_driver()


def build_context():
    blocks = lambda n: ModbusSequentialDataBlock(0, [0] * n)
    ctx = ModbusServerContext(slaves={
        1: ModbusSlaveContext(di=blocks(32), co=blocks(32), hr=blocks(32),
                              ir=blocks(32), zero_mode=True),
        2: ModbusSlaveContext(di=blocks(64), co=blocks(64), hr=blocks(64),
                              ir=blocks(64), zero_mode=True),
    }, single=False)
    for name, (idx, default) in HOLDING.items():
        ctx[1].setValues(FC_HOLDING, idx, [default])
    for name, (idx, default) in PV_HOLDING.items():
        ctx[2].setValues(FC_HOLDING, idx, [default])
    return ctx


async def run_plants(plants):
    while True:
        for p in plants.values():
            try:
                p.step()
            except Exception:
                log.exception("plant step failed")
        await asyncio.sleep(1)


def make_app(plants):
    """Per-plant control API: /api/sim/<plant>/{state,scenario,fault,point,reset}.
    Plus legacy flat /api/sim/* aliases mapped to the heat station."""
    app = web.Application()

    def resolve(req):
        key = req.match_info.get("plant", "heat_station")
        plant = plants.get(key)
        if plant is None:
            return None, web.json_response(
                {"error": f"unknown plant '{key}'", "plants": list(plants)}, status=404)
        return plant, None

    async def state(req):
        plant, err = resolve(req)
        return err or web.json_response(plant.state())

    async def scenario(req):
        plant, err = resolve(req)
        if err:
            return err
        name = (await req.json()).get("name")
        scns = SCENARIOS if isinstance(plant, Plant) else PV_SCENARIOS
        if name not in scns:
            return web.json_response({"error": f"unknown scenario '{name}'",
                                      "scenarios": list(scns)}, status=400)
        plant.apply_scenario(name)
        log.info("[%s] scenario '%s'", req.match_info.get("plant", "heat_station"), name)
        return web.json_response({"ok": True, "scenario": name, "state": plant.state()})

    async def fault(req):
        plant, err = resolve(req)
        if err:
            return err
        b = await req.json()
        name, on = b.get("name"), bool(b.get("on", True))
        if name not in plant.faults:
            return web.json_response({"error": f"unknown fault '{name}'",
                                      "faults": list(plant.faults)}, status=400)
        plant.faults[name] = on
        return web.json_response({"ok": True, "fault": name, "on": on, "state": plant.state()})

    async def point(req):
        plant, err = resolve(req)
        if err:
            return err
        b = await req.json()
        name, value = b.get("name"), b.get("value")
        holds = HOLDING if isinstance(plant, Plant) else PV_HOLDING
        if name not in holds:
            return web.json_response({"error": f"unknown control '{name}'",
                                      "controls": list(holds)}, status=400)
        plant.set_hold(name, value)
        return web.json_response({"ok": True, "name": name, "value": int(value)})

    async def reset(req):
        plant, err = resolve(req)
        if err:
            return err
        plant.apply_scenario("normal")
        return web.json_response({"ok": True, "state": plant.state()})

    # per-plant routes
    app.add_routes([
        web.get("/api/sim/{plant}/state", state),
        web.post("/api/sim/{plant}/scenario", scenario),
        web.post("/api/sim/{plant}/fault", fault),
        web.post("/api/sim/{plant}/point", point),
        web.post("/api/sim/{plant}/reset", reset),
    ])
    # legacy flat aliases -> heat_station (keeps the existing MCP working
    # until it reconnects with the new per-plant `plant` argument)
    app.add_routes([
        web.get("/api/sim/state", state),
        web.post("/api/sim/scenario", scenario),
        web.post("/api/sim/fault", fault),
        web.post("/api/sim/point", point),
        web.post("/api/sim/reset", reset),
    ])
    return app


async def main():
    ctx = build_context()
    plants = {"heat_station": Plant(ctx), "power_plant": SolarPlant(ctx)}
    plants["power_plant"].apply_scenario("normal")
    asyncio.create_task(run_plants(plants))

    runner = web.AppRunner(make_app(plants))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 5021).start()
    log.info("control API on 0.0.0.0:5021 (plants: %s)", ", ".join(plants))

    log.info("Modbus TCP server on 0.0.0.0:5020 (slave 1 heat_station, slave 2 power_plant)")
    await StartAsyncTcpServer(context=ctx, address=("0.0.0.0", 5020))


if __name__ == "__main__":
    asyncio.run(main())
