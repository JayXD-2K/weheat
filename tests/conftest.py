"""Pytest test harness and offline fixtures for Weheat integration tests."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncGenerator, Generator
from pathlib import Path
from typing import Any

import aiohttp
import aiohttp.client_reqrep
import pytest
from aioresponses import aioresponses

from tests.ha_compat import install_ha_stubs

install_ha_stubs()

# Ensure compatibility between aioresponses and aiohttp versions (>=3.12)
# where ClientResponse.__init__ requires stream_writer (with idempotency guard)
if not getattr(aiohttp.client_reqrep.ClientResponse, "_weheat_compat_patched", False):
    _orig_client_response_init = aiohttp.client_reqrep.ClientResponse.__init__
    try:
        import inspect
        from unittest.mock import Mock

        if "stream_writer" in inspect.signature(_orig_client_response_init).parameters:

            def _compat_client_response_init(
                self: Any, *args: Any, **kwargs: Any
            ) -> None:
                if "stream_writer" not in kwargs or kwargs["stream_writer"] is None:
                    kwargs["stream_writer"] = Mock(output_size=0)
                _orig_client_response_init(self, *args, **kwargs)

            aiohttp.client_reqrep.ClientResponse.__init__ = _compat_client_response_init
            aiohttp.client_reqrep.ClientResponse._weheat_compat_patched = True
    except Exception:  # noqa: S110
        pass

# Fixture directory relative to tests/
FIXTURES_DIR = Path(__file__).parent / "fixtures"

# Standard test constants matching mock fixtures
TEST_USER_ID = "00000000-0000-0000-0000-000000000001"
TEST_HP_UUID_BLACKBIRD = "00000000-0000-0000-0000-000000000001"
TEST_HP_UUID_SPARROW = "00000000-0000-0000-0000-000000000002"
TEST_HP_UUID_FLINT = "00000000-0000-0000-0000-000000000003"
TEST_CB_ID_BLACKBIRD = "cb-00000000-0000-0000-0000-000000000001"
TEST_INSTALLATION_ID = "00000000-0000-0000-0000-000000000101"
TEST_SETTINGS_REQUEST_ID = "00000000-0000-0000-0000-000000000999"
TEST_ACCESS_TOKEN = "mock_access_token_12345"
TEST_REFRESH_TOKEN = "mock_refresh_token_67890"

API_BASE_URL = "https://api.weheat.nl/third_party"
PORTAL_BASE_URL = "https://api.weheat.nl"
AUTH_BASE_URL = "https://auth.weheat.nl"


def load_fixture(filename: str) -> Any:
    """Load and parse a JSON fixture file from tests/fixtures/.

    Args:
        filename: Name of the JSON fixture file (e.g. 'user_me.json').

    Returns:
        The deserialized JSON content (dict or list).

    Raises:
        FileNotFoundError: If the fixture file does not exist.
        json.JSONDecodeError: If the file contains invalid JSON.
    """
    path = FIXTURES_DIR / filename
    if not path.is_file():
        raise FileNotFoundError(f"Fixture file not found: {path.resolve()}")
    with path.open(encoding="utf-8") as file:
        return json.load(file)


@pytest.fixture
def token_response_fixture() -> dict[str, Any]:
    """Provide the parsed token_response.json fixture."""
    return load_fixture("token_response.json")


@pytest.fixture
def user_me_fixture() -> dict[str, Any]:
    """Provide the parsed user_me.json fixture."""
    return load_fixture("user_me.json")


@pytest.fixture
def heat_pumps_fixture() -> dict[str, Any]:
    """Provide the parsed heat_pumps.json fixture."""
    return load_fixture("heat_pumps.json")


@pytest.fixture
def latest_logs_fixture() -> dict[str, Any]:
    """Provide the parsed latest_logs.json fixture."""
    return load_fixture("latest_logs.json")


@pytest.fixture
def energy_total_fixture() -> dict[str, Any]:
    """Provide the parsed energy_total.json fixture."""
    return load_fixture("energy_total.json")


@pytest.fixture
def installation_settings_fixture() -> dict[str, Any]:
    """Provide the parsed installation_settings_get.json fixture."""
    return load_fixture("installation_settings_get.json")


@pytest.fixture
def installation_settings_pascal_fixture() -> dict[str, Any]:
    """Provide the parsed installation_settings_pascal_get.json fixture."""
    return load_fixture("installation_settings_pascal_get.json")


@pytest.fixture
def settings_update_fixture() -> dict[str, Any]:
    """Provide the parsed settings_update_success.json fixture."""
    return load_fixture("settings_update_success.json")


@pytest.fixture
def boost_trigger_fixture() -> dict[str, Any]:
    """Provide the parsed boost_trigger_success.json fixture."""
    return load_fixture("boost_trigger_success.json")


@pytest.fixture
def mock_aioresponse() -> Generator[aioresponses, None, None]:
    """Context-managed aioresponses interceptor for HTTP mocking.

    Guarantees no real network calls escape during test execution.
    """
    with aioresponses() as mock:
        yield mock


@pytest.fixture
async def client_session() -> AsyncGenerator[aiohttp.ClientSession, None]:
    """Create an aiohttp.ClientSession for async client testing."""
    async with aiohttp.ClientSession() as session:
        yield session


@pytest.fixture
def mock_weheat_client(client_session: aiohttp.ClientSession) -> Any:
    """Instantiate a WeheatClient wired to the test session and mock URLs."""
    try:
        from custom_components.weheat.client import WeheatClient

        return WeheatClient(
            session=client_session,
            base_url=API_BASE_URL,
            portal_base_url=PORTAL_BASE_URL,
        )
    except ImportError:
        return None


@pytest.fixture
def setup_mock_api(mock_aioresponse: aioresponses) -> aioresponses:
    """Pre-configure standard endpoints on the mock aioresponses instance.

    Sets up:
    - Auth token endpoint (POST)
    - User info /api/v1/users/me (GET)
    - Heat pumps list /api/v1/heat_pumps (GET) and fully-expanded (GET)
    - Latest logs /api/v1/heat-pumps/{uuid}/logs/latest (GET)
    - Total energy /api/v1/energy-logs/{uuid}/total and /total/{uuid} (GET)
    - Installation settings /api/v1/installations/{id}/settings (GET and PUT)
    - Control board settings PATCH & polling GET
    - Variables PATCH
    - DHW boost /api/v1/heat-pumps/{uuid}/dhw/boost (POST)
    """
    # Auth token endpoint
    mock_aioresponse.post(
        re.compile(r"^https://auth\.weheat\.nl/.*token.*$"),
        payload=load_fixture("token_response.json"),
        status=200,
    )

    # User profile
    mock_aioresponse.get(
        f"{API_BASE_URL}/api/v1/users/me",
        payload=load_fixture("user_me.json"),
        status=200,
    )
    mock_aioresponse.get(
        f"{PORTAL_BASE_URL}/api/v1/users/me",
        payload=load_fixture("user_me.json"),
        status=200,
    )

    # Active heat pumps discovery
    mock_aioresponse.get(
        re.compile(
            r"^https://api\.weheat\.nl/third_party/api/v1/heat[-_]pumps(\?.*)?$"
        ),
        payload=load_fixture("heat_pumps.json"),
        status=200,
    )
    mock_aioresponse.get(
        re.compile(r"^https://api\.weheat\.nl/api/v1/installations/fully-expanded.*$"),
        payload=load_fixture("installations_fully_expanded.json"),
        status=200,
    )

    # Latest operational logs for Blackbird
    mock_aioresponse.get(
        f"{API_BASE_URL}/api/v1/heat-pumps/{TEST_HP_UUID_BLACKBIRD}/logs/latest",
        payload=load_fixture("latest_logs.json"),
        status=200,
    )

    # Cumulative energy totals
    mock_aioresponse.get(
        f"{API_BASE_URL}/api/v1/energy-logs/{TEST_HP_UUID_BLACKBIRD}/total",
        payload=load_fixture("energy_total.json"),
        status=200,
    )
    mock_aioresponse.get(
        f"{API_BASE_URL}/total/{TEST_HP_UUID_BLACKBIRD}",
        payload=load_fixture("energy_total.json"),
        status=200,
    )

    # Portal installation settings GET
    mock_aioresponse.get(
        f"{PORTAL_BASE_URL}/api/v1/installations/{TEST_INSTALLATION_ID}/settings",
        payload=load_fixture("installation_settings_get.json"),
        status=200,
    )

    # Portal installation settings PUT
    mock_aioresponse.put(
        f"{PORTAL_BASE_URL}/api/v1/installations/{TEST_INSTALLATION_ID}/settings",
        payload=load_fixture("settings_update_success.json"),
        status=200,
    )

    # Control board settings PATCH
    mock_aioresponse.patch(
        f"{PORTAL_BASE_URL}/api/v1/control-boards/{TEST_CB_ID_BLACKBIRD}/settings",
        payload=load_fixture("settings_update_success.json"),
        status=200,
    )

    # Control board settings polling GET
    mock_aioresponse.get(
        f"{PORTAL_BASE_URL}/api/v1/control-boards/{TEST_CB_ID_BLACKBIRD}/settings/{TEST_SETTINGS_REQUEST_ID}",
        payload={"settingsRequestId": TEST_SETTINGS_REQUEST_ID, "status": 2},
        status=200,
    )

    # Control board variables PATCH
    mock_aioresponse.patch(
        f"{PORTAL_BASE_URL}/api/v1/control-boards/{TEST_CB_ID_BLACKBIRD}/variables",
        payload={"success": True, "variables": {"RequestLegionellaPrevention": 1}},
        status=200,
    )

    # DHW boost trigger POST
    mock_aioresponse.post(
        f"{PORTAL_BASE_URL}/api/v1/heat-pumps/{TEST_HP_UUID_BLACKBIRD}/dhw/boost",
        payload=load_fixture("boost_trigger_success.json"),
        status=200,
    )

    return mock_aioresponse
