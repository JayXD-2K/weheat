"""Comprehensive offline unit test suite for Weheat custom services/actions."""

from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError, ClientResponseError
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_TOKEN
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError

from custom_components.weheat.client import WeheatApiError, WeheatClient
from custom_components.weheat.const import DOMAIN
from custom_components.weheat.coordinator import WeheatData
from custom_components.weheat.models import HeatPumpInfo
from custom_components.weheat.services import (
    SERVICE_SET_QUIET_MODE,
    SERVICE_SET_SILENT_SCHEDULE,
    SERVICE_TRIGGER_LEGIONELLA,
    async_handle_set_quiet_mode,
    async_handle_set_silent_schedule,
    async_handle_trigger_legionella,
    async_setup_services,
    async_unload_services,
)

TEST_HP_UUID_1 = "00000000-0000-0000-0000-000000000001"
TEST_HP_UUID_2 = "00000000-0000-0000-0000-000000000002"
TEST_CB_ID_1 = "cb-00000000-0000-0000-0000-000000000001"
TEST_CB_ID_2 = "cb-00000000-0000-0000-0000-000000000002"
TEST_TOKEN = "mock_token_abc123"
TEST_REQ_ID = "00000000-0000-0000-0000-000000000999"


@pytest.fixture
def mock_heat_pump_info_1() -> HeatPumpInfo:
    return HeatPumpInfo(
        heatpump_id=TEST_HP_UUID_1,
        name="Blackbird Pump",
        model=0,
        serial_number="SN-001",
        control_board_id=TEST_CB_ID_1,
        has_dhw=True,
    )


@pytest.fixture
def mock_heat_pump_info_2() -> HeatPumpInfo:
    return HeatPumpInfo(
        heatpump_id=TEST_HP_UUID_2,
        name="Sparrow Pump",
        model=2,
        serial_number="SN-002",
        control_board_id=TEST_CB_ID_2,
        has_dhw=True,
    )


@pytest.fixture
def mock_weheat_client() -> MagicMock:
    client = MagicMock(spec=WeheatClient)
    client.async_set_performance_mode = AsyncMock(return_value=TEST_REQ_ID)
    client.async_set_silent_hours_preset = AsyncMock(return_value=TEST_REQ_ID)
    client.async_set_silent_hours_schedule = AsyncMock(return_value=TEST_REQ_ID)
    client.async_poll_settings_request = AsyncMock(
        return_value={"settingsRequestId": TEST_REQ_ID, "status": 2}
    )
    client.async_trigger_legionella_prevention = AsyncMock(return_value=True)
    client.async_resolve_control_board_id = AsyncMock(return_value=TEST_CB_ID_1)
    client.async_get_control_board_id = AsyncMock(return_value=TEST_CB_ID_1)
    return client


@pytest.fixture
def mock_session() -> MagicMock:
    session = MagicMock()
    session.async_ensure_token_valid = AsyncMock()
    session.token = {CONF_ACCESS_TOKEN: TEST_TOKEN}
    return session


@pytest.fixture
def mock_coordinator() -> MagicMock:
    coord = MagicMock()
    coord.async_request_refresh = AsyncMock()
    return coord


@pytest.fixture
def mock_weheat_data_1(
    mock_heat_pump_info_1: HeatPumpInfo,
    mock_weheat_client: MagicMock,
    mock_session: MagicMock,
    mock_coordinator: MagicMock,
) -> WeheatData:
    return WeheatData(
        heat_pump_info=mock_heat_pump_info_1,
        data_coordinator=mock_coordinator,
        energy_coordinator=mock_coordinator,
        client=mock_weheat_client,
        session=mock_session,
    )


@pytest.fixture
def mock_weheat_data_2(
    mock_heat_pump_info_2: HeatPumpInfo,
    mock_weheat_client: MagicMock,
    mock_session: MagicMock,
    mock_coordinator: MagicMock,
) -> WeheatData:
    return WeheatData(
        heat_pump_info=mock_heat_pump_info_2,
        data_coordinator=mock_coordinator,
        energy_coordinator=mock_coordinator,
        client=mock_weheat_client,
        session=mock_session,
    )


@pytest.fixture
def hass_with_single_pump(mock_weheat_data_1: WeheatData) -> HomeAssistant:
    hass = HomeAssistant()
    entry = ConfigEntry(
        entry_id="entry_1",
        data={CONF_TOKEN: {CONF_ACCESS_TOKEN: TEST_TOKEN}},
        runtime_data=[mock_weheat_data_1],
    )
    hass.config_entries.async_add(entry)
    return hass


@pytest.fixture
def hass_with_two_pumps(
    mock_weheat_data_1: WeheatData, mock_weheat_data_2: WeheatData
) -> HomeAssistant:
    hass = HomeAssistant()
    entry = ConfigEntry(
        entry_id="entry_dual",
        data={CONF_TOKEN: {CONF_ACCESS_TOKEN: TEST_TOKEN}},
        runtime_data=[mock_weheat_data_1, mock_weheat_data_2],
    )
    hass.config_entries.async_add(entry)
    return hass


# -----------------------------------------------------------------------------
# Setup & Unload Lifecycle Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_setup_and_unload_services() -> None:
    """Verify registration and unregistration of custom actions on HomeAssistant."""
    hass = HomeAssistant()
    await async_setup_services(hass)

    assert hass.services.has_service(DOMAIN, SERVICE_SET_QUIET_MODE)
    assert hass.services.has_service(DOMAIN, SERVICE_SET_SILENT_SCHEDULE)
    assert hass.services.has_service(DOMAIN, SERVICE_TRIGGER_LEGIONELLA)

    # Idempotent re-setup
    await async_setup_services(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_SET_QUIET_MODE)

    # Unload
    await async_unload_services(hass)
    assert not hass.services.has_service(DOMAIN, SERVICE_SET_QUIET_MODE)
    assert not hass.services.has_service(DOMAIN, SERVICE_SET_SILENT_SCHEDULE)
    assert not hass.services.has_service(DOMAIN, SERVICE_TRIGGER_LEGIONELLA)


# -----------------------------------------------------------------------------
# weheat.set_quiet_mode Action Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("input_mode", "expected_preset"),
    [
        ("Performance", 0),
        ("performance", 0),
        (0, 0),
        ("Silent 1", 1),
        ("silent 1", 1),
        ("silent1", 1),
        (1, 1),
        ("Silent 2", 2),
        ("  silent 2  ", 2),
        (2, 2),
        ("Silent 3", 3),
        (3, 3),
        ("Silent 4", 4),
        (4, 4),
    ],
)
async def test_set_quiet_mode_valid(
    hass_with_single_pump: HomeAssistant,
    mock_weheat_client: MagicMock,
    mock_coordinator: MagicMock,
    input_mode: Any,
    expected_preset: int,
) -> None:
    """Verify set_quiet_mode parses strings and ints, dispatches PATCH, and polls."""
    call = ServiceCall(DOMAIN, SERVICE_SET_QUIET_MODE, {"mode": input_mode})
    await async_handle_set_quiet_mode(hass_with_single_pump, call)

    mock_weheat_client.async_set_performance_mode.assert_called_once_with(
        control_board_id=TEST_CB_ID_1,
        token=TEST_TOKEN,
        preset=expected_preset,
    )
    mock_weheat_client.async_poll_settings_request.assert_called_once_with(
        control_board_id=TEST_CB_ID_1,
        request_id=TEST_REQ_ID,
        token=TEST_TOKEN,
    )
    mock_coordinator.async_request_refresh.assert_called_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_mode",
    [
        "Ultra Quiet",
        "Turbo",
        5,
        -1,
        "Unknown",
        None,
    ],
)
async def test_set_quiet_mode_invalid_values(
    hass_with_single_pump: HomeAssistant,
    invalid_mode: Any,
) -> None:
    """Verify invalid quiet mode options trigger descriptive HomeAssistantError."""
    data = {"mode": invalid_mode} if invalid_mode is not None else {}
    call = ServiceCall(DOMAIN, SERVICE_SET_QUIET_MODE, data)

    with pytest.raises(HomeAssistantError) as exc_info:
        await async_handle_set_quiet_mode(hass_with_single_pump, call)

    assert "Invalid quiet mode" in str(
        exc_info.value
    ) or "Missing required parameter" in str(exc_info.value)


# -----------------------------------------------------------------------------
# weheat.set_silent_schedule Action Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_silent_schedule_weekdays_custom_window(
    hass_with_single_pump: HomeAssistant,
    mock_weheat_client: MagicMock,
    mock_coordinator: MagicMock,
) -> None:
    """Verify set_silent_schedule with explicit days and start/stop HH:MM window."""
    call = ServiceCall(
        DOMAIN,
        SERVICE_SET_SILENT_SCHEDULE,
        {
            "preset": "Silent 2",
            "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
            "start_time": "22:30",
            "stop_time": "06:45",
        },
    )
    await async_handle_set_silent_schedule(hass_with_single_pump, call)

    mock_weheat_client.async_set_silent_hours_schedule.assert_called_once()
    kwargs = mock_weheat_client.async_set_silent_hours_schedule.call_args.kwargs

    assert kwargs["control_board_id"] == TEST_CB_ID_1
    assert kwargs["token"] == TEST_TOKEN
    assert kwargs["preset"] == 2

    schedule = kwargs["schedule"]
    assert len(schedule) == 20  # 5 days * 4 keys
    for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday"):
        assert schedule[f"SilentHours{day}Start"] == 22
        assert schedule[f"SilentMinutes{day}Start"] == 30
        assert schedule[f"SilentHours{day}Stop"] == 6
        assert schedule[f"SilentMinutes{day}Stop"] == 45

    assert "SilentHoursSaturdayStart" not in schedule
    assert "SilentHoursSundayStart" not in schedule

    mock_weheat_client.async_poll_settings_request.assert_called_once()
    mock_coordinator.async_request_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_set_silent_schedule_all_days_default_and_time_objects(
    hass_with_single_pump: HomeAssistant,
    mock_weheat_client: MagicMock,
) -> None:
    """Verify omitting days defaults to all 7 days, and datetime.time is supported."""
    call = ServiceCall(
        DOMAIN,
        SERVICE_SET_SILENT_SCHEDULE,
        {
            "preset": "Off",
            "start_time": datetime.time(23, 0),
            "stop_time": datetime.time(7, 0),
        },
    )
    await async_handle_set_silent_schedule(hass_with_single_pump, call)

    kwargs = mock_weheat_client.async_set_silent_hours_schedule.call_args.kwargs
    assert kwargs["preset"] == 0
    schedule = kwargs["schedule"]
    assert len(schedule) == 28  # 7 days * 4 keys


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("days_alias", "expected_count"),
    [
        ("all", 7),
        ("weekdays", 5),
        ("weekend", 2),
        ("Monday, Friday", 2),
    ],
)
async def test_set_silent_schedule_day_aliases(
    hass_with_single_pump: HomeAssistant,
    mock_weheat_client: MagicMock,
    days_alias: str,
    expected_count: int,
) -> None:
    """Verify day shortcut aliases ('all', 'weekdays', 'weekend', comma-delimited)."""
    call = ServiceCall(
        DOMAIN,
        SERVICE_SET_SILENT_SCHEDULE,
        {
            "preset": 1,
            "days": days_alias,
            "start_time": "20:00",
            "stop_time": "08:00",
        },
    )
    await async_handle_set_silent_schedule(hass_with_single_pump, call)

    kwargs = mock_weheat_client.async_set_silent_hours_schedule.call_args.kwargs
    schedule = kwargs["schedule"]
    assert len(schedule) == expected_count * 4


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_call_data",
    [
        {"preset": "NonExistentPreset", "start_time": "22:00", "stop_time": "07:00"},
        {"preset": "Silent 1", "start_time": "25:00", "stop_time": "07:00"},
        {"preset": "Silent 1", "start_time": "22:00", "stop_time": "07:99"},
        {"preset": "Silent 1", "start_time": "invalid_time", "stop_time": "07:00"},
        {
            "preset": "Silent 1",
            "start_time": "22:00",
            "stop_time": "07:00",
            "days": ["Funday"],
        },
        {"start_time": "22:00", "stop_time": "07:00"},  # Missing preset
    ],
)
async def test_set_silent_schedule_validation_errors(
    hass_with_single_pump: HomeAssistant,
    invalid_call_data: dict[str, Any],
) -> None:
    """Verify comprehensive validation of parameters in set_silent_schedule."""
    call = ServiceCall(DOMAIN, SERVICE_SET_SILENT_SCHEDULE, invalid_call_data)
    with pytest.raises(HomeAssistantError):
        await async_handle_set_silent_schedule(hass_with_single_pump, call)


# -----------------------------------------------------------------------------
# weheat.trigger_legionella_prevention Action Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trigger_legionella_prevention_success(
    hass_with_single_pump: HomeAssistant,
    mock_weheat_client: MagicMock,
    mock_coordinator: MagicMock,
) -> None:
    """Verify trigger_legionella_prevention calls client and refreshes coordinator."""
    call = ServiceCall(DOMAIN, SERVICE_TRIGGER_LEGIONELLA, {})
    await async_handle_trigger_legionella(hass_with_single_pump, call)

    mock_weheat_client.async_trigger_legionella_prevention.assert_called_once_with(
        control_board_id=TEST_CB_ID_1,
        token=TEST_TOKEN,
    )
    mock_coordinator.async_request_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_trigger_legionella_prevention_api_failure(
    hass_with_single_pump: HomeAssistant,
    mock_weheat_client: MagicMock,
) -> None:
    """Verify API error raises HomeAssistantError."""
    mock_weheat_client.async_trigger_legionella_prevention = AsyncMock(
        side_effect=WeheatApiError("Portal rejected variable patch", status_code=500)
    )
    call = ServiceCall(DOMAIN, SERVICE_TRIGGER_LEGIONELLA, {})

    with pytest.raises(HomeAssistantError) as exc_info:
        await async_handle_trigger_legionella(hass_with_single_pump, call)

    assert "Failed to trigger Legionella prevention" in str(exc_info.value)


# -----------------------------------------------------------------------------
# Multi-Device & Target Resolution Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_target_resolution_multi_pumps_disambiguation(
    hass_with_two_pumps: HomeAssistant,
    mock_weheat_client: MagicMock,
) -> None:
    """Verify specific heat pump targeted by heat_pump_id when multiple are loaded."""
    call = ServiceCall(
        DOMAIN,
        SERVICE_SET_QUIET_MODE,
        {"mode": "Silent 3", "heat_pump_id": TEST_HP_UUID_2},
    )
    await async_handle_set_quiet_mode(hass_with_two_pumps, call)

    # Should have targeted TEST_CB_ID_2, not TEST_CB_ID_1
    mock_weheat_client.async_set_performance_mode.assert_called_once_with(
        control_board_id=TEST_CB_ID_2,
        token=TEST_TOKEN,
        preset=3,
    )


@pytest.mark.asyncio
async def test_target_resolution_multi_pumps_missing_target_raises(
    hass_with_two_pumps: HomeAssistant,
) -> None:
    """Verify calling service without target on multiple pumps raises HomeAssistantError."""
    call = ServiceCall(DOMAIN, SERVICE_SET_QUIET_MODE, {"mode": "Silent 1"})

    with pytest.raises(HomeAssistantError) as exc_info:
        await async_handle_set_quiet_mode(hass_with_two_pumps, call)

    assert "Multiple Weheat heat pumps found" in str(exc_info.value)


# -----------------------------------------------------------------------------
# Service Auth Exception & Network Error Translation Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_set_quiet_mode_oauth_token_refresh_client_response_error(
    hass_with_single_pump: HomeAssistant,
    mock_session: MagicMock,
) -> None:
    """Verify HTTP 401 token refresh failure in set_quiet_mode raises HomeAssistantError."""
    mock_session.async_ensure_token_valid = AsyncMock(
        side_effect=ClientResponseError(
            request_info=MagicMock(), history=(), status=401, message="Unauthorized"
        )
    )
    call = ServiceCall(DOMAIN, SERVICE_SET_QUIET_MODE, {"mode": "Performance"})
    with pytest.raises(HomeAssistantError) as exc_info:
        await async_handle_set_quiet_mode(hass_with_single_pump, call)

    assert "Authentication failed" in str(exc_info.value)
    assert "401" in str(exc_info.value)


@pytest.mark.asyncio
async def test_set_quiet_mode_oauth_token_refresh_network_client_error(
    hass_with_single_pump: HomeAssistant,
    mock_session: MagicMock,
) -> None:
    """Verify network connection error during token refresh raises HomeAssistantError."""
    mock_session.async_ensure_token_valid = AsyncMock(
        side_effect=ClientError("Cannot connect to Keycloak host")
    )
    call = ServiceCall(DOMAIN, SERVICE_SET_QUIET_MODE, {"mode": 0})
    with pytest.raises(HomeAssistantError) as exc_info:
        await async_handle_set_quiet_mode(hass_with_single_pump, call)

    assert "Network error refreshing OAuth token" in str(exc_info.value)


@pytest.mark.asyncio
async def test_set_silent_schedule_oauth_token_refresh_client_response_error(
    hass_with_single_pump: HomeAssistant,
    mock_session: MagicMock,
) -> None:
    """Verify HTTP 403 token refresh failure in set_silent_schedule raises HomeAssistantError."""
    mock_session.async_ensure_token_valid = AsyncMock(
        side_effect=ClientResponseError(
            request_info=MagicMock(), history=(), status=403, message="Forbidden"
        )
    )
    call = ServiceCall(
        DOMAIN,
        SERVICE_SET_SILENT_SCHEDULE,
        {"preset": 1, "start_time": "22:00", "stop_time": "07:00"},
    )
    with pytest.raises(HomeAssistantError) as exc_info:
        await async_handle_set_silent_schedule(hass_with_single_pump, call)

    assert "Authentication failed" in str(exc_info.value)
    assert "403" in str(exc_info.value)


@pytest.mark.asyncio
async def test_trigger_legionella_oauth_token_refresh_client_error(
    hass_with_single_pump: HomeAssistant,
    mock_session: MagicMock,
) -> None:
    """Verify network error during token refresh in trigger_legionella raises HomeAssistantError."""
    mock_session.async_ensure_token_valid = AsyncMock(
        side_effect=ClientError("Connection refused by auth server")
    )
    call = ServiceCall(DOMAIN, SERVICE_TRIGGER_LEGIONELLA, {})
    with pytest.raises(HomeAssistantError) as exc_info:
        await async_handle_trigger_legionella(hass_with_single_pump, call)

    assert "Network error refreshing OAuth token" in str(exc_info.value)


@pytest.mark.asyncio
async def test_service_control_board_resolution_client_error(
    hass_with_single_pump: HomeAssistant,
    mock_weheat_client: MagicMock,
    mock_heat_pump_info_1: HeatPumpInfo,
) -> None:
    """Verify network error resolving control board ID raises HomeAssistantError."""
    mock_heat_pump_info_1.control_board_id = ""  # Trigger resolution
    mock_weheat_client.async_get_control_board_id = AsyncMock(
        side_effect=ClientError("DNS lookup failed for api.weheat.nl")
    )
    call = ServiceCall(DOMAIN, SERVICE_SET_QUIET_MODE, {"mode": 0})
    with pytest.raises(HomeAssistantError) as exc_info:
        await async_handle_set_quiet_mode(hass_with_single_pump, call)

    assert "Failed to resolve control board ID" in str(exc_info.value)
