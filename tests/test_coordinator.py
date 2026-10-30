"""Tests for Weheat Data Update Coordinators."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError, ClientResponseError
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.weheat.client import (
    WeheatApiError,
    WeheatAuthError,
    WeheatTimeoutError,
)
from custom_components.weheat.coordinator import (
    WeheatData,
    WeheatDataUpdateCoordinator,
    WeheatEnergyUpdateCoordinator,
)
from custom_components.weheat.models import (
    HeatPumpEnergy,
    HeatPumpInfo,
    HeatPumpLog,
)


@pytest.fixture
def mock_hp_info() -> HeatPumpInfo:
    return HeatPumpInfo(
        heatpump_id="00000000-0000-0000-0000-000000000001",
        name="Test Blackbird",
        model=0,
        serial_number="WH-BB80-001",
        has_dhw=True,
        has_ch_boiler=False,
        firmware_version="2.4.1",
        part_number="PN-12345",
    )


def test_coordinator_intervals(mock_hp_info: HeatPumpInfo) -> None:
    """Verify coordinator polling intervals scale per HA Core spec."""
    hass = HomeAssistant()
    entry = ConfigEntry()

    # 1 heat pump: 120s
    coord1 = WeheatDataUpdateCoordinator(
        hass, entry, None, mock_hp_info, nr_of_heat_pumps=1
    )
    assert coord1.update_interval == timedelta(seconds=120)

    # 3 heat pumps: 360s
    coord3 = WeheatDataUpdateCoordinator(
        hass, entry, None, mock_hp_info, nr_of_heat_pumps=3
    )
    assert coord3.update_interval == timedelta(seconds=360)

    # Energy coordinator: fixed 1800s (30m)
    coord_energy = WeheatEnergyUpdateCoordinator(hass, entry, None, mock_hp_info)
    assert coord_energy.update_interval == timedelta(seconds=1800)


@pytest.mark.asyncio
async def test_data_coordinator_success(mock_hp_info: HeatPumpInfo) -> None:
    """Verify successful telemetry fetch and nominal_rpm propagation."""
    hass = HomeAssistant()
    entry = ConfigEntry()
    mock_client = MagicMock()
    mock_log = HeatPumpLog(
        heat_pump_id=mock_hp_info.heatpump_id,
        rpm=2250,
    )
    mock_client.async_get_latest_logs = AsyncMock(return_value=mock_log)

    coord = WeheatDataUpdateCoordinator(
        hass, entry, None, mock_hp_info, nr_of_heat_pumps=1, client=mock_client
    )

    data = await coord._async_update_data()
    assert data is mock_log
    assert data.nominal_rpm == mock_hp_info.nominal_rpm
    mock_client.async_get_latest_logs.assert_called_once_with(
        mock_hp_info.heatpump_id, token=None
    )


@pytest.mark.asyncio
async def test_data_coordinator_error_mapping(mock_hp_info: HeatPumpInfo) -> None:
    """Verify exception mapping: WeheatAuthError -> ConfigEntryAuthFailed, etc."""
    hass = HomeAssistant()
    entry = ConfigEntry()
    mock_client = MagicMock()

    coord = WeheatDataUpdateCoordinator(
        hass, entry, None, mock_hp_info, nr_of_heat_pumps=1, client=mock_client
    )

    # 1. WeheatAuthError -> ConfigEntryAuthFailed
    mock_client.async_get_latest_logs = AsyncMock(
        side_effect=WeheatAuthError("401 Unauthorized")
    )
    with pytest.raises(ConfigEntryAuthFailed):
        await coord._async_update_data()

    # 2. WeheatApiError -> UpdateFailed
    mock_client.async_get_latest_logs = AsyncMock(
        side_effect=WeheatApiError("500 Internal Error")
    )
    with pytest.raises(UpdateFailed):
        await coord._async_update_data()

    # 3. WeheatTimeoutError -> UpdateFailed
    mock_client.async_get_latest_logs = AsyncMock(
        side_effect=WeheatTimeoutError("Timed out")
    )
    with pytest.raises(UpdateFailed):
        await coord._async_update_data()

    # 4. ClientError -> UpdateFailed
    mock_client.async_get_latest_logs = AsyncMock(
        side_effect=ClientError("Connection reset")
    )
    with pytest.raises(UpdateFailed):
        await coord._async_update_data()


@pytest.mark.asyncio
async def test_data_coordinator_oauth_session_token_validation(
    mock_hp_info: HeatPumpInfo,
) -> None:
    """Verify OAuth2Session token validation errors."""
    hass = HomeAssistant()
    entry = ConfigEntry()
    mock_session = MagicMock()
    mock_client = MagicMock()

    coord = WeheatDataUpdateCoordinator(
        hass,
        entry,
        session=mock_session,
        heat_pump_info=mock_hp_info,
        client=mock_client,
    )

    # Token renewal returns 401 ClientResponseError -> ConfigEntryAuthFailed
    mock_session.async_ensure_token_valid = AsyncMock(
        side_effect=ClientResponseError(
            request_info=MagicMock(), history=(), status=401, message="Unauthorized"
        )
    )
    with pytest.raises(ConfigEntryAuthFailed):
        await coord._async_update_data()

    # Token renewal returns 500 ClientResponseError -> UpdateFailed
    mock_session.async_ensure_token_valid = AsyncMock(
        side_effect=ClientResponseError(
            request_info=MagicMock(), history=(), status=500, message="Server error"
        )
    )
    with pytest.raises(UpdateFailed):
        await coord._async_update_data()


@pytest.mark.asyncio
async def test_energy_coordinator_success_and_errors(
    mock_hp_info: HeatPumpInfo,
) -> None:
    """Verify energy coordinator fetch and error mapping."""
    hass = HomeAssistant()
    entry = ConfigEntry()
    mock_client = MagicMock()
    mock_energy = HeatPumpEnergy(
        heatpump_id=mock_hp_info.heatpump_id, total_ein_heating=120.5
    )
    mock_client.async_get_energy_total = AsyncMock(return_value=mock_energy)

    coord = WeheatEnergyUpdateCoordinator(
        hass, entry, None, mock_hp_info, client=mock_client
    )

    data = await coord._async_update_data()
    assert data is mock_energy

    # Auth error
    mock_client.async_get_energy_total = AsyncMock(
        side_effect=WeheatAuthError("Forbidden", status_code=403)
    )
    with pytest.raises(ConfigEntryAuthFailed):
        await coord._async_update_data()

    # API error
    mock_client.async_get_energy_total = AsyncMock(
        side_effect=WeheatApiError("Bad request")
    )
    with pytest.raises(UpdateFailed):
        await coord._async_update_data()


def test_weheat_data_container(mock_hp_info: HeatPumpInfo) -> None:
    """Verify WeheatData container holds all elements expected by core and custom."""
    hass = HomeAssistant()
    entry = ConfigEntry()
    mock_client = MagicMock()

    data_coord = WeheatDataUpdateCoordinator(
        hass, entry, None, mock_hp_info, client=mock_client
    )
    energy_coord = WeheatEnergyUpdateCoordinator(
        hass, entry, None, mock_hp_info, client=mock_client
    )

    item = WeheatData(
        heat_pump_info=mock_hp_info,
        data_coordinator=data_coord,
        energy_coordinator=energy_coord,
        client=mock_client,
    )

    assert item.heat_pump_info is mock_hp_info
    assert item.data_coordinator is data_coord
    assert item.energy_coordinator is energy_coord
    assert item.client is mock_client
