"""Buttons for Nissan Connect Canada."""

import asyncio
import logging

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import NissanConnectAuthenticationError, NissanConnectCommandError
from .command_guard import guard_command
from .const import DOMAIN

PARALLEL_UPDATES = 1

_LOGGER = logging.getLogger(__name__)

LOCK_POLL_ATTEMPTS = 3
LOCK_POLL_DELAY = 5
# Nissan can take several minutes to close an engine request.
ENGINE_POLL_ATTEMPTS = 16
ENGINE_POLL_DELAY = 15
CLIMATE_POLL_ATTEMPTS = 12
CLIMATE_POLL_DELAY = 10
CHARGE_POLL_ATTEMPTS = 10
CHARGE_POLL_DELAY = 10
HORN_LIGHT_POLL_ATTEMPTS = 6
HORN_LIGHT_POLL_DELAY = 5


def _overall_lock_status(coordinator):
    locks = (coordinator.data or {}).get("health", {}).get("locks", [])
    return next(
        (str(lock.get("status", "")).lower() for lock in locks if lock.get("type") == "OVERALL_LOCK"),
        None,
    )


async def _async_poll_lock_status(coordinator, expect_locked: bool, vehicle_name: str = "Nissan") -> None:
    """Refresh a few times until the lock status matches the requested state."""
    for attempt in range(1, LOCK_POLL_ATTEMPTS + 1):
        await asyncio.sleep(LOCK_POLL_DELAY)
        await coordinator.async_refresh()
        status = _overall_lock_status(coordinator)
        _LOGGER.debug(
            "[%s] Lock poll %s/%s: OVERALL_LOCK=%r (expected locked=%s)",
            vehicle_name, attempt, LOCK_POLL_ATTEMPTS, status, expect_locked,
        )
        if status is not None and (status == "locked") == expect_locked:
            return


async def _async_poll_engine_request(coordinator, vehicle_name: str = "Nissan") -> None:
    """Refresh a few times until the latest engine request leaves PENDING."""
    for attempt in range(1, ENGINE_POLL_ATTEMPTS + 1):
        await asyncio.sleep(ENGINE_POLL_DELAY)
        await coordinator.async_refresh()
        requests = (coordinator.data or {}).get("engines", {}).get("svcRequests", [])
        latest = max(requests, key=lambda item: item.get("requestedTime", ""), default={})
        status = latest.get("status")
        _LOGGER.debug(
            "[%s] Engine poll %s/%s: latest request status=%r",
            vehicle_name, attempt, ENGINE_POLL_ATTEMPTS, status,
        )
        if status and status != "PENDING":
            return


async def _async_poll_climate_request(hass: HomeAssistant, api, service_request_id: str, coordinator) -> None:
    """Refresh a few times until the climate request completes."""
    label = getattr(api, "_label", "Nissan")
    for attempt in range(1, CLIMATE_POLL_ATTEMPTS + 1):
        await asyncio.sleep(CLIMATE_POLL_DELAY)
        try:
            status_data = await hass.async_add_executor_job(
                api.check_climate_request, service_request_id
            )
            status = status_data.get("status")
            _LOGGER.debug(
                "[%s] Climate poll %s/%s: status=%r, details=%s",
                label, attempt, CLIMATE_POLL_ATTEMPTS, status, status_data.get("statusDetails")
            )
            if status and status not in ("PENDING", "IN_PROGRESS"):
                await coordinator.async_refresh()
                return
        except Exception as err:
            _LOGGER.warning("[%s] Erreur lors du suivi de la requête climatisation %s: %s", label, service_request_id, err)


async def _async_poll_charge_request(hass: HomeAssistant, api, service_request_id: str, coordinator) -> None:
    """Refresh a few times until the charge request completes."""
    label = getattr(api, "_label", "Nissan")
    for attempt in range(1, CHARGE_POLL_ATTEMPTS + 1):
        await asyncio.sleep(CHARGE_POLL_DELAY)
        try:
            status_data = await hass.async_add_executor_job(
                api.check_charge_request, service_request_id
            )
            status = status_data.get("status")
            _LOGGER.debug(
                "[%s] Charge poll %s/%s: status=%r",
                label, attempt, CHARGE_POLL_ATTEMPTS, status,
            )
            if status and status not in ("PENDING", "IN_PROGRESS"):
                await coordinator.async_refresh()
                return
        except Exception as err:
            _LOGGER.warning("[%s] Erreur lors du suivi de la requête de recharge %s: %s", label, service_request_id, err)


async def _async_poll_horn_light_request(hass: HomeAssistant, api, service_request_id: str) -> None:
    """Refresh a few times until the horn/lights request completes."""
    label = getattr(api, "_label", "Nissan")
    for attempt in range(1, HORN_LIGHT_POLL_ATTEMPTS + 1):
        await asyncio.sleep(HORN_LIGHT_POLL_DELAY)
        try:
            status_data = await hass.async_add_executor_job(
                api.check_horn_light_request, service_request_id
            )
            status = status_data.get("status")
            _LOGGER.debug(
                "[%s] Horn/lights poll %s/%s: status=%r",
                label, attempt, HORN_LIGHT_POLL_ATTEMPTS, status,
            )
            if status and status not in ("PENDING", "IN_PROGRESS"):
                return
        except Exception as err:
            _LOGGER.warning("[%s] Erreur lors du suivi de la requête phares/klaxon %s: %s", label, service_request_id, err)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up the remote-start button when Nissan reports it as requested."""
    runtime = entry.runtime_data
    coordinator = runtime.coordinator
    vehicle = coordinator.data["vehicle"]
    services = vehicle.get("allVehicleServiceDetails", {}).get(
        "vehicleServiceDetails", []
    )
    remote_start_requested = any(
        service.get("vehicleServiceName") == "Remote Engine Start / Stop"
        and service.get("serviceRequested") is True
        for service in services
    )
    engines = coordinator.data["engines"].get("engines", [])
    engine = next((item for item in engines if item.get("id")), None)
    is_electric = vehicle.get("engineType") == "Electric" or bool(coordinator.data.get("battery"))
    buttons = [NissanRefreshButton(coordinator, entry)]
    if not is_electric and remote_start_requested and engine is not None:
        buttons.append(
            NissanRemoteStartButton(coordinator, entry, runtime.api, engine["id"])
        )

    # Climatisation à distance (pour véhicules électriques ou services climatisation)
    climate_requested = any(
        "climate" in (service.get("vehicleServiceName") or "").lower()
        and service.get("serviceRequested") is True
        for service in services
    )
    if is_electric or climate_requested:
        buttons.append(NissanStartClimateButton(coordinator, entry, runtime.api))
        buttons.append(NissanStopClimateButton(coordinator, entry, runtime.api))

    # Recharge à distance (pour véhicules électriques)
    start_charge_requested = any(
        "charge" in (service.get("vehicleServiceName") or "").lower()
        and service.get("serviceRequested") is True
        for service in services
    )
    if is_electric or start_charge_requested:
        buttons.append(NissanStartChargeButton(coordinator, entry, runtime.api))
        buttons.append(NissanStopChargeButton(coordinator, entry, runtime.api))

    # Phares et avertisseur sonore à distance
    horn_lights_requested = any(
        "horn" in (service.get("vehicleServiceName") or "").lower()
        and service.get("serviceRequested") is True
        for service in services
    )
    if horn_lights_requested or not services:
        buttons.append(NissanFlashLightsButton(coordinator, entry, runtime.api))
        buttons.append(NissanFlashLightsHornButton(coordinator, entry, runtime.api))

    # Note: Les boutons individuels de verrouillage / déverrouillage sont omis
    # car ils sont désormais remplacés par la véritable entité Lock (lock.py).

    async_add_entities(buttons)


class NissanRefreshButton(CoordinatorEntity, ButtonEntity):
    """Refresh read-only Nissan vehicle data on demand."""

    _attr_has_entity_name = True
    _attr_translation_key = "refresh_vehicle"
    _attr_icon = "mdi:refresh"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        """Initialize the refresh button."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_refresh_vehicle"
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

    async def async_press(self) -> None:
        """Request an immediate vehicle data refresh."""
        label = self._attr_device_info.get("name", "Nissan")
        _LOGGER.debug("[%s] Refresh button pressed", label)
        await self.coordinator.async_refresh()
        _LOGGER.debug(
            "[%s] Refresh finished, success: %s", label, self.coordinator.last_update_success
        )


class NissanRemoteStartButton(CoordinatorEntity, ButtonEntity):
    """Start the vehicle remotely using Nissan's RTS engine endpoint."""

    _attr_has_entity_name = True
    _attr_translation_key = "remote_start"
    _attr_icon = "mdi:engine"

    def __init__(self, coordinator, entry: ConfigEntry, api, engine_id: str) -> None:
        """Initialize the remote-start button."""
        super().__init__(coordinator)
        self._api = api
        self._engine_id = engine_id
        self._attr_unique_id = f"{entry.entry_id}_remote_start"
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

    @guard_command()
    async def async_press(self) -> None:
        """Request a remote start and refresh the vehicle state."""
        _LOGGER.debug("[%s] Remote start button pressed", self._api._label)
        try:
            await self.hass.async_add_executor_job(
                self._api.start_engine, self._engine_id
            )
            self.hass.async_create_task(
                _async_poll_engine_request(self.coordinator, self._api._label)
            )
        except NissanConnectAuthenticationError as err:
            raise HomeAssistantError(
                "Nissan access token expired or rejected; reauthenticate the integration"
            ) from err
        except NissanConnectCommandError as err:
            raise HomeAssistantError(f"Nissan remote start failed: {err}") from err
        except Exception as err:
            raise HomeAssistantError("Nissan remote start request failed") from err


class NissanUnlockDoorsButton(CoordinatorEntity, ButtonEntity):
    """Unlock all doors using Nissan's confirmed RTS request."""

    _attr_has_entity_name = True
    _attr_translation_key = "unlock_doors"
    _attr_icon = "mdi:lock-open-variant"

    def __init__(self, coordinator, entry: ConfigEntry, api) -> None:
        """Initialize the door-unlock button."""
        super().__init__(coordinator)
        self._api = api
        self._attr_unique_id = f"{entry.entry_id}_unlock_doors"
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

    @guard_command()
    async def async_press(self) -> None:
        """Request door unlock and refresh the vehicle state."""
        _LOGGER.debug("[%s] Unlock doors button pressed", self._api._label)
        try:
            await self.hass.async_add_executor_job(self._api.unlock_doors)
            self.hass.async_create_task(
                _async_poll_lock_status(self.coordinator, False, self._api._label)
            )
        except NissanConnectAuthenticationError as err:
            raise HomeAssistantError(
                "Nissan access token expired or rejected; reauthenticate the integration"
            ) from err
        except NissanConnectCommandError as err:
            raise HomeAssistantError(f"Nissan door unlock failed: {err}") from err
        except Exception as err:
            raise HomeAssistantError("Nissan door unlock request failed") from err


class NissanLockDoorsButton(CoordinatorEntity, ButtonEntity):
    """Lock all doors using Nissan's RTS endpoint."""

    _attr_has_entity_name = True
    _attr_translation_key = "lock_doors"
    _attr_icon = "mdi:lock"

    def __init__(self, coordinator, entry: ConfigEntry, api) -> None:
        """Initialize the door-lock button."""
        super().__init__(coordinator)
        self._api = api
        self._attr_unique_id = f"{entry.entry_id}_lock_doors"
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

    @guard_command()
    async def async_press(self) -> None:
        """Request door lock and refresh the vehicle state."""
        _LOGGER.debug("[%s] Lock doors button pressed", self._api._label)
        try:
            await self.hass.async_add_executor_job(self._api.lock_doors)
            self.hass.async_create_task(
                _async_poll_lock_status(self.coordinator, True, self._api._label)
            )
        except NissanConnectAuthenticationError as err:
            raise HomeAssistantError(
                "Nissan access token expired or rejected; reauthenticate the integration"
            ) from err
        except NissanConnectCommandError as err:
            raise HomeAssistantError(f"Nissan door lock failed: {err}") from err
        except Exception as err:
            raise HomeAssistantError("Nissan door lock request failed") from err


class NissanStartClimateButton(CoordinatorEntity, ButtonEntity):
    """Start vehicle cabin climate control remotely."""

    _attr_has_entity_name = True
    _attr_translation_key = "start_climate"
    _attr_icon = "mdi:air-conditioner"

    def __init__(self, coordinator, entry: ConfigEntry, api) -> None:
        """Initialize the climate start button."""
        super().__init__(coordinator)
        self._api = api
        self._entry = entry
        self._attr_unique_id = f"{entry.entry_id}_start_climate"
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

    @guard_command()
    async def async_press(self) -> None:
        """Request remote climate start."""
        _LOGGER.debug("[%s] Start climate button pressed", self._api._label)
        try:
            target_temp = 21.0
            for state in self.hass.states.async_all("climate"):
                if "nissan_connect_ca" in state.entity_id and (
                    str(self._entry.entry_id) in state.entity_id
                    or str(self._entry.data.get("vehicle_id")) in state.entity_id
                    or (self.coordinator.data.get("vehicle", {}).get("model", "").lower() in state.entity_id.lower())
                ):
                    temp = state.attributes.get(ATTR_TEMPERATURE)
                    if temp is not None:
                        try:
                            target_temp = float(temp)
                            break
                        except (ValueError, TypeError):
                            pass

            req_id = await self.hass.async_add_executor_job(
                self._api.start_climate, target_temp, "CELSIUS"
            )
            if req_id:
                self.hass.async_create_task(
                    _async_poll_climate_request(self.hass, self._api, req_id, self.coordinator)
                )
        except NissanConnectAuthenticationError as err:
            raise HomeAssistantError(
                "Nissan access token expired or rejected; reauthenticate the integration"
            ) from err
        except NissanConnectCommandError as err:
            raise HomeAssistantError(f"Nissan start climate failed: {err}") from err
        except Exception as err:
            raise HomeAssistantError("Nissan start climate request failed") from err


class NissanStopClimateButton(CoordinatorEntity, ButtonEntity):
    """Stop vehicle cabin climate control remotely."""

    _attr_has_entity_name = True
    _attr_translation_key = "stop_climate"
    _attr_icon = "mdi:power"

    def __init__(self, coordinator, entry: ConfigEntry, api) -> None:
        """Initialize the climate stop button."""
        super().__init__(coordinator)
        self._api = api
        self._attr_unique_id = f"{entry.entry_id}_stop_climate"
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

    @guard_command()
    async def async_press(self) -> None:
        """Request remote climate stop."""
        _LOGGER.debug("[%s] Stop climate button pressed", self._api._label)
        try:
            req_id = await self.hass.async_add_executor_job(self._api.stop_climate)
            if req_id:
                self.hass.async_create_task(
                    _async_poll_climate_request(self.hass, self._api, req_id, self.coordinator)
                )
        except NissanConnectAuthenticationError as err:
            raise HomeAssistantError(
                "Nissan access token expired or rejected; reauthenticate the integration"
            ) from err
        except NissanConnectCommandError as err:
            raise HomeAssistantError(f"Nissan stop climate failed: {err}") from err
        except Exception as err:
            raise HomeAssistantError("Nissan stop climate request failed") from err


class NissanStartChargeButton(CoordinatorEntity, ButtonEntity):
    """Start vehicle battery charging remotely."""

    _attr_has_entity_name = True
    _attr_translation_key = "start_charge"
    _attr_icon = "mdi:ev-station"

    def __init__(self, coordinator, entry: ConfigEntry, api) -> None:
        """Initialize the start charging button."""
        super().__init__(coordinator)
        self._api = api
        self._attr_unique_id = f"{entry.entry_id}_start_charge"
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

    @guard_command()
    async def async_press(self) -> None:
        """Request remote charge start."""
        _LOGGER.debug("[%s] Start charge button pressed", self._api._label)
        try:
            req_id = await self.hass.async_add_executor_job(self._api.start_charge)
            if req_id:
                self.hass.async_create_task(
                    _async_poll_charge_request(self.hass, self._api, req_id, self.coordinator)
                )
        except NissanConnectAuthenticationError as err:
            raise HomeAssistantError(
                "Nissan access token expired or rejected; reauthenticate the integration"
            ) from err
        except NissanConnectCommandError as err:
            raise HomeAssistantError(f"Nissan start charge failed: {err}") from err
        except Exception as err:
            raise HomeAssistantError("Nissan start charge request failed") from err


class NissanStopChargeButton(CoordinatorEntity, ButtonEntity):
    """Stop vehicle battery charging remotely."""

    _attr_has_entity_name = True
    _attr_translation_key = "stop_charge"
    _attr_icon = "mdi:battery-charging-off"

    def __init__(self, coordinator, entry: ConfigEntry, api) -> None:
        """Initialize the stop charging button."""
        super().__init__(coordinator)
        self._api = api
        self._attr_unique_id = f"{entry.entry_id}_stop_charge"
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

    @guard_command()
    async def async_press(self) -> None:
        """Request remote charge stop."""
        _LOGGER.debug("[%s] Stop charge button pressed", self._api._label)
        try:
            req_id = await self.hass.async_add_executor_job(self._api.stop_charge)
            if req_id:
                self.hass.async_create_task(
                    _async_poll_charge_request(self.hass, self._api, req_id, self.coordinator)
                )
        except NissanConnectAuthenticationError as err:
            raise HomeAssistantError(
                "Nissan access token expired or rejected; reauthenticate the integration"
            ) from err
        except NissanConnectCommandError as err:
            raise HomeAssistantError(f"Nissan stop charge failed: {err}") from err
        except Exception as err:
            raise HomeAssistantError("Nissan stop charge request failed") from err


class NissanFlashLightsButton(CoordinatorEntity, ButtonEntity):
    """Flash vehicle exterior lights remotely."""

    _attr_has_entity_name = True
    _attr_translation_key = "flash_lights"
    _attr_icon = "mdi:car-light-high"

    def __init__(self, coordinator, entry: ConfigEntry, api) -> None:
        """Initialize the flash lights button."""
        super().__init__(coordinator)
        self._api = api
        self._attr_unique_id = f"{entry.entry_id}_flash_lights"
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

    @guard_command()
    async def async_press(self) -> None:
        """Request remote flash lights."""
        _LOGGER.debug("[%s] Flash lights button pressed", self._api._label)
        try:
            req_id = await self.hass.async_add_executor_job(self._api.flash_lights)
            if req_id:
                self.hass.async_create_task(
                    _async_poll_horn_light_request(self.hass, self._api, req_id)
                )
        except NissanConnectAuthenticationError as err:
            raise HomeAssistantError(
                "Nissan access token expired or rejected; reauthenticate the integration"
            ) from err
        except NissanConnectCommandError as err:
            raise HomeAssistantError(f"Nissan flash lights failed: {err}") from err
        except Exception as err:
            raise HomeAssistantError("Nissan flash lights request failed") from err


class NissanFlashLightsHornButton(CoordinatorEntity, ButtonEntity):
    """Honk horn and flash lights remotely."""

    _attr_has_entity_name = True
    _attr_translation_key = "flash_lights_horn"
    _attr_icon = "mdi:bullhorn"

    def __init__(self, coordinator, entry: ConfigEntry, api) -> None:
        """Initialize the honk horn and flash lights button."""
        super().__init__(coordinator)
        self._api = api
        self._attr_unique_id = f"{entry.entry_id}_flash_lights_horn"
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

    @guard_command()
    async def async_press(self) -> None:
        """Request remote honk horn and flash lights."""
        _LOGGER.debug("[%s] Honk horn and flash lights button pressed", self._api._label)
        try:
            req_id = await self.hass.async_add_executor_job(self._api.flash_lights_horn)
            if req_id:
                self.hass.async_create_task(
                    _async_poll_horn_light_request(self.hass, self._api, req_id)
                )
        except NissanConnectAuthenticationError as err:
            raise HomeAssistantError(
                "Nissan access token expired or rejected; reauthenticate the integration"
            ) from err
        except NissanConnectCommandError as err:
            raise HomeAssistantError(f"Nissan horn and lights failed: {err}") from err
        except Exception as err:
            raise HomeAssistantError("Nissan horn and lights request failed") from err

