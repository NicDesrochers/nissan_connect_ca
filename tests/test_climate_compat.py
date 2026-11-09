"""The climate entity must expose temperatures under the name the installed core uses."""
import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pytest
from homeassistant.components.climate import ClimateEntity, HVACMode
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from custom_components.nissan_connect_ca.api import NissanConnectCommandError
from custom_components.nissan_connect_ca.climate import NissanVehicleClimate, _async_poll_climate_request


def test_temperature_members_match_installed_core():
    native = hasattr(ClimateEntity, "native_current_temperature")
    entity = NissanVehicleClimate.__new__(NissanVehicleClimate)
    entity.coordinator = SimpleNamespace(data={"climate": {"temperature": {"value": 21}}})
    entity._target_temperature = 22.0
    name = "native_current_temperature" if native else "current_temperature"
    target = "native_target_temperature" if native else "target_temperature"
    assert getattr(entity, name) == 21.0
    assert getattr(entity, target) == 22.0
    assert (entity.temperature_unit is not None)


async def test_setting_temperature_while_off_refreshes_vehicle_data():
    entity = NissanVehicleClimate.__new__(NissanVehicleClimate)
    entity.coordinator = Mock()
    entity.coordinator.data = {"climate": {"state": "OFF"}}
    entity.coordinator.async_refresh = AsyncMock()
    entity._target_temperature = None
    entity._vehicle_name = "Nissan"
    entity._entry = SimpleNamespace(data={})
    entity._temperature_lock = asyncio.Lock()
    entity.async_write_ha_state = Mock()
    entity._api = Mock()
    entity.hass = Mock()

    async def executor(func, *args):
        return func(*args)

    entity.hass.async_add_executor_job = executor
    await entity.async_set_temperature(temperature=22)
    entity._api.sync_climate_defaults.assert_called_once_with(22, "CELSIUS", None, None)
    entity.coordinator.async_refresh.assert_awaited_once()
    entity.async_write_ha_state.assert_called_once()


@pytest.mark.parametrize("model", ["ARIYA", "LEAF", "Other"])
async def test_temperature_while_running_is_saved_for_next_start(model):
    entity = NissanVehicleClimate.__new__(NissanVehicleClimate)
    entity.coordinator = Mock()
    entity.coordinator.data = {"vehicle": {"model": model}, "climate": {"state": "ON"}}
    entity._entry = SimpleNamespace(data={})
    entity._temperature_lock = asyncio.Lock()
    entity.async_write_ha_state = Mock()
    entity._target_temperature = 19
    entity._vehicle_name = model
    entity._api = Mock()
    entity.hass = Mock()
    entity.hass.async_add_executor_job = AsyncMock(return_value=None)

    await entity.async_set_temperature(temperature=20)

    assert entity._target_temperature == 20
    entity.async_write_ha_state.assert_called_once()
    entity.hass.async_add_executor_job.assert_not_awaited()
    entity.hass.async_create_task.assert_not_called()
    entity._api.start_climate_with_defaults.assert_not_called()
    entity._api.sync_climate_defaults.assert_not_called()

    entity.coordinator.data["climate"]["state"] = "OFF"
    await entity.async_set_hvac_mode(HVACMode.HEAT_COOL)
    entity.hass.async_add_executor_job.assert_awaited_once_with(
        entity._api.start_climate_with_defaults, 20, "CELSIUS", None, None
    )


async def test_failed_climate_request_is_logged(caplog):
    hass = Mock()
    api = Mock()
    coordinator = Mock()
    coordinator.async_refresh = AsyncMock()
    hass.async_add_executor_job = AsyncMock(return_value={"status": "FAILED", "statusDetails": "rejected"})
    with patch("custom_components.nissan_connect_ca.climate.asyncio.sleep", new_callable=AsyncMock):
        await _async_poll_climate_request(hass, api, "request", coordinator, "ARIYA")
    assert "Climate request failed: status=FAILED, details=rejected" in caplog.text
    coordinator.async_refresh.assert_awaited_once()


async def test_local_target_does_not_replace_nissan_default_snapshot():
    entity = NissanVehicleClimate.__new__(NissanVehicleClimate)
    entity.coordinator = Mock()
    entity.coordinator.data = {
        "climate": {"state": "OFF"},
        "climateDefaults": {"climate": {"value": 19, "unit": "CELSIUS"}},
    }
    entity.coordinator.async_refresh = AsyncMock()
    entity._entry = SimpleNamespace(data={})
    entity._temperature_lock = asyncio.Lock()
    entity._target_temperature = 19
    entity._vehicle_name = "Nissan"
    entity._api = Mock()
    entity._api.sync_climate_defaults.return_value = None
    entity.hass = Mock()
    entity.async_write_ha_state = Mock()

    async def executor(func, *args):
        return func(*args)

    entity.hass.async_add_executor_job = executor
    await entity.async_set_temperature(temperature=23)
    assert entity._climate_target_temperature == 23
    assert entity.coordinator.data["climateDefaults"]["climate"]["value"] == 19


async def test_setting_temperature_surfaces_default_update_failure():
    entity = NissanVehicleClimate.__new__(NissanVehicleClimate)
    entity.coordinator = Mock()
    entity.coordinator.data = {"climate": {"state": "OFF"}}
    entity._entry = SimpleNamespace(data={})
    entity._temperature_lock = asyncio.Lock()
    entity.async_write_ha_state = Mock()
    entity._target_temperature = 18
    entity._vehicle_name = "Nissan"
    entity._api = Mock()
    entity._api.sync_climate_defaults.side_effect = NissanConnectCommandError("rejected")
    entity.hass = Mock()

    async def executor(func, *args):
        return func(*args)

    entity.hass.async_add_executor_job = executor
    with pytest.raises(HomeAssistantError, match="rejected"):
        await entity.async_set_temperature(temperature=22)
    assert entity._target_temperature == 18
    entity.async_write_ha_state.assert_not_called()


async def test_saved_target_wins_over_cloud_and_survives_updates(caplog):
    entity = NissanVehicleClimate.__new__(NissanVehicleClimate)
    entity.coordinator = SimpleNamespace(data={"climateDefaults": {"climate": {"value": 18}}})
    entity._target_temperature = None
    entity._vehicle_name = "Nissan"
    entity.async_get_last_state = AsyncMock(return_value=SimpleNamespace(attributes={"temperature": 23}))
    caplog.set_level(logging.DEBUG, logger="custom_components.nissan_connect_ca.climate")
    with patch.object(CoordinatorEntity, "async_added_to_hass", new_callable=AsyncMock), \
         patch.object(CoordinatorEntity, "_handle_coordinator_update"):
        await entity.async_added_to_hass()
        entity._handle_coordinator_update()
    assert entity._climate_target_temperature == 23
    assert "target=23.0 °C, source=restored Home Assistant state" in caplog.text


async def test_missing_restored_target_uses_cloud_default(caplog):
    entity = NissanVehicleClimate.__new__(NissanVehicleClimate)
    entity.coordinator = SimpleNamespace(data={"climateDefaults": {"climate": {"value": 19}}})
    entity._target_temperature = None
    entity._vehicle_name = "Nissan"
    entity.async_get_last_state = AsyncMock(return_value=None)
    caplog.set_level(logging.DEBUG, logger="custom_components.nissan_connect_ca.climate")
    with patch.object(CoordinatorEntity, "async_added_to_hass", new_callable=AsyncMock):
        await entity.async_added_to_hass()
    assert entity._climate_target_temperature == 19
    assert "target=19.0 °C, source=Nissan default" in caplog.text


async def test_thermostat_logs_selected_target_and_nissan_default(caplog):
    entity = NissanVehicleClimate.__new__(NissanVehicleClimate)
    entity.coordinator = Mock()
    entity.coordinator.data = {
        "climateDefaults": {"climate": {"value": 19, "unit": "CELSIUS"}}
    }
    entity._target_temperature = 20
    entity._vehicle_name = "ARIYA"
    entity._entry = SimpleNamespace(data={})
    entity._api = Mock()
    entity.hass = Mock()
    entity.hass.async_add_executor_job = AsyncMock(return_value=None)
    caplog.set_level(logging.DEBUG, logger="custom_components.nissan_connect_ca.climate")

    await entity.async_set_hvac_mode(HVACMode.HEAT_COOL)

    assert "target=20 °C, source=retained thermostat target, retained=20 °C" in caplog.text
    assert "'value': 19" in caplog.text
    entity.hass.async_add_executor_job.assert_awaited_once_with(
        entity._api.start_climate_with_defaults, 20, "CELSIUS", None, None
    )


async def test_temperature_is_retained_when_refresh_returns_stale_default():
    entity = NissanVehicleClimate.__new__(NissanVehicleClimate)
    entity.coordinator = Mock()
    entity.coordinator.data = {"climate": {"state": "OFF"}, "climateDefaults": {"climate": {"value": 18}}}
    entity._entry = SimpleNamespace(data={})
    entity._temperature_lock = asyncio.Lock()
    entity._target_temperature = 18
    entity._vehicle_name = "Nissan"
    entity._api = Mock()
    entity.hass = Mock()
    entity.async_write_ha_state = Mock()

    async def executor(func, *args):
        return func(*args)

    async def stale_refresh():
        entity.coordinator.data["climateDefaults"]["climate"]["value"] = 18
        entity._handle_coordinator_update()

    entity.hass.async_add_executor_job = executor
    entity.coordinator.async_refresh = stale_refresh
    with patch.object(CoordinatorEntity, "_handle_coordinator_update"):
        await entity.async_set_temperature(temperature=23)
    assert entity._climate_target_temperature == 23
    entity.async_write_ha_state.assert_called_once()


async def test_temperature_changes_are_serialized():
    entity = NissanVehicleClimate.__new__(NissanVehicleClimate)
    entity.coordinator = Mock()
    entity.coordinator.data = {"climate": {"state": "OFF"}}
    entity.coordinator.async_refresh = AsyncMock()
    entity._entry = SimpleNamespace(data={})
    entity._temperature_lock = asyncio.Lock()
    entity._target_temperature = 18
    entity._vehicle_name = "Nissan"
    entity._api = Mock()
    entity.hass = Mock()
    entity.async_write_ha_state = Mock()
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def executor(func, target, *args):
        calls.append(target)
        if target == 22:
            started.set()
            await release.wait()

    entity.hass.async_add_executor_job = executor
    first = asyncio.create_task(entity.async_set_temperature(temperature=22))
    await started.wait()
    second = asyncio.create_task(entity.async_set_temperature(temperature=23))
    await asyncio.sleep(0)
    assert calls == [22]
    release.set()
    await asyncio.gather(first, second)
    assert calls == [22, 23]
    assert entity._climate_target_temperature == 23
