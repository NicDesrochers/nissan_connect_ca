"""Tests for remote engine buttons."""
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.nissan_connect_ca.api import (
    NissanConnectAuthenticationError,
    NissanConnectCommandError,
)
from custom_components.nissan_connect_ca.button import (
    NissanRequestVehicleRefreshButton,
    NissanRemoteStartButton,
    NissanRemoteStopButton,
    _async_poll_vehicle_refresh,
    async_setup_entry,
)


def _entry(service_requested=True, engine_id="engine-id", electric=False):
    coordinator = Mock()
    coordinator.data = {
        "vehicle": {
            "engineType": "Electric" if electric else "Gasoline",
            "allVehicleServiceDetails": {
                "vehicleServiceDetails": [{
                    "vehicleServiceName": "Remote Engine Start / Stop AVK",
                    "serviceGroup": "rts-engine-state",
                    "serviceRequested": service_requested,
                }],
            },
        },
        "engines": {"engines": [{"id": engine_id}] if engine_id else []},
    }
    api = Mock()
    api._label = "Nissan"
    entry = SimpleNamespace(
        entry_id="entry",
        data={"vehicle_id": "vehicle-id"},
        title="Nissan",
        runtime_data=SimpleNamespace(coordinator=coordinator, api=api),
    )
    return coordinator, api, entry


@pytest.mark.parametrize(
    ("requested", "engine_id", "electric", "expected"),
    [(True, "engine-id", False, True), (False, "engine-id", False, False),
     (True, None, False, False), (True, "engine-id", True, False)],
)
async def test_engine_buttons_share_capability_gate(requested, engine_id, electric, expected):
    _, _, entry = _entry(requested, engine_id, electric)
    add_entities = Mock()
    await async_setup_entry(Mock(), entry, add_entities)
    buttons = add_entities.call_args.args[0]
    assert any(type(button) is NissanRemoteStartButton for button in buttons) is expected
    assert any(type(button) is NissanRemoteStopButton for button in buttons) is expected


@pytest.mark.parametrize(
    ("button_class", "method", "unique_id"),
    [(NissanRemoteStartButton, "start_engine", "entry_remote_start"),
     (NissanRemoteStopButton, "stop_engine", "entry_remote_stop")],
)
async def test_engine_button_sends_command_and_polls(button_class, method, unique_id):
    coordinator, api, entry = _entry()
    button = button_class(coordinator, entry, api, "engine-id")
    hass = Mock()
    hass.async_add_executor_job = AsyncMock()
    hass.async_create_task = Mock(side_effect=lambda coro: coro.close())
    button.hass = hass

    await button.async_press()

    assert button.unique_id == unique_id
    hass.async_add_executor_job.assert_awaited_once_with(getattr(api, method), "engine-id")
    hass.async_create_task.assert_called_once()


@pytest.mark.parametrize(
    ("error", "message"),
    [(NissanConnectAuthenticationError("rejected"), "reauthenticate"),
     (NissanConnectCommandError("rejected"), "remote stop failed"),
     (ValueError("invalid"), "remote stop request failed")],
)
async def test_remote_stop_surfaces_errors(error, message):
    coordinator, api, entry = _entry()
    button = NissanRemoteStopButton(coordinator, entry, api, "engine-id")
    hass = Mock()
    hass.async_add_executor_job = AsyncMock(side_effect=error)
    button.hass = hass

    with pytest.raises(HomeAssistantError, match=message):
        await button.async_press()
    hass.async_create_task.assert_not_called()


@pytest.mark.parametrize("requested", [True, False])
async def test_vehicle_refresh_button_requires_service(requested):
    _, _, entry = _entry()
    entry.runtime_data.coordinator.data["vehicle"]["allVehicleServiceDetails"]["vehicleServiceDetails"].append(
        {"vehicleServiceName": "Remote Vehicle Status", "serviceRequested": requested}
    )
    add_entities = Mock()
    await async_setup_entry(Mock(), entry, add_entities)
    assert any(type(button) is NissanRequestVehicleRefreshButton
               for button in add_entities.call_args.args[0]) is requested


async def test_vehicle_refresh_button_requests_and_schedules_poll():
    coordinator, api, entry = _entry()
    button = NissanRequestVehicleRefreshButton(coordinator, entry, api)
    hass = Mock()
    hass.async_add_executor_job = AsyncMock(return_value="request-id")
    hass.async_create_task = Mock(side_effect=lambda coro: coro.close())
    button.hass = hass
    await button.async_press()
    hass.async_add_executor_job.assert_awaited_once_with(api.refresh_vehicle_status)
    hass.async_create_task.assert_called_once()


@pytest.mark.parametrize(("status", "refresh"), [("SUCCESS", True), ("FAILED", False)])
async def test_vehicle_refresh_poll_updates_only_on_success(status, refresh, monkeypatch):
    coordinator, api, _ = _entry()
    coordinator.async_refresh = AsyncMock()
    hass = Mock()
    hass.async_add_executor_job = AsyncMock(return_value={"status": status})
    sleep = AsyncMock()
    monkeypatch.setattr("custom_components.nissan_connect_ca.button.asyncio.sleep", sleep)
    await _async_poll_vehicle_refresh(hass, api, "request-id", coordinator)
    assert coordinator.async_refresh.await_count == int(refresh)


async def test_vehicle_refresh_poll_waits_for_intermediate_states(monkeypatch):
    coordinator, api, _ = _entry()
    coordinator.async_refresh = AsyncMock()
    hass = Mock()
    hass.async_add_executor_job = AsyncMock(side_effect=[
        {"status": "INITIATED"}, {"status": "SENT"}, {"status": "SUCCESS"},
    ])
    monkeypatch.setattr("custom_components.nissan_connect_ca.button.asyncio.sleep", AsyncMock())
    await _async_poll_vehicle_refresh(hass, api, "request-id", coordinator)
    assert hass.async_add_executor_job.await_count == 3
    coordinator.async_refresh.assert_awaited_once()
