import configparser

import pytest

from cli import (
    TunnelSettings,
    mgmt_connection_string,
    resolve_tunnel_settings,
)


def _cp(**vrm):
    cp = configparser.ConfigParser()
    if vrm:
        cp["VRM_TUNNEL"] = {k: str(v) for k, v in vrm.items()}
    return cp


def test_defaults_to_disabled_when_section_absent():
    t = resolve_tunnel_settings(_cp())
    assert t == TunnelSettings(
        enabled=False, advertise_ip="", evcc_target="127.0.0.1:7070", proxy_port=8099
    )


def test_disabled_skips_validation_even_with_loopback_ip():
    # When disabled, a loopback (or empty) AdvertiseIp must NOT raise.
    t = resolve_tunnel_settings(_cp(Enabled="false", AdvertiseIp="127.0.0.1"))
    assert t.enabled is False


def test_enabled_happy_path():
    t = resolve_tunnel_settings(
        _cp(Enabled="true", AdvertiseIp="172.20.4.135",
            EvccTarget="172.20.4.90:7070", ProxyPort="8099")
    )
    assert t == TunnelSettings(
        enabled=True, advertise_ip="172.20.4.135",
        evcc_target="172.20.4.90:7070", proxy_port=8099,
    )


def test_enabled_strips_whitespace():
    t = resolve_tunnel_settings(
        _cp(Enabled="true", AdvertiseIp="  172.20.4.135  ",
            EvccTarget="  127.0.0.1:7070 ")
    )
    assert t.advertise_ip == "172.20.4.135"
    assert t.evcc_target == "127.0.0.1:7070"


def test_enabled_rejects_empty_advertise_ip():
    with pytest.raises(ValueError, match="AdvertiseIp"):
        resolve_tunnel_settings(_cp(Enabled="true", AdvertiseIp=""))


def test_enabled_rejects_loopback_advertise_ip():
    with pytest.raises(ValueError, match="non-loopback"):
        resolve_tunnel_settings(
            _cp(Enabled="true", AdvertiseIp="127.0.0.1", EvccTarget="172.20.4.90:7070")
        )


def test_enabled_rejects_evcc_target_without_port():
    with pytest.raises(ValueError, match="EvccTarget"):
        resolve_tunnel_settings(
            _cp(Enabled="true", AdvertiseIp="172.20.4.135", EvccTarget="172.20.4.90")
        )


def test_enabled_rejects_out_of_range_proxy_port():
    with pytest.raises(ValueError, match="ProxyPort"):
        resolve_tunnel_settings(
            _cp(Enabled="true", AdvertiseIp="172.20.4.135",
                EvccTarget="127.0.0.1:7070", ProxyPort="70000")
        )


def test_enabled_rejects_zero_proxy_port():
    with pytest.raises(ValueError, match="ProxyPort"):
        resolve_tunnel_settings(
            _cp(Enabled="true", AdvertiseIp="172.20.4.135",
                EvccTarget="127.0.0.1:7070", ProxyPort="0"))


def test_mgmt_connection_string_when_enabled():
    t = TunnelSettings(
        enabled=True, advertise_ip="172.20.4.135",
        evcc_target="127.0.0.1:7070", proxy_port=8099,
    )
    assert mgmt_connection_string(t) == "Modbus TCP 172.20.4.135"


def test_mgmt_connection_string_when_disabled():
    t = TunnelSettings(
        enabled=False, advertise_ip="",
        evcc_target="127.0.0.1:7070", proxy_port=8099,
    )
    assert mgmt_connection_string(t) == "EVCC REST API"
