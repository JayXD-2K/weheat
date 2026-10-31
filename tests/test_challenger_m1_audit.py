"""Empirical Challenger 1 Audit Suite for Milestone 1 (Upstream Core Alignment).

Independent verification of:
1. Strict JSON metadata integrity with duplicate key detection for:
   - icons.json
   - strings.json
   - translations/en.json
2. 100% translation key coverage for all sensors and binary sensors.
3. 100% upstream icon matching and MDI format validation.
4. Temperature sensor attributes: device_class, unit of measurement, state_class, and icon mapping.
5. Fuzzing and stress testing of worker's modified logic:
   - cop unrounded precision and suggested_display_precision
   - cooling_conditions_met translation unit and boundary conditions
   - cooling_available_from and cooling_wait_until edge cases and exceptions
"""

from __future__ import annotations

import datetime
import json
import re
from pathlib import Path
from typing import Any

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfTemperature

from custom_components.weheat.binary_sensor import (
    BINARY_SENSORS,
    COOLING_START_CONDITION_SENSORS,
)
from custom_components.weheat.const import (
    DISPLAY_PRECISION_COP,
    DISPLAY_PRECISION_WATER_TEMP,
)
from custom_components.weheat.models import (
    COOLING_START_CONDITION_BITS,
    CoolingPauseReason,
    CoolingStatus,
    CoolingStopReason,
    DhwControlMethod,
    HeatPumpLog,
    HeatPumpState,
)
from custom_components.weheat.sensor import (
    COOLING_SENSORS,
    DHW_ENERGY_SENSORS,
    DHW_SENSORS,
    ENERGY_SENSORS,
    SENSORS,
)

COMPONENT_DIR = Path("custom_components/weheat")
ICONS_PATH = COMPONENT_DIR / "icons.json"
STRINGS_PATH = COMPONENT_DIR / "strings.json"
EN_PATH = COMPONENT_DIR / "translations" / "en.json"
UPSTREAM_DIR = Path(".agents/teamwork/survey_upstream_spec/upstream_core")

MDI_REGEX = re.compile(r"^mdi:[a-z0-9-]+$")


def _strict_json_loader(raw_text: str) -> dict[str, Any]:
    """Parse JSON with strict duplicate key rejection."""

    def _reject_duplicates(ordered_pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        d: dict[str, Any] = {}
        for k, v in ordered_pairs:
            if k in d:
                raise ValueError(f"Duplicate key detected in JSON: '{k}'")
            d[k] = v
        return d

    return json.loads(raw_text, object_pairs_hook=_reject_duplicates)


# ============================================================================
# 1. Metadata Integrity & Duplicate Key Stress Tests
# ============================================================================


def test_metadata_files_exist_and_utf8():
    """Verify all metadata files exist and are valid UTF-8 without decoding errors."""
    for path in (ICONS_PATH, STRINGS_PATH, EN_PATH):
        assert path.exists(), f"File does not exist: {path}"
        content = path.read_text(encoding="utf-8")
        assert len(content) > 0, f"File is empty: {path}"


def test_metadata_strict_duplicate_key_check():
    """Verify icons.json, strings.json, and en.json contain NO duplicate keys at any depth."""
    for path in (ICONS_PATH, STRINGS_PATH, EN_PATH):
        raw = path.read_text(encoding="utf-8")
        try:
            data = _strict_json_loader(raw)
            assert isinstance(data, dict)
        except ValueError as err:
            pytest.fail(f"Duplicate key failure in {path}: {err}")


def test_strings_and_en_json_structural_parity():
    """Verify translations/en.json and strings.json have identical keys and values."""
    strings_data = _strict_json_loader(STRINGS_PATH.read_text(encoding="utf-8"))
    en_data = _strict_json_loader(EN_PATH.read_text(encoding="utf-8"))

    def _compare_dicts(path_str: str, d1: dict[str, Any], d2: dict[str, Any]):
        keys1 = set(d1.keys())
        keys2 = set(d2.keys())
        assert keys1 == keys2, (
            f"Key mismatch at {path_str}: d1 extra: {keys1 - keys2}, d2 extra: {keys2 - keys1}"
        )
        for k in keys1:
            v1 = d1[k]
            v2 = d2[k]
            current_path = f"{path_str}.{k}"
            if isinstance(v1, dict):
                assert isinstance(v2, dict), (
                    f"Type mismatch at {current_path}: {type(v1)} vs {type(v2)}"
                )
                _compare_dicts(current_path, v1, v2)
            else:
                assert v1 == v2, f"Value mismatch at {current_path}: '{v1}' != '{v2}'"

    _compare_dicts("root", strings_data, en_data)


# ============================================================================
# 2. Translation Keys & State Enums Completeness
# ============================================================================


def test_all_sensor_entities_bidirectional_parity():
    """Assert 100% bidirectional parity between code sensors and translations (no unmapped or missing keys)."""
    strings_data = _strict_json_loader(STRINGS_PATH.read_text(encoding="utf-8"))
    sensor_strings = strings_data.get("entity", {}).get("sensor", {})

    all_sensors = (
        *SENSORS,
        *COOLING_SENSORS,
        *DHW_SENSORS,
        *DHW_ENERGY_SENSORS,
        *ENERGY_SENSORS,
    )
    code_keys = {desc.translation_key for desc in all_sensors}
    str_keys = set(sensor_strings.keys())

    assert len(code_keys) == 39
    assert code_keys == str_keys, (
        f"Sensor key diff: missing in strings={code_keys - str_keys}, extra in strings={str_keys - code_keys}"
    )

    for desc in all_sensors:
        tk = desc.translation_key
        assert tk is not None, f"Sensor {desc.key} has no translation_key"
        assert "name" in sensor_strings[tk] or "state" in sensor_strings[tk], (
            f"No name/state in translation for sensor: {tk}"
        )


def test_all_binary_sensor_entities_bidirectional_parity():
    """Assert 100% bidirectional parity between code binary sensors and translations (no unmapped or missing keys)."""
    strings_data = _strict_json_loader(STRINGS_PATH.read_text(encoding="utf-8"))
    bs_strings = strings_data.get("entity", {}).get("binary_sensor", {})

    all_bs = (*BINARY_SENSORS, *COOLING_START_CONDITION_SENSORS)
    code_keys = {desc.translation_key for desc in all_bs}
    str_keys = set(bs_strings.keys())

    assert len(code_keys) == 16
    assert code_keys == str_keys, (
        f"Binary sensor key diff: missing in strings={code_keys - str_keys}, extra in strings={str_keys - code_keys}"
    )

    for desc in all_bs:
        tk = desc.translation_key
        assert tk is not None, f"Binary sensor {desc.key} has no translation_key"
        assert "name" in bs_strings[tk], f"Missing 'name' for binary sensor: {tk}"
        assert len(bs_strings[tk]["name"]) > 0


def test_icons_json_entity_mapping_parity():
    """Assert no unmapped sensor or binary_sensor icons exist in icons.json."""
    loc_icons = _strict_json_loader(ICONS_PATH.read_text(encoding="utf-8"))
    all_sensors = (
        *SENSORS,
        *COOLING_SENSORS,
        *DHW_SENSORS,
        *DHW_ENERGY_SENSORS,
        *ENERGY_SENSORS,
    )
    all_bs = (*BINARY_SENSORS, *COOLING_START_CONDITION_SENSORS)

    sensor_code_keys = {desc.translation_key for desc in all_sensors}
    bs_code_keys = {desc.translation_key for desc in all_bs}

    icon_sensor_keys = set(loc_icons.get("entity", {}).get("sensor", {}).keys())
    icon_bs_keys = set(loc_icons.get("entity", {}).get("binary_sensor", {}).keys())

    # Every icon in icons.json must map to a real sensor/binary_sensor in code
    unmapped_sensor_icons = icon_sensor_keys - sensor_code_keys
    unmapped_bs_icons = icon_bs_keys - bs_code_keys
    assert not unmapped_sensor_icons, (
        f"Unmapped sensor icons found in icons.json: {unmapped_sensor_icons}"
    )
    assert not unmapped_bs_icons, (
        f"Unmapped binary sensor icons found in icons.json: {unmapped_bs_icons}"
    )

    # Binary sensors have 100% icon coverage in upstream core
    assert icon_bs_keys == bs_code_keys


def test_enum_state_translations_exhaustiveness():
    """Assert all enum values for stateful sensors are mapped in translations."""
    strings_data = _strict_json_loader(STRINGS_PATH.read_text(encoding="utf-8"))
    sensor_strings = strings_data.get("entity", {}).get("sensor", {})
    up_strings = _strict_json_loader(
        (UPSTREAM_DIR / "strings.json").read_text(encoding="utf-8")
    )
    up_sensor_strings = up_strings["entity"]["sensor"]

    # 1. heat_pump_state (with [%key:common::state::standby%] resolved to "Standby" for HACS)
    hp_states = sensor_strings["heat_pump_state"]["state"]
    expected_hp_states = {
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
    }
    assert set(hp_states.keys()) == expected_hp_states
    expected_up_hp = {
        **up_sensor_strings["heat_pump_state"]["state"],
        "standby": "Standby",
    }
    assert hp_states == expected_up_hp

    # 2. cooling_blocked_by
    cb_states = sensor_strings["cooling_blocked_by"]["state"]
    expected_cb_states = {"none", *COOLING_START_CONDITION_BITS}
    assert set(cb_states.keys()) == expected_cb_states
    assert cb_states == up_sensor_strings["cooling_blocked_by"]["state"]

    # 3. cooling_pause_reason
    cpr_states = sensor_strings["cooling_pause_reason"]["state"]
    expected_cpr_states = {r.name.lower() for r in CoolingPauseReason}
    assert set(cpr_states.keys()) == expected_cpr_states
    assert cpr_states == up_sensor_strings["cooling_pause_reason"]["state"]

    # 4. cooling_state (Must contain all CoolingStatus members and match upstream Core with Standby resolved)
    cs_states = sensor_strings["cooling_state"]["state"]
    assert {s.name.lower() for s in CoolingStatus}.issubset(set(cs_states.keys()))
    expected_up_cs = {
        **up_sensor_strings["cooling_state"]["state"],
        "standby": "Standby",
    }
    assert cs_states == expected_up_cs

    # 5. cooling_stop_reason
    csr_states = sensor_strings["cooling_stop_reason"]["state"]
    expected_csr_states = {r.name.lower() for r in CoolingStopReason}
    assert set(csr_states.keys()) == expected_csr_states
    assert csr_states == up_sensor_strings["cooling_stop_reason"]["state"]

    # 6. dhw_control_method
    dcm_states = sensor_strings["dhw_control_method"]["state"]
    expected_dcm_states = {m.name.lower() for m in DhwControlMethod}
    assert set(dcm_states.keys()) == expected_dcm_states
    assert dcm_states == up_sensor_strings["dhw_control_method"]["state"]


# ============================================================================
# 3. Upstream Icons Parity & MDI Format
# ============================================================================


def test_upstream_icons_match_core_exactly():
    """Assert all 47 upstream icons in upstream_core/icons.json match local icons.json exactly."""
    up_icons = _strict_json_loader(
        (UPSTREAM_DIR / "icons.json").read_text(encoding="utf-8")
    )
    loc_icons = _strict_json_loader(ICONS_PATH.read_text(encoding="utf-8"))

    # Binary sensors
    for k, v in up_icons["entity"]["binary_sensor"].items():
        assert k in loc_icons["entity"]["binary_sensor"], (
            f"Missing binary sensor icon: {k}"
        )
        assert loc_icons["entity"]["binary_sensor"][k]["default"] == v["default"]

    # Sensors
    for k, v in up_icons["entity"]["sensor"].items():
        assert k in loc_icons["entity"]["sensor"], f"Missing sensor icon: {k}"
        assert loc_icons["entity"]["sensor"][k]["default"] == v["default"]


def test_all_icons_conform_to_mdi_regex():
    """Assert all icon strings across all platforms conform to 'mdi:[a-z0-9-]+'."""
    loc_icons = _strict_json_loader(ICONS_PATH.read_text(encoding="utf-8"))

    for platform, entities in loc_icons.get("entity", {}).items():
        for ent_name, icon_dict in entities.items():
            assert "default" in icon_dict, (
                f"Icon missing 'default' in {platform}.{ent_name}"
            )
            icon_str = icon_dict["default"]
            assert MDI_REGEX.match(icon_str), (
                f"Invalid MDI icon format '{icon_str}' in {platform}.{ent_name}"
            )


# ============================================================================
# 4. Temperature Sensors Specification Verification
# ============================================================================


def test_temperature_sensors_device_class_unit_and_icons():
    """Verify all temperature sensors display proper device_class, unit, and icon."""
    all_sensors = (
        *SENSORS,
        *COOLING_SENSORS,
        *DHW_SENSORS,
        *DHW_ENERGY_SENSORS,
        *ENERGY_SENSORS,
    )
    loc_icons = _strict_json_loader(ICONS_PATH.read_text(encoding="utf-8"))
    sensor_icons = loc_icons["entity"]["sensor"]

    temp_sensors = [
        s for s in all_sensors if s.device_class == SensorDeviceClass.TEMPERATURE
    ]

    # Exactly 11 temperature sensors defined in the integration
    expected_temp_keys = {
        "water_inlet_temperature",
        "water_outlet_temperature",
        "ch_inlet_temperature",
        "outside_temperature",
        "air_outlet_temperature",
        "thermostat_water_setpoint",
        "thermostat_room_temperature",
        "thermostat_room_temperature_setpoint",
        "dhw_top_temperature",
        "dhw_bottom_temperature",
        "dhw_target_temperature",
    }
    actual_temp_keys = {s.key for s in temp_sensors}
    assert actual_temp_keys == expected_temp_keys

    for s in temp_sensors:
        assert s.device_class == SensorDeviceClass.TEMPERATURE
        assert s.native_unit_of_measurement == UnitOfTemperature.CELSIUS
        assert s.state_class == SensorStateClass.MEASUREMENT
        assert s.suggested_display_precision == DISPLAY_PRECISION_WATER_TEMP

    # Specific icon requirements:
    # 1. Radiator icon for central heating inlet temperature
    assert sensor_icons.get("ch_inlet_temperature", {}).get("default") == "mdi:radiator"

    # 2. Room/home thermometer icons
    assert (
        sensor_icons.get("thermostat_room_temperature", {}).get("default")
        == "mdi:home-thermometer"
    )
    assert (
        sensor_icons.get("thermostat_room_temperature_setpoint", {}).get("default")
        == "mdi:home-thermometer"
    )
    assert (
        sensor_icons.get("outside_temperature", {}).get("default")
        == "mdi:home-thermometer-outline"
    )


# ============================================================================
# 5. Stress Testing of Worker Changes: cop, cooling_conditions_met, cooling_wait_until
# ============================================================================


def test_cop_stress_and_precision():
    """Stress test COP sensor: unrounded float precision, cooling mode, and boundaries."""
    cop_desc = next(s for s in SENSORS if s.key == "cop")

    assert cop_desc.native_unit_of_measurement is None
    assert cop_desc.suggested_display_precision == DISPLAY_PRECISION_COP
    assert cop_desc.state_class == SensorStateClass.MEASUREMENT

    # Boundary tests
    log = HeatPumpLog(
        heat_pump_id="hp1", p_compressor_in=1000.0, p_compressor_out=4321.0
    )
    # 4321 / 1000 = 4.321
    val = cop_desc.value_fn(log)
    assert val == 4.321
    assert val != 4.3  # Must NOT be pre-rounded to 1 or 2 decimals

    # Negative during cooling
    log_cooling = HeatPumpLog(
        heat_pump_id="hp1",
        state=HeatPumpState.COOLING,
        p_compressor_in=1000.0,
        p_compressor_out=3500.0,
    )
    assert cop_desc.value_fn(log_cooling) == -3.5

    # Power input <= 0
    log_zero_in = HeatPumpLog(
        heat_pump_id="hp1", p_compressor_in=0.0, p_compressor_out=100.0
    )
    assert cop_desc.value_fn(log_zero_in) == 0.0

    # None power
    log_none = HeatPumpLog(
        heat_pump_id="hp1", p_compressor_in=None, p_compressor_out=None
    )
    assert cop_desc.value_fn(log_none) is None


def test_cooling_conditions_met_stress():
    """Stress test cooling_conditions_met: translation unit, bitmask combinations."""
    desc = next(s for s in COOLING_SENSORS if s.key == "cooling_conditions_met")

    assert desc.native_unit_of_measurement is None
    strings_data = _strict_json_loader(STRINGS_PATH.read_text(encoding="utf-8"))
    assert (
        strings_data["entity"]["sensor"]["cooling_conditions_met"][
            "unit_of_measurement"
        ]
        == "of 9"
    )

    # When actively cooling (cooling_status in 1, 2, 3 or state == COOLING), returns None
    for status_code in (1, 2, 3):
        log = HeatPumpLog(
            heat_pump_id="hp1",
            cooling_status=status_code,
            cooling_start_conditions_raw=0b11111111111,
        )
        assert desc.value_fn(log) is None

    log_cool_state = HeatPumpLog(
        heat_pump_id="hp1",
        state=HeatPumpState.COOLING,
        cooling_start_conditions_raw=0b11111111111,
    )
    assert desc.value_fn(log_cool_state) is None

    # When cooling_start_conditions is None
    log_no_cond = HeatPumpLog(heat_pump_id="hp1", cooling_start_conditions_raw=None)
    assert desc.value_fn(log_no_cond) is None

    # When all 11 conditions are met:
    # 2 conditions are not counted (control_method, contact_not_blocked).
    # Total met counted must be 9!
    log_all_met = HeatPumpLog(
        heat_pump_id="hp1", cooling_status=0, cooling_start_conditions_raw=0b11111111111
    )
    assert desc.value_fn(log_all_met) == 9

    # When none are met:
    log_none_met = HeatPumpLog(
        heat_pump_id="hp1", cooling_status=0, cooling_start_conditions_raw=0
    )
    assert desc.value_fn(log_none_met) == 0

    # Only uncounted conditions are met
    # COOLING_CONDITIONS_NOT_COUNTED = ("control_method", "contact_not_blocked")
    uncounted_mask = (
        COOLING_START_CONDITION_BITS["control_method"]
        | COOLING_START_CONDITION_BITS["contact_not_blocked"]
    )
    log_only_uncounted = HeatPumpLog(
        heat_pump_id="hp1",
        cooling_status=0,
        cooling_start_conditions_raw=uncounted_mask,
    )
    assert desc.value_fn(log_only_uncounted) == 0


def test_cooling_available_from_and_wait_until_adversarial():
    """Stress test HeatPumpLog.cooling_available_from and cooling_wait_until with malformed inputs."""
    wait_desc = next(s for s in COOLING_SENSORS if s.key == "cooling_wait_until")

    # 1. last_cooling_time is None
    log1 = HeatPumpLog(heat_pump_id="hp1", last_cooling_time=None)
    assert log1.cooling_available_from is None
    assert wait_desc.value_fn(log1) is None

    # 2. last_cooling_time is empty string
    log2 = HeatPumpLog(heat_pump_id="hp1", last_cooling_time="")
    assert log2.cooling_available_from is None
    assert wait_desc.value_fn(log2) is None

    # 3. last_cooling_time is malformed string
    log3 = HeatPumpLog(heat_pump_id="hp1", last_cooling_time="not-a-valid-date-time")
    assert log3.cooling_available_from is None
    assert wait_desc.value_fn(log3) is None

    # 4. last_cooling_time ISO with Z
    log4 = HeatPumpLog(
        heat_pump_id="hp1",
        last_cooling_time="2026-10-31T08:00:00Z",
        cooling_exponential_backoff=15,
        cooling_start_conditions_raw=0,  # exponential_backoff bit 512 is 0 (False)
    )
    expected4 = datetime.datetime(2026, 10, 31, 8, 15, tzinfo=datetime.UTC)
    assert log4.cooling_available_from == expected4
    assert wait_desc.value_fn(log4) == expected4

    # 5. cooling_exponential_backoff is None (defaults to +0 minutes)
    log5 = HeatPumpLog(
        heat_pump_id="hp1",
        last_cooling_time="2026-10-31T08:00:00Z",
        cooling_exponential_backoff=None,
        cooling_start_conditions_raw=0,
    )
    expected5 = datetime.datetime(2026, 10, 31, 8, 0, tzinfo=datetime.UTC)
    assert log5.cooling_available_from == expected5
    assert wait_desc.value_fn(log5) == expected5

    # 6. exponential_backoff active in cooling_start_conditions (bit 512 = 1)
    # Should return None because wait_until is masked when exponential_backoff is True
    exp_mask = COOLING_START_CONDITION_BITS["exponential_backoff"]
    log6 = HeatPumpLog(
        heat_pump_id="hp1",
        last_cooling_time="2026-10-31T08:00:00Z",
        cooling_exponential_backoff=15,
        cooling_start_conditions_raw=exp_mask,
    )
    assert log6.cooling_available_from == expected4
    assert wait_desc.value_fn(log6) is None

    # 7. naive datetime object passed directly
    naive_dt = datetime.datetime(2026, 10, 31, 12, 0, 0)
    log7 = HeatPumpLog(
        heat_pump_id="hp1", last_cooling_time=naive_dt, cooling_exponential_backoff=30
    )
    assert log7.cooling_available_from == datetime.datetime(
        2026, 10, 31, 12, 30, 0, tzinfo=datetime.UTC
    )
