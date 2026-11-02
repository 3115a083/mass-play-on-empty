"""Small, dependency-free safety rules for Sonos Play-on-empty recovery."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any


def _value(value: Any) -> str:
    """Normalize enum values without importing Music Assistant models."""
    return str(getattr(value, "value", value)).lower()


def eligible_error(error: Mapping[str, Any]) -> bool:
    """Only react to no-content, not an arbitrary failed or missing track.

    A physical Sonos Play press with nothing loaded reports no trackName/itemId.
    An item-specific stream failure is NOT permission to replace playback.
    """
    return (
        error.get("errorCode") == "ERROR_PLAYBACK_NO_CONTENT"
        and error.get("reason") in (None, "ERROR_NO_CONTENT", "No content loaded")
        and not error.get("itemId")
        and not error.get("trackName")
    )


def eligible_state(
    player_state: Any,
    queue_state: Any,
    current_item: Any,
    queue_items: Sequence[Any] | None,
) -> bool:
    """Fail closed on paused/playing/unknown states or a non-empty queue."""
    return (
        _value(player_state) in ("idle", "stopped")
        and _value(queue_state) in ("idle", "stopped")
        and current_item is None
        and queue_items is not None
        and len(queue_items) == 0
    )


def normalize_players(raw: Any) -> set[str]:
    """Preserve valid player IDs; never treat a raw string as a character list."""
    if not isinstance(raw, (list, tuple, set)):
        return set()
    return {s for item in raw if isinstance(item, str) if (s := item.strip())}


def safe_media_uri(value: Any) -> str:
    """Reject blank or multiline options; MA resolves the URI itself."""
    if not isinstance(value, str):
        return ""
    if any(character in value for character in "\r\n\x00"):
        return ""
    uri = value.strip()
    if not uri or len(uri) > 2048:
        return ""
    return uri
