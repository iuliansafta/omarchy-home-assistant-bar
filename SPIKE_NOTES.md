# Phase 1 — Compatibility spike: findings and verified contracts

Date: 2026-09-09. All checks ran on the live machine against the real LAN
instance unless marked otherwise. Nothing was written outside user-owned
paths except the disposable keyring secret (created and deleted).

Private server addresses below have been replaced with documentation-only examples.

## Verified environment

| Item | Result |
|---|---|
| Home Assistant | `http://192.0.2.10:8123`, version **2026.9.1**, instance name "Home" (found via `_home-assistant._tcp` mDNS; internal_url matches) |
| Reachability | Frontend 200; `/api/` 401 unauthenticated (expected); `/auth/providers` answers |
| Auth endpoints | `/auth/authorize` GET renders login (200); `/auth/token` POST validates grants (400 on garbage) |
| Omarchy shell | omarchy-shell (Quickshell) running; plugin contract confirmed from source |
| Secret Service | GNOME Keyring owns `org.freedesktop.secrets`; **default collection unlocked, no prompt** |
| Python | System `/usr/bin/python3` is 3.14.7 (mise shadows PATH — controller must use `/usr/bin/python3` or a venv); `python-aiohttp` 3.13.5 and `python-keyring` 25.7.0 installed system-wide |
| hass-cli | PyPI package is `homeassistant-cli` (not `hass-cli`); latest **1.0.0**, requires Python ≥3.13 |

## Spike 1 — omarchy-shell plugin contract (verified from installed source)

- Third-party plugins install under `~/.config/omarchy/plugins/<id>/`;
  symlinked dirs work (`iulian.mcp-bar`, `iuliansafta.apple-music` are symlinks).
- Manifest: `schemaVersion: 1`, required `id/name/version/kinds/entryPoints`.
  Our plugin uses `kinds: ["service", "bar-widget"]`.
- **Service** entry point is mounted once per shell when the plugin id
  appears in shell.json (third-party: enabled ⇔ present in shell.json).
  Instance receives `shell` (capability-scoped facade) etc.
- **Bar widget** is instantiated **once per monitor**; receives `bar`
  facade (`PluginBarApi`), `moduleName`, `settings` (inline shell.json entry).
- Widget → own service: `bar?.shell?.serviceFor("iulian.home-assistant")`.
- Popup: `Ui.KeyboardPanel` (layer-shell, anchored to button, keyboard
  nav via `Ui.PanelKeyCatcher`), button via `Ui.WidgetButton`. Theme via
  `qs.Commons` (`Style`, `Color`), icons via Nerd Font glyphs.
- IPC: `IpcHandler` must live on the **service** (single instance); widget
  instances are per-monitor and would collide. Registered target verified
  with `quickshell ipc --pid <pid> show`.
- Base classes `Ui.Panel` / `Ui.BarWidget` provide open/close/toggle,
  `setting(key, fallback)`, `broadcast()`.

**Dev-loop gotchas (documented, cost real debugging time):**
- The plugin dir watcher uses `inotifywait` on the plugins dir; edits inside
  a **symlinked** plugin do not fire events. Force reload by recreating the
  symlink, or restart the shell. `rescanPlugins` alone does NOT clear the
  QML component cache — a full reload (`reloadPlugins`) is required.
- mise Python shadows `/usr/bin/python3`; venvs are the reliable path.

## Spike 2 — QML ⇄ controller IPC (PROVEN end-to-end)

- `Quickshell.Io.Socket` (Unix client) + `SplitParser` + `write(string)`:
  connected to the Python controller, received controller-initiated pushes,
  sent commands, received replies. No HTTP listener involved.
- Protocol: line-delimited JSON. Message kinds: `snapshot` (push on
  connect), `update` (push), `reply {id, ok, error}` (per request id),
  commands `{cmd, id, ...}`.
- Guards proven: oversized message rejection, bad-JSON rejection, unknown
  cmd rejection, invalid `entity_id` rejection (server-side check).
- Keyring access proven **from the controller process only**
  (`secret.check` cmd → ok=true); QML never touches secrets.
- Socket security: dir `$XDG_RUNTIME_DIR/omarchy-ha/` mode 0700, socket
  mode 0600 — same-UID only.

## Spike 3 — Secret Service round-trip (PASSED)

- `keyring.backends.SecretService.Keyring` (explicit, priority 5):
  set → get → delete of a disposable secret, **no unlock prompt**
  (default collection unlocked at login).
- Important: default `keyring.get_keyring()` returns `ChainerBackend`,
  which tries **KWallet first**; KWallet errors *propagate* instead of
  falling through. The controller must pin the SecretService backend
  explicitly (plan requirement "explicitly verified backend" — now
  mandatory, not stylistic).
- Still open (interactive, phase 6): persistence across reboot + locked
  keyring UX. Service availability ≠ persistence proof.

## Spike 4 — native bar widget + popup (WORKING, mock data)

Installed as `~/.config/omarchy/plugins/iulian.home-assistant -> <repo>/plugin`
and enabled in `~/.config/omarchy/shell.json` (right section, entry
`{"id": "iulian.home-assistant"}`).

Verified on the live bar (screenshots during spike):
- Bar pill: home glyph, accent when lights on, count `1`; dimmed offline.
- Popup: title + `2026.9.1 · connected`; area group "Living room 1/2 on"
  with **All on / All off** pills; light rows (bulb glyph, name, switch);
  unavailable lights render disabled; offline disables controls; footer
  `Open HA` opens the server URL.
- IPC: `omarchy-shell iulian.home-assistant status` →
  `{"status":"connected","serverVersion":"2026.9.1","lightsOn":1,"lights":2,"areas":1}`.
- Service reconnect: backoff timer wired; controller restart → automatic
  reconnect observed during spike restarts.

Files: `plugin/{manifest.json,Service.qml,BarWidget.qml,Panel.qml,lib/HaModel.js}`,
spike harness `spike/{controller_spike.py,spike-shell/shell.qml}`.

## Spike 5 — hass-cli compatibility (RESOLVED with pin)

- Package: `homeassistant-cli==1.0.0` on PyPI (`hass-cli` alone is a
  different/nonexistent project name).
- **Python 3.14: broken** — async commands crash
  (`RuntimeError: There is no current event loop in thread 'MainThread.'`;
  e.g. `area list`). Sync commands (`info`) work.
- **Python 3.13: works** — `area list` reaches the websocket and fails only
  at auth (no token supplied), `state list` gets a clean 401 from REST.
  → **Decision: pin the CLI venv to Python 3.13** (uv-provisioned;
  3.13.15 verified).
- `hass-cli --help` documents `HASS_SERVER`/`HASS_TOKEN` env vars — the
  launcher's env-injection design is supported by the pinned release.

## Still open after phase 1 (phase 2+)

1. **Browser authorization flow**: endpoints exist; the exact desktop
   `client_id` + loopback redirect registration must be validated with a
   live browser login (needs the user to complete username/password/MFA).
2. Registry-command permissions under an ordinary account (needs a token).
3. Keyring persistence across reboot + locked-keyring UX (interactive).
4. Real light control (explicit user approval, phase 6).

## Decisions recorded (user-confirmed)

- Target: LAN plain HTTP `http://192.0.2.10:8123` (unencrypted-transport
  warning will be shown in setup).
- Login: browser OAuth flow; long-lived token only as labeled advanced path.
- Sequencing: phase order 1→5.
- Aggregate light-group entities excluded from the picker.

---

# Phase 2 — Controller and secure onboarding: results

## Implemented

- `controller/omarchy_ha_controller.py` — single-file controller (aiohttp + keyring):
  URL validation/probing with failure classification, browser authorization-code
  flow with loopback callback, pinned SecretService keyring storage, access-token
  refresh (in-memory tokens only), `/api/websocket` session with subscribe-before-
  snapshot ordering, capped exponential reconnect backoff with jitter, explicit
  `light.turn_on/turn_off` service calls, sign-out with best-effort server-side
  revocation and honest failure messaging.
- `controller/install.sh` — venv (`--system-site-packages`, no downloads),
  systemd **user** unit (`omarchy-home-assistant.service`), enable + start.
- `controller/ipc_client.py` — protocol test client.
- Widget: Service.qml maps all controller states; Panel.qml gains the onboarding
  flow (URL → HTTP warning + explicit trusted-LAN ack → "Continue in browser" →
  waiting/cancel), keyring-locked and credentials-invalid recovery, and a
  two-click confirmed Sign out.

## Verified against the live server

- **client_id/redirect contract** (from HA core `indieauth.py` master source):
  `verify_redirect_uri` passes when client_id and redirect_uri share
  scheme+netloc. Used `client_id = redirect_uri = http://127.0.0.1:<ephemeral>`.
- **Full browser sign-in completed by the user** (password + MFA on HA's own
  page): state validated, code exchanged, tokens stored, WS connected,
  `get_states: 109 entities, 15 lights` — real entities in the bar widget.
- **Restart preserves login**: controller restart → refresh grant with the
  persisted (non-secret) `client_id` → connected. NB: HA's refresh response has
  no new `refresh_token` (reusable); HA validates `client_id` on refresh, so the
  exact authorize-time string is persisted in config.json.
- **Bad callback state rejected**: wrong state and wrong path → HTTP 400,
  "state mismatch" logged, exchange never runs.
- **No secrets outside the keyring**: config.json contains only
  url/instance_id/setup_complete/client_id (not secret); controller logs and
  journal scanned — zero token occurrences; tokens travel only in HTTP POST
  bodies / WS auth frames; IPC never carries secrets.
- **systemd user service**: active, reconnects on failure, no root, only
  user-owned paths touched.

## Bugs found and fixed during bring-up

- `aiohttp` `ws_connect(timeout=ClientTimeout(total=15))` kills the session
  after 15s total — use `ClientWSTimeout`.
- Sequential read-after-write WS handling deadlocks on the first reply — the
  reader must be a concurrent task started before the first command.
- Keyring chainer tries KWallet first and its errors propagate — pin
  `SecretService.Keyring` explicitly (already mandated by the spike).

## Deferred to later phases (with reasons)

- Locked-keyring live test: requires locking the GNOME keyring interactively
  (phase 6 with the user).
- `invalid_grant` live test: would need tampering with the stored token;
  code path reviewed, will be exercised in phase 6 (token revocation test).
- Onboarding UI shown end-to-end: the live session already exists, so the
  setup states render only when signed out; full pass scheduled with the
  phase-6 sign-out/sign-in validation.

---

# Phase 3 — Discovery and control model: results

## Implemented

- `controller/topology.py` — pure resolution module: entity-area → device-area →
  Unassigned precedence; disabled/hidden registry entries dropped; aggregate
  light groups excluded (user decision); deleted-area references fall back to
  Unassigned; `area_targets()` returns explicit controllable entity ids only.
- Controller: registries fetched over WS after `get_states`
  (`config/area_registry/list`, `config/device_registry/list`,
  `config/entity_registry/list`); topology rebuilt from state+registry caches;
  `area_registry_updated` / `device_registry_updated` / `entity_registry_updated`
  subscriptions with a 1s debounce; fast-path state updates with full rebuild
  when filters change (e.g. unavailable → controllable).
- `area.all` IPC command: explicit `light.turn_on/turn_off` with the area's
  visible controllable entity ids only — no toggle, no whole-area fan-out.
- Widget: no changes needed beyond the existing grouping (groups now carry
  real area ids).

## Verified against the live server

- Registries load under the ordinary user account (permission gate passed):
  4 areas, 29 devices, 175 entities — no admin required.
- All 9 individual lights resolved to their real areas (Bedroom, Kitchen,
  Living Room, Office); 6 Hue room-group aggregates correctly excluded
  (`light.kitchen_kitchen` etc. carry member lists); popup screenshot confirms
  grouped rendering with per-area All on.
- Fixture tests: 10/10 pass (`controller/tests/test_topology.py`, pytest):
  precedence, unassigned, unavailable preservation, disabled/hidden/group
  drops, deleted-entity + deleted-area handling, purity (no input mutation).

## Notes

- Many Hue bulbs report `unavailable` (Zigbee reachability) — honest state,
  controls disabled per plan.
- Live light-toggle verification deferred: needs user approval (phase 6 gate;
  plan forbids changing devices without it).

## Live control test (user-approved)

- `light.set` on `light.office_hue_color_candle_living`: ON → service call
  accepted → observed `on` pushed over IPC; OFF → observed `off`. Pending/
  reconcile flow confirmed with a physical light.
- Bug found & fixed en route: `cmd_light_set` still used the broken
  dot-index validation (rejected every valid id) — aligned with
  `topology._valid_light`. Lesson: validation helpers must live in exactly
  one place; the next refactor funnels QML + IPC through the same rule.
- `area.all` shares the call path; physical verification deferred to phase 6
  (plan: no unapproved device changes).

## Dev-loop finding (updated)

Symlink-recreate reloads proved **unreliable for both JS imports and QML
components** — the running shell repeatedly served stale code (caused the
"switch not clickable" incident: an old `validEntityId` kept rejecting ids
silently). Rule: after changing plugin files, run `omarchy-restart-shell`;
treat symlink-recreate reloads as best-effort only.

---

# Phase 4 — Widget and visual polish: results

## Implemented

- **Light picker (onboarding step 3, available anytime)**: "Choose lights" in
  the popup footer (and `omarchy-shell iulian.home-assistant picker` IPC).
  Per-area and per-light checkboxes (area toggle selects its lights), draft
  kept locally until Save; empty selection = show everything. Selection is
  stored as **stable ids** in controller config (`selected_lights`), applied
  as a topology filter, and survives restarts. `picker.save` validates ids;
  unknown ids are ignored.
- **Icon classes from HA metadata**: controller maps each light's `icon`
  attribute to a coarse class (strip/lamp/ceiling/bulb) in
  `topology.light_icon()`; QML maps classes to distinct Nerd Font glyphs with
  name-heuristic fallback.
- **Scrollable content**: main lights list and picker list are Flickables
  with height caps (many-areas safe).
- Unavailable-light feedback, sign-out confirm, picker IPC surface.

## Verified

- Fixture tests: **14/14** (selection filter: area OR entity matching, empty
  selection shows all, unknown ids ignored, icon classification).
- Selection round-trip live: save Kitchen-only → widget shows 3 lights;
  reset → 9. Filter survives controller restart (config-backed).
- Picker UI renders with correct checkbox glyphs (first pass used wrong Nerd
  Font codepoints — gears; fixed to fa checkbox pair).
- Long names elide; 2 monitors both render the widget with a single shared
  controller connection (consumer-count pattern); shell restarts re-attach
  without duplicate connections.
- Theme compliance: only theme tokens (`Style.*`, `Color.*`) — no hardcoded
  colors outside glyph choices.

---

# Phase 5 — CLI launcher and packaging: results

## Implemented

- **`omarchy-ha` launcher** (`bin/omarchy-ha`, symlinked to `~/.local/bin`):
  sends only the argument vector over the private controller socket. The
  controller spawns hass-cli with `HASS_SERVER`/`HASS_TOKEN` injected into the
  child environment — the token never crosses IPC, argv, or the launcher's
  environment (documented same-user exposure only).
- **`controller/cli_guard.py`**: vetted-argument gate — credential options
  (`--token/--password/--supervisor-token/--cert/--insecure`), debug/trace
  (`--debug`, `-x`, `--loglevel`), server overrides, and any unknown flag are
  rejected; documented operations from the plan pass. Output is scrubbed of
  token occurrences before it crosses IPC (belt & braces).
- **Packaging** (`controller/install.sh`): pinned `homeassistant-cli==1.0.0`
  in an isolated Python **3.13** venv at
  `~/.local/share/omarchy-home-assistant/hass-cli-venv` (uv-provisioned;
  3.14 breaks hass-cli's asyncio commands — spike finding). Launcher +
  systemd unit + uninstall instructions; user-owned paths only.

## Verified live

- `omarchy-ha state list '^light\.'` → real table from the user's session,
  no token export.
- `omarchy-ha area list` → 4 areas.
- `omarchy-ha service call light.turn_on|turn_off --arguments
  entity_id=light.office_hue_color_candle_living` → bulb toggled; bar widget
  tracked it (lightsOn 1→0).
- Rejections: `--debug`, `-s http://evil`, `--token xyz` → refused with
  reasons, no spawn.
- Journal token scan: clean.

---

# End-to-end reinstall, restart and reconnect verification

## `install.sh` unit re-render broke the service (fixed)

The first `render_systemd_unit.sh` produced a unit systemd rejected:
`WorkingDirectory=` does **not** use ExecStart-style quoting, so the quoted
path was taken literally (`WorkingDirectory= path is not absolute: "..."`)
and the unit became `bad-setting`. The already-running process kept working,
so the only symptom was that every later restart failed. The renderer now
emits an unquoted `WorkingDirectory=~` (the previous implicit default) and
escapes only the `ExecStart` paths; a `systemd-analyze --user verify` test
fails the suite if systemd rejects the rendered unit.

## The widget did not reconnect after a failed attempt (fixed)

Supersedes the phase-1 note "controller restart → automatic reconnect": that
holds only while the *first* attempt succeeds. Quickshell 0.3.1
`socket.cpp` `setConnected(true)` creates a `QLocalSocket` **only when it
holds none**, and a failed connect never emits `disconnected` (only an
established connection does), so the dead socket is never cleared and later
`connected = true` writes are silent no-ops. Any reconnect attempt that lands
while the controller is down wedges the widget at `connecting` with stale data
until the shell restarts.

`Service.qml` now recreates the `Socket` element per attempt
(`Component.createObject`) instead of re-assigning `connected`.

Verified live by stopping/starting the controller with the shell left running:

| controller down | recovery |
| --- | --- |
| 5 s | ~8 s |
| 15 s | ~14 s |
| 30 s | ~30 s |

The recovery latency is the capped exponential backoff (30 s cap), not a
wedge: before the fix the widget never recovered (45 s+ with no client connect
in the controller journal).
