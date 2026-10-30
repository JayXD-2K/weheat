"""Unit tests for the Weheat binary sensor platform and entity specifications."""

from __future__ import annotations

import pytest
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.const import EntityCategory

from custom_components.weheat.binary_sensor import (
    BINARY_SENSORS,
    COOLING_START_CONDITION_SENSORS,
    WeheatHeatPumpBinarySensor,
)
from custom_components.weheat.models import (
    COOLING_START_CONDITION_BITS,
    HeatPumpInfo,
    HeatPumpLog,
)
from tests.conftest import load_fixture


@pytest.fixture
def mock_hp_info() -> HeatPumpInfo:
    """Provide a mock HeatPumpInfo instance."""
    data = load_fixture("heat_pumps.json")["data"][0]
    return HeatPumpInfo.from_api_dict(data)


@pytest.fixture
def mock_log() -> HeatPumpLog:
    """Provide a mock HeatPumpLog instance."""
    data = load_fixture("latest_logs.json")
    return HeatPumpLog.from_api_dict(data)


# ---------------------------------------------------------------------------
# 1. Binary Sensor Catalog & Attributes
# ---------------------------------------------------------------------------


def test_binary_sensor_catalog_counts() -> None:
    """Verify exact counts of binary sensors across actuator and cooling condition groups."""
    assert len(BINARY_SENSORS) == 5, "Expected 5 indoor unit actuator binary sensors"
    assert len(COOLING_START_CONDITION_SENSORS) == 11, (
        "Expected 11 cooling condition binary sensors"
    )
    assert len(BINARY_SENSORS) + len(COOLING_START_CONDITION_SENSORS) == 16


def test_binary_sensor_keys_are_unique() -> None:
    """Verify that all 16 binary sensor keys are distinct."""
    all_keys = [
        desc.key
        for group in (BINARY_SENSORS, COOLING_START_CONDITION_SENSORS)
        for desc in group
    ]
    assert len(all_keys) == 16
    assert len(set(all_keys)) == 16


def test_actuator_binary_sensor_device_classes() -> None:
    """Verify device classes for indoor unit actuators."""
    desc_map = {desc.key: desc for desc in BINARY_SENSORS}

    # Water pumps and valves use RUNNING device class
    assert (
        desc_map["indoor_unit_water_pump_state"].device_class
        == BinarySensorDeviceClass.RUNNING
    )
    assert (
        desc_map["indoor_unit_auxiliary_pump_state"].device_class
        == BinarySensorDeviceClass.RUNNING
    )
    assert (
        desc_map["indoor_unit_dhw_valve_or_pump_state"].device_class
        == BinarySensorDeviceClass.RUNNING
    )
    assert (
        desc_map["indoor_unit_electric_heater_state"].device_class
        == BinarySensorDeviceClass.RUNNING
    )

    # Gas boiler has no device class (standard boolean)
    assert desc_map["indoor_unit_gas_boiler_state"].device_class is None


def test_cooling_condition_binary_sensors_metadata() -> None:
    """Verify cooling start condition diagnostic binary sensors are disabled by default."""
    for desc in COOLING_START_CONDITION_SENSORS:
        assert desc.entity_category == EntityCategory.DIAGNOSTIC
        assert desc.entity_registry_enabled_default is False
        assert desc.key.startswith("cooling_start_condition_")


# ---------------------------------------------------------------------------
# 2. Values & Decodes Against Mock Fixtures
# ---------------------------------------------------------------------------


def test_actuator_binary_sensor_values(mock_log: HeatPumpLog) -> None:
    """Verify actuator binary sensors resolve expected boolean states from latest_logs.json."""
    desc_map = {desc.key: desc for desc in BINARY_SENSORS}

    # In latest_logs.json, controlBridgeStatusDecodedWaterPump is true, others are false
    assert desc_map["indoor_unit_water_pump_state"].value_fn(mock_log) is True
    assert desc_map["indoor_unit_auxiliary_pump_state"].value_fn(mock_log) is False
    assert desc_map["indoor_unit_dhw_valve_or_pump_state"].value_fn(mock_log) is False
    assert desc_map["indoor_unit_gas_boiler_state"].value_fn(mock_log) is False
    assert desc_map["indoor_unit_electric_heater_state"].value_fn(mock_log) is False


def test_cooling_condition_bitmask_decoding(mock_log: HeatPumpLog) -> None:
    """Verify bitmask decoding across all 11 conditions."""
    desc_map = {desc.key: desc for desc in COOLING_START_CONDITION_SENSORS}

    # Test bit 1 (control_method) and bit 64 (demand)
    mock_log.cooling_start_conditions_raw = 1 | 64

    assert desc_map["cooling_start_condition_control_method"].value_fn(mock_log) is True
    assert desc_map["cooling_start_condition_demand"].value_fn(mock_log) is True
    assert desc_map["cooling_start_condition_dtc"].value_fn(mock_log) is False
    assert (
        desc_map["cooling_start_condition_exponential_backoff"].value_fn(mock_log)
        is False
    )

    # Test all 11 bits set: 1+2+4+8+16+32+64+128+256+512+1024 = 2047
    mock_log.cooling_start_conditions_raw = 2047
    for name in COOLING_START_CONDITION_BITS:
        assert desc_map[f"cooling_start_condition_{name}"].value_fn(mock_log) is True


def test_binary_sensor_unique_id_construction(
    mock_hp_info: HeatPumpInfo, mock_log: HeatPumpLog
) -> None:
    """Verify unique ID format: {heat_pump_id}_{entity_key}."""

    class DummyCoord:
        data = mock_log

    desc = BINARY_SENSORS[0]
    binary_sensor = WeheatHeatPumpBinarySensor(
        mock_hp_info,
        DummyCoord(),  # type: ignore[arg-type]
        desc,
    )
    expected_id = f"{mock_hp_info.heatpump_id}_{desc.key}"
    assert binary_sensor._attr_unique_id == expected_id
    assert binary_sensor.is_on is True
