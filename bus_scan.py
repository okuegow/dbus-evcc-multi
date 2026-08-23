"""Read-only look at the evcharger services currently on the system bus.

Used before a migration: while the legacy single-loadpoint bridges are still
running we note their DeviceInstance and their /Ac/Energy/Forward, so the new
bridge can continue those counters instead of restarting them (see energy.py).

Kept apart from sync.py so the CLI tools can import it without pulling in the
poll loop, and so tests can inject a fake bus.
"""
from __future__ import annotations

import logging
from typing import Dict, NamedTuple

from log_setup import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)

EVCHARGER_PREFIX = "com.victronenergy.evcharger."
BUSITEM = "com.victronenergy.BusItem"


class ExistingCharger(NamedTuple):
    service: str
    deviceinstance: int
    energy_forward: float
    custom_name: str


def _get(bus, service, path, default=None):
    try:
        obj = bus.get_object(service, path)
        return obj.GetValue(dbus_interface=BUSITEM)
    except Exception:
        return default


def scan_evchargers(bus) -> Dict[int, ExistingCharger]:
    """{DeviceInstance: ExistingCharger} for every evcharger service on the bus.

    Services we cannot read are skipped with a warning rather than raising:
    this runs on a live GX device where any driver may come and go.
    """
    found: Dict[int, ExistingCharger] = {}
    try:
        names = bus.list_names()
    except Exception as e:
        logger.warning("Could not enumerate D-Bus names: %s", e)
        return found

    for name in names:
        service = str(name)
        if not service.startswith(EVCHARGER_PREFIX):
            continue
        raw_di = _get(bus, service, "/DeviceInstance")
        if raw_di is None:
            logger.warning("%s has no readable /DeviceInstance - skipped", service)
            continue
        try:
            di = int(raw_di)
        except (TypeError, ValueError):
            logger.warning("%s reports a non-integer DeviceInstance %r - skipped",
                           service, raw_di)
            continue
        try:
            energy = float(_get(bus, service, "/Ac/Energy/Forward", 0.0) or 0.0)
        except (TypeError, ValueError):
            energy = 0.0
        name_value = _get(bus, service, "/CustomName", "") or ""
        found[di] = ExistingCharger(
            service=service, deviceinstance=di, energy_forward=energy,
            custom_name=str(name_value),
        )
    return found
