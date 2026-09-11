#!/bin/bash
# Render the controller's systemd user unit to stdout.
#
# Usage: render_systemd_unit.sh <python-executable> <controller-script>
#
# The checkout path is user-controlled, so the two ExecStart paths are escaped
# for systemd: a bare path can be re-split on whitespace, and '"' or '%' would
# otherwise be interpreted by systemd. A path containing a newline is rejected
# outright -- that is the one case escaping cannot make safe. Both paths are
# checked before any output, so a rejected path never yields a partial unit.
#
# WorkingDirectory is deliberately not interpolated: systemd's path settings do
# not unquote, so a quoted path is taken literally ("path is not absolute"),
# and for a user service the default is already the user's home directory.

set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: $0 <python> <controller-script>" >&2
  exit 2
fi

for value in "$@"; do
  case "$value" in
    *$'\n'* | *$'\r'*)
      printf 'error: path contains a newline: %q\n' "$value" >&2
      exit 1
      ;;
  esac
done

escape() {
  local value="$1"
  value="${value//\\/\\\\}"   # backslash first: it escapes the next character
  value="${value//\"/\\\"}"   # then the double quote
  value="${value//%/%%}"      # literal %, no systemd specifier expansion
  printf '"%s"' "$value"
}

cat <<EOF
[Unit]
Description=Omarchy Home Assistant bar controller
PartOf=graphical-session.target
After=graphical-session.target

[Service]
ExecStart=$(escape "$1") $(escape "$2")
WorkingDirectory=~
Restart=on-failure
RestartSec=3
# No tokens in service argv/config; hass-cli receives a child-environment token.
Slice=user.slice

[Install]
WantedBy=graphical-session.target
EOF
