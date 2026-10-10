"""Locations of the external programs the app shells out to.

ffmpeg (audio extraction and transcoding) is a separate executable, not a
Python package, so freezing the app does not bring it along on its own — and
releases do not bundle it: the user installs it once, and it is found on PATH.
A user who cannot edit PATH can drop a copy next to the .exe instead, and a
self-contained build can still carry one (dropped in vendor/ before building,
see kroma-player.spec) — three locations, searched in order of specificity.

The same rule finds the programs a *plugin* needs: a plugin names them in its
manifest, and :mod:`app.plugins.programs` resolves each one through
:func:`resolve_program` and hands the plugin the path. The core itself needs
only ffmpeg.

Every lookup returns ``None`` when the program cannot be found anywhere, so
callers decide what a missing dependency means for them — a transcode is
impossible without ffmpeg, but the rest of the app is unaffected.
"""

from __future__ import annotations

import shutil
import sys
from functools import cache
from pathlib import Path

_EXE_SUFFIX = ".exe" if sys.platform == "win32" else ""


def _match_in(root: Path, name: str) -> Path | None:
    """Find *name* directly inside *root*, ignoring case."""
    candidate = root / name
    if candidate.is_file():
        return candidate

    # A copy keeps whatever filename it was given, and on Windows PATHEXT
    # commonly yields an upper-case ".EXE". Matching case-insensitively keeps a
    # miss here from silently falling through to the next location, which would
    # look fine on the machine it was set up on and fail everywhere else.
    target = name.casefold()
    try:
        entries = list(root.iterdir())
    except OSError:
        return None
    return next(
        (p for p in entries if p.name.casefold() == target and p.is_file()),
        None,
    )


def _bundled(name: str) -> Path | None:
    """Return the copy shipped inside a frozen build, if this is one.

    Bundled copies sit at the root of the bundle, ffmpeg alongside ffprobe —
    tools that take an ffmpeg location derive ffprobe's from it, so the two
    must stay siblings.
    """
    meipass = getattr(sys, "_MEIPASS", None)
    return _match_in(Path(meipass), name) if meipass else None


def _beside_exe(name: str) -> Path | None:
    """Return the copy the user dropped next to the application itself.

    The escape hatch for a machine where PATH cannot be changed. Only meaningful
    in a frozen build: from source, ``sys.executable`` is the interpreter.
    """
    if not getattr(sys, "frozen", False):
        return None
    return _match_in(Path(sys.executable).parent, name)


@cache
def resolve_program(stem: str) -> str | None:
    """Return the path to *stem*: bundled, then beside the .exe, then PATH."""
    name = f"{stem}{_EXE_SUFFIX}"
    found = _bundled(name) or _beside_exe(name)
    if found is not None:
        return str(found)
    return shutil.which(stem)


def clear_cache() -> None:
    """Forget every resolved path.

    Lookups are cached for the life of the process, so a program installed
    while the app is running would otherwise stay missing until a restart.
    Calling this is what lets the settings screen re-check on demand.
    """
    resolve_program.cache_clear()


def ffmpeg_path() -> str | None:
    """Path to the ffmpeg executable, or None if it is not available."""
    return resolve_program("ffmpeg")
