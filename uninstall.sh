#!/bin/bash
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
SERVICE_NAME=$(basename "$SCRIPT_DIR")

# Uninstall must remove what it can even when individual steps fail
# (a partial install state can still benefit from a partial uninstall).
# Track failures and report them at the end so the operator sees the truth
# instead of an unconditional "Uninstalled successfully".
FAILED=0
fail()    { echo "  [FAIL] $*" >&2; FAILED=1; }
step_ok() { echo "  [OK]   $*"; }

echo "Uninstalling $SERVICE_NAME..."

# 1. Tell daemontools to stop respawning the service
if [ -L "/service/$SERVICE_NAME" ]; then
    if svc -dx "/service/$SERVICE_NAME" 2>/dev/null; then
        step_ok "svc -dx /service/$SERVICE_NAME"
    else
        fail "svc -dx /service/$SERVICE_NAME"
    fi
    if svc -dx "/service/$SERVICE_NAME/log" 2>/dev/null; then
        step_ok "svc -dx /service/$SERVICE_NAME/log"
    else
        fail "svc -dx /service/$SERVICE_NAME/log"
    fi
    if rm -f "/service/$SERVICE_NAME"; then
        step_ok "rm /service/$SERVICE_NAME"
    else
        fail "rm /service/$SERVICE_NAME"
    fi
else
    step_ok "no /service/$SERVICE_NAME symlink (already uninstalled?)"
fi

# 2. SF7: targeted kill - only PIDs whose argv path lives INSIDE $SCRIPT_DIR.
# `pkill -f "python3 .*dbus-evcc.py"` would also kill the single-LP variant
# or any other unrelated dbus-evcc.py on the system.
killed=0
for pid in $(pgrep -f "dbus-evcc.py" 2>/dev/null); do
    cmdline=$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null || true)
    case "$cmdline" in
        *"$SCRIPT_DIR"*)
            if kill "$pid" 2>/dev/null; then
                killed=$((killed + 1))
            else
                fail "could not kill stray PID $pid"
            fi
            ;;
    esac
done
step_ok "killed $killed leftover bridge process(es)"

# 3. Clear the run-bit so a stale symlink can't accidentally come back up
if chmod a-x "$SCRIPT_DIR/service/run" 2>/dev/null; then
    step_ok "chmod a-x service/run"
else
    fail "chmod a-x service/run"
fi

# 4. Remove rc.local entry (idempotent)
if [ -f /data/rc.local ]; then
    if sed -i "\|$SCRIPT_DIR/install.sh|d" /data/rc.local 2>/dev/null; then
        step_ok "removed rc.local entry"
    else
        fail "could not edit /data/rc.local"
    fi
else
    step_ok "no /data/rc.local to clean"
fi

# Reset any leftover 'down' marker so a re-install starts cleanly
rm -f "$SCRIPT_DIR/service/down"

echo
if [ "$FAILED" = "0" ]; then
    echo "Uninstalled $SERVICE_NAME. state.json and logs left intact."
    exit 0
else
    echo "Uninstall completed WITH ERRORS - see [FAIL] lines above."
    echo "state.json and logs left intact."
    exit 1
fi
