"""Track repository - domain-specific queries for tracks.

This module extends BaseRepository with track-specific queries and business logic.
It provides the primary interface for all track data access.

Responsibilities:
- Query tracks by various criteria (ID, title, artist, genre, album)
- Full-text search on track metadata
- Windowed reads: id vectors and page fetches for the track lists
- Create new track entries (used by download/import processes)

Design:
- Inherits generic CRUD from BaseRepository[Track]
- Adds custom queries that only make sense for tracks
- Encapsulates complex joins with artists/genres/versions

Relationship to other modules:
- Receives a session from bootstrap.py
- Used by TrackService (business logic)
- Used by the library core (app/library) for windowed reads
- Called by downloader.py when saving new tracks
"""
from typing import Any, Dict, Iterable, List, Optional
from sqlalchemy import bindparam, func, select
from sqlalchemy.orm import Session, joinedload
from app.library.fields import FIELDS_BY_KEY, SCALAR_FIELDS
from app.plugins import external_ids
from app.models.track import (
    Track,
    Album,
    Artist,
    Genre,
    track_albums_association,
    track_artists_association,
    track_genres_association,
)
from app.repositories.base_repository import BaseRepository
from app.search.search_query import AdvancedSearchFilter, SearchField, SearchOperator

# SQLite's default host-parameter limit is 999; stay well under it when
# expanding an `IN (...)` clause for a page of ids.
_ID_CHUNK = 500


def _chunked(ids: List[int]):
    """Yield *ids* in slices small enough for a single `IN (...)` clause."""
    for start in range(0, len(ids), _ID_CHUNK):
        yield ids[start:start + _ID_CHUNK]


def write_artist_positions(session, order: Dict[int, List[int]]) -> None:
    """Number each track's credits to read in the order given for it.

    The half of the ordering SQLAlchemy cannot do. ``Track.artists`` is a
    ``secondary`` relationship, which reads a column of the association table —
    that is what its ``order_by`` does — but can never write one: assigning the
    collection writes ``(track_id, artist_id)`` pairs and nothing else. So every
    path that arranges artists flushes the collection and then calls this.

    A module-level function rather than a method because two repositories arrange
    credits: TrackRepository when a track's artists are set, ArtistRepository
    when a split re-credits a whole catalogue at once. One statement for the lot,
    which is what keeps the split from being a round trip per track.

    A pair not actually in the table is simply not matched, so a stale list
    narrows what gets renumbered rather than corrupting it.
    """
    rows = [
        {"t": int(track_id), "a": int(artist_id), "p": position}
        for track_id, artist_ids in order.items()
        for position, artist_id in enumerate(artist_ids)
    ]
    if not rows:
        return

    assoc = track_artists_association
    stmt = (
        assoc.update()
        .where(assoc.c.track_id == bindparam("t"))
        .where(assoc.c.artist_id == bindparam("a"))
        .values(position=bindparam("p"))
    )
    session.execute(stmt, rows)


def write_album_positions(session, order: Dict[int, List[int]]) -> None:
    """Number each track's albums so the one it belongs to comes first.

    The album twin of :func:`write_artist_positions`, and it exists for the same
    reason: ``Track.albums`` is a ``secondary`` relationship, so assigning the
    collection writes ``(track_id, album_id)`` pairs and nothing else. The order
    has to be written afterwards, by hand.

    This writes ``album_position`` — the *track's* view, where 0 means "this is
    the album this recording belongs to". It does **not** touch ``position``,
    which is the album's view of where the track sits in its running order; that
    one belongs to the album and is written by
    :meth:`~app.repositories.album_repository.AlbumRepository.set_order`. The two
    are stored side by side in one table and are never written together, because
    nothing ever changes both at once: re-ordering an album says nothing about
    which album any of its tracks belongs to.
    """
    rows = [
        {"t": int(track_id), "b": int(album_id), "p": position}
        for track_id, album_ids in order.items()
        for position, album_id in enumerate(album_ids)
    ]
    if not rows:
        return

    assoc = track_albums_association
    stmt = (
        assoc.update()
        .where(assoc.c.track_id == bindparam("t"))
        .where(assoc.c.album_id == bindparam("b"))
        .values(album_position=bindparam("p"))
    )
    session.execute(stmt, rows)


class TrackRepository(BaseRepository[Track]):
    """Repository for track-specific data access.

    This repository provides both generic CRUD (inherited from BaseRepository)
    and domain-specific queries for tracks.

    Custom queries include:
    - get_by_title_and_artist: Find a track by title and artist name
    - get_by_artist: Get all tracks by a specific artist
    - get_by_genre: Get all tracks in a specific genre
    - get_by_album: Get all tracks in an album
    - quick_search_ids: Ids of tracks whose title or album matches

    These queries handle the complexity of joining the many-to-many tables
    (track_artists, track_genres) so callers don't need to think about it.
    """

    def __init__(self, session: Session):
        """Initialize track repository.

        Args:
            session: SQLAlchemy session for database access
        """
        super().__init__(session, Track)

    def tracks_at_durations(
        self, durations: Iterable[int]
    ) -> Dict[int, tuple[str, int]]:
        """Return ``{track_id: (title, duration_seconds)}`` for these exact lengths.

        The candidate set for "does the library already hold this recording?" —
        see :mod:`app.library.recording_match`. Duration is the narrowest filter
        of the three tests that question applies, and it is the only one SQL can
        answer cheaply: titles need normalising and artists need a join, but a
        length is an integer.

        **Exact lengths, and the tolerance is the caller's to expand.** A
        tolerance is a small set of whole seconds rather than a range predicate
        (``recording_match.duration_window`` builds it), which is what lets a
        whole album's worth of candidates be asked for in one ``IN`` query
        instead of one query per track.

        Chunked like every other ``IN`` here, for SQLite's parameter limit.
        """
        wanted = sorted({int(d) for d in durations if int(d) > 0})
        if not wanted:
            return {}

        out: Dict[int, tuple[str, int]] = {}
        for chunk in _chunked(wanted):
            stmt = select(Track.id, Track.title, Track.duration_seconds).where(
                Track.duration_seconds.in_(chunk)
            )
            for track_id, title, seconds in self.session.execute(stmt).all():
                out[int(track_id)] = (title or "", int(seconds or 0))
        return out

    def get_by_artist(self, artist_name: str) -> List[Track]:
        """Get all tracks by a specific artist.

        Args:
            artist_name: Artist name to search for

        Returns:
            List of all tracks by that artist (empty list if none found)
        """
        return self.session.query(Track).join(Track.artists).filter(
            Artist.name == artist_name
        ).all()

    def get_by_genre(self, genre_name: str) -> List[Track]:
        """Get all tracks in a specific genre.

        Args:
            genre_name: Genre name to search for

        Returns:
            List of all tracks in that genre (empty list if none found)
        """
        return self.session.query(Track).join(Track.genres).filter(
            Genre.name == genre_name
        ).all()

    def get_by_album(self, album: str) -> List[Track]:
        """Get all tracks on an album, by name.

        By *name*, so it still answers when several albums share one — every
        track on any of them comes back. Callers that mean one particular
        release should hold its id and use
        :meth:`~app.repositories.album_repository.AlbumRepository.track_ids`,
        which also returns them in the album's running order rather than
        whatever order the join serves.

        Args:
            album: Album name to search for

        Returns:
            List of all tracks on an album of that name (empty if none found)
        """
        assoc = track_albums_association
        return (
            self.session.query(Track)
            .join(assoc, assoc.c.track_id == Track.id)
            .join(Album, Album.id == assoc.c.album_id)
            .filter(Album.name == album)
            .all()
        )

    def _album_name_matches(self, term: str):
        """An EXISTS over the track's albums, for the plain search bar's OR.

        An EXISTS rather than a join because this sits beside a title match in
        an ``OR``: a join would multiply the row out once per album and turn a
        track on four compilations into four search hits.
        """
        assoc = track_albums_association
        return (
            select(1)
            .select_from(assoc)
            .join(Album, Album.id == assoc.c.album_id)
            .where(assoc.c.track_id == Track.id, Album.name.ilike(term))
            .exists()
        )

    def _primary_album_name(self):
        """Correlated scalar subquery giving the track's primary album name."""
        assoc = track_albums_association
        return (
            select(Album.name)
            .select_from(assoc)
            .join(Album, Album.id == assoc.c.album_id)
            .where(assoc.c.track_id == Track.id, assoc.c.album_position == 0)
            .scalar_subquery()
        )

    # ------------------------------------------------------------------
    # Windowed reads — id vectors and page fetches
    #
    # These are the queries the library core runs. They deliberately use
    # SQLAlchemy Core rather than the ORM: a page fetch must not pin Track,
    # Artist and Genre objects in the long-lived shared session, or scrolling
    # a large library grows the identity map without bound.
    # ------------------------------------------------------------------

    def query_ids(
        self,
        filters: Optional[List[AdvancedSearchFilter]] = None,
        sort_field: str = "",
        ascending: bool = True,
    ) -> List[int]:
        """Return the ordered track ids matching *filters*.

        One column, no joins beyond what the filters and sort need, and no row
        data — this is what lets a list of 10,000 tracks be structurally
        complete before a single field has been read.

        Args:
            filters: AND-ed filter clauses; empty or None means the whole library.
            sort_field: A field key from the catalog, or "" for insertion order.
            ascending: Sort direction.

        Returns:
            Track ids in display order.
        """
        stmt = select(Track.id).select_from(Track)

        joined: set = set()
        for f in filters or []:
            stmt, joined = self._apply_filter_select(stmt, joined, f)

        stmt = self._apply_sort(stmt, sort_field, ascending)
        return [row[0] for row in self.session.execute(stmt).all()]

    def quick_search_ids(
        self, query: str, sort_field: str = "", ascending: bool = True
    ) -> List[int]:
        """Ids of tracks whose title or album contains *query*.

        The plain search bar's vocabulary. It is an OR, which the AND-ed
        ``AdvancedSearchFilter`` list cannot express, so it gets its own query
        rather than being bent into one.
        """
        term = f"%{query}%"
        stmt = select(Track.id).select_from(Track).where(
            Track.title.ilike(term) | self._album_name_matches(term)
        )
        stmt = self._apply_sort(stmt, sort_field, ascending)
        return [row[0] for row in self.session.execute(stmt).all()]

    def quick_search_rows(self, query: str) -> List[tuple]:
        """``(id, title, album)`` for tracks matching *query*, for ranking.

        The same WHERE clause as :meth:`quick_search_ids`, but carrying the two
        columns a relevance score has to look at. Kept separate rather than
        folded in: the plain search returns ids precisely so that no row data is
        read before something paints it, and making it read two columns for every
        match would give that up for every caller to serve one.

        Deliberately unbounded. A ranked list that quietly dropped its tail would
        make "Play all" mean "play the first two hundred", and the rows it dropped
        would be chosen before anything had scored them.
        """
        term = f"%{query}%"
        stmt = (
            # The album carried back is the *primary* one, while the match above
            # accepts any of them. A track found through a compilation therefore
            # scores nothing for its album and rides on its title alone, which is
            # the right outcome: it is the title that made it a plausible answer,
            # and album is already the weakest signal in `score_track`.
            select(Track.id, Track.title, self._primary_album_name())
            .select_from(Track)
            .where(Track.title.ilike(term) | self._album_name_matches(term))
            .order_by(Track.id)
        )
        return [tuple(row) for row in self.session.execute(stmt).all()]

    def _apply_sort(self, stmt, sort_field: str, ascending: bool):
        """Order an id select by a catalog field, collapsing duplicate rows."""
        field = FIELDS_BY_KEY.get(sort_field)

        # The tiebreak runs with the sort, not against it. Some fields are
        # coarser than the rows they order — `stashedDate` is a whole day, so
        # everything imported in one sitting shares a value — and there the
        # tiebreak *is* the order the user sees. Newest first followed by that
        # day walked forwards reads as a broken sort, not as a tiebreak.
        tiebreak = Track.id.asc() if ascending else Track.id.desc()

        if field is None or not field.sortable:
            return stmt.distinct().order_by(tiebreak)

        # Sorting by artist needs its own join, aliased so it cannot collide
        # with a join a filter already added.
        if field.key == "artist":
            assoc = track_artists_association.alias("sort_artists_assoc")
            artist = Artist.__table__.alias("sort_artists")
            expr = func.min(func.lower(artist.c.name))
            return (
                stmt.outerjoin(assoc, assoc.c.track_id == Track.id)
                .outerjoin(artist, artist.c.id == assoc.c.artist_id)
                .group_by(Track.id)
                .order_by(expr.asc() if ascending else expr.desc(), tiebreak)
            )

        # And so does sorting by album, for the same reason — with one extra
        # clause. The join is restricted to `album_position = 0` so a track that
        # also appears on three compilations still sorts by the release it
        # belongs to, rather than by whichever of its albums happens to sort
        # lowest. Without that restriction a single on a compilation would
        # wander to wherever the compilation's name put it.
        if field.key == "album":
            assoc = track_albums_association.alias("sort_albums_assoc")
            album = Album.__table__.alias("sort_albums")
            expr = func.min(func.lower(album.c.name))
            return (
                stmt.outerjoin(
                    assoc,
                    (assoc.c.track_id == Track.id) & (assoc.c.album_position == 0),
                )
                .outerjoin(album, album.c.id == assoc.c.album_id)
                .group_by(Track.id)
                .order_by(expr.asc() if ascending else expr.desc(), tiebreak)
            )

        col = field.column
        expr = func.lower(col) if field.type == "string" else col
        return (
            stmt.distinct()
            .order_by(expr.asc() if ascending else expr.desc(), tiebreak)
        )

    def _apply_filter_select(self, stmt, joined: set, f: AdvancedSearchFilter):
        """Apply one filter to a Core select, tracking which tables are joined.

        Mirrors :meth:`_apply_filter` but for ``select()`` rather than
        ``Query``. The *joined* set stops a second artist or genre filter from
        joining the same table twice.
        """
        field    = f.field
        operator = f.operator
        value    = f.value

        if value is None or str(value).strip() == "":
            return stmt, joined

        # ---- Text fields ----
        text_field_map = {
            SearchField.TITLE: Track.title,
            SearchField.KEY:   Track.musical_key,
            SearchField.ISRC:  Track.isrc,
        }
        if field in text_field_map:
            col = text_field_map[field]
            if operator == SearchOperator.CONTAINS:
                return stmt.where(col.ilike(f"%{value}%")), joined
            if operator == SearchOperator.EQUALS:
                return stmt.where(col.ilike(str(value))), joined
            return stmt, joined

        # ---- One artist, by id ----
        #
        # Its own aliased join rather than sharing the name join below, for the
        # same reason the artist *sort* aliases its own: the two can appear in one
        # query, and a shared join would make each silently narrow the other. The
        # alias also means no `Artist` row has to be read at all — the association
        # table already carries the id being matched.
        if field == SearchField.ARTIST_ID:
            try:
                artist_id = int(value)
            except (TypeError, ValueError):
                return stmt, joined
            if operator not in (SearchOperator.EQUALS, SearchOperator.CONTAINS):
                return stmt, joined
            # Every artist-id filter gets a join of its own, unlike the name
            # filters below which share one. Two of these AND-ed means "credits
            # both of these artists", and one join could only ever mean "credits
            # an artist who is somehow two people at once" — which matches nothing.
            nth = sum(1 for marker in joined if marker.startswith("artist_id:"))
            assoc = track_artists_association.alias(f"filter_artist_id_{nth}")
            stmt = stmt.join(assoc, assoc.c.track_id == Track.id)
            joined = joined | {f"artist_id:{nth}"}
            return stmt.where(assoc.c.artist_id == artist_id), joined

        # ---- One album, by id ----
        #
        # The album twin of the artist-id filter above, and what lets the album
        # page declare its track list as an ordinary TrackQuery instead of
        # needing a list model of its own.
        #
        # Note what is *not* here: a restriction to `album_position = 0`. Asking
        # for an album means asking for its whole running order, compilation
        # appearances included — the album page must show every track on it, not
        # only the ones that call it home. That is the opposite of what the sort
        # above needs, which is why the two cannot share a join.
        if field == SearchField.ALBUM_ID:
            try:
                album_id = int(value)
            except (TypeError, ValueError):
                return stmt, joined
            if operator not in (SearchOperator.EQUALS, SearchOperator.CONTAINS):
                return stmt, joined
            nth = sum(1 for marker in joined if marker.startswith("album_id:"))
            assoc = track_albums_association.alias(f"filter_album_id_{nth}")
            stmt = stmt.join(assoc, assoc.c.track_id == Track.id)
            joined = joined | {f"album_id:{nth}"}
            return stmt.where(assoc.c.album_id == album_id), joined

        # ---- Relationship fields ----
        if field in (SearchField.ARTIST, SearchField.GENRE, SearchField.ALBUM):
            if field == SearchField.ARTIST:
                assoc, target, name_col, marker = (
                    track_artists_association, Artist, Artist.name, "artists",
                )
                on_clause = target.id == assoc.c.artist_id
            elif field == SearchField.ALBUM:
                # Filtering by album *name* was a plain column match until albums
                # became rows. It joins now, and matches across every album a
                # track is on rather than only its primary: someone filtering for
                # "Discovery" wants the track that appears on it either way.
                assoc, target, name_col, marker = (
                    track_albums_association, Album, Album.name, "albums",
                )
                on_clause = target.id == assoc.c.album_id
            else:
                assoc, target, name_col, marker = (
                    track_genres_association, Genre, Genre.name, "genres",
                )
                on_clause = target.id == assoc.c.genre_id

            if marker not in joined:
                stmt = stmt.join(assoc, assoc.c.track_id == Track.id).join(target, on_clause)
                joined = joined | {marker}

            if operator == SearchOperator.CONTAINS:
                return stmt.where(name_col.ilike(f"%{value}%")), joined
            if operator == SearchOperator.EQUALS:
                return stmt.where(name_col.ilike(str(value))), joined
            return stmt, joined

        # ---- Numeric and date fields (same operator set) ----
        numeric_field_map = {
            SearchField.BPM:          Track.bpm,
            SearchField.DURATION:     Track.duration_seconds,
            SearchField.DATE:         Track.release_date,
            SearchField.STASHED_DATE: Track.created_at,
        }
        if field in numeric_field_map:
            try:
                num = int(value)
            except (ValueError, TypeError):
                return stmt, joined
            col = numeric_field_map[field]
            clause = {
                SearchOperator.EQUALS:        col == num,
                SearchOperator.GREATER_THAN:  col > num,
                SearchOperator.LESS_THAN:     col < num,
                SearchOperator.GREATER_EQUAL: col >= num,
                SearchOperator.LESS_EQUAL:    col <= num,
            }.get(operator)
            if clause is not None:
                return stmt.where(clause), joined

        return stmt, joined

    def fetch_scalar_rows(self, ids: List[int]) -> Dict[int, Dict[str, Any]]:
        """Read every `tracks` column for *ids*, keyed by track id.

        All scalar fields arrive together because they cost the same single
        SELECT; splitting them further would buy nothing. The external ids ride
        along in it too, LEFT JOINed on, so a track holding two ids comes back
        as two rows that fold into one ``externalIds`` map. That map holds every
        id the track has, declared or not. Which plugins are on is decided at
        paint time, and the cache must not hold an answer that a toggle would
        make stale.
        """
        if not ids:
            return {}

        columns = [Track.id.label("trackId")] + [
            f.column.label(f.key) for f in SCALAR_FIELDS  # type: ignore[union-attr]
        ]
        id_columns = external_ids.joined_columns(external_ids.TRACK)
        id_labels = {c.key for c in id_columns}
        out: Dict[int, Dict[str, Any]] = {}
        for chunk in _chunked(ids):
            stmt = external_ids.outer_join(
                select(*columns, *id_columns), external_ids.TRACK
            ).where(Track.id.in_(chunk))
            for row in self.session.execute(stmt):
                track_id = row._mapping["trackId"]
                data = out.get(track_id)
                if data is None:
                    data = {
                        k: v for k, v in row._mapping.items()
                        if k != "trackId" and k not in id_labels
                    }
                    data["externalIds"] = {}
                    out[track_id] = data
                pair = external_ids.joined_pair(row)
                if pair is not None:
                    data["externalIds"][pair[0]] = pair[1]
        return out

    def fetch_durations(self, ids: List[int]) -> Dict[int, int]:
        """Return ``{track_id: duration_seconds}`` for *ids*.

        Two columns rather than the whole SCALAR tier: totalling a list means
        touching every id in it, and a full-library sweep should not pull every
        column of every row through the row cache to add up one of them.
        """
        if not ids:
            return {}

        out: Dict[int, int] = {}
        for chunk in _chunked(ids):
            stmt = select(Track.id, Track.duration_seconds).where(Track.id.in_(chunk))
            for track_id, seconds in self.session.execute(stmt).all():
                out[int(track_id)] = int(seconds or 0)
        return out

    def fetch_artists(self, ids: List[int]) -> Dict[int, str]:
        """Return ``{track_id: "Artist A, Artist B"}`` for *ids*.

        Tracks with no artists are present with an empty string, so the caller
        can tell "fetched and empty" from "not fetched".
        """
        return self._fetch_related(
            ids,
            track_artists_association,
            track_artists_association.c.artist_id,
            Artist,
            # The track's own credit order, which is the whole reason the column
            # exists. Everything else here would order by `artists.id` — the
            # order the artist joined the library — and so would lead every
            # track with whoever was imported earliest.
            order_column=track_artists_association.c.position,
        )

    def fetch_albums(self, ids: List[int]) -> Dict[int, str]:
        """Return ``{track_id: "Primary Album"}`` for *ids*.

        The *primary* album only — ``album_position = 0`` — where the artist and
        genre fetches comma-join everything they find. That is the difference
        between a credit list and a home: a track credits several artists at
        once, but it belongs to one release and merely *appears* on the
        compilations. A column showing "Discovery, NOW 47, Chillout Sessions"
        would be answering a question nobody asked.

        Tracks on no album are present with an empty string, so a caller can
        tell "fetched and empty" from "not fetched" — the same contract
        :meth:`fetch_artists` keeps.
        """
        if not ids:
            return {}

        assoc = track_albums_association
        out: Dict[int, str] = {int(i): "" for i in ids}
        for chunk in _chunked(ids):
            stmt = (
                select(assoc.c.track_id, Album.name)
                .join(Album, Album.id == assoc.c.album_id)
                .where(assoc.c.track_id.in_(chunk), assoc.c.album_position == 0)
            )
            for track_id, name in self.session.execute(stmt).all():
                out[int(track_id)] = name or ""
        return out

    def write_album_order(self, track_id: int, album_ids: List[int]) -> None:
        """Number *track_id*'s albums so *album_ids[0]* becomes its primary."""
        write_album_positions(
            self.session, {int(track_id): [int(a) for a in album_ids]}
        )

    def fetch_genres(self, ids: List[int]) -> Dict[int, str]:
        """Return ``{track_id: "Genre A, Genre B"}`` for *ids*."""
        return self._fetch_related(
            ids,
            track_genres_association,
            track_genres_association.c.genre_id,
            Genre,
        )

    def _fetch_related(
        self, ids: List[int], assoc, fk_column, target, order_column=None
    ) -> Dict[int, str]:
        """Collapse a many-to-many name list into one comma-separated string per track.

        ``order_column`` says what the names are ordered by within a track, and
        defaults to the related row's id. Artists pass the association's own
        ``position``, because a track's credits have an order of their own that
        nothing about the artist rows can express. Genres have no such order and
        take the default — a set of tags, where "first" means nothing.
        """
        if not ids:
            return {}

        within = target.id if order_column is None else order_column
        out: Dict[int, str] = {int(i): "" for i in ids}
        for chunk in _chunked(ids):
            stmt = (
                select(assoc.c.track_id, target.name)
                .join(target, target.id == fk_column)
                .where(assoc.c.track_id.in_(chunk))
                .order_by(assoc.c.track_id, within)
            )
            grouped: Dict[int, List[str]] = {}
            for track_id, name in self.session.execute(stmt).all():
                grouped.setdefault(int(track_id), []).append(name)
            for track_id, names in grouped.items():
                out[track_id] = ", ".join(names)
        return out

    def write_artist_order(self, track_id: int, artist_ids: List[int]) -> None:
        """Number *track_id*'s credits so they read in the order of *artist_ids*."""
        write_artist_positions(
            self.session, {int(track_id): [int(a) for a in artist_ids]}
        )

    def get_by_ids(self, ids: list[int]) -> List[Track]:
        """Bulk-fetch tracks by primary key with artists and genres eager-loaded.

        Uses a single IN query to avoid N+1 when backfilling display_data for
        a newly-visible column.
        """
        if not ids:
            return []
        return (
            self.session.query(Track)
            .filter(Track.id.in_(ids))
            .options(joinedload(Track.artists), joinedload(Track.genres))
            .all()
        )

    def get_all_ids(self) -> List[int]:
        """Return every track id, unpaginated.

        ``get_all()`` is paginated (100 rows by default), so full-library
        sweeps such as the on-disk size total must go through this instead.
        """
        return [row[0] for row in self.session.query(Track.id).all()]

    def get_stats(self) -> dict:
        """Return aggregate stats for the DatabaseView stats panel."""
        total   = self.session.query(func.count(Track.id)).scalar() or 0
        max_id  = self.session.query(func.max(Track.id)).scalar() or 0
        holes   = max(0, max_id - total)
        return {"total": total, "max_id": max_id, "holes": holes}

    def create_entry(self, track: Track) -> Track:
        """Create a new track entry.

        This method is specifically designed for the import flow:
        1. A Track ORM object is created with metadata from the source
        2. Passed to this method to be persisted
        3. The auto-generated ID is used for file storage

        Args:
            track: Track ORM object with metadata but no ID yet

        Returns:
            The same Track object, now with an ID (set by the database)

        Note:
            Used by downloader.py in the download_and_save_track flow
        """
        self.session.add(track)
        self.session.commit()
        return track
