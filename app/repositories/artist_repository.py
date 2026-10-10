"""Artist repository - data access for Artist model.

This module provides specialized queries for the Artist model.

Responsibilities:
- Resolve artist names to rows during track import/download
- List and search artists for the artist pages and the track editor's chip input
- Reassign tracks between artists, and clear away the rows nothing points at

Design:
- Inherits generic CRUD from BaseRepository[Artist]
- Adds domain-specific queries for artist resolution

Relationship to other modules:
- Used by TrackService when resolving artist names during track creation
- Used by ArtistService for renames, splits and orphan sweeps
- Called during download flow to ensure artists are created/linked properly
"""
from typing import Any, Collection, Dict, List, Optional

from sqlalchemy import case, delete, func, select
from sqlalchemy.orm import Session

from app.models.track import Artist, Track, track_artists_association
from app.plugins import external_ids
from app.repositories.base_repository import BaseRepository
from app.repositories.track_repository import write_artist_positions

# How :meth:`ArtistRepository.list_all` may be ordered. Not a field catalog like
# the track list's: an artist has four columns and only three of them mean
# anything to sort on, so the keys are written here rather than derived.
SORT_FIELDS = ("trackCount", "name", "dateAdded")

# Biggest catalogue first, which is the order the artists you actually listen to
# come out in. An unrecognised key falls back to this rather than raising — a GUI
# is free to ask for anything, and a stale preference should show a library, not
# an error.
DEFAULT_SORT = "trackCount"


class ArtistRepository(BaseRepository[Artist]):
    """Repository for artist data access.

    Provides queries specific to artists, primarily for resolving artist
    names during track creation/import.
    """

    def __init__(self, session: Session):
        """Initialize artist repository.

        Args:
            session: SQLAlchemy session for database access
        """
        super().__init__(session, Artist)

    def get_by_name(self, name: str) -> Optional[Artist]:
        """Get artist by exact name.

        Args:
            name: Artist name to search for (case-sensitive)

        Returns:
            The artist if found, None otherwise
        """
        return self.session.query(Artist).filter(Artist.name == name).first()

    def get_by_name_insensitive(self, name: str) -> Optional[Artist]:
        """Get artist by name, ignoring case.

        Separate from :meth:`get_by_name` rather than replacing it. ``get_or_create``
        must keep matching exactly, because a library already holding both "Skrillex"
        and "skrillex" would otherwise have one of them silently start absorbing the
        other's tracks on the next import. This is for the callers that *want* the
        loose match — chiefly the conflict check before a rename, which needs to
        refuse "daft punk" when "Daft Punk" exists.
        """
        if not name:
            return None
        return (
            self.session.query(Artist)
            .filter(func.lower(Artist.name) == name.strip().lower())
            .first()
        )

    def get_or_create(self, name: str) -> Artist:
        """Get existing artist or create a new one.

        This is the primary method for artist resolution during track creation.
        It ensures that the same artist name always maps to the same database
        entry, preventing duplicates.

        Args:
            name: Artist name (will be created if doesn't exist)

        Returns:
            The existing or newly created artist object
        """
        artist = self.get_by_name(name)
        if artist:
            return artist
        # Create new artist
        return self.create(name=name)

    # ------------------------------------------------------------------
    # Listing and searching
    # ------------------------------------------------------------------

    def list_all(
        self, sort_field: str = DEFAULT_SORT, ascending: bool = False
    ) -> List[Dict[str, Any]]:
        """Every artist with its track count, in the requested order.

        One grouped query, and an *outer* join so that an artist with no tracks is
        still listed. Those are exactly the rows a user needs to see — an artist
        nothing points at is debris, and hiding it would hide the mess.

        Ordered in SQL rather than by the caller, for the reason the track list is
        (``TrackRepository._apply_sort``): the count is already an aggregate this
        query produces, and sorting on it afterwards would mean re-deriving in
        Python what the database has just worked out.
        """
        assoc = track_artists_association
        count = func.count(assoc.c.track_id)
        expressions = {
            "trackCount": count,
            "name": func.lower(Artist.name),
            # Artists carry no timestamp, so the row id *is* the order they were
            # added in — every path that creates one goes through `get_or_create`.
            "dateAdded": Artist.id,
        }
        expr = expressions.get(sort_field, expressions[DEFAULT_SORT])

        # The tiebreak runs with the sort, not against it: reversing the direction
        # of the whole ordering is what a user pressing the chevron means, and a
        # tiebreak pinned one way would leave equal rows in the same order both
        # times. Same rule as the track list's.
        tiebreak = Artist.id.asc() if ascending else Artist.id.desc()

        stmt = (
            select(
                Artist.id,
                Artist.name,
                Artist.image_hash,
                count.label("track_count"),
            )
            .select_from(Artist)
            .outerjoin(assoc, assoc.c.artist_id == Artist.id)
            .group_by(Artist.id)
            .order_by(expr.asc() if ascending else expr.desc(), tiebreak)
        )
        return self._rows_to_dicts(self.session.execute(stmt).all())

    def search(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Artists whose name matches *query*, best match first.

        Ordered by how the match was made — a name that *starts* with what was typed
        is a better answer than one that merely contains it somewhere — then by track
        count, so the artist with a catalogue outranks the one-off collaboration of
        the same name. Both orderings are done in SQL so the limit can be applied
        there too.
        """
        term = (query or "").strip()
        if not term:
            return []

        assoc = track_artists_association
        lowered = func.lower(Artist.name)
        rank = func.min(
            # 0 exact, 1 prefix, 2 contains — the same shape as the search ranking,
            # kept small so it sorts ascending alongside a descending track count.
            self._match_rank(lowered, term.lower())
        )
        stmt = (
            select(
                Artist.id,
                Artist.name,
                Artist.image_hash,
                func.count(assoc.c.track_id).label("track_count"),
            )
            .select_from(Artist)
            .outerjoin(assoc, assoc.c.artist_id == Artist.id)
            .where(lowered.like(f"%{term.lower()}%"))
            .group_by(Artist.id)
            .order_by(rank.asc(), func.count(assoc.c.track_id).desc(), lowered)
            .limit(max(1, int(limit)))
        )
        return self._rows_to_dicts(self.session.execute(stmt).all())

    @staticmethod
    def _match_rank(lowered_name, term: str):
        """0 for an exact name, 1 for a prefix, 2 for a match anywhere else."""
        return case(
            (lowered_name == term, 0),
            (lowered_name.like(f"{term}%"), 1),
            else_=2,
        )

    def _rows_to_dicts(self, rows) -> List[Dict[str, Any]]:
        """The list rows as dicts, each carrying every id its artist holds.

        The ids are a second query for the whole list rather than a join in the
        first. An artist holds any number of them, and joining would turn one
        row per artist into one per id under a GROUP BY that is already
        counting tracks.
        """
        out = [
            {
                "id": int(row[0]),
                "name": row[1] or "",
                "image_hash": row[2] or "",
                "track_count": int(row[3] or 0),
            }
            for row in rows
        ]
        held = external_ids.read_many(self.session, external_ids.ARTIST, [r["id"] for r in out])
        for row in out:
            # Every id the artist is known by — see `app.plugins.external_ids`.
            row["external_ids"] = held.get(row["id"], {})
        return out

    def track_ids(self, artist_id: int) -> List[int]:
        """The ids of every track credited to *artist_id*, in library order."""
        assoc = track_artists_association
        stmt = (
            select(assoc.c.track_id)
            .where(assoc.c.artist_id == int(artist_id))
            .order_by(assoc.c.track_id)
        )
        return [int(row[0]) for row in self.session.execute(stmt).all()]

    def identifiable_tracks(
        self, artist_id: int, limit: int = 3, keys: Optional[Collection[str]] = None,
    ) -> List[Dict[str, Any]]:
        """The artist's tracks that carry an external id, newest first.

        What identifying an artist starts from: a recording they are on, as some
        catalogue knows it, is an unambiguous handle on who they are, where
        their name is not. *keys*, when given, narrows that to tracks holding
        one of those kinds, the ones the provider being asked can read.

        More than one, because an entry taken down since it was saved answers
        nothing and the next track is a free second try. Three is enough — an
        artist whose three most recent tracks all answer nothing is not going
        to be rescued by a fourth, and each one costs a network round trip.
        """
        assoc = track_artists_association
        stmt = (
            select(Track.id, Track.title, Track.duration_seconds)
            .join(assoc, assoc.c.track_id == Track.id)
            .where(
                assoc.c.artist_id == int(artist_id),
                external_ids.identified_clause(external_ids.TRACK, keys),
            )
            .order_by(Track.id.desc())
            .limit(int(limit))
        )
        rows = self.session.execute(stmt).all()
        held = external_ids.read_many(
            self.session, external_ids.TRACK, [int(row[0]) for row in rows]
        )
        return [
            {
                "track_id": int(row[0]),
                "title": row[1] or "",
                "duration": int(row[2] or 0),
                "external_ids": held.get(int(row[0]), {}),
            }
            for row in rows
        ]

    def identities_for_names(self, names: List[str]) -> Dict[str, Dict[str, str]]:
        """What is already known about these artists, keyed by lowercased name.

        One query for a whole track's worth of artists, so the ingestion path can
        decide what still needs fetching without a round trip each. Returns
        ``{name.lower(): {"external_ids", "image_hash"}}``; names with no row are
        simply absent.
        """
        wanted = {n.strip().lower() for n in names if n and n.strip()}
        if not wanted:
            return {}
        stmt = select(
            Artist.id, Artist.name, Artist.image_hash
        ).where(func.lower(Artist.name).in_(wanted))
        rows = self.session.execute(stmt).all()
        held = external_ids.read_many(
            self.session, external_ids.ARTIST, [int(row[0]) for row in rows]
        )
        return {
            (row[1] or "").lower(): {
                "external_ids": held.get(int(row[0]), {}),
                "image_hash": row[2] or "",
            }
            for row in rows
        }

    def track_count(self, artist_id: int) -> int:
        """How many tracks are credited to *artist_id*."""
        assoc = track_artists_association
        stmt = select(func.count()).select_from(assoc).where(
            assoc.c.artist_id == int(artist_id)
        )
        return int(self.session.execute(stmt).scalar() or 0)

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def orphan_ids(self) -> List[int]:
        """Artists no track credits any more."""
        assoc = track_artists_association
        stmt = select(Artist.id).where(
            ~Artist.id.in_(select(assoc.c.artist_id).distinct())
        )
        return [int(row[0]) for row in self.session.execute(stmt).all()]

    def delete_orphans(self) -> tuple[int, set[str]]:
        """Delete every artist no track credits, and report what that retired.

        Returns ``(deleted_count, portrait_hashes)``. The hashes are handed back
        rather than acted on here because reclaiming them means touching a second
        database — the cover cache — and a repository does not reach across that
        boundary. The caller runs one sweep for the whole batch.

        Nothing has ever deleted an artist row before this, so a library that has
        seen a few hundred edits will shed a real number of rows the first time.
        """
        ids = self.orphan_ids()
        if not ids:
            return 0, set()

        hashes = {
            row[0]
            for row in self.session.execute(
                select(Artist.image_hash).where(
                    Artist.id.in_(ids), Artist.image_hash.is_not(None)
                )
            ).all()
            if row[0]
        }
        self.session.execute(delete(Artist).where(Artist.id.in_(ids)))
        self.session.commit()
        # Same reason as `reassign_tracks`: a bulk DELETE leaves the session
        # holding Artist objects for rows that are gone, and Track collections
        # still pointing at them.
        self.session.expire_all()
        return len(ids), hashes

    def reassign_tracks(self, from_artist_id: int, to_artist_ids: List[int]) -> int:
        """Credit every track of *from_artist_id* to *to_artist_ids* instead.

        Returns the number of tracks moved. The source artist keeps its rows here —
        deleting it is the caller's decision, and doing it in one place makes the
        move replayable if something below it fails.

        Naming the source among the targets means "and keep this one too": its
        credits are left alone and the others are added beside it. Splitting
        "A & B" into "A & B" and "B" is a real thing to want, and dropping the
        artist the user just named would destroy the row they asked to keep.

        Written as bulk statements rather than by walking ORM objects: a prolific
        artist can carry hundreds of tracks, and loading each one to edit a
        collection would be hundreds of round trips to change one join table.
        """
        assoc = track_artists_association
        track_ids = self.track_ids(from_artist_id)
        if not track_ids or not to_artist_ids:
            return 0

        # Read the credit order before touching anything, so the artists a
        # compound name splits into can take *its* slot rather than landing at
        # the end. "SAMMY & LESEN" credited third becomes two artists still
        # credited third — a split corrects who the credits name, and has no
        # opinion about how important they were.
        current: Dict[int, List[int]] = {}
        for row in self.session.execute(
            select(assoc.c.track_id, assoc.c.artist_id)
            .where(assoc.c.track_id.in_(track_ids))
            .order_by(assoc.c.track_id, assoc.c.position)
        ).all():
            current.setdefault(int(row[0]), []).append(int(row[1]))

        existing = {
            (int(row[0]), int(row[1]))
            for row in self.session.execute(
                select(assoc.c.track_id, assoc.c.artist_id).where(
                    assoc.c.track_id.in_(track_ids),
                    assoc.c.artist_id.in_(to_artist_ids),
                )
            ).all()
        }
        # INSERT OR IGNORE would say this more briefly, but the composite primary
        # key means a duplicate is an error rather than a no-op, and a track that
        # already credits one of the targets is entirely ordinary — splitting
        # "A/B" when a track already credits "A" is the normal case, not the edge.
        new_rows = [
            {"track_id": track_id, "artist_id": artist_id}
            for track_id in track_ids
            for artist_id in to_artist_ids
            if (track_id, artist_id) not in existing
        ]
        if new_rows:
            self.session.execute(assoc.insert(), new_rows)

        if int(from_artist_id) not in {int(i) for i in to_artist_ids}:
            self.session.execute(
                delete(assoc).where(assoc.c.artist_id == int(from_artist_id))
            )

        # Substitute the targets in wherever the source stood, then keep the
        # first mention of each. A track that already credited one of the targets
        # keeps it where it was rather than gaining a second, later mention —
        # splitting "A/B" on a track that already credits "A" is the ordinary
        # case, not the edge.
        source = int(from_artist_id)
        targets = [int(i) for i in to_artist_ids]
        reordered: Dict[int, List[int]] = {}
        for track_id, artist_ids in current.items():
            expanded: List[int] = []
            for artist_id in artist_ids:
                expanded.extend(targets if artist_id == source else [artist_id])
            seen: set[int] = set()
            reordered[track_id] = [
                artist_id for artist_id in expanded
                if artist_id not in seen and not seen.add(artist_id)
            ]
        write_artist_positions(self.session, reordered)

        self.session.commit()

        # The statements above went straight to the join table, so any Track the
        # session already had loaded still holds the artist collection it was
        # loaded with — including the artist that has just stopped crediting it.
        # The whole application shares one session, so "already loaded" is the
        # normal case: the open editor, the now-playing track, anything a list
        # has touched. Expiring makes the next read of any of them go back to
        # the database, which is the only way they can learn what moved.
        self.session.expire_all()
        return len(track_ids)
