"""Playlist repository - data access for playlists and their ordered tracks.

Extends BaseRepository with playlist-specific queries. Like TrackRepository, this
encapsulates the joins against the ordered join table (playlist_tracks) so callers
never have to think about positions or aggregates.

Responsibilities:
- Create / rename / delete playlists.
- Add / remove a track to / from a playlist (append at the end, de-duped).
- Rearrange one: `set_order` rewrites the whole ordering.
- Fetch a playlist's tracks in order, with artists/genres eager-loaded.
- Produce lightweight summaries (track count, total duration, cover) for the grid.

Design:
- Inherits generic CRUD from BaseRepository[Playlist].
- Track membership is stored as PlaylistTrack rows; this repo owns the position
  bookkeeping (new tracks go to max(position)+1, removals leave gaps, and
  `set_order` renumbers the block densely).
"""
from typing import List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from app.models.track import Track
from app.models.playlist import Playlist, PlaylistTrack, _now_us
from app.repositories.base_repository import BaseRepository


class PlaylistRepository(BaseRepository[Playlist]):
    """Repository for playlist-specific data access."""

    def __init__(self, session: Session):
        super().__init__(session, Playlist)

    def _next_modified_at(self) -> int:
        """Keep edits ordered even if the clock repeats or moves backwards."""
        latest = self.session.query(func.max(Playlist.updated_at)).scalar() or 0
        return max(_now_us(), latest + 1)

    def _touch(self, playlist_id: int) -> None:
        playlist = self.get_by_id(playlist_id)
        if playlist is not None:
            playlist.updated_at = self._next_modified_at()

    def touch_for_track_removal(self, track_id: int) -> None:
        """Mark playlists before a library deletion cascades their entries away.

        The caller commits this together with the track deletion.
        """
        playlists = (
            self.session.query(Playlist)
            .join(PlaylistTrack, PlaylistTrack.playlist_id == Playlist.id)
            .filter(PlaylistTrack.track_id == track_id)
            .all()
        )
        if playlists:
            modified_at = self._next_modified_at()
            for playlist in playlists:
                playlist.updated_at = modified_at

    # ------------------------------------------------------------------
    # Playlist CRUD
    # ------------------------------------------------------------------

    def create_playlist(self, name: str, description: str = "") -> Playlist:
        """Create a new (empty) playlist and return it with its generated id."""
        playlist = Playlist(name=name.strip() or "Untitled Playlist",
                            description=(description or "").strip() or None,
                            updated_at=self._next_modified_at())
        self.session.add(playlist)
        self.session.commit()
        return playlist

    def rename_playlist(self, playlist_id: int, name: str) -> Optional[Playlist]:
        """Rename a playlist (and bump updated_at). Returns None if not found."""
        playlist = self.get_by_id(playlist_id)
        if playlist is None:
            return None
        new_name = name.strip() or playlist.name
        if new_name == playlist.name:
            return playlist
        playlist.name = new_name  # type: ignore[assignment]
        self._touch(playlist_id)
        self.session.commit()
        return playlist

    def delete_playlist(self, playlist_id: int) -> bool:
        """Delete a playlist. Its PlaylistTrack rows cascade away at the DB level."""
        playlist = self.get_by_id(playlist_id)
        if playlist is None:
            return False
        self.session.delete(playlist)
        self.session.commit()
        return True

    # ------------------------------------------------------------------
    # Membership
    # ------------------------------------------------------------------

    def add_track(self, playlist_id: int, track_id: int) -> bool:
        """Append a track to the end of a playlist.

        De-dupes: if the track is already in the playlist, nothing happens and
        False is returned. Returns True when a new entry was inserted.
        """
        exists = (
            self.session.query(PlaylistTrack.id)
            .filter_by(playlist_id=playlist_id, track_id=track_id)
            .first()
        )
        if exists is not None:
            return False
        max_pos = (
            self.session.query(func.max(PlaylistTrack.position))
            .filter_by(playlist_id=playlist_id)
            .scalar()
        )
        next_pos = 0 if max_pos is None else max_pos + 1
        self.session.add(
            PlaylistTrack(playlist_id=playlist_id, track_id=track_id, position=next_pos)
        )
        self._touch(playlist_id)
        self.session.commit()
        return True

    def remove_track(self, playlist_id: int, track_id: int) -> bool:
        """Remove a track from a playlist. Returns False if it wasn't present."""
        entry = (
            self.session.query(PlaylistTrack)
            .filter_by(playlist_id=playlist_id, track_id=track_id)
            .first()
        )
        if entry is None:
            return False
        self.session.delete(entry)
        self._touch(playlist_id)
        self.session.commit()
        return True

    def remove_tracks(self, playlist_id: int, track_ids: List[int]) -> List[int]:
        """Remove several tracks in one transaction. Returns the ids actually removed.

        One commit rather than one per track. Removing a dozen rows through
        ``remove_track`` is a dozen transactions, and — because the service emits
        an event per removal and the open playlist answers those — a dozen model
        rebuilds on the way past.

        Survivors are left where they are. Positions are only ever read through
        ``ORDER BY``, so the gaps this leaves cost nothing; ``set_order`` closes
        them the next time the list is arranged by hand.
        """
        wanted = {int(t) for t in track_ids}
        if not wanted:
            return []
        entries = (
            self.session.query(PlaylistTrack)
            .filter(
                PlaylistTrack.playlist_id == playlist_id,
                PlaylistTrack.track_id.in_(wanted),
            )
            .all()
        )
        if not entries:
            return []
        removed = [entry.track_id for entry in entries]
        for entry in entries:
            self.session.delete(entry)
        self._touch(playlist_id)
        self.session.commit()
        return removed

    def set_order(self, playlist_id: int, track_ids: List[int]) -> bool:
        """Rewrite a playlist's ordering to the given one. Returns True if it changed.

        Renumbers to a dense ``0..N-1`` rather than patching the rows that moved.
        Positions are handed out as ``max+1`` on append and never renumbered on
        removal, so a playlist that has been edited for a while has gaps in it —
        arithmetic that assumed dense input would quietly reorder the wrong rows.
        Rewriting the block outright is also what makes this idempotent.

        Ids that are not in the playlist are ignored, and entries the caller did
        not name keep their relative order *after* the ones it did. Neither
        should happen from the GUI, which always sends the whole list; both are
        cheap insurance against a caller working from a stale copy, and the
        alternative is a playlist with two rows claiming one position.
        """
        entries = (
            self.session.query(PlaylistTrack)
            .filter(PlaylistTrack.playlist_id == playlist_id)
            .order_by(PlaylistTrack.position.asc())
            .all()
        )
        if not entries:
            return False

        by_track = {entry.track_id: entry for entry in entries}
        ordered = []
        seen = set()
        for track_id in track_ids:
            entry = by_track.get(int(track_id))
            if entry is not None and entry.track_id not in seen:
                seen.add(entry.track_id)
                ordered.append(entry)
        ordered.extend(e for e in entries if e.track_id not in seen)

        changed = False
        for position, entry in enumerate(ordered):
            if entry.position != position:
                entry.position = position
                changed = True
        if changed:
            self._touch(playlist_id)
            self.session.commit()
        return changed

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get_tracks(self, playlist_id: int) -> List[Track]:
        """Return a playlist's tracks in order, with artists/genres eager-loaded.

        Mirrors TrackRepository.get_by_ids() eager-loading so the track-list
        ViewModel can build display entries without N+1 queries.
        """
        return (
            self.session.query(Track)
            .join(PlaylistTrack, PlaylistTrack.track_id == Track.id)
            .filter(PlaylistTrack.playlist_id == playlist_id)
            .order_by(PlaylistTrack.position.asc())
            .options(joinedload(Track.artists), joinedload(Track.genres))
            .all()
        )

    def get_track_ids(self, playlist_id: int) -> List[int]:
        """Return just the ordered track ids of a playlist (for play-all)."""
        rows = (
            self.session.query(PlaylistTrack.track_id)
            .filter(PlaylistTrack.playlist_id == playlist_id)
            .order_by(PlaylistTrack.position.asc())
            .all()
        )
        return [r[0] for r in rows]

    def get_summaries(self) -> List[dict]:
        """Return one summary dict per playlist for the playlist grid.

        Each summary carries derived aggregates (track_count, total_duration) and a
        resolved cover hash: the playlist's own cover if set, otherwise the first
        track's artwork. Aggregates are derived here rather than stored, so they are
        always current.

        Returns:
            List of dicts ordered newest-first:
                {id, name, description, cover_hash, track_count, total_duration,
                 created_at, updated_at}
        """
        playlists = (
            self.session.query(Playlist)
            .order_by(Playlist.created_at.desc(), Playlist.id.desc())
            .all()
        )

        summaries: List[dict] = []
        for pl in playlists:
            count, duration = (
                self.session.query(
                    func.count(PlaylistTrack.id),
                    func.coalesce(func.sum(Track.duration_seconds), 0),
                )
                .select_from(PlaylistTrack)
                .join(Track, Track.id == PlaylistTrack.track_id)
                .filter(PlaylistTrack.playlist_id == pl.id)
                .one()
            )

            summaries.append({
                "id": pl.id,
                "name": pl.name,
                "description": pl.description or "",
                "cover_hash": self._resolve_cover_hash(pl),
                "track_count": int(count or 0),
                "total_duration": int(duration or 0),
                "created_at": pl.created_at,
                "updated_at": pl.updated_at,
            })
        return summaries

    def get_cover_hash(self, playlist_id: int) -> Optional[str]:
        """Return one playlist's resolved cover hash, as its summary would.

        For the open playlist's header, which would otherwise have to build
        every playlist's summary to read one cover.
        """
        playlist = self.get_by_id(playlist_id)
        return self._resolve_cover_hash(playlist) if playlist else None

    def _resolve_cover_hash(self, playlist: Playlist) -> Optional[str]:
        """The playlist's own cover if set, otherwise its first track's artwork."""
        if playlist.cover_hash:
            return playlist.cover_hash
        first = (
            self.session.query(Track.cover_hash)
            .join(PlaylistTrack, PlaylistTrack.track_id == Track.id)
            .filter(
                PlaylistTrack.playlist_id == playlist.id,
                Track.cover_hash.isnot(None),
            )
            .order_by(PlaylistTrack.position.asc())
            .first()
        )
        return first[0] if first else None
