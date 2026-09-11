"""Controller light paths with a fake WebSocket and no network/keyring access."""

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

pytest.importorskip("aiohttp")
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import omarchy_ha_controller as controller_module
from omarchy_ha_controller import Controller


def state_event(entity_id, state="on", attributes=None):
    return {"data": {"new_state": {
        "entity_id": entity_id, "state": state, "attributes": attributes or {},
    }}}


@pytest.fixture
def controller():
    instance = Controller()
    instance.status = "connected"
    instance._states = {"light.desk": "on"}
    instance._state_attrs = {"light.desk": {"supported_color_modes": ["rgb"], "brightness": 100}}
    instance._registry = {"areas": [], "devices": [], "entities": []}
    instance.light_selection = lambda: {}
    instance.rebuild_topology(broadcast=False)
    instance.ws_command = AsyncMock(return_value={"success": True})
    return instance


def test_setup_url_broadcasts_snapshot_with_new_server(controller, monkeypatch):
    messages = []
    controller.status = "offline"
    controller.base_url = "https://old.example"
    controller.broadcast = messages.append
    controller.probe_server = AsyncMock(return_value=(True, ""))
    monkeypatch.setattr(controller_module, "config_load", lambda: {})
    monkeypatch.setattr(controller_module, "config_save", lambda cfg: None)

    reply = AsyncMock()
    asyncio.run(controller.cmd_setup_url({"url": "https://new.example"}, reply))

    snapshots = [message for message in messages if message["type"] == "snapshot"]
    assert snapshots[-1]["server"]["url"] == "https://new.example"
    assert snapshots[-1]["status"] == "setup"


def test_initial_registry_fetch_forces_snapshot(controller):
    messages = []
    controller.broadcast = messages.append
    controller.ws_command = AsyncMock(side_effect=[
        {"success": True, "result": []},
        {"success": True, "result": []},
        {"success": True, "result": []},
    ])

    asyncio.run(controller.fetch_registries(force_snapshot=True))

    assert [message["type"] for message in messages] == ["snapshot"]
    assert messages[0]["lights"][0]["entity_id"] == "light.desk"


def test_brightness_command_waits_for_observed_state(controller):
    reply = AsyncMock()
    asyncio.run(controller.cmd_light_set({"entity_id": "light.desk", "on": True, "brightness": 200}, reply))
    controller.ws_command.assert_awaited_once_with({
        "type": "call_service", "domain": "light", "service": "turn_on",
        "target": {"entity_id": ["light.desk"]}, "service_data": {"brightness": 200},
    })
    reply.assert_awaited_once_with(True, error=None)
    assert controller.lights[0]["brightness"] == 100


@pytest.mark.parametrize("brightness,service", [(0, "turn_off"), (255, "turn_on")])
def test_service_selection(controller, brightness, service):
    asyncio.run(controller.cmd_light_set({"entity_id": "light.desk", "on": True, "brightness": brightness}, AsyncMock()))
    payload = controller.ws_command.call_args.args[0]
    assert payload["service"] == service
    assert ("service_data" in payload) is (brightness != 0)


@pytest.mark.parametrize("result", [None, {"success": False}, {"success": False, "error": {"code": "failed"}}])
def test_service_failure(controller, result):
    controller.ws_command.return_value = result
    reply = AsyncMock()
    asyncio.run(controller.cmd_light_set({"entity_id": "light.desk", "on": True}, reply))
    reply.assert_awaited_once_with(False, error="service call failed")


@pytest.mark.parametrize("status,options", [("offline", {}), ("connected", {"brightness": 999})])
def test_no_send_offline_or_invalid(controller, status, options):
    controller.status = status
    reply = AsyncMock()
    asyncio.run(controller.cmd_light_set({"entity_id": "light.desk", "on": True, **options}, reply))
    controller.ws_command.assert_not_awaited()
    assert reply.call_args.args == (False,)


def test_attribute_only_updates_and_nulls(controller):
    messages = []
    controller.broadcast = messages.append
    controller.handle_state_event(state_event("light.desk", attributes={
        "supported_color_modes": ["rgb"], "brightness": 200,
        "rgb_color": [255, 0, 0], "color_mode": "rgb"},
    ))
    assert messages[-1]["type"] == "update"
    assert messages[-1]["changes"][0]["brightness"] == 200
    assert messages[-1]["changes"][0]["rgb_color"] == [255, 0, 0]
    controller.handle_state_event(state_event("light.desk", "off", {"supported_color_modes": ["rgb"]}))
    assert messages[-1]["changes"][0]["brightness"] is None
    assert messages[-1]["changes"][0]["supports_color"]


@pytest.mark.parametrize("entry", [{"hidden_by": "user"}, {"disabled_by": "user"}, {"platform": "group"}])
def test_filtered_light_cannot_reappear_in_update(controller, entry):
    messages = []
    controller.broadcast = messages.append
    controller._registry["entities"] = [{"entity_id": "light.hidden", **entry}]
    controller.handle_state_event(state_event("light.hidden"))
    assert messages == []
    assert [light["entity_id"] for light in controller.lights] == ["light.desk"]


def test_new_aggregate_and_removal_use_snapshot(controller):
    messages = []
    controller.broadcast = messages.append
    controller.handle_state_event(state_event("light.desk", attributes={"entity_id": ["light.member"]}))
    assert messages[-1]["type"] == "snapshot"
    assert messages[-1]["lights"] == []
    controller.handle_state_event({"data": {"old_state": {"entity_id": "light.desk"}, "new_state": None}})
    assert len(messages) == 1
