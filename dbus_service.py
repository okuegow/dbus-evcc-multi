"""Wraps a single VeDbusService instance for one EVCC loadpoint.

VeDbusService is imported from the Victron velib_python tree at module load time
on Venus OS. On macOS the import falls through to None and tests monkeypatch
`dbus_service.VeDbusService` before instantiation.

CRITICAL PERFORMANCE PATTERN: update() and mark_disconnected() wrap their
property sets in `with self._svc as s:`. VeDbusService.__exit__ then fires a
single ItemsChanged signal with the batched diff (Venus OS 2.80+), instead of
N PropertiesChanged signals. This is the largest CPU win on the Cerbo ARMv7.
Reference: mvader on Victron community + dbus-systemcalc-py reference driver.
"""
from __future__ import annotations

import logging
import os
import platform
import sys

from evcc_api import Loadpoint
from log_setup import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)

STATUS_DISCONNECTED = 0
STATUS_CONNECTED = 1
STATUS_CHARGING = 2

MODE_MANUAL = 0
MODE_AUTO = 1

_VICTRON_VELIB = "/opt/victronenergy/dbus-systemcalc-py/ext/velib_python"
if os.path.isdir(_VICTRON_VELIB) and _VICTRON_VELIB not in sys.path:
    sys.path.insert(1, _VICTRON_VELIB)

try:
    from vedbus import VeDbusService  # type: ignore
except ImportError:
    VeDbusService = None  # tests monkeypatch this


def _fmt_w(_path, value):
    return "%.1fW" % round(float(value), 1)


def _fmt_a(_path, value):
    return "%.1fA" % round(float(value), 1)


def _fmt_v(_path, value):
    return "%.1fV" % round(float(value), 1)


def _fmt_kwh(_path, value):
    return "%.2fkWh" % round(float(value), 2)


def _fmt_s(_path, value):
    return "%ss" % value


def _fmt_int(_path, value):
    return str(value)


class LoadpointDbusService:
    PRODUCT_VERSION = "v2.2"

    def __init__(self, service_name, device_instance, title, bus,
                 mgmt_connection="EVCC REST API"):
        self.service_name = service_name
        self.device_instance = device_instance
        self.title = title
        self.mgmt_connection = mgmt_connection
        # register=False -> all mandatory paths added first, then explicit
        # register() so dbusmonitor.py never sees an incomplete service.
        self._svc = VeDbusService(service_name, bus=bus, register=False)
        self._register_paths()
        self._svc.register()

    def _register_paths(self) -> None:
        s = self._svc
        s.add_path("/Mgmt/ProcessName", "dbus-evcc-multi")
        s.add_path(
            "/Mgmt/ProcessVersion",
            "dbus-evcc-multi %s on Python %s"
            % (self.PRODUCT_VERSION, platform.python_version()),
        )
        s.add_path("/Mgmt/Connection", self.mgmt_connection)

        s.add_path("/DeviceInstance", self.device_instance)
        s.add_path("/ProductId", 0xFFFF)
        s.add_path("/ProductName", "EVCC Charger")
        s.add_path("/CustomName", self.title)
        s.add_path("/HardwareVersion", 2)
        s.add_path("/Connected", 1)
        s.add_path("/UpdateIndex", 0)
        s.add_path("/Position", 0)
        s.add_path("/Status", None)
        s.add_path("/Mode", None)
        s.add_path("/StartStop", 0, gettextcallback=_fmt_int, writeable=False)
        s.add_path("/Ac/Power", 0, gettextcallback=_fmt_w, writeable=False)
        s.add_path("/Ac/L1/Power", 0, gettextcallback=_fmt_w, writeable=False)
        s.add_path("/Ac/L2/Power", 0, gettextcallback=_fmt_w, writeable=False)
        s.add_path("/Ac/L3/Power", 0, gettextcallback=_fmt_w, writeable=False)
        s.add_path("/Ac/Voltage", 230, gettextcallback=_fmt_v, writeable=False)
        # /Ac/Energy/Forward + /ChargingTime are CUMULATIVE. On disconnect we
        # keep the last value so VRM history doesn't zero out on cable unplug.
        s.add_path("/Ac/Energy/Forward", 0, gettextcallback=_fmt_kwh, writeable=False)
        s.add_path("/ChargingTime", 0, gettextcallback=_fmt_s, writeable=False)
        s.add_path("/Current", 0, gettextcallback=_fmt_a, writeable=False)
        s.add_path("/SetCurrent", 0, gettextcallback=_fmt_a, writeable=False)
        s.add_path("/MaxCurrent", 0, gettextcallback=_fmt_a, writeable=False)

    def update(self, lp: Loadpoint) -> None:
        with self._svc as s:
            currents = lp.charge_currents
            voltages = lp.charge_voltages
            # Per-phase power = current * per-phase voltage. Real grids run
            # 225-237 V; EVCC reports actuals, so we use them instead of 230.
            s["/Ac/L1/Power"] = float(currents[0]) * float(voltages[0])
            s["/Ac/L2/Power"] = float(currents[1]) * float(voltages[1])
            s["/Ac/L3/Power"] = float(currents[2]) * float(voltages[2])
            s["/Ac/Voltage"] = (
                float(voltages[0]) + float(voltages[1]) + float(voltages[2])
            ) / 3.0
            s["/Ac/Power"] = float(lp.charge_power)

            total_current = sum(float(c) for c in currents)
            s["/Current"] = total_current
            s["/SetCurrent"] = total_current
            s["/MaxCurrent"] = int(lp.effective_max_current)

            if "pv" in lp.mode:
                s["/Mode"] = MODE_AUTO
                s["/StartStop"] = 1
            elif lp.mode == "off":
                s["/Mode"] = MODE_MANUAL
                s["/StartStop"] = 0
            else:
                s["/Mode"] = MODE_MANUAL
                s["/StartStop"] = 1

            if not lp.connected:
                status = STATUS_DISCONNECTED
            elif lp.charging:
                status = STATUS_CHARGING
            else:
                status = STATUS_CONNECTED
            s["/Status"] = status
            s["/Connected"] = 1

            # Cumulative counters only refreshed while connected. UNVERIFIED:
            # chargedEnergy is treated as Wh (-> /1000 = kWh). Must be confirmed
            # against a real EVCC /api/state sample before deploy.
            if status != STATUS_DISCONNECTED:
                s["/Ac/Energy/Forward"] = float(lp.charged_energy) / 1000.0
                s["/ChargingTime"] = int(lp.charge_duration_ns) // 1_000_000_000

            idx = (int(s["/UpdateIndex"]) + 1) % 256
            s["/UpdateIndex"] = idx

    def mark_disconnected(self) -> None:
        """Loadpoint no longer present in EVCC. Keep the service alive
        (preserves VRM identity) but flag it offline.
        """
        with self._svc as s:
            s["/Connected"] = 0
            s["/Status"] = STATUS_DISCONNECTED
