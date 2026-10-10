"""The artist editor, and the identity that reaches it from a search result.

Two halves. The view model keeps ``editVM``'s bargain — staged edits, exact
dirtiness, a refusal that writes nothing — and the import path carries artist
ids through so that most artists are identified without anyone typing one.

What a pasted link means is the declaring plugin's parse rules; the real
plugin's are tested in its own repository, and these use the fake plugin's.
"""
from __future__ import annotations

import pytest

from app.events.event_bus import EventBus
from app.models.track import Artist, Track
from app.repositories.artist_repository import ArtistRepository
from app.repositories.genre_repository import GenreRepository
from app.repositories.track_repository import TrackRepository
from app.services.artist_service import ArtistService
from app.services.track_service import TrackService
from app.viewmodels.artist_edit_view_model import ArtistEditViewModel
from app.viewmodels.artists_view_model import ArtistsViewModel
from tests.fakes.providers import CHANNEL, FakeIdentity, FakeMetadata, FakeRegistry, held_id


@pytest.fixture
def editor(tmp_path, qt_app):
    from app.database.connection import DatabaseManager

    manager = DatabaseManager.from_path(tmp_path / "tracks.db")
    session = manager.create_session()

    skrillex = Artist(name="Skrillex")
    compound = Artist(name="person1/person2")
    session.add_all([skrillex, compound])
    session.flush()
    session.add_all([
        Track(title="Bangarang", duration_seconds=215, artists=[skrillex]),
        Track(title="Promises", duration_seconds=240, artists=[compound]),
        Track(title="Guilt", duration_seconds=200, artists=[compound]),
    ])
    session.commit()

    bus = EventBus()
    artist_repo = ArtistRepository(session)
    track_service = TrackService(
        TrackRepository(session), artist_repo, GenreRepository(session), bus
    )
    service = ArtistService(artist_repo, bus, track_service=track_service)
    vm = ArtistEditViewModel(
        service, registry=FakeRegistry(metadata=FakeMetadata(), identity=FakeIdentity()),
    )
    yield vm, service, artist_repo, session, {"skrillex": skrillex, "compound": compound}
    session.close()


def _errors(vm):
    seen: list[str] = []
    vm.error_occurred.connect(seen.append)
    return seen


def _staged(vm):
    return next(f["value"] for f in vm.externalIdFields if f["key"] == CHANNEL)


# ---------------------------------------------------------------------------
# Staged editing
# ---------------------------------------------------------------------------


def test_load_and_dirtiness(editor):
    vm, _, _, _, rows = editor
    vm.load(rows["skrillex"].id)

    assert vm.hasArtist and vm.name == "Skrillex"
    assert vm.trackCount == 1
    assert not vm.isDirty and not vm.canSave

    vm.name = "Skrillex "
    assert vm.isDirty and vm.canSave


def test_revert_costs_nothing(editor):
    vm, _, _, _, rows = editor
    vm.load(rows["skrillex"].id)
    vm.name = "Something else"
    vm.setExternalId(CHANNEL, "UCabcdefghij")

    vm.revert()

    assert vm.name == "Skrillex"
    assert _staged(vm) == ""
    assert not vm.isDirty


def test_a_blank_name_cannot_be_saved(editor):
    vm, _, _, _, rows = editor
    vm.load(rows["skrillex"].id)
    vm.name = "   "
    assert not vm.canSave


def test_unparseable_id_text_is_refused_not_discarded(editor):
    vm, _, repo, _, rows = editor
    vm.load(rows["skrillex"].id)
    errors = _errors(vm)

    vm.setExternalId(CHANNEL, "definitely not a channel")
    vm.save()

    assert errors and "channel id" in errors[-1]
    # Nothing written, and the staged text survives so it can be corrected.
    assert held_id(repo.get_by_id(rows["skrillex"].id), CHANNEL) is None
    assert _staged(vm) == "definitely not a channel"


def test_a_name_collision_writes_nothing_at_all(editor):
    """A refused save must not apply the half of it that was legal."""
    vm, _, repo, _, rows = editor
    vm.load(rows["skrillex"].id)
    errors = _errors(vm)

    vm.name = "person1/person2"
    vm.setExternalId(CHANNEL, "UCabcdefghij")
    vm.save()

    assert errors
    artist = repo.get_by_id(rows["skrillex"].id)
    assert artist.name == "Skrillex"
    assert held_id(artist, CHANNEL) is None


def test_saving_a_new_channel_id_offers_the_overwrite(editor):
    vm, _, _, _, rows = editor
    vm.load(rows["skrillex"].id)
    offered: list[str] = []
    vm.overwrite_offered.connect(offered.append)

    vm.setExternalId(CHANNEL, "https://fake.example/channel/UCabcdefghij")
    vm.save()

    # The link was normalised on the way in, and the offer names the id.
    assert _staged(vm) == "UCabcdefghij"
    assert offered == ["UCabcdefghij"]


def test_a_save_that_changes_only_the_name_offers_nothing(editor):
    """Only the id can put the other fields right, so only the id offers."""
    vm, _, _, _, rows = editor
    vm.load(rows["skrillex"].id)
    offered: list[str] = []
    vm.overwrite_offered.connect(offered.append)

    vm.name = "Skrillex!"
    vm.save()

    assert offered == []


def test_the_editor_clears_when_its_artist_is_deleted(editor):
    vm, service, _, _, rows = editor
    vm.load(rows["compound"].id)
    assert vm.hasArtist

    service.split(rows["compound"].id, ["person1", "person2"])

    assert not vm.hasArtist


# ---------------------------------------------------------------------------
# Splitting from the editor
# ---------------------------------------------------------------------------


def test_proposed_split_reads_the_staged_name(editor):
    vm, _, _, _, rows = editor
    vm.load(rows["compound"].id)
    assert vm.proposedSplit() == ["person1", "person2"]

    # Typing a different name changes what would be proposed, before any save.
    vm.name = "A feat. B"
    assert vm.proposedSplit() == ["A", "B"]


def test_split_moves_the_tracks_and_reports_how_many_artists(editor):
    vm, _, repo, _, rows = editor
    # Held as an int, because the object it came from is about to be deleted and
    # a bulk delete expires the session — reading `.id` off it afterwards raises
    # rather than answering. Every caller inside the app already works in ids
    # for the same reason.
    compound_id = rows["compound"].id
    vm.load(compound_id)
    completed: list[int] = []
    vm.split_completed.connect(completed.append)

    vm.splitInto(["person1", "person2"])

    assert completed == [2]
    assert repo.track_count(repo.get_by_name("person1").id) == 2
    assert repo.get_by_id(compound_id) is None


def test_track_ids_answers_for_play_all(editor):
    vm, _, _, _, rows = editor
    vm.load(rows["compound"].id)
    assert len(vm.trackIds()) == 2

    vm.clear()
    assert vm.trackIds() == []


# ---------------------------------------------------------------------------
# The list model
# ---------------------------------------------------------------------------


def test_artists_view_model_flags_the_names_worth_looking_at(editor):
    _, service, _, _, _ = editor
    model = ArtistsViewModel(service, service.event_bus)

    assert model.count == 2
    assert model.needsAttentionCount == 1


def test_suggest_excludes_nothing_and_is_case_insensitive(editor):
    _, service, _, _, rows = editor
    model = ArtistsViewModel(service, service.event_bus)

    found = model.suggest("SKRIL")
    assert [f["name"] for f in found] == ["Skrillex"]
    assert found[0]["trackCount"] == 1


def test_id_for_name_is_the_duplicate_guard(editor):
    """This is what stops "daft punk" becoming a second "Daft Punk"."""
    _, service, _, _, rows = editor
    model = ArtistsViewModel(service, service.event_bus)

    assert model.idForName("skrillex") == rows["skrillex"].id
    assert model.idForName("Nobody At All") == 0


def test_the_list_refreshes_when_an_artist_changes(editor):
    _, service, _, _, rows = editor
    model = ArtistsViewModel(service, service.event_bus)
    assert model.needsAttentionCount == 1

    service.split(rows["compound"].id, ["person1", "person2"])

    assert model.needsAttentionCount == 0
    assert model.count == 3


# ---------------------------------------------------------------------------
# Identity arriving from a search result
# ---------------------------------------------------------------------------


def test_artist_refs_identify_artists_at_import_time(editor):
    """A downloaded track arrives knowing which channel each artist is.

    Without this every portrait would have to be set by hand, and the
    comma-joined string would be the only thing the library ever saw.
    """
    _, service, repo, _, _ = editor
    track_service = service.track_service

    track_service.create_track_with_relationships({
        "title": "Where Are Ü Now",
        "duration_seconds": 250,
        "artists": "Jack Ü, Justin Bieber",
        "artist_refs": [
            {"name": "Jack Ü", "external_ids": {CHANNEL: "UCjacku000001"}},
            {"name": "Justin Bieber", "external_ids": {CHANNEL: "UCbieber00001"}},
        ],
    })

    assert held_id(repo.get_by_name("Jack Ü"), CHANNEL) == "UCjacku000001"
    assert held_id(repo.get_by_name("Justin Bieber"), CHANNEL) == "UCbieber00001"


def test_artist_refs_survive_a_name_containing_a_comma(editor):
    """The structured form has no separator to guess at.

    The string path would split this into two artists; the refs say plainly that
    it is one.
    """
    _, service, repo, _, _ = editor
    track_service = service.track_service

    track_service.create_track_with_relationships({
        "title": "A Song",
        "duration_seconds": 100,
        "artists": "Earth, Wind & Fire",
        "artist_refs": [{"name": "Earth, Wind & Fire", "external_ids": {CHANNEL: "UCewf00000001"}}],
    })

    assert repo.get_by_name("Earth, Wind & Fire") is not None
    assert repo.get_by_name("Earth") is None


def test_the_comma_string_is_still_used_when_there_are_no_refs(editor):
    _, service, repo, _, _ = editor
    track_service = service.track_service

    track_service.create_track_with_relationships({
        "title": "Another Song",
        "duration_seconds": 100,
        "artists": "Alpha, Beta",
    })

    assert repo.get_by_name("Alpha") is not None
    assert repo.get_by_name("Beta") is not None


def test_refs_never_overwrite_an_id_already_recorded(editor):
    _, service, repo, _, rows = editor
    service.update(rows["skrillex"].id, ids={CHANNEL: "UCoriginal001"})
    track_service = service.track_service

    track_service.create_track_with_relationships({
        "title": "Yet Another",
        "duration_seconds": 100,
        "artists": "Skrillex",
        "artist_refs": [{"name": "Skrillex", "external_ids": {CHANNEL: "UCimposter001"}}],
    })

    assert held_id(repo.get_by_id(rows["skrillex"].id), CHANNEL) == "UCoriginal001"
