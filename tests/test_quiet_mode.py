"""Tests for Weheat quiet mode, select, and switch entities."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.config_entries import ConfigEntry

from custom_components.weheat.const import DOMAIN
from custom_components.weheat.coordinator import WeheatData
from custom_components.weheat.models import (
    ControlBoardSettings,
    HeatPumpInfo,
    HeatPumpLog,
    HeatPumpState,
    PerformanceModePreset,
    SilentHoursScheduledPreset,
)
from custom_components.weheat.select import (
    PERFORMANCE_MODE_OPTIONS,
    SELECT_ENTITIES,
    SILENT_HOURS_OPTIONS,
    WeheatPerformanceModeSelect,
    WeheatSilentHoursPresetSelect,
)
from custom_components.weheat.select import (
    async_setup_entry as async_setup_select_entry,
)
from custom_components.weheat.switch import (
    SWITCH_DESCRIPTIONS,
    WeheatDhwBoostSwitch,
    WeheatLegionellaPreventionSwitch,
    WeheatSilentHoursScheduleSwitch,
)
from custom_components.weheat.switch import (
    async_setup_entry as async_setup_switch_entry,
)
from tests.conftest import (
    TEST_CB_ID_BLACKBIRD,
    TEST_SETTINGS_REQUEST_ID,
    load_fixture,
)


@pytest.fixture
def mock_hp_info() -> HeatPumpInfo:
    data = load_fixture("heat_pumps.json")["data"][0]
    return HeatPumpInfo.from_api_dict(data)


@pytest.fixture
def mock_settings() -> ControlBoardSettings:
    data = load_fixture("installation_settings_get.json")
    return ControlBoardSettings.from_api_dict(data)


@pytest.fixture
def mock_log() -> HeatPumpLog:
    data = load_fixture("latest_logs.json")
    return HeatPumpLog.from_api_dict(data)


# ---------------------------------------------------------------------------
# 1. Select Entities Tests
# ---------------------------------------------------------------------------


def test_select_catalog_and_options() -> None:
    """Verify select entity catalog and options."""
    assert len(SELECT_ENTITIES) == 2
    assert SELECT_ENTITIES[0].key == "performance_mode_preset"
    assert SELECT_ENTITIES[0].options == PERFORMANCE_MODE_OPTIONS
    assert PERFORMANCE_MODE_OPTIONS == [
        "Performance",
        "Silent 1",
        "Silent 2",
        "Silent 3",
        "Silent 4",
    ]

    assert SELECT_ENTITIES[1].key == "silent_hours_preset"
    assert SELECT_ENTITIES[1].options == SILENT_HOURS_OPTIONS
    assert SILENT_HOURS_OPTIONS == [
        "Off",
        "Silent 1",
        "Silent 2",
        "Silent 3",
        "Silent 4",
        "Silent 6",
        "Silent 7",
    ]


def test_performance_mode_select_current_option(
    mock_hp_info: HeatPumpInfo, mock_settings: ControlBoardSettings
) -> None:
    """Verify Performance Mode Select correctly reads option from coordinator settings."""
    coord = MagicMock()
    coord.data = mock_settings
    entity = WeheatPerformanceModeSelect(
        heat_pump_info=mock_hp_info,
        coordinator=coord,
        entity_description=SELECT_ENTITIES[0],
    )

    # Initial fixture has PerformanceModePreset == 0 -> "Performance"
    assert entity.current_option == "Performance"

    mock_settings.performance_mode_preset = 1
    assert entity.current_option == "Silent 1"

    mock_settings.performance_mode_preset = 2
    assert entity.current_option == "Silent 2"

    mock_settings.performance_mode_preset = 3
    assert entity.current_option == "Silent 3"

    mock_settings.performance_mode_preset = 4
    assert entity.current_option == "Silent 4"

    mock_settings.performance_mode_preset = 99
    assert entity.current_option is None


def test_silent_hours_preset_select_current_option(
    mock_hp_info: HeatPumpInfo, mock_settings: ControlBoardSettings
) -> None:
    """Verify Silent Hours Preset Select correctly reads option from coordinator settings."""
    coord = MagicMock()
    coord.data = mock_settings
    entity = WeheatSilentHoursPresetSelect(
        heat_pump_info=mock_hp_info,
        coordinator=coord,
        entity_description=SELECT_ENTITIES[1],
    )

    # Initial fixture has SilentHoursScheduledPreset == 2 -> "Silent 2"
    assert entity.current_option == "Silent 2"

    mock_settings.silent_hours_preset = 0
    assert entity.current_option == "Off"

    mock_settings.silent_hours_preset = 5  # OFF_ALT
    assert entity.current_option == "Off"

    mock_settings.silent_hours_preset = 1
    assert entity.current_option == "Silent 1"

    mock_settings.silent_hours_preset = 3
    assert entity.current_option == "Silent 3"

    mock_settings.silent_hours_preset = 4
    assert entity.current_option == "Silent 4"


@pytest.mark.asyncio
async def test_performance_mode_select_async_select_option(
    mock_hp_info: HeatPumpInfo, mock_settings: ControlBoardSettings
) -> None:
    """Verify async_select_option dispatches patch, polls request, and refreshes coordinator."""
    coord = MagicMock()
    coord.data = mock_settings
    coord.async_request_refresh = AsyncMock()

    client = MagicMock()
    client.async_set_performance_mode = AsyncMock(return_value=TEST_SETTINGS_REQUEST_ID)
    client.async_poll_settings_request = AsyncMock(return_value={"status": 2})

    session = MagicMock()
    session.async_ensure_token_valid = AsyncMock()
    session.token = {"access_token": "mock_token"}

    entity = WeheatPerformanceModeSelect(
        heat_pump_info=mock_hp_info,
        coordinator=coord,
        entity_description=SELECT_ENTITIES[0],
        client=client,
        session=session,
    )

    # Select "Silent 2"
    await entity.async_select_option("Silent 2")

    session.async_ensure_token_valid.assert_awaited_once()
    client.async_set_performance_mode.assert_awaited_once_with(
        control_board_id=TEST_CB_ID_BLACKBIRD,
        token="mock_token",
        preset=PerformanceModePreset.SILENT_2,
    )
    client.async_poll_settings_request.assert_awaited_once_with(
        control_board_id=TEST_CB_ID_BLACKBIRD,
        request_id=TEST_SETTINGS_REQUEST_ID,
        token="mock_token",
    )
    coord.async_request_refresh.assert_awaited_once()

    # Invalid option raises ValueError
    with pytest.raises(ValueError, match="Invalid option 'NonExistent'"):
        await entity.async_select_option("NonExistent")


@pytest.mark.asyncio
async def test_silent_hours_preset_select_async_select_option(
    mock_hp_info: HeatPumpInfo, mock_settings: ControlBoardSettings
) -> None:
    """Verify async_select_option dispatches patch, polls request, and refreshes coordinator."""
    coord = MagicMock()
    coord.data = mock_settings
    coord.async_request_refresh = AsyncMock()

    client = MagicMock()
    client.async_set_silent_hours_preset = AsyncMock(
        return_value=TEST_SETTINGS_REQUEST_ID
    )
    client.async_poll_settings_request = AsyncMock(return_value={"status": 2})

    session = MagicMock()
    session.async_ensure_token_valid = AsyncMock()
    session.token = {"access_token": "mock_token"}

    entity = WeheatSilentHoursPresetSelect(
        heat_pump_info=mock_hp_info,
        coordinator=coord,
        entity_description=SELECT_ENTITIES[1],
        client=client,
        session=session,
    )

    await entity.async_select_option("Off")

    client.async_set_silent_hours_preset.assert_awaited_once_with(
        control_board_id=TEST_CB_ID_BLACKBIRD,
        token="mock_token",
        preset=SilentHoursScheduledPreset.OFF,
    )
    client.async_poll_settings_request.assert_awaited_once_with(
        control_board_id=TEST_CB_ID_BLACKBIRD,
        request_id=TEST_SETTINGS_REQUEST_ID,
        token="mock_token",
    )
    coord.async_request_refresh.assert_awaited_once()


# ---------------------------------------------------------------------------
# 2. Switch Entities Tests
# ---------------------------------------------------------------------------


def test_switch_catalog_descriptions() -> None:
    """Verify switch entity catalog descriptions."""
    assert len(SWITCH_DESCRIPTIONS) == 3
    assert SWITCH_DESCRIPTIONS[0].key == "silent_hours_schedule_enabled"
    assert SWITCH_DESCRIPTIONS[1].key == "legionella_prevention"
    assert SWITCH_DESCRIPTIONS[2].key == "dhw_boost"


def test_silent_hours_schedule_switch_is_on(
    mock_hp_info: HeatPumpInfo, mock_settings: ControlBoardSettings
) -> None:
    """Verify schedule switch is_on tracks preset > 0 and != 5."""
    coord = MagicMock()
    coord.data = mock_settings
    entity = WeheatSilentHoursScheduleSwitch(
        heat_pump_info=mock_hp_info,
        coordinator=coord,
        entity_description=SWITCH_DESCRIPTIONS[0],
    )

    # Initial fixture has preset == 2 -> True
    assert entity.is_on is True

    mock_settings.silent_hours_preset = 0
    assert entity.is_on is False

    mock_settings.silent_hours_preset = 5  # OFF_ALT
    assert entity.is_on is False

    mock_settings.silent_hours_preset = 1
    assert entity.is_on is True


@pytest.mark.asyncio
async def test_silent_hours_schedule_switch_turn_on_off(
    mock_hp_info: HeatPumpInfo, mock_settings: ControlBoardSettings
) -> None:
    """Verify turn_off saves preset and sets 0; turn_on restores preset."""
    coord = MagicMock()
    coord.data = mock_settings
    coord.async_request_refresh = AsyncMock()

    client = MagicMock()
    client.async_set_silent_hours_preset = AsyncMock(
        return_value=TEST_SETTINGS_REQUEST_ID
    )
    client.async_poll_settings_request = AsyncMock(return_value={"status": 2})

    session = MagicMock()
    session.async_ensure_token_valid = AsyncMock()
    session.token = {"access_token": "mock_token"}

    entity = WeheatSilentHoursScheduleSwitch(
        heat_pump_info=mock_hp_info,
        coordinator=coord,
        entity_description=SWITCH_DESCRIPTIONS[0],
        client=client,
        session=session,
    )

    # Initially preset is 2
    mock_settings.silent_hours_preset = 3

    # Turn OFF -> sets preset 0 and remembers 3
    await entity.async_turn_off()
    client.async_set_silent_hours_preset.assert_awaited_with(
        control_board_id=TEST_CB_ID_BLACKBIRD,
        token="mock_token",
        preset=SilentHoursScheduledPreset.OFF,
    )

    # Now turn ON -> restores preset 3
    await entity.async_turn_on()
    client.async_set_silent_hours_preset.assert_awaited_with(
        control_board_id=TEST_CB_ID_BLACKBIRD,
        token="mock_token",
        preset=3,
    )


def test_legionella_prevention_switch_is_on(
    mock_hp_info: HeatPumpInfo, mock_log: HeatPumpLog
) -> None:
    """Verify Legionella prevention switch is_on tracks state 160."""
    coord = MagicMock()
    coord.data = mock_log
    entity = WeheatLegionellaPreventionSwitch(
        heat_pump_info=mock_hp_info,
        coordinator=coord,
        entity_description=SWITCH_DESCRIPTIONS[1],
    )

    # In fixture, state is HEATING (70) -> False
    assert entity.is_on is False

    # When state becomes LEGIONELLA_PREVENTION (160) -> True
    mock_log.state = HeatPumpState.LEGIONELLA_PREVENTION
    mock_log.raw_state = 160
    assert entity.is_on is True


@pytest.mark.asyncio
async def test_legionella_prevention_switch_turn_on_off(
    mock_hp_info: HeatPumpInfo, mock_log: HeatPumpLog
) -> None:
    """Verify Legionella switch turn_on triggers cycle and turn_off skips cycle."""
    coord = MagicMock()
    coord.data = mock_log
    coord.async_request_refresh = AsyncMock()

    client = MagicMock()
    client.async_trigger_legionella_prevention = AsyncMock(return_value=True)
    client.async_skip_legionella_prevention = AsyncMock(return_value=True)

    session = MagicMock()
    session.async_ensure_token_valid = AsyncMock()
    session.token = {"access_token": "mock_token"}

    entity = WeheatLegionellaPreventionSwitch(
        heat_pump_info=mock_hp_info,
        coordinator=coord,
        entity_description=SWITCH_DESCRIPTIONS[1],
        client=client,
        session=session,
    )

    # Turn ON -> triggers RequestLegionellaPrevention: 1
    await entity.async_turn_on()
    client.async_trigger_legionella_prevention.assert_awaited_once_with(
        control_board_id=TEST_CB_ID_BLACKBIRD,
        token="mock_token",
    )
    coord.async_request_refresh.assert_awaited_once()

    # Turn OFF -> calls skip legionella
    coord.async_request_refresh.reset_mock()
    await entity.async_turn_off()
    client.async_skip_legionella_prevention.assert_awaited_once_with(
        control_board_id=TEST_CB_ID_BLACKBIRD,
        token="mock_token",
    )
    coord.async_request_refresh.assert_awaited_once()


# ---------------------------------------------------------------------------
# 3. Entity Identity & Device Registry Tests
# ---------------------------------------------------------------------------


def test_entity_identities(
    mock_hp_info: HeatPumpInfo, mock_settings: ControlBoardSettings
) -> None:
    """Verify unique ID format: {heatpump_id}_{entity_key} and device info mapping."""
    coord = MagicMock()
    coord.data = mock_settings

    sel_perf = WeheatPerformanceModeSelect(
        heat_pump_info=mock_hp_info,
        coordinator=coord,
        entity_description=SELECT_ENTITIES[0],
    )
    assert sel_perf.unique_id == f"{mock_hp_info.heatpump_id}_performance_mode_preset"
    assert sel_perf.device_info["identifiers"] == {(DOMAIN, mock_hp_info.heatpump_id)}

    sel_silent = WeheatSilentHoursPresetSelect(
        heat_pump_info=mock_hp_info,
        coordinator=coord,
        entity_description=SELECT_ENTITIES[1],
    )
    assert sel_silent.unique_id == f"{mock_hp_info.heatpump_id}_silent_hours_preset"
    assert sel_silent.device_info["identifiers"] == {(DOMAIN, mock_hp_info.heatpump_id)}

    sw_sched = WeheatSilentHoursScheduleSwitch(
        heat_pump_info=mock_hp_info,
        coordinator=coord,
        entity_description=SWITCH_DESCRIPTIONS[0],
    )
    assert (
        sw_sched.unique_id
        == f"{mock_hp_info.heatpump_id}_silent_hours_schedule_enabled"
    )
    assert sw_sched.device_info["identifiers"] == {(DOMAIN, mock_hp_info.heatpump_id)}

    sw_leg = WeheatLegionellaPreventionSwitch(
        heat_pump_info=mock_hp_info,
        coordinator=coord,
        entity_description=SWITCH_DESCRIPTIONS[1],
    )
    assert sw_leg.unique_id == f"{mock_hp_info.heatpump_id}_legionella_prevention"
    assert sw_leg.device_info["identifiers"] == {(DOMAIN, mock_hp_info.heatpump_id)}

    sw_boost = WeheatDhwBoostSwitch(
        heat_pump_info=mock_hp_info,
        coordinator=coord,
        entity_description=SWITCH_DESCRIPTIONS[2],
    )
    assert sw_boost.unique_id == f"{mock_hp_info.heatpump_id}_dhw_boost"
    assert sw_boost.device_info["identifiers"] == {(DOMAIN, mock_hp_info.heatpump_id)}


@pytest.mark.asyncio
async def test_dhw_boost_switch_turn_on(
    mock_hp_info: HeatPumpInfo, mock_log: HeatPumpLog
) -> None:
    """Verify DHW boost switch turn_on triggers client.async_trigger_dhw_boost."""
    coord = MagicMock()
    coord.data = mock_log
    coord.async_request_refresh = AsyncMock()

    client = MagicMock()
    client.async_trigger_dhw_boost = AsyncMock(return_value={"success": True})

    session = MagicMock()
    session.async_ensure_token_valid = AsyncMock()
    session.token = {"access_token": "mock_token"}

    entity = WeheatDhwBoostSwitch(
        heat_pump_info=mock_hp_info,
        coordinator=coord,
        entity_description=SWITCH_DESCRIPTIONS[2],
        client=client,
        session=session,
    )

    await entity.async_turn_on()
    client.async_trigger_dhw_boost.assert_awaited_once_with(
        heat_pump_uuid=mock_hp_info.heatpump_id,
        token="mock_token",
        duration_minutes=60,
        target_temperature=60.0,
    )
    coord.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_async_setup_entry_select_and_switch(
    mock_hp_info: HeatPumpInfo, mock_settings: ControlBoardSettings
) -> None:
    """Verify async_setup_entry adds select and switch entities."""
    hass = MagicMock()
    coord = MagicMock()
    coord.data = mock_settings

    data_item = WeheatData(
        heat_pump_info=mock_hp_info,
        data_coordinator=coord,
        energy_coordinator=MagicMock(),
        settings_coordinator=coord,
        client=MagicMock(),
        session=MagicMock(),
    )
    entry = ConfigEntry(runtime_data=[data_item])

    added_selects: list[Any] = []
    await async_setup_select_entry(hass, entry, added_selects.extend)
    assert len(added_selects) == 2

    added_switches: list[Any] = []
    await async_setup_switch_entry(hass, entry, added_switches.extend)
    # mock_hp_info has has_dhw=True, so 3 switches: schedule, legionella, dhw_boost
    assert len(added_switches) == 3
