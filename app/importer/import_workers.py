"""Background QObject workers for the import pipeline.

Each worker is moved onto its own ``QThread`` by the view model and communicates
results back via signals (delivered to the main thread as queued connections).
The import queue's phases run off the UI thread:

* :class:`ImportAnalyzeWorker` — scan + probe local files (disk I/O only).
* :class:`ImportSearchWorker`  — the Identify step: ask the identity provider
  which recording each probed file is, or — with none enabled — skip it and
  build each row from the file's own tags.
* :class:`ImportStashWorker`   — transcode + tag + persist one local row.
* :class:`FetchAudioWorker`    — fetch one wanted row's audio from an audio
  source, then stash it.
* :class:`CoverPrefetchWorker` — fetch one row's artwork ahead of its stash.

And the ones an edit page drives, acting on the one track, artist or album the
user is already looking at:

* :class:`InstrumentalImportWorker` — transcode one file into ``bt/`` as an
  existing track's instrumental.
* :class:`InstrumentalFetchWorker` — fetch an instrumental from an audio source
  into ``bt/``.
* :class:`RefreshWorker` — re-read one track's, artist's or album's metadata
  and artwork from a metadata provider, without fetching any audio.
* :class:`IdentifyWorker` — work out an artist's or album's external id from
  its own tracks, then read the picture it points at.
* :class:`AlbumTracksWorker` — read a whole release's track list, in order.
* :class:`ImportSourceWorker` — resolve what the user typed into a plugin's
  import source into rows.

:class:`AlbumTracksWorker` serves both sides: the album page starts it to learn a
running order, and the import tab starts it to fill its list.

**None of them speaks to a plugin directly.** Each is handed the
:class:`~app.plugins.providers.ProviderRegistry` and asks it for a capability,
passing its own stop flag into every call so a cancel reaches the plugin as
``cancel`` rather than waiting for an answer nobody wants. With no plugins, the
registry answers ``None`` and each worker has a path for that.

The analyze and identify workers touch no Qt models and no database, so they are
fully thread-safe. Every worker here is cancellable — see
:class:`CancellableWorker` — but the stash workers stop only where it is safe
to: each of them does its slow work in a temporary directory and commits at the
end, so the last place they will listen is just before that commit. Stopping
after it would leave a track row whose file was never moved into the vault,
which is worse than the wait it saved. See ``app.importer.stash.StopCheck``.
The view model runs stash and fetch workers one at a time to avoid concurrent
writers.

:class:`CoverPrefetchWorker` is the one worker that runs *beside* another. It is
allowed to because it is the one worker that writes nothing: it fetches bytes
and hands them back, touching no model, no database and no service. What it buys
is the overlap the serial queue otherwise gives up — a stash spends most of its
time transcoding and encoding on the CPU, so the next row's artwork can come
down the wire during it for free. It needs no plugin either: a row's artwork is
a list of URLs it already carries.
"""

from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping, Optional, Sequence

from PySide6.QtCore import QObject, Signal, Slot

from app.artwork.cover_service import CoverService
from app.importer.csv_import import csv_overlay_result
from app.importer.fetch import NoAudioSource, fetch_and_stash, fetch_audio
from app.importer.media_probe import ProbedFile
from app.importer.stash import fetch_first, stash_imported_track
from app.lyrics.lookup import lookup_lyrics
from app.plugins import external_ids
from app.plugins.paths import temp_root
from app.plugins.process import PluginCancelled
from app.plugins.providers import first_artwork
from app.services.import_service import ImportAnalysis, ImportService
from app.services.track_service import TrackService

logger = logging.getLogger(__name__)

#: How a row came to be what it is. The import classifier reads this rather than
#: guessing from which fields are filled in.
IDENTIFY_MATCHED = "matched"
IDENTIFY_UNMATCHED = "unmatched"
IDENTIFY_SKIPPED = "skipped"


def row_from_probe(probed: ProbedFile, identify: str) -> Dict[str, Any]:
    """A core row built from a probed file's own tags.

    What the Identify step produces when it found nothing (``unmatched``) or was
    not run at all (``skipped`` — no identity provider is enabled). The two look
    alike here and mean different things to the classifier: a file nothing
    matched is worth flagging, and a file nobody tried to match is simply a
    file.
    """
    return {
        "title": probed.title,
        "artist": probed.artist,
        "album": probed.album,
        "duration": probed.duration,
        "release_date": probed.release_date,
        "external_ids": dict(probed.external_ids),
        "alternate_ids": {},
        "artwork_urls": [],
        "cover_url": "",
        "artist_refs": [],
        "album_ref": {},
        "source_path": probed.source_path or None,
        "identify": identify,
        "not_found": identify == IDENTIFY_UNMATCHED,
    }


class CancellableWorker(QObject):
    """A worker the UI thread can ask to stop.

    Cancellation is cooperative and coarse on purpose. Neither of the two long
    phases can be interrupted where it stands — one is inside a file read, the
    other inside a plugin call — so ``cancel`` sets a flag and the worker
    notices it at the next item boundary. A plugin call polls the same flag and
    sends ``cancel`` for its request, which is as close to "now" as a process
    boundary allows. What makes that acceptable is that a cancelled worker emits
    no result: the view model has already moved on, and whatever this finishes
    computing is dropped on the floor.

    ``_stop`` is a plain bool written from one thread and read from another,
    which is safe here for the same reason it is everywhere in Qt: a single
    aligned word, never read-modify-written, and correctness does not depend on
    *which* iteration observes the change.
    """

    def __init__(self) -> None:
        super().__init__()
        self._stop = False

    def cancel(self) -> None:
        """Ask the run to stop at the next item. Callable from the UI thread."""
        self._stop = True

    def _stopped(self) -> bool:
        return self._stop


class ImportAnalyzeWorker(CancellableWorker):
    """Scan + probe the selected files/folders off the UI thread."""

    finished = Signal(object)   # ImportAnalysis
    failed = Signal(str)
    done = Signal()

    def __init__(
        self, paths: List[str], service: ImportService, kinds: Sequence[Any] = ()
    ) -> None:
        super().__init__()
        self._paths = paths
        self._service = service
        # A snapshot taken on the UI thread: frozen declarations, so the probe
        # reads immutable data and never asks the plugin host anything.
        self._kinds = tuple(kinds)

    @Slot()
    def run(self) -> None:
        try:
            analysis: ImportAnalysis = self._service.analyze(
                self._paths, lambda: self._stop, self._kinds
            )
            if self._stop:
                return          # a cancelled scan has nothing to report
            self.finished.emit(analysis)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Import analysis failed")
            self.failed.emit(str(exc))
        finally:
            self.done.emit()


class ImportSearchWorker(CancellableWorker):
    """The Identify step: which recording is each probed file?

    One result per candidate, always — the contract the merge relies on. With
    an identity provider, a match becomes a ``matched`` row and a miss an
    ``unmatched`` one built from the file's own tags. Without one, every row is
    ``skipped``: built from the tags, no network, no "matched 0 of N".
    """

    progress = Signal(int, int)   # completed, total
    finished = Signal(list)       # list[row dict], one per candidate
    failed = Signal(str)
    done = Signal()

    def __init__(
        self,
        probed_files: List[ProbedFile],
        use_filename_fallback: bool,
        registry: Any,
    ) -> None:
        super().__init__()
        self._files = probed_files
        self._use_filename_fallback = use_filename_fallback
        self._registry = registry

    @Slot()
    def run(self) -> None:
        try:
            identity = (
                self._registry.identity(external_ids.TRACK)
                if self._registry is not None else None
            )
            results: List[Dict[str, Any]] = []
            total = len(self._files)
            for index, probed in enumerate(self._files):
                # Between files, not inside one: an identify is a blocking call
                # into a plugin. Returning here drops `results` without emitting
                # it, which is what "cancel throws away what it found" means.
                if self._stop:
                    return
                results.append(csv_overlay_result(self._identify(identity, probed), probed))
                self.progress.emit(index + 1, total)
            if self._stop:
                return
            self.finished.emit(results)
        except PluginCancelled:
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("Import identify failed")
            self.failed.emit(str(exc))
        finally:
            self.done.emit()

    def _identify(self, identity: Any, probed: ProbedFile) -> Dict[str, Any]:
        if identity is None:
            return row_from_probe(probed, IDENTIFY_SKIPPED)
        try:
            match = identity.identify_file(
                probed,
                filename_fallback=self._use_filename_fallback,
                should_stop=self._stopped,
            )
        except PluginCancelled:
            raise
        except Exception as exc:  # noqa: BLE001 — one bad file never aborts
            logger.warning("Identify failed for %s: %s", probed.label, exc)
            match = None
        if match is None:
            return row_from_probe(probed, IDENTIFY_UNMATCHED)
        match["source_path"] = probed.source_path or None
        # What the file knew that the match did not. A provider that matched by
        # title may know no date; the file's own is better than none.
        if match.get("release_date") is None and probed.release_date:
            match["release_date"] = probed.release_date
        for key, value in probed.external_ids.items():
            match.setdefault("external_ids", {}).setdefault(key, value)
        match["identify"] = IDENTIFY_MATCHED
        match["not_found"] = False
        return match


class ImportStashWorker(CancellableWorker):
    """Stash a single local import row (transcode + tag + persist)."""

    finished = Signal(int, int)   # uid, track_id
    failed = Signal(int, str)     # uid, error message
    done = Signal(int)            # uid (always emitted last)

    def __init__(
        self,
        uid: int,
        result: Dict[str, Any],
        track_service: TrackService,
        cover_service: CoverService | None = None,
        cover_bytes: bytes | None = None,
        registry: Any = None,
        lyrics_service=None,
    ) -> None:
        super().__init__()
        self._uid = uid
        self._result = result
        self._track_service = track_service
        self._cover_service = cover_service
        self._cover_bytes = cover_bytes
        self._registry = registry
        self._lyrics_service = lyrics_service

    @Slot()
    def run(self) -> None:
        try:
            track_id = stash_imported_track(
                self._result, self._track_service, self._cover_service,
                lambda: self._stop,
                cover_bytes=self._cover_bytes,
                registry=self._registry,
                lyrics_service=self._lyrics_service,
            )
            # Asked, rather than inferred from the absent track id: a stash that
            # was stopped and one that failed both come back empty, and only one
            # of them is worth putting an error in front of the user for.
            if self._stop:
                return
            if track_id:
                self.finished.emit(self._uid, track_id)
            else:
                self.failed.emit(self._uid, "Stash returned no track id")
        except Exception as exc:  # noqa: BLE001
            logger.exception("Stash failed for uid %s", self._uid)
            self.failed.emit(self._uid, str(exc))
        finally:
            self.done.emit(self._uid)


class FetchAudioWorker(CancellableWorker):
    """Fetch one wanted row's audio from an audio source and stash it.

    Mirrors :class:`ImportStashWorker`'s uid-keyed signal shape, so the import
    list and the search results track it the same way. *metadata_override* is
    what the caller knew better than the source — a CSV row's columns, a search
    result's full artist credits — applied over the source's answer.

    ``progress`` carries the plugin's own reports, for a row to draw.
    """

    finished = Signal(int, int)        # uid, track_id
    failed = Signal(int, str)          # uid, error message
    done = Signal(int)                 # uid (always emitted last)
    progress = Signal(int, str, float)  # uid, stage, percent (-1 unknown)

    def __init__(
        self,
        uid: int,
        row: Mapping[str, Any],
        track_service: TrackService,
        registry: Any,
        cover_service: CoverService | None = None,
        metadata_override: Optional[Mapping[str, Any]] = None,
        lyrics_service=None,
    ) -> None:
        super().__init__()
        self._uid = uid
        self._row = dict(row)
        self._track_service = track_service
        self._registry = registry
        self._cover_service = cover_service
        self._metadata_override = dict(metadata_override or {}) or None
        self._lyrics_service = lyrics_service

    def _report(self, params: Mapping[str, Any]) -> None:
        percent = params.get("percent")
        try:
            value = float(percent) if percent is not None else -1.0
        except (TypeError, ValueError):
            value = -1.0
        self.progress.emit(self._uid, str(params.get("stage") or ""), value)

    @Slot()
    def run(self) -> None:
        try:
            track_id = fetch_and_stash(
                self._row,
                self._track_service,
                self._registry,
                self._cover_service,
                self._metadata_override,
                lambda: self._stop,
                lyrics_service=self._lyrics_service,
                on_progress=self._report,
            )
            if self._stop:
                return
            if track_id:
                self.finished.emit(self._uid, track_id)
            else:
                self.failed.emit(self._uid, "The fetch returned no track")
        except PluginCancelled:
            return
        except Exception as exc:  # noqa: BLE001
            if self._stop:
                return
            logger.warning("Fetch failed for uid %s: %s", self._uid, exc)
            self.failed.emit(self._uid, str(exc))
        finally:
            self.done.emit(self._uid)


class CoverPrefetchWorker(CancellableWorker):
    """Fetch one row's cover art ahead of the stash that will need it.

    Runs concurrently with the stash worker, which is safe here and nowhere else
    in this module: this touches no Qt model, no database and no service, and
    its whole output is a `bytes` handed back over a signal. A prefetch that
    fails, is cancelled, or finds no artwork simply reports ``None`` and the
    stash fetches its own — so nothing downstream has to distinguish "not
    prefetched" from "prefetched nothing".

    It walks the URLs the row already carries, best first, and so needs no
    plugin call: whatever identified the row said where its pictures are.
    """

    finished = Signal(int, object)   # uid, bytes | None
    failed = Signal(int, str)        # uid, error message
    done = Signal()                  # always emitted last

    def __init__(self, uid: int, urls: Sequence[str]) -> None:
        super().__init__()
        self._uid = uid
        self._urls = [str(u) for u in urls if u]

    @Slot()
    def run(self) -> None:
        try:
            cover_bytes = fetch_first(self._urls, should_stop=self._stopped)
            # A cancelled prefetch reports nothing, for the same reason the
            # other workers do: the view model has already dropped the row this
            # belonged to, and caching art for it would outlive the queue entry.
            if self._stop:
                return
            self.finished.emit(self._uid, cover_bytes)
        except Exception as exc:  # noqa: BLE001 — art is optional; never surface
            logger.warning("Cover prefetch failed for uid %s: %s", self._uid, exc)
            self.failed.emit(self._uid, str(exc))
        finally:
            self.done.emit()


class InstrumentalImportWorker(CancellableWorker):
    """Install one local file as a named track's instrumental.

    Keyed by track id and nothing else, because that is all an instrumental is:
    the same recording without vocals, stored beside the mix under the same
    name. There is no database write and no metadata — the track already has
    both, and a second copy of them in ``bt/`` could only ever go stale.

    Unlike the workers above this is not part of the import queue. It belongs to
    the edit page, which acts on one track that the user is already looking at.
    """

    finished = Signal(int)        # track_id
    failed = Signal(str)          # error message
    done = Signal()               # always emitted last

    def __init__(self, track_id: int, source_path: str, backing_service) -> None:
        super().__init__()
        self._track_id = track_id
        self._source_path = source_path
        self._backing = backing_service

    @Slot()
    def run(self) -> None:
        try:
            self._backing.import_file(
                self._track_id, self._source_path, lambda: self._stop
            )
            if self._stop:
                return
            self.finished.emit(self._track_id)
        except Exception as exc:  # noqa: BLE001
            logger.exception("Instrumental import failed for track %s", self._track_id)
            self.failed.emit(str(exc))
        finally:
            self.done.emit()


class InstrumentalFetchWorker(CancellableWorker):
    """Fetch an instrumental from an audio source and install it for a named track.

    :class:`InstrumentalImportWorker` with an ``audio.get`` in front of it. The
    request asks for the ``instrumental`` kind, so it goes through the same
    consent gates every other fetch does — it is a recording being obtained,
    whatever it is attached to — and lands in a temporary directory that is gone
    again once ``bt/`` has its copy.

    The length check is skipped. A separate upload of an instrumental is almost
    never the mix's length to the frame, so the rule a hand-picked file is held
    to would refuse nearly every one of these. See
    :meth:`~app.services.backing_track_service.BackingTrackService.import_file`.

    Like every worker here it writes nothing to the library. ``finished``
    carries the id back so the UI thread can record it against the track.
    """

    finished = Signal(int, str, str)  # track_id, key, value
    failed = Signal(str)              # error message
    done = Signal()                   # always emitted last

    def __init__(
        self,
        track_id: int,
        key: str,
        value: str,
        backing_service,
        registry: Any,
    ) -> None:
        super().__init__()
        self._track_id = track_id
        self._key = key
        self._value = value
        self._backing = backing_service
        self._registry = registry

    @Slot()
    def run(self) -> None:
        try:
            row = {"external_ids": {self._key: self._value}}
            with tempfile.TemporaryDirectory(dir=temp_root(), ignore_cleanup_errors=True) as tmp:
                output = Path(tmp)
                audio_path, _row = fetch_audio(
                    row, self._registry, output, kind="instrumental",
                    should_stop=self._stopped,
                )
                if self._stop:
                    return
                installed = self._backing.import_file(
                    self._track_id, audio_path, lambda: self._stop,
                    match_length=False,
                )
            if self._stop or installed is None:
                return
            self.finished.emit(self._track_id, self._key, self._value)
        except PluginCancelled:
            return
        except NoAudioSource:
            self.failed.emit("No enabled plugin can fetch an instrumental from that")
        except Exception as exc:  # noqa: BLE001
            if self._stop:
                return
            logger.warning("Instrumental fetch failed for track %s: %s", self._track_id, exc)
            self.failed.emit(str(exc))
        finally:
            self.done.emit()


def _picture(
    registry: Any,
    cover_service: CoverService | None,
    entity: str,
    ids: Mapping[str, str],
    urls: Sequence[str],
    stop: Callable[[], bool],
) -> tuple[str, Optional[bytes]]:
    """``(cover hash, AVIF master)`` for an entity's picture. ``("", None)`` if none.

    The URLs a metadata answer carried first; then the artwork providers, in
    priority order. An empty hash means the picture could not be reached —
    never that there is none — which is why callers leave the stored picture
    alone rather than clearing it.
    """
    if cover_service is None:
        return "", None
    image_bytes = fetch_first(urls, should_stop=stop)
    if not image_bytes and not stop():
        with tempfile.TemporaryDirectory(dir=temp_root(), ignore_cleanup_errors=True) as tmp:
            image_bytes = first_artwork(
                registry, entity, ids, Path(tmp),
                lambda more: fetch_first(more, should_stop=stop),
                should_stop=stop,
            )
    if stop() or not image_bytes:
        return "", None
    return cover_service.ingest_source(image_bytes)


class RefreshWorker(CancellableWorker):
    """Re-read one track's, artist's or album's metadata, without any audio.

    The edit pages' "Refresh from …". A track whose id was wrong carries the
    wrong title, artist, album and cover with it, and correcting the id is the
    moment to offer all of them back; an artist or album gets its canonical name
    and its picture.

    It writes nothing to the library. Everything here is network and CPU — the
    metadata read, the picture fetch, and the AVIF encode that fills the cover
    cache — and the result is handed back over a signal for the UI thread to
    apply. That division is not stylistic: the whole application shares one
    SQLAlchemy ``Session``. ``CoverService`` is the exception it is safe to call
    from here, because ``CoverStore`` opens a connection per thread.

    ``fetched`` carries ``(info, cover_hash, master)``. For a track, *info* is
    the canonical ``track_info`` shape — title, artists, album, release date,
    and the lyrics the core's own lookup found. For an artist or album it is
    ``{"name"}``. When there is no reachable picture the last two are ``""``
    and ``None``, and the entity keeps the artwork it already had.
    """

    # `object` rather than `dict` for the metadata: a QVariantMap round-trip
    # rewrites the values on the way through, and the parsers downstream want
    # the ints and the Nones exactly as the provider gave them.
    fetched = Signal(object, str, object)  # info, cover_hash, master avif | None
    failed = Signal(str)                  # error message
    done = Signal()                       # always emitted last

    def __init__(
        self,
        entity: str,
        ids: Mapping[str, str],
        registry: Any,
        cover_service: CoverService | None = None,
    ) -> None:
        super().__init__()
        self._entity = entity
        self._ids = dict(ids)
        self._registry = registry
        self._cover_service = cover_service

    @Slot()
    def run(self) -> None:
        try:
            provider = self._registry.metadata(self._entity) if self._registry else None
            if provider is None:
                self.failed.emit("No enabled plugin can refresh this")
                return
            answer = provider.get(self._entity, self._ids, should_stop=self._stopped)
            if self._stop:
                return
            if not answer:
                self.failed.emit(f"{provider.name} returned nothing for that id")
                return

            if self._entity == external_ids.TRACK:
                info: Dict[str, Any] = {
                    "title": answer.get("title") or "",
                    "artists": answer.get("artist") or "",
                    "album": answer.get("album") or "",
                    "release_date": answer.get("release_date"),
                }
                # The core's own lyrics lookup, the one a fresh fetch and the
                # Now Playing button use too — so a refresh writes exactly the
                # lyrics a new fetch would have.
                if lrc := lookup_lyrics(info["title"], info["artists"]):
                    info["lyrics"] = lrc
            else:
                info = {"name": answer.get("name") or ""}
            if self._stop:
                return

            cover_hash, master = _picture(
                self._registry, self._cover_service, self._entity, self._ids,
                answer.get("artwork_urls") or [], self._stopped,
            )
            if self._stop:
                return
            self.fetched.emit(info, cover_hash, master)
        except PluginCancelled:
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("Refresh failed for %s %s: %s", self._entity, self._ids, exc)
            self.failed.emit(str(exc))
        finally:
            self.done.emit()


class IdentifyWorker(CancellableWorker):
    """Work out which catalogue entry an artist or album is, from its own tracks.

    The library already holds the answer. A track credited to this artist — or
    on this album — carries an external id, and the catalogue it came from can
    say who is credited on it and which release it is filed under. The identity
    provider does that asking; this worker hands it the tracks, takes the ids it
    answers with, and then reads the picture they point at.

    The match is the provider's to make, and it is held to the same rule the
    core holds itself to: the **whole** name, case-insensitively, and nothing
    looser. A loose match attaches a collaborator's identity to this artist, or
    a compilation's to a studio album, and nothing ever overwrites an id once
    set — so the mistake would be permanent.

    Pure network and CPU, like every worker here: it hands the ids, the
    canonical name and the picture back for the UI thread to write.
    """

    identified = Signal(object, str, str)   # ids, canonical_name, image_hash
    failed = Signal(str)
    done = Signal()

    def __init__(
        self,
        entity: str,
        name: str,
        sources: Sequence[Mapping[str, Any]],
        registry: Any,
        cover_service: CoverService | None = None,
    ) -> None:
        super().__init__()
        self._entity = entity
        self._name = name
        # Already read from the database, on the UI thread, before this started.
        self._sources = [dict(s) for s in sources or []]
        self._registry = registry
        self._cover_service = cover_service

    @Slot()
    def run(self) -> None:
        try:
            identity = self._registry.identity(self._entity) if self._registry else None
            if identity is None:
                self.failed.emit("No enabled plugin can identify this")
                return
            ids = identity.identify_entity(
                self._entity, self._name, self._sources, should_stop=self._stopped,
            )
            if self._stop:
                return
            # Already narrowed to the kinds the plugin declares, by the provider.
            ids = {k: v for k, v in (ids or {}).items() if v}
            if not ids:
                kind = "credit any of this artist's tracks to" if self._entity == external_ids.ARTIST \
                    else "file any of these tracks under"
                self.failed.emit(f"{identity.name} does not {kind} “{self._name}”")
                return

            name = ""
            urls: List[str] = []
            metadata = self._registry.metadata(self._entity)
            if metadata is not None:
                try:
                    answer = metadata.get(self._entity, ids, should_stop=self._stopped) or {}
                except PluginCancelled:
                    raise
                except Exception as exc:  # noqa: BLE001 — the id stands without a name
                    logger.debug("Metadata after identify failed: %s", exc)
                    answer = {}
                name = answer.get("name") or ""
                urls = list(answer.get("artwork_urls") or [])
            if self._stop:
                return
            image_hash, _master = _picture(
                self._registry, self._cover_service, self._entity, ids, urls, self._stopped,
            )
            if self._stop:
                return
            self.identified.emit(ids, name, image_hash)
        except PluginCancelled:
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("Identify failed for %s %r: %s", self._entity, self._name, exc)
            self.failed.emit(str(exc))
        finally:
            self.done.emit()


class AlbumTracksWorker(CancellableWorker):
    """Read a whole release's track list, by the album's external ids.

    Two callers with the same need and different uses for the answer: the
    import tab wants the rows, the album page wants the order they came back
    in. It emits core rows unchanged, which is what lets ``ImportViewModel``
    hand them straight to the merge its identify worker already feeds — one
    call for the whole record rather than one identify per track to re-derive
    what this call has already returned.

    ``counted`` is emitted first and separately, because the number of tracks on
    the record is the denominator the progress strip and the completion toast
    quote, and it is not knowable until the fetch lands.

    No consent gate: reading what a public release contains is not obtaining a
    recording.
    """

    counted = Signal(int)     # how many tracks the release has
    finished = Signal(list)   # core rows, in running order
    failed = Signal(str)
    done = Signal()

    def __init__(self, ids: Mapping[str, str], registry: Any) -> None:
        super().__init__()
        self._ids = dict(ids)
        self._registry = registry

    @Slot()
    def run(self) -> None:
        try:
            provider = self._registry.album_tracks() if self._registry else None
            if provider is None:
                self.failed.emit("No enabled plugin can list an album's tracks")
                return
            results = provider.album_tracks(self._ids, should_stop=self._stopped)
            # Nothing written, nothing kept between the halves — a cancel that
            # arrives any earlier has nothing to undo, and one that arrives
            # later has nothing left to stop.
            if self._stop:
                return
            if not results:
                self.failed.emit(f"{provider.name} returned no tracks for that album")
                return
            self.counted.emit(len(results))
            self.finished.emit(results)
        except PluginCancelled:
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("Album track list failed for %s: %s", self._ids, exc)
            self.failed.emit(str(exc))
        finally:
            self.done.emit()


class ImportSourceWorker(CancellableWorker):
    """Resolve what the user typed or picked for a plugin's import source.

    The same signals as :class:`AlbumTracksWorker`, because what comes back is
    the same thing — rows the import list can merge as they are — and the view
    model feeds both through one path.
    """

    counted = Signal(int)
    finished = Signal(list)
    failed = Signal(str)
    done = Signal()

    def __init__(self, provider: Any, source: Any, text: str) -> None:
        super().__init__()
        self._provider = provider
        self._source = source
        self._text = text

    @Slot()
    def run(self) -> None:
        try:
            rows = self._provider.resolve(self._source, self._text, should_stop=self._stopped)
            if self._stop:
                return
            if not rows:
                self.failed.emit(f"{self._provider.name} found nothing for that")
                return
            self.counted.emit(len(rows))
            self.finished.emit(rows)
        except PluginCancelled:
            return
        except Exception as exc:  # noqa: BLE001
            logger.warning("Import source %s failed: %s", self._source.id, exc)
            self.failed.emit(str(exc))
        finally:
            self.done.emit()
