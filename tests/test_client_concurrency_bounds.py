"""Adversarial concurrency, thread safety, and validation bounds test suite.

Milestone 2 Challenger 2 verification.
"""

from __future__ import annotations

import asyncio
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import aiohttp
import pytest
from aioresponses import aioresponses

from custom_components.weheat.client import (
    WeheatClient,
)
from tests.conftest import (
    API_BASE_URL,
    PORTAL_BASE_URL,
    TEST_ACCESS_TOKEN,
    TEST_CB_ID_BLACKBIRD,
    TEST_HP_UUID_BLACKBIRD,
    TEST_HP_UUID_FLINT,
    TEST_HP_UUID_SPARROW,
    TEST_SETTINGS_REQUEST_ID,
    load_fixture,
)


@pytest.fixture
def client(client_session: aiohttp.ClientSession) -> WeheatClient:
    """Create a WeheatClient instance for concurrency and bounds tests."""
    return WeheatClient(
        session=client_session,
        base_url=API_BASE_URL,
        portal_base_url=PORTAL_BASE_URL,
    )


# ===========================================================================
# 1. Concurrency & Race Conditions: Shared Session Stress Testing
# ===========================================================================


async def test_rapid_concurrent_requests_shared_session(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
) -> None:
    """Adversarial stress: 100+ concurrent mixed telemetry and mutation requests sharing one session."""
    mock_aioresponse.get(
        f"{API_BASE_URL}/api/v1/users/me",
        payload=load_fixture("user_me.json"),
        repeat=True,
    )
    mock_aioresponse.get(
        f"{API_BASE_URL}/api/v1/heat-pumps?limit=1000&page=1&state=3",
        payload=load_fixture("heat_pumps.json"),
        repeat=True,
    )
    mock_aioresponse.get(
        f"{API_BASE_URL}/api/v1/heat-pumps/{TEST_HP_UUID_BLACKBIRD}/logs/latest",
        payload=load_fixture("latest_logs.json"),
        repeat=True,
    )
    mock_aioresponse.get(
        f"{API_BASE_URL}/api/v1/energy-logs/{TEST_HP_UUID_BLACKBIRD}/total",
        payload=load_fixture("energy_total.json"),
        repeat=True,
    )
    mock_aioresponse.patch(
        f"{PORTAL_BASE_URL}/api/v1/control-boards/{TEST_CB_ID_BLACKBIRD}/settings",
        payload=load_fixture("settings_update_success.json"),
        repeat=True,
    )

    tasks = []
    for _ in range(25):
        tasks.append(client.async_get_user_id(TEST_ACCESS_TOKEN))
        tasks.append(client.async_get_heat_pumps(TEST_ACCESS_TOKEN))
        tasks.append(
            client.async_get_latest_logs(TEST_HP_UUID_BLACKBIRD, TEST_ACCESS_TOKEN)
        )
        tasks.append(
            client.async_get_energy_total(TEST_HP_UUID_BLACKBIRD, TEST_ACCESS_TOKEN)
        )
        tasks.append(
            client.async_set_performance_mode(
                TEST_CB_ID_BLACKBIRD, TEST_ACCESS_TOKEN, 2
            )
        )

    results = await asyncio.gather(*tasks, return_exceptions=True)
    exceptions = [r for r in results if isinstance(r, Exception)]
    assert len(exceptions) == 0, (
        f"Concurrent tasks failed with exceptions: {exceptions}"
    )
    assert len(results) == 125


def test_multithread_safe_dispatch() -> None:
    """Verify WeheatClient invoked across thread pool using run_coroutine_threadsafe."""
    loop = asyncio.new_event_loop()

    def run_loop() -> None:
        asyncio.set_event_loop(loop)
        loop.run_forever()

    t = threading.Thread(target=run_loop, daemon=True)
    t.start()

    async def init_client() -> tuple[WeheatClient, aiohttp.ClientSession]:
        session = aiohttp.ClientSession()
        return WeheatClient(session, API_BASE_URL, PORTAL_BASE_URL), session

    future = asyncio.run_coroutine_threadsafe(init_client(), loop)
    client, session = future.result()

    try:
        with aioresponses() as m:
            m.get(
                f"{API_BASE_URL}/api/v1/users/me",
                payload=load_fixture("user_me.json"),
                repeat=True,
            )

            def worker_call(_idx: int) -> str:
                fut = asyncio.run_coroutine_threadsafe(
                    client.async_get_user_id(TEST_ACCESS_TOKEN), loop
                )
                return fut.result()

            with ThreadPoolExecutor(max_workers=8) as executor:
                results = list(executor.map(worker_call, range(40)))

            assert len(results) == 40
            assert all(r == "00000000-0000-0000-0000-000000000001" for r in results)
    finally:

        async def close_session() -> None:
            await session.close()

        asyncio.run_coroutine_threadsafe(close_session(), loop).result()
        loop.call_soon_threadsafe(loop.stop)
        t.join(timeout=2.0)


async def test_control_board_cache_concurrency_and_consistency(
    client: WeheatClient,
) -> None:
    """Verify concurrent async_get_control_board_id calls resolve consistently and do not corrupt cache."""
    doc = load_fixture("installations_fully_expanded.json")
    items = doc["data"]
    network_call_count = 0

    async def mock_topology(token: str | None = None) -> list[dict[str, Any]]:
        nonlocal network_call_count
        network_call_count += 1
        await asyncio.sleep(0.02)  # Simulate network latency
        for item in items:
            cb_id = item.get("controlBoardId")
            hp_id = item.get("id") or item.get("heatPumpId")
            inst_id = item.get("installationId")
            if hp_id:
                client._control_board_cache[str(hp_id)] = str(cb_id)
            if inst_id:
                client._control_board_cache[str(inst_id)] = str(cb_id)
            client._control_board_cache[str(cb_id)] = str(cb_id)
        return items

    client.async_get_installation_topology = mock_topology

    # Request 3 different heat pumps and multiple duplicates concurrently
    tasks = [
        client.async_get_control_board_id(TEST_HP_UUID_BLACKBIRD),
        client.async_get_control_board_id(TEST_HP_UUID_SPARROW),
        client.async_get_control_board_id(TEST_HP_UUID_FLINT),
        client.async_get_control_board_id(TEST_HP_UUID_BLACKBIRD),
        client.async_get_control_board_id(TEST_HP_UUID_SPARROW),
        client.async_get_control_board_id(TEST_HP_UUID_FLINT),
    ]

    results = await asyncio.gather(*tasks)

    # Verify all resolved IDs are accurate and uncorrupted
    assert results[0] == "cb-00000000-0000-0000-0000-000000000001"
    assert results[1] == "cb-00000000-0000-0000-0000-000000000002"
    assert results[2] == "cb-00000000-0000-0000-0000-000000000003"
    assert results[3] == "cb-00000000-0000-0000-0000-000000000001"
    assert results[4] == "cb-00000000-0000-0000-0000-000000000002"
    assert results[5] == "cb-00000000-0000-0000-0000-000000000003"

    # Once warm, subsequent calls perform 0 network requests
    pre_count = network_call_count
    cached_id = await client.async_get_control_board_id(TEST_HP_UUID_BLACKBIRD)
    assert cached_id == "cb-00000000-0000-0000-0000-000000000001"
    assert network_call_count == pre_count


async def test_control_board_already_prefixed_bypass(client: WeheatClient) -> None:
    """Verify target_id starting with 'cb-' bypasses topology lookup."""
    cb_target = "cb-test-direct-id"
    resolved = await client.async_get_control_board_id(cb_target)
    assert resolved == cb_target
    assert client._control_board_cache[cb_target] == cb_target


# ===========================================================================
# 2. Input Validation Bounds: Rejection with ValueError BEFORE Network Calls
# ===========================================================================


@pytest.mark.parametrize(
    "invalid_preset",
    [-100, -1, 5, 6, 10, 100, 1.5, "0", None, [0], {"preset": 1}],
)
async def test_performance_mode_preset_out_of_bounds(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
    invalid_preset: Any,
) -> None:
    """Verify PerformanceModePreset < 0 or > 4 and non-ints raise ValueError without network activity."""
    with pytest.raises(ValueError, match="preset must be between 0 and 4"):
        await client.async_set_performance_mode(
            control_board_id=TEST_CB_ID_BLACKBIRD,
            token=TEST_ACCESS_TOKEN,
            preset=invalid_preset,
        )
    # Confirm 0 network requests were initiated
    assert len(mock_aioresponse.requests) == 0


@pytest.mark.parametrize(
    "invalid_preset",
    [-100, -1, 8, 9, 10, 100, 2.5, "1", None, [1], {"preset": 1}],
)
async def test_silent_hours_preset_out_of_bounds(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
    invalid_preset: Any,
) -> None:
    """Verify SilentHoursScheduledPreset < 0 or > 7 and non-ints raise ValueError without network activity."""
    with pytest.raises(ValueError, match="preset must be between 0 and 7"):
        await client.async_set_silent_hours_preset(
            control_board_id=TEST_CB_ID_BLACKBIRD,
            token=TEST_ACCESS_TOKEN,
            preset=invalid_preset,
        )
    assert len(mock_aioresponse.requests) == 0


@pytest.mark.parametrize(
    "invalid_preset",
    [-100, -1, 8, 9, 10, 100, 2.5, "1", None, [1]],
)
async def test_silent_hours_schedule_preset_out_of_bounds(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
    invalid_preset: Any,
) -> None:
    """Verify async_set_silent_hours_schedule validates preset before network dispatch."""
    with pytest.raises(ValueError, match="preset must be between 0 and 7"):
        await client.async_set_silent_hours_schedule(
            control_board_id=TEST_CB_ID_BLACKBIRD,
            token=TEST_ACCESS_TOKEN,
            preset=invalid_preset,
        )
    assert len(mock_aioresponse.requests) == 0


@pytest.mark.parametrize(
    "invalid_temp",
    [
        -50.0,
        0.0,
        39.0,
        39.9,
        39.99999999999999,
        60.00000000000001,
        60.1,
        75.0,
        100.0,
        "50",
        None,
        [50],
        float("nan"),
        float("inf"),
        float("-inf"),
    ],
)
async def test_dhw_target_temperature_out_of_bounds(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
    invalid_temp: Any,
) -> None:
    """Verify DHW target temperature < 40 or > 60 or non-numeric raises ValueError without network activity."""
    with pytest.raises(ValueError, match="temperature must be between 40 and 60"):
        await client.async_set_dhw_target_temperature(
            control_board_id=TEST_CB_ID_BLACKBIRD,
            token=TEST_ACCESS_TOKEN,
            temperature=invalid_temp,
        )
    assert len(mock_aioresponse.requests) == 0


@pytest.mark.parametrize("valid_temp", [40.0, 40, 50.5, 60.0, 60])
async def test_dhw_target_temperature_valid_boundaries(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
    valid_temp: float | int,
) -> None:
    """Verify DHW target temperature exactly at 40.0 and 60.0 boundaries succeeds."""
    url = f"{PORTAL_BASE_URL}/api/v1/control-boards/{TEST_CB_ID_BLACKBIRD}/settings"
    mock_aioresponse.patch(
        url, payload=load_fixture("settings_update_success.json"), status=200
    )

    req_id = await client.async_set_dhw_target_temperature(
        control_board_id=TEST_CB_ID_BLACKBIRD,
        token=TEST_ACCESS_TOKEN,
        temperature=valid_temp,
    )
    assert req_id == TEST_SETTINGS_REQUEST_ID


# ===========================================================================
# 3. Malformed Day Schedule Validation
# ===========================================================================


@pytest.mark.parametrize(
    ("schedule", "err_pattern"),
    [
        ({"SilentHoursMondayStart": 24}, "between 0 and 23"),
        ({"SilentHoursMondayStart": 25}, "between 0 and 23"),
        ({"SilentHoursMondayStart": 99}, "between 0 and 23"),
        ({"SilentHoursMondayStart": -1}, "between 0 and 23"),
        ({"SilentHoursMondayStop": 24}, "between 0 and 23"),
        ({"SilentMinutesMondayStart": 60}, "between 0 and 59"),
        ({"SilentMinutesMondayStart": 61}, "between 0 and 59"),
        ({"SilentMinutesMondayStart": 99}, "between 0 and 59"),
        ({"SilentMinutesMondayStart": -1}, "between 0 and 59"),
        ({"SilentMinutesMondayStop": 60}, "between 0 and 59"),
        ({"SilentHoursFundayStart": 10}, "Unknown schedule key"),
        ({"SilentHoursMonday": 10}, "Unknown schedule key"),
        ({"RandomKey": 10}, "Unknown schedule key"),
        ({"SilentHoursMondayStart": 12.5}, "must be an integer"),
        ({"SilentHoursMondayStart": "12"}, "must be an integer"),
        ({"SilentHoursMondayStart": None}, "must be an integer"),
    ],
)
async def test_async_set_silent_schedule_malformed(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
    schedule: dict[str, Any],
    err_pattern: str,
) -> None:
    """Verify malformed day schedule hours (>23), minutes (>59), and invalid keys raise ValueError."""
    with pytest.raises(ValueError, match=err_pattern):
        await client.async_set_silent_schedule(
            control_board_id=TEST_CB_ID_BLACKBIRD,
            token=TEST_ACCESS_TOKEN,
            schedule=schedule,
        )
    assert len(mock_aioresponse.requests) == 0


async def test_async_set_silent_schedule_empty_rejected(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
) -> None:
    """Verify empty dictionary in async_set_silent_schedule raises ValueError."""
    with pytest.raises(ValueError, match="schedule dictionary must not be empty"):
        await client.async_set_silent_schedule(
            control_board_id=TEST_CB_ID_BLACKBIRD,
            token=TEST_ACCESS_TOKEN,
            schedule={},
        )
    assert len(mock_aioresponse.requests) == 0


@pytest.mark.parametrize(
    ("schedule", "err_pattern"),
    [
        ({"SilentHoursMondayStart": 24}, "Invalid hour"),
        ({"SilentMinutesMondayStart": 60}, "Invalid minute"),
        ({"InvalidKey": 10}, "Unknown schedule key"),
        ({"SilentHoursMondayStart": "10"}, "must be integer"),
    ],
)
async def test_async_set_silent_hours_schedule_malformed(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
    schedule: dict[str, Any],
    err_pattern: str,
) -> None:
    """Verify async_set_silent_hours_schedule rejects malformed schedule components before network call."""
    with pytest.raises(ValueError, match=err_pattern):
        await client.async_set_silent_hours_schedule(
            control_board_id=TEST_CB_ID_BLACKBIRD,
            token=TEST_ACCESS_TOKEN,
            preset=1,
            schedule=schedule,
        )
    assert len(mock_aioresponse.requests) == 0


# ===========================================================================
# 4. Target ID & Boost Validation Bounds
# ===========================================================================


@pytest.mark.parametrize("empty_id", ["", "   ", None])
async def test_control_board_id_empty_rejection(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
    empty_id: Any,
) -> None:
    """Verify empty target ID raises ValueError before network dispatch."""
    with pytest.raises(ValueError, match="target_id must not be empty"):
        await client.async_get_control_board_id(empty_id)

    with pytest.raises(ValueError, match="control_board_id must not be empty"):
        await client.async_set_performance_mode(empty_id, preset=0)

    with pytest.raises(ValueError, match="control_board_id must not be empty"):
        await client.async_set_silent_hours_preset(empty_id, preset=0)

    with pytest.raises(ValueError, match="control_board_id must not be empty"):
        await client.async_set_dhw_target_temperature(empty_id, temperature=50.0)

    assert len(mock_aioresponse.requests) == 0


@pytest.mark.parametrize("invalid_duration", [-1, 0, 15, 45, 180, 240])
async def test_dhw_boost_invalid_duration(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
    invalid_duration: int,
) -> None:
    """Verify DHW boost rejects invalid durations not in (30, 60, 90, 120)."""
    with pytest.raises(ValueError, match="Invalid boost duration"):
        await client.async_trigger_dhw_boost(
            heat_pump_uuid=TEST_HP_UUID_BLACKBIRD,
            duration_minutes=invalid_duration,
        )
    assert len(mock_aioresponse.requests) == 0


@pytest.mark.parametrize("invalid_temp", [39.9, 65.1, 100.0, -10.0])
async def test_dhw_boost_invalid_temperature(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
    invalid_temp: float,
) -> None:
    """Verify DHW boost rejects temperatures outside 40.0..65.0 °C."""
    with pytest.raises(ValueError, match="Invalid boost temperature"):
        await client.async_trigger_dhw_boost(
            heat_pump_uuid=TEST_HP_UUID_BLACKBIRD,
            target_temperature=invalid_temp,
        )
    assert len(mock_aioresponse.requests) == 0
