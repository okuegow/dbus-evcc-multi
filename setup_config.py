#!/usr/bin/env python3
"""setup_config.py - robust config.ini edits for the interactive installer.

Reads config.ini with configparser (case-preserving), mutates ONLY the
requested section/keys and writes it back through config.ini.example: the
template's comments and order stay, every value comes from the operator's
file, and keys or sections the template does not know are kept at the end of
their section. Importable + unit-tested; no dbus/gi. Validation of an enabled
tunnel reuses cli.resolve_tunnel_settings so there is one source of truth for
the rules.
"""
from __future__ import annotations

import argparse
import configparser
import os
import re
import sys
from pathlib import Path

TEMPLATE = Path(__file__).resolve().parent / "config.ini.example"
_SECTION = re.compile(r"^\[([^\]]+)\]\s*$")
_KEY = re.compile(r"^([A-Za-z0-9_]+)\s*=")


def _load(path: Path) -> configparser.ConfigParser:
    # Keys are compared case-insensitively, exactly as the bridge reads them
    # (configparser's default optionxform). [DEFAULT] is read as an ordinary
    # section (_value() applies the inheritance), so its keys do not leak into
    # the others on write-back. No interpolation: values are kept raw
    # ("100%%" stays "100%%").
    cp = configparser.ConfigParser(default_section="\0", interpolation=None)
    if path.exists():
        cp.read(path)
    return cp


def _value(cp: configparser.ConfigParser, section: str, key: str):
    """The value the bridge would see, [DEFAULT] inheritance included."""
    if cp.has_option(section, key):
        return cp[section][key]
    if section != "DEFAULT" and cp.has_option("DEFAULT", key):
        return cp["DEFAULT"][key]
    return None


def _kv(key, value) -> str:
    # Continuation lines of a multi-line value must stay indented.
    return ("%s = %s" % (key, value.replace("\n", "\n    "))).rstrip()


def _render(cp: configparser.ConfigParser) -> str:
    """The template's text with the operator's values filled in."""
    out, seen, section = [], {}, None

    def flush_extra(sec):
        if sec is not None and cp.has_section(sec):
            extra = [k for k in cp[sec] if k not in seen.get(sec, ())]
            while out and out[-1] == "":
                out.pop()
            out.extend(_kv(k, cp[sec][k]) for k in extra)
            out.append("")

    for line in TEMPLATE.read_text(encoding="utf-8").splitlines():
        m = _SECTION.match(line)
        if m:
            flush_extra(section)
            section = m.group(1)
            seen.setdefault(section, set())
        else:
            k = _KEY.match(line)
            if k and section is not None:
                key = k.group(1)
                seen[section].add(key.lower())
                if cp.has_option(section, key):
                    line = _kv(key, cp[section][key])
                elif _value(cp, section, key) is not None:
                    continue  # inherited from [DEFAULT]: keep it inherited
        out.append(line)
    flush_extra(section)
    for sec in cp.sections():
        if sec not in seen:
            out.append("[%s]" % sec)
            out.extend(_kv(k, v) for k, v in cp[sec].items())
            out.append("")
    while out and out[-1] == "":
        out.pop()
    return "\n".join(out) + "\n"


def _write(cp: configparser.ConfigParser, path: Path) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(_render(cp), encoding="utf-8")
    os.replace(tmp, path)  # atomic: a reader never sees a half-written file


def sync_with_example(path: Path) -> bool:
    """Complete a config.ini that lacks keys of the template (hand-written,
    or from an older release). A complete file is left alone, own comments
    included. Returns True when the file was rewritten."""
    cp = _load(path)
    template = _load(TEMPLATE)
    missing = [(s, k) for s in template.sections() for k in template[s]
               if _value(cp, s, k) is None]
    if not missing:
        return False
    for s, k in missing:
        if not cp.has_section(s):
            cp.add_section(s)
        cp[s][k] = template[s][k]
    _write(cp, path)
    return True


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

    sub.add_parser("sync-example",
                   help="add keys missing from config.ini.example, keep all values")

    args = p.parse_args(argv)
    path = Path(args.config)
    try:
        if args.cmd == "set-host":
            set_onpremise_host(path, args.host.strip())
        elif args.cmd == "sync-example":
            if sync_with_example(path):
                print("%s completed from %s" % (path, TEMPLATE.name))
        elif args.cmd == "set-tunnel":
            set_tunnel(
                path, enabled=(args.enabled == "true"),
                advertise_ip=args.advertise_ip.strip(),
                evcc_target=args.evcc_target.strip(),
                proxy_port=args.proxy_port,
            )
    except (ValueError, configparser.Error) as e:
        print("config error: %s" % e, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
