import logging
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests

from evcc_api import EvccClient
from log_setup import LOGGER_NAME
from state_store import StateStore
from sync import LoadpointSync

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def mock_dbus_service_class(monkeypatch):
    """Replaces LoadpointDbusService so no real D-Bus is touched."""
    instances = {}

    def factory(service_name, device_instance, title, bus=None,
                mgmt_connection=None):
        m = MagicMock(name="DbusSvc[" + title + "]")
        m.service_name = service_name
        m.device_instance = device_instance
        m.title = title
        m.bus = bus
        m.mgmt_connection = mgmt_connection
        instances[title] = m
        return m

    monkeypatch.setattr("sync.LoadpointDbusService", factory)
    return instances


def _arm(requests_mock, fixture_name):
    requests_mock.get(
        "http://evcc:7070/api/state",
        text=(FIXTURES / fixture_name).read_text(),
    )


def test_first_tick_creates_services_for_all_loadpoints(
    tmp_path, requests_mock, mock_dbus_service_class
):
    _arm(requests_mock, "evcc_state_3lp.json")
    client = EvccClient(host="evcc:7070")
    store = StateStore(tmp_path / "state.json", di_range=(40, 59))
    sync_ = LoadpointSync(client, store, bus_factory=lambda: MagicMock(name="bus"))
    sync_.tick()
    assert set(mock_dbus_service_class) == {"Wallbox", "Heizstab", "Heatpump"}
    for svc in mock_dbus_service_class.values():
        svc.update.assert_called_once()


def test_each_loadpoint_gets_a_distinct_bus_connection(
    tmp_path, requests_mock, mock_dbus_service_class
):
    """Regression: every VeDbusService must own its OWN dbus connection.

    dbus-python forbids registering the object path '/' twice on a single
    connection (VeDbusService creates a VeDbusRootExport at '/' in __init__).
    A shared connection therefore makes every loadpoint after the first fail
    to register with: "Can't register the object-path handler for '/'".
    bus_factory() must hand out a fresh connection per loadpoint.
    """
    _arm(requests_mock, "evcc_state_3lp.json")
    sync_ = LoadpointSync(
        EvccClient("evcc:7070"),
        StateStore(tmp_path / "state.json", di_range=(40, 59)),
        bus_factory=lambda: object(),  # fresh connection object per call
    )
    sync_.tick()
    buses = [m.bus for m in mock_dbus_service_class.values()]
    assert len(buses) == 3
    assert len({id(b) for b in buses}) == 3, "each loadpoint needs its own bus"


def test_second_tick_reuses_existing_services(
    tmp_path, requests_mock, mock_dbus_service_class
):
    _arm(requests_mock, "evcc_state_3lp.json")
    sync_ = LoadpointSync(
        EvccClient("evcc:7070"),
        StateStore(tmp_path / "state.json"),
        bus_factory=lambda: MagicMock(name="bus"),
    )
    sync_.tick()
    sync_.tick()
    assert len(mock_dbus_service_class) == 3
    for svc in mock_dbus_service_class.values():
        assert svc.update.call_count == 2


def test_reordering_keeps_device_instances_stable(
    tmp_path, requests_mock, mock_dbus_service_class
):
    p = tmp_path / "state.json"
    _arm(requests_mock, "evcc_state_3lp.json")
    sync_ = LoadpointSync(
        EvccClient("evcc:7070"), StateStore(p), bus_factory=lambda: MagicMock(name="bus")
    )
    sync_.tick()
    di_before = {t: m.device_instance for t, m in mock_dbus_service_class.items()}

    mock_dbus_service_class.clear()
    _arm(requests_mock, "evcc_state_reordered.json")
    sync_2 = LoadpointSync(
        EvccClient("evcc:7070"), StateStore(p), bus_factory=lambda: MagicMock(name="bus")
    )
    sync_2.tick()
    di_after = {t: m.device_instance for t, m in mock_dbus_service_class.items()}
    assert di_after == di_before


def test_disappeared_loadpoint_marked_disconnected_not_destroyed(
    tmp_path, requests_mock, mock_dbus_service_class
):
    _arm(requests_mock, "evcc_state_3lp.json")
    sync_ = LoadpointSync(
        EvccClient("evcc:7070"),
        StateStore(tmp_path / "state.json"),
        bus_factory=lambda: MagicMock(name="bus"),
    )
    sync_.tick()
    _arm(requests_mock, "evcc_state_1lp.json")
    sync_.tick()
    mock_dbus_service_class["Wallbox"].mark_disconnected.assert_called_once()
    mock_dbus_service_class["Heatpump"].mark_disconnected.assert_called_once()
    assert mock_dbus_service_class["Heizstab"].mark_disconnected.call_count == 0


def test_rename_creates_new_service_and_disconnects_old(
    tmp_path, requests_mock, mock_dbus_service_class
):
    p = tmp_path / "state.json"
    _arm(requests_mock, "evcc_state_1lp.json")
    sync_ = LoadpointSync(
        EvccClient("evcc:7070"), StateStore(p), bus_factory=lambda: MagicMock(name="bus")
    )
    sync_.tick()
    assert "Heizstab" in mock_dbus_service_class
    di_old = mock_dbus_service_class["Heizstab"].device_instance

    _arm(requests_mock, "evcc_state_renamed.json")
    sync_.tick()
    assert "Heater" in mock_dbus_service_class
    assert mock_dbus_service_class["Heater"].device_instance != di_old
    mock_dbus_service_class["Heizstab"].mark_disconnected.assert_called_once()


def test_evcc_unreachable_logs_but_does_not_crash(
    tmp_path, requests_mock, mock_dbus_service_class, caplog, propagate_app_logger
):
    requests_mock.get(
        "http://evcc:7070/api/state",
        exc=requests.exceptions.ConnectTimeout,
    )
    sync_ = LoadpointSync(
        EvccClient("evcc:7070"),
        StateStore(tmp_path / "state.json"),
        bus_factory=lambda: MagicMock(name="bus"),
    )
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    result = sync_.tick()
    assert result is True
    assert len(mock_dbus_service_class) == 0
    assert any("unreachable" in r.message.lower() for r in caplog.records)


def test_service_name_pattern_uses_id_prefix(
    tmp_path, requests_mock, mock_dbus_service_class
):
    _arm(requests_mock, "evcc_state_1lp.json")
    sync_ = LoadpointSync(
        EvccClient("evcc:7070"),
        StateStore(tmp_path / "state.json", di_range=(40, 59)),
        bus_factory=lambda: MagicMock(name="bus"),
    )
    sync_.tick()
    svc = mock_dbus_service_class["Heizstab"]
    assert svc.service_name.startswith("com.victronenergy.evcharger.http_id")
    assert svc.service_name == "com.victronenergy.evcharger.http_id40"


def test_duplicate_titles_in_same_poll_are_skipped(
    tmp_path, requests_mock, mock_dbus_service_class, caplog, propagate_app_logger
):
    duplicate_fixture = {
        "loadpoints": [
            {"title": "Heizstab", "mode": "pv", "connected": True, "charging": True,
             "chargeCurrents": [6.5, 0, 0]},
            {"title": "Heizstab", "mode": "pv", "connected": True, "charging": False,
             "chargeCurrents": [0, 0, 0]},
        ]
    }
    requests_mock.get("http://evcc:7070/api/state", json=duplicate_fixture)
    caplog.set_level(logging.ERROR, logger=LOGGER_NAME)
    sync_ = LoadpointSync(
        EvccClient("evcc:7070"),
        StateStore(tmp_path / "state.json"),
        bus_factory=lambda: MagicMock(name="bus"),
    )
    sync_.tick()
    assert "Heizstab" not in mock_dbus_service_class
    error_messages = [r.message for r in caplog.records if r.levelno == logging.ERROR]
    assert any("duplicate" in m.lower() for m in error_messages)


def test_absent_loadpoint_logs_disconnect_only_once(
    tmp_path, requests_mock, mock_dbus_service_class
):
    _arm(requests_mock, "evcc_state_3lp.json")
    sync_ = LoadpointSync(
        EvccClient("evcc:7070"),
        StateStore(tmp_path / "state.json"),
        bus_factory=lambda: MagicMock(name="bus"),
    )
    sync_.tick()
    _arm(requests_mock, "evcc_state_1lp.json")
    sync_.tick()
    sync_.tick()
    sync_.tick()
    assert mock_dbus_service_class["Wallbox"].mark_disconnected.call_count == 1
    assert mock_dbus_service_class["Heatpump"].mark_disconnected.call_count == 1


def test_reappearing_loadpoint_resumes_updates(
    tmp_path, requests_mock, mock_dbus_service_class
):
    """When a loadpoint disappears and later reappears, update() must
    resume and mark_disconnected may fire again on a future disappearance."""
    _arm(requests_mock, "evcc_state_3lp.json")
    sync_ = LoadpointSync(
        EvccClient("evcc:7070"),
        StateStore(tmp_path / "state.json"),
        bus_factory=lambda: MagicMock(name="bus"),
    )
    sync_.tick()
    _arm(requests_mock, "evcc_state_1lp.json")
    sync_.tick()  # Wallbox + Heatpump go offline
    _arm(requests_mock, "evcc_state_3lp.json")
    sync_.tick()  # back online
    # Wallbox: tick1 update, tick2 mark_disconnected, tick3 update -> 2 updates
    assert mock_dbus_service_class["Wallbox"].update.call_count == 2
    _arm(requests_mock, "evcc_state_1lp.json")
    sync_.tick()  # offline again
    assert mock_dbus_service_class["Wallbox"].mark_disconnected.call_count == 2


def test_tick_returns_true_for_glib_loop(
    tmp_path, requests_mock, mock_dbus_service_class
):
    """LoadpointSync.tick() is wired as a GLib.timeout_add_seconds() callback.
    GLib stops the timer if the callback returns False; we always return True."""
    _arm(requests_mock, "evcc_state_1lp.json")
    sync_ = LoadpointSync(
        EvccClient("evcc:7070"),
        StateStore(tmp_path / "state.json"),
        bus_factory=lambda: MagicMock(name="bus"),
    )
    assert sync_.tick() is True


def test_di_exhausted_logs_and_continues(
    tmp_path, requests_mock, mock_dbus_service_class, caplog, propagate_app_logger
):
    """If the DI range is full when a new loadpoint shows up, the loop must
    log an error and move on, not crash."""
    _arm(requests_mock, "evcc_state_3lp.json")
    # Only 2 slots free -> third allocation raises DeviceInstanceExhausted
    sync_ = LoadpointSync(
        EvccClient("evcc:7070"),
        StateStore(tmp_path / "state.json", di_range=(40, 41)),
        bus_factory=lambda: MagicMock(name="bus"),
    )
    caplog.set_level(logging.ERROR, logger=LOGGER_NAME)
    sync_.tick()  # must not raise
    # Two services made it, third did not
    assert len(mock_dbus_service_class) == 2
    err = [r.message for r in caplog.records if r.levelno == logging.ERROR]
    assert any("Cannot allocate DI" in m for m in err)


def test_dbus_service_construction_failure_skipped(
    tmp_path, requests_mock, monkeypatch, caplog, propagate_app_logger
):
    """If VeDbusService construction blows up for one loadpoint, the tick
    must keep going for the others and log the exception."""
    _arm(requests_mock, "evcc_state_3lp.json")

    instances = {}

    def factory(service_name, device_instance, title, bus=None,
                mgmt_connection=None):
        if title == "Heizstab":
            raise RuntimeError("simulated bus failure for Heizstab")
        m = MagicMock()
        m.service_name = service_name
        m.device_instance = device_instance
        m.title = title
        instances[title] = m
        return m

    monkeypatch.setattr("sync.LoadpointDbusService", factory)
    caplog.set_level(logging.ERROR, logger=LOGGER_NAME)
    sync_ = LoadpointSync(
        EvccClient("evcc:7070"),
        StateStore(tmp_path / "state.json"),
        bus_factory=lambda: MagicMock(name="bus"),
    )
    sync_.tick()
    # The two non-failing loadpoints still got services
    assert set(instances) == {"Wallbox", "Heatpump"}
    # Error message captured
    assert any(
        "Failed to register" in r.message and "Heizstab" in r.message
        for r in caplog.records if r.levelno == logging.ERROR
    )


def test_update_exception_in_one_lp_does_not_kill_tick(
    tmp_path, requests_mock, monkeypatch, caplog, propagate_app_logger
):
    """One loadpoint's update() raising must not stop the other loadpoints
    from updating. This is the blast-radius mitigation from the README."""
    _arm(requests_mock, "evcc_state_3lp.json")

    instances = {}

    def factory(service_name, device_instance, title, bus=None,
                mgmt_connection=None):
        m = MagicMock()
        m.service_name = service_name
        m.device_instance = device_instance
        m.title = title
        if title == "Heizstab":
            m.update.side_effect = RuntimeError("simulated update failure")
        instances[title] = m
        return m

    monkeypatch.setattr("sync.LoadpointDbusService", factory)
    caplog.set_level(logging.ERROR, logger=LOGGER_NAME)
    sync_ = LoadpointSync(
        EvccClient("evcc:7070"),
        StateStore(tmp_path / "state.json"),
        bus_factory=lambda: MagicMock(name="bus"),
    )
    sync_.tick()
    instances["Wallbox"].update.assert_called_once()
    instances["Heatpump"].update.assert_called_once()
    assert any(
        "Update failed" in r.message and "Heizstab" in r.message
        for r in caplog.records if r.levelno == logging.ERROR
    )


def test_mark_disconnected_exception_does_not_kill_tick(
    tmp_path, requests_mock, monkeypatch, caplog, propagate_app_logger
):
    """mark_disconnected() raising for one absent loadpoint must not stop
    the loop from marking others."""
    _arm(requests_mock, "evcc_state_3lp.json")

    instances = {}

    def factory(service_name, device_instance, title, bus=None,
                mgmt_connection=None):
        m = MagicMock()
        m.service_name = service_name
        m.device_instance = device_instance
        m.title = title
        if title == "Wallbox":
            m.mark_disconnected.side_effect = RuntimeError(
                "simulated disconnect failure"
            )
        instances[title] = m
        return m

    monkeypatch.setattr("sync.LoadpointDbusService", factory)
    sync_ = LoadpointSync(
        EvccClient("evcc:7070"),
        StateStore(tmp_path / "state.json"),
        bus_factory=lambda: MagicMock(name="bus"),
    )
    sync_.tick()
    _arm(requests_mock, "evcc_state_1lp.json")
    caplog.set_level(logging.ERROR, logger=LOGGER_NAME)
    sync_.tick()
    instances["Wallbox"].mark_disconnected.assert_called_once()
    instances["Heatpump"].mark_disconnected.assert_called_once()
    assert any(
        "mark_disconnected failed" in r.message and "Wallbox" in r.message
        for r in caplog.records if r.levelno == logging.ERROR
    )


def test_mgmt_connection_passed_to_new_services(
    tmp_path, requests_mock, mock_dbus_service_class
):
    _arm(requests_mock, "evcc_state_3lp.json")
    sync_ = LoadpointSync(
        EvccClient("evcc:7070"),
        StateStore(tmp_path / "state.json", di_range=(40, 59)),
        bus_factory=lambda: MagicMock(name="bus"),
        mgmt_connection="Modbus TCP 172.20.4.135",
    )
    sync_.tick()
    for m in mock_dbus_service_class.values():
        assert m.mgmt_connection == "Modbus TCP 172.20.4.135"


def test_mgmt_connection_defaults_to_rest_api(
    tmp_path, requests_mock, mock_dbus_service_class
):
    _arm(requests_mock, "evcc_state_3lp.json")
    sync_ = LoadpointSync(
        EvccClient("evcc:7070"),
        StateStore(tmp_path / "state.json", di_range=(40, 59)),
        bus_factory=lambda: MagicMock(name="bus"),
    )
    sync_.tick()
    for m in mock_dbus_service_class.values():
        assert m.mgmt_connection == "EVCC REST API"
