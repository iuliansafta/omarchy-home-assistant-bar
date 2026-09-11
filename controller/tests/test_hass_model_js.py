"""HaModel.js entity-id validation must match the controller's rule.

The QML layer has no other test harness, so the model is loaded with node when
available (the test skips otherwise).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest

NODE = shutil.which("node")
MODEL = Path(__file__).resolve().parent.parent.parent / "plugin" / "lib" / "HaModel.js"

pytestmark = pytest.mark.skipif(NODE is None, reason="node not available")


def _validate(ids):
    script = (
        "const fs=require('fs');"
        f"eval(fs.readFileSync({json.dumps(str(MODEL))},'utf8'));"
        f"console.log(JSON.stringify({json.dumps(ids)}.map(validEntityId)));"
    )
    result = subprocess.run([NODE, "-e", script], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_accepts_well_formed_light_ids():
    assert _validate(["light.desk", "light.a_b_c"]) == [True, True]


def test_rejects_extra_dots_and_other_domains():
    # A second dot must be rejected here exactly as the controller rejects it
    # (validEntityId used to accept "light.a.b").
    assert _validate([
        "light.a.b",
        "switch.desk",
        "light.",
        ".desk",
        "lightdesk",
        "light",
        "",
        None,
    ]) == [False] * 8
