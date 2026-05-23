"""Multi-loadpoint orchestrator.

Per Victron mvader pattern: one process holds a dict of VeDbusService
instances, one per loadpoint title. tick() polls /api/state, reconciles
the dict, and pushes per-loadpoint updates.

Service-name pattern: com.victronenergy.evcharger.http_id<NN>
(consistent with dbus-mqtt-devices precedent and dbus-evcc-lp1).
"""
from __future__ import annotations

import logging
from typing import Dict, Set

from dbus_service import LoadpointDbusService
from evcc_api import EvccClient, EvccUnreachable
from log_setup import LOGGER_NAME
from state_store import DeviceInstanceExhausted, StateStore

logger = logging.getLogger(LOGGER_NAME)

SERVICE_NAME_TEMPLATE = "com.victronenergy.evcharger.http_id%02d"


def preflight_check_di_collisions(bus, di_range) -> None:
    """Scan the system bus for existing com.victronenergy.evcharger.* services
    and read their /DeviceInstance. Raise RuntimeError if any overlap our
    configured range. Otherwise the user gets a silent per-loadpoint skip at
    runtime instead of a clear startup error.

    Pure function, no global state. bus is a dbus.SystemBus() instance.
    """
    lo, hi = di_range
    try:
        names = bus.list_names()
    except Exception as e:
        logger.warning("Could not enumerate D-Bus names for preflight: %s", e)
        return

    collisions = []
    for name in names:
        sname = str(name)
        if not sname.startswith("com.victronenergy.evcharger."):
            continue
        if "dbus-evcc-multi" in sname:
            continue  # our own previous instance, fine
        try:
            obj = bus.get_object(sname, "/DeviceInstance")
            di = int(obj.GetValue(dbus_interface="com.victronenergy.BusItem"))
        except Exception:
            continue
        if lo <= di <= hi:
            collisions.append((sname, di))
    if collisions:
        raise RuntimeError(
            "Existing evcharger services occupy DeviceInstances in range "
            "%d-%d: %s" % (lo, hi, collisions)
        )


class LoadpointSync:
    def __init__(self, client: EvccClient, store: StateStore, bus_factory,
                 mgmt_connection: str = "EVCC REST API") -> None:
        # bus_factory() returns a FRESH dbus connection per loadpoint. A single
        # shared connection cannot host more than one VeDbusService: each one
        # registers a VeDbusRootExport at object path '/', and dbus-python
        # rejects a second handler for '/' on the same connection. So every
        # loadpoint gets its own connection.
        self.client = client
        self.store = store
        self.bus_factory = bus_factory
        self.mgmt_connection = mgmt_connection
        self._services: Dict[str, LoadpointDbusService] = {}
        self._offline_titles: Set[str] = set()

    def tick(self) -> bool:
        """Returns True so GLib.timeout_add_seconds keeps calling us."""
        try:
            loadpoints = self.client.fetch_loadpoints()
        except EvccUnreachable as e:
            logger.warning("EVCC unreachable: %s", e)
            return True

        # SF5: duplicate-title detection. EVCC technically allows duplicate
        # titles; our identity model collapses them into one service.
        title_counts: Dict[str, int] = {}
        for lp in loadpoints:
            title_counts[lp.title] = title_counts.get(lp.title, 0) + 1
        duplicates = {t for t, n in title_counts.items() if n > 1}
        if duplicates:
            logger.error(
                "EVCC returned duplicate loadpoint titles %s - they will be "
                "skipped. Rename them in EVCC so each loadpoint has a unique "
                "title.",
                sorted(duplicates),
            )

        seen_titles: Set[str] = set()
        for lp in loadpoints:
            if lp.title in duplicates:
                continue
            seen_titles.add(lp.title)
            svc = self._services.get(lp.title)
            if svc is None:
                try:
                    di = self.store.get_or_allocate(lp.title)
                except DeviceInstanceExhausted as e:
                    logger.error("Cannot allocate DI for %r: %s", lp.title, e)
                    continue
                service_name = SERVICE_NAME_TEMPLATE % di
                logger.info(
                    "Registering new D-Bus service %r (DI=%d) for loadpoint %r",
                    service_name, di, lp.title,
                )
                try:
                    svc = LoadpointDbusService(
                        service_name, di, lp.title, bus=self.bus_factory(),
                        mgmt_connection=self.mgmt_connection,
                    )
                except Exception:
                    logger.exception(
                        "Failed to register D-Bus service for %r", lp.title,
                    )
                    continue
                self._services[lp.title] = svc

            # Edge: title was offline last tick, came back. Clear flag so a
            # future disappearance fires mark_disconnected once again.
            self._offline_titles.discard(lp.title)
            try:
                svc.update(lp)
            except Exception:
                logger.exception("Update failed for loadpoint %r", lp.title)

        # N1: transition-tracked disconnect - mark_disconnected fires on the
        # edge only, not every tick while a loadpoint stays absent.
        for title, svc in self._services.items():
            if title in seen_titles:
                continue
            if title in self._offline_titles:
                continue
            logger.info(
                "Loadpoint %r no longer in EVCC - marking disconnected", title,
            )
            self._offline_titles.add(title)
            try:
                svc.mark_disconnected()
            except Exception:
                logger.exception("mark_disconnected failed for %r", title)
        return True
