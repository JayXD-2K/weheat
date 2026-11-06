"""Base entity for Weheat heat pump integration."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)

from .const import DOMAIN, MANUFACTURER
from .models import ControlBoardSettings, HeatPumpInfo

if TYPE_CHECKING:
    from .client import WeheatClient


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


class WeheatControlEntity[WeheatEntityT: DataUpdateCoordinator[Any]](
    WeheatEntity[WeheatEntityT]
):
    """Base entity for Weheat Portal control entities (select, switch, number, time)."""

    def __init__(
        self,
        heat_pump_info: HeatPumpInfo,
        coordinator: WeheatEntityT,
        entity_description: EntityDescription | None = None,
        client: WeheatClient | None = None,
        session: Any | None = None,
    ) -> None:
        """Initialize the Weheat control entity."""
        super().__init__(heat_pump_info, coordinator, entity_description)
        self._client = client
        self._session = session

    def _check_portal_auth(self) -> None:
        """Ensure portal authentication is configured before write operations."""
        if self._session is not None and hasattr(self._session, "check_portal_auth"):
            self._session.check_portal_auth()

    async def _async_get_token(self) -> str | None:
        """Retrieve valid OAuth2 Bearer access token."""
        if self._session is not None:
            if hasattr(self._session, "check_portal_auth"):
                self._session.check_portal_auth()
            await self._session.async_ensure_token_valid()
            return self._session.token.get(CONF_ACCESS_TOKEN)
        return None

    async def _async_get_control_board_id(self, token: str | None = None) -> str:
        """Resolve target control board ID."""
        if self.heat_pump_info.control_board_id:
            return self.heat_pump_info.control_board_id
        if self._client is not None:
            return await self._client.async_get_control_board_id(
                self.heat_pump_info.heatpump_id, token=token
            )
        raise HomeAssistantError("No client available to resolve control board ID")

    def _get_settings(self) -> ControlBoardSettings | None:
        """Return typed ControlBoardSettings from coordinator data."""
        data = self.coordinator.data
        if data is None:
            return None
        if isinstance(data, ControlBoardSettings):
            return data
        if isinstance(data, dict):
            return ControlBoardSettings.from_api_dict(data)
        return None
