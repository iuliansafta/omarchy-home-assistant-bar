"""Pure topology resolution for the Home Assistant controller.

Joins live states with the area/device/entity registries:

  1. Entity's explicit area wins.
  2. Otherwise its device's area.
  3. Otherwise the light is "unassigned" (area "").

Filters (plan §4):
  - only `light.*` entities that exist in states
  - registry-disabled (`disabled_by`) and hidden (`hidden_by`) entries drop
  - aggregate light groups drop (user decision: exclude groups so a group and
    its members don't render as duplicate controls)

All functions are pure and unit-tested with fixtures; discovery performs no
writes and no network I/O.
"""

from __future__ import annotations

MAX_ENTITY_ID_LEN = 64


def _valid_light(entity_id: str) -> bool:
    if not isinstance(entity_id, str) or len(entity_id) > MAX_ENTITY_ID_LEN:
        return False
    parts = entity_id.split(".")
    return len(parts) == 2 and parts[0] == "light" and bool(parts[1])


def _state_of(states: dict, entity_id: str) -> str:
    st = states.get(entity_id)
    if st is None:
        return "unavailable"
    return st if st in ("on", "off") else "unavailable"


def _friendly_name(entity_id: str, attributes: dict) -> str:
    name = attributes.get("friendly_name")
    if isinstance(name, str) and name.strip():
        return name.strip()
    return entity_id.split(".", 1)[1].replace("_", " ").title()


def is_aggregate_group(entity_entry: dict, state_attributes: dict | None) -> bool:
    """Detect light-group aggregates so they can be excluded from the picker."""
    platform = entity_entry.get("platform")
    if platform == "group":
        return True
    # Light groups created through the UI expose their members.
    if isinstance(state_attributes, dict):
        members = state_attributes.get("entity_id")
        if isinstance(members, list) and members:
            return True
    return False


def light_attributes(attributes: dict) -> dict:
    """Project only typed light controls; capabilities do not depend on power state."""
    modes = attributes.get("supported_color_modes")
    modes = {mode for mode in modes if isinstance(mode, str)} if isinstance(modes, (list, tuple)) else set()
    color_modes = {"hs", "xy", "rgb", "rgbw", "rgbww"}
    minimum = attributes.get("min_color_temp_kelvin")
    maximum = attributes.get("max_color_temp_kelvin")
    valid_bounds = type(minimum) is int and type(maximum) is int and 0 < minimum <= maximum
    brightness = attributes.get("brightness")
    rgb = attributes.get("rgb_color")
    temperature = attributes.get("color_temp_kelvin")
    mode = attributes.get("color_mode")
    return {
        "supports_brightness": bool(modes & (color_modes | {"brightness", "color_temp", "white"})),
        "supports_color": bool(modes & color_modes),
        "supports_color_temp": "color_temp" in modes and valid_bounds,
        "brightness": brightness if type(brightness) is int and 0 <= brightness <= 255 else None,
        "rgb_color": list(rgb) if isinstance(rgb, (list, tuple)) and len(rgb) == 3
        and all(type(channel) is int and 0 <= channel <= 255 for channel in rgb) else None,
        "color_mode": mode if isinstance(mode, str) else None,
        "color_temp_kelvin": temperature if type(temperature) is int and temperature > 0 else None,
        "min_color_temp_kelvin": minimum if valid_bounds else None,
        "max_color_temp_kelvin": maximum if valid_bounds else None,
    }


def validate_light_set(msg: dict, lights: list) -> tuple[str, dict]:
    """Return an explicit service and allowlisted data, or raise ValueError."""
    entity_id = msg.get("entity_id")
    if not _valid_light(entity_id):
        raise ValueError("invalid entity_id")
    if type(msg.get("on")) is not bool:
        raise ValueError("on must be a boolean")
    options = {key: msg[key] for key in ("brightness", "rgb_color", "color_temp_kelvin") if key in msg}
    if msg.keys() - {"cmd", "id", "entity_id", "on"} - options.keys():
        raise ValueError("unsupported light.set field")
    if options and not msg["on"]:
        raise ValueError("light options require on true")
    if "rgb_color" in options and "color_temp_kelvin" in options:
        raise ValueError("rgb_color and color_temp_kelvin are mutually exclusive")
    light = next((light for light in lights if light["entity_id"] == entity_id), None)
    if light is None:
        raise ValueError("light is not selected or visible")
    if light.get("state") not in ("on", "off"):
        raise ValueError("light is unavailable")
    if "brightness" in options:
        value = options["brightness"]
        if type(value) is not int or not 0 <= value <= 255:
            raise ValueError("brightness must be an integer from 0 to 255")
        if not light.get("supports_brightness"):
            raise ValueError("light does not support brightness")
        if value == 0 and len(options) > 1:
            raise ValueError("brightness 0 cannot be combined with color")
    if "rgb_color" in options:
        value = options["rgb_color"]
        if not isinstance(value, list) or len(value) != 3 or not all(
            type(channel) is int and 0 <= channel <= 255 for channel in value
        ):
            raise ValueError("rgb_color must contain three integers from 0 to 255")
        if not light.get("supports_color"):
            raise ValueError("light does not support color")
        options["rgb_color"] = list(value)
    if "color_temp_kelvin" in options:
        value = options["color_temp_kelvin"]
        if type(value) is not int:
            raise ValueError("color_temp_kelvin must be an integer")
        if not light.get("supports_color_temp"):
            raise ValueError("light does not support color temperature")
        if not light["min_color_temp_kelvin"] <= value <= light["max_color_temp_kelvin"]:
            raise ValueError("color_temp_kelvin is outside the device range")
    if not msg["on"] or options.get("brightness") == 0:
        return "turn_off", {}
    return "turn_on", options


def resolve_topology(
    states: dict[str, str],
    attributes: dict[str, dict],
    areas: list,
    devices: list,
    entity_entries: list,
    selection: dict | None = None,
) -> tuple[list, list, list]:
    """Join registries with states.

    selection (user picker, both keys optional):
      {"areas": [area_id...], "entities": [entity_id...]}
    Empty/missing selection = show everything. A non-empty selection keeps a
    light when its area is selected OR the entity itself is selected (plan §3:
    user picks areas or individual lights; stable ids only).

    Returns (areas_out, lights_out, dropped) where dropped lists reasons per
    excluded entity id (for diagnostics only).
    """
    area_names: dict[str, str] = {}
    for area in areas or []:
        if isinstance(area, dict) and area.get("area_id"):
            area_names[str(area["area_id"])] = str(area.get("name") or area["area_id"])

    device_area: dict[str, str] = {}
    for dev in devices or []:
        if isinstance(dev, dict) and dev.get("id"):
            area = dev.get("area_id")
            if isinstance(area, str) and area:
                device_area[str(dev["id"])] = area

    entity_meta: dict[str, dict] = {}
    for entry in entity_entries or []:
        if isinstance(entry, dict) and isinstance(entry.get("entity_id"), str):
            entity_meta[entry["entity_id"]] = entry

    areas_out = [
        {"id": area_id, "name": name} for area_id, name in sorted(area_names.items(), key=lambda kv: kv[1].lower())
    ]

    sel_areas: set[str] = set()
    sel_entities: set[str] = set()
    if isinstance(selection, dict):
        for a in selection.get("areas") or []:
            if isinstance(a, str) and a in area_names:
                sel_areas.add(a)
        for e in selection.get("entities") or []:
            if _valid_light(e):
                sel_entities.add(e)
    selection_active = bool(sel_areas or sel_entities)

    def _selected(light_area: str, light_entity: str) -> bool:
        if not selection_active:
            return True
        return light_area in sel_areas or light_entity in sel_entities

    lights_out: list = []
    dropped: list = []
    for entity_id in sorted(states):
        if not _valid_light(entity_id):
            continue
        entry = entity_meta.get(entity_id)
        if entry is not None:
            if entry.get("disabled_by"):
                dropped.append({"entity_id": entity_id, "reason": "disabled"})
                continue
            if entry.get("hidden_by"):
                dropped.append({"entity_id": entity_id, "reason": "hidden"})
                continue
            # Registry entries whose entity no longer exists are ignored
            # naturally: we drive the loop from live states only.
            area = entry.get("area_id") or device_area.get(entry.get("device_id") or "")
        else:
            area = None
        if is_aggregate_group(entry or {}, attributes.get(entity_id)):
            dropped.append({"entity_id": entity_id, "reason": "aggregate_group"})
            continue
        if not isinstance(area, str) or not area or area not in area_names:
            # Unknown/deleted area ids fall back to unassigned (plan §4).
            area = ""
        if not _selected(area, entity_id):
            dropped.append({"entity_id": entity_id, "reason": "not_selected"})
            continue
        lights_out.append({
            "entity_id": entity_id,
            "name": _friendly_name(entity_id, attributes.get(entity_id) or {}),
            "area": area,
            "state": _state_of(states, entity_id),
            "icon": light_icon(attributes.get(entity_id) or {}),
            **light_attributes(attributes.get(entity_id) or {}),
        })

    # Drop areas that ended up with no visible lights (they'd be empty headers).
    used_areas = {light["area"] for light in lights_out if light["area"]}
    areas_out = [a for a in areas_out if a["id"] in used_areas]
    return areas_out, lights_out, dropped


def light_icon(attributes: dict) -> str:
    """HA icon attribute (mdi:...) reduced to a coarse shape class.

    The widget maps the class to a glyph; unknown values fall back to "bulb".
    """
    icon = ""
    if isinstance(attributes, dict):
        icon = str(attributes.get("icon") or "")
    text = icon.lower()
    if "strip" in text:
        return "strip"
    if "lamp" in text or "sconce" in text or "wall-wash" in text:
        return "lamp"
    if "ceiling" in text or "chandelier" in text or "pendant" in text or "recessed" in text:
        return "ceiling"
    return "bulb"


def area_targets(lights: list, area_id: str) -> list[str]:
    """Explicit target list for an area all-on/all-off action.

    Only the visible, currently controllable light ids in the area — never
    every device in the area (plan §4).
    """
    return [
        light["entity_id"]
        for light in lights
        if light.get("area") == area_id and light.get("state") != "unavailable"
    ]
