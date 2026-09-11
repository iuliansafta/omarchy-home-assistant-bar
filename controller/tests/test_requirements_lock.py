"""The hass-cli dependency lock stays hash-pinned and is used by install.sh.

Network-free: this checks the lock's shape, not that the hashes resolve.
"""

import re
from pathlib import Path

CONTROLLER = Path(__file__).resolve().parent.parent
LOCK = CONTROLLER / "requirements-hass-cli.txt"
REQS_IN = CONTROLLER / "requirements-hass-cli.in"
INSTALL = CONTROLLER / "install.sh"


def _requirements():
    """Return [(name, version, sha256_hash_count), ...] from the lock."""
    entries = []
    current = None
    for line in LOCK.read_text().splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+)==(\S+)", line)
        if match:
            current = [match.group(1), match.group(2), 0]
            entries.append(current)
        elif current is not None and "--hash=sha256:" in line:
            current[2] += 1
    return [(name, version, hashes) for name, version, hashes in entries]


def test_lock_is_not_empty_and_every_pin_has_a_hash():
    entries = _requirements()
    assert entries
    assert [name for name, _, hashes in entries if hashes == 0] == []


def test_lock_contains_the_requested_version():
    requested = {
        line.split("==")[0].strip().lower(): line.split("==")[1].strip()
        for line in REQS_IN.read_text().splitlines()
        if "==" in line and not line.startswith("#")
    }
    locked = {name.lower(): version for name, version, _ in _requirements()}
    assert requested
    for name, version in requested.items():
        assert locked.get(name) == version


def test_installer_uses_the_hash_pinned_lock():
    text = INSTALL.read_text()
    assert "requirements-hass-cli.txt" in text
    assert "--require-hashes" in text
    # no bare, unpinned/partially-pinned install of the CLI
    assert "homeassistant-cli==" not in text
