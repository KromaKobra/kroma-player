"""In-process stand-ins for plugin providers.

Everything above :mod:`app.plugins.providers` is written against a
:class:`~app.plugins.providers.ProviderRegistry`, so a test that wants "a plugin
that identifies tracks" hands a view model a :class:`FakeRegistry` holding a
:class:`FakeIdentity` — and no process, no protocol and no network is involved.
What each fake answers is a dict lookup the test fills in.

The id kinds are real: :data:`KINDS` is parsed out of :data:`MANIFEST` by the
same code that reads an installed plugin's manifest, so parse rules, labels and
roles behave exactly as they would. The provider is ``fake``: the core keeps
whatever kinds a plugin declares, so a made-up catalogue is as real to it as
any other.

Slow providers take a ``threading.Event`` gate, the same hook
``test_import_cancel`` uses: the call blocks until the test opens it, so a test
can cancel, abort or tear down while a request is in flight.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence

from app.models.track import AlbumExternalId, ArtistExternalId, TrackExternalId
from app.plugins.external_ids import split_key
from app.plugins.manifest import parse
from app.plugins.process import PluginCancelled
from app.plugins.providers import ProviderRegistry

PLUGIN_ID = "test.fake"

VIDEO = "fake:video"
INSTRUMENTAL = "fake:instrumental-video"
CHANNEL = "fake:channel"
RELEASE = "fake:release"

#: The Vorbis comment the fake plugin mirrors a track's :data:`VIDEO` id into.
VIDEO_TAG = "FAKEID"

MANIFEST: Dict[str, Any] = {
    "id": PLUGIN_ID,
    "name": "Fake Source",
    "version": "1.0.0",
    "api": 13,
    "backend": {"runtime": "python", "main": "main.py"},
    "externalIds": [
        {
            "entity": "track", "provider": "fake", "kind": "video",
            "role": "recording", "label": "Fake id or link", "sourceLabel": "Fake Music",
            "url": "https://fake.example/watch?v={value}",
            "parse": ["^([A-Za-z0-9_-]{11})$", "watch\\?v=([A-Za-z0-9_-]{11})"],
            "tagScan": ["watch\\?v=([A-Za-z0-9_-]{11})"],
            "readTags": [VIDEO_TAG],
            "writeTag": VIDEO_TAG,
            "csvColumns": ["fake_id", "fid"],
        },
        {
            "entity": "track", "provider": "fake", "kind": "instrumental-video",
            "role": "instrumental", "label": "Fake instrumental id",
            "parse": ["^([A-Za-z0-9_-]{11})$", "watch\\?v=([A-Za-z0-9_-]{11})"],
        },
        {
            "entity": "artist", "provider": "fake", "kind": "channel",
            "label": "Fake channel id", "sourceLabel": "Fake Music",
            "url": "https://fake.example/channel/{value}",
            "parse": ["^((?:UC|MPLA)[A-Za-z0-9_-]{8,})$", "/channel/((?:UC|MPLA)[A-Za-z0-9_-]{8,})"],
        },
        {
            "entity": "album", "provider": "fake", "kind": "release",
            "label": "Fake album id", "sourceLabel": "Fake Music",
            "url": "https://fake.example/browse/{value}",
            "parse": ["^((?:MPRE|OLAK5uy_)[A-Za-z0-9_-]*)$", "browse/((?:MPRE|OLAK5uy_)[A-Za-z0-9_-]+)"],
        },
    ],
    "provides": {
        "audio": {"roles": ["recording", "instrumental"], "accepts": [VIDEO, INSTRUMENTAL]},
        "trackSources": [{"id": "search", "query": True, "label": "Fake Music"}],
        "identity": {"entities": ["track", "artist", "album"]},
        "metadata": {"entities": ["track", "artist", "album"], "albumTracks": True},
        "artwork": {"entities": ["track", "artist", "album"]},
        "importSources": {},
    },
    "contributes": {
        "pages": [{"id": "search", "title": "Fake Music", "trackSource": "search"}],
        "importSources": [{"id": "link", "label": "From a fake link", "input": "text"}],
        "actions": [
            {"id": "open", "label": "Open on Fake Music", "on": ["track"],
             "openUrl": "https://fake.example/watch?v={track/fake/video}"},
            {"id": "copy", "label": "Copy id", "on": ["track"], "copy": "{track/fake/video}"},
        ],
    },
}

FAKE_MANIFEST = parse(json.dumps(MANIFEST), "fake plugin.json")
KINDS = FAKE_MANIFEST.external_ids

_ID_ROWS = {"track": TrackExternalId, "artist": ArtistExternalId, "album": AlbumExternalId}


def id_rows(entity: str, ids: Mapping[str, str]) -> List[Any]:
    """Model rows for ``Track(external_id_rows=...)``, and the same for artists and albums.

    The ids live in tables of their own now, so a test that builds a row
    holding one hands it these. ``{VIDEO: "abc"}`` reads the way the test
    means it.
    """
    model = _ID_ROWS[entity]
    out = []
    for key, value in ids.items():
        provider, kind = split_key(key)
        out.append(model(provider=provider, kind=kind, value=value))
    return out


def set_id(obj: Any, key: str, value: str) -> None:
    """Give the model row *obj* this id, as an edit straight to the database would.

    Changes the value in place when *obj* already holds the kind. Replacing the
    row instead would insert its successor before deleting it, and both have
    the same primary key.
    """
    provider, kind = split_key(key)
    for row in obj.external_id_rows:
        if (row.provider, row.kind) == (provider, kind):
            row.value = value
            return
    model = type(obj).external_id_rows.property.entity.class_
    obj.external_id_rows.append(model(provider=provider, kind=kind, value=value))


def held_id(obj: Any, key: str) -> Optional[str]:
    """The value the model row *obj* holds under *key*, or None when it holds none."""
    provider, kind = split_key(key)
    return next(
        (r.value for r in obj.external_id_rows if (r.provider, r.kind) == (provider, kind)),
        None,
    )


def row(
    title: str = "",
    artist: str = "",
    *,
    video: str = "",
    album: str = "",
    duration: int = 0,
    release_date: Optional[int] = None,
    alternates: Sequence[str] = (),
    artwork: Sequence[str] = (),
    refs: Sequence[Mapping[str, Any]] = (),
    album_ref: Optional[Mapping[str, Any]] = None,
    **extra: Any,
) -> Dict[str, Any]:
    """A core row, as :func:`app.plugins.providers.row_from_protocol` makes them."""
    return {
        "title": title,
        "artist": artist,
        "album": album,
        "duration": duration,
        "release_date": release_date,
        "external_ids": {VIDEO: video} if video else {},
        "alternate_ids": {VIDEO: list(alternates)} if alternates else {},
        "artwork_urls": list(artwork),
        "cover_url": artwork[-1] if artwork else "",
        "artist_refs": [dict(r) for r in refs],
        "album_ref": dict(album_ref or {}),
        "source_path": None,
        "provider": PLUGIN_ID,
        **extra,
    }


def _wait(gate: Optional[threading.Event], should_stop: Optional[Callable[[], bool]]) -> None:
    """Block on *gate*, but give up — as a real call would — when told to stop."""
    if gate is None:
        return
    while not gate.wait(0.02):
        if should_stop is not None and should_stop():
            raise PluginCancelled("Cancelled")


class _Fake:
    plugin_id = PLUGIN_ID
    name = "Fake Source"
    manifest = FAKE_MANIFEST

    def __init__(self, gate: Optional[threading.Event] = None) -> None:
        self.gate = gate
        self.calls: List[Any] = []


class FakeTrackSource(_Fake):
    key = f"{PLUGIN_ID}/search"
    label = "Fake Music"
    icon = ""

    def __init__(self, results: Optional[Dict[str, List[Dict[str, Any]]]] = None, **kw) -> None:
        super().__init__(**kw)
        self.results = results or {}

    def rows(self, query, *, offset=0, limit=20, should_stop=None):
        self.calls.append(query)
        _wait(self.gate, should_stop)
        return [dict(r) for r in self.results.get(query, [])][offset:offset + limit]


class FakeIdentity(_Fake):
    """Identifies files by title (or path), and artists and albums by name."""

    entities = ("track", "artist", "album")

    def __init__(
        self,
        matches: Optional[Dict[str, Dict[str, Any]]] = None,
        entities: Optional[Dict[str, Dict[str, str]]] = None,
        error: Optional[Exception] = None,
        **kw,
    ) -> None:
        super().__init__(**kw)
        self.matches = matches or {}
        self.entity_ids = entities or {}
        self.error = error

    def identify_file(self, probed, *, filename_fallback=False, should_stop=None):
        self.calls.append(("file", getattr(probed, "title", ""), filename_fallback))
        _wait(self.gate, should_stop)
        if self.error is not None:
            raise self.error
        for key in (getattr(probed, "source_path", ""), getattr(probed, "title", "")):
            if key and key in self.matches:
                return dict(self.matches[key])
        video = (getattr(probed, "external_ids", {}) or {}).get(VIDEO)
        if video and video in self.matches:
            return dict(self.matches[video])
        return None

    def identify_row(self, row, *, should_stop=None):
        _wait(self.gate, should_stop)
        return dict(self.matches[row.get("title")]) if row.get("title") in self.matches else None

    def identify_entity(self, entity, name, tracks, *, should_stop=None):
        self.calls.append((entity, name, [dict(t) for t in tracks]))
        _wait(self.gate, should_stop)
        if self.error is not None:
            raise self.error
        return dict(self.entity_ids.get(name, {}))


class FakeMetadata(_Fake):
    entities = ("track", "artist", "album")
    album_tracks_supported = True

    def __init__(
        self,
        answers: Optional[Dict[str, Dict[str, Any]]] = None,
        releases: Optional[Dict[str, List[Dict[str, Any]]]] = None,
        **kw,
    ) -> None:
        super().__init__(**kw)
        self.answers = answers or {}
        self.releases = releases or {}

    def get(self, entity, ids, *, should_stop=None):
        self.calls.append((entity, dict(ids)))
        _wait(self.gate, should_stop)
        for value in ids.values():
            if value in self.answers:
                return dict(self.answers[value])
        return None

    def album_tracks(self, ids, *, should_stop=None):
        self.calls.append(("album.tracks", dict(ids)))
        _wait(self.gate, should_stop)
        for value in ids.values():
            if value in self.releases:
                return [dict(r) for r in self.releases[value]]
        return []


class FakeArtwork(_Fake):
    entities = ("track", "artist", "album")

    def __init__(self, urls: Optional[Dict[str, List[str]]] = None, **kw) -> None:
        super().__init__(**kw)
        self.urls = urls or {}

    def get(self, entity, ids, output_dir, *, should_stop=None):
        self.calls.append((entity, dict(ids)))
        for value in ids.values():
            if value in self.urls:
                return {"urls": list(self.urls[value])}
        return {"urls": []}


class FakeAudio(_Fake):
    """Writes a file for each fetch — a real Opus copy when given one, else bytes."""

    roles = ("recording", "instrumental")
    accepts = (VIDEO, INSTRUMENTAL)

    def __init__(
        self,
        source_file: Optional[Path] = None,
        described: Optional[Dict[str, Dict[str, Any]]] = None,
        error: Optional[Exception] = None,
        consent: bool = True,
        **kw,
    ) -> None:
        super().__init__(**kw)
        self.source_file = source_file
        self.described = described or {}
        self.error = error
        self.consent = consent

    def accepts_row(self, row, role="recording"):
        if role not in self.roles:
            return False
        ids = row.get("external_ids") or {}
        return any(ids.get(key) for key in self.accepts)

    def get(self, row, output_dir, *, kind="recording", should_stop=None,
            on_progress=None, job_label=""):
        ids = row.get("external_ids") or {}
        self.calls.append((kind, dict(ids)))
        _wait(self.gate, should_stop)
        if self.error is not None:
            raise self.error
        if on_progress is not None:
            on_progress({"stage": "fetching", "percent": 50})
        value = ids.get(VIDEO) or ids.get(INSTRUMENTAL) or "audio"
        target = Path(output_dir) / f"{value}.opus"
        if self.source_file is not None:
            target.write_bytes(Path(self.source_file).read_bytes())
        else:
            target.write_bytes(b"not really audio")
        return target, dict(self.described.get(value, {}))


class FakeImportSource(_Fake):
    def __init__(self, results: Optional[Dict[str, List[Dict[str, Any]]]] = None, **kw) -> None:
        super().__init__(**kw)
        self.results = results or {}

    @property
    def sources(self):
        return FAKE_MANIFEST.import_sources

    def resolve(self, source, text, *, should_stop=None):
        self.calls.append((source.local_id, text))
        _wait(self.gate, should_stop)
        return [dict(r) for r in self.results.get(text, [])]


class FakeRegistry(ProviderRegistry):
    """A registry that answers from the fakes it was given, and nothing else.

    Each capability is present exactly when a fake for it was passed. *kinds*
    are the declared id kinds — every kind in :data:`KINDS` by default, as if
    the fake plugin were enabled; pass ``()`` for a plain install that has none.
    """

    def __init__(
        self,
        *,
        identity: Optional[FakeIdentity] = None,
        metadata: Optional[FakeMetadata] = None,
        artwork: Optional[FakeArtwork] = None,
        audio: Optional[FakeAudio] = None,
        sources: Iterable[FakeTrackSource] = (),
        import_source: Optional[FakeImportSource] = None,
        kinds: Optional[Sequence[Any]] = None,
        consent: bool = True,
    ) -> None:
        super().__init__(None)
        self.identity_provider = identity
        self.metadata_provider = metadata
        self.artwork_provider = artwork
        self.audio = audio
        self.sources = list(sources)
        self.import_provider = import_source
        self.kinds = list(KINDS if kinds is None else kinds)
        self.consent = consent

    def set(self, **changes: Any) -> None:
        """Swap providers in and out, and announce it — a plugin toggled."""
        for key, value in changes.items():
            setattr(self, key, value)
        self.changed.emit()

    # -- declarations --------------------------------------------------

    def declarations(self, entity=None):
        return [k for k in self.kinds if entity is None or k.entity == entity]

    # -- capabilities --------------------------------------------------

    def track_sources(self):
        return list(self.sources)

    def identity(self, entity="track"):
        p = self.identity_provider
        return p if p is not None and entity in p.entities else None

    def metadata(self, entity="track"):
        p = self.metadata_provider
        return p if p is not None and entity in p.entities else None

    def album_tracks(self):
        return self.metadata_provider

    def artwork(self, entity):
        p = self.artwork_provider
        return [p] if p is not None and entity in p.entities else []

    def audio_sources(self, role="recording"):
        return [self.audio] if self.audio is not None and role in self.audio.roles else []

    def import_sources(self):
        if self.import_provider is None:
            return []
        return [(self.import_provider, decl) for decl in self.import_provider.sources]

    def has_consent(self, plugin_id):
        return self.consent


# ----------------------------------------------------------------------
# Adapting the doubles the import tests were written with
# ----------------------------------------------------------------------

def from_legacy(result: Optional[Mapping[str, Any]]) -> Optional[Dict[str, Any]]:
    """A result dict in the shape the core used before plugins, as a core row.

    The import tests were written against an engine that answered with
    ``source_id``, ``alt_source_ids`` and refs carrying ``source_id``. What they
    test — merging, dedupe, classification, cancellation — did not change, so
    their doubles are translated here rather than each test rewritten.
    """
    if result is None:
        return None
    out = dict(result)
    video = str(out.pop("source_id", "") or "")
    ids = dict(out.get("external_ids") or {})
    if video:
        ids[VIDEO] = video
    out["external_ids"] = ids
    alternates = [str(a) for a in out.pop("alt_source_ids", None) or [] if a]
    out["alternate_ids"] = {VIDEO: alternates} if alternates else dict(out.get("alternate_ids") or {})
    master = out.pop("cover_master_url", "") or ""
    urls = list(out.get("artwork_urls") or [])
    if master and master not in urls:
        urls.append(master)
    out["artwork_urls"] = urls
    out.setdefault("cover_url", "")
    out["artist_refs"] = [
        {"name": r.get("name", ""),
         "external_ids": {CHANNEL: r["source_id"]} if r.get("source_id") else dict(r.get("external_ids") or {}),
         **({"image_hash": r["image_hash"]} if r.get("image_hash") else {})}
        for r in out.get("artist_refs") or []
    ]
    album_ref = out.get("album_ref") or {}
    if album_ref:
        out["album_ref"] = {
            "name": album_ref.get("name", ""),
            "external_ids": {RELEASE: album_ref["source_id"]} if album_ref.get("source_id")
            else dict(album_ref.get("external_ids") or {}),
        }
    else:
        out["album_ref"] = {}
    out.setdefault("provider", PLUGIN_ID)
    return out


class EngineIdentity(FakeIdentity):
    """An identity provider answering through an old engine double's ``match_file``."""

    def __init__(self, engine: Any) -> None:
        super().__init__()
        self.engine = engine

    def identify_file(self, probed, *, filename_fallback=False, should_stop=None):
        if not hasattr(probed, "source_id"):
            # The engine doubles read `probed.source_id`; the probe now
            # carries a map. A view, not a copy, so nothing they do leaks.
            probed.__dict__.setdefault("source_id", (probed.external_ids or {}).get(VIDEO, ""))
        return from_legacy(self.engine.match_file(probed, filename_fallback))


def registry_for(engine: Any = None, **kwargs: Any) -> FakeRegistry:
    """A registry whose identity provider is *engine*, with anything else given."""
    if engine is not None and "identity" not in kwargs:
        kwargs["identity"] = EngineIdentity(engine)
    return FakeRegistry(**kwargs)


def use_registry(view_model: Any, registry: ProviderRegistry) -> None:
    """Point an already-built editor at *registry*, as bootstrap would have.

    For tests that build the whole application (and so get the real, empty
    plugin host) but want one page to see a plugin: the editor and the id
    staging inside it both read the registry, and a toggle has to reach them.
    """
    view_model._registry = registry
    staging = getattr(view_model, "_ids", None)
    if staging is not None:
        staging._registry = registry
    handler = getattr(view_model, "_on_providers_changed", None)
    if handler is not None:
        registry.changed.connect(handler)
        handler()
