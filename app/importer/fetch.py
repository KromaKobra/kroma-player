"""Fetch a wanted track's audio from an audio source, and stash it.

The Audio step of the import pipeline, for rows that are *wanted* rather than
already on disk: a search result the user pressed Download on, a CSV row, a
track off a release listing. The audio comes from whichever enabled plugin can
supply it; everything after that is the core's, in this order:

1. a temporary directory, made by the host for this one request;
2. ``audio.get`` — the plugin writes one file into that directory and says
   what it is;
3. the returned path is checked to be inside the directory
   (:func:`app.plugins.providers.returned_file`);
4. ``ensure_opus`` — a source that already delivers Opus is copied, anything
   else transcoded, so the vault holds one format whatever the source;
5. the caller's overlay — CSV values, or the artist refs a search result knew —
   with the same precedence v1's downloader had: any non-empty value wins;
6. the core's lyrics lookup;
7. artist enrichment, for portraits;
8. a last :data:`~app.importer.stash.StopCheck`;
9. ``finalize_and_stash`` — the one step that writes.

Everything up to 8 happens in the temporary directory and can be abandoned with
nothing left behind, which is the cancellation rule the local import has always
had. Past 8, the track is written whatever the user clicks.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any, Dict, Mapping, Optional

from app.importer.artist_enrichment import enrich_artist_refs
from app.importer.stash import StopCheck, _stopped, fetch_bytes, fetch_first, finalize_and_stash
from app.importer.transcode import ensure_opus
from app.lyrics.lookup import lookup_lyrics
from app.plugins.paths import temp_root

logger = logging.getLogger(__name__)


class NoAudioSource(RuntimeError):
    """No enabled plugin can fetch this row. Raised before anything starts."""

    def __init__(self, message: str = "") -> None:
        super().__init__(message or (
            "No enabled plugin can fetch audio for this track — install or turn "
            "on one that can in Settings → Plugins"
        ))


def track_info_from_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    """A core row as the ``track_info`` the stash consumes."""
    info: Dict[str, Any] = {
        "title": row.get("title") or "",
        "artists": row.get("artist") or "",
        "album": row.get("album") or "",
        "duration_seconds": int(row.get("duration") or 0) or None,
        "release_date": row.get("release_date"),
        "external_ids": dict(row.get("external_ids") or {}),
        "artist_refs": list(row.get("artist_refs") or []),
    }
    for key in ("isrc", "genres", "bpm", "musical_key", "copyright_text"):
        if row.get(key) not in (None, ""):
            info[key] = row[key]
    return info


def fetch_audio(
    row: Mapping[str, Any],
    registry: Any,
    output_dir: Path,
    *,
    kind: str = "recording",
    should_stop: StopCheck = None,
    on_progress=None,
) -> tuple[Path, Dict[str, Any]]:
    """Steps 2 and 3: ask the first source that can, and check what it returned.

    Shared with the instrumental fetch, which wants the file and nothing else.
    Raises :class:`NoAudioSource` when no enabled source accepts *row*.
    """
    source = registry.audio_source_for(row, kind) if registry is not None else None
    if source is None:
        raise NoAudioSource()
    return source.get(row, output_dir, kind=kind, should_stop=should_stop,
                      on_progress=on_progress)


def fetch_and_stash(
    row: Mapping[str, Any],
    track_service,
    registry: Any,
    cover_service=None,
    metadata_override: Optional[Mapping[str, Any]] = None,
    should_stop: StopCheck = None,
    lyrics_service=None,
    on_progress=None,
) -> Optional[int]:
    """Fetch *row*'s audio and file it. Returns the new track id, or ``None`` if stopped.

    *metadata_override* is what the caller knows better than the source does —
    a CSV row's columns, or a search result's full artist credits. Any
    non-empty value wins; fields it omits fall back to the source's answer.
    Pass it only when non-empty: the "non-empty wins" test lets an empty *list*
    through, so ``[]`` here would erase refs the source supplied.

    Raises :class:`NoAudioSource`, the plugin's own error, or whatever the
    stash raised. Both callers are workers that turn the exception into a
    ``failed`` signal, which is what puts a real reason in front of the user.
    """
    if _stopped(should_stop):
        return None

    with tempfile.TemporaryDirectory(dir=temp_root(), ignore_cleanup_errors=True) as temp_name:
        temp_dir = Path(temp_name)
        output_dir = temp_dir / "out"
        output_dir.mkdir()
        audio_path, described = fetch_audio(
            row, registry, output_dir, should_stop=should_stop, on_progress=on_progress,
        )
        if _stopped(should_stop):
            return None

        opus_path = ensure_opus(audio_path, temp_dir / "opus")
        if _stopped(should_stop):
            return None

        # What the source says it fetched, under what the row asked for: the
        # row's own ids are the ones that were requested, and a source that
        # answers with a different upload of the same recording must not
        # replace them.
        base = described or {}
        track_info = track_info_from_row(base) if base else track_info_from_row(row)
        if base:
            for key in ("title", "artist", "album"):
                if not base.get(key) and row.get(key):
                    track_info["artists" if key == "artist" else key] = row[key]
        track_info["external_ids"] = {
            **dict(base.get("external_ids") or {}),
            **dict(row.get("external_ids") or {}),
        }

        cover_bytes = fetch_first(base.get("artwork_urls") or row.get("artwork_urls") or [])
        if not cover_bytes and (base.get("cover_url") or row.get("cover_url")):
            cover_bytes = fetch_bytes(str(base.get("cover_url") or row.get("cover_url")))

        if metadata_override:
            for key, value in metadata_override.items():
                if value not in (None, ""):
                    track_info[key] = value

        if lrc := lookup_lyrics(track_info.get("title") or "", track_info.get("artists") or ""):
            track_info["lyrics"] = lrc

        # After the override, so a caller that knew better — a search result
        # knows every credited artist's identity, where the fetched file may
        # know the primary's — has already won.
        track_info["artist_refs"] = enrich_artist_refs(
            track_info.get("artist_refs") or [],
            track_service=track_service,
            registry=registry,
            cover_service=cover_service,
            should_stop=should_stop,
        )

        # Last chance: `finalize_and_stash` creates the track row and then
        # moves the file, and stopping between those two leaves the library
        # pointing at nothing. See the note beside `StopCheck`.
        if _stopped(should_stop):
            return None

        return finalize_and_stash(
            opus_path, track_info, cover_bytes, track_service, cover_service,
            lyrics_service=lyrics_service,
        )
