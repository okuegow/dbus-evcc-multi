"""Persistent per-loadpoint state: DeviceInstance plus energy continuity.

On-disk format v2:

    {"version": 2,
     "loadpoints": {"Carport": {"deviceinstance": 49,
                                "energy_offset": -17008.5,
                                "energy_source": 17010.75}}}

The v1 format ({"Carport": 49}) is still read and is rewritten as v2 on the
next write, so upgrading an installed bridge needs no migration step.

Critical invariants:
  - A title that has ever been seen keeps its DI for the lifetime of the file.
  - Reordering loadpoints in EVCC does NOT change DI assignment.
  - Renaming a loadpoint in EVCC creates a NEW DI (and orphans the old entry).
    This is documented in README; user must be aware.
  - File is written atomically (tmp + os.replace) so a crash never truncates it.

Caller seeds known mappings before first allocate() for migration cases.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Dict, Optional, Tuple

from energy import EnergyCounter
from log_setup import LOGGER_NAME

logger = logging.getLogger(LOGGER_NAME)


class DeviceInstanceExhausted(Exception):
    """No free DeviceInstance left in the configured range."""


class InvalidStateFile(Exception):
    """state.json contents are inconsistent (duplicate DIs, out-of-range DIs)."""


class StateStore:
    def __init__(
        self,
        path,
        di_range: Tuple[int, int] = (40, 59),
    ) -> None:
        self.path = Path(path)
        self.lo, self.hi = di_range
        if self.lo > self.hi:
            raise ValueError("Invalid DI range: %r" % (di_range,))
        # title -> {"deviceinstance": int, "energy_*": ...}
        self._records: Dict[str, dict] = self._load()
        self._map: Dict[str, int] = {
            t: r["deviceinstance"] for t, r in self._records.items()
        }
        self._validate_map(self._map)

    def _load(self) -> Dict[str, dict]:
        if not self.path.exists():
            return {}
        try:
            with self.path.open() as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("state file is not a JSON object")
            if data.get("version") == 2:
                raw = data.get("loadpoints") or {}
                if not isinstance(raw, dict):
                    raise ValueError("'loadpoints' is not a JSON object")
                out = {}
                for title, rec in raw.items():
                    if not isinstance(rec, dict):
                        raise ValueError("entry %r is not an object" % title)
                    rec = dict(rec)
                    rec["deviceinstance"] = int(rec["deviceinstance"])
                    out[str(title)] = rec
                return out
            # v1: flat {title: deviceinstance}
            return {str(k): {"deviceinstance": int(v)} for k, v in data.items()}
        except (OSError, ValueError, TypeError, KeyError) as e:
            logger.warning(
                "Could not load state file %s (%s) - starting empty",
                self.path, e,
            )
            return {}

    def _validate_map(self, m: Dict[str, int]) -> None:
        out_of_range = {t: di for t, di in m.items() if not (self.lo <= di <= self.hi)}
        if out_of_range:
            raise InvalidStateFile(
                "DeviceInstance(s) out of range [%d,%d]: %r"
                % (self.lo, self.hi, out_of_range)
            )
        seen: Dict[int, str] = {}
        dup_list = []
        for title, di in m.items():
            if di in seen:
                dup_list.append((seen[di], title, di))
            else:
                seen[di] = title
        if dup_list:
            raise InvalidStateFile(
                "Duplicate DeviceInstance assignments: %r" % (dup_list,)
            )

    def _flush(self) -> None:
        payload = {"version": 2, "loadpoints": self._records}
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w") as f:
            json.dump(payload, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, self.path)

    def seed(self, mapping: Dict[str, int]) -> None:
        """Pre-populate title->DI pairs (e.g., migration from single-LP).
        Existing entries are NOT overwritten. Validates result for uniqueness
        BEFORE persisting (dry-run, no partial state on failure). If the
        atomic flush itself fails, rolls back so in-memory state matches disk.
        """
        proposed = dict(self._map)
        for title, di in mapping.items():
            proposed.setdefault(title, di)
        self._validate_map(proposed)
        previous_map, previous_records = self._map, dict(self._records)
        self._map = proposed
        for title, di in proposed.items():
            rec = dict(self._records.get(title) or {})
            rec["deviceinstance"] = di
            self._records[title] = rec
        try:
            self._flush()
        except OSError:
            self._map, self._records = previous_map, previous_records
            raise

    def seed_energy(self, adopt: Dict[str, float]) -> None:
        """Record the counter reading each title should CONTINUE from.

        Used when taking a DeviceInstance over from a legacy bridge: the value
        is the old service's /Ac/Energy/Forward, captured while it was still
        running. Consumed by EnergyCounter on the first publish.
        """
        previous = dict(self._records)
        for title, value in adopt.items():
            rec = dict(self._records.get(title) or {})
            if "deviceinstance" not in rec:
                raise InvalidStateFile(
                    "Cannot seed a counter for unknown loadpoint %r - seed the "
                    "title->DeviceInstance mapping first" % title
                )
            rec["energy_adopt"] = float(value)
            self._records[title] = rec
        try:
            self._flush()
        except OSError:
            self._records = previous
            raise

    def energy_counter(self, title: str) -> EnergyCounter:
        """The persisted counter state for a title (fresh one if unknown)."""
        return EnergyCounter.from_dict(self._records.get(title) or {}, title=title)

    def save_energy(self, title: str, counter: EnergyCounter) -> bool:
        """Persist the counter if it changed materially. Returns True if written."""
        if not counter.dirty:
            return False
        rec = dict(self._records.get(title) or {})
        if "deviceinstance" not in rec:
            return False
        rec.pop("energy_adopt", None)
        rec.update(counter.as_dict())
        previous = self._records.get(title)
        self._records[title] = rec
        try:
            self._flush()
        except OSError:
            if previous is None:
                self._records.pop(title, None)
            else:
                self._records[title] = previous
            raise
        counter.dirty = False
        return True

    def get_or_allocate(self, title: str) -> int:
        if title in self._map:
            return self._map[title]
        used = set(self._map.values())
        for candidate in range(self.lo, self.hi + 1):
            if candidate not in used:
                # Stage in-memory + flush; if flush raises, roll back so that
                # callers catching the OSError don't see a phantom allocation.
                self._map[title] = candidate
                self._records[title] = {"deviceinstance": candidate}
                try:
                    self._flush()
                except OSError:
                    del self._map[title]
                    self._records.pop(title, None)
                    raise
                logger.info(
                    "Allocated DeviceInstance %d for loadpoint '%s'",
                    candidate, title,
                )
                return candidate
        raise DeviceInstanceExhausted(
            "No free DI in range %d-%d for '%s' (used: %r)"
            % (self.lo, self.hi, title, sorted(used))
        )

    def snapshot(self) -> Dict[str, int]:
        """Read-only copy of the current map (for diagnostics/tests)."""
        return dict(self._map)
