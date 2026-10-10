"""Artist portraits share the cover cache, and must survive its sweeps.

An artist's portrait is an ordinary content-addressed row in ``covers.db``,
keyed exactly as an album cover is. That buys the whole existing pipeline for
free — the same store, the same tiers, the same ``image://cover/`` provider,
the same backup — at the cost of one thing that has to be got right: the sweep
that reclaims unreferenced artwork counts references, and it has to count *every*
table that holds a hash.

It used to count only ``tracks``. So the first track edit after setting an
artist's portrait deleted the portrait, and it would have looked like the
picture failing to load rather than like a garbage collector eating it. The
playlist case is the same bug and was already live before artists existed.
"""
from __future__ import annotations

import pytest

from app.events.event_bus import EventBus
from app.models.playlist import Playlist
from app.models.track import Artist, Track
from app.repositories.artist_repository import ArtistRepository
from app.repositories.genre_repository import GenreRepository
from app.repositories.track_repository import TrackRepository
from app.services.artist_service import ArtistService
from app.services.track_service import TrackService


class FakeCoverStore:
    """A CoverStore that records what a sweep would have deleted."""

    def __init__(self, hashes: set[str]) -> None:
        self.hashes = set(hashes)
        self.swept: list[set[str]] = []

    def vacuum_orphans(self, valid_hashes: set[str]) -> int:
        self.swept.append(set(valid_hashes))
        dead = self.hashes - valid_hashes
        self.hashes -= dead
        return len(dead)


ALBUM_ART = "a" * 32
PORTRAIT = "b" * 32
PLAYLIST_ART = "c" * 32
RETIRED = "d" * 32


@pytest.fixture
def library(tmp_path):
    from app.database.connection import DatabaseManager

    manager = DatabaseManager.from_path(tmp_path / "tracks.db")
    session = manager.create_session()

    artist = Artist(name="Skrillex", image_hash=PORTRAIT)
    session.add(artist)
    session.flush()
    session.add_all([
        Track(title="Bangarang", duration_seconds=215,
              cover_hash=ALBUM_ART, artists=[artist]),
        # The track whose cover is about to be replaced.
        Track(title="Kyoto", duration_seconds=190, cover_hash=RETIRED,
              artists=[artist]),
    ])
    session.add(Playlist(name="Mix", cover_hash=PLAYLIST_ART))
    session.commit()

    store = FakeCoverStore({ALBUM_ART, PORTRAIT, PLAYLIST_ART, RETIRED})
    bus = EventBus()
    artist_repo = ArtistRepository(session)
    track_service = TrackService(
        TrackRepository(session), artist_repo, GenreRepository(session), bus,
        cover_store=store,
    )
    artist_service = ArtistService(artist_repo, bus, track_service=track_service)
    yield track_service, artist_service, store, session
    session.close()


def test_a_track_edit_does_not_eat_an_artist_portrait(library):
    """The regression this whole file exists for."""
    track_service, _, store, session = library
    kyoto = session.query(Track).filter(Track.title == "Kyoto").one()

    track_service.update_track_metadata(kyoto.id, {"cover_hash": "e" * 32})

    assert PORTRAIT in store.hashes
    # And the cover it actually displaced did go.
    assert RETIRED not in store.hashes


def test_a_track_edit_does_not_eat_a_playlist_cover(library):
    track_service, _, store, session = library
    kyoto = session.query(Track).filter(Track.title == "Kyoto").one()

    track_service.update_track_metadata(kyoto.id, {"cover_hash": "e" * 32})

    assert PLAYLIST_ART in store.hashes


def test_the_sweep_counts_every_table_that_names_a_hash(library):
    track_service, _, store, _ = library
    valid = track_service._referenced_cover_hashes()
    assert valid == {ALBUM_ART, RETIRED, PORTRAIT, PLAYLIST_ART}


def test_replacing_a_portrait_retires_the_one_it_displaced(library):
    _, artist_service, store, session = library
    artist = session.query(Artist).one()

    artist_service.update(artist.id, image_hash="f" * 32)

    assert PORTRAIT not in store.hashes
    # Everything still referenced survived.
    assert {ALBUM_ART, PLAYLIST_ART, RETIRED} <= store.hashes


def test_a_portrait_shared_with_an_album_cover_survives_the_artist_losing_it(library):
    """Content addressing means two things can be the same picture.

    An artist whose portrait happens to be one of their album covers shares the
    row. Dropping it from the artist must not delete it out from under the track.
    """
    _, artist_service, store, session = library
    artist = session.query(Artist).one()
    artist_service.update(artist.id, image_hash=ALBUM_ART)
    assert ALBUM_ART in store.hashes

    artist_service.update(artist.id, image_hash="f" * 32)

    assert ALBUM_ART in store.hashes


def test_deleting_an_orphaned_artist_reclaims_its_portrait(library):
    _, artist_service, store, session = library
    lonely = Artist(name="Nobody", image_hash="9" * 32)
    session.add(lonely)
    session.commit()
    store.hashes.add("9" * 32)

    assert artist_service.sweep_orphans() == 1
    assert "9" * 32 not in store.hashes
    assert PORTRAIT in store.hashes
