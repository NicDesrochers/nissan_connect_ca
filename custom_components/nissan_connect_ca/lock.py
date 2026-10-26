"""Locks for Nissan Connect Canada."""

import asyncio
import logging

from homeassistant.components.lock import LockEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import NissanConnectAuthenticationError, NissanConnectCommandError
from .command_guard import guard_command
from .const import DOMAIN

PARALLEL_UPDATES = 1

_LOGGER = logging.getLogger(__name__)

LOCK_POLL_ATTEMPTS = 6
LOCK_POLL_DELAY = 5


def _overall_lock_status(coordinator):
    """Return locked/unlocked based on health locks or GraphQL doorsStatus."""
    locks = (coordinator.data or {}).get("health", {}).get("locks", [])
    overall = next(
        (str(lock.get("status", "")).lower() for lock in locks if lock.get("type") == "OVERALL_LOCK"),
        None,
    )
    if overall:
        return overall
    doors = (coordinator.data or {}).get("vehicle", {}).get("doorsStatus", {})
    lock_val = doors.get("overallLock", {}).get("lock")
    if lock_val:
        return str(lock_val).lower()
    return None


async def _async_poll_lock_status(coordinator, expect_locked: bool) -> None:
    """Refresh a few times until the lock status matches the requested state."""
    for attempt in range(1, LOCK_POLL_ATTEMPTS + 1):
        await asyncio.sleep(LOCK_POLL_DELAY)
        await coordinator.async_refresh()
        status = _overall_lock_status(coordinator)
        _LOGGER.debug(
            "Lock poll %s/%s: OVERALL_LOCK=%r (expected locked=%s)",
            attempt, LOCK_POLL_ATTEMPTS, status, expect_locked,
        )
        if status is not None and (status == "locked") == expect_locked:
            return


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up Nissan vehicle lock."""
    coordinator = entry.runtime_data.coordinator
    api = entry.runtime_data.api
    async_add_entities([NissanVehicleLock(coordinator, entry, api)])


class NissanVehicleLock(CoordinatorEntity, LockEntity):
    """Representation of Nissan vehicle door lock."""

    _attr_has_entity_name = True
    _attr_translation_key = "door_lock"

    def __init__(self, coordinator, entry: ConfigEntry, api) -> None:
        """Initialize the vehicle lock entity."""
        super().__init__(coordinator)
        self._api = api
        self._attr_unique_id = f"{entry.entry_id}_door_lock"
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

    @property
    def is_locked(self) -> bool | None:
        """Return true if vehicle is locked."""
        status = _overall_lock_status(self.coordinator)
        if status is None:
            return None
        return status == "locked"

    @guard_command()
    async def async_lock(self, **kwargs) -> None:
        """Lock the doors."""
        _LOGGER.debug("Locking vehicle doors")
        try:
            await self.hass.async_add_executor_job(self._api.lock_doors)
            self.hass.async_create_task(
                _async_poll_lock_status(self.coordinator, True)
            )
        except NissanConnectAuthenticationError as err:
            raise HomeAssistantError(
                "Nissan access token expired or rejected; reauthenticate the integration"
            ) from err
        except NissanConnectCommandError as err:
            raise HomeAssistantError(f"Nissan door lock failed: {err}") from err
        except Exception as err:
            raise HomeAssistantError("Nissan door lock request failed") from err

    @guard_command()
    async def async_unlock(self, **kwargs) -> None:
        """Unlock the doors."""
        _LOGGER.debug("Unlocking vehicle doors")
        try:
            await self.hass.async_add_executor_job(self._api.unlock_doors)
            self.hass.async_create_task(
                _async_poll_lock_status(self.coordinator, False)
            )
        except NissanConnectAuthenticationError as err:
            raise HomeAssistantError(
                "Nissan access token expired or rejected; reauthenticate the integration"
            ) from err
        except NissanConnectCommandError as err:
            raise HomeAssistantError(f"Nissan door unlock failed: {err}") from err
        except Exception as err:
            raise HomeAssistantError("Nissan door unlock request failed") from err

