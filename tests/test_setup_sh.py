"""The VRM tunnel step of setup.sh, run with real bash and real answers.

setup.sh as a whole needs root, a TTY and daemontools, so the tunnel section
is cut out and executed on its own against a temporary config.ini.
"""
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from setup_config import set_tunnel
from cli import read_config, resolve_tunnel_settings

ROOT = Path(__file__).resolve().parent.parent
BLOCK = re.search(r"^# --- 4\. VRM tunnel.*?(?=^# --- 5\.)",
                  (ROOT / "setup.sh").read_text(), re.S | re.M).group(0)

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="needs bash")


def _run(cfg, answers):
    return subprocess.run(
        ["bash", "-c", "set -euo pipefail\n" + BLOCK],
        input=answers, text=True, capture_output=True,
        env={"SCRIPT_DIR": str(ROOT), "CONFIG": str(cfg), "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"},
    )


def _tunnel(cfg):
    t = resolve_tunnel_settings(read_config(cfg))
    return t.enabled, t.advertise_ip, t.evcc_target, t.proxy_port


@pytest.fixture
def enabled_cfg(tmp_path):
    p = tmp_path / "config.ini"
    set_tunnel(p, enabled=True, advertise_ip="172.20.4.135",
               evcc_target="172.20.4.128:7070", proxy_port=8100)
    return p


def test_enter_keeps_an_enabled_tunnel(enabled_cfg):
    r = _run(enabled_cfg, "\n\n\n\n")
    assert r.returncode == 0, r.stderr
    assert _tunnel(enabled_cfg) == (True, "172.20.4.135", "172.20.4.128:7070", 8100)


def test_no_disables_but_keeps_parameters(enabled_cfg):
    r = _run(enabled_cfg, "n\n")
    assert r.returncode == 0, r.stderr
    assert _tunnel(enabled_cfg) == (False, "172.20.4.135", "172.20.4.128:7070", 8100)


def test_enter_keeps_a_disabled_tunnel_off(tmp_path):
    p = tmp_path / "config.ini"
    set_tunnel(p, enabled=False)
    r = _run(p, "\n")
    assert r.returncode == 0, r.stderr
    assert _tunnel(p)[0] is False


def test_broken_config_stops_with_a_clear_message(tmp_path):
    # Reading the current settings must not kill the installer silently; the
    # write that follows then refuses the broken file with a readable error.
    p = tmp_path / "config.ini"
    p.write_text("Host = no section header\n")
    r = _run(p, "\n")
    assert r.returncode != 0
    assert "config error:" in r.stderr
    assert "Traceback" not in r.stderr
