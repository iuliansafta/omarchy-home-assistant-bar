// Pure-JS model helpers for the Home Assistant bar widget. Keep every
// function here deterministic so it can be unit-tested without QML.

// Group lights into popup rows. Area resolution follows the controller:
// explicit entity area first, then "unassigned". Registry ids drive the
// join; names are display-only.
function groupByArea(lights, areas) {
  var byId = {};
  for (var i = 0; i < areas.length; i++) if (areas[i]) byId[areas[i].id] = areas[i];
  var groups = [];
  var unassigned = null;
  for (var j = 0; j < areas.length; j++) {
    if (!areas[j]) continue;
    groups.push({ id: areas[j].id, name: areas[j].name, lights: [], onCount: 0, allOn: false, mixed: false });
  }
  for (var k = 0; k < lights.length; k++) {
    var light = lights[k];
    if (!light || !light.entity_id) continue;
    var group = null;
    for (var g = 0; g < groups.length; g++) if (groups[g].id === light.area) { group = groups[g]; break; }
    if (!group) {
      if (!unassigned) {
        unassigned = { id: "", name: "Unassigned", lights: [], onCount: 0, allOn: false, mixed: false };
        groups.push(unassigned);
      }
      group = unassigned;
    }
    group.lights.push(light);
    if (light.state === "on") group.onCount++;
  }
  for (var m = 0; m < groups.length; m++) {
    var grp = groups[m];
    if (!grp.lights.length) continue;
    grp.allOn = grp.onCount === grp.lights.length;
    grp.mixed = grp.onCount > 0 && !grp.allOn;
  }
  // Drop empty groups; keep a lone Unassigned even when empty is never shown.
  return groups.filter(function(group) { return group.lights.length > 0; });
}

function countOn(lights) {
  var n = 0;
  for (var i = 0; i < lights.length; i++) if (lights[i] && lights[i].state === "on") n++;
  return n;
}

// Distinct bulb / lamp / strip glyphs (Nerd Font). Preference order: the
// Home Assistant icon metadata class from the controller, then the light's
// friendly-name heuristics, then the generic bulb.
function iconFor(light) {
  var cls = light ? light.icon : "";
  if (cls === "strip") return "\uF495";
  if (cls === "lamp") return "\uF65E";
  if (cls === "ceiling") return "\u{F03C6}";
  if (!light) return "\uF0EB";
  var name = (light.name || "").toLowerCase();
  if (name.indexOf("lamp") >= 0) return "\uF65E";
  if (name.indexOf("strip") >= 0 || name.indexOf("led") >= 0) return "\uF495";
  return "\uF0EB";
}

// Validate an entity id before it leaves QML. Mirrors the controller's
// rule (exactly one dot, "light" domain, non-empty object id): the controller
// re-validates, but nothing this obviously malformed should ever be sent.
function validEntityId(id) {
  if (typeof id !== "string" || id.length === 0 || id.length > 64) return false;
  var parts = id.split(".");
  return parts.length === 2 && parts[0] === "light" && parts[1].length > 0;
}

function rgbToHs(rgb) {
  if (!rgb) return [0, 100];
  var r = rgb[0] / 255, g = rgb[1] / 255, b = rgb[2] / 255;
  var max = Math.max(r, g, b), min = Math.min(r, g, b), delta = max - min;
  var hue = 0;
  if (delta) {
    if (max === r) hue = ((g - b) / delta) % 6;
    else if (max === g) hue = (b - r) / delta + 2;
    else hue = (r - g) / delta + 4;
  }
  return [(hue * 60 + 360) % 360, max ? delta / max * 100 : 0];
}

function hsToRgb(hue, saturation) {
  var h = ((hue % 360) + 360) % 360 / 60;
  var s = Math.max(0, Math.min(100, saturation)) / 100;
  var x = s * (1 - Math.abs(h % 2 - 1));
  var rgb = h < 1 ? [s, x, 0] : h < 2 ? [x, s, 0] : h < 3 ? [0, s, x]
    : h < 4 ? [0, x, s] : h < 5 ? [x, 0, s] : [s, 0, x];
  return rgb.map(function(channel) { return Math.round((channel + 1 - s) * 255); });
}

// Device conversions and rounding need small tolerances. Acceptance of the
// service call alone never confirms a pending target.
function matchesTarget(light, target) {
  if (!light || light.state !== (target.on ? "on" : "off")) return false;
  if (!target.on) return true;
  if (target.brightness !== undefined
      && (light.brightness === null || light.brightness === undefined
        || Math.abs(light.brightness - target.brightness) > 3)) return false;
  if (target.color_temp_kelvin !== undefined
      && (light.color_mode !== "color_temp" || !light.color_temp_kelvin
        || Math.abs(light.color_temp_kelvin - target.color_temp_kelvin) > 100)) return false;
  if (target.rgb_color) {
    if (!light.rgb_color || ["hs", "xy", "rgb", "rgbw", "rgbww"].indexOf(light.color_mode) < 0) return false;
    for (var i = 0; i < 3; i++)
      if (Math.abs(light.rgb_color[i] - target.rgb_color[i]) > 16) return false;
  }
  return true;
}
