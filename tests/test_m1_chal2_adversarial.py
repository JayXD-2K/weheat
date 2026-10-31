"""Adversarial stress-test suite for Milestone 1 sensor logic changes.

Conducted by Challenger 2 for:
1. cooling_wait_until:
   - cooling_available_from in past, present, future, and None.
   - Timezone handling: UTC ISO 8601 vs naive datetime vs aware datetime vs offsets.
   - Corrupt, invalid, and edge-case inputs.
2. cooling_conditions_met:
   - 0..9 conditions met systematically tested across bitmask spectrum.
   - Uncounted bits (control_method, contact_not_blocked) exclusion verification.
   - Suppression states (active cooling states 1, 2, 3 and HeatPumpState.COOLING).
   - Missing keys, empty dicts, translation unit and state formatting.
3. cop:
   - Extreme values: 0 kW input, negative thermal output in heating vs cooling cycle,
     defrosting, huge COP, very small positive inputs.
   - Raw float precision preservation and suggested_display_precision checks.
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import pytest

from custom_components.weheat.const import DISPLAY_PRECISION_COP
from custom_components.weheat.models import (
    COOLING_START_CONDITION_BITS,
    HeatPumpInfo,
    HeatPumpLog,
    HeatPumpState,
)
from custom_components.weheat.sensor import (
    COOLING_CONDITIONS_NOT_COUNTED,
    COOLING_SENSORS,
    SENSORS,
    WeheatHeatPumpSensor,
)
from tests.conftest import load_fixture

STRINGS_PATH = Path("custom_components/weheat/strings.json")
EN_TRANSLATION_PATH = Path("custom_components/weheat/translations/en.json")


# ===========================================================================
# 1. Adversarial Probe: cooling_wait_until & cooling_available_from
# ===========================================================================


class TestCoolingWaitUntilAdversarial:
    """Stress tests for cooling_wait_until and HeatPumpLog.cooling_available_from."""

    @pytest.fixture
    def wait_desc(self):
        desc_map = {desc.key: desc for desc in COOLING_SENSORS}
        return desc_map["cooling_wait_until"]

    def test_cooling_available_from_iso_formats(self) -> None:
        """Verify cooling_available_from correctly parses diverse ISO 8601 timestamps into UTC."""
        log = HeatPumpLog(heat_pump_id="hp-adv-1")

        # 1. Standard UTC with 'Z'
        log.last_cooling_time = "2026-10-31T12:00:00Z"
        log.cooling_exponential_backoff = 10
        expected_utc = datetime.datetime(2026, 10, 31, 12, 10, tzinfo=datetime.UTC)
        assert log.cooling_available_from == expected_utc
        assert log.cooling_available_from.tzinfo == datetime.UTC

        # 2. ISO with explicit '+00:00'
        log.last_cooling_time = "2026-10-31T12:00:00+00:00"
        log.cooling_exponential_backoff = 0
        expected_utc_zero = datetime.datetime(2026, 10, 31, 12, 0, tzinfo=datetime.UTC)
        assert log.cooling_available_from == expected_utc_zero
        assert log.cooling_available_from.tzinfo == datetime.UTC

        # 3. ISO with positive offset (+02:00)
        log.last_cooling_time = "2026-10-31T14:00:00+02:00"
        log.cooling_exponential_backoff = 15
        expected_offset = datetime.datetime(
            2026,
            10,
            31,
            14,
            15,
            tzinfo=datetime.timezone(datetime.timedelta(hours=2)),
        )
        res = log.cooling_available_from
        assert res == expected_offset
        assert res.astimezone(datetime.UTC) == datetime.datetime(
            2026, 10, 31, 12, 15, tzinfo=datetime.UTC
        )

        # 4. ISO with negative offset (-05:00)
        log.last_cooling_time = "2026-10-31T07:00:00-05:00"
        log.cooling_exponential_backoff = 5
        res_neg = log.cooling_available_from
        assert res_neg.astimezone(datetime.UTC) == datetime.datetime(
            2026, 10, 31, 12, 5, tzinfo=datetime.UTC
        )

        # 5. Naive ISO timestamp string (no offset specified) -> must attach UTC tzinfo
        log.last_cooling_time = "2026-10-31T12:00:00"
        log.cooling_exponential_backoff = 20
        res_naive = log.cooling_available_from
        assert res_naive is not None
        assert res_naive.tzinfo == datetime.UTC
        assert res_naive == datetime.datetime(2026, 10, 31, 12, 20, tzinfo=datetime.UTC)

        # 6. Fractional seconds with 'Z'
        log.last_cooling_time = "2026-10-31T12:00:00.654321Z"
        log.cooling_exponential_backoff = 5
        res_frac = log.cooling_available_from
        assert res_frac == datetime.datetime(
            2026, 10, 31, 12, 5, 0, 654321, tzinfo=datetime.UTC
        )

    def test_cooling_available_from_datetime_objects(self) -> None:
        """Verify handling of direct datetime objects (both naive and aware)."""
        log = HeatPumpLog(heat_pump_id="hp-adv-2")
        log.cooling_exponential_backoff = 10

        # Naive datetime -> replaced with UTC
        naive_dt = datetime.datetime(2026, 10, 31, 8, 0, 0)
        log.last_cooling_time = naive_dt  # type: ignore[assignment]
        res = log.cooling_available_from
        assert res is not None
        assert res.tzinfo == datetime.UTC
        assert res == datetime.datetime(2026, 10, 31, 8, 10, 0, tzinfo=datetime.UTC)

        # Aware UTC datetime
        aware_utc = datetime.datetime(2026, 10, 31, 8, 0, 0, tzinfo=datetime.UTC)
        log.last_cooling_time = aware_utc  # type: ignore[assignment]
        assert log.cooling_available_from == datetime.datetime(
            2026, 10, 31, 8, 10, 0, tzinfo=datetime.UTC
        )

    def test_cooling_available_from_temporal_states(self) -> None:
        """Verify behavior when availability is in past, present, or future."""
        log = HeatPumpLog(heat_pump_id="hp-adv-3")
        now_utc = datetime.datetime.now(datetime.UTC)

        # Past: cooling cycle was 2 hours ago, backoff was 10 min -> available 1h50m ago
        past_time = now_utc - datetime.timedelta(hours=2)
        log.last_cooling_time = past_time.isoformat()
        log.cooling_exponential_backoff = 10
        available_past = log.cooling_available_from
        assert available_past is not None
        assert available_past < now_utc
        assert available_past == past_time.replace(
            tzinfo=datetime.UTC
        ) + datetime.timedelta(minutes=10)

        # Present: cooling cycle was 15 min ago, backoff was 15 min -> available right now
        present_time = now_utc - datetime.timedelta(minutes=15)
        log.last_cooling_time = present_time.isoformat()
        log.cooling_exponential_backoff = 15
        available_present = log.cooling_available_from
        assert available_present is not None
        diff_seconds = abs((available_present - now_utc).total_seconds())
        assert diff_seconds < 1.0

        # Future: cooling cycle just finished (now), backoff 45 min -> available in 45 min
        log.last_cooling_time = now_utc.isoformat()
        log.cooling_exponential_backoff = 45
        available_future = log.cooling_available_from
        assert available_future is not None
        assert available_future > now_utc
        assert available_future == now_utc.replace(
            tzinfo=datetime.UTC
        ) + datetime.timedelta(minutes=45)

    def test_cooling_available_from_invalid_and_edge_inputs(self) -> None:
        """Stress-test corrupt, None, and edge inputs to ensure robust fallback to None."""
        log = HeatPumpLog(heat_pump_id="hp-adv-4")

        # 1. None last_cooling_time
        log.last_cooling_time = None
        log.cooling_exponential_backoff = 10
        assert log.cooling_available_from is None

        # 2. Empty string
        log.last_cooling_time = ""
        assert log.cooling_available_from is None

        # 3. Whitespace string
        log.last_cooling_time = "    "
        assert log.cooling_available_from is None

        # 4. Malformed string inputs
        for corrupt in (
            "not-a-date",
            "2026-99-99T99:99:99Z",
            "2026-02-30T10:00:00Z",
            "null",
            "undefined",
            "0",
        ):
            log.last_cooling_time = corrupt
            assert log.cooling_available_from is None

        # 5. Non-string / non-datetime types
        for non_string in (12345, 12.34, [2026, 10, 31], {"time": "now"}):
            log.last_cooling_time = non_string  # type: ignore[assignment]
            assert log.cooling_available_from is None

        # 6. Backoff None or negative
        log.last_cooling_time = "2026-10-31T12:00:00Z"
        log.cooling_exponential_backoff = None
        assert log.cooling_available_from == datetime.datetime(
            2026, 10, 31, 12, 0, 0, tzinfo=datetime.UTC
        )

        log.cooling_exponential_backoff = -10
        assert log.cooling_available_from == datetime.datetime(
            2026, 10, 31, 11, 50, 0, tzinfo=datetime.UTC
        )

    def test_cooling_wait_until_value_fn_matrix(self, wait_desc) -> None:
        """Verify cooling_wait_until sensor value_fn across all condition permutations."""
        log = HeatPumpLog(heat_pump_id="hp-adv-5")
        log.last_cooling_time = "2026-10-31T10:00:00Z"
        log.cooling_exponential_backoff = 20
        expected_dt = datetime.datetime(2026, 10, 31, 10, 20, 0, tzinfo=datetime.UTC)

        # 1. When cooling_start_conditions is None -> returns None
        log.cooling_start_conditions_raw = None
        assert wait_desc.value_fn(log) is None

        # 2. When exponential_backoff bit (512) is SET (condition is met) -> returns None
        log.cooling_start_conditions_raw = 512
        assert wait_desc.value_fn(log) is None

        # 3. When exponential_backoff bit (512) is NOT set (waiting on backoff) -> returns future datetime
        log.cooling_start_conditions_raw = 0
        assert wait_desc.value_fn(log) == expected_dt

        # 4. When all other condition bits except 512 are set (2047 - 512 = 1535) -> returns future datetime
        log.cooling_start_conditions_raw = 1535
        assert wait_desc.value_fn(log) == expected_dt

        # 5. When all bits including 512 are set (2047) -> returns None
        log.cooling_start_conditions_raw = 2047
        assert wait_desc.value_fn(log) is None

    def test_cooling_available_from_leap_years_and_dst(self) -> None:
        """Verify date transitions (leap years, year end, DST offsets)."""
        log = HeatPumpLog(heat_pump_id="hp-adv-6")

        # Leap year leap day transition
        log.last_cooling_time = "2024-02-29T23:55:00Z"
        log.cooling_exponential_backoff = 10
        assert log.cooling_available_from == datetime.datetime(
            2024, 3, 1, 0, 5, 0, tzinfo=datetime.UTC
        )

        # Year end rollover
        log.last_cooling_time = "2026-12-31T23:50:00Z"
        log.cooling_exponential_backoff = 20
        assert log.cooling_available_from == datetime.datetime(
            2027, 1, 1, 0, 10, 0, tzinfo=datetime.UTC
        )

        # Extreme timezone offset (+14:00 Kiribati)
        log.last_cooling_time = "2026-10-31T12:00:00+14:00"
        log.cooling_exponential_backoff = 10
        assert log.cooling_available_from.astimezone(datetime.UTC) == datetime.datetime(
            2026, 10, 30, 22, 10, 0, tzinfo=datetime.UTC
        )


# ===========================================================================
# 2. Adversarial Probe: cooling_conditions_met
# ===========================================================================


class TestCoolingConditionsMetAdversarial:
    """Stress tests for cooling_conditions_met sensor logic and translations."""

    @pytest.fixture
    def cond_desc(self):
        desc_map = {desc.key: desc for desc in COOLING_SENSORS}
        return desc_map["cooling_conditions_met"]

    def test_cooling_conditions_met_0_to_9_spectrum(self, cond_desc) -> None:
        """Systematically verify exact tally from 0 to 9 conditions met."""
        # Identified counted conditions (9 total)
        counted_bits = [
            bit
            for name, bit in COOLING_START_CONDITION_BITS.items()
            if name not in COOLING_CONDITIONS_NOT_COUNTED
        ]
        assert len(counted_bits) == 9

        log = HeatPumpLog(heat_pump_id="hp-cond-1")
        log.cooling_status = 0
        log.state = HeatPumpState.STANDBY

        # 0 conditions met
        log.cooling_start_conditions_raw = 0
        assert cond_desc.value_fn(log) == 0

        # Incrementally enable 1 through 9 conditions
        cumulative_mask = 0
        for count, bit in enumerate(counted_bits, start=1):
            cumulative_mask |= bit
            log.cooling_start_conditions_raw = cumulative_mask
            assert cond_desc.value_fn(log) == count

    def test_cooling_conditions_met_uncounted_bits(self, cond_desc) -> None:
        """Verify uncounted conditions (control_method=1, contact_not_blocked=256) never alter tally."""
        log = HeatPumpLog(heat_pump_id="hp-cond-2")
        log.cooling_status = 0
        log.state = HeatPumpState.STANDBY

        # Only uncounted bits set -> tally must be 0
        uncounted_mask = (
            COOLING_START_CONDITION_BITS["control_method"]
            | (COOLING_START_CONDITION_BITS["contact_not_blocked"])
        )
        assert uncounted_mask == (1 | 256)  # 257
        log.cooling_start_conditions_raw = uncounted_mask
        assert cond_desc.value_fn(log) == 0

        # All 11 bits set (2047) -> tally must be 9, NOT 11
        all_bits_mask = (1 << 11) - 1  # 2047
        log.cooling_start_conditions_raw = all_bits_mask
        assert cond_desc.value_fn(log) == 9

        # Toggle uncounted bits on/off while 5 counted bits are active
        five_counted = (
            COOLING_START_CONDITION_BITS["dtc"]
            | COOLING_START_CONDITION_BITS["outside_air_temperature"]
            | COOLING_START_CONDITION_BITS["inside_temperature"]
            | COOLING_START_CONDITION_BITS["indoor_unit_connected"]
            | COOLING_START_CONDITION_BITS["demand"]
        )
        log.cooling_start_conditions_raw = five_counted
        assert cond_desc.value_fn(log) == 5

        # Add control_method bit -> still 5
        log.cooling_start_conditions_raw = (
            five_counted | (COOLING_START_CONDITION_BITS["control_method"])
        )
        assert cond_desc.value_fn(log) == 5

        # Add contact_not_blocked bit -> still 5
        log.cooling_start_conditions_raw = (
            five_counted
            | COOLING_START_CONDITION_BITS["control_method"]
            | COOLING_START_CONDITION_BITS["contact_not_blocked"]
        )
        assert cond_desc.value_fn(log) == 5

    def test_cooling_conditions_met_suppression_active_cooling(self, cond_desc) -> None:
        """Verify conditions_met returns None during active cooling cycles."""
        log = HeatPumpLog(heat_pump_id="hp-cond-3")
        log.cooling_start_conditions_raw = 2047

        # 1. When cooling_status in (1, 2, 3) -> None
        for active_status in (1, 2, 3):
            log.cooling_status = active_status
            log.state = HeatPumpState.STANDBY
            assert cond_desc.value_fn(log) is None

        # 2. When cooling_status is 0 or other status but state is COOLING -> None
        log.cooling_status = 0
        log.state = HeatPumpState.COOLING
        assert cond_desc.value_fn(log) is None

        # 3. When cooling_status is None and state is COOLING -> None
        log.cooling_status = None
        log.state = HeatPumpState.COOLING
        assert cond_desc.value_fn(log) is None

        # 4. In non-cooling state (HEATING, STANDBY, DHW) and cooling_status not in (1, 2, 3) -> returns tally
        for inactive_state in (
            HeatPumpState.STANDBY,
            HeatPumpState.HEATING,
            HeatPumpState.DHW,
            HeatPumpState.DEFROSTING,
        ):
            log.state = inactive_state
            log.cooling_status = 0
            assert cond_desc.value_fn(log) == 9

    def test_cooling_conditions_met_missing_and_corrupt_data(self, cond_desc) -> None:
        """Verify behavior with missing raw data or corrupt bitmask."""
        log = HeatPumpLog(heat_pump_id="hp-cond-4")

        # None raw bitmask -> returns None
        log.cooling_start_conditions_raw = None
        assert cond_desc.value_fn(log) is None

    def test_cooling_conditions_met_exhaustive_2048_combinations(
        self, cond_desc
    ) -> None:
        """Exhaustively verify all 2^11 = 2048 bitmask combinations against independent oracle."""
        log = HeatPumpLog(heat_pump_id="hp-cond-oracle")
        log.cooling_status = 0
        log.state = HeatPumpState.STANDBY

        counted_items = [
            (name, bit)
            for name, bit in COOLING_START_CONDITION_BITS.items()
            if name not in COOLING_CONDITIONS_NOT_COUNTED
        ]

        for mask in range(1 << 11):  # 0 to 2047
            expected_count = sum(bool(mask & bit) for _, bit in counted_items)
            log.cooling_start_conditions_raw = mask
            assert cond_desc.value_fn(log) == expected_count, (
                f"Mismatch at mask={mask:#05x}: expected {expected_count}, got {cond_desc.value_fn(log)}"
            )

    def test_cooling_conditions_met_mocked_dict_missing_keys(self, cond_desc) -> None:
        """Verify behavior if cooling_start_conditions dict has missing keys or partial items."""

        class MockStatus:
            cooling_status = 0
            state = HeatPumpState.STANDBY
            cooling_start_conditions: dict[str, bool] | None = None

        mock_st = MockStatus()

        # Empty dict -> 0
        mock_st.cooling_start_conditions = {}
        assert cond_desc.value_fn(mock_st) == 0

        # Only one counted condition
        mock_st.cooling_start_conditions = {"demand": True}
        assert cond_desc.value_fn(mock_st) == 1

        # Only uncounted conditions
        mock_st.cooling_start_conditions = {
            "control_method": True,
            "contact_not_blocked": True,
        }
        assert cond_desc.value_fn(mock_st) == 0

        # Mix of counted and uncounted
        mock_st.cooling_start_conditions = {
            "control_method": True,
            "demand": True,
            "water_temperature": False,
            "dtc": True,
        }
        assert (
            cond_desc.value_fn(mock_st) == 2
        )  # demand and dtc are True; water_temperature is False

    def test_cooling_conditions_met_translation_metadata(self) -> None:
        """Verify strings.json and en.json define unit_of_measurement='of 9' and name."""
        with STRINGS_PATH.open(encoding="utf-8") as f:
            strings = json.load(f)["entity"]["sensor"]["cooling_conditions_met"]
        with EN_TRANSLATION_PATH.open(encoding="utf-8") as f:
            en = json.load(f)["entity"]["sensor"]["cooling_conditions_met"]

        assert strings["name"] == "Cooling conditions met"
        assert strings["unit_of_measurement"] == "of 9"
        assert en["name"] == "Cooling conditions met"
        assert en["unit_of_measurement"] == "of 9"

    def test_cooling_conditions_met_ha_sensor_entity_rendering(self) -> None:
        """Verify WeheatHeatPumpSensor entity reports native_value as integer without hardcoded unit."""
        data_hp = load_fixture("heat_pumps.json")["data"][0]
        hp_info = HeatPumpInfo.from_api_dict(data_hp)

        log = HeatPumpLog(heat_pump_id="hp-cond-5")
        log.cooling_status = 0
        log.state = HeatPumpState.STANDBY
        log.cooling_start_conditions_raw = 2047  # all 9 counted bits met

        class DummyCoordinator:
            data = log

        desc_map = {desc.key: desc for desc in COOLING_SENSORS}
        desc = desc_map["cooling_conditions_met"]

        sensor = WeheatHeatPumpSensor(hp_info, DummyCoordinator(), desc)  # type: ignore[arg-type]
        assert sensor.native_value == 9
        # native_unit_of_measurement is None on entity_description so HA loads 'of 9' from translation string
        assert sensor.entity_description.native_unit_of_measurement is None


# ===========================================================================
# 3. Adversarial Probe: cop
# ===========================================================================


class TestCopAdversarial:
    """Stress tests for COP extreme values, thermodynamic cycle directions, and float precision."""

    @pytest.fixture
    def cop_desc(self):
        desc_map = {desc.key: desc for desc in SENSORS}
        return desc_map["cop"]

    def test_cop_zero_and_negative_input_power(self, cop_desc) -> None:
        """Verify COP handles 0 W and negative power input safely without division by zero."""
        log = HeatPumpLog(heat_pump_id="hp-cop-1")
        log.cm_mass_power_out = 3500.0
        log.p_compressor_out = 3500.0
        log.state = HeatPumpState.HEATING

        # Exactly 0.0 W input power
        log.cm_mass_power_in = 0.0
        log.p_compressor_in = 0.0
        assert cop_desc.value_fn(log) == 0.0
        assert log.cop == 0.0

        # Negative input power (sensor noise / calibration drift)
        for neg_in in (-0.1, -1.0, -100.0, -5000.0):
            log.cm_mass_power_in = neg_in
            log.p_compressor_in = neg_in
            assert cop_desc.value_fn(log) == 0.0
            assert log.cop == 0.0

    def test_cop_none_inputs(self, cop_desc) -> None:
        """Verify COP returns None if either input or output power is None."""
        log = HeatPumpLog(heat_pump_id="hp-cop-2")

        # Missing input
        log.cm_mass_power_in = None
        log.p_compressor_in = None
        log.cm_mass_power_out = 3000.0
        log.p_compressor_out = 3000.0
        assert cop_desc.value_fn(log) is None
        assert log.cop is None

        # Missing output
        log.cm_mass_power_in = 1000.0
        log.p_compressor_in = 1000.0
        log.cm_mass_power_out = None
        log.p_compressor_out = None
        assert cop_desc.value_fn(log) is None
        assert log.cop is None

    def test_cop_heating_and_cooling_directionality(self, cop_desc) -> None:
        """Verify sign and ratio conventions in heating vs cooling vs defrosting."""
        log = HeatPumpLog(heat_pump_id="hp-cop-3")
        log.cm_mass_power_in = 1000.0
        log.p_compressor_in = 1000.0

        # 1. Normal Heating: positive output -> positive COP
        log.state = HeatPumpState.HEATING
        log.cm_mass_power_out = 4200.0
        log.p_compressor_out = 4200.0
        assert cop_desc.value_fn(log) == 4.2

        # 2. Heating with negative output (system losing heat) -> negative COP
        log.state = HeatPumpState.HEATING
        log.cm_mass_power_out = -1500.0
        log.p_compressor_out = -1500.0
        assert cop_desc.value_fn(log) == -1.5

        # 3. Normal Cooling: extracting heat from water (negative output) -> POSITIVE COP
        log.state = HeatPumpState.COOLING
        log.cm_mass_power_out = -3200.0
        log.p_compressor_out = -3200.0
        assert cop_desc.value_fn(log) == 3.2

        # 4. Cooling with positive output (heat injected into water, wrong direction) -> NEGATIVE COP
        log.state = HeatPumpState.COOLING
        log.cm_mass_power_out = 1000.0
        log.p_compressor_out = 1000.0
        assert cop_desc.value_fn(log) == -1.0

        # 5. Defrosting: heat extracted from water to melt coil ice -> negative COP (energy loss)
        log.state = HeatPumpState.DEFROSTING
        log.cm_mass_power_out = -2500.0
        log.p_compressor_out = -2500.0
        assert cop_desc.value_fn(log) == -2.5

        # 6. Unknown / None state -> reports raw ratio as measured
        log.state = None
        log.cm_mass_power_out = -2000.0
        log.p_compressor_out = -2000.0
        assert cop_desc.value_fn(log) == -2.0

    def test_cop_extreme_values_and_float_precision(self, cop_desc) -> None:
        """Verify very high COP, very small inputs, and raw unrounded float fidelity."""
        log = HeatPumpLog(heat_pump_id="hp-cop-4")
        log.state = HeatPumpState.HEATING

        # Very high output / very small positive input
        log.cm_mass_power_in = 0.01  # 10 milliwatts
        log.p_compressor_in = 0.01
        log.cm_mass_power_out = 10000.0
        log.p_compressor_out = 10000.0
        assert cop_desc.value_fn(log) == 1_000_000.0

        # Repeating/periodic floating point ratio: 10000 / 3 = 3333.3333333333335
        log.cm_mass_power_in = 3.0
        log.p_compressor_in = 3.0
        log.cm_mass_power_out = 10.0
        log.p_compressor_out = 10.0
        res = cop_desc.value_fn(log)
        assert isinstance(res, float)
        assert res == pytest.approx(10.0 / 3.0, rel=1e-12)
        # Ensure it was NOT rounded to 3.3 or 3.33 in python
        assert res != 3.3
        assert res != 3.33

        # Suggested display precision must be 1 for UI presentation
        assert cop_desc.suggested_display_precision == DISPLAY_PRECISION_COP
        assert DISPLAY_PRECISION_COP == 1

    def test_cop_cartesian_product_matrix(self, cop_desc) -> None:
        """Stress-test cartesian product of power inputs, outputs, and all HeatPumpState values."""
        inputs = [-500.0, -0.01, 0.0, 0.001, 1.0, 500.0, 1500.0, 10000.0]
        outputs = [-5000.0, -100.0, 0.0, 100.0, 4500.0, 20000.0]
        states = list(HeatPumpState) + [None]

        log = HeatPumpLog(heat_pump_id="hp-cop-matrix")

        for p_in in inputs:
            for p_out in outputs:
                for st in states:
                    log.cm_mass_power_in = p_in
                    log.p_compressor_in = p_in
                    log.cm_mass_power_out = p_out
                    log.p_compressor_out = p_out
                    log.state = st

                    val = cop_desc.value_fn(log)
                    assert val is not None
                    if p_in <= 0.0:
                        assert val == 0.0
                    else:
                        expected = p_out / p_in
                        if st == HeatPumpState.COOLING:
                            expected *= -1.0
                        assert val == pytest.approx(expected, rel=1e-9)
