"""Shared, id-keyed row cache.

Every track list in the application reads through one instance of this. Two
properties follow from that, and both matter:

- **Keyed by track id, not row index.** Re-sorting or re-filtering a query
  swaps its id vector and repaints straight out of the cache, with no new
  reads. Nothing here has to know that a row moved.
- **Shared across lists.** A track visible in the queue, in a search result and
  in a playlist at the same time is read once, for all three.

Fields are fetched by *tier* (see ``fields.Tier``). A tier fills atomically, so
the presence of any one of its keys on a row proves the whole tier was loaded —
that is how ``get`` can distinguish "not fetched yet" from "fetched, and the
column is NULL". The two are very different to a list model: one means ask
again, the other means draw a blank.
"""
from __future__ import annotations

import logging
from collections import OrderedDict
from typing import TYPE_CHECKING, Any, Iterable

from app.library.fields import TIER_PROBE, Tier, tiers_for_keys

if TYPE_CHECKING:
    from app.repositories.track_repository import TrackRepository

logger = logging.getLogger(__name__)

# Returned by `get` when a field has not been read from the database yet.
# A distinct sentinel because None is a legitimate value for most columns.
MISS: Any = object()

DEFAULT_CAPACITY = 4096


class TrackRowCache:
    """Bounded LRU of track rows, filled on demand a tier at a time."""

    def __init__(self, repository: "TrackRepository", capacity: int = DEFAULT_CAPACITY) -> None:
        self._repo = repository
        self._capacity = max(1, capacity)
        self._rows: "OrderedDict[int, dict[str, Any]]" = OrderedDict()

    # -- reading -------------------------------------------------------

    def get(self, track_id: int, key: str) -> Any:
        """Return the cached value for *key*, or ``MISS`` if it is not loaded.

        Cheap enough to call from ``data()`` on every paint: one dict lookup,
        no I/O, ever.
        """
        row = self._rows.get(track_id)
        if row is None or key not in row:
            return MISS
        self._rows.move_to_end(track_id)
        return row[key]

    def has_tier(self, track_id: int, tier: Tier) -> bool:
        """True when *tier* has already been loaded for this track."""
        probe = TIER_PROBE.get(tier)
        row = self._rows.get(track_id)
        return bool(probe and row is not None and probe in row)

    # -- filling -------------------------------------------------------

    def fetch(self, ids: Iterable[int], keys: Iterable[str]) -> set[int]:
        """Load whatever *keys* require for *ids*, and return the ids updated.

        Only the tiers that are actually missing are queried, and only for the
        ids missing them — so re-requesting a page that is already warm costs
        nothing but a couple of set operations.
        """
        id_list = [int(i) for i in ids]
        if not id_list:
            return set()

        updated: set[int] = set()
        for tier in tiers_for_keys(set(keys)):
            wanted = [i for i in id_list if not self.has_tier(i, tier)]
            if not wanted:
                continue
            try:
                updated |= self._fetch_tier(tier, wanted)
            except Exception:
                # A failed page read must not take a paint down with it; the
                # rows stay missing and will be requested again on next scroll.
                logger.exception("Row cache fetch failed for tier %s", tier.name)
        return updated

    def _fetch_tier(self, tier: Tier, ids: list[int]) -> set[int]:
        if tier is Tier.SCALAR:
            fetched = self._repo.fetch_scalar_rows(ids)
            return self._merge(fetched)
        if tier is Tier.ARTISTS:
            return self._merge({tid: {"artist": v} for tid, v in self._repo.fetch_artists(ids).items()})
        if tier is Tier.GENRES:
            return self._merge({tid: {"genres": v} for tid, v in self._repo.fetch_genres(ids).items()})
        if tier is Tier.ALBUMS:
            return self._merge({tid: {"album": v} for tid, v in self._repo.fetch_albums(ids).items()})
        return set()

    def _merge(self, fetched: dict[int, dict[str, Any]]) -> set[int]:
        for track_id, values in fetched.items():
            row = self._rows.get(track_id)
            if row is None:
                row = {}
                self._rows[track_id] = row
            row.update(values)
            self._rows.move_to_end(track_id)
        self._evict()
        return set(fetched)

    def _evict(self) -> None:
        while len(self._rows) > self._capacity:
            self._rows.popitem(last=False)

    def durations(self, ids: Iterable[int]) -> dict[int, int]:
        """Return ``{track_id: duration_seconds}`` for *ids*.

        Deliberately not cached here: this exists for list totals, which read
        every id in a list at once. Filling the LRU with a whole library's rows
        to add up one column would evict everything the user can actually see.
        """
        id_list = sorted({int(i) for i in ids if int(i) > 0})
        if not id_list:
            return {}
        try:
            return self._repo.fetch_durations(id_list)
        except Exception:
            logger.exception("Duration total fetch failed")
            return {}

    # -- invalidation --------------------------------------------------

    def invalidate(self, track_id: int) -> None:
        """Drop one track, so its next read comes from the database."""
        self._rows.pop(int(track_id), None)

    def clear(self) -> None:
        self._rows.clear()

    # -- introspection (tests, diagnostics) ----------------------------

    def __len__(self) -> int:
        return len(self._rows)

    @property
    def capacity(self) -> int:
        return self._capacity
