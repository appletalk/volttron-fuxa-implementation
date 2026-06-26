"""
Modbus TCP simulator for the VOLTTRON + FUXA dev environment.

Pretends to be a field device so the rest of the stack (VOLTTRON platform
driver -> fuxa-bridge -> FUXA) has live, writable SCADA data without any real
hardware. A background task nudges the sensor registers each second so values
move the way a real sensor feed would; setpoint registers/coils stay wherever
VOLTTRON (and ultimately FUXA) writes them.

The register placement matches how the VOLTTRON Modbus interface reads points:
read-only analog points are read from INPUT registers (FC4) and writable ones
from HOLDING registers (FC3); read-only bits from discrete inputs (FC2) and
writable bits from coils (FC1). So sensors live in input registers and
setpoints in holding registers -- otherwise the driver would read zeros.

Map (unit/slave id 1):
    IR[0]  temperature   x10  (215 -> 21.5 C)   read-only, wanders   (FC4)
    IR[1]  humidity      x10  (480 -> 48.0 %)   read-only, wanders   (FC4)
    IR[2]  flow_rate     x10                     read-only, wanders   (FC4)
    HR[0]  pump_speed    raw 0-100               writable, holds      (FC3)
    HR[1]  valve_open    raw 0-100               writable, holds      (FC3)
    HR[2]  run_command   0/1                      writable, holds      (FC3)
    CO[0]  pump_enable   0/1                      writable, holds      (FC1)
"""

import asyncio
import logging

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

# pymodbus datastore function-code selectors used with setValues():
#   3 = holding registers, 4 = input registers.
FC_INPUT_REG = 4

# Input-register indices we actively wander, with their bounds.
WANDER = {
    0: (200, 230),   # temperature x10  -> 20.0..23.0 C
    1: (400, 600),   # humidity x10     -> 40.0..60.0 %
    2: (0, 1000),    # flow_rate x10
}


def build_context() -> ModbusServerContext:
    # zero_mode=True so datastore index N maps to Modbus address N (no +1 offset).
    inputs = ModbusSequentialDataBlock(0, [215, 480, 500] + [0] * 50)   # IR (FC4)
    holding = ModbusSequentialDataBlock(0, [0, 0, 0] + [0] * 50)        # HR (FC3)
    coils = ModbusSequentialDataBlock(0, [0] * 16)                      # CO (FC1)
    discrete = ModbusSequentialDataBlock(0, [0] * 16)                   # DI (FC2)
    slave = ModbusSlaveContext(
        di=discrete, co=coils, hr=holding, ir=inputs, zero_mode=True
    )
    return ModbusServerContext(slaves={1: slave}, single=False)


async def wander(context: ModbusServerContext) -> None:
    """Deterministically nudge the sensor registers so values move on screen.

    A slow triangle wave per register (no RNG -- reproducible across restarts,
    and the harness bans Math.random anyway) is enough to see motion in a gauge.
    """
    slave = context[1]
    phase = 0
    while True:
        phase = (phase + 1) % 1000
        for idx, (lo, hi) in WANDER.items():
            span = hi - lo
            t = (phase * (idx + 1)) % (2 * span)        # 0..2*span sawtooth
            val = lo + (t if t <= span else 2 * span - t)  # fold into triangle
            slave.setValues(FC_INPUT_REG, idx, [val])
        await asyncio.sleep(1)


async def main() -> None:
    context = build_context()
    asyncio.create_task(wander(context))
    log.info("starting Modbus TCP server on 0.0.0.0:5020 (unit id 1)")
    await StartAsyncTcpServer(context=context, address=("0.0.0.0", 5020))


if __name__ == "__main__":
    asyncio.run(main())
