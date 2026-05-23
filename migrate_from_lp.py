#!/usr/bin/env python3
"""Auto-migrate legacy dbus-evcc-* installations into dbus-evcc-multi's state.json.

Scans /data/dbus-evcc-* (or --data-dir), reads each config.ini, fetches the
current EVCC loadpoint titles, builds a proposed {title -> DeviceInstance}
mapping, asks the operator to confirm, and writes it into state.json via
state_store.seed().

Usage:
    python3 migrate_from_lp.py                       # interactive, /data
    python3 migrate_from_lp.py --dry-run             # show plan, don't write
    python3 migrate_from_lp.py --auto                # accept all auto-matched
    python3 migrate_from_lp.py --uninstall-old       # also run each install's uninstall.sh
    python3 migrate_from_lp.py --data-dir /tmp/...   # custom location (tests)
    python3 migrate_from_lp.py --host 192.168.1.50:7070   # override EVCC host

Idempotent: re-runs skip already-seeded titles. Seed-validation rejects
duplicate DIs and out-of-range DIs (rolls back in-memory if a flush fails).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple


MULTI_SERVICE_PATH = Path("/service/dbus-evcc-multi")

from evcc_api import EvccClient, EvccUnreachable
from migrator import (
    LegacyInstall,
    Proposal,
    discover_installations,
    propose_mappings,
)
from state_store import InvalidStateFile, StateStore


@dataclass
class AppliedMapping:
    title: str
    deviceinstance: int
    install: LegacyInstall


def fetch_evcc_titles(host: str, timeout: float = 5.0) -> List[str]:
    """Pull /api/state from EVCC, return ordered list of loadpoint titles.

    Empty list on any failure (caller handles the empty case gracefully).
    """
    if not host:
        return []
    client = EvccClient(host=host, timeout=timeout)
    try:
        lps = client.fetch_loadpoints()
    except EvccUnreachable:
        return []
    finally:
        client.close()
    return [lp.title for lp in lps]


def is_multi_service_running() -> bool:
    """SF4: True if /service/dbus-evcc-multi looks active (no down-marker).

    Defensive: any I/O failure returns False (the writeable-state-dir
    preflight will still catch problems before damage is done).
    """
    try:
        if not MULTI_SERVICE_PATH.exists():
            return False
        service_dir = MULTI_SERVICE_PATH.resolve()
        down = service_dir / "down"
        return not down.exists()
    except OSError:
        return False


def can_write_to(directory: Path) -> bool:
    """SF5: True iff directory exists and the current user can write into it."""
    try:
        return directory.is_dir() and os.access(str(directory), os.W_OK)
    except OSError:
        return False


def pick_host(installs: List[LegacyInstall], override: Optional[str]) -> Optional[str]:
    """Return the EVCC host to use:
       1. --host override
       2. host from the first usable install's config.ini
       3. None (caller works with an empty title list)
    """
    if override:
        return override.strip() or None
    for inst in installs:
        if inst.is_usable and inst.host:
            return inst.host
    return None


def format_install_row(idx: int, install: LegacyInstall) -> str:
    di = "??" if install.deviceinstance is None else "%d" % install.deviceinstance
    name = install.custom_name or "(no CustomName)"
    lp_idx = ""
    if install.loadpoint_index is not None:
        lp_idx = " idx=%d" % install.loadpoint_index
    return "  [%d] %s  DI=%s  CustomName=%r%s" % (
        idx, install.path, di, name, lp_idx,
    )


def format_proposal_row(idx: int, p: Proposal) -> str:
    title = p.evcc_title or "(no auto-match)"
    return "  [%d] %s  ->  Title=%r  DI=%d  (%s)" % (
        idx, p.install.path.name, title, p.deviceinstance, p.confidence,
    )


def decide_proposal(
    proposal: Proposal,
    *,
    auto: bool,
    prompt: Callable[[str], str],
    evcc_titles: List[str],
) -> Tuple[Optional[str], bool]:
    """Return (title-to-seed-or-None, was-free-form-override).

    Auto-mode: accept exact-name and index matches, skip needs-operator.
    Interactive mode: prompt operator for y/N/skip or a free-form title.

    `was-override` is True when the operator typed a title that differed
    from the auto-suggestion (or when there was no suggestion at all).
    The CLI uses it to trigger a final-confirm step before writing - SF6.
    """
    if auto:
        if proposal.confidence in ("index", "exact-name") and proposal.evcc_title:
            return proposal.evcc_title, False
        return None, False

    if proposal.evcc_title:
        suggestion = "%r" % proposal.evcc_title
        ans = prompt(
            "Accept %s -> DI %d (%s)? [Y/n/type title to override] "
            % (suggestion, proposal.deviceinstance, proposal.confidence)
        ).strip()
        if not ans or ans.lower() in ("y", "yes", "j", "ja"):
            return proposal.evcc_title, False
        if ans.lower() in ("n", "no", "skip", "nein"):
            return None, False
        return ans, True  # free-form override
    else:
        if evcc_titles:
            hint = "Known EVCC titles: %s" % ", ".join(repr(t) for t in evcc_titles)
        else:
            hint = "EVCC unreachable or no titles known"
        print("  %s" % hint)
        ans = prompt(
            "Type EVCC title for DI %d (blank to skip): " % proposal.deviceinstance
        ).strip()
        if not ans:
            return None, False
        return ans, True


def apply_to_store(
    store: StateStore,
    accepted: List[tuple],  # (title, di, install)
) -> List[AppliedMapping]:
    """Seed accepted (title -> DI) pairs into the store.

    Calls store.seed(mapping) once with the full batch so atomic-write
    semantics hold (all-or-nothing in case of flush failure).
    """
    if not accepted:
        return []
    mapping: Dict[str, int] = {}
    for title, di, _install in accepted:
        if title in mapping and mapping[title] != di:
            print(
                "  WARNING: title %r appears twice with different DIs "
                "(%d vs %d). Keeping first." % (title, mapping[title], di),
                file=sys.stderr,
            )
            continue
        mapping[title] = di
    store.seed(mapping)
    return [
        AppliedMapping(title=title, deviceinstance=di, install=install)
        for title, di, install in accepted
        if mapping.get(title) == di
    ]


def run_uninstall_scripts(
    applied: List[AppliedMapping],
    *,
    dry_run: bool,
    timeout: Optional[float] = None,
    runner: Optional[Callable[..., subprocess.CompletedProcess]] = None,
) -> int:
    """Invoke uninstall.sh on each applied install. Returns count of failures.

    Errors are reported but don't abort the loop - operator may have to
    clean up partials by hand, but every script gets a chance.

    `timeout` caps how long one script may run (None = no limit). A
    timeout counts as a failure.

    `runner` defaults to subprocess.run looked up at CALL TIME (so tests
    can monkeypatch cli.subprocess.run after import).
    """
    if runner is None:
        runner = subprocess.run
    failures = 0
    for entry in applied:
        script = entry.install.uninstall_script
        if script is None:
            print("  [%s] no uninstall.sh present, skipping" % entry.install.path.name)
            continue
        if dry_run:
            print("  [%s] would run %s" % (entry.install.path.name, script))
            continue
        print("  [%s] running %s ..." % (entry.install.path.name, script))
        try:
            result = runner([str(script)], timeout=timeout)
            if result.returncode != 0:
                failures += 1
                print(
                    "  [%s] uninstall.sh exited with code %d"
                    % (entry.install.path.name, result.returncode),
                    file=sys.stderr,
                )
        except subprocess.TimeoutExpired:
            failures += 1
            print(
                "  [%s] uninstall.sh timed out after %ss"
                % (entry.install.path.name, timeout),
                file=sys.stderr,
            )
        except Exception as e:
            failures += 1
            print(
                "  [%s] uninstall.sh crashed: %s" % (entry.install.path.name, e),
                file=sys.stderr,
            )
    return failures


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="migrate_from_lp",
        description="Discover legacy dbus-evcc-* installations and seed "
                    "their DeviceInstances into dbus-evcc-multi's state.json.",
    )
    parser.add_argument("--data-dir", default="/data",
                        help="Directory to scan for dbus-evcc-* (default: /data)")
    parser.add_argument("--host", default=None,
                        help="EVCC host:port for title lookup "
                             "(default: derive from first legacy install)")
    parser.add_argument("--state-path", default=None,
                        help="Path to state.json (default: next to this script)")
    parser.add_argument("--auto", action="store_true",
                        help="Accept all auto-matched proposals without prompting")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show plan, do not modify state.json or run uninstall")
    parser.add_argument("--uninstall-old", action="store_true",
                        help="After seeding, run uninstall.sh on accepted installs")
    parser.add_argument("--no-fetch-titles", action="store_true",
                        help="Skip EVCC HTTP call; all proposals need operator input")
    parser.add_argument("--di-range", default="40-59",
                        help="DeviceInstance range for state.json validation "
                             "(default: 40-59)")
    parser.add_argument("--ignore-running", action="store_true",
                        help="Proceed even if the multi-bridge is currently up "
                             "(default: refuse, to avoid state.json race)")
    parser.add_argument("--uninstall-timeout", type=float, default=30.0,
                        help="Per-script timeout for --uninstall-old in seconds "
                             "(default: 30)")
    args = parser.parse_args(argv if argv is not None else sys.argv[1:])

    data_dir = Path(args.data_dir)
    if not data_dir.is_dir():
        print("ERROR: data-dir %s does not exist or is not a directory" % data_dir,
              file=sys.stderr)
        return 2

    here = Path(__file__).resolve().parent
    state_path = Path(args.state_path) if args.state_path else here / "state.json"

    try:
        lo, hi = (int(x) for x in args.di_range.split("-", 1))
    except ValueError:
        print("ERROR: --di-range must look like '40-59'", file=sys.stderr)
        return 2

    # SF4: refuse to write state.json while the bridge is running, unless
    # the operator explicitly opts in (--ignore-running).
    if is_multi_service_running() and not args.ignore_running:
        print(
            "ERROR: dbus-evcc-multi service is currently running. "
            "Writing state.json while the bridge is up races its atomic "
            "writer. Stop the service first (svc -d %s), or pass "
            "--ignore-running if you know what you are doing."
            % MULTI_SERVICE_PATH,
            file=sys.stderr,
        )
        return 1

    # SF5: catch missing write permission BEFORE prompting the operator
    # for any decisions.
    if not args.dry_run and not can_write_to(state_path.parent):
        print(
            "ERROR: state.json parent directory %s is not writable by the "
            "current user. Re-run as root, or fix permissions."
            % state_path.parent,
            file=sys.stderr,
        )
        return 1

    # Discover ----
    installs = discover_installations(data_dir)
    if not installs:
        print("No legacy dbus-evcc-* installations found in %s." % data_dir)
        return 0

    print("Found %d legacy installation(s) in %s:" % (len(installs), data_dir))
    for idx, inst in enumerate(installs, 1):
        print(format_install_row(idx, inst))
        for err in inst.errors:
            print("       ERROR: %s" % err)
    print()

    # EVCC titles ----
    evcc_titles: List[str] = []
    if not args.no_fetch_titles:
        host = pick_host(installs, args.host)
        if host:
            print("Fetching EVCC loadpoint titles from http://%s/api/state ..." % host)
            evcc_titles = fetch_evcc_titles(host)
            if evcc_titles:
                print("  found %d title(s): %s"
                      % (len(evcc_titles), ", ".join(repr(t) for t in evcc_titles)))
            else:
                print("  EVCC unreachable or returned no loadpoints. Operator "
                      "will have to enter titles manually.")
        else:
            print("No EVCC host known (no --host, no legacy install carried one). "
                  "Operator will have to enter titles manually.")
        print()

    # Propose + decide ----
    proposals = propose_mappings(installs, evcc_titles)
    if not proposals:
        print("No proposal could be built (no usable installs). "
              "Fix the ERRORs above and re-run.")
        return 1

    print("Proposed mapping:")
    for idx, p in enumerate(proposals, 1):
        print(format_proposal_row(idx, p))
    print()

    accepted: List[tuple] = []
    saw_override = False
    for p in proposals:
        title, was_override = decide_proposal(
            p, auto=args.auto, prompt=input, evcc_titles=evcc_titles,
        )
        if was_override:
            saw_override = True
        if title:
            accepted.append((title, p.deviceinstance, p.install))
        else:
            print("  skipped DI %d (%s)" % (p.deviceinstance, p.install.path.name))

    if not accepted:
        print("\nNothing accepted. state.json unchanged.")
        return 0

    print("\nWill seed %d mapping(s) into %s:" % (len(accepted), state_path))
    for title, di, _ in accepted:
        print("  %r -> %d" % (title, di))
    if args.dry_run:
        print("\n--dry-run: not writing state.json")
        return 0

    # SF6: when the operator typed a free-form title, echo the table and
    # confirm once more before writing. Catches typos and accidental
    # off-by-one selections.
    if saw_override and not args.auto:
        ans = input(
            "\nConfirm seeding above %d entries? [Y/n] " % len(accepted)
        ).strip()
        if ans.lower() in ("n", "no", "nein"):
            print("Aborted by operator. Nothing written.")
            return 0

    # Apply ----
    try:
        store = StateStore(state_path, di_range=(lo, hi))
        applied = apply_to_store(store, accepted)
    except InvalidStateFile as e:
        print("ERROR: state.json validation failed: %s" % e, file=sys.stderr)
        return 1
    except OSError as e:
        print("ERROR: could not write state.json: %s" % e, file=sys.stderr)
        return 1
    print("Seeded %d mapping(s). state.json: %s" % (len(applied), state_path))

    # Optional uninstall ----
    if args.uninstall_old and applied:
        print("\nRunning uninstall.sh on accepted installations:")
        failures = run_uninstall_scripts(
            applied,
            dry_run=args.dry_run,
            timeout=args.uninstall_timeout,
        )
        if failures:
            print("\n%d uninstall script(s) reported failures. Check output above."
                  % failures, file=sys.stderr)
            return 1

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
