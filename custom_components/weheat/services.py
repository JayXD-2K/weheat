"""Custom actions / services for the Weheat heat pump integration.

Defines actions for:
- weheat.set_quiet_mode: Set performance / quiet mode preset (0..4 or Performance..Silent 4)
- weheat.set_silent_schedule: Configure silent hours scheduled preset and time window for days
- weheat.trigger_legionella_prevention: Trigger one-shot Legionella prevention heating cycle
"""

from __future__ import annotations

import datetime
import logging
from collections.abc import Sequence
from typing import Any

from aiohttp import ClientError, ClientResponseError
from homeassistant.const import CONF_ACCESS_TOKEN
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import HomeAssistantError

from .client import WeheatError
from .const import DAYS_OF_WEEK, DOMAIN
from .coordinator import WeheatConfigEntry, WeheatData
from .models import PerformanceModePreset, SilentHoursScheduledPreset

_LOGGER = logging.getLogger(__name__)

# Service Action Names
SERVICE_SET_QUIET_MODE = "set_quiet_mode"
SERVICE_SET_SILENT_SCHEDULE = "set_silent_schedule"
SERVICE_TRIGGER_LEGIONELLA = "trigger_legionella_prevention"

# Preset Mapping Dictionaries (case-insensitive & aliases)
QUIET_MODE_MAPPING: dict[str | int, int] = {
    0: PerformanceModePreset.PERFORMANCE,
    1: PerformanceModePreset.SILENT_1,
    2: PerformanceModePreset.SILENT_2,
    3: PerformanceModePreset.SILENT_3,
    4: PerformanceModePreset.SILENT_4,
    "0": PerformanceModePreset.PERFORMANCE,
    "1": PerformanceModePreset.SILENT_1,
    "2": PerformanceModePreset.SILENT_2,
    "3": PerformanceModePreset.SILENT_3,
    "4": PerformanceModePreset.SILENT_4,
    "performance": PerformanceModePreset.PERFORMANCE,
    "silent 1": PerformanceModePreset.SILENT_1,
    "silent1": PerformanceModePreset.SILENT_1,
    "silent 2": PerformanceModePreset.SILENT_2,
    "silent2": PerformanceModePreset.SILENT_2,
    "silent 3": PerformanceModePreset.SILENT_3,
    "silent3": PerformanceModePreset.SILENT_3,
    "silent 4": PerformanceModePreset.SILENT_4,
    "silent4": PerformanceModePreset.SILENT_4,
}

SILENT_PRESET_MAPPING: dict[str | int, int] = {
    0: SilentHoursScheduledPreset.OFF,
    1: SilentHoursScheduledPreset.SILENT_1,
    2: SilentHoursScheduledPreset.SILENT_2,
    3: SilentHoursScheduledPreset.SILENT_3,
    4: SilentHoursScheduledPreset.SILENT_4,
    5: SilentHoursScheduledPreset.OFF_ALT,
    6: SilentHoursScheduledPreset.SILENT_6,
    7: SilentHoursScheduledPreset.SILENT_7,
    "0": SilentHoursScheduledPreset.OFF,
    "1": SilentHoursScheduledPreset.SILENT_1,
    "2": SilentHoursScheduledPreset.SILENT_2,
    "3": SilentHoursScheduledPreset.SILENT_3,
    "4": SilentHoursScheduledPreset.SILENT_4,
    "5": SilentHoursScheduledPreset.OFF_ALT,
    "6": SilentHoursScheduledPreset.SILENT_6,
    "7": SilentHoursScheduledPreset.SILENT_7,
    "off": SilentHoursScheduledPreset.OFF,
    "disabled": SilentHoursScheduledPreset.OFF,
    "silent 1": SilentHoursScheduledPreset.SILENT_1,
    "silent1": SilentHoursScheduledPreset.SILENT_1,
    "silent 2": SilentHoursScheduledPreset.SILENT_2,
    "silent2": SilentHoursScheduledPreset.SILENT_2,
    "silent 3": SilentHoursScheduledPreset.SILENT_3,
    "silent3": SilentHoursScheduledPreset.SILENT_3,
    "silent 4": SilentHoursScheduledPreset.SILENT_4,
    "silent4": SilentHoursScheduledPreset.SILENT_4,
    "silent 5": SilentHoursScheduledPreset.OFF_ALT,
    "silent5": SilentHoursScheduledPreset.OFF_ALT,
    "silent 6": SilentHoursScheduledPreset.SILENT_6,
    "silent6": SilentHoursScheduledPreset.SILENT_6,
    "silent 7": SilentHoursScheduledPreset.SILENT_7,
    "silent7": SilentHoursScheduledPreset.SILENT_7,
}


def _resolve_target_data(hass: HomeAssistant, call: ServiceCall) -> list[WeheatData]:
    """Resolve target WeheatData instances matching the service call target."""
    all_data: list[WeheatData] = []

    # Collect all loaded WeheatData instances across config entries
    config_entries: list[WeheatConfigEntry] = getattr(
        hass.config_entries, "async_entries", lambda _: []
    )(DOMAIN)

    # Also support mock entries in test harness
    if not config_entries and hasattr(hass.config_entries, "_entries"):
        config_entries = hass.config_entries._entries.get(DOMAIN, [])
    elif not config_entries and hasattr(hass.config_entries, "entries"):
        config_entries = hass.config_entries.entries.get(DOMAIN, [])

    for entry in config_entries:
        if hasattr(entry, "runtime_data") and isinstance(entry.runtime_data, list):
            all_data.extend(entry.runtime_data)

    if not all_data:
        raise HomeAssistantError("No Weheat heat pump entries are currently loaded")

    # Extract target criteria from call data
    target_hp_id = call.data.get("heat_pump_id") or call.data.get("heatpump_id")
    target_cb_id = call.data.get("control_board_id")
    device_id = call.data.get("device_id")
    entity_id = call.data.get("entity_id")

    # Check nested target dict if present
    target_dict = call.data.get("target")
    if isinstance(target_dict, dict):
        if device_id is None:
            device_id = target_dict.get("device_id")
        if entity_id is None:
            entity_id = target_dict.get("entity_id")

    # If single target criterion specified, filter
    matched: list[WeheatData] = []

    if target_hp_id:
        target_hp_id_str = str(target_hp_id).strip()
        matched = [
            d for d in all_data if d.heat_pump_info.heatpump_id == target_hp_id_str
        ]
        if not matched:
            raise HomeAssistantError(
                f"No Weheat heat pump found with ID '{target_hp_id_str}'"
            )
        return matched

    if target_cb_id:
        target_cb_id_str = str(target_cb_id).strip()
        matched = [
            d for d in all_data if d.heat_pump_info.control_board_id == target_cb_id_str
        ]
        if not matched:
            raise HomeAssistantError(
                f"No Weheat heat pump found with control board ID '{target_cb_id_str}'"
            )
        return matched

    if device_id is not None:
        if isinstance(device_id, str):
            dev_ids = [device_id]
        elif isinstance(device_id, (list, tuple, set)):
            dev_ids = list(device_id)
        else:
            raise HomeAssistantError(
                f"Invalid device_id format: '{device_id}'. Expected string or list of strings."
            )
        if not dev_ids:
            raise HomeAssistantError("No device_id specified in target list")

        # Device registry resolution if available
        try:
            from homeassistant.helpers import device_registry as dr

            dev_reg = dr.async_get(hass)
            for d_id in dev_ids:
                device_entry = dev_reg.async_get(str(d_id))
                if device_entry:
                    for ident in device_entry.identifiers:
                        if len(ident) == 2 and ident[0] == DOMAIN:
                            hp_uuid = ident[1]
                            matched.extend(
                                [
                                    d
                                    for d in all_data
                                    if d.heat_pump_info.heatpump_id == hp_uuid
                                ]
                            )
        except Exception:  # noqa: S110
            pass

        # Fallback matching device_id directly to heatpump_id
        if not matched:
            for d_id in dev_ids:
                matched.extend(
                    [d for d in all_data if d.heat_pump_info.heatpump_id == str(d_id)]
                )

        if matched:
            return list({id(d): d for d in matched}.values())
        raise HomeAssistantError(f"No Weheat heat pump matched device ID '{device_id}'")

    if entity_id is not None:
        if isinstance(entity_id, str):
            ent_ids = [entity_id]
        elif isinstance(entity_id, (list, tuple, set)):
            ent_ids = list(entity_id)
        else:
            raise HomeAssistantError(
                f"Invalid entity_id format: '{entity_id}'. Expected string or list of strings."
            )
        if not ent_ids:
            raise HomeAssistantError("No entity_id specified in target list")

        # Entity registry resolution if available
        try:
            from homeassistant.helpers import entity_registry as er

            ent_reg = er.async_get(hass)
            for e_id in ent_ids:
                entity_entry = ent_reg.async_get(str(e_id))
                if (
                    entity_entry
                    and entity_entry.platform == DOMAIN
                    and entity_entry.unique_id
                ):
                    for d in all_data:
                        if d.heat_pump_info.heatpump_id in entity_entry.unique_id:
                            matched.append(d)
        except Exception:  # noqa: S110
            pass

        # Fallback matching entity_id substring to heatpump_id
        for e_id in ent_ids:
            e_id_str = str(e_id)
            for d in all_data:
                if d.heat_pump_info.heatpump_id in e_id_str:
                    matched.append(d)

        if matched:
            return list({id(d): d for d in matched}.values())
        raise HomeAssistantError(f"No Weheat heat pump matched entity ID '{entity_id}'")

    # If no target specified and exactly one heat pump is configured, use it
    if len(all_data) == 1:
        return all_data

    # Multiple heat pumps and no specific target
    raise HomeAssistantError(
        f"Multiple Weheat heat pumps found ({len(all_data)}). Please specify a target device or entity."
    )


async def _async_get_token(data: WeheatData) -> str:
    """Ensure OAuth token is valid and return access token string."""
    if data.session is not None:
        try:
            await data.session.async_ensure_token_valid()
        except ClientResponseError as err:
            raise HomeAssistantError(
                f"Authentication failed for heat pump {data.heat_pump_info.heatpump_id}: "
                f"OAuth token refresh rejected with HTTP {err.status} ({err.message})"
            ) from err
        except (ClientError, TimeoutError) as err:
            raise HomeAssistantError(
                f"Authentication failed for heat pump {data.heat_pump_info.heatpump_id}: "
                f"Network error refreshing OAuth token: {err}"
            ) from err
        except HomeAssistantError:
            raise
        except Exception as err:
            raise HomeAssistantError(
                f"Authentication failed for heat pump {data.heat_pump_info.heatpump_id}: {err}"
            ) from err

        token = (
            data.session.token.get(CONF_ACCESS_TOKEN)
            if isinstance(data.session.token, dict)
            else None
        )
        if token:
            return str(token)

    raise HomeAssistantError(
        f"Authentication token unavailable for heat pump {data.heat_pump_info.heatpump_id}"
    )


async def _async_get_control_board_id(data: WeheatData, token: str) -> str:
    """Resolve target control board ID for heat pump."""
    if data.heat_pump_info.control_board_id:
        return data.heat_pump_info.control_board_id

    client = data.client
    if client is None:
        raise HomeAssistantError(
            f"API client unavailable for heat pump {data.heat_pump_info.heatpump_id}"
        )

    try:
        cb_id = await client.async_get_control_board_id(
            data.heat_pump_info.heatpump_id, token=token
        )
        data.heat_pump_info.control_board_id = cb_id
        return cb_id
    except WeheatError as err:
        raise HomeAssistantError(
            f"Failed to resolve control board ID for heat pump {data.heat_pump_info.heatpump_id}: {err}"
        ) from err
    except ClientResponseError as err:
        raise HomeAssistantError(
            f"Failed to resolve control board ID for heat pump {data.heat_pump_info.heatpump_id}: "
            f"API rejected request with HTTP {err.status} ({err.message})"
        ) from err
    except (ClientError, TimeoutError) as err:
        raise HomeAssistantError(
            f"Failed to resolve control board ID for heat pump {data.heat_pump_info.heatpump_id}: "
            f"Network error: {err}"
        ) from err
    except HomeAssistantError:
        raise
    except Exception as err:
        raise HomeAssistantError(
            f"Unexpected error resolving control board ID for heat pump {data.heat_pump_info.heatpump_id}: {err}"
        ) from err


def _parse_time(time_val: Any) -> tuple[int, int]:
    """Parse time string 'HH:MM' or datetime.time into (hour, minute)."""
    if isinstance(time_val, datetime.time):
        return time_val.hour, time_val.minute

    if isinstance(time_val, str):
        parts = time_val.strip().split(":")
        if len(parts) >= 2:
            try:
                hour = int(parts[0])
                minute = int(parts[1])
                if 0 <= hour <= 23 and 0 <= minute <= 59:
                    return hour, minute
            except ValueError:
                pass

    raise HomeAssistantError(
        f"Invalid time format '{time_val}'. Expected 'HH:MM' (e.g. '22:00')."
    )


def _parse_days(days_input: Any) -> list[str]:
    """Normalize days list, supporting single day, list of days, or 'all'/'weekdays'/'weekend'."""
    if not days_input:
        # Default to all days of week
        return list(DAYS_OF_WEEK)

    raw_list: list[str] = []
    if isinstance(days_input, str):
        cleaned = days_input.strip().lower()
        if cleaned in ("all", "*"):
            return list(DAYS_OF_WEEK)
        if cleaned in ("weekdays", "weekday"):
            return ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        if cleaned in ("weekend", "weekends"):
            return ["Saturday", "Sunday"]
        if "," in days_input:
            raw_list = [d.strip() for d in days_input.split(",")]
        else:
            raw_list = [days_input.strip()]
    elif isinstance(days_input, Sequence):
        raw_list = [str(d).strip() for d in days_input]
    else:
        raise HomeAssistantError(
            f"Invalid days specification: '{days_input}'. Expected list of days."
        )

    valid_day_map = {d.lower(): d for d in DAYS_OF_WEEK}
    normalized_days: list[str] = []

    for d in raw_list:
        clean_d = d.strip().lower()
        if clean_d in valid_day_map:
            normalized_days.append(valid_day_map[clean_d])
        else:
            raise HomeAssistantError(
                f"Invalid day '{d}'. Must be one of Monday, Tuesday, Wednesday, Thursday, Friday, Saturday, Sunday."
            )

    return list(dict.fromkeys(normalized_days))  # Preserve order, deduplicate


async def async_handle_set_quiet_mode(hass: HomeAssistant, call: ServiceCall) -> None:
    """Handle weheat.set_quiet_mode service call."""
    raw_mode = call.data.get("mode")
    if raw_mode is None:
        raise HomeAssistantError("Missing required parameter: 'mode'")

    if isinstance(raw_mode, bool) or not isinstance(raw_mode, (str, int)):
        raise HomeAssistantError(
            f"Invalid quiet mode '{raw_mode}'. Expected string or integer."
        )

    # Normalize mode
    norm_key = str(raw_mode).strip().lower() if isinstance(raw_mode, str) else raw_mode
    if norm_key not in QUIET_MODE_MAPPING:
        raise HomeAssistantError(
            f"Invalid quiet mode '{raw_mode}'. Must be one of: Performance, Silent 1, Silent 2, Silent 3, Silent 4 (or 0..4)."
        )

    preset = QUIET_MODE_MAPPING[norm_key]
    targets = _resolve_target_data(hass, call)

    for data in targets:
        client = data.client
        if client is None:
            raise HomeAssistantError("Weheat API client is unavailable")

        try:
            token = await _async_get_token(data)
            cb_id = await _async_get_control_board_id(data, token)

            req_id = await client.async_set_performance_mode(
                control_board_id=cb_id,
                token=token,
                preset=preset,
            )
            # Await asynchronous completion
            await client.async_poll_settings_request(
                control_board_id=cb_id,
                request_id=req_id,
                token=token,
            )
            # Refresh coordinator telemetry
            await data.data_coordinator.async_request_refresh()
            if hasattr(data, "settings_coordinator") and data.settings_coordinator:
                await data.settings_coordinator.async_request_refresh()
        except HomeAssistantError:
            raise
        except WeheatError as err:
            raise HomeAssistantError(
                f"Failed to set quiet mode for heat pump {data.heat_pump_info.heatpump_id}: {err}"
            ) from err
        except ClientResponseError as err:
            raise HomeAssistantError(
                f"Communication error setting quiet mode for heat pump {data.heat_pump_info.heatpump_id}: "
                f"HTTP {err.status} ({err.message})"
            ) from err
        except (ClientError, TimeoutError) as err:
            raise HomeAssistantError(
                f"Communication error setting quiet mode for heat pump {data.heat_pump_info.heatpump_id}: {err}"
            ) from err
        except Exception as err:
            raise HomeAssistantError(
                f"Unexpected error setting quiet mode for heat pump {data.heat_pump_info.heatpump_id}: {err}"
            ) from err


async def async_handle_set_silent_schedule(
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Handle weheat.set_silent_schedule service call."""
    raw_preset = call.data.get("preset")
    if raw_preset is None:
        raise HomeAssistantError("Missing required parameter: 'preset'")

    if isinstance(raw_preset, bool) or not isinstance(raw_preset, (str, int)):
        raise HomeAssistantError(
            f"Invalid silent hours preset '{raw_preset}'. Expected string or integer."
        )

    norm_preset_key = (
        str(raw_preset).strip().lower() if isinstance(raw_preset, str) else raw_preset
    )
    if norm_preset_key not in SILENT_PRESET_MAPPING:
        raise HomeAssistantError(
            f"Invalid silent hours preset '{raw_preset}'. Must be one of: Off, Silent 1, Silent 2, Silent 3, Silent 4 (or 0..4)."
        )

    preset = SILENT_PRESET_MAPPING[norm_preset_key]

    raw_start = call.data.get("start_time")
    if raw_start is None:
        raise HomeAssistantError("Missing required parameter: 'start_time'")
    start_hour, start_min = _parse_time(raw_start)

    raw_stop = call.data.get("stop_time")
    if raw_stop is None:
        raise HomeAssistantError("Missing required parameter: 'stop_time'")
    stop_hour, stop_min = _parse_time(raw_stop)

    days = _parse_days(call.data.get("days"))

    # Build schedule dictionary
    schedule_dict: dict[str, int] = {}
    for day in days:
        schedule_dict[f"SilentHours{day}Start"] = start_hour
        schedule_dict[f"SilentMinutes{day}Start"] = start_min
        schedule_dict[f"SilentHours{day}Stop"] = stop_hour
        schedule_dict[f"SilentMinutes{day}Stop"] = stop_min

    targets = _resolve_target_data(hass, call)

    for data in targets:
        client = data.client
        if client is None:
            raise HomeAssistantError("Weheat API client is unavailable")

        try:
            token = await _async_get_token(data)
            cb_id = await _async_get_control_board_id(data, token)

            req_id = await client.async_set_silent_hours_schedule(
                control_board_id=cb_id,
                token=token,
                preset=preset,
                schedule=schedule_dict,
            )
            await client.async_poll_settings_request(
                control_board_id=cb_id,
                request_id=req_id,
                token=token,
            )
            await data.data_coordinator.async_request_refresh()
            if hasattr(data, "settings_coordinator") and data.settings_coordinator:
                await data.settings_coordinator.async_request_refresh()
        except HomeAssistantError:
            raise
        except WeheatError as err:
            raise HomeAssistantError(
                f"Failed to set silent schedule for heat pump {data.heat_pump_info.heatpump_id}: {err}"
            ) from err
        except ClientResponseError as err:
            raise HomeAssistantError(
                f"Communication error setting silent schedule for heat pump {data.heat_pump_info.heatpump_id}: "
                f"HTTP {err.status} ({err.message})"
            ) from err
        except (ClientError, TimeoutError) as err:
            raise HomeAssistantError(
                f"Communication error setting silent schedule for heat pump {data.heat_pump_info.heatpump_id}: {err}"
            ) from err
        except Exception as err:
            raise HomeAssistantError(
                f"Unexpected error setting silent schedule for heat pump {data.heat_pump_info.heatpump_id}: {err}"
            ) from err


async def async_handle_trigger_legionella(
    hass: HomeAssistant, call: ServiceCall
) -> None:
    """Handle weheat.trigger_legionella_prevention service call."""
    targets = _resolve_target_data(hass, call)

    for data in targets:
        client = data.client
        if client is None:
            raise HomeAssistantError("Weheat API client is unavailable")

        try:
            token = await _async_get_token(data)
            cb_id = await _async_get_control_board_id(data, token)

            success = await client.async_trigger_legionella_prevention(
                control_board_id=cb_id,
                token=token,
            )
            if not success:
                raise HomeAssistantError(
                    f"Legionella prevention trigger was not acknowledged by heat pump {data.heat_pump_info.heatpump_id}"
                )
            await data.data_coordinator.async_request_refresh()
        except HomeAssistantError:
            raise
        except WeheatError as err:
            raise HomeAssistantError(
                f"Failed to trigger Legionella prevention for heat pump {data.heat_pump_info.heatpump_id}: {err}"
            ) from err
        except ClientResponseError as err:
            raise HomeAssistantError(
                f"Communication error triggering Legionella prevention for heat pump {data.heat_pump_info.heatpump_id}: "
                f"HTTP {err.status} ({err.message})"
            ) from err
        except (ClientError, TimeoutError) as err:
            raise HomeAssistantError(
                f"Communication error triggering Legionella prevention for heat pump {data.heat_pump_info.heatpump_id}: {err}"
            ) from err
        except Exception as err:
            raise HomeAssistantError(
                f"Unexpected error triggering Legionella prevention for heat pump {data.heat_pump_info.heatpump_id}: {err}"
            ) from err


async def async_setup_services(hass: HomeAssistant) -> None:
    """Register all Weheat integration custom actions/services."""
    if not hasattr(hass, "services"):
        return

    # Check if already registered
    if hasattr(hass.services, "has_service") and hass.services.has_service(
        DOMAIN, SERVICE_SET_QUIET_MODE
    ):
        return

    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_QUIET_MODE,
        lambda call: async_handle_set_quiet_mode(hass, call),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_SET_SILENT_SCHEDULE,
        lambda call: async_handle_set_silent_schedule(hass, call),
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_TRIGGER_LEGIONELLA,
        lambda call: async_handle_trigger_legionella(hass, call),
    )
    _LOGGER.info(
        "Registered Weheat custom actions: %s, %s, %s",
        SERVICE_SET_QUIET_MODE,
        SERVICE_SET_SILENT_SCHEDULE,
        SERVICE_TRIGGER_LEGIONELLA,
    )


async def async_unload_services(hass: HomeAssistant) -> None:
    """Unregister Weheat integration custom actions/services."""
    if not hasattr(hass, "services"):
        return

    for service in (
        SERVICE_SET_QUIET_MODE,
        SERVICE_SET_SILENT_SCHEDULE,
        SERVICE_TRIGGER_LEGIONELLA,
    ):
        if hasattr(hass.services, "has_service") and hass.services.has_service(
            DOMAIN, service
        ):
            hass.services.async_remove(DOMAIN, service)
    _LOGGER.info("Unloaded Weheat custom actions")
