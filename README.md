# Play on Empty for Music Assistant

A native [Music Assistant](https://www.music-assistant.io/) **Plugin Provider** for Sonos S2 speakers. When a physical Play press causes the Sonos **no-content playback error**, this plugin can start a configured Music Assistant radio station or playlist **only if the MA queue is empty and playback is not paused**.

- Direct Sonos event listener, no log scraping or polling
- Multi-speaker picker and optional individual fallback media URIs
- Per-speaker cooldown and protection against duplicate events
- No Home Assistant dependency or extra network service
- Read-only bind-mount installation for standalone Docker deployments

**Compatibility:** This plugin uses Music Assistant's **internal Sonos S2** client interface. It is not a generic Sonos S1 or multi-brand hardware-button detector. Music Assistant releases can break this integration. Mocked tests are included, but a live speaker acceptance test is still required.

## Behavior and safeguards

1. Subscribe to `SonosEventType.PLAYBACK_ERROR` on the existing Sonos S2 player client.
2. Accept only `ERROR_PLAYBACK_NO_CONTENT`, without a track name or item ID, and with a no-content reason.
3. Require the selected player and its Music Assistant queue to be idle or stopped, with no current item and zero queue items.
4. Play the configured Music Assistant media URI on that player's queue with the replace option.
5. Ignore further errors from the same speaker for a configurable cooldown, default 20 seconds.

The plugin **does not auto-play on ordinary idle state changes**. It never overrides a paused or active queue. If a player is unavailable or a queue is missing or non-empty, nothing happens. It ignores Sonos group followers and errors tied to specific tracks.

## Configure in Music Assistant

Go to **Settings → Providers → Add Provider → Play on Empty**, then open its options.

| Option | Function |
| --- | --- |
| **Sonos S2 speakers** | Choose one or more Sonos players. Empty selection is inert. |
| **Default fallback media URI** | A Music Assistant radio or playlist URI, used by all selected players unless overridden. Blank disables fallback. |
| **Fallback media URI for [speaker]** | Optional individual override. Select speakers, save and reopen options to see per-speaker fields. |
| **Repeat-event cooldown** | 5 to 120 seconds, default 20. |
| **Log ignored attempts** | Additional diagnostic messages when the state or configuration blocks playback. |

Use a **Music Assistant URI**, not merely a display name, for reliable playback resolution. Obtain the station/playlist URI from your own MA library or media browser. The URI is passed unchanged to Music Assistant's own media resolver. It can represent a radio station or playlist.

For a radio station in the bathroom, select **Speaker Bad** and provide its desired URI. To assign a different station to the kitchen, select that speaker too, save, reopen the provider options and fill its speaker-specific override.

## Install in an independent Docker container

Music Assistant scans the installed Python package's providers directory. It does **not** automatically discover provider code placed in the persistent `/data` directory, nor does its normal setup flow install code directly from GitHub.

**1. Clone the repository on the Docker host:**

```bash
git clone https://github.com/3115a083/mass-play-on-empty.git /opt/mass-play-on-empty
```

**2. Discover the real installed-provider path in your existing container:**

```bash
docker exec music-assistant-server python -c \
  'import music_assistant, pathlib; print(pathlib.Path(music_assistant.__file__).parent / "providers" / "play_on_empty")'
```

Replace the container name if yours differs. The package location depends on the image and Python version.

**3. Add a read-only bind mount to the existing Music Assistant Compose service.** Substitute the right-hand path with the result of step 2.

```yaml
services:
  music-assistant-server:
    image: ghcr.io/music-assistant/server:latest
    network_mode: host
    volumes:
      - /your/existing/music-assistant/data:/data/
      - /opt/mass-play-on-empty/provider/play_on_empty:/app/venv/lib/python3.14/site-packages/music_assistant/providers/play_on_empty:ro
```

**Important:** The displayed container path is **an example, not a guaranteed path**. Preserve your existing Compose service settings, image pin, volumes, environment and restart policy. Mount **only** the `play_on_empty` folder, not the entire installed providers directory. Do not grant privileged mode or additional Linux capabilities.

**4. Recreate only this service to activate the mount:**

```bash
docker compose up -d --no-deps --force-recreate music-assistant-server
docker exec music-assistant-server python -c \
  'import music_assistant, pathlib; p=pathlib.Path(music_assistant.__file__).parent/"providers"/"play_on_empty"; print(p, p.exists())'
```

The mounted folder must contain `__init__.py`, `logic.py`, `manifest.json` and `strings.json`. Then add the provider in the Music Assistant web interface and configure it.

**Updates:** Update this checkout to a reviewed/tagged version and restart Music Assistant. Recheck the mount path after base-image upgrades. Avoid unreviewed automatic updates of this private-API extension.

**Rollback:** Disable/remove the provider in Music Assistant, remove just the new bind mount from Compose and recreate the container. Your existing Music Assistant data remains mounted under `/data`.

## Reproduce and troubleshoot

Start with an idle Sonos S2 speaker whose MA queue is empty. Press the **physical Play button**. The expected upstream warning is:

```text
Speaker Bad could not play None and reported ERROR_PLAYBACK_NO_CONTENT (ERROR_NO_CONTENT)
```

After the plugin responds, its info-level message looks like:

```text
Started configured fallback media on RINCON_... after empty Sonos Play
```

If the upstream warning appears but the radio does not start, check whether the correct Sonos S2 MA player is selected, a URI is configured, the queue is empty and idle, the speaker is the group coordinator, and the cooldown has expired. Enable **Log ignored attempts** for more detail. No IP addresses, passwords, raw event payloads or playback history are intentionally logged by this plugin.

This plugin cannot react to hardware presses that never cause the Sonos no-content error. It intentionally refuses to replace a specific track that failed for network, decoder or service reasons.

## Development and compatibility

Primary upstream contracts:

- [Plugin provider template](https://github.com/music-assistant/server/tree/main/music_assistant/providers/_demo_plugin_provider)
- [Sonos S2 provider](https://github.com/music-assistant/server/blob/main/music_assistant/providers/sonos/player.py)
- [Music Assistant setup flows and options](https://developers.music-assistant.io/setup-flows/)
- [Official Docker installation](https://www.music-assistant.io/installation/)

The implementation uses `PluginProvider`, provider runtime options, `loaded_in_mass` / `unload`, Sonos S2's existing `aiosonos` client, and `mass.player_queues.play_media`. All callbacks are released on unload. It requires the normal Sonos S2 provider to supply `aiosonos`; there is no extra pip installation in the plugin manifest.

Run static and simulated-event tests:

```bash
python -m unittest discover -s tests -v
python -m compileall -q provider
```

These tests verify configuration, event routing, queue guards, per-speaker overrides, reconnect, cooldown and cleanup with test doubles. They do **not** prove behavior on a real Sonos device or on your specific installed Music Assistant version.

## Project records

- [CHANGELOG.md](CHANGELOG.md): dated changes.
- [DECISIONS.md](DECISIONS.md): architectural tradeoffs.
- [ERRORS.md](ERRORS.md): known limitations and regressions.

## License

MIT. See [LICENSE](LICENSE).
