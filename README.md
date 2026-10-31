# Weheat Custom Home Assistant Integration (Unofficial)

> **⚠️ Disclaimer:** This is an **unofficial, personal project** and is **not affiliated with, endorsed by, or supported by Weheat B.V.** Use at your own risk.

Custom Home Assistant integration for Weheat heat pumps (**Blackbird P60/P80**, **Sparrow P60**, **Flint P40**). It is a drop-in superset of the built-in Home Assistant Core `weheat` integration, preserving all 39 telemetry/energy sensors and 16 binary sensors while adding full read/write control over **Performance Mode**, **Silent Hours / Night Limit schedules**, **Cooling Mode & 2-Point Cooling Curve**, **Legionella Prevention**, and **DHW Boost**.

---

## Features

- **Zero External Package Dependencies**: Built-in async API client (`requirements: []`) communicating directly with Weheat's `/third_party` and Portal APIs over Home Assistant's shared `aiohttp` session.
- **Performance & Silent Hours Control**:
  - **Performance mode** select (`Mode 5 - Max capacity` down to `Mode 1 - Minimum noise`).
  - **Silent hours preset** select (`Off`, `Minimum`, `Whisper`, `Library`, `Light rain`, `Background chatter`, `Normal`) with automatic firmware constraint enforcement (`SilentHoursScheduledPreset > PerformanceModePreset`).
  - **Silent hours schedule** toggle switch + 14 per-day start/stop time entities (`Monday`–`Sunday`).
- **Cooling Mode & 2-Point Cooling Curve**:
  - **Cooling mode** switch matching the Weheat Portal's 3-branch control logic (supports both `CoolingCurve` and `OpenTherm` cooling setups).
  - **Cooling water target at 22°C outside** (mild day, `15–22 °C`) and **Cooling water target at 40°C outside** (peak-summer day, `15–22 °C`) number entities.
- **DHW & Disinfection Controls** (automatically created when `has_dhw` is detected):
  - **DHW boost** switch and **Legionella prevention** switch.
- **Two-Way Sync & Background Token Refresh**:
  - **Telemetry coordinator**: Polls live heat pump logs every **2 minutes** (scaled by number of heat pumps).
  - **Portal settings coordinator**: Polls installation settings every **5 minutes** so changes made in the Weheat mobile app or web portal are reflected in Home Assistant.
  - **Energy coordinator**: Polls cumulative energy counters every **30 minutes**.
  - Automatic Keycloak PKCE authentication and silent refresh token rotation without triggering integration reloads.

---

## Installation & Configuration

### 1. Install via HACS (Recommended)
1. Open **HACS** in Home Assistant → **Integrations** → top-right menu (`⋮`) → **Custom repositories**.
2. Add `https://github.com/JayXD-2K/weheat` with category **Integration**.
3. Click **Download** on the **Weheat** integration and restart Home Assistant.

### 2. Add the Integration
1. Go to **Settings → Devices & Services → Add Integration** and search for **Weheat**.
2. Sign in with your Weheat account via the standard OAuth2 flow (uses Weheat's official `HomeAssistantAPI` client credentials automatically).

### 3. Enable Write Controls (Weheat Portal Credentials)
Because Weheat's public `HomeAssistantAPI` OAuth client is read-only for control board settings, enabling write controls requires configuring Portal authentication once:
1. Go to **Settings → Devices & Services → Weheat** and click **Configure** (gear icon).
2. Enter your **Weheat Portal email and password** (or a portal session/refresh token) and submit.
3. The integration exchanges your credentials via Keycloak PKCE for a renewable token and automatically refreshes it in the background.

---

## Entities Reference

### Controls Card
| Platform | Entity | Description |
| :--- | :--- | :--- |
| `select` | **Performance mode** | Daytime power/noise cap: `Mode 5 - Max capacity` (0), `Mode 4 - Standard` (1), `Mode 3 - Reduced` (2), `Mode 2 - Low noise` (3), `Mode 1 - Minimum noise` (4). |
| `select` | **Silent hours preset** | Night limit preset applied during scheduled quiet hours: `Off` (0), `Minimum` (7 / ~41 dBA), `Whisper` (6 / ~44 dBA), `Library` (4 / ~47 dBA), `Light rain` (3 / ~50 dBA), `Background chatter` (2 / ~52 dBA), `Normal` (1 / ~55 dBA). |
| `switch` | **Silent hours schedule** | Toggles the scheduled night limit on or off while remembering your last active silent hours preset. |
| `switch` | **Cooling mode** | Enables or disables active cooling mode on the heat pump. |
| `number` | **Cooling water target at 22°C outside** | Mild-day cooling curve water temperature target (`15–22 °C`, default `18 °C`). |
| `number` | **Cooling water target at 40°C outside** | Peak-summer cooling curve water temperature target (`15–22 °C`, default `20 °C`). |
| `switch` | **Legionella prevention** *(DHW only)* | Triggers or skips a one-shot Legionella thermal disinfection cycle. |
| `switch` | **DHW boost** *(DHW only)* | Triggers a Domestic Hot Water boost cycle. |

### Configuration Card
- **Daily Silent Hours Schedule (`time`)**: 14 time pickers (`Monday silent hours start`, `Monday silent hours stop` … `Sunday silent hours start`, `Sunday silent hours stop`) to adjust the quiet window per day of the week.

### Sensors & Binary Sensors
- **39 Sensors**: Heat pump status, COP, input/output power, compressor speed (`rpm` & `%`), water inlet/outlet/house-in/target temperatures, room temperature & setpoint, outside temperature, DHW top/bottom temperatures & flow, CH flow volume, cooling diagnostics (`cooling_state`, `cooling_pause_reason`, `cooling_stop_reason`, `cooling_blocked_by`, `cooling_conditions_met`, `cooling_wait_until`, `last_cooling_time`), and cumulative electrical/thermal energy counters (including indoor unit splits).
- **16 Binary Sensors**: Indoor unit water pump, auxiliary pump, DHW valve/pump, gas boiler, electric heater, and 11 diagnostic cooling start condition flags.

---

## Custom Actions (Services)

### `weheat.set_quiet_mode`
Set the daytime performance/quiet mode preset.
```yaml
action: weheat.set_quiet_mode
target:
  device_id: YOUR_DEVICE_ID
data:
  mode: "Mode 3 - Reduced"
```

### `weheat.set_silent_schedule`
Configure the silent hours night limit preset and start/stop window for specific days (or omit `days` for all 7 days).
```yaml
action: weheat.set_silent_schedule
target:
  device_id: YOUR_DEVICE_ID
data:
  preset: "Background chatter"
  days:
    - Monday
    - Tuesday
    - Wednesday
    - Thursday
    - Friday
  start_time: "22:00:00"
  stop_time: "07:00:00"
```

### `weheat.set_cooling`
Enable or disable cooling mode and optionally update the 2-point cooling curve water target temperatures (`15–22 °C`).
```yaml
action: weheat.set_cooling
target:
  device_id: YOUR_DEVICE_ID
data:
  enabled: true
  mild_temperature: 18
  peak_temperature: 20
```

### `weheat.trigger_legionella_prevention`
Manually trigger a one-shot Legionella thermal disinfection cycle.
```yaml
action: weheat.trigger_legionella_prevention
target:
  device_id: YOUR_DEVICE_ID
```

---

## Development & Testing

This repository uses [`mise`](https://mise.jdx.dev/) and [`uv`](https://docs.astral.sh/uv/) for reproducible cross-platform development and offline unit testing.

```bash
# Sync dependencies
uv sync --all-extras

# Run the full test suite (1,150+ offline unit & adversarial tests)
uv run pytest -q

# Run Ruff linter and formatter checks
uv run ruff check .
uv run ruff format --check .
```

---

## Privacy & Security

All mock fixtures and tests use anonymized UUIDs and mock identifiers. Never commit real credentials, device serial numbers, or access tokens.

## License & Attribution

Licensed under the [Apache License 2.0](LICENSE).

Portions of this integration (such as sensor/binary sensor entity definitions, telemetry formulas, `icons.json`, and translation strings) are based on the upstream [Home Assistant Core `weheat` integration](https://github.com/home-assistant/core/tree/dev/homeassistant/components/weheat) and [`wefabricate/wh-python`](https://github.com/wefabricate/wh-python). This project is an independent, unofficial modification and is not affiliated with or endorsed by Weheat B.V. or Home Assistant.
