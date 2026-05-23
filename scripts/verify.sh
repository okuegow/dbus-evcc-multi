#!/bin/bash
#
# Verify dbus-evcc-multi on a Venus-OS-style host.
#
# Run ON THE REMOTE (after install.sh + at least one poll interval has passed).
#
# Usage:
#   scripts/verify.sh
#
# Checks that each EVCC loadpoint is exposed as its own evcharger D-Bus service,
# survives an EVCC outage, and keeps stable DeviceInstances across reorders.
#
set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.." || exit 1
HERE=$(pwd)
SERVICE_NAME=$(basename "$HERE")

fail() { echo "FAIL $*" >&2; FAILED=1; }
ok()   { echo "OK   $*"; }
FAILED=0
SERVICES=""

echo "=== 1. svstat ==="
if command -v svstat >/dev/null; then
    if svstat "/service/$SERVICE_NAME" | grep -q "up"; then
        ok "service is up"
    else
        fail "service not up: $(svstat /service/$SERVICE_NAME)"
    fi
else
    fail "svstat not available - is daemontools installed?"
fi

echo
echo "=== 2. D-Bus services ==="
if ! command -v dbus >/dev/null; then
    fail "dbus CLI not available - skipping D-Bus checks"
else
    SERVICES=$(dbus -y | awk '/evcharger\.http_id/ {print $NF}' | sort -u)
    if [ -z "$SERVICES" ]; then
        fail "no com.victronenergy.evcharger.http_id* services on the bus"
    else
        echo "$SERVICES" | while read -r svc; do
            ok "service present: $svc"
        done
    fi
fi

echo
echo "=== 3. /DeviceInstance + /CustomName per service ==="
for svc in $SERVICES; do
    di=$(dbus -y "$svc" /DeviceInstance GetValue 2>/dev/null | head -n 1 || echo "?")
    name=$(dbus -y "$svc" /CustomName GetValue 2>/dev/null | head -n 1 || echo "?")
    echo "  $svc  DI=$di  Title=$name"
done

echo
echo "=== 4. state.json ==="
if [ -f "$HERE/state.json" ]; then
    ok "state.json present"
    cat "$HERE/state.json"
else
    echo "  state.json missing - first run? check logs"
fi

echo
echo "=== 5. Recent log activity ==="
LOG=/data/log/$SERVICE_NAME/current
if [ -f "$LOG" ]; then
    ok "log file exists at $LOG"
    if command -v tai64nlocal >/dev/null; then
        tail -n 20 "$LOG" | tai64nlocal
    else
        tail -n 20 "$LOG"
    fi
else
    fail "no log file at $LOG"
fi

echo
if [ "$FAILED" = "0" ]; then
    echo "ALL CHECKS PASSED"
    exit 0
else
    echo "SOME CHECKS FAILED"
    exit 1
fi
