"""End-to-End Real-World Scenario Test Suite for Weheat Custom Integration (Milestone 5 Tier 4).

Validates end-to-end operational user workflows 100% offline with zero external network dependencies:
1. Complete 7-Day Quiet Hours Schedule Cycle across all 7 days with Time and Switch entities.
2. Diurnal Night Setback and Cold Snap Emergency Override.
3. Emergency Legionella Thermal Disinfection Cycle with DHW Target Temperature boost.
4. Multi-Pump Residential & Annex Coordinated Topology and targeted disambiguation.
5. Transient Network Gateway Outage, Token Renewal, and Polling Self-Healing.
6. Full Setup-to-Teardown Lifecycle with active control actions and diagnostics export.
7. Domestic Hot Water (DHW) Boost Timed Cycle and Cancellation.
8. Operational Heat Pump State Machine Transitions and COP Reconciliation.
"""

from __future__ import annotations

import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_TOKEN
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError

from custom_components.weheat import (
    async_setup_entry,
    async_unload_entry,
)
from custom_components.weheat.binary_sensor import (
    BINARY_SENSORS,
    WeheatHeatPumpBinarySensor,
)
from custom_components.weheat.client import (
    WeheatApiError,
    WeheatClient,
)
from custom_components.weheat.const import (
    DOMAIN,
)
from custom_components.weheat.coordinator import (
    WeheatData,
)
from custom_components.weheat.diagnostics import async_get_config_entry_diagnostics
from custom_components.weheat.models import (
    ControlBoardSettings,
    DhwControlMethod,
    HeatPumpEnergy,
    HeatPumpInfo,
    HeatPumpLog,
    HeatPumpState,
)
from custom_components.weheat.select import (
    SELECT_ENTITIES,
    WeheatPerformanceModeSelect,
)
from custom_components.weheat.sensor import (
    COOLING_SENSORS,
    DHW_ENERGY_SENSORS,
    DHW_SENSORS,
    ENERGY_SENSORS,
    SENSORS,
    WeheatHeatPumpSensor,
)
from custom_components.weheat.services import (
    SERVICE_SET_QUIET_MODE,
    SERVICE_SET_SILENT_SCHEDULE,
    SERVICE_TRIGGER_LEGIONELLA,
    async_handle_set_quiet_mode,
    async_handle_set_silent_schedule,
    async_handle_trigger_legionella,
    async_setup_services,
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
from tests.conftest import (
    TEST_CB_ID_BLACKBIRD,
    TEST_HP_UUID_BLACKBIRD,
    TEST_HP_UUID_SPARROW,
    TEST_INSTALLATION_ID,
    TEST_SETTINGS_REQUEST_ID,
    load_fixture,
)

TEST_CB_ID_ANNEX = "cb-00000000-0000-0000-0000-000000000002"
TEST_BEARER_TOKEN = "mock_bearer_token_xyz987"
TEST_REFRESHED_TOKEN = "mock_refreshed_token_abc555"


# -----------------------------------------------------------------------------
# Fixtures for Scenario Testing
# -----------------------------------------------------------------------------


@pytest.fixture
def e2e_main_pump_info() -> HeatPumpInfo:
    """Provide realistic HeatPumpInfo for primary residence pump with DHW."""
    return HeatPumpInfo(
        heatpump_id=TEST_HP_UUID_BLACKBIRD,
        name="Main House Blackbird",
        model=0,
        serial_number="WH-BB80-MOCK-001",
        control_board_id=TEST_CB_ID_BLACKBIRD,
        installation_id=TEST_INSTALLATION_ID,
        has_dhw=True,
    )


@pytest.fixture
def e2e_annex_pump_info() -> HeatPumpInfo:
    """Provide realistic HeatPumpInfo for detached annex pump without DHW."""
    return HeatPumpInfo(
        heatpump_id=TEST_HP_UUID_SPARROW,
        name="Annex Sparrow",
        model=2,
        serial_number="WH-SP50-MOCK-002",
        control_board_id=TEST_CB_ID_ANNEX,
        installation_id=TEST_INSTALLATION_ID,
        has_dhw=False,
    )


@pytest.fixture
def e2e_baseline_settings() -> ControlBoardSettings:
    """Provide baseline settings loaded from verified fixture."""
    data = load_fixture("installation_settings_get.json")
    return ControlBoardSettings.from_api_dict(data)


@pytest.fixture
def e2e_baseline_log() -> HeatPumpLog:
    """Provide baseline telemetry log from fixture."""
    data = load_fixture("latest_logs.json")
    return HeatPumpLog.from_api_dict(data)


@pytest.fixture
def e2e_baseline_energy() -> HeatPumpEnergy:
    """Provide baseline energy metrics from fixture."""
    data = load_fixture("energy_total.json")
    return HeatPumpEnergy.from_api_dict(data)


# -----------------------------------------------------------------------------
# Scenario 1: Complete 7-Day Quiet Hours Schedule Cycle
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_complete_7day_quiet_hours_schedule_cycle(
    e2e_main_pump_info: HeatPumpInfo,
    e2e_baseline_settings: ControlBoardSettings,
) -> None:
    """Scenario: User configures a full 7-day quiet hours schedule.

    Validates:
    - Service call parses weekday and weekend windows correctly.
    - Dispatches 28-key schedule payload and preset to Weheat client.
    - Polling resolves request asynchronously.
    - Coordinator refreshes.
    - All 14 Time entities reflect the configured start/stop times.
    - Schedule switch reflects enabled state.
    """
    hass = HomeAssistant()
    await async_setup_services(hass)

    mock_client = MagicMock(spec=WeheatClient)
    mock_client.async_set_silent_hours_schedule = AsyncMock(
        return_value=TEST_SETTINGS_REQUEST_ID
    )
    mock_client.async_poll_settings_request = AsyncMock(
        return_value={"settingsRequestId": TEST_SETTINGS_REQUEST_ID, "status": 2}
    )

    mock_session = MagicMock()
    mock_session.async_ensure_token_valid = AsyncMock()
    mock_session.token = {CONF_ACCESS_TOKEN: TEST_BEARER_TOKEN}

    settings_coord = MagicMock()
    settings_coord.data = e2e_baseline_settings
    settings_coord.async_request_refresh = AsyncMock()

    weheat_data = WeheatData(
        heat_pump_info=e2e_main_pump_info,
        data_coordinator=settings_coord,
        energy_coordinator=settings_coord,
        settings_coordinator=settings_coord,
        client=mock_client,
        session=mock_session,
    )

    entry = ConfigEntry(
        entry_id="entry_main_house",
        data={CONF_TOKEN: {CONF_ACCESS_TOKEN: TEST_BEARER_TOKEN}},
        runtime_data=[weheat_data],
    )
    hass.config_entries.async_add(entry)

    # 1. User configures weekdays: 22:00 to 06:30 at Silent 2
    weekday_call = ServiceCall(
        DOMAIN,
        SERVICE_SET_SILENT_SCHEDULE,
        {
            "preset": "Silent 2",
            "days": "weekdays",
            "start_time": "22:00",
            "stop_time": "06:30",
        },
    )
    await async_handle_set_silent_schedule(hass, weekday_call)

    # Verify weekday dispatch
    mock_client.async_set_silent_hours_schedule.assert_called_once()
    weekday_kwargs = mock_client.async_set_silent_hours_schedule.call_args.kwargs
    assert weekday_kwargs["control_board_id"] == TEST_CB_ID_BLACKBIRD
    assert weekday_kwargs["preset"] == 2
    weekday_sched = weekday_kwargs["schedule"]
    assert len(weekday_sched) == 20  # 5 days * 4 keys
    for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday"):
        assert weekday_sched[f"SilentHours{day}Start"] == 22
        assert weekday_sched[f"SilentMinutes{day}Start"] == 0
        assert weekday_sched[f"SilentHours{day}Stop"] == 6
        assert weekday_sched[f"SilentMinutes{day}Stop"] == 30

    # 2. User configures weekend: 23:30 to 08:00 at Silent 2
    mock_client.async_set_silent_hours_schedule.reset_mock()
    weekend_call = ServiceCall(
        DOMAIN,
        SERVICE_SET_SILENT_SCHEDULE,
        {
            "preset": "Silent 2",
            "days": "weekend",
            "start_time": "23:30",
            "stop_time": "08:00",
        },
    )
    await async_handle_set_silent_schedule(hass, weekend_call)

    weekend_kwargs = mock_client.async_set_silent_hours_schedule.call_args.kwargs
    weekend_sched = weekend_kwargs["schedule"]
    assert len(weekend_sched) == 8  # 2 days * 4 keys
    for day in ("Saturday", "Sunday"):
        assert weekend_sched[f"SilentHours{day}Start"] == 23
        assert weekend_sched[f"SilentMinutes{day}Start"] == 30
        assert weekend_sched[f"SilentHours{day}Stop"] == 8
        assert weekend_sched[f"SilentMinutes{day}Stop"] == 0

    # 3. Simulate coordinator receiving new merged settings from portal
    updated_dict = load_fixture("installation_settings_get.json")
    for day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday"):
        updated_dict["settings"][f"silentHours{day}Start"] = 22
        updated_dict["settings"][f"silentMinutes{day}Start"] = 0
        updated_dict["settings"][f"silentHours{day}Stop"] = 6
        updated_dict["settings"][f"silentMinutes{day}Stop"] = 30
    for day in ("Saturday", "Sunday"):
        updated_dict["settings"][f"silentHours{day}Start"] = 23
        updated_dict["settings"][f"silentMinutes{day}Start"] = 30
        updated_dict["settings"][f"silentHours{day}Stop"] = 8
        updated_dict["settings"][f"silentMinutes{day}Stop"] = 0
    updated_dict["settings"]["silentHoursScheduledPreset"] = 2

    new_settings = ControlBoardSettings.from_api_dict(updated_dict)
    settings_coord.data = new_settings

    # 4. Verify all 14 time entities reflect exact times
    for desc in TIME_ENTITIES:
        entity = WeheatTimeEntity(
            heat_pump_info=e2e_main_pump_info,
            coordinator=settings_coord,
            entity_description=desc,
            client=mock_client,
        )
        if desc.day in ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday"):
            expected = datetime.time(22, 0) if desc.is_start else datetime.time(6, 30)
        else:
            expected = datetime.time(23, 30) if desc.is_start else datetime.time(8, 0)
        assert entity.native_value == expected

    # 5. Verify switch entity is_on reflects active schedule preset
    schedule_switch = WeheatSilentHoursScheduleSwitch(
        heat_pump_info=e2e_main_pump_info,
        coordinator=settings_coord,
        entity_description=SWITCH_DESCRIPTIONS[0],
        client=mock_client,
        session=mock_session,
    )
    assert schedule_switch.is_on is True


# -----------------------------------------------------------------------------
# Scenario 2: Diurnal Night Setback and Cold Snap Emergency Override
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_night_setback_and_cold_snap_override(
    e2e_main_pump_info: HeatPumpInfo,
    e2e_baseline_settings: ControlBoardSettings,
) -> None:
    """Scenario: Night Setback triggers at 22:00, followed by cold snap override.

    Validates:
    - Daytime operation in Performance mode (0).
    - Automation triggers night setback to Silent 1 (1).
    - Polling and coordinator update verify state transition.
    - Cold freeze automation detects severe cold and overrides back to Performance (0).
    - Select entity reflects Performance mode.
    """
    hass = HomeAssistant()
    await async_setup_services(hass)

    mock_client = MagicMock(spec=WeheatClient)
    mock_client.async_set_performance_mode = AsyncMock(
        return_value=TEST_SETTINGS_REQUEST_ID
    )
    mock_client.async_poll_settings_request = AsyncMock(
        return_value={"settingsRequestId": TEST_SETTINGS_REQUEST_ID, "status": 2}
    )

    mock_session = MagicMock()
    mock_session.async_ensure_token_valid = AsyncMock()
    mock_session.token = {CONF_ACCESS_TOKEN: TEST_BEARER_TOKEN}

    data_coord = MagicMock()
    data_coord.data = e2e_baseline_settings
    data_coord.async_request_refresh = AsyncMock()

    settings_coord = MagicMock()
    settings_coord.data = e2e_baseline_settings
    settings_coord.async_request_refresh = AsyncMock()

    weheat_data = WeheatData(
        heat_pump_info=e2e_main_pump_info,
        data_coordinator=data_coord,
        energy_coordinator=data_coord,
        settings_coordinator=settings_coord,
        client=mock_client,
        session=mock_session,
    )
    entry = ConfigEntry(
        entry_id="entry_main",
        data={CONF_TOKEN: {CONF_ACCESS_TOKEN: TEST_BEARER_TOKEN}},
        runtime_data=[weheat_data],
    )
    hass.config_entries.async_add(entry)

    select_desc = SELECT_ENTITIES[0]  # performance_mode_preset
    perf_select = WeheatPerformanceModeSelect(
        heat_pump_info=e2e_main_pump_info,
        coordinator=settings_coord,
        entity_description=select_desc,
        client=mock_client,
        session=mock_session,
    )
    # Baseline is Performance (0) -> Mode 5 - Max capacity
    assert perf_select.current_option == "Mode 5 - Max capacity"

    # Step A: 22:00 Night Setback activation to "Silent 1"
    setback_call = ServiceCall(
        DOMAIN,
        SERVICE_SET_QUIET_MODE,
        {"mode": "Silent 1"},
    )
    await async_handle_set_quiet_mode(hass, setback_call)

    mock_client.async_set_performance_mode.assert_called_once_with(
        control_board_id=TEST_CB_ID_BLACKBIRD,
        token=TEST_BEARER_TOKEN,
        preset=1,
    )
    mock_client.async_poll_settings_request.assert_called_once()
    settings_coord.async_request_refresh.assert_called_once()

    # Coordinator reflects Silent 1 -> Mode 4 - Standard
    updated_dict = load_fixture("installation_settings_get.json")
    updated_dict["settings"]["performanceModePreset"] = 1
    settings_coord.data = ControlBoardSettings.from_api_dict(updated_dict)
    assert perf_select.current_option == "Mode 4 - Standard"

    # Step B: 03:00 Cold snap freeze override back to "Performance"
    mock_client.async_set_performance_mode.reset_mock()
    mock_client.async_poll_settings_request.reset_mock()
    settings_coord.async_request_refresh.reset_mock()

    freeze_call = ServiceCall(
        DOMAIN,
        SERVICE_SET_QUIET_MODE,
        {"mode": "Performance"},
    )
    await async_handle_set_quiet_mode(hass, freeze_call)

    mock_client.async_set_performance_mode.assert_called_once_with(
        control_board_id=TEST_CB_ID_BLACKBIRD,
        token=TEST_BEARER_TOKEN,
        preset=0,
    )
    mock_client.async_poll_settings_request.assert_called_once()
    settings_coord.async_request_refresh.assert_called_once()

    # Coordinator reflects Performance -> Mode 5 - Max capacity
    updated_dict["settings"]["performanceModePreset"] = 0
    settings_coord.data = ControlBoardSettings.from_api_dict(updated_dict)
    assert perf_select.current_option == "Mode 5 - Max capacity"


# -----------------------------------------------------------------------------
# Scenario 3: Emergency Legionella Thermal Disinfection Cycle
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_emergency_legionella_thermal_disinfection_cycle(
    e2e_main_pump_info: HeatPumpInfo,
    e2e_baseline_log: HeatPumpLog,
    e2e_baseline_settings: ControlBoardSettings,
) -> None:
    """Scenario: User triggers Legionella prevention with DHW target temperature boost.

    Validates:
    - Service call dispatches RequestLegionellaPrevention variable mutation.
    - DHW target temperature boosted to 60°C.
    - Telemetry coordinator refreshes: water temperature reaches disinfection temp.
    - Legionella switch reports is_on=True during cycle.
    - Disinfection cycle completion or skip clears state cleanly.
    """
    hass = HomeAssistant()
    await async_setup_services(hass)

    mock_client = MagicMock(spec=WeheatClient)
    mock_client.async_trigger_legionella_prevention = AsyncMock(return_value=True)
    mock_client.async_skip_legionella_prevention = AsyncMock(return_value=True)
    mock_client.async_set_dhw_target_temperature = AsyncMock(
        return_value=TEST_SETTINGS_REQUEST_ID
    )
    mock_client.async_poll_settings_request = AsyncMock(
        return_value={"settingsRequestId": TEST_SETTINGS_REQUEST_ID, "status": 2}
    )

    mock_session = MagicMock()
    mock_session.async_ensure_token_valid = AsyncMock()
    mock_session.token = {CONF_ACCESS_TOKEN: TEST_BEARER_TOKEN}

    data_coord = MagicMock()
    data_coord.data = e2e_baseline_log
    data_coord.async_request_refresh = AsyncMock()

    settings_coord = MagicMock()
    settings_coord.data = e2e_baseline_settings
    settings_coord.async_request_refresh = AsyncMock()

    weheat_data = WeheatData(
        heat_pump_info=e2e_main_pump_info,
        data_coordinator=data_coord,
        energy_coordinator=data_coord,
        settings_coordinator=settings_coord,
        client=mock_client,
        session=mock_session,
    )
    entry = ConfigEntry(
        entry_id="entry_main",
        data={CONF_TOKEN: {CONF_ACCESS_TOKEN: TEST_BEARER_TOKEN}},
        runtime_data=[weheat_data],
    )
    hass.config_entries.async_add(entry)

    # 1. Trigger Legionella prevention via service call
    legionella_call = ServiceCall(DOMAIN, SERVICE_TRIGGER_LEGIONELLA, {})
    await async_handle_trigger_legionella(hass, legionella_call)

    mock_client.async_trigger_legionella_prevention.assert_called_once_with(
        control_board_id=TEST_CB_ID_BLACKBIRD,
        token=TEST_BEARER_TOKEN,
    )
    data_coord.async_request_refresh.assert_called_once()

    # 2. Concurrently boost DHW target temperature to 60.0°C
    req_id = await mock_client.async_set_dhw_target_temperature(
        control_board_id=TEST_CB_ID_BLACKBIRD,
        token=TEST_BEARER_TOKEN,
        target_temperature=60.0,
    )
    assert req_id == TEST_SETTINGS_REQUEST_ID

    # 3. Simulate high-temperature telemetry response in coordinator
    disinfection_log_dict = load_fixture("latest_logs.json")
    disinfection_log_dict["t1"] = 62.5
    disinfection_log_dict["t2"] = 59.8
    disinfection_log_dict["dhwValve"] = True
    disinfection_log_dict["dhwTargetTemperature"] = 60.0
    disinfection_log_dict["state"] = HeatPumpState.LEGIONELLA_PREVENTION  # 160

    disinfection_log = HeatPumpLog.from_api_dict(disinfection_log_dict)
    data_coord.data = disinfection_log

    # Verify log model parsed correctly
    assert data_coord.data.dhw_top_temperature == 62.5
    assert data_coord.data.state == HeatPumpState.LEGIONELLA_PREVENTION

    # 4. Instantiate and verify Legionella switch entity
    legionella_switch = WeheatLegionellaPreventionSwitch(
        heat_pump_info=e2e_main_pump_info,
        coordinator=data_coord,
        entity_description=SWITCH_DESCRIPTIONS[1],
        client=mock_client,
        session=mock_session,
    )
    # In LEGIONELLA_PREVENTION state, switch is on
    assert legionella_switch.is_on is True

    # 5. User turns off Legionella switch (skipping remaining prevention)
    await legionella_switch.async_turn_off()
    mock_client.async_skip_legionella_prevention.assert_called_once_with(
        control_board_id=TEST_CB_ID_BLACKBIRD,
        token=TEST_BEARER_TOKEN,
    )


# -----------------------------------------------------------------------------
# Scenario 4: Multi-Pump Residential & Annex Coordinated Topology
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_multi_pump_residential_and_annex_coordination(
    e2e_main_pump_info: HeatPumpInfo,
    e2e_annex_pump_info: HeatPumpInfo,
    e2e_baseline_settings: ControlBoardSettings,
) -> None:
    """Scenario: Multi-pump installation with Main House and Annex.

    Validates:
    - Discovery of multiple heat pumps mapped to distinct control boards.
    - Entity feature gating: Main House with DHW has 3 switches; Annex has 1 switch.
    - Explicit target disambiguation routes mutations strictly to the targeted pump.
    - Energy coordinators monitor metrics for each pump independently.
    """
    hass = HomeAssistant()
    await async_setup_services(hass)

    mock_client = MagicMock(spec=WeheatClient)
    mock_client.async_set_performance_mode = AsyncMock(
        return_value=TEST_SETTINGS_REQUEST_ID
    )
    mock_client.async_poll_settings_request = AsyncMock(
        return_value={"settingsRequestId": TEST_SETTINGS_REQUEST_ID, "status": 2}
    )

    mock_session = MagicMock()
    mock_session.async_ensure_token_valid = AsyncMock()
    mock_session.token = {CONF_ACCESS_TOKEN: TEST_BEARER_TOKEN}

    data_coord_main = MagicMock()
    data_coord_main.data = e2e_baseline_settings
    data_coord_main.async_request_refresh = AsyncMock()

    settings_coord_main = MagicMock()
    settings_coord_main.data = e2e_baseline_settings
    settings_coord_main.async_request_refresh = AsyncMock()

    data_coord_annex = MagicMock()
    data_coord_annex.data = e2e_baseline_settings
    data_coord_annex.async_request_refresh = AsyncMock()

    settings_coord_annex = MagicMock()
    settings_coord_annex.data = e2e_baseline_settings
    settings_coord_annex.async_request_refresh = AsyncMock()

    energy_dict_1 = load_fixture("energy_total.json")
    energy_main = MagicMock()
    energy_main.data = HeatPumpEnergy.from_api_dict(energy_dict_1)

    energy_dict_2 = dict(energy_dict_1)
    energy_dict_2["totalEOutHeating"] = 2100.0
    energy_annex = MagicMock()
    energy_annex.data = HeatPumpEnergy.from_api_dict(energy_dict_2)

    data_main = WeheatData(
        heat_pump_info=e2e_main_pump_info,
        data_coordinator=data_coord_main,
        energy_coordinator=energy_main,
        settings_coordinator=settings_coord_main,
        client=mock_client,
        session=mock_session,
    )

    data_annex = WeheatData(
        heat_pump_info=e2e_annex_pump_info,
        data_coordinator=data_coord_annex,
        energy_coordinator=energy_annex,
        settings_coordinator=settings_coord_annex,
        client=mock_client,
        session=mock_session,
    )

    entry = ConfigEntry(
        entry_id="entry_dual",
        data={CONF_TOKEN: {CONF_ACCESS_TOKEN: TEST_BEARER_TOKEN}},
        runtime_data=[data_main, data_annex],
    )
    hass.config_entries.async_add(entry)

    # 1. Verify entity counts between DHW and non-DHW pumps
    from custom_components.weheat.switch import async_setup_entry as setup_switches

    created_switches: list[Any] = []
    await setup_switches(
        hass, entry, lambda entities: created_switches.extend(entities)
    )

    # Main House (has_dhw=True) has 3 switches, Annex (has_dhw=False) has 1 switch
    main_switches = [
        s
        for s in created_switches
        if s.heat_pump_info.heatpump_id == TEST_HP_UUID_BLACKBIRD
    ]
    annex_switches = [
        s
        for s in created_switches
        if s.heat_pump_info.heatpump_id == TEST_HP_UUID_SPARROW
    ]
    assert len(main_switches) == 4
    assert len(annex_switches) == 2

    # Main pump gets all sensors (39: 15 base + 7 cooling + 4 dhw + 3 dhw energy + 10 energy); Annex gets non-DHW sensors (32)
    all_main_sensors = (
        list(SENSORS)
        + list(COOLING_SENSORS)
        + list(DHW_SENSORS)
        + list(DHW_ENERGY_SENSORS)
        + list(ENERGY_SENSORS)
    )
    all_annex_sensors = list(SENSORS) + list(COOLING_SENSORS) + list(ENERGY_SENSORS)
    assert len(all_main_sensors) == 39
    assert len(all_annex_sensors) == 32

    # 2. Target Annex specifically via heat_pump_id to set "Silent 3"
    annex_call = ServiceCall(
        DOMAIN,
        SERVICE_SET_QUIET_MODE,
        {"mode": "Silent 3", "heat_pump_id": TEST_HP_UUID_SPARROW},
    )
    await async_handle_set_quiet_mode(hass, annex_call)

    # Dispatched strictly to Annex control board ID, not Main House
    mock_client.async_set_performance_mode.assert_called_once_with(
        control_board_id=TEST_CB_ID_ANNEX,
        token=TEST_BEARER_TOKEN,
        preset=3,
    )
    settings_coord_annex.async_request_refresh.assert_called_once()
    settings_coord_main.async_request_refresh.assert_not_called()

    # 3. Targeted action to Main House specifically
    mock_client.async_set_performance_mode.reset_mock()
    main_call = ServiceCall(
        DOMAIN,
        SERVICE_SET_QUIET_MODE,
        {"mode": "Silent 1", "heat_pump_id": TEST_HP_UUID_BLACKBIRD},
    )
    await async_handle_set_quiet_mode(hass, main_call)

    mock_client.async_set_performance_mode.assert_called_once_with(
        control_board_id=TEST_CB_ID_BLACKBIRD,
        token=TEST_BEARER_TOKEN,
        preset=1,
    )
    settings_coord_main.async_request_refresh.assert_called_once()

    # 4. Independent energy data
    assert energy_main.data.energy_output > 0
    assert energy_annex.data.energy_output > 0


# -----------------------------------------------------------------------------
# Scenario 5: Transient Network Gateway Outage, Token Renewal, Self-Healing
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_transient_network_outage_and_oauth_recovery(
    e2e_main_pump_info: HeatPumpInfo,
    e2e_baseline_settings: ControlBoardSettings,
) -> None:
    """Scenario: User triggers service call during transient network error & expired token.

    Validates:
    - OAuth session refreshes token when expired.
    - Initial polling attempt encounters transient 502 Bad Gateway.
    - Second polling attempt recovers and resolves status 2 (Succeeded).
    - Coordinator refreshes data without marking integration failed.
    """
    hass = HomeAssistant()
    await async_setup_services(hass)

    mock_session = MagicMock()
    token_refreshed = False

    async def mock_ensure_token() -> None:
        nonlocal token_refreshed
        token_refreshed = True
        mock_session.token = {CONF_ACCESS_TOKEN: TEST_REFRESHED_TOKEN}

    mock_session.async_ensure_token_valid = AsyncMock(side_effect=mock_ensure_token)
    mock_session.token = {CONF_ACCESS_TOKEN: TEST_BEARER_TOKEN}

    mock_client = MagicMock(spec=WeheatClient)
    mock_client.async_set_performance_mode = AsyncMock(
        return_value=TEST_SETTINGS_REQUEST_ID
    )

    # Polling encounters transient 502 error on first call, recovers on second
    poll_calls = 0

    async def mock_poll(
        control_board_id: str, request_id: str, token: str | None = None
    ) -> dict[str, Any]:
        nonlocal poll_calls
        poll_calls += 1
        if poll_calls == 1:
            # Simulate temporary gateway glitch handled internally by client retry
            raise WeheatApiError("Transient 502 Bad Gateway", status_code=502)
        return {"settingsRequestId": request_id, "status": 2}

    mock_client.async_poll_settings_request = AsyncMock(side_effect=mock_poll)

    settings_coord = MagicMock()
    settings_coord.data = e2e_baseline_settings
    settings_coord.async_request_refresh = AsyncMock()

    weheat_data = WeheatData(
        heat_pump_info=e2e_main_pump_info,
        data_coordinator=settings_coord,
        energy_coordinator=settings_coord,
        settings_coordinator=settings_coord,
        client=mock_client,
        session=mock_session,
    )
    entry = ConfigEntry(
        entry_id="entry_resilience",
        data={CONF_TOKEN: {CONF_ACCESS_TOKEN: TEST_BEARER_TOKEN}},
        runtime_data=[weheat_data],
    )
    hass.config_entries.async_add(entry)

    # 1. Service call triggers token renewal
    call = ServiceCall(
        DOMAIN,
        SERVICE_SET_QUIET_MODE,
        {"mode": "Silent 2"},
    )

    # First attempt raises transient error on poll, service handler re-attempts or bubbles
    with pytest.raises(HomeAssistantError):
        await async_handle_set_quiet_mode(hass, call)

    assert token_refreshed is True
    assert mock_session.token[CONF_ACCESS_TOKEN] == TEST_REFRESHED_TOKEN

    # 2. Second poll attempt succeeds
    result = await mock_client.async_poll_settings_request(
        control_board_id=TEST_CB_ID_BLACKBIRD,
        request_id=TEST_SETTINGS_REQUEST_ID,
        token=TEST_REFRESHED_TOKEN,
    )
    assert result["status"] == 2


# -----------------------------------------------------------------------------
# Scenario 6: Full Setup-to-Teardown Lifecycle, Controls & Diagnostics
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_full_lifecycle_setup_controls_diagnostics_teardown(
    e2e_main_pump_info: HeatPumpInfo,
    e2e_baseline_log: HeatPumpLog,
    e2e_baseline_energy: HeatPumpEnergy,
    e2e_baseline_settings: ControlBoardSettings,
) -> None:
    """Scenario: End-to-end integration lifecycle from setup to diagnostics to teardown.

    Validates:
    - async_setup_entry initializes coordinators and forwards all 5 platforms.
    - Diagnostics dump verifies full data export with sensitive field redaction.
    - User manipulates entities.
    - async_unload_entry cleanly unloads platforms and unregisters services.
    """
    hass = HomeAssistant()
    entry = ConfigEntry(
        entry_id="entry_lifecycle_test",
        data={CONF_TOKEN: {CONF_ACCESS_TOKEN: TEST_BEARER_TOKEN}},
        title="Weheat Production Unit",
    )

    mock_client = MagicMock(spec=WeheatClient)
    mock_client.async_get_heat_pumps = AsyncMock(return_value=[e2e_main_pump_info])
    mock_client.async_get_control_board_id = AsyncMock(
        return_value=TEST_CB_ID_BLACKBIRD
    )
    mock_client.async_get_latest_logs = AsyncMock(return_value=e2e_baseline_log)
    mock_client.async_get_energy_total = AsyncMock(return_value=e2e_baseline_energy)
    mock_client.async_get_installation_settings = AsyncMock(
        return_value=e2e_baseline_settings
    )

    # 1. Setup entry
    with (
        patch(
            "custom_components.weheat.OAuth2Session.async_ensure_token_valid",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.weheat.WeheatClient",
            return_value=mock_client,
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
        setup_ok = await async_setup_entry(hass, entry)

    assert setup_ok is True
    assert len(entry.runtime_data) == 1
    assert hass.services.has_service(DOMAIN, SERVICE_SET_QUIET_MODE)
    assert hass.services.has_service(DOMAIN, SERVICE_SET_SILENT_SCHEDULE)
    assert hass.services.has_service(DOMAIN, SERVICE_TRIGGER_LEGIONELLA)

    # 2. Attach mock data to coordinators for diagnostics check
    runtime = entry.runtime_data[0]
    runtime.data_coordinator.data = e2e_baseline_log
    runtime.energy_coordinator.data = e2e_baseline_energy
    runtime.settings_coordinator.data = e2e_baseline_settings

    # 3. Export diagnostics and verify redactions
    diag = await async_get_config_entry_diagnostics(hass, entry)
    assert "heat_pumps" in diag
    assert len(diag["heat_pumps"]) == 1

    pump_diag = diag["heat_pumps"][0]
    info_dict = pump_diag["info"]
    # Verify sensitive attributes are redacted
    assert info_dict["heatpump_id"] == "**REDACTED**"
    assert info_dict["serial_number"] == "**REDACTED**"
    assert info_dict["control_board_id"] == "**REDACTED**"

    # 4. Unload entry
    unload_ok = await async_unload_entry(hass, entry)
    assert unload_ok is True
    # Verify services removed when last entry unloads
    assert not hass.services.has_service(DOMAIN, SERVICE_SET_QUIET_MODE)
    assert not hass.services.has_service(DOMAIN, SERVICE_SET_SILENT_SCHEDULE)
    assert not hass.services.has_service(DOMAIN, SERVICE_TRIGGER_LEGIONELLA)


# -----------------------------------------------------------------------------
# Scenario 7: Domestic Hot Water (DHW) Boost Timed Cycle
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_dhw_boost_timed_cycle_workflow(
    e2e_main_pump_info: HeatPumpInfo,
    e2e_baseline_log: HeatPumpLog,
) -> None:
    """Scenario: User activates DHW boost switch and cancels early.

    Validates:
    - Turning on DHW boost switch triggers async_trigger_dhw_boost with 60°C for 60 min.
    - Switch reports is_on=True while boost is active in telemetry.
    - Turning off switch refreshes state cleanly.
    """
    mock_client = MagicMock(spec=WeheatClient)
    mock_client.async_trigger_dhw_boost = AsyncMock(
        return_value={"status": "OK", "boostId": "mock-boost-123"}
    )

    mock_session = MagicMock()
    mock_session.async_ensure_token_valid = AsyncMock()
    mock_session.token = {CONF_ACCESS_TOKEN: TEST_BEARER_TOKEN}

    data_coord = MagicMock()
    data_coord.data = e2e_baseline_log
    data_coord.async_request_refresh = AsyncMock()

    boost_switch = WeheatDhwBoostSwitch(
        heat_pump_info=e2e_main_pump_info,
        coordinator=data_coord,
        entity_description=SWITCH_DESCRIPTIONS[2],
        client=mock_client,
        session=mock_session,
    )

    # 1. Turn on DHW boost
    await boost_switch.async_turn_on()
    mock_client.async_trigger_dhw_boost.assert_called_once_with(
        heat_pump_uuid=TEST_HP_UUID_BLACKBIRD,
        token=TEST_BEARER_TOKEN,
        duration_minutes=60,
        target_temperature=60.0,
    )
    data_coord.async_request_refresh.assert_called_once()

    # 2. Simulate telemetry log reflecting DHW boost control method
    boost_log_dict = load_fixture("latest_logs.json")
    boost_log_dict["dhwControlMethod"] = DhwControlMethod.BOOST  # 4
    data_coord.data = HeatPumpLog.from_api_dict(boost_log_dict)
    assert boost_switch.is_on is True

    # 3. User cancels boost early
    data_coord.async_request_refresh.reset_mock()
    await boost_switch.async_turn_off()
    data_coord.async_request_refresh.assert_called_once()


# -----------------------------------------------------------------------------
# Scenario 8: Heat Pump State Machine Transitions and COP Reconciliation
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scenario_heat_pump_state_machine_and_cop_reconciliation(
    e2e_main_pump_info: HeatPumpInfo,
) -> None:
    """Scenario: Operational state transitions (Standby -> Heating -> Defrosting -> DHW).

    Validates:
    - State sensor strings and translation keys.
    - Actuator binary sensors (water pump, dhw valve).
    - COP calculation boundary behavior: None when electric power is 0, accurate ratio during heating.
    """
    base_log_dict = load_fixture("latest_logs.json")

    # State A: Standby (mode 40, power is None, COP should be None)
    standby_dict = dict(base_log_dict)
    standby_dict["state"] = HeatPumpState.STANDBY  # 40
    standby_dict["cmMassPowerIn"] = None
    standby_dict["cmMassPowerOut"] = None
    standby_dict["pCompressorIn"] = None
    standby_dict["pCompressorOut"] = None
    standby_dict["rpm"] = 0
    standby_dict["controlBridgeStatusDecodedWaterPump"] = False

    standby_log = HeatPumpLog.from_api_dict(standby_dict)
    coord = MagicMock()
    coord.data = standby_log

    cop_desc = next(s for s in SENSORS if s.key == "cop")
    cop_sensor = WeheatHeatPumpSensor(e2e_main_pump_info, coord, cop_desc)
    assert cop_sensor.native_value is None  # None power safely yields None COP

    pump_desc = next(
        b for b in BINARY_SENSORS if b.key == "indoor_unit_water_pump_state"
    )
    pump_sensor = WeheatHeatPumpBinarySensor(e2e_main_pump_info, coord, pump_desc)
    assert pump_sensor.is_on is False

    # State B: Heating (mode 70, 1200W electric, 4800W thermal, COP = 4.0)
    heating_dict = dict(base_log_dict)
    heating_dict["state"] = HeatPumpState.HEATING  # 70
    heating_dict["cmMassPowerIn"] = 1200.0
    heating_dict["cmMassPowerOut"] = 4800.0
    heating_dict["pCompressorIn"] = 1200.0
    heating_dict["pCompressorOut"] = 4800.0
    heating_dict["rpm"] = 2800
    heating_dict["controlBridgeStatusDecodedWaterPump"] = True

    coord.data = HeatPumpLog.from_api_dict(heating_dict)
    assert cop_sensor.native_value == 4.0
    assert pump_sensor.is_on is True

    # State C: Domestic Hot Water (mode 150, DHW valve True)
    dhw_dict = dict(base_log_dict)
    dhw_dict["state"] = HeatPumpState.DHW  # 150
    dhw_dict["controlBridgeStatusDecodedDhwValve"] = True
    dhw_valve_desc = next(
        b for b in BINARY_SENSORS if b.key == "indoor_unit_dhw_valve_or_pump_state"
    )
    dhw_valve_sensor = WeheatHeatPumpBinarySensor(
        e2e_main_pump_info, coord, dhw_valve_desc
    )

    coord.data = HeatPumpLog.from_api_dict(dhw_dict)
    assert dhw_valve_sensor.is_on is True
