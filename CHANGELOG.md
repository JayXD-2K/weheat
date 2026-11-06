# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.8] - 2026-11-06

### Added
- Shared [`WeheatControlEntity`](custom_components/weheat/entity.py) base class centralizing portal token resolution, control board ID fallback lookup, and cached settings access across `number`, `select`, `switch`, and `time` platforms.
- Shared `WeheatClient.apply_topology_to_heat_pumps` helper used by both startup discovery and settings coordinator updates.
- Unit tests covering session cookie rotation rejection, NextAuth PKCE error branches, coordinator 401 topology recovery, options flow credential clearing, path sanitization, and origin/state validation.

### Changed
- Consolidated coordinator refresh and error translation helpers (`_async_refresh_coordinators`, `_raise_service_error`) in [`services.py`](custom_components/weheat/services.py) and simplified schedule payload extraction.
- Deduplicated HTTP error extraction (`_extract_error_message`, `_raise_for_portal_response`) and silent schedule validation (`_validate_silent_schedule_dict`) in [`client.py`](custom_components/weheat/client.py).
- Simplified `WeheatHeatPumpSensor` and `WeheatHeatPumpBinarySensor` initialization by delegating `entity_description` and `_attr_unique_id` setup to `WeheatEntity`.

### Security
- Added `_validate_trusted_url` origin and `https://` scheme enforcement in `async_login_portal_pkce` before following NextAuth redirects, submitting Keycloak credentials, or calling the NextAuth OAuth callback.
- Added OAuth2 `state` parameter verification on the Keycloak login callback in both NextAuth and direct PKCE modes.
- Added `_sanitize_path_segment` validation on all dynamic resource IDs (`uuid`, `installation_id`, `control_board_id`, `request_id`, `heat_pump_id`) interpolated into URL paths to prevent path traversal and query injection.
- Hardened `async_refresh_portal_session` to reject un-rotated access tokens when `previous_access_token` is supplied, ensuring automatic fallback to full PKCE re-authentication after a server-side 401 revocation.
- Expanded diagnostics redaction (`TO_REDACT`) in [`diagnostics.py`](custom_components/weheat/diagnostics.py) to include `serialNumber`, `token`, `password`, `email`, `cookie`, `cookies`, and `csrfToken`.

## [0.1.7] - 2026-11-06

### Fixed
- **Issue #2 (`Unauthorized Client`)**: Updated `WeheatClient.async_login_portal_pkce` to authenticate through the `portal.weheat.nl` NextAuth BFF flow (`/api/auth/csrf` -> `/api/auth/signin/keycloak` -> Keycloak `loginAction` -> `/api/auth/callback/keycloak` -> `/api/auth/session`) instead of calling Keycloak's token endpoint directly without `weheat-portal`'s confidential client secret.
- **Issue #1 (`After reboot, can't log in to weheat`)**:
  - Added `WeheatClient.async_refresh_portal_session` and `WeheatPortalSession.async_force_refresh` to renew expiring or rejected portal tokens via NextAuth session cookies or full PKCE re-login.
  - Added automatic HTTP 401 force-refresh and retry in `async_setup_entry` topology discovery and `WeheatSettingsUpdateCoordinator._async_update_data`.
  - Fixed Home Assistant 2026.12+ reauth reload warning in `OAuth2FlowHandler.async_oauth_create_entry` by using `async_update_and_abort` when `update_listeners` are registered.
- Added support for pasting raw NextAuth `Cookie` headers (`__Secure-next-auth.session-token=...`) in the Options Flow.

## [0.1.6] - 2026-10-31

### Changed
- Moved cooling water target number entities to the Configuration card (`EntityCategory.CONFIG`).

## [0.1.5] - 2026-10-31

### Added
- Added cooling mode switch, 2-point cooling curve water target number entities (`22°C` and `40°C` outdoor reference points), and the `weheat.set_cooling` custom action.

## [0.1.4] - 2026-10-31

### Changed
- Updated silent hours preset labels to match the Weheat app names (`Off`, `Minimum`, `Whisper`, `Library`, `Light rain`, `Background chatter`, `Normal`) and fixed the `standby` state translation string.

## [0.1.3] - 2026-10-31

### Changed
- Reverted silent hours schedule time entity names to concise day names and moved mode controls to the Controls card.

## [0.1.2] - 2026-10-31

### Changed
- Ordered silent hours schedule time entities Monday–Sunday, updated performance mode labels (`Mode 5 - Max capacity` to `Mode 1 - Minimum noise`), and skipped full entry reload on background portal token refresh.

## [0.1.1] - 2026-10-31

### Fixed
- Used `PORTAL_CLIENT_ID` (`weheat-portal`) for portal PKCE login and token refresh.

## [0.1.0] - 2026-10-31

### Added
- Initial pre-stable release of the custom Weheat Home Assistant integration with full telemetry sensors, binary sensors, performance mode select, silent hours preset & schedule controls, DHW boost, Legionella prevention switch, and custom actions.
