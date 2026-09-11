"""Diagnostic client handles text streams and disconnected controllers offline."""
import io
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import ipc_client


def test_malformed_json_from_text_makefile():
    assert ipc_client.recv_line(io.StringIO("not json\n")) == {
        "type": "unparseable", "raw": "not json\n"}


@pytest.mark.parametrize("args", [
    ["status"], ["setup.url", "https://ha.example"], ["setup.browser"],
    ["setup.cancel"], ["signout"], ["listen"], ["wait-for"],
])
def test_eof_terminates_instead_of_spinning(monkeypatch, args):
    sock = MagicMock()
    sock.makefile.return_value = io.StringIO('{"type":"snapshot"}\n')
    monkeypatch.setattr(ipc_client, "connect", lambda: sock)
    monkeypatch.setattr(sys, "argv", ["ipc_client.py"] + args)
    assert ipc_client.main() == 1
