from unittest.mock import MagicMock

from dbus_service import (
    MODE_AUTO,
    MODE_MANUAL,
    STATUS_CHARGING,
    STATUS_CONNECTED,
    STATUS_DISCONNECTED,
    LoadpointDbusService,
)
from evcc_api import Loadpoint


def _make_svc(monkeypatch, deviceinstance=56, title="HeatingElement"):
    fake_vedbus = MagicMock()
    fake_vedbus.__enter__ = MagicMock(return_value=fake_vedbus)
    fake_vedbus.__exit__ = MagicMock(return_value=False)
    fake_vedbus.register = MagicMock()

    captured = {}

    def factory(name, bus=None, register=None):
        captured["name"] = name
        captured["bus"] = bus
        captured["register"] = register
        return fake_vedbus

    monkeypatch.setattr("dbus_service.VeDbusService", factory)
    fake_bus = MagicMock(name="SharedBus")
    svc = LoadpointDbusService(
        service_name="com.victronenergy.evcharger.http_id%02d" % deviceinstance,
        device_instance=deviceinstance,
        title=title,
        bus=fake_bus,
    )
    return svc, fake_vedbus, captured


def test_create_uses_register_false(monkeypatch):
    svc, vedbus, captured = _make_svc(monkeypatch)
    assert captured["register"] is False


def test_create_passes_shared_bus(monkeypatch):
    svc, vedbus, captured = _make_svc(monkeypatch)
    assert captured["bus"] is not None


def test_register_called_after_all_mandatory_paths(monkeypatch):
    svc, vedbus, captured = _make_svc(monkeypatch)
    mandatory = [
        "/DeviceInstance",
        "/ProductId",
        "/ProductName",
        "/Connected",
        "/Mgmt/ProcessName",
        "/Mgmt/ProcessVersion",
        "/Mgmt/Connection",
    ]
    register_calls = [c for c in vedbus.mock_calls if c[0] == "register"]
    assert len(register_calls) == 1
    register_idx = next(
        i for i, c in enumerate(vedbus.mock_calls) if c[0] == "register"
    )
    for path in mandatory:
        path_idx = next(
            i for i, c in enumerate(vedbus.mock_calls)
            if c[0] == "add_path" and c.args and c.args[0] == path
        )
        assert path_idx < register_idx, (
            "add_path(%r) must happen BEFORE register()" % path
        )


def test_create_registers_mandatory_paths(monkeypatch):
    svc, vedbus, captured = _make_svc(monkeypatch)
    added = [c.args[0] for c in vedbus.add_path.call_args_list]
    for required in [
        "/DeviceInstance", "/ProductId", "/ProductName",
        "/CustomName", "/Connected", "/UpdateIndex",
        "/Ac/Power", "/Ac/L1/Power", "/Ac/L2/Power", "/Ac/L3/Power",
        "/Current", "/SetCurrent", "/MaxCurrent",
        "/Mode", "/Status",
        "/Ac/Energy/Forward", "/ChargingTime",
    ]:
        assert required in added, "Missing path: " + required


def test_update_disconnected_preserves_cumulative_counters(monkeypatch):
    svc, vedbus, _ = _make_svc(monkeypatch)
    vedbus.__getitem__.return_value = 0
    lp = Loadpoint(title="HeatingElement", connected=False, charging=False, mode="pv")
    svc.update(lp)
    sets_paths = [c.args[0] for c in vedbus.__setitem__.call_args_list]
    sets = dict(c.args for c in vedbus.__setitem__.call_args_list)
    assert sets["/Status"] == STATUS_DISCONNECTED
    assert sets["/Connected"] == 1
    assert sets["/Ac/Power"] == 0.0
    assert "/Ac/Energy/Forward" not in sets_paths
    assert "/ChargingTime" not in sets_paths


def test_update_connected_not_charging(monkeypatch):
    svc, vedbus, _ = _make_svc(monkeypatch)
    vedbus.__getitem__.return_value = 0
    lp = Loadpoint(title="HeatingElement", connected=True, charging=False, mode="pv")
    svc.update(lp)
    sets = dict(c.args for c in vedbus.__setitem__.call_args_list)
    assert sets["/Status"] == STATUS_CONNECTED


def test_update_charging_uses_per_phase_voltages(monkeypatch):
    svc, vedbus, _ = _make_svc(monkeypatch)
    vedbus.__getitem__.return_value = 5
    lp = Loadpoint(
        title="HeatingElement", connected=True, charging=True, mode="pv",
        charge_power=4500.0,
        charge_currents=[6.5, 6.5, 6.5],
        charge_voltages=[229.0, 231.0, 232.5],
        effective_max_current=20,
        charged_energy=1800.0,
        charge_duration_ns=3_600_000_000_000,
    )
    svc.update(lp)
    sets = dict(c.args for c in vedbus.__setitem__.call_args_list)
    assert sets["/Status"] == STATUS_CHARGING
    assert sets["/Ac/Power"] == 4500.0
    assert sets["/Ac/L1/Power"] == 6.5 * 229.0
    assert sets["/Ac/L2/Power"] == 6.5 * 231.0
    assert sets["/Ac/L3/Power"] == 6.5 * 232.5
    assert abs(sets["/Ac/Voltage"] - (229.0 + 231.0 + 232.5) / 3) < 0.01
    assert sets["/Current"] == 19.5
    assert sets["/MaxCurrent"] == 20
    assert sets["/Mode"] == MODE_AUTO
    # UNVERIFIED unit: chargedEnergy treated as Wh; must confirm against
    # a live EVCC /api/state sample before merging the deploy.
    assert sets["/Ac/Energy/Forward"] == 1.8
    assert sets["/ChargingTime"] == 3600
    assert sets["/UpdateIndex"] == 6


def test_update_index_wraps_at_255(monkeypatch):
    svc, vedbus, _ = _make_svc(monkeypatch)
    vedbus.__getitem__.return_value = 255
    lp = Loadpoint(title="HeatingElement", connected=True, charging=True, mode="pv")
    svc.update(lp)
    sets = dict(c.args for c in vedbus.__setitem__.call_args_list)
    assert sets["/UpdateIndex"] == 0


def test_off_mode_sets_mode_manual(monkeypatch):
    svc, vedbus, _ = _make_svc(monkeypatch)
    vedbus.__getitem__.return_value = 0
    lp = Loadpoint(title="HeatingElement", connected=True, charging=False, mode="off")
    svc.update(lp)
    sets = dict(c.args for c in vedbus.__setitem__.call_args_list)
    assert sets["/Mode"] == MODE_MANUAL
    assert sets["/StartStop"] == 0


def test_mark_disconnected_sets_connected_zero(monkeypatch):
    svc, vedbus, _ = _make_svc(monkeypatch)
    svc.mark_disconnected()
    sets = dict(c.args for c in vedbus.__setitem__.call_args_list)
    assert sets["/Connected"] == 0
    assert sets["/Status"] == STATUS_DISCONNECTED


def test_update_uses_context_manager_for_batched_itemschanged(monkeypatch):
    svc, vedbus, _ = _make_svc(monkeypatch)
    lp = Loadpoint(
        title="HeatingElement", connected=True, charging=True, mode="pv",
        charge_currents=[6.5, 0, 0],
    )
    vedbus.__getitem__.return_value = 0
    vedbus.__enter__.reset_mock()
    vedbus.__exit__.reset_mock()
    vedbus.__setitem__.reset_mock()
    svc.update(lp)
    vedbus.__enter__.assert_called_once()
    vedbus.__exit__.assert_called_once()
    calls = vedbus.mock_calls
    enter_idx = next(i for i, c in enumerate(calls) if c[0] == "__enter__")
    exit_idx = next(i for i, c in enumerate(calls) if c[0] == "__exit__")
    setitem_indices = [i for i, c in enumerate(calls) if c[0] == "__setitem__"]
    assert all(enter_idx < i < exit_idx for i in setitem_indices), \
        "All property sets must happen inside the context manager"


def test_mark_disconnected_also_uses_context_manager(monkeypatch):
    svc, vedbus, _ = _make_svc(monkeypatch)
    vedbus.__enter__.reset_mock()
    vedbus.__exit__.reset_mock()
    svc.mark_disconnected()
    vedbus.__enter__.assert_called_once()
    vedbus.__exit__.assert_called_once()


def test_service_name_stored(monkeypatch):
    svc, vedbus, captured = _make_svc(monkeypatch, deviceinstance=56)
    assert captured["name"] == "com.victronenergy.evcharger.http_id56"
    assert svc.service_name == "com.victronenergy.evcharger.http_id56"
    assert svc.device_instance == 56


def test_unknown_mode_falls_back_to_manual_with_start(monkeypatch):
    """A mode value EVCC may introduce in the future (e.g. 'now') must not
    crash the bridge. Fallback: manual mode, StartStop=1 (charging allowed)."""
    svc, vedbus, _ = _make_svc(monkeypatch)
    vedbus.__getitem__.return_value = 0
    lp = Loadpoint(
        title="HeatingElement", connected=True, charging=False, mode="now",
    )
    svc.update(lp)
    sets = dict(c.args for c in vedbus.__setitem__.call_args_list)
    assert sets["/Mode"] == MODE_MANUAL
    assert sets["/StartStop"] == 1
    assert sets["/Status"] == STATUS_CONNECTED


def test_minpv_mode_treated_as_pv(monkeypatch):
    """EVCC's 'minpv' mode is still automatic PV charging; our 'pv' in mode
    branch covers it because the substring 'pv' appears in 'minpv'."""
    svc, vedbus, _ = _make_svc(monkeypatch)
    vedbus.__getitem__.return_value = 0
    lp = Loadpoint(
        title="HeatingElement", connected=True, charging=True, mode="minpv",
    )
    svc.update(lp)
    sets = dict(c.args for c in vedbus.__setitem__.call_args_list)
    assert sets["/Mode"] == MODE_AUTO
    assert sets["/StartStop"] == 1


def test_default_mgmt_connection_is_evcc_rest_api(monkeypatch):
    svc, vedbus, _ = _make_svc(monkeypatch)
    added = {c.args[0]: c.args[1] for c in vedbus.add_path.call_args_list
             if len(c.args) >= 2}
    assert added["/Mgmt/Connection"] == "EVCC REST API"


def test_custom_mgmt_connection_is_used(monkeypatch):
    fake_vedbus = MagicMock()
    fake_vedbus.__enter__ = MagicMock(return_value=fake_vedbus)
    fake_vedbus.__exit__ = MagicMock(return_value=False)
    fake_vedbus.register = MagicMock()
    monkeypatch.setattr(
        "dbus_service.VeDbusService",
        lambda name, bus=None, register=None: fake_vedbus,
    )
    LoadpointDbusService(
        service_name="com.victronenergy.evcharger.http_id40",
        device_instance=40,
        title="Carport",
        bus=MagicMock(),
        mgmt_connection="Modbus TCP 172.20.4.135",
    )
    added = {c.args[0]: c.args[1] for c in fake_vedbus.add_path.call_args_list
             if len(c.args) >= 2}
    assert added["/Mgmt/Connection"] == "Modbus TCP 172.20.4.135"
