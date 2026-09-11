"""The systemd unit renderer must produce a unit systemd actually accepts.

A checkout path is user-controlled, so it is escaped for ExecStart; a path
containing a newline is rejected. WorkingDirectory is intentionally not
interpolated (systemd path settings do not unquote).
"""

import shutil
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "render_systemd_unit.sh"
SYSTEMD_ANALYZE = shutil.which("systemd-analyze")

pytestmark = pytest.mark.skipif(shutil.which("bash") is None, reason="bash not available")


def render(*args):
    return subprocess.run(["bash", str(SCRIPT), *args], capture_output=True, text=True)


def test_renders_a_valid_unit_from_plain_paths():
    result = render("/usr/bin/python3", "/opt/repo/controller/omarchy_ha_controller.py")
    assert result.returncode == 0
    assert 'ExecStart="/usr/bin/python3" "/opt/repo/controller/omarchy_ha_controller.py"' in result.stdout
    # systemd does not unquote WorkingDirectory, so it must not be quoted.
    assert "WorkingDirectory=~\n" in result.stdout
    assert 'WorkingDirectory="' not in result.stdout
    assert "WantedBy=graphical-session.target" in result.stdout


def test_newline_in_path_is_rejected_without_partial_output():
    result = render("/usr/bin/python3", "/opt/repo\nExecStartPre=/bin/sh -c pwned")
    assert result.returncode != 0
    assert result.stdout == ""
    assert "newline" in result.stderr


def test_quotes_percent_and_backslash_are_escaped():
    result = render('/p"q%i', "/r\\s")
    assert result.returncode == 0
    assert 'ExecStart="/p\\"q%%i" "/r\\\\s"' in result.stdout


def test_each_path_stays_one_quoted_argument():
    result = render("/usr/bin/python3", "/opt/my repo/controller/omarchy_ha_controller.py")
    assert result.returncode == 0
    exec_line = next(line for line in result.stdout.splitlines() if line.startswith("ExecStart="))
    assert exec_line == 'ExecStart="/usr/bin/python3" "/opt/my repo/controller/omarchy_ha_controller.py"'


def test_wrong_argument_count_is_an_error():
    assert render("/usr/bin/python3").returncode == 2
    assert render("/usr/bin/python3", "/a", "/b").returncode == 2


@pytest.mark.skipif(SYSTEMD_ANALYZE is None, reason="systemd-analyze not available")
def test_rendered_unit_has_no_fatal_setting(tmp_path):
    unit = tmp_path / "omarchy-home-assistant.service"
    rendered = render("/usr/bin/python3", "/opt/repo/controller/omarchy_ha_controller.py")
    assert rendered.returncode == 0
    unit.write_text(rendered.stdout)

    result = subprocess.run(
        [SYSTEMD_ANALYZE, "--user", "verify", str(unit)], capture_output=True, text=True
    )
    combined = result.stdout + result.stderr
    assert "bad unit file setting" not in combined
    assert "Unit configuration has fatal error" not in combined
    assert "path is not absolute" not in combined
