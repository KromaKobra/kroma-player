"""System media integration: what the desktop sees, and what it can ask for.

Split the way the code is. The conversions and the artwork export are plain
functions over stub objects, so the fiddly parts — microseconds, object paths,
partial release dates — are tested with no bus and no Qt, in the style of
``test_audio_device.py``. Everything that genuinely needs D-Bus is gathered at
the bottom behind a skip, so a machine or CI container without a session bus
still runs the rest rather than erroring out.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.artwork import LARGE_SIZE  # noqa: E402
from app.artwork.cover_store import CoverStore  # noqa: E402
from app.system import mpris_art, mpris_metadata  # noqa: E402


class _StubGenre:
    def __init__(self, name: str) -> None:
        self.name = name


class _StubTrack:
    """Just enough Track to build metadata from: no session, no ORM."""

    def __init__(self, **overrides) -> None:
        self.id = 42
        self.title = "Nightdrive"
        self.artist = "Kavinsky, Lovefoxxx"
        self.album = "OutRun"
        self.release_date = 20130225
        self.bpm = 118
        self.genres = [_StubGenre("Synthwave")]
        for key, value in overrides.items():
            setattr(self, key, value)


# ----------------------------------------------------------------------
# Time and identity conversions
# ----------------------------------------------------------------------


def test_seconds_survive_a_round_trip_through_microseconds():
    assert mpris_metadata.to_micros(12.5) == 12_500_000
    assert mpris_metadata.from_micros(12_500_000) == 12.5


def test_microsecond_conversion_rounds_rather_than_truncates():
    # Position arrives as a float from the decoder; truncating would drift the
    # scrubber a little further behind on every update.
    assert mpris_metadata.to_micros(0.0000009) == 1


def test_a_track_id_becomes_a_valid_object_path_and_back():
    path = mpris_metadata.track_object_path(42)
    assert path == "/org/kroma/kord/track/42"
    assert mpris_metadata.track_id_from_path(path) == 42


def test_no_track_uses_the_specs_reserved_path():
    assert mpris_metadata.track_object_path(None) == mpris_metadata.NO_TRACK_PATH


@pytest.mark.parametrize(
    "path",
    [
        "/org/mpris/MediaPlayer2/TrackList/NoTrack",
        "/some/other/player/track/42",
        "/org/kroma/kord/track/notanumber",
        "",
    ],
)
def test_a_foreign_or_malformed_object_path_resolves_to_no_track(path):
    """SetPosition compares against this; anything unparseable must not match."""
    assert mpris_metadata.track_id_from_path(path) is None


# ----------------------------------------------------------------------
# PlaybackStatus
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "is_playing,has_track,expected",
    [
        (True, True, "Playing"),
        (False, True, "Paused"),
        (False, False, "Stopped"),
        # Nothing loaded wins over a stale playing flag: there is no such thing
        # as playing no track.
        (True, False, "Stopped"),
    ],
)
def test_two_booleans_map_onto_the_three_mpris_states(is_playing, has_track, expected):
    assert mpris_metadata.playback_status(is_playing, has_track) == expected


# ----------------------------------------------------------------------
# Dates and artists
# ----------------------------------------------------------------------


@pytest.mark.parametrize(
    "stored,expected",
    [
        (20130225, "2013-02-25"),
        (20130200, "2013-02"),   # month known, day not
        (20130000, "2013"),      # year only
        (0, None),
        (None, None),
    ],
)
def test_a_partial_release_date_yields_the_longest_iso_prefix_it_supports(stored, expected):
    """Real libraries have year-only dates; inventing January 1st would be a lie."""
    assert mpris_metadata.iso_date(stored) == expected


def test_the_display_artist_string_is_split_back_into_a_list():
    assert mpris_metadata.split_artists("Kavinsky, Lovefoxxx") == ["Kavinsky", "Lovefoxxx"]


def test_splitting_artists_drops_empty_fragments():
    assert mpris_metadata.split_artists("Solo,") == ["Solo"]
    assert mpris_metadata.split_artists("") == []
    assert mpris_metadata.split_artists(None) == []


# ----------------------------------------------------------------------
# Metadata assembly
# ----------------------------------------------------------------------


def test_metadata_carries_the_fields_a_media_widget_draws():
    meta = mpris_metadata.build_metadata(_StubTrack(), 214.0, "file:///art.png")

    assert meta["mpris:trackid"] == "/org/kroma/kord/track/42"
    assert meta["mpris:length"] == 214_000_000
    assert meta["mpris:artUrl"] == "file:///art.png"
    assert meta["xesam:title"] == "Nightdrive"
    assert meta["xesam:artist"] == ["Kavinsky", "Lovefoxxx"]
    assert meta["xesam:album"] == "OutRun"
    assert meta["xesam:genre"] == ["Synthwave"]
    assert meta["xesam:contentCreated"] == "2013-02-25"
    assert meta["xesam:audioBPM"] == 118


def test_absent_fields_are_omitted_rather_than_sent_empty():
    """An empty string draws an empty album line; a missing key draws nothing."""
    track = _StubTrack(album=None, genres=[], release_date=None, bpm=None, artist=None)
    meta = mpris_metadata.build_metadata(track, 0.0, None)

    for absent in (
        "xesam:album",
        "xesam:genre",
        "xesam:contentCreated",
        "xesam:audioBPM",
        "xesam:artist",
        "mpris:artUrl",
        "mpris:length",  # a zero duration is "not known yet", not "zero long"
    ):
        assert absent not in meta

    # The two a client cannot do without are always present.
    assert meta["mpris:trackid"] == "/org/kroma/kord/track/42"
    assert meta["xesam:title"] == "Nightdrive"


def test_metadata_for_no_track_is_just_the_no_track_id():
    meta = mpris_metadata.build_metadata(None, 0.0, None)
    assert meta == {"mpris:trackid": mpris_metadata.NO_TRACK_PATH}


def test_a_track_with_no_title_still_gets_one():
    meta = mpris_metadata.build_metadata(_StubTrack(title=""), 10.0, None)
    assert meta["xesam:title"] == "Unknown Title"


# ----------------------------------------------------------------------
# Artwork export
# ----------------------------------------------------------------------


def _avif_bytes(color=(200, 30, 90)) -> bytes:
    import io

    from PIL import Image

    from app.artwork.image_ops import ensure_avif_support

    ensure_avif_support()
    buf = io.BytesIO()
    Image.new("RGB", (LARGE_SIZE, LARGE_SIZE), color).save(buf, format="AVIF")
    return buf.getvalue()


@pytest.fixture
def store(tmp_path):
    return CoverStore(tmp_path / "covers.db")


def test_a_cached_cover_is_exported_as_a_readable_png(store, tmp_path):
    from PIL import Image

    store.put("abc123", LARGE_SIZE, _avif_bytes())
    out = mpris_art.export_cover(store, "abc123", tmp_path / "art")

    assert out is not None and out.exists()
    with Image.open(out) as im:
        assert im.format == "PNG"
        assert im.size == (LARGE_SIZE, LARGE_SIZE)


def test_exporting_the_same_cover_twice_reuses_the_file(store, tmp_path):
    """Content-addressed, so every track sharing an album cover decodes once."""
    store.put("abc123", LARGE_SIZE, _avif_bytes())
    cache = tmp_path / "art"

    first = mpris_art.export_cover(store, "abc123", cache)
    assert first is not None
    stamp = first.stat().st_mtime_ns

    second = mpris_art.export_cover(store, "abc123", cache)
    assert second == first
    assert second.stat().st_mtime_ns == stamp


@pytest.mark.parametrize("cover_hash", [None, "", "never-cached"])
def test_art_that_cannot_be_produced_returns_none_instead_of_raising(store, tmp_path, cover_hash):
    """Callers omit mpris:artUrl on None. A missing cover must not stop playback."""
    assert mpris_art.export_cover(store, cover_hash, tmp_path / "art") is None


def test_an_undecodable_blob_leaves_no_half_written_file_behind(store, tmp_path):
    store.put("garbage", LARGE_SIZE, b"this is not an image")
    cache = tmp_path / "art"

    assert mpris_art.export_cover(store, "garbage", cache) is None
    # A leftover empty file would be served forever by the exists() fast path.
    assert not (cache / "garbage.png").exists()


def test_art_urls_escape_the_spaces_real_vault_paths_have():
    url = mpris_art.art_url(Path("/home/u/My Music/.data/mpris-art/a b.png"))
    assert url == "file:///home/u/My%20Music/.data/mpris-art/a%20b.png"
    assert mpris_art.art_url(None) is None


# ----------------------------------------------------------------------
# The bus itself
# ----------------------------------------------------------------------


def _session_bus_or_skip():
    try:
        from PySide6.QtDBus import QDBusConnection
    except ImportError:  # pragma: no cover - platform dependent
        pytest.skip("QtDBus is not available")
    bus = QDBusConnection.sessionBus()
    if not bus.isConnected():
        pytest.skip("no session bus")
    return bus


def _fake_player_class():
    """Build the stand-in for PlayerViewModel lazily.

    It has to be a QObject with real Signals, because the service connects to
    them — but QObject cannot be imported at module scope here without pulling
    Qt into the pure-function tests above, which are meant to run without it.
    """
    from PySide6.QtCore import QObject, Signal

    class _FakePlayer(QObject):
        """PlayerViewModel's public surface, as MPRIS uses it.

        Properties are plain attributes rather than Qt Properties: the service
        reads them by attribute access, so this stays readable and lets a test
        move the playhead with an assignment.
        """

        current_track_changed = Signal()
        is_playing_changed = Signal()
        duration_changed = Signal()
        volume_changed = Signal()
        position_changed = Signal()
        seeked = Signal(float)

        def __init__(self):
            QObject.__init__(self)
            self._init_state()

        def _init_state(self):
            self.calls = []
            self.is_playing = False
            self.has_current_track = True
            self.current_track_id = 42
            self.current_track_cover_hash = ""
            self.position = 30.0
            self.duration = 200.0
            self.volume = 0.5
            self.can_go_next = True
            self.can_go_previous = True
            # Doubles as its own track service, which is all the service asks of it.
            self.track_service = self

        def get_tracks_by_ids(self, ids):
            return [_StubTrack()]

        def play(self):
            self.calls.append("play")

        def pause(self):
            self.calls.append("pause")

        def toggle_playback(self):
            self.calls.append("toggle")

        def stop_player(self):
            self.calls.append("stop")

        def next(self):
            self.calls.append("next")

        def previous(self):
            self.calls.append("previous")

        def seek(self, seconds):
            self.calls.append(("seek", round(seconds, 3)))

        def set_volume(self, volume):
            self.calls.append(("volume", volume))

    return _FakePlayer


@pytest.fixture
def player(qt_app):
    _session_bus_or_skip()
    return _fake_player_class()()


@pytest.fixture
def service(player):
    """A started MprisService on the real session bus, torn down after.

    Every bus test goes through this one fixture. QDBusConnection.sessionBus()
    is shared process-wide, so an object path can only be registered once at a
    time — a test that leaked a started service would break the next one.
    """
    from app.system.mpris import MprisService, MprisUnavailable

    svc = MprisService(player)
    try:
        svc.start()
    except MprisUnavailable as exc:  # pragma: no cover - environment dependent
        pytest.skip(f"MPRIS unavailable: {exc}")
    yield svc
    svc.stop()


def test_the_service_claims_an_mpris_bus_name(service):
    """Not pinned to the exact name: a real Kroma Player may be running.

    If one is, it holds the primary name and this service correctly falls back
    to the per-instance form. Asserting equality here would make the suite fail
    depending on whether the developer happens to have the app open.
    """
    from app.system.mpris import BUS_NAME

    assert service.bus_name == BUS_NAME or service.bus_name.startswith(f"{BUS_NAME}.instance")


def test_a_second_instance_takes_a_suffixed_name_instead_of_going_invisible(player):
    """Two copies running at once must both stay addressable.

    A real second instance is a second process; here the primary name is taken
    from a separate bus connection to reproduce the same contention, since two
    connections can hold names independently while sharing a bus.
    """
    from PySide6.QtDBus import QDBusConnection

    from app.system.mpris import BUS_NAME, MprisService

    squatter = QDBusConnection.connectToBus(
        QDBusConnection.BusType.SessionBus, "kroma-test-squatter"
    )
    if not squatter.isConnected() or not squatter.registerService(BUS_NAME):
        pytest.skip("could not take the primary bus name to contend for it")

    svc = MprisService(player)
    try:
        svc.start()
        assert svc.bus_name != BUS_NAME
        assert svc.bus_name.startswith(f"{BUS_NAME}.instance")
    finally:
        svc.stop()
        squatter.unregisterService(BUS_NAME)
        QDBusConnection.disconnectFromBus("kroma-test-squatter")


def test_shuffle_and_loopstatus_are_absent_so_clients_disable_those_buttons(service):
    """Kroma's queue has no such modes. See the note in gui/nocturne/README.md.

    Both properties are optional in the spec, and their absence is what makes a
    client grey the controls out instead of offering a switch that does nothing.
    """
    adaptor = service._player_adaptor
    names = {
        adaptor.metaObject().property(i).name()
        for i in range(adaptor.metaObject().propertyOffset(), adaptor.metaObject().propertyCount())
    }
    assert "LoopStatus" not in names
    assert "Shuffle" not in names
    # The ones that must be there, since their absence would disable real controls.
    assert {"PlaybackStatus", "Metadata", "Position", "Volume", "CanSeek"} <= names


def test_transport_methods_reach_the_view_model(service):
    player = service.player
    adaptor = service._player_adaptor

    adaptor.PlayPause()
    adaptor.Next()
    adaptor.Previous()
    adaptor.Stop()

    assert player.calls == ["toggle", "next", "previous", "stop"]


def test_a_relative_seek_is_applied_to_the_current_position(service):
    service._player_adaptor.Seek(-10_000_000)  # ten seconds back from 30s
    assert service.player.calls == [("seek", 20.0)]


def test_seeking_before_the_start_clamps_to_zero(service):
    service._player_adaptor.Seek(-90_000_000)
    assert service.player.calls == [("seek", 0.0)]


def test_seeking_past_the_end_advances_the_queue(service):
    """The spec makes this equivalent to Next rather than a no-op."""
    service._player_adaptor.Seek(500_000_000)
    assert service.player.calls == ["next"]


def test_set_position_seeks_when_the_track_id_matches(service):
    from PySide6.QtDBus import QDBusObjectPath

    service._player_adaptor.SetPosition(
        QDBusObjectPath(mpris_metadata.track_object_path(42)), 45_000_000
    )
    assert service.player.calls == [("seek", 45.0)]


def test_set_position_for_a_different_track_is_ignored(service):
    """Guards against a client whose scrubber is one track behind."""
    from PySide6.QtDBus import QDBusObjectPath

    service._player_adaptor.SetPosition(
        QDBusObjectPath(mpris_metadata.track_object_path(999)), 45_000_000
    )
    assert service.player.calls == []


def test_set_position_beyond_the_duration_is_ignored(service):
    from PySide6.QtDBus import QDBusObjectPath

    service._player_adaptor.SetPosition(
        QDBusObjectPath(mpris_metadata.track_object_path(42)), 900_000_000
    )
    assert service.player.calls == []


def test_position_is_reported_in_microseconds(service):
    assert service._player_adaptor.property("Position") == 30_000_000


def test_metadata_exposes_the_track_id_as_an_object_path(service):
    from PySide6.QtDBus import QDBusObjectPath

    meta = service._player_adaptor.property("Metadata")
    assert isinstance(meta["mpris:trackid"], QDBusObjectPath)
    assert meta["mpris:trackid"].path() == "/org/kroma/kord/track/42"


def test_can_raise_is_false_until_a_window_is_bound(service):
    assert service._root.property("CanRaise") is False
    service.set_window(object())
    assert service._root.property("CanRaise") is True


def test_a_burst_of_view_model_signals_becomes_one_properties_changed(service, player, qt_app):
    """Loading a track emits four notifies in a row; that is one bus message.

    Without coalescing every property change would be its own signal, and the
    view model fires them in bursts by design.
    """
    sent = []
    service._emit_properties_changed = lambda iface, changed: sent.append((iface, set(changed)))

    player.current_track_changed.emit()
    player.duration_changed.emit()
    player.is_playing_changed.emit()
    player.volume_changed.emit()

    # Nothing goes out until the event loop turns over.
    assert sent == []
    qt_app.processEvents()

    assert len(sent) == 1
    interface, names = sent[0]
    assert interface == "org.mpris.MediaPlayer2.Player"
    assert {"Metadata", "PlaybackStatus", "Volume"} <= names
    # Position advances continuously and is excluded from PropertiesChanged by
    # the spec; announcing it would put messages on the bus several times a second.
    assert "Position" not in names


def test_position_changes_alone_put_nothing_on_the_bus(service, player, qt_app):
    sent = []
    service._emit_properties_changed = lambda iface, changed: sent.append(iface)

    for _ in range(20):
        player.position_changed.emit()
    qt_app.processEvents()

    assert sent == []


def test_properties_changed_types_its_invalidated_list_as_strings(qt_app):
    """The third argument must be "as", not "av", or clients drop the signal.

    An empty Python list marshals as a variant array because there is no element
    to infer the type from, and the resulting "(sa{sv}av)" mismatch is rejected
    silently: dbus-monitor renders it identically, GLib logs a warning nobody
    reads, and every client keeps its first reading of the player forever. The
    visible symptom is a play/pause key that no longer toggles.
    """
    _session_bus_or_skip()
    from app.system.mpris import _empty_string_array

    assert _empty_string_array().currentSignature() == "as"


def test_a_seek_emits_the_seeked_signal_in_microseconds(service, player, qt_app):
    """Clients resync their scrubber on Seeked; position updates can't say "jump"."""
    seen = []
    service._player_adaptor.Seeked.connect(seen.append)

    player.seeked.emit(42.5)

    assert seen == [42_500_000]


def test_stopping_is_idempotent_and_safe_before_start(player):
    from app.system.mpris import MprisService

    svc = MprisService(player)
    svc.stop()  # never started
    assert svc.bus_name is None
    svc.stop()
