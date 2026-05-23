import json

import pytest

from seed_state import main, parse_pairs


def test_parse_pairs_single():
    assert parse_pairs(["HeatingElement:56"]) == {"HeatingElement": 56}


def test_parse_pairs_multiple():
    assert parse_pairs(["HeatingElement:56", "Wallbox:49"]) == {
        "HeatingElement": 56,
        "Wallbox": 49,
    }


def test_parse_pairs_strips_whitespace():
    assert parse_pairs(["  Heater :40"]) == {"Heater": 40}


def test_parse_pairs_title_with_colon_uses_rsplit():
    # Edge: title contains ':' (e.g. "Foo:Bar:42" -> Title="Foo:Bar", DI=42)
    assert parse_pairs(["Foo:Bar:42"]) == {"Foo:Bar": 42}


def test_parse_pairs_rejects_missing_separator():
    with pytest.raises(ValueError, match="expected"):
        parse_pairs(["HeatingElement"])


def test_parse_pairs_rejects_non_integer_di():
    with pytest.raises(ValueError, match="integer"):
        parse_pairs(["HeatingElement:abc"])


def test_parse_pairs_rejects_empty_title():
    with pytest.raises(ValueError, match="empty"):
        parse_pairs([":56"])


def test_main_writes_state_file(tmp_path, monkeypatch, capsys):
    # Re-route state.json to tmp_path by changing the script's parent dir
    # via monkeypatching __file__ on the module
    import seed_state
    fake_script = tmp_path / "seed_state.py"
    fake_script.write_text("")
    monkeypatch.setattr(seed_state, "__file__", str(fake_script))

    rc = main(["seed_state.py", "HeatingElement:56", "Wallbox:49"])
    assert rc == 0
    state_path = tmp_path / "state.json"
    assert state_path.exists()
    data = json.loads(state_path.read_text())
    assert data == {"HeatingElement": 56, "Wallbox": 49}


def test_main_prints_help_on_no_args(capsys):
    rc = main(["seed_state.py"])
    assert rc == 1
    out = capsys.readouterr().out
    assert "Usage" in out or "Seed state.json" in out


def test_main_returns_2_on_bad_arg(tmp_path, monkeypatch, capsys):
    import seed_state
    fake_script = tmp_path / "seed_state.py"
    fake_script.write_text("")
    monkeypatch.setattr(seed_state, "__file__", str(fake_script))

    rc = main(["seed_state.py", "bogus-no-colon"])
    assert rc == 2
    err = capsys.readouterr().err
    assert "expected" in err.lower()
