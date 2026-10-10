"""Album repository - data access for the Album model.

Responsibilities:
- Resolve album names to rows during track import/download
- List and search albums for the album grid and the track editor's input
- Keep an album's running order, and move tracks between albums for a split
- Answer whether an album's tracks agree about their artwork
- Answer who is credited on an album, which no column records
- Clear away the rows nothing points at

Design:
- Inherits generic CRUD from BaseRepository[Album]
- Mirrors ArtistRepository closely, and deliberately: the two answer the same
  shape of question, and a reader who knows one should recognise the other

The one structural difference is ordering. An artist's tracks have no order —
"first track by this artist" means nothing — so ArtistRepository.track_ids sorts
by id and leaves it there. An album's running order is a fact about the album,
stored in ``track_albums.position``, and every read here honours it.

Relationship to other modules:
- Used by TrackService when resolving album names during track creation
- Used by AlbumService for renames, splits, unifies and orphan sweeps
"""
from typing import Any, Collection, Dict, Iterable, List, Optional

from sqlalchemy import case, delete, func, select, update
from sqlalchemy.orm import Session

from app.models.track import (
    Album,
    Artist,
    Track,
    track_albums_association,
    track_artists_association,
)
from app.plugins import external_ids
from app.repositories.base_repository import BaseRepository

# How :meth:`AlbumRepository.list_all` may be ordered. The same three keys as
# the artist grid's, because it is the same grid with different pictures in it,
# and a sort control written against one should read the other.
SORT_FIELDS = ("trackCount", "name", "dateAdded")

# Biggest record first. An unrecognised key falls back here rather than raising,
# for the reason the artist list does: a stale preference should show a library,
# not an error.
DEFAULT_SORT = "trackCount"


class AlbumRepository(BaseRepository[Album]):
    """Repository for album data access."""

    def __init__(self, session: Session):
        super().__init__(session, Album)

    # ------------------------------------------------------------------
    # Resolution
    # ------------------------------------------------------------------

    def lead_artist_subquery(self, track_id_column):
        """Correlated subquery giving a track's first-credited artist id."""
        ta = track_artists_association
        return (
            select(ta.c.artist_id)
            .where(ta.c.track_id == track_id_column)
            .order_by(ta.c.position, ta.c.artist_id)
            .limit(1)
            .scalar_subquery()
        )

    def lead_artist_id(self, album_id: int) -> Optional[int]:
        """The artist this album is credited to, or None.

        Defined as the first-credited artist of the album's first track, which
        is the same rule the backfill in ``_v4_albums`` grouped by. An album has
        no artist column of its own on purpose: it would be a third copy of a
        fact the tracks already carry, and it would go stale the moment a track
        was re-credited.
        """
        assoc = track_albums_association
        stmt = (
            select(self.lead_artist_subquery(assoc.c.track_id))
            .where(assoc.c.album_id == int(album_id))
            .order_by(assoc.c.position)
            .limit(1)
        )
        value = self.session.execute(stmt).scalar()
        return int(value) if value is not None else None

    def get_by_name_insensitive(self, name: str) -> Optional[Album]:
        """The first album of this name, ignoring case, or None."""
        if not name:
            return None
        return (
            self.session.query(Album)
            .filter(func.lower(Album.name) == name.strip().lower())
            .order_by(Album.id)
            .first()
        )

    def get_by_name_and_artist(
        self, name: str, artist_id: Optional[int]
    ) -> Optional[Album]:
        """The album of this name belonging to *artist_id*, or None.

        The resolution key, and the reason ``albums.name`` is not unique. Two
        unrelated records really are both called "Greatest Hits", and matching on
        the name alone would fold one artist's into the other's on the next
        import — the exact merge ``_v4_albums`` grouped by (name, lead artist) to
        avoid, undone one track at a time.

        With no artist to go on, falls back to the name alone. A track credited
        to nobody is rare and already degraded; putting it beside the same-named
        album is a better guess than minting a row per import.
        """
        if not name:
            return None
        wanted = name.strip().lower()
        if artist_id is None:
            return self.get_by_name_insensitive(name)

        assoc = track_albums_association
        stmt = (
            select(Album)
            .where(
                func.lower(Album.name) == wanted,
                select(1)
                .select_from(assoc)
                .where(
                    assoc.c.album_id == Album.id,
                    self.lead_artist_subquery(assoc.c.track_id) == int(artist_id),
                )
                .exists(),
            )
            .order_by(Album.id)
            .limit(1)
        )
        return self.session.execute(stmt).scalars().first()

    def get_or_create(self, name: str, artist_id: Optional[int] = None) -> Album:
        """Get the album of this name for this artist, or make one."""
        album = self.get_by_name_and_artist(name, artist_id)
        if album is not None:
            return album
        return self.create(name=name.strip())

    # ------------------------------------------------------------------
    # Listing and searching
    # ------------------------------------------------------------------

    def _distinct_cover_count(self):
        """Correlated count of the distinct covers among an album's own tracks.

        "Its own" meaning the ones it is *primary* for. A track that merely
        appears on this compilation carries the artwork of the record it belongs
        to, correctly, and counting it here would flag every compilation in the
        library as broken on the day it was created.
        """
        assoc = track_albums_association
        return (
            select(func.count(func.distinct(Track.cover_hash)))
            .select_from(assoc)
            .join(Track, Track.id == assoc.c.track_id)
            .where(
                assoc.c.album_id == Album.id,
                assoc.c.album_position == 0,
                Track.cover_hash.is_not(None),
            )
            .scalar_subquery()
        )

    def _album_cover_is_foreign(self):
        """Whether the album's own sleeve is a picture none of its tracks carry.

        The other half of the artwork verdict, and the half a distinct count
        cannot express. ``_distinct_cover_count`` compares the tracks to each
        other; this compares the album to them. An album identified from an online
        catalogue holds the release's real artwork, and if nothing was ever unified
        onto it every one of its tracks disagrees with the sleeve on its own
        page — while the count stays at 1 and says everything is fine.

        Two separate subqueries rather than one union so each answers one
        question. Empty hashes are not counted here either, for the same reason
        ``artwork_disagrees`` drops them: an album with no cover of its own is
        not in disagreement with anything.
        """
        assoc = track_albums_association
        carries_it = (
            select(1)
            .select_from(assoc)
            .join(Track, Track.id == assoc.c.track_id)
            .where(
                assoc.c.album_id == Album.id,
                assoc.c.album_position == 0,
                Track.cover_hash == Album.cover_hash,
            )
            .exists()
        )
        return case(
            (Album.cover_hash.is_(None), False),
            (Album.cover_hash == "", False),
            (carries_it, False),
            else_=True,
        )

    def list_all(
        self, sort_field: str = DEFAULT_SORT, ascending: bool = False
    ) -> List[Dict[str, Any]]:
        """Every album with its track count and artwork agreement, in order.

        One grouped query, and an *outer* join so an album with no tracks is
        still listed — a row nothing points at is debris, and hiding it would
        hide the mess. Same argument, same shape as ``ArtistRepository.list_all``.
        """
        assoc = track_albums_association
        count = func.count(assoc.c.track_id)
        expressions = {
            "trackCount": count,
            "name": func.lower(Album.name),
            # Albums carry no timestamp, so the row id is the order they were
            # added in — every path that creates one goes through get_or_create.
            "dateAdded": Album.id,
        }
        expr = expressions.get(sort_field, expressions[DEFAULT_SORT])
        tiebreak = Album.id.asc() if ascending else Album.id.desc()

        stmt = (
            select(
                Album.id,
                Album.name,
                Album.cover_hash,
                count.label("track_count"),
                self._distinct_cover_count().label("distinct_covers"),
                self._album_cover_is_foreign().label("album_cover_foreign"),
                Album.is_complete,
            )
            .select_from(Album)
            .outerjoin(assoc, assoc.c.album_id == Album.id)
            .group_by(Album.id)
            .order_by(expr.asc() if ascending else expr.desc(), tiebreak)
        )
        return self._rows_to_dicts(self.session.execute(stmt).all())

    def search(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Albums whose name matches *query*, best match first."""
        term = (query or "").strip()
        if not term:
            return []

        assoc = track_albums_association
        lowered = func.lower(Album.name)
        rank = func.min(self._match_rank(lowered, term.lower()))
        stmt = (
            select(
                Album.id,
                Album.name,
                Album.cover_hash,
                func.count(assoc.c.track_id).label("track_count"),
                self._distinct_cover_count().label("distinct_covers"),
                self._album_cover_is_foreign().label("album_cover_foreign"),
                Album.is_complete,
            )
            .select_from(Album)
            .outerjoin(assoc, assoc.c.album_id == Album.id)
            .where(lowered.like(f"%{term.lower()}%"))
            .group_by(Album.id)
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
        """The list rows as dicts, each carrying every id its album holds.

        The ids are a second query for the whole list, for the reason
        `ArtistRepository._rows_to_dicts` gives.
        """
        out = [
            {
                "id": int(row[0]),
                "name": row[1] or "",
                "cover_hash": row[2] or "",
                "track_count": int(row[3] or 0),
                # More than one distinct cover among the tracks that call this album
                # home. Reported as a count rather than a bool so the caller can say
                # how bad it is without asking again.
                "distinct_covers": int(row[4] or 0),
                # And whether the album's own sleeve is a picture none of them carry.
                "album_cover_foreign": bool(row[5]),
                # Stored rather than worked out — see `Album.is_complete`.
                "is_complete": bool(row[6]),
            }
            for row in rows
        ]
        held = external_ids.read_many(self.session, external_ids.ALBUM, [r["id"] for r in out])
        for row in out:
            # Every id the album is known by — see `app.plugins.external_ids`.
            row["external_ids"] = held.get(row["id"], {})
        return out

    # ------------------------------------------------------------------
    # Membership and order
    # ------------------------------------------------------------------

    def track_ids(self, album_id: int) -> List[int]:
        """The album's tracks, **in its running order**."""
        assoc = track_albums_association
        stmt = (
            select(assoc.c.track_id)
            .where(assoc.c.album_id == int(album_id))
            .order_by(assoc.c.position, assoc.c.track_id)
        )
        return [int(row[0]) for row in self.session.execute(stmt).all()]

    def primary_track_ids(self, album_id: int) -> List[int]:
        """The tracks this album is the *primary* album for, in running order.

        The set every artwork judgement is made over. See
        :meth:`_distinct_cover_count`.
        """
        assoc = track_albums_association
        stmt = (
            select(assoc.c.track_id)
            .where(assoc.c.album_id == int(album_id), assoc.c.album_position == 0)
            .order_by(assoc.c.position, assoc.c.track_id)
        )
        return [int(row[0]) for row in self.session.execute(stmt).all()]

    def track_count(self, album_id: int) -> int:
        """How many tracks are on *album_id*."""
        assoc = track_albums_association
        stmt = select(func.count()).select_from(assoc).where(
            assoc.c.album_id == int(album_id)
        )
        return int(self.session.execute(stmt).scalar() or 0)

    def cover_rows(self, album_id: int) -> List[Dict[str, Any]]:
        """``{track_id, title, cover_hash, is_primary}`` for every track on it.

        What the artwork panel draws: every track, whether or not this album is
        its home, with the flag that says which of them the mismatch check
        actually counted. A track shown but not counted is not a defect to hide —
        it is the explanation for why the album's covers differ and nothing is
        wrong.
        """
        assoc = track_albums_association
        stmt = (
            select(
                Track.id,
                Track.title,
                Track.cover_hash,
                assoc.c.album_position,
                assoc.c.position,
            )
            .select_from(assoc)
            .join(Track, Track.id == assoc.c.track_id)
            .where(assoc.c.album_id == int(album_id))
            .order_by(assoc.c.position, assoc.c.track_id)
        )
        return [
            {
                "track_id": int(row[0]),
                "title": row[1] or "",
                "cover_hash": row[2] or "",
                "is_primary": int(row[3] or 0) == 0,
                "position": int(row[4] or 0),
            }
            for row in self.session.execute(stmt).all()
        ]

    def artist_credit_rows(self, album_id: int) -> List[Dict[str, Any]]:
        """Every artist credit on the album, in the order the record makes them.

        One row per (track, artist) pair — an artist on four tracks comes back
        four times. Ordered by the album's running order and then by each
        track's own credit order, which are the two orderings a user arranged by
        hand: that is what makes "who the record introduces first" a fact about
        the album rather than an accident of artist id.

        Not aggregated here, for the reason :meth:`cover_rows` leaves its
        grouping to ``AlbumService.cover_groups``: a repository answers what is
        stored, and the shape a panel wants is not that.
        """
        assoc = track_albums_association
        credits = track_artists_association
        stmt = (
            select(Artist.id, Artist.name, Artist.image_hash)
            .select_from(assoc)
            .join(credits, credits.c.track_id == assoc.c.track_id)
            .join(Artist, Artist.id == credits.c.artist_id)
            .where(assoc.c.album_id == int(album_id))
            # The trailing pair are not decoration. Two tracks can briefly share
            # a position while a reorder is being written, and without a
            # tiebreaker the order the chips come out in would be SQLite's row
            # order — stable in practice, arbitrary in principle, and the whole
            # point here is that the order means something.
            .order_by(
                assoc.c.position,
                assoc.c.track_id,
                credits.c.position,
                credits.c.artist_id,
            )
        )
        return [
            {
                "artist_id": int(row[0]),
                "name": row[1] or "",
                "image_hash": row[2] or "",
            }
            for row in self.session.execute(stmt).all()
        ]

    def identifiable_tracks(
        self, album_id: int, limit: int = 3, keys: Optional[Collection[str]] = None,
    ) -> List[Dict[str, Any]]:
        """The album's tracks that carry an external id, in running order.

        What identifying an album starts from: a recording from it, as some
        catalogue knows it, is an unambiguous handle on which record it is,
        where the name is not. *keys* narrows it as
        `ArtistRepository.identifiable_tracks` does.

        In running order rather than newest-first, unlike the artist version.
        Track one is the most likely to actually be on the release a catalogue
        serves, where a bonus track or a remix at the end may not be.

        More than one, because an entry taken down since it was saved answers
        nothing and the next track is a free second try.
        """
        assoc = track_albums_association
        stmt = (
            select(Track.id, Track.title, Track.duration_seconds)
            .join(assoc, assoc.c.track_id == Track.id)
            .where(
                assoc.c.album_id == int(album_id),
                external_ids.identified_clause(external_ids.TRACK, keys),
            )
            .order_by(assoc.c.position, Track.id)
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

    def set_order(self, album_id: int, track_ids: List[int]) -> bool:
        """Renumber the album densely so it reads in the order given.

        Returns True if anything actually moved. Dense ``0..N-1`` rather than
        leaving gaps, and a single statement per row: the whole list arrives at
        once from a drag, so there is nothing to be gained by being clever about
        which subset changed.

        Ids not on the album are ignored, and ids on the album but missing from
        *track_ids* keep their relative order after the ones given. A stale list
        from a view that has not caught up therefore narrows what gets
        renumbered rather than dropping tracks out of the album.
        """
        assoc = track_albums_association
        stored = {
            int(row[0]): int(row[1])
            for row in self.session.execute(
                select(assoc.c.track_id, assoc.c.position).where(
                    assoc.c.album_id == int(album_id)
                )
            ).all()
        }
        if not stored:
            return False
        current = self.track_ids(album_id)

        wanted = [int(t) for t in track_ids if int(t) in stored]
        seen = set(wanted)
        ordered = wanted + [t for t in current if t not in seen]

        # Compared against the stored *numbers*, not against the id order. The
        # obvious version — "did the sequence change?" — cannot do the two jobs
        # this method also has to do: number an album whose rows are all still at
        # the server default of 0, and close the gaps a removal left behind. In
        # both of those the ids come back in the right order already and an
        # id-order check would return early, leaving the positions wrong.
        if all(stored.get(t) == i for i, t in enumerate(ordered)):
            return False

        for position, track_id in enumerate(ordered):
            self.session.execute(
                assoc.update()
                .where(
                    assoc.c.album_id == int(album_id),
                    assoc.c.track_id == int(track_id),
                )
                .values(position=position)
            )
        self.session.commit()
        return True

    def add_tracks(self, album_id: int, track_ids: List[int]) -> int:
        """Append tracks to the end of the album's running order.

        Skips tracks already on it rather than raising: the composite primary key
        makes a duplicate an error, and adding a track that is already there is
        an ordinary thing for a user to do twice.

        New members land at ``album_position`` one past whatever the track
        already has, so adding a track to a compilation never quietly changes
        which album it belongs to. Making something primary is
        :meth:`~app.services.album_service.AlbumService.set_primary`'s job, and
        it is always a deliberate act.
        """
        assoc = track_albums_association
        existing = set(self.track_ids(album_id))
        fresh = [int(t) for t in track_ids if int(t) not in existing]
        if not fresh:
            return 0

        start = len(existing)
        counts = {
            int(row[0]): int(row[1])
            for row in self.session.execute(
                select(assoc.c.track_id, func.count())
                .where(assoc.c.track_id.in_(fresh))
                .group_by(assoc.c.track_id)
            ).all()
        }
        self.session.execute(
            assoc.insert(),
            [
                {
                    "track_id": track_id,
                    "album_id": int(album_id),
                    "position": start + offset,
                    "album_position": counts.get(track_id, 0),
                }
                for offset, track_id in enumerate(fresh)
            ],
        )
        self.clear_complete([album_id])
        self.session.commit()
        self.session.expire_all()
        return len(fresh)

    def remove_tracks(self, album_id: int, track_ids: List[int]) -> int:
        """Take tracks off an album, leaving the album and the tracks in place.

        Both orderings are repaired afterwards, and the second one is the part
        that is easy to forget: dropping a track's *primary* album leaves its
        remaining albums numbered from 1, so the next read finds no row at
        ``album_position = 0`` and the track has no primary at all. Renumbering
        promotes the next one, which is what "removed from the album it belonged
        to" should mean.
        """
        assoc = track_albums_association
        wanted = [int(t) for t in track_ids]
        if not wanted:
            return 0

        removed = self.session.execute(
            delete(assoc).where(
                assoc.c.album_id == int(album_id), assoc.c.track_id.in_(wanted)
            )
        ).rowcount or 0
        if not removed:
            return 0
        self.clear_complete([album_id])
        self.session.commit()

        self.set_order(album_id, self.track_ids(album_id))
        self._renumber_album_positions(wanted)
        self.session.expire_all()
        return int(removed)

    def _renumber_album_positions(self, track_ids: List[int]) -> None:
        """Close the gaps in each track's own album ordering."""
        assoc = track_albums_association
        for track_id in track_ids:
            remaining = [
                int(row[0])
                for row in self.session.execute(
                    select(assoc.c.album_id)
                    .where(assoc.c.track_id == int(track_id))
                    .order_by(assoc.c.album_position, assoc.c.album_id)
                ).all()
            ]
            for position, album_id in enumerate(remaining):
                self.session.execute(
                    assoc.update()
                    .where(
                        assoc.c.track_id == int(track_id),
                        assoc.c.album_id == album_id,
                    )
                    .values(album_position=position)
                )
        self.session.commit()

    def set_primary(self, track_id: int, album_id: int) -> bool:
        """Make *album_id* the album *track_id* belongs to.

        Renumbers the track's own ordering so the named album is 0 and the rest
        keep their relative order behind it. Returns False when the track is not
        on that album at all, rather than silently adding it — joining an album
        and choosing which one you belong to are two different decisions, and a
        typo in one should not quietly perform the other.
        """
        assoc = track_albums_association
        current = [
            int(row[0])
            for row in self.session.execute(
                select(assoc.c.album_id)
                .where(assoc.c.track_id == int(track_id))
                .order_by(assoc.c.album_position, assoc.c.album_id)
            ).all()
        ]
        if int(album_id) not in current:
            return False
        if current[0] == int(album_id):
            return False

        ordered = [int(album_id)] + [a for a in current if a != int(album_id)]
        for position, aid in enumerate(ordered):
            self.session.execute(
                assoc.update()
                .where(assoc.c.track_id == int(track_id), assoc.c.album_id == aid)
                .values(album_position=position)
            )
        self.session.commit()
        self.session.expire_all()
        return True

    def move_tracks(
        self, from_album_id: int, track_ids: List[int], to_album_id: int
    ) -> int:
        """Move tracks from one album to another, preserving their order.

        The split primitive. Written as bulk statements rather than by walking
        ORM objects, for the reason ``ArtistRepository.reassign_tracks`` is:
        a long record is a hundred round trips to change one join table.

        Both albums are renumbered densely afterwards, so the album left behind
        does not keep a running order full of holes where the moved tracks were.
        """
        assoc = track_albums_association
        wanted = [int(t) for t in track_ids]
        if not wanted or int(from_album_id) == int(to_album_id):
            return 0

        # The order they were in on the source, so they arrive on the target in
        # that order rather than in whatever order the caller listed them.
        ordered = [t for t in self.track_ids(from_album_id) if t in set(wanted)]
        if not ordered:
            return 0

        # A track already on the target would collide on the primary key. Drop
        # its source row and leave the target's alone.
        already = set(self.track_ids(to_album_id))
        positions = {
            int(row[0]): int(row[1])
            for row in self.session.execute(
                select(assoc.c.track_id, assoc.c.album_position).where(
                    assoc.c.album_id == int(from_album_id),
                    assoc.c.track_id.in_(ordered),
                )
            ).all()
        }

        self.session.execute(
            delete(assoc).where(
                assoc.c.album_id == int(from_album_id),
                assoc.c.track_id.in_(ordered),
            )
        )
        moving = [t for t in ordered if t not in already]
        if moving:
            base = len(already)
            self.session.execute(
                assoc.insert(),
                [
                    {
                        "track_id": track_id,
                        "album_id": int(to_album_id),
                        "position": base + offset,
                        # Carried across, so a track that called the source album
                        # home calls the new one home too. Splitting a record in
                        # two must not silently orphan half of it.
                        "album_position": positions.get(track_id, 0),
                    }
                    for offset, track_id in enumerate(moving)
                ],
            )
        self.clear_complete([from_album_id, to_album_id])
        self.session.commit()

        self.set_order(from_album_id, self.track_ids(from_album_id))
        self.set_order(to_album_id, self.track_ids(to_album_id))

        # The statements above went straight to the join table, so any Track the
        # session already had loaded still holds the album collection it was
        # loaded with. The whole application shares one session, so "already
        # loaded" is the normal case.
        self.session.expire_all()
        return len(ordered)

    # ------------------------------------------------------------------
    # Completeness
    # ------------------------------------------------------------------

    def set_complete(self, album_id: int, value: bool) -> bool:
        """Record whether this album holds the whole release. True if it moved.

        Reports whether anything actually changed so a caller can decide not to
        announce a write that did not happen — re-checking a record that was
        already complete is the ordinary case, not the exception, and it should
        not repaint every album tile in the library.
        """
        changed = self.session.execute(
            update(Album)
            .where(Album.id == int(album_id), Album.is_complete != bool(value))
            .values(is_complete=bool(value))
        ).rowcount or 0
        if not changed:
            return False
        self.session.commit()
        self.session.expire_all()
        return True

    def clear_complete(self, album_ids: Iterable[int]) -> int:
        """Un-flag these albums, and say how many were flagged.

        The membership half of the rule, and it lives here rather than in the
        service because this module holds the only statements that add or remove
        a ``track_albums`` row. A flag that says "this is the whole record" stops
        being true the moment the record changes size, and putting the clear
        beside the write is what makes it impossible for a new caller to forget.

        Deliberately **not** called from :meth:`set_order` or
        :meth:`set_primary`: neither adds or removes a member. Re-ordering an
        album is how a complete album gets *confirmed*, and making a track
        primary rearranges the track's own list, not this album's.

        No commit of its own — every caller is mid-statement and commits after.
        """
        wanted = [int(a) for a in album_ids if a]
        if not wanted:
            return 0
        return self.session.execute(
            update(Album)
            .where(Album.id.in_(wanted), Album.is_complete.is_(True))
            .values(is_complete=False)
        ).rowcount or 0

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def orphan_ids(self) -> List[int]:
        """Albums no track is on any more."""
        assoc = track_albums_association
        stmt = select(Album.id).where(
            ~Album.id.in_(select(assoc.c.album_id).distinct())
        )
        return [int(row[0]) for row in self.session.execute(stmt).all()]

    def delete_orphans(self) -> tuple[int, set[str]]:
        """Delete every album no track is on, and report what that retired.

        Returns ``(deleted_count, cover_hashes)``. The hashes are handed back
        rather than acted on here because reclaiming them means touching a second
        database — the cover cache — and a repository does not reach across that
        boundary. The caller runs one sweep for the whole batch.
        """
        ids = self.orphan_ids()
        if not ids:
            return 0, set()

        hashes = {
            row[0]
            for row in self.session.execute(
                select(Album.cover_hash).where(
                    Album.id.in_(ids), Album.cover_hash.is_not(None)
                )
            ).all()
            if row[0]
        }
        self.session.execute(delete(Album).where(Album.id.in_(ids)))
        self.session.commit()
        self.session.expire_all()
        return len(ids), hashes
