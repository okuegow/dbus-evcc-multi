"""Backoff after a failed start, and the run script that relies on it.

daemontools respawns the run script the moment it exits. A config error
(DeviceInstance collision, bad VRM_TUNNEL) must therefore not exit right
away, or the service spins hot enough to trip the Venus load watchdog.
The pause lives in Python so that service/run can `exec` the bridge: only
then does `svc -t` / `svc -d` signal python itself instead of orphaning it.
"""
from pathlib import Path

import pytest

from cli import BACKOFF_SECONDS, run_with_backoff

ROOT = Path(__file__).resolve().parent.parent


def test_success_exits_without_pause():
    sleeps = []
    assert run_with_backoff(lambda: 0, sleep=sleeps.append) == 0
    assert sleeps == []


def test_error_code_pauses_before_exit():
    sleeps = []
    assert run_with_backoff(lambda: 1, sleep=sleeps.append) == 1
    assert sleeps == [BACKOFF_SECONDS]


def test_crash_pauses_and_exits_nonzero(capsys):
    def boom():
        raise RuntimeError("dbus not ready")

    sleeps = []
    assert run_with_backoff(boom, sleep=sleeps.append) == 1
    assert sleeps == [BACKOFF_SECONDS]
    assert "dbus not ready" in capsys.readouterr().err


def test_backoff_is_long_enough_for_the_watchdog():
    assert BACKOFF_SECONDS >= 30


@pytest.mark.parametrize("run_file", [
    "service/run",
    "dbus-vrm-tunnel/service/run",
])
def test_run_script_execs_python(run_file):
    lines = [l.strip() for l in (ROOT / run_file).read_text().splitlines()
             if l.strip() and not l.strip().startswith("#")]
    python_lines = [l for l in lines if "python3" in l]
    assert python_lines and all(l.startswith("exec python3") for l in python_lines)


def _run_entry(monkeypatch, tmp_path, config_text):
    """Execute dbus-evcc.py as `python3 dbus-evcc.py --config <file>`."""
    import runpy
    import sys

    cfg = tmp_path / "config.ini"
    cfg.write_text(config_text)
    sleeps = []
    monkeypatch.setattr("time.sleep", sleeps.append)
    monkeypatch.setattr(sys, "argv", ["dbus-evcc.py", "--config", str(cfg)])
    with pytest.raises(SystemExit) as exc:
        runpy.run_path(str(ROOT / "dbus-evcc.py"), run_name="__main__")
    return exc.value.code, sleeps


def test_entry_point_pauses_on_config_error(monkeypatch, tmp_path):
    code, sleeps = _run_entry(
        monkeypatch, tmp_path, "[VRM_TUNNEL]\nEnabled = true\nAdvertiseIp =\n")
    assert code == 1
    assert sleeps == [BACKOFF_SECONDS]


def test_entry_point_pauses_on_import_error(monkeypatch, tmp_path):
    import sys

    monkeypatch.setitem(sys.modules, "sync", None)  # makes `import sync` fail
    code, sleeps = _run_entry(monkeypatch, tmp_path, "")
    assert code == 1
    assert sleeps == [BACKOFF_SECONDS]
