# custom_components/nissan_connect_ca/__init__.py
import logging
from dataclasses import dataclass
from functools import partial
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import NissanConnectAuthenticationError, NissanConnectCAAPI
from .const import (
    CONF_EXPIRES_AT,
    CONF_ID_TOKEN,
    CONF_PASSWORD,
    CONF_REFRESH_TOKEN,
    CONF_TOKEN,
    CONF_USERNAME,
    CONF_VEHICLE_ID,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    CONF_CHARGING_UPDATE_INTERVAL,
    DEFAULT_CHARGING_UPDATE_INTERVAL,
    CONF_CLIMATE_UPDATE_INTERVAL,
    DEFAULT_CLIMATE_UPDATE_INTERVAL,
    DOMAIN,
)
from .crypto import KEY_FILE, _decrypt

_LOGGER = logging.getLogger(__name__)


@dataclass
class NissanRuntimeData:
    """Données d'exécution d'une entrée de configuration."""

    api: NissanConnectCAAPI
    coordinator: DataUpdateCoordinator


type NissanConfigEntry = ConfigEntry[NissanRuntimeData]

PLATFORMS = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.DEVICE_TRACKER,
    Platform.BUTTON,
    Platform.LOCK,
    Platform.CLIMATE,
]


CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up the integration domain."""
    hass.data.setdefault(DOMAIN, {"accounts": {}})
    return True


async def async_setup_entry(hass: HomeAssistant, entry: NissanConfigEntry) -> bool:
    """Set up a Nissan Connect account and vehicle telemetry."""
    domain_data = hass.data.setdefault(DOMAIN, {"accounts": {}})
    accounts = domain_data.setdefault("accounts", {})

    username = (entry.data.get(CONF_USERNAME) or "").strip()
    vehicle_id = entry.data.get(CONF_VEHICLE_ID)
    vin = entry.data.get("vin")

    if not entry.unique_id and vehicle_id:
        hass.config_entries.async_update_entry(entry, unique_id=str(vehicle_id))

    # Reuse existing API session for the same account to avoid duplicate logins
    if username and username in accounts:
        _LOGGER.debug("Reusing existing Nissan API session for account %s", username)
        shared_account = accounts[username]
        parent_api = shared_account["api"]
        api = parent_api.clone_for_vehicle(vehicle_id=vehicle_id, vin=vin)
        shared_account["entries"].add(entry.entry_id)
    else:
        _LOGGER.debug("Initializing Nissan API session for account %s", username)
        api = NissanConnectCAAPI(
            username="",
            password="",
            tokens=entry.data,
            vehicle_id=vehicle_id,
            vin=vin,
        )
        if username:
            accounts[username] = {
                "api": api,
                "entries": {entry.entry_id},
            }

    key_path = hass.config.path(KEY_FILE)

    def relogin() -> None:
        """Renew tokens (executor thread) using encrypted stored credentials."""
        user = (entry.data.get(CONF_USERNAME) or "").strip()
        encrypted = entry.data.get(CONF_PASSWORD)
        if not user or not encrypted:
            raise ValueError("No stored credentials")
        password = _decrypt(key_path, encrypted)

        # Authenticate using the shared parent API if available
        target_api = accounts.get(user, {}).get("api", api)
        new_tokens = target_api.reauthenticate(user, password)

        # Synchronize tokens across all active vehicle API clients for this user
        associated_entries = accounts.get(user, {}).get("entries", {entry.entry_id})
        for entry_id in list(associated_entries):
            # Persist fresh tokens into Home Assistant config entries
            config_entry = hass.config_entries.async_get_entry(entry_id)
            runtime = getattr(config_entry, "runtime_data", None)
            if runtime is not None:
                runtime.api.set_tokens(new_tokens)
            if config_entry:
                hass.loop.call_soon_threadsafe(
                    partial(
                        hass.config_entries.async_update_entry,
                        config_entry,
                        data={**config_entry.data, **new_tokens},
                    )
                )

    api.set_relogin_handler(relogin)
    if username in accounts:
        accounts[username]["api"].set_relogin_handler(relogin)

    # Read update intervals from entry options (with defaults)
    normal_interval_hours = entry.options.get(
        CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL
    )
    charging_interval_minutes = entry.options.get(
        CONF_CHARGING_UPDATE_INTERVAL, DEFAULT_CHARGING_UPDATE_INTERVAL
    )
    climate_interval_minutes = entry.options.get(
        CONF_CLIMATE_UPDATE_INTERVAL, DEFAULT_CLIMATE_UPDATE_INTERVAL
    )

    coordinator_ref = [None]

    async def async_update_vehicle() -> dict:
        try:
            snapshot = await hass.async_add_executor_job(api.get_vehicle_snapshot)
            # Persist discovered VIN if it was missing in entry data
            if api.vin and not entry.data.get("vin"):
                hass.config_entries.async_update_entry(
                    entry,
                    data={**entry.data, "vin": api.vin},
                )

            # Adjust polling interval dynamically based on climate and charging state
            coordinator = coordinator_ref[0]
            if coordinator is not None:
                climate = snapshot.get("climate", {})
                climate_state = str(climate.get("state", "")).upper()
                is_climate_active = climate_state in ("ON", "RUNNING", "HEATING", "COOLING")

                battery = snapshot.get("battery", {})
                is_charging = battery.get("isCharging") is True

                if is_climate_active:
                    new_interval = timedelta(minutes=climate_interval_minutes)
                    if coordinator.update_interval != new_interval:
                        coordinator.update_interval = new_interval
                        _LOGGER.info(
                            "Vehicle %s climate is active (%s); polling set to %s minutes",
                            entry.title,
                            climate_state,
                            climate_interval_minutes,
                        )
                elif is_charging:
                    new_interval = timedelta(minutes=charging_interval_minutes)
                    if coordinator.update_interval != new_interval:
                        coordinator.update_interval = new_interval
                        _LOGGER.info(
                            "Vehicle %s is charging; polling set to %s minutes",
                            entry.title,
                            charging_interval_minutes,
                        )
                else:
                    new_interval = timedelta(hours=normal_interval_hours)
                    if coordinator.update_interval != new_interval:
                        coordinator.update_interval = new_interval
                        _LOGGER.debug(
                            "Vehicle %s is idle; polling set to %s hours",
                            entry.title,
                            normal_interval_hours,
                        )

            return snapshot
        except NissanConnectAuthenticationError as err:
            raise ConfigEntryAuthFailed("Nissan access token expired or rejected") from err
        except Exception as err:
            raise UpdateFailed("Unable to retrieve the Nissan vehicle") from err

    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{DOMAIN}_{entry.entry_id}",
        update_method=async_update_vehicle,
        update_interval=timedelta(hours=normal_interval_hours),
    )
    coordinator_ref[0] = coordinator
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = NissanRuntimeData(api=api, coordinator=coordinator)

    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Reload config entry when options are changed."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: NissanConfigEntry) -> bool:
    """Unload the integration platforms."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        username = (entry.data.get(CONF_USERNAME) or "").strip()
        domain_data = hass.data.get(DOMAIN, {})
        accounts = domain_data.get("accounts", {})
        if username and username in accounts:
            accounts[username]["entries"].discard(entry.entry_id)
            if not accounts[username]["entries"]:
                try:
                    accounts[username]["api"].close()
                except Exception:
                    pass
                accounts.pop(username, None)
    return unload_ok
