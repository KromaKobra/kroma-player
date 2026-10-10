"""Stash an import (or fetched audio) into the library.

Two entry points:

- ``stash_imported_track`` — local-file import: transcodes the user's own file to
  Opus, applies the metadata and artwork of whatever identified it — or, when
  nothing did, its own tags and its own embedded picture — and moves the result
  into the managed library.

- ``finalize_and_stash`` — shared tail used by both the importer and
  :mod:`app.importer.fetch`: given a ready Opus file and a metadata dict it
  handles cover processing, track-row creation, Opus tagging, and the final
  library move. This is the Finalize step of the import pipeline, and the only
  step that writes.

Artwork arrives as a list of URLs on the row, best first, because a plugin knows
which pictures exist and the host is the one that fetches: :func:`fetch_first`
walks them and takes the first that resolves. That walk is also why a prefetch
needs no plugin call at all — the URLs are already on the row.
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import urllib.request
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional

from app.artwork.cover_service import CoverService
from app.artwork.embedded import read_embedded_art
from app.importer.artist_enrichment import enrich_artist_refs
from app.importer.transcode import ensure_opus
from app.importer.tagging import build_picture_block_from_bytes, tag_opus_file
from app.services.lyrics_service import SOURCE_SEARCH, LyricsService
from app.services.track_service import TrackService
from app.utils.library_paths import normalize_local_id, track_path

logger = logging.getLogger(__name__)

# How far into a stash the user is still allowed to change their mind.
#
# Everything before `finalize_and_stash` happens inside a temporary directory:
# the transcode, the artwork fetch, the metadata. Abandoning any of it leaves
# nothing behind — the temp dir goes and the library never hears about the file.
#
# `finalize_and_stash` is the other side of that line. It creates the track row
# and *then* moves the audio into the vault, so an interruption between those
# two writes leaves the library holding a track whose file does not exist —
# which no later import would notice or repair. So the last check is the one
# just before it, and once past, a stash finishes whatever the user has clicked.
#
# This is the same bargain the identify worker makes: stop at the next boundary,
# not at the next instruction.
StopCheck = Optional[Callable[[], bool]]


def _stopped(should_stop: StopCheck) -> bool:
    return should_stop is not None and should_stop()


def finalize_and_stash(
    opus_path: Path,
    track_info: Dict[str, Any],
    cover_bytes: Optional[bytes],
    track_service: TrackService,
    cover_service: Optional[CoverService] = None,
    lyrics_service: Optional[LyricsService] = None,
) -> int:
    """Cover processing → create track row → record lyrics → tag → move to library.

    Args:
        opus_path: A ready, untagged Opus file (typically inside a temp dir).
        track_info: Metadata dict consumed by ``create_track_with_relationships``
            and ``tag_opus_file``. Must not yet contain ``local_id`` or
            ``cover_hash`` — those are filled in here.
        cover_bytes: Raw bytes of any Pillow-readable image (JPEG/PNG/WebP/AVIF),
            or ``None`` to skip artwork. When provided, the bytes are
            downscaled to at most 800x800, encoded as a square AVIF master for
            embedding, and the 128 / 512 thumbnail tiers are written to the
            cover cache.
        track_service: Creates the track row with artist/genre resolution.
        cover_service: Builds + caches the AVIF thumbnails. When omitted the
            track is stashed without artwork.
        lyrics_service: Records ``track_info["lyrics"]`` against the new track in
            the library. The file gets them either way, from ``tag_opus_file``
            below; without this the library simply has no row for them and the
            Now Playing tab would fall back to reading the file. See
            ``docs/architecture/decisions/0008-lyrics-are-mirrored-into-the-database.md``.

    Returns:
        The newly created track id.
    """
    picture_block: Optional[str] = None
    cover_hash: Optional[str] = None

    if cover_bytes and cover_service is not None:
        # ingest_source: crops to square, downscales to ≤800px, encodes AVIF
        # master for embedding, and writes 128 / 512 thumbnail tiers to cache.
        cover_hash, master_avif = cover_service.ingest_source(cover_bytes)
        picture_block = build_picture_block_from_bytes(master_avif, mime="image/avif")

    track_info = {**track_info, "cover_hash": cover_hash}
    track_id = track_service.create_track_with_relationships(track_info)

    # Record the lyrics against the new track. This runs on the stash worker,
    # which is one of the serialised writers already holding the shared
    # TrackService — see docs/architecture/cross-cutting/threading.md — so it is
    # allowed to write here, and it is the only moment at which the new track id
    # and the lyrics the search found are both in hand.
    #
    # `write_tag=False` because this is the one call site where the inference
    # from `source` is wrong: the audio is still in a temp directory under its
    # download name, and `tag_opus_file` below writes the whole block — LYRICS
    # included — a few lines later.
    if lyrics_service is not None and track_info.get("lyrics"):
        try:
            lyrics_service.save(
                track_id, track_info["lyrics"], SOURCE_SEARCH, write_tag=False,
            )
        except Exception:  # noqa: BLE001 — lyrics must not cost the user the track
            logger.warning("Could not store lyrics for track %s", track_id, exc_info=True)

    track_info["local_id"] = normalize_local_id(track_id)
    # print(track_info)
    tag_opus_file(
        opus_path, track_info, picture_block=picture_block, id_tags=track_service.id_tags(),
    )
    _stash_to_library(opus_path, track_id)
    return track_id


def stash_imported_track(
    result: Dict[str, Any],
    track_service: TrackService,
    cover_service: Optional[CoverService] = None,
    should_stop: StopCheck = None,
    *,
    cover_bytes: Optional[bytes] = None,
    registry: Optional[Any] = None,
    lyrics_service: Optional[LyricsService] = None,
) -> Optional[int]:
    """Persist one import row. Returns the new track id, or ``None``.

    Args:
        result: An import row — identified by a provider, paired by hand, or
            built from the file's own probe when nothing identified it. Carries
            ``source_path``, any ``external_ids``, and the metadata to apply.
        track_service: Used to create the track row with artist/genre resolution.
        cover_service: Builds + caches the AVIF cover master. When omitted, the
            track is stashed without artwork.
        should_stop: Polled at the boundaries described above. Returns ``None``
            without having written anything when it says yes — which reads the
            same as a failed stash to a caller that only looks at the return, so
            the worker asks the flag itself rather than inferring.
        cover_bytes: Artwork already in hand, which skips the fetch below. The
            import view model prefetches the next row's cover while this one
            transcodes, so by the time a stash starts its artwork is usually
            already here. Absent, this fetches its own and nothing is lost but
            the overlap.
        registry: The providers, for fetching portraits of the artists this
            track introduces. ``None`` — no plugins — and the artists arrive as
            names, which is all a local file ever knew about them.
    """
    source = Path(result.get("source_path", ""))
    if not source.exists():
        raise FileNotFoundError(f"Import source no longer exists: {source}")
    if _stopped(should_stop):
        return None

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as temp_name:
        temp_dir = Path(temp_name)
        opus_path = ensure_opus(source, temp_dir)
        if _stopped(should_stop):
            return None

        # The row's own artwork first, best first. A provider puts the largest
        # picture it knows of at the front; the list-sized thumbnail is the last
        # resort, because a master built from it would be visibly soft.
        if not cover_bytes:
            cover_bytes = fetch_first(result.get("artwork_urls") or [])
        if not cover_bytes and result.get("cover_url"):
            cover_bytes = fetch_bytes(str(result["cover_url"]))
        # Nothing online — no match, no provider, or a fetch that failed. The
        # file may well carry its own picture, and the import list has been
        # showing it all along; keeping it is what stops a matched-nothing row
        # arriving in the library blank.
        if not cover_bytes:
            cover_bytes = read_embedded_art(source)

        if _stopped(should_stop):
            return None

        transcoded_from = (
            None
            if source.suffix.lower() == ".opus"
            else source.suffix.lower().lstrip(".")
        )

        track_info: Dict[str, Any] = {
            "title": result.get("title") or source.stem,
            "artists": result.get("artist") or "",
            "album": result.get("album") or "",
            "duration_seconds": int(result.get("duration") or 0),
            "release_date": result.get("release_date"),
            "external_ids": dict(result.get("external_ids") or {}),
            "transcoded_from": transcoded_from,
            # The structured form of "artists", when the identification knew
            # it. Carrying it means an imported track arrives with its artists
            # already identified, rather than as names to be matched by hand
            # later — and enriched, with the portrait their ids point at, so
            # they arrive with a face too. Both are no-ops without providers.
            "artist_refs": enrich_artist_refs(
                result.get("artist_refs") or [],
                track_service=track_service,
                registry=registry,
                cover_service=cover_service,
                should_stop=should_stop,
            ),
        }

        return finalize_and_stash(
            opus_path, track_info, cover_bytes, track_service, cover_service,
            lyrics_service=lyrics_service,
        )


def _stash_to_library(opus_path: Path, track_id: int) -> Path:
    """Move a tagged Opus file to its canonical managed location."""
    destination = track_path(track_id, category="mix")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(opus_path), str(destination))
    return destination


def fetch_first(urls: Iterable[str], *, should_stop: StopCheck = None) -> Optional[bytes]:
    """The first of *urls* that resolves, as bytes. ``None`` if none does.

    The walk every artwork answer takes. A provider lists pictures best first,
    and not every one of them exists — a catalogue's largest tier is missing
    for plenty of older entries, and a miss there is routine. Worse, a miss is
    sometimes an HTTP 404 whose *body is a placeholder image*, so this has to
    key off :func:`fetch_bytes` returning ``None`` (it lets ``urlopen`` raise on
    the 404 and swallows it) and never off "some bytes came back".
    """
    tried = 0
    for url in urls or []:
        if not url or (should_stop is not None and should_stop()):
            continue
        tried += 1
        found = fetch_bytes(str(url), expect_misses=True)
        if found:
            return found
    if tried:
        # Reported once, here, rather than per miss — see `fetch_bytes`.
        logger.warning("None of %d artwork URLs resolved", tried)
    return None


def fetch_bytes(url: str, *, expect_misses: bool = False) -> Optional[bytes]:
    """Download remote bytes, returning ``None`` on any failure.

    *expect_misses* lowers a failure to debug. :func:`fetch_first` sets it
    because a miss there is routine — a list of candidates exists precisely
    because the best of them may not — and logging each as a warning would bury
    the failures genuinely worth seeing.
    """
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "KromaPlayer"})
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
            return response.read()
    except Exception as exc:  # noqa: BLE001 — art is optional; never block a stash
        log = logger.debug if expect_misses else logger.warning
        log("Could not fetch cover art from %s: %s", url, exc)
        return None
