"""Milestone 3 Empirical Adversarial Test Harness.

Adversarial stress-testing of:
1. Portal authentication guards across select, switch, time, and services.
2. Live firmware constraint enforcement (Night Limit > Daytime Performance).
3. Time entity 30-key atomic bundling and schedule preservation.
4. Presets 6 and 7 handling across entities and services.
"""

from __future__ import annotations

import datetime
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
from custom_components.weheat.select import (
    SELECT_ENTITIES,
    WeheatPerformanceModeSelect,
    WeheatSilentHoursPresetSelect,
)
from custom_components.weheat.services import (
    SERVICE_SET_QUIET_MODE,
    SERVICE_SET_SILENT_SCHEDULE,
    SERVICE_TRIGGER_LEGIONELLA,
    async_handle_set_quiet_mode,
    async_handle_set_silent_schedule,
    async_handle_trigger_legionella,
)
from custom_components.weheat.switch import (
    SWITCH_DESCRIPTIONS,
    WeheatDhwBoostSwitch,
    WeheatLegionellaPreventionSwitch,
    WeheatSilentHoursScheduleSwitch,
)
from custom_components.weheat.time import (
    TIME_ENTITIES,
    WeheatTimeEntity,
)

# =============================================================================
# Helper Fixtures & Builders
# =============================================================================


def _create_mock_heat_pump_info() -> HeatPumpInfo:
    """Create a fully-populated mock HeatPumpInfo instance."""
    return HeatPumpInfo(
        heatpump_id="hp-adv-uuid-001",
        name="Adversarial Test Heat Pump",
        model=0,
        serial_number="SN-TEST-ADV",
        control_board_id="cb-adv-uuid-001",
        installation_id="inst-adv-uuid-001",
        has_dhw=True,
    )


def _create_unauthorized_portal_session(hass: HomeAssistant) -> WeheatPortalSession:
    """Create a WeheatPortalSession without portal authentication."""
    entry = ConfigEntry(
        entry_id="entry_unauth",
        data={CONF_TOKEN: {CONF_ACCESS_TOKEN: "third_party_readonly_token"}},
        options={},
    )
    client = WeheatClient(session=MagicMock())
    return WeheatPortalSession(hass, entry, client)


def _create_authorized_portal_session(hass: HomeAssistant) -> WeheatPortalSession:
    """Create a WeheatPortalSession with valid portal authentication."""
    entry = ConfigEntry(
        entry_id="entry_auth",
        data={CONF_TOKEN: {CONF_ACCESS_TOKEN: "third_party_token"}},
        options={
            CONF_PORTAL_TOKEN: {
                CONF_ACCESS_TOKEN: "valid_portal_jwt",
                "expires_at": 9999999999.0,
            }
        },
    )
    client = WeheatClient(session=MagicMock())
    return WeheatPortalSession(hass, entry, client)


# =============================================================================
# 1. Auth Guard Stress Tests
# =============================================================================


class TestAuthGuards:
    """Empirical challenge: write methods without portal auth must raise actionable HomeAssistantError."""

    @pytest.mark.asyncio
    async def test_select_entities_unauthorized_portal_session(self) -> None:
        """Select entities with unauthorized portal session must raise HomeAssistantError."""
        hass = HomeAssistant()
        unauth_session = _create_unauthorized_portal_session(hass)
        hp_info = _create_mock_heat_pump_info()

        coord = MagicMock()
        coord.data = ControlBoardSettings()
        client = WeheatClient(session=MagicMock())

        # Performance mode select
        perf_select = WeheatPerformanceModeSelect(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=SELECT_ENTITIES[0],
            client=client,
            session=unauth_session,
        )
        with pytest.raises(
            HomeAssistantError,
            match="Weheat Portal credentials required for control actions",
        ):
            await perf_select.async_select_option("Silent 1")

        # Silent hours preset select
        silent_select = WeheatSilentHoursPresetSelect(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=SELECT_ENTITIES[1],
            client=client,
            session=unauth_session,
        )
        with pytest.raises(
            HomeAssistantError,
            match="Weheat Portal credentials required for control actions",
        ):
            await silent_select.async_select_option("Off")

    @pytest.mark.asyncio
    async def test_switch_entities_unauthorized_portal_session(self) -> None:
        """All switch entities with unauthorized portal session must raise HomeAssistantError."""
        hass = HomeAssistant()
        unauth_session = _create_unauthorized_portal_session(hass)
        hp_info = _create_mock_heat_pump_info()

        coord = MagicMock()
        coord.data = ControlBoardSettings()
        client = WeheatClient(session=MagicMock())

        # Silent schedule switch
        sched_switch = WeheatSilentHoursScheduleSwitch(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=SWITCH_DESCRIPTIONS[0],
            client=client,
            session=unauth_session,
        )
        with pytest.raises(
            HomeAssistantError,
            match="Weheat Portal credentials required for control actions",
        ):
            await sched_switch.async_turn_on()

        with pytest.raises(
            HomeAssistantError,
            match="Weheat Portal credentials required for control actions",
        ):
            await sched_switch.async_turn_off()

        # Legionella prevention switch
        leg_switch = WeheatLegionellaPreventionSwitch(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=SWITCH_DESCRIPTIONS[1],
            client=client,
            session=unauth_session,
        )
        with pytest.raises(
            HomeAssistantError,
            match="Weheat Portal credentials required for control actions",
        ):
            await leg_switch.async_turn_on()

        with pytest.raises(
            HomeAssistantError,
            match="Weheat Portal credentials required for control actions",
        ):
            await leg_switch.async_turn_off()

        # DHW boost switch
        dhw_switch = WeheatDhwBoostSwitch(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=SWITCH_DESCRIPTIONS[2],
            client=client,
            session=unauth_session,
        )
        with pytest.raises(
            HomeAssistantError,
            match="Weheat Portal credentials required for control actions",
        ):
            await dhw_switch.async_turn_on()

    @pytest.mark.asyncio
    async def test_time_entity_unauthorized_portal_session(self) -> None:
        """Time entities with unauthorized portal session must raise HomeAssistantError."""
        hass = HomeAssistant()
        unauth_session = _create_unauthorized_portal_session(hass)
        hp_info = _create_mock_heat_pump_info()

        coord = MagicMock()
        coord.data = ControlBoardSettings()
        client = WeheatClient(session=MagicMock())

        time_entity = WeheatTimeEntity(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=TIME_ENTITIES[0],  # Monday Start
            client=client,
            session=unauth_session,
        )

        with pytest.raises(
            HomeAssistantError,
            match="Weheat Portal credentials required for control actions",
        ):
            await time_entity.async_set_value(datetime.time(20, 0))

    @pytest.mark.asyncio
    async def test_services_unauthorized_portal_session(self) -> None:
        """Services with unauthorized portal session must raise HomeAssistantError."""
        hass = HomeAssistant()
        unauth_session = _create_unauthorized_portal_session(hass)
        hp_info = _create_mock_heat_pump_info()

        coord = MagicMock()
        coord.data = ControlBoardSettings()
        client = WeheatClient(session=MagicMock())

        data = WeheatData(
            heat_pump_info=hp_info,
            data_coordinator=coord,
            energy_coordinator=coord,
            client=client,
            session=unauth_session,
        )
        entry = ConfigEntry(
            entry_id="entry_unauth_services",
            data={CONF_TOKEN: {CONF_ACCESS_TOKEN: "tok"}},
            runtime_data=[data],
        )
        hass.config_entries.async_add(entry)

        # 1. set_quiet_mode
        call_quiet = ServiceCall(DOMAIN, SERVICE_SET_QUIET_MODE, {"mode": 1})
        with pytest.raises(
            HomeAssistantError,
            match="Weheat Portal credentials required for control actions",
        ):
            await async_handle_set_quiet_mode(hass, call_quiet)

        # 2. set_silent_schedule
        call_sched = ServiceCall(
            DOMAIN,
            SERVICE_SET_SILENT_SCHEDULE,
            {"preset": 2, "start_time": "21:00", "stop_time": "07:00"},
        )
        with pytest.raises(
            HomeAssistantError,
            match="Weheat Portal credentials required for control actions",
        ):
            await async_handle_set_silent_schedule(hass, call_sched)

        # 3. trigger_legionella_prevention
        call_leg = ServiceCall(DOMAIN, SERVICE_TRIGGER_LEGIONELLA, {})
        with pytest.raises(
            HomeAssistantError,
            match="Weheat Portal credentials required for control actions",
        ):
            await async_handle_trigger_legionella(hass, call_leg)


# =============================================================================
# 2. Live Firmware Constraint Enforcement Tests
# =============================================================================


class TestFirmwareConstraints:
    """Empirical challenge: strict Night Limit > Daytime Performance inequality."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("daytime_perf", "invalid_night_preset", "invalid_option"),
        [
            (1, 1, "Silent 1"),
            (2, 1, "Silent 1"),
            (2, 2, "Silent 2"),
            (3, 1, "Silent 1"),
            (3, 2, "Silent 2"),
            (3, 3, "Silent 3"),
            (4, 1, "Silent 1"),
            (4, 2, "Silent 2"),
            (4, 3, "Silent 3"),
            (4, 4, "Silent 4"),
        ],
    )
    async def test_silent_hours_preset_select_rejection(
        self, daytime_perf: int, invalid_night_preset: int, invalid_option: str
    ) -> None:
        """Silent Hours Preset Select must reject any active night preset <= daytime perf."""
        hass = HomeAssistant()
        auth_session = _create_authorized_portal_session(hass)
        hp_info = _create_mock_heat_pump_info()

        coord = MagicMock()
        coord.data = ControlBoardSettings(
            performance_mode_preset=daytime_perf,
            silent_hours_preset=0,
        )
        client = WeheatClient(session=MagicMock())

        entity = WeheatSilentHoursPresetSelect(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=SELECT_ENTITIES[1],
            client=client,
            session=auth_session,
        )

        with pytest.raises(
            HomeAssistantError,
            match="Night limit preset must be strictly greater than daytime performance preset",
        ):
            await entity.async_select_option(invalid_option)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("daytime_perf", "invalid_preset"),
        [
            (1, 1),
            (2, 1),
            (2, 2),
            (3, 2),
            (3, 3),
            (4, 3),
            (4, 4),
        ],
    )
    async def test_service_set_silent_schedule_rejection(
        self, daytime_perf: int, invalid_preset: int
    ) -> None:
        """Service set_silent_schedule must reject any active night preset <= daytime perf."""
        hass = HomeAssistant()
        auth_session = _create_authorized_portal_session(hass)
        hp_info = _create_mock_heat_pump_info()

        coord = MagicMock()
        settings = ControlBoardSettings(
            performance_mode_preset=daytime_perf,
            silent_hours_preset=0,
        )
        coord.data = settings
        client = WeheatClient(session=MagicMock())

        data = WeheatData(
            heat_pump_info=hp_info,
            data_coordinator=coord,
            energy_coordinator=coord,
            settings_coordinator=coord,
            client=client,
            session=auth_session,
        )
        entry = ConfigEntry(
            entry_id="entry_sched_constraint",
            data={CONF_TOKEN: {CONF_ACCESS_TOKEN: "tok"}},
            runtime_data=[data],
        )
        hass.config_entries.async_add(entry)

        call = ServiceCall(
            DOMAIN,
            SERVICE_SET_SILENT_SCHEDULE,
            {
                "preset": invalid_preset,
                "start_time": "22:00",
                "stop_time": "06:00",
            },
        )

        with pytest.raises(
            HomeAssistantError,
            match="Night limit preset must be strictly greater than daytime performance preset",
        ):
            await async_handle_set_silent_schedule(hass, call)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("current_silent", "new_daytime_option", "expected_adjusted_night"),
        [
            (2, "Silent 2", 3),  # 2 meets 2 -> adjusts to 3
            (2, "Silent 3", 4),  # 3 exceeds 2 -> adjusts to 4
            (2, "Silent 4", 6),  # 4 exceeds 2 -> adjusts to 6 (skipping 5=Off)
            (3, "Silent 3", 4),  # 3 meets 3 -> adjusts to 4
            (3, "Silent 4", 6),  # 4 exceeds 3 -> adjusts to 6
            (4, "Silent 4", 6),  # 4 meets 4 -> adjusts to 6
        ],
    )
    async def test_performance_mode_select_auto_adjusts_night_limit(
        self,
        current_silent: int,
        new_daytime_option: str,
        expected_adjusted_night: int,
    ) -> None:
        """Increasing daytime performance to meet/exceed active night limit must auto-adjust."""
        hass = HomeAssistant()
        auth_session = _create_authorized_portal_session(hass)
        hp_info = _create_mock_heat_pump_info()

        coord = MagicMock()
        coord.data = ControlBoardSettings(
            performance_mode_preset=0,
            silent_hours_preset=current_silent,
        )
        coord.async_request_refresh = AsyncMock()

        client = WeheatClient(session=MagicMock())
        client.async_patch_control_board_settings = AsyncMock(return_value="req-adjust")
        client.async_poll_settings_request = AsyncMock()

        entity = WeheatPerformanceModeSelect(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=SELECT_ENTITIES[0],
            client=client,
            session=auth_session,
        )

        # Must not raise an error
        await entity.async_select_option(new_daytime_option)

        client.async_patch_control_board_settings.assert_called_once()
        sent_settings = client.async_patch_control_board_settings.call_args.kwargs[
            "settings"
        ]["settings"]

        assert sent_settings["SilentHoursScheduledPreset"] == expected_adjusted_night, (
            f"Expected night limit auto-adjusted to {expected_adjusted_night}, got {sent_settings['SilentHoursScheduledPreset']}"
        )
        assert (
            sent_settings["SilentHoursScheduledPreset"]
            > sent_settings["PerformanceModePreset"]
        ), "Firmware constraint violated in dispatched payload"

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("current_perf", "expected_night_preset"),
        [
            (0, 2),  # Default 2 > 0
            (1, 2),  # Default 2 > 1
            (2, 3),  # 2 <= 2 -> adjusts to 3
            (3, 4),  # 2 <= 3 -> adjusts to 4
            (4, 6),  # 2 <= 4 -> adjusts to 5 -> 6 (skipping 5=Off)
        ],
    )
    async def test_silent_hours_schedule_switch_turn_on_picks_valid_preset(
        self, current_perf: int, expected_night_preset: int
    ) -> None:
        """Turning ON Silent Hours Schedule switch must pick preset strictly > daytime perf."""
        hass = HomeAssistant()
        auth_session = _create_authorized_portal_session(hass)
        hp_info = _create_mock_heat_pump_info()

        coord = MagicMock()
        coord.data = ControlBoardSettings(
            performance_mode_preset=current_perf,
            silent_hours_preset=0,
        )
        coord.async_request_refresh = AsyncMock()

        client = WeheatClient(session=MagicMock())
        client.async_patch_control_board_settings = AsyncMock(return_value="req-sw-on")
        client.async_poll_settings_request = AsyncMock()

        entity = WeheatSilentHoursScheduleSwitch(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=SWITCH_DESCRIPTIONS[0],
            client=client,
            session=auth_session,
        )

        await entity.async_turn_on()

        client.async_patch_control_board_settings.assert_called_once()
        sent_settings = client.async_patch_control_board_settings.call_args.kwargs[
            "settings"
        ]["settings"]

        assert sent_settings["SilentHoursScheduledPreset"] == expected_night_preset
        assert sent_settings["SilentHoursScheduledPreset"] > current_perf, (
            f"Night preset {sent_settings['SilentHoursScheduledPreset']} not strictly greater than daytime {current_perf}"
        )


# =============================================================================
# 3. Time Entity Bundling & Schedule Preservation Tests
# =============================================================================


class TestTimeEntityBundling:
    """Empirical challenge: modifying 1 time entity produces 30 keys and preserves all other days/hours."""

    @pytest.mark.asyncio
    async def test_single_time_entity_edit_preserves_all_other_days(self) -> None:
        """Changing Wednesday stop time must preserve Monday, Tuesday, Thursday, Friday, Saturday, Sunday."""
        hass = HomeAssistant()
        auth_session = _create_authorized_portal_session(hass)
        hp_info = _create_mock_heat_pump_info()

        # Build highly specific non-default baseline schedules for each day
        initial_schedules: dict[str, DailySilentSchedule] = {
            "Monday": DailySilentSchedule("Monday", 22, 10, 6, 40),
            "Tuesday": DailySilentSchedule("Tuesday", 21, 30, 7, 15),
            "Wednesday": DailySilentSchedule("Wednesday", 20, 0, 5, 0),
            "Thursday": DailySilentSchedule("Thursday", 23, 45, 8, 30),
            "Friday": DailySilentSchedule("Friday", 22, 15, 7, 45),
            "Saturday": DailySilentSchedule("Saturday", 23, 0, 9, 0),
            "Sunday": DailySilentSchedule("Sunday", 21, 0, 6, 30),
        }

        settings_baseline = ControlBoardSettings(
            performance_mode_preset=1,
            silent_hours_preset=3,
            dhw_target_temperature=52.0,
            schedules=initial_schedules,
        )

        coord = MagicMock()
        coord.data = settings_baseline
        coord.async_request_refresh = AsyncMock()

        client = WeheatClient(session=MagicMock())
        client.async_patch_control_board_settings = AsyncMock(
            return_value="req-time-bundle"
        )
        client.async_poll_settings_request = AsyncMock()

        # Wednesday Stop time entity
        wed_stop_desc = next(
            d for d in TIME_ENTITIES if d.day == "Wednesday" and not d.is_start
        )
        entity = WeheatTimeEntity(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=wed_stop_desc,
            client=client,
            session=auth_session,
        )

        # Update Wednesday stop time from 05:00 to 07:45
        new_stop_time = datetime.time(7, 45)
        await entity.async_set_value(new_stop_time)

        client.async_patch_control_board_settings.assert_called_once()
        raw_payload = client.async_patch_control_board_settings.call_args.kwargs[
            "settings"
        ]

        assert raw_payload["updateImmediately"] is False, (
            "Payload must specify updateImmediately=False"
        )
        inner = raw_payload["settings"]

        # 1. Exactly 30 PascalCase keys
        assert len(inner) == 30, (
            f"Bundled payload must have exactly 30 keys, got {len(inner)}: {list(inner.keys())}"
        )

        # 2. Presets preserved
        assert inner["PerformanceModePreset"] == 1
        assert inner["SilentHoursScheduledPreset"] == 3

        # 3. Wednesday Stop modified
        assert inner["SilentHoursWednesdayStop"] == 7
        assert inner["SilentMinutesWednesdayStop"] == 45

        # 4. Wednesday Start preserved
        assert inner["SilentHoursWednesdayStart"] == 20
        assert inner["SilentMinutesWednesdayStart"] == 0

        # 5. All other 6 days preserved verbatim
        for day in ("Monday", "Tuesday", "Thursday", "Friday", "Saturday", "Sunday"):
            orig = initial_schedules[day]
            assert inner[f"SilentHours{day}Start"] == orig.start_hour
            assert inner[f"SilentMinutes{day}Start"] == orig.start_minute
            assert inner[f"SilentHours{day}Stop"] == orig.stop_hour
            assert inner[f"SilentMinutes{day}Stop"] == orig.stop_minute


# =============================================================================
# 4. Presets 6 and 7 Support Tests
# =============================================================================


class TestPresets6And7:
    """Empirical challenge: selecting Silent 6 and Silent 7 yields presets 6 and 7 outbound."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("option_str", "expected_preset_int"),
        [
            ("Silent 6", 6),
            ("Silent 7", 7),
        ],
    )
    async def test_select_preset_6_and_7(
        self, option_str: str, expected_preset_int: int
    ) -> None:
        """Selecting Silent 6 and Silent 7 on select entity produces 6 and 7 in payload."""
        hass = HomeAssistant()
        auth_session = _create_authorized_portal_session(hass)
        hp_info = _create_mock_heat_pump_info()

        coord = MagicMock()
        coord.data = ControlBoardSettings(
            performance_mode_preset=0,
            silent_hours_preset=0,
        )
        coord.async_request_refresh = AsyncMock()

        client = WeheatClient(session=MagicMock())
        client.async_patch_control_board_settings = AsyncMock(
            return_value="req-preset-67"
        )
        client.async_poll_settings_request = AsyncMock()

        entity = WeheatSilentHoursPresetSelect(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=SELECT_ENTITIES[1],
            client=client,
            session=auth_session,
        )

        # Check options list includes Silent 6 and 7
        assert "Silent 6" in entity.options
        assert "Silent 7" in entity.options

        await entity.async_select_option(option_str)

        client.async_patch_control_board_settings.assert_called_once()
        sent_settings = client.async_patch_control_board_settings.call_args.kwargs[
            "settings"
        ]["settings"]
        assert sent_settings["SilentHoursScheduledPreset"] == expected_preset_int

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("preset_arg", "expected_preset_int"),
        [
            ("Silent 6", 6),
            ("6", 6),
            (6, 6),
            ("Silent 7", 7),
            ("7", 7),
            (7, 7),
        ],
    )
    async def test_service_set_silent_schedule_presets_6_and_7(
        self, preset_arg: Any, expected_preset_int: int
    ) -> None:
        """Service set_silent_schedule accepts Silent 6 and Silent 7 in diverse representations."""
        hass = HomeAssistant()
        auth_session = _create_authorized_portal_session(hass)
        hp_info = _create_mock_heat_pump_info()

        coord = MagicMock()
        coord.data = ControlBoardSettings(
            performance_mode_preset=0,
            silent_hours_preset=0,
        )
        coord.async_request_refresh = AsyncMock()

        client = WeheatClient(session=MagicMock())
        client.async_patch_control_board_settings = AsyncMock(return_value="req-srv-67")
        client.async_poll_settings_request = AsyncMock()

        data = WeheatData(
            heat_pump_info=hp_info,
            data_coordinator=coord,
            energy_coordinator=coord,
            settings_coordinator=coord,
            client=client,
            session=auth_session,
        )
        entry = ConfigEntry(
            entry_id="entry_p67_service",
            data={CONF_TOKEN: {CONF_ACCESS_TOKEN: "tok"}},
            runtime_data=[data],
        )
        hass.config_entries.async_add(entry)

        call = ServiceCall(
            DOMAIN,
            SERVICE_SET_SILENT_SCHEDULE,
            {
                "preset": preset_arg,
                "start_time": "22:00",
                "stop_time": "06:00",
            },
        )

        await async_handle_set_silent_schedule(hass, call)

        client.async_patch_control_board_settings.assert_called_once()
        sent_settings = client.async_patch_control_board_settings.call_args.kwargs[
            "settings"
        ]["settings"]
        assert sent_settings["SilentHoursScheduledPreset"] == expected_preset_int

    def test_current_option_displays_silent_6_and_7(self) -> None:
        """Entity property current_option correctly reflects Silent 6 and Silent 7."""
        hp_info = _create_mock_heat_pump_info()
        coord = MagicMock()
        coord.data = ControlBoardSettings(silent_hours_preset=6)

        entity = WeheatSilentHoursPresetSelect(
            heat_pump_info=hp_info,
            coordinator=coord,
            entity_description=SELECT_ENTITIES[1],
        )
        assert entity.current_option == "Silent 6"

        coord.data = ControlBoardSettings(silent_hours_preset=7)
        assert entity.current_option == "Silent 7"


# =============================================================================
# 5. Adversarial Edge Case Discovery: Service set_quiet_mode Night Limit Auto-Adjustment
# =============================================================================


class TestServiceSetQuietModeEdgeCases:
    """Stress testing edge cases in weheat services."""

    @pytest.mark.asyncio
    async def test_service_set_quiet_mode_preserves_active_night_limit(self) -> None:
        """When daytime mode is increased via service call, active night limit must be preserved or adjusted."""
        hass = HomeAssistant()
        auth_session = _create_authorized_portal_session(hass)
        hp_info = _create_mock_heat_pump_info()

        # Baseline: Night Limit is active at Silent 3 (preset 3)
        coord = MagicMock()
        coord.data = ControlBoardSettings(
            performance_mode_preset=0,
            silent_hours_preset=3,
        )
        coord.async_request_refresh = AsyncMock()

        client = WeheatClient(session=MagicMock())
        client.async_patch_control_board_settings = AsyncMock(
            return_value="req-quiet-adj"
        )
        client.async_poll_settings_request = AsyncMock()

        data = WeheatData(
            heat_pump_info=hp_info,
            data_coordinator=coord,
            energy_coordinator=coord,
            settings_coordinator=coord,
            client=client,
            session=auth_session,
        )
        entry = ConfigEntry(
            entry_id="entry_quiet_adj",
            data={CONF_TOKEN: {CONF_ACCESS_TOKEN: "tok"}},
            runtime_data=[data],
        )
        hass.config_entries.async_add(entry)

        # Call service to set daytime mode to Silent 3 (mode 3)
        # Since active night limit is 3, night limit must be adjusted to 4 (> 3) or at least NOT reset to 0!
        call = ServiceCall(DOMAIN, SERVICE_SET_QUIET_MODE, {"mode": 3})
        await async_handle_set_quiet_mode(hass, call)

        client.async_patch_control_board_settings.assert_called_once()
        sent_settings = client.async_patch_control_board_settings.call_args.kwargs[
            "settings"
        ]["settings"]

        # If sent_settings["SilentHoursScheduledPreset"] == 0, the active schedule was corrupted to Off
        assert sent_settings["SilentHoursScheduledPreset"] > 0, (
            f"CORRUPTION DETECTED: Active night limit was reset to 0 (Off)! Outbound settings: {sent_settings}"
        )
        assert sent_settings["SilentHoursScheduledPreset"] > 3, (
            f"FIRMWARE CONSTRAINT VIOLATION: Night limit {sent_settings['SilentHoursScheduledPreset']} "
            f"is not strictly greater than daytime mode 3"
        )

    @pytest.mark.asyncio
    async def test_service_set_silent_schedule_schedule_times_dispatched(self) -> None:
        """When set_silent_schedule is called, specified start/stop times must appear in outbound payload."""
        hass = HomeAssistant()
        auth_session = _create_authorized_portal_session(hass)
        hp_info = _create_mock_heat_pump_info()

        # Baseline: Monday schedule is currently 19:00 to 07:00
        initial_schedules = {
            "Monday": DailySilentSchedule("Monday", 19, 0, 7, 0),
        }
        coord = MagicMock()
        coord.data = ControlBoardSettings(
            performance_mode_preset=0,
            silent_hours_preset=0,
            schedules=initial_schedules,
        )
        coord.async_request_refresh = AsyncMock()

        client = WeheatClient(session=MagicMock())
        client.async_patch_control_board_settings = AsyncMock(
            return_value="req-sched-times"
        )
        client.async_poll_settings_request = AsyncMock()

        data = WeheatData(
            heat_pump_info=hp_info,
            data_coordinator=coord,
            energy_coordinator=coord,
            settings_coordinator=coord,
            client=client,
            session=auth_session,
        )
        entry = ConfigEntry(
            entry_id="entry_sched_times",
            data={CONF_TOKEN: {CONF_ACCESS_TOKEN: "tok"}},
            runtime_data=[data],
        )
        hass.config_entries.async_add(entry)

        # Call service to set Monday schedule to 22:30 -> 05:45
        call = ServiceCall(
            DOMAIN,
            SERVICE_SET_SILENT_SCHEDULE,
            {
                "preset": 2,
                "start_time": "22:30",
                "stop_time": "05:45",
                "days": ["Monday"],
            },
        )
        await async_handle_set_silent_schedule(hass, call)

        client.async_patch_control_board_settings.assert_called_once()
        sent_settings = client.async_patch_control_board_settings.call_args.kwargs[
            "settings"
        ]["settings"]

        assert sent_settings["SilentHoursMondayStart"] == 22, (
            f"SilentHoursMondayStart was NOT updated to 22, got {sent_settings.get('SilentHoursMondayStart')}"
        )
        assert sent_settings["SilentMinutesMondayStart"] == 30, (
            f"SilentMinutesMondayStart was NOT updated to 30, got {sent_settings.get('SilentMinutesMondayStart')}"
        )
        assert sent_settings["SilentHoursMondayStop"] == 5, (
            f"SilentHoursMondayStop was NOT updated to 5, got {sent_settings.get('SilentHoursMondayStop')}"
        )
        assert sent_settings["SilentMinutesMondayStop"] == 45, (
            f"SilentMinutesMondayStop was NOT updated to 45, got {sent_settings.get('SilentMinutesMondayStop')}"
        )
