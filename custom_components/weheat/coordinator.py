"""Define data update coordinators for the Weheat integration."""

from __future__ import annotations

import contextlib
import time
from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from aiohttp import ClientError, ClientResponseError
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import (
    WeheatApiError,
    WeheatAuthError,
    WeheatClient,
    WeheatError,
    WeheatTimeoutError,
)
from .const import (
    CONF_PORTAL_PASSWORD,
    CONF_PORTAL_TOKEN,
    CONF_PORTAL_USERNAME,
    DOMAIN,
    ENERGY_UPDATE_INTERVAL,
    LOG_UPDATE_INTERVAL,
    LOGGER,
    SETTINGS_UPDATE_INTERVAL,
)
from .models import (
    ControlBoardSettings,
    HeatPumpEnergy,
    HeatPumpInfo,
    HeatPumpLog,
)

if TYPE_CHECKING:
    from homeassistant.helpers.config_entry_oauth2_flow import OAuth2Session

type WeheatConfigEntry = ConfigEntry[list[WeheatData]]


class WeheatDataUpdateCoordinator(DataUpdateCoordinator[HeatPumpLog]):
    """Data update coordinator for high-frequency operational telemetry logs."""

    config_entry: WeheatConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: WeheatConfigEntry,
        session: OAuth2Session | None = None,
        heat_pump_info: HeatPumpInfo | None = None,
        nr_of_heat_pumps: int = 1,
        client: WeheatClient | None = None,
    ) -> None:
        """Initialize the operational logs data coordinator.

        Args:
            hass: Home Assistant core instance.
            config_entry: Configuration entry.
            session: Optional OAuth2Session for token refresh.
            heat_pump_info: Discovery metadata for this heat pump.
            nr_of_heat_pumps: Total number of active heat pumps on account (scales interval).
            client: Optional WeheatClient instance (instantiated if not provided).
        """
        interval_seconds = LOG_UPDATE_INTERVAL * max(1, nr_of_heat_pumps)
        super().__init__(
            hass,
            logger=getattr(hass, "logger", None) or LOGGER,
            name=DOMAIN,
            config_entry=config_entry,
            update_interval=timedelta(seconds=interval_seconds),
        )
        self.session = session
        self.heat_pump_info = heat_pump_info
        self.nr_of_heat_pumps = max(1, nr_of_heat_pumps)
        self.client = client or WeheatClient(session=async_get_clientsession(hass))

    async def _async_update_data(self) -> HeatPumpLog:
        """Fetch latest telemetry log data from the Weheat API.

        Returns:
            Populated HeatPumpLog snapshot.

        Raises:
            ConfigEntryAuthFailed: If token renewal fails or server rejects auth.
            UpdateFailed: If API or network connection errors occur.
        """
        token: str | None = None
        if self.session is not None:
            try:
                await self.session.async_ensure_token_valid()
                token = self.session.token.get(CONF_ACCESS_TOKEN)
            except ClientResponseError as err:
                if err.status in (400, 401, 403):
                    raise ConfigEntryAuthFailed(
                        "OAuth token invalid or expired"
                    ) from err
                raise UpdateFailed(f"Error refreshing OAuth token: {err}") from err
            except Exception as err:
                raise UpdateFailed(
                    f"Failed to ensure valid OAuth token: {err}"
                ) from err

        if not self.heat_pump_info:
            raise UpdateFailed("Heat pump information is missing from coordinator")

        try:
            log_data = await self.client.async_get_latest_logs(
                self.heat_pump_info.heatpump_id, token=token
            )
            # Propagate nominal compressor RPM for percentage calculations
            if hasattr(log_data, "nominal_rpm"):
                log_data.nominal_rpm = self.heat_pump_info.nominal_rpm
            return log_data
        except WeheatAuthError as err:
            raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
        except (WeheatApiError, WeheatTimeoutError, ClientError) as err:
            raise UpdateFailed(f"Error communicating with Weheat API: {err}") from err


class WeheatEnergyUpdateCoordinator(DataUpdateCoordinator[HeatPumpEnergy]):
    """Data update coordinator for cumulative energy totals (polled every 30 minutes)."""

    config_entry: WeheatConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: WeheatConfigEntry,
        session: OAuth2Session | None = None,
        heat_pump_info: HeatPumpInfo | None = None,
        client: WeheatClient | None = None,
    ) -> None:
        """Initialize the energy update coordinator.

        Args:
            hass: Home Assistant core instance.
            config_entry: Configuration entry.
            session: Optional OAuth2Session for token refresh.
            heat_pump_info: Discovery metadata for this heat pump.
            client: Optional WeheatClient instance (instantiated if not provided).
        """
        super().__init__(
            hass,
            logger=getattr(hass, "logger", None) or LOGGER,
            name=DOMAIN,
            config_entry=config_entry,
            update_interval=timedelta(seconds=ENERGY_UPDATE_INTERVAL),
        )
        self.session = session
        self.heat_pump_info = heat_pump_info
        self.client = client or WeheatClient(session=async_get_clientsession(hass))

    async def _async_update_data(self) -> HeatPumpEnergy:
        """Fetch cumulative energy totals from the Weheat API.

        Returns:
            Populated HeatPumpEnergy snapshot.

        Raises:
            ConfigEntryAuthFailed: If token renewal fails or server rejects auth.
            UpdateFailed: If API or network connection errors occur.
        """
        token: str | None = None
        if self.session is not None:
            try:
                await self.session.async_ensure_token_valid()
                token = self.session.token.get(CONF_ACCESS_TOKEN)
            except ClientResponseError as err:
                if err.status in (400, 401, 403):
                    raise ConfigEntryAuthFailed(
                        "OAuth token invalid or expired"
                    ) from err
                raise UpdateFailed(f"Error refreshing OAuth token: {err}") from err
            except Exception as err:
                raise UpdateFailed(
                    f"Failed to ensure valid OAuth token: {err}"
                ) from err

        if not self.heat_pump_info:
            raise UpdateFailed("Heat pump information is missing from coordinator")

        try:
            return await self.client.async_get_energy_total(
                self.heat_pump_info.heatpump_id, token=token
            )
        except WeheatAuthError as err:
            raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
        except (WeheatApiError, WeheatTimeoutError, ClientError) as err:
            raise UpdateFailed(f"Error communicating with Weheat API: {err}") from err


class WeheatSettingsUpdateCoordinator(DataUpdateCoordinator[ControlBoardSettings]):
    """Data update coordinator for control board and installation settings.

    Polls /api/v1/installations/{installation_id}/settings every 5 minutes (300s)
    and supports immediate on-demand refresh after control mutations.
    """

    config_entry: WeheatConfigEntry

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: WeheatConfigEntry,
        session: OAuth2Session | None = None,
        heat_pump_info: HeatPumpInfo | None = None,
        client: WeheatClient | None = None,
    ) -> None:
        """Initialize the settings update coordinator.

        Args:
            hass: Home Assistant core instance.
            config_entry: Configuration entry.
            session: Optional OAuth2Session for token refresh.
            heat_pump_info: Discovery metadata for this heat pump.
            client: Optional WeheatClient instance (instantiated if not provided).
        """
        super().__init__(
            hass,
            logger=getattr(hass, "logger", None) or LOGGER,
            name=f"{DOMAIN}_settings",
            config_entry=config_entry,
            update_interval=timedelta(seconds=SETTINGS_UPDATE_INTERVAL),
        )
        self.session = session
        self.heat_pump_info = heat_pump_info
        self.client = client or WeheatClient(session=async_get_clientsession(hass))

    async def _async_update_data(self) -> ControlBoardSettings:
        """Fetch control board and installation settings from the Weheat API.

        Returns:
            Populated ControlBoardSettings instance.

        Raises:
            ConfigEntryAuthFailed: If token renewal fails or server rejects auth.
            UpdateFailed: If API or network connection errors occur.
        """
        token: str | None = None
        if self.session is not None:
            try:
                await self.session.async_ensure_token_valid()
                token = self.session.token.get(CONF_ACCESS_TOKEN)
            except ClientResponseError as err:
                if err.status in (400, 401, 403):
                    raise ConfigEntryAuthFailed(
                        "OAuth token invalid or expired"
                    ) from err
                raise UpdateFailed(f"Error refreshing OAuth token: {err}") from err
            except Exception as err:
                raise UpdateFailed(
                    f"Failed to ensure valid OAuth token: {err}"
                ) from err

        if not self.heat_pump_info:
            raise UpdateFailed("Heat pump information is missing from coordinator")

        installation_id = self.heat_pump_info.installation_id
        if not installation_id:
            # Fallback: discover topology from portal
            try:
                topology = await self.client.async_get_installation_topology(
                    token=token
                )
                for item in topology:
                    hp_nested = item.get("heatPump")
                    hp_dict = hp_nested if isinstance(hp_nested, dict) else {}
                    hp_id = (
                        item.get("heatPumpId") or hp_dict.get("id") or item.get("id")
                    )
                    if hp_id == self.heat_pump_info.heatpump_id:
                        installation_id = item.get("installationId") or item.get("id")
                        self.heat_pump_info.installation_id = installation_id
                        cb_id = item.get("controlBoardId") or hp_dict.get(
                            "controlBoardId"
                        )
                        if cb_id and not self.heat_pump_info.control_board_id:
                            self.heat_pump_info.control_board_id = str(cb_id)
                        break
            except (
                WeheatAuthError,
                WeheatApiError,
                WeheatTimeoutError,
                ClientError,
            ) as err:
                LOGGER.warning("Could not resolve installation topology: %s", err)

        if not installation_id:
            raise UpdateFailed(
                f"Missing installation ID for heat pump {self.heat_pump_info.heatpump_id}"
            )

        try:
            return await self.client.async_get_installation_settings(
                installation_id, token=token
            )
        except WeheatAuthError as err:
            if err.status_code == 403:
                raise UpdateFailed(
                    f"Portal settings access forbidden (HTTP 403). Configure Weheat Portal credentials in integration options for settings/control access: {err}"
                ) from err
            raise ConfigEntryAuthFailed(f"Authentication failed: {err}") from err
        except (WeheatApiError, WeheatTimeoutError, ClientError) as err:
            raise UpdateFailed(f"Error communicating with Weheat API: {err}") from err


class WeheatPortalSession:
    """Session manager for weheat-portal OAuth2 PKCE tokens with OAuth2Session fallback."""

    def __init__(
        self,
        hass: HomeAssistant,
        config_entry: ConfigEntry[Any],
        client: WeheatClient,
        fallback_session: OAuth2Session | None = None,
    ) -> None:
        """Initialize the portal session wrapper."""
        self.hass = hass
        self.config_entry = config_entry
        self.client = client
        self.fallback_session = fallback_session
        self._portal_token: dict[str, Any] | None = self._extract_portal_token()

    def _extract_portal_token(self) -> dict[str, Any] | None:
        """Extract portal token dict from config entry options or data."""
        options = getattr(self.config_entry, "options", None) or {}
        data = getattr(self.config_entry, "data", None) or {}
        raw_tok = options.get(CONF_PORTAL_TOKEN) or data.get(CONF_PORTAL_TOKEN)
        if isinstance(raw_tok, dict):
            access_tok = raw_tok.get(CONF_ACCESS_TOKEN) or raw_tok.get("accessToken")
            if not access_tok and isinstance(raw_tok.get("token"), dict):
                access_tok = raw_tok["token"].get(CONF_ACCESS_TOKEN) or raw_tok[
                    "token"
                ].get("accessToken")
            if access_tok:
                tok_dict = dict(raw_tok)
                tok_dict[CONF_ACCESS_TOKEN] = str(access_tok)
                return tok_dict
        elif isinstance(raw_tok, str) and raw_tok.strip():
            try:
                return WeheatClient.parse_portal_session_input(raw_tok)
            except Exception:
                pass
        return None

    def _get_portal_credentials(self) -> tuple[str | None, str | None]:
        """Extract optional saved portal username and password."""
        options = getattr(self.config_entry, "options", None) or {}
        data = getattr(self.config_entry, "data", None) or {}
        username = options.get(CONF_PORTAL_USERNAME) or data.get(CONF_PORTAL_USERNAME)
        password = options.get(CONF_PORTAL_PASSWORD) or data.get(CONF_PORTAL_PASSWORD)
        return (
            str(username).strip() if username else None,
            str(password).strip() if password else None,
        )

    @property
    def has_portal_auth(self) -> bool:
        """Return True if portal token or portal credentials are configured."""
        if self._portal_token and self._portal_token.get(CONF_ACCESS_TOKEN):
            return True
        if self._extract_portal_token() is not None:
            return True
        username, password = self._get_portal_credentials()
        return bool(username and password)

    @property
    def token(self) -> dict[str, Any]:
        """Return the active token dictionary."""
        if not (self._portal_token and self._portal_token.get(CONF_ACCESS_TOKEN)):
            stored = self._extract_portal_token()
            if stored and stored.get(CONF_ACCESS_TOKEN):
                self._portal_token = stored

        if self._portal_token and self._portal_token.get(CONF_ACCESS_TOKEN):
            return self._portal_token
        if self.fallback_session is not None:
            return self.fallback_session.token
        return {}

    def _persist_portal_token(self, new_token: dict[str, Any]) -> None:
        """Persist updated portal token into memory and config entry options."""
        self._portal_token = dict(new_token)
        options = dict(getattr(self.config_entry, "options", None) or {})
        options[CONF_PORTAL_TOKEN] = dict(new_token)
        if hasattr(self.config_entry, "options") and isinstance(
            self.config_entry.options, dict
        ):
            self.config_entry.options[CONF_PORTAL_TOKEN] = dict(new_token)
        if hasattr(self.hass, "config_entries") and hasattr(
            self.hass.config_entries, "async_update_entry"
        ):
            with contextlib.suppress(Exception):
                self.hass.config_entries.async_update_entry(
                    self.config_entry, options=options
                )

    def check_portal_auth(self) -> None:
        """Verify portal authentication is configured before write operations.

        Raises:
            HomeAssistantError: If portal credentials or token are not configured.
        """
        if not self.has_portal_auth:
            raise HomeAssistantError(
                "Weheat Portal credentials required for control actions. "
                "Please configure your Weheat Portal account in the integration Configure options."
            )

    async def async_ensure_token_valid(self) -> None:
        """Ensure portal token (or fallback OAuth2Session token) is valid."""
        latest_stored = self._extract_portal_token()
        if latest_stored is not None:
            self._portal_token = latest_stored

        username, password = self._get_portal_credentials()
        has_auth = (self._portal_token is not None) or bool(username and password)

        if not has_auth:
            if self.fallback_session is not None:
                await self.fallback_session.async_ensure_token_valid()
            return

        now = time.time()
        if self._portal_token is not None:
            expires_at = float(self._portal_token.get("expires_at", 0.0))
            if self._portal_token.get(CONF_ACCESS_TOKEN) and expires_at > now + 30.0:
                return

            refresh_token = self._portal_token.get("refresh_token")
            if refresh_token:
                try:
                    refreshed = await self.client.async_refresh_portal_token(
                        str(refresh_token)
                    )
                    self._persist_portal_token(refreshed)
                    return
                except WeheatError as err:
                    LOGGER.debug(
                        "Portal refresh_token renewal failed (%s), checking PKCE fallback",
                        err,
                    )
                    if not (username and password):
                        raise

        if username and password:
            new_token = await self.client.async_login_portal_pkce(username, password)
            self._persist_portal_token(new_token)
            return

        if self.fallback_session is not None:
            await self.fallback_session.async_ensure_token_valid()


@dataclass
class WeheatData:
    """Data container holding coordinators and client for a single heat pump."""

    heat_pump_info: HeatPumpInfo
    data_coordinator: WeheatDataUpdateCoordinator
    energy_coordinator: WeheatEnergyUpdateCoordinator
    settings_coordinator: WeheatSettingsUpdateCoordinator | None = None
    client: WeheatClient | None = None
    session: OAuth2Session | WeheatPortalSession | None = None
