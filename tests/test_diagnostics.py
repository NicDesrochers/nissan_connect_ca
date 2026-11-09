"""Tests de l'expurgation des diagnostics."""
from types import SimpleNamespace

from homeassistant.components.diagnostics import async_redact_data

from custom_components.nissan_connect_ca.diagnostics import (
    TO_REDACT,
    async_get_config_entry_diagnostics,
)


def test_sensitive_fields_are_redacted():
    data = {
        "vin": "JN1AZ2EBXTM303707",
        "location": {"latitude": 45.5, "longitude": -73.5},
        "access_token": "tok",
        "vehicleNickname": "Ma Rogue",
        "battery": {"level": 66},
    }
    result = async_redact_data(data, TO_REDACT)
    assert result["vin"] == "**REDACTED**"
    assert result["location"]["latitude"] == "**REDACTED**"
    assert result["location"]["longitude"] == "**REDACTED**"
    assert result["access_token"] == "**REDACTED**"
    assert result["vehicleNickname"] == "**REDACTED**"
    assert result["battery"]["level"] == 66


async def test_diagnostics_redacts_entry_title_and_nested_vehicle_nickname():
    entry = SimpleNamespace(
        title="Nissan Connect CA (private nickname)",
        data={"vin": "", "vehicle_id": "vehicle-id"},
        options={},
        runtime_data=SimpleNamespace(
            coordinator=SimpleNamespace(
                data={"vehicle": {"refProperties": {"vehicleNickname": "private nickname"}}}
            )
        ),
    )

    result = await async_get_config_entry_diagnostics(None, entry)

    assert result["title"] == "**REDACTED**"
    assert result["vehicle_id"] == "**REDACTED**"
    assert result["data"]["vehicle"]["refProperties"]["vehicleNickname"] == "**REDACTED**"
