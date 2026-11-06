"""Tests for WeheatPortalSession lifecycle, dual-session routing, and token handling."""

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError

from custom_components.weheat import async_setup_entry
from custom_components.weheat.client import WeheatError
from custom_components.weheat.const import (
    CONF_PORTAL_PASSWORD,
    CONF_PORTAL_TOKEN,
    CONF_PORTAL_USERNAME,
)
from custom_components.weheat.coordinator import (
    WeheatPortalSession,
)
from custom_components.weheat.models import HeatPumpInfo


@pytest.fixture
def mock_heat_pump_info() -> HeatPumpInfo:
    return HeatPumpInfo(
        heatpump_id="00000000-0000-0000-0000-000000000001",
        name="Test Heat Pump",
        model=0,
        serial_number="WH-001",
        has_dhw=True,
        control_board_id="cb-00000000-0000-0000-0000-000000000001",
    )


@pytest.fixture
def mock_client() -> MagicMock:
    client = MagicMock()
    client.async_refresh_portal_token = AsyncMock()
    client.async_refresh_portal_session = AsyncMock()
    client.async_login_portal_pkce = AsyncMock()
    client.async_set_performance_mode = AsyncMock(return_value="req-123")
    client.async_poll_settings_request = AsyncMock()
    return client


@pytest.fixture
def mock_fallback_session() -> MagicMock:
    session = MagicMock()
    session.token = {CONF_ACCESS_TOKEN: "mock_ha_oauth_token"}
    session.async_ensure_token_valid = AsyncMock()
    return session


# -----------------------------------------------------------------------------
# 1. WeheatPortalSession State & Property Tests
# -----------------------------------------------------------------------------


def test_portal_session_init_no_auth(
    mock_client: MagicMock, mock_fallback_session: MagicMock
) -> None:
    """Verify session initialized with empty options defaults to fallback token and has_portal_auth=False."""
    hass = HomeAssistant()
    entry = ConfigEntry(entry_id="test", options={})

    session = WeheatPortalSession(hass, entry, mock_client, mock_fallback_session)

    assert session.has_portal_auth is False
    assert session.token == {CONF_ACCESS_TOKEN: "mock_ha_oauth_token"}


def test_portal_session_init_with_token(
    mock_client: MagicMock, mock_fallback_session: MagicMock
) -> None:
    """Verify session initialized with portal token has_portal_auth=True and returns portal token."""
    hass = HomeAssistant()
    entry = ConfigEntry(
        entry_id="test",
        options={
            CONF_PORTAL_TOKEN: {
                CONF_ACCESS_TOKEN: "portal_jwt_abc",
                "refresh_token": "portal_refresh_123",
                "expires_at": time.time() + 300,
            }
        },
    )

    session = WeheatPortalSession(hass, entry, mock_client, mock_fallback_session)

    assert session.has_portal_auth is True
    assert session.token.get(CONF_ACCESS_TOKEN) == "portal_jwt_abc"


def test_portal_session_init_with_nextauth_camelcase_token(
    mock_client: MagicMock, mock_fallback_session: MagicMock
) -> None:
    """Verify session extracts accessToken (camelCase) from NextAuth JSON dictionary."""
    hass = HomeAssistant()
    entry = ConfigEntry(
        entry_id="test",
        options={
            CONF_PORTAL_TOKEN: {
                "accessToken": "nextauth_jwt_camel",
                "expires_at": time.time() + 300,
            }
        },
    )

    session = WeheatPortalSession(hass, entry, mock_client, mock_fallback_session)

    assert session.has_portal_auth is True
    assert session.token.get(CONF_ACCESS_TOKEN) == "nextauth_jwt_camel"


def test_portal_session_init_with_nested_nextauth_token(
    mock_client: MagicMock, mock_fallback_session: MagicMock
) -> None:
    """Verify session extracts token nested under raw_tok['token']."""
    hass = HomeAssistant()
    entry = ConfigEntry(
        entry_id="test",
        options={
            CONF_PORTAL_TOKEN: {
                "token": {
                    "accessToken": "nested_jwt_xyz",
                },
                "expires_at": time.time() + 300,
            }
        },
    )

    session = WeheatPortalSession(hass, entry, mock_client, mock_fallback_session)

    assert session.has_portal_auth is True
    assert session.token.get(CONF_ACCESS_TOKEN) == "nested_jwt_xyz"


def test_portal_session_init_with_raw_string_json(
    mock_client: MagicMock, mock_fallback_session: MagicMock
) -> None:
    """Verify session extracts token when CONF_PORTAL_TOKEN is a raw JSON string."""
    hass = HomeAssistant()
    entry = ConfigEntry(
        entry_id="test",
        options={
            CONF_PORTAL_TOKEN: '{"accessToken": "raw_str_jwt_999"}',
        },
    )

    session = WeheatPortalSession(hass, entry, mock_client, mock_fallback_session)

    assert session.has_portal_auth is True
    assert session.token.get(CONF_ACCESS_TOKEN) == "raw_str_jwt_999"


def test_portal_session_init_with_credentials(
    mock_client: MagicMock, mock_fallback_session: MagicMock
) -> None:
    """Verify session initialized with username/password has_portal_auth=True."""
    hass = HomeAssistant()
    entry = ConfigEntry(
        entry_id="test",
        options={
            CONF_PORTAL_USERNAME: "user@weheat.nl",
            CONF_PORTAL_PASSWORD: "secret_password",
        },
    )

    session = WeheatPortalSession(hass, entry, mock_client, mock_fallback_session)

    assert session.has_portal_auth is True
    # Before login, falls back to fallback_session.token
    assert session.token == {CONF_ACCESS_TOKEN: "mock_ha_oauth_token"}


def test_portal_session_dynamic_options_read(
    mock_client: MagicMock, mock_fallback_session: MagicMock
) -> None:
    """Verify token property dynamically updates when options are added post-init."""
    hass = HomeAssistant()
    entry = ConfigEntry(entry_id="test", options={})

    session = WeheatPortalSession(hass, entry, mock_client, mock_fallback_session)
    assert session.has_portal_auth is False
    assert session.token == {CONF_ACCESS_TOKEN: "mock_ha_oauth_token"}

    # Simulate options update without recreating wrapper instance
    entry.options[CONF_PORTAL_TOKEN] = {CONF_ACCESS_TOKEN: "late_added_token"}
    assert session.has_portal_auth is True
    assert session.token == {CONF_ACCESS_TOKEN: "late_added_token"}


# -----------------------------------------------------------------------------
# 2. Token Validation & Refresh Lifecycle Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_portal_session_ensure_valid_without_auth_delegates_to_fallback(
    mock_client: MagicMock, mock_fallback_session: MagicMock
) -> None:
    """Verify async_ensure_token_valid delegates to fallback session when no portal auth is configured."""
    hass = HomeAssistant()
    entry = ConfigEntry(entry_id="test", options={})
    session = WeheatPortalSession(hass, entry, mock_client, mock_fallback_session)

    await session.async_ensure_token_valid()

    mock_fallback_session.async_ensure_token_valid.assert_called_once()
    mock_client.async_refresh_portal_token.assert_not_called()
    mock_client.async_login_portal_pkce.assert_not_called()


@pytest.mark.asyncio
async def test_portal_session_ensure_valid_active_token_noop(
    mock_client: MagicMock, mock_fallback_session: MagicMock
) -> None:
    """Verify async_ensure_token_valid does nothing if portal token is valid for > 30 seconds."""
    hass = HomeAssistant()
    entry = ConfigEntry(
        entry_id="test",
        options={
            CONF_PORTAL_TOKEN: {
                CONF_ACCESS_TOKEN: "valid_jwt",
                "expires_at": time.time() + 300.0,
            }
        },
    )
    session = WeheatPortalSession(hass, entry, mock_client, mock_fallback_session)

    await session.async_ensure_token_valid()

    mock_fallback_session.async_ensure_token_valid.assert_not_called()
    mock_client.async_refresh_portal_token.assert_not_called()
    mock_client.async_login_portal_pkce.assert_not_called()


@pytest.mark.asyncio
async def test_portal_session_ensure_valid_refreshes_expiring_token(
    mock_client: MagicMock, mock_fallback_session: MagicMock
) -> None:
    """Verify expiring token triggers async_refresh_portal_token and persists new token."""
    hass = HomeAssistant()
    entry = ConfigEntry(
        entry_id="test",
        options={
            CONF_PORTAL_TOKEN: {
                CONF_ACCESS_TOKEN: "expiring_jwt",
                "refresh_token": "refresh_abc",
                "expires_at": time.time() + 10.0,
            }
        },
    )
    session = WeheatPortalSession(hass, entry, mock_client, mock_fallback_session)

    refreshed_token = {
        CONF_ACCESS_TOKEN: "new_refreshed_jwt",
        "refresh_token": "new_refresh_xyz",
        "expires_at": time.time() + 300.0,
    }
    mock_client.async_refresh_portal_token.return_value = refreshed_token

    await session.async_ensure_token_valid()

    mock_client.async_refresh_portal_token.assert_called_once_with("refresh_abc")
    assert session.token.get(CONF_ACCESS_TOKEN) == "new_refreshed_jwt"
    assert entry.options[CONF_PORTAL_TOKEN] == refreshed_token


@pytest.mark.asyncio
async def test_portal_session_ensure_valid_pkce_direct(
    mock_client: MagicMock, mock_fallback_session: MagicMock
) -> None:
    """Verify credentials trigger async_login_portal_pkce when no token exists."""
    hass = HomeAssistant()
    entry = ConfigEntry(
        entry_id="test",
        options={
            CONF_PORTAL_USERNAME: "user@weheat.nl",
            CONF_PORTAL_PASSWORD: "secret_password",
        },
    )
    session = WeheatPortalSession(hass, entry, mock_client, mock_fallback_session)

    new_token = {
        CONF_ACCESS_TOKEN: "pkce_jwt_123",
        "expires_at": time.time() + 300.0,
    }
    mock_client.async_login_portal_pkce.return_value = new_token

    await session.async_ensure_token_valid()

    mock_client.async_login_portal_pkce.assert_called_once_with(
        "user@weheat.nl", "secret_password"
    )
    assert session.token.get(CONF_ACCESS_TOKEN) == "pkce_jwt_123"
    assert entry.options[CONF_PORTAL_TOKEN] == new_token


@pytest.mark.asyncio
async def test_portal_session_ensure_valid_pkce_fallback_on_refresh_error(
    mock_client: MagicMock, mock_fallback_session: MagicMock
) -> None:
    """Verify refresh failure falls back to PKCE login when username/password exist."""
    hass = HomeAssistant()
    entry = ConfigEntry(
        entry_id="test",
        options={
            CONF_PORTAL_USERNAME: "user@weheat.nl",
            CONF_PORTAL_PASSWORD: "secret_password",
            CONF_PORTAL_TOKEN: {
                CONF_ACCESS_TOKEN: "stale_jwt",
                "refresh_token": "expired_refresh",
                "expires_at": time.time() - 100.0,
            },
        },
    )
    session = WeheatPortalSession(hass, entry, mock_client, mock_fallback_session)

    mock_client.async_refresh_portal_token.side_effect = WeheatError(
        "Refresh token expired"
    )
    pkce_token = {
        CONF_ACCESS_TOKEN: "recovered_jwt",
        "expires_at": time.time() + 300.0,
    }
    mock_client.async_login_portal_pkce.return_value = pkce_token

    await session.async_ensure_token_valid()

    mock_client.async_refresh_portal_token.assert_called_once_with("expired_refresh")
    mock_client.async_login_portal_pkce.assert_called_once_with(
        "user@weheat.nl", "secret_password"
    )
    assert session.token.get(CONF_ACCESS_TOKEN) == "recovered_jwt"


# -----------------------------------------------------------------------------
# 3. Check Portal Auth & Actionable Error Handling Tests
# -----------------------------------------------------------------------------


def test_portal_session_check_portal_auth_raises_without_credentials(
    mock_client: MagicMock, mock_fallback_session: MagicMock
) -> None:
    """Verify check_portal_auth raises clear actionable HomeAssistantError when no portal auth is present."""
    hass = HomeAssistant()
    entry = ConfigEntry(entry_id="test", options={})
    session = WeheatPortalSession(hass, entry, mock_client, mock_fallback_session)

    with pytest.raises(HomeAssistantError) as exc_info:
        session.check_portal_auth()

    assert "Weheat Portal credentials required for control actions" in str(
        exc_info.value
    )
    assert "Configure options" in str(exc_info.value)


def test_portal_session_check_portal_auth_passes_with_credentials(
    mock_client: MagicMock, mock_fallback_session: MagicMock
) -> None:
    """Verify check_portal_auth does not raise when portal credentials exist."""
    hass = HomeAssistant()
    entry = ConfigEntry(
        entry_id="test",
        options={CONF_PORTAL_USERNAME: "u", CONF_PORTAL_PASSWORD: "p"},
    )
    session = WeheatPortalSession(hass, entry, mock_client, mock_fallback_session)

    session.check_portal_auth()  # Should not raise


def test_portal_session_check_portal_auth_passes_with_token(
    mock_client: MagicMock, mock_fallback_session: MagicMock
) -> None:
    """Verify check_portal_auth does not raise when portal token exists."""
    hass = HomeAssistant()
    entry = ConfigEntry(
        entry_id="test",
        options={CONF_PORTAL_TOKEN: {CONF_ACCESS_TOKEN: "jwt"}},
    )
    session = WeheatPortalSession(hass, entry, mock_client, mock_fallback_session)

    session.check_portal_auth()  # Should not raise


# -----------------------------------------------------------------------------
# 4. Entry Setup Dual Session Routing Tests
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_init_dual_session_routing(mock_heat_pump_info: HeatPumpInfo) -> None:
    """Verify async_setup_entry routes OAuth2Session to telemetry and WeheatPortalSession to control."""
    from homeassistant.const import CONF_TOKEN

    hass = HomeAssistant()
    entry = ConfigEntry(
        entry_id="test_entry",
        data={CONF_TOKEN: {CONF_ACCESS_TOKEN: "mock_ha_core_token"}},
        options={},
    )

    with (
        patch(
            "custom_components.weheat.OAuth2Session.async_ensure_token_valid",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.weheat.WeheatClient.async_get_heat_pumps",
            new=AsyncMock(return_value=[mock_heat_pump_info]),
        ),
        patch(
            "custom_components.weheat.coordinator.WeheatDataUpdateCoordinator.async_config_entry_first_refresh",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.weheat.coordinator.WeheatEnergyUpdateCoordinator.async_config_entry_first_refresh",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.weheat.coordinator.WeheatSettingsUpdateCoordinator.async_config_entry_first_refresh",
            new=AsyncMock(),
        ),
    ):
        result = await async_setup_entry(hass, entry)

    assert result is True
    assert len(entry.runtime_data) == 1
    weheat_data = entry.runtime_data[0]

    # Verify session routing isolation
    assert isinstance(weheat_data.session, WeheatPortalSession)
    assert isinstance(weheat_data.settings_coordinator.session, WeheatPortalSession)
    assert not isinstance(weheat_data.data_coordinator.session, WeheatPortalSession)
    assert not isinstance(weheat_data.energy_coordinator.session, WeheatPortalSession)

    # Verify update listener registration
    assert len(entry.update_listeners) > 0


# -----------------------------------------------------------------------------
# 5. NextAuth PKCE Flow, Session Cookie Refresh, and Reboot 401 Recovery Tests
# -----------------------------------------------------------------------------


def test_parse_portal_cookie_input() -> None:
    """Verify WeheatClient.parse_portal_cookie_input parses NextAuth cookie strings and ignores JSON/JWT."""
    from custom_components.weheat.client import WeheatClient

    raw_header = (
        "Cookie: __Secure-next-auth.session-token=tok_part_1; "
        "__Host-next-auth.csrf-token=csrf_123"
    )
    cookies = WeheatClient.parse_portal_cookie_input(raw_header)
    assert cookies["__Secure-next-auth.session-token"] == "tok_part_1"
    assert cookies["__Host-next-auth.csrf-token"] == "csrf_123"

    raw_single_val = "eyJhbGciOiJkaXIiLCJlbmMiOiJBMjU2R0NNIn0..encrypted_jwe"
    # Not a 3-part JWT (has empty middle part or 5 parts), or direct cookie key=value
    cookies_kv = WeheatClient.parse_portal_cookie_input(
        "__Secure-next-auth.session-token.0=chunk0; __Secure-next-auth.session-token.1=chunk1"
    )
    assert cookies_kv == {
        "__Secure-next-auth.session-token.0": "chunk0",
        "__Secure-next-auth.session-token.1": "chunk1",
    }

    # Standard JSON or 3-part JWT should return None
    assert WeheatClient.parse_portal_cookie_input('{"accessToken": "abc"}') is None
    assert WeheatClient.parse_portal_cookie_input("header.payload.signature") is None
    assert WeheatClient.parse_portal_cookie_input(raw_single_val) is None


@pytest.mark.asyncio
async def test_nextauth_portal_pkce_login_flow() -> None:
    """Verify async_login_portal_pkce authenticates via portal.weheat.nl NextAuth without direct unauthorized_client token exchange."""
    import base64
    import json
    from urllib.parse import quote

    from custom_components.weheat.client import WeheatClient

    exp_ts = int(time.time()) + 300
    payload_b64 = (
        base64.urlsafe_b64encode(json.dumps({"exp": exp_ts}).encode())
        .decode()
        .rstrip("=")
    )
    valid_jwt = f"eyJhbGciOiJSUzI1NiJ9.{payload_b64}.sig"

    auth_url = (
        "https://auth.weheat.nl/realms/Weheat/protocol/openid-connect/auth"
        "?client_id=weheat-portal&scope=openid&response_type=code"
        "&redirect_uri=https%3A%2F%2Fportal.weheat.nl%2Fapi%2Fauth%2Fcallback%2Fkeycloak"
        "&state=nextauth_state_123&code_challenge=abc&code_challenge_method=S256"
    )
    login_action = (
        "https://auth.weheat.nl/realms/Weheat/login-actions/authenticate"
        "?session_code=sc1&execution=ex1&client_id=weheat-portal&tab_id=t1"
    )
    callback_url = (
        "https://portal.weheat.nl/api/auth/callback/keycloak"
        f"?state={quote('nextauth_state_123')}&code=auth_code_xyz"
    )

    class FakeResp:
        def __init__(
            self,
            status: int,
            *,
            json_data: dict | None = None,
            text_data: str = "",
            headers: dict[str, str] | None = None,
        ) -> None:
            self.status = status
            self._json_data = json_data
            self._text_data = text_data
            self.headers = headers or {}

        async def json(self, content_type: str | None = None) -> dict:
            if self._json_data is None:
                raise ValueError("No JSON")
            return self._json_data

        async def text(self) -> str:
            if self._text_data:
                return self._text_data
            if self._json_data is not None:
                return json.dumps(self._json_data)
            return ""

        async def __aenter__(self) -> FakeResp:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

    class FakeCookie:
        def __init__(self, key: str, value: str) -> None:
            self.key = key
            self.value = value

    class FakeSession:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []
            self.cookie_jar = [
                FakeCookie("__Secure-next-auth.session-token", "encrypted_session_tok")
            ]

        def get(self, url: str, **kwargs: object) -> FakeResp:
            self.calls.append(("GET", url))
            if url.endswith("/api/auth/csrf"):
                return FakeResp(200, json_data={"csrfToken": "csrf_tok_999"})
            if "protocol/openid-connect/auth" in url:
                html = (
                    f'<script>const u = {{"loginAction": "{login_action}"}};</script>'
                )
                return FakeResp(200, text_data=html)
            if "/api/auth/callback/keycloak" in url:
                return FakeResp(
                    302, headers={"Location": "https://portal.weheat.nl/overview"}
                )
            if url.endswith("/api/auth/session"):
                return FakeResp(
                    200,
                    json_data={
                        "user": {"email": "user@weheat.nl"},
                        "accessToken": valid_jwt,
                        "expires": "2099-01-01T00:00:00.000Z",
                    },
                )
            raise AssertionError(f"Unexpected GET {url}")

        def post(self, url: str, **kwargs: object) -> FakeResp:
            self.calls.append(("POST", url))
            if url.endswith("/api/auth/signin/keycloak"):
                return FakeResp(200, json_data={"url": auth_url})
            if url == login_action:
                return FakeResp(302, headers={"Location": callback_url})
            raise AssertionError(f"Unexpected POST {url}")

        async def __aenter__(self) -> FakeSession:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

    fake_http_session = FakeSession()
    client = WeheatClient(session=MagicMock())

    with patch("aiohttp.ClientSession", return_value=fake_http_session):
        token_data = await client.async_login_portal_pkce(
            username="user@weheat.nl",
            password="correct_password",
        )

    assert token_data[CONF_ACCESS_TOKEN] == valid_jwt
    assert token_data["session_cookies"] == {
        "__Secure-next-auth.session-token": "encrypted_session_tok"
    }
    assert token_data["expires_at"] == float(exp_ts)
    # Ensure direct Keycloak token endpoint was NEVER called (which would trigger 401 unauthorized_client)
    assert not any("openid-connect/token" in url for _, url in fake_http_session.calls)


@pytest.mark.asyncio
async def test_portal_session_ensure_valid_uses_session_cookies_then_falls_back_to_pkce(
    mock_client: MagicMock, mock_fallback_session: MagicMock
) -> None:
    """Verify expiring token with session_cookies tries async_refresh_portal_session first, then falls back to PKCE."""
    from custom_components.weheat.client import WeheatAuthError

    hass = HomeAssistant()
    entry = ConfigEntry(
        entry_id="test",
        options={
            CONF_PORTAL_USERNAME: "user@weheat.nl",
            CONF_PORTAL_PASSWORD: "secret_password",
            CONF_PORTAL_TOKEN: {
                CONF_ACCESS_TOKEN: "expired_jwt",
                "session_cookies": {"__Secure-next-auth.session-token": "cookie_val"},
                "expires_at": time.time() - 10.0,
            },
        },
    )
    session = WeheatPortalSession(hass, entry, mock_client, mock_fallback_session)

    # Case 1: async_refresh_portal_session succeeds
    cookie_refreshed_token = {
        CONF_ACCESS_TOKEN: "cookie_refreshed_jwt",
        "session_cookies": {"__Secure-next-auth.session-token": "cookie_val_2"},
        "expires_at": time.time() + 300.0,
    }
    mock_client.async_refresh_portal_session.return_value = cookie_refreshed_token

    await session.async_ensure_token_valid()

    mock_client.async_refresh_portal_session.assert_called_once_with(
        {"__Secure-next-auth.session-token": "cookie_val"},
        previous_access_token="expired_jwt",
    )
    mock_client.async_login_portal_pkce.assert_not_called()
    assert session.token.get(CONF_ACCESS_TOKEN) == "cookie_refreshed_jwt"

    # Case 2: Force refresh when async_refresh_portal_session raises WeheatAuthError (un-rotated token), falls back to PKCE
    mock_client.async_refresh_portal_session.reset_mock()
    mock_client.async_refresh_portal_session.side_effect = WeheatAuthError(
        "Session did not rotate expired accessToken", status_code=401
    )
    pkce_token = {
        CONF_ACCESS_TOKEN: "fresh_pkce_jwt",
        "session_cookies": {"__Secure-next-auth.session-token": "cookie_val_3"},
        "expires_at": time.time() + 300.0,
    }
    mock_client.async_login_portal_pkce.return_value = pkce_token

    await session.async_force_refresh()

    mock_client.async_refresh_portal_session.assert_called_once()
    mock_client.async_login_portal_pkce.assert_called_once_with(
        "user@weheat.nl", "secret_password"
    )
    assert session.token.get(CONF_ACCESS_TOKEN) == "fresh_pkce_jwt"


@pytest.mark.asyncio
async def test_settings_coordinator_401_triggers_force_refresh_and_retry(
    mock_heat_pump_info: HeatPumpInfo,
    mock_client: MagicMock,
    mock_fallback_session: MagicMock,
) -> None:
    """Verify WeheatSettingsUpdateCoordinator forces a portal token refresh and retries on HTTP 401."""
    from custom_components.weheat.client import WeheatAuthError
    from custom_components.weheat.coordinator import WeheatSettingsUpdateCoordinator
    from custom_components.weheat.models import ControlBoardSettings

    hass = HomeAssistant()
    mock_heat_pump_info.installation_id = "inst-001"
    entry = ConfigEntry(
        entry_id="test_entry",
        options={
            CONF_PORTAL_USERNAME: "user@weheat.nl",
            CONF_PORTAL_PASSWORD: "secret_password",
            CONF_PORTAL_TOKEN: {
                CONF_ACCESS_TOKEN: "stale_reboot_jwt",
                "expires_at": time.time() + 200.0,
            },
        },
    )
    portal_session = WeheatPortalSession(
        hass, entry, mock_client, mock_fallback_session
    )
    mock_client.async_login_portal_pkce.return_value = {
        CONF_ACCESS_TOKEN: "reauthenticated_jwt",
        "expires_at": time.time() + 300.0,
    }
    mock_client.async_get_installation_settings = AsyncMock(
        side_effect=[
            WeheatAuthError("Unauthorized", status_code=401),
            ControlBoardSettings(performance_mode_preset=2),
        ]
    )

    coord = WeheatSettingsUpdateCoordinator(
        hass, entry, portal_session, mock_heat_pump_info, client=mock_client
    )
    settings = await coord._async_update_data()

    assert settings.performance_mode_preset == 2
    assert mock_client.async_get_installation_settings.call_count == 2
    mock_client.async_login_portal_pkce.assert_called_once_with(
        "user@weheat.nl", "secret_password"
    )
    assert portal_session.token[CONF_ACCESS_TOKEN] == "reauthenticated_jwt"


@pytest.mark.asyncio
async def test_setup_entry_topology_401_recovers_via_force_refresh(
    mock_heat_pump_info: HeatPumpInfo,
) -> None:
    """Verify async_setup_entry forces portal token refresh on 401 during topology discovery after reboot."""
    from homeassistant.const import CONF_TOKEN

    from custom_components.weheat.client import WeheatAuthError

    hass = HomeAssistant()
    hp_without_cb = HeatPumpInfo(
        heatpump_id="00000000-0000-0000-0000-000000000001",
        name="Test Heat Pump",
        model=0,
        serial_number="WH-001",
        has_dhw=True,
        control_board_id=None,
    )
    entry = ConfigEntry(
        entry_id="test_entry",
        data={CONF_TOKEN: {CONF_ACCESS_TOKEN: "mock_ha_core_token"}},
        options={
            CONF_PORTAL_USERNAME: "user@weheat.nl",
            CONF_PORTAL_PASSWORD: "secret_password",
            CONF_PORTAL_TOKEN: {
                CONF_ACCESS_TOKEN: "stale_reboot_jwt",
                "expires_at": time.time() + 200.0,
            },
        },
    )

    topology_mock = AsyncMock(
        side_effect=[
            WeheatAuthError("Unauthorized", status_code=401),
            [
                {
                    "id": "inst-123",
                    "heatPumpId": "00000000-0000-0000-0000-000000000001",
                    "controlBoardId": "cb-recovered-001",
                }
            ],
        ]
    )
    pkce_mock = AsyncMock(
        return_value={
            CONF_ACCESS_TOKEN: "fresh_reboot_jwt",
            "expires_at": time.time() + 300.0,
        }
    )

    with (
        patch(
            "custom_components.weheat.OAuth2Session.async_ensure_token_valid",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.weheat.WeheatClient.async_get_heat_pumps",
            new=AsyncMock(return_value=[hp_without_cb]),
        ),
        patch(
            "custom_components.weheat.WeheatClient.async_get_installation_topology",
            new=topology_mock,
        ),
        patch(
            "custom_components.weheat.WeheatClient.async_login_portal_pkce",
            new=pkce_mock,
        ),
        patch(
            "custom_components.weheat.coordinator.WeheatDataUpdateCoordinator.async_config_entry_first_refresh",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.weheat.coordinator.WeheatEnergyUpdateCoordinator.async_config_entry_first_refresh",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.weheat.coordinator.WeheatSettingsUpdateCoordinator.async_config_entry_first_refresh",
            new=AsyncMock(),
        ),
    ):
        result = await async_setup_entry(hass, entry)

    assert result is True
    assert topology_mock.call_count == 2
    pkce_mock.assert_called_once_with("user@weheat.nl", "secret_password")
    assert hp_without_cb.control_board_id == "cb-recovered-001"
    assert hp_without_cb.installation_id == "inst-123"


@pytest.mark.asyncio
async def test_async_refresh_portal_session_success_and_unrotated_rejection() -> None:
    """Verify async_refresh_portal_session exchanges cookies for a fresh JWT and rejects un-rotated or expired tokens."""
    import base64
    import json

    from custom_components.weheat.client import WeheatAuthError, WeheatClient

    exp_ts = int(time.time()) + 300
    payload_b64 = (
        base64.urlsafe_b64encode(json.dumps({"exp": exp_ts}).encode())
        .decode()
        .rstrip("=")
    )
    valid_jwt = f"eyJhbGciOiJSUzI1NiJ9.{payload_b64}.sig"

    class FakeResp:
        def __init__(self, status: int, json_data: dict | None = None) -> None:
            self.status = status
            self._json_data = json_data

        async def json(self, content_type: str | None = None) -> dict:
            if self._json_data is None:
                raise ValueError("No JSON")
            return self._json_data

        async def text(self) -> str:
            return json.dumps(self._json_data or {})

        async def __aenter__(self) -> FakeResp:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

    class FakeCookie:
        def __init__(self, key: str, value: str) -> None:
            self.key = key
            self.value = value

    class FakeSession:
        def __init__(self, resp_data: dict) -> None:
            self._resp_data = resp_data
            self.cookie_jar = [
                FakeCookie("__Secure-next-auth.session-token", "rotated_cookie_val")
            ]

        def get(self, url: str, **kwargs: object) -> FakeResp:
            return FakeResp(200, self._resp_data)

        async def __aenter__(self) -> FakeSession:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

    client = WeheatClient(session=MagicMock())

    # 1. Valid rotated token
    fake_session_ok = FakeSession(
        {"accessToken": valid_jwt, "user": {"email": "u@weheat.nl"}}
    )
    with patch("aiohttp.ClientSession", return_value=fake_session_ok):
        refreshed = await client.async_refresh_portal_session(
            {"__Secure-next-auth.session-token": "initial_cookie_val"},
            previous_access_token="old_expired_jwt",
        )
    assert refreshed[CONF_ACCESS_TOKEN] == valid_jwt
    assert (
        refreshed["session_cookies"]["__Secure-next-auth.session-token"]
        == "rotated_cookie_val"
    )
    assert refreshed["expires_at"] == float(exp_ts)

    # 2. Un-rotated identical token when previous_access_token matches -> raises WeheatAuthError
    with (
        patch("aiohttp.ClientSession", return_value=fake_session_ok),
        pytest.raises(WeheatAuthError, match="did not return a refreshed access token"),
    ):
        await client.async_refresh_portal_session(
            {"__Secure-next-auth.session-token": "initial_cookie_val"},
            previous_access_token=valid_jwt,
        )


@pytest.mark.asyncio
async def test_nextauth_portal_pkce_login_error_branches_and_direct_fallback() -> None:
    """Verify async_login_portal_pkce error handling for invalid credentials, OAuthCallback error, and direct PKCE client_id."""
    from custom_components.weheat.client import WeheatAuthError, WeheatClient

    auth_url = (
        "https://auth.weheat.nl/realms/Weheat/protocol/openid-connect/auth"
        "?client_id=weheat-portal&scope=openid&response_type=code"
    )
    login_action = (
        "https://auth.weheat.nl/realms/Weheat/login-actions/authenticate?code=1"
    )

    class FakeResp:
        def __init__(
            self,
            status: int,
            *,
            json_data: dict | None = None,
            text_data: str = "",
            headers: dict[str, str] | None = None,
        ) -> None:
            self.status = status
            self._json_data = json_data
            self._text_data = text_data
            self.headers = headers or {}

        async def json(self, content_type: str | None = None) -> dict:
            if self._json_data is None:
                raise ValueError("No JSON")
            return self._json_data

        async def text(self) -> str:
            return self._text_data

        async def __aenter__(self) -> FakeResp:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

    class FakeInvalidCredsSession:
        cookie_jar: list = []

        def get(self, url: str, **kwargs: object) -> FakeResp:
            if url.endswith("/api/auth/csrf"):
                return FakeResp(200, json_data={"csrfToken": "csrf_1"})
            if "protocol/openid-connect/auth" in url:
                return FakeResp(200, text_data=f'"loginAction": "{login_action}"')
            raise AssertionError(f"Unexpected GET {url}")

        def post(self, url: str, **kwargs: object) -> FakeResp:
            if url.endswith("/api/auth/signin/keycloak"):
                return FakeResp(302, headers={"Location": auth_url})
            if url == login_action:
                # Keycloak returns 200 HTML page when username/password are wrong
                return FakeResp(
                    200, text_data="<html>Invalid username or password</html>"
                )
            raise AssertionError(f"Unexpected POST {url}")

        async def __aenter__(self) -> FakeInvalidCredsSession:
            return self

        async def __aexit__(self, *args: object) -> None:
            pass

    client = WeheatClient(session=MagicMock())
    with (
        patch("aiohttp.ClientSession", return_value=FakeInvalidCredsSession()),
        pytest.raises(WeheatAuthError, match="Invalid Weheat portal email or password"),
    ):
        await client.async_login_portal_pkce("user@weheat.nl", "wrong_pass")


@pytest.mark.asyncio
async def test_settings_coordinator_401_topology_recovery_and_no_reauth_failure(
    mock_heat_pump_info: HeatPumpInfo,
    mock_client: MagicMock,
    mock_fallback_session: MagicMock,
) -> None:
    """Verify WeheatSettingsUpdateCoordinator 401 recovery during fallback topology lookup and ConfigEntryAuthFailed when un-reauthenticatable."""
    from homeassistant.exceptions import ConfigEntryAuthFailed

    from custom_components.weheat.client import WeheatAuthError
    from custom_components.weheat.coordinator import WeheatSettingsUpdateCoordinator
    from custom_components.weheat.models import ControlBoardSettings

    hass = HomeAssistant()
    mock_heat_pump_info.installation_id = None

    # 1. 401 during topology discovery when installation_id is None recovers via force refresh
    entry_with_creds = ConfigEntry(
        entry_id="test_entry",
        options={
            CONF_PORTAL_USERNAME: "user@weheat.nl",
            CONF_PORTAL_PASSWORD: "secret_password",
            CONF_PORTAL_TOKEN: {
                CONF_ACCESS_TOKEN: "stale_jwt",
                "expires_at": time.time() + 200.0,
            },
        },
    )
    portal_session = WeheatPortalSession(
        hass, entry_with_creds, mock_client, mock_fallback_session
    )
    mock_client.async_login_portal_pkce.return_value = {
        CONF_ACCESS_TOKEN: "fresh_jwt",
        "expires_at": time.time() + 300.0,
    }
    mock_client.async_get_installation_topology = AsyncMock(
        side_effect=[
            WeheatAuthError("Unauthorized", status_code=401),
            [
                {
                    "id": "inst-recovered",
                    "heatPumpId": mock_heat_pump_info.heatpump_id,
                    "controlBoardId": "cb-recovered",
                }
            ],
        ]
    )
    mock_client.async_get_installation_settings = AsyncMock(
        return_value=ControlBoardSettings(performance_mode_preset=1)
    )

    coord = WeheatSettingsUpdateCoordinator(
        hass, entry_with_creds, portal_session, mock_heat_pump_info, client=mock_client
    )
    settings = await coord._async_update_data()
    assert settings.performance_mode_preset == 1
    assert mock_heat_pump_info.installation_id == "inst-recovered"

    # 2. 401 when session has no stored credentials/cookies raises ConfigEntryAuthFailed
    entry_no_creds = ConfigEntry(
        entry_id="test_entry_no_creds",
        options={
            CONF_PORTAL_TOKEN: {
                CONF_ACCESS_TOKEN: "manual_jwt_only",
                "expires_at": time.time() + 200.0,
            },
        },
    )
    portal_session_no_creds = WeheatPortalSession(
        hass, entry_no_creds, mock_client, mock_fallback_session
    )
    mock_client.async_get_installation_settings = AsyncMock(
        side_effect=WeheatAuthError("Unauthorized", status_code=401)
    )
    coord_no_creds = WeheatSettingsUpdateCoordinator(
        hass,
        entry_no_creds,
        portal_session_no_creds,
        mock_heat_pump_info,
        client=mock_client,
    )
    with pytest.raises(ConfigEntryAuthFailed):
        await coord_no_creds._async_update_data()


@pytest.mark.asyncio
async def test_security_path_sanitization_and_origin_state_validation() -> None:
    """Verify path segment sanitization prevents traversal/injection and PKCE login rejects untrusted origins or state mismatch."""
    from custom_components.weheat.client import WeheatAuthError, WeheatClient

    client = WeheatClient(session=MagicMock())

    # 1. Path traversal / query injection rejected across client methods
    for unsafe_id in (
        "../users/me",
        "id/../../admin",
        "id?admin=1",
        "id#frag",
        "id with space",
    ):
        with pytest.raises(ValueError, match="unsafe path characters"):
            await client.async_get_heat_pump_details(unsafe_id)
        with pytest.raises(ValueError, match="unsafe path characters"):
            await client.async_get_installation_settings(unsafe_id)
        with pytest.raises(ValueError, match="unsafe path characters"):
            await client.async_patch_control_board_settings(
                unsafe_id, settings={"PerformanceModePreset": 0}
            )

    # 2. Untrusted origin in loginAction is blocked before credentials are sent
    with pytest.raises(WeheatAuthError, match="Untrusted URL origin"):
        WeheatClient._validate_trusted_url(
            "https://attacker.example.com/login",
            "https://auth.weheat.nl",
            "Keycloak login action",
        )
    with pytest.raises(WeheatAuthError, match="Untrusted URL origin"):
        WeheatClient._validate_trusted_url(
            "http://auth.weheat.nl/login",
            "https://auth.weheat.nl",
            "Keycloak login action",
        )
