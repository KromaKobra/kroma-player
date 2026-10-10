"""Fill in what an incoming track's artists are missing, before it is written.

An artist ref arrives from a provider as ``{name, external_ids}`` — a name and,
when the source knew it, the ids that name it in an online catalogue. What it
never carries is a portrait, because a portrait is bytes and a row is JSON. So
every artist created by a fetch or an import would arrive identified but
faceless, and stay that way until somebody opened their page and pressed a
button. A library's worth of blank circles is the result.

This closes that. Given the refs a track came in with, it asks the artwork
providers for the portrait each one is entitled to and hands the refs back with
an ``image_hash`` added, so that ``TrackService._resolve_artists`` can store it
in the same write that creates the artist. Nothing extra is asked of the user
and nothing extra happens afterwards — it is part of pulling the artist in, not
a step after it.

**Why here and not inside TrackService.** ``_resolve_artists`` is the tidier
seam: it already holds the ``Artist`` row, so it can see for itself what is
missing. But it runs *mid-transaction*, with the new ``Track`` already added to
the session and autoflush live. A network call from there would hold an open
SQLite transaction across a fifteen-second socket timeout and an AVIF encode, on
the one ``Session`` the whole application shares — including the UI thread, which
would be reading through it the entire time. Doing the network first and the
write afterwards costs one extra query per track and cannot freeze anything.

**What it costs.** One ``SELECT`` per track, plus one artwork question and one
image download per artist the library has never seen a picture for. For a 200-track
import by 30 artists that is 200 queries and about 30 fetches: every track after
the first by a given artist is free, and the memo is the database row itself, so
it survives a restart and there is no cache to go stale.

**What it never does.** Fail a track. Enrichment is a bonus on a path whose job
is getting the audio in, so every error here is swallowed and the refs come back
exactly as they arrived.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable, Dict, List, Mapping, Optional

from app.plugins import external_ids

if TYPE_CHECKING:  # pragma: no cover - imported for types only
    from app.artwork.cover_service import CoverService
    from app.plugins.providers import ProviderRegistry
    from app.services.track_service import TrackService

logger = logging.getLogger(__name__)

StopCheck = Optional[Callable[[], bool]]


def enrich_artist_refs(
    refs: List[Dict[str, Any]],
    *,
    track_service: "TrackService",
    registry: "Optional[ProviderRegistry]",
    cover_service: "Optional[CoverService]" = None,
    should_stop: StopCheck = None,
) -> List[Dict[str, Any]]:
    """Add an ``image_hash`` to every ref that needs one and can have one.

    Returns a new list in the same order; refs that need nothing are copied
    through untouched. ``registry`` or ``cover_service`` being ``None`` — or no
    enabled plugin offering artist artwork — disables enrichment entirely,
    which is what keeps every headless caller and every existing test behaving
    exactly as it did.
    """
    if not refs or registry is None or cover_service is None:
        return refs
    if not registry.artwork(external_ids.ARTIST):
        return refs

    try:
        names = [str(r.get("name") or "").strip() for r in refs]
        if not any(names):
            return refs

        # One read for the whole track's worth of artists, before any network, so
        # the session is never held open across a socket.
        known = track_service.artist_repo.identities_for_names(names)

        enriched: List[Dict[str, Any]] = []
        for ref, name in zip(refs, names):
            row = known.get(name.lower()) if name else None
            row_ids: Mapping[str, str] = (row or {}).get("external_ids") or {}

            # Nothing to add: this artist is already identified *and* already has
            # a picture. The overwhelmingly common case once a library is warm,
            # and it costs no network and no further query.
            if row and external_ids.is_identified(external_ids.ARTIST, row_ids) and row["image_hash"]:
                enriched.append(ref)
                continue

            # The ids the ref brought, over the ones the row already had.
            # Deliberately no search-by-name fallback: guessing an identity from
            # a name during a bulk import is exactly how a wrong id gets
            # written, and nothing ever overwrites one.
            ids = {**dict(row_ids), **{
                k: v for k, v in (ref.get("external_ids") or {}).items() if v
            }}
            if not external_ids.is_identified(external_ids.ARTIST, ids):
                enriched.append(ref)
                continue

            # Identified, and the picture is already in the cache.
            if row and row["image_hash"]:
                enriched.append({**ref, "external_ids": ids})
                continue

            if should_stop is not None and should_stop():
                enriched.append(ref)
                continue

            image_hash = _portrait_hash(registry, cover_service, ids, should_stop)
            if image_hash:
                enriched.append({**ref, "external_ids": ids, "image_hash": image_hash})
            else:
                enriched.append({**ref, "external_ids": ids})

        return enriched
    except Exception:  # noqa: BLE001
        logger.warning("Artist enrichment failed; keeping refs as they arrived", exc_info=True)
        return refs


def _portrait_hash(
    registry: "ProviderRegistry",
    cover_service: "CoverService",
    ids: Mapping[str, str],
    should_stop: StopCheck = None,
) -> str:
    """Read an artist's portrait into the cover cache. ``""`` if there is none.

    ``fetch_first`` is imported here rather than at module scope because the two
    modules would otherwise import each other: ``stash`` calls this one on its way
    to creating a track, and ``fetch_first`` is defined below that call site.
    """
    from app.importer import stash
    from app.plugins.paths import temp_root
    from app.plugins.providers import first_artwork

    with tempfile.TemporaryDirectory(dir=temp_root(), ignore_cleanup_errors=True) as tmp:
        image_bytes = first_artwork(
            registry, external_ids.ARTIST, ids, Path(tmp),
            lambda urls: stash.fetch_first(urls, should_stop=should_stop),
            should_stop=should_stop,
        )
    if not image_bytes:
        return ""
    cover_hash, _master = cover_service.ingest_source(image_bytes)
    logger.debug("Fetched portrait for %s", ids)
    return cover_hash
