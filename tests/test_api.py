"""Tests du client API (sans réseau)."""
from unittest.mock import patch

from custom_components.nissan_connect_ca.api import NissanConnectCAAPI


def _api():
    return NissanConnectCAAPI("u", "p", vehicle_id="vid", vin="VIN123")


def test_set_tokens_updates_client():
    api = _api()
    api.set_tokens({"access_token": "a", "refresh_token": "r", "id_token": "i", "expires_at": 42})
    assert api._access_token == "a"
    assert api._refresh_token == "r"
    assert api._id_token == "i"
    assert api._token_expiration_time == 42


def test_set_climate_defaults_payload():
    api = _api()
    with patch.object(api, "_post_graphql", return_value={}) as post:
        api.set_climate_defaults(23)
    name, query, variables = post.call_args.args
    assert name == "SetClimateDefaults"
    assert "setClimateDefaults" in query
    assert variables["vin"] == "VIN123"
    assert variables["climate"] == {"temperatureValue": 23.0, "unit": "CELSIUS"}
    # Nissan renvoie 400 si les paramètres d'accessoires sont absents.
    assert "parameters" in variables


def test_start_climate_does_not_set_default():
    api = _api()
    with patch.object(
        api, "_post_graphql", return_value={"startClimate": {"serviceRequestId": "abc"}}
    ) as post:
        assert api.start_climate(21) == "abc"
    assert "setAsDefault" not in post.call_args.args[2]
