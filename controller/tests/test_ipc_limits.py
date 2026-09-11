"""IPC framing limits: oversized lines are rejected, never crash the handler.

A real Unix-domain socket is used so asyncio's stream-limit behaviour is
exercised. Each test binds its own socket under tmp_path and never touches the
live runtime directory.
"""

import asyncio
import sys
from pathlib import Path

import pytest

pytest.importorskip("aiohttp")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from omarchy_ha_controller import MAX_LINE_BYTES, STREAM_LIMIT, Controller  # noqa: E402


def test_stream_limit_allows_the_declared_message_size():
    # The explicit MAX_LINE_BYTES check is only reachable if the reader can
    # buffer a whole over-limit line.
    assert STREAM_LIMIT > MAX_LINE_BYTES


def test_oversized_line_is_rejected_and_connection_survives(tmp_path):
    controller = Controller()
    path = tmp_path / "ipc.sock"

    async def scenario():
        server = await asyncio.start_unix_server(
            controller.client_connected, path=str(path), limit=STREAM_LIMIT
        )
        reader, writer = await asyncio.open_unix_connection(str(path))
        await reader.readline()  # snapshot push
        writer.write(b"x" * (MAX_LINE_BYTES + 1) + b"\n")
        writer.write(b'{"cmd": "status", "id": 7}\n')
        await writer.drain()
        first = await asyncio.wait_for(reader.readline(), timeout=5)
        second = await asyncio.wait_for(reader.readline(), timeout=5)
        writer.close()
        server.close()
        await server.wait_closed()
        return first, second

    first, second = asyncio.run(scenario())
    assert b'"message too large"' in first
    assert b'"id": 7' in second and b'"ok": true' in second


def test_line_beyond_stream_limit_is_rejected_and_closed(tmp_path):
    controller = Controller()
    path = tmp_path / "ipc.sock"

    async def scenario():
        server = await asyncio.start_unix_server(
            controller.client_connected, path=str(path), limit=STREAM_LIMIT
        )
        reader, writer = await asyncio.open_unix_connection(str(path))
        await reader.readline()  # snapshot push
        writer.write(b"x" * (STREAM_LIMIT + 1024) + b"\n")
        await writer.drain()
        lines = []
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=5)
            if not line:
                break
            lines.append(line)
        writer.close()
        server.close()
        await server.wait_closed()
        return lines

    lines = asyncio.run(scenario())
    assert any(b'"message too large"' in line for line in lines)


@pytest.mark.parametrize("peer", ["same", "other", "malformed", "unavailable"])
def test_peer_credentials_fail_closed(peer):
    import os
    import struct
    from unittest.mock import AsyncMock, MagicMock

    async def scenario():
        c = Controller()
        c.send_client = AsyncMock()
        writer = MagicMock()
        sock = writer.get_extra_info.return_value
        if peer == "unavailable":
            sock.getsockopt.side_effect = OSError
        else:
            sock.getsockopt.return_value = (b"bad" if peer == "malformed" else
                struct.pack("3i", 1, os.getuid() + (peer == "other"), 1))
        reader = AsyncMock()
        reader.readline.return_value = b""
        await c.client_connected(reader, writer)
        assert c.send_client.called == (peer == "same")
        assert sock.getsockopt.call_args.args[2] == struct.calcsize("3i")
        assert writer.close.called
    asyncio.run(scenario())
