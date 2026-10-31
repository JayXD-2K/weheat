"""Data models and enums for the Weheat custom Home Assistant integration."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import IntEnum
from typing import Any

from .const import DAYS_OF_WEEK

# Numeric states observed during defrost cycles
DEFROST_STATES: frozenset[int] = frozenset({84, 90, 100, 110, 120, *range(200, 240)})

# Numeric states observed during cooling cycles
COOLING_STATES: frozenset[int] = frozenset(range(130, 138))

# Bit masks for diagnostic cooling start conditions
COOLING_START_CONDITION_BITS: dict[str, int] = {
    "control_method": 1,
    "dtc": 2,
    "outside_air_temperature": 4,
    "inside_temperature": 8,
    "indoor_unit_connected": 16,
    "water_to_air": 32,
    "demand": 64,
    "water_temperature": 128,
    "contact_not_blocked": 256,
    "exponential_backoff": 512,
    "heat_cool_delay": 1024,
}


def pwm_to_volume(pwm: float | int | None, max_vol: float = 2.1) -> float | None:
    """Calculate PWM duty cycle (0-100) to water flow volume in m³/h.

    Flow sensor characteristic:
    - PWM < 0 or PWM > 75: abnormal state (e.g. 255 = Off), maps to None.
    - PWM <= 5: standby, maps to 0.0 m³/h.
    - PWM 5..75: linear from 0 to max_vol (2.1 m³/h).
    """
    if pwm is None:
        return None
    if pwm < 0 or pwm > 75:
        return None
    if pwm <= 5:
        return 0.0
    return round(((pwm - 5) / 70.0) * max_vol, 2)


class HeatPumpState(IntEnum):
    """Heat pump operating states decoded from raw telemetry."""

    OFFLINE = 1
    STANDBY = 40
    HEATING = 70
    DEFROSTING = 110
    COOLING = 132
    WATER_CHECK = 136
    DHW = 150
    LEGIONELLA_PREVENTION = 160
    SELF_TEST = 170
    MANUAL_CONTROL = 180
    UPDATING = 1010

    @classmethod
    def from_api_state(cls, numeric_state: int | None) -> HeatPumpState | None:
        """Decode raw integer state from telemetry into HeatPumpState enum."""
        if numeric_state is None:
            return None
        if numeric_state == 1:
            return cls.OFFLINE
        if numeric_state == 40:
            return cls.STANDBY
        if numeric_state == 70:
            return cls.HEATING
        if numeric_state == 136:
            return cls.WATER_CHECK
        if numeric_state in COOLING_STATES:
            return cls.COOLING
        if numeric_state == 150:
            return cls.DHW
        if numeric_state == 160:
            return cls.LEGIONELLA_PREVENTION
        if numeric_state == 170:
            return cls.SELF_TEST
        if numeric_state == 180:
            return cls.MANUAL_CONTROL
        if numeric_state == 1010:
            return cls.UPDATING
        if numeric_state in DEFROST_STATES:
            return cls.DEFROSTING
        return None


class PerformanceModePreset(IntEnum):
    """Quiet Mode / Performance Mode daytime preset levels (0 to 4).

    In the Weheat Portal UI, this corresponds to the "Quiet level" daytime slider,
    ranging from maximum efficiency / maximum heating power ("Extra savings") to
    maximum noise reduction ("Extra quiet") by progressively capping compressor and fan RPM:
    - 0 (PERFORMANCE): "Extra savings" / Maximum power. Unrestricted compressor and fan RPM.
    - 1 (SILENT_1): Quiet level 1. Mild noise reduction, slight compressor/fan speed limit.
    - 2 (SILENT_2): Quiet level 2. Moderate noise reduction, balanced quietness and capacity.
    - 3 (SILENT_3): Quiet level 3. High noise reduction, lower compressor/fan speed limit.
    - 4 (SILENT_4): "Extra quiet" / Quiet level 4. Maximum daytime noise reduction, lowest daytime power limit.
    """

    PERFORMANCE = 0  # Extra savings / Maximum power (unrestricted)
    SILENT_1 = 1  # Quiet level 1 (mild noise reduction)
    SILENT_2 = 2  # Quiet level 2 (moderate noise reduction)
    SILENT_3 = 3  # Quiet level 3 (high noise reduction)
    SILENT_4 = 4  # Extra quiet / Quiet level 4 (maximum daytime noise reduction)


class SilentHoursScheduledPreset(IntEnum):
    """Silent Hours schedule night limit preset levels (0 to 7).

    In the Weheat Portal UI, this corresponds to the Night Limit preset applied
    during scheduled silent hours:
    - 0 (OFF): Scheduled quiet hours disabled / inactive.
    - 1 (SILENT_1): Night limit level 1 (mildest night limit / highest night heating capacity).
    - 2 (SILENT_2): Night limit level 2 (moderate night noise cap).
    - 3 (SILENT_3): Night limit level 3 (high night noise cap).
    - 4 (SILENT_4): Night limit level 4 (very quiet night limit).
    - 5 (OFF_ALT): Alternative off value (equivalent to schedule disabled).
    - 6 (SILENT_6): Night limit level 6 (ultra quiet night limit).
    - 7 (SILENT_7): Night limit level 7 (maximum quietness / lowest night power limit).

    Firmware Constraint:
    When Night Limit is active (preset not in {0, 5}), the control board firmware
    strictly enforces: SilentHoursScheduledPreset > PerformanceModePreset.
    """

    OFF = 0  # Schedule disabled
    SILENT_1 = 1  # Level 1 night limit (highest night power)
    SILENT_2 = 2  # Level 2 night limit
    SILENT_3 = 3  # Level 3 night limit
    SILENT_4 = 4  # Level 4 night limit
    OFF_ALT = 5  # Alternative disabled code
    SILENT_6 = 6  # Level 6 night limit (ultra quiet)
    SILENT_7 = 7  # Level 7 night limit (maximum quietness)


class SettingsRequestStatus(IntEnum):
    """Asynchronous settings mutation request status returned by the portal."""

    PENDING = 0
    EXECUTING = 1
    SUCCEEDED = 2
    CANCELLED = 3
    FAILED = 4
    SUCCEEDED_WITH_HEAT_PUMP_SETTINGS_MISMATCH = 5
    FAILED_DEVICE_WRONG_STATE = 6
    FAILED_WRONG_EXECUTION_ORDER = 7
    FAILED_DEVICE_DISCONNECTED = 8
    FAILED_SETTINGS_REQUEST_MISMATCH = 9
    FAILED_SETTINGS_KEY_NOT_FOUND_IN_SDF = 10
    FAILED_ANOTHER_SETTINGS_REQUEST_EXECUTING = 11

    @property
    def is_resolved(self) -> bool:
        """Return True if the request is no longer pending or executing."""
        return self not in (self.PENDING, self.EXECUTING)

    @property
    def is_successful(self) -> bool:
        """Return True if the request succeeded."""
        return self in (self.SUCCEEDED, self.SUCCEEDED_WITH_HEAT_PUMP_SETTINGS_MISMATCH)


class DhwControlMethod(IntEnum):
    """Domestic Hot Water control methods reported by the heat pump."""

    NONE = 0
    FIXED = 1
    SCHEDULE = 2
    WEHEAT_INTELLIGENCE = 3
    BOOST = 4


class CoolingPauseReason(IntEnum):
    """Reasons why cooling is currently paused."""

    NONE = 0
    ROOM_TEMPERATURE_TOO_LOW = 1
    OUTSIDE_TEMPERATURE_TOO_LOW = 2
    OUTSIDE_COLDER_THAN_WATER_TEMPERATURE = 3
    WATER_TEMPERATURE_BELOW_SETPOINT = 4
    HEAT_PUMP_CONTROL = 5
    WATER_TEMPERATURE_BELOW_DEWPOINT = 6
    CONTACT_BLOCKED = 7
    DEMAND = 8


class CoolingStopReason(IntEnum):
    """Reasons why cooling is stopped."""

    NONE = 0
    DTC = 1
    CONTROL_METHOD = 2
    NO_INDOOR_UNIT_COMMUNICATION = 3
    HEAT_PUMP_CONTROL = 4
    COOLING_CONTROL = 5
    CONTACT_SWITCH_OVER = 6
    THERMOSTAT_DISABLED = 7


class CoolingStatus(IntEnum):
    """Cooling status reported in the telemetry log."""

    IDLE = 0
    STARTING = 1
    ACTIVE = 2
    STOPPING = 3
    STANDBY = 4


class ControlMethod(IntEnum):
    """Current central heating control method."""

    NONE = 0
    HEATING_CURVE = 1
    ON_OFF_THERMOSTAT = 2
    BASE_OPEN_THERM = 3
    SMART_OPEN_THERM = 4
    CLOUD_THERMOSTAT = 5
    MANUAL_POWER = 6
    AUTO_INPUT_SELECT = 7
    MANUAL_SETPOINT = 8


@dataclass
class HeatPumpInfo:
    """Strongly typed heat pump discovery metadata and capabilities."""

    heatpump_id: str
    name: str
    model: int
    serial_number: str
    has_dhw: bool = False
    has_ch_boiler: bool = False
    is_online: bool = True
    control_board_id: str | None = None
    firmware_version: str | None = None
    part_number: str | None = None
    installation_id: str | None = None
    commissioned_at: str | None = None

    @property
    def uuid(self) -> str:
        """Alias for heatpump_id for compatibility."""
        return self.heatpump_id

    @property
    def device_name(self) -> str:
        """Alias for name for compatibility."""
        return self.name

    @property
    def sn(self) -> str:
        """Alias for serial_number for compatibility."""
        return self.serial_number

    @property
    def readable_name(self) -> str:
        """Human-readable display name for Home Assistant device registry."""
        return self.name or self.model_name

    @property
    def model_name(self) -> str:
        """Return the commercial model string based on model number."""
        if self.model == 0:
            return "Blackbird P80 heat pump"
        if self.model == 1:
            return "Blackbird P60 heat pump"
        if 2 <= self.model <= 4:
            return "Sparrow P60 heat pump"
        if self.model == 5:
            return "Flint P40 heat pump"
        return f"Weheat heat pump (Model {self.model})"

    @property
    def nominal_rpm(self) -> int:
        """Return nominal compressor RPM (100% capacity) for percentage scaling."""
        if self.model == 1:
            return 5520
        if 2 <= self.model <= 4:
            return 5520
        if self.model == 5:
            return 5400
        return 4500

    @classmethod
    def from_api_dict(cls, data: dict[str, Any]) -> HeatPumpInfo:
        """Instantiate HeatPumpInfo from Weheat API JSON item.

        Handles standard heat pump dictionary as well as nested structures returned
        by /installations/fully-expanded.
        """
        hp_nested = data.get("heatPump")
        hp_dict = hp_nested if isinstance(hp_nested, dict) else {}

        heatpump_id = str(
            hp_dict.get("id") or data.get("heatPumpId") or data.get("id") or ""
        )
        control_board_id = hp_dict.get("controlBoardId") or data.get("controlBoardId")
        installation_id = data.get("installationId") or (
            data.get("id") if hp_dict and data.get("id") != heatpump_id else None
        )

        name = hp_dict.get("name") or data.get("name", "Weheat Heat Pump")
        model = (
            hp_dict.get("model")
            if hp_dict.get("model") is not None
            else data.get("model", 0)
        )
        serial_number = hp_dict.get("serialNumber") or data.get("serialNumber", "")

        dhw_type = (
            hp_dict.get("dhwType")
            if hp_dict.get("dhwType") is not None
            else data.get("dhwType")
        )
        boiler_type = (
            hp_dict.get("boilerType")
            if hp_dict.get("boilerType") is not None
            else data.get("boilerType")
        )
        state = (
            hp_dict.get("state")
            if hp_dict.get("state") is not None
            else data.get("state")
        )

        firmware_version = hp_dict.get("firmwareVersion") or data.get("firmwareVersion")
        part_number = hp_dict.get("partNumber") or data.get("partNumber")
        commissioned_at = hp_dict.get("commissionedAt") or data.get("commissionedAt")

        has_dhw = (
            bool(hp_dict.get("hasDhw") or data.get("hasDhw"))
            if ("hasDhw" in hp_dict or "hasDhw" in data)
            else (dhw_type == 1 if dhw_type is not None else False)
        )

        return cls(
            heatpump_id=heatpump_id,
            name=name,
            model=model,
            serial_number=serial_number,
            has_dhw=has_dhw,
            has_ch_boiler=boiler_type is not None and boiler_type > 0,
            is_online=state != 1 if state is not None else True,
            control_board_id=str(control_board_id) if control_board_id else None,
            firmware_version=firmware_version,
            part_number=part_number,
            installation_id=str(installation_id) if installation_id else None,
            commissioned_at=commissioned_at,
        )


class CompressorPercentage(int):
    """Integer that can also be called as a method for backward compatibility."""

    def __new__(
        cls,
        value: int,
        rpm: float | None = None,
        nominal_rpm: int = 4500,
    ) -> CompressorPercentage:
        obj = super().__new__(cls, value)
        obj._rpm = rpm
        obj._nominal_rpm = nominal_rpm
        return obj

    def __call__(self, nominal_rpm: int | None = None) -> int | None:
        if self._rpm is None:
            return None
        target = nominal_rpm if nominal_rpm is not None else self._nominal_rpm
        if target <= 0:
            return None
        return int((100.0 / target) * self._rpm)


@dataclass
class HeatPumpLog:
    """Strongly typed real-time telemetry snapshot (30+ metrics)."""

    heat_pump_id: str
    nominal_rpm: int = 4500
    timestamp: str | None = None
    is_online: bool = True
    state: HeatPumpState | None = None
    raw_state: int | None = None

    # Temperatures (°C)
    t_water_in: float | None = None
    t_water_out: float | None = None
    t_water_house_in: float | None = None
    t_air_in: float | None = None
    t_air_out: float | None = None
    t_room: float | None = None
    t_room_target: float | None = None
    t_thermostat_setpoint: float | None = None
    t1: float | None = None  # DHW top temperature
    t2: float | None = None  # DHW bottom temperature
    ot_boiler_feed_temperature: float | None = None
    ot_boiler_return_temperature: float | None = None
    t_board: float | None = None
    t_inverter: float | None = None
    t_compressor_in: float | None = None
    t_compressor_out: float | None = None
    indoor_unit_heater_temperature: float | None = None

    # Compressor, Power & Electrical
    rpm: int | None = None
    p_compressor_in: float | None = None
    p_compressor_out: float | None = None
    p_compressor_in_target: float | None = None
    cm_mass_power_in: float | None = None
    cm_mass_power_out: float | None = None
    rpm_limiter: int | None = None
    rpm_limiter_type: int | None = None
    fan: int | None = None
    fan_power: float | None = None
    signal_strength: int | None = None
    sinr: int | None = None
    inverter_input_voltage: float | None = None

    # Water Flow (PWM duty cycles 0-100)
    central_heating_flow: int | None = None
    dhw_flow: int | None = None

    # Control Settings & Thermostat
    control_bridge_status: int | None = None
    current_control_method: int | None = None
    dhw_control_method: int | None = None
    dhw_target_temperature: float | None = None
    on_off_thermostat_state: int | None = None
    thermostat_status: int | None = None
    ot_boiler_status: int | None = None

    # Indoor Unit Actuator Decodes (Binary)
    water_pump_state: bool = False
    gas_boiler_state: bool = False
    electric_heater_state: bool = False
    water_pump2_state: bool = False
    dhw_valve_state: bool = False

    # Cooling Diagnostics
    cooling_status: int | None = None
    cooling_start_conditions_raw: int | None = None
    cooling_exponential_backoff: int | None = None
    cooling_pause_reason: int | None = None
    cooling_stop_reason: int | None = None
    last_cooling_time: str | None = None

    # Error Diagnostics
    error: int | None = None
    error_decoded_dtc_none: bool = True

    # Raw DTO dictionary preservation
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def power_input(self) -> float | None:
        """Electrical input power in Watts."""
        if self.cm_mass_power_in is not None:
            return self.cm_mass_power_in
        return self.p_compressor_in

    @property
    def power_output(self) -> float | None:
        """Hydraulic thermal output power in Watts."""
        if self.cm_mass_power_out is not None:
            return self.cm_mass_power_out
        return self.p_compressor_out

    @property
    def cop(self) -> float | None:
        """Instantaneous Coefficient of Performance (COP)."""
        p_in = self.power_input
        p_out = self.power_output
        if p_in is None or p_out is None:
            return None
        if p_in <= 0:
            return 0.0
        ratio = p_out / p_in
        if self.state == HeatPumpState.COOLING:
            return -1.0 * ratio
        return ratio

    @property
    def compressor_rpm(self) -> float | None:
        """Compressor RPM as a float matching Core sensor expectations."""
        return float(self.rpm) if self.rpm is not None else None

    @property
    def compressor_percentage(self) -> Any:
        """Calculate compressor speed as percentage of nominal power."""
        if self.rpm is None or self.nominal_rpm <= 0:
            return None
        val = int((100.0 / self.nominal_rpm) * self.rpm)
        return CompressorPercentage(val, rpm=self.rpm, nominal_rpm=self.nominal_rpm)

    # Core Sensor Property Mappings
    @property
    def heat_pump_state(self) -> HeatPumpState | None:
        """Operating state enum matching HA Core sensor lambda."""
        return self.state

    @property
    def water_inlet_temperature(self) -> float | None:
        return self.t_water_in

    @property
    def water_outlet_temperature(self) -> float | None:
        return self.t_water_out

    @property
    def ch_inlet_temperature(self) -> float | None:
        return self.t_water_house_in

    @property
    def water_house_in_temperature(self) -> float | None:
        """Alias for t_water_house_in matching HA Core sensor lambda."""
        return self.t_water_house_in

    @property
    def outside_temperature(self) -> float | None:
        return self.t_air_in

    @property
    def air_inlet_temperature(self) -> float | None:
        """Alias for t_air_in matching HA Core sensor lambda."""
        return self.t_air_in

    @property
    def air_outlet_temperature(self) -> float | None:
        return self.t_air_out

    @property
    def thermostat_water_setpoint(self) -> float | None:
        return self.t_thermostat_setpoint

    @property
    def thermostat_room_temperature(self) -> float | None:
        return self.t_room

    @property
    def thermostat_room_temperature_setpoint(self) -> float | None:
        return self.t_room_target

    @property
    def dhw_top_temperature(self) -> float | None:
        return self.t1

    @property
    def dhw_bottom_temperature(self) -> float | None:
        return self.t2

    @property
    def central_heating_flow_volume(self) -> float | None:
        """Central heating flow volume in m³/h."""
        return pwm_to_volume(self.central_heating_flow, max_vol=2.1)

    @property
    def dhw_flow_volume(self) -> float | None:
        """Domestic hot water flow volume in m³/h."""
        return pwm_to_volume(self.dhw_flow, max_vol=2.1)

    # Core Binary Sensor Property Mappings
    @property
    def indoor_unit_water_pump_state(self) -> bool:
        return self.water_pump_state

    @property
    def indoor_unit_auxiliary_pump_state(self) -> bool:
        return self.water_pump2_state

    @property
    def indoor_unit_dhw_valve_or_pump_state(self) -> bool:
        return self.dhw_valve_state

    @property
    def indoor_unit_gas_boiler_state(self) -> bool:
        return self.gas_boiler_state

    @property
    def indoor_unit_electric_heater_state(self) -> bool:
        return self.electric_heater_state

    @property
    def cooling_start_conditions(self) -> dict[str, bool] | None:
        """Bitmask decode of diagnostic cooling start conditions."""
        if self.cooling_start_conditions_raw is None:
            return None
        return {
            name: bool(self.cooling_start_conditions_raw & bit)
            for name, bit in COOLING_START_CONDITION_BITS.items()
        }

    @property
    def cooling_available_from(self) -> datetime | None:
        """The moment when restart delay after the last cooling cycle expires."""
        if not self.last_cooling_time:
            return None
        try:
            if isinstance(self.last_cooling_time, datetime):
                dt = self.last_cooling_time
            else:
                raw = str(self.last_cooling_time).replace("Z", "+00:00")
                dt = datetime.fromisoformat(raw)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=UTC)
            return dt + timedelta(minutes=self.cooling_exponential_backoff or 0)
        except Exception:
            return None

    @classmethod
    def from_api_dict(cls, data: dict[str, Any]) -> HeatPumpLog:
        """Instantiate HeatPumpLog from Weheat latest logs JSON dict."""
        raw_state = data.get("state")
        return cls(
            heat_pump_id=data.get("heatPumpId", ""),
            timestamp=data.get("timestamp"),
            is_online=data.get("isOnline", True),
            state=HeatPumpState.from_api_state(raw_state),
            raw_state=raw_state,
            t_water_in=data.get("tWaterIn"),
            t_water_out=data.get("tWaterOut"),
            t_water_house_in=data.get("tWaterHouseIn"),
            t_air_in=data.get("tAirIn"),
            t_air_out=data.get("tAirOut"),
            t_room=data.get("tRoom"),
            t_room_target=data.get("tRoomTarget"),
            t_thermostat_setpoint=data.get("tThermostatSetpoint"),
            t1=data.get("t1"),
            t2=data.get("t2"),
            ot_boiler_feed_temperature=data.get("otBoilerFeedTemperature"),
            ot_boiler_return_temperature=data.get("otBoilerReturnTemperature"),
            t_board=data.get("tBoard"),
            t_inverter=data.get("tInverter"),
            t_compressor_in=data.get("tCompressorIn"),
            t_compressor_out=data.get("tCompressorOut"),
            indoor_unit_heater_temperature=data.get("indoorUnitHeaterTemperature"),
            rpm=data.get("rpm"),
            p_compressor_in=data.get("pCompressorIn"),
            p_compressor_out=data.get("pCompressorOut"),
            p_compressor_in_target=data.get("pCompressorInTarget"),
            cm_mass_power_in=data.get("cmMassPowerIn"),
            cm_mass_power_out=data.get("cmMassPowerOut"),
            rpm_limiter=data.get("rpmLimiter"),
            rpm_limiter_type=data.get("rpmLimiterType"),
            fan=data.get("fan"),
            fan_power=data.get("fanPower"),
            signal_strength=data.get("signalStrength"),
            sinr=data.get("sinr"),
            inverter_input_voltage=data.get("inverterInputVoltage"),
            central_heating_flow=data.get("centralHeatingFlow"),
            dhw_flow=data.get("dhwFlow"),
            control_bridge_status=data.get("controlBridgeStatus"),
            current_control_method=data.get("currentControlMethod"),
            dhw_control_method=data.get("dhwControlMethod"),
            dhw_target_temperature=data.get("dhwTargetTemperature"),
            on_off_thermostat_state=data.get("onOffThermostatState"),
            thermostat_status=data.get("thermostatStatus"),
            ot_boiler_status=data.get("otBoilerStatus"),
            water_pump_state=bool(
                data.get("controlBridgeStatusDecodedWaterPump", False)
            ),
            gas_boiler_state=bool(
                data.get("controlBridgeStatusDecodedGasBoiler", False)
            ),
            electric_heater_state=bool(
                data.get("controlBridgeStatusDecodedElectricHeater", False)
            ),
            water_pump2_state=bool(
                data.get("controlBridgeStatusDecodedWaterPump2", False)
            ),
            dhw_valve_state=bool(data.get("controlBridgeStatusDecodedDhwValve", False)),
            cooling_status=data.get("coolingStatus"),
            cooling_start_conditions_raw=data.get("coolingStartConditions"),
            cooling_exponential_backoff=data.get("coolingExponentialBackoff"),
            cooling_pause_reason=data.get("coolingPauseReason"),
            cooling_stop_reason=data.get("coolingStopReason"),
            last_cooling_time=data.get("lastCoolingTime"),
            error=data.get("error"),
            error_decoded_dtc_none=bool(data.get("errorDecodedDtcNone", True)),
            raw=data,
        )


@dataclass
class HeatPumpEnergy:
    """Strongly typed cumulative energy registers (kWh)."""

    heatpump_id: str
    total_ein_heating: float = 0.0
    total_ein_standby: float = 0.0
    total_ein_dhw: float = 0.0
    total_ein_heating_defrost: float = 0.0
    total_ein_dhw_defrost: float = 0.0
    total_ein_cooling: float = 0.0
    total_eout_heating: float = 0.0
    total_eout_dhw: float = 0.0
    total_eout_heating_defrost: float = 0.0  # Inherently negative
    total_eout_dhw_defrost: float = 0.0  # Inherently negative
    total_eout_cooling: float = 0.0
    total_ein_iu_standby: float = 0.0
    total_ein_iu_heating: float = 0.0
    total_ein_iu_dhw: float = 0.0
    total_ein_iu_heating_defrost: float = 0.0
    total_ein_iu_dhw_defrost: float = 0.0
    total_ein_iu_cooling: float = 0.0

    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def energy_total(self) -> float:
        """The total used (electrical) energy of the outdoor unit in kWh."""
        return round(
            self.total_ein_heating
            + self.total_ein_dhw
            + self.total_ein_cooling
            + self.total_ein_standby
            + self.total_ein_heating_defrost
            + self.total_ein_dhw_defrost,
            2,
        )

    @property
    def electricity_used(self) -> float:
        """Alias for energy_total matching HA Core translation key."""
        return self.energy_total

    @property
    def energy_in_indoor_unit(self) -> float:
        """The total used (electrical) energy of the indoor unit in kWh."""
        return round(
            self.total_ein_iu_heating
            + self.total_ein_iu_dhw
            + self.total_ein_iu_cooling
            + self.total_ein_iu_standby
            + self.total_ein_iu_heating_defrost
            + self.total_ein_iu_dhw_defrost,
            2,
        )

    @property
    def electricity_used_indoor_unit(self) -> float:
        """Alias for energy_in_indoor_unit matching HA Core translation key."""
        return self.energy_in_indoor_unit

    @property
    def energy_output(self) -> float:
        """The total useful generated energy for the house in kWh."""
        return round(
            self.total_eout_heating
            + self.total_eout_dhw
            + self.total_eout_heating_defrost
            + self.total_eout_dhw_defrost
            + (-self.total_eout_cooling),
            2,
        )

    @property
    def energy_in_heating(self) -> float:
        return self.total_ein_heating

    @property
    def electricity_used_heating(self) -> float:
        return self.energy_in_heating

    @property
    def energy_in_cooling(self) -> float:
        return self.total_ein_cooling

    @property
    def electricity_used_cooling(self) -> float:
        return self.energy_in_cooling

    @property
    def energy_in_defrost(self) -> float:
        """Electricity consumed during defrost cycles (kWh)."""
        return round(self.total_ein_heating_defrost + self.total_ein_dhw_defrost, 2)

    @property
    def electricity_used_defrost(self) -> float:
        return self.energy_in_defrost

    @property
    def energy_in_standby(self) -> float:
        return self.total_ein_standby

    @property
    def electricity_used_standby(self) -> float:
        return self.energy_in_standby

    @property
    def energy_in_dhw(self) -> float:
        return self.total_ein_dhw

    @property
    def electricity_used_dhw(self) -> float:
        return self.energy_in_dhw

    @property
    def energy_out_heating(self) -> float:
        return self.total_eout_heating

    @property
    def energy_output_heating(self) -> float:
        return self.energy_out_heating

    @property
    def energy_out_cooling(self) -> float:
        return self.total_eout_cooling

    @property
    def energy_output_cooling(self) -> float:
        return self.energy_out_cooling

    @property
    def energy_out_defrost(self) -> float:
        """Heat extracted from water loop during defrost (inherently negative kWh)."""
        return round(self.total_eout_heating_defrost + self.total_eout_dhw_defrost, 2)

    @property
    def energy_output_defrost(self) -> float:
        return self.energy_out_defrost

    @property
    def energy_out_dhw(self) -> float:
        return self.total_eout_dhw

    @property
    def energy_output_dhw(self) -> float:
        return self.energy_out_dhw

    @classmethod
    def from_api_dict(cls, data: dict[str, Any]) -> HeatPumpEnergy:
        """Instantiate HeatPumpEnergy from Weheat energy/total JSON dict."""
        return cls(
            heatpump_id=data.get("heatPumpId", ""),
            total_ein_heating=float(data.get("totalEInHeating", 0.0)),
            total_ein_standby=float(data.get("totalEInStandby", 0.0)),
            total_ein_dhw=float(data.get("totalEInDhw", 0.0)),
            total_ein_heating_defrost=float(data.get("totalEInHeatingDefrost", 0.0)),
            total_ein_dhw_defrost=float(data.get("totalEInDhwDefrost", 0.0)),
            total_ein_cooling=float(data.get("totalEInCooling", 0.0)),
            total_eout_heating=float(data.get("totalEOutHeating", 0.0)),
            total_eout_dhw=float(data.get("totalEOutDhw", 0.0)),
            total_eout_heating_defrost=float(data.get("totalEOutHeatingDefrost", 0.0)),
            total_eout_dhw_defrost=float(data.get("totalEOutDhwDefrost", 0.0)),
            total_eout_cooling=float(data.get("totalEOutCooling", 0.0)),
            total_ein_iu_standby=float(data.get("totalEInIUStandby", 0.0)),
            total_ein_iu_heating=float(data.get("totalEInIUHeating", 0.0)),
            total_ein_iu_dhw=float(data.get("totalEInIUDhw", 0.0)),
            total_ein_iu_heating_defrost=float(
                data.get("totalEInIUHeatingDefrost", 0.0)
            ),
            total_ein_iu_dhw_defrost=float(data.get("totalEInIUDhwDefrost", 0.0)),
            total_ein_iu_cooling=float(data.get("totalEInIUCooling", 0.0)),
            raw=data,
        )


@dataclass
class DailySilentSchedule:
    """Silent hours schedule window for a single day of the week."""

    day: str
    start_hour: int = 19
    start_minute: int = 0
    stop_hour: int = 7
    stop_minute: int = 0

    @property
    def start_time_str(self) -> str:
        """Return HH:MM start time string."""
        return f"{self.start_hour:02d}:{self.start_minute:02d}"

    @property
    def stop_time_str(self) -> str:
        """Return HH:MM stop time string."""
        return f"{self.stop_hour:02d}:{self.stop_minute:02d}"


@dataclass
class ControlBoardSettings:
    """Strongly typed control board settings including quiet schedule."""

    performance_mode_preset: int = 0
    silent_hours_preset: int = 0
    dhw_target_temperature: float = 50.0
    schedules: dict[str, DailySilentSchedule] = field(default_factory=dict)
    raw_settings: dict[str, Any] = field(default_factory=dict)

    @property
    def silent_hours_scheduled_preset(self) -> int:
        """Alias for silent_hours_preset to match API naming and legacy callers."""
        return self.silent_hours_preset

    @silent_hours_scheduled_preset.setter
    def silent_hours_scheduled_preset(self, value: int) -> None:
        self.silent_hours_preset = int(value)

    @classmethod
    def from_api_dict(cls, data: dict[str, Any] | None) -> ControlBoardSettings:
        """Parse settings from /api/v1/installations/{id}/settings JSON dict.

        Checks camelCase keys first (live portal API format), then falls back
        to PascalCase keys (fixtures / legacy format). Handles nested 'settings'
        sub-dictionaries as well as flat payloads. Safely handles None data.
        """
        if not isinstance(data, dict):
            return cls()

        nested_settings = data.get("settings")
        if not isinstance(nested_settings, dict):
            nested_settings = data.get("Settings")
        settings_dict: dict[str, Any] = (
            nested_settings if isinstance(nested_settings, dict) else data
        )

        def _get_val(
            camel_key: str, pascal_key: str, default: Any, val_type: type = int
        ) -> Any:
            val = None
            if isinstance(settings_dict, dict):
                val = settings_dict.get(camel_key)
                if val is None:
                    val = settings_dict.get(pascal_key)
            if val is None and isinstance(data, dict):
                val = data.get(camel_key)
                if val is None:
                    val = data.get(pascal_key)
            if val is None:
                return default
            try:
                return val_type(val)
            except (ValueError, TypeError):
                return default

        schedules: dict[str, DailySilentSchedule] = {}
        for day in DAYS_OF_WEEK:
            schedules[day] = DailySilentSchedule(
                day=day,
                start_hour=_get_val(
                    f"silentHours{day}Start", f"SilentHours{day}Start", 19, int
                ),
                start_minute=_get_val(
                    f"silentMinutes{day}Start", f"SilentMinutes{day}Start", 0, int
                ),
                stop_hour=_get_val(
                    f"silentHours{day}Stop", f"SilentHours{day}Stop", 7, int
                ),
                stop_minute=_get_val(
                    f"silentMinutes{day}Stop", f"SilentMinutes{day}Stop", 0, int
                ),
            )

        dhw_default = 50.0
        if isinstance(data.get("dhw"), dict) and "targetTemperature" in data["dhw"]:
            with contextlib.suppress(ValueError, TypeError):
                dhw_default = float(data["dhw"]["targetTemperature"])

        return cls(
            performance_mode_preset=_get_val(
                "performanceModePreset", "PerformanceModePreset", 0, int
            ),
            silent_hours_preset=_get_val(
                "silentHoursScheduledPreset", "SilentHoursScheduledPreset", 0, int
            ),
            dhw_target_temperature=_get_val(
                "dhwTargetTemperature", "DhwTargetTemperature", dhw_default, float
            ),
            schedules=schedules,
            raw_settings=settings_dict,
        )

    def build_bundled_silent_schedule_payload(
        self,
        performance_preset: int | None = None,
        performance_mode_preset: int | None = None,
        silent_preset: int | None = None,
        silent_hours_preset: int | None = None,
        day_updates: dict[str, int] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        """Build the complete 30-key quiet mode and silent schedule payload.

        Generates the complete 30-key PascalCase dictionary wrapped in
        {"settings": {...}, "updateImmediately": False} so partial updates
        never corrupt schedule state.
        """
        perf_val = (
            performance_preset
            if performance_preset is not None
            else performance_mode_preset
        )
        silent_val = silent_preset if silent_preset is not None else silent_hours_preset

        perf = self.performance_mode_preset if perf_val is None else int(perf_val)
        silent = self.silent_hours_preset if silent_val is None else int(silent_val)

        payload_settings: dict[str, int] = {
            "PerformanceModePreset": perf,
            "SilentHoursScheduledPreset": silent,
        }

        for day in DAYS_OF_WEEK:
            sched = self.schedules.get(day)
            payload_settings[f"SilentHours{day}Start"] = (
                sched.start_hour if sched else 19
            )
            payload_settings[f"SilentMinutes{day}Start"] = (
                sched.start_minute if sched else 0
            )
            payload_settings[f"SilentHours{day}Stop"] = sched.stop_hour if sched else 7
            payload_settings[f"SilentMinutes{day}Stop"] = (
                sched.stop_minute if sched else 0
            )

        merged_updates: dict[str, Any] = dict(day_updates) if day_updates else {}
        if kwargs:
            for k, v in kwargs.items():
                if v is not None:
                    merged_updates[k] = v

        if merged_updates:
            for k, v in merged_updates.items():
                if v is None:
                    continue
                if "_" in k:
                    norm_k = "".join(part.capitalize() for part in k.split("_"))
                elif k.startswith("silent") or k.startswith("performance"):
                    norm_k = k[0].upper() + k[1:]
                else:
                    norm_k = k

                if norm_k in payload_settings:
                    if norm_k == "PerformanceModePreset" and perf_val is not None:
                        continue
                    if (
                        norm_k == "SilentHoursScheduledPreset"
                        and silent_val is not None
                    ):
                        continue
                    with contextlib.suppress(ValueError, TypeError):
                        payload_settings[norm_k] = int(v)

        return {
            "settings": payload_settings,
            "updateImmediately": False,
        }

    build_quiet_payload = build_bundled_silent_schedule_payload

    def to_patch_payload(self) -> dict[str, Any]:
        """Convert settings dataclass to PATCH /control-boards/{id}/settings payload."""
        payload_settings: dict[str, Any] = {
            "PerformanceModePreset": self.performance_mode_preset,
            "SilentHoursScheduledPreset": self.silent_hours_preset,
            "DhwTargetTemperature": self.dhw_target_temperature,
        }
        for day, sched in self.schedules.items():
            payload_settings[f"SilentHours{day}Start"] = sched.start_hour
            payload_settings[f"SilentMinutes{day}Start"] = sched.start_minute
            payload_settings[f"SilentHours{day}Stop"] = sched.stop_hour
            payload_settings[f"SilentMinutes{day}Stop"] = sched.stop_minute

        return {
            "settings": payload_settings,
            "updateImmediately": False,
        }
