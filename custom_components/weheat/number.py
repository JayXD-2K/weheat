"""Number platform for Weheat integration.

Provides 2-point Cooling Curve target water temperature controls:
- Cooling water target at 22°C outside (mild day setpoint, 15..22 °C)
- Cooling water target at 40°C outside (peak-summer day setpoint, 15..22 °C)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.number import (
    NumberDeviceClass,
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .client import WeheatClient, WeheatError
from .const import (
    COOLING_CURVE_MAX_TEMP,
    COOLING_CURVE_MIN_TEMP_DEFAULT,
    DEFAULT_COOLING_CURVE_Y,
    DEFAULT_HEATING_CURVE_Y,
    HEATING_CURVE_MAX_TEMP,
    HEATING_CURVE_MIN_TEMP,
)
from .coordinator import WeheatConfigEntry
from .entity import WeheatControlEntity
from .models import ControlBoardSettings, HeatPumpInfo

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class WeheatCoolingCurveNumberDescription(NumberEntityDescription):
    """Describes a Weheat cooling curve number entity."""

    curve_index: int = 0
    outside_temperature: int = 22
    default_value: int = 18


@dataclass(frozen=True, kw_only=True)
class WeheatHeatingCurveNumberDescription(NumberEntityDescription):
    """Describes a Weheat heating curve number entity."""

    curve_index: int = 0
    outside_temperature: int = -20
    default_value: int = 42


COOLING_NUMBER_DESCRIPTIONS: tuple[WeheatCoolingCurveNumberDescription, ...] = (
    WeheatCoolingCurveNumberDescription(
        key="cooling_curve_mild_temperature",
        translation_key="cooling_curve_mild_temperature",
        entity_category=EntityCategory.CONFIG,
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=float(COOLING_CURVE_MIN_TEMP_DEFAULT),
        native_max_value=float(COOLING_CURVE_MAX_TEMP),
        native_step=1.0,
        mode=NumberMode.BOX,
        curve_index=0,
        outside_temperature=22,
        default_value=DEFAULT_COOLING_CURVE_Y[0],
    ),
    WeheatCoolingCurveNumberDescription(
        key="cooling_curve_peak_temperature",
        translation_key="cooling_curve_peak_temperature",
        entity_category=EntityCategory.CONFIG,
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=float(COOLING_CURVE_MIN_TEMP_DEFAULT),
        native_max_value=float(COOLING_CURVE_MAX_TEMP),
        native_step=1.0,
        mode=NumberMode.BOX,
        curve_index=1,
        outside_temperature=40,
        default_value=DEFAULT_COOLING_CURVE_Y[1],
    ),
)

HEATING_NUMBER_DESCRIPTIONS: tuple[WeheatHeatingCurveNumberDescription, ...] = (
    WeheatHeatingCurveNumberDescription(
        key="heating_curve_at_minus_20c",
        translation_key="heating_curve_at_minus_20c",
        entity_category=EntityCategory.CONFIG,
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=float(HEATING_CURVE_MIN_TEMP),
        native_max_value=float(HEATING_CURVE_MAX_TEMP),
        native_step=1.0,
        mode=NumberMode.BOX,
        curve_index=0,
        outside_temperature=-20,
        default_value=DEFAULT_HEATING_CURVE_Y[0],
    ),
    WeheatHeatingCurveNumberDescription(
        key="heating_curve_at_9c",
        translation_key="heating_curve_at_9c",
        entity_category=EntityCategory.CONFIG,
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=float(HEATING_CURVE_MIN_TEMP),
        native_max_value=float(HEATING_CURVE_MAX_TEMP),
        native_step=1.0,
        mode=NumberMode.BOX,
        curve_index=1,
        outside_temperature=9,
        default_value=DEFAULT_HEATING_CURVE_Y[1],
    ),
    WeheatHeatingCurveNumberDescription(
        key="heating_curve_at_15c",
        translation_key="heating_curve_at_15c",
        entity_category=EntityCategory.CONFIG,
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=float(HEATING_CURVE_MIN_TEMP),
        native_max_value=float(HEATING_CURVE_MAX_TEMP),
        native_step=1.0,
        mode=NumberMode.BOX,
        curve_index=2,
        outside_temperature=15,
        default_value=DEFAULT_HEATING_CURVE_Y[2],
    ),
    WeheatHeatingCurveNumberDescription(
        key="heating_curve_at_20c",
        translation_key="heating_curve_at_20c",
        entity_category=EntityCategory.CONFIG,
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=float(HEATING_CURVE_MIN_TEMP),
        native_max_value=float(HEATING_CURVE_MAX_TEMP),
        native_step=1.0,
        mode=NumberMode.BOX,
        curve_index=3,
        outside_temperature=20,
        default_value=DEFAULT_HEATING_CURVE_Y[3],
    ),
    WeheatHeatingCurveNumberDescription(
        key="heating_curve_at_25c",
        translation_key="heating_curve_at_25c",
        entity_category=EntityCategory.CONFIG,
        device_class=NumberDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        native_min_value=float(HEATING_CURVE_MIN_TEMP),
        native_max_value=float(HEATING_CURVE_MAX_TEMP),
        native_step=1.0,
        mode=NumberMode.BOX,
        curve_index=4,
        outside_temperature=25,
        default_value=DEFAULT_HEATING_CURVE_Y[4],
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WeheatConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Weheat cooling and heating curve number entities from a config entry."""
    entities: list[NumberEntity] = []

    for weheatdata in entry.runtime_data:
        info = weheatdata.heat_pump_info
        client = weheatdata.client
        session = weheatdata.session

        settings_coord = getattr(weheatdata, "settings_coordinator", None)
        coord = settings_coord or weheatdata.data_coordinator

        for desc in COOLING_NUMBER_DESCRIPTIONS:
            entities.append(
                WeheatCoolingCurveNumber(
                    heat_pump_info=info,
                    coordinator=coord,
                    entity_description=desc,
                    client=client,
                    session=session,
                )
            )
        for h_desc in HEATING_NUMBER_DESCRIPTIONS:
            entities.append(
                WeheatHeatingCurveNumber(
                    heat_pump_info=info,
                    coordinator=coord,
                    entity_description=h_desc,
                    client=client,
                    session=session,
                )
            )

    async_add_entities(entities)


class WeheatCoolingCurveNumber(WeheatControlEntity, NumberEntity):
    """Number entity for a point on the 2-point Weheat Cooling Curve."""

    entity_description: WeheatCoolingCurveNumberDescription

    def __init__(
        self,
        heat_pump_info: HeatPumpInfo,
        coordinator: Any,
        entity_description: WeheatCoolingCurveNumberDescription,
        client: WeheatClient | None = None,
        session: Any | None = None,
    ) -> None:
        """Initialize the cooling curve number entity."""
        super().__init__(
            heat_pump_info, coordinator, entity_description, client, session
        )
        self._attr_icon = "mdi:snowflake-thermometer"
        self._attr_mode = NumberMode.BOX

    @property
    def native_min_value(self) -> float:
        """Return dynamic minimum cooling setpoint from installation settings."""
        settings = self._get_settings()
        if (
            settings is not None
            and isinstance(settings.cooling_min_setpoint, int)
            and 10 <= settings.cooling_min_setpoint <= COOLING_CURVE_MAX_TEMP
        ):
            return float(settings.cooling_min_setpoint)
        return float(COOLING_CURVE_MIN_TEMP_DEFAULT)

    @property
    def native_max_value(self) -> float:
        """Return maximum cooling curve setpoint (22°C)."""
        return float(COOLING_CURVE_MAX_TEMP)

    @property
    def native_step(self) -> float:
        """Return 1°C step matching Weheat Portal spinbuttons."""
        return 1.0

    @property
    def native_value(self) -> float | None:
        """Return the current cooling water target temperature (°C) for this curve point."""
        settings = self._get_settings()
        if settings is None:
            return None
        if self.entity_description.curve_index == 0:
            return float(settings.cooling_curve_mild_temperature)
        return float(settings.cooling_curve_peak_temperature)

    async def async_set_native_value(self, value: float) -> None:
        """Update the cooling curve water target temperature on the control board."""
        if self._session is not None and hasattr(self._session, "check_portal_auth"):
            self._session.check_portal_auth()

        if self._client is None:
            raise HomeAssistantError("Weheat client is not available")

        target_int = round(float(value))
        min_val = round(self.native_min_value)
        max_val = COOLING_CURVE_MAX_TEMP
        if not (min_val <= target_int <= max_val):
            raise HomeAssistantError(
                f"Invalid cooling water temperature {value} °C; must be between {min_val} and {max_val} °C."
            )

        try:
            token = await self._async_get_token()
            control_board_id = await self._async_get_control_board_id(token)

            settings_data = self._get_settings() or ControlBoardSettings()
            if self.entity_description.curve_index == 0:
                payload = settings_data.build_cooling_payload(
                    mild_temperature=target_int
                )
            else:
                payload = settings_data.build_cooling_payload(
                    peak_temperature=target_int
                )

            if hasattr(self._client, "async_patch_control_board_settings"):
                request_id = await self._client.async_patch_control_board_settings(
                    control_board_id=control_board_id,
                    token=token,
                    settings=payload,
                )
            else:
                request_id = await self._client.async_set_cooling_settings(
                    control_board_id=control_board_id,
                    token=token,
                    cooling_payload=payload,
                )

            await self._client.async_poll_settings_request(
                control_board_id=control_board_id,
                request_id=request_id,
                token=token,
            )
            await self.coordinator.async_request_refresh()
        except WeheatError as err:
            _LOGGER.error("Failed to update cooling curve temperature: %s", err)
            raise HomeAssistantError(
                f"Failed to update cooling curve temperature: {err}"
            ) from err
        except HomeAssistantError:
            raise
        except Exception as err:
            _LOGGER.error(
                "Unexpected error updating cooling curve temperature: %s", err
            )
            raise HomeAssistantError(
                f"Unexpected error updating cooling curve temperature: {err}"
            ) from err


class WeheatHeatingCurveNumber(WeheatControlEntity, NumberEntity):
    """Number entity for a point on the 5-point Weheat Heating Curve."""

    entity_description: WeheatHeatingCurveNumberDescription

    def __init__(
        self,
        heat_pump_info: HeatPumpInfo,
        coordinator: Any,
        entity_description: WeheatHeatingCurveNumberDescription,
        client: WeheatClient | None = None,
        session: Any | None = None,
    ) -> None:
        """Initialize the heating curve number entity."""
        super().__init__(
            heat_pump_info, coordinator, entity_description, client, session
        )
        self._attr_icon = "mdi:chart-bell-curve-cumulative"
        self._attr_mode = NumberMode.BOX

    @property
    def native_min_value(self) -> float:
        """Return minimum heating curve setpoint (15°C)."""
        return float(HEATING_CURVE_MIN_TEMP)

    @property
    def native_max_value(self) -> float:
        """Return maximum heating curve setpoint (65°C)."""
        return float(HEATING_CURVE_MAX_TEMP)

    @property
    def native_step(self) -> float:
        """Return 1°C step matching Weheat Portal spinbuttons."""
        return 1.0

    @property
    def native_value(self) -> float | None:
        """Return the current heating water target temperature (°C) for this curve point."""
        settings = self._get_settings()
        if settings is None:
            return None
        idx = self.entity_description.curve_index
        if idx == 0:
            return float(settings.heating_curve_at_minus_20c)
        if idx == 1:
            return float(settings.heating_curve_at_9c)
        if idx == 2:
            return float(settings.heating_curve_at_15c)
        if idx == 3:
            return float(settings.heating_curve_at_20c)
        return float(settings.heating_curve_at_25c)

    async def async_set_native_value(self, value: float) -> None:
        """Update the heating curve water target temperature on the control board."""
        if self._session is not None and hasattr(self._session, "check_portal_auth"):
            self._session.check_portal_auth()

        if self._client is None:
            raise HomeAssistantError("Weheat client is not available")

        target_int = round(float(value))
        min_val = HEATING_CURVE_MIN_TEMP
        max_val = HEATING_CURVE_MAX_TEMP
        if not (min_val <= target_int <= max_val):
            raise HomeAssistantError(
                f"Invalid heating water temperature {value} °C; must be between {min_val} and {max_val} °C."
            )

        try:
            token = await self._async_get_token()
            control_board_id = await self._async_get_control_board_id(token)

            settings_data = self._get_settings() or ControlBoardSettings()
            idx = self.entity_description.curve_index
            kwargs: dict[str, int] = {}
            if idx == 0:
                kwargs["at_minus_20c"] = target_int
            elif idx == 1:
                kwargs["at_9c"] = target_int
            elif idx == 2:
                kwargs["at_15c"] = target_int
            elif idx == 3:
                kwargs["at_20c"] = target_int
            else:
                kwargs["at_25c"] = target_int

            payload = settings_data.build_heating_curve_payload(**kwargs)

            if hasattr(self._client, "async_patch_control_board_settings"):
                request_id = await self._client.async_patch_control_board_settings(
                    control_board_id=control_board_id,
                    token=token,
                    settings=payload,
                )
            else:
                request_id = await self._client.async_set_heating_curve(
                    control_board_id=control_board_id,
                    token=token,
                    heating_payload=payload,
                )

            await self._client.async_poll_settings_request(
                control_board_id=control_board_id,
                request_id=request_id,
                token=token,
            )
            await self.coordinator.async_request_refresh()
        except WeheatError as err:
            _LOGGER.error("Failed to update heating curve temperature: %s", err)
            raise HomeAssistantError(
                f"Failed to update heating curve temperature: {err}"
            ) from err
        except HomeAssistantError:
            raise
        except Exception as err:
            _LOGGER.error(
                "Unexpected error updating heating curve temperature: %s", err
            )
            raise HomeAssistantError(
                f"Unexpected error updating heating curve temperature: {err}"
            ) from err
