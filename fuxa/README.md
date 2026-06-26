# FUXA integration

The FUXA-side work lives in a **fork** of
[frangoteam/FUXA](https://github.com/frangoteam/FUXA), not in this repo:

> **[appletalk/FUXA](https://github.com/appletalk/FUXA) — branch `volttron-connector`**

## What the fork adds

A new `Volttron` device type whose connector talks to the
[`fuxa-bridge`](../volttron/bridge/agent.py): real-time reads over the bridge
WebSocket (`/ws`), writes via `PUT /api/points` (→ `platform.driver` `set_point`).

- **Device property** `address` = bridge base URL, e.g. `http://volttron:8080`.
- **Tag address** = a VOLTTRON point key `"<campus>/<building>/<device>/<point>"`,
  e.g. `campus/building/modbus_sim/temperature`; on write it is split on the
  last `/` into `(path, point)` for `set_point`.

## Changes in the fork (branch `volttron-connector`)

| File | Change |
|---|---|
| `server/runtime/devices/volttron/index.js` | new connector (the integration logic) |
| `server/runtime/devices/device.js` | register the connector: `require`, `create()` dispatch, `DeviceEnum.Volttron`, `loadPlugin` |
| `client/src/app/_models/device.ts` | add `Volttron` to the client `DeviceType` enum |

## Status

Server-side connector is complete and syntax-checked. Remaining: wire the
`Volttron` type into the Angular client UI (type selector + property form with
the `address` field + manual tag-address entry) across `device-list`,
`device-property`, `device-map`, `device-tag-selection`, then a client rebuild.
After that, point the `fuxa` service in `../docker-compose.yml` at a local build
of the fork instead of `frangoteam/fuxa:latest`.
