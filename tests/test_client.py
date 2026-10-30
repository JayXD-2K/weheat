"""Unit tests for the in-tree WeheatClient and data models."""

from __future__ import annotations

import asyncio

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.weheat.client import (
    WeheatApiError,
    WeheatAuthError,
    WeheatClient,
    WeheatTimeoutError,
)
from custom_components.weheat.models import (
    ControlBoardSettings,
    HeatPumpEnergy,
    HeatPumpInfo,
    HeatPumpLog,
    HeatPumpState,
    PerformanceModePreset,
    SettingsRequestStatus,
    SilentHoursScheduledPreset,
)
from tests.conftest import (
    API_BASE_URL,
    PORTAL_BASE_URL,
    TEST_ACCESS_TOKEN,
    TEST_CB_ID_BLACKBIRD,
    TEST_HP_UUID_BLACKBIRD,
    TEST_HP_UUID_FLINT,
    TEST_HP_UUID_SPARROW,
    TEST_INSTALLATION_ID,
    TEST_SETTINGS_REQUEST_ID,
    TEST_USER_ID,
    load_fixture,
)


@pytest.fixture
def client(client_session: aiohttp.ClientSession) -> WeheatClient:
    """Create a WeheatClient instance for testing."""
    return WeheatClient(
        session=client_session,
        base_url=API_BASE_URL,
        portal_base_url=PORTAL_BASE_URL,
    )


# ---------------------------------------------------------------------------
# 1. Client Initialization & Configuration Tests
# ---------------------------------------------------------------------------


def test_client_init_defaults(client_session: aiohttp.ClientSession) -> None:
    """Verify WeheatClient default base URLs."""
    default_client = WeheatClient(session=client_session)
    assert default_client.base_url == "https://api.weheat.nl/third_party"
    assert default_client.portal_base_url == "https://api.weheat.nl"


def test_client_init_custom_urls(client_session: aiohttp.ClientSession) -> None:
    """Verify WeheatClient custom base URLs are honored."""
    custom_client = WeheatClient(
        session=client_session,
        base_url="https://custom-api.example.com",
        portal_base_url="https://custom-portal.example.com",
    )
    assert custom_client.base_url == "https://custom-api.example.com"
    assert custom_client.portal_base_url == "https://custom-portal.example.com"


# ---------------------------------------------------------------------------
# 2. Authentication and HTTP Error Handling Tests
# ---------------------------------------------------------------------------


async def test_auth_error_on_401(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
) -> None:
    """Verify HTTP 401 Unauthorized raises WeheatAuthError."""
    mock_aioresponse.get(
        f"{API_BASE_URL}/api/v1/users/me",
        status=401,
        payload={"message": "Unauthorized token"},
    )
    with pytest.raises(WeheatAuthError) as exc_info:
        await client.async_get_user_id("invalid_token")
    assert exc_info.value.status_code == 401


async def test_auth_error_on_403(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
) -> None:
    """Verify HTTP 403 Forbidden raises WeheatAuthError."""
    mock_aioresponse.get(
        f"{API_BASE_URL}/api/v1/users/me",
        status=403,
        payload={"message": "Forbidden scope"},
    )
    with pytest.raises(WeheatAuthError) as exc_info:
        await client.async_get_user_id("forbidden_token")
    assert exc_info.value.status_code == 403


async def test_api_error_on_404(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
) -> None:
    """Verify HTTP 404 Not Found raises WeheatApiError with status code."""
    mock_aioresponse.get(
        f"{API_BASE_URL}/api/v1/heat-pumps/unknown-uuid/logs/latest",
        status=404,
        payload={"message": "Heat pump not found"},
    )
    with pytest.raises(WeheatApiError) as exc_info:
        await client.async_get_latest_logs("unknown-uuid", TEST_ACCESS_TOKEN)
    assert exc_info.value.status_code == 404


async def test_api_error_on_500(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
) -> None:
    """Verify HTTP 500 Internal Server Error raises WeheatApiError."""
    mock_aioresponse.get(
        f"{API_BASE_URL}/api/v1/heat-pumps/{TEST_HP_UUID_BLACKBIRD}/logs/latest",
        status=500,
        payload={"message": "Internal server error"},
    )
    with pytest.raises(WeheatApiError) as exc_info:
        await client.async_get_latest_logs(TEST_HP_UUID_BLACKBIRD, TEST_ACCESS_TOKEN)
    assert exc_info.value.status_code == 500


async def test_timeout_error_handling(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
) -> None:
    """Verify network timeout raises WeheatTimeoutError."""
    mock_aioresponse.get(
        f"{API_BASE_URL}/api/v1/users/me",
        exception=TimeoutError(),
    )
    with pytest.raises(WeheatTimeoutError):
        await client.async_get_user_id(TEST_ACCESS_TOKEN)


# ---------------------------------------------------------------------------
# 3. Telemetry Read Methods Tests
# ---------------------------------------------------------------------------


async def test_async_get_user_id(
    client: WeheatClient,
    setup_mock_api: aioresponses,
) -> None:
    """Verify async_get_user_id retrieves user UUID."""
    user_id = await client.async_get_user_id(TEST_ACCESS_TOKEN)
    assert user_id == TEST_USER_ID


async def test_async_get_heat_pumps(
    client: WeheatClient,
    setup_mock_api: aioresponses,
) -> None:
    """Verify async_get_heat_pumps deserializes HeatPumpInfo instances."""
    pumps = await client.async_get_heat_pumps(TEST_ACCESS_TOKEN)
    assert len(pumps) == 3

    # Model 0: Blackbird
    bb = pumps[0]
    assert isinstance(bb, HeatPumpInfo)
    assert bb.heatpump_id == TEST_HP_UUID_BLACKBIRD
    assert bb.control_board_id == TEST_CB_ID_BLACKBIRD
    assert bb.model == 0
    assert bb.model_name == "Blackbird P80 heat pump"
    assert bb.nominal_rpm == 4500
    assert bb.has_dhw is True
    assert bb.has_ch_boiler is True

    # Model 2: Sparrow
    sp = pumps[1]
    assert sp.heatpump_id == TEST_HP_UUID_SPARROW
    assert sp.model == 2
    assert sp.model_name == "Sparrow P60 heat pump"
    assert sp.nominal_rpm == 5520
    assert sp.has_dhw is False

    # Model 5: Flint
    fl = pumps[2]
    assert fl.heatpump_id == TEST_HP_UUID_FLINT
    assert fl.model == 5
    assert fl.model_name == "Flint P40 heat pump"
    assert fl.nominal_rpm == 5400


async def test_async_get_installation_topology(
    client: WeheatClient,
    setup_mock_api: aioresponses,
) -> None:
    """Verify async_get_installation_topology maps installations and control boards."""
    topology = await client.async_get_installation_topology(TEST_ACCESS_TOKEN)
    assert len(topology) == 3
    assert topology[0]["controlBoardId"] == TEST_CB_ID_BLACKBIRD
    assert topology[0]["id"] == TEST_HP_UUID_BLACKBIRD


async def test_async_get_control_board_id_success(
    client: WeheatClient,
    setup_mock_api: aioresponses,
) -> None:
    """Verify async_get_control_board_id resolves controlBoardId for a heat pump UUID."""
    cb_id = await client.async_get_control_board_id(
        TEST_HP_UUID_BLACKBIRD, TEST_ACCESS_TOKEN
    )
    assert cb_id == TEST_CB_ID_BLACKBIRD


async def test_async_get_control_board_id_not_found(
    client: WeheatClient,
    setup_mock_api: aioresponses,
) -> None:
    """Verify async_get_control_board_id raises WeheatApiError if pump is missing."""
    with pytest.raises(WeheatApiError):
        await client.async_get_control_board_id("non-existent-pump", TEST_ACCESS_TOKEN)


async def test_async_get_latest_logs(
    client: WeheatClient,
    setup_mock_api: aioresponses,
) -> None:
    """Verify async_get_latest_logs decodes all operational metrics into HeatPumpLog."""
    logs = await client.async_get_latest_logs(TEST_HP_UUID_BLACKBIRD, TEST_ACCESS_TOKEN)
    assert isinstance(logs, HeatPumpLog)
    assert logs.heat_pump_id == TEST_HP_UUID_BLACKBIRD
    assert logs.state == HeatPumpState.HEATING
    assert logs.is_online is True

    # Temperatures
    assert logs.t_water_in == 32.4
    assert logs.t_water_out == 37.2
    assert logs.t_air_in == 7.8
    assert logs.t_air_out == 4.1
    assert logs.water_inlet_temperature == 32.4
    assert logs.water_outlet_temperature == 37.2

    # Compressor & Power
    assert logs.rpm == 2850
    assert logs.compressor_rpm == 2850.0
    assert logs.compressor_percentage(4500) == 63
    assert logs.power_input == 1120.0
    assert logs.power_output == 4700.0
    assert round(logs.cop, 2) == 4.20

    # Flow
    assert logs.central_heating_flow == 35
    assert logs.central_heating_flow_volume == 0.90
    assert logs.dhw_flow == 0
    assert logs.dhw_flow_volume == 0.0

    # Binary Actuators
    assert logs.indoor_unit_water_pump_state is True
    assert logs.indoor_unit_gas_boiler_state is False
    assert logs.indoor_unit_electric_heater_state is False

    # Cooling Diagnostics
    assert logs.cooling_start_conditions is not None
    assert isinstance(logs.cooling_start_conditions, dict)


async def test_async_get_energy_total(
    client: WeheatClient,
    setup_mock_api: aioresponses,
) -> None:
    """Verify async_get_energy_total decodes cumulative kWh registers into HeatPumpEnergy."""
    energy = await client.async_get_energy_total(
        TEST_HP_UUID_BLACKBIRD, TEST_ACCESS_TOKEN
    )
    assert isinstance(energy, HeatPumpEnergy)
    assert energy.heatpump_id == TEST_HP_UUID_BLACKBIRD
    assert energy.electricity_used == 1721.0
    assert energy.energy_total == 1721.0
    assert energy.electricity_used_indoor_unit == 49.4
    assert energy.energy_output == 6309.4
    assert energy.energy_output_defrost == -33.2
    assert energy.electricity_used_defrost == 37.5


async def test_async_get_installation_settings(
    client: WeheatClient,
    setup_mock_api: aioresponses,
) -> None:
    """Verify async_get_installation_settings decodes ControlBoardSettings."""
    settings = await client.async_get_installation_settings(
        TEST_INSTALLATION_ID, TEST_ACCESS_TOKEN
    )
    assert isinstance(settings, ControlBoardSettings)
    assert settings.performance_mode_preset == 0
    assert settings.silent_hours_preset == 2
    assert settings.dhw_target_temperature == 50.0
    assert "Monday" in settings.schedules
    assert settings.schedules["Monday"].start_hour == 19
    assert settings.schedules["Monday"].stop_hour == 7


# ---------------------------------------------------------------------------
# 4. Settings Mutation Methods Tests
# ---------------------------------------------------------------------------


async def test_async_set_performance_mode_success(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
) -> None:
    """Verify setting Performance Mode preset sends correct PATCH payload."""
    url = f"{PORTAL_BASE_URL}/api/v1/control-boards/{TEST_CB_ID_BLACKBIRD}/settings"
    mock_aioresponse.patch(
        url,
        payload=load_fixture("settings_update_success.json"),
        status=200,
    )

    request_id = await client.async_set_performance_mode(
        control_board_id=TEST_CB_ID_BLACKBIRD,
        token=TEST_ACCESS_TOKEN,
        preset=PerformanceModePreset.SILENT_2,
    )
    assert request_id == TEST_SETTINGS_REQUEST_ID

    # Verify intercepted request
    assert any(
        method == "PATCH" and str(target_url) == url
        for method, target_url in mock_aioresponse.requests
    )


@pytest.mark.parametrize("invalid_preset", [-1, 5, 10])
async def test_async_set_performance_mode_validation(
    client: WeheatClient,
    invalid_preset: int,
) -> None:
    """Verify invalid performance mode presets raise ValueError before network dispatch."""
    with pytest.raises(ValueError, match="preset must be between 0 and 4"):
        await client.async_set_performance_mode(
            control_board_id=TEST_CB_ID_BLACKBIRD,
            token=TEST_ACCESS_TOKEN,
            preset=invalid_preset,
        )


async def test_async_set_silent_hours_preset_success(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
) -> None:
    """Verify setting Silent Hours schedule preset sends correct PATCH payload."""
    url = f"{PORTAL_BASE_URL}/api/v1/control-boards/{TEST_CB_ID_BLACKBIRD}/settings"
    mock_aioresponse.patch(
        url,
        payload=load_fixture("settings_update_success.json"),
        status=200,
    )

    request_id = await client.async_set_silent_hours_preset(
        control_board_id=TEST_CB_ID_BLACKBIRD,
        token=TEST_ACCESS_TOKEN,
        preset=SilentHoursScheduledPreset.SILENT_3,
    )
    assert request_id == TEST_SETTINGS_REQUEST_ID


@pytest.mark.parametrize("invalid_preset", [-1, 8, 99])
async def test_async_set_silent_hours_preset_validation(
    client: WeheatClient,
    invalid_preset: int,
) -> None:
    """Verify invalid silent hours presets raise ValueError."""
    with pytest.raises(ValueError, match="preset must be between 0 and 7"):
        await client.async_set_silent_hours_preset(
            control_board_id=TEST_CB_ID_BLACKBIRD,
            token=TEST_ACCESS_TOKEN,
            preset=invalid_preset,
        )


async def test_async_set_silent_schedule(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
) -> None:
    """Verify setting day-by-day silent schedule sends full dictionary."""
    url = f"{PORTAL_BASE_URL}/api/v1/control-boards/{TEST_CB_ID_BLACKBIRD}/settings"
    mock_aioresponse.patch(
        url,
        payload=load_fixture("settings_update_success.json"),
        status=200,
    )

    schedule = {
        "SilentHoursMondayStart": 22,
        "SilentMinutesMondayStart": 0,
        "SilentHoursMondayStop": 7,
        "SilentMinutesMondayStop": 30,
    }
    request_id = await client.async_set_silent_schedule(
        control_board_id=TEST_CB_ID_BLACKBIRD,
        token=TEST_ACCESS_TOKEN,
        schedule=schedule,
    )
    assert request_id == TEST_SETTINGS_REQUEST_ID


async def test_async_set_dhw_target_temperature_success(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
) -> None:
    """Verify setting DHW target temperature dispatches PATCH."""
    url = f"{PORTAL_BASE_URL}/api/v1/control-boards/{TEST_CB_ID_BLACKBIRD}/settings"
    mock_aioresponse.patch(
        url,
        payload=load_fixture("settings_update_success.json"),
        status=200,
    )

    request_id = await client.async_set_dhw_target_temperature(
        control_board_id=TEST_CB_ID_BLACKBIRD,
        token=TEST_ACCESS_TOKEN,
        temperature=52.5,
    )
    assert request_id == TEST_SETTINGS_REQUEST_ID


@pytest.mark.parametrize("invalid_temp", [35.0, 39.9, 60.1, 75.0])
async def test_async_set_dhw_target_temperature_validation(
    client: WeheatClient,
    invalid_temp: float,
) -> None:
    """Verify out-of-range DHW temperatures raise ValueError."""
    with pytest.raises(ValueError, match="temperature must be between 40 and 60"):
        await client.async_set_dhw_target_temperature(
            control_board_id=TEST_CB_ID_BLACKBIRD,
            token=TEST_ACCESS_TOKEN,
            temperature=invalid_temp,
        )


# ---------------------------------------------------------------------------
# 5. Asynchronous Polling & Status Enum Handling Tests
# ---------------------------------------------------------------------------


async def test_async_poll_settings_request_success(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
) -> None:
    """Verify polling loop transitions through Pending -> Executing -> Succeeded."""
    url = f"{PORTAL_BASE_URL}/api/v1/control-boards/{TEST_CB_ID_BLACKBIRD}/settings/{TEST_SETTINGS_REQUEST_ID}"

    # Three successive GET calls returning Pending (0), Executing (1), Succeeded (2)
    mock_aioresponse.get(
        url, payload={"status": SettingsRequestStatus.PENDING}, status=200
    )
    mock_aioresponse.get(
        url, payload={"status": SettingsRequestStatus.EXECUTING}, status=200
    )
    mock_aioresponse.get(
        url,
        payload={
            "status": SettingsRequestStatus.SUCCEEDED,
            "settingsRequestId": TEST_SETTINGS_REQUEST_ID,
        },
        status=200,
    )

    result = await client.async_poll_settings_request(
        control_board_id=TEST_CB_ID_BLACKBIRD,
        request_id=TEST_SETTINGS_REQUEST_ID,
        token=TEST_ACCESS_TOKEN,
        timeout_seconds=5.0,
        poll_interval=0.01,
    )
    assert result["status"] == SettingsRequestStatus.SUCCEEDED


async def test_async_poll_settings_request_failure(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
) -> None:
    """Verify polling loop raises WeheatApiError when setting request status is Failed (4)."""
    url = f"{PORTAL_BASE_URL}/api/v1/control-boards/{TEST_CB_ID_BLACKBIRD}/settings/{TEST_SETTINGS_REQUEST_ID}"
    mock_aioresponse.get(
        url,
        payload={
            "status": SettingsRequestStatus.FAILED,
            "error": "Device rejected settings",
        },
        status=200,
    )

    with pytest.raises(WeheatApiError, match="Settings request failed with status"):
        await client.async_poll_settings_request(
            control_board_id=TEST_CB_ID_BLACKBIRD,
            request_id=TEST_SETTINGS_REQUEST_ID,
            token=TEST_ACCESS_TOKEN,
            poll_interval=0.01,
        )


async def test_async_poll_settings_request_timeout(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
) -> None:
    """Verify polling loop raises WeheatTimeoutError if request does not resolve within timeout."""
    url = f"{PORTAL_BASE_URL}/api/v1/control-boards/{TEST_CB_ID_BLACKBIRD}/settings/{TEST_SETTINGS_REQUEST_ID}"
    # Continuously return pending
    for _ in range(5):
        mock_aioresponse.get(
            url, payload={"status": SettingsRequestStatus.PENDING}, status=200
        )

    with pytest.raises(WeheatTimeoutError, match="timed out"):
        await client.async_poll_settings_request(
            control_board_id=TEST_CB_ID_BLACKBIRD,
            request_id=TEST_SETTINGS_REQUEST_ID,
            token=TEST_ACCESS_TOKEN,
            timeout_seconds=0.05,
            poll_interval=0.01,
        )


# ---------------------------------------------------------------------------
# 6. Variables Mutation Tests (Legionella Prevention)
# ---------------------------------------------------------------------------


async def test_async_trigger_legionella_prevention_success(
    client: WeheatClient,
    setup_mock_api: aioresponses,
) -> None:
    """Verify triggering Legionella prevention cycle dispatches variables PATCH."""
    success = await client.async_trigger_legionella_prevention(
        control_board_id=TEST_CB_ID_BLACKBIRD,
        token=TEST_ACCESS_TOKEN,
    )
    assert success is True


async def test_async_trigger_legionella_prevention_failure(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
) -> None:
    """Verify failure to mutate variables raises WeheatApiError."""
    url = f"{PORTAL_BASE_URL}/api/v1/control-boards/{TEST_CB_ID_BLACKBIRD}/variables"
    mock_aioresponse.patch(
        url,
        status=500,
        payload={"error": "Failed to update variable"},
    )
    with pytest.raises(WeheatApiError):
        await client.async_trigger_legionella_prevention(
            control_board_id=TEST_CB_ID_BLACKBIRD,
            token=TEST_ACCESS_TOKEN,
        )


# ---------------------------------------------------------------------------
# 7. Domestic Hot Water Boost Action Tests
# ---------------------------------------------------------------------------


async def test_async_trigger_dhw_boost(
    client: WeheatClient,
    setup_mock_api: aioresponses,
) -> None:
    """Verify DHW boost POST request."""
    result = await client.async_trigger_dhw_boost(
        heat_pump_uuid=TEST_HP_UUID_BLACKBIRD,
        token=TEST_ACCESS_TOKEN,
        duration_minutes=60,
        target_temperature=60.0,
    )
    assert result["success"] is True
    assert result["boostActive"] is True
    assert result["durationMinutes"] == 60
    assert result["targetTemperature"] == 60.0


# ---------------------------------------------------------------------------
# 8. Test Harness Idempotency Guard (Challenger 2 Advisory)
# ---------------------------------------------------------------------------


def test_conftest_monkeypatch_idempotency() -> None:
    """Verify repeated reloading of tests.conftest does not cause RecursionError (Challenger 2)."""
    import importlib

    import tests.conftest

    for _ in range(25):
        importlib.reload(tests.conftest)

    # Verify ClientResponse can be instantiated without RecursionError
    loop = asyncio.new_event_loop()
    try:
        import yarl

        resp = aiohttp.client_reqrep.ClientResponse(
            "GET",
            yarl.URL("http://example.com"),
            writer=None,
            continue100=None,
            timer=None,
            request_info=None,
            traces=[],
            loop=loop,
            session=None,
        )
        assert resp is not None
    finally:
        loop.close()
