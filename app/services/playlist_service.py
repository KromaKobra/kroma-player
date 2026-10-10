"""Playlist service - business logic layer for playlists.

Sits between the ViewModels and PlaylistRepository, mirroring TrackService: it wraps
repository calls, emits EventBus events on every state change, and logs. ViewModels
subscribe to the events to keep the playlist grid and the open-playlist view in sync
without polling.

Emitted events:
- ``playlist.created``        (playlist_id)
- ``playlist.deleted``        (playlist_id)
- ``playlist.updated``        (playlist_id)            — renamed
- ``playlist.track_added``    (playlist_id, track_id)
- ``playlist.track_removed``  (playlist_id, track_id)
- ``playlist.reordered``      (playlist_id)            — same tracks, new order

Relationship:
    PlaylistView / context menus ← PlaylistService ← PlaylistRepository ← SQLite
"""
import logging
from typing import List, Optional

from app.models.playlist import Playlist
from app.models.track import Track
from app.repositories.playlist_repository import PlaylistRepository
from app.events.event_bus import EventBus

logger = logging.getLogger(__name__)


class PlaylistService:
    """Orchestrates playlist operations with event emission and logging."""

    def __init__(self, playlist_repo: PlaylistRepository, event_bus: EventBus):
        self.playlist_repo = playlist_repo
        self.event_bus = event_bus

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_summaries(self) -> List[dict]:
        """Return summary dicts for every playlist (for the grid)."""
        return self.playlist_repo.get_summaries()

    def get_tracks(self, playlist_id: int) -> List[Track]:
        """Return a playlist's tracks in order (artists/genres eager-loaded)."""
        return self.playlist_repo.get_tracks(playlist_id)

    def get_track_ids(self, playlist_id: int) -> List[int]:
        """Return a playlist's ordered track ids (for replace-queue-and-play)."""
        return self.playlist_repo.get_track_ids(playlist_id)

    def get_playlist(self, playlist_id: int) -> Optional[Playlist]:
        """Fetch a single playlist by id (for header metadata)."""
        return self.playlist_repo.get_by_id(playlist_id)

    # ------------------------------------------------------------------
    # Mutations
    # ------------------------------------------------------------------

    def create_playlist(self, name: str, description: str = "") -> int:
        """Create a playlist and emit ``playlist.created``. Returns its id."""
        playlist = self.playlist_repo.create_playlist(name, description)
        self.event_bus.emit('playlist.created', playlist.id)
        logger.info(f"Playlist created: {playlist.name} (ID: {playlist.id})")
        return playlist.id  # type: ignore[return-value]

    def rename_playlist(self, playlist_id: int, name: str) -> bool:
        """Rename a playlist and emit ``playlist.updated``."""
        playlist = self.playlist_repo.rename_playlist(playlist_id, name)
        if playlist is None:
            return False
        self.event_bus.emit('playlist.updated', playlist_id)
        logger.info(f"Playlist renamed: ID {playlist_id} -> {name}")
        return True

    def delete_playlist(self, playlist_id: int) -> bool:
        """Delete a playlist and emit ``playlist.deleted``."""
        if not self.playlist_repo.delete_playlist(playlist_id):
            return False
        self.event_bus.emit('playlist.deleted', playlist_id)
        logger.info(f"Playlist deleted: ID {playlist_id}")
        return True

    def add_track(self, playlist_id: int, track_id: int) -> bool:
        """Append a track to a playlist; emit ``playlist.track_added`` if inserted."""
        added = self.playlist_repo.add_track(playlist_id, track_id)
        if added:
            self.event_bus.emit('playlist.track_added', playlist_id, track_id)
            logger.info(f"Track {track_id} added to playlist {playlist_id}")
        return added

    def add_tracks(self, playlist_id: int, track_ids: List[int]) -> int:
        """Append several tracks (skipping duplicates). Returns the number added."""
        added = 0
        for track_id in track_ids:
            if self.playlist_repo.add_track(playlist_id, track_id):
                added += 1
                self.event_bus.emit('playlist.track_added', playlist_id, track_id)
        if added:
            logger.info(f"Added {added} track(s) to playlist {playlist_id}")
        return added

    def remove_track(self, playlist_id: int, track_id: int) -> bool:
        """Remove a track from a playlist; emit ``playlist.track_removed``."""
        removed = self.playlist_repo.remove_track(playlist_id, track_id)
        if removed:
            self.event_bus.emit('playlist.track_removed', playlist_id, track_id)
            logger.info(f"Track {track_id} removed from playlist {playlist_id}")
        return removed

    def remove_tracks(self, playlist_id: int, track_ids: List[int]) -> int:
        """Remove several tracks from a playlist. Returns the number removed.

        One transaction for the lot, then one event per track. The events stay
        per-track because that is what every subscriber already expects; it is
        the twelve commits behind them that were worth losing.
        """
        removed = self.playlist_repo.remove_tracks(playlist_id, track_ids)
        for track_id in removed:
            self.event_bus.emit('playlist.track_removed', playlist_id, track_id)
        if removed:
            logger.info(f"Removed {len(removed)} track(s) from playlist {playlist_id}")
        return len(removed)

    def set_order(self, playlist_id: int, track_ids: List[int]) -> bool:
        """Rearrange a playlist; emit ``playlist.reordered``. True if it changed.

        A distinct event rather than ``playlist.updated``, which means "renamed"
        to its only subscriber, and not ``track_added``/``track_removed``, which
        would have the open playlist re-read a membership that has not moved.
        Nothing joined or left — the same tracks are in a different order.
        """
        changed = self.playlist_repo.set_order(playlist_id, track_ids)
        if changed:
            self.event_bus.emit('playlist.reordered', playlist_id)
            logger.info(f"Playlist {playlist_id} reordered")
        return changed
