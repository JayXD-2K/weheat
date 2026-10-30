"""Unit tests for Weheat diagnostics support."""

from __future__ import annotations

import pytest

from custom_components.weheat.diagnostics import async_get_config_entry_diagnostics
from custom_components.weheat.models import (
    HeatPumpEnergy,
    HeatPumpInfo,
    HeatPumpLog,
)
from tests.conftest import load_fixture


@pytest.fixture
def mock_hp_info() -> HeatPumpInfo:
    data = load_fixture("heat_pumps.json")["data"][0]
    return HeatPumpInfo.from_api_dict(data)


@pytest.fixture
def mock_log() -> HeatPumpLog:
    data = load_fixture("latest_logs.json")
    return HeatPumpLog.from_api_dict(data)


@pytest.fixture
def mock_energy() -> HeatPumpEnergy:
    data = load_fixture("energy_total.json")
    return HeatPumpEnergy.from_api_dict(data)


async def test_diagnostics_redacts_sensitive_fields(
    mock_hp_info: HeatPumpInfo, mock_log: HeatPumpLog, mock_energy: HeatPumpEnergy
) -> None:
    """Verify that diagnostics dump redacts UUIDs and serial numbers."""

    class DummyCoord:
        def __init__(self, data: object) -> None:
            self.data = data

    class DummyData:
        heat_pump_info = mock_hp_info
        data_coordinator = DummyCoord(mock_log)
        energy_coordinator = DummyCoord(mock_energy)

    class DummyEntry:
        runtime_data = [DummyData()]

    diag = await async_get_config_entry_diagnostics(None, DummyEntry())  # type: ignore[arg-type]

    assert "heat_pumps" in diag
    assert len(diag["heat_pumps"]) == 1

    hp_diag = diag["heat_pumps"][0]
    assert "info" in hp_diag
    assert "logs" in hp_diag
    assert "energy" in hp_diag

    # Verify info redactions
    info_dict = hp_diag["info"]
    assert info_dict["heatpump_id"] == "**REDACTED**"
    assert info_dict["serial_number"] == "**REDACTED**"
    assert info_dict["control_board_id"] == "**REDACTED**"

    # Verify logs redactions
    logs_dict = hp_diag["logs"]
    assert (
        logs_dict.get("heatPumpId") == "**REDACTED**"
        or logs_dict.get("heat_pump_id") == "**REDACTED**"
    )

    # Verify energy redactions
    energy_dict = hp_diag["energy"]
    assert (
        energy_dict.get("heatPumpId") == "**REDACTED**"
        or energy_dict.get("heat_pump_id") == "**REDACTED**"
    )

    # Verify non-sensitive telemetry is preserved
    assert logs_dict.get("rpm") == 2850
    assert energy_dict.get("totalEInHeating") == 1245.8
