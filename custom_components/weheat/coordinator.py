"""Define data update coordinators for the Weheat integration."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import TYPE_CHECKING

from aiohttp import ClientError, ClientResponseError
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .client import (
    WeheatApiError,
    WeheatAuthError,
    WeheatClient,
    WeheatTimeoutError,
)
from .const import DOMAIN, ENERGY_UPDATE_INTERVAL, LOG_UPDATE_INTERVAL, LOGGER
from .models import (
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


@dataclass
class WeheatData:
    """Data container holding coordinators and client for a single heat pump."""

    heat_pump_info: HeatPumpInfo
    data_coordinator: WeheatDataUpdateCoordinator
    energy_coordinator: WeheatEnergyUpdateCoordinator
    client: WeheatClient | None = None
    session: OAuth2Session | None = None
