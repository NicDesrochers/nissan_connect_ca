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
    UnitOfEnergy,
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
GRAPHQL_TYRE_FIELDS = {
    "FRONT_LEFT": ("flPressure", "flStatus"),
    "FRONT_RIGHT": ("frPressure", "frStatus"),
    "REAR_LEFT": ("rlPressure", "rlStatus"),
    "REAR_RIGHT": ("rrPressure", "rrStatus"),
}


def _tyre_data(snapshot, position):
    """Prefer GraphQL PSI values and fall back to legacy RTS measurements."""
    pressure_field, status_field = GRAPHQL_TYRE_FIELDS[position]
    pressure = snapshot.get("tirePressure") or {}
    if pressure.get(pressure_field) is not None:
        return {
            "value": pressure[pressure_field],
            "status": pressure.get(status_field),
            "UoM": "PSI",
            "lastUpdatedAt": pressure.get("lastUpdatedAt"),
        }
    return next(
        (tyre for tyre in snapshot.get("health", {}).get("tyres", [])
         if tyre.get("position") == position and tyre.get("value") is not None),
        None,
    )


def _latest_charge_summary(snapshot):
    summaries = (snapshot.get("extra", {}).get("chargeHistory") or {}).get("chargeSummaries") or []
    summaries = [item for item in summaries if item.get("year") and item.get("month")]
    return max(summaries, key=lambda item: (item["year"], item["month"]), default=None)


def _count_schedules(kind):
    def getter(snapshot):
        schedules = snapshot.get("extra", {}).get(kind)
        if schedules is None:
            return None
        return sum(1 for item in schedules if item.get("state") == "ON")
    return getter


def _schedule_attributes(kind):
    def getter(snapshot):
        return {"schedules": [
            {k: item.get(k) for k in ("id", "state", "startDateTime", "duration", "weekDays")
             if item.get(k) is not None}
            for item in snapshot.get("extra", {}).get(kind) or []
        ]}
    return getter


def _summary_value(field):
    def getter(snapshot):
        summary = _latest_charge_summary(snapshot)
        return summary.get(field) if summary else None
    return getter


def _summary_attributes(snapshot):
    summary = _latest_charge_summary(snapshot)
    return {"month": summary["month"], "year": summary["year"]} if summary else None


def _alert_count(field):
    def getter(snapshot):
        alerts = snapshot.get("extra", {}).get("alerts")
        if alerts is None or field not in alerts:
            return None
        value = alerts[field]
        if field == "valetAlert":
            return 1 if value else 0
        return sum(1 for item in value or [] if item.get("enabled"))
    return getter


def _v2l(field):
    return lambda snapshot: (snapshot.get("extra", {}).get("v2lStatus") or {}).get(field)


def _has_schedules(kind):
    return lambda snapshot: bool(snapshot.get("extra", {}).get(kind))


# key: (value getter, availability getter, device class, state class, unit, attributes getter, icon)
EXTRA_SENSORS = {
    "charge_schedules_enabled": (_count_schedules("chargeSchedules"), _has_schedules("chargeSchedules"), None, None, None, _schedule_attributes("chargeSchedules"), "mdi:battery-clock"),
    "climate_schedules_enabled": (_count_schedules("climateSchedules"), _has_schedules("climateSchedules"), None, None, None, _schedule_attributes("climateSchedules"), "mdi:calendar-clock"),
    "charge_sessions": (_summary_value("numberOfChargeSessions"), lambda s: _latest_charge_summary(s) is not None, None, SensorStateClass.MEASUREMENT, None, _summary_attributes, "mdi:ev-station"),
    "charge_energy": (_summary_value("totalEnergyRecovered"), lambda s: _latest_charge_summary(s) is not None, SensorDeviceClass.ENERGY, None, UnitOfEnergy.KILO_WATT_HOUR, _summary_attributes, None),
    "charge_duration": (_summary_value("totalDuration"), lambda s: _latest_charge_summary(s) is not None, SensorDeviceClass.DURATION, SensorStateClass.MEASUREMENT, UnitOfTime.MINUTES, _summary_attributes, None),
    "v2l_state": (_v2l("state"), lambda s: _v2l("state")(s) is not None, None, None, None, None, "mdi:power-plug-battery"),
    "v2l_minimum_charge_level": (_v2l("chargeLimitationLevel"), lambda s: _v2l("chargeLimitationLevel")(s) is not None, SensorDeviceClass.BATTERY, SensorStateClass.MEASUREMENT, PERCENTAGE, None, None),
    "boundary_alerts": (_alert_count("boundaryAlerts"), lambda s: "boundaryAlerts" in (s.get("extra", {}).get("alerts") or {}), None, SensorStateClass.MEASUREMENT, None, None, "mdi:map-marker-radius"),
    "curfew_alerts": (_alert_count("curfewAlerts"), lambda s: "curfewAlerts" in (s.get("extra", {}).get("alerts") or {}), None, SensorStateClass.MEASUREMENT, None, None, "mdi:clock-alert"),
    "speed_alerts": (_alert_count("speedAlerts"), lambda s: "speedAlerts" in (s.get("extra", {}).get("alerts") or {}), None, SensorStateClass.MEASUREMENT, None, None, "mdi:speedometer"),
    "valet_alert": (_alert_count("valetAlert"), lambda s: "valetAlert" in (s.get("extra", {}).get("alerts") or {}), None, None, None, None, "mdi:account-key"),
}
DIAGNOSTIC_EXTRA = {"charge_schedules_enabled", "climate_schedules_enabled", "v2l_state", "v2l_minimum_charge_level", "boundary_alerts", "curfew_alerts", "speed_alerts", "valet_alert"}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities,
) -> None:
    """Set up Nissan vehicle sensors."""
    coordinator = entry.runtime_data.coordinator
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

    tire_keys = {
        key for position, key in TYRE_POSITION_KEYS.items()
        if _tyre_data(coordinator.data or {}, position) is not None
    }
    keys.extend(tire_keys)
    async_add_entities(
        [NissanVehicleSensor(coordinator, entry, key) for key in keys]
    )
    extra_keys = set()

    def async_add_extra():
        data = coordinator.data or {}
        new = [k for k, spec in EXTRA_SENSORS.items() if k not in extra_keys and spec[1](data)]
        if new:
            extra_keys.update(new)
            async_add_entities([NissanExtraSensor(coordinator, entry, key) for key in new])

    async_add_extra()
    entry.async_on_unload(coordinator.async_add_listener(async_add_extra))

    def async_add_new_tires():
        new_keys = [
            key for position, key in TYRE_POSITION_KEYS.items()
            if key not in tire_keys and _tyre_data(coordinator.data or {}, position) is not None
        ]
        if new_keys:
            tire_keys.update(new_keys)
            async_add_entities([NissanVehicleSensor(coordinator, entry, key) for key in new_keys])

    entry.async_on_unload(coordinator.async_add_listener(async_add_new_tires))


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
            tyre = _tyre_data(snapshot, position)
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
            tyre = _tyre_data(snapshot, position)
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
        tyre = _tyre_data(self.coordinator.data or {}, position)
        return {"status": tyre.get("status")} if tyre else None


class NissanExtraSensor(NissanVehicleSensor):
    """Sensors backed by the optional GraphQL data."""

    def __init__(self, coordinator, entry: ConfigEntry, key: str) -> None:
        super().__init__(coordinator, entry, key)
        _, _, device_class, state_class, unit, _, icon = EXTRA_SENSORS[key]
        self._attr_device_class = device_class
        self._attr_state_class = state_class
        self._attr_native_unit_of_measurement = unit
        self._attr_icon = icon
        if key in DIAGNOSTIC_EXTRA:
            self._attr_entity_category = EntityCategory.DIAGNOSTIC

    @property
    def native_value(self):
        return EXTRA_SENSORS[self._key][0](self.coordinator.data or {})

    @property
    def native_unit_of_measurement(self):
        return self._attr_native_unit_of_measurement

    @property
    def extra_state_attributes(self):
        getter = EXTRA_SENSORS[self._key][5]
        return getter(self.coordinator.data or {}) if getter else None
