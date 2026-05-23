"""End-to-end tests for migrate_from_lp.py CLI.

We never reach a real /data/ or a real EVCC. data_dir is tmp_path; the
EVCC HTTP call is intercepted via requests_mock; input() is monkey-patched.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

import migrate_from_lp as cli


BRUCKSCH_INI = """\
[DEFAULT]
AccessType = OnPremise
Deviceinstance = {di}
CustomName = {name}

[ONPREMISE]
Host = {host}
"""


def _make_install(base, name, di, customname, host="192.168.1.50:7070",
                  uninstall=True):
    d = base / ("dbus-evcc-" + name)
    d.mkdir()
    (d / "config.ini").write_text(
        BRUCKSCH_INI.format(di=di, name=customname, host=host)
    )
    if uninstall:
        (d / "uninstall.sh").write_text("#!/bin/sh\nexit 0\n")
        (d / "uninstall.sh").chmod(0o755)
    return d


# ----- No legacy installs -------------------------------------------------

def test_no_legacy_installs_returns_zero(tmp_path, capsys):
    (tmp_path / "dbus-evcc-multi").mkdir()
    rc = cli.main([
        "--data-dir", str(tmp_path),
        "--state-path", str(tmp_path / "state.json"),
        "--no-fetch-titles",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "No legacy" in out
    assert not (tmp_path / "state.json").exists()


def test_data_dir_missing_returns_2(tmp_path, capsys):
    rc = cli.main([
        "--data-dir", str(tmp_path / "nope"),
        "--state-path", str(tmp_path / "state.json"),
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "does not exist" in err


def test_bad_di_range_returns_2(tmp_path, capsys):
    rc = cli.main([
        "--data-dir", str(tmp_path),
        "--di-range", "garbage",
    ])
    assert rc == 2
    err = capsys.readouterr().err
    assert "di-range" in err.lower()


# ----- Auto mode: happy path ----------------------------------------------

def test_auto_mode_seeds_exact_name_matches(tmp_path, requests_mock, capsys):
    _make_install(tmp_path, "heizstab", 56, "Heizstab")
    _make_install(tmp_path, "garage", 49, "Garage")
    requests_mock.get(
        "http://192.168.1.50:7070/api/state",
        json={"loadpoints": [{"title": "Heizstab"}, {"title": "Garage"}]},
    )
    state_path = tmp_path / "state.json"
    rc = cli.main([
        "--data-dir", str(tmp_path),
        "--state-path", str(state_path),
        "--auto",
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Seeded 2" in out
    assert state_path.exists()
    data = json.loads(state_path.read_text())
    assert data == {"Heizstab": 56, "Garage": 49}


def test_auto_mode_skips_needs_operator(tmp_path, requests_mock, capsys):
    """If CustomName has no exact-name match and no LoadpointIndex,
    --auto must skip it (operator review required)."""
    _make_install(tmp_path, "heizstab", 56, "Heizstab")
    _make_install(tmp_path, "weirdname", 49, "TotallyDifferent")
    requests_mock.get(
        "http://192.168.1.50:7070/api/state",
        json={"loadpoints": [{"title": "Heizstab"}, {"title": "Garage"}]},
    )
    state_path = tmp_path / "state.json"
    rc = cli.main([
        "--data-dir", str(tmp_path),
        "--state-path", str(state_path),
        "--auto",
    ])
    assert rc == 0
    data = json.loads(state_path.read_text())
    assert data == {"Heizstab": 56}  # weirdname skipped


# ----- Dry-run ------------------------------------------------------------

def test_dry_run_does_not_write_state(tmp_path, requests_mock, capsys):
    _make_install(tmp_path, "heizstab", 56, "Heizstab")
    requests_mock.get(
        "http://192.168.1.50:7070/api/state",
        json={"loadpoints": [{"title": "Heizstab"}]},
    )
    state_path = tmp_path / "state.json"
    rc = cli.main([
        "--data-dir", str(tmp_path),
        "--state-path", str(state_path),
        "--auto",
        "--dry-run",
    ])
    assert rc == 0
    assert not state_path.exists()
    out = capsys.readouterr().out
    assert "dry-run" in out.lower()


# ----- EVCC unreachable ---------------------------------------------------

def test_evcc_unreachable_still_runs_in_auto_with_no_matches(
    tmp_path, requests_mock, capsys
):
    """If EVCC times out, evcc_titles=[] - in --auto mode no match has
    confidence='exact-name' so nothing is accepted. Exit code is 0
    (nothing went wrong, just nothing to do)."""
    import requests
    _make_install(tmp_path, "heizstab", 56, "Heizstab")
    requests_mock.get(
        "http://192.168.1.50:7070/api/state",
        exc=requests.exceptions.ConnectTimeout,
    )
    state_path = tmp_path / "state.json"
    rc = cli.main([
        "--data-dir", str(tmp_path),
        "--state-path", str(state_path),
        "--auto",
    ])
    assert rc == 0
    assert not state_path.exists()
    out = capsys.readouterr().out
    assert "unreachable" in out.lower()


def test_no_fetch_titles_skips_http_entirely(tmp_path, capsys):
    """--no-fetch-titles must not call EVCC at all (no requests_mock = if
    a call goes out, NoMockAddress exception surfaces)."""
    _make_install(tmp_path, "heizstab", 56, "Heizstab")
    state_path = tmp_path / "state.json"
    rc = cli.main([
        "--data-dir", str(tmp_path),
        "--state-path", str(state_path),
        "--auto",
        "--no-fetch-titles",
    ])
    # No EVCC call -> evcc_titles=[] -> auto-mode skips Heizstab
    assert rc == 0
    assert not state_path.exists()


# ----- Interactive mode (monkeypatched input) -----------------------------

def test_interactive_accepts_with_default_yes(tmp_path, requests_mock, monkeypatch, capsys):
    _make_install(tmp_path, "heizstab", 56, "Heizstab")
    requests_mock.get(
        "http://192.168.1.50:7070/api/state",
        json={"loadpoints": [{"title": "Heizstab"}]},
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "")  # empty -> default Yes
    state_path = tmp_path / "state.json"
    rc = cli.main([
        "--data-dir", str(tmp_path),
        "--state-path", str(state_path),
    ])
    assert rc == 0
    assert json.loads(state_path.read_text()) == {"Heizstab": 56}


def test_interactive_skips_on_no(tmp_path, requests_mock, monkeypatch, capsys):
    _make_install(tmp_path, "heizstab", 56, "Heizstab")
    requests_mock.get(
        "http://192.168.1.50:7070/api/state",
        json={"loadpoints": [{"title": "Heizstab"}]},
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: "n")
    state_path = tmp_path / "state.json"
    rc = cli.main([
        "--data-dir", str(tmp_path),
        "--state-path", str(state_path),
    ])
    assert rc == 0
    assert not state_path.exists()
    out = capsys.readouterr().out
    assert "Nothing accepted" in out


def test_interactive_accepts_override_title(tmp_path, requests_mock, monkeypatch):
    """When user types a free-form title at the prompt, that wins over the
    auto-suggestion - handy when CustomName doesn't match the EVCC title.
    A final confirm step (SF6) catches typos by echoing the full table."""
    _make_install(tmp_path, "heizstab", 56, "WrongName")
    requests_mock.get(
        "http://192.168.1.50:7070/api/state",
        json={"loadpoints": [{"title": "Heizstab"}, {"title": "Garage"}]},
    )

    # Two prompts: title override, then final confirm
    answers = iter(["Heizstab", "y"])

    def fake_input(_prompt):
        return next(answers)

    monkeypatch.setattr("builtins.input", fake_input)
    state_path = tmp_path / "state.json"
    rc = cli.main([
        "--data-dir", str(tmp_path),
        "--state-path", str(state_path),
    ])
    assert rc == 0
    assert json.loads(state_path.read_text()) == {"Heizstab": 56}


# ----- Idempotency --------------------------------------------------------

def test_rerun_is_idempotent(tmp_path, requests_mock):
    """Running the migrator twice in a row must not raise InvalidStateFile.
    state_store.seed() preserves existing entries."""
    _make_install(tmp_path, "heizstab", 56, "Heizstab")
    requests_mock.get(
        "http://192.168.1.50:7070/api/state",
        json={"loadpoints": [{"title": "Heizstab"}]},
    )
    state_path = tmp_path / "state.json"
    rc1 = cli.main([
        "--data-dir", str(tmp_path),
        "--state-path", str(state_path),
        "--auto",
    ])
    rc2 = cli.main([
        "--data-dir", str(tmp_path),
        "--state-path", str(state_path),
        "--auto",
    ])
    assert rc1 == 0 and rc2 == 0
    assert json.loads(state_path.read_text()) == {"Heizstab": 56}


# ----- Title collision in state.json --------------------------------------

def test_seeding_collision_returns_error(tmp_path, requests_mock, capsys):
    """If state.json already maps a title to a DIFFERENT DI than what we
    propose, seed() must reject + we surface the error cleanly."""
    state_path = tmp_path / "state.json"
    # Pre-populate state.json with Heizstab -> 40 (different DI)
    state_path.write_text(json.dumps({"Heizstab": 40}))
    _make_install(tmp_path, "heizstab", 56, "Heizstab")
    requests_mock.get(
        "http://192.168.1.50:7070/api/state",
        json={"loadpoints": [{"title": "Heizstab"}]},
    )
    rc = cli.main([
        "--data-dir", str(tmp_path),
        "--state-path", str(state_path),
        "--auto",
    ])
    # seed() preserves existing: Heizstab stays at 40, 56 is silently ignored
    # because setdefault won't overwrite. So state.json is unchanged.
    assert rc == 0
    data = json.loads(state_path.read_text())
    assert data["Heizstab"] == 40


def test_seeding_di_outside_range_errors(tmp_path, requests_mock, capsys):
    _make_install(tmp_path, "weird", 999, "Heizstab")
    requests_mock.get(
        "http://192.168.1.50:7070/api/state",
        json={"loadpoints": [{"title": "Heizstab"}]},
    )
    state_path = tmp_path / "state.json"
    rc = cli.main([
        "--data-dir", str(tmp_path),
        "--state-path", str(state_path),
        "--auto",
        "--di-range", "40-59",
    ])
    assert rc == 1
    err = capsys.readouterr().err
    assert "out of range" in err.lower() or "validation" in err.lower()


# ----- Uninstall-old hook -------------------------------------------------

def test_uninstall_old_invokes_each_script(tmp_path, requests_mock, monkeypatch):
    _make_install(tmp_path, "heizstab", 56, "Heizstab")
    _make_install(tmp_path, "garage", 49, "Garage")
    requests_mock.get(
        "http://192.168.1.50:7070/api/state",
        json={"loadpoints": [{"title": "Heizstab"}, {"title": "Garage"}]},
    )
    state_path = tmp_path / "state.json"
    invoked = []

    def fake_runner(cmd, *a, **kw):
        invoked.append(cmd[0])
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr(cli.subprocess, "run", fake_runner)
    rc = cli.main([
        "--data-dir", str(tmp_path),
        "--state-path", str(state_path),
        "--auto",
        "--uninstall-old",
    ])
    assert rc == 0
    assert len(invoked) == 2
    assert all("uninstall.sh" in x for x in invoked)


def test_uninstall_old_reports_script_failures(tmp_path, requests_mock,
                                                monkeypatch, capsys):
    _make_install(tmp_path, "heizstab", 56, "Heizstab")
    requests_mock.get(
        "http://192.168.1.50:7070/api/state",
        json={"loadpoints": [{"title": "Heizstab"}]},
    )
    state_path = tmp_path / "state.json"

    def fake_runner(cmd, *a, **kw):
        return subprocess.CompletedProcess(args=cmd, returncode=7)

    monkeypatch.setattr(cli.subprocess, "run", fake_runner)
    rc = cli.main([
        "--data-dir", str(tmp_path),
        "--state-path", str(state_path),
        "--auto",
        "--uninstall-old",
    ])
    assert rc == 1  # uninstall failure surfaces as non-zero
    err = capsys.readouterr().err
    assert "code 7" in err


def test_running_service_refused_without_force(tmp_path, requests_mock,
                                                 monkeypatch, capsys):
    """SF4: writing state.json while the multi-bridge is up would race the
    running process's atomic write. Refuse unless the operator opts in via
    --ignore-running."""
    _make_install(tmp_path, "heizstab", 56, "Heizstab")
    requests_mock.get(
        "http://192.168.1.50:7070/api/state",
        json={"loadpoints": [{"title": "Heizstab"}]},
    )
    state_path = tmp_path / "state.json"
    # Simulate running service: presence of the symlink WITHOUT a 'down' file
    service_dir = tmp_path / "fake-service"
    service_dir.mkdir()
    # the helper checks (a) symlink target exists, (b) no 'down' marker
    monkeypatch.setattr(cli, "is_multi_service_running",
                        lambda: True)
    rc = cli.main([
        "--data-dir", str(tmp_path),
        "--state-path", str(state_path),
        "--auto",
    ])
    assert rc == 1
    err = capsys.readouterr().err
    assert "running" in err.lower()
    assert not state_path.exists()


def test_running_service_accepted_with_ignore_flag(tmp_path, requests_mock,
                                                    monkeypatch):
    """SF4 escape hatch."""
    _make_install(tmp_path, "heizstab", 56, "Heizstab")
    requests_mock.get(
        "http://192.168.1.50:7070/api/state",
        json={"loadpoints": [{"title": "Heizstab"}]},
    )
    state_path = tmp_path / "state.json"
    monkeypatch.setattr(cli, "is_multi_service_running", lambda: True)
    rc = cli.main([
        "--data-dir", str(tmp_path),
        "--state-path", str(state_path),
        "--auto",
        "--ignore-running",
    ])
    assert rc == 0
    assert state_path.exists()


def test_unwritable_state_dir_fails_preflight(tmp_path, requests_mock,
                                               monkeypatch, capsys):
    """SF5: catch missing write permission BEFORE prompting the operator
    for 5 mappings only to fail at the end."""
    _make_install(tmp_path, "heizstab", 56, "Heizstab")
    requests_mock.get(
        "http://192.168.1.50:7070/api/state",
        json={"loadpoints": [{"title": "Heizstab"}]},
    )
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o555)  # read+exec, no write
    state_path = locked / "state.json"
    monkeypatch.setattr(cli, "is_multi_service_running", lambda: False)
    try:
        rc = cli.main([
            "--data-dir", str(tmp_path),
            "--state-path", str(state_path),
            "--auto",
        ])
    finally:
        locked.chmod(0o755)  # let tmp cleanup work
    assert rc == 1
    err = capsys.readouterr().err
    assert "writ" in err.lower()  # 'writable' or 'write'


def test_final_confirm_table_shown_with_free_form_overrides(
    tmp_path, requests_mock, monkeypatch, capsys
):
    """SF6: when the operator typed a free-form title, the apply-step must
    echo the full final mapping table before writing - so they catch typos."""
    _make_install(tmp_path, "heizstab", 56, "OldName")
    requests_mock.get(
        "http://192.168.1.50:7070/api/state",
        json={"loadpoints": [{"title": "Heizstab"}, {"title": "Garage"}]},
    )
    # First prompt: needs-operator -> operator types "Heizstab"
    # Second prompt: final confirmation -> "y"
    answers = iter(["Heizstab", "y"])
    monkeypatch.setattr("builtins.input", lambda _p: next(answers))
    monkeypatch.setattr(cli, "is_multi_service_running", lambda: False)
    state_path = tmp_path / "state.json"
    rc = cli.main([
        "--data-dir", str(tmp_path),
        "--state-path", str(state_path),
    ])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Will seed" in out  # final table
    assert "Heizstab" in out
    assert json.loads(state_path.read_text()) == {"Heizstab": 56}


def test_final_confirm_aborts_on_no(
    tmp_path, requests_mock, monkeypatch, capsys
):
    """If the operator says n at the final confirm, nothing is written."""
    _make_install(tmp_path, "heizstab", 56, "OldName")
    requests_mock.get(
        "http://192.168.1.50:7070/api/state",
        json={"loadpoints": [{"title": "Heizstab"}]},
    )
    answers = iter(["Heizstab", "n"])
    monkeypatch.setattr("builtins.input", lambda _p: next(answers))
    monkeypatch.setattr(cli, "is_multi_service_running", lambda: False)
    state_path = tmp_path / "state.json"
    rc = cli.main([
        "--data-dir", str(tmp_path),
        "--state-path", str(state_path),
    ])
    assert rc == 0
    assert not state_path.exists()


def test_uninstall_timeout_kills_hanging_script(
    tmp_path, requests_mock, monkeypatch, capsys
):
    """NICE-TO-HAVE: --uninstall-timeout caps how long a slow/hanging
    uninstall.sh may block the migrator."""
    _make_install(tmp_path, "heizstab", 56, "Heizstab")
    requests_mock.get(
        "http://192.168.1.50:7070/api/state",
        json={"loadpoints": [{"title": "Heizstab"}]},
    )
    monkeypatch.setattr(cli, "is_multi_service_running", lambda: False)
    state_path = tmp_path / "state.json"

    captured_timeout = []

    def fake_runner(cmd, *a, **kw):
        captured_timeout.append(kw.get("timeout"))
        return subprocess.CompletedProcess(args=cmd, returncode=0)

    monkeypatch.setattr(cli.subprocess, "run", fake_runner)
    rc = cli.main([
        "--data-dir", str(tmp_path),
        "--state-path", str(state_path),
        "--auto",
        "--uninstall-old",
        "--uninstall-timeout", "45",
    ])
    assert rc == 0
    assert captured_timeout == [45.0]


def test_uninstall_dry_run_does_not_invoke(tmp_path, requests_mock, monkeypatch):
    _make_install(tmp_path, "heizstab", 56, "Heizstab")
    requests_mock.get(
        "http://192.168.1.50:7070/api/state",
        json={"loadpoints": [{"title": "Heizstab"}]},
    )
    state_path = tmp_path / "state.json"
    invoked = []
    monkeypatch.setattr(
        cli.subprocess, "run",
        lambda cmd, *a, **kw: invoked.append(cmd) or subprocess.CompletedProcess(cmd, 0),
    )
    rc = cli.main([
        "--data-dir", str(tmp_path),
        "--state-path", str(state_path),
        "--auto",
        "--uninstall-old",
        "--dry-run",
    ])
    assert rc == 0
    # --dry-run path: state.json never written, so nothing is "applied",
    # and uninstall section is unreachable. Either way: no script run.
    assert invoked == []
