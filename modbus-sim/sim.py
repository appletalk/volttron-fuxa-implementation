"""
District-heating substation digital twin -- Modbus TCP device + control API.

This replaces the toy sensor sim with a COUPLED process model so changes
cascade realistically: pump speed -> flow -> delivered heat -> return temp;
a pump trip drops flow and trips an alarm; a makeup-pump fault bleeds system
pressure until the low-pressure alarm fires; losing the primary heat main
makes the secondary loop unable to hold its supply setpoint. Everything moves
through real Modbus registers, so it shows up in VOLTTRON, the bridge, the MCP
and the FUXA dashboard.

Two servers share one asyncio loop:
  - Modbus TCP  :5020   the field device VOLTTRON polls
  - Control API :5021   scenarios / faults / setpoints (driven by the MCP)

Register map (unit/slave id 1). VOLTTRON reads read-only analog from INPUT
registers (FC4), writable analog from HOLDING (FC3), read-only bits from
DISCRETE INPUTS (FC2). So: computed measurements -> input regs, operator
setpoints/commands -> holding regs, alarms -> discrete inputs.
"""

import asyncio
import logging

from aiohttp import web
from pymodbus.datastore import (
    ModbusSequentialDataBlock,
    ModbusServerContext,
    ModbusSlaveContext,
)
from pymodbus.server import StartAsyncTcpServer

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s heat-sim %(levelname)s %(message)s"
)
log = logging.getLogger("heat-sim")

FC_DISCRETE, FC_HOLDING, FC_INPUT = 2, 3, 4

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
HEAT_K = 0.007         # GJ/h per (m3/h * degC) -- tuned so dT ~15-25 and heat tracks demand

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
    """The coupled process model. Reads holding (control) regs, writes input
    (measurement) regs and discrete (alarm) bits every tick."""

    def __init__(self, context):
        self.ctx = context
        self.faults = {"circ_pump1": False, "circ_pump2": False,
                       "makeup_pump": False, "primary": False}
        # continuous state (with inertia)
        self.s = {
            "primary_supply_temp": 90.0, "primary_return_temp": 60.0,
            "secondary_supply_temp": 70.0, "secondary_return_temp": 50.0,
            "secondary_flow": 0.0, "instant_heat": 0.0,
            "secondary_supply_pressure": 600.0, "secondary_return_pressure": 400.0,
            "makeup_tank_level": 78.0,
            "circ_pump1_hz": 0.0, "circ_pump2_hz": 0.0, "makeup_pump_hz": 0.0,
        }

    def _slave(self):
        return self.ctx[1]

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
        # tank slowly drains while makeup is faulted/off
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

        # --- write measurements to input regs ---
        sl = self._slave()
        out = {**{k: s[k] for k in s}, **statuses}
        for name, idx in INPUTS.items():
            sl.setValues(FC_INPUT, idx, [max(0, int(round(out[name])))])
        # --- write alarm bits to discrete inputs ---
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


def build_context():
    blocks = lambda n: ModbusSequentialDataBlock(0, [0] * n)
    ctx = ModbusServerContext(slaves={1: ModbusSlaveContext(
        di=blocks(32), co=blocks(32), hr=blocks(32), ir=blocks(32), zero_mode=True)},
        single=False)
    # seed holding defaults (operator setpoints)
    slave = ctx[1]
    for name, (idx, default) in HOLDING.items():
        slave.setValues(FC_HOLDING, idx, [default])
    return ctx


async def run_plant(plant):
    while True:
        try:
            plant.step()
        except Exception:
            log.exception("plant step failed")
        await asyncio.sleep(1)


def make_app(plant):
    app = web.Application()

    async def state(_):
        return web.json_response(plant.state())

    async def scenario(req):
        name = (await req.json()).get("name")
        if name not in SCENARIOS:
            return web.json_response({"error": f"unknown scenario '{name}'",
                                      "scenarios": list(SCENARIOS)}, status=400)
        plant.apply_scenario(name)
        log.info("scenario '%s' applied", name)
        return web.json_response({"ok": True, "scenario": name, "state": plant.state()})

    async def fault(req):
        b = await req.json()
        name, on = b.get("name"), bool(b.get("on", True))
        if name not in plant.faults:
            return web.json_response({"error": f"unknown fault '{name}'",
                                      "faults": list(plant.faults)}, status=400)
        plant.faults[name] = on
        return web.json_response({"ok": True, "fault": name, "on": on, "state": plant.state()})

    async def point(req):
        b = await req.json()
        name, value = b.get("name"), b.get("value")
        if name not in HOLDING:
            return web.json_response({"error": f"unknown control '{name}'",
                                      "controls": list(HOLDING)}, status=400)
        plant.set_hold(name, value)
        return web.json_response({"ok": True, "name": name, "value": int(value)})

    async def reset(_):
        plant.apply_scenario("normal")
        return web.json_response({"ok": True, "state": plant.state()})

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
    plant = Plant(ctx)
    asyncio.create_task(run_plant(plant))

    runner = web.AppRunner(make_app(plant))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 5021).start()
    log.info("control API on 0.0.0.0:5021")

    log.info("Modbus TCP server on 0.0.0.0:5020 (heat substation, unit id 1)")
    await StartAsyncTcpServer(context=ctx, address=("0.0.0.0", 5020))


if __name__ == "__main__":
    asyncio.run(main())
