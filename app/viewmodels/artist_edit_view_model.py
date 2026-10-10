"""Artist ViewModel — one artist, open.

The artist counterpart of ``editVM``, and it holds the same position: one entity,
not a selection. It backs both the artist page and the artist editor, because
there is only ever one artist open and splitting that across two objects would
mean keeping them agreed.

Edits are staged exactly as a track's are. ``load()`` snapshots the artist, the
writable properties change only the working copy, and ``save()`` sends the
difference — so ``isDirty`` is exact, ``revert()`` costs nothing, and a save that
is refused leaves the staged text intact for the user to correct.

Three things an artist can do that a track cannot:

- **Be identified.** An external id is what makes an artist the same artist as
  the one in an online catalogue, and it is what a portrait and a canonical name
  can always be re-fetched from. Which catalogues exist is the enabled plugins'
  business; the editor shows one field per kind they declare.
- **Be recognised.** Nobody knows their favourite artist's catalogue id, so the
  editor asks an identity provider to work it out from a track the artist is
  on — see :meth:`ArtistEditViewModel.identifyFromTracks`.
- **Be taken apart.** ``person1/person2`` is one row that should be two, and
  :meth:`splitInto` is how it becomes two without editing every track by hand.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Property, QObject, QThread, Signal, Slot

from app.importer.import_workers import IdentifyWorker, RefreshWorker
from app.plugins import external_ids
from app.services.artist_service import ArtistService, looks_malformed, propose_split
from app.viewmodels.external_id_fields import IdStaging
from app.viewmodels.worker_threads import own_worker

logger = logging.getLogger(__name__)


class ArtistEditViewModel(QObject):
    """Holds a single artist while it is being looked at or changed."""

    artist_changed = Signal()
    dirty_changed = Signal()
    saving_changed = Signal()
    saved = Signal(int)                  # artist_id
    error_occurred = Signal(str)

    # Identity
    fetching_changed = Signal()
    overwrite_offered = Signal(str)      # the newly saved id
    refreshed = Signal(int)              # artist_id
    ids_changed = Signal()

    # Repair
    split_completed = Signal(int)        # how many artists it became

    _FIELDS = ("name",)

    def __init__(
        self,
        artist_service: ArtistService,
        registry=None,
        cover_service=None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._service = artist_service
        # The providers: who can identify this artist and who can refresh them.
        # ``None`` — no plugins — and the page is a name and a portrait.
        self._registry = registry
        self._cover_service = cover_service
        self._ids = IdStaging(external_ids.ARTIST, registry)
        if registry is not None and hasattr(registry, "changed"):
            registry.changed.connect(self._on_providers_changed)

        self._artist_id: int = -1
        self._baseline: Dict[str, str] = {}
        self._staged: Dict[str, str] = {}
        self._saving = False

        # Read-only context shown beside the fields.
        self._image_hash = ""
        self._track_count = 0

        # One slot for both jobs, not two. Identifying an artist and refreshing
        # one from its stored id are the same job reached from different
        # starting points — both end in "identity and portrait written from the
        # catalogue" — and they must never run at once, because the second
        # would write over what the first just decided.
        self._fetch_thread: Optional[QThread] = None
        self._fetch_worker: Optional[QObject] = None

        artist_service.event_bus.subscribe("artist.deleted", self._on_artist_deleted)

    # ==================================================================
    # Loading
    # ==================================================================

    @Slot(int)
    def load(self, artist_id: int) -> None:
        """Open an artist, discarding whatever was staged before it."""
        details = self._service.details(int(artist_id))
        if details is None:
            self.error_occurred.emit("That artist no longer exists")
            self.clear()
            return

        self._artist_id = int(details["id"])
        self._baseline = {"name": details["name"]}
        self._staged = dict(self._baseline)
        self._ids.load(details.get("external_ids") or {})
        self._image_hash = details["image_hash"]
        self._track_count = int(details["track_count"])

        self.artist_changed.emit()
        self.dirty_changed.emit()
        self.fetching_changed.emit()
        self.ids_changed.emit()

    @Slot()
    def reload(self) -> None:
        """Re-read the open artist from the database, dropping staged edits.

        What a page calls after something else changed the artist — a track edit
        that moved its last song away, say, changing the count under it.
        """
        if self._artist_id > 0:
            self.load(self._artist_id)

    @Slot()
    def revert(self) -> None:
        """Throw away every staged change, keeping the artist open."""
        if self._staged == self._baseline and not self._ids.dirty:
            return
        self._staged = dict(self._baseline)
        self._ids.revert()
        self.artist_changed.emit()
        self.dirty_changed.emit()
        self.ids_changed.emit()

    @Slot()
    def clear(self) -> None:
        """Close the editor. What makes the tab's rail slot go away."""
        self._artist_id = -1
        self._baseline = {}
        self._staged = {}
        self._ids.clear()
        self._image_hash = ""
        self._track_count = 0
        self.artist_changed.emit()
        self.dirty_changed.emit()
        self.ids_changed.emit()

    def _on_artist_deleted(self, artist_id: int) -> None:
        if self._artist_id > 0 and int(artist_id) == self._artist_id:
            self.clear()

    # ==================================================================
    # Saving
    # ==================================================================

    @Slot()
    def save(self) -> None:
        """Write the staged changes, refusing anything that would collide.

        Values missing from the staging mean "unchanged" — never "empty". The
        name is sent only when it moved, and an id only when the user changed
        one they could see; a kind whose plugin is off is not in the diff at
        all, so saving a rename with that plugin off cannot clear its id.
        """
        if not self.canSave:
            return

        name = self._staged.get("name", "").strip()
        ids, error = self._ids.changes()
        if error:
            # Unparseable text is refused, not discarded. Treating an id we
            # cannot read as "clear the field" would lose the artist's identity
            # because of a typo.
            self.error_occurred.emit(error)
            return

        self._saving = True
        self.saving_changed.emit()
        try:
            result = self._service.update(
                self._artist_id,
                name=name if name != self._baseline.get("name") else None,
                ids=ids or None,
            )
        finally:
            self._saving = False
            self.saving_changed.emit()

        if result.error:
            # Nothing was written, and the staged edits survive so the user can
            # fix the one field that was wrong and save again.
            self.error_occurred.emit(result.error)
            return

        artist_id = self._artist_id
        self.load(artist_id)
        self.saved.emit(artist_id)

        # A newly set id is the one field that, once right, can put the name
        # and the portrait right too — so it is the one field that offers.
        offered = next((v for v in ids.values() if v), "")
        if offered and self.canRefresh:
            self.overwrite_offered.emit(offered)

    # ==================================================================
    # Identity
    # ==================================================================

    def _claim(self) -> bool:
        if self._fetch_thread is not None:
            self.error_occurred.emit("Already fetching for this artist")
            return False
        return True

    @Property(bool, notify=ids_changed)
    def canRefresh(self) -> bool:  # noqa: N802
        """Whether a metadata provider is on and this artist has an id it declares."""
        return (
            self._artist_id > 0
            and self._registry is not None
            and self._registry.metadata(external_ids.ARTIST) is not None
            and bool(self._ids.baseline_ids())
        )

    @Property(bool, notify=ids_changed)
    def canIdentify(self) -> bool:  # noqa: N802
        """Whether an identity provider for artists is on and this one is unidentified."""
        return (
            self._artist_id > 0
            and self._registry is not None
            and self._registry.identity(external_ids.ARTIST) is not None
            and not self._ids.baseline_ids()
        )

    @Property(str, notify=ids_changed)
    def identifiedLabel(self) -> str:  # noqa: N802
        """Where this artist is identified, as its plugin names it — or ``""``.

        Read from the saved ids an enabled plugin declares, so an artist
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
        """Replace the name and portrait with what the stored id's catalogue says.

        Runs off the UI thread — it reaches a plugin and the network and
        encodes an image — and writes nothing from there: the worker hands the
        result back and this object, on the UI thread, is what touches the
        database.
        """
        if self._artist_id <= 0 or not self._claim():
            return
        ids = self._ids.baseline_ids()
        if not ids:
            self.error_occurred.emit("This artist has no id to refresh from")
            return
        if not self.canRefresh:
            self.error_occurred.emit("No enabled plugin can refresh this artist")
            return

        thread = QThread(self)
        worker = RefreshWorker(external_ids.ARTIST, ids, self._registry, self._cover_service)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.fetched.connect(self._on_refresh_fetched)
        worker.failed.connect(self._on_fetch_failed)
        own_worker(thread, worker, self._on_fetch_stopped)
        self._fetch_thread = thread
        self._fetch_worker = worker
        thread.start()
        self.fetching_changed.emit()

    def _on_refresh_fetched(self, info, image_hash: str, _master) -> None:
        if self._artist_id <= 0:
            return
        name = str((info or {}).get("name") or "")
        if not name and not image_hash:
            self.error_occurred.emit("The source had nothing to refresh this artist with")
            return

        # Only what was actually supplied. A fetch that could not reach a portrait
        # is not a fetch saying the artist has none, so an empty hash leaves the
        # picture alone rather than clearing it.
        result = self._service.update(
            self._artist_id,
            name=name or None,
            image_hash=image_hash or None,
        )
        if result.error:
            self.error_occurred.emit(result.error)
            return

        artist_id = self._artist_id
        self.load(artist_id)
        self.refreshed.emit(artist_id)

    def _on_fetch_failed(self, message: str) -> None:
        self.error_occurred.emit(message or "Could not reach the source")

    def _on_fetch_stopped(self) -> None:
        self._fetch_thread = None
        self._fetch_worker = None
        self.fetching_changed.emit()

    @Slot()
    def identifyFromTracks(self) -> None:  # noqa: N802
        """Work out this artist's catalogue id from one of their tracks.

        Searching by name asks the user to recognise an id, which nobody can do
        — and offers a list of strangers when the whole difficulty is telling
        strangers apart. This asks the identity provider instead, handing it
        the tracks credited to this artist that carry an id of their own; the
        provider knows who is credited on them, and the one whose name matches
        is this artist.

        Writes as soon as it has the answer rather than staging it. An id
        arrived at this way is not a guess for the user to confirm, and a
        portrait cannot be staged in any case — ``imageHash`` is read-only and
        only :meth:`ArtistService.update` writes one.

        The **name is deliberately left alone**. The match was made *by* name, so
        the only thing the catalogue's spelling can add is different casing or
        punctuation — and ``update`` refuses a case-insensitive collision, which
        would throw away the id along with the name. "Refresh" is the button for
        anyone who wants the canonical spelling.
        """
        if self._artist_id <= 0 or not self._claim():
            return
        if self._ids.baseline_ids():
            self.error_occurred.emit("This artist is already identified — use Refresh")
            return
        provider = self._registry.identity(external_ids.ARTIST) if self._registry else None
        if provider is None:
            self.error_occurred.emit("No enabled plugin can identify artists")
            return

        # Read here, on the UI thread: the worker gets no session.
        sources = self._service.identify_sources(
            self._artist_id, keys=provider.manifest.recording_keys(),
        )
        if not sources:
            # Said rather than done silently. "Nothing happened" is how a broken
            # button looks, and the reason is something the user can act on.
            self.error_occurred.emit(
                "None of this artist's tracks carries an id, so there is nothing "
                "to identify them from"
            )
            return

        thread = QThread(self)
        worker = IdentifyWorker(
            external_ids.ARTIST, self._baseline.get("name", ""), sources,
            self._registry, self._cover_service,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.identified.connect(self._on_identified)
        worker.failed.connect(self._on_fetch_failed)
        own_worker(thread, worker, self._on_fetch_stopped)
        self._fetch_thread = thread
        self._fetch_worker = worker
        thread.start()
        self.fetching_changed.emit()

    def _on_identified(self, ids, _name: str, image_hash: str) -> None:
        if self._artist_id <= 0 or not ids:
            return

        # `update` already refuses an id another artist holds, and refuses it
        # before writing anything — so this surfaces that answer rather than
        # re-implementing the check and getting the two rules out of step.
        result = self._service.update(
            self._artist_id,
            ids=dict(ids),
            # "" means the portrait could not be reached, not that there is
            # none, so it leaves whatever picture the artist already had.
            image_hash=image_hash or None,
        )
        if result.error:
            self.error_occurred.emit(result.error)
            return

        artist_id = self._artist_id
        self.load(artist_id)
        self.refreshed.emit(artist_id)

    # ==================================================================
    # External ids
    # ==================================================================

    @Property("QVariantList", notify=ids_changed)
    def externalIdFields(self) -> list:  # noqa: N802
        """One entry per artist id kind an enabled plugin declares. See ``editVM``."""
        return self._ids.fields()

    @Slot(str, str)
    def setExternalId(self, key: str, text: str) -> None:  # noqa: N802
        if self._artist_id <= 0:
            return
        if self._ids.set(str(key), text):
            self.dirty_changed.emit()

    @Slot()
    def _on_providers_changed(self) -> None:
        self.ids_changed.emit()
        self.dirty_changed.emit()

    # ==================================================================
    # Repair
    # ==================================================================

    @Slot(result="QVariantList")
    def proposedSplit(self) -> list:  # noqa: N802
        """The artists this name is probably hiding, or an empty list.

        A suggestion for the user to confirm or edit — never applied on its own.
        "AC/DC" trips the same separator that "person1/person2" does, and only a
        person can tell those apart.
        """
        return propose_split(self._staged.get("name", "") or self._baseline.get("name", ""))

    @Slot("QVariantList")
    def splitInto(self, names: list) -> None:  # noqa: N802
        """Replace this artist with the named ones, moving every track across."""
        if self._artist_id <= 0:
            return

        cleaned: List[str] = [str(n).strip() for n in (names or []) if str(n).strip()]
        result = self._service.split(self._artist_id, cleaned)
        if result.error:
            self.error_occurred.emit(result.error)
            return

        # The artist that was open no longer exists; `artist.deleted` has already
        # cleared this object through the event bus.
        self.split_completed.emit(len(cleaned))

    @Slot(result="QVariantList")
    def trackIds(self) -> list:  # noqa: N802
        """Every track credited to the open artist, for "play all"."""
        if self._artist_id <= 0:
            return []
        return self._service.artist_repo.track_ids(self._artist_id)

    # ==================================================================
    # Properties
    # ==================================================================

    def _get(self, field: str) -> str:
        return self._staged.get(field, "")

    def _set(self, field: str, value: str) -> None:
        value = "" if value is None else str(value)
        if self._staged.get(field, "") == value:
            return
        self._staged[field] = value
        self.dirty_changed.emit()

    @Property(bool, notify=artist_changed)
    def hasArtist(self) -> bool:  # noqa: N802
        return self._artist_id > 0

    @Property(int, notify=artist_changed)
    def artistId(self) -> int:  # noqa: N802
        return self._artist_id

    @Property(str, notify=artist_changed)
    def name(self) -> str:
        return self._get("name")

    @name.setter
    def name(self, value: str) -> None:
        self._set("name", value)

    @Property(str, notify=artist_changed)
    def imageHash(self) -> str:  # noqa: N802
        return self._image_hash

    @Property(int, notify=artist_changed)
    def trackCount(self) -> int:  # noqa: N802
        return self._track_count

    @Property(bool, notify=dirty_changed)
    def isDirty(self) -> bool:  # noqa: N802
        return bool(self._staged) and (self._staged != self._baseline or self._ids.dirty)

    @Property(bool, notify=saving_changed)
    def isSaving(self) -> bool:  # noqa: N802
        return self._saving

    @Property(bool, notify=dirty_changed)
    def canSave(self) -> bool:  # noqa: N802
        """A name is the one thing an artist cannot be without."""
        return (
            self.hasArtist
            and not self._saving
            and self.isDirty
            and bool(self._staged.get("name", "").strip())
        )

    @Property(bool, notify=fetching_changed)
    def isFetching(self) -> bool:  # noqa: N802
        return self._fetch_thread is not None

    @Property(bool, notify=artist_changed)
    def needsAttention(self) -> bool:  # noqa: N802
        """Whether the open artist's name looks like several artists run together."""
        return looks_malformed(self._baseline.get("name", ""))
