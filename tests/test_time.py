"""Unit tests for Weheat quiet hours schedule TimeEntity platform."""

from __future__ import annotations

from datetime import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.exceptions import HomeAssistantError

from custom_components.weheat.client import WeheatApiError
from custom_components.weheat.const import DAYS_OF_WEEK, DOMAIN
from custom_components.weheat.coordinator import WeheatData
from custom_components.weheat.models import (
    ControlBoardSettings,
    HeatPumpInfo,
)
from custom_components.weheat.time import (
    TIME_ENTITIES,
    WeheatTimeEntity,
    async_setup_entry,
)
from tests.conftest import (
    TEST_CB_ID_BLACKBIRD,
    TEST_HP_UUID_BLACKBIRD,
    TEST_INSTALLATION_ID,
    TEST_SETTINGS_REQUEST_ID,
    load_fixture,
)


@pytest.fixture
def sample_settings() -> ControlBoardSettings:
    """Instantiate ControlBoardSettings from fixture."""
    return ControlBoardSettings.from_api_dict(
        load_fixture("installation_settings_get.json")
    )


@pytest.fixture
def mock_hp_info() -> HeatPumpInfo:
    """Create sample HeatPumpInfo."""
    return HeatPumpInfo(
        heatpump_id=TEST_HP_UUID_BLACKBIRD,
        name="Blackbird Outdoor",
        model=0,
        serial_number="WH-BB80-MOCK-001",
        control_board_id=TEST_CB_ID_BLACKBIRD,
        installation_id=TEST_INSTALLATION_ID,
    )


@pytest.fixture
def mock_settings_coordinator(sample_settings: ControlBoardSettings) -> MagicMock:
    """Create mock settings coordinator with sample settings data."""
    coord = MagicMock()
    coord.data = sample_settings
    coord.async_request_refresh = AsyncMock()
    coord.session = None
    coord.client = MagicMock()
    return coord


def test_time_entities_inventory() -> None:
    """Verify exactly 14 time entities are registered (7 days * 2 actions)."""
    assert len(TIME_ENTITIES) == 14
    for day in DAYS_OF_WEEK:
        start_key = f"{day.lower()}_silent_hours_start"
        stop_key = f"{day.lower()}_silent_hours_stop"
        start_desc = next((d for d in TIME_ENTITIES if d.key == start_key), None)
        stop_desc = next((d for d in TIME_ENTITIES if d.key == stop_key), None)
        assert start_desc is not None
        assert start_desc.is_start is True
        assert start_desc.day == day
        assert stop_desc is not None
        assert stop_desc.is_start is False
        assert stop_desc.day == day


def test_time_entity_properties_from_fixture(
    mock_hp_info: HeatPumpInfo, mock_settings_coordinator: MagicMock
) -> None:
    """Verify native_value matches fixture data for various days."""
    monday_start_desc = next(
        d for d in TIME_ENTITIES if d.key == "monday_silent_hours_start"
    )
    monday_start_entity = WeheatTimeEntity(
        heat_pump_info=mock_hp_info,
        coordinator=mock_settings_coordinator,
        entity_description=monday_start_desc,
    )
    # Fixture has SilentHoursMondayStart: 19, SilentMinutesMondayStart: 0
    assert monday_start_entity.native_value == time(19, 0)
    assert monday_start_entity.icon == "mdi:clock-start"
    assert (
        monday_start_entity.unique_id
        == f"{TEST_HP_UUID_BLACKBIRD}_monday_silent_hours_start"
    )
    assert monday_start_entity.device_info["identifiers"] == {
        (DOMAIN, TEST_HP_UUID_BLACKBIRD)
    }

    monday_stop_desc = next(
        d for d in TIME_ENTITIES if d.key == "monday_silent_hours_stop"
    )
    monday_stop_entity = WeheatTimeEntity(
        heat_pump_info=mock_hp_info,
        coordinator=mock_settings_coordinator,
        entity_description=monday_stop_desc,
    )
    # Fixture has SilentHoursMondayStop: 7, SilentMinutesMondayStop: 0
    assert monday_stop_entity.native_value == time(7, 0)
    assert monday_stop_entity.icon == "mdi:clock-end"


@pytest.mark.asyncio
async def test_time_entity_async_set_value_success(
    mock_hp_info: HeatPumpInfo, mock_settings_coordinator: MagicMock
) -> None:
    """Verify setting time mutates client, polls settings, updates local state, and refreshes."""
    mock_client = MagicMock()
    mock_client.async_set_silent_schedule = AsyncMock(
        return_value=TEST_SETTINGS_REQUEST_ID
    )
    mock_client.async_poll_settings_request = AsyncMock(
        return_value={"status": 2, "settingsRequestId": TEST_SETTINGS_REQUEST_ID}
    )
    mock_settings_coordinator.client = mock_client

    wednesday_start_desc = next(
        d for d in TIME_ENTITIES if d.key == "wednesday_silent_hours_start"
    )
    entity = WeheatTimeEntity(
        heat_pump_info=mock_hp_info,
        coordinator=mock_settings_coordinator,
        entity_description=wednesday_start_desc,
        client=mock_client,
    )

    new_time = time(22, 30)
    await entity.async_set_value(new_time)

    mock_client.async_set_silent_schedule.assert_awaited_once_with(
        control_board_id=TEST_CB_ID_BLACKBIRD,
        token=None,
        schedule={
            "SilentHoursWednesdayStart": 22,
            "SilentMinutesWednesdayStart": 30,
        },
    )
    mock_client.async_poll_settings_request.assert_awaited_once_with(
        control_board_id=TEST_CB_ID_BLACKBIRD,
        request_id=TEST_SETTINGS_REQUEST_ID,
        token=None,
    )
    mock_settings_coordinator.async_request_refresh.assert_awaited_once()

    # Verify optimistic update changed the coordinator's local data
    schedule = mock_settings_coordinator.data.schedules["Wednesday"]
    assert schedule.start_hour == 22
    assert schedule.start_minute == 30


@pytest.mark.asyncio
async def test_time_entity_async_set_value_api_error(
    mock_hp_info: HeatPumpInfo, mock_settings_coordinator: MagicMock
) -> None:
    """Verify API error raises HomeAssistantError and refreshes coordinator."""
    mock_client = MagicMock()
    mock_client.async_set_silent_schedule = AsyncMock(
        side_effect=WeheatApiError("Device communication error")
    )
    mock_settings_coordinator.client = mock_client

    tuesday_stop_desc = next(
        d for d in TIME_ENTITIES if d.key == "tuesday_silent_hours_stop"
    )
    entity = WeheatTimeEntity(
        heat_pump_info=mock_hp_info,
        coordinator=mock_settings_coordinator,
        entity_description=tuesday_stop_desc,
        client=mock_client,
    )

    with pytest.raises(HomeAssistantError, match="Failed to update quiet time"):
        await entity.async_set_value(time(8, 15))

    mock_settings_coordinator.async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_time_entity_async_set_value_invalid_type(
    mock_hp_info: HeatPumpInfo, mock_settings_coordinator: MagicMock
) -> None:
    """Verify non-time value raises ValueError."""
    desc = TIME_ENTITIES[0]
    entity = WeheatTimeEntity(
        heat_pump_info=mock_hp_info,
        coordinator=mock_settings_coordinator,
        entity_description=desc,
    )

    with pytest.raises(ValueError, match="Expected datetime.time"):
        await entity.async_set_value("22:00")  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_async_setup_entry_time(
    mock_hp_info: HeatPumpInfo, mock_settings_coordinator: MagicMock
) -> None:
    """Verify async_setup_entry forwards all 14 time entities."""
    hass = MagicMock()
    data_item = WeheatData(
        heat_pump_info=mock_hp_info,
        data_coordinator=mock_settings_coordinator,
        energy_coordinator=MagicMock(),
        settings_coordinator=mock_settings_coordinator,
        client=MagicMock(),
        session=MagicMock(),
    )
    entry = ConfigEntry(runtime_data=[data_item])

    added_entities: list[Any] = []
    await async_setup_entry(hass, entry, added_entities.extend)

    assert len(added_entities) == 14
    keys = {e.entity_description.key for e in added_entities}
    for day in DAYS_OF_WEEK:
        assert f"{day.lower()}_silent_hours_start" in keys
        assert f"{day.lower()}_silent_hours_stop" in keys
