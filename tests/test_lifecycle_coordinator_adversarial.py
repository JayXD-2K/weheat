"""Adversarial stress testing for Milestone 3: Lifecycle, Coordinator Concurrency, and Diagnostics Redaction.

This test suite covers:
1. Coordinator lifecycle & concurrency:
   - Burst concurrent calls to _async_update_data (100 parallel tasks)
   - Interleaved concurrent executions of data and energy coordinators
   - Rapid refresh requests / burst updates
   - Simulated network drops (ClientError, ServerDisconnectedError, ClientOSError, ClientPayloadError)
   - HTTP 401/403 auth expirations triggering ConfigEntryAuthFailed
   - HTTP 500/502/503/504 errors triggering UpdateFailed
   - Coordinator invocation with missing heat pump discovery metadata
2. Polling interval dynamics:
   - Verification of update_interval scaling for 1, 2, 5, 10 heat pumps (LOG_UPDATE_INTERVAL * nr_of_heat_pumps)
   - Clamping of 0 and negative heat pump counts to 120s
   - Energy coordinator fixed interval of 1800s (30m) regardless of heat pump count
3. Config flow edge cases:
   - Reauth with matching account ID (reauth_successful)
   - Reauth with mismatched account ID (wrong_account)
   - OAuth token failure variants (WeheatAuthError, WeheatApiError, generic Exception, None/empty user ID)
   - Duplicate entry creation (already_configured)
   - Integration setup with zero heat pumps returned by API (clean graceful setup)
4. Diagnostics sensitive data leakage:
   - 100% redaction of all sensitive keys (heat_pump_id, sn, serial_number, uuid, access_token, refresh_token, control_board_id, installation_id)
   - Deeply nested structures, lists of objects, and maliciously injected keys
   - Preservation of legitimate telemetry (temperatures, flow, rpm, etc.)
   - Graceful handling of empty or missing coordinator data
   - Diagnostics fallback for non-dataclass heat pump info
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from aiohttp import (
    ClientError,
    ClientOSError,
    ClientPayloadError,
    ClientResponseError,
    ServerDisconnectedError,
)
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.weheat import (
    PLATFORMS,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.weheat.client import (
    WeheatApiError,
    WeheatAuthError,
    WeheatTimeoutError,
)
from custom_components.weheat.config_flow import OAuth2FlowHandler
from custom_components.weheat.const import (
    ENERGY_UPDATE_INTERVAL,
    ENTRY_TITLE,
    LOG_UPDATE_INTERVAL,
)
from custom_components.weheat.coordinator import (
    WeheatDataUpdateCoordinator,
    WeheatEnergyUpdateCoordinator,
)
from custom_components.weheat.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.weheat.models import (
    HeatPumpEnergy,
    HeatPumpInfo,
    HeatPumpLog,
)
from tests.ha_compat import FlowAbortException


@pytest.fixture
def test_hp_info() -> HeatPumpInfo:
    """Fixture providing a standard test HeatPumpInfo instance."""
    return HeatPumpInfo(
        heatpump_id="00000000-0000-0000-0000-000000000001",
        name="Adversarial Blackbird P80",
        model=0,
        serial_number="WH-BB80-ADV-001",
        has_dhw=True,
        has_ch_boiler=False,
        firmware_version="3.0.0",
        part_number="PN-ADV-01",
        control_board_id="00000000-0000-0000-0000-0000000000CB",
        installation_id="00000000-0000-0000-0000-0000000000IN",
    )


# ===========================================================================
# 1. Coordinator Lifecycle & Concurrency Stress Testing
# ===========================================================================


@pytest.mark.asyncio
async def test_coordinator_concurrency_burst_reads(
    test_hp_info: HeatPumpInfo,
) -> None:
    """Stress-test coordinator with 100 concurrent asynchronous update calls."""
    hass = HomeAssistant()
    entry = ConfigEntry()
    mock_client = MagicMock()

    call_counter = 0

    async def mock_fetch_logs(uuid: str, token: str | None = None) -> HeatPumpLog:
        nonlocal call_counter
        call_counter += 1
        # Introduce micro-yield to stress concurrency interleaving
        await asyncio.sleep(0.001)
        return HeatPumpLog(
            heat_pump_id=uuid,
            rpm=3000,
            t_water_in=45.2,
            t_water_out=49.8,
        )

    mock_client.async_get_latest_logs = AsyncMock(side_effect=mock_fetch_logs)

    coord = WeheatDataUpdateCoordinator(
        hass,
        entry,
        session=None,
        heat_pump_info=test_hp_info,
        nr_of_heat_pumps=1,
        client=mock_client,
    )

    # Launch 100 concurrent requests simultaneously
    results = await asyncio.gather(*[coord._async_update_data() for _ in range(100)])

    assert len(results) == 100
    assert call_counter == 100
    for item in results:
        assert isinstance(item, HeatPumpLog)
        assert item.heat_pump_id == test_hp_info.heatpump_id
        assert item.rpm == 3000
        # Verify nominal_rpm propagation under heavy concurrency
        assert item.nominal_rpm == test_hp_info.nominal_rpm
        assert item.compressor_percentage == int((100.0 / 4500) * 3000)


@pytest.mark.asyncio
async def test_coordinator_concurrency_mixed_data_and_energy(
    test_hp_info: HeatPumpInfo,
) -> None:
    """Stress-test concurrent interleave between telemetry and energy coordinators."""
    hass = HomeAssistant()
    entry = ConfigEntry()
    mock_client = MagicMock()

    mock_client.async_get_latest_logs = AsyncMock(
        side_effect=lambda uuid, token=None: HeatPumpLog(
            heat_pump_id=uuid, rpm=2400, t_water_in=38.0
        )
    )
    mock_client.async_get_energy_total = AsyncMock(
        side_effect=lambda uuid, token=None: HeatPumpEnergy(
            heatpump_id=uuid, total_ein_heating=1500.5, total_eout_heating=6000.2
        )
    )

    data_coord = WeheatDataUpdateCoordinator(
        hass, entry, None, test_hp_info, client=mock_client
    )
    energy_coord = WeheatEnergyUpdateCoordinator(
        hass, entry, None, test_hp_info, client=mock_client
    )

    # Launch 50 telemetry reads and 50 energy reads concurrently
    tasks = []
    for _ in range(50):
        tasks.append(data_coord._async_update_data())
        tasks.append(energy_coord._async_update_data())

    mixed_results = await asyncio.gather(*tasks)
    assert len(mixed_results) == 100

    # Evens are logs, odds are energy
    for idx, res in enumerate(mixed_results):
        if idx % 2 == 0:
            assert isinstance(res, HeatPumpLog)
            assert res.rpm == 2400
        else:
            assert isinstance(res, HeatPumpEnergy)
            assert res.total_ein_heating == 1500.5


@pytest.mark.asyncio
async def test_coordinator_rapid_refresh_requests(
    test_hp_info: HeatPumpInfo,
) -> None:
    """Verify rapid burst of async_request_refresh does not deadlock or crash."""
    hass = HomeAssistant()
    entry = ConfigEntry()
    mock_client = MagicMock()
    mock_client.async_get_latest_logs = AsyncMock(
        return_value=HeatPumpLog(heat_pump_id=test_hp_info.heatpump_id, rpm=1800)
    )

    coord = WeheatDataUpdateCoordinator(
        hass, entry, None, test_hp_info, client=mock_client
    )

    for _ in range(25):
        await coord.async_request_refresh()

    assert coord.data is not None
    assert coord.data.rpm == 1800
    assert mock_client.async_get_latest_logs.call_count == 25


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "network_error",
    [
        ClientError("Generic aiohttp connection dropped"),
        ServerDisconnectedError("Remote server disconnected unexpectedly"),
        ClientOSError(10054, "WSAECONNRESET: Connection reset by peer"),
        ClientPayloadError("Payload chunk stream corrupted or prematurely ended"),
    ],
)
async def test_coordinator_simulated_network_drops(
    test_hp_info: HeatPumpInfo, network_error: Exception
) -> None:
    """Verify all low-level network drops and client errors cleanly map to UpdateFailed."""
    hass = HomeAssistant()
    entry = ConfigEntry()
    mock_client = MagicMock()

    # 1. Test telemetry log coordinator
    mock_client.async_get_latest_logs = AsyncMock(side_effect=network_error)
    data_coord = WeheatDataUpdateCoordinator(
        hass, entry, None, test_hp_info, client=mock_client
    )

    with pytest.raises(UpdateFailed) as exc_info:
        await data_coord._async_update_data()
    assert "Error communicating with Weheat API" in str(exc_info.value)
    assert exc_info.value.__cause__ is network_error

    # 2. Test energy coordinator
    mock_client.async_get_energy_total = AsyncMock(side_effect=network_error)
    energy_coord = WeheatEnergyUpdateCoordinator(
        hass, entry, None, test_hp_info, client=mock_client
    )

    with pytest.raises(UpdateFailed) as exc_info:
        await energy_coord._async_update_data()
    assert "Error communicating with Weheat API" in str(exc_info.value)
    assert exc_info.value.__cause__ is network_error


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [401, 403])
async def test_coordinator_auth_expirations_trigger_auth_failed(
    test_hp_info: HeatPumpInfo, status_code: int
) -> None:
    """Verify HTTP 401 and 403 auth errors trigger ConfigEntryAuthFailed on all coordinators."""
    hass = HomeAssistant()
    entry = ConfigEntry()
    mock_client = MagicMock()

    auth_err = WeheatAuthError(
        f"API auth rejected status={status_code}", status_code=status_code
    )

    # 1. Telemetry coordinator
    mock_client.async_get_latest_logs = AsyncMock(side_effect=auth_err)
    data_coord = WeheatDataUpdateCoordinator(
        hass, entry, None, test_hp_info, client=mock_client
    )
    with pytest.raises(ConfigEntryAuthFailed) as exc_info:
        await data_coord._async_update_data()
    assert "Authentication failed" in str(exc_info.value)
    assert exc_info.value.__cause__ is auth_err

    # 2. Energy coordinator
    mock_client.async_get_energy_total = AsyncMock(side_effect=auth_err)
    energy_coord = WeheatEnergyUpdateCoordinator(
        hass, entry, None, test_hp_info, client=mock_client
    )
    with pytest.raises(ConfigEntryAuthFailed) as exc_info:
        await energy_coord._async_update_data()
    assert "Authentication failed" in str(exc_info.value)
    assert exc_info.value.__cause__ is auth_err


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [400, 401, 403])
async def test_coordinator_oauth_session_auth_failure(
    test_hp_info: HeatPumpInfo, status_code: int
) -> None:
    """Verify OAuth2Session token renewal 400/401/403 triggers ConfigEntryAuthFailed."""
    hass = HomeAssistant()
    entry = ConfigEntry()
    mock_session = MagicMock()
    mock_client = MagicMock()

    err = ClientResponseError(
        request_info=MagicMock(),
        history=(),
        status=status_code,
        message=f"HTTP {status_code} Auth Failure",
    )
    mock_session.async_ensure_token_valid = AsyncMock(side_effect=err)

    coord = WeheatDataUpdateCoordinator(
        hass,
        entry,
        session=mock_session,
        heat_pump_info=test_hp_info,
        client=mock_client,
    )
    with pytest.raises(ConfigEntryAuthFailed) as exc_info:
        await coord._async_update_data()
    assert "OAuth token invalid or expired" in str(exc_info.value)
    assert exc_info.value.__cause__ is err


@pytest.mark.asyncio
@pytest.mark.parametrize("status_code", [500, 502, 503, 504])
async def test_coordinator_server_errors_trigger_update_failed(
    test_hp_info: HeatPumpInfo, status_code: int
) -> None:
    """Verify HTTP 500, 502, 503, and 504 server errors trigger UpdateFailed."""
    hass = HomeAssistant()
    entry = ConfigEntry()
    mock_client = MagicMock()

    api_err = WeheatApiError(
        f"Server error response status={status_code}", status_code=status_code
    )

    # 1. Telemetry coordinator
    mock_client.async_get_latest_logs = AsyncMock(side_effect=api_err)
    data_coord = WeheatDataUpdateCoordinator(
        hass, entry, None, test_hp_info, client=mock_client
    )
    with pytest.raises(UpdateFailed) as exc_info:
        await data_coord._async_update_data()
    assert "Error communicating with Weheat API" in str(exc_info.value)
    assert exc_info.value.__cause__ is api_err

    # 2. Energy coordinator
    mock_client.async_get_energy_total = AsyncMock(side_effect=api_err)
    energy_coord = WeheatEnergyUpdateCoordinator(
        hass, entry, None, test_hp_info, client=mock_client
    )
    with pytest.raises(UpdateFailed) as exc_info:
        await energy_coord._async_update_data()
    assert "Error communicating with Weheat API" in str(exc_info.value)
    assert exc_info.value.__cause__ is api_err


@pytest.mark.asyncio
async def test_coordinator_timeout_error_triggers_update_failed(
    test_hp_info: HeatPumpInfo,
) -> None:
    """Verify WeheatTimeoutError triggers UpdateFailed."""
    hass = HomeAssistant()
    entry = ConfigEntry()
    mock_client = MagicMock()
    timeout_err = WeheatTimeoutError("Gateway timeout after 10s")

    mock_client.async_get_latest_logs = AsyncMock(side_effect=timeout_err)
    data_coord = WeheatDataUpdateCoordinator(
        hass, entry, None, test_hp_info, client=mock_client
    )
    with pytest.raises(UpdateFailed) as exc_info:
        await data_coord._async_update_data()
    assert "Error communicating with Weheat API" in str(exc_info.value)
    assert exc_info.value.__cause__ is timeout_err


@pytest.mark.asyncio
async def test_coordinator_missing_heat_pump_info() -> None:
    """Verify UpdateFailed is raised if coordinator has heat_pump_info=None."""
    hass = HomeAssistant()
    entry = ConfigEntry()

    # 1. Data coordinator
    data_coord = WeheatDataUpdateCoordinator(
        hass, entry, session=None, heat_pump_info=None
    )
    with pytest.raises(UpdateFailed) as exc_info:
        await data_coord._async_update_data()
    assert "Heat pump information is missing from coordinator" in str(exc_info.value)

    # 2. Energy coordinator
    energy_coord = WeheatEnergyUpdateCoordinator(
        hass, entry, session=None, heat_pump_info=None
    )
    with pytest.raises(UpdateFailed) as exc_info:
        await energy_coord._async_update_data()
    assert "Heat pump information is missing from coordinator" in str(exc_info.value)


# ===========================================================================
# 2. Polling Interval Dynamics Stress Testing
# ===========================================================================


@pytest.mark.parametrize(
    ("nr_of_heat_pumps", "expected_seconds"),
    [
        (1, 120),  # 1 pump: 120s
        (2, 240),  # 2 pumps: 240s
        (5, 600),  # 5 pumps: 600s
        (10, 1200),  # 10 pumps: 1200s
        (0, 120),  # Clamping: 0 pumps clamped to max(1, 0) == 1 -> 120s
        (-5, 120),  # Clamping: negative clamped to max(1, -5) == 1 -> 120s
    ],
)
def test_polling_interval_scaling_dynamics(
    test_hp_info: HeatPumpInfo, nr_of_heat_pumps: int, expected_seconds: int
) -> None:
    """Verify update_interval dynamically scales with nr_of_heat_pumps and clamps negative/zero values."""
    hass = HomeAssistant()
    entry = ConfigEntry()

    coord = WeheatDataUpdateCoordinator(
        hass,
        entry,
        session=None,
        heat_pump_info=test_hp_info,
        nr_of_heat_pumps=nr_of_heat_pumps,
    )
    assert coord.update_interval == timedelta(seconds=expected_seconds)
    assert coord.update_interval.total_seconds() == LOG_UPDATE_INTERVAL * max(
        1, nr_of_heat_pumps
    )


@pytest.mark.parametrize("nr_of_heat_pumps", [1, 2, 5, 10, 0, -1])
def test_energy_coordinator_fixed_interval(
    test_hp_info: HeatPumpInfo, nr_of_heat_pumps: int
) -> None:
    """Verify energy coordinator interval remains strictly fixed at ENERGY_UPDATE_INTERVAL (1800s)."""
    hass = HomeAssistant()
    entry = ConfigEntry()

    coord = WeheatEnergyUpdateCoordinator(
        hass,
        entry,
        session=None,
        heat_pump_info=test_hp_info,
    )
    assert coord.update_interval == timedelta(seconds=ENERGY_UPDATE_INTERVAL)
    assert coord.update_interval == timedelta(seconds=1800)


# ===========================================================================
# 3. Config Flow Edge Cases Stress Testing
# ===========================================================================


@pytest.mark.asyncio
async def test_config_flow_reauth_matching_account() -> None:
    """Verify reauth completes successfully when user UUID matches existing entry."""
    flow = OAuth2FlowHandler()
    mock_user_id = "00000000-0000-0000-0000-000000000001"
    existing_entry = ConfigEntry(
        entry_id="existing_reauth_entry",
        data={CONF_TOKEN: {CONF_ACCESS_TOKEN: "expired_token_abc"}},
        unique_id=mock_user_id,
        title=ENTRY_TITLE,
    )
    flow.source = SOURCE_REAUTH
    flow._reauth_entry = existing_entry

    new_token_data = {
        CONF_TOKEN: {
            CONF_ACCESS_TOKEN: "new_valid_token_xyz",
        }
    }

    with patch(
        "custom_components.weheat.config_flow.WeheatClient.async_get_user_id",
        new=AsyncMock(return_value=mock_user_id),
    ):
        result = await flow.async_oauth_create_entry(new_token_data)

    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    assert existing_entry.data[CONF_TOKEN][CONF_ACCESS_TOKEN] == "new_valid_token_xyz"


@pytest.mark.asyncio
async def test_config_flow_reauth_mismatched_account() -> None:
    """Verify reauth aborts with 'wrong_account' when authenticated user UUID differs."""
    flow = OAuth2FlowHandler()
    existing_entry = ConfigEntry(
        entry_id="existing_reauth_entry",
        unique_id="00000000-0000-0000-0000-000000000001",
    )
    flow.source = SOURCE_REAUTH
    flow._reauth_entry = existing_entry

    token_data = {
        CONF_TOKEN: {
            CONF_ACCESS_TOKEN: "token_for_different_user",
        }
    }

    with (
        patch(
            "custom_components.weheat.config_flow.WeheatClient.async_get_user_id",
            new=AsyncMock(return_value="00000000-0000-0000-0000-000000000999"),
        ),
        pytest.raises(FlowAbortException) as excinfo,
    ):
        await flow.async_oauth_create_entry(token_data)

    assert excinfo.value.reason == "wrong_account"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure_effect",
    [
        WeheatAuthError("401 Unauthorized token rejected", status_code=401),
        WeheatAuthError("403 Forbidden", status_code=403),
        WeheatApiError("500 Internal error from auth", status_code=500),
        RuntimeError("Unexpected client crash or connection failure"),
        KeyError("Missing expected user key"),
    ],
)
async def test_config_flow_oauth_token_failures(failure_effect: Exception) -> None:
    """Verify all OAuth token validation exceptions cleanly abort with oauth_failed."""
    flow = OAuth2FlowHandler()
    token_data = {
        CONF_TOKEN: {
            CONF_ACCESS_TOKEN: "mock_failing_token",
        }
    }

    with patch(
        "custom_components.weheat.config_flow.WeheatClient.async_get_user_id",
        new=AsyncMock(side_effect=failure_effect),
    ):
        result = await flow.async_oauth_create_entry(token_data)

    assert result["type"] == "abort"
    assert result["reason"] == "oauth_failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("empty_user_id", [None, ""])
async def test_config_flow_empty_user_id_aborts(empty_user_id: str | None) -> None:
    """Verify empty user ID (None or empty string) returns abort(reason='oauth_failed')."""
    flow = OAuth2FlowHandler()
    token_data = {
        CONF_TOKEN: {
            CONF_ACCESS_TOKEN: "mock_token",
        }
    }

    with patch(
        "custom_components.weheat.config_flow.WeheatClient.async_get_user_id",
        new=AsyncMock(return_value=empty_user_id),
    ):
        result = await flow.async_oauth_create_entry(token_data)

    assert result["type"] == "abort"
    assert result["reason"] == "oauth_failed"


@pytest.mark.asyncio
async def test_config_flow_whitespace_user_id_edge_case() -> None:
    """Verify edge case: unstripped whitespace user ID is handled (truthy in Python bool check)."""
    flow = OAuth2FlowHandler()
    token_data = {
        CONF_TOKEN: {
            CONF_ACCESS_TOKEN: "mock_token",
        }
    }

    # Python bool("   ") evaluates to True, so unless explicit strip() is performed,
    # the unique_id is assigned as-is without crashing.
    with patch(
        "custom_components.weheat.config_flow.WeheatClient.async_get_user_id",
        new=AsyncMock(return_value="   "),
    ):
        result = await flow.async_oauth_create_entry(token_data)

    assert result["type"] == "create_entry"
    assert flow.unique_id == "   "


@pytest.mark.asyncio
async def test_config_flow_duplicate_entry_aborts() -> None:
    """Verify normal setup flow aborts with 'already_configured' if user ID is known."""
    flow = OAuth2FlowHandler()
    configured_uuid = "00000000-0000-0000-0000-000000000001"
    flow.configured_unique_ids.add(configured_uuid)

    token_data = {
        CONF_TOKEN: {
            CONF_ACCESS_TOKEN: "mock_token",
        }
    }

    with (
        patch(
            "custom_components.weheat.config_flow.WeheatClient.async_get_user_id",
            new=AsyncMock(return_value=configured_uuid),
        ),
        pytest.raises(FlowAbortException) as excinfo,
    ):
        await flow.async_oauth_create_entry(token_data)

    assert excinfo.value.reason == "already_configured"


@pytest.mark.asyncio
async def test_integration_setup_zero_heat_pumps() -> None:
    """Verify async_setup_entry gracefully completes when API returns zero heat pumps."""
    hass = HomeAssistant()
    entry = ConfigEntry(
        entry_id="test_zero_pumps_entry",
        data={CONF_TOKEN: {CONF_ACCESS_TOKEN: "valid_token_zero_pumps"}},
        title="Weheat cloud",
    )

    with (
        patch(
            "custom_components.weheat.OAuth2Session.async_ensure_token_valid",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.weheat.WeheatClient.async_get_heat_pumps",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await async_setup_entry(hass, entry)

    assert result is True
    # Verify runtime_data is initialized as empty list
    assert entry.runtime_data == []
    # Verify platforms are forwarded cleanly without error
    assert len(hass.config_entries.forwarded_setups) == 1
    assert hass.config_entries.forwarded_setups[0][1] == PLATFORMS

    # Verify unload also succeeds gracefully
    unload_res = await async_unload_entry(hass, entry)
    assert unload_res is True


# ===========================================================================
# 4. Diagnostics Sensitive Data Leakage & Redaction
# ===========================================================================


@pytest.mark.asyncio
async def test_diagnostics_redacts_all_sensitive_keys_flat(
    test_hp_info: HeatPumpInfo,
) -> None:
    """Adversarially inject every single sensitive key into info, logs, and energy; ensure 100% redacted."""
    # List of all sensitive keys that must be redacted
    all_sensitive_keys = [
        "heat_pump_id",
        "heatpump_id",
        "heatPumpId",
        "uuid",
        "sn",
        "serial_number",
        "control_board_id",
        "controlBoardId",
        "installation_id",
        "installationId",
        "access_token",
        "refresh_token",
    ]

    # Create logs dictionary poisoned with every sensitive key
    poisoned_logs_raw = {"rpm": 2500, "tWaterIn": 42.0}
    for key in all_sensitive_keys:
        poisoned_logs_raw[key] = f"MALICIOUS_LOG_SECRET_{key.upper()}"

    # Create energy dictionary poisoned with every sensitive key
    poisoned_energy_raw = {"totalEInHeating": 1100.0}
    for key in all_sensitive_keys:
        poisoned_energy_raw[key] = f"MALICIOUS_ENERGY_SECRET_{key.upper()}"

    mock_log = HeatPumpLog(
        heat_pump_id=test_hp_info.heatpump_id,
        rpm=2500,
        raw=poisoned_logs_raw,
    )
    mock_energy = HeatPumpEnergy(
        heatpump_id=test_hp_info.heatpump_id,
        total_ein_heating=1100.0,
        raw=poisoned_energy_raw,
    )

    class CoordShim:
        def __init__(self, data: Any) -> None:
            self.data = data

    class DataShim:
        heat_pump_info = test_hp_info
        data_coordinator = CoordShim(mock_log)
        energy_coordinator = CoordShim(mock_energy)

    class EntryShim:
        runtime_data = [DataShim()]

    diagnostics = await async_get_config_entry_diagnostics(None, EntryShim())  # type: ignore[arg-type]

    assert "heat_pumps" in diagnostics
    assert len(diagnostics["heat_pumps"]) == 1
    hp_diag = diagnostics["heat_pumps"][0]

    info = hp_diag["info"]
    logs = hp_diag["logs"]
    energy = hp_diag["energy"]

    # Verify that every injected sensitive key in logs was redacted
    for key in all_sensitive_keys:
        assert logs[key] == "**REDACTED**", (
            f"Sensitive key '{key}' was NOT redacted in logs!"
        )
        assert energy[key] == "**REDACTED**", (
            f"Sensitive key '{key}' was NOT redacted in energy!"
        )

    # Verify info redactions
    for key in ("heatpump_id", "serial_number", "control_board_id", "installation_id"):
        assert info[key] == "**REDACTED**", f"Info key '{key}' was NOT redacted!"

    # Verify non-sensitive telemetry was NOT corrupted or redacted
    assert logs["rpm"] == 2500
    assert logs["tWaterIn"] == 42.0
    assert energy["totalEInHeating"] == 1100.0
    assert info["name"] == test_hp_info.name
    assert info["model"] == test_hp_info.model
    assert info["has_dhw"] is True


@pytest.mark.asyncio
async def test_diagnostics_deeply_nested_and_list_structures(
    test_hp_info: HeatPumpInfo,
) -> None:
    """Verify recursive redaction inside nested dictionaries, lists of objects, and mixed payloads."""
    nested_logs_raw = {
        "status": "active",
        "hardware_tree": {
            "board": {
                "control_board_id": "SECRET_BOARD_UUID_12345",
                "auth_cache": {
                    "access_token": "BEARER_SECRET_TOKEN_XYZ",
                    "refresh_token": "REFRESH_SECRET_TOKEN_ABC",
                },
            },
            "modules": [
                {
                    "name": "inverter",
                    "serial_number": "SN_INVERTER_9999",
                    "uuid": "UUID_INVERTER_8888",
                },
                {
                    "name": "pump",
                    "sn": "SN_PUMP_7777",
                    "heatPumpId": "PUMP_ID_6666",
                },
            ],
        },
        "deep_installation": {
            "installationId": "INSTALLATION_SECRET_5555",
            "installation_id": "INSTALLATION_SECRET_4444",
        },
    }

    mock_log = HeatPumpLog(
        heat_pump_id=test_hp_info.heatpump_id,
        rpm=2000,
        raw=nested_logs_raw,
    )
    mock_energy = HeatPumpEnergy(
        heatpump_id=test_hp_info.heatpump_id,
        raw={"safe_val": 123},
    )

    class CoordShim:
        def __init__(self, data: Any) -> None:
            self.data = data

    class DataShim:
        heat_pump_info = test_hp_info
        data_coordinator = CoordShim(mock_log)
        energy_coordinator = CoordShim(mock_energy)

    class EntryShim:
        runtime_data = [DataShim()]

    diagnostics = await async_get_config_entry_diagnostics(None, EntryShim())  # type: ignore[arg-type]
    logs = diagnostics["heat_pumps"][0]["logs"]

    # Verify deep nested dict redactions
    board = logs["hardware_tree"]["board"]
    assert board["control_board_id"] == "**REDACTED**"
    assert board["auth_cache"]["access_token"] == "**REDACTED**"
    assert board["auth_cache"]["refresh_token"] == "**REDACTED**"

    # Verify list of dicts redactions
    modules = logs["hardware_tree"]["modules"]
    assert modules[0]["serial_number"] == "**REDACTED**"
    assert modules[0]["uuid"] == "**REDACTED**"
    assert modules[0]["name"] == "inverter"

    assert modules[1]["sn"] == "**REDACTED**"
    assert modules[1]["heatPumpId"] == "**REDACTED**"
    assert modules[1]["name"] == "pump"

    # Verify deep installation keys
    assert logs["deep_installation"]["installationId"] == "**REDACTED**"
    assert logs["deep_installation"]["installation_id"] == "**REDACTED**"


@pytest.mark.asyncio
async def test_diagnostics_empty_and_corrupt_data_resilience(
    test_hp_info: HeatPumpInfo,
) -> None:
    """Verify diagnostics handles None coordinator data, empty runtime_data, and missing raw dict."""

    # 1. Empty runtime_data
    class EmptyEntry:
        runtime_data = []

    diag_empty = await async_get_config_entry_diagnostics(None, EmptyEntry())  # type: ignore[arg-type]
    assert diag_empty == {"heat_pumps": []}

    # 2. Coordinator data is None
    class CoordNoneShim:
        data = None

    class DataShimNone:
        heat_pump_info = test_hp_info
        data_coordinator = CoordNoneShim()
        energy_coordinator = CoordNoneShim()

    class EntryNone:
        runtime_data = [DataShimNone()]

    diag_none = await async_get_config_entry_diagnostics(None, EntryNone())  # type: ignore[arg-type]
    assert len(diag_none["heat_pumps"]) == 1
    assert diag_none["heat_pumps"][0]["logs"] == {}
    assert diag_none["heat_pumps"][0]["energy"] == {}

    # 3. Coordinator data is an object without 'raw'
    class CoordNoRawShim:
        class ObjectWithoutRaw:
            foo = "bar"

        data = ObjectWithoutRaw()

    class DataShimNoRaw:
        heat_pump_info = test_hp_info
        data_coordinator = CoordNoRawShim()
        energy_coordinator = CoordNoRawShim()

    class EntryNoRaw:
        runtime_data = [DataShimNoRaw()]

    diag_no_raw = await async_get_config_entry_diagnostics(None, EntryNoRaw())  # type: ignore[arg-type]
    assert len(diag_no_raw["heat_pumps"]) == 1
    assert diag_no_raw["heat_pumps"][0]["logs"] == {}
    assert diag_no_raw["heat_pumps"][0]["energy"] == {}


@pytest.mark.asyncio
async def test_diagnostics_fallback_for_non_dataclass_info() -> None:
    """Verify vars() fallback in diagnostics when heat_pump_info is a non-dataclass custom object."""

    class PlainCustomHeatPumpInfo:
        def __init__(self) -> None:
            self.heatpump_id = "CUSTOM_UUID_1111"
            self.serial_number = "CUSTOM_SN_2222"
            self.control_board_id = "CUSTOM_CB_3333"
            self.name = "Custom Heat Pump"
            self.model = 0

    custom_info = PlainCustomHeatPumpInfo()
    assert not hasattr(custom_info, "__dataclass_fields__")

    class CoordShim:
        data = None

    class DataShim:
        heat_pump_info = custom_info
        data_coordinator = CoordShim()
        energy_coordinator = CoordShim()

    class EntryShim:
        runtime_data = [DataShim()]

    diag = await async_get_config_entry_diagnostics(None, EntryShim())  # type: ignore[arg-type]
    info = diag["heat_pumps"][0]["info"]

    assert info["heatpump_id"] == "**REDACTED**"
    assert info["serial_number"] == "**REDACTED**"
    assert info["control_board_id"] == "**REDACTED**"
    assert info["name"] == "Custom Heat Pump"
    assert info["model"] == 0
