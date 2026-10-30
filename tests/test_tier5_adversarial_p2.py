"""Tier 5 Adversarial Coverage Hardening Suite - Phase 2.

Empirical verification suite designed by Challenger 2 (m5_chal2).
Focus areas:
1. `custom_components/weheat/services.py` & `services.yaml`
   - YAML specification integrity and action definitions
   - Target resolution: heat_pump_id alias, control_board_id, device_id formats (string, list, tuple, set, empty, invalid types)
   - Real Device Registry and Entity Registry resolution with mock registries & exception fallbacks
   - Simultaneous multi-target dispatch and per-device coordinator refresh
   - Parameter parsing stress: boundary times, whitespace, invalid formats, days shortcuts/aliases/types
   - Boolean type-traps (preventing bool masquerading as int in mode/preset)
   - Control board ID on-demand discovery, caching, and failure branches
   - Comprehensive error bubbling: ClientResponseError (4xx, 5xx), ClientError, TimeoutError, generic Exception
   - Partial failure in multi-pump dispatch
2. `custom_components/weheat/config_flow.py`
   - Reauth step dialogs (async_step_reauth, async_step_reauth_confirm)
   - Generic unhandled exception bubbling caught cleanly as oauth_failed
   - Reauth missing existing entry RuntimeError guard
3. `custom_components/weheat/diagnostics.py`
   - Multi-device topology independent redaction across multiple heat pumps
   - Injected sensitive keys verification across info, logs, and energy dictionaries
4. `custom_components/weheat/__init__.py`
   - Multi-pump entry setup with 2+ heat pumps (parallel first refresh across all coordinators)
   - Token validation HTTP status codes (400, 401, 403 -> ConfigEntryAuthFailed; 500, 502, 503, 504, ClientError -> ConfigEntryNotReady)
   - Heat pump discovery exception handling (WeheatAuthError, WeheatApiError, WeheatTimeoutError, ClientError)
   - Multi-entry lifecycle: service retention when another entry remains active
   - Unload platform failure branch (services retained if unload fails)
   - Service registration & unregistration idempotency under burst calls
"""

from __future__ import annotations

import asyncio
import datetime
from http import HTTPStatus
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import ClientError, ClientResponseError
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_TOKEN
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
)

from custom_components.weheat import (
    PLATFORMS,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.weheat.client import (
    WeheatApiError,
    WeheatAuthError,
    WeheatClient,
    WeheatError,
    WeheatTimeoutError,
)
from custom_components.weheat.config_flow import OAuth2FlowHandler
from custom_components.weheat.const import DAYS_OF_WEEK, DOMAIN
from custom_components.weheat.coordinator import WeheatData
from custom_components.weheat.diagnostics import async_get_config_entry_diagnostics
from custom_components.weheat.models import (
    HeatPumpEnergy,
    HeatPumpInfo,
    HeatPumpLog,
    PerformanceModePreset,
    SilentHoursScheduledPreset,
)
from custom_components.weheat.services import (
    SERVICE_SET_QUIET_MODE,
    SERVICE_SET_SILENT_SCHEDULE,
    SERVICE_TRIGGER_LEGIONELLA,
    _parse_days,
    _parse_time,
    _resolve_target_data,
    async_handle_set_quiet_mode,
    async_handle_set_silent_schedule,
    async_handle_trigger_legionella,
    async_setup_services,
    async_unload_services,
)
from tests.ha_compat import FlowAbortException

TEST_HP_UUID_1 = "00000000-0000-0000-0000-000000000001"
TEST_HP_UUID_2 = "00000000-0000-0000-0000-000000000002"
TEST_CB_ID_1 = "cb-00000000-0000-0000-0000-000000000001"
TEST_CB_ID_2 = "cb-00000000-0000-0000-0000-000000000002"
TEST_TOKEN = "mock_tier5_token_xyz"
TEST_REQ_ID = "00000000-0000-0000-0000-000000000999"


@pytest.fixture
def mock_hp_info_1() -> HeatPumpInfo:
    return HeatPumpInfo(
        heatpump_id=TEST_HP_UUID_1,
        name="Blackbird Pump",
        model=0,
        serial_number="SN-001",
        control_board_id=TEST_CB_ID_1,
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
        has_dhw=False,
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
def mock_settings_coordinator() -> MagicMock:
    coord = MagicMock()
    coord.async_request_refresh = AsyncMock()
    return coord


@pytest.fixture
def mock_weheat_data_1(
    mock_hp_info_1: HeatPumpInfo,
    mock_weheat_client: MagicMock,
    mock_session: MagicMock,
    mock_coordinator: MagicMock,
    mock_settings_coordinator: MagicMock,
) -> WeheatData:
    return WeheatData(
        heat_pump_info=mock_hp_info_1,
        data_coordinator=mock_coordinator,
        energy_coordinator=mock_coordinator,
        settings_coordinator=mock_settings_coordinator,
        client=mock_weheat_client,
        session=mock_session,
    )


@pytest.fixture
def mock_weheat_data_2(
    mock_hp_info_2: HeatPumpInfo,
    mock_weheat_client: MagicMock,
    mock_session: MagicMock,
    mock_coordinator: MagicMock,
    mock_settings_coordinator: MagicMock,
) -> WeheatData:
    return WeheatData(
        heat_pump_info=mock_hp_info_2,
        data_coordinator=mock_coordinator,
        energy_coordinator=mock_coordinator,
        settings_coordinator=mock_settings_coordinator,
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
# 1. SERVICES YAML SPECIFICATION & TARGET RESOLUTION ADVERSARIAL TESTS
# =============================================================================


def test_services_yaml_structure_and_options_integrity() -> None:
    """Verify services.yaml exists and contains required service actions and schemas."""
    services_yaml_path = (
        Path(__file__).parent.parent / "custom_components" / "weheat" / "services.yaml"
    )
    assert services_yaml_path.exists(), "services.yaml is missing!"

    content = services_yaml_path.read_text(encoding="utf-8")

    # Assert service action headers
    assert "set_quiet_mode:" in content
    assert "set_silent_schedule:" in content
    assert "trigger_legionella_prevention:" in content

    # Assert set_quiet_mode fields and options
    assert "mode:" in content
    assert "Performance" in content
    assert "Silent 1" in content
    assert "Silent 2" in content
    assert "Silent 3" in content
    assert "Silent 4" in content

    # Assert set_silent_schedule fields
    assert "preset:" in content
    assert "days:" in content
    assert "start_time:" in content
    assert "stop_time:" in content


@pytest.mark.asyncio
async def test_target_resolution_fallback_to_entries_dict(
    mock_weheat_data_1: WeheatData,
) -> None:
    """Verify _resolve_target_data handles hass.config_entries with .entries dict instead of .async_entries."""

    class MockEntriesHass:
        class MockConfigEntries:
            def __init__(self) -> None:
                self.entries = {
                    DOMAIN: [
                        ConfigEntry(
                            entry_id="e1",
                            runtime_data=[mock_weheat_data_1],
                        )
                    ]
                }

        def __init__(self) -> None:
            self.config_entries = self.MockConfigEntries()

    hass = MockEntriesHass()
    call = ServiceCall(DOMAIN, SERVICE_SET_QUIET_MODE, {"mode": 0})
    targets = _resolve_target_data(hass, call)  # type: ignore[arg-type]
    assert len(targets) == 1
    assert targets[0].heat_pump_info.heatpump_id == TEST_HP_UUID_1


@pytest.mark.asyncio
async def test_target_resolution_heatpump_id_alias_and_whitespace(
    hass_with_single_pump: HomeAssistant,
) -> None:
    """Verify target resolution supports heatpump_id (alias without underscore) and strips whitespace."""
    # 1. heatpump_id alias with whitespace
    call = ServiceCall(
        DOMAIN,
        SERVICE_SET_QUIET_MODE,
        {"mode": 0, "heatpump_id": f"  {TEST_HP_UUID_1}  "},
    )
    targets = _resolve_target_data(hass_with_single_pump, call)
    assert len(targets) == 1
    assert targets[0].heat_pump_info.heatpump_id == TEST_HP_UUID_1

    # 2. Unknown heat_pump_id raises descriptive HomeAssistantError
    bad_call = ServiceCall(
        DOMAIN,
        SERVICE_SET_QUIET_MODE,
        {"mode": 0, "heat_pump_id": "non-existent-uuid-999"},
    )
    with pytest.raises(HomeAssistantError) as exc_info:
        _resolve_target_data(hass_with_single_pump, bad_call)
    assert "No Weheat heat pump found with ID 'non-existent-uuid-999'" in str(
        exc_info.value
    )


@pytest.mark.asyncio
async def test_target_resolution_by_control_board_id(
    hass_with_two_pumps: HomeAssistant,
) -> None:
    """Verify target resolution matches control_board_id with whitespace stripping."""
    # 1. Match Pump 2 via control_board_id
    call = ServiceCall(
        DOMAIN,
        SERVICE_SET_QUIET_MODE,
        {"mode": 0, "control_board_id": f"  {TEST_CB_ID_2}  "},
    )
    targets = _resolve_target_data(hass_with_two_pumps, call)
    assert len(targets) == 1
    assert targets[0].heat_pump_info.heatpump_id == TEST_HP_UUID_2

    # 2. Unknown control_board_id raises descriptive HomeAssistantError
    bad_call = ServiceCall(
        DOMAIN,
        SERVICE_SET_QUIET_MODE,
        {"mode": 0, "control_board_id": "cb-unknown-999"},
    )
    with pytest.raises(HomeAssistantError) as exc_info:
        _resolve_target_data(hass_with_two_pumps, bad_call)
    assert "No Weheat heat pump found with control board ID 'cb-unknown-999'" in str(
        exc_info.value
    )


@pytest.mark.asyncio
async def test_target_resolution_nested_target_dict_device_and_entity(
    hass_with_single_pump: HomeAssistant,
) -> None:
    """Verify nested target dictionary extraction for device_id and entity_id."""
    # 1. Nested target device_id
    call_dev = ServiceCall(
        DOMAIN,
        SERVICE_SET_QUIET_MODE,
        {"mode": 0, "target": {"device_id": TEST_HP_UUID_1}},
    )
    targets_dev = _resolve_target_data(hass_with_single_pump, call_dev)
    assert len(targets_dev) == 1

    # 2. Nested target entity_id
    call_ent = ServiceCall(
        DOMAIN,
        SERVICE_SET_QUIET_MODE,
        {"mode": 0, "target": {"entity_id": f"sensor.blackbird_{TEST_HP_UUID_1}"}},
    )
    targets_ent = _resolve_target_data(hass_with_single_pump, call_ent)
    assert len(targets_ent) == 1

    # 3. Non-dict target is safely ignored
    call_nondict = ServiceCall(
        DOMAIN,
        SERVICE_SET_QUIET_MODE,
        {"mode": 0, "target": "invalid_string_target"},
    )
    # Single pump resolves automatically
    targets_nondict = _resolve_target_data(hass_with_single_pump, call_nondict)
    assert len(targets_nondict) == 1


@pytest.mark.asyncio
async def test_target_resolution_device_id_formats_and_empty_list(
    hass_with_single_pump: HomeAssistant,
) -> None:
    """Verify device_id handles tuple, set, empty sequences, and invalid types."""
    # 1. Tuple of device_ids
    call_tuple = ServiceCall(
        DOMAIN,
        SERVICE_SET_QUIET_MODE,
        {"mode": 0, "device_id": (TEST_HP_UUID_1,)},
    )
    targets = _resolve_target_data(hass_with_single_pump, call_tuple)
    assert len(targets) == 1

    # 2. Set of device_ids
    call_set = ServiceCall(
        DOMAIN,
        SERVICE_SET_QUIET_MODE,
        {"mode": 0, "device_id": {TEST_HP_UUID_1}},
    )
    targets_set = _resolve_target_data(hass_with_single_pump, call_set)
    assert len(targets_set) == 1

    # 3. Empty list raises HomeAssistantError
    call_empty = ServiceCall(
        DOMAIN,
        SERVICE_SET_QUIET_MODE,
        {"mode": 0, "device_id": []},
    )
    with pytest.raises(HomeAssistantError) as exc_info:
        _resolve_target_data(hass_with_single_pump, call_empty)
    assert "No device_id specified in target list" in str(exc_info.value)

    # 4. Invalid device_id type (float, bool, dict) raises HomeAssistantError
    for invalid_val in (12.34, True, {"dev": 1}):
        call_bad = ServiceCall(
            DOMAIN,
            SERVICE_SET_QUIET_MODE,
            {"mode": 0, "device_id": invalid_val},
        )
        with pytest.raises(HomeAssistantError) as exc_bad:
            _resolve_target_data(hass_with_single_pump, call_bad)
        assert "Invalid device_id format" in str(exc_bad.value)


@pytest.mark.asyncio
async def test_target_resolution_entity_id_formats_and_empty_list(
    hass_with_single_pump: HomeAssistant,
) -> None:
    """Verify entity_id handles tuple, set, empty sequences, and invalid types."""
    # 1. Tuple of entity_ids
    call_tuple = ServiceCall(
        DOMAIN,
        SERVICE_SET_QUIET_MODE,
        {"mode": 0, "entity_id": (f"sensor.pump_{TEST_HP_UUID_1}",)},
    )
    targets = _resolve_target_data(hass_with_single_pump, call_tuple)
    assert len(targets) == 1

    # 2. Set of entity_ids
    call_set = ServiceCall(
        DOMAIN,
        SERVICE_SET_QUIET_MODE,
        {"mode": 0, "entity_id": {f"sensor.pump_{TEST_HP_UUID_1}"}},
    )
    targets_set = _resolve_target_data(hass_with_single_pump, call_set)
    assert len(targets_set) == 1

    # 3. Empty list raises HomeAssistantError
    call_empty = ServiceCall(
        DOMAIN,
        SERVICE_SET_QUIET_MODE,
        {"mode": 0, "entity_id": []},
    )
    with pytest.raises(HomeAssistantError) as exc_info:
        _resolve_target_data(hass_with_single_pump, call_empty)
    assert "No entity_id specified in target list" in str(exc_info.value)

    # 4. Invalid entity_id type raises HomeAssistantError
    for invalid_val in (54321, False, {"ent": 1}):
        call_bad = ServiceCall(
            DOMAIN,
            SERVICE_SET_QUIET_MODE,
            {"mode": 0, "entity_id": invalid_val},
        )
        with pytest.raises(HomeAssistantError) as exc_bad:
            _resolve_target_data(hass_with_single_pump, call_bad)
        assert "Invalid entity_id format" in str(exc_bad.value)


@pytest.mark.asyncio
async def test_target_resolution_with_mock_device_registry(
    hass_with_two_pumps: HomeAssistant,
) -> None:
    """Verify device registry resolution matches DOMAIN identifiers and tolerates other domains."""
    mock_dev_reg = MagicMock()

    # Device 1: Matches DOMAIN
    dev_entry_1 = MagicMock()
    dev_entry_1.identifiers = {(DOMAIN, TEST_HP_UUID_1)}

    # Device 2: Matches another domain (should be ignored)
    dev_entry_2 = MagicMock()
    dev_entry_2.identifiers = {("hue", "light_123"), ("zigbee", "zb_456")}

    # Device 3: Malformed identifier (not length 2)
    dev_entry_3 = MagicMock()
    dev_entry_3.identifiers = {("only_one_item",), (DOMAIN, "extra", "bad")}

    mock_dev_reg.async_get.side_effect = lambda d_id: {
        "dev_ha_1": dev_entry_1,
        "dev_ha_2": dev_entry_2,
        "dev_ha_3": dev_entry_3,
    }.get(d_id)

    with patch(
        "homeassistant.helpers.device_registry.async_get",
        return_value=mock_dev_reg,
        create=True,
    ):
        # 1. Matching device resolves to Pump 1
        call_1 = ServiceCall(
            DOMAIN,
            SERVICE_SET_QUIET_MODE,
            {"mode": 0, "device_id": "dev_ha_1"},
        )
        targets = _resolve_target_data(hass_with_two_pumps, call_1)
        assert len(targets) == 1
        assert targets[0].heat_pump_info.heatpump_id == TEST_HP_UUID_1

        # 2. Non-matching device entry raises HomeAssistantError
        call_2 = ServiceCall(
            DOMAIN,
            SERVICE_SET_QUIET_MODE,
            {"mode": 0, "device_id": "dev_ha_2"},
        )
        with pytest.raises(HomeAssistantError):
            _resolve_target_data(hass_with_two_pumps, call_2)


@pytest.mark.asyncio
async def test_target_resolution_mock_device_registry_exception_fallback(
    hass_with_single_pump: HomeAssistant,
) -> None:
    """Verify device registry lookup exception falls back gracefully to direct heatpump_id matching."""
    with patch(
        "homeassistant.helpers.device_registry.async_get",
        side_effect=RuntimeError("Device registry corrupted or unavailable"),
        create=True,
    ):
        call = ServiceCall(
            DOMAIN,
            SERVICE_SET_QUIET_MODE,
            {"mode": 0, "device_id": TEST_HP_UUID_1},
        )
        targets = _resolve_target_data(hass_with_single_pump, call)
        assert len(targets) == 1
        assert targets[0].heat_pump_info.heatpump_id == TEST_HP_UUID_1


@pytest.mark.asyncio
async def test_target_resolution_with_mock_entity_registry(
    hass_with_two_pumps: HomeAssistant,
) -> None:
    """Verify entity registry resolution matches DOMAIN platform and unique_id."""
    import sys
    import types

    mock_ent_reg = MagicMock()

    # Entity 1: Matches DOMAIN with pump 2 unique_id
    ent_entry_1 = MagicMock()
    ent_entry_1.platform = DOMAIN
    ent_entry_1.unique_id = f"{TEST_HP_UUID_2}_power"

    # Entity 2: Different platform
    ent_entry_2 = MagicMock()
    ent_entry_2.platform = "sensor"
    ent_entry_2.unique_id = f"{TEST_HP_UUID_1}_power"

    # Entity 3: unique_id is None
    ent_entry_3 = MagicMock()
    ent_entry_3.platform = DOMAIN
    ent_entry_3.unique_id = None

    mock_ent_reg.async_get.side_effect = lambda e_id: {
        "sensor.sparrow_power": ent_entry_1,
        "sensor.other_platform": ent_entry_2,
        "sensor.no_unique_id": ent_entry_3,
    }.get(e_id)

    er_module = types.ModuleType("homeassistant.helpers.entity_registry")
    er_module.async_get = MagicMock(return_value=mock_ent_reg)  # type: ignore[attr-defined]

    with patch.dict(sys.modules, {"homeassistant.helpers.entity_registry": er_module}):
        call_1 = ServiceCall(
            DOMAIN,
            SERVICE_SET_QUIET_MODE,
            {"mode": 0, "entity_id": "sensor.sparrow_power"},
        )
        targets = _resolve_target_data(hass_with_two_pumps, call_1)
        assert len(targets) == 1
        assert targets[0].heat_pump_info.heatpump_id == TEST_HP_UUID_2


@pytest.mark.asyncio
async def test_target_resolution_multi_target_simultaneous_execution(
    hass_with_two_pumps: HomeAssistant,
    mock_weheat_client: MagicMock,
    mock_weheat_data_1: WeheatData,
    mock_weheat_data_2: WeheatData,
) -> None:
    """Verify service call targeting multiple heat pumps executes across all targets and refreshes coordinators."""
    call = ServiceCall(
        DOMAIN,
        SERVICE_SET_QUIET_MODE,
        {
            "mode": "Silent 2",
            "device_id": [TEST_HP_UUID_1, TEST_HP_UUID_2],
        },
    )
    await async_handle_set_quiet_mode(hass_with_two_pumps, call)

    # Verify both pumps had async_set_performance_mode called
    assert mock_weheat_client.async_set_performance_mode.call_count == 2
    mock_weheat_client.async_set_performance_mode.assert_any_call(
        control_board_id=TEST_CB_ID_1,
        token=TEST_TOKEN,
        preset=PerformanceModePreset.SILENT_2,
    )
    mock_weheat_client.async_set_performance_mode.assert_any_call(
        control_board_id=TEST_CB_ID_2,
        token=TEST_TOKEN,
        preset=PerformanceModePreset.SILENT_2,
    )

    # Verify coordinators for both pumps refreshed
    assert mock_weheat_data_1.data_coordinator.async_request_refresh.call_count >= 1
    assert mock_weheat_data_2.data_coordinator.async_request_refresh.call_count >= 1


# =============================================================================
# 2. SERVICES PARAMETER PARSING & TYPE-TRAP STRESS TESTS
# =============================================================================


@pytest.mark.parametrize(
    ("valid_time_input", "expected_hour", "expected_min"),
    [
        ("00:00", 0, 0),
        ("23:59", 23, 59),
        ("0:0", 0, 0),
        ("05:07", 5, 7),
        ("22:30:45", 22, 30),  # With seconds
        ("  14:45  ", 14, 45),  # Whitespace
        (datetime.time(18, 15), 18, 15),
    ],
)
def test_parse_time_valid_inputs(
    valid_time_input: Any, expected_hour: int, expected_min: int
) -> None:
    """Verify _parse_time extracts (hour, minute) correctly across valid formats."""
    h, m = _parse_time(valid_time_input)
    assert h == expected_hour
    assert m == expected_min


@pytest.mark.parametrize(
    "invalid_time_input",
    [
        "24:00",  # Hour out of bounds
        "00:60",  # Minute out of bounds
        "-1:00",  # Negative hour
        "12:-5",  # Negative minute
        "12",  # Missing colon
        "",  # Empty
        "   ",  # Whitespace only
        "noon",  # Words
        "xx:yy",  # Non-numeric
        1234,  # Int
        None,  # None
        ["22:00"],  # List
        {"time": "22:00"},  # Dict
    ],
)
def test_parse_time_invalid_inputs_raise(invalid_time_input: Any) -> None:
    """Verify _parse_time raises HomeAssistantError on all invalid inputs."""
    with pytest.raises(HomeAssistantError) as exc_info:
        _parse_time(invalid_time_input)
    assert "Invalid time format" in str(exc_info.value)


@pytest.mark.parametrize(
    ("days_input", "expected_list"),
    [
        (None, list(DAYS_OF_WEEK)),
        ("", list(DAYS_OF_WEEK)),
        ([], list(DAYS_OF_WEEK)),
        ("all", list(DAYS_OF_WEEK)),
        ("*", list(DAYS_OF_WEEK)),
        (
            "weekdays",
            ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
        ),
        (
            "weekday",
            ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
        ),
        ("weekend", ["Saturday", "Sunday"]),
        ("weekends", ["Saturday", "Sunday"]),
        ("monday, friday, monday", ["Monday", "Friday"]),  # Deduplication
        ("  WEDNESDAY  ", ["Wednesday"]),
        (["Monday", "tuesday", "MONDAY"], ["Monday", "Tuesday"]),
    ],
)
def test_parse_days_valid_inputs(days_input: Any, expected_list: list[str]) -> None:
    """Verify _parse_days parses and normalizes days specifications."""
    assert _parse_days(days_input) == expected_list


@pytest.mark.parametrize(
    "invalid_days_input",
    [
        "Funday",
        "Monday, Funday",
        ["Monday", "NotADay"],
        12345,
        {"Monday": True},
        ["Monday", 123],
    ],
)
def test_parse_days_invalid_inputs_raise(invalid_days_input: Any) -> None:
    """Verify _parse_days raises HomeAssistantError on malformed day names or types."""
    with pytest.raises(HomeAssistantError):
        _parse_days(invalid_days_input)


@pytest.mark.asyncio
async def test_set_quiet_mode_boolean_type_trap(
    hass_with_single_pump: HomeAssistant,
) -> None:
    """Verify boolean mode (True/False) raises HomeAssistantError rather than masquerading as 1/0."""
    for bool_val in (True, False):
        call = ServiceCall(DOMAIN, SERVICE_SET_QUIET_MODE, {"mode": bool_val})
        with pytest.raises(HomeAssistantError) as exc_info:
            await async_handle_set_quiet_mode(hass_with_single_pump, call)
        assert f"Invalid quiet mode '{bool_val}'" in str(exc_info.value)


@pytest.mark.asyncio
async def test_set_silent_schedule_boolean_preset_trap(
    hass_with_single_pump: HomeAssistant,
) -> None:
    """Verify boolean preset (True/False) raises HomeAssistantError rather than masquerading as 1/0."""
    for bool_val in (True, False):
        call = ServiceCall(
            DOMAIN,
            SERVICE_SET_SILENT_SCHEDULE,
            {"preset": bool_val, "start_time": "22:00", "stop_time": "07:00"},
        )
        with pytest.raises(HomeAssistantError) as exc_info:
            await async_handle_set_silent_schedule(hass_with_single_pump, call)
        assert f"Invalid silent hours preset '{bool_val}'" in str(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("preset_input", "expected_preset_enum"),
    [
        (0, SilentHoursScheduledPreset.OFF),
        ("0", SilentHoursScheduledPreset.OFF),
        ("off", SilentHoursScheduledPreset.OFF),
        ("disabled", SilentHoursScheduledPreset.OFF),
        (1, SilentHoursScheduledPreset.SILENT_1),
        ("silent 1", SilentHoursScheduledPreset.SILENT_1),
        (2, SilentHoursScheduledPreset.SILENT_2),
        (3, SilentHoursScheduledPreset.SILENT_3),
        (4, SilentHoursScheduledPreset.SILENT_4),
        (5, SilentHoursScheduledPreset.OFF_ALT),
        ("silent 5", SilentHoursScheduledPreset.OFF_ALT),
        (6, SilentHoursScheduledPreset.SILENT_6),
        ("silent 6", SilentHoursScheduledPreset.SILENT_6),
        (7, SilentHoursScheduledPreset.SILENT_7),
        ("silent 7", SilentHoursScheduledPreset.SILENT_7),
    ],
)
async def test_set_silent_schedule_all_presets_and_aliases(
    hass_with_single_pump: HomeAssistant,
    mock_weheat_client: MagicMock,
    preset_input: Any,
    expected_preset_enum: int,
) -> None:
    """Verify set_silent_schedule maps all 0..7 presets, numbers, strings, and aliases."""
    call = ServiceCall(
        DOMAIN,
        SERVICE_SET_SILENT_SCHEDULE,
        {"preset": preset_input, "start_time": "21:00", "stop_time": "06:00"},
    )
    await async_handle_set_silent_schedule(hass_with_single_pump, call)
    kwargs = mock_weheat_client.async_set_silent_hours_schedule.call_args.kwargs
    assert kwargs["preset"] == expected_preset_enum


@pytest.mark.asyncio
async def test_control_board_id_resolution_and_caching(
    hass_with_single_pump: HomeAssistant,
    mock_weheat_client: MagicMock,
    mock_hp_info_1: HeatPumpInfo,
) -> None:
    """Verify on-demand control_board_id resolution when initially missing, and caching."""
    mock_hp_info_1.control_board_id = ""  # Clear control board ID

    call = ServiceCall(DOMAIN, SERVICE_SET_QUIET_MODE, {"mode": "Performance"})
    await async_handle_set_quiet_mode(hass_with_single_pump, call)

    # Verified client resolved it
    mock_weheat_client.async_get_control_board_id.assert_called_once_with(
        TEST_HP_UUID_1, token=TEST_TOKEN
    )
    assert mock_hp_info_1.control_board_id == TEST_CB_ID_1

    # Second call should use cached ID without calling client again
    mock_weheat_client.async_get_control_board_id.reset_mock()
    await async_handle_set_quiet_mode(hass_with_single_pump, call)
    mock_weheat_client.async_get_control_board_id.assert_not_called()


@pytest.mark.asyncio
async def test_control_board_id_resolution_client_none_raises(
    hass_with_single_pump: HomeAssistant,
    mock_weheat_data_1: WeheatData,
    mock_hp_info_1: HeatPumpInfo,
) -> None:
    """Verify resolution raises HomeAssistantError when client is None."""
    mock_hp_info_1.control_board_id = ""
    mock_weheat_data_1.client = None

    call = ServiceCall(DOMAIN, SERVICE_SET_QUIET_MODE, {"mode": 0})
    with pytest.raises(HomeAssistantError) as exc_info:
        await async_handle_set_quiet_mode(hass_with_single_pump, call)
    assert "Weheat API client is unavailable" in str(exc_info.value)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("client_error", "expected_message_substring"),
    [
        (WeheatError("Board mapping failure"), "Failed to resolve control board ID"),
        (
            ClientResponseError(
                request_info=MagicMock(),
                history=(),
                status=404,
                message="Not Found",
            ),
            "API rejected request with HTTP 404",
        ),
        (
            ClientError("Connection reset"),
            "Network error",
        ),
        (
            TimeoutError("DNS timeout"),
            "Network error",
        ),
        (
            RuntimeError("Unexpected memory corruption"),
            "Unexpected error resolving control board ID",
        ),
    ],
)
async def test_control_board_id_resolution_error_variations(
    hass_with_single_pump: HomeAssistant,
    mock_weheat_client: MagicMock,
    mock_hp_info_1: HeatPumpInfo,
    client_error: Exception,
    expected_message_substring: str,
) -> None:
    """Verify all control board resolution failure types are translated to descriptive HomeAssistantError."""
    mock_hp_info_1.control_board_id = ""
    mock_weheat_client.async_get_control_board_id = AsyncMock(side_effect=client_error)

    call = ServiceCall(DOMAIN, SERVICE_SET_QUIET_MODE, {"mode": 0})
    with pytest.raises(HomeAssistantError) as exc_info:
        await async_handle_set_quiet_mode(hass_with_single_pump, call)
    assert expected_message_substring in str(exc_info.value)


@pytest.mark.asyncio
async def test_services_token_failure_variations(
    hass_with_single_pump: HomeAssistant,
    mock_weheat_data_1: WeheatData,
) -> None:
    """Verify token retrieval edge cases: missing session, invalid token dict, TimeoutError, generic Exception."""
    call = ServiceCall(DOMAIN, SERVICE_SET_QUIET_MODE, {"mode": 0})

    # 1. session is None
    mock_weheat_data_1.session = None
    with pytest.raises(HomeAssistantError) as exc1:
        await async_handle_set_quiet_mode(hass_with_single_pump, call)
    assert "Authentication token unavailable" in str(exc1.value)

    # 2. session.token missing CONF_ACCESS_TOKEN
    mock_session = MagicMock()
    mock_session.async_ensure_token_valid = AsyncMock()
    mock_session.token = {"other_key": "val"}
    mock_weheat_data_1.session = mock_session
    with pytest.raises(HomeAssistantError) as exc2:
        await async_handle_set_quiet_mode(hass_with_single_pump, call)
    assert "Authentication token unavailable" in str(exc2.value)

    # 3. async_ensure_token_valid raises TimeoutError
    mock_session.async_ensure_token_valid = AsyncMock(
        side_effect=TimeoutError("Token endpoint timeout")
    )
    with pytest.raises(HomeAssistantError) as exc3:
        await async_handle_set_quiet_mode(hass_with_single_pump, call)
    assert "Network error refreshing OAuth token" in str(exc3.value)

    # 4. async_ensure_token_valid raises generic Exception
    mock_session.async_ensure_token_valid = AsyncMock(
        side_effect=RuntimeError("Corrupt storage")
    )
    with pytest.raises(HomeAssistantError) as exc4:
        await async_handle_set_quiet_mode(hass_with_single_pump, call)
    assert "Corrupt storage" in str(exc4.value)


# =============================================================================
# 3. ERROR BUBBLING IN SERVICE ACTIONS
# =============================================================================


@pytest.mark.asyncio
async def test_quiet_mode_error_bubbling_client_response_error(
    hass_with_single_pump: HomeAssistant,
    mock_weheat_client: MagicMock,
) -> None:
    """Verify HTTP 500 in async_set_performance_mode bubbles as HomeAssistantError."""
    mock_weheat_client.async_set_performance_mode = AsyncMock(
        side_effect=ClientResponseError(
            request_info=MagicMock(),
            history=(),
            status=500,
            message="Internal Server Error",
        )
    )
    call = ServiceCall(DOMAIN, SERVICE_SET_QUIET_MODE, {"mode": "Performance"})
    with pytest.raises(HomeAssistantError) as exc_info:
        await async_handle_set_quiet_mode(hass_with_single_pump, call)
    assert "HTTP 500 (Internal Server Error)" in str(exc_info.value)


@pytest.mark.asyncio
async def test_quiet_mode_error_bubbling_polling_timeout(
    hass_with_single_pump: HomeAssistant,
    mock_weheat_client: MagicMock,
) -> None:
    """Verify TimeoutError in async_poll_settings_request bubbles as HomeAssistantError."""
    mock_weheat_client.async_poll_settings_request = AsyncMock(
        side_effect=TimeoutError("Settings polling timed out after 30s")
    )
    call = ServiceCall(DOMAIN, SERVICE_SET_QUIET_MODE, {"mode": 1})
    with pytest.raises(HomeAssistantError) as exc_info:
        await async_handle_set_quiet_mode(hass_with_single_pump, call)
    assert "Communication error setting quiet mode" in str(exc_info.value)


@pytest.mark.asyncio
async def test_quiet_mode_error_bubbling_generic_exception(
    hass_with_single_pump: HomeAssistant,
    mock_weheat_client: MagicMock,
) -> None:
    """Verify unexpected generic Exception in async_set_performance_mode bubbles as HomeAssistantError."""
    mock_weheat_client.async_set_performance_mode = AsyncMock(
        side_effect=RuntimeError("Unexpected client crash")
    )
    call = ServiceCall(DOMAIN, SERVICE_SET_QUIET_MODE, {"mode": 1})
    with pytest.raises(HomeAssistantError) as exc_info:
        await async_handle_set_quiet_mode(hass_with_single_pump, call)
    assert "Unexpected error setting quiet mode" in str(exc_info.value)


@pytest.mark.asyncio
async def test_silent_schedule_error_bubbling_variations(
    hass_with_single_pump: HomeAssistant,
    mock_weheat_client: MagicMock,
) -> None:
    """Verify ClientResponseError, ClientError, and generic Exception bubble cleanly in set_silent_schedule."""
    call = ServiceCall(
        DOMAIN,
        SERVICE_SET_SILENT_SCHEDULE,
        {"preset": 1, "start_time": "22:00", "stop_time": "07:00"},
    )

    # 1. ClientResponseError 502
    mock_weheat_client.async_set_silent_hours_schedule = AsyncMock(
        side_effect=ClientResponseError(
            request_info=MagicMock(),
            history=(),
            status=502,
            message="Bad Gateway",
        )
    )
    with pytest.raises(HomeAssistantError) as exc1:
        await async_handle_set_silent_schedule(hass_with_single_pump, call)
    assert "HTTP 502 (Bad Gateway)" in str(exc1.value)

    # 2. ClientError
    mock_weheat_client.async_set_silent_hours_schedule = AsyncMock(
        side_effect=ClientError("SSL handshake failure")
    )
    with pytest.raises(HomeAssistantError) as exc2:
        await async_handle_set_silent_schedule(hass_with_single_pump, call)
    assert "SSL handshake failure" in str(exc2.value)

    # 3. Generic Exception
    mock_weheat_client.async_set_silent_hours_schedule = AsyncMock(
        side_effect=KeyError("Corrupt payload key")
    )
    with pytest.raises(HomeAssistantError) as exc3:
        await async_handle_set_silent_schedule(hass_with_single_pump, call)
    assert "Unexpected error setting silent schedule" in str(exc3.value)


@pytest.mark.asyncio
async def test_trigger_legionella_error_bubbling_variations(
    hass_with_single_pump: HomeAssistant,
    mock_weheat_client: MagicMock,
) -> None:
    """Verify ClientResponseError, ClientError, and generic Exception bubble cleanly in trigger_legionella."""
    call = ServiceCall(DOMAIN, SERVICE_TRIGGER_LEGIONELLA, {})

    # 1. ClientResponseError 403
    mock_weheat_client.async_trigger_legionella_prevention = AsyncMock(
        side_effect=ClientResponseError(
            request_info=MagicMock(),
            history=(),
            status=403,
            message="Feature not licensed",
        )
    )
    with pytest.raises(HomeAssistantError) as exc1:
        await async_handle_trigger_legionella(hass_with_single_pump, call)
    assert "HTTP 403 (Feature not licensed)" in str(exc1.value)

    # 2. ClientError
    mock_weheat_client.async_trigger_legionella_prevention = AsyncMock(
        side_effect=ClientError("Connection aborted")
    )
    with pytest.raises(HomeAssistantError) as exc2:
        await async_handle_trigger_legionella(hass_with_single_pump, call)
    assert "Connection aborted" in str(exc2.value)

    # 3. Generic Exception
    mock_weheat_client.async_trigger_legionella_prevention = AsyncMock(
        side_effect=ValueError("Bad variable value")
    )
    with pytest.raises(HomeAssistantError) as exc3:
        await async_handle_trigger_legionella(hass_with_single_pump, call)
    assert "Unexpected error triggering Legionella prevention" in str(exc3.value)


@pytest.mark.asyncio
async def test_multi_pump_partial_failure_error_bubbling(
    hass_with_two_pumps: HomeAssistant,
    mock_weheat_client: MagicMock,
) -> None:
    """Verify when multi-target service call encounters failure on second pump, error bubbles with pump ID."""

    async def mock_set_perf_side_effect(
        control_board_id: str, token: str, preset: int
    ) -> str:
        if control_board_id == TEST_CB_ID_1:
            return TEST_REQ_ID
        raise WeheatApiError("Device disconnected", status_code=500)

    mock_weheat_client.async_set_performance_mode = AsyncMock(
        side_effect=mock_set_perf_side_effect
    )

    call = ServiceCall(
        DOMAIN,
        SERVICE_SET_QUIET_MODE,
        {"mode": 0, "device_id": [TEST_HP_UUID_1, TEST_HP_UUID_2]},
    )

    with pytest.raises(HomeAssistantError) as exc_info:
        await async_handle_set_quiet_mode(hass_with_two_pumps, call)

    # Assert error identifies Pump 2 as the failure source
    assert f"Failed to set quiet mode for heat pump {TEST_HP_UUID_2}" in str(
        exc_info.value
    )
    assert "Device disconnected" in str(exc_info.value)


# =============================================================================
# 4. CONFIG FLOW ADVERSARIAL EDGE CASES
# =============================================================================


@pytest.mark.asyncio
async def test_config_flow_reauth_step_flow_dialogs() -> None:
    """Verify async_step_reauth and async_step_reauth_confirm step transitions."""
    flow = OAuth2FlowHandler()

    # Step reauth returns reauth_confirm form
    result_reauth = await flow.async_step_reauth({})
    assert result_reauth["type"] == "form"
    assert result_reauth["step_id"] == "reauth_confirm"

    # Confirm dialog without user input returns form
    result_confirm_none = await flow.async_step_reauth_confirm(user_input=None)
    assert result_confirm_none["type"] == "form"
    assert result_confirm_none["step_id"] == "reauth_confirm"

    # Confirm dialog with input transitions to user external auth step
    result_confirm_submit = await flow.async_step_reauth_confirm(user_input={})
    assert result_confirm_submit["type"] == "external"


@pytest.mark.asyncio
async def test_config_flow_unexpected_exception_aborts_oauth_failed() -> None:
    """Verify unhandled generic Exception during user ID lookup aborts with oauth_failed."""
    flow = OAuth2FlowHandler()
    token_data = {
        CONF_TOKEN: {
            CONF_ACCESS_TOKEN: "mock_token",
        }
    }

    with patch(
        "custom_components.weheat.config_flow.WeheatClient.async_get_user_id",
        new=AsyncMock(side_effect=Exception("Unexpected socket EOF")),
    ):
        result = await flow.async_oauth_create_entry(token_data)

    assert result["type"] == "abort"
    assert result["reason"] == "oauth_failed"


def test_config_flow_reauth_missing_existing_entry_raises_runtime_error() -> None:
    """Verify _get_reauth_entry() raises RuntimeError when _reauth_entry is None."""
    flow = OAuth2FlowHandler()
    flow._reauth_entry = None
    with pytest.raises(RuntimeError) as exc_info:
        flow._get_reauth_entry()
    assert "No reauth entry" in str(exc_info.value)


@pytest.mark.asyncio
async def test_config_flow_reauth_unique_id_mismatch_aborts() -> None:
    """Verify reauth aborts with wrong_account when authenticated user ID does not match entry unique ID."""
    flow = OAuth2FlowHandler()
    flow.source = SOURCE_REAUTH
    flow._reauth_entry = ConfigEntry(unique_id="user_expected_123")
    token_data = {CONF_TOKEN: {CONF_ACCESS_TOKEN: "new_token"}}

    with (
        patch(
            "custom_components.weheat.config_flow.WeheatClient.async_get_user_id",
            new=AsyncMock(return_value="user_different_456"),
        ),
        pytest.raises(FlowAbortException) as exc,
    ):
        await flow.async_oauth_create_entry(token_data)

    assert exc.value.reason == "wrong_account"


# =============================================================================
# 5. DIAGNOSTICS MULTI-PUMP INDEPENDENT REDACTION
# =============================================================================


@pytest.mark.asyncio
async def test_diagnostics_multi_pump_independent_redaction(
    mock_hp_info_1: HeatPumpInfo,
    mock_hp_info_2: HeatPumpInfo,
) -> None:
    """Verify diagnostics dump independently redacts multiple heat pumps in multi-device topology."""
    log_1 = HeatPumpLog(
        heat_pump_id=mock_hp_info_1.heatpump_id,
        rpm=2800,
        raw={"heatPumpId": mock_hp_info_1.heatpump_id, "sn": "SN-001"},
    )
    log_2 = HeatPumpLog(
        heat_pump_id=mock_hp_info_2.heatpump_id,
        rpm=1500,
        raw={"heatPumpId": mock_hp_info_2.heatpump_id, "sn": "SN-002"},
    )
    energy_1 = HeatPumpEnergy(
        heatpump_id=mock_hp_info_1.heatpump_id,
        total_ein_heating=100.0,
        raw={"uuid": mock_hp_info_1.heatpump_id},
    )
    energy_2 = HeatPumpEnergy(
        heatpump_id=mock_hp_info_2.heatpump_id,
        total_ein_heating=200.0,
        raw={"uuid": mock_hp_info_2.heatpump_id},
    )

    class CoordShim:
        def __init__(self, data: Any) -> None:
            self.data = data

    class DataShim1:
        heat_pump_info = mock_hp_info_1
        data_coordinator = CoordShim(log_1)
        energy_coordinator = CoordShim(energy_1)

    class DataShim2:
        heat_pump_info = mock_hp_info_2
        data_coordinator = CoordShim(log_2)
        energy_coordinator = CoordShim(energy_2)

    class EntryShim:
        runtime_data = [DataShim1(), DataShim2()]

    diagnostics = await async_get_config_entry_diagnostics(None, EntryShim())  # type: ignore[arg-type]

    assert "heat_pumps" in diagnostics
    assert len(diagnostics["heat_pumps"]) == 2

    # Pump 1 redactions
    hp1 = diagnostics["heat_pumps"][0]
    assert hp1["info"]["heatpump_id"] == "**REDACTED**"
    assert hp1["info"]["serial_number"] == "**REDACTED**"
    assert hp1["logs"]["heatPumpId"] == "**REDACTED**"
    assert hp1["energy"]["uuid"] == "**REDACTED**"

    # Pump 2 redactions
    hp2 = diagnostics["heat_pumps"][1]
    assert hp2["info"]["heatpump_id"] == "**REDACTED**"
    assert hp2["info"]["serial_number"] == "**REDACTED**"
    assert hp2["logs"]["heatPumpId"] == "**REDACTED**"
    assert hp2["energy"]["uuid"] == "**REDACTED**"


# =============================================================================
# 6. INTEGRATION LIFECYCLE, MULTI-ENTRY, AND UNLOAD IDEMPOTENCY
# =============================================================================


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("http_status", "expected_exception_cls"),
    [
        (HTTPStatus.BAD_REQUEST, ConfigEntryAuthFailed),  # 400
        (HTTPStatus.UNAUTHORIZED, ConfigEntryAuthFailed),  # 401
        (HTTPStatus.FORBIDDEN, ConfigEntryAuthFailed),  # 403
        (HTTPStatus.INTERNAL_SERVER_ERROR, ConfigEntryNotReady),  # 500
        (HTTPStatus.BAD_GATEWAY, ConfigEntryNotReady),  # 502
        (HTTPStatus.SERVICE_UNAVAILABLE, ConfigEntryNotReady),  # 503
        (HTTPStatus.GATEWAY_TIMEOUT, ConfigEntryNotReady),  # 504
    ],
)
async def test_setup_entry_token_validation_status_codes(
    http_status: HTTPStatus,
    expected_exception_cls: type[Exception],
) -> None:
    """Verify HTTP status codes during async_ensure_token_valid raise correct exception."""
    hass = HomeAssistant()
    entry = ConfigEntry(
        entry_id="status_entry",
        data={CONF_TOKEN: {CONF_ACCESS_TOKEN: "mock_tok"}},
    )

    err = ClientResponseError(
        request_info=MagicMock(),
        history=(),
        status=http_status,
        message="OAuth Error",
    )

    with (
        patch(
            "custom_components.weheat.OAuth2Session.async_ensure_token_valid",
            new=AsyncMock(side_effect=err),
        ),
        pytest.raises(expected_exception_cls),
    ):
        await async_setup_entry(hass, entry)


@pytest.mark.asyncio
async def test_setup_entry_discovery_timeout_raises_not_ready() -> None:
    """Verify WeheatTimeoutError during discovery raises ConfigEntryNotReady."""
    hass = HomeAssistant()
    entry = ConfigEntry(
        entry_id="timeout_entry",
        data={CONF_TOKEN: {CONF_ACCESS_TOKEN: "mock_tok"}},
    )

    with (
        patch(
            "custom_components.weheat.OAuth2Session.async_ensure_token_valid",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.weheat.WeheatClient.async_get_heat_pumps",
            new=AsyncMock(side_effect=WeheatTimeoutError("Discovery timed out")),
        ),
        pytest.raises(ConfigEntryNotReady),
    ):
        await async_setup_entry(hass, entry)


@pytest.mark.asyncio
async def test_setup_entry_discovery_auth_failed_raises() -> None:
    """Verify WeheatAuthError during discovery raises ConfigEntryAuthFailed."""
    hass = HomeAssistant()
    entry = ConfigEntry(
        entry_id="auth_failed_entry",
        data={CONF_TOKEN: {CONF_ACCESS_TOKEN: "mock_tok"}},
    )

    with (
        patch(
            "custom_components.weheat.OAuth2Session.async_ensure_token_valid",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.weheat.WeheatClient.async_get_heat_pumps",
            new=AsyncMock(side_effect=WeheatAuthError("Invalid credentials or 401")),
        ),
        pytest.raises(ConfigEntryAuthFailed),
    ):
        await async_setup_entry(hass, entry)


@pytest.mark.asyncio
async def test_setup_entry_multi_pump_topology_setup(
    mock_hp_info_1: HeatPumpInfo,
    mock_hp_info_2: HeatPumpInfo,
) -> None:
    """Verify entry setup with 2 pumps initializes all 6 coordinators and forwards platforms."""
    hass = HomeAssistant()
    entry = ConfigEntry(
        entry_id="dual_setup_entry",
        data={CONF_TOKEN: {CONF_ACCESS_TOKEN: "mock_tok"}},
    )

    with (
        patch(
            "custom_components.weheat.OAuth2Session.async_ensure_token_valid",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.weheat.WeheatClient.async_get_heat_pumps",
            new=AsyncMock(return_value=[mock_hp_info_1, mock_hp_info_2]),
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
        result = await async_setup_entry(hass, entry)

    assert result is True
    assert len(entry.runtime_data) == 2
    assert entry.runtime_data[0].heat_pump_info is mock_hp_info_1
    assert entry.runtime_data[1].heat_pump_info is mock_hp_info_2
    assert len(hass.config_entries.forwarded_setups) == 1
    assert hass.config_entries.forwarded_setups[0][1] == PLATFORMS


@pytest.mark.asyncio
async def test_multi_entry_lifecycle_and_service_retention() -> None:
    """Verify services remain active when unloading one config entry while another remains loaded."""
    hass = HomeAssistant()
    await async_setup_services(hass)

    entry_a = ConfigEntry(entry_id="entry_alpha", data={})
    entry_b = ConfigEntry(entry_id="entry_beta", data={})
    hass.config_entries.async_add(entry_a)
    hass.config_entries.async_add(entry_b)

    assert hass.services.has_service(DOMAIN, SERVICE_SET_QUIET_MODE)

    # 1. Unload Entry A: Entry B is still active
    unload_a = await async_unload_entry(hass, entry_a)
    assert unload_a is True
    # Services MUST remain registered
    assert hass.services.has_service(DOMAIN, SERVICE_SET_QUIET_MODE)
    assert hass.services.has_service(DOMAIN, SERVICE_SET_SILENT_SCHEDULE)
    assert hass.services.has_service(DOMAIN, SERVICE_TRIGGER_LEGIONELLA)

    # 2. Unload Entry B: Now zero entries remain
    hass.config_entries._entries[DOMAIN] = [entry_b]
    unload_b = await async_unload_entry(hass, entry_b)
    assert unload_b is True
    # Services MUST now be unloaded
    assert not hass.services.has_service(DOMAIN, SERVICE_SET_QUIET_MODE)
    assert not hass.services.has_service(DOMAIN, SERVICE_SET_SILENT_SCHEDULE)
    assert not hass.services.has_service(DOMAIN, SERVICE_TRIGGER_LEGIONELLA)


@pytest.mark.asyncio
async def test_unload_platforms_failure_does_not_unload_services() -> None:
    """Verify when async_unload_platforms returns False, services are not unloaded."""
    hass = HomeAssistant()
    await async_setup_services(hass)

    entry = ConfigEntry(entry_id="failing_unload_entry", data={})
    hass.config_entries.async_add(entry)

    # Mock async_unload_platforms returning False
    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        new=AsyncMock(return_value=False),
    ):
        unload_res = await async_unload_entry(hass, entry)

    assert unload_res is False
    # Services must NOT be removed
    assert hass.services.has_service(DOMAIN, SERVICE_SET_QUIET_MODE)


@pytest.mark.asyncio
async def test_services_setup_and_unload_idempotency_burst() -> None:
    """Verify burst setup and unload calls remain safe, idempotent, and non-crashing."""
    hass = HomeAssistant()

    # 1. Setup in rapid sequence
    await asyncio.gather(*[async_setup_services(hass) for _ in range(20)])
    assert hass.services.has_service(DOMAIN, SERVICE_SET_QUIET_MODE)

    # 2. Unload in rapid sequence
    await asyncio.gather(*[async_unload_services(hass) for _ in range(20)])
    assert not hass.services.has_service(DOMAIN, SERVICE_SET_QUIET_MODE)

    # 3. Setup again
    await async_setup_services(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_SET_QUIET_MODE)
