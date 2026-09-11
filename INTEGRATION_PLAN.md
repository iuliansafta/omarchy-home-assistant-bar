# Home Assistant bar widget for Omarchy

Status: proposed implementation plan; no widget, packages, or desktop configuration installed or changed.

## 1. Goal and MVP

Build a native Omarchy bar widget that keeps the user signed in and controls Home Assistant lights, grouped by area.

MVP:
- One Home Assistant instance and one account per Linux user.
- Bar icon with connected/offline state and number of lights on.
- Click to open a themed popup with collapsible areas and individual ON/OFF switches.
- Area-level all-on/all-off control with a visible mixed state.
- Guided setup, secure persistent login, reconnect, and sign-out.
- A credential-aware `hass-cli` launcher for terminal workflows.

Defer brightness/color, scenes, automations, other entity domains, multi-instance support, and Waybar compatibility. These are not needed to validate the core experience.

### Verified local environment

- Omarchy `4.0.3-1`, running the native `omarchy-shell` Quickshell process.
- Quickshell `0.3.1-1` is installed; this is not a Waybar implementation.
- GNOME Keyring, libsecret, and Python keyring are installed.
- `org.freedesktop.secrets` is owned by the running GNOME Keyring daemon. Collection unlock and persistence still need an interactive test; service availability alone does not prove either.
- Neither `ha` nor `hass-cli` is on PATH.
- Project directory was empty when planning started.

## 2. Proposed architecture

```text
Omarchy QML bar widget + popup + setup flow
                  │ token-free local IPC
                  ▼
Python session/controller process
 ├─ Secret Service / GNOME Keyring
 ├─ Home Assistant authentication + refresh
 ├─ WebSocket state updates, registries, service calls
 └─ hass-cli subprocess launcher
                  │ HTTPS / WSS (normally)
                  ▼
             Home Assistant
```

### Native shell plugin

Use a third-party plugin, proposed ID `iulian.home-assistant`, with a root `manifest.json` declaring a `bar-widget` entry point. Follow the installed shell's popup, theme, and settings conventions rather than designing a separate desktop window system.

Install only under `~/.config/omarchy/plugins/iulian.home-assistant/`; never edit `/usr/share/omarchy/`. Keep non-secret settings inline on the plugin entry in `~/.config/omarchy/shell.json`, consistent with the current shell contract. Set `allowMultiple: false` for MVP and default placement to the right section.

### Controller

Use Python for session ownership, Secret Service access, asynchronous networking, and subprocess handling. Suggested libraries: `aiohttp` and `keyring`, with an explicitly verified Secret Service backend; reject plaintext fallback backends.

Run one controller per user via a systemd user service, independent of popup visibility and QML reloads. Use a local Unix socket under a private `$XDG_RUNTIME_DIR` directory, restricted to the current UID. QML transport support must be proven in the first spike; use a small line-oriented bridge if needed. Do not add a local HTTP server for routine IPC.

The protocol carries snapshots, incremental updates, connection status, and explicit commands with request IDs. It must not expose credentials. Validate entity IDs, message sizes, and command kinds; do not offer arbitrary shell execution through widget IPC.

The service starts only after setup/enablement and must not require system-wide installation or root. Disabling the integration should stop its background connection; unloading one popup must not stop it.

## 3. Onboarding and authentication

### Step 1 — Home Assistant URL

- Input example: `https://homeassistant.example.com` or `http://homeassistant.local:8123`.
- Normalize scheme/host/port, reject embedded credentials, and validate supported URL structure.
- Probe reachability with a timeout without assuming the API is accessible unauthenticated.
- Distinguish DNS, connection, TLS, and unexpected-server errors.
- Prefer HTTPS. Permit HTTP only after an explicit warning that credentials/tokens travel unencrypted, intended for a trusted LAN; never silently downgrade TLS or disable certificate checks.

### Step 2 — Sign in to Home Assistant

Show “Continue in browser”. Open Home Assistant's own authorization page, where the user enters their username/password and completes MFA. The widget never collects or stores the password.

Use Home Assistant's documented authorization-code flow, exchanging the code for access and refresh tokens. Before implementation, validate the exact desktop `client_id` and redirect rules against the target server. A loopback callback is the preferred candidate, not an assumed supported configuration.

Use random single-use `state`, a short callback deadline, loopback-only binding, and strict callback validation. Close the callback listener after success/cancel. Never send tokens or authorization codes to a third-party callback service. Do not assume generic OAuth features such as PKCE are supported without checking.

If native browser flow cannot be supported cleanly, offer an explicitly labeled advanced long-lived-access-token setup path. Store that token in the keyring as well, and explain its broader lifetime and manual revocation. Do not fall back to a custom username/password form.

### Step 3 — Choose lights

Load areas and available `light.*` entities, let the user select areas or individual lights, and show a preview. Include “Unassigned” for entities without an area. Save stable IDs, not just names.

### Step 4 — Ready

Show connected server/account information and “Open controls”. An optional “Test a light” action requires an explicit click; onboarding must not change a device automatically.

### Persistent session and sign-out

- Store refresh tokens (or advanced-mode long-lived tokens) in Secret Service, keyed by application and a stable account/instance identifier.
- Keep access tokens in controller memory and refresh before expiry using server-provided lifetime information.
- Never write credentials to shell.json, plugin files, logs, command arguments, or shell startup files.
- On restart, load the saved secret after the keyring is available. If locked, display “Unlock keyring”; avoid repeated automatic prompts.
- On invalid/revoked credentials, stop retrying authentication and ask the user to reconnect.
- Sign-out should attempt server-side token revocation, remove the local secret, close the connection, and clear the displayed data. If revocation fails offline, explain that remote revocation is still needed; do not claim it succeeded.

Secret Service protects storage, not against malicious code already running as the same user. Omarchy plugins are unsandboxed. Keeping tokens out of QML reduces accidental exposure but is not a security boundary against other same-user processes.

## 4. Entities, areas, and real-time behavior

Although the UI may call them “devices”, controls target Home Assistant **entities**. One physical device can expose several light entities.

Load states plus area, device, and entity registries. Resolve grouping in this order:
1. Entity's explicit area.
2. Its device's area.
3. “Unassigned”.

Use registry IDs for joins and current names for display. Filter to available configured `light.*` entities; account for disabled/hidden entries where registry metadata is available. Handle deleted entities and renamed areas without invalidating unrelated selections.

Validate registry-command permissions using an ordinary account on the target Home Assistant version. If discovery requires elevated permissions, explain the limitation and allow explicit entity selection; do not silently demand administrator access or infer areas from friendly names.

Use a persistent `/api/websocket` connection for authentication, initial state retrieval, service calls, and state-change subscriptions. Subscribe before taking a snapshot and reconcile buffered events so initialization cannot miss a change. Refresh topology on relevant registry changes, with a documented bounded fallback refresh if needed.

Send explicit `light.turn_on` / `light.turn_off`, not `toggle`: explicit desired state avoids stale-state inversions. Area actions target only the selected visible controllable light IDs, not every device in the area. Show a pending state, then reconcile with observed Home Assistant state; successful service execution is not proof the physical light changed.

On disconnect: mark data stale, disable controls, reconnect with capped exponential backoff and jitter, and fetch a fresh snapshot. Never queue offline actions for replay. Test suspend/resume and server restarts.

## 5. UI and interaction

```text
Bar: [home icon]  3

┌ Home Assistant          Connected ┐
│ Living room        2/3 on  [Mixed] │
│   [bulb] Ceiling             [ON] │
│   [lamp] Floor lamp          [ON] │
│   [bulb] Wall light         [OFF] │
│ Kitchen            0/1 on    [OFF]│
│   [strip] Counter lights    [OFF] │
│ Unassigned                       │
│   [bulb] Desk        Unavailable  │
│ Settings · Open HA · Sign out     │
└───────────────────────────────────┘
```

Use the installed Omarchy theme's colors, spacing, typography, and popup primitives. Provide distinct bulb, lamp, and strip-light icons using a locally bundled, license-compatible icon set or shell-supported icons. Map Home Assistant icon metadata where supported, with a generic bulb fallback; do not fetch icons from the network.

Use accent color for ON, muted OFF, explicit unavailable/offline symbols, and pending feedback. Never rely on color alone. Support keyboard navigation, visible focus, accessible labels, tooltips, and a scrollable popup. Define mixed-area behavior explicitly: clicking mixed turns all selected lights on, with an explicit all-off action available.

## 6. Home Assistant CLI integration

Two different tools share similar names:
- `ha` (`home-assistant/cli`) manages the Supervisor/OS. It is not the appropriate dependency for routine light control from this desktop.
- `hass-cli` (`home-assistant-ecosystem/home-assistant-cli`) accesses Home Assistant entities/services and is the intended integration.

Install `hass-cli` in an isolated, pinned Python environment after checking current Python/server compatibility. Provide a project launcher, proposed `omarchy-ha`, that obtains a valid session through the controller and executes approved CLI operations without asking the user to export a permanent token.

The controller injects `HASS_SERVER` and a current `HASS_TOKEN` only into the child environment. Do not return the token through public IPC or put it in process arguments. Environment injection still exposes it to sufficiently privileged/same-user process inspection; document this limitation and keep child processes short-lived. Redact diagnostics and reject debug/credential-printing options in the supported launcher surface.

Reference CLI operations to validate against the pinned release:

```bash
hass-cli -o json state list '^light\.'
hass-cli area list
hass-cli device list
hass-cli service call light.turn_on --arguments entity_id=light.example
```

Use the CLI for terminal control and diagnostics, not continuous polling or one subprocess per widget update. The widget's direct API path and CLI share the same credential owner. CLI absence/failure must not break the widget. Do not use state-setting commands to represent physical device control.

## 7. Implementation phases and verification gates

1. **Compatibility spike**
   - Prove a minimal native bar popup, token-free QML/controller IPC, Secret Service round-trip with a disposable secret, browser callback flow, and ordinary-user registry access.
   - Check pinned `hass-cli` against the actual server and Python version.
   - Gate: document working contracts and resolve redirect/permission blockers before full implementation.
2. **Controller and secure onboarding**
   - Implement URL validation, browser flow, secret storage, refresh, reconnect, and logout.
   - Gate: restart preserves login; locked keyring is handled; no password or token appears in config/logs/argv; bad callback state is rejected.
3. **Discovery and control model**
   - Add area resolution, filtering, live states, and explicit individual/area actions.
   - Gate: fixture tests cover entity-over-device area precedence, unassigned lights, unavailable entities, snapshot/event races, and partial area failures. No writes occur during discovery.
4. **Native widget and visual polish**
   - Implement themed popup, onboarding states, icons, keyboard behavior, settings, and reconnect feedback.
   - Gate: test light/dark themes, long names, many areas, multiple monitors, scaling, hot reload, and shell restart without duplicate connections.
5. **CLI launcher and packaging**
   - Add isolated dependencies, user-service setup, plugin installation instructions, and safe disable/uninstall instructions.
   - Gate: CLI uses the existing session without manual token export; installation modifies only user-owned paths and preserves other bar settings. Removing credentials remains an explicit choice.
6. **End-to-end validation**
   - Test real lights only with the user's approval. Cover MFA, reboot, token expiry/revocation, keyring locking, network loss, suspend/resume, HA restart, and sign-out while offline.
   - Gate: UI reflects confirmed state rather than falsely reporting success; no offline action is replayed; document any skipped checks.

## 8. Decisions to confirm before coding

Recommended defaults above make this plan actionable, but implementation needs:
- Home Assistant version and URL/deployment pattern (LAN, TLS reverse proxy, or remote).
- Agreement on browser-based username/password login rather than an embedded password form.
- Whether the chosen account can read the required registries; prefer least privilege.
- Initial areas/lights and whether to exclude aggregate light-group entities to avoid duplicate controls.
- Approval of the proposed plugin ID and optional advanced token fallback.

No credentials are needed to discuss or review this plan.

## 9. Evidence and references

Local sources inspected:
- `/usr/share/omarchy/shell/README.md`: plugin manifest, installation, settings ownership, and unsandboxed-plugin caveats.
- `/usr/share/omarchy/shell/plugins/README.md`: built-in widget/panel examples.
- Installed package versions, running shell process, Secret Service D-Bus ownership, and command availability.

Documentation checked through Context7:
- Home Assistant developers: https://developers.home-assistant.io/docs/auth_api/
- WebSocket API: https://developers.home-assistant.io/docs/api/websocket/
- REST API: https://developers.home-assistant.io/docs/api/rest/
- Entity-control CLI: https://github.com/home-assistant-ecosystem/home-assistant-cli
- Supervisor CLI: https://github.com/home-assistant/cli

Documentation excerpts confirmed token authentication, callback code/state, revocation, and `hass-cli` environment/service operations. Exact native redirect registration, registry command availability/permissions, event sequencing, and CLI compatibility remain explicit implementation-spike checks—not claims of already tested behavior.
