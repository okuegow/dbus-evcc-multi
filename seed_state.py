#!/usr/bin/env python3
"""Seed state.json with known {title:DeviceInstance} mappings.

Use case: migrating an existing single-loadpoint deployment (dbus-evcc-lp1 and
friends) whose DeviceInstances are already bound to VRM history. Run ONCE
before the first start of dbus-evcc-multi.

Usage:
    python3 seed_state.py "HeatingElement:56" "Wallbox:49"
    python3 seed_state.py --adopt-counters "HeatingElement:56" "Wallbox:49"

--adopt-counters additionally reads /Ac/Energy/Forward from the legacy service
that currently holds each DeviceInstance and stores it, so the new bridge
continues that counter instead of jumping to EVCC's lifetime total. Run it
while the OLD bridges are still running; without it VRM sees a step in the
charger's energy counter.

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


def collect_counters(pairs, scan):
    """{title: energy} for the seeded DIs that a live service reports.

    scan is {DeviceInstance: ExistingCharger}; injected so this stays testable
    without a D-Bus.
    """
    adopt = {}
    missing = []
    for title, di in sorted(pairs.items()):
        charger = scan.get(di)
        if charger is None:
            missing.append((title, di))
            continue
        adopt[title] = charger.energy_forward
    return adopt, missing


def main(argv):
    args = list(argv[1:])
    adopt_counters = False
    if "--adopt-counters" in args:
        adopt_counters = True
        args.remove("--adopt-counters")
    if not args:
        print(__doc__)
        return 1

    here = Path(__file__).resolve().parent
    state_path = here / "state.json"
    try:
        pairs = parse_pairs(args)
    except ValueError as e:
        print(str(e), file=sys.stderr)
        return 2

    store = StateStore(state_path, di_range=(40, 59))
    store.seed(pairs)
    print("Seeded %d entries into %s" % (len(pairs), state_path))
    print(json.dumps(pairs, indent=2, sort_keys=True))

    if not adopt_counters:
        return 0

    import dbus  # only needed on the GX device

    from bus_scan import scan_evchargers

    scan = scan_evchargers(dbus.SystemBus())
    adopt, missing = collect_counters(pairs, scan)
    for title, di in missing:
        print("No live service on DeviceInstance %d (%s) - its counter will "
              "start at EVCC's own value" % (di, title), file=sys.stderr)
    if adopt:
        store.seed_energy(adopt)
        print("Counters to continue from:")
        for title, value in sorted(adopt.items()):
            print("  %-24s %10.3f kWh (from %s)"
                  % (title, value, scan[pairs[title]].service))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
