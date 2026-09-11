#!/usr/bin/env python3
"""Phase-1 spike: token-free line-delimited JSON IPC over a private Unix socket.

Proves:
  - Unix socket in a 0700 dir under $XDG_RUNTIME_DIR (same-UID only)
  - QML Quickshell.Io.Socket can connect, send, and receive
  - snapshot / update / status message shapes round-trip
No credentials ever appear in this protocol.
"""

import asyncio
import json
import os
import sys
import time

RUNTIME_DIR = os.environ.get("XDG_RUNTIME_DIR", "")
if not RUNTIME_DIR:
    print("controller: XDG_RUNTIME_DIR not set", file=sys.stderr)
    sys.exit(1)

SOCKET_DIR = os.path.join(RUNTIME_DIR, "omarchy-ha")
SOCKET_PATH = os.path.join(SOCKET_DIR, sys.argv[1]) if len(sys.argv) > 1 \
    else os.path.join(SOCKET_DIR, "spike.sock")

MAX_LINE_BYTES = 256 * 1024  # protocol guard: reject oversized messages


def make_socket_dir() -> None:
    os.makedirs(SOCKET_DIR, mode=0o700, exist_ok=True)
    os.chmod(SOCKET_DIR, 0o700)
    try:
        os.unlink(SOCKET_PATH)
    except FileNotFoundError:
        pass


async def push_fake_update(writer: asyncio.StreamWriter) -> None:
    """Prove controller-initiated (server push) messages reach QML."""
    await asyncio.sleep(1.5)
    update = {
        "type": "update",
        "seq": 1,
        "changes": [{"entity_id": "light.ceiling", "state": "on"}],
    }
    writer.write((json.dumps(update) + "\n").encode())
    await writer.drain()


async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("socket").getpeername()
    print(f"controller: client connected {peer}", flush=True)

    # Controller-initiated push immediately after connect (snapshot shape).
    snapshot = {
        "type": "snapshot",
        "connected": True,
        "server": {"url": "http://192.0.2.10:8123", "version": "2026.9.1"},
        "areas": [{"id": "living_room", "name": "Living room"}],
        "lights": [
            {"entity_id": "light.ceiling", "name": "Ceiling", "area": "living_room", "state": "on"},
            {"entity_id": "light.lamp", "name": "Floor lamp", "area": "living_room", "state": "off"},
        ],
    }
    writer.write((json.dumps(snapshot) + "\n").encode())
    await writer.drain()

    push_task = asyncio.create_task(push_fake_update(writer))
    try:
        while True:
            line = await reader.readline()
            if not line:
                break
            if len(line) > MAX_LINE_BYTES:
                writer.write(b'{"type":"error","error":"message too large"}\n')
                await writer.drain()
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                writer.write(b'{"type":"error","error":"bad json"}\n')
                await writer.drain()
                continue

            cmd = msg.get("cmd")
            if cmd == "ping":
                reply = {"type": "reply", "id": msg.get("id"), "ok": True, "pong": True,
                         "controller_pid": os.getpid(), "ts": time.time()}
            elif cmd == "light.set":
                # Spike: no real service call; validate the command shape only.
                entity_id = msg.get("entity_id", "")
                ok = entity_id.startswith("light.") and "." in entity_id and len(entity_id) <= 64
                reply = {"type": "reply", "id": msg.get("id"), "ok": ok,
                         "error": None if ok else "invalid entity_id"}
            elif cmd == "secret.check":
                # Spike-only: proves the controller (not QML) owns keyring access.
                import keyring
                from keyring.backends import SecretService as _ss
                keyring.set_keyring(_ss.Keyring())
                keyring.set_password("omarchy-ha-spike", "ipc-check", "present")
                value = keyring.get_password("omarchy-ha-spike", "ipc-check")
                keyring.delete_password("omarchy-ha-spike", "ipc-check")
                reply = {"type": "reply", "id": msg.get("id"), "ok": value == "present"}
            else:
                reply = {"type": "reply", "id": msg.get("id"), "ok": False,
                         "error": f"unknown cmd: {cmd!r}"}
            writer.write((json.dumps(reply) + "\n").encode())
            await writer.drain()
    finally:
        push_task.cancel()
        writer.close()
        print("controller: client disconnected", flush=True)


async def main() -> None:
    make_socket_dir()
    server = await asyncio.start_unix_server(handle, path=SOCKET_PATH)
    os.chmod(SOCKET_PATH, 0o600)
    print(f"controller: listening on {SOCKET_PATH} (pid {os.getpid()})", flush=True)
    async with server:
        await server.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
