"""Select platform for Weheat integration.

Provides:
- Performance Mode Preset Select Entity:
  Maps to the Weheat Portal UI "Quiet level" daytime slider:
  0=Performance ("Extra savings" / Maximum power, unrestricted compressor/fan speed)
  1=Silent 1 (Quiet level 1, mild noise reduction)
  2=Silent 2 (Quiet level 2, moderate noise reduction)
  3=Silent 3 (Quiet level 3, high noise reduction)
  4=Silent 4 ("Extra quiet" / Quiet level 4, lowest daytime power limit)

- Silent Hours Scheduled Preset Select Entity:
  Maps to the Weheat Portal UI "Night limit" preset applied during scheduled silent hours:
  0=Off (Schedule disabled)
  1=Silent 1 (Level 1 night limit / highest night heating capacity)
  2=Silent 2 (Level 2 night limit)
  3=Silent 3 (Level 3 night limit)
  4=Silent 4 (Level 4 night limit)
  6=Silent 6 (Level 6 night limit, ultra quiet)
  7=Silent 7 (Level 7 night limit, maximum quietness)

Firmware constraint: When Night Limit is active (preset not in {0, 5}),
SilentHoursScheduledPreset must be strictly greater than PerformanceModePreset.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.select import SelectEntity, SelectEntityDescription
from homeassistant.const import CONF_ACCESS_TOKEN
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
# Maps to Weheat documentation Sound Modes (Modus 1–5) & Portal "Quiet level" daytime slider:
# Mode 5 - Max capacity = Extra savings / Maximum power (0)
# Mode 4 - Standard = Nominal rated performance (1 / Silent 1)
# Mode 3 - Reduced = Reduced power / lower noise (2 / Silent 2)
# Mode 2 - Low noise = Strict residential noise limits (3 / Silent 3)
# Mode 1 - Minimum noise = Extra quiet / Lowest daytime power limit (4 / Silent 4)
PERFORMANCE_MODE_OPTIONS: list[str] = [
    "Mode 5 - Max capacity",
    "Mode 4 - Standard",
    "Mode 3 - Reduced",
    "Mode 2 - Low noise",
    "Mode 1 - Minimum noise",
]

PRESET_TO_PERFORMANCE_MODE_OPTION: dict[int, str] = {
    PerformanceModePreset.PERFORMANCE: "Mode 5 - Max capacity",
    PerformanceModePreset.SILENT_1: "Mode 4 - Standard",
    PerformanceModePreset.SILENT_2: "Mode 3 - Reduced",
    PerformanceModePreset.SILENT_3: "Mode 2 - Low noise",
    PerformanceModePreset.SILENT_4: "Mode 1 - Minimum noise",
}

PERFORMANCE_MODE_OPTION_TO_PRESET: dict[str, int] = {
    "Mode 5 - Max capacity": PerformanceModePreset.PERFORMANCE,
    "Mode 4 - Standard": PerformanceModePreset.SILENT_1,
    "Mode 3 - Reduced": PerformanceModePreset.SILENT_2,
    "Mode 2 - Low noise": PerformanceModePreset.SILENT_3,
    "Mode 1 - Minimum noise": PerformanceModePreset.SILENT_4,
    # Legacy aliases supported for backward compatibility
    "Performance": PerformanceModePreset.PERFORMANCE,
    "Silent 1": PerformanceModePreset.SILENT_1,
    "Silent 2": PerformanceModePreset.SILENT_2,
    "Silent 3": PerformanceModePreset.SILENT_3,
    "Silent 4": PerformanceModePreset.SILENT_4,
}

# Silent Hours Scheduled Preset Options & Mappings
# Maps to Weheat Mobile App & Portal UI "Night limit" preset applied during scheduled window:
# Off = Scheduled quiet hours disabled (0, 5)
# Normal = Night limit level 1 (mildest night limit / highest night heating capacity) (1 / Silent 1)
# Background chatter = Night limit level 2 (moderate night noise cap) (2 / Silent 2)
# Light rain = Night limit level 3 (high night noise cap) (3 / Silent 3)
# Library = Night limit level 4 (very quiet night limit) (4 / Silent 4)
# Whisper = Night limit level 6 (ultra quiet night limit) (6 / Silent 6)
# Minimum = Night limit level 7 (maximum quietness / lowest night power limit) (7 / Silent 7)
SILENT_HOURS_OPTIONS: list[str] = [
    "Off",
    "Minimum",
    "Whisper",
    "Library",
    "Light rain",
    "Background chatter",
    "Normal",
]

PRESET_TO_SILENT_HOURS_OPTION: dict[int, str] = {
    SilentHoursScheduledPreset.OFF: "Off",
    SilentHoursScheduledPreset.SILENT_1: "Normal",
    SilentHoursScheduledPreset.SILENT_2: "Background chatter",
    SilentHoursScheduledPreset.SILENT_3: "Light rain",
    SilentHoursScheduledPreset.SILENT_4: "Library",
    SilentHoursScheduledPreset.OFF_ALT: "Off",  # 5 maps to Off/Disabled
    SilentHoursScheduledPreset.SILENT_6: "Whisper",
    SilentHoursScheduledPreset.SILENT_7: "Minimum",
}

SILENT_HOURS_OPTION_TO_PRESET: dict[str, int] = {
    "Off": SilentHoursScheduledPreset.OFF,
    "Normal": SilentHoursScheduledPreset.SILENT_1,
    "Background chatter": SilentHoursScheduledPreset.SILENT_2,
    "Light rain": SilentHoursScheduledPreset.SILENT_3,
    "Library": SilentHoursScheduledPreset.SILENT_4,
    "Whisper": SilentHoursScheduledPreset.SILENT_6,
    "Minimum": SilentHoursScheduledPreset.SILENT_7,
    # Legacy aliases supported for backward compatibility
    "Silent 1": SilentHoursScheduledPreset.SILENT_1,
    "Silent 2": SilentHoursScheduledPreset.SILENT_2,
    "Silent 3": SilentHoursScheduledPreset.SILENT_3,
    "Silent 4": SilentHoursScheduledPreset.SILENT_4,
    "Silent 6": SilentHoursScheduledPreset.SILENT_6,
    "Silent 7": SilentHoursScheduledPreset.SILENT_7,
}


@dataclass(frozen=True, kw_only=True)
class WeheatSelectEntityDescription(SelectEntityDescription):
    """Describes Weheat select entity."""


SELECT_ENTITIES: tuple[WeheatSelectEntityDescription, ...] = (
    WeheatSelectEntityDescription(
        key="performance_mode_preset",
        translation_key="performance_mode_preset",
        options=PERFORMANCE_MODE_OPTIONS,
    ),
    WeheatSelectEntityDescription(
        key="silent_hours_preset",
        translation_key="silent_hours_preset",
        options=SILENT_HOURS_OPTIONS,
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
            preset = data.get("performanceModePreset")
            if preset is None:
                preset = data.get("PerformanceModePreset")
            if preset is None:
                preset = data.get("performance_mode_preset")
        else:
            return None

        if preset is not None and preset in PRESET_TO_PERFORMANCE_MODE_OPTION:
            return PRESET_TO_PERFORMANCE_MODE_OPTION[int(preset)]
        return None

    async def async_select_option(self, option: str) -> None:
        """Change the performance mode preset and await setting completion."""
        if self._session is not None and hasattr(self._session, "check_portal_auth"):
            self._session.check_portal_auth()

        if option not in PERFORMANCE_MODE_OPTION_TO_PRESET:
            raise ValueError(
                f"Invalid option '{option}'. Must be one of {PERFORMANCE_MODE_OPTIONS}"
            )

        preset = PERFORMANCE_MODE_OPTION_TO_PRESET[option]
        if self._client is None:
            raise HomeAssistantError("Weheat client is not available")

        # Cleanly adjust night limit if daytime preset meets or exceeds it
        current_silent = 0
        if isinstance(self.coordinator.data, ControlBoardSettings) or hasattr(
            self.coordinator.data, "silent_hours_preset"
        ):
            raw = self.coordinator.data.silent_hours_preset
            if isinstance(raw, int):
                current_silent = raw
        elif isinstance(self.coordinator.data, dict):
            raw = (
                self.coordinator.data.get("silentHoursScheduledPreset")
                if self.coordinator.data.get("silentHoursScheduledPreset") is not None
                else self.coordinator.data.get("SilentHoursScheduledPreset", 0)
            )
            if isinstance(raw, int):
                current_silent = raw

        target_silent = current_silent
        if (
            current_silent not in (0, SilentHoursScheduledPreset.OFF_ALT)
            and preset >= current_silent
        ):
            target_silent = preset + 1 if preset < 4 else 6
            _LOGGER.info(
                "Adjusting silent hours scheduled preset to %s to satisfy firmware constraint over daytime preset %s",
                target_silent,
                preset,
            )

        try:
            token = await self._async_get_token()
            control_board_id = await self._async_get_control_board_id(token)

            settings_data = (
                self.coordinator.data
                if isinstance(self.coordinator.data, ControlBoardSettings)
                else ControlBoardSettings()
            )
            bundled = settings_data.build_bundled_silent_schedule_payload(
                performance_preset=preset,
                silent_hours_preset=target_silent,
            )

            # 1. Dispatch setting mutation PATCH
            if hasattr(self._client, "async_patch_control_board_settings") and (
                type(self._client).__name__ == "WeheatClient"
                or not hasattr(self._client, "async_set_performance_mode")
            ):
                request_id = await self._client.async_patch_control_board_settings(
                    control_board_id=control_board_id,
                    token=token,
                    settings=bundled,
                )
            else:
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
            preset = data.get("silentHoursScheduledPreset")
            if preset is None:
                preset = data.get("SilentHoursScheduledPreset")
            if preset is None:
                preset = data.get("silent_hours_preset")
        else:
            return None

        if preset is not None and preset in PRESET_TO_SILENT_HOURS_OPTION:
            return PRESET_TO_SILENT_HOURS_OPTION[int(preset)]
        return None

    async def async_select_option(self, option: str) -> None:
        """Change the silent hours scheduled preset and await setting completion."""
        if self._session is not None and hasattr(self._session, "check_portal_auth"):
            self._session.check_portal_auth()

        if option not in SILENT_HOURS_OPTION_TO_PRESET:
            raise ValueError(
                f"Invalid option '{option}'. Must be one of {SILENT_HOURS_OPTIONS}"
            )

        preset = SILENT_HOURS_OPTION_TO_PRESET[option]
        if self._client is None:
            raise HomeAssistantError("Weheat client is not available")

        # Enforce live constraint: when Night Limit is active, preset > performance_mode_preset
        current_perf = 0
        if isinstance(self.coordinator.data, ControlBoardSettings) or hasattr(
            self.coordinator.data, "performance_mode_preset"
        ):
            raw = self.coordinator.data.performance_mode_preset
            if isinstance(raw, int):
                current_perf = raw
        elif isinstance(self.coordinator.data, dict):
            raw = (
                self.coordinator.data.get("performanceModePreset")
                if self.coordinator.data.get("performanceModePreset") is not None
                else self.coordinator.data.get("PerformanceModePreset", 0)
            )
            if isinstance(raw, int):
                current_perf = raw

        if (
            preset not in (0, SilentHoursScheduledPreset.OFF_ALT)
            and preset <= current_perf
        ):
            raise HomeAssistantError(
                "Night limit preset must be strictly greater than daytime performance preset"
            )

        try:
            token = await self._async_get_token()
            control_board_id = await self._async_get_control_board_id(token)

            settings_data = (
                self.coordinator.data
                if isinstance(self.coordinator.data, ControlBoardSettings)
                else ControlBoardSettings()
            )
            bundled = settings_data.build_bundled_silent_schedule_payload(
                silent_preset=preset
            )

            # 1. Dispatch setting mutation PATCH
            if hasattr(self._client, "async_patch_control_board_settings") and (
                type(self._client).__name__ == "WeheatClient"
                or not hasattr(self._client, "async_set_silent_hours_preset")
            ):
                request_id = await self._client.async_patch_control_board_settings(
                    control_board_id=control_board_id,
                    token=token,
                    settings=bundled,
                )
            else:
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
