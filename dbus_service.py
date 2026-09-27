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

from energy import EnergyCounter
from evcc_api import Loadpoint
from log_setup import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)

# Venus OS Evcs_Status (gui-v2 src/enums.h). We only ever set the subset that
# is derivable from EVCC's loadpoint state; the error codes belong to real
# wallbox hardware.
STATUS_DISCONNECTED = 0
STATUS_CONNECTED = 1
STATUS_CHARGING = 2
STATUS_CHARGED = 3
STATUS_WAITING_FOR_SUN = 4
STATUS_WAITING_FOR_START = 6
STATUS_SWITCHING_TO_3P = 22
STATUS_SWITCHING_TO_1P = 23

MODE_MANUAL = 0
MODE_AUTO = 1
MODE_SCHEDULED = 2

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


def evcc_status(lp: Loadpoint) -> int:
    """Map an EVCC loadpoint onto a Venus OS Evcs_Status code.

    Only states EVCC actually reports are produced. Deliberately NOT mapped:
    Evcs_Status_LowStateOfCharge (7), which on Victron hardware means the
    SYSTEM battery is too low to charge - EVCC's minSocNotReached is about the
    vehicle and means the opposite (charge now, regardless of PV).
    """
    if not lp.connected:
        return STATUS_DISCONNECTED

    if lp.charging:
        if lp.phase_action == "scale3p":
            return STATUS_SWITCHING_TO_3P
        if lp.phase_action == "scale1p":
            return STATUS_SWITCHING_TO_1P
        return STATUS_CHARGING

    # Connected, not charging. Why not?
    if lp.vehicle_soc > 0 and lp.limit_soc > 0 and lp.vehicle_soc >= lp.limit_soc:
        return STATUS_CHARGED
    if "pv" in lp.mode:
        # minpv/pv with no charge running = waiting for surplus.
        return STATUS_WAITING_FOR_SUN
    if not lp.enabled:
        return STATUS_WAITING_FOR_START
    return STATUS_CONNECTED


def evcc_mode(lp: Loadpoint) -> int:
    """Map an EVCC loadpoint mode onto a Venus OS Evcs_Mode code."""
    if lp.plan_active:
        return MODE_SCHEDULED
    if "pv" in lp.mode:
        return MODE_AUTO
    return MODE_MANUAL


class LoadpointDbusService:
    PRODUCT_VERSION = "v2.6"

    def __init__(self, service_name, device_instance, title, bus,
                 mgmt_connection="EVCC REST API", ac_position=0,
                 evcc_version="", energy=None):
        self.service_name = service_name
        self.device_instance = device_instance
        self.title = title
        self.mgmt_connection = mgmt_connection
        self.ac_position = ac_position
        self.evcc_version = evcc_version
        # Keeps /Ac/Energy/Forward monotonic and continuous. Without one we
        # would publish EVCC's raw counter, which jumps whenever the source
        # field or the underlying install changes (see energy.py).
        self.energy = energy if energy is not None else EnergyCounter(title=title)
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
        # VRM logs these three as device identity (vrmlogger/datalist.py).
        # There is no hardware behind us, so Model is constant and Serial is
        # derived from the loadpoint title, which is our identity anyway.
        s.add_path("/Model", "EVCC loadpoint")
        s.add_path("/Serial", "evcc-%s" % self.title)
        s.add_path("/FirmwareVersion", self.evcc_version or "unknown")
        s.add_path("/Connected", 1)
        s.add_path("/UpdateIndex", 0)
        s.add_path("/Position", self.ac_position)
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
        # gui-v2 (Venus OS 3.5x+) reads the CURRENT SESSION from /Session/*,
        # not from the cumulative paths above: /Session/Energy in kWh,
        # /Session/Time in seconds. Without them the new GUI shows no session
        # energy and no charging time for our chargers.
        s.add_path("/Session/Energy", 0, gettextcallback=_fmt_kwh, writeable=False)
        s.add_path("/Session/Time", 0, gettextcallback=_fmt_s, writeable=False)
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

            s["/Mode"] = evcc_mode(lp)
            s["/StartStop"] = 0 if lp.mode == "off" else 1

            status = evcc_status(lp)
            s["/Status"] = status
            s["/Connected"] = 1

            # chargeTotalImport is EVCC's cumulative kWh meter value. It keeps
            # increasing for IntegratedDevice loadpoints (heatpump, sgready-
            # relay, …) where chargedEnergy stays at 0 because EVCC has no
            # session semantics for "always connected" chargers. Fall back to
            # chargedEnergy / 1000 when the meter value is missing.
            total_import = float(lp.charge_total_import or 0.0)
            if total_import > 0:
                candidate = total_import
            elif status != STATUS_DISCONNECTED:
                candidate = float(lp.charged_energy) / 1000.0
            else:
                candidate = None

            if candidate is not None:
                # EnergyCounter handles monotonicity, source switches and
                # taking over a legacy service's counter. The extra max()
                # against the published value guards a restart within the
                # same source.
                published = self.energy.value(candidate)
                previous = float(s["/Ac/Energy/Forward"] or 0.0)
                s["/Ac/Energy/Forward"] = max(published, previous)

            # Session values come straight from EVCC and reset with the
            # session, so they are published in every state - including
            # DISCONNECTED, where EVCC zeroes them.
            session_seconds = int(lp.charge_duration_s)
            s["/Session/Energy"] = float(lp.charged_energy) / 1000.0
            s["/Session/Time"] = session_seconds
            if status != STATUS_DISCONNECTED:
                s["/ChargingTime"] = session_seconds

            idx = (int(s["/UpdateIndex"]) + 1) % 256
            s["/UpdateIndex"] = idx

    def mark_disconnected(self) -> None:
        """Loadpoint no longer present in EVCC. Keep the service alive
        (preserves VRM identity) but flag it offline.
        """
        with self._svc as s:
            s["/Connected"] = 0
            s["/Status"] = STATUS_DISCONNECTED
