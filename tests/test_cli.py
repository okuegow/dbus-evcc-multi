import configparser

import pytest

from cli import Settings, parse_args, read_config, resolve_settings


def _make_cp(host="evcc:7070", poll=15, lo=40, hi=59):
    cp = configparser.ConfigParser()
    cp["DEFAULT"] = {
        "PollSeconds": str(poll),
        "DeviceInstanceRangeStart": str(lo),
        "DeviceInstanceRangeEnd": str(hi),
    }
    cp["ONPREMISE"] = {"Host": host}
    return cp


def test_parse_args_defaults():
    args = parse_args([])
    assert args.debug is False
    assert args.config is None


def test_parse_args_debug_flag():
    args = parse_args(["--debug"])
    assert args.debug is True


def test_parse_args_config_path():
    args = parse_args(["--config", "/tmp/cfg.ini"])
    assert args.config == "/tmp/cfg.ini"


def test_read_config_returns_empty_for_missing(tmp_path):
    cp = read_config(tmp_path / "does-not-exist.ini")
    assert isinstance(cp, configparser.ConfigParser)
    assert list(cp.sections()) == []


def test_read_config_parses_existing(tmp_path):
    p = tmp_path / "c.ini"
    p.write_text("[ONPREMISE]\nHost = 1.2.3.4:7070\n")
    cp = read_config(p)
    assert cp.get("ONPREMISE", "Host") == "1.2.3.4:7070"


def test_resolve_settings_happy_path():
    s = resolve_settings(_make_cp())
    assert s == Settings(host="evcc:7070", poll_seconds=15, di_lo=40, di_hi=59)


def test_resolve_settings_strips_host_whitespace():
    s = resolve_settings(_make_cp(host="  evcc:7070  "))
    assert s.host == "evcc:7070"


def test_resolve_settings_uses_fallbacks_for_missing_keys():
    cp = configparser.ConfigParser()
    s = resolve_settings(cp)
    assert s.host == ""
    assert s.poll_seconds == 15
    assert s.di_lo == 40
    assert s.di_hi == 59


def test_resolve_settings_rejects_zero_poll():
    with pytest.raises(ValueError, match="PollSeconds"):
        resolve_settings(_make_cp(poll=0))


def test_resolve_settings_rejects_inverted_range():
    with pytest.raises(ValueError, match="Start"):
        resolve_settings(_make_cp(lo=59, hi=40))


def test_resolve_settings_rejects_out_of_byte_range():
    with pytest.raises(ValueError, match=r"\[0, 255\]"):
        resolve_settings(_make_cp(lo=40, hi=300))


def test_resolve_settings_propagates_non_integer_config(tmp_path):
    """An operator who hand-edits config.ini with a non-integer PollSeconds
    must get a clear ValueError at startup, not a TypeError mid-loop."""
    p = tmp_path / "c.ini"
    p.write_text(
        "[DEFAULT]\nPollSeconds = fifteen\n[ONPREMISE]\nHost = h:1\n"
    )
    cp = read_config(p)
    with pytest.raises(ValueError):
        resolve_settings(cp)
