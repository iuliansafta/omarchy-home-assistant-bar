# Omarchy Home Assistant

Home Assistant lights as a native [Omarchy 4.0](https://omarchy.org/) bar widget.

![Home Assistant bar widget with the light controls open](preview.png)

Control lights by area without leaving your desktop. Sign in once through your browser, then manage your lights from the bar with live Home Assistant state.

## Features

- Compact home icon, highlighted while any selected light is on
- Lights grouped by Home Assistant area
- Individual light switches and area-wide on/off controls
- Brightness sliders, color presets, hue/saturation and white temperature for supported lights
- Area and individual-light selection, saved across restarts
- Available/unavailable filters and keyboard navigation
- Live state updates over Home Assistant WebSocket, without CLI polling
- Browser sign-in with refresh tokens stored in Secret Service
- Shared controller across monitors
- An `omarchy-ha` launcher for validated `hass-cli` commands

## Requirements

- Omarchy 4.0 with its Quickshell plugin host
- Home Assistant with browser authorization and WebSocket APIs
- A running, unlocked Secret Service keyring, such as GNOME Keyring
- System Python (`/usr/bin/python3`) with venv support, `aiohttp` (with `ClientWSTimeout` support) and `keyring`
- `uv` or Python 3.13 to install the pinned `homeassistant-cli==1.0.0` environment
- A systemd user session, D-Bus, `XDG_RUNTIME_DIR`, a browser and `xdg-open`

The installer checks for system dependencies but does not install them. It creates separate environments for the controller and CLI, using Python 3.13 for `hass-cli`. See [compatibility notes](docs/DEVELOPMENT.md#compatibility-and-live-verification) for tested versions.

## Install

Install and enable the plugin:

```bash
omarchy plugin add https://github.com/iuliansafta/omarchy-home-assistant-bar.git --enable
```

Then install its controller from the plugin checkout:

```bash
bash ~/.config/omarchy/plugins/iulian.home-assistant/controller/install.sh
```

This creates the Python environments, installs the `omarchy-ha` launcher and enables/starts `omarchy-home-assistant.service`. It may download Python 3.13 and the hash-pinned CLI dependencies. Omarchy does not run plugin install hooks, so **both steps are required**.

### First launch and sign-in

1. Click the home icon in the bar.
2. Enter your Home Assistant base URL, preferably `https://…`.
3. Sign in through the browser and unlock your keyring if prompted.
4. Choose the areas or individual lights to show.

The saved URL and selection live in `~/.config/omarchy-home-assistant/config.json`. See [SECURITY.md](SECURITY.md) for credential storage, connection security and vulnerability reporting.

## Controls

- **Click the bar icon:** open or close the light panel.
- **Light switch:** turn an individual light on or off.
- **Area controls:** turn the selected, available lights in that area on or off.
- **Brightness:** adjust a supported light with its slider.
- **Color controls:** expand a supported light to choose colors or white temperature.
- **Keyboard:** navigate light rows with Up/Down; Left/Right adjusts brightness on a selected dimmable light. Activate to switch the selected light; Escape closes the panel.

Selecting an area includes its lights; individual selections add lights outside those areas. **An empty selection means all lights**, not none. To exclude one light from an area, select that area's individual lights instead of the whole area.

Hidden, disabled and aggregate-group lights are excluded. Lights without an area appear under **Unassigned**. Area actions target explicit light IDs rather than Home Assistant's entire area, so they do not affect lights outside your selection.

The panel reflects the state reported by Home Assistant. Controls are unavailable while offline, and missed actions are never replayed.

### Command line

With `~/.local/bin` on your `PATH`, read-only examples are:

```bash
omarchy-ha info
omarchy-ha area list
omarchy-ha state get light.example
```

Replace `light.example` with an entity ID from your Home Assistant instance. The launcher forwards supported commands through the running controller; see [CLI behavior and limits](docs/DEVELOPMENT.md#architecture).

## Update

```bash
omarchy plugin update iulian.home-assistant
bash ~/.config/omarchy/plugins/iulian.home-assistant/controller/install.sh
systemctl --user restart omarchy-home-assistant.service
```

The explicit restart applies controller changes to an already-running service. If QML/JS remains cached, run `omarchy-restart-shell` to reload the desktop shell.

The installer skips CLI provisioning when `hass-cli` already exists. If an update changes the CLI lock file, stop the service and recreate only its dedicated `hass-cli-venv` before rerunning the installer. Inspect the [resolved paths](docs/DEVELOPMENT.md#runtime-paths) first, especially with custom XDG settings.

## Troubleshooting

Check the controller:

```bash
systemctl --user status omarchy-home-assistant.service
journalctl --user -u omarchy-home-assistant -f
```

- **Dependency check fails:** make sure `aiohttp` and `keyring` are installed for `/usr/bin/python3`, not just another Python on your `PATH`.
- **Sign-in cannot be saved:** check that Secret Service is running and unlocked. The controller deliberately uses Secret Service, not KWallet.
- **CLI is not found:** check `~/.local/bin` is on `PATH` and the controller's CLI environment exists.
- **Custom XDG directories:** the systemd user service must receive the same settings as the installer. See [runtime paths](docs/DEVELOPMENT.md#runtime-paths).
- **Multiple monitors:** widgets share state, but opening the panel through IPC can open it on multiple monitors rather than only the focused monitor.

Do not start another controller manually against the live runtime directory; it would unlink the active socket.

## Remove

1. Use **Sign out** while the controller and keyring are available. See [credential cleanup](SECURITY.md#credential-cleanup) if signout fails.
2. Stop and disable the controller:

   ```bash
   systemctl --user disable --now omarchy-home-assistant.service
   ```

3. Remove its unit (`~/.config/systemd/user/omarchy-home-assistant.service`) and launcher symlink (`~/.local/bin/omarchy-ha`), then run `systemctl --user daemon-reload`. Adjust paths for custom XDG settings.
4. Remove the plugin:

   ```bash
   omarchy plugin remove iulian.home-assistant
   ```

The CLI environment at `~/.local/share/omarchy-home-assistant/hass-cli-venv` and non-secret configuration at `~/.config/omarchy-home-assistant` may remain. Remove them manually only if no longer needed.

## Development

See [docs/DEVELOPMENT.md](docs/DEVELOPMENT.md) for architecture, offline tests and runtime paths.

## License

[MIT](LICENSE)
