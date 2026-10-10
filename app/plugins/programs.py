"""The outside programs a plugin needs: where they are, and whether they are new enough.

A plugin names its programs in ``requires.programs``; the host finds each one
with exactly the rule the core uses for ffmpeg —
:func:`app.utils.binaries.resolve_program`, bundled then beside the executable
then PATH — and passes the paths in ``initialize``. A plugin therefore never
searches PATH itself, and the Dependencies page and the plugin cannot disagree
about which copy is in use.

A program can carry a ``minVersion``. Some go stale rather than old — a
downloader whose upstream changes every few weeks does not degrade when it falls
behind, it stops working, with an error that names nothing about versions — so
the floor is shown on the Dependencies page *before* anything fails.

ADR 0012's rule carries over unchanged and is the reason :func:`is_stale` is
shaped the way it is: **a version this cannot read is never called stale**.
Telling someone a perfectly good program is too old sends them off updating
something that was never the problem, which is worse than missing the warning
for the few builds that print something odd.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from functools import lru_cache
from typing import Dict, Optional, Sequence, Tuple

from app.utils.binaries import clear_cache as _clear_paths, resolve_program
from app.utils.proc import no_window

_NUMBER_RUN_RE = re.compile(r"\d+(?:\.\d+)+")


@dataclass(frozen=True)
class ProgramStatus:
    """One program as the Dependencies page shows it."""

    name: str
    path: str
    available: bool
    version: str
    minimum_version: str
    stale: bool
    needed_by: Tuple[str, ...] = ()

    def as_dict(self) -> Dict[str, object]:
        return {
            "name": self.name,
            "path": self.path,
            "available": self.available,
            "version": self.version,
            "minimumVersion": self.minimum_version,
            "stale": self.stale,
            "neededBy": list(self.needed_by),
        }


def path_of(name: str) -> Optional[str]:
    """Where *name* is, by the core's own rule, or None."""
    return resolve_program(name)


@lru_cache(maxsize=64)
def version_of(name: str, args: Tuple[str, ...] = ("--version",)) -> str:
    """The first line *name* prints for *args*, or ``""`` when it cannot be asked.

    Cached for the life of the process like the path lookup beside it, and
    dropped by the same :func:`clear_cache` — a re-check after an update has to
    see the new copy.
    """
    executable = resolve_program(name)
    if executable is None:
        return ""
    try:
        completed = subprocess.run(
            [executable, *args], check=True, capture_output=True,
            encoding="utf-8", errors="replace", timeout=10, **no_window(),
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    text = (completed.stdout or completed.stderr or "").strip()
    return text.splitlines()[0].strip() if text else ""


def _numbers(version: str) -> Optional[Tuple[int, ...]]:
    """The dotted number in *version* as a tuple, or None when there is none.

    Takes the first run of dotted digits, so ``ffmpeg version 7.1`` and
    ``deno 2.4.1 (stable, …)`` both read; a string with no such run is
    unreadable, and unreadable is never stale.
    """
    found = _NUMBER_RUN_RE.search(version or "")
    if not found:
        return None
    try:
        return tuple(int(part) for part in found.group(0).split("."))
    except ValueError:
        return None


def is_stale(version: str, minimum: str) -> bool:
    """True when *version* is readably older than *minimum*.

    False for no minimum, for an unreadable version, and for an unreadable
    minimum. Both are padded to the same length so ``2026.8`` against
    ``2026.08.19`` compares as the dates they are.
    """
    if not minimum:
        return False
    have, want = _numbers(version), _numbers(minimum)
    if have is None or want is None:
        return False
    width = max(len(have), len(want))
    return have + (0,) * (width - len(have)) < want + (0,) * (width - len(want))


def status(
    name: str,
    *,
    minimum: str = "",
    version_args: Sequence[str] = ("--version",),
    needed_by: Sequence[str] = (),
) -> ProgramStatus:
    """Everything the Dependencies page draws for one program."""
    path = path_of(name) or ""
    version = version_of(name, tuple(version_args)) if path else ""
    return ProgramStatus(
        name=name,
        path=path,
        available=bool(path),
        version=version,
        minimum_version=minimum,
        stale=bool(path) and is_stale(version, minimum),
        needed_by=tuple(needed_by),
    )


def clear_cache() -> None:
    """Forget every path and version, so a re-check sees what is installed now."""
    _clear_paths()
    version_of.cache_clear()
