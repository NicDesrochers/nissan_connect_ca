"""Tests for tire pressure sources and discovery."""
from types import SimpleNamespace
from unittest.mock import Mock

from homeassistant.const import UnitOfPressure

from custom_components.nissan_connect_ca.sensor import (
    NissanVehicleSensor,
    async_setup_entry,
)


def _entry(snapshot):
    coordinator = Mock()
    coordinator.data = snapshot
    coordinator.async_add_listener = Mock(return_value=lambda: None)
    entry = Mock()
    entry.entry_id = "entry"
    entry.data = {"vehicle_id": "vehicle"}
    entry.title = "Nissan"
    entry.runtime_data = SimpleNamespace(coordinator=coordinator)
    return coordinator, entry


async def test_graphql_tires_use_psi_and_take_precedence():
    coordinator, entry = _entry({
        "vehicle": {"engineType": "Electric"},
        "health": {"tyres": [{"position": "FRONT_LEFT", "value": 230, "UoM": "KPA"}]},
        "tirePressure": {"flPressure": 34, "flStatus": 1, "frPressure": 36},
    })
    add = Mock()
    await async_setup_entry(Mock(), entry, add)
    sensors = {sensor.unique_id: sensor for sensor in add.call_args.args[0]}
    front_left = sensors["entry_tyre_pressure_front_left"]
    assert front_left.native_value == 34
    assert front_left.native_unit_of_measurement == UnitOfPressure.PSI
    assert front_left.extra_state_attributes == {"status": 1}
    assert sensors["entry_tyre_pressure_front_right"].native_value == 36


async def test_tires_added_when_they_arrive_later():
    coordinator, entry = _entry({"vehicle": {"engineType": "Electric"}, "health": {}})
    add = Mock()
    await async_setup_entry(Mock(), entry, add)
    callback = coordinator.async_add_listener.call_args.args[0]
    coordinator.data["tirePressure"] = {"rlPressure": 33}
    callback()
    callback()
    assert add.call_count == 2
    tire = add.call_args.args[0][0]
    assert tire.unique_id == "entry_tyre_pressure_rear_left"
    assert tire.native_value == 33


def test_tire_falls_back_to_rts_when_graphql_value_missing():
    coordinator, entry = _entry({
        "vehicle": {},
        "tirePressure": {"flPressure": None},
        "health": {"tyres": [{"position": "FRONT_LEFT", "value": 230, "UoM": "KPA"}]},
    })
    tire = NissanVehicleSensor(coordinator, entry, "tyre_pressure_front_left")
    assert tire.native_value == 230
    assert tire.native_unit_of_measurement == UnitOfPressure.KPA


async def test_extra_sensors_created_only_with_data_and_values():
    snapshot = {
        "vehicle": {},
        "extra": {
            "chargeHistory": {"chargeSummaries": [
                {"month": 5, "year": 2026, "numberOfChargeSessions": 2, "totalEnergyRecovered": 82.7, "totalDuration": 769},
                {"month": 6, "year": 2026, "numberOfChargeSessions": 3, "totalEnergyRecovered": 71.3, "totalDuration": 709},
            ]},
            "climateSchedules": [{"id": "1", "state": "ON"}, {"id": "2", "state": "OFF"}],
            "alerts": {"speedAlerts": [{"enabled": True}], "valetAlert": None},
        },
    }
    coordinator, entry = _entry(snapshot)
    added = []
    await async_setup_entry(Mock(), entry, lambda entities: added.extend(entities))
    values = {e._key: e.native_value for e in added}
    assert values["charge_sessions"] == 3
    assert values["charge_energy"] == 71.3
    assert values["climate_schedules_enabled"] == 1
    assert values["speed_alerts"] == 1
    assert values["valet_alert"] == 0
    assert "charge_schedules_enabled" not in values
    assert "v2l_state" not in values
