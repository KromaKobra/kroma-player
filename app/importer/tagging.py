"""Opus tagging helpers."""

from __future__ import annotations

import base64
import mimetypes
from pathlib import Path
from typing import Any

from mutagen.flac import Picture
from mutagen.oggopus import OggOpus

from app.importer.core_tags import CORE_TAGS
from app.plugins import external_ids


def strip_opus_tags(opus_path: Path) -> None:
    """Remove every tag from an Opus file, artwork included.

    For files that carry no metadata of their own. An instrumental is the one
    such file Kroma stores: it is the same recording as its mix, found by the
    mix's own id, and the mix already holds the title, the artist and the
    artwork. A second copy in ``bt/`` could only go stale — and worse, it would
    be the copy a player reading tags off disk found first.

    Necessary because ``ensure_opus`` only strips tags on the paths that
    re-encode (``-map_metadata -1``). A source that is *already* Opus is copied
    verbatim, tags and all, which is exactly the case a user handing over an
    instrumental they exported themselves is most likely to hit.
    """
    audio = OggOpus(str(opus_path))
    if audio.tags is None:
        return
    audio.tags.clear()
    audio.save()


def build_picture_block(image_path: Path) -> str:
    """Create a METADATA_BLOCK_PICTURE payload from an image file."""
    mime = mimetypes.guess_type(image_path.name)[0] or "image/jpeg"
    return build_picture_block_from_bytes(image_path.read_bytes(), mime=mime)


def build_picture_block_from_bytes(data: bytes, mime: str = "image/avif", desc: str = "cover") -> str:
    """Create a METADATA_BLOCK_PICTURE payload from raw image bytes.

    Used by the stash to embed the AVIF cover master directly without a
    temporary file. The FLAC picture block stores arbitrary image data + MIME, so
    AVIF (``image/avif``) is fully supported here; Kroma decodes it via Pillow.
    """
    picture = Picture()
    picture.data = data
    picture.type = 3  # front cover
    picture.desc = desc
    picture.mime = mime
    return base64.b64encode(picture.write()).decode("ascii")


def extract_embedded_picture(opus_path: Path) -> bytes | None:
    """Return the raw bytes of the cover embedded in an Opus file, or None.

    Reads the first ``METADATA_BLOCK_PICTURE`` entry. Used by the cover backfill to
    rebuild thumbnails from artwork already living inside the audio files.
    """
    try:
        audio = OggOpus(str(opus_path))
    except Exception:
        return None
    tags = audio.tags
    if not tags:
        return None
    blocks = tags.get("METADATA_BLOCK_PICTURE")
    if not blocks:
        return None
    try:
        picture = Picture(base64.b64decode(blocks[0]))
        return picture.data or None
    except Exception:
        return None


def tag_opus_file(
    opus_path: Path,
    info: dict[str, Any],
    picture_block: str | None = None,
    *,
    id_tags: dict[str, str] | None = None,
) -> None:
    """Write the requested metadata into an Opus file.

    External ids are written for the kinds whose declaration names a
    ``writeTag``. *id_tags* is ``{key: comment}`` for those kinds, and the
    values are read off ``info["external_ids"]``, the same map the track row
    was created from, so the file and the row cannot be told two different
    things. A comment in :data:`CORE_TAGS` is skipped.
    """
    audio = OggOpus(str(opus_path))
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    if tags is None:
        raise RuntimeError(f"Unable to create tags for {opus_path}")
    tags.clear()

    values = {
        "LOCALID": info["local_id"],
        "TITLE": info["title"],
        "ARTIST": info["artists"],
        "ALBUM": info["album"],
        "DATE": info["release_date"],
        "COPYRIGHT": info.get("copyright_text") or info.get("copyright"),
        "GENRE": info.get("genres") or info.get("genre"),
        "KEY": info.get("musical_key") or info.get("key"),
        "BPM": info.get("bpm"),
        "CONTENTTYPE": info.get("content_type"),
        "AUDIOPROCESSING": info.get("audio_processing"),
        **{
            comment: held
            for comment, held in external_ids.tags_for(
                info.get("external_ids") or {}, id_tags or {}
            ).items()
            if comment not in CORE_TAGS
        },
        "ISRC": info.get("isrc"),
        "LYRICS": info.get("lyrics"),
        "TRANSCODEDFROM": info.get("transcoded_from"),
    }
    for key, value in values.items():
        if value:
            tags[key] = [str(value)]
    if picture_block:
        tags["METADATA_BLOCK_PICTURE"] = [picture_block]
    audio.save()


def update_opus_tags(
    opus_path: Path,
    changes: dict[str, Any],
    picture_block: str | None = None,
) -> None:
    """Merge changed Vorbis comments into an existing Opus file.

    Unlike :func:`tag_opus_file` this never clears the tag block, so everything
    not named in ``changes`` survives — the embedded ``METADATA_BLOCK_PICTURE``,
    ``LYRICS``, ``LOCALID``, and the provenance tags written at import time. That
    is the whole point: an edit knows about the handful of fields the user
    touched, not about the full picture the download path had.

    It is also how :meth:`app.services.lyrics_service.LyricsService.save` writes
    lyrics back into a file without disturbing anything else in the block.

    Keys are the Vorbis names :func:`tag_opus_file` writes (``TITLE``,
    ``ARTIST``, ``DATE``, …). A value that is empty or ``None`` **removes** the
    tag rather than writing a blank one, so clearing a field in the editor and
    clearing it in the database mean the same thing here.

    ``picture_block`` replaces the embedded artwork, and is the one exception to
    "everything unnamed survives" — passing it is how refreshing a track's
    metadata from a source installs the new cover. Omitted, the existing picture
    is left exactly where it was; there is no way to ask for the artwork to be
    *removed*, because a track that has lost its art has nothing to fall back on.
    """
    audio = OggOpus(str(opus_path))
    if audio.tags is None:
        audio.add_tags()
    tags = audio.tags
    if tags is None:
        raise RuntimeError(f"Unable to create tags for {opus_path}")

    if picture_block:
        tags["METADATA_BLOCK_PICTURE"] = [picture_block]

    for key, value in changes.items():
        name = key.upper()
        if value is None or str(value) == "":
            # mutagen's tag dict takes no default on pop, and comment keys are
            # matched case-insensitively, so ask before deleting.
            if name in tags:
                del tags[name]
        else:
            tags[name] = [str(value)]
    audio.save()
