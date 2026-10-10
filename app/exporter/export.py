"""The export engine.

Free of Qt so it can be driven straight from a test, and free of SQLAlchemy so
it can be driven from a worker thread: everything it needs about a track
arrives as a frozen :class:`ExportItem` snapshotted on the UI thread. The
:mod:`app.exporter.workers` module is the only thing standing between this and
the view model.

Two rules shape the whole file:

* **The database is what gets written.** Both formats re-apply title, artist and
  album from the snapshot, so a track whose file tags drifted from its library
  record still exports correctly.
* **Nothing half-written is ever visible.** Every file is built at a ``.part``
  path and moved into place with :func:`os.replace`, so a cancel, a crash or a
  full disk leaves the destination holding only finished files.
"""
from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

from app.exporter import encode, naming
from app.exporter.formats import FORMAT_MP3, extension, normalize
from app.exporter.tagging import cover_jpeg, write_mp3_tags
from app.importer.tagging import update_opus_tags
from app.services.audio_import_service import AudioImportService
from app.utils.binaries import ffmpeg_path

logger = logging.getLogger(__name__)

# A run that has failed this many times in a row has hit something systemic — a
# full disk, a disconnected drive — not a bad track. Without the cap, one of
# those grinds through the whole playlist emitting the same error per file.
CONSECUTIVE_FAILURE_LIMIT = 3

# Written and deleted to answer "can we actually create files here?", which on
# Windows no amount of inspecting permissions will tell you.
_PROBE_NAME = ".kroma-export-check"


class ExportError(RuntimeError):
    """The export could not run, or could not continue."""


class ExportCancelled(RuntimeError):
    """The export was stopped by the user partway through."""


@dataclass(frozen=True)
class ExportItem:
    """One track's worth of everything the engine needs.

    A snapshot, not an ORM object: the worker runs on another thread and the app
    shares a single SQLAlchemy session.
    """
    track_id: int
    title: str
    artist: str
    album: str
    # The track's words, read out of the `lyrics` table when the item is built.
    # Carried on the snapshot rather than looked up here for the usual reason:
    # this runs on a worker thread and the table is on the shared session.
    lyrics: str = ""


@dataclass(frozen=True)
class ExportRequest:
    items: tuple[ExportItem, ...]
    destination: Path       # the folder the user picked
    playlist_name: str      # the subfolder created inside it
    fmt: str
    embed_art: bool = True


@dataclass
class ExportOutcome:
    exported: int = 0
    skipped: list[tuple[int, str]] = field(default_factory=list)
    destination: Optional[Path] = None   # the subfolder actually written to


def preflight(request: ExportRequest) -> None:
    """Raise :class:`ExportError` if the request cannot possibly succeed.

    Everything checkable is checked here, before a single file is written, so a
    misconfigured export fails as a message rather than as a half-populated
    folder.
    """
    if not request.items:
        raise ExportError("There are no tracks to export")

    # ``Path("")`` collapses to ``Path(".")``, so an unset destination arrives
    # here looking like the working directory — which exists, is writable, and
    # is absolutely not where anyone meant their music to go. Both spellings are
    # refused: nobody picks the process's cwd from a folder chooser.
    destination = Path(request.destination)
    if str(destination) in ("", "."):
        raise ExportError("Choose a folder to export to")
    if not destination.is_dir():
        raise ExportError(f"No such folder: {destination}")

    probe = destination / _PROBE_NAME
    try:
        probe.write_bytes(b"")
    except OSError as exc:
        raise ExportError(f"Cannot write to {destination} — {exc.strerror or exc}") from exc
    finally:
        try:
            probe.unlink()
        except OSError:
            pass

    if normalize(request.fmt) == FORMAT_MP3 and ffmpeg_path() is None:
        raise ExportError(
            "ffmpeg is not installed — .mp3 export needs it. Export as .opus "
            "instead, or install ffmpeg and put it on PATH."
        )


def export_tracks(
    request: ExportRequest,
    *,
    on_stage: Optional[Callable[[str], None]] = None,
    on_progress: Optional[Callable[[int, int], None]] = None,
    should_cancel: Optional[Callable[[], bool]] = None,
) -> ExportOutcome:
    """Copy every item in *request* into its own folder under the destination.

    Returns once every item has been written or skipped. A track whose audio is
    missing, or which ffmpeg refuses, is recorded in
    :attr:`ExportOutcome.skipped` and does not stop the run — one unplayable
    track should not cost the user the other forty-nine.
    """
    preflight(request)

    fmt = normalize(request.fmt)
    suffix = extension(fmt)
    total = len(request.items)

    root = Path(request.destination)
    folder = naming.NameAllocator(root).allocate_folder(
        naming.sanitize_component(request.playlist_name, fallback="Export")
    )
    folder.mkdir(parents=True, exist_ok=True)

    allocator = naming.NameAllocator(folder)
    outcome = ExportOutcome(destination=folder)
    consecutive_failures = 0

    for index, item in enumerate(request.items):
        if should_cancel is not None and should_cancel():
            raise ExportCancelled(f"Stopped after {outcome.exported} files")

        if on_stage is not None:
            on_stage(item.title or f"Track {item.track_id}")

        source = AudioImportService.get_track_path(item.track_id)
        if not source.exists():
            outcome.skipped.append((item.track_id, "its audio file is missing"))
            logger.warning("Export skipped track %s: no file at %s", item.track_id, source)
        else:
            final = allocator.allocate(
                naming.track_filename(item.title, item.artist, suffix)
            )
            try:
                _write_one(item, source, final, fmt, embed_art=request.embed_art)
            except Exception as exc:
                consecutive_failures += 1
                outcome.skipped.append((item.track_id, str(exc)))
                logger.warning("Export failed for track %s: %s", item.track_id, exc)
                if consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
                    raise ExportError(
                        f"Export stopped after {CONSECUTIVE_FAILURE_LIMIT} failures "
                        f"in a row — {exc}"
                    ) from exc
            else:
                consecutive_failures = 0
                outcome.exported += 1

        if on_progress is not None:
            on_progress(index + 1, total)

    return outcome


def _write_one(item: ExportItem, source: Path, final: Path, fmt: str,
               *, embed_art: bool) -> None:
    """Produce *final* from *source*, via a temporary file next to it."""
    temp = final.with_name(final.name + ".part")
    try:
        if fmt == FORMAT_MP3:
            encode.to_mp3(source, temp)
            _tag_mp3(item, source, temp, embed_art=embed_art)
        else:
            shutil.copy2(source, temp)
            _tag_opus(item, temp)
        os.replace(temp, final)
    finally:
        # Reached on the success path too, where the replace already moved it.
        try:
            temp.unlink()
        except OSError:
            pass


def _tag_opus(item: ExportItem, path: Path) -> None:
    """Re-apply the database's title/artist/album/lyrics to a copied Opus file.

    A merge, not a rewrite: the embedded cover and the provenance tags survive,
    because :func:`~app.importer.tagging.update_opus_tags` only touches the keys
    it is handed.

    Lyrics are re-applied from the database for the same reason the other three
    fields are — the rule at the top of this module is that *the database is
    what gets written*, so a track whose file tag drifted from its library row
    exports correctly. They are handed over only when the database has some:
    an empty value *removes* a tag rather than writing a blank one, and a track
    with no row should not have the words stripped off its copy.
    """
    changes: dict[str, str] = {
        "TITLE": item.title,
        "ARTIST": item.artist,
        "ALBUM": item.album,
    }
    if item.lyrics:
        changes["LYRICS"] = item.lyrics
    update_opus_tags(path, changes)


def _tag_mp3(item: ExportItem, source: Path, path: Path, *, embed_art: bool) -> None:
    """Write ID3 frames onto a freshly transcoded MP3.

    The cover has to come from *source*: ffmpeg was told to drop it, and it
    would be AVIF and unreadable to most players even if it hadn't been.
    """
    write_mp3_tags(
        path,
        title=item.title,
        artist=item.artist,
        album=item.album,
        lyrics=item.lyrics,
        cover=cover_jpeg(source) if embed_art else None,
    )
