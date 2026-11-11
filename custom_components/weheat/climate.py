"""Climate platform for Weheat integration.

Provides a native Home Assistant Thermostat (ClimateEntity) that smoothly controls
the Weheat heat pump via its 5-point weather-compensated Heating Curve (ControlMethod=1)
or OpenTherm mode (ControlMethod=4), preventing aggressive OpenTherm water setpoint
spikes and avoiding unnecessary 2.85 kW backup electric heater activation.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from homeassistant.components.climate import (
    ClimateEntity,
    ClimateEntityDescription,
    ClimateEntityFeature,
    HVACAction,
    HVACMode,
)
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .client import WeheatClient, WeheatError
from .const import (
    BOOST_HEATING_CURVE_Y,
    COMFORT_HEATING_CURVE_Y,
    DEFAULT_HEATING_CURVE_X,
    ECO_HEATING_CURVE_Y,
    STANDBY_HEATING_CURVE_Y,
)
from .coordinator import WeheatConfigEntry
from .entity import WeheatControlEntity
from .models import (
    ControlBoardSettings,
    ControlMethod,
    HeatPumpInfo,
    HeatPumpLog,
    HeatPumpState,
)

_LOGGER = logging.getLogger(__name__)

PARALLEL_UPDATES = 1

PRESET_COMFORT = "comfort"
PRESET_ECO = "eco"
PRESET_BOOST = "boost"
PRESET_STANDBY = "standby"
PRESET_OPENTHERM = "opentherm"

CLIMATE_PRESET_MODES: list[str] = [
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_BOOST,
    PRESET_STANDBY,
    PRESET_OPENTHERM,
]

MIN_ROOM_TEMP = 16.0
MAX_ROOM_TEMP = 24.0
ROOM_TEMP_STEP = 0.5
DEFAULT_ROOM_TARGET = 21.0


def curve_for_room_temperature(target_temp: float) -> list[int]:
    """Calculate a 5-point Weheat HeatingCurveY for a desired room temperature (°C).

    Uses the anti-electric-heater Comfort curve [37, 28, 24, 20, 18] at 21.0°C as
    reference, shifting by 2°C water temperature per 0.5°C room temperature delta
    and capping the -20°C point at 40°C so the indoor unit backup heater is not
    triggered by excessive water target temperatures.
    """
    temp = max(MIN_ROOM_TEMP, min(MAX_ROOM_TEMP, float(target_temp)))
    if temp <= 18.0:
        return list(STANDBY_HEATING_CURVE_Y)

    offset = round((temp - DEFAULT_ROOM_TARGET) * 4.0)
    y0 = max(25, min(40, COMFORT_HEATING_CURVE_Y[0] + offset))
    y1 = max(20, min(33, COMFORT_HEATING_CURVE_Y[1] + offset))
    y2 = max(18, min(28, COMFORT_HEATING_CURVE_Y[2] + round(offset * 0.75)))
    y3 = max(18, min(24, COMFORT_HEATING_CURVE_Y[3] + round(offset * 0.5)))
    y4 = 18
    return [y0, y1, y2, y3, y4]


def room_temperature_from_curve(at_minus_20c: int) -> float:
    """Derive the effective room temperature setpoint (°C) from the -20°C curve point."""
    if at_minus_20c <= 22:
        return 18.0
    raw = DEFAULT_ROOM_TARGET + (int(at_minus_20c) - COMFORT_HEATING_CURVE_Y[0]) * 0.25
    stepped = round(raw * 2.0) / 2.0
    return max(MIN_ROOM_TEMP, min(MAX_ROOM_TEMP, stepped))


@dataclass(frozen=True, kw_only=True)
class WeheatClimateEntityDescription(ClimateEntityDescription):
    """Describes the Weheat heating curve thermostat climate entity."""


CLIMATE_DESCRIPTION = WeheatClimateEntityDescription(
    key="thermostat",
    translation_key="thermostat",
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: WeheatConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up Weheat climate entity from a config entry."""
    entities: list[ClimateEntity] = []

    for weheatdata in entry.runtime_data:
        info = weheatdata.heat_pump_info
        client = weheatdata.client
        session = weheatdata.session

        settings_coord = getattr(weheatdata, "settings_coordinator", None)
        coordinator = settings_coord or weheatdata.data_coordinator

        entities.append(
            WeheatHeatingThermostat(
                heat_pump_info=info,
                coordinator=coordinator,
                data_coordinator=weheatdata.data_coordinator,
                entity_description=CLIMATE_DESCRIPTION,
                client=client,
                session=session,
            )
        )

    async_add_entities(entities)


class WeheatHeatingThermostat(WeheatControlEntity, ClimateEntity):
    """Smooth Heating-Curve Thermostat for Weheat heat pumps."""

    entity_description: WeheatClimateEntityDescription
    _enable_turn_on_off_backwards_compatibility = False

    def __init__(
        self,
        heat_pump_info: HeatPumpInfo,
        coordinator: Any,
        data_coordinator: Any = None,
        entity_description: WeheatClimateEntityDescription = CLIMATE_DESCRIPTION,
        client: WeheatClient | None = None,
        session: Any | None = None,
    ) -> None:
        """Initialize the Weheat climate thermostat entity."""
        super().__init__(
            heat_pump_info, coordinator, entity_description, client, session
        )
        self._data_coordinator = data_coordinator
        self._attr_icon = "mdi:thermostat"
        self._attr_native_temperature_unit = UnitOfTemperature.CELSIUS
        self._attr_hvac_modes = [HVACMode.HEAT, HVACMode.AUTO, HVACMode.OFF]
        self._attr_preset_modes = list(CLIMATE_PRESET_MODES)
        self._attr_supported_features = (
            ClimateEntityFeature.TARGET_TEMPERATURE
            | ClimateEntityFeature.PRESET_MODE
            | ClimateEntityFeature.TURN_ON
            | ClimateEntityFeature.TURN_OFF
        )
        self._attr_min_temp = MIN_ROOM_TEMP
        self._attr_max_temp = MAX_ROOM_TEMP
        self._attr_target_temperature_step = ROOM_TEMP_STEP
        self._last_valid_room_temp: float | None = None

    def _get_log(self) -> HeatPumpLog | None:
        """Return latest HeatPumpLog from data_coordinator if available."""
        if self._data_coordinator is not None:
            data = getattr(self._data_coordinator, "data", None)
            if isinstance(data, HeatPumpLog):
                return data
        data = getattr(self.coordinator, "data", None)
        if isinstance(data, HeatPumpLog):
            return data
        return None

    @property
    def native_current_temperature(self) -> float | None:
        """Return the current room temperature (°C)."""
        log = self._get_log()
        if log is not None and log.t_room is not None:
            t_room = float(log.t_room)
            if 5.0 <= t_room <= 40.0:
                self._last_valid_room_temp = round(t_room, 1)
                return self._last_valid_room_temp
        return self._last_valid_room_temp

    @property
    def native_target_temperature(self) -> float | None:
        """Return the target room temperature (°C)."""
        settings = self._get_settings()
        log = self._get_log()
        if (
            settings is not None
            and settings.control_method == ControlMethod.SMART_OPEN_THERM
        ):
            if log is not None and log.t_room_target is not None:
                t_target = float(log.t_room_target)
                if 10.0 <= t_target <= 30.0:
                    return round(t_target, 1)
            return DEFAULT_ROOM_TARGET

        if settings is not None:
            return room_temperature_from_curve(settings.heating_curve_at_minus_20c)
        return DEFAULT_ROOM_TARGET

    @property
    def hvac_mode(self) -> HVACMode:
        """Return current HVAC operating mode."""
        settings = self._get_settings()
        if settings is None:
            return HVACMode.AUTO
        if settings.control_method == ControlMethod.SMART_OPEN_THERM:
            return HVACMode.AUTO
        if settings.heating_curve_at_minus_20c <= 22:
            return HVACMode.OFF
        return HVACMode.HEAT

    @property
    def hvac_action(self) -> HVACAction:
        """Return current HVAC action from real-time heat pump telemetry."""
        if self.hvac_mode == HVACMode.OFF:
            return HVACAction.OFF
        log = self._get_log()
        if log is None or log.state is None:
            return HVACAction.IDLE
        if log.state in (HeatPumpState.HEATING, HeatPumpState.DEFROSTING):
            return HVACAction.HEATING
        if log.state == HeatPumpState.COOLING:
            return HVACAction.COOLING
        return HVACAction.IDLE

    @property
    def preset_mode(self) -> str | None:
        """Return the active preset mode."""
        settings = self._get_settings()
        if settings is None:
            return None
        if settings.control_method == ControlMethod.SMART_OPEN_THERM:
            return PRESET_OPENTHERM
        at_m20 = settings.heating_curve_at_minus_20c
        if at_m20 <= 22:
            return PRESET_STANDBY
        if at_m20 <= 35:
            return PRESET_ECO
        if at_m20 >= 39:
            return PRESET_BOOST
        return PRESET_COMFORT

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic heating curve and water target attributes."""
        settings = self._get_settings()
        log = self._get_log()
        attrs: dict[str, Any] = {}
        if settings is not None:
            attrs["control_method_id"] = settings.control_method
            attrs["heating_curve_x"] = list(DEFAULT_HEATING_CURVE_X)
            attrs["heating_curve_y"] = [
                settings.heating_curve_at_minus_20c,
                settings.heating_curve_at_9c,
                settings.heating_curve_at_15c,
                settings.heating_curve_at_20c,
                settings.heating_curve_at_25c,
            ]
            if log is not None and log.t_air_in is not None:
                attrs["outside_temperature"] = round(float(log.t_air_in), 1)
                attrs["calculated_curve_water_target"] = (
                    settings.interpolate_heating_curve(float(log.t_air_in))
                )
        if log is not None and log.t_thermostat_setpoint is not None:
            attrs["live_water_target_temperature"] = round(
                float(log.t_thermostat_setpoint), 1
            )
        return attrs

    async def _async_apply_heating_settings(
        self,
        *,
        control_method: int,
        curve_y: list[int] | None = None,
    ) -> None:
        """Send atomic heating control method and curve PATCH to the control board."""
        if self._session is not None and hasattr(self._session, "check_portal_auth"):
            self._session.check_portal_auth()

        if self._client is None:
            raise HomeAssistantError("Weheat client is not available")

        try:
            token = await self._async_get_token()
            control_board_id = await self._async_get_control_board_id(token)

            settings_data = self._get_settings() or ControlBoardSettings()
            payload = settings_data.build_heating_curve_payload(
                control_method=control_method,
                curve_y=curve_y,
            )

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
            _LOGGER.error("Failed to update thermostat heating curve: %s", err)
            raise HomeAssistantError(
                f"Failed to update thermostat heating curve: {err}"
            ) from err
        except HomeAssistantError:
            raise
        except Exception as err:
            _LOGGER.error("Unexpected error updating thermostat heating curve: %s", err)
            raise HomeAssistantError(
                f"Unexpected error updating thermostat heating curve: {err}"
            ) from err

    async def async_set_temperature(self, **kwargs: Any) -> None:
        """Set new target room temperature by shifting the 5-point heating curve."""
        temp = kwargs.get(ATTR_TEMPERATURE)
        if temp is None:
            return
        curve_y = curve_for_room_temperature(float(temp))
        await self._async_apply_heating_settings(
            control_method=int(ControlMethod.HEATING_CURVE),
            curve_y=curve_y,
        )

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        """Set new target HVAC mode (HEAT, AUTO, OFF)."""
        if hvac_mode == HVACMode.OFF:
            await self._async_apply_heating_settings(
                control_method=int(ControlMethod.HEATING_CURVE),
                curve_y=list(STANDBY_HEATING_CURVE_Y),
            )
        elif hvac_mode == HVACMode.AUTO:
            await self._async_apply_heating_settings(
                control_method=int(ControlMethod.SMART_OPEN_THERM),
                curve_y=None,
            )
        elif hvac_mode == HVACMode.HEAT:
            settings = self._get_settings()
            current_y = (
                [
                    settings.heating_curve_at_minus_20c,
                    settings.heating_curve_at_9c,
                    settings.heating_curve_at_15c,
                    settings.heating_curve_at_20c,
                    settings.heating_curve_at_25c,
                ]
                if settings is not None
                and 25 <= settings.heating_curve_at_minus_20c <= 40
                else list(COMFORT_HEATING_CURVE_Y)
            )
            await self._async_apply_heating_settings(
                control_method=int(ControlMethod.HEATING_CURVE),
                curve_y=current_y,
            )
        else:
            raise HomeAssistantError(f"Unsupported HVAC mode: {hvac_mode}")

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        """Set new preset mode (comfort, eco, boost, standby, opentherm)."""
        norm = preset_mode.strip().lower()
        if norm == PRESET_COMFORT:
            await self._async_apply_heating_settings(
                control_method=int(ControlMethod.HEATING_CURVE),
                curve_y=list(COMFORT_HEATING_CURVE_Y),
            )
        elif norm == PRESET_ECO:
            await self._async_apply_heating_settings(
                control_method=int(ControlMethod.HEATING_CURVE),
                curve_y=list(ECO_HEATING_CURVE_Y),
            )
        elif norm == PRESET_BOOST:
            await self._async_apply_heating_settings(
                control_method=int(ControlMethod.HEATING_CURVE),
                curve_y=list(BOOST_HEATING_CURVE_Y),
            )
        elif norm == PRESET_STANDBY:
            await self._async_apply_heating_settings(
                control_method=int(ControlMethod.HEATING_CURVE),
                curve_y=list(STANDBY_HEATING_CURVE_Y),
            )
        elif norm == PRESET_OPENTHERM:
            await self._async_apply_heating_settings(
                control_method=int(ControlMethod.SMART_OPEN_THERM),
                curve_y=None,
            )
        else:
            raise ValueError(
                f"Invalid preset mode '{preset_mode}'. Must be one of {CLIMATE_PRESET_MODES}"
            )

    async def async_turn_on(self) -> None:
        """Turn the thermostat on to HEAT mode."""
        await self.async_set_hvac_mode(HVACMode.HEAT)

    async def async_turn_off(self) -> None:
        """Turn the thermostat off to standby curve."""
        await self.async_set_hvac_mode(HVACMode.OFF)
