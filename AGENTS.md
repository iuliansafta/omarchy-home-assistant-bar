# Repository Context

- This is a native Omarchy/Quickshell plugin, not Waybar or a standalone QML app. Root `manifest.json` points to `plugin/Service.qml` (mounted once per shell) and `plugin/BarWidget.qml` (mounted per monitor); widgets obtain the shared service via `bar.shell.serviceFor("iulian.home-assistant")`. Keep IPC handlers on the service to avoid per-monitor collisions.
- `controller/omarchy_ha_controller.py` owns authentication, keyring, HA WebSocket, and newline-delimited JSON IPC. `bin/omarchy-ha` forwards arguments through IPC; the controller validates them with `cli_guard.py` and spawns `hass-cli`. The widget does not use CLI polling.
- `spike/` is the mock compatibility harness, not the current controller. `INTEGRATION_PLAN.md` is historical intent; `SPIKE_NOTES.md` records bring-up findings chronologically. Later findings supersede earlier ones; executable code wins over prose.

# Verification

Run from the repository root:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -p no:cacheprovider -q controller/tests
```

- For one test, replace `controller/tests` with e.g. `controller/tests/test_topology.py::test_area_targets_excludes_unavailable_and_other_areas`.
- Some modules skip when their dependency is absent: the controller-session/IPC tests need `aiohttp`, and `test_hass_model_js.py` needs `node`. The pytest interpreter often lacks `aiohttp` (the mise Python does), so a "passing" run can silently skip them. To run the whole suite, put the system site-packages on `PYTHONPATH`; this is also what `controller/.venv` sees via `--system-site-packages`:

```bash
PYTHONPATH="$(/usr/bin/python3 -c 'import aiohttp,os;print(os.path.dirname(os.path.dirname(aiohttp.__file__)))')" \
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python -m pytest -p no:cacheprovider -q controller/tests
```

- Use `-rs` to list skip reasons; confirm the expected number of tests actually ran.
- The installer does not install pytest into `controller/.venv`; `bin/omarchy-ha` and `controller/ipc_client.py` are live-controller tools, not offline runners. The spike's `secret.check` writes/deletes a keyring entry. `__pycache__/`, `.venv/` and `.pytest_cache/` are gitignored, so bytecode suppression is only tidiness.
- Coverage: topology and CLI validation/redaction, controller session/credential handling, IPC framing limits, URL validation, the systemd unit renderer (bash) and the `hass-cli` lock file, and `HaModel.js` (node). QML components are not unit-tested; verify them live.

# Runtime And Dev Loop

- `bash controller/install.sh` creates environments, writes a systemd user unit and launcher symlink, and enables/starts `omarchy-home-assistant.service`. It is not a build/check command and does not register the QML plugin. The unit embeds the checkout path.
- Controller runtime uses `/usr/bin/python3` via `controller/.venv` with `--system-site-packages`, requiring system `aiohttp` and `keyring`. PATH Python may be shadowed by mise. Separately, `homeassistant-cli==1.0.0` needs the installer's Python 3.13 environment: its async commands fail on 3.14.
- The CLI installer and controller honor `XDG_DATA_HOME` (default `~/.local/share`) for `omarchy-home-assistant/hass-cli-venv/bin/hass-cli`; ensure the systemd user service receives the same XDG environment, or set `OMARCHY_HA_HASS_CLI` on the controller explicitly.
- Set `XDG_RUNTIME_DIR`; controller and CLI use `$XDG_RUNTIME_DIR/omarchy-ha/controller.sock`. `OMARCHY_HA_SOCKET` overrides only the QML client. Never start a second controller against the live runtime directory: startup unlinks the socket.
- Public plugin installation is `omarchy plugin add <git-url> --enable`, which clones the repository to `~/.config/omarchy/plugins/iulian.home-assistant`; root `manifest.json` keeps QML under `plugin/`. The separate controller installer must then be run from that clone. Do not edit `/usr/share/omarchy/` for installation.
- After plugin QML/JS edits, use `omarchy-restart-shell` for live verification. Symlink recreation and rescanning proved unreliable at clearing cached components (see the updated dev-loop finding in `SPIKE_NOTES.md`).
- For installed controller changes: `systemctl --user restart omarchy-home-assistant.service`; logs: `journalctl --user -u omarchy-home-assistant -f`. Restarts affect the live desktop session. Real light-control tests require explicit user approval.

# Invariants

- Pin `keyring.backends.SecretService.Keyring`; default backend chaining tries KWallet and can fail before reaching Secret Service. Preserve the exact authorize-time `client_id` in `$XDG_CONFIG_HOME/omarchy-home-assistant/config.json` (default `~/.config/...`): HA validates it on refresh after restart.
- Refresh tokens belong in Secret Service; access tokens stay in controller memory except `HASS_TOKEN` injection into the `hass-cli` child environment. Never expose credentials in QML, IPC, config, logs, or argv; retain CLI argument validation and output redaction.
- Keep `controller/topology.py` pure: entity area overrides device area, otherwise Unassigned; exclude hidden/disabled/aggregate-group lights. Selection matches area OR entity; empty selection means all lights.
- Area actions target explicit selected, visible, available entity IDs, not HA's whole-area target. Send `light.turn_on`/`turn_off`, never `toggle`; service acceptance is not observed state, and offline actions must not be replayed.
- HA WebSocket replies require a concurrent reader started before requests. Subscribe before fetching initial state; use `aiohttp.ClientWSTimeout`, not a total `ClientTimeout` that terminates the persistent session.
