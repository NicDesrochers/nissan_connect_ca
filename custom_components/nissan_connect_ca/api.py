# custom_components/nissan_connect_ca/api.py
import base64
import hashlib
import json
import logging
import secrets
import threading
import requests
import time
import uuid
from urllib.parse import parse_qs, quote, urljoin, urlparse

from bs4 import BeautifulSoup

from .const import (
    CONF_TOKEN,
    CONF_ID_TOKEN,
    CONF_REFRESH_TOKEN,
    CONF_EXPIRES_AT,
    CONF_USER_ID,
    CLIENT_ID,
    TENANT_ID,
    TOKEN_URL,
    VEHICLE_INFO_URL,
    API_BASE_URL,
    VEHICLE_HEALTH_URL,
    VEHICLE_ENGINES_URL,
    VEHICLE_DOORS_URL,
    VEHICLE_LOCATION_URL,
    GRAPHQL_URL,
    USER_INFO_URL,
    NISSAN_LOGIN_URL,
    NISSAN_OAUTH_TOKEN_URL,
    NISSAN_OAUTH_CLIENT_ID,
    NISSAN_OAUTH_CLIENT_SECRET,
    NISSAN_REDIRECT_URI,
    NISSAN_OAUTH_SCOPE,
    NISSAN_OAUTH_REALM,
    NISSAN_REGION_CODE,
)

_LOGGER = logging.getLogger(__name__)


class NissanConnectAuthenticationError(Exception):
    """Raised when Nissan rejects or expires the access token."""


class NissanConnectCommandError(Exception):
    """Raised when Nissan rejects a vehicle command."""


class NissanConnectCAAPI:
    """API Client pour interagir avec NissanConnect CA."""

    def __init__(self, username, password, tokens=None, vehicle_id=None, vin=None):
        """Initialise l'API Client."""
        self._username = username
        self._password = password
        self._session = requests.Session()
        self._tokens = dict(tokens) if tokens else {}
        self.vehicle_id = vehicle_id
        self.vin = vin or (tokens or {}).get("vin")
        self._auth_stage = "idle"
        self._relogin_handler = None
        self._relogin_lock = threading.Lock()
        self._callback_diagnostic = "not-received"

        # Initialisation des jetons
        self._access_token = self._tokens.get(CONF_TOKEN)
        self._id_token = self._tokens.get(CONF_ID_TOKEN)
        self._refresh_token = self._tokens.get(CONF_REFRESH_TOKEN)
        self._oauth_access_token = self._tokens.get("oauth_access_token")
        self._oauth_id_token = self._tokens.get("oauth_id_token")
        self._token_expiration_time = self._tokens.get(CONF_EXPIRES_AT, 0)

    @staticmethod
    def _decode_token_claims(token):
        """Decode a JWT payload without logging or retaining its contents."""
        try:
            payload = token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            return json.loads(base64.urlsafe_b64decode(payload))
        except (IndexError, ValueError, TypeError):
            return {}

    @property
    def auth_stage(self):
        """Return the last authentication stage for safe diagnostics."""
        return self._auth_stage

    @property
    def auth_diagnostic(self):
        """Return callback metadata without exposing query parameter values."""
        return self._callback_diagnostic

    def authenticate(self):
        """Log in with Nissan credentials and exchange PKCE and OEM tokens."""
        if not self._username or not self._password:
            raise ValueError("Nissan username and password are required")

        device_uuid = str(uuid.uuid4())
        state = "b51cb09e-731d-4d3f-beff-15c6556f2507"
        scope = f"openid device_{device_uuid}"
        authorization_params = {
            "prompt": "login",
            "client_id": NISSAN_OAUTH_CLIENT_ID,
            "redirect_uri": NISSAN_REDIRECT_URI,
            "brand": "Nissan",
            "language": "fr-CA",
            "response_type": "code",
            "state": state,
            "scope": scope,
            "viewer": "browser",
        }

        self._auth_stage = "authorization page"
        login_page = self._session.get(
            NISSAN_LOGIN_URL, params=authorization_params, timeout=30
        )
        login_page.raise_for_status()
        self._auth_stage = "login form"
        form = BeautifulSoup(login_page.text, "html.parser").find("form")
        if form is None:
            raise NissanConnectAuthenticationError("Nissan login form was not returned")

        form_data = {
            field.get("name"): field.get("value", "")
            for field in form.find_all("input")
            if field.get("name")
        }
        if not form_data.get("sessionDataKey"):
            raise NissanConnectAuthenticationError("Nissan login session is missing")
        form_data.update(
            {
                "username": f"{NISSAN_REGION_CODE}/{self._username}",
                "userName": self._username,
                "password": self._password,
                "regionCode": NISSAN_REGION_CODE,
                "chkRemember": "on",
            }
        )

        action = urljoin(login_page.url, form.get("action") or "../commonauth")
        if urlparse(action).scheme != "https" or urlparse(action).hostname != "login.nissan.ca":
            raise NissanConnectAuthenticationError("Unexpected Nissan login form action")
        self._auth_stage = "credential submission"
        callback = self._session.post(
            action,
            data=form_data,
            headers={
                "accept": (
                    "text/html,application/xhtml+xml,application/xml;q=0.9,"
                    "image/avif,image/webp,*/*;q=0.8"
                ),
                "origin": "https://login.nissan.ca",
                "referer": login_page.url,
                "sec-fetch-dest": "document",
                "sec-fetch-mode": "navigate",
                "sec-fetch-site": "same-origin",
                "sec-fetch-user": "?1",
                "upgrade-insecure-requests": "1",
                "user-agent": (
                    "Mozilla/5.0 (Linux; Android 14) AppleWebKit/537.36 "
                    "(KHTML, like Gecko) Chrome/120.0 Mobile Safari/537.36 MyNISSAN/9.9.91"
                ),
            },
            allow_redirects=False,
            timeout=30,
        )

        self._auth_stage = "OAuth callback"
        code = None
        curr = callback.headers.get("Location")
        curr_action = action
        while curr:
            next_url = urljoin(curr_action, curr)
            if "oneapp-nissan-android-home" in next_url:
                parsed = urlparse(next_url)
                qs = parse_qs(parsed.query)
                if "code" in qs:
                    code = qs["code"][0]
                elif parsed.fragment and "code=" in parsed.fragment:
                    frag_part = parsed.fragment.split("?", 1)[-1]
                    code = parse_qs(frag_part).get("code", [None])[0]
                if code:
                    break
            resp = self._session.get(next_url, allow_redirects=False, timeout=30)
            curr = resp.headers.get("Location")
            curr_action = next_url

        if not code:
            raise NissanConnectAuthenticationError(
                "Nissan login failed to return authorization code"
            )

        self._auth_stage = "PKCE token exchange"
        basic_auth = base64.b64encode(
            f"{NISSAN_OAUTH_CLIENT_ID}:{NISSAN_OAUTH_CLIENT_SECRET}".encode()
        ).decode()
        token_response = self._session.post(
            NISSAN_OAUTH_TOKEN_URL,
            headers={
                "Authorization": f"Basic {basic_auth}",
                "Content-Type": "application/x-www-form-urlencoded",
                "User-Agent": "MyNISSAN/9.9.91 (Android)",
            },
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": NISSAN_REDIRECT_URI,
                "scope": scope,
            },
            timeout=30,
        )
        token_response.raise_for_status()
        oauth_tokens = token_response.json()
        id_token = oauth_tokens.get("id_token")
        oauth_access_token = oauth_tokens.get("access_token")
        if not id_token:
            raise NissanConnectAuthenticationError("Nissan did not return an ID token")

        self._oauth_access_token = oauth_access_token
        self._oauth_id_token = id_token

        self._auth_stage = "Nissan token exchange"
        exchange_response = self._session.post(
            TOKEN_URL,
            headers={
                "cv-oem-auth-token": id_token,
                "cv-client-id": CLIENT_ID,
                "cv-correlation-id": str(uuid.uuid4()),
                "accept": "application/json",
            },
            timeout=30,
        )
        exchange_response.raise_for_status()
        exchanged_tokens = exchange_response.json()
        self._access_token = exchanged_tokens.get("access_token")
        self._refresh_token = exchanged_tokens.get("refresh_token")
        self._id_token = id_token
        self._token_expiration_time = time.time() + int(
            exchanged_tokens.get("expires_in", oauth_tokens.get("expires_in", 3600))
        )
        claims = self._decode_token_claims(self._access_token or "")
        user_id = claims.get("sub")
        if not self._access_token or not user_id:
            raise NissanConnectAuthenticationError(
                "Nissan token response is missing account information"
            )

        self._tokens.update(
            {
                CONF_TOKEN: self._access_token,
                CONF_ID_TOKEN: self._id_token,
                CONF_REFRESH_TOKEN: self._refresh_token,
                CONF_EXPIRES_AT: self._token_expiration_time,
                "oauth_access_token": self._oauth_access_token,
                "oauth_id_token": self._oauth_id_token,
            }
        )
        self._auth_stage = "authenticated"
        return {
            CONF_TOKEN: self._access_token,
            CONF_ID_TOKEN: self._id_token,
            CONF_REFRESH_TOKEN: self._refresh_token,
            CONF_EXPIRES_AT: self._token_expiration_time,
            CONF_USER_ID: str(user_id),
            "oauth_access_token": self._oauth_access_token,
            "oauth_id_token": self._oauth_id_token,
        }

    def reauthenticate(self, username, password):
        """Log in again with stored credentials, then forget them."""
        self._username = username
        self._password = password
        try:
            return self.authenticate()
        finally:
            self._username = ""
            self._password = ""

    def close(self):
        """Close the HTTP session."""
        self._username = ""
        self._password = ""
        self._session.close()

    def _is_token_expired(self):
        """Vérifie si l'Access Token est sur le point d'expirer."""
        # On considère qu'il expire 300 secondes (5 minutes) avant l'heure réelle
        return time.time() > self._token_expiration_time - 300

    # =======================================================
    # !!! CONTOURNEMENT CRITIQUE DU RAFRAÎCHISSEMENT !!!
    # =======================================================
    def _refresh_tokens(self):
        """
        Désactive la tentative de rafraîchissement standard due à l'erreur 'invalid_grant'.
        L'utilisateur DOIT saisir de nouveaux jetons manuellement toutes les 7 jours.
        """
        _LOGGER.warning(
            "Le rafraîchissement des jetons a été désactivé pour éviter l'erreur "
            "'invalid_grant'. Le système continuera d'utiliser le Access Token actuel "
            "jusqu'à son expiration, puis échouera. Un nouveau Refresh Token devra "
            "être saisi manuellement."
        )
        # On ne fait RIEN. L'Access Token reste le même et échouera à l'expiration.
        # Vous devrez relancer le flux de configuration pour saisir un nouveau jeton.
        return 

    # =======================================================
    # FONCTIONS UTILES
    # =======================================================

    def set_relogin_handler(self, handler):
        """Register a callable returning fresh tokens, used when the token expires."""
        self._relogin_handler = handler

    def _relogin(self):
        """Renew tokens through the handler; return True on success."""
        if not self._relogin_handler:
            return False
        with self._relogin_lock:
            if not self._is_token_expired() and self._access_token:
                return True
            try:
                self._relogin_handler()
            except Exception as err:
                _LOGGER.warning("Automatic Nissan sign-in failed: %s", type(err).__name__)
                _LOGGER.debug("Automatic sign-in traceback", exc_info=True)
                return False
        return True

    def get_headers(self):
        """Construit les en-têtes nécessaires pour les appels API."""
        if (self._is_token_expired() or not self._access_token) and not self._relogin():
            raise NissanConnectAuthenticationError("Access token expired")

        if not self._access_token:
            raise NissanConnectAuthenticationError("Access token is missing")

        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "cv-tenant-id": TENANT_ID,
            "cv-correlation-id": str(uuid.uuid4()),
            "accept": "application/json",
            "cv-client-id": CLIENT_ID,
            "accept-language": "fr-CA",
        }
        if self._id_token:
            headers["id_token"] = self._id_token
        return headers

    def _get_json(self, url, *, require_id_token=False):
        """Perform an authenticated Nissan API request."""
        headers = self.get_headers()
        if require_id_token and not self._id_token:
            raise NissanConnectAuthenticationError("ID token is missing")

        response = self._session.get(url, headers=headers, timeout=15)
        if response.status_code == 401 and self._relogin():
            headers = self.get_headers()
            response = self._session.get(url, headers=headers, timeout=15)
        _LOGGER.debug(
            "GET %s -> HTTP %s", self._safe_url(url), response.status_code
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as err:
            if response.status_code == 401:
                raise NissanConnectAuthenticationError("Nissan rejected the token") from err
            raise
        return response.json()

    @staticmethod
    def _safe_url(url):
        """Return the URL path only, without host or query string."""
        return urlparse(url).path

    def get_vehicles(self, user_id):
        """Retrieve the account's vehicles from its observed profile endpoint."""
        self._auth_stage = "vehicle profile"
        url = USER_INFO_URL.format(user_id=quote(user_id, safe=""))
        profile = self._get_json(url)
        vehicles = []
        for vehicle in profile.get("vehicles", []):
            vehicle_id = vehicle.get("vehicleId")
            if not vehicle_id:
                continue
            nickname = vehicle.get("refProperties", {}).get("vehicleNickname")
            vin = vehicle.get("vin") or vehicle.get("refProperties", {}).get("vin")
            vehicles.append(
                {
                    "vehicle_id": str(vehicle_id),
                    "vin": str(vin) if vin else str(vehicle_id),
                    "name": nickname or str(vehicle_id),
                    "raw": vehicle,
                }
            )
        if not vehicles:
            raise ValueError("No vehicles found in the Nissan account profile")
        return vehicles

    def get_vehicle_snapshot(self):
        """Retrieve vehicle metadata and read-only telematics data."""
        if not self.vehicle_id:
            raise ValueError("Vehicle ID is missing")

        vehicle_id = self.vehicle_id
        vehicle_url = VEHICLE_INFO_URL.format(vehicle_id=vehicle_id)
        health_url = VEHICLE_HEALTH_URL.format(vehicle_id=vehicle_id)
        engines_url = VEHICLE_ENGINES_URL.format(vehicle_id=vehicle_id)
        location_url = VEHICLE_LOCATION_URL.format(vehicle_id=vehicle_id)

        vehicle_info = self._get_json(vehicle_url)
        if not self.vin:
            self.vin = vehicle_info.get("vin") or vehicle_info.get("refProperties", {}).get("vin")

        snapshot = {
            "vehicle": vehicle_info,
            "health": self._get_json(health_url, require_id_token=True),
            "engines": self._get_json(engines_url, require_id_token=True),
            "location": {},
            "battery": {},
            "climate": {},
        }
        try:
            snapshot["location"] = self._get_json(
                location_url, require_id_token=True
            )
        except requests.HTTPError as err:
            if err.response is None or err.response.status_code not in (403, 404):
                raise
            _LOGGER.debug("Nissan location service is unavailable for this vehicle")

        if (
            vehicle_info.get("engineType") in ("Electric", "EV")
            or "leaf" in str(vehicle_info.get("model", "")).lower()
            or "ariya" in str(vehicle_info.get("model", "")).lower()
        ):
            try:
                dynamic_data = self.get_dynamic_vehicle_data()
                snapshot["battery"] = dynamic_data.get("batteryStatus") or {}
                snapshot["climate"] = dynamic_data.get("climateStatus") or {}
            except Exception as err:
                _LOGGER.warning("Could not fetch EV dynamic data via GraphQL: %s", err)

        _LOGGER.debug("Nissan health data: %s", snapshot["health"])
        _LOGGER.debug("Nissan engines data: %s", snapshot["engines"])
        _LOGGER.debug("Nissan location data: %s", snapshot["location"])
        _LOGGER.debug("Nissan battery data: %s", snapshot["battery"])
        _LOGGER.debug("Nissan climate data: %s", snapshot["climate"])
        return snapshot

    @staticmethod
    def _raise_for_command_response(response, service):
        """Raise sanitized command errors without including vehicle URLs."""
        _LOGGER.debug(
            "%s response: HTTP %s, body: %s",
            service,
            response.status_code,
            response.text[:500],
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as err:
            if response.status_code == 401:
                raise NissanConnectAuthenticationError(
                    "Nissan rejected the token"
                ) from err
            raise NissanConnectCommandError(
                f"{service} returned HTTP {response.status_code}"
            ) from err
        if response.status_code != 202:
            raise NissanConnectCommandError(
                f"{service} returned unexpected HTTP {response.status_code}"
            )

    def start_engine(self, engine_id):
        """Request a remote engine start for the configured vehicle."""
        if not self.vehicle_id:
            raise ValueError("Vehicle ID is missing")
        response = self._session.put(
            VEHICLE_ENGINES_URL.format(vehicle_id=self.vehicle_id),
            headers=self.get_headers(),
            json={"engine": {"id": engine_id, "state": "START"}},
            timeout=20,
        )
        self._raise_for_command_response(response, "Nissan engine service")

    def unlock_doors(self):
        """Request an unlock of all doors using Nissan's RTS endpoint."""
        self._set_door_lock_state("UNLOCK")

    def lock_doors(self):
        """Request a lock of all doors using Nissan's RTS endpoint."""
        self._set_door_lock_state("LOCK")

    def _set_door_lock_state(self, state):
        """Send Nissan's all-door lock or unlock request."""
        if not self.vehicle_id:
            raise ValueError("Vehicle ID is missing")
        _LOGGER.debug("Sending door command: %s", state)
        response = self._session.put(
            VEHICLE_DOORS_URL.format(vehicle_id=self.vehicle_id),
            headers=self.get_headers(),
            json={"doors": [{"id": "ALL", "locks": [{"state": state, "id": "1"}]}]},
            timeout=20,
        )
        self._raise_for_command_response(response, "Nissan door service")

    def get_graphql_headers(self):
        """Construit les en-têtes nécessaires pour l'API GraphQL Nissan Cloud."""
        if (self._is_token_expired() or not self._access_token) and not self._relogin():
            raise NissanConnectAuthenticationError("Access token expired")

        if not self._access_token:
            raise NissanConnectAuthenticationError("Access token is missing")

        auth_token = self._oauth_access_token or self._access_token
        id_token = self._oauth_id_token or self._id_token
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Brand": "Nissan",
            "country": "CA",
            "Accept-Language": "fr-CA",
            "Authorization": f"Bearer {auth_token}",
            "x-correlation-id": str(uuid.uuid4()),
            "User-Agent": "MyNISSAN/9.9.91 (Android)",
        }
        if id_token:
            headers["id-token"] = id_token
        return headers

    def _post_graphql(self, operation_name, query, variables):
        """Exécute une requête GraphQL vers l'API Nissan Cloud."""
        url = GRAPHQL_URL
        headers = self.get_graphql_headers()
        payload = {
            "operationName": operation_name,
            "query": query,
            "variables": variables,
        }
        _LOGGER.debug(
            "GraphQL request '%s' vers %s: variables=%s",
            operation_name,
            url,
            variables,
        )
        response = self._session.post(url, headers=headers, json=payload, timeout=25)
        if response.status_code == 401 and self._relogin():
            headers = self.get_graphql_headers()
            response = self._session.post(url, headers=headers, json=payload, timeout=25)

        _LOGGER.debug(
            "GraphQL '%s' réponse: HTTP %s, corps: %s",
            operation_name,
            response.status_code,
            response.text[:1000],
        )
        try:
            response.raise_for_status()
        except requests.HTTPError as err:
            if response.status_code == 401:
                raise NissanConnectAuthenticationError("Nissan rejected the token") from err
            raise NissanConnectCommandError(
                f"GraphQL {operation_name} returned HTTP {response.status_code}"
            ) from err

        result = response.json()
        if "errors" in result and result["errors"]:
            error_msgs = [e.get("message", "unknown") for e in result["errors"]]
            _LOGGER.error("GraphQL errors in %s: %s", operation_name, error_msgs)
            raise NissanConnectCommandError(f"Nissan GraphQL error: {'; '.join(error_msgs)}")

        return result.get("data", {})

    def get_dynamic_vehicle_data(self):
        """Récupère l'état dynamique (batterie, autonomie, climatisation) via GraphQL."""
        vin = self.vin or self.vehicle_id
        if not vin:
            return {}
        query = (
            "query VehicleDynamicData($vin: String!, $unit: DistanceUnit, $temperatureUnit: TemperatureUnitEnumType) { "
            "vehicle(vin: $vin) { __typename ... on BaseElectricVehicle { "
            "batteryStatus { __typename level isPluggedIn isCharging remainingChargeTime remainingMileage(unit: $unit) { unit value } } "
            "climateStatus { __typename state temperature(unit: $temperatureUnit) { value unit } } } } }"
        )
        data = self._post_graphql(
            "VehicleDynamicData",
            query,
            {"vin": vin, "unit": "KILOMETER", "temperatureUnit": "CELSIUS"},
        )
        return data.get("vehicle", {})

    def clone_for_vehicle(self, vehicle_id, vin=None):
        """Retourne un client API pour un autre véhicule partageant la session et les jetons."""
        client = NissanConnectCAAPI(
            username=self._username,
            password=self._password,
            tokens=self._tokens,
            vehicle_id=vehicle_id,
            vin=vin,
        )
        client._session = self._session
        client._oauth_access_token = self._oauth_access_token
        client._oauth_id_token = self._oauth_id_token
        client._access_token = self._access_token
        client._id_token = self._id_token
        client._refresh_token = self._refresh_token
        client._token_expiration_time = self._token_expiration_time
        client._relogin_handler = self._relogin_handler
        client._relogin_lock = self._relogin_lock
        return client

    def get_climate_status(self):
        """Récupère l'état actuel de la climatisation (véhicule électrique)."""
        vin = self.vin or self.vehicle_id
        if not vin:
            raise ValueError("Vehicle ID or VIN is missing")
        query = (
            "query VehicleClimateStatus($vin: String!, $temperatureUnit: TemperatureUnitEnumType!) { "
            "vehicle(vin: $vin) { __typename ... on BaseElectricVehicle { "
            "climateStatus { __typename ...ClimateStatusDetails } } } } "
            "fragment ClimateStatusDetails on ClimateStatus { __typename state "
            "temperature(unit: $temperatureUnit) { __typename value unit } }"
        )
        data = self._post_graphql(
            "VehicleClimateStatus",
            query,
            {"vin": vin, "temperatureUnit": "CELSIUS"},
        )
        return data.get("vehicle", {}).get("climateStatus", {})

    def start_climate(self, temperature_value=21.0, unit="CELSIUS"):
        """Démarre la climatisation à distance (habitacle)."""
        vin = self.vin or self.vehicle_id
        if not vin:
            raise ValueError("Vehicle ID or VIN is missing")
        query = (
            "mutation StartClimate($vin: String!, $climate: StartClimateInput!, "
            "$parameters: SetClimateParametersInput, $setAsDefault: Boolean) { "
            "startClimate(vin: $vin, climate: $climate, parameters: $parameters, setAsDefault: $setAsDefault) { "
            "__typename ... on TelematicsResponse { serviceRequestId additionalData { "
            "__typename ... on SetClimateDefaultsResponse { success } "
            "... on SetClimateDefaultsError { message } } } } }"
        )
        variables = {
            "vin": vin,
            "climate": {
                "temperatureValue": float(temperature_value),
                "unit": unit,
            },
        }
        data = self._post_graphql("StartClimate", query, variables)
        start_result = data.get("startClimate") or {}
        service_request_id = start_result.get("serviceRequestId")
        _LOGGER.debug("StartClimate résultat: %s, serviceRequestId: %s", start_result, service_request_id)
        return service_request_id

    def stop_climate(self):
        """Arrête la climatisation à distance."""
        vin = self.vin or self.vehicle_id
        if not vin:
            raise ValueError("Vehicle ID or VIN is missing")
        query = (
            "mutation StopClimate($vin: String!) { "
            "stopClimate(vin: $vin) { __typename serviceRequestId } }"
        )
        variables = {"vin": vin}
        data = self._post_graphql("StopClimate", query, variables)
        stop_result = data.get("stopClimate") or {}
        service_request_id = stop_result.get("serviceRequestId")
        _LOGGER.debug("StopClimate résultat: %s, serviceRequestId: %s", stop_result, service_request_id)
        return service_request_id

    def check_climate_request(self, service_request_id):
        """Vérifie le statut d'une requête de climatisation."""
        vin = self.vin or self.vehicle_id
        if not vin:
            raise ValueError("Vehicle ID or VIN is missing")
        query = (
            "mutation CheckRemoteClimateRequest($vin: String!, $serviceRequestId: String!) { "
            "checkRemoteClimateRequest(vin: $vin, serviceRequestId: $serviceRequestId) { "
            "__typename status statusDetails } }"
        )
        variables = {
            "vin": vin,
            "serviceRequestId": service_request_id,
        }
        data = self._post_graphql("CheckRemoteClimateRequest", query, variables)
        return data.get("checkRemoteClimateRequest", {})

