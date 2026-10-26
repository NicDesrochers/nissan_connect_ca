"""Binary sensors for Nissan Connect Canada."""

import logging

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
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
    """Set up Nissan binary sensors."""
    coordinator = entry.runtime_data.coordinator
    entities = [
        NissanDoorsLockedSensor(coordinator, entry),
        NissanVehicleWarningSensor(coordinator, entry),
        NissanBattery12vSensor(coordinator, entry),
        NissanHoodSensor(coordinator, entry),
        NissanTrunkSensor(coordinator, entry),
    ]

    battery = (coordinator.data or {}).get("battery")
    if battery:
        entities.append(NissanChargingSensor(coordinator, entry))
        entities.append(NissanPluggedInSensor(coordinator, entry))

    async_add_entities(entities)


class NissanDoorsLockedSensor(CoordinatorEntity, BinarySensorEntity):
    """Report the overall vehicle lock status."""

    _attr_has_entity_name = True
    _attr_translation_key = "doors_locked"
    _attr_device_class = BinarySensorDeviceClass.LOCK

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        """Initialize the lock status entity."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_doors_locked"
        vehicle = (coordinator.data or {}).get("vehicle", {})
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
    def is_on(self):
        """Return true when unlocked (HA LOCK device class: on = unlocked)."""
        locks = (self.coordinator.data or {}).get("health", {}).get("locks", [])
        overall_lock = next(
            (lock for lock in locks if lock.get("type") == "OVERALL_LOCK"),
            None,
        )
        label = self._attr_device_info.get("name", "Nissan")
        if overall_lock is None:
            _LOGGER.debug("[%s] No OVERALL_LOCK entry in locks: %s", label, locks)
            return None
        status = str(overall_lock.get("status", ""))
        _LOGGER.debug("[%s] Nissan OVERALL_LOCK status: %r", label, status)
        return status.lower() != "locked"


class NissanChargingSensor(CoordinatorEntity, BinarySensorEntity):
    """Report whether the vehicle is actively charging."""

    _attr_has_entity_name = True
    _attr_translation_key = "is_charging"
    _attr_device_class = BinarySensorDeviceClass.BATTERY_CHARGING

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        """Initialize the charging binary sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_is_charging"
        vehicle = (coordinator.data or {}).get("vehicle", {})
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
    def is_on(self) -> bool | None:
        """Return True if vehicle is charging."""
        battery = (self.coordinator.data or {}).get("battery", {})
        return bool(battery.get("isCharging"))


class NissanPluggedInSensor(CoordinatorEntity, BinarySensorEntity):
    """Report whether the vehicle is plugged into a charger."""

    _attr_has_entity_name = True
    _attr_translation_key = "is_plugged_in"
    _attr_device_class = BinarySensorDeviceClass.PLUG

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        """Initialize the plugged-in binary sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_is_plugged_in"
        vehicle = (coordinator.data or {}).get("vehicle", {})
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
    def is_on(self) -> bool | None:
        """Return True if vehicle is plugged in."""
        battery = (self.coordinator.data or {}).get("battery", {})
        return bool(battery.get("isPluggedIn"))


class NissanVehicleWarningSensor(CoordinatorEntity, BinarySensorEntity):
    """Report overall vehicle malfunction indicator lamps (MIL)."""

    _attr_has_entity_name = True
    _attr_translation_key = "warning_alert"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        """Initialize warning sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_warning_alert"
        vehicle = (coordinator.data or {}).get("vehicle", {})
        model = vehicle.get("model")
        year = vehicle.get("year")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(entry.data["vehicle_id"]))},
            name=entry.title,
            manufacturer="Nissan",
            model=f"{year} {model}" if year and model else model,
            model_id=vehicle.get("modelCode"),
        )

    def _get_active_mil(self):
        mil_list = (self.coordinator.data or {}).get("health", {}).get("mil", [])
        vehicle = (self.coordinator.data or {}).get("vehicle", {})
        is_electric = vehicle.get("engineType") == "Electric" or bool((self.coordinator.data or {}).get("battery"))
        active = []
        for item in mil_list:
            m_type = item.get("type", "")
            # Electric vehicles do not have an oil pressure switch
            if is_electric and m_type == "OIL_PRESSURE_SWITCH":
                continue
            status = str(item.get("status", "")).strip()
            if status not in ("0", "", "None", "false", "False"):
                active.append(item)
        return active

    @property
    def is_on(self) -> bool:
        """Return True if any warning indicator is active."""
        return len(self._get_active_mil()) > 0

    @property
    def extra_state_attributes(self):
        """Return array and details of active warnings."""
        active = self._get_active_mil()
        active_types = [item.get("type") for item in active]
        messages = [
            f"{item.get('type')}: {item.get('detailedMessage', '').strip()}"
            for item in active
            if item.get("detailedMessage")
        ]
        total_systems = len((self.coordinator.data or {}).get("health", {}).get("mil", []))
        return {
            "active_warnings": active_types,
            "warning_messages": messages,
            "total_active_warnings": len(active),
            "monitored_systems_count": total_systems,
        }


class NissanBattery12vSensor(CoordinatorEntity, BinarySensorEntity):
    """Report whether the 12V auxiliary battery has an alert."""

    _attr_has_entity_name = True
    _attr_translation_key = "battery_12v_alert"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        """Initialize 12V battery sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_battery_12v_alert"
        vehicle = (coordinator.data or {}).get("vehicle", {})
        model = vehicle.get("model")
        year = vehicle.get("year")
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, str(entry.data["vehicle_id"]))},
            name=entry.title,
            manufacturer="Nissan",
            model=f"{year} {model}" if year and model else model,
            model_id=vehicle.get("modelCode"),
        )

    def _get_12v_mil(self):
        mil_list = (self.coordinator.data or {}).get("health", {}).get("mil", [])
        return [
            item for item in mil_list
            if item.get("type") in ("BATTERY_LOW_ELECTRIC", "BATTERY_HIGH_ELECTRIC")
        ]

    @property
    def is_on(self) -> bool:
        """Return True if 12V battery problem is detected."""
        for item in self._get_12v_mil():
            status = str(item.get("status", "")).strip()
            if status not in ("0", "", "None"):
                return True
        return False

    @property
    def extra_state_attributes(self):
        """Return warning messages if 12V problem exists."""
        alerts = [
            item.get("detailedMessage", "").strip()
            for item in self._get_12v_mil()
            if str(item.get("status", "")).strip() not in ("0", "", "None")
        ]
        return {"details": alerts if alerts else "Normal"}


class NissanHoodSensor(CoordinatorEntity, BinarySensorEntity):
    """Report engine hood open/closed state."""

    _attr_has_entity_name = True
    _attr_translation_key = "hood"
    _attr_device_class = BinarySensorDeviceClass.OPENING
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        """Initialize hood sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_hood"
        vehicle = (coordinator.data or {}).get("vehicle", {})
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
    def is_on(self) -> bool | None:
        """Return True if hood is open (BinarySensor OPENING: on = open)."""
        locks = (self.coordinator.data or {}).get("health", {}).get("locks", [])
        hood = next((item for item in locks if item.get("type") == "ENGINE_HOOD"), None)
        if hood is None:
            return None
        return str(hood.get("status", "")).lower() != "closed"


class NissanTrunkSensor(CoordinatorEntity, BinarySensorEntity):
    """Report trunk / hatch open/closed state."""

    _attr_has_entity_name = True
    _attr_translation_key = "trunk"
    _attr_device_class = BinarySensorDeviceClass.DOOR
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry: ConfigEntry) -> None:
        """Initialize trunk sensor."""
        super().__init__(coordinator)
        self._attr_unique_id = f"{entry.entry_id}_trunk"
        vehicle = (coordinator.data or {}).get("vehicle", {})
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
    def is_on(self) -> bool | None:
        """Return True if trunk / hatch is open (BinarySensor DOOR: on = open)."""
        locks = (self.coordinator.data or {}).get("health", {}).get("locks", [])
        hatch = next((item for item in locks if item.get("type") in ("HATCH", "TRUNK")), None)
        if hatch is None:
            return None
        status = str(hatch.get("status", "")).lower()
        return status in ("open", "unlocked_open")
