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

