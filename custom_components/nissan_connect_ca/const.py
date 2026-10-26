# custom_components/nissan_connect_ca/const.py
"""Constantes pour l'intégration NissanConnect CA."""

DOMAIN = "nissan_connect_ca"

# Configuration keys
CONF_TOKEN = "access_token"
CONF_ID_TOKEN = "id_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_EXPIRES_AT = "expires_at"
CONF_USERNAME = "username"
CONF_PASSWORD = "password"
CONF_VEHICLE_ID = "vehicle_id"
CONF_USER_ID = "user_id"
CONF_UPDATE_INTERVAL = "update_interval"
DEFAULT_UPDATE_INTERVAL = 8
CONF_CHARGING_UPDATE_INTERVAL = "charging_update_interval"
DEFAULT_CHARGING_UPDATE_INTERVAL = 30
CONF_CLIMATE_UPDATE_INTERVAL = "climate_update_interval"
DEFAULT_CLIMATE_UPDATE_INTERVAL = 10

# API Endpoints
# Ces URLs proviennent de vos traces et sont cruciales.
TOKEN_URL = "https://am.cv000-telematics.net/auth/oauth2/exchange/token"
REFRESH_URL = "https://am.cv000-telematics.net/auth/oauth2/token" # Endpoint standard de rafraîchissement (sera ignoré ici)
NISSAN_LOGIN_URL = "https://login.nissan.ca/oauth2/authorize"
NISSAN_OAUTH_TOKEN_URL = "https://services.nissanusa.com/token"
NISSAN_OAUTH_CLIENT_ID = "v9no_nW7GqHYsfAKkki6_N5AFVIa"
NISSAN_OAUTH_CLIENT_SECRET = "Ynn56ncVx0yVffnDHIIMTWVYiuAa"
NISSAN_REDIRECT_URI = "https://canada.nissanconnect.com/oneid-migration/#/oneapp-nissan-android-home-fr"
NISSAN_OAUTH_SCOPE = "openid"
NISSAN_OAUTH_REALM = "/nissanca&scope=openid%20write%20cps%20rts"
NISSAN_REGION_CODE = "NISNCIVCS"
API_BASE_URL = "https://cps.cv000-telematics.net/vehicle-api/v1"
USER_INFO_URL = (
	"https://idm.cv000-telematics.net/openidm/v1/managed/user/info/"
	"{user_id}?_fields=*,vehicles/*"
)
RTS_BASE_URL = "https://rts.cv000-telematics.net/telematicsservices/v1"
GRAPHQL_URL = "https://api-ccs.na.nissancloud.com/iotmw-hades-ccs/graphql"

# Identifiants (provenant de vos en-têtes)
# L'ID client utilisé par le portail
CLIENT_ID = "nissanca-cwp-9f4b610e-a0cb-11ec-b909-0242ac120002" 
# Le 'cv-tenant-id' est souvent 'nissanca' ou 'nissana' selon la région
TENANT_ID = "nissanca" 

# Services et autres
VEHICLE_INFO_URL = f"{API_BASE_URL}/vehicles/{{vehicle_id}}?include=vehicle_services"
VEHICLE_HEALTH_URL = f"{RTS_BASE_URL}/vehicles/{{vehicle_id}}/health"
VEHICLE_ENGINES_URL = f"{RTS_BASE_URL}/vehicles/{{vehicle_id}}/engines"
VEHICLE_DOORS_URL = f"{RTS_BASE_URL}/vehicles/{{vehicle_id}}/doors"
VEHICLE_LOCATION_URL = (
	f"{RTS_BASE_URL}/vehicles/{{vehicle_id}}/locations/tracker/locate"
)
