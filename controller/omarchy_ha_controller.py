#!/usr/bin/env python3
"""omarchy-home-assistant controller.

Per-user session owner for the Omarchy Home Assistant bar widget:
  - validates the Home Assistant URL and probes reachability
  - runs Home Assistant's browser authorization-code flow (loopback callback)
  - stores the refresh token in Secret Service / GNOME Keyring (pinned backend)
  - keeps access tokens in memory only, refreshing before expiry
  - holds the /api/websocket connection, snapshots state, re-authenticates
  - serves the widget over a private Unix socket (line-delimited JSON)

Security invariants:
  - tokens/credentials never appear in logs, argv, config files, or IPC
  - the IPC protocol never carries secrets
  - no arbitrary command execution through IPC
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import socket
import struct
import sys
import time
from urllib.parse import urlencode, urlparse, urlunparse

import aiohttp
from aiohttp import web

from topology import area_targets, resolve_topology, validate_light_set  # noqa: E402
from cli_guard import redact_output, validate_cli_args  # noqa: E402

# ---------------------------------------------------------------------------
# Paths and constants
# ---------------------------------------------------------------------------

RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR", "")
CONFIG_DIR = os.path.join(
    os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")),
    "omarchy-home-assistant",
)
CONFIG_PATH = os.path.join(CONFIG_DIR, "config.json")
SOCKET_DIR = os.path.join(RUNTIME_DIR, "omarchy-ha")
SOCKET_PATH = os.path.join(SOCKET_DIR, "controller.sock")

KEYRING_SERVICE = "omarchy-home-assistant"
KEYRING_ACCOUNT = "ha-refresh-token"

MAX_LINE_BYTES = 256 * 1024
# The stream reader must be able to buffer a whole over-limit line so the
# explicit check below can reject it with an error reply. Past this the reader
# raises ValueError (and resets its buffer), so the connection is dropped.
STREAM_LIMIT = MAX_LINE_BYTES + 4096
CALLBACK_TIMEOUT_S = 300  # browser sign-in deadline
AUTHORIZE_PATH = "/auth/authorize"
TOKEN_PATH = "/auth/token"
REVOKE_PATH = "/auth/revoke"
WS_PATH = "/api/websocket"
PROBE_PATH = "/auth/providers"

# Refresh the access token this many seconds before expiry.
REFRESH_MARGIN_S = 300

RECONNECT_BASE_MS = 1000
RECONNECT_CAP_MS = 30000

log = logging.getLogger("omarchy-ha")

# ---------------------------------------------------------------------------
# Keyring storage (pinned Secret Service backend; reject plaintext backends)
# ---------------------------------------------------------------------------


def load_keyring():
    """Return the pinned SecretService keyring, or None if unavailable."""
    import keyring
    from keyring.backends import SecretService

    kr = SecretService.Keyring()
    import keyring.errors

    try:
        # Force a D-Bus round trip so a locked/missing collection surfaces now.
        _ = kr.get_password(KEYRING_SERVICE, "__probe__")
        return kr
    except keyring.errors.KeyringError as exc:
        log.warning("keyring unavailable: %s", type(exc).__name__)
        return None


def secret_save(kr, refresh_token: str) -> bool:
    import keyring.errors

    try:
        kr.set_password(KEYRING_SERVICE, KEYRING_ACCOUNT, refresh_token)
        return True
    except keyring.errors.KeyringError:
        log.warning("keyring write failed")
        return False


def secret_load(kr) -> str | None:
    import keyring.errors

    try:
        return kr.get_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
    except keyring.errors.KeyringError:
        log.warning("keyring read failed")
        return None


def secret_delete(kr) -> bool:
    import keyring.errors

    try:
        kr.delete_password(KEYRING_SERVICE, KEYRING_ACCOUNT)
        return True
    except keyring.errors.KeyringError:
        return False


# ---------------------------------------------------------------------------
# Non-secret config (no credentials here, ever)
# ---------------------------------------------------------------------------


def config_load() -> dict:
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def config_save(cfg: dict) -> None:
    os.makedirs(CONFIG_DIR, mode=0o700, exist_ok=True)
    os.chmod(CONFIG_DIR, 0o700)
    tmp = CONFIG_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
        fh.write("\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, CONFIG_PATH)


# ---------------------------------------------------------------------------
# URL validation and probing
# ---------------------------------------------------------------------------


def validate_base_url(raw: str) -> tuple[str | None, str]:
    """Normalize and validate the instance base URL.

    Returns (normalized_url, error). error is "" on success.
    """
    url = (raw or "").strip()
    if not url:
        return None, "Enter a URL"
    if "://" not in url:
        url = "http://" + url
    try:
        parts = urlparse(url)
    except ValueError:
        # urlparse raises on malformed input such as an unmatched IPv6 bracket.
        return None, "URL is malformed"
    if parts.scheme not in ("http", "https"):
        return None, "Only http:// or https:// URLs are supported"
    if not parts.hostname:
        return None, "URL has no host name"
    if parts.username or parts.password or "@" in url.split("://", 1)[1].split("/", 1)[0]:
        return None, "URLs with embedded credentials are not supported"
    if parts.query or parts.fragment:
        return None, "URL must be the server base address"
    # Drop any path; the base URL is scheme://host:port only.
    try:
        port = parts.port  # raises ValueError on a non-numeric/out-of-range port
        host = parts.hostname
    except ValueError:
        return None, "URL has an invalid port"
    if ":" in host:  # IPv6 literal: keep the brackets so the URL stays parseable
        host = f"[{host}]"
    netloc = host if port is None else f"{host}:{port}"
    normalized = f"{parts.scheme}://{netloc}"
    return normalized, ""


def websocket_origin(url: str) -> tuple[str, str, int]:
    """Compare HTTP/WS origins without treating a TLS downgrade as equivalent."""
    parts = urlparse(url)
    scheme = {"http": "ws", "https": "wss", "ws": "ws", "wss": "wss"}[parts.scheme]
    return scheme, parts.hostname, parts.port if parts.port is not None else (443 if scheme == "wss" else 80)


async def probe_server(session: aiohttp.ClientSession, base: str) -> tuple[bool, str]:
    """Probe reachability without assuming API access. Classifies failures."""
    try:
        async with session.get(base + PROBE_PATH, timeout=aiohttp.ClientTimeout(total=5)) as resp:
            if resp.status < 500:
                # 401 on /api is normal; /auth/providers answers 200.
                return True, ""
            return False, f"Server answered HTTP {resp.status}"
    except aiohttp.ClientConnectorCertificateError:
        return False, "TLS certificate error"
    except aiohttp.ClientSSLError:
        return False, "TLS handshake error"
    except aiohttp.ClientConnectorError as exc:
        os_error = getattr(exc, "os_error", None)
        if isinstance(os_error, socket.gaierror):
            return False, "Host not found (DNS)"
        return False, "Connection refused or unreachable"
    except asyncio.TimeoutError:
        return False, "Server did not answer (timeout)"
    except aiohttp.ClientError as exc:
        return False, f"Unexpected client error: {type(exc).__name__}"


# ---------------------------------------------------------------------------
# Controller
# ---------------------------------------------------------------------------


class Controller:
    def __init__(self) -> None:
        self.status = "setup"  # setup|validating|authorizing|connected|offline|keyring_locked|credentials_invalid
        self.status_detail = ""
        self.base_url: str | None = None
        self.ha_version = ""
        self.instance_id = ""

        self.keyring = None
        self.refresh_token: str | None = None
        self.access_token: str | None = None
        self.access_expires_at: float = 0.0

        self.clients: dict[asyncio.StreamWriter, dict] = {}
        self.next_req_id = 1
        self.reply_futures: dict[int, asyncio.Future] = {}

        self.ws_task: asyncio.Task | None = None
        self.ws_connected = False
        self.reconnect_delay_ms = RECONNECT_BASE_MS
        self.reconnect_timer: asyncio.TimerHandle | None = None
        self.refresh_timer: asyncio.TimerHandle | None = None
        self._refresh_task: asyncio.Task | None = None
        self._states_request_id: int | None = None
        self.stopping = False

        self.session_generation = 0
        self.pending_auth: dict | None = None  # active browser flow
        self.areas: list = []
        self.lights: list = []
        self.seq = 0

        self.http_session: aiohttp.ClientSession | None = None

        # Per-instance caches. These must never be class attributes: two
        # controllers (or a controller and a test) must not share entity or
        # registry state.
        self._live_ws: aiohttp.ClientWebSocketResponse | None = None  # set/cleared in ws_ready
        self._states: dict = {}  # entity_id -> normalized state
        self._state_attrs: dict = {}  # entity_id -> attributes
        self._registry: dict = {"areas": [], "devices": [], "entities": []}
        self._registry_limited: bool = False
        self._registry_rebuild_task: asyncio.Task | None = None
        self._sub_ids: set = set()

    # ---- IPC plumbing -----------------------------------------------------

    async def client_connected(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer_ok = False
        try:
            sock = writer.get_extra_info("socket")
            cred = sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            peer_ok = struct_unpack_uid(cred) == os.getuid()
        except (OSError, AttributeError):
            pass
        if not peer_ok:
            writer.close()
            return

        self.clients[writer] = {}
        log.info("client connected (%d total)", len(self.clients))
        try:
            await self.send_client(writer, self.snapshot_message())
            while True:
                try:
                    line = await reader.readline()
                except ValueError:
                    # readline raises when a line exceeds the stream limit and
                    # resets its buffer, so the stream can no longer be trusted.
                    # Reply, then drop the connection.
                    await self.send_client(writer, {"type": "error", "error": "message too large"})
                    break
                if not line:
                    break
                if len(line) > MAX_LINE_BYTES:
                    await self.send_client(writer, {"type": "error", "error": "message too large"})
                    continue
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    await self.send_client(writer, {"type": "error", "error": "bad json"})
                    continue
                if not isinstance(msg, dict):
                    await self.send_client(writer, {"type": "error", "error": "bad message"})
                    continue
                try:
                    await self.handle_command(writer, msg)
                except Exception:  # noqa: BLE001
                    log.exception("command %s failed", msg.get("cmd"))
                    if msg.get("id") is not None:
                        await self.send_client(writer, {"type": "reply", "id": msg.get("id"), "ok": False, "error": "internal error"})
        except (ConnectionResetError, asyncio.CancelledError):
            pass
        finally:
            self.clients.pop(writer, None)
            try:
                writer.close()
            except Exception:
                pass
            log.info("client disconnected (%d total)", len(self.clients))

    async def send_client(self, writer: asyncio.StreamWriter, msg: dict) -> None:
        try:
            writer.write((json.dumps(msg) + "\n").encode())
            await writer.drain()
        except (ConnectionResetError, RuntimeError):
            self.clients.pop(writer, None)

    def broadcast(self, msg: dict) -> None:
        for writer in list(self.clients):
            asyncio.create_task(self.send_client(writer, msg))

    def snapshot_message(self) -> dict:
        return {
            "type": "snapshot",
            "seq": self.seq,
            "status": self.status,
            "detail": self.status_detail,
            "server": {
                "url": self.base_url or "",
                "version": self.ha_version,
                "http": bool(self.base_url and self.base_url.startswith("http://")),
            },
            "areas": self.areas,
            "lights": self.lights,
        }

    def set_status(self, status: str, detail: str = "") -> None:
        if self.status == status and self.status_detail == detail:
            return
        self.status = status
        self.status_detail = detail
        self.seq += 1
        self.broadcast({"type": "status", "seq": self.seq, "status": status, "detail": detail})
        self.broadcast({"type": "update", "seq": self.seq, "changes": []})

    # ---- command handling ---------------------------------------------------

    async def handle_command(self, writer: asyncio.StreamWriter, msg: dict) -> None:
        cmd = msg.get("cmd")
        req_id = msg.get("id")

        async def reply(ok: bool, error: str | None = None, extra: dict | None = None) -> None:
            if req_id is None:
                return
            payload = {"type": "reply", "id": req_id, "ok": ok}
            if error:
                payload["error"] = error
            if extra:
                payload.update(extra)
            await self.send_client(writer, payload)

        if cmd == "status":
            await reply(True, extra={
                "status": self.status, "detail": self.status_detail,
                "ha_version": self.ha_version, "lights": len(self.lights),
            })

        elif cmd == "setup.url":
            await self.cmd_setup_url(msg, reply)

        elif cmd == "setup.browser":
            await self.cmd_setup_browser(reply)

        elif cmd == "setup.cancel":
            self.cancel_browser_auth()
            await reply(True)

        elif cmd == "retry.keyring":
            await self.cmd_retry_keyring(reply)

        elif cmd == "signout":
            await self.cmd_signout(reply)

        elif cmd == "light.set":
            await self.cmd_light_set(msg, reply)

        elif cmd == "area.all":
            await self.cmd_area_all(msg, reply)

        elif cmd == "picker.data":
            await self.cmd_picker_data(reply)

        elif cmd == "picker.save":
            await self.cmd_picker_save(msg, reply)

        elif cmd == "cli.exec":
            await self.cmd_cli_exec(msg, reply)

        else:
            await reply(False, error=f"unknown cmd: {cmd!r}")

    HASS_CLI_PATH = os.path.expanduser(
        os.environ.get("OMARCHY_HA_HASS_CLI")
        or os.path.join(os.environ.get("XDG_DATA_HOME") or "~/.local/share",
                        "omarchy-home-assistant/hass-cli-venv/bin/hass-cli")
    )
    CLI_TIMEOUT_S = 60
    CLI_MAX_OUTPUT = 512 * 1024

    async def cmd_cli_exec(self, msg: dict, reply) -> None:
        """Run hass-cli for the omarchy-ha launcher.

        The token never travels over IPC or argv: the launcher sends only the
        argument vector, and the controller injects HASS_SERVER/HASS_TOKEN
        into the child environment (documented same-user exposure, plan §6).
        """
        if self.status != "connected":
            await reply(False, error="offline")
            return
        ok, reason, cleaned = validate_cli_args(msg.get("args"))
        if not ok:
            await reply(False, error=reason)
            return
        if not os.path.isfile(self.HASS_CLI_PATH) or not os.access(self.HASS_CLI_PATH, os.X_OK):
            await reply(False, error="hass-cli not installed — run controller/install.sh")
            return
        # A current access token, refreshing if it is close to expiry.
        if not await self.refresh_access_token():
            await reply(False, error="no valid session")
            return

        child_secrets = [s for s in (self.access_token, self.refresh_token) if s]
        env = dict(os.environ)
        env["HASS_SERVER"] = self.base_url or ""
        env["HASS_TOKEN"] = self.access_token or ""
        argv = [self.HASS_CLI_PATH] + cleaned
        log.info("cli.exec: hass-cli %s", " ".join(cleaned[:6]))
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env,
            )
            try:
                out, err = await collect_cli_output(proc, self.CLI_MAX_OUTPUT, self.CLI_TIMEOUT_S)
            except CLIOutputLimit:
                await reply(False, error="hass-cli output too large")
                return
            except asyncio.TimeoutError:
                await reply(False, error="hass-cli timed out")
                return
        except OSError as exc:
            log.warning("cli.exec spawn failed: %s", type(exc).__name__)
            await reply(False, error="could not run hass-cli")
            return

        secrets = child_secrets + [s for s in (self.access_token, self.refresh_token) if s]
        stdout = redact_output(out.decode("utf-8", "replace"), secrets)[: self.CLI_MAX_OUTPUT]
        stderr = redact_output(err.decode("utf-8", "replace"), secrets)[: 16 * 1024]
        await reply(True, extra={"code": proc.returncode, "stdout": stdout, "stderr": stderr})

    async def cmd_picker_data(self, reply) -> None:
        """Everything available for selection, unfiltered, plus the current pick."""
        if self.status != "connected":
            await reply(False, error="offline")
            return
        areas, lights, _ = resolve_topology(
            self._states, self._state_attrs,
            self._registry["areas"], self._registry["devices"], self._registry["entities"],
        )
        await reply(True, extra={
            "areas": areas, "lights": lights, "selection": self.light_selection(),
        })

    async def cmd_picker_save(self, msg: dict, reply) -> None:
        areas_in = msg.get("areas")
        entities_in = msg.get("entities")
        if not isinstance(areas_in, list) or not isinstance(entities_in, list):
            await reply(False, error="areas and entities must be lists")
            return
        clean_areas, clean_entities = [], []
        for a in areas_in:
            if isinstance(a, str) and 0 < len(a) <= 64 and all(c.isalnum() or c in "-_" for c in a):
                clean_areas.append(a)
        for e in entities_in:
            parts = e.split(".") if isinstance(e, str) else []
            if len(parts) == 2 and parts[0] == "light" and parts[1] and len(e) <= 64:
                clean_entities.append(e)
        cfg = config_load()
        cfg["selected_lights"] = {"areas": clean_areas, "entities": clean_entities}
        config_save(cfg)
        self.rebuild_topology()
        await reply(True, extra={"areas": len(clean_areas), "entities": len(clean_entities)})

    async def cmd_setup_url(self, msg: dict, reply) -> None:
        base, err = validate_base_url(str(msg.get("url", "")))
        if err:
            await reply(False, error=err)
            return
        self.set_status("validating", "Checking server")
        ok, probe_err = await self.probe_server(base)
        if not ok:
            self.set_status("setup", "")
            await reply(False, error=probe_err)
            return
        if self.base_url and self.base_url != base:
            # A refresh/access token is only valid for the instance that issued
            # it. Never carry a session across a server change: the next
            # refresh would otherwise POST the old token to the new host.
            self._clear_session()
        self.base_url = base
        self.instance_id = urlparse(base).netloc
        cfg = config_load()
        cfg["url"] = base
        cfg["instance_id"] = self.instance_id
        cfg["setup_complete"] = False
        config_save(cfg)
        self.set_status("setup", "URL verified; ready for sign-in")
        # URL metadata lives in snapshots; publish it immediately so existing
        # widget clients do not keep opening the previous server.
        self.broadcast(self.snapshot_message())
        await reply(True, extra={"url": base, "http": base.startswith("http://")})

    async def probe_server(self, base: str) -> tuple[bool, str]:
        if self.http_session is None:
            self.http_session = aiohttp.ClientSession()
        return await probe_server(self.http_session, base)

    async def cmd_setup_browser(self, reply) -> None:
        if self.pending_auth is not None:
            await reply(False, error="Sign-in already in progress")
            return
        if not self.base_url:
            await reply(False, error="Set the server URL first")
            return
        flow = await self.start_browser_auth()
        if flow is None:
            await reply(False, error="Could not start local callback listener")
            return
        await reply(True, extra={"authorize_url": flow["authorize_url"]})

    async def cmd_retry_keyring(self, reply) -> None:
        self.keyring = load_keyring()
        if self.keyring is None:
            await reply(False, error="Keyring still unavailable")
            self.set_status("keyring_locked", "Keyring unavailable")
            return
        await self.startup_connect()
        await reply(True)

    async def cmd_signout(self, reply) -> None:
        await self.signout(revoke=True)
        await reply(True)

    async def cmd_area_all(self, msg: dict, reply) -> None:
        area_id = msg.get("area_id", "")
        on = bool(msg.get("on"))
        if not isinstance(area_id, str) or len(area_id) > 64:
            await reply(False, error="invalid area_id")
            return
        if self.status != "connected":
            await reply(False, error="offline")
            return
        targets = area_targets(self.lights, area_id)
        if not targets:
            # Unavailable lights are not controllable; do not fan out to the
            # whole area (plan §4).
            await reply(True, extra={"targets": 0})
            return
        service = "turn_on" if on else "turn_off"
        result = await self.ws_command(
            {"type": "call_service", "domain": "light", "service": service,
             "target": {"entity_id": targets}}
        )
        ok = result is not None and result.get("success", False)
        await reply(ok, error=None if ok else "service call failed",
                    extra={"targets": len(targets)})

    async def cmd_light_set(self, msg: dict, reply) -> None:
        try:
            service, service_data = validate_light_set(msg, self.lights)
        except ValueError as exc:
            await reply(False, error=str(exc))
            return
        if self.status != "connected":
            await reply(False, error="offline")
            return
        payload = {"type": "call_service", "domain": "light", "service": service,
                   "target": {"entity_id": [msg["entity_id"]]}}
        if service_data:
            payload["service_data"] = service_data
        result = await self.ws_command(payload)
        ok = result is not None and result.get("success", False)
        await reply(ok, error=None if ok else "service call failed")

    # ---- browser authorization flow ----------------------------------------

    async def start_browser_auth(self) -> dict | None:
        """Bind a loopback listener, open HA's authorize page in the browser."""
        generation = self.session_generation
        try:
            server = await asyncio.start_server(
                self._callback_handler, host="127.0.0.1", port=0, family=socket.AF_INET
            )
        except OSError as exc:
            log.warning("callback bind failed: %s", type(exc).__name__)
            return None
        if generation != self.session_generation:
            server.close()
            await server.wait_closed()
            return None
        self.session_generation += 1
        port = server.sockets[0].getsockname()[1]
        redirect_uri = f"http://127.0.0.1:{port}/"
        client_id = redirect_uri  # same scheme+netloc passes HA's IndieAuth check
        state = secrets.token_urlsafe(32)
        authorize_url = self.base_url + AUTHORIZE_PATH + "?" + urlencode(
            {"client_id": client_id, "redirect_uri": redirect_uri, "state": state}
        )
        self.pending_auth = {
            "server": server,
            "state": state,
            "redirect_uri": redirect_uri,
            "client_id": client_id,
            "deadline": time.monotonic() + CALLBACK_TIMEOUT_S,
            "future": asyncio.get_running_loop().create_future(),
        }
        self.set_status("authorizing", "Waiting for browser sign-in")
        asyncio.create_task(self._auth_flow_watchdog())
        try:
            proc = await asyncio.create_subprocess_exec(
                "xdg-open", authorize_url,
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
        except FileNotFoundError:
            log.warning("xdg-open missing; authorize URL offered over IPC")
        log.info("authorization flow started (loopback port %d)", port)
        return {"authorize_url": authorize_url}

    async def _auth_flow_watchdog(self) -> None:
        pending = self.pending_auth
        if pending is None:
            return
        remaining = pending["deadline"] - time.monotonic()
        try:
            code = await asyncio.wait_for(pending["future"], timeout=max(1.0, remaining))
        except asyncio.TimeoutError:
            log.info("authorization flow timed out")
            if pending is self.pending_auth:
                self.finish_browser_auth("Sign-in timed out")
            return
        except asyncio.CancelledError:
            return
        if pending is self.pending_auth:
            await self.exchange_code(code)

    def finish_browser_auth(self, detail: str = "") -> None:
        pending = self.pending_auth
        if pending is None:
            return
        self.session_generation += 1
        self.pending_auth = None
        try:
            pending["server"].close()
        except Exception:
            pass
        if not pending["future"].done():
            pending["future"].cancel()
        if detail:
            self.set_status("setup", detail)

    def cancel_browser_auth(self) -> None:
        self.session_generation += 1
        self.finish_browser_auth("Sign-in cancelled")

    async def _callback_handler(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """Minimal HTTP responder for the OAuth loopback callback."""
        try:
            data = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), timeout=10)
        except (asyncio.TimeoutError, asyncio.IncompleteReadError, ConnectionResetError):
            writer.close()
            return
        head = data.split(b"\r\n", 1)[0].decode("latin-1", "replace")
        parts = head.split(" ")
        if len(parts) < 2 or parts[0] != "GET":
            writer.close()
            return
        from urllib.parse import parse_qs, urlsplit

        target = urlsplit(parts[1])
        params = parse_qs(target.query)
        pending = self.pending_auth

        body_ok = (
            b"<html><body><h2>Home Assistant sign-in complete.</h2>"
            b"<p>You can close this tab and return to the setup window.</p></body></html>"
        )
        body_err = (
            b"<html><body><h2>Sign-in failed.</h2>"
            b"<p>The callback did not match the pending sign-in request. Close this tab and try again.</p></body></html>"
        )

        if pending is None or target.path != "/":
            self._http_respond(writer, 400, body_err)
            return
        # Strict callback validation: single-use state, exact match, loopback only.
        # Compare in constant time (as bytes, so non-ASCII input cannot raise) so
        # a local process probing the loopback port cannot learn the state.
        if not secrets.compare_digest(
            params.get("state", [""])[0].encode("utf-8"), pending["state"].encode("utf-8")
        ):
            log.warning("callback rejected: state mismatch")
            self._http_respond(writer, 400, body_err)
            return
        if "error" in params:
            self._http_respond(writer, 200, body_err)
            self.finish_browser_auth("Sign-in was cancelled in the browser")
            return
        code = params.get("code", [""])[0]
        if not code:
            self._http_respond(writer, 400, body_err)
            return
        if not pending["future"].done():
            pending["future"].set_result(code)
        self._http_respond(writer, 200, body_ok)

    def _http_respond(self, writer: asyncio.StreamWriter, code: int, body: bytes) -> None:
        reason = {200: "OK", 400: "Bad Request"}.get(code, "OK")
        writer.write(
            b"HTTP/1.1 %d %s\r\nContent-Type: text/html; charset=utf-8\r\n"
            b"Content-Length: %d\r\nConnection: close\r\n\r\n%s"
            % (code, reason.encode(), len(body), body)
        )
        writer.close()

    async def exchange_code(self, code: str) -> None:
        pending = self.pending_auth
        if pending is None:
            return
        generation = self.session_generation
        data = {
            "grant_type": "authorization_code",
            "code": code,
            "client_id": pending["client_id"],
        }
        try:
            tokens = await self.token_request(data)
        except TokenError as exc:
            if generation != self.session_generation or pending is not self.pending_auth:
                return
            log.warning("token exchange failed: %s", exc.kind)
            self.finish_browser_auth("Sign-in failed (" + exc.kind + ")")
            return
        if generation != self.session_generation or pending is not self.pending_auth:
            return
        self.refresh_token = tokens["refresh_token"]
        self.apply_access_token(tokens, refresh_token=tokens["refresh_token"])
        saved = secret_save(self.keyring, self.refresh_token) if self.keyring else False
        cfg = config_load()
        cfg["setup_complete"] = True
        cfg["url"] = self.base_url
        cfg["instance_id"] = self.instance_id
        # HA's refresh endpoint validates client_id against the one used at
        # authorize time. Ours embeds an ephemeral port, so persist the exact
        # string (non-secret) or the session cannot survive a restart.
        cfg["client_id"] = pending["client_id"]
        config_save(cfg)
        self.finish_browser_auth()
        if not saved:
            self.set_status("keyring_locked", "Signed in, but keyring storage failed")
            return
        log.info("authorization succeeded")
        await self.connect_ws()

    async def token_request(self, data: dict) -> dict:
        assert self.base_url and self.http_session
        try:
            async with self.http_session.post(
                self.base_url + TOKEN_PATH, data=data,
                timeout=aiohttp.ClientTimeout(total=10), allow_redirects=False,
            ) as resp:
                if resp.status != 200:
                    body = await resp.text()
                    kind = "invalid_grant" if "invalid_grant" in body else f"http_{resp.status}"
                    raise TokenError(kind)
                payload = await resp.json()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            raise TokenError("network")
        if not isinstance(payload, dict) or "access_token" not in payload:
            raise TokenError("malformed_response")
        return payload

    def authorize_client_id(self) -> str | None:
        if self.pending_auth:
            return self.pending_auth["client_id"]
        cfg = config_load()
        cid = cfg.get("client_id")
        return str(cid) if cid else None

    def apply_access_token(self, payload: dict, refresh_token: str | None = None) -> None:
        self.access_token = payload["access_token"]
        if refresh_token:
            self.refresh_token = refresh_token
        expires_in = int(payload.get("expires_in", 1800))
        self.access_expires_at = time.monotonic() + expires_in
        ha_version = payload.get("ha_version")
        if ha_version:
            self.ha_version = str(ha_version)
        self.schedule_refresh(expires_in)

    def schedule_refresh(self, expires_in: int) -> None:
        loop = asyncio.get_running_loop()
        if self.refresh_timer:
            self.refresh_timer.cancel()
        delay = max(30, int(expires_in * 0.75))
        async def refresh(generation: int) -> None:
            if generation == self.session_generation and not self.stopping:
                await self.refresh_access_token(force=True)

        def start_refresh() -> None:
            self.refresh_timer = None
            self._refresh_task = asyncio.create_task(refresh(self.session_generation))

        self.refresh_timer = loop.call_later(delay, start_refresh)

    async def refresh_access_token(self, force: bool = False) -> bool:
        if self.stopping or not self.refresh_token or not self.base_url:
            return False
        if not force and self.access_token and time.monotonic() < self.access_expires_at - REFRESH_MARGIN_S:
            return True
        generation = self.session_generation
        client_id = self.authorize_client_id()
        if not client_id:
            log.warning("no persisted client_id; cannot refresh")
            self.set_status("credentials_invalid", "Sign in again")
            return False
        try:
            tokens = await self.token_request(
                {"grant_type": "refresh_token", "refresh_token": self.refresh_token,
                 "client_id": client_id}
            )
        except TokenError as exc:
            if generation != self.session_generation or self.stopping:
                return False
            log.warning("token refresh failed: %s", exc.kind)
            if exc.kind == "invalid_grant":
                # Revoked/expired: stop retrying, ask the user to reconnect.
                self.refresh_token = None
                if self.refresh_timer:
                    self.refresh_timer.cancel()
                    self.refresh_timer = None
                if self.keyring:
                    secret_delete(self.keyring)
                self.set_status("credentials_invalid", "Session expired; sign in again")
            else:
                self.set_status("offline", "Could not refresh session")
                self.schedule_refresh(0)  # Retry transient failures after 30 seconds.
            return False
        if generation != self.session_generation or self.stopping:
            return False
        self.apply_access_token(tokens)
        if self.ws_connected and self._live_ws is not None and not self._live_ws.closed:
            self.set_status("connected", self.registry_status_detail())
        log.info("access token refreshed")
        return True
    # ---- websocket ----------------------------------------------------------

    async def connect_ws(self) -> None:
        if self.ws_task and not self.ws_task.done():
            return
        self.ws_task = asyncio.create_task(self.ws_loop())

    async def ws_loop(self) -> None:
        assert self.base_url
        ws_url = urlunparse(urlparse(self.base_url)._replace(scheme="ws" if self.base_url.startswith("http://") else "wss")) + WS_PATH
        while not self.stopping:
            if not self.access_token:
                ok = await self.refresh_access_token(force=True)
                if not ok:
                    await self.ws_reconnect_wait()
                    continue
            try:
                await self.ws_session(ws_url)
            except (aiohttp.ClientError, asyncio.TimeoutError, ConnectionError) as exc:
                log.info("websocket dropped: %s", type(exc).__name__)
            except Exception as exc:  # noqa: BLE001
                log.warning("websocket error: %s: %s", type(exc).__name__, exc)
            if self.stopping:
                break
            self.ws_connected = False
            if self.status == "connected":
                self.set_status("offline", "Connection lost; reconnecting")
            await self.ws_reconnect_wait()

    async def ws_reconnect_wait(self) -> None:
        jitter = secrets.randbelow(500)
        delay = min(self.reconnect_delay_ms * 2, RECONNECT_CAP_MS) + jitter
        self.reconnect_delay_ms = min(self.reconnect_delay_ms * 2, RECONNECT_CAP_MS)
        await asyncio.sleep(delay / 1000)

    async def ws_session(self, ws_url: str) -> None:
        assert self.http_session
        generation = self.session_generation
        expected_origin = websocket_origin(self.base_url)
        # NB: ClientTimeout(total=...) would close the session after N seconds
        # total. Only per-receive and close timeouts apply to a WebSocket.
        ws_timeout = aiohttp.ClientWSTimeout(ws_receive=None, ws_close=10)
        async with self.http_session.ws_connect(
            ws_url, timeout=ws_timeout, max_msg_size=8 * 1024 * 1024,
            proxy=None,
        ) as ws:
            # aiohttp exposes the final handshake URL only on its response.
            # Fail closed if this API changes; no credentials have been sent.
            if websocket_origin(str(ws._response.url)) != expected_origin:
                raise ConnectionError("WebSocket redirected to another origin")
            msg = await ws.receive_json(timeout=15)
            if msg.get("type") != "auth_required":
                raise ConnectionError(f"unexpected first message: {msg.get('type')}")
            if generation != self.session_generation:
                return
            self.ha_version = str(msg.get("ha_version", self.ha_version))
            await ws.send_json({"type": "auth", "access_token": self.access_token})
            msg = await ws.receive_json(timeout=15)
            if msg.get("type") == "auth_invalid":
                # One refresh attempt before giving up.
                if await self.refresh_access_token(force=True) and generation == self.session_generation:
                    await ws.send_json({"type": "auth", "access_token": self.access_token})
                    msg = await ws.receive_json(timeout=15)
                if generation != self.session_generation:
                    return
                if msg.get("type") != "auth_ok":
                    self.set_status("credentials_invalid", "Session expired; sign in again")
                    return
            if msg.get("type") != "auth_ok":
                raise ConnectionError(f"auth failed: {msg.get('type')}")
            if generation != self.session_generation:
                return
            self.reconnect_delay_ms = RECONNECT_BASE_MS
            await self.ws_ready(ws)

    async def ws_ready(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        self._live_ws = ws
        try:
            await self._ws_ready_inner(ws)
        finally:
            self._live_ws = None

    async def _ws_ready_inner(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        # The reader must run concurrently from the first request onward;
        # a sequential read-after-command pattern deadlocks on the first reply.
        reader_task = asyncio.create_task(self._ws_reader(ws))
        try:
            self.ws_connected = True
            self.set_status("connected", "")
            self.seq += 1

            # Subscribe to state changes BEFORE the snapshot so no change is
            # missed, then reconcile: the plan's initialize-cannot-miss-a-change
            # ordering. Registry-change events refresh topology too.
            sub_ids: set = set()
            for event_type in (
                "state_changed",
                "area_registry_updated",
                "device_registry_updated",
                "entity_registry_updated",
            ):
                sub = await self.ws_command(
                    {"type": "subscribe_events", "event_type": event_type}
                )
                if sub is None or not sub.get("success", True):
                    if event_type == "state_changed":
                        raise ConnectionError("subscribe failed")
                    log.warning("could not subscribe to %s", event_type)
                    continue
                sub_ids.add(sub.get("id"))
            self._sub_ids = sub_ids
            states = await self.ws_command({"type": "get_states"})
            if states is None:
                raise ConnectionError("get_states failed")
            result = states.get("result") or []
            if not states.get("success", True):
                log.warning("get_states error: %s", states.get("error"))
            light_count = sum(
                1 for st in result
                if isinstance(st, dict) and str(st.get("entity_id", "")).startswith("light.")
            )
            log.info("get_states: %d entities, %d lights", len(result), light_count)
            # The reader applies get_states before processing subsequent events.
            # Always finish bootstrap with one complete snapshot. The topology
            # may equal cached data, in which case change detection alone would
            # leave existing widget clients with empty pre-sign-in state.
            await self.fetch_registries(force_snapshot=True)
            self.set_status("connected", self.registry_status_detail())
            await reader_task
        finally:
            self.ws_connected = False
            self._live_ws = None
            reader_task.cancel()

    def registry_status_detail(self) -> str:
        return "Registry access limited — lights grouped as Unassigned" if self._registry_limited else ""

    async def fetch_registries(self, force_snapshot: bool = False) -> None:
        """Load area/device/entity registries; degrade gracefully without them.

        Plan §4: validate registry-command permissions under an ordinary
        account; if discovery requires elevated permissions, explain the
        limitation (all lights land in Unassigned) and keep explicit control
        working.
        """
        fetched: dict = {"areas": [], "devices": [], "entities": []}
        failed = False
        for key, command in (
            ("areas", {"type": "config/area_registry/list"}),
            ("devices", {"type": "config/device_registry/list"}),
            ("entities", {"type": "config/entity_registry/list"}),
        ):
            reply = await self.ws_command(command)
            if reply is None or not reply.get("success", False):
                error = (reply or {}).get("error")
                log.warning("registry fetch failed for %s: %s", key, error)
                failed = True
                continue
            result = reply.get("result")
            fetched[key] = result if isinstance(result, list) else []
        self._registry = fetched
        self._registry_limited = failed
        self.rebuild_topology(broadcast=not force_snapshot)
        if force_snapshot:
            self.broadcast(self.snapshot_message())
        if failed:
            self.set_status("connected", self.registry_status_detail())
        else:
            log.info(
                "registries: %d areas, %d devices, %d entities",
                len(fetched["areas"]), len(fetched["devices"]), len(fetched["entities"]),
            )

    def schedule_registry_rebuild(self) -> None:
        """Debounced refresh: registry events can arrive in bursts."""
        if self._registry_rebuild_task and not self._registry_rebuild_task.done():
            return
        async def _rebuild() -> None:
            await asyncio.sleep(1.0)
            await self.fetch_registries()
        self._registry_rebuild_task = asyncio.create_task(_rebuild())

    _sub_id: int | None = None  # legacy; superseded by _sub_ids

    async def _ws_reader(self, ws: aiohttp.ClientWebSocketResponse) -> None:
        while not ws.closed:
            msg = await ws.receive(timeout=None)
            if msg.type == aiohttp.WSMsgType.TEXT:
                try:
                    data = json.loads(msg.data)
                except json.JSONDecodeError:
                    continue
                mtype = data.get("type")
                if mtype == "event":
                    if data.get("id") in self._sub_ids:
                        ev = data.get("event") or {}
                        if ev.get("event_type") == "state_changed":
                            self.handle_state_event(ev)
                        elif ev.get("event_type") in (
                            "area_registry_updated",
                            "device_registry_updated",
                            "entity_registry_updated",
                        ):
                            self.schedule_registry_rebuild()
                elif mtype == "result":
                    fut = self.reply_futures.pop(data.get("id"), None)
                    if fut and not fut.done():
                        if data.get("id") == self._states_request_id and data.get("success", True):
                            # Commit the snapshot in wire order, before later events.
                            self.apply_states(data.get("result") or [])
                        fut.set_result(data)
                # pong/other types ignored
            elif msg.type in (aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.ERROR):
                break

    async def ws_command(self, payload: dict, timeout: float = 15) -> dict | None:
        if not self.ws_connected:
            return None
        loop = asyncio.get_running_loop()
        req_id = self.next_req_id
        self.next_req_id += 1
        payload = dict(payload)
        payload["id"] = req_id
        fut = loop.create_future()
        self.reply_futures[req_id] = fut

        # Find the live ws from the running task; simplest: send via a stored ref.
        ws = self._live_ws
        if ws is None or ws.closed:
            self.reply_futures.pop(req_id, None)
            return None
        if payload.get("type") == "get_states":
            self._states_request_id = req_id
        try:
            await ws.send_json(payload)
            return await asyncio.wait_for(fut, timeout=timeout)
        except (asyncio.TimeoutError, ConnectionResetError):
            self.reply_futures.pop(req_id, None)
            return None
        finally:
            if self._states_request_id == req_id:
                self._states_request_id = None

    # ---- state handling -------------------------------------------------------

    def apply_states(self, states: list) -> None:
        self._states = {}
        self._state_attrs = {}
        for st in states or []:
            if not isinstance(st, dict):
                continue
            entity_id = st.get("entity_id")
            if not isinstance(entity_id, str) or not entity_id:
                continue
            state = st.get("state", "unknown")
            self._states[entity_id] = state if state in ("on", "off") else "unavailable"
            attrs = st.get("attributes")
            self._state_attrs[entity_id] = attrs if isinstance(attrs, dict) else {}
        self.rebuild_topology(broadcast=False)

    def rebuild_topology(self, broadcast: bool = True, incremental: bool = False) -> None:
        """Recompute areas/lights from the state + registry caches."""
        areas, lights, dropped = resolve_topology(
            self._states, self._state_attrs,
            self._registry["areas"], self._registry["devices"], self._registry["entities"],
            selection=self.light_selection(),
        )
        changed = areas != self.areas or lights != self.lights
        topology_keys = ("entity_id", "name", "area", "icon")
        topology_changed = areas != self.areas or [
            tuple(light[key] for key in topology_keys) for light in lights
        ] != [tuple(light[key] for key in topology_keys) for light in self.lights]
        previous = {light["entity_id"]: light for light in self.lights}
        self.areas = areas
        self.lights = lights
        self.seq += 1
        if changed and broadcast:
            if incremental and not topology_changed:
                self.broadcast({"type": "update", "seq": self.seq, "changes": [
                    light for light in lights if previous.get(light["entity_id"]) != light
                ]})
            else:
                # Membership/metadata changes require a complete replacement.
                self.broadcast(self.snapshot_message())
        if dropped:
            log.info("topology: dropped %s", ", ".join(
                f"{d['entity_id']}({d['reason']})" for d in dropped))

    def light_selection(self) -> dict:
        """User's picker selection from config; empty = show everything."""
        cfg = config_load()
        sel = cfg.get("selected_lights")
        return sel if isinstance(sel, dict) else {}

    def handle_state_event(self, event: dict) -> None:
        data = event.get("data") or {}
        new_state = data.get("new_state")
        old_state = data.get("old_state")
        entity_id = (new_state or old_state or {}).get("entity_id", "")
        if not entity_id.startswith("light."):
            return
        if new_state is None:
            # Entity removed: drop it from caches and topology.
            self._states.pop(entity_id, None)
            self._state_attrs.pop(entity_id, None)
            self.rebuild_topology()
            return
        state = new_state.get("state", "unknown")
        norm = state if state in ("on", "off") else "unavailable"
        self._states[entity_id] = norm
        attrs = new_state.get("attributes")
        self._state_attrs[entity_id] = attrs if isinstance(attrs, dict) else {}

        # Reapply visibility before emitting anything, including attribute-only
        # changes (which can also identify a newly aggregated light group).
        self.rebuild_topology(incremental=True)

    # ---- lifecycle ------------------------------------------------------------

    async def startup(self) -> None:
        self.keyring = load_keyring()
        cfg = config_load()
        if cfg.get("url"):
            base, err = validate_base_url(str(cfg["url"]))
            if not err:
                self.base_url = base
                self.instance_id = cfg.get("instance_id") or urlparse(base).netloc
        self.http_session = aiohttp.ClientSession()
        if self.keyring is None and cfg.get("setup_complete"):
            self.set_status("keyring_locked", "Keyring unavailable; unlock and retry")
            return
        await self.startup_connect()

    async def startup_connect(self) -> None:
        if not self.base_url:
            self.set_status("setup", "")
            return
        ok, probe_err = await self.probe_server(self.base_url)
        if not ok:
            self.set_status("offline", probe_err)
            # Keep retrying in the background while the server is unreachable.
            asyncio.create_task(self.probe_until_reachable())
            return
        self.refresh_token = secret_load(self.keyring) if self.keyring else None
        if not self.refresh_token:
            self.set_status("setup", "Sign in to Home Assistant")
            return
        if await self.refresh_access_token(force=True):
            await self.connect_ws()
        elif self.status == "offline":
            # Network hiccup, not bad credentials: keep probing in background.
            asyncio.create_task(self.probe_until_reachable())
        # refresh_access_token() sets the failure status itself.

    async def probe_until_reachable(self) -> None:
        while self.status == "offline" and not self.stopping and self.base_url:
            await asyncio.sleep(10)
            if self.stopping:
                return
            ok, _ = await self.probe_server(self.base_url)
            if ok and not self.stopping:
                await self.startup_connect()
                return

    def _clear_session(self) -> None:
        """Drop all live session state plus the stored refresh token.

        Shared by sign-out and by a server change. Tokens are bound to the
        instance that issued them, so a server switch must leave nothing
        behind for the next request to send to the new host.
        """
        if self.ws_task:
            self.ws_task.cancel()
            self.ws_task = None
        self.ws_connected = False
        self._live_ws = None
        self.cancel_browser_auth()
        if self._refresh_task:
            self._refresh_task.cancel()
            self._refresh_task = None
        if self.refresh_timer:
            self.refresh_timer.cancel()
            self.refresh_timer = None
        self.refresh_token = None
        self.access_token = None
        self.access_expires_at = 0.0
        self.lights = []
        self.areas = []
        # Entity caches belong to the previous server; drop them so stale
        # entities cannot leak into the next server's topology.
        self._states = {}
        self._state_attrs = {}
        self._registry = {"areas": [], "devices": [], "entities": []}
        if self.keyring:
            secret_delete(self.keyring)

    async def signout(self, revoke: bool = True) -> None:
        self.stopping = False
        token, base = self.refresh_token, self.base_url
        # Local signout must finish before revocation yields to other requests.
        self._clear_session()
        generation = self.session_generation
        cfg = config_load()
        cfg["setup_complete"] = False
        config_save(cfg)
        self.set_status("setup", "")
        self.seq += 1
        self.broadcast(self.snapshot_message())
        detail = ""
        if revoke and token and base:
            try:
                async with self.http_session.post(
                    base + REVOKE_PATH, data={"token": token},
                    timeout=aiohttp.ClientTimeout(total=5), allow_redirects=False,
                ) as resp:
                    if resp.status != 200:
                        detail = "Signed out locally; the server session may still be active — revoke it in your Home Assistant profile"
            except (aiohttp.ClientError, asyncio.TimeoutError):
                detail = "Signed out locally; the server session may still be active — revoke it in your Home Assistant profile"
        if detail and generation == self.session_generation:
            self.set_status("setup", detail)

    async def run(self) -> None:
        os.makedirs(SOCKET_DIR, mode=0o700, exist_ok=True)
        os.chmod(SOCKET_DIR, 0o700)
        try:
            os.unlink(SOCKET_PATH)
        except FileNotFoundError:
            pass
        server = await asyncio.start_unix_server(
            self.client_connected, path=SOCKET_PATH, limit=STREAM_LIMIT
        )
        os.chmod(SOCKET_PATH, 0o600)
        await self.startup()
        log.info("listening on %s", SOCKET_PATH)
        async with server:
            await server.serve_forever()

    async def aclose(self) -> None:
        self.stopping = True
        if self.refresh_timer:
            self.refresh_timer.cancel()
            self.refresh_timer = None
        if self._refresh_task:
            self._refresh_task.cancel()
            await asyncio.gather(self._refresh_task, return_exceptions=True)
            self._refresh_task = None
        if self.ws_task:
            self.ws_task.cancel()
        if self.http_session:
            await self.http_session.close()
        try:
            os.unlink(SOCKET_PATH)
        except FileNotFoundError:
            pass


class CLIOutputLimit(Exception):
    pass


async def collect_cli_output(proc, limit: int, timeout: float) -> tuple[bytes, bytes]:
    """Bound combined pipe storage; always terminate and reap interrupted children."""
    total = 0

    async def drain(stream, capture=True):
        nonlocal total
        chunks = []
        while chunk := await stream.read(8192):
            if capture:
                total += len(chunk)
                if total > limit:
                    raise CLIOutputLimit
                chunks.append(chunk)
        return b"".join(chunks)

    tasks = [asyncio.create_task(drain(proc.stdout)), asyncio.create_task(drain(proc.stderr))]
    try:
        async def collect():
            output = await asyncio.gather(*tasks)
            await proc.wait()
            return tuple(output)
        return await asyncio.wait_for(collect(), timeout)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if proc.returncode is None:
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        # A descendant can inherit these descriptors and keep them open after
        # the direct child dies. Close our pipe transports instead of waiting
        # indefinitely for EOF, then reap the direct child.
        for stream in (proc.stdout, proc.stderr):
            transport = getattr(stream, "_transport", None)
            if transport is not None:
                transport.close()
        await proc.wait()


class TokenError(Exception):
    def __init__(self, kind: str) -> None:
        super().__init__(kind)
        self.kind = kind


def struct_unpack_uid(cred) -> int | None:
    # SO_PEERCRED returns a packed struct(u32 pid, u32 uid, u32 gid) on Linux.
    import struct

    try:
        return struct.unpack("3i", cred)[1]
    except Exception:
        return None


async def amain() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    controller = Controller()
    try:
        await controller.run()
    finally:
        await controller.aclose()


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass
