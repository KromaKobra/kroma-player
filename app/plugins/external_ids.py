"""External identities — kept by the core, defined by plugins.

A track, an artist or an album can be *identified*: tied to the same thing in an
online catalogue, so that its name, its artwork and its running order can be
fetched again. Which catalogue, and what its ids look like, is a plugin's
business, and the core names none. Where the answers are kept is the core's: one
table per entity (``track_external_ids``, ``artist_external_ids``,
``album_external_ids``). Each row is ``(owner, provider, kind, value)`` with an
opaque value. See ``_ExternalIdRow`` in :mod:`app.models.track`.

A plugin declares the *kinds* it deals in, ``{entity, provider, kind}``, in its
manifest. Everything outside this module speaks in **keys**: ``"provider:kind"``,
one per kind, unique within its entity. A row from a provider carries
``{"external_ids": {key: value}}``, and a view model hands QML the same map.
Nothing outside names a table, and this module reads no manifest. A caller
that needs to know what a kind means asks the declarations it was given and
passes the answer in.

Every caller inherits these rules, which is why they live here and not at
each call site:

1. **A provider never clears an id.** :func:`write` refuses an empty value.
   Clearing is :func:`clear`, which exists for one caller: a person who emptied
   the field in an editor and pressed Save.
2. **A unique id belongs to one row.** :func:`write` will not give a row an id
   another row holds, and :func:`conflict` names that other row so an editor can
   say who. Every kind is unique except an *instrumental* one, which names the
   upload a track's instrumental came from. That can back two tracks, and it
   does not name the track. Only the caller holding the declaration knows
   which is which, so :func:`write` takes ``unique=False`` from it.
3. **Fill, don't overwrite, unless told to.** A passing search result is not
   grounds to overrule an id that was set by hand or confirmed once already, so
   ingestion writes with ``fill_only=True``. An explicit identify or edit does
   not.
4. **Turning a plugin off never deletes its ids.** Nothing here looks at which
   plugins are enabled. Ids an enabled plugin does not declare are hidden by
   whoever draws them. A row's ids go only with the row (ON DELETE CASCADE) or
   through :func:`clear`.

``albums.is_complete`` is deliberately absent. It is derived — a record that a
check against a release happened and what it found — and membership changes
clear it whatever the plugin state. It is not an identity, and nothing here
treats it as one.
"""
from __future__ import annotations

import re
from typing import Any, Collection, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from sqlalchemy import and_, exists, or_, select

from app.models.track import (
    Album, AlbumExternalId, Artist, ArtistExternalId, Track, TrackExternalId,
)

TRACK = "track"
ARTIST = "artist"
ALBUM = "album"
ENTITIES: Tuple[str, ...] = (TRACK, ARTIST, ALBUM)

_OWNERS = {TRACK: Track, ARTIST: Artist, ALBUM: Album}
_ROWS = {TRACK: TrackExternalId, ARTIST: ArtistExternalId, ALBUM: AlbumExternalId}

#: What a provider or a kind may be spelled with. The manifest refuses anything
#: else, and :func:`write` refuses it again, because a ``:`` would make a key
#: ambiguous and the columns are only so wide.
PART_RE = re.compile(r"^[a-z0-9][a-z0-9.-]{0,63}$")

#: The longest value kept. Ids are short. Anything longer is not an id, and a
#: plugin that sends one is refused rather than allowed to fill the library.
MAX_VALUE_LENGTH = 255

# SQLite caps bound parameters, and an album import can ask about a few hundred
# values at once. Chunked like the repositories' own IN lists.
_CHUNK = 500

#: ``(key, value)`` — one id, as lookups and joined reads hand them around.
Pair = Tuple[str, str]


def make_key(provider: str, kind: str) -> str:
    """The one spelling of a kind outside this module: ``"provider:kind"``."""
    return f"{provider}:{kind}"


def split_key(key: str) -> Tuple[str, str]:
    """``("provider", "kind")`` for a key, or ``("", "")`` when it is not one."""
    provider, sep, kind = str(key or "").partition(":")
    if not sep or not provider or not kind:
        return "", ""
    return provider, kind


def valid_key(key: str) -> bool:
    """Whether *key* is spelled as a declared kind's key can be."""
    provider, kind = split_key(key)
    return bool(PART_RE.match(provider) and PART_RE.match(kind))


def _clean(value: Any) -> str:
    cleaned = str(value or "").strip()
    return cleaned if len(cleaned) <= MAX_VALUE_LENGTH else ""


def _held(obj: Any, key: str) -> Any:
    """The id row *obj* holds under *key*, or None."""
    provider, kind = split_key(key)
    for row in getattr(obj, "external_id_rows", None) or ():
        if row.provider == provider and row.kind == kind:
            return row
    return None


# ----------------------------------------------------------------------
# Reading
# ----------------------------------------------------------------------

def read(entity: str, obj: Any) -> Dict[str, str]:
    """Every id the model row *obj* holds, as ``{key: value}``.

    Loads the row's ids through its session the first time it is asked. For a
    page of rows, use :func:`read_many`: one query for all of them.
    """
    if obj is None:
        return {}
    return {
        make_key(row.provider, row.kind): str(row.value)
        for row in getattr(obj, "external_id_rows", None) or ()
        if row.value
    }


def value(entity: str, obj: Any, key: str) -> str:
    """One id off *obj*, or ``""``."""
    row = _held(obj, key) if obj is not None else None
    return str(row.value) if row is not None and row.value else ""


def read_many(session, entity: str, owner_ids: Iterable[int]) -> Dict[int, Dict[str, str]]:
    """``{owner id: {key: value}}`` for every row in *owner_ids* that holds any id.

    Rows with no ids are absent rather than mapped to ``{}``. A caller that
    needs every row present writes ``.get(row_id, {})``.
    """
    model = _ROWS[entity]
    wanted = sorted({int(i) for i in owner_ids if i is not None})
    found: Dict[int, Dict[str, str]] = {}
    for start in range(0, len(wanted), _CHUNK):
        chunk = wanted[start:start + _CHUNK]
        stmt = (
            select(model.owner_id, model.provider, model.kind, model.value)
            .where(model.owner_id.in_(chunk))
            .order_by(model.owner_id, model.provider, model.kind)
        )
        for owner, provider, kind, held in session.execute(stmt):
            if held:
                found.setdefault(int(owner), {})[make_key(provider, kind)] = str(held)
    return found


def outer_join(stmt, entity: str):
    """*stmt* with its owner rows' ids LEFT JOINed on.

    For a query that must stay one SELECT. Each owner row comes back once per
    id it holds, or once with NULLs when it holds none. Select
    :func:`joined_columns` alongside, and fold the rows back together with
    :func:`joined_pair`.
    """
    model = _ROWS[entity]
    return stmt.outerjoin(model, model.owner_id == _OWNERS[entity].id)


def joined_columns(entity: str) -> List[Any]:
    """The id columns an :func:`outer_join` query selects."""
    model = _ROWS[entity]
    return [
        model.provider.label("ext_provider"),
        model.kind.label("ext_kind"),
        model.value.label("ext_value"),
    ]


def joined_pair(row: Any) -> Optional[Pair]:
    """``(key, value)`` from one row of an :func:`outer_join` query, or None."""
    mapping = row._mapping
    provider, kind, held = mapping["ext_provider"], mapping["ext_kind"], mapping["ext_value"]
    if not provider or not held:
        return None
    return make_key(provider, kind), str(held)


def is_identified(entity: str, obj_or_ids: Any) -> bool:
    """Whether *obj* — or an ``{key: value}`` map — holds any id at all.

    Reads the row and ignores the plugins, on purpose. An album keeps its
    release's cover because it was identified, and that stays true while the
    plugin that identified it is off.
    """
    if isinstance(obj_or_ids, Mapping):
        return any(obj_or_ids.values())
    return bool(read(entity, obj_or_ids))


def identified_clause(entity: str, keys: Optional[Collection[str]] = None):
    """A SQL condition: the owner row holds an id — one of *keys*, when given.

    *keys* is how a caller asks for rows a particular provider can use. An id
    of a kind it never declared is no help to it, and neither is an
    instrumental's.
    """
    model = _ROWS[entity]
    owner = _OWNERS[entity]
    condition = exists().where(model.owner_id == owner.id, model.value != "")
    if keys is not None:
        pairs = [split_key(k) for k in keys]
        pairs = [(p, k) for p, k in pairs if p]
        if not pairs:
            return False
        condition = condition.where(
            or_(*[and_(model.provider == p, model.kind == k) for p, k in pairs])
        )
    return condition


# ----------------------------------------------------------------------
# Writing
# ----------------------------------------------------------------------

def conflict(
    session, entity: str, key: str, value: str, *, exclude_id: Optional[int] = None
) -> Any:
    """The *other* row already holding *value* under *key*, or None.

    Ask it only about a unique kind: this does not know which kinds are. The
    caller decides what to do with the answer. An editor names the other row
    and refuses. Ingestion skips quietly.
    """
    provider, kind = split_key(key)
    cleaned = _clean(value)
    if not provider or not cleaned:
        return None
    model = _ROWS[entity]
    owner = _OWNERS[entity]
    query = (
        session.query(owner)
        .join(model, model.owner_id == owner.id)
        .filter(model.provider == provider, model.kind == kind, model.value == cleaned)
    )
    if exclude_id is not None:
        query = query.filter(owner.id != int(exclude_id))
    return query.first()


def write(
    session, entity: str, obj: Any, key: str, value: Any, *,
    fill_only: bool = False, unique: bool = True,
) -> bool:
    """Give *obj* this id. Returns whether anything was written.

    Refuses — returns False and touches nothing — when:

    - *key* is not spelled as a kind can be;
    - *value* is empty, or too long to be an id. Clearing is :func:`clear`,
      never this;
    - *fill_only* is set and *obj* already holds a value for *key*;
    - *unique* is set and another row holds *value* under *key*.

    *unique* comes from the kind's declaration. It is False only for an
    instrumental kind; see rule 2 above.

    Does not commit. The caller's transaction is the one this belongs to.
    """
    cleaned = _clean(value)
    if obj is None or not cleaned or not valid_key(key):
        return False
    held = _held(obj, key)
    current = str(held.value) if held is not None and held.value else ""
    if current == cleaned:
        return False
    if fill_only and current:
        return False
    if unique and conflict(session, entity, key, cleaned, exclude_id=getattr(obj, "id", None)):
        return False
    if held is not None:
        held.value = cleaned
    else:
        provider, kind = split_key(key)
        obj.external_id_rows.append(_ROWS[entity](provider=provider, kind=kind, value=cleaned))
    return True


def clear(entity: str, obj: Any, key: str) -> bool:
    """Remove one id from *obj*. Returns whether there was one.

    For an explicit edit by a person and nothing else. No provider path calls
    this, and none may: an answer that knows nothing about an id is not an
    answer saying there is none.
    """
    held = _held(obj, key) if obj is not None else None
    if held is None:
        return False
    obj.external_id_rows.remove(held)
    return True


# ----------------------------------------------------------------------
# Lookup
# ----------------------------------------------------------------------


def lookup_values(row: Mapping[str, Any]) -> List[Pair]:
    """Every ``(key, value)`` that names this row, its own first.

    Two subsystems share this rule, and neither may state it separately: the
    import list asks it which ids to look the library up under, and
    :meth:`AlbumService.reorder_from_release` asks it which ids a release
    position can be claimed by. If those disagreed, a track could be reported
    "already in your library" by one and unrecognised by the other.

    Own ids first, and that order is the answer to "which one is this row". An
    alternate is a *second name* for the recording, not a better one, and a
    download must fetch what the row's own id says.
    """
    pairs: List[Pair] = []
    own = row.get("external_ids") or {}
    alternates = row.get("alternate_ids") or {}
    if isinstance(own, Mapping):
        for key, raw in own.items():
            mine = str(raw or "").strip()
            if mine and (key, mine) not in pairs:
                pairs.append((key, mine))
    if isinstance(alternates, Mapping):
        for key, extra in alternates.items():
            for alt in extra or []:
                alt = str(alt or "").strip()
                if alt and (key, alt) not in pairs:
                    pairs.append((key, alt))
    return pairs


def lookup(session, entity: str, pairs: Iterable[Pair]) -> Dict[Pair, int]:
    """Map each ``(key, value)`` to the id of the row holding it.

    One query per kind asked about, however many values — never one per row.
    A pair no row holds is simply absent from the answer.
    """
    by_key: Dict[str, set] = {}
    for key, raw in pairs:
        cleaned = _clean(raw)
        if cleaned and valid_key(key):
            by_key.setdefault(key, set()).add(cleaned)

    model = _ROWS[entity]
    found: Dict[Pair, int] = {}
    for key, wanted in by_key.items():
        provider, kind = split_key(key)
        values = sorted(wanted)
        for start in range(0, len(values), _CHUNK):
            chunk = values[start:start + _CHUNK]
            stmt = (
                select(model.value, model.owner_id)
                .where(model.provider == provider, model.kind == kind, model.value.in_(chunk))
                .order_by(model.owner_id)
            )
            for held, owner in session.execute(stmt):
                found.setdefault((key, str(held)), int(owner))
    return found


def lookup_rows(session, rows: Sequence[Mapping[str, Any]], entity: str = TRACK) -> List[Optional[int]]:
    """Positionally, which existing row each description names by id — or None.

    :func:`lookup_values` and :func:`lookup` together, which is what every
    caller wanted from them.
    """
    candidates = [lookup_values(row) for row in rows]
    found = lookup(session, entity, [pair for pairs in candidates for pair in pairs])
    return [
        next((found[pair] for pair in pairs if pair in found), None)
        for pairs in candidates
    ]


def held(session) -> List[Tuple[str, str]]:
    """``(entity, key)`` for every kind at least one row of the library holds.

    One ``DISTINCT`` per entity, answered from the value index. The upgrade
    notice is decided from this: a library carrying ids that no enabled plugin
    declares is a library whose online features went somewhere, and the user
    should be told where.
    """
    found: List[Tuple[str, str]] = []
    for entity in ENTITIES:
        model = _ROWS[entity]
        stmt = select(model.provider, model.kind).distinct().order_by(model.provider, model.kind)
        found.extend((entity, make_key(p, k)) for p, k in session.execute(stmt))
    return found


# ----------------------------------------------------------------------
# Tags
# ----------------------------------------------------------------------

def tags_for(ids: Mapping[str, Any], tags: Mapping[str, str]) -> Dict[str, str]:
    """``{Vorbis comment: value}`` for the ids in *ids* that a declaration mirrors.

    *tags* is ``{key: comment}``, from the declarations of the kinds that name
    a ``writeTag``. The file is told what the row holds and nothing else.
    """
    out: Dict[str, str] = {}
    for key, comment in (tags or {}).items():
        held_value = str((ids or {}).get(key) or "").strip()
        if comment and held_value:
            out[comment] = held_value
    return out
