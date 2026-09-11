# Omarchy Home Assistant bar

Native Omarchy/Quickshell plugin for Home Assistant lights grouped by area, with a shared Python controller and an authenticated `omarchy-ha` CLI bridge. Not a Waybar module or standalone QML app.

![Home Assistant plugin panel](preview.png)

**Pre-release:** manifest version `0.1.0` is not yet a validated plugin release. Licensed under [MIT](LICENSE). Live host acceptance is required before tagging a release. See [SECURITY.md](SECURITY.md).

## Requirements and compatibility

- Omarchy's Quickshell plugin host with `qs.Ui`, `qs.Commons`, service and bar-widget entry points, and `bar.shell.serviceFor`. Exact Omarchy/Quickshell versions were not recorded during bring-up: a versioned host compatibility matrix is a release gate, not a promise of compatibility with every Omarchy version.
- Linux user session with systemd user services, `XDG_RUNTIME_DIR`, D-Bus and an unlocked Secret Service keyring (GNOME Keyring). The controller pins the Secret Service backend, not KWallet.
- `/usr/bin/python3` with venv support, system `aiohttp` and `keyring`; browser plus `xdg-open`. Recorded bring-up: Python 3.14.7, aiohttp 3.13.5, keyring 25.7.0. aiohttp must provide `ClientWSTimeout`; final WebSocket origin validation currently uses its internal response URL, tested with 3.13.5.
- `uv` or Python 3.13 to provision hash-pinned `homeassistant-cli==1.0.0`. Its async commands do not work with Python 3.14. Controller and CLI have separate environments.
- Home Assistant with browser authorization and WebSocket APIs; recorded bring-up used HA 2026.9.1. No minimum HA version has been established.

## Installation (changes the live user session)

Only proceed once you choose to install; these are not validation commands. Keep the checkout at a stable absolute path: the service and launcher reference it.

1. Install the requirements above using your distribution's package manager, then from the checkout run `bash controller/install.sh`. This provisions environments, writes a systemd user unit and launcher symlink, and **enables/starts** `omarchy-home-assistant.service`. CLI provisioning can download dependencies. Failure can leave partial user-path changes; the installer is not transactional.
2. Separately register the plugin (the installer does not do this). Create `~/.config/omarchy/plugins/iulian.home-assistant` as a symlink to this checkout's `plugin/` directory; do not overwrite an unrelated existing path. In `~/.config/omarchy/shell.json`, add `{"id": "iulian.home-assistant"}` to the desired bar section (bring-up used `right`), preserving existing settings. Do not edit `/usr/share/omarchy/`.
3. Run `omarchy-restart-shell` to load the plugin. This restarts the desktop shell. Rescanning/symlink recreation alone may retain cached QML.
4. Open the widget, enter the HA base URL, and sign in through the browser. Prefer **HTTPS**: HTTP is supported but sends credentials in plaintext over the network. Unlock the keyring if prompted. Choose areas/lights after sign-in.

Paths default to `~/.config/omarchy-home-assistant/config.json`, `~/.local/share/omarchy-home-assistant/hass-cli-venv`, and `~/.local/bin/omarchy-ha`. Installer paths honor `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, and `XDG_BIN_HOME` respectively. The controller must receive the same XDG environment in its systemd user service; shell exports alone do not ensure that. `OMARCHY_HA_HASS_CLI` on the controller overrides CLI discovery. IPC uses `$XDG_RUNTIME_DIR/omarchy-ha/controller.sock`; `OMARCHY_HA_SOCKET` overrides only the QML client.

## Usage and behavior

The controller streams HA state; widgets do not poll the CLI. Area actions send explicit selected, visible, available light IDs, not HA whole-area targets. Offline actions are rejected, never replayed. Service acceptance is not observed light state.

Read-only CLI examples: `omarchy-ha info`, `omarchy-ha area list`, `omarchy-ha state get light.example`. Only validated commands/options are forwarded. CLI output has a combined 512 KiB collection ceiling and a 60-second timeout; overflow/timeout terminates the child and returns an error. Credential/debug options are rejected.

Widgets exist per monitor with a single shared service/IPC target. Panel requests currently reach all connected widget instances; focused-monitor routing is not guaranteed. Multi-monitor behavior needs live verification.

## Upgrade and uninstall

Before upgrading, review changes and retain a backup of non-secret configuration. Rerunning the installer **skips CLI provisioning if the executable exists** and `enable --now` does not explicitly restart an already-running controller. To apply a changed CLI lock, stop the service, remove/recreate only its dedicated `hass-cli-venv` under your resolved data directory, and rerun the installer. Then explicitly restart `omarchy-home-assistant.service` with `systemctl --user restart omarchy-home-assistant.service`; QML/JS changes require `omarchy-restart-shell`. These affect the live desktop. Moving the checkout requires reinstalling paths.

To uninstall deliberately:

1. While the controller/keyring are available, use widget **Sign out**. If server revocation fails, revoke the session in your Home Assistant profile too. Verify deletion of the Secret Service entry (service `omarchy-home-assistant`, account `ha-refresh-token`) with your keyring manager; deleting config does not delete keyring credentials.
2. Remove the plugin ID from `~/.config/omarchy/shell.json` and its plugin symlink, then restart the shell.
3. Run `systemctl --user disable --now omarchy-home-assistant.service`; remove its unit from your resolved XDG config directory's `systemd/user/`, then `systemctl --user daemon-reload`.
4. Remove the launcher symlink from your resolved XDG bin directory, checkout's `controller/.venv`, and optionally the dedicated CLI venv and non-secret config directory. Inspect resolved paths before deletion. Do not delete unrelated user data.

## Troubleshooting and offline checks

Use `systemctl --user status omarchy-home-assistant.service` and `journalctl --user -u omarchy-home-assistant -f`. Do not post raw logs/config/history without reviewing private URLs, identities and credentials. Never start a second controller against the live runtime directory: startup unlinks the socket. `controller/ipc_client.py` is a developer tool that connects to the running controller, not an offline test runner.

From the checkout, with pytest, aiohttp, keyring and Node available:

```sh
PYTHONDONTWRITEBYTECODE=1 PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
python -m pytest -p no:cacheprovider -q -rs controller/tests
bash -n controller/install.sh controller/render_systemd_unit.sh
```

If PATH Python lacks system packages, use the dependency-aware `PYTHONPATH` command in [AGENTS.md](AGENTS.md). Confirm zero skips (aiohttp and Node modules can otherwise skip). Tests use mocks, temporary sockets, loopback servers and disposable subprocesses, not live HA/keyring/device actions. CI does not validate QML, installation or hardware.

Before any version/tag/release: complete independent security review, owner attribution and reachable-history privacy decisions, record exact host versions, and obtain explicit permission for fresh-install, QML/multi-monitor, reconnect and real light-control acceptance. Then agree release notes and version/tag; no automatic publishing workflow is provided.
