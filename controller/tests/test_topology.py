"""Fixture tests for topology resolution (plan phase-3 gate).

Covers: entity-area-over-device-area precedence, unassigned lights,
unavailable entities, disabled/hidden drops, aggregate-group exclusion,
deleted-area fallback, area targeting, and no writes (pure functions).
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from topology import area_targets, is_aggregate_group, light_icon, resolve_topology  # noqa: E402


AREAS = [
    {"area_id": "living_room", "name": "Living room"},
    {"area_id": "kitchen", "name": "Kitchen"},
    # NOTE: ghost_area is deliberately absent — an entity's registry entry
    # still references it, but the area itself was deleted.
]

DEVICES = [
    {"id": "dev_bridge", "area_id": "living_room"},
    {"id": "dev_orphan", "area_id": None},
]

ENTITIES = [
    # Entity area wins over device area.
    {"entity_id": "light.explicit", "area_id": "kitchen", "device_id": "dev_bridge", "platform": "hue"},
    # No entity area: device area used.
    {"entity_id": "light.via_device", "area_id": None, "device_id": "dev_bridge", "platform": "hue"},
    # Device without area and no entity area -> unassigned.
    {"entity_id": "light.orphan", "area_id": None, "device_id": "dev_orphan", "platform": "hue"},
    # No registry entry at all -> unassigned.
    # (light.ghost has no entry; added to states below)
    # Disabled and hidden entries drop.
    {"entity_id": "light.disabled", "area_id": "kitchen", "disabled_by": "user"},
    {"entity_id": "light.hidden", "area_id": "kitchen", "hidden_by": "integration"},
    # Aggregate light group drops (user decision).
    {"entity_id": "light.group_all", "area_id": "living_room", "platform": "group"},
    # Registry entry pointing at a deleted area falls back to unassigned.
    {"entity_id": "light.ghost_area", "area_id": "ghost_area", "platform": "hue"},
    # Registry entry for a deleted entity must not resurrect anything.
    {"entity_id": "light.deleted", "area_id": "kitchen"},
]

STATES = {
    "light.explicit": "on",
    "light.via_device": "off",
    "light.orphan": "unavailable",
    "light.ghost": "off",          # no registry entry
    "light.disabled": "off",
    "light.hidden": "off",
    "light.group_all": "on",
    "light.ghost_area": "off",
    # light.deleted intentionally absent from states
    "switch.plug": "on",           # wrong domain, ignored
}

ATTRS = {
    "light.explicit": {"friendly_name": "Explicit"},
    "light.via_device": {"friendly_name": "Via device"},
    "light.orphan": {},
    "light.ghost": {"friendly_name": "Ghost"},
    "light.group_all": {"friendly_name": "All lights", "entity_id": ["light.explicit", "light.via_device"]},
    "light.ghost_area": {"friendly_name": "Ghost area"},
    "switch.plug": {"friendly_name": "Plug"},
}


def find(lights, entity_id):
    return next((l for l in lights if l["entity_id"] == entity_id), None)


def test_precedence_entity_over_device():
    areas, lights, _ = resolve_topology(STATES, ATTRS, AREAS, DEVICES, ENTITIES)
    assert find(lights, "light.explicit")["area"] == "kitchen"
    assert find(lights, "light.via_device")["area"] == "living_room"


def test_unassigned():
    areas, lights, _ = resolve_topology(STATES, ATTRS, AREAS, DEVICES, ENTITIES)
    assert find(lights, "light.orphan")["area"] == ""
    assert find(lights, "light.ghost")["area"] == ""
    assert find(lights, "light.ghost_area")["area"] == ""  # deleted area -> unassigned


def test_unavailable_state_preserved():
    _, lights, _ = resolve_topology(STATES, ATTRS, AREAS, DEVICES, ENTITIES)
    assert find(lights, "light.orphan")["state"] == "unavailable"
    assert find(lights, "light.ghost")["state"] == "off"


def test_disabled_hidden_group_dropped():
    _, lights, dropped = resolve_topology(STATES, ATTRS, AREAS, DEVICES, ENTITIES)
    ids = {l["entity_id"] for l in lights}
    assert "light.disabled" not in ids
    assert "light.hidden" not in ids
    assert "light.group_all" not in ids
    reasons = {d["entity_id"]: d["reason"] for d in dropped}
    assert reasons["light.disabled"] == "disabled"
    assert reasons["light.hidden"] == "hidden"
    assert reasons["light.group_all"] == "aggregate_group"


def test_deleted_entity_not_resurrected():
    _, lights, _ = resolve_topology(STATES, ATTRS, AREAS, DEVICES, ENTITIES)
    assert find(lights, "light.deleted") is None


def test_wrong_domain_ignored():
    _, lights, _ = resolve_topology(STATES, ATTRS, AREAS, DEVICES, ENTITIES)
    assert find(lights, "switch.plug") is None


def test_empty_area_groups_dropped_from_areas():
    areas, _, _ = resolve_topology(STATES, ATTRS, AREAS, DEVICES, ENTITIES)
    # No light resolves to the kitchen by visible state? explicit -> kitchen. So kitchen stays.
    ids = {a["id"] for a in areas}
    assert "kitchen" in ids and "living_room" in ids
    # ghost_area has a visible light but resolves to unassigned, so the area
    # header must disappear.
    assert "ghost_area" not in ids


def test_area_targets_excludes_unavailable_and_other_areas():
    _, lights, _ = resolve_topology(STATES, ATTRS, AREAS, DEVICES, ENTITIES)
    targets = area_targets(lights, "kitchen")
    assert targets == ["light.explicit"]  # on/off only, not the unavailable orphan
    assert area_targets(lights, "") == [
        "light.ghost", "light.ghost_area",
    ]  # unassigned bucket: both controllable (off)


def test_group_detection_heuristics():
    assert is_aggregate_group({"platform": "group"}, None)
    assert is_aggregate_group({"platform": "hue"}, {"entity_id": ["light.a"]})
    assert not is_aggregate_group({"platform": "hue"}, {"brightness": 100})
    assert not is_aggregate_group({}, None)


def test_no_writes_pure():
    # Calling twice yields identical results: discovery must not mutate inputs.
    import copy
    states = copy.deepcopy(STATES)
    areas, lights, dropped = resolve_topology(states, ATTRS, AREAS, DEVICES, ENTITIES)
    assert resolve_topology(states, ATTRS, AREAS, DEVICES, ENTITIES) == (areas, lights, dropped)
    assert states == STATES


def test_selection_filters_by_area_or_entity():
    _, lights, dropped = resolve_topology(STATES, ATTRS, AREAS, DEVICES, ENTITIES,
                                          selection={"areas": ["living_room"], "entities": ["light.ghost"]})
    ids = {l["entity_id"] for l in lights}
    assert "light.via_device" in ids          # device area living_room
    assert "light.ghost" in ids               # explicitly selected
    assert "light.explicit" not in ids        # kitchen, not selected
    reasons = {d["entity_id"]: d["reason"] for d in dropped}
    assert reasons.get("light.explicit") == "not_selected"


def test_selection_empty_shows_everything():
    full, _, _ = resolve_topology(STATES, ATTRS, AREAS, DEVICES, ENTITIES)
    again, _, _ = resolve_topology(STATES, ATTRS, AREAS, DEVICES, ENTITIES, selection={})
    assert full == again


def test_selection_unknown_ids_ignored():
    _, lights, _ = resolve_topology(STATES, ATTRS, AREAS, DEVICES, ENTITIES,
                                    selection={"areas": ["nope"], "entities": ["light.nothere"]})
    assert lights == []  # selection active, nothing matched


def test_icon_classification():
    assert light_icon({"icon": "mdi:led-strip-variant"}) == "strip"
    assert light_icon({"icon": "mdi:wall-sconce-flat"}) == "lamp"
    assert light_icon({"icon": "mdi:ceiling-light"}) == "ceiling"
    assert light_icon({"icon": "mdi:lightbulb"}) == "bulb"
    assert light_icon({}) == "bulb"
