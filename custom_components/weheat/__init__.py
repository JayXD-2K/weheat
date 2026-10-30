"""The Weheat custom integration."""

from __future__ import annotations

import asyncio
import logging
from http import HTTPStatus

import aiohttp
from aiohttp import ClientError
from homeassistant.const import CONF_ACCESS_TOKEN, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.config_entry_oauth2_flow import (
    OAuth2Session,
    async_get_config_entry_implementation,
)

from .client import (
    WeheatApiError,
    WeheatAuthError,
    WeheatClient,
    WeheatTimeoutError,
)
from .const import DEFAULT_API_BASE_URL, DEFAULT_PORTAL_BASE_URL
from .coordinator import (
    WeheatConfigEntry,
    WeheatData,
    WeheatDataUpdateCoordinator,
    WeheatEnergyUpdateCoordinator,
)

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: WeheatConfigEntry) -> bool:
    """Set up Weheat from a config entry."""
    implementation = await async_get_config_entry_implementation(hass, entry)
    session = OAuth2Session(hass, entry, implementation)

    try:
        await session.async_ensure_token_valid()
    except aiohttp.ClientResponseError as ex:
        if ex.status in (
            HTTPStatus.BAD_REQUEST,
            HTTPStatus.UNAUTHORIZED,
            HTTPStatus.FORBIDDEN,
        ):
            raise ConfigEntryAuthFailed("Token not valid, trigger renewal") from ex
        raise ConfigEntryNotReady(f"API error: {ex.status} ({ex.message})") from ex
    except ClientError as ex:
        raise ConfigEntryNotReady(f"Connection error to Weheat auth: {ex}") from ex

    token = session.token[CONF_ACCESS_TOKEN]
    entry.runtime_data = []

    client = WeheatClient(
        session=async_get_clientsession(hass),
        base_url=DEFAULT_API_BASE_URL,
        portal_base_url=DEFAULT_PORTAL_BASE_URL,
    )

    try:
        discovered_heat_pumps = await client.async_get_heat_pumps(token)
    except WeheatAuthError as err:
        raise ConfigEntryAuthFailed(
            f"Authentication failed during discovery: {err}"
        ) from err
    except (WeheatApiError, WeheatTimeoutError, ClientError) as err:
        raise ConfigEntryNotReady(
            f"Failed to communicate with Weheat API during discovery: {err}"
        ) from err

    nr_of_pumps = len(discovered_heat_pumps)

    for pump_info in discovered_heat_pumps:
        _LOGGER.debug(
            "Adding Weheat heat pump %s (%s)",
            pump_info.heatpump_id,
            pump_info.readable_name,
        )

        data_coordinator = WeheatDataUpdateCoordinator(
            hass, entry, session, pump_info, nr_of_pumps, client=client
        )
        energy_coordinator = WeheatEnergyUpdateCoordinator(
            hass, entry, session, pump_info, client=client
        )

        entry.runtime_data.append(
            WeheatData(
                client=client,
                session=session,
                heat_pump_info=pump_info,
                data_coordinator=data_coordinator,
                energy_coordinator=energy_coordinator,
            )
        )

    if entry.runtime_data:
        await asyncio.gather(
            *[
                data.data_coordinator.async_config_entry_first_refresh()
                for data in entry.runtime_data
            ],
            *[
                data.energy_coordinator.async_config_entry_first_refresh()
                for data in entry.runtime_data
            ],
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: WeheatConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
