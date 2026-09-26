"""Pure-function CLI helpers for dbus-evcc.py.

Kept separate so they can be unit-tested on macOS without importing the
hyphenated dbus-evcc.py entry script (which pulls dbus + gi at runtime).
"""
from __future__ import annotations

import argparse
import configparser
import ipaddress
import sys
import time
import traceback
from pathlib import Path
from typing import NamedTuple


# daemontools respawns a service the moment it exits. After a failed start
# (DeviceInstance collision, bad VRM_TUNNEL config, crash) a hot respawn loop
# costs enough CPU to trip the Venus load watchdog (/etc/watchdog.conf:
# max-load-5 = 10, max-load-15 = 6), which reboots the GX. So pause first.
# The pause lives here, not in service/run: the run script must `exec` python,
# otherwise `svc -t`/`svc -d` kill only the shell and orphan the bridge.
BACKOFF_SECONDS = 30


def run_with_backoff(main, sleep=None) -> int:
    """Run main(); on a non-zero exit or an exception wait BACKOFF_SECONDS.

    main() must do its own application imports: an ImportError at module
    level would exit before this guard runs and respawn hot.
    """
    try:
        rc = main()
    except Exception:
        traceback.print_exc()
        rc = 1
    if rc:
        print("dbus-evcc-multi exited with code %d - waiting %ds before restart"
              % (rc, BACKOFF_SECONDS), file=sys.stderr, flush=True)
        (sleep or time.sleep)(BACKOFF_SECONDS)
    return rc


class Settings(NamedTuple):
    host: str
    poll_seconds: int
    di_lo: int
    di_hi: int
    ac_position: int


def parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="dbus-evcc-multi",
        description="Auto-discovery bridge from EVCC to Victron D-Bus.",
    )
    parser.add_argument(
        "--debug", action="store_true", help="DEBUG log level (default INFO)",
    )
    parser.add_argument(
        "--config", default=None,
        help="Path to config.ini (default: next to this script)",
    )
    parser.add_argument(
        "--plan", action="store_true",
        help="Show what the first start would do (DeviceInstances, energy "
             "counters) and exit without touching the D-Bus",
    )
    return parser.parse_args(argv)


def read_config(path: Path) -> configparser.ConfigParser:
    cp = configparser.ConfigParser()
    if path.exists():
        cp.read(path)
    return cp


def resolve_settings(cp: configparser.ConfigParser) -> Settings:
    host = cp.get("ONPREMISE", "Host", fallback="").strip()
    poll_s = cp.getint("DEFAULT", "PollSeconds", fallback=15)
    di_lo = cp.getint("DEFAULT", "DeviceInstanceRangeStart", fallback=40)
    di_hi = cp.getint("DEFAULT", "DeviceInstanceRangeEnd", fallback=59)
    ac_position = cp.getint("DEFAULT", "AcPosition", fallback=0)
    if poll_s < 1:
        raise ValueError("PollSeconds must be >= 1, got %d" % poll_s)
    if di_lo > di_hi:
        raise ValueError(
            "DeviceInstanceRangeStart (%d) > End (%d)" % (di_lo, di_hi)
        )
    if di_lo < 0 or di_hi > 255:
        raise ValueError(
            "DeviceInstance range must lie within [0, 255]; got [%d, %d]"
            % (di_lo, di_hi)
        )
    if ac_position not in (0, 1):
        raise ValueError(
            "AcPosition must be 0 (AC-Out) or 1 (AC-In), got %d" % ac_position
        )
    return Settings(host=host, poll_seconds=poll_s, di_lo=di_lo, di_hi=di_hi,
                    ac_position=ac_position)


class TunnelSettings(NamedTuple):
    enabled: bool
    advertise_ip: str
    evcc_target: str   # "host:port" the rewrite proxy forwards to
    proxy_port: int


def _is_loopback(ip: str) -> bool:
    try:
        return ipaddress.ip_address(ip).is_loopback
    except ValueError:
        # Not a literal IP (e.g. a hostname). Loopback check does not apply.
        return False


def resolve_tunnel_settings(cp: configparser.ConfigParser) -> TunnelSettings:
    enabled = cp.getboolean("VRM_TUNNEL", "Enabled", fallback=False)
    advertise_ip = cp.get("VRM_TUNNEL", "AdvertiseIp", fallback="").strip()
    evcc_target = cp.get(
        "VRM_TUNNEL", "EvccTarget",
        fallback="127.0.0.1:7070",  # proxy -> local EVCC default
    ).strip()
    proxy_port = cp.getint("VRM_TUNNEL", "ProxyPort", fallback=8099)

    if enabled:
        if not advertise_ip:
            raise ValueError(
                "VRM_TUNNEL enabled but AdvertiseIp is empty - set it to the "
                "non-loopback IP VRM should tunnel to."
            )
        if _is_loopback(advertise_ip):
            raise ValueError(
                "AdvertiseIp must be a non-loopback IP (got %s); a loopback "
                "address would require the fake-Modbus fallback which is out "
                "of scope. Use the Cerbo LAN IP (on-Cerbo) or the EVCC host "
                "IP (remote)." % advertise_ip
            )
        host, sep, port = evcc_target.rpartition(":")
        if not sep or not host or not port.isdigit():
            raise ValueError(
                "EvccTarget must be host:port (got %r)" % evcc_target
            )
        if not (1 <= proxy_port <= 65535):
            raise ValueError(
                "ProxyPort must be in 1..65535, got %d" % proxy_port
            )

    return TunnelSettings(
        enabled=enabled,
        advertise_ip=advertise_ip,
        evcc_target=evcc_target,
        proxy_port=proxy_port,
    )


def mgmt_connection_string(tunnel: TunnelSettings) -> str:
    """The /Mgmt/Connection value each loadpoint advertises. With the tunnel
    on, the 'Modbus TCP <ip>' prefix is what makes generate_authorized_keys.sh
    whitelist <ip>:80 so the VRM 'Control panel' button works."""
    if tunnel.enabled:
        return "Modbus TCP %s" % tunnel.advertise_ip
    return "EVCC REST API"


def format_plan(rows) -> str:
    """Human-readable table for --plan.

    rows: list of dicts with title, deviceinstance, known (bool), source
    (EVCC kWh), published (kWh the bridge would publish first), note.
    """
    if not rows:
        return "EVCC reported no loadpoints - nothing to do."
    head = "%-24s %5s %6s %14s %14s  %s" % (
        "LOADPOINT", "DI", "KNOWN", "EVCC kWh", "PUBLISHED kWh", "NOTE")
    lines = [head, "-" * len(head)]
    for r in rows:
        lines.append("%-24s %5s %6s %14s %14s  %s" % (
            r["title"][:24],
            r["deviceinstance"] if r["deviceinstance"] is not None else "new",
            "yes" if r["known"] else "no",
            "-" if r["source"] is None else "%.3f" % r["source"],
            "-" if r["published"] is None else "%.3f" % r["published"],
            r.get("note", ""),
        ))
    return "\n".join(lines)
