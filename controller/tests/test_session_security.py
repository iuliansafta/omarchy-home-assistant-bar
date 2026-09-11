"""Session-lifecycle security: credentials must not survive a server change.

The Controller is driven directly with mocks; no network or keyring access.
"""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

pytest.importorskip("aiohttp")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import omarchy_ha_controller as controller_module  # noqa: E402
from omarchy_ha_controller import Controller  # noqa: E402


def _controller(**overrides):
    c = Controller()
    c.status = "connected"
    c.base_url = "https://old.example:8123"
    c.refresh_token = "REFRESH-SECRET"
    c.access_token = "ACCESS-SECRET"
    c.access_expires_at = 9e18
    c.keyring = MagicMock()
    c.probe_server = AsyncMock(return_value=(True, ""))
    c.broadcast = lambda msg: None
    for key, value in overrides.items():
        setattr(c, key, value)
    return c


def test_setup_url_change_clears_credentials_and_keyring(monkeypatch):
    c = _controller()
    saved = {}
    monkeypatch.setattr(controller_module, "config_load", lambda: {})
    monkeypatch.setattr(controller_module, "config_save", lambda cfg: saved.update(cfg))

    asyncio.run(c.cmd_setup_url({"url": "https://new.example"}, AsyncMock()))

    assert c.base_url == "https://new.example"
    assert c.refresh_token is None
    assert c.access_token is None
    assert c.keyring.delete_password.called
    assert saved["url"] == "https://new.example"
    assert saved["setup_complete"] is False


def test_setup_url_change_drops_previous_topology(monkeypatch):
    c = _controller()
    c.lights = [{"entity_id": "light.old", "state": "on"}]
    c.areas = [{"id": "old", "name": "Old"}]
    c._states = {"light.old": "on"}
    monkeypatch.setattr(controller_module, "config_load", lambda: {})
    monkeypatch.setattr(controller_module, "config_save", lambda cfg: None)

    asyncio.run(c.cmd_setup_url({"url": "https://new.example"}, AsyncMock()))

    assert c.lights == []
    assert c.areas == []
    assert c._states == {}


def test_setup_url_same_server_keeps_session(monkeypatch):
    c = _controller()
    monkeypatch.setattr(controller_module, "config_load", lambda: {})
    monkeypatch.setattr(controller_module, "config_save", lambda cfg: None)

    asyncio.run(c.cmd_setup_url({"url": "https://old.example:8123"}, AsyncMock()))

    assert c.refresh_token == "REFRESH-SECRET"
    assert c.access_token == "ACCESS-SECRET"
    assert not c.keyring.delete_password.called


def test_refresh_after_server_change_has_no_token_to_send(monkeypatch):
    """Regression for the refresh-token exfiltration path.

    Before the fix the token survived the server change and the next refresh
    POSTed it to the new (attacker-chosen) host.
    """
    c = _controller()
    monkeypatch.setattr(controller_module, "config_load", lambda: {})
    monkeypatch.setattr(controller_module, "config_save", lambda cfg: None)

    asyncio.run(c.cmd_setup_url({"url": "https://attacker.example"}, AsyncMock()))

    sent = []

    async def fake_token_request(data):
        sent.append(data)
        raise controller_module.TokenError("http_401")

    c.token_request = fake_token_request
    assert asyncio.run(c.refresh_access_token(force=True)) is False
    assert sent == []


class _FakeResponse:
    """Minimal async-context-manager stand-in for an aiohttp response."""

    def __init__(self, status=200, payload=None, text=""):
        self.status = status
        self._payload = payload if payload is not None else {"access_token": "T", "expires_in": 1800}
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def json(self):
        return self._payload

    async def text(self):
        return self._text


def test_token_request_does_not_follow_redirects():
    """A 307/308 must not re-POST the refresh token to a redirect target."""
    c = _controller()
    c.http_session = MagicMock()
    c.http_session.post = MagicMock(return_value=_FakeResponse())

    asyncio.run(c.token_request({"grant_type": "refresh_token", "refresh_token": "R"}))

    assert c.http_session.post.call_args.kwargs["allow_redirects"] is False


def test_revoke_does_not_follow_redirects(monkeypatch):
    c = _controller()
    c.http_session = MagicMock()
    c.http_session.post = MagicMock(return_value=_FakeResponse(status=200, payload={}))
    monkeypatch.setattr(controller_module, "config_load", lambda: {})
    monkeypatch.setattr(controller_module, "config_save", lambda cfg: None)

    asyncio.run(c.signout(revoke=True))

    assert c.http_session.post.call_args.kwargs["allow_redirects"] is False


def test_refresh_without_keyring_does_not_crash(monkeypatch):
    """A missing keyring must not break invalid_grant recovery."""
    c = _controller(keyring=None)
    monkeypatch.setattr(controller_module, "config_load", lambda: {"client_id": "cid"})

    async def fake_token_request(data):
        raise controller_module.TokenError("invalid_grant")

    c.token_request = fake_token_request

    assert asyncio.run(c.refresh_access_token(force=True)) is False
    assert c.refresh_token is None
    assert c.status == "credentials_invalid"


class _FakeHeaderReader:
    def __init__(self, target):
        self._data = f"GET {target} HTTP/1.1\r\n\r\n".encode()

    async def readuntil(self, sep):
        return self._data


class _FakeWriter:
    def __init__(self):
        self.data = b""
        self.closed = False

    def write(self, chunk):
        self.data += chunk

    def close(self):
        self.closed = True


def _run_callback(target, state="GOODSTATE"):
    async def scenario():
        c = Controller()
        c.pending_auth = {
            "server": MagicMock(),
            "state": state,
            "redirect_uri": "http://127.0.0.1:1/",
            "client_id": "http://127.0.0.1:1/",
            "deadline": 0,
            "future": asyncio.get_running_loop().create_future(),
        }
        writer = _FakeWriter()
        await c._callback_handler(_FakeHeaderReader(target), writer)
        return c.pending_auth["future"], writer

    return asyncio.run(scenario())


def test_callback_accepts_the_matching_state():
    future, writer = _run_callback("/?state=GOODSTATE&code=THECODE")
    assert future.result() == "THECODE"
    assert b"200 OK" in writer.data


def test_callback_rejects_a_wrong_state():
    future, writer = _run_callback("/?state=WRONG&code=THECODE")
    assert not future.done()
    assert b"400 Bad Request" in writer.data


def test_callback_rejects_non_ascii_state_without_raising():
    future, writer = _run_callback("/?state=%FF%FE&code=THECODE")
    assert not future.done()
    assert b"400 Bad Request" in writer.data


@pytest.mark.parametrize("operation", ["server", "cancel", "signout"])
@pytest.mark.parametrize("token_flow", ["exchange", "refresh"])
@pytest.mark.parametrize("error", [False, True])
def test_stale_token_result_cannot_change_session(monkeypatch, operation, token_flow, error):
    """Delayed old responses must neither restore secrets nor invalidate a new flow."""
    saved = MagicMock()
    monkeypatch.setattr(controller_module, "config_load", lambda: {"client_id": "cid"})
    monkeypatch.setattr(controller_module, "config_save", saved)
    monkeypatch.setattr(controller_module, "secret_save", MagicMock())

    async def scenario():
        c = _controller()
        c.pending_auth = {"client_id": "cid", "server": MagicMock(),
                          "future": asyncio.get_running_loop().create_future()}
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(data):
            entered.set()
            await release.wait()
            if error:
                raise controller_module.TokenError("invalid_grant")
            return {"access_token": "STALE", "refresh_token": "STALE-REFRESH"}

        c.token_request = delayed
        task = asyncio.create_task(c.exchange_code("code") if token_flow == "exchange"
                                   else c.refresh_access_token(force=True))
        await entered.wait()
        if operation == "server":
            await c.cmd_setup_url({"url": "https://new.example"}, AsyncMock())
        elif operation == "cancel":
            c.cancel_browser_auth()
        else:
            await c.signout(revoke=False)
        before = (c.access_token, c.refresh_token, c.status, saved.call_count,
                  c.keyring.delete_password.call_count)
        release.set()
        await task
        assert (c.access_token, c.refresh_token, c.status, saved.call_count,
                c.keyring.delete_password.call_count) == before
        controller_module.secret_save.assert_not_called()
        assert c.refresh_timer is None

    asyncio.run(scenario())


def test_signout_timeout_clears_locally_before_revocation(monkeypatch):
    saved = {}
    monkeypatch.setattr(controller_module, "config_load", lambda: {})
    monkeypatch.setattr(controller_module, "config_save", lambda cfg: saved.update(cfg))
    c = _controller()

    class TimeoutResponse(_FakeResponse):
        async def __aenter__(self):
            assert c.access_token is None and c.refresh_token is None
            assert c.keyring.delete_password.called
            assert saved["setup_complete"] is False
            raise asyncio.TimeoutError

    c.http_session = MagicMock()
    c.http_session.post.return_value = TimeoutResponse()
    asyncio.run(c.signout())
    assert c.status == "setup"
    assert "server session may still be active" in c.status_detail


@pytest.mark.parametrize("left,right,equal", [
    ("https://EXAMPLE", "wss://example:443/api/websocket", True),
    ("http://example", "ws://example:80/api/websocket", True),
    ("https://example", "ws://example:443", False),
    ("https://example", "wss://other", False),
    ("https://example", "wss://example:444", False),
])
def test_websocket_origin_normalizes_transport_and_port(left, right, equal):
    assert (controller_module.websocket_origin(left) == controller_module.websocket_origin(right)) == equal


def test_redirected_websocket_receives_no_credentials():
    """Real aiohttp redirects across two isolated loopback servers must not leak auth."""
    from aiohttp import ClientSession, WSMsgType, web

    async def scenario():
        received = []
        done = asyncio.Event()

        async def destination(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.send_json({"type": "auth_required"})
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    received.append(msg.data)
            done.set()
            return ws

        async def start(handler):
            app = web.Application()
            app.router.add_get("/api/websocket", handler)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            return runner, f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}"

        dest_runner, dest = await start(destination)

        async def redirect(request):
            raise web.HTTPTemporaryRedirect(dest + "/api/websocket")

        source_runner, source = await start(redirect)
        try:
            async with ClientSession() as session:
                c = _controller(base_url=source, http_session=session)
                with pytest.raises(ConnectionError, match="another origin"):
                    await c.ws_session(source.replace("http:", "ws:") + "/api/websocket")
                await asyncio.wait_for(done.wait(), 2)
                assert received == []
        finally:
            await source_runner.cleanup()
            await dest_runner.cleanup()

    asyncio.run(scenario())


def test_revocation_completion_cannot_overwrite_new_session(monkeypatch):
    monkeypatch.setattr(controller_module, "config_load", lambda: {})
    monkeypatch.setattr(controller_module, "config_save", lambda cfg: None)

    async def scenario():
        c = _controller()
        entered, release = asyncio.Event(), asyncio.Event()

        class DelayedResponse(_FakeResponse):
            async def __aenter__(self):
                entered.set()
                await release.wait()
                raise asyncio.TimeoutError

        c.http_session = MagicMock()
        c.http_session.post.return_value = DelayedResponse()
        task = asyncio.create_task(c.signout())
        await entered.wait()
        await c.cmd_setup_url({"url": "https://new.example"}, AsyncMock())
        c.access_token = "NEW"
        c.set_status("connected", "New session")
        release.set()
        await task
        assert c.access_token == "NEW"
        assert (c.status, c.status_detail) == ("connected", "New session")
    asyncio.run(scenario())


@pytest.mark.parametrize("final_url,allowed", [
    ("ws://old.example:8123/api/websocket", False),
    ("wss://old.example:8123/redirected", True),
])
def test_ws_auth_rejects_downgrade_but_allows_same_origin(final_url, allowed):
    async def scenario():
        c = _controller()
        ws = MagicMock()
        ws._response.url = final_url
        ws.receive_json = AsyncMock(side_effect=[{"type": "auth_required"}, {"type": "auth_ok"}])
        ws.send_json = AsyncMock()
        c.ws_ready = AsyncMock()
        c.http_session = MagicMock()
        c.http_session.ws_connect.return_value.__aenter__.return_value = ws
        if allowed:
            await c.ws_session("wss://old.example:8123/api/websocket")
            ws.send_json.assert_awaited_once_with({"type": "auth", "access_token": "ACCESS-SECRET"})
            c.ws_ready.assert_awaited_once()
        else:
            with pytest.raises(ConnectionError):
                await c.ws_session("wss://old.example:8123/api/websocket")
            ws.send_json.assert_not_called()
    asyncio.run(scenario())
