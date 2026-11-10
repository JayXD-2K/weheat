"""Unit tests for Weheat heating control method, 5-point heating curve, and climate thermostat."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.components.climate import HVACAction, HVACMode
from homeassistant.const import ATTR_TEMPERATURE, CONF_ACCESS_TOKEN, CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.weheat.climate import (
    PRESET_BOOST,
    PRESET_COMFORT,
    PRESET_ECO,
    PRESET_OPENTHERM,
    PRESET_STANDBY,
    WeheatHeatingThermostat,
    curve_for_room_temperature,
    room_temperature_from_curve,
)
from custom_components.weheat.climate import (
    async_setup_entry as async_setup_climate_entry,
)
from custom_components.weheat.coordinator import WeheatData
from custom_components.weheat.models import (
    ControlBoardSettings,
    ControlMethod,
    HeatPumpInfo,
    HeatPumpLog,
    HeatPumpState,
)
from custom_components.weheat.number import (
    HEATING_NUMBER_DESCRIPTIONS,
    WeheatHeatingCurveNumber,
)
from custom_components.weheat.select import (
    SELECT_ENTITIES,
    WeheatControlMethodSelect,
)
from custom_components.weheat.services import (
    SERVICE_SET_HEATING_CURVE,
    async_setup_services,
)
from tests.ha_compat import ConfigEntry

TEST_HP_UUID = "99999999-8888-7777-6666-555555555555"
TEST_CB_UUID = "cb-99999999"


@pytest.fixture
def mock_hp_info() -> HeatPumpInfo:
    """Return mock HeatPumpInfo."""
    return HeatPumpInfo(
        heatpump_id=TEST_HP_UUID,
        name="House Heat Pump",
        model=1,
        serial_number="SN-12345",
        has_dhw=True,
        control_board_id=TEST_CB_UUID,
    )


def test_heating_curve_parsing_and_interpolation() -> None:
    """Verify ControlBoardSettings parses controlMethod and heatingCurveY and interpolates."""
    raw = {
        "settings": {
            "controlMethod": 1,
            "heatingCurveX": [-20, 9, 15, 20, 25],
            "heatingCurveY": [37, 28, 24, 20, 18],
        }
    }
    settings = ControlBoardSettings.from_api_dict(raw)
    assert settings.control_method == ControlMethod.HEATING_CURVE
    assert settings.heating_curve_at_minus_20c == 37
    assert settings.heating_curve_at_9c == 28
    assert settings.heating_curve_at_15c == 24
    assert settings.heating_curve_at_20c == 20
    assert settings.heating_curve_at_25c == 18

    assert settings.interpolate_heating_curve(-25.0) == 37.0
    assert settings.interpolate_heating_curve(-20.0) == 37.0
    assert settings.interpolate_heating_curve(0.0) == 30.8
    assert settings.interpolate_heating_curve(9.0) == 28.0
    assert settings.interpolate_heating_curve(12.0) == 26.0
    assert settings.interpolate_heating_curve(25.0) == 18.0
    assert settings.interpolate_heating_curve(30.0) == 18.0


def test_build_heating_curve_payload() -> None:
    """Verify build_heating_curve_payload matches Weheat Portal specification."""
    settings = ControlBoardSettings(
        control_method=ControlMethod.HEATING_CURVE,
        heating_curve_y=[42, 27, 23, 20, 18],
    )
    payload = settings.build_heating_curve_payload(at_minus_20c=37, at_9c=28)
    assert payload == {
        "settings": {
            "ControlMethod": 1,
            "HeatingCurveY": [37, 28, 23, 20, 18],
        },
        "updateImmediately": False,
    }

    # Switching to OpenTherm without curve overrides sends ControlMethod=4
    ot_payload = settings.build_heating_curve_payload(
        control_method=ControlMethod.SMART_OPEN_THERM
    )
    assert ot_payload == {
        "settings": {
            "ControlMethod": 4,
        },
        "updateImmediately": False,
    }


@pytest.mark.asyncio
async def test_control_method_select_entity(mock_hp_info: HeatPumpInfo) -> None:
    """Verify WeheatControlMethodSelect reads and updates ControlMethod."""
    coord = MagicMock()
    coord.data = ControlBoardSettings(
        control_method=ControlMethod.SMART_OPEN_THERM,
        heating_curve_y=[37, 28, 24, 20, 18],
    )
    coord.async_request_refresh = AsyncMock()

    client = MagicMock()
    client.async_patch_control_board_settings = AsyncMock(return_value="req-1")
    client.async_poll_settings_request = AsyncMock(return_value={"status": 2})

    session = MagicMock()
    session.token = {CONF_ACCESS_TOKEN: "tok"}
    session.async_ensure_token_valid = AsyncMock()

    entity = WeheatControlMethodSelect(
        heat_pump_info=mock_hp_info,
        coordinator=coord,
        entity_description=SELECT_ENTITIES[2],
        client=client,
        session=session,
    )
    assert entity.current_option == "OpenTherm thermostat"

    await entity.async_select_option("Heating curve")
    client.async_patch_control_board_settings.assert_awaited_once_with(
        control_board_id=TEST_CB_UUID,
        token="tok",
        settings={
            "settings": {
                "ControlMethod": 1,
                "HeatingCurveY": [37, 28, 24, 20, 18],
            },
            "updateImmediately": False,
        },
    )


@pytest.mark.asyncio
async def test_heating_curve_number_entities(mock_hp_info: HeatPumpInfo) -> None:
    """Verify all 5 WeheatHeatingCurveNumber entities read and update their curve points."""
    coord = MagicMock()
    coord.data = ControlBoardSettings(
        control_method=ControlMethod.HEATING_CURVE,
        heating_curve_y=[42, 27, 23, 20, 18],
    )
    coord.async_request_refresh = AsyncMock()

    client = MagicMock()
    client.async_patch_control_board_settings = AsyncMock(return_value="req-hc")
    client.async_poll_settings_request = AsyncMock(return_value={"status": 2})

    session = MagicMock()
    session.token = {CONF_ACCESS_TOKEN: "tok"}
    session.async_ensure_token_valid = AsyncMock()

    num_m20 = WeheatHeatingCurveNumber(
        heat_pump_info=mock_hp_info,
        coordinator=coord,
        entity_description=HEATING_NUMBER_DESCRIPTIONS[0],
        client=client,
        session=session,
    )
    assert num_m20.native_value == 42.0
    await num_m20.async_set_native_value(36.0)
    client.async_patch_control_board_settings.assert_awaited_once_with(
        control_board_id=TEST_CB_UUID,
        token="tok",
        settings={
            "settings": {
                "ControlMethod": 1,
                "HeatingCurveY": [36, 27, 23, 20, 18],
            },
            "updateImmediately": False,
        },
    )

    with pytest.raises(HomeAssistantError):
        await num_m20.async_set_native_value(75.0)


@pytest.mark.asyncio
async def test_set_heating_curve_service(mock_hp_info: HeatPumpInfo) -> None:
    """Verify weheat.set_heating_curve action updates control method and curve atomically."""
    hass = HomeAssistant()
    coord = MagicMock()
    coord.data = ControlBoardSettings(
        control_method=ControlMethod.SMART_OPEN_THERM,
        heating_curve_y=[42, 27, 23, 20, 18],
    )
    coord.async_request_refresh = AsyncMock()

    data_coord = MagicMock()
    data_coord.async_request_refresh = AsyncMock()

    client = MagicMock()
    client.async_patch_control_board_settings = AsyncMock(return_value="req-svc")
    client.async_poll_settings_request = AsyncMock(return_value={"status": 2})

    session = MagicMock()
    session.token = {CONF_ACCESS_TOKEN: "tok"}
    session.async_ensure_token_valid = AsyncMock()

    weheat_data = WeheatData(
        client=client,
        session=session,
        heat_pump_info=mock_hp_info,
        data_coordinator=data_coord,
        energy_coordinator=MagicMock(),
        settings_coordinator=coord,
    )
    entry = ConfigEntry(
        entry_id="entry_hc",
        data={CONF_TOKEN: {CONF_ACCESS_TOKEN: "tok"}},
        runtime_data=[weheat_data],
    )
    hass.config_entries.async_add(entry)
    await async_setup_services(hass)

    await hass.services.async_call(
        "weheat",
        SERVICE_SET_HEATING_CURVE,
        {
            "control_method": "Heating curve",
            "at_minus_20c": 37,
            "at_9c": 28,
            "at_15c": 24,
            "at_20c": 20,
            "at_25c": 18,
        },
    )
    client.async_patch_control_board_settings.assert_awaited_once_with(
        control_board_id=TEST_CB_UUID,
        token="tok",
        settings={
            "settings": {
                "ControlMethod": 1,
                "HeatingCurveY": [37, 28, 24, 20, 18],
            },
            "updateImmediately": False,
        },
    )


@pytest.mark.asyncio
async def test_climate_thermostat_entity(mock_hp_info: HeatPumpInfo) -> None:
    """Verify WeheatHeatingThermostat modes, presets, and temperature curve shifting."""
    assert curve_for_room_temperature(21.0) == [37, 28, 24, 20, 18]
    assert curve_for_room_temperature(20.5) == [35, 26, 22, 19, 18]
    assert curve_for_room_temperature(21.5) == [39, 30, 26, 21, 18]
    assert curve_for_room_temperature(18.0) == [20, 18, 18, 18, 18]
    assert room_temperature_from_curve(37) == 21.0
    assert room_temperature_from_curve(35) == 20.5
    assert room_temperature_from_curve(39) == 21.5
    assert room_temperature_from_curve(20) == 18.0

    settings_coord = MagicMock()
    settings_coord.data = ControlBoardSettings(
        control_method=ControlMethod.HEATING_CURVE,
        heating_curve_y=[37, 28, 24, 20, 18],
    )
    settings_coord.async_request_refresh = AsyncMock()

    data_coord = MagicMock()
    data_coord.data = HeatPumpLog(
        heat_pump_id=TEST_HP_UUID,
        state=HeatPumpState.HEATING,
        t_room=20.8,
        t_room_target=21.0,
        t_air_in=0.0,
        t_thermostat_setpoint=30.8,
    )

    client = MagicMock()
    client.async_patch_control_board_settings = AsyncMock(return_value="req-clim")
    client.async_poll_settings_request = AsyncMock(return_value={"status": 2})

    session = MagicMock()
    session.token = {CONF_ACCESS_TOKEN: "tok"}
    session.async_ensure_token_valid = AsyncMock()

    weheat_data = WeheatData(
        client=client,
        session=session,
        heat_pump_info=mock_hp_info,
        data_coordinator=data_coord,
        energy_coordinator=MagicMock(),
        settings_coordinator=settings_coord,
    )
    entry = ConfigEntry(runtime_data=[weheat_data])
    added_climates: list[WeheatHeatingThermostat] = []
    await async_setup_climate_entry(HomeAssistant(), entry, added_climates.extend)
    assert len(added_climates) == 1

    thermostat = added_climates[0]
    assert thermostat.current_temperature == 20.8
    assert thermostat.target_temperature == 21.0
    assert thermostat.hvac_mode == HVACMode.HEAT
    assert thermostat.hvac_action == HVACAction.HEATING
    assert thermostat.preset_mode == PRESET_COMFORT
    assert thermostat.extra_state_attributes["calculated_curve_water_target"] == 30.8

    # Set preset to eco
    await thermostat.async_set_preset_mode(PRESET_ECO)
    client.async_patch_control_board_settings.assert_awaited_with(
        control_board_id=TEST_CB_UUID,
        token="tok",
        settings={
            "settings": {
                "ControlMethod": 1,
                "HeatingCurveY": [35, 26, 22, 19, 18],
            },
            "updateImmediately": False,
        },
    )

    # Set preset to boost
    await thermostat.async_set_preset_mode(PRESET_BOOST)
    client.async_patch_control_board_settings.assert_awaited_with(
        control_board_id=TEST_CB_UUID,
        token="tok",
        settings={
            "settings": {
                "ControlMethod": 1,
                "HeatingCurveY": [39, 30, 25, 21, 18],
            },
            "updateImmediately": False,
        },
    )

    # Set preset to standby
    await thermostat.async_set_preset_mode(PRESET_STANDBY)
    client.async_patch_control_board_settings.assert_awaited_with(
        control_board_id=TEST_CB_UUID,
        token="tok",
        settings={
            "settings": {
                "ControlMethod": 1,
                "HeatingCurveY": [20, 18, 18, 18, 18],
            },
            "updateImmediately": False,
        },
    )

    # Set preset to opentherm
    await thermostat.async_set_preset_mode(PRESET_OPENTHERM)
    client.async_patch_control_board_settings.assert_awaited_with(
        control_board_id=TEST_CB_UUID,
        token="tok",
        settings={
            "settings": {
                "ControlMethod": 4,
            },
            "updateImmediately": False,
        },
    )

    # Set target temperature to 20.5°C
    await thermostat.async_set_temperature(**{ATTR_TEMPERATURE: 20.5})
    client.async_patch_control_board_settings.assert_awaited_with(
        control_board_id=TEST_CB_UUID,
        token="tok",
        settings={
            "settings": {
                "ControlMethod": 1,
                "HeatingCurveY": [35, 26, 22, 19, 18],
            },
            "updateImmediately": False,
        },
    )
