"""Adversarial and boundary stress tests for Milestone 3 implementation."""

from __future__ import annotations

import datetime
import math
from unittest.mock import MagicMock

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from custom_components.weheat.binary_sensor import (
    BINARY_SENSORS,
    COOLING_START_CONDITION_SENSORS,
    WeheatHeatPumpBinarySensor,
)
from custom_components.weheat.binary_sensor import (
    async_setup_entry as async_setup_binary_sensors,
)
from custom_components.weheat.coordinator import (
    WeheatData,
    WeheatDataUpdateCoordinator,
    WeheatEnergyUpdateCoordinator,
)
from custom_components.weheat.models import (
    CoolingPauseReason,
    CoolingStatus,
    CoolingStopReason,
    HeatPumpEnergy,
    HeatPumpInfo,
    HeatPumpLog,
    HeatPumpState,
    pwm_to_volume,
)
from custom_components.weheat.sensor import (
    COOLING_SENSORS,
    DHW_ENERGY_SENSORS,
    DHW_SENSORS,
    ENERGY_SENSORS,
    SENSORS,
    WeheatHeatPumpSensor,
    _latched_reason,
)
from custom_components.weheat.sensor import (
    async_setup_entry as async_setup_sensors,
)
from tests.conftest import load_fixture

TEST_HP_UUID = "00000000-0000-0000-0000-000000000001"


@pytest.fixture
def base_hp_info() -> HeatPumpInfo:
    """Fixture providing a baseline HeatPumpInfo."""
    return HeatPumpInfo(
        heatpump_id=TEST_HP_UUID,
        name="Adversarial Test Heat Pump",
        model=0,
        serial_number="WH-ADV-001",
        has_dhw=True,
        has_ch_boiler=False,
        firmware_version="3.0.0",
        part_number="PN-99999",
    )


@pytest.fixture
def base_log() -> HeatPumpLog:
    """Fixture providing a baseline HeatPumpLog."""
    raw = load_fixture("latest_logs.json")
    return HeatPumpLog.from_api_dict(raw)


@pytest.fixture
def base_energy() -> HeatPumpEnergy:
    """Fixture providing a baseline HeatPumpEnergy."""
    raw = load_fixture("energy_total.json")
    return HeatPumpEnergy.from_api_dict(raw)


# ===========================================================================
# 1. Extreme Telemetry Payloads & Nullable Fields
# ===========================================================================


class TestExtremeTelemetryPayloads:
    """Adversarial stress tests for extreme payloads and nullable field resilience."""

    def test_all_nullable_fields_none_log(self, base_hp_info: HeatPumpInfo) -> None:
        """Verify HeatPumpLog with all fields None evaluates safely in all sensor value_fn."""
        empty_log = HeatPumpLog(
            heat_pump_id=TEST_HP_UUID,
            nominal_rpm=4500,
            timestamp=None,
            is_online=True,
            state=None,
            raw_state=None,
        )

        # Base sensors
        for desc in SENSORS:
            val = desc.value_fn(empty_log)
            assert val is None, f"Sensor {desc.key} expected None, got {val}"

        # DHW sensors
        for desc in DHW_SENSORS:
            val = desc.value_fn(empty_log)
            assert val is None, f"DHW Sensor {desc.key} expected None, got {val}"

        # Cooling sensors
        for desc in COOLING_SENSORS:
            val = desc.value_fn(empty_log)
            assert val is None, f"Cooling Sensor {desc.key} expected None, got {val}"

        # Binary sensors
        for desc in BINARY_SENSORS:
            val = desc.value_fn(empty_log)
            # Actuator decodes default to False (bool)
            assert val is False, f"Binary Sensor {desc.key} expected False, got {val}"

        for desc in COOLING_START_CONDITION_SENSORS:
            val = desc.value_fn(empty_log)
            assert val is None, f"Cooling condition {desc.key} expected None, got {val}"

    def test_extreme_temperatures(self, base_log: HeatPumpLog) -> None:
        """Verify temperature sensors handle extreme negative and positive values."""
        desc_map = {desc.key: desc for desc in SENSORS}

        extremes = [-273.15, -50.0, 0.0, 100.0, 150.0, 999.9]
        temp_keys = [
            "water_inlet_temperature",
            "water_outlet_temperature",
            "ch_inlet_temperature",
            "outside_temperature",
            "air_outlet_temperature",
            "thermostat_water_setpoint",
            "thermostat_room_temperature",
            "thermostat_room_temperature_setpoint",
        ]

        for temp in extremes:
            base_log.t_water_in = temp
            base_log.t_water_out = temp
            base_log.t_water_house_in = temp
            base_log.t_air_in = temp
            base_log.t_air_out = temp
            base_log.t_thermostat_setpoint = temp
            base_log.t_room = temp
            base_log.t_room_target = temp

            for k in temp_keys:
                assert desc_map[k].value_fn(base_log) == temp

    def test_cop_division_by_zero_and_negative_power(
        self, base_log: HeatPumpLog
    ) -> None:
        """Verify COP safely returns 0.0 under zero, negative, or invalid power."""
        desc_map = {desc.key: desc for desc in SENSORS}
        cop_desc = desc_map["cop"]

        # Case 1: p_in = 0, p_out = 5000 (division by zero trap)
        base_log.cm_mass_power_in = 0.0
        base_log.cm_mass_power_out = 5000.0
        assert cop_desc.value_fn(base_log) == 0.0

        # Case 2: p_in = -500, p_out = 2000 (negative electrical consumption)
        base_log.cm_mass_power_in = -500.0
        base_log.cm_mass_power_out = 2000.0
        assert cop_desc.value_fn(base_log) == 0.0

        # Case 3: p_in = 0, p_out = 0
        base_log.cm_mass_power_in = 0.0
        base_log.cm_mass_power_out = 0.0
        assert cop_desc.value_fn(base_log) == 0.0

        # Case 4: p_in = -0.0
        base_log.cm_mass_power_in = -0.0
        base_log.cm_mass_power_out = 1000.0
        assert cop_desc.value_fn(base_log) == 0.0

    def test_nan_and_infinity_float_handling(self, base_log: HeatPumpLog) -> None:
        """Verify behavior when float inputs are NaN or Infinity."""
        desc_map = {desc.key: desc for desc in SENSORS}

        # NaN power input
        base_log.cm_mass_power_in = float("nan")
        base_log.cm_mass_power_out = 1000.0
        cop_val = desc_map["cop"].value_fn(base_log)
        # NaN / p_in: float("nan") <= 0 evaluates to False, so division occurs resulting in NaN
        assert math.isnan(cop_val)

        # Infinity power input: 1000 / inf -> 0.0
        base_log.cm_mass_power_in = float("inf")
        base_log.cm_mass_power_out = 1000.0
        assert desc_map["cop"].value_fn(base_log) == 0.0

        # Negative infinity power input: -inf <= 0 is True -> returns 0.0
        base_log.cm_mass_power_in = float("-inf")
        assert desc_map["cop"].value_fn(base_log) == 0.0

    def test_pwm_to_volume_boundaries(self) -> None:
        """Verify pwm_to_volume boundary and abnormal value conversions."""
        assert pwm_to_volume(None) is None
        assert pwm_to_volume(-1) is None
        assert pwm_to_volume(-0.01) is None
        assert pwm_to_volume(75.1) is None
        assert pwm_to_volume(100) is None
        assert pwm_to_volume(255) is None  # Common sensor error code

        # Standby range (<= 5)
        assert pwm_to_volume(0) == 0.0
        assert pwm_to_volume(5) == 0.0
        assert pwm_to_volume(5.0) == 0.0

        # Valid operational range
        assert pwm_to_volume(40) == 1.05  # halfway: (35/70) * 2.1 = 1.05
        assert pwm_to_volume(75) == 2.10  # max

    def test_negative_energy_values(self) -> None:
        """Verify HeatPumpEnergy handles negative and non-standard energy numbers."""
        energy = HeatPumpEnergy(
            heatpump_id=TEST_HP_UUID,
            total_ein_heating=100.0,
            total_ein_dhw=50.0,
            total_ein_cooling=10.0,
            total_ein_standby=5.0,
            total_ein_heating_defrost=2.0,
            total_ein_dhw_defrost=1.0,
            total_eout_heating=400.0,
            total_eout_dhw=150.0,
            total_eout_heating_defrost=-30.0,  # inherently negative
            total_eout_dhw_defrost=-10.0,  # inherently negative
            total_eout_cooling=20.0,
        )

        assert energy.electricity_used == 168.0
        # energy_output = 400 + 150 + (-30) + (-10) + (-20) = 490.0
        assert energy.energy_output == 490.0
        assert energy.energy_output_defrost == -40.0

    def test_corrupt_and_edge_timestamps(self, base_log: HeatPumpLog) -> None:
        """Verify sensor platform handles missing or strange timestamp strings."""
        desc_map = {desc.key: desc for desc in COOLING_SENSORS}
        wait_desc = desc_map["cooling_wait_until"]
        last_desc = desc_map["last_cooling_time"]

        # ISO format
        base_log.last_cooling_time = "2026-10-30T07:00:00Z"
        base_log.cooling_start_conditions_raw = 0
        assert wait_desc.value_fn(base_log) == datetime.datetime(
            2026, 10, 30, 7, 0, tzinfo=datetime.UTC
        )
        assert last_desc.value_fn(base_log) == "2026-10-30T07:00:00Z"

        # None timestamp
        base_log.last_cooling_time = None
        assert wait_desc.value_fn(base_log) is None
        assert last_desc.value_fn(base_log) is None

    def test_energy_from_api_dict_null_field_trap(self) -> None:
        """Empirically demonstrate that explicit null (None) in API energy JSON triggers TypeError."""
        payload_with_null = {
            "heatPumpId": TEST_HP_UUID,
            "totalEInHeating": None,  # null from cloud
        }
        with pytest.raises(TypeError, match="float.*NoneType"):
            HeatPumpEnergy.from_api_dict(payload_with_null)

    def test_heat_pump_info_model_none_trap(self) -> None:
        """Empirically demonstrate that model=None triggers TypeError on comparison in model_name/nominal_rpm."""
        info = HeatPumpInfo.from_api_dict({"id": TEST_HP_UUID, "model": None})
        with pytest.raises(TypeError, match="'<=' not supported"):
            _ = info.model_name

        with pytest.raises(TypeError, match="'<=' not supported"):
            _ = info.nominal_rpm

    def test_unknown_state_codes_mapping(self, base_log: HeatPumpLog) -> None:
        """Verify that unmapped numeric states safely decode to None."""
        assert HeatPumpState.from_api_state(None) is None
        assert HeatPumpState.from_api_state(99999) is None
        assert HeatPumpState.from_api_state(-1) is None


# ===========================================================================
# 2. DHW Gating & Entity Count Verification (32 vs 39)
# ===========================================================================


class TestDhwGatingAndEntityCounts:
    """Adversarial verification of DHW sensor gating and exact entity counts."""

    DHW_KEYS = {
        "dhw_top_temperature",
        "dhw_bottom_temperature",
        "dhw_flow_volume",
        "dhw_target_temperature",
        "dhw_control_method",
        "electricity_used_dhw",
        "energy_output_dhw",
    }

    @pytest.mark.asyncio
    async def test_dhw_gating_disabled_exact_32_sensors(
        self, base_log: HeatPumpLog, base_energy: HeatPumpEnergy
    ) -> None:
        """Verify that when has_dhw is False, EXACTLY 32 sensors are created and ZERO DHW sensors."""
        hass = HomeAssistant()
        no_dhw_info = HeatPumpInfo(
            heatpump_id=TEST_HP_UUID,
            name="No DHW Heat Pump",
            model=0,
            serial_number="WH-NODHW-001",
            has_dhw=False,  # strictly False
        )

        data_coord = MagicMock(spec=WeheatDataUpdateCoordinator)
        data_coord.data = base_log
        energy_coord = MagicMock(spec=WeheatEnergyUpdateCoordinator)
        energy_coord.data = base_energy

        weheat_data = WeheatData(
            heat_pump_info=no_dhw_info,
            data_coordinator=data_coord,
            energy_coordinator=energy_coord,
        )

        entry = ConfigEntry(runtime_data=[weheat_data])
        created_entities: list[WeheatHeatPumpSensor] = []

        def mock_add_entities(entities: list[WeheatHeatPumpSensor]) -> None:
            created_entities.extend(entities)

        await async_setup_sensors(hass, entry, mock_add_entities)

        created_keys = {e.entity_description.key for e in created_entities}

        # 1. Total count must be exactly 32
        assert len(created_entities) == 32, (
            f"Expected 32 sensors when has_dhw is False, got {len(created_entities)}"
        )
        assert len(created_keys) == 32

        # 2. Not a single DHW sensor must be present
        forbidden = created_keys.intersection(self.DHW_KEYS)
        assert not forbidden, (
            f"Found leaked DHW sensors when has_dhw=False: {forbidden}"
        )

    @pytest.mark.asyncio
    async def test_dhw_gating_enabled_exact_39_sensors(
        self, base_log: HeatPumpLog, base_energy: HeatPumpEnergy
    ) -> None:
        """Verify that when has_dhw is True, EXACTLY 39 sensors are created including all 7 DHW sensors."""
        hass = HomeAssistant()
        dhw_info = HeatPumpInfo(
            heatpump_id=TEST_HP_UUID,
            name="DHW Heat Pump",
            model=0,
            serial_number="WH-DHW-001",
            has_dhw=True,  # strictly True
        )

        data_coord = MagicMock(spec=WeheatDataUpdateCoordinator)
        data_coord.data = base_log
        energy_coord = MagicMock(spec=WeheatEnergyUpdateCoordinator)
        energy_coord.data = base_energy

        weheat_data = WeheatData(
            heat_pump_info=dhw_info,
            data_coordinator=data_coord,
            energy_coordinator=energy_coord,
        )

        entry = ConfigEntry(runtime_data=[weheat_data])
        created_entities: list[WeheatHeatPumpSensor] = []

        def mock_add_entities(entities: list[WeheatHeatPumpSensor]) -> None:
            created_entities.extend(entities)

        await async_setup_sensors(hass, entry, mock_add_entities)

        created_keys = {e.entity_description.key for e in created_entities}

        # 1. Total count must be exactly 39
        assert len(created_entities) == 39, (
            f"Expected 39 sensors when has_dhw is True, got {len(created_entities)}"
        )

        # 2. All 7 DHW sensors must be present
        assert self.DHW_KEYS.issubset(created_keys), (
            f"Missing DHW sensors: {self.DHW_KEYS - created_keys}"
        )

    @pytest.mark.asyncio
    async def test_dhw_gating_binary_sensor_count(self, base_log: HeatPumpLog) -> None:
        """Verify that binary sensor setup creates exactly 16 binary sensors."""
        hass = HomeAssistant()
        info = HeatPumpInfo(
            heatpump_id=TEST_HP_UUID,
            name="Test Heat Pump",
            model=0,
            serial_number="WH-BS-001",
            has_dhw=False,
        )

        data_coord = MagicMock(spec=WeheatDataUpdateCoordinator)
        data_coord.data = base_log

        weheat_data = WeheatData(
            heat_pump_info=info,
            data_coordinator=data_coord,
            energy_coordinator=MagicMock(),
        )

        entry = ConfigEntry(runtime_data=[weheat_data])
        created_entities: list[WeheatHeatPumpBinarySensor] = []

        def mock_add_entities(entities: list[WeheatHeatPumpBinarySensor]) -> None:
            created_entities.extend(entities)

        await async_setup_binary_sensors(hass, entry, mock_add_entities)

        # 5 actuator + 11 cooling conditions = 16 binary sensors
        assert len(created_entities) == 16, (
            f"Expected exactly 16 binary sensors, got {len(created_entities)}"
        )


# ===========================================================================
# 3. Cooling Latching States & Latched Reason Verification
# ===========================================================================


class TestCoolingLatchingStates:
    """Adversarial stress testing of cooling state transitions and reason latching."""

    def test_latched_reason_active_cooling_always_returns_none(
        self, base_log: HeatPumpLog
    ) -> None:
        """Verify _latched_reason strictly returns 'none' when actively cooling (1, 2, 3 or state=COOLING)."""
        reasons = [
            CoolingPauseReason.ROOM_TEMPERATURE_TOO_LOW.value,
            CoolingPauseReason.OUTSIDE_TEMPERATURE_TOO_LOW.value,
            CoolingPauseReason.CONTACT_BLOCKED.value,
            99,  # invalid/unmapped reason
        ]

        active_cooling_statuses = [
            CoolingStatus.STARTING.value,  # 1
            CoolingStatus.ACTIVE.value,  # 2
            CoolingStatus.STOPPING.value,  # 3
        ]

        for status in active_cooling_statuses:
            base_log.cooling_status = status
            base_log.state = HeatPumpState.HEATING  # even if heatpump state differs

            for r in reasons:
                result = _latched_reason(base_log, r, CoolingPauseReason)
                assert result == "none", (
                    f"Expected 'none' during cooling_status={status}, got {result}"
                )

        # Also test via state == HeatPumpState.COOLING (even if cooling_status is 0/Idle)
        base_log.cooling_status = CoolingStatus.IDLE.value
        base_log.state = HeatPumpState.COOLING

        for r in reasons:
            result = _latched_reason(base_log, r, CoolingPauseReason)
            assert result == "none", f"Expected 'none' when state=COOLING, got {result}"

    def test_latched_reason_idle_or_standby_returns_reason_or_none(
        self, base_log: HeatPumpLog
    ) -> None:
        """Verify _latched_reason returns lowercase reason name when inactive, or None if reason is None."""
        base_log.cooling_status = CoolingStatus.IDLE.value
        base_log.state = HeatPumpState.STANDBY

        # Reason None -> returns None
        assert _latched_reason(base_log, None, CoolingPauseReason) is None

        # Reason 0 (NONE) -> returns "none"
        assert (
            _latched_reason(base_log, CoolingPauseReason.NONE.value, CoolingPauseReason)
            == "none"
        )

        # Valid reasons -> returns lowercase string
        assert (
            _latched_reason(
                base_log,
                CoolingPauseReason.ROOM_TEMPERATURE_TOO_LOW.value,
                CoolingPauseReason,
            )
            == "room_temperature_too_low"
        )
        assert (
            _latched_reason(
                base_log,
                CoolingStopReason.HEAT_PUMP_CONTROL.value,
                CoolingStopReason,
            )
            == "heat_pump_control"
        )

        # Unmapped reason int (e.g. 999) -> handles gracefully returning None
        assert _latched_reason(base_log, 999, CoolingPauseReason) is None
        assert _latched_reason(base_log, 999, CoolingStopReason) is None

    def test_cooling_blocked_by_logic(self, base_log: HeatPumpLog) -> None:
        """Verify cooling_blocked_by returns 'none' when cooling or all conditions met, else first unmet."""
        desc_map = {desc.key: desc for desc in COOLING_SENSORS}
        blocked_desc = desc_map["cooling_blocked_by"]

        # When active cooling -> 'none'
        base_log.cooling_status = CoolingStatus.ACTIVE.value
        base_log.cooling_start_conditions_raw = 0  # no conditions met
        assert blocked_desc.value_fn(base_log) == "none"

        # When idle and all conditions met (raw = 2047) -> 'none'
        base_log.cooling_status = CoolingStatus.IDLE.value
        base_log.state = HeatPumpState.STANDBY
        base_log.cooling_start_conditions_raw = 2047  # all 11 bits set
        assert blocked_desc.value_fn(base_log) == "none"

        # When idle and 'control_method' (bit 1) is missing
        base_log.cooling_start_conditions_raw = 2047 & ~1
        assert blocked_desc.value_fn(base_log) == "control_method"

        # When idle and conditions None -> None
        base_log.cooling_start_conditions_raw = None
        assert blocked_desc.value_fn(base_log) is None

    def test_cooling_conditions_met_count(self, base_log: HeatPumpLog) -> None:
        """Verify cooling_conditions_met counts correctly excluding not counted conditions."""
        desc_map = {desc.key: desc for desc in COOLING_SENSORS}
        met_desc = desc_map["cooling_conditions_met"]

        # When active cooling -> returns None
        base_log.cooling_status = CoolingStatus.ACTIVE.value
        assert met_desc.value_fn(base_log) is None

        # When idle: total counted = 9 (11 - 2)
        base_log.cooling_status = CoolingStatus.IDLE.value
        base_log.state = HeatPumpState.STANDBY

        # All 11 bits set -> counted = 9
        base_log.cooling_start_conditions_raw = 2047
        assert met_desc.value_fn(base_log) == 9

        # Zero bits set -> counted = 0
        base_log.cooling_start_conditions_raw = 0
        assert met_desc.value_fn(base_log) == 0


# ===========================================================================
# 4. Compressor Percentage Polymorphism & RPM Scaling
# ===========================================================================


class TestCompressorPercentagePolymorphism:
    """Adversarial testing of CompressorPercentage as integer and callable."""

    def test_compressor_percentage_as_int(self, base_log: HeatPumpLog) -> None:
        """Verify CompressorPercentage behaves as an int in arithmetic, comparison, and formatting."""
        base_log.rpm = 2250
        base_log.nominal_rpm = 4500

        cp = base_log.compressor_percentage
        assert cp is not None
        assert isinstance(cp, int)
        assert cp == 50
        assert cp + 10 == 60
        assert cp - 10 == 40
        assert cp * 2 == 100
        assert cp // 2 == 25
        assert f"{cp}%" == "50%"
        assert int(cp) == 50

    def test_compressor_percentage_as_callable(self, base_log: HeatPumpLog) -> None:
        """Verify CompressorPercentage can be invoked as a method with or without nominal_rpm."""
        base_log.rpm = 2250
        base_log.nominal_rpm = 4500

        cp = base_log.compressor_percentage
        assert cp is not None
        assert callable(cp)

        # Call with no args (uses bound nominal_rpm 4500)
        assert cp() == 50

        # Call with alternative nominal_rpm (e.g. 5520 for Sparrow/Blackbird P60)
        assert cp(5520) == int((100.0 / 5520) * 2250)  # 40

        # Call with zero or negative nominal_rpm -> returns None safely
        assert cp(0) is None
        assert cp(-100) is None

    def test_compressor_percentage_extremes_and_none(
        self, base_log: HeatPumpLog
    ) -> None:
        """Verify compressor_percentage under zero RPM, None RPM, and zero nominal RPM."""
        # RPM is None -> returns None
        base_log.rpm = None
        assert base_log.compressor_percentage is None

        # nominal_rpm is 0 -> returns None safely (avoids ZeroDivisionError)
        base_log.rpm = 2000
        base_log.nominal_rpm = 0
        assert base_log.compressor_percentage is None

        # RPM is 0 -> 0%
        base_log.rpm = 0
        base_log.nominal_rpm = 4500
        cp = base_log.compressor_percentage
        assert cp == 0
        assert cp() == 0

        # High RPM exceeding 100% capacity (e.g. boost mode)
        base_log.rpm = 5400
        base_log.nominal_rpm = 4500
        cp = base_log.compressor_percentage
        assert cp == 120
        assert cp() == 120


# ===========================================================================
# 5. Device Registry Identifiers & Unique ID Immutability
# ===========================================================================


class TestDeviceRegistryAndUniqueIdImmutability:
    """Verification of unique ID immutability and exact DeviceInfo registry identifiers."""

    def test_all_39_sensors_unique_id_format(
        self, base_hp_info: HeatPumpInfo, base_log: HeatPumpLog
    ) -> None:
        """Verify all 39 sensors follow {heatpump_id}_{key} unique ID format and registry identifiers."""
        all_groups = (
            SENSORS,
            DHW_SENSORS,
            COOLING_SENSORS,
            ENERGY_SENSORS,
            DHW_ENERGY_SENSORS,
        )

        class DummyCoordinator:
            data = base_log

        seen_unique_ids: set[str] = set()

        for group in all_groups:
            for desc in group:
                sensor = WeheatHeatPumpSensor(
                    base_hp_info,
                    DummyCoordinator(),
                    desc,  # type: ignore[arg-type]
                )
                expected_unique_id = f"{base_hp_info.heatpump_id}_{desc.key}"

                # 1. Unique ID matches formula exactly
                assert sensor.unique_id == expected_unique_id
                assert sensor._attr_unique_id == expected_unique_id

                # 2. Must be globally distinct across all sensors
                assert expected_unique_id not in seen_unique_ids
                seen_unique_ids.add(expected_unique_id)

                # 3. Device info identifiers must match Core {(DOMAIN, heatpump_id)}
                assert sensor.device_info.identifiers == {
                    ("weheat", base_hp_info.heatpump_id)
                }
                assert sensor.device_info.manufacturer == "Weheat"
                assert sensor.device_info.model == "Blackbird P80 heat pump"
                assert sensor.device_info.sw_version == "3.0.0"
                assert sensor.device_info.hw_version == "PN-99999"

        assert len(seen_unique_ids) == 39

    def test_all_16_binary_sensors_unique_id_format(
        self, base_hp_info: HeatPumpInfo, base_log: HeatPumpLog
    ) -> None:
        """Verify all 16 binary sensors follow {heatpump_id}_{key} unique ID format."""
        all_groups = (BINARY_SENSORS, COOLING_START_CONDITION_SENSORS)

        class DummyCoordinator:
            data = base_log

        seen_unique_ids: set[str] = set()

        for group in all_groups:
            for desc in group:
                bs = WeheatHeatPumpBinarySensor(
                    base_hp_info,
                    DummyCoordinator(),
                    desc,  # type: ignore[arg-type]
                )
                expected_unique_id = f"{base_hp_info.heatpump_id}_{desc.key}"

                # 1. Unique ID matches formula
                assert bs.unique_id == expected_unique_id
                assert bs._attr_unique_id == expected_unique_id

                # 2. Distinctness
                assert expected_unique_id not in seen_unique_ids
                seen_unique_ids.add(expected_unique_id)

                # 3. Device info identifiers
                assert bs.device_info.identifiers == {
                    ("weheat", base_hp_info.heatpump_id)
                }

        assert len(seen_unique_ids) == 16
