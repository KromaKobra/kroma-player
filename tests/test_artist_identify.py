"""Identifying an artist from a track of theirs, and refusing to guess.

The library holds an external id for every track a plugin identified, and the
catalogue behind it can say who is credited on that recording. That is a far
better handle on "which Skrillex" than a name search, which can only offer
strangers and ask the user to recognise one. *How* a catalogue answers is the
plugin's — its own tests pin the cases where the right answer is nothing — and
what is pinned here is the core's half: which tracks it offers, what the worker
does with the answer, and that an id written is never written wrongly over
another.

Nothing in the application ever overwrites an artist's id once it is set by a
provider — not ``resolve_external_id``, not ``_resolve_artists``, not a later
import. So an id written wrongly is wrong permanently, and every portrait
fetched from it afterwards is wrong too. Most of the tests below are about the
cases where the right answer is to write *nothing*.
"""
from __future__ import annotations

import pytest

from app.events.event_bus import EventBus
from app.importer.import_workers import IdentifyWorker
from app.models.track import Artist, Track
from app.repositories.artist_repository import ArtistRepository
from app.repositories.genre_repository import GenreRepository
from app.repositories.track_repository import TrackRepository
from app.services.artist_service import ArtistService
from app.services.track_service import TrackService
from app.viewmodels.artist_edit_view_model import ArtistEditViewModel
from tests.fakes.providers import (
    CHANNEL,
    FAKE_MANIFEST,
    INSTRUMENTAL,
    VIDEO,
    FakeArtwork,
    FakeIdentity,
    FakeMetadata,
    FakeRegistry,
    held_id,
    id_rows,
)

SEVEN_LIONS = "UCfakeChannel0000000001A"


class FakeCoverService:
    def ingest_source(self, data):
        return ("p" * 32, b"master")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    from app.importer import import_workers

    monkeypatch.setattr(import_workers, "fetch_first", lambda urls, **kw: b"img" if urls else None)


# ---------------------------------------------------------------------------
# The worker
# ---------------------------------------------------------------------------


def _worker(identity, metadata=None, artwork=None, sources=None, covers=FakeCoverService()):
    registry = FakeRegistry(identity=identity, metadata=metadata, artwork=artwork)
    return IdentifyWorker(
        "artist", "Seven Lions",
        sources or [{"title": "Foolish Of Me", "external_ids": {VIDEO: "Hn4Rt8Vw1Ys"}}],
        registry, covers,
    )


def _run(worker):
    seen: dict = {"identified": [], "failed": []}
    worker.identified.connect(lambda *args: seen["identified"].append(args))
    worker.failed.connect(seen["failed"].append)
    worker.run()
    return seen


def test_the_provider_is_handed_the_name_and_the_tracks():
    identity = FakeIdentity(entities={"Seven Lions": {CHANNEL: SEVEN_LIONS}})

    _run(_worker(identity))

    assert identity.calls == [(
        "artist", "Seven Lions",
        [{"title": "Foolish Of Me", "external_ids": {VIDEO: "Hn4Rt8Vw1Ys"}}],
    )]


def test_an_answer_comes_back_with_the_name_and_the_portrait():
    identity = FakeIdentity(entities={"Seven Lions": {CHANNEL: SEVEN_LIONS}})
    metadata = FakeMetadata(answers={SEVEN_LIONS: {
        "name": "Seven Lions (Official)", "artwork_urls": ["https://img/p.jpg"],
    }})

    seen = _run(_worker(identity, metadata))

    assert seen["identified"] == [({CHANNEL: SEVEN_LIONS}, "Seven Lions (Official)", "p" * 32)]


def test_no_answer_is_said_rather_than_guessed():
    seen = _run(_worker(FakeIdentity()))

    assert seen["identified"] == []
    assert seen["failed"] == ["Fake Source does not credit any of this artist's tracks to “Seven Lions”"]


def test_an_answer_of_a_kind_the_plugin_never_declared_is_dropped():
    """A track's kind given as an artist's id is not an answer.

    Dropped by the provider, where every answer from a plugin is checked
    against what that plugin declared for the entity asked about.
    """
    from app.plugins.providers import IdentityProvider

    class Host:
        def call(self, plugin_id, method, params, **kwargs):
            return {"externalIds": [
                {"provider": "fake", "kind": "video", "value": "not-an-artist"},
                {"provider": "elsewhere", "kind": "channel", "value": "UCundeclared"},
            ]}

    provider = IdentityProvider(Host(), FAKE_MANIFEST)

    assert provider.identify_entity("artist", "Seven Lions", []) == {}


def test_an_unreachable_portrait_still_identifies_the_artist():
    identity = FakeIdentity(entities={"Seven Lions": {CHANNEL: SEVEN_LIONS}})

    seen = _run(_worker(identity, FakeMetadata(), FakeArtwork()))

    assert seen["identified"] == [({CHANNEL: SEVEN_LIONS}, "", "")]


def test_the_artwork_chain_is_asked_when_the_metadata_had_no_picture():
    identity = FakeIdentity(entities={"Seven Lions": {CHANNEL: SEVEN_LIONS}})
    artwork = FakeArtwork(urls={SEVEN_LIONS: ["https://img/p.jpg"]})

    seen = _run(_worker(identity, FakeMetadata(), artwork))

    assert seen["identified"][0][2] == "p" * 32
    assert artwork.calls == [("artist", {CHANNEL: SEVEN_LIONS})]


def test_a_provider_that_raises_reports_rather_than_propagating():
    seen = _run(_worker(FakeIdentity(error=RuntimeError("network down"))))

    assert seen["failed"] == ["network down"]


def test_no_identity_provider_is_said_plainly():
    worker = IdentifyWorker("artist", "Seven Lions", [], FakeRegistry(), None)

    assert _run(worker)["failed"] == ["No enabled plugin can identify this"]


# ---------------------------------------------------------------------------
# The view model
# ---------------------------------------------------------------------------

@pytest.fixture
def editor(tmp_path, qt_app):
    from app.database.connection import DatabaseManager

    manager = DatabaseManager.from_path(tmp_path / "tracks.db")
    session = manager.create_session()

    lions = Artist(name="Seven Lions")
    local = Artist(name="A Friend")
    session.add_all([lions, local])
    session.flush()
    session.add_all([
        Track(title="Foolish Of Me", duration_seconds=187,
              external_id_rows=id_rows("track", {VIDEO: "Hn4Rt8Vw1Ys"}), artists=[lions]),
        Track(title="Rush Over", duration_seconds=200, artists=[lions]),
        Track(title="Home Demo", duration_seconds=120, artists=[local]),
    ])
    session.commit()

    bus = EventBus()
    artist_repo = ArtistRepository(session)
    track_service = TrackService(
        TrackRepository(session), artist_repo, GenreRepository(session), bus
    )
    service = ArtistService(artist_repo, bus, track_service=track_service)
    vm = ArtistEditViewModel(
        service, registry=FakeRegistry(identity=FakeIdentity(), metadata=FakeMetadata()),
    )
    yield vm, service, artist_repo, session, {"lions": lions, "local": local}
    session.close()


def _errors(vm):
    seen: list[str] = []
    vm.error_occurred.connect(seen.append)
    return seen


def test_identify_sources_offers_only_identified_tracks(editor):
    _vm, service, _repo, _session, artists = editor
    sources = service.identify_sources(artists["lions"].id)

    assert [s["external_ids"] for s in sources] == [{VIDEO: "Hn4Rt8Vw1Ys"}]
    assert sources[0]["artist"] == "Seven Lions"
    assert sources[0]["title"] == "Foolish Of Me"


def test_identify_sources_is_empty_for_a_purely_local_artist(editor):
    _vm, service, _repo, _session, artists = editor
    assert service.identify_sources(artists["local"].id) == []


def test_identifiable_tracks_prefers_the_newest_and_respects_the_limit(editor):
    """Newest first because a recent upload is likelier to still exist, and
    capped because each one costs a network round trip."""
    _vm, _service, repo, session, artists = editor
    lions = artists["lions"]
    session.add_all([
        Track(title=f"Track {i}", duration_seconds=100,
              external_id_rows=id_rows("track", {VIDEO: f"vid{i:08d}"}), artists=[lions])
        for i in range(5)
    ])
    session.commit()

    rows = repo.identifiable_tracks(lions.id, limit=3)

    assert len(rows) == 3
    assert [r["title"] for r in rows] == ["Track 4", "Track 3", "Track 2"]


def test_identifiable_tracks_skips_tracks_with_no_id(editor):
    _vm, _service, repo, _session, artists = editor
    rows = repo.identifiable_tracks(artists["lions"].id, limit=10)
    assert [r["title"] for r in rows] == ["Foolish Of Me"]


def test_identifiable_tracks_skips_an_empty_id(editor):
    """An empty string is not an id, and the query has to say so."""
    _vm, _service, repo, session, artists = editor
    session.add(Track(title="Blank", duration_seconds=90,
                      external_id_rows=id_rows("track", {VIDEO: ""}), artists=[artists["lions"]]))
    session.commit()

    rows = repo.identifiable_tracks(artists["lions"].id, limit=10)

    assert [r["title"] for r in rows] == ["Foolish Of Me"]


def test_identifiable_tracks_ignore_an_instrumentals_id(editor):
    """Where the instrumental came from says nothing about who the artist is."""
    _vm, _service, repo, session, artists = editor
    session.add(Track(title="Instrumental Only", duration_seconds=90,
                      external_id_rows=id_rows("track", {INSTRUMENTAL: "Bc3Df6Gh9Jk"}),
                      artists=[artists["lions"]]))
    session.commit()

    rows = repo.identifiable_tracks(
        artists["lions"].id, limit=10, keys=FAKE_MANIFEST.recording_keys(),
    )

    assert [r["title"] for r in rows] == ["Foolish Of Me"]


def test_the_editor_asks_only_for_tracks_its_provider_can_read(editor, monkeypatch):
    """An instrumental's id, or another catalogue's, is no handle for this one."""
    vm, service, _repo, _session, artists = editor
    asked: dict = {}
    monkeypatch.setattr(
        service, "identify_sources", lambda artist_id, **kw: asked.update(kw) or [],
    )
    vm.load(artists["lions"].id)

    vm.identifyFromTracks()

    assert asked == {"keys": FAKE_MANIFEST.recording_keys()}


def test_identify_refuses_with_a_reason_when_there_is_nothing_to_ask_about(editor):
    vm, service, repo, _session, artists = editor
    vm.load(artists["local"].id)
    errors = _errors(vm)

    vm.identifyFromTracks()

    assert "nothing to identify them from" in errors[0]
    assert held_id(service.get(artists["local"].id), CHANNEL) is None


def test_identify_refuses_an_artist_that_is_already_identified(editor):
    vm, service, _repo, _session, artists = editor
    service.update(artists["lions"].id, ids={CHANNEL: SEVEN_LIONS})
    vm.load(artists["lions"].id)
    errors = _errors(vm)

    vm.identifyFromTracks()

    assert "already identified" in errors[0]


def test_identify_is_only_offered_with_a_provider_and_without_an_id(editor):
    vm, service, _repo, _session, artists = editor
    vm.load(artists["lions"].id)
    assert vm.canIdentify is True

    vm._registry.set(identity_provider=None)
    assert vm.canIdentify is False


def test_a_successful_identify_writes_the_id_and_the_portrait(editor):
    vm, service, repo, _session, artists = editor
    vm.load(artists["lions"].id)

    vm._on_identified({CHANNEL: SEVEN_LIONS}, "Seven Lions (Official)", "p" * 32)

    artist = service.get(artists["lions"].id)
    assert held_id(artist, CHANNEL) == SEVEN_LIONS
    assert artist.image_hash == "p" * 32


def test_identify_leaves_the_name_alone(editor):
    """The match was made *by* name, so the catalogue's spelling adds nothing —
    and a rename that collided would take the id down with it."""
    vm, service, repo, _session, artists = editor
    vm.load(artists["lions"].id)

    vm._on_identified({CHANNEL: SEVEN_LIONS}, "SEVEN LIONS", "p" * 32)

    assert service.get(artists["lions"].id).name == "Seven Lions"


def test_identify_refuses_an_id_another_artist_already_holds(editor):
    vm, service, repo, _session, artists = editor
    service.update(artists["local"].id, ids={CHANNEL: SEVEN_LIONS})
    vm.load(artists["lions"].id)
    errors = _errors(vm)

    vm._on_identified({CHANNEL: SEVEN_LIONS}, "Seven Lions", "p" * 32)

    assert "already belongs to" in errors[0]
    assert held_id(service.get(artists["lions"].id), CHANNEL) is None
    assert service.get(artists["lions"].id).image_hash is None


def test_an_empty_portrait_hash_leaves_the_existing_picture_alone(editor):
    vm, service, repo, _session, artists = editor
    service.update(artists["lions"].id, image_hash="o" * 32)
    vm.load(artists["lions"].id)

    vm._on_identified({CHANNEL: SEVEN_LIONS}, "Seven Lions", "")

    artist = service.get(artists["lions"].id)
    assert held_id(artist, CHANNEL) == SEVEN_LIONS
    assert artist.image_hash == "o" * 32
