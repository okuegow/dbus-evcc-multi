#!/bin/bash
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
SERVICE_NAME=$(basename "$SCRIPT_DIR")

# Stop the service (SIGTERM -> run_tunnel removes its iptables DNAT rule).
if [ -L "/service/$SERVICE_NAME" ]; then
    svc -d "/service/$SERVICE_NAME" 2>/dev/null
    svc -d "/service/$SERVICE_NAME/log" 2>/dev/null
    rm -f "/service/$SERVICE_NAME"
    echo "Removed /service/$SERVICE_NAME"
fi

# Drop the rc.local persistence line.
filename=/data/rc.local
if [ -f "$filename" ]; then
    grep -vxF "$SCRIPT_DIR/install.sh" "$filename" > "$filename.tmp" && mv "$filename.tmp" "$filename"
fi

echo "Uninstall complete. (The shared config.ini is left untouched; set"
echo "[VRM_TUNNEL] Enabled=false and restart dbus-evcc-multi to fully revert.)"
