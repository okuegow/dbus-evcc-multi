#!/usr/bin/env python3
"""dbus-evcc-multi - auto-discovery EVCC bridge for Victron Venus OS.

Single-process, multi-VeDbusService per Victron community pattern.
Logging follows mvader recommendation: named logger, stdout/stderr only,
daemontools multilog handles persistence.

Pure functions live in cli.py and are unit-tested on macOS. This entry
script needs dbus + gi.repository (Linux / Venus OS).
"""
from __future__ import annotations

import sys
from pathlib import Path

from cli import (
    mgmt_connection_string,
    parse_args,
    read_config,
    resolve_settings,
    resolve_tunnel_settings,
)
from evcc_api import EvccClient
from log_setup import configure_logging
from state_store import StateStore
from sync import LoadpointSync, preflight_check_di_collisions


def main(argv=None) -> int:
    args = parse_args(argv if argv is not None else sys.argv[1:])
    logger = configure_logging(debug=args.debug)

    here = Path(__file__).resolve().parent
    config_path = Path(args.config) if args.config else here / "config.ini"
    cp = read_config(config_path)
    settings = resolve_settings(cp)
    try:
        tunnel = resolve_tunnel_settings(cp)
    except ValueError as e:
        logger.error("[VRM_TUNNEL] config invalid: %s", e)
        return 1
    mgmt_connection = mgmt_connection_string(tunnel)

    if not settings.host:
        logger.warning(
            "config.ini ONPREMISE/Host is empty - bridge idles until configured."
        )
    if tunnel.enabled:
        logger.info(
            "VRM tunnel ENABLED - advertising loadpoints as %r (AdvertiseIp=%s). "
            "Run the dbus-vrm-tunnel service for the button to work.",
            mgmt_connection, tunnel.advertise_ip,
        )

    import dbus
    from dbus.mainloop.glib import DBusGMainLoop
    from gi.repository import GLib

    # GLib mainloop MUST be set as default before SystemBus(), otherwise
    # dbus-python uses the wrong mainloop and signals won't dispatch.
    DBusGMainLoop(set_as_default=True)
    # Shared connection used ONLY for the read-only preflight scan below.
    bus = dbus.SystemBus()

    try:
        preflight_check_di_collisions(
            bus, di_range=(settings.di_lo, settings.di_hi),
        )
    except RuntimeError as e:
        logger.error("DeviceInstance collision check failed: %s", e)
        logger.error(
            "Fix: adjust DeviceInstanceRangeStart/End in config.ini to a "
            "non-overlapping range, or uninstall the conflicting service."
        )
        return 1

    client = EvccClient(host=settings.host or "0.0.0.0:0")
    store = StateStore(
        here / "state.json",
        di_range=(settings.di_lo, settings.di_hi),
    )
    # Each loadpoint needs its OWN connection: dbus-python only allows one
    # handler for object path '/' per connection, and every VeDbusService
    # registers a VeDbusRootExport there. private=True forces a new connection
    # instead of returning the shared SystemBus singleton.
    sync = LoadpointSync(
        client, store, bus_factory=lambda: dbus.SystemBus(private=True),
        mgmt_connection=mgmt_connection,
    )

    logger.info(
        "dbus-evcc-multi starting (host=%s, poll=%ds, di-range=%d-%d)",
        settings.host or "<unset>",
        settings.poll_seconds,
        settings.di_lo,
        settings.di_hi,
    )

    sync.tick()
    # PERFORMANCE: timeout_add_seconds (not timeout_add!) lets GLib/kernel
    # coalesce wakeups with other Cerbo services on the same mainloop tick.
    # Existing dbus-evcc forks use gobject.timeout_add(15000) -- millisecond
    # granularity with no coalescing.
    GLib.timeout_add_seconds(settings.poll_seconds, sync.tick)
    GLib.MainLoop().run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
