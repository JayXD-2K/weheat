"""Tests for Weheat config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_TOKEN

from custom_components.weheat.client import WeheatAuthError
from custom_components.weheat.config_flow import OAuth2FlowHandler
from custom_components.weheat.const import (
    DOMAIN,
    ENTRY_TITLE,
    OAUTH2_SCOPES,
)
from tests.ha_compat import FlowAbortException


@pytest.fixture
def flow_handler() -> OAuth2FlowHandler:
    handler = OAuth2FlowHandler()
    return handler


def test_flow_handler_domain_and_scopes(flow_handler: OAuth2FlowHandler) -> None:
    """Verify flow handler domain and extra authorize data scopes."""
    assert flow_handler.DOMAIN == DOMAIN
    assert flow_handler.extra_authorize_data == {"scope": " ".join(OAUTH2_SCOPES)}


@pytest.mark.asyncio
async def test_oauth_create_entry_success(flow_handler: OAuth2FlowHandler) -> None:
    """Verify successful entry creation sets unique ID and title."""
    token_data = {
        CONF_TOKEN: {
            CONF_ACCESS_TOKEN: "mock_test_token",
        }
    }
    mock_user_id = "00000000-0000-0000-0000-000000000001"

    with patch(
        "custom_components.weheat.config_flow.WeheatClient.async_get_user_id",
        new=AsyncMock(return_value=mock_user_id),
    ):
        result = await flow_handler.async_oauth_create_entry(token_data)

    assert result["type"] == "create_entry"
    assert result["title"] == ENTRY_TITLE
    assert result["data"] == token_data
    assert flow_handler.unique_id == mock_user_id


@pytest.mark.asyncio
async def test_oauth_create_entry_user_id_error(
    flow_handler: OAuth2FlowHandler,
) -> None:
    """Verify abort with oauth_failed on client error."""
    token_data = {
        CONF_TOKEN: {
            CONF_ACCESS_TOKEN: "mock_test_token",
        }
    }

    with patch(
        "custom_components.weheat.config_flow.WeheatClient.async_get_user_id",
        new=AsyncMock(side_effect=WeheatAuthError("Invalid token")),
    ):
        result = await flow_handler.async_oauth_create_entry(token_data)

    assert result["type"] == "abort"
    assert result["reason"] == "oauth_failed"


@pytest.mark.asyncio
async def test_oauth_create_entry_empty_user_id(
    flow_handler: OAuth2FlowHandler,
) -> None:
    """Verify abort with oauth_failed when user ID is empty."""
    token_data = {
        CONF_TOKEN: {
            CONF_ACCESS_TOKEN: "mock_test_token",
        }
    }

    with patch(
        "custom_components.weheat.config_flow.WeheatClient.async_get_user_id",
        new=AsyncMock(return_value=None),
    ):
        result = await flow_handler.async_oauth_create_entry(token_data)

    assert result["type"] == "abort"
    assert result["reason"] == "oauth_failed"


@pytest.mark.asyncio
async def test_oauth_create_entry_already_configured(
    flow_handler: OAuth2FlowHandler,
) -> None:
    """Verify abort with already_configured if unique ID is already known."""
    mock_user_id = "00000000-0000-0000-0000-000000000001"
    flow_handler.configured_unique_ids.add(mock_user_id)

    token_data = {
        CONF_TOKEN: {
            CONF_ACCESS_TOKEN: "mock_test_token",
        }
    }

    with (
        patch(
            "custom_components.weheat.config_flow.WeheatClient.async_get_user_id",
            new=AsyncMock(return_value=mock_user_id),
        ),
        pytest.raises(FlowAbortException) as excinfo,
    ):
        await flow_handler.async_oauth_create_entry(token_data)

    assert excinfo.value.reason == "already_configured"


@pytest.mark.asyncio
async def test_oauth_reauth_success(flow_handler: OAuth2FlowHandler) -> None:
    """Verify successful reauth when user ID matches existing entry."""
    mock_user_id = "00000000-0000-0000-0000-000000000001"
    existing_entry = ConfigEntry(
        entry_id="reauth_entry",
        data={CONF_TOKEN: {CONF_ACCESS_TOKEN: "old_token"}},
        unique_id=mock_user_id,
    )
    flow_handler.source = SOURCE_REAUTH
    flow_handler._reauth_entry = existing_entry

    new_data = {
        CONF_TOKEN: {
            CONF_ACCESS_TOKEN: "new_refreshed_token",
        }
    }

    with patch(
        "custom_components.weheat.config_flow.WeheatClient.async_get_user_id",
        new=AsyncMock(return_value=mock_user_id),
    ):
        result = await flow_handler.async_oauth_create_entry(new_data)

    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    assert existing_entry.data[CONF_TOKEN][CONF_ACCESS_TOKEN] == "new_refreshed_token"


@pytest.mark.asyncio
async def test_oauth_reauth_wrong_account(flow_handler: OAuth2FlowHandler) -> None:
    """Verify reauth aborts with wrong_account when user ID does not match."""
    existing_entry = ConfigEntry(
        entry_id="reauth_entry",
        unique_id="00000000-0000-0000-0000-000000000001",
    )
    flow_handler.source = SOURCE_REAUTH
    flow_handler._reauth_entry = existing_entry

    token_data = {
        CONF_TOKEN: {
            CONF_ACCESS_TOKEN: "different_user_token",
        }
    }

    with (
        patch(
            "custom_components.weheat.config_flow.WeheatClient.async_get_user_id",
            new=AsyncMock(return_value="00000000-0000-0000-0000-000000000002"),
        ),
        pytest.raises(FlowAbortException) as excinfo,
    ):
        await flow_handler.async_oauth_create_entry(token_data)

    assert excinfo.value.reason == "wrong_account"


@pytest.mark.asyncio
async def test_reauth_steps(flow_handler: OAuth2FlowHandler) -> None:
    """Verify reauth step shows reauth_confirm form."""
    result = await flow_handler.async_step_reauth({})
    assert result["type"] == "form"
    assert result["step_id"] == "reauth_confirm"

    # User submits confirmation dialog
    confirm_result = await flow_handler.async_step_reauth_confirm(user_input={})
    assert confirm_result["type"] == "external"


@pytest.mark.asyncio
async def test_options_flow_portal_pkce_success() -> None:
    """Verify options flow authenticates via portal PKCE and saves portal_token."""
    entry = ConfigEntry(entry_id="opt_entry", options={})
    opt_flow = OAuth2FlowHandler.async_get_options_flow(entry)

    # Initial form
    form_res = await opt_flow.async_step_init(None)
    assert form_res["type"] == "form"
    assert form_res["step_id"] == "init"

    mock_portal_token = {
        "access_token": "portal_jwt_token",
        "refresh_token": "portal_refresh_token",
        "expires_at": 9999999999.0,
    }
    with patch(
        "custom_components.weheat.config_flow.WeheatClient.async_login_portal_pkce",
        new=AsyncMock(return_value=mock_portal_token),
    ):
        res = await opt_flow.async_step_init(
            {
                "portal_username": "user@example.com",
                "portal_password": "secret_password",
            }
        )

    assert res["type"] == "create_entry"
    assert res["data"]["portal_username"] == "user@example.com"
    assert res["data"]["portal_token"] == mock_portal_token


@pytest.mark.asyncio
async def test_options_flow_portal_session_json_success() -> None:
    """Verify options flow accepts NextAuth /api/auth/session JSON."""
    entry = ConfigEntry(entry_id="opt_entry", options={})
    opt_flow = OAuth2FlowHandler.async_get_options_flow(entry)

    with patch(
        "custom_components.weheat.config_flow.WeheatClient.async_get_installation_topology",
        new=AsyncMock(return_value=[]),
    ):
        res = await opt_flow.async_step_init(
            {
                "portal_session_token": '{"accessToken": "session_jwt_123"}',
            }
        )

    assert res["type"] == "create_entry"
    assert res["data"]["portal_token"]["access_token"] == "session_jwt_123"


@pytest.mark.asyncio
async def test_options_flow_portal_auth_error() -> None:
    """Verify options flow surfaces invalid_portal_auth on PKCE failure."""
    entry = ConfigEntry(entry_id="opt_entry", options={})
    opt_flow = OAuth2FlowHandler.async_get_options_flow(entry)

    with patch(
        "custom_components.weheat.config_flow.WeheatClient.async_login_portal_pkce",
        new=AsyncMock(side_effect=WeheatAuthError("Bad credentials")),
    ):
        res = await opt_flow.async_step_init(
            {
                "portal_username": "user@example.com",
                "portal_password": "wrong",
            }
        )

    assert res["type"] == "form"
    assert res["errors"] == {"base": "invalid_portal_auth"}
