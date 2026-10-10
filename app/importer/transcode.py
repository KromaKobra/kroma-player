"""Transcode arbitrary local audio to Opus for stashing into the library.

Kroma stores every track as Opus, so any non-Opus import must be converted
before it is stashed. Conversion is delegated to ffmpeg. The source's own tags
are intentionally dropped (``-map_metadata -1``); the canonical metadata is
written afterwards by ``app.importer.tagging.tag_opus_file``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from app.utils.binaries import ffmpeg_path
from app.utils.proc import run_ffmpeg

# Output bitrate for transcodes. 192k VBR Opus is transparent for music while
# keeping imported files reasonably sized.
_OPUS_BITRATE = "192k"


def ensure_opus(source: Path, output_dir: Path) -> Path:
    """Return a path to an Opus copy of ``source`` inside ``output_dir``.

    Already-Opus files are copied verbatim (tags are cleared later at the tagging
    step); every other format is transcoded with ffmpeg. The returned file lives
    in ``output_dir`` — typically a caller-owned temp dir — and is safe to move
    into the managed library afterwards.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    destination = output_dir / f"{source.stem}.opus"

    if source.suffix.lower() == ".opus":
        shutil.copy2(source, destination)
        return destination

    run_ffmpeg(
        [
            ffmpeg_path() or "ffmpeg",
            "-y",
            "-hide_banner",
            "-loglevel", "error",
            "-i", str(source),
            "-vn",                    # drop any embedded cover/video stream
            "-map_metadata", "-1",    # strip source tags; we re-tag canonically
            "-c:a", "libopus",
            "-b:a", _OPUS_BITRATE,
            str(destination),
        ]
    )

    if not destination.exists():
        raise RuntimeError(f"Transcode produced no Opus output for {source}")
    return destination
