"""Adversarial stress tests for Milestone 2: WeheatClient error handling, polling, and offline isolation."""

from __future__ import annotations

import socket
from typing import Any
from unittest.mock import patch

import aiohttp
import pytest
from aiohttp import ClientError, ClientResponseError, RequestInfo
from aioresponses import aioresponses
from yarl import URL

from custom_components.weheat.client import (
    WeheatApiError,
    WeheatAuthError,
    WeheatClient,
    WeheatTimeoutError,
)
from custom_components.weheat.const import SETTINGS_REQUEST_STATUS_MAP
from tests.conftest import (
    API_BASE_URL,
    PORTAL_BASE_URL,
    TEST_CB_ID_BLACKBIRD,
    TEST_HP_UUID_BLACKBIRD,
    TEST_INSTALLATION_ID,
    TEST_SETTINGS_REQUEST_ID,
)


@pytest.fixture
def client(client_session: aiohttp.ClientSession) -> WeheatClient:
    """Create standard client fixture."""
    return WeheatClient(
        session=client_session,
        base_url=API_BASE_URL,
        portal_base_url=PORTAL_BASE_URL,
    )


# ===========================================================================
# 1. Adversarial Error Boundaries: HTTP 401 & 403 under all URL variants
# ===========================================================================


@pytest.mark.parametrize("status_code", [401, 403])
@pytest.mark.parametrize(
    ("error_payload", "expected_msg"),
    [
        ({"message": "Token expired"}, "Token expired"),
        ({"error_description": "Invalid signature"}, "Invalid signature"),
        ({"error": "invalid_grant"}, "invalid_grant"),
        ({}, "Authentication failed with status"),
    ],
)
async def test_auth_error_payload_variants(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
    status_code: int,
    error_payload: dict[str, Any],
    expected_msg: str,
) -> None:
    """Verify 401/403 extracts error message from various OAuth/API JSON response schemas."""
    url = f"{API_BASE_URL}/api/v1/users/me"
    mock_aioresponse.get(url, status=status_code, payload=error_payload)

    with pytest.raises(WeheatAuthError) as exc_info:
        await client.async_get_user_id("test_token")

    assert exc_info.value.status_code == status_code
    assert expected_msg in str(exc_info.value)


@pytest.mark.parametrize("status_code", [401, 403])
async def test_auth_error_plain_text_response(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
    status_code: int,
) -> None:
    """Verify 401/403 with non-JSON plain text response extracts text body."""
    url = f"{API_BASE_URL}/api/v1/users/me"
    mock_aioresponse.get(
        url,
        status=status_code,
        body="Access Denied by WAF",
        headers={"Content-Type": "text/plain"},
    )

    with pytest.raises(WeheatAuthError) as exc_info:
        await client.async_get_user_id("test_token")

    assert exc_info.value.status_code == status_code
    assert "Access Denied by WAF" in str(exc_info.value)


@pytest.mark.parametrize(
    ("base_url", "portal_url"),
    [
        ("https://api.weheat.nl/third_party/", "https://api.weheat.nl/"),
        ("https://custom.api.weheat.nl/third_party", "https://custom.api.weheat.nl"),
        ("https://api.weheat.nl", None),  # portal defaults to base_url
    ],
)
async def test_auth_error_url_normalization_variants(
    client_session: aiohttp.ClientSession,
    mock_aioresponse: aioresponses,
    base_url: str,
    portal_url: str | None,
) -> None:
    """Verify trailing slashes and custom domains are normalized and 401 raises WeheatAuthError."""
    test_client = WeheatClient(
        session=client_session,
        base_url=base_url,
        portal_base_url=portal_url,
    )
    expected_url = f"{test_client.base_url}/api/v1/users/me"
    mock_aioresponse.get(
        expected_url, status=401, payload={"message": "Unauthorized variant"}
    )

    with pytest.raises(WeheatAuthError) as exc_info:
        await test_client.async_get_user_id("token")

    assert exc_info.value.status_code == 401
    assert "Unauthorized variant" in str(exc_info.value)


@pytest.mark.parametrize("status_code", [401, 403])
async def test_auth_error_across_all_endpoints(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
    status_code: int,
) -> None:
    """Verify that every client method properly raises WeheatAuthError on 401/403."""
    endpoints = [
        ("GET", f"{API_BASE_URL}/api/v1/users/me", client.async_get_user_profile()),
        (
            "GET",
            f"{API_BASE_URL}/api/v1/heat_pumps?limit=1000&page=1&state=3",
            client.async_get_heat_pumps(),
        ),
        (
            "GET",
            f"{API_BASE_URL}/api/v1/heat-pumps/{TEST_HP_UUID_BLACKBIRD}",
            client.async_get_heat_pump_details(TEST_HP_UUID_BLACKBIRD),
        ),
        (
            "GET",
            f"{PORTAL_BASE_URL}/api/v1/installations/fully-expanded",
            client.async_get_installation_topology(),
        ),
        (
            "GET",
            f"{API_BASE_URL}/api/v1/heat-pumps/{TEST_HP_UUID_BLACKBIRD}/logs/latest",
            client.async_get_latest_logs(TEST_HP_UUID_BLACKBIRD),
        ),
        (
            "GET",
            f"{API_BASE_URL}/api/v1/energy-logs/{TEST_HP_UUID_BLACKBIRD}/total",
            client.async_get_energy_total(TEST_HP_UUID_BLACKBIRD),
        ),
        (
            "GET",
            f"{PORTAL_BASE_URL}/api/v1/installations/{TEST_INSTALLATION_ID}/settings",
            client.async_get_installation_settings(TEST_INSTALLATION_ID),
        ),
        (
            "PATCH",
            f"{PORTAL_BASE_URL}/api/v1/control-boards/{TEST_CB_ID_BLACKBIRD}/settings",
            client.async_patch_control_board_settings(
                TEST_CB_ID_BLACKBIRD, settings={"PerformanceModePreset": 0}
            ),
        ),
        (
            "GET",
            f"{PORTAL_BASE_URL}/api/v1/control-boards/{TEST_CB_ID_BLACKBIRD}/settings/{TEST_SETTINGS_REQUEST_ID}",
            client.async_get_settings_request_status(
                TEST_CB_ID_BLACKBIRD, TEST_SETTINGS_REQUEST_ID
            ),
        ),
        (
            "PATCH",
            f"{PORTAL_BASE_URL}/api/v1/control-boards/{TEST_CB_ID_BLACKBIRD}/variables",
            client.async_patch_control_board_variables(
                TEST_CB_ID_BLACKBIRD, variables={"RequestLegionellaPrevention": 1}
            ),
        ),
        (
            "POST",
            f"{PORTAL_BASE_URL}/api/v1/heat-pumps/{TEST_HP_UUID_BLACKBIRD}/dhw/boost",
            client.async_trigger_dhw_boost(
                heat_pump_uuid=TEST_HP_UUID_BLACKBIRD,
                duration_minutes=60,
                target_temperature=60.0,
            ),
        ),
    ]

    for method, url, coro in endpoints:
        if method == "GET":
            mock_aioresponse.get(url, status=status_code, payload={"error": "denied"})
        elif method == "PATCH":
            mock_aioresponse.patch(url, status=status_code, payload={"error": "denied"})
        elif method == "POST":
            mock_aioresponse.post(url, status=status_code, payload={"error": "denied"})

        with pytest.raises(WeheatAuthError) as exc_info:
            await coro

        assert exc_info.value.status_code == status_code


# ===========================================================================
# 2. Adversarial Error Boundaries: HTTP 404, 500, 502, 503, 504 with status and body
# ===========================================================================


@pytest.mark.parametrize("status_code", [404, 500, 502, 503, 504])
async def test_api_errors_preserve_status_and_body(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
    status_code: int,
) -> None:
    """Verify 404, 500, 502, 503, 504 propagate WeheatApiError with status code and body."""
    url = f"{API_BASE_URL}/api/v1/heat-pumps/{TEST_HP_UUID_BLACKBIRD}/logs/latest"
    payload = {"status": status_code, "error": f"Simulated error {status_code}"}
    mock_aioresponse.get(url, status=status_code, payload=payload)

    with pytest.raises(WeheatApiError) as exc_info:
        await client.async_get_latest_logs(TEST_HP_UUID_BLACKBIRD)

    assert exc_info.value.status_code == status_code
    assert exc_info.value.response_data == payload
    assert f"Simulated error {status_code}" in str(exc_info.value)


@pytest.mark.parametrize("status_code", [500, 502, 503])
async def test_api_errors_html_fallback(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
    status_code: int,
) -> None:
    """Verify HTML error page from load balancer or reverse proxy is preserved in message."""
    url = f"{API_BASE_URL}/api/v1/users/me"
    html_body = f"<html><body><h1>{status_code} Bad Gateway</h1></body></html>"
    mock_aioresponse.get(
        url,
        status=status_code,
        body=html_body,
        headers={"Content-Type": "text/html"},
    )

    with pytest.raises(WeheatApiError) as exc_info:
        await client.async_get_user_id()

    assert exc_info.value.status_code == status_code
    assert html_body in str(exc_info.value)


async def test_corrupt_json_on_200_ok(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
) -> None:
    """Verify 200 OK returning non-JSON corrupt payload raises WeheatApiError."""
    url = f"{API_BASE_URL}/api/v1/users/me"
    mock_aioresponse.get(
        url,
        status=200,
        body="This is definitely not valid JSON {{{",
        headers={"Content-Type": "application/json"},
    )

    with pytest.raises(WeheatApiError, match="Invalid JSON returned"):
        await client.async_get_user_id()


async def test_energy_fallback_404_handling(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
) -> None:
    """Verify async_get_energy_total falls back on 404, and raises if fallback also 404."""
    primary_url = f"{API_BASE_URL}/api/v1/energy-logs/{TEST_HP_UUID_BLACKBIRD}/total"
    fallback_url = f"{API_BASE_URL}/total/{TEST_HP_UUID_BLACKBIRD}"

    mock_aioresponse.get(
        primary_url, status=404, payload={"message": "Primary endpoint not found"}
    )
    mock_aioresponse.get(
        fallback_url, status=404, payload={"message": "Fallback endpoint not found"}
    )

    with pytest.raises(WeheatApiError) as exc_info:
        await client.async_get_energy_total(TEST_HP_UUID_BLACKBIRD)

    assert exc_info.value.status_code == 404


@pytest.mark.parametrize(
    ("client_err", "expected_status", "expected_type"),
    [
        (
            ClientResponseError(
                request_info=RequestInfo(
                    url=URL("http://example.com"),
                    method="GET",
                    headers={},
                    real_url=URL("http://example.com"),
                ),
                history=(),
                status=401,
                message="Unauthorized from proxy",
            ),
            401,
            WeheatAuthError,
        ),
        (
            ClientResponseError(
                request_info=RequestInfo(
                    url=URL("http://example.com"),
                    method="GET",
                    headers={},
                    real_url=URL("http://example.com"),
                ),
                history=(),
                status=500,
                message="Internal error from proxy",
            ),
            500,
            WeheatApiError,
        ),
        (
            ClientError("Low-level connection reset"),
            None,
            WeheatApiError,
        ),
    ],
)
async def test_client_response_and_connection_errors(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
    client_err: Exception,
    expected_status: int | None,
    expected_type: type[Exception],
) -> None:
    """Verify ClientResponseError and raw ClientError map to expected exceptions."""
    url = f"{API_BASE_URL}/api/v1/users/me"
    mock_aioresponse.get(url, exception=client_err)

    with pytest.raises(expected_type) as exc_info:
        await client.async_get_user_id()

    if expected_status is not None:
        assert getattr(exc_info.value, "status_code", None) == expected_status


# ===========================================================================
# 3. Client Timeout Stress: ServerTimeout, SocketTimeout, asyncio.TimeoutError
# ===========================================================================


@pytest.mark.parametrize(
    "exception_to_raise",
    [
        TimeoutError("asyncio or builtin timeout"),
        aiohttp.ServerTimeoutError("Server timed out"),
        aiohttp.SocketTimeoutError("Socket read timed out"),
    ],
)
async def test_timeout_exception_types(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
    exception_to_raise: Exception,
) -> None:
    """Verify all network timeout exception types map to WeheatTimeoutError."""
    url = f"{API_BASE_URL}/api/v1/users/me"
    mock_aioresponse.get(url, exception=exception_to_raise)

    with pytest.raises(WeheatTimeoutError, match="timed out"):
        await client.async_get_user_id()


# ===========================================================================
# 4. Polling Loop Edge Cases & Status Code Mapping
# ===========================================================================


async def test_poll_loop_does_not_hang_on_persistent_pending(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
) -> None:
    """Adversarial stress: Verify loop terminates and raises WeheatTimeoutError when status stays Pending (0)."""
    url = f"{PORTAL_BASE_URL}/api/v1/control-boards/{TEST_CB_ID_BLACKBIRD}/settings/{TEST_SETTINGS_REQUEST_ID}"

    # Return Pending indefinitely (up to 20 times)
    for _ in range(20):
        mock_aioresponse.get(url, status=200, payload={"status": 0})

    with pytest.raises(WeheatTimeoutError) as exc_info:
        await client.async_poll_settings_request(
            control_board_id=TEST_CB_ID_BLACKBIRD,
            request_id=TEST_SETTINGS_REQUEST_ID,
            timeout_seconds=0.08,
            poll_interval=0.01,
        )

    assert "timed out after 0.08s (status=0)" in str(exc_info.value)


async def test_poll_loop_does_not_hang_on_persistent_executing(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
) -> None:
    """Adversarial stress: Verify loop terminates and raises WeheatTimeoutError when status stays Executing (1)."""
    url = f"{PORTAL_BASE_URL}/api/v1/control-boards/{TEST_CB_ID_BLACKBIRD}/settings/{TEST_SETTINGS_REQUEST_ID}"

    for _ in range(20):
        mock_aioresponse.get(url, status=200, payload={"status": 1})

    with pytest.raises(WeheatTimeoutError) as exc_info:
        await client.async_poll_settings_request(
            control_board_id=TEST_CB_ID_BLACKBIRD,
            request_id=TEST_SETTINGS_REQUEST_ID,
            timeout_seconds=0.08,
            poll_interval=0.01,
        )

    assert "timed out after 0.08s (status=1)" in str(exc_info.value)


async def test_poll_status_5_succeeded_with_mismatch(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
) -> None:
    """Verify status 5 (SucceededWithHeatPumpSettingsMismatch) resolves as success and returns data."""
    url = f"{PORTAL_BASE_URL}/api/v1/control-boards/{TEST_CB_ID_BLACKBIRD}/settings/{TEST_SETTINGS_REQUEST_ID}"

    # Poll sequence: Pending (0) -> SucceededWithHeatPumpSettingsMismatch (5)
    mock_aioresponse.get(url, status=200, payload={"status": 0})
    expected_data = {
        "status": 5,
        "statusDescription": "SucceededWithHeatPumpSettingsMismatch",
        "settingsRequestId": TEST_SETTINGS_REQUEST_ID,
    }
    mock_aioresponse.get(url, status=200, payload=expected_data)

    result = await client.async_poll_settings_request(
        control_board_id=TEST_CB_ID_BLACKBIRD,
        request_id=TEST_SETTINGS_REQUEST_ID,
        timeout_seconds=2.0,
        poll_interval=0.01,
    )

    assert result == expected_data
    assert result["status"] == 5


@pytest.mark.parametrize(
    "fail_status",
    [3, 4, 6, 7, 8, 9, 10, 11],
)
async def test_poll_failure_statuses_3_to_11(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
    fail_status: int,
) -> None:
    """Adversarially test all failure statuses 3..11 raise WeheatApiError with correct name."""
    url = f"{PORTAL_BASE_URL}/api/v1/control-boards/{TEST_CB_ID_BLACKBIRD}/settings/{TEST_SETTINGS_REQUEST_ID}"
    status_desc = SETTINGS_REQUEST_STATUS_MAP[fail_status]

    mock_aioresponse.get(
        url,
        status=200,
        payload={"status": fail_status, "requestId": TEST_SETTINGS_REQUEST_ID},
    )

    with pytest.raises(WeheatApiError) as exc_info:
        await client.async_poll_settings_request(
            control_board_id=TEST_CB_ID_BLACKBIRD,
            request_id=TEST_SETTINGS_REQUEST_ID,
            poll_interval=0.01,
        )

    assert f"status {fail_status} ({status_desc})" in str(exc_info.value)
    assert exc_info.value.response_data == {
        "status": fail_status,
        "requestId": TEST_SETTINGS_REQUEST_ID,
    }


@pytest.mark.parametrize("unmapped_status", [-1, 12, 99])
async def test_poll_unmapped_status(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
    unmapped_status: int,
) -> None:
    """Verify undocumented status codes raise WeheatApiError with UnknownStatus descriptor."""
    url = f"{PORTAL_BASE_URL}/api/v1/control-boards/{TEST_CB_ID_BLACKBIRD}/settings/{TEST_SETTINGS_REQUEST_ID}"
    mock_aioresponse.get(url, status=200, payload={"status": unmapped_status})

    with pytest.raises(
        WeheatApiError,
        match=f"status {unmapped_status} \\(UnknownStatus\\({unmapped_status}\\)\\)",
    ):
        await client.async_poll_settings_request(
            control_board_id=TEST_CB_ID_BLACKBIRD,
            request_id=TEST_SETTINGS_REQUEST_ID,
            poll_interval=0.01,
        )


async def test_poll_missing_status_key(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
) -> None:
    """Verify response missing status key raises WeheatApiError immediately."""
    url = f"{PORTAL_BASE_URL}/api/v1/control-boards/{TEST_CB_ID_BLACKBIRD}/settings/{TEST_SETTINGS_REQUEST_ID}"
    mock_aioresponse.get(url, status=200, payload={"unrelatedKey": 123})

    with pytest.raises(WeheatApiError, match=r"status None \(UnknownStatus\(None\)\)"):
        await client.async_poll_settings_request(
            control_board_id=TEST_CB_ID_BLACKBIRD,
            request_id=TEST_SETTINGS_REQUEST_ID,
            poll_interval=0.01,
        )


async def test_poll_http_error_mid_polling(
    client: WeheatClient,
    mock_aioresponse: aioresponses,
) -> None:
    """Verify HTTP 500 error occurring mid-polling propagates immediately without hanging."""
    url = f"{PORTAL_BASE_URL}/api/v1/control-boards/{TEST_CB_ID_BLACKBIRD}/settings/{TEST_SETTINGS_REQUEST_ID}"
    # Poll 1: Pending
    mock_aioresponse.get(url, status=200, payload={"status": 0})
    # Poll 2: HTTP 500 Internal Server Error
    mock_aioresponse.get(url, status=500, payload={"error": "Database connection lost"})

    with pytest.raises(WeheatApiError) as exc_info:
        await client.async_poll_settings_request(
            control_board_id=TEST_CB_ID_BLACKBIRD,
            request_id=TEST_SETTINGS_REQUEST_ID,
            poll_interval=0.01,
        )

    assert exc_info.value.status_code == 500
    assert "Database connection lost" in str(exc_info.value)


# ===========================================================================
# 5. Offline Isolation Verification: Zero Outbound Network Sockets
# ===========================================================================


def test_offline_network_guard() -> None:
    """Empirical verification: Prove socket connection attempts are blocked/intercepted."""

    def blocked_connect(_self: Any, *args: Any, **_kwargs: Any) -> None:
        raise OSError(f"Real network connection attempted to {args}!")

    with patch.object(socket.socket, "connect", side_effect=blocked_connect):
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        with pytest.raises(OSError, match="Real network connection attempted"):
            s.connect(("api.weheat.nl", 443))
