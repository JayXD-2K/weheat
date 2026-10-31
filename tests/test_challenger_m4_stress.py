"""Milestone 4 Empirical Challenger Stress Test Suite.

Authored by Challenger 1 (m4_r1_chal1_gen2) to stress-test commit 4f6cb20:
1. SemVer compliance and strict synchronization across manifest.json, pyproject.toml, and uv.lock.
2. Comparison between manifest regex (^\\d+\\.\\d+\\.\\d+$) and strict SemVer 2.0.0 specification regex.
3. Robustness of ControlBoardSettings.from_api_dict across camelCase, PascalCase, mixed-casing,
   empty/missing/corrupted keys, envelope variations, and adversarial type injections.
4. Guarantee that build_bundled_silent_schedule_payload produces exactly 30 PascalCase keys
   under all degraded, corrupted, and adversarial configurations.
"""

from __future__ import annotations

import json
import re
import tomllib
from pathlib import Path
from typing import Any

import pytest

from custom_components.weheat.const import DAYS_OF_WEEK
from custom_components.weheat.models import (
    ControlBoardSettings,
    DailySilentSchedule,
)

# Official SemVer 2.0.0 Regular Expression (https://semver.org/)
SEMVER_2_REGEX = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
    r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
)

# Home Assistant test_manifest.py regex
HA_MANIFEST_REGEX = re.compile(r"^\d+\.\d+\.\d+$")

REPO_ROOT = Path(__file__).resolve().parent.parent


class TestSemVerRobustnessAndSynchronization:
    """Empirical verification of versioning constraints across repository files."""

    def test_manifest_version_semver_compliance(self) -> None:
        """Validate manifest.json version complies with SemVer 2.0.0 and HA standards."""
        manifest_path = REPO_ROOT / "custom_components" / "weheat" / "manifest.json"
        assert manifest_path.is_file(), f"Manifest file missing at {manifest_path}"

        with manifest_path.open(encoding="utf-8") as f:
            manifest = json.load(f)

        version = manifest.get("version")
        assert version is not None, "manifest.json must contain a 'version' key"
        assert isinstance(version, str), (
            f"Version must be string, got {type(version).__name__}"
        )
        assert version == "0.1.5", (
            f"Expected initial pre-release version 0.1.5, got {version}"
        )

        # SemVer 2.0.0 strict format check
        match_semver = SEMVER_2_REGEX.match(version)
        assert match_semver is not None, (
            f"Version '{version}' violates SemVer 2.0.0 specification"
        )

        # Home Assistant manifest regex check
        match_ha = HA_MANIFEST_REGEX.match(version)
        assert match_ha is not None, (
            f"Version '{version}' must conform to X.Y.Z format for Home Assistant"
        )

    def test_version_synchronization_across_all_package_manifests(self) -> None:
        """Verify version is exactly synchronized across manifest.json, pyproject.toml, and uv.lock."""
        manifest_path = REPO_ROOT / "custom_components" / "weheat" / "manifest.json"
        pyproject_path = REPO_ROOT / "pyproject.toml"
        uv_lock_path = REPO_ROOT / "uv.lock"

        with manifest_path.open(encoding="utf-8") as f:
            manifest = json.load(f)
        manifest_version = manifest["version"]

        with pyproject_path.open("rb") as f:
            pyproject = tomllib.load(f)
        pyproject_version = pyproject.get("project", {}).get("version")

        with uv_lock_path.open("rb") as f:
            uv_lock = tomllib.load(f)

        lock_packages = uv_lock.get("package", [])
        weheat_package = next(
            (p for p in lock_packages if p.get("name") == "weheat-custom"), None
        )
        assert weheat_package is not None, "weheat-custom package not found in uv.lock"
        uv_lock_version = weheat_package.get("version")

        # Invariant: All three must match exactly
        assert manifest_version == pyproject_version == uv_lock_version == "0.1.5", (
            f"Version synchronization failure: manifest={manifest_version}, "
            f"pyproject={pyproject_version}, uv.lock={uv_lock_version}"
        )

    @pytest.mark.parametrize(
        "invalid_version",
        [
            "",
            "1",
            "1.0",
            "v1.1.0",
            "1.1.0.0",
            "1.1.a",
            "1.1.-0",
            "1.1.0.",
            ".1.1.0",
            "1.1.0-beta",
            "1.1.0-alpha..1",
            "1.1.0+build.123",
            "random_string",
        ],
    )
    def test_invalid_versions_rejected_by_manifest_regex(
        self, invalid_version: str
    ) -> None:
        """Verify that flawed version strings fail the manifest X.Y.Z regex."""
        ha_match = HA_MANIFEST_REGEX.match(invalid_version)
        assert ha_match is None, (
            f"Invalid version '{invalid_version}' unexpectedly passed HA manifest regex"
        )

    @pytest.mark.parametrize(
        "leading_zero_version",
        [
            "01.1.0",
            "1.01.0",
            "1.1.01",
            "00.00.00",
        ],
    )
    def test_leading_zero_versions_rejected_by_semver_spec(
        self, leading_zero_version: str
    ) -> None:
        """SemVer 2.0.0 Section 2 strictly forbids leading zeroes; verify spec regex enforces this."""
        spec_match = SEMVER_2_REGEX.match(leading_zero_version)
        assert spec_match is None, (
            f"Leading zero version '{leading_zero_version}' must be rejected by SemVer 2.0.0"
        )
        # Note: manifest regex ^\d+\.\d+\.\d+$ accepts leading zeroes, but 1.1.0 has none
        assert HA_MANIFEST_REGEX.match(leading_zero_version) is not None


class TestControlBoardSettingsDualCasingStress:
    """Stress tests on ControlBoardSettings.from_api_dict across casing and format variations."""

    def test_pure_camelcase_and_pure_pascalcase_exact_parity(
        self,
        installation_settings_fixture: dict[str, Any],
        installation_settings_pascal_fixture: dict[str, Any],
    ) -> None:
        """Verify that authentic portal camelCase and legacy PascalCase fixtures parse to identical settings."""
        s_camel = ControlBoardSettings.from_api_dict(installation_settings_fixture)
        s_pascal = ControlBoardSettings.from_api_dict(
            installation_settings_pascal_fixture
        )

        assert s_camel.performance_mode_preset == s_pascal.performance_mode_preset == 0
        assert s_camel.silent_hours_preset == s_pascal.silent_hours_preset == 2
        assert (
            s_camel.silent_hours_scheduled_preset
            == s_pascal.silent_hours_scheduled_preset
            == 2
        )
        assert s_camel.dhw_target_temperature == s_pascal.dhw_target_temperature == 50.0

        for day in DAYS_OF_WEEK:
            assert s_camel.schedules[day] == s_pascal.schedules[day]
            assert s_camel.schedules[day].start_time_str == "19:00"
            assert s_camel.schedules[day].stop_time_str == "07:00"

    def test_mixed_casing_interleaved_in_single_payload(self) -> None:
        """Verify mixed casing within a single payload correctly populates all fields."""
        payload = {
            "settings": {
                # Preset in camelCase
                "performanceModePreset": 1,
                # Night limit in PascalCase
                "SilentHoursScheduledPreset": 3,
                # Monday: mixed keys
                "silentHoursMondayStart": 20,
                "SilentMinutesMondayStart": 45,
                "SilentHoursMondayStop": 6,
                "silentMinutesMondayStop": 15,
                # Tuesday: all camelCase
                "silentHoursTuesdayStart": 22,
                "silentMinutesTuesdayStart": 0,
                "silentHoursTuesdayStop": 8,
                "silentMinutesTuesdayStop": 0,
                # Wednesday: all PascalCase
                "SilentHoursWednesdayStart": 21,
                "SilentMinutesWednesdayStart": 30,
                "SilentHoursWednesdayStop": 7,
                "SilentMinutesWednesdayStop": 30,
                # DHW in PascalCase
                "DhwTargetTemperature": 56.5,
            }
        }

        settings = ControlBoardSettings.from_api_dict(payload)
        assert settings.performance_mode_preset == 1
        assert settings.silent_hours_preset == 3
        assert settings.dhw_target_temperature == 56.5

        # Monday assertions
        mon = settings.schedules["Monday"]
        assert (mon.start_hour, mon.start_minute, mon.stop_hour, mon.stop_minute) == (
            20,
            45,
            6,
            15,
        )
        assert mon.start_time_str == "20:45"
        assert mon.stop_time_str == "06:15"

        # Tuesday assertions
        tue = settings.schedules["Tuesday"]
        assert (tue.start_hour, tue.start_minute, tue.stop_hour, tue.stop_minute) == (
            22,
            0,
            8,
            0,
        )

        # Wednesday assertions
        wed = settings.schedules["Wednesday"]
        assert (wed.start_hour, wed.start_minute, wed.stop_hour, wed.stop_minute) == (
            21,
            30,
            7,
            30,
        )

        # Unspecified days default safely
        thu = settings.schedules["Thursday"]
        assert (thu.start_hour, thu.start_minute, thu.stop_hour, thu.stop_minute) == (
            19,
            0,
            7,
            0,
        )

    def test_casing_priority_conflict_resolution(self) -> None:
        """When both camelCase and PascalCase are supplied, verify camelCase priority and fallback."""
        # 1. When both exist with conflicting valid values, camelCase wins
        conflicting = {
            "settings": {
                "performanceModePreset": 3,
                "PerformanceModePreset": 1,
                "silentHoursScheduledPreset": 4,
                "SilentHoursScheduledPreset": 2,
                "dhwTargetTemperature": 52.0,
                "DhwTargetTemperature": 60.0,
            }
        }
        s1 = ControlBoardSettings.from_api_dict(conflicting)
        assert s1.performance_mode_preset == 3
        assert s1.silent_hours_preset == 4
        assert s1.dhw_target_temperature == 52.0

        # 2. When camelCase is None and PascalCase has value, PascalCase fallback activates
        none_fallback = {
            "settings": {
                "performanceModePreset": None,
                "PerformanceModePreset": 2,
                "silentHoursScheduledPreset": None,
                "SilentHoursScheduledPreset": 3,
                "dhwTargetTemperature": None,
                "DhwTargetTemperature": 54.0,
            }
        }
        s2 = ControlBoardSettings.from_api_dict(none_fallback)
        assert s2.performance_mode_preset == 2
        assert s2.silent_hours_preset == 3
        assert s2.dhw_target_temperature == 54.0

        # 3. When both are None, default is returned
        both_none = {
            "settings": {
                "performanceModePreset": None,
                "PerformanceModePreset": None,
                "silentHoursScheduledPreset": None,
                "SilentHoursScheduledPreset": None,
                "dhwTargetTemperature": None,
                "DhwTargetTemperature": None,
            }
        }
        s3 = ControlBoardSettings.from_api_dict(both_none)
        assert s3.performance_mode_preset == 0
        assert s3.silent_hours_preset == 0
        assert s3.dhw_target_temperature == 50.0

    def test_envelope_variations(self) -> None:
        """Verify flat dictionaries, 'settings' envelope, 'Settings' envelope, and malformed envelopes."""
        # Flat dictionary without envelope
        flat = {
            "performanceModePreset": 2,
            "silentHoursScheduledPreset": 1,
            "dhwTargetTemperature": 48.0,
        }
        s_flat = ControlBoardSettings.from_api_dict(flat)
        assert s_flat.performance_mode_preset == 2
        assert s_flat.silent_hours_preset == 1
        assert s_flat.dhw_target_temperature == 48.0

        # PascalCase 'Settings' envelope
        pascal_envelope = {
            "Settings": {
                "PerformanceModePreset": 4,
                "SilentHoursScheduledPreset": 7,
                "DhwTargetTemperature": 59.0,
            }
        }
        s_pascal = ControlBoardSettings.from_api_dict(pascal_envelope)
        assert s_pascal.performance_mode_preset == 4
        assert s_pascal.silent_hours_preset == 7
        assert s_pascal.dhw_target_temperature == 59.0

        # Corrupted envelope types (None, string, list) falling back to root dictionary
        for bad_env in [None, "invalid_string", [1, 2, 3], 42]:
            corrupted_envelope = {
                "settings": bad_env,
                "performanceModePreset": 1,
                "silentHoursScheduledPreset": 2,
            }
            s_corrupt = ControlBoardSettings.from_api_dict(corrupted_envelope)
            assert s_corrupt.performance_mode_preset == 1
            assert s_corrupt.silent_hours_preset == 2

    def test_dhw_target_temperature_hierarchy(self) -> None:
        """Verify DHW target temperature extraction priority across settings and dhw blocks."""
        # Case 1: dhwTargetTemperature in settings overrides dhw.targetTemperature
        payload1 = {
            "settings": {"dhwTargetTemperature": 52.0},
            "dhw": {"targetTemperature": 45.0},
        }
        assert (
            ControlBoardSettings.from_api_dict(payload1).dhw_target_temperature == 52.0
        )

        # Case 2: Absence in settings falls back to dhw.targetTemperature
        payload2 = {
            "settings": {},
            "dhw": {"targetTemperature": 45.0},
        }
        assert (
            ControlBoardSettings.from_api_dict(payload2).dhw_target_temperature == 45.0
        )

        # Case 3: dhw.targetTemperature is invalid string -> falls back to default 50.0
        payload3 = {
            "settings": {},
            "dhw": {"targetTemperature": "corrupted"},
        }
        assert (
            ControlBoardSettings.from_api_dict(payload3).dhw_target_temperature == 50.0
        )

        # Case 4: dhw block is not a dict
        payload4 = {
            "settings": {},
            "dhw": "not_a_dict",
        }
        assert (
            ControlBoardSettings.from_api_dict(payload4).dhw_target_temperature == 50.0
        )


class TestAdversarialInputsAndTypeCoercion:
    """Stress testing ControlBoardSettings against malformed types, fuzz data, and edge conditions."""

    @pytest.mark.parametrize(
        "non_dict_input",
        [
            None,
            "",
            "invalid_json_string",
            123,
            45.67,
            [],
            [{"settings": {}}],
            True,
            False,
            object(),
        ],
    )
    def test_non_dict_and_none_inputs_return_safe_defaults(
        self, non_dict_input: Any
    ) -> None:
        """Passing non-dict inputs must never raise an exception and returns safe defaults."""
        settings = ControlBoardSettings.from_api_dict(non_dict_input)
        assert isinstance(settings, ControlBoardSettings)
        assert settings.performance_mode_preset == 0
        assert settings.silent_hours_preset == 0
        assert settings.dhw_target_temperature == 50.0

    def test_empty_dictionary_initializes_all_seven_schedules(self) -> None:
        """Passing empty dict {} initializes all 7 days with 19:00-07:00 defaults."""
        settings = ControlBoardSettings.from_api_dict({})
        assert len(settings.schedules) == 7
        for day in DAYS_OF_WEEK:
            assert day in settings.schedules
            sched = settings.schedules[day]
            assert sched.start_hour == 19
            assert sched.start_minute == 0
            assert sched.stop_hour == 7
            assert sched.stop_minute == 0
            assert sched.start_time_str == "19:00"
            assert sched.stop_time_str == "07:00"

    def test_numeric_string_coercion(self) -> None:
        """Verify numeric strings are coerced cleanly to int / float."""
        payload = {
            "settings": {
                "performanceModePreset": "3",
                "silentHoursScheduledPreset": "4",
                "silentHoursMondayStart": "21",
                "silentMinutesMondayStart": "15",
                "silentHoursMondayStop": "08",
                "silentMinutesMondayStop": "00",
                "dhwTargetTemperature": "55.5",
            }
        }
        settings = ControlBoardSettings.from_api_dict(payload)
        assert settings.performance_mode_preset == 3
        assert settings.silent_hours_preset == 4
        assert settings.dhw_target_temperature == 55.5
        mon = settings.schedules["Monday"]
        assert (mon.start_hour, mon.start_minute, mon.stop_hour, mon.stop_minute) == (
            21,
            15,
            8,
            0,
        )

    @pytest.mark.parametrize(
        ("bad_preset", "expected_default"),
        [
            ("corrupted_text", 0),
            ("", 0),
            ([1, 2], 0),
            ({"nested": 1}, 0),
            (None, 0),
            ("19.5", 0),  # int("19.5") raises ValueError
        ],
    )
    def test_malformed_preset_types_fall_back_to_defaults(
        self, bad_preset: Any, expected_default: int
    ) -> None:
        """Verify invalid preset types fall back to default safely without raising ValueError/TypeError."""
        payload = {
            "settings": {
                "performanceModePreset": bad_preset,
                "silentHoursScheduledPreset": bad_preset,
            }
        }
        settings = ControlBoardSettings.from_api_dict(payload)
        assert settings.performance_mode_preset == expected_default
        assert settings.silent_hours_preset == expected_default

    def test_property_alias_and_setter_resilience(self) -> None:
        """Verify silent_hours_scheduled_preset property alias getter and setter."""
        settings = ControlBoardSettings(silent_hours_preset=2)
        assert settings.silent_hours_scheduled_preset == 2

        # Setter with integer
        settings.silent_hours_scheduled_preset = 4
        assert settings.silent_hours_preset == 4
        assert settings.silent_hours_scheduled_preset == 4

        # Setter with string integer
        settings.silent_hours_scheduled_preset = "6"  # type: ignore[assignment]
        assert settings.silent_hours_preset == 6

    def test_extra_and_unknown_keys_tolerated(self) -> None:
        """Verify unknown or forward-compatible API keys are safely ignored and preserved in raw_settings."""
        payload = {
            "settings": {
                "performanceModePreset": 2,
                "silentHoursScheduledPreset": 3,
                "unknownFutureFeature": "beta",
                "hpc_compressor_target_frequency": 65.5,
                "installer_unlocked": True,
                "arbitraryList": [10, 20, 30],
            }
        }
        settings = ControlBoardSettings.from_api_dict(payload)
        assert settings.performance_mode_preset == 2
        assert settings.silent_hours_preset == 3
        assert "unknownFutureFeature" in settings.raw_settings
        assert settings.raw_settings["installer_unlocked"] is True


class TestBundledPayloadGenerationInvariants:
    """Stress tests on build_bundled_silent_schedule_payload invariants."""

    @pytest.mark.parametrize(
        "source_data",
        [
            None,
            {},
            {"performanceModePreset": 3},
            {"settings": {"SilentHoursScheduledPreset": 4}},
            {
                "settings": {
                    "performanceModePreset": "invalid",
                    "silentHoursMondayStart": [1, 2],
                }
            },
        ],
    )
    def test_bundled_payload_always_generates_30_pascalcase_keys(
        self, source_data: Any
    ) -> None:
        """Regardless of input corruption, build_bundled_silent_schedule_payload must produce 30 PascalCase keys."""
        settings = ControlBoardSettings.from_api_dict(source_data)
        payload = settings.build_bundled_silent_schedule_payload()

        assert "settings" in payload, "Missing 'settings' key in bundled payload"
        assert payload.get("updateImmediately") is False, (
            "updateImmediately must be False"
        )

        inner = payload["settings"]
        assert len(inner) == 30, f"Expected exactly 30 keys, got {len(inner)}"

        # Verify core preset keys
        assert "PerformanceModePreset" in inner
        assert "SilentHoursScheduledPreset" in inner
        assert isinstance(inner["PerformanceModePreset"], int)
        assert isinstance(inner["SilentHoursScheduledPreset"], int)

        # Verify all 28 day schedule keys
        for day in DAYS_OF_WEEK:
            for field in ["Start", "Stop"]:
                hour_key = f"SilentHours{day}{field}"
                minute_key = f"SilentMinutes{day}{field}"
                assert hour_key in inner, f"Missing key {hour_key}"
                assert minute_key in inner, f"Missing key {minute_key}"
                assert isinstance(inner[hour_key], int)
                assert isinstance(inner[minute_key], int)
                assert 0 <= inner[hour_key] <= 23, (
                    f"Hour out of bounds: {inner[hour_key]}"
                )
                assert 0 <= inner[minute_key] <= 59, (
                    f"Minute out of bounds: {inner[minute_key]}"
                )

    def test_schedule_partial_overrides_in_bundled_payload(self) -> None:
        """Verify that passing partial keyword arguments or day_updates correctly mutates target keys."""
        settings = ControlBoardSettings(
            performance_mode_preset=1,
            silent_hours_preset=2,
            schedules={
                day: DailySilentSchedule(
                    day=day, start_hour=19, start_minute=0, stop_hour=7, stop_minute=0
                )
                for day in DAYS_OF_WEEK
            },
        )

        # Mutate Monday start and Friday stop via day_updates
        payload = settings.build_bundled_silent_schedule_payload(
            day_updates={
                "silentHoursMondayStart": 22,
                "silent_minutes_friday_stop": 45,
            }
        )

        inner = payload["settings"]
        assert inner["SilentHoursMondayStart"] == 22
        assert inner["SilentMinutesMondayStart"] == 0
        assert inner["SilentMinutesFridayStop"] == 45
        # Other days untouched
        assert inner["SilentHoursTuesdayStart"] == 19
        assert inner["SilentHoursSundayStop"] == 7
