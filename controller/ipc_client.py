#!/usr/bin/env python3
"""Tiny IPC test client for the controller. Usage:

  ipc_client.py status
  ipc_client.py setup.url http://192.0.2.10:8123
  ipc_client.py setup.browser        (prints authorize URL)
  ipc_client.py setup.cancel
  ipc_client.py signout
  ipc_client.py listen               (dump all controller messages for N s)
  ipc_client.py wait-for CONNECTED   (listen until a status message matches)

Line-delimited JSON over $XDG_RUNTIME_DIR/omarchy-ha/controller.sock.
"""

import json
import os
import socket
import sys
import time

SOCKET_PATH = os.path.join(
    os.environ.get("XDG_RUNTIME_DIR", ""), "omarchy-ha", "controller.sock"
)


def connect():
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(90)
    s.connect(SOCKET_PATH)
    return s


def send(s, msg):
    s.sendall((json.dumps(msg) + "\n").encode())


def recv_line(f):
    line = f.readline()
    if not line:
        return None
    try:
        return json.loads(line)
    except json.JSONDecodeError:
        return {"type": "unparseable", "raw": line[:200]}


def redact(msg):
    """Never print anything that could carry a token."""
    out = json.dumps(msg)
    for key in ("access_token", "refresh_token", "code"):
        if key in out:
            return "<redacted message>"
    return out


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        return 2
    cmd = args[0]
    s = connect()
    f = s.makefile("r")

    # Consume the snapshot push, then proceed.
    first = recv_line(f)

    if cmd == "status":
        send(s, {"cmd": "status", "id": 1})
        while True:
            m = recv_line(f)
            if m is None:
                return 1
            if m.get("type") == "reply":
                print(json.dumps({k: m.get(k) for k in ("ok", "status", "detail")}))
                return 0
    if cmd == "setup.url":
        send(s, {"cmd": "setup.url", "url": args[1], "id": 1})
        while True:
            m = recv_line(f)
            if m is None:
                return 1
            if m.get("type") == "reply":
                print(json.dumps({k: m.get(k) for k in ("ok", "error", "url", "http")}))
                return 0
    if cmd == "setup.browser":
        send(s, {"cmd": "setup.browser", "id": 1})
        while True:
            m = recv_line(f)
            if m is None:
                return 1
            if m.get("type") == "reply":
                print(json.dumps({k: m.get(k) for k in ("ok", "error")}))
                if m.get("authorize_url"):
                    # The URL contains client_id/redirect_uri/state but no secret.
                    print("AUTHORIZE_URL=" + m["authorize_url"])
                return 0
    if cmd in ("setup.cancel", "signout"):
        send(s, {"cmd": cmd, "id": 1})
        while True:
            m = recv_line(f)
            if m is None:
                return 1
            if m.get("type") == "reply":
                print(json.dumps({"ok": m.get("ok")}))
                return 0
    if cmd == "listen":
        duration = float(args[1]) if len(args) > 1 else 10
        if first:
            print("SNAPSHOT", redact(first)[:400])
        s.settimeout(duration)
        end = time.time() + duration
        while time.time() < end:
            try:
                m = recv_line(f)
            except (socket.timeout, TimeoutError):
                break
            if m is None:
                return 1
            if m:
                print("MSG", redact(m)[:400])
        return 0
    if cmd == "wait-for":
        want = args[1].upper() if len(args) > 1 else "CONNECTED"
        if first:
            print("SNAPSHOT status=" + str(first.get("status")))
            if str(first.get("status", "")).upper() == want:
                return 0
        s.settimeout(120)
        while True:
            try:
                m = recv_line(f)
            except (socket.timeout, TimeoutError):
                print("TIMEOUT waiting for", want)
                return 1
            if m is None:
                return 1
            if m.get("type") == "status":
                print("STATUS", m.get("status"), m.get("detail", ""))
                if str(m.get("status", "")).upper() == want:
                    return 0
    print("unknown command", cmd)
    return 2


if __name__ == "__main__":
    sys.exit(main())
