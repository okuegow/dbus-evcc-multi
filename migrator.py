"""Discovery + parsing of legacy dbus-evcc-* installations on a Cerbo.

The auto-migrator (migrate_from_lp.py) uses these pure functions to find
existing single-loadpoint bridges (Brucksch original and its forks), read
their config.ini, and propose a seed-mapping for our state.json.

We never touch /data/ directly here - the data_dir argument is injected so
tests can run against tmp_path fixtures.
"""
from __future__ import annotations

import configparser
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional

# Our own multi-bridge directory must NEVER be reported as a legacy install.
OUR_OWN_DIR_NAME = "dbus-evcc-multi"
LEGACY_DIR_PREFIX = "dbus-evcc-"


@dataclass
class LegacyInstall:
    path: Path
    deviceinstance: Optional[int] = None
    host: Optional[str] = None
    custom_name: Optional[str] = None
    loadpoint_index: Optional[int] = None
    uninstall_script: Optional[Path] = None
    errors: List[str] = field(default_factory=list)

    @property
    def is_usable(self) -> bool:
        """True if this install has the minimum data we need to seed it
        into state.json: a valid DeviceInstance and no parse errors."""
        return self.deviceinstance is not None and not self.errors


def parse_legacy_config(path: Path) -> LegacyInstall:
    """Parse a single legacy dbus-evcc config.ini into a LegacyInstall.

    Tolerant: missing file, broken syntax, missing keys, non-integer DI all
    produce a LegacyInstall with errors[] populated rather than raising.
    The caller (discover_installations + the CLI) decides what to do.
    """
    install = LegacyInstall(path=path.parent)
    if not path.exists():
        install.errors.append("config.ini not found at %s" % path)
        return install

    cp = configparser.ConfigParser()
    try:
        with path.open() as f:
            cp.read_file(f)
    except (configparser.Error, OSError) as e:
        install.errors.append("Could not parse %s: %s" % (path, e))
        return install

    raw_di = cp.get("DEFAULT", "Deviceinstance", fallback=None)
    if raw_di is None:
        install.errors.append("Deviceinstance missing in %s" % path)
    else:
        try:
            install.deviceinstance = int(raw_di)
        except ValueError:
            install.errors.append(
                "Deviceinstance must be integer, got %r in %s"
                % (raw_di, path)
            )

    install.host = (cp.get("ONPREMISE", "Host", fallback="") or "").strip() or None
    install.custom_name = (
        cp.get("DEFAULT", "CustomName", fallback="") or ""
    ).strip() or None

    raw_idx = cp.get("DEFAULT", "LoadpointIndex", fallback=None)
    if raw_idx is not None:
        try:
            install.loadpoint_index = int(raw_idx)
        except ValueError:
            install.errors.append(
                "LoadpointIndex must be integer, got %r in %s"
                % (raw_idx, path)
            )

    return install


@dataclass
class Proposal:
    """One row in the operator's confirm-mapping table."""
    install: LegacyInstall
    deviceinstance: int
    evcc_title: Optional[str]
    # 'index': matched by LoadpointIndex (authoritative)
    # 'exact-name': case-insensitive equality of CustomName and EVCC title
    # 'needs-operator': no automated match; operator must pick or skip
    confidence: str


def propose_mappings(
    installs: List[LegacyInstall],
    evcc_titles: List[str],
) -> List[Proposal]:
    """Build a Proposal per usable install.

    Resolution order per install:
      1. LoadpointIndex (when in range AND target title is unique):
         authoritative -> confidence='index'.
      2. CustomName equals a unique EVCC title (case-insensitive):
         high confidence -> confidence='exact-name'.
      3. Otherwise: confidence='needs-operator', evcc_title=None.

    Hardening (BLOCKER + SF2):
      - Duplicate EVCC titles never auto-match. Our v2.0 sync collapses
        same-titled loadpoints into one service, so picking either of two
        would silently drop the other. Force operator review.
      - A stale `LoadpointIndex` (out of evcc_titles range) is NOT silently
        downgraded to name matching. The mismatch is a strong signal the
        old install is desynced - require operator review.
    """
    # Detect duplicate titles (case-insensitive); each appears at any of
    # multiple slot indices.
    from collections import Counter
    lower_counts = Counter(t.lower() for t in evcc_titles)
    duplicate_titles_lower = {k for k, n in lower_counts.items() if n > 1}
    # Map first-seen lower -> original casing for unique titles only
    titles_lower_unique = {}
    seen_lower = set()
    for t in evcc_titles:
        lk = t.lower()
        if lk in seen_lower:
            continue
        seen_lower.add(lk)
        if lk not in duplicate_titles_lower:
            titles_lower_unique[lk] = t

    out: List[Proposal] = []
    for install in installs:
        if install.deviceinstance is None:
            continue
        title: Optional[str] = None
        confidence = "needs-operator"

        if install.loadpoint_index is not None:
            idx = install.loadpoint_index
            if 0 <= idx < len(evcc_titles):
                candidate_title = evcc_titles[idx]
                # Block if the indexed slot itself is a duplicate elsewhere
                if candidate_title.lower() not in duplicate_titles_lower:
                    title = candidate_title
                    confidence = "index"
                # else: needs-operator, no name fallback (SF2)
            # else: out-of-range -> needs-operator, no name fallback (SF2)
        elif install.custom_name:
            candidate = titles_lower_unique.get(install.custom_name.lower())
            if candidate is not None:
                title = candidate
                confidence = "exact-name"

        out.append(Proposal(
            install=install,
            deviceinstance=install.deviceinstance,
            evcc_title=title,
            confidence=confidence,
        ))
    return out


def discover_installations(data_dir: Path) -> List[LegacyInstall]:
    """Scan data_dir for legacy dbus-evcc-* subdirectories.

    Skips OUR_OWN_DIR_NAME ('dbus-evcc-multi'). Returns one LegacyInstall
    per matching directory, even if its config.ini is missing or broken -
    the operator needs to see those entries to know they exist.

    SF1: deduplicates symlinked aliases. If
    `/data/dbus-evcc-current -> /data/dbus-evcc-heizstab` is a symlink to
    a real legacy directory, both would otherwise produce installs sharing
    the same uninstall.sh - running --uninstall-old would invoke it twice.
    We canonicalize paths and keep only the first (sorted-name-order) hit
    per resolved target. Real directories beat symlinks via sort order
    only if their name sorts later; if a symlink sorts first, we still
    report the symlink alias - but only once.

    Sorted by path name for predictable diff output.
    """
    if not data_dir.exists():
        return []
    installs: List[LegacyInstall] = []
    seen_resolved: set = set()
    for child in sorted(data_dir.iterdir(), key=lambda p: p.name):
        if child.name == OUR_OWN_DIR_NAME:
            continue
        if not child.name.startswith(LEGACY_DIR_PREFIX):
            continue
        if not child.is_dir():
            # Skip dangling symlinks and non-dir files
            continue
        try:
            resolved = child.resolve()
        except (OSError, RuntimeError):
            resolved = child
        if resolved in seen_resolved:
            continue
        seen_resolved.add(resolved)
        install = parse_legacy_config(child / "config.ini")
        # Use the resolved real path so downstream uninstall.sh lookups
        # hit the actual directory, not the symlink alias.
        install.path = resolved
        uninstall = resolved / "uninstall.sh"
        if uninstall.is_file():
            install.uninstall_script = uninstall
        installs.append(install)
    return installs
