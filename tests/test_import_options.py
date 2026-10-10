"""The options card: one question, asked once, over a scan the user can see.

Importing local files used to ask two questions through two dialogs shown one
after the other — how tracks should be identified, then which file types to
take, the second of them only when a non-Opus file was present. Each stated a
fragment of the scan in prose, neither could be gone back to, and neither was
remembered.

One card asks both now, over a list of what the scan actually held. That turns
the arithmetic into something worth pinning, because the card is *live*: every
figure on it moves as its controls move, and the number under the list has to be
the number the Import button acts on. So the card computes nothing. It asks
``makeup`` for a set of options and draws the answer, and ``beginImport`` runs
``selection`` over the same two arguments — one rule, read twice.

The two menus are still here, still working, and pinned below. They are part of
GUI API 2 and a replacement GUI is entitled to go on calling them; what they are
no longer allowed to be is a *second* implementation of what a candidate is.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.importer.media_probe import ProbedFile  # noqa: E402
from app.services.import_service import ImportAnalysis  # noqa: E402
from app.settings.imports import (  # noqa: E402
    DEFAULT_EXCLUDED_FORMATS,
    DEFAULT_FILENAME_FALLBACK,
    EXCLUDED_FORMATS_KEY,
    FILENAME_FALLBACK_KEY,
    ImportSettings,
)
from app.settings.store import SettingsStore  # noqa: E402
from app.viewmodels.import_view_model import ImportViewModel  # noqa: E402


# -- the scan every test below reads ---------------------------------------


def _file(name: str, *, tagged: bool = True, readable: bool = True) -> ProbedFile:
    return ProbedFile(
        path=Path(f"/music/{name}"),
        title="Title" if tagged else "",
        artist="Artist" if tagged else "",
        readable=readable,
    )


@pytest.fixture
def analysis() -> ImportAnalysis:
    """Eight files: two containers, and untagged ones on both sides.

    Deliberately untagged on *both* sides, because that is the folder the two
    controls can disagree about — one where excluding a container and turning
    the fallback off remove overlapping but different files.
    """
    return ImportAnalysis(
        files=[
            _file("a.opus"),
            _file("b.opus"),
            _file("c.opus", tagged=False),
            _file("d.mp3"),
            _file("e.mp3"),
            _file("f.mp3", tagged=False),
            _file("g.mp3", tagged=False, readable=False),
            _file("h.flac"),
        ]
    )


def _names(files):
    return sorted(f.path.name for f in files)


# ======================================================================
# selection — what the run will actually search
# ======================================================================


def test_everything_proceeds_when_nothing_is_turned_off(analysis):
    assert len(analysis.selection(True, ())) == 8


def test_without_the_fallback_a_file_with_no_tags_has_nothing_to_search_by(analysis):
    assert _names(analysis.selection(False, ())) == [
        "a.opus", "b.opus", "d.mp3", "e.mp3", "h.flac"
    ]


def test_an_unticked_container_is_dropped_whatever_its_tags_say(analysis):
    """The format question is asked first, and it is asked of the file.

    A tagged mp3 is as excluded as an untagged one — the user unticked the
    container, not the files in it that happened to match.
    """
    assert _names(analysis.selection(True, ("mp3",))) == [
        "a.opus", "b.opus", "c.opus", "h.flac"
    ]


def test_the_two_controls_compose(analysis):
    assert _names(analysis.selection(False, ("mp3",))) == [
        "a.opus", "b.opus", "h.flac"
    ]


def test_an_extension_is_read_with_or_without_its_dot(analysis):
    """The setting stores bare names and QML hands back what it was given.

    Neither of them should have to know which form the other prefers, and a
    mismatch here would silently import a container the user turned off.
    """
    assert _names(analysis.selection(True, (".MP3",))) == _names(
        analysis.selection(True, ("mp3",))
    )


def test_excluding_everything_selects_nothing(analysis):
    """The state the card disables its own button in, rather than starting a run
    that can only end in "No files match the selected import options."""
    assert analysis.selection(True, ("opus", "mp3", "flac")) == []


def test_candidates_is_selection_with_no_container_excluded(analysis):
    """`chooseMetadata` still answers only the fallback question, and it has to
    answer it the same way the card does."""
    for fallback in (True, False):
        assert analysis.candidates(fallback) == analysis.selection(fallback, ())


# ======================================================================
# makeup — what the card draws
# ======================================================================


def test_the_identity_buckets_describe_the_scan_not_the_selection(analysis):
    """They are what was *found*, so they do not move when a control does.

    The card says them under "what was found", above the controls, and a figure
    that changed as the user ticked would be answering a different question from
    the one its heading asks.
    """
    a = analysis.makeup(True, ())
    b = analysis.makeup(False, ("mp3", "flac"))

    for key in ("total", "withTitleArtist", "withoutMetadata", "unreadable"):
        assert a[key] == b[key]
    assert a["total"] == 8
    assert a["withTitleArtist"] == 5
    assert a["withoutMetadata"] == 3


def test_a_file_that_could_not_be_read_is_counted_and_still_offered(analysis):
    """Probing is best-effort so one bad file never aborts a 400-file import,
    which means it is in the scan like any other and will be searched by name if
    anything is. The count exists to explain a row that arrives knowing nothing
    about itself, not to remove it."""
    assert analysis.makeup(True, ())["unreadable"] == 1
    assert "g.mp3" in _names(analysis.selection(True, ()))


def test_the_total_it_reports_is_the_one_the_run_will_take(analysis):
    for fallback in (True, False):
        for excluded in ((), ("mp3",), ("opus", "flac")):
            makeup = analysis.makeup(fallback, excluded)
            assert makeup["selected"] == len(analysis.selection(fallback, excluded))


def test_each_format_reports_how_many_of_it_survive_both_controls(analysis):
    """The card sums nothing. A row saying "98" over a run that takes 92 is the
    drift this shape exists to make impossible."""
    rows = {row["ext"]: row for row in analysis.makeup(False, ("flac",))["formats"]}

    assert rows["opus"]["count"] == 3 and rows["opus"]["selected"] == 2
    assert rows["mp3"]["count"] == 4 and rows["mp3"]["selected"] == 2
    assert rows["flac"]["count"] == 1 and rows["flac"]["selected"] == 0


def test_untagged_is_per_format_because_that_is_where_it_decides_anything(analysis):
    rows = {row["ext"]: row for row in analysis.makeup(True, ())["formats"]}

    assert rows["opus"]["untagged"] == 1
    assert rows["mp3"]["untagged"] == 2
    assert rows["flac"]["untagged"] == 0


def test_a_format_says_whether_it_costs_a_transcode(analysis):
    rows = {row["ext"]: row for row in analysis.makeup(True, ())["formats"]}

    assert rows["opus"]["transcoded"] is False
    assert rows["mp3"]["transcoded"] is True
    assert rows["flac"]["transcoded"] is True


def test_an_excluded_format_is_still_listed_so_it_can_be_turned_back_on(analysis):
    rows = analysis.makeup(True, ("mp3",))["formats"]
    mp3 = next(row for row in rows if row["ext"] == "mp3")

    assert mp3["excluded"] is True
    assert mp3["count"] == 4, "the count is of what was found, not of what is taken"


def test_opus_leads_and_the_rest_follow_by_size(analysis):
    """The native format first — it is the one that costs nothing — then the
    ones worth arguing about, biggest first."""
    order = [row["ext"] for row in analysis.makeup(True, ())["formats"]]

    assert order == ["opus", "mp3", "flac"]


def test_a_scan_of_nothing_says_so_without_raising():
    assert ImportAnalysis().makeup(True, ()) == {
        "total": 0,
        "selected": 0,
        "unreadable": 0,
        "withExternalId": 0,
        "withTitleArtist": 0,
        "withoutMetadata": 0,
        "formats": [],
    }


# ======================================================================
# The remembered options
# ======================================================================


@pytest.fixture
def settings():
    return ImportSettings(SettingsStore({}, {}))


def test_the_defaults_are_the_ones_the_card_opens_with(settings):
    assert settings.filename_fallback is DEFAULT_FILENAME_FALLBACK is True
    assert settings.excluded_formats == frozenset()


def test_without_a_session_behind_it_the_defaults_still_serve():
    """A script or a test built without a vault gets documented behaviour rather
    than an exception — the rule the whole module is written to."""
    bare = ImportSettings()

    assert bare.filename_fallback is DEFAULT_FILENAME_FALLBACK
    assert bare.excluded_formats == frozenset()


def test_both_options_round_trip(settings):
    settings.set_import_options(False, ["mp3", "wav"])

    assert settings.filename_fallback is False
    assert settings.excluded_formats == {"mp3", "wav"}


def test_they_are_written_together(settings):
    """One press of one button, so one write. Two would let a crash between them
    leave an import remembered half one way and half the other."""
    store = SettingsStore({}, {})
    ImportSettings(store).set_import_options(False, ["flac"])

    assert store.app_get(FILENAME_FALLBACK_KEY) is False
    assert store.app_get(EXCLUDED_FORMATS_KEY) == "flac"


def test_an_extension_is_normalised_on_the_way_in(settings):
    settings.set_import_options(True, [".MP3", " flac "])

    assert settings.excluded_formats == {"mp3", "flac"}


def test_a_container_this_build_does_not_import_is_not_stored(settings):
    """Storing it would be harmless today and a trap later: the name it does not
    match now is a name a future release might."""
    settings.set_import_options(True, ["mp3", "wma", "ape"])

    assert settings.excluded_formats == {"mp3"}


def test_a_hand_edited_value_reads_as_nothing_excluded():
    """The store keeps strings it never interprets, so this is the only place
    that can tell a container name from a typo. It fails towards importing the
    file — the direction where the user can see what happened and undo it."""
    store = SettingsStore({}, {})
    store.app_set(EXCLUDED_FORMATS_KEY, "mp3,,NOTAFORMAT,.FLAC")

    assert ImportSettings(store).excluded_formats == {"mp3", "flac"}


@pytest.mark.parametrize("stored", [None, 7, ["mp3"], True])
def test_a_value_of_the_wrong_type_excludes_nothing(stored):
    store = SettingsStore({}, {})
    store.app_set(EXCLUDED_FORMATS_KEY, stored)

    assert ImportSettings(store).excluded_formats == frozenset()


def test_a_non_boolean_fallback_reads_as_the_default():
    store = SettingsStore({}, {})
    store.app_set(FILENAME_FALLBACK_KEY, "no")

    assert ImportSettings(store).filename_fallback is DEFAULT_FILENAME_FALLBACK


def test_nothing_excluded_is_stored_as_nothing(settings):
    settings.set_import_options(True, [])

    assert settings.excluded_formats == frozenset()
    assert DEFAULT_EXCLUDED_FORMATS == ""


# ======================================================================
# The view model — the one commit point
# ======================================================================



@pytest.fixture
def vm(qt_app, analysis, monkeypatch):
    """A view model with a scan already landed and the search stubbed out.

    The run itself is another file's subject; what this one is about is which
    files reach it.
    """
    model = ImportViewModel(
        import_service=None,
        track_service=None,
        import_settings=ImportSettings(SettingsStore({}, {})),
    )
    model._analysis = analysis

    searched: list = []
    monkeypatch.setattr(
        ImportViewModel, "_start_search", lambda self, files: searched.append(files)
    )
    model.searched = searched
    return model


def test_the_card_is_handed_the_makeup_of_the_scan_it_is_drawn_over(vm, analysis):
    assert vm.importPreview(False, ["mp3"]) == analysis.makeup(False, ["mp3"])


def test_before_a_scan_there_is_nothing_to_preview(qt_app):
    model = ImportViewModel(
        import_service=None,
        track_service=None,
        import_settings=ImportSettings(SettingsStore({}, {})),
    )

    assert model.importPreview(True, []) == {}


def test_importing_searches_exactly_what_the_card_counted(vm, analysis):
    vm.beginImport(False, ["flac"])

    assert vm.searched == [analysis.selection(False, ["flac"])]
    assert len(vm.searched[0]) == vm.importPreview(False, ["flac"])["selected"]


def test_importing_is_where_the_options_become_remembered(vm):
    vm.beginImport(False, ["mp3"])

    assert vm.filenameFallback is False
    assert vm.excludedFormats == ["mp3"]


def test_a_card_that_was_cancelled_changes_nothing(vm):
    """The preview is asked on every tick of a checkbox. If *that* wrote the
    setting, an import the user backed out of would have quietly changed the
    defaults of the next one."""
    vm.importPreview(False, ["mp3", "flac", "opus"])

    assert vm.filenameFallback is True
    assert vm.excludedFormats == []


def test_the_change_is_announced_so_a_reopened_card_is_not_stale(vm):
    heard = []
    vm.import_options_changed.connect(lambda: heard.append(vm.excludedFormats))

    vm.beginImport(True, ["mp3"])

    assert heard == [["mp3"]]


def test_importing_before_a_scan_does_nothing(qt_app, monkeypatch):
    started = []
    monkeypatch.setattr(
        ImportViewModel, "_start_search", lambda self, files: started.append(files)
    )
    model = ImportViewModel(
        import_service=None,
        track_service=None,
        import_settings=ImportSettings(SettingsStore({}, {})),
    )

    model.beginImport(True, [])

    assert started == []


def test_the_candidate_makeup_follows_the_options(vm):
    """`fileTypeSummary` and `nonOpusCount` describe the candidate set, and the
    card's answer is what the candidate set now is."""
    vm.beginImport(True, ["mp3"])

    assert vm.candidateCount == 4
    assert vm.nonOpusCount == 1
    assert vm.fileTypeSummary == "3 opus, 1 flac"


# ======================================================================
# The two menus the card replaced, which still work
# ======================================================================


def test_the_metadata_menu_still_asks_only_about_the_fallback(vm, analysis):
    vm.chooseMetadata(False)

    assert vm.candidateCount == len(analysis.selection(False, ()))


def test_the_file_type_menu_is_still_offered_when_it_has_something_to_say(vm):
    heard = []
    vm.file_type_required.connect(heard.append)

    vm.chooseMetadata(True)

    assert heard == [True], "the scan holds mp3 and flac"


def test_the_two_menus_together_still_reach_the_opus_only_set(vm):
    vm.chooseMetadata(True)
    vm.beginSearch(True)

    assert _names(vm.searched[0]) == ["a.opus", "b.opus", "c.opus"]


def test_the_menus_do_not_touch_what_the_card_remembers(vm):
    """They predate the setting and were never an answer to it. A GUI still
    driving them must not be silently rewriting the defaults of a GUI that is
    not."""
    vm.chooseMetadata(False)
    vm.beginSearch(True)

    assert vm.filenameFallback is True
    assert vm.excludedFormats == []
