"""Dedicated test suite for Milestone 3: Casing, Target ID & Write Entities.

Covers:
- camelCase vs PascalCase parsing in ControlBoardSettings
- Nested heatPump parsing in HeatPumpInfo
- Bundled 30-key PascalCase payload generation
- Target ID resolution and caching in WeheatClient
- Auth guards via check_portal_auth() across entities and services
- Preset constraint rules and clean adjustment
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError

from custom_components.weheat.client import WeheatClient
from custom_components.weheat.const import DAYS_OF_WEEK, DOMAIN
from custom_components.weheat.coordinator import WeheatData
from custom_components.weheat.models import (
    ControlBoardSettings,
    HeatPumpInfo,
)
from custom_components.weheat.select import (
    SELECT_ENTITIES,
    WeheatPerformanceModeSelect,
    WeheatSilentHoursPresetSelect,
)
from custom_components.weheat.services import (
    SERVICE_SET_QUIET_MODE,
    SERVICE_SET_SILENT_SCHEDULE,
    SERVICE_TRIGGER_LEGIONELLA,
    async_handle_set_quiet_mode,
    async_handle_set_silent_schedule,
    async_handle_trigger_legionella,
)
from custom_components.weheat.switch import (
    SWITCH_DESCRIPTIONS,
    WeheatDhwBoostSwitch,
    WeheatLegionellaPreventionSwitch,
    WeheatSilentHoursScheduleSwitch,
)
from custom_components.weheat.time import (
    TIME_ENTITIES,
    WeheatTimeEntity,
)

# -----------------------------------------------------------------------------
# 1. Model Parsing & Casing Tests
# -----------------------------------------------------------------------------


def test_control_board_settings_from_camelcase_api_dict() -> None:
    """Verify live portal camelCase settings format parses accurately."""
    payload = {
        "performanceModePreset": 2,
        "silentHoursScheduledPreset": 3,
        "dhwTargetTemperature": 55.5,
        "silentHoursMondayStart": 22,
        "silentMinutesMondayStart": 15,
        "silentHoursMondayStop": 6,
        "silentMinutesMondayStop": 45,
        "silentHoursTuesdayStart": 23,
        "silentMinutesTuesdayStart": 0,
        "silentHoursTuesdayStop": 7,
        "silentMinutesTuesdayStop": 30,
        "silentHoursWednesdayStart": 21,
        "silentMinutesWednesdayStart": 30,
        "silentHoursWednesdayStop": 6,
        "silentMinutesWednesdayStop": 0,
        "silentHoursThursdayStart": 22,
        "silentMinutesThursdayStart": 0,
        "silentHoursThursdayStop": 7,
        "silentMinutesThursdayStop": 0,
        "silentHoursFridayStart": 23,
        "silentMinutesFridayStart": 30,
        "silentHoursFridayStop": 8,
        "silentMinutesFridayStop": 0,
        "silentHoursSaturdayStart": 0,
        "silentMinutesSaturdayStart": 0,
        "silentHoursSaturdayStop": 8,
        "silentMinutesSaturdayStop": 30,
        "silentHoursSundayStart": 21,
        "silentMinutesSundayStart": 45,
        "silentHoursSundayStop": 7,
        "silentMinutesSundayStop": 15,
    }

    settings = ControlBoardSettings.from_api_dict(payload)
    assert settings.performance_mode_preset == 2
    assert settings.silent_hours_preset == 3
    assert settings.dhw_target_temperature == 55.5

    # Check Monday schedule
    mon = settings.schedules["Monday"]
    assert mon.start_hour == 22
    assert mon.start_minute == 15
    assert mon.stop_hour == 6
    assert mon.stop_minute == 45
    assert mon.start_time_str == "22:15"
    assert mon.stop_time_str == "06:45"

    # Check Sunday schedule
    sun = settings.schedules["Sunday"]
    assert sun.start_hour == 21
    assert sun.start_minute == 45
    assert sun.stop_hour == 7
    assert sun.stop_minute == 15


def test_control_board_settings_from_nested_settings_dict() -> None:
    """Verify wrapped {"settings": {...}} dict is parsed correctly."""
    nested = {
        "settings": {
            "performanceModePreset": 1,
            "silentHoursScheduledPreset": 4,
            "dhwTargetTemperature": 48.0,
        }
    }
    settings = ControlBoardSettings.from_api_dict(nested)
    assert settings.performance_mode_preset == 1
    assert settings.silent_hours_preset == 4
    assert settings.dhw_target_temperature == 48.0


def test_control_board_settings_from_pascalcase_fallback() -> None:
    """Verify PascalCase legacy / fixture dictionary parses correctly."""
    pascal_payload = {
        "PerformanceModePreset": 0,
        "SilentHoursScheduledPreset": 2,
        "DhwTargetTemperature": 52.0,
        "SilentHoursFridayStart": 22,
        "SilentMinutesFridayStart": 0,
        "SilentHoursFridayStop": 7,
        "SilentMinutesFridayStop": 0,
    }
    settings = ControlBoardSettings.from_api_dict(pascal_payload)
    assert settings.performance_mode_preset == 0
    assert settings.silent_hours_preset == 2
    assert settings.dhw_target_temperature == 52.0
    fri = settings.schedules["Friday"]
    assert fri.start_hour == 22
    assert fri.stop_hour == 7


def test_control_board_settings_safe_none_handling() -> None:
    """Verify None and empty input safely yields default settings."""
    settings_none = ControlBoardSettings.from_api_dict(None)
    assert settings_none.performance_mode_preset == 0
    assert settings_none.silent_hours_preset == 0
    assert len(settings_none.schedules) == 0

    settings_empty = ControlBoardSettings.from_api_dict({})
    assert settings_empty.performance_mode_preset == 0
    assert len(settings_empty.schedules) == 7


def test_heat_pump_info_nested_heat_pump_structure() -> None:
    """Verify HeatPumpInfo parses nested heatPump returned by /installations/fully-expanded."""
    topology_item = {
        "id": "inst-12345",
        "name": "Home Installation",
        "heatPump": {
            "id": "hp-uuid-67890",
            "name": "Blackbird 8kW",
            "model": 0,
            "serialNumber": "SN-WH-9999",
            "controlBoardId": "cb-uuid-abcde",
            "hasDhw": True,
        },
    }
    info = HeatPumpInfo.from_api_dict(topology_item)
    assert info.heatpump_id == "hp-uuid-67890"
    assert info.control_board_id == "cb-uuid-abcde"
    assert info.installation_id == "inst-12345"
    assert info.name == "Blackbird 8kW"
    assert info.model == 0
    assert info.serial_number == "SN-WH-9999"
    assert info.has_dhw is True


# -----------------------------------------------------------------------------
# 2. Bundled 30-Key Payload Generation Tests
# -----------------------------------------------------------------------------


def test_build_bundled_silent_schedule_payload_exact_keys() -> None:
    """Verify bundled silent schedule payload contains exactly 30 PascalCase keys."""
    settings = ControlBoardSettings(
        performance_mode_preset=1,
        silent_hours_preset=3,
    )
    wrapped = settings.build_bundled_silent_schedule_payload()
    assert "settings" in wrapped
    assert wrapped["updateImmediately"] is False

    inner = wrapped["settings"]
    assert len(inner) == 30  # 2 presets + 7 days * 4 time keys

    # Check presets
    assert inner["PerformanceModePreset"] == 1
    assert inner["SilentHoursScheduledPreset"] == 3

    # Check day schedule keys
    for day in DAYS_OF_WEEK:
        assert f"SilentHours{day}Start" in inner
        assert f"SilentMinutes{day}Start" in inner
        assert f"SilentHours{day}Stop" in inner
        assert f"SilentMinutes{day}Stop" in inner


def test_build_bundled_silent_schedule_payload_parameter_overrides() -> None:
    """Verify parameter overrides and day_updates in bundled payload."""
    settings = ControlBoardSettings()
    wrapped = settings.build_bundled_silent_schedule_payload(
        performance_mode_preset=2,
        silent_hours_preset=4,
        day_updates={
            "SilentHoursMondayStart": 23,
            "SilentMinutesMondayStart": 45,
        },
    )
    inner = wrapped["settings"]
    assert inner["PerformanceModePreset"] == 2
    assert inner["SilentHoursScheduledPreset"] == 4
    assert inner["SilentHoursMondayStart"] == 23
    assert inner["SilentMinutesMondayStart"] == 45


# -----------------------------------------------------------------------------
# 3. Target ID Resolution & Client Routing Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_client_target_id_resolution_and_fully_expanded_alias() -> None:
    """Verify WeheatClient fully expanded alias and target ID cache translation."""
    client = WeheatClient(session=MagicMock())
    client._request = AsyncMock()

    # 1. Alias async_get_installations_fully_expanded
    client._request.return_value = [
        {
            "id": "inst-100",
            "heatPumps": [
                {
                    "id": "hp-100",
                    "controlBoardId": "cb-100",
                    "installationId": "inst-100",
                }
            ],
        }
    ]
    topology = await client.async_get_installations_fully_expanded(token="test_tok")
    assert len(topology) == 1
    assert client._installation_cache.get("hp-100") == "inst-100"
    assert client._control_board_cache.get("hp-100") == "cb-100"

    # 2. Target ID resolution in async_get_control_board_settings
    client._request.return_value = {"performanceModePreset": 0}
    settings = await client.async_get_control_board_settings(
        target_id="hp-100", token="test_tok"
    )
    assert isinstance(settings, ControlBoardSettings)
    # Target hp-100 was resolved to inst-100 for GET /installations/{id}/settings
    call_args = client._request.call_args[0]
    assert "installations/inst-100/settings" in call_args[1]

    # 3. Target ID translation in async_patch_control_board_settings
    client._request.return_value = {"settingsRequestId": "req-1"}
    await client.async_patch_control_board_settings(
        control_board_id="hp-100",
        token="test_tok",
        settings={"PerformanceModePreset": 1},
    )
    # Sent to cb-100
    call_args = client._request.call_args[0]
    assert "control-boards/cb-100/settings" in call_args[1]


# -----------------------------------------------------------------------------
# 4. Auth Guard & Preset Constraint Tests in Select Platform
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_performance_mode_select_auth_guard() -> None:
    """Verify WeheatPerformanceModeSelect rejects write when portal auth fails."""
    hp_info = HeatPumpInfo(
        heatpump_id="hp-1",
        name="HP 1",
        model=0,
        serial_number="SN",
        control_board_id="cb-1",
    )
    session = MagicMock()
    session.check_portal_auth.side_effect = HomeAssistantError("Portal auth required")
    coord = MagicMock()
    coord.data = ControlBoardSettings()

    entity = WeheatPerformanceModeSelect(
        heat_pump_info=hp_info,
        coordinator=coord,
        entity_description=SELECT_ENTITIES[0],
        client=MagicMock(spec=WeheatClient),
        session=session,
    )

    with pytest.raises(HomeAssistantError, match="Portal auth required"):
        await entity.async_select_option("Silent 1")


@pytest.mark.asyncio
async def test_silent_hours_preset_select_constraint_enforcement() -> None:
    """Verify WeheatSilentHoursPresetSelect rejects night preset <= performance preset."""
    hp_info = HeatPumpInfo(
        heatpump_id="hp-1",
        name="HP 1",
        model=0,
        serial_number="SN",
        control_board_id="cb-1",
    )
    session = MagicMock()
    session.async_ensure_token_valid = AsyncMock()
    session.token = {"access_token": "valid_token"}

    # Current daytime performance preset is 2 (Silent 2)
    coord = MagicMock()
    coord.data = ControlBoardSettings(
        performance_mode_preset=2,
        silent_hours_preset=0,
    )

    client = WeheatClient(session=MagicMock())
    client.async_patch_control_board_settings = AsyncMock(return_value="req-ok")
    client.async_poll_settings_request = AsyncMock()
    coord.async_request_refresh = AsyncMock()

    entity = WeheatSilentHoursPresetSelect(
        heat_pump_info=hp_info,
        coordinator=coord,
        entity_description=SELECT_ENTITIES[1],
        client=client,
        session=session,
    )

    # Attempting to set night limit to Silent 1 (1 <= 2) or Silent 2 (2 <= 2) must be rejected
    with pytest.raises(
        HomeAssistantError,
        match="Night limit preset must be strictly greater than daytime performance preset",
    ):
        await entity.async_select_option("Silent 1")

    with pytest.raises(
        HomeAssistantError,
        match="Night limit preset must be strictly greater than daytime performance preset",
    ):
        await entity.async_select_option("Silent 2")

    # Setting to Silent 3 (3 > 2) must succeed and dispatch bundled payload
    await entity.async_select_option("Silent 3")
    client.async_patch_control_board_settings.assert_called_once()
    payload = client.async_patch_control_board_settings.call_args.kwargs["settings"]
    assert payload["settings"]["SilentHoursScheduledPreset"] == 3
    assert payload["settings"]["PerformanceModePreset"] == 2


# -----------------------------------------------------------------------------
# 5. Switch & Time Entity Bundled Writes & Auth Guard Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_switch_entities_auth_guards() -> None:
    """Verify all switch entities check portal auth before mutating settings."""
    hp_info = HeatPumpInfo(
        heatpump_id="hp-1",
        name="HP 1",
        model=0,
        serial_number="SN",
        control_board_id="cb-1",
    )
    session = MagicMock()
    session.check_portal_auth.side_effect = HomeAssistantError(
        "Portal write authorization missing"
    )
    coord = MagicMock()
    coord.data = ControlBoardSettings()
    client = MagicMock(spec=WeheatClient)

    # 1. Silent hours schedule switch
    switch_sched = WeheatSilentHoursScheduleSwitch(
        heat_pump_info=hp_info,
        coordinator=coord,
        entity_description=SWITCH_DESCRIPTIONS[0],
        client=client,
        session=session,
    )
    with pytest.raises(HomeAssistantError, match="Portal write authorization missing"):
        await switch_sched.async_turn_on()
    with pytest.raises(HomeAssistantError, match="Portal write authorization missing"):
        await switch_sched.async_turn_off()

    # 2. Legionella prevention switch
    switch_leg = WeheatLegionellaPreventionSwitch(
        heat_pump_info=hp_info,
        coordinator=coord,
        entity_description=SWITCH_DESCRIPTIONS[1],
        client=client,
        session=session,
    )
    with pytest.raises(HomeAssistantError, match="Portal write authorization missing"):
        await switch_leg.async_turn_on()

    # 3. DHW boost switch
    switch_dhw = WeheatDhwBoostSwitch(
        heat_pump_info=hp_info,
        coordinator=coord,
        entity_description=SWITCH_DESCRIPTIONS[2],
        client=client,
        session=session,
    )
    with pytest.raises(HomeAssistantError, match="Portal write authorization missing"):
        await switch_dhw.async_turn_on()


@pytest.mark.asyncio
async def test_time_entity_bundled_write_and_auth_guard() -> None:
    """Verify WeheatTimeEntity checks auth and sends complete bundled 30-key payload."""
    import datetime

    hp_info = HeatPumpInfo(
        heatpump_id="hp-1",
        name="HP 1",
        model=0,
        serial_number="SN",
        control_board_id="cb-1",
    )
    session = MagicMock()
    session.check_portal_auth.side_effect = HomeAssistantError(
        "Time entity portal auth check failed"
    )
    coord = MagicMock()
    coord.data = ControlBoardSettings()
    client = WeheatClient(session=MagicMock())
    client.async_patch_control_board_settings = AsyncMock(return_value="req-time")
    client.async_poll_settings_request = AsyncMock()
    coord.async_request_refresh = AsyncMock()

    time_entity = WeheatTimeEntity(
        coordinator=coord,
        entity_description=TIME_ENTITIES[0],  # Monday start
        heat_pump_info=hp_info,
        client=client,
        session=session,
    )

    with pytest.raises(
        HomeAssistantError, match="Time entity portal auth check failed"
    ):
        await time_entity.async_set_value(datetime.time(22, 15))

    # Allow auth and verify bundled write
    session.check_portal_auth = MagicMock()
    session.async_ensure_token_valid = AsyncMock()
    session.token = {"access_token": "valid_token"}

    await time_entity.async_set_value(datetime.time(23, 45))
    client.async_patch_control_board_settings.assert_called_once()
    payload = client.async_patch_control_board_settings.call_args.kwargs["settings"]
    assert len(payload["settings"]) == 30
    assert payload["settings"]["SilentHoursMondayStart"] == 23
    assert payload["settings"]["SilentMinutesMondayStart"] == 45


# -----------------------------------------------------------------------------
# 6. Service Handlers Auth Guard & Bundled Execution Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_services_auth_guards_and_constraint() -> None:
    """Verify services enforce check_portal_auth and preset inequality."""
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.const import CONF_ACCESS_TOKEN, CONF_TOKEN

    hass = HomeAssistant()
    mock_hp_info = HeatPumpInfo(
        heatpump_id="hp-1",
        name="HP 1",
        model=0,
        serial_number="SN",
        control_board_id="cb-1",
    )
    mock_session = MagicMock()
    mock_session.async_ensure_token_valid = AsyncMock()
    mock_session.token = {CONF_ACCESS_TOKEN: "valid_tok"}
    mock_client = WeheatClient(session=MagicMock())
    mock_client.async_patch_control_board_settings = AsyncMock(return_value="req-1")
    mock_client.async_poll_settings_request = AsyncMock()
    mock_client.async_trigger_legionella_prevention = AsyncMock(return_value=True)
    mock_coord = MagicMock()
    mock_coord.async_request_refresh = AsyncMock()

    data = WeheatData(
        heat_pump_info=mock_hp_info,
        data_coordinator=mock_coord,
        energy_coordinator=mock_coord,
        client=mock_client,
        session=mock_session,
    )
    entry = ConfigEntry(
        entry_id="entry_1",
        data={CONF_TOKEN: {CONF_ACCESS_TOKEN: "valid_tok"}},
        runtime_data=[data],
    )
    hass.config_entries.async_add(entry)

    mock_session.check_portal_auth.side_effect = HomeAssistantError(
        "Portal auth required for service"
    )

    # 1. set_quiet_mode blocked by auth
    call_quiet = ServiceCall(DOMAIN, SERVICE_SET_QUIET_MODE, {"mode": 1})
    with pytest.raises(HomeAssistantError, match="Portal auth required for service"):
        await async_handle_set_quiet_mode(hass, call_quiet)

    # 2. set_silent_schedule blocked by auth
    call_sched = ServiceCall(
        DOMAIN,
        SERVICE_SET_SILENT_SCHEDULE,
        {"preset": 2, "start_time": "22:00", "stop_time": "07:00"},
    )
    with pytest.raises(HomeAssistantError, match="Portal auth required for service"):
        await async_handle_set_silent_schedule(hass, call_sched)

    # 3. trigger_legionella blocked by auth
    call_leg = ServiceCall(DOMAIN, SERVICE_TRIGGER_LEGIONELLA, {})
    with pytest.raises(HomeAssistantError, match="Portal auth required for service"):
        await async_handle_trigger_legionella(hass, call_leg)

    # Restore session auth
    mock_session.check_portal_auth = MagicMock()

    # Configure current performance mode to 3 (Silent 3)
    settings_coord = MagicMock()
    settings_coord.data = ControlBoardSettings(
        performance_mode_preset=3,
        silent_hours_preset=0,
    )
    data.settings_coordinator = settings_coord

    # Attempting to set night limit <= 3 (e.g. 2) must raise inequality error
    call_invalid_night = ServiceCall(
        DOMAIN,
        SERVICE_SET_SILENT_SCHEDULE,
        {"preset": 2, "start_time": "22:00", "stop_time": "07:00"},
    )
    with pytest.raises(
        HomeAssistantError,
        match="Night limit preset must be strictly greater than daytime performance preset",
    ):
        await async_handle_set_silent_schedule(hass, call_invalid_night)
