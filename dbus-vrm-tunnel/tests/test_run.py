from types import SimpleNamespace

import vrm_tunnel


def test_run_tunnel_starts_proxy_and_dnat_then_cleans_up(monkeypatch):
    events = []

    class FakeProxy:
        def __init__(self, *a):
            events.append(("proxy_init", a))
        def start(self):
            events.append(("proxy_start",))

    class FakeDnat:
        def __init__(self, *a):
            events.append(("dnat_init", a))
        def add(self):
            events.append(("dnat_add",))
        def remove(self):
            events.append(("dnat_remove",))

    monkeypatch.setattr(vrm_tunnel, "RewriteProxy", FakeProxy)
    monkeypatch.setattr(vrm_tunnel, "DnatRule", FakeDnat)

    plan = vrm_tunnel.build_tunnel_plan(
        SimpleNamespace(advertise_ip="172.20.4.135",
                        evcc_target="127.0.0.1:7070", proxy_port=8099)
    )
    stop = vrm_tunnel.threading.Event()
    stop.set()  # return immediately instead of blocking forever

    rc = vrm_tunnel.run_tunnel(plan, stop_event=stop)

    assert rc == 0
    names = [e[0] for e in events]
    assert names == ["proxy_init", "proxy_start", "dnat_init", "dnat_add",
                     "dnat_remove"]
    # proxy bound to advertise ip:proxy_port -> evcc target
    assert events[0][1] == ("172.20.4.135", 8099, "127.0.0.1", 7070)
    # dnat advertise ip:80 -> advertise ip:proxy_port
    assert events[2][1] == ("172.20.4.135", 80, "172.20.4.135:8099")
