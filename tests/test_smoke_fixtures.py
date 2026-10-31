"""Smoke tests verifying mock API fixtures, schema integrity, and test harness."""

from __future__ import annotations

from typing import Any

import aiohttp
import pytest

from custom_components.weheat.const import DAYS_OF_WEEK
from custom_components.weheat.models import ControlBoardSettings
from tests.conftest import (
    API_BASE_URL,
    PORTAL_BASE_URL,
    TEST_CB_ID_BLACKBIRD,
    TEST_HP_UUID_BLACKBIRD,
    TEST_INSTALLATION_ID,
    TEST_SETTINGS_REQUEST_ID,
    TEST_USER_ID,
    load_fixture,
)

FIXTURE_NAMES = [
    "token_response.json",
    "user_me.json",
    "heat_pumps.json",
    "installations_fully_expanded.json",
    "latest_logs.json",
    "energy_total.json",
    "installation_settings_get.json",
    "installation_settings_pascal_get.json",
    "settings_update_success.json",
    "boost_trigger_success.json",
]


@pytest.mark.parametrize("filename", FIXTURE_NAMES)
def test_fixture_loads_successfully(filename: str) -> None:
    """Verify that every fixture in the catalog loads valid JSON."""
    data = load_fixture(filename)
    assert data is not None
    assert isinstance(data, (dict, list))
    assert len(data) > 0


def test_token_response_content(token_response_fixture: dict[str, Any]) -> None:
    """Verify token_response.json schema and expected fields."""
    assert "access_token" in token_response_fixture
    assert "token_type" in token_response_fixture
    assert token_response_fixture["token_type"] == "Bearer"
    assert token_response_fixture["expires_in"] == 300
    assert "refresh_token" in token_response_fixture


def test_user_me_content(user_me_fixture: dict[str, Any]) -> None:
    """Verify user_me.json schema and expected fields."""
    assert user_me_fixture["id"] == TEST_USER_ID
    assert user_me_fixture["firstName"] == "Jean"
    assert user_me_fixture["email"] == "jean.tester@example.com"
    assert "role" in user_me_fixture
    assert "language" in user_me_fixture


def test_heat_pumps_content(heat_pumps_fixture: dict[str, Any]) -> None:
    """Verify heat_pumps.json paged structure and multi-model coverage."""
    assert "metadata" in heat_pumps_fixture
    assert "data" in heat_pumps_fixture
    pumps = heat_pumps_fixture["data"]
    assert len(pumps) == 3

    # Model 0: Blackbird P80
    blackbird = pumps[0]
    assert blackbird["id"] == TEST_HP_UUID_BLACKBIRD
    assert blackbird["model"] == 0
    assert blackbird["controlBoardId"] == TEST_CB_ID_BLACKBIRD
    assert blackbird["dhwType"] == 1
    assert blackbird["boilerType"] == 2
    assert blackbird["status"] == 70

    # Model 2: Sparrow P60
    sparrow = pumps[1]
    assert sparrow["model"] == 2
    assert sparrow["dhwType"] == 0
    assert sparrow["status"] == 40

    # Model 5: Flint P40
    flint = pumps[2]
    assert flint["model"] == 5
    assert flint["dhwType"] == 1


def test_latest_logs_telemetry(latest_logs_fixture: dict[str, Any]) -> None:
    """Verify latest_logs.json contains essential operational telemetry."""
    assert latest_logs_fixture["heatPumpId"] == TEST_HP_UUID_BLACKBIRD
    assert latest_logs_fixture["isOnline"] is True
    assert latest_logs_fixture["state"] == 70

    # Core temperatures
    assert "tWaterIn" in latest_logs_fixture
    assert "tWaterOut" in latest_logs_fixture
    assert "tWaterHouseIn" in latest_logs_fixture
    assert "tAirIn" in latest_logs_fixture
    assert "tAirOut" in latest_logs_fixture

    # Compressor and flow metrics
    assert latest_logs_fixture["rpm"] == 2850
    assert latest_logs_fixture["pCompressorIn"] == 1120.0
    assert latest_logs_fixture["pCompressorOut"] == 4700.0
    assert latest_logs_fixture["centralHeatingFlow"] == 35

    # Control bridge status decodes
    assert latest_logs_fixture["controlBridgeStatusDecodedWaterPump"] is True
    assert latest_logs_fixture["controlBridgeStatusDecodedGasBoiler"] is False
    assert latest_logs_fixture["errorDecodedDtcNone"] is True


def test_energy_total_metrics(energy_total_fixture: dict[str, Any]) -> None:
    """Verify energy_total.json cumulative energy registers."""
    assert energy_total_fixture["heatPumpId"] == TEST_HP_UUID_BLACKBIRD
    assert energy_total_fixture["totalEInHeating"] == 1245.8
    assert energy_total_fixture["totalEOutHeating"] == 5230.6
    assert energy_total_fixture["totalEInDhw"] == 380.5
    assert energy_total_fixture["totalEOutDhw"] == 1150.2


def test_installation_settings_content(
    installation_settings_fixture: dict[str, Any],
) -> None:
    """Verify installation_settings_get.json contains both quiet schedule and live camelCase portal keys."""
    # Silent hours high-level structure
    assert "silentHours" in installation_settings_fixture
    silent = installation_settings_fixture["silentHours"]
    assert silent["quietLevel"] == 2
    assert silent["nightLimitEnabled"] is True
    assert silent["nightLimitStartTime"] == "22:00"
    assert silent["nightLimitEndTime"] == "07:00"
    assert silent["nightLimitPowerPercentage"] == 50

    # Live portal settings dictionary (authentic camelCase)
    assert "settings" in installation_settings_fixture
    portal_settings = installation_settings_fixture["settings"]
    assert portal_settings["performanceModePreset"] == 0
    assert portal_settings["silentHoursScheduledPreset"] == 2
    assert portal_settings["silentHoursMondayStart"] == 19
    assert portal_settings["silentMinutesMondayStart"] == 0
    assert portal_settings["silentHoursMondayStop"] == 7
    assert portal_settings["silentMinutesMondayStop"] == 0
    assert portal_settings["dhwTargetTemperature"] == 50

    # DHW settings
    assert "dhw" in installation_settings_fixture
    assert installation_settings_fixture["dhw"]["targetTemperature"] == 55.0


def test_installation_settings_pascal_content(
    installation_settings_pascal_fixture: dict[str, Any],
) -> None:
    """Verify installation_settings_pascal_get.json contains legacy PascalCase portal keys."""
    # Silent hours high-level structure
    assert "silentHours" in installation_settings_pascal_fixture
    silent = installation_settings_pascal_fixture["silentHours"]
    assert silent["quietLevel"] == 2
    assert silent["nightLimitEnabled"] is True

    # Legacy PascalCase settings dictionary
    assert "settings" in installation_settings_pascal_fixture
    portal_settings = installation_settings_pascal_fixture["settings"]
    assert portal_settings["PerformanceModePreset"] == 0
    assert portal_settings["SilentHoursScheduledPreset"] == 2
    assert portal_settings["SilentHoursMondayStart"] == 19
    assert portal_settings["SilentMinutesMondayStart"] == 0
    assert portal_settings["SilentHoursMondayStop"] == 7
    assert portal_settings["SilentMinutesMondayStop"] == 0
    assert portal_settings["DhwTargetTemperature"] == 50

    # DHW settings
    assert "dhw" in installation_settings_pascal_fixture
    assert installation_settings_pascal_fixture["dhw"]["targetTemperature"] == 55.0


def test_dual_casing_parity(
    installation_settings_fixture: dict[str, Any],
    installation_settings_pascal_fixture: dict[str, Any],
) -> None:
    """Verify ControlBoardSettings.from_api_dict parses both camelCase and PascalCase fixtures identically."""
    s_camel = ControlBoardSettings.from_api_dict(installation_settings_fixture)
    s_pascal = ControlBoardSettings.from_api_dict(installation_settings_pascal_fixture)

    assert s_camel.performance_mode_preset == s_pascal.performance_mode_preset
    assert s_camel.silent_hours_preset == s_pascal.silent_hours_preset
    assert s_camel.dhw_target_temperature == s_pascal.dhw_target_temperature
    assert s_camel.schedules == s_pascal.schedules
    for day in DAYS_OF_WEEK:
        assert s_camel.schedules[day] == s_pascal.schedules[day]


def test_settings_update_content(settings_update_fixture: dict[str, Any]) -> None:
    """Verify settings_update_success.json structure."""
    assert settings_update_fixture["success"] is True
    assert settings_update_fixture["status"] == 0
    assert "requestId" in settings_update_fixture
    assert "settingsRequestId" in settings_update_fixture


def test_boost_trigger_content(boost_trigger_fixture: dict[str, Any]) -> None:
    """Verify boost_trigger_success.json structure."""
    assert boost_trigger_fixture["success"] is True
    assert boost_trigger_fixture["boostActive"] is True
    assert boost_trigger_fixture["durationMinutes"] == 60
    assert boost_trigger_fixture["targetTemperature"] == 60.0


async def test_mock_api_offline_routing(
    setup_mock_api: Any,
    client_session: aiohttp.ClientSession,
) -> None:
    """Verify mock API harness routes requests completely offline without network."""
    # 1. User profile GET
    async with client_session.get(f"{API_BASE_URL}/api/v1/users/me") as resp:
        assert resp.status == 200
        user_data = await resp.json()
        assert user_data["firstName"] == "Jean"

    # 2. Latest logs GET
    async with client_session.get(
        f"{API_BASE_URL}/api/v1/heat-pumps/{TEST_HP_UUID_BLACKBIRD}/logs/latest"
    ) as resp:
        assert resp.status == 200
        logs = await resp.json()
        assert logs["rpm"] == 2850

    # 3. Installation settings GET
    async with client_session.get(
        f"{PORTAL_BASE_URL}/api/v1/installations/{TEST_INSTALLATION_ID}/settings"
    ) as resp:
        assert resp.status == 200
        settings = await resp.json()
        assert settings["silentHours"]["quietLevel"] == 2

    # 4. Control board settings PATCH
    patch_payload = {
        "settings": {
            "PerformanceModePreset": 1,
            "SilentHoursScheduledPreset": 2,
        },
        "updateImmediately": False,
    }
    async with client_session.patch(
        f"{PORTAL_BASE_URL}/api/v1/control-boards/{TEST_CB_ID_BLACKBIRD}/settings",
        json=patch_payload,
    ) as resp:
        assert resp.status == 200
        result = await resp.json()
        assert result["success"] is True
        assert result["settingsRequestId"] == TEST_SETTINGS_REQUEST_ID

    # 5. Polling control board setting request GET
    async with client_session.get(
        f"{PORTAL_BASE_URL}/api/v1/control-boards/{TEST_CB_ID_BLACKBIRD}/settings/{TEST_SETTINGS_REQUEST_ID}"
    ) as resp:
        assert resp.status == 200
        poll_res = await resp.json()
        assert poll_res["status"] == 2

    # 6. DHW boost POST
    async with client_session.post(
        f"{PORTAL_BASE_URL}/api/v1/heat-pumps/{TEST_HP_UUID_BLACKBIRD}/dhw/boost",
        json={"durationMinutes": 60, "targetTemperature": 60.0},
    ) as resp:
        assert resp.status == 200
        boost_res = await resp.json()
        assert boost_res["boostActive"] is True
