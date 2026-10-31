"""Tier 5 Adversarial and Boundary Stress Test Suite (Phase 1).

Covers all remaining edge cases, defensive branches, fallback routines,
unhandled error boundaries, and race condition mitigations across:
- custom_components/weheat/client.py
- custom_components/weheat/models.py
- custom_components/weheat/coordinator.py
- custom_components/weheat/select.py
- custom_components/weheat/switch.py
- custom_components/weheat/time.py
- custom_components/weheat/services.py

100% offline, zero network dependencies, 100% deterministic.
"""

from __future__ import annotations

from datetime import time
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest
from aiohttp import ClientResponseError, RequestInfo
from aioresponses import aioresponses
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed
from yarl import URL

from custom_components.weheat.client import (
    WeheatApiError,
    WeheatAuthError,
    WeheatClient,
)
from custom_components.weheat.coordinator import (
    WeheatData,
    WeheatDataUpdateCoordinator,
    WeheatEnergyUpdateCoordinator,
    WeheatSettingsUpdateCoordinator,
)
from custom_components.weheat.models import (
    CompressorPercentage,
    ControlBoardSettings,
    DailySilentSchedule,
    HeatPumpEnergy,
    HeatPumpInfo,
    HeatPumpLog,
    HeatPumpState,
    SettingsRequestStatus,
)
from custom_components.weheat.select import (
    SELECT_ENTITIES,
    WeheatPerformanceModeSelect,
    WeheatSilentHoursPresetSelect,
)
from custom_components.weheat.services import (
    _resolve_target_data,
    async_handle_set_quiet_mode,
    async_handle_set_silent_schedule,
)
from custom_components.weheat.switch import (
    SWITCH_DESCRIPTIONS,
    WeheatDhwBoostSwitch,
    WeheatLegionellaPreventionSwitch,
    WeheatSilentHoursScheduleSwitch,
)
from custom_components.weheat.time import (
    TIME_ENTITIES,
    WeheatTimeEntity,
)
from tests.conftest import (
    API_BASE_URL,
    PORTAL_BASE_URL,
    load_fixture,
)

# ===========================================================================
# 1. Client Adversarial Branches & Error Handling
# ===========================================================================


class TestClientAdversarialBranches:
    """Stress tests for corner cases in WeheatClient HTTP & API handling."""

    @pytest.mark.asyncio
    async def test_request_http_204_no_content(
        self, client_session: aiohttp.ClientSession, mock_aioresponse: aioresponses
    ) -> None:
        """HTTP 204 No Content response should safely return empty dict."""
        client = WeheatClient(
            session=client_session,
            base_url=API_BASE_URL,
            portal_base_url=PORTAL_BASE_URL,
        )
        url = f"{API_BASE_URL}/api/v1/test_no_content"
        mock_aioresponse.get(url, status=204)

        result = await client._request("GET", url)
        assert result == {}

    @pytest.mark.asyncio
    async def test_get_user_id_missing_id_field(
        self, client_session: aiohttp.ClientSession, mock_aioresponse: aioresponses
    ) -> None:
        """Endpoint /users/me returning valid dict but missing 'id' raises WeheatApiError."""
        client = WeheatClient(
            session=client_session,
            base_url=API_BASE_URL,
            portal_base_url=PORTAL_BASE_URL,
        )
        mock_aioresponse.get(
            f"{API_BASE_URL}/api/v1/users/me",
            status=200,
            payload={"email": "user@example.com"},  # no 'id'
        )

        with pytest.raises(
            WeheatApiError, match="Missing 'id' field in /users/me response"
        ):
            await client.async_get_user_id()

    @pytest.mark.asyncio
    async def test_get_user_profile_non_dict_fallback(
        self, client_session: aiohttp.ClientSession, mock_aioresponse: aioresponses
    ) -> None:
        """Endpoint /users/me returning a non-dict safely returns empty dict."""
        client = WeheatClient(
            session=client_session,
            base_url=API_BASE_URL,
            portal_base_url=PORTAL_BASE_URL,
        )
        mock_aioresponse.get(
            f"{API_BASE_URL}/api/v1/users/me",
            status=200,
            payload=["unexpected", "list"],
        )

        profile = await client.async_get_user_profile()
        assert profile == {}

    @pytest.mark.asyncio
    async def test_get_heat_pumps_bare_list_and_empty_formats(
        self, client_session: aiohttp.ClientSession, mock_aioresponse: aioresponses
    ) -> None:
        """Endpoint /heat_pumps returning bare list or unexpected format."""
        client = WeheatClient(
            session=client_session,
            base_url=API_BASE_URL,
            portal_base_url=PORTAL_BASE_URL,
        )

        # 1. Bare list
        mock_aioresponse.get(
            f"{API_BASE_URL}/api/v1/heat-pumps?limit=1000&page=1&state=3",
            status=200,
            payload=[
                {
                    "id": "hp-1",
                    "name": "HP 1",
                    "model": 0,
                    "serialNumber": "SN1",
                    "controlBoardId": "cb-1",
                    "installationId": "inst-1",
                }
            ],
        )
        pumps = await client.async_get_heat_pumps()
        assert len(pumps) == 1
        assert pumps[0].heatpump_id == "hp-1"
        assert client._control_board_cache.get("hp-1") == "cb-1"
        assert client._control_board_cache.get("inst-1") == "cb-1"

        # 2. Unexpected non-list/non-data dict
        mock_aioresponse.get(
            f"{API_BASE_URL}/api/v1/heat-pumps?limit=1000&page=1&state=3",
            status=200,
            payload="not a dict or list",
        )
        pumps2 = await client.async_get_heat_pumps()
        assert pumps2 == []

    @pytest.mark.asyncio
    async def test_get_heat_pump_details_non_dict(
        self, client_session: aiohttp.ClientSession, mock_aioresponse: aioresponses
    ) -> None:
        """Endpoint /heat-pumps/{uuid} returning non-dict returns empty dict."""
        client = WeheatClient(
            session=client_session,
            base_url=API_BASE_URL,
            portal_base_url=PORTAL_BASE_URL,
        )
        mock_aioresponse.get(
            f"{API_BASE_URL}/api/v1/heat-pumps/test-hp",
            status=200,
            payload=None,
        )
        details = await client.async_get_heat_pump_details("test-hp")
        assert details == {}

    @pytest.mark.asyncio
    async def test_get_installation_topology_bare_list_and_filtering(
        self, client_session: aiohttp.ClientSession, mock_aioresponse: aioresponses
    ) -> None:
        """Endpoint /installations/fully-expanded returning bare list with missing controlBoardId items."""
        client = WeheatClient(
            session=client_session,
            base_url=API_BASE_URL,
            portal_base_url=PORTAL_BASE_URL,
        )
        mock_aioresponse.get(
            f"{PORTAL_BASE_URL}/api/v1/installations/fully-expanded",
            status=200,
            payload=[
                {"id": "hp-1", "installationId": "inst-1"},  # missing controlBoardId
                {
                    "heatPumpId": "hp-2",
                    "installationId": "inst-2",
                    "controlBoardId": "cb-2",
                },
            ],
        )

        topology = await client.async_get_installation_topology()
        assert len(topology) == 2
        assert "hp-1" not in client._control_board_cache
        assert client._control_board_cache.get("hp-2") == "cb-2"
        assert client._control_board_cache.get("inst-2") == "cb-2"

    @pytest.mark.asyncio
    async def test_get_latest_logs_unexpected_format(
        self, client_session: aiohttp.ClientSession, mock_aioresponse: aioresponses
    ) -> None:
        """Endpoint /logs/latest returning non-dict raises WeheatApiError."""
        client = WeheatClient(
            session=client_session,
            base_url=API_BASE_URL,
            portal_base_url=PORTAL_BASE_URL,
        )
        mock_aioresponse.get(
            f"{API_BASE_URL}/api/v1/heat-pumps/test-hp/logs/latest",
            status=200,
            payload=["not", "a", "dict"],
        )
        with pytest.raises(
            WeheatApiError, match="Unexpected response format for latest logs"
        ):
            await client.async_get_latest_logs("test-hp")

    @pytest.mark.asyncio
    async def test_get_energy_total_404_fallback_paths(
        self, client_session: aiohttp.ClientSession, mock_aioresponse: aioresponses
    ) -> None:
        """Endpoint /energy-logs/{uuid}/total returning 404 tests fallback path."""
        client = WeheatClient(
            session=client_session,
            base_url=API_BASE_URL,
            portal_base_url=PORTAL_BASE_URL,
        )

        # Path 1: 404 on primary, fallback succeeds
        mock_aioresponse.get(
            f"{API_BASE_URL}/api/v1/energy-logs/test-hp/total",
            status=404,
            payload={"message": "Not found"},
        )
        mock_aioresponse.get(
            f"{API_BASE_URL}/total/test-hp",
            status=200,
            payload=load_fixture("energy_total.json"),
        )
        res = await client.async_get_energy_total("test-hp")
        assert isinstance(res, HeatPumpEnergy)

        # Path 2: 404 on primary, fallback returns non-dict -> re-raises original 404
        mock_aioresponse.get(
            f"{API_BASE_URL}/api/v1/energy-logs/test-hp2/total",
            status=404,
            payload={"message": "Not found"},
        )
        mock_aioresponse.get(
            f"{API_BASE_URL}/total/test-hp2",
            status=200,
            payload=["not", "dict"],
        )
        with pytest.raises(WeheatApiError, match="Not found"):
            await client.async_get_energy_total("test-hp2")

        # Path 3: Primary returns 200 but non-dict
        mock_aioresponse.get(
            f"{API_BASE_URL}/api/v1/energy-logs/test-hp3/total",
            status=200,
            payload="scalar_value",
        )
        with pytest.raises(
            WeheatApiError, match="Unexpected response format for energy total"
        ):
            await client.async_get_energy_total("test-hp3")

    @pytest.mark.asyncio
    async def test_get_installation_settings_non_dict(
        self, client_session: aiohttp.ClientSession, mock_aioresponse: aioresponses
    ) -> None:
        """Endpoint /installations/{id}/settings returning non-dict raises WeheatApiError."""
        client = WeheatClient(
            session=client_session,
            base_url=API_BASE_URL,
            portal_base_url=PORTAL_BASE_URL,
        )
        mock_aioresponse.get(
            f"{PORTAL_BASE_URL}/api/v1/installations/inst-1/settings",
            status=200,
            payload=None,
        )
        with pytest.raises(
            WeheatApiError, match="Unexpected response format for installation settings"
        ):
            await client.async_get_installation_settings("inst-1")

    @pytest.mark.asyncio
    async def test_patch_control_board_settings_validation_and_missing_id(
        self, client_session: aiohttp.ClientSession, mock_aioresponse: aioresponses
    ) -> None:
        """Verify empty settings dict raises ValueError and missing request ID raises WeheatApiError."""
        client = WeheatClient(
            session=client_session,
            base_url=API_BASE_URL,
            portal_base_url=PORTAL_BASE_URL,
        )

        # Empty settings dict
        with pytest.raises(ValueError, match="settings dictionary must not be empty"):
            await client.async_patch_control_board_settings(
                control_board_id="cb-1",
                settings={},
            )

        # Missing request ID in response
        mock_aioresponse.patch(
            f"{PORTAL_BASE_URL}/api/v1/control-boards/cb-1/settings",
            status=200,
            payload={"status": 0},  # no settingsRequestId or requestId
        )
        with pytest.raises(
            WeheatApiError, match="No settingsRequestId or requestId in response"
        ):
            await client.async_patch_control_board_settings(
                control_board_id="cb-1",
                settings={"PerformanceModePreset": 1},
            )

    @pytest.mark.asyncio
    async def test_set_silent_hours_schedule_invalid_minute_boundary(
        self, client_session: aiohttp.ClientSession
    ) -> None:
        """Verify async_set_silent_hours_schedule rejects minutes outside 0..59."""
        client = WeheatClient(session=client_session)

        with pytest.raises(ValueError, match="Invalid minute"):
            await client.async_set_silent_hours_schedule(
                control_board_id="cb-1",
                preset=1,
                schedule={"SilentMinutesMondayStart": 60},
            )

        with pytest.raises(ValueError, match="Invalid minute"):
            await client.async_set_silent_hours_schedule(
                control_board_id="cb-1",
                preset=1,
                schedule={"SilentMinutesMondayStart": -1},
            )

    @pytest.mark.asyncio
    async def test_get_settings_request_status_empty_parameters(
        self, client_session: aiohttp.ClientSession
    ) -> None:
        """Verify empty control_board_id or request_id raises ValueError."""
        client = WeheatClient(session=client_session)

        with pytest.raises(
            ValueError, match="control_board_id and request_id must not be empty"
        ):
            await client.async_get_settings_request_status(
                control_board_id="", request_id="req-1"
            )

        with pytest.raises(
            ValueError, match="control_board_id and request_id must not be empty"
        ):
            await client.async_get_settings_request_status(
                control_board_id="cb-1", request_id=""
            )

    @pytest.mark.asyncio
    async def test_poll_settings_request_status_5_succeeded_with_mismatch(
        self, client_session: aiohttp.ClientSession, mock_aioresponse: aioresponses
    ) -> None:
        """Status 5 (SUCCEEDED_WITH_HEAT_PUMP_SETTINGS_MISMATCH) resolves successfully."""
        client = WeheatClient(
            session=client_session,
            base_url=API_BASE_URL,
            portal_base_url=PORTAL_BASE_URL,
        )
        url = f"{PORTAL_BASE_URL}/api/v1/control-boards/cb-1/settings/req-1"
        mock_aioresponse.get(
            url,
            status=200,
            payload={"status": 5, "settingsRequestId": "req-1"},
        )

        res = await client.async_poll_settings_request(
            control_board_id="cb-1", request_id="req-1", poll_interval=0.01
        )
        assert res["status"] == 5

    @pytest.mark.asyncio
    async def test_patch_control_board_variables_validation(
        self, client_session: aiohttp.ClientSession
    ) -> None:
        """Verify control_board_id and variables validation in patch_variables."""
        client = WeheatClient(session=client_session)

        with pytest.raises(ValueError, match="control_board_id must not be empty"):
            await client.async_patch_control_board_variables(
                control_board_id=" ", variables={"k": 1}
            )

        with pytest.raises(ValueError, match="variables dictionary must not be empty"):
            await client.async_patch_control_board_variables(
                control_board_id="cb-1", variables={}
            )

    @pytest.mark.asyncio
    async def test_skip_legionella_prevention(
        self, client_session: aiohttp.ClientSession, mock_aioresponse: aioresponses
    ) -> None:
        """Verify async_skip_legionella_prevention sends Hpc_skip_legionella_prevention: 1."""
        client = WeheatClient(
            session=client_session,
            base_url=API_BASE_URL,
            portal_base_url=PORTAL_BASE_URL,
        )
        url = f"{PORTAL_BASE_URL}/api/v1/control-boards/cb-1/variables"
        mock_aioresponse.patch(
            url,
            status=200,
            payload={"success": True},
        )

        success = await client.async_skip_legionella_prevention("cb-1")
        assert success is True

    @pytest.mark.asyncio
    async def test_trigger_dhw_boost_empty_target(
        self, client_session: aiohttp.ClientSession
    ) -> None:
        """Verify async_trigger_dhw_boost raises ValueError when target is empty."""
        client = WeheatClient(session=client_session)

        with pytest.raises(
            ValueError, match="heat_pump_uuid or heat_pump_id must be provided"
        ):
            await client.async_trigger_dhw_boost(heat_pump_uuid="  ")


# ===========================================================================
# 2. Models Adversarial Branches & Enums
# ===========================================================================


class TestModelsAdversarialBranches:
    """Stress tests for data models, enum mappings, and properties."""

    @pytest.mark.parametrize(
        ("state_int", "expected_enum"),
        [
            (1, HeatPumpState.OFFLINE),
            (136, HeatPumpState.WATER_CHECK),
            (130, HeatPumpState.COOLING),
            (131, HeatPumpState.COOLING),
            (137, HeatPumpState.COOLING),
            (170, HeatPumpState.SELF_TEST),
            (180, HeatPumpState.MANUAL_CONTROL),
            (1010, HeatPumpState.UPDATING),
            (84, HeatPumpState.DEFROSTING),
            (90, HeatPumpState.DEFROSTING),
            (100, HeatPumpState.DEFROSTING),
            (120, HeatPumpState.DEFROSTING),
            (205, HeatPumpState.DEFROSTING),
            (239, HeatPumpState.DEFROSTING),
            (9999, None),
            (None, None),
        ],
    )
    def test_heat_pump_state_from_api_state(
        self, state_int: int | None, expected_enum: HeatPumpState | None
    ) -> None:
        """Verify HeatPumpState decoding for all uncatalogued or special state integers."""
        assert HeatPumpState.from_api_state(state_int) == expected_enum

    @pytest.mark.parametrize(
        ("status_enum", "expected_resolved", "expected_successful"),
        [
            (SettingsRequestStatus.PENDING, False, False),
            (SettingsRequestStatus.EXECUTING, False, False),
            (SettingsRequestStatus.SUCCEEDED, True, True),
            (
                SettingsRequestStatus.SUCCEEDED_WITH_HEAT_PUMP_SETTINGS_MISMATCH,
                True,
                True,
            ),
            (SettingsRequestStatus.CANCELLED, True, False),
            (SettingsRequestStatus.FAILED, True, False),
            (SettingsRequestStatus.FAILED_DEVICE_WRONG_STATE, True, False),
            (SettingsRequestStatus.FAILED_DEVICE_DISCONNECTED, True, False),
        ],
    )
    def test_settings_request_status_properties(
        self,
        status_enum: SettingsRequestStatus,
        expected_resolved: bool,
        expected_successful: bool,
    ) -> None:
        """Verify is_resolved and is_successful boolean properties for SettingsRequestStatus."""
        assert status_enum.is_resolved == expected_resolved
        assert status_enum.is_successful == expected_successful

    def test_heat_pump_info_model_properties_and_aliases(self) -> None:
        """Verify model name, nominal RPM, and property aliases for various heat pump models."""
        # Model 1: Blackbird P60
        hp1 = HeatPumpInfo(
            heatpump_id="id-1",
            name="Unit 1",
            model=1,
            serial_number="SN-1",
        )
        assert hp1.uuid == "id-1"
        assert hp1.device_name == "Unit 1"
        assert hp1.sn == "SN-1"
        assert hp1.model_name == "Blackbird P60 heat pump"
        assert hp1.nominal_rpm == 5520

        # Model 3: Sparrow P60
        hp3 = HeatPumpInfo(
            heatpump_id="id-3",
            name="Unit 3",
            model=3,
            serial_number="SN-3",
        )
        assert hp3.model_name == "Sparrow P60 heat pump"
        assert hp3.nominal_rpm == 5520

        # Model 5: Flint P40
        hp5 = HeatPumpInfo(
            heatpump_id="id-5",
            name="Unit 5",
            model=5,
            serial_number="SN-5",
        )
        assert hp5.model_name == "Flint P40 heat pump"
        assert hp5.nominal_rpm == 5400

        # Unknown Model 99
        hp99 = HeatPumpInfo(
            heatpump_id="id-99",
            name="Unit 99",
            model=99,
            serial_number="SN-99",
        )
        assert hp99.model_name == "Weheat heat pump (Model 99)"
        assert hp99.nominal_rpm == 4500

    def test_compressor_percentage_callable_edge_cases(self) -> None:
        """Verify CompressorPercentage callable returns None for invalid target or rpm."""
        comp = CompressorPercentage(50, rpm=2250, nominal_rpm=4500)
        assert comp(nominal_rpm=0) is None
        assert comp(nominal_rpm=-100) is None

        comp_none_rpm = CompressorPercentage(0, rpm=None, nominal_rpm=4500)
        assert comp_none_rpm() is None

    def test_heat_pump_log_property_mappings(self) -> None:
        """Verify secondary properties of HeatPumpLog."""
        log = HeatPumpLog(
            heat_pump_id="hp-1",
            state=HeatPumpState.HEATING,
            t_water_house_in=35.5,
            t_air_in=10.2,
        )
        assert log.heat_pump_state == HeatPumpState.HEATING
        assert log.water_house_in_temperature == 35.5
        assert log.air_inlet_temperature == 10.2

    def test_daily_silent_schedule_time_strings(self) -> None:
        """Verify start_time_str and stop_time_str formatting with padding."""
        sched = DailySilentSchedule(
            day="Monday",
            start_hour=7,
            start_minute=5,
            stop_hour=23,
            stop_minute=59,
        )
        assert sched.start_time_str == "07:05"
        assert sched.stop_time_str == "23:59"

    def test_control_board_settings_to_patch_payload(self) -> None:
        """Verify ControlBoardSettings.to_patch_payload() generates complete 28-key schedule."""
        raw = load_fixture("installation_settings_get.json")
        settings = ControlBoardSettings.from_api_dict(raw)
        payload = settings.to_patch_payload()

        assert payload["updateImmediately"] is False
        assert "settings" in payload
        s = payload["settings"]
        assert s["PerformanceModePreset"] == settings.performance_mode_preset
        assert s["SilentHoursScheduledPreset"] == settings.silent_hours_preset
        assert s["DhwTargetTemperature"] == settings.dhw_target_temperature

        # Verify all 7 days present
        for day in [
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        ]:
            assert f"SilentHours{day}Start" in s
            assert f"SilentMinutes{day}Start" in s
            assert f"SilentHours{day}Stop" in s
            assert f"SilentMinutes{day}Stop" in s


# ===========================================================================
# 3. Coordinator Adversarial Branches & Fallbacks
# ===========================================================================


class TestCoordinatorAdversarialBranches:
    """Stress tests for coordinator token handling and topology fallbacks."""

    @pytest.mark.asyncio
    async def test_data_coordinator_token_extraction_and_failure(self) -> None:
        """Verify WeheatDataUpdateCoordinator extracts token or raises on unexpected error."""
        hass = MagicMock()
        entry = MagicMock()
        session = MagicMock()
        session.async_ensure_token_valid = AsyncMock()
        session.token = {CONF_ACCESS_TOKEN: "valid_token"}

        client = MagicMock()
        client.async_get_latest_logs = AsyncMock(
            return_value=HeatPumpLog(heat_pump_id="hp-1")
        )

        hp_info = HeatPumpInfo(
            heatpump_id="hp-1", name="HP", model=0, serial_number="SN"
        )
        coord = WeheatDataUpdateCoordinator(
            hass=hass,
            config_entry=entry,
            session=session,
            heat_pump_info=hp_info,
            client=client,
        )

        res = await coord._async_update_data()
        assert res.heat_pump_id == "hp-1"
        client.async_get_latest_logs.assert_awaited_with("hp-1", token="valid_token")

        # Generic token refresh exception raises UpdateFailed
        session.async_ensure_token_valid.side_effect = RuntimeError("Broken session")
        with pytest.raises(UpdateFailed, match="Failed to ensure valid OAuth token"):
            await coord._async_update_data()

    @pytest.mark.asyncio
    async def test_energy_coordinator_token_handling(self) -> None:
        """Verify WeheatEnergyUpdateCoordinator token error boundaries."""
        hass = MagicMock()
        entry = MagicMock()
        session = MagicMock()
        hp_info = HeatPumpInfo(
            heatpump_id="hp-1", name="HP", model=0, serial_number="SN"
        )
        client = MagicMock()

        coord = WeheatEnergyUpdateCoordinator(
            hass=hass,
            config_entry=entry,
            session=session,
            heat_pump_info=hp_info,
            client=client,
        )

        # 1. Successful token retrieval
        session.async_ensure_token_valid = AsyncMock()
        session.token = {CONF_ACCESS_TOKEN: "energy_token"}
        client.async_get_energy_total = AsyncMock(
            return_value=HeatPumpEnergy(heatpump_id="hp-1")
        )
        await coord._async_update_data()
        client.async_get_energy_total.assert_awaited_with("hp-1", token="energy_token")

        # 2. ClientResponseError 401 raises ConfigEntryAuthFailed
        req_info = RequestInfo(
            url=URL("https://example.com"),
            method="POST",
            headers={},
            real_url=URL("https://example.com"),
        )
        session.async_ensure_token_valid.side_effect = ClientResponseError(
            request_info=req_info, history=(), status=401, message="Unauthorized"
        )
        with pytest.raises(ConfigEntryAuthFailed):
            await coord._async_update_data()

        # 3. ClientResponseError 500 raises UpdateFailed
        session.async_ensure_token_valid.side_effect = ClientResponseError(
            request_info=req_info, history=(), status=500, message="Server Error"
        )
        with pytest.raises(UpdateFailed, match="Error refreshing OAuth token"):
            await coord._async_update_data()

        # 4. Generic exception raises UpdateFailed
        session.async_ensure_token_valid.side_effect = Exception("Unknown boom")
        with pytest.raises(UpdateFailed, match="Failed to ensure valid OAuth token"):
            await coord._async_update_data()

    @pytest.mark.asyncio
    async def test_settings_coordinator_installation_id_topology_discovery(
        self,
    ) -> None:
        """Verify WeheatSettingsUpdateCoordinator resolves missing installation_id via topology."""
        hass = MagicMock()
        entry = MagicMock()
        hp_info = HeatPumpInfo(
            heatpump_id="hp-target",
            name="HP",
            model=0,
            serial_number="SN",
            installation_id=None,  # Missing!
        )
        client = MagicMock()
        client.async_get_installation_topology = AsyncMock(
            return_value=[
                {"heatPumpId": "hp-target", "installationId": "inst-resolved-99"}
            ]
        )
        client.async_get_installation_settings = AsyncMock(
            return_value=ControlBoardSettings()
        )

        coord = WeheatSettingsUpdateCoordinator(
            hass=hass,
            config_entry=entry,
            session=None,
            heat_pump_info=hp_info,
            client=client,
        )

        # 1. Fallback successfully resolves installationId
        await coord._async_update_data()
        assert hp_info.installation_id == "inst-resolved-99"
        client.async_get_installation_settings.assert_awaited_with(
            "inst-resolved-99", token=None
        )

        # 2. Topology fails with WeheatApiError -> logs warning, raises UpdateFailed
        hp_info.installation_id = None
        client.async_get_installation_topology.side_effect = WeheatApiError(
            "Topology down"
        )
        with pytest.raises(UpdateFailed, match="Missing installation ID for heat pump"):
            await coord._async_update_data()


# ===========================================================================
# 4. Select Platform Adversarial Branches
# ===========================================================================


class TestSelectAdversarialBranches:
    """Stress tests for Weheat select entities."""

    @pytest.mark.asyncio
    async def test_select_performance_mode_control_board_resolution_and_client_error(
        self,
    ) -> None:
        """Verify resolution of control_board_id and error when client is missing."""
        hp_info = HeatPumpInfo(
            heatpump_id="hp-1",
            name="HP",
            model=0,
            serial_number="SN",
            control_board_id=None,
        )
        client = MagicMock()
        client.async_get_control_board_id = AsyncMock(return_value="cb-resolved")
        client.async_set_performance_mode = AsyncMock(return_value="req-1")
        client.async_poll_settings_request = AsyncMock()

        coord = MagicMock()
        coord.data = ControlBoardSettings()
        coord.async_request_refresh = AsyncMock()

        entity = WeheatPerformanceModeSelect(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=SELECT_ENTITIES[0],
            client=client,
        )

        # Resolves via client
        await entity.async_select_option("Silent 1")
        client.async_get_control_board_id.assert_awaited_with("hp-1", token=None)

        # Client missing raises HomeAssistantError
        entity_no_client = WeheatPerformanceModeSelect(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=SELECT_ENTITIES[0],
            client=None,
        )
        with pytest.raises(HomeAssistantError, match="Weheat client is not available"):
            await entity_no_client.async_select_option("Silent 1")

        with pytest.raises(
            HomeAssistantError, match="No client available to resolve control board ID"
        ):
            await entity_no_client._async_get_control_board_id()

    def test_select_current_option_dict_and_none_fallbacks(self) -> None:
        """Verify current_option handles None data, dict data, and invalid preset values."""
        hp_info = HeatPumpInfo(
            heatpump_id="hp-1", name="HP", model=0, serial_number="SN"
        )
        coord = MagicMock()

        entity_perf = WeheatPerformanceModeSelect(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=SELECT_ENTITIES[0],
        )
        entity_silent = WeheatSilentHoursPresetSelect(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=SELECT_ENTITIES[1],
        )

        # 1. Data is None
        coord.data = None
        assert entity_perf.current_option is None
        assert entity_silent.current_option is None

        # 2. Data is dict with PerformanceModePreset
        coord.data = {"PerformanceModePreset": 2, "SilentHoursScheduledPreset": 3}
        assert entity_perf.current_option == "Mode 3 - Reduced"
        assert entity_silent.current_option == "Silent 3"

        # 3. Data is dict with lower-case keys
        coord.data = {
            "performance_mode_preset": 0,
            "silent_hours_preset": 0,
        }
        assert entity_perf.current_option == "Mode 5 - Max capacity"
        assert entity_silent.current_option == "Off"

        # 4. Out of range preset returns None
        coord.data = {"PerformanceModePreset": 99, "SilentHoursScheduledPreset": 99}
        assert entity_perf.current_option is None
        assert entity_silent.current_option is None

    @pytest.mark.asyncio
    async def test_select_unexpected_exception_wrapped_in_home_assistant_error(
        self,
    ) -> None:
        """Verify unexpected runtime exceptions in async_select_option are wrapped in HomeAssistantError."""
        hp_info = HeatPumpInfo(
            heatpump_id="hp-1",
            name="HP",
            model=0,
            serial_number="SN",
            control_board_id="cb-1",
        )
        client = MagicMock()
        client.async_set_performance_mode.side_effect = RuntimeError(
            "Unexpected internal crash"
        )
        client.async_set_silent_hours_preset.side_effect = RuntimeError(
            "Unexpected internal crash"
        )

        coord = MagicMock()
        coord.data = ControlBoardSettings()

        entity_perf = WeheatPerformanceModeSelect(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=SELECT_ENTITIES[0],
            client=client,
        )
        with pytest.raises(
            HomeAssistantError, match="Unexpected error setting performance mode"
        ):
            await entity_perf.async_select_option("Silent 1")

        entity_silent = WeheatSilentHoursPresetSelect(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=SELECT_ENTITIES[1],
            client=client,
        )
        with pytest.raises(
            HomeAssistantError, match="Unexpected error setting silent hours preset"
        ):
            await entity_silent.async_select_option("Silent 1")


# ===========================================================================
# 5. Switch Platform Adversarial Branches
# ===========================================================================


class TestSwitchAdversarialBranches:
    """Stress tests for Weheat switch entities."""

    @pytest.mark.asyncio
    async def test_silent_hours_schedule_switch_branches(self) -> None:
        """Verify WeheatSilentHoursScheduleSwitch edge cases and error boundaries."""
        hp_info = HeatPumpInfo(
            heatpump_id="hp-1",
            name="HP",
            model=0,
            serial_number="SN",
            control_board_id=None,
        )
        coord = MagicMock()
        coord.async_request_refresh = AsyncMock()
        client = MagicMock()
        client.async_get_control_board_id = AsyncMock(return_value="cb-1")
        client.async_set_silent_hours_preset = AsyncMock(return_value="req-1")
        client.async_poll_settings_request = AsyncMock()

        switch = WeheatSilentHoursScheduleSwitch(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=SWITCH_DESCRIPTIONS[0],
            client=client,
        )

        # 1. is_on with None data
        coord.data = None
        assert switch.is_on is None

        # 2. is_on with dict data
        coord.data = {"SilentHoursScheduledPreset": 2}
        assert switch.is_on is True
        coord.data = {"SilentHoursScheduledPreset": 0}
        assert switch.is_on is False
        coord.data = {"SilentHoursScheduledPreset": 5}  # OFF_ALT
        assert switch.is_on is False

        # 3. async_turn_off caches last active preset when in (1..4)
        settings = ControlBoardSettings()
        settings.silent_hours_preset = 3
        coord.data = settings
        await switch.async_turn_off()
        assert switch._last_active_preset == 3

        # 4. async_turn_on restores last active preset (3)
        await switch.async_turn_on()
        client.async_set_silent_hours_preset.assert_awaited_with(
            control_board_id="cb-1",
            token=None,
            preset=3,
        )

        # 5. Missing client raises HomeAssistantError
        switch_no_client = WeheatSilentHoursScheduleSwitch(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=SWITCH_DESCRIPTIONS[0],
            client=None,
        )
        with pytest.raises(HomeAssistantError):
            await switch_no_client.async_turn_on()
        with pytest.raises(HomeAssistantError):
            await switch_no_client.async_turn_off()
        with pytest.raises(HomeAssistantError):
            await switch_no_client._async_get_control_board_id()

        # 6. Unexpected errors wrapped
        client.async_set_silent_hours_preset.side_effect = RuntimeError("Crash")
        with pytest.raises(
            HomeAssistantError, match="Unexpected error enabling silent hours schedule"
        ):
            await switch.async_turn_on()
        with pytest.raises(
            HomeAssistantError, match="Unexpected error disabling silent hours schedule"
        ):
            await switch.async_turn_off()

    @pytest.mark.asyncio
    async def test_legionella_prevention_switch_branches(self) -> None:
        """Verify WeheatLegionellaPreventionSwitch edge cases and skips."""
        hp_info = HeatPumpInfo(
            heatpump_id="hp-1",
            name="HP",
            model=0,
            serial_number="SN",
            control_board_id="cb-1",
        )
        coord = MagicMock()
        coord.async_request_refresh = AsyncMock()
        client = MagicMock()
        client.async_trigger_legionella_prevention = AsyncMock(return_value=True)
        client.async_skip_legionella_prevention = AsyncMock(return_value=True)

        switch = WeheatLegionellaPreventionSwitch(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=SWITCH_DESCRIPTIONS[1],
            client=client,
        )

        # 1. is_on with None
        coord.data = None
        assert switch.is_on is None

        # 2. async_turn_on and async_turn_off
        await switch.async_turn_on()
        client.async_trigger_legionella_prevention.assert_awaited_with(
            control_board_id="cb-1", token=None
        )

        await switch.async_turn_off()
        client.async_skip_legionella_prevention.assert_awaited_with(
            control_board_id="cb-1", token=None
        )

        # 3. Missing client
        switch_no_client = WeheatLegionellaPreventionSwitch(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=SWITCH_DESCRIPTIONS[1],
            client=None,
        )
        with pytest.raises(HomeAssistantError):
            await switch_no_client.async_turn_on()
        with pytest.raises(HomeAssistantError):
            await switch_no_client.async_turn_off()

        # 4. Error wrapping
        client.async_trigger_legionella_prevention.side_effect = WeheatApiError("Fail")
        with pytest.raises(
            HomeAssistantError, match="Failed to trigger Legionella prevention"
        ):
            await switch.async_turn_on()

        client.async_skip_legionella_prevention.side_effect = WeheatApiError("Fail")
        with pytest.raises(
            HomeAssistantError, match="Failed to skip Legionella prevention"
        ):
            await switch.async_turn_off()

    @pytest.mark.asyncio
    async def test_dhw_boost_switch_branches(self) -> None:
        """Verify WeheatDhwBoostSwitch edge cases and turn off."""
        hp_info = HeatPumpInfo(
            heatpump_id="hp-1",
            name="HP",
            model=0,
            serial_number="SN",
        )
        coord = MagicMock()
        coord.async_request_refresh = AsyncMock()
        client = MagicMock()
        client.async_trigger_dhw_boost = AsyncMock()

        switch = WeheatDhwBoostSwitch(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=SWITCH_DESCRIPTIONS[2],
            client=client,
        )

        # 1. is_on with None
        coord.data = None
        assert switch.is_on is None

        # 2. turn_off refreshes coordinator
        await switch.async_turn_off()
        coord.async_request_refresh.assert_awaited()

        # 3. turn_on missing client
        switch_no_client = WeheatDhwBoostSwitch(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=SWITCH_DESCRIPTIONS[2],
            client=None,
        )
        with pytest.raises(HomeAssistantError):
            await switch_no_client.async_turn_on()

        # 4. turn_on error wrapping
        client.async_trigger_dhw_boost.side_effect = WeheatApiError("Boost failed")
        with pytest.raises(HomeAssistantError, match="Failed to trigger DHW boost"):
            await switch.async_turn_on()


# ===========================================================================
# 6. Time Platform Adversarial Branches
# ===========================================================================


class TestTimeAdversarialBranches:
    """Stress tests for Weheat Time entities."""

    def test_time_native_value_fallbacks(self) -> None:
        """Verify native_value handles None coordinator data and dict with/without 'settings' wrapper."""
        hp_info = HeatPumpInfo(
            heatpump_id="hp-1", name="HP", model=0, serial_number="SN"
        )
        coord = MagicMock()
        entity = WeheatTimeEntity(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=TIME_ENTITIES[0],  # Monday Start
        )

        # 1. None data
        coord.data = None
        assert entity.native_value is None

        # 2. Raw dict without 'settings' wrapper
        coord.data = {
            "SilentHoursMondayStart": 22,
            "SilentMinutesMondayStart": 15,
        }
        assert entity.native_value == time(22, 15)

        # 3. Raw dict with 'settings' wrapper
        coord.data = {
            "settings": {
                "SilentHoursMondayStart": 20,
                "SilentMinutesMondayStart": 45,
            }
        }
        assert entity.native_value == time(20, 45)

    @pytest.mark.asyncio
    async def test_time_async_set_value_errors_and_fallbacks(self) -> None:
        """Verify async_set_value client validation, token failure, control board error, and fallback method."""
        hp_info = HeatPumpInfo(
            heatpump_id="hp-1",
            name="HP",
            model=0,
            serial_number="SN",
            control_board_id="cb-1",
        )
        coord = MagicMock()
        coord.client = None
        coord.session = None
        coord.data = ControlBoardSettings()
        coord.async_request_refresh = AsyncMock()

        # 1. Missing client
        entity_no_client = WeheatTimeEntity(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=TIME_ENTITIES[0],
            client=None,
        )
        with pytest.raises(
            HomeAssistantError, match="Weheat API client is not configured"
        ):
            await entity_no_client.async_set_value(time(22, 0))

        # 2. Token refresh failure
        client = MagicMock()
        session = MagicMock()
        session.async_ensure_token_valid.side_effect = Exception("OAuth expired")
        coord.session = session
        entity = WeheatTimeEntity(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=TIME_ENTITIES[0],
            client=client,
        )
        with pytest.raises(HomeAssistantError, match="Failed to refresh OAuth token"):
            await entity.async_set_value(time(22, 0))

        # 3. Control board resolution failure
        coord.session = None
        hp_info_no_cb = HeatPumpInfo(
            heatpump_id="hp-1",
            name="HP",
            model=0,
            serial_number="SN",
            control_board_id=None,
        )
        client.async_get_control_board_id = AsyncMock(
            side_effect=Exception("Unknown CB")
        )
        entity_no_cb = WeheatTimeEntity(
            heat_pump_info=hp_info_no_cb,
            coordinator=coord,
            entity_description=TIME_ENTITIES[0],
            client=client,
        )
        with pytest.raises(
            HomeAssistantError, match="Could not resolve control board ID"
        ):
            await entity_no_cb.async_set_value(time(22, 0))

        # 4. Fallback to client.async_set_silent_hours_schedule when async_set_silent_schedule is missing
        client_fallback = MagicMock(
            spec=["async_set_silent_hours_schedule", "async_poll_settings_request"]
        )
        client_fallback.async_set_silent_hours_schedule = AsyncMock(
            return_value="req-fallback"
        )
        client_fallback.async_poll_settings_request = AsyncMock()

        entity_fallback = WeheatTimeEntity(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=TIME_ENTITIES[0],
            client=client_fallback,
        )
        await entity_fallback.async_set_value(time(21, 30))
        client_fallback.async_set_silent_hours_schedule.assert_awaited()

        # 5. WeheatAuthError refreshes coordinator and raises HomeAssistantError
        client_auth_err = MagicMock()
        client_auth_err.async_set_silent_schedule = AsyncMock(
            side_effect=WeheatAuthError("Invalid signature")
        )
        entity_auth_err = WeheatTimeEntity(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=TIME_ENTITIES[0],
            client=client_auth_err,
        )
        with pytest.raises(
            HomeAssistantError, match="Authentication failed updating quiet time"
        ):
            await entity_auth_err.async_set_value(time(21, 30))
        coord.async_request_refresh.assert_awaited()


# ===========================================================================
# 7. Services Target Resolution & Action Branches
# ===========================================================================


class TestServicesAdversarialBranches:
    """Stress tests for services target resolution and corner cases."""

    @pytest.mark.asyncio
    async def test_resolve_target_control_board_id_matching(self) -> None:
        """Verify target resolution via control_board_id parameter."""
        hass = MagicMock()
        hp1 = HeatPumpInfo(
            heatpump_id="hp-1",
            name="HP1",
            model=0,
            serial_number="SN1",
            control_board_id="cb-target-001",
        )
        hp2 = HeatPumpInfo(
            heatpump_id="hp-2",
            name="HP2",
            model=0,
            serial_number="SN2",
            control_board_id="cb-other",
        )

        d1 = WeheatData(
            heat_pump_info=hp1,
            data_coordinator=MagicMock(),
            energy_coordinator=MagicMock(),
        )
        d2 = WeheatData(
            heat_pump_info=hp2,
            data_coordinator=MagicMock(),
            energy_coordinator=MagicMock(),
        )
        entry = ConfigEntry(runtime_data=[d1, d2])
        hass.config_entries.async_entries = MagicMock(return_value=[entry])

        # Match target
        call = MagicMock()
        call.data = {"control_board_id": "cb-target-001"}
        matched = _resolve_target_data(hass, call)
        assert len(matched) == 1
        assert matched[0].heat_pump_info.heatpump_id == "hp-1"

        # Not found
        call_not_found = MagicMock()
        call_not_found.data = {"control_board_id": "cb-nonexistent"}
        with pytest.raises(
            HomeAssistantError, match="No Weheat heat pump found with control board ID"
        ):
            _resolve_target_data(hass, call_not_found)

    @pytest.mark.asyncio
    async def test_resolve_target_invalid_device_id_formats(self) -> None:
        """Verify target resolution rejects invalid device_id types or empty lists."""
        hass = MagicMock()
        d1 = WeheatData(
            heat_pump_info=HeatPumpInfo(
                heatpump_id="hp-1", name="HP1", model=0, serial_number="SN1"
            ),
            data_coordinator=MagicMock(),
            energy_coordinator=MagicMock(),
        )
        entry = ConfigEntry(runtime_data=[d1])
        hass.config_entries.async_entries = MagicMock(return_value=[entry])

        # Invalid type (e.g. integer)
        call_invalid_type = MagicMock()
        call_invalid_type.data = {"device_id": 12345}
        with pytest.raises(HomeAssistantError, match="Invalid device_id format"):
            _resolve_target_data(hass, call_invalid_type)

        # Empty list
        call_empty_list = MagicMock()
        call_empty_list.data = {"device_id": []}
        with pytest.raises(
            HomeAssistantError, match="No device_id specified in target list"
        ):
            _resolve_target_data(hass, call_empty_list)

    @pytest.mark.asyncio
    async def test_services_missing_client_error(self) -> None:
        """Service execution raises HomeAssistantError when target item has no client."""
        hass = MagicMock()
        session = MagicMock()
        session.async_ensure_token_valid = AsyncMock()
        session.token = {CONF_ACCESS_TOKEN: "valid_token"}

        d1 = WeheatData(
            heat_pump_info=HeatPumpInfo(
                heatpump_id="hp-1",
                name="HP1",
                model=0,
                serial_number="SN1",
                control_board_id="cb-1",
            ),
            data_coordinator=MagicMock(),
            energy_coordinator=MagicMock(),
            client=None,  # No client!
            session=session,
        )
        entry = ConfigEntry(runtime_data=[d1])
        hass.config_entries.async_entries = MagicMock(return_value=[entry])

        call = MagicMock()
        call.data = {"heat_pump_id": "hp-1", "mode": 1}

        with pytest.raises(
            HomeAssistantError, match="Weheat API client is unavailable"
        ):
            await async_handle_set_quiet_mode(hass, call)

    @pytest.mark.asyncio
    async def test_set_quiet_mode_execution(self) -> None:
        """Verify weheat.set_quiet_mode dispatches async_set_performance_mode."""
        hass = MagicMock()
        client = MagicMock()
        client.async_set_performance_mode = AsyncMock(return_value="req-perf")
        client.async_poll_settings_request = AsyncMock()

        session = MagicMock()
        session.async_ensure_token_valid = AsyncMock()
        session.token = {CONF_ACCESS_TOKEN: "valid_token"}

        data_coord = MagicMock()
        data_coord.async_request_refresh = AsyncMock()

        settings_coord = MagicMock()
        settings_coord.data = ControlBoardSettings()
        settings_coord.async_request_refresh = AsyncMock()

        d1 = WeheatData(
            heat_pump_info=HeatPumpInfo(
                heatpump_id="hp-1",
                name="HP1",
                model=0,
                serial_number="SN1",
                control_board_id="cb-1",
            ),
            data_coordinator=data_coord,
            energy_coordinator=MagicMock(),
            settings_coordinator=settings_coord,
            client=client,
            session=session,
        )
        entry = ConfigEntry(runtime_data=[d1])
        hass.config_entries.async_entries = MagicMock(return_value=[entry])

        call = MagicMock()
        call.data = {
            "heat_pump_id": "hp-1",
            "mode": 2,
        }

        await async_handle_set_quiet_mode(hass, call)
        client.async_set_performance_mode.assert_awaited_with(
            control_board_id="cb-1",
            token="valid_token",
            preset=2,
        )
        settings_coord.async_request_refresh.assert_awaited()
        data_coord.async_request_refresh.assert_awaited()

    @pytest.mark.asyncio
    async def test_set_silent_schedule_with_preset_parameter(self) -> None:
        """Verify weheat.set_silent_schedule accepts preset parameter and updates both preset and schedule."""
        hass = MagicMock()
        client = MagicMock()
        client.async_set_silent_hours_schedule = AsyncMock(return_value="req-sched2")
        client.async_poll_settings_request = AsyncMock()

        session = MagicMock()
        session.async_ensure_token_valid = AsyncMock()
        session.token = {CONF_ACCESS_TOKEN: "valid_token"}

        data_coord = MagicMock()
        data_coord.async_request_refresh = AsyncMock()

        settings_coord = MagicMock()
        settings_coord.data = ControlBoardSettings()
        settings_coord.async_request_refresh = AsyncMock()

        d1 = WeheatData(
            heat_pump_info=HeatPumpInfo(
                heatpump_id="hp-1",
                name="HP1",
                model=0,
                serial_number="SN1",
                control_board_id="cb-1",
            ),
            data_coordinator=data_coord,
            energy_coordinator=MagicMock(),
            settings_coordinator=settings_coord,
            client=client,
            session=session,
        )
        entry = ConfigEntry(runtime_data=[d1])
        hass.config_entries.async_entries = MagicMock(return_value=[entry])

        call = MagicMock()
        call.data = {
            "heat_pump_id": "hp-1",
            "days": "weekdays",
            "start_time": "22:00",
            "stop_time": "06:00",
            "preset": 3,
        }

        await async_handle_set_silent_schedule(hass, call)
        client.async_set_silent_hours_schedule.assert_awaited()
        # Verify preset was passed as 3
        kwargs = client.async_set_silent_hours_schedule.await_args.kwargs
        assert kwargs["preset"] == 3
        assert "SilentHoursMondayStart" in kwargs["schedule"]


# ===========================================================================
# 8. Hardening Exhaustive Branches & Fallback Safety
# ===========================================================================


class TestHardeningExhaustiveBranches:
    """Targeted coverage hardening for remaining defensive branches."""

    @pytest.mark.asyncio
    async def test_topology_non_dict_non_list_payload(
        self, client_session: aiohttp.ClientSession, mock_aioresponse: aioresponses
    ) -> None:
        """Endpoint /installations/fully-expanded returning string or primitive returns empty list."""
        client = WeheatClient(
            session=client_session,
            base_url=API_BASE_URL,
            portal_base_url=PORTAL_BASE_URL,
        )
        mock_aioresponse.get(
            f"{PORTAL_BASE_URL}/api/v1/installations/fully-expanded",
            status=200,
            payload="malformed_string_response",
        )
        topology = await client.async_get_installation_topology()
        assert topology == []

    @pytest.mark.asyncio
    async def test_silent_hours_schedule_minutes_stop_boundary(
        self, client_session: aiohttp.ClientSession
    ) -> None:
        """Verify async_set_silent_hours_schedule rejects minutes outside 0..59 on Stop minutes."""
        client = WeheatClient(session=client_session)
        with pytest.raises(
            ValueError, match="Invalid minute for 'SilentMinutesMondayStop': 75"
        ):
            await client.async_set_silent_hours_schedule(
                control_board_id="cb-1",
                preset=2,
                schedule={"SilentMinutesMondayStop": 75},
            )

    @pytest.mark.asyncio
    async def test_silent_hours_preset_select_control_board_and_client_errors(
        self,
    ) -> None:
        """Verify WeheatSilentHoursPresetSelect resolves control_board_id or raises when client is None."""
        hp_info = HeatPumpInfo(
            heatpump_id="hp-1",
            name="HP",
            model=0,
            serial_number="SN",
            control_board_id=None,
        )
        client = MagicMock()
        client.async_get_control_board_id = AsyncMock(return_value="cb-resolved-silent")
        coord = MagicMock()
        coord.data = ControlBoardSettings()

        entity = WeheatSilentHoursPresetSelect(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=SELECT_ENTITIES[1],
            client=client,
        )
        cb_id = await entity._async_get_control_board_id()
        assert cb_id == "cb-resolved-silent"

        # Missing client raises HomeAssistantError
        entity_no_client = WeheatSilentHoursPresetSelect(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=SELECT_ENTITIES[1],
            client=None,
        )
        with pytest.raises(
            HomeAssistantError, match="No client available to resolve control board ID"
        ):
            await entity_no_client._async_get_control_board_id()

        with pytest.raises(HomeAssistantError, match="Weheat client is not available"):
            await entity_no_client.async_select_option("Silent 1")

    @pytest.mark.asyncio
    async def test_silent_hours_schedule_switch_turn_off_weheat_error(self) -> None:
        """Verify WeheatSilentHoursScheduleSwitch.async_turn_off catches WeheatError."""
        hp_info = HeatPumpInfo(
            heatpump_id="hp-1",
            name="HP",
            model=0,
            serial_number="SN",
            control_board_id="cb-1",
        )
        coord = MagicMock()
        coord.data = ControlBoardSettings()
        client = MagicMock()
        client.async_set_silent_hours_preset = AsyncMock(
            side_effect=WeheatApiError("Portal rejected turn off")
        )

        switch = WeheatSilentHoursScheduleSwitch(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=SWITCH_DESCRIPTIONS[0],
            client=client,
        )
        with pytest.raises(
            HomeAssistantError,
            match="Failed to disable silent hours schedule: Portal rejected turn off",
        ):
            await switch.async_turn_off()

    @pytest.mark.asyncio
    async def test_legionella_switch_control_board_resolution_and_unexpected_crashes(
        self,
    ) -> None:
        """Verify WeheatLegionellaPreventionSwitch control board resolution and unexpected error wrapping."""
        hp_info = HeatPumpInfo(
            heatpump_id="hp-1",
            name="HP",
            model=0,
            serial_number="SN",
            control_board_id=None,
        )
        client = MagicMock()
        client.async_get_control_board_id = AsyncMock(return_value="cb-resolved-leg")
        client.async_trigger_legionella_prevention = AsyncMock(
            side_effect=RuntimeError("Hardware communication crash")
        )
        client.async_skip_legionella_prevention = AsyncMock(
            side_effect=RuntimeError("Hardware communication crash")
        )
        coord = MagicMock()

        switch = WeheatLegionellaPreventionSwitch(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=SWITCH_DESCRIPTIONS[1],
            client=client,
        )
        cb_id = await switch._async_get_control_board_id()
        assert cb_id == "cb-resolved-leg"

        with pytest.raises(
            HomeAssistantError,
            match="Unexpected error triggering Legionella prevention",
        ):
            await switch.async_turn_on()

        with pytest.raises(
            HomeAssistantError, match="Unexpected error skipping Legionella prevention"
        ):
            await switch.async_turn_off()

        # Missing client raises HomeAssistantError on _async_get_control_board_id
        switch_no_client = WeheatLegionellaPreventionSwitch(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=SWITCH_DESCRIPTIONS[1],
            client=None,
        )
        with pytest.raises(
            HomeAssistantError, match="No client available to resolve control board ID"
        ):
            await switch_no_client._async_get_control_board_id()

    @pytest.mark.asyncio
    async def test_dhw_boost_switch_unexpected_crash(self) -> None:
        """Verify WeheatDhwBoostSwitch wraps unexpected exceptions in HomeAssistantError."""
        hp_info = HeatPumpInfo(
            heatpump_id="hp-1",
            name="HP",
            model=0,
            serial_number="SN",
        )
        client = MagicMock()
        client.async_trigger_dhw_boost = AsyncMock(
            side_effect=RuntimeError("Boost backend exploded")
        )
        coord = MagicMock()

        switch = WeheatDhwBoostSwitch(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=SWITCH_DESCRIPTIONS[2],
            client=client,
        )
        with pytest.raises(
            HomeAssistantError,
            match="Unexpected error triggering DHW boost: Boost backend exploded",
        ):
            await switch.async_turn_on()

    @pytest.mark.asyncio
    async def test_time_entity_non_dict_data_and_optimistic_write(self) -> None:
        """Verify WeheatTimeEntity returns None for scalar data and executes async_write_ha_state."""
        hp_info = HeatPumpInfo(
            heatpump_id="hp-1",
            name="HP",
            model=0,
            serial_number="SN",
            control_board_id="cb-1",
        )
        coord = MagicMock()
        coord.client = None
        coord.session = None
        coord.data = "scalar_string_data"

        entity = WeheatTimeEntity(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=TIME_ENTITIES[0],
            client=MagicMock(),
        )
        # 1. Non-dict, non-dataclass data returns None
        assert entity.native_value is None

        # 2. Optimistic update invokes async_write_ha_state when hass is attached
        settings = ControlBoardSettings.from_api_dict(
            load_fixture("installation_settings_get.json")
        )
        coord.data = settings
        entity._client.async_set_silent_schedule = AsyncMock(return_value="req-time")
        entity._client.async_poll_settings_request = AsyncMock()
        coord.async_request_refresh = AsyncMock()

        entity.hass = MagicMock()
        entity.async_write_ha_state = MagicMock()

        await entity.async_set_value(time(23, 15))
        assert settings.schedules["Monday"].start_hour == 23
        assert settings.schedules["Monday"].start_minute == 15
        entity.async_write_ha_state.assert_called_once()
