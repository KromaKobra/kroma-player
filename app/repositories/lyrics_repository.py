"""Lyrics repository - data access for the TrackLyrics model.

Responsibilities:
- Read one track's lyrics by id (a primary-key lookup, see the model)
- Upsert a document, replacing whatever was there
- Answer which tracks have no lyrics at all

Design:
- Inherits generic CRUD from BaseRepository[TrackLyrics], but overrides nothing
  that assumes an ``id`` column — ``get_by_id``/``delete`` on the base filter by
  ``id``, which this table does not have. Use ``get``/``remove`` below instead.

Relationship to other modules:
- Used by LyricsService, which is the only thing that writes here
- ``track_ids_without_lyrics()`` is what the tag sweep in app/lyrics/workers.py
  works from: it is the whole list of tracks whose files still have to be looked
  at, and it shrinks as the sweep writes.
"""
from typing import List, Optional

from sqlalchemy.orm import Session

from app.models.track import Track, TrackLyrics
from app.repositories.base_repository import BaseRepository


class LyricsRepository(BaseRepository[TrackLyrics]):
    """Repository for one-row-per-track lyric documents."""

    def __init__(self, session: Session):
        """Initialize lyrics repository.

        Args:
            session: SQLAlchemy session for database access
        """
        super().__init__(session, TrackLyrics)

    def get(self, track_id: int) -> Optional[TrackLyrics]:
        """Get the lyrics row for a track, or None.

        Args:
            track_id: Primary key of the track (and of the lyrics row)

        Returns:
            The row if the track has lyrics, None otherwise
        """
        return (
            self.session.query(TrackLyrics)
            .filter(TrackLyrics.track_id == track_id)
            .first()
        )

    def set(self, track_id: int, content: str, source: str) -> TrackLyrics:
        """Store *content* as this track's lyrics, replacing anything there.

        An upsert rather than an insert because the second set of lyrics for a
        track is always a correction of the first — a search result replacing
        what a file's tag had, most often — and there is nowhere to put both.

        Args:
            track_id: The track these lyrics belong to
            content: An LRC document or plain text
            source: ``'tag'`` or ``'search'`` — see LyricsService

        Returns:
            The stored row.
        """
        row = self.get(track_id)
        if row is None:
            row = TrackLyrics(track_id=track_id, content=content, source=source)
            self.session.add(row)
        else:
            row.content = content
            row.source = source
        self.session.commit()
        return row

    def set_many(self, entries: List[tuple], source: str) -> int:
        """Store many ``(track_id, content)`` pairs in one commit.

        What the tag sweep writes through. Two hundred separate commits per
        batch would be two hundred fsyncs on the session every other writer
        shares; one is the same work to the same rows.

        Returns:
            How many rows were written.
        """
        written = 0
        for track_id, content in entries:
            row = self.get(track_id)
            if row is None:
                self.session.add(
                    TrackLyrics(track_id=track_id, content=content, source=source)
                )
            else:
                row.content = content
                row.source = source
            written += 1
        if written:
            self.session.commit()
        return written

    def remove(self, track_id: int) -> bool:
        """Delete a track's lyrics.

        Named ``remove`` rather than overriding ``delete``: the base class's
        ``delete(id)`` looks the row up by an ``id`` column this table does not
        have, and shadowing it with a different lookup would make the two
        spellings mean subtly different things.

        Returns:
            True if a row was deleted, False if the track had none.
        """
        row = self.get(track_id)
        if row is None:
            return False
        self.session.delete(row)
        self.session.commit()
        return True

    def track_ids_without_lyrics(self) -> List[int]:
        """Every track id with no lyrics row, ascending.

        A LEFT JOIN rather than a ``NOT IN`` subquery so it stays one indexed
        pass over the two tables however big the library gets.
        """
        rows = (
            self.session.query(Track.id)
            .outerjoin(TrackLyrics, TrackLyrics.track_id == Track.id)
            .filter(TrackLyrics.track_id.is_(None))
            .order_by(Track.id)
            .all()
        )
        return [row[0] for row in rows]
