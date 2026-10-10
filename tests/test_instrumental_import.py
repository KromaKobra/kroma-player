"""Importing an instrumental for one track.

An instrumental is not a track. It is a second file for a track the library
already has: the same recording without vocals, stored in ``bt/`` under the
mix's own id. That identity is the whole design — nothing pairs the two files
except their name, so nothing can get out of step, and there is no row, no
metadata and no search anywhere in it.

Which is why this moved off the import screen. There it was a mode over a whole
matched batch, so every file had to be searched online first just to work
out which library track it belonged to: a network round trip to answer a
question the user could already answer, and a batch of guesses where one
certainty would do. On the edit page the track is simply the one on screen.

Three things are then required of the file, and all three are tested here
against real audio rather than a stub, because all three are claims about what
ends up on disk:

* it is the same length as the mix, or the mid-playback toggle is not seamless;
* it is Opus, whatever it arrived as;
* it carries no tags at all.

The last one is the one that was quietly untrue: `ensure_opus` strips tags on
the paths that re-encode, but copies an already-Opus source verbatim — which is
exactly what a user handing over an instrumental they exported themselves has.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from tests.fakes.providers import INSTRUMENTAL, FakeAudio, FakeRegistry, held_id
from mutagen.oggopus import OggOpus

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.events.event_bus import EventBus  # noqa: E402
from app.importer.tagging import strip_opus_tags  # noqa: E402
from app.services.backing_track_service import BackingTrackService  # noqa: E402
from app.utils.binaries import ffmpeg_path  # noqa: E402
from app.utils.library_paths import track_path  # noqa: E402
from app.utils.proc import no_window  # noqa: E402

FFMPEG = ffmpeg_path()
needs_ffmpeg = pytest.mark.skipif(
    not FFMPEG, reason="ffmpeg is not available; it ships in release builds"
)

TRACK_ID = 0x2A


def _tone(path: Path, seconds: float, codec: str) -> Path:
    """Write a silent test file of an exact length in the requested codec."""
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            FFMPEG or "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", f"anullsrc=r=48000:cl=stereo",
            "-t", f"{seconds}", "-c:a", codec, str(path),
        ],
        check=True, capture_output=True, **no_window(),
    )
    return path


@pytest.fixture
def vault(isolated_library_root):
    """A vault with one track's mix in it, so there is something to match."""
    return isolated_library_root


@pytest.fixture
def mix(vault):
    path = track_path(TRACK_ID)
    path.parent.mkdir(parents=True, exist_ok=True)
    return _tone(path, 3.0, "libopus")


@pytest.fixture
def backing(vault):
    return BackingTrackService(EventBus())


# -- the pairing ----------------------------------------------------------


def test_the_instrumental_takes_the_mixs_own_filename(backing):
    """The only thing that pairs the two files, so it is worth asserting.

    Same shard, same id, same extension — one hex name in two trees. Nothing
    else records the relationship, which is what makes it impossible to have a
    stale one.
    """
    assert backing.path(TRACK_ID).name == track_path(TRACK_ID).name
    assert backing.path(TRACK_ID) != track_path(TRACK_ID)
    assert backing.path(TRACK_ID).suffix == ".opus"


# -- what lands on disk ---------------------------------------------------


@needs_ffmpeg
def test_a_matching_file_is_installed_as_the_instrumental(backing, mix, tmp_path):
    source = _tone(tmp_path / "instrumental.opus", 3.0, "libopus")

    installed = backing.import_file(TRACK_ID, source)

    assert installed == backing.path(TRACK_ID)
    assert installed.exists()
    assert backing.has(TRACK_ID)


@needs_ffmpeg
def test_any_format_arrives_as_opus(backing, mix, tmp_path):
    """No web search, no metadata lookup — just a transcode."""
    source = _tone(tmp_path / "instrumental.flac", 3.0, "flac")

    installed = backing.import_file(TRACK_ID, source)

    assert installed is not None
    assert installed.suffix == ".opus"
    assert OggOpus(str(installed)).info.length == pytest.approx(3.0, abs=0.05)


@needs_ffmpeg
def test_an_instrumental_arrives_with_no_metadata_at_all(backing, mix, tmp_path):
    """The mix holds the metadata. A second copy here could only go stale.

    An already-Opus source is the case that matters: `ensure_opus` copies it
    verbatim, so without an explicit strip its tags would ride into the vault.
    """
    source = _tone(tmp_path / "tagged.opus", 3.0, "libopus")
    tagged = OggOpus(str(source))
    tagged.add_tags() if tagged.tags is None else None
    tagged["TITLE"] = ["Someone else's title"]
    tagged["ARTIST"] = ["Someone else"]
    tagged["ALBUM"] = ["Some album"]
    tagged.save()
    assert OggOpus(str(source)).tags, "the fixture failed to tag anything"

    installed = backing.import_file(TRACK_ID, source)

    assert installed is not None
    assert not OggOpus(str(installed)).tags, "the instrumental kept its tags"


@needs_ffmpeg
def test_a_generated_instrumental_is_stripped_too(backing, mix, tmp_path):
    """`install` skips the checks `import_file` does, but not this one."""
    source = _tone(tmp_path / "generated.opus", 3.0, "libopus")
    audio = OggOpus(str(source))
    audio.add_tags() if audio.tags is None else None
    audio["TITLE"] = ["Left over from the separator"]
    audio.save()

    installed = backing.install(TRACK_ID, source)

    assert not OggOpus(str(installed)).tags


@needs_ffmpeg
def test_strip_leaves_the_audio_alone(tmp_path):
    """Tags go; the stream does not get re-encoded on the way past."""
    path = _tone(tmp_path / "a.opus", 2.0, "libopus")
    audio = OggOpus(str(path))
    audio.add_tags() if audio.tags is None else None
    audio["TITLE"] = ["x"]
    audio.save()
    before = OggOpus(str(path)).info.length

    strip_opus_tags(path)

    assert not OggOpus(str(path)).tags
    assert OggOpus(str(path)).info.length == pytest.approx(before, abs=0.001)


# -- the length rule ------------------------------------------------------


@needs_ffmpeg
def test_a_different_length_file_is_refused(backing, mix, tmp_path):
    """The toggle happens mid-playback; a drifting instrumental is unusable."""
    source = _tone(tmp_path / "too-long.opus", 4.0, "libopus")

    with pytest.raises(ValueError, match="length"):
        backing.import_file(TRACK_ID, source)

    assert not backing.path(TRACK_ID).exists(), "a refused file was installed anyway"
    assert not backing.has(TRACK_ID)


@needs_ffmpeg
def test_a_missing_file_is_refused_before_anything_is_written(backing, mix, tmp_path):
    with pytest.raises(FileNotFoundError):
        backing.import_file(TRACK_ID, tmp_path / "nope.opus")
    assert not backing.path(TRACK_ID).exists()


@needs_ffmpeg
def test_a_stopped_import_leaves_the_vault_untouched(backing, mix, tmp_path):
    """Same boundary contract as every other import — see `stash.StopCheck`."""
    source = _tone(tmp_path / "instrumental.opus", 3.0, "libopus")

    assert backing.import_file(TRACK_ID, source, lambda: True) is None
    assert not backing.path(TRACK_ID).exists()
    assert not backing.has(TRACK_ID)


# -- the edit page's end of it --------------------------------------------


def _pump_until(qt_app, predicate, timeout: float = 15.0) -> bool:
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if predicate():
            return True
        time.sleep(0.005)
    return False


@pytest.fixture
def edit_vm(qt_app, db, event_bus, backing):
    from app.models.track import Artist, Track
    from app.repositories.artist_repository import ArtistRepository
    from app.repositories.genre_repository import GenreRepository
    from app.repositories.track_repository import TrackRepository
    from app.services.track_service import TrackService
    from app.viewmodels.track_edit_view_model import TrackEditViewModel

    artist = Artist(name="Someone")
    db.session.add(artist)
    db.session.flush()
    track = Track(title="A Song", duration_seconds=3, artists=[artist])
    db.session.add(track)
    db.session.commit()

    service = TrackService(
        TrackRepository(db.session),
        ArtistRepository(db.session),
        GenreRepository(db.session),
        event_bus,
    )
    model = TrackEditViewModel(
        service, backing, registry=FakeRegistry(audio=FakeAudio()),
    )
    model.load(track.id)
    assert model.hasTrack
    return model


def test_the_button_has_nothing_to_offer_without_a_track(qt_app, backing, db, event_bus):
    from app.repositories.artist_repository import ArtistRepository
    from app.repositories.genre_repository import GenreRepository
    from app.repositories.track_repository import TrackRepository
    from app.services.track_service import TrackService
    from app.viewmodels.track_edit_view_model import TrackEditViewModel

    service = TrackService(
        TrackRepository(db.session),
        ArtistRepository(db.session),
        GenreRepository(db.session),
        event_bus,
    )
    model = TrackEditViewModel(service, backing)

    assert model.hasInstrumental is False
    model.importInstrumental("/music/anything.opus")
    assert model.isImportingInstrumental is False, "started without a track open"


@needs_ffmpeg
def test_the_edit_page_imports_one_and_says_so(qt_app, edit_vm, backing, tmp_path):
    """The whole feature, end to end, off the UI thread."""
    track_id = edit_vm.trackId
    _tone(track_path(track_id), 3.0, "libopus")          # the mix
    source = _tone(tmp_path / "instrumental.flac", 3.0, "flac")

    assert edit_vm.hasInstrumental is False

    imported: list[int] = []
    edit_vm.instrumental_imported.connect(imported.append)
    edit_vm.importInstrumental(str(source))

    assert edit_vm.isImportingInstrumental is True
    assert _pump_until(qt_app, lambda: not edit_vm.isImportingInstrumental)

    assert imported == [track_id]
    assert edit_vm.hasInstrumental is True
    assert backing.path(track_id).exists()
    assert not OggOpus(str(backing.path(track_id))).tags


def test_the_button_is_on_the_edit_page_and_follows_the_track(qt_app, tmp_path):
    """The feature's surface, on the real loaded GUI.

    `test_gui_packages` loads this same window but cannot catch a mistake here:
    a misspelt property is not a QML error, it is `undefined`, and the button
    would simply sit there in its default state forever. This drives the view
    model and watches the button answer.
    """
    from PySide6.QtCore import QObject, QUrl
    from PySide6.QtQml import QQmlApplicationEngine

    from app.artwork.cover_provider import CoverImageProvider
    from app.artwork.local_art_provider import LocalArtImageProvider
    from app.gui.gui_paths import bundled_gui_root
    from app.main import CONTEXT_PROPERTIES, _register_qml_types
    from app.models.track import Artist, Track
    from app.utils.library_paths import set_library_root

    vault = tmp_path / "venomvault"
    (vault / ".data").mkdir(parents=True)
    set_library_root(vault)

    from app.bootstrap import create_service_registry

    registry = create_service_registry()
    _register_qml_types(registry)

    directory = bundled_gui_root() / "nocturne"
    engine = QQmlApplicationEngine()
    engine.addImageProvider(
        "cover", CoverImageProvider(registry.get_service("cover_store"))
    )
    engine.addImageProvider("localart", LocalArtImageProvider())
    context = engine.rootContext()
    for qml_name, service in CONTEXT_PROPERTIES.items():
        context.setContextProperty(qml_name, registry.get_service(service))
    engine.addImportPath(str(directory))
    engine.load(QUrl.fromLocalFile(str(directory / "main.qml")))

    roots = engine.rootObjects()
    assert roots, "the GUI failed to load"
    qt_app.processEvents()

    button = roots[0].findChild(QObject, "importInstrumentalButton")
    assert button is not None, "the instrumental button is gone or renamed"

    def state():
        qt_app.processEvents()
        return button.property("text"), button.property("enabledAction")

    edit = registry.get_service(CONTEXT_PROPERTIES["editVM"])
    session = registry.get_service("db_session")
    artist = Artist(name="Someone")
    session.add(artist)
    session.flush()
    track = Track(title="A Song", duration_seconds=3, artists=[artist])
    session.add(track)
    session.commit()

    edit.load(track.id)
    text, enabled = state()
    assert text.startswith("Import") and enabled is True, text

    registry.get_service("backing_track_service").mark_available(track.id)
    edit.instrumental_changed.emit()
    text, enabled = state()
    assert text.startswith("Replace"), f"an existing instrumental is not shown: {text}"

    def find_item(name):
        # A Repeater's delegates are children of its parent *item*, not QObject
        # children of anything `findChild` walks — so walk the item tree.
        stack = [roots[0].contentItem()]
        while stack:
            item = stack.pop()
            if item.objectName() == name:
                return item
            stack.extend(item.childItems())
        return None

    # No plugin, no way to fetch one: the row and its button are not drawn.
    assert find_item("fetchInstrumentalButton") is None

    # An enabled audio source that can fetch instrumentals brings both.
    from tests.fakes.providers import FakeAudio, FakeRegistry, use_registry

    use_registry(edit, FakeRegistry(audio=FakeAudio()))
    for _ in range(5):
        qt_app.processEvents()
    fetch = find_item("fetchInstrumentalButton")
    assert fetch is not None, "the fetch button is gone or renamed"
    assert fetch.property("text") == "Fetch"
    assert fetch.property("enabledAction") is False, "enabled with no id typed"

    del engine


@needs_ffmpeg
def test_a_rejected_file_is_explained_not_swallowed(qt_app, edit_vm, backing, tmp_path):
    track_id = edit_vm.trackId
    _tone(track_path(track_id), 3.0, "libopus")
    source = _tone(tmp_path / "wrong.opus", 5.0, "libopus")

    complaints: list[str] = []
    edit_vm.error_occurred.connect(complaints.append)
    edit_vm.importInstrumental(str(source))

    assert _pump_until(qt_app, lambda: not edit_vm.isImportingInstrumental)
    assert len(complaints) == 1 and "length" in complaints[0]
    assert edit_vm.hasInstrumental is False


# -- fetching one from an audio source -------------------------------------
#
# A separate upload of an instrumental is almost never the mix's length to the
# frame, so a fetched one is installed whatever its length. The file import
# keeps its rule; only the caller that asks for it gets the exception.

VIDEO_ID = "Xq7Lm2Pz9Ka"


@needs_ffmpeg
def test_a_mismatched_file_is_refused_by_default(backing, mix, tmp_path):
    source = _tone(tmp_path / "longer.opus", 3.5, "libopus")

    with pytest.raises(ValueError, match="length"):
        backing.import_file(TRACK_ID, source)
    assert not backing.has(TRACK_ID)


@needs_ffmpeg
def test_a_mismatched_file_is_installed_when_the_length_is_not_asked_for(
    backing, mix, tmp_path
):
    source = _tone(tmp_path / "longer.opus", 3.5, "libopus")
    tagged = OggOpus(str(source))
    tagged.add_tags() if tagged.tags is None else None
    tagged["TITLE"] = ["Someone else's title"]
    tagged.save()

    installed = backing.import_file(TRACK_ID, source, match_length=False)

    assert installed == backing.path(TRACK_ID)
    assert backing.has(TRACK_ID)
    assert OggOpus(str(installed)).info.length == pytest.approx(3.5, abs=0.05)
    assert not OggOpus(str(installed)).tags


def test_without_an_audio_source_the_fetch_is_refused(backing):
    """No plugin that can fetch an instrumental is a refusal, never a free pass."""
    from app.importer.import_workers import InstrumentalFetchWorker

    worker = InstrumentalFetchWorker(TRACK_ID, INSTRUMENTAL, VIDEO_ID, backing, FakeRegistry())
    failures: list[str] = []
    finished: list = []
    worker.failed.connect(failures.append)
    worker.finished.connect(lambda *args: finished.append(args))
    worker.run()

    assert failures == ["No enabled plugin can fetch an instrumental from that"]
    assert finished == []
    assert not backing.has(TRACK_ID)


def _source_with_audio(tmp_path, seconds: float):
    """An audio source whose fetch writes a tone where the plugin would."""
    tone = _tone(tmp_path / "fetched.opus", seconds, "libopus")
    return FakeAudio(source_file=tone)


@needs_ffmpeg
def test_the_worker_installs_what_it_fetched(backing, mix, tmp_path):
    from app.importer import import_workers

    audio = _source_with_audio(tmp_path, 3.4)
    worker = import_workers.InstrumentalFetchWorker(
        TRACK_ID, INSTRUMENTAL, VIDEO_ID, backing, FakeRegistry(audio=audio),
    )
    finished: list = []
    worker.finished.connect(lambda *args: finished.append(args))
    worker.run()

    assert finished == [(TRACK_ID, INSTRUMENTAL, VIDEO_ID)]
    assert audio.calls == [("instrumental", {INSTRUMENTAL: VIDEO_ID})]
    assert backing.has(TRACK_ID)


@needs_ffmpeg
def test_the_edit_page_fetches_one_and_records_the_id(
    qt_app, edit_vm, backing, db, tmp_path
):
    """End to end, off the UI thread — and nothing else staged is lost."""
    from app.models.track import Track

    edit_vm._registry.audio = _source_with_audio(tmp_path, 3.4)
    track_id = edit_vm.trackId
    _tone(track_path(track_id), 3.0, "libopus")          # the mix

    edit_vm.title = "Still typing"

    imported: list[int] = []
    edit_vm.instrumental_imported.connect(imported.append)
    edit_vm.fetchInstrumental(f"https://fake.example/watch?v={VIDEO_ID}")

    assert edit_vm.isFetchingInstrumental is True
    assert edit_vm.isImportingInstrumental is True
    assert _pump_until(qt_app, lambda: not edit_vm.isImportingInstrumental)

    assert imported == [track_id]
    assert edit_vm.hasInstrumental is True
    assert edit_vm.isFetchingInstrumental is False
    db.session.expire_all()
    assert held_id(db.session.get(Track, track_id), INSTRUMENTAL) == VIDEO_ID
    # The id settled, the title did not.
    field = next(f for f in edit_vm.externalIdFields if f["key"] == INSTRUMENTAL)
    assert field["saved"] == VIDEO_ID
    assert edit_vm.title == "Still typing"
    assert edit_vm.isDirty is True


def test_a_fetch_needs_an_id(qt_app, edit_vm):
    complaints: list[str] = []
    edit_vm.error_occurred.connect(complaints.append)

    edit_vm.fetchInstrumental("")
    edit_vm.fetchInstrumental("not a video")

    assert edit_vm.isImportingInstrumental is False
    assert complaints == [
        "Enter the instrumental's Fake instrumental id first",
        "“not a video” is not a valid Fake instrumental id",
    ]


def test_the_fetch_is_only_offered_when_something_can_do_it(qt_app, edit_vm):
    assert edit_vm.canFetchInstrumental is True

    edit_vm._registry.set(audio=None)

    assert edit_vm.canFetchInstrumental is False
