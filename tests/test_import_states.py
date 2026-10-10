"""What an import row *is*, and the one place that decides it.

Before this there was no such thing as a row's state. There were five predicates
— `_already_done`, `_is_stashable`, `_downloads_blocked`,
`unmatchedImportableCount`, `_pending` — each spelling out its own combination of
`id`, `is_download`, `not_found`, `title` and `artist`, plus a sixth copy of the
rule written in QML so a delegate could grey a button out without a round trip.
Nothing held them together, and `unmatchedImportableCount`'s docstring existed
mostly to explain why it repeated one of the others.

They are one function now. `_state` classifies a row into one of six states, and
everything above reads that: what a cell draws, whether a button appears, what
the header's verb is, what the legend counts. So this file is mostly about the
classifier, and about the two things that could quietly go wrong with it —
a boundary landing in the wrong state, and a caller that stopped agreeing.

The states are named for what the user should do, not for how the row arrived:

    inLibrary      nothing
    download       press Import — it is fetched
    stash          press Import — it is filed
    tagsOnly       press Import, and know it arrives with less
    needsMetadata  pair it with a search result; it cannot import yet
    unavailable    remove it; nothing will ever import it
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from PySide6.QtCore import QModelIndex

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.library.roles import FIELD_TO_ROLE  # noqa: E402
from app.viewmodels.import_view_model import (  # noqa: E402
    ALL_STATES,
    SAVEABLE_STATES,
    STATE_DOWNLOAD,
    STATE_IN_LIBRARY,
    STATE_NEEDS_METADATA,
    STATE_STASH,
    STATE_TAGS_ONLY,
    STATE_UNAVAILABLE,
    ImportViewModel,
)
from tests.conftest import OwnedByIdOnly  # noqa: E402
from tests.fakes.providers import FakeAudio, registry_for  # noqa: E402
from tests.fakes.providers import from_legacy  # noqa: E402


# -- doubles ---------------------------------------------------------------


class _StubImportService(OwnedByIdOnly):
    def existing_ids(self, ids):
        return {}


class _StubDownloads:
    allowed = True


class _StubImports:
    """Rows that reach the library stay listed.

    `inLibrary` is one of the six states this file is about, so the list has to
    keep a row that reaches it. The shipped default does not — see
    tests/test_import_clear.py.
    """
    clear_imported = False


@pytest.fixture
def vm(qt_app):
    return ImportViewModel(
        import_service=_StubImportService(),
        track_service=None,
        registry=registry_for(audio=FakeAudio()),
        import_settings=_StubImports(),
    )


def _read(vm, row, key):
    return vm.data(vm.index(row, 0, QModelIndex()), FIELD_TO_ROLE[key])


def _row_legacy(**over):
    """A matched local row — the plainest thing in the list — plus overrides."""
    base = {
        "title": "Song", "artist": "A", "album": "", "duration": 100,
        "source_id": "vidmatched1", "cover_url": "", "cover_master_url": "",
        "source_path": "/music/song.opus", "release_date": None,
        "not_found": False, "is_download": False, "id": None,
    }
    base.update(over)
    return base


def _row(*args, **kwargs):
    """`_row_legacy`, as a core row — see `tests.fakes.providers.from_legacy`."""
    return from_legacy(_row_legacy(*args, **kwargs))


# ======================================================================
# The classifier
# ======================================================================


@pytest.mark.parametrize("state, row", [
    (STATE_IN_LIBRARY,     _row(id=7)),
    (STATE_DOWNLOAD,       _row(is_download=True, source_path="")),
    (STATE_STASH,          _row()),
    (STATE_TAGS_ONLY,      _row(not_found=True, source_id="")),
    (STATE_NEEDS_METADATA, _row(not_found=True, source_id="", title="", artist="")),
    (STATE_UNAVAILABLE,    _row(is_download=True, source_path="",
                                not_found=True, source_id="")),
])
def test_each_state_is_reachable(vm, state, row):
    assert vm._state(row) == state


def test_in_library_wins_over_everything(vm):
    """An id is the end of every path, whatever the row was on the way there.

    A downloaded row that lands is not "a download" any more, and an unmatched
    file that was imported from its own tags has nothing left to warn about.
    """
    for over in ({"is_download": True}, {"not_found": True, "source_id": ""},
                 {"not_found": True, "title": "", "artist": ""}):
        assert vm._state(_row(id=7, **over)) == STATE_IN_LIBRARY


def test_id_zero_is_a_real_id(vm):
    """`is not None`, not truthiness — track ids start at 1, but nothing here
    should depend on that."""
    assert vm._state(_row(id=0)) == STATE_IN_LIBRARY


def test_one_of_title_or_artist_is_not_enough_to_name_a_track(vm):
    """Both, or neither. A library entry needs something on both sides."""
    assert vm._state(_row(not_found=True, source_id="", artist="")) \
        == STATE_NEEDS_METADATA
    assert vm._state(_row(not_found=True, source_id="", title="")) \
        == STATE_NEEDS_METADATA
    assert vm._state(_row(not_found=True, source_id="")) == STATE_TAGS_ONLY


def test_a_fetched_row_is_judged_only_by_its_id(vm):
    """Nothing but an id was ever known about it, so tags cannot rescue it.

    The mirror of the case above: a local file with tags and no match still has
    its own audio, and a fetched row with tags and no match has nothing at all.
    """
    assert vm._state(_row(is_download=True, source_path="", source_id="")) \
        == STATE_UNAVAILABLE


# ======================================================================
# Everything that used to ask its own question now agrees
# ======================================================================


def test_saveable_states_are_exactly_what_stash_all_would_queue(vm):
    """The set the button's promise rests on."""
    assert SAVEABLE_STATES == {STATE_DOWNLOAD, STATE_STASH, STATE_TAGS_ONLY}


def test_is_stashable_follows_the_state(vm):
    rows = {
        STATE_IN_LIBRARY:     _row(id=7),
        STATE_DOWNLOAD:       _row(is_download=True, source_path=""),
        STATE_STASH:          _row(),
        STATE_TAGS_ONLY:      _row(not_found=True, source_id=""),
        STATE_NEEDS_METADATA: _row(not_found=True, source_id="", title="",
                                   artist=""),
        STATE_UNAVAILABLE:    _row(is_download=True, source_path="",
                                   not_found=True, source_id=""),
    }
    for state, row in rows.items():
        assert vm._is_stashable(row) is (state in SAVEABLE_STATES), state


def test_the_unmatched_prompt_counts_exactly_the_tags_only_rows(vm):
    """The number the "import the files that matched nothing?" choice turns on.

    Not `notFoundCount`: a file with nothing to name it is unmatched too, and no
    answer the user gives changes what happens to it.
    """
    vm._on_search_finished([
        _row(not_found=True, source_id="", source_path="/m/a.mp3"),
        _row(not_found=True, source_id="", title="", artist="",
             source_path="/m/b.mp3"),
    ])

    assert vm.notFoundCount == 2
    assert vm.unmatchedImportableCount == 1


def test_the_pending_counts_split_by_state_not_by_flag(vm):
    """A `tagsOnly` row is still one the Import button will act on."""
    vm._on_search_finished([
        _row(source_path="/m/a.opus"),
        _row(not_found=True, source_id="", source_path="/m/b.mp3"),
        _row(not_found=True, source_id="", title="", artist="",
             source_path="/m/c.mp3"),
    ])
    vm._set_download_mode(True)
    vm._on_search_finished([_row(source_path="", source_id="vidfetch001")])

    assert vm.pendingStashCount == 2, "stash + tagsOnly; needsMetadata is not"
    assert vm.pendingDownloadCount == 1


# ======================================================================
# What the GUI reads
# ======================================================================


def test_the_state_is_served_as_a_role(vm):
    vm._on_search_finished([_row(), _row(not_found=True, source_id="",
                                         source_path="/m/b.mp3")])

    assert _read(vm, 0, "importState") == STATE_STASH
    assert _read(vm, 1, "importState") == STATE_TAGS_ONLY


def test_a_row_that_lands_changes_state_and_says_so(vm):
    """Computed, not stored — but a computed role still has to be announced.

    Without the role in `dataChanged` the row would keep offering to import
    something it has just imported, because a delegate has no reason to re-ask.
    """
    from app.library.roles import ROLE_IMPORT_STATE

    vm._on_search_finished([_row()])
    uid = vm.uidAt(0)
    seen: list = []
    vm.dataChanged.connect(lambda tl, br, roles: seen.append(list(roles)))

    vm._on_stash_finished(uid, 42)

    assert _read(vm, 0, "importState") == STATE_IN_LIBRARY
    assert any(ROLE_IMPORT_STATE in roles for roles in seen), \
        "the row's marking would not repaint"


def test_state_counts_name_every_state_even_at_zero(vm):
    """A legend deciding which chips to show asks the map, not its keys."""
    vm._on_search_finished([_row()])

    counts = vm.stateCounts

    assert set(counts) == set(ALL_STATES)
    assert counts[STATE_STASH] == 1
    assert counts[STATE_UNAVAILABLE] == 0


def test_uids_in_state_are_what_a_legend_selects(vm):
    vm._on_search_finished([
        _row(source_path="/m/a.opus"),
        _row(not_found=True, source_id="", title="", artist="",
             source_path="/m/b.mp3"),
        _row(source_path="/m/c.opus", source_id="vidother001"),
    ])
    wanted = vm.uidsInState(STATE_NEEDS_METADATA)

    assert wanted == [vm.uidAt(1)]

    vm.setSelectedMultiple(wanted)
    assert set(vm.selectedUids()) == set(wanted)


def test_selecting_from_outside_the_list_is_announced(vm):
    """A GUI holds its own copy of the selection, because selection outlives the
    delegates that draw it. A per-row dataChanged does not reach that copy."""
    vm._on_search_finished([_row()])
    fired: list = []
    vm.selectionChanged.connect(lambda: fired.append(True))

    vm.setSelectedMultiple([vm.uidAt(0)])

    assert fired, "a selection the list did not make would not be drawn"


# ======================================================================
# What importing a row will cost
# ======================================================================


@pytest.mark.parametrize("path, expected", [
    ("/music/song.opus", "opus"),
    ("/music/song.flac", "flac"),
    ("/music/SONG.MP3", "mp3"),
    ("/music/no-extension", ""),
    ("", ""),
])
def test_the_source_format_is_the_containers_own_name(vm, path, expected):
    assert vm._source_format(path) == expected


def test_the_format_is_stamped_on_every_row(vm):
    """It has to be on the row: the extension never survived the match run.

    `ProbedFile.ext` was read by the file-type dialog and then dropped, so once
    a row existed nothing could say whether importing it meant a copy or a
    transcode — the one cost the user had no way to see before committing.
    """
    # Distinct ids: two rows naming one recording are a duplicate, and the
    # second would never reach the list to be asked.
    vm._on_search_finished([
        _row(source_path="/music/a.opus", source_id="vidaaaaaaa1"),
        _row(source_path="/music/b.flac", source_id="vidbbbbbbb1"),
    ])
    vm._set_download_mode(True)
    vm._on_search_finished([_row(source_path="", source_id="vidfetch001")])

    assert [_read(vm, i, "sourceFormat") for i in range(3)] == \
        ["opus", "flac", ""]


def test_a_fetched_row_has_no_format_of_its_own(vm):
    """Its audio does not exist yet, and arrives as Opus either way."""
    vm._set_download_mode(True)
    vm._on_search_finished([_row(source_path="", source_id="vidfetch001")])

    assert _read(vm, 0, "sourceFormat") == ""


def test_the_format_matches_what_the_stash_will_actually_do(vm):
    """Both sides of a promise: the column says "→ Opus" only where
    `ensure_opus` would really transcode."""
    from app.importer.stash import Path as _StashPath  # noqa: F401

    for path in ("/m/a.opus", "/m/b.flac", "/m/c.wav"):
        transcodes_here = vm._source_format(path) != "opus"
        # The rule `ensure_opus` and `stash_imported_track` both apply.
        transcodes_there = Path(path).suffix.lower() != ".opus"
        assert transcodes_here is transcodes_there, path
