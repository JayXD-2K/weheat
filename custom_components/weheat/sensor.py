"""Platform for Weheat sensor integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE,
    REVOLUTIONS_PER_MINUTE,
    EntityCategory,
    UnitOfEnergy,
    UnitOfPower,
    UnitOfTemperature,
    UnitOfVolumeFlowRate,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .const import (
    DISPLAY_PRECISION_COP,
    DISPLAY_PRECISION_FLOW,
    DISPLAY_PRECISION_WATER_TEMP,
    DISPLAY_PRECISION_WATTS,
)
from .coordinator import (
    WeheatConfigEntry,
    WeheatDataUpdateCoordinator,
    WeheatEnergyUpdateCoordinator,
)
from .entity import WeheatEntity
from .models import (
    COOLING_START_CONDITION_BITS,
    CoolingPauseReason,
    CoolingStatus,
    CoolingStopReason,
    DhwControlMethod,
    HeatPumpInfo,
    HeatPumpLog,
    HeatPumpState,
)

# Coordinator is used to centralize data updates
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class WeHeatSensorEntityDescription(SensorEntityDescription):
    """Describes Weheat sensor entity."""

    value_fn: Callable[[Any], StateType | datetime]


# The portal counts the conditions the heat pump waits on and leaves these two
# settings out of its tally.
COOLING_CONDITIONS_NOT_COUNTED = ("control_method", "contact_not_blocked")
CONDITIONS_COUNTED = len(COOLING_START_CONDITION_BITS) - len(
    COOLING_CONDITIONS_NOT_COUNTED
)


def _latched_reason(
    status: HeatPumpLog, reason_int: int | None, reason_enum: type[Enum]
) -> str | None:
    """Return a reason from before the cycle, which says 'none' while one runs."""
    is_cooling_active = (
        status.cooling_status is not None and status.cooling_status in (1, 2, 3)
    ) or status.state == HeatPumpState.COOLING
    if is_cooling_active:
        return "none"
    if reason_int is None:
        return None
    try:
        return reason_enum(reason_int).name.lower()
    except (ValueError, KeyError):
        return None


# 15 Base Operational Sensors
SENSORS: tuple[WeHeatSensorEntityDescription, ...] = (
    WeHeatSensorEntityDescription(
        translation_key="power_output",
        key="power_output",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=DISPLAY_PRECISION_WATTS,
        value_fn=lambda status: status.power_output,
    ),
    WeHeatSensorEntityDescription(
        translation_key="power_input",
        key="power_input",
        native_unit_of_measurement=UnitOfPower.WATT,
        device_class=SensorDeviceClass.POWER,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=DISPLAY_PRECISION_WATTS,
        value_fn=lambda status: status.power_input,
    ),
    WeHeatSensorEntityDescription(
        translation_key="cop",
        key="cop",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=DISPLAY_PRECISION_COP,
        value_fn=lambda status: (
            round(status.cop, 2) if status.cop is not None else None
        ),
    ),
    WeHeatSensorEntityDescription(
        translation_key="water_inlet_temperature",
        key="water_inlet_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=DISPLAY_PRECISION_WATER_TEMP,
        value_fn=lambda status: status.water_inlet_temperature,
    ),
    WeHeatSensorEntityDescription(
        translation_key="water_outlet_temperature",
        key="water_outlet_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=DISPLAY_PRECISION_WATER_TEMP,
        value_fn=lambda status: status.water_outlet_temperature,
    ),
    WeHeatSensorEntityDescription(
        translation_key="ch_inlet_temperature",
        key="ch_inlet_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=DISPLAY_PRECISION_WATER_TEMP,
        value_fn=lambda status: status.ch_inlet_temperature,
    ),
    WeHeatSensorEntityDescription(
        translation_key="outside_temperature",
        key="outside_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=DISPLAY_PRECISION_WATER_TEMP,
        value_fn=lambda status: status.outside_temperature,
    ),
    WeHeatSensorEntityDescription(
        translation_key="air_outlet_temperature",
        key="air_outlet_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=DISPLAY_PRECISION_WATER_TEMP,
        value_fn=lambda status: status.air_outlet_temperature,
    ),
    WeHeatSensorEntityDescription(
        translation_key="thermostat_water_setpoint",
        key="thermostat_water_setpoint",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=DISPLAY_PRECISION_WATER_TEMP,
        value_fn=lambda status: status.thermostat_water_setpoint,
    ),
    WeHeatSensorEntityDescription(
        translation_key="thermostat_room_temperature",
        key="thermostat_room_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=DISPLAY_PRECISION_WATER_TEMP,
        value_fn=lambda status: status.thermostat_room_temperature,
    ),
    WeHeatSensorEntityDescription(
        translation_key="thermostat_room_temperature_setpoint",
        key="thermostat_room_temperature_setpoint",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=DISPLAY_PRECISION_WATER_TEMP,
        value_fn=lambda status: status.thermostat_room_temperature_setpoint,
    ),
    WeHeatSensorEntityDescription(
        translation_key="heat_pump_state",
        key="heat_pump_state",
        name=None,
        device_class=SensorDeviceClass.ENUM,
        options=[s.name.lower() for s in HeatPumpState],
        value_fn=(
            lambda status: (
                status.state.name.lower() if status.state is not None else None
            )
        ),
    ),
    WeHeatSensorEntityDescription(
        translation_key="compressor_rpm",
        key="compressor_rpm",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=REVOLUTIONS_PER_MINUTE,
        value_fn=lambda status: status.compressor_rpm,
    ),
    WeHeatSensorEntityDescription(
        translation_key="compressor_percentage",
        key="compressor_percentage",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        value_fn=lambda status: status.compressor_percentage,
    ),
    WeHeatSensorEntityDescription(
        translation_key="central_heating_flow_volume",
        key="central_heating_flow_volume",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=DISPLAY_PRECISION_FLOW,
        native_unit_of_measurement=UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
        value_fn=lambda status: status.central_heating_flow_volume,
    ),
)

# 5 DHW Operational Sensors (Conditional on has_dhw == True)
DHW_SENSORS: tuple[WeHeatSensorEntityDescription, ...] = (
    WeHeatSensorEntityDescription(
        translation_key="dhw_top_temperature",
        key="dhw_top_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=DISPLAY_PRECISION_WATER_TEMP,
        value_fn=lambda status: status.dhw_top_temperature,
    ),
    WeHeatSensorEntityDescription(
        translation_key="dhw_bottom_temperature",
        key="dhw_bottom_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=DISPLAY_PRECISION_WATER_TEMP,
        value_fn=lambda status: status.dhw_bottom_temperature,
    ),
    WeHeatSensorEntityDescription(
        translation_key="dhw_flow_volume",
        key="dhw_flow_volume",
        device_class=SensorDeviceClass.VOLUME_FLOW_RATE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=DISPLAY_PRECISION_FLOW,
        native_unit_of_measurement=UnitOfVolumeFlowRate.CUBIC_METERS_PER_HOUR,
        value_fn=lambda status: status.dhw_flow_volume,
    ),
    WeHeatSensorEntityDescription(
        translation_key="dhw_target_temperature",
        key="dhw_target_temperature",
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=DISPLAY_PRECISION_WATER_TEMP,
        # A target of zero indicates DHW control is off
        value_fn=lambda status: status.dhw_target_temperature or None,
    ),
    WeHeatSensorEntityDescription(
        translation_key="dhw_control_method",
        key="dhw_control_method",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=[method.name.lower() for method in DhwControlMethod],
        value_fn=(
            lambda status: (
                DhwControlMethod(status.dhw_control_method).name.lower()
                if status.dhw_control_method is not None
                and status.dhw_control_method in DhwControlMethod._value2member_map_
                else None
            )
        ),
    ),
)

# 7 Cooling Diagnostic Sensors (Conditional on cooling activity / cooling status)
COOLING_SENSORS: tuple[WeHeatSensorEntityDescription, ...] = (
    WeHeatSensorEntityDescription(
        translation_key="cooling_state",
        key="cooling_state",
        device_class=SensorDeviceClass.ENUM,
        options=[status.name.lower() for status in CoolingStatus],
        value_fn=lambda status: (
            CoolingStatus(status.cooling_status).name.lower()
            if status.cooling_status is not None
            and status.cooling_status in CoolingStatus._value2member_map_
            else None
        ),
    ),
    WeHeatSensorEntityDescription(
        translation_key="cooling_blocked_by",
        key="cooling_blocked_by",
        device_class=SensorDeviceClass.ENUM,
        options=["none", *COOLING_START_CONDITION_BITS],
        value_fn=lambda status: (
            None
            if status.cooling_start_conditions is None
            else "none"
            if (
                (
                    status.cooling_status is not None
                    and status.cooling_status in (1, 2, 3)
                )
                or status.state == HeatPumpState.COOLING
            )
            else next(
                (
                    name
                    for name in COOLING_START_CONDITION_BITS
                    if not status.cooling_start_conditions.get(name, True)
                ),
                "none",
            )
        ),
    ),
    WeHeatSensorEntityDescription(
        translation_key="cooling_conditions_met",
        key="cooling_conditions_met",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=f"of {CONDITIONS_COUNTED}",
        value_fn=lambda status: (
            None
            if status.cooling_start_conditions is None
            or (
                (
                    status.cooling_status is not None
                    and status.cooling_status in (1, 2, 3)
                )
                or status.state == HeatPumpState.COOLING
            )
            else sum(
                met
                for name, met in status.cooling_start_conditions.items()
                if name not in COOLING_CONDITIONS_NOT_COUNTED
            )
        ),
    ),
    WeHeatSensorEntityDescription(
        translation_key="cooling_wait_until",
        key="cooling_wait_until",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda status: (
            None
            if status.cooling_start_conditions is None
            or status.cooling_start_conditions.get("exponential_backoff", False)
            else status.last_cooling_time
        ),
    ),
    WeHeatSensorEntityDescription(
        translation_key="last_cooling_time",
        key="last_cooling_time",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda status: status.last_cooling_time,
    ),
    WeHeatSensorEntityDescription(
        translation_key="cooling_pause_reason",
        key="cooling_pause_reason",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=[reason.name.lower() for reason in CoolingPauseReason],
        value_fn=lambda status: _latched_reason(
            status, status.cooling_pause_reason, CoolingPauseReason
        ),
    ),
    WeHeatSensorEntityDescription(
        translation_key="cooling_stop_reason",
        key="cooling_stop_reason",
        device_class=SensorDeviceClass.ENUM,
        entity_category=EntityCategory.DIAGNOSTIC,
        options=[reason.name.lower() for reason in CoolingStopReason],
        value_fn=lambda status: _latched_reason(
            status, status.cooling_stop_reason, CoolingStopReason
        ),
    ),
)

# 10 Cumulative Energy Sensors (From Energy Update Coordinator)
ENERGY_SENSORS: tuple[WeHeatSensorEntityDescription, ...] = (
    WeHeatSensorEntityDescription(
        translation_key="electricity_used",
        key="electricity_used",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda status: status.electricity_used,
    ),
    WeHeatSensorEntityDescription(
        translation_key="electricity_used_indoor_unit",
        key="electricity_used_indoor_unit",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda status: status.electricity_used_indoor_unit,
    ),
    WeHeatSensorEntityDescription(
        translation_key="energy_output",
        key="energy_output",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda status: status.energy_output,
    ),
    WeHeatSensorEntityDescription(
        translation_key="electricity_used_heating",
        key="electricity_used_heating",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda status: status.electricity_used_heating,
    ),
    WeHeatSensorEntityDescription(
        translation_key="electricity_used_cooling",
        key="electricity_used_cooling",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda status: status.electricity_used_cooling,
    ),
    WeHeatSensorEntityDescription(
        translation_key="electricity_used_defrost",
        key="electricity_used_defrost",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda status: status.electricity_used_defrost,
    ),
    WeHeatSensorEntityDescription(
        translation_key="electricity_used_standby",
        key="electricity_used_standby",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda status: status.electricity_used_standby,
    ),
    WeHeatSensorEntityDescription(
        translation_key="energy_output_heating",
        key="energy_output_heating",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda status: status.energy_output_heating,
    ),
    WeHeatSensorEntityDescription(
        translation_key="energy_output_cooling",
        key="energy_output_cooling",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda status: status.energy_output_cooling,
    ),
    WeHeatSensorEntityDescription(
        translation_key="energy_output_defrost",
        key="energy_output_defrost",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        value_fn=lambda status: status.energy_output_defrost,
    ),
)

# 2 DHW Cumulative Energy Sensors (Conditional on has_dhw == True)
DHW_ENERGY_SENSORS: tuple[WeHeatSensorEntityDescription, ...] = (
    WeHeatSensorEntityDescription(
        translation_key="electricity_used_dhw",
        key="electricity_used_dhw",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda status: status.electricity_used_dhw,
    ),
    WeHeatSensorEntityDescription(
        translation_key="energy_output_dhw",
        key="energy_output_dhw",
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL_INCREASING,
        value_fn=lambda status: status.energy_output_dhw,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WeheatConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the sensors for Weheat heat pump."""
    entities: list[WeheatHeatPumpSensor] = []

    for weheatdata in entry.runtime_data:
        info = weheatdata.heat_pump_info
        data_coord = weheatdata.data_coordinator
        energy_coord = weheatdata.energy_coordinator

        # 1. Base operational sensors (filtered if value is not None)
        entities.extend(
            WeheatHeatPumpSensor(info, data_coord, desc)
            for desc in SENSORS
            if desc.value_fn(data_coord.data) is not None
        )

        # 2. Cooling diagnostic sensors
        if (
            data_coord.data.cooling_status is not None
            or data_coord.data.cooling_start_conditions is not None
        ):
            entities.extend(
                WeheatHeatPumpSensor(info, data_coord, desc) for desc in COOLING_SENSORS
            )

        # 3. DHW sensors (if heat pump has DHW support)
        if info.has_dhw:
            entities.extend(
                WeheatHeatPumpSensor(info, data_coord, desc) for desc in DHW_SENSORS
            )
            entities.extend(
                WeheatHeatPumpSensor(info, energy_coord, desc)
                for desc in DHW_ENERGY_SENSORS
            )

        # 4. Energy cumulative sensors (filtered if value is not None)
        entities.extend(
            WeheatHeatPumpSensor(info, energy_coord, desc)
            for desc in ENERGY_SENSORS
            if desc.value_fn(energy_coord.data) is not None
        )

    async_add_entities(entities)


class WeheatHeatPumpSensor(WeheatEntity, SensorEntity):
    """Defines a Weheat heat pump sensor entity."""

    heat_pump_info: HeatPumpInfo
    coordinator: WeheatDataUpdateCoordinator | WeheatEnergyUpdateCoordinator
    entity_description: WeHeatSensorEntityDescription

    def __init__(
        self,
        heat_pump_info: HeatPumpInfo,
        coordinator: WeheatDataUpdateCoordinator | WeheatEnergyUpdateCoordinator,
        entity_description: WeHeatSensorEntityDescription,
    ) -> None:
        """Initialize the Weheat sensor."""
        super().__init__(heat_pump_info, coordinator)
        self.entity_description = entity_description
        self._attr_unique_id = f"{heat_pump_info.heatpump_id}_{entity_description.key}"

    @property
    def native_value(self) -> StateType | datetime:
        """Return the state of the sensor."""
        return self.entity_description.value_fn(self.coordinator.data)
