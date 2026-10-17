"""Playlist grid layout, cache ownership, stale jobs and precise UI updates."""
from __future__ import annotations

import io
import threading
import time
from types import SimpleNamespace

import pytest
from PIL import Image
from sqlalchemy import event

from app.artwork import LARGE_SIZE, THUMB_SIZE, image_ops
from app.artwork.cover_provider import CoverImageProvider
from app.artwork.cover_store import CoverStore
from app.artwork.playlist_artwork import playlist_cover_hash
from app.database.connection import DatabaseManager
from app.events.event_bus import EventBus
from app.library.row_cache import TrackRowCache
from app.models.playlist import Playlist
from app.models.track import Album, Artist, Track
from app.repositories.playlist_repository import PlaylistRepository
from app.repositories.track_repository import TrackRepository
from app.services.playlist_service import PlaylistService
from app.viewmodels.playlist_tracks_view_model import PlaylistTracksViewModel
from app.viewmodels.playlists_view_model import PlaylistRoles, PlaylistsViewModel

COLORS = [(240, 30, 30), (20, 220, 40), (30, 50, 240), (240, 220, 30), (180, 20, 200)]


def _png(color):
    output = io.BytesIO()
    Image.new("RGB", (96, 64), color).save(output, format="PNG")
    return output.getvalue()


class _Pool:
    """Hold actual jobs to make rapid edits and late completions deterministic."""
    def __init__(self):
        self.jobs = []

    def start(self, job):
        self.jobs.append(job)

    def waitForDone(self):
        assert not self.jobs


@pytest.fixture
def artwork(qt_app, tmp_path):
    manager = DatabaseManager.from_path(tmp_path / "tracks.db")
    session = manager.create_session()
    store = CoverStore(tmp_path / "covers.db")
    tracks = []
    for index, color in enumerate(COLORS):
        source = _png(color)
        cover_hash = image_ops.content_hash(source)
        store.put(cover_hash, LARGE_SIZE, source, "png")
        tracks.append(Track(title=str(index), duration_seconds=60, cover_hash=cover_hash))
    session.add_all(tracks)
    session.commit()
    repo = PlaylistRepository(session)
    events = EventBus()
    service = PlaylistService(repo, events, store)
    pool = _Pool()
    service._artwork._pool = pool
    context = SimpleNamespace(
        service=service, repo=repo, events=events, store=store, tracks=tracks,
        session=session, pool=pool, app=qt_app, tmp_path=tmp_path,
    )
    yield context
    pool.jobs.clear()
    service.shutdown()
    session.close()
    manager.engine.dispose()


def _playlist(ctx, count=4):
    playlist_id = ctx.service.create_playlist("Grid")
    ctx.service.add_tracks(playlist_id, [track.id for track in ctx.tracks[:count]])
    return playlist_id


def _finish(ctx):
    ctx.service._artwork._start()
    assert len(ctx.pool.jobs) == 1
    ctx.pool.jobs.pop().run()


def _assert_colors(data, expected, size):
    with Image.open(io.BytesIO(data)) as image:
        assert image.size == (size, size)
        for index, color in enumerate(expected):
            x = (index % 2) * (size // 2) + size // 4
            y = (index // 2) * (size // 2) + size // 4
            actual = image.convert("RGB").getpixel((x, y))
            assert max(abs(a - b) for a, b in zip(actual, color)) < 15


@pytest.mark.parametrize("count", [0, 1, 2, 3])
def test_small_playlists_reuse_the_first_track_without_encoding(artwork, count):
    ctx = artwork
    pid = _playlist(ctx, count)
    assert ctx.service.get_cover_hash(pid) == (ctx.tracks[0].cover_hash if count else None)
    assert not ctx.service._artwork._pending
    assert ctx.store.playlist_cover(pid) is None


def test_both_tiers_follow_reading_order_and_reach_both_views(artwork):
    ctx = artwork
    pid = _playlist(ctx)
    grid = PlaylistsViewModel(ctx.service, ctx.events)
    cache = TrackRowCache(TrackRepository(ctx.session))
    header = PlaylistTracksViewModel(ctx.service, ctx.events, cache)
    header.load(pid)
    resets, changes = [], []
    grid.modelReset.connect(lambda: resets.append(True))
    grid.dataChanged.connect(lambda _a, _b, roles: changes.append(roles))
    grid.setSelectedExclusive(pid)
    _finish(ctx)
    cover_hash = ctx.service.get_cover_hash(pid)
    assert cover_hash not in {track.cover_hash for track in ctx.tracks}
    assert header.coverHash == grid.data(grid.index(0), PlaylistRoles.CoverHash) == cover_hash
    assert not resets
    assert changes == [[PlaylistRoles.CoverHash]]
    assert grid.selectedUids() == [pid]
    for size in (THUMB_SIZE, LARGE_SIZE):
        _assert_colors(ctx.store.get(cover_hash, size), COLORS[:4], size)
    # These are real AVIF blobs usable by the existing provider.
    from PySide6.QtCore import QSize
    image = CoverImageProvider(ctx.store).requestImage(cover_hash, QSize(), QSize(512, 512))
    assert image.size() == QSize(512, 512)


def test_missing_art_keeps_its_quadrant_including_repeated_covers(artwork):
    ctx = artwork
    ctx.tracks[0].cover_hash = None
    ctx.tracks[2].cover_hash = ctx.tracks[1].cover_hash
    ctx.session.commit()
    pid = _playlist(ctx)
    ctx.service.get_cover_hash(pid)
    _finish(ctx)
    data = ctx.store.get(ctx.service.get_cover_hash(pid), LARGE_SIZE)
    _assert_colors(data, [(40, 40, 44), COLORS[1], COLORS[1], COLORS[3]], LARGE_SIZE)


def test_reordering_replaces_art_and_retires_only_the_unused_grid(artwork):
    ctx = artwork
    pid = _playlist(ctx, 5)
    ctx.service.get_cover_hash(pid)
    _finish(ctx)
    old = ctx.service.get_cover_hash(pid)
    ids = [track.id for track in ctx.tracks]
    ctx.service.set_order(pid, [ids[1], ids[0], *ids[2:]])
    assert ctx.service.get_cover_hash(pid) == old  # Keep current artwork while working.
    _finish(ctx)
    new = ctx.service.get_cover_hash(pid)
    assert new != old
    assert not ctx.store.has(old, THUMB_SIZE)
    _assert_colors(ctx.store.get(new, LARGE_SIZE), [COLORS[1], COLORS[0], *COLORS[2:4]], LARGE_SIZE)
    assert all(ctx.store.has(track.cover_hash, LARGE_SIZE) for track in ctx.tracks)


def test_tail_edits_and_restarts_reuse_the_same_grid(artwork):
    ctx = artwork
    pid = _playlist(ctx)
    ctx.service.get_cover_hash(pid)
    _finish(ctx)
    cover_hash = ctx.service.get_cover_hash(pid)
    ctx.service.add_track(pid, ctx.tracks[4].id)
    assert ctx.service.get_cover_hash(pid) == cover_hash
    ctx.service.remove_track(pid, ctx.tracks[4].id)
    assert ctx.service.get_cover_hash(pid) == cover_hash
    fresh = PlaylistService(ctx.repo, ctx.events, CoverStore(ctx.tmp_path / "covers.db"))
    try:
        assert fresh.get_cover_hash(pid) == cover_hash
        assert not fresh._artwork._pending
    finally:
        fresh.shutdown()
    assert not ctx.service._artwork._pending


def test_shared_grid_survives_until_its_last_playlist_is_deleted(artwork):
    ctx = artwork
    first, second = _playlist(ctx), _playlist(ctx)
    ctx.service.get_summaries()
    _finish(ctx)
    cover_hash = ctx.service.get_cover_hash(first)
    assert ctx.service.get_cover_hash(second) == cover_hash
    # A library-wide sweep must preserve generated references too.
    ctx.store.vacuum_orphans({track.cover_hash for track in ctx.tracks})
    assert ctx.store.has(cover_hash, LARGE_SIZE)
    ctx.service.delete_playlist(first)
    assert ctx.store.has(cover_hash, LARGE_SIZE)
    ctx.service.delete_playlist(second)
    assert not ctx.store.has(cover_hash, LARGE_SIZE)


@pytest.mark.parametrize("owner", ["track", "album", "artist", "playlist"])
def test_other_artwork_owners_prevent_deletion(artwork, owner):
    ctx = artwork
    pid = _playlist(ctx)
    ctx.service.get_cover_hash(pid)
    _finish(ctx)
    cover_hash = ctx.service.get_cover_hash(pid)
    if owner == "track":
        ctx.tracks[4].cover_hash = cover_hash
    elif owner == "album":
        ctx.session.add(Album(name="Shared", cover_hash=cover_hash))
    elif owner == "artist":
        ctx.session.add(Artist(name="Shared", image_hash=cover_hash))
    else:
        ctx.session.add(Playlist(name="Custom", cover_hash=cover_hash))
    ctx.session.commit()
    ctx.service.delete_playlist(pid)
    assert ctx.store.has(cover_hash, LARGE_SIZE)


def test_shrinking_below_four_returns_to_the_first_track(artwork):
    ctx = artwork
    pid = _playlist(ctx)
    ctx.service.get_cover_hash(pid)
    _finish(ctx)
    old = ctx.service.get_cover_hash(pid)
    ctx.service.remove_track(pid, ctx.tracks[3].id)
    assert ctx.service.get_cover_hash(pid) == ctx.tracks[0].cover_hash
    assert not ctx.store.has(old, LARGE_SIZE)


def test_edits_coalesce_and_late_results_never_publish_the_old_order(artwork):
    ctx = artwork
    pid = _playlist(ctx)
    ctx.service.get_cover_hash(pid)
    ctx.service._artwork._start()
    first = ctx.pool.jobs.pop()
    ids = [track.id for track in ctx.tracks[:4]]
    ctx.service.set_order(pid, list(reversed(ids)))
    # No intervening cover read: completion must revalidate against the database.
    first.run()
    assert ctx.store.playlist_cover(pid) is None
    old_hash = playlist_cover_hash(tuple(track.cover_hash for track in ctx.tracks[:4]))
    assert not ctx.store.has(old_hash, LARGE_SIZE)
    _finish(ctx)
    _assert_colors(ctx.store.get(ctx.service.get_cover_hash(pid), LARGE_SIZE),
                   list(reversed(COLORS[:4])), LARGE_SIZE)


def test_deleting_a_playlist_during_encoding_discards_the_result(artwork):
    ctx = artwork
    pid = _playlist(ctx)
    ctx.service.get_cover_hash(pid)
    ctx.service._artwork._start()
    job = ctx.pool.jobs.pop()
    ctx.service.delete_playlist(pid)
    job.run()
    assert ctx.service.get_cover_hash(pid) is None
    assert ctx.store.playlist_cover(pid) is None
    assert not ctx.service._artwork._pending


def test_custom_art_is_preserved_and_does_not_schedule_encoding(artwork):
    ctx = artwork
    pid = _playlist(ctx)
    ctx.session.get(Playlist, pid).cover_hash = ctx.tracks[4].cover_hash
    ctx.session.commit()
    assert ctx.service.get_cover_hash(pid) == ctx.tracks[4].cover_hash
    assert ctx.service.get_summaries()[0]["cover_hash"] == ctx.tracks[4].cover_hash
    assert not ctx.service._artwork._pending


def test_source_art_changes_refresh_the_open_header(artwork):
    ctx = artwork
    pid = _playlist(ctx)
    header = PlaylistTracksViewModel(ctx.service, ctx.events, TrackRowCache(TrackRepository(ctx.session)))
    header.load(pid)
    _finish(ctx)
    old = header.coverHash
    ctx.tracks[0].cover_hash = ctx.tracks[4].cover_hash
    ctx.session.commit()
    ctx.events.emit("track.updated", ctx.tracks[0])
    _finish(ctx)
    assert header.coverHash != old
    _assert_colors(ctx.store.get(header.coverHash, LARGE_SIZE), [COLORS[4], *COLORS[1:4]], LARGE_SIZE)


def test_worker_track_events_refresh_artwork_on_the_models_thread(artwork):
    ctx = artwork
    pid = _playlist(ctx)
    grid = PlaylistsViewModel(ctx.service, ctx.events)
    header = PlaylistTracksViewModel(ctx.service, ctx.events, TrackRowCache(TrackRepository(ctx.session)))
    header.load(pid)
    _finish(ctx)
    old = header.coverHash
    threads = []
    grid.modelReset.connect(lambda: threads.append(threading.get_ident()))
    ctx.tracks[0].cover_hash = ctx.tracks[4].cover_hash
    ctx.session.commit()
    worker = threading.Thread(target=lambda: ctx.events.emit("track.updated", ctx.tracks[0]))
    worker.start()
    worker.join()
    assert not threads
    ctx.app.processEvents()
    assert threads == [threading.get_ident()]
    # processEvents may have already dispatched the coalesced request.
    if not ctx.pool.jobs:
        ctx.service._artwork._start()
    assert len(ctx.pool.jobs) == 1
    ctx.pool.jobs.pop().run()
    assert header.coverHash == grid.entity(pid)["coverHash"] != old


def test_library_track_deletion_rebuilds_the_leading_four(artwork):
    ctx = artwork
    pid = _playlist(ctx, 5)
    grid = PlaylistsViewModel(ctx.service, ctx.events)
    header = PlaylistTracksViewModel(ctx.service, ctx.events, TrackRowCache(TrackRepository(ctx.session)))
    header.load(pid)
    _finish(ctx)
    old = header.coverHash
    track_id = ctx.tracks[0].id
    ctx.session.delete(ctx.tracks[0])
    ctx.session.commit()
    ctx.events.emit("track.deleted", track_id)
    _finish(ctx)
    assert header.trackCount == 4
    assert header.coverHash == grid.entity(pid)["coverHash"] != old
    _assert_colors(ctx.store.get(header.coverHash, LARGE_SIZE), COLORS[1:], LARGE_SIZE)
    assert not ctx.store.has(old, LARGE_SIZE)


def test_corrupt_source_does_not_publish_a_partial_cover_and_can_retry(artwork):
    ctx = artwork
    pid = _playlist(ctx)
    source_hash = ctx.tracks[1].cover_hash
    ctx.store.put(source_hash, LARGE_SIZE, b"broken")
    ctx.service.get_cover_hash(pid)
    _finish(ctx)
    assert ctx.store.playlist_cover(pid) is None
    ctx.store.put(source_hash, LARGE_SIZE, _png(COLORS[1]), "png")
    ctx.service.get_cover_hash(pid)
    _finish(ctx)
    assert ctx.store.playlist_cover(pid) == ctx.service.get_cover_hash(pid)


def test_grid_loading_batches_queries_instead_of_querying_each_playlist(artwork):
    ctx = artwork
    for _ in range(8):
        _playlist(ctx)
    statements = []
    def record(_conn, _cursor, statement, *_args):
        if statement.lstrip().upper().startswith("SELECT"):
            statements.append(statement)
    event.listen(ctx.session.bind, "before_cursor_execute", record)
    try:
        assert len(ctx.service.get_summaries()) == 8
    finally:
        event.remove(ctx.session.bind, "before_cursor_execute", record)
    assert len(statements) == 3
    _finish(ctx)  # All eight requests share one encode.
    assert not ctx.service._artwork._pending


def test_real_worker_encodes_off_the_ui_thread(artwork, monkeypatch):
    from PySide6.QtCore import QThreadPool
    ctx = artwork
    coordinator = ctx.service._artwork
    coordinator._pool = QThreadPool(coordinator)
    coordinator._pool.setMaxThreadCount(1)
    threads = []
    encode = image_ops.make_playlist_covers
    def record(*args):
        threads.append(threading.get_ident())
        return encode(*args)
    monkeypatch.setattr(image_ops, "make_playlist_covers", record)
    pid = _playlist(ctx)
    assert ctx.service.get_cover_hash(pid) == ctx.tracks[0].cover_hash
    assert not threads  # Resolving/scheduling does not perform image work.
    deadline = time.monotonic() + 5
    while not ctx.store.playlist_cover(pid) and time.monotonic() < deadline:
        ctx.app.processEvents()
        time.sleep(.005)
    assert ctx.store.playlist_cover(pid)
    assert threads and threads[0] != threading.get_ident()
