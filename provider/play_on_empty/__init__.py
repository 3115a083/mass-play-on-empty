"""Music Assistant native plugin: play fallback media after Sonos reports an empty source.

Subscribes to the existing Sonos S2 (aiosonos) client; no polling, log scraping,
monkey-patching, or Home Assistant dependency. Internal provider APIs are version-sensitive.
"""

from __future__ import annotations

import asyncio
import hashlib
import time
from collections.abc import Callable, Mapping
from typing import TYPE_CHECKING, Any

from aiosonos.const import EventType as SonosEventType
from music_assistant_models.config_entries import ConfigEntry, ConfigValueOption
from music_assistant_models.enums import ConfigEntryType, EventType, PlaybackState, QueueOption
from music_assistant.models.plugin import PluginProvider

from .logic import eligible_error, eligible_state, normalize_players, safe_media_uri

if TYPE_CHECKING:
    from music_assistant.mass import MusicAssistant
    from music_assistant.models import ProviderInstanceType
    from music_assistant_models.config_entries import ProviderConfig
    from music_assistant_models.provider import ProviderManifest

DOMAIN = "play_on_empty"
CONF_PLAYERS = "players"
CONF_FALLBACK_URI = "fallback_uri"
CONF_COOLDOWN = "cooldown_seconds"
CONF_LOG_SKIPS = "log_skips"
SUPPORTED_FEATURES: set = set()

# Sonos S2 integration dependency is already installed with the Sonos provider.
# The add-on does not attempt to install or update aiosonos itself.


def override_key(player_id: str) -> str:
    """Produce a stable and UI-safe config entry key, including for special player IDs."""
    return "fallback_" + hashlib.sha256(player_id.encode("utf-8")).hexdigest()[:16]


async def setup(
    mass: MusicAssistant, manifest: ProviderManifest, config: ProviderConfig
) -> ProviderInstanceType:
    """Create the Music Assistant provider instance."""
    return PlayOnEmptyProvider(mass, manifest, config, SUPPORTED_FEATURES)


class PlayOnEmptyProvider(PluginProvider):
    """Observe only explicitly enabled Sonos S2 speakers."""

    def __init__(self, mass: MusicAssistant, manifest: ProviderManifest,
                 config: ProviderConfig, supported_features: set | None = None) -> None:
        super().__init__(mass, manifest, config, supported_features)
        self._subscriptions: dict[str, tuple[Any, Callable[[], None]]] = {}
        self._unsubscribe_players: Callable[[], None] | None = None
        self._pending: dict[str, asyncio.Task] = {}
        self._last_attempt: dict[str, float] = {}
        self._closing = False

    def _selected_ids(self) -> set[str]:
        return normalize_players(self.get_config_value(CONF_PLAYERS, []))

    def _player(self, player_id: str) -> Any | None:
        player = self.mass.players.get_player(player_id)
        if player is None or getattr(getattr(player, "provider", None), "domain", None) != "sonos":
            return None
        return player

    async def get_config_entries(self) -> tuple[ConfigEntry, ...]:
        """Show a multi-player picker, one shared fallback, and per-player overrides."""
        chosen = self._selected_ids()
        available = {
            player.player_id: player
            for player in self.mass.players.all_players(False, False)
            if getattr(getattr(player, "provider", None), "domain", None) == "sonos"
        }
        # Keep temporarily unavailable selections visible when editing settings.
        player_options = [
            ConfigValueOption(pid, title=(available[pid].display_name if pid in available else pid))
            for pid in sorted(available.keys() | chosen)
        ]
        result: list[ConfigEntry] = [
            ConfigEntry(
                key=CONF_PLAYERS,
                type=ConfigEntryType.STRING,
                multi_value=True,
                required=False,
                default_value=[],
                options=player_options,
            ),
            ConfigEntry(
                key=CONF_FALLBACK_URI,
                type=ConfigEntryType.STRING,
                required=False,
                default_value="",
            ),
            ConfigEntry(
                key=CONF_COOLDOWN,
                type=ConfigEntryType.INTEGER,
                required=True,
                default_value=20,
                range=(5, 120),
                advanced=True,
            ),
            ConfigEntry(
                key=CONF_LOG_SKIPS,
                type=ConfigEntryType.BOOLEAN,
                default_value=False,
                required=False,
                advanced=True,
            ),
        ]
        for player_id in sorted(chosen):
            label = available[player_id].display_name if player_id in available else player_id
            result.append(
                ConfigEntry(
                    key=override_key(player_id),
                    label=f"Fallback media URI for {label}",
                    type=ConfigEntryType.STRING,
                    required=False,
                    default_value="",
                    advanced=True,
                )
            )
        return tuple(result)

    async def loaded_in_mass(self) -> None:
        """Register to Sonos S2's existing local playback error stream."""
        await super().loaded_in_mass()
        self._closing = False
        self._unsubscribe_players = self.mass.subscribe(
            self._player_event,
            (EventType.PLAYER_ADDED, EventType.PLAYER_UPDATED, EventType.PLAYER_REMOVED),
        )
        for player_id in self._selected_ids():
            self._refresh_subscription(player_id)
        self.logger.info(
            "Play on Empty enabled for %d selected Sonos S2 player(s)", len(self._selected_ids())
        )

    def _player_event(self, event: Any) -> None:
        """Recover subscriptions when a player appears, disappears, or reconnects."""
        if self._closing or not event.object_id or event.object_id not in self._selected_ids():
            return
        self._refresh_subscription(event.object_id)

    def _refresh_subscription(self, player_id: str) -> None:
        """Maintain exactly one callback per currently connected Sonos client."""
        player = self._player(player_id)
        client = getattr(player, "client", None) if player and getattr(player, "connected", False) else None
        existing = self._subscriptions.get(player_id)
        if existing is not None and existing[0] is client:
            return
        if existing is not None:
            self._remove_subscription(player_id)
        if client is None or not callable(getattr(client, "subscribe", None)):
            return

        def on_error(event: Any) -> None:
            if self._closing or player_id not in self._selected_ids():
                return
            raw = getattr(event, "data", None)
            if not isinstance(raw, Mapping) or not eligible_error(raw):
                return
            # Reserve the player before scheduling: duplicates cannot both enqueue.
            if player_id in self._pending:
                return
            now = time.monotonic()
            cooldown = int(self.get_config_value(CONF_COOLDOWN, 20) or 20)
            if now - self._last_attempt.get(player_id, float("-inf")) < cooldown:
                return
            self._last_attempt[player_id] = now
            task = self.mass.create_task(self._handle_empty_press(player_id))
            self._pending[player_id] = task
            task.add_done_callback(lambda completed: self._pending.pop(player_id, None)
                                   if self._pending.get(player_id) is completed else None)

        try:
            unsubscribe = client.subscribe(on_error, SonosEventType.PLAYBACK_ERROR)
        except Exception:
            self.logger.exception("Failed to subscribe to Sonos playback errors for %s", player_id)
            return
        self._subscriptions[player_id] = (client, unsubscribe)
        self.logger.debug("Subscribed to Sonos playback errors for %s", player_id)

    def _remove_subscription(self, player_id: str) -> None:
        record = self._subscriptions.pop(player_id, None)
        if record is None:
            return
        try:
            record[1]()
        except Exception:
            self.logger.debug("Sonos callback already disconnected for %s", player_id)

    async def _handle_empty_press(self, player_id: str) -> None:
        """Fail closed unless the selected player's own MA queue is really empty."""
        try:
            if self._closing or player_id not in self._selected_ids():
                return
            player = self._player(player_id)
            if player is None or not getattr(player, "connected", False):
                return
            # Do not act on synced group members; only group coordinators emit
            # the active Sonos playback error.
            if getattr(player, "synced_to", None):
                return
            queue = self.mass.player_queues.get(player_id)
            if queue is None:
                return
            if not eligible_state(
                getattr(player.state, "playback_state", None),
                getattr(queue, "state", None),
                getattr(queue, "current_item", None),
                self.mass.player_queues.items(player_id, limit=1),
            ):
                if self.get_config_value(CONF_LOG_SKIPS, False):
                    self.logger.info("Ignoring empty-content error: player/queue not idle (%s)", player_id)
                return
            uri = safe_media_uri(self.get_config_value(override_key(player_id), "")) or (
                safe_media_uri(self.get_config_value(CONF_FALLBACK_URI, ""))
            )
            if not uri:
                if self.get_config_value(CONF_LOG_SKIPS, False):
                    self.logger.info("No fallback media URI configured for %s", player_id)
                return
            # Re-check after reading configuration, before changing playback.
            if self._closing or not eligible_state(
                getattr(player.state, "playback_state", None),
                getattr(queue, "state", None),
                getattr(queue, "current_item", None),
                self.mass.player_queues.items(player_id, limit=1),
            ):
                return
            await self.mass.player_queues.play_media(
                queue_id=player_id, media=uri, option=QueueOption.REPLACE
            )
            self.logger.info("Started configured fallback media on %s after empty Sonos Play", player_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            self.logger.exception("Cannot start fallback media on %s", player_id)

    async def unload(self, is_removed: bool = False) -> None:
        """Unsubscribe first, then stop in-flight fallback tasks."""
        self._closing = True
        if self._unsubscribe_players is not None:
            self._unsubscribe_players()
            self._unsubscribe_players = None
        for player_id in list(self._subscriptions):
            self._remove_subscription(player_id)
        pending = list(self._pending.values())
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._pending.clear()
