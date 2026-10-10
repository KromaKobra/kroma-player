"""Track editing — the model behind Nocturne's other hidden tab.

Holds exactly one track's metadata while the user changes it. Single-track by
design, not by omission: the tab is where the per-track tools live, and things
like "detect this track's key" or "pitch this track down two semitones" have no
batch meaning. The context-menu entry that opens it hides itself the moment more
than one row is selected.

Edits are *staged*. ``load()`` takes a baseline snapshot and a working copy;
every field property reads and writes the working copy, and ``save()`` sends
only the keys that actually differ. That is what makes ``isDirty`` cheap and
exact, lets ``revert()`` be one assignment, and keeps the update from rewriting —
and retagging — fields the user never touched.

The fields that mean more than themselves are the external ids: saving a new
one offers to refresh the track's metadata and artwork from it. See the
"Refreshing from a source" section below for what that does and does not touch.
Which id fields exist at all is the enabled plugins' business — see
:mod:`app.viewmodels.external_id_fields` for how they are staged without ever
losing one a disabled plugin left behind.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from PySide6.QtCore import Property, QObject, QThread, QUrl, Signal, Slot

from app.importer.csv_import import (
    normalize_date,
    normalize_key,
    parse_bpm,
)
from app.albums.unify import UnifyError, master_bytes
from app.artwork import LARGE_SIZE, THUMB_SIZE
from app.audio.gain import clamp_gain_db
from app.importer.import_workers import (
    InstrumentalFetchWorker,
    InstrumentalImportWorker,
    RefreshWorker,
)
from app.importer.tagging import build_picture_block_from_bytes
from app.plugins import external_ids
from app.services.lyrics_service import SOURCE_SEARCH
from app.services.track_service import TrackService
from app.utils.library_paths import track_path
from app.viewmodels.external_id_fields import IdStaging
from app.viewmodels.worker_threads import own_worker

logger = logging.getLogger(__name__)

# Every staged field, and how the raw text the user typed becomes the value the
# database stores. A parser of None means "keep the string as typed".
#
# Text fields normalise to None when blank so that clearing a field in the UI
# clears the column, rather than leaving an empty string behind that sorts and
# filters differently from a genuinely missing value.
#
# External ids are not here. Their parsers come from the declaring plugin, and
# they are staged apart — see `IdStaging` — because a plugin can be off, and a
# field nobody can parse must never be saved as a delete.
_PARSERS = {
    "title":          None,
    "artists":        None,
    "album":          None,
    "release_date":   normalize_date,
    "genres":         None,
    "musical_key":    normalize_key,
    "bpm":            parse_bpm,
    "isrc":           None,
    "copyright_text": None,
    # Staged as text like everything else here, so `isDirty`, `revert()` and the
    # save diff all keep working unchanged — the float lives at the QML boundary
    # and nowhere else. The parser only has to clamp, because a slider cannot
    # produce anything a user could be told off for.
    "gain_db":        clamp_gain_db,
}

# Only the parsed fields need these — they name what a rejected value failed to be.
_FIELD_LABELS = {
    "release_date": "date",
    "musical_key":  "musical key",
    "bpm":          "tempo",
}


def _to_local_path(value) -> str:
    """Turn a QML ``file:///`` URL — or a plain path — into a filesystem path."""
    if isinstance(value, QUrl):
        return value.toLocalFile()
    text = str(value or "")
    if text.startswith("file:"):
        return QUrl(text).toLocalFile()
    return text


def _date_to_text(value: Optional[int]) -> str:
    """Render a stored ``YYYYMMDD`` as the ISO-ish text the field shows.

    The library's convention is ``00`` for an unknown month or day, so a
    year-only release comes back as just the year — which is also what
    ``normalize_date`` will turn back into the same integer.
    """
    if not value:
        return ""
    text = str(value).zfill(8)
    year, month, day = text[0:4], text[4:6], text[6:8]
    if month == "00":
        return year
    if day == "00":
        return f"{year}-{month}"
    return f"{year}-{month}-{day}"


class TrackEditViewModel(QObject):
    """One track's editable metadata, staged until saved."""

    track_changed = Signal()
    dirty_changed = Signal()
    saving_changed = Signal()
    saved = Signal(int)
    error_occurred = Signal(str)

    # Instrumental lifecycle
    instrumental_changed = Signal()
    instrumental_imported = Signal(int)   # track_id

    # Refreshing from a source
    overwrite_offered = Signal(str)       # the newly saved recording id
    fetching_changed = Signal()
    refreshed = Signal(int)               # track_id
    # Which id fields there are, or what one of them holds.
    ids_changed = Signal()

    # The gain slider, heard before it is saved. Announced rather than called:
    # the editor knows which track it holds and nothing about what is playing,
    # which is `PlayerViewModel`'s to judge. Wired in bootstrap.
    gain_preview_requested = Signal(int, float)   # track_id, decibels
    gain_preview_cleared = Signal()

    def __init__(
        self,
        track_service: TrackService,
        backing_service=None,
        cover_service=None,
        lyrics_service=None,
        registry=None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._track_service = track_service
        self._backing = backing_service
        self._cover_service = cover_service
        # Where a refresh puts the lyrics it finds. Optional like the two above;
        # without it a refresh simply keeps no lyrics.
        self._lyrics_service = lyrics_service
        # The providers: who can refresh this track, and who can fetch an
        # instrumental. ``None`` — no plugins — and both are simply absent.
        self._registry = registry
        self._ids = IdStaging(external_ids.TRACK, registry)
        if registry is not None and hasattr(registry, "changed"):
            registry.changed.connect(self._on_providers_changed)

        self._track_id: int = -1
        self._baseline: Dict[str, str] = {}
        self._staged: Dict[str, str] = {}
        self._saving = False

        # The artist set as ids, for GUIs that pick artists rather than type them.
        # See `setArtistIds` for why an id path exists at all.
        self._artist_ids: list[int] = []
        self._baseline_artist_ids: list[int] = []

        # The whole album list, in the track's own order, `[0]` primary. Refs
        # rather than ids — see `setAlbums` for why albums carry their name and
        # artists do not.
        self._album_refs: list[dict] = []
        self._baseline_album_refs: list[dict] = []

        # Read-only context shown beside the fields.
        self._cover_hash = ""
        self._duration_seconds = 0
        self._date_added = 0

        # The instrumental import runs off the UI thread — it transcodes.
        # So does the fetch, which shares the slot: one instrumental job at
        # a time, whichever way it arrives.
        self._instrumental_thread: Optional[QThread] = None
        self._instrumental_worker: Optional[QObject] = None
        self._fetching_instrumental = False

        # So does the refresh — it reaches a plugin and then the network.
        self._refresh_thread: Optional[QThread] = None
        self._refresh_worker: Optional[RefreshWorker] = None

        # A track that is deleted while it is open has nothing left to save to.
        track_service.event_bus.subscribe("track.deleted", self._on_track_deleted)

    # ==================================================================
    # Loading
    # ==================================================================

    @Slot(int)
    def load(self, track_id: int) -> None:
        """Open a track for editing, discarding whatever was staged before.

        Deliberately unconditional: the tab holds one track, and the caller —
        a context menu or the details panel — has already decided which.
        """
        try:
            track_id = int(track_id)
        except (TypeError, ValueError):
            return
        # Whatever gain the last track was being previewed at stops applying the
        # moment this one is opened. Emitted before the read so it lands even if
        # the track turns out to be gone.
        self.gain_preview_cleared.emit()

        track = self._track_service.get_track_by_id(track_id) if track_id > 0 else None
        if track is None:
            self.clear()
            self.error_occurred.emit("That track is no longer in the library")
            return

        self._track_id = track_id
        self._baseline = {
            "title":          track.title or "",
            "artists":        ", ".join(a.name for a in track.artists),
            "album":          track.album or "",
            "release_date":   _date_to_text(track.release_date),
            "genres":         ", ".join(g.name for g in track.genres),
            "musical_key":    track.musical_key or "",
            "bpm":            str(track.bpm) if track.bpm else "",
            "isrc":           track.isrc or "",
            "copyright_text": track.copyright_text or "",
            # One decimal, always. A slider drag would otherwise stage "0.5"
            # against a baseline of "0.50" and report a dirty editor over a
            # value nobody changed.
            "gain_db":        f"{clamp_gain_db(track.gain_db):.1f}",
        }
        self._staged = dict(self._baseline)
        # Every id the track holds, whatever is enabled — see `IdStaging`.
        self._ids.load(external_ids.read(external_ids.TRACK, track))
        self._cover_hash = track.cover_hash or ""
        self._duration_seconds = track.duration_seconds or 0
        self._date_added = track.created_at or 0
        # The same artists as `_baseline["artists"]`, by id. Kept beside the
        # string rather than replacing it: a GUI that only knows the text field
        # keeps working, and a save uses whichever the GUI actually touched.
        self._artist_ids = [int(a.id) for a in track.artists]
        self._baseline_artist_ids = list(self._artist_ids)
        # Every album, not just the primary `_baseline["album"]` names. In the
        # track's own order, so `[0]` is the record it belongs to.
        self._album_refs = [
            {"id": int(a.id), "name": a.name or ""} for a in track.albums
        ]
        self._baseline_album_refs = [dict(r) for r in self._album_refs]

        self.track_changed.emit()
        self.dirty_changed.emit()
        self.instrumental_changed.emit()
        self.ids_changed.emit()

    @Slot("QVariantList")
    def setArtistIds(self, ids: list) -> None:  # noqa: N802
        """Stage the track's artists as a list of artist ids.

        The alternative to typing names into ``artists``, and the reason a GUI
        should prefer it: names are resolved by exact match, so saving "daft punk"
        beside an existing "Daft Punk" mints a second artist and splits the
        catalogue between them. An id says which artist was meant.

        Staging it also updates the ``artists`` string so a GUI showing both stays
        coherent, but the save sends the ids — the string is the display.
        """
        cleaned: list[int] = []
        for raw in ids or []:
            try:
                artist_id = int(raw)
            except (TypeError, ValueError):
                continue
            if artist_id > 0 and artist_id not in cleaned:
                cleaned.append(artist_id)

        if cleaned == self._artist_ids:
            return
        self._artist_ids = cleaned
        self._staged["artists"] = ", ".join(self._artist_names(cleaned))
        self.track_changed.emit()
        self.dirty_changed.emit()

    @Slot("QVariantList")
    def setAlbums(self, entries: list) -> None:  # noqa: N802
        """Stage the track's albums, in order. ``entries[0]`` is the primary.

        Each entry is ``{"id", "name"}``. An id of 0 is a name that matched
        nothing and will be created on save.

        Why refs where ``setArtistIds`` takes bare ids. An artist chip that
        matched nothing can fall back to the ``artists`` name string, and the
        service creates the artist from it. Albums have no such fallback: the
        ``album`` string sets only the *primary*, so a newly-typed album in
        second place could never be created at all. Albums are also resolved by
        name **and lead artist**, because two unrelated records really are both
        called "Greatest Hits" — a name on its own does not identify one.

        Replaces the whole list, which is the other difference. Assigning
        ``album`` says "this is the record it belongs to" and leaves the
        compilations alone; this says "these are its albums", so what is not
        here was taken off on purpose.
        """
        cleaned: list[dict] = []
        seen_ids: set[int] = set()
        seen_names: set[str] = set()
        for raw in entries or []:
            if not isinstance(raw, dict):
                continue
            try:
                album_id = int(raw.get("id") or 0)
            except (TypeError, ValueError):
                album_id = 0
            name = str(raw.get("name") or "").strip()
            if album_id <= 0 and not name:
                continue
            # Same row, or the same name typed twice in different capitals.
            # Either way it is already on the list.
            if album_id > 0 and album_id in seen_ids:
                continue
            if album_id <= 0 and name.lower() in seen_names:
                continue
            if album_id > 0:
                seen_ids.add(album_id)
            seen_names.add(name.lower())
            cleaned.append({"id": album_id, "name": name})

        if cleaned == self._album_refs:
            return
        self._album_refs = cleaned
        # The `album` string is the primary's name, and a GUI showing both
        # should stay coherent. The save sends the refs; this is the display.
        self._staged["album"] = cleaned[0]["name"] if cleaned else ""
        self.track_changed.emit()
        self.dirty_changed.emit()

    def _artist_names(self, ids: list[int]) -> list[str]:
        repo = self._track_service.artist_repo
        names = []
        for artist_id in ids:
            artist = repo.get_by_id(artist_id)
            if artist is not None:
                names.append(artist.name)
        return names

    @Slot()
    def revert(self) -> None:
        """Throw away every staged change, keeping the track open."""
        if (self._staged == self._baseline
                and self._artist_ids == self._baseline_artist_ids
                and self._album_refs == self._baseline_album_refs
                and not self._ids.dirty):
            return
        self._staged = dict(self._baseline)
        self._artist_ids = list(self._baseline_artist_ids)
        self._album_refs = [dict(r) for r in self._baseline_album_refs]
        self._ids.revert()
        self.gain_preview_cleared.emit()
        self.track_changed.emit()
        self.dirty_changed.emit()
        self.ids_changed.emit()

    @Slot()
    def clear(self) -> None:
        """Close the editor. What makes the tab's rail slot go away."""
        self._track_id = -1
        self._baseline = {}
        self._staged = {}
        self._artist_ids = []
        self._baseline_artist_ids = []
        self._album_refs = []
        self._baseline_album_refs = []
        self._ids.clear()
        self._cover_hash = ""
        self._duration_seconds = 0
        self._date_added = 0
        # Closing the editor abandons a previewed gain the same way Revert does.
        # This is also the path a deleted track takes, which QML never sees —
        # the reason the restore is wired here rather than in the view.
        self.gain_preview_cleared.emit()
        self.track_changed.emit()
        self.dirty_changed.emit()
        self.instrumental_changed.emit()
        self.ids_changed.emit()

    def _on_track_deleted(self, track_id: int) -> None:
        if self._track_id > 0 and int(track_id) == self._track_id:
            self.clear()

    # ==================================================================
    # Saving
    # ==================================================================

    @Slot()
    def save(self) -> None:
        """Write the staged changes, normalising each field on the way through."""
        if not self.canSave:
            return

        changes: Dict[str, Any] = {}
        for field, raw in self._staged.items():
            if raw == self._baseline.get(field):
                continue
            if field not in _PARSERS:
                # Not a field this editor knows how to write. Skipped rather
                # than a KeyError: a stray key must never fail — or reach — a
                # save.
                continue
            parser = _PARSERS[field]
            if parser is None:
                # Relationship strings stay strings even when empty — the
                # service reads "" as "this track now has no artists".
                value: Any = raw.strip() or (
                    "" if field in ("artists", "genres") else None
                )
            else:
                value = parser(raw)
                # A normaliser that finds nothing in text the user did type is a
                # rejection, not a request to clear the field. Saying so beats
                # silently discarding a mistyped id or an unparseable date.
                #
                # Membership in `_FIELD_LABELS` is what makes a field rejectable
                # at all — a field with no label has no way to name what the
                # value failed to be, because nothing about it can fail. That is
                # `gain_db`: it is clamped rather than parsed, and 0.0 is dead
                # centre of its range, which a falsiness test would read as a
                # failure and refuse the whole save over.
                if field in _FIELD_LABELS and raw.strip() and not value:
                    self.error_occurred.emit(
                        f"“{raw.strip()}” is not a valid {_FIELD_LABELS[field]}"
                    )
                    return
            changes[field] = value

        # When the GUI picked artists by id, send the ids and drop the string it
        # derived. Both describe the same set, but only the ids say *which* rows —
        # and the service falls back to name resolution for the string, which is
        # exactly the ambiguity the id path exists to avoid.
        if self._artist_ids != self._baseline_artist_ids:
            changes.pop("artists", None)
            changes["artist_ids"] = list(self._artist_ids)

        # Same trade for the albums, and one step further: the refs are the whole
        # list, where the string only ever named the primary. Sending both would
        # let `_apply_albums` re-promote a name the refs had just demoted.
        if self._album_refs != self._baseline_album_refs:
            changes.pop("album", None)
            changes["album_refs"] = [dict(r) for r in self._album_refs]

        # The ids, only the kinds that changed. Parsed by the declaring plugin's
        # own rules; text they cannot read is refused rather than taken as a
        # request to clear — see `IdStaging.changes`.
        id_changes, id_error = self._ids.changes()
        if id_error:
            self.error_occurred.emit(id_error)
            return
        if id_changes:
            changes["external_ids"] = id_changes

        if not changes:
            return

        # Refused for the same reason a mistyped id is: writing it would lose
        # something. Two tracks sharing a unique id makes the library's own
        # "you already have this" check ambiguous, so the whole save is held back
        # — nothing is written, the staged edits stay put, and the user can
        # correct the id and click Save again.
        for key, value in id_changes.items():
            conflict = self._id_conflict(key, value) if value else ""
            if conflict:
                self.error_occurred.emit(conflict)
                return

        # A different album is now the one this recording belongs to, so its
        # artwork comes with it. Resolved before the write and refused rather
        # than skipped if it cannot be: a save that quietly kept the old cover
        # would leave the album flagged as disagreeing with its own tracks, which
        # is the mess this is here to avoid.
        picture_block: Optional[str] = None
        album = self._new_primary_album()
        if album is not None:
            try:
                picture_block = self._picture_block_for(album)
            except UnifyError as exc:
                self.error_occurred.emit(str(exc))
                return
            changes["cover_hash"] = album.cover_hash

        self._set_saving(True)
        try:
            result = self._track_service.update_track_metadata(
                self._track_id, changes, picture_block=picture_block
            )
        except Exception as exc:  # noqa: BLE001 — a failed save must not kill the tab
            logger.exception("Saving track %s failed", self._track_id)
            result = None
            self.error_occurred.emit(str(exc))
        finally:
            self._set_saving(False)

        if result is None:
            return
        if result.track is None:
            self.error_occurred.emit("That track is no longer in the library")
            self.clear()
            return

        # Re-read rather than assuming: normalisation means what was stored is
        # often not what was typed (2021 → 20210000 → "2021"), and the fields
        # should settle on the truth.
        track_id = self._track_id
        self.load(track_id)
        if result.warning:
            self.error_occurred.emit(result.warning)
        self.saved.emit(track_id)

        # A new id almost always means the old metadata described the wrong
        # recording, so this is the moment to offer the right one's. Only a
        # recording id that actually changed and actually points somewhere:
        # clearing the field is a removal, and there is nothing to fetch from it.
        recording = {k.key for k in self._ids.declarations("recording")}
        offered = next((v for k, v in id_changes.items() if v and k in recording), "")
        if offered and self.canRefresh:
            self.overwrite_offered.emit(offered)

    def _picture_block_for(self, album) -> str:
        """The album's cover as a payload to embed in this track's file.

        Delegates the choice of source to :func:`~app.albums.unify.master_bytes`,
        which prefers a file already carrying the picture over the cache. The
        cache is a re-encode; reaching for it first would quietly downgrade the
        artwork every time an album was promoted. Raises ``UnifyError`` when the
        cover cannot be read back from either.
        """
        keeper_path = None
        for row in self._track_service.album_repo.cover_rows(int(album.id)):
            if row["cover_hash"] == album.cover_hash:
                keeper_path = track_path(row["track_id"])
                break

        store = getattr(self._cover_service, "store", None)

        def read_cached(digest: str):
            if store is None:
                return None
            return store.get(digest, LARGE_SIZE) or store.get(digest, THUMB_SIZE)

        data = master_bytes(keeper_path, str(album.cover_hash), read_cached)
        return build_picture_block_from_bytes(data, mime="image/avif")

    def _id_conflict(self, key: str, value: str) -> str:
        """Name the other track already holding *value* under *key*, or "".

        A unique id belongs to one track. Online search relies on it: a result
        the library already has shows a checkmark instead of a download button,
        and that lookup — the same one used here — answers with a single track
        id. Two rows sharing an id would make it answer arbitrarily.

        The track's own id is not a conflict with itself, which matters because
        an edit that only reformats the field (a full link pasted over the bare
        id it already held) parses back to the value already stored.
        """
        owner_id = self._track_service.track_id_for(key, value, exclude_id=self._track_id)
        if owner_id is None:
            return ""
        owner = self._track_service.get_track_by_id(owner_id)
        name = f"“{owner.title}”" if owner and owner.title else f"track {owner_id}"
        kind = self._ids.declaration(key)
        return f"{name} already has that {kind.label if kind else 'id'}"

    def _set_saving(self, value: bool) -> None:
        if value == self._saving:
            return
        self._saving = value
        self.saving_changed.emit()
        # canSave reads _saving as well as the staged diff, and a Qt property
        # gets exactly one notify signal — so the dirty one has to cover both.
        self.dirty_changed.emit()

    # ==================================================================
    # Instrumental
    # ==================================================================
    #
    # An instrumental is the same recording as the track it belongs to, without
    # the vocals, stored at ``bt/`` under the mix's own id — so the two files
    # share a name and nothing else has to be recorded to pair them.
    #
    # Which is why this lives on the *edit* page and not in the importer. It is
    # not a track being added to the library; it is a second file for a track
    # already there, chosen by a user looking at that exact track. The importer
    # used to offer it as a mode over a whole matched batch, which meant every
    # file had to be matched online first just to work out which library
    # track it belonged to — a search to answer a question the user could
    # already answer, and a batch of guesses where one certainty would do.
    #
    # Two things are required of the file and both are checked in the service:
    # it must be the same length as the mix, or the mid-playback toggle is not
    # seamless; and it is transcoded to Opus with its tags stripped, because the
    # metadata belongs to the mix and a second copy could only go stale.

    @Property(bool, notify=instrumental_changed)
    def hasInstrumental(self) -> bool:
        """Whether this track already has one."""
        if self._backing is None or self._track_id <= 0:
            return False
        return self._backing.has(self._track_id)

    @Property(bool, notify=instrumental_changed)
    def isImportingInstrumental(self) -> bool:
        """True while either an import or a fetch is running."""
        return self._instrumental_thread is not None

    @Property(bool, notify=instrumental_changed)
    def isFetchingInstrumental(self) -> bool:  # noqa: N802
        """True while the running instrumental job is a fetch from a source."""
        return self._fetching_instrumental

    def _instrumental_kind(self) -> Any:
        """The instrumental id kind an enabled audio source can fetch by, or None."""
        if self._registry is None:
            return None
        accepted = set(self._registry.audio_keys("instrumental"))
        return next(
            (k for k in self._ids.declarations("instrumental") if k.key in accepted), None
        )

    @Property(bool, notify=ids_changed)
    def canFetchInstrumental(self) -> bool:  # noqa: N802
        """Whether an enabled audio source can fetch an instrumental by id."""
        return self._backing is not None and self._instrumental_kind() is not None

    @Slot(QUrl)
    @Slot(str)
    def importInstrumental(self, url) -> None:
        """Install *url* as this track's instrumental, off the UI thread.

        No matching, no search, no metadata: the track is the one on screen and
        everything worth knowing about the recording is already stored against
        it. All this does is transcode and check the length.
        """
        if self._backing is None or self._track_id <= 0:
            return
        if self._instrumental_thread is not None:
            self.error_occurred.emit("Still importing the last instrumental")
            return

        path = _to_local_path(url)
        if not path:
            self.error_occurred.emit("No file was selected")
            return

        thread = QThread(self)
        worker = InstrumentalImportWorker(self._track_id, path, self._backing)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_instrumental_finished)
        worker.failed.connect(self._on_instrumental_failed)
        own_worker(thread, worker, self._on_instrumental_stopped)
        self._instrumental_thread = thread
        self._instrumental_worker = worker
        thread.start()
        self.instrumental_changed.emit()

    @Slot(str)
    def fetchInstrumental(self, text: str) -> None:  # noqa: N802
        """Fetch the instrumental *text* names into this track's ``bt/``.

        *text* is read with the instrumental kind's own parse rules, so a link
        works as well as a bare id. The id is recorded against the track once
        the file is installed, and a fetch that fails records nothing.
        """
        if self._backing is None or self._track_id <= 0:
            return
        if self._instrumental_thread is not None:
            self.error_occurred.emit("Still working on the last instrumental")
            return
        kind = self._instrumental_kind()
        if kind is None:
            self.error_occurred.emit("No enabled plugin can fetch an instrumental")
            return

        raw = str(text or "").strip()
        value = kind.parse_value(raw) if raw else ""
        if not value:
            self.error_occurred.emit(
                f"“{raw}” is not a valid {kind.label}" if raw
                else f"Enter the instrumental's {kind.label} first"
            )
            return

        thread = QThread(self)
        worker = InstrumentalFetchWorker(
            self._track_id, kind.key, value, self._backing, self._registry
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_instrumental_fetched)
        worker.failed.connect(self._on_instrumental_failed)
        own_worker(thread, worker, self._on_instrumental_stopped)
        self._instrumental_thread = thread
        self._instrumental_worker = worker
        self._fetching_instrumental = True
        thread.start()
        self.instrumental_changed.emit()

    @Slot(int)
    def _on_instrumental_finished(self, track_id: int) -> None:
        self.instrumental_imported.emit(track_id)

    @Slot(int, str, str)
    def _on_instrumental_fetched(self, track_id: int, key: str, value: str) -> None:
        """Record where the instrumental came from. Runs on the UI thread.

        Written straight to the row rather than through ``save()``: the file is
        already in ``bt/`` and the id is a fact about it, not an edit the user
        still has to confirm. Everything else staged stays staged — this only
        settles the one id, so nothing typed elsewhere is thrown away.

        Keyed by the worker's track, not the open one, because the user may have
        opened another track while the fetch ran.
        """
        try:
            result = self._track_service.update_track_metadata(
                track_id, {"external_ids": {key: value}}
            )
        except Exception as exc:  # noqa: BLE001 — the instrumental is installed either way
            logger.exception("Recording the instrumental id for track %s failed", track_id)
            self.error_occurred.emit(str(exc))
            result = None

        if result is not None and result.track is not None and track_id == self._track_id:
            self._ids.settle(key, value)
            self.ids_changed.emit()
            self.dirty_changed.emit()

        self.instrumental_imported.emit(track_id)

    @Slot(str)
    def _on_instrumental_failed(self, message: str) -> None:
        self.error_occurred.emit(message)

    @Slot()
    def _on_instrumental_stopped(self) -> None:
        """Released on `finished`, not on `done` — see `own_worker`."""
        self._instrumental_thread = None
        self._instrumental_worker = None
        self._fetching_instrumental = False
        self.instrumental_changed.emit()

    # ==================================================================
    # Refreshing from a source
    # ==================================================================
    #
    # A wrong id is rarely wrong alone. A track matched to the wrong recording
    # on import took that recording's title, artist, album and cover with it,
    # so the id is the field that, once corrected, can put all the others right
    # — which is why `save` offers this and no other field does.
    #
    # What it does *not* do is fetch any audio. The recording on disk may be the
    # user's own file, transcoded once and never again, and replacing it to fix
    # a spelling would be a trade nobody asked for. So the audio stays and only
    # what describes it changes: the database row, the cover cache, and the
    # file's own tags.
    #
    # The consequence worth knowing is that `duration_seconds` is left alone even
    # though a source supplies it. It describes the file on disk, not the
    # catalogue entry — and the instrumental toggle refuses a backing track that
    # is not exactly the mix's length, so a duration copied from elsewhere would
    # break a feature that has nothing to do with this one.

    def _refresh_provider(self) -> Any:
        if self._registry is None:
            return None
        return self._registry.metadata(external_ids.TRACK)

    @Property(bool, notify=fetching_changed)
    def isFetching(self) -> bool:  # noqa: N802
        return self._refresh_thread is not None

    @Property(bool, notify=ids_changed)
    def canRefresh(self) -> bool:  # noqa: N802
        """Whether a metadata provider is on and this track has an id it declares."""
        return (
            self._track_id > 0
            and self._refresh_provider() is not None
            and bool(self._recording_ids())
        )

    @Property(str, notify=ids_changed)
    def refreshLabel(self) -> str:  # noqa: N802
        """What to call the source a refresh reads from, as its plugin names it."""
        kinds = self._ids.declarations("recording")
        saved = self._recording_ids()
        kind = next((k for k in kinds if k.key in saved), kinds[0] if kinds else None)
        if kind is not None:
            return kind.source_label or kind.label
        provider = self._refresh_provider()
        return provider.name if provider is not None else ""

    def _recording_ids(self) -> Dict[str, str]:
        recording = {k.key for k in self._ids.declarations("recording")}
        return {k: v for k, v in self._ids.baseline_ids().items() if k in recording}

    @Slot()
    def refreshFromSource(self) -> None:  # noqa: N802
        """Replace this track's metadata and artwork with its source's.

        Reads the ids from the baseline rather than from the staged copy: the
        offer follows a save, so what is stored is what was just agreed to, and
        anything typed since is not what the user said yes to.
        """
        if self._track_id <= 0:
            return
        if self._refresh_thread is not None:
            self.error_occurred.emit("Still fetching the last one")
            return
        ids = self._recording_ids()
        if not ids:
            self.error_occurred.emit("This track has no id to refresh from")
            return
        if self._refresh_provider() is None:
            self.error_occurred.emit("No enabled plugin can refresh this track")
            return

        thread = QThread(self)
        worker = RefreshWorker(external_ids.TRACK, ids, self._registry, self._cover_service)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.fetched.connect(self._on_refresh_fetched)
        worker.failed.connect(self._on_refresh_failed)
        own_worker(thread, worker, self._on_refresh_stopped)
        self._refresh_thread = thread
        self._refresh_worker = worker
        thread.start()
        self.fetching_changed.emit()

    @Slot(object, str, object)
    def _on_refresh_fetched(self, track_info, cover_hash: str, master) -> None:
        """Apply what the worker fetched. Runs on the UI thread — see the worker."""
        if self._track_id <= 0:
            return

        # Only what the source actually gave us. A field it has nothing for is a
        # field it knows nothing about, not a field it says is empty — blanking
        # the tempo or the ISRC a user measured by hand would lose real work.
        changes: Dict[str, Any] = {
            field: track_info[field]
            for field in ("title", "artists", "album", "release_date")
            if track_info.get(field) not in (None, "")
        }

        picture_block: Optional[str] = None
        if cover_hash and master:
            changes["cover_hash"] = cover_hash
            picture_block = build_picture_block_from_bytes(master, mime="image/avif")

        # Lyrics go through their own service rather than through `extra_tags`,
        # which is still the escape hatch for tags with no model field. The
        # service writes the row and then the file's LYRICS comment, in that
        # order — see ADR 0008 — so this reaches both places `extra_tags` used
        # to reach one of.
        lyrics = track_info.get("lyrics") if self._lyrics_service is not None else None

        if not changes and not lyrics:
            self.error_occurred.emit("The source had nothing to refresh this track with")
            return

        if lyrics:
            try:
                self._lyrics_service.save(self._track_id, lyrics, SOURCE_SEARCH)
            except Exception:  # noqa: BLE001 — the metadata write still stands
                logger.exception("Storing refreshed lyrics for track %s failed",
                                 self._track_id)

        if not changes:
            # Lyrics and nothing else. The service has already written the row,
            # the file's LYRICS comment and the bus event, and there is no other
            # edit left to make — calling the metadata update with an empty dict
            # would commit nothing and retag the file a second time for it.
            self.refreshed.emit(self._track_id)
            return

        try:
            result = self._track_service.update_track_metadata(
                self._track_id, changes, picture_block=picture_block
            )
        except Exception as exc:  # noqa: BLE001 — a failed refresh must not kill the tab
            logger.exception("Refreshing track %s failed", self._track_id)
            self.error_occurred.emit(str(exc))
            return

        if result.track is None:
            self.error_occurred.emit("That track is no longer in the library")
            self.clear()
            return

        track_id = self._track_id
        self.load(track_id)
        if result.warning:
            self.error_occurred.emit(result.warning)
        self.refreshed.emit(track_id)

    @Slot(str)
    def _on_refresh_failed(self, message: str) -> None:
        self.error_occurred.emit(message)

    @Slot()
    def _on_refresh_stopped(self) -> None:
        self._refresh_thread = None
        self._refresh_worker = None
        self.fetching_changed.emit()

    # ==================================================================
    # External ids
    # ==================================================================

    @Property("QVariantList", notify=ids_changed)
    def externalIdFields(self) -> list:  # noqa: N802
        """One entry per id kind an enabled plugin declares for tracks.

        ``{key, label, sourceLabel, role, value, saved, url, editable}``. The
        ``role`` says which section a field belongs in: ``recording`` ids name
        the track, ``instrumental`` ids name where its instrumental came from.
        Text is pushed in with :meth:`setExternalId`, not bound — the field
        keeps what was typed while ``value`` is the staged copy.
        """
        return self._ids.fields()

    @Slot(str, str)
    def setExternalId(self, key: str, text: str) -> None:  # noqa: N802
        if self._track_id <= 0:
            return
        if self._ids.set(str(key), text):
            self.dirty_changed.emit()

    @Slot()
    def _on_providers_changed(self) -> None:
        """A plugin went on or off: which id fields exist may have changed."""
        self.ids_changed.emit()
        self.dirty_changed.emit()

    # ==================================================================
    # State
    # ==================================================================

    @Property(bool, notify=track_changed)
    def hasTrack(self) -> bool:
        return self._track_id > 0

    @Property(int, notify=track_changed)
    def trackId(self) -> int:
        return self._track_id

    @Property(bool, notify=dirty_changed)
    def isDirty(self) -> bool:
        if self._album_refs != self._baseline_album_refs:
            return True
        if self._artist_ids != self._baseline_artist_ids:
            return True
        if self._ids.dirty:
            return True
        return bool(self._staged) and self._staged != self._baseline

    @Property(bool, notify=saving_changed)
    def isSaving(self) -> bool:
        return self._saving

    @Property(bool, notify=dirty_changed)
    def canSave(self) -> bool:
        """A title is the one thing a track cannot be without."""
        return (
            self.hasTrack
            and not self._saving
            and self.isDirty
            and bool(self._staged.get("title", "").strip())
        )

    # ---- read-only context -------------------------------------------

    @Property(str, notify=track_changed)
    def coverHash(self) -> str:
        return self._cover_hash

    @Property(int, notify=track_changed)
    def durationSeconds(self) -> int:
        return self._duration_seconds

    @Property(int, notify=track_changed)
    def dateAdded(self) -> int:
        return self._date_added

    @Property(str, notify=track_changed)
    def localIdHex(self) -> str:
        return f"{self._track_id:04X}" if self._track_id > 0 else ""

    # ==================================================================
    # Editable fields
    # ==================================================================

    def _get(self, field: str) -> str:
        return self._staged.get(field, "")

    def _set(self, field: str, value: str) -> None:
        text = str(value if value is not None else "")
        if not self._staged or self._staged.get(field) == text:
            return
        self._staged[field] = text
        self.dirty_changed.emit()

    @Property(str, notify=track_changed)
    def title(self) -> str:
        return self._get("title")

    @title.setter
    def title(self, value: str) -> None:
        self._set("title", value)

    @Property(str, notify=track_changed)
    def artists(self) -> str:
        return self._get("artists")

    @artists.setter
    def artists(self, value: str) -> None:
        # Typing into the text field abandons any id-based selection: the names
        # are now what the user means, and keeping stale ids around would let the
        # save send a set the user can no longer see.
        self._artist_ids = list(self._baseline_artist_ids)
        self._set("artists", value)

    @Property("QVariantList", notify=track_changed)
    def artistIds(self) -> list:  # noqa: N802
        """The staged artists as ids, in order. Read with ``setArtistIds``."""
        return list(self._artist_ids)

    @Property(str, notify=track_changed)
    def album(self) -> str:
        return self._get("album")

    @album.setter
    def album(self, value: str) -> None:
        # Typing into the text field abandons any staged chip selection, the same
        # way the `artists` setter does — and for the extra reason that the two
        # do not even mean the same thing. The string sets the primary and keeps
        # the compilations; the refs are the whole list.
        self._album_refs = [dict(r) for r in self._baseline_album_refs]
        self._set("album", value)

    @Property("QVariantList", notify=track_changed)
    def albumIds(self) -> list:  # noqa: N802
        """The staged albums as ids, in order, ``[0]`` primary.

        A 0 is an album the user named that does not exist yet. Written with
        ``setAlbums``, which carries the names as well — see it for why.
        """
        return [int(r.get("id") or 0) for r in self._album_refs]

    @Property(str, notify=dirty_changed)
    def primaryAlbumName(self) -> str:  # noqa: N802
        """The staged primary album's name, or "". What the artwork prompt names."""
        return str(self._album_refs[0]["name"]) if self._album_refs else ""

    @Property(bool, notify=dirty_changed)
    def artworkWillChange(self) -> bool:  # noqa: N802
        """Whether saving now would replace this track's artwork.

        A track's artwork belongs to the album it is primary for, so promoting a
        different album moves the cover with it — in the library *and* in the
        audio file. That is worth a question before it happens rather than a
        toast afterwards, and this is what a GUI asks to decide whether to ask.

        False unless there is genuinely something to change to: a brand-new album
        has no cover yet, an album whose cover the track already carries is a
        no-op, and reordering the compilations behind the primary changes nothing
        about which record this recording belongs to.
        """
        album = self._new_primary_album()
        return album is not None and bool(album.cover_hash)

    def _new_primary_album(self):
        """The album promoted to primary by the staged edit, if one was.

        Returns the row only when its artwork differs from the track's — the one
        case anything downstream cares about.
        """
        if self._track_id <= 0 or not self._album_refs:
            return None
        staged = int(self._album_refs[0].get("id") or 0)
        was = int(self._baseline_album_refs[0].get("id") or 0) \
            if self._baseline_album_refs else 0
        if staged <= 0 or staged == was:
            return None
        album = self._track_service.album_repo.get_by_id(staged)
        if album is None or not album.cover_hash:
            return None
        return album if album.cover_hash != self._cover_hash else None

    @Property(str, notify=track_changed)
    def releaseDate(self) -> str:
        return self._get("release_date")

    @releaseDate.setter
    def releaseDate(self, value: str) -> None:
        self._set("release_date", value)

    @Property(str, notify=track_changed)
    def genres(self) -> str:
        return self._get("genres")

    @genres.setter
    def genres(self, value: str) -> None:
        self._set("genres", value)

    @Property(str, notify=track_changed)
    def musicalKey(self) -> str:
        return self._get("musical_key")

    @musicalKey.setter
    def musicalKey(self, value: str) -> None:
        self._set("musical_key", value)

    @Property(str, notify=track_changed)
    def bpm(self) -> str:
        return self._get("bpm")

    @bpm.setter
    def bpm(self, value: str) -> None:
        self._set("bpm", value)

    @Property(str, notify=track_changed)
    def isrc(self) -> str:
        return self._get("isrc")

    @isrc.setter
    def isrc(self, value: str) -> None:
        self._set("isrc", value)

    @Property(str, notify=track_changed)
    def copyrightText(self) -> str:
        return self._get("copyright_text")

    @copyrightText.setter
    def copyrightText(self, value: str) -> None:
        self._set("copyright_text", value)

    @Property(float, notify=track_changed)
    def gainDb(self) -> float:  # noqa: N802
        """This recording's level trim, in decibels. 0.0 is "as recorded".

        The one field exposed as a number rather than as text, because the
        control behind it is a slider and there is nothing for a user to mistype.
        The staged value is still a string like every other one — see `_PARSERS`.
        """
        try:
            return float(self._get("gain_db") or 0.0)
        except ValueError:
            return 0.0

    @gainDb.setter
    def gainDb(self, value: float) -> None:  # noqa: N802
        staged = f"{clamp_gain_db(value):.1f}"
        if self._staged.get("gain_db") == staged:
            return
        self._set("gain_db", staged)
        # Say so even while the drag is still in progress. A level is set by ear
        # against something audible, and a slider that only takes effect on Save
        # would make that a guess followed by a correction.
        self.gain_preview_requested.emit(self._track_id, float(staged))
