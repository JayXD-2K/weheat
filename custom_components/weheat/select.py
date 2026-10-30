"""Select platform for Weheat integration.

Provides:
- Performance Mode Preset Select Entity (0=Performance, 1=Silent 1, 2=Silent 2, 3=Silent 3, 4=Silent 4)
- Silent Hours Scheduled Preset Select Entity (0=Off, 1=Silent 1, 2=Silent 2, 3=Silent 3, 4=Silent 4)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import CONF_ACCESS_TOKEN, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .client import WeheatClient, WeheatError
from .coordinator import WeheatConfigEntry
from .entity import WeheatEntity
from .models import (
    ControlBoardSettings,
    HeatPumpInfo,
    PerformanceModePreset,
    SilentHoursScheduledPreset,
)

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1

# Performance Mode Preset (Quiet Level) Options & Mappings
PERFORMANCE_MODE_OPTIONS: list[str] = [
    "Performance",
    "Silent 1",
    "Silent 2",
    "Silent 3",
    "Silent 4",
]

PRESET_TO_PERFORMANCE_MODE_OPTION: dict[int, str] = {
    PerformanceModePreset.PERFORMANCE: "Performance",
    PerformanceModePreset.SILENT_1: "Silent 1",
    PerformanceModePreset.SILENT_2: "Silent 2",
    PerformanceModePreset.SILENT_3: "Silent 3",
    PerformanceModePreset.SILENT_4: "Silent 4",
}

PERFORMANCE_MODE_OPTION_TO_PRESET: dict[str, int] = {
    "Performance": PerformanceModePreset.PERFORMANCE,
    "Silent 1": PerformanceModePreset.SILENT_1,
    "Silent 2": PerformanceModePreset.SILENT_2,
    "Silent 3": PerformanceModePreset.SILENT_3,
    "Silent 4": PerformanceModePreset.SILENT_4,
}

# Silent Hours Scheduled Preset Options & Mappings
SILENT_HOURS_OPTIONS: list[str] = [
    "Off",
    "Silent 1",
    "Silent 2",
    "Silent 3",
    "Silent 4",
]

PRESET_TO_SILENT_HOURS_OPTION: dict[int, str] = {
    SilentHoursScheduledPreset.OFF: "Off",
    SilentHoursScheduledPreset.SILENT_1: "Silent 1",
    SilentHoursScheduledPreset.SILENT_2: "Silent 2",
    SilentHoursScheduledPreset.SILENT_3: "Silent 3",
    SilentHoursScheduledPreset.SILENT_4: "Silent 4",
    SilentHoursScheduledPreset.OFF_ALT: "Off",  # 5 maps to Off/Disabled
}

SILENT_HOURS_OPTION_TO_PRESET: dict[str, int] = {
    "Off": SilentHoursScheduledPreset.OFF,
    "Silent 1": SilentHoursScheduledPreset.SILENT_1,
    "Silent 2": SilentHoursScheduledPreset.SILENT_2,
    "Silent 3": SilentHoursScheduledPreset.SILENT_3,
    "Silent 4": SilentHoursScheduledPreset.SILENT_4,
}


@dataclass(frozen=True, kw_only=True)
class WeheatSelectEntityDescription(SelectEntityDescription):
    """Describes Weheat select entity."""


SELECT_ENTITIES: tuple[WeheatSelectEntityDescription, ...] = (
    WeheatSelectEntityDescription(
        key="performance_mode_preset",
        translation_key="performance_mode_preset",
        options=PERFORMANCE_MODE_OPTIONS,
        entity_category=EntityCategory.CONFIG,
    ),
    WeheatSelectEntityDescription(
        key="silent_hours_preset",
        translation_key="silent_hours_preset",
        options=SILENT_HOURS_OPTIONS,
        entity_category=EntityCategory.CONFIG,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WeheatConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Weheat select entities from a config entry."""
    entities: list[SelectEntity] = []

    for weheatdata in entry.runtime_data:
        info = weheatdata.heat_pump_info
        client = weheatdata.client
        session = weheatdata.session

        # Use settings_coordinator if present, or fallback to data_coordinator
        settings_coord = getattr(weheatdata, "settings_coordinator", None)
        coordinator = settings_coord or weheatdata.data_coordinator

        entities.append(
            WeheatPerformanceModeSelect(
                heat_pump_info=info,
                coordinator=coordinator,
                entity_description=SELECT_ENTITIES[0],
                client=client,
                session=session,
            )
        )
        entities.append(
            WeheatSilentHoursPresetSelect(
                heat_pump_info=info,
                coordinator=coordinator,
                entity_description=SELECT_ENTITIES[1],
                client=client,
                session=session,
            )
        )

    async_add_entities(entities)


class WeheatPerformanceModeSelect(WeheatEntity, SelectEntity):
    """Select entity for heat pump Performance Mode (Quiet Level: 0..4)."""

    entity_description: WeheatSelectEntityDescription

    def __init__(
        self,
        heat_pump_info: HeatPumpInfo,
        coordinator: Any,
        entity_description: WeheatSelectEntityDescription,
        client: WeheatClient | None = None,
        session: Any | None = None,
    ) -> None:
        """Initialize the performance mode select entity."""
        super().__init__(heat_pump_info, coordinator, entity_description)
        self._client = client
        self._session = session
        self._attr_options = list(PERFORMANCE_MODE_OPTIONS)
        self._attr_icon = "mdi:volume-low"

    async def _async_get_token(self) -> str | None:
        """Retrieve valid OAuth2 Bearer access token."""
        if self._session is not None:
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

    @property
    def current_option(self) -> str | None:
        """Return current performance mode preset option from coordinator cache."""
        data = self.coordinator.data
        if data is None:
            return None

        # When data is ControlBoardSettings or has attribute
        if isinstance(data, ControlBoardSettings) or hasattr(
            data, "performance_mode_preset"
        ):
            preset = data.performance_mode_preset
        elif isinstance(data, dict):
            preset = data.get("PerformanceModePreset") or data.get(
                "performance_mode_preset"
            )
        else:
            return None

        if preset is not None and preset in PRESET_TO_PERFORMANCE_MODE_OPTION:
            return PRESET_TO_PERFORMANCE_MODE_OPTION[int(preset)]
        return None

    async def async_select_option(self, option: str) -> None:
        """Change the performance mode preset and await setting completion."""
        if option not in PERFORMANCE_MODE_OPTION_TO_PRESET:
            raise ValueError(
                f"Invalid option '{option}'. Must be one of {PERFORMANCE_MODE_OPTIONS}"
            )

        preset = PERFORMANCE_MODE_OPTION_TO_PRESET[option]
        if self._client is None:
            raise HomeAssistantError("Weheat client is not available")

        try:
            token = await self._async_get_token()
            control_board_id = await self._async_get_control_board_id(token)

            # 1. Dispatch setting mutation PATCH
            request_id = await self._client.async_set_performance_mode(
                control_board_id=control_board_id,
                token=token,
                preset=preset,
            )

            # 2. Await asynchronous polling until status == Succeeded (2)
            await self._client.async_poll_settings_request(
                control_board_id=control_board_id,
                request_id=request_id,
                token=token,
            )

            # 3. Trigger coordinator refresh to pull updated settings
            await self.coordinator.async_request_refresh()
        except WeheatError as err:
            _LOGGER.error("Failed to set performance mode to %s: %s", option, err)
            raise HomeAssistantError(
                f"Failed to set performance mode to {option}: {err}"
            ) from err
        except Exception as err:
            _LOGGER.error("Unexpected error setting performance mode: %s", err)
            raise HomeAssistantError(
                f"Unexpected error setting performance mode: {err}"
            ) from err


class WeheatSilentHoursPresetSelect(WeheatEntity, SelectEntity):
    """Select entity for Silent Hours Scheduled Preset (0=Off, 1..4=Silent 1..4)."""

    entity_description: WeheatSelectEntityDescription

    def __init__(
        self,
        heat_pump_info: HeatPumpInfo,
        coordinator: Any,
        entity_description: WeheatSelectEntityDescription,
        client: WeheatClient | None = None,
        session: Any | None = None,
    ) -> None:
        """Initialize the silent hours preset select entity."""
        super().__init__(heat_pump_info, coordinator, entity_description)
        self._client = client
        self._session = session
        self._attr_options = list(SILENT_HOURS_OPTIONS)
        self._attr_icon = "mdi:clock-outline"

    async def _async_get_token(self) -> str | None:
        """Retrieve valid OAuth2 Bearer access token."""
        if self._session is not None:
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

    @property
    def current_option(self) -> str | None:
        """Return current silent hours scheduled preset from coordinator cache."""
        data = self.coordinator.data
        if data is None:
            return None

        # When data is ControlBoardSettings or has attribute
        if isinstance(data, ControlBoardSettings) or hasattr(
            data, "silent_hours_preset"
        ):
            preset = data.silent_hours_preset
        elif isinstance(data, dict):
            preset = data.get("SilentHoursScheduledPreset") or data.get(
                "silent_hours_preset"
            )
        else:
            return None

        if preset is not None and preset in PRESET_TO_SILENT_HOURS_OPTION:
            return PRESET_TO_SILENT_HOURS_OPTION[int(preset)]
        return None

    async def async_select_option(self, option: str) -> None:
        """Change the silent hours scheduled preset and await setting completion."""
        if option not in SILENT_HOURS_OPTION_TO_PRESET:
            raise ValueError(
                f"Invalid option '{option}'. Must be one of {SILENT_HOURS_OPTIONS}"
            )

        preset = SILENT_HOURS_OPTION_TO_PRESET[option]
        if self._client is None:
            raise HomeAssistantError("Weheat client is not available")

        try:
            token = await self._async_get_token()
            control_board_id = await self._async_get_control_board_id(token)

            # 1. Dispatch setting mutation PATCH
            request_id = await self._client.async_set_silent_hours_preset(
                control_board_id=control_board_id,
                token=token,
                preset=preset,
            )

            # 2. Await asynchronous polling until status == Succeeded (2)
            await self._client.async_poll_settings_request(
                control_board_id=control_board_id,
                request_id=request_id,
                token=token,
            )

            # 3. Trigger coordinator refresh to pull updated settings
            await self.coordinator.async_request_refresh()
        except WeheatError as err:
            _LOGGER.error("Failed to set silent hours preset to %s: %s", option, err)
            raise HomeAssistantError(
                f"Failed to set silent hours preset to {option}: {err}"
            ) from err
        except Exception as err:
            _LOGGER.error("Unexpected error setting silent hours preset: %s", err)
            raise HomeAssistantError(
                f"Unexpected error setting silent hours preset: {err}"
            ) from err
