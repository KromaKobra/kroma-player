"""Turning a plugin off — even uninstalling it — never costs the library a thing.

The plugin owns *what an id means*; the library owns the id. So a plugin going
away takes its features with it and leaves every id, every portrait and every
file tag exactly where it was, through whatever the user does in the meantime,
and a plugin coming back finds the library as it left it.

This is the whole of that promise, end to end, through the real service graph
and a real installed plugin:

1. a library with all four kinds of id, an artist portrait, and the track ids
   in the files' own tags;
2. the plugin turned off, then uninstalled;
3. the things a user does without it: edit a track, an artist and an album;
   unify an album's artwork; claim a track onto an album; export; import other
   files; delete a track that has nothing to do with any of it;
4. the plugin reinstalled and turned back on — no second agreement, since the
   terms did not change.

Then every id, portrait and tag is what it was. ``is_complete`` may move — but
only on the album whose membership the claim changed.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import zipfile
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.utils.binaries import ffmpeg_path  # noqa: E402
from app.utils.proc import no_window  # noqa: E402
from app.versions import KPP_FORMAT_VERSION  # noqa: E402
from tests.fakes.plugin_env import ECHO, FIXTURE, isolate  # noqa: E402
from tests.fakes.providers import held_id, id_rows  # noqa: E402

FFMPEG = ffmpeg_path()
pytestmark = [
    pytest.mark.plugin_process,
    pytest.mark.skipif(not FFMPEG, reason="ffmpeg is not available; it ships in release builds"),
]

VID1, VID2, VID4 = "Ab1Cd2Ef3Gh", "Ij4Kl5Mn6Op", "Qr7St8Uv9Wx"
INST1 = "Yz0Ab1Cd2Ef"
CHANNEL = "UCfakeChannel0000000001A"
RELEASE = "MPREb_fakeRelease001"
PORTRAIT = "p" * 32

VIDEO_KEY, INST_KEY = "echo:video", "echo:instrumental-video"
CHANNEL_KEY, RELEASE_KEY = "echo:channel", "echo:release"
ID_TAG = "ECHOID"


def _kpp(tmp_path: Path) -> Path:
    """The fixture plugin, declaring a kind of id for every entity, and one for instrumentals."""
    manifest = json.loads((FIXTURE / "plugin.json").read_text())
    manifest["externalIds"] = [
        {"entity": "track", "provider": "echo", "kind": "video", "role": "recording",
         "label": "Echo id", "parse": ["^([A-Za-z0-9_-]{11})$"],
         "readTags": [ID_TAG], "writeTag": ID_TAG},
        {"entity": "track", "provider": "echo", "kind": "instrumental-video",
         "role": "instrumental", "label": "Echo instrumental id",
         "parse": ["^([A-Za-z0-9_-]{11})$"]},
        {"entity": "artist", "provider": "echo", "kind": "channel",
         "label": "Echo channel", "parse": ["^(UC[A-Za-z0-9_-]{22})$"]},
        {"entity": "album", "provider": "echo", "kind": "release",
         "label": "Echo release", "parse": ["^(MPREb_[A-Za-z0-9_-]+)$"]},
    ]
    package = tmp_path / "echo.kpp"
    with zipfile.ZipFile(package, "w") as archive:
        archive.writestr("kpp.json", json.dumps({"format": KPP_FORMAT_VERSION, "id": ECHO}))
        archive.writestr("plugin.json", json.dumps(manifest))
        for name in ("main.py", "agreement.md"):
            archive.writestr(name, (FIXTURE / name).read_text())
    return package


def _opus(path: Path, tags: dict) -> Path:
    from mutagen.oggopus import OggOpus

    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo",
         "-t", "2", "-c:a", "libopus", str(path)],
        check=True, capture_output=True, **no_window(),
    )
    audio = OggOpus(str(path))
    for key, value in tags.items():
        audio[key] = value
    audio.save()
    return path


def _file_tag(path: Path, key: str):
    from mutagen.oggopus import OggOpus

    tags = OggOpus(str(path)).tags or {}
    values = tags.get(key)
    return values[0] if values else None


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
def world(qt_app, tmp_path, monkeypatch):
    """A library seeded with every kind of id, and the plugin installed and on."""
    from app.models.track import Album, Artist, Track
    from app.plugins import installer
    from app.utils.library_paths import set_library_root, track_path

    isolate(tmp_path, monkeypatch)
    vault = tmp_path / "venomvault"
    (vault / ".data").mkdir(parents=True)
    set_library_root(vault)

    from app.bootstrap import create_service_registry

    registry = create_service_registry()
    host = registry.get_service("plugin_host")
    package = _kpp(tmp_path)
    installer.install(package, stop=host.stop)
    host.discover()
    host.accept_consent(ECHO)
    assert host.set_enabled(ECHO, True) == ""

    session = registry.get_service("db_session")
    known = Artist(
        name="Known Artist", image_hash=PORTRAIT,
        external_id_rows=id_rows("artist", {CHANNEL_KEY: CHANNEL}),
    )
    local = Artist(name="Local Artist")
    session.add_all([known, local])
    session.flush()
    release = Album(
        name="The Release", is_complete=True,
        external_id_rows=id_rows("album", {RELEASE_KEY: RELEASE}),
    )
    homemade = Album(name="Homemade", is_complete=True)
    # The unrelated track's own record: deleting it changes this album's
    # membership and no other, which is the point of it being unrelated.
    elsewhere = Album(name="Elsewhere")
    session.add_all([release, homemade, elsewhere])
    session.flush()

    def track(title, artist, album, **ids):
        t = Track(
            title=title, duration_seconds=2, artists=[artist],
            external_id_rows=id_rows("track", ids),
        )
        t.albums = [album]
        session.add(t)
        return t

    t1 = track("One", known, release, **{VIDEO_KEY: VID1, INST_KEY: INST1})
    t2 = track("Two", known, release, **{VIDEO_KEY: VID2})
    t3 = track("Three", local, homemade)
    t4 = track("Unrelated", local, elsewhere, **{VIDEO_KEY: VID4})
    session.commit()

    for t in (t1, t2, t3, t4):
        tags = {"TITLE": t.title, "ARTIST": t.artists[0].name}
        if held_id(t, VIDEO_KEY):
            tags[ID_TAG] = held_id(t, VIDEO_KEY)
        _opus(track_path(t.id), tags)

    yield {
        "registry": registry, "host": host, "package": package, "session": session,
        "t1": t1.id, "t2": t2.id, "t3": t3.id, "t4": t4.id,
        "known": known.id, "local": local.id,
        "release": release.id, "homemade": homemade.id,
        "tmp": tmp_path,
    }
    host.shutdown()


def _snapshot(world) -> dict:
    """Every id, portrait, file tag and completeness flag this test protects."""
    from app.models.track import Album, Artist, Track
    from app.utils.library_paths import track_path

    session = world["session"]
    session.expire_all()
    out: dict = {}
    for key in ("t1", "t2", "t3"):
        t = session.get(Track, world[key])
        out[key] = (held_id(t, VIDEO_KEY), held_id(t, INST_KEY),
                    _file_tag(track_path(t.id), ID_TAG))
    for key in ("known", "local"):
        a = session.get(Artist, world[key])
        out[key] = (held_id(a, CHANNEL_KEY), a.image_hash)
    for key in ("release", "homemade"):
        al = session.get(Album, world[key])
        out[key] = (held_id(al, RELEASE_KEY),)
        out[key + ":complete"] = bool(al.is_complete)
    return out


def test_nothing_is_lost_while_the_plugin_is_away(world, qt_app, tmp_path):
    from app.main import CONTEXT_PROPERTIES
    from app.plugins import installer

    registry = world["registry"]
    host = world["host"]
    plugins = registry.get_service("plugins_view_model")
    providers = registry.get_service("provider_registry")
    edit = registry.get_service(CONTEXT_PROPERTIES["editVM"])
    artist = registry.get_service(CONTEXT_PROPERTIES["artistVM"])
    album = registry.get_service(CONTEXT_PROPERTIES["albumVM"])
    albums = registry.get_service("album_service")
    tracks = registry.get_service("track_service")
    export = registry.get_service(CONTEXT_PROPERTIES["exportVM"])
    imports = registry.get_service(CONTEXT_PROPERTIES["importVM"])

    assert len(providers.declarations()) == 4
    before = _snapshot(world)
    assert before["t1"] == (VID1, INST1, VID1), "the seed is what the test thinks it is"
    assert before["known"] == (CHANNEL, PORTRAIT)

    # -- 2. off, then gone ------------------------------------------------------
    plugins.setEnabled(ECHO, False)
    assert providers.declarations() == []
    assert plugins.uninstall(ECHO) is True
    assert host.record(ECHO) is None, "off and not installed: nothing to list"

    errors: list[str] = []
    for vm in (edit, artist, album, imports):
        vm.error_occurred.connect(errors.append)

    # -- 3. a user's afternoon without it ---------------------------------------
    edit.load(world["t1"])
    assert edit.externalIdFields == [], "no plugin, no id fields — and none cleared"
    edit.title = "One (renamed)"
    edit.save()
    assert _pump_until(qt_app, lambda: not edit.isSaving)

    artist.load(world["known"])
    artist.name = "Known Artist (renamed)"
    artist.save()

    album.load(world["release"])
    album.name = "The Release (renamed)"
    album.save()

    album.load(world["release"])
    album.unify("u" * 32, [])          # the album's own sleeve, no files rewritten
    _pump_until(qt_app, lambda: not album.isBusy)

    albums.claim_tracks(world["release"], [world["t3"]], [
        {"title": "One", "external_ids": {VIDEO_KEY: VID1}},
        {"title": "Two", "external_ids": {VIDEO_KEY: VID2}},
    ])
    claimed_complete = bool(albums.album_repo.get_by_id(world["release"]).is_complete)

    destination = tmp_path / "exported"
    destination.mkdir()
    export.destination = destination.as_uri()
    export.exportTracks([world["t1"], world["t2"]], "Afternoon")
    assert _pump_until(qt_app, lambda: not export.isRunning, timeout=60)

    folder = tmp_path / "more"
    _opus(folder / "fresh.opus", {"TITLE": "Fresh", "ARTIST": "Somebody New"})
    ready: list = []
    imports.analysis_ready.connect(lambda: ready.append(True))
    imports.analyzePaths([str(folder)])
    assert _pump_until(qt_app, lambda: bool(ready))
    done: list = []
    imports.search_completed.connect(lambda: done.append(True))
    imports.beginImport(False, [])
    assert _pump_until(qt_app, lambda: bool(done) and not imports.isSearching)
    landed: list = []
    imports.stash_completed.connect(lambda uid, track_id: landed.append(track_id))
    imports.stashAll(True)
    assert _pump_until(qt_app, lambda: landed and not imports.isStashing, timeout=60)

    assert tracks.delete_track(world["t4"]) is True

    assert errors == [], errors
    # Every step above did what it says — a test of "nothing was lost" that
    # quietly did nothing would pass for the wrong reason.
    world["session"].expire_all()
    assert tracks.get_track_by_id(world["t1"]).title == "One (renamed)"
    assert registry.get_service("artist_service").get(world["known"]).name == "Known Artist (renamed)"
    assert albums.album_repo.get_by_id(world["release"]).name == "The Release (renamed)"
    assert world["t3"] in albums.album_repo.track_ids(world["release"]), "the claim landed"
    assert export.lastExported == 2
    assert len(landed) == 1 and not tracks.get_track_by_id(landed[0]).external_id_rows
    assert tracks.get_track_by_id(world["t4"]) is None

    # -- 4. back, and on ---------------------------------------------------------
    installer.install(world["package"], stop=host.stop)
    host.discover()
    asked: list = []
    plugins.consent_required.connect(lambda *args: asked.append(args))
    plugins.setEnabled(ECHO, True)
    assert asked == [], "the same terms, already agreed to, are not asked again"
    assert host.usable(ECHO)
    assert len(providers.declarations()) == 4

    # -- 5. as it was -----------------------------------------------------------
    after = _snapshot(world)
    for key in ("t1", "t2", "t3", "known", "local", "release", "homemade"):
        assert after[key] == before[key], f"{key} changed: {before[key]} → {after[key]}"

    # Completeness moved only where membership did: the claim re-decided the
    # release, and nothing touched the homemade album's.
    assert after["release:complete"] == claimed_complete
    assert after["homemade:complete"] == before["homemade:complete"]

    # And the editor sees the ids again, from the same rows.
    edit.load(world["t1"])
    shown = {f["key"]: f["saved"] for f in edit.externalIdFields}
    assert shown == {VIDEO_KEY: VID1, INST_KEY: INST1}
