# Development

## Architecture

This is a native Omarchy/Quickshell plugin, not a Waybar module or standalone QML app.

- `manifest.json` registers `plugin/Service.qml` once per shell and `plugin/BarWidget.qml` per monitor. Widgets use `bar.shell.serviceFor("iulian.home-assistant")`; IPC handlers live on the shared service.
- `controller/omarchy_ha_controller.py` owns browser authorization, Secret Service credentials, the HA WebSocket connection and newline-delimited JSON IPC. Widgets do not poll the CLI.
- `controller/topology.py` derives visible lights and area targets. Entity area overrides device area; otherwise a light is Unassigned. Hidden, disabled and aggregate-group lights are excluded. Selection matches area **or** entity; empty selection means all lights.
- `bin/omarchy-ha` forwards arguments through IPC. `controller/cli_guard.py` validates them before the controller starts `hass-cli`. Credential/debug options are rejected; combined output is limited to 512 KiB and execution to 60 seconds. Overflow or timeout terminates the child.
- `spike/` is the historical mock harness, not the current controller. `INTEGRATION_PLAN.md` is historical intent; later findings in `SPIKE_NOTES.md` supersede earlier ones. Executable code wins over prose.

## Offline checks

From the repository root, with pytest, aiohttp, keyring and Node available:

```bash
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python -m pytest -p no:cacheprovider -q -rs controller/tests
bash -n controller/install.sh controller/render_systemd_unit.sh
```

If PATH Python lacks the system dependencies:

```bash
PYTHONPATH="$(/usr/bin/python3 -c 'import aiohttp,os;print(os.path.dirname(os.path.dirname(aiohttp.__file__)))')" \
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python -m pytest -p no:cacheprovider -q -rs controller/tests
```

Confirm zero skips: aiohttp-dependent tests and the Node-backed model tests can otherwise skip. Tests use mocks, temporary sockets, loopback servers and disposable subprocesses, not live HA/keyring/device actions. CI does not validate QML, installation or hardware. The installer does not install pytest; `controller/ipc_client.py` connects to a running controller and is not an offline test runner.

## Runtime paths

| Purpose | Default |
| --- | --- |
| Non-secret configuration | `~/.config/omarchy-home-assistant/config.json` |
| Controller environment | `<plugin checkout>/controller/.venv` |
| CLI environment | `~/.local/share/omarchy-home-assistant/hass-cli-venv` |
| CLI launcher | `~/.local/bin/omarchy-ha` |
| User unit | `~/.config/systemd/user/omarchy-home-assistant.service` |
| IPC socket | `$XDG_RUNTIME_DIR/omarchy-ha/controller.sock` |

Installer paths honor `XDG_CONFIG_HOME`, `XDG_DATA_HOME` and `XDG_BIN_HOME`. The controller must receive matching XDG settings in its systemd user service; shell exports alone do not ensure that. `OMARCHY_HA_HASS_CLI` on the controller overrides CLI discovery. `OMARCHY_HA_SOCKET` overrides only the QML client.

Never start a second controller against the live runtime directory: startup unlinks the socket. The spike's `secret.check` writes and deletes a keyring entry.

## Compatibility and live verification

Recorded bring-up used Python 3.14.7, aiohttp 3.13.5, keyring 25.7.0 and Home Assistant 2026.9.1. These are observations, not a minimum-version guarantee. aiohttp must provide `ClientWSTimeout`; final WebSocket origin validation uses its internal response URL and needs retesting on upgrades. `homeassistant-cli==1.0.0` requires a separate Python 3.13 environment for its async commands.

The installer embeds the checkout path in the unit. After installed controller edits, restart `omarchy-home-assistant.service`. After QML/JS edits, use `omarchy-restart-shell`; symlink recreation and rescanning have not reliably cleared cached components. Both affect the live desktop and require owner approval during agent-driven testing. Real light-control tests also require explicit approval.

Release acceptance checks are tracked in [SECURITY.md](../SECURITY.md#release-acceptance-checklist).

See [AGENTS.md](../AGENTS.md) for additional development invariants.
