"""CLI subprocess boundaries: secrets and collection limits, without HA access."""
import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("aiohttp")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import omarchy_ha_controller as module
from cli_guard import redact_output


@pytest.mark.parametrize("replacement", [None, "ROTATED"])
def test_cli_scrubs_injected_token_after_session_changes(monkeypatch, replacement):
    async def scenario():
        c = module.Controller()
        c.status = "connected"
        c.access_token = "ORIGINAL-SECRET"
        c.refresh_access_token = AsyncMock(return_value=True)
        monkeypatch.setattr(module.os.path, "isfile", lambda path: True)
        monkeypatch.setattr(module.os, "access", lambda *args: True)
        proc = MagicMock(returncode=0)

        async def spawn(*args, **kwargs):
            assert kwargs["env"]["HASS_TOKEN"] == "ORIGINAL-SECRET"
            c.access_token = replacement
            return proc

        monkeypatch.setattr(module.asyncio, "create_subprocess_exec", spawn)
        monkeypatch.setattr(module, "collect_cli_output", AsyncMock(return_value=(b"ORIGINAL-SECRET", b"")))
        reply = AsyncMock()
        await c.cmd_cli_exec({"args": ["info"]}, reply)
        assert reply.call_args.kwargs["extra"]["stdout"] == "***"
    asyncio.run(scenario())


def test_redaction_precedes_output_cutoff():
    assert redact_output("x" * 12 + "SECRET" + "tail", ["SECRET"])[:15] == "x" * 12 + "***"


@pytest.mark.parametrize("mode", ["overflow", "high-output", "timeout", "success"])
def test_collection_bounds_both_pipes_and_reaps_child(mode):
    async def scenario():
        script = {
            "overflow": "import os; os.write(1, b'x'*6000); os.write(2, b'y'*6000)",
            "high-output": "import os; os.write(1, b'x'*80000); os.write(2, b'y'*80000)",
            "timeout": "import time; time.sleep(60)",
            "success": "import os; os.write(1,b'out'); os.write(2,b'err')",
        }[mode]
        proc = await asyncio.create_subprocess_exec(sys.executable, "-c", script,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        if mode == "success":
            assert await module.collect_cli_output(proc, 10000, 5) == (b"out", b"err")
        else:
            error = module.CLIOutputLimit if mode in ("overflow", "high-output") else asyncio.TimeoutError
            with pytest.raises(error):
                await module.collect_cli_output(proc, 10000, 0.1 if mode == "timeout" else 5)
        assert proc.returncode is not None
    asyncio.run(scenario())


def test_timeout_is_not_extended_by_descendant_inheriting_pipes():
    async def scenario():
        child = (
            "import subprocess,sys,time; "
            "subprocess.Popen([sys.executable,'-c','import time; time.sleep(1)']); "
            "time.sleep(60)"
        )
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", child,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        )
        loop = asyncio.get_running_loop()
        started = loop.time()
        with pytest.raises(asyncio.TimeoutError):
            await module.collect_cli_output(proc, 10000, 0.05)
        assert loop.time() - started < 0.5
        assert proc.returncode is not None
    asyncio.run(scenario())


@pytest.mark.parametrize("xdg,override,suffix", [
    (None, None, ".local/share/omarchy-home-assistant/hass-cli-venv/bin/hass-cli"),
    ("/custom/data", None, "/custom/data/omarchy-home-assistant/hass-cli-venv/bin/hass-cli"),
    ("/custom/data", "/explicit/hass-cli", "/explicit/hass-cli"),
])
def test_cli_discovery_honors_installer_xdg_and_explicit_override(xdg, override, suffix):
    import os
    import subprocess
    env = dict(os.environ)
    env.pop("XDG_DATA_HOME", None)
    env.pop("OMARCHY_HA_HASS_CLI", None)
    if xdg:
        env["XDG_DATA_HOME"] = xdg
    if override:
        env["OMARCHY_HA_HASS_CLI"] = override
    result = subprocess.run([sys.executable, "-c",
        "from omarchy_ha_controller import Controller; print(Controller.HASS_CLI_PATH)"],
        cwd=Path(__file__).resolve().parent.parent, env=env, text=True, capture_output=True, check=True)
    expected = suffix if suffix.startswith("/") else str(Path.home() / suffix)
    assert result.stdout.strip() == expected


def test_cli_cutoff_cannot_expose_secret_prefix(monkeypatch):
    async def scenario():
        c = module.Controller()
        c.status = "connected"
        c.access_token = "SECRET"
        c.refresh_access_token = AsyncMock(return_value=True)
        monkeypatch.setattr(module.os.path, "isfile", lambda path: True)
        monkeypatch.setattr(module.os, "access", lambda *args: True)
        monkeypatch.setattr(module.asyncio, "create_subprocess_exec", AsyncMock(return_value=MagicMock(returncode=0)))
        err = b"x" * (16 * 1024 - 3) + b"SECRET"
        monkeypatch.setattr(module, "collect_cli_output", AsyncMock(return_value=(b"", err)))
        reply = AsyncMock()
        await c.cmd_cli_exec({"args": ["info"]}, reply)
        assert reply.call_args.kwargs["extra"]["stderr"] == "x" * (16 * 1024 - 3) + "***"
    asyncio.run(scenario())
