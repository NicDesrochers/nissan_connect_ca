# custom_components/nissan_connect_ca/api.py
import base64
import functools
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

from .climate_parameters import climate_parameters
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


def synchronized_account(func):
    """Ensure operations on this Nissan account are strictly serialized in queue."""

    @functools.wraps(func)
    def wrapper(self, *args, **kwargs):
        with self._account_lock:
            return func(self, *args, **kwargs)

    return wrapper


class NissanConnectCAAPI:
    """API Client pour interagir avec NissanConnect CA."""

    def __init__(self, username, password, tokens=None, vehicle_id=None, vin=None, account_lock=None, vehicle_name=None):
        """Initialise l'API Client."""
        self._username = username
        self._password = password
        self._session = requests.Session()
        self._tokens = dict(tokens) if tokens else {}
        self.vehicle_id = vehicle_id
        self.vin = vin or (tokens or {}).get("vin")
        self.vehicle_name = vehicle_name
        self._auth_stage = "idle"
        self._relogin_handler = None
        self._relogin_lock = threading.Lock()
        self._account_lock = account_lock if account_lock is not None else threading.RLock()
        self._callback_diagnostic = "not-received"
        self._last_snapshot = {}
        self._v2l_fragment_unsupported = False

        # Initialisation des jetons
        self._access_token = self._tokens.get(CONF_TOKEN)
        self._id_token = self._tokens.get(CONF_ID_TOKEN)
        self._refresh_token = self._tokens.get(CONF_REFRESH_TOKEN)
        self._oauth_access_token = self._tokens.get("oauth_access_token")
        self._oauth_id_token = self._tokens.get("oauth_id_token")
        self._token_expiration_time = self._tokens.get(CONF_EXPIRES_AT, 0)

    def set_tokens(self, tokens):
        """Met à jour les jetons de ce client à partir d'un dictionnaire."""
        self._tokens.update(tokens)
        self._access_token = tokens.get(CONF_TOKEN)
        self._id_token = tokens.get(CONF_ID_TOKEN)
        self._refresh_token = tokens.get(CONF_REFRESH_TOKEN)
        self._oauth_access_token = tokens.get("oauth_access_token")
        self._oauth_id_token = tokens.get("oauth_id_token")
        self._token_expiration_time = tokens.get(CONF_EXPIRES_AT, 0)

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
    def _label(self):
        """Return a readable vehicle label for log messages."""
        return self.vehicle_name or self.vin or self.vehicle_id or "Nissan"

    @property
    def auth_stage(self):
        """Return the last authentication stage for safe diagnostics."""
        return self._auth_stage

    @property
    def auth_diagnostic(self):
        """Return callback metadata without exposing query parameter values."""
        return self._callback_diagnostic

    @synchronized_account
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

    @synchronized_account
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
    @synchronized_account
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

    def _relogin(self, force: bool = False, rejected_token: str = None):
        """Renew tokens through the handler; return True on success."""
        if not self._relogin_handler:
            return False
        if not force and not self._is_token_expired() and self._access_token:
            return True
        initial_token = rejected_token or self._access_token
        with self._relogin_lock:
            # Si un autre véhicule a déjà renouvelé le token pendant qu'on attendait le verrou,
            # on réutilise directement le nouveau token sans faire de double login auprès de Nissan.
            if self._access_token != initial_token and not self._is_token_expired():
                _LOGGER.debug("Token was already refreshed by another vehicle, reusing it")
                return True
            try:
                self._relogin_handler()
            except requests.HTTPError as err:
                if err.response is None or err.response.status_code not in (401, 403):
                    raise
                _LOGGER.warning("Automatic Nissan sign-in was rejected")
                return False
            except requests.RequestException:
                _LOGGER.warning("Automatic Nissan sign-in failed because of a connection error")
                raise
            except (NissanConnectAuthenticationError, ValueError) as err:
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

    @synchronized_account
    def _get_json(self, url, *, require_id_token=False):
        """Perform an authenticated Nissan API request."""
        headers = self.get_headers()
        if require_id_token and not self._id_token:
            raise NissanConnectAuthenticationError("ID token is missing")

        used_token = self._access_token
        response = self._session.get(url, headers=headers, timeout=15)
        if response.status_code == 401 and self._relogin(force=True, rejected_token=used_token):
            headers = self.get_headers()
            response = self._session.get(url, headers=headers, timeout=15)
        _LOGGER.debug(
            "[%s] GET %s -> HTTP %s", self._label, self._safe_url(url), response.status_code
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

    @synchronized_account
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

    @synchronized_account
    def get_vehicle_snapshot(self):
        """Retrieve vehicle metadata and read-only telematics data."""
        if not self.vehicle_id:
            raise ValueError("Vehicle ID is missing")

        vehicle_id = self.vehicle_id
        vehicle_url = VEHICLE_INFO_URL.format(vehicle_id=vehicle_id)
        health_url = VEHICLE_HEALTH_URL.format(vehicle_id=vehicle_id)
        location_url = VEHICLE_LOCATION_URL.format(vehicle_id=vehicle_id)

        try:
            vehicle_info = self._get_json(vehicle_url)
        except (requests.RequestException, ValueError) as err:
            if self._last_snapshot.get("vehicle"):
                _LOGGER.warning(
                    "[%s] Erreur lors de la récupération des infos véhicule (%s), utilisation du cache",
                    self._label,
                    err,
                )
                vehicle_info = self._last_snapshot["vehicle"]
            else:
                raise

        if not self.vin:
            self.vin = vehicle_info.get("vin") or vehicle_info.get("refProperties", {}).get("vin")

        health_data = self._last_snapshot.get("health", {})
        try:
            health_data = self._get_json(health_url, require_id_token=True)
        except (requests.RequestException, ValueError) as err:
            _LOGGER.warning(
                "[%s] Erreur lors de la récupération du diagnostic (health: %s), conservation des dernières données",
                self._label,
                err,
            )

        engine_type = vehicle_info.get("engineType")
        is_electric = engine_type in ("Electric", "EV") or (
            not engine_type
            and any(model in str(vehicle_info.get("model", "")).lower() for model in ("leaf", "ariya"))
        )
        engines_data = {}
        if not is_electric:
            engines_data = self._last_snapshot.get("engines", {})
            try:
                engines_data = self._get_json(
                    VEHICLE_ENGINES_URL.format(vehicle_id=vehicle_id), require_id_token=True
                )
            except (requests.RequestException, ValueError) as err:
                _LOGGER.warning(
                    "[%s] Erreur lors de la récupération de l'état du moteur (engines: %s), conservation des dernières données",
                    self._label,
                    err,
                )

        location_data = self._last_snapshot.get("location", {})
        try:
            location_data = self._get_json(
                location_url, require_id_token=True
            )
        except requests.HTTPError as err:
            if err.response is not None and err.response.status_code in (403, 404):
                _LOGGER.debug("[%s] Nissan location service is unavailable for this vehicle", self._label)
            else:
                _LOGGER.warning(
                    "[%s] Erreur lors de la récupération de la localisation (%s), conservation des dernières données",
                    self._label,
                    err,
                )
        except (requests.RequestException, ValueError) as err:
            _LOGGER.warning(
                "[%s] Erreur lors de la récupération de la localisation (%s), conservation des dernières données",
                self._label,
                err,
            )

        battery_data = self._last_snapshot.get("battery", {})
        climate_data = self._last_snapshot.get("climate", {})
        climate_defaults = self._last_snapshot.get("climateDefaults", {})
        tire_pressure = self._last_snapshot.get("tirePressure")
        extra_data = self._last_snapshot.get("extra", {})

        if is_electric:
            try:
                dynamic_data = self.get_dynamic_vehicle_data()
                if dynamic_data.get("batteryStatus"):
                    battery_data = dynamic_data["batteryStatus"]
                if dynamic_data.get("climateStatus"):
                    climate_data = dynamic_data["climateStatus"]
                if dynamic_data.get("climateDefaults"):
                    climate_defaults = dynamic_data["climateDefaults"]
                if "tirePressure" in dynamic_data:
                    tire_pressure = dynamic_data["tirePressure"]
            except Exception as err:
                _LOGGER.warning("[%s] Could not fetch EV dynamic data via GraphQL: %s", self._label, err)

        try:
            fetched_extra = self.get_extra_vehicle_data()
            if fetched_extra:
                extra_data = {**extra_data, **fetched_extra}
        except Exception as err:
            _LOGGER.debug("[%s] Optional GraphQL data unavailable: %s", self._label, err)

        snapshot = {
            "vehicle": vehicle_info,
            "health": health_data,
            "engines": engines_data,
            "location": location_data,
            "battery": battery_data,
            "climate": climate_data,
        }
        if climate_defaults:
            snapshot["climateDefaults"] = climate_defaults
        if tire_pressure is not None:
            snapshot["tirePressure"] = tire_pressure
        if extra_data:
            snapshot["extra"] = extra_data

        self._last_snapshot = snapshot

        _LOGGER.debug("[%s] Nissan health data: %s", self._label, snapshot["health"])
        _LOGGER.debug("[%s] Nissan engines data: %s", self._label, snapshot["engines"])
        _LOGGER.debug("[%s] Nissan location data: %s", self._label, snapshot["location"])
        _LOGGER.debug("[%s] Nissan battery data: %s", self._label, snapshot["battery"])
        _LOGGER.debug("[%s] Nissan climate data: %s", self._label, snapshot["climate"])
        _LOGGER.debug("[%s] Nissan climateDefaults data: %s", self._label, snapshot.get("climateDefaults"))
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

    @synchronized_account
    def start_engine(self, engine_id):
        """Request a remote engine start for the configured vehicle."""
        self._set_engine_state(engine_id, "START")

    @synchronized_account
    def stop_engine(self, engine_id):
        """Request a remote engine stop for the configured vehicle."""
        self._set_engine_state(engine_id, "STOP")

    def _set_engine_state(self, engine_id, state):
        """Send an engine state command using Nissan's RTS endpoint."""
        if not self.vehicle_id:
            raise ValueError("Vehicle ID is missing")
        response = self._session.put(
            VEHICLE_ENGINES_URL.format(vehicle_id=self.vehicle_id),
            headers=self.get_headers(),
            json={"engine": {"id": engine_id, "state": state}},
            timeout=20,
        )
        self._raise_for_command_response(response, "Nissan engine service")

    @synchronized_account
    def unlock_doors(self):
        """Request an unlock of all doors using Nissan's RTS endpoint."""
        self._set_door_lock_state("UNLOCK")

    @synchronized_account
    def lock_doors(self):
        """Request a lock of all doors using Nissan's RTS endpoint."""
        self._set_door_lock_state("LOCK")

    @synchronized_account
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

    @synchronized_account
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
            "[%s] GraphQL request '%s' vers %s: variables=%s",
            self._label,
            operation_name,
            url,
            variables,
        )
        used_token = self._oauth_access_token or self._access_token
        response = self._session.post(url, headers=headers, json=payload, timeout=25)
        if response.status_code == 401 and self._relogin(force=True, rejected_token=used_token):
            headers = self.get_graphql_headers()
            response = self._session.post(url, headers=headers, json=payload, timeout=25)

        _LOGGER.debug(
            "[%s] GraphQL '%s' réponse: HTTP %s, corps: %s",
            self._label,
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

    @synchronized_account
    def get_dynamic_vehicle_data(self):
        """Récupère l'état dynamique (batterie, autonomie, climatisation) via GraphQL."""
        vin = self.vin or self.vehicle_id
        if not vin:
            return {}
        query = (
            "query VehicleDynamicData($vin: String!, $unit: DistanceUnit, $temperatureUnit: TemperatureUnitEnumType) { "
            "vehicle(vin: $vin) { __typename {tire_fragment} "
            "... on BaseElectricVehicle { "
            "batteryStatus { __typename level isPluggedIn isCharging remainingChargeTime remainingMileage(unit: $unit) { unit value } } "
            "climateStatus { __typename state temperature(unit: $temperatureUnit) { value unit } } } "
            "... on ElectricVehicle { climateDefaults { __typename climate(unit: $temperatureUnit) { value unit } } } "
            "... on BaseAVK2Vehicle { climateDefaults { __typename climate(unit: $temperatureUnit) { value unit } "
            "parameters { steeringWheelHeaterState defrostAndDeicerState seatsClimate { "
            "frontDriverState frontPassengerState rearLeftPassengerState rearRightPassengerState "
            "rearCenterPassengerState thirdLeftState thirdRightState } } } } } }"
        )
        tire_fragment = (
            "... on BaseAVKVehicle { tirePressure { lastUpdatedAt flPressure frPressure "
            "rlPressure rrPressure flStatus frStatus rlStatus rrStatus } }"
        )
        variables = {"vin": vin, "unit": "KILOMETER", "temperatureUnit": "CELSIUS"}
        # Some vehicle backends may not implement the application's tire fragment.
        try:
            data = self._post_graphql(
                "VehicleDynamicData", query.replace("{tire_fragment}", tire_fragment), variables
            )
        except NissanConnectCommandError as err:
            message = str(err).lower()
            if not (
                message.startswith("nissan graphql error:")
                and ("tirepressure" in message or "baseavkvehicle" in message)
                and any(term in message for term in ("cannot query", "unknown", "undefined", "validation"))
            ):
                raise
            _LOGGER.warning("[%s] GraphQL tire data unavailable, retrying basic EV data: %s", self._label, err)
            data = self._post_graphql(
                "VehicleDynamicData", query.replace("{tire_fragment}", ""), variables
            )
        return data.get("vehicle", {})

    @synchronized_account
    def get_extra_vehicle_data(self):
        """Fetch optional GraphQL data; each feature fails independently."""
        vin = self.vin or self.vehicle_id
        if not vin:
            return {}
        queries = {
            "capabilities": (
                "VehicleCapabilities",
                "query VehicleCapabilities($vin: String!) { vehicle(vin: $vin) { "
                "capabilities { accessoriesDetails { seatHeater { enabled accessories { "
                "secondLeftSeat secondRightSeat } } } } } }",
                {"vin": vin},
            ),
            "chargeSchedules": (
                "VehicleChargeSchedules",
                "query VehicleChargeSchedules($vin: String!) { vehicle(vin: $vin) { __typename "
                "... on BaseElectricVehicle { chargeSchedules { __typename id state startDateTime duration weekDays } } } }",
                {"vin": vin},
            ),
            "climateSchedules": (
                "VehicleClimateSchedules",
                "query VehicleClimateSchedules($vin: String!, $temperatureUnit: TemperatureUnitEnumType!) { "
                "vehicle(vin: $vin) { __typename ... on BaseElectricVehicle { climateSchedules { __typename id state "
                "startDateTime weekDays temperature(unit: $temperatureUnit) { __typename value unit } } } } }",
                {"vin": vin, "temperatureUnit": "CELSIUS"},
            ),
            "chargeHistory": (
                "VehicleChargeHistory",
                "query VehicleChargeHistory($vin: String!, $aggregator: ChargeHistoryAggregator!) { vehicle(vin: $vin) { "
                "__typename ... on BaseElectricVehicle { chargeHistory(aggregator: $aggregator) { __typename "
                "chargeSummaries { __typename day month year numberOfChargeSessions totalEnergyRecovered totalDuration numberOfErrors } } } } }",
                {"vin": vin, "aggregator": "MONTHLY"},
            ),
            "chargeConfig": (
                "ChargeConfig",
                "query ChargeConfig($vin: String!) { vehicle(vin: $vin) { __typename ... on BaseElectric2Vehicle { "
                "chargeConfig { __typename limits { __typename notification { __typename percent } charge { __typename percent } } } } } }",
                {"vin": vin},
            ),
            "v2lStatus": (
                "V2lStatus",
                "query V2lStatus($vin: String!) { vehicle(vin: $vin) { __typename ... on BaseElectric2Vehicle { "
                "v2lStatus { __typename state chargeLimitationLevel chargeMinimumLimitationLevel } } } }",
                {"vin": vin},
            ),
            "alerts": (
                "VehicleAlerts",
                "query VehicleAlerts($vin: String!, $speedUnit: SpeedUnit) { vehicle(vin: $vin) { __typename "
                "... on BaseConnectedVehicle { boundaryAlerts { __typename enabled } curfewAlerts { __typename enabled } "
                "speedAlerts(speedUnit: $speedUnit) { __typename enabled } valetAlert { __typename serviceRequestId } } } }",
                {"vin": vin, "speedUnit": "KPH"},
            ),
        }
        extra = {}
        for key, (name, query, variables) in queries.items():
            if key == "v2lStatus" and self._v2l_fragment_unsupported:
                continue
            try:
                vehicle = self._post_graphql(name, query, variables).get("vehicle") or {}
            except Exception as err:
                _LOGGER.debug("[%s] Optional GraphQL data %s unavailable: %s", self._label, key, err)
                continue
            if key in vehicle and vehicle[key] is not None:
                extra[key] = vehicle[key]
            elif key == "v2lStatus" and vehicle.get("__typename") and key not in vehicle:
                self._v2l_fragment_unsupported = True
                _LOGGER.debug("[%s] V2L fragment is not supported by this vehicle", self._label)
            elif key == "alerts" and any(k in vehicle for k in ("boundaryAlerts", "curfewAlerts", "speedAlerts", "valetAlert")):
                extra[key] = {k: vehicle.get(k) for k in ("boundaryAlerts", "curfewAlerts", "speedAlerts", "valetAlert")}
        return extra

    @synchronized_account
    def set_charge_limit(self, percent):
        """Set the target charge limit through GraphQL."""
        vin = self.vin or self.vehicle_id
        query = (
            "mutation SetChargeLimit($vin: String!, $percent: Int!) { "
            "setChargeLimit(vin: $vin, percent: $percent) { __typename serviceRequestId } }"
        )
        data = self._post_graphql("SetChargeLimit", query, {"vin": vin, "percent": int(percent)})
        return (data.get("setChargeLimit") or {}).get("serviceRequestId")

    @synchronized_account
    def set_v2l_minimum_charge_level(self, percent):
        """Set the minimum battery level reserved when using V2L."""
        if not isinstance(percent, int) or isinstance(percent, bool) or not 10 <= percent <= 95 or percent % 5:
            raise ValueError("V2L minimum charge level must be from 10 to 95 in steps of 5")
        query = (
            "mutation SetV2L($vin: String!, $input: SetV2LInput!) { "
            "setV2L(vin: $vin, input: $input) { __typename serviceRequestId } }"
        )
        data = self._post_graphql(
            "SetV2L", query,
            {"vin": self.vin or self.vehicle_id, "input": {"minimumBatteryChargeLevel": percent}},
        )
        return (data.get("setV2L") or {}).get("serviceRequestId")

    @synchronized_account
    def check_configuration_request(self, kind, request_id):
        """Check the status of a charge limit or V2L change."""
        operations = {
            "charge": ("CheckChargeConfigServiceRequest", "checkChargeConfigServiceRequest"),
            "v2l": ("CheckV2LServiceRequest", "checkV2LServiceRequest"),
        }
        if kind not in operations:
            raise ValueError("Unsupported configuration request")
        name, field = operations[kind]
        query = (
            f"mutation {name}($vin: String!, $serviceRequestId: String!) {{ "
            f"{field}(vin: $vin, serviceRequestId: $serviceRequestId) {{ __typename status }} }}"
        )
        data = self._post_graphql(
            name, query, {"vin": self.vin or self.vehicle_id, "serviceRequestId": request_id}
        )
        return data.get(field) or {}

    @synchronized_account
    def toggle_schedule(self, kind, schedule_id, enable):
        """Enable or disable a charge or climate schedule."""
        vin = self.vin or self.vehicle_id
        if kind not in ("charge", "climate"):
            raise ValueError("Unsupported schedule kind")
        name = "Toggle%sSchedule" % kind.capitalize()
        field = "toggle%sSchedule" % kind.capitalize()
        query = (
            "mutation %s($vin: String!, $schedule: %sInput!) { %s(vin: $vin, schedule: $schedule) "
            "{ __typename serviceRequestId } }" % (name, name, field)
        )
        data = self._post_graphql(name, query, {"vin": vin, "schedule": {"id": schedule_id, "enable": bool(enable)}})
        return (data.get(field) or {}).get("serviceRequestId")

    def clone_for_vehicle(self, vehicle_id, vin=None, vehicle_name=None):
        """Retourne un client API pour un autre véhicule partageant la session et les jetons."""
        client = NissanConnectCAAPI(
            username=self._username,
            password=self._password,
            tokens=self._tokens,
            vehicle_id=vehicle_id,
            vin=vin,
            account_lock=self._account_lock,
            vehicle_name=vehicle_name,
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
        client._account_lock = self._account_lock
        client._last_snapshot = dict(self._last_snapshot)
        return client

    @synchronized_account
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

    @synchronized_account
    def set_climate_defaults(self, temperature_value, unit="CELSIUS", parameters=None):
        """Save the default climate settings, rejecting unsuccessful responses."""
        vin = self.vin or self.vehicle_id
        if not vin:
            raise ValueError("Vehicle ID or VIN is missing")
        query = (
            "mutation SetClimateDefaults($vin: String!, $climate: StartClimateInput!, $parameters: SetClimateParametersInput) { "
            "setClimateDefaults(vin: $vin, climate: $climate, parameters: $parameters) { "
            "__typename ... on SetClimateDefaultsResponse { success } "
            "... on SetClimateDefaultsError { message } } }"
        )
        variables = {
            "vin": vin,
            "climate": {
                "temperatureValue": float(temperature_value),
                "unit": unit,
            },
        }
        if parameters is not None:
            variables["parameters"] = parameters
        data = self._post_graphql("SetClimateDefaults", query, variables)
        result = data.get("setClimateDefaults") or {}
        if result.get("success") is not True:
            raise NissanConnectCommandError(
                f"Nissan climate defaults update failed: {result.get('message') or 'no success confirmation'}"
            )
        return data

    @synchronized_account
    def sync_climate_defaults(self, temperature_value, unit="CELSIUS", overrides=None, capabilities=None):
        """Read current defaults and save the desired settings only when changed."""
        vehicle = self.get_dynamic_vehicle_data()
        defaults = vehicle.get("climateDefaults")
        climate = (defaults or {}).get("climate") or {}
        _LOGGER.debug(
            "[%s] Climate defaults comparison: requested=%s %s, Nissan default=%s %s, vehicle type=%s",
            self._label, temperature_value, unit, climate.get("value"), climate.get("unit"),
            vehicle.get("__typename"),
        )
        if climate.get("value") is None or climate.get("unit") != unit:
            raise NissanConnectCommandError("Nissan climate defaults could not be read in the requested unit")
        if vehicle.get("__typename") == "ElectricVehicle" and "parameters" not in defaults:
            _LOGGER.debug(
                "[%s] Climate target is retained locally; this vehicle does not expose accessory defaults",
                self._label,
            )
            return None
        snapshot = {"climateDefaults": defaults, "extra": {"capabilities": capabilities or {}}}
        current = climate_parameters(snapshot)
        desired = climate_parameters(snapshot, overrides)
        if climate["value"] != float(temperature_value) or current != desired:
            self.set_climate_defaults(temperature_value, unit, desired)
        return desired

    @synchronized_account
    def start_climate_with_defaults(self, temperature_value, unit="CELSIUS", overrides=None, capabilities=None):
        """Synchronize supported Nissan defaults before starting climate."""
        parameters = self.sync_climate_defaults(temperature_value, unit, overrides, capabilities)
        return self.start_climate(temperature_value, unit, parameters or None)

    @synchronized_account
    def start_climate(self, temperature_value=21.0, unit="CELSIUS", parameters=None):
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
        if parameters:
            variables["parameters"] = parameters
        _LOGGER.debug(
            "[%s] StartClimate API temperature: temperatureValue=%s, unit=%s",
            self._label, variables["climate"]["temperatureValue"], variables["climate"]["unit"],
        )
        data = self._post_graphql("StartClimate", query, variables)
        start_result = data.get("startClimate") or {}
        service_request_id = start_result.get("serviceRequestId")
        _LOGGER.debug("StartClimate résultat: %s, serviceRequestId: %s", start_result, service_request_id)
        return service_request_id

    @synchronized_account
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

    @synchronized_account
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

    @synchronized_account
    def start_charge(self):
        """Démarre la recharge du véhicule électrique à distance."""
        vin = self.vin or self.vehicle_id
        if not vin:
            raise ValueError("Vehicle ID or VIN is missing")
        query = (
            "mutation StartCharge($vin: String!) { "
            "startCharge(vin: $vin) { __typename serviceRequestId } }"
        )
        variables = {"vin": vin}
        data = self._post_graphql("StartCharge", query, variables)
        result = data.get("startCharge") or {}
        service_request_id = result.get("serviceRequestId")
        _LOGGER.debug("StartCharge résultat: %s, serviceRequestId: %s", result, service_request_id)
        return service_request_id

    @synchronized_account
    def stop_charge(self):
        """Arrête la recharge du véhicule électrique à distance."""
        vin = self.vin or self.vehicle_id
        if not vin:
            raise ValueError("Vehicle ID or VIN is missing")
        query = (
            "mutation StopCharge($vin: String!) { "
            "stopCharge(vin: $vin) { __typename serviceRequestId } }"
        )
        variables = {"vin": vin}
        data = self._post_graphql("StopCharge", query, variables)
        result = data.get("stopCharge") or {}
        service_request_id = result.get("serviceRequestId")
        _LOGGER.debug("StopCharge résultat: %s, serviceRequestId: %s", result, service_request_id)
        return service_request_id

    @synchronized_account
    def check_charge_request(self, service_request_id):
        """Vérifie le statut d'une requête de recharge."""
        vin = self.vin or self.vehicle_id
        if not vin:
            raise ValueError("Vehicle ID or VIN is missing")
        query = (
            "mutation CheckChargeServiceRequest($vin: String!, $serviceRequestId: String!) { "
            "checkChargeServiceRequest(vin: $vin, serviceRequestId: $serviceRequestId) { "
            "__typename status } }"
        )
        variables = {
            "vin": vin,
            "serviceRequestId": service_request_id,
        }
        data = self._post_graphql("CheckChargeServiceRequest", query, variables)
        return data.get("checkChargeServiceRequest", {})

    @synchronized_account
    def refresh_battery_status(self):
        """Demande au véhicule de pousser son état de batterie vers le cloud Nissan."""
        vin = self.vin or self.vehicle_id
        if not vin:
            raise ValueError("Vehicle ID or VIN is missing")
        query = (
            "mutation RefreshBatteryStatus($vin: String!) { "
            "refreshBatteryStatus(vin: $vin) { __typename success } }"
        )
        data = self._post_graphql("RefreshBatteryStatus", query, {"vin": vin})
        result = data.get("refreshBatteryStatus") or {}
        _LOGGER.debug("RefreshBatteryStatus résultat: %s", result)
        return result.get("success")

    @synchronized_account
    def refresh_vehicle_status(self):
        """Ask Nissan to refresh the vehicle's remote status."""
        vin = self.vin or self.vehicle_id
        if not vin:
            raise ValueError("Vehicle ID or VIN is missing")
        query = (
            "mutation RefreshVehicleStatus($vin: String!) { "
            "refreshVehicleStatus(vin: $vin) { __typename serviceRequestId } }"
        )
        data = self._post_graphql("RefreshVehicleStatus", query, {"vin": vin})
        request_id = (data.get("refreshVehicleStatus") or {}).get("serviceRequestId")
        if not request_id:
            raise NissanConnectCommandError("Vehicle status refresh returned no request ID")
        return request_id

    @synchronized_account
    def check_refresh_vehicle_status(self, request_id):
        """Check the status of a vehicle status refresh request."""
        vin = self.vin or self.vehicle_id
        if not vin:
            raise ValueError("Vehicle ID or VIN is missing")
        query = (
            "mutation CheckRefreshVehicleStatusRequest($vin: String!, $serviceRequestId: String!) { "
            "checkRefreshVehicleStatusRequest(vin: $vin, serviceRequestId: $serviceRequestId) "
            "{ __typename status } }"
        )
        data = self._post_graphql(
            "CheckRefreshVehicleStatusRequest", query,
            {"vin": vin, "serviceRequestId": request_id},
        )
        return data.get("checkRefreshVehicleStatusRequest") or {}

    @synchronized_account
    def flash_lights(self):
        """Fait clignoter les phares du véhicule à distance."""
        vin = self.vin or self.vehicle_id
        if not vin:
            raise ValueError("Vehicle ID or VIN is missing")
        query = (
            "mutation FlashLights($vin: String!) { "
            "flashLights(vin: $vin) { __typename serviceRequestId } }"
        )
        variables = {"vin": vin}
        data = self._post_graphql("FlashLights", query, variables)
        result = data.get("flashLights") or {}
        service_request_id = result.get("serviceRequestId")
        _LOGGER.debug("FlashLights résultat: %s, serviceRequestId: %s", result, service_request_id)
        return service_request_id

    @synchronized_account
    def flash_lights_horn(self):
        """Actionne les phares et le klaxon du véhicule à distance."""
        vin = self.vin or self.vehicle_id
        if not vin:
            raise ValueError("Vehicle ID or VIN is missing")
        query = (
            "mutation FlashLightsHorn($vin: String!) { "
            "flashLightsHorn(vin: $vin) { __typename serviceRequestId } }"
        )
        variables = {"vin": vin}
        data = self._post_graphql("FlashLightsHorn", query, variables)
        result = data.get("flashLightsHorn") or {}
        service_request_id = result.get("serviceRequestId")
        _LOGGER.debug("FlashLightsHorn résultat: %s, serviceRequestId: %s", result, service_request_id)
        return service_request_id

    @synchronized_account
    def check_horn_light_request(self, service_request_id):
        """Vérifie le statut d'une requête de phares/klaxon."""
        vin = self.vin or self.vehicle_id
        if not vin:
            raise ValueError("Vehicle ID or VIN is missing")
        query = (
            "mutation CheckHornLightServiceRequest($vin: String!, $serviceRequestId: String!) { "
            "checkHornLightServiceRequest(vin: $vin, serviceRequestId: $serviceRequestId) { "
            "__typename status } }"
        )
        variables = {
            "vin": vin,
            "serviceRequestId": service_request_id,
        }
        data = self._post_graphql("CheckHornLightServiceRequest", query, variables)
        return data.get("checkHornLightServiceRequest", {})
