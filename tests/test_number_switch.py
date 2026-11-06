"""Tests for the charge limit number and schedule switches."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.nissan_connect_ca import number, switch
from custom_components.nissan_connect_ca.api import NissanConnectCommandError


def _setup(extra):
    coordinator = Mock()
    coordinator.data = {"vehicle": {"model": "LEAF"}, "extra": extra}
    coordinator.async_add_listener = Mock(return_value=lambda: None)
    coordinator.async_request_refresh = AsyncMock()
    entry = Mock(entry_id="entry", data={"vehicle_id": "v"})
    api = Mock()
    entry.runtime_data = SimpleNamespace(coordinator=coordinator, api=api)
    return coordinator, entry, api


async def test_number_only_created_with_charge_config_and_sets_limit():
    coordinator, entry, api = _setup({})
    added = []
    await number.async_setup_entry(Mock(), entry, added.extend)
    assert added == []

    coordinator, entry, api = _setup({"chargeConfig": {"limits": {"charge": {"percent": 80}}}})
    await number.async_setup_entry(Mock(), entry, added.extend)
    entity = added[0]
    assert entity.native_value == 80
    entity.hass = Mock()
    entity.hass.async_add_executor_job = _executor
    await entity.async_set_native_value(90)
    api.set_charge_limit.assert_called_once_with(90)


async def _executor(func, *args):
    return func(*args)


async def test_switches_reflect_state_and_toggle():
    coordinator, entry, api = _setup(
        {"climateSchedules": [{"id": "a", "state": "ON"}, {"id": "b", "state": "OFF"}]}
    )
    added = []
    await switch.async_setup_entry(Mock(), entry, added.extend)
    assert [e.is_on for e in added] == [True, False]
    entity = added[1]
    entity.hass = Mock()
    entity.hass.async_add_executor_job = _executor
    await entity.async_turn_on()
    api.toggle_schedule.assert_called_once_with("climate", "b", True)


async def test_switch_command_error_is_user_facing():
    coordinator, entry, api = _setup({"chargeSchedules": [{"id": "1", "state": "OFF"}]})
    api.toggle_schedule.side_effect = NissanConnectCommandError("boom")
    added = []
    await switch.async_setup_entry(Mock(), entry, added.extend)
    added[0].hass = Mock()
    added[0].hass.async_add_executor_job = _executor
    with pytest.raises(HomeAssistantError):
        await added[0].async_turn_on()
