"""Time platform for Weheat heat pump quiet hours schedules.

Provides 14 day-specific TimeEntity instances (Monday through Sunday start/stop)
for controlling the heat pump's silent hours schedule via Weheat Portal API.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import time
from typing import Any

from aiohttp import ClientError
from homeassistant.components.time import TimeEntity, TimeEntityDescription
from homeassistant.const import CONF_ACCESS_TOKEN, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .client import (
    WeheatApiError,
    WeheatAuthError,
    WeheatClient,
    WeheatTimeoutError,
)
from .const import DAYS_OF_WEEK
from .coordinator import WeheatConfigEntry
from .entity import WeheatEntity
from .models import ControlBoardSettings, DailySilentSchedule, HeatPumpInfo

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class WeheatTimeEntityDescription(TimeEntityDescription):
    """Class describing Weheat quiet hours schedule time entities."""

    day: str
    is_start: bool
    icon: str | None = None


TIME_ENTITIES: tuple[WeheatTimeEntityDescription, ...] = tuple(
    WeheatTimeEntityDescription(
        key=f"{day.lower()}_silent_hours_{action.lower()}",
        translation_key=f"{day.lower()}_silent_hours_{action.lower()}",
        day=day,
        is_start=(action == "Start"),
        entity_category=EntityCategory.CONFIG,
        icon="mdi:clock-start" if action == "Start" else "mdi:clock-end",
    )
    for day in DAYS_OF_WEEK
    for action in ("Start", "Stop")
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WeheatConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Weheat time entities from a config entry.

    Args:
        hass: Home Assistant instance.
        entry: Configuration entry with runtime_data populated.
        async_add_entities: Callback to register entities with Home Assistant.
    """
    entities: list[WeheatTimeEntity] = []

    for data in entry.runtime_data:
        # Prefer dedicated settings coordinator, fallback to data coordinator
        coordinator: DataUpdateCoordinator[Any] = (
            getattr(data, "settings_coordinator", None) or data.data_coordinator
        )
        for description in TIME_ENTITIES:
            entities.append(
                WeheatTimeEntity(
                    heat_pump_info=data.heat_pump_info,
                    coordinator=coordinator,
                    entity_description=description,
                    client=data.client,
                    session=data.session,
                )
            )

    async_add_entities(entities)


class WeheatTimeEntity(WeheatEntity[DataUpdateCoordinator[Any]], TimeEntity):
    """Representation of a Weheat Quiet Hours Schedule Time entity.

    Exposes the scheduled quiet time start or stop hour:minute for a given day
    of the week, converting back and forth between datetime.time and the
    API keys SilentHours<Day>Start/Stop & SilentMinutes<Day>Start/Stop.
    """

    entity_description: WeheatTimeEntityDescription

    def __init__(
        self,
        heat_pump_info: HeatPumpInfo,
        coordinator: DataUpdateCoordinator[Any],
        entity_description: WeheatTimeEntityDescription,
        client: WeheatClient | None = None,
        session: Any | None = None,
    ) -> None:
        """Initialize the Weheat time entity.

        Args:
            heat_pump_info: Discovery metadata of the heat pump.
            coordinator: Update coordinator providing settings data.
            entity_description: Description of the specific day/action time entity.
            client: Optional WeheatClient instance (defaults to coordinator's client).
            session: Optional session providing portal auth and token refresh.
        """
        super().__init__(heat_pump_info, coordinator, entity_description)
        self.entity_description = entity_description
        self._client = client or getattr(coordinator, "client", None)
        self._session = session or getattr(coordinator, "session", None)

    @property
    def icon(self) -> str | None:
        """Return icon for the entity."""
        return self.entity_description.icon

    @property
    def native_value(self) -> time | None:
        """Return the current scheduled time value from coordinator data."""
        if not self.coordinator.data:
            return None

        # Primary path: coordinator.data is a ControlBoardSettings dataclass
        if hasattr(self.coordinator.data, "schedules"):
            schedules: dict[str, DailySilentSchedule] = self.coordinator.data.schedules
            schedule: DailySilentSchedule | None = schedules.get(
                self.entity_description.day
            )
            if schedule is not None:
                if self.entity_description.is_start:
                    return time(schedule.start_hour, schedule.start_minute)
                return time(schedule.stop_hour, schedule.stop_minute)

        # Fallback path: coordinator.data is a raw dict (mock fixture or fallback)
        if isinstance(self.coordinator.data, dict):
            settings_dict = self.coordinator.data.get("settings", self.coordinator.data)
            action = "Start" if self.entity_description.is_start else "Stop"
            hour_key = f"SilentHours{self.entity_description.day}{action}"
            minute_key = f"SilentMinutes{self.entity_description.day}{action}"
            default_hour = 19 if self.entity_description.is_start else 7
            hour = int(settings_dict.get(hour_key, default_hour))
            minute = int(settings_dict.get(minute_key, 0))
            return time(hour, minute)

        return None

    async def async_set_value(self, value: time) -> None:
        """Set a new quiet time schedule value in the Weheat portal.

        Converts the provided datetime.time into SilentHours<Day><Action> and
        SilentMinutes<Day><Action> settings, patches the control board, polls
        the asynchronous request to completion, and refreshes the coordinator.

        Args:
            value: The target time (hour and minute).

        Raises:
            ValueError: If value is not a datetime.time instance.
            HomeAssistantError: If API mutation or polling fails.
        """
        if not isinstance(value, time):
            raise ValueError(f"Expected datetime.time, got {type(value).__name__}")

        hour = value.hour
        minute = value.minute

        action = "Start" if self.entity_description.is_start else "Stop"
        hour_key = f"SilentHours{self.entity_description.day}{action}"
        minute_key = f"SilentMinutes{self.entity_description.day}{action}"

        schedule_payload: dict[str, int] = {
            hour_key: hour,
            minute_key: minute,
        }

        client = self._client or getattr(self.coordinator, "client", None)
        if client is None:
            raise HomeAssistantError("Weheat API client is not configured")

        # Refresh OAuth token if an active session is associated with coordinator
        token: str | None = None
        session = self._session or getattr(self.coordinator, "session", None)
        if session is not None:
            if hasattr(session, "check_portal_auth"):
                session.check_portal_auth()
            try:
                await session.async_ensure_token_valid()
                token = session.token.get(CONF_ACCESS_TOKEN)
            except Exception as err:
                raise HomeAssistantError(
                    f"Failed to refresh OAuth token: {err}"
                ) from err

        # Resolve control board ID (cached on heat_pump_info or queried via client)
        control_board_id = self.heat_pump_info.control_board_id
        if not control_board_id:
            try:
                control_board_id = await client.async_get_control_board_id(
                    self.heat_pump_info.heatpump_id, token=token
                )
            except Exception as err:
                raise HomeAssistantError(
                    f"Could not resolve control board ID for heat pump {self.heat_pump_info.heatpump_id}: {err}"
                ) from err

        # Optimistic local state update for immediate UI feedback
        if hasattr(self.coordinator.data, "schedules"):
            day_schedule = self.coordinator.data.schedules.get(
                self.entity_description.day
            )
            if day_schedule is not None:
                if self.entity_description.is_start:
                    day_schedule.start_hour = hour
                    day_schedule.start_minute = minute
                else:
                    day_schedule.stop_hour = hour
                    day_schedule.stop_minute = minute
                if (
                    hasattr(self, "async_write_ha_state")
                    and getattr(self, "hass", None) is not None
                ):
                    self.async_write_ha_state()

        try:
            _LOGGER.debug(
                "Dispatching quiet time schedule update for %s (%s): %s",
                self.heat_pump_info.heatpump_id,
                self.entity_description.key,
                schedule_payload,
            )

            settings_data = (
                self.coordinator.data
                if isinstance(self.coordinator.data, ControlBoardSettings)
                else ControlBoardSettings()
            )
            bundled = settings_data.build_bundled_silent_schedule_payload(
                day_updates=schedule_payload
            )

            if hasattr(client, "async_patch_control_board_settings") and (
                type(client).__name__ == "WeheatClient"
                or (
                    not hasattr(client, "async_set_silent_schedule")
                    and not hasattr(client, "async_set_silent_hours_schedule")
                )
            ):
                request_id = await client.async_patch_control_board_settings(
                    control_board_id=control_board_id,
                    token=token,
                    settings=bundled,
                )
            elif hasattr(client, "async_set_silent_schedule"):
                request_id = await client.async_set_silent_schedule(
                    control_board_id=control_board_id,
                    token=token,
                    schedule=schedule_payload,
                )
            else:
                preset = getattr(self.coordinator.data, "silent_hours_preset", 0)
                request_id = await client.async_set_silent_hours_schedule(
                    control_board_id=control_board_id,
                    token=token,
                    preset=preset,
                    schedule=schedule_payload,
                )

            _LOGGER.debug(
                "Awaiting settings request %s completion for %s",
                request_id,
                self.entity_description.key,
            )

            await client.async_poll_settings_request(
                control_board_id=control_board_id,
                request_id=request_id,
                token=token,
            )
        except WeheatAuthError as err:
            await self.coordinator.async_request_refresh()
            raise HomeAssistantError(
                f"Authentication failed updating quiet time: {err}"
            ) from err
        except (WeheatApiError, WeheatTimeoutError, ClientError) as err:
            await self.coordinator.async_request_refresh()
            raise HomeAssistantError(
                f"Failed to update quiet time on Weheat portal: {err}"
            ) from err

        # Synchronize authoritative state from the cloud
        await self.coordinator.async_request_refresh()
