"""Controller caches must be per-instance, not shared class attributes.

A shared mutable class attribute leaked state between Controller instances
(tests, or any future second controller in one process).
"""

import sys
from pathlib import Path

import pytest

pytest.importorskip("aiohttp")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from omarchy_ha_controller import Controller  # noqa: E402


def test_instances_do_not_share_state_caches():
    first, second = Controller(), Controller()

    first._states["light.x"] = "on"
    first._state_attrs["light.x"] = {}
    first._registry["areas"].append({"area_id": "x"})
    first._sub_ids.add(1)
    first._registry_limited = True
    first._live_ws = object()

    assert second._states == {}
    assert second._state_attrs == {}
    assert second._registry == {"areas": [], "devices": [], "entities": []}
    assert second._sub_ids == set()
    assert second._registry_limited is False
    assert second._live_ws is None


def test_cache_objects_are_not_shared():
    first, second = Controller(), Controller()
    assert first._states is not second._states
    assert first._state_attrs is not second._state_attrs
    assert first._registry is not second._registry
    assert first._sub_ids is not second._sub_ids
