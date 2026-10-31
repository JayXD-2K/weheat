# Weheat Custom Home Assistant Integration (Unofficial)

> **⚠️ Disclaimer:** This is an **unofficial, personal project** and is **not affiliated with, endorsed by, or supported by Weheat B.V.** Use at your own risk.

Custom Home Assistant integration for Weheat heat pumps (**Blackbird P60/P80**, **Sparrow P60**, **Flint P40**). It extends the Home Assistant Core `weheat` integration with full read/write controls over performance mode, silent hours scheduling, cooling mode & curve, and domestic hot water (DHW).

## Features

- **Performance & Silent Hours**:
  - **Performance mode** (`Mode 5 - Max capacity` down to `Mode 1 - Minimum noise`).
  - **Silent hours preset** (`Off`, `Minimum`, `Whisper`, `Library`, `Light rain`, `Background chatter`, `Normal`).
  - **Silent hours schedule** toggle and per-day start/stop time controls (`Monday`–`Sunday`).
- **Cooling Mode & Curve**:
  - **Cooling mode** switch.
  - **Cooling water target at 22°C outside** and **40°C outside** (`15–22 °C`).
- **DHW Controls** *(when DHW is present)*:
  - **DHW boost** and **Legionella prevention** switches.
- **Full Telemetry & Two-Way Sync**:
  - 39 sensors and 16 binary sensors matching the official integration.
  - Automatic polling every **2m** (telemetry), **5m** (portal settings sync), and **30m** (energy totals) with silent background token refresh.
- **Custom Actions**: `weheat.set_quiet_mode`, `weheat.set_silent_schedule`, `weheat.set_cooling`, and `weheat.trigger_legionella_prevention`.

## Installation

1. In **HACS → Integrations → Custom repositories**, add `https://github.com/JayXD-2K/weheat` as an **Integration**, install **Weheat**, and restart Home Assistant.
2. Go to **Settings → Devices & Services → Add Integration → Weheat** and sign in with OAuth2.
3. To enable write controls, open **Settings → Devices & Services → Weheat → Configure** and enter your Weheat Portal email and password.

## Development

```bash
uv sync --all-extras
uv run pytest -q
uv run ruff check .
uv run ruff format .
```

## License

Licensed under the [Apache License 2.0](LICENSE). Portions of the sensor/binary sensor definitions and telemetry formulas are based on the [Home Assistant Core `weheat` integration](https://github.com/home-assistant/core/tree/dev/homeassistant/components/weheat) and [`wefabricate/wh-python`](https://github.com/wefabricate/wh-python).
