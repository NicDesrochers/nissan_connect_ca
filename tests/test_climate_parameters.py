"""Tests for remote climate accessory preferences."""

from unittest.mock import AsyncMock, Mock

import pytest

from homeassistant.components.climate import HVACMode
from custom_components.nissan_connect_ca.button import NissanStartClimateButton
from custom_components.nissan_connect_ca.climate import NissanVehicleClimate
from custom_components.nissan_connect_ca.climate_parameters import CLIMATE_OVERRIDES, climate_parameters
from custom_components.nissan_connect_ca.switch import async_setup_entry
from tests.test_number_switch import _setup


def snapshot():
    return {"climateDefaults": {"parameters": {
        "__typename": "ClimateParameters",
        "steeringWheelHeaterState": "OFF",
        "defrostAndDeicerState": "ON",
        "seatsClimate": {
            "__typename": "SeatsClimate",
            "frontDriverState": "OFF",
            "frontPassengerState": "HEAT",
            "rearLeftPassengerState": None,
        },
    }}}


def test_filter_and_override_parameters_without_mutating_snapshot():
    data = snapshot()
    result = climate_parameters(data, {"climate_driver_seat": True, "climate_steering_wheel": True})
    assert result == {
        "steeringWheelHeaterState": "ON",
        "defrostAndDeicerState": "ON",
        "seatsClimate": {"frontDriverState": "HEAT", "frontPassengerState": "HEAT"},
    }
    assert data["climateDefaults"]["parameters"]["seatsClimate"]["frontDriverState"] == "OFF"
    assert climate_parameters({}, {"climate_driver_seat": True}) == {}


async def test_switches_created_only_for_supported_accessories():
    coordinator, entry, _ = _setup({})
    coordinator.data.update(snapshot())
    added = []
    await async_setup_entry(Mock(), entry, added.extend)
    assert {entity._key for entity in added} == {
        "climate_steering_wheel", "climate_defrost", "climate_driver_seat", "climate_passenger_seat"
    }
    driver = next(entity for entity in added if entity._key == "climate_driver_seat")
    assert driver.is_on is False
    driver.hass = Mock()
    driver.async_write_ha_state = Mock()
    await driver.async_turn_on()
    data = driver.hass.config_entries.async_update_entry.call_args.kwargs["data"]
    assert data[CLIMATE_OVERRIDES] == {"climate_driver_seat": True}


async def test_both_climate_start_paths_use_same_accessory_preferences():
    coordinator, entry, api = _setup({})
    coordinator.data.update(snapshot())
    entry.data[CLIMATE_OVERRIDES] = {"climate_driver_seat": True, "climate_steering_wheel": True}
    entry.title = "LEAF"
    hass = Mock()
    hass.states.async_all.return_value = []
    hass.async_add_executor_job = AsyncMock(return_value=None)
    button = NissanStartClimateButton(coordinator, entry, api)
    button.hass = hass
    await button.async_press()
    hass.async_add_executor_job.assert_awaited_once_with(
        api.start_climate_with_defaults, 21.0, "CELSIUS", entry.data[CLIMATE_OVERRIDES], None
    )

    hass.async_add_executor_job.reset_mock()
    entity = NissanVehicleClimate(coordinator, entry, api)
    entity._target_temperature = 32
    entity.hass = hass
    await entity.async_set_hvac_mode(HVACMode.HEAT_COOL)
    hass.async_add_executor_job.assert_awaited_once_with(
        api.start_climate_with_defaults, 32, "CELSIUS", entry.data[CLIMATE_OVERRIDES], None
    )


@pytest.mark.parametrize(("setting", "field", "state"), [
    ("climate_rear_left_seat", "rearLeftPassengerState", True),
    ("climate_rear_right_seat", "rearRightPassengerState", False),
])
def test_rear_seat_override_is_sent_for_supported_null_defaults(setting, field, state):
    data = snapshot()
    data["extra"] = {"capabilities": {"accessoriesDetails": {"seatHeater": {
        "enabled": True,
        "accessories": {"secondLeftSeat": "HEATING", "secondRightSeat": "HEATING_AND_COOLING"},
    }}}}
    assert field not in climate_parameters(data)["seatsClimate"]
    assert climate_parameters(data, {setting: state})["seatsClimate"][field] == ("HEAT" if state else "OFF")
    assert field not in climate_parameters(snapshot(), {setting: state})["seatsClimate"]


async def test_rear_switches_require_individual_enabled_capabilities():
    coordinator, entry, _ = _setup({})
    coordinator.data.update(snapshot())
    coordinator.data["extra"]["capabilities"] = {"accessoriesDetails": {"seatHeater": {
        "enabled": True,
        "accessories": {"secondLeftSeat": "HEATING", "secondRightSeat": None},
    }}}
    added = []
    await async_setup_entry(Mock(), entry, added.extend)
    rear = [entity for entity in added if entity._key.startswith("climate_rear_")]
    assert [entity._key for entity in rear] == ["climate_rear_left_seat"]
    assert rear[0].is_on is None
    entry.data[CLIMATE_OVERRIDES] = {"climate_rear_left_seat": True}
    assert rear[0].is_on is True
    coordinator.data["extra"]["capabilities"]["accessoriesDetails"]["seatHeater"]["enabled"] = False
    assert "rearLeftPassengerState" not in climate_parameters(coordinator.data, entry.data[CLIMATE_OVERRIDES])["seatsClimate"]
