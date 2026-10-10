"""Artist service - business logic for artists as entities in their own right.

An artist used to be a name on a track and nothing else. This service is what makes
one a thing you can open, correct and identify: rename it, tie it to an online
catalogue's entry for it, hang a portrait on it, or take a badly-formed one apart.

Two rules shape the whole module:

- **A conflict is refused, never resolved silently.** ``artists.name`` is UNIQUE and
  an external id belongs to one artist, so a rename or an id that would collide is
  reported back by name rather than raising an IntegrityError at the user or
  quietly merging two artists they did not ask to merge. The one place a merge
  *does* happen is :meth:`split`, and only because the user confirmed the names.
- **Nothing is left orphaned.** Every operation that moves tracks between artists
  sweeps afterwards, so the row that has just been emptied out goes rather than
  lingering as debris. That accretion is most of how the malformed names arrived.

Relationship:
   ArtistViewModels ← ArtistService ← ArtistRepository ← SQLite
                                    ← TrackService (for the shared cover sweep)
"""
from __future__ import annotations

import logging
import re
from typing import Any, Collection, Dict, List, NamedTuple, Optional

from app.events.event_bus import EventBus
from app.models.track import Artist
from app.plugins import external_ids
from app.repositories.artist_repository import DEFAULT_SORT, ArtistRepository

logger = logging.getLogger(__name__)


# Separators that mean "this is really several artists", used to *propose* a split
# — never to perform one automatically.
#
# Automatic splitting is the trap here. "AC/DC", "Above & Beyond" and "Hall & Oates"
# are single artists containing separators, and a library that silently tore them
# apart on import would be worse off than one carrying the occasional "person1/person2".
# So this only ever fills in a suggestion the user confirms or edits.
#
# Note the shape of the abbreviations: ``\bfeat\b\.?`` and not ``\bfeat\.?\b``. The
# trailing ``\b`` in the second cannot match between "." and a space — both are
# non-word — so the optional dot is given up and "feat. B" splits into "feat" and
# ". B". Asserting the boundary *before* the dot and then consuming it optionally
# also keeps "feature" from matching, which dropping the ``\b`` entirely would not.
_SPLIT_PATTERN = re.compile(
    r"\s*(?:/|;|\||\bfeat\b\.?|\bft\b\.?|\bvs\b\.?|\bwith\b|&|\bx\b|,)\s*",
    re.IGNORECASE,
)

# What makes a name *look* like it needs attention. Deliberately narrower than the
# split pattern: a comma already splits correctly on the way in, and "&" is common
# enough in legitimate names that flagging it would cry wolf.
_SUSPECT_PATTERN = re.compile(
    r"(/|;|\||\bfeat\b|\bft\b|\bvs\b)",
    re.IGNORECASE,
)


class ArtistResult(NamedTuple):
    """The outcome of an artist operation.

    ``error`` is empty on success. A non-empty error means **nothing was written** —
    these operations do not half-apply.
    """

    artist: Optional[Artist]
    error: str


def propose_split(name: str) -> List[str]:
    """Suggest the artists a compound name is probably hiding.

    Returns a list of at least two names, or an empty list when the name does not
    look compound. Order and spelling are preserved — this proposes, the user
    disposes.
    """
    if not name:
        return []
    parts = [part.strip() for part in _SPLIT_PATTERN.split(name)]
    parts = [part for part in parts if part]
    # Deduplicate while keeping order: "A & A" is one artist typed twice.
    seen: set[str] = set()
    unique: List[str] = []
    for part in parts:
        key = part.lower()
        if key not in seen:
            seen.add(key)
            unique.append(part)
    return unique if len(unique) > 1 else []


def looks_malformed(name: str) -> bool:
    """Whether *name* carries a separator suggesting it is several artists."""
    return bool(name) and bool(_SUSPECT_PATTERN.search(name))


class ArtistService:
    """Orchestrates artist operations with event emission and conflict checks."""

    def __init__(
        self,
        artist_repo: ArtistRepository,
        event_bus: EventBus,
        track_service=None,
    ) -> None:
        """Initialize with repository and event bus dependencies.

        ``track_service`` is optional and untyped to avoid an import cycle. It is
        used only to sweep the shared cover cache when a portrait is retired —
        artist portraits and album art live in one content-addressed store, so
        deciding a hash is dead means asking about tracks and playlists too.
        """
        self.artist_repo = artist_repo
        self.event_bus = event_bus
        self.track_service = track_service

    # ------------------------------------------------------------------
    # Reading
    # ------------------------------------------------------------------

    def get(self, artist_id: int) -> Optional[Artist]:
        return self.artist_repo.get_by_id(int(artist_id))

    def details(self, artist_id: int) -> Optional[Dict[str, Any]]:
        """One artist as a plain dict, track count included."""
        artist = self.get(artist_id)
        if artist is None:
            return None
        return {
            "id": int(artist.id),
            "name": artist.name or "",
            "external_ids": external_ids.read(external_ids.ARTIST, artist),
            "image_hash": artist.image_hash or "",
            "track_count": self.artist_repo.track_count(artist.id),
            "needs_attention": looks_malformed(artist.name or ""),
        }

    def list_all(
        self, sort_field: str = DEFAULT_SORT, ascending: bool = False
    ) -> List[Dict[str, Any]]:
        """Every artist, with a track count and a "looks compound" flag.

        ``sort_field`` is one of :data:`~app.repositories.artist_repository.SORT_FIELDS`;
        anything else falls back to the default rather than raising.
        """
        rows = self.artist_repo.list_all(sort_field, ascending)
        for row in rows:
            row["needs_attention"] = looks_malformed(row["name"])
        return rows

    def search(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        rows = self.artist_repo.search(query, limit)
        for row in rows:
            row["needs_attention"] = looks_malformed(row["name"])
        return rows

    # ------------------------------------------------------------------
    # Editing
    # ------------------------------------------------------------------

    def update(
        self,
        artist_id: int,
        *,
        name: Optional[str] = None,
        ids: Optional[Dict[str, Optional[str]]] = None,
        image_hash: Optional[str] = None,
    ) -> ArtistResult:
        """Apply an edit to one artist, refusing any collision outright.

        *ids* is ``{"provider:kind": value}`` for only the kinds being changed.
        An empty value clears that id; this is an edit, and an edit is the one
        thing allowed to. A key absent from *ids* is left exactly as it is,
        which is what lets an editor that cannot show a kind — its plugin is off
        — save everything else without touching it.

        Every conflict is checked *before* anything is written, so a save that
        would collide leaves the artist exactly as it was rather than applying
        the half of the edit that happened to be legal.
        """
        artist = self.get(artist_id)
        if artist is None:
            return ArtistResult(None, "That artist no longer exists")

        if name is not None:
            cleaned = name.strip()
            if not cleaned:
                return ArtistResult(None, "An artist needs a name")
            # Case-insensitive, because the collision that actually bites is
            # "daft punk" against "Daft Punk" — the exact-match UNIQUE index would
            # let that through and leave the catalogue split across two rows.
            clash = self.artist_repo.get_by_name_insensitive(cleaned)
            if clash is not None and int(clash.id) != int(artist.id):
                return ArtistResult(
                    None, f"“{clash.name}” already exists — split or rename that one instead"
                )

        for key, value in (ids or {}).items():
            holder = external_ids.conflict(
                self.artist_repo.session, external_ids.ARTIST, key, str(value or ""),
                exclude_id=int(artist.id),
            )
            if holder is not None:
                return ArtistResult(None, f"That id already belongs to “{holder.name}”")

        retired_hash = ""
        if name is not None:
            artist.name = name.strip()
        for key, value in (ids or {}).items():
            if str(value or "").strip():
                external_ids.write(
                    self.artist_repo.session, external_ids.ARTIST, artist, key, value
                )
            else:
                external_ids.clear(external_ids.ARTIST, artist, key)
        if image_hash is not None:
            previous = artist.image_hash
            artist.image_hash = image_hash.strip() or None
            if previous and previous != artist.image_hash:
                retired_hash = previous

        self.artist_repo.session.commit()

        if retired_hash:
            self._retire_hashes({retired_hash})

        self.event_bus.emit("artist.updated", artist)
        logger.info("Artist updated: %s (ID: %s)", artist.name, artist.id)
        return ArtistResult(artist, "")

    def identify_sources(
        self, artist_id: int, limit: int = 3, keys: Optional[Collection[str]] = None,
    ) -> List[Dict[str, Any]]:
        """Tracks this artist can be identified from, as rows an identity provider reads.

        Each is ``{track_id, title, artist, duration, external_ids}`` — the
        recording as the library knows it, credited to this artist by name. What
        the provider does with them is its own business: a catalogue that
        credits channels on a recording can say which one carries this name.

        Empty when none of the artist's tracks carries an external id — of one
        of *keys*, when given — the honest answer, and the one the caller turns
        into a refusal rather than a silently useless lookup.
        """
        artist = self.get(artist_id)
        if artist is None:
            return []
        rows = self.artist_repo.identifiable_tracks(artist_id, limit=limit, keys=keys)
        return [{**row, "artist": artist.name or ""} for row in rows]

    def resolve_external_id(self, name: str, key: str, value: str) -> Optional[Artist]:
        """Record an id against an artist that has none of that kind yet.

        The backfill path: a provider already knows which catalogue entry each
        of a track's artists is, so an artist created from one arrives
        identified instead of waiting to be looked up by hand later.

        Deliberately never *overwrites* an existing id. One already on the row
        was either set by hand or confirmed once already, and a passing search
        result is not grounds to overrule it. Also skipped when another artist
        already holds the id, which is how two similarly-named rows avoid ending
        up claiming the same entry.
        """
        if not name or not value:
            return None
        artist = self.artist_repo.get_or_create(name)
        if external_ids.write(
            self.artist_repo.session, external_ids.ARTIST, artist, key, value, fill_only=True
        ):
            self.artist_repo.session.commit()
            logger.debug("Backfilled %s for artist %s", key, name)
        return artist

    # ------------------------------------------------------------------
    # Repair
    # ------------------------------------------------------------------

    def split(self, artist_id: int, names: List[str]) -> ArtistResult:
        """Replace one artist with several, moving every track across.

        This is the answer to a row like ``person1/person2``: the tracks are
        re-credited to the real artists and the compound row goes.

        Names that already exist are *reused* rather than duplicated, which quietly
        gives this the merge case as well — splitting ``person1/person2`` in a
        library that already knows ``person1`` folds into that artist and their
        catalogue comes out whole. It is also why renaming into an existing name is
        refused above: split is the operation that is allowed to combine artists,
        because it is the one where the user named the targets.
        """
        artist = self.get(artist_id)
        if artist is None:
            return ArtistResult(None, "That artist no longer exists")

        cleaned: List[str] = []
        seen: set[str] = set()
        for raw in names or []:
            part = str(raw).strip()
            if part and part.lower() not in seen:
                seen.add(part.lower())
                cleaned.append(part)

        if not cleaned:
            return ArtistResult(None, "Name at least one artist to split into")
        if len(cleaned) == 1 and cleaned[0].lower() == (artist.name or "").lower():
            return ArtistResult(None, "That is the same artist — nothing to split")

        targets = [self.artist_repo.get_or_create(part) for part in cleaned]
        target_ids = [int(t.id) for t in targets]
        if target_ids == [int(artist.id)]:
            return ArtistResult(None, "That is the same artist — nothing to split")

        # The source is passed through when the user named it, which means "keep
        # this one as well" — see reassign_tracks. Removing it because it happens
        # to be the row being split would delete an artist they asked to keep.
        moved = self.artist_repo.reassign_tracks(int(artist.id), target_ids)

        # If the source was not among the targets it now credits nothing, so the
        # ordinary orphan sweep takes it — along with any *other* artist this
        # happened to empty out.
        retired = self.sweep_orphans()

        # Only announce a deletion that happened. When the user kept the source
        # among the targets it is still there, and telling the open editor its
        # artist had gone would close a page that is looking at a live row.
        if self.artist_repo.get_by_id(int(artist_id)) is None:
            self.event_bus.emit("artist.deleted", int(artist_id))
        for target in targets:
            self.event_bus.emit("artist.updated", target)
        logger.info(
            "Split artist %s into %s — %d track(s) moved, %d row(s) removed",
            artist_id, cleaned, moved, retired,
        )
        return ArtistResult(targets[0] if targets else None, "")

    def sweep_orphans(self) -> int:
        """Delete artists no track credits, reclaiming their portraits."""
        deleted, retired = self.artist_repo.delete_orphans()
        if retired:
            self._retire_hashes(retired)
        if deleted:
            logger.info("Removed %d artist(s) no track credits any more", deleted)
        return deleted

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _retire_hashes(self, hashes: set[str]) -> None:
        """Let the cover cache reclaim portraits nothing points at any more.

        Routed through TrackService because the decision is not this service's to
        make: the store is shared with album art, so a portrait's hash may still be
        very much alive as some album's cover.
        """
        if not hashes or self.track_service is None:
            return
        try:
            self.track_service.vacuum_covers(hashes)
        except Exception:  # noqa: BLE001 — a cache sweep must never fail an edit
            logger.warning("Could not sweep retired artist artwork", exc_info=True)
