"""The track editor: the database write, the retag, and the staged view model.

The interesting guarantees are all about *not* losing things — an edit must not
drop the tags it did not mention, must not silently discard text it could not
parse, and must not cost the user their save just because the audio file was
busy. Each of those gets a test here.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
from mutagen.oggopus import OggOpus

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.importer.tagging import build_picture_block_from_bytes, tag_opus_file, update_opus_tags
from app.models.track import Artist, Genre, Track
from app.repositories.artist_repository import ArtistRepository
from app.repositories.genre_repository import GenreRepository
from app.repositories.track_repository import TrackRepository
from app.services.track_service import TrackService
from tests.fakes.providers import INSTRUMENTAL, VIDEO, held_id, set_id


@pytest.fixture
def service(db, event_bus, isolated_library_root):
    return TrackService(
        TrackRepository(db.session),
        ArtistRepository(db.session),
        GenreRepository(db.session),
        event_bus,
    )


# ---------------------------------------------------------------- tagging


def _make_opus(path: Path) -> bool:
    """Encode a second of silence to Opus. False when ffmpeg is not installed."""
    import subprocess

    from app.utils.binaries import ffmpeg_path

    ffmpeg = ffmpeg_path()
    if ffmpeg is None:
        return False
    subprocess.run(
        [ffmpeg, "-y", "-loglevel", "error", "-f", "lavfi",
         "-i", "anullsrc=r=48000:cl=stereo", "-t", "1", "-c:a", "libopus", str(path)],
        check=True,
    )
    return path.is_file()


def test_update_opus_tags_keeps_everything_it_was_not_told_about(tmp_path):
    """The whole point of the read-modify-write path."""
    target = tmp_path / "track.opus"
    if not _make_opus(target):
        pytest.skip("ffmpeg is not on PATH, so there is no Opus file to tag")

    picture = build_picture_block_from_bytes(b"not really an image", mime="image/avif")
    tag_opus_file(
        target,
        {
            "local_id": "002A",
            "title": "Old Title",
            "artists": "Old Artist",
            "album": "Old Album",
            "release_date": 20200101,
            "lyrics": "a line of lyrics",
            "isrc": "OLDISRC000001",
        },
        picture_block=picture,
    )

    update_opus_tags(target, {"TITLE": "New Title", "ISRC": ""})

    tags = OggOpus(str(target)).tags
    assert tags["TITLE"] == ["New Title"]
    # Untouched keys survive — this is what tag_opus_file would have destroyed.
    assert tags["LOCALID"] == ["002A"]
    assert tags["ARTIST"] == ["Old Artist"]
    assert tags["LYRICS"] == ["a line of lyrics"]
    assert tags["METADATA_BLOCK_PICTURE"] == [picture]
    # An empty value clears the tag rather than writing a blank one.
    assert "ISRC" not in tags


# ------------------------------------------------------------ the service


def test_update_track_metadata_writes_scalars_and_relationships(service, db):
    track_id = db.session.query(Track).first().id

    result = service.update_track_metadata(
        track_id,
        {
            "title": "Renamed",
            "bpm": 174,
            "artists": "Brand New Artist, Artist 000",
            "genres": "Drum and Bass",
        },
    )

    assert result.track is not None
    track = db.session.get(Track, track_id)
    assert track.title == "Renamed"
    assert track.bpm == 174
    assert [a.name for a in track.artists] == ["Brand New Artist", "Artist 000"]
    assert [g.name for g in track.genres] == ["Drum and Bass"]

    # New names create rows; existing ones are reused rather than duplicated.
    assert db.session.query(Artist).filter_by(name="Brand New Artist").count() == 1
    assert db.session.query(Artist).filter_by(name="Artist 000").count() == 1
    assert db.session.query(Genre).filter_by(name="Drum and Bass").count() == 1


def test_update_track_metadata_emits_track_updated(service, db, event_bus):
    seen = []
    event_bus.subscribe("track.updated", seen.append)

    track_id = db.session.query(Track).first().id
    service.update_track_metadata(track_id, {"album": "Something Else"})

    assert [t.id for t in seen] == [track_id]


def test_an_unwritable_audio_file_does_not_cost_the_user_their_save(service, db):
    """The isolated vault holds no .opus files, so the retag always fails here —
    which stands in for the real case, a file held open by playback. Either way
    the database write must survive and be reported as a success."""
    track_id = db.session.query(Track).first().id

    result = service.update_track_metadata(track_id, {"title": "Saved Anyway"})

    assert result.track is not None
    assert result.warning != ""
    assert db.session.get(Track, track_id).title == "Saved Anyway"


def test_updating_a_missing_track_reports_rather_than_raises(service):
    result = service.update_track_metadata(999_999, {"title": "Nobody"})
    assert result.track is None


# --------------------------------------------------------- the view model


# Ids the fake plugin's parse rules accept. The seeded tracks carry "v%08d",
# which is nine characters and so not a valid id at all — the parse rule rejects
# those before any of the checks below get a look at them.
NEW_ID = "Xq7Lm2Pz9Ka"
OTHER_ID = "Mn2Pq5Rs8Tv"


def _registry():
    from tests.fakes.providers import FakeMetadata, FakeRegistry

    return FakeRegistry(metadata=FakeMetadata())


@pytest.fixture
def edit_vm(qt_app, service):
    from app.viewmodels.track_edit_view_model import TrackEditViewModel

    return TrackEditViewModel(service, registry=_registry())


def _field(vm, key):
    return next(f for f in vm.externalIdFields if f["key"] == key)


def test_load_stages_a_clean_copy(edit_vm, db):
    track = db.session.query(Track).first()
    edit_vm.load(track.id)

    assert edit_vm.hasTrack
    assert edit_vm.trackId == track.id
    assert edit_vm.title == track.title
    assert edit_vm.artists == track.artists[0].name
    assert not edit_vm.isDirty
    assert not edit_vm.canSave


def test_typing_makes_it_dirty_and_revert_undoes_it(edit_vm, db):
    edit_vm.load(db.session.query(Track).first().id)
    original = edit_vm.title

    edit_vm.title = "Changed"
    assert edit_vm.isDirty
    assert edit_vm.canSave

    edit_vm.revert()
    assert edit_vm.title == original
    assert not edit_vm.isDirty


def test_a_track_cannot_be_saved_without_a_title(edit_vm, db):
    edit_vm.load(db.session.query(Track).first().id)
    edit_vm.title = "   "
    assert edit_vm.isDirty
    assert not edit_vm.canSave


def test_unparseable_input_is_refused_rather_than_swallowed(edit_vm, db):
    """Clearing a field and mistyping it are different intentions."""
    track = db.session.query(Track).first()
    edit_vm.load(track.id)

    errors = []
    edit_vm.error_occurred.connect(errors.append)

    edit_vm.setExternalId(VIDEO, "definitely not a video id")
    edit_vm.save()

    assert errors, "a rejected value should say so"
    assert held_id(db.session.get(Track, track.id), VIDEO) == "v00000000"


def test_save_normalises_then_rereads(edit_vm, db):
    track_id = db.session.query(Track).first().id
    edit_vm.load(track_id)

    edit_vm.releaseDate = "1998"
    edit_vm.bpm = "128.6"
    edit_vm.save()

    stored = db.session.get(Track, track_id)
    assert stored.release_date == 19980000
    assert stored.bpm == 129
    # The fields settle on what was stored, not on what was typed.
    assert edit_vm.releaseDate == "1998"
    assert edit_vm.bpm == "129"
    assert not edit_vm.isDirty


def test_clearing_a_field_clears_the_column(edit_vm, db):
    track_id = db.session.query(Track).first().id
    edit_vm.load(track_id)

    edit_vm.isrc = ""
    edit_vm.save()

    assert db.session.get(Track, track_id).isrc is None


def test_an_id_another_track_holds_is_refused(edit_vm, db):
    """One id, one track — web search's "you already have this" depends on it."""
    first, second = db.session.query(Track).limit(2).all()
    set_id(second, VIDEO, OTHER_ID)
    db.session.commit()
    edit_vm.load(first.id)

    errors = []
    edit_vm.error_occurred.connect(errors.append)

    edit_vm.setExternalId(VIDEO, OTHER_ID)
    edit_vm.save()

    assert errors and second.title in errors[0]
    assert held_id(db.session.get(Track, first.id), VIDEO) != OTHER_ID
    # Nothing at all was written, and the edit is still there to correct.
    assert edit_vm.isDirty


def test_a_conflict_holds_back_the_rest_of_the_save_too(edit_vm, db):
    """The other fields go with it, so Save means one thing rather than some."""
    first, second = db.session.query(Track).limit(2).all()
    set_id(second, VIDEO, OTHER_ID)
    db.session.commit()
    edit_vm.load(first.id)

    edit_vm.title = "Should Not Land"
    edit_vm.setExternalId(VIDEO, OTHER_ID)
    edit_vm.save()

    assert db.session.get(Track, first.id).title != "Should Not Land"


def test_reformatting_the_id_a_track_already_has_is_not_a_conflict(edit_vm, db):
    """Pasting the watch URL for the id already stored parses back to itself."""
    track = db.session.query(Track).first()
    set_id(track, VIDEO, NEW_ID)
    db.session.commit()
    edit_vm.load(track.id)

    errors = []
    edit_vm.error_occurred.connect(errors.append)

    edit_vm.setExternalId(VIDEO, f"https://fake.example/watch?v={NEW_ID}")
    edit_vm.title = "Renamed Along The Way"
    edit_vm.save()

    # The vault holds no audio here, so a retag warning is expected; a conflict
    # complaint is not.
    assert not any("already has" in message for message in errors)
    assert db.session.get(Track, track.id).title == "Renamed Along The Way"


def test_an_unused_id_saves(edit_vm, db):
    track = db.session.query(Track).first()
    edit_vm.load(track.id)

    errors = []
    edit_vm.error_occurred.connect(errors.append)

    edit_vm.setExternalId(VIDEO, NEW_ID)
    edit_vm.save()

    assert not any("already has" in message for message in errors)
    assert held_id(db.session.get(Track, track.id), VIDEO) == NEW_ID


def test_an_instrumental_link_saves_as_the_bare_id(edit_vm, db):
    """Not a claim on the id — it may even be the one the track itself holds."""
    first, second = db.session.query(Track).limit(2).all()
    set_id(second, VIDEO, NEW_ID)
    db.session.commit()
    edit_vm.load(first.id)

    errors = []
    edit_vm.error_occurred.connect(errors.append)
    edit_vm.setExternalId(INSTRUMENTAL, f"https://fake.example/watch?v={NEW_ID}")
    edit_vm.save()

    assert errors == []
    assert held_id(db.session.get(Track, first.id), INSTRUMENTAL) == NEW_ID
    assert _field(edit_vm, INSTRUMENTAL)["value"] == NEW_ID
    assert not edit_vm.isDirty


def test_an_unparseable_instrumental_id_is_refused(edit_vm, db):
    track = db.session.query(Track).first()
    edit_vm.load(track.id)

    errors = []
    edit_vm.error_occurred.connect(errors.append)
    edit_vm.setExternalId(INSTRUMENTAL, "not a video")
    edit_vm.save()

    assert errors == ["“not a video” is not a valid Fake instrumental id"]
    assert held_id(db.session.get(Track, track.id), INSTRUMENTAL) is None
    assert edit_vm.isDirty


def test_deleting_the_open_track_closes_the_editor(edit_vm, db, event_bus):
    track_id = db.session.query(Track).first().id
    edit_vm.load(track_id)

    event_bus.emit("track.deleted", track_id)

    assert not edit_vm.hasTrack
    assert edit_vm.trackId == -1


# ------------------------------------------- overwriting from a source
#
# The offer follows a save, and what it applies is deliberately narrower than
# "everything": only fields the source supplied, never the audio, never the length.
# Each of those boundaries gets a test, because each of them is the kind that
# looks like an oversight later and gets "fixed" into a data loss.


def test_update_opus_tags_can_replace_the_artwork(tmp_path):
    """The one thing the merge path is now allowed to overwrite unasked."""
    target = tmp_path / "track.opus"
    if not _make_opus(target):
        pytest.skip("ffmpeg is not on PATH, so there is no Opus file to tag")

    original = build_picture_block_from_bytes(b"the old cover", mime="image/avif")
    tag_opus_file(
        target,
        {
            "local_id": "002A",
            "title": "Title",
            "artists": "Artist",
            "album": "Album",
            "release_date": 20200101,
            "bpm": 174,
        },
        picture_block=original,
    )

    replacement = build_picture_block_from_bytes(b"the new cover", mime="image/avif")
    update_opus_tags(target, {"TITLE": "New Title"}, replacement)

    tags = OggOpus(str(target)).tags
    assert tags["METADATA_BLOCK_PICTURE"] == [replacement]
    assert tags["TITLE"] == ["New Title"]
    # Still a merge everywhere else — the artwork is the exception, not a mode.
    assert tags["LOCALID"] == ["002A"]
    assert tags["BPM"] == ["174"]


@pytest.fixture
def covers(tmp_path):
    from app.artwork.cover_store import CoverStore

    return CoverStore(tmp_path / "covers.db")


@pytest.fixture
def cover_service(db, event_bus, covers):
    return TrackService(
        TrackRepository(db.session),
        ArtistRepository(db.session),
        GenreRepository(db.session),
        event_bus,
        cover_store=covers,
    )


def test_replacing_a_cover_retires_the_one_it_displaced(cover_service, db, covers):
    from app.artwork import THUMB_SIZE

    track = db.session.query(Track).first()
    old_hash = track.cover_hash
    covers.put(old_hash, THUMB_SIZE, b"old thumbnail")

    cover_service.update_track_metadata(track.id, {"cover_hash": "f" * 32})

    assert db.session.get(Track, track.id).cover_hash == "f" * 32
    assert covers.get(old_hash, THUMB_SIZE) is None


def test_a_cover_another_track_still_uses_survives(cover_service, db, covers):
    """Art is content-addressed and shared, so one track dropping it proves nothing."""
    from app.artwork import THUMB_SIZE

    first, second = db.session.query(Track).limit(2).all()
    shared = first.cover_hash
    second.cover_hash = shared
    db.session.commit()
    covers.put(shared, THUMB_SIZE, b"shared thumbnail")

    cover_service.update_track_metadata(first.id, {"cover_hash": "f" * 32})

    assert covers.get(shared, THUMB_SIZE) == b"shared thumbnail"


def test_a_new_id_offers_a_refresh(edit_vm, db):
    edit_vm.load(db.session.query(Track).first().id)

    offered = []
    edit_vm.overwrite_offered.connect(offered.append)

    edit_vm.setExternalId(VIDEO, f"https://fake.example/watch?v={NEW_ID}")
    edit_vm.save()

    assert offered == [NEW_ID]


def test_clearing_the_id_offers_nothing(edit_vm, db):
    """A removal has nothing to fetch from."""
    edit_vm.load(db.session.query(Track).first().id)

    offered = []
    edit_vm.overwrite_offered.connect(offered.append)

    edit_vm.setExternalId(VIDEO, "")
    edit_vm.save()

    assert offered == []
    assert not held_id(db.session.get(Track, edit_vm.trackId), VIDEO)


def test_a_save_that_leaves_the_id_alone_offers_nothing(edit_vm, db):
    edit_vm.load(db.session.query(Track).first().id)

    offered = []
    edit_vm.overwrite_offered.connect(offered.append)

    edit_vm.album = "A Different Album"
    edit_vm.save()

    assert offered == []


def test_a_refresh_applies_only_what_the_source_supplied(edit_vm, db):
    """Tempo, key and ISRC are the user's own work; a fetch never clears them."""
    track = db.session.query(Track).first()
    edit_vm.load(track.id)
    original_bpm, original_isrc = track.bpm, track.isrc
    original_duration = track.duration_seconds

    overwritten = []
    edit_vm.refreshed.connect(overwritten.append)

    edit_vm._on_refresh_fetched(
        {
            "title": "The Real Title",
            "artists": "The Real Artist",
            "album": "",                      # the source had none — leave the album be
            "release_date": 19980000,
            "duration_seconds": 999,          # describes the upload, not our file
            "lyrics": "[00:01.00] a line",
        },
        "a" * 32,
        b"pretending to be an avif master",
    )

    stored = db.session.get(Track, track.id)
    assert stored.title == "The Real Title"
    assert [a.name for a in stored.artists] == ["The Real Artist"]
    assert stored.release_date == 19980000
    assert stored.cover_hash == "a" * 32
    # Untouched: not supplied, or not about the video.
    assert stored.album == "Album 0"
    assert stored.bpm == original_bpm
    assert stored.isrc == original_isrc
    assert stored.duration_seconds == original_duration

    assert overwritten == [track.id]
    # The fields settle on what was stored, not on what was fetched.
    assert edit_vm.title == "The Real Title"
    assert edit_vm.coverHash == "a" * 32
    assert not edit_vm.isDirty


def test_a_refresh_without_artwork_keeps_the_existing_cover(edit_vm, db):
    """No reachable thumbnail is not a request to leave the track with none."""
    track = db.session.query(Track).first()
    edit_vm.load(track.id)
    original_cover = track.cover_hash

    edit_vm._on_refresh_fetched({"title": "Still Fine"}, "", None)

    stored = db.session.get(Track, track.id)
    assert stored.title == "Still Fine"
    assert stored.cover_hash == original_cover


def test_a_refresh_writes_the_lyrics_to_both_the_table_and_the_file(
    qt_app, service, db, event_bus,
):
    """A refresh's lyrics land in both places — see ADR 0008.

    The table is what the app reads; the file tag is what travels with the
    audio. `LyricsService.save()` writes the row and then the file, so this
    pins both halves at once.
    """
    from app.repositories.lyrics_repository import LyricsRepository
    from app.services.lyrics_service import LyricsService
    from app.viewmodels.track_edit_view_model import TrackEditViewModel

    lyrics = LyricsService(LyricsRepository(db.session), event_bus)
    edit_vm = TrackEditViewModel(service, lyrics_service=lyrics, registry=_registry())

    track = db.session.query(Track).first()
    opus = Path(track.file_path)
    opus.parent.mkdir(parents=True, exist_ok=True)
    if not _make_opus(opus):
        pytest.skip("ffmpeg is not on PATH, so there is no Opus file to tag")
    tag_opus_file(
        opus,
        {"local_id": "0001", "title": "Old", "artists": "Old",
         "album": "Old", "release_date": 20200101, "bpm": 174},
    )
    edit_vm.load(track.id)

    edit_vm._on_refresh_fetched(
        {"title": "New Title", "artists": "New Artist", "lyrics": "a line"},
        "a" * 32,
        b"pretending to be an avif master",
    )

    tags = OggOpus(str(opus)).tags
    assert tags["TITLE"] == ["New Title"]
    assert tags["ARTIST"] == ["New Artist"]
    assert tags["METADATA_BLOCK_PICTURE"]
    # Nothing the fetch had no opinion about was disturbed.
    assert tags["BPM"] == ["174"]
    assert tags["LOCALID"] == ["0001"]
    # Both copies of the lyrics, and they agree.
    assert lyrics.get(track.id) == "a line"
    assert tags["LYRICS"] == ["a line"]


def test_the_refresh_worker_hands_back_metadata_and_artwork(monkeypatch):
    """The worker writes nothing itself — it asks, fetches and reports."""
    from app.importer import import_workers
    from tests.fakes.providers import FakeMetadata, FakeRegistry, row

    monkeypatch.setattr(import_workers, "lookup_lyrics", lambda title, artist: None)
    monkeypatch.setattr(import_workers, "fetch_first", lambda urls, **kw: b"jpeg bytes")
    registry = FakeRegistry(metadata=FakeMetadata(answers={
        NEW_ID: row("Fetched", "An Artist", video=NEW_ID, artwork=["https://img/1.jpg"]),
    }))

    class FakeCoverService:
        def ingest_source(self, source_image):
            assert source_image == b"jpeg bytes"
            return "b" * 32, b"avif master"

    worker = import_workers.RefreshWorker(
        "track", {VIDEO: NEW_ID}, registry, FakeCoverService(),
    )
    seen = []
    worker.fetched.connect(lambda *args: seen.append(args))
    worker.run()

    info, cover_hash, master = seen[0]
    assert info["title"] == "Fetched" and info["artists"] == "An Artist"
    assert (cover_hash, master) == ("b" * 32, b"avif master")


def test_the_refresh_worker_reports_a_dead_id_rather_than_raising():
    from app.importer import import_workers
    from tests.fakes.providers import FakeMetadata, FakeRegistry

    worker = import_workers.RefreshWorker(
        "track", {VIDEO: NEW_ID}, FakeRegistry(metadata=FakeMetadata()),
    )
    failures = []
    worker.failed.connect(failures.append)
    worker.run()

    assert failures == ["Fake Source returned nothing for that id"]


def test_without_a_metadata_provider_there_is_nothing_to_refresh_from(qt_app, service, db):
    from app.viewmodels.track_edit_view_model import TrackEditViewModel
    from tests.fakes.providers import FakeRegistry

    vm = TrackEditViewModel(service, registry=FakeRegistry())
    vm.load(db.session.query(Track).first().id)

    assert vm.canRefresh is False


def test_with_every_plugin_off_no_id_field_is_shown_and_none_is_lost(qt_app, service, db):
    """Hiding a field never stages a delete — the data-safety rule for editors."""
    from app.viewmodels.track_edit_view_model import TrackEditViewModel
    from tests.fakes.providers import FakeRegistry

    track = db.session.query(Track).first()
    vm = TrackEditViewModel(service, registry=FakeRegistry(kinds=()))
    vm.load(track.id)
    assert vm.externalIdFields == []

    vm.setExternalId(VIDEO, "")          # nothing can be set that is not shown
    vm.title = "Renamed With Plugins Off"
    vm.save()

    assert held_id(db.session.get(Track, track.id), VIDEO) == "v00000000"


# ------------------------------------------------------------------ gain

# The per-track level trim. What makes it different from every other editable
# field is that it is stored *only* in the database — deliberately, so that
# nudging a slider never opens the audio file. The first test here is the one
# that pins that decision down.


def test_a_gain_only_save_never_touches_the_audio_file(service, db, monkeypatch):
    """No field maps to a tag, so the retag has nothing to do and does nothing.

    This is the whole storage decision in one assertion: a control the user
    drags is not a reason to rewrite a file, and a rewrite that cannot happen
    cannot corrupt anything.
    """
    from app.services import track_service as track_service_module

    def refuse(*args, **kwargs):
        raise AssertionError("the audio file was opened for a gain-only save")

    monkeypatch.setattr(track_service_module, "update_opus_tags", refuse)

    track = db.session.query(Track).first()
    result = service.update_track_metadata(track.id, {"gain_db": -4.5})

    assert result.warning == ""
    assert db.session.get(Track, track.id).gain_db == pytest.approx(-4.5)


def test_gain_defaults_to_zero_and_stages_as_a_number(edit_vm, db):
    track = db.session.query(Track).first()
    edit_vm.load(track.id)

    assert edit_vm.gainDb == pytest.approx(0.0)
    assert not edit_vm.isDirty

    edit_vm.gainDb = 3.5
    assert edit_vm.gainDb == pytest.approx(3.5)
    assert edit_vm.isDirty

    edit_vm.revert()
    assert edit_vm.gainDb == pytest.approx(0.0)
    assert not edit_vm.isDirty


def test_gain_is_clamped_to_the_slider_range(edit_vm, db):
    edit_vm.load(db.session.query(Track).first().id)

    edit_vm.gainDb = 99.0
    assert edit_vm.gainDb == pytest.approx(12.0)

    edit_vm.gainDb = -99.0
    assert edit_vm.gainDb == pytest.approx(-12.0)


def test_returning_the_gain_to_zero_saves_rather_than_being_refused(edit_vm, db):
    """0.0 is a legitimate value, and the save loop's falsiness test must not eat it.

    Every other parsed field treats a falsy result as "that would not parse" and
    refuses the whole save. Gain is dead centre of its range at zero, so undoing
    a correction would be indistinguishable from a typo.
    """
    track = db.session.query(Track).first()
    track.gain_db = 6.0
    db.session.commit()

    edit_vm.load(track.id)
    assert edit_vm.gainDb == pytest.approx(6.0)

    errors = []
    edit_vm.error_occurred.connect(errors.append)
    edit_vm.gainDb = 0.0
    edit_vm.save()

    assert errors == []
    assert db.session.get(Track, track.id).gain_db == pytest.approx(0.0)


def test_the_slider_announces_a_preview_and_takes_it_back(edit_vm, db):
    """Dragging is audible; reverting, closing and reopening all undo it."""
    track = db.session.query(Track).first()
    edit_vm.load(track.id)

    previews = []
    cleared = []
    edit_vm.gain_preview_requested.connect(lambda tid, db_: previews.append((tid, db_)))
    edit_vm.gain_preview_cleared.connect(lambda: cleared.append(True))

    edit_vm.gainDb = -6.0
    assert previews == [(track.id, pytest.approx(-6.0))]

    # Setting the same value again is not a second drag step.
    edit_vm.gainDb = -6.0
    assert len(previews) == 1

    edit_vm.revert()
    assert len(cleared) == 1

    edit_vm.clear()
    assert len(cleared) == 2


def test_a_track_with_a_stored_gain_loads_clean(edit_vm, db):
    """The one-decimal staging format has to round-trip, or every open is dirty."""
    track = db.session.query(Track).first()
    track.gain_db = -4.5
    db.session.commit()

    edit_vm.load(track.id)

    assert edit_vm.gainDb == pytest.approx(-4.5)
    assert not edit_vm.isDirty
    assert not edit_vm.canSave


# --------------------------------------------------------------------- albums
#
# A track can be on its original release and on three compilations, and only one
# of those is the record it belongs to. The editor stages all of them as ordered
# `{id, name}` refs; `album_position == 0` is what "belongs to" means, and the
# artwork follows it.


def _albums_of(db, track_id):
    return [a.name for a in db.session.get(Track, track_id).albums]


def test_setting_albums_stages_the_whole_list_and_the_primary_name(edit_vm, db):
    track = db.session.query(Track).first()
    edit_vm.load(track.id)
    original = edit_vm.album
    first = int(track.albums[0].id)

    edit_vm.setAlbums([{"id": first, "name": original},
                       {"id": 0, "name": "Late Night Tapes"}])

    assert edit_vm.isDirty
    assert edit_vm.albumIds == [first, 0]
    assert edit_vm.primaryAlbumName == original
    # The string stays the primary's name so a GUI showing both agrees with it.
    assert edit_vm.album == original

    edit_vm.revert()
    assert edit_vm.albumIds == [first]
    assert not edit_vm.isDirty


def test_saving_adds_an_album_without_disturbing_the_primary(edit_vm, db):
    track = db.session.query(Track).first()
    edit_vm.load(track.id)
    home = edit_vm.album
    first = int(track.albums[0].id)

    edit_vm.setAlbums([{"id": first, "name": home},
                       {"id": 0, "name": "Late Night Tapes"}])
    edit_vm.save()

    assert _albums_of(db, track.id) == [home, "Late Night Tapes"]
    assert db.session.get(Track, track.id).album == home


def test_a_ref_with_no_id_creates_the_album(edit_vm, db):
    """A name typed into the chips that matched nothing is still a real answer."""
    from app.models.track import Album

    track = db.session.query(Track).first()
    edit_vm.load(track.id)
    assert db.session.query(Album).filter(Album.name == "Late Night Tapes").first() is None

    edit_vm.setAlbums([{"id": 0, "name": "Late Night Tapes"}])
    edit_vm.save()

    made = db.session.query(Album).filter(Album.name == "Late Night Tapes").first()
    assert made is not None
    assert _albums_of(db, track.id) == ["Late Night Tapes"]


def test_the_refs_replace_the_list_where_the_string_only_adds_to_it(edit_vm, db):
    """The difference between a field showing one name and one showing them all."""
    track = db.session.query(Track).first()
    edit_vm.load(track.id)
    home = edit_vm.album
    first = int(track.albums[0].id)

    edit_vm.setAlbums([{"id": first, "name": home}, {"id": 0, "name": "Compilation"}])
    edit_vm.save()
    assert len(_albums_of(db, track.id)) == 2

    # Assigning the string promotes a name and keeps the rest — a tag cannot say
    # anything about the memberships it does not mention.
    edit_vm.album = "Compilation"
    edit_vm.save()
    assert _albums_of(db, track.id) == ["Compilation", home]

    # The refs are the whole list, so what is missing from them was taken off.
    kept = int(db.session.get(Track, track.id).albums[0].id)
    edit_vm.setAlbums([{"id": kept, "name": "Compilation"}])
    edit_vm.save()
    assert _albums_of(db, track.id) == ["Compilation"]


def test_reordering_writes_album_position_and_the_files_album_tag(edit_vm, db):
    """`album_position` is the only place "belongs to" is recorded."""
    from app.repositories.track_repository import track_albums_association

    track = db.session.query(Track).first()
    edit_vm.load(track.id)
    home = edit_vm.album
    first = int(track.albums[0].id)

    edit_vm.setAlbums([{"id": first, "name": home}, {"id": 0, "name": "Compilation"}])
    edit_vm.save()

    ids = list(edit_vm.albumIds)
    edit_vm.setAlbums([{"id": ids[1], "name": "Compilation"},
                       {"id": ids[0], "name": home}])
    edit_vm.save()

    assert db.session.get(Track, track.id).album == "Compilation"
    positions = dict(db.session.execute(
        track_albums_association.select().with_only_columns(
            track_albums_association.c.album_id,
            track_albums_association.c.album_position,
        ).where(track_albums_association.c.track_id == track.id)
    ).all())
    assert positions[ids[1]] == 0
    assert positions[ids[0]] == 1


def test_clearing_the_chips_takes_the_track_off_every_album(edit_vm, db):
    track = db.session.query(Track).first()
    edit_vm.load(track.id)

    edit_vm.setAlbums([])
    edit_vm.save()

    assert _albums_of(db, track.id) == []
    assert db.session.get(Track, track.id).album == ""


# ------------------------------------------------------- artwork follows the primary


def test_artwork_will_not_change_for_an_album_that_does_not_exist_yet(edit_vm, db):
    """Nothing to adopt: a brand-new album has no cover of its own."""
    track = db.session.query(Track).first()
    edit_vm.load(track.id)
    home = edit_vm.album
    first = int(track.albums[0].id)

    edit_vm.setAlbums([{"id": 0, "name": "Late Night Tapes"},
                       {"id": first, "name": home}])

    assert edit_vm.primaryAlbumName == "Late Night Tapes"
    assert edit_vm.artworkWillChange is False


def test_artwork_will_not_change_when_the_new_primary_has_no_cover(edit_vm, db):
    from app.models.track import Album

    track = db.session.query(Track).first()
    bare = Album(name="No Sleeve")
    db.session.add(bare)
    db.session.flush()
    track.albums = list(track.albums) + [bare]
    db.session.commit()

    edit_vm.load(track.id)
    ids = list(edit_vm.albumIds)
    edit_vm.setAlbums([{"id": ids[1], "name": "No Sleeve"},
                       {"id": ids[0], "name": edit_vm.album}])

    assert edit_vm.artworkWillChange is False


def test_promoting_an_album_moves_the_tracks_artwork(qt_app, cover_service, db, covers):
    """The rule the whole artwork flow turns on, from the editor's end.

    Deliberately arranged so that **no track carries the promoted album's
    cover**, which is the ordinary case rather than the edge: an album takes its
    sleeve from the release it was identified as, or from a track that has since been retagged, and
    then the cache is the only place that picture exists. The keeper-file source
    is absent here, so this exercises the fallback — and it is the fallback that
    was silently dead while `CoverService` kept its store as `_store`.
    """
    from app.artwork import LARGE_SIZE, THUMB_SIZE
    from app.models.track import Album
    from app.viewmodels.track_edit_view_model import TrackEditViewModel

    track = db.session.query(Track).first()
    displaced = track.cover_hash
    other = Album(name="Compilation", cover_hash="d" * 32)
    db.session.add(other)
    db.session.flush()
    track.albums = list(track.albums) + [other]
    db.session.commit()
    # The cover exists only in the cache — an album identified against a
    # catalogue, whose artwork no file was ever tagged with.
    covers.put("d" * 32, LARGE_SIZE, _PNG_BYTES)
    covers.put(displaced, THUMB_SIZE, b"the old thumbnail")

    # The real service, not a stand-in. A stub exposing `store` was what made an
    # earlier version of this test pass while the feature was broken in the app:
    # `CoverService` kept its store as `_store`, so the `getattr(…, "store")` the
    # view model does answered None and the cache fallback never ran.
    from app.artwork.cover_service import CoverService

    vm = TrackEditViewModel(cover_service, cover_service=CoverService(covers))
    vm.load(track.id)
    ids = list(vm.albumIds)
    home = vm.album

    vm.setAlbums([{"id": ids[1], "name": "Compilation"}, {"id": ids[0], "name": home}])
    assert vm.artworkWillChange is True

    vm.save()
    assert db.session.get(Track, track.id).cover_hash == "d" * 32
    # The cover it displaced goes with it — nothing else in the library was
    # using it, and the same sweep that runs after any other replacement runs
    # after this one.
    assert covers.get(displaced, THUMB_SIZE) is None
    # And it is a no-op the second time: the track already carries it.
    assert vm.artworkWillChange is False


def test_a_cover_that_cannot_be_read_back_refuses_the_whole_save(qt_app, service, db):
    """A save that kept the old cover would leave exactly the mess this prevents."""
    from app.models.track import Album
    from app.viewmodels.track_edit_view_model import TrackEditViewModel

    track = db.session.query(Track).first()
    other = Album(name="Compilation", cover_hash="d" * 32)
    db.session.add(other)
    db.session.flush()
    track.albums = list(track.albums) + [other]
    db.session.commit()

    vm = TrackEditViewModel(service)          # no cover service, nothing cached
    vm.load(track.id)
    ids = list(vm.albumIds)
    errors: list[str] = []
    vm.error_occurred.connect(errors.append)

    vm.setAlbums([{"id": ids[1], "name": "Compilation"},
                  {"id": ids[0], "name": vm.album}])
    vm.save()

    assert errors and "read back" in errors[0]
    assert _albums_of(db, track.id)[0] != "Compilation", "nothing was written"
    assert vm.isDirty, "the staged chips survive a refusal"


# A one-pixel PNG. `build_picture_block_from_bytes` only base64s what it is
# given, so the bytes need to be a plausible image and nothing more.
_PNG_BYTES = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01"
    b"\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00\x00\x00IEND\xaeB`\x82"
)
