"""Offline-only regressions at timer and WebSocket bootstrap caller seams."""
import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("aiohttp")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import omarchy_ha_controller as module


@pytest.fixture
def controller(monkeypatch):
    monkeypatch.setattr(module, "config_load", lambda: {"client_id": "cid"})
    monkeypatch.setattr(module, "config_save", lambda cfg: None)
    c = module.Controller()
    c.base_url = "https://example.invalid"
    c.refresh_token = "refresh"
    c.broadcast = lambda msg: None
    return c


async def fire_timer(c):
    # Execute the actual scheduled callback without wall-clock sleeps.
    timer = c.refresh_timer
    callback, args = timer._callback, timer._args
    timer.cancel()
    callback(*args)
    await asyncio.sleep(0)
    await asyncio.sleep(0)


@pytest.mark.parametrize("live,ready,limited", [(True, True, False), (True, True, True),
                                               (False, True, False), (True, False, False)])
def test_timer_retries_and_recovers_only_live_ready_ws(controller, live, ready, limited):
    async def scenario():
        c = controller
        c.ws_connected = ready
        c._live_ws = SimpleNamespace(closed=not live)
        c._registry_limited = limited
        c.token_request = AsyncMock(side_effect=[module.TokenError("network"),
                                                 {"access_token": "new", "expires_in": 1800}])
        c.apply_access_token({"access_token": "old", "expires_in": 1800})
        try:
            await fire_timer(c)
            assert c.status == "offline"  # timer must not early-return on valid old token
            assert not c.refresh_timer.cancelled()
            assert 0 < c.refresh_timer.when() - asyncio.get_running_loop().time() <= 30
            await fire_timer(c)
            assert c.access_token == "new"
            assert not c.refresh_timer.cancelled()
            assert c.status == ("connected" if live and ready else "offline")
            if live and ready:
                assert ("Registry access limited" in c.status_detail) == limited
        finally:
            if c.refresh_timer:
                c.refresh_timer.cancel()
    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["signout", "shutdown", "invalid_grant"])
def test_refresh_retry_stops_at_session_end(controller, monkeypatch, tmp_path, operation):
    monkeypatch.setattr(module, "SOCKET_PATH", str(tmp_path / "absent.sock"))

    async def scenario():
        c = controller
        c.token_request = AsyncMock(side_effect=module.TokenError("network"))
        c.schedule_refresh(0)
        await fire_timer(c)
        retry = c.refresh_timer
        assert not retry.cancelled()
        if operation == "signout":
            await c.signout(revoke=False)
        elif operation == "shutdown":
            await c.aclose()
        else:
            c.token_request = AsyncMock(side_effect=module.TokenError("invalid_grant"))
            await c.refresh_access_token(force=True)
        assert retry.cancelled()
        assert c.refresh_timer is None
    asyncio.run(scenario())


@pytest.mark.parametrize("operation", ["signout", "shutdown"])
def test_inflight_timer_cannot_restore_ended_session(controller, monkeypatch, tmp_path, operation):
    monkeypatch.setattr(module, "SOCKET_PATH", str(tmp_path / "absent.sock"))

    async def scenario():
        c = controller
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(data):
            entered.set()
            await release.wait()
            return {"access_token": "stale"}

        c.token_request = delayed
        c.schedule_refresh(0)
        await fire_timer(c)
        await entered.wait()
        if operation == "signout":
            await c.signout(revoke=False)
        else:
            await c.aclose()
        release.set()
        await asyncio.sleep(0)
        assert c.access_token != "stale"
        assert c.refresh_timer is None
    asyncio.run(scenario())


class BootstrapSocket:
    """Immediate queued frames let reader outrun the get_states awaiter."""
    closed = False

    def __init__(self, limited, after):
        self.frames = asyncio.Queue()
        self.limited = limited
        self.after = after
        self.bootstrapped = asyncio.Event()

    def event(self, entity, state):
        return {"type": "event", "id": 1, "event": {"event_type": "state_changed",
                "data": {"new_state": {"entity_id": entity, "state": state, "attributes": {}}}}}

    async def send_json(self, payload):
        kind = payload["type"]
        result = []
        if kind == "get_states":
            self.frames.put_nowait(self.event("light.before", "on"))
            result = [{"entity_id": "light.kept", "state": "off", "attributes": {}}]
        self.frames.put_nowait({"type": "result", "id": payload["id"],
                               "success": not (self.limited and kind.startswith("config/")),
                               "result": result})
        if kind == "get_states" and self.after:
            self.frames.put_nowait(self.event("light.kept", "on"))
            self.frames.put_nowait(self.event("light.after", "on"))
        if kind == "config/entity_registry/list":
            self.bootstrapped.set()

    async def receive(self, timeout=None):
        data = await self.frames.get()
        return SimpleNamespace(type=module.aiohttp.WSMsgType.TEXT, data=json.dumps(data))


@pytest.mark.parametrize("limited", [False, True])
@pytest.mark.parametrize("after", [False, True])
def test_ws_bootstrap_replaces_snapshot_in_reader_order_and_keeps_warning(controller, limited, after):
    async def scenario():
        c = controller
        c._states = {"light.removed": "on"}
        c._state_attrs = {"light.removed": {"friendly_name": "Removed"}}
        ws = BootstrapSocket(limited, after)
        task = asyncio.create_task(c.ws_ready(ws))
        try:
            await asyncio.wait_for(ws.bootstrapped.wait(), 1)
            for _ in range(6):
                await asyncio.sleep(0)
            expected = {"light.kept": "on" if after else "off"}
            if after:
                expected["light.after"] = "on"
            assert c.status == "connected"
            assert ("Registry access limited" in c.status_detail) == limited
            assert c._states == expected
            assert set(c._state_attrs) == set(expected)
            assert {light["entity_id"] for light in c.lights} == set(expected)
        finally:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())


def test_browser_completion_keeps_refresh_timer_usable(controller):
    """Finishing authorization advances generation after installing its token."""
    async def scenario():
        c = controller
        c.pending_auth = {"client_id": "cid", "server": SimpleNamespace(close=lambda: None),
                          "future": asyncio.get_running_loop().create_future()}
        c.token_request = AsyncMock(side_effect=[{"access_token": "initial", "refresh_token": "refresh"},
                                                 {"access_token": "renewed"}])
        try:
            await c.exchange_code("code")
            assert c.pending_auth is None
            await fire_timer(c)
            assert c.access_token == "renewed"
        finally:
            if c.refresh_timer:
                c.refresh_timer.cancel()
    asyncio.run(scenario())
