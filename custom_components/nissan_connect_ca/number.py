"""Number entities for Nissan Connect Canada."""

import logging

import requests
from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import NissanConnectAuthenticationError, NissanConnectCommandError
from .command_guard import guard_command
from .command_refresh import async_poll_configuration
from .const import DOMAIN

PARALLEL_UPDATES = 1

_LOGGER = logging.getLogger(__name__)


def _charge_limit(data):
    return ((data or {}).get("extra", {}).get("chargeConfig") or {}).get("limits", {}).get("charge", {}).get("percent")


def _v2l_limit(data):
    return ((data or {}).get("extra", {}).get("v2lStatus") or {}).get("chargeLimitationLevel")


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    """Set up limits when Nissan reports their configuration."""
    runtime = entry.runtime_data
    coordinator = runtime.coordinator
    added = set()

    def async_add_limit():
        entities = []
        for key, getter, entity_type in (
            ("charge", _charge_limit, NissanChargeLimitNumber),
            ("v2l", _v2l_limit, NissanV2LMinimumChargeNumber),
        ):
            if key not in added and getter(coordinator.data) is not None:
                added.add(key)
                entities.append(entity_type(coordinator, entry, runtime.api))
        if entities:
            async_add_entities(entities)

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
            request_id = await self.hass.async_add_executor_job(self._api.set_charge_limit, int(value))
        except NissanConnectAuthenticationError as err:
            raise HomeAssistantError("Nissan access token expired or rejected; reauthenticate the integration") from err
        except (NissanConnectCommandError, requests.RequestException) as err:
            raise HomeAssistantError(f"Nissan charge limit update failed: {err}") from err
        await self.coordinator.async_refresh()
        if request_id:
            self.hass.async_create_task(
                async_poll_configuration(self.hass, self._api, self.coordinator, "charge", request_id)
            )
        else:
            _LOGGER.warning("Nissan did not return a charge limit request ID; completion cannot be tracked")


class NissanV2LMinimumChargeNumber(NissanChargeLimitNumber):
    """Configure the minimum battery level reserved for V2L."""

    _attr_translation_key = "v2l_minimum_charge_level"
    _attr_native_min_value = 10
    _attr_native_max_value = 95
    _attr_native_step = 5
    _attr_icon = "mdi:power-plug-battery"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry: ConfigEntry, api) -> None:
        super().__init__(coordinator, entry, api)
        self._attr_unique_id = f"{entry.entry_id}_v2l_minimum_charge_level"

    @property
    def native_value(self):
        return _v2l_limit(self.coordinator.data)

    @guard_command()
    async def async_set_native_value(self, value: float) -> None:
        """Set the V2L limit and refresh the vehicle data."""
        if not 10 <= value <= 95 or value % 5:
            raise HomeAssistantError("V2L minimum charge level must be from 10 to 95 in steps of 5")
        try:
            request_id = await self.hass.async_add_executor_job(
                self._api.set_v2l_minimum_charge_level, int(value)
            )
        except NissanConnectAuthenticationError as err:
            raise HomeAssistantError("Nissan access token expired or rejected; reauthenticate the integration") from err
        except (NissanConnectCommandError, requests.RequestException) as err:
            raise HomeAssistantError(f"Nissan V2L limit update failed: {err}") from err
        await self.coordinator.async_refresh()
        if request_id:
            self.hass.async_create_task(
                async_poll_configuration(self.hass, self._api, self.coordinator, "v2l", request_id)
            )
        else:
            _LOGGER.warning("Nissan did not return a V2L request ID; completion cannot be tracked")
