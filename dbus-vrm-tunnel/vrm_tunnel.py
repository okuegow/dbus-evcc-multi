#!/usr/bin/env python3
"""vrm_tunnel.py - importable core for the dbus-vrm-tunnel service.

Pure logic (should_redirect, build_tunnel_plan) is unit-tested on macOS.
RewriteProxy / DnatRule do socket + iptables I/O and are exercised on real
Venus OS hardware. The hyphenated entry script dbus-vrm-tunnel.py wires config ->
these.
"""
from __future__ import annotations

import signal
import socket
import subprocess
import threading
from typing import NamedTuple

REDIRECT_PATHS = (b"/login.htm", b"/login.html")


def should_redirect(first_line: bytes) -> bool:
    """True if the HTTP request's path (first request line) is the VRM-hardcoded
    /login.htm (or .html), which EVCC 404s. Query string is ignored. A
    malformed line with no path token falls through to '/' -> no redirect."""
    parts = first_line.split(b" ")
    path = parts[1] if len(parts) >= 2 else b"/"
    return path.split(b"?", 1)[0] in REDIRECT_PATHS


class TunnelPlan(NamedTuple):
    proxy_listen_ip: str
    proxy_listen_port: int
    proxy_target_host: str
    proxy_target_port: int
    dnat_dst_ip: str
    dnat_dst_port: int   # always 80 (VRM hardcodes :80)
    dnat_to_dest: str    # "<advertise_ip>:<proxy_port>"


def build_tunnel_plan(settings) -> TunnelPlan:
    """Map resolved tunnel settings (duck-typed: .advertise_ip, .evcc_target,
    .proxy_port) to the concrete proxy + DNAT wiring.

    VRM tunnels to <advertise_ip>:80 -> iptables DNATs that to
    <advertise_ip>:<proxy_port> (the rewrite proxy) -> proxy forwards to the
    real EVCC at evcc_target.
    """
    host, _, port = settings.evcc_target.rpartition(":")
    return TunnelPlan(
        proxy_listen_ip=settings.advertise_ip,
        proxy_listen_port=settings.proxy_port,
        proxy_target_host=host,
        proxy_target_port=int(port),
        dnat_dst_ip=settings.advertise_ip,
        dnat_dst_port=80,
        dnat_to_dest="%s:%d" % (settings.advertise_ip, settings.proxy_port),
    )


class RewriteProxy(threading.Thread):
    """Peeks ONLY the first request line per connection. /login.htm -> 302 /;
    everything else is spliced raw (HTTP, SSE, WebSocket pass through)."""

    def __init__(self, listen_ip, listen_port, target_host, target_port):
        super().__init__(daemon=True)
        self.listen_ip = listen_ip
        self.listen_port = listen_port
        self.target = (target_host, target_port)

    @staticmethod
    def _splice(src, dst):
        try:
            while True:
                data = src.recv(65536)
                if not data:
                    break
                dst.sendall(data)
        except OSError:
            pass
        finally:
            for s in (src, dst):
                try:
                    s.shutdown(socket.SHUT_RDWR)
                except OSError:
                    pass

    def _handle(self, client):
        try:
            client.settimeout(15)
            buf = b""
            while b"\r\n" not in buf and len(buf) < 8192:
                chunk = client.recv(4096)
                if not chunk:
                    client.close()
                    return
                buf += chunk
            client.settimeout(None)
            first_line = buf.split(b"\r\n", 1)[0]
            if should_redirect(first_line):
                client.sendall(
                    b"HTTP/1.1 302 Found\r\nLocation: /\r\n"
                    b"Content-Length: 0\r\nConnection: close\r\n\r\n"
                )
                client.close()
                return
            upstream = socket.create_connection(self.target, timeout=10)
            upstream.sendall(buf)  # replay the bytes we already read
            threading.Thread(target=self._splice, args=(client, upstream), daemon=True).start()
            self._splice(upstream, client)
        except OSError as e:
            print("[proxy] client error: %s" % e, flush=True)
            try:
                client.close()
            except OSError:
                pass

    def run(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            srv.bind((self.listen_ip, self.listen_port))
        except Exception as e:
            print("[proxy] BIND FAILED on %s:%s: %s" % (self.listen_ip, self.listen_port, e), flush=True)
            return
        srv.listen(50)
        print("[proxy] %s:%s -> %s:%s  (/login.htm -> 302 /)"
              % (self.listen_ip, self.listen_port, self.target[0], self.target[1]), flush=True)
        while True:
            try:
                conn, _ = srv.accept()
                threading.Thread(target=self._handle, args=(conn,), daemon=True).start()
            except Exception as e:
                print("[proxy] accept error: %s" % e, flush=True)


class DnatRule:
    """iptables OUTPUT DNAT helper: idempotent add (-C check first), tracked
    removal on exit."""

    def __init__(self, dst_ip, dst_port, to_dest):
        self.rule = [
            "-d", dst_ip, "-p", "tcp",
            "--dport", str(dst_port), "-j", "DNAT", "--to-destination", to_dest,
        ]
        self.added = False

    def add(self):
        if subprocess.call(["iptables", "-t", "nat", "-C", "OUTPUT"] + self.rule,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) != 0:
            rc = subprocess.call(["iptables", "-t", "nat", "-A", "OUTPUT"] + self.rule,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            self.added = (rc == 0)
            print("[iptables] add OUTPUT %s rc=%s" % (" ".join(self.rule), rc), flush=True)
        else:
            print("[iptables] rule already present, leaving as-is", flush=True)

    def remove(self):
        if self.added:
            rc = subprocess.call(["iptables", "-t", "nat", "-D", "OUTPUT"] + self.rule,
                                 stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            print("[iptables] del OUTPUT rc=%s" % rc, flush=True)
            self.added = False


def run_tunnel(plan, stop_event=None):
    """Start the rewrite proxy + DNAT for `plan`, then block until SIGTERM/
    SIGINT (or stop_event). Removes the DNAT rule on the way out. Returns 0.

    No dbus/gi dependency: integration mode publishes no D-Bus device, so a
    plain Event-wait is enough and keeps this importable/testable on macOS.
    """
    if stop_event is None:
        stop_event = threading.Event()

    RewriteProxy(
        plan.proxy_listen_ip, plan.proxy_listen_port,
        plan.proxy_target_host, plan.proxy_target_port,
    ).start()

    dnat = DnatRule(plan.dnat_dst_ip, plan.dnat_dst_port, plan.dnat_to_dest)
    dnat.add()

    def _stop(*_a):
        print("[main] shutting down", flush=True)
        stop_event.set()

    # signal.signal only works on the main thread; guard for safety in tests.
    try:
        signal.signal(signal.SIGTERM, _stop)
        signal.signal(signal.SIGINT, _stop)
    except ValueError:
        pass

    try:
        stop_event.wait()
    finally:
        dnat.remove()
    print("[main] bye.", flush=True)
    return 0
