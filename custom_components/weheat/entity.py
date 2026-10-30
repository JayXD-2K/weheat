"""Base entity for Weheat heat pump integration."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import DOMAIN, MANUFACTURER
from .models import HeatPumpInfo


class WeheatEntity[WeheatEntityT: DataUpdateCoordinator[Any]](
    CoordinatorEntity[WeheatEntityT]
):
    """Defines a base Weheat entity bound to a heat pump device in the registry."""

    _attr_has_entity_name = True

    def __init__(
        self,
        heat_pump_info: HeatPumpInfo,
        coordinator: WeheatEntityT,
        entity_description: EntityDescription | None = None,
    ) -> None:
        """Initialize the Weheat entity.

        Args:
            heat_pump_info: Discovery metadata of the heat pump.
            coordinator: Update coordinator feeding data to this entity.
            entity_description: Optional entity description setting description and unique ID.
        """
        super().__init__(coordinator)
        self.heat_pump_info = heat_pump_info

        if entity_description is not None:
            self.entity_description = entity_description
            self._attr_unique_id = (
                f"{heat_pump_info.heatpump_id}_{entity_description.key}"
            )

        device_info_kwargs: dict[str, Any] = {
            "identifiers": {(DOMAIN, heat_pump_info.heatpump_id)},
            "name": heat_pump_info.readable_name,
            "manufacturer": MANUFACTURER,
            "model": heat_pump_info.model_name,
        }
        if heat_pump_info.serial_number:
            device_info_kwargs["serial_number"] = heat_pump_info.serial_number
        elif heat_pump_info.sn:
            device_info_kwargs["serial_number"] = heat_pump_info.sn

        if heat_pump_info.firmware_version:
            device_info_kwargs["sw_version"] = heat_pump_info.firmware_version
        if heat_pump_info.part_number:
            device_info_kwargs["hw_version"] = heat_pump_info.part_number

        self._attr_device_info = DeviceInfo(**device_info_kwargs)
