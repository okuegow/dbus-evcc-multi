import configparser
from pathlib import Path

import pytest

from setup_config import set_onpremise_host, set_tunnel, main as setup_config_main
from cli import read_config, resolve_settings, resolve_tunnel_settings


def _read_raw(path):
    cp = configparser.ConfigParser()
    cp.optionxform = str
    cp.read(path)
    return cp


def test_set_host_on_new_file(tmp_path):
    p = tmp_path / "config.ini"
    set_onpremise_host(p, "192.168.1.50:7070")
    assert resolve_settings(read_config(p)).host == "192.168.1.50:7070"


def test_set_host_preserves_other_sections(tmp_path):
    p = tmp_path / "config.ini"
    p.write_text(
        "[DEFAULT]\nPollSeconds = 15\n\n[ONPREMISE]\nHost =\n\n"
        "[VRM_TUNNEL]\nEnabled = false\nProxyPort = 8099\n"
    )
    set_onpremise_host(p, "evcc:7070")
    raw = _read_raw(p)
    assert raw["ONPREMISE"]["Host"] == "evcc:7070"
    assert raw["DEFAULT"]["PollSeconds"] == "15"
    assert raw["VRM_TUNNEL"]["ProxyPort"] == "8099"


def test_set_tunnel_enabled_round_trips_through_cli(tmp_path):
    p = tmp_path / "config.ini"
    set_tunnel(p, enabled=True, advertise_ip="172.20.4.135",
               evcc_target="127.0.0.1:7070", proxy_port=8099)
    t = resolve_tunnel_settings(read_config(p))
    assert (t.enabled, t.advertise_ip, t.evcc_target, t.proxy_port) == (
        True, "172.20.4.135", "127.0.0.1:7070", 8099)


def test_set_tunnel_disabled(tmp_path):
    p = tmp_path / "config.ini"
    set_tunnel(p, enabled=False)
    assert resolve_tunnel_settings(read_config(p)).enabled is False


def test_set_tunnel_replaces_section_no_stale_keys(tmp_path):
    p = tmp_path / "config.ini"
    p.write_text(
        "[VRM_TUNNEL]\nEnabled = true\nAdvertiseIp = 172.20.4.135\n"
        "ExtraKey = foo\n"
    )
    set_tunnel(p, enabled=False)
    raw = _read_raw(p)
    assert raw["VRM_TUNNEL"]["Enabled"] == "false"
    assert raw["VRM_TUNNEL"]["AdvertiseIp"] == ""
    assert "ExtraKey" not in raw["VRM_TUNNEL"]


def test_set_tunnel_enabled_rejects_loopback(tmp_path):
    p = tmp_path / "config.ini"
    with pytest.raises(ValueError, match="non-loopback"):
        set_tunnel(p, enabled=True, advertise_ip="127.0.0.1")


def test_set_tunnel_enabled_rejects_empty_advertise_ip(tmp_path):
    p = tmp_path / "config.ini"
    with pytest.raises(ValueError, match="AdvertiseIp"):
        set_tunnel(p, enabled=True, advertise_ip="")


def test_set_host_is_idempotent(tmp_path):
    p = tmp_path / "config.ini"
    set_onpremise_host(p, "evcc:7070")
    first = p.read_text()
    set_onpremise_host(p, "evcc:7070")
    assert p.read_text() == first


def test_cli_set_host(tmp_path):
    p = tmp_path / "config.ini"
    rc = setup_config_main(["--config", str(p), "set-host", "evcc:7070"])
    assert rc == 0
    assert resolve_settings(read_config(p)).host == "evcc:7070"


def test_cli_set_tunnel_enabled(tmp_path):
    p = tmp_path / "config.ini"
    rc = setup_config_main([
        "--config", str(p), "set-tunnel", "--enabled", "true",
        "--advertise-ip", "172.20.4.135", "--evcc-target", "127.0.0.1:7070",
        "--proxy-port", "8099",
    ])
    assert rc == 0
    assert resolve_tunnel_settings(read_config(p)).enabled is True


def test_cli_set_tunnel_disabled(tmp_path):
    p = tmp_path / "config.ini"
    rc = setup_config_main(["--config", str(p), "set-tunnel", "--enabled", "false"])
    assert rc == 0
    assert resolve_tunnel_settings(read_config(p)).enabled is False


def test_cli_set_tunnel_invalid_returns_nonzero_and_does_not_write(tmp_path, capsys):
    p = tmp_path / "config.ini"
    rc = setup_config_main([
        "--config", str(p), "set-tunnel", "--enabled", "true",
        "--advertise-ip", "127.0.0.1",
    ])
    assert rc == 1
    assert "non-loopback" in capsys.readouterr().err
    assert not p.exists()  # invalid input must NOT create/modify the file


def test_cli_set_host_strips_whitespace(tmp_path):
    p = tmp_path / "config.ini"
    rc = setup_config_main(["--config", str(p), "set-host", "  evcc:7070  "])
    assert rc == 0
    assert resolve_settings(read_config(p)).host == "evcc:7070"
