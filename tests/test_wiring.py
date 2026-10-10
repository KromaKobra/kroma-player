"""End-to-end check that the real service registry still hangs together.

Builds the actual DI graph against a temporary vault and drives the list models
the way the GUI does. This is the test that catches a constructor signature
drifting out of step with bootstrap — the kind of break that otherwise only
shows up when the window fails to open.

No QML and no window: a QCoreApplication is enough for QObject and QTimer, and
the models expose ``flush_now()`` so a test never has to spin an event loop.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from conftest import make_track

from app.library.roles import FIELD_TO_ROLE  # noqa: E402
from app.models.track import Artist, Track  # noqa: E402

TITLE = FIELD_TO_ROLE["title"]
ARTIST = FIELD_TO_ROLE["artist"]
IS_SELECTED = FIELD_TO_ROLE["isSelected"]

TRACKS = 400


@pytest.fixture(scope="module")
def registry(qt_app, tmp_path_factory):
    """The real registry, over a throwaway vault seeded with tracks."""
    from app.utils.library_paths import set_library_root

    vault = tmp_path_factory.mktemp("vault") / "venomvault"
    (vault / ".data").mkdir(parents=True)
    set_library_root(vault)

    from app.bootstrap import create_service_registry

    reg = create_service_registry()

    session = reg.get_service("db_session")
    artists = [Artist(name=f"Artist {i}") for i in range(5)]
    session.add_all(artists)
    session.flush()
    for i in range(TRACKS):
        session.add(
            make_track(
                session,
                title=f"Track {i:04d}",
                album=f"Album {i % 20}",
                duration_seconds=180,
                bpm=120,
                artists=[artists[i % 5]],
            )
        )
    session.commit()
    return reg


def test_every_context_property_resolves(registry):
    """Every name main.py hands to QML must actually exist in the registry."""
    from app.main import CONTEXT_PROPERTIES

    missing = [
        qml_name
        for qml_name, service in CONTEXT_PROPERTIES.items()
        if registry.get_service(service) is None
    ]
    assert missing == []


def test_qml_types_are_registered_before_the_engine_exists():
    """The one ordering in main() that no test running a window can see.

    Registration is process-wide, and every GUI test registers before it builds
    an engine, so a `main()` that builds its engine first still passes all of
    them — and then fails to open a window at all, blaming Nocturne's `Theme`
    ("Cannot assign object of type QtObject to property of type QObject*").
    So this reads the order off `_main()` itself — `main()` is only the wrapper
    that logs what escapes it — and checks that `_build_engine()`, which it
    calls, is where the engine is made.
    """
    import ast
    import inspect

    from app import main as main_module

    def first_calls(function) -> dict[str, int]:
        tree = ast.parse(inspect.getsource(function))
        lines: dict[str, int] = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                lines.setdefault(node.func.id, node.lineno)
        return lines

    lines = first_calls(main_module._main)
    assert "_register_qml_types" in lines and "_build_engine" in lines
    assert "QQmlApplicationEngine" not in lines, "an engine made outside _build_engine"
    assert lines["_register_qml_types"] < lines["_build_engine"]
    assert "QQmlApplicationEngine" in first_calls(main_module._build_engine)


def test_whole_library_loads_without_reading_rows(registry):
    search = registry.get_service("search_view_model")
    search.load_all_tracks()

    assert search.rowCount() == TRACKS
    assert search.result_count == TRACKS
    assert search.data(search.index(0), TITLE) is None, "structure first, rows on demand"

    search.flush_now()
    assert search.data(search.index(0), TITLE) == "Track 0000"


def test_quick_search_matches_title_or_album(registry):
    search = registry.get_service("search_view_model")

    search.text_search("Track 003")
    assert search.rowCount() == 10

    search.text_search("Album 5")
    assert search.rowCount() == TRACKS // 20, "matched on album, not title"


def test_with_no_online_source_the_library_is_the_only_side(registry):
    """A fresh vault has no plugin on, so there is nowhere to switch to."""
    search = registry.get_service("search_view_model")
    search.text_search("Track 003")
    assert search.rowCount() == 10
    assert search.sources == []

    search.toggle_search_source()
    assert search.search_in_database is True
    assert search.rowCount() == 10, "the library results stayed"

    search.setSource("someone.else/search")
    assert search.currentSource == "library", "a source nothing offers is refused"


def test_queue_mutations_keep_rows_in_step(registry):
    search = registry.get_service("search_view_model")
    queue = registry.get_service("queue_view_model")

    search.text_search("Track 003")
    ids = search.get_result_ids()[:5]
    queue.append_track_ids(ids)
    queue.flush_now()

    assert queue.rowCount() == 5
    assert queue.count == 5
    first_title = queue.data(queue.index(0), TITLE)
    assert first_title is not None

    queue.moveTrack(0, 2)
    queue.flush_now()
    assert queue.data(queue.index(2), TITLE) == first_title, "the moved row followed"

    queue.removeAt(2)
    assert queue.rowCount() == 4


def test_queue_selection_goes_through_the_queue_manager(registry):
    queue = registry.get_service("queue_view_model")
    manager = registry.get_service("queue_manager")

    uid = queue.uidAt(0)
    queue.setSelectedExclusive(uid)

    assert queue.data(queue.index(0), IS_SELECTED) is True
    assert manager.is_selected(uid) is True, "the manager owns queue selection"
    assert queue.selectedUids() == [uid]

    queue.clearSelection()
    assert queue.selectedTrackIds() == []


def test_playing_moves_a_row_into_history(registry):
    queue = registry.get_service("queue_view_model")
    history = registry.get_service("history_view_model")
    player = registry.get_service("player_view_model")

    queued = queue.rowCount()
    before = history.rowCount()
    played_title = queue.data(queue.index(0), TITLE)

    player.next()

    assert queue.rowCount() == queued - 1
    assert history.rowCount() == before + 1
    history.flush_now()
    assert history.data(history.index(0), TITLE) == played_title


def test_play_next_inserts_the_whole_selection_in_order(registry):
    """Two rows sent to Play next land in queue slots 1 and 2, in display order."""
    search = registry.get_service("search_view_model")
    queue = registry.get_service("queue_view_model")

    search.load_all_tracks()
    picked = [search.trackIdAt(4), search.trackIdAt(8)]

    before = queue.rowCount()
    queue.insertNext(picked)

    assert queue.rowCount() == before + 2, "the model counted both rows"
    assert [queue.trackIdAt(1), queue.trackIdAt(2)] == picked


def test_playing_down_the_queue_keeps_the_rows_it_passed(registry):
    """The resilience rule, through the real model/signal path."""
    queue = registry.get_service("queue_view_model")
    history = registry.get_service("history_view_model")
    player = registry.get_service("player_view_model")

    before = [queue.trackIdAt(row) for row in range(queue.rowCount())]
    history_before = history.rowCount()
    target = before[4]

    player.play_queue_rows([4])

    assert queue.trackIdAt(0) == target
    assert queue.rowCount() == len(before), "nothing left the queue"
    assert sorted(queue.trackIdAt(r) for r in range(queue.rowCount())) == sorted(before)
    assert history.rowCount() == history_before, "a fresh track is not history"


def test_removing_a_selection_takes_every_selected_row(registry):
    queue = registry.get_service("queue_view_model")

    before = queue.rowCount()
    queue.setSelectedMultiple([queue.uidAt(1), queue.uidAt(3), queue.uidAt(4)])
    survivor = queue.trackIdAt(2)

    queue.removeSelected()

    assert queue.rowCount() == before - 3
    assert queue.trackIdAt(1) == survivor, "the non-contiguous run was handled"
    assert queue.selectedUids() == []


def _seed_queue(registry, count=10):
    """A queue of `count` distinct tracks, from empty. Returns the model and ids.

    Distinct on purpose: a track id then names exactly one row, so a reorder can
    be asserted by reading the ids back in order.
    """
    search = registry.get_service("search_view_model")
    queue = registry.get_service("queue_view_model")

    queue.clearSelection()
    while queue.rowCount() > 0:
        queue.removeAt(0)

    search.load_all_tracks()
    ids = [search.trackIdAt(row) for row in range(count)]
    queue.append_track_ids(ids)

    assert queue.rowCount() == count
    return queue, ids


def _order(queue):
    return [queue.trackIdAt(row) for row in range(queue.rowCount())]


def test_a_move_that_would_change_nothing_is_declined(registry):
    """The calls Qt chokes on are refused rather than passed along.

    A drop that lands a row where it already was is the ordinary end of a drag
    the user thought better of, and Qt answers ``beginMoveRows(row -> row)``
    with False — which leaves the ``endMoveRows`` after it unbalanced. An
    out-of-range source is worse: the manager declines quietly while Qt has
    already been told a move happened.
    """
    queue, ids = _seed_queue(registry)

    queue.moveTrack(3, 3)
    queue.moveTrack(99, 2)
    queue.moveTrack(-1, 2)

    assert _order(queue) == ids, "nothing moved and nothing was corrupted"


def test_moving_a_block_lands_it_contiguously_in_its_own_order(registry):
    queue, ids = _seed_queue(registry)

    queue.moveRows([2, 5, 6], 3)

    rest = [ids[0], ids[1], ids[3], ids[4], ids[7], ids[8], ids[9]]
    block = [ids[2], ids[5], ids[6]]
    assert _order(queue) == rest[:3] + block + rest[3:]
    assert queue.rowCount() == len(ids), "a reorder is not a removal"


def test_a_block_straddling_the_drop_point_lands_where_it_was_aimed(registry):
    """`to` counts what is left once the block is lifted, not the original rows.

    Rows 1 and 8 with a destination of 4 means "in front of the fourth of the
    eight that remain". Read against the original numbering it would mean
    something else, and the answer would come out one place off.
    """
    queue, ids = _seed_queue(registry)

    queue.moveRows([1, 8], 4)

    rest = [ids[0], ids[2], ids[3], ids[4], ids[5], ids[6], ids[7], ids[9]]
    assert _order(queue) == rest[:4] + [ids[1], ids[8]] + rest[4:]


def test_a_block_travelling_down_does_not_shed_its_own_members(registry):
    """Both members start above the destination — the case a naive walk breaks.

    Sent to its final slot one at a time front to back, the second member
    travels down through the slot the first has just taken and pushes it back
    out, leaving the two of them a row apart with a stranger in between.
    """
    queue, ids = _seed_queue(registry)

    queue.moveRows([0, 1], 8)

    assert _order(queue) == ids[2:] + [ids[0], ids[1]]


def test_a_block_dropped_past_the_end_stops_at_the_end(registry):
    queue, ids = _seed_queue(registry)

    queue.moveRows([0, 1], 999)

    assert _order(queue) == ids[2:] + [ids[0], ids[1]]


def test_a_block_move_carries_the_selection_with_it(registry):
    """Uids survive a reorder, which is what lets a drag move a whole selection."""
    queue, ids = _seed_queue(registry)

    picked = [queue.uidAt(2), queue.uidAt(5), queue.uidAt(6)]
    queue.setSelectedMultiple(picked)

    queue.moveRows([2, 5, 6], 3)

    assert queue.selectedRows() == [3, 4, 5], "the selection followed the rows"
    assert sorted(queue.selectedUids()) == sorted(picked)


# --------------------------------------------- selection the GUI did not change
#
# Playback edits the selection without anyone clicking anything, and a GUI
# mirrors the selection locally to repaint fast. These pin the announcement that
# tells it to re-read — without it the mirror keeps a uid that no longer exists
# and the details panel goes on showing a track that is not selected.


def _fresh_playback(registry, count=10):
    """A seeded queue, empty history, and nothing selected anywhere."""
    manager = registry.get_service("queue_manager")
    history = registry.get_service("history_view_model")

    queue, ids = _seed_queue(registry, count)
    manager.set_history([])
    history.notify_reset()
    history.clearSelection()
    return queue, history, ids


def test_the_playing_row_loses_its_selection_when_it_is_played_through(registry):
    from PySide6.QtTest import QSignalSpy

    queue, history, _ = _fresh_playback(registry)
    player = registry.get_service("player_view_model")

    queue.setSelectedExclusive(queue.uidAt(0))
    spy = QSignalSpy(queue.selectionChanged)

    player.next()

    assert queue.selectedUids() == [], "it left the queue and its selection with it"
    assert history.selectedUids() == [], "and did not arrive in history selected"
    assert spy.count() == 1, "announced once, so a GUI mirror can re-read"


def test_a_row_merely_passed_over_stays_selected_and_silent(registry):
    from PySide6.QtTest import QSignalSpy

    queue, _, _ = _fresh_playback(registry)
    player = registry.get_service("player_view_model")

    picked = queue.uidAt(4)
    queue.setSelectedExclusive(picked)
    spy = QSignalSpy(queue.selectionChanged)

    player.next()

    assert queue.selectedUids() == [picked], "row 4 became row 3, still selected"
    assert queue.selectedRows() == [3]
    assert spy.count() == 0, "nothing changed, so nothing was announced"


def test_stepping_back_from_history_does_not_restore_a_selection(registry):
    """Previous is not an undo of the selection — that data is gone.

    The track re-enters the queue as a new instance with a fresh uid, so there
    is nothing for the old selection to attach to.
    """
    queue, history, _ = _fresh_playback(registry)
    player = registry.get_service("player_view_model")

    played = queue.trackIdAt(0)
    queue.setSelectedExclusive(queue.uidAt(0))
    player.next()
    assert history.trackIdAt(0) == played

    player.previous()

    assert queue.trackIdAt(0) == played, "it came back"
    assert queue.selectedUids() == [], "but not selected"
    assert history.selectedUids() == []


def test_playing_a_queue_row_reaches_the_queue_as_a_move_not_a_reset(registry):
    """A view can only follow a row it was told travelled.

    Through the whole real path — the player, the manager's mutation, and the
    walk the model makes of it — because the arithmetic that turns one into the
    other is the part with somewhere to go wrong.
    """
    queue, _, _ = _fresh_playback(registry)
    player = registry.get_service("player_view_model")
    player.play_queue_rows([0])          # something is playing, and listened to
    player._listened = True

    target = queue.trackIdAt(4)
    target_uid = queue.uidAt(4)
    moves, resets = [], []
    queue.rowsMoved.connect(lambda *a: moves.append(a))
    queue.modelReset.connect(lambda: resets.append(True))

    player.play_queue_rows([4])

    assert resets == [], "a reset would cost the view every delegate"
    assert len(moves) == 1, "one row travelled"
    assert queue.trackIdAt(0) == target
    assert queue.uidAt(0) == target_uid, "the same row, not a rebuilt one"


def test_stepping_back_reaches_the_queue_as_an_insert_not_a_reset(registry):
    """A GUI can only animate an arrival it was told about.

    ``rowsInserted`` carries where the row landed; ``modelReset`` says only that
    everything is different now, and a view that hears it rebuilds every row
    from scratch with nothing to distinguish the new one.
    """
    queue, _, _ = _fresh_playback(registry)
    player = registry.get_service("player_view_model")
    player.next()

    inserted, resets = [], []
    queue.rowsInserted.connect(lambda parent, first, last: inserted.append((first, last)))
    queue.modelReset.connect(lambda: resets.append(True))
    before = queue.rowCount()

    player.previous()

    assert inserted == [(0, 0)], "one row, at the front"
    assert resets == []
    assert queue.rowCount() == before + 1


def test_a_reorder_announces_nothing(registry):
    """Uids survive a move, so the selection is unchanged by construction."""
    from PySide6.QtTest import QSignalSpy

    queue, ids = _seed_queue(registry)
    queue.setSelectedMultiple([queue.uidAt(2), queue.uidAt(5)])
    spy = QSignalSpy(queue.selectionChanged)

    queue.moveRows([2, 5], 3)

    assert spy.count() == 0
    assert queue.selectedRows() == [3, 4]


def test_removing_a_selection_announces_once(registry):
    """Not once per contiguous run — the GUI has no use for the middle states."""
    from PySide6.QtTest import QSignalSpy

    queue, _ = _seed_queue(registry)
    queue.setSelectedMultiple([queue.uidAt(1), queue.uidAt(3), queue.uidAt(4)])
    spy = QSignalSpy(queue.selectionChanged)

    queue.removeSelected()

    assert queue.selectedUids() == []
    assert spy.count() == 1


def test_the_details_panel_lets_go_of_a_track_that_was_played_through(registry):
    """The whole point, end to end: sidebarVM stops reporting the stale track.

    The GUI wiring in between is QML, so this stands in for it — the panel is
    fed by whatever the table last reported, and the table re-reads on
    `selectionChanged`.
    """
    queue, _, _ = _fresh_playback(registry)
    player = registry.get_service("player_view_model")
    sidebar = registry.get_service("sidebar_view_model")
    sidebar.active = True

    queue.setSelectedExclusive(queue.uidAt(0))
    sidebar.setSelection(queue.selectedTrackIds())
    assert sidebar.mode == "single"
    shown = sidebar.trackId

    player.next()
    sidebar.setSelection(queue.selectedTrackIds())   # what the re-read reports

    assert sidebar.trackId != shown, "not the track that was played through"
    assert sidebar.trackId == queue.trackIdAt(0), "fell back to what is playing"


def test_a_block_move_keeps_the_rows_readable(registry):
    """The lazily-filled fields still resolve against the rows they belong to."""
    queue, ids = _seed_queue(registry)
    queue.flush_now()
    moved_title = queue.data(queue.index(5), TITLE)
    assert moved_title is not None

    queue.moveRows([5], 1)
    queue.flush_now()

    assert queue.data(queue.index(1), TITLE) == moved_title


def test_playlist_round_trip(registry):
    search = registry.get_service("search_view_model")
    playlists = registry.get_service("playlists_view_model")
    tracks = registry.get_service("playlist_tracks_view_model")

    search.text_search("Track 001")
    ids = search.get_result_ids()[:4]

    playlist_id = playlists.createPlaylist("Test list")
    playlists.addTracksToPlaylist(playlist_id, ids)

    tracks.load(playlist_id)
    assert tracks.rowCount() == 4
    assert tracks.playlistName == "Test list"
    assert tracks.trackIds() == ids

    tracks.flush_now()
    assert tracks.data(tracks.index(0), TITLE) is not None

    tracks.setSelectedMultiple([tracks.uidAt(0)])
    tracks.removeSelectedFromPlaylist()
    assert tracks.rowCount() == 3


def test_renaming_or_deleting_reaches_the_open_playlist(registry):
    """The header of an open playlist follows a rename made anywhere else.

    The name is read once by load(), so without a playlist.updated subscription
    the hero keeps painting the old one until the playlist is reopened.
    """
    search = registry.get_service("search_view_model")
    playlists = registry.get_service("playlists_view_model")
    tracks = registry.get_service("playlist_tracks_view_model")

    # The registry is module-scoped and later tests read whatever playlist is
    # left open, so put it back before returning.
    previously_open = tracks.playlistId

    search.text_search("Track 002")
    ids = search.get_result_ids()[:2]

    playlist_id = playlists.createPlaylist("Before")
    playlists.addTracksToPlaylist(playlist_id, ids)
    tracks.load(playlist_id)
    assert tracks.playlistName == "Before"

    playlists.renamePlaylist(playlist_id, "After")
    assert tracks.playlistName == "After"

    # A rename of some other playlist must leave this one alone.
    other_id = playlists.createPlaylist("Untouched")
    playlists.renamePlaylist(other_id, "Still untouched")
    assert tracks.playlistName == "After"

    playlists.deletePlaylist(playlist_id)
    assert tracks.playlistId == -1
    assert tracks.rowCount() == 0

    playlists.deletePlaylist(other_id)
    if previously_open > 0:
        tracks.load(previously_open)


def test_export_view_model_starts_inert(registry, tmp_path):
    """Export must be configurable but unable to run until it has a folder."""
    export = registry.get_service("export_view_model")

    assert export.format == "opus"
    assert export.formats == ["opus", "mp3"]
    assert export.destination == ""
    assert export.destinationValid is False
    assert export.canExport is False, "nowhere to write yet"
    assert export.isRunning is False

    # QML hands paths over as file:// URLs; the plain path is what gets stored.
    export.destination = tmp_path.as_uri()
    assert export.destination == str(tmp_path).replace("\\", "/")
    assert export.destinationValid is True
    assert export.canExport is True


def test_exporting_a_missing_playlist_reports_instead_of_raising(registry):
    export = registry.get_service("export_view_model")

    seen: list[str] = []
    export.error_occurred.connect(seen.append)
    export.exportPlaylist(999_999)
    export.error_occurred.disconnect(seen.append)

    assert seen and "no longer exists" in seen[0]
    assert export.isRunning is False


def test_nothing_can_fetch_on_a_fresh_vault(registry):
    """No plugin is on until the user turns one on — through the real DI graph too."""
    plugins = registry.get_service("plugins_view_model")

    assert plugins.hasAudioSource is False
    assert plugins.hasIdentity is False
    assert plugins.importSources == []
    assert registry.get_service("provider_registry").audio_sources() == []


def test_a_download_of_no_row_starts_no_thread(registry):
    """The refusal happens in the view model, before any worker exists."""
    search = registry.get_service("search_view_model")

    blocked: list = []
    search.download_blocked.connect(lambda uid, reason: blocked.append(uid))
    search.downloadRow(12345)

    assert blocked == [], "there is no such row, so nothing to refuse either"
    assert search._download_thread is None, "nothing was started"
    assert search.isDownloading is False


def test_volume_starts_at_the_default_on_a_fresh_vault(registry):
    from app.settings.playback import DEFAULT_VOLUME

    player = registry.get_service("player_view_model")
    backend = registry.get_service("audio_backend")

    assert player.volume == pytest.approx(DEFAULT_VOLUME)
    assert backend._volume == pytest.approx(DEFAULT_VOLUME), "pushed to the output too"


def test_volume_survives_a_restart(registry):
    """Set it, shut down, come back to it — through the real DI graph.

    ``SettingsStore(None)`` re-reads both documents from disk, which is what
    the next launch does; the level the player then starts at is whatever a
    fresh ``PlaybackSettings`` reads out of settings.json.
    """
    from app.settings.playback import PlaybackSettings
    from app.settings.store import SettingsStore

    player = registry.get_service("player_view_model")
    store = registry.get_service("settings_store")

    player.set_volume(0.63)
    store.flush()

    assert PlaybackSettings(SettingsStore(None)).volume == pytest.approx(0.63)


def test_muting_persists_as_muted(registry):
    """Zero is a saved value, not an absent one — closing muted comes back muted."""
    from app.settings.playback import PlaybackSettings
    from app.settings.store import SettingsStore

    player = registry.get_service("player_view_model")
    store = registry.get_service("settings_store")

    player.set_volume(0.0)
    store.flush()

    assert PlaybackSettings(SettingsStore(None)).volume == 0.0


def test_output_device_survives_a_restart(registry):
    """Pin a device, shut down, come back pinned to it.

    Both decks, not just the audible one: they trade places at the end of a
    crossfade, so a device applied to one of them lasts until the next
    instrumental toggle and no longer.
    """
    from app.settings.playback import PlaybackSettings
    from app.settings.store import SettingsStore

    player = registry.get_service("player_view_model")
    backend = registry.get_service("audio_backend")
    store = registry.get_service("settings_store")

    device = backend.available_output_devices()[0]
    device_id = bytes(device.id()).decode()

    player.audioOutputDeviceId = device_id
    store.flush()

    for deck in backend._decks:
        assert bytes(deck.output.device().id()).decode() == device_id

    restored = PlaybackSettings(SettingsStore(None))
    assert restored.output_device == device_id
    # Stored beside the id so the device is still recognisable if it comes back
    # under another Bluetooth profile.
    assert restored.output_device_name == device.description()

    player.audioOutputDeviceId = ""
    store.flush()
    assert PlaybackSettings(SettingsStore(None)).output_device == ""


def test_instrumental_gain_only_lifts_the_instrumental(registry):
    """The trim is per-variant: the mix must come out exactly as before.

    Reaching into ``_level`` rather than reading the outputs back is deliberate
    — QAudioOutput rounds what it is given, and what is asserted here is the
    arithmetic, not Qt's storage of it.
    """
    from app.utils.library_paths import BACKING_CATEGORY, DEFAULT_CATEGORY

    player = registry.get_service("player_view_model")
    backend = registry.get_service("audio_backend")

    player.set_volume(0.4)
    player.backingGainDb = 6.0

    deck = backend._active
    deck.category = DEFAULT_CATEGORY
    assert backend._level(deck) == pytest.approx(0.4), "mixes are untouched"

    deck.category = BACKING_CATEGORY
    # +6 dB is a hair under double the amplitude.
    assert backend._level(deck) == pytest.approx(0.4 * 10 ** 0.3, rel=1e-6)


def test_instrumental_gain_cannot_push_the_output_past_full(registry):
    """There is no headroom above 1.0, and asking for it must not raise."""
    from app.utils.library_paths import BACKING_CATEGORY

    player = registry.get_service("player_view_model")
    backend = registry.get_service("audio_backend")

    player.set_volume(0.9)
    player.backingGainDb = 12.0

    backend._active.category = BACKING_CATEGORY
    assert backend._level(backend._active) == 1.0


def test_instrumental_gain_survives_a_restart(registry):
    from app.settings.backing_track import BackingTrackSettings
    from app.settings.store import SettingsStore

    player = registry.get_service("player_view_model")
    store = registry.get_service("settings_store")

    player.backingGainDb = 4.5
    store.flush()

    assert BackingTrackSettings(SettingsStore(None)).gain_db == pytest.approx(4.5)


def test_instrumental_gain_is_clamped_to_its_range(registry):
    from app.settings.backing_track import MAX_GAIN_DB, MIN_GAIN_DB

    player = registry.get_service("player_view_model")

    player.backingGainDb = 99.0
    assert player.backingGainDb == pytest.approx(MAX_GAIN_DB)

    player.backingGainDb = -99.0
    assert player.backingGainDb == pytest.approx(MIN_GAIN_DB)


# ------------------------------------------------------- per-track gain


def test_track_gain_trims_both_variants(registry):
    """A track's own gain is about the recording, so it holds for mix and instrumental.

    Contrast with the instrumental trim above, which is per-variant on purpose.
    The two stack rather than one replacing the other.
    """
    from app.utils.library_paths import BACKING_CATEGORY, DEFAULT_CATEGORY

    player = registry.get_service("player_view_model")
    backend = registry.get_service("audio_backend")

    player.set_volume(0.4)
    player.backingGainDb = 0.0
    backend.set_track_gain_db(-6.0)

    quieter = 10 ** -0.3  # -6 dB, a hair over half the amplitude

    deck = backend._active
    deck.category = DEFAULT_CATEGORY
    assert backend._level(deck) == pytest.approx(0.4 * quieter, rel=1e-6)

    deck.category = BACKING_CATEGORY
    assert backend._level(deck) == pytest.approx(0.4 * quieter, rel=1e-6)


def test_track_gain_and_instrumental_gain_stack(registry):
    """A quietly-mastered song with a quiet instrumental wants both corrections."""
    from app.utils.library_paths import BACKING_CATEGORY

    player = registry.get_service("player_view_model")
    backend = registry.get_service("audio_backend")

    player.set_volume(0.2)
    player.backingGainDb = 6.0
    backend.set_track_gain_db(6.0)

    backend._active.category = BACKING_CATEGORY
    # +6 and +6 is +12 dB, i.e. two doublings — and 0.2 leaves room for both.
    assert backend._level(backend._active) == pytest.approx(0.2 * 10 ** 0.6, rel=1e-6)


def test_track_gain_cannot_push_the_output_past_full(registry):
    """Same ceiling as the instrumental trim, and it must not raise either."""
    from app.utils.library_paths import DEFAULT_CATEGORY

    player = registry.get_service("player_view_model")
    backend = registry.get_service("audio_backend")

    player.set_volume(0.9)
    backend.set_track_gain_db(12.0)

    backend._active.category = DEFAULT_CATEGORY
    assert backend._level(backend._active) == 1.0


def test_loading_a_track_picks_its_gain_up_off_the_row(registry):
    """The trim arrives with the recording, not through a separate push.

    This is what stops a correction landing a moment late — by the time the
    first sample is audible the output is already at the right level.
    """
    from app.models.track import Track

    player = registry.get_service("player_view_model")
    backend = registry.get_service("audio_backend")
    session = registry.get_service("db_session")

    track = session.query(Track).first()
    track.gain_db = -6.0
    session.commit()

    backend.set_track_gain_db(0.0)
    player.load_track(track.id)

    assert backend._track_gain == pytest.approx(10 ** -0.3, rel=1e-6)


def test_a_previewed_gain_only_applies_to_the_track_playing(registry):
    """The editor announces; the player decides whether it is audible."""
    from app.models.track import Track

    player = registry.get_service("player_view_model")
    backend = registry.get_service("audio_backend")
    session = registry.get_service("db_session")

    playing, other = session.query(Track).limit(2).all()
    player.load_track(playing.id)
    backend.set_track_gain_db(0.0)

    player.set_gain_preview(other.id, -12.0)
    assert backend._track_gain == pytest.approx(1.0), "a different track is not audible"

    player.set_gain_preview(playing.id, -6.0)
    assert backend._track_gain == pytest.approx(10 ** -0.3, rel=1e-6)


def test_clearing_the_preview_falls_back_to_what_is_stored(registry):
    """Correct after a revert *and* after a save, because it re-reads the row."""
    from app.models.track import Track

    player = registry.get_service("player_view_model")
    backend = registry.get_service("audio_backend")
    session = registry.get_service("db_session")

    track = session.query(Track).first()
    track.gain_db = 3.0
    session.commit()

    player.load_track(track.id)
    player.set_gain_preview(track.id, -12.0)
    assert backend._track_gain == pytest.approx(10 ** -0.6, rel=1e-6)

    player.clear_gain_preview()
    assert backend._track_gain == pytest.approx(10 ** 0.15, rel=1e-6)


def test_saving_a_gain_is_audible_without_replaying_the_track(registry):
    """`track.updated` carries the new level to the deck that is already playing."""
    from app.models.track import Track

    player = registry.get_service("player_view_model")
    backend = registry.get_service("audio_backend")
    track_service = registry.get_service("track_service")
    session = registry.get_service("db_session")

    track = session.query(Track).first()
    track.gain_db = 0.0
    session.commit()

    player.load_track(track.id)
    assert backend._track_gain == pytest.approx(1.0)

    track_service.update_track_metadata(track.id, {"gain_db": -6.0})

    assert backend._track_gain == pytest.approx(10 ** -0.3, rel=1e-6)


def test_deleting_a_track_removes_it_from_every_list(registry):
    search = registry.get_service("search_view_model")
    tracks = registry.get_service("playlist_tracks_view_model")
    track_service = registry.get_service("track_service")

    search.load_all_tracks()
    victim = tracks.trackIds()[0]
    search_before = search.rowCount()
    playlist_before = tracks.rowCount()

    track_service.delete_track(victim)

    assert search.rowCount() == search_before - 1
    assert tracks.rowCount() == playlist_before - 1
    assert victim not in tracks.trackIds()


def test_list_totals_sum_track_durations(registry):
    """The playing-time headers add up, counting a repeated track twice."""
    search = registry.get_service("search_view_model")
    queue = registry.get_service("queue_view_model")

    search.load_all_tracks()
    assert search.totalDuration == search.rowCount() * 180

    before = queue.rowCount()
    track_id = search.get_result_ids()[0]
    queue.append_track_ids([track_id, track_id])

    assert queue.rowCount() == before + 2
    assert queue.totalDuration == (before + 2) * 180, "the same track queued twice counts twice"


# ============================================ what a shutdown writes, and where


def test_shutdown_puts_the_queue_in_the_session_and_the_volume_in_the_settings(registry):
    """The split, through the real DI graph and the real shutdown pass.

    Reading the files rather than the store: the store is what decides which
    document a key belongs to, so asking it would be asking the code under test
    to grade its own answer.
    """
    import json

    from app.bootstrap import persist_app_state
    from app.session import session_path
    from app.settings.file import settings_path

    player = registry.get_service("player_view_model")
    search = registry.get_service("search_view_model")
    queue = registry.get_service("queue_view_model")

    search.text_search("Track 01")
    ids = search.get_result_ids()[:3]
    queue.append_track_ids(ids)
    queue.flush_now()
    player.set_volume(0.42)

    persist_app_state(registry)

    session = json.loads(session_path().read_text(encoding="utf-8"))
    settings = json.loads(settings_path().read_text(encoding="utf-8"))

    assert session["app"]["queue_track_ids"][-len(ids):] == list(ids)
    assert "history_track_ids" in session["app"]
    assert settings["app"]["volume"] == pytest.approx(0.42)
    assert "volume" not in session["app"]
    assert "queue_track_ids" not in settings["app"]


def test_the_settings_singleton_is_registered_over_the_same_store(qt_app, registry):
    """`Kroma.Settings` is what lets Theme.qml persist without its own file.

    A view model of its own, because Qt will not let one registered instance be
    reached from two engines — but over the *store* the rest of the app uses,
    which is the part that decides where a value ends up.
    """
    from app.main import _build_settings_singleton, _register_qml_types

    _register_qml_types(registry)
    singleton = _build_settings_singleton(qt_app)

    assert singleton.store is registry.get_service("settings_store")
    assert singleton.guiPackage == registry.get_service("settings_view_model").guiPackage

    singleton.setValue("accent", "ember")
    assert registry.get_service("settings_view_model").value("accent") == "ember"
