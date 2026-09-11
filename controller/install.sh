#!/bin/bash
# Install the omarchy-home-assistant controller as a systemd user service
# plus the omarchy-ha CLI launcher. Only touches user-owned paths:
#   - controller venv:  <repo>/controller/.venv
#   - hass-cli venv:    ~/.local/share/omarchy-home-assistant/hass-cli-venv
#   - launcher:         ~/.local/bin/omarchy-ha (symlink)
#   - unit:             ~/.config/systemd/user/omarchy-home-assistant.service
#   - config:           ~/.config/omarchy-home-assistant/ (created by the controller)
# Uninstall: see the commands printed at the end.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONTROLLER_DIR="$REPO/controller"
VENV="$CONTROLLER_DIR/.venv"
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
UNIT="$UNIT_DIR/omarchy-home-assistant.service"

# System python: the venv reuses system site-packages (aiohttp, keyring and
# the D-Bus bindings are all system packages on this machine), so no pip
# downloads are needed.
if [ ! -x /usr/bin/python3 ]; then
  echo "error: /usr/bin/python3 not found" >&2
  exit 1
fi

if [ ! -d "$VENV" ]; then
  /usr/bin/python3 -m venv --system-site-packages "$VENV"
fi

"$VENV/bin/python" -c "import aiohttp, keyring" 2>/dev/null || {
  echo "error: aiohttp/keyring not importable in the venv" >&2
  exit 1
}

mkdir -p "$UNIT_DIR"

# Paths are escaped by the renderer so an unusual checkout name cannot break or
# inject unit directives. Render to a temp file first so a failed render never
# clobbers an existing unit.
UNIT_TMP="$UNIT.tmp"
if ! bash "$CONTROLLER_DIR/render_systemd_unit.sh" \
    "$VENV/bin/python" "$CONTROLLER_DIR/omarchy_ha_controller.py" > "$UNIT_TMP"; then
  rm -f "$UNIT_TMP"
  echo "error: could not render the systemd unit" >&2
  exit 1
fi
mv "$UNIT_TMP" "$UNIT"

# ---- hass-cli (hash-pinned, isolated, Python 3.13 for its asyncio paths) --
HASS_DATA="${XDG_DATA_HOME:-$HOME/.local/share}/omarchy-home-assistant"
HASS_CLI_VENV="$HASS_DATA/hass-cli-venv"
# Exact dependency set with sha256 hashes. Regenerate after editing the .in:
#   uv pip compile --python-version 3.13 --generate-hashes \
#     controller/requirements-hass-cli.in -o controller/requirements-hass-cli.txt
HASS_CLI_REQS="$CONTROLLER_DIR/requirements-hass-cli.txt"
if [ ! -x "$HASS_CLI_VENV/bin/hass-cli" ]; then
  mkdir -p "$HASS_DATA"
  if command -v uv >/dev/null 2>&1; then
    uv venv --python 3.13 "$HASS_CLI_VENV"
    uv pip install --python "$HASS_CLI_VENV/bin/python" --quiet \
      --require-hashes -r "$HASS_CLI_REQS"
  else
    echo "uv not found; trying python3.13 for the hass-cli venv" >&2
    PY313="$(command -v python3.13 || true)"
    if [ -z "$PY313" ]; then
      echo "error: need uv or python3.13 (hass-cli asyncio commands break on 3.14+)" >&2
      exit 1
    fi
    "$PY313" -m venv "$HASS_CLI_VENV"
    "$HASS_CLI_VENV/bin/pip" install --quiet --require-hashes -r "$HASS_CLI_REQS"
  fi
fi

# ---- omarchy-ha launcher --------------------------------------------------
mkdir -p "${XDG_BIN_HOME:-$HOME/.local/bin}"
ln -sfn "$REPO/bin/omarchy-ha" "${XDG_BIN_HOME:-$HOME/.local/bin}/omarchy-ha"

systemctl --user daemon-reload
systemctl --user enable --now omarchy-home-assistant.service

echo
echo "Installed and started: omarchy-home-assistant.service"
echo "Widget CLI: omarchy-ha state list '^light\\.'"
echo "Logs:    journalctl --user -u omarchy-home-assistant -f"
echo "Status:  systemctl --user status omarchy-home-assistant"
echo
echo "Uninstall:"
echo "  systemctl --user disable --now omarchy-home-assistant.service"
echo "  rm $UNIT"
echo "  rm ~/.local/bin/omarchy-ha"
echo "  rm -rf $VENV"
echo "  (optional) rm -rf $HASS_DATA        # removes the hass-cli venv"
echo "  (optional) rm -rf ~/.config/omarchy-home-assistant  # removes saved URL; keyring entry is separate"
