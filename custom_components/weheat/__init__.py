"""The Weheat custom integration."""

from __future__ import annotations

import asyncio
import inspect
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
from homeassistant.helpers.update_coordinator import UpdateFailed

from .client import (
    WeheatApiError,
    WeheatAuthError,
    WeheatClient,
    WeheatError,
    WeheatTimeoutError,
)
from .const import DEFAULT_API_BASE_URL, DEFAULT_PORTAL_BASE_URL, DOMAIN
from .coordinator import (
    WeheatConfigEntry,
    WeheatData,
    WeheatDataUpdateCoordinator,
    WeheatEnergyUpdateCoordinator,
    WeheatPortalSession,
    WeheatSettingsUpdateCoordinator,
)
from .models import ControlBoardSettings
from .services import async_setup_services, async_unload_services

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SELECT,
    Platform.SWITCH,
    Platform.TIME,
]


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
    control_session = WeheatPortalSession(hass, entry, client, session)

    if control_session.has_portal_auth:
        try:
            await control_session.async_ensure_token_valid()
            portal_token = (
                control_session.token.get(CONF_ACCESS_TOKEN)
                if isinstance(control_session.token, dict)
                else None
            )
            topology_items = await client.async_get_installation_topology(
                token=portal_token
            )
            for item in topology_items:
                hp_nested = item.get("heatPump")
                hp_dict = hp_nested if isinstance(hp_nested, dict) else {}
                cb_id = item.get("controlBoardId") or hp_dict.get("controlBoardId")
                hp_id = item.get("heatPumpId") or hp_dict.get("id") or item.get("id")
                inst_id = item.get("installationId") or (
                    item.get("id") if hp_dict or item.get("id") != hp_id else None
                )
                for pump_info in discovered_heat_pumps:
                    if str(pump_info.heatpump_id) == str(hp_id) or (
                        len(discovered_heat_pumps) == 1
                        and not pump_info.control_board_id
                    ):
                        if cb_id and not pump_info.control_board_id:
                            pump_info.control_board_id = str(cb_id)
                        if inst_id and not pump_info.installation_id:
                            pump_info.installation_id = str(inst_id)
        except Exception as err:
            _LOGGER.debug("Safe topology discovery skipped during setup: %s", err)

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
        settings_coordinator = WeheatSettingsUpdateCoordinator(
            hass, entry, control_session, pump_info, client=client
        )

        entry.runtime_data.append(
            WeheatData(
                client=client,
                session=control_session,
                heat_pump_info=pump_info,
                data_coordinator=data_coordinator,
                energy_coordinator=energy_coordinator,
                settings_coordinator=settings_coordinator,
            )
        )

    async def _async_refresh_settings_safe(
        coord: WeheatSettingsUpdateCoordinator,
    ) -> None:
        """Refresh portal settings non-fatally so read-only OAuth tokens still load telemetry."""
        try:
            await coord.async_config_entry_first_refresh()
        except (
            ConfigEntryAuthFailed,
            ConfigEntryNotReady,
            UpdateFailed,
            WeheatError,
            ClientError,
        ) as err:
            _LOGGER.warning(
                "Portal settings refresh skipped during setup (%s). "
                "If using the read-only HomeAssistantAPI OAuth client, configure "
                "Weheat Portal credentials in the integration's Configure options "
                "to enable quiet mode, silent schedules, and control actions.",
                err,
            )
            if coord.data is None:
                coord.data = ControlBoardSettings()

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
            *[
                _async_refresh_settings_safe(data.settings_coordinator)
                for data in entry.runtime_data
                if data.settings_coordinator is not None
            ],
        )

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await async_setup_services(hass)
    entry.async_on_unload(entry.add_update_listener(async_reload_entry))
    return True


async def async_reload_entry(hass: HomeAssistant, entry: WeheatConfigEntry) -> None:
    """Reload config entry when options are updated."""
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: WeheatConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        # Cancel coordinator intervals and detach background listeners
        for data in getattr(entry, "runtime_data", []) or []:
            for coord in (
                getattr(data, "data_coordinator", None),
                getattr(data, "energy_coordinator", None),
                getattr(data, "settings_coordinator", None),
            ):
                if coord is not None and hasattr(coord, "async_shutdown"):
                    shutdown_fn = coord.async_shutdown
                    if callable(shutdown_fn):
                        res = shutdown_fn()
                        if inspect.isawaitable(res):
                            await res

        # Clear runtime_data to eliminate stale coordinator references
        if hasattr(entry, "runtime_data"):
            entry.runtime_data = []

        remaining_entries = [
            e
            for e in getattr(hass.config_entries, "async_entries", lambda _: [])(DOMAIN)
            if e.entry_id != entry.entry_id
        ]
        if not remaining_entries:
            await async_unload_services(hass)
    return unload_ok
