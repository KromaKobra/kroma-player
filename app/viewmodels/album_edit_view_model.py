"""Album-editor ViewModel — the one album open in the editor, and its repairs.

Structurally :mod:`app.viewmodels.artist_edit_view_model`: staged fields that
are pushed rather than bound, an explicit save that refuses collisions without
losing what was typed, and a single worker slot for anything that reaches the
network.

Two things it has that the artist editor does not, both because an album is a
*set of tracks* where an artist is a name:

- **Unify.** Point some of the album's tracks at one cover. Slow, because it
  rewrites Opus files — see :mod:`app.albums` for why it must — so it is a
  worker with progress rather than a method call.
- **Split.** Move some tracks onto a new album with the same name. Note this is
  the opposite operation to ``ArtistService.split`` despite the shared word:
  that one merges rows by name, this one divides one row into two.

**One worker slot, not two.** A refresh from a source and the unify run both
decide what artwork this album has, and letting them overlap means the second writes
over what the first just settled. The artist editor keeps one thread for the
same reason; here the consequence would be worse, because one of the two also
rewrites files.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from PySide6.QtCore import Property, QObject, QThread, Signal, Slot

from app.albums.unify import UnifyError, build_plan
from app.artwork import LARGE_SIZE, THUMB_SIZE
from app.importer.import_workers import (
    AlbumTracksWorker,
    IdentifyWorker,
    RefreshWorker,
)
from app.albums.workers import AlbumUnifyWorker
from app.plugins import external_ids
from app.services.album_service import AlbumService
from app.utils.library_paths import track_path
from app.viewmodels.external_id_fields import IdStaging
from app.viewmodels.worker_threads import own_worker

logger = logging.getLogger(__name__)


class AlbumEditViewModel(QObject):
    """The album currently open in the editor, staged and saveable."""

    album_changed = Signal()
    dirty_changed = Signal()
    saving_changed = Signal()
    saved = Signal(int)
    error_occurred = Signal(str)
    # The worker slot started or stopped — a refresh, an identify, a reorder or
    # a unify run.
    fetching_changed = Signal()
    overwrite_offered = Signal(str)
    refreshed = Signal(int)
    ids_changed = Signal()
    split_completed = Signal(int)
    unify_progress = Signal(int, int)
    unify_completed = Signal(int)
    # matched count, whether anything actually moved, the titles of the tracks
    # the release did not name — which are now at the end.
    reorder_completed = Signal(int, bool, "QVariantList")

    _FIELDS = ("name",)

    def __init__(
        self,
        album_service: AlbumService,
        registry=None,
        cover_service=None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._service = album_service
        # The providers: who can identify, refresh, list or reorder this album.
        # ``None`` — no plugins — and the page keeps everything that is local.
        self._registry = registry
        self._cover_service = cover_service
        self._ids = IdStaging(external_ids.ALBUM, registry)
        if registry is not None and hasattr(registry, "changed"):
            registry.changed.connect(self._on_providers_changed)

        self._album_id: int = 0
        # `Any` rather than `str` because `is_complete` is staged here too, and a
        # tick box is the one field on this page that is not text. Everything
        # that reads these compares them as a whole or by a named key, so the
        # mixed value type costs nothing.
        self._baseline: Dict[str, Any] = {}
        self._staged: Dict[str, Any] = {}
        self._details: Dict[str, Any] = {}
        self._saving = False

        self._worker_thread: Optional[QThread] = None
        self._worker: Optional[QObject] = None
        self._unify_worker: Optional[AlbumUnifyWorker] = None
        self._unify_done = 0
        self._unify_total = 0
        self._pending_unify: Dict[str, Any] = {}

    # ==================================================================
    # Loading
    # ==================================================================

    @Slot(int)
    def load(self, album_id: int) -> None:
        details = self._service.details(int(album_id))
        if details is None:
            self.clear()
            return
        self._album_id = int(album_id)
        self._details = details
        self._baseline = {
            "name": details.get("name", ""),
            "is_complete": bool(details.get("is_complete", False)),
        }
        self._staged = dict(self._baseline)
        self._ids.load(details.get("external_ids") or {})
        self.album_changed.emit()
        self.dirty_changed.emit()
        self.ids_changed.emit()

    @Slot()
    def reload(self) -> None:
        if self._album_id > 0:
            self.load(self._album_id)

    @Slot()
    def revert(self) -> None:
        if self._staged == self._baseline and not self._ids.dirty:
            return
        self._staged = dict(self._baseline)
        self._ids.revert()
        self.album_changed.emit()
        self.dirty_changed.emit()
        self.ids_changed.emit()

    @Slot()
    def clear(self) -> None:
        self._album_id = 0
        self._baseline = {}
        self._staged = {}
        self._ids.clear()
        self._details = {}
        self.album_changed.emit()
        self.dirty_changed.emit()
        self.ids_changed.emit()

    # ==================================================================
    # Saving
    # ==================================================================

    @Slot()
    def save(self) -> None:
        """Write the staged changes, refusing anything that would collide.

        Values missing from the staging mean "unchanged", never "empty" or
        "false". The completeness tick and the name are sent only when they
        moved, and an id only when the user changed one they could see — so a
        rename saved with the album's plugin off cannot clear its id, and one
        saved before the page had finished loading cannot un-tick the record.
        """
        if not self.canSave:
            return

        name = self._staged.get("name", "").strip()
        ids, error = self._ids.changes()
        if error:
            self.error_occurred.emit(error)
            return

        complete = self._staged.get("is_complete")
        changed_complete = (
            "is_complete" in self._staged
            and "is_complete" in self._baseline
            and bool(complete) != bool(self._baseline["is_complete"])
        )

        self._saving = True
        self.saving_changed.emit()
        try:
            result = self._service.update(
                self._album_id,
                name=name if name != self._baseline.get("name") else None,
                ids=ids or None,
                is_complete=bool(complete) if changed_complete else None,
            )
        finally:
            self._saving = False
            self.saving_changed.emit()

        if result.error:
            # Nothing was written, and the staged edits survive so the user can
            # fix the one field that was wrong and save again.
            self.error_occurred.emit(result.error)
            return

        album_id = self._album_id
        self.load(album_id)
        self.saved.emit(album_id)

        offered = next((v for v in ids.values() if v), "")
        if offered and self.canRefresh:
            self.overwrite_offered.emit(offered)

    # ==================================================================
    # Identity
    # ==================================================================

    def _claim_worker(self, what: str) -> bool:
        if self._worker_thread is not None:
            self.error_occurred.emit(f"Already working on this album — {what} must wait")
            return False
        return True

    def _start(self, worker: QObject, connect) -> None:
        thread = QThread(self)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        connect(worker)
        worker.failed.connect(self._on_worker_failed)
        own_worker(thread, worker, self._on_worker_stopped)
        self._worker_thread = thread
        self._worker = worker
        thread.start()
        self.fetching_changed.emit()

    @Property(bool, notify=ids_changed)
    def canRefresh(self) -> bool:  # noqa: N802
        """Whether a metadata provider for albums is on and this one carries an id."""
        return (
            self._album_id > 0
            and self._registry is not None
            and self._registry.metadata(external_ids.ALBUM) is not None
            and bool(self._ids.baseline_ids())
        )

    @Property(bool, notify=ids_changed)
    def canIdentify(self) -> bool:  # noqa: N802
        """Whether an identity provider for albums is on and this one is unidentified."""
        return (
            self._album_id > 0
            and self._registry is not None
            and self._registry.identity(external_ids.ALBUM) is not None
            and not self._ids.baseline_ids()
        )

    @Property(bool, notify=ids_changed)
    def canFindTracks(self) -> bool:  # noqa: N802
        """Whether "Find tracks" can list this album's release.

        An identified album and a plugin that can list a release's tracks.
        Shown only then: with either missing there is no release to read, and a
        button that can only ever say so is not one worth drawing.
        """
        return (
            self._album_id > 0
            and self._registry is not None
            and self._registry.album_tracks() is not None
            and bool(self._ids.baseline_ids())
        )

    @Property(bool, notify=ids_changed)
    def canReorder(self) -> bool:  # noqa: N802
        """Whether "Reorder from …" has a release listing to take an order from."""
        return self.canFindTracks

    @Property(bool, notify=ids_changed)
    def isIdentified(self) -> bool:  # noqa: N802
        """Whether the album holds a release id at all, whatever is enabled.

        The rule the cover follows — an identified album keeps its release's
        sleeve — reads the row, not the plugins, so this does too. With the
        plugin off it can be true while ``identifiedLabel`` is empty.
        """
        return external_ids.is_identified(
            external_ids.ALBUM, self._ids.baseline_ids(declared_only=False),
        )

    @Property(str, notify=ids_changed)
    def identifiedLabel(self) -> str:  # noqa: N802
        """Where this album is identified, as its plugin names it — or ``""``.

        Read from the saved ids an enabled plugin declares, so an album
        whose plugin is off reads as unidentified without anything having
        been cleared.
        """
        return self._ids.identified().get("label", "")

    @Property(str, notify=ids_changed)
    def identifiedIcon(self) -> str:  # noqa: N802
        """That plugin's picture for the kind, as an ``image://plugin`` URL, or ``""``."""
        return self._ids.identified().get("iconUrl", "")

    @Property(str, notify=ids_changed)
    def refreshLabel(self) -> str:  # noqa: N802
        """What to call the source a refresh reads from, as its plugin names it."""
        saved = self._ids.baseline_ids()
        kinds = self._ids.declarations()
        kind = next((k for k in kinds if k.key in saved), kinds[0] if kinds else None)
        return (kind.source_label or kind.label) if kind is not None else ""

    @Slot()
    def refreshFromSource(self) -> None:  # noqa: N802
        """Replace the name and cover with what the stored id's catalogue says."""
        if self._album_id <= 0 or not self._claim_worker("a refresh"):
            return
        ids = self._ids.baseline_ids()
        if not ids:
            self.error_occurred.emit("This album has no id to refresh from")
            return
        if not self.canRefresh:
            self.error_occurred.emit("No enabled plugin can refresh this album")
            return
        self._start(
            RefreshWorker(external_ids.ALBUM, ids, self._registry, self._cover_service),
            lambda worker: worker.fetched.connect(self._on_refresh_fetched),
        )

    def _on_refresh_fetched(self, info, cover_hash: str, _master) -> None:
        if self._album_id <= 0:
            return
        name = str((info or {}).get("name") or "")
        if not name and not cover_hash:
            self.error_occurred.emit("The source had nothing to refresh this album with")
            return
        # Only what was actually supplied. An empty hash means the cover could
        # not be reached, never that the album has none, so the stored artwork
        # stays rather than being cleared.
        result = self._service.update(
            self._album_id,
            name=name or None,
            cover_hash=cover_hash or None,
        )
        if result.error:
            self.error_occurred.emit(result.error)
            return
        album_id = self._album_id
        self.load(album_id)
        self.refreshed.emit(album_id)

    @Slot()
    def identifyFromTracks(self) -> None:  # noqa: N802
        """Work out this album's release from one of its tracks.

        Writes the id as soon as it has it, like the artist editor does, and for
        the same reason: an id arrived at this way is not a guess for the user to
        confirm. The **name is deliberately left alone** — the match was made by
        name, so the only thing the catalogue's spelling adds is different
        casing.
        """
        if self._album_id <= 0 or not self._claim_worker("identifying"):
            return
        if self._ids.baseline_ids():
            self.error_occurred.emit("This album is already identified")
            return
        provider = self._registry.identity(external_ids.ALBUM) if self._registry else None
        if provider is None:
            self.error_occurred.emit("No enabled plugin can identify albums")
            return

        sources = self._service.identify_sources(
            self._album_id, keys=provider.manifest.recording_keys(),
        )
        if not sources:
            self.error_occurred.emit(
                "None of this album's tracks carries an id, so there is "
                "nothing to identify it from"
            )
            return
        self._start(
            IdentifyWorker(
                external_ids.ALBUM, self._baseline.get("name", ""), sources,
                self._registry, self._cover_service,
            ),
            lambda worker: worker.identified.connect(self._on_identified),
        )

    def _on_identified(self, ids, _name: str, cover_hash: str) -> None:
        if self._album_id <= 0 or not ids:
            return
        result = self._service.update(
            self._album_id,
            ids=dict(ids),
            cover_hash=cover_hash or None,
        )
        if result.error:
            self.error_occurred.emit(result.error)
            return
        album_id = self._album_id
        self.load(album_id)
        self.refreshed.emit(album_id)

    @Slot()
    def reorderFromSource(self) -> None:  # noqa: N802
        """Arrange this album to read in its release's order.

        The other half of what an album's id is worth. A refresh takes the
        release's name and sleeve; this takes its running order, which is the
        one fact about an album a library assembled one track at a time cannot
        have got right by itself.

        Applied as soon as it arrives rather than offered for confirmation. A
        running order is not a destructive edit — it is one ``set_order`` write
        that a drag can undo — and nothing about a preview would tell the user
        anything the reordered list does not tell them better.
        """
        if self._album_id <= 0 or not self._claim_worker("reordering"):
            return
        ids = self._ids.baseline_ids()
        if not ids:
            self.error_occurred.emit("This album has no id to take an order from")
            return
        if not self.canReorder:
            self.error_occurred.emit("No enabled plugin can list this album's release")
            return
        self._start(
            AlbumTracksWorker(ids, self._registry),
            lambda worker: worker.finished.connect(self._on_tracklist_fetched),
        )

    def _on_tracklist_fetched(self, results: list) -> None:
        """Apply the fetched running order. Main thread — this writes."""
        if self._album_id <= 0:
            return
        # The rows straight through, not the ids off them. A position carries
        # several candidate ids and a title, and the service needs all of it:
        # an album of local rips has no external id to be matched by at all.
        outcome = self._service.reorder_from_release(
            self._album_id, list(results or [])
        )
        if outcome.error:
            self.error_occurred.emit(outcome.error)
            return

        # Named, not just counted. "Two tracks moved to the end" leaves the user
        # hunting for which two; the titles are already one query away and this
        # is the only moment anything knows they were the odd ones out.
        titles: list = []
        track_service = getattr(self._service, "track_service", None)
        if outcome.unmatched and track_service is not None:
            by_id = {
                int(t.id): (t.title or "")
                for t in track_service.get_tracks_by_ids(outcome.unmatched)
            }
            titles = [by_id.get(t, "") for t in outcome.unmatched if by_id.get(t)]

        self.reorder_completed.emit(len(outcome.matched), outcome.moved, titles)

    def _on_worker_failed(self, message: str) -> None:
        self.error_occurred.emit(message or "Could not reach the source")

    def _on_worker_stopped(self) -> None:
        self._worker_thread = None
        self._worker = None
        self._unify_worker = None
        self.fetching_changed.emit()

    # ==================================================================
    # External ids
    # ==================================================================

    @Property("QVariantList", notify=ids_changed)
    def externalIdFields(self) -> list:  # noqa: N802
        """One entry per album id kind an enabled plugin declares. See ``editVM``."""
        return self._ids.fields()

    @Slot(str, str)
    def setExternalId(self, key: str, text: str) -> None:  # noqa: N802
        if self._album_id <= 0:
            return
        if self._ids.set(str(key), text):
            self.dirty_changed.emit()

    @Slot()
    def _on_providers_changed(self) -> None:
        self.ids_changed.emit()
        self.dirty_changed.emit()

    # ==================================================================
    # Who is on it
    # ==================================================================

    @Slot(result="QVariantList")
    def contributingArtists(self) -> list:  # noqa: N802
        """Everyone credited on this album, most tracks first.

        ``[{artistId, name, imageHash, trackCount, isMain}]``. Ties are broken
        by whoever the record introduces first, and ``isMain`` marks an artist
        on strictly more than half the record — see
        ``AlbumService.contributing_artists``, which owns both rules.

        A slot rather than a property, the way ``coverGroups`` is: it is a join
        over every track on the record, so the page asks for it when it opens
        and when the membership moves rather than on every repaint.

        ``imageHash`` and not ``coverHash``, matching the object ``artistsVM``
        hands out. Both keys point into the same content-addressed store, so
        one ``CoverArt`` reads either — but a GUI that already knows what an
        artist looks like should not have to learn a second name for it here.
        """
        if self._album_id <= 0:
            return []
        return [
            {
                "artistId": row["artist_id"],
                "name": row["name"],
                "imageHash": row["image_hash"],
                "trackCount": row["track_count"],
                "isMain": row["is_main"],
            }
            for row in self._service.contributing_artists(self._album_id)
        ]

    # ==================================================================
    # Artwork repair
    # ==================================================================

    @Slot(result="QVariantList")
    def coverGroups(self) -> list:  # noqa: N802
        """The distinct covers among this album's own tracks, biggest group first.

        What both repair dialogs are built from. Only the tracks this album is
        primary for appear — a track visiting from another record is entitled to
        different artwork and must never be offered up for rewriting.

        ``isAlbumCover`` marks the album's own sleeve. Its ``trackIds`` is empty
        when nothing carries it yet, which is not an empty group to hide: it is
        the repair for the disagreement between an identified album and the files
        that were never tagged with its artwork.
        """
        return [
            {
                "coverHash": group["cover_hash"],
                "trackIds": group["track_ids"],
                "titles": group["titles"],
                "isAlbumCover": bool(group.get("is_album_cover", False)),
            }
            for group in self._service.cover_groups(self._album_id)
        ]

    @Slot()
    def useFirstTrackArtwork(self) -> None:  # noqa: N802
        """Take the album's cover from the first of its tracks that has one."""
        if self._album_id <= 0:
            return
        if self._service.seed_cover_from_tracks(self._album_id):
            self.reload()

    @Slot("QVariantList")
    def splitOut(self, track_ids: list) -> None:  # noqa: N802
        """Move the named tracks onto a new album with the same name."""
        if self._album_id <= 0:
            return
        result = self._service.split(self._album_id, [int(t) for t in track_ids or []])
        if result.error:
            self.error_occurred.emit(result.error)
            return
        self.reload()
        self.split_completed.emit(int(result.album.id) if result.album else 0)

    @Slot(str, "QVariantList")
    def unify(self, cover_hash: str, track_ids: list) -> None:  # noqa: N802
        """Rewrite the named tracks to carry *cover_hash*, files included.

        ``track_ids`` is exactly what the dialog ticked and is never widened
        here. That is the whole reason the dialog exists: a track this album is
        not primary for legitimately carries different artwork, so which tracks
        change is a judgement only the user can make.

        An empty list is the *other* repair: every track already agrees and it is
        the album's own sleeve that is out of step. Nothing is embedded, so it
        does not go near the worker — and, more to the point, does not go near
        ``master_bytes`` either. There is no picture to read back when no file is
        being written, and demanding one would refuse the repair over artwork
        that was never needed.
        """
        if self._album_id <= 0 or not self._claim_worker("unifying"):
            return

        wanted = [int(t) for t in track_ids or []]
        if not cover_hash:
            self.error_occurred.emit("Choose an artwork to keep")
            return

        if not wanted:
            changed = self._service.apply_unified_cover(self._album_id, cover_hash, [])
            self.reload()
            self.unify_completed.emit(changed)
            return

        # A track that already carries the keeper is the natural source for the
        # original bytes — the file's embedded picture beats the cache, which is
        # only ever a re-encode of it.
        keeper_path = None
        for group in self._service.cover_groups(self._album_id):
            if group["cover_hash"] == cover_hash and group["track_ids"]:
                keeper_path = track_path(group["track_ids"][0])
                break

        store = getattr(self._cover_service, "store", None)

        def read_cached(digest: str):
            if store is None:
                return None
            return store.get(digest, LARGE_SIZE) or store.get(digest, THUMB_SIZE)

        try:
            plan = build_plan(
                album_id=self._album_id,
                album_name=self._baseline.get("name", ""),
                cover_hash=cover_hash,
                track_paths=[(t, track_path(t)) for t in wanted],
                keeper_path=keeper_path,
                read_cached=read_cached,
            )
        except UnifyError as exc:
            self.error_occurred.emit(str(exc))
            return

        self._pending_unify = {"cover_hash": cover_hash}
        self._unify_done = 0
        self._unify_total = plan.total

        thread = QThread(self)
        worker = AlbumUnifyWorker(plan)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_unify_progress)
        worker.finished.connect(self._on_unify_finished)
        worker.cancelled.connect(self._on_unify_finished)
        worker.failed.connect(self._on_worker_failed)
        own_worker(thread, worker, self._on_worker_stopped)
        self._worker_thread = thread
        self._worker = worker
        self._unify_worker = worker
        thread.start()
        self.fetching_changed.emit()

    def _on_unify_progress(self, done: int, total: int) -> None:
        self._unify_done = int(done)
        self._unify_total = int(total)
        self.unify_progress.emit(self._unify_done, self._unify_total)

    def _on_unify_finished(self, written: list) -> None:
        """Write the library half, for the files that were actually rewritten.

        ``written`` and not the plan's full list: a file the worker could not
        open — the playing track on Windows, most likely — still carries its old
        picture, and claiming otherwise in the database would make the two
        disagree in exactly the way unify exists to stop.
        """
        cover_hash = self._pending_unify.get("cover_hash", "")
        self._pending_unify = {}
        if not cover_hash or not written:
            return
        changed = self._service.apply_unified_cover(
            self._album_id, cover_hash, [int(t) for t in written]
        )
        self.reload()
        self.unify_completed.emit(changed)

    @Slot()
    def cancelUnify(self) -> None:  # noqa: N802
        if self._unify_worker is not None:
            self._unify_worker.cancel()

    def shutdown(self) -> None:
        """Stop any run and wait for it, before the window goes.

        The unify worker writes Opus files inside the vault, so it must not
        outlive the window that started it — a half-written tag is not something
        to leave behind at exit.
        """
        if self._unify_worker is not None:
            self._unify_worker.cancel()
        thread = self._worker_thread
        if thread is not None:
            thread.quit()
            thread.wait(5000)

    # ==================================================================
    # Properties
    # ==================================================================

    def _get(self, field: str) -> str:
        return self._staged.get(field, "")

    def _set(self, field: str, value: str) -> None:
        value = str(value or "")
        if self._staged.get(field, "") == value:
            return
        self._staged[field] = value
        self.dirty_changed.emit()

    def _get_name(self) -> str:
        return self._get("name")

    def _set_name(self, value: str) -> None:
        self._set("name", value)

    def _get_complete(self) -> bool:
        return bool(self._staged.get("is_complete", False))

    def _set_complete(self, value: bool) -> None:
        value = bool(value)
        if bool(self._staged.get("is_complete", False)) == value:
            return
        self._staged["is_complete"] = value
        # Both signals, unlike the text fields. `dirty_changed` arms Save;
        # `album_changed` is what a tick box binding to this reads, and without
        # it the box would not draw the state it was just clicked into.
        self.dirty_changed.emit()
        self.album_changed.emit()

    name = Property(str, _get_name, _set_name, notify=album_changed)
    # Staged like the name above it, so a mis-click is undone by Revert rather
    # than by clicking again — and so the page has one Save and not two ways to
    # write. What it asserts, and when it un-asserts itself, is
    # `Album.is_complete`'s business.
    isComplete = Property(bool, _get_complete, _set_complete, notify=album_changed)

    @Property(int, notify=album_changed)
    def albumId(self) -> int:  # noqa: N802
        return self._album_id

    @Property(bool, notify=album_changed)
    def hasAlbum(self) -> bool:  # noqa: N802
        return self._album_id > 0

    @Property(str, notify=album_changed)
    def coverHash(self) -> str:  # noqa: N802
        return self._details.get("cover_hash", "")

    @Property(int, notify=album_changed)
    def trackCount(self) -> int:  # noqa: N802
        return int(self._details.get("track_count", 0))

    @Property(bool, notify=album_changed)
    def needsAttention(self) -> bool:  # noqa: N802
        """Whether this album's own tracks disagree about their artwork."""
        return bool(self._details.get("needs_attention", False))

    @Property(bool, notify=dirty_changed)
    def isDirty(self) -> bool:  # noqa: N802
        return bool(self._staged) and (self._staged != self._baseline or self._ids.dirty)

    @Property(bool, notify=dirty_changed)
    def canSave(self) -> bool:  # noqa: N802
        return (
            self._album_id > 0
            and not self._saving
            and self.isDirty
            and bool(self._staged.get("name", "").strip())
        )

    @Property(bool, notify=saving_changed)
    def isSaving(self) -> bool:  # noqa: N802
        return self._saving

    @Property(bool, notify=fetching_changed)
    def isBusy(self) -> bool:  # noqa: N802
        return self._worker_thread is not None

    @Property(bool, notify=fetching_changed)
    def isFetching(self) -> bool:  # noqa: N802
        """The same slot as ``isBusy``, under the name every editor shares."""
        return self._worker_thread is not None

    @Property(int, notify=unify_progress)
    def unifyDone(self) -> int:  # noqa: N802
        return self._unify_done

    @Property(int, notify=unify_progress)
    def unifyTotal(self) -> int:  # noqa: N802
        return self._unify_total

    @Slot(result="QVariantList")
    def trackIds(self) -> list:  # noqa: N802
        """This album's track ids, in running order."""
        if self._album_id <= 0:
            return []
        return self._service.album_repo.track_ids(self._album_id)

    @Slot(result="QVariantList")
    def visitingTrackIds(self) -> list:  # noqa: N802
        """Tracks on this album that belong to a different one."""
        if self._album_id <= 0:
            return []
        return self._service.visiting_track_ids(self._album_id)
