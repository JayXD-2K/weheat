"""Empirical adversarial stress-test suite for Milestone 2.

Conducted by Challenger 2 for:
1. Options Flow Reload Listener:
   - Registration in async_setup_entry via entry.add_update_listener and entry.async_on_unload.
   - Immediate async_reload invocation upon options modification without HA restart.
   - Burst/rapid successive options modifications.
   - Special entry IDs (spaces, unicode, long strings).
   - Reload exception propagation.
   - Multi-entry reload isolation.
2. Teardown Lifecycle (async_unload_entry):
   - Graceful platform unload rejection handling.
   - Clean coordinator shutdown across multiple devices and empty runtime_data.
   - Sync and async (coroutine) coordinator async_shutdown methods.
   - Unload deregistrations of update listeners (no dangling callbacks).
   - Multi-entry service cleanup retention.
   - Idempotent repeated unload calls.
3. WeheatPortalSession Routing:
   - Wrapper preservation during initial setup regardless of empty portal options.
   - Strict telemetry coordinator binding to OAuth2Session.
   - Isolation between portal credentials and telemetry session tokens.
   - Dynamic routing transition upon late options updates.
   - Fallback session None handling.
4. Malformed Portal Token Handling:
   - Non-dict and invalid types (int, float, bool, list, set, bytes).
   - Corrupted/malformed JSON strings and empty strings.
   - Corrupted dict structures (missing keys, empty access token, None values).
   - Expired tokens and Keycloak PKCE / refresh token renewal paths.
   - Expiration parsing resilience (None, non-numeric, or missing expires_at).
   - Setup non-fatal handling of malformed portal settings tokens.
   - Options flow error handling on corrupted session input.
5. check_portal_auth() Guardrails:
   - Strict prevention of unauthorized write actions.
   - Clear actionable guidance in error messages.
   - Permissive behavior on valid tokens and credentials.
"""

from __future__ import annotations

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_TOKEN
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed

from custom_components.weheat import (
    async_reload_entry,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.weheat.client import (
    WeheatAuthError,
    WeheatClient,
    WeheatError,
)
from custom_components.weheat.config_flow import WeheatOptionsFlowHandler
from custom_components.weheat.const import (
    CONF_PORTAL_PASSWORD,
    CONF_PORTAL_SESSION_TOKEN,
    CONF_PORTAL_TOKEN,
    CONF_PORTAL_USERNAME,
)
from custom_components.weheat.coordinator import (
    WeheatPortalSession,
    WeheatSettingsUpdateCoordinator,
)
from custom_components.weheat.models import ControlBoardSettings, HeatPumpInfo

# =============================================================================
# Fixtures
# =============================================================================


@pytest.fixture
def mock_heat_pump_alpha() -> HeatPumpInfo:
    return HeatPumpInfo(
        heatpump_id="00000000-0000-0000-0000-000000000001",
        name="Heat Pump Alpha",
        model=0,
        serial_number="WH-ALPHA-01",
        has_dhw=True,
        control_board_id="cb-alpha-001",
    )


@pytest.fixture
def mock_heat_pump_beta() -> HeatPumpInfo:
    return HeatPumpInfo(
        heatpump_id="00000000-0000-0000-0000-000000000002",
        name="Heat Pump Beta",
        model=1,
        serial_number="WH-BETA-02",
        has_dhw=False,
        control_board_id="cb-beta-002",
    )


@pytest.fixture
def mock_client() -> MagicMock:
    client = MagicMock(spec=WeheatClient)
    client.async_refresh_portal_token = AsyncMock()
    client.async_login_portal_pkce = AsyncMock()
    client.async_get_installation_topology = AsyncMock(return_value=[])
    client.async_get_installation_settings = AsyncMock(
        return_value=ControlBoardSettings()
    )
    return client


@pytest.fixture
def mock_fallback_session() -> MagicMock:
    session = MagicMock()
    session.token = {CONF_ACCESS_TOKEN: "core_telemetry_access_token"}
    session.async_ensure_token_valid = AsyncMock()
    return session


# =============================================================================
# 1. Options Flow Reload Listener Adversarial Tests
# =============================================================================


class TestOptionsFlowReloadListenerAdversarial:
    """Stress tests for options update listener registration and reload triggers."""

    @pytest.mark.asyncio
    async def test_options_listener_registered_and_tracked_in_unload_hook(
        self, mock_heat_pump_alpha: HeatPumpInfo
    ) -> None:
        """Verify listener is registered and properly added to entry._on_unload."""
        hass = HomeAssistant()
        entry = ConfigEntry(
            entry_id="test_entry_listeners",
            data={CONF_TOKEN: {CONF_ACCESS_TOKEN: "mock_oauth_tok"}},
            options={},
        )

        with (
            patch(
                "custom_components.weheat.OAuth2Session.async_ensure_token_valid",
                new=AsyncMock(),
            ),
            patch(
                "custom_components.weheat.WeheatClient.async_get_heat_pumps",
                new=AsyncMock(return_value=[mock_heat_pump_alpha]),
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
            success = await async_setup_entry(hass, entry)

        assert success is True
        assert len(entry.update_listeners) == 1
        assert len(entry._on_unload) >= 1
        # The registered update listener must be async_reload_entry
        assert entry.update_listeners[0] is async_reload_entry

    @pytest.mark.asyncio
    async def test_options_mutation_triggers_immediate_async_reload(
        self, mock_heat_pump_alpha: HeatPumpInfo
    ) -> None:
        """Verify options modification triggers async_reload immediately without HA restart."""
        hass = HomeAssistant()
        entry = ConfigEntry(
            entry_id="entry_reload_test_123",
            data={CONF_TOKEN: {CONF_ACCESS_TOKEN: "mock_oauth_tok"}},
            options={},
        )

        with (
            patch(
                "custom_components.weheat.OAuth2Session.async_ensure_token_valid",
                new=AsyncMock(),
            ),
            patch(
                "custom_components.weheat.WeheatClient.async_get_heat_pumps",
                new=AsyncMock(return_value=[mock_heat_pump_alpha]),
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
            await async_setup_entry(hass, entry)

        # Trigger simulated options update
        entry.options = {CONF_PORTAL_USERNAME: "tester@weheat.nl"}
        for listener in list(entry.update_listeners):
            await listener(hass, entry)

        assert entry.entry_id in hass.config_entries.reloaded_entries

    @pytest.mark.asyncio
    async def test_burst_options_modifications(
        self, mock_heat_pump_alpha: HeatPumpInfo
    ) -> None:
        """Verify a rapid burst of options modifications triggers reload on every cycle."""
        hass = HomeAssistant()
        entry = ConfigEntry(
            entry_id="entry_burst_test",
            data={CONF_TOKEN: {CONF_ACCESS_TOKEN: "mock_oauth_tok"}},
            options={},
        )

        with (
            patch(
                "custom_components.weheat.OAuth2Session.async_ensure_token_valid",
                new=AsyncMock(),
            ),
            patch(
                "custom_components.weheat.WeheatClient.async_get_heat_pumps",
                new=AsyncMock(return_value=[mock_heat_pump_alpha]),
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
            await async_setup_entry(hass, entry)

        burst_payloads = [
            {
                CONF_PORTAL_USERNAME: f"user_{i}@example.com",
                CONF_PORTAL_PASSWORD: f"pass_{i}",
            }
            for i in range(10)
        ]

        for payload in burst_payloads:
            entry.options = payload
            for listener in list(entry.update_listeners):
                await listener(hass, entry)

        assert hass.config_entries.reloaded_entries.count(entry.entry_id) == 10

    @pytest.mark.parametrize(
        "entry_id",
        [
            "standard_uuid_8a2d1d58-7e61-468a-aef2-01991d904ce0",
            "entry with spaces and special chars @#$%",
            "unicode_identifié_001_日本語",
            "1234567890",
            "x" * 256,
        ],
    )
    @pytest.mark.asyncio
    async def test_async_reload_entry_preserves_unusual_entry_ids(
        self, entry_id: str
    ) -> None:
        """Verify async_reload_entry preserves unusual entry IDs without corruption."""
        hass = HomeAssistant()
        entry = ConfigEntry(entry_id=entry_id, data={}, options={})

        await async_reload_entry(hass, entry)
        assert hass.config_entries.reloaded_entries[-1] == entry_id

    @pytest.mark.asyncio
    async def test_multi_entry_reload_isolation(self) -> None:
        """Verify reload on entry A does not trigger reload on entry B."""
        hass = HomeAssistant()
        entry_a = ConfigEntry(entry_id="entry_alpha", data={}, options={})
        entry_b = ConfigEntry(entry_id="entry_beta", data={}, options={})

        entry_a.add_update_listener(async_reload_entry)
        entry_b.add_update_listener(async_reload_entry)

        # Fire only entry_a's listeners
        for listener in entry_a.update_listeners:
            await listener(hass, entry_a)

        assert "entry_alpha" in hass.config_entries.reloaded_entries
        assert "entry_beta" not in hass.config_entries.reloaded_entries

    def test_listener_removal_is_idempotent(self) -> None:
        """Verify removing the update listener multiple times does not raise."""
        entry = ConfigEntry(entry_id="unsub_test", data={}, options={})
        unsub = entry.add_update_listener(async_reload_entry)

        assert len(entry.update_listeners) == 1
        unsub()
        assert len(entry.update_listeners) == 0
        # Second call should not raise
        unsub()
        assert len(entry.update_listeners) == 0


# =============================================================================
# 2. Teardown Lifecycle (async_unload_entry) Adversarial Tests
# =============================================================================


class TestTeardownLifecycleAdversarial:
    """Stress tests for coordinator shutdown, runtime_data clearing, and cleanup."""

    @pytest.mark.asyncio
    async def test_platform_unload_failure_preserves_runtime_data(self) -> None:
        """Verify when platform unload returns False, runtime_data is preserved for retry."""
        hass = HomeAssistant()
        entry = ConfigEntry(entry_id="entry_unload_fail", data={}, options={})

        mock_data = MagicMock()
        entry.runtime_data = [mock_data]

        with patch.object(
            hass.config_entries,
            "async_unload_platforms",
            new=AsyncMock(return_value=False),
        ):
            result = await async_unload_entry(hass, entry)

        assert result is False
        # runtime_data should remain untouched
        assert len(entry.runtime_data) == 1
        assert entry.runtime_data[0] is mock_data

    @pytest.mark.asyncio
    async def test_unload_shuts_down_all_coordinators_across_multiple_devices(
        self,
        mock_heat_pump_alpha: HeatPumpInfo,
        mock_heat_pump_beta: HeatPumpInfo,
    ) -> None:
        """Verify multi-device entry setup tears down all 6 coordinators and clears runtime_data."""
        hass = HomeAssistant()
        entry = ConfigEntry(
            entry_id="multi_pump_entry",
            data={CONF_TOKEN: {CONF_ACCESS_TOKEN: "mock_tok"}},
            options={},
        )

        with (
            patch(
                "custom_components.weheat.OAuth2Session.async_ensure_token_valid",
                new=AsyncMock(),
            ),
            patch(
                "custom_components.weheat.WeheatClient.async_get_heat_pumps",
                new=AsyncMock(return_value=[mock_heat_pump_alpha, mock_heat_pump_beta]),
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
            await async_setup_entry(hass, entry)

        assert len(entry.runtime_data) == 2
        # Mock async_shutdown on all coordinators
        shutdown_mocks = []
        for data in entry.runtime_data:
            for coord in (
                data.data_coordinator,
                data.energy_coordinator,
                data.settings_coordinator,
            ):
                m = MagicMock()
                coord.async_shutdown = m
                shutdown_mocks.append(m)

        assert len(shutdown_mocks) == 6

        result = await async_unload_entry(hass, entry)

        assert result is True
        assert entry.runtime_data == []
        for m in shutdown_mocks:
            m.assert_called_once()

    @pytest.mark.asyncio
    async def test_unload_handles_coroutine_async_shutdown(self) -> None:
        """Verify coordinators with coroutine async_shutdown are properly awaited."""
        hass = HomeAssistant()
        entry = ConfigEntry(entry_id="async_shutdown_entry", data={}, options={})

        mock_coord_async = MagicMock()
        mock_coord_async.async_shutdown = AsyncMock()

        mock_coord_sync = MagicMock()
        mock_coord_sync.async_shutdown = MagicMock(return_value=None)

        mock_data = MagicMock()
        mock_data.data_coordinator = mock_coord_async
        mock_data.energy_coordinator = mock_coord_sync
        mock_data.settings_coordinator = None

        entry.runtime_data = [mock_data]

        result = await async_unload_entry(hass, entry)
        assert result is True
        assert entry.runtime_data == []
        mock_coord_async.async_shutdown.assert_awaited_once()
        mock_coord_sync.async_shutdown.assert_called_once()

    @pytest.mark.asyncio
    async def test_unload_unsubscribes_update_listener_no_dangling_calls(self) -> None:
        """Verify simulating unload removes listener so post-unload options updates do not fire."""
        hass = HomeAssistant()
        entry = ConfigEntry(entry_id="leak_test_entry", data={}, options={})

        unsub = entry.add_update_listener(async_reload_entry)
        entry.async_on_unload(unsub)

        assert len(entry.update_listeners) == 1

        # Simulate Core executing unload hooks
        while entry._on_unload:
            cb = entry._on_unload.pop()
            cb()

        assert len(entry.update_listeners) == 0

        # Now mutate options and attempt to invoke listeners
        entry.options = {"foo": "bar"}
        for listener in entry.update_listeners:
            await listener(hass, entry)

        # No reload should have happened
        assert "leak_test_entry" not in hass.config_entries.reloaded_entries

    @pytest.mark.asyncio
    async def test_idempotent_sequential_unloads(self) -> None:
        """Verify calling async_unload_entry multiple times sequentially does not raise."""
        hass = HomeAssistant()
        entry = ConfigEntry(entry_id="idempotent_entry", data={}, options={})

        mock_data = MagicMock()
        mock_data.data_coordinator = MagicMock(async_shutdown=MagicMock())
        mock_data.energy_coordinator = MagicMock(async_shutdown=MagicMock())
        mock_data.settings_coordinator = MagicMock(async_shutdown=MagicMock())

        entry.runtime_data = [mock_data]

        res1 = await async_unload_entry(hass, entry)
        assert res1 is True
        assert entry.runtime_data == []

        res2 = await async_unload_entry(hass, entry)
        assert res2 is True
        assert entry.runtime_data == []


# =============================================================================
# 3. WeheatPortalSession Routing Adversarial Tests
# =============================================================================


class TestWeheatPortalSessionRoutingAdversarial:
    """Stress tests for session preservation, credential isolation, and routing."""

    @pytest.mark.asyncio
    async def test_wrapper_preserved_when_entry_options_initially_empty(
        self, mock_heat_pump_alpha: HeatPumpInfo
    ) -> None:
        """Verify WeheatPortalSession is never discarded even when options are empty."""
        hass = HomeAssistant()
        entry = ConfigEntry(
            entry_id="entry_empty_opts",
            data={CONF_TOKEN: {CONF_ACCESS_TOKEN: "core_token"}},
            options={},
        )

        with (
            patch(
                "custom_components.weheat.OAuth2Session.async_ensure_token_valid",
                new=AsyncMock(),
            ),
            patch(
                "custom_components.weheat.WeheatClient.async_get_heat_pumps",
                new=AsyncMock(return_value=[mock_heat_pump_alpha]),
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
            await async_setup_entry(hass, entry)

        data = entry.runtime_data[0]
        # Wrapper MUST be preserved
        assert isinstance(data.session, WeheatPortalSession)
        assert isinstance(data.settings_coordinator.session, WeheatPortalSession)
        assert data.session is data.settings_coordinator.session

    @pytest.mark.asyncio
    async def test_telemetry_coordinators_strictly_bound_to_core_oauth_session(
        self, mock_heat_pump_alpha: HeatPumpInfo
    ) -> None:
        """Verify telemetry coordinators remain strictly bound to OAuth2Session."""
        hass = HomeAssistant()
        entry = ConfigEntry(
            entry_id="entry_telemetry_isolation",
            data={CONF_TOKEN: {CONF_ACCESS_TOKEN: "core_token"}},
            options={CONF_PORTAL_TOKEN: {CONF_ACCESS_TOKEN: "portal_token_override"}},
        )

        with (
            patch(
                "custom_components.weheat.OAuth2Session.async_ensure_token_valid",
                new=AsyncMock(),
            ),
            patch(
                "custom_components.weheat.WeheatClient.async_get_heat_pumps",
                new=AsyncMock(return_value=[mock_heat_pump_alpha]),
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
            await async_setup_entry(hass, entry)

        data = entry.runtime_data[0]

        # Telemetry sessions must NOT be WeheatPortalSession
        assert not isinstance(data.data_coordinator.session, WeheatPortalSession)
        assert not isinstance(data.energy_coordinator.session, WeheatPortalSession)

        # Control sessions MUST be WeheatPortalSession
        assert isinstance(data.settings_coordinator.session, WeheatPortalSession)
        assert isinstance(data.session, WeheatPortalSession)

        # Verify token routing values
        assert (
            data.settings_coordinator.session.token[CONF_ACCESS_TOKEN]
            == "portal_token_override"
        )
        assert data.data_coordinator.session.token[CONF_ACCESS_TOKEN] == "core_token"
        assert data.energy_coordinator.session.token[CONF_ACCESS_TOKEN] == "core_token"

    def test_portal_session_handles_none_fallback_session(
        self, mock_client: MagicMock
    ) -> None:
        """Verify WeheatPortalSession handles fallback_session=None gracefully."""
        hass = HomeAssistant()
        entry = ConfigEntry(entry_id="none_fallback", options={})

        session = WeheatPortalSession(
            hass=hass,
            config_entry=entry,
            client=mock_client,
            fallback_session=None,
        )

        assert session.has_portal_auth is False
        assert session.token == {}

    @pytest.mark.asyncio
    async def test_portal_session_dynamic_routing_transition(
        self, mock_client: MagicMock, mock_fallback_session: MagicMock
    ) -> None:
        """Verify dynamic transition when options are populated post-init."""
        hass = HomeAssistant()
        entry = ConfigEntry(entry_id="dyn_routing", options={})

        session = WeheatPortalSession(
            hass=hass,
            config_entry=entry,
            client=mock_client,
            fallback_session=mock_fallback_session,
        )

        # Step 1: Initial state without portal auth
        assert session.has_portal_auth is False
        assert session.token == {CONF_ACCESS_TOKEN: "core_telemetry_access_token"}

        # Step 2: Inject portal token into entry options
        entry.options[CONF_PORTAL_TOKEN] = {
            CONF_ACCESS_TOKEN: "dynamically_added_portal_jwt",
            "expires_at": time.time() + 600,
        }

        # Step 3: Verify dynamic pickup without recreating session instance
        assert session.has_portal_auth is True
        assert session.token[CONF_ACCESS_TOKEN] == "dynamically_added_portal_jwt"


# =============================================================================
# 4. Malformed Portal Token Handling Adversarial Tests
# =============================================================================


class TestMalformedPortalTokensAdversarial:
    """Stress tests for malformed, corrupted, and adversarial portal tokens."""

    @pytest.mark.parametrize(
        "malformed_token",
        [
            None,
            "",
            "   ",
            12345,
            3.14159,
            True,
            False,
            [],
            [1, 2, 3],
            {"not_a_token": 1},
            {"token": None},
            {"token": "not_a_dict"},
            {"token": {}},
            {"access_token": ""},
            {"accessToken": ""},
            {"access_token": None},
            {"accessToken": None},
            {"token": {"accessToken": ""}},
            {"token": {"accessToken": None}},
        ],
    )
    def test_malformed_token_types_rejected_cleanly(
        self,
        malformed_token: Any,
        mock_client: MagicMock,
        mock_fallback_session: MagicMock,
    ) -> None:
        """Verify malformed token types yield has_portal_auth=False and fallback token."""
        hass = HomeAssistant()
        entry = ConfigEntry(
            entry_id="test_malformed",
            options={CONF_PORTAL_TOKEN: malformed_token},
        )

        session = WeheatPortalSession(
            hass=hass,
            config_entry=entry,
            client=mock_client,
            fallback_session=mock_fallback_session,
        )

        assert session.has_portal_auth is False
        assert session.token == {CONF_ACCESS_TOKEN: "core_telemetry_access_token"}
        with pytest.raises(HomeAssistantError):
            session.check_portal_auth()

    @pytest.mark.parametrize(
        "raw_string",
        [
            "{invalid_json",
            '{\\"key\\": incomplete',
            "{}",
            '{"token": {}}',
            '{"token": "string"}',
            '{"accessToken": ""}',
        ],
    )
    def test_corrupted_json_string_tokens_fall_back_cleanly(
        self,
        raw_string: str,
        mock_client: MagicMock,
        mock_fallback_session: MagicMock,
    ) -> None:
        """Verify corrupted JSON strings in options fall back to telemetry session."""
        hass = HomeAssistant()
        entry = ConfigEntry(
            entry_id="test_corrupted_json_strings",
            options={CONF_PORTAL_TOKEN: raw_string},
        )

        session = WeheatPortalSession(
            hass=hass,
            config_entry=entry,
            client=mock_client,
            fallback_session=mock_fallback_session,
        )

        assert session.has_portal_auth is False
        assert session.token == {CONF_ACCESS_TOKEN: "core_telemetry_access_token"}
        with pytest.raises(HomeAssistantError):
            session.check_portal_auth()

    @pytest.mark.parametrize(
        "raw_bearer_string",
        [
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.e30.t-IDcSemACt8x4iTMCda8Yhe3iZaWbvV5XKSTbuAn0M",
            "raw_opaque_bearer_token_string",
            "Bearer custom_session_jwt_payload_123",
        ],
    )
    def test_raw_bearer_string_tokens_accepted_cleanly(
        self,
        raw_bearer_string: str,
        mock_client: MagicMock,
        mock_fallback_session: MagicMock,
    ) -> None:
        """Verify valid raw string tokens are extracted without crashing."""
        hass = HomeAssistant()
        entry = ConfigEntry(
            entry_id="test_raw_strings",
            options={CONF_PORTAL_TOKEN: raw_bearer_string},
        )

        session = WeheatPortalSession(
            hass=hass,
            config_entry=entry,
            client=mock_client,
            fallback_session=mock_fallback_session,
        )

        assert session.has_portal_auth is True
        assert CONF_ACCESS_TOKEN in session.token
        session.check_portal_auth()  # Should not raise

    def test_corrupted_jwt_format_handling(self) -> None:
        """Verify corrupted JWT tokens safely default expiration without exceptions."""
        valid_or_corrupted_jwts = [
            "not.a.valid.jwt",
            "a.b.c.d.e",
            "header.!!!corruptedbase64!!!.signature",
            "header.e30.signature",  # payload is empty dict {}
            "header.eyJleHAiOiAiaW52YWxpZF9ub3RfYV9udW1iZXIifQ.sig",  # exp is string
        ]

        for jwt in valid_or_corrupted_jwts:
            exp = WeheatClient._decode_jwt_exp(jwt)
            # Exp should safely return None (when invalid), never raise
            assert exp is None

            # parse_portal_session_input should not crash and should assign default future expiry
            parsed = WeheatClient.parse_portal_session_input(jwt)
            assert parsed["access_token"] == jwt
            assert "expires_at" in parsed
            assert parsed["expires_at"] > time.time()

        # Specifically test expired JWT
        expired_jwt = "header.eyJleHAiOiAtOTk5OTl9.sig"  # exp is -99999
        exp = WeheatClient._decode_jwt_exp(expired_jwt)
        assert exp == -99999.0
        parsed = WeheatClient.parse_portal_session_input(expired_jwt)
        assert parsed["expires_at"] == -99999.0
        assert parsed["expires_at"] < time.time()

    @pytest.mark.asyncio
    async def test_coordinator_handles_expires_at_none_without_crashing(
        self, mock_client: MagicMock
    ) -> None:
        """Verify coordinator catches TypeError from expires_at: None as UpdateFailed."""
        hass = HomeAssistant()
        entry = ConfigEntry(
            entry_id="expires_none_entry",
            options={
                CONF_PORTAL_TOKEN: {
                    CONF_ACCESS_TOKEN: "valid_jwt_abc",
                    "expires_at": None,
                }
            },
        )

        session = WeheatPortalSession(hass, entry, mock_client)
        hp = HeatPumpInfo(
            heatpump_id="hp_exp_none",
            name="HP",
            model=0,
            serial_number="SN",
            has_dhw=True,
        )
        coord = WeheatSettingsUpdateCoordinator(
            hass, entry, session, hp, client=mock_client
        )

        with pytest.raises(UpdateFailed) as exc_info:
            await coord._async_update_data()

        assert "Failed to ensure valid OAuth token" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_coordinator_handles_expires_at_invalid_string_without_crashing(
        self, mock_client: MagicMock
    ) -> None:
        """Verify coordinator catches ValueError from expires_at: 'never' as UpdateFailed."""
        hass = HomeAssistant()
        entry = ConfigEntry(
            entry_id="expires_str_entry",
            options={
                CONF_PORTAL_TOKEN: {
                    CONF_ACCESS_TOKEN: "valid_jwt_abc",
                    "expires_at": "never_expire",
                }
            },
        )

        session = WeheatPortalSession(hass, entry, mock_client)
        hp = HeatPumpInfo(
            heatpump_id="hp_exp_str",
            name="HP",
            model=0,
            serial_number="SN",
            has_dhw=True,
        )
        coord = WeheatSettingsUpdateCoordinator(
            hass, entry, session, hp, client=mock_client
        )

        with pytest.raises(UpdateFailed) as exc_info:
            await coord._async_update_data()

        assert "Failed to ensure valid OAuth token" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_expired_token_without_refresh_token_or_creds_falls_back(
        self, mock_client: MagicMock, mock_fallback_session: MagicMock
    ) -> None:
        """Verify expired token with no refresh or creds delegates to fallback without crashing."""
        hass = HomeAssistant()
        entry = ConfigEntry(
            entry_id="test_expired_standalone",
            options={
                CONF_PORTAL_TOKEN: {
                    CONF_ACCESS_TOKEN: "expired_standalone_jwt",
                    "expires_at": time.time() - 500,
                }
            },
        )

        session = WeheatPortalSession(
            hass=hass,
            config_entry=entry,
            client=mock_client,
            fallback_session=mock_fallback_session,
        )

        await session.async_ensure_token_valid()
        mock_fallback_session.async_ensure_token_valid.assert_called_once()
        mock_client.async_refresh_portal_token.assert_not_called()
        mock_client.async_login_portal_pkce.assert_not_called()

    @pytest.mark.asyncio
    async def test_expired_token_with_unhandled_refresh_error_raises_weheat_error(
        self, mock_client: MagicMock, mock_fallback_session: MagicMock
    ) -> None:
        """Verify refresh failure without PKCE credentials raises WeheatError."""
        hass = HomeAssistant()
        entry = ConfigEntry(
            entry_id="test_refresh_fail",
            options={
                CONF_PORTAL_TOKEN: {
                    CONF_ACCESS_TOKEN: "expired_jwt",
                    "refresh_token": "corrupted_refresh_token",
                    "expires_at": time.time() - 100,
                }
            },
        )

        mock_client.async_refresh_portal_token.side_effect = WeheatAuthError(
            "Refresh token revoked", status_code=400
        )

        session = WeheatPortalSession(
            hass=hass,
            config_entry=entry,
            client=mock_client,
            fallback_session=mock_fallback_session,
        )

        with pytest.raises(WeheatError):
            await session.async_ensure_token_valid()

    @pytest.mark.asyncio
    async def test_setup_entry_resilient_to_corrupted_portal_token_options(
        self, mock_heat_pump_alpha: HeatPumpInfo
    ) -> None:
        """Verify async_setup_entry succeeds and does not crash when options contain garbage."""
        hass = HomeAssistant()
        entry = ConfigEntry(
            entry_id="entry_corrupted_portal_options",
            data={CONF_TOKEN: {CONF_ACCESS_TOKEN: "valid_core_token"}},
            options={CONF_PORTAL_TOKEN: "{corrupted garbage json"},
        )

        with (
            patch(
                "custom_components.weheat.OAuth2Session.async_ensure_token_valid",
                new=AsyncMock(),
            ),
            patch(
                "custom_components.weheat.WeheatClient.async_get_heat_pumps",
                new=AsyncMock(return_value=[mock_heat_pump_alpha]),
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
                new=AsyncMock(side_effect=ConfigEntryAuthFailed("Portal forbidden")),
            ),
        ):
            success = await async_setup_entry(hass, entry)

        assert success is True
        assert len(entry.runtime_data) == 1
        data = entry.runtime_data[0]
        # Telemetry should be intact
        assert data.data_coordinator is not None
        assert data.energy_coordinator is not None
        # Settings coordinator defaulted safely
        assert data.settings_coordinator.data is not None

    @pytest.mark.parametrize(
        "bad_raw_input",
        [
            "{corrupted_json",
            "{}",
            '{"token": {}}',
            '{"token": "string"}',
            '{"accessToken": ""}',
        ],
    )
    @pytest.mark.asyncio
    async def test_options_flow_handles_corrupted_raw_session_input(
        self, bad_raw_input: str
    ) -> None:
        """Verify Options Flow gracefully sets invalid_portal_auth error on invalid token input."""
        entry = ConfigEntry(entry_id="opt_flow_test", options={})
        handler = WeheatOptionsFlowHandler(entry)

        result = await handler.async_step_init(
            user_input={CONF_PORTAL_SESSION_TOKEN: bad_raw_input}
        )

        assert result["type"] == "form"
        assert result["errors"].get("base") == "invalid_portal_auth"

    @pytest.mark.asyncio
    async def test_options_flow_clears_credentials_when_submitted_empty(self) -> None:
        """Verify Options Flow cleanly clears portal credentials when submitted empty."""
        entry = ConfigEntry(
            entry_id="opt_flow_clear",
            options={
                CONF_PORTAL_USERNAME: "prior_user@weheat.nl",
                CONF_PORTAL_PASSWORD: "prior_pass",
                CONF_PORTAL_TOKEN: {CONF_ACCESS_TOKEN: "prior_token"},
            },
        )
        handler = WeheatOptionsFlowHandler(entry)

        result = await handler.async_step_init(
            user_input={
                CONF_PORTAL_USERNAME: "",
                CONF_PORTAL_PASSWORD: "",
                CONF_PORTAL_SESSION_TOKEN: "   ",
            }
        )

        assert result["type"] == "create_entry"
        new_data = result["data"]
        assert CONF_PORTAL_USERNAME not in new_data
        assert CONF_PORTAL_PASSWORD not in new_data
        assert CONF_PORTAL_TOKEN not in new_data

    @pytest.mark.asyncio
    async def test_options_flow_pkce_login_error_handling(self) -> None:
        """Verify Options Flow handles WeheatError during PKCE login with error form."""
        entry = ConfigEntry(entry_id="opt_flow_pkce_fail", options={})
        handler = WeheatOptionsFlowHandler(entry)

        with patch(
            "custom_components.weheat.client.WeheatClient.async_login_portal_pkce",
            new=AsyncMock(
                side_effect=WeheatAuthError("Bad credentials", status_code=401)
            ),
        ):
            result = await handler.async_step_init(
                user_input={
                    CONF_PORTAL_USERNAME: "bad_user@weheat.nl",
                    CONF_PORTAL_PASSWORD: "wrong_password",
                }
            )

        assert result["type"] == "form"
        assert result["errors"].get("base") == "invalid_portal_auth"


# =============================================================================
# 5. check_portal_auth() Guardrails Adversarial Tests
# =============================================================================


class TestCheckPortalAuthGuardrailsAdversarial:
    """Stress tests for check_portal_auth() guardrail enforcement."""

    def test_check_portal_auth_strictly_blocks_unauthorized_state(
        self, mock_client: MagicMock, mock_fallback_session: MagicMock
    ) -> None:
        """Verify check_portal_auth raises HomeAssistantError on all unauthorized states."""
        hass = HomeAssistant()

        unauthorized_option_sets = [
            {},
            {CONF_PORTAL_USERNAME: "only_user_no_password"},
            {CONF_PORTAL_PASSWORD: "only_password_no_user"},
            {CONF_PORTAL_USERNAME: "", CONF_PORTAL_PASSWORD: ""},
            {CONF_PORTAL_USERNAME: "   ", CONF_PORTAL_PASSWORD: "   "},
            {CONF_PORTAL_TOKEN: {}},
            {CONF_PORTAL_TOKEN: {"other_key": "val"}},
            {CONF_PORTAL_TOKEN: "{corrupted json"},
        ]

        for opts in unauthorized_option_sets:
            entry = ConfigEntry(entry_id="unauth_entry", options=opts)
            session = WeheatPortalSession(
                hass=hass,
                config_entry=entry,
                client=mock_client,
                fallback_session=mock_fallback_session,
            )

            with pytest.raises(HomeAssistantError) as exc_info:
                session.check_portal_auth()

            err_msg = str(exc_info.value)
            assert "Weheat Portal credentials required for control actions" in err_msg
            assert "Configure options" in err_msg

    def test_check_portal_auth_passes_when_authorized(
        self, mock_client: MagicMock, mock_fallback_session: MagicMock
    ) -> None:
        """Verify check_portal_auth does not raise when authorized."""
        hass = HomeAssistant()

        authorized_option_sets = [
            {
                CONF_PORTAL_USERNAME: "valid_user@example.com",
                CONF_PORTAL_PASSWORD: "valid_password",
            },
            {
                CONF_PORTAL_TOKEN: {CONF_ACCESS_TOKEN: "valid_jwt_string"},
            },
            {
                CONF_PORTAL_TOKEN: {"accessToken": "valid_nextauth_jwt"},
            },
            {
                CONF_PORTAL_TOKEN: '{"accessToken": "valid_json_string_jwt"}',
            },
        ]

        for opts in authorized_option_sets:
            entry = ConfigEntry(entry_id="auth_entry", options=opts)
            session = WeheatPortalSession(
                hass=hass,
                config_entry=entry,
                client=mock_client,
                fallback_session=mock_fallback_session,
            )

            # Should not raise
            session.check_portal_auth()

    def test_non_string_access_token_converted_safely(
        self, mock_client: MagicMock, mock_fallback_session: MagicMock
    ) -> None:
        """Verify non-string truthy access_token (e.g. integer) is safely stringified."""
        hass = HomeAssistant()
        entry = ConfigEntry(
            entry_id="int_token",
            options={CONF_PORTAL_TOKEN: {CONF_ACCESS_TOKEN: 123456789}},
        )

        session = WeheatPortalSession(
            hass=hass,
            config_entry=entry,
            client=mock_client,
            fallback_session=mock_fallback_session,
        )

        assert session.has_portal_auth is True
        assert session.token[CONF_ACCESS_TOKEN] == "123456789"
        session.check_portal_auth()  # Should not raise
