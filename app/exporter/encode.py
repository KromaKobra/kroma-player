"""The ffmpeg half of exporting.

Only MP3 needs it — an Opus export is a file copy — so this is one function.
Tags are stripped on the way out (``-map_metadata -1``, the same as the
importer does) because ffmpeg's idea of how to carry Vorbis comments into ID3
is not the one we want; :mod:`app.exporter.tagging` writes the frames
afterwards from the database.
"""
from __future__ import annotations

from pathlib import Path

from app.exporter.formats import MP3_BITRATE
from app.utils.binaries import ffmpeg_path
from app.utils.proc import run_ffmpeg


def to_mp3(source: Path, destination: Path) -> Path:
    """Transcode *source* to MP3 at :data:`~app.exporter.formats.MP3_BITRATE`.

    No ``-ar``: library Opus is always 48 kHz and libmp3lame encodes that
    natively, so resampling would only add a conversion nobody asked for.

    ``-f mp3`` is not redundant. The exporter writes to a ``.part`` file and
    renames it once finished, and ffmpeg picks its muxer from the extension —
    without this it would refuse an output it could not identify.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    run_ffmpeg([
        ffmpeg_path() or "ffmpeg",
        "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source),
        "-vn",                    # drop the embedded cover; re-added as ID3 APIC
        "-map_metadata", "-1",    # strip source tags; we re-tag from the database
        "-c:a", "libmp3lame",
        "-b:a", MP3_BITRATE,
        "-f", "mp3",
        str(destination),
    ])
    if not destination.exists():
        raise RuntimeError(f"ffmpeg produced no MP3 output for {source}")
    return destination
