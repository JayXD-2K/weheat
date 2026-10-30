"""Diagnostics support for Weheat custom integration."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from .coordinator import WeheatConfigEntry

# Sensitive keys to redact from diagnostic dumps
TO_REDACT: set[str] = {
    "heat_pump_id",
    "heatpump_id",
    "heatPumpId",
    "uuid",
    "sn",
    "serial_number",
    "control_board_id",
    "controlBoardId",
    "installation_id",
    "installationId",
    "access_token",
    "refresh_token",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: WeheatConfigEntry
) -> dict[str, Any]:
    """Return redacted diagnostics dump for a config entry."""
    return {
        "heat_pumps": [
            {
                "info": async_redact_data(
                    asdict(weheatdata.heat_pump_info)
                    if hasattr(weheatdata.heat_pump_info, "__dataclass_fields__")
                    else vars(weheatdata.heat_pump_info),
                    TO_REDACT,
                ),
                "logs": async_redact_data(
                    weheatdata.data_coordinator.data.raw
                    if hasattr(weheatdata.data_coordinator.data, "raw")
                    else {},
                    TO_REDACT,
                ),
                "energy": async_redact_data(
                    weheatdata.energy_coordinator.data.raw
                    if hasattr(weheatdata.energy_coordinator.data, "raw")
                    else {},
                    TO_REDACT,
                ),
            }
            for weheatdata in entry.runtime_data
        ]
    }
