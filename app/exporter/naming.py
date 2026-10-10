"""Turning track metadata into filenames that survive the trip.

Pure functions and one small class, no filesystem writes and no database, which
is deliberate: this is where the bugs live, so it is the part worth testing
exhaustively.

The rules are the *union* of what every platform forbids rather than just the
host's, because the whole point of exporting is that the folder gets copied
somewhere else — a phone, a USB stick formatted FAT32, a car stereo. A filename
that only works on the machine that produced it has failed at its one job.
"""
from __future__ import annotations

import re
from pathlib import Path

# Forbidden on Windows; ``/`` and the control characters are forbidden on POSIX
# too. Everything here becomes an underscore rather than disappearing, so
# "AC/DC" stays legible as "AC_DC".
_ILLEGAL = '<>:"/\\|?*'

# Windows refuses to create a file whose stem is a DOS device name, whatever the
# extension, and does so with an error that explains nothing.
_RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}

# Filesystems cap a path component at 255 bytes. Stopping well short leaves room
# for a " (12)" collision suffix and an extension, and keeps multi-byte titles
# from tripping the byte limit while looking well under the character one.
MAX_STEM = 120

_WHITESPACE = re.compile(r"\s+")
_ILLEGAL_PATTERN = re.compile(f"[{re.escape(_ILLEGAL)}]|[\x00-\x1f]")


def sanitize_component(text: str, *, fallback: str = "Untitled") -> str:
    """Return *text* reduced to a single safe path component.

    Trailing dots and spaces are stripped rather than replaced because Windows
    silently drops them when creating the file. Leaving them in would mean the
    name we check for collisions against and the name that lands on disk are
    different strings, which is exactly how two tracks quietly become one.
    """
    cleaned = _ILLEGAL_PATTERN.sub("_", str(text or ""))
    cleaned = _WHITESPACE.sub(" ", cleaned).strip()
    cleaned = cleaned[:MAX_STEM].rstrip(" .")

    if not cleaned:
        return fallback
    if cleaned.upper() in _RESERVED:
        return f"{cleaned}_"
    return cleaned


def track_filename(title: str, artist: str, extension: str) -> str:
    """Build ``Artist - Title.ext`` from a track's metadata.

    The joined stem is sanitised as one string, not part by part: a title that
    genuinely contains " - " is harmless, whereas sanitising the halves
    separately lets the same illegal character behave differently depending on
    which field it turned up in.
    """
    title = str(title or "").strip()
    artist = str(artist or "").strip()

    if artist and title:
        stem = f"{artist} - {title}"
    else:
        stem = title or artist

    if not extension.startswith("."):
        extension = f".{extension}"
    return f"{sanitize_component(stem)}{extension}"


class NameAllocator:
    """Hands out filenames inside one directory, never reusing one.

    Claims are tracked casefolded and checked against the directory's existing
    contents. Both matter: NTFS and APFS are case-insensitive, so ``Faith`` and
    ``FAITH`` are the same file there, and a second export into a folder that
    already holds the first must not overwrite it.
    """

    def __init__(self, directory: Path) -> None:
        self._directory = Path(directory)
        self._claimed: set[str] = set()

    def allocate(self, filename: str) -> Path:
        """Reserve and return a free path for *filename*.

        On conflict a ``" (2)"``, ``" (3)"``… suffix is inserted before the
        extension, counting up until something is free.
        """
        candidate = Path(filename)
        return self._claim(candidate.stem, candidate.suffix)

    def allocate_folder(self, name: str) -> Path:
        """Reserve and return a free subdirectory path named *name*.

        Separate from :meth:`allocate` because a folder has no extension to
        preserve, and treating one as if it did mangles perfectly ordinary
        playlist names — "Vol. 2" would collide as "Vol (2). 2".
        """
        return self._claim(name, "")

    def _claim(self, stem: str, suffix: str) -> Path:
        attempt = 1
        while True:
            name = stem if attempt == 1 else f"{stem} ({attempt})"
            name = f"{name}{suffix}"
            path = self._directory / name
            if name.casefold() not in self._claimed and not path.exists():
                self._claimed.add(name.casefold())
                return path
            attempt += 1
