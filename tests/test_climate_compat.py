"""The climate entity must expose temperatures under the name the installed core uses."""
from types import SimpleNamespace
from unittest.mock import Mock

from homeassistant.components.climate import ClimateEntity

from custom_components.nissan_connect_ca.climate import NissanVehicleClimate


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
