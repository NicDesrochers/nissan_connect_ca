"""Tests du client API (sans réseau)."""
from unittest.mock import Mock, call, patch

import pytest
import requests

from custom_components.nissan_connect_ca.api import (
    NissanConnectAuthenticationError,
    NissanConnectCAAPI,
    NissanConnectCommandError,
)
from custom_components.nissan_connect_ca.const import VEHICLE_ENGINES_URL


def _api():
    return NissanConnectCAAPI("u", "p", vehicle_id="vid", vin="VIN123")


@pytest.mark.parametrize(("method", "state"), [("start_engine", "START"), ("stop_engine", "STOP")])
def test_engine_command_payload(method, state):
    api = _api()
    response = Mock(status_code=202, text="")
    with patch.object(api, "get_headers", return_value={"Authorization": "token"}), \
         patch.object(api._session, "put", return_value=response) as put:
        getattr(api, method)("engine-id")
    put.assert_called_once_with(
        VEHICLE_ENGINES_URL.format(vehicle_id="vid"),
        headers={"Authorization": "token"},
        json={"engine": {"id": "engine-id", "state": state}},
        timeout=20,
    )
    response.raise_for_status.assert_called_once()


@pytest.mark.parametrize("status, error", [
    (401, NissanConnectAuthenticationError),
    (500, NissanConnectCommandError),
    (200, NissanConnectCommandError),
])
def test_stop_engine_rejects_unsuccessful_responses(status, error):
    api = _api()
    response = Mock(status_code=status, text="")
    if status >= 400:
        response.raise_for_status.side_effect = requests.HTTPError(response=response)
    with patch.object(api, "get_headers", return_value={}), \
         patch.object(api._session, "put", return_value=response), \
         pytest.raises(error):
        api.stop_engine("engine-id")


def test_stop_engine_requires_vehicle_id():
    api = _api()
    api.vehicle_id = None
    with patch.object(api._session, "put") as put, pytest.raises(ValueError):
        api.stop_engine("engine-id")
    put.assert_not_called()


def test_set_tokens_updates_client():
    api = _api()
    api.set_tokens({"access_token": "a", "refresh_token": "r", "id_token": "i", "expires_at": 42})
    assert api._access_token == "a"
    assert api._refresh_token == "r"
    assert api._id_token == "i"
    assert api._token_expiration_time == 42


def test_set_climate_defaults_payload():
    api = _api()
    with patch.object(api, "_post_graphql", return_value={"setClimateDefaults": {"success": True}}) as post:
        api.set_climate_defaults(23)
    name, query, variables = post.call_args.args
    assert name == "SetClimateDefaults"
    assert "setClimateDefaults" in query
    assert variables["vin"] == "VIN123"
    assert variables["climate"] == {"temperatureValue": 23.0, "unit": "CELSIUS"}
    assert variables["parameters"] == {}


def test_refresh_battery_status_payload():
    api = _api()
    with patch.object(
        api, "_post_graphql", return_value={"refreshBatteryStatus": {"success": True}}
    ) as post:
        assert api.refresh_battery_status() is True
    name, query, variables = post.call_args.args
    assert name == "RefreshBatteryStatus"
    assert "refreshBatteryStatus(vin: $vin)" in query
    assert variables == {"vin": "VIN123"}


def test_start_climate_does_not_set_default():
    api = _api()
    with patch.object(
        api, "_post_graphql", return_value={"startClimate": {"serviceRequestId": "abc"}}
    ) as post:
        assert api.start_climate(21) == "abc"
    assert "setAsDefault" not in post.call_args.args[2]


def test_start_climate_sends_accessories_without_changing_defaults():
    api = _api()
    parameters = {
        "steeringWheelHeaterState": "ON",
        "defrostAndDeicerState": "ON",
        "seatsClimate": {"frontDriverState": "HEAT", "frontPassengerState": "HEAT"},
    }
    with patch.object(api, "_post_graphql", return_value={"startClimate": {"serviceRequestId": "id"}}) as post:
        assert api.start_climate(32, "CELSIUS", parameters) == "id"
    assert post.call_args.args[2]["climate"] == {"temperatureValue": 32.0, "unit": "CELSIUS"}
    assert post.call_args.args[2]["parameters"] == parameters
    assert "setAsDefault" not in post.call_args.args[2]


def test_set_climate_defaults_preserves_accessories():
    api = _api()
    parameters = {"steeringWheelHeaterState": "ON", "seatsClimate": {"frontDriverState": "HEAT"}}
    with patch.object(api, "_post_graphql", return_value={"setClimateDefaults": {"success": True}}) as post:
        api.set_climate_defaults(25, "CELSIUS", parameters)
    assert post.call_args.args[2]["parameters"] == parameters


@pytest.mark.parametrize("response", [
    {},
    {"setClimateDefaults": {"__typename": "SetClimateDefaultsError", "message": "rejected"}},
    {"setClimateDefaults": {"__typename": "SetClimateDefaultsResponse", "success": False}},
])
def test_set_climate_defaults_rejects_unsuccessful_response(response):
    api = _api()
    with patch.object(api, "_post_graphql", return_value=response), pytest.raises(NissanConnectCommandError):
        api.set_climate_defaults(18)


def test_climate_start_updates_defaults_before_starting():
    api = _api()
    defaults = {
        "climate": {"value": 18, "unit": "CELSIUS"},
        "parameters": {
            "steeringWheelHeaterState": "ON",
            "seatsClimate": {"frontDriverState": "HEAT", "rearLeftPassengerState": "HEAT",
                             "rearRightPassengerState": "HEAT"},
        },
    }
    desired = {"steeringWheelHeaterState": "ON", "seatsClimate": {
        "frontDriverState": "HEAT", "rearLeftPassengerState": "OFF", "rearRightPassengerState": "OFF",
    }}
    operations = Mock()
    operations.read.return_value = {"climateDefaults": defaults}
    operations.start.return_value = "request"
    with patch.object(api, "get_dynamic_vehicle_data", operations.read), \
         patch.object(api, "set_climate_defaults", operations.write), \
         patch.object(api, "start_climate", operations.start):
        assert api.start_climate_with_defaults(18, "CELSIUS", {
            "climate_rear_left_seat": False, "climate_rear_right_seat": False,
        }) == "request"
    assert operations.mock_calls == [
        call.read(),
        call.write(18, "CELSIUS", desired),
        call.start(18, "CELSIUS", desired),
    ]


def test_climate_start_does_not_rewrite_matching_defaults():
    api = _api()
    defaults = {"climate": {"value": 18, "unit": "CELSIUS"},
                "parameters": {"seatsClimate": {"rearLeftPassengerState": "OFF"}}}
    with patch.object(api, "get_dynamic_vehicle_data", return_value={"climateDefaults": defaults}), \
         patch.object(api, "set_climate_defaults") as write, \
         patch.object(api, "start_climate", return_value="request") as start:
        assert api.start_climate_with_defaults(18, "CELSIUS", {"climate_rear_left_seat": False}) == "request"
    write.assert_not_called()
    start.assert_called_once_with(18, "CELSIUS", {"seatsClimate": {"rearLeftPassengerState": "OFF"}})


def test_sync_climate_defaults_updates_temperature_and_preserves_live_accessories():
    api = _api()
    parameters = {"steeringWheelHeaterState": "ON", "seatsClimate": {"frontDriverState": "HEAT"}}
    defaults = {"climate": {"value": 18, "unit": "CELSIUS"}, "parameters": parameters}
    with patch.object(api, "get_dynamic_vehicle_data", return_value={"climateDefaults": defaults}), \
         patch.object(api, "set_climate_defaults") as write:
        assert api.sync_climate_defaults(22) == parameters
    write.assert_called_once_with(22, "CELSIUS", parameters)


def test_ariya_start_sends_target_without_writing_unavailable_defaults():
    api = _api()
    vehicle = {"__typename": "ElectricVehicle",
               "climateDefaults": {"climate": {"value": 19, "unit": "CELSIUS"}}}
    with patch.object(api, "get_dynamic_vehicle_data", return_value=vehicle), \
         patch.object(api, "set_climate_defaults") as write, \
         patch.object(api, "start_climate", return_value="request") as start:
        assert api.start_climate_with_defaults(23) == "request"
    write.assert_not_called()
    start.assert_called_once_with(23, "CELSIUS", None)


def test_ariya_target_change_does_not_send_empty_default_mutation():
    api = _api()
    vehicle = {"__typename": "ElectricVehicle",
               "climateDefaults": {"climate": {"value": 19, "unit": "CELSIUS"}}}
    with patch.object(api, "get_dynamic_vehicle_data", return_value=vehicle), \
         patch.object(api, "set_climate_defaults") as write:
        assert api.sync_climate_defaults(23) is None
    write.assert_not_called()


def test_modern_vehicle_empty_accessory_defaults_still_updates():
    api = _api()
    vehicle = {"__typename": "ElectricAVK2Vehicle",
               "climateDefaults": {"climate": {"value": 19, "unit": "CELSIUS"}, "parameters": {}}}
    with patch.object(api, "get_dynamic_vehicle_data", return_value=vehicle), \
         patch.object(api, "set_climate_defaults") as write:
        api.sync_climate_defaults(23)
    write.assert_called_once_with(23, "CELSIUS", {})


def test_sync_climate_defaults_applies_override_for_supported_seat_with_null_default():
    api = _api()
    defaults = {"climate": {"value": 18, "unit": "CELSIUS"},
                "parameters": {"seatsClimate": {"rearLeftPassengerState": None}}}
    capabilities = {"accessoriesDetails": {"seatHeater": {
        "enabled": True, "accessories": {"secondLeftSeat": "HEATING"},
    }}}
    with patch.object(api, "get_dynamic_vehicle_data", return_value={"climateDefaults": defaults}), \
         patch.object(api, "set_climate_defaults") as write:
        api.sync_climate_defaults(18, "CELSIUS", {"climate_rear_left_seat": False}, capabilities)
    write.assert_called_once_with(18, "CELSIUS", {"seatsClimate": {"rearLeftPassengerState": "OFF"}})


@pytest.mark.parametrize("failure", ["read", "write"])
def test_climate_start_is_not_sent_if_default_sync_fails(failure):
    api = _api()
    defaults = {"climate": {"value": 18, "unit": "CELSIUS"}, "parameters": {}}
    with patch.object(api, "get_dynamic_vehicle_data", return_value={"climateDefaults": defaults} if failure == "write" else {}), \
         patch.object(api, "set_climate_defaults", side_effect=NissanConnectCommandError("rejected")), \
         patch.object(api, "start_climate") as start, pytest.raises(NissanConnectCommandError):
        api.start_climate_with_defaults(19)
    start.assert_not_called()


@pytest.mark.parametrize("method", ["get_headers", "get_graphql_headers"])
def test_relogin_timeout_is_not_authentication_failure(method):
    api = _api()
    api.set_relogin_handler(Mock(side_effect=requests.ReadTimeout("timeout")))
    with pytest.raises(requests.ReadTimeout):
        getattr(api, method)()


def test_relogin_rejected_credentials_still_require_authentication():
    api = _api()
    api.set_relogin_handler(Mock(side_effect=NissanConnectAuthenticationError("rejected")))
    with pytest.raises(NissanConnectAuthenticationError):
        api.get_headers()


def test_dynamic_data_requests_tire_pressure_in_existing_query():
    api = _api()
    data = {"vehicle": {"tirePressure": {"flPressure": 35}}}
    with patch.object(api, "_post_graphql", return_value=data) as post:
        assert api.get_dynamic_vehicle_data() == data["vehicle"]
    query = post.call_args.args[1]
    assert "... on BaseAVKVehicle" in query
    assert "flPressure frPressure rlPressure rrPressure" in query
    assert "batteryStatus" in query
    assert post.call_count == 1


def test_dynamic_data_falls_back_if_tire_fragment_is_rejected():
    api = _api()
    with patch.object(api, "_post_graphql", side_effect=[
        NissanConnectCommandError("Nissan GraphQL error: Cannot query field tirePressure"),
        {"vehicle": {"batteryStatus": {"level": 80}}},
    ]) as post:
        assert api.get_dynamic_vehicle_data()["batteryStatus"]["level"] == 80
    assert post.call_count == 2
    assert "tirePressure" in post.call_args_list[0].args[1]
    assert "tirePressure" not in post.call_args_list[1].args[1]


def test_dynamic_data_does_not_retry_other_errors():
    api = _api()
    with patch.object(
        api, "_post_graphql", side_effect=NissanConnectCommandError("GraphQL returned HTTP 500")
    ) as post, pytest.raises(NissanConnectCommandError):
        api.get_dynamic_vehicle_data()
    assert post.call_count == 1


def test_snapshot_retains_graphql_tire_pressure():
    api = _api()
    def get_json(url, *, require_id_token=False):
        if "/health" in url:
            return {}
        if "/engines" in url:
            return {"engines": []}
        if "/locations" in url:
            return {}
        return {"vin": "VIN123", "model": "LEAF", "engineType": "Electric"}

    with patch.object(api, "_get_json", side_effect=get_json), \
         patch.object(api, "get_dynamic_vehicle_data", return_value={
             "tirePressure": {"flPressure": 35}
         }):
        assert api.get_vehicle_snapshot()["tirePressure"]["flPressure"] == 35


@pytest.mark.parametrize(("vehicle", "expect_engine"), [
    ({"engineType": "Electric", "model": "ARIYA"}, False),
    ({"engineType": "EV", "model": "LEAF"}, False),
    ({"model": "ARIYA"}, False),
    ({"model": "LEAF"}, False),
    ({"engineType": "Gasoline", "model": "Rogue"}, True),
    ({"engineType": "Hybrid", "model": "Rogue"}, True),
    ({"engineType": "Hybrid", "model": "ARIYA"}, True),
    ({"model": "Unknown"}, True),
])
def test_snapshot_fetches_engines_only_for_non_electric_or_unknown(vehicle, expect_engine):
    api = _api()
    api._last_snapshot = {"engines": {"engines": [{"id": "stale"}]}}

    def get_json(url, *, require_id_token=False):
        if "/engines" in url:
            return {"engines": [{"id": "engine"}]}
        if "/health" in url or "/locations" in url:
            return {}
        return vehicle

    with patch.object(api, "_get_json", side_effect=get_json) as get, \
         patch.object(api, "get_dynamic_vehicle_data", return_value={}) as dynamic, \
         patch.object(api, "get_extra_vehicle_data", return_value={}):
        snapshot = api.get_vehicle_snapshot()
    engine_calls = [c for c in get.call_args_list if "/engines" in c.args[0]]
    assert bool(engine_calls) is expect_engine
    assert snapshot["engines"] == ({"engines": [{"id": "engine"}]} if expect_engine else {})
    assert dynamic.called is not expect_engine


def test_refresh_vehicle_status_and_check():
    api = _api()
    with patch.object(api, "_post_graphql", side_effect=[
        {"refreshVehicleStatus": {"serviceRequestId": "request-id"}},
        {"checkRefreshVehicleStatusRequest": {"status": "SUCCESS"}},
    ]) as post:
        request_id = api.refresh_vehicle_status()
        assert api.check_refresh_vehicle_status(request_id) == {"status": "SUCCESS"}
    assert post.call_args_list[0].args[0] == "RefreshVehicleStatus"
    assert post.call_args_list[1].args[2] == {"vin": "VIN123", "serviceRequestId": "request-id"}


def test_refresh_vehicle_status_missing_request_id_is_an_error():
    api = _api()
    with patch.object(api, "_post_graphql", return_value={"refreshVehicleStatus": {}}), \
         pytest.raises(NissanConnectCommandError, match="no request ID"):
        api.refresh_vehicle_status()


def test_post_graphql_success():
    from unittest.mock import MagicMock
    api = _api()
    api.set_tokens({"access_token": "token123", "expires_at": 9999999999})
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"data": {"vehicle": {"batteryStatus": {"level": 80}}}}
    with patch.object(api._session, "post", return_value=mock_resp):
        res = api._post_graphql("VehicleDynamicData", "query { test }", {"vin": "VIN123"})
        assert res["vehicle"]["batteryStatus"]["level"] == 80



def test_concurrent_relogin_deduplicates():
    import threading, time

    parent = _api()
    parent.set_tokens({"access_token": "old_token", "expires_at": time.time() + 3600})
    child = parent.clone_for_vehicle("vid2", vin="VIN456")

    login_calls = 0

    def mock_relogin_handler():
        nonlocal login_calls
        login_calls += 1
        time.sleep(0.05)
        fresh = {"access_token": "fresh_token", "expires_at": time.time() + 3600}
        parent.set_tokens(fresh)
        child.set_tokens(fresh)

    parent.set_relogin_handler(mock_relogin_handler)
    child.set_relogin_handler(mock_relogin_handler)

    # Simule les deux véhicules qui reçoivent 401 en même temps
    t1 = threading.Thread(target=parent._relogin, kwargs={"force": True})
    t2 = threading.Thread(target=child._relogin, kwargs={"force": True})

    t1.start()
    t2.start()
    t1.join()
    t2.join()

    # Le handler ne doit avoir été appelé qu'une seule fois !
    assert login_calls == 1
    assert parent._access_token == "fresh_token"
    assert child._access_token == "fresh_token"


def test_synchronized_account_queues_operations():
    import threading, time

    parent = _api()
    child = parent.clone_for_vehicle("vid2", vin="VIN456")

    execution_order = []
    active_operations = 0
    max_concurrent = 0
    lock = threading.Lock()

    def mock_post(operation_name, query, variables=None, retry_auth=True):
        nonlocal active_operations, max_concurrent
        with lock:
            active_operations += 1
            if active_operations > max_concurrent:
                max_concurrent = active_operations
            execution_order.append(f"start_{variables.get('vin')}")
        time.sleep(0.05)
        with lock:
            active_operations -= 1
            execution_order.append(f"end_{variables.get('vin')}")
        return {"startClimate": {"serviceRequestId": "req-123"}}

    with patch.object(parent, "_post_graphql", side_effect=mock_post), \
         patch.object(child, "_post_graphql", side_effect=mock_post):
        t1 = threading.Thread(target=parent.start_climate, args=(21.0,))
        t2 = threading.Thread(target=child.start_climate, args=(22.0,))

        t1.start()
        t2.start()
        t1.join()
        t2.join()

    # Grâce à @synchronized_account et au verrou partagé, jamais plus de 1 opération concurrente
    assert max_concurrent == 1
    assert len(execution_order) == 4


def test_get_vehicle_snapshot_health_500_resilience():
    import requests
    api = _api()
    api.set_tokens({"access_token": "a", "id_token": "i", "expires_at": 9999999999})

    def mock_get_json(url, *, require_id_token=False):
        if "/health" in url:
            return {"batteryLevel": 85, "mil": []}
        if "/engines" in url:
            return {"engines": []}
        if "/location" in url:
            return {"latitude": 45.0, "longitude": -73.0}
        return {"vin": "VIN123", "model": "LEAF", "engineType": "Electric"}

    with patch.object(api, "_get_json", side_effect=mock_get_json), \
         patch.object(api, "get_dynamic_vehicle_data", return_value={"batteryStatus": {"level": 85}}):
        snap1 = api.get_vehicle_snapshot()
        assert snap1["health"]["batteryLevel"] == 85

    def mock_get_json_fail_health(url, *, require_id_token=False):
        if "/health" in url:
            mock_response = requests.Response()
            mock_response.status_code = 500
            raise requests.exceptions.HTTPError("500 Server Error", response=mock_response)
        if "/engines" in url:
            return {"engines": []}
        if "/location" in url:
            return {"latitude": 45.0, "longitude": -73.0}
        return {"vin": "VIN123", "model": "LEAF", "engineType": "Electric"}

    with patch.object(api, "_get_json", side_effect=mock_get_json_fail_health), \
         patch.object(api, "get_dynamic_vehicle_data", return_value={"batteryStatus": {"level": 84}}):
        snap2 = api.get_vehicle_snapshot()
        # Les données health précédentes sont conservées
        assert snap2["health"]["batteryLevel"] == 85
        # Les données dynamiques ont quand même été rafraîchies
        assert snap2["battery"]["level"] == 84


def test_toggle_schedule_and_charge_limit_use_graphql_inputs():
    api = _api()
    with patch.object(api, "_post_graphql", return_value={"toggleClimateSchedule": {"serviceRequestId": "r1"}}) as post:
        assert api.toggle_schedule("climate", "7", True) == "r1"
    name, query, variables = post.call_args.args
    assert name == "ToggleClimateSchedule"
    assert "ToggleClimateScheduleInput" in query
    assert variables["schedule"] == {"id": "7", "enable": True}
    with patch.object(api, "_post_graphql", return_value={"setChargeLimit": {"serviceRequestId": "r2"}}) as post:
        assert api.set_charge_limit(80) == "r2"
    assert post.call_args.args[2]["percent"] == 80


def test_v2l_and_configuration_tracking_graphql_inputs():
    api = _api()
    with patch.object(api, "_post_graphql", return_value={"setV2L": {"serviceRequestId": "id"}}) as post:
        assert api.set_v2l_minimum_charge_level(95) == "id"
    assert post.call_args.args[2]["input"] == {"minimumBatteryChargeLevel": 95}
    for kind, name, field in (
        ("v2l", "CheckV2LServiceRequest", "checkV2LServiceRequest"),
        ("charge", "CheckChargeConfigServiceRequest", "checkChargeConfigServiceRequest"),
    ):
        with patch.object(api, "_post_graphql", return_value={field: {"status": "SUCCESS"}}) as post:
            assert api.check_configuration_request(kind, "id") == {"status": "SUCCESS"}
        assert post.call_args.args[0] == name
        assert post.call_args.args[2]["serviceRequestId"] == "id"


@pytest.mark.parametrize("value", [9, 11, 96, 100, 12.5, True])
def test_v2l_rejects_out_of_range_or_invalid_increment(value):
    api = _api()
    with patch.object(api, "_post_graphql") as post, pytest.raises(ValueError):
        api.set_v2l_minimum_charge_level(value)
    post.assert_not_called()


def test_extra_vehicle_data_tolerates_failing_queries():
    api = _api()

    def fake(name, query, variables):
        if name == "V2lStatus":
            raise NissanConnectCommandError("boom")
        if name == "ChargeConfig":
            return {"vehicle": {"chargeConfig": {"limits": {"charge": {"percent": 80}}}}}
        return {"vehicle": {}}

    with patch.object(api, "_post_graphql", side_effect=fake):
        extra = api.get_extra_vehicle_data()
    assert extra == {"chargeConfig": {"limits": {"charge": {"percent": 80}}}}


def test_v2l_fragment_absent_is_probed_once_per_client():
    api = _api()

    def fake(name, query, variables):
        return {"vehicle": {"__typename": "ElectricVehicle"}}

    with patch.object(api, "_post_graphql", side_effect=fake) as post:
        api.get_extra_vehicle_data()
        api.get_extra_vehicle_data()
    assert [c.args[0] for c in post.call_args_list].count("V2lStatus") == 1


@pytest.mark.parametrize("first", [
    {"vehicle": {"__typename": "ElectricAVK2Vehicle", "v2lStatus": None}},
    {"vehicle": {}},
    NissanConnectCommandError("temporary failure"),
])
def test_v2l_is_retried_after_null_missing_vehicle_or_error(first):
    api = _api()
    responses = iter([first, {"vehicle": {"__typename": "ElectricAVK2Vehicle",
                                          "v2lStatus": {"state": "OFF"}}}])

    def fake(name, query, variables):
        if name != "V2lStatus":
            return {"vehicle": {}}
        result = next(responses)
        if isinstance(result, Exception):
            raise result
        return result

    with patch.object(api, "_post_graphql", side_effect=fake):
        assert "v2lStatus" not in api.get_extra_vehicle_data()
        assert api.get_extra_vehicle_data()["v2lStatus"]["state"] == "OFF"


def test_v2l_supported_vehicle_continues_polling():
    api = _api()

    def fake(name, query, variables):
        if name == "V2lStatus":
            return {"vehicle": {"__typename": "ElectricAVK2Vehicle", "v2lStatus": {"state": "OFF"}}}
        return {"vehicle": {}}

    with patch.object(api, "_post_graphql", side_effect=fake) as post:
        api.get_extra_vehicle_data()
        api.get_extra_vehicle_data()
    assert [c.args[0] for c in post.call_args_list].count("V2lStatus") == 2
