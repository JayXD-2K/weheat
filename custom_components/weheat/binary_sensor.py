"""Binary sensor platform for Weheat integration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.typing import StateType

from .coordinator import (
    WeheatConfigEntry,
    WeheatDataUpdateCoordinator,
)
from .entity import WeheatEntity
from .models import COOLING_START_CONDITION_BITS, HeatPumpInfo, HeatPumpLog

# Coordinator is used to centralize data updates
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class WeHeatBinarySensorEntityDescription(BinarySensorEntityDescription):
    """Describes Weheat binary sensor entity."""

    value_fn: Callable[[HeatPumpLog], StateType]


# 5 Indoor Unit Actuator Binary Sensors
BINARY_SENSORS: tuple[WeHeatBinarySensorEntityDescription, ...] = (
    WeHeatBinarySensorEntityDescription(
        translation_key="indoor_unit_water_pump_state",
        key="indoor_unit_water_pump_state",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda status: status.indoor_unit_water_pump_state,
    ),
    WeHeatBinarySensorEntityDescription(
        translation_key="indoor_unit_auxiliary_pump_state",
        key="indoor_unit_auxiliary_pump_state",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda status: status.indoor_unit_auxiliary_pump_state,
    ),
    WeHeatBinarySensorEntityDescription(
        translation_key="indoor_unit_dhw_valve_or_pump_state",
        key="indoor_unit_dhw_valve_or_pump_state",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda status: status.indoor_unit_dhw_valve_or_pump_state,
    ),
    WeHeatBinarySensorEntityDescription(
        translation_key="indoor_unit_gas_boiler_state",
        key="indoor_unit_gas_boiler_state",
        device_class=None,
        value_fn=lambda status: status.indoor_unit_gas_boiler_state,
    ),
    WeHeatBinarySensorEntityDescription(
        translation_key="indoor_unit_electric_heater_state",
        key="indoor_unit_electric_heater_state",
        device_class=BinarySensorDeviceClass.RUNNING,
        value_fn=lambda status: status.indoor_unit_electric_heater_state,
    ),
)


def _make_cooling_condition_description(
    condition: str,
) -> WeHeatBinarySensorEntityDescription:
    """Create a diagnostic binary sensor description for a cooling start condition."""
    return WeHeatBinarySensorEntityDescription(
        translation_key=f"cooling_start_condition_{condition}",
        key=f"cooling_start_condition_{condition}",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        value_fn=lambda status, c=condition: (
            status.cooling_start_conditions.get(c)
            if status.cooling_start_conditions is not None
            else None
        ),
    )


# 11 Diagnostic Cooling Start Condition Binary Sensors
COOLING_START_CONDITION_SENSORS: tuple[WeHeatBinarySensorEntityDescription, ...] = (
    tuple(
        _make_cooling_condition_description(condition)
        for condition in COOLING_START_CONDITION_BITS
    )
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WeheatConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up binary sensors for Weheat heat pump."""
    entities: list[WeheatHeatPumpBinarySensor] = []

    for weheatdata in entry.runtime_data:
        info = weheatdata.heat_pump_info
        data_coord = weheatdata.data_coordinator

        # 1. Indoor unit actuator binary sensors
        entities.extend(
            WeheatHeatPumpBinarySensor(info, data_coord, desc)
            for desc in BINARY_SENSORS
            if desc.value_fn(data_coord.data) is not None
        )

        # 2. Cooling start condition diagnostic binary sensors (if reported)
        if data_coord.data.cooling_start_conditions is not None:
            entities.extend(
                WeheatHeatPumpBinarySensor(info, data_coord, desc)
                for desc in COOLING_START_CONDITION_SENSORS
            )

    async_add_entities(entities)


class WeheatHeatPumpBinarySensor(WeheatEntity, BinarySensorEntity):
    """Defines a Weheat heat pump binary sensor entity."""

    heat_pump_info: HeatPumpInfo
    coordinator: WeheatDataUpdateCoordinator
    entity_description: WeHeatBinarySensorEntityDescription

    def __init__(
        self,
        heat_pump_info: HeatPumpInfo,
        coordinator: WeheatDataUpdateCoordinator,
        entity_description: WeHeatBinarySensorEntityDescription,
    ) -> None:
        """Initialize the Weheat binary sensor."""
        super().__init__(heat_pump_info, coordinator)
        self.entity_description = entity_description
        self._attr_unique_id = f"{heat_pump_info.heatpump_id}_{entity_description.key}"

    @property
    def is_on(self) -> bool | None:
        """Return True if the binary sensor is on."""
        value = self.entity_description.value_fn(self.coordinator.data)
        return bool(value) if value is not None else None
