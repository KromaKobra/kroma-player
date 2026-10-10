"""Field catalog — the single source of truth for what a track exposes.

This module deliberately contains no presentation. No labels, no colours, no
widths, no icons, no renderers: those are the GUI's business and live in the GUI
package. What is here is the *data* contract — which fields exist, what type
each carries, whether it can be sorted or filtered on, and how expensive it is
to read.

``Tier`` says where a field comes from, which is what lets the row cache fetch
only what was actually asked for. Every ``SCALAR`` field arrives in one SELECT
because they are all columns on ``tracks``; ``artist`` and ``genres`` each cost
a join, so they are only read when a delegate actually binds them.

This module imports no Qt. Qt role numbers are an adapter detail and live in
``roles``, so the repository layer can describe the library without depending
on the toolkit that happens to draw it.

To add a field: append one ``Field`` here. That is the whole change — roles,
role names, the QML catalog and the cache all follow from it.
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum

from sqlalchemy.orm import InstrumentedAttribute

from app.models.track import Track


class Tier(IntEnum):
    """Where a field's value comes from, and therefore what it costs to read."""

    ROW = 0      # supplied by the list model itself; never read from the database
    SCALAR = 1   # a column on `tracks` — the whole tier arrives in one SELECT
    ARTISTS = 2  # needs the track_artists join
    GENRES = 3   # needs the track_genres join
    ALBUMS = 4   # needs the track_albums join


@dataclass(frozen=True)
class Field:
    """One field a track exposes.

    ``key`` is both the role name seen from QML and the key used in the row
    cache, so there is a single spelling for every field everywhere.
    """

    key: str
    type: str                                  # "string" | "int" | "bool" | "seconds" | "yyyymmdd" | "map"
    tier: Tier
    column: InstrumentedAttribute | None = None  # the `tracks` column, for SCALAR fields
    sortable: bool = False
    filterable: bool = False


# Declaration order fixes the role numbers, so only ever append.
FIELDS: tuple[Field, ...] = (
    # --- identity and per-row state: the model supplies these, never the DB ---
    Field("trackId",       "int",      Tier.ROW),
    Field("uid",           "int",      Tier.ROW),
    Field("isSelected",    "bool",     Tier.ROW),

    # --- columns on `tracks` ---
    Field("title",         "string",   Tier.SCALAR, Track.title,            sortable=True, filterable=True),
    # Was a `tracks` column until albums became rows of their own; it stays in
    # this slot because declaration order fixes the role numbers, and a GUI that
    # cached "album is role N" must keep getting the album at role N. Only the
    # tier moved, which is invisible from above: `tiers_for_keys` now fetches it
    # through the join, and the value arriving is the *primary* album's name.
    Field("album",         "string",   Tier.ALBUMS,                         sortable=True, filterable=True),
    Field("duration",      "seconds",  Tier.SCALAR, Track.duration_seconds, sortable=True, filterable=True),
    Field("bpm",           "int",      Tier.SCALAR, Track.bpm,              sortable=True, filterable=True),
    Field("key",           "string",   Tier.SCALAR, Track.musical_key,      sortable=True, filterable=True),
    Field("date",          "yyyymmdd", Tier.SCALAR, Track.release_date,     sortable=True, filterable=True),
    Field("stashedDate",   "yyyymmdd", Tier.SCALAR, Track.created_at,       sortable=True, filterable=True),
    Field("isrc",          "string",   Tier.SCALAR, Track.isrc,             sortable=True, filterable=True),
    # Every external id the track carries, as ``{"provider:kind": value}`` —
    # only the kinds an enabled plugin declares. No single column: the row
    # cache reads them through `app.plugins.external_ids`, and the list model
    # filters them against what is declared at paint time. See IDENTITY_KEYS.
    Field("externalIds",   "map",      Tier.SCALAR),
    Field("copyright",     "string",   Tier.SCALAR, Track.copyright_text),
    Field("coverHash",     "string",   Tier.SCALAR, Track.cover_hash),

    # --- joined relationships, one query each, fetched only on demand ---
    Field("artist",        "string",   Tier.ARTISTS, sortable=True, filterable=True),
    Field("genres",        "string",   Tier.GENRES,  filterable=True),

    # --- model-supplied fields used by lists whose rows are not library tracks
    #     (web search results, import candidates). Never fetched from the DB. ---
    Field("coverUrl",      "string",   Tier.ROW),
    Field("coverFilePath", "string",   Tier.ROW),
    Field("notFound",      "bool",     Tier.ROW),
    Field("isDownload",    "bool",     Tier.ROW),
    Field("importState",   "string",   Tier.ROW),
    Field("sourceFormat",  "string",   Tier.ROW),

    # --- rows that are an artist rather than a track ---
    #
    # Search can return both at once, and a result list is one model. Rather than
    # a second model overlaid in the GUI, an artist arrives as a row whose
    # `trackId` is null and whose values come from these — the same mechanism a
    # web search result already uses, for the same reason: there is no `tracks`
    # row behind it to read.
    #
    # `rowKind` is the discriminator, and it is the only one: a delegate should
    # branch on it rather than inferring from a missing trackId, which is also
    # true of an undownloaded web result.
    Field("rowKind",          "string", Tier.ROW),
    Field("artistId",         "int",    Tier.ROW),
    Field("artistName",       "string", Tier.ROW),
    Field("artistImageHash",  "string", Tier.ROW),
    Field("artistTrackCount", "int",    Tier.ROW),

    # --- rows that are an album rather than a track ---
    #
    # The same mechanism again, for the same reason, under `rowKind == "album"`.
    # Appended rather than filed beside `album` above, because declaration order
    # fixes the role numbers and a GUI that cached one must keep getting it.
    #
    # Note `albumCoverHash` rather than reusing `coverHash`: that one is a
    # SCALAR read off the `tracks` row, and a row with no track behind it cannot
    # answer it from the database. The value is mirrored onto it anyway — see
    # `SearchViewModel._set_album_result` — so a delegate that has never heard of
    # album rows still draws a sleeve.
    Field("albumId",          "int",    Tier.ROW),
    Field("albumName",        "string", Tier.ROW),
    Field("albumCoverHash",   "string", Tier.ROW),
    Field("albumTrackCount",  "int",    Tier.ROW),
    Field("albumIsComplete",  "bool",   Tier.ROW),

    # --- what an external id is worth on screen ---
    #
    # Worked out from `externalIds` and the enabled plugins' declarations each
    # time they are painted, never stored: turning a plugin off has to take the
    # link and the label away at once, without a cache to go stale. SCALAR so a
    # miss on a library track fetches the ids they are derived from.
    Field("externalUrl",   "string",   Tier.SCALAR),
    Field("sourceLabel",   "string",   Tier.SCALAR),
)


FIELDS_BY_KEY: dict[str, Field] = {f.key: f for f in FIELDS}

# The SCALAR fields that are a column of their own. The rest of the tier is the
# identity group below, which the same SELECT fills by other means.
SCALAR_FIELDS: tuple[Field, ...] = tuple(
    f for f in FIELDS if f.tier is Tier.SCALAR and f.column is not None
)

# Fields derived from a track's external ids. ``externalIds`` is what the row
# cache holds; the other two are computed from it, and all three are filtered
# against the declarations of enabled plugins when they are read.
IDENTITY_FIELD_KEYS: frozenset[str] = frozenset({"externalIds", "externalUrl", "sourceLabel"})
FETCHABLE_KEYS: frozenset[str] = frozenset(f.key for f in FIELDS if f.tier is not Tier.ROW)

# The ROW fields every track list answers for itself, library or not.
IDENTITY_KEYS: frozenset[str] = frozenset({"trackId", "uid", "isSelected"})

# The rest of the ROW tier: fields only a list whose rows are *not* library
# tracks can supply, because there is no database row behind them. A model that
# serves such rows must answer these from its own data; every other model
# returns None and the GUI falls through to what it does have.
LOCAL_ROW_KEYS: frozenset[str] = frozenset(
    f.key for f in FIELDS if f.tier is Tier.ROW and f.key not in IDENTITY_KEYS
)

# One field per fetchable tier, used by the row cache to tell whether that tier
# has already been loaded for a given track. Tiers fill atomically, so any one
# of a tier's keys answers for all of them.
TIER_PROBE: dict[Tier, str] = {}
for _f in FIELDS:
    if _f.tier is not Tier.ROW:
        TIER_PROBE.setdefault(_f.tier, _f.key)


def tiers_for_keys(keys: frozenset[str] | set[str]) -> set[Tier]:
    """Return the fetch tiers needed to satisfy *keys*, ignoring ROW fields."""
    tiers = set()
    for key in keys:
        field = FIELDS_BY_KEY.get(key)
        if field is not None and field.tier is not Tier.ROW:
            tiers.add(field.tier)
    return tiers


def catalog() -> list[dict]:
    """The field catalog as plain dicts, for handing to QML.

    This is what replaces the old column definitions: a GUI is told *what
    exists*, and decides for itself what to call it and how to draw it.
    """
    return [
        {
            "key": f.key,
            "type": f.type,
            "sortable": f.sortable,
            "filterable": f.filterable,
        }
        for f in FIELDS
    ]
