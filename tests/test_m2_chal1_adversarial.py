"""Adversarial Empirical Stress Test Suite for Milestone 2.

Conducted by Challenger 1 (critic, specialist) to stress-test:
1. Default credentials auto-seeding when no credentials exist:
   - Official Client ID: "HomeAssistantAPI"
   - Official Client Secret: "TqpNpiJDKbGXF8jaL9D1Y8yzl1pI1Fly"
   - Keycloak realm URLs and PKCE setup
   - End-to-end flow with auto-seeding through entry creation
2. Existing custom credentials preservation:
   - Single custom credential: must NOT be overwritten, clobbered, or touched
   - Multiple custom credentials: all must be retained
   - Custom implementations with unique attributes
3. Fallback behavior when application_credentials component is unavailable or encounters errors:
   - ImportError / ModuleNotFoundError when importing application_credentials
   - Runtime storage errors (RuntimeError, ValueError, OSError, PermissionError)
   - Failures in async_get_implementations
   - Total cascade failure resilience (both app_credentials and direct registration fail)
4. Idempotency & Concurrency Stress:
   - Repeated sequential calls on same flow handler
   - Repeated sequential calls across distinct flow handlers
   - High-concurrency burst (asyncio.gather)
   - Reauth confirmation idempotency
5. Integration with Home Assistant OAuth2 implementation registration:
   - application_credentials.async_get_auth_implementation contract & PKCE conformance
   - Custom credential passing through platform hook
   - Exact Keycloak realm endpoint trailing slash verification
   - Scopes and extra authorize data
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.components.application_credentials import ClientCredential
from homeassistant.config_entries import SOURCE_REAUTH, ConfigEntry
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.config_entry_oauth2_flow import (
    LocalOAuth2ImplementationWithPkce,
)

from custom_components.weheat import application_credentials as app_cred
from custom_components.weheat.client import WeheatClient
from custom_components.weheat.config_flow import OAuth2FlowHandler
from custom_components.weheat.const import (
    DEFAULT_CLIENT_ID,
    DEFAULT_CLIENT_SECRET,
    DEFAULT_OAUTH_CLIENT_ID,
    DEFAULT_OAUTH_CLIENT_SECRET,
    DOMAIN,
    ENTRY_TITLE,
    OAUTH2_AUTHORIZE,
    OAUTH2_CLIENT_ID,
    OAUTH2_SCOPES,
    OAUTH2_TOKEN,
    PORTAL_CLIENT_ID,
)
from tests.ha_compat import (
    _OAUTH_IMPLEMENTATIONS,
    async_get_implementations,
    async_register_implementation,
)


@pytest.fixture(autouse=True)
def clean_oauth_implementations():
    """Ensure OAuth implementation registry is isolated before and after each test."""
    _OAUTH_IMPLEMENTATIONS.clear()
    yield
    _OAUTH_IMPLEMENTATIONS.clear()


# ============================================================================
# Section 1: Default Credentials Auto-Seeding When No Credentials Exist
# ============================================================================


class TestDefaultCredentialsAutoSeeding:
    """Stress tests for auto-seeding official public credentials."""

    def test_oauth_constants_exact_values(self) -> None:
        """Verify official credentials and Keycloak endpoints match Weheat specification."""
        assert DEFAULT_CLIENT_ID == "HomeAssistantAPI"
        assert DEFAULT_CLIENT_SECRET == "TqpNpiJDKbGXF8jaL9D1Y8yzl1pI1Fly"
        assert DEFAULT_OAUTH_CLIENT_ID == "HomeAssistantAPI"
        assert DEFAULT_OAUTH_CLIENT_SECRET == "TqpNpiJDKbGXF8jaL9D1Y8yzl1pI1Fly"
        assert OAUTH2_CLIENT_ID == "HomeAssistantAPI"
        assert PORTAL_CLIENT_ID == "weheat-portal"

        # Keycloak OpenID-Connect realm endpoints MUST match the Weheat realm and end in trailing slash
        assert (
            OAUTH2_AUTHORIZE
            == "https://auth.weheat.nl/auth/realms/Weheat/protocol/openid-connect/auth/"
        )
        assert (
            OAUTH2_TOKEN
            == "https://auth.weheat.nl/auth/realms/Weheat/protocol/openid-connect/token/"
        )
        assert OAUTH2_SCOPES == ["openid", "offline_access"]

    @pytest.mark.asyncio
    async def test_auto_seed_invoked_on_empty_registry(self) -> None:
        """Verify _async_ensure_default_credentials imports official credentials when registry is empty."""
        handler = OAuth2FlowHandler()
        assert await async_get_implementations(handler.hass, DOMAIN) == {}

        mock_import = AsyncMock()
        with patch(
            "homeassistant.components.application_credentials.async_import_client_credential",
            new=mock_import,
        ):
            await handler._async_ensure_default_credentials()

        mock_import.assert_awaited_once()
        args, kwargs = mock_import.await_args
        assert args[0] is handler.hass
        assert args[1] == DOMAIN

        credential_arg: ClientCredential = args[2]
        assert isinstance(credential_arg, ClientCredential)
        assert credential_arg.client_id == "HomeAssistantAPI"
        assert credential_arg.client_secret == "TqpNpiJDKbGXF8jaL9D1Y8yzl1pI1Fly"
        assert credential_arg.name == "Weheat (Default)"

    @pytest.mark.asyncio
    async def test_auto_seed_live_registration_produces_valid_implementation(
        self,
    ) -> None:
        """Verify auto-seeding without mocking yields a functioning LocalOAuth2ImplementationWithPkce."""
        handler = OAuth2FlowHandler()
        assert await async_get_implementations(handler.hass, DOMAIN) == {}

        # Run user step
        result = await handler.async_step_user()
        assert result["type"] == "external"
        assert result["step_id"] == "auth"

        # Check implementation in registry
        impls = await async_get_implementations(handler.hass, DOMAIN)
        assert len(impls) == 1
        impl = next(iter(impls.values()))

        assert isinstance(impl, LocalOAuth2ImplementationWithPkce)
        assert impl.client_id == "HomeAssistantAPI"
        assert impl.client_secret == "TqpNpiJDKbGXF8jaL9D1Y8yzl1pI1Fly"
        assert (
            impl.authorize_url
            == "https://auth.weheat.nl/auth/realms/Weheat/protocol/openid-connect/auth/"
        )
        assert (
            impl.token_url
            == "https://auth.weheat.nl/auth/realms/Weheat/protocol/openid-connect/token/"
        )

    @pytest.mark.asyncio
    async def test_e2e_flow_with_auto_seeded_credentials_to_entry_creation(
        self,
    ) -> None:
        """Simulate complete user flow starting from unseeded state to successful entry creation."""
        handler = OAuth2FlowHandler()
        mock_user_uuid = "11111111-2222-3333-4444-555555555555"

        # 1. User starts flow
        step_user_res = await handler.async_step_user()
        assert step_user_res["type"] == "external"

        # 2. Token received from OAuth exchange
        token_payload = {
            CONF_TOKEN: {
                CONF_ACCESS_TOKEN: "mock_jwt_access_token_xyz",
                "refresh_token": "mock_jwt_refresh_token_abc",
                "expires_in": 3600,
            }
        }

        with patch.object(
            WeheatClient,
            "async_get_user_id",
            new=AsyncMock(return_value=mock_user_uuid),
        ):
            entry_res = await handler.async_oauth_create_entry(token_payload)

        assert entry_res["type"] == "create_entry"
        assert entry_res["title"] == ENTRY_TITLE
        assert entry_res["data"] == token_payload
        assert handler.unique_id == mock_user_uuid


# ============================================================================
# Section 2: Existing Custom Credentials Preservation
# ============================================================================


class TestCustomCredentialsPreservation:
    """Stress tests verifying existing custom credentials are NEVER overwritten or clobbered."""

    @pytest.mark.asyncio
    async def test_single_custom_credential_unmodified(self) -> None:
        """Verify that existing custom credentials prevent default credential seeding."""
        hass = HomeAssistant()
        custom_impl = LocalOAuth2ImplementationWithPkce(
            hass=hass,
            auth_domain=DOMAIN,
            client_id="CustomWeheatClientID",
            authorize_url="https://custom.auth.local/auth",
            token_url="https://custom.auth.local/token",
            client_secret="CustomSuperSecretKey999",
        )
        custom_impl.name = "My Personal Weheat App"
        async_register_implementation(hass, DOMAIN, custom_impl)

        handler = OAuth2FlowHandler()
        handler.hass = hass

        mock_import = AsyncMock()
        with patch(
            "homeassistant.components.application_credentials.async_import_client_credential",
            new=mock_import,
        ):
            result = await handler.async_step_user()

        assert result["type"] == "external"
        # Must NOT attempt to import default credentials
        mock_import.assert_not_called()

        # Implementation registry MUST remain unchanged
        impls = await async_get_implementations(hass, DOMAIN)
        assert len(impls) == 1
        assert "My Personal Weheat App" in impls
        registered = impls["My Personal Weheat App"]
        assert registered.client_id == "CustomWeheatClientID"
        assert registered.client_secret == "CustomSuperSecretKey999"
        assert registered.authorize_url == "https://custom.auth.local/auth"

    @pytest.mark.asyncio
    async def test_multiple_custom_credentials_preserved(self) -> None:
        """Verify that multiple custom credentials in application credentials are all retained."""
        hass = HomeAssistant()
        custom1 = LocalOAuth2ImplementationWithPkce(
            hass,
            DOMAIN,
            "ClientAlpha",
            OAUTH2_AUTHORIZE,
            OAUTH2_TOKEN,
            "SecretAlpha",
        )
        custom1.name = "CustomAlpha"

        custom2 = LocalOAuth2ImplementationWithPkce(
            hass, DOMAIN, "ClientBeta", OAUTH2_AUTHORIZE, OAUTH2_TOKEN, "SecretBeta"
        )
        custom2.name = "CustomBeta"

        async_register_implementation(hass, DOMAIN, custom1)
        async_register_implementation(hass, DOMAIN, custom2)

        handler = OAuth2FlowHandler()
        handler.hass = hass

        mock_import = AsyncMock()
        with patch(
            "homeassistant.components.application_credentials.async_import_client_credential",
            new=mock_import,
        ):
            result = await handler.async_step_user()

        assert result["type"] == "external"
        mock_import.assert_not_called()

        impls = await async_get_implementations(hass, DOMAIN)
        assert len(impls) == 2
        assert set(impls.keys()) == {"CustomAlpha", "CustomBeta"}
        assert impls["CustomAlpha"].client_id == "ClientAlpha"
        assert impls["CustomBeta"].client_id == "ClientBeta"

    @pytest.mark.asyncio
    async def test_custom_credentials_with_different_domain_ignored(self) -> None:
        """Verify that custom credentials belonging to another integration do not block Weheat defaults."""
        hass = HomeAssistant()
        other_impl = LocalOAuth2ImplementationWithPkce(
            hass, "other_domain", "OtherClient", "auth_url", "token_url", "OtherSecret"
        )
        other_impl.name = "OtherApp"
        async_register_implementation(hass, "other_domain", other_impl)

        handler = OAuth2FlowHandler()
        handler.hass = hass

        result = await handler.async_step_user()
        assert result["type"] == "external"

        weheat_impls = await async_get_implementations(hass, DOMAIN)
        assert len(weheat_impls) == 1
        impl = next(iter(weheat_impls.values()))
        assert impl.client_id == "HomeAssistantAPI"

        # Other domain untouched
        other_impls = await async_get_implementations(hass, "other_domain")
        assert len(other_impls) == 1
        assert "OtherApp" in other_impls


# ============================================================================
# Section 3: Fallback Behavior When application_credentials Fails
# ============================================================================


class TestApplicationCredentialsFallback:
    """Stress tests for fallback to direct async_register_implementation on component failures."""

    @pytest.mark.asyncio
    async def test_fallback_on_import_error(self) -> None:
        """Simulate application_credentials package being absent / raising ImportError."""
        handler = OAuth2FlowHandler()

        # Simulate builtins.__import__ raising ImportError for application_credentials
        orig_import = __import__

        def fake_import(name, *args, **kwargs):
            if "application_credentials" in name:
                raise ImportError(
                    "No module named 'homeassistant.components.application_credentials'"
                )
            return orig_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            result = await handler.async_step_user()

        assert result["type"] == "external"

        # Implementation MUST have been registered via direct LocalOAuth2ImplementationWithPkce fallback
        impls = await async_get_implementations(handler.hass, DOMAIN)
        assert len(impls) == 1
        impl = next(iter(impls.values()))
        assert impl.client_id == "HomeAssistantAPI"
        assert impl.client_secret == "TqpNpiJDKbGXF8jaL9D1Y8yzl1pI1Fly"
        assert impl.authorize_url == OAUTH2_AUTHORIZE
        assert impl.token_url == OAUTH2_TOKEN

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "raised_exception",
        [
            RuntimeError("Storage collection unavailable"),
            ValueError("Corrupted application credentials JSON"),
            OSError("Read-only filesystem error"),
            PermissionError("Storage directory permission denied"),
            TypeError("Unexpected type during credential storage"),
            KeyError("Missing storage key"),
        ],
    )
    async def test_fallback_on_various_storage_exceptions(
        self, raised_exception: Exception
    ) -> None:
        """Verify that any exception raised by async_import_client_credential cleanly triggers fallback."""
        handler = OAuth2FlowHandler()

        with patch(
            "homeassistant.components.application_credentials.async_import_client_credential",
            side_effect=raised_exception,
        ):
            result = await handler.async_step_user()

        assert result["type"] == "external"

        impls = await async_get_implementations(handler.hass, DOMAIN)
        assert len(impls) == 1
        impl = next(iter(impls.values()))
        assert impl.client_id == DEFAULT_CLIENT_ID
        assert impl.client_secret == DEFAULT_CLIENT_SECRET

    @pytest.mark.asyncio
    async def test_resilience_when_async_get_implementations_raises(self) -> None:
        """Verify that exceptions raised by async_get_implementations do not crash the flow."""
        handler = OAuth2FlowHandler()

        # First call to get_implementations raises, second succeeds
        call_count = 0

        async def faulty_get_implementations(hass, domain):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("Registry lock timeout")
            return await async_get_implementations(hass, domain)

        with (
            patch(
                "homeassistant.helpers.config_entry_oauth2_flow.async_get_implementations",
                side_effect=faulty_get_implementations,
            ),
        ):
            result = await handler.async_step_user()

        assert result["type"] == "external"
        impls = await async_get_implementations(handler.hass, DOMAIN)
        assert len(impls) == 1

    @pytest.mark.asyncio
    async def test_total_cascade_failure_does_not_raise(self) -> None:
        """Verify that even if BOTH app_credentials import and direct registration fail, user step recovers."""
        handler = OAuth2FlowHandler()

        with (
            patch(
                "homeassistant.components.application_credentials.async_import_client_credential",
                side_effect=RuntimeError("Storage dead"),
            ),
            patch(
                "homeassistant.helpers.config_entry_oauth2_flow.async_register_implementation",
                side_effect=RuntimeError("Registry dead"),
            ),
        ):
            # Must not crash with unhandled exception
            result = await handler.async_step_user()

        assert result is not None
        assert result["type"] == "external"


# ============================================================================
# Section 4: Idempotency & Concurrency Stress Testing
# ============================================================================


class TestIdempotencyAndConcurrency:
    """Stress tests verifying idempotency under rapid sequential and concurrent execution."""

    @pytest.mark.asyncio
    async def test_repeated_sequential_step_user_invocations(self) -> None:
        """Verify that calling async_step_user 10 times sequentially only imports once."""
        handler = OAuth2FlowHandler()
        mock_import = AsyncMock()

        # First invocation seeds credentials
        result1 = await handler.async_step_user()
        assert result1["type"] == "external"

        # Invocations 2 to 10
        with patch(
            "homeassistant.components.application_credentials.async_import_client_credential",
            new=mock_import,
        ):
            for _ in range(2, 11):
                res = await handler.async_step_user()
                assert res["type"] == "external"

        # Mock import was never called during subsequent runs
        mock_import.assert_not_called()

        # Implementation registry still has exactly 1 implementation
        impls = await async_get_implementations(handler.hass, DOMAIN)
        assert len(impls) == 1

    @pytest.mark.asyncio
    async def test_multiple_independent_handler_instances(self) -> None:
        """Verify that multiple distinct flow handlers do not create duplicate registrations."""
        handlers = [OAuth2FlowHandler() for _ in range(15)]
        # Share the same hass instance
        shared_hass = HomeAssistant()
        for h in handlers:
            h.hass = shared_hass

        for h in handlers:
            res = await h.async_step_user()
            assert res["type"] == "external"

        impls = await async_get_implementations(shared_hass, DOMAIN)
        assert len(impls) == 1
        impl = next(iter(impls.values()))
        assert impl.client_id == DEFAULT_CLIENT_ID

    @pytest.mark.asyncio
    async def test_high_concurrency_burst(self) -> None:
        """Stress test 30 concurrent async_step_user invocations via asyncio.gather."""
        shared_hass = HomeAssistant()
        handlers = [OAuth2FlowHandler() for _ in range(30)]
        for h in handlers:
            h.hass = shared_hass

        results = await asyncio.gather(*[h.async_step_user() for h in handlers])

        assert len(results) == 30
        for res in results:
            assert res["type"] == "external"
            assert res["step_id"] == "auth"

        impls = await async_get_implementations(shared_hass, DOMAIN)
        assert len(impls) == 1
        impl = next(iter(impls.values()))
        assert impl.client_id == DEFAULT_CLIENT_ID
        assert impl.client_secret == DEFAULT_CLIENT_SECRET

    @pytest.mark.asyncio
    async def test_reauth_step_idempotency_and_preservation(self) -> None:
        """Verify reauth flow steps preserve implementations and operate idempotently."""
        handler = OAuth2FlowHandler()
        reauth_entry = ConfigEntry(
            entry_id="reauth_test_123",
            data={
                CONF_TOKEN: {
                    CONF_ACCESS_TOKEN: "old_token",
                    "refresh_token": "old_refresh",
                }
            },
            unique_id="user_uuid_999",
        )
        handler.source = SOURCE_REAUTH
        handler._reauth_entry = reauth_entry

        # User confirms reauth multiple times
        for _ in range(3):
            confirm_res = await handler.async_step_reauth_confirm(user_input={})
            assert confirm_res["type"] == "external"

        impls = await async_get_implementations(handler.hass, DOMAIN)
        assert len(impls) == 1


# ============================================================================
# Section 5: Integration with Home Assistant OAuth2 Implementation Registration
# ============================================================================


class TestOAuth2ImplementationRegistration:
    """Stress tests for application_credentials platform hook and PKCE protocol compliance."""

    @pytest.mark.asyncio
    async def test_application_credentials_platform_contract(self) -> None:
        """Verify that async_get_auth_implementation adheres to Home Assistant's platform contract."""
        hass = HomeAssistant()
        credential = ClientCredential(
            client_id="TestHookClient",
            client_secret="TestHookSecret",
            name="TestHook",
        )

        impl = await app_cred.async_get_auth_implementation(hass, DOMAIN, credential)

        assert isinstance(impl, LocalOAuth2ImplementationWithPkce)
        assert impl.hass is hass
        assert impl.auth_domain == DOMAIN
        assert impl.client_id == "TestHookClient"
        assert impl.client_secret == "TestHookSecret"
        assert impl.authorize_url == OAUTH2_AUTHORIZE
        assert impl.token_url == OAUTH2_TOKEN

    @pytest.mark.asyncio
    async def test_application_credentials_platform_arbitrary_credentials(self) -> None:
        """Verify platform hook with arbitrary Unicode and special character credentials."""
        hass = HomeAssistant()
        credential = ClientCredential(
            client_id="Client-!@#$%^&*()_+~",
            client_secret="Secret-1234567890-abcdef=",
        )

        impl = await app_cred.async_get_auth_implementation(hass, DOMAIN, credential)
        assert impl.client_id == "Client-!@#$%^&*()_+~"
        assert impl.client_secret == "Secret-1234567890-abcdef="

    def test_flow_handler_scopes_and_logger(self) -> None:
        """Verify flow handler logger and extra_authorize_data formatting."""
        handler = OAuth2FlowHandler()
        assert handler.logger.name == "custom_components.weheat.config_flow"
        assert handler.extra_authorize_data == {
            "scope": "openid offline_access",
        }

    @pytest.mark.asyncio
    async def test_options_update_listener_and_portal_session_lifecycle(self) -> None:
        from custom_components.weheat import async_reload_entry, async_setup_entry
        from custom_components.weheat.coordinator import WeheatPortalSession
        from custom_components.weheat.models import HeatPumpInfo

        hass = HomeAssistant()
        entry = ConfigEntry(
            entry_id="integration_m2_entry",
            data={
                CONF_TOKEN: {
                    CONF_ACCESS_TOKEN: "valid_oauth_token",
                    "refresh_token": "valid_refresh_token",
                }
            },
            options={},
            unique_id="user_m2_test",
        )
        mock_hp = HeatPumpInfo(
            heatpump_id="00000000-0000-0000-0000-000000000001",
            name="Test Heat Pump",
            model=0,
            serial_number="WH-001",
            has_dhw=True,
        )

        with (
            patch(
                "custom_components.weheat.OAuth2Session.async_ensure_token_valid",
                new=AsyncMock(),
            ),
            patch.object(
                WeheatClient,
                "async_get_heat_pumps",
                new=AsyncMock(return_value=[mock_hp]),
            ),
            patch.object(
                WeheatClient,
                "async_get_installation_topology",
                new=AsyncMock(return_value=[]),
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
            setup_success = await async_setup_entry(hass, entry)

        assert setup_success is True

        # Verify options update listener is registered on the entry
        assert len(entry.update_listeners) == 1

        # Trigger update listener and verify entry reload is dispatched
        await entry.update_listeners[0](hass, entry)
        assert "integration_m2_entry" in hass.config_entries.reloaded_entries

        # Verify async_reload_entry directly
        await async_reload_entry(hass, entry)
        assert hass.config_entries.reloaded_entries.count("integration_m2_entry") == 2

        # Verify WeheatPortalSession was instantiated and assigned to settings_coordinator
        assert len(entry.runtime_data) == 1
        hp_data = entry.runtime_data[0]
        control_session = hp_data.settings_coordinator.session
        assert isinstance(control_session, WeheatPortalSession)
        assert control_session.has_portal_auth is False

        # Verify check_portal_auth() guard
        with pytest.raises(HomeAssistantError) as exc_info:
            control_session.check_portal_auth()
        assert "Weheat Portal credentials required for control actions" in str(
            exc_info.value
        )
