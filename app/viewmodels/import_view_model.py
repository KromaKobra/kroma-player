"""Import ViewModel — drives the Database tab's local-file import flow.

Lifecycle (all long steps run on worker threads):

1. ``analyzePaths(urls)``  → scan + probe files, then emit ``analysis_ready``.
2. QML shows the *options* card, which draws what the scan is made of by asking
   ``importPreview(fallback, excludedFormats)`` — once when it opens and again
   on every control it offers, because each of them changes what the numbers on
   it say.
3. ``beginImport(fallback, excludedFormats)`` takes that answer, remembers it,
   turns it into the candidate set and starts the run. There is one press and
   one commit point; the two menus this replaced asked two questions in
   sequence, the second of them only sometimes, with no way back to the first.
4. The identify worker asks the identity provider which recording each file is
   — or, with none enabled, skips that step and builds each row from the file's
   own tags. The results are *appended* to the model and ``search_completed``
   fires with found / not-found / already-in-library stats.
5. Per-row ``stashTrack(uid)`` / ``stashAll()`` transcode + tag + persist the
   chosen matches. Stashes run **one at a time** through an internal queue.

Every source adds to the list rather than replacing it, so several folders — a
folder and a CSV, a hand-paired file and a pasted link — can be gathered up and
taken in one batch. Rows of either **kind** sit in the one list: nothing here
speaks for the list as a whole any more.

That download/stash split is what ``is_download`` marks on each row, and it is
the only distinction the saving code draws. A local file is *stashed* — it is
already on the machine and only has to be transcoded and filed. A CSV row and a
row from a plugin's import source are *downloaded*, because nothing but an id is known about
them. Where a downloaded row came from stops mattering the moment it is in the
list, which is why the flag says what happens to it rather than who sent it. It
is exposed as the ``isDownload`` role so the row draws itself from the same
fact the saving code reads. Which audio source fetches it is decided when it is
saved, by whichever enabled one accepts one of its ids.

A row already listed is skipped rather than added twice. What makes two rows
the same is `_identities`: the file they came from, an external id they carry,
or either. Most of that filtering happens before step 4, so a re-imported folder
costs no network calls at all; the rest happens at the merge, because a local
file has no id to be compared by until the match run gives it one.

A row that is *in the library* is a separate question from one already listed,
and ``ImportSettings.clear_imported`` — on by default — answers it: the list
*shows* only rows that still have work in them. One a match finds is already
there is listed but not shown, and one that finishes importing goes off screen
as it finishes, named by ``imported_removed`` on the way out so the GUI can tell
that departure from a row the user threw out.

It hides; it does not discard. Every such row stays in ``_results`` — what the
model exposes is ``_visible``, an ascending subsequence of it — so turning the
setting off gives them back where they were, and their count stays askable
through ``inDatabaseCount`` and ``stateCounts`` the whole time they are off
screen. A scan and a match run are expensive enough that a settings toggle
should not be able to throw half of one away, and a count nothing on the page
can reach is a count the user cannot act on. The per-run figure the completion
toast quotes is still ``lastInDatabaseCount``; the list's own is
``inDatabaseCount``.

Three side doors skip straight past the scan. ``importFileAs(url, metadata)``
takes a file the caller has already identified — an online search result the
user pointed at — so steps 1 to 4 have nothing left to work out.
``importFromSource`` hands what the user typed to a plugin's import source and
lists the rows it resolves to. ``findAlbumTracks`` takes a library album and
lists the whole release it is identified as, in one call. All three produce rows
indistinguishable from identified ones thereafter.

Steps 1 and 4 are the long ones, and ``cancelImport()`` abandons either: the
worker stops at its next file, its results are dropped, and the tab goes back to
whatever it was showing before that run began — an earlier import's results, or
the library page. Nothing already in the model is disturbed, because a run that
was cancelled never put anything there.

The results model reuses the shared track-list roles, so it renders through the
same delegate/columns as the online search results (cover, title/artist, album,
duration) plus an import-specific *stash* action column.
"""

from __future__ import annotations

import logging
from bisect import bisect_left
from pathlib import Path
from typing import Any, Dict, List, Optional

from PySide6.QtCore import (
    QAbstractListModel,
    QModelIndex,
    QObject,
    QThread,
    QUrl,
    Property,
    Qt,
    Signal,
    Slot,
)

from app.artwork.cover_service import CoverService
from app.importer.csv_import import CsvColumnsMissingError
from app.importer.import_workers import (
    IDENTIFY_SKIPPED,
    IDENTIFY_UNMATCHED,
    AlbumTracksWorker,
    CoverPrefetchWorker,
    FetchAudioWorker,
    ImportAnalyzeWorker,
    ImportSearchWorker,
    ImportSourceWorker,
    ImportStashWorker,
)
from app.library.fields import IDENTITY_FIELD_KEYS
from app.library.identity import identity_value
from app.plugins import external_ids
from app.services.import_service import ImportAnalysis, ImportService
from app.services.track_service import TrackService
from app.settings.imports import ImportSettings
from app.viewmodels.worker_threads import own_worker
from app.library.roles import (
    FIELD_TO_ROLE,
    ROLE_NAMES,
    ROLE_TO_FIELD,
    ROLE_IMPORT_STATE,
    ROLE_IS_SELECTED,
    ROLE_TRACK_ID,
    ROLE_UID,
)

logger = logging.getLogger(__name__)


def _ascending_runs(indices: List[int]) -> List[tuple[int, int]]:
    """Group ascending *indices* into inclusive `(start, end)` runs.

    A model signal names a contiguous span, so anything taking several rows at
    once has to break them into runs first. Two callers want the same grouping
    in opposite orders, which is the whole reason it is a function.
    """
    runs: List[tuple[int, int]] = []
    for index in indices:
        if runs and index == runs[-1][1] + 1:
            runs[-1] = (runs[-1][0], index)
        else:
            runs.append((index, index))
    return runs


def _descending_runs(indices: List[int]) -> List[tuple[int, int]]:
    """The same runs, highest first — the order a *removal* has to take them in.

    Each run's indices are still the right ones as the runs above it go; taken
    the other way round, every removal would shift the ones after it.
    """
    return list(reversed(_ascending_runs(indices)))


# Import results are flat dicts using snake_case keys; catalog field keys are
# camelCase. Anything absent here spells the same in both.
_RESULT_KEY: dict[str, str] = {
    "coverUrl": "cover_url",
    "coverHash": "cover_hash",
    "coverFilePath": "cover_file_path",
    "notFound": "not_found",
    "isDownload": "is_download",
    "sourceFormat": "source_format",
    "stashedDate": "stashed_date",
}

# ----------------------------------------------------------------------
# What a row *is*, named by what the user should do about it.
#
# Six states, and the classifier below is the only place any of them is
# decided. Everything that used to ask its own question of a row — can this be
# saved, is it worth offering the unmatched prompt for, how many are pending,
# which glyph does the cell draw — reads one of these instead, so the answers
# cannot drift apart. That mattered most across the language boundary: the
# status cell used to carry a hand-written copy of `_is_stashable` in QML and
# said so in its own comment.
#
# The strings are the GUI contract (the `importState` role), so they are
# camelCase like every other role value and must not be renamed lightly.

#: In the library already. Nothing left to do.
STATE_IN_LIBRARY = "inLibrary"
#: Wanted, and an enabled audio source will fetch it.
STATE_DOWNLOAD = "download"
#: A local file, ready to file: identified, or — with no identity provider —
#: named well enough by its own tags that there was nothing to identify.
STATE_STASH = "stash"
#: A local file the identity provider matched to nothing, but which names
#: itself. Importable from its own tags — without the album, artwork or release
#: date a match would have brought.
STATE_TAGS_ONLY = "tagsOnly"
#: A local file that matched nothing and has no title or artist either. There is
#: nothing to name a track after, so it cannot be imported until the user pairs
#: it with a search result — which `importFileAs` accepts for exactly this row.
STATE_NEEDS_METADATA = "needsMetadata"
#: A row that would be fetched, and nothing can fetch it: no id any enabled audio
#: source accepts — a CSV row nothing identified, or every audio source off.
STATE_UNAVAILABLE = "unavailable"

#: Every state, in the order a legend should read them: what is done, then what
#: will happen, then what wants attention, then what cannot be helped.
ALL_STATES: tuple[str, ...] = (
    STATE_IN_LIBRARY,
    STATE_DOWNLOAD,
    STATE_STASH,
    STATE_TAGS_ONLY,
    STATE_NEEDS_METADATA,
    STATE_UNAVAILABLE,
)

#: The states an "Import all" would queue. Everything else is finished or stuck.
SAVEABLE_STATES: frozenset[str] = frozenset(
    {STATE_DOWNLOAD, STATE_STASH, STATE_TAGS_ONLY}
)


class ImportViewModel(QAbstractListModel):
    """State + results model for importing local media into the library."""

    # Flow signals
    analysis_ready = Signal()           # probing done; show the options card
    file_type_required = Signal(bool)   # superseded; see `chooseMetadata`
    search_completed = Signal()         # results ready; switch to the results view
    error_occurred = Signal(str)

    # A downloading row could not be saved, and why — no audio source accepts
    # it, or its plugin's terms have not been agreed to. Carries the row uid so
    # the delegate can drop its spinner; -1 means the whole batch was refused,
    # before any row started.
    download_blocked = Signal(int, str)  # uid or -1, reason

    # Rows arrived at the end of the list, and *where*. Neither existing signal
    # answers that: `search_completed` says a run finished but not what it left
    # behind or where, and `importFileAs` raises no `search_completed` at all.
    # A list that accumulates puts new rows below the fold, so a GUI wanting to
    # point at what just landed has nothing else to ask.
    #
    # Emitted for a first batch too, `first` being 0. Whether an arrival onto an
    # empty list is worth pointing out is a question about presentation, and the
    # answer belongs where the pointing happens.
    rows_appended = Signal(int, int)    # first row, how many

    # A row is going off screen because it is in the library and the list is set
    # to show only what still has work in it — it just finished importing, or
    # the user turned the setting on with it already listed. It stays *in* the
    # list; only the model stops exposing it, and turning the setting off puts
    # it back.
    #
    # Emitted *before* the row goes, since afterwards there is no model row left
    # to read a uid off. This is `queueVM.advance_removed` for the import list
    # and exists for the same reason: a row that left under its own steam should
    # not be animated like one that was thrown out. Nothing else answers it —
    # `stash_completed` says a row finished, not that it went anywhere.
    imported_removed = Signal(int)      # uid

    # An album search found tracks the library holds that are *not* on the
    # album it was started from — a song downloaded years ago off a single,
    # which the release has just proved belongs to this record. Carries enough
    # to ask about it by name: the album, and the tracks with their titles.
    #
    # An offer, not a result: nothing is written until `claimTracksForAlbum` is
    # called back. Fired once per album run, so declining is not re-asked when
    # the import it prompted drains and the check runs a second time.
    album_claim_offered = Signal(int, str, "QVariantList", "QVariantList")
    #                            album id, album name, track ids, titles

    # A fetching row reported progress: what it is doing, and how far along
    # (-1 when the source does not say).
    stash_progress = Signal(int, str, float)  # uid, stage, percent

    # Stash lifecycle (carry the per-row uid so the delegate can track its button)
    stash_started = Signal(int)         # uid
    stash_completed = Signal(int, int)  # uid, track_id
    stash_failed = Signal(int, str)     # uid, error message
    stash_finished = Signal(int)        # uid (always last)

    # The selection changed, however it changed. Named to match every other
    # track list, so `TrackTable` picks it up through the connection it already
    # makes rather than through a special case for this model.
    selectionChanged = Signal()

    # Property notifications
    analysis_changed = Signal()
    candidates_changed = Signal()
    stats_changed = Signal()
    result_count_changed = Signal()
    has_results_changed = Signal()
    is_analyzing_changed = Signal()
    is_searching_changed = Signal()
    search_progress_changed = Signal()
    download_mode_changed = Signal()
    stashing_changed = Signal()
    clear_imported_changed = Signal()
    import_options_changed = Signal()

    # Role numbers come from the shared field catalog; QML reads them by name.

    def __init__(
        self,
        import_service: ImportService,
        track_service: TrackService,
        registry=None,
        cover_service: CoverService | None = None,
        parent: QObject | None = None,
        *,
        import_settings: ImportSettings | None = None,
        lyrics_service=None,
        album_service=None,
    ) -> None:
        super().__init__(parent)
        self._import_service = import_service
        self._track_service = track_service
        # The providers: who can identify a file, fetch a wanted track, resolve
        # an import source or list an album. ``None`` is a plain install — no
        # plugins — and every flow here has a path for it.
        self._registry = registry
        self._cover_service = cover_service
        # Where a downloaded track's lyrics are recorded in the library.
        # Optional in the same way `cover_service` is: without it an import
        # still succeeds and the file is still tagged with them — there is just
        # no row, and Now Playing falls back to reading the file.
        self._lyrics_service = lyrics_service
        # Where "this album is the whole record" gets written down after an album
        # search. Optional in the same way `lyrics_service` is: without it every
        # other part of an album import works exactly as before, and the only
        # thing missing is the checkmark.
        self._album_service = album_service
        # Which track ids an enabled audio source can fetch by. Read off the
        # registry once per change rather than per row: `_state` runs for every
        # row on every count, and the answer only moves when a plugin does.
        self._audio_keys: tuple[str, ...] = ()
        self._refresh_audio_keys()
        if registry is not None and hasattr(registry, "changed"):
            registry.changed.connect(self._on_providers_changed)
        # Whether the list keeps rows that are already in the library. Optional
        # because a store-less `ImportSettings` serves the documented default,
        # so a caller with no session — a test, a script — gets the shipped
        # behaviour rather than having to name it.
        self._imports = import_settings or ImportSettings()

        # Whether this run fetches its audio from a source — a CSV, a plugin's
        # import source, an album listing — rather than stashing files off the
        # disk. Set at analyze time, before any results appear.
        #
        # The mode of the *run*, and of nothing else. It is read in exactly two
        # places: to stamp `is_download` on the rows this run brings, and to
        # word this run's own progress line and completion toast. The list it
        # adds to may hold rows of either kind, from this run or an earlier one,
        # and no question about the *list* may be answered from here — ask the
        # rows, as `pendingDownloadCount` and `pendingStashCount` do.
        self._download_mode: bool = False

        # Results model state.
        #
        # `_results` is every row the page has gathered, in arrival order, and
        # nothing is ever taken out of it for being in the library — only the
        # user takes rows out. `_visible` is the ascending subsequence of it the
        # model actually exposes, which is where `clear_imported` acts.
        #
        # A mapping rather than a second list of set-aside rows, so that
        # everything asking a question *about the list* — the state counts a
        # legend draws, the identities a re-imported folder is deduped against,
        # `result_count` — goes on reading `_results` and is right about hidden
        # rows for free. And the import list is not reorderable, so the mapping
        # can only ever be an ascending subsequence: there is no move to keep it
        # in step with.
        self._results: List[Dict[str, Any]] = []
        self._visible: List[int] = []
        self._selected_ids: set[int] = set()
        self._next_uid: int = 0

        # Flow state
        self._analysis: Optional[ImportAnalysis] = None
        self._candidates: List[Any] = []
        self._candidate_type_counts: Dict[str, int] = {}
        self._use_filename_fallback: bool = False
        self._searched_count: int = 0

        # Set only by `findAlbumTracks`: the library album this run is about, and
        # the release listing it came back with. Kept because the completeness check has to
        # run twice off one fetch — once when the listing lands, and again when
        # the import it prompted has finished bringing the missing tracks in.
        # Held rather than re-fetched: the second answer needs no network call,
        # only the tracks that arrived in between.
        self._album_id: int = 0
        self._album_release_rows: List[Dict[str, Any]] = []
        # Whether this run has already offered its claim. The completeness check
        # runs twice off one fetch and would otherwise ask twice — the second
        # time after the user has declined, which is the one answer a question
        # must not overrule.
        self._album_claim_offered: bool = False

        self._is_analyzing: bool = False
        self._is_searching: bool = False
        self._has_results: bool = False
        self._progress_done: int = 0
        self._progress_total: int = 0

        # Stats over the whole list, not one run: rows accumulate across
        # imports, so the header's totals have to as well.
        self._found: int = 0
        self._not_found: int = 0
        self._in_database: int = 0

        # ...and the two figures that *are* about the run that just finished,
        # for the "matched N of M" report. `searchTotal` is already per-run.
        self._last_matched: int = 0
        self._skipped_duplicates: int = 0
        # Matches the run just finished that were in the library already. A
        # different fact from `_in_database`, which is the whole list's: this one
        # is quoted against `searchTotal` in the completion toast, and a
        # cumulative total there would compare two different things.
        self._last_in_database: int = 0
        # Whether the run that just finished identified anything at all. False
        # when no identity provider was enabled and every row was built from
        # its own tags — which is not "matched 0 of 12", and the toast must not
        # say it was.
        self._last_identified: bool = False

        # Worker/thread bookkeeping (kept alive while running).
        #
        # These, not `_is_analyzing`/`_is_searching`, are what say whether a
        # phase is running. Cancelling clears the flags immediately so the tab
        # can return to what the user was looking at, but the worker itself
        # keeps going until the file or match in flight comes back — so the two
        # answers differ for about a second after every cancel, and starting a
        # new run has to consult the slot rather than the flag.
        self._analyze_thread: Optional[QThread] = None
        self._analyze_worker: Optional[ImportAnalyzeWorker] = None
        self._search_thread: Optional[QThread] = None
        self._search_worker: Optional[ImportSearchWorker] = None

        # Set by `cancelImport` for the run in flight, cleared when that run
        # reports done. Covers the race where a worker emits its result a
        # moment before the user cancels: the signal is already queued and will
        # arrive, and this is what says to drop it.
        self._cancelled: bool = False

        # Sequential stash queue
        self._stash_queue: List[int] = []
        self._stash_pending: set[int] = set()
        self._stash_active: bool = False
        self._stashing_uid: int = -1
        # An abort has been asked for and the run has not finished unwinding.
        # Distinct from "not stashing": the row in flight is still working its
        # way to a boundary where it can safely stop.
        self._aborting: bool = False
        self._stash_thread: Optional[QThread] = None
        self._stash_worker: Optional[ImportStashWorker] = None

        # Cover prefetch — one slot, filled while the row before it is written.
        #
        # `_prefetch_uid` names the row the slot is dedicated to, whether its
        # fetch is still running or has already settled, and is deliberately
        # *not* cleared when a fetch comes back empty. Clearing it there would
        # let `_on_prefetch_stopped` re-target the same row immediately, so a
        # row whose artwork 404s in 50ms would spin start/fail/start for the
        # whole of the current stash. It is cleared only where the slot is
        # genuinely free again — see `_invalidate_prefetch`.
        #
        # Only truthy bytes are ever banked here. A prefetch that failed is
        # recorded as no prefetch at all, so the stash refetches on a fresh
        # connection rather than inheriting the failure as "this row has no
        # artwork" — which nothing would retry.
        self._prefetch_thread: Optional[QThread] = None
        self._prefetch_worker: Optional[CoverPrefetchWorker] = None
        self._prefetch_uid: int = -1
        self._prefetch_bytes: Optional[bytes] = None

    # ==================================================================
    # QAbstractListModel overrides
    # ==================================================================

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return len(self._visible)

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or index.row() >= len(self._visible):
            return None
        result = self._results[self._visible[index.row()]]
        if role == ROLE_TRACK_ID:
            return result.get("id")
        if role == ROLE_UID:
            return result.get("uid")
        if role == ROLE_IS_SELECTED:
            return result.get("uid") in self._selected_ids
        # Computed, not stored: a row's state moves under it when the stash
        # lands and gives it an id. Storing it would mean two facts to keep in
        # step; `_on_stash_finished` names this role in `dataChanged` instead.
        if role == ROLE_IMPORT_STATE:
            return self._state(result)
        field = ROLE_TO_FIELD.get(int(role))
        if field is None:
            return None
        if field.key in IDENTITY_FIELD_KEYS:
            return identity_value(field.key, result.get("external_ids"), self._kinds())
        return result.get(_RESULT_KEY.get(field.key, field.key))

    def roleNames(self) -> dict[int, bytes]:
        return ROLE_NAMES

    # Row → identity accessors. QML calls these instead of computing Qt role
    # numbers itself, which keeps the role enum an internal detail: a GUI that
    # hardcoded role integers would break silently when a role is inserted.
    @Slot(int, result="QVariant")
    def uidAt(self, row: int):
        """Per-row instance id at *row* — the key used for selection."""
        return self._role_at(row, ROLE_UID)

    @Slot(int, result="QVariant")
    def trackIdAt(self, row: int):
        """Library track id at *row*, or undefined for a candidate not yet imported."""
        return self._role_at(row, ROLE_TRACK_ID)

    def _role_at(self, row: int, role: int):
        if row < 0 or row >= len(self._visible):
            return None
        return self.data(self.index(row), role)

    # ==================================================================
    # Which rows are on screen
    # ==================================================================
    #
    # `clear_imported` hides rows; it does not discard them. A row that is in
    # the library stays in `_results` for as long as the list does, so turning
    # the setting off brings it back where it was — a list the user has spent a
    # scan and a match run building is not something a settings toggle should
    # be able to destroy half of.
    #
    # What the GUI sees is unchanged by that: hiding is a real `removeRows` and
    # revealing a real `insertRows`, so a row leaving still fades out through
    # the same transition it always did, announced by `imported_removed` first.

    def _is_hidden(self, result: Dict[str, Any]) -> bool:
        """Whether the setting says to keep this row off screen.

        The only statement of the rule, and it asks `_already_done` rather than
        the row, so library membership stays decided in `_state` alone.
        """
        return self._imports.clear_imported and self._already_done(result)

    def _visible_rows(self) -> List[int]:
        """The mapping `_visible` should hold for the list as it stands."""
        return [
            row
            for row, result in enumerate(self._results)
            if not self._is_hidden(result)
        ]

    def _model_row(self, source: int) -> Optional[int]:
        """Model row for the row at *source* in `_results`, or None if hidden."""
        found = bisect_left(self._visible, source)
        if found < len(self._visible) and self._visible[found] == source:
            return found
        return None

    def _resync_visible(self) -> None:
        """Bring `_visible` in line with the setting, as removals and inserts.

        The one place a row changes between shown and hidden, whichever way it
        got there: the setting was flipped, or a row finished stashing and is
        now in the library. Both want the same two things — the departures named
        before they go, and each change bracketed so the view animates it.

        Every departure is announced, because every one of them is a row leaving
        under its own steam: nothing else can hide a row. The GUI needs the uid
        while there is still a model row to read it off, and it is what tells
        this departure from a row the user threw off the list.

        Removals run descending so each run's indices are still the ones it was
        computed at as the runs above it go; insertions run ascending, which is
        what makes a target position also the model row it lands at — everything
        before it is in place by the time it is inserted.
        """
        target = self._visible_rows()
        if target == self._visible:
            return

        wanted = set(target)
        going = [
            model_row
            for model_row, source in enumerate(self._visible)
            if source not in wanted
        ]
        if going:
            for model_row in going:
                uid = self._results[self._visible[model_row]].get("uid")
                if uid is not None:
                    self.imported_removed.emit(int(uid))
            for start, end in _descending_runs(going):
                self.beginRemoveRows(QModelIndex(), start, end)
                del self._visible[start:end + 1]
                self.endRemoveRows()

        # What is left is a subsequence of the target, so every row still to
        # come is an insert.
        present = set(self._visible)
        arriving = [
            model_row
            for model_row, source in enumerate(target)
            if source not in present
        ]
        for start, end in _ascending_runs(arriving):
            self.beginInsertRows(QModelIndex(), start, end)
            self._visible[start:start] = target[start:end + 1]
            self.endInsertRows()

    # ==================================================================
    # Properties
    # ==================================================================

    @Property(bool, notify=download_mode_changed)
    def isDownloadMode(self) -> bool:
        """True while the run in flight is fetching its audio rather than filing files.

        About the **run**, not the list — the list can hold both kinds at once.
        What it is for is wording that run: "Fetching" beside its progress, and
        "matched 3 of 8 tracks" rather than "files" when it finishes. A GUI asking what the *list* is made of wants
        `pendingDownloadCount` and `pendingStashCount` instead.
        """
        return self._download_mode

    @Property(int, notify=result_count_changed)
    def result_count(self) -> int:
        return len(self._results)

    @Property(bool, notify=has_results_changed)
    def hasResults(self) -> bool:
        return self._has_results

    @Property(bool, notify=is_analyzing_changed)
    def isAnalyzing(self) -> bool:
        return self._is_analyzing

    @Property(bool, notify=is_searching_changed)
    def isSearching(self) -> bool:
        return self._is_searching

    # ---- Analysis counts (the options card) --------------------------

    @Property(int, notify=analysis_changed)
    def totalFiles(self) -> int:
        return self._analysis.total if self._analysis else 0

    @Property(int, notify=analysis_changed)
    def withExternalId(self) -> int:  # noqa: N802
        return self._analysis.with_external_id if self._analysis else 0

    @Property(int, notify=analysis_changed)
    def withTitleArtist(self) -> int:
        return self._analysis.with_title_artist if self._analysis else 0

    @Property(int, notify=analysis_changed)
    def withoutMetadata(self) -> int:
        return self._analysis.without_metadata if self._analysis else 0

    # ---- Remembered import options -----------------------------------
    #
    # Read-only on purpose. `beginImport` is the only writer, which keeps "the
    # user pressed Import" and "the preference changed" one event rather than
    # two that can disagree — a card that wrote as its controls moved would
    # remember the settings of an import that was then cancelled.

    @Property(bool, notify=import_options_changed)
    def filenameFallback(self) -> bool:
        """What the options card should open with, for the fallback switch."""
        return self._imports.filename_fallback

    @Property("QVariantList", notify=import_options_changed)
    def excludedFormats(self) -> List[str]:
        """Container names the card should open with unticked."""
        return sorted(self._imports.excluded_formats)

    # ---- Candidate file-type makeup ----------------------------------

    @Property(int, notify=candidates_changed)
    def candidateCount(self) -> int:
        return len(self._candidates)

    @Property(int, notify=candidates_changed)
    def nonOpusCount(self) -> int:
        return sum(
            count for ext, count in self._candidate_type_counts.items() if ext != "opus"
        )

    # @Property(int, notify=candidates_changed)
    # def opusCount(self) -> int:
    #     return self._candidate_type_counts.get("opus", 0)

    @Property(str, notify=candidates_changed)
    def fileTypeSummary(self) -> str:
        """Human-readable makeup, e.g. ``"12 opus, 3 mp3, 1 flac"``."""
        parts = [f"{count} {ext}" for ext, count in self._candidate_type_counts.items()]
        return ", ".join(parts)

    # ---- Search stats (results header) -------------------------------
    #
    # These four count the whole list. Imports accumulate, so a header that
    # reported only the last run would understate what the "Import all" button
    # is about to do — which is the number the user is deciding on.

    @Property(int, notify=stats_changed)
    def foundCount(self) -> int:
        return self._found

    @Property(int, notify=stats_changed)
    def notFoundCount(self) -> int:
        return self._not_found

    @Property(int, notify=stats_changed)
    def inDatabaseCount(self) -> int:
        """Rows in the list the library already has, shown or hidden alike.

        True whichever way `clearImported` is set, because the setting decides
        what is on screen and this is about the list. It is what an "in library"
        chip counts: the number is most worth saying when the rows are hidden,
        which is exactly when nothing else on the page could say it.
        """
        return self._in_database

    @Property(int, notify=stats_changed)
    def lastMatchedCount(self) -> int:
        """Matches found by the run that just finished, not by the list.

        Separate from `foundCount` because the completion toast reports on a
        run — "matched 8 of 12 files" — and reads its other half from
        `searchTotal`, which has always been per-run. Quoting the cumulative
        total against a per-run total would compare two different things and
        could read as more matches than files.
        """
        return self._last_matched

    @Property(int, notify=stats_changed)
    def duplicatesSkipped(self) -> int:
        """Files the run just finished left out because they were already listed.

        Worth saying out loud: a folder re-imported by mistake otherwise looks
        identical to one that matched nothing, and the user has no way to tell
        which happened from a list that simply did not grow.
        """
        return self._skipped_duplicates

    @Property(bool, notify=stats_changed)
    def lastRunIdentified(self) -> bool:  # noqa: N802
        """Whether the run that just finished asked an identity provider anything.

        False when none was enabled — every row came from its own tags, nothing
        was matched or failed to match, and a toast quoting "matched 0 of 12"
        would describe a step that never ran.
        """
        return self._last_identified

    @Property(int, notify=stats_changed)
    def lastInDatabaseCount(self) -> int:
        """Matches the run just finished that were in the library already.

        The other half of `duplicatesSkipped`, and a different fact: that one is
        about the *list* — a folder dragged in twice — and this one is about the
        *library*. A user who re-imports a folder they already took in wants to
        be told it was already there, not left to infer it from a list that grew
        by less than the file count.

        Per-run, like `lastMatchedCount`, so that the toast can quote it against
        `searchTotal` — "2 of 12 were already in your library" is about the run
        the user just watched. The cumulative figure is `inDatabaseCount`, and
        both are worth having: one reports a run, the other describes the list.
        """
        return self._last_in_database

    @Property(bool, notify=clear_imported_changed)
    def clearImported(self) -> bool:
        """Whether the list *shows* only rows that still have work in them.

        On by default. A row that is in the library has nothing to press and
        nothing to choose, and forty of them at the end of a forty-file import
        are forty rows to read past.

        It hides rather than discards. The rows stay in the list, so the setting
        is reversible over a list already gathered — a scan and a match run are
        expensive enough that a switch should not be able to throw half of one
        away — and so the count they carry stays askable through
        `inDatabaseCount` and `stateCounts` while they are off screen.
        """
        return self._imports.clear_imported

    @clearImported.setter
    def clearImported(self, value: bool) -> None:
        value = bool(value)
        if value == self._imports.clear_imported:
            return
        self._imports.set_clear_imported(value)
        # Read back rather than assumed. With no session behind it the write is
        # a no-op, and announcing a change that did not happen would leave the
        # switch's knob and the setting disagreeing.
        if self._imports.clear_imported != value:
            return
        # Both ways. Turned on over a list already holding finished rows it has
        # to mean what it says now rather than only from the next import
        # onwards; turned off, it has to give those rows back, which is the
        # whole reason they were kept.
        self._resync_visible()
        self.clear_imported_changed.emit()

    @Property(bool, notify=stashing_changed)
    def isStashing(self) -> bool:
        """Whether an import run is under way — a row being written, or one waiting.

        Both halves matter. Between one row finishing and the next starting
        there is an event loop turn with no worker at all, and a button that
        flicked back to "Import all" across that gap would be wrong and
        clickable in the same instant.
        """
        return self._stash_active or bool(self._stash_queue)

    @Property(bool, notify=stashing_changed)
    def isAborting(self) -> bool:
        """Whether an abort has been asked for but the run has not stopped yet.

        There is always a gap: the row in flight only stops at its next safe
        boundary, which can be a transcode away. The button says so rather than
        going quiet, because a control that appears to do nothing is precisely
        the complaint that started this.
        """
        return self._aborting

    @Property(int, notify=stashing_changed)
    def stashingUid(self) -> int:
        """The row being saved right now, or -1 when none is.

        A property, and not just the `stash_started` / `stash_finished` pair it
        duplicates, because of what draws the spinner. The status cell lives in
        a list delegate, and a delegate is destroyed once it scrolls past the
        cache buffer and built again on the way back — so a row that latched "I
        am working" when the signal arrived loses that with the delegate, and
        comes back showing a button while it is genuinely still importing.
        A row that reads its state from here cannot fall out of step, however
        far the user scrolls during a long import.
        """
        return self._stashing_uid

    def _count_states(self, *states: str) -> int:
        return sum(1 for result in self._results if self._state(result) in states)

    @Property(int, notify=stats_changed)
    def unmatchedImportableCount(self) -> int:
        """Unmatched rows that an "Import all" would still bring in.

        Deliberately not `notFoundCount`. A file that matched nothing *and* has
        no title or artist of its own cannot be imported at all — there would be
        nothing to name the track — so counting it here would put a number in
        front of the user that no answer they give changes. This is the number
        the choice actually decides, which is the only honest thing to quote.

        Which is exactly the line `tagsOnly` and `needsMetadata` are drawn on, so
        this is that count and not a second spelling of it.
        """
        return self._count_states(STATE_TAGS_ONLY)

    # The list's own makeup, which is what a header speaks for now that a list
    # can hold both kinds. Counted off the states rather than re-asking the
    # rows, so a button labelled from these cannot promise work the press would
    # not do — `stashAll` queues exactly `SAVEABLE_STATES`.
    #
    # `stats_changed` carries all of these, and it already fires wherever the
    # list or a row's `id` moves: a merge, a removal, a row finishing its stash.

    @Property(int, notify=stats_changed)
    def pendingDownloadCount(self) -> int:
        """Rows still to be fetched from an audio source."""
        return self._count_states(STATE_DOWNLOAD)

    @Property(int, notify=stats_changed)
    def pendingStashCount(self) -> int:
        """Rows still to be filed from a file already on the machine."""
        return self._count_states(STATE_STASH, STATE_TAGS_ONLY)

    @Property("QVariantMap", notify=stats_changed)
    def stateCounts(self) -> Dict[str, int]:
        """How many rows are in each state, for a legend to name the list with.

        Every state is present as a key, zero included: a GUI deciding which
        chips to show wants to ask this map rather than discover that a state it
        has never seen is simply missing. Values are what
        `uidsInState` would return the length of.
        """
        counts = {state: 0 for state in ALL_STATES}
        for result in self._results:
            counts[self._state(result)] += 1
        return counts

    @Slot(str, result="QVariantList")
    def uidsInState(self, state: str) -> List[int]:
        """The uids of every row in *state*, in list order.

        What makes a legend actionable: hand these to `setSelectedMultiple` and
        the whole of a state is selected, which is how "remove the four rows
        that will never import" becomes one gesture rather than four.
        """
        return [
            result["uid"]
            for result in self._results
            if self._state(result) == state and result.get("uid") is not None
        ]

    @Property(int, notify=stats_changed)
    def removableSelectedCount(self) -> int:
        """How many of the selected rows "Remove from list" would actually take.

        The selection, less the row being written right now — see
        `_removable_selected_rows`. Quoted rather than the plain selection size
        for the same reason `unmatchedImportableCount` above is: an item reading
        "Remove 2 from list" over three highlighted rows is a number the user
        cannot act on, and the honest one costs nothing to work out.
        """
        return len(self._removable_selected_rows())

    @Property(int, notify=search_progress_changed)
    def searchProgress(self) -> int:
        return self._progress_done

    @Property(int, notify=search_progress_changed)
    def searchTotal(self) -> int:
        return self._progress_total

    # ==================================================================
    # Flow — step 1: analyze
    # ==================================================================

    @Slot("QVariantList")
    def analyzePaths(self, urls: list) -> None:
        """Begin importing the given files/folders (QUrl or path strings)."""
        if not self._can_start():
            return
        paths = self._to_local_paths(urls)
        if not paths:
            self.error_occurred.emit("No files or folders were selected.")
            return

        self._reset_flow_state()
        self._set_download_mode(False)
        self._set_is_analyzing(True)

        thread = QThread(self)
        worker = ImportAnalyzeWorker(
            paths, self._import_service, self._probe_kinds(),
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_analyze_finished)
        worker.failed.connect(self._on_analyze_failed)
        own_worker(thread, worker, self._on_analyze_stopped)
        self._analyze_thread = thread
        self._analyze_worker = worker
        thread.start()

    # Both arguments are QVariant rather than the tighter QUrl/QVariantMap. The
    # pairing this exists for is `importFileAs(file, searchVM.webResult(uid))`,
    # and `webResult` answers null for a uid it does not know — which against a
    # QVariantMap parameter is a QML TypeError rather than something this can
    # answer. Taken loosely it is one more empty dict, and the row still gets
    # the file the user picked.
    @Slot("QVariant", "QVariant")
    def importFileAs(self, url, metadata: Any) -> None:  # noqa: N802
        """Queue one local file for import under someone else's metadata.

        The ordinary import works out which track a file is by asking the
        identity provider about whatever its tags say — which is exactly what
        fails for a file with no tags and a filename like ``track03.mp3``. This
        is the same import with that step removed: the caller has already
        identified the track, so the row is built from *metadata* and marked
        matched. Nothing downstream changes, because ``stash_imported_track``
        consumes these same fields whether they arrived from an identify run or
        from here.

        *metadata* is an online search result — see ``SearchViewModel.webResult``
        — so it is a core row, read by the snake_case keys
        :func:`app.plugins.providers.row_from_protocol` produces. Anything
        missing degrades to empty rather than failing: the file is the part that
        has to be real.

        The row is appended, whatever the list already holds — several results
        paired with several files, a CSV batch, or both. The one time it is
        *not* appended is when the file is already listed as a row that matched
        nothing: that row is what this call answers, so it is filled in rather
        than duplicated. See ``_upgrade_unmatched``.
        """
        if not self._can_start():
            return
        paths = self._to_local_paths([url])
        if not paths:
            self.error_occurred.emit("No file was selected.")
            return
        if not isinstance(metadata, dict):
            metadata = {}

        ids = self._clean_ids(metadata.get("external_ids"))
        # Asked once, and asked the wide way — the same rule an identify run's
        # rows get, so a result the user paired by hand is recognised as owned
        # on the same terms as one the list found for itself.
        owned = self._import_service.owned_track_ids([{**metadata, "external_ids": ids}])[0]

        # Already in the library, and the list is set to show no such row: say so
        # rather than appending one that lands straight off screen. Pairing a
        # file with a result by hand is the most deliberate act on this page, and
        # the one thing it must not do is look like it did nothing — which a
        # silently hidden row would, more completely than a vanishing one did.
        # With the setting off the row is appended as before, wearing its
        # checkmark.
        if self._imports.clear_imported and owned is not None:
            self.error_occurred.emit("That track is already in your library.")
            return

        # See `_identities`. Two mistakes, and they are worth telling apart
        # because the user's next move differs: pairing the same *file* twice
        # would stash one piece of audio into the library under two names,
        # while pairing a result whose *track* is already listed — as a CSV row
        # is, or as another file's match — would import it twice over.
        #
        # The track check comes first and applies to an upgrade as well: filling
        # in one row with a track another row already holds is the same
        # duplicate by a longer route.
        listed = self._listed_identities()
        if self._identities("", ids) & listed:
            self.error_occurred.emit("That track is already in the import list.")
            return
        if self._identities(paths[0], {}) & listed:
            if self._upgrade_unmatched(paths[0], metadata, ids):
                return
            self.error_occurred.emit("That file is already in the import list.")
            return

        result: Dict[str, Any] = {
            **self._paired_fields(metadata, ids),
            "source_path": paths[0],
            # The user asserted this match, so the row must not wear the amber
            # unmatched marking or be swept up by the "import the files that
            # matched nothing?" question. It is the most certain row in the list.
            "identify": "matched",
            "not_found": False,
            "cover_hash": None,
            # This row stashes the file it was given; it never downloads.
            "is_download": False,
            "uid": self._next_uid,
            "id": owned,
            "source_format": self._source_format(paths[0]),
        }
        self._next_uid += 1

        # An insert rather than a reset: a list the user has been building keeps
        # its selection and its scroll position.
        source = len(self._results)
        self._results.append(result)
        if not self._is_hidden(result):
            row = len(self._visible)
            self.beginInsertRows(QModelIndex(), row, row)
            self._visible.append(source)
            self.endInsertRows()
            self.rows_appended.emit(row, 1)

        self._found += 1
        if result["id"] is not None:
            self._in_database += 1
        self._set_has_results(True)
        self.stats_changed.emit()
        self.result_count_changed.emit()
        # Deliberately not `search_completed`: that signal is an identify run's
        # report — "matched N of M files" — and no run happened here. The
        # results panel is bound to `hasResults`, which is what put it on screen.

    @staticmethod
    def _paired_fields(metadata: Dict[str, Any], ids: Dict[str, str]) -> Dict[str, Any]:
        """The fields a hand-paired result contributes to a row."""
        return {
            "title": str(metadata.get("title") or ""),
            "artist": str(metadata.get("artist") or ""),
            "album": str(metadata.get("album") or ""),
            "duration": int(metadata.get("duration") or 0),
            "release_date": metadata.get("release_date"),
            "external_ids": dict(ids),
            "alternate_ids": dict(metadata.get("alternate_ids") or {}),
            "artwork_urls": [str(u) for u in (metadata.get("artwork_urls") or []) if u],
            "cover_url": str(metadata.get("cover_url") or ""),
            "artist_refs": list(metadata.get("artist_refs") or []),
            "album_ref": dict(metadata.get("album_ref") or {}),
            "provider": str(metadata.get("provider") or ""),
        }

    @staticmethod
    def _clean_ids(raw: Any) -> Dict[str, str]:
        """A ``{"provider:kind": value}`` map from QML, with the empties dropped."""
        if not isinstance(raw, dict):
            return {}
        return {str(k): str(v) for k, v in raw.items() if k and v}

    def _upgrade_unmatched(
        self, source_path: str, metadata: Dict[str, Any], ids: Dict[str, str]
    ) -> bool:
        """Fill in a listed row that matched nothing. True if one was found.

        A `needsMetadata` row's whole instruction to the user is "pair this file
        with a search result", and pairing is `importFileAs` — which, taken as a
        plain append, refused it for being a file already in the list. The
        guidance was a dead end, and a control that silently does nothing is
        worse than no control.

        So the pairing lands on the row that asked for it. The file is the same
        file; what changes is that it is now identified. `tagsOnly` is upgraded
        on the same path, which is the other half of the offer — a row importing
        from its own tags can be given the album, artwork and date it was
        missing instead of settling for them being absent. So is a row whose
        identify step was *skipped*: there was nothing to identify it with then,
        and the user has just done it by hand.

        A row that already matched is *not* upgraded. That is the genuine
        duplicate the check exists for: pairing a second result onto a file
        that is already spoken for would quietly overwrite the first answer.
        """
        wanted = self._identities(source_path, {})
        for row, result in enumerate(self._results):
            if result.get("identify") not in (IDENTIFY_UNMATCHED, IDENTIFY_SKIPPED) \
                    and not result.get("not_found"):
                continue
            if result.get("is_download"):
                continue
            if not (wanted & self._identities(str(result.get("source_path") or ""), {})):
                continue

            # Counted the way `_on_search_finished` counts an arriving row: this
            # one crosses from the not-found tally to the found one.
            was_unmatched = bool(result.get("not_found"))
            result.update({
                **self._paired_fields(metadata, ids),
                "identify": "matched",
                "not_found": False,
                # The same wide question the append path asks — the pairing may
                # have identified a recording the library already holds under a
                # different release's id.
                "id": self._import_service.owned_track_ids(
                    [{**metadata, "external_ids": ids}]
                )[0],
            })
            # The local-art fallback belonged to the unmatched row and would now
            # sit in front of the artwork the match brought.
            result.pop("cover_file_path", None)

            if was_unmatched:
                self._not_found -= 1
                self._found += 1
            if result["id"] is not None:
                self._in_database += 1

            model_row = self._model_row(row)
            if model_row is not None:
                index = self.index(model_row)
                self.dataChanged.emit(index, index)
            # The pairing can have made it a row the library already has, and
            # the setting may then want it off screen. Announced and animated
            # like any other departure — it is the same fact arriving by a
            # different door.
            self._resync_visible()
            self.stats_changed.emit()
            return True
        return False

    @Slot(object)
    def _on_analyze_finished(self, analysis: ImportAnalysis) -> None:
        if self._cancelled:
            return
        self._analysis = analysis
        self.analysis_changed.emit()
        if analysis.total == 0:
            self.error_occurred.emit("No supported audio files were found.")
            return
        self.analysis_ready.emit()

    @Slot(str)
    def _on_analyze_failed(self, message: str) -> None:
        if self._cancelled:
            return
        self.error_occurred.emit(f"Analysis failed: {message}")

    @Slot()
    def _on_analyze_stopped(self) -> None:
        self._analyze_thread = None
        self._analyze_worker = None
        self._clear_cancelled()
        self._set_is_analyzing(False)

    # ==================================================================
    # Flow — CSV import (parse synchronously, then identify → fetch)
    # ==================================================================

    @Slot(QUrl)
    @Slot(str)
    def analyzeCsv(self, url) -> None:
        """Import tracks listed in a CSV file.

        Parsing is fast and synchronous so a malformed/empty file surfaces an
        error popup immediately. Each row is then identified by the same worker
        the local import uses, and *fetched* (not stashed) by whichever enabled
        audio source accepts it, with the CSV metadata applied over what the
        source supplies.

        A CSV is a list of wanted tracks and nothing else, so with no audio
        source enabled there is nothing it could do — the GUI does not offer it,
        and a call that arrives anyway is refused with the reason.
        """
        if not self._can_start():
            return
        paths = self._to_local_paths([url])
        if not paths:
            self.error_occurred.emit("No CSV file was selected.")
            return

        if not self._has_audio_source():
            self.error_occurred.emit(
                "No enabled plugin can fetch audio, so there is nothing a CSV can import."
            )
            return

        self._reset_flow_state()
        self._set_download_mode(True)

        try:
            files = self._import_service.parse_csv(paths[0], self._probe_kinds())
        except CsvColumnsMissingError as exc:
            self.error_occurred.emit(str(exc))
            return
        except Exception as exc:  # noqa: BLE001
            logger.exception("CSV parse failed")
            self.error_occurred.emit(f"Could not read CSV: {exc}")
            return

        if not files:
            self.error_occurred.emit("The CSV file contained no usable rows.")
            return

        self._candidates = files
        self._use_filename_fallback = False
        self._start_search(files)

    # ==================================================================
    # Flow — a plugin's import source (resolve → fetch)
    # ==================================================================

    @Slot(str, str)
    def importFromSource(self, source_key: str, text: str) -> None:  # noqa: N802
        """Add the rows a plugin's import source resolves *text* to.

        *source_key* is one of ``pluginsVM.importSources``' keys; *text* is what
        the user typed, or the path they picked, for it. What the input means —
        a link, an id, a file of some format — is the plugin's to know, and the
        rows it answers with arrive ready to fetch.

        Deliberately **not** routed through `_start_search`, for the reason a
        release listing is not: the plugin has already returned every field an
        identify would find, so the rows go straight to the merge — through the
        same path, and the same two slots, an album run uses.
        """
        if not self._can_start():
            return
        found = self._registry.import_source(str(source_key or "")) if self._registry else None
        if found is None:
            self.error_occurred.emit("That import source is not available.")
            return
        cleaned = str(text or "").strip()
        if getattr(found[1], "input", "") == "file" and cleaned.startswith("file:"):
            # A file source is answered with a picked file, which QML hands over
            # as a URL. The plugin is promised a path.
            cleaned = QUrl(cleaned).toLocalFile()
        if not cleaned:
            self.error_occurred.emit("Nothing to import.")
            return
        provider, source = found

        self._reset_flow_state()
        self._set_download_mode(True)
        self._set_is_searching(True)
        self._progress_done = 0
        self._progress_total = 1
        self.search_progress_changed.emit()

        thread = QThread(self)
        worker = ImportSourceWorker(provider, source, cleaned)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.counted.connect(self._on_album_counted)
        worker.finished.connect(self._on_search_finished)
        worker.failed.connect(self._on_search_failed)
        own_worker(thread, worker, self._on_search_stopped)
        self._search_thread = thread
        self._search_worker = worker
        thread.start()

    # ==================================================================
    # Flow — one whole album (read the release → fetch what is missing)
    # ==================================================================

    @Slot(int)
    def findAlbumTracks(self, album_id: int) -> None:  # noqa: N802
        """List every track on the release a library album is identified as.

        The answer to "what am I missing off this record" — the album knows
        which release it is, and the release knows its own track list, so the
        whole thing can be listed at once and the rows already in the library
        marked as such. Which of them are *shown* is `clear_imported`'s
        business, exactly as for any other source: a row it hides still holds
        the count the legend draws.

        Keyed by the library album, not by an id: the album's external ids are
        read here, and its completeness and claim are decided against the very
        row the user started from.

        Deliberately **not** routed through `_start_search`. That spawns the
        identify worker, which asks one question per candidate to work out what
        a file is — and here nothing needs working out. One listing has already
        returned every field an identify would have found, so
        `AlbumTracksWorker` hands the rows straight to `_on_search_finished` and
        a twelve-track album costs one round trip rather than a dozen.

        `_without_listed` is skipped for the same reason it can be: it compares
        candidates by source path and external id, and these rows have no path.
        The id half of that comparison is `_without_matched`, which
        `_on_search_finished` runs anyway — so pressing the button twice reports
        the album as already listed instead of listing it twice.
        """
        if not self._can_start():
            return
        album = self._album_service.get(int(album_id or 0)) if self._album_service else None
        if album is None:
            self.error_occurred.emit("That album is no longer in the library.")
            return
        ids = external_ids.read(external_ids.ALBUM, album)
        if not external_ids.is_identified(external_ids.ALBUM, ids):
            self.error_occurred.emit("This album is not identified, so there is no release to read.")
            return
        if self._registry is None or self._registry.album_tracks() is None:
            self.error_occurred.emit("No enabled plugin can list an album's tracks.")
            return

        self._reset_flow_state()
        self._set_download_mode(True)
        # `_reset_flow_state` has already emptied `_candidates` and zeroed the
        # run's counters. Nothing else needs setting: that list holds
        # `ProbedFile`s for the identify worker, and there is no such run here.
        self._start_album_search(int(album.id), ids)

    def _start_album_search(self, album_id: int, ids: Dict[str, str]) -> None:
        """Spawn the track-list worker. The album counterpart of `_start_search`."""
        # `_reset_flow_state` has just cleared this; the run owns it from here.
        self._album_id = int(album_id)
        self._set_is_searching(True)
        self._progress_done = 0
        # A denominator for the second before the fetch lands; `counted` replaces
        # it with the real one the moment the release says how long it is.
        self._progress_total = 1
        self.search_progress_changed.emit()

        thread = QThread(self)
        worker = AlbumTracksWorker(ids, self._registry)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.counted.connect(self._on_album_counted)
        worker.finished.connect(self._on_search_finished)
        worker.failed.connect(self._on_search_failed)
        own_worker(thread, worker, self._on_search_stopped)
        # The same two slots the identify run uses, so `cancelImport`,
        # `_busy_message` and `_on_search_stopped` need no idea this happened.
        self._search_thread = thread
        self._search_worker = worker
        thread.start()

    @Slot(int)
    def _on_album_counted(self, total: int) -> None:
        """How many rows the release — or the import source — turned out to have.

        The figure the completion toast quotes against `lastMatchedCount`, and
        the only honest denominator for this run: a scan knows how many files it
        is about to identify, and a listing does not until it has been read.
        """
        if self._cancelled:
            return
        self._searched_count = int(total)
        self._progress_done = 0
        self._progress_total = int(total)
        self.search_progress_changed.emit()

    # ==================================================================
    # Flow — step 2: the import options
    # ==================================================================

    @Slot(bool, "QVariantList", result="QVariantMap")
    def importPreview(
        self, use_filename_fallback: bool, excluded_formats: list
    ) -> Dict[str, Any]:
        """What the scan is made of, for one setting of the options card.

        Pure: it answers a question about the analysis and changes nothing, so
        the card can call it on every tick of a checkbox. Empty before a scan
        has landed, which is the honest answer and the one a card binding to it
        can draw as nothing.

        Every count it returns already accounts for both options, so the card
        adds nothing up itself. A card that summed its own totals would be a
        second statement of the selection rule, free to disagree with the run
        the Import button starts — the same reason ``stashAll`` publishes
        ``importState`` rather than leaving a GUI to re-derive it.
        """
        if self._analysis is None:
            return {}
        return self._analysis.makeup(
            bool(use_filename_fallback), [str(ext) for ext in (excluded_formats or [])]
        )

    @Slot(bool, "QVariantList")
    def beginImport(
        self, use_filename_fallback: bool, excluded_formats: list
    ) -> None:
        """Take the options card's answer and start the match run.

        The one commit point for a local import: it is where the choices become
        a candidate set *and* where they become remembered, because those are
        the same act. Recording them as the controls moved instead would leave
        a cancelled import having quietly changed the next one's defaults.
        """
        if self._analysis is None:
            return

        use_filename_fallback = bool(use_filename_fallback)
        excluded = [str(ext) for ext in (excluded_formats or [])]

        self._imports.set_import_options(use_filename_fallback, excluded)
        self.import_options_changed.emit()

        self._use_filename_fallback = use_filename_fallback
        self._candidates = self._analysis.selection(use_filename_fallback, excluded)
        self._candidate_type_counts = ImportAnalysis.file_type_counts(self._candidates)
        self.candidates_changed.emit()

        self._start_search(self._candidates)

    # ------------------------------------------------------------------
    # The two menus the options card replaced.
    #
    # Kept working rather than removed: both are part of GUI API 2, and a GUI
    # written against it is entitled to go on asking its two questions. They run
    # through `ImportAnalysis.selection` like everything else, so there is still
    # one statement of what a candidate is — two doors, one rule.
    # ------------------------------------------------------------------

    @Slot(bool)
    def chooseMetadata(self, use_filename_fallback: bool) -> None:
        """Apply the metadata-menu choice and decide whether menu 2 is needed."""
        if self._analysis is None:
            return
        self._use_filename_fallback = use_filename_fallback
        self._candidates = self._analysis.selection(use_filename_fallback)
        self._candidate_type_counts = ImportAnalysis.file_type_counts(self._candidates)
        self.candidates_changed.emit()
        self.file_type_required.emit(self.nonOpusCount > 0) #type: ignore (treated as bool at runtime)

    @Slot()
    @Slot(bool)
    def beginSearch(self, opus_only: bool = False) -> None:
        """Identify the (filtered) candidate files off the UI thread."""
        if self._analysis is None:
            return

        files = self._candidates
        if opus_only:
            files = [f for f in files if f.is_opus]

        self._start_search(files)

    def _start_search(self, files: List[Any]) -> None:
        """Spawn the identify worker over *files* (shared by local + CSV)."""
        if self._search_thread is not None:
            # The same guard as `_can_start`, one step further in: this is the
            # path that arrives through the import menus rather than through a
            # file dialog.
            self.error_occurred.emit(self._busy_message())
            return
        if not files:
            self.error_occurred.emit("No files match the selected import options.")
            return

        files, skipped = self._without_listed(files)
        if skipped and not files:
            # Said plainly rather than through the generic "nothing matched the
            # options" below, which would send the user back to a file-type menu
            # that has nothing to do with it.
            self._skipped_duplicates = skipped
            self._last_matched = 0
            self._last_in_database = 0
            self.stats_changed.emit()
            self.error_occurred.emit(
                "Every file selected is already in the import list."
            )
            return
        self._skipped_duplicates = skipped
        self._last_matched = 0
        self._last_in_database = 0
        self.stats_changed.emit()

        self._searched_count = len(files)
        self._set_is_searching(True)
        self._progress_done = 0
        self._progress_total = len(files)
        self.search_progress_changed.emit()
        self._last_identified = self._has_identity()

        thread = QThread(self)
        worker = ImportSearchWorker(files, self._use_filename_fallback, self._registry)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.progress.connect(self._on_search_progress)
        worker.finished.connect(self._on_search_finished)
        worker.failed.connect(self._on_search_failed)
        own_worker(thread, worker, self._on_search_stopped)
        self._search_thread = thread
        self._search_worker = worker
        thread.start()

    @Slot(int, int)
    def _on_search_progress(self, done: int, total: int) -> None:
        if self._cancelled:
            return
        self._progress_done = done
        self._progress_total = total
        self.search_progress_changed.emit()

    @Slot(list)
    def _on_search_finished(self, results: List[Dict[str, Any]]) -> None:
        """Merge a finished match run into the list.

        Appended, not assigned, and appended whatever kind the list already
        holds. A user gathering an import from several folders — or pairing
        files one at a time through ``importFileAs`` between scans, or adding a
        CSV to a folder — is building one list, and a run that replaced it would
        throw away everything decided before it without saying so.

        Repeats are filtered in two passes, because the two questions become
        answerable at different times. A file is known before the run and is
        skipped in ``_start_search``, where skipping is free. The *track* a file
        turns out to be is only known here, which is what ``_without_matched``
        catches: a CSV row and a local file can name the same recording without
        looking alike until the identify comes back.
        """
        if self._cancelled:
            return
        # Taken before anything filters it, and this is not fussiness. The two
        # passes below drop rows — `_identifiable` the ones that say nothing,
        # `_without_matched` the ones already on the list — so pressing "Find
        # tracks" a second time leaves `listed` empty while the release is
        # exactly as long as it ever was. The run's counters are about the run;
        # completeness is about the *release*, and only the untouched list can
        # answer that.
        if self._album_id:
            self._album_release_rows = list(results)
        results = self._identifiable(results)
        results, matched_dupes = self._without_matched(results)
        self._skipped_duplicates += matched_dupes
        # Flag matches already present in the library.
        #
        # Asked as "is this recording already here", not "is this id already
        # here" — see `ImportService.owned_track_ids`. A row is recognised under
        # any of the ids that name it, and failing that by its title, length and
        # credit, which is what finds a track the library downloaded from a
        # different release of the same record. Three queries for the whole run,
        # whatever its length.
        already_owned = self._import_service.owned_track_ids(results)

        # Which of these the library already has. Every one of them is listed,
        # whatever the setting says: `clear_imported` decides what is *shown*,
        # and a row it hides is still a row — it holds the count the chip draws,
        # it is what the setting has to give back when it is turned off, and it
        # is what stops the same folder being added twice over.
        #
        # Counted here as well because this figure is about the run rather than
        # the list, and the toast quotes it against `searchTotal` — see
        # `lastInDatabaseCount`.
        in_database = 0
        listed: List[Dict[str, Any]] = results
        for result, track_id in zip(listed, already_owned):
            result["id"] = track_id
            if track_id is not None:
                in_database += 1
        self._last_in_database = in_database

        not_found_count = 0
        for result in listed:
            result["uid"] = self._next_uid
            self._next_uid += 1
            result["cover_hash"] = None
            # Stamped once, like `cover_file_path` below: a row's file never
            # changes under it, so this cannot go stale, and a scrolling list
            # should not rebuild a Path per repaint to answer it.
            result["source_format"] = self._source_format(
                str(result.get("source_path") or "")
            )
            # What this row does when saved, settled here and never re-read off
            # the view model: a row outlives the run that made it.
            result["is_download"] = self._download_mode
            # A listing or an import source says nothing about identifying —
            # its rows are what was asked for — so a row without the field
            # is matched unless it says it found nothing.
            result.setdefault(
                "identify", IDENTIFY_UNMATCHED if result.get("not_found") else "matched"
            )
            result.setdefault("external_ids", {})
            if result.get("not_found"):
                not_found_count += 1
            # A local file that nothing identified draws its own embedded art,
            # whether the identify step found nothing or never ran.
            if result["identify"] in (IDENTIFY_UNMATCHED, IDENTIFY_SKIPPED):
                src = result.get("source_path") or ""
                result["cover_file_path"] = src.replace("\\", "/") if src else ""

        if listed:
            source = len(self._results)
            self._results.extend(listed)
            # Only the rows the setting does not hide reach the view, and they
            # are the tail of it — an append cannot land above anything.
            arriving = [
                row for row in range(source, len(self._results))
                if not self._is_hidden(self._results[row])
            ]
            if arriving:
                row = len(self._visible)
                # An insert rather than a reset, for the same reason
                # `importFileAs` uses one: a list the user has been building
                # keeps its selection and its scroll position. Existing rows
                # keep their index and their uid, so neither `_selected_ids` nor
                # the prefetch slot — which is keyed by uid — needs touching.
                self.beginInsertRows(
                    QModelIndex(), row, row + len(arriving) - 1
                )
                self._visible.extend(arriving)
                self.endInsertRows()
                # Model rows, because what reads this scrolls to them.
                self.rows_appended.emit(row, len(arriving))

        # These three describe the *list*, so they count every row in it —
        # including the ones the setting is holding off screen, which are as
        # much a part of what was gathered as any other.
        self._found += len(listed) - not_found_count
        self._in_database += sum(1 for r in listed if r.get("id") is not None)
        self._not_found += not_found_count
        # ...and this one describes the *run*, so it counts every match the run
        # found. A row that was already in the library still matched, and the
        # toast quotes this against `searchTotal`.
        self._last_matched = len(results) - not_found_count

        self._evaluate_album_completeness()

        self._set_has_results(bool(self._results))
        self.stats_changed.emit()
        self.result_count_changed.emit()
        self.search_completed.emit()

    def _evaluate_album_completeness(self) -> None:
        """Tell the album whether the library now holds the whole release.

        Runs twice off one fetch — here, when the listing lands, and again from
        `_on_stash_stopped` when the import it prompted has drained. The second
        pass is the one that ticks a record the user has just finished filling
        in, and it costs no network call because the listing is still held.

        It asks `owned_track_ids` again rather than reusing the pass above,
        because that one ran over the *filtered* rows and this question is about
        the release. Three queries, once per album run — the same batch the merge
        pays, and cheap next to the fetch that got here.

        Silent about everything that could go wrong. The album may have been
        deleted while the listing was in flight, and that is not a failure of
        the import — there is simply nothing to write the answer on.

        It is also where the **claim** is offered, because this is the one place
        holding the album row, the release's rows and `owned_track_ids` together
        — which is exactly what the offer is made of. See `_offer_album_claim`.
        """
        if self._album_service is None or not self._album_id:
            return
        rows = self._album_release_rows
        if not rows:
            return
        album = self._album_service.get(self._album_id)
        if album is None:
            return
        try:
            owned = self._import_service.owned_track_ids(rows)
            self._album_service.record_release_check(
                int(album.id), release_count=len(rows), owned_track_ids=owned
            )
            self._offer_album_claim(album, rows, owned)
        except Exception:
            # A checkmark is not worth failing an import over.
            logger.exception("Could not record album completeness")

    def _offer_album_claim(
        self, album: Any, rows: List[Dict[str, Any]], owned: List[Any]
    ) -> None:
        """Ask whether tracks the library has but this album lacks should join it.

        `AlbumService.split_owned` draws the same line completeness is decided
        on: the release names a recording, the library holds it, and it is on
        some *other* record — a single, or another pressing. The run has just
        proved it belongs to this one, and until there was somewhere to put that
        finding it was spent on marking the row "already in your library" and
        dropping it.

        The titles come free. `owned` is positional against `rows`, so the row
        beside an id names the track without a query — the same trick
        `reorder_completed` uses to name the odd ones out rather than count them.

        Once per album run, whichever pass gets here first. The second pass
        arrives after the user has answered, and re-asking a declined question is
        worse than never having asked it.
        """
        if self._album_claim_offered:
            return
        _, off_album = self._album_service.split_owned(int(album.id), owned)
        if not off_album:
            return

        wanted = set(off_album)
        titles: Dict[int, str] = {}
        for row, track_id in zip(rows, owned):
            if track_id and int(track_id) in wanted and int(track_id) not in titles:
                titles[int(track_id)] = str(row.get("title") or "")

        # Set before the signal goes out: a handler that answers synchronously —
        # a dialog is not obliged to be modal — would otherwise re-enter here.
        self._album_claim_offered = True
        self.album_claim_offered.emit(
            int(album.id),
            str(album.name or ""),
            off_album,
            [titles.get(track_id, "") for track_id in off_album],
        )

    @Slot(int, "QVariantList", result=int)
    def claimTracksForAlbum(self, album_id: int, track_ids: list) -> int:  # noqa: N802
        """Put the offered tracks on the album. Returns how many were added.

        The accept half of `album_claim_offered`, and it belongs here rather
        than on `albumVM` for one reason: the claim reorders the album against
        the release, and this view model is the only object still holding that
        listing — the album page navigated away when the run started.

        Each claimed track gains this album as a **non-primary** one; the record
        it belongs to is untouched. See `AlbumService.claim_tracks`.

        Failure is reported through `error_occurred` rather than a signal of its
        own, and never raised: a claim is not worth killing the import tab over,
        the same posture the completeness check takes.
        """
        if self._album_service is None:
            return 0
        wanted = [int(t) for t in track_ids or [] if t]
        if not wanted:
            return 0
        try:
            return int(self._album_service.claim_tracks(
                int(album_id), wanted, self._album_release_rows
            ))
        except Exception:  # noqa: BLE001
            logger.exception("Could not add tracks to album %s", album_id)
            self.error_occurred.emit("Could not add those tracks to the album.")
            return 0

    def _identifiable(self, results: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Drop rows that say nothing and could never be imported.

        A row with no file, no id, no title and no artist is not a near miss —
        it is a blank line. There is nothing to name a track after, nothing to
        retry against, and `_is_stashable` already refuses it, so it can only sit
        in the list wearing an error marker until the user clears it away.

        Reachable from a source that answered with an empty row — a plugin is
        not obliged to be tidy. The identify worker's contract is one result per
        candidate, so it reports a failure as a row rather than as nothing, and
        for a local file that is right: the file is still there and still
        importable off its own tags. A row with nothing in it leaves nothing
        behind at all.

        A CSV row cannot reach this — `parse_csv` refuses a row with no title,
        artists, id, album or genres — so its unmatched rows still show, which is
        what makes "4 of 30 didn't match" legible.

        The count is not adjusted here because these rows are removed before
        anything counts them; `_start_search` reports what the run was asked for,
        and `search_completed`'s "matched 0 of 1" is what says it found nothing.
        """
        kept = [
            result for result in results
            if result.get("source_path")
            or any((result.get("external_ids") or {}).values())
            or result.get("title")
            or result.get("artist")
        ]
        if len(kept) != len(results):
            logger.info(
                "Dropped %d unidentifiable result(s) from an import run",
                len(results) - len(kept),
            )
        return kept

    def _without_matched(
        self, results: List[Dict[str, Any]]
    ) -> tuple[List[Dict[str, Any]], int]:
        """Drop arriving rows whose track is already spoken for.

        The other half of `_without_listed`, and the half that only a mixed list
        makes necessary. A candidate's *file* is known before the run and is
        filtered there; the *track* it is a copy of is only known once the match
        comes back. Import a folder, then a CSV naming one of the same
        recordings, and without this the library would gain the track twice —
        stashed from disk once and fetched once — because nothing downstream
        compares external ids before creating a row.

        Two rows in the *same* run can collide the same way: two files of one
        recording in one folder both match the same id. The first keeps the
        place; the rest are counted as repeats.

        A row that matched nothing has no id, and is never dropped here. It was
        compared by its file before the run, which is the only comparison it can
        answer to.
        """
        listed = {
            identity for identity in self._listed_identities()
            if identity[0] == "ext"
        }
        kept: List[Dict[str, Any]] = []
        for result in results:
            identities = self._identities("", result.get("external_ids") or {})
            if identities & listed:
                continue
            listed |= identities
            kept.append(result)
        return kept, len(results) - len(kept)

    def _replace_results(self) -> None:
        """Empty the list in place, keeping `_download_mode` — the run's mode — intact.

        `clearResults` would do the rest of this and then force `_download_mode` back
        to False, which is right for a user pressing Done and wrong for a run
        that is about to fill the list with rows of its own mode.
        """
        self.beginResetModel()
        self._results = []
        self._visible = []
        self._selected_ids.clear()
        self._invalidate_prefetch()   # the row the slot named no longer exists
        self.endResetModel()
        self._found = self._not_found = self._in_database = 0

    @Slot(str)
    def _on_search_failed(self, message: str) -> None:
        if self._cancelled:
            return
        self.error_occurred.emit(f"Search failed: {message}")

    @Slot()
    def _on_search_stopped(self) -> None:
        self._search_thread = None
        self._search_worker = None
        self._clear_cancelled()
        self._set_is_searching(False)

    # ==================================================================
    # What a row is
    # ==================================================================

    def _state(self, result: Dict[str, Any]) -> str:
        """Classify one row. The only place this decision is made.

        Asks the row — and, for a row that would be fetched, which ids the
        enabled audio sources accept. `_download_mode` describes the run that
        is or was going on, and rows outlive runs — a list built across two
        imports would otherwise have its older half judged by the newer half's
        mode.

        Order matters. Being in the library comes first because it is the end of
        every path: whatever a row was, once it has an id there is nothing left
        to say about it. After that the row's kind splits the tree, because a
        missing match means something different on each side — a local file
        still has its own audio and can be filed from its own tags, while a row
        that had only an id has nothing left at all.

        The identify state splits the local side in three. ``matched`` is ready.
        ``unmatched`` — the provider was asked and knew nothing — is worth
        saying, which is what `tagsOnly` does. ``skipped`` — there was no
        provider to ask — is not a near miss of anything: it is a file, filed
        by its own tags, and it is drawn exactly like one that matched.
        """
        if result.get("id") is not None:
            return STATE_IN_LIBRARY
        if result.get("is_download"):
            # Nothing but an id was ever known about this row, so no source
            # that accepts one of its ids is the end of it.
            return STATE_DOWNLOAD if self._fetchable(result) else STATE_UNAVAILABLE
        identify = result.get("identify") or (
            IDENTIFY_UNMATCHED if result.get("not_found") else "matched"
        )
        named = bool(result.get("title") and result.get("artist"))
        if identify == IDENTIFY_SKIPPED:
            return STATE_STASH if named else STATE_NEEDS_METADATA
        if identify != IDENTIFY_UNMATCHED:
            return STATE_STASH
        # Matched nothing, but the file is still here. Whether it can be
        # imported comes down to whether there is anything to name the track.
        return STATE_TAGS_ONLY if named else STATE_NEEDS_METADATA

    def _fetchable(self, result: Dict[str, Any]) -> bool:
        """Whether some enabled audio source accepts one of this row's ids."""
        ids = result.get("external_ids") or {}
        return any(ids.get(key) for key in self._audio_keys)

    def _already_done(self, result: Dict[str, Any]) -> bool:
        """Whether this row has nothing left to do — it is in the library."""
        return self._state(result) == STATE_IN_LIBRARY

    def _is_stashable(self, result: Dict[str, Any]) -> bool:
        """Whether a row can be saved (fetched for a download row, else filed)."""
        return self._state(result) in SAVEABLE_STATES

    def _blocked_reason(self, result: Dict[str, Any]) -> str:
        """Why this row cannot be fetched right now, or ``""`` if it can.

        Only a download row fetches audio; a local row works from a file that is
        already on the machine, so it is never blocked. A source that needs the
        user's agreement and has not had it never reaches here — the host does
        not count it as enabled — so the reasons left are the ones a user can
        act on in Settings → Plugins.
        """
        if not result.get("is_download"):
            return ""
        if self._registry is None or not self._has_audio_source():
            return "No enabled plugin can fetch audio. Install or turn one on in Settings → Plugins."
        if not self._fetchable(result):
            return "None of the enabled plugins can fetch this track."
        source = self._registry.audio_source_for(result)
        if source is not None and not self._registry.has_consent(source.plugin_id):
            return f"{source.name} needs you to agree to its terms first."
        return ""

    @Slot(int)
    def stashTrack(self, uid: int) -> None:
        """Queue a single matched row for saving (no-op if already saved)."""
        result = self._result_by_uid(uid)
        if result is None or self._already_done(result):
            return
        reason = self._blocked_reason(result)
        if reason:
            self.download_blocked.emit(uid, reason)
            return
        if not self._is_stashable(result):
            return
        if uid in self._stash_pending:
            return
        self._stash_pending.add(uid)
        self._stash_queue.append(uid)
        self._pump_stash()
        # A row queued while another is already being written would otherwise
        # never be prefetched: the trigger in `_start_stash` has already run for
        # the row in flight, and the next one will not run until it finishes.
        self._maybe_start_prefetch()

    @Slot()
    @Slot(bool)
    def stashAll(self, include_unmatched: bool = True) -> None:
        """Queue every saveable not-yet-saved row.

        *include_unmatched* decides the fate of the files that matched nothing.
        They can be imported — a title and artist read from their own tags is
        enough to make a library entry — but with none of the album, artwork or
        release date a match would have supplied, so they are worth asking
        about rather than deciding for the user. The default is True, which is
        what the button did before there was anything to ask.

        Only a local-file row has a choice to make here. A CSV row that matched
        nothing has no id to fetch by, so `_is_stashable` has already excluded
        it and this flag changes nothing for it.
        """
        pending_downloads = [
            result for result in self._results
            if result.get("is_download") and not self._already_done(result)
        ]
        if pending_downloads and not self._has_audio_source():
            # Refused as a batch, before anything is queued: one explanation is
            # what the user needs, not one per row. Asked of the rows rather
            # than the mode, so a list holding both kinds would still be
            # stopped by the one row that needs a source.
            self.download_blocked.emit(-1, self._blocked_reason(pending_downloads[0]))
            return
        for result in self._results:
            uid = result.get("uid")
            if uid is None or uid in self._stash_pending:
                continue
            if self._already_done(result) or not self._is_stashable(result):
                continue
            if not include_unmatched and result.get("not_found"):
                continue
            self._stash_pending.add(uid)
            self._stash_queue.append(uid)
        self._pump_stash()
        self._maybe_start_prefetch()   # see `stashTrack`

    @Slot()
    def abortStash(self) -> None:
        """Stop an import run: cancel the row in flight, drop the ones queued.

        The queue empties at once, so nothing further starts. The row already
        being written stops at its next safe boundary — after the transcode or
        the download, never between creating a track row and moving its file
        into the vault, because that is the one interruption that would leave
        the library naming a file that is not there. Everything before that
        boundary happens inside a temporary directory, so a stopped row leaves
        nothing behind and simply never appears in the library.

        Tracks already imported are not touched. They are finished; there is
        nothing partial about them to undo.
        """
        if not self.isStashing:
            return

        self._aborting = True
        self._stash_queue.clear()
        # The queue is gone, so the row the slot was fetching for is gone with
        # it. Cancels the worker; its references stay until its thread ends.
        self._invalidate_prefetch()
        # The row in flight keeps its place in `_stash_pending` until its own
        # `done` arrives, so its button cannot queue it a second time while it
        # is still winding down.
        self._stash_pending = {self._stashing_uid} if self._stash_active else set()
        if self._stash_worker is not None:
            self._stash_worker.cancel()
        self.stashing_changed.emit()

    def _pump_stash(self) -> None:
        """Start the next queued stash if none is currently running."""
        if self._stash_active or not self._stash_queue:
            return

        # Skip over rows that became invalid/saved while queued.
        while self._stash_queue:
            uid = self._stash_queue.pop(0)
            result = self._result_by_uid(uid)
            if result is None or self._already_done(result):
                self._stash_pending.discard(uid)
                continue
            self._start_stash(uid, result)
            return

    def _peek_next_stashable(self) -> Optional[tuple[int, Dict[str, Any]]]:
        """The row `_pump_stash` would take next, without consuming the queue.

        A peek, unlike the pump: a row this skips over is left in the queue for
        the pump to discard properly, together with its `_stash_pending` entry.
        Guessing wrong about which row is next costs a wasted download; editing
        the queue from here would cost a row.
        """
        for uid in self._stash_queue:
            result = self._result_by_uid(uid)
            if result is not None and not self._already_done(result):
                return uid, result
        return None

    # ------------------------------------------------------------------
    # Cover prefetch
    # ------------------------------------------------------------------

    def _maybe_start_prefetch(self) -> None:
        """Fetch the next queued row's cover while the current one is written.

        Every guard that decides whether a prefetch happens lives here, so the
        three call sites — a row starting, the slot freeing, a row being queued
        mid-run — are each an unconditional one-liner that cannot double-start.
        """
        if not self._stash_active:
            return                      # never speculate outside a run
        if self._prefetch_thread is not None:
            return                      # one at a time; the slot retries itself
        target = self._peek_next_stashable()
        if target is None:
            return
        uid, result = target
        if result.get("is_download"):
            return                      # the fetch path fetches its own art
        if uid == self._prefetch_uid:
            return                      # already fetched, or already tried
        urls = [str(u) for u in (result.get("artwork_urls") or []) if u]
        if not urls:
            return                      # nothing online to fetch; the file's own art is used

        self._prefetch_uid = uid
        self._prefetch_bytes = None
        thread = QThread(self)
        worker = CoverPrefetchWorker(uid, urls)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_prefetch_finished)
        own_worker(thread, worker, self._on_prefetch_stopped)
        self._prefetch_thread = thread
        self._prefetch_worker = worker
        thread.start()

    def _take_prefetched(self, uid: int) -> Optional[bytes]:
        """This row's prefetched art, if it is genuinely this row's.

        Frees the slot either way. Uids are never reused — `_next_uid` only
        climbs, across `clearResults` and new searches alike — so a stale
        result can only ever fail this check, never be misapplied to a
        different row that inherited the number.
        """
        cover_bytes = self._prefetch_bytes if uid == self._prefetch_uid else None
        self._invalidate_prefetch()
        return cover_bytes

    def _invalidate_prefetch(self) -> None:
        """Forget the slot, and ask any worker filling it to stop.

        References are deliberately left alone: dropping them here would drop
        them while the thread is still alive, which is the crash `own_worker`
        exists to prevent. `_on_prefetch_stopped` owns that, and only it.
        """
        if self._prefetch_worker is not None:
            self._prefetch_worker.cancel()
        self._prefetch_uid = -1
        self._prefetch_bytes = None

    @Slot(int, object)
    def _on_prefetch_finished(self, uid: int, cover_bytes: object) -> None:
        """Bank the bytes, if the row they were fetched for still wants them."""
        if uid != self._prefetch_uid:
            return      # aborted, retargeted, or already stashing without them
        # Falsy means the fetch found nothing, which is banked as nothing: the
        # stash will try again itself rather than take a failed fetch as proof
        # that this row has no artwork.
        self._prefetch_bytes = cover_bytes if cover_bytes else None

    @Slot()
    def _on_prefetch_stopped(self) -> None:
        """The prefetch thread has gone. Release it, then reconsider the slot.

        Re-entering `_maybe_start_prefetch` here recovers the one case the
        trigger in `_start_stash` cannot: a prefetch still in flight when its
        own row started, which holds the slot past the moment the row after it
        became the obvious target.

        Nothing here touches `isStashing` or `isAborting`. A prefetch is not
        work the user asked for, and letting a lingering one count as "still
        running" would leave the abort button hanging on a network timeout long
        after everything they care about had stopped.
        """
        self._prefetch_thread = None
        self._prefetch_worker = None
        self._maybe_start_prefetch()

    def _start_stash(self, uid: int, result: Dict[str, Any]) -> None:
        self._stash_active = True
        # A row is starting, so whatever was being aborted is over. Reachable
        # because a single row's own button can queue work while a cancelled
        # run is still unwinding.
        self._aborting = False
        # Claimed before the worker is built, and released whether it matched
        # this row or not: from here on the slot belongs to whatever comes
        # next. Doing it unconditionally here is what spares every other path
        # from per-row cleanup — a row's uid can only sit in the slot while it
        # has not yet started.
        cover_bytes = self._take_prefetched(uid)
        thread = QThread(self)
        if result.get("is_download"):
            # Fetch the identified track and apply the CSV metadata. The row
            # already knows every credited artist's identity, where the fetched
            # file may know only the primary's — so hand it over rather than
            # let the source work it out again from less. Added only when it has
            # something in it: `fetch_and_stash` treats any non-empty value as a
            # win, and an empty *list* passes that test, so `[]` here would
            # erase the refs the source did manage to supply.
            csv_override = dict(result.get("csv_override") or {})
            if result.get("artist_refs"):
                csv_override["artist_refs"] = result["artist_refs"]

            worker: QObject = FetchAudioWorker(
                uid,
                result,
                self._track_service,
                self._registry,
                self._cover_service,
                metadata_override=csv_override,
                lyrics_service=self._lyrics_service,
            )
            worker.progress.connect(self.stash_progress)
        else:
            # Popped, not read: this row's entry is consumed exactly once, which
            # is what keeps the cache to a single row without an eviction pass.
            # A miss (prefetch still in flight, or never started) hands over
            # `None` and the worker fetches its own artwork as it always did.
            worker = ImportStashWorker(
                uid, result, self._track_service, self._cover_service,
                cover_bytes=cover_bytes,
                registry=self._registry,
                lyrics_service=self._lyrics_service,
            )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_stash_finished)
        worker.failed.connect(self._on_stash_failed)
        worker.done.connect(self._on_stash_done)
        own_worker(thread, worker, self._on_stash_stopped)
        self._stash_thread = thread
        self._stash_worker = worker
        thread.start()
        self._set_stashing(uid)
        self.stash_started.emit(uid)
        # This row now owns a thread for several seconds. Spend them on the
        # next row's artwork — started here rather than when the row *ends*,
        # because by then there is nothing left to overlap with.
        self._maybe_start_prefetch()

    @Slot(int, int)
    def _on_stash_finished(self, uid: int, track_id: int) -> None:
        row = self._row_by_uid(uid)
        if row is not None:
            self._results[row]["id"] = track_id
            # Counted up whichever way the row goes next — it stays in the list
            # either way, and hiding it does not stop it being in the library.
            # `_remove_rows` is the only thing that takes it back off, and only
            # when the row itself goes.
            self._in_database += 1
            if self._imports.clear_imported:
                # Off screen, not out of the list: it goes on holding the count
                # the `in library` chip draws, and it comes back if the setting
                # does. `_resync_visible` names it through `imported_removed`
                # before it goes, which is what lets the GUI give a row that
                # finished the exit it has earned rather than the one a row the
                # user threw off the list gets.
                self._resync_visible()
            else:
                model_row = self._model_row(row)
                if model_row is not None:
                    idx = self.index(model_row)
                    # `importState` is computed from `id`, so the row's whole
                    # marking — its chip, its wash, whether it fades — turns on
                    # that line above. A dataChanged that named only the track id
                    # would leave the row still offering to import something it
                    # has just imported.
                    self.dataChanged.emit(
                        idx, idx, [ROLE_TRACK_ID, ROLE_IMPORT_STATE]
                    )
            self.stats_changed.emit()
        self.stash_completed.emit(uid, track_id)

    @Slot(int, str)
    def _on_stash_failed(self, uid: int, message: str) -> None:
        logger.warning("Stash failed for uid %s: %s", uid, message)
        self.stash_failed.emit(uid, message)

    @Slot(int)
    def _on_stash_done(self, uid: int) -> None:
        """This row is finished with. Its thread may not be — see `own_worker`.

        The spinner goes out from here rather than from `_on_stash_stopped`, so
        it clears when the row is done rather than one event loop later, when
        the thread it ran on has finished unwinding.
        """
        self._stash_pending.discard(uid)
        if self._stashing_uid == uid:
            self._set_stashing(-1)
        self.stash_finished.emit(uid)

    @Slot()
    def _on_stash_stopped(self) -> None:
        """The thread has gone. Release it, then let the queue move on.

        Pumping from here rather than from `done` is what keeps the queue's
        promise of one stash at a time honest: the next worker is not created
        until the previous thread is genuinely finished, instead of merely
        having been asked to stop.
        """
        self._stash_thread = None
        self._stash_worker = None
        self._stash_active = False
        self._pump_stash()
        if not self.isStashing:
            self._aborting = False
            # The run is over — possibly by the pump skipping every row that
            # was left, which never reaches `_start_stash`. This is the only
            # path on which a settled buffer could outlive its run.
            self._invalidate_prefetch()
            # And the only point at which "did that finish the record" is
            # answerable. Each arriving track cleared the flag on its way in
            # through `_apply_albums`; this lands after the last of them.
            self._evaluate_album_completeness()
        # `isStashing` goes false the moment the queue runs dry, and nothing
        # else announces it — `_set_stashing` only fires when a row starts or
        # stops, and the last row stopping is not the same event as the run
        # ending.
        self.stashing_changed.emit()
        # `_stash_active` is the other half of "which row is in flight", so the
        # Remove item's count has to be re-asked here too — see `_set_stashing`.
        self.stats_changed.emit()

    # ==================================================================
    # Cancel / clear
    # ==================================================================

    @Slot()
    def cancelImport(self) -> None:
        """Abandon the scan or match in flight and put the tab back.

        Three things have to happen, and only the first of them is about the
        worker:

        * **Stop.** The worker is told to give up at its next file. It cannot be
          stopped where it stands — it is inside a disk read or a network call —
          so this returns long before the thread does.
        * **Drop what it found.** A cancelled worker emits no result, and
          `_cancelled` covers the one case it cannot: a result already queued
          when the user clicked. Either way nothing from this run reaches the
          model.
        * **Go back.** The busy flags are cleared here rather than when the
          thread eventually unwinds, so the tab returns immediately. It returns
          to whatever it was showing *before* this run started, because that is
          what is still there — an earlier import's results if there were any,
          the library page if there were not. Neither is touched.

        Cancelling when nothing is running still resets the flow state, which is
        what aborts the options card. That case is the whole reason the card
        cancels through here rather than by closing itself: the scan it was
        drawn from is finished and holds a candidate set nothing else on the
        page can reach, so a card dismissed without this would strand it.
        """
        for worker in (self._analyze_worker, self._search_worker):
            if worker is not None:
                worker.cancel()
                self._cancelled = True

        self._set_is_analyzing(False)
        self._set_is_searching(False)
        self._progress_done = 0
        self._progress_total = 0
        self.search_progress_changed.emit()
        self._reset_flow_state()

    @Slot()
    def clearResults(self) -> None:
        """Discard the results list and return the tab to its stats view."""
        self._replace_results()
        self._set_has_results(False)
        self._last_matched = 0
        self._skipped_duplicates = 0
        self._last_in_database = 0
        # An emptied list is no longer about any release, so a later import
        # finishing must not be judged against the one that used to be here —
        # nor answered for by a claim the user has already been asked about.
        self._album_id = 0
        self._album_release_rows = []
        self._album_claim_offered = False
        self.stats_changed.emit()
        self.result_count_changed.emit()
        self._set_download_mode(False)

    # ==================================================================
    # Selection (mirrors SearchViewModel)
    # ==================================================================

    @Slot(int, bool)
    def setSelected(self, uid: int, selected: bool) -> None:
        if selected:
            self._selected_ids.add(uid)
        else:
            self._selected_ids.discard(uid)
        self._emit_selection_changed({uid})

    @Slot(int)
    def setSelectedExclusive(self, uid: int) -> None:
        affected = self._selected_ids | {uid}
        self._selected_ids = {uid}
        self._emit_selection_changed(affected)

    @Slot(list)
    def setSelectedMultiple(self, uids: list[int]) -> None:
        affected = self._selected_ids | set(uids)
        self._selected_ids = set(uids)
        self._emit_selection_changed(affected)

    @Slot()
    def clearSelection(self) -> None:
        if not self._selected_ids:
            return
        affected = set(self._selected_ids)
        self._selected_ids.clear()
        self._emit_selection_changed(affected)

    @Slot(result="QVariantList")
    def selectedUids(self) -> List[int]:
        """The selected uids, unordered.

        The other half of `selectionChanged`: a GUI mirrors the selection to
        draw it, and this is how it re-reads one it did not make itself. Every
        other track list has answered this since they shared a base class; this
        one is a bare list model and had to say it for itself.
        """
        return list(self._selected_ids)

    def _emit_selection_changed(self, affected_uids: set) -> None:
        # Over the rows on screen: a hidden row can be in `_selected_ids` — the
        # legend's `in library` chip puts it there — and `dataChanged` for a row
        # the model is not exposing is a row index that does not exist.
        for model_row, source in enumerate(self._visible):
            if self._results[source].get("uid") in affected_uids:
                idx = self.index(model_row)
                self.dataChanged.emit(idx, idx, [ROLE_IS_SELECTED])
        # `removableSelectedCount` is a stat about the selection, and this is
        # the only place the selection changes without the list changing. The
        # menu row's label is bound to it and would otherwise keep the number it
        # was opened with.
        self.stats_changed.emit()
        # A per-row `dataChanged` is enough for a delegate that is on screen,
        # and not enough for a GUI holding its own copy of the selection — which
        # `TrackTable` does, because selection outlives the delegates that draw
        # it. Without this a selection made anywhere but the list itself — the
        # legend picking out every unavailable row, say — would be invisible to
        # it.
        self.selectionChanged.emit()

    # ==================================================================
    # Removing rows from the list
    # ==================================================================

    def _removable_selected_rows(self) -> List[int]:
        """Ascending indices of the selected rows that may be taken out.

        Everything selected except the row being written right now. That one is
        already inside a transcode or a fetch, and pulling it out of the list
        would not stop it — it would only hide it, and the track would land in
        the library with no row left to show for it. Stopping a run is what
        Abort is for, and it says so on the button.

        A row that is already in the library is *not* excluded. It is finished
        with, and clearing away what is done is most of the point of this.

        A row the setting is *hiding* is excluded, though, and that is a
        different question from being in the library — the two only coincide
        while the setting is on. Selecting the `in library` chip selects rows
        that may be off screen, and destroying rows the user cannot see is not
        what a gesture aimed at the visible list can be allowed to mean.
        """
        in_flight = self._stashing_uid if self._stash_active else None
        return [
            row
            for row in self._visible
            if self._results[row].get("uid") in self._selected_ids
            and self._results[row].get("uid") != in_flight
        ]

    @Slot()
    def removeSelected(self) -> None:  # noqa: N802
        """Take the selected rows out of the import list.

        Nothing is deleted: these rows are candidates that have not been saved,
        so removing one only says "not this time". A row that *was* saved is
        already in the library and stays there — this drops the receipt, not the
        track.

        A row can be in four places at once, and all four have to let go of it
        or the tab keeps reporting work it no longer has. The queue is the one
        that shows: `isStashing` counts a queued uid, so a stale entry would
        leave the button reading "Abort import" over an empty run. `_pump_stash`
        does skip rows that have vanished, but only when something next pumps
        it, which for the last queued row is never.
        """
        self._remove_rows(self._removable_selected_rows())

    def _remove_rows(self, rows: List[int]) -> None:
        """Take *rows* — ascending `_results` indices — out and settle up after.

        The removal itself, with none of the policy about which rows may go. The
        in-flight guard belongs to `_removable_selected_rows` and stays there.

        This is a genuine removal, the only kind there is: `clear_imported`
        hides rows and this destroys them. Indices are into `_results`, so a run
        may hold rows that are on screen and rows that are not, and only the
        former have a model signal to raise. They are contiguous in `_visible`
        when they are contiguous in `_results`, because hiding never reorders.
        """
        if not rows:
            return

        removed_uids: set[int] = set()
        # Descending, so each run's indices are still valid as the ones above it
        # are removed. Same shape as `QueueViewModel.removeSelected`.
        for start, end in _descending_runs(rows):
            shown = [
                model_row
                for model_row in (self._model_row(row)
                                  for row in range(start, end + 1))
                if model_row is not None
            ]
            for result in self._results[start:end + 1]:
                uid = result.get("uid")
                if uid is not None:
                    removed_uids.add(uid)
                # Counted down the way `_on_search_finished` counted them up,
                # and `id` is the same test `_on_stash_finished` adds on.
                if result.get("not_found"):
                    self._not_found -= 1
                else:
                    self._found -= 1
                if result.get("id") is not None:
                    self._in_database -= 1

            if shown:
                self.beginRemoveRows(QModelIndex(), shown[0], shown[-1])
                del self._results[start:end + 1]
                self._visible = self._visible_rows()
                self.endRemoveRows()
            else:
                # Nothing on screen went, so there is nothing to announce — but
                # the mapping still moves, because every index above the run
                # shifted down.
                del self._results[start:end + 1]
                self._visible = self._visible_rows()

        self._selected_ids -= removed_uids
        self._stash_pending -= removed_uids
        self._stash_queue = [
            uid for uid in self._stash_queue if uid not in removed_uids
        ]
        if self._prefetch_uid in removed_uids:
            self._invalidate_prefetch()

        self._set_has_results(bool(self._results))
        self.stats_changed.emit()
        self.result_count_changed.emit()
        # The queue may have just emptied, which is what turns the button back
        # from "Abort import" into "Import all".
        self.stashing_changed.emit()

    # ==================================================================
    # Helpers
    # ==================================================================

    def _result_by_uid(self, uid: int) -> Optional[Dict[str, Any]]:
        for result in self._results:
            if result.get("uid") == uid:
                return result
        return None

    def external_ids_for_uid(self, uid: int) -> Dict[str, str]:
        """Every id the row with *uid* carries — for a context menu's actions."""
        result = self._result_by_uid(int(uid))
        return dict((result or {}).get("external_ids") or {})

    def _row_by_uid(self, uid: int) -> Optional[int]:
        for row, result in enumerate(self._results):
            if result.get("uid") == uid:
                return row
        return None

    @staticmethod
    def _source_format(source_path: str) -> str:
        """The row's container format, lower-case and without the dot.

        `""` for a row with no file — a CSV row or an import source's, whose
        audio does not exist yet and is filed as Opus whatever the source sends.

        The same test the saving code makes: `ensure_opus` copies a `.opus` file
        and transcodes everything else, and `stash_imported_track` records what
        it came from on the same comparison. Deriving it here from `source_path`
        rather than carrying it down from `ProbedFile.ext` keeps that to one
        rule — and the extension never survived the match run anyway, which is
        why nothing could say what a row would cost until now.
        """
        if not source_path:
            return ""
        return Path(source_path).suffix.lower().lstrip(".")

    @staticmethod
    def _identities(source_path: str, ids: Dict[str, Any]) -> set[tuple[str, str]]:
        """Every way a row can be recognised as one already listed.

        Both kinds, where a row has both, because a mixed list can collide
        either way round. Two rows are the same import when they are the same
        audio on disk — whatever identifying them decided to call each of them —
        *or* when they share an external id, which is what would otherwise let a
        folder and a CSV each bring in one copy of the same track.

        An id is spelled ``("ext", "provider:kind:value")``: a flat string, so
        an identity set can be compared and combined without caring which kinds
        exist, and a tuple only here — never in a row dict, which crosses into
        QML and must hold dicts and lists alone.

        Paths are resolved so that a folder reached by a symlink and the same
        folder reached directly are one file and not two, which is the same
        judgement `gather_media_files` makes within a single scan. Resolution
        can fail — a dead symlink, a permission error — and the raw path is used
        then, exactly as it is there.

        An empty set means "nothing to compare": a row with neither a file nor
        an id is neither the same as nor different from anything, so it is
        always kept.
        """
        identities: set[tuple[str, str]] = set()
        if source_path:
            path = Path(source_path)
            try:
                identities.add(("path", str(path.resolve())))
            except OSError:
                identities.add(("path", str(path)))
        for key, value in (ids or {}).items():
            if value:
                identities.add(("ext", f"{key}:{value}"))
        return identities

    def _listed_identities(self) -> set[tuple[str, str]]:
        """Identities of the rows already in the list, for skipping repeats."""
        identities: set[tuple[str, str]] = set()
        for result in self._results:
            identities |= self._identities(
                str(result.get("source_path") or ""),
                result.get("external_ids") or {},
            )
        return identities

    def _without_listed(self, files: List[Any]) -> tuple[List[Any], int]:
        """Drop candidates already in the list; return what is left and how many went.

        Before the identify run rather than after it, because identifying is one
        plugin call per file: re-importing a folder of five hundred tracks
        should cost nothing, not five hundred lookups whose answers are thrown
        away. Nothing can reach `_results` between here and the merge — every
        other entry point goes through `_can_start`, which refuses while a
        search thread is alive — so this one pass is the whole of the check.

        It is not the whole of the *rule*, though: a local candidate may carry
        no external id yet, so a file that will turn out to be a track already
        listed cannot be recognised until it has been identified. That is what
        `_without_matched` catches at the merge.
        """
        listed = self._listed_identities()
        if not listed:
            return files, 0

        kept: List[Any] = []
        for probed in files:
            identities = self._identities(
                str(getattr(probed, "source_path", "") or ""),
                getattr(probed, "external_ids", {}) or {},
            )
            if identities & listed:
                continue
            kept.append(probed)
        return kept, len(files) - len(kept)

    @staticmethod
    def _to_local_paths(urls: list) -> List[str]:
        """Convert QML FileDialog QUrls (or strings) to local filesystem paths."""
        paths: List[str] = []
        for item in urls or []:
            if isinstance(item, QUrl):
                local = item.toLocalFile()
            elif isinstance(item, str) and item.startswith("file:"):
                local = QUrl(item).toLocalFile()
            else:
                local = str(item)
            if local:
                paths.append(local)
        return paths

    def _can_start(self) -> bool:
        """Whether a new import may begin, complaining if it may not.

        Asks the thread slots rather than the busy flags. A cancelled run clears
        the flags at once but keeps its thread for as long as the file or
        identify in flight takes to come back, and letting a second run overwrite the
        slots would leave the first one's teardown pointing at the second one's
        thread. Normally the wait is one file or one match and the user has a
        file dialog to get through first, so this never fires; when the network
        call it is waiting on hangs, it fires and says so, because a button that
        does nothing without explanation is the bug this whole change is about.
        """
        if self._analyze_thread is None and self._search_thread is None:
            return True
        self.error_occurred.emit(self._busy_message())
        return False

    def _busy_message(self) -> str:
        """Why a run cannot start — which is not always the same reason.

        A thread is in the way either because the user has one going, or because
        they cancelled one and it has not noticed yet. Telling someone their
        import is "still running" a moment after they stopped it would read as
        the cancel button having failed again.
        """
        if self._cancelled:
            return "Still stopping the previous import — try again in a moment."
        return "An import is already running."

    def _clear_cancelled(self) -> None:
        """Retire the cancel flag once the run it applied to has unwound."""
        self._cancelled = False

    def _reset_flow_state(self) -> None:
        self._analysis = None
        self._candidates = []
        self._candidate_type_counts = {}
        self._use_filename_fallback = False
        self._searched_count = 0
        # Which release this run is about, what it turned out to hold, and
        # whether its claim has been offered. All three belong to the run rather
        # than to the list, so an abandoned run must not leave a listing behind
        # for the next import to be judged against — or a spent answer that
        # would keep the next run's offer from being made.
        self._album_id = 0
        self._album_release_rows = []
        self._album_claim_offered = False
        # A run that was abandoned has nothing to report about itself; the rows
        # it would have added are separate, and the list's own totals stay.
        self._last_matched = 0
        self._skipped_duplicates = 0
        self._last_in_database = 0
        self.analysis_changed.emit()
        self.candidates_changed.emit()
        self.stats_changed.emit()

    def _set_is_analyzing(self, value: bool) -> None:
        if self._is_analyzing != value:
            self._is_analyzing = value
            self.is_analyzing_changed.emit()

    def _set_is_searching(self, value: bool) -> None:
        if self._is_searching != value:
            self._is_searching = value
            self.is_searching_changed.emit()

    def _set_has_results(self, value: bool) -> None:
        if self._has_results != value:
            self._has_results = value
            self.has_results_changed.emit()

    def _set_stashing(self, uid: int) -> None:
        if self._stashing_uid != uid:
            self._stashing_uid = uid
            self.stashing_changed.emit()
            # `removableSelectedCount` excludes whichever row this names, and
            # is bound in QML off `stats_changed`. Without this the Remove item
            # keeps the number it had when the run started.
            self.stats_changed.emit()

    def _set_download_mode(self, value: bool) -> None:
        if self._download_mode != value:
            self._download_mode = value
            self.download_mode_changed.emit()

    # ==================================================================
    # Providers
    # ==================================================================

    def _kinds(self) -> tuple:
        """Every id kind enabled plugins declare, for drawing a row's ids."""
        return tuple(self._registry.declarations()) if self._registry is not None else ()

    def _probe_kinds(self) -> tuple:
        """The snapshot a probe or a CSV parse reads ids by: track kinds only.

        Taken here, on the UI thread, and handed to the worker as frozen
        declarations — so the worker reads immutable data and never asks the
        plugin host anything from its own thread.
        """
        if self._registry is None:
            return ()
        return tuple(
            kind for kind in self._registry.declarations(external_ids.TRACK)
            if kind.role == "recording"
        )

    def _has_audio_source(self) -> bool:
        return bool(self._audio_keys)

    def _has_identity(self) -> bool:
        return self._registry is not None and self._registry.has_identity(external_ids.TRACK)

    def _refresh_audio_keys(self) -> None:
        self._audio_keys = (
            tuple(self._registry.audio_keys()) if self._registry is not None else ()
        )

    @Slot()
    def _on_providers_changed(self) -> None:
        """A plugin went on or off: every download row may have changed state.

        Nothing about the rows moved — their ids are what they were — but
        whether anything can fetch them did, and with it `importState` and
        every count built on it.
        """
        self._refresh_audio_keys()
        if self._visible:
            self.dataChanged.emit(
                self.index(0), self.index(len(self._visible) - 1), [ROLE_IMPORT_STATE],
            )
        self._resync_visible()
        self.stats_changed.emit()
