import configparser
from pathlib import Path

import pytest

from setup_config import set_onpremise_host, set_tunnel, main as setup_config_main
from cli import read_config, resolve_settings, resolve_tunnel_settings


def _read_raw(path):
    # Read the way the bridge does (keys case-insensitive, DEFAULT inherited),
    # but without interpolation so raw values can be compared.
    cp = configparser.ConfigParser(interpolation=None)
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


# --- Writes go through config.ini.example: comments stay, values are ours ----

from setup_config import sync_with_example, TEMPLATE

MINIMAL = "[ONPREMISE]\nHost = 172.20.4.128:7070\n"


def test_template_ships_next_to_the_module():
    assert TEMPLATE.name == "config.ini.example" and TEMPLATE.exists()


def test_set_host_keeps_the_template_comments(tmp_path):
    p = tmp_path / "config.ini"
    p.write_text(TEMPLATE.read_text())
    set_onpremise_host(p, "evcc:7070")
    text = p.read_text()
    assert "# IP/hostname + port of the EVCC instance" in text
    assert "Host = evcc:7070" in text.splitlines()
    assert resolve_settings(read_config(p)).host == "evcc:7070"


def test_set_tunnel_disabled_keeps_given_values(tmp_path):
    p = tmp_path / "config.ini"
    set_tunnel(p, enabled=False, advertise_ip="172.20.4.135",
               evcc_target="172.20.4.128:7070", proxy_port=8100)
    raw = _read_raw(p)
    assert raw["VRM_TUNNEL"]["Enabled"] == "false"
    assert raw["VRM_TUNNEL"]["AdvertiseIp"] == "172.20.4.135"
    assert raw["VRM_TUNNEL"]["ProxyPort"] == "8100"


def test_sync_completes_a_hand_written_config(tmp_path):
    p = tmp_path / "config.ini"
    p.write_text(MINIMAL + "\n[DEFAULT]\nDeviceInstanceRangeStart = 60\n")
    assert sync_with_example(p) is True
    raw = _read_raw(p)
    assert raw["ONPREMISE"]["Host"] == "172.20.4.128:7070"
    assert raw["DEFAULT"]["DeviceInstanceRangeStart"] == "60"   # ours kept
    assert raw["DEFAULT"]["DeviceInstanceRangeEnd"] == "59"     # template filled
    assert raw["VRM_TUNNEL"]["Enabled"] == "false"
    assert "# Poll interval in seconds" in p.read_text()


def test_sync_keeps_unknown_keys_and_sections(tmp_path):
    p = tmp_path / "config.ini"
    p.write_text(MINIMAL + "Extra = 1\n\n[CUSTOM]\nFoo = bar\n")
    sync_with_example(p)
    raw = _read_raw(p)
    assert raw["ONPREMISE"]["Extra"] == "1"
    assert raw["CUSTOM"]["Foo"] == "bar"


def test_sync_leaves_a_complete_config_untouched(tmp_path):
    p = tmp_path / "config.ini"
    own = TEMPLATE.read_text().replace("\nHost =\n", "\n# my own note\nHost = evcc:7070\n")
    p.write_text(own)
    assert sync_with_example(p) is False
    assert p.read_text() == own


def test_sync_is_idempotent(tmp_path):
    p = tmp_path / "config.ini"
    p.write_text(MINIMAL)
    sync_with_example(p)
    first = p.read_text()
    assert sync_with_example(p) is False
    assert p.read_text() == first


def test_cli_sync_example(tmp_path):
    p = tmp_path / "config.ini"
    p.write_text(MINIMAL)
    assert setup_config_main(["--config", str(p), "sync-example"]) == 0
    assert resolve_settings(read_config(p)).di_hi == 59


# --- The bridge must read exactly what the operator had -----------------------

def test_sync_lowercase_key_is_not_duplicated(tmp_path):
    p = tmp_path / "config.ini"
    p.write_text("[ONPREMISE]\nhost = evcc:7070\n")
    sync_with_example(p)
    assert resolve_settings(read_config(p)).host == "evcc:7070"  # no DuplicateOptionError


def test_sync_keeps_values_inherited_from_default(tmp_path):
    p = tmp_path / "config.ini"
    p.write_text("[DEFAULT]\nHost = evcc:7070\n")
    sync_with_example(p)
    assert resolve_settings(read_config(p)).host == "evcc:7070"
    # still inherited, not copied: changing [DEFAULT] later still takes effect
    text = p.read_text()
    assert text.lower().count("host = evcc:7070") == 1  # only in [DEFAULT]
    p.write_text(text.replace("evcc:7070", "other:7070"))
    assert resolve_settings(read_config(p)).host == "other:7070"


def test_sync_keeps_multiline_values(tmp_path):
    p = tmp_path / "config.ini"
    p.write_text(MINIMAL + "Extra = first\n    second\n")
    sync_with_example(p)
    assert _read_raw(p)["ONPREMISE"]["Extra"] == "first\nsecond"


def test_sync_keeps_percent_signs_raw(tmp_path):
    p = tmp_path / "config.ini"
    p.write_text(MINIMAL + "Extra = 100%%\n")
    sync_with_example(p)
    assert "Extra = 100%%" in p.read_text().replace("extra", "Extra")
    assert read_config(p)["ONPREMISE"]["Extra"] == "100%"  # the bridge's view


def test_set_host_on_lowercase_key_leaves_one_key(tmp_path):
    p = tmp_path / "config.ini"
    p.write_text("[ONPREMISE]\nhost = old:7070\n")
    set_onpremise_host(p, "new:7070")
    assert resolve_settings(read_config(p)).host == "new:7070"
