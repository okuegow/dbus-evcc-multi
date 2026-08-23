"""Continuity guard for the cumulative /Ac/Energy/Forward counter.

VRM turns that path into hourly energy deltas (vrmlogger/kwhdeltas.py). It is
therefore not enough for the value to be "some kWh number": it has to be
monotonic AND continuous across everything that can change underneath it -
a bridge upgrade that switches source field, an EVCC reinstall that resets its
meters, or this bridge taking over a DeviceInstance from a legacy single-
loadpoint install.

Without a guard, VRM sees either
  - a jump of more than 250 kWh, which its logger discards as suspicious and
    which then freezes the counter for that service until the logger restarts,
    or
  - a smaller jump, which it happily books as energy charged in that hour.

So we never publish EVCC's raw counter. We publish source + offset, where the
offset is chosen once so the series continues where the previous one stopped,
and is corrected when the source counter really falls backwards.
"""
from __future__ import annotations

import logging

from log_setup import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)

# A source counter that drops by less than this is rounding, not a reset.
RESET_EPSILON_KWH = 0.001

# EVCC returns 0 / null for a loadpoint whose meter is momentarily unavailable.
# Such readings are dropped rather than treated as a reset.
ZERO_KWH = 0.001

# How many consecutive lower readings it takes before we believe the source
# counter really restarted. One glitchy poll must not shift the offset.
RESET_CONFIRMATIONS = 3


class EnergyCounter:
    """Maps EVCC's raw kWh reading onto a monotonic published counter.

    offset:  published = source + offset
    source:  last accepted raw value, to detect a source-side reset
    adopt:   published value to continue from at the next call; set when
             taking over the history of another service (migration) and
             consumed once, because from then on the offset carries it.
    """

    def __init__(self, offset: float = 0.0, source=None, adopt=None,
                 title: str = "") -> None:
        self.offset = float(offset)
        self.source = None if source is None else float(source)
        self.adopt = None if adopt is None else float(adopt)
        self.title = title
        self.published = None
        self.dirty = False
        self._low_readings = 0

    @property
    def _name(self) -> str:
        return self.title or "loadpoint"

    def value(self, source) -> float:
        """Published counter for this raw reading. Updates internal state."""
        source = float(source)

        # A zero reading means "no data", never "the meter restarted".
        if source <= ZERO_KWH:
            return self.published if self.published is not None else 0.0

        if self.adopt is not None:
            # First reading after a migration: continue the old device's
            # counter instead of jumping to EVCC's lifetime total.
            self.offset = self.adopt - source
            logger.info(
                "%s: continuing the counter at %.3f kWh (EVCC reports %.3f, "
                "offset %.3f)", self._name, self.adopt, source, self.offset,
            )
            self.adopt = None
            self.dirty = True
        elif self.source is None:
            # First reading ever for this loadpoint: EVCC's own number is as
            # good a starting point as any, so publish it unchanged.
            self.offset = 0.0
            self.dirty = True
        elif source < self.source - RESET_EPSILON_KWH:
            self._low_readings += 1
            if self._low_readings < RESET_CONFIRMATIONS:
                # Probably a glitch. Hold the last published value and wait.
                logger.debug(
                    "%s: EVCC counter %.3f below the last %.3f (%d/%d) - "
                    "holding", self._name, source, self.source,
                    self._low_readings, RESET_CONFIRMATIONS,
                )
                return self.published if self.published is not None else 0.0
            # Confirmed: the source counter restarted. Carry the published
            # series over the gap instead of falling back with it.
            base = self.published if self.published is not None else self.source
            self.offset = base - source
            logger.warning(
                "%s: EVCC counter restarted (%.3f -> %.3f kWh) - published "
                "counter continues at %.3f (offset %.3f)",
                self._name, self.source, source, base, self.offset,
            )
            self._low_readings = 0
            self.dirty = True
        else:
            self._low_readings = 0

        if self.source is None or abs(source - self.source) >= 1.0:
            # Persist roughly every kWh, not on every poll: /data lives on
            # flash. A crash costs at most the last kWh of offset precision,
            # and erring low never creates a spike.
            self.dirty = True
        self.source = source

        published = source + self.offset
        if self.published is not None and published < self.published:
            published = self.published
        self.published = published
        return published

    def as_dict(self) -> dict:
        d = {"energy_offset": round(self.offset, 6),
             "energy_source": None if self.source is None else round(self.source, 6)}
        if self.adopt is not None:
            d["energy_adopt"] = round(self.adopt, 6)
        return d

    @classmethod
    def from_dict(cls, d: dict, title: str = "") -> "EnergyCounter":
        return cls(
            offset=d.get("energy_offset") or 0.0,
            source=d.get("energy_source"),
            adopt=d.get("energy_adopt"),
            title=title,
        )
