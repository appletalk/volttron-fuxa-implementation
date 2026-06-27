"""
Modbus TCP simulator + control API for the VOLTTRON + FUXA dev environment.

Two servers in one asyncio loop:
  - Modbus TCP  on :5020  -- the field device VOLTTRON polls
  - Control API on :5021  -- scenario / fault injection so Claude (via the MCP)
                            can drive faults into sensors and watch them
                            propagate through VOLTTRON -> bridge -> FUXA.

Sensors wander on their own; a "hold" pins a sensor to a fixed value (the wander
task stops touching it), which is how faults are injected. Everything flows
through real Modbus, so a fault here shows up everywhere downstream.

Register map (unit/slave id 1):
    IR[0] temperature  x10  read-only, wanders   (FC4)
    IR[1] humidity     x10  read-only, wanders   (FC4)
    IR[2] flow_rate    x10  read-only, wanders   (FC4)
    HR[0] pump_speed   raw  writable, holds       (FC3)
    HR[1] valve_open   raw  writable, holds       (FC3)
    HR[2] run_command  0/1  writable, holds       (FC3)
    CO[0] pump_enable  0/1  writable, holds       (FC1)
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
    level=logging.INFO, format="%(asctime)s modbus-sim %(levelname)s %(message)s"
)
log = logging.getLogger("modbus-sim")

FC_INPUT_REG = 4  # pymodbus datastore selector for input registers

# Wandering sensors: name -> (input-register index, low, high)
SENSORS = {
    "temperature": (0, 200, 230),   # x10 -> 20.0..23.0 C
    "humidity": (1, 400, 600),      # x10 -> 40.0..60.0 %
    "flow_rate": (2, 0, 1000),      # x10
}

# Named scenarios: name -> {sensor: held_value}. "normal" clears all holds.
SCENARIOS = {
    "normal": {},
    "overheat": {"temperature": 320},          # 32.0 C, well above normal band
    "sensor_failure": {"flow_rate": 0},        # flow sensor reads dead-zero
    "humidity_spike": {"humidity": 850},       # 85.0 %
    "frozen_plant": {"temperature": 50, "flow_rate": 0},  # 5.0 C + no flow
}


class ControlState:
    """Shared state between the control API and the wander task."""

    def __init__(self, context):
        self.context = context
        self.holds = {}   # sensor name -> pinned value (wander skips it)

    def _slave(self):
        return self.context[1]

    def read_sensor(self, name):
        idx = SENSORS[name][0]
        return self._slave().getValues(FC_INPUT_REG, idx, count=1)[0]

    def hold(self, name, value):
        self.holds[name] = int(value)
        self._slave().setValues(FC_INPUT_REG, SENSORS[name][0], [int(value)])

    def release(self, name):
        self.holds.pop(name, None)

    def reset(self):
        self.holds.clear()

    def state(self):
        return {
            "sensors": {n: self.read_sensor(n) for n in SENSORS},
            "holds": dict(self.holds),
            "scenarios": list(SCENARIOS),
        }


def build_context() -> ModbusServerContext:
    inputs = ModbusSequentialDataBlock(0, [215, 480, 500] + [0] * 50)   # IR (FC4)
    holding = ModbusSequentialDataBlock(0, [0, 0, 0] + [0] * 50)        # HR (FC3)
    coils = ModbusSequentialDataBlock(0, [0] * 16)                      # CO (FC1)
    discrete = ModbusSequentialDataBlock(0, [0] * 16)                   # DI (FC2)
    slave = ModbusSlaveContext(
        di=discrete, co=coils, hr=holding, ir=inputs, zero_mode=True
    )
    return ModbusServerContext(slaves={1: slave}, single=False)


async def wander(state: ControlState) -> None:
    """Move un-held sensors with a slow triangle wave; held sensors stay pinned."""
    slave = state.context[1]
    phase = 0
    while True:
        phase = (phase + 1) % 1000
        for name, (idx, lo, hi) in SENSORS.items():
            if name in state.holds:
                continue  # injected fault / pinned value
            span = hi - lo
            t = (phase * (idx + 1)) % (2 * span)
            val = lo + (t if t <= span else 2 * span - t)
            slave.setValues(FC_INPUT_REG, idx, [val])
        await asyncio.sleep(1)


# --- control API ------------------------------------------------------------
def make_app(state: ControlState) -> web.Application:
    app = web.Application()

    async def get_state(_req):
        return web.json_response(state.state())

    async def post_fault(req):
        body = await req.json()
        name = body.get("name")
        kind = (body.get("kind") or "stuck").lower()
        if name not in SENSORS:
            return web.json_response({"error": f"unknown sensor '{name}'", "sensors": list(SENSORS)}, status=400)
        idx, lo, hi = SENSORS[name]
        if kind == "clear":
            state.release(name)
        elif kind == "stuck":
            state.hold(name, state.read_sensor(name))
        elif kind == "high":
            state.hold(name, hi)
        elif kind == "low":
            state.hold(name, lo)
        elif kind == "zero":
            state.hold(name, 0)
        else:
            return web.json_response({"error": f"unknown kind '{kind}'",
                                      "kinds": ["stuck", "high", "low", "zero", "clear"]}, status=400)
        return web.json_response({"ok": True, "name": name, "kind": kind, "state": state.state()})

    async def post_point(req):
        body = await req.json()
        name, value = body.get("name"), body.get("value")
        hold = bool(body.get("hold", True))
        if name not in SENSORS:
            return web.json_response({"error": f"unknown sensor '{name}'"}, status=400)
        if hold:
            state.hold(name, value)
        else:
            state.context[1].setValues(FC_INPUT_REG, SENSORS[name][0], [int(value)])
        return web.json_response({"ok": True, "name": name, "value": int(value), "held": hold})

    async def post_scenario(req):
        body = await req.json()
        name = body.get("name")
        if name not in SCENARIOS:
            return web.json_response({"error": f"unknown scenario '{name}'", "scenarios": list(SCENARIOS)}, status=400)
        state.reset()
        for sensor, value in SCENARIOS[name].items():
            state.hold(sensor, value)
        log.info("loaded scenario '%s'", name)
        return web.json_response({"ok": True, "scenario": name, "state": state.state()})

    async def post_reset(_req):
        state.reset()
        return web.json_response({"ok": True, "state": state.state()})

    app.add_routes([
        web.get("/api/sim/state", get_state),
        web.post("/api/sim/fault", post_fault),
        web.post("/api/sim/point", post_point),
        web.post("/api/sim/scenario", post_scenario),
        web.post("/api/sim/reset", post_reset),
    ])
    return app


async def main() -> None:
    context = build_context()
    state = ControlState(context)
    asyncio.create_task(wander(state))

    runner = web.AppRunner(make_app(state))
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", 5021).start()
    log.info("control API on 0.0.0.0:5021")

    log.info("starting Modbus TCP server on 0.0.0.0:5020 (unit id 1)")
    await StartAsyncTcpServer(context=context, address=("0.0.0.0", 5020))


if __name__ == "__main__":
    asyncio.run(main())
