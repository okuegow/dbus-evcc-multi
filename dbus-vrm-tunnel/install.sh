#!/bin/bash
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
SERVICE_NAME=$(basename "$SCRIPT_DIR")

set -e

chmod 755 "$SCRIPT_DIR/install.sh" "$SCRIPT_DIR/uninstall.sh"
chmod 755 "$SCRIPT_DIR/service/run" "$SCRIPT_DIR/service/log/run"

mkdir -p "/data/log/$SERVICE_NAME"

# First install -> drop 'down' marker; the tunnel must NOT start until the
# operator has set [VRM_TUNNEL] Enabled=true + AdvertiseIp in the shared
# /data/dbus-evcc-multi/config.ini. On re-install (rc.local re-entry after
# reboot) the symlink already exists -> no 'down' file -> service resumes.
if [ ! -L "/service/$SERVICE_NAME" ]; then
    touch "$SCRIPT_DIR/service/down"
    ln -s "$SCRIPT_DIR/service" "/service/$SERVICE_NAME"
    echo "Created /service/$SERVICE_NAME (in 'down' state - not running yet)"
    FIRST_INSTALL=1
else
    echo "Service symlink already exists, skipping"
    FIRST_INSTALL=0
fi

# Persist across firmware updates via rc.local
filename=/data/rc.local
if [ ! -f "$filename" ]; then
    touch "$filename"
    chmod 755 "$filename"
    echo "#!/bin/bash" >> "$filename"
    echo >> "$filename"
fi
grep -qxF "$SCRIPT_DIR/install.sh" "$filename" || echo "$SCRIPT_DIR/install.sh" >> "$filename"

if [ "$FIRST_INSTALL" = "1" ]; then
    echo "Install complete (service is DOWN, will NOT auto-start)."
    echo
    echo "Next steps:"
    echo "  1. In /data/dbus-evcc-multi/config.ini set under [VRM_TUNNEL]:"
    echo "       Enabled = true"
    echo "       AdvertiseIp = <cerbo-or-evcc-host LAN IP>"
    echo "       EvccTarget  = 127.0.0.1:7070   (or <evcc-host>:7070 if remote)"
    echo "  2. Make sure dbus-evcc-multi has the SAME [VRM_TUNNEL] settings"
    echo "     (it sets /Mgmt/Connection accordingly) and restart it:"
    echo "       svc -t /service/dbus-evcc-multi"
    echo "  3. Activate the tunnel service:"
    echo "       rm $SCRIPT_DIR/service/down && svc -u /service/$SERVICE_NAME"
    echo "  4. tail -F /data/log/$SERVICE_NAME/current | tai64nlocal"
else
    echo "Install complete (re-install detected, service state unchanged)."
fi
