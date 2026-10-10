"""Importing from a plugin's import source — a pasted link, a picked file.

The other sources all start from something the core understands: a file, a
folder of them, a spreadsheet. This one starts from whatever the user handed a
*plugin* — the core does not know what a link from that service looks like, and
must not. So two things have to hold:

**The input goes to the plugin as typed.** Sanitising a link is the plugin's
business — its own tests pin which spellings it accepts — and the core passes
the text through, trimmed, under the source's own id. What the core refuses up
front is only what no source could use: an empty line, or a source key no
enabled plugin offers.

**The rows that come back are downloads, merged like any other.** A row from an
import source is identical, once listed, to one a CSV brought in — which is the
point of ``is_download`` naming what happens to a row rather than where it came
from. Repeats collide by id, rows the library holds are marked, and a row that
says nothing at all is dropped.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.viewmodels import import_view_model  # noqa: E402
from app.viewmodels.import_view_model import ImportViewModel  # noqa: E402
from tests.conftest import OwnedByIdOnly  # noqa: E402
from tests.fakes.providers import (  # noqa: E402
    VIDEO,
    FakeAudio,
    FakeImportSource,
    FakeRegistry,
    row,
)

VIDEO_ID = "Xq7Lm2Pz9Ka"
SOURCE = "test.fake/link"


class _StubImportService(OwnedByIdOnly):
    def __init__(self, existing=None):
        self.existing = existing or {}

    def existing_ids(self, values):
        return {v: self.existing[v] for v in values if v in self.existing}


class _StubImports:
    """Rows that reach the library stay listed — see tests/test_import_clear.py."""
    clear_imported = False


def _vm(qt_app, existing=None, *, source=True):
    return ImportViewModel(
        import_service=_StubImportService(existing),
        track_service=None,
        registry=FakeRegistry(
            audio=FakeAudio(),
            import_source=FakeImportSource() if source else None,
        ),
        import_settings=_StubImports(),
    )


@pytest.fixture
def vm(qt_app):
    return _vm(qt_app)


@pytest.fixture
def started(vm, monkeypatch):
    """Capture the worker a run would start, without starting its thread."""
    workers: list = []
    monkeypatch.setattr(import_view_model.QThread, "start", lambda self: None)
    real = import_view_model.ImportSourceWorker

    def capture(provider, source, text):
        worker = real(provider, source, text)
        workers.append((source.id, text))
        return worker

    monkeypatch.setattr(import_view_model, "ImportSourceWorker", capture)
    return workers


def _seed(vm, *rows):
    """Put rows in the list as a finished run from a source, skipping the worker."""
    vm._set_download_mode(True)
    vm._on_search_finished([dict(r) for r in rows])


# ======================================================================
# What is refused, and what is passed through
# ======================================================================


def test_the_text_reaches_the_plugin_as_typed(vm, started):
    """The core does not parse a link it cannot know the shape of."""
    vm.importFromSource(SOURCE, f"  https://fake.example/watch?v={VIDEO_ID}&t=42  ")

    assert started == [(SOURCE, f"https://fake.example/watch?v={VIDEO_ID}&t=42")]


@pytest.mark.parametrize("junk", ["", "   "])
def test_an_empty_line_is_refused_up_front(vm, started, junk):
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)

    vm.importFromSource(SOURCE, junk)

    assert started == []
    assert errors == ["Nothing to import."]


def test_a_source_no_plugin_offers_is_refused(qt_app, monkeypatch):
    """Turned off between the menu opening and the click, say."""
    vm = _vm(qt_app, source=False)
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)

    vm.importFromSource(SOURCE, VIDEO_ID)

    assert errors == ["That import source is not available."]
    assert vm._search_thread is None


def test_the_run_is_a_downloading_one(vm, started):
    """No file exists yet, so the rows will be fetched rather than stashed."""
    vm.importFromSource(SOURCE, VIDEO_ID)

    assert vm.isDownloadMode is True


def test_a_run_already_going_is_not_interrupted(vm, started, monkeypatch):
    """`_can_start` guards every entry point; this is one of them."""
    monkeypatch.setattr(vm, "_can_start", lambda: False)

    vm.importFromSource(SOURCE, VIDEO_ID)

    assert started == []


# ======================================================================
# The rows that arrive
# ======================================================================


def test_the_track_lands_in_the_import_list(vm):
    _seed(vm, row("A Track", "A", video=VIDEO_ID))

    assert vm.result_count == 1
    assert vm._results[0]["external_ids"] == {VIDEO: VIDEO_ID}
    assert vm.hasResults is True


def test_the_row_downloads_like_a_csv_row_does(vm):
    """Identical to a CSV row once it is in the list — that is the whole design."""
    _seed(vm, row("A Track", "A", video=VIDEO_ID))
    listed = vm._results[0]

    assert listed["is_download"] is True
    assert vm._is_stashable(listed)


def test_a_second_import_is_appended(vm):
    """Sources accumulate the way every other one does."""
    _seed(vm, row("First", "A", video=VIDEO_ID))
    _seed(vm, row("Second", "A", video="Bc3Df6Gh9Jk"))

    assert [r["title"] for r in vm._results] == ["First", "Second"]


def test_the_same_track_twice_is_skipped(vm):
    """Identity for a row with no file is its id."""
    _seed(vm, row("A Track", "A", video=VIDEO_ID))
    _seed(vm, row("A Track again", "A", video=VIDEO_ID))

    assert vm.result_count == 1
    assert vm.duplicatesSkipped == 1


def test_a_track_already_in_the_library_is_flagged(qt_app):
    vm = _vm(qt_app, {VIDEO_ID: 77})
    _seed(vm, row("A Track", "A", video=VIDEO_ID))

    assert vm._results[0]["id"] == 77
    assert vm.inDatabaseCount == 1


def test_a_row_that_says_nothing_leaves_no_row(vm):
    """No file, no id, no title — a blank line, whichever plugin sent it."""
    _seed(vm, row("", ""))

    assert vm.result_count == 0
    assert vm.hasResults is False


def test_a_row_no_enabled_source_can_fetch_is_unavailable(vm):
    """Listed, so the user can see it — but nothing will pretend to fetch it."""
    _seed(vm, row("A Track", "A", video=VIDEO_ID))
    vm._registry.set(audio=None)

    assert vm._state(vm._results[0]) == "unavailable"
