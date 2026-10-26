"""Climate control for Nissan Connect Canada."""

import asyncio
import logging
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import NissanConnectAuthenticationError, NissanConnectCommandError
from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

DEFAULT_TARGET_TEMPERATURE = 21.0
CLIMATE_POLL_ATTEMPTS = 12
CLIMATE_POLL_DELAY = 10


async def _async_poll_climate_request(hass: HomeAssistant, api, service_request_id: str, coordinator) -> None:
    """Refresh a few times until the climate request completes."""
    for attempt in range(1, CLIMATE_POLL_ATTEMPTS + 1):
        await asyncio.sleep(CLIMATE_POLL_DELAY)
        try:
            status_data = await hass.async_add_executor_job(
                api.check_climate_request, service_request_id
            )
            status = status_data.get("status")
            _LOGGER.debug(
                "Climate poll %s/%s: status=%r, details=%s",
                attempt, CLIMATE_POLL_ATTEMPTS, status, status_data.get("statusDetails")
            )
            if status and status not in ("PENDING", "IN_PROGRESS"):
                await coordinator.async_refresh()
                return
        except Exception as err:
            _LOGGER.warning("Erreur lors du suivi de la climatisation %s: %s", service_request_id, err)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up Nissan vehicle climate control for electric vehicles."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    vehicle = (coordinator.data or {}).get("vehicle", {})
    is_electric = vehicle.get("engineType") == "Electric" or bool((coordinator.data or {}).get("battery"))
    if is_electric:
        api = hass.data[DOMAIN][entry.entry_id]["api"]
        async_add_entities([NissanVehicleClimate(coordinator, entry, api)])


class NissanVehicleClimate(CoordinatorEntity, ClimateEntity, RestoreEntity):
    """Representation of Nissan vehicle climate system."""

    _attr_has_entity_name = True
    _attr_translation_key = "climate"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )
    _attr_hvac_modes = [HVACMode.HEAT_COOL, HVACMode.OFF]
    _attr_min_temp = 18.0
    _attr_max_temp = 32.0
    _attr_target_temperature_step = 1.0

    def __init__(self, coordinator, entry: ConfigEntry, api) -> None:
        """Initialize the vehicle climate entity."""
        super().__init__(coordinator)
        self._api = api
        self._attr_unique_id = f"{entry.entry_id}_climate"
        self._target_temperature = DEFAULT_TARGET_TEMPERATURE
        vehicle = coordinator.data["vehicle"]
        model = vehicle.get("model")
        year = vehicle.get("year")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(entry.data["vehicle_id"]))},
            name=entry.title,
            manufacturer="Nissan",
            model=f"{year} {model}" if year and model else model,
            model_id=vehicle.get("modelCode"),
        )

    async def async_added_to_hass(self) -> None:
        """Restore last state upon addition."""
        await super().async_added_to_hass()
        if (last_state := await self.async_get_last_state()) is not None:
            if last_state.attributes.get(ATTR_TEMPERATURE) is not None:
                try:
                    restored_temp = float(last_state.attributes[ATTR_TEMPERATURE])
                    if self._attr_min_temp <= restored_temp <= self._attr_max_temp:
                        self._target_temperature = restored_temp
                        _LOGGER.debug(
                            "Restored target temperature %s °C for %s",
                            self._target_temperature,
                            self.entity_id,
                        )
                except (ValueError, TypeError):
                    pass

    @property
    def current_temperature(self) -> float | None:
        """Return the current interior temperature."""
        temp = (self.coordinator.data or {}).get("climate", {}).get("temperature", {}).get("value")
        try:
            return float(temp) if temp is not None else None
        except (ValueError, TypeError):
            return None

    @property
    def target_temperature(self) -> float | None:
        """Return the temperature we try to reach."""
        return self._target_temperature

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current operation mode."""
        climate = (self.coordinator.data or {}).get("climate", {})
        state = str(climate.get("state", "")).upper()
        if state in ("ON", "RUNNING", "HEATING", "COOLING"):
            return HVACMode.HEAT_COOL
        return HVACMode.OFF

    @property
    def hvac_action(self) -> HVACAction | None:
        """Return current HVAC action."""
        mode = self.hvac_mode
        if mode == HVACMode.OFF:
            return HVACAction.OFF
        return HVACAction.HEATING

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target temperature and optionally apply if running."""
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        self._target_temperature = float(round(float(temp)))
        if self.hvac_mode != HVACMode.OFF:
            await self.async_set_hvac_mode(HVACMode.HEAT_COOL)
        else:
            self.async_write_ha_state()

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target hvac mode."""
        if hvac_mode == HVACMode.OFF:
            _LOGGER.debug("Stopping vehicle climate")
            try:
                req_id = await self.hass.async_add_executor_job(self._api.stop_climate)
                if req_id:
                    self.hass.async_create_task(
                        _async_poll_climate_request(self.hass, self._api, req_id, self.coordinator)
                    )
            except Exception as err:
                raise HomeAssistantError(f"Failed to stop climate: {err}") from err
        else:
            _LOGGER.debug("Starting vehicle climate at %s °C", self._target_temperature)
            try:
                req_id = await self.hass.async_add_executor_job(
                    self._api.start_climate, self._target_temperature, "CELSIUS"
                )
                if req_id:
                    self.hass.async_create_task(
                        _async_poll_climate_request(self.hass, self._api, req_id, self.coordinator)
                    )
            except Exception as err:
                raise HomeAssistantError(f"Failed to start climate: {err}") from err

    async def async_turn_on(self) -> None:
        """Turn on climate control."""
        await self.async_set_hvac_mode(HVACMode.HEAT_COOL)

    async def async_turn_off(self) -> None:
        """Turn off climate control."""
        await self.async_set_hvac_mode(HVACMode.OFF)
