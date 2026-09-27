#!/bin/bash
# Build the release tarball. dbus-vrm-tunnel lives inside dbus-evcc-multi/, so
# one `tar dbus-evcc-multi` ships both services. A single extract under /data
# lays down /data/dbus-evcc-multi/ with dbus-vrm-tunnel/ inside it (the tunnel
# reads ../config.ini = the bridge's shared config).
set -euo pipefail

REPO=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)   # dbus-evcc-multi
PARENT=$(dirname "$REPO")                                     # EVCC-Cerbo
VER=$(cat "$REPO/version")
OUT="$PARENT/dist/dbus-evcc-multi-$VER.tar.gz"

EXCLUDES=(
  --exclude='*/tests' --exclude='*/.venv' --exclude='*/__pycache__'
  --exclude='*/state.json' --exclude='*/.git' --exclude='*/.gitignore'
  --exclude='*/.pytest_cache' --exclude='.DS_Store' --exclude='*/service/down'
  # .installed marks a device where install.sh already ran; shipping it
  # would make a fresh install skip the guarded first-install path.
  --exclude='*/.installed'
)

mkdir -p "$PARENT/dist"
cd "$PARENT"
# Build into a temp file and only rename it once every check passed, so a
# rejected tarball never sits in dist/ where deploy.sh would pick it up.
TMP="$OUT.tmp"
trap 'rm -f "$TMP"' EXIT
# COPYFILE_DISABLE + --no-xattrs: no macOS metadata (._ files, xattr headers
# that GNU/BusyBox tar reports as "Ignoring unknown extended header keyword").
# config.ini is the operator's file and must never ship (see install.sh).
COPYFILE_DISABLE=1 tar czf "$TMP" --no-xattrs --no-mac-metadata "${EXCLUDES[@]}" \
    --exclude='dbus-evcc-multi/config.ini' dbus-evcc-multi

# Reads the pax headers themselves instead of grepping file contents.
python3 - "$TMP" <<'PY'
import sys, tarfile
bad = []
with tarfile.open(sys.argv[1]) as tf:
    names = tf.getnames()
    for m in tf.getmembers():
        base = m.name.rsplit("/", 1)[-1]
        if base.startswith("._") or m.name == "dbus-evcc-multi/config.ini":
            bad.append(m.name)
        keys = [k for k in m.pax_headers if "xattr" in k.lower()]
        if keys:
            bad.append("%s (%s)" % (m.name, ", ".join(keys)))
if "dbus-evcc-multi/config.ini.example" not in names:
    bad.append("config.ini.example missing")
if bad:
    sys.exit("ERROR: tarball rejected:\n  " + "\n  ".join(bad[:10]))
PY
REQUIRED='dbus-evcc-multi/(setup\.sh|setup_config\.py|install\.sh|dbus-vrm-tunnel/(dbus-vrm-tunnel\.py|vrm_tunnel\.py|service/run|install\.sh))$'
present=$(tar tzf "$TMP" | grep -Ec "$REQUIRED" || true)
if [ "$present" -ge 6 ]; then
    echo "All required files present ($present matches)."
else
    echo "ERROR: required files missing from tarball (only $present matches)" >&2
    exit 1
fi
mv "$TMP" "$OUT"
echo "Built $OUT"
