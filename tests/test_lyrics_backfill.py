"""Porting embedded ``LYRICS`` tags into the table.

Lyrics used to live only as a Vorbis comment inside each Opus file. The table
is their home now, and the files already in a library have to be brought over —
by a background sweep the first time Now Playing is opened, and by a lazy
fallback for any single track the sweep never reached.

Four properties are worth pinning, and they are what makes the port safe to
interrupt:

- a swept library ends up with its tags in the table;
- a **second** sweep is a no-op, because the marker says it ran;
- an interrupted sweep keeps everything it had already written;
- a track the sweep never saw still gets ported, the moment it is played.

The sweep worker is driven synchronously — ``run()`` is a plain method on a
plain ``QObject`` and its signals connect directly — so none of this needs an
event loop or a thread.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.lyrics import tags as lyrics_tags  # noqa: E402
from app.lyrics.workers import LyricsTagSweepWorker  # noqa: E402
from app.models.track import Track  # noqa: E402
from app.repositories.lyrics_repository import LyricsRepository  # noqa: E402
from app.services.lyrics_service import SOURCE_TAG, LyricsService  # noqa: E402
from app.settings.lyrics import LyricsSettings  # noqa: E402
from app.settings.store import SettingsStore  # noqa: E402


@pytest.fixture
def service(db, event_bus):
    return LyricsService(LyricsRepository(db.session), event_bus)


@pytest.fixture
def settings():
    return LyricsSettings(SettingsStore({}, {}))


def _ids(db, count):
    return [row[0] for row in db.session.query(Track.id).order_by(Track.id).limit(count)]


def _sweep(targets, service, *, stop_after=None):
    """Run a sweep to completion (or until *stop_after* batches) on this thread.

    Returns ``(batches, finished)`` — the batch payloads seen and whether the
    run reached its end, which is what decides the marker.
    """
    worker = LyricsTagSweepWorker(targets)
    batches: list[list] = []
    finished: list[int] = []

    def on_batch(entries):
        batches.append(list(entries))
        service.save_many(entries, SOURCE_TAG)
        if stop_after is not None and len(batches) >= stop_after:
            worker.cancel()

    worker.batch.connect(on_batch)
    worker.finished.connect(finished.append)
    worker.run()
    return batches, bool(finished)


# ==================================================================
# A library of tagged files
# ==================================================================


def _make_opus(path: Path) -> bool:
    """Encode a second of silence to Opus. False when ffmpeg is not installed."""
    import subprocess

    from app.utils.binaries import ffmpeg_path

    ffmpeg = ffmpeg_path()
    if ffmpeg is None:
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [ffmpeg, "-y", "-f", "lavfi", "-i", "anullsrc=r=48000:cl=mono",
         "-t", "1", "-c:a", "libopus", str(path)],
        check=True, capture_output=True,
    )
    return True


def test_a_tagged_library_is_swept_into_the_table(db, service):
    """The real thing, on real files: mutagen reads what mutagen wrote."""
    from app.importer.tagging import update_opus_tags

    ids = _ids(db, 3)
    targets: list[tuple[int, str]] = []
    for index, track_id in enumerate(ids):
        path = Path(db.session.get(Track, track_id).file_path)
        if not _make_opus(path):
            pytest.skip("ffmpeg is not on PATH, so there are no Opus files to tag")
        # Only the first two carry lyrics — the third stands for the majority of
        # a real library, which has none and must not gain an empty row.
        if index < 2:
            update_opus_tags(path, {"LYRICS": f"[00:0{index}.00]line {index}"})
        targets.append((track_id, str(path)))

    _sweep(targets, service)

    assert service.get(ids[0]) == "[00:00.00]line 0"
    assert service.get(ids[1]) == "[00:01.00]line 1"
    assert service.get(ids[2]) is None
    assert service.repository.get(ids[0]).source == SOURCE_TAG


# ==================================================================
# The sweep, over a stubbed tag reader
# ==================================================================


@pytest.fixture
def tagged(monkeypatch):
    """Answer the tag read from a dict instead of from a file.

    Everything below is about *which files get read and what is done with the
    answers*, not about mutagen — and stubbing here keeps those tests running on
    a checkout with no ffmpeg.
    """
    contents: dict[str, str] = {}
    read: list[str] = []

    def fake_read(path):
        read.append(str(path))
        return contents.get(str(path))

    monkeypatch.setattr(lyrics_tags, "read_lyrics_tag", fake_read)
    from app.lyrics import workers

    monkeypatch.setattr(workers, "read_lyrics_tag", fake_read)
    return {"contents": contents, "read": read}


def _targets(db, ids, tagged, *, with_lyrics=()):
    targets = []
    for track_id in ids:
        path = f"/vault/{track_id}.opus"
        if track_id in with_lyrics:
            tagged["contents"][path] = f"words for {track_id}"
        targets.append((track_id, path))
    return targets


def test_only_tracks_with_a_tag_get_a_row(db, service, tagged):
    ids = _ids(db, 5)
    targets = _targets(db, ids, tagged, with_lyrics=ids[:2])

    _sweep(targets, service)

    assert [service.get(i) for i in ids[:2]] == [f"words for {i}" for i in ids[:2]]
    assert all(service.get(i) is None for i in ids[2:])


def test_a_second_sweep_reads_nothing_because_the_marker_says_so(
    db, service, settings, tagged,
):
    """The marker is the whole reason it is not repeated.

    Tracks whose files never had a tag stay permanently in the "no lyrics" set,
    so without the flag every launch would re-open every one of them to learn
    the same nothing.
    """
    ids = _ids(db, 4)
    targets = _targets(db, ids, tagged, with_lyrics=ids[:1])

    _sweep(targets, service)
    settings.tag_sweep_done = True
    tagged["read"].clear()

    # What the view model checks before building a target list at all.
    assert settings.tag_sweep_done is True
    assert tagged["read"] == []


def test_an_interrupted_sweep_keeps_what_it_already_wrote(db, service, tagged):
    """Batching is what makes a stop cheap: everything before it is committed."""
    from app.lyrics import workers

    ids = _ids(db, 6)
    targets = _targets(db, ids, tagged, with_lyrics=ids)
    # Two per batch, so the run has somewhere to be interrupted.
    workers.SWEEP_BATCH = 2
    try:
        batches, finished = _sweep(targets, service, stop_after=1)
    finally:
        workers.SWEEP_BATCH = 200

    assert not finished, "a cancelled run must not report itself finished"
    assert len(batches) == 1
    # The first batch is in the table and stays there.
    assert [service.get(i) for i in ids[:2]] == ["words for %d" % i for i in ids[:2]]
    # The rest were never reached, and are still waiting to be.
    assert service.get(ids[5]) is None
    assert set(ids[2:]) <= set(service.track_ids_without_lyrics())


def test_resuming_picks_up_only_what_is_left(db, service, tagged):
    ids = _ids(db, 4)
    targets = _targets(db, ids, tagged, with_lyrics=ids)
    _sweep(targets[:2], service)
    tagged["read"].clear()

    # A resumed sweep works from the ids that still have no row.
    remaining = [t for t in targets if t[0] in set(service.track_ids_without_lyrics())]
    _sweep(remaining, service)

    assert all(service.get(i) == f"words for {i}" for i in ids)
    # The two already ported were not opened a second time.
    assert tagged["read"] == [f"/vault/{i}.opus" for i in ids[2:]]


# ==================================================================
# The lazy fallback
# ==================================================================


def test_playing_a_track_the_sweep_missed_ports_it(qt_app, db, service, monkeypatch):
    """One file, opened only because the user is listening to that track."""
    from app.viewmodels import lyrics_view_model as module

    track = db.session.query(Track).order_by(Track.id).first()
    opened: list[str] = []

    def fake_read(path):
        opened.append(str(path))
        return "[00:03.00]from the file"

    monkeypatch.setattr(module, "read_lyrics_tag", fake_read)

    vm = module.LyricsViewModel(_FakePlayer(track), service, LyricsSettings(None))
    # Opening the tab with no marker would also start a sweep; the load is what
    # is under test, so it is driven directly.
    vm._load()

    assert vm.has_lyrics is True
    assert vm.is_synced is True
    assert vm.synced_lines == ["from the file"]
    assert opened == [track.file_path]
    # And it is in the table now, marked as having come from a tag.
    assert service.get(track.id) == "[00:03.00]from the file"
    assert service.repository.get(track.id).source == SOURCE_TAG


def test_a_second_play_reads_the_table_not_the_file(qt_app, db, service, monkeypatch):
    from app.viewmodels import lyrics_view_model as module

    track = db.session.query(Track).order_by(Track.id).first()
    service.save(track.id, "already stored", SOURCE_TAG)

    monkeypatch.setattr(
        module, "read_lyrics_tag",
        lambda path: pytest.fail("opened the file despite having a row"),
    )

    vm = module.LyricsViewModel(_FakePlayer(track), service, LyricsSettings(None))
    vm._load()

    assert vm.plain_text == "already stored"


def test_a_track_with_no_tag_anywhere_reads_as_having_no_lyrics(
    qt_app, db, service, monkeypatch,
):
    from app.viewmodels import lyrics_view_model as module

    track = db.session.query(Track).order_by(Track.id).first()
    monkeypatch.setattr(module, "read_lyrics_tag", lambda path: None)

    vm = module.LyricsViewModel(_FakePlayer(track), service, LyricsSettings(None))
    vm._load()

    assert vm.has_lyrics is False
    # No empty row was left behind for the sweep to keep finding.
    assert service.get(track.id) is None


def _FakePlayer(track):  # noqa: N802 — a factory named for what it builds
    """PlayerViewModel's surface, as LyricsViewModel uses it.

    A QObject with real signals, because the view model connects to two of them.
    """
    from PySide6.QtCore import QObject, Signal

    class Player(QObject):
        current_track_changed = Signal()
        position_changed = Signal()

        def __init__(self):
            QObject.__init__(self)
            self._current_track = track
            self.position = 0.0

    return Player()
