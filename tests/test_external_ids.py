"""Where external ids are kept — and the rules every caller inherits.

``app.plugins.external_ids`` keeps ``(owner, provider, kind, value)`` rows in one
table per entity and understands none of them: a plugin declares the kinds, and
the core stores whatever it is handed under ``"provider:kind"``. These pin that
store and the rules on top of it: a provider never clears an id, a unique id
belongs to one row, ingestion fills rather than overwrites, and an id goes only
with the row that holds it.
"""
from __future__ import annotations

import pytest
from sqlalchemy import delete, select

from app.models.track import Album, Artist, ArtistExternalId, Track, TrackExternalId
from app.plugins import external_ids as ext
from tests.fakes.providers import (
    CHANNEL, FAKE_MANIFEST, INSTRUMENTAL, RELEASE, VIDEO, VIDEO_TAG, FakeRegistry,
    held_id, id_rows,
)


@pytest.fixture
def session(db):
    return db.session


def _service(session, **kwargs):
    from app.events.event_bus import EventBus
    from app.repositories.artist_repository import ArtistRepository
    from app.repositories.genre_repository import GenreRepository
    from app.repositories.track_repository import TrackRepository
    from app.services.track_service import TrackService

    return TrackService(
        TrackRepository(session), ArtistRepository(session), GenreRepository(session),
        EventBus(), **kwargs,
    )


# -- keys --------------------------------------------------------------------------


def test_keys_split_and_join():
    assert ext.make_key("fake", "video") == VIDEO
    assert ext.split_key(VIDEO) == ("fake", "video")
    assert ext.split_key("nonsense") == ("", "")


@pytest.mark.parametrize("key, valid", [
    ("fake:video", True),
    ("acme.music:recording-2", True),
    ("Fake:video", False),
    ("fake:", False),
    ("fake:vid:eo", False),
    ("fake video", False),
])
def test_a_key_is_spelled_as_a_manifest_allows(key, valid):
    assert ext.valid_key(key) is valid


# -- reading -----------------------------------------------------------------------


def test_read_returns_every_id_the_row_holds(session):
    track = session.query(Track).first()
    track.external_id_rows.append(TrackExternalId(provider="acme", kind="recording", value="r1"))

    assert ext.read("track", track) == {VIDEO: "v00000000", "acme:recording": "r1"}


def test_read_many_answers_a_page_in_one_query(session, queries):
    queries.reset()
    held = ext.read_many(session, "track", [1, 2, 3, 99999])

    assert held == {1: {VIDEO: "v00000000"}, 2: {VIDEO: "v00000001"}, 3: {VIDEO: "v00000002"}}
    assert len(queries) == 1


def test_an_outer_join_keeps_a_page_to_one_select(session, queries):
    track = session.get(Track, 2)
    track.external_id_rows.append(TrackExternalId(provider="acme", kind="recording", value="r2"))
    session.commit()
    queries.reset()

    stmt = ext.outer_join(
        select(Track.id, *ext.joined_columns("track")), "track"
    ).where(Track.id.in_([1, 2]))
    folded: dict = {}
    for row in session.execute(stmt):
        pair = ext.joined_pair(row)
        folded.setdefault(row[0], {}).update([pair] if pair else [])

    assert folded == {1: {VIDEO: "v00000000"}, 2: {VIDEO: "v00000001", "acme:recording": "r2"}}
    assert len(queries) == 1


def test_identified_means_any_id_whatever_is_enabled(session):
    """The cover rule reads the row, not the plugins — see Album."""
    album = session.query(Album).first()
    assert not ext.is_identified("album", album)

    album.external_id_rows = id_rows("album", {"gone:release": "x"})
    assert ext.is_identified("album", album)
    assert ext.is_identified("album", {"gone:release": "x"})
    assert not ext.is_identified("album", {"gone:release": ""})


def test_identified_clause_can_ask_for_the_kinds_one_provider_reads(session):
    first, second = session.query(Track).order_by(Track.id).limit(2).all()
    first.external_id_rows = id_rows("track", {INSTRUMENTAL: "inst0000001"})
    session.commit()

    def matching(keys):
        stmt = select(Track.id).where(
            Track.id.in_([first.id, second.id]), ext.identified_clause("track", keys)
        )
        return sorted(session.execute(stmt).scalars())

    assert matching(None) == [first.id, second.id]
    assert matching({VIDEO}) == [second.id], "an instrumental's id is no handle on the track"
    assert matching(set()) == []


# -- writing: the rules ------------------------------------------------------------


def test_a_provider_cannot_clear_an_id(session):
    track = session.query(Track).first()

    assert ext.write(session, "track", track, VIDEO, "") is False
    assert ext.write(session, "track", track, VIDEO, None) is False
    assert ext.write(session, "track", track, VIDEO, "   ") is False
    assert held_id(track, VIDEO) == "v00000000"


def test_clearing_is_a_separate_deliberate_call(session):
    track = session.query(Track).first()

    assert ext.clear("track", track, VIDEO) is True
    session.commit()
    assert held_id(track, VIDEO) is None
    assert ext.read_many(session, "track", [track.id]) == {}, "the row is gone, not blanked"
    assert ext.clear("track", track, VIDEO) is False, "nothing left to clear"


def test_fill_only_never_overwrites(session):
    track = session.query(Track).first()

    assert ext.write(session, "track", track, VIDEO, "newvalue123", fill_only=True) is False
    assert held_id(track, VIDEO) == "v00000000"


def test_fill_only_fills_a_gap(session):
    artist = session.query(Artist).first()

    assert ext.write(session, "artist", artist, CHANNEL, "UCfilled00001", fill_only=True)
    session.commit()
    assert ext.read_many(session, "artist", [artist.id]) == {artist.id: {CHANNEL: "UCfilled00001"}}


def test_a_unique_id_another_row_holds_is_refused(session):
    first, second = session.query(Track).order_by(Track.id).limit(2).all()

    assert ext.write(session, "track", second, VIDEO, "v00000000") is False
    assert ext.conflict(session, "track", VIDEO, "v00000000", exclude_id=second.id) is first
    assert ext.conflict(session, "track", VIDEO, "v00000000", exclude_id=first.id) is None


def test_a_kind_that_is_not_unique_may_repeat(session):
    """Two tracks can have instrumentals cut from the same upload."""
    first, second = session.query(Track).order_by(Track.id).limit(2).all()
    assert ext.write(session, "track", first, INSTRUMENTAL, "shared00001", unique=False)

    assert ext.write(session, "track", second, INSTRUMENTAL, "shared00001", unique=False)
    session.commit()
    assert held_id(second, INSTRUMENTAL) == "shared00001"


def test_any_well_formed_kind_is_kept(session):
    """The core has no list of catalogues. Which kinds reach it is the providers' gate."""
    track = session.query(Track).first()
    assert ext.write(session, "track", track, "acme:recording", "r1") is True
    session.commit()
    assert ext.read_many(session, "track", [track.id])[track.id]["acme:recording"] == "r1"


@pytest.mark.parametrize("key, value", [
    ("not a key", "x"),
    ("fake:video:extra", "x"),
    (VIDEO, "x" * (ext.MAX_VALUE_LENGTH + 1)),
])
def test_a_malformed_key_or_value_writes_nothing(session, key, value):
    track = session.query(Track).first()
    assert ext.write(session, "track", track, key, value) is False
    assert ext.read("track", track) == {VIDEO: "v00000000"}


# -- an id goes with its row and only with its row ---------------------------------


def test_deleting_a_track_takes_its_ids(session):
    track = session.query(Track).first()
    track_id = track.id
    session.delete(track)
    session.commit()

    assert session.query(TrackExternalId).filter_by(owner_id=track_id).count() == 0


def test_a_bulk_delete_takes_its_ids_at_the_database(session):
    """The orphan sweeps delete in SQL, past the ORM — the cascade is the schema's."""
    artist = Artist(name="Orphan", external_id_rows=id_rows("artist", {CHANNEL: "UCgoing000001"}))
    session.add(artist)
    session.commit()
    artist_id = artist.id

    session.execute(delete(Artist).where(Artist.id == artist_id))
    session.commit()

    assert session.query(ArtistExternalId).filter_by(owner_id=artist_id).count() == 0


# -- lookup ------------------------------------------------------------------------


def test_lookup_values_puts_the_rows_own_id_first():
    row = {"external_ids": {VIDEO: "own"}, "alternate_ids": {VIDEO: ["alt1", "own", "alt2"]}}

    assert ext.lookup_values(row) == [(VIDEO, "own"), (VIDEO, "alt1"), (VIDEO, "alt2")]


def test_lookup_answers_every_value_in_one_pass(session, queries):
    ids = [f"v{n:08d}" for n in (0, 5, 9)]
    queries.reset()

    found = ext.lookup(session, "track", [(VIDEO, v) for v in ids] + [(VIDEO, "absent")])

    assert {value for (_k, value) in found} == set(ids)
    assert len(queries) == 1, "one query per kind, however many values"


def test_lookup_rows_is_positional(session):
    rows = [
        {"external_ids": {VIDEO: "absent"}},
        {"external_ids": {VIDEO: "absent"}, "alternate_ids": {VIDEO: ["v00000003"]}},
        {"external_ids": {}},
    ]

    assert ext.lookup_rows(session, rows) == [None, 4, None]


def test_held_reports_which_kinds_have_values(session):
    held = set(ext.held(session))

    assert ("track", VIDEO) in held
    assert ("artist", CHANNEL) not in held


# -- tags --------------------------------------------------------------------------


def test_only_declared_tags_reach_a_file():
    tags = {VIDEO: VIDEO_TAG}
    assert ext.tags_for({VIDEO: "abc", INSTRUMENTAL: "def"}, tags) == {VIDEO_TAG: "abc"}
    assert ext.tags_for({VIDEO: "abc"}, {}) == {}


# -- through the services ----------------------------------------------------------


def test_a_track_is_created_with_its_ids(db):
    session = db.session
    service = _service(session)
    track_id = service.create_track_with_relationships({
        "title": "New", "duration_seconds": 100, "artists": "",
        "external_ids": {VIDEO: "freshvideo1"},
        "artist_refs": [{"name": "Fresh Artist", "external_ids": {CHANNEL: "UCfresh000001"}}],
    })

    track = service.get_track_by_id(track_id)
    assert held_id(track, VIDEO) == "freshvideo1"
    assert held_id(track.artists[0], CHANNEL) == "UCfresh000001"


def test_ingestion_never_takes_an_id_another_artist_holds(db):
    session = db.session
    holder = session.query(Artist).first()
    holder.external_id_rows = id_rows("artist", {CHANNEL: "UCtaken000001"})
    session.commit()
    service = _service(session)

    track_id = service.create_track_with_relationships({
        "title": "New", "duration_seconds": 100,
        "artist_refs": [{"name": "Somebody Else", "external_ids": {CHANNEL: "UCtaken000001"}}],
    })

    newcomer = service.get_track_by_id(track_id).artists[0]
    assert held_id(newcomer, CHANNEL) is None
    assert held_id(holder, CHANNEL) == "UCtaken000001"


def test_an_edit_can_clear_and_set(db):
    session = db.session
    service = _service(session)
    track = session.query(Track).first()

    service.update_track_metadata(track.id, {"external_ids": {VIDEO: ""}})
    assert held_id(track, VIDEO) is None

    service.update_track_metadata(track.id, {"external_ids": {VIDEO: "edited00001"}})
    assert held_id(track, VIDEO) == "edited00001"


def test_what_a_kind_means_comes_from_its_declaration(db):
    """Unique or not, and which tag it travels in: the plugin says, the service asks."""
    session = db.session
    first, second = session.query(Track).order_by(Track.id).limit(2).all()
    first.external_id_rows.extend(id_rows("track", {INSTRUMENTAL: "shared00001"}))
    session.commit()

    plain = _service(session)
    assert plain.id_unique(INSTRUMENTAL), "with nothing declared, every kind is unique"
    assert plain.id_tags() == {}

    declared = _service(session, id_kinds=FakeRegistry())
    assert not declared.id_unique(INSTRUMENTAL)
    assert declared.id_unique(VIDEO)
    assert declared.id_tags() == {VIDEO: VIDEO_TAG}

    declared.update_track_metadata(second.id, {"external_ids": {INSTRUMENTAL: "shared00001"}})
    assert held_id(second, INSTRUMENTAL) == "shared00001"


def test_an_identified_album_keeps_its_sleeve(session):
    """The release's own cover outranks whatever a joining track carries."""
    album = session.query(Album).first()
    album.external_id_rows = id_rows("album", {RELEASE: "MPREb_identified"})
    album.cover_hash = None

    assert ext.is_identified("album", album)


# -- the providers' gate -----------------------------------------------------------


def test_a_plugin_row_keeps_only_the_kinds_its_plugin_declares():
    from app.plugins.providers import row_from_protocol

    proto = {
        "title": "Song",
        "externalIds": [
            {"provider": "fake", "kind": "video", "value": "v1"},
            {"provider": "other", "kind": "video", "value": "smuggled"},
        ],
        "artistRefs": [{"name": "A", "externalIds": [
            {"provider": "fake", "kind": "channel", "value": "UC1"},
            {"provider": "fake", "kind": "video", "value": "wrong-entity"},
        ]}],
    }

    row = row_from_protocol(proto, "test.fake", FAKE_MANIFEST)

    assert row["external_ids"] == {VIDEO: "v1"}
    assert row["artist_refs"][0]["external_ids"] == {CHANNEL: "UC1"}
