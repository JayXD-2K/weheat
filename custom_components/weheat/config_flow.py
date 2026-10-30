"""Config flow for Weheat custom integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, override

from homeassistant.config_entries import SOURCE_REAUTH, ConfigFlowResult
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_TOKEN
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.config_entry_oauth2_flow import AbstractOAuth2FlowHandler

from .client import WeheatClient, WeheatError
from .const import (
    DEFAULT_API_BASE_URL,
    DEFAULT_PORTAL_BASE_URL,
    DOMAIN,
    ENTRY_TITLE,
    OAUTH2_SCOPES,
)

_LOGGER = logging.getLogger(__name__)


class OAuth2FlowHandler(AbstractOAuth2FlowHandler, domain=DOMAIN):
    """Config flow to handle Weheat OAuth2 PKCE authentication."""

    DOMAIN = DOMAIN

    @property
    @override
    def logger(self) -> logging.Logger:
        """Return logger."""
        return _LOGGER

    @property
    @override
    def extra_authorize_data(self) -> dict[str, str]:
        """Extra data that needs to be appended to the authorize url."""
        return {
            "scope": " ".join(OAUTH2_SCOPES),
        }

    @override
    async def async_oauth_create_entry(self, data: dict[str, Any]) -> ConfigFlowResult:
        """Create entry after OAuth token exchange and user UUID verification."""
        session = async_get_clientsession(self.hass)
        client = WeheatClient(
            session=session,
            base_url=DEFAULT_API_BASE_URL,
            portal_base_url=DEFAULT_PORTAL_BASE_URL,
        )
        token = data[CONF_TOKEN][CONF_ACCESS_TOKEN]

        try:
            user_id = await client.async_get_user_id(token=token)
        except WeheatError as err:
            self.logger.error("Failed to get user ID from Weheat API: %s", err)
            return self.async_abort(reason="oauth_failed")
        except Exception as err:
            self.logger.exception(
                "Unexpected error getting user ID from Weheat API: %s", err
            )
            return self.async_abort(reason="oauth_failed")

        if not user_id:
            return self.async_abort(reason="oauth_failed")

        await self.async_set_unique_id(user_id)

        if self.source != SOURCE_REAUTH:
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=ENTRY_TITLE, data=data)

        self._abort_if_unique_id_mismatch(reason="wrong_account")
        return self.async_update_reload_and_abort(
            self._get_reauth_entry(), data_updates=data
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Perform reauth upon an API authentication error."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm reauth dialog."""
        if user_input is None:
            return self.async_show_form(step_id="reauth_confirm")
        return await self.async_step_user()
