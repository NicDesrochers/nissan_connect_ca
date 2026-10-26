"""Device tracker for Nissan Connect Canada."""

import logging

from homeassistant.components.device_tracker import SourceType, TrackerEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up the Nissan vehicle tracker."""
    coordinator = entry.runtime_data.coordinator
    async_add_entities([NissanVehicleTracker(coordinator, entry)])


class NissanVehicleTracker(CoordinatorEntity, TrackerEntity):
    """Track the vehicle's last reported location."""

    _attr_has_entity_name = False
    _attr_source_type = SourceType.GPS
    _attr_icon = "mdi:car-electric"

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        """Initialize the vehicle tracker."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_vehicle_location"
        vehicle = coordinator.data["vehicle"]
        model = vehicle.get("model")
        year = vehicle.get("year")
        self._attr_name = model or entry.title
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(entry.data["vehicle_id"]))},
            name=entry.title,
            manufacturer="Nissan",
            model=f"{year} {model}" if year and model else model,
            model_id=vehicle.get("modelCode"),
        )

    @property
    def latitude(self):
        """Return the last known vehicle latitude."""
        coordinates = self._coordinates
        return coordinates.get("latitude") if coordinates else None

    @property
    def longitude(self):
        """Return the last known vehicle longitude."""
        coordinates = self._coordinates
        return coordinates.get("longitude") if coordinates else None

    @property
    def _coordinates(self):
        locations = self.coordinator.data["location"].get("locations", [])
        coordinates = locations[0].get("coordinates") if locations else None
        if not coordinates:
            _LOGGER.debug(
                "No coordinates found; location payload: %s",
                self.coordinator.data["location"],
            )
        return coordinates

    @property
    def extra_state_attributes(self):
        """Expose the last location and service-request status."""
        location = self.coordinator.data["location"]
        locations = location.get("locations", [])
        requests = location.get("svcRequests", [])
        attributes = {}
        if locations:
            attributes["location_status"] = locations[0].get("lastKnownStatus")
        if requests:
            latest_request = max(
                requests,
                key=lambda item: item.get("requestedTime", ""),
            )
            attributes["service_status"] = latest_request.get("status")
            attributes["status_changed_time"] = latest_request.get("statusChangedTime")
        return attributes