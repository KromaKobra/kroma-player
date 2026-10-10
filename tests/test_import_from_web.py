"""Pairing a local file with an online search result.

The ordinary import guesses what a file is: it reads the tags, asks the identity
provider about whatever they said, and hopes. For a file with no tags and a filename like
``track03.mp3`` there is nothing to search with, so it matches nothing and
imports with no album, no artwork and no release date — even when the user knows
exactly which song it is and can see it in an online search.

This is the other direction. The user points at the result, hands over the file,
and the row is *built* from that result rather than searched for. Two things make
it work, and both are tested here.

``SearchViewModel.webResult`` hands the result dict over whole, because two of
its fields cannot be reached any other way: ``artwork_urls`` has no catalog
field at all, and the release date is not spelled ``date``. Without those, the
imported track would arrive with a thumbnail for artwork and no year.

``ImportViewModel.importFileAs`` puts one such row into the results list. What
matters is that it is indistinguishable from a matched row afterwards — matched,
stashable, and stashed by the same worker — and that it *appends*, so several
web results can be paired with several files before pressing Import all.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QModelIndex, QUrl

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.library.roles import FIELD_TO_ROLE  # noqa: E402
from app.viewmodels.import_view_model import ImportViewModel  # noqa: E402
from tests.conftest import FindsOwnedByIdOnly, OwnedByIdOnly  # noqa: E402
from app.viewmodels.search_view_model import SearchViewModel  # noqa: E402
from tests.fakes.providers import (  # noqa: E402
    VIDEO, FakeAudio, FakeRegistry, FakeTrackSource, from_legacy, registry_for,
)


COVER_URL = "https://img.fake.example/thumb=w120"
COVER_MASTER_URL = "https://img.fake.example/thumb=w1280"

# One online search result, as a core row.
WEB_RESULT = from_legacy({
    "title": "Web Track",
    "artist": "Web Artist",
    "album": "Web Album",
    "duration": 210,
    "source_id": "vidweb0001",
    "cover_url": COVER_URL,
    "cover_master_url": COVER_MASTER_URL,
    "source_path": None,
    "release_date": 20200304,
})


# ======================================================================
# searchVM.webResult — the fields no role exposes
# ======================================================================


class _StubTrackService(FindsOwnedByIdOnly):
    def existing_ids(self, values):
        return {}


@pytest.fixture
def web_vm(repo, cache, event_bus):
    """A SearchViewModel showing one online result."""
    source = FakeTrackSource()
    vm = SearchViewModel(
        track_repository=repo,
        registry=FakeRegistry(sources=[source]),
        event_bus=event_bus,
        track_service=_StubTrackService(),
        row_cache=cache,
    )
    vm.toggle_search_source()
    assert not vm.search_in_database
    vm._on_web_search_finished(source.key, [dict(WEB_RESULT)])
    return vm


def _uid(vm, row=0):
    return vm.data(vm.index(row, 0, QModelIndex()), FIELD_TO_ROLE["uid"])


def test_web_result_carries_the_fields_no_role_can(web_vm):
    """The two reasons this slot exists at all."""
    result = web_vm.webResult(_uid(web_vm))

    assert result is not None
    assert COVER_MASTER_URL in result["artwork_urls"], "no catalog field for it"
    assert result["release_date"] == 20200304, "the role is called `date`"


def test_web_result_carries_everything_the_import_needs(web_vm):
    result = web_vm.webResult(_uid(web_vm))

    for key in ("title", "artist", "album", "duration", "external_ids", "cover_url"):
        assert result[key] == WEB_RESULT[key]


def test_web_result_is_a_copy(web_vm):
    """Editing what a GUI was handed must not edit the results."""
    uid = _uid(web_vm)
    web_vm.webResult(uid)["title"] = "Vandalised"

    assert web_vm.webResult(uid)["title"] == "Web Track"


def test_an_unknown_uid_has_no_result(web_vm):
    assert web_vm.webResult(999_999) is None


def test_library_rows_have_no_web_result(web_vm):
    """A uid minted for a database row is not a web row, whichever source is up."""
    web_vm.toggle_search_source()
    web_vm.load_all_tracks()
    db_uid = _uid(web_vm)

    assert web_vm.webResult(db_uid) is None


def test_the_web_result_survives_toggling_the_source(web_vm):
    """It reads the web state, not the active one — the two are kept apart."""
    uid = _uid(web_vm)
    web_vm.toggle_search_source()
    web_vm.load_all_tracks()

    assert web_vm.webResult(uid)["title"] == "Web Track"


# ======================================================================
# importVM.importFileAs — one file, someone else's metadata
# ======================================================================


class _StubImportService(OwnedByIdOnly):
    """Answers "is this already in the library?" with whatever it was given."""

    def __init__(self, existing=None):
        self.existing = existing or {}

    def existing_ids(self, ids):
        return {
            yid: self.existing[yid]
            for yid in ids
            if yid and yid in self.existing
        }


class _StubImports:
    """Rows that reach the library stay listed — see tests/test_import_clear.py."""
    clear_imported = False


@pytest.fixture
def import_vm(qt_app):
    return ImportViewModel(
        import_service=_StubImportService(),
        track_service=None,
        registry=registry_for(audio=FakeAudio()),
        import_settings=_StubImports(),
    )


@pytest.fixture
def audio_file(tmp_path):
    path = tmp_path / "track03.mp3"
    path.write_bytes(b"not really audio")
    return path


def _read(vm, row, key):
    return vm.data(vm.index(row, 0, QModelIndex()), FIELD_TO_ROLE[key])


def test_the_row_shows_the_web_results_metadata(import_vm, audio_file):
    import_vm.importFileAs(str(audio_file), dict(WEB_RESULT))

    assert import_vm.result_count == 1
    assert import_vm.hasResults
    assert _read(import_vm, 0, "title") == "Web Track"
    assert _read(import_vm, 0, "artist") == "Web Artist"
    assert _read(import_vm, 0, "album") == "Web Album"
    assert _read(import_vm, 0, "duration") == 210
    assert _read(import_vm, 0, "externalIds") == {VIDEO: "vidweb0001"}
    assert _read(import_vm, 0, "coverUrl") == COVER_URL


def test_the_row_keeps_what_the_stash_will_need(import_vm, audio_file):
    """`stash_imported_track` reads these off the dict; no role exposes them."""
    import_vm.importFileAs(str(audio_file), dict(WEB_RESULT))
    result = import_vm._results[0]

    assert result["source_path"] == str(audio_file)
    assert result["release_date"] == 20200304
    assert COVER_MASTER_URL in result["artwork_urls"]


def test_the_row_is_matched_not_unmatched(import_vm, audio_file):
    """The user asserted this match, so it is the surest row in the list.

    An unmatched row is marked amber and is what "Import the files that matched
    nothing?" asks about. Neither applies to a row the user built by hand.
    """
    import_vm.importFileAs(str(audio_file), dict(WEB_RESULT))

    assert _read(import_vm, 0, "notFound") is False
    assert import_vm.notFoundCount == 0
    assert import_vm.unmatchedImportableCount == 0
    assert import_vm.foundCount == 1
    assert import_vm._is_stashable(import_vm._results[0])


def test_a_file_url_resolves_the_same_as_a_path(import_vm, audio_file):
    """What a QML FileDialog actually hands over."""
    import_vm.importFileAs(QUrl.fromLocalFile(str(audio_file)), dict(WEB_RESULT))

    assert import_vm._results[0]["source_path"] == str(audio_file)


def test_a_second_file_is_appended(import_vm, audio_file, tmp_path):
    """Pair several results with several files, then import them in one batch."""
    other = tmp_path / "track04.mp3"
    other.write_bytes(b"nor is this")

    import_vm.importFileAs(str(audio_file), dict(WEB_RESULT))
    import_vm.importFileAs(str(other), {**WEB_RESULT,
                                        "title": "Second",
                                        "external_ids": {VIDEO: "vidweb0002"}})

    assert import_vm.result_count == 2
    assert _read(import_vm, 0, "title") == "Web Track"
    assert _read(import_vm, 1, "title") == "Second"
    assert import_vm.uidAt(0) != import_vm.uidAt(1), "uid is the selection key"
    assert import_vm.foundCount == 2


def test_matched_rows_from_a_search_are_not_disturbed(import_vm, audio_file):
    """A run that already matched files keeps its rows, and its selection."""
    import_vm._on_search_finished([from_legacy({
        "title": "From a match run",
        "artist": "A",
        "album": "",
        "duration": 100,
        "source_id": "vidmatched1",
        "cover_url": "",
        "source_path": "/music/a.opus",
        "not_found": False,
    })])
    first_uid = import_vm.uidAt(0)
    import_vm.setSelected(first_uid, True)

    import_vm.importFileAs(str(audio_file), dict(WEB_RESULT))

    assert import_vm.result_count == 2
    assert _read(import_vm, 0, "title") == "From a match run"
    assert _read(import_vm, 0, "isSelected") is True, "an insert, not a reset"


def test_a_track_already_in_the_library_is_flagged(audio_file, qt_app):
    vm = ImportViewModel(
        import_service=_StubImportService({"vidweb0001": 77}),
        track_service=None,
        registry=registry_for(audio=FakeAudio()),
        import_settings=_StubImports(),
    )

    vm.importFileAs(str(audio_file), dict(WEB_RESULT))

    assert _read(vm, 0, "trackId") == 77
    assert vm.inDatabaseCount == 1


def test_no_file_is_an_error_and_adds_nothing(import_vm):
    errors = []
    import_vm.error_occurred.connect(errors.append)

    import_vm.importFileAs("", dict(WEB_RESULT))

    assert errors == ["No file was selected."]
    assert import_vm.result_count == 0
    assert not import_vm.hasResults


def test_a_run_in_flight_refuses_and_adds_nothing(import_vm, audio_file):
    """The same guard the file and CSV pickers answer to."""
    errors = []
    import_vm.error_occurred.connect(errors.append)
    import_vm._search_thread = object()      # a match run is still going

    import_vm.importFileAs(str(audio_file), dict(WEB_RESULT))

    assert errors == ["An import is already running."]
    assert import_vm.result_count == 0


def test_a_paired_file_joins_a_csv_batch(import_vm, audio_file):
    """CSV rows download and local rows stash, and one list holds both.

    A hand-paired file is the case that makes this worth having: the reason to
    pair one is that its tags are too poor to match, and that is no reason to
    lose the CSV rows already gathered.
    """
    import_vm._set_download_mode(True)
    import_vm._on_search_finished([from_legacy({
        "title": "From a CSV",
        "artist": "A",
        "album": "",
        "duration": 100,
        "source_id": "vidcsv00001",
        "cover_url": "",
        "source_path": "",
        "not_found": False,
    })])

    import_vm.importFileAs(str(audio_file), dict(WEB_RESULT))

    assert import_vm.result_count == 2
    assert _read(import_vm, 0, "title") == "From a CSV"
    assert _read(import_vm, 1, "title") == "Web Track"
    assert import_vm._results[0]["is_download"] is True
    assert import_vm._results[1]["is_download"] is False
    assert (import_vm.pendingDownloadCount, import_vm.pendingStashCount) == (1, 1)


def test_pairing_a_track_the_csv_already_names_is_refused(import_vm, audio_file):
    """Both rows would import the same recording, one fetched and one filed."""
    errors = []
    import_vm.error_occurred.connect(errors.append)
    import_vm._set_download_mode(True)
    import_vm._on_search_finished([from_legacy({
        "title": "Web Track",
        "artist": "A",
        "album": "",
        "duration": 100,
        "source_id": WEB_RESULT["external_ids"][VIDEO],
        "cover_url": "",
        "source_path": "",
        "not_found": False,
    })])

    import_vm.importFileAs(str(audio_file), dict(WEB_RESULT))

    assert errors == ["That track is already in the import list."]
    assert import_vm.result_count == 1


# ======================================================================
# Pairing onto a row that asked for it
# ======================================================================
#
# A `needsMetadata` row's whole instruction to the user is "find this track in
# Browse and pair it with this file" — and pairing is this slot, which used to
# refuse it for being a file already in the list. The guidance was a dead end.
# So the pairing lands on the row that asked for it instead of beside it.


def _unmatched_row(source_path, title="", artist=""):
    return from_legacy({
        "title": title, "artist": artist, "album": "", "duration": 0,
        "source_id": "", "cover_url": "", "cover_master_url": "",
        "source_path": str(source_path), "release_date": None,
        "not_found": True,
    })


def test_pairing_fills_in_the_row_that_needed_it(import_vm, audio_file):
    """The file is the same file; what changes is that it is now identified."""
    import_vm._on_search_finished([_unmatched_row(audio_file)])
    assert _read(import_vm, 0, "importState") == "needsMetadata"
    uid = import_vm.uidAt(0)

    import_vm.importFileAs(str(audio_file), dict(WEB_RESULT))

    assert import_vm.result_count == 1, "filled in, not duplicated"
    assert import_vm.uidAt(0) == uid, "the same row, so a selection survives"
    assert _read(import_vm, 0, "title") == "Web Track"
    assert _read(import_vm, 0, "externalIds") == WEB_RESULT["external_ids"]
    assert _read(import_vm, 0, "importState") == "stash"


def test_pairing_moves_the_row_across_the_headers_totals(import_vm, audio_file):
    """It stopped being unmatched, and the counts have to say so."""
    import_vm._on_search_finished([_unmatched_row(audio_file)])
    assert (import_vm.foundCount, import_vm.notFoundCount) == (0, 1)

    import_vm.importFileAs(str(audio_file), dict(WEB_RESULT))

    assert (import_vm.foundCount, import_vm.notFoundCount) == (1, 0)


def test_a_tags_only_row_can_be_upgraded_too(import_vm, audio_file):
    """The other half of the offer: settle for its own tags, or go and improve
    on them."""
    import_vm._on_search_finished(
        [_unmatched_row(audio_file, title="Rough", artist="A")]
    )
    assert _read(import_vm, 0, "importState") == "tagsOnly"

    import_vm.importFileAs(str(audio_file), dict(WEB_RESULT))

    assert import_vm.result_count == 1
    assert _read(import_vm, 0, "importState") == "stash"
    assert _read(import_vm, 0, "album") == WEB_RESULT["album"]


def test_pairing_a_track_another_row_already_holds_is_still_refused(
    import_vm, audio_file, tmp_path
):
    """The duplicate check comes first, so it applies to an upgrade as well.

    Filling one row in with a recording another row already names is the same
    duplicate by a longer route.
    """
    other = tmp_path / "other.opus"
    other.write_bytes(b"not really audio")
    import_vm.importFileAs(str(other), dict(WEB_RESULT))
    import_vm._on_search_finished([_unmatched_row(audio_file)])
    errors = []
    import_vm.error_occurred.connect(errors.append)

    import_vm.importFileAs(str(audio_file), dict(WEB_RESULT))

    assert errors == ["That track is already in the import list."]
    assert _read(import_vm, 1, "importState") == "needsMetadata", "untouched"


def test_a_matched_row_is_never_overwritten(import_vm, audio_file):
    """That is the genuine mistake the file check exists for: pairing a second
    result onto a file already spoken for would quietly discard the first."""
    import_vm.importFileAs(str(audio_file), dict(WEB_RESULT))
    errors = []
    import_vm.error_occurred.connect(errors.append)

    import_vm.importFileAs(
        str(audio_file),
        {**WEB_RESULT, "title": "Second", "external_ids": {VIDEO: "vidweb0002"}},
    )

    assert errors == ["That file is already in the import list."]
    assert import_vm.result_count == 1
    assert _read(import_vm, 0, "title") == "Web Track"


@pytest.mark.parametrize("metadata", [{}, None], ids=["empty", "null"])
def test_missing_metadata_degrades_rather_than_failing(import_vm, audio_file, metadata):
    """The file is the part that has to be real; the rest can be blank.

    ``None`` is not hypothetical: the pairing this slot exists for is
    ``importFileAs(file, searchVM.webResult(uid))``, and `webResult` answers
    null for a uid it no longer knows.
    """
    import_vm.importFileAs(str(audio_file), metadata)

    assert import_vm.result_count == 1
    assert _read(import_vm, 0, "title") == ""
    assert _read(import_vm, 0, "duration") == 0
    assert import_vm._results[0]["source_path"] == str(audio_file)
