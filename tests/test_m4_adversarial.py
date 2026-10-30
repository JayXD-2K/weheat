"""Milestone 4 Adversarial Stress Testing: Custom Action Services, Target Resolution, and Settings Coordinator.

Empirical verification suite designed by Challenger 2 (m4_r1_chal2).
Covers:
1. Target resolution edge cases and input validation
2. Action payload injections (non-hashable types, nulls, unexpected keys)
3. WeheatSettingsUpdateCoordinator concurrency, fallback, and failure modes
4. Clean unloading of services and post-unload invocation failure
"""

from __future__ import annotations

import asyncio
import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp import ClientError, ClientResponseError
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_TOKEN
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.weheat import async_unload_entry
from custom_components.weheat.client import (
    WeheatApiError,
    WeheatAuthError,
    WeheatClient,
    WeheatTimeoutError,
)
from custom_components.weheat.const import DOMAIN, SETTINGS_UPDATE_INTERVAL
from custom_components.weheat.coordinator import (
    WeheatData,
    WeheatSettingsUpdateCoordinator,
)
from custom_components.weheat.models import (
    ControlBoardSettings,
    HeatPumpInfo,
)
from custom_components.weheat.services import (
    SERVICE_SET_QUIET_MODE,
    SERVICE_SET_SILENT_SCHEDULE,
    SERVICE_TRIGGER_LEGIONELLA,
    _resolve_target_data,
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
TEST_INSTALLATION_ID_1 = "inst-00000000-0000-0000-0000-000000000001"
TEST_TOKEN = "mock_token_adv123"
TEST_REQ_ID = "00000000-0000-0000-0000-000000000999"


@pytest.fixture
def mock_hp_info_1() -> HeatPumpInfo:
    return HeatPumpInfo(
        heatpump_id=TEST_HP_UUID_1,
        name="Blackbird Pump",
        model=0,
        serial_number="SN-001",
        control_board_id=TEST_CB_ID_1,
        installation_id=TEST_INSTALLATION_ID_1,
        has_dhw=True,
    )


@pytest.fixture
def mock_hp_info_2() -> HeatPumpInfo:
    return HeatPumpInfo(
        heatpump_id=TEST_HP_UUID_2,
        name="Sparrow Pump",
        model=2,
        serial_number="SN-002",
        control_board_id=TEST_CB_ID_2,
        installation_id="inst-00000000-0000-0000-0000-000000000002",
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
    client.async_get_installation_settings = AsyncMock(
        return_value=ControlBoardSettings(
            performance_mode_preset=0,
            silent_hours_preset=0,
        )
    )
    client.async_get_installation_topology = AsyncMock(
        return_value=[
            {
                "id": TEST_HP_UUID_1,
                "heatPumpId": TEST_HP_UUID_1,
                "installationId": TEST_INSTALLATION_ID_1,
                "controlBoardId": TEST_CB_ID_1,
            }
        ]
    )
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
    mock_hp_info_1: HeatPumpInfo,
    mock_weheat_client: MagicMock,
    mock_session: MagicMock,
    mock_coordinator: MagicMock,
) -> WeheatData:
    return WeheatData(
        heat_pump_info=mock_hp_info_1,
        data_coordinator=mock_coordinator,
        energy_coordinator=mock_coordinator,
        settings_coordinator=mock_coordinator,
        client=mock_weheat_client,
        session=mock_session,
    )


@pytest.fixture
def mock_weheat_data_2(
    mock_hp_info_2: HeatPumpInfo,
    mock_weheat_client: MagicMock,
    mock_session: MagicMock,
    mock_coordinator: MagicMock,
) -> WeheatData:
    return WeheatData(
        heat_pump_info=mock_hp_info_2,
        data_coordinator=mock_coordinator,
        energy_coordinator=mock_coordinator,
        settings_coordinator=mock_coordinator,
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


# =============================================================================
# 1. TARGET RESOLUTION TESTS & EDGE CASES
# =============================================================================


@pytest.mark.asyncio
async def test_target_resolution_single_pump_automatic_resolution(
    hass_with_single_pump: HomeAssistant,
) -> None:
    """Verify single-pump environments automatically resolve when no target is provided."""
    call = ServiceCall(DOMAIN, SERVICE_SET_QUIET_MODE, {"mode": 0})
    targets = _resolve_target_data(hass_with_single_pump, call)
    assert len(targets) == 1
    assert targets[0].heat_pump_info.heatpump_id == TEST_HP_UUID_1


@pytest.mark.asyncio
async def test_target_resolution_multi_pump_without_target_raises_home_assistant_error(
    hass_with_two_pumps: HomeAssistant,
) -> None:
    """Verify multi-pump environments without target raise HomeAssistantError."""
    call = ServiceCall(DOMAIN, SERVICE_SET_QUIET_MODE, {"mode": 0})
    with pytest.raises(HomeAssistantError) as exc_info:
        _resolve_target_data(hass_with_two_pumps, call)
    assert "Multiple Weheat heat pumps found" in str(exc_info.value)


@pytest.mark.asyncio
async def test_target_resolution_non_existent_device_id_raises_home_assistant_error(
    hass_with_single_pump: HomeAssistant,
) -> None:
    """Verify calling action with non-existent device_id raises HomeAssistantError."""
    call = ServiceCall(
        DOMAIN,
        SERVICE_SET_QUIET_MODE,
        {"mode": 0, "device_id": "non_existent_device_id_12345"},
    )
    with pytest.raises(HomeAssistantError) as exc_info:
        _resolve_target_data(hass_with_single_pump, call)
    assert "No Weheat heat pump matched device ID" in str(exc_info.value)


@pytest.mark.asyncio
async def test_target_resolution_no_loaded_entries_raises_home_assistant_error() -> (
    None
):
    """Verify target resolution raises HomeAssistantError when no config entries are loaded."""
    empty_hass = HomeAssistant()
    call = ServiceCall(DOMAIN, SERVICE_SET_QUIET_MODE, {"mode": 0})
    with pytest.raises(HomeAssistantError) as exc_info:
        _resolve_target_data(empty_hass, call)
    assert "No Weheat heat pump entries are currently loaded" in str(exc_info.value)


@pytest.mark.asyncio
async def test_target_resolution_non_existent_entity_id_must_raise(
    hass_with_single_pump: HomeAssistant,
) -> None:
    """Verify calling action with non-existent entity_id raises HomeAssistantError."""
    call = ServiceCall(
        DOMAIN,
        SERVICE_SET_QUIET_MODE,
        {"mode": 0, "entity_id": "sensor.non_existent_entity_id_12345"},
    )
    with pytest.raises(HomeAssistantError) as exc_info:
        _resolve_target_data(hass_with_single_pump, call)
    assert "No Weheat heat pump matched entity ID" in str(exc_info.value)


@pytest.mark.asyncio
async def test_target_resolution_malformed_device_id_int_must_raise_home_assistant_error(
    hass_with_single_pump: HomeAssistant,
) -> None:
    """Verify integer device_id raises HomeAssistantError instead of crashing with TypeError."""
    call = ServiceCall(
        DOMAIN,
        SERVICE_SET_QUIET_MODE,
        {"mode": 0, "device_id": 12345},
    )
    with pytest.raises(HomeAssistantError):
        _resolve_target_data(hass_with_single_pump, call)


@pytest.mark.asyncio
async def test_target_resolution_malformed_entity_id_int_must_raise_home_assistant_error(
    hass_with_single_pump: HomeAssistant,
) -> None:
    """Verify integer entity_id raises HomeAssistantError instead of crashing with TypeError."""
    call = ServiceCall(
        DOMAIN,
        SERVICE_SET_QUIET_MODE,
        {"mode": 0, "entity_id": 99999},
    )
    with pytest.raises(HomeAssistantError):
        _resolve_target_data(hass_with_single_pump, call)


# =============================================================================
# 2. ACTION PAYLOAD INJECTIONS (UNEXPECTED KEYS, NULL VALUES, TYPE MISMATCHES)
# =============================================================================


@pytest.mark.asyncio
async def test_set_quiet_mode_with_unexpected_extra_keys_succeeds(
    hass_with_single_pump: HomeAssistant,
    mock_weheat_client: MagicMock,
) -> None:
    """Verify unexpected extra keys in payload are safely tolerated."""
    call = ServiceCall(
        DOMAIN,
        SERVICE_SET_QUIET_MODE,
        {"mode": 0, "unexpected_junk_key": "some_value", "inject": {"nested": True}},
    )
    await async_handle_set_quiet_mode(hass_with_single_pump, call)
    mock_weheat_client.async_set_performance_mode.assert_called_once()


@pytest.mark.asyncio
async def test_set_quiet_mode_missing_mode_raises_home_assistant_error(
    hass_with_single_pump: HomeAssistant,
) -> None:
    """Verify missing mode parameter raises HomeAssistantError."""
    call = ServiceCall(DOMAIN, SERVICE_SET_QUIET_MODE, {})
    with pytest.raises(HomeAssistantError) as exc_info:
        await async_handle_set_quiet_mode(hass_with_single_pump, call)
    assert "Missing required parameter: 'mode'" in str(exc_info.value)


@pytest.mark.asyncio
async def test_set_quiet_mode_unhashable_dict_injection_must_raise_home_assistant_error(
    hass_with_single_pump: HomeAssistant,
) -> None:
    """Verify unhashable dict mode raises HomeAssistantError, not unhandled TypeError."""
    call = ServiceCall(DOMAIN, SERVICE_SET_QUIET_MODE, {"mode": {"unexpected": "dict"}})
    with pytest.raises(HomeAssistantError):
        await async_handle_set_quiet_mode(hass_with_single_pump, call)


@pytest.mark.asyncio
async def test_set_quiet_mode_unhashable_list_injection_must_raise_home_assistant_error(
    hass_with_single_pump: HomeAssistant,
) -> None:
    """Verify unhashable list mode raises HomeAssistantError, not unhandled TypeError."""
    call = ServiceCall(DOMAIN, SERVICE_SET_QUIET_MODE, {"mode": [1, 2, 3]})
    with pytest.raises(HomeAssistantError):
        await async_handle_set_quiet_mode(hass_with_single_pump, call)


@pytest.mark.asyncio
async def test_set_silent_schedule_missing_params_raises_home_assistant_error(
    hass_with_single_pump: HomeAssistant,
) -> None:
    """Verify missing required parameters in set_silent_schedule raise HomeAssistantError."""
    # Missing preset
    with pytest.raises(HomeAssistantError):
        await async_handle_set_silent_schedule(
            hass_with_single_pump,
            ServiceCall(
                DOMAIN,
                SERVICE_SET_SILENT_SCHEDULE,
                {"start_time": "22:00", "stop_time": "07:00"},
            ),
        )
    # Missing start_time
    with pytest.raises(HomeAssistantError):
        await async_handle_set_silent_schedule(
            hass_with_single_pump,
            ServiceCall(
                DOMAIN, SERVICE_SET_SILENT_SCHEDULE, {"preset": 1, "stop_time": "07:00"}
            ),
        )
    # Missing stop_time
    with pytest.raises(HomeAssistantError):
        await async_handle_set_silent_schedule(
            hass_with_single_pump,
            ServiceCall(
                DOMAIN,
                SERVICE_SET_SILENT_SCHEDULE,
                {"preset": 1, "start_time": "22:00"},
            ),
        )


@pytest.mark.asyncio
async def test_set_silent_schedule_unhashable_preset_must_raise_home_assistant_error(
    hass_with_single_pump: HomeAssistant,
) -> None:
    """Verify unhashable dict preset raises HomeAssistantError, not unhandled TypeError."""
    call = ServiceCall(
        DOMAIN,
        SERVICE_SET_SILENT_SCHEDULE,
        {"preset": {"invalid": "dict"}, "start_time": "22:00", "stop_time": "07:00"},
    )
    with pytest.raises(HomeAssistantError):
        await async_handle_set_silent_schedule(hass_with_single_pump, call)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_time",
    [
        {"hour": 22, "minute": 0},
        12345,
        ["22:00"],
    ],
)
async def test_set_silent_schedule_time_injections_raise_home_assistant_error(
    hass_with_single_pump: HomeAssistant,
    bad_time: Any,
) -> None:
    """Verify malformed time payload types raise HomeAssistantError."""
    call = ServiceCall(
        DOMAIN,
        SERVICE_SET_SILENT_SCHEDULE,
        {"preset": 1, "start_time": bad_time, "stop_time": "07:00"},
    )
    with pytest.raises(HomeAssistantError):
        await async_handle_set_silent_schedule(hass_with_single_pump, call)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_days",
    [
        {"Monday": True},
        12345,
        ["NotADay"],
    ],
)
async def test_set_silent_schedule_days_injections_raise_home_assistant_error(
    hass_with_single_pump: HomeAssistant,
    bad_days: Any,
) -> None:
    """Verify malformed days payload types raise HomeAssistantError."""
    call = ServiceCall(
        DOMAIN,
        SERVICE_SET_SILENT_SCHEDULE,
        {"preset": 1, "start_time": "22:00", "stop_time": "07:00", "days": bad_days},
    )
    with pytest.raises(HomeAssistantError):
        await async_handle_set_silent_schedule(hass_with_single_pump, call)


@pytest.mark.asyncio
async def test_service_call_oauth_token_refresh_client_error_must_raise_home_assistant_error(
    hass_with_single_pump: HomeAssistant,
    mock_session: MagicMock,
) -> None:
    """Verify token refresh failure during service call raises HomeAssistantError, not raw ClientResponseError."""
    mock_session.async_ensure_token_valid = AsyncMock(
        side_effect=ClientResponseError(
            request_info=MagicMock(), history=(), status=401, message="Unauthorized"
        )
    )
    call = ServiceCall(DOMAIN, SERVICE_SET_QUIET_MODE, {"mode": 0})
    with pytest.raises(HomeAssistantError):
        await async_handle_set_quiet_mode(hass_with_single_pump, call)


# =============================================================================
# 3. WEHEAT SETTINGS UPDATE COORDINATOR STRESS & CONCURRENCY TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_settings_coordinator_interval(mock_hp_info_1: HeatPumpInfo) -> None:
    """Verify WeheatSettingsUpdateCoordinator polling interval is 300 seconds."""
    hass = HomeAssistant()
    entry = ConfigEntry()
    coord = WeheatSettingsUpdateCoordinator(hass, entry, None, mock_hp_info_1)
    assert coord.update_interval == datetime.timedelta(seconds=SETTINGS_UPDATE_INTERVAL)
    assert coord.update_interval.total_seconds() == 300


@pytest.mark.asyncio
async def test_settings_coordinator_success(
    mock_hp_info_1: HeatPumpInfo,
    mock_weheat_client: MagicMock,
) -> None:
    """Verify settings coordinator successfully fetches settings snapshot."""
    hass = HomeAssistant()
    entry = ConfigEntry()
    coord = WeheatSettingsUpdateCoordinator(
        hass, entry, None, mock_hp_info_1, client=mock_weheat_client
    )

    data = await coord._async_update_data()
    assert isinstance(data, ControlBoardSettings)
    mock_weheat_client.async_get_installation_settings.assert_called_once_with(
        TEST_INSTALLATION_ID_1, token=None
    )


@pytest.mark.asyncio
async def test_settings_coordinator_rapid_concurrent_calls(
    mock_hp_info_1: HeatPumpInfo,
    mock_weheat_client: MagicMock,
) -> None:
    """Verify settings coordinator handles rapid concurrent calls without failure or race conditions."""
    hass = HomeAssistant()
    entry = ConfigEntry()
    coord = WeheatSettingsUpdateCoordinator(
        hass, entry, None, mock_hp_info_1, client=mock_weheat_client
    )

    # Launch 50 concurrent updates
    results = await asyncio.gather(*[coord._async_update_data() for _ in range(50)])
    assert len(results) == 50
    for res in results:
        assert isinstance(res, ControlBoardSettings)


@pytest.mark.asyncio
async def test_settings_coordinator_missing_installation_id_topology_fallback_success(
    mock_weheat_client: MagicMock,
) -> None:
    """Verify fallback to topology discovery when heat_pump_info.installation_id is initially None."""
    hass = HomeAssistant()
    entry = ConfigEntry()
    hp_info = HeatPumpInfo(
        heatpump_id=TEST_HP_UUID_1,
        name="No Installation ID Pump",
        model=0,
        serial_number="SN-001",
        installation_id=None,
    )
    coord = WeheatSettingsUpdateCoordinator(
        hass, entry, None, hp_info, client=mock_weheat_client
    )

    data = await coord._async_update_data()
    assert isinstance(data, ControlBoardSettings)
    assert hp_info.installation_id == TEST_INSTALLATION_ID_1
    mock_weheat_client.async_get_installation_topology.assert_called_once()
    mock_weheat_client.async_get_installation_settings.assert_called_once_with(
        TEST_INSTALLATION_ID_1, token=None
    )


@pytest.mark.asyncio
async def test_settings_coordinator_missing_installation_id_topology_fallback_failure(
    mock_weheat_client: MagicMock,
) -> None:
    """Verify failure to resolve installation topology raises UpdateFailed."""
    hass = HomeAssistant()
    entry = ConfigEntry()
    hp_info = HeatPumpInfo(
        heatpump_id="unknown-pump-uuid",
        name="Unknown Pump",
        model=0,
        serial_number="SN-999",
        installation_id=None,
    )
    coord = WeheatSettingsUpdateCoordinator(
        hass, entry, None, hp_info, client=mock_weheat_client
    )

    with pytest.raises(UpdateFailed) as exc_info:
        await coord._async_update_data()
    assert "Missing installation ID" in str(exc_info.value)


@pytest.mark.asyncio
async def test_settings_coordinator_error_mapping(
    mock_hp_info_1: HeatPumpInfo,
    mock_weheat_client: MagicMock,
) -> None:
    """Verify error mapping in WeheatSettingsUpdateCoordinator."""
    hass = HomeAssistant()
    entry = ConfigEntry()
    coord = WeheatSettingsUpdateCoordinator(
        hass, entry, None, mock_hp_info_1, client=mock_weheat_client
    )

    # 1. WeheatAuthError -> ConfigEntryAuthFailed
    mock_weheat_client.async_get_installation_settings = AsyncMock(
        side_effect=WeheatAuthError("401 Unauthorized", status_code=401)
    )
    with pytest.raises(ConfigEntryAuthFailed):
        await coord._async_update_data()

    # 2. WeheatApiError -> UpdateFailed
    mock_weheat_client.async_get_installation_settings = AsyncMock(
        side_effect=WeheatApiError("500 Server Error", status_code=500)
    )
    with pytest.raises(UpdateFailed):
        await coord._async_update_data()

    # 3. WeheatTimeoutError -> UpdateFailed
    mock_weheat_client.async_get_installation_settings = AsyncMock(
        side_effect=WeheatTimeoutError("Request timed out")
    )
    with pytest.raises(UpdateFailed):
        await coord._async_update_data()

    # 4. ClientError -> UpdateFailed
    mock_weheat_client.async_get_installation_settings = AsyncMock(
        side_effect=ClientError("Connection reset by peer")
    )
    with pytest.raises(UpdateFailed):
        await coord._async_update_data()


@pytest.mark.asyncio
async def test_settings_coordinator_oauth_token_validation(
    mock_hp_info_1: HeatPumpInfo,
    mock_weheat_client: MagicMock,
) -> None:
    """Verify OAuth2Session token renewal errors in WeheatSettingsUpdateCoordinator."""
    hass = HomeAssistant()
    entry = ConfigEntry()
    mock_session = MagicMock()
    coord = WeheatSettingsUpdateCoordinator(
        hass,
        entry,
        session=mock_session,
        heat_pump_info=mock_hp_info_1,
        client=mock_weheat_client,
    )

    # 401 ClientResponseError -> ConfigEntryAuthFailed
    mock_session.async_ensure_token_valid = AsyncMock(
        side_effect=ClientResponseError(
            request_info=MagicMock(), history=(), status=401, message="Unauthorized"
        )
    )
    with pytest.raises(ConfigEntryAuthFailed):
        await coord._async_update_data()

    # 500 ClientResponseError -> UpdateFailed
    mock_session.async_ensure_token_valid = AsyncMock(
        side_effect=ClientResponseError(
            request_info=MagicMock(), history=(), status=500, message="Server error"
        )
    )
    with pytest.raises(UpdateFailed):
        await coord._async_update_data()

    # Network generic error -> UpdateFailed
    mock_session.async_ensure_token_valid = AsyncMock(
        side_effect=RuntimeError("DNS failure")
    )
    with pytest.raises(UpdateFailed):
        await coord._async_update_data()


@pytest.mark.asyncio
async def test_settings_coordinator_missing_heat_pump_info_raises_update_failed() -> (
    None
):
    """Verify coordinator raises UpdateFailed when heat_pump_info is None."""
    hass = HomeAssistant()
    entry = ConfigEntry()
    coord = WeheatSettingsUpdateCoordinator(hass, entry, None, None)
    with pytest.raises(UpdateFailed) as exc_info:
        await coord._async_update_data()
    assert "Heat pump information is missing" in str(exc_info.value)


# =============================================================================
# 4. SERVICES UNLOAD & LIFECYCLE TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_services_unloaded_when_last_config_entry_unloads() -> None:
    """Verify async_unload_entry unloads services when the last config entry is removed."""
    hass = HomeAssistant()
    await async_setup_services(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_SET_QUIET_MODE)
    assert hass.services.has_service(DOMAIN, SERVICE_SET_SILENT_SCHEDULE)
    assert hass.services.has_service(DOMAIN, SERVICE_TRIGGER_LEGIONELLA)

    entry = ConfigEntry(entry_id="single_entry", data={})
    hass.config_entries.async_add(entry)

    # Unload entry
    unload_ok = await async_unload_entry(hass, entry)
    assert unload_ok is True

    # Services must now be removed
    assert not hass.services.has_service(DOMAIN, SERVICE_SET_QUIET_MODE)
    assert not hass.services.has_service(DOMAIN, SERVICE_SET_SILENT_SCHEDULE)
    assert not hass.services.has_service(DOMAIN, SERVICE_TRIGGER_LEGIONELLA)

    # Calling an unloaded service raises HomeAssistantError
    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SET_QUIET_MODE,
            {"mode": 0},
        )


@pytest.mark.asyncio
async def test_services_retained_when_other_config_entries_remain() -> None:
    """Verify services remain registered if another Weheat config entry is still active."""
    hass = HomeAssistant()
    await async_setup_services(hass)

    entry1 = ConfigEntry(entry_id="entry_1", data={})
    entry2 = ConfigEntry(entry_id="entry_2", data={})
    hass.config_entries.async_add(entry1)
    hass.config_entries.async_add(entry2)

    # Unload first entry only
    unload_ok = await async_unload_entry(hass, entry1)
    assert unload_ok is True

    # Services MUST remain registered because entry2 is still active
    assert hass.services.has_service(DOMAIN, SERVICE_SET_QUIET_MODE)
    assert hass.services.has_service(DOMAIN, SERVICE_SET_SILENT_SCHEDULE)
    assert hass.services.has_service(DOMAIN, SERVICE_TRIGGER_LEGIONELLA)

    # Now unload second entry
    hass.config_entries._entries[DOMAIN] = [entry2]
    unload_ok = await async_unload_entry(hass, entry2)
    assert unload_ok is True

    # Now services must be removed
    assert not hass.services.has_service(DOMAIN, SERVICE_SET_QUIET_MODE)
    assert not hass.services.has_service(DOMAIN, SERVICE_SET_SILENT_SCHEDULE)
    assert not hass.services.has_service(DOMAIN, SERVICE_TRIGGER_LEGIONELLA)


@pytest.mark.asyncio
async def test_service_unloading_idempotent() -> None:
    """Verify async_unload_services is safely idempotent when called multiple times."""
    hass = HomeAssistant()
    await async_setup_services(hass)
    await async_unload_services(hass)
    # Re-unloading should be clean
    await async_unload_services(hass)
    assert not hass.services.has_service(DOMAIN, SERVICE_SET_QUIET_MODE)


# =============================================================================
# 5. LEGIONELLA TRIGGER ACTION TESTS
# =============================================================================


@pytest.mark.asyncio
async def test_trigger_legionella_action_success(
    hass_with_single_pump: HomeAssistant,
    mock_weheat_client: MagicMock,
    mock_coordinator: MagicMock,
) -> None:
    """Verify trigger_legionella_prevention action dispatches call and refreshes."""
    call = ServiceCall(DOMAIN, SERVICE_TRIGGER_LEGIONELLA, {})
    await async_handle_trigger_legionella(hass_with_single_pump, call)
    mock_weheat_client.async_trigger_legionella_prevention.assert_called_once_with(
        control_board_id=TEST_CB_ID_1,
        token=TEST_TOKEN,
    )
    mock_coordinator.async_request_refresh.assert_called_once()


@pytest.mark.asyncio
async def test_trigger_legionella_action_unacknowledged_raises_error(
    hass_with_single_pump: HomeAssistant,
    mock_weheat_client: MagicMock,
) -> None:
    """Verify unacknowledged legionella action (returns False) raises HomeAssistantError."""
    mock_weheat_client.async_trigger_legionella_prevention = AsyncMock(
        return_value=False
    )
    call = ServiceCall(DOMAIN, SERVICE_TRIGGER_LEGIONELLA, {})
    with pytest.raises(HomeAssistantError) as exc_info:
        await async_handle_trigger_legionella(hass_with_single_pump, call)
    assert "was not acknowledged" in str(exc_info.value)
