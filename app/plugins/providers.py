"""Providers — the questions the core can ask, and the one object that asks them.

Nothing outside :mod:`app.plugins` speaks the protocol. A view model or a worker
is handed a :class:`ProviderRegistry` and asks it for a *capability* — "is there
an audio source for this row", "who can identify an artist" — and gets back an
object with ordinary methods, or ``None``. "None" is an ordinary answer: it is
what a plain install says to every question here, and every caller has a path
for it.

Each provider is a typed façade over JSON-RPC calls to one plugin. Its job is
translation in both directions:

- **Protocol rows** are camelCase and carry ids as lists of
  ``{provider, kind, value}``, because that is what a plugin in any language can
  write without knowing Kroma's internals.
- **Core rows** are the snake_case dicts the import list and the search results
  have always used, with ids as ``{"provider:kind": value}`` maps — the shape
  :mod:`app.plugins.external_ids` reads. See :func:`row_from_protocol` for the
  full list of keys.

Translation is also where a plugin's ids are checked: a row keeps only the
kinds its plugin declares. The id tables keep any key they are given, so this
is the one place that stands between a plugin and the library.

Tests replace the registry with in-process fakes (``tests/fakes/providers.py``)
— subclasses of the classes here that answer from dicts — so everything above
this layer is exercised without a process anywhere.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from PySide6.QtCore import QObject, Signal

from app.plugins import external_ids
from app.plugins.manifest import (
    IdKind,
    ImportSourceDecl,
    PageDecl,
    PluginManifest,
    TrackSourceDecl,
)
from app.plugins.process import ERROR_CONSENT_REQUIRED, PluginError, ProgressCallback, StopCheck

logger = logging.getLogger(__name__)

#: How long each kind of question may take. Generous: these are network round
#: trips through a program that may itself be starting a child process.
TIMEOUT_QUERY = 45.0
TIMEOUT_LOOKUP = 90.0
#: Audio has no deadline — a long recording on a slow line is not a hang — and
#: is cancelled by the user rather than by the clock.
TIMEOUT_AUDIO: Optional[float] = None


class ConsentRequired(PluginError):
    """The plugin's terms have not been agreed to. Gate two of three."""

    def __init__(self, name: str) -> None:
        super().__init__(f"{name} needs you to agree to its terms first", ERROR_CONSENT_REQUIRED)


class UnsafeOutput(PluginError):
    """A plugin returned a file outside the folder it was given."""


# ----------------------------------------------------------------------
# Row translation
# ----------------------------------------------------------------------

def ids_from_protocol(entries: Any) -> Dict[str, str]:
    """``[{provider, kind, value}]`` → ``{"provider:kind": value}``. First of each kind wins."""
    out: Dict[str, str] = {}
    for entry in entries or []:
        if not isinstance(entry, Mapping):
            continue
        provider = str(entry.get("provider") or "").strip()
        kind = str(entry.get("kind") or "").strip()
        value = str(entry.get("value") or "").strip()
        if provider and kind and value:
            out.setdefault(external_ids.make_key(provider, kind), value)
    return out


def alternates_from_protocol(entries: Any) -> Dict[str, List[str]]:
    """``[{provider, kind, value}]`` → ``{"provider:kind": [values]}``, order kept."""
    out: Dict[str, List[str]] = {}
    for entry in entries or []:
        if not isinstance(entry, Mapping):
            continue
        provider = str(entry.get("provider") or "").strip()
        kind = str(entry.get("kind") or "").strip()
        value = str(entry.get("value") or "").strip()
        if provider and kind and value:
            values = out.setdefault(external_ids.make_key(provider, kind), [])
            if value not in values:
                values.append(value)
    return out


def ids_to_protocol(ids: Mapping[str, Any]) -> List[Dict[str, str]]:
    """The inverse of :func:`ids_from_protocol`."""
    out: List[Dict[str, str]] = []
    for key, value in (ids or {}).items():
        provider, kind = external_ids.split_key(key)
        cleaned = str(value or "").strip()
        if provider and cleaned:
            out.append({"provider": provider, "kind": kind, "value": cleaned})
    return out


def _alternates_to_protocol(alternates: Mapping[str, Any]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for key, values in (alternates or {}).items():
        provider, kind = external_ids.split_key(key)
        for value in values or []:
            if provider and value:
                out.append({"provider": provider, "kind": kind, "value": str(value)})
    return out


def _int(value: Any) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _date(value: Any) -> Optional[int]:
    """A release date as ``YYYYMMDD``, padding a year or a year-month with zeros."""
    if value in (None, ""):
        return None
    digits = "".join(ch for ch in str(value) if ch.isdigit())
    if not digits:
        return None
    return int(digits.ljust(8, "0")[:8])


def _str_list(value: Any) -> List[str]:
    return [str(v) for v in (value or []) if isinstance(v, str) and v.strip()]


def _only(ids: Dict[str, Any], manifest: Optional[PluginManifest], entity: str) -> Dict[str, Any]:
    """*ids* without the kinds *manifest* does not declare for *entity*.

    This is the trust boundary for ids. Nothing after it checks again, and the
    tables keep whatever key they are handed, so a kind the plugin never
    declared is dropped here. No manifest means the caller is the core itself.
    """
    if manifest is None:
        return ids
    declared = manifest.declared_keys(entity)
    return {key: held for key, held in ids.items() if key in declared}


def row_from_protocol(
    proto: Mapping[str, Any], plugin_id: str = "", manifest: Optional[PluginManifest] = None,
) -> Dict[str, Any]:
    """A protocol ``Row`` as the core's result dict.

    The keys every core row has, whatever produced it::

        title, artist, album, duration, release_date,
        external_ids   {"provider:kind": value}  — the row's own ids
        alternate_ids  {"provider:kind": [value]} — other names for it
        artwork_urls   [url]       — best first; the host walks them
        cover_url      str         — small, for drawing in a list
        artist_refs    [{"name", "external_ids"}]
        album_ref      {"name", "external_ids"} or {}
        source_path    None        — a local file's path, imports only
        provider       str         — the plugin that answered

    ``artist`` is the credits joined with ", ", which is what tags, MPRIS and the
    display want; ``artist_refs`` is the structured form, which is what keeps a
    group with a comma in its name intact and carries each artist's identity.
    Empty lists and dicts mean "not known here", never "there are none".

    With *manifest*, every id is checked against what that plugin declares
    for its entity, and the rest are dropped — see :func:`_only`.
    """
    artists = _str_list(proto.get("artists"))
    if not artists and isinstance(proto.get("artist"), str) and proto.get("artist"):
        artists = [str(proto["artist"])]
    urls = _str_list(proto.get("artworkUrls"))
    refs = [
        {
            "name": str(ref.get("name") or "").strip(),
            "external_ids": _only(
                ids_from_protocol(ref.get("externalIds")), manifest, external_ids.ARTIST
            ),
        }
        for ref in (proto.get("artistRefs") or [])
        if isinstance(ref, Mapping) and str(ref.get("name") or "").strip()
    ]
    album_ref_raw = proto.get("albumRef") or {}
    album_ref: Dict[str, Any] = {}
    if isinstance(album_ref_raw, Mapping) and str(album_ref_raw.get("name") or "").strip():
        album_ref = {
            "name": str(album_ref_raw.get("name")).strip(),
            "external_ids": _only(
                ids_from_protocol(album_ref_raw.get("externalIds")), manifest, external_ids.ALBUM
            ),
        }
    row: Dict[str, Any] = {
        "title": str(proto.get("title") or ""),
        "artist": ", ".join(artists),
        "album": str(proto.get("album") or ""),
        "duration": _int(proto.get("duration")),
        "release_date": _date(proto.get("releaseDate")),
        "external_ids": _only(
            ids_from_protocol(proto.get("externalIds")), manifest, external_ids.TRACK
        ),
        "alternate_ids": _only(
            alternates_from_protocol(proto.get("alternateIds")), manifest, external_ids.TRACK
        ),
        "artwork_urls": urls,
        "cover_url": str(proto.get("thumbnailUrl") or (urls[-1] if urls else "")),
        "artist_refs": refs,
        "album_ref": album_ref,
        "source_path": None,
        "provider": plugin_id,
    }
    # The handful of richer fields a metadata answer may carry. Only present
    # when given, so a row that never had them cannot blank a stored value.
    for proto_key, core_key in (
        ("isrc", "isrc"), ("genres", "genres"), ("bpm", "bpm"),
        ("key", "musical_key"), ("copyright", "copyright_text"),
    ):
        if proto.get(proto_key) not in (None, "", []):
            value = proto[proto_key]
            row[core_key] = ", ".join(value) if isinstance(value, list) else value
    return row


def row_to_protocol(row: Mapping[str, Any]) -> Dict[str, Any]:
    """A core row as a protocol ``Row``, for sending one back to a plugin."""
    refs = row.get("artist_refs") or []
    artists = [str(r.get("name") or "") for r in refs if isinstance(r, Mapping) and r.get("name")]
    if not artists:
        artists = [a.strip() for a in str(row.get("artist") or "").split(",") if a.strip()]
    proto: Dict[str, Any] = {
        "title": str(row.get("title") or ""),
        "artists": artists,
        "album": str(row.get("album") or ""),
        "duration": _int(row.get("duration")),
        "releaseDate": row.get("release_date"),
        "externalIds": ids_to_protocol(row.get("external_ids") or {}),
        "alternateIds": _alternates_to_protocol(row.get("alternate_ids") or {}),
        "artworkUrls": list(row.get("artwork_urls") or []),
        "artistRefs": [
            {"name": str(r.get("name") or ""),
             "externalIds": ids_to_protocol(r.get("external_ids") or {})}
            for r in refs if isinstance(r, Mapping)
        ],
    }
    album_ref = row.get("album_ref") or {}
    if isinstance(album_ref, Mapping) and album_ref.get("name"):
        proto["albumRef"] = {
            "name": str(album_ref.get("name")),
            "externalIds": ids_to_protocol(album_ref.get("external_ids") or {}),
        }
    return proto


def entity_from_protocol(
    proto: Mapping[str, Any], plugin_id: str = "",
    manifest: Optional[PluginManifest] = None, entity: str = external_ids.ARTIST,
) -> Dict[str, Any]:
    """An artist or album answer: ``{"name", "artwork_urls", "external_ids"}``."""
    return {
        "name": str(proto.get("name") or proto.get("title") or ""),
        "artwork_urls": _str_list(proto.get("artworkUrls")),
        "external_ids": _only(ids_from_protocol(proto.get("externalIds")), manifest, entity),
        "provider": plugin_id,
    }


def file_to_protocol(probed: Any) -> Dict[str, Any]:
    """What a probed file — or a CSV row — tells an identity provider about itself."""
    path = getattr(probed, "path", None)
    return {
        "path": str(path) if path else None,
        "name": path.stem if path else "",
        "title": getattr(probed, "title", "") or "",
        "artist": getattr(probed, "artist", "") or "",
        "album": getattr(probed, "album", "") or "",
        "duration": _int(getattr(probed, "duration", 0)),
        "releaseDate": getattr(probed, "release_date", None),
        "externalIds": ids_to_protocol(getattr(probed, "external_ids", {}) or {}),
        "fromCsv": bool(getattr(probed, "from_csv", False)),
    }


def returned_file(raw: Any, output_dir: Path) -> Path:
    """A path a plugin returned, checked to be a file inside *output_dir*.

    The host made that folder for this one request and will delete it; a file
    anywhere else is either a plugin bug or a plugin trying to make the host
    move something it should not, and both are refused the same way.
    """
    if not isinstance(raw, str) or not raw:
        raise UnsafeOutput("The plugin returned no file")
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = output_dir / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to(output_dir.resolve()):
        raise UnsafeOutput("The plugin returned a file outside the folder it was given")
    if not resolved.is_file():
        raise UnsafeOutput("The plugin returned a file that does not exist")
    return resolved


# ----------------------------------------------------------------------
# Providers
# ----------------------------------------------------------------------

class Provider:
    """One plugin, seen as one kind of answer."""

    def __init__(self, host: Any, manifest: PluginManifest) -> None:
        self.host = host
        self.manifest = manifest

    @property
    def plugin_id(self) -> str:
        return self.manifest.id

    @property
    def name(self) -> str:
        return self.manifest.name

    def _row(self, proto: Mapping[str, Any]) -> Dict[str, Any]:
        """A row this plugin sent, holding only ids of kinds it declares."""
        return row_from_protocol(proto, self.plugin_id, self.manifest)

    def _call(self, method: str, params: Mapping[str, Any], *, timeout: Optional[float],
              should_stop: StopCheck = None, on_progress: ProgressCallback = None,
              job_label: str = "") -> Any:
        return self.host.call(
            self.plugin_id, method, params, timeout=timeout,
            should_stop=should_stop, on_progress=on_progress, job_label=job_label,
        )


class TrackSourceProvider(Provider):
    """Rows for a page — today, the Search page's online sources."""

    def __init__(self, host: Any, manifest: PluginManifest, source: TrackSourceDecl,
                 page: Optional[PageDecl] = None) -> None:
        super().__init__(host, manifest)
        self.source = source
        self.page = page

    @property
    def key(self) -> str:
        return f"{self.plugin_id}/{self.source.id}"

    @property
    def label(self) -> str:
        if self.page is not None:
            return self.page.title
        return self.source.label or self.manifest.name

    @property
    def icon(self) -> str:
        return self.page.icon if self.page is not None else ""

    def rows(self, query: str, *, offset: int = 0, limit: int = 20,
             should_stop: StopCheck = None) -> List[Dict[str, Any]]:
        result = self._call("trackSource.rows", {
            "source": self.source.id, "query": query, "offset": offset, "limit": limit,
        }, timeout=TIMEOUT_QUERY, should_stop=should_stop) or {}
        return [self._row(r) for r in result.get("rows") or []
                if isinstance(r, Mapping)]


class IdentityProvider(Provider):
    """"Which recording is this?" — and which artist, and which album."""

    @property
    def entities(self) -> Tuple[str, ...]:
        return self.manifest.entities_for("identity") or (external_ids.TRACK,)

    def identify_file(self, probed: Any, *, filename_fallback: bool = False,
                      should_stop: StopCheck = None) -> Optional[Dict[str, Any]]:
        result = self._call("identify", {
            "entity": "track",
            "file": file_to_protocol(probed),
            "options": {"filenameFallback": bool(filename_fallback)},
        }, timeout=TIMEOUT_LOOKUP, should_stop=should_stop) or {}
        match = result.get("match")
        return self._row(match) if isinstance(match, Mapping) else None

    def identify_row(self, row: Mapping[str, Any], *,
                     should_stop: StopCheck = None) -> Optional[Dict[str, Any]]:
        result = self._call("identify", {
            "entity": "track", "row": row_to_protocol(row), "options": {},
        }, timeout=TIMEOUT_LOOKUP, should_stop=should_stop) or {}
        match = result.get("match")
        return self._row(match) if isinstance(match, Mapping) else None

    def identify_entity(self, entity: str, name: str, tracks: Sequence[Mapping[str, Any]], *,
                        should_stop: StopCheck = None) -> Dict[str, str]:
        """``{"provider:kind": value}`` naming this artist or album, or ``{}``."""
        result = self._call("identify", {
            "entity": entity, "name": name,
            "tracks": [row_to_protocol(t) for t in tracks],
        }, timeout=TIMEOUT_LOOKUP, should_stop=should_stop) or {}
        return _only(ids_from_protocol(result.get("externalIds")), self.manifest, entity)


class MetadataProvider(Provider):
    """What a track, artist or album is called — and what is on an album."""

    @property
    def entities(self) -> Tuple[str, ...]:
        return self.manifest.entities_for("metadata")

    @property
    def album_tracks_supported(self) -> bool:
        return self.manifest.album_tracks

    def get(self, entity: str, ids: Mapping[str, str], *,
            should_stop: StopCheck = None) -> Optional[Dict[str, Any]]:
        result = self._call("metadata.get", {
            "entity": entity, "externalIds": ids_to_protocol(ids),
        }, timeout=TIMEOUT_LOOKUP, should_stop=should_stop) or {}
        row = result.get("row")
        if not isinstance(row, Mapping):
            return None
        if entity == external_ids.TRACK:
            return self._row(row)
        return entity_from_protocol(row, self.plugin_id, self.manifest, entity)

    def album_tracks(self, ids: Mapping[str, str], *,
                     should_stop: StopCheck = None) -> List[Dict[str, Any]]:
        """Every track on the release, **in its running order**. Nothing here may sort."""
        result = self._call("album.tracks", {
            "externalIds": ids_to_protocol(ids),
        }, timeout=TIMEOUT_LOOKUP, should_stop=should_stop) or {}
        return [self._row(r) for r in result.get("rows") or []
                if isinstance(r, Mapping)]


class ArtworkProvider(Provider):
    """Where a cover or a portrait can be fetched from."""

    @property
    def entities(self) -> Tuple[str, ...]:
        return self.manifest.entities_for("artwork")

    def get(self, entity: str, ids: Mapping[str, str], output_dir: Path, *,
            should_stop: StopCheck = None) -> Dict[str, Any]:
        """``{"urls": [...]}`` to walk, or ``{"file": Path}`` already fetched."""
        result = self._call("artwork.get", {
            "entity": entity, "externalIds": ids_to_protocol(ids),
            "outputDir": str(output_dir),
        }, timeout=TIMEOUT_LOOKUP, should_stop=should_stop) or {}
        if result.get("file"):
            return {"file": returned_file(result["file"], output_dir), "urls": []}
        return {"urls": _str_list(result.get("urls"))}


class AudioSource(Provider):
    """"Get audio for this track." The one provider that obtains a recording."""

    @property
    def roles(self) -> Tuple[str, ...]:
        return self.manifest.audio_roles

    @property
    def accepts(self) -> Tuple[str, ...]:
        return self.manifest.audio_accepts

    def accepts_row(self, row: Mapping[str, Any], role: str = "recording") -> bool:
        """Whether this source can fetch *row* — it holds a kind this source takes."""
        if role not in self.roles:
            return False
        ids = row.get("external_ids") or {}
        return any(ids.get(key) for key in self.accepts)

    def get(self, row: Mapping[str, Any], output_dir: Path, *, kind: str = "recording",
            should_stop: StopCheck = None, on_progress: ProgressCallback = None,
            job_label: str = "") -> Tuple[Path, Dict[str, Any]]:
        """Fetch the audio into *output_dir*. Returns ``(file, row describing it)``.

        The second consent gate is here, one line above the request: whatever
        happened since the plugin was started — terms changed by an upgrade,
        consent withdrawn — the request is not sent without it.
        """
        if not self.host.has_consent(self.plugin_id):
            raise ConsentRequired(self.name)
        result = self._call("audio.get", {
            "row": row_to_protocol(row), "kind": kind, "outputDir": str(output_dir),
        }, timeout=TIMEOUT_AUDIO, should_stop=should_stop, on_progress=on_progress,
            job_label=job_label or f"Fetching {row.get('title') or 'audio'}") or {}
        path = returned_file(result.get("file"), output_dir)
        described = result.get("row")
        return path, (self._row(described) if isinstance(described, Mapping) else {})


class ImportSourceProvider(Provider):
    """Rows for the import list from something the user typed or picked."""

    @property
    def sources(self) -> Tuple[ImportSourceDecl, ...]:
        return self.manifest.import_sources

    def resolve(self, source: ImportSourceDecl, text: str, *,
                should_stop: StopCheck = None) -> List[Dict[str, Any]]:
        result = self._call("importSource.resolve", {
            "source": source.local_id, "input": text,
        }, timeout=TIMEOUT_LOOKUP, should_stop=should_stop) or {}
        return [self._row(r) for r in result.get("rows") or []
                if isinstance(r, Mapping)]


# ----------------------------------------------------------------------
# The registry
# ----------------------------------------------------------------------

class ProviderRegistry(QObject):
    """Every capability the enabled plugins offer, asked for by kind.

    Built from the host's manifests on every question rather than cached, so
    turning a plugin off takes its providers away at once — there is no second
    list to keep in step. Asking costs a walk over a handful of manifests.

    ``changed`` fires whenever the answer to any question here may have moved:
    a plugin enabled, disabled, installed, removed or given consent. A view
    model holding something derived — the Search page's list of sources —
    re-reads on it.
    """

    changed = Signal()

    def __init__(self, host: Any = None, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.host = host
        if host is not None and hasattr(host, "plugins_changed"):
            host.plugins_changed.connect(self.changed)

    # -- what is on ------------------------------------------------------

    def _providers(self, kind: str) -> List[PluginManifest]:
        if self.host is None:
            return []
        return list(self.host.providers(kind))

    def declarations(self, entity: Optional[str] = None) -> List[IdKind]:
        """The id kinds enabled plugins declare — what editors, probes and CSVs know of."""
        if self.host is None:
            return []
        return list(self.host.declarations(entity))

    def declaration(self, entity: str, key: str) -> Optional[IdKind]:
        return next((k for k in self.declarations(entity) if k.key == key), None)

    # -- track sources ---------------------------------------------------

    def track_sources(self) -> List[TrackSourceProvider]:
        """Sources the Search page offers: a page whose track source takes a query."""
        found: List[TrackSourceProvider] = []
        for manifest in self._providers("trackSources"):
            for source in manifest.track_sources:
                if not source.query:
                    continue
                page = next((p for p in manifest.pages if p.track_source == source.id), None)
                found.append(TrackSourceProvider(self.host, manifest, source, page))
        return found

    def track_source(self, key: str) -> Optional[TrackSourceProvider]:
        return next((s for s in self.track_sources() if s.key == key), None)

    # -- identity, metadata, artwork --------------------------------------

    def identity(self, entity: str = external_ids.TRACK) -> Optional[IdentityProvider]:
        for manifest in self._providers("identity"):
            provider = IdentityProvider(self.host, manifest)
            if entity in provider.entities:
                return provider
        return None

    def has_identity(self, entity: str = external_ids.TRACK) -> bool:
        return self.identity(entity) is not None

    def metadata(self, entity: str = external_ids.TRACK) -> Optional[MetadataProvider]:
        for manifest in self._providers("metadata"):
            provider = MetadataProvider(self.host, manifest)
            if entity in provider.entities:
                return provider
        return None

    def album_tracks(self) -> Optional[MetadataProvider]:
        for manifest in self._providers("metadata"):
            provider = MetadataProvider(self.host, manifest)
            if provider.album_tracks_supported:
                return provider
        return None

    def artwork(self, entity: str) -> List[ArtworkProvider]:
        """Every artwork provider for *entity*, in priority order — a fallback chain."""
        return [
            provider for provider in (ArtworkProvider(self.host, m) for m in self._providers("artwork"))
            if entity in provider.entities
        ]

    # -- audio -----------------------------------------------------------

    def audio_sources(self, role: str = "recording") -> List[AudioSource]:
        return [
            source for source in (AudioSource(self.host, m) for m in self._providers("audio"))
            if role in source.roles
        ]

    def has_audio_source(self, role: str = "recording") -> bool:
        return bool(self.audio_sources(role))

    def audio_source_for(self, row: Mapping[str, Any], role: str = "recording") -> Optional[AudioSource]:
        """The first source, in priority order, that can fetch *row*."""
        return next((s for s in self.audio_sources(role) if s.accepts_row(row, role)), None)

    def audio_keys(self, role: str = "recording") -> List[str]:
        """Every track key some enabled source can fetch audio by."""
        keys: List[str] = []
        for source in self.audio_sources(role):
            for key in source.accepts:
                if key not in keys:
                    keys.append(key)
        return keys

    # -- import sources --------------------------------------------------

    def import_sources(self) -> List[Tuple[ImportSourceProvider, ImportSourceDecl]]:
        found = []
        for manifest in self._providers("importSources"):
            provider = ImportSourceProvider(self.host, manifest)
            found.extend((provider, decl) for decl in provider.sources)
        return found

    def import_source(self, key: str) -> Optional[Tuple[ImportSourceProvider, ImportSourceDecl]]:
        return next(((p, d) for p, d in self.import_sources() if d.id == key), None)

    # -- consent ---------------------------------------------------------

    def has_consent(self, plugin_id: str) -> bool:
        return bool(self.host is not None and self.host.has_consent(plugin_id))


def first_artwork(
    registry: ProviderRegistry,
    entity: str,
    ids: Mapping[str, str],
    output_dir: Path,
    fetch_urls: Callable[[Iterable[str]], Optional[bytes]],
    *,
    should_stop: StopCheck = None,
) -> Optional[bytes]:
    """The first picture any artwork provider can supply, as bytes. ``None`` if none can.

    A fallback chain, first answer wins: each provider in priority order is
    asked, its URLs are walked with *fetch_urls*, and a provider that answers
    nothing — or fails — hands over to the next. Never raises, because artwork
    is a bonus on every path that asks for it.
    """
    if not ids:
        return None
    for provider in registry.artwork(entity):
        if should_stop is not None and should_stop():
            return None
        try:
            answer = provider.get(entity, ids, output_dir, should_stop=should_stop)
        except PluginError as exc:
            logger.debug("Artwork from %s failed: %s", provider.plugin_id, exc)
            continue
        if answer.get("file"):
            try:
                return Path(answer["file"]).read_bytes()
            except OSError:
                continue
        found = fetch_urls(answer.get("urls") or [])
        if found:
            return found
    return None
