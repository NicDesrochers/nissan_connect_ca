# custom_components/nissan_connect_ca/config_flow.py
import logging

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback

from .const import (
    DOMAIN,
    CONF_VEHICLE_ID,
    CONF_USER_ID,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_UPDATE_INTERVAL,
    DEFAULT_UPDATE_INTERVAL,
    CONF_CHARGING_UPDATE_INTERVAL,
    DEFAULT_CHARGING_UPDATE_INTERVAL,
    CONF_CLIMATE_UPDATE_INTERVAL,
    DEFAULT_CLIMATE_UPDATE_INTERVAL,
)
from .api import NissanConnectCAAPI
from .crypto import async_encrypt

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema({
    vol.Required(CONF_USERNAME): str,
    vol.Required(CONF_PASSWORD): str,
})


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Gère le flux de configuration pour NissanConnect CA."""
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Get the options flow handler for this entry."""
        return OptionsFlowHandler(config_entry)

    async def async_step_user(self, user_input=None):
        """Configure the Nissan Connect account."""
        errors = {}
        if user_input is not None:
            try:
                data, vehicles = await self._async_login(user_input)
            except Exception as err:
                _LOGGER.warning("Nissan login failed: %s", type(err).__name__)
                errors["base"] = "invalid_auth"
            else:
                configured_ids = set(self._async_current_ids())
                for existing_entry in self._async_current_entries():
                    vid = existing_entry.data.get(CONF_VEHICLE_ID)
                    if vid:
                        configured_ids.add(vid)

                available_vehicles = [
                    v for v in vehicles if v["vehicle_id"] not in configured_ids
                ]
                if not available_vehicles:
                    return self.async_abort(reason="already_configured")

                self._pending_data = data
                self._pending_vehicles = available_vehicles
                if len(available_vehicles) == 1:
                    return self._create_vehicle_entry(data, available_vehicles[0])
                return await self.async_step_select_vehicle()

        return self.async_show_form(
            step_id="user", data_schema=USER_SCHEMA, errors=errors
        )

    async def async_step_select_vehicle(self, user_input=None):
        """Let the user choose which account vehicle to add."""
        if user_input is not None:
            selected_id = user_input[CONF_VEHICLE_ID]
            vehicle = next(
                item
                for item in self._pending_vehicles
                if item["vehicle_id"] == selected_id
            )
            return self._create_vehicle_entry(self._pending_data, vehicle)

        choices = {
            vehicle["vehicle_id"]: vehicle["name"]
            for vehicle in self._pending_vehicles
        }
        return self.async_show_form(
            step_id="select_vehicle",
            data_schema=vol.Schema(
                {vol.Required(CONF_VEHICLE_ID): vol.In(choices)}
            ),
        )

    def _create_vehicle_entry(self, data, vehicle):
        """Create one config entry for the selected vehicle."""
        vehicle_id = vehicle["vehicle_id"]
        data[CONF_VEHICLE_ID] = vehicle_id
        if vehicle.get("vin"):
            data["vin"] = vehicle["vin"]
        self.async_set_unique_id(vehicle_id)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=f"Nissan Connect CA ({vehicle['name']})",
            data=data,
            options={
                CONF_UPDATE_INTERVAL: DEFAULT_UPDATE_INTERVAL,
                CONF_CHARGING_UPDATE_INTERVAL: DEFAULT_CHARGING_UPDATE_INTERVAL,
                CONF_CLIMATE_UPDATE_INTERVAL: DEFAULT_CLIMATE_UPDATE_INTERVAL,
            },
        )

    async def async_step_reauth(self, entry_data):
        """Ask the user to sign in again when authentication expires."""
        self.reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input=None):
        """Replace expired tokens by signing in again."""
        errors = {}
        if user_input is not None:
            try:
                data, vehicles = await self._async_login(user_input)
                vehicle_id = self.reauth_entry.data[CONF_VEHICLE_ID]
                if not any(item["vehicle_id"] == vehicle_id for item in vehicles):
                    raise ValueError("Configured vehicle is not in the Nissan account")
                data[CONF_VEHICLE_ID] = vehicle_id
                return self.async_update_reload_and_abort(
                    self.reauth_entry,
                    data=data,
                    title=self.reauth_entry.title,
                    reason="reauth_successful",
                )
            except Exception as err:
                _LOGGER.warning("Nissan reauthentication failed: %s", type(err).__name__)
                errors["base"] = "invalid_auth"
        return self.async_show_form(
            step_id="reauth_confirm", data_schema=USER_SCHEMA, errors=errors
        )

    async def _async_login(self, user_input):
        """Authenticate, load vehicles and return data with the encrypted password."""
        api = NissanConnectCAAPI(
            username=user_input[CONF_USERNAME].strip(),
            password=user_input[CONF_PASSWORD],
        )
        try:
            data = await self.hass.async_add_executor_job(api.authenticate)
            vehicles = await self.hass.async_add_executor_job(
                api.get_vehicles, data[CONF_USER_ID]
            )
            data[CONF_USERNAME] = user_input[CONF_USERNAME].strip()
            data[CONF_PASSWORD] = await async_encrypt(
                self.hass, user_input[CONF_PASSWORD]
            )
            return data, vehicles
        except Exception as err:
            status_code = getattr(getattr(err, "response", None), "status_code", None)
            _LOGGER.warning(
                "Nissan login failed at %s (%s, HTTP %s; %s)",
                api.auth_stage,
                type(err).__name__,
                status_code or "n/a",
                api.auth_diagnostic,
            )
            raise
        finally:
            await self.hass.async_add_executor_job(api.close)


class OptionsFlowHandler(config_entries.OptionsFlow):
    """Gère les options de mise à jour pour Nissan Connect CA."""

    def __init__(self, config_entry: config_entries.ConfigEntry | None = None) -> None:
        """Initialize options flow."""
        if config_entry is not None:
            self._config_entry = config_entry

    async def async_step_init(self, user_input=None):
        """Manage the options."""
        entry = getattr(self, "config_entry", None) or getattr(self, "_config_entry", None)
        options = entry.options if entry else {}

        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_UPDATE_INTERVAL,
                    default=options.get(CONF_UPDATE_INTERVAL, DEFAULT_UPDATE_INTERVAL),
                ): vol.All(vol.Coerce(int), vol.Range(min=1, max=24)),
                vol.Optional(
                    CONF_CHARGING_UPDATE_INTERVAL,
                    default=options.get(
                        CONF_CHARGING_UPDATE_INTERVAL, DEFAULT_CHARGING_UPDATE_INTERVAL
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=5, max=120)),
                vol.Optional(
                    CONF_CLIMATE_UPDATE_INTERVAL,
                    default=options.get(
                        CONF_CLIMATE_UPDATE_INTERVAL, DEFAULT_CLIMATE_UPDATE_INTERVAL
                    ),
                ): vol.All(vol.Coerce(int), vol.Range(min=2, max=60)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
