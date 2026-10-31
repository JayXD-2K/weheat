"""Unit tests verifying 100% Home Assistant Core upstream alignment for Milestone 1.

Verifies:
- icons.json syntax, schema, and all 47 upstream icons + custom platforms.
- strings.json & translations/en.json completeness, 1:1 parity, and 6 enum state tables.
- cooling_conditions_met translation unit and None native_unit_of_measurement.
- cooling_wait_until logic and HeatPumpLog.cooling_available_from property.
- cop unrounded float precision and display precision.
"""

from __future__ import annotations

import datetime
import json
import re
from pathlib import Path

import pytest

from custom_components.weheat.binary_sensor import (
    BINARY_SENSORS,
    COOLING_START_CONDITION_SENSORS,
)
from custom_components.weheat.const import DISPLAY_PRECISION_COP
from custom_components.weheat.models import HeatPumpLog
from custom_components.weheat.sensor import (
    COOLING_SENSORS,
    DHW_ENERGY_SENSORS,
    DHW_SENSORS,
    ENERGY_SENSORS,
    SENSORS,
)
from tests.conftest import load_fixture

COMPONENT_DIR = Path("custom_components/weheat")
ICONS_PATH = COMPONENT_DIR / "icons.json"
STRINGS_PATH = COMPONENT_DIR / "strings.json"
EN_TRANSLATION_PATH = COMPONENT_DIR / "translations" / "en.json"
SERVICES_PATH = COMPONENT_DIR / "services.yaml"

MDI_REGEX = re.compile(r"^mdi:[a-z0-9-]+$")

# ---------------------------------------------------------------------------
# Official Upstream Census Specifications
# ---------------------------------------------------------------------------

UPSTREAM_BINARY_SENSOR_ICONS: dict[str, str] = {
    "cooling_start_condition_contact_not_blocked": "mdi:snowflake-check",
    "cooling_start_condition_control_method": "mdi:snowflake-check",
    "cooling_start_condition_demand": "mdi:snowflake-check",
    "cooling_start_condition_dtc": "mdi:snowflake-check",
    "cooling_start_condition_exponential_backoff": "mdi:snowflake-check",
    "cooling_start_condition_heat_cool_delay": "mdi:snowflake-check",
    "cooling_start_condition_indoor_unit_connected": "mdi:snowflake-check",
    "cooling_start_condition_inside_temperature": "mdi:snowflake-check",
    "cooling_start_condition_outside_air_temperature": "mdi:snowflake-check",
    "cooling_start_condition_water_temperature": "mdi:snowflake-check",
    "cooling_start_condition_water_to_air": "mdi:snowflake-check",
    "indoor_unit_auxiliary_pump_state": "mdi:pump",
    "indoor_unit_dhw_valve_or_pump_state": "mdi:pump",
    "indoor_unit_electric_heater_state": "mdi:heating-coil",
    "indoor_unit_gas_boiler_state": "mdi:toggle-switch",
    "indoor_unit_water_pump_state": "mdi:pump",
}

UPSTREAM_SENSOR_ICONS: dict[str, str] = {
    "central_heating_flow_volume": "mdi:pump",
    "ch_inlet_temperature": "mdi:radiator",
    "compressor_percentage": "mdi:fan",
    "compressor_rpm": "mdi:fan",
    "cooling_blocked_by": "mdi:snowflake-alert",
    "cooling_conditions_met": "mdi:checkbox-multiple-marked-outline",
    "cooling_pause_reason": "mdi:snowflake-off",
    "cooling_state": "mdi:snowflake-thermometer",
    "cooling_stop_reason": "mdi:snowflake-off",
    "cooling_wait_until": "mdi:timer-sand",
    "cop": "mdi:speedometer",
    "dhw_control_method": "mdi:tune",
    "dhw_flow_volume": "mdi:pump",
    "electricity_used": "mdi:flash",
    "electricity_used_cooling": "mdi:flash",
    "electricity_used_defrost": "mdi:flash",
    "electricity_used_dhw": "mdi:flash",
    "electricity_used_heating": "mdi:flash",
    "electricity_used_indoor_unit": "mdi:flash",
    "energy_output": "mdi:flash",
    "energy_output_cooling": "mdi:snowflake",
    "energy_output_defrost": "mdi:snowflake",
    "energy_output_dhw": "mdi:heat-wave",
    "energy_output_heating": "mdi:heat-wave",
    "heat_pump_state": "mdi:state-machine",
    "last_cooling_time": "mdi:snowflake-check",
    "outside_temperature": "mdi:home-thermometer-outline",
    "power_input": "mdi:lightning-bolt",
    "power_output": "mdi:heat-wave",
    "thermostat_room_temperature": "mdi:home-thermometer",
    "thermostat_room_temperature_setpoint": "mdi:home-thermometer",
}

EXPECTED_ENUM_STATES: dict[str, set[str]] = {
    "heat_pump_state": {
        "cooling",
        "defrosting",
        "dhw",
        "heating",
        "legionella_prevention",
        "manual_control",
        "offline",
        "self_test",
        "standby",
        "updating",
        "water_check",
    },
    "cooling_blocked_by": {
        "contact_not_blocked",
        "control_method",
        "demand",
        "dtc",
        "exponential_backoff",
        "heat_cool_delay",
        "indoor_unit_connected",
        "inside_temperature",
        "none",
        "outside_air_temperature",
        "water_temperature",
        "water_to_air",
    },
    "cooling_pause_reason": {
        "contact_blocked",
        "demand",
        "heat_pump_control",
        "none",
        "outside_colder_than_water_temperature",
        "outside_temperature_too_low",
        "room_temperature_too_low",
        "water_temperature_below_dewpoint",
        "water_temperature_below_setpoint",
    },
    "cooling_state": {
        "active",
        "idle",
        "paused",
        "pausing",
        "standby",
        "standby_run_cp",
        "starting",
        "stopped",
        "stopping",
        "waiting",
        "water_check",
    },
    "cooling_stop_reason": {
        "contact_switch_over",
        "control_method",
        "cooling_control",
        "dtc",
        "heat_pump_control",
        "no_indoor_unit_communication",
        "none",
        "thermostat_disabled",
    },
    "dhw_control_method": {
        "boost",
        "fixed",
        "none",
        "schedule",
        "weheat_intelligence",
    },
}


# ---------------------------------------------------------------------------
# 1. icons.json Validation Tests
# ---------------------------------------------------------------------------


def test_icons_json_file_exists_and_is_valid_json() -> None:
    """Verify icons.json exists and is valid, readable JSON."""
    assert ICONS_PATH.is_file(), f"Expected {ICONS_PATH} to exist"
    with ICONS_PATH.open(encoding="utf-8") as f:
        data = json.load(f)
    assert isinstance(data, dict)
    assert "entity" in data
    assert isinstance(data["entity"], dict)


def test_icons_json_contains_all_47_upstream_icons() -> None:
    """Verify icons.json contains all 16 binary sensor and 31 sensor icons from Core."""
    with ICONS_PATH.open(encoding="utf-8") as f:
        data = json.load(f)

    entities = data["entity"]
    assert "binary_sensor" in entities
    assert "sensor" in entities

    # 1. Verify 16 Binary Sensor Icons
    bs_icons = entities["binary_sensor"]
    assert len(UPSTREAM_BINARY_SENSOR_ICONS) == 16
    for key, expected_icon in UPSTREAM_BINARY_SENSOR_ICONS.items():
        assert key in bs_icons, f"Missing binary sensor icon for '{key}'"
        assert bs_icons[key].get("default") == expected_icon
        assert MDI_REGEX.match(expected_icon), f"Invalid MDI format: {expected_icon}"

    # 2. Verify 31 Sensor Icons
    s_icons = entities["sensor"]
    assert len(UPSTREAM_SENSOR_ICONS) == 31
    for key, expected_icon in UPSTREAM_SENSOR_ICONS.items():
        assert key in s_icons, f"Missing sensor icon for '{key}'"
        assert s_icons[key].get("default") == expected_icon
        assert MDI_REGEX.match(expected_icon), f"Invalid MDI format: {expected_icon}"


def test_icons_json_preserves_custom_platforms() -> None:
    """Verify icons.json maintains custom entity icons (select, switch, time, button, number)."""
    with ICONS_PATH.open(encoding="utf-8") as f:
        data = json.load(f)

    entities = data["entity"]
    for custom_platform in ("select", "switch", "time", "button", "number"):
        assert custom_platform in entities, (
            f"Missing platform '{custom_platform}' in icons.json"
        )
        assert len(entities[custom_platform]) > 0


# ---------------------------------------------------------------------------
# 2. strings.json and en.json Parity & Completeness Tests
# ---------------------------------------------------------------------------


def test_strings_and_en_json_validity_and_structural_parity() -> None:
    """Verify strings.json and en.json exist, are valid JSON, and share identical entity keys."""
    assert STRINGS_PATH.is_file(), f"Missing {STRINGS_PATH}"
    assert EN_TRANSLATION_PATH.is_file(), f"Missing {EN_TRANSLATION_PATH}"

    with STRINGS_PATH.open(encoding="utf-8") as f:
        strings_data = json.load(f)
    with EN_TRANSLATION_PATH.open(encoding="utf-8") as f:
        en_data = json.load(f)

    assert "entity" in strings_data
    assert "entity" in en_data

    # Parity check on entity platform keys
    assert set(strings_data["entity"].keys()) == set(en_data["entity"].keys())
    for platform in strings_data["entity"]:
        strings_keys = set(strings_data["entity"][platform].keys())
        en_keys = set(en_data["entity"][platform].keys())
        assert strings_keys == en_keys, (
            f"Key mismatch in platform '{platform}' between strings and en.json"
        )


def test_strings_contains_all_39_sensors_and_16_binary_sensors() -> None:
    """Verify strings.json contains translation entries for all 55 upstream entities."""
    with STRINGS_PATH.open(encoding="utf-8") as f:
        data = json.load(f)

    sensor_strings = data["entity"]["sensor"]
    binary_sensor_strings = data["entity"]["binary_sensor"]

    # All 16 binary sensors
    all_bs_descs = list(BINARY_SENSORS) + list(COOLING_START_CONDITION_SENSORS)
    assert len(all_bs_descs) == 16
    for desc in all_bs_descs:
        assert desc.key in binary_sensor_strings, (
            f"Binary sensor '{desc.key}' missing from strings.json"
        )
        assert "name" in binary_sensor_strings[desc.key]

    # All 39 sensors
    all_s_descs = (
        list(SENSORS)
        + list(DHW_SENSORS)
        + list(COOLING_SENSORS)
        + list(ENERGY_SENSORS)
        + list(DHW_ENERGY_SENSORS)
    )
    assert len(all_s_descs) == 39
    for desc in all_s_descs:
        assert desc.key in sensor_strings, (
            f"Sensor '{desc.key}' missing from strings.json"
        )
        # heat_pump_state has name=None upstream; all other sensors have name
        if desc.key == "heat_pump_state":
            assert "state" in sensor_strings[desc.key]
        else:
            assert "name" in sensor_strings[desc.key]


def test_strings_enum_states_completeness() -> None:
    """Verify all 6 state enum dictionaries contain exact expected localized state keys."""
    with STRINGS_PATH.open(encoding="utf-8") as f:
        data = json.load(f)

    sensors = data["entity"]["sensor"]
    for entity_key, expected_states in EXPECTED_ENUM_STATES.items():
        assert entity_key in sensors, f"Missing '{entity_key}' in entity.sensor"
        assert "state" in sensors[entity_key], (
            f"Missing 'state' block in '{entity_key}'"
        )
        actual_states = set(sensors[entity_key]["state"].keys())
        assert actual_states == expected_states, (
            f"State enum mismatch for '{entity_key}': diff={actual_states ^ expected_states}"
        )


# ---------------------------------------------------------------------------
# 3. Sensor Logic Alignment: cooling_conditions_met
# ---------------------------------------------------------------------------


def test_cooling_conditions_met_spec() -> None:
    """Verify cooling_conditions_met has native_unit_of_measurement=None and translation unit 'of 9'."""
    desc_map = {desc.key: desc for desc in COOLING_SENSORS}
    desc = desc_map["cooling_conditions_met"]

    # 1. Native unit must be None in python descriptor (relying on translations)
    assert desc.native_unit_of_measurement is None, (
        "cooling_conditions_met native_unit_of_measurement must be None, not hardcoded in python!"
    )

    # 2. Unit of measurement must be defined in strings.json and en.json
    with STRINGS_PATH.open(encoding="utf-8") as f:
        strings_data = json.load(f)
    assert (
        strings_data["entity"]["sensor"]["cooling_conditions_met"][
            "unit_of_measurement"
        ]
        == "of 9"
    )

    with EN_TRANSLATION_PATH.open(encoding="utf-8") as f:
        en_data = json.load(f)
    assert (
        en_data["entity"]["sensor"]["cooling_conditions_met"]["unit_of_measurement"]
        == "of 9"
    )


# ---------------------------------------------------------------------------
# 4. Sensor Logic Alignment: cooling_wait_until & cooling_available_from
# ---------------------------------------------------------------------------


def test_heat_pump_log_cooling_available_from_property() -> None:
    """Verify HeatPumpLog.cooling_available_from calculates correct future availability datetime."""
    log = HeatPumpLog(heat_pump_id="hp-1")

    # 1. None last_cooling_time -> None
    log.last_cooling_time = None
    assert log.cooling_available_from is None

    # 2. Corrupt string -> safely returns None without throwing
    log.last_cooling_time = "corrupt-non-iso-time"
    assert log.cooling_available_from is None

    # 3. ISO timestamp with zero backoff
    log.last_cooling_time = "2026-10-30T07:00:00Z"
    log.cooling_exponential_backoff = 0
    expected_dt = datetime.datetime(2026, 10, 30, 7, 0, tzinfo=datetime.UTC)
    assert log.cooling_available_from == expected_dt

    # 4. ISO timestamp with 15 minutes backoff
    log.cooling_exponential_backoff = 15
    expected_backoff_dt = datetime.datetime(2026, 10, 30, 7, 15, tzinfo=datetime.UTC)
    assert log.cooling_available_from == expected_backoff_dt


def test_cooling_wait_until_value_fn() -> None:
    """Verify cooling_wait_until returns status.cooling_available_from when exponential_backoff is false."""
    desc_map = {desc.key: desc for desc in COOLING_SENSORS}
    desc = desc_map["cooling_wait_until"]

    log = HeatPumpLog(heat_pump_id="hp-1")
    log.last_cooling_time = "2026-10-30T07:00:00Z"
    log.cooling_exponential_backoff = 10
    expected_dt = datetime.datetime(2026, 10, 30, 7, 10, tzinfo=datetime.UTC)

    # When cooling_start_conditions is None -> returns None
    log.cooling_start_conditions_raw = None
    assert desc.value_fn(log) is None

    # When exponential_backoff bit (512) is set -> returns None
    log.cooling_start_conditions_raw = 512
    assert desc.value_fn(log) is None

    # When exponential_backoff bit is NOT set -> returns cooling_available_from (future datetime)
    log.cooling_start_conditions_raw = 0
    assert desc.value_fn(log) == expected_dt


# ---------------------------------------------------------------------------
# 5. Sensor Logic Alignment: cop Unrounded Float Precision
# ---------------------------------------------------------------------------


def test_cop_unrounded_float_precision() -> None:
    """Verify cop returns raw unrounded float matching upstream Home Assistant Core."""
    desc_map = {desc.key: desc for desc in SENSORS}
    cop_desc = desc_map["cop"]

    # 1. Suggested display precision must be 1
    assert cop_desc.suggested_display_precision == DISPLAY_PRECISION_COP

    # 2. Check unrounded precision with latest_logs fixture values (4700 / 1120)
    data = load_fixture("latest_logs.json")
    log = HeatPumpLog.from_api_dict(data)

    unrounded_cop = 4700.0 / 1120.0  # 4.196428571428571
    result = cop_desc.value_fn(log)

    assert result is not None
    assert isinstance(result, float)
    assert result == pytest.approx(unrounded_cop, rel=1e-9)
    # Ensure it was NOT prematurely rounded to 2 decimals (4.2)
    assert result != 4.2


# ---------------------------------------------------------------------------
# 6. Quiet Mode & Night Limit Expanded Explanations in Metadata
# ---------------------------------------------------------------------------


def test_quiet_mode_and_night_limit_expanded_explanations() -> None:
    """Verify strings.json, en.json, and services.yaml contain expanded explanations for presets.

    Asserts that:
    - Daytime presets explain 0 (Extra savings / Performance / Maximum power) to 4 (Extra quiet / Silent 4).
    - Night limits explain 0 (Off) through 1..7 (Silent 1..Silent 7 night limits).
    - All three metadata files (strings.json, translations/en.json, services.yaml) provide clear documentation.
    """
    assert SERVICES_PATH.is_file(), f"Missing {SERVICES_PATH}"
    services_text = SERVICES_PATH.read_text(encoding="utf-8")

    # 1. Check services.yaml contains daytime preset explanations (0..4) and night limits (0, 1..7)
    assert "Mode 5 - Max capacity (0: Extra savings / Maximum power)" in services_text
    assert (
        "Mode 4 - Standard (1: Silent 1 - Nominal balanced capacity)" in services_text
    )
    assert "Mode 3 - Reduced (2: Silent 2 - Reduced power & noise)" in services_text
    assert (
        "Mode 2 - Low noise (3: Silent 3 - Strict residential noise limits)"
        in services_text
    )
    assert (
        "Mode 1 - Minimum noise (4: Silent 4 - Extra quiet / lowest power)"
        in services_text
    )

    assert "Off (0: Disable scheduled quiet hours)" in services_text
    assert (
        "Normal (1: Silent 1 - Level 1 night limit - Highest night power)"
        in services_text
    )
    assert (
        "Background chatter (2: Silent 2 - Level 2 night limit - Moderate night noise cap)"
        in services_text
    )
    assert (
        "Light rain (3: Silent 3 - Level 3 night limit - High night noise cap)"
        in services_text
    )
    assert (
        "Library (4: Silent 4 - Level 4 night limit - Very quiet night limit)"
        in services_text
    )
    assert (
        "Whisper (6: Silent 6 - Level 6 night limit - Ultra quiet night limit)"
        in services_text
    )
    assert (
        "Minimum (7: Silent 7 - Level 7 night limit - Maximum quietness)"
        in services_text
    )

    # 2. Check strings.json and translations/en.json
    for path in (STRINGS_PATH, EN_TRANSLATION_PATH):
        with path.open(encoding="utf-8") as f:
            data = json.load(f)

        services_dict = data.get("services", {})
        assert "set_quiet_mode" in services_dict
        mode_field = services_dict["set_quiet_mode"]["fields"]["mode"]
        mode_desc = mode_field["description"]

        # Check daytime explanations (0 = Extra savings, 1..3, 4 = Extra quiet)
        assert "Extra savings" in mode_desc
        assert "Maximum power" in mode_desc
        assert "unrestricted compressor/fan speed" in mode_desc
        assert "Silent 1" in mode_desc and "slight noise reduction" in mode_desc
        assert "Silent 2" in mode_desc and "moderate noise reduction" in mode_desc
        assert "Silent 3" in mode_desc and "high noise reduction" in mode_desc
        assert "Silent 4" in mode_desc and "Extra quiet" in mode_desc

        assert "set_silent_schedule" in services_dict
        preset_field = services_dict["set_silent_schedule"]["fields"]["preset"]
        preset_desc = preset_field["description"]

        # Check night limit explanations (0 = Off, 1..7)
        assert "Off" in preset_desc and "0: schedule disabled" in preset_desc
        assert "Silent 1" in preset_desc and "1: Level 1 night limit" in preset_desc
        assert "Silent 2" in preset_desc
        assert "Silent 3" in preset_desc
        assert "Silent 4" in preset_desc
        assert "Silent 6" in preset_desc
        assert "Silent 7" in preset_desc and "7: extra quiet night limit" in preset_desc
