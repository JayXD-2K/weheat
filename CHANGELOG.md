# Changelog

## [0.1.8] - 2026-11-06
- Deduplicated control entity logic (`number`, `select`, `switch`, `time`), coordinator refresh helpers, and API validation.
- Hardened portal authentication with HTTPS/origin checks, OAuth `state` verification, URL path sanitization, and expanded diagnostics redaction.

## [0.1.7] - 2026-11-06
- Fixed `Unauthorized Client` error during portal login by authenticating through the `portal.weheat.nl` NextAuth flow (#2).
- Fixed post-reboot authentication failures with automatic 401 token refresh, NextAuth session cookie renewal, and HA 2026.12 reauth support (#1).

## [0.1.6] - 2026-10-31
- Moved cooling water target entities to the Configuration card.

## [0.1.5] - 2026-10-31
- Added cooling mode switch, 2-point cooling curve controls, and `weheat.set_cooling` action.

## [0.1.4] - 2026-10-31
- Aligned silent hours preset names with the Weheat app and fixed `standby` translation string.

## [0.1.3] - 2026-10-31
- Simplified schedule time entity names to day names and moved mode controls to the Controls card.

## [0.1.2] - 2026-10-31
- Ordered schedule entities Monday–Sunday, updated performance mode labels, and avoided full reloads on token refresh.

## [0.1.1] - 2026-10-31
- Fixed portal PKCE login and refresh to use `weheat-portal` client ID.

## [0.1.0] - 2026-10-31
- Initial pre-stable release with telemetry sensors, performance mode, silent hours scheduling, DHW boost, and Legionella controls.
