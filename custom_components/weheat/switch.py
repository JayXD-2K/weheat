"""Switch platform for Weheat integration.

Provides:
- Silent Hours Schedule Toggle Switch (Enables/disables silent hours schedule)
- Legionella Prevention Cycle Switch (Triggers or skips RequestLegionellaPrevention)
- DHW Boost Switch (Triggers Domestic Hot Water boost cycle)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .client import WeheatClient, WeheatError
from .coordinator import (
    WeheatConfigEntry,
    WeheatDataUpdateCoordinator,
)
from .entity import WeheatEntity
from .models import (
    ControlBoardSettings,
    DhwControlMethod,
    HeatPumpInfo,
    HeatPumpState,
    SilentHoursScheduledPreset,
)

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1

DEFAULT_SILENT_HOURS_ACTIVE_PRESET = SilentHoursScheduledPreset.SILENT_2


@dataclass(frozen=True, kw_only=True)
class WeheatSwitchEntityDescription(SwitchEntityDescription):
    """Describes Weheat switch entity."""


SWITCH_DESCRIPTIONS: tuple[WeheatSwitchEntityDescription, ...] = (
    WeheatSwitchEntityDescription(
        key="silent_hours_schedule_enabled",
        translation_key="silent_hours_schedule_enabled",
    ),
    WeheatSwitchEntityDescription(
        key="legionella_prevention",
        translation_key="legionella_prevention",
    ),
    WeheatSwitchEntityDescription(
        key="dhw_boost",
        translation_key="dhw_boost",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WeheatConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Weheat switch entities from a config entry."""
    entities: list[SwitchEntity] = []

    for weheatdata in entry.runtime_data:
        info = weheatdata.heat_pump_info
        client = weheatdata.client
        session = weheatdata.session

        # 1. Silent Hours Schedule Switch (binds to settings coordinator if available, else data_coordinator)
        settings_coord = getattr(weheatdata, "settings_coordinator", None)
        schedule_coord = settings_coord or weheatdata.data_coordinator

        entities.append(
            WeheatSilentHoursScheduleSwitch(
                heat_pump_info=info,
                coordinator=schedule_coord,
                entity_description=SWITCH_DESCRIPTIONS[0],
                client=client,
                session=session,
            )
        )

        # 2. DHW-specific switches (Legionella Prevention & DHW Boost) only for heat pumps with DHW
        if info.has_dhw:
            entities.append(
                WeheatLegionellaPreventionSwitch(
                    heat_pump_info=info,
                    coordinator=weheatdata.data_coordinator,
                    entity_description=SWITCH_DESCRIPTIONS[1],
                    client=client,
                    session=session,
                )
            )
            entities.append(
                WeheatDhwBoostSwitch(
                    heat_pump_info=info,
                    coordinator=weheatdata.data_coordinator,
                    entity_description=SWITCH_DESCRIPTIONS[2],
                    client=client,
                    session=session,
                )
            )

    async_add_entities(entities)


class WeheatSilentHoursScheduleSwitch(WeheatEntity, SwitchEntity):
    """Switch entity to enable or disable Silent Hours Schedule."""

    entity_description: WeheatSwitchEntityDescription

    def __init__(
        self,
        heat_pump_info: HeatPumpInfo,
        coordinator: Any,
        entity_description: WeheatSwitchEntityDescription,
        client: WeheatClient | None = None,
        session: Any | None = None,
    ) -> None:
        """Initialize the silent hours schedule switch entity."""
        super().__init__(heat_pump_info, coordinator, entity_description)
        self._client = client
        self._session = session
        self._last_active_preset: int = DEFAULT_SILENT_HOURS_ACTIVE_PRESET
        self._attr_icon = "mdi:calendar-clock"

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
    def is_on(self) -> bool | None:
        """Return True if Silent Hours Schedule is enabled (preset > 0 and != 5)."""
        data = self.coordinator.data
        if data is None:
            return None

        preset = 0
        if isinstance(data, ControlBoardSettings) or hasattr(
            data, "silent_hours_preset"
        ):
            raw = data.silent_hours_preset
            if isinstance(raw, int):
                preset = raw
        elif isinstance(data, dict):
            raw = data.get("silentHoursScheduledPreset")
            if raw is None:
                raw = data.get("SilentHoursScheduledPreset")
            if raw is None:
                raw = data.get("silent_hours_preset")
            if isinstance(raw, int):
                preset = raw

        # 0=Off, 5=Off/Disabled; 1..4, 6..7 are active schedules
        return preset > 0 and preset != SilentHoursScheduledPreset.OFF_ALT

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Enable silent hours schedule by restoring active preset."""
        if self._session is not None and hasattr(self._session, "check_portal_auth"):
            self._session.check_portal_auth()

        if self._client is None:
            raise HomeAssistantError("Weheat client is not available")

        # Determine target preset satisfying target_preset > performance_mode_preset
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

        target_preset = self._last_active_preset or DEFAULT_SILENT_HOURS_ACTIVE_PRESET
        if target_preset <= current_perf:
            target_preset = current_perf + 1
            if target_preset == 5:
                target_preset = 6
            if target_preset > 7:
                target_preset = 7

        try:
            token = await self._async_get_token()
            control_board_id = await self._async_get_control_board_id(token)

            settings_data = (
                self.coordinator.data
                if isinstance(self.coordinator.data, ControlBoardSettings)
                else ControlBoardSettings()
            )
            bundled = settings_data.build_bundled_silent_schedule_payload(
                silent_preset=target_preset
            )

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
                    preset=target_preset,
                )

            await self._client.async_poll_settings_request(
                control_board_id=control_board_id,
                request_id=request_id,
                token=token,
            )
            await self.coordinator.async_request_refresh()
        except WeheatError as err:
            _LOGGER.error("Failed to enable silent hours schedule: %s", err)
            raise HomeAssistantError(
                f"Failed to enable silent hours schedule: {err}"
            ) from err
        except Exception as err:
            _LOGGER.error("Unexpected error enabling silent hours schedule: %s", err)
            raise HomeAssistantError(
                f"Unexpected error enabling silent hours schedule: {err}"
            ) from err

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Disable silent hours schedule by setting preset to Off (0)."""
        if self._session is not None and hasattr(self._session, "check_portal_auth"):
            self._session.check_portal_auth()

        if self._client is None:
            raise HomeAssistantError("Weheat client is not available")

        # Remember current active preset for subsequent turn_on
        data = self.coordinator.data
        if isinstance(data, ControlBoardSettings) and data.silent_hours_preset in (
            1,
            2,
            3,
            4,
            6,
            7,
        ):
            self._last_active_preset = data.silent_hours_preset

        try:
            token = await self._async_get_token()
            control_board_id = await self._async_get_control_board_id(token)

            settings_data = (
                self.coordinator.data
                if isinstance(self.coordinator.data, ControlBoardSettings)
                else ControlBoardSettings()
            )
            bundled = settings_data.build_bundled_silent_schedule_payload(
                silent_preset=SilentHoursScheduledPreset.OFF
            )

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
                    preset=SilentHoursScheduledPreset.OFF,
                )

            await self._client.async_poll_settings_request(
                control_board_id=control_board_id,
                request_id=request_id,
                token=token,
            )
            await self.coordinator.async_request_refresh()
        except WeheatError as err:
            _LOGGER.error("Failed to disable silent hours schedule: %s", err)
            raise HomeAssistantError(
                f"Failed to disable silent hours schedule: {err}"
            ) from err
        except Exception as err:
            _LOGGER.error("Unexpected error disabling silent hours schedule: %s", err)
            raise HomeAssistantError(
                f"Unexpected error disabling silent hours schedule: {err}"
            ) from err


class WeheatLegionellaPreventionSwitch(WeheatEntity, SwitchEntity):
    """Switch entity to trigger or cancel Legionella prevention cycle."""

    entity_description: WeheatSwitchEntityDescription

    def __init__(
        self,
        heat_pump_info: HeatPumpInfo,
        coordinator: WeheatDataUpdateCoordinator,
        entity_description: WeheatSwitchEntityDescription,
        client: WeheatClient | None = None,
        session: Any | None = None,
    ) -> None:
        """Initialize the Legionella prevention switch entity."""
        super().__init__(heat_pump_info, coordinator, entity_description)
        self._client = client
        self._session = session
        self._attr_icon = "mdi:shield-bug"

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
    def is_on(self) -> bool | None:
        """Return True if heat pump is currently in Legionella prevention state."""
        log_data = self.coordinator.data
        if log_data is None:
            return None
        if isinstance(log_data, dict):
            raw_state = log_data.get("state")
            return raw_state == HeatPumpState.LEGIONELLA_PREVENTION or raw_state == 160
        return (
            getattr(log_data, "state", None) == HeatPumpState.LEGIONELLA_PREVENTION
            or getattr(log_data, "raw_state", None) == 160
        )

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Trigger Legionella prevention cycle via RequestLegionellaPrevention: 1."""
        if self._session is not None and hasattr(self._session, "check_portal_auth"):
            self._session.check_portal_auth()

        if self._client is None:
            raise HomeAssistantError("Weheat client is not available")

        try:
            token = await self._async_get_token()
            control_board_id = await self._async_get_control_board_id(token)
            await self._client.async_trigger_legionella_prevention(
                control_board_id=control_board_id,
                token=token,
            )
            await self.coordinator.async_request_refresh()
        except WeheatError as err:
            _LOGGER.error("Failed to trigger Legionella prevention: %s", err)
            raise HomeAssistantError(
                f"Failed to trigger Legionella prevention: {err}"
            ) from err
        except Exception as err:
            _LOGGER.error("Unexpected error triggering Legionella prevention: %s", err)
            raise HomeAssistantError(
                f"Unexpected error triggering Legionella prevention: {err}"
            ) from err

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Cancel/skip active Legionella prevention cycle via Hpc_skip_legionella_prevention: 1."""
        if self._session is not None and hasattr(self._session, "check_portal_auth"):
            self._session.check_portal_auth()

        if self._client is None:
            raise HomeAssistantError("Weheat client is not available")

        try:
            token = await self._async_get_token()
            control_board_id = await self._async_get_control_board_id(token)
            await self._client.async_skip_legionella_prevention(
                control_board_id=control_board_id,
                token=token,
            )
            await self.coordinator.async_request_refresh()
        except WeheatError as err:
            _LOGGER.error("Failed to skip Legionella prevention: %s", err)
            raise HomeAssistantError(
                f"Failed to skip Legionella prevention: {err}"
            ) from err
        except Exception as err:
            _LOGGER.error("Unexpected error skipping Legionella prevention: %s", err)
            raise HomeAssistantError(
                f"Unexpected error skipping Legionella prevention: {err}"
            ) from err


class WeheatDhwBoostSwitch(WeheatEntity, SwitchEntity):
    """Switch entity to trigger Domestic Hot Water boost."""

    entity_description: WeheatSwitchEntityDescription

    def __init__(
        self,
        heat_pump_info: HeatPumpInfo,
        coordinator: WeheatDataUpdateCoordinator,
        entity_description: WeheatSwitchEntityDescription,
        client: WeheatClient | None = None,
        session: Any | None = None,
    ) -> None:
        """Initialize the DHW boost switch entity."""
        super().__init__(heat_pump_info, coordinator, entity_description)
        self._client = client
        self._session = session
        self._attr_icon = "mdi:water-boiler-alert"

    async def _async_get_token(self) -> str | None:
        """Retrieve valid OAuth2 Bearer access token."""
        if self._session is not None:
            if hasattr(self._session, "check_portal_auth"):
                self._session.check_portal_auth()
            await self._session.async_ensure_token_valid()
            return self._session.token.get(CONF_ACCESS_TOKEN)
        return None

    @property
    def is_on(self) -> bool | None:
        """Return True if DHW boost is currently active."""
        log_data = self.coordinator.data
        if log_data is None:
            return None
        if isinstance(log_data, dict):
            method = log_data.get("dhwControlMethod")
            if method is None:
                method = log_data.get("dhw_control_method")
            return method == DhwControlMethod.BOOST or method == 2
        return getattr(log_data, "dhw_control_method", None) == DhwControlMethod.BOOST

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Trigger DHW boost (default 60 minutes at 60.0°C)."""
        if self._session is not None and hasattr(self._session, "check_portal_auth"):
            self._session.check_portal_auth()

        if self._client is None:
            raise HomeAssistantError("Weheat client is not available")

        try:
            token = await self._async_get_token()
            await self._client.async_trigger_dhw_boost(
                heat_pump_uuid=self.heat_pump_info.heatpump_id,
                token=token,
                duration_minutes=60,
                target_temperature=60.0,
            )
            await self.coordinator.async_request_refresh()
        except WeheatError as err:
            _LOGGER.error("Failed to trigger DHW boost: %s", err)
            raise HomeAssistantError(f"Failed to trigger DHW boost: {err}") from err
        except Exception as err:
            _LOGGER.error("Unexpected error triggering DHW boost: %s", err)
            raise HomeAssistantError(
                f"Unexpected error triggering DHW boost: {err}"
            ) from err

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off DHW boost."""
        # DHW boost does not have a dedicated off endpoint; refreshing state
        await self.coordinator.async_request_refresh()
