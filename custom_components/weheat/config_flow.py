"""Config flow for Weheat custom integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any, override

from homeassistant.config_entries import (
    SOURCE_REAUTH,
    ConfigEntry,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_TOKEN
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.config_entry_oauth2_flow import AbstractOAuth2FlowHandler

from .client import WeheatClient, WeheatError
from .const import (
    CONF_PORTAL_PASSWORD,
    CONF_PORTAL_SESSION_TOKEN,
    CONF_PORTAL_TOKEN,
    CONF_PORTAL_USERNAME,
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

    @staticmethod
    def async_get_options_flow(
        config_entry: ConfigEntry,
    ) -> WeheatOptionsFlowHandler:
        """Return the options flow handler for configuring Weheat Portal credentials."""
        return WeheatOptionsFlowHandler(config_entry)


class WeheatOptionsFlowHandler(OptionsFlow):
    """Options flow for configuring Weheat Portal PKCE or session authentication."""

    def __init__(self, config_entry: ConfigEntry) -> None:
        """Initialize the Weheat options flow handler."""
        super().__init__()
        self._config_entry = config_entry

    @staticmethod
    def _build_schema(current_username: str = "") -> Any:
        """Build voluptuous schema if voluptuous is available."""
        try:
            import voluptuous as vol

            return vol.Schema(
                {
                    vol.Optional(CONF_PORTAL_USERNAME, default=current_username): str,
                    vol.Optional(CONF_PORTAL_PASSWORD, default=""): str,
                    vol.Optional(CONF_PORTAL_SESSION_TOKEN, default=""): str,
                }
            )
        except ImportError:
            return None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage Weheat Portal authentication options."""
        errors: dict[str, str] = {}
        existing_options = dict(getattr(self._config_entry, "options", None) or {})
        current_username = str(existing_options.get(CONF_PORTAL_USERNAME, ""))

        if user_input is not None:
            username = str(user_input.get(CONF_PORTAL_USERNAME, "")).strip()
            password = str(user_input.get(CONF_PORTAL_PASSWORD, ""))
            raw_session = str(user_input.get(CONF_PORTAL_SESSION_TOKEN, "")).strip()

            session = async_get_clientsession(getattr(self, "hass", None))
            client = WeheatClient(
                session=session,
                base_url=DEFAULT_API_BASE_URL,
                portal_base_url=DEFAULT_PORTAL_BASE_URL,
            )

            if username and password:
                try:
                    portal_token = await client.async_login_portal_pkce(
                        username=username,
                        password=password,
                    )
                    new_options = dict(existing_options)
                    new_options[CONF_PORTAL_USERNAME] = username
                    new_options[CONF_PORTAL_PASSWORD] = password
                    new_options[CONF_PORTAL_TOKEN] = portal_token
                    return self.async_create_entry(title="", data=new_options)
                except WeheatError as err:
                    _LOGGER.error("Weheat Portal PKCE login failed: %s", err)
                    errors["base"] = "invalid_portal_auth"
                except Exception as err:
                    _LOGGER.exception(
                        "Unexpected error during Weheat Portal PKCE login: %s", err
                    )
                    errors["base"] = "invalid_portal_auth"
            elif raw_session:
                try:
                    portal_token = WeheatClient.parse_portal_session_input(raw_session)
                    await client.async_get_installation_topology(
                        token=portal_token[CONF_ACCESS_TOKEN]
                    )
                    new_options = dict(existing_options)
                    new_options[CONF_PORTAL_TOKEN] = portal_token
                    return self.async_create_entry(title="", data=new_options)
                except (ValueError, WeheatError) as err:
                    _LOGGER.error(
                        "Weheat Portal session token validation failed: %s", err
                    )
                    errors["base"] = "invalid_portal_auth"
                except Exception as err:
                    _LOGGER.exception(
                        "Unexpected error validating Weheat Portal session token: %s",
                        err,
                    )
                    errors["base"] = "invalid_portal_auth"
            else:
                # Clear portal credentials if submitted empty
                new_options = dict(existing_options)
                new_options.pop(CONF_PORTAL_USERNAME, None)
                new_options.pop(CONF_PORTAL_PASSWORD, None)
                new_options.pop(CONF_PORTAL_TOKEN, None)
                return self.async_create_entry(title="", data=new_options)

        return self.async_show_form(
            step_id="init",
            data_schema=self._build_schema(current_username),
            errors=errors,
        )
