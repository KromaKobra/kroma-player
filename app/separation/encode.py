"""ffmpeg on either side of the model.

Separation happens on WAV — it is what the model libraries read and write — but
the library stores Opus, so a generated instrumental has to be decoded on the
way in and encoded on the way out.

The encode step is also where the seamlessness guarantee is enforced. A model
returns however many samples its windowing happened to produce, which is
usually the input length and occasionally a few milliseconds off. Rather than
measure and hope, the output is padded with silence and then cut to the mix's
exact duration, so it comes out the right length by construction.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.utils.binaries import ffmpeg_path
from app.utils.proc import run_ffmpeg as _run

logger = logging.getLogger(__name__)

# Matches the importer's bitrate so a generated instrumental and an imported one
# are the same kind of file.
_OPUS_BITRATE = "192k"


def to_wav(source: Path, destination: Path) -> Path:
    """Decode *source* to 44.1 kHz stereo WAV for the model to read."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    _run([
        ffmpeg_path() or "ffmpeg",
        "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source),
        "-vn",
        "-ac", "2",
        "-ar", "44100",
        "-c:a", "pcm_s16le",
        str(destination),
    ])
    if not destination.exists():
        raise RuntimeError(f"ffmpeg produced no WAV for {source}")
    return destination


def to_opus_exact(source: Path, destination: Path, seconds: float) -> Path:
    """Encode *source* to Opus at exactly *seconds* long.

    ``apad`` appends unlimited silence and ``-t`` then truncates, so a model
    output that came back slightly short is padded and one that came back
    slightly long is cut. Either way the result matches the mix, which is what
    makes the mid-playback toggle seamless.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    _run([
        ffmpeg_path() or "ffmpeg",
        "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source),
        "-vn",
        "-map_metadata", "-1",
        "-af", "apad",
        "-t", f"{seconds:.6f}",
        "-c:a", "libopus",
        "-b:a", _OPUS_BITRATE,
        str(destination),
    ])
    if not destination.exists():
        raise RuntimeError(f"ffmpeg produced no Opus output for {source}")
    return destination
