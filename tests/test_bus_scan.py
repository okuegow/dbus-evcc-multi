from bus_scan import scan_evchargers
from seed_state import collect_counters


class FakeItem:
    def __init__(self, value):
        self.value = value

    def GetValue(self, dbus_interface=None):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class FakeBus:
    """Minimal stand-in for dbus.SystemBus(): names plus per-path values."""

    def __init__(self, services, names=None):
        self.services = services
        self._names = names

    def list_names(self):
        if self._names is not None:
            if isinstance(self._names, Exception):
                raise self._names
            return self._names
        return list(self.services) + ["com.victronenergy.system", "org.bluez"]

    def get_object(self, service, path):
        try:
            return FakeItem(self.services[service][path])
        except KeyError:
            raise RuntimeError("no such path %s on %s" % (path, service))


def _bus():
    return FakeBus({
        "com.victronenergy.evcharger.http_49": {
            "/DeviceInstance": 49, "/Ac/Energy/Forward": 812.4,
            "/CustomName": "Wallbox go-E"},
        "com.victronenergy.evcharger.http_55": {
            "/DeviceInstance": 55, "/Ac/Energy/Forward": 1930.25,
            "/CustomName": "Heatpump"},
    })


def test_scan_returns_chargers_keyed_by_deviceinstance():
    found = scan_evchargers(_bus())
    assert set(found) == {49, 55}
    assert found[49].energy_forward == 812.4
    assert found[49].custom_name == "Wallbox go-E"
    assert found[55].service == "com.victronenergy.evcharger.http_55"


def test_scan_ignores_non_evcharger_services():
    found = scan_evchargers(_bus())
    assert all(f.service.startswith("com.victronenergy.evcharger.") for f in found.values())


def test_scan_skips_service_without_deviceinstance():
    bus = FakeBus({"com.victronenergy.evcharger.broken": {"/Ac/Energy/Forward": 5.0}})
    assert scan_evchargers(bus) == {}


def test_scan_tolerates_unreadable_energy():
    bus = FakeBus({"com.victronenergy.evcharger.http_49": {
        "/DeviceInstance": 49, "/CustomName": "X"}})
    found = scan_evchargers(bus)
    assert found[49].energy_forward == 0.0


def test_scan_survives_unlistable_bus():
    assert scan_evchargers(FakeBus({}, names=RuntimeError("bus down"))) == {}


def test_collect_counters_matches_seeded_dis():
    scan = scan_evchargers(_bus())
    adopt, missing = collect_counters({"Carport": 49, "Waermepumpe": 55}, scan)
    assert adopt == {"Carport": 812.4, "Waermepumpe": 1930.25}
    assert missing == []


def test_collect_counters_reports_dis_without_a_live_service():
    scan = scan_evchargers(_bus())
    adopt, missing = collect_counters({"Carport": 49, "Heizstab": 56}, scan)
    assert adopt == {"Carport": 812.4}
    assert missing == [("Heizstab", 56)]
