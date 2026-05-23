#!/usr/bin/env python3
"""setup_config.py - robust config.ini edits for the interactive installer.

Reads config.ini with configparser (case-preserving), mutates ONLY the
requested section/keys, writes it back. Comments are NOT preserved (a known
configparser limitation; accepted - the shipped template documents the fields
and the file is operator-owned after setup). Importable + unit-tested; no
dbus/gi. Validation of an enabled tunnel reuses cli.resolve_tunnel_settings so
there is one source of truth for the rules.
"""
from __future__ import annotations

import argparse
import configparser
import sys
from pathlib import Path


def _load(path: Path) -> configparser.ConfigParser:
    cp = configparser.ConfigParser()
    cp.optionxform = str  # preserve key case: Host, Enabled, AdvertiseIp, ...
    if path.exists():
        cp.read(path)
    return cp


def _write(cp: configparser.ConfigParser, path: Path) -> None:
    with open(path, "w", encoding="utf-8") as f:
        cp.write(f)


def set_onpremise_host(path: Path, host: str) -> None:
    cp = _load(path)
    if not cp.has_section("ONPREMISE"):
        cp.add_section("ONPREMISE")
    cp["ONPREMISE"]["Host"] = host
    _write(cp, path)


def set_tunnel(path: Path, *, enabled: bool, advertise_ip: str = "",
               evcc_target: str = "127.0.0.1:7070",
               proxy_port: int = 8099) -> None:
    section = {
        "Enabled": "true" if enabled else "false",
        "AdvertiseIp": advertise_ip,
        "EvccTarget": evcc_target,
        "ProxyPort": str(proxy_port),
    }
    if enabled:
        # Validate via the bridge's single source of truth (raises ValueError).
        import cli
        probe = configparser.ConfigParser()
        probe.optionxform = str
        probe["VRM_TUNNEL"] = section
        cli.resolve_tunnel_settings(probe)
    cp = _load(path)
    cp["VRM_TUNNEL"] = section
    _write(cp, path)


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="setup_config")
    p.add_argument("--config", required=True, help="path to config.ini")
    sub = p.add_subparsers(dest="cmd", required=True)

    sh = sub.add_parser("set-host", help="set [ONPREMISE] Host")
    sh.add_argument("host")

    st = sub.add_parser("set-tunnel", help="set/replace [VRM_TUNNEL]")
    st.add_argument("--enabled", required=True, choices=["true", "false"])
    st.add_argument("--advertise-ip", default="")
    st.add_argument("--evcc-target", default="127.0.0.1:7070")
    st.add_argument("--proxy-port", type=int, default=8099)

    args = p.parse_args(argv)
    path = Path(args.config)
    try:
        if args.cmd == "set-host":
            set_onpremise_host(path, args.host.strip())
        elif args.cmd == "set-tunnel":
            set_tunnel(
                path, enabled=(args.enabled == "true"),
                advertise_ip=args.advertise_ip.strip(),
                evcc_target=args.evcc_target.strip(),
                proxy_port=args.proxy_port,
            )
    except ValueError as e:
        print("config error: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
