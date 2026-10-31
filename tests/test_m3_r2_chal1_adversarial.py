"""Adversarial Empirical Stress Test Suite for Milestone 3 Iteration 2.

Authored by Challenger 1 (m3_r2_chal1_gen2) to stress-test commit 5e46a610e541e48f8687315f746b61897ca012a2:
1. Scaling to 3+ heat pumps (and up to 10 pumps) in multi-pump topologies.
2. Root-level vs per-pump controlBoardId conflict resolution and fallback.
3. Multi-installation cache isolation (preventing cross-installation contamination).
4. Concurrent async topology discovery and target ID resolution bursts.
5. Malformed, non-dict, None, and UUID data structures in topology discovery.
6. Exhaustive schedule payload bundling and property alias resilience.
"""

from __future__ import annotations

import asyncio
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.weheat.client import WeheatApiError, WeheatClient
from custom_components.weheat.models import ControlBoardSettings


class TestMultiPumpTopologyScaling:
    """Stress tests for multi-pump installations with 3+ heat pumps."""

    @pytest.mark.asyncio
    async def test_three_plus_heatpumps_single_installation(self) -> None:
        """Verify an installation with 5 heat pumps maps all 5 to distinct control boards."""
        client = WeheatClient(session=MagicMock())
        pumps = [
            {"id": f"hp-cascade-{i}", "controlBoardId": f"cb-cascade-{i}"}
            for i in range(1, 6)
        ]
        installation_data = [
            {
                "id": "inst-cascade-main",
                "heatPumps": pumps,
            }
        ]
        client._request = AsyncMock(return_value=installation_data)

        topology = await client.async_get_installation_topology(token="test-token")
        assert len(topology) == 1

        # Verify each of the 5 pumps maps to its own control board
        for i in range(1, 6):
            hp_id = f"hp-cascade-{i}"
            expected_cb = f"cb-cascade-{i}"
            resolved_cb = await client.async_get_control_board_id(hp_id)
            assert resolved_cb == expected_cb
            assert client._control_board_cache[hp_id] == expected_cb
            assert client._installation_cache[hp_id] == "inst-cascade-main"
            assert client._installation_cache[expected_cb] == "inst-cascade-main"

        # Verify the shared installation dict was not mutated with a single pump's controlBoardId
        # (in-place mutation should only happen when len(hps) == 1)
        assert "controlBoardId" not in topology[0]
        assert "heatPumpId" not in topology[0]

    @pytest.mark.asyncio
    async def test_ten_heatpumps_enterprise_cascade(self) -> None:
        """Verify large-scale cascade of 10 heat pumps has zero cache collisions."""
        client = WeheatClient(session=MagicMock())
        pumps = [
            {"id": f"hp-ent-{i:02d}", "controlBoardId": f"cb-ent-{i:02d}"}
            for i in range(1, 11)
        ]
        client._request = AsyncMock(
            return_value=[{"id": "inst-ent-site", "heatPumps": pumps}]
        )

        await client.async_get_installation_topology()

        # Check all 10 pairs
        for i in range(1, 11):
            hp = f"hp-ent-{i:02d}"
            expected_cb = f"cb-ent-{i:02d}"
            assert client._control_board_cache[hp] == expected_cb
            assert client._installation_cache[hp] == "inst-ent-site"
            assert client._installation_cache[expected_cb] == "inst-ent-site"

        # Installation ID itself maps to the first encountered pump's CB stably
        assert client._control_board_cache["inst-ent-site"] == "cb-ent-01"

    @pytest.mark.asyncio
    async def test_heatpumps_with_conflicting_root_control_board_id(self) -> None:
        """Verify that per-pump controlBoardId overrides root-level controlBoardId."""
        client = WeheatClient(session=MagicMock())
        payload = [
            {
                "id": "inst-conflict",
                "controlBoardId": "cb-root-level-rogue",
                "heatPumps": [
                    {
                        "id": "hp-pump-alpha",
                        "controlBoardId": "cb-pump-alpha-actual",
                    },
                    {
                        "id": "hp-pump-beta",
                        "controlBoardId": "cb-pump-beta-actual",
                    },
                ],
            }
        ]
        client._request = AsyncMock(return_value=payload)

        await client.async_get_installation_topology()

        # Both pumps must resolve to their own actual CB, NOT the rogue root CB
        alpha_cb = await client.async_get_control_board_id("hp-pump-alpha")
        beta_cb = await client.async_get_control_board_id("hp-pump-beta")

        assert alpha_cb == "cb-pump-alpha-actual"
        assert beta_cb == "cb-pump-beta-actual"
        assert alpha_cb != "cb-root-level-rogue"
        assert beta_cb != "cb-root-level-rogue"

    @pytest.mark.asyncio
    async def test_heatpump_without_control_board_id_inherits_root_id(self) -> None:
        """Verify that a pump lacking controlBoardId falls back to root-level controlBoardId."""
        client = WeheatClient(session=MagicMock())
        payload = [
            {
                "id": "inst-fallback",
                "controlBoardId": "cb-root-shared",
                "heatPumps": [
                    {
                        "id": "hp-explicit",
                        "controlBoardId": "cb-explicit-custom",
                    },
                    {
                        "id": "hp-implicit",
                        # Missing controlBoardId
                    },
                ],
            }
        ]
        client._request = AsyncMock(return_value=payload)

        await client.async_get_installation_topology()

        assert (
            await client.async_get_control_board_id("hp-explicit")
            == "cb-explicit-custom"
        )
        assert (
            await client.async_get_control_board_id("hp-implicit") == "cb-root-shared"
        )

    @pytest.mark.asyncio
    async def test_multi_pump_missing_control_board_ids_graceful_skipping(self) -> None:
        """Verify graceful handling when some pumps completely lack control board IDs."""
        client = WeheatClient(session=MagicMock())
        payload = [
            {
                "id": "inst-partial",
                "heatPumps": [
                    {"id": "hp-valid", "controlBoardId": "cb-valid"},
                    {"id": "hp-orphan"},  # No controlBoardId and no root CB
                    {"controlBoardId": "cb-nameless"},  # No hp_id
                ],
            }
        ]
        client._request = AsyncMock(return_value=payload)

        await client.async_get_installation_topology()

        assert await client.async_get_control_board_id("hp-valid") == "cb-valid"
        # Orphan pump has no control board ID; querying it should raise WeheatApiError
        with pytest.raises(
            WeheatApiError, match="could not be resolved for target 'hp-orphan'"
        ):
            await client.async_get_control_board_id("hp-orphan")


class TestMultiInstallationCacheIsolation:
    """Stress tests verifying cache isolation across multiple distinct installations."""

    @pytest.mark.asyncio
    async def test_cross_installation_isolation(self) -> None:
        """Verify 4 distinct installations in one account do not contaminate each other."""
        client = WeheatClient(session=MagicMock())
        multi_site_payload = [
            # Installation A: Single pump
            {
                "id": "inst-A",
                "heatPumps": [{"id": "hp-A1", "controlBoardId": "cb-A1"}],
            },
            # Installation B: Dual pump
            {
                "id": "inst-B",
                "heatPumps": [
                    {"id": "hp-B1", "controlBoardId": "cb-B1"},
                    {"id": "hp-B2", "controlBoardId": "cb-B2"},
                ],
            },
            # Installation C: Singular heatPump object
            {
                "id": "inst-C",
                "heatPump": {"id": "hp-C1", "controlBoardId": "cb-C1"},
            },
            # Installation D: Flat top-level structure
            {
                "id": "inst-D",
                "heatPumpId": "hp-D1",
                "controlBoardId": "cb-D1",
            },
        ]
        client._request = AsyncMock(return_value=multi_site_payload)

        topology = await client.async_get_installation_topology()
        assert len(topology) == 4

        # Verify all heat pumps resolve to their exact CB
        assert await client.async_get_control_board_id("hp-A1") == "cb-A1"
        assert await client.async_get_control_board_id("hp-B1") == "cb-B1"
        assert await client.async_get_control_board_id("hp-B2") == "cb-B2"
        assert await client.async_get_control_board_id("hp-C1") == "cb-C1"
        assert await client.async_get_control_board_id("hp-D1") == "cb-D1"

        # Verify installation caches are strictly isolated
        assert client._installation_cache["hp-A1"] == "inst-A"
        assert client._installation_cache["cb-A1"] == "inst-A"
        assert client._installation_cache["hp-B1"] == "inst-B"
        assert client._installation_cache["hp-B2"] == "inst-B"
        assert client._installation_cache["cb-B1"] == "inst-B"
        assert client._installation_cache["cb-B2"] == "inst-B"
        assert client._installation_cache["hp-C1"] == "inst-C"
        assert client._installation_cache["cb-C1"] == "inst-C"
        assert client._installation_cache["hp-D1"] == "inst-D"
        assert client._installation_cache["cb-D1"] == "inst-D"

    @pytest.mark.asyncio
    async def test_async_get_control_board_settings_cross_installation_routing(
        self,
    ) -> None:
        """Verify settings requests route to the correct installation URL for each pump."""
        client = WeheatClient(session=MagicMock())
        client._installation_cache = {
            "hp-site1-pump1": "inst-site1",
            "hp-site2-pump2": "inst-site2",
        }
        client._request = AsyncMock(return_value={"performanceModePreset": 1})

        # Request settings for site 1 pump
        await client.async_get_control_board_settings("hp-site1-pump1")
        assert (
            "installations/inst-site1/settings"
            in client._request.call_args_list[0][0][1]
        )

        # Request settings for site 2 pump
        await client.async_get_control_board_settings("hp-site2-pump2")
        assert (
            "installations/inst-site2/settings"
            in client._request.call_args_list[1][0][1]
        )

    @pytest.mark.asyncio
    async def test_patch_settings_and_variables_cross_installation_isolation(
        self,
    ) -> None:
        """Verify mutations for pumps on different installations go to distinct CB endpoints."""
        client = WeheatClient(session=MagicMock())
        client._control_board_cache = {
            "hp-site1-pump1": "cb-site1-actual",
            "hp-site2-pump2": "cb-site2-actual",
        }
        client._request = AsyncMock(return_value={"settingsRequestId": "req-1"})

        # Mutation for site 1
        await client.async_patch_control_board_settings(
            control_board_id="hp-site1-pump1",
            settings={"PerformanceModePreset": 2},
        )
        assert (
            "control-boards/cb-site1-actual/settings"
            in client._request.call_args_list[0][0][1]
        )

        # Mutation for site 2
        await client.async_patch_control_board_variables(
            control_board_id="hp-site2-pump2",
            variables={"RequestLegionellaPrevention": 1},
        )
        assert (
            "control-boards/cb-site2-actual/variables"
            in client._request.call_args_list[1][0][1]
        )


class TestConcurrentTopologyResolutions:
    """Stress tests for concurrent async topology discoveries and target ID resolutions."""

    @pytest.mark.asyncio
    async def test_concurrent_async_get_control_board_id_burst(self) -> None:
        """Verify 50 concurrent calls resolving cached and non-cached IDs execute deterministically."""
        client = WeheatClient(session=MagicMock())
        client._control_board_cache = {
            f"hp-pre-{i}": f"cb-pre-{i}" for i in range(1, 26)
        }
        # Pre-seed request mock for non-cached target
        client._request = AsyncMock(
            return_value=[
                {
                    "id": "inst-dyn",
                    "heatPumps": [{"id": "hp-dyn-1", "controlBoardId": "cb-dyn-1"}],
                }
            ]
        )

        tasks = []
        # 25 cached calls
        for i in range(1, 26):
            tasks.append(client.async_get_control_board_id(f"hp-pre-{i}"))

        # 25 calls to the dynamic uncached ID
        for _ in range(25):
            tasks.append(client.async_get_control_board_id("hp-dyn-1"))

        results = await asyncio.gather(*tasks)

        assert len(results) == 50
        for i in range(25):
            assert results[i] == f"cb-pre-{i + 1}"
        for i in range(25, 50):
            assert results[i] == "cb-dyn-1"

    @pytest.mark.asyncio
    async def test_concurrent_async_get_installation_topology_burst(self) -> None:
        """Verify 20 concurrent calls calling async_get_installation_topology() simultaneously."""
        client = WeheatClient(session=MagicMock())
        data = [
            {
                "id": "inst-burst",
                "heatPumps": [
                    {"id": f"hp-burst-{i}", "controlBoardId": f"cb-burst-{i}"}
                    for i in range(1, 4)
                ],
            }
        ]
        client._request = AsyncMock(return_value=data)

        tasks = [client.async_get_installation_topology() for _ in range(20)]
        results = await asyncio.gather(*tasks)

        assert len(results) == 20
        for res in results:
            assert len(res) == 1
        assert client._control_board_cache["hp-burst-1"] == "cb-burst-1"
        assert client._control_board_cache["hp-burst-2"] == "cb-burst-2"
        assert client._control_board_cache["hp-burst-3"] == "cb-burst-3"


class TestEdgeCasesAndMalformedPayloads:
    """Stress tests against corrupted or non-standard payloads."""

    @pytest.mark.asyncio
    async def test_topology_with_malformed_and_missing_keys(self) -> None:
        """Verify resilience when topology items contain missing, None, or unexpected structures."""
        client = WeheatClient(session=MagicMock())
        client._request = AsyncMock(
            return_value=[
                {},
                {
                    "id": "inst-valid",
                    "heatPump": {"id": "hp-v", "controlBoardId": "cb-v"},
                },
                {"id": "inst-bad-hps", "heatPumps": None},
                {"id": "inst-bad-hps-2", "heatPumps": [None, 999, "bad", {}]},
                {"id": "inst-bad-hp", "heatPump": "not-a-dict"},
                {"id": None, "heatPumps": []},
            ]
        )

        topology = await client.async_get_installation_topology()
        assert len(topology) == 6
        assert client._control_board_cache["hp-v"] == "cb-v"

    @pytest.mark.asyncio
    async def test_topology_with_uuid_objects(self) -> None:
        """Verify client converts Python uuid.UUID objects to clean strings."""
        client = WeheatClient(session=MagicMock())
        inst_u = uuid.uuid4()
        hp_u = uuid.uuid4()
        cb_u = uuid.uuid4()

        client._request = AsyncMock(
            return_value=[
                {
                    "id": inst_u,
                    "heatPump": {"id": hp_u, "controlBoardId": cb_u},
                }
            ]
        )

        await client.async_get_installation_topology()

        assert client._control_board_cache[str(hp_u)] == str(cb_u)
        assert client._installation_cache[str(hp_u)] == str(inst_u)
        assert await client.async_get_control_board_id(str(hp_u)) == str(cb_u)

    @pytest.mark.asyncio
    async def test_target_id_whitespace_handling(self) -> None:
        """Verify target ID with leading and trailing whitespace resolves properly."""
        client = WeheatClient(session=MagicMock())
        client._control_board_cache["hp-spaced"] = "cb-spaced"

        resolved = await client.async_get_control_board_id("  \t hp-spaced \n ")
        assert resolved == "cb-spaced"

    @pytest.mark.asyncio
    async def test_empty_data_structures(self) -> None:
        """Verify empty data payloads return empty topology without exceptions."""
        client = WeheatClient(session=MagicMock())
        for empty_val in [None, {}, {"data": None}, {"data": []}, []]:
            client._request = AsyncMock(return_value=empty_val)
            res = await client.async_get_installation_topology()
            assert isinstance(res, list)


class TestScheduleBundlingAdversarialEdgeCases:
    """Stress tests for build_bundled_silent_schedule_payload and property aliases."""

    def test_all_preset_combinations_in_bundling(self) -> None:
        """Verify building bundled silent schedule payload across all preset ranges."""
        settings = ControlBoardSettings()
        # Test performance presets 0..4 against silent presets 0, 1, 2, 3, 4, 5, 6, 7
        for perf in range(5):
            for silent in [0, 1, 2, 3, 4, 5, 6, 7]:
                payload = settings.build_bundled_silent_schedule_payload(
                    performance_preset=perf,
                    silent_preset=silent,
                )
                inner = payload["settings"]
                assert inner["PerformanceModePreset"] == perf
                assert inner["SilentHoursScheduledPreset"] == silent
                assert payload["updateImmediately"] is False
                assert len(inner) == 30

    def test_bundling_with_conflicting_day_updates_and_kwargs(self) -> None:
        """Verify kwargs and day_updates are correctly normalized into PascalCase."""
        settings = ControlBoardSettings()
        # Pass snake_case and camelCase in day_updates and kwargs
        day_updates = {
            "silent_hours_monday_start": 21,
            "silentMinutesMondayStart": 45,
        }
        payload = settings.build_bundled_silent_schedule_payload(
            day_updates=day_updates,
            silent_hours_tuesday_start=22,
            SilentMinutesTuesdayStart=15,
        )
        inner = payload["settings"]
        assert inner["SilentHoursMondayStart"] == 21
        assert inner["SilentMinutesMondayStart"] == 45
        assert inner["SilentHoursTuesdayStart"] == 22
        assert inner["SilentMinutesTuesdayStart"] == 15

    def test_property_alias_bidirectional_sync(self) -> None:
        """Verify silent_hours_scheduled_preset property alias syncs with silent_hours_preset."""
        settings = ControlBoardSettings()
        settings.silent_hours_preset = 3
        assert settings.silent_hours_scheduled_preset == 3

        settings.silent_hours_scheduled_preset = 6
        assert settings.silent_hours_preset == 6
        assert settings.silent_hours_scheduled_preset == 6
