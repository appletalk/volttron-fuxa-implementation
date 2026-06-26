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
| `server/runtime/devices/volttron/index.js` | new connector (reads via bridge WS, writes via REST, `browse()` lists points) |
| `server/runtime/devices/device.js` | register the connector: `require`, `create()` dispatch, `DeviceEnum.Volttron`, `loadPlugin`, browse dispatch |
| `client/src/app/_models/device.ts` | add `Volttron` to the client `DeviceType` enum |
| `client/.../device-map.component.ts` | `Volttron` in the device-type selector |
| `client/.../device-property.component.html` | `Volttron` property form with bridge-URL address field |
| `client/.../tag-property.service.ts` | `scanTagsVolttron()` point browser (reuses the scan dialog) |
| `client/.../device-list.component.ts` | route "add tag" to the point browser |

## Status — complete and verified

Verified end-to-end in the FUXA UI: the `Volttron` device type is selectable,
the point browser lists all 7 VOLTTRON points (correct inferred types), tags
import, live reads stream into the editor, and a write (`valve_open=77`)
round-trips through the connector to the device. The `fuxa` service in
`../docker-compose.yml` builds the fork locally (`build: ../FUXA`).
