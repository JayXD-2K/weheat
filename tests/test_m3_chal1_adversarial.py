"""Adversarial Empirical Stress Test Suite for Milestone 3.

Conducted by Challenger 1 (critic, specialist) to stress-test:
1. ControlBoardSettings.from_api_dict:
   - Live camelCase responses (e.g. from portal API).
   - Legacy PascalCase responses.
   - Mixed casing responses.
   - Empty/None dictionaries, missing schedule keys.
2. build_bundled_silent_schedule_payload:
   - Verify all 30 keys are in PascalCase.
   - Verify wrapping in {"settings": {...}, "updateImmediately": False}.
   - Parameter overrides and edge values.
3. Target ID caching and translation in WeheatClient:
   - Resolving heatPumpId or installationId to controlBoardId.
   - Handling singular and plural heatPumps topology structures.
   - Fallback behavior when controlBoardId is identical or distinct.
"""

from __future__ import annotations

import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.weheat.client import WeheatApiError, WeheatClient
from custom_components.weheat.const import DAYS_OF_WEEK
from custom_components.weheat.models import ControlBoardSettings

# Regex for strict PascalCase: starts with uppercase, alphanumeric only
PASCAL_CASE_REGEX = re.compile(r"^[A-Z][a-zA-Z0-9]+$")


# =============================================================================
# Section 1: ControlBoardSettings.from_api_dict Casing & Resilience Stress Tests
# =============================================================================


class TestControlBoardSettingsFromApiDict:
    """Adversarial challenge for ControlBoardSettings dictionary parsing."""

    def test_live_portal_camelcase_full_payload(self) -> None:
        """Verify real-world portal response with 100% camelCase keys parses accurately."""
        live_portal_payload = {
            "performanceModePreset": 3,
            "silentHoursScheduledPreset": 4,
            "dhwTargetTemperature": 52.5,
            "silentHoursMondayStart": 20,
            "silentMinutesMondayStart": 30,
            "silentHoursMondayStop": 8,
            "silentMinutesMondayStop": 15,
            "silentHoursTuesdayStart": 21,
            "silentMinutesTuesdayStart": 0,
            "silentHoursTuesdayStop": 7,
            "silentMinutesTuesdayStop": 30,
            "silentHoursWednesdayStart": 22,
            "silentMinutesWednesdayStart": 0,
            "silentHoursWednesdayStop": 7,
            "silentMinutesWednesdayStop": 0,
            "silentHoursThursdayStart": 21,
            "silentMinutesThursdayStart": 45,
            "silentHoursThursdayStop": 6,
            "silentMinutesThursdayStop": 45,
            "silentHoursFridayStart": 23,
            "silentMinutesFridayStart": 0,
            "silentHoursFridayStop": 9,
            "silentMinutesFridayStop": 0,
            "silentHoursSaturdayStart": 23,
            "silentMinutesSaturdayStart": 30,
            "silentHoursSaturdayStop": 9,
            "silentMinutesSaturdayStop": 30,
            "silentHoursSundayStart": 20,
            "silentMinutesSundayStart": 0,
            "silentHoursSundayStop": 7,
            "silentMinutesSundayStop": 0,
        }

        settings = ControlBoardSettings.from_api_dict(live_portal_payload)

        assert settings.performance_mode_preset == 3
        assert settings.silent_hours_preset == 4
        assert settings.dhw_target_temperature == 52.5
        assert len(settings.schedules) == 7

        mon = settings.schedules["Monday"]
        assert mon.start_hour == 20
        assert mon.start_minute == 30
        assert mon.stop_hour == 8
        assert mon.stop_minute == 15
        assert mon.start_time_str == "20:30"
        assert mon.stop_time_str == "08:15"

        sat = settings.schedules["Saturday"]
        assert sat.start_hour == 23
        assert sat.start_minute == 30
        assert sat.stop_hour == 9
        assert sat.stop_minute == 30

    def test_nested_camelcase_settings(self) -> None:
        """Verify wrapped {'settings': {...}} with camelCase keys parses accurately."""
        payload = {
            "settings": {
                "performanceModePreset": 1,
                "silentHoursScheduledPreset": 2,
                "dhwTargetTemperature": 49.0,
                "silentHoursWednesdayStart": 18,
                "silentMinutesWednesdayStart": 45,
                "silentHoursWednesdayStop": 6,
                "silentMinutesWednesdayStop": 15,
            }
        }
        settings = ControlBoardSettings.from_api_dict(payload)
        assert settings.performance_mode_preset == 1
        assert settings.silent_hours_preset == 2
        assert settings.dhw_target_temperature == 49.0
        wed = settings.schedules["Wednesday"]
        assert wed.start_hour == 18
        assert wed.start_minute == 45
        assert wed.stop_hour == 6
        assert wed.stop_minute == 15

    def test_nested_capitalized_settings(self) -> None:
        """Verify wrapped {'Settings': {...}} with capitalized container parses accurately."""
        payload = {
            "Settings": {
                "performanceModePreset": 0,
                "silentHoursScheduledPreset": 6,
                "dhwTargetTemperature": 58.0,
            }
        }
        settings = ControlBoardSettings.from_api_dict(payload)
        assert settings.performance_mode_preset == 0
        assert settings.silent_hours_preset == 6
        assert settings.dhw_target_temperature == 58.0

    def test_string_encoded_numbers_in_camelcase(self) -> None:
        """Verify string-encoded integers and floats are converted properly."""
        payload = {
            "performanceModePreset": "2",
            "silentHoursScheduledPreset": "4",
            "dhwTargetTemperature": "55.0",
            "silentHoursTuesdayStart": "22",
            "silentMinutesTuesdayStart": "15",
            "silentHoursTuesdayStop": "7",
            "silentMinutesTuesdayStop": "45",
        }
        settings = ControlBoardSettings.from_api_dict(payload)
        assert settings.performance_mode_preset == 2
        assert settings.silent_hours_preset == 4
        assert settings.dhw_target_temperature == 55.0
        tue = settings.schedules["Tuesday"]
        assert tue.start_hour == 22
        assert tue.start_minute == 15
        assert tue.stop_hour == 7
        assert tue.stop_minute == 45

    def test_unrecognized_extra_keys_in_camelcase(self) -> None:
        """Verify presence of foreign portal keys does not degrade parsing."""
        payload = {
            "performanceModePreset": 2,
            "silentHoursScheduledPreset": 3,
            "heatingCurveX": [-20, 9, 15, 20, 25],
            "heatingCurveY": [45, 38, 32, 28, 25],
            "coolingCurveY": [18, 20],
            "controlMethod": 4,
            "circulationPumpChTargetFlow": 15,
            "unknownFutureKey": {"deeply": ["nested", "data"]},
        }
        settings = ControlBoardSettings.from_api_dict(payload)
        assert settings.performance_mode_preset == 2
        assert settings.silent_hours_preset == 3
        assert len(settings.schedules) == 7

    def test_legacy_pascalcase_flat_payload(self) -> None:
        """Verify pure legacy / fixture PascalCase payload parses accurately."""
        legacy_payload = {
            "PerformanceModePreset": 1,
            "SilentHoursScheduledPreset": 3,
            "DhwTargetTemperature": 45.0,
            "SilentHoursThursdayStart": 21,
            "SilentMinutesThursdayStart": 30,
            "SilentHoursThursdayStop": 6,
            "SilentMinutesThursdayStop": 30,
        }
        settings = ControlBoardSettings.from_api_dict(legacy_payload)
        assert settings.performance_mode_preset == 1
        assert settings.silent_hours_preset == 3
        assert settings.dhw_target_temperature == 45.0
        thu = settings.schedules["Thursday"]
        assert thu.start_hour == 21
        assert thu.start_minute == 30
        assert thu.stop_hour == 6
        assert thu.stop_minute == 30

    def test_legacy_pascalcase_nested_payload(self) -> None:
        """Verify nested PascalCase payload inside {'settings': {...}} parses accurately."""
        payload = {
            "settings": {
                "PerformanceModePreset": 4,
                "SilentHoursScheduledPreset": 7,
                "DhwTargetTemperature": 60.0,
            }
        }
        settings = ControlBoardSettings.from_api_dict(payload)
        assert settings.performance_mode_preset == 4
        assert settings.silent_hours_preset == 7
        assert settings.dhw_target_temperature == 60.0

    def test_mixed_casing_payload(self) -> None:
        """Verify arbitrary mixed camelCase and PascalCase fields parse seamlessly."""
        mixed_payload = {
            "performanceModePreset": 1,  # camel
            "SilentHoursScheduledPreset": 4,  # Pascal
            "dhwTargetTemperature": 53.0,  # camel
            "SilentHoursMondayStart": 22,  # Pascal
            "silentMinutesMondayStart": 10,  # camel
            "SilentHoursMondayStop": 7,  # Pascal
            "silentMinutesMondayStop": 50,  # camel
            "silentHoursFridayStart": 23,  # camel
            "SilentMinutesFridayStart": 45,  # Pascal
            "silentHoursFridayStop": 8,  # camel
            "SilentMinutesFridayStop": 15,  # Pascal
        }
        settings = ControlBoardSettings.from_api_dict(mixed_payload)
        assert settings.performance_mode_preset == 1
        assert settings.silent_hours_preset == 4
        assert settings.dhw_target_temperature == 53.0

        mon = settings.schedules["Monday"]
        assert mon.start_hour == 22
        assert mon.start_minute == 10
        assert mon.stop_hour == 7
        assert mon.stop_minute == 50

        fri = settings.schedules["Friday"]
        assert fri.start_hour == 23
        assert fri.start_minute == 45
        assert fri.stop_hour == 8
        assert fri.stop_minute == 15

    def test_conflicting_casing_precedence_camelcase_preferred(self) -> None:
        """Verify when both camelCase and PascalCase coexist, camelCase is preferred."""
        conflicting = {
            "performanceModePreset": 2,
            "PerformanceModePreset": 0,
            "silentHoursScheduledPreset": 4,
            "SilentHoursScheduledPreset": 1,
            "dhwTargetTemperature": 55.0,
            "DhwTargetTemperature": 45.0,
            "silentHoursSundayStart": 23,
            "SilentHoursSundayStart": 18,
        }
        settings = ControlBoardSettings.from_api_dict(conflicting)
        assert settings.performance_mode_preset == 2
        assert settings.silent_hours_preset == 4
        assert settings.dhw_target_temperature == 55.0
        assert settings.schedules["Sunday"].start_hour == 23

    def test_nested_vs_toplevel_precedence(self) -> None:
        """Verify nested settings dict takes precedence over top-level container keys."""
        conflicting = {
            "performanceModePreset": 0,
            "settings": {
                "performanceModePreset": 3,
                "silentHoursScheduledPreset": 6,
            },
        }
        settings = ControlBoardSettings.from_api_dict(conflicting)
        assert settings.performance_mode_preset == 3
        assert settings.silent_hours_preset == 6

    def test_none_input_safety(self) -> None:
        """Verify passing None safely produces default ControlBoardSettings instance."""
        settings = ControlBoardSettings.from_api_dict(None)
        assert settings.performance_mode_preset == 0
        assert settings.silent_hours_preset == 0
        assert settings.dhw_target_temperature == 50.0
        assert settings.schedules == {}
        assert settings.raw_settings == {}

    def test_empty_dict_input_defaults(self) -> None:
        """Verify empty dictionary input produces safe defaults with all 7 days populated."""
        settings = ControlBoardSettings.from_api_dict({})
        assert settings.performance_mode_preset == 0
        assert settings.silent_hours_preset == 0
        assert settings.dhw_target_temperature == 50.0
        assert len(settings.schedules) == 7
        for day in DAYS_OF_WEEK:
            sched = settings.schedules[day]
            assert sched.start_hour == 19
            assert sched.start_minute == 0
            assert sched.stop_hour == 7
            assert sched.stop_minute == 0

    @pytest.mark.parametrize(
        "invalid_input", ["not_a_dict", 123, 45.6, [], False, True]
    )
    def test_non_dict_inputs_safety(self, invalid_input: Any) -> None:
        """Verify any non-dictionary input type is safely rejected and defaults returned."""
        settings = ControlBoardSettings.from_api_dict(invalid_input)
        assert isinstance(settings, ControlBoardSettings)
        assert settings.performance_mode_preset == 0
        assert settings.schedules == {}

    @pytest.mark.parametrize("corrupt_settings", [None, "invalid", 42, [], True])
    def test_nested_settings_none_or_non_dict_safety(
        self, corrupt_settings: Any
    ) -> None:
        """Verify nested 'settings' field containing non-dict falls back to parent dict."""
        payload = {
            "settings": corrupt_settings,
            "performanceModePreset": 2,
        }
        settings = ControlBoardSettings.from_api_dict(payload)
        assert settings.performance_mode_preset == 2

    def test_partial_schedule_keys_fallback_to_defaults(self) -> None:
        """Verify partial schedule keys default individually without crashing."""
        payload = {
            "performanceModePreset": 1,
            "silentHoursMondayStart": 22,
            # MondayStart minute missing
            # MondayStop hour missing
            # MondayStop minute missing
            # All other days missing completely
        }
        settings = ControlBoardSettings.from_api_dict(payload)
        mon = settings.schedules["Monday"]
        assert mon.start_hour == 22
        assert mon.start_minute == 0  # default
        assert mon.stop_hour == 7  # default
        assert mon.stop_minute == 0  # default

        tue = settings.schedules["Tuesday"]
        assert tue.start_hour == 19
        assert tue.start_minute == 0
        assert tue.stop_hour == 7
        assert tue.stop_minute == 0

    def test_corrupt_types_fallback_to_defaults(self) -> None:
        """Verify malformed value types fall back to defaults rather than raising."""
        payload = {
            "performanceModePreset": "invalid_number",
            "silentHoursScheduledPreset": {"dict": "not_an_int"},
            "dhwTargetTemperature": [1, 2, 3],
            "silentHoursMondayStart": None,
            "silentMinutesMondayStart": "corrupt",
        }
        settings = ControlBoardSettings.from_api_dict(payload)
        assert settings.performance_mode_preset == 0
        assert settings.silent_hours_preset == 0
        assert settings.dhw_target_temperature == 50.0
        assert settings.schedules["Monday"].start_hour == 19
        assert settings.schedules["Monday"].start_minute == 0

    def test_dhw_target_temperature_from_nested_dhw_object(self) -> None:
        """Verify DHW target temperature extraction from nested 'dhw.targetTemperature'."""
        payload = {
            "performanceModePreset": 0,
            "dhw": {
                "targetTemperature": 56.5,
                "controlMethod": 1,
            },
        }
        settings = ControlBoardSettings.from_api_dict(payload)
        assert settings.dhw_target_temperature == 56.5


# =============================================================================
# Section 2: Bundled 30-Key Silent Schedule Payload Stress Tests
# =============================================================================


class TestBuildBundledSilentSchedulePayload:
    """Adversarial stress tests for 30-key PascalCase bundled payload generator."""

    def test_all_30_keys_present_and_exact_count(self) -> None:
        """Verify generated payload has exactly 30 keys in 'settings'."""
        settings = ControlBoardSettings()
        payload = settings.build_bundled_silent_schedule_payload()

        assert "settings" in payload
        inner = payload["settings"]
        assert len(inner) == 30

    def test_all_30_keys_are_strictly_pascalcase_regex(self) -> None:
        """Verify EVERY single key inside 'settings' conforms strictly to PascalCase."""
        settings = ControlBoardSettings(
            performance_mode_preset=1,
            silent_hours_preset=3,
        )
        payload = settings.build_bundled_silent_schedule_payload()
        inner = payload["settings"]

        for key in inner:
            assert PASCAL_CASE_REGEX.match(key) is not None, (
                f"Key '{key}' does not conform to strict PascalCase!"
            )
            assert not key.startswith("silent"), f"Found camelCase key '{key}'!"
            assert not key.startswith("performance"), f"Found camelCase key '{key}'!"

    def test_no_camelcase_keys_leak(self) -> None:
        """Verify no lowercase or camelCase keys leak into inner dictionary."""
        settings = ControlBoardSettings()
        payload = settings.build_bundled_silent_schedule_payload()
        inner = payload["settings"]

        camel_forbidden = {
            "performanceModePreset",
            "silentHoursScheduledPreset",
            "silentHoursMondayStart",
            "silentMinutesMondayStart",
            "silentHoursMondayStop",
            "silentMinutesMondayStop",
        }
        leaked = camel_forbidden.intersection(inner.keys())
        assert not leaked, f"Forbidden camelCase keys leaked: {leaked}"

    def test_wrapping_structure_and_update_immediately_false(self) -> None:
        """Verify payload root contains exactly {'settings': ..., 'updateImmediately': False}."""
        settings = ControlBoardSettings()
        payload = settings.build_bundled_silent_schedule_payload()

        assert set(payload.keys()) == {"settings", "updateImmediately"}
        assert payload["updateImmediately"] is False
        assert type(payload["updateImmediately"]) is bool

    def test_performance_preset_override(self) -> None:
        """Verify performance_preset argument overrides instance default."""
        settings = ControlBoardSettings(performance_mode_preset=0)
        payload = settings.build_bundled_silent_schedule_payload(performance_preset=3)
        assert payload["settings"]["PerformanceModePreset"] == 3

    def test_performance_mode_preset_alias_override(self) -> None:
        """Verify performance_mode_preset alias argument overrides instance default."""
        settings = ControlBoardSettings(performance_mode_preset=0)
        payload = settings.build_bundled_silent_schedule_payload(
            performance_mode_preset=4
        )
        assert payload["settings"]["PerformanceModePreset"] == 4

    def test_performance_preset_precedence_over_alias(self) -> None:
        """Verify performance_preset takes precedence when both aliases are supplied."""
        settings = ControlBoardSettings()
        payload = settings.build_bundled_silent_schedule_payload(
            performance_preset=1,
            performance_mode_preset=2,
        )
        assert payload["settings"]["PerformanceModePreset"] == 1

    def test_silent_preset_override(self) -> None:
        """Verify silent_preset argument overrides instance default."""
        settings = ControlBoardSettings(silent_hours_preset=0)
        payload = settings.build_bundled_silent_schedule_payload(silent_preset=6)
        assert payload["settings"]["SilentHoursScheduledPreset"] == 6

    def test_silent_hours_preset_alias_override(self) -> None:
        """Verify silent_hours_preset alias argument overrides instance default."""
        settings = ControlBoardSettings(silent_hours_preset=0)
        payload = settings.build_bundled_silent_schedule_payload(silent_hours_preset=7)
        assert payload["settings"]["SilentHoursScheduledPreset"] == 7

    def test_silent_preset_precedence_over_alias(self) -> None:
        """Verify silent_preset takes precedence when both aliases are supplied."""
        settings = ControlBoardSettings()
        payload = settings.build_bundled_silent_schedule_payload(
            silent_preset=3,
            silent_hours_preset=5,
        )
        assert payload["settings"]["SilentHoursScheduledPreset"] == 3

    def test_day_updates_camelcase_normalized_to_pascalcase(self) -> None:
        """Verify camelCase keys in day_updates are cleanly normalized to PascalCase."""
        settings = ControlBoardSettings()
        payload = settings.build_bundled_silent_schedule_payload(
            day_updates={
                "silentHoursMondayStart": 22,
                "silentMinutesMondayStart": 15,
                "silentHoursMondayStop": 6,
                "silentMinutesMondayStop": 45,
            }
        )
        inner = payload["settings"]
        assert inner["SilentHoursMondayStart"] == 22
        assert inner["SilentMinutesMondayStart"] == 15
        assert inner["SilentHoursMondayStop"] == 6
        assert inner["SilentMinutesMondayStop"] == 45
        assert "silentHoursMondayStart" not in inner
        assert len(inner) == 30

    def test_day_updates_pascalcase_preserved(self) -> None:
        """Verify PascalCase keys in day_updates work directly."""
        settings = ControlBoardSettings()
        payload = settings.build_bundled_silent_schedule_payload(
            day_updates={
                "SilentHoursSundayStart": 23,
                "SilentMinutesSundayStart": 30,
            }
        )
        inner = payload["settings"]
        assert inner["SilentHoursSundayStart"] == 23
        assert inner["SilentMinutesSundayStart"] == 30

    def test_day_updates_extraneous_keys_ignored(self) -> None:
        """Verify unrecognized keys in day_updates do NOT pollute the 30-key payload."""
        settings = ControlBoardSettings()
        payload = settings.build_bundled_silent_schedule_payload(
            day_updates={
                "arbitraryUnknownKey": 999,
                "silentInvalidDayStart": 12,
            }
        )
        inner = payload["settings"]
        assert len(inner) == 30
        assert "arbitraryUnknownKey" not in inner
        assert "SilentInvalidDayStart" not in inner

    def test_day_updates_string_integers_converted(self) -> None:
        """Verify day_updates values as strings are parsed to int."""
        settings = ControlBoardSettings()
        payload = settings.build_bundled_silent_schedule_payload(
            day_updates={
                "SilentHoursThursdayStart": "21",
                "SilentMinutesThursdayStart": "45",
            }
        )
        inner = payload["settings"]
        assert inner["SilentHoursThursdayStart"] == 21
        assert inner["SilentMinutesThursdayStart"] == 45
        assert isinstance(inner["SilentHoursThursdayStart"], int)

    def test_empty_schedules_in_settings_still_builds_complete_payload(self) -> None:
        """Verify ControlBoardSettings with schedules={} safely defaults all 28 schedule keys."""
        settings = ControlBoardSettings.from_api_dict(None)
        assert settings.schedules == {}

        payload = settings.build_bundled_silent_schedule_payload()
        inner = payload["settings"]
        assert len(inner) == 30
        for day in DAYS_OF_WEEK:
            assert inner[f"SilentHours{day}Start"] == 19
            assert inner[f"SilentMinutes{day}Start"] == 0
            assert inner[f"SilentHours{day}Stop"] == 7
            assert inner[f"SilentMinutes{day}Stop"] == 0

    def test_build_quiet_payload_alias_identity(self) -> None:
        """Verify build_quiet_payload alias exists and matches build_bundled_silent_schedule_payload."""
        settings = ControlBoardSettings(
            performance_mode_preset=2, silent_hours_preset=4
        )
        p1 = settings.build_bundled_silent_schedule_payload()
        p2 = settings.build_quiet_payload()
        assert p1 == p2

    def test_boundary_hour_and_minute_values(self) -> None:
        """Verify boundary values (0:00 and 23:59) are accurately generated."""
        settings = ControlBoardSettings()
        payload = settings.build_bundled_silent_schedule_payload(
            day_updates={
                "SilentHoursSaturdayStart": 0,
                "SilentMinutesSaturdayStart": 0,
                "SilentHoursSaturdayStop": 23,
                "SilentMinutesSaturdayStop": 59,
            }
        )
        inner = payload["settings"]
        assert inner["SilentHoursSaturdayStart"] == 0
        assert inner["SilentMinutesSaturdayStart"] == 0
        assert inner["SilentHoursSaturdayStop"] == 23
        assert inner["SilentMinutesSaturdayStop"] == 59


# =============================================================================
# Section 3: Target ID Caching, Discovery & Routing in WeheatClient
# =============================================================================


class TestTargetIdCachingAndTranslation:
    """Adversarial stress tests for target ID caching and client routing."""

    @pytest.mark.asyncio
    async def test_resolve_heatpump_to_control_board_id(self) -> None:
        """Verify heatPumpId resolves to controlBoardId via cached topology."""
        client = WeheatClient(session=MagicMock())
        client._control_board_cache["hp-alpha-123"] = "cb-omega-456"

        resolved = await client.async_get_control_board_id("hp-alpha-123")
        assert resolved == "cb-omega-456"

    @pytest.mark.asyncio
    async def test_resolve_installation_to_control_board_id(self) -> None:
        """Verify installationId resolves to controlBoardId via cached topology."""
        client = WeheatClient(session=MagicMock())
        client._control_board_cache["inst-site-999"] = "cb-omega-456"

        resolved = await client.async_get_control_board_id("inst-site-999")
        assert resolved == "cb-omega-456"

    @pytest.mark.asyncio
    async def test_resolve_control_board_id_identity(self) -> None:
        """Verify controlBoardId resolves to itself without modification."""
        client = WeheatClient(session=MagicMock())
        client._control_board_cache["cb-direct-001"] = "cb-direct-001"

        resolved = await client.async_get_control_board_id("cb-direct-001")
        assert resolved == "cb-direct-001"

    @pytest.mark.asyncio
    async def test_control_board_prefix_cb_short_circuit(self) -> None:
        """Verify ID starting with 'cb-' short-circuits without calling network."""
        client = WeheatClient(session=MagicMock())
        client._request = AsyncMock()

        resolved = await client.async_get_control_board_id("cb-unseen-target")
        assert resolved == "cb-unseen-target"
        client._request.assert_not_called()
        # Populated into cache
        assert client._control_board_cache["cb-unseen-target"] == "cb-unseen-target"

    @pytest.mark.asyncio
    async def test_force_refresh_bypasses_cache(self) -> None:
        """Verify force_refresh=True forces network topology refresh even if cached."""
        client = WeheatClient(session=MagicMock())
        client._control_board_cache["hp-1"] = "cb-old"
        client._request = AsyncMock(
            return_value=[
                {
                    "id": "inst-1",
                    "heatPump": {"id": "hp-1", "controlBoardId": "cb-new"},
                }
            ]
        )

        resolved = await client.async_get_control_board_id(
            "hp-1", token="test_tok", force_refresh=True
        )
        assert resolved == "cb-new"
        client._request.assert_called_once()

    @pytest.mark.asyncio
    async def test_unresolvable_target_id_raises_weheat_api_error(self) -> None:
        """Verify unresolvable target ID raises WeheatApiError after topology fetch."""
        client = WeheatClient(session=MagicMock())
        client._request = AsyncMock(return_value=[])  # Empty topology

        with pytest.raises(
            WeheatApiError,
            match="Control board ID could not be resolved for target 'missing-target'",
        ):
            await client.async_get_control_board_id("missing-target", token="tok")

    @pytest.mark.parametrize("empty_target", ["", "   ", "\t\n"])
    @pytest.mark.asyncio
    async def test_empty_or_whitespace_target_id_raises_value_error(
        self, empty_target: str
    ) -> None:
        """Verify empty or whitespace target ID raises ValueError immediately."""
        client = WeheatClient(session=MagicMock())
        with pytest.raises(ValueError, match="target_id must not be empty"):
            await client.async_get_control_board_id(empty_target)

    @pytest.mark.asyncio
    async def test_plural_heatpumps_topology_structure(self) -> None:
        """Verify parsing topology with plural heatPumps list."""
        client = WeheatClient(session=MagicMock())
        client._request = AsyncMock(
            return_value=[
                {
                    "id": "inst-plural-1",
                    "heatPumps": [
                        {
                            "id": "hp-plural-1",
                            "controlBoardId": "cb-plural-1",
                        }
                    ],
                }
            ]
        )

        topology = await client.async_get_installation_topology(token="tok")
        assert len(topology) == 1
        assert client._control_board_cache["hp-plural-1"] == "cb-plural-1"
        assert client._control_board_cache["inst-plural-1"] == "cb-plural-1"
        assert client._control_board_cache["cb-plural-1"] == "cb-plural-1"
        assert client._installation_cache["hp-plural-1"] == "inst-plural-1"
        assert client._installation_cache["cb-plural-1"] == "inst-plural-1"

    @pytest.mark.asyncio
    async def test_plural_multi_pump_topology_structure(self) -> None:
        """Verify parsing installation topology with multiple heat pumps on one site."""
        client = WeheatClient(session=MagicMock())
        client._request = AsyncMock(
            return_value=[
                {
                    "id": "inst-multi-site",
                    "heatPumps": [
                        {
                            "id": "hp-left",
                            "controlBoardId": "cb-left",
                        },
                        {
                            "id": "hp-right",
                            "controlBoardId": "cb-right",
                        },
                    ],
                }
            ]
        )

        await client.async_get_installation_topology(token="tok")
        assert client._control_board_cache["hp-left"] == "cb-left"
        assert client._control_board_cache["hp-right"] == "cb-right"
        assert client._installation_cache["hp-left"] == "inst-multi-site"
        assert client._installation_cache["hp-right"] == "inst-multi-site"
        assert client._installation_cache["cb-left"] == "inst-multi-site"
        assert client._installation_cache["cb-right"] == "inst-multi-site"

    @pytest.mark.asyncio
    async def test_singular_heatpump_topology_structure(self) -> None:
        """Verify parsing topology with singular heatPump object."""
        client = WeheatClient(session=MagicMock())
        client._request = AsyncMock(
            return_value=[
                {
                    "id": "inst-singular-1",
                    "heatPump": {
                        "id": "hp-singular-1",
                        "controlBoardId": "cb-singular-1",
                    },
                }
            ]
        )

        await client.async_get_installation_topology(token="tok")
        assert client._control_board_cache["hp-singular-1"] == "cb-singular-1"
        assert client._control_board_cache["inst-singular-1"] == "cb-singular-1"
        assert client._installation_cache["hp-singular-1"] == "inst-singular-1"

    @pytest.mark.asyncio
    async def test_flat_root_topology_structure(self) -> None:
        """Verify parsing flat topology where heatPumpId and controlBoardId are top-level."""
        client = WeheatClient(session=MagicMock())
        client._request = AsyncMock(
            return_value=[
                {
                    "id": "inst-flat-1",
                    "heatPumpId": "hp-flat-1",
                    "controlBoardId": "cb-flat-1",
                }
            ]
        )

        await client.async_get_installation_topology(token="tok")
        assert client._control_board_cache["hp-flat-1"] == "cb-flat-1"
        assert client._control_board_cache["inst-flat-1"] == "cb-flat-1"
        assert client._installation_cache["hp-flat-1"] == "inst-flat-1"

    @pytest.mark.asyncio
    async def test_empty_or_malformed_heatpumps_topology(self) -> None:
        """Verify robustness against empty lists or non-dict items in topology."""
        client = WeheatClient(session=MagicMock())
        client._request = AsyncMock(
            return_value=[
                {"id": "inst-empty", "heatPumps": []},
                {"id": "inst-malformed", "heatPumps": ["not_a_dict", None, 123]},
                {"id": "inst-none", "heatPump": None},
            ]
        )

        topology = await client.async_get_installation_topology(token="tok")
        assert len(topology) == 3

    @pytest.mark.asyncio
    async def test_topology_wrapped_in_data_dict_vs_raw_list(self) -> None:
        """Verify client parses both {'data': [...]} wrapper and direct [...] list."""
        client = WeheatClient(session=MagicMock())
        client._request = AsyncMock(
            return_value={
                "data": [
                    {
                        "id": "inst-wrapped",
                        "heatPump": {"id": "hp-w", "controlBoardId": "cb-w"},
                    }
                ]
            }
        )

        topology = await client.async_get_installation_topology(token="tok")
        assert len(topology) == 1
        assert client._control_board_cache["hp-w"] == "cb-w"

    def test_alias_async_get_installations_fully_expanded(self) -> None:
        """Verify async_get_installations_fully_expanded is an alias for async_get_installation_topology."""
        client = WeheatClient(session=MagicMock())
        assert (
            client.async_get_installations_fully_expanded
            == client.async_get_installation_topology
        )

    @pytest.mark.asyncio
    async def test_distinct_control_board_id_routing_in_patch_settings(self) -> None:
        """Verify target ID translation when heatPumpId is distinct from controlBoardId."""
        client = WeheatClient(session=MagicMock())
        client._control_board_cache["hp-distinct-1"] = "cb-target-99"
        client._request = AsyncMock(return_value={"settingsRequestId": "req-ok"})

        await client.async_patch_control_board_settings(
            control_board_id="hp-distinct-1",
            token="tok",
            settings={"PerformanceModePreset": 2},
        )

        call_args = client._request.call_args[0]
        method, url = call_args[0], call_args[1]
        assert method == "PATCH"
        assert "control-boards/cb-target-99/settings" in url
        assert "hp-distinct-1" not in url

    @pytest.mark.asyncio
    async def test_identical_control_board_id_routing_in_patch_settings(self) -> None:
        """Verify target ID translation when controlBoardId is identical to heatPumpId."""
        client = WeheatClient(session=MagicMock())
        client._control_board_cache["same-uuid-1"] = "same-uuid-1"
        client._request = AsyncMock(return_value={"settingsRequestId": "req-ok"})

        await client.async_patch_control_board_settings(
            control_board_id="same-uuid-1",
            token="tok",
            settings={"PerformanceModePreset": 1},
        )

        call_args = client._request.call_args[0]
        assert "control-boards/same-uuid-1/settings" in call_args[1]

    @pytest.mark.asyncio
    async def test_patch_settings_unwraps_already_wrapped_payload(self) -> None:
        """Verify passing an already wrapped {'settings': ..., 'updateImmediately': False} does not double wrap."""
        client = WeheatClient(session=MagicMock())
        client._request = AsyncMock(return_value={"settingsRequestId": "req-ok"})

        bundled = {
            "settings": {
                "PerformanceModePreset": 3,
                "SilentHoursScheduledPreset": 6,
            },
            "updateImmediately": False,
        }

        await client.async_patch_control_board_settings(
            control_board_id="cb-wrap-1",
            token="tok",
            settings=bundled,
        )

        kwargs = client._request.call_args.kwargs
        json_payload = kwargs["json"]
        assert "settings" in json_payload
        assert json_payload["updateImmediately"] is False
        # Must not have double settings: {"settings": {"settings": ...}}
        assert "settings" not in json_payload["settings"]
        assert json_payload["settings"]["PerformanceModePreset"] == 3

    @pytest.mark.asyncio
    async def test_patch_variables_routes_to_control_board_id(self) -> None:
        """Verify async_patch_control_board_variables translates heatPumpId to controlBoardId."""
        client = WeheatClient(session=MagicMock())
        client._control_board_cache["hp-var-1"] = "cb-var-1"
        client._request = AsyncMock(return_value={"success": True})

        res = await client.async_patch_control_board_variables(
            control_board_id="hp-var-1",
            token="tok",
            variables={"RequestLegionellaPrevention": 1},
        )

        assert res == {"success": True}
        call_args = client._request.call_args[0]
        assert "control-boards/cb-var-1/variables" in call_args[1]
        assert client._request.call_args.kwargs["json"] == {
            "variables": {"RequestLegionellaPrevention": 1}
        }

    @pytest.mark.asyncio
    async def test_get_control_board_settings_resolves_to_installation_id(self) -> None:
        """Verify async_get_control_board_settings translates target_id to installation_id."""
        client = WeheatClient(session=MagicMock())
        client._installation_cache["hp-target-1"] = "inst-actual-1"
        client._request = AsyncMock(return_value={"performanceModePreset": 0})

        settings = await client.async_get_control_board_settings(
            target_id="hp-target-1",
            token="tok",
        )

        assert isinstance(settings, ControlBoardSettings)
        call_args = client._request.call_args[0]
        assert "installations/inst-actual-1/settings" in call_args[1]

    @pytest.mark.asyncio
    async def test_get_control_board_settings_fallback_when_not_cached(self) -> None:
        """Verify async_get_control_board_settings triggers topology fetch if target not in cache."""
        client = WeheatClient(session=MagicMock())
        client._request = AsyncMock()

        # First call is topology discovery, second call is settings GET
        client._request.side_effect = [
            [
                {
                    "id": "inst-found-42",
                    "heatPump": {"id": "hp-unseen", "controlBoardId": "cb-42"},
                }
            ],
            {"performanceModePreset": 2},
        ]

        settings = await client.async_get_control_board_settings(
            target_id="hp-unseen",
            token="tok",
        )
        assert isinstance(settings, ControlBoardSettings)
        assert settings.performance_mode_preset == 2

        # Second request must target installations/inst-found-42/settings
        second_call = client._request.call_args_list[1][0]
        assert "installations/inst-found-42/settings" in second_call[1]

    @pytest.mark.parametrize("invalid_id", ["", "   "])
    @pytest.mark.asyncio
    async def test_patch_settings_empty_control_board_raises_value_error(
        self, invalid_id: str
    ) -> None:
        """Verify empty control_board_id in async_patch_control_board_settings raises ValueError."""
        client = WeheatClient(session=MagicMock())
        with pytest.raises(ValueError, match="control_board_id must not be empty"):
            await client.async_patch_control_board_settings(
                control_board_id=invalid_id,
                settings={"PerformanceModePreset": 1},
            )

    @pytest.mark.asyncio
    async def test_patch_settings_empty_settings_dict_raises_value_error(self) -> None:
        """Verify empty settings dict in async_patch_control_board_settings raises ValueError."""
        client = WeheatClient(session=MagicMock())
        with pytest.raises(ValueError, match="settings dictionary must not be empty"):
            await client.async_patch_control_board_settings(
                control_board_id="cb-1",
                settings={},
            )

    @pytest.mark.parametrize("invalid_id", ["", "   "])
    @pytest.mark.asyncio
    async def test_patch_variables_empty_control_board_raises_value_error(
        self, invalid_id: str
    ) -> None:
        """Verify empty control_board_id in async_patch_control_board_variables raises ValueError."""
        client = WeheatClient(session=MagicMock())
        with pytest.raises(ValueError, match="control_board_id must not be empty"):
            await client.async_patch_control_board_variables(
                control_board_id=invalid_id,
                variables={"reboot_mode": 1},
            )

    @pytest.mark.asyncio
    async def test_patch_variables_empty_variables_dict_raises_value_error(
        self,
    ) -> None:
        """Verify empty variables dict in async_patch_control_board_variables raises ValueError."""
        client = WeheatClient(session=MagicMock())
        with pytest.raises(ValueError, match="variables dictionary must not be empty"):
            await client.async_patch_control_board_variables(
                control_board_id="cb-1",
                variables={},
            )
