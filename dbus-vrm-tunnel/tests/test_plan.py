from types import SimpleNamespace

from vrm_tunnel import build_tunnel_plan


def _settings(advertise_ip, evcc_target, proxy_port):
    return SimpleNamespace(
        advertise_ip=advertise_ip, evcc_target=evcc_target, proxy_port=proxy_port
    )


def test_remote_topology():
    plan = build_tunnel_plan(_settings("172.20.4.90", "172.20.4.90:7070", 8099))
    assert plan.proxy_listen_ip == "172.20.4.90"
    assert plan.proxy_listen_port == 8099
    assert plan.proxy_target_host == "172.20.4.90"
    assert plan.proxy_target_port == 7070
    assert plan.dnat_dst_ip == "172.20.4.90"
    assert plan.dnat_dst_port == 80
    assert plan.dnat_to_dest == "172.20.4.90:8099"


def test_on_cerbo_topology():
    plan = build_tunnel_plan(_settings("172.20.4.135", "127.0.0.1:7070", 8099))
    assert plan.proxy_listen_ip == "172.20.4.135"
    assert plan.proxy_target_host == "127.0.0.1"
    assert plan.proxy_target_port == 7070
    assert plan.dnat_to_dest == "172.20.4.135:8099"
