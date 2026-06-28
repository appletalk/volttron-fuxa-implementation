#!/usr/bin/env bash
# Boot the VOLTTRON platform, then install + configure the platform driver
# (Modbus -> modbus-sim) and the sqlite historian. Idempotent: safe to re-run
# against a persisted VOLTTRON_HOME (config stores overwrite, installs --force).
set -euo pipefail

export VOLTTRON_HOME=${VOLTTRON_HOME:-/home/volttron/.volttron}
CFG=/home/volttron/config
mkdir -p "$VOLTTRON_HOME/data"

log() { echo ">>> [entrypoint] $*"; }

# --- 1. start the platform in the background ---------------------------------
log "starting VOLTTRON platform"
volttron -vv -l "$VOLTTRON_HOME/volttron.log" &
VPID=$!

# --- 2. wait until the message bus is accepting commands ---------------------
log "waiting for platform to become ready"
ready=0
for _ in $(seq 1 90); do
    if vctl status >/dev/null 2>&1; then ready=1; break; fi
    # bail out early if the platform process died
    if ! kill -0 "$VPID" 2>/dev/null; then
        log "VOLTTRON process exited during startup; dumping log:"
        tail -n 50 "$VOLTTRON_HOME/volttron.log" || true
        exit 1
    fi
    sleep 1
done
if [ "$ready" -ne 1 ]; then
    log "platform did not become ready in time; dumping log:"
    tail -n 50 "$VOLTTRON_HOME/volttron.log" || true
    exit 1
fi
log "platform is up"

# --- 3. push driver configs into the config store ----------------------------
# Stored under the platform.driver identity; the agent loads them on start and
# live-reloads on change. CSV registry must be stored with --csv.
log "storing platform.driver config + heat-station Modbus device"
vctl config store platform.driver config "$CFG/platform-driver.config"
vctl config store platform.driver devices/campus/building/heat_station "$CFG/device.heat_station.json"
vctl config store platform.driver heat_station.csv "$CFG/heat_station.registry.csv" --csv

log "storing solar power-plant Modbus device (slave 2)"
vctl config store platform.driver devices/campus/solar/power_plant "$CFG/device.power_plant.json"
vctl config store platform.driver power_plant.csv "$CFG/power_plant.registry.csv" --csv

# --- 4. install + (re)start the agents ---------------------------------------
# Install by BARE package name (no ==version): VOLTTRON records the whole
# install string as the package id and later looks up its metadata by that
# exact string to start the agent, so a version spec makes start_agent fail.
# The correct v10 versions are already pinned + installed in this venv via
# requirements.txt, and `pip install <name>` does NOT upgrade an already
# satisfied requirement (no -U / no --force-reinstall), so pip is a no-op and
# the pre-installed 0.2.1rc2 / 0.2.1rc1 are kept. Do NOT add --force here: it
# maps to pip --force-reinstall, which re-resolves to the latest 2.0 line and
# breaks the Modbus driver's base-driver pin.
# Idempotent installs: on a container RESTART the writable layer (and thus
# VOLTTRON_HOME) persists, so a fresh `vctl install` fails with "Identity already
# exists, but not forced!" and -e would loop the container. So: if the identity
# is already installed, start it by its UUID (vctl start takes a UUID, NOT an
# identity); otherwise install + start fresh.
ensure_agent() {
    local identity="$1" pkg="$2"; shift 2
    local uuid
    uuid=$(vctl status 2>/dev/null | awk -v id="$identity" 'index($0, id) {print $1; exit}')
    if [ -n "$uuid" ]; then
        log "$identity present (uuid $uuid); starting"
        vctl start "$uuid" || true
    else
        log "installing $identity"
        vctl install "$pkg" --vip-identity "$identity" "$@" --start
    fi
}

ensure_agent platform.driver volttron-platform-driver
ensure_agent platform.historian volttron-sqlite-historian --agent-config "$CFG/historian.config"

sleep 3
log "agent status:"
vctl status || true

# --- 5. start the fuxa-bridge (devices/# pub-sub + set_point RPC -> HTTP/WS) --
log "starting fuxa-bridge on :8080"
python /home/volttron/bridge/agent.py &
BRIDGE_PID=$!

# --- 6. hand the foreground to the platform ----------------------------------
log "VOLTTRON ready; following platform process (pid $VPID)"
wait "$VPID"
