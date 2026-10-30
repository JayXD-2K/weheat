"""Direct aiohttp client for Weheat API and Portal endpoints.

Zero unvetted dependencies; pure aiohttp implementation.
"""

from __future__ import annotations

import asyncio
import json as json_lib
import logging
from typing import Any

import aiohttp
from aiohttp import ClientError, ClientResponseError, ClientSession, ClientTimeout

from .const import (
    DEFAULT_API_BASE_URL,
    DEFAULT_TIMEOUT_SECONDS,
    SETTINGS_REQUEST_STATUS_MAP,
    VALID_SILENT_SCHEDULE_KEYS,
)
from .models import (
    ControlBoardSettings,
    HeatPumpEnergy,
    HeatPumpInfo,
    HeatPumpLog,
)

_LOGGER = logging.getLogger(__name__)


class WeheatError(Exception):
    """Base exception for all Weheat integration errors."""


class WeheatAuthError(WeheatError):
    """Exception raised when authentication fails (HTTP 401/403 or invalid token)."""

    def __init__(
        self, message: str = "Authentication failed", status_code: int = 401
    ) -> None:
        super().__init__(message)
        self.status_code = status_code


class WeheatApiError(WeheatError):
    """Exception raised when Weheat API returns an error response (HTTP 4xx/5xx)."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response_data: Any = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_data = response_data


class WeheatTimeoutError(WeheatError):
    """Exception raised when an API request times out."""

    def __init__(self, message: str = "Request timed out") -> None:
        super().__init__(message)


class WeheatClient:
    """Client for communicating with the Weheat API and Portal endpoints."""

    def __init__(
        self,
        session: ClientSession,
        base_url: str = DEFAULT_API_BASE_URL,
        portal_base_url: str | None = None,
        access_token: str | None = None,
        request_timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        """Initialize the Weheat API client.

        Args:
            session: Shared aiohttp ClientSession.
            base_url: Base URL for third-party API endpoints.
            portal_base_url: Base URL for portal endpoints.
            access_token: Optional default Bearer access token.
            request_timeout: Default request timeout in seconds.
        """
        self._session = session
        self._base_url = base_url.rstrip("/")
        if portal_base_url is not None:
            self._portal_base_url = portal_base_url.rstrip("/")
        elif self._base_url.endswith("/third_party"):
            self._portal_base_url = self._base_url[: -len("/third_party")]
        else:
            self._portal_base_url = self._base_url

        self._access_token = access_token
        self._request_timeout = request_timeout
        self._control_board_cache: dict[str, str] = {}

    @property
    def base_url(self) -> str:
        """Return the base URL used for telemetry endpoints."""
        return self._base_url

    @property
    def portal_base_url(self) -> str:
        """Return the base URL used for portal/control endpoints."""
        return self._portal_base_url

    async def _request(
        self,
        method: str,
        url: str,
        token: str | None = None,
        params: dict[str, Any] | None = None,
        json: Any = None,
        json_data: Any = None,
        timeout: float | None = None,
    ) -> Any:
        """Execute an authenticated HTTP request and handle common error states."""
        effective_token = token or self._access_token
        headers: dict[str, str] = {
            "Accept": "application/json",
        }
        if effective_token:
            headers["Authorization"] = f"Bearer {effective_token}"

        payload = json if json is not None else json_data
        if payload is not None:
            headers["Content-Type"] = "application/json"

        req_timeout = ClientTimeout(total=timeout or self._request_timeout)

        try:
            async with self._session.request(
                method=method,
                url=url,
                headers=headers,
                params=params,
                json=payload,
                timeout=req_timeout,
            ) as response:
                status = response.status

                if status == 204:
                    return {}

                if status in (401, 403):
                    error_msg = f"Authentication failed with status {status}"
                    try:
                        err_payload = await response.json(content_type=None)
                        if isinstance(err_payload, dict):
                            error_msg = (
                                err_payload.get("message")
                                or err_payload.get("error_description")
                                or err_payload.get("error")
                                or error_msg
                            )
                    except Exception:
                        text = await response.text()
                        if text:
                            error_msg = text
                    raise WeheatAuthError(error_msg, status_code=status)

                if status >= 400:
                    error_msg = f"API request to {url} failed with status {status}"
                    err_payload = None
                    try:
                        err_payload = await response.json(content_type=None)
                        if isinstance(err_payload, dict):
                            error_msg = (
                                err_payload.get("message")
                                or err_payload.get("error_description")
                                or err_payload.get("error")
                                or error_msg
                            )
                    except Exception:
                        text = await response.text()
                        if text:
                            error_msg = text
                    raise WeheatApiError(
                        error_msg, status_code=status, response_data=err_payload
                    )

                try:
                    return await response.json(content_type=None)
                except (json_lib.JSONDecodeError, aiohttp.ContentTypeError) as err:
                    text_content = await response.text()
                    try:
                        return json_lib.loads(text_content)
                    except Exception:
                        raise WeheatApiError(
                            f"Invalid JSON returned from {url}: {err}"
                        ) from err

        except (
            TimeoutError,
            aiohttp.ServerTimeoutError,
            aiohttp.SocketTimeoutError,
        ) as err:
            raise WeheatTimeoutError(f"Request to {url} timed out: {err}") from err
        except (WeheatAuthError, WeheatApiError, WeheatTimeoutError):
            raise
        except ClientResponseError as err:
            if err.status in (401, 403):
                raise WeheatAuthError(err.message, status_code=err.status) from err
            raise WeheatApiError(
                f"ClientResponseError: {err.message}",
                status_code=err.status,
            ) from err
        except ClientError as err:
            raise WeheatApiError(f"Connection error to {url}: {err}") from err

    # -------------------------------------------------------------------------
    # Telemetry Read Methods
    # -------------------------------------------------------------------------

    async def async_get_user_id(self, token: str | None = None) -> str:
        """Retrieve current user's unique identifier.

        Args:
            token: Access token for Bearer authorization.

        Returns:
            The user UUID string.
        """
        url = f"{self._base_url}/api/v1/users/me"
        data = await self._request("GET", url, token=token)
        if isinstance(data, dict) and "id" in data:
            return str(data["id"])
        raise WeheatApiError(
            "Missing 'id' field in /users/me response", response_data=data
        )

    async def async_get_user_profile(self, token: str | None = None) -> dict[str, Any]:
        """Retrieve full user profile metadata.

        Args:
            token: Access token for Bearer authorization.

        Returns:
            Dictionary matching user_me.json schema.
        """
        url = f"{self._base_url}/api/v1/users/me"
        data = await self._request("GET", url, token=token)
        return data if isinstance(data, dict) else {}

    async def async_get_heat_pumps(
        self,
        token: str | None = None,
        state: int = 3,
    ) -> list[HeatPumpInfo]:
        """Retrieve all active heat pumps linked to the user account.

        Args:
            token: Access token for Bearer authorization.
            state: Filter by state (default 3 = active).

        Returns:
            List of HeatPumpInfo dataclass instances.
        """
        url = f"{self._base_url}/api/v1/heat_pumps"
        params = {"state": state, "page": 1, "limit": 1000}
        data = await self._request("GET", url, token=token, params=params)

        items: list[dict[str, Any]]
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
            items = data["data"]
        elif isinstance(data, list):
            items = data
        else:
            items = []

        result: list[HeatPumpInfo] = []
        for item in items:
            pump = HeatPumpInfo.from_api_dict(item)
            result.append(pump)
            if pump.control_board_id:
                self._control_board_cache[pump.heatpump_id] = str(pump.control_board_id)
                if pump.installation_id:
                    self._control_board_cache[pump.installation_id] = str(
                        pump.control_board_id
                    )
                self._control_board_cache[str(pump.control_board_id)] = str(
                    pump.control_board_id
                )

        return result

    async def async_get_heat_pump_details(
        self,
        uuid: str,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Retrieve specifications for a single heat pump.

        Args:
            uuid: Heat pump UUID.
            token: Access token for Bearer authorization.

        Returns:
            Dictionary of heat pump specifications.
        """
        url = f"{self._base_url}/api/v1/heat-pumps/{uuid}"
        data = await self._request("GET", url, token=token)
        return data if isinstance(data, dict) else {}

    async def async_get_installation_topology(
        self,
        token: str | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve fully-expanded installation topology mapping heat pumps to control boards.

        Args:
            token: Access token for Bearer authorization.

        Returns:
            List of fully-expanded installation dictionaries.
        """
        url = f"{self._portal_base_url}/api/v1/installations/fully-expanded"
        data = await self._request("GET", url, token=token)

        items: list[dict[str, Any]]
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], list):
            items = data["data"]
        elif isinstance(data, list):
            items = data
        else:
            items = []

        for item in items:
            cb_id = item.get("controlBoardId")
            if not cb_id:
                continue
            hp_id = item.get("id") or item.get("heatPumpId")
            inst_id = item.get("installationId")

            if hp_id:
                self._control_board_cache[str(hp_id)] = str(cb_id)
            if inst_id:
                self._control_board_cache[str(inst_id)] = str(cb_id)
            self._control_board_cache[str(cb_id)] = str(cb_id)

        return items

    async def async_get_control_board_id(
        self,
        target_id: str,
        token: str | None = None,
        force_refresh: bool = False,
    ) -> str:
        """Resolve a heat_pump_id or installation_id to a control_board_id.

        Uses cached topology if available, or fetches from
        /api/v1/installations/fully-expanded.

        Args:
            target_id: Heat pump UUID, installation ID, or control board ID.
            token: Access token for Bearer authorization.
            force_refresh: Whether to bypass cache and refresh topology.

        Returns:
            The resolved controlBoardId string.

        Raises:
            ValueError: If target_id is empty.
            WeheatApiError: If target cannot be resolved to a control board.
        """
        if not target_id or not target_id.strip():
            raise ValueError("target_id must not be empty")

        clean_id = target_id.strip()

        if not force_refresh and clean_id in self._control_board_cache:
            return self._control_board_cache[clean_id]

        if clean_id.startswith("cb-") and not force_refresh:
            self._control_board_cache[clean_id] = clean_id
            return clean_id

        await self.async_get_installation_topology(token=token)

        if clean_id in self._control_board_cache:
            return self._control_board_cache[clean_id]

        raise WeheatApiError(
            f"Control board ID could not be resolved for target '{target_id}'"
        )

    async def async_get_latest_logs(
        self,
        uuid: str,
        token: str | None = None,
    ) -> HeatPumpLog:
        """Retrieve latest operational telemetry logs for a heat pump.

        Args:
            uuid: Heat pump UUID.
            token: Access token for Bearer authorization.

        Returns:
            HeatPumpLog dataclass instance.
        """
        url = f"{self._base_url}/api/v1/heat-pumps/{uuid}/logs/latest"
        data = await self._request("GET", url, token=token)
        if isinstance(data, dict):
            return HeatPumpLog.from_api_dict(data)
        raise WeheatApiError(
            f"Unexpected response format for latest logs of {uuid}",
            response_data=data,
        )

    async def async_get_energy_total(
        self,
        uuid: str,
        token: str | None = None,
    ) -> HeatPumpEnergy:
        """Retrieve cumulative energy totals for a heat pump.

        Args:
            uuid: Heat pump UUID.
            token: Access token for Bearer authorization.

        Returns:
            HeatPumpEnergy dataclass instance.
        """
        url = f"{self._base_url}/api/v1/energy-logs/{uuid}/total"
        try:
            data = await self._request("GET", url, token=token)
            if isinstance(data, dict):
                return HeatPumpEnergy.from_api_dict(data)
        except WeheatApiError as err:
            if err.status_code == 404:
                fallback_url = f"{self._base_url}/total/{uuid}"
                data = await self._request("GET", fallback_url, token=token)
                if isinstance(data, dict):
                    return HeatPumpEnergy.from_api_dict(data)
            raise
        raise WeheatApiError(
            f"Unexpected response format for energy total of {uuid}",
            response_data=data,
        )

    async def async_get_installation_settings(
        self,
        installation_id: str,
        token: str | None = None,
    ) -> ControlBoardSettings:
        """Retrieve installation settings including quiet mode and DHW configurations.

        Args:
            installation_id: Installation UUID.
            token: Access token for Bearer authorization.

        Returns:
            ControlBoardSettings dataclass instance.
        """
        url = f"{self._portal_base_url}/api/v1/installations/{installation_id}/settings"
        data = await self._request("GET", url, token=token)
        if isinstance(data, dict):
            return ControlBoardSettings.from_api_dict(data)
        raise WeheatApiError(
            f"Unexpected response format for installation settings of {installation_id}",
            response_data=data,
        )

    # -------------------------------------------------------------------------
    # Control Board Settings Mutation Methods
    # -------------------------------------------------------------------------

    async def async_patch_control_board_settings(
        self,
        control_board_id: str,
        token: str | None = None,
        settings: dict[str, Any] | None = None,
        update_immediately: bool = False,
    ) -> str:
        """Dispatch settings mutation to /api/v1/control-boards/{id}/settings.

        Args:
            control_board_id: Target control board ID.
            token: Access token for Bearer authorization.
            settings: Dictionary of setting keys and values.
            update_immediately: Whether to force immediate update (default False).

        Returns:
            The settingsRequestId string.

        Raises:
            ValueError: If control_board_id or settings are empty.
            WeheatApiError: If request fails or request ID is missing.
        """
        if not control_board_id or not control_board_id.strip():
            raise ValueError("control_board_id must not be empty")
        if not settings:
            raise ValueError("settings dictionary must not be empty")

        url = f"{self._portal_base_url}/api/v1/control-boards/{control_board_id.strip()}/settings"
        payload = {
            "settings": settings,
            "updateImmediately": update_immediately,
        }

        data = await self._request("PATCH", url, token=token, json=payload)
        request_id = data.get("settingsRequestId") or data.get("requestId")
        if not request_id:
            raise WeheatApiError(
                "No settingsRequestId or requestId in response",
                response_data=data,
            )
        return str(request_id)

    async def async_set_performance_mode(
        self,
        control_board_id: str,
        token: str | None = None,
        preset: int = 0,
    ) -> str:
        """Set performance mode preset (0=Performance, 1=Silent1..4=Silent4).

        Args:
            control_board_id: Target control board ID.
            token: Access token for Bearer authorization.
            preset: Performance mode preset integer (0 to 4).

        Returns:
            The settingsRequestId string.
        """
        if not isinstance(preset, int) or not (0 <= preset <= 4):
            raise ValueError(
                f"Invalid performance mode preset {preset}; preset must be between 0 and 4."
            )
        return await self.async_patch_control_board_settings(
            control_board_id=control_board_id,
            token=token,
            settings={"PerformanceModePreset": int(preset)},
            update_immediately=False,
        )

    async def async_set_silent_hours_preset(
        self,
        control_board_id: str,
        token: str | None = None,
        preset: int = 0,
    ) -> str:
        """Set silent hours scheduled preset (0=Off, 1=Silent1..7=Silent7).

        Args:
            control_board_id: Target control board ID.
            token: Access token for Bearer authorization.
            preset: Silent hours scheduled preset integer (0 to 7).

        Returns:
            The settingsRequestId string.
        """
        if not isinstance(preset, int) or not (0 <= preset <= 7):
            raise ValueError(
                f"Invalid silent hours preset {preset}; preset must be between 0 and 7."
            )
        return await self.async_patch_control_board_settings(
            control_board_id=control_board_id,
            token=token,
            settings={"SilentHoursScheduledPreset": int(preset)},
            update_immediately=False,
        )

    async def async_set_silent_schedule(
        self,
        control_board_id: str,
        token: str | None = None,
        schedule: dict[str, int] | None = None,
    ) -> str:
        """Set day-specific silent hours schedule (hours 0..23, minutes 0..59).

        Args:
            control_board_id: Target control board ID.
            token: Access token for Bearer authorization.
            schedule: Dictionary of SilentHours/SilentMinutes keys and values.

        Returns:
            The settingsRequestId string.
        """
        if not schedule:
            raise ValueError("schedule dictionary must not be empty")

        cleaned_schedule: dict[str, int] = {}
        for key, value in schedule.items():
            if key not in VALID_SILENT_SCHEDULE_KEYS:
                raise ValueError(
                    f"Unknown schedule key: '{key}'. Must be one of valid SilentHours/SilentMinutes keys."
                )
            if not isinstance(value, int):
                raise ValueError(
                    f"Schedule value for '{key}' must be an integer, got {type(value).__name__}."
                )
            if "Hours" in key and not (0 <= value <= 23):
                raise ValueError(
                    f"Invalid hour for '{key}': {value}. Must be between 0 and 23."
                )
            if "Minutes" in key and not (0 <= value <= 59):
                raise ValueError(
                    f"Invalid minute for '{key}': {value}. Must be between 0 and 59."
                )
            cleaned_schedule[key] = value

        return await self.async_patch_control_board_settings(
            control_board_id=control_board_id,
            token=token,
            settings=cleaned_schedule,
            update_immediately=False,
        )

    async def async_set_silent_hours_schedule(
        self,
        control_board_id: str,
        token: str | None = None,
        preset: int = 0,
        schedule: dict[str, int] | None = None,
    ) -> str:
        """Combined atomic update of silent hours preset and schedule.

        Args:
            control_board_id: Target control board ID.
            token: Access token for Bearer authorization.
            preset: Preset integer (0 to 7).
            schedule: Optional day schedule dictionary.

        Returns:
            The settingsRequestId string.
        """
        if not isinstance(preset, int) or not (0 <= preset <= 7):
            raise ValueError(
                f"Invalid silent hours preset {preset}; preset must be between 0 and 7."
            )

        settings: dict[str, Any] = {"SilentHoursScheduledPreset": int(preset)}
        if schedule:
            for key, value in schedule.items():
                if key not in VALID_SILENT_SCHEDULE_KEYS:
                    raise ValueError(f"Unknown schedule key: '{key}'")
                if not isinstance(value, int):
                    raise ValueError(f"Schedule value for '{key}' must be integer")
                if "Hours" in key and not (0 <= value <= 23):
                    raise ValueError(f"Invalid hour for '{key}': {value}")
                if "Minutes" in key and not (0 <= value <= 59):
                    raise ValueError(f"Invalid minute for '{key}': {value}")
                settings[key] = value

        return await self.async_patch_control_board_settings(
            control_board_id=control_board_id,
            token=token,
            settings=settings,
            update_immediately=False,
        )

    async def async_set_dhw_target_temperature(
        self,
        control_board_id: str,
        token: str | None = None,
        temperature: float = 50.0,
    ) -> str:
        """Set DHW target temperature (valid range: 40.0°C to 60.0°C).

        Args:
            control_board_id: Target control board ID.
            token: Access token for Bearer authorization.
            temperature: Target temperature in °C.

        Returns:
            The settingsRequestId string.
        """
        if not isinstance(temperature, (int, float)) or not (
            40.0 <= float(temperature) <= 60.0
        ):
            raise ValueError(
                f"Invalid DHW target temperature {temperature}; temperature must be between 40 and 60 °C."
            )

        return await self.async_patch_control_board_settings(
            control_board_id=control_board_id,
            token=token,
            settings={"DhwTargetTemperature": round(float(temperature), 1)},
            update_immediately=False,
        )

    # -------------------------------------------------------------------------
    # Asynchronous Status Polling
    # -------------------------------------------------------------------------

    async def async_get_settings_request_status(
        self,
        control_board_id: str,
        request_id: str,
        token: str | None = None,
    ) -> dict[str, Any]:
        """Query single-shot status of a settings request.

        Args:
            control_board_id: Target control board ID.
            request_id: Settings request UUID.
            token: Access token for Bearer authorization.

        Returns:
            Status response dictionary.
        """
        if not control_board_id or not request_id:
            raise ValueError("control_board_id and request_id must not be empty")

        url = (
            f"{self._portal_base_url}/api/v1/control-boards/"
            f"{control_board_id.strip()}/settings/{request_id.strip()}"
        )
        return await self._request("GET", url, token=token)

    async def async_poll_settings_request(
        self,
        control_board_id: str,
        request_id: str,
        token: str | None = None,
        timeout_seconds: float = 30.0,
        poll_interval: float = 1.5,
    ) -> dict[str, Any]:
        """Poll settings request until completion or failure.

        Status:
            0: Pending (continue)
            1: Executing (continue)
            2: Succeeded (return)
            3..11: Terminal failure (raise WeheatApiError)

        Args:
            control_board_id: Target control board ID.
            request_id: Settings request UUID.
            token: Access token for Bearer authorization.
            timeout_seconds: Maximum time to wait in seconds.
            poll_interval: Delay between poll attempts in seconds.

        Returns:
            The successful settings request status dictionary.

        Raises:
            WeheatApiError: If request fails.
            WeheatTimeoutError: If timeout expires before terminal state.
        """
        loop = asyncio.get_running_loop()
        start_time = loop.time()

        while True:
            data = await self.async_get_settings_request_status(
                control_board_id=control_board_id,
                request_id=request_id,
                token=token,
            )
            status = data.get("status")

            if status not in (0, 1):
                if status in (2, 5):
                    return data
                status_desc = SETTINGS_REQUEST_STATUS_MAP.get(
                    status, f"UnknownStatus({status})"
                )
                raise WeheatApiError(
                    f"Settings request failed with status {status} ({status_desc}) for request {request_id}",
                    status_code=None,
                    response_data=data,
                )

            elapsed = loop.time() - start_time
            if elapsed >= timeout_seconds:
                raise WeheatTimeoutError(
                    f"Settings request {request_id} timed out after {timeout_seconds}s (status={status})"
                )

            sleep_duration = min(poll_interval, max(0.0, timeout_seconds - elapsed))
            await asyncio.sleep(sleep_duration)

    # -------------------------------------------------------------------------
    # Variables Mutation Methods
    # -------------------------------------------------------------------------

    async def async_patch_control_board_variables(
        self,
        control_board_id: str,
        token: str | None = None,
        variables: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Dispatch variable mutations to /api/v1/control-boards/{id}/variables.

        Args:
            control_board_id: Target control board ID.
            token: Access token for Bearer authorization.
            variables: Dictionary of variables to patch.

        Returns:
            Response dictionary.
        """
        if not control_board_id or not control_board_id.strip():
            raise ValueError("control_board_id must not be empty")
        if not variables:
            raise ValueError("variables dictionary must not be empty")

        url = (
            f"{self._portal_base_url}/api/v1/control-boards/"
            f"{control_board_id.strip()}/variables"
        )
        return await self._request(
            "PATCH", url, token=token, json={"variables": variables}
        )

    async def async_trigger_legionella_prevention(
        self,
        control_board_id: str,
        token: str | None = None,
    ) -> bool:
        """Trigger Legionella prevention cycle (RequestLegionellaPrevention: 1)."""
        res = await self.async_patch_control_board_variables(
            control_board_id=control_board_id,
            token=token,
            variables={"RequestLegionellaPrevention": 1},
        )
        return bool(res.get("success", True))

    async def async_skip_legionella_prevention(
        self,
        control_board_id: str,
        token: str | None = None,
    ) -> bool:
        """Skip Legionella prevention cycle (Hpc_skip_legionella_prevention: 1)."""
        res = await self.async_patch_control_board_variables(
            control_board_id=control_board_id,
            token=token,
            variables={"Hpc_skip_legionella_prevention": 1},
        )
        return bool(res.get("success", True))

    # -------------------------------------------------------------------------
    # Domestic Hot Water Boost Action
    # -------------------------------------------------------------------------

    async def async_trigger_dhw_boost(
        self,
        heat_pump_uuid: str | None = None,
        heat_pump_id: str | None = None,
        token: str | None = None,
        duration_minutes: int = 60,
        target_temperature: float = 60.0,
    ) -> dict[str, Any]:
        """Trigger DHW boost (duration in 30, 60, 90, 120; temp 40..65°C).

        Args:
            heat_pump_uuid: Heat pump UUID (keyword alias).
            heat_pump_id: Heat pump UUID (keyword alias).
            token: Access token for Bearer authorization.
            duration_minutes: Boost duration in minutes (30, 60, 90, or 120).
            target_temperature: Target water temperature (40.0 to 65.0 °C).

        Returns:
            Dictionary matching boost_trigger_success.json schema.
        """
        target = (heat_pump_uuid or heat_pump_id or "").strip()
        if not target:
            raise ValueError("heat_pump_uuid or heat_pump_id must be provided")

        if duration_minutes not in (30, 60, 90, 120):
            raise ValueError(
                f"Invalid boost duration {duration_minutes}. Must be 30, 60, 90, or 120."
            )
        if not (40.0 <= float(target_temperature) <= 65.0):
            raise ValueError(
                f"Invalid boost temperature {target_temperature}. Must be 40.0..65.0 °C."
            )

        url = f"{self._portal_base_url}/api/v1/heat-pumps/{target}/dhw/boost"
        payload = {
            "durationMinutes": duration_minutes,
            "targetTemperature": float(target_temperature),
        }
        return await self._request("POST", url, token=token, json=payload)
