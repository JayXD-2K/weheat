"""Tests for Weheat base entity architecture."""

from __future__ import annotations

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityDescription

from custom_components.weheat.coordinator import WeheatDataUpdateCoordinator
from custom_components.weheat.entity import WeheatEntity
from custom_components.weheat.models import HeatPumpInfo


@pytest.fixture
def mock_hp_info() -> HeatPumpInfo:
    return HeatPumpInfo(
        heatpump_id="00000000-0000-0000-0000-000000000001",
        name="Test Blackbird",
        model=0,
        serial_number="WH-BB80-001",
        has_dhw=True,
        has_ch_boiler=False,
        firmware_version="2.4.1",
        part_number="PN-12345",
    )


def test_weheat_entity_device_info_and_unique_id(mock_hp_info: HeatPumpInfo) -> None:
    """Verify 100% Core compatibility of DeviceInfo and unique_id."""
    hass = HomeAssistant()
    entry = ConfigEntry()
    coord = WeheatDataUpdateCoordinator(hass, entry, None, mock_hp_info)

    desc = EntityDescription(key="power_output")
    entity = WeheatEntity(mock_hp_info, coord, entity_description=desc)

    # 1. Device Registry Identifiers MUST match Core
    assert entity._attr_device_info.identifiers == {
        ("weheat", mock_hp_info.heatpump_id)
    }

    # 2. Device info metadata
    assert entity._attr_device_info.name == "Test Blackbird"
    assert entity._attr_device_info.manufacturer == "Weheat"
    assert entity._attr_device_info.model == "Blackbird P80 heat pump"
    assert entity._attr_device_info.serial_number == "WH-BB80-001"
    assert entity._attr_device_info.sw_version == "2.4.1"
    assert entity._attr_device_info.hw_version == "PN-12345"

    # 3. Unique ID MUST match Core formula
    assert entity._attr_unique_id == f"{mock_hp_info.heatpump_id}_power_output"
    assert entity._attr_has_entity_name is True


def test_unique_id_catalog(mock_hp_info: HeatPumpInfo) -> None:
    """Verify unique ID generation across representative sensor keys."""
    hass = HomeAssistant()
    entry = ConfigEntry()
    coord = WeheatDataUpdateCoordinator(hass, entry, None, mock_hp_info)

    test_keys = [
        "power_output",
        "power_input",
        "cop",
        "electricity_used",
        "energy_output",
        "indoor_unit_water_pump_state",
        "cooling_start_condition_outside_air_temperature",
    ]

    for key in test_keys:
        entity = WeheatEntity(
            mock_hp_info, coord, entity_description=EntityDescription(key=key)
        )
        expected_unique_id = f"{mock_hp_info.heatpump_id}_{key}"
        assert entity._attr_unique_id == expected_unique_id
