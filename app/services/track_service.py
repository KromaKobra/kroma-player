"""Track service - business logic layer.

The TrackService orchestrates all track-related operations. It sits between the
UI layer and the data layer (repositories), adding:
- Business logic validation
- Event emission for state changes
- Logging and error handling
- Artist/genre resolution during track creation

All database access goes through repositories. This service handles:
- CRUD operations on tracks
- Relationship resolution (artists, genres) during creation
- Event emission for all state changes

Relationship:
   Downloader/UI ← TrackService ← TrackRepository/ArtistRepository/GenreRepository ← SQLite
"""
from typing import List, Mapping, NamedTuple, Optional, Any, Dict, Sequence
from datetime import datetime
from pathlib import Path
from app.library.recording_match import (
    best_match,
    duration_window,
    recording_of,
)
from app.models.track import Album, Artist, Genre, Track
# The other half of "is this the same recording" — the id half, which is asked
# first. Kept with the id store because it reads ids by kind; imported here
# so both halves are applied in one place.
from app.plugins import external_ids
from app.repositories.track_repository import TrackRepository
from app.repositories.album_repository import AlbumRepository
from app.repositories.artist_repository import ArtistRepository
from app.repositories.genre_repository import GenreRepository
from app.events.event_bus import EventBus
from app.artwork.cover_store import CoverStore
from app.importer.core_tags import CORE_TAGS
from app.importer.tagging import update_opus_tags
import logging

logger = logging.getLogger(__name__)

# Model attribute → Vorbis comment, for mirroring an edit into the audio file.
# Only the fields the editor can change are here; the rest of a file's tags are
# left exactly as the fetch or import path wrote them. External ids are not
# fields: which of them reach the file, and under what name, is each kind's
# `writeTag` — see `TrackService.id_tags`.
_FIELD_TO_TAG: Dict[str, str] = {
    'title':          'TITLE',
    'artists':        'ARTIST',
    'album':          'ALBUM',
    'release_date':   'DATE',
    'genres':         'GENRE',
    'musical_key':    'KEY',
    'bpm':            'BPM',
    'isrc':           'ISRC',
    'copyright_text': 'COPYRIGHT',
}


class MetadataUpdate(NamedTuple):
    """The outcome of an edit: the saved track, plus any non-fatal complaint.

    ``warning`` is empty on a clean save. A non-empty warning still means the
    database write succeeded — only the file's tags did not follow.
    """
    track: Optional[Track]
    warning: str


class TrackService:
    """Orchestrates track operations with event emission and logging.

    Responsibilities:
    - Wraps repository CRUD operations
    - Emits events on state changes (track added/updated/deleted)
    - Resolves artists and genres during track creation
    - Provides logging for debugging
    - Single entry point for all track operations
    """

    def __init__(self, track_repo: TrackRepository, artist_repo: ArtistRepository,
                 genre_repo: GenreRepository, event_bus: EventBus,
                 cover_store: Optional[CoverStore] = None,
                 backing_service=None,
                 album_repo: Optional[AlbumRepository] = None,
                 id_kinds: Any = None):
        """Initialize with repository and event bus dependencies.

        ``backing_service`` is optional and untyped to avoid an import cycle —
        it is only used to drop a deleted track's instrumental from the
        availability cache.

        ``album_repo`` is optional and defaults to one built on the same session,
        rather than being required: this constructor is called from a dozen tests
        that predate albums entirely, and none of them has an opinion about
        album resolution. Passing one explicitly is what bootstrap does, so the
        application shares a single repository per session.

        ``id_kinds`` answers ``declarations(entity)`` and
        ``declaration(entity, key)`` — the provider registry, in the app. It is
        what says whether a kind of track id is unique and which tag it is
        mirrored into. Without one, every kind is unique and nothing reaches a
        file, which is what a library with no plugin enabled should get.
        """
        self.track_repo = track_repo
        self.artist_repo = artist_repo
        self.genre_repo = genre_repo
        self.event_bus = event_bus
        self.cover_store = cover_store
        self.backing_service = backing_service
        self.album_repo = album_repo or AlbumRepository(track_repo.session)
        self._id_kinds = id_kinds

    def id_unique(self, key: str) -> bool:
        """Whether a track id of kind *key* may belong to one track only.

        Unique unless an enabled plugin declares it otherwise — see
        :attr:`app.plugins.manifest.IdKind.unique`.
        """
        kind = (
            self._id_kinds.declaration(external_ids.TRACK, key)
            if self._id_kinds is not None else None
        )
        return kind.unique if kind is not None else True

    def id_tags(self) -> Dict[str, str]:
        """``{key: Vorbis comment}`` for every track kind mirrored into the file.

        From the declarations of enabled plugins, so turning a plugin off stops
        its ids reaching new files without taking them off old ones. A comment
        the core writes itself is never offered, whatever a manifest says.
        """
        if self._id_kinds is None:
            return {}
        return {
            kind.key: kind.write_tag
            for kind in self._id_kinds.declarations(external_ids.TRACK)
            if kind.write_tag and kind.write_tag not in CORE_TAGS
        }

    def get_library_stats(self) -> dict:
        """Return track count, id holes, and file-size totals for the Database tab."""
        import os
        from app.utils.library_paths import track_path, library_root

        stats = self.track_repo.get_stats()

        total_bytes = 0
        for track_id in self.track_repo.get_all_ids():
            try:
                total_bytes += os.path.getsize(track_path(track_id))
            except OSError:
                pass

        vault_bytes = 0
        try:
            vault_bytes = sum(
                f.stat().st_size
                for f in library_root().rglob("*")
                if f.is_file()
            )
        except OSError:
            pass

        stats["track_bytes"] = total_bytes
        stats["vault_bytes"] = vault_bytes
        stats["vault_path"]  = str(library_root())
        return stats

    def get_all_tracks(self) -> List[Track]:
        """Get all tracks (paginated)."""
        return self.track_repo.get_all()

    def get_tracks_by_ids(self, ids: List[int]) -> List[Track]:
        """Bulk-fetch tracks by id (used for display_data backfill)."""
        return self.track_repo.get_by_ids(ids)

    def get_track_by_id(self, track_id: int) -> Optional[Track]:
        """Fetch a track by ID."""
        return self.track_repo.get_by_id(track_id)

    def track_id_for(self, key: str, value: str, *, exclude_id: Optional[int] = None) -> Optional[int]:
        """The track holding *value* under id kind *key*, other than *exclude_id*.

        What an editor asks before saving an id, so it can name the other track
        rather than silently not writing — see `external_ids.conflict`.
        """
        holder = external_ids.conflict(
            self.track_repo.session, external_ids.TRACK, key, value, exclude_id=exclude_id
        )
        return int(holder.id) if holder is not None else None

    def find_owned(
        self, descriptions: Sequence[Mapping[str, Any]]
    ) -> List[Optional[int]]:
        """Which library track each description names — positionally, or None.

        The one answer to "does the library already have this recording?", asked
        by the import list, by the album importer and by the online search
        results. It takes core row dicts (see
        :func:`app.plugins.providers.row_from_protocol`) and returns a list as
        long as the one it was given, so a caller pairs the answers back up by
        index.

        **Two passes, and the order is the argument.** An id is evidence; a
        resemblance is a guess, and a guess must never overrule evidence:

        1. Every id that names each description —
           :func:`external_ids.lookup_values`, so a release row is asked about
           under its own id and every alternate — resolved through
           :func:`external_ids.lookup`, which every other ownership question
           runs on too. One query per kind, however many rows.
        2. Only for what pass 1 left: the *recording* — title, duration, artist
           — through :func:`same_recording`. This is what recognises a track
           downloaded from a different release of the same record, which is the
           ordinary case and which no id can reach. Two more queries, again
           however many rows: one for the candidates at those durations, one for
           their credits.

        A description that resolves to nothing is ``None``, which is the honest
        answer and the one that lets a caller offer the download.
        """
        rows = list(descriptions)
        owned: List[Optional[int]] = [None] * len(rows)
        if not rows:
            return owned

        owned = external_ids.lookup_rows(self.track_repo.session, rows)
        self._match_owned_recordings(rows, owned)
        return owned

    def _match_owned_recordings(
        self, rows: Sequence[Mapping[str, Any]], owned: List[Optional[int]]
    ) -> None:
        """Pass 2 of :meth:`find_owned`, filling *owned* in place.

        Kept separate because it is the half that costs queries and the half
        that can be wrong. It runs only over the rows no id answered for, and it
        asks the database only about lengths those rows could actually be — so a
        run where every id landed does no work at all and touches nothing.
        """
        # Only the rows an id did not answer for, and only the ones with enough
        # to compare. A row missing any of the three could not match anyway —
        # `same_recording` fails closed on each — and dropping it here is what
        # keeps an untimed row from widening the query below to the whole
        # library before failing.
        wanted: Dict[int, Any] = {}
        for index, track_id in enumerate(owned):
            if track_id is not None:
                continue
            row = rows[index]
            recording = recording_of(
                row.get("title"), row.get("artist"), row.get("duration")
            )
            if recording.key and recording.duration and recording.artists:
                wanted[index] = recording
        if not wanted:
            return

        lengths = {
            seconds
            for recording in wanted.values()
            for seconds in duration_window(recording.duration)
        }
        found = self.track_repo.tracks_at_durations(lengths)
        if not found:
            return

        # Credits come second and only for what survived the length filter,
        # which is what keeps this off the whole library: a join per candidate
        # would be the expensive half of a question that is usually answered
        # "no".
        artists = self.track_repo.fetch_artists(list(found))
        library = {
            track_id: recording_of(title, artists.get(track_id, ""), seconds)
            for track_id, (title, seconds) in found.items()
        }

        for index, recording in wanted.items():
            owned[index] = best_match(recording, library)

    def add_track(self, track: Track) -> Track:
        """Create a new track and emit event."""
        added = self.track_repo.create_entry(track)
        self.event_bus.emit('track.added', added)
        logger.info(f"Track added: {added.title} (ID: {added.id})")
        return added

    def create_track_with_relationships(self, track_info: Dict[str, Any]) -> int:
        """Create a track with artist and genre resolution.

        This is the primary method for track creation during download/import.
        It handles:
        1. Creating or retrieving artists (with deduplication)
        2. Creating or retrieving genres (with deduplication)
        3. Creating the track with relationships
        4. Emitting events
        5. Returning the track ID

        Args:
            track_info: Dictionary with track metadata:
                - title: Track title
                - album: Album name (optional)
                - duration_seconds: Duration in seconds
                - release_date: Release date (optional)
                - musical_key: Musical key (optional)
                - bpm: BPM (optional)
                - external_ids: ``{"provider:kind": value}`` (optional)
                - isrc: ISRC code (optional)
                - copyright_text: Copyright info (optional)
                - artist: Comma-separated artist names (optional)
                - genre: Single genre name (optional)

        Returns:
            The newly created track's ID
        """
        # Create Track object (without relationships yet)
        track = Track(
            title=track_info.get('title', 'Unknown'),
            duration_seconds=track_info.get('duration_seconds', 0),
            release_date=track_info.get('release_date'),
            musical_key=track_info.get('musical_key'),
            bpm=track_info.get('bpm'),
            isrc=track_info.get('isrc'),
            copyright_text=track_info.get('copyright_text', ''),
            cover_hash=track_info.get('cover_hash'),
        )

        # Attach early so relationship appends are tracked during autoflush.
        self.track_repo.session.add(track)

        # Whatever ids the source knew. A new row has none, so nothing is
        # overwritten — but a unique id another track already holds is still
        # refused, because two rows answering to one id would make every
        # "already in your library" question answer arbitrarily.
        for key, value in (track_info.get('external_ids') or {}).items():
            external_ids.write(
                self.track_repo.session, external_ids.TRACK, track, key, value,
                fill_only=True, unique=self.id_unique(key),
            )

        # Resolve and add artists. `artist_refs` wins when the source knew it —
        # see _resolve_artists for why the string is the lossy path.
        credited = self._resolve_artists(
            track_info.get('artists'), refs=track_info.get('artist_refs')
        )
        track.artists.extend(credited)

        # Resolve and add genres (accepts a single 'genre' or comma-separated 'genres')
        track.genres.extend(
            self._resolve_genres(track_info.get('genres') or track_info.get('genre'))
        )

        # The order `credited` is already in is the credit order — `artist_refs`
        # carries the source's own, and the comma string carries what was typed.
        # This is where it stops being thrown away: the association rows have to
        # exist before they can be numbered, so flush, number, then commit.
        self.track_repo.session.flush()
        self._write_artist_order(track)

        # The album is resolved *after* the artists, and it has to be: an album
        # is identified by its name together with the artist it belongs to, so
        # there is nothing to look it up by until the credits exist. This is also
        # why it comes after the flush — `credited` needs ids.
        self._apply_albums(track, track_info.get('album'))

        # Create the track and emit event
        created = self.track_repo.create_entry(track)
        self.event_bus.emit('track.added', created)
        logger.info(f"Track created with relationships: {created.title} (ID: {created.id})")

        return created.id # type: ignore

    def _resolve_artists(
        self, names: Optional[str], refs: Optional[List[Dict[str, str]]] = None
    ) -> List[Artist]:
        """Turn artist metadata into rows, creating what is new.

        Two inputs for one job, in order of preference:

        ``refs`` is the structured form — ``[{"name", "external_ids",
        "image_hash"}]`` — as a provider gave it, with the portrait hash added by
        ``app.importer.artist_enrichment`` on the way in. It is preferred because
        it has not been through a string: no separator to guess at, so a group
        whose name contains a comma survives, and each artist arrives already
        carrying its identity and its picture. Both of the latter are optional;
        an empty one means "not known here", never "this artist has none".

        ``names`` is the comma-separated fallback, used for hand edits, CSV rows
        and tag-only imports, none of which offer anything better. It splits on a
        bare comma and nothing else, deliberately: "/" would tear "AC/DC" in half
        every time it was written, and a wrong split is far more work to undo than
        a missed one. Names that really are compound are taken apart on request by
        ArtistService.split, where the user confirms the result.
        """
        if refs:
            resolved: List[Artist] = []
            seen: set[int] = set()
            for ref in refs:
                name = (ref.get("name") or "").strip()
                if not name:
                    continue
                artist = self.artist_repo.get_or_create(name)
                # Identify the row while we have the ids, but never overwrite
                # one that is already set, and never take one another artist
                # holds — `fill_only` and the uniqueness check, both inside
                # `external_ids.write`.
                ref_ids = ref.get("external_ids") or {}
                for key, value in ref_ids.items():
                    external_ids.write(
                        self.artist_repo.session, external_ids.ARTIST, artist,
                        key, value, fill_only=True,
                    )
                # The portrait, when the caller went and got one — see
                # `app.importer.artist_enrichment`, which does the network before
                # this transaction opens because this one must not.
                #
                # Same backfill rule the id above follows: fill a gap, never
                # overwrite. Nothing is replaced, so no hash is ever retired
                # here and this path stays clear of ArtistService._retire_hashes.
                image_hash = (ref.get("image_hash") or "").strip()
                if image_hash and not artist.image_hash:
                    artist.image_hash = image_hash
                if int(artist.id) not in seen:
                    seen.add(int(artist.id))
                    resolved.append(artist)
                logger.debug("Resolved artist: %s (%s)", name, ref_ids or "no id")
            if resolved:
                return resolved

        if not names:
            return []
        resolved = []
        for name in (n.strip() for n in names.split(',')):
            if not name:
                continue
            resolved.append(self.artist_repo.get_or_create(name))
            logger.debug(f"Resolved artist: {name}")
        return resolved

    def _apply_albums(self, track: Track, name: Optional[str]) -> None:
        """Put *track* on the album called *name*, as its primary.

        The album counterpart of ``_resolve_artists``, and much smaller because
        the input is much poorer: a tag, a CSV cell or an editor field carries
        one album name and no id, so there is exactly one album to resolve and it
        is by definition the one the track belongs to.

        What that means in practice, and it is the subtle part: setting the album
        makes it **primary**, but does not evict the compilations. A track edited
        to say "Discovery" and already on two compilations comes out on all three
        with Discovery first. Anything else would make a routine metadata fix
        silently drop memberships the user never mentioned.

        An empty name removes the track's primary album only, promoting whatever
        was next. Clearing the field means "this is not on that record", not
        "this is on nothing".
        """
        if track.id is None:
            return
        cleaned = (name or "").strip()
        current = list(track.albums)

        if not cleaned:
            if not current:
                return
            # The album it is leaving is a track shorter than it was, so it can
            # no longer claim to be the whole release. The assignment below goes
            # through the ORM relationship rather than through AlbumRepository,
            # which is why the clear has to be said here — see `clear_complete`.
            self.album_repo.clear_complete([int(current[0].id)])
            track.albums = current[1:]
            self.track_repo.session.flush()
            self._write_album_order(track)
            return

        lead = int(track.artists[0].id) if track.artists else None
        album = self.album_repo.get_or_create(cleaned, lead)
        self.track_repo.session.flush()

        others = [a for a in current if int(a.id) != int(album.id)]
        # Only a track genuinely joining is a membership change. Re-saving a
        # track whose album has not moved must not knock the flag off the record
        # it is already on.
        if int(album.id) not in {int(a.id) for a in current}:
            self.album_repo.clear_complete([int(album.id)])
        track.albums = [album] + others
        self.track_repo.session.flush()
        self._write_album_order(track)
        # A brand-new album has no artwork of its own yet, and the track that
        # just joined it is the only evidence available. An album that has been
        # identified already holds the release's real cover and is left alone —
        # see the Album model for the order of preference.
        if (not album.cover_hash and track.cover_hash
                and not external_ids.is_identified(external_ids.ALBUM, album)):
            album.cover_hash = track.cover_hash
        # The album's running order is the album's business, but a track that has
        # just arrived has to land somewhere; put it last.
        self.album_repo.set_order(
            int(album.id), self.album_repo.track_ids(int(album.id))
        )

    def _apply_album_refs(self, track: Track, refs: Any) -> None:
        """Put *track* on exactly these albums, in this order, ``refs[0]`` primary.

        The multi-album sibling of :meth:`_apply_albums`, and the difference
        between them is the difference between a tag and a chip field. A tag
        carries one album name, so ``_apply_albums`` can only say "this is the
        record it belongs to" and must leave the compilations alone — evicting
        memberships the user never mentioned would make a routine metadata fix
        destructive. A chip field shows the whole list, so the list *is* the
        answer: what is not in it was taken out on purpose.

        Each ref is ``{"id", "name"}``. An id resolves a row and cannot drift; an
        id of 0 is a name the user typed that matched nothing, and is created —
        by name **and lead artist**, which is why this takes refs rather than the
        bare ids the artist path is happy with. Two records really are both
        called "Greatest Hits", and ``get_or_create`` needs the artist to tell
        them apart.
        """
        if track.id is None:
            return

        lead = int(track.artists[0].id) if track.artists else None
        resolved: List[Album] = []
        seen: set[int] = set()
        for ref in refs or []:
            album = self._album_from_ref(ref, lead)
            if album is None:
                continue
            self.track_repo.session.flush()
            if int(album.id) not in seen:
                seen.add(int(album.id))
                resolved.append(album)

        before = {int(a.id) for a in track.albums}
        # Both sides of the change: the records this track is joining and the
        # ones it is leaving. Either way that album is now a different length
        # from the one that was checked against the release, so the flag goes.
        # The symmetric difference and not the union — an album the track was on
        # before and is still on has not changed size.
        self.album_repo.clear_complete(before ^ seen)
        track.albums = resolved
        self.track_repo.session.flush()
        self._write_album_order(track)

        for album in resolved:
            if int(album.id) in before:
                continue
            # A brand-new album has no artwork of its own yet, and the track that
            # just joined it is the only evidence available. Same rule and same
            # order of preference as _apply_albums.
            if (not album.cover_hash and track.cover_hash
                    and not external_ids.is_identified(external_ids.ALBUM, album)):
                album.cover_hash = track.cover_hash
            # A track that has just arrived has to land somewhere in the album's
            # running order; set_order numbers it densely onto the end.
            self.album_repo.set_order(
                int(album.id), self.album_repo.track_ids(int(album.id))
            )

        # And an album the track just left has a gap where it used to sit. Its
        # running order is the album's own business, but nobody else is going to
        # close that gap.
        for album_id in before - seen:
            self.album_repo.set_order(
                album_id, self.album_repo.track_ids(album_id)
            )

    def _album_from_ref(self, ref: Any, lead: Optional[int]) -> Optional[Album]:
        """One ``{"id", "name"}`` as a row, or None when it names nothing."""
        if not isinstance(ref, dict):
            return None
        try:
            album_id = int(ref.get("id") or 0)
        except (TypeError, ValueError):
            album_id = 0
        if album_id > 0:
            return self.album_repo.get_by_id(album_id)
        name = str(ref.get("name") or "").strip()
        if not name:
            return None
        return self.album_repo.get_or_create(name, lead)

    def _write_album_order(self, track: Track) -> None:
        """Pin which of *track*'s albums is its primary.

        The same trick, and the same one-moment-only caveat, as
        :meth:`_write_artist_order`: the collection is a plain list right after
        assignment and still holds the order meant, which is the only place that
        order exists until it is written.
        """
        if track.id is None:
            return
        self.track_repo.write_album_order(
            int(track.id), [int(a.id) for a in track.albums]
        )

    def sweep_orphan_albums(self) -> int:
        """Delete albums no track is on, reclaiming their covers."""
        deleted, retired = self.album_repo.delete_orphans()
        if retired:
            self.vacuum_covers(retired)
        if deleted:
            logger.info("Removed %d album(s) no track is on any more", deleted)
        return deleted

    def _artists_by_id(self, ids: Any) -> List[Artist]:
        """Resolve an explicit list of artist ids to rows, dropping any that are gone.

        The id path exists because the name path cannot tell "the artist I picked
        from the list" from "a new artist who happens to be spelled almost like an
        existing one". ``get_or_create`` matches names exactly, so a track saved as
        "daft punk" mints a second row beside "Daft Punk" and neither has the whole
        catalogue. An id says which row was meant and cannot drift.
        """
        if not ids:
            return []
        resolved: List[Artist] = []
        seen: set[int] = set()
        for raw in ids:
            try:
                artist_id = int(raw)
            except (TypeError, ValueError):
                continue
            if artist_id in seen:
                continue
            artist = self.artist_repo.get_by_id(artist_id)
            if artist is not None:
                seen.add(artist_id)
                resolved.append(artist)
        return resolved

    def _write_artist_order(self, track: Track) -> None:
        """Pin the credit order of the artists currently on *track*.

        The collection is a plain Python list until it is loaded back, so right
        after an assignment it still holds the order the caller meant — and this
        is the only moment that order exists anywhere. Read it here, before the
        session has any reason to refresh from the database and re-sort by the
        column that has not been written yet.
        """
        if track.id is None:
            return
        self.track_repo.write_artist_order(
            int(track.id), [int(a.id) for a in track.artists]
        )

    def sweep_orphan_artists(self) -> int:
        """Delete artists no track credits, reclaiming their portraits.

        Returns how many rows went. Safe to call after any reassignment: it counts
        before it deletes, so an artist that merely lost one track of several stays.
        """
        deleted, retired = self.artist_repo.delete_orphans()
        if retired:
            self.vacuum_covers(retired)
        if deleted:
            logger.info("Removed %d artist(s) no track credits any more", deleted)
        return deleted

    def _resolve_genres(self, names: Optional[str]) -> List[Genre]:
        """Turn a comma-separated genre string into rows, creating what is new."""
        if not names:
            return []
        resolved = []
        for name in (n.strip() for n in names.split(',')):
            if not name:
                continue
            resolved.append(self.genre_repo.get_or_create(name))
            logger.debug(f"Resolved genre: {name}")
        return resolved

    def update_track(self, track_id: int, **kwargs) -> Optional[Track]:
        """Update track metadata and emit event."""
        track = self.track_repo.update(track_id, **kwargs)
        if track:
            self.event_bus.emit('track.updated', track)
            logger.info(f"Track updated: {track.title} (ID: {track.id})")
        return track

    def update_track_metadata(
        self,
        track_id: int,
        changes: Dict[str, Any],
        *,
        extra_tags: Optional[Dict[str, Any]] = None,
        picture_block: Optional[str] = None,
    ) -> MetadataUpdate:
        """Apply an edit to one track: database first, then the file's tags.

        ``changes`` is keyed by model attribute (``title``, ``album``,
        ``release_date``, ``musical_key``, ``bpm``, ``isrc``,
        ``copyright_text``) plus the two relationship strings ``artists`` and
        ``genres``, which are comma-separated names and replace the track's
        existing sets outright.

        External ids travel under one more key, ``external_ids``: a
        ``{"provider:kind": value}`` map of only the kinds that changed. An
        empty value clears that id — the one place in the application allowed
        to, because this is a person's edit — and a unique id another track
        holds is not written; the editor checks that first and says so. Kinds
        whose declaration names a ``writeTag`` follow into the file.

        Two keys say the same thing more precisely than a name can, and a caller
        that has them should send them instead: ``artist_ids`` is the credits by
        row, and ``album_refs`` is the **whole** album list as
        ``[{"id", "name"}]`` with the primary first. ``album`` sets only the
        primary and never removes a membership; ``album_refs`` replaces the list.

        The database is the source of truth and commits first; retagging the
        Opus file is best effort. On Windows the playing track's file is held
        open by the audio backend, so a save on the track you are listening to
        can be refused — that must not cost the user their edit, so the failure
        is reported alongside a successful result rather than raised.

        The three keyword arguments exist for the one caller that knows more
        than a hand edit does — refreshing a track's metadata from a source:

        - ``changes`` may include ``cover_hash``. It is written to the row like
          any other column and ignored by the retag, which only walks
          ``_FIELD_TO_TAG``; the artwork itself travels in ``picture_block``.
          Replacing it also retires the cover it displaced, if nothing else uses it.
        - ``extra_tags`` are Vorbis comments with no model field behind them,
          written verbatim. The escape hatch for anything the schema does not
          model; it used to carry ``LYRICS``, which now has a table of its own
          and a service that writes both copies —
          :class:`~app.services.lyrics_service.LyricsService`.
        - ``picture_block`` replaces the embedded artwork.
        """
        track = self.track_repo.get_by_id(track_id)
        if track is None:
            return MetadataUpdate(None, "")

        previous_cover: Optional[str] = track.cover_hash  # type: ignore[assignment]

        reassigned_artists = False
        # Held back rather than applied in the loop: an album is resolved by name
        # *and* artist, so it cannot be settled until the credits in this same
        # edit have been. Editing a track's artist and album together is the
        # ordinary case, not the edge.
        album_change: Optional[str] = None
        album_refs: Optional[Any] = None
        album_changed = False
        for key, value in changes.items():
            if key == 'artist_ids':
                track.artists = self._artists_by_id(value)
                reassigned_artists = True
            elif key == 'artists':
                track.artists = self._resolve_artists(value)
                reassigned_artists = True
            elif key == 'genres':
                track.genres = self._resolve_genres(value)
            elif key == 'album':
                album_change = value
                album_changed = True
            elif key == 'album_refs':
                album_refs = value
                album_changed = True
            elif key == 'external_ids':
                for id_key, id_value in (value or {}).items():
                    if str(id_value or "").strip():
                        external_ids.write(
                            self.track_repo.session, external_ids.TRACK, track,
                            id_key, id_value, unique=self.id_unique(id_key),
                        )
                    else:
                        external_ids.clear(external_ids.TRACK, track, id_key)
            else:
                setattr(track, key, value)

        # Both artist paths carry the order the user arranged, and both lose it
        # at the association table unless it is written explicitly. Flush first:
        # a reassignment inserts rows that do not exist yet, and there is nothing
        # to number until they do.
        if reassigned_artists:
            self.track_repo.session.flush()
            self._write_artist_order(track)

        # After the artists, for the reason given where `album_change` is set.
        # The refs win when a caller sends both: they describe the whole list,
        # and the string only ever describes its first entry.
        if album_refs is not None:
            self._apply_album_refs(track, album_refs)
        elif album_changed:
            self._apply_albums(track, album_change)

        self.track_repo.session.commit()

        # An artist the track just stopped crediting may now credit nothing at
        # all. Nothing else ever deletes an artist row, so without this the list
        # accretes debris from every edit — which is most of how the malformed
        # names got there in the first place.
        if reassigned_artists:
            self.sweep_orphan_artists()
        # And the album it just left may now hold nothing, for the same reason.
        if album_changed:
            self.sweep_orphan_albums()

        if previous_cover and previous_cover != track.cover_hash:
            self._vacuum_cover_if_orphaned(previous_cover)

        self.event_bus.emit('track.updated', track)
        logger.info(f"Track updated: {track.title} (ID: {track.id})")

        # The file's tags are written from `_FIELD_TO_TAG`, which knows `artists`
        # and not `artist_ids` — an edit made by id would otherwise leave ARTIST in
        # the Opus file naming whoever it named before. Restate the outcome as the
        # name string the tag wants, from the artists actually on the track.
        if 'artist_ids' in changes:
            changes = {k: v for k, v in changes.items() if k != 'artist_ids'}
            changes['artists'] = ", ".join(a.name for a in track.artists)

        # And the same for the albums, for the same reason: `_FIELD_TO_TAG` knows
        # `album` and not `album_refs`, so without this the file's ALBUM comment
        # goes on naming whichever record it named before. One name, because the
        # tag holds one — the album this track belongs to, which is the primary.
        if 'album_refs' in changes:
            changes = {k: v for k, v in changes.items() if k != 'album_refs'}
            changes['album'] = track.albums[0].name if track.albums else ""

        # And the ids, which `_FIELD_TO_TAG` does not know at all: each kind that
        # names a tag becomes that comment, read back off the row so a refused
        # write does not reach the file either. A cleared id is an empty value,
        # which removes the comment.
        if 'external_ids' in changes:
            ids = changes['external_ids'] or {}
            changes = {k: v for k, v in changes.items() if k != 'external_ids'}
            tags = self.id_tags()
            id_comments = {
                tags[key]: external_ids.value(external_ids.TRACK, track, key)
                for key in ids if key in tags
            }
            if id_comments:
                extra_tags = {**id_comments, **(extra_tags or {})}

        return MetadataUpdate(
            track, self._retag_file(track, changes, extra_tags, picture_block)
        )

    def _retag_file(
        self,
        track: Track,
        changes: Dict[str, Any],
        extra_tags: Optional[Dict[str, Any]] = None,
        picture_block: Optional[str] = None,
    ) -> str:
        """Mirror an applied edit into the Opus file. Returns a warning, or ""."""
        tag_changes = {
            tag: changes[field]
            for field, tag in _FIELD_TO_TAG.items()
            if field in changes
        }
        tag_changes.update(extra_tags or {})
        if not tag_changes and not picture_block:
            return ""
        try:
            update_opus_tags(Path(track.file_path), tag_changes, picture_block)
        except Exception as exc:  # noqa: BLE001 — any file-level failure is non-fatal
            logger.warning("Could not retag %s: %s", track.file_path, exc)
            return (
                "Saved, but the audio file's own tags could not be updated "
                "— it may be in use."
            )
        return ""

    def _vacuum_cover_if_orphaned(self, cover_hash: str) -> None:
        """Drop a cover's thumbnails once nothing references the hash any more.

        The cache is content-addressed and shared, so a hash going unused by one
        track says nothing about the rest of an album — the survivors have to be
        counted before anything is deleted.

        "The survivors" is every table that names a hash, not just ``tracks``.
        Artist portraits, playlist covers and album covers live in the same store,
        keyed the same way, and a sweep that only looked at tracks would delete
        them: the first edit after setting an artist's portrait would take the
        portrait with it.
        Being content-addressed also means an artist whose picture happens to *be*
        an album cover shares that row, so a hash can be live for one reason and
        dead for another and must stay.
        """
        if not cover_hash or not self.cover_store:
            return
        valid = self._referenced_cover_hashes()
        if cover_hash not in valid:
            self.cover_store.vacuum_orphans(valid)

    def vacuum_covers(self, candidates: Optional[set[str]] = None) -> None:
        """Sweep the cover cache once for a batch of possibly-retired hashes.

        The batch counterpart of :meth:`_vacuum_cover_if_orphaned`, for callers that
        retired several at once — deleting a run of orphaned artists, say. One
        recount and at most one sweep, rather than one of each per hash.
        """
        if not self.cover_store:
            return
        valid = self._referenced_cover_hashes()
        if candidates is None or (candidates - valid):
            self.cover_store.vacuum_orphans(valid)

    def _referenced_cover_hashes(self) -> set[str]:
        """Every cover hash any table still points at."""
        from sqlalchemy import text

        rows = self.track_repo.session.execute(
            text(
                "SELECT cover_hash FROM tracks    WHERE cover_hash IS NOT NULL "
                "UNION SELECT image_hash FROM artists   WHERE image_hash IS NOT NULL "
                "UNION SELECT cover_hash FROM playlists WHERE cover_hash IS NOT NULL "
                # Albums keep their own cover, which is very often *not* any
                # track's: an identified album holds the
                # release's real artwork while its files still carry whatever
                # they were tagged with. Leave this line out and the next track
                # edit finds that hash referenced by nothing and deletes it.
                "UNION SELECT cover_hash FROM albums    WHERE cover_hash IS NOT NULL"
            )
        )
        return {row[0] for row in rows if row[0]}

    def delete_track(self, track_id: int) -> bool:
        """Delete a track from the DB, its audio file from disk, and orphaned cover art."""
        import os
        from app.utils.library_paths import track_path

        track = self.track_repo.get_by_id(track_id)
        if track is None:
            return False

        cover_hash: Optional[str] = track.cover_hash  # type: ignore[assignment]

        # Read before the delete, because `track_albums` rows go with the track
        # through ON DELETE CASCADE and no Python code ever sees that happen.
        # Afterwards there is nothing left to ask which records just got shorter.
        album_ids = [int(a.id) for a in track.albums]

        result = self.track_repo.delete(track_id)
        if not result:
            return False

        if album_ids:
            self.album_repo.clear_complete(album_ids)
            self.track_repo.session.commit()

        # Remove the audio files from disk — the mix, and the instrumental that
        # mirrors it under bt/. Leaving the latter behind would orphan it: its
        # only name is the track id that no longer exists.
        from app.utils.library_paths import BACKING_CATEGORY

        for category in (None, BACKING_CATEGORY):
            try:
                audio_path = (
                    track_path(track_id) if category is None
                    else track_path(track_id, category=category)
                )
                if os.path.exists(audio_path):
                    os.remove(audio_path)
            except OSError as e:
                logger.warning(
                    f"Could not delete {category or 'mix'} file for track {track_id}: {e}"
                )

        if self.backing_service is not None:
            self.backing_service.mark_removed(track_id)

        # Remove cover thumbnails from covers.db if no remaining track uses this hash.
        if cover_hash:
            self._vacuum_cover_if_orphaned(cover_hash)

        self.event_bus.emit('track.deleted', track_id)
        logger.info(f"Track deleted: ID {track_id}")
        return True
