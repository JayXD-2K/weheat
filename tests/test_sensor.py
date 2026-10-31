"""Unit tests for the Weheat sensor platform and entity specifications."""

from __future__ import annotations

import pytest
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import (
    PERCENTAGE,
    REVOLUTIONS_PER_MINUTE,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfVolumeFlowRate,
)

from custom_components.weheat.const import (
    DISPLAY_PRECISION_FLOW,
    DISPLAY_PRECISION_WATER_TEMP,
    DISPLAY_PRECISION_WATTS,
)
from custom_components.weheat.models import (
    CoolingPauseReason,
    CoolingStopReason,
    HeatPumpEnergy,
    HeatPumpInfo,
    HeatPumpLog,
    HeatPumpState,
)
from custom_components.weheat.sensor import (
    COOLING_SENSORS,
    DHW_ENERGY_SENSORS,
    DHW_SENSORS,
    ENERGY_SENSORS,
    SENSORS,
    WeheatHeatPumpSensor,
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


@pytest.fixture
def mock_energy() -> HeatPumpEnergy:
    """Provide a mock HeatPumpEnergy instance."""
    data = load_fixture("energy_total.json")
    return HeatPumpEnergy.from_api_dict(data)


# ---------------------------------------------------------------------------
# 1. Entity Catalog & Schema Completeness
# ---------------------------------------------------------------------------


def test_sensor_catalog_counts() -> None:
    """Verify exact counts of sensors across all groups."""
    assert len(SENSORS) == 15, "Expected exactly 15 base operational sensors"
    assert len(DHW_SENSORS) == 5, "Expected exactly 5 DHW operational sensors"
    assert len(COOLING_SENSORS) == 7, "Expected exactly 7 cooling diagnostic sensors"
    assert len(ENERGY_SENSORS) == 10, "Expected exactly 10 cumulative energy sensors"
    assert len(DHW_ENERGY_SENSORS) == 2, (
        "Expected exactly 2 DHW cumulative energy sensors"
    )

    total = (
        len(SENSORS)
        + len(DHW_SENSORS)
        + len(COOLING_SENSORS)
        + len(ENERGY_SENSORS)
        + len(DHW_ENERGY_SENSORS)
    )
    assert total == 39, "Expected exactly 39 total sensors"


def test_sensor_keys_are_unique() -> None:
    """Verify that all 39 sensor keys are distinct."""
    all_keys = [
        desc.key
        for group in (
            SENSORS,
            DHW_SENSORS,
            COOLING_SENSORS,
            ENERGY_SENSORS,
            DHW_ENERGY_SENSORS,
        )
        for desc in group
    ]
    assert len(all_keys) == 39
    assert len(set(all_keys)) == 39, "All sensor keys must be strictly unique"


def test_sensor_metadata_attributes() -> None:
    """Verify device classes, units, and state classes for representative sensors."""
    desc_map = {desc.key: desc for desc in SENSORS}

    # Power output
    assert desc_map["power_output"].device_class == SensorDeviceClass.POWER
    assert desc_map["power_output"].state_class == SensorStateClass.MEASUREMENT
    assert desc_map["power_output"].native_unit_of_measurement == UnitOfPower.WATT
    assert (
        desc_map["power_output"].suggested_display_precision == DISPLAY_PRECISION_WATTS
    )

    # Temperatures
    for temp_key in (
        "water_inlet_temperature",
        "water_outlet_temperature",
        "ch_inlet_temperature",
        "outside_temperature",
        "air_outlet_temperature",
    ):
        assert desc_map[temp_key].device_class == SensorDeviceClass.TEMPERATURE
        assert (
            desc_map[temp_key].native_unit_of_measurement == UnitOfTemperature.CELSIUS
        )
        assert (
            desc_map[temp_key].suggested_display_precision
            == DISPLAY_PRECISION_WATER_TEMP
        )

    # Flow
    assert (
        desc_map["central_heating_flow_volume"].device_class
        == SensorDeviceClass.VOLUME_FLOW_RATE
    )
    assert (
        desc_map["central_heating_flow_volume"].native_unit_of_measurement
        == UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR
    )
    assert (
        desc_map["central_heating_flow_volume"].suggested_display_precision
        == DISPLAY_PRECISION_FLOW
    )

    # RPM & %
    assert (
        desc_map["compressor_rpm"].native_unit_of_measurement == REVOLUTIONS_PER_MINUTE
    )
    assert desc_map["compressor_percentage"].native_unit_of_measurement == PERCENTAGE

    # State enum
    assert desc_map["heat_pump_state"].device_class == SensorDeviceClass.ENUM
    assert "heating" in desc_map["heat_pump_state"].options
    assert "cooling" in desc_map["heat_pump_state"].options
    assert "dhw" in desc_map["heat_pump_state"].options


# ---------------------------------------------------------------------------
# 2. Value Mappings Against Mock Fixtures
# ---------------------------------------------------------------------------


def test_base_sensor_values(mock_log: HeatPumpLog) -> None:
    """Verify base operational sensors resolve expected values from latest_logs.json."""
    desc_map = {desc.key: desc for desc in SENSORS}

    assert desc_map["power_output"].value_fn(mock_log) == 4700.0
    assert desc_map["power_input"].value_fn(mock_log) == 1120.0
    assert desc_map["cop"].value_fn(mock_log) == pytest.approx(4700.0 / 1120.0)
    assert desc_map["water_inlet_temperature"].value_fn(mock_log) == 32.4
    assert desc_map["water_outlet_temperature"].value_fn(mock_log) == 37.2
    assert desc_map["ch_inlet_temperature"].value_fn(mock_log) == 35.8
    assert desc_map["outside_temperature"].value_fn(mock_log) == 7.8
    assert desc_map["air_outlet_temperature"].value_fn(mock_log) == 4.1
    assert desc_map["thermostat_water_setpoint"].value_fn(mock_log) == 38.0
    assert desc_map["thermostat_room_temperature"].value_fn(mock_log) == 20.5
    assert desc_map["thermostat_room_temperature_setpoint"].value_fn(mock_log) == 21.0
    assert desc_map["heat_pump_state"].value_fn(mock_log) == "heating"
    assert desc_map["compressor_rpm"].value_fn(mock_log) == 2850.0
    assert desc_map["compressor_percentage"].value_fn(mock_log) == 63
    assert desc_map["central_heating_flow_volume"].value_fn(mock_log) == 0.9


def test_dhw_sensor_values(mock_log: HeatPumpLog) -> None:
    """Verify DHW sensors resolve expected values from latest_logs.json."""
    desc_map = {desc.key: desc for desc in DHW_SENSORS}

    assert desc_map["dhw_top_temperature"].value_fn(mock_log) == 52.5
    assert desc_map["dhw_bottom_temperature"].value_fn(mock_log) == 48.2
    assert desc_map["dhw_flow_volume"].value_fn(mock_log) == 0.0
    assert desc_map["dhw_target_temperature"].value_fn(mock_log) == 55.0
    assert desc_map["dhw_control_method"].value_fn(mock_log) == "schedule"


def test_energy_sensor_values(mock_energy: HeatPumpEnergy) -> None:
    """Verify energy sensors resolve expected cumulative registers from energy_total.json."""
    desc_map = {desc.key: desc for desc in ENERGY_SENSORS}
    dhw_map = {desc.key: desc for desc in DHW_ENERGY_SENSORS}

    assert desc_map["electricity_used"].value_fn(mock_energy) == 1721.0
    assert desc_map["electricity_used_indoor_unit"].value_fn(mock_energy) == 49.4
    assert desc_map["energy_output"].value_fn(mock_energy) == 6309.4
    assert desc_map["electricity_used_heating"].value_fn(mock_energy) == 1245.8
    assert desc_map["electricity_used_cooling"].value_fn(mock_energy) == 12.0
    assert desc_map["electricity_used_defrost"].value_fn(mock_energy) == 37.5
    assert desc_map["electricity_used_standby"].value_fn(mock_energy) == 45.2
    assert desc_map["energy_output_heating"].value_fn(mock_energy) == 5230.6
    assert desc_map["energy_output_cooling"].value_fn(mock_energy) == 38.2
    assert desc_map["energy_output_defrost"].value_fn(mock_energy) == -33.2

    assert dhw_map["electricity_used_dhw"].value_fn(mock_energy) == 380.5
    assert dhw_map["energy_output_dhw"].value_fn(mock_energy) == 1150.2


# ---------------------------------------------------------------------------
# 3. Edge Cases & Boundary Behaviors
# ---------------------------------------------------------------------------


def test_cop_boundary_behavior(mock_log: HeatPumpLog) -> None:
    """Verify COP behavior under zero power, cooling state, and missing values."""
    desc_map = {desc.key: desc for desc in SENSORS}
    cop_desc = desc_map["cop"]

    # Zero input power -> 0.0
    mock_log.p_compressor_in = 0.0
    mock_log.cm_mass_power_in = 0.0
    assert cop_desc.value_fn(mock_log) == 0.0

    # Negative input power -> 0.0
    mock_log.p_compressor_in = -10.0
    mock_log.cm_mass_power_in = -10.0
    assert cop_desc.value_fn(mock_log) == 0.0

    # Cooling state directionality (multiplied by -1)
    mock_log.p_compressor_in = 1000.0
    mock_log.cm_mass_power_in = 1000.0
    mock_log.p_compressor_out = 3000.0
    mock_log.cm_mass_power_out = 3000.0
    mock_log.state = HeatPumpState.COOLING
    assert cop_desc.value_fn(mock_log) == -3.0

    # Missing power -> None
    mock_log.p_compressor_in = None
    mock_log.cm_mass_power_in = None
    assert cop_desc.value_fn(mock_log) is None


def test_dhw_target_temp_zero_maps_to_none(mock_log: HeatPumpLog) -> None:
    """Verify that a target temperature of 0 indicates DHW off and maps to None."""
    desc_map = {desc.key: desc for desc in DHW_SENSORS}
    target_desc = desc_map["dhw_target_temperature"]

    mock_log.dhw_target_temperature = 0.0
    assert target_desc.value_fn(mock_log) is None

    mock_log.dhw_target_temperature = 50.0
    assert target_desc.value_fn(mock_log) == 50.0


def test_cooling_latched_reason_reporting(mock_log: HeatPumpLog) -> None:
    """Verify cooling pause and stop reasons report 'none' during active cooling."""
    desc_map = {desc.key: desc for desc in COOLING_SENSORS}
    pause_desc = desc_map["cooling_pause_reason"]
    stop_desc = desc_map["cooling_stop_reason"]

    mock_log.cooling_pause_reason = CoolingPauseReason.ROOM_TEMPERATURE_TOO_LOW.value
    mock_log.cooling_stop_reason = CoolingStopReason.HEAT_PUMP_CONTROL.value

    # When cooling is IDLE (0)
    mock_log.cooling_status = 0
    mock_log.state = HeatPumpState.STANDBY
    assert pause_desc.value_fn(mock_log) == "room_temperature_too_low"
    assert stop_desc.value_fn(mock_log) == "heat_pump_control"

    # When cooling is ACTIVE (2)
    mock_log.cooling_status = 2
    mock_log.state = HeatPumpState.COOLING
    assert pause_desc.value_fn(mock_log) == "none"
    assert stop_desc.value_fn(mock_log) == "none"


def test_sensor_unique_id_construction(
    mock_hp_info: HeatPumpInfo, mock_log: HeatPumpLog
) -> None:
    """Verify unique ID format: {heat_pump_id}_{entity_key}."""

    class DummyCoord:
        data = mock_log

    desc = SENSORS[0]
    sensor = WeheatHeatPumpSensor(mock_hp_info, DummyCoord(), desc)  # type: ignore[arg-type]
    expected_id = f"{mock_hp_info.heatpump_id}_{desc.key}"
    assert sensor._attr_unique_id == expected_id
    assert sensor.native_value == 4700.0
