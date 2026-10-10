"""Search ViewModel — one list model over very different sources.

The library and the online sources share a view but not a nature:

- **Library results** are library tracks. The search resolves to an ordered id
  vector and the shared row cache fills the fields as they are painted, so
  "load every track" costs one query instead of one query plus two per row.
- **Online results** come from a plugin's track source and are not in the
  library at all. There is nothing to read from the database, so those rows
  carry their own values: fetchable fields are served through ``local_value``,
  and the ROW-tier ones the model can only answer itself — ``coverUrl`` —
  through ``row_value``.

Which online sources exist is the enabled plugins' business — ``sources`` lists
them, a plain install has none, and the library is always there. Switching the
source swaps the vectors and the selection; each source keeps its own results,
so switching back does not lose them.

A library search can also return **artists** interleaved with tracks, or
**albums** on their own, under ``searchMode``. Both reuse the online-result
mechanism exactly — an id of 0 and their values served from a row dict — because
all three have the same shape of problem: none is a ``tracks`` row, so none can
be read from one.

Albums are their own mode and never join the mixed one. ``"best"`` ranks two
answers to "which recording did you mean", and an album is a different question
— one asked deliberately, by picking the mode, rather than one that should push
tracks off the top of a list nobody asked it of.

``searchMode`` defaults to ``"tracks"``, and on that setting this class behaves
precisely as it did before any of this existed. That default is the
compatibility promise: a GUI written against the older contract never asks for
artist or album rows and so never receives a row it has no delegate for.
Anything but tracks is opt-in.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from PySide6.QtCore import (
    Property,
    QModelIndex,
    QObject,
    QThread,
    Signal,
    Slot,
)

from app.artwork.cover_service import CoverService
from app.events.event_bus import EventBus
from app.importer.import_workers import FetchAudioWorker
from app.library.fields import LOCAL_ROW_KEYS
from app.library.list_model import TrackListModel
from app.library.row_cache import MISS, TrackRowCache
from app.library.selection import InMemorySelection
from app.repositories.track_repository import TrackRepository
from app.search.ranking import rank
from app.search.search_query import parse_filter
from app.search.source_worker import TrackSourceWorker
from app.services.track_service import TrackService
from app.viewmodels.worker_threads import own_worker

#: The one source that is always there.
LIBRARY_SOURCE = "library"

# Online results are flat dicts using snake_case keys; catalog field keys are
# camelCase. Anything not listed here spells the same in both.
_WEB_KEY: Dict[str, str] = {
    "externalIds": "external_ids",
    "coverUrl": "cover_url",
    "coverHash": "cover_hash",
    "stashedDate": "stashed_date",
}


@dataclass
class _SearchState:
    """Isolated state for one search source."""

    ids: List[int] = field(default_factory=list)          # library ids; 0 = not in the library
    uids: List[int] = field(default_factory=list)
    rows: List[Dict[str, Any]] = field(default_factory=list)  # online only; empty for library results
    selection: InMemorySelection = field(default_factory=InMemorySelection)
    query: str = ""


class SearchViewModel(TrackListModel):
    """The search interface and its results list."""

    search_results_changed = Signal()
    is_searching_changed = Signal()
    search_in_database_changed = Signal()
    result_count_changed = Signal()
    error_occurred = Signal(str)
    # `sources` changed — a plugin was turned on or off.
    sources_changed = Signal()

    # Download lifecycle, keyed by row uid
    download_completed = Signal(int, int)   # uid, track_id
    download_failed = Signal(int, str)      # uid, message
    download_finished = Signal(int)         # uid
    # Refused before it began, and why: nothing can fetch it, or its plugin's
    # terms have not been agreed to. Separate from download_failed because
    # nothing went wrong — the GUI answers this one with a route to Settings →
    # Plugins rather than an error.
    download_blocked = Signal(int, str)     # uid, reason
    # Refused before it began: a different download is still running. Also not a
    # failure, and a different answer again — there is nothing to turn on and
    # nothing to fix, only something to wait for.
    download_busy = Signal(int)             # uid that was turned away
    # What the download in flight is doing, as its source reports it.
    download_progress = Signal(int, str, float)  # uid, stage, percent (-1 unknown)
    # `downloadingUid` / `isDownloading` changed.
    downloading_changed = Signal()
    # `searchMode` changed.
    search_mode_changed = Signal()

    # What a library search is allowed to return.
    MODE_TRACKS = "tracks"
    MODE_ARTISTS = "artists"
    MODE_BEST = "best"
    # Albums, and deliberately **not** part of `MODE_BEST`. The mixed list ranks
    # two kinds of answer to "which recording did you mean" — a track is one, and
    # an artist is a route to all of theirs. An album is a different question,
    # asked on purpose, and interleaving it would push tracks off the top of a
    # list for a record the user was not looking for. Nothing in `ranking.py`
    # knows about albums, and that is the design rather than an omission.
    MODE_ALBUMS = "albums"
    SEARCH_MODES = (MODE_TRACKS, MODE_ARTISTS, MODE_BEST, MODE_ALBUMS)

    # The row kinds that serve their own values instead of the row cache. A
    # track row has a `tracks` row behind it and must go to the cache; these do
    # not exist there at all. See `local_value`.
    _SELF_SERVED_KINDS = frozenset({"artist", "album"})

    # Field definitions for the UI dropdown
    SEARCH_FIELDS = [
        {"label": "Title",        "value": "title"},
        {"label": "Artist",       "value": "artist"},
        {"label": "Album",        "value": "album"},
        {"label": "Genre",        "value": "genre"},
        {"label": "BPM",          "value": "bpm"},
        {"label": "Key",          "value": "key"},
        {"label": "Date",         "value": "date"},
        {"label": "Duration",     "value": "duration"},
        {"label": "URL",          "value": "url"},
        {"label": "ISRC",         "value": "isrc"},
        {"label": "Stashed Date", "value": "stashed_date"},
    ]

    TEXT_OPERATORS = ["Contains", "Equals"]
    NUMERIC_OPERATORS = ["=", ">", "<", ">=", "<="]

    FIELD_TYPES = {
        "title": "text", "artist": "text", "album": "text", "genre": "text",
        "key": "text", "url": "text", "isrc": "text",
        "bpm": "numeric", "duration": "numeric",
        "date": "date", "stashed_date": "date",
    }

    def __init__(
        self,
        track_repository: TrackRepository,
        registry: Any,
        event_bus: EventBus,
        track_service: TrackService,
        cover_service: Optional[CoverService] = None,
        row_cache: Optional[TrackRowCache] = None,
        artist_service=None,
        lyrics_service=None,
        album_service=None,
        parent=None,
    ) -> None:
        super().__init__(row_cache, None, parent)
        self._repo = track_repository
        # Optional and untyped: a search with no artist service simply never
        # produces artist rows, which is exactly what `MODE_TRACKS` means anyway.
        self._artist_service = artist_service
        # And the same for albums. Both degrade to the track path rather than
        # raising, so a caller assembling a cut-down registry gets a search that
        # works with fewer answers rather than one that does not work.
        self._album_service = album_service
        # Likewise optional: a download with no lyrics service keeps no lyrics,
        # which is the same outcome as a lookup that found none.
        self._lyrics_service = lyrics_service
        self._search_mode: str = self.MODE_TRACKS
        # The providers. ``None`` — a plain install — means the library is the
        # only source there is.
        self._registry = registry
        self.event_bus = event_bus
        self.track_service = track_service
        self.cover_service = cover_service

        self._db_state = _SearchState()
        # One state per online source, made the first time it is shown and kept
        # while the source exists — switching away and back loses nothing.
        self._source_states: Dict[str, _SearchState] = {}
        self._source: str = LIBRARY_SOURCE
        self._selection = self._db_state.selection
        self._sources: List[Dict[str, str]] = self._read_sources()

        self._is_searching: bool = False
        self._next_uid: int = 0
        # One download at a time — see `downloadRow`. Single-valued rather than
        # a map on purpose: two downloads in flight is not a state this class
        # can represent, so it is not a state a caller can talk it into.
        self._download_thread: Optional[QThread] = None
        self._download_worker: Optional[FetchAudioWorker] = None
        self._downloading_uid: int = -1
        self._web_search_thread: Optional[QThread] = None
        self._web_search_worker: Optional[TrackSourceWorker] = None

        event_bus.subscribe("track.deleted", self._on_track_deleted)
        if registry is not None and hasattr(registry, "changed"):
            registry.changed.connect(self._on_providers_changed)

    @property
    def _search_in_database(self) -> bool:
        return self._source == LIBRARY_SOURCE

    @property
    def _active(self) -> _SearchState:
        if self._search_in_database:
            return self._db_state
        return self._source_states.setdefault(self._source, _SearchState())

    def _online_states(self) -> List[_SearchState]:
        return list(self._source_states.values())

    # ------------------------------------------------------------------
    # Rows that are not library tracks
    # ------------------------------------------------------------------

    def local_value(self, row: int, key: str) -> Any:
        """Serve online and artist rows from their own dicts; library rows use the cache."""
        rows = self._active.rows
        if not rows or row >= len(rows):
            return MISS
        # A mixed library result holds a dict per row, but only the artist and
        # album ones carry anything: a track row still has a `tracks` row behind
        # it and must go to the cache. Returning its (empty) dict's None here
        # instead would answer "no title" forever, because MISS is what triggers
        # the fetch — and getting this membership test wrong the other way is how
        # a whole kind of row comes out blank and stays blank.
        if (rows[row].get("rowKind") not in self._SELF_SERVED_KINDS
                and self._search_in_database):
            return MISS
        return rows[row].get(_WEB_KEY.get(key, key))

    def row_value(self, row: int, key: str) -> Any:
        rows = self._active.rows
        if rows and row < len(rows):
            # An online result that is not in the library has no track id, and
            # neither does an artist. Report that as None rather than 0 so a GUI can tell
            # "not a library track" from "id 0".
            if key == "trackId":
                return self._ids[row] or None
            # ROW fields with no database behind them — `coverUrl` above all,
            # which is the only artwork an online result has, and the artist
            # fields.
            # These never reach ``local_value``: ``data()`` sends the whole ROW
            # tier here.
            if key in LOCAL_ROW_KEYS:
                value = rows[row].get(_WEB_KEY.get(key, key))
                # `rowKind` is the one field every row must answer, including the
                # track rows in a mixed list whose dict is empty. A delegate
                # branches on it, so leaving it null would make a track row
                # indistinguishable from one that had not loaded yet.
                if value is None and key == "rowKind":
                    return "track"
                return value
        return super().row_value(row, key)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @Property(int, notify=result_count_changed)
    def result_count(self) -> int:
        return len(self._ids)

    @Property(bool, notify=search_in_database_changed)
    def search_in_database(self) -> bool:
        """Whether the library is the source on screen."""
        return self._search_in_database

    @Property("QVariantList", notify=sources_changed)
    def sources(self) -> List[Dict[str, str]]:
        """The online sources the enabled plugins offer: ``[{key, label, icon, plugin}]``.

        Empty on a plain install, which is the GUI's cue to show no source
        picker at all. The library is not in it: it is always there, and its
        key is ``"library"``.
        """
        return [dict(source) for source in self._sources]

    def _get_current_source(self) -> str:
        return self._source

    def _set_current_source(self, key: str) -> None:
        key = str(key or "") or LIBRARY_SOURCE
        if key != LIBRARY_SOURCE and key not in {s["key"] for s in self._sources}:
            return
        if key == self._source:
            return
        self._source = key
        self._activate(self._active)
        self.search_in_database_changed.emit()
        self.search_results_changed.emit()
        self.result_count_changed.emit()

    currentSource = Property(  # noqa: N815 (Qt property, camelCase for QML)
        str, _get_current_source, _set_current_source, notify=search_in_database_changed
    )

    @Slot(str)
    def setSource(self, key: str) -> None:  # noqa: N802
        """Show *key*'s results — ``"library"`` or one of ``sources``."""
        self._set_current_source(key)

    def _read_sources(self) -> List[Dict[str, str]]:
        if self._registry is None:
            return []
        return [
            {"key": source.key, "label": source.label, "icon": source.icon,
             "plugin": source.plugin_id}
            for source in self._registry.track_sources()
        ]

    @Slot()
    def _on_providers_changed(self) -> None:
        """A plugin went on or off. Re-read the sources; fall back to the library.

        A source that has gone takes its results with it — there is nothing
        left that could fetch them — and if it was the one on screen the
        library takes its place, which is the one source that cannot vanish.
        """
        fresh = self._read_sources()
        if fresh == self._sources:
            self.refresh_identity_roles()
            return
        self._sources = fresh
        keys = {source["key"] for source in fresh}
        for key in list(self._source_states):
            if key not in keys:
                del self._source_states[key]
        self.sources_changed.emit()
        if self._source != LIBRARY_SOURCE and self._source not in keys:
            self._source = LIBRARY_SOURCE
            self._activate(self._db_state)
            self.search_in_database_changed.emit()
            self.search_results_changed.emit()
            self.result_count_changed.emit()
        else:
            self.refresh_identity_roles()

    @Property(bool, notify=is_searching_changed)
    def isSearching(self) -> bool:  # noqa: N802
        return self._is_searching

    @Property(int, notify=downloading_changed)
    def downloadingUid(self) -> int:  # noqa: N802
        """The row being downloaded right now, or -1 when idle.

        The one place a GUI should read a row's download state from. A row that
        latches its own spinner on click instead will lose it: these cells live
        in list delegates, and scrolling one out of the cache buffer and back
        destroys and rebuilds it with every local flag at its default. Read off
        here it cannot drift — the same reason `importVM.stashingUid` exists.

        Read-only by construction. There is no setter, so a GUI cannot claim or
        release the slot; only `downloadRow` and a thread ending move it.
        """
        return self._downloading_uid

    @Property(bool, notify=downloading_changed)
    def isDownloading(self) -> bool:  # noqa: N802
        """Whether a download is in flight. What greys out the other buttons."""
        return self._downloading_uid >= 0

    def _get_search_mode(self) -> str:
        return self._search_mode

    def _set_search_mode(self, mode: str) -> None:
        mode = str(mode or "").strip().lower()
        if mode not in self.SEARCH_MODES or mode == self._search_mode:
            return
        self._search_mode = mode
        self.search_mode_changed.emit()
        # Re-run rather than leaving the old shape on screen: the mode is a
        # question about the current query, not about the next one.
        if self._search_in_database and self._active.query:
            self.text_search(self._active.query)

    searchMode = Property(  # noqa: N815 (Qt property, camelCase for QML)
        str, _get_search_mode, _set_search_mode, notify=search_mode_changed
    )

    @Slot()
    def toggle_search_source(self) -> None:
        """Between the library and the first online source, for a GUI with one button."""
        if self._search_in_database and self._sources:
            self._set_current_source(self._sources[0]["key"])
        else:
            self._set_current_source(LIBRARY_SOURCE)

    def _activate(self, state: _SearchState) -> None:
        """Point the model at one source's rows."""
        self._selection = state.selection
        self.set_ids(state.ids, state.uids)

    # ------------------------------------------------------------------
    # Search
    # ------------------------------------------------------------------

    @Slot(str)
    def text_search(self, query: str) -> None:
        if not query.strip():
            self._set_results([])
            return
        if self._is_searching:
            return  # ignore a new search while one is running
        self._active.query = query

        if not self._search_in_database:
            self._start_web_search(self._source, query)
            return

        try:
            self._is_searching = True
            self.is_searching_changed.emit()
            if self._search_mode == self.MODE_ALBUMS and self._album_service:
                self._set_album_result(query)
            elif (self._search_mode in (self.MODE_TRACKS, self.MODE_ALBUMS)
                    or self._artist_service is None):
                # The untouched path. An id vector, no row data, exactly as
                # before — and where a mode whose service is missing lands, so an
                # incomplete registry answers with fewer kinds rather than none.
                self._set_ids_result(self._repo.quick_search_ids(query))
            else:
                self._set_mixed_result(query)
        except Exception as exc:
            self.error_occurred.emit(str(exc))
        finally:
            self._is_searching = False
            self.is_searching_changed.emit()

    def _set_mixed_result(self, query: str) -> None:
        """Library results as artists, tracks, or both ranked together.

        Artists come back as rows the model serves itself; tracks stay ids the
        row cache fills on paint. Both live in one vector, so a delegate sees one
        list and the selection, the play-all and the scrolling all keep working
        without knowing two kinds of row exist.
        """
        artists = self._artist_service.search(query, limit=50)
        tracks = (
            []
            if self._search_mode == self.MODE_ARTISTS
            else self._repo.quick_search_rows(query)
        )
        ordered = rank(artists, tracks, query)

        ids: List[int] = []
        rows: List[Dict[str, Any]] = []
        for entry in ordered:
            if entry["kind"] == "artist":
                artist = entry["artist"]
                ids.append(0)
                rows.append({
                    "rowKind": "artist",
                    "artistId": artist["id"],
                    "artistName": artist["name"],
                    "artistImageHash": artist["image_hash"],
                    "artistTrackCount": artist["track_count"],
                    # Mirrored onto the track fields a plain delegate already
                    # binds, so a GUI that has not learned about `rowKind` shows
                    # the artist's name rather than an empty row.
                    "title": artist["name"],
                    "coverHash": artist["image_hash"],
                })
            else:
                ids.append(entry["id"])
                # Empty: a track row has a database row behind it, and
                # `local_value` sends it to the cache rather than reading this.
                rows.append({})

        state = self._db_state
        state.ids = ids
        state.uids = self._mint_uids(len(ids))
        state.rows = rows
        state.selection.clear()
        if self._search_in_database:
            self._activate(state)
        self._announce()

    def _set_album_result(self, query: str) -> None:
        """Library results as albums, and only albums.

        No ranking pass. `rank()` exists to interleave two kinds of answer to one
        question; this mode has one kind, and the repository has already ordered
        it — exact name, then prefix, then contains, then catalogue size — by the
        same rule the artist search uses.

        Every id is 0, so `get_result_ids()` and `trackIds()` come back empty and
        "Play all" over a page of albums correctly offers nothing. There is no
        ambiguity to resolve there: an album is a route to a page, not a track to
        queue.
        """
        found = self._album_service.search(query, limit=50)

        ids: List[int] = [0] * len(found)
        rows: List[Dict[str, Any]] = [
            {
                "rowKind": "album",
                "albumId": album["id"],
                "albumName": album["name"],
                "albumCoverHash": album["cover_hash"],
                "albumTrackCount": album["track_count"],
                "albumIsComplete": bool(album.get("is_complete", False)),
                # Mirrored onto the track fields a plain delegate already binds,
                # the same bargain the artist rows make: a GUI that has not
                # learned about `rowKind` shows the record's name and sleeve
                # rather than an empty row.
                "title": album["name"],
                "coverHash": album["cover_hash"],
            }
            for album in found
        ]

        state = self._db_state
        state.ids = ids
        state.uids = self._mint_uids(len(ids))
        state.rows = rows
        state.selection.clear()
        if self._search_in_database:
            self._activate(state)
        self._announce()

    @Slot("QVariantList")
    def advanced_search(self, filters: list) -> None:
        try:
            self._is_searching = True
            self.is_searching_changed.emit()
            parsed = [f for f in (parse_filter(raw) for raw in filters) if f is not None]
            self._set_ids_result(self._repo.query_ids(parsed))
        except Exception as exc:
            self.error_occurred.emit(str(exc))
        finally:
            self._is_searching = False
            self.is_searching_changed.emit()

    @Slot()
    def load_all_tracks(self) -> None:
        """Show the whole library.

        Cheap now: it resolves to an id vector, and no row is read until it is
        painted.
        """
        self.advanced_search([])

    @Slot(result="QVariantList")
    def get_result_ids(self) -> list:
        """The library track ids of the current results."""
        return [track_id for track_id in self._ids if track_id]

    @Slot(int, result="QVariant")
    def webResult(self, uid: int):  # noqa: N802
        """The whole result dict behind an online row, or ``None``.

        An online result carries more than the roles expose — the artwork URLs
        have no catalog field at all, and the release date is not spelled
        ``date`` — and the import flow wants all of it. Handing the dict over
        whole is what lets a GUI say "this local file is that track" without the
        model growing a role per field.

        Reads every online state rather than the active one: the caller is
        asking about an online row, and switching the source to the library
        must not make this answer about a different list. A copy, so a GUI
        cannot edit the results out from under the model.
        """
        found = self._online_row(uid)
        return dict(found[1]) if found is not None else None

    def _online_row(self, uid: int) -> Optional[tuple[_SearchState, Dict[str, Any], int]]:
        for state in self._online_states():
            if uid in state.uids:
                index = state.uids.index(uid)
                return state, state.rows[index], index
        return None

    @Slot()
    def clear_results(self) -> None:
        self._set_results([])
        self._active.query = ""

    # ------------------------------------------------------------------
    # Applying results
    # ------------------------------------------------------------------

    def _set_ids_result(self, ids: List[int]) -> None:
        """Database results: an ordered id vector, no row data."""
        state = self._db_state
        state.ids = list(ids)
        state.uids = self._mint_uids(len(ids))
        state.rows = []
        state.selection.clear()
        if self._search_in_database:
            self._activate(state)
        self._announce()

    def _set_results(self, results: List[Dict[str, Any]], key: Optional[str] = None) -> None:
        """Online results: flat dicts the model serves itself."""
        state = self._active if key is None else self._source_states.setdefault(key, _SearchState())
        state.rows = list(results)
        state.ids = [int(r.get("id") or 0) for r in state.rows]
        state.uids = self._mint_uids(len(state.rows))
        state.selection.clear()
        if state is self._active:
            self._activate(state)
            self._announce()

    def _mint_uids(self, count: int) -> List[int]:
        first = self._next_uid
        self._next_uid += count
        return list(range(first, self._next_uid))

    def _announce(self) -> None:
        self.search_results_changed.emit()
        self.result_count_changed.emit()

    # ------------------------------------------------------------------
    # Online search
    # ------------------------------------------------------------------

    def _start_web_search(self, key: str, query: str) -> None:
        if self._web_search_thread is not None:
            return  # a previous search is still running
        source = self._registry.track_source(key) if self._registry is not None else None
        if source is None:
            self.error_occurred.emit("That source is no longer available")
            return
        self._is_searching = True
        self.is_searching_changed.emit()

        thread = QThread(self)
        worker = TrackSourceWorker(source, query)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_web_search_finished)
        worker.failed.connect(self._on_web_search_failed)
        own_worker(thread, worker, self._on_web_search_stopped)
        self._web_search_thread = thread
        self._web_search_worker = worker
        thread.start()

    @Slot(str, list)
    def _on_web_search_finished(self, key: str, results: list) -> None:
        # The source went while it was searching; its results have nowhere to go.
        if key not in {source["key"] for source in self._sources}:
            return
        # Annotate which results are already in the library.
        #
        # Through `find_owned`, which is the same question the import list asks
        # and asks it the same way: by every id that names the recording, and
        # then by the recording itself. A result the library holds from a
        # different release of the same record used to show as new here and as
        # owned in the import tab, off one press each — the badge and the list
        # now cannot disagree.
        for result, track_id in zip(results, self.track_service.find_owned(results)):
            result["id"] = track_id
        self._set_results(results, key)

    @Slot(str, str)
    def _on_web_search_failed(self, key: str, message: str) -> None:
        self.error_occurred.emit(f"Search failed: {message}")

    @Slot()
    def _on_web_search_stopped(self) -> None:
        self._web_search_thread = None
        self._web_search_worker = None
        self._is_searching = False
        self.is_searching_changed.emit()

    # ------------------------------------------------------------------
    # Event bus handlers
    # ------------------------------------------------------------------

    def _on_track_deleted(self, track_id: int) -> None:
        track_id = int(track_id)
        self._cache.invalidate(track_id)
        for state in (self._db_state, *self._online_states()):
            rows = [i for i, tid in enumerate(state.ids) if tid == track_id]
            if not rows:
                continue
            for row in reversed(rows):
                active = state is self._active
                if active:
                    self.beginRemoveRows(QModelIndex(), row, row)
                state.selection.set_selected(state.uids[row], False)
                state.ids.pop(row)
                state.uids.pop(row)
                if state.rows:
                    state.rows.pop(row)
                if active:
                    self._set_vectors(state.ids, state.uids)
                    self.endRemoveRows()
            self._announce()
            self.refresh_selection()

    # ------------------------------------------------------------------
    # Downloads
    # ------------------------------------------------------------------

    @Slot(int)
    def downloadRow(self, uid: int) -> None:  # noqa: N802
        """Start one download, if nothing else is downloading.

        **Downloads run one at a time, and the limit is enforced here.** Not in
        the GUI: a dimmed button is a courtesy, and this method is the only way
        to reach `FetchAudioWorker` from search, so it is where the limit has to
        hold however the call arrives.

        The reason is that every download ends by writing to the database
        through the one `Session` the whole application shares
        (`bootstrap.create_session`), and a SQLAlchemy session is not
        thread-safe. Two workers finishing at once can interleave their flushes:
        one commits the other's half-built track, `get_or_create` resolves the
        same artist twice, or the session raises mid-flush and a download that
        actually succeeded is reported as failed. The import pipeline reached
        the same conclusion about the same write path first; see the note atop
        `app.importer.import_workers`.

        Three ways in and three different answers, in this order:

        * the row already downloading — silence. Clicking it twice means the
          user wants what is already happening.
        * nothing can fetch it — `download_blocked`, with the reason, so the
          GUI can offer Settings → Plugins. Ahead of the busy check because that
          stays the honest answer whether or not something else is running.
        * something else downloading — `download_busy`, and nothing starts.
        """
        uid = int(uid)
        if uid < 0 or uid == self._downloading_uid:
            return
        found = self._online_row(uid)
        if found is None:
            return
        _state, row, _index = found

        # Refused here so no thread is ever started for a download that cannot
        # happen. The plugin host checks consent again on its own, immediately
        # before the request goes out.
        source = self._registry.audio_source_for(row) if self._registry is not None else None
        if source is None:
            self.download_blocked.emit(
                uid, "No enabled plugin can fetch this track. "
                     "Install or turn one on in Settings → Plugins.",
            )
            return
        if not self._registry.has_consent(source.plugin_id):
            self.download_blocked.emit(uid, f"{source.name} needs you to agree to its terms first.")
            return

        if self._downloading_uid >= 0:
            self.download_busy.emit(uid)
            return

        # The result row this download came from already knows every credited
        # artist and their identities — the fetched file may only know the
        # primary's. It is sitting in memory either way, so handing it over
        # costs nothing and identifies the whole credit rather than a third of
        # it. Added only when non-empty: `fetch_and_stash` counts any non-empty
        # value as a win and an empty *list* passes that test, so `[]` here
        # would erase what the source did supply.
        override: Optional[Dict[str, Any]] = (
            {"artist_refs": row["artist_refs"]} if row.get("artist_refs") else None
        )

        thread = QThread(self)
        worker = FetchAudioWorker(
            uid, row, self.track_service, self._registry, self.cover_service,
            metadata_override=override,
            lyrics_service=self._lyrics_service,
        )
        worker.moveToThread(thread)

        thread.started.connect(worker.run)
        worker.finished.connect(self._on_download_worker_finished)
        worker.failed.connect(self._on_download_worker_failed)
        worker.done.connect(self._on_download_worker_done)
        worker.progress.connect(self.download_progress)
        own_worker(thread, worker, self._on_download_thread_stopped)

        # Claimed before the thread exists, so there is no window in which the
        # work has started but the slot still reads free. Everything from here
        # to `_on_download_thread_stopped` runs on the main thread — this slot
        # is called from QML, and the worker's signals cross back queued — so
        # the check above and this claim cannot be split by another caller.
        self._downloading_uid = uid
        self._download_thread = thread
        self._download_worker = worker
        thread.start()
        # Last, so nothing handling it can observe a claim without a thread.
        self.downloading_changed.emit()

    @Slot(int, int)
    def _on_download_worker_finished(self, uid: int, track_id: int) -> None:
        found = self._online_row(uid)
        if found is not None:
            state, result, row = found
            result["id"] = track_id
            state.ids[row] = int(track_id)
            if state is self._active:
                self._set_vectors(state.ids, state.uids)
                self.dataChanged.emit(self.index(row), self.index(row))
        self.download_completed.emit(uid, track_id)

    @Slot(int, str)
    def _on_download_worker_failed(self, uid: int, error_message: str) -> None:
        self.download_failed.emit(uid, error_message)

    @Slot(int)
    def _on_download_worker_done(self, uid: int) -> None:
        """This download is over. Its thread may not be — see `own_worker`."""
        self.download_finished.emit(uid)

    @Slot()
    def _on_download_thread_stopped(self) -> None:
        """The download's thread has gone. Release it, and free the slot.

        ``QThread.finished`` carries nothing to say which download ended, and
        does not need to: there is only ever one.

        Freeing the slot here rather than on the worker's ``done`` is what makes
        the limit mean what `downloadRow` says it means. ``done`` is emitted
        from inside ``run``, with the thread still alive — releasing there would
        let the next download start alongside a worker that has not finished
        unwinding. By ``finished`` the previous worker is genuinely gone, so no
        two of them are ever live at once, whatever they were doing.

        This is also where the references are dropped, which they may not be any
        earlier; see :mod:`app.viewmodels.worker_threads` for what goes wrong.
        """
        self._download_thread = None
        self._download_worker = None
        if self._downloading_uid >= 0:
            self._downloading_uid = -1
            self.downloading_changed.emit()

    def shutdown(self) -> None:
        """Ask a download in flight to stop, before the window goes."""
        if self._download_worker is not None:
            self._download_worker.cancel()
        if self._web_search_worker is not None:
            self._web_search_worker.cancel()
