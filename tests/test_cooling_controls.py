"""Unit tests for Weheat Cooling Mode switch, Cooling Curve numbers, and set_cooling service."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONF_ACCESS_TOKEN,
    CONF_TOKEN,
    EntityCategory,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError

from custom_components.weheat.client import WeheatClient
from custom_components.weheat.const import DOMAIN
from custom_components.weheat.coordinator import WeheatData
from custom_components.weheat.models import (
    ControlBoardSettings,
    CoolingControlMethod,
    CoolingEnabledState,
    HeatPumpInfo,
)
from custom_components.weheat.number import (
    WeheatCoolingCurveNumber,
)
from custom_components.weheat.number import (
    async_setup_entry as async_setup_number_entry,
)
from custom_components.weheat.services import (
    SERVICE_SET_COOLING,
    async_handle_set_cooling,
    async_setup_services,
    async_unload_services,
)
from custom_components.weheat.switch import (
    COOLING_SWITCH_DESCRIPTION,
    WeheatCoolingModeSwitch,
)

TEST_HP_UUID = "00000000-0000-0000-0000-000000000001"
TEST_CB_ID = "cb-00000000-0000-0000-0000-000000000001"
TEST_REQ_ID = "req-00000000-0000-0000-0000-000000000123"


@pytest.fixture
def mock_hp_info() -> HeatPumpInfo:
    """Return a mock HeatPumpInfo instance."""
    return HeatPumpInfo(
        heatpump_id=TEST_HP_UUID,
        name="Blackbird P60",
        model=1,
        serial_number="WH-BB60-001",
        control_board_id=TEST_CB_ID,
        has_dhw=True,
    )


def test_cooling_settings_parsing_and_is_cooling_enabled() -> None:
    """Verify ControlBoardSettings parses cooling fields and evaluates getIsCoolingEnabled."""
    # 1. Live Blackbird P60 capture (coolingControlMethod=0, coolingEnabled=None) -> False
    live_payload = {
        "coolingControlMethod": 0,
        "coolingEnabled": None,
        "coolingMinSetpoint": 15,
        "coolingCurveX": [22, 40],
        "coolingCurveY": [18, 20],
        "consumerCoolingSetpoint": 18,
    }
    s1 = ControlBoardSettings.from_api_dict(live_payload)
    assert s1.cooling_control_method == CoolingControlMethod.NONE
    assert s1.cooling_enabled is None
    assert s1.cooling_min_setpoint == 15
    assert s1.cooling_curve_x == [22, 40]
    assert s1.cooling_curve_y == [18, 20]
    assert s1.cooling_curve_mild_temperature == 18
    assert s1.cooling_curve_peak_temperature == 20
    assert s1.is_cooling_enabled is False

    # 2. CoolingCurve active with coolingEnabled=None (Unset) -> True
    s2 = ControlBoardSettings.from_api_dict(
        {
            "coolingControlMethod": CoolingControlMethod.COOLING_CURVE,
            "coolingEnabled": None,
            "coolingCurveY": [17, 19],
        }
    )
    assert s2.is_cooling_enabled is True
    assert s2.cooling_curve_mild_temperature == 17
    assert s2.cooling_curve_peak_temperature == 19

    # 3. CoolingCurve with coolingEnabled=Off (1) -> False
    s3 = ControlBoardSettings.from_api_dict(
        {
            "coolingControlMethod": CoolingControlMethod.COOLING_CURVE,
            "coolingEnabled": CoolingEnabledState.OFF,
        }
    )
    assert s3.is_cooling_enabled is False

    # 4. OpenTherm (4) with coolingEnabled=Enabled (2) -> True
    s4 = ControlBoardSettings.from_api_dict(
        {
            "coolingControlMethod": CoolingControlMethod.OPEN_THERM,
            "coolingEnabled": CoolingEnabledState.ENABLED,
        }
    )
    assert s4.is_cooling_enabled is True


def test_build_cooling_payload_three_branch_portal_logic() -> None:
    """Verify build_cooling_payload matches all 3 branches of portal handleSubmit."""
    # Branch 1: coolingEnabled is None/0 (Unset) -> sets CoolingControlMethod: 2 (on) or 0 (off)
    s_unset = ControlBoardSettings(
        cooling_control_method=0,
        cooling_enabled=None,
        cooling_curve_y=[18, 20],
    )
    assert s_unset.build_cooling_payload(enabled=True) == {
        "settings": {
            "CoolingCurveY": [18, 20],
            "CoolingControlMethod": 2,
        },
        "updateImmediately": False,
    }
    assert s_unset.build_cooling_payload(enabled=False) == {
        "settings": {
            "CoolingCurveY": [18, 20],
            "CoolingControlMethod": 0,
        },
        "updateImmediately": False,
    }

    # Branch 2: coolingEnabled is Off (1) and coolingControlMethod is None (0) -> enabling sets both
    s_both = ControlBoardSettings(
        cooling_control_method=0,
        cooling_enabled=1,
        cooling_curve_y=[18, 20],
    )
    assert s_both.build_cooling_payload(enabled=True) == {
        "settings": {
            "CoolingCurveY": [18, 20],
            "CoolingEnabled": 2,
            "CoolingControlMethod": 2,
        },
        "updateImmediately": False,
    }

    # Branch 3: coolingEnabled is set (1 or 2) and coolingControlMethod is OpenTherm (4) -> preserves method
    s_opentherm = ControlBoardSettings(
        cooling_control_method=4,
        cooling_enabled=2,
        cooling_curve_y=[18, 20],
    )
    assert s_opentherm.build_cooling_payload(
        enabled=False, mild_temperature=16, peak_temperature=19
    ) == {
        "settings": {
            "CoolingCurveY": [16, 19],
            "CoolingEnabled": 1,
        },
        "updateImmediately": False,
    }
    assert s_opentherm.build_cooling_payload(
        enabled=True, mild_temperature=17, peak_temperature=21
    ) == {
        "settings": {
            "CoolingCurveY": [17, 21],
            "CoolingEnabled": 2,
        },
        "updateImmediately": False,
    }


@pytest.mark.asyncio
async def test_cooling_mode_switch_turn_on_and_off(
    mock_hp_info: HeatPumpInfo,
) -> None:
    """Verify WeheatCoolingModeSwitch toggles cooling mode via portal settings API."""
    settings = ControlBoardSettings(
        cooling_control_method=0,
        cooling_enabled=None,
        cooling_curve_y=[18, 20],
    )
    coord = MagicMock()
    coord.data = settings
    coord.async_request_refresh = AsyncMock()

    client = MagicMock(spec=WeheatClient)
    client.async_patch_control_board_settings = AsyncMock(return_value=TEST_REQ_ID)
    client.async_poll_settings_request = AsyncMock(return_value={"status": 2})

    session = MagicMock()
    session.async_ensure_token_valid = AsyncMock()
    session.token = {CONF_ACCESS_TOKEN: "mock_token"}

    switch = WeheatCoolingModeSwitch(
        heat_pump_info=mock_hp_info,
        coordinator=coord,
        entity_description=COOLING_SWITCH_DESCRIPTION,
        client=client,
        session=session,
    )

    assert switch.unique_id == f"{TEST_HP_UUID}_cooling_mode"
    assert switch.entity_description.entity_category is None
    assert switch.is_on is False

    # Turn ON
    await switch.async_turn_on()
    client.async_patch_control_board_settings.assert_awaited_once_with(
        control_board_id=TEST_CB_ID,
        token="mock_token",
        settings={
            "settings": {
                "CoolingCurveY": [18, 20],
                "CoolingControlMethod": 2,
            },
            "updateImmediately": False,
        },
    )
    client.async_poll_settings_request.assert_awaited_once_with(
        control_board_id=TEST_CB_ID,
        request_id=TEST_REQ_ID,
        token="mock_token",
    )
    coord.async_request_refresh.assert_awaited_once()

    # Simulate updated coordinator state
    settings.cooling_control_method = 2
    assert switch.is_on is True

    # Turn OFF
    client.async_patch_control_board_settings.reset_mock()
    await switch.async_turn_off()
    client.async_patch_control_board_settings.assert_awaited_once_with(
        control_board_id=TEST_CB_ID,
        token="mock_token",
        settings={
            "settings": {
                "CoolingCurveY": [18, 20],
                "CoolingControlMethod": 0,
            },
            "updateImmediately": False,
        },
    )


@pytest.mark.asyncio
async def test_cooling_curve_number_entities(
    mock_hp_info: HeatPumpInfo,
) -> None:
    """Verify WeheatCoolingCurveNumber reads and updates mild (22°C) and peak (40°C) water targets."""
    settings = ControlBoardSettings(
        cooling_control_method=2,
        cooling_enabled=None,
        cooling_min_setpoint=15,
        cooling_curve_y=[18, 20],
    )
    coord = MagicMock()
    coord.data = settings
    coord.async_request_refresh = AsyncMock()

    client = MagicMock(spec=WeheatClient)
    client.async_patch_control_board_settings = AsyncMock(return_value=TEST_REQ_ID)
    client.async_poll_settings_request = AsyncMock(return_value={"status": 2})

    session = MagicMock()
    session.async_ensure_token_valid = AsyncMock()
    session.token = {CONF_ACCESS_TOKEN: "mock_token"}

    data_item = WeheatData(
        heat_pump_info=mock_hp_info,
        data_coordinator=coord,
        energy_coordinator=MagicMock(),
        settings_coordinator=coord,
        client=client,
        session=session,
    )
    entry = ConfigEntry(runtime_data=[data_item])

    added_numbers: list[WeheatCoolingCurveNumber] = []
    await async_setup_number_entry(MagicMock(), entry, added_numbers.extend)
    assert len(added_numbers) == 2

    mild_num, peak_num = added_numbers[0], added_numbers[1]
    assert mild_num.unique_id == f"{TEST_HP_UUID}_cooling_curve_mild_temperature"
    assert peak_num.unique_id == f"{TEST_HP_UUID}_cooling_curve_peak_temperature"
    assert mild_num.entity_description.entity_category == EntityCategory.CONFIG
    assert peak_num.entity_description.entity_category == EntityCategory.CONFIG
    assert (
        mild_num.entity_description.native_unit_of_measurement
        == UnitOfTemperature.CELSIUS
    )
    assert mild_num.native_min_value == 15.0
    assert mild_num.native_max_value == 22.0
    assert mild_num.native_step == 1.0
    assert mild_num.native_value == 18.0
    assert peak_num.native_value == 20.0

    # Set mild temperature to 16°C -> preserves peak at 20°C and CoolingControlMethod=2
    await mild_num.async_set_native_value(16.0)
    client.async_patch_control_board_settings.assert_awaited_once_with(
        control_board_id=TEST_CB_ID,
        token="mock_token",
        settings={
            "settings": {
                "CoolingCurveY": [16, 20],
                "CoolingControlMethod": 2,
            },
            "updateImmediately": False,
        },
    )

    # Set peak temperature to 21°C -> preserves mild at 18°C
    client.async_patch_control_board_settings.reset_mock()
    await peak_num.async_set_native_value(21.0)
    client.async_patch_control_board_settings.assert_awaited_once_with(
        control_board_id=TEST_CB_ID,
        token="mock_token",
        settings={
            "settings": {
                "CoolingCurveY": [18, 21],
                "CoolingControlMethod": 2,
            },
            "updateImmediately": False,
        },
    )

    # Out-of-bounds value raises HomeAssistantError
    with pytest.raises(HomeAssistantError, match="between 15 and 22"):
        await mild_num.async_set_native_value(14.0)
    with pytest.raises(HomeAssistantError, match="between 15 and 22"):
        await peak_num.async_set_native_value(25.0)


@pytest.mark.asyncio
async def test_service_set_cooling(
    mock_hp_info: HeatPumpInfo,
) -> None:
    """Verify weheat.set_cooling service action enables cooling and sets curve temperatures."""
    hass = HomeAssistant()
    await async_setup_services(hass)
    assert hass.services.has_service(DOMAIN, SERVICE_SET_COOLING)

    settings = ControlBoardSettings(
        cooling_control_method=0,
        cooling_enabled=None,
        cooling_min_setpoint=15,
        cooling_curve_y=[18, 20],
    )
    data_coord = MagicMock()
    data_coord.async_request_refresh = AsyncMock()
    settings_coord = MagicMock()
    settings_coord.data = settings
    settings_coord.async_request_refresh = AsyncMock()

    client = MagicMock(spec=WeheatClient)
    client.async_patch_control_board_settings = AsyncMock(return_value=TEST_REQ_ID)
    client.async_poll_settings_request = AsyncMock(return_value={"status": 2})

    session = MagicMock()
    session.async_ensure_token_valid = AsyncMock()
    session.token = {CONF_ACCESS_TOKEN: "mock_token"}

    weheat_data = WeheatData(
        heat_pump_info=mock_hp_info,
        data_coordinator=data_coord,
        energy_coordinator=MagicMock(),
        settings_coordinator=settings_coord,
        client=client,
        session=session,
    )
    entry = ConfigEntry(
        entry_id="entry_1",
        data={CONF_TOKEN: {CONF_ACCESS_TOKEN: "mock_token"}},
        runtime_data=[weheat_data],
    )
    hass.config_entries.async_add(entry)

    call = ServiceCall(
        DOMAIN,
        SERVICE_SET_COOLING,
        {
            "enabled": True,
            "mild_temperature": 17,
            "peak_temperature": 19,
        },
    )
    await async_handle_set_cooling(hass, call)

    client.async_patch_control_board_settings.assert_awaited_once_with(
        control_board_id=TEST_CB_ID,
        token="mock_token",
        settings={
            "settings": {
                "CoolingCurveY": [17, 19],
                "CoolingControlMethod": 2,
            },
            "updateImmediately": False,
        },
    )
    data_coord.async_request_refresh.assert_awaited_once()
    settings_coord.async_request_refresh.assert_awaited_once()

    # Invalid parameters raise HomeAssistantError
    with pytest.raises(HomeAssistantError, match="enabled"):
        await async_handle_set_cooling(
            hass, ServiceCall(DOMAIN, SERVICE_SET_COOLING, {"enabled": "yes"})
        )
    with pytest.raises(HomeAssistantError, match="between 15 and 22"):
        await async_handle_set_cooling(
            hass,
            ServiceCall(
                DOMAIN,
                SERVICE_SET_COOLING,
                {"enabled": True, "mild_temperature": 12},
            ),
        )

    await async_unload_services(hass)
    assert not hass.services.has_service(DOMAIN, SERVICE_SET_COOLING)
