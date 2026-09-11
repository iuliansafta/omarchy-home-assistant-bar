"""Tests for the omarchy-ha launcher guard (plan §6)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from cli_guard import redact_output, validate_cli_args  # noqa: E402


def test_documented_operations_pass():
    ok, _, _ = validate_cli_args(["state", "list", "^light\\."])
    assert ok
    ok, _, _ = validate_cli_args(["area", "list"])
    assert ok
    ok, _, _ = validate_cli_args(["device", "list"])
    assert ok
    ok, _, _ = validate_cli_args(["service", "call", "light.turn_on", "--arguments", "entity_id=light.x"])
    assert ok


def test_credential_and_debug_flags_rejected():
    for bad in (
        ["--token", "abc", "state", "list"],
        ["--password", "hunter2"],
        ["--supervisor-token=zzz"],
        ["--debug"],
        ["-x"],
        ["--insecure"],
    ):
        ok, reason, cleaned = validate_cli_args(bad)
        assert not ok, bad
        assert "not allowed" in reason or "managed" in reason
        assert cleaned == []


def test_server_override_rejected():
    ok, reason, _ = validate_cli_args(["-s", "http://evil", "state", "list"])
    assert not ok and "managed" in reason
    ok, _, _ = validate_cli_args(["--server=http://evil", "state", "list"])
    assert not ok


def test_unknown_long_option_rejected():
    ok, reason, _ = validate_cli_args(["state", "list", "--something-new"])
    assert not ok and "not allowed" in reason


def test_unknown_single_dash_rejected():
    ok, reason, _ = validate_cli_args(["state", "list", "-q"])
    assert not ok and "not allowed" in reason


def test_value_flags_take_next_arg():
    ok, _, cleaned = validate_cli_args(["state", "list", "-o", "json"])
    assert ok and cleaned == ["state", "list", "-o", "json"]


def test_limits_and_types():
    assert not validate_cli_args(["a"] * 25)[0]
    assert not validate_cli_args(["x" * 300])[0]
    assert not validate_cli_args([None])[0]
    assert not validate_cli_args([])[0]


def test_redaction():
    assert redact_output("token=SECRET here", ["SECRET"]) == "token=*** here"


def test_dangerous_subcommands_rejected():
    for bad in (
        ["raw", "get", "config"],
        ["raw", "ws", "subscribe_events"],
        ["raw", "post", "template"],
        ["template", "payload.jinja"],
        ["ha", "core", "restart"],
        ["system", "restart"],
        ["config", "check"],
        ["integration", "disable", "x"],
        ["event", "fire", "x"],
        ["discover", "x"],
        ["map", "x"],
    ):
        ok, reason, cleaned = validate_cli_args(bad)
        assert not ok, bad
        assert cleaned == []
        assert "not allowed" in reason


def test_service_call_is_restricted_to_the_light_domain():
    for bad in (
        ["service", "call", "shell_command.run_thing"],
        ["service", "call", "hassio.host_reboot"],
        ["service", "call", "python_script.anything"],
        ["service", "call", "light."],
        ["service", "call"],
    ):
        ok, _, _ = validate_cli_args(bad)
        assert not ok, bad
    ok, _, _ = validate_cli_args(
        ["service", "call", "light.turn_off", "--arguments", "entity_id=light.x"]
    )
    assert ok


def test_read_only_commands_pass():
    for good in (["state", "get", "light.x"], ["entity", "list"], ["info"]):
        ok, reason, _ = validate_cli_args(good)
        assert ok, (good, reason)


def test_missing_or_extra_subcommand_rejected():
    for bad in (["state"], ["area"], ["service"], ["info", "extra"], ["state", "delete"]):
        ok, _, _ = validate_cli_args(bad)
        assert not ok, bad
