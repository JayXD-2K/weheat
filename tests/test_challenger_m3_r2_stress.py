"""Milestone 3 Iteration 2 Empirical Challenger Stress-Test Suite.

Exhaustive boundary testing of:
1. Matrix of daytime quiet presets (0..4) vs initial night limits (0..7), verifying auto-adjustment
   and preservation of active schedules.
2. Silent schedule dispatch across all 7 days, midnight wrap-arounds, boundary times (00:00, 23:59),
   time objects, and partial day updates.
3. Strict invariant checks on 30 PascalCase keys and payload bundling structure.
4. Graceful handling of invalid inputs and firmware constraint rejections.
"""

from __future__ import annotations

import datetime
import re
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ACCESS_TOKEN, CONF_TOKEN
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError

from custom_components.weheat.client import WeheatClient
from custom_components.weheat.const import (
    CONF_PORTAL_TOKEN,
    DAYS_OF_WEEK,
    DOMAIN,
)
from custom_components.weheat.coordinator import (
    WeheatData,
    WeheatPortalSession,
)
from custom_components.weheat.models import (
    ControlBoardSettings,
    DailySilentSchedule,
    HeatPumpInfo,
)
from custom_components.weheat.services import (
    SERVICE_SET_QUIET_MODE,
    SERVICE_SET_SILENT_SCHEDULE,
    async_handle_set_quiet_mode,
    async_handle_set_silent_schedule,
)


def _create_mock_hp_info(
    hp_id: str = "hp-stress-001", cb_id: str = "cb-stress-001"
) -> HeatPumpInfo:
    return HeatPumpInfo(
        heatpump_id=hp_id,
        name="Stress Test Heat Pump",
        model=0,
        serial_number="SN-STRESS-001",
        control_board_id=cb_id,
        installation_id="inst-stress-001",
        has_dhw=True,
    )


def _create_authorized_portal_session(hass: HomeAssistant) -> WeheatPortalSession:
    entry = ConfigEntry(
        entry_id="entry_stress_auth",
        data={CONF_TOKEN: {CONF_ACCESS_TOKEN: "tok_tp"}},
        options={
            CONF_PORTAL_TOKEN: {
                CONF_ACCESS_TOKEN: "tok_portal_jwt",
                "expires_at": 9999999999.0,
            }
        },
    )
    client = WeheatClient(session=MagicMock())
    return WeheatPortalSession(hass, entry, client)


def _setup_service_environment(
    hass: HomeAssistant,
    daytime_perf: int = 0,
    night_silent: int = 0,
    schedules: dict[str, DailySilentSchedule] | None = None,
) -> tuple[AsyncMock, WeheatData]:
    """Helper to register a mock WeheatData entry in hass with specified settings."""
    if schedules is None:
        schedules = {
            day: DailySilentSchedule(
                day=day, start_hour=19, start_minute=0, stop_hour=7, stop_minute=0
            )
            for day in DAYS_OF_WEEK
        }

    settings = ControlBoardSettings(
        performance_mode_preset=daytime_perf,
        silent_hours_preset=night_silent,
        schedules=schedules,
    )

    coord = MagicMock()
    coord.data = settings
    coord.async_request_refresh = AsyncMock()

    client = WeheatClient(session=MagicMock())
    mock_patch = AsyncMock(return_value="req-uuid-stress")
    client.async_patch_control_board_settings = mock_patch
    client.async_poll_settings_request = AsyncMock()

    hp_info = _create_mock_hp_info()
    auth_session = _create_authorized_portal_session(hass)

    data = WeheatData(
        heat_pump_info=hp_info,
        data_coordinator=coord,
        energy_coordinator=coord,
        settings_coordinator=coord,
        client=client,
        session=auth_session,
    )

    entry = ConfigEntry(
        entry_id=f"entry_stress_{daytime_perf}_{night_silent}",
        data={CONF_TOKEN: {CONF_ACCESS_TOKEN: "tok"}},
        runtime_data=[data],
    )
    hass.config_entries.async_add(entry)
    return mock_patch, data


# =============================================================================
# 1. Exhaustive Quiet Mode Matrix: Day Presets (0..4) vs Night Presets (0..7)
# =============================================================================


class TestQuietModeExhaustiveMatrix:
    """Stress-test all 40 combinations of daytime preset vs current night limit."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("day_preset", [0, 1, 2, 3, 4])
    @pytest.mark.parametrize("initial_night", [0, 1, 2, 3, 4, 5, 6, 7])
    async def test_set_quiet_mode_matrix(
        self, day_preset: int, initial_night: int
    ) -> None:
        """Verify daytime mode transition and proper night schedule preservation or auto-bump."""
        hass = HomeAssistant()
        mock_patch, _ = _setup_service_environment(
            hass,
            daytime_perf=0,
            night_silent=initial_night,
        )

        call = ServiceCall(
            DOMAIN,
            SERVICE_SET_QUIET_MODE,
            {
                "mode": day_preset,
            },
        )
        await async_handle_set_quiet_mode(hass, call)

        mock_patch.assert_called_once()
        sent_settings = mock_patch.call_args.kwargs["settings"]["settings"]

        # Daytime preset must always match requested mode
        assert sent_settings["PerformanceModePreset"] == day_preset

        outbound_night = sent_settings["SilentHoursScheduledPreset"]

        if initial_night == 0:
            # Schedule was off (0); must remain 0 and never be bumped or wiped
            assert outbound_night == 0, (
                f"Expected night limit to remain 0, got {outbound_night}"
            )
        elif initial_night == 5:
            # Schedule was off (5 / OFF_ALT); must remain 5
            assert outbound_night == 5, (
                f"Expected night limit to remain 5, got {outbound_night}"
            )
        elif day_preset >= initial_night:
            # Daytime meets or exceeds active night limit: firmware constraint requires auto-adjustment
            expected_bump = day_preset + 1 if day_preset < 4 else 6
            assert outbound_night == expected_bump, (
                f"Auto-adjustment failed for day_preset={day_preset}, initial_night={initial_night}: "
                f"expected {expected_bump}, got {outbound_night}"
            )
            # Invariant: outbound night must be strictly greater than daytime mode
            assert outbound_night > sent_settings["PerformanceModePreset"], (
                f"Constraint violation: outbound night {outbound_night} not > day {day_preset}"
            )
        else:
            # Daytime is strictly less than initial night: night preset must be preserved as-is!
            assert outbound_night == initial_night, (
                f"Active night limit corrupted! Expected {initial_night}, got {outbound_night}"
            )
            assert outbound_night > sent_settings["PerformanceModePreset"]


# =============================================================================
# 2. Silent Schedule Dispatch Across All 7 Days & Boundary Conditions
# =============================================================================


class TestSilentScheduleDayDispatchAndBoundaryTimes:
    """Stress-test silent schedule across days, boundary hours, and midnight wrap-around."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize("target_day", DAYS_OF_WEEK)
    async def test_single_day_update_preserves_other_6_days(
        self, target_day: str
    ) -> None:
        """Updating a single day must only mutate that day and preserve the other 6 days intact."""
        hass = HomeAssistant()
        # Seed all 7 days with known baseline: 19:15 -> 07:45
        initial_schedules = {
            d: DailySilentSchedule(
                day=d, start_hour=19, start_minute=15, stop_hour=7, stop_minute=45
            )
            for d in DAYS_OF_WEEK
        }
        mock_patch, _ = _setup_service_environment(
            hass,
            daytime_perf=0,
            night_silent=0,
            schedules=initial_schedules,
        )

        call = ServiceCall(
            DOMAIN,
            SERVICE_SET_SILENT_SCHEDULE,
            {
                "preset": 3,
                "start_time": "22:00",
                "stop_time": "06:30",
                "days": [target_day],
            },
        )
        await async_handle_set_silent_schedule(hass, call)

        mock_patch.assert_called_once()
        sent = mock_patch.call_args.kwargs["settings"]["settings"]

        # Target day must be updated
        assert sent[f"SilentHours{target_day}Start"] == 22
        assert sent[f"SilentMinutes{target_day}Start"] == 0
        assert sent[f"SilentHours{target_day}Stop"] == 6
        assert sent[f"SilentMinutes{target_day}Stop"] == 30

        # All other 6 days must retain initial baseline 19:15 -> 07:45
        for other_day in DAYS_OF_WEEK:
            if other_day == target_day:
                continue
            assert sent[f"SilentHours{other_day}Start"] == 19
            assert sent[f"SilentMinutes{other_day}Start"] == 15
            assert sent[f"SilentHours{other_day}Stop"] == 7
            assert sent[f"SilentMinutes{other_day}Stop"] == 45

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("start_str", "stop_str", "exp_sh", "exp_sm", "exp_eh", "exp_em"),
        [
            ("00:00", "23:59", 0, 0, 23, 59),  # Full day coverage boundary
            ("23:59", "00:00", 23, 59, 0, 0),  # Wrap around midnight edge
            ("00:59", "23:00", 0, 59, 23, 0),  # Inner minute boundaries
            ("22:45", "05:15", 22, 45, 5, 15),  # Realistic night shift
            ("00:00", "00:00", 0, 0, 0, 0),  # Zero duration / midnight
        ],
    )
    async def test_boundary_and_wrap_around_times(
        self,
        start_str: str,
        stop_str: str,
        exp_sh: int,
        exp_sm: int,
        exp_eh: int,
        exp_em: int,
    ) -> None:
        """Boundary and midnight-crossing times must parse and dispatch accurately."""
        hass = HomeAssistant()
        mock_patch, _ = _setup_service_environment(hass, daytime_perf=0, night_silent=0)

        call = ServiceCall(
            DOMAIN,
            SERVICE_SET_SILENT_SCHEDULE,
            {
                "preset": 2,
                "start_time": start_str,
                "stop_time": stop_str,
                "days": "all",
            },
        )
        await async_handle_set_silent_schedule(hass, call)

        sent = mock_patch.call_args.kwargs["settings"]["settings"]
        for day in DAYS_OF_WEEK:
            assert sent[f"SilentHours{day}Start"] == exp_sh
            assert sent[f"SilentMinutes{day}Start"] == exp_sm
            assert sent[f"SilentHours{day}Stop"] == exp_eh
            assert sent[f"SilentMinutes{day}Stop"] == exp_em

    @pytest.mark.asyncio
    async def test_datetime_time_objects_accepted(self) -> None:
        """Service must accept python datetime.time objects as start_time and stop_time."""
        hass = HomeAssistant()
        mock_patch, _ = _setup_service_environment(hass, daytime_perf=0, night_silent=0)

        call = ServiceCall(
            DOMAIN,
            SERVICE_SET_SILENT_SCHEDULE,
            {
                "preset": "Silent 2",
                "start_time": datetime.time(21, 45),
                "stop_time": datetime.time(6, 15),
                "days": ["Tuesday", "Thursday"],
            },
        )
        await async_handle_set_silent_schedule(hass, call)

        sent = mock_patch.call_args.kwargs["settings"]["settings"]
        assert sent["SilentHoursTuesdayStart"] == 21
        assert sent["SilentMinutesTuesdayStart"] == 45
        assert sent["SilentHoursTuesdayStop"] == 6
        assert sent["SilentMinutesTuesdayStop"] == 15
        assert sent["SilentHoursThursdayStart"] == 21
        assert sent["SilentMinutesThursdayStart"] == 45

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("day_spec", "expected_days"),
        [
            ("weekdays", ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]),
            ("weekday", ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]),
            ("weekend", ["Saturday", "Sunday"]),
            ("weekends", ["Saturday", "Sunday"]),
            ("*", list(DAYS_OF_WEEK)),
            ("all", list(DAYS_OF_WEEK)),
            ("Monday, Wednesday, Friday", ["Monday", "Wednesday", "Friday"]),
            (["Saturday", "Sunday"], ["Saturday", "Sunday"]),
        ],
    )
    async def test_day_specifiers(
        self, day_spec: Any, expected_days: list[str]
    ) -> None:
        """Keywords and sequence day specifiers must map correctly to targeted days."""
        hass = HomeAssistant()
        mock_patch, _ = _setup_service_environment(hass, daytime_perf=0, night_silent=0)

        call = ServiceCall(
            DOMAIN,
            SERVICE_SET_SILENT_SCHEDULE,
            {
                "preset": 4,
                "start_time": "20:00",
                "stop_time": "08:00",
                "days": day_spec,
            },
        )
        await async_handle_set_silent_schedule(hass, call)

        sent = mock_patch.call_args.kwargs["settings"]["settings"]
        for day in DAYS_OF_WEEK:
            if day in expected_days:
                assert sent[f"SilentHours{day}Start"] == 20
                assert sent[f"SilentHours{day}Stop"] == 8
            else:
                assert sent[f"SilentHours{day}Start"] == 19
                assert sent[f"SilentHours{day}Stop"] == 7


# =============================================================================
# 3. Payload Casing & 30-Key Invariant Verification
# =============================================================================


class TestPayloadBundlingInvariants:
    """Rigorous verification of the 30-key PascalCase dictionary and envelope."""

    PASCAL_CASE_REGEX = re.compile(r"^[A-Z][a-zA-Z0-9]+$")

    def test_default_payload_structure(self) -> None:
        """Empty settings must produce exactly 30 PascalCase keys wrapped in standard envelope."""
        settings = ControlBoardSettings()
        payload = settings.build_bundled_silent_schedule_payload()

        assert "settings" in payload
        assert "updateImmediately" in payload
        assert payload["updateImmediately"] is False

        inner = payload["settings"]
        assert len(inner) == 30, (
            f"Expected exactly 30 keys, got {len(inner)}: {list(inner.keys())}"
        )

        for key in inner:
            assert self.PASCAL_CASE_REGEX.match(key), f"Key '{key}' is not PascalCase"
            assert isinstance(inner[key], int), (
                f"Value for '{key}' is not int: {inner[key]}"
            )

    def test_payload_with_kwargs_and_day_updates(self) -> None:
        """snake_case, camelCase, and PascalCase updates must normalize properly into existing keys."""
        settings = ControlBoardSettings()
        payload = settings.build_bundled_silent_schedule_payload(
            performance_mode_preset=2,
            silent_hours_preset=3,
            day_updates={"SilentHoursMondayStart": 22},
            silent_hours_tuesday_start=23,
            silentHoursWednesdayStart=21,
            unknown_extra_kwarg=999,  # Must be ignored, not added
        )

        inner = payload["settings"]
        assert len(inner) == 30, (
            f"Extra keys injected! Found {len(inner)} keys: {list(inner.keys())}"
        )
        assert inner["PerformanceModePreset"] == 2
        assert inner["SilentHoursScheduledPreset"] == 3
        assert inner["SilentHoursMondayStart"] == 22
        assert inner["SilentHoursTuesdayStart"] == 23
        assert inner["SilentHoursWednesdayStart"] == 21
        assert "unknown_extra_kwarg" not in inner
        assert "UnknownExtraKwarg" not in inner

    def test_payload_invalid_type_graceful_suppression(self) -> None:
        """Invalid types in updates must be suppressed without crashing, retaining default int values."""
        settings = ControlBoardSettings()
        payload = settings.build_bundled_silent_schedule_payload(
            day_updates={"SilentHoursMondayStart": "not_an_int"},  # type: ignore[dict-item]
            silent_hours_tuesday_start=None,
            silent_hours_wednesday_start=[1, 2, 3],  # type: ignore[arg-type]
        )

        inner = payload["settings"]
        assert len(inner) == 30
        assert inner["SilentHoursMondayStart"] == 19  # Default retained
        assert inner["SilentHoursTuesdayStart"] == 19
        assert inner["SilentHoursWednesdayStart"] == 19


# =============================================================================
# 4. Error Handling and Firmware Constraint Rejections
# =============================================================================


class TestServiceConstraintAndInputValidation:
    """Stress-test error paths and firmware constraint enforcement in services."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "invalid_mode", ["turbo", "extreme", -1, 5, True, False, ""]
    )
    async def test_set_quiet_mode_invalid_values_rejected(
        self, invalid_mode: Any
    ) -> None:
        """Invalid quiet mode values must raise HomeAssistantError immediately."""
        hass = HomeAssistant()
        _setup_service_environment(hass, daytime_perf=0, night_silent=0)

        call = ServiceCall(DOMAIN, SERVICE_SET_QUIET_MODE, {"mode": invalid_mode})
        with pytest.raises(HomeAssistantError):
            await async_handle_set_quiet_mode(hass, call)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "invalid_time", ["24:00", "12:60", "-1:00", "invalid", 1234, ""]
    )
    async def test_set_silent_schedule_invalid_time_rejected(
        self, invalid_time: Any
    ) -> None:
        """Invalid time formats must be rejected with HomeAssistantError."""
        hass = HomeAssistant()
        _setup_service_environment(hass, daytime_perf=0, night_silent=0)

        call = ServiceCall(
            DOMAIN,
            SERVICE_SET_SILENT_SCHEDULE,
            {
                "preset": 2,
                "start_time": invalid_time,
                "stop_time": "07:00",
            },
        )
        with pytest.raises(HomeAssistantError, match="Invalid time format"):
            await async_handle_set_silent_schedule(hass, call)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "invalid_days", ["Funday", "Noday", 123, ["Monday", "InvalidDay"]]
    )
    async def test_set_silent_schedule_invalid_days_rejected(
        self, invalid_days: Any
    ) -> None:
        """Invalid day strings or types must be rejected with HomeAssistantError."""
        hass = HomeAssistant()
        _setup_service_environment(hass, daytime_perf=0, night_silent=0)

        call = ServiceCall(
            DOMAIN,
            SERVICE_SET_SILENT_SCHEDULE,
            {
                "preset": 2,
                "start_time": "22:00",
                "stop_time": "07:00",
                "days": invalid_days,
            },
        )
        with pytest.raises(HomeAssistantError, match="Invalid day"):
            await async_handle_set_silent_schedule(hass, call)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("daytime_perf", "rejected_night_preset"),
        [
            (1, 1),
            (2, 1),
            (2, 2),
            (3, 1),
            (3, 2),
            (3, 3),
            (4, 1),
            (4, 2),
            (4, 3),
            (4, 4),
        ],
    )
    async def test_set_silent_schedule_firmware_constraint_rejection(
        self, daytime_perf: int, rejected_night_preset: int
    ) -> None:
        """Attempting to set night limit <= daytime perf must be rejected with HomeAssistantError."""
        hass = HomeAssistant()
        _setup_service_environment(hass, daytime_perf=daytime_perf, night_silent=0)

        call = ServiceCall(
            DOMAIN,
            SERVICE_SET_SILENT_SCHEDULE,
            {
                "preset": rejected_night_preset,
                "start_time": "22:00",
                "stop_time": "07:00",
            },
        )
        with pytest.raises(
            HomeAssistantError,
            match="Night limit preset must be strictly greater than daytime performance preset",
        ):
            await async_handle_set_silent_schedule(hass, call)

    @pytest.mark.asyncio
    @pytest.mark.parametrize("off_preset", [0, 5, "Off", "silent 5"])
    async def test_set_silent_schedule_off_allowed_regardless_of_daytime_perf(
        self, off_preset: Any
    ) -> None:
        """Disabling night limit (preset 0 or 5) is always permitted, even at max daytime performance 4."""
        hass = HomeAssistant()
        mock_patch, _ = _setup_service_environment(hass, daytime_perf=4, night_silent=6)

        call = ServiceCall(
            DOMAIN,
            SERVICE_SET_SILENT_SCHEDULE,
            {
                "preset": off_preset,
                "start_time": "22:00",
                "stop_time": "07:00",
            },
        )
        await async_handle_set_silent_schedule(hass, call)

        mock_patch.assert_called_once()
        sent = mock_patch.call_args.kwargs["settings"]["settings"]
        assert sent["SilentHoursScheduledPreset"] in (0, 5)
