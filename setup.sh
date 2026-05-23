#!/bin/bash
# setup.sh - interactive one-shot installer for dbus-evcc-multi + dbus-vrm-tunnel.
#
# Run ONCE, by hand, as root, with a TTY:
#     ssh -t root@<gx-device> /data/dbus-evcc-multi/setup.sh
#
# Installs both daemontools services (via the unchanged, boot-safe component
# install.sh scripts), auto-detects + migrates a legacy dbus-evcc-lp1 install if
# present, asks for the EVCC host and optionally enables the VRM tunnel, then
# starts the services. Re-run any time to reconfigure (prompts default to the
# current config). NOT registered in rc.local - the component install.sh scripts
# handle boot persistence non-interactively.
set -euo pipefail

SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &>/dev/null && pwd )
CONFIG="$SCRIPT_DIR/config.ini"
TUNNEL_DIR="$SCRIPT_DIR/dbus-vrm-tunnel"

die() { echo "ERROR: $*" >&2; exit 1; }

# --- 0. Preamble ---------------------------------------------------------
[ "$(id -u)" = "0" ] || die "Please run as root."
[ -t 0 ] || die "No interactive terminal. Run as: ssh -t root@<gx-device> $SCRIPT_DIR/setup.sh"
{ [ -f "$SCRIPT_DIR/install.sh" ] && [ -f "$TUNNEL_DIR/install.sh" ]; } \
    || die "Expected install.sh + dbus-vrm-tunnel/install.sh in $SCRIPT_DIR (did you extract the tarball into /data?)."

echo "=== dbus-evcc-multi setup ==="
echo "This will:"
echo "  1. install the bridge + VRM tunnel as services"
echo "  2. detect and migrate an existing legacy lp1 install (with a prompt)"
echo "  3. ask for the EVCC host"
echo "  4. optionally enable the VRM tunnel"
echo "  5. start the services"
echo
printf "Continue? [Y/n] "; read -r ans
case "${ans:-Y}" in [nN]*) echo "Aborted."; exit 0 ;; esac

# --- 1. Install both components (idempotent) -----------------------------
echo; echo "--- 1. Installing components ---"
"$SCRIPT_DIR/install.sh"
"$TUNNEL_DIR/install.sh"

# --- 2. Legacy migration (auto-detect) -----------------------------------
echo; echo "--- 2. lp1 migration ---"
# shellcheck disable=SC2010,SC2012
legacy=$(ls -d /data/dbus-evcc-* 2>/dev/null | grep -v '/dbus-evcc-multi$' || true)
if [ -n "$legacy" ]; then
    echo "Found legacy install(s):"
    while IFS= read -r line; do echo "  $line"; done <<< "$legacy"
    echo "Starting interactive migration (confirm the mapping)..."
    python3 "$SCRIPT_DIR/migrate_from_lp.py" \
        || echo "WARNING: migration reported an error - please check the output above."
    printf "Uninstall the old lp1 services now? [y/N] "; read -r ans
    case "${ans:-N}" in
        [yY]*) python3 "$SCRIPT_DIR/migrate_from_lp.py" --uninstall-old --auto \
                   || echo "WARNING: uninstalling the old services reported an error." ;;
    esac
else
    echo "No legacy lp1 install found - skipped."
fi

# --- 3. EVCC host --------------------------------------------------------
echo; echo "--- 3. EVCC host ---"
cur_host=$(PYTHONPATH="$SCRIPT_DIR" CONFIG_PATH="$CONFIG" python3 - <<'PY' 2>/dev/null || true
import os
from pathlib import Path
from cli import read_config, resolve_settings
print(resolve_settings(read_config(Path(os.environ["CONFIG_PATH"]))).host)
PY
)
printf "EVCC host:port [%s]: " "${cur_host:-e.g. 192.168.1.50:7070}"
read -r host
host="${host:-$cur_host}"
[ -n "$host" ] || die "No EVCC host given."
python3 "$SCRIPT_DIR/setup_config.py" --config "$CONFIG" set-host "$host"
echo "ONPREMISE/Host = $host set."

# --- 4. VRM tunnel (optional) --------------------------------------------
echo; echo "--- 4. VRM tunnel (Control panel button) ---"
printf "Enable the VRM tunnel? [y/N] "; read -r ans
case "${ans:-N}" in [yY]*) tunnel=yes ;; *) tunnel=no ;; esac
if [ "$tunnel" = yes ]; then
    lan=$(ip route get 1.1.1.1 2>/dev/null \
          | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}' || true)
    while :; do
        printf "AdvertiseIp (LAN IP, NOT 127.x) [%s]: " "${lan:-}"
        read -r aip; aip="${aip:-$lan}"
        printf "EvccTarget [127.0.0.1:7070]: "; read -r tgt; tgt="${tgt:-127.0.0.1:7070}"
        printf "ProxyPort [8099]: "; read -r pp; pp="${pp:-8099}"
        if python3 "$SCRIPT_DIR/setup_config.py" --config "$CONFIG" set-tunnel \
               --enabled true --advertise-ip "$aip" \
               --evcc-target "$tgt" --proxy-port "$pp"; then
            echo "VRM tunnel enabled (AdvertiseIp=$aip)."
            break
        fi
        echo "Invalid input - please try again."
    done
else
    python3 "$SCRIPT_DIR/setup_config.py" --config "$CONFIG" set-tunnel --enabled false
    echo "VRM tunnel disabled."
fi

# --- 5. Start ------------------------------------------------------------
echo; echo "--- 5. Starting services ---"
rm -f "$SCRIPT_DIR/service/down" "$TUNNEL_DIR/service/down"
# Bring up if down, then restart so a re-run/upgrade picks up the new config.ini
# (svc -u is a no-op on an already-running service; svc -t forces the reload).
svc -u /service/dbus-evcc-multi /service/dbus-vrm-tunnel \
    || echo "WARNING: 'svc -u' failed - please check the /service symlinks."
svc -t /service/dbus-evcc-multi /service/dbus-vrm-tunnel \
    || echo "WARNING: 'svc -t' (restart) failed."
sleep 2

# --- 6. Status -----------------------------------------------------------
echo; echo "--- 6. Status ---"
svstat /service/dbus-evcc-multi /service/dbus-vrm-tunnel || true
echo
echo "Logs:"
echo "  tail -F /data/log/dbus-evcc-multi/current | tai64nlocal"
echo "  tail -F /data/log/dbus-vrm-tunnel/current | tai64nlocal"
echo
echo "Done."
exit 0
