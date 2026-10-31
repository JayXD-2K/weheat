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
