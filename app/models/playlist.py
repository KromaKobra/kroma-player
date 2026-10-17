"""SQLAlchemy ORM models for user playlists.

Playlists are stored in the same database as tracks (tracks.db) so they share the
library's referential integrity, transactions, and portable-folder layout. There is
deliberately no separate playlists file — splitting the database would only add
session-management complexity for zero gain.

Model hierarchy:
- Playlist: a named, ordered collection of tracks.
- PlaylistTrack: the ordered join row linking a playlist to one track at a given
  ``position``. This is a first-class table (not a bare association table) because
  it carries the explicit ordering column and lets the same track appear at a known
  spot without gap-shuffling tricks.

Design notes:
- Tracks are referenced by id only. Track metadata always lives on Track — a
  playlist never copies it.
- The foreign keys use ``ondelete="CASCADE"`` so that deleting a Playlist removes
  its PlaylistTrack rows, and deleting a Track removes it from every playlist it
  was in. SQLite enforces this because the connection sets ``PRAGMA foreign_keys=ON``
  (see app/database/connection.py).
- These models attach to the same declarative ``Base`` as Track, so
  ``create_schema()`` creates their tables automatically.
"""
from datetime import datetime
from time import time_ns

from sqlalchemy import Column, Integer, String, ForeignKey, Index
from sqlalchemy.orm import relationship

from app.models.track import Base


def _today_int() -> int:
    """Return today's date as a YYYYMMDD integer (matches Track.created_at)."""
    return int(datetime.utcnow().strftime("%Y%m%d"))


def _now_us() -> int:
    """UTC Unix time in microseconds, so same-day edits sort independently."""
    return time_ns() // 1_000


class Playlist(Base):
    """Playlist model - a named, ordered collection of tracks.

    A playlist holds only presentation metadata (name, description, optional cover)
    plus an ordered set of track references via :class:`PlaylistTrack`. Aggregate
    values such as track count and total duration are derived at query time rather
    than denormalized here, so they can never go stale.

    Attributes:
        id: Primary key.
        name: User-visible playlist name (required).
        description: Optional freeform notes.
        cover_hash: Optional content address into the cover cache (covers.db). When
            null, the service derives artwork from the first track (under four
            tracks) or the first four tracks as a grid. Generated references live
            in covers.db, keeping this field reserved for custom artwork.
        created_at: Creation date as a YYYYMMDD integer.
        updated_at: Last modification as Unix microseconds, used for sorting.

    Relationships:
        entries: ordered PlaylistTrack rows (ordered by position). Deleting the
        playlist cascades to its entries (DB-level ON DELETE CASCADE).
    """
    __tablename__ = 'playlists'

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(255), nullable=False)
    description = Column(String)
    cover_hash = Column(String(32))
    created_at = Column(Integer, default=_today_int)
    updated_at = Column(Integer, default=_now_us)

    entries = relationship(
        'PlaylistTrack',
        back_populates='playlist',
        order_by='PlaylistTrack.position',
        cascade='all, delete-orphan',
        passive_deletes=True,
    )

    def __repr__(self):
        return f"<Playlist(id={self.id}, name='{self.name}')>"

    def to_dict(self) -> dict:
        """Convert playlist to a JSON-serializable summary (counts excluded)."""
        return {
            'id': self.id,
            'name': self.name,
            'description': self.description or '',
            'cover_hash': self.cover_hash,
            'created_at': self.created_at,
            'updated_at': self.updated_at,
        }


class PlaylistTrack(Base):
    """Ordered join row linking a playlist to a single track.

    The ``position`` column is the key design choice: it makes fetching a playlist
    a simple ``ORDER BY position`` and lets entries be reordered without renumbering
    everything. A track may appear in many playlists; within one playlist each track
    appears at most once (enforced by the repository, which de-dupes on add).

    Storage:
        - playlist_id / track_id are foreign keys with ON DELETE CASCADE, so the
          row disappears automatically when either side is deleted.
        - A composite index on (playlist_id, position) keeps ordered reads fast.
    """
    __tablename__ = 'playlist_tracks'

    id = Column(Integer, primary_key=True, autoincrement=True)
    playlist_id = Column(
        Integer, ForeignKey('playlists.id', ondelete='CASCADE'), nullable=False
    )
    track_id = Column(
        Integer, ForeignKey('tracks.id', ondelete='CASCADE'), nullable=False
    )
    position = Column(Integer, nullable=False)

    playlist = relationship('Playlist', back_populates='entries')
    track = relationship('Track')

    __table_args__ = (
        Index('ix_playlist_tracks_playlist_position', 'playlist_id', 'position'),
    )

    def __repr__(self):
        return (
            f"<PlaylistTrack(playlist_id={self.playlist_id}, "
            f"track_id={self.track_id}, position={self.position})>"
        )
