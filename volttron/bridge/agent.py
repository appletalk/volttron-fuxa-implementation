"""
fuxa-bridge: exposes the VOLTTRON message bus to FUXA over plain HTTP.

It runs as a dynamic VOLTTRON agent inside the platform container (so it reaches
the VIP bus over IPC via VOLTTRON_HOME -- no extra auth wiring), and serves a
small gevent WSGI + WebSocket API tailored to FUXA's device-connector needs:

    GET  /api/health              -> {"status","points"}
    GET  /api/points              -> {topic: {"value","ts"}, ...}  (snapshot)
    PUT  /api/points              -> body {"path","point","value"}; calls
                                     platform.driver set_point, returns written value
    WS   /ws                      -> on connect: {"type":"snapshot","points":{...}}
                                     then per scrape: {"type":"update","points":{...}}

Reads come from the `devices/#` pub/sub stream (real-time); writes go through the
platform driver's set_point RPC. The whole thing is gevent-based because VOLTTRON
runs on gevent -- an asyncio server would fight the hub.

Topic shape: VOLTTRON publishes devices/<campus>/<building>/<device>/all with a
[values, metadata] payload. We key points as "<campus>/<building>/<device>/<point>"
which is exactly the (path, point) split set_point expects.
"""

import json
import logging
import sqlite3
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs

import gevent
from gevent.pywsgi import WSGIServer
from geventwebsocket.handler import WebSocketHandler

from volttron.client.vip.agent import build_agent

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s fuxa-bridge %(levelname)s %(message)s"
)
log = logging.getLogger("fuxa-bridge")

DEVICES_PREFIX = "devices/"
ALL_SUFFIX = "/all"
DRIVER_PEER = "platform.driver"
CONFIG_PEER = "config.store"
LISTEN = ("0.0.0.0", 8080)
HISTORIAN_DB = "/home/volttron/.volttron/data/historian.sqlite"


def _coerce(s):
    """Parse a historian value_string back into a bool/int/float, else leave it."""
    if s is None:
        return None
    low = str(s).strip().lower()
    if low in ("true", "false"):
        return low == "true"
    try:
        f = float(s)
        return int(f) if f.is_integer() else f
    except (ValueError, TypeError):
        return s


class Bridge:
    def __init__(self):
        self.latest = {}      # "campus/building/device/point" -> {"value":.., "ts":..}
        self.clients = set()  # connected WebSocket objects
        # build_agent() with no args connects to the local platform using
        # VOLTTRON_HOME's address + server keys.
        self.agent = build_agent(identity="fuxa.bridge")
        self.agent.vip.pubsub.subscribe(
            "pubsub", DEVICES_PREFIX, self._on_device
        ).get(timeout=10)
        log.info("subscribed to %s# on the VIP bus", DEVICES_PREFIX)

    # --- VOLTTRON bus -> cache + push ---------------------------------------
    def _on_device(self, peer, sender, bus, topic, headers, message):
        # Only the aggregate "/all" publish carries every point at once.
        if not topic.endswith(ALL_SUFFIX):
            return
        base = topic[len(DEVICES_PREFIX):-len(ALL_SUFFIX)]  # campus/building/device
        values = message[0] if isinstance(message, (list, tuple)) else message
        ts = headers.get("TimeStamp") or headers.get("Date")
        updates = {}
        for point, value in values.items():
            key = base + "/" + point
            self.latest[key] = {"value": value, "ts": ts}
            updates[key] = value
        if updates:
            self._broadcast({"type": "update", "ts": ts, "points": updates})

    def _broadcast(self, obj):
        data = json.dumps(obj)
        for ws in list(self.clients):
            try:
                ws.send(data)
            except Exception:
                self.clients.discard(ws)

    # --- writes -> platform driver RPC --------------------------------------
    def set_point(self, path, point, value):
        return self.agent.vip.rpc.call(
            DRIVER_PEER, "set_point", path, point, value
        ).get(timeout=15)

    # --- gateway: history / devices / platform ------------------------------
    def history(self, point, minutes, limit):
        """Time series for a point from the sqlite historian (newest first)."""
        cutoff = (datetime.now(timezone.utc) - timedelta(minutes=minutes)).isoformat()
        con = sqlite3.connect(f"file:{HISTORIAN_DB}?mode=ro", uri=True)
        try:
            cur = con.execute(
                "SELECT d.ts, d.value_string FROM data d "
                "JOIN topics t ON t.topic_id = d.topic_id "
                "WHERE t.topic_name = ? AND d.ts >= ? "
                "ORDER BY d.ts DESC LIMIT ?",
                (point, cutoff, limit),
            )
            return [{"ts": ts, "value": _coerce(v)} for ts, v in cur.fetchall()]
        finally:
            con.close()

    def devices(self):
        """Group the live point cache by device path -> {point: value}."""
        out = {}
        for key, entry in self.latest.items():
            idx = key.rfind("/")
            path, point = key[:idx], key[idx + 1:]
            out.setdefault(path, {})[point] = entry.get("value")
        return out

    def platform(self):
        """Health summary: agents on the bus, point/device counts, freshness."""
        try:
            agents = sorted(self.agent.vip.peerlist().get(timeout=5))
        except Exception as e:  # noqa: BLE001 - report rather than crash
            agents = []
            log.warning("peerlist failed: %s", e)
        last = max((e.get("ts") or "" for e in self.latest.values()), default=None)
        return {
            "bridge_connected": True,
            "agents": agents,
            "point_count": len(self.latest),
            "device_count": len(self.devices()),
            "last_update": last or None,
            "ws_clients": len(self.clients),
        }

    # --- WSGI / WebSocket ----------------------------------------------------
    def wsgi(self, environ, start_response):
        path = environ.get("PATH_INFO", "")
        method = environ.get("REQUEST_METHOD", "GET")
        ws = environ.get("wsgi.websocket")

        if ws is not None and path == "/ws":
            return self._serve_ws(ws)
        if path == "/api/health":
            return self._json(start_response, {"status": "ok", "points": len(self.latest)})
        if path == "/api/points" and method == "GET":
            return self._json(start_response, self.latest)
        if path == "/api/points" and method in ("PUT", "POST"):
            return self._write(environ, start_response)
        if path == "/api/devices" and method == "GET":
            return self._json(start_response, self.devices())
        if path == "/api/platform" and method == "GET":
            return self._json(start_response, self.platform())
        if path == "/api/history" and method == "GET":
            return self._history(environ, start_response)
        start_response("404 Not Found", [("Content-Type", "text/plain")])
        return [b"not found"]

    def _history(self, environ, start_response):
        qs = parse_qs(environ.get("QUERY_STRING", ""))
        point = (qs.get("point") or [None])[0]
        if not point:
            start_response("400 Bad Request", [("Content-Type", "application/json")])
            return [json.dumps({"error": "missing 'point' query param"}).encode()]
        minutes = int((qs.get("minutes") or ["60"])[0])
        limit = int((qs.get("limit") or ["1000"])[0])
        try:
            samples = self.history(point, minutes, limit)
            return self._json(start_response, {
                "point": point, "minutes": minutes, "count": len(samples), "samples": samples,
            })
        except Exception as e:  # noqa: BLE001
            log.exception("history query failed")
            start_response("500 Internal Server Error", [("Content-Type", "application/json")])
            return [json.dumps({"error": str(e)}).encode()]

    def _serve_ws(self, ws):
        self.clients.add(ws)
        try:
            ws.send(json.dumps({"type": "snapshot", "points": self.latest}))
            while not ws.closed:
                # FUXA reads are push-only; we just hold the socket open and
                # drain any inbound frames (keepalives) until it closes.
                if ws.receive() is None:
                    break
        finally:
            self.clients.discard(ws)
        return []

    def _write(self, environ, start_response):
        try:
            size = int(environ.get("CONTENT_LENGTH") or 0)
            req = json.loads(environ["wsgi.input"].read(size) or b"{}")
            result = self.set_point(req["path"], req["point"], req["value"])
            return self._json(start_response, {
                "ok": True, "path": req["path"], "point": req["point"], "value": result,
            })
        except Exception as e:
            log.exception("write failed")
            start_response("400 Bad Request", [("Content-Type", "application/json")])
            return [json.dumps({"ok": False, "error": str(e)}).encode()]

    @staticmethod
    def _json(start_response, obj):
        start_response("200 OK", [("Content-Type", "application/json")])
        return [json.dumps(obj).encode()]


def main():
    bridge = Bridge()
    server = WSGIServer(LISTEN, bridge.wsgi, handler_class=WebSocketHandler, log=None)
    log.info("fuxa-bridge HTTP + WebSocket listening on %s:%d", *LISTEN)
    server.serve_forever()


if __name__ == "__main__":
    main()
