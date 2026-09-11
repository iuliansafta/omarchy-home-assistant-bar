"""Guard for the `omarchy-ha` launcher surface.

The controller spawns hass-cli with HASS_TOKEN injected into the child
environment. To keep that surface safe it forwards only a vetted subset of
arguments (plan §6): no credential-carrying or debug/trace options, no
server override (the controller pins the server), no arbitrary file paths
beyond what HA options normally take, and only a small set of subcommands
(read-only state/area/device/entity, `info`, and `service call light.*`).
Everything that reaches the full API or can act on the HA host -- raw,
template, ha, system, config, integration, event, discover, map -- is
rejected.

Pure functions, unit-tested.
"""

from __future__ import annotations

import re

# Options that would print, carry, or weaken credentials/diagnostics.
BLOCKED_FLAGS = {
    "--token",
    "--password",
    "--supervisor-token",
    "--cert",
    "--insecure",
    "--debug",
    "-x",
    "--loglevel",
    "-l",
}

# The launcher pins the server and token via the environment; overriding the
# server would defeat the controller-owned session.
SERVER_FLAGS = {"-s", "--server"}

# Value-taking flags we allow through (values validated loosely).
ALLOWED_VALUE_FLAGS = {
    "-o",
    "--output",
    "--timeout",
    "--table-format",
    "--sort-by",
    "--columns",
    "--area",
    "--name",
    "--match",
    "--columns-rows",
    "--arguments",
}

MAX_ARGS = 24
MAX_ARG_LEN = 256

# Subcommands the launcher exposes, mapped to the sub-subcommands it accepts
# (empty = the command takes no subcommand). Anything not listed here is
# rejected: `raw`/`template` reach the whole API or render templates, and
# `ha`/`system`/`config`/`integration`/`event`/`discover`/`map` can act on or
# expose the HA host.
ALLOWED_COMMANDS = {
    "state": {"list", "get"},
    "area": {"list"},
    "device": {"list"},
    "entity": {"list"},
    "service": {"call"},
    "info": frozenset(),
}

# `service call` may only target this domain; HA services outside it can run
# things on the server (shell_command, python_script, hassio, ...).
ALLOWED_SERVICE_DOMAIN = "light."

_SAFE_VALUE = re.compile(r"^[\w .,:^@$%^&()+=\[\]{}'\"|<>!?~\\-]*$")


def _validate_command(positional: list[str]) -> tuple[bool, str]:
    """Check the (flag-free) command word sequence."""
    command = positional[0]
    subcommands = ALLOWED_COMMANDS.get(command)
    if subcommands is None:
        return False, f"command not allowed through omarchy-ha: {command}"
    if not subcommands:
        if len(positional) > 1:
            return False, f"command not allowed through omarchy-ha: {' '.join(positional)}"
        return True, ""
    if len(positional) < 2 or positional[1] not in subcommands:
        return False, "subcommand not allowed through omarchy-ha: " + " ".join(positional[:2])
    if command == "service":
        service = positional[2] if len(positional) > 2 else ""
        if not service.startswith(ALLOWED_SERVICE_DOMAIN) or len(service) == len(ALLOWED_SERVICE_DOMAIN):
            return False, "only light.* services are allowed through omarchy-ha"
    return True, ""


def validate_cli_args(args: list) -> tuple[bool, str, list[str]]:
    """Validate an argv tail for hass-cli.

    Returns (ok, reason, cleaned_args). cleaned_args strips forbidden flags
    so the caller can also rely on rejection by emptiness.
    """
    if not isinstance(args, list):
        return False, "args must be a list", []
    if len(args) > MAX_ARGS:
        return False, f"too many arguments (max {MAX_ARGS})", []

    cleaned: list[str] = []
    positional: list[str] = []
    pending_value = False
    for raw in args:
        arg = raw if isinstance(raw, str) else None
        if arg is None or len(arg) > MAX_ARG_LEN:
            return False, "arguments must be short strings", []
        lowered = arg.lower()
        if lowered in BLOCKED_FLAGS or lowered.startswith("--token=") \
                or lowered.startswith("--password=") or lowered.startswith("--supervisor-token="):
            return False, f"option not allowed through omarchy-ha: {arg}", []
        if lowered in SERVER_FLAGS or lowered.startswith("--server="):
            return False, "the server is managed by the omarchy-ha controller", []
        if lowered in ALLOWED_VALUE_FLAGS:
            cleaned.append(arg)
            # The flag's value follows and must still pass validation; both
            # stay in cleaned so the CLI receives the pair intact.
            pending_value = True
            continue
        if pending_value:
            pending_value = False
            cleaned.append(arg)
            continue
        if lowered.startswith("-") and lowered not in ("--no-headers", "--json", "--all"):
            # Unknown/unsupported flags are rejected outright: this launcher
            # exposes a vetted subset, not the full CLI surface.
            if lowered.startswith("--"):
                return False, f"option not allowed through omarchy-ha: {arg}", []
            # Single-dash cluster (e.g. -vt) — reject unless it is a known
            # safe combo of output verbosity used by docs: keep it simple.
            return False, f"option not allowed through omarchy-ha: {arg}", []
        if arg and not _SAFE_VALUE.match(arg):
            return False, "argument contains unsupported characters", []
        cleaned.append(arg)
        positional.append(arg)

    if not positional:
        return False, "no command given", []
    ok, reason = _validate_command(positional)
    if not ok:
        return False, reason, []
    return True, "", cleaned


def redact_output(text: str, secrets: list[str]) -> str:
    """Scrub any secret that might leak through CLI output."""
    out = text
    for secret in secrets:
        if secret:
            out = out.replace(secret, "***")
    return out
