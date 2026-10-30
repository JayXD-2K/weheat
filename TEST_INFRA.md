# Test Infrastructure Specification: Weheat Custom Home Assistant Integration

## Test Philosophy
1. **Opaque-Box Verification**: The test suite evaluates external contract behaviors, state transitions, HTTP wire payloads, and Home Assistant registry interactions rather than internal implementation details.
2. **Requirement-Driven**: Every test case directly derives its expected values from authoritative project specifications (`ORIGINAL_REQUEST.md`, `PROJECT.md`, live Weheat portal traces, and Home Assistant Core integration standards).
3. **100% Deterministic & Offline**: Zero network calls to live Weheat cloud endpoints or live hardware. All HTTP requests are intercepted via mock sessions, `aioresponses`, and JSON fixtures in `tests/fixtures/`.
4. **Anonymized Fixtures**: Strict adherence to security standards—all device UUIDs, installation IDs, and serial numbers in test fixtures use RFC 4122 anonymized test UUIDs (`00000000-0000-0000-0000-000000000001`, `cb-000...`). No real user credentials exist anywhere in the repository.
5. **Progressive 4-Tier Test Architecture**: Test suites span unit, edge, integration, and full operational user workflow dimensions.

---

## 4-Tier Test Architecture

| Tier | Tier Name | Purpose | Scope | Test Count |
|:---:|:---|:---|:---|:---:|
| **Tier 1** | Feature Coverage | Verifies happy-path functionality for all integration features, entities, coordinators, client methods, and configuration flows. | Base functional verification across all 10 core components and platforms | 107 |
| **Tier 2** | Boundary & Corner Cases | Stresses input limits, extreme physical values, nulls/empties, malformed payloads, and API HTTP error status handling (400, 401, 403, 404, 500, 502, 503, 504). | Defensive validation of models, decoders, API client, and entity state mappings | 372 |
| **Tier 3** | Cross-Feature Combinations | Tests concurrency, multi-device disambiguation, token refresh races during polling/mutation, entity lifecycle teardowns, and debouncing. | Inter-module coordination, async races, and multi-pump setups | 38 |
| **Tier 4** | Real-World Scenarios | Exercises end-to-end multi-step user workflows that mirror real Home Assistant daily operations (7-day schedules, night setbacks, thermal disinfection cycles, failover). | Full operational workflows in `tests/test_e2e_scenarios.py` | 8 |
| **Total** | **All Tiers** | **Comprehensive Full Integration Suite** | **Entire Codebase** | **525** |

---

## Feature Inventory & Test Mapping

### Feature 1: Manifest & Tooling Config (Tier 1)
- **Component**: `manifest.json`, `pyproject.toml`, `.mise.toml`
- **Description**: Zero unvetted dependencies (`"requirements": []`), correct domain (`weheat`), single config entry iot_class (`cloud_polling`).
- **Tests**: `tests/test_manifest.py::test_manifest_requirements`

### Feature 2: Offline Mock Fixture Catalog (Tier 1)
- **Component**: `tests/fixtures/*.json`, `tests/conftest.py`
- **Description**: 10 offline JSON fixtures for OAuth token renewal, user discovery, multi-pump topology, telemetry logs, cumulative energy, and control board settings.
- **Tests**: `tests/test_smoke_fixtures.py` (18 tests)

### Feature 3: Direct In-Tree aiohttp API Client (Tiers 1, 2, 3)
- **Component**: `custom_components/weheat/client.py`
- **Description**: Robust async client for `/third_party/api/v1` and `/api/v1` endpoints with Bearer auth, custom exceptions, settings request dispatch and polling.
- **Tests**:
  - Tier 1: `tests/test_client.py` (happy paths for auth, user_id, pumps, topology, settings, mutations)
  - Tier 2: `tests/test_client_adversarial.py` (48 tests), `tests/test_client.py` (error paths), `tests/test_client_concurrency_bounds.py` (boundary parameters)
  - Tier 3: `tests/test_client_concurrency_bounds.py` (thread-safety, burst requests, cache consistency)

### Feature 4: Models & Bitmask Decoders (Tiers 1, 2)
- **Component**: `custom_components/weheat/models.py`
- **Description**: Dataclasses for `HeatPumpInfo`, `HeatPumpLog`, `HeatPumpEnergy`, `ControlBoardSettings`, and bitmask decoders for cooling start conditions.
- **Tests**:
  - Tier 1: `tests/test_smoke_fixtures.py`, `tests/test_sensor.py`, `tests/test_binary_sensor.py`
  - Tier 2: `tests/test_m3_adversarial.py` (null traps, NaN/infinity floats, negative energies, corrupt timestamps)

### Feature 5: OAuth2 Config Flow (Tiers 1, 2, 3)
- **Component**: `custom_components/weheat/config_flow.py`
- **Description**: PKCE authorization flow, unique user ID assignment, duplicate entry rejection, and reauthentication handling.
- **Tests**:
  - Tier 1: `tests/test_config_flow.py::test_flow_handler_domain_and_scopes`, `test_oauth_create_entry_success`
  - Tier 2: `tests/test_config_flow.py` (abort cases, missing tokens, invalid credentials), `tests/test_lifecycle_coordinator_adversarial.py`
  - Tier 3: `tests/test_config_flow.py::test_reauth_flow_success`, `tests/test_lifecycle_coordinator_adversarial.py::test_config_flow_reauth_*`

### Feature 6: Data Update Coordinators (Tiers 1, 2, 3)
- **Component**: `custom_components/weheat/coordinator.py`
- **Description**: 60s telemetry polling, 300s energy polling, 300s settings polling, automatic token refresh, dynamic error backoff, fast post-mutation refresh.
- **Tests**:
  - Tier 1: `tests/test_coordinator.py` (intervals, success paths for telemetry, energy, settings)
  - Tier 2: `tests/test_coordinator.py` (error mappings, missing control boards), `tests/test_lifecycle_coordinator_adversarial.py` (server errors, timeouts)
  - Tier 3: `tests/test_coordinator.py::test_coordinator_token_refresh_resilience`, `test_coordinator_concurrent_refresh_debounced`, `test_coordinator_fast_polling_after_write`

### Feature 7: Base Entity Architecture (Tier 1)
- **Component**: `custom_components/weheat/entity.py`
- **Description**: Device registry linking via `{(DOMAIN, heatpump_id)}`, deterministic unique IDs (`{heatpump_id}_{key}`), and availability binding.
- **Tests**: `tests/test_entity.py` (2 tests), `tests/test_m3_adversarial.py` (uniqueness audits)

### Feature 8: Telemetry Sensors (39 Entities) (Tiers 1, 2)
- **Component**: `custom_components/weheat/sensor.py`
- **Description**: 15 operational sensors, 7 cooling diagnostics, 4 DHW telemetry sensors, 3 DHW energy sensors, 10 energy cumulative registers. Gated on `has_dhw`.
- **Tests**:
  - Tier 1: `tests/test_sensor.py` (catalog counts, keys uniqueness, state mapping, unit classes)
  - Tier 2: `tests/test_sensor.py` (COP boundary at 0W, DHW target temp 0 mapping, latched cooling reasons), `tests/test_m3_adversarial.py` (extreme temperatures, divide-by-zero protection)

### Feature 9: Binary Sensors (16 Entities) (Tiers 1, 2)
- **Component**: `custom_components/weheat/binary_sensor.py`
- **Description**: 5 indoor unit actuator states (water pump, auxiliary pump, DHW valve, gas boiler, immersion heater) + 11 cooling condition bitmask sensors.
- **Tests**:
  - Tier 1: `tests/test_binary_sensor.py` (catalog counts, running device classes, bitmask decoding)
  - Tier 2: `tests/test_binary_sensor.py::test_cooling_condition_bitmask_decoding`, `tests/test_m3_adversarial.py` (cooling condition gates)

### Feature 10: Quiet Level & Silent Hours Select Entities (Tiers 1, 2)
- **Component**: `custom_components/weheat/select.py`
- **Description**: `WeheatPerformanceModeSelect` (0=Performance, 1..4=Silent1..4) and `WeheatSilentHoursPresetSelect` (0=Off, 1..4=Silent1..4).
- **Tests**:
  - Tier 1: `tests/test_quiet_mode.py` (options catalogs, current option reads, `async_select_option` dispatch)
  - Tier 2: `tests/test_m4_quiet_time_adversarial.py` (invalid options, out-of-range strings, API failure recovery)

### Feature 11: Control Action Switches (3 Entities) (Tiers 1, 2)
- **Component**: `custom_components/weheat/switch.py`
- **Description**: `WeheatSilentHoursScheduleSwitch` (schedule enable/disable), `WeheatLegionellaPreventionSwitch` (request/skip disinfection), `WeheatDhwBoostSwitch` (timed DHW boost).
- **Tests**:
  - Tier 1: `tests/test_quiet_mode.py` (turn_on, turn_off, is_on property evaluation)
  - Tier 2: `tests/test_quiet_mode.py` (unavailable when missing control board), `tests/test_m4_quiet_time_adversarial.py` (DHw gating, network drop rollback)

### Feature 12: Silent Hours Time Entities (14 Entities) (Tiers 1, 2)
- **Component**: `custom_components/weheat/time.py`
- **Description**: 14 `WeheatTimeEntity` entities for day-specific quiet hour start and stop times (Monday through Sunday).
- **Tests**:
  - Tier 1: `tests/test_time.py` (14-entity catalog, native_value `time(HH, MM)` parsing, `async_set_value` dispatch and optimistic cache update)
  - Tier 2: `tests/test_time.py` (API failure, invalid types), `tests/test_m4_quiet_time_adversarial.py` (optimistic rollback on network/polling errors)

### Feature 13: Custom Services & Multi-Device Target Resolution (Tiers 1, 2, 3)
- **Component**: `custom_components/weheat/services.py`, `services.yaml`
- **Description**: `weheat.set_quiet_mode`, `weheat.set_silent_schedule`, `weheat.trigger_legionella` supporting day shortcuts ('all', 'weekdays', 'weekend'), flexible time inputs, and target resolution across multi-pump installations.
- **Tests**:
  - Tier 1: `tests/test_services.py` (valid preset strings/ints, day aliases, legionella trigger)
  - Tier 2: `tests/test_services.py` (invalid modes, malformed time strings, missing targets), `tests/test_m4_adversarial.py`
  - Tier 3: `tests/test_services.py::test_target_resolution_multi_pumps_disambiguation`, `test_set_quiet_mode_oauth_token_refresh_client_response_error`

### Feature 14: Diagnostics & Privacy Redaction (Tier 1)
- **Component**: `custom_components/weheat/diagnostics.py`
- **Description**: Export integration diagnostic bundle while strictly redacting sensitive tokens, serial numbers, control board IDs, and installation IDs.
- **Tests**: `tests/test_diagnostics.py::test_diagnostics_redacts_sensitive_fields`, `tests/test_lifecycle_coordinator_adversarial.py`

---

## Tier 4 Real-World Application Scenarios

The suite in `tests/test_e2e_scenarios.py` implements 8 realistic, multi-step user workflows:

1. **`test_scenario_complete_7day_quiet_hours_schedule_cycle`**:
   Full 7-day schedule setup (Weekdays 22:00-06:30, Weekends 23:30-08:00) with preset Silent 2. Verifies 28-key schedule payload format, async request resolution, coordinator refresh, 14 Time entities state reconciliation, and schedule switch synchronization.
2. **`test_scenario_night_setback_and_cold_snap_override`**:
   Diurnal cycle: Daytime Performance mode (0) transitions at 22:00 to Silent 1 (1) via service call; subsequent emergency cold-snap detection triggers immediate override back to Performance mode (0), verifying seamless state transitions and acoustic/thermal management.
3. **`test_scenario_emergency_legionella_thermal_disinfection_cycle`**:
   User initiates Legionella prevention service call and sets DHW target temperature to 60°C. Verified variable patch `RequestLegionellaPrevention: 1`, high-temperature telemetry response (62.5°C water temp, DHW valve open), active switch state, and clean cancellation via `async_skip_legionella_prevention`.
4. **`test_scenario_multi_pump_residential_and_annex_coordination`**:
   Dual-pump topology: Main House (with DHW: 39 sensors, 3 switches) and Detached Annex (without DHW: 32 sensors, 1 switch). Verifies feature gating, targeted service routing by `heat_pump_id` to distinct control board IDs without cross-device mutation, and independent energy monitoring.
5. **`test_scenario_transient_network_outage_and_oauth_recovery`**:
   Transient outage resilience: Service call encounters expired OAuth token (401), triggers transparent token renewal, encounters a transient 502 Bad Gateway during settings polling, recovers on subsequent poll attempt (status 2), and refreshes data without marking integration failed.
6. **`test_scenario_full_lifecycle_setup_controls_diagnostics_teardown`**:
   Full setup-to-teardown integration lifecycle: `async_setup_entry` initialization across all 5 platforms, service registration, entity interactions, diagnostics export with comprehensive token/serial redaction, and `async_unload_entry` clean teardown with zero lingering resources.
7. **`test_scenario_dhw_boost_timed_cycle_workflow`**:
   Domestic Hot Water timed boost: User triggers boost switch, verifying client call with target temperature 60°C and 60-minute duration. Telemetry confirms active boost control method, and manual turn-off cleanly refreshes state.
8. **`test_scenario_heat_pump_state_machine_and_cop_reconciliation`**:
   Thermodynamic state machine: Heat pump cycles through Standby (40) -> Heating (70) -> Defrosting (110) -> DHW (150). Validates sensor state strings, actuator binary sensors, and COP behavior (safe `None` during zero/None power in standby, accurate 4.0 during active heating).

---

## Test Execution & Verification

### Test Runner Commands
```bash
# Direct via uv (fast execution)
uv run pytest

# Verbose execution with test names
uv run pytest -v

# Via mise task runner
mise run test

# Linting & code formatting
uv run ruff check .
uv run ruff format --check .
```

### Coverage Thresholds
- **Pass Rate**: 100% (0 failures, 0 errors, 0 flaky tests allowed).
- **Execution Speed**: Full suite of 525 tests executes in ~1.2 seconds.
- **Offline Integrity**: 0 unmocked network sockets, 0 live credential requirements.
