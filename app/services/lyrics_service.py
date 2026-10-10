"""Storing and serving a track's lyrics.

The only thing that writes lyrics anywhere, and it writes them **twice**: the
row in ``tracks.db`` first, then the ``LYRICS`` Vorbis comment in the track's
own Opus file. Both copies are deliberate, and which is which matters:

- **The table is the read path.** It is a primary-key lookup on a session the
  app already holds, so showing the playing track's words costs no file open
  on the UI thread and "which tracks have lyrics" is a question that can be
  asked at all.
- **The file is the portable copy.** It is what keeps lyrics with the audio
  when it is copied, exported or read by another player, and it is what the
  table can be rebuilt from — the same property that lets ``covers.db`` be
  disposable.

Two copies of a mutable value is normally a synchronisation problem. What makes
it safe here is that there is exactly one writer and one direction: everything
goes through :meth:`LyricsService.save`, which commits to the database and only
then touches the file, so a half-finished write leaves the table right and the
file stale — never the reverse. See
``docs/architecture/decisions/0008-lyrics-are-mirrored-into-the-database.md``.

It sits in ``app/services/`` rather than in ``app/lyrics/`` for the reason
``docs/guides/add-a-feature.md`` gives: it touches the library, and everything
that writes the library is a service. The ``app.lyrics`` package below it is
pure — parsing, tag reading, network lookup — and knows nothing about SQLAlchemy.

Every write announces itself as ``lyrics.updated`` carrying the track id, so a
view showing that track can reload without polling. Three things reach here:
the importer, stashing what a fetch found; the track editor, applying a
refresh from a source; and the Now Playing tab — for the mic button, for the one-off
copy of existing tags into the table, and for the lyrics editor, which is the
only one of them whose text a person typed. See
``docs/architecture/cross-cutting/events-and-invalidation.md``.
"""
from __future__ import annotations

import logging
from typing import Optional, Sequence

from app.events.event_bus import EventBus
from app.importer.tagging import update_opus_tags
from app.repositories.lyrics_repository import LyricsRepository
from app.utils.library_paths import track_path

logger = logging.getLogger(__name__)

# Where a document came from. Not derivable from the document itself, which is
# why it is a column at all — see :class:`app.models.track.TrackLyrics`.
#
# It also decides, by default, whether a write needs to reach the file: lyrics
# that came *out of* a file tag are already in that file.
SOURCE_TAG = "tag"        # read from the file's own LYRICS comment
SOURCE_SEARCH = "search"  # found by app.lyrics.lookup
SOURCE_MANUAL = "manual"  # written by the user in the Now Playing editor
SOURCES = (SOURCE_TAG, SOURCE_SEARCH, SOURCE_MANUAL)


def _mirrors_to_file(source: str, write_tag: Optional[bool]) -> bool:
    """Whether a write of this *source* also has to reach the Opus file.

    An explicit *write_tag* always wins. Absent one, the answer is "unless it
    came from the file in the first place" — which is not a shortcut but the
    actual rule: SOURCE_TAG means this exact text was read out of that exact
    file, so writing it back could only ever be a no-op.
    """
    if write_tag is not None:
        return write_tag
    return source != SOURCE_TAG


class LyricsService:
    """The library's lyrics, read and written."""

    def __init__(self, repository: LyricsRepository, event_bus: EventBus) -> None:
        self.repository = repository
        self.event_bus = event_bus

    # -- reading --------------------------------------------------------

    def get(self, track_id: int) -> Optional[str]:
        """This track's lyric document, or ``None``.

        Returns the text rather than the row: no caller has any use for the
        ORM object, and handing one out would invite it being walked from
        somewhere it should not be.
        """
        if not track_id or track_id <= 0:
            return None
        row = self.repository.get(int(track_id))
        return row.content if row is not None else None

    def track_ids_without_lyrics(self) -> list[int]:
        """Every track that has no lyrics stored. What the tag sweep works from."""
        return self.repository.track_ids_without_lyrics()

    # -- writing --------------------------------------------------------

    def save(
        self, track_id: int, content: str, source: str, *,
        write_tag: Optional[bool] = None,
    ) -> bool:
        """Store *content* as this track's lyrics: database, then file, then say so.

        The order is the invariant. The row commits first and the file is
        rewritten after, so an interruption or a locked file leaves the library
        holding lyrics whose file has not caught up — which the next write
        fixes and which nothing reads incorrectly in the meantime. The reverse
        order would leave a file carrying words the library does not know about.

        Empty content is a no-op rather than a delete: every caller reaches
        here holding what a lookup or a tag read returned, and "nothing was
        found" must not wipe lyrics that are already there. Use :meth:`delete`
        to actually remove them.

        *write_tag* defaults to what *source* implies — a document that came out
        of the file's own tag is already in that file, and re-writing it would
        be a pointless rewrite of the container. Pass it explicitly only when
        that inference is wrong; :func:`app.importer.stash.finalize_and_stash`
        is the one case, because its audio is still in a temporary directory and
        gets its whole tag block written a moment later.

        Returns:
            True if the row was written. The file write is best effort and does
            **not** change this: the database is the read path, so a track whose
            file could not be retagged still shows its lyrics everywhere in the
            app.
        """
        if not track_id or track_id <= 0:
            return False
        if not content or not content.strip():
            return False
        if source not in SOURCES:
            raise ValueError(f"Unknown lyrics source: {source!r}")

        track_id = int(track_id)
        self.repository.set(track_id, content, source)

        if _mirrors_to_file(source, write_tag):
            self._write_tag(track_id, content)

        self.event_bus.emit("lyrics.updated", track_id)
        return True

    def save_many(
        self, entries: Sequence[tuple], source: str, *,
        write_tag: Optional[bool] = None,
    ) -> int:
        """Store a batch of ``(track_id, content)`` pairs in one commit.

        The write path for the one-off copy of existing tags into the table,
        which is also why the *write_tag* default matters here: that batch came
        out of the files it would otherwise be written back to.

        One event per track still, because a subscriber cares about the one id
        it is showing and a batch event would make every subscriber unpack a
        list to find out it was not in it.

        Returns:
            How many rows were written.
        """
        if source not in SOURCES:
            raise ValueError(f"Unknown lyrics source: {source!r}")

        usable = [
            (int(track_id), content)
            for track_id, content in entries
            if track_id and int(track_id) > 0 and content and content.strip()
        ]
        if not usable:
            return 0

        written = self.repository.set_many(usable, source)

        if _mirrors_to_file(source, write_tag):
            for track_id, content in usable:
                self._write_tag(track_id, content)

        for track_id, _content in usable:
            self.event_bus.emit("lyrics.updated", track_id)
        return written

    def delete(self, track_id: int) -> bool:
        """Remove a track's lyrics, from the table and from the file.

        Same order as :meth:`save`, for the same reason.
        """
        if not track_id or track_id <= 0:
            return False
        track_id = int(track_id)
        removed = self.repository.remove(track_id)
        if removed:
            # An empty value *removes* the comment rather than writing a blank
            # one — see `update_opus_tags`.
            self._write_tag(track_id, "")
            self.event_bus.emit("lyrics.updated", track_id)
        return removed

    def _write_tag(self, track_id: int, content: str) -> bool:
        """Mirror *content* into the track's Opus file. Never raises.

        Best effort, and it has to be. On Windows the audio backend holds the
        playing track's file open, and the playing track is exactly the one the
        mic button targets — so a refused retag is an ordinary occurrence, not
        an error worth costing the user their lyrics over. The row is already
        committed by the time this runs, and the row is what the app reads.

        A merge rather than a rewrite: ``update_opus_tags`` only touches the
        keys it is handed, so the embedded artwork, the provenance tags and
        everything else in the block survive.
        """
        path = track_path(track_id)
        if not path.exists():
            logger.debug("No file to write lyrics into for track %s", track_id)
            return False
        try:
            update_opus_tags(path, {"LYRICS": content})
            return True
        except Exception as exc:  # noqa: BLE001 — the database write already stands
            logger.warning(
                "Could not write the LYRICS tag for track %s: %s", track_id, exc
            )
            return False
