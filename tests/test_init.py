"""Tests for Weheat integration entry setup and unload lifecycle."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientError, ClientResponseError
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from custom_components.weheat import (
    PLATFORMS,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.weheat.client import WeheatApiError, WeheatAuthError
from custom_components.weheat.models import HeatPumpInfo


@pytest.fixture
def mock_heat_pump_info() -> HeatPumpInfo:
    return HeatPumpInfo(
        heatpump_id="00000000-0000-0000-0000-000000000001",
        name="Test Heat Pump",
        model=0,
        serial_number="WH-001",
        has_dhw=True,
    )


@pytest.fixture
def mock_config_entry() -> ConfigEntry:
    return ConfigEntry(
        entry_id="test_entry_id",
        data={CONF_TOKEN: {CONF_ACCESS_TOKEN: "mock_access_token_123"}},
        title="Weheat cloud",
    )


@pytest.mark.asyncio
async def test_setup_entry_success(
    mock_config_entry: ConfigEntry, mock_heat_pump_info: HeatPumpInfo
) -> None:
    """Verify successful entry setup, coordinator refresh, and platform forwarding."""
    hass = HomeAssistant()

    with (
        patch(
            "custom_components.weheat.OAuth2Session.async_ensure_token_valid",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.weheat.WeheatClient.async_get_heat_pumps",
            new=AsyncMock(return_value=[mock_heat_pump_info]),
        ),
        patch(
            "custom_components.weheat.coordinator.WeheatDataUpdateCoordinator.async_config_entry_first_refresh",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.weheat.coordinator.WeheatEnergyUpdateCoordinator.async_config_entry_first_refresh",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.weheat.coordinator.WeheatSettingsUpdateCoordinator.async_config_entry_first_refresh",
            new=AsyncMock(),
        ),
    ):
        result = await async_setup_entry(hass, mock_config_entry)

    assert result is True
    assert len(mock_config_entry.runtime_data) == 1
    assert mock_config_entry.runtime_data[0].heat_pump_info is mock_heat_pump_info
    assert len(hass.config_entries.forwarded_setups) == 1
    assert hass.config_entries.forwarded_setups[0][1] == PLATFORMS


@pytest.mark.asyncio
async def test_setup_entry_empty_pumps(mock_config_entry: ConfigEntry) -> None:
    """Verify entry setup when no heat pumps are associated with account."""
    hass = HomeAssistant()

    with (
        patch(
            "custom_components.weheat.OAuth2Session.async_ensure_token_valid",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.weheat.WeheatClient.async_get_heat_pumps",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await async_setup_entry(hass, mock_config_entry)

    assert result is True
    assert len(mock_config_entry.runtime_data) == 0
    assert len(hass.config_entries.forwarded_setups) == 1


@pytest.mark.asyncio
async def test_setup_entry_token_auth_failed(mock_config_entry: ConfigEntry) -> None:
    """Verify ConfigEntryAuthFailed on HTTP 401 token refresh."""
    hass = HomeAssistant()

    err = ClientResponseError(
        request_info=MagicMock(), history=(), status=401, message="Unauthorized"
    )

    with (
        patch(
            "custom_components.weheat.OAuth2Session.async_ensure_token_valid",
            new=AsyncMock(side_effect=err),
        ),
        pytest.raises(ConfigEntryAuthFailed),
    ):
        await async_setup_entry(hass, mock_config_entry)


@pytest.mark.asyncio
async def test_setup_entry_token_server_error(mock_config_entry: ConfigEntry) -> None:
    """Verify ConfigEntryNotReady on HTTP 500 token refresh."""
    hass = HomeAssistant()

    err = ClientResponseError(
        request_info=MagicMock(), history=(), status=500, message="Server error"
    )

    with (
        patch(
            "custom_components.weheat.OAuth2Session.async_ensure_token_valid",
            new=AsyncMock(side_effect=err),
        ),
        pytest.raises(ConfigEntryNotReady),
    ):
        await async_setup_entry(hass, mock_config_entry)


@pytest.mark.asyncio
async def test_setup_entry_token_client_error(mock_config_entry: ConfigEntry) -> None:
    """Verify ConfigEntryNotReady on ClientError during token refresh."""
    hass = HomeAssistant()

    with (
        patch(
            "custom_components.weheat.OAuth2Session.async_ensure_token_valid",
            new=AsyncMock(side_effect=ClientError("Network down")),
        ),
        pytest.raises(ConfigEntryNotReady),
    ):
        await async_setup_entry(hass, mock_config_entry)


@pytest.mark.asyncio
async def test_setup_entry_discovery_auth_failed(
    mock_config_entry: ConfigEntry,
) -> None:
    """Verify ConfigEntryAuthFailed on WeheatAuthError during discovery."""
    hass = HomeAssistant()

    with (
        patch(
            "custom_components.weheat.OAuth2Session.async_ensure_token_valid",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.weheat.WeheatClient.async_get_heat_pumps",
            new=AsyncMock(side_effect=WeheatAuthError("403 Forbidden")),
        ),
        pytest.raises(ConfigEntryAuthFailed),
    ):
        await async_setup_entry(hass, mock_config_entry)


@pytest.mark.asyncio
async def test_setup_entry_discovery_api_failed(
    mock_config_entry: ConfigEntry,
) -> None:
    """Verify ConfigEntryNotReady on WeheatApiError during discovery."""
    hass = HomeAssistant()

    with (
        patch(
            "custom_components.weheat.OAuth2Session.async_ensure_token_valid",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.weheat.WeheatClient.async_get_heat_pumps",
            new=AsyncMock(side_effect=WeheatApiError("503 Service Unavailable")),
        ),
        pytest.raises(ConfigEntryNotReady),
    ):
        await async_setup_entry(hass, mock_config_entry)


@pytest.mark.asyncio
async def test_unload_entry(mock_config_entry: ConfigEntry) -> None:
    """Verify unload entry delegates to async_unload_platforms."""
    hass = HomeAssistant()

    result = await async_unload_entry(hass, mock_config_entry)
    assert result is True
    assert len(hass.config_entries.unloaded_platforms) == 1
    assert hass.config_entries.unloaded_platforms[0][1] == PLATFORMS


@pytest.mark.asyncio
async def test_setup_entry_settings_403_non_fatal(
    mock_config_entry: ConfigEntry, mock_heat_pump_info: HeatPumpInfo
) -> None:
    """Verify 403/UpdateFailed on settings coordinator does not fail integration setup."""
    hass = HomeAssistant()

    with (
        patch(
            "custom_components.weheat.OAuth2Session.async_ensure_token_valid",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.weheat.WeheatClient.async_get_heat_pumps",
            new=AsyncMock(return_value=[mock_heat_pump_info]),
        ),
        patch(
            "custom_components.weheat.coordinator.WeheatDataUpdateCoordinator.async_config_entry_first_refresh",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.weheat.coordinator.WeheatEnergyUpdateCoordinator.async_config_entry_first_refresh",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.weheat.coordinator.WeheatSettingsUpdateCoordinator.async_config_entry_first_refresh",
            new=AsyncMock(side_effect=ConfigEntryAuthFailed("403 Forbidden on portal")),
        ),
    ):
        result = await async_setup_entry(hass, mock_config_entry)

    assert result is True
    assert len(mock_config_entry.runtime_data) == 1
    assert mock_config_entry.runtime_data[0].settings_coordinator.data is not None


@pytest.mark.asyncio
async def test_setup_entry_registers_update_listener(
    mock_config_entry: ConfigEntry, mock_heat_pump_info: HeatPumpInfo
) -> None:
    """Verify entry setup attaches options update listener and on_unload hook."""
    hass = HomeAssistant()

    with (
        patch(
            "custom_components.weheat.OAuth2Session.async_ensure_token_valid",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.weheat.WeheatClient.async_get_heat_pumps",
            new=AsyncMock(return_value=[mock_heat_pump_info]),
        ),
        patch(
            "custom_components.weheat.coordinator.WeheatDataUpdateCoordinator.async_config_entry_first_refresh",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.weheat.coordinator.WeheatEnergyUpdateCoordinator.async_config_entry_first_refresh",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.weheat.coordinator.WeheatSettingsUpdateCoordinator.async_config_entry_first_refresh",
            new=AsyncMock(),
        ),
    ):
        result = await async_setup_entry(hass, mock_config_entry)

    assert result is True
    assert len(mock_config_entry.update_listeners) == 1
    assert len(mock_config_entry._on_unload) >= 1


@pytest.mark.asyncio
async def test_async_reload_entry_calls_async_reload(
    mock_config_entry: ConfigEntry,
) -> None:
    """Verify async_reload_entry requests Home Assistant to reload the entry ID."""
    hass = HomeAssistant()
    from custom_components.weheat import async_reload_entry

    await async_reload_entry(hass, mock_config_entry)
    assert hass.config_entries.reloaded_entries == [mock_config_entry.entry_id]


@pytest.mark.asyncio
async def test_options_update_triggers_reload(
    mock_config_entry: ConfigEntry, mock_heat_pump_info: HeatPumpInfo
) -> None:
    """Verify modifying options triggers the reload listener."""
    hass = HomeAssistant()

    with (
        patch(
            "custom_components.weheat.OAuth2Session.async_ensure_token_valid",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.weheat.WeheatClient.async_get_heat_pumps",
            new=AsyncMock(return_value=[mock_heat_pump_info]),
        ),
        patch(
            "custom_components.weheat.coordinator.WeheatDataUpdateCoordinator.async_config_entry_first_refresh",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.weheat.coordinator.WeheatEnergyUpdateCoordinator.async_config_entry_first_refresh",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.weheat.coordinator.WeheatSettingsUpdateCoordinator.async_config_entry_first_refresh",
            new=AsyncMock(),
        ),
    ):
        await async_setup_entry(hass, mock_config_entry)

    # Simulate Home Assistant options update firing listeners
    mock_config_entry.options = {"portal_username": "user@example.com"}
    for listener in mock_config_entry.update_listeners:
        await listener(hass, mock_config_entry)

    assert mock_config_entry.entry_id in hass.config_entries.reloaded_entries


@pytest.mark.asyncio
async def test_unload_entry_cleans_runtime_data_and_coordinators(
    mock_config_entry: ConfigEntry,
) -> None:
    """Verify unload entry shuts down coordinators and empties runtime_data."""
    hass = HomeAssistant()
    mock_coord = MagicMock()
    mock_coord.async_shutdown = MagicMock()

    mock_data = MagicMock()
    mock_data.data_coordinator = mock_coord
    mock_data.energy_coordinator = mock_coord
    mock_data.settings_coordinator = mock_coord

    mock_config_entry.runtime_data = [mock_data]

    result = await async_unload_entry(hass, mock_config_entry)
    assert result is True
    assert mock_config_entry.runtime_data == []
    assert mock_coord.async_shutdown.call_count == 3


@pytest.mark.asyncio
async def test_update_listener_unsubscribes_on_unload(
    mock_config_entry: ConfigEntry,
) -> None:
    """Verify that unloading invokes unsub callbacks registered in _on_unload."""
    from custom_components.weheat import async_reload_entry

    unsub = mock_config_entry.add_update_listener(async_reload_entry)
    mock_config_entry.async_on_unload(unsub)

    assert len(mock_config_entry.update_listeners) == 1

    # Simulate HA core executing _on_unload on unload
    while mock_config_entry._on_unload:
        cb = mock_config_entry._on_unload.pop()
        cb()

    assert len(mock_config_entry.update_listeners) == 0
