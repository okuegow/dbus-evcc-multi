import json
from pathlib import Path

import pytest
import requests

from evcc_api import EvccClient, EvccUnreachable, Loadpoint, _parse_loadpoints

FIXTURES = Path(__file__).parent / "fixtures"


def _load_fixture(name):
    return json.loads((FIXTURES / name).read_text())


def test_fetch_state_returns_loadpoints_1lp(requests_mock):
    requests_mock.get(
        "http://192.0.2.10:7070/api/state",
        json=_load_fixture("evcc_state_1lp.json"),
    )
    client = EvccClient(host="192.0.2.10:7070", timeout=2)
    lps = client.fetch_loadpoints()
    assert len(lps) == 1
    lp = lps[0]
    assert isinstance(lp, Loadpoint)
    assert lp.title == "HeatingElement"
    assert lp.connected is True
    assert lp.charging is True
    assert lp.charge_power == 1500.0
    assert lp.charge_currents == [6.5, 0, 0]
    assert lp.effective_max_current == 20
    assert lp.mode == "pv"
    assert lp.charge_total_import == 12.345


def test_fetch_state_returns_loadpoints_3lp(requests_mock):
    requests_mock.get(
        "http://evcc.local:7070/api/state",
        json=_load_fixture("evcc_state_3lp.json"),
    )
    client = EvccClient(host="evcc.local:7070")
    lps = client.fetch_loadpoints()
    titles = [lp.title for lp in lps]
    assert titles == ["Wallbox", "HeatingElement", "Heatpump"]


def test_fetch_raises_on_http_error(requests_mock):
    requests_mock.get(
        "http://192.0.2.10:7070/api/state", status_code=502,
    )
    client = EvccClient(host="192.0.2.10:7070")
    with pytest.raises(EvccUnreachable):
        client.fetch_loadpoints()


def test_fetch_raises_on_connection_error(requests_mock):
    requests_mock.get(
        "http://192.0.2.10:7070/api/state",
        exc=requests.exceptions.ConnectTimeout,
    )
    client = EvccClient(host="192.0.2.10:7070")
    with pytest.raises(EvccUnreachable):
        client.fetch_loadpoints()


def test_fetch_raises_on_invalid_json(requests_mock):
    requests_mock.get(
        "http://192.0.2.10:7070/api/state",
        text="<html>not json</html>",
        headers={"content-type": "text/html"},
    )
    client = EvccClient(host="192.0.2.10:7070")
    with pytest.raises(EvccUnreachable):
        client.fetch_loadpoints()


def test_missing_loadpoints_key_returns_empty():
    assert _parse_loadpoints({}) == []


def test_loadpoints_value_none_returns_empty():
    assert _parse_loadpoints({"loadpoints": None}) == []


def test_missing_optional_fields_use_defaults():
    lps = _parse_loadpoints({
        "loadpoints": [{"title": "Test", "mode": "pv"}]
    })
    assert len(lps) == 1
    assert lps[0].title == "Test"
    assert lps[0].charge_currents == [0, 0, 0]
    assert lps[0].connected is False
    assert lps[0].charge_power == 0.0
    assert lps[0].charge_total_import == 0.0
    assert lps[0].effective_max_current == 16


def test_short_chargecurrents_padded_to_3():
    lps = _parse_loadpoints({
        "loadpoints": [{"title": "X", "chargeCurrents": [5.0]}]
    })
    assert lps[0].charge_currents == [5.0, 0, 0]


def test_long_chargecurrents_truncated_to_3():
    lps = _parse_loadpoints({
        "loadpoints": [{"title": "X", "chargeCurrents": [1, 2, 3, 4, 5]}]
    })
    assert lps[0].charge_currents == [1, 2, 3]


def test_client_reuses_session_across_calls(requests_mock):
    requests_mock.get(
        "http://192.0.2.10:7070/api/state",
        json={"loadpoints": []},
    )
    client = EvccClient(host="192.0.2.10:7070")
    s1 = client.session
    client.fetch_loadpoints()
    client.fetch_loadpoints()
    s2 = client.session
    assert s1 is s2
    assert isinstance(s1, requests.Session)


def test_client_close_releases_session():
    client = EvccClient(host="192.0.2.10:7070")
    s = client.session
    client.close()
    assert client.session is not s
