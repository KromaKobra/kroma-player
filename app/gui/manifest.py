"""Parsing and validation of a GUI package's ``gui.json`` manifest."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from app.gui.gui_paths import (
    GUI_API_VERSION,
    MANIFEST_FILENAME,
    MIN_GUI_API_VERSION,
)


class GuiManifestError(Exception):
    """Raised when a gui.json is missing, malformed, or unsafe."""


@dataclass(frozen=True)
class GuiManifest:
    """A validated GUI package manifest."""

    name: str
    title: str
    version: str
    api_version: int
    entry: str
    author: str = ""
    description: str = ""
    homepage: str = ""

    @property
    def is_compatible(self) -> bool:
        """True when this build can load the GUI."""
        return MIN_GUI_API_VERSION <= self.api_version <= GUI_API_VERSION

    def incompatibility_reason(self) -> str:
        """Human-readable explanation of why the GUI cannot load, or ""."""
        if self.is_compatible:
            return ""
        if self.api_version > GUI_API_VERSION:
            return (
                f"needs GUI API version {self.api_version}, "
                f"but this build provides {GUI_API_VERSION} — update Kroma Player"
            )
        return (
            f"targets GUI API version {self.api_version}, which this build no "
            f"longer supports (minimum {MIN_GUI_API_VERSION})"
        )


def _require_str(data: dict, key: str, source: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise GuiManifestError(f"{source}: '{key}' must be a non-empty string")
    return value.strip()


def _validate_entry(entry: str, source: str, exists: Callable[[str], bool]) -> str:
    """Ensure *entry* is a relative path that the package actually contains.

    Existence is asked of the caller rather than of the filesystem: a manifest
    read out of a mounted ``.kpi`` is checked against the bundle's file list,
    and one read out of a source tree is checked against the directory. The
    traversal check is the same either way and matters more in the bundle case,
    where the entry becomes part of a URL.
    """
    candidate = Path(entry)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise GuiManifestError(
            f"{source}: 'entry' must be a relative path inside the GUI package"
        )
    normalised = candidate.as_posix()
    if not exists(normalised):
        raise GuiManifestError(f"{source}: entry file '{entry}' does not exist")
    return normalised


def _parse(raw: str | bytes, source: str, exists: Callable[[str], bool]) -> GuiManifest:
    """Parse and validate manifest *raw*, reporting problems against *source*."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise GuiManifestError(f"{source}: invalid JSON ({exc})") from exc

    if not isinstance(data, dict):
        raise GuiManifestError(f"{source}: must contain a JSON object")

    api_version = data.get("api_version")
    if not isinstance(api_version, int) or isinstance(api_version, bool):
        raise GuiManifestError(f"{source}: 'api_version' must be an integer")

    name = _require_str(data, "name", source)
    entry = _validate_entry(_require_str(data, "entry", source), source, exists)

    return GuiManifest(
        name=name,
        title=str(data.get("title") or name),
        version=_require_str(data, "version", source),
        api_version=api_version,
        entry=entry,
        author=str(data.get("author") or ""),
        description=str(data.get("description") or ""),
        homepage=str(data.get("homepage") or ""),
    )


def from_bytes(
    data: bytes, files: Sequence[str], source: str = MANIFEST_FILENAME
) -> GuiManifest:
    """Parse a manifest read out of a mounted bundle.

    *files* is the bundle's file list, which is what the entry point is checked
    against — there is no directory to stat.

    Compatibility is *not* checked here, exactly as in ``load_manifest``: read
    ``is_compatible`` on the result so an incompatible GUI can still be listed
    with a reason instead of vanishing from the picker.
    """
    known = set(files)
    return _parse(data, source, known.__contains__)


def load_manifest(directory: Path) -> GuiManifest:
    """Load and validate ``<directory>/gui.json`` from a source tree.

    Only source trees are read this way now — an installed GUI is a ``.kpi`` and
    goes through ``from_bytes``. This is what the compiler reads to fill in the
    build record, and what a development checkout is resolved through.

    Raises ``GuiManifestError`` for anything that would make the package
    unloadable.
    """
    path = directory / MANIFEST_FILENAME
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise GuiManifestError(f"{path}: no {MANIFEST_FILENAME} found") from exc
    except OSError as exc:
        raise GuiManifestError(f"{path}: cannot be read ({exc})") from exc

    return _parse(raw, str(path), lambda entry: (directory / entry).is_file())


def version_tuple(version: str) -> tuple[int, ...]:
    """Parse a dotted version into a comparable tuple.

    Non-numeric segments sort as 0 so a nonsense version never compares greater
    than a real one — the upgrade path treats "unknown" as "older".
    """
    parts: list[int] = []
    for segment in version.split("."):
        digits = "".join(c for c in segment if c.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts) or (0,)
