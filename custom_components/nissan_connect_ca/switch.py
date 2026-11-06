"""Switches for Nissan charge and climate schedules."""

import logging

import requests
from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .api import NissanConnectAuthenticationError, NissanConnectCommandError
from .command_guard import guard_command
from .const import DOMAIN

PARALLEL_UPDATES = 1

_LOGGER = logging.getLogger(__name__)

KINDS = {"chargeSchedules": "charge", "climateSchedules": "climate"}


def _schedules(data, key):
    return (data or {}).get("extra", {}).get(key) or []


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, async_add_entities) -> None:
    """Create one switch per schedule reported by Nissan."""
    runtime = entry.runtime_data
    coordinator = runtime.coordinator
    known: set[str] = set()

    def async_add_schedules():
        new = []
        for key, kind in KINDS.items():
            for schedule in _schedules(coordinator.data, key):
                uid = f"{entry.entry_id}_{kind}_schedule_{schedule.get('id')}"
                if schedule.get("id") and uid not in known:
                    known.add(uid)
                    new.append(NissanScheduleSwitch(coordinator, entry, runtime.api, key, kind, schedule["id"], uid))
        if new:
            async_add_entities(new)

    async_add_schedules()
    entry.async_on_unload(coordinator.async_add_listener(async_add_schedules))


class NissanScheduleSwitch(CoordinatorEntity, SwitchEntity):
    """Enable or disable a charge or climate schedule."""

    _attr_has_entity_name = True
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry, api, key, kind, schedule_id, unique_id) -> None:
        super().__init__(coordinator)
        self._api = api
        self._key = key
        self._kind = kind
        self._schedule_id = schedule_id
        self._attr_unique_id = unique_id
        self._attr_translation_key = f"{kind}_schedule"
        self._attr_icon = "mdi:battery-clock" if kind == "charge" else "mdi:calendar-clock"
        ids = [s.get("id") for s in _schedules(coordinator.data, key)]
        self._attr_translation_placeholders = {
            "number": str(ids.index(schedule_id) + 1) if schedule_id in ids else schedule_id
        }
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, str(entry.data["vehicle_id"]))})

    @property
    def _schedule(self):
        return next((s for s in _schedules(self.coordinator.data, self._key) if s.get("id") == self._schedule_id), None)

    @property
    def available(self):
        return super().available and self._schedule is not None

    @property
    def is_on(self):
        schedule = self._schedule
        return None if schedule is None else schedule.get("state") == "ON"

    @property
    def extra_state_attributes(self):
        schedule = self._schedule or {}
        return {k: schedule[k] for k in ("startDateTime", "duration", "weekDays", "temperature") if schedule.get(k) is not None}

    async def _async_toggle(self, enable: bool) -> None:
        try:
            await self.hass.async_add_executor_job(self._api.toggle_schedule, self._kind, self._schedule_id, enable)
        except NissanConnectAuthenticationError as err:
            raise HomeAssistantError("Nissan access token expired or rejected; reauthenticate the integration") from err
        except (NissanConnectCommandError, requests.RequestException) as err:
            raise HomeAssistantError(f"Nissan schedule update failed: {err}") from err
        await self.coordinator.async_request_refresh()

    @guard_command(cooldown=2.0)
    async def async_turn_on(self, **kwargs) -> None:
        await self._async_toggle(True)

    @guard_command(cooldown=2.0)
    async def async_turn_off(self, **kwargs) -> None:
        await self._async_toggle(False)
