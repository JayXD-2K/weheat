"""Adversarial stress testing for Milestone 4: Quiet Mode, Time Entities, and Switch/Select Controls."""

from __future__ import annotations

import asyncio
from datetime import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError

from custom_components.weheat.client import (
    WeheatApiError,
    WeheatClient,
    WeheatTimeoutError,
)
from custom_components.weheat.const import (
    DAYS_OF_WEEK,
    SETTINGS_REQUEST_STATUS_MAP,
)
from custom_components.weheat.coordinator import WeheatData
from custom_components.weheat.models import (
    ControlBoardSettings,
    HeatPumpInfo,
    HeatPumpLog,
)
from custom_components.weheat.select import (
    SELECT_ENTITIES,
    WeheatPerformanceModeSelect,
    WeheatSilentHoursPresetSelect,
)
from custom_components.weheat.services import (
    _parse_days,
    _parse_time,
    async_handle_set_quiet_mode,
    async_handle_set_silent_schedule,
)
from custom_components.weheat.switch import (
    WeheatDhwBoostSwitch,
    WeheatLegionellaPreventionSwitch,
    WeheatSilentHoursScheduleSwitch,
)
from custom_components.weheat.switch import (
    async_setup_entry as async_setup_switch_entry,
)
from custom_components.weheat.time import (
    TIME_ENTITIES,
    WeheatTimeEntity,
)
from tests.conftest import (
    TEST_CB_ID_BLACKBIRD,
    TEST_HP_UUID_BLACKBIRD,
    TEST_INSTALLATION_ID,
    TEST_SETTINGS_REQUEST_ID,
    load_fixture,
)


@pytest.fixture
def sample_settings() -> ControlBoardSettings:
    """Instantiate fresh ControlBoardSettings from fixture."""
    return ControlBoardSettings.from_api_dict(
        load_fixture("installation_settings_get.json")
    )


@pytest.fixture
def mock_hp_info() -> HeatPumpInfo:
    """Create sample HeatPumpInfo with DHW enabled."""
    return HeatPumpInfo(
        heatpump_id=TEST_HP_UUID_BLACKBIRD,
        name="Blackbird Outdoor",
        model=0,
        serial_number="WH-BB80-MOCK-001",
        has_dhw=True,
        control_board_id=TEST_CB_ID_BLACKBIRD,
        installation_id=TEST_INSTALLATION_ID,
    )


@pytest.fixture
def mock_hp_info_no_dhw() -> HeatPumpInfo:
    """Create sample HeatPumpInfo with DHW disabled."""
    return HeatPumpInfo(
        heatpump_id=TEST_HP_UUID_BLACKBIRD,
        name="Blackbird Outdoor (No DHW)",
        model=0,
        serial_number="WH-BB80-MOCK-002",
        has_dhw=False,
        control_board_id=TEST_CB_ID_BLACKBIRD,
        installation_id=TEST_INSTALLATION_ID,
    )


# ===========================================================================
# 1. Performance Mode Invalid / Boundary Presets
# ===========================================================================


@pytest.mark.asyncio
async def test_client_performance_mode_valid_boundaries() -> None:
    """Verify client accepts boundary presets 0 (Performance) and 4 (Silent 4)."""
    mock_session = MagicMock()
    client = WeheatClient(session=mock_session)
    client.async_patch_control_board_settings = AsyncMock(
        return_value=TEST_SETTINGS_REQUEST_ID
    )

    # Lower boundary: 0
    req_id_0 = await client.async_set_performance_mode(
        control_board_id=TEST_CB_ID_BLACKBIRD,
        preset=0,
    )
    assert req_id_0 == TEST_SETTINGS_REQUEST_ID
    client.async_patch_control_board_settings.assert_awaited_with(
        control_board_id=TEST_CB_ID_BLACKBIRD,
        token=None,
        settings={"PerformanceModePreset": 0},
        update_immediately=False,
    )

    # Upper boundary: 4
    req_id_4 = await client.async_set_performance_mode(
        control_board_id=TEST_CB_ID_BLACKBIRD,
        preset=4,
    )
    assert req_id_4 == TEST_SETTINGS_REQUEST_ID
    client.async_patch_control_board_settings.assert_awaited_with(
        control_board_id=TEST_CB_ID_BLACKBIRD,
        token=None,
        settings={"PerformanceModePreset": 4},
        update_immediately=False,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_preset", [-1, 5, 999, -100, 100])
async def test_client_performance_mode_invalid_numeric_boundaries(
    invalid_preset: int,
) -> None:
    """Verify client rejects out-of-bounds presets (-1, 5, 999, etc.) with ValueError."""
    mock_session = MagicMock()
    client = WeheatClient(session=mock_session)

    with pytest.raises(ValueError, match="preset must be between 0 and 4"):
        await client.async_set_performance_mode(
            control_board_id=TEST_CB_ID_BLACKBIRD,
            preset=invalid_preset,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_type_preset",
    ["0", "Performance", None, 3.14, [], {}, "invalid"],
)
async def test_client_performance_mode_invalid_types(
    invalid_type_preset: Any,
) -> None:
    """Verify client rejects non-integer types (strings, None, float, list) with ValueError."""
    mock_session = MagicMock()
    client = WeheatClient(session=mock_session)

    with pytest.raises(ValueError, match="preset must be between 0 and 4"):
        await client.async_set_performance_mode(
            control_board_id=TEST_CB_ID_BLACKBIRD,
            preset=invalid_type_preset,
        )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_option",
    ["-1", "5", "999", "invalid", None, 0, 4, "Silent 5", "Turbo", ""],
)
async def test_select_performance_mode_invalid_options(
    mock_hp_info: HeatPumpInfo,
    sample_settings: ControlBoardSettings,
    invalid_option: Any,
) -> None:
    """Verify WeheatPerformanceModeSelect rejects invalid option strings with ValueError."""
    coord = MagicMock()
    coord.session = None
    coord.data = sample_settings
    entity = WeheatPerformanceModeSelect(
        heat_pump_info=mock_hp_info,
        coordinator=coord,
        entity_description=SELECT_ENTITIES[0],
        client=MagicMock(),
    )

    with pytest.raises(ValueError, match="Invalid option"):
        await entity.async_select_option(invalid_option)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_option",
    ["-1", "5", "999", "invalid", None, 0, 7, "Silent 5", "Off_Alt", ""],
)
async def test_select_silent_hours_preset_invalid_options(
    mock_hp_info: HeatPumpInfo,
    sample_settings: ControlBoardSettings,
    invalid_option: Any,
) -> None:
    """Verify WeheatSilentHoursPresetSelect rejects invalid options with ValueError."""
    coord = MagicMock()
    coord.session = None
    coord.data = sample_settings
    entity = WeheatSilentHoursPresetSelect(
        heat_pump_info=mock_hp_info,
        coordinator=coord,
        entity_description=SELECT_ENTITIES[1],
        client=MagicMock(),
    )

    with pytest.raises(ValueError, match="Invalid option"):
        await entity.async_select_option(invalid_option)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_mode",
    [-1, 5, 999, -50, 10, "Someday", "invalid", "ultra_quiet", "silent 5", "turbo", ""],
)
async def test_service_set_quiet_mode_invalid_modes(
    mock_hp_info: HeatPumpInfo,
    sample_settings: ControlBoardSettings,
    invalid_mode: Any,
) -> None:
    """Verify weheat.set_quiet_mode service raises HomeAssistantError on invalid presets."""
    hass = MagicMock()
    data_coord = MagicMock()
    data_coord.data = MagicMock()
    settings_coord = MagicMock()
    settings_coord.data = sample_settings

    data_item = WeheatData(
        heat_pump_info=mock_hp_info,
        data_coordinator=data_coord,
        energy_coordinator=MagicMock(),
        settings_coordinator=settings_coord,
        client=MagicMock(),
        session=MagicMock(),
    )
    entry = ConfigEntry(runtime_data=[data_item])
    hass.config_entries.async_entries = MagicMock(return_value=[entry])

    call = MagicMock()
    call.data = {"mode": invalid_mode}

    with pytest.raises(HomeAssistantError, match="Invalid quiet mode"):
        await async_handle_set_quiet_mode(hass, call)


@pytest.mark.asyncio
async def test_service_set_quiet_mode_missing_mode(
    mock_hp_info: HeatPumpInfo,
) -> None:
    """Verify weheat.set_quiet_mode raises HomeAssistantError when mode is None or omitted."""
    hass = MagicMock()
    call = MagicMock()
    call.data = {}

    with pytest.raises(HomeAssistantError, match="Missing required parameter: 'mode'"):
        await async_handle_set_quiet_mode(hass, call)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid_preset", [-1, 8, 999, "invalid", "silent 8"])
async def test_service_set_silent_schedule_invalid_preset(
    mock_hp_info: HeatPumpInfo,
    sample_settings: ControlBoardSettings,
    invalid_preset: Any,
) -> None:
    """Verify weheat.set_silent_schedule raises HomeAssistantError on invalid presets."""
    hass = MagicMock()
    settings_coord = MagicMock()
    settings_coord.data = sample_settings

    data_item = WeheatData(
        heat_pump_info=mock_hp_info,
        data_coordinator=MagicMock(),
        energy_coordinator=MagicMock(),
        settings_coordinator=settings_coord,
        client=MagicMock(),
        session=MagicMock(),
    )
    entry = ConfigEntry(runtime_data=[data_item])
    hass.config_entries.async_entries = MagicMock(return_value=[entry])

    call = MagicMock()
    call.data = {
        "preset": invalid_preset,
        "start_time": "22:00",
        "stop_time": "06:00",
    }

    with pytest.raises(HomeAssistantError, match="Invalid silent hours preset"):
        await async_handle_set_silent_schedule(hass, call)


# ===========================================================================
# 2. Quiet Schedule Extreme / Boundary Times & Day Strings
# ===========================================================================


def test_parse_time_valid_boundaries() -> None:
    """Verify _parse_time correctly parses valid boundaries '00:00' and '23:59'."""
    assert _parse_time("00:00") == (0, 0)
    assert _parse_time("23:59") == (23, 59)
    assert _parse_time("0:0") == (0, 0)
    assert _parse_time("12:30") == (12, 30)
    assert _parse_time(time(0, 0)) == (0, 0)
    assert _parse_time(time(23, 59)) == (23, 59)


@pytest.mark.parametrize(
    "invalid_time",
    [
        "24:00",
        "-01:00",
        "invalid",
        None,
        "12:60",
        "-1:30",
        "25:00",
        "00:-01",
        "",
        "23",
        "noon",
        1200,
        [],
        {},
    ],
)
def test_parse_time_invalid_boundaries(invalid_time: Any) -> None:
    """Verify _parse_time rejects extreme/boundary invalid times with HomeAssistantError."""
    with pytest.raises(HomeAssistantError, match="Invalid time format"):
        _parse_time(invalid_time)


def test_parse_days_defaults_and_shortcuts() -> None:
    """Verify _parse_days correctly handles empty, shortcuts, and valid lists."""
    # Empty inputs default to all 7 days
    assert _parse_days([]) == list(DAYS_OF_WEEK)
    assert _parse_days("") == list(DAYS_OF_WEEK)
    assert _parse_days(None) == list(DAYS_OF_WEEK)
    assert _parse_days("all") == list(DAYS_OF_WEEK)
    assert _parse_days("*") == list(DAYS_OF_WEEK)

    # Shortcuts
    assert _parse_days("weekdays") == [
        "Monday",
        "Tuesday",
        "Wednesday",
        "Thursday",
        "Friday",
    ]
    assert _parse_days("weekend") == ["Saturday", "Sunday"]

    # Deduplication and order preservation
    assert _parse_days(["Monday", "Tuesday", "Monday"]) == ["Monday", "Tuesday"]
    assert _parse_days("Monday, Tuesday, Friday") == ["Monday", "Tuesday", "Friday"]


@pytest.mark.parametrize(
    "invalid_days",
    [
        "Someday",
        "123",
        ["Someday"],
        ["123"],
        ["Monday", "Someday"],
        ["Monday", "123"],
        123,
        {"Monday": True},
    ],
)
def test_parse_days_invalid_inputs(invalid_days: Any) -> None:
    """Verify _parse_days raises HomeAssistantError on invalid day strings or non-sequences."""
    with pytest.raises(HomeAssistantError):
        _parse_days(invalid_days)


@pytest.mark.asyncio
async def test_time_entity_valid_boundary_values(
    mock_hp_info: HeatPumpInfo,
    sample_settings: ControlBoardSettings,
) -> None:
    """Verify WeheatTimeEntity accepts time(0, 0) and time(23, 59)."""
    coord = MagicMock()
    coord.session = None
    coord.data = sample_settings
    coord.async_request_refresh = AsyncMock()

    client = MagicMock()
    client.async_set_silent_schedule = AsyncMock(return_value="req_boundary")
    client.async_poll_settings_request = AsyncMock(return_value={"status": 2})

    desc = TIME_ENTITIES[0]  # monday_silent_hours_start
    entity = WeheatTimeEntity(
        heat_pump_info=mock_hp_info,
        coordinator=coord,
        entity_description=desc,
        client=client,
    )

    # Boundary 00:00
    await entity.async_set_value(time(0, 0))
    client.async_set_silent_schedule.assert_awaited_with(
        control_board_id=TEST_CB_ID_BLACKBIRD,
        token=None,
        schedule={"SilentHoursMondayStart": 0, "SilentMinutesMondayStart": 0},
    )

    # Boundary 23:59
    await entity.async_set_value(time(23, 59))
    client.async_set_silent_schedule.assert_awaited_with(
        control_board_id=TEST_CB_ID_BLACKBIRD,
        token=None,
        schedule={"SilentHoursMondayStart": 23, "SilentMinutesMondayStart": 59},
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "invalid_val",
    ["00:00", "23:59", "24:00", "-01:00", "invalid", None, 123, 12.5, []],
)
async def test_time_entity_invalid_types_raise_value_error(
    mock_hp_info: HeatPumpInfo,
    sample_settings: ControlBoardSettings,
    invalid_val: Any,
) -> None:
    """Verify WeheatTimeEntity rejects non-datetime.time instances with ValueError."""
    coord = MagicMock()
    coord.session = None
    coord.data = sample_settings
    entity = WeheatTimeEntity(
        heat_pump_info=mock_hp_info,
        coordinator=coord,
        entity_description=TIME_ENTITIES[0],
    )

    with pytest.raises(ValueError, match="Expected datetime.time"):
        await entity.async_set_value(invalid_val)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("hour", "minute"),
    [
        (-1, 0),
        (24, 0),
        (12, -1),
        (12, 60),
        (100, 100),
    ],
)
async def test_client_set_silent_schedule_out_of_bounds(hour: int, minute: int) -> None:
    """Verify client rejects out-of-bounds hours/minutes with ValueError."""
    client = WeheatClient(session=MagicMock())

    with pytest.raises(ValueError):
        await client.async_set_silent_schedule(
            control_board_id=TEST_CB_ID_BLACKBIRD,
            schedule={
                "SilentHoursMondayStart": hour,
                "SilentMinutesMondayStart": minute,
            },
        )


@pytest.mark.asyncio
async def test_client_set_silent_schedule_empty_dict() -> None:
    """Verify client rejects empty schedule dict with ValueError."""
    client = WeheatClient(session=MagicMock())

    with pytest.raises(ValueError, match="schedule dictionary must not be empty"):
        await client.async_set_silent_schedule(
            control_board_id=TEST_CB_ID_BLACKBIRD,
            schedule={},
        )


@pytest.mark.asyncio
async def test_client_set_silent_schedule_invalid_key() -> None:
    """Verify client rejects unknown schedule key with ValueError."""
    client = WeheatClient(session=MagicMock())

    with pytest.raises(ValueError, match="Unknown schedule key"):
        await client.async_set_silent_schedule(
            control_board_id=TEST_CB_ID_BLACKBIRD,
            schedule={"SilentHoursFundayStart": 12},
        )


# ===========================================================================
# 3. Simulating API Polling Failures, Timeouts, and Reversion
# ===========================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status_code", "status_name"),
    [
        (3, "Cancelled"),
        (4, "Failed"),
        (6, "FailedDeviceWrongState"),
        (7, "FailedWrongExecutionOrder"),
        (8, "FailedDeviceDisconnected"),
        (9, "FailedSettingsRequestMismatch"),
        (10, "FailedSettingsKeyNotFoundInSdf"),
        (11, "FailedAnotherSettingsRequestExecuting"),
    ],
)
async def test_client_poll_settings_request_terminal_statuses(
    status_code: int, status_name: str
) -> None:
    """Verify client.async_poll_settings_request raises WeheatApiError on terminal failure codes."""
    client = WeheatClient(session=MagicMock())
    client.async_get_settings_request_status = AsyncMock(
        return_value={
            "status": status_code,
            "settingsRequestId": TEST_SETTINGS_REQUEST_ID,
        }
    )

    with pytest.raises(WeheatApiError) as exc_info:
        await client.async_poll_settings_request(
            control_board_id=TEST_CB_ID_BLACKBIRD,
            request_id=TEST_SETTINGS_REQUEST_ID,
        )

    assert f"status {status_code}" in str(exc_info.value)
    assert status_name in str(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error_status",
    [3, 4, 11],
)
async def test_time_entity_reverts_optimistic_update_on_polling_error(
    mock_hp_info: HeatPumpInfo,
    error_status: int,
) -> None:
    """Verify WeheatTimeEntity rolls back optimistic local changes and raises HomeAssistantError on polling errors."""
    fixture_dict = load_fixture("installation_settings_get.json")
    original_settings = ControlBoardSettings.from_api_dict(fixture_dict)
    original_start_hour = original_settings.schedules["Monday"].start_hour
    original_start_min = original_settings.schedules["Monday"].start_minute
    assert original_start_hour == 19
    assert original_start_min == 0

    coord = MagicMock()
    coord.session = None
    coord.data = original_settings

    # When refresh is called on rollback, restore server authoritative state
    async def mock_refresh() -> None:
        coord.data = ControlBoardSettings.from_api_dict(fixture_dict)

    coord.async_request_refresh = AsyncMock(side_effect=mock_refresh)

    client = MagicMock()
    client.async_set_silent_schedule = AsyncMock(return_value="req_fail")
    client.async_poll_settings_request = AsyncMock(
        side_effect=WeheatApiError(
            f"Settings request failed with status {error_status} ({SETTINGS_REQUEST_STATUS_MAP.get(error_status)})"
        )
    )

    monday_start_desc = next(
        d for d in TIME_ENTITIES if d.key == "monday_silent_hours_start"
    )
    entity = WeheatTimeEntity(
        heat_pump_info=mock_hp_info,
        coordinator=coord,
        entity_description=monday_start_desc,
        client=client,
    )

    # Attempt to change to 22:30, which should fail during polling
    with pytest.raises(HomeAssistantError, match="Failed to update quiet time"):
        await entity.async_set_value(time(22, 30))

    # Verify coordinator refresh was triggered to revert state
    coord.async_request_refresh.assert_awaited()
    # Verify entity native_value is cleanly reverted to original 19:00
    assert entity.native_value == time(19, 0)


@pytest.mark.asyncio
async def test_time_entity_reverts_on_timeout_error(
    mock_hp_info: HeatPumpInfo,
) -> None:
    """Verify WeheatTimeEntity reverts optimistic state and raises HomeAssistantError on timeout."""
    fixture_dict = load_fixture("installation_settings_get.json")
    coord = MagicMock()
    coord.session = None
    coord.data = ControlBoardSettings.from_api_dict(fixture_dict)

    async def mock_refresh() -> None:
        coord.data = ControlBoardSettings.from_api_dict(fixture_dict)

    coord.async_request_refresh = AsyncMock(side_effect=mock_refresh)

    client = MagicMock()
    client.async_set_silent_schedule = AsyncMock(return_value="req_timeout")
    client.async_poll_settings_request = AsyncMock(
        side_effect=WeheatTimeoutError("Settings request timed out after 30s")
    )

    desc = TIME_ENTITIES[0]
    entity = WeheatTimeEntity(
        heat_pump_info=mock_hp_info,
        coordinator=coord,
        entity_description=desc,
        client=client,
    )

    with pytest.raises(HomeAssistantError, match="Failed to update quiet time"):
        await entity.async_set_value(time(23, 15))

    coord.async_request_refresh.assert_awaited()
    assert entity.native_value == time(19, 0)


@pytest.mark.asyncio
async def test_time_entity_reverts_on_network_drop(
    mock_hp_info: HeatPumpInfo,
) -> None:
    """Verify WeheatTimeEntity reverts optimistic state and raises HomeAssistantError on network drop."""
    fixture_dict = load_fixture("installation_settings_get.json")
    coord = MagicMock()
    coord.session = None
    coord.data = ControlBoardSettings.from_api_dict(fixture_dict)

    async def mock_refresh() -> None:
        coord.data = ControlBoardSettings.from_api_dict(fixture_dict)

    coord.async_request_refresh = AsyncMock(side_effect=mock_refresh)

    client = MagicMock()
    client.async_set_silent_schedule = AsyncMock(return_value="req_drop")
    client.async_poll_settings_request = AsyncMock(
        side_effect=ClientError("Connection reset by peer")
    )

    desc = TIME_ENTITIES[0]
    entity = WeheatTimeEntity(
        heat_pump_info=mock_hp_info,
        coordinator=coord,
        entity_description=desc,
        client=client,
    )

    with pytest.raises(HomeAssistantError, match="Failed to update quiet time"):
        await entity.async_set_value(time(21, 45))

    coord.async_request_refresh.assert_awaited()
    assert entity.native_value == time(19, 0)


@pytest.mark.asyncio
async def test_select_performance_mode_error_handling(
    mock_hp_info: HeatPumpInfo,
    sample_settings: ControlBoardSettings,
) -> None:
    """Verify WeheatPerformanceModeSelect raises HomeAssistantError and does not update on API failure."""
    coord = MagicMock()
    coord.session = None
    coord.data = sample_settings
    coord.async_request_refresh = AsyncMock()

    client = MagicMock()
    client.async_set_performance_mode = AsyncMock(return_value="req_err")
    client.async_poll_settings_request = AsyncMock(
        side_effect=WeheatApiError("Settings request failed with status 4 (Failed)")
    )

    entity = WeheatPerformanceModeSelect(
        heat_pump_info=mock_hp_info,
        coordinator=coord,
        entity_description=SELECT_ENTITIES[0],
        client=client,
    )

    # Initial option from fixture is Performance (0) -> Mode 5 - Max capacity
    assert entity.current_option == "Mode 5 - Max capacity"

    with pytest.raises(HomeAssistantError, match="Failed to set performance mode"):
        await entity.async_select_option("Mode 2 - Low noise")

    # Current option remains unchanged
    assert entity.current_option == "Mode 5 - Max capacity"


@pytest.mark.asyncio
async def test_select_silent_hours_preset_error_handling(
    mock_hp_info: HeatPumpInfo,
    sample_settings: ControlBoardSettings,
) -> None:
    """Verify WeheatSilentHoursPresetSelect raises HomeAssistantError and does not update on API failure."""
    coord = MagicMock()
    coord.session = None
    coord.data = sample_settings
    coord.async_request_refresh = AsyncMock()

    client = MagicMock()
    client.async_set_silent_hours_preset = AsyncMock(return_value="req_err")
    client.async_poll_settings_request = AsyncMock(
        side_effect=WeheatApiError(
            "Settings request failed with status 11 (FailedAnotherSettingsRequestExecuting)"
        )
    )

    entity = WeheatSilentHoursPresetSelect(
        heat_pump_info=mock_hp_info,
        coordinator=coord,
        entity_description=SELECT_ENTITIES[1],
        client=client,
    )

    # Initial option from fixture is Silent 2
    assert entity.current_option == "Silent 2"

    with pytest.raises(HomeAssistantError, match="Failed to set silent hours preset"):
        await entity.async_select_option("Silent 4")

    # Current option remains unchanged
    assert entity.current_option == "Silent 2"


@pytest.mark.asyncio
async def test_switch_silent_hours_error_handling(
    mock_hp_info: HeatPumpInfo,
    sample_settings: ControlBoardSettings,
) -> None:
    """Verify WeheatSilentHoursScheduleSwitch raises HomeAssistantError on API error."""
    coord = MagicMock()
    coord.session = None
    coord.data = sample_settings
    coord.async_request_refresh = AsyncMock()

    client = MagicMock()
    client.async_set_silent_hours_preset = AsyncMock(return_value="req_err")
    client.async_poll_settings_request = AsyncMock(
        side_effect=WeheatApiError("Settings request failed with status 4 (Failed)")
    )

    entity = WeheatSilentHoursScheduleSwitch(
        heat_pump_info=mock_hp_info,
        coordinator=coord,
        entity_description=MagicMock(),
        client=client,
    )

    with pytest.raises(HomeAssistantError, match="Failed to enable silent hours"):
        await entity.async_turn_on()


# ===========================================================================
# 4. Concurrency Stress: Rapid Consecutive & Concurrent Mutations
# ===========================================================================


@pytest.mark.asyncio
async def test_concurrency_rapid_sequential_time_mutations(
    mock_hp_info: HeatPumpInfo,
    sample_settings: ControlBoardSettings,
) -> None:
    """Stress test: 20 rapid sequential mutations on time entities."""
    coord = MagicMock()
    coord.session = None
    coord.data = sample_settings
    coord.async_request_refresh = AsyncMock()

    client = MagicMock()
    client.async_set_silent_schedule = AsyncMock(return_value="req_seq")
    client.async_poll_settings_request = AsyncMock(return_value={"status": 2})

    entity = WeheatTimeEntity(
        heat_pump_info=mock_hp_info,
        coordinator=coord,
        entity_description=TIME_ENTITIES[0],
        client=client,
    )

    for i in range(20):
        target_time = time(i % 24, (i * 2) % 60)
        await entity.async_set_value(target_time)

    assert client.async_set_silent_schedule.await_count == 20
    assert client.async_poll_settings_request.await_count == 20
    assert coord.async_request_refresh.await_count == 20


@pytest.mark.asyncio
async def test_concurrency_simultaneous_time_mutations(
    mock_hp_info: HeatPumpInfo,
    sample_settings: ControlBoardSettings,
) -> None:
    """Stress test: 14 simultaneous time mutations across all 14 time entities."""
    coord = MagicMock()
    coord.session = None
    coord.data = sample_settings
    coord.async_request_refresh = AsyncMock()

    client = MagicMock()
    client.async_set_silent_schedule = AsyncMock(return_value="req_simul")
    client.async_poll_settings_request = AsyncMock(return_value={"status": 2})

    entities = [
        WeheatTimeEntity(
            heat_pump_info=mock_hp_info,
            coordinator=coord,
            entity_description=desc,
            client=client,
        )
        for desc in TIME_ENTITIES
    ]

    async def mutate_entity(ent: WeheatTimeEntity, idx: int) -> None:
        target = time((idx + 8) % 24, (idx * 5) % 60)
        await ent.async_set_value(target)

    # Launch all 14 mutations concurrently
    await asyncio.gather(*(mutate_entity(ent, i) for i, ent in enumerate(entities)))

    assert client.async_set_silent_schedule.await_count == 14
    assert client.async_poll_settings_request.await_count == 14


@pytest.mark.asyncio
async def test_concurrency_rapid_select_mutations(
    mock_hp_info: HeatPumpInfo,
    sample_settings: ControlBoardSettings,
) -> None:
    """Stress test: alternating rapid select option calls."""
    coord = MagicMock()
    coord.session = None
    coord.data = sample_settings
    coord.async_request_refresh = AsyncMock()

    client = MagicMock()
    client.async_set_performance_mode = AsyncMock(return_value="req_select")
    client.async_poll_settings_request = AsyncMock(return_value={"status": 2})

    entity = WeheatPerformanceModeSelect(
        heat_pump_info=mock_hp_info,
        coordinator=coord,
        entity_description=SELECT_ENTITIES[0],
        client=client,
    )

    options_to_cycle = ["Performance", "Silent 1", "Silent 2", "Silent 3", "Silent 4"]
    for opt in options_to_cycle:
        await entity.async_select_option(opt)

    assert client.async_set_performance_mode.await_count == 5
    assert client.async_poll_settings_request.await_count == 5


# ===========================================================================
# 5. DHW Gating Stress: Heat Pumps without DHW (has_dhw == False)
# ===========================================================================


@pytest.mark.asyncio
async def test_dhw_gating_heat_pump_without_dhw_never_instantiates_dhw_switches(
    mock_hp_info_no_dhw: HeatPumpInfo,
    sample_settings: ControlBoardSettings,
) -> None:
    """Verify heat pumps with has_dhw == False NEVER instantiate DHW switches."""
    assert mock_hp_info_no_dhw.has_dhw is False

    coord = MagicMock()
    coord.session = None
    coord.data = sample_settings
    data_coord = MagicMock()
    data_coord.data = HeatPumpLog.from_api_dict(load_fixture("latest_logs.json"))

    data_item = WeheatData(
        heat_pump_info=mock_hp_info_no_dhw,
        data_coordinator=data_coord,
        energy_coordinator=MagicMock(),
        settings_coordinator=coord,
        client=MagicMock(),
        session=MagicMock(),
    )
    entry = ConfigEntry(runtime_data=[data_item])

    added_entities: list[Any] = []
    await async_setup_switch_entry(MagicMock(), entry, added_entities.extend)

    # Exactly 1 switch entity should be created: Silent Hours Schedule Switch
    assert len(added_entities) == 1
    assert isinstance(added_entities[0], WeheatSilentHoursScheduleSwitch)

    # CRITICAL: Legionella and DHW Boost must NEVER be instantiated
    for entity in added_entities:
        assert not isinstance(entity, WeheatLegionellaPreventionSwitch)
        assert not isinstance(entity, WeheatDhwBoostSwitch)
        assert entity.entity_description.key != "legionella_prevention"
        assert entity.entity_description.key != "dhw_boost"


@pytest.mark.asyncio
async def test_dhw_gating_heat_pump_with_dhw_instantiates_all_switches(
    mock_hp_info: HeatPumpInfo,
    sample_settings: ControlBoardSettings,
) -> None:
    """Verify heat pumps with has_dhw == True instantiate all 3 switch entities."""
    assert mock_hp_info.has_dhw is True

    coord = MagicMock()
    coord.session = None
    coord.data = sample_settings
    data_coord = MagicMock()
    data_coord.data = HeatPumpLog.from_api_dict(load_fixture("latest_logs.json"))

    data_item = WeheatData(
        heat_pump_info=mock_hp_info,
        data_coordinator=data_coord,
        energy_coordinator=MagicMock(),
        settings_coordinator=coord,
        client=MagicMock(),
        session=MagicMock(),
    )
    entry = ConfigEntry(runtime_data=[data_item])

    added_entities: list[Any] = []
    await async_setup_switch_entry(MagicMock(), entry, added_entities.extend)

    # All 3 switch entities must be instantiated
    assert len(added_entities) == 3
    entity_types = {type(e) for e in added_entities}
    assert WeheatSilentHoursScheduleSwitch in entity_types
    assert WeheatLegionellaPreventionSwitch in entity_types
    assert WeheatDhwBoostSwitch in entity_types


def test_dhw_gating_model_parsing_matrix() -> None:
    """Verify HeatPumpInfo.from_api_dict strictly decodes has_dhw across API variations."""
    base_dict: dict[str, Any] = {
        "id": "mock-hp-123",
        "name": "Test Pump",
        "model": 0,
        "serialNumber": "WH-TEST",
    }

    # dhwType == 1 -> has_dhw == True
    info_dhw = HeatPumpInfo.from_api_dict({**base_dict, "dhwType": 1})
    assert info_dhw.has_dhw is True

    # dhwType == 0 -> has_dhw == False
    info_no_dhw = HeatPumpInfo.from_api_dict({**base_dict, "dhwType": 0})
    assert info_no_dhw.has_dhw is False

    # dhwType == None -> has_dhw == False
    info_none_dhw = HeatPumpInfo.from_api_dict({**base_dict, "dhwType": None})
    assert info_none_dhw.has_dhw is False

    # missing dhwType -> has_dhw == False
    info_missing_dhw = HeatPumpInfo.from_api_dict({**base_dict})
    assert info_missing_dhw.has_dhw is False

    # dhwType == 2 (unknown) -> has_dhw == False
    info_other_dhw = HeatPumpInfo.from_api_dict({**base_dict, "dhwType": 2})
    assert info_other_dhw.has_dhw is False
