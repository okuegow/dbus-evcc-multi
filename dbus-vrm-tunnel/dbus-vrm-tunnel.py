#!/usr/bin/env python3
"""dbus-vrm-tunnel - VRM "Bedienfeld" tunnel for the EVCC web UI.

Reads the shared [VRM_TUNNEL] section of dbus-evcc-multi's config.ini. When
enabled, runs a /login.htm rewrite proxy + iptables DNAT so the VRM portal
button reaches the real EVCC UI. When disabled, idles (no-op) so daemontools
keeps the slot without thrashing.

Pure logic lives in vrm_tunnel.py and is unit-tested on macOS. This script does
the config read + wiring and runs on Venus OS.
"""
from __future__ import annotations

import argparse
import sys
import threading
from pathlib import Path

DEFAULT_CONFIG = "/data/dbus-evcc-multi/config.ini"
# Co-deployed bridge dir holds cli.py (the single [VRM_TUNNEL] parser). Add it
# to sys.path so we never duplicate parsing logic.
DEFAULT_EVCC_MULTI_DIR = "/data/dbus-evcc-multi"


def main(argv=None):
    argv = argv if argv is not None else sys.argv[1:]
    p = argparse.ArgumentParser(prog="dbus-vrm-tunnel")
    p.add_argument("--config", default=DEFAULT_CONFIG,
                   help="shared config.ini (default: %s)" % DEFAULT_CONFIG)
    p.add_argument("--evcc-multi-dir", default=DEFAULT_EVCC_MULTI_DIR,
                   help="dir holding the bridge cli.py (single config parser)")
    args = p.parse_args(argv)

    if args.evcc_multi_dir not in sys.path:
        sys.path.insert(0, args.evcc_multi_dir)
    # Also import vrm_tunnel from next to this script.
    here = str(Path(__file__).resolve().parent)
    if here not in sys.path:
        sys.path.insert(0, here)

    from cli import read_config, resolve_tunnel_settings
    import vrm_tunnel

    cp = read_config(Path(args.config))
    try:
        tunnel = resolve_tunnel_settings(cp)
    except ValueError as e:
        print("[main] [VRM_TUNNEL] config invalid: %s" % e, flush=True)
        return 1

    if not tunnel.enabled:
        print("[main] VRM tunnel disabled (config.ini [VRM_TUNNEL] Enabled=false). "
              "Idling.", flush=True)
        # Block forever without busy-spinning; supervise restarts on config change.
        try:
            threading.Event().wait()
        except KeyboardInterrupt:
            pass
        return 0

    plan = vrm_tunnel.build_tunnel_plan(tunnel)
    print("[main] VRM tunnel enabled. proxy %s:%d -> %s:%d ; DNAT %s:80 -> %s"
          % (plan.proxy_listen_ip, plan.proxy_listen_port,
             plan.proxy_target_host, plan.proxy_target_port,
             plan.dnat_dst_ip, plan.dnat_to_dest), flush=True)
    return vrm_tunnel.run_tunnel(plan)


if __name__ == "__main__":
    sys.exit(main())
