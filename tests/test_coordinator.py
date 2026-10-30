"""Tests for Weheat Data Update Coordinators."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError, ClientResponseError
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.weheat.client import (
    WeheatApiError,
    WeheatAuthError,
    WeheatTimeoutError,
)
from custom_components.weheat.const import SETTINGS_UPDATE_INTERVAL
from custom_components.weheat.coordinator import (
    WeheatData,
    WeheatDataUpdateCoordinator,
    WeheatEnergyUpdateCoordinator,
    WeheatSettingsUpdateCoordinator,
)
from custom_components.weheat.models import (
    ControlBoardSettings,
    HeatPumpEnergy,
    HeatPumpInfo,
    HeatPumpLog,
)

TEST_INSTALLATION_ID = "00000000-0000-0000-0000-000000000101"


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
        installation_id=TEST_INSTALLATION_ID,
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

    # Settings coordinator: fixed 300s (5m)
    coord_settings = WeheatSettingsUpdateCoordinator(hass, entry, None, mock_hp_info)
    assert coord_settings.update_interval == timedelta(seconds=300)
    assert coord_settings.update_interval == timedelta(seconds=SETTINGS_UPDATE_INTERVAL)
    assert coord_settings.name == "weheat_settings"


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


# =============================================================================
# WEHEAT SETTINGS UPDATE COORDINATOR TESTS
# =============================================================================


def test_settings_coordinator_interval(mock_hp_info: HeatPumpInfo) -> None:
    """Verify WeheatSettingsUpdateCoordinator polling interval is 300 seconds."""
    hass = HomeAssistant()
    entry = ConfigEntry()
    coord = WeheatSettingsUpdateCoordinator(hass, entry, None, mock_hp_info)
    assert coord.update_interval == timedelta(seconds=300)
    assert coord.update_interval == timedelta(seconds=SETTINGS_UPDATE_INTERVAL)
    assert coord.name == "weheat_settings"


@pytest.mark.asyncio
async def test_settings_coordinator_success(mock_hp_info: HeatPumpInfo) -> None:
    """Verify successful update fetching control board settings via client."""
    hass = HomeAssistant()
    entry = ConfigEntry()
    mock_client = MagicMock()

    expected_settings = ControlBoardSettings(
        performance_mode_preset=1,
        silent_hours_preset=2,
        dhw_target_temperature=52.0,
    )
    mock_client.async_get_installation_settings = AsyncMock(
        return_value=expected_settings
    )

    coord = WeheatSettingsUpdateCoordinator(
        hass, entry, None, mock_hp_info, client=mock_client
    )

    data = await coord._async_update_data()
    assert data is expected_settings
    assert data.performance_mode_preset == 1
    assert data.silent_hours_preset == 2
    assert data.dhw_target_temperature == 52.0
    mock_client.async_get_installation_settings.assert_called_once_with(
        TEST_INSTALLATION_ID, token=None
    )


@pytest.mark.asyncio
async def test_settings_coordinator_topology_fallback_success(
    mock_hp_info: HeatPumpInfo,
) -> None:
    """Verify topology discovery fallback when installation_id is missing from heat_pump_info."""
    hass = HomeAssistant()
    entry = ConfigEntry()
    mock_client = MagicMock()
    mock_hp_info.installation_id = None

    expected_settings = ControlBoardSettings(
        performance_mode_preset=0,
        silent_hours_preset=0,
        dhw_target_temperature=50.0,
    )
    mock_client.async_get_installation_topology = AsyncMock(
        return_value=[
            {
                "id": mock_hp_info.heatpump_id,
                "installationId": "discovered-inst-uuid-1234",
                "controlBoardId": "cb-discovered-5678",
            }
        ]
    )
    mock_client.async_get_installation_settings = AsyncMock(
        return_value=expected_settings
    )

    coord = WeheatSettingsUpdateCoordinator(
        hass, entry, None, mock_hp_info, client=mock_client
    )

    data = await coord._async_update_data()
    assert data is expected_settings
    assert mock_hp_info.installation_id == "discovered-inst-uuid-1234"
    mock_client.async_get_installation_topology.assert_called_once_with(token=None)
    mock_client.async_get_installation_settings.assert_called_once_with(
        "discovered-inst-uuid-1234", token=None
    )


@pytest.mark.asyncio
async def test_settings_coordinator_topology_fallback_failure(
    mock_hp_info: HeatPumpInfo,
) -> None:
    """Verify failure to resolve installation_id via topology discovery raises UpdateFailed."""
    hass = HomeAssistant()
    entry = ConfigEntry()
    mock_client = MagicMock()
    mock_hp_info.installation_id = None

    # Topology returns unrelated heat pump
    mock_client.async_get_installation_topology = AsyncMock(
        return_value=[
            {
                "id": "unrelated-heat-pump-uuid",
                "installationId": "unrelated-inst-id",
            }
        ]
    )

    coord = WeheatSettingsUpdateCoordinator(
        hass, entry, None, mock_hp_info, client=mock_client
    )

    with pytest.raises(UpdateFailed) as exc_info:
        await coord._async_update_data()
    assert f"Missing installation ID for heat pump {mock_hp_info.heatpump_id}" in str(
        exc_info.value
    )


@pytest.mark.asyncio
async def test_settings_coordinator_missing_heat_pump_info_raises_update_failed() -> (
    None
):
    """Verify settings coordinator raises UpdateFailed when heat_pump_info is None."""
    hass = HomeAssistant()
    entry = ConfigEntry()
    coord = WeheatSettingsUpdateCoordinator(hass, entry, None, None)

    with pytest.raises(UpdateFailed) as exc_info:
        await coord._async_update_data()
    assert "Heat pump information is missing from coordinator" in str(exc_info.value)


@pytest.mark.asyncio
async def test_settings_coordinator_error_mapping(
    mock_hp_info: HeatPumpInfo,
) -> None:
    """Verify settings coordinator exception mapping: WeheatAuthError -> ConfigEntryAuthFailed, others -> UpdateFailed."""
    hass = HomeAssistant()
    entry = ConfigEntry()
    mock_client = MagicMock()

    coord = WeheatSettingsUpdateCoordinator(
        hass, entry, None, mock_hp_info, client=mock_client
    )

    # 1. WeheatAuthError -> ConfigEntryAuthFailed
    mock_client.async_get_installation_settings = AsyncMock(
        side_effect=WeheatAuthError("401 Unauthorized", status_code=401)
    )
    with pytest.raises(ConfigEntryAuthFailed) as exc_info:
        await coord._async_update_data()
    assert "Authentication failed" in str(exc_info.value)

    # 2. WeheatApiError -> UpdateFailed
    mock_client.async_get_installation_settings = AsyncMock(
        side_effect=WeheatApiError("500 Server Error", status_code=500)
    )
    with pytest.raises(UpdateFailed) as exc_info:
        await coord._async_update_data()
    assert "Error communicating with Weheat API" in str(exc_info.value)

    # 3. WeheatTimeoutError -> UpdateFailed
    mock_client.async_get_installation_settings = AsyncMock(
        side_effect=WeheatTimeoutError("Request timed out")
    )
    with pytest.raises(UpdateFailed) as exc_info:
        await coord._async_update_data()
    assert "Error communicating with Weheat API" in str(exc_info.value)

    # 4. ClientError (network connection drop) -> UpdateFailed
    mock_client.async_get_installation_settings = AsyncMock(
        side_effect=ClientError("Connection reset by peer")
    )
    with pytest.raises(UpdateFailed) as exc_info:
        await coord._async_update_data()
    assert "Error communicating with Weheat API" in str(exc_info.value)


@pytest.mark.asyncio
async def test_settings_coordinator_token_refresh_lifecycle_and_validation(
    mock_hp_info: HeatPumpInfo,
) -> None:
    """Verify OAuth token lifecycle: valid refresh passes bearer token, renewal errors raise proper exceptions."""
    hass = HomeAssistant()
    entry = ConfigEntry()
    mock_session = MagicMock()
    mock_client = MagicMock()

    expected_settings = ControlBoardSettings()
    mock_client.async_get_installation_settings = AsyncMock(
        return_value=expected_settings
    )

    coord = WeheatSettingsUpdateCoordinator(
        hass,
        entry,
        session=mock_session,
        heat_pump_info=mock_hp_info,
        client=mock_client,
    )

    # 1. Successful token refresh lifecycle: Bearer token extracted and forwarded
    mock_session.async_ensure_token_valid = AsyncMock()
    mock_session.token = {CONF_ACCESS_TOKEN: "valid_bearer_token_12345"}
    data = await coord._async_update_data()
    assert data is expected_settings
    mock_session.async_ensure_token_valid.assert_called_once()
    mock_client.async_get_installation_settings.assert_called_once_with(
        TEST_INSTALLATION_ID, token="valid_bearer_token_12345"
    )

    # 2. Token refresh 401 ClientResponseError -> ConfigEntryAuthFailed
    mock_session.async_ensure_token_valid = AsyncMock(
        side_effect=ClientResponseError(
            request_info=MagicMock(), history=(), status=401, message="Unauthorized"
        )
    )
    with pytest.raises(ConfigEntryAuthFailed) as exc_info:
        await coord._async_update_data()
    assert "OAuth token invalid or expired" in str(exc_info.value)

    # 3. Token refresh 403 ClientResponseError -> ConfigEntryAuthFailed
    mock_session.async_ensure_token_valid = AsyncMock(
        side_effect=ClientResponseError(
            request_info=MagicMock(), history=(), status=403, message="Forbidden"
        )
    )
    with pytest.raises(ConfigEntryAuthFailed) as exc_info:
        await coord._async_update_data()
    assert "OAuth token invalid or expired" in str(exc_info.value)

    # 4. Token refresh 500 ClientResponseError -> UpdateFailed
    mock_session.async_ensure_token_valid = AsyncMock(
        side_effect=ClientResponseError(
            request_info=MagicMock(), history=(), status=500, message="Server error"
        )
    )
    with pytest.raises(UpdateFailed) as exc_info:
        await coord._async_update_data()
    assert "Error refreshing OAuth token" in str(exc_info.value)

    # 5. Token refresh generic unexpected Exception (network error) -> UpdateFailed
    mock_session.async_ensure_token_valid = AsyncMock(
        side_effect=RuntimeError("DNS resolution failed")
    )
    with pytest.raises(UpdateFailed) as exc_info:
        await coord._async_update_data()
    assert "Failed to ensure valid OAuth token" in str(exc_info.value)


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
    settings_coord = WeheatSettingsUpdateCoordinator(
        hass, entry, None, mock_hp_info, client=mock_client
    )

    item = WeheatData(
        heat_pump_info=mock_hp_info,
        data_coordinator=data_coord,
        energy_coordinator=energy_coord,
        settings_coordinator=settings_coord,
        client=mock_client,
    )

    assert item.heat_pump_info is mock_hp_info
    assert item.data_coordinator is data_coord
    assert item.energy_coordinator is energy_coord
    assert item.settings_coordinator is settings_coord
    assert item.client is mock_client


@pytest.mark.asyncio
async def test_settings_coordinator_403_maps_to_update_failed(
    mock_hp_info: HeatPumpInfo,
) -> None:
    """Verify HTTP 403 on portal settings raises UpdateFailed instead of ConfigEntryAuthFailed."""
    hass = HomeAssistant()
    entry = ConfigEntry()
    mock_client = MagicMock()
    mock_client.async_get_installation_settings = AsyncMock(
        side_effect=WeheatAuthError("403 Forbidden", status_code=403)
    )

    coord = WeheatSettingsUpdateCoordinator(
        hass, entry, None, mock_hp_info, client=mock_client
    )

    with pytest.raises(UpdateFailed) as exc_info:
        await coord._async_update_data()
    assert "HTTP 403" in str(exc_info.value)
