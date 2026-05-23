import logging
from unittest.mock import MagicMock

import pytest

from log_setup import LOGGER_NAME
from sync import preflight_check_di_collisions


def _make_bus(names, di_by_name=None, fail_list_names=False):
    bus = MagicMock(name="SystemBus")
    if fail_list_names:
        bus.list_names.side_effect = RuntimeError("bus unhappy")
    else:
        bus.list_names.return_value = names
    di_by_name = di_by_name or {}

    def get_object(name, _path):
        if name in di_by_name:
            obj = MagicMock()
            obj.GetValue.return_value = di_by_name[name]
            return obj
        obj = MagicMock()
        obj.GetValue.side_effect = RuntimeError("no /DeviceInstance")
        return obj

    bus.get_object.side_effect = get_object
    return bus


def test_no_collision_returns_silently():
    bus = _make_bus(names=[], di_by_name={})
    preflight_check_di_collisions(bus, di_range=(40, 59))


def test_collision_raises_runtime_error():
    bus = _make_bus(
        names=["com.victronenergy.evcharger.foo"],
        di_by_name={"com.victronenergy.evcharger.foo": 45},
    )
    with pytest.raises(RuntimeError, match="40-59"):
        preflight_check_di_collisions(bus, di_range=(40, 59))


def test_non_evcharger_services_ignored():
    bus = _make_bus(
        names=["com.victronenergy.battery.foo", "org.freedesktop.Avahi"],
        di_by_name={"com.victronenergy.battery.foo": 45},
    )
    preflight_check_di_collisions(bus, di_range=(40, 59))


def test_own_service_ignored():
    """A previous run of dbus-evcc-multi on the same DI must not block startup."""
    bus = _make_bus(
        names=["com.victronenergy.evcharger.dbus-evcc-multi.http_id40"],
        di_by_name={
            "com.victronenergy.evcharger.dbus-evcc-multi.http_id40": 40,
        },
    )
    preflight_check_di_collisions(bus, di_range=(40, 59))


def test_unreadable_deviceinstance_skipped():
    """A foreign evcharger service that does not expose /DeviceInstance is
    skipped rather than crashing the preflight."""
    bus = _make_bus(
        names=["com.victronenergy.evcharger.legacy.bogus"],
        di_by_name={},  # GetValue will raise
    )
    preflight_check_di_collisions(bus, di_range=(40, 59))


def test_di_outside_range_allowed():
    bus = _make_bus(
        names=["com.victronenergy.evcharger.foo"],
        di_by_name={"com.victronenergy.evcharger.foo": 99},
    )
    preflight_check_di_collisions(bus, di_range=(40, 59))


def test_list_names_failure_logs_warning_but_returns(
    caplog, propagate_app_logger
):
    bus = _make_bus(names=[], fail_list_names=True)
    caplog.set_level(logging.WARNING, logger=LOGGER_NAME)
    preflight_check_di_collisions(bus, di_range=(40, 59))
    assert any(
        "enumerate" in r.message.lower() for r in caplog.records
    )


def test_collision_lists_all_offenders():
    bus = _make_bus(
        names=[
            "com.victronenergy.evcharger.foo",
            "com.victronenergy.evcharger.bar",
        ],
        di_by_name={
            "com.victronenergy.evcharger.foo": 41,
            "com.victronenergy.evcharger.bar": 55,
        },
    )
    with pytest.raises(RuntimeError) as exc:
        preflight_check_di_collisions(bus, di_range=(40, 59))
    msg = str(exc.value)
    assert "foo" in msg
    assert "bar" in msg
    assert "41" in msg
    assert "55" in msg
