# Architectural decisions

## 2026-11-02 | Native Music Assistant plugin

**Decision:** Subscribe to the Sonos S2 player's aiosonos PLAYBACK_ERROR stream in Music Assistant. Do not parse logs and do not require Home Assistant events.

**Reason:** The user's physical Sonos button generated ERROR_PLAYBACK_NO_CONTENT in Music Assistant. The upstream provider already processes the same event.

**Safety:** Only selected Sonos S2 players; accept the exact no-content code with no item identity, verify idle player and empty idle MA queue, and avoid group followers. Enforce a cooldown, prevent concurrent fallback work, and unsubscribe on unload.

**Configuration:** Native runtime options, multi-player selection, shared fallback URI and per-player overrides. Media URIs avoid ambiguous name resolution and work with Music Assistant radios and playlists.

**Deployment:** Read-only bind mount into the actual installed music_assistant/providers/play_on_empty path. No added privileges, credentials or network endpoints.

**Tradeoff:** SonosPlayer.client is private and version-sensitive. Live tests with physical devices are still pending.
