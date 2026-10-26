"""Diagnostics support for Nissan Connect Canada."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .const import DOMAIN

TO_REDACT = {
    "vin",
    "vehicleId",
    "vehicle_id",
    "id",
    "latitude",
    "longitude",
    "lat",
    "lon",
    "address",
    "access_token",
    "refresh_token",
    "id_token",
    "oauth_access_token",
    "oauth_id_token",
    "password",
    "username",
    "user_id",
    "licensePlate",
    "nickname",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    runtime = getattr(entry, "runtime_data", None)
    coordinator = runtime.coordinator if runtime else None
    raw_data = coordinator.data if coordinator else {}

    # Redact sensitive data (VIN, location, identifiers, tokens, passwords)
    clean_data = async_redact_data(raw_data if isinstance(raw_data, dict) else {}, TO_REDACT)

    vin = entry.data.get("vin") or ""
    masked_vin = vin[:8] + "******" if len(vin) >= 8 else "***"

    return {
        "title": entry.title,
        "vin": masked_vin,
        "vehicle_id": "**REDACTED**",
        "options": dict(entry.options),
        "data": clean_data,
    }

