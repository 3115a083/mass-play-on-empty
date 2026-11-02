# Known limitations and failures

## 2026-11-02 | Physical Sonos button event

**Evidence:** User's standalone Music Assistant logs showed two physical presses on Speaker Bad triggering ERROR_PLAYBACK_NO_CONTENT (ERROR_NO_CONTENT).

**Cause:** The Sonos S2 provider logs the aiosonos playback error but does not publish it as an ordinary Home Assistant automation event.

**Mitigation:** Register an additional Sonos client callback from a Music Assistant Plugin Provider. Strictly check the queue/player state before triggering playback.

**Verification:** Behavioral tests use a simulated Sonos client and MA player controller. Real hardware and installed-server-version compatibility remain unverified.

**Other constraints:** Sonos S1 is out of scope. No observable event, a non-empty queue, unknown playback state or absent fallback URI will not trigger playback.
