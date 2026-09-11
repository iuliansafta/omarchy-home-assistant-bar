"""Pure capability projection and light.set validation, with no HA session."""

import copy
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from topology import light_attributes, resolve_topology, validate_light_set


ATTRS = {
    "supported_color_modes": ["rgb", "color_temp"],
    "min_color_temp_kelvin": 2000,
    "max_color_temp_kelvin": 6500,
}
LIGHT = {"entity_id": "light.desk", "state": "on", **light_attributes(ATTRS)}


@pytest.mark.parametrize("mode,brightness,color,temperature", [
    ("onoff", False, False, False),
    ("brightness", True, False, False),
    ("white", True, False, False),
    ("color_temp", True, False, True),
    ("hs", True, True, False),
    ("xy", True, True, False),
    ("rgb", True, True, False),
    ("rgbw", True, True, False),
    ("rgbww", True, True, False),
    ("unknown", False, False, False),
])
def test_capabilities_while_off(mode, brightness, color, temperature):
    _, lights, _ = resolve_topology(
        {"light.desk": "off"},
        {"light.desk": {**ATTRS, "supported_color_modes": [mode]}}, [], [], [],
    )
    light = lights[0]
    assert light["supports_brightness"] is brightness
    assert light["supports_color"] is color
    assert light["supports_color_temp"] is temperature
    assert light["brightness"] is None
    assert light["rgb_color"] is None
    assert light["color_temp_kelvin"] is None


@pytest.mark.parametrize("attrs", [
    {}, {"supported_color_modes": None}, {"supported_color_modes": "rgb"},
    {"supported_color_modes": [None, {}, 1]},
    {"brightness": True, "rgb_color": [0, 0, False], "color_temp_kelvin": -1},
    {"brightness": float("nan"), "rgb_color": [256, 0, 0]},
])
def test_missing_or_malformed_attributes(attrs):
    projected = light_attributes(attrs)
    assert not projected["supports_color"]
    assert projected["brightness"] is None
    assert projected["rgb_color"] is None
    assert projected["color_temp_kelvin"] is None


@pytest.mark.parametrize("minimum,maximum", [(None, None), (6500, 2000), (0, 6500), (True, 6500)])
def test_temperature_requires_device_bounds(minimum, maximum):
    data = light_attributes({**ATTRS, "min_color_temp_kelvin": minimum, "max_color_temp_kelvin": maximum})
    assert not data["supports_color_temp"]
    assert data["min_color_temp_kelvin"] is None
    assert data["max_color_temp_kelvin"] is None


@pytest.mark.parametrize("options,service,data", [
    ({}, "turn_on", {}),
    ({"on": False}, "turn_off", {}),
    ({"brightness": 0}, "turn_off", {}),
    ({"brightness": 1}, "turn_on", {"brightness": 1}),
    ({"brightness": 255}, "turn_on", {"brightness": 255}),
    ({"rgb_color": [255, 100, 0]}, "turn_on", {"rgb_color": [255, 100, 0]}),
    ({"color_temp_kelvin": 2000}, "turn_on", {"color_temp_kelvin": 2000}),
    ({"color_temp_kelvin": 6500, "brightness": 128}, "turn_on", {"color_temp_kelvin": 6500, "brightness": 128}),
])
def test_explicit_service_payload(options, service, data):
    msg = {"cmd": "light.set", "id": 1, "entity_id": "light.desk", "on": True, **options}
    original = copy.deepcopy(msg)
    assert validate_light_set(msg, [LIGHT]) == (service, data)
    assert msg == original


@pytest.mark.parametrize("options", [
    {"on": None}, {"on": 1}, {"on": "false"},
    {"brightness": -1}, {"brightness": 256}, {"brightness": 1.5},
    {"brightness": True}, {"brightness": None}, {"brightness": float("inf")},
    {"rgb_color": [0, 1]}, {"rgb_color": [0, 1, 256]}, {"rgb_color": [0, 1, -1]},
    {"rgb_color": [0, True, 1]}, {"rgb_color": [0, 1, 1.0]}, {"rgb_color": "red"},
    {"color_temp_kelvin": 1999}, {"color_temp_kelvin": 6501},
    {"color_temp_kelvin": True}, {"color_temp_kelvin": 3000.0},
    {"color_temp_kelvin": 3000, "rgb_color": [255, 0, 0]},
    {"on": False, "brightness": 100}, {"brightness": 0, "rgb_color": [255, 0, 0]},
    {"transition": 1}, {"entity_id": "switch.desk"}, {"entity_id": None},
    {"entity_id": "light.desk.bad"}, {"entity_id": "light.hidden"},
])
def test_invalid_requests_rejected(options):
    with pytest.raises(ValueError):
        validate_light_set({"entity_id": "light.desk", "on": True, **options}, [LIGHT])


@pytest.mark.parametrize("options", [{"brightness": 100}, {"rgb_color": [255, 0, 0]}, {"color_temp_kelvin": 3000}])
def test_unsupported_controls_rejected(options):
    basic = {"entity_id": "light.desk", "state": "off", **light_attributes({})}
    with pytest.raises(ValueError, match="does not support"):
        validate_light_set({"entity_id": "light.desk", "on": True, **options}, [basic])


def test_unavailable_and_missing_power_rejected():
    with pytest.raises(ValueError, match="unavailable"):
        validate_light_set({"entity_id": "light.desk", "on": True}, [{**LIGHT, "state": "unavailable"}])
    with pytest.raises(ValueError, match="boolean"):
        validate_light_set({"entity_id": "light.desk"}, [LIGHT])


def test_projection_does_not_expose_arbitrary_attributes_or_alias_rgb():
    attrs = {**ATTRS, "rgb_color": [255, 0, 0], "brightness": 128, "private_data": "hidden"}
    projected = light_attributes(attrs)
    assert "private_data" not in projected
    assert projected["brightness"] == 128
    projected["rgb_color"][0] = 0
    assert attrs["rgb_color"] == [255, 0, 0]
