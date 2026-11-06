"""Number entities for Nissan Connect Canada."""

import logging

import requests
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import NissanConnectAuthenticationError, NissanConnectCommandError
from .command_guard import guard_command
from .const import DOMAIN

PARALLEL_UPDATES = 1

_LOGGER = logging.getLogger(__name__)


def _charge_limit(data):
    return ((data or {}).get("extra", {}).get("chargeConfig") or {}).get("limits", {}).get("charge", {}).get("percent")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    """Set up the charge limit when Nissan reports a charge configuration."""
    runtime = entry.runtime_data
    coordinator = runtime.coordinator
    added = False

    def async_add_limit():
        nonlocal added
        if not added and _charge_limit(coordinator.data) is not None:
            added = True
            async_add_entities([NissanChargeLimitNumber(coordinator, entry, runtime.api)])

    async_add_limit()
    entry.async_on_unload(coordinator.async_add_listener(async_add_limit))


class NissanChargeLimitNumber(CoordinatorEntity, NumberEntity):
    """Target charge limit of the traction battery."""

    _attr_has_entity_name = True
    _attr_translation_key = "charge_limit"
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_native_min_value = 50
    _attr_native_max_value = 100
    _attr_native_step = 10
    _attr_mode = NumberMode.SLIDER
    _attr_icon = "mdi:battery-charging-high"

    def __init__(self, coordinator, entry: ConfigEntry, api) -> None:
        super().__init__(coordinator)
        self._api = api
        self._attr_unique_id = f"{entry.entry_id}_charge_limit"
        vehicle = coordinator.data.get("vehicle", {})
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, str(entry.data["vehicle_id"]))})
        self._vehicle_name = vehicle.get("model")

    @property
    def native_value(self):
        value = _charge_limit(self.coordinator.data)
        return value if isinstance(value, (int, float)) and value > 0 else None

    @guard_command()
    async def async_set_native_value(self, value: float) -> None:
        """Send the new limit to the vehicle and refresh the state."""
        try:
            await self.hass.async_add_executor_job(self._api.set_charge_limit, int(value))
        except NissanConnectAuthenticationError as err:
            raise HomeAssistantError("Nissan access token expired or rejected; reauthenticate the integration") from err
        except (NissanConnectCommandError, requests.RequestException) as err:
            raise HomeAssistantError(f"Nissan charge limit update failed: {err}") from err
        await self.coordinator.async_request_refresh()
