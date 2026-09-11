"""Base-URL validation rejects bad input with a message and never raises."""

import sys
from pathlib import Path

import pytest

pytest.importorskip("aiohttp")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from omarchy_ha_controller import validate_base_url  # noqa: E402


def test_accepts_and_normalizes_simple_urls():
    assert validate_base_url("http://h:8123") == ("http://h:8123", "")
    assert validate_base_url("h:8123") == ("http://h:8123", "")
    assert validate_base_url("https://ha.example/path") == ("https://ha.example", "")
    assert validate_base_url("http://h:8123/") == ("http://h:8123", "")
    # An empty port is dropped, not an error.
    assert validate_base_url("http://h:") == ("http://h", "")


def test_ipv6_literal_keeps_brackets():
    assert validate_base_url("http://[::1]:8123") == ("http://[::1]:8123", "")
    assert validate_base_url("http://[fe80::1]") == ("http://[fe80::1]", "")


def test_invalid_ipv6_is_rejected_not_raised():
    url, err = validate_base_url("http://[::1")
    assert url is None and err


@pytest.mark.parametrize("bad", ["http://h:abc", "http://h:99999"])
def test_invalid_ports_are_rejected_not_raised(bad):
    url, err = validate_base_url(bad)
    assert url is None and err


@pytest.mark.parametrize("bad", [
    "ftp://h",
    "http://user:pw@h",
    "http://h?x=1",
    "http://h#frag",
    "",
])
def test_invalid_urls_are_rejected(bad):
    url, err = validate_base_url(bad)
    assert url is None and err
