"""Constants for the Weheat custom integration."""

from __future__ import annotations

import logging

DOMAIN = "weheat"
MANUFACTURER = "Weheat"
ENTRY_TITLE = "Weheat cloud"

LOGGER: logging.Logger = logging.getLogger(__package__)

# Polling intervals (seconds)
LOG_UPDATE_INTERVAL = 120
ENERGY_UPDATE_INTERVAL = 1800
SETTINGS_UPDATE_INTERVAL = 300

# Suggested display precision
DISPLAY_PRECISION_WATTS = 0
DISPLAY_PRECISION_COP = 1
DISPLAY_PRECISION_WATER_TEMP = 1
DISPLAY_PRECISION_FLOW = 1

# API Base URLs
DEFAULT_API_BASE_URL = "https://api.weheat.nl/third_party"
DEFAULT_PORTAL_BASE_URL = "https://api.weheat.nl"
DEFAULT_AUTH_BASE_URL = "https://auth.weheat.nl"
DEFAULT_TIMEOUT_SECONDS = 15.0

# Keycloak / OAuth2 configuration
OAUTH2_AUTHORIZE = (
    "https://auth.weheat.nl/auth/realms/Weheat/protocol/openid-connect/auth/"
)
OAUTH2_TOKEN = (
    "https://auth.weheat.nl/auth/realms/Weheat/protocol/openid-connect/token/"
)
OAUTH2_CLIENT_ID = "weheat-portal"
OAUTH2_SCOPES = ["openid", "offline_access"]

# API Endpoints
ENDPOINT_USER_ME = "/api/v1/users/me"
ENDPOINT_HEAT_PUMPS = "/api/v1/heat-pumps"
ENDPOINT_HEAT_PUMP_DETAILS = "/api/v1/heat-pumps/{uuid}"
ENDPOINT_HEAT_PUMP_LOGS_LATEST = "/api/v1/heat-pumps/{uuid}/logs/latest"
ENDPOINT_ENERGY_TOTAL = "/api/v1/energy-logs/{uuid}/total"
ENDPOINT_ENERGY_TOTAL_FALLBACK = "/total/{uuid}"
ENDPOINT_INSTALLATIONS_FULLY_EXPANDED = "/api/v1/installations/fully-expanded"
ENDPOINT_INSTALLATION_SETTINGS = "/api/v1/installations/{installation_id}/settings"
ENDPOINT_CONTROL_BOARD_SETTINGS = "/api/v1/control-boards/{control_board_id}/settings"
ENDPOINT_CONTROL_BOARD_SETTINGS_STATUS = (
    "/api/v1/control-boards/{control_board_id}/settings/{request_id}"
)
ENDPOINT_CONTROL_BOARD_VARIABLES = "/api/v1/control-boards/{control_board_id}/variables"
ENDPOINT_DHW_BOOST = "/api/v1/heat-pumps/{heat_pump_id}/dhw/boost"

# Asynchronous settings request status code map
SETTINGS_REQUEST_STATUS_MAP: dict[int, str] = {
    0: "Pending",
    1: "Executing",
    2: "Succeeded",
    3: "Cancelled",
    4: "Failed",
    5: "SucceededWithHeatPumpSettingsMismatch",
    6: "FailedDeviceWrongState",
    7: "FailedWrongExecutionOrder",
    8: "FailedDeviceDisconnected",
    9: "FailedSettingsRequestMismatch",
    10: "FailedSettingsKeyNotFoundInSdf",
    11: "FailedAnotherSettingsRequestExecuting",
}

# Silent Hours Schedule Constants
DAYS_OF_WEEK: tuple[str, ...] = (
    "Monday",
    "Tuesday",
    "Wednesday",
    "Thursday",
    "Friday",
    "Saturday",
    "Sunday",
)

VALID_SILENT_SCHEDULE_KEYS: frozenset[str] = frozenset(
    f"Silent{comp}{day}{action}"
    for day in DAYS_OF_WEEK
    for comp in ("Hours", "Minutes")
    for action in ("Start", "Stop")
)

# Input bounds and validation limits
PERFORMANCE_MODE_MIN = 0
PERFORMANCE_MODE_MAX = 4

SILENT_HOURS_PRESET_MIN = 0
SILENT_HOURS_PRESET_MAX = 7

DHW_TARGET_TEMP_MIN = 40.0
DHW_TARGET_TEMP_MAX = 60.0

DHW_BOOST_DURATIONS: tuple[int, ...] = (30, 60, 90, 120)
DHW_BOOST_TEMP_MIN = 40.0
DHW_BOOST_TEMP_MAX = 65.0
