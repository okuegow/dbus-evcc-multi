#!/usr/bin/env python3
"""Seed state.json with known {title:DeviceInstance} mappings.

Use case: migrating an existing dbus-evcc-lp1 deployment where DI=56
is already in use (and bound to VRM history). Run ONCE before first
start of dbus-evcc-multi.

Usage:
    python3 seed_state.py "Heizstab:56" "Wallbox:49"

Idempotent: existing entries are preserved.
"""
import json
import sys
from pathlib import Path

from state_store import StateStore


def parse_pairs(args):
    pairs = {}
    for arg in args:
        if ":" not in arg:
            raise ValueError("Bad arg %r - expected 'Title:DI'" % arg)
        title, di_str = arg.rsplit(":", 1)
        title = title.strip()
        if not title:
            raise ValueError("Bad arg %r - empty title" % arg)
        try:
            di = int(di_str)
        except ValueError:
            raise ValueError("Bad arg %r - DI must be integer" % arg)
        pairs[title] = di
    return pairs


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    here = Path(__file__).resolve().parent
    state_path = here / "state.json"
    try:
        pairs = parse_pairs(argv[1:])
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2
    store = StateStore(state_path, di_range=(40, 59))
    store.seed(pairs)
    print("Seeded %d entries into %s" % (len(pairs), state_path))
    print(json.dumps(pairs, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
