"""Tests de l'expurgation des diagnostics."""
from homeassistant.components.diagnostics import async_redact_data

from custom_components.nissan_connect_ca.diagnostics import TO_REDACT


def test_sensitive_fields_are_redacted():
    data = {
        "vin": "JN1AZ2EBXTM303707",
        "location": {"latitude": 45.5, "longitude": -73.5},
        "access_token": "tok",
        "battery": {"level": 66},
    }
    result = async_redact_data(data, TO_REDACT)
    assert result["vin"] == "**REDACTED**"
    assert result["location"]["latitude"] == "**REDACTED**"
    assert result["location"]["longitude"] == "**REDACTED**"
    assert result["access_token"] == "**REDACTED**"
    assert result["battery"]["level"] == 66
