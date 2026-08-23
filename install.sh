#!/bin/bash
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
SERVICE_NAME=$(basename "$SCRIPT_DIR")

set -e

# Permissions
chmod 755 "$SCRIPT_DIR/restart.sh" "$SCRIPT_DIR/uninstall.sh"
chmod 755 "$SCRIPT_DIR/service/run" "$SCRIPT_DIR/service/log/run"

# Ensure log directory exists (multilog needs it writable)
mkdir -p "/data/log/$SERVICE_NAME"

# Symlink into daemontools. On the FIRST install we drop a 'down' marker so
# supervise does not auto-start the bridge before the operator has run
# seed_state.py (otherwise fresh DeviceInstances get allocated and the legacy
# title->DI mapping is lost).
#
# /service is a tmpfs, so after a reboot the symlink is gone and rc.local runs
# this script again. That must NOT look like a first install - it used to drop
# the 'down' marker again and the bridge stayed off until someone noticed the
# chargers missing in VRM. The marker file below survives the reboot and tells
# the two cases apart.
INSTALL_MARKER="$SCRIPT_DIR/.installed"
if [ ! -L "/service/$SERVICE_NAME" ]; then
    if [ -f "$INSTALL_MARKER" ]; then
        rm -f "$SCRIPT_DIR/service/down"
        ln -s "$SCRIPT_DIR/service" "/service/$SERVICE_NAME"
        echo "Re-created /service/$SERVICE_NAME after reboot (service will start)"
        FIRST_INSTALL=0
    else
        touch "$SCRIPT_DIR/service/down"
        ln -s "$SCRIPT_DIR/service" "/service/$SERVICE_NAME"
        echo "Created /service/$SERVICE_NAME (in 'down' state - not running yet)"
        FIRST_INSTALL=1
    fi
else
    echo "Service symlink already exists, skipping"
    FIRST_INSTALL=0
fi
touch "$INSTALL_MARKER"

# Persist across firmware updates via rc.local
filename=/data/rc.local
if [ ! -f "$filename" ]; then
    touch "$filename"
    echo "#!/bin/bash" >> "$filename"
    echo >> "$filename"
fi
# Venus only runs /data/rc.local when it is executable
# (/etc/init.d/custom-rc-late.sh: `if [ -x /data/rc.local ]`). An existing but
# non-executable file is the silent way to lose the bridge after a reboot, so
# fix the mode on every install, not just when we create the file.
chmod 755 "$filename"
grep -qxF "$SCRIPT_DIR/install.sh" "$filename" || echo "$SCRIPT_DIR/install.sh" >> "$filename"

# The legacy single-loadpoint bridges leave their own line in rc.local; their
# uninstall.sh does not remove it. Left there, they come back on the next
# reboot, grab the DeviceInstances we just took over, and our preflight then
# refuses to start - the chargers vanish from VRM until someone looks.
leftovers=$(grep -E "^/data/dbus-evcc-[^/]+/install\.sh$" "$filename" \
    | grep -v "^$SCRIPT_DIR/install.sh$" || true)
if [ -n "$leftovers" ]; then
    echo
    echo "WARNING: $filename still starts other dbus-evcc bridges at boot:"
    echo "$leftovers" | while read -r line; do echo "    $line"; done
    echo "  They will re-register their DeviceInstances after a reboot and"
    echo "  collide with this bridge. Remove those lines once you are done"
    echo "  migrating."
fi

if [ "$FIRST_INSTALL" = "1" ]; then
    echo "Install complete (service is DOWN, will NOT auto-start)."
    echo
    echo "Next steps:"
    echo "  1. Edit $SCRIPT_DIR/config.ini and set ONPREMISE/Host = <evcc-ip>:7070"
    echo "  2. If migrating from dbus-evcc-lp1: seed state.json BEFORE first start"
    echo "       python3 $SCRIPT_DIR/seed_state.py \"HeatingElement:56\""
    echo "  3. Activate the service (clears the 'down' flag and starts it):"
    echo "       rm $SCRIPT_DIR/service/down && svc -u /service/$SERVICE_NAME"
    echo "  4. tail -F /data/log/$SERVICE_NAME/current | tai64nlocal"
else
    echo "Install complete (re-install detected, service state unchanged)."
fi
