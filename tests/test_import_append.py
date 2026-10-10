"""Imports add to the import list; they do not replace it.

The bug: every finished match run did ``self._results = results``. Import one
folder, look through what matched, then import a second folder — and the first
folder was gone, along with whatever had been selected in it. The same happened
to rows paired by hand through ``importFileAs``, which appended perfectly well
right up until the next scan finished and threw them away.

So the list now accumulates, and five things follow from that, each covered
here:

* a run **appends**, keeping earlier rows, their uids, and their selection, and
  the header's totals count the whole list rather than the last run;
* a row already in the list is **skipped**, by its file before the match run —
  because matching costs one network call per file and a re-imported folder
  should cost nothing — and by the track it turned out to be at the merge,
  which is the only point at which a local file has an id to be compared by;
* what a row does when saved — stash a local file, or download a match — is
  stamped on the **row**, not read off the view model, because rows now outlive
  the run that made them, and is exposed as the `isDownload` role so the row
  draws itself from the same fact;
* a change of kind is **not** a change of list. A folder, a CSV and a pasted
  link gather into one batch, and the header describes it through
  `pendingDownloadCount` / `pendingStashCount` rather than through the run;
* an append **says where it landed**, through `rows_appended(first, count)`.
  Keeping the scroll position is what makes new rows invisible — they arrive
  below the fold — and no other signal carries the index a GUI would need to
  scroll to them.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PySide6.QtCore import Property, QModelIndex, QObject, Signal

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.importer.media_probe import ProbedFile  # noqa: E402
from app.library.roles import FIELD_TO_ROLE  # noqa: E402
from app.viewmodels.import_view_model import ImportViewModel  # noqa: E402
from tests.conftest import OwnedByIdOnly  # noqa: E402
from tests.fakes.providers import FakeAudio, registry_for  # noqa: E402
from tests.fakes.providers import VIDEO, from_legacy  # noqa: E402


# -- doubles ---------------------------------------------------------------


class _StubImportService(OwnedByIdOnly):
    def __init__(self, existing=None):
        self.existing = existing or {}

    def existing_ids(self, ids):
        return {
            yid: self.existing[yid]
            for yid in ids
            if yid and yid in self.existing
        }


class _StubDownloads:
    def __init__(self, allowed: bool = True) -> None:
        self.allowed = allowed


class _StubImports:
    """Rows that reach the library stay listed.

    Appending is about how a second batch joins a list that is already there,
    which needs the list to keep what it is given. The shipped default takes an
    already-imported row off instead — see tests/test_import_clear.py.
    """
    clear_imported = False


class _StubPlayer(QObject):
    """Stands in for `playerVM` and `queueVM` when a TrackTable is built.

    The delegate reads these to decide whether a row is the playing one. It
    needs answers, not behaviour — a missing context property would leave every
    binding that touches it undefined and the row half-built.
    """

    changed = Signal()

    @Property(bool, notify=changed)
    def has_current_track(self) -> bool:
        return False

    @Property(int, notify=changed)
    def current_track_id(self) -> int:
        return -1

    @Property(bool, notify=changed)
    def is_playing(self) -> bool:
        return False


class _RecordingEngine:
    """Never actually called — `_start_search` is what these tests watch.

    The worker is real, so a test that lets a search start would go to the
    network. Every test here either stops before the worker is built or drives
    `_on_search_finished` directly.
    """

    def match_file(self, probed, use_filename_fallback):  # pragma: no cover
        raise AssertionError("no test here should reach the match engine")


@pytest.fixture
def vm(qt_app):
    return ImportViewModel(
        import_service=_StubImportService(),
        track_service=None,
        registry=registry_for(_RecordingEngine(), audio=FakeAudio()),
        import_settings=_StubImports(),
    )


# -- helpers ---------------------------------------------------------------


def _read(vm, row, key):
    return vm.data(vm.index(row, 0, QModelIndex()), FIELD_TO_ROLE[key])


def _row_legacy(title, *, path=None, source_id=None, not_found=False):
    """One entry in the list a match worker emits."""
    return {
        "title": title,
        "artist": "A",
        "album": "",
        "duration": 100,
        "source_id": "" if not_found else (source_id or f"vid-{title}"),
        "cover_url": "",
        "cover_master_url": "",
        "source_path": path or f"/music/{title}.opus",
        "release_date": None,
        "not_found": not_found,
    }


def _row(*args, **kwargs):
    """`_row_legacy`, as a core row — see `tests.fakes.providers.from_legacy`."""
    return from_legacy(_row_legacy(*args, **kwargs))


def _csv_row_legacy(title, source_id=None, *, not_found=False):
    """A row from a CSV or a pasted link: an id and metadata, but no file."""
    return {**_row(title, source_id=source_id, not_found=not_found),
            "source_path": ""}


def _csv_row(*args, **kwargs):
    """`_csv_row_legacy`, as a core row — see `tests.fakes.providers.from_legacy`."""
    return from_legacy(_csv_row_legacy(*args, **kwargs))


def _probed(title, path=None):
    return ProbedFile(
        path=Path(path or f"/music/{title}.opus"), title=title, artist="A"
    )


def _csv_probed(title, source_id):
    """A CSV candidate: metadata and an id, but no file behind it."""
    return ProbedFile(
        path=None, title=title, artist="A", external_ids={VIDEO: source_id},
        from_csv=True,
    )


# ======================================================================
# A run appends
# ======================================================================


def test_a_second_run_keeps_the_first_runs_rows(vm):
    vm._on_search_finished([_row("One"), _row("Two")])
    vm._on_search_finished([_row("Three")])

    assert vm.result_count == 3
    assert [_read(vm, i, "title") for i in range(3)] == ["One", "Two", "Three"]


def test_uids_stay_unique_across_runs(vm):
    """Uid is the selection key and the stash queue's handle; a reuse is a bug."""
    vm._on_search_finished([_row("One"), _row("Two")])
    vm._on_search_finished([_row("Three"), _row("Four")])

    uids = [r["uid"] for r in vm._results]
    assert len(set(uids)) == 4


def test_the_headers_totals_count_the_whole_list(vm):
    """What "Import all" is about to do is the list, not the last run."""
    vm = ImportViewModel(
        import_service=_StubImportService({"vid-Known": 42}),
        track_service=None,
        registry=registry_for(_RecordingEngine(), audio=FakeAudio()),
        import_settings=_StubImports(),
    )
    vm._on_search_finished([_row("One"), _row("Missing", not_found=True)])
    assert (vm.foundCount, vm.notFoundCount, vm.inDatabaseCount) == (1, 1, 0)

    vm._on_search_finished([_row("Known"), _row("Gone", not_found=True)])

    assert vm.foundCount == 2
    assert vm.notFoundCount == 2
    assert vm.inDatabaseCount == 1


def test_a_selection_survives_the_next_run(vm):
    """An insert, not a reset: the list the user has been building is theirs."""
    vm._on_search_finished([_row("One"), _row("Two")])
    kept = vm.uidAt(0)
    vm.setSelected(kept, True)

    vm._on_search_finished([_row("Three")])

    assert vm.uidAt(0) == kept
    assert _read(vm, 0, "isSelected") is True
    assert _read(vm, 2, "isSelected") is False


def test_a_hand_paired_row_survives_a_scan(vm, tmp_path):
    """The case that broke first: importFileAs appended, then a scan wiped it."""
    paired = tmp_path / "track03.mp3"
    paired.write_bytes(b"not really audio")
    vm.importFileAs(str(paired), {"title": "Paired", "source_id": "vidpaired1"})

    vm._on_search_finished([_row("Scanned")])

    assert vm.result_count == 2
    assert _read(vm, 0, "title") == "Paired"
    assert _read(vm, 1, "title") == "Scanned"


def test_an_empty_run_leaves_the_list_alone(vm):
    """A worker can come back with nothing; `beginInsertRows` must not see it."""
    vm._on_search_finished([_row("One")])

    vm._on_search_finished([])

    assert vm.result_count == 1
    assert vm.hasResults is True


# ======================================================================
# An append says where it landed
# ======================================================================


def _appends(vm):
    """Every `rows_appended` the vm raises from here on, as (first, count)."""
    seen: list[tuple[int, int]] = []
    vm.rows_appended.connect(lambda first, count: seen.append((first, count)))
    return seen


def test_a_run_says_where_its_rows_landed(vm):
    """The whole point: a list that accumulates puts arrivals below the fold.

    `search_completed` says a run finished, not what it left behind or where,
    so a GUI wanting to point at the new rows has nothing else to ask.
    """
    seen = _appends(vm)

    vm._on_search_finished([_row("One"), _row("Two")])
    vm._on_search_finished([_row("Three")])

    assert seen == [(0, 2), (2, 1)]


def test_a_first_batch_reports_itself_too(vm):
    """Whether an arrival onto an empty list is worth pointing out is the GUI's
    question; the signal states the fact and lets the answer live there."""
    seen = _appends(vm)

    vm._on_search_finished([_row("One")])

    assert seen == [(0, 1)]


def test_a_hand_paired_row_says_where_it_landed(vm, tmp_path):
    """`importFileAs` raises no `search_completed` at all, so without this there
    is nothing to tell a GUI that the row it just asked for exists."""
    paired = tmp_path / "track03.mp3"
    paired.write_bytes(b"not really audio")
    vm._on_search_finished([_row("One"), _row("Two")])
    seen = _appends(vm)

    vm.importFileAs(str(paired), {"title": "Paired", "source_id": "vidpaired1"})

    assert seen == [(2, 1)]


def test_a_run_that_appends_nothing_says_nothing(vm):
    """Raised beside the insert, so a run with no rows to insert never gets there."""
    vm._on_search_finished([_row("One")])
    seen = _appends(vm)

    vm._on_search_finished([])
    vm._on_search_finished([_row("One")])  # a repeat: dropped at the merge

    assert seen == []
    assert vm.result_count == 1


def test_filling_a_row_in_is_not_an_append(vm, tmp_path):
    """`_upgrade_unmatched` answers a row that was already there — nothing
    arrived, so nothing should be pointed at."""
    paired = tmp_path / "track03.mp3"
    paired.write_bytes(b"not really audio")
    vm._on_search_finished([_row("Nameless", path=str(paired), not_found=True)])
    seen = _appends(vm)

    vm.importFileAs(str(paired), {"title": "Named", "source_id": "vidpaired1"})

    assert vm.result_count == 1, "the row was filled in, not duplicated"
    assert _read(vm, 0, "title") == "Named"
    assert seen == []


# ======================================================================
# Repeats are skipped, and skipped early
# ======================================================================


def test_a_file_already_listed_never_reaches_the_match_engine(vm):
    """The saving that matters: no network call for a folder already imported."""
    vm._on_search_finished([_row("One", path="/music/one.opus")])

    kept, skipped = vm._without_listed(
        [_probed("One", "/music/one.opus"), _probed("Two", "/music/two.opus")]
    )

    assert [p.title for p in kept] == ["Two"]
    assert skipped == 1


def test_a_run_of_nothing_but_repeats_never_starts(vm):
    """Said plainly, rather than through the file-type menu's error."""
    vm._on_search_finished([_row("One", path="/music/one.opus")])
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)

    vm._start_search([_probed("One", "/music/one.opus")])

    assert vm._search_thread is None, "no worker for a run with nothing to do"
    assert vm.isSearching is False
    assert errors == ["Every file selected is already in the import list."]
    assert vm.duplicatesSkipped == 1


def test_an_unmatched_row_is_still_matched_by_its_file(vm):
    """A file that matched nothing has no id, so the path is what identifies it."""
    vm._on_search_finished(
        [_row("One", path="/music/one.opus", not_found=True)]
    )

    kept, skipped = vm._without_listed([_probed("One", "/music/one.opus")])

    assert kept == []
    assert skipped == 1


def test_a_csv_row_is_identified_by_its_source_id(vm):
    """CSV candidates have no file at all; the id is all there is to compare."""
    vm._set_download_mode(True)
    vm._on_search_finished([_row("One", source_id="vidcsv0001")])
    vm._results[0]["source_path"] = ""

    kept, skipped = vm._without_listed(
        [_csv_probed("One", "vidcsv0001"), _csv_probed("Two", "vidcsv0002")]
    )

    assert [p.title for p in kept] == ["Two"]
    assert skipped == 1


def test_pairing_the_same_file_twice_is_refused(vm, tmp_path):
    """Both rows would stash the same audio, under two different names."""
    paired = tmp_path / "track03.mp3"
    paired.write_bytes(b"not really audio")
    vm.importFileAs(str(paired), {"title": "First", "source_id": "vidweb0001"})
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)

    vm.importFileAs(str(paired), {"title": "Second", "source_id": "vidweb0002"})

    assert vm.result_count == 1
    assert errors == ["That file is already in the import list."]


def test_the_run_reports_its_own_numbers_not_the_lists(vm):
    """The toast says "matched N of M" about the run; the header covers the list."""
    vm._on_search_finished([_row("One"), _row("Two")])

    vm._on_search_finished([_row("Three"), _row("Gone", not_found=True)])

    assert vm.foundCount == 3, "the list"
    assert vm.lastMatchedCount == 1, "the run"


# ======================================================================
# A change of kind is not a change of list
# ======================================================================


def test_a_csv_run_joins_a_list_of_local_rows(vm):
    """The arriving kind adds to the list; it no longer takes it."""
    vm._on_search_finished([_row("Local One"), _row("Local Two")])
    kept_uid = vm.uidAt(0)
    vm.setSelected(kept_uid, True)

    vm._set_download_mode(True)
    vm._on_search_finished([_csv_row("From CSV", "vidcsv0001")])

    assert vm.result_count == 3
    assert [_read(vm, i, "title") for i in range(3)] == \
        ["Local One", "Local Two", "From CSV"]
    assert vm.foundCount == 3, "the earlier rows' totals stayed with them"
    assert vm.uidAt(0) == kept_uid, "an insert, not a reset"
    assert vm._selected_ids == {kept_uid}, "the selection survived the CSV"


def test_what_a_row_does_when_saved_is_stamped_on_the_row(vm):
    """Two kinds in one list are each judged, gated and saved on their own terms.

    Built through the supported flow now, rather than by hand: producing this
    list is the feature.
    """
    vm._on_search_finished([_row("Local")])
    vm._set_download_mode(True)
    vm._on_search_finished([_csv_row("Downloaded", "vidcsv0001")])

    local, from_csv = vm._results[0], vm._results[1]
    assert local["is_download"] is False
    assert from_csv["is_download"] is True
    assert vm._is_stashable(local) and vm._is_stashable(from_csv)

    # A download needs an audio source; a local file on disk never does.
    vm._registry.set(audio=None)
    assert vm._blocked_reason(from_csv)
    assert vm._blocked_reason(local) == ""


def test_a_row_says_which_kind_it_is_through_a_role(vm):
    """What the status cell draws its button from — the row, not the page."""
    vm._on_search_finished([_row("Local")])
    vm._set_download_mode(True)
    vm._on_search_finished([_csv_row("Downloaded", "vidcsv0001")])

    assert _read(vm, 0, "isDownload") is False
    assert _read(vm, 1, "isDownload") is True


# ======================================================================
# What the header says about a list of more than one kind
# ======================================================================


def test_the_pending_counts_split_the_list_by_kind(vm):
    vm._on_search_finished([_row("Local One"), _row("Local Two")])
    vm._set_download_mode(True)
    vm._on_search_finished([_csv_row("From CSV", "vidcsv0001")])

    assert (vm.pendingDownloadCount, vm.pendingStashCount) == (1, 2)


def test_a_saved_row_is_no_longer_pending(vm):
    """The counts are what the button would act on, so a finished row is out."""
    vm._on_search_finished([_row("One"), _row("Two")])
    assert vm.pendingStashCount == 2

    vm._results[0]["id"] = 42

    assert vm.pendingStashCount == 1


def test_a_row_that_cannot_be_saved_is_not_pending_either(vm):
    """`_is_stashable` decides, so the count cannot promise what a press skips."""
    vm._set_download_mode(True)
    vm._on_search_finished([_csv_row("Gone", not_found=True)])

    assert vm.result_count == 1
    assert vm.pendingDownloadCount == 0, "no id to fetch"


# ======================================================================
# Two kinds, one recording: the duplicate a mixed list makes possible
# ======================================================================


def test_a_csv_row_for_a_track_already_matched_is_skipped(vm):
    """Otherwise the library gains it twice — filed once and fetched once."""
    vm._on_search_finished([_row("One", source_id="vidshared01")])

    kept, skipped = vm._without_listed([_csv_probed("One", "vidshared01")])

    assert kept == []
    assert skipped == 1


def test_a_file_that_matches_a_listed_track_is_dropped_at_the_merge(vm):
    """A local candidate has no id to be compared by until it has been matched.

    So this one cannot be caught before the run, the way a CSV row is — the
    match is what reveals that the file is a copy of something already listed.
    """
    vm._set_download_mode(True)
    vm._on_search_finished([_csv_row("One", "vidshared01")])
    vm._set_download_mode(False)

    vm._on_search_finished([_row("A Copy", path="/music/copy.opus",
                                 source_id="vidshared01")])

    assert vm.result_count == 1
    assert vm.foundCount == 1
    assert vm.duplicatesSkipped == 1, "said out loud, not silently dropped"


def test_two_files_of_one_recording_in_one_run_collapse(vm):
    """The first keeps the place; nothing needs to have been listed before."""
    vm._on_search_finished([
        _row("One", path="/music/a.opus", source_id="vidsame0001"),
        _row("Again", path="/music/b.opus", source_id="vidsame0001"),
    ])

    assert vm.result_count == 1
    assert _read(vm, 0, "title") == "One"
    assert vm.duplicatesSkipped == 1


def test_unmatched_rows_are_never_collapsed_together(vm):
    """They share an empty id, which is not a thing two rows can share."""
    vm._on_search_finished([
        _row("One", path="/music/a.opus", not_found=True),
        _row("Two", path="/music/b.opus", not_found=True),
    ])

    assert vm.result_count == 2
    assert vm.duplicatesSkipped == 0


def test_stash_all_is_stopped_by_the_one_row_that_needs_the_network(vm):
    """Asked of the rows, so a mixed list would still be refused as a batch."""
    vm._set_download_mode(True)
    vm._on_search_finished([_row("Downloaded", source_id="vidcsv0001")])
    vm._registry.set(audio=None)
    blocked: list[int] = []
    vm.download_blocked.connect(lambda uid, _reason: blocked.append(uid))

    vm.stashAll()

    assert blocked == [-1]
    assert vm._stash_queue == []


def test_an_unmatched_csv_row_is_not_offered_as_importable(vm):
    """It has no id to download, so no answer the user gives changes anything."""
    vm._set_download_mode(True)
    vm._on_search_finished([_row("Gone", not_found=True)])

    assert vm.notFoundCount == 1
    assert vm.unmatchedImportableCount == 0


# ======================================================================
# Clearing still clears
# ======================================================================


def test_done_still_empties_everything(vm):
    """"Done" is the way out, and it has to leave nothing behind for the next go."""
    vm._on_search_finished([_row("One"), _row("Two")])
    vm.setSelected(vm.uidAt(0), True)

    vm.clearResults()

    assert vm.result_count == 0
    assert vm.hasResults is False
    assert (vm.foundCount, vm.notFoundCount, vm.inDatabaseCount) == (0, 0, 0)
    assert (vm.lastMatchedCount, vm.duplicatesSkipped) == (0, 0)
    assert vm._selected_ids == set()
    assert vm.isDownloadMode is False


def test_a_cleared_list_takes_a_repeat_of_what_it_held(vm):
    """The skip is about what is *on screen*, not everything ever imported."""
    vm._on_search_finished([_row("One", path="/music/one.opus")])
    vm.clearResults()

    kept, skipped = vm._without_listed([_probed("One", "/music/one.opus")])

    assert len(kept) == 1
    assert skipped == 0


# ======================================================================
# The row draws itself from the state the backend gave it
# ======================================================================
#
# The status cell used to keep its own copy of `_is_stashable` in QML so a row
# could grey its button out without a round trip. Two statements of one rule,
# and nothing to notice when they drifted. There is one classifier now —
# `_state` — and this is what holds the cell to it: for every state, does the
# cell offer a button exactly when the backend would accept one?


def test_the_cell_offers_a_button_exactly_when_the_backend_accepts_one(
    qt_app, vm, tmp_path
):
    """All six states, asked of both halves.

    The states with no button are as much of the point as the ones with: an
    enabled control that silently does nothing is the failure this replaces.
    """
    from PySide6.QtCore import QUrl
    from PySide6.QtQml import QQmlApplicationEngine

    from app.gui.gui_paths import bundled_gui_root
    from app.viewmodels.import_view_model import ALL_STATES, SAVEABLE_STATES

    expected = [state in SAVEABLE_STATES for state in ALL_STATES]

    # -- the backend. Built from rows rather than asserted against the constant,
    # so a state that `_state` can no longer produce fails here.
    rows = {
        "inLibrary": {**_row("Done"), "is_download": False, "id": 7},
        "download": {**_csv_row("Fetch", "vidfetch0001"), "is_download": True},
        "stash": {**_row("Filed"), "is_download": False},
        "tagsOnly": {**_row("Tagged", not_found=True), "is_download": False},
        "needsMetadata": {**_row("Bare", not_found=True), "is_download": False,
                          "title": "", "artist": ""},
        "unavailable": {**_csv_row("Gone", not_found=True), "is_download": True},
    }
    assert [vm._state(rows[s]) for s in ALL_STATES] == list(ALL_STATES), \
        "a state the classifier no longer produces"
    assert [vm._is_stashable(rows[s]) for s in ALL_STATES] == expected

    # -- the cell
    nocturne = bundled_gui_root() / "nocturne"
    components = QUrl.fromLocalFile(str(nocturne / "components")).toString()
    cells = "\n".join(
        f"""
            property var cell{i}: TrackStatusCell {{
                statusMode: "import"; uid: {i}
                importState: "{state}"
            }}
        """
        for i, state in enumerate(ALL_STATES)
    )
    source = tmp_path / "Probe.qml"
    source.write_text(
        f"""
        import QtQuick
        import Nocturne
        import "{components}"

        Item {{
        {cells}

            // A glyph name Icons does not know renders as nothing at all, with
            // no warning — so the names are checked against the set rather than
            // merely for being non-empty.
            property var unknownIcons: {{
                var missing = [];
                for (var i = 0; i < {len(ALL_STATES)}; i++) {{
                    var c = this["cell" + i];
                    if (c && !Icons.has(c.stateIcon))
                        missing.push(c.importState + ":" + c.stateIcon);
                }}
                return missing;
            }}
        }}
        """,
        encoding="utf-8",
    )

    engine = QQmlApplicationEngine()
    engine.addImportPath(str(nocturne))
    engine.load(QUrl.fromLocalFile(str(source)))
    roots = engine.rootObjects()
    assert roots, "probe failed to load"

    cells_out = [roots[0].property(f"cell{i}") for i in range(len(ALL_STATES))]
    assert [c.property("actionable") for c in cells_out] == expected

    # Every state says what it is, including — especially — the two with no
    # button, where the tooltip is the only thing that can explain itself.
    assert all(c.property("stateTooltip") != "" for c in cells_out)
    # A QML `var` list crosses as a QJSValue, which is neither a list nor
    # iterable until it is converted.
    assert roots[0].property("unknownIcons").toVariant() == []

    del engine


def test_every_track_list_answers_for_the_new_roles(qt_app):
    """The delegate requires them, and the delegate is shared.

    `TrackTable` draws the queue, the library, playlists and search results as
    well as this list, and a required property that a model has no role for is a
    delegate that will not build. They all report the one catalog, so adding a
    field to it adds the role everywhere — and every list that is not an import
    answers None, exactly as it already does for `notFound`.
    """
    from app.library.list_model import TrackListModel
    from app.library.roles import ROLE_NAMES
    from app.library.selection import InMemorySelection

    names = set(ROLE_NAMES.values())
    for key in (b"isDownload", b"importState", b"sourceFormat"):
        assert key in names

    plain = TrackListModel(row_cache=object(), selection=InMemorySelection())
    plain.set_ids([7])
    for key in ("isDownload", "importState", "sourceFormat", "notFound"):
        assert plain.row_value(0, key) is None


# ======================================================================
# The list scrolls to what arrived
# ======================================================================
#
# `rows_appended` tells the GUI where the new rows are; `_revealTargetY` is
# what the GUI does with that — where the list has to sit for the first new
# row to read as the top of the batch. It is arithmetic over the viewport, and
# the interesting half is what happens when there is not enough list below the
# new rows to lift them: no scroll position puts them at the top, so the view
# goes as far as it can and they stay at the bottom.
#
# A real Window, unlike the probe above: a ListView with nowhere to draw never
# creates a delegate, and its contentHeight stays a per-item *estimate* (100px,
# Qt's default) rather than a measurement — which is exactly the number this
# arithmetic turns on. Offscreen is enough; see conftest.


def test_the_list_scrolls_so_the_new_rows_read_as_the_top(qt_app, vm, tmp_path):
    """Asked of the shape of the answer, not by restating the formula."""
    from PySide6.QtCore import QMetaObject, QTimer, QUrl
    from PySide6.QtQml import QQmlApplicationEngine

    from app.gui.gui_paths import bundled_gui_root

    # Long enough that the viewport cannot swallow it whatever size the
    # platform hands the window — the offscreen plugin sizes it from the
    # virtual screen and ignores what is asked for, so the height is read back
    # below rather than assumed.
    count = 400
    vm._on_search_finished([_row(f"T{i}") for i in range(count)])

    nocturne = bundled_gui_root() / "nocturne"
    components = QUrl.fromLocalFile(str(nocturne / "components")).toString()
    probes = [0, 1, 5, 20, count - 3, count - 1]
    source = tmp_path / "Probe.qml"
    source.write_text(
        f"""
        import QtQuick
        import QtQuick.Window
        import Nocturne
        import "{components}"

        Window {{
            visible: true
            property var out: []
            property real rh: table.rowHeight
            property real viewH: table.height
            function compute() {{
                var probes = {probes}
                var r = []
                for (var i = 0; i < probes.length; i++)
                    r.push(table._revealTargetY(probes[i]))
                out = r
            }}
            TrackTable {{
                id: table
                anchors.fill: parent
                mode: "library"; listVM: importVM
                statusMode: "import"; active: true
            }}
        }}
        """,
        encoding="utf-8",
    )

    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("importVM", vm)
    engine.rootContext().setContextProperty("playerVM", _StubPlayer())
    engine.rootContext().setContextProperty("queueVM", _StubPlayer())
    engine.addImportPath(str(nocturne))
    engine.load(QUrl.fromLocalFile(str(source)))
    roots = engine.rootObjects()
    assert roots, "probe failed to load"

    # Let the view lay out and build its delegates; contentHeight is only a
    # measurement once it has.
    QTimer.singleShot(400, qt_app.quit)
    qt_app.exec()
    QMetaObject.invokeMethod(roots[0], "compute")

    rh = roots[0].property("rh")
    view_h = roots[0].property("viewH")
    out = roots[0].property("out").toVariant()
    at_top, second, mid_a, mid_b, near_end, last = out
    furthest = count * rh - view_h
    assert furthest > 20 * rh, "the probes below need a list taller than the view"

    # Never off either end of the list.
    assert all(0 <= y <= furthest for y in out), out

    # One row of lead-in, so the seam between old rows and new ones is on
    # screen: the row *before* the first new one sits at the top.
    assert mid_a == 4 * rh
    assert mid_b == 19 * rh

    # Row 0 has no row before it to lead in with, and row 1's lead-in is row 0,
    # so both are simply the top of the list.
    assert at_top == 0
    assert second == 0

    # The case the clamp is for. There is no scroll position that raises these
    # rows to the top, so the view saturates — and saturates at the same place
    # for two different rows, which is what "as far as it can go" means.
    assert near_end == furthest
    assert last == furthest

    del engine
