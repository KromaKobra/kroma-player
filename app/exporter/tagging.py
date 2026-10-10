"""Writing ID3 tags onto an exported MP3.

The rest of the app never writes ID3 — the library is Opus end to end, so
:mod:`app.importer.tagging` only needs Vorbis comments. Export is the one place
audio leaves in another container, and a file with no tags is not much use to
whoever receives it.

mutagen does the writing rather than ffmpeg's ``-metadata`` flags. The deciding
factor is the cover: the library stores it as AVIF, which almost nothing outside
this app can read, so it has to be re-encoded to JPEG with Pillow anyway.
Having gone that far, handing the text frames to ffmpeg as well would mean two
programs writing into one tag block. mutagen also lets us pin the output to
ID3v2.3 with an ID3v1 fallback, which is the pair with the widest player
support — and that is the entire point of the feature.
"""
from __future__ import annotations

import io
import logging
from pathlib import Path

from mutagen.id3 import APIC, ID3, ID3NoHeaderError, TALB, TIT2, TPE1, USLT

from app.artwork.image_ops import ensure_avif_support
from app.importer.tagging import extract_embedded_picture

logger = logging.getLogger(__name__)

# High enough that the art still looks right full-screen on a phone, low enough
# that it does not dominate a 4-minute MP3.
COVER_QUALITY = 88


def cover_jpeg(opus_path: Path, max_size: int = 800) -> bytes | None:
    """Return the track's embedded cover re-encoded as JPEG, or ``None``.

    Best-effort throughout. Artwork is the one part of an export nobody would
    accept losing the audio over, so every failure here — no picture, an AVIF
    plugin that will not load, a corrupt image — returns ``None`` and lets the
    export carry on untagged rather than propagating.
    """
    raw = extract_embedded_picture(opus_path)
    if not raw:
        return None

    try:
        from PIL import Image

        ensure_avif_support()
        with Image.open(io.BytesIO(raw)) as im:
            im = im.convert("RGB")
            if im.width > max_size:
                height = round(im.height * max_size / im.width)
                im = im.resize((max_size, height), Image.Resampling.LANCZOS)
            buffer = io.BytesIO()
            im.save(buffer, format="JPEG", quality=COVER_QUALITY, optimize=True)
            return buffer.getvalue()
    except Exception as exc:
        logger.warning("Could not convert cover art for %s: %s", opus_path, exc)
        return None


def write_mp3_tags(path: Path, *, title: str, artist: str, album: str,
                   lyrics: str = "", cover: bytes | None = None) -> None:
    """Write the exported track's metadata into *path* as ID3.

    *artist* is written as a single ``TPE1`` value rather than one per artist:
    that is what the Opus files already carry, and multi-value TPE1 gets joined
    with a slash by most players anyway.

    *lyrics* becomes a ``USLT`` frame, so the MP3 path is not quietly the one
    export format that loses them. Written verbatim — an LRC document keeps its
    timestamps, which the players that understand them read out of exactly this
    frame, and the ones that do not show them as text.

    Saved as ID3v2.3 plus an ID3v1 trailer. v2.4 is the newer spec and is worse
    supported by exactly the devices someone exports MP3s for.
    """
    try:
        tags = ID3(str(path))
    except ID3NoHeaderError:
        # ffmpeg was told to write no metadata, so this is the normal path.
        tags = ID3()

    tags.delall("TIT2")
    tags.delall("TPE1")
    tags.delall("TALB")
    tags.add(TIT2(encoding=3, text=[str(title or "")]))
    tags.add(TPE1(encoding=3, text=[str(artist or "")]))
    tags.add(TALB(encoding=3, text=[str(album or "")]))

    # ``delall`` by frame name, then re-add: USLT is keyed by language and
    # description as well as by name, so a plain overwrite could leave a second
    # copy of the lyrics behind under a different key.
    tags.delall("USLT")
    if lyrics:
        tags.add(USLT(encoding=3, lang="eng", desc="", text=str(lyrics)))

    tags.delall("APIC")
    if cover:
        tags.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="cover",
                      data=cover))

    tags.save(str(path), v2_version=3, v1=2)
