"""End-to-end smoke: API + State + Sync with the D-Bus layer mocked."""
import json
from pathlib import Path
from unittest.mock import MagicMock


from evcc_api import EvccClient
from state_store import StateStore
from sync import LoadpointSync


def test_two_polls_full_cycle(tmp_path, requests_mock, monkeypatch):
    fixture = (
        Path(__file__).parent / "fixtures" / "evcc_state_3lp.json"
    ).read_text()

    instances = {}

    def factory(service_name, device_instance, title, bus=None,
                mgmt_connection=None):
        m = MagicMock()
        m.service_name = service_name
        m.device_instance = device_instance
        m.title = title
        m.bus = bus
        instances[title] = m
        return m

    monkeypatch.setattr("sync.LoadpointDbusService", factory)

    requests_mock.get("http://evcc:7070/api/state", text=fixture)
    client = EvccClient("evcc:7070")
    store = StateStore(tmp_path / "state.json", di_range=(40, 59))
    sync_ = LoadpointSync(client, store, bus_factory=lambda: MagicMock(name="bus"))
    sync_.tick()
    sync_.tick()

    state = json.loads((tmp_path / "state.json").read_text())
    assert set(state.keys()) == {"Wallbox", "Heizstab", "Heatpump"}
    for di in state.values():
        assert 40 <= di <= 59
    for inst in instances.values():
        assert inst.update.call_count == 2


def test_smoke_handles_evcc_drop_and_recover(tmp_path, requests_mock, monkeypatch):
    """Full glue still works when EVCC blips out and back."""
    import requests
    fixture_3lp = (
        Path(__file__).parent / "fixtures" / "evcc_state_3lp.json"
    ).read_text()
    fixture_1lp = (
        Path(__file__).parent / "fixtures" / "evcc_state_1lp.json"
    ).read_text()

    instances = {}

    def factory(service_name, device_instance, title, bus=None,
                mgmt_connection=None):
        m = MagicMock()
        m.service_name = service_name
        m.device_instance = device_instance
        m.title = title
        instances[title] = m
        return m

    monkeypatch.setattr("sync.LoadpointDbusService", factory)

    sync_ = LoadpointSync(
        EvccClient("evcc:7070"),
        StateStore(tmp_path / "state.json"),
        bus_factory=lambda: MagicMock(name="bus"),
    )

    # tick 1: 3 LPs
    requests_mock.get("http://evcc:7070/api/state", text=fixture_3lp)
    sync_.tick()
    assert set(instances) == {"Wallbox", "Heizstab", "Heatpump"}

    # tick 2: EVCC unreachable - nothing changes, no crash
    requests_mock.get(
        "http://evcc:7070/api/state",
        exc=requests.exceptions.ConnectTimeout,
    )
    sync_.tick()
    # tick 3: only Heizstab visible
    requests_mock.get("http://evcc:7070/api/state", text=fixture_1lp)
    sync_.tick()
    instances["Wallbox"].mark_disconnected.assert_called_once()
    # tick 4: all back
    requests_mock.get("http://evcc:7070/api/state", text=fixture_3lp)
    sync_.tick()
    # Wallbox: tick1 update, tick3 mark_disconnected, tick4 update -> 2 updates
    assert instances["Wallbox"].update.call_count == 2
