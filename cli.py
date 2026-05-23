"""Pure-function CLI helpers for dbus-evcc.py.

Kept separate so they can be unit-tested on macOS without importing the
hyphenated dbus-evcc.py entry script (which pulls dbus + gi at runtime).
"""
from __future__ import annotations

import argparse
import configparser
import ipaddress
from pathlib import Path
from typing import NamedTuple


class Settings(NamedTuple):
    host: str
    poll_seconds: int
    di_lo: int
    di_hi: int


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
    return Settings(host=host, poll_seconds=poll_s, di_lo=di_lo, di_hi=di_hi)


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
    whitelist <ip>:80 so the VRM 'Bedienfeld' button works."""
    if tunnel.enabled:
        return "Modbus TCP %s" % tunnel.advertise_ip
    return "EVCC REST API"
