"""EVCC REST API client - pure logic, no D-Bus dependencies.

Wraps /api/state and yields typed Loadpoint objects. Tolerates missing
fields (EVCC API has drifted before - see docs/dbus-evcc-changes.md).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional

import requests


class EvccUnreachable(Exception):
    """EVCC API unreachable or returned non-2xx / invalid JSON."""


@dataclass
class Loadpoint:
    title: str
    mode: str = "off"
    connected: bool = False
    charging: bool = False
    charge_power: float = 0.0
    charge_currents: List[float] = field(default_factory=lambda: [0.0, 0.0, 0.0])
    charge_voltages: List[float] = field(default_factory=lambda: [230.0, 230.0, 230.0])
    effective_max_current: int = 16
    charged_energy: float = 0.0
    charge_duration_ns: int = 0


def _normalize_triple(value, default: float) -> List[float]:
    if not value:
        return [default, default, default]
    out: List[float] = []
    for v in value[:3]:
        try:
            out.append(float(v) if v is not None else default)
        except (TypeError, ValueError):
            out.append(default)
    while len(out) < 3:
        out.append(default)
    return out


def _parse_loadpoints(state: dict) -> List[Loadpoint]:
    raw_lps = state.get("loadpoints") or []
    if not isinstance(raw_lps, list):
        return []
    out: List[Loadpoint] = []
    for raw in raw_lps:
        if not isinstance(raw, dict):
            continue
        out.append(Loadpoint(
            title=str(raw.get("title") or "Loadpoint"),
            mode=str(raw.get("mode") or "off"),
            connected=bool(raw.get("connected", False)),
            charging=bool(raw.get("charging", False)),
            charge_power=float(raw.get("chargePower") or 0.0),
            charge_currents=_normalize_triple(raw.get("chargeCurrents"), 0.0),
            charge_voltages=_normalize_triple(raw.get("chargeVoltages"), 230.0),
            effective_max_current=int(raw.get("effectiveMaxCurrent") or 16),
            charged_energy=float(raw.get("chargedEnergy") or 0.0),
            charge_duration_ns=int(raw.get("chargeDuration") or 0),
        ))
    return out


class EvccClient:
    def __init__(self, host: str, timeout: float = 10.0) -> None:
        self.host = host
        self.timeout = timeout
        self._session: Optional[requests.Session] = None

    @property
    def session(self) -> requests.Session:
        if self._session is None:
            self._session = requests.Session()
            self._session.headers.update({"Connection": "keep-alive"})
        return self._session

    def close(self) -> None:
        if self._session is not None:
            self._session.close()
            self._session = None

    @property
    def state_url(self) -> str:
        return "http://%s/api/state" % self.host

    def fetch_loadpoints(self) -> List[Loadpoint]:
        try:
            r = self.session.get(self.state_url, timeout=self.timeout)
            r.raise_for_status()
            data = r.json()
        except requests.RequestException as e:
            raise EvccUnreachable("%s: %s" % (self.state_url, e)) from e
        except ValueError as e:
            raise EvccUnreachable(
                "%s: invalid JSON: %s" % (self.state_url, e)
            ) from e
        return _parse_loadpoints(data)
