"""Diagnostics support for Nissan Connect Canada."""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import DOMAIN


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    domain_data = hass.data.get(DOMAIN, {})
    runtime = domain_data.get(entry.entry_id, {})
    coordinator = runtime.get("coordinator")
    raw_data = coordinator.data if coordinator else {}

    # Redact sensitive data (VIN partially, passwords, tokens)
    clean_data = dict(raw_data) if isinstance(raw_data, dict) else {}

    vin = entry.data.get("vin") or ""
    masked_vin = vin[:8] + "******" if len(vin) >= 8 else "***"

    return {
        "title": entry.title,
        "vin": masked_vin,
        "vehicle_id": entry.data.get("vehicle_id"),
        "options": dict(entry.options),
        "data": clean_data,
    }

