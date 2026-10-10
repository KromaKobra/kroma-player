"""The mic button: searching for a track's lyrics on demand.

Every track imported from local files arrives without lyrics, permanently —
before this there was no way to get them short of `refreshFromSource()`,
which also replaces the title, the artists, the album and the artwork, and so is
not something anyone reaches for just to get the words.

Three outcomes, and the third is the one worth testing hardest: a lookup that
**raises** must come back as "no lyrics", not as an exception unwinding through
a worker thread. `docs/architecture/cross-cutting/degradation.md` records that
as the documented outcome, and the download path, which shares this code,
depends on it.

The worker is driven synchronously — a plain `QObject`, a plain `run()`, direct
signal connections — so nothing here needs an event loop.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.lyrics import lookup as lookup_mod  # noqa: E402
from app.lyrics.workers import LyricsFetchWorker  # noqa: E402
from app.models.track import Track  # noqa: E402
from app.repositories.lyrics_repository import LyricsRepository  # noqa: E402
from app.services.lyrics_service import (  # noqa: E402
    SOURCE_SEARCH,
    LyricsService,
)
from app.settings.lyrics import LyricsSettings  # noqa: E402


@pytest.fixture
def service(db, event_bus):
    return LyricsService(LyricsRepository(db.session), event_bus)


@pytest.fixture
def track(db):
    return db.session.query(Track).order_by(Track.id).first()


def _player(track):
    """PlayerViewModel's surface, as LyricsViewModel uses it."""
    from PySide6.QtCore import QObject, Signal

    class Player(QObject):
        current_track_changed = Signal()
        position_changed = Signal()

        def __init__(self):
            QObject.__init__(self)
            self._current_track = track
            self.position = 0.0

    return Player()


def _vm(qt_app, track, service):
    """A view model on a visible tab, over a library whose tag port has run.

    Both matter. The mic button is only reachable from the tab, so ``active`` is
    what the real thing is in when any of this happens — and a view model that
    becomes active with the port outstanding starts a sweep, which is a
    different test's subject (`tests/test_lyrics_backfill.py`).
    """
    from app.settings.store import SettingsStore
    from app.viewmodels.lyrics_view_model import LyricsViewModel

    settings = LyricsSettings(SettingsStore({}, {}))
    settings.tag_sweep_done = True

    player = _player(track)
    vm = LyricsViewModel(player, service, settings)
    # Held so the player is not collected while the view model is connected to it.
    vm._test_player = player
    vm.active = True
    return vm


# ==================================================================
# The search itself
# ==================================================================


def test_the_query_is_the_title_and_the_lead_artist(monkeypatch):
    asked: list[str] = []
    monkeypatch.setattr(lookup_mod.syncedlyrics, "search",
                        lambda query: asked.append(query) or "words")

    assert lookup_mod.lookup_lyrics("Archangel", "Burial, Someone Else") == "words"

    # Only the lead artist — a query naming four collaborators matches nothing.
    assert asked == ["Archangel Burial"]


def test_nothing_to_search_for_is_not_searched_for(monkeypatch):
    monkeypatch.setattr(
        lookup_mod.syncedlyrics, "search",
        lambda query: pytest.fail("searched with an empty query"),
    )

    assert lookup_mod.lookup_lyrics("", "") is None


def test_a_raising_provider_is_answered_with_none(monkeypatch):
    """The promise the download path is built on."""
    def explode(query):
        raise RuntimeError("every provider is down")

    monkeypatch.setattr(lookup_mod.syncedlyrics, "search", explode)

    assert lookup_mod.lookup_lyrics("Archangel", "Burial") is None


# ==================================================================
# The worker
# ==================================================================


def _run_worker(monkeypatch, result, track_id=7):
    monkeypatch.setattr(
        "app.lyrics.workers.lookup_lyrics",
        result if callable(result) else (lambda title, artist: result),
    )
    worker = LyricsFetchWorker(track_id, "Archangel", "Burial")
    seen: dict = {"found": [], "not_found": [], "failed": [], "done": 0}
    worker.found.connect(lambda tid, text: seen["found"].append((tid, text)))
    worker.not_found.connect(seen["not_found"].append)
    worker.failed.connect(seen["failed"].append)
    worker.done.connect(lambda: seen.__setitem__("done", seen["done"] + 1))
    worker.run()
    return seen


def test_a_hit_is_reported_with_the_track_id(qt_app, monkeypatch):
    seen = _run_worker(monkeypatch, "[00:01.00]words")

    assert seen["found"] == [(7, "[00:01.00]words")]
    assert seen["not_found"] == []
    assert seen["done"] == 1


@pytest.mark.parametrize("result", [None, "", "   \n "])
def test_a_miss_is_an_ordinary_outcome(qt_app, monkeypatch, result):
    seen = _run_worker(monkeypatch, result)

    assert seen["not_found"] == [7]
    assert seen["found"] == []
    assert seen["failed"] == []
    assert seen["done"] == 1


def test_done_is_emitted_even_when_the_lookup_raises(qt_app, monkeypatch):
    """`own_worker` tears the thread down on `done` and nothing else."""
    def explode(title, artist):
        raise RuntimeError("boom")

    seen = _run_worker(monkeypatch, explode)

    assert seen["failed"] == ["boom"]
    assert seen["done"] == 1


# ==================================================================
# The view model
# ==================================================================


def test_a_found_result_is_stored_and_shown(qt_app, db, service, track):
    vm = _vm(qt_app, track, service)
    vm._load()
    assert vm.has_lyrics is False

    # What the worker's `found` signal delivers, on the UI thread.
    vm._on_fetch_found(track.id, "[00:04.00]the words")

    assert service.get(track.id) == "[00:04.00]the words"
    assert service.repository.get(track.id).source == SOURCE_SEARCH
    # The bus event reached the view model, so the pane swapped with no help
    # from the GUI.
    assert vm.has_lyrics is True
    assert vm.is_synced is True
    assert vm.synced_lines == ["the words"]


def test_a_result_for_another_track_does_not_replace_what_is_showing(
    qt_app, db, service, track,
):
    """A search takes seconds; the user may well have skipped on."""
    other = db.session.query(Track).order_by(Track.id).offset(1).first()
    service.save(track.id, "the playing track's words", SOURCE_SEARCH)
    vm = _vm(qt_app, track, service)
    vm._load()

    vm._on_fetch_found(other.id, "somebody else's words")

    assert vm.plain_text == "the playing track's words"
    assert service.get(other.id) == "somebody else's words"


def test_nothing_found_surfaces_as_a_message_not_a_silence(qt_app, db, service, track):
    vm = _vm(qt_app, track, service)
    messages: list[str] = []
    vm.fetch_failed.connect(messages.append)

    vm._on_fetch_not_found(track.id)

    assert messages == ["No lyrics found for this track"]
    assert vm.has_lyrics is False


def test_a_raising_lookup_surfaces_rather_than_escaping(qt_app, db, service, track):
    """The failure crosses back as a signal, which is what the toast shows."""
    vm = _vm(qt_app, track, service)
    messages: list[str] = []
    vm.fetch_failed.connect(messages.append)

    vm._on_fetch_failed("every provider is down")

    assert messages == ["every provider is down"]


def test_fetching_is_refused_while_one_is_already_running(qt_app, db, service, track):
    """`is_fetching` is the mic button's disabled state, and the slot honours it too.

    A user can click faster than a network search returns, and two searches for
    one track would race each other into the same row.
    """
    vm = _vm(qt_app, track, service)
    # Standing in for a thread already running — what `fetchLyrics` sets and
    # `_on_fetch_stopped` clears.
    vm._fetch_thread = object()

    assert vm.is_fetching is True
    vm.fetchLyrics()

    # Nothing was started: the guard returns before a worker is built.
    assert vm._fetch_worker is None


def test_nothing_playing_is_nothing_to_search_for(qt_app, db, service):
    vm = _vm(qt_app, None, service)

    vm.fetchLyrics()

    assert vm.is_fetching is False
