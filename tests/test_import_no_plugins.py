"""Importing with no plugin at all — the plain install, and the floor.

Kroma Player is a library first. Everything online is a plugin's, and a library
with none installed has to work without a hint that anything is missing: a
folder of tagged files comes in with its own names and its own artwork, and
nothing on screen offers what nothing can do.

Three things are pinned, through the real service graph:

* **A tagged folder imports as itself.** No identify step runs — there is
  nothing to ask — so no row is "matched nothing" (amber), the run does not
  claim to have matched anything, and the cover embedded in each file is the
  cover the library keeps. Before plugins an unmatched file lost its artwork
  here; the stash now reads it from the file whenever nothing else supplied one.
* **Nothing online is offered.** No audio source, no identity, no import
  source; the CSV and link entries are absent from the Import menu (that menu
  is pinned in tests/test_import_button.py) and the matching options are not
  drawn.
* **The ids a library already holds are left alone,** which is
  tests/test_plugin_toggle_preserves_data.py's subject in full.
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.utils.binaries import ffmpeg_path  # noqa: E402
from app.utils.proc import no_window  # noqa: E402

FFMPEG = ffmpeg_path()
needs_ffmpeg = pytest.mark.skipif(
    not FFMPEG, reason="ffmpeg is not available; it ships in release builds"
)


def _png() -> bytes:
    """A small, real picture — the cover pipeline decodes what it is given."""
    from PySide6.QtCore import QBuffer, QByteArray, QIODevice
    from PySide6.QtGui import QColor, QImage

    image = QImage(64, 64, QImage.Format.Format_RGB32)
    image.fill(QColor(200, 40, 90))
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    image.save(buffer, "PNG")
    buffer.close()
    return bytes(data.data())


def _tagged_opus(path: Path, title: str, artist: str, picture: bytes) -> Path:
    """A short silent Opus file with a title, an artist and an embedded cover."""
    from mutagen.oggopus import OggOpus

    from app.importer.tagging import build_picture_block_from_bytes

    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            FFMPEG or "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
            "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
            "-t", "2", "-c:a", "libopus", str(path),
        ],
        check=True, capture_output=True, **no_window(),
    )
    audio = OggOpus(str(path))
    audio["TITLE"] = title
    audio["ARTIST"] = artist
    audio["METADATA_BLOCK_PICTURE"] = [build_picture_block_from_bytes(picture, mime="image/png")]
    audio.save()
    return path


def _pump_until(qt_app, condition, timeout: float = 30.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        qt_app.processEvents()
        if condition():
            return True
        time.sleep(0.01)
    qt_app.processEvents()
    return bool(condition())


@pytest.fixture
def app_graph(qt_app, tmp_path):
    """The real registry over a fresh vault: no plugin enabled, none installed."""
    from app.utils.library_paths import set_library_root

    vault = tmp_path / "venomvault"
    (vault / ".data").mkdir(parents=True)
    set_library_root(vault)

    from app.bootstrap import create_service_registry

    registry = create_service_registry()
    yield registry
    registry.get_service("plugin_host").shutdown()


def test_nothing_online_is_offered(app_graph):
    plugins = app_graph.get_service("plugins_view_model")
    providers = app_graph.get_service("provider_registry")
    search = app_graph.get_service("search_view_model")

    assert plugins.hasAudioSource is False
    assert plugins.hasIdentity is False
    assert plugins.importSources == []
    assert providers.declarations() == []
    assert search.sources == [], "no source picker in Browse"


@needs_ffmpeg
def test_a_tagged_folder_imports_as_itself_with_its_own_artwork(app_graph, qt_app, tmp_path):
    folder = tmp_path / "rips"
    picture = _png()
    _tagged_opus(folder / "one.opus", "First Light", "Nobody Famous", picture)
    _tagged_opus(folder / "two.opus", "Second Wind", "Nobody Famous", picture)

    vm = app_graph.get_service("import_view_model")
    ready: list = []
    vm.analysis_ready.connect(lambda: ready.append(True))
    vm.analyzePaths([str(folder)])
    assert _pump_until(qt_app, lambda: bool(ready)), "the folder was never scanned"

    done: list = []
    vm.search_completed.connect(lambda: done.append(True))
    vm.beginImport(False, [])
    assert _pump_until(qt_app, lambda: bool(done) and not vm.isSearching)

    # No identify step ran, so nothing failed to match: no amber, no "matched
    # 0 of 2", and every row is simply ready to file.
    assert vm.lastRunIdentified is False
    assert vm.notFoundCount == 0
    states = dict(vm.stateCounts)
    assert states.get("tagsOnly", 0) == 0
    assert states.get("stash", 0) == 2

    landed: list = []
    vm.stash_completed.connect(lambda uid, track_id: landed.append(track_id))
    vm.stashAll(True)
    assert _pump_until(qt_app, lambda: len(landed) == 2 and not vm.isStashing, timeout=60)

    tracks = app_graph.get_service("track_service")
    saved = [tracks.get_track_by_id(track_id) for track_id in landed]
    assert sorted(t.title for t in saved) == ["First Light", "Second Wind"]
    assert all(t.cover_hash for t in saved), "the embedded cover was dropped"
    assert all(not t.external_id_rows for t in saved), "nothing invented an id"


def test_the_matching_options_are_not_drawn(app_graph, qt_app):
    """A switch about how matching behaves, with nothing to match against, is a
    switch about nothing."""
    from PySide6.QtCore import QObject, QUrl
    from PySide6.QtQml import QQmlApplicationEngine

    from app.artwork.cover_provider import CoverImageProvider
    from app.artwork.local_art_provider import LocalArtImageProvider
    from app.gui.gui_paths import bundled_gui_root
    from app.main import CONTEXT_PROPERTIES, _register_qml_types

    _register_qml_types(app_graph)
    directory = bundled_gui_root() / "nocturne"
    engine = QQmlApplicationEngine()
    engine.addImageProvider("cover", CoverImageProvider(app_graph.get_service("cover_store")))
    engine.addImageProvider("localart", LocalArtImageProvider())
    context = engine.rootContext()
    for qml_name, service in CONTEXT_PROPERTIES.items():
        context.setContextProperty(qml_name, app_graph.get_service(service))
    engine.addImportPath(str(directory))
    engine.load(QUrl.fromLocalFile(str(directory / "main.qml")))
    roots = engine.rootObjects()
    assert roots
    qt_app.processEvents()

    dialog = roots[0].findChild(QObject, "importOptionsDialog")
    assert dialog is not None
    assert dialog.property("canIdentify") is False

    del engine
