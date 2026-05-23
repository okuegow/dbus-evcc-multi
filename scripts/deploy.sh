#!/bin/bash
#
# Deploy the dbus-evcc-multi tar.gz to a Venus OS (Cerbo GX) device over SSH.
#
# Usage:
#   scripts/deploy.sh <user>@<host> [<remote-data-dir>]
#
# Default remote-data-dir is /data (Venus-OS convention). On a vanilla
# Raspberry Pi without Venus userland, override to /opt/dbus-evcc or
# similar and skip install.sh (it expects /service + /data/log/* paths).
#
# This script does NOT run install.sh on the remote, on purpose. After scp+tar,
# log in manually, edit config.ini (set ONPREMISE/Host), optionally seed,
# then run install.sh by hand. That keeps the human in the loop for the first
# install on an unfamiliar host.
#
set -euo pipefail

if [ $# -lt 1 ]; then
    echo "Usage: $0 <user>@<host> [<remote-data-dir>]" >&2
    exit 2
fi

REMOTE="$1"
REMOTE_DIR="${2:-/data}"

HERE=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
TARBALL=$(ls -1t "$HERE"/../dist/dbus-evcc-multi-v*.tar.gz 2>/dev/null | head -1 || true)

if [ -z "$TARBALL" ]; then
    echo "No tar.gz found under $HERE/../dist/. Build it first:" >&2
    echo "  $HERE/build-release.sh" >&2
    echo "(ships dbus-evcc-multi incl. the dbus-vrm-tunnel subdir; excludes tests/venv/pyc/down)" >&2
    exit 1
fi

echo "Deploying: $TARBALL"
echo "  to:       $REMOTE:$REMOTE_DIR/"
echo

scp "$TARBALL" "$REMOTE:/tmp/dbus-evcc-multi-deploy.tar.gz"
ssh "$REMOTE" "tar xzf /tmp/dbus-evcc-multi-deploy.tar.gz -C '$REMOTE_DIR/' && \
    ls -la '$REMOTE_DIR/dbus-evcc-multi/' && \
    rm -f /tmp/dbus-evcc-multi-deploy.tar.gz"

cat <<EOF

Deployed to $REMOTE:$REMOTE_DIR/dbus-evcc-multi/.

Recommended (guided, interactive - needs a TTY):
  ssh -t $REMOTE '$REMOTE_DIR/dbus-evcc-multi/setup.sh'

Manual alternative:
  1. ssh $REMOTE 'vi $REMOTE_DIR/dbus-evcc-multi/config.ini'   # set ONPREMISE/Host
  2. (optional, migration) ssh $REMOTE 'python3 $REMOTE_DIR/dbus-evcc-multi/migrate_from_lp.py'
  3. ssh $REMOTE '$REMOTE_DIR/dbus-evcc-multi/install.sh'
  3b. (optional tunnel) ssh $REMOTE '$REMOTE_DIR/dbus-evcc-multi/dbus-vrm-tunnel/install.sh'
  4. ssh $REMOTE 'tail -F /data/log/dbus-evcc-multi/current | tai64nlocal'
EOF
