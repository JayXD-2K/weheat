# Weheat Custom Home Assistant Integration (Unofficial)

> **⚠️ Disclaimer:** This is an **unofficial, personal project** and is **not affiliated with, endorsed by, or supported by Weheat B.V.** It is very much untested at the moment—use at your own risk.

Custom Home Assistant integration for Weheat heat pumps (Blackbird, Sparrow, Flint), extending the core integration with full control capabilities including Quiet Level, Silent Hours / Night Limit scheduling, Legionella prevention, and domestic hot water (DHW) controls.

## Features

- **Direct In-Tree API Client**: Zero unvetted third-party dependencies (`requirements: []`), communicating directly with the Weheat API via `aiohttp>=3.9.0`.
- **Comprehensive Telemetry**: Full coverage of 39 sensor and 16 binary sensor entities matching Home Assistant core models.
- **Quiet Mode & Silent Hours Control**: Exposes services and controls to set performance mode presets and daily silent hours schedules.
- **Portal & Cloud Feature Parity**: Controls target control boards directly (`/api/v1/control-boards/{controlBoardId}/settings`) with asynchronous request polling.
- **Offline Deterministic Testing**: 100% mock API fixture coverage allowing offline verification with `aioresponses` and `pytest`.

## Project Tooling & Setup

This repository uses [`mise`](https://mise.jdx.dev/) and [`uv`](https://docs.astral.sh/uv/) for reproducible cross-platform development.

### Requirements
- `mise` (or `uv` directly)
- Python 3.12+ (Python 3.13 default in `.mise.toml`)

### Quickstart

```bash
# Authorize mise configuration
mise trust

# Install dependencies and initialize virtual environment
mise run setup

# Run the test suite
mise run test

# Run linter checks
mise run lint

# Auto-format and fix lint issues
mise run format
```

Alternatively, commands can be run directly with `uv`:
```bash
uv sync --all-extras
uv run pytest -v
uv run ruff check .
uv run ruff format .
```

## Repository Structure

```
├── .gitattributes                # LF line ending normalization
├── .gitignore                    # Comprehensive exclusion rules
├── .mise.toml                    # Mise task runner and tool configuration
├── pyproject.toml                # Project metadata, uv, ruff, and pytest config
├── README.md                     # Documentation
├── custom_components/
│   └── weheat/                   # Custom integration code
└── tests/
    ├── conftest.py               # Test harness and mock fixtures
    ├── test_smoke_fixtures.py    # Fixture verification smoke tests
    └── fixtures/                 # Anonymized mock API responses
        ├── token_response.json
        ├── user_me.json
        ├── heat_pumps.json
        ├── latest_logs.json
        ├── energy_total.json
        ├── installation_settings_get.json
        ├── settings_update_success.json
        └── boost_trigger_success.json
```

## Privacy & Security

All mock fixtures and tests use anonymized UUIDs and mock identifiers. Real credentials, device serial numbers, and access tokens are strictly prohibited in git commits.

## License & Attribution

Licensed under the [Apache License 2.0](LICENSE).

Portions of this integration (such as sensor/binary sensor entity definitions, `icons.json`, and translation strings) are based on the upstream [Home Assistant Core `weheat` integration](https://github.com/home-assistant/core/tree/dev/homeassistant/components/weheat) (Apache-2.0). This project is an independent, unofficial modification and is not affiliated with or endorsed by Weheat B.V. or Home Assistant.
