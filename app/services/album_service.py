"""Album service - business logic for albums as entities in their own right.

An album used to be a string on a track. This service is what makes one a thing
you can open, order, identify and repair: rename it, tie it to a catalogue's
entry for the release, hang a cover on it, arrange its running order, or take a
record that is really two records apart.

The same two rules that shape ``ArtistService`` shape this one:

- **A conflict is refused, never resolved silently.** An external id belongs to
  one album, so an id that would collide is reported back by name rather than
  quietly moving an identity from one record to another.
- **Nothing is left orphaned.** Every operation that moves tracks between albums
  sweeps afterwards, so an album that has just been emptied out goes rather than
  lingering as debris.

One deliberate difference from the artist service, and it matters because the
methods share a name: :meth:`AlbumService.split` is **not**
``ArtistService.split``. That one merges — it reuses existing rows by name and
destroys the source. This one divides: it makes a *new* album with the same name
and a different id, moves the named tracks onto it, and leaves the source
standing. Two records genuinely called the same thing is the ordinary case here
and an error there, which is the whole reason ``albums.name`` is not unique.

The artwork rule, stated once here because three callers depend on it:

    A track's artwork belongs to the album it is **primary** for. So the tracks
    of an album are expected to agree only among those it is primary for; a
    track that merely appears on a compilation carries its own record's cover,
    correctly, and is not evidence of anything wrong.

Relationship:
   AlbumViewModels ← AlbumService ← AlbumRepository ← SQLite
                                  ← TrackService (for the shared cover sweep)
"""
from __future__ import annotations

import logging
from typing import Any, Collection, Dict, Iterable, List, NamedTuple, Optional

from app.events.event_bus import EventBus
from app.albums.credits import contributing_artists
from app.models.track import Album
from app.library.recording_match import DURATION_TOLERANCE_SECONDS, title_key
from app.repositories.album_repository import DEFAULT_SORT, AlbumRepository
# The one rule for "which ids name this recording", shared with the import
# list's already-in-library check so the two cannot answer differently about
# the same row.
from app.plugins import external_ids

logger = logging.getLogger(__name__)


class AlbumResult(NamedTuple):
    """The outcome of an album operation.

    ``error`` is empty on success. A non-empty error means **nothing was
    written** — these operations do not half-apply.
    """

    album: Optional[Album]
    error: str


class ReorderResult(NamedTuple):
    """The outcome of arranging an album against an external track list.

    ``matched`` is the tracks that were on the list, in its order; ``unmatched``
    is the ones that were not, which end up after them. ``moved`` is False when
    the album already read that way — an honest "nothing to do" rather than a
    failure, which is why it is separate from ``error``.
    """

    matched: List[int]
    unmatched: List[int]
    moved: bool
    error: str


def artwork_disagrees(cover_hashes: Iterable[str]) -> bool:
    """Whether these covers are more than one picture.

    Pure, module-level and tiny on purpose: the grid's badge, the album page's
    banner, the service's ``needs_attention`` flag and the tests all have to
    mean exactly the same thing by "disagrees", and the cheapest way to
    guarantee that is for all of them to call the same three lines.

    Empty hashes are dropped rather than counted as a distinct picture. A track
    with no artwork at all is a different problem — one Unify can fix rather
    than one it should be triggered by — and counting it would flag every album
    with a single untagged track.
    """
    return len({h for h in cover_hashes if h}) > 1


class AlbumService:
    """Orchestrates album operations with event emission and conflict checks."""

    def __init__(
        self,
        album_repo: AlbumRepository,
        event_bus: EventBus,
        track_service=None,
    ) -> None:
        """Initialize with repository and event bus dependencies.

        ``track_service`` is optional and untyped to avoid an import cycle. It is
        used only to sweep the shared cover cache when a cover is retired —
        album covers, track artwork and artist portraits live in one
        content-addressed store, so deciding a hash is dead means asking about
        all three.
        """
        self.album_repo = album_repo
        self.event_bus = event_bus
        self.track_service = track_service

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def get(self, album_id: int) -> Optional[Album]:
        return self.album_repo.get_by_id(int(album_id))

    def details(self, album_id: int) -> Optional[Dict[str, Any]]:
        """One album as a plain dict, with its counts and its artwork verdict."""
        album = self.get(album_id)
        if album is None:
            return None
        rows = self.album_repo.cover_rows(album.id)
        primary = [r["cover_hash"] for r in rows if r["is_primary"]]
        return {
            "id": int(album.id),
            "name": album.name or "",
            "external_ids": external_ids.read(external_ids.ALBUM, album),
            "cover_hash": album.cover_hash or "",
            "track_count": len(rows),
            "primary_count": len(primary),
            # The album's own sleeve is one of the pictures that has to agree.
            # It is what the grid, the page and every track's own row show for
            # this record, so a sleeve none of the tracks carry is exactly the
            # disagreement a user came here to see.
            "needs_attention": artwork_disagrees(primary + [album.cover_hash or ""]),
            # Read off the row rather than worked out, unlike everything above
            # it — see `Album.is_complete`.
            "is_complete": bool(album.is_complete),
        }

    def list_all(
        self, sort_field: str = DEFAULT_SORT, ascending: bool = False
    ) -> List[Dict[str, Any]]:
        """Every album, with a track count and an "artwork disagrees" flag."""
        rows = self.album_repo.list_all(sort_field, ascending)
        for row in rows:
            row["needs_attention"] = (
                int(row.get("distinct_covers", 0)) > 1
                or bool(row.get("album_cover_foreign", False))
            )
        return rows

    def search(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        rows = self.album_repo.search(query, limit)
        for row in rows:
            row["needs_attention"] = (
                int(row.get("distinct_covers", 0)) > 1
                or bool(row.get("album_cover_foreign", False))
            )
        return rows

    def cover_groups(self, album_id: int) -> List[Dict[str, Any]]:
        """The distinct covers among the album's own tracks, biggest group first.

        The single source for both repair dialogs, which is what keeps the
        "primary members only" rule in one place: a dialog built from this
        cannot accidentally offer to rewrite a track that is only visiting.

        Returns ``[{cover_hash, track_ids, titles, is_album_cover}]``. A group
        with an empty hash is the tracks carrying no artwork at all; it is
        returned rather than hidden, because "fill these in from the album's
        cover" is a reasonable thing to want and the dialog needs the ids to
        offer it.

        The album's **own** sleeve is a group too, even when no track carries it
        and its ``track_ids`` is therefore empty. Without it there would be a
        disagreement the warning can point at and the repair cannot reach: an
        identified album holds artwork that exists only in the cache, and "put
        the real sleeve on these files" is the answer.
        """
        album = self.get(album_id)
        groups: Dict[str, Dict[str, Any]] = {}
        for row in self.album_repo.cover_rows(album_id):
            if not row["is_primary"]:
                continue
            group = groups.setdefault(
                row["cover_hash"],
                {
                    "cover_hash": row["cover_hash"],
                    "track_ids": [],
                    "titles": [],
                    "is_album_cover": False,
                },
            )
            group["track_ids"].append(row["track_id"])
            group["titles"].append(row["title"])

        own = (album.cover_hash or "") if album is not None else ""
        if own:
            groups.setdefault(
                own,
                {"cover_hash": own, "track_ids": [], "titles": [], "is_album_cover": False},
            )["is_album_cover"] = True

        ordered = sorted(
            groups.values(),
            # Most-used cover first, so the dialog's default keeper is the one
            # that changes the fewest files. An empty hash never wins that
            # default, whatever its count: unifying *onto* "no artwork" would
            # strip the album rather than repair it.
            key=lambda g: (bool(g["cover_hash"]), len(g["track_ids"])),
            reverse=True,
        )
        # An identified album is the one case where the fewest-files rule is
        # wrong. Its sleeve came from the release rather than from whatever a
        # particular file happened to be tagged with, so it is the answer even
        # when it is the picture nothing carries yet.
        if own and album is not None and external_ids.is_identified(external_ids.ALBUM, album):
            ordered.sort(key=lambda g: not g["is_album_cover"])
        return ordered

    def contributing_artists(self, album_id: int) -> List[Dict[str, Any]]:
        """Everyone credited on the album, most tracks first.

        ``[{artist_id, name, image_hash, track_count, is_main}]``. Derived every
        time it is asked for, because an album has no artist of its own —
        :meth:`AlbumRepository.lead_artist_id` explains at length why it must
        not grow one.

        Ties go to whoever the record introduces first. That falls out of the
        two ingredients rather than being arranged: the repository hands the
        rows over in running order, a dict keeps insertion order, and
        ``list.sort`` is stable — so sorting on the count *alone* leaves equal
        counts standing in the order they were first seen. Adding a secondary
        key here would not refine that ordering, it would overwrite it.

        Counts every track on the album, including one this album is not
        primary for. A single visiting from another record is still on this
        one, and the track count these chips sit beside already counts it;
        having the two disagree would be worse than either rule alone. The
        artwork code's primary-only rule is not the same question — that one is
        about a claim on a *file*, and a credit is a claim on the album.

        ``is_main`` is the one judgement made here rather than counted: an
        artist on strictly more than half the record. It is what separates the
        two people who made an album from the fourteen who visit it once, and it
        is decided here because this is where the counts are — a GUI is told who
        is main and chooses what that looks like, and one that has never heard
        of the key simply draws everybody the same.

        The denominator is :meth:`AlbumRepository.track_count`, every track on
        the album — including one carrying no credit at all, which no row below
        can see. A track nobody is credited on is still a track somebody else is
        not on.

        Strictly more than half, so a four-track split where each pair takes two
        has no main artist rather than two of them, and so the flag can only
        ever describe a majority. That threshold also buys an ordering the
        caller can lean on: a main artist's count exceeds ``total / 2`` and
        every other count is at most that, so with the list already sorted by
        count the main artists are a *leading run*. A GUI drawing them larger
        never has a big chip appear behind a small one.
        """
        return contributing_artists(
            self.album_repo.artist_credit_rows(album_id),
            self.album_repo.track_count(album_id),
        )

    def visiting_track_ids(self, album_id: int) -> List[int]:
        """Tracks on this album that belong to a different one.

        What the album page needs in order to explain itself: these are exactly
        the tracks whose artwork may differ without anything being wrong.
        """
        rows = self.album_repo.cover_rows(album_id)
        return [r["track_id"] for r in rows if not r["is_primary"]]

    def identify_sources(
        self, album_id: int, limit: int = 3, keys: Optional[Collection[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Tracks this album can be identified from, as rows an identity provider reads.

        Each is ``{track_id, title, album, duration, external_ids}``. Empty when
        none of the album's tracks carries an external id — of one of *keys*,
        when given — the honest answer, and the one the caller turns into a
        refusal rather than a silently useless lookup.
        """
        album = self.get(album_id)
        if album is None:
            return []
        rows = self.album_repo.identifiable_tracks(album_id, limit=limit, keys=keys)
        return [{**row, "album": album.name or ""} for row in rows]

    # ------------------------------------------------------------------
    # Editing
    # ------------------------------------------------------------------

    def update(
        self,
        album_id: int,
        *,
        name: Optional[str] = None,
        ids: Optional[Dict[str, Optional[str]]] = None,
        cover_hash: Optional[str] = None,
        is_complete: Optional[bool] = None,
    ) -> AlbumResult:
        """Apply an edit to one album, refusing any collision outright.

        Only one thing can collide. There is deliberately **no** name check: two
        albums may share a name, which is what makes :meth:`split` expressible
        at all. An external id is different — it names one record in a
        catalogue, and letting two rows claim it would make "refresh from …" a
        coin toss.

        *ids* is ``{"provider:kind": value}`` for only the kinds being changed,
        exactly as for :meth:`ArtistService.update`: empty clears, absent leaves
        alone. ``is_complete`` follows the same rule — ``None`` is "unchanged",
        never "not complete".
        """
        album = self.get(album_id)
        if album is None:
            return AlbumResult(None, "That album no longer exists")

        if name is not None and not name.strip():
            return AlbumResult(None, "An album needs a name")

        for key, value in (ids or {}).items():
            holder = external_ids.conflict(
                self.album_repo.session, external_ids.ALBUM, key, str(value or ""),
                exclude_id=int(album.id),
            )
            if holder is not None:
                return AlbumResult(None, f"That id already belongs to “{holder.name}”")

        retired_hash = ""
        if name is not None:
            album.name = name.strip()
        for key, value in (ids or {}).items():
            if str(value or "").strip():
                external_ids.write(self.album_repo.session, external_ids.ALBUM, album, key, value)
            else:
                external_ids.clear(external_ids.ALBUM, album, key)
        if cover_hash is not None:
            previous = album.cover_hash
            album.cover_hash = cover_hash.strip() or None
            if previous and previous != album.cover_hash:
                retired_hash = previous
        if is_complete is not None:
            # Set by hand here, where the three automatic paths go through
            # `record_release_check`. A user who says a record is complete has
            # looked at a sleeve the application cannot read, and that is better
            # evidence than a release listing nobody has fetched.
            album.is_complete = bool(is_complete)

        self.album_repo.session.commit()

        if retired_hash:
            self._retire_hashes({retired_hash})

        self.event_bus.emit("album.updated", album)
        logger.info("Album updated: %s (ID: %s)", album.name, album.id)
        return AlbumResult(album, "")

    def resolve_external_id(
        self, name: str, artist_id: Optional[int], key: str, value: str
    ) -> Optional[Album]:
        """Record an id against an album that has none of that kind yet.

        The backfill path. Deliberately never *overwrites* an existing id: one
        already on the row was either set by hand or confirmed once already, and
        a passing search result is not grounds to overrule it. Also skipped when
        another album already holds the id.
        """
        if not name or not value:
            return None
        album = self.album_repo.get_or_create(name, artist_id)
        if external_ids.write(
            self.album_repo.session, external_ids.ALBUM, album, key, value, fill_only=True
        ):
            self.album_repo.session.commit()
            logger.debug("Backfilled %s for album %s", key, name)
        return album

    # ------------------------------------------------------------------
    # Completeness
    # ------------------------------------------------------------------

    def split_owned(
        self, album_id: int, owned_track_ids: Iterable[Any]
    ) -> tuple[List[int], List[int]]:
        """Which of these library tracks this album already holds, and which it does not.

        The one statement of "on the album or not", because two features turn on
        it and they must not answer differently. Completeness counts the first
        list; the claim offer is made out of the second — a track the release
        names, the library has, and this record does not hold.

        ``owned_track_ids`` is what ``ImportService.owned_track_ids`` answered
        for a release's rows, so it is positional against those rows and carries
        a ``None`` wherever the library had nothing. Falsy ids are dropped and
        repeats are kept once; what is left comes back in the order it was
        given, which for a release listing is the running order the caller can
        name titles from.
        """
        on_album = set(self.album_repo.track_ids(int(album_id)))
        held: List[int] = []
        missing: List[int] = []
        seen: set[int] = set()
        for value in owned_track_ids:
            if not value:
                continue
            track_id = int(value)
            if track_id in seen:
                continue
            seen.add(track_id)
            (held if track_id in on_album else missing).append(track_id)
        return held, missing

    def record_release_check(
        self, album_id: int, *, release_count: int, owned_track_ids: Iterable[Any]
    ) -> bool:
        """Decide completeness from a listing plus "which of these do we own".

        The import tab's route in. It holds the release's rows and the answer
        ``ImportService.owned_track_ids`` gave for them, and that answer is about
        the **library** — "is this recording here at all". The rule asks a
        narrower question, "is it on *this* record", so the owned ids are
        intersected with the album's own members before anything is decided. A
        single that also lives on its own release does not complete the
        compilation it appears on.

        That intersection is :meth:`split_owned`, and the half it throws away is
        what :meth:`claim_tracks` is offered over.
        """
        on_album, _ = self.split_owned(album_id, owned_track_ids)
        return self._apply_completeness(
            int(album_id),
            release_count=int(release_count),
            matched_on_album=len(on_album),
        )

    def claim_tracks(
        self, album_id: int, track_ids: List[int], release: List[Dict[str, Any]]
    ) -> int:
        """Put library tracks the release names onto this album. Returns how many.

        The other half of a *Find tracks* run. The listing proved these
        recordings belong to this record, and until now that finding was spent
        on marking them "already in your library" and dropping them.

        Two existing calls, and the order between them is the point:

        1. :meth:`add_tracks`, whose semantics are already exactly the ones
           wanted. ``AlbumRepository.add_tracks`` appends at an
           ``album_position`` one past whatever the track already carries, so
           **this album never becomes primary** — the record a claimed track
           belongs to is not touched. The one exception is a track with no album
           at all, which gains one, because a track with albums must have a
           primary.
        2. :meth:`reorder_from_release`, so the claimed tracks land in their real
           slots rather than in a clump at the end. It costs no network call: the
           caller is still holding the listing that got here.

        The order also repairs a trap. ``AlbumRepository.add_tracks`` calls
        ``clear_complete``, so a record the claim has just *finished* would be
        left un-ticked. The reorder ends in :meth:`_apply_completeness` with an
        exact match count, which re-decides it.

        A reorder that refuses — an empty listing, or nothing on the album
        matched it — falls through to :meth:`record_release_check` so the flag is
        still re-recorded rather than left wherever ``clear_complete`` put it.
        The only evidence there is are the tracks just claimed, which the caller
        took off the listing; anything the release names and the album holds
        besides them is unproven, so that check can only come out negative. That
        is the honest answer: a listing nothing matched is not evidence a record
        is whole.
        """
        added = self.add_tracks(int(album_id), [int(t) for t in track_ids or []])
        rows = list(release or [])
        if self.reorder_from_release(int(album_id), rows).error:
            self.record_release_check(
                int(album_id),
                release_count=len(rows),
                owned_track_ids=[int(t) for t in track_ids or []],
            )
        logger.info("Claimed %d track(s) for album %s", added, album_id)
        return added

    def _apply_completeness(
        self, album_id: int, *, release_count: int, matched_on_album: int
    ) -> bool:
        """Store what one release listing says about this album. True if it moved.

        The single door all three automatic paths go through — the reorder, the
        album search, and the import run that follows it — so they cannot come to
        different conclusions about what "complete" means. The same argument
        ``ImportService.owned_track_ids`` makes for "already in your library".

        Three conditions, and the third is the one that is easy to leave out:

        1. **The listing said something.** A release of length zero is a failed
           fetch or an id pointing at nothing, and it is evidence of neither
           answer. The stored flag is left exactly as it was rather than cleared
           — a network hiccup must not un-tick a record.
        2. **Every track the release names is on this album.**
        3. **And the album holds no more than that.** An album longer than the
           release is not the release; a bonus track, a mis-filed song or a
           duplicate is in there, and a checkmark claiming "this is the whole
           record" would be reading past the problem rather than showing it.

        A check that comes out negative **clears** a flag that was set. A listing
        just read is better evidence than one read a month ago.
        """
        if release_count <= 0:
            return False
        local = self.album_repo.track_count(album_id)
        complete = matched_on_album == release_count == local
        if not self.album_repo.set_complete(album_id, complete):
            return False
        album = self.get(album_id)
        if album is not None:
            self.event_bus.emit("album.updated", album)
        logger.info(
            "Album %s marked %s from a release of %d (%d on the album, %d local)",
            album_id, "complete" if complete else "incomplete",
            release_count, matched_on_album, local,
        )
        return True

    def seed_cover_from_tracks(self, album_id: int) -> str:
        """Take the album's cover from the first of its tracks that has one.

        The unidentified half of the artwork rule on the ``Album`` model.
        Refuses to run when the album *is* identified: an external id means the
        stored cover came from the record itself, and a file's tag should not
        overrule it. ``apply_unified_cover`` does overrule it, and the difference
        is that a user picked that one by looking at it.

        **Its own** tracks, meaning the ones it is primary for — the same set
        every other artwork judgement here is made over. A visiting track carries
        the artwork of the record it belongs to, correctly, so seeding from one
        would hand this album a sleeve none of its own tracks carry: the exact
        disagreement ``artwork_disagrees`` then flags, manufactured by the code
        meant to prevent it.

        Returns the hash it settled on, or ``""`` when it did nothing — a
        refusal included. Answering with the *current* hash there would tell the
        caller something changed when nothing had.
        """
        album = self.get(album_id)
        if album is None or external_ids.is_identified(external_ids.ALBUM, album):
            return ""
        for row in self.album_repo.cover_rows(album_id):
            if row["is_primary"] and row["cover_hash"]:
                result = self.update(album_id, cover_hash=row["cover_hash"])
                return result.album.cover_hash or "" if result.album else ""
        return ""

    def set_order(self, album_id: int, track_ids: List[int]) -> bool:
        """Arrange the album's running order. What a drag commits to."""
        if not self.album_repo.set_order(album_id, track_ids):
            return False
        self.event_bus.emit("album.reordered", int(album_id))
        return True

    def reorder_from_release(
        self, album_id: int, tracks: List[Dict[str, Any]]
    ) -> ReorderResult:
        """Arrange the album to read in the order the release's *tracks* are in.

        The running order a library assembles for itself is arrival order — the
        sequence songs happened to be downloaded in — and for a record built one
        track at a time that is close to random. The release itself knows the
        real order; this is what applies it.

        It takes the release's **rows** rather than bare ids because a position
        now has several candidate ids *and* a title, and passing three parallel
        lists would be the same thing said worse.

        Two passes, and the order between them is the whole correctness
        argument:

        1. **By id.** For each release track in order, the first of its
           :func:`external_ids.lookup_values` that maps to a track on this album
           and is not already claimed. Identity is still the external id,
           through the **same call the import tab's already-in-library check
           runs on**, so what
           counts as "the library already has this recording" is decided in one
           place and the two cannot drift apart. A track the library holds but
           that is not on *this* album is ignored: a single that also appears on
           a compilation must not be dragged out of the record it belongs to.
        2. **By title**, over what pass 1 left. Exact, ``strip().casefold()`` —
           the comparison identity providers are expected to use for names, and
           for the same reason: anything looser attaches the wrong recording and
           nothing downstream would catch it. Safe here only because the scope
           is this album's own remaining tracks — roughly a dozen titles against
           a dozen the user has already asserted belong to this record. It is
           what makes a reorder work at all on an album of local rips, which
           carry no external id to be matched by.

        Pass 2 runs **after** pass 1 completes, never interleaved: a track whose
        title happens to match position 3 would otherwise claim it before the
        track whose *id* matches position 3 is ever considered. An id is
        evidence; a title is a guess.

        **An ambiguous title is not a match.** A title held by more than one
        unclaimed track — or by more than one unmatched release position — is
        dropped from both indexes, so a record carrying a version and its remix
        under one name falls through to "unmatched" rather than having one of
        them picked arbitrarily.

        Tracks the release does not name — bonus tracks, a mis-filed song, a live
        version — are **not** passed to :meth:`set_order`, which is exactly how
        they end up last: ``AlbumRepository.set_order`` keeps ids it was not
        given after the ones it was, in their existing relative order. They come
        back in ``unmatched`` so a caller can say which tracks moved that way
        rather than leaving the user to notice.

        Refused rather than silently doing nothing when *nothing* matched: an
        id pointing at the wrong record is what still lands here, and "no
        tracks on this album are on that release" is the useful thing to say
        about it.
        """
        if self.track_service is None:
            return ReorderResult([], [], False, "Track lookup is unavailable")

        current = self.album_repo.track_ids(album_id)
        if not current:
            return ReorderResult([], [], False, "That album has no tracks")

        rows = list(tracks or [])
        candidates = [external_ids.lookup_values(row) for row in rows]
        on_album = set(current)
        found = external_ids.lookup(
            self.album_repo.session, external_ids.TRACK,
            [pair for pairs in candidates for pair in pairs],
        )

        # One slot per release position, filled by whichever pass claims it.
        # Keeping the gaps is what lets pass 2 run over what pass 1 left without
        # losing where in the running order each of those positions was.
        matched: List[Optional[int]] = [None] * len(rows)
        seen: set[int] = set()
        for index, pairs in enumerate(candidates):
            for pair in pairs:
                track_id = found.get(pair)
                if track_id is None or track_id not in on_album or track_id in seen:
                    continue
                seen.add(int(track_id))
                matched[index] = int(track_id)
                break

        self._match_remaining_by_title(rows, matched, current, seen)

        ordered = [track_id for track_id in matched if track_id is not None]
        if not ordered:
            return ReorderResult(
                [], list(current), False,
                "None of this album's tracks are on that release",
            )

        unmatched = [t for t in current if t not in seen]
        moved = self.set_order(album_id, ordered)
        # A reorder has already done the expensive half of the completeness
        # check, and done it more exactly than the import tab can: `ordered` is
        # release tracks matched to tracks *on this album*, which is the very
        # thing `record_release_check` has to reconstruct with an intersection.
        # Free evidence, so it would be strange to throw it away.
        self._apply_completeness(
            album_id, release_count=len(rows), matched_on_album=len(ordered)
        )
        return ReorderResult(ordered, unmatched, moved, "")

    def _match_remaining_by_title(
        self,
        rows: List[Dict[str, Any]],
        matched: List[Optional[int]],
        current: List[int],
        seen: set[int],
    ) -> None:
        """Pass 2 of :meth:`reorder_from_release`, filling *matched* in place.

        Only ever asked about what pass 1 left over on both sides — the release
        positions still empty, and the album's tracks still unclaimed. That
        narrow scope is what makes a title comparison safe at all: a dozen
        titles against a dozen the user has already said belong to this record.

        Titles compare through :func:`title_key`, so a ``(feat. …)`` the release
        spells out and the library does not stops being a reason to fail.

        **Duration breaks a tie rather than the tie ending it.** A title held by
        two of the album's unclaimed tracks used to match neither, because
        picking one arbitrarily would write a wrong running order. But a version
        and its remix filed under one name differ in *length*, and when exactly
        one candidate is within :data:`DURATION_TOLERANCE_SECONDS` of the
        release's, that is not a guess. Two candidates both in range still
        matches nothing — there the length has stopped distinguishing them and
        there is nothing left to decide on.

        No artist test, unlike the library-wide check: the user has already
        asserted these tracks belong to this record, and that assertion is the
        scope this whole pass rests on.
        """
        open_rows = [index for index, track_id in enumerate(matched) if track_id is None]
        remaining = [t for t in current if t not in seen]
        if not open_rows or not remaining:
            return

        def _seconds(value: Any) -> int:
            try:
                return int(value or 0)
            except (TypeError, ValueError):
                return 0

        # Every unclaimed track under its key, so a duplicated title arrives as
        # a list to choose from rather than as one row silently overwriting
        # another.
        by_title: Dict[str, List[tuple[int, int]]] = {}
        for track in self.track_service.get_tracks_by_ids(remaining):
            key = title_key(str(getattr(track, "title", "") or ""))
            if not key:
                continue
            by_title.setdefault(key, []).append(
                (int(track.id), _seconds(getattr(track, "duration_seconds", 0)))
            )

        wanted = [title_key(str(rows[index].get("title") or "")) for index in open_rows]
        # A title the *release* uses twice names nothing here either. Both
        # positions want the same track and only one can have it, and there is
        # no evidence for which — the mirror of the album-side rule below.
        repeated = {key for key in wanted if wanted.count(key) > 1}

        for index, key in zip(open_rows, wanted):
            if not key or key in repeated:
                continue
            candidates = [
                (track_id, seconds)
                for track_id, seconds in by_title.get(key, [])
                if track_id not in seen
            ]
            if not candidates:
                continue
            if len(candidates) > 1:
                length = _seconds(rows[index].get("duration"))
                candidates = [
                    (track_id, seconds)
                    for track_id, seconds in candidates
                    if length and seconds
                    and abs(seconds - length) <= DURATION_TOLERANCE_SECONDS
                ]
                if len(candidates) != 1:
                    continue
            track_id = candidates[0][0]
            seen.add(track_id)
            matched[index] = track_id

    def set_primary(self, track_id: int, album_id: int) -> bool:
        """Make *album_id* the album *track_id* belongs to."""
        if not self.album_repo.set_primary(track_id, album_id):
            return False
        album = self.get(album_id)
        if album is not None:
            self.event_bus.emit("album.updated", album)
        return True

    def add_tracks(self, album_id: int, track_ids: List[int]) -> int:
        """Put tracks on the end of an album's running order."""
        added = self.album_repo.add_tracks(album_id, track_ids)
        if added:
            album = self.get(album_id)
            if album is not None:
                self.event_bus.emit("album.updated", album)
        return added

    def remove_tracks(self, album_id: int, track_ids: List[int]) -> int:
        """Take tracks off an album, and sweep it away if that emptied it."""
        removed = self.album_repo.remove_tracks(album_id, track_ids)
        if not removed:
            return 0
        self.sweep_orphans()
        album = self.get(album_id)
        if album is not None:
            self.event_bus.emit("album.updated", album)
        return removed

    # ------------------------------------------------------------------
    # Repair
    # ------------------------------------------------------------------

    def split(self, album_id: int, track_ids: List[int]) -> AlbumResult:
        """Move some of an album's tracks onto a new album of the same name.

        The answer to "these are two different records that happen to share a
        title" — a deluxe edition filed with the original, or two pressings whose
        artwork differs. The new row carries the same ``name`` and a new ``id``,
        which is the whole reason ``albums.name`` is not unique.

        The new album gets **no** external ids, whatever the source had. An id
        names one record; if the user has just said these are two records, at
        most one of them can still be the one that id refers to, and guessing
        which would silently mis-identify the other.

        Its cover is seeded from the tracks that moved — which is exactly the
        artwork that differed, and so exactly what the user was looking at when
        they decided to split.
        """
        album = self.get(album_id)
        if album is None:
            return AlbumResult(None, "That album no longer exists")

        wanted = [int(t) for t in track_ids or []]
        if not wanted:
            return AlbumResult(None, "Choose at least one track to split out")

        members = set(self.album_repo.track_ids(album_id))
        moving = [t for t in wanted if t in members]
        if not moving:
            return AlbumResult(None, "None of those tracks are on this album")
        if len(moving) == len(members):
            return AlbumResult(
                None, "That is every track — there would be nothing left behind"
            )

        target = self.album_repo.create(name=album.name)
        self.album_repo.session.flush()
        self.album_repo.move_tracks(int(album_id), moving, int(target.id))
        self.seed_cover_from_tracks(int(target.id))

        self.sweep_orphans()

        self.event_bus.emit("album.created", target)
        # Only if it survived. A split cannot empty the source — the "that is
        # every track" guard above sees to it — but the sweep is shared code and
        # saying so here would be trusting it to stay that way.
        if self.album_repo.get_by_id(int(album_id)) is not None:
            self.event_bus.emit("album.updated", album)
        logger.info(
            "Split album %s (%s) — %d track(s) moved to new album %s",
            album_id, album.name, len(moving), target.id,
        )
        return AlbumResult(target, "")

    def apply_unified_cover(
        self, album_id: int, cover_hash: str, track_ids: List[int]
    ) -> int:
        """Point the named tracks at one cover, and reclaim what that displaced.

        The database half of Unify. The *file* half — re-embedding the picture
        into each Opus file — belongs to the worker, because it is slow and
        because the embedded picture is what survives a rebuilt cache. This runs
        afterwards, on the ids the worker reports it actually rewrote, so a run
        that failed on one file does not claim it succeeded.

        ``track_ids`` is always an explicit list and never defaults to "all of
        them". A track this album is not primary for is entitled to different
        artwork, so which tracks change is the user's judgement, made in the
        dialog, and this method's job is to carry it out rather than to widen it.

        An **empty** list is a legitimate run rather than nothing to do: the
        album's own sleeve is one of the pictures that has to agree, and when it
        is the only one out of step there is no file to rewrite and the row is
        still wrong. Returning early on it left that repair unreachable — the
        dialog's button read "Rewrite 0 tracks" and did nothing. Only a missing
        ``cover_hash`` is refused; there is nothing to point anything at.
        """
        if not cover_hash:
            return 0

        repo = self.album_repo
        session = repo.session
        from app.models.track import Track

        displaced: set[str] = set()
        changed = 0
        for track_id in (int(t) for t in track_ids):
            track = session.get(Track, track_id)
            if track is None or track.cover_hash == cover_hash:
                continue
            if track.cover_hash:
                displaced.add(track.cover_hash)
            track.cover_hash = cover_hash
            changed += 1

        # The sleeve follows the keeper, identified or not. That is the one place
        # this differs from `seed_cover_from_tracks`, which refuses an identified
        # album — and the difference is who is deciding. Seeding is the
        # application guessing from whatever a file happened to be tagged with,
        # and an id means the release's own artwork is already known to be
        # better. Getting here means a user looked at the pictures side by side
        # and picked one, which is better evidence than either.
        album = self.get(album_id)
        if album is not None and album.cover_hash != cover_hash:
            if album.cover_hash:
                displaced.add(album.cover_hash)
            album.cover_hash = cover_hash

        if album is None and not changed:
            return 0
        session.commit()

        # After the commit, so a hash still referenced by a row this method just
        # wrote is counted as live. Retiring first would recount the old state.
        if displaced:
            self._retire_hashes(displaced)

        for track_id in (int(t) for t in track_ids):
            track = session.get(Track, track_id)
            if track is not None:
                self.event_bus.emit("track.updated", track)
        if album is not None:
            self.event_bus.emit("album.updated", album)
        logger.info("Unified artwork on album %s — %d track(s) rewritten", album_id, changed)
        return changed

    def sweep_orphans(self) -> int:
        """Delete albums no track is on, reclaiming their covers."""
        ids = self.album_repo.orphan_ids()
        deleted, retired = self.album_repo.delete_orphans()
        if retired:
            self._retire_hashes(retired)
        for album_id in ids:
            # Per id, unlike ArtistService.sweep_orphans, which announces
            # nothing and leaves an open editor looking at a row that has gone.
            # That is a documented gap there; there is no reason to reproduce it.
            self.event_bus.emit("album.deleted", int(album_id))
        if deleted:
            logger.info("Removed %d album(s) no track is on any more", deleted)
        return deleted

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _retire_hashes(self, hashes: set[str]) -> None:
        """Let the cover cache reclaim artwork nothing points at any more.

        Routed through TrackService because the decision is not this service's
        to make: the store is shared, so an album cover's hash may still be very
        much alive as some track's artwork or some artist's portrait.
        """
        if not hashes or self.track_service is None:
            return
        try:
            self.track_service.vacuum_covers(hashes)
        except Exception:  # noqa: BLE001 — a cache sweep must never fail an edit
            logger.warning("Could not sweep retired album artwork", exc_info=True)
