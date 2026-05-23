"""Persistent {loadpoint_title -> DeviceInstance} map.

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
from typing import Dict, Tuple

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
        self._map: Dict[str, int] = self._load()
        self._validate_map(self._map)

    def _load(self) -> Dict[str, int]:
        if not self.path.exists():
            return {}
        try:
            with self.path.open() as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("state file is not a JSON object")
            return {str(k): int(v) for k, v in data.items()}
        except (OSError, ValueError, TypeError) as e:
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
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w") as f:
            json.dump(self._map, f, indent=2, sort_keys=True)
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
        previous = self._map
        self._map = proposed
        try:
            self._flush()
        except OSError:
            self._map = previous
            raise

    def get_or_allocate(self, title: str) -> int:
        if title in self._map:
            return self._map[title]
        used = set(self._map.values())
        for candidate in range(self.lo, self.hi + 1):
            if candidate not in used:
                # Stage in-memory + flush; if flush raises, roll back so that
                # callers catching the OSError don't see a phantom allocation.
                self._map[title] = candidate
                try:
                    self._flush()
                except OSError:
                    del self._map[title]
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
