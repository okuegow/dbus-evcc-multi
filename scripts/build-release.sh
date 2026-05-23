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
)

mkdir -p "$PARENT/dist"
cd "$PARENT"
tar czf "$OUT" "${EXCLUDES[@]}" dbus-evcc-multi
echo "Built $OUT"
REQUIRED='dbus-evcc-multi/(setup\.sh|setup_config\.py|install\.sh|dbus-vrm-tunnel/(dbus-vrm-tunnel\.py|vrm_tunnel\.py|service/run|install\.sh))$'
present=$(tar tzf "$OUT" | grep -Ec "$REQUIRED" || true)
if [ "$present" -ge 6 ]; then
    echo "All required files present ($present matches)."
else
    echo "ERROR: required files missing from tarball (only $present matches)" >&2
    exit 1
fi
