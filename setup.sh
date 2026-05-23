#!/bin/bash
# setup.sh - interactive one-shot installer for dbus-evcc-multi + dbus-vrm-tunnel.
#
# Run ONCE, by hand, as root, with a TTY:
#     ssh -t root@<cerbo> /data/dbus-evcc-multi/setup.sh
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

die() { echo "FEHLER: $*" >&2; exit 1; }

# --- 0. Preamble ---------------------------------------------------------
[ "$(id -u)" = "0" ] || die "Bitte als root ausfuehren."
[ -t 0 ] || die "Kein interaktives Terminal. Aufruf: ssh -t root@<cerbo> $SCRIPT_DIR/setup.sh"
{ [ -f "$SCRIPT_DIR/install.sh" ] && [ -f "$TUNNEL_DIR/install.sh" ]; } \
    || die "Erwarte install.sh + dbus-vrm-tunnel/install.sh in $SCRIPT_DIR (Tar nach /data entpackt?)."

echo "=== dbus-evcc-multi Setup ==="
echo "Dies wird:"
echo "  1. Bridge + VRM-Tunnel als Dienste installieren"
echo "  2. eine vorhandene lp1-Altinstallation erkennen + migrieren (mit Rueckfrage)"
echo "  3. den EVCC-Host abfragen"
echo "  4. optional den VRM-Tunnel aktivieren"
echo "  5. die Dienste starten"
echo
printf "Fortfahren? [J/n] "; read -r ans
case "${ans:-J}" in [nN]*) echo "Abgebrochen."; exit 0 ;; esac

# --- 1. Install both components (idempotent) -----------------------------
echo; echo "--- 1. Komponenten installieren ---"
"$SCRIPT_DIR/install.sh"
"$TUNNEL_DIR/install.sh"

# --- 2. Legacy migration (auto-detect) -----------------------------------
echo; echo "--- 2. lp1-Migration ---"
# shellcheck disable=SC2010,SC2012
legacy=$(ls -d /data/dbus-evcc-* 2>/dev/null | grep -v '/dbus-evcc-multi$' || true)
if [ -n "$legacy" ]; then
    echo "Gefundene Alt-Installation(en):"
    while IFS= read -r line; do echo "  $line"; done <<< "$legacy"
    echo "Starte interaktive Migration (Mapping bestaetigen)..."
    python3 "$SCRIPT_DIR/migrate_from_lp.py" \
        || echo "WARNUNG: Migration meldete einen Fehler - bitte Ausgabe oben pruefen."
    printf "Alte lp1-Dienste jetzt deinstallieren? [j/N] "; read -r ans
    case "${ans:-N}" in
        [jJyY]*) python3 "$SCRIPT_DIR/migrate_from_lp.py" --uninstall-old --auto \
                     || echo "WARNUNG: Deinstallation alter Dienste meldete einen Fehler." ;;
    esac
else
    echo "Keine lp1-Altinstallation gefunden - uebersprungen."
fi

# --- 3. EVCC host --------------------------------------------------------
echo; echo "--- 3. EVCC-Host ---"
cur_host=$(PYTHONPATH="$SCRIPT_DIR" CONFIG_PATH="$CONFIG" python3 - <<'PY' 2>/dev/null || true
import os
from pathlib import Path
from cli import read_config, resolve_settings
print(resolve_settings(read_config(Path(os.environ["CONFIG_PATH"]))).host)
PY
)
printf "EVCC-Host:Port [%s]: " "${cur_host:-z.B. 192.168.1.50:7070}"
read -r host
host="${host:-$cur_host}"
[ -n "$host" ] || die "Kein EVCC-Host angegeben."
python3 "$SCRIPT_DIR/setup_config.py" --config "$CONFIG" set-host "$host"
echo "ONPREMISE/Host = $host gesetzt."

# --- 4. VRM tunnel (optional) --------------------------------------------
echo; echo "--- 4. VRM-Tunnel (Bedienfeld-Button) ---"
printf "VRM-Tunnel aktivieren? [j/N] "; read -r ans
case "${ans:-N}" in [jJyY]*) tunnel=yes ;; *) tunnel=no ;; esac
if [ "$tunnel" = yes ]; then
    lan=$(ip route get 1.1.1.1 2>/dev/null \
          | awk '{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}' || true)
    while :; do
        printf "AdvertiseIp (LAN-IP, KEINE 127.x) [%s]: " "${lan:-}"
        read -r aip; aip="${aip:-$lan}"
        printf "EvccTarget [127.0.0.1:7070]: "; read -r tgt; tgt="${tgt:-127.0.0.1:7070}"
        printf "ProxyPort [8099]: "; read -r pp; pp="${pp:-8099}"
        if python3 "$SCRIPT_DIR/setup_config.py" --config "$CONFIG" set-tunnel \
               --enabled true --advertise-ip "$aip" \
               --evcc-target "$tgt" --proxy-port "$pp"; then
            echo "VRM-Tunnel aktiviert (AdvertiseIp=$aip)."
            break
        fi
        echo "Ungueltige Eingabe - bitte erneut."
    done
else
    python3 "$SCRIPT_DIR/setup_config.py" --config "$CONFIG" set-tunnel --enabled false
    echo "VRM-Tunnel deaktiviert."
fi

# --- 5. Start ------------------------------------------------------------
echo; echo "--- 5. Dienste starten ---"
rm -f "$SCRIPT_DIR/service/down" "$TUNNEL_DIR/service/down"
# Bring up if down, then restart so a re-run/upgrade picks up the new config.ini
# (svc -u is a no-op on an already-running service; svc -t forces the reload).
svc -u /service/dbus-evcc-multi /service/dbus-vrm-tunnel \
    || echo "WARNUNG: 'svc -u' fehlgeschlagen - bitte /service-Symlinks pruefen."
svc -t /service/dbus-evcc-multi /service/dbus-vrm-tunnel \
    || echo "WARNUNG: 'svc -t' (Neustart) fehlgeschlagen."
sleep 2

# --- 6. Status -----------------------------------------------------------
echo; echo "--- 6. Status ---"
svstat /service/dbus-evcc-multi /service/dbus-vrm-tunnel || true
echo
echo "Logs:"
echo "  tail -F /data/log/dbus-evcc-multi/current | tai64nlocal"
echo "  tail -F /data/log/dbus-vrm-tunnel/current | tai64nlocal"
echo
echo "Fertig."
exit 0
