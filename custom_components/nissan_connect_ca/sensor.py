"""Sensors for Nissan Connect Canada."""

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    PERCENTAGE,
    EntityCategory,
    UnitOfLength,
    UnitOfPressure,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
import homeassistant.util.dt as dt_util

from .const import DOMAIN

TYRE_POSITION_KEYS = {
    "FRONT_LEFT": "tyre_pressure_front_left",
    "FRONT_RIGHT": "tyre_pressure_front_right",
    "REAR_LEFT": "tyre_pressure_rear_left",
    "REAR_RIGHT": "tyre_pressure_rear_right",
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up Nissan vehicle sensors."""
    coordinator = hass.data[DOMAIN][entry.entry_id]["coordinator"]
    vehicle = coordinator.data.get("vehicle", {})
    is_electric = vehicle.get("engineType") == "Electric" or bool(coordinator.data.get("battery"))

    keys = ["odometer"]
    if not is_electric:
        keys.extend(("oil_level", "engine_state"))
    else:
        # Capteurs spécifiques aux véhicules électriques
        keys.extend(("battery_level", "remaining_range", "remaining_charge_time"))
        if coordinator.data.get("climate", {}).get("temperature", {}).get("value") is not None:
            keys.append("interior_temperature")

    # Capteurs de diagnostic
    if (coordinator.data or {}).get("health", {}).get("lastUpdatedTime"):
        keys.append("last_updated")

    due_mileage = (
        (coordinator.data or {})
        .get("health", {})
        .get("mileage", {})
        .get("dueMileage", {})
        .get("value")
    )
    if due_mileage is not None:
        keys.append("due_mileage")

    keys.extend(
        key
        for tyre in coordinator.data.get("health", {}).get("tyres", [])
        if (key := TYRE_POSITION_KEYS.get(tyre.get("position")))
        and tyre.get("value") is not None
    )
    async_add_entities(
        [NissanVehicleSensor(coordinator, entry, key) for key in keys]
    )


class NissanVehicleSensor(CoordinatorEntity, SensorEntity):
    """Expose vehicle metadata and read-only telemetry values."""

    _attr_has_entity_name = True

    def __init__(self, coordinator, entry: ConfigEntry, key: str) -> None:
        """Initialize a Nissan vehicle sensor."""
        super().__init__(coordinator)
        self._key = key
        self._attr_unique_id = f"{entry.entry_id}_{key}"
        self._attr_translation_key = key
        vehicle = coordinator.data.get("vehicle", {})
        model = vehicle.get("model")
        year = vehicle.get("year")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(entry.data["vehicle_id"]))},
            name=entry.title,
            manufacturer="Nissan",
            model=f"{year} {model}" if year and model else model,
            model_id=vehicle.get("modelCode"),
        )
        if key == "odometer":
            self._attr_device_class = SensorDeviceClass.DISTANCE
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        elif key in TYRE_POSITION_KEYS.values():
            self._attr_device_class = SensorDeviceClass.PRESSURE
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif key == "battery_level":
            self._attr_device_class = SensorDeviceClass.BATTERY
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_native_unit_of_measurement = PERCENTAGE
        elif key == "remaining_range":
            self._attr_device_class = SensorDeviceClass.DISTANCE
            self._attr_state_class = SensorStateClass.MEASUREMENT
        elif key == "remaining_charge_time":
            self._attr_device_class = SensorDeviceClass.DURATION
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_native_unit_of_measurement = UnitOfTime.MINUTES
            self._attr_icon = "mdi:battery-clock"
        elif key == "interior_temperature":
            self._attr_device_class = SensorDeviceClass.TEMPERATURE
            self._attr_state_class = SensorStateClass.MEASUREMENT
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
        elif key == "last_updated":
            self._attr_device_class = SensorDeviceClass.TIMESTAMP
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
        elif key == "due_mileage":
            self._attr_device_class = SensorDeviceClass.DISTANCE
            self._attr_entity_category = EntityCategory.DIAGNOSTIC
            self._attr_icon = "mdi:wrench-clock"

    @property
    def native_value(self):
        """Return the current value for this sensor."""
        snapshot = self.coordinator.data or {}
        if self._key == "engine_state":
            engines = snapshot.get("engines", {}).get("engines", [])
            return engines[0].get("lastKnownState") if engines else None
        if self._key == "oil_level":
            return snapshot.get("health", {}).get("oilLevel")
        if self._key == "battery_level":
            return snapshot.get("battery", {}).get("level")
        if self._key == "remaining_range":
            mileage = snapshot.get("battery", {}).get("remainingMileage", {})
            return mileage.get("value")
        if self._key == "remaining_charge_time":
            return snapshot.get("battery", {}).get("remainingChargeTime")
        if self._key == "interior_temperature":
            temp = snapshot.get("climate", {}).get("temperature", {})
            return temp.get("value")
        if self._key == "last_updated":
            timestamp_str = snapshot.get("health", {}).get("lastUpdatedTime")
            if timestamp_str:
                return dt_util.parse_datetime(timestamp_str)
            return None
        if self._key == "due_mileage":
            due_val = snapshot.get("health", {}).get("mileage", {}).get("dueMileage", {}).get("value")
            try:
                return float(due_val) if due_val is not None else None
            except (TypeError, ValueError):
                return None
        if self._key in TYRE_POSITION_KEYS.values():
            position = next(
                pos
                for pos, key in TYRE_POSITION_KEYS.items()
                if key == self._key
            )
            tyre = next(
                (
                    item
                    for item in snapshot.get("health", {}).get("tyres", [])
                    if item.get("position") == position
                ),
                None,
            )
            if tyre is None:
                return None
            try:
                return float(tyre.get("value"))
            except (TypeError, ValueError):
                return None

        odometer = snapshot.get("health", {}).get("odometer", {})
        value = odometer.get("value")
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @property
    def native_unit_of_measurement(self):
        """Return the unit reported by Nissan."""
        snapshot = self.coordinator.data or {}
        if self._key == "odometer":
            unit = snapshot.get("health", {}).get("odometer", {}).get("UoM")
            return {"KM": UnitOfLength.KILOMETERS, "MI": UnitOfLength.MILES}.get(unit)
        if self._key == "remaining_range":
            unit = snapshot.get("battery", {}).get("remainingMileage", {}).get("unit")
            return UnitOfLength.MILES if unit == "MILE" else UnitOfLength.KILOMETERS
        if self._key == "battery_level":
            return PERCENTAGE
        if self._key == "remaining_charge_time":
            return UnitOfTime.MINUTES
        if self._key == "interior_temperature":
            return UnitOfTemperature.CELSIUS
        if self._key == "due_mileage":
            uom = snapshot.get("health", {}).get("mileage", {}).get("dueMileage", {}).get("UoM")
            return UnitOfLength.MILES if uom == "MI" else UnitOfLength.KILOMETERS
        if self._key in TYRE_POSITION_KEYS.values():
            position = next(
                pos
                for pos, key in TYRE_POSITION_KEYS.items()
                if key == self._key
            )
            tyre = next(
                (
                    item
                    for item in snapshot.get("health", {}).get("tyres", [])
                    if item.get("position") == position
                ),
                None,
            )
            if tyre is None:
                return None
            return {
                "PSI": UnitOfPressure.PSI,
                "KPA": UnitOfPressure.KPA,
                "BAR": UnitOfPressure.BAR,
            }.get(tyre.get("UoM"))
        return None

    @property
    def extra_state_attributes(self):
        """Expose Nissan's raw tire status code without assigning semantics."""
        if self._key not in TYRE_POSITION_KEYS.values():
            return None
        position = next(
            pos
            for pos, key in TYRE_POSITION_KEYS.items()
            if key == self._key
        )
        tyre = next(
            (
                item
                for item in (self.coordinator.data or {}).get("health", {}).get("tyres", [])
                if item.get("position") == position
            ),
            None,
        )
        return {"status": tyre.get("status")} if tyre else None
