"""Probe local media files for embedded external ids and basic metadata.

The import flow needs three things from each local file before anything else
happens to it:

* an external id, if one is embedded in the tags (authoritative identity), or
* a title + artist (and optionally album) to identify it by, or
* failing both, the file name (used only when the user opts into the
  filename fallback, and only when an identity provider is there to use it).

*Which* ids can be found is not this module's to know. An enabled plugin
declares, for each kind of id it understands, the tag keys that carry one
(``readTags``) and the patterns that find one inside any other tag value
(``tagScan``). The import view model snapshots those declarations on the UI
thread and passes them down, so the probe on its worker thread reads immutable
data and never asks the plugin host anything. With no plugins, no ids are
looked for — a file is its title and artist.

Reading is fully best-effort: any unreadable/corrupt file degrades to an empty
``ProbedFile`` rather than raising, so one bad file never aborts an import.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, Iterable, List, Optional, Sequence

import mutagen
from mutagen.id3 import ID3

logger = logging.getLogger(__name__)

# Audio container extensions Kroma is willing to import. Lower-case, dot-prefixed.
MEDIA_EXTENSIONS: frozenset[str] = frozenset(
    {".aac", ".flac", ".m4a", ".mp3", ".ogg", ".opus", ".wav"}
)

@dataclass
class ProbedFile:
    """An import candidate plus whatever identity/metadata is known about it.

    Two sources produce these: local audio files (``path`` set, metadata read
    from tags) and CSV rows (``path`` is ``None``, ``from_csv`` True, metadata
    from spreadsheet columns). The richer CSV-only fields (``genres``,
    ``musical_key``, ``bpm``, ``isrc``, ``copyright_text``)
    default empty for local files and are surfaced via :meth:`csv_override`.
    """

    path: Optional[Path] = None
    title: str = ""
    artist: str = ""
    album: str = ""
    # Every id found in the tags (or a CSV's id columns), as
    # ``{"provider:kind": value}`` — the shape `app.plugins.external_ids` reads.
    external_ids: Dict[str, str] = field(default_factory=dict)
    duration: int = 0
    release_date: Optional[int] = None
    readable: bool = True
    # CSV-sourced extras (empty for local-file probes).
    # NOTE: local-file probes should also be able to carry these too in the future.
    from_csv: bool = False
    genres: str = ""
    musical_key: str = ""
    bpm: Optional[int] = None
    isrc: str = ""
    copyright_text: str = ""

    @property
    def ext(self) -> str:
        """Lower-case file extension including the leading dot (e.g. ``.mp3``)."""
        return self.path.suffix.lower() if self.path else ""

    @property
    def is_opus(self) -> bool:
        return self.ext == ".opus"

    @property
    def source_path(self) -> str:
        """The local file path as a string, or '' for path-less (CSV) candidates."""
        return str(self.path) if self.path else ""

    @property
    def label(self) -> str:
        """Short human label for logging (file name, else title/id)."""
        if self.path:
            return self.path.name
        return self.title or next(iter(self.external_ids.values()), "") or "row"

    @property
    def has_external_id(self) -> bool:
        return any(self.external_ids.values())

    @property
    def has_title_artist(self) -> bool:
        return bool(self.title.strip() and self.artist.strip())

    @property
    def has_metadata(self) -> bool:
        """True when the file can be identified without falling back to its name."""
        return self.has_external_id or self.has_title_artist

    def csv_override(self) -> dict:
        """Canonical metadata dict of the CSV-provided fields (non-empty only).

        Keys match what the download/tagging pipeline consumes; empty for
        non-CSV probes so the local-import path is unaffected. The download step
        applies these over the source's track data (CSV wins, the source fills
        gaps).
        """
        if not self.from_csv:
            return {}
        override: dict = {}
        if self.title:
            override["title"] = self.title
        if self.artist:
            override["artists"] = self.artist
        if self.album:
            override["album"] = self.album
        if self.release_date:
            override["release_date"] = self.release_date
        if self.bpm:
            override["bpm"] = self.bpm
        if self.musical_key:
            override["musical_key"] = self.musical_key
        if self.genres:
            override["genres"] = self.genres
        if self.isrc:
            override["isrc"] = self.isrc
        if self.copyright_text:
            override["copyright_text"] = self.copyright_text
        return override


def gather_media_files(paths: Iterable[Path | str]) -> List[Path]:
    """Expand a mix of files and folders into a de-duplicated list of media files.

    Folders are walked recursively. Non-media files are ignored. Order is stable
    (sorted within each folder) and duplicates (by resolved path) are dropped.
    """
    seen: set[Path] = set()
    out: List[Path] = []

    def _consider(file: Path) -> None:
        if file.suffix.lower() not in MEDIA_EXTENSIONS:
            return
        try:
            resolved = file.resolve()
        except OSError:
            resolved = file
        if resolved in seen:
            return
        seen.add(resolved)
        out.append(file)

    for raw in paths:
        path = Path(raw)
        try:
            if path.is_dir():
                for child in sorted(path.rglob("*")):
                    if child.is_file():
                        _consider(child)
            elif path.is_file():
                _consider(path)
        except OSError as exc:
            logger.warning("Skipping unreadable import path %s: %s", path, exc)

    return out


def probe_media_file(path: Path, kinds: Sequence = ()) -> ProbedFile:
    """Read identity + basic metadata from one media file (never raises).

    *kinds* is the snapshot of declared id kinds — see the module docstring.
    Each is an :class:`~app.plugins.manifest.IdKind`; nothing here imports it,
    because nothing here needs more than its ``key``, ``read_tags``,
    ``tag_scan`` and ``parse_value``.
    """
    probed = ProbedFile(path=path)

    try:
        audio = mutagen.File(path)  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001 — corrupt files are expected here
        logger.warning("Could not read %s: %s", path, exc)
        probed.readable = False
        return probed

    if audio is None:
        probed.readable = False
        return probed

    # Duration (seconds) — present for every parsed container.
    info = getattr(audio, "info", None)
    length = getattr(info, "length", None)
    if length:
        probed.duration = int(length)

    # Title / artist / album / date via the uniform "easy" interface where available.
    title, artist, album, release_date = _read_easy_tags(path)
    probed.title = title
    probed.artist = artist
    probed.album = album
    # Easy interface only maps TDRC; fall back to raw tags to catch TDRL/TYER.
    probed.release_date = release_date or _read_raw_date_tag(audio)

    # External ids: each declared kind's dedicated tags first, then a scan of
    # every tag value for something that embeds one (a URL in a comment).
    if kinds:
        strings = _collect_tag_strings(audio)
        for kind in kinds:
            value = _explicit_id(audio, kind) or _scan_id(strings, kind)
            if value:
                probed.external_ids.setdefault(kind.key, value)

    return probed


def probe_media_files(
    paths: Iterable[Path],
    should_stop: Callable[[], bool] | None = None,
    kinds: Sequence = (),
) -> List[ProbedFile]:
    """Probe a sequence of already-gathered media files.

    *should_stop* is polled between files. It exists because probing opens every
    file on disk, which is the part of an import worth interrupting: a user who
    cancels a folder of ten thousand tracks should not have to wait for the
    scan to finish being pointless. What comes back when it returns True is a
    partial list, and the only caller — the analyze worker — discards it.
    """
    out: List[ProbedFile] = []
    for path in paths:
        if should_stop is not None and should_stop():
            break
        out.append(probe_media_file(path, kinds))
    return out


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _read_easy_tags(path: Path) -> tuple[str, str, str, Optional[int]]:
    """Return (title, artist, album, release_date) using mutagen's easy interface, best-effort."""
    try:
        easy = mutagen.File(path, easy=True)  # type: ignore[attr-defined]
    except Exception:  # noqa: BLE001
        return "", "", "", None
    if easy is None or easy.tags is None:
        return "", "", "", None

    def first(key: str) -> str:
        try:
            value = easy.tags.get(key)
        except Exception:  # noqa: BLE001
            return ""
        if not value:
            return ""
        item = value[0] if isinstance(value, list) else value
        return str(item).strip()

    release_date = _parse_date_tag(first("date"))
    return first("title"), first("artist"), first("album"), release_date


def _stringify(value: object) -> List[str]:
    """Flatten a tag value (str / bytes / list / frame) into a list of strings."""
    out: List[str] = []
    if value is None:
        return out
    if isinstance(value, (list, tuple)):
        for item in value:
            out.extend(_stringify(item))
        return out
    if isinstance(value, bytes):
        try:
            out.append(value.decode("utf-8", "ignore"))
        except Exception:  # noqa: BLE001
            pass
        return out
    out.append(str(value))
    return out


def _collect_tag_strings(audio: object) -> List[str]:
    """Collect every textual tag value/key for URL scanning (best-effort)."""
    tags = getattr(audio, "tags", None)
    if not tags:
        return []

    out: List[str] = []

    if isinstance(tags, ID3):
        # ID3 frames: pull text frames, URL frames, and freeform descriptions.
        for frame in tags.values():
            out.extend(_stringify(getattr(frame, "text", None)))
            url = getattr(frame, "url", None)
            if url:
                out.extend(_stringify(url))
            desc = getattr(frame, "desc", None)
            if desc:
                out.append(str(desc))
        return [s for s in out if s]

    # Dict-like containers: Vorbis comments (flac/ogg/opus), MP4, APE.
    try:
        keys = list(tags.keys())
    except Exception:  # noqa: BLE001
        return out
    for key in keys:
        out.append(str(key))
        try:
            out.extend(_stringify(tags[key]))
        except Exception:  # noqa: BLE001
            continue
    return [s for s in out if s]


def _explicit_id(audio: object, kind) -> str:
    """An id of *kind* from one of the tag keys it names, if present and readable."""
    tags = getattr(audio, "tags", None)
    if not tags:
        return ""
    for key in kind.read_tags:
        try:
            value = tags.get(key)
        except Exception:  # noqa: BLE001
            value = None
        for candidate in _stringify(value):
            found = kind.parse_value(candidate)
            if found:
                return found
    return ""


def _read_raw_date_tag(audio: object) -> Optional[int]:
    """Read a release date from raw mutagen tags, covering ID3 and Vorbis containers.

    For ID3 (MP3): tries TDRL (release time), TYER (legacy year), then TDRC (recording time).
    For Vorbis/MP4/other dict-like: tries the DATE key directly.
    """
    from mutagen.id3 import ID3

    tags = getattr(audio, "tags", None)
    if not tags:
        return None

    if isinstance(tags, ID3):
        for frame_key in ("TDRL", "TYER", "TDRC"):
            frame = tags.get(frame_key)
            if frame is None:
                continue
            texts = getattr(frame, "text", None)
            if not texts:
                continue
            raw = str(texts[0]).strip()
            result = _parse_date_tag(raw)
            if result is not None:
                return result
        return None

    # Vorbis comments (OGG, Opus, FLAC) and MP4: dict-like, case-insensitive lookup.
    for key in ("date", "DATE", "year", "YEAR"):
        try:
            value = tags.get(key)
        except Exception:  # noqa: BLE001
            continue
        if not value:
            continue
        raw = str(value[0] if isinstance(value, list) else value).strip()
        result = _parse_date_tag(raw)
        if result is not None:
            return result
    return None


def _parse_date_tag(raw: str) -> Optional[int]:
    """Normalise a free-form date string to an integer YYYYMMDD (or shorter year-only form).

    Strips separators, converts to int, then pads with trailing zeros to 8 digits.
    Returns None when the value is absent or unparseable.
    """
    if not raw:
        return None
    cleaned = raw.replace("-", "").replace("/", "").replace(".", "").strip()
    if not cleaned.isdigit():
        return None
    value = int(cleaned)
    while value < 10000000:
        value *= 10
    return value


def _scan_id(strings: Iterable[str], kind) -> str:
    """The first id of *kind* embedded anywhere in the tag values."""
    if not kind.tag_scan:
        return ""
    for text in strings:
        found = kind.scan(text)
        if found:
            return found
    return ""
