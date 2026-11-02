"""Behavioral tests for the plugin using controlled Music Assistant/Sonos doubles.

No Sonos network traffic or installed Music Assistant package is required.
"""
from __future__ import annotations

import asyncio
import importlib
import json
import logging
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
PROVIDER_DIR = ROOT / "provider" / "play_on_empty"


def _stub(name: str) -> types.ModuleType:
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


def load_plugin():
    """Import the real provider against a minimal, explicit compatibility contract."""
    for name in (
        "aiosonos", "aiosonos.const", "music_assistant_models",
        "music_assistant_models.config_entries", "music_assistant_models.enums",
        "music_assistant", "music_assistant.models", "music_assistant.models.plugin",
        "provider",
    ):
        if name not in sys.modules:
            _stub(name)

    class ConfigEntry:
        def __init__(self, **kwargs):
            self.__dict__.update(kwargs)

    class ConfigValueOption:
        def __init__(self, value, **kwargs):
            self.value = value
            self.__dict__.update(kwargs)

    class ConfigEntryType:
        STRING = "string"
        BOOLEAN = "boolean"
        INTEGER = "integer"

    class EventType:
        PLAYER_ADDED = "player_added"
        PLAYER_UPDATED = "player_updated"
        PLAYER_REMOVED = "player_removed"

    class QueueOption:
        REPLACE = "replace"

    class PlaybackState:
        IDLE = "idle"
        PLAYING = "playing"
        PAUSED = "paused"

    class SonosEventType:
        PLAYBACK_ERROR = "playback_error"

    class PluginProvider:
        def __init__(self, mass, manifest, config, supported_features):
            self.mass = mass
            self.manifest = manifest
            self.config = config
            self.logger = logging.getLogger("play_on_empty.test")

        def get_config_value(self, key, default=None):
            return self.config.get(key, default)

        async def loaded_in_mass(self):
            pass

    sys.modules["aiosonos.const"].EventType = SonosEventType
    sys.modules["music_assistant_models.config_entries"].ConfigEntry = ConfigEntry
    sys.modules["music_assistant_models.config_entries"].ConfigValueOption = ConfigValueOption
    enums = sys.modules["music_assistant_models.enums"]
    enums.ConfigEntryType = ConfigEntryType
    enums.EventType = EventType
    enums.PlaybackState = PlaybackState
    enums.QueueOption = QueueOption
    sys.modules["music_assistant.models.plugin"].PluginProvider = PluginProvider
    sys.modules["provider"].__path__ = [str(ROOT / "provider")]

    sys.modules.pop("provider.play_on_empty", None)
    sys.modules.pop("provider.play_on_empty.logic", None)
    plugin = importlib.import_module("provider.play_on_empty")
    return plugin, EventType


class Client:
    def __init__(self):
        self.listeners = []

    def subscribe(self, callback, event_type):
        self.listeners.append(callback)
        def unsubscribe():
            self.listeners.remove(callback)
        return unsubscribe

    def emit(self, payload):
        for cb in self.listeners[:]:
            cb(SimpleNamespace(data=payload))


class Player:
    def __init__(self, player_id, state="idle", *, domain="sonos"):
        self.player_id = player_id
        self.display_name = f"Speaker {player_id}"
        self.connected = True
        self.provider = SimpleNamespace(domain=domain)
        self.client = Client()
        self.state = SimpleNamespace(playback_state=state)
        self.synced_to = None


class Queues:
    def __init__(self):
        self.queues = {}
        self.queue_items = {}
        self.calls = []

    def get(self, player_id):
        return self.queues.get(player_id)

    def items(self, player_id, limit=500):
        return self.queue_items.get(player_id, [])[:limit]

    async def play_media(self, **kwargs):
        self.calls.append(kwargs)


class Mass:
    def __init__(self, players):
        self._players = {p.player_id: p for p in players}
        self.players = SimpleNamespace(
            get_player=lambda player_id: self._players.get(player_id),
            all_players=lambda *args: list(self._players.values()),
        )
        self.player_queues = Queues()
        self._listeners = []
        self.tasks = []
        for player in players:
            self.player_queues.queues[player.player_id] = SimpleNamespace(
                state="idle", current_item=None
            )

    def create_task(self, coroutine):
        task = asyncio.create_task(coroutine)
        self.tasks.append(task)
        return task

    def subscribe(self, callback, events):
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback)

    def notify(self, player_id):
        for callback in self._listeners[:]:
            callback(SimpleNamespace(object_id=player_id))

    async def settle(self):
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)


class PluginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.plugin, self.event_type = load_plugin()
        self.bad = Player("RINCON_BAD")
        self.kitchen = Player("RINCON_KITCHEN")
        self.mass = Mass([self.bad, self.kitchen])
        self.config = {
            "players": [self.bad.player_id, self.kitchen.player_id],
            "fallback_uri": "library://radio/default",
            "cooldown_seconds": 20,
        }
        self.provider = self.plugin.PlayOnEmptyProvider(self.mass, None, self.config, set())

    async def asyncTearDown(self):
        await self.provider.unload()

    @staticmethod
    def error(**changes):
        return {"errorCode": "ERROR_PLAYBACK_NO_CONTENT",
                "reason": "ERROR_NO_CONTENT",
                "itemId": None,
                "trackName": None, **changes}

    async def test_physical_empty_content_error_starts_radio_once(self):
        await self.provider.loaded_in_mass()
        self.bad.client.emit(self.error())
        self.bad.client.emit(self.error())
        await self.mass.settle()
        self.assertEqual(self.mass.player_queues.calls, [{
            "queue_id": "RINCON_BAD",
            "media": "library://radio/default",
            "option": "replace",
        }])

    async def test_paused_playing_and_non_empty_queue_are_not_replaced(self):
        await self.provider.loaded_in_mass()
        for state in ("paused", "playing", "buffering", "unknown", "unavailable"):
            self.bad.state.playback_state = state
            self.bad.client.emit(self.error())
            await self.mass.settle()
        self.bad.state.playback_state = "idle"
        self.mass.player_queues.queue_items["RINCON_KITCHEN"] = ["a"]
        self.kitchen.client.emit(self.error())
        await self.mass.settle()
        self.assertEqual(self.mass.player_queues.calls, [])

    async def test_strict_error_rejection(self):
        await self.provider.loaded_in_mass()
        for bad in (
            self.error(errorCode="SOME_OTHER_ERROR"),
            self.error(itemId="track-123"),
            self.error(trackName="track"),
            self.error(reason="unrelated error"),
        ):
            self.bad.client.emit(bad)
        await self.mass.settle()
        self.assertEqual(self.mass.player_queues.calls, [])

    async def test_specific_speaker_override_and_selection(self):
        self.config[self.plugin.override_key("RINCON_KITCHEN")] = "library://playlist/my-mix"
        await self.provider.loaded_in_mass()
        self.kitchen.client.emit(self.error())
        await self.mass.settle()
        self.assertEqual(len(self.mass.player_queues.calls), 1)
        self.assertEqual(self.mass.player_queues.calls[0]["media"], "library://playlist/my-mix")
        self.assertEqual(self.mass.player_queues.calls[0]["queue_id"], "RINCON_KITCHEN")

    async def test_no_media_configured_does_not_start(self):
        self.config["fallback_uri"] = ""
        await self.provider.loaded_in_mass()
        self.bad.client.emit(self.error())
        await self.mass.settle()
        self.assertEqual(self.mass.player_queues.calls, [])

    async def test_missing_queue_fails_closed(self):
        self.mass.player_queues.queues.pop(self.bad.player_id)
        await self.provider.loaded_in_mass()
        self.bad.client.emit(self.error())
        await self.mass.settle()
        self.assertEqual(self.mass.player_queues.calls, [])

    async def test_inflight_cooldown_and_unload(self):
        await self.provider.loaded_in_mass()
        self.bad.client.emit(self.error())
        await self.mass.settle()
        self.bad.client.emit(self.error())
        await self.mass.settle()
        self.assertEqual(len(self.mass.player_queues.calls), 1)
        self.provider._last_attempt[self.bad.player_id] -= 30
        self.bad.client.emit(self.error())
        await self.mass.settle()
        self.assertEqual(len(self.mass.player_queues.calls), 2)
        await self.provider.unload()
        self.assertEqual(len(self.bad.client.listeners), 0)
        self.bad.client.emit(self.error())
        await self.mass.settle()
        self.assertEqual(len(self.mass.player_queues.calls), 2)

    async def test_reconnect_and_new_players(self):
        await self.provider.loaded_in_mass()
        original = self.bad.client
        self.bad.connected = False
        self.mass.notify(self.bad.player_id)
        self.assertEqual(len(original.listeners), 0)
        self.bad.client = Client()
        self.bad.connected = True
        self.mass.notify(self.bad.player_id)
        self.assertEqual(len(self.bad.client.listeners), 1)
        self.bad.client.emit(self.error())
        await self.mass.settle()
        self.assertEqual(len(self.mass.player_queues.calls), 1)

    async def test_options_keep_disconnected_selection_and_per_player_fields(self):
        self.mass._players.pop(self.bad.player_id)
        config_entries = await self.provider.get_config_entries()
        players = next(v for v in config_entries if v.key == "players")
        self.assertEqual({o.value for o in players.options},
                         {"RINCON_BAD", "RINCON_KITCHEN"})
        self.assertIn(self.plugin.override_key("RINCON_BAD"),
                      [item.key for item in config_entries])

    async def test_sync_group_member_is_ignored(self):
        self.bad.synced_to = "RINCON_KITCHEN"
        await self.provider.loaded_in_mass()
        self.bad.client.emit(self.error())
        await self.mass.settle()
        self.assertEqual(self.mass.player_queues.calls, [])


class SafetyTests(unittest.TestCase):
    def test_error_and_queue_guards(self):
        plugin, _ = load_plugin()
        logic = importlib.import_module("provider.play_on_empty.logic")
        self.assertTrue(logic.eligible_error({
            "errorCode": "ERROR_PLAYBACK_NO_CONTENT", "reason": "ERROR_NO_CONTENT",
        }))
        self.assertFalse(logic.eligible_error({"errorCode": "ERROR_PLAYBACK_NO_CONTENT",
                                               "itemId": "bad-track"}))
        self.assertTrue(logic.eligible_state("idle", "idle", None, []))
        self.assertFalse(logic.eligible_state("paused", "idle", None, []))
        self.assertFalse(logic.eligible_state("idle", "paused", None, []))
        self.assertFalse(logic.eligible_state("idle", "idle", "old", []))
        self.assertFalse(logic.eligible_state("idle", "idle", None, ["old"]))
        self.assertFalse(logic.eligible_state("unknown", "idle", None, []))

    def test_input_validation(self):
        load_plugin()
        logic = importlib.import_module("provider.play_on_empty.logic")
        self.assertEqual(logic.normalize_players("RINCON_BAD"), set())
        self.assertEqual(logic.normalize_players([" RINCON_BAD ", None]), {"RINCON_BAD"})
        self.assertEqual(logic.safe_media_uri(" \nradio://x "), "")
        self.assertEqual(logic.safe_media_uri(" radio://station "), "radio://station")
        self.assertEqual(logic.safe_media_uri(None), "")

    def test_manifest_contract(self):
        manifest = json.loads((PROVIDER_DIR / "manifest.json").read_text())
        self.assertEqual(manifest["type"], "plugin")
        self.assertEqual(manifest["domain"], "play_on_empty")
        self.assertEqual(manifest["requirements"], [])
        strings = json.loads((PROVIDER_DIR / "strings.json").read_text())
        for key in ("players", "fallback_uri", "cooldown_seconds", "log_skips"):
            self.assertIn(key, strings["config_entries"])


if __name__ == "__main__":
    unittest.main()
