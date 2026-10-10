"""Shared fixtures for the library core tests.

The tests here run headless — no QGuiApplication, no QML engine. That is
possible because the list models never depend on an event loop to be correct:
the coalescing flush is exposed as ``flush_now()`` so a test can drive it
directly, which is also how a caller would force a synchronous read.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import event as sa_event

# Must be set before any Qt application is constructed, so the QML tests can
# build a real engine without needing a display.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

# Read by app.utils.library_paths: it turns the "fall back to the configured
# vault" behaviour into a hard error, so an unset library root can never quietly
# resolve to the developer's real music library. See isolated_library_root.
os.environ["KROMA_TESTING"] = "1"

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def pytest_collection_modifyitems(items):
    """Mark every test that stands up a Qt application as ``gui``.

    Read off the fixture closure rather than written on each test: a test is a
    GUI test exactly when something it asks for reaches ``qt_app``, and that
    stays true as fixtures move between files or grow a layer in between — a
    hand-applied marker would go stale the first time one did.

    What it buys is ``-m "not gui"`` as the inner-loop suite. The GUI tests are
    three quarters of the wall clock (~200s of ~260s) for a third of the files,
    almost all of it spent pumping the event loop through cross-fades that have
    to run in real time; the rest of the suite is a minute and answers most of
    what a small change breaks. Run the whole thing before pushing.

    Walked from what each test *asks for* rather than off ``item.fixturenames``,
    which is the whole closure and therefore includes the autouse fixtures. The
    session-scoped ``qml_settings`` above is autouse and takes ``qt_app``, so
    every test in the suite reaches ``qt_app`` through it and the closure marks
    all 1792 as GUI. Only an explicit request means the test wants a window.
    """
    for item in items:
        info = getattr(item, "_fixtureinfo", None)
        if info is None:
            continue
        pending, seen = list(info.argnames), set()
        while pending:
            name = pending.pop()
            if name in seen:
                continue
            seen.add(name)
            if name == "qt_app":
                item.add_marker(pytest.mark.gui)
                break
            for definition in info.name2fixturedefs.get(name, ()):
                pending.extend(definition.argnames)

from app.database.connection import DatabaseManager  # noqa: E402
from app.events.event_bus import EventBus  # noqa: E402
from app.library.context import LibraryContext, clear_library_context, set_library_context  # noqa: E402
from app.library.row_cache import TrackRowCache  # noqa: E402
from app.plugins.external_ids import lookup_values  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app.models.track import (  # noqa: E402
    Album,
    Artist,
    Genre,
    Track,
    track_albums_association,
)
from app.repositories.track_repository import TrackRepository  # noqa: E402
from tests.fakes.providers import VIDEO, id_rows  # noqa: E402

TRACK_COUNT = 1200
ARTIST_COUNT = 40
GENRE_COUNT = 5
ALBUM_COUNT = 60


@pytest.fixture(autouse=True)
def isolated_library_root(tmp_path):
    """Point the path helpers at a throwaway vault, and put it back afterwards.

    Autouse, and not merely tidy. ``library_root()`` resolves to the developer's
    *real* vault when nothing has set it, so ``Track.file_path`` on a seeded test
    track would name a genuine audio file — and code paths that retag or delete
    would then act on it. This happened once, before this fixture existed.

    Pinning it per test makes every test hermetic by default. Tests that need a
    vault of their own can still call ``set_library_root`` — whatever they set is
    saved and put back here, so no test leaks its root into the next one.
    """
    from app.utils import library_paths

    previous = library_paths._library_root
    vault = tmp_path / "vault"
    (vault / ".data").mkdir(parents=True, exist_ok=True)
    library_paths.set_library_root(vault)
    yield vault
    library_paths._library_root = previous


@pytest.fixture(autouse=True)
def no_cover_network(monkeypatch):
    """Keep cover-art fetches off the network unless a test opts back in.

    Autouse for the same reason as the vault above: the import queue prefetches
    the next row's artwork while the current one is being written, so *any* test
    that drives a stash — including ones that care only about aborting — spawns
    a real HTTP fetch for every queued row unless something stops it.

    That is not merely slow. The prefetch thread outlives the row it was
    started for, and a test that finishes while it is still in a socket read
    drops the view model with a live thread under it, which is a hard crash
    rather than a failure. Stubbed here so no test has to remember.

    Deliberately only the name the workers call, not the one in
    `app.importer.stash`: the tests that exercise the URL walk itself call
    `stash.fetch_first` directly and must still reach the real thing. A test
    wanting artwork out of a prefetch patches this same name with its own stub,
    which wins because it is applied later.
    """
    from app.importer import import_workers

    monkeypatch.setattr(
        import_workers, "fetch_first", lambda urls, **kwargs: None,
    )


def make_track(session, *, album: str = "", external_ids=None, **kwargs) -> Track:
    """Build a Track, resolving *album* to an ``Album`` row on the way.

    Tests wrote ``Track(album="Bangarang EP", ...)`` back when the album was a
    column on the track. It is a relationship now, so the row has to exist
    before the track can point at it — this keeps that a one-liner instead of
    three, and keeps the tests reading the way they did. *external_ids* is
    ``{key: value}`` for the track's id rows, for the same reason.

    A plain function rather than a fixture because tests build their tracks
    inside list literals, where a fixture cannot reach.
    """
    track = Track(**kwargs)
    if external_ids:
        track.external_id_rows = id_rows("track", external_ids)
    if album:
        row = session.query(Album).filter(Album.name == album).first()
        if row is None:
            row = Album(name=album)
            session.add(row)
            session.flush()
        track.albums = [row]
    return track


def owned_by_id_only(rows, lookup):
    """Resolve *rows* to track ids using *lookup*, by external id and no further.

    The id half of "is this recording already in the library" — see
    `TrackService.find_owned`, which does this and then falls back to comparing
    the recording itself. Most doubles across these tests have no opinion about
    that second half: they stand for a library that holds nothing, or a handful
    of ids, and the id half is exactly what they always meant.

    *lookup* takes a list of id values and answers ``{value: track_id}`` — the
    doubles hold one kind of id each, so the kind is not worth carrying.

    A test that is *about* the recording rule wants a double that implements it
    — see `_StubImportService` in `tests/test_import_album.py`, which runs the
    real `recording_match` over a small library.
    """
    rows = list(rows)
    found = lookup([value for row in rows for _key, value in lookup_values(row)])
    return [
        next((found[value] for _key, value in lookup_values(row) if value in found), None)
        for row in rows
    ]


class OwnedByIdOnly:
    """`owned_track_ids` for an *import service* double, from its own ids.

    Mixed in rather than copied into eleven files. See `owned_by_id_only`.
    """

    def owned_track_ids(self, results):
        return owned_by_id_only(results, self.existing_ids)


class FindsOwnedByIdOnly:
    """The same, for a *track service* double — `find_owned` rather than
    `owned_track_ids`, and reading the map the double already answers with."""

    def find_owned(self, descriptions):
        return owned_by_id_only(descriptions, self.existing_ids)


class SeededDb:
    """A seeded database plus the session the repositories share.

    ``DatabaseManager`` is a frozen dataclass, so the session travels alongside
    it rather than being attached to it.
    """

    def __init__(self, manager, session):
        self.manager = manager
        self.session = session
        self.engine = manager.engine


@pytest.fixture
def db(tmp_path):
    """A seeded SQLite library in a temporary directory.

    Deliberately more than 500 tracks so the `IN (...)` chunking in the
    repository is exercised by an ordinary full-library fetch.
    """
    manager = DatabaseManager.from_path(tmp_path / "tracks.db")
    session = manager.create_session()

    artists = [Artist(name=f"Artist {i:03d}") for i in range(ARTIST_COUNT)]
    genres = [Genre(name=f"Genre {i}") for i in range(GENRE_COUNT)]
    # An album is a row now rather than a string on the track, so the fixture
    # has to make them before it can hand them out. One album per track, which
    # keeps every existing count assertion true: `Track.album` still reads back
    # the same "Album N" it did when it was a column.
    albums = [Album(name=f"Album {i}") for i in range(ALBUM_COUNT)]
    session.add_all(artists + genres + albums)
    session.flush()

    for i in range(TRACK_COUNT):
        session.add(
            Track(
                title=f"Track {i:05d}",
                duration_seconds=100 + (i % 300),
                bpm=60 + (i % 120),
                musical_key="Am",
                release_date=20200101 + i,
                isrc=f"ISRC{i:07d}",
                external_id_rows=id_rows("track", {VIDEO: f"v{i:08d}"}),
                cover_hash=f"{i:032x}",
                artists=[artists[i % ARTIST_COUNT]],
                genres=[genres[i % GENRE_COUNT]],
                albums=[albums[i % ALBUM_COUNT]],
            )
        )
    session.flush()

    # Assigning the collection writes the pairs and neither ordering column —
    # see write_album_positions. `album_position` is right by default (one album
    # each, server default 0), but the running order has to be numbered or every
    # track on an album sits at position 0.
    for album in albums:
        assoc = track_albums_association
        members = [
            int(row[0])
            for row in session.execute(
                select(assoc.c.track_id)
                .where(assoc.c.album_id == album.id)
                .order_by(assoc.c.track_id)
            ).all()
        ]
        for position, track_id in enumerate(members):
            session.execute(
                assoc.update()
                .where(assoc.c.album_id == album.id, assoc.c.track_id == track_id)
                .values(position=position)
            )
    session.commit()

    yield SeededDb(manager, session)
    session.close()


@pytest.fixture
def repo(db):
    return TrackRepository(db.session)


@pytest.fixture
def cache(repo):
    return TrackRowCache(repo, capacity=100_000)


@pytest.fixture
def event_bus():
    return EventBus()


@pytest.fixture
def library(repo, cache, event_bus):
    """Install the library context, as bootstrap would."""
    set_library_context(
        LibraryContext(repository=repo, row_cache=cache, event_bus=event_bus)
    )
    yield LibraryContext(repository=repo, row_cache=cache, event_bus=event_bus)
    clear_library_context()


class QueryCounter:
    """Counts SQL statements issued against an engine.

    Several of the guarantees in this design are about *how many* queries a
    thing costs, not just what it returns — "one query before first paint",
    "a re-sort reads no rows". Those are only testable by counting.
    """

    def __init__(self, engine):
        self.statements: list[str] = []
        sa_event.listen(engine, "before_cursor_execute", self._record)
        self._engine = engine

    def _record(self, conn, cursor, statement, parameters, context, executemany):
        self.statements.append(" ".join(statement.split())[:120])

    def reset(self) -> None:
        self.statements.clear()

    def __len__(self) -> int:
        return len(self.statements)

    def detach(self) -> None:
        sa_event.remove(self._engine, "before_cursor_execute", self._record)


@pytest.fixture
def queries(db):
    counter = QueryCounter(db.engine)
    yield counter
    counter.detach()


@pytest.fixture(scope="session")
def qt_app():
    """One QGuiApplication for the whole run.

    A QGuiApplication rather than a QCoreApplication because the QML tests need
    a GUI application object; on the offscreen platform it costs nothing extra
    and Qt permits only one instance per process either way.
    """
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuickControls2 import QQuickStyle

    # Same style main() sets, so the GUI-loading tests do not drown in
    # "the current style does not support customization" notices.
    QQuickStyle.setStyle("Fusion")
    app = QGuiApplication.instance() or QGuiApplication(sys.argv)
    yield app


@pytest.fixture(scope="session", autouse=True)
def qml_settings(qt_app):
    """The ``Kroma.Settings`` singleton every QML test resolves against.

    Nocturne's ``Theme.qml`` is a ``pragma Singleton`` and reads its preferences
    through ``Kroma.Settings``, so any test that builds a Nocturne component
    needs the type registered before an engine resolves it. Without it the
    failure is a load error naming a missing module, not a missing value — and
    QML caches a compiled type by URL for the whole process, so the first engine
    that fails to resolve ``Theme.qml`` poisons every engine built afterwards.
    Hence autouse: one test that forgot to ask would take the rest of the QML
    suite with it.

    Session-scoped, and it has to be. ``register_settings_singleton`` registers
    once per process because a second registration invalidates every QML file
    compiled before it; this is that one registration, made before any
    module-scoped window fixture runs. The ``_register_qml_types(registry)``
    calls in the window tests therefore register the declarable types and leave
    this singleton alone — so ``settingsVM`` and ``Kroma.Settings`` are two
    stores under test where the application has one. Nothing is asserted across
    that seam, and one shared in-memory store is tidier than what it replaced: a
    file inside the GUI package, which leaked between whole *runs*.
    """
    from app.main import register_settings_singleton
    from app.settings.store import SettingsStore
    from app.viewmodels.settings_view_model import SettingsViewModel

    # Held for the length of the session. Qt does not take ownership of a
    # singleton instance, and every engine built in the run keeps the pointer.
    view_model = SettingsViewModel(SettingsStore({}, {}), "nocturne")
    register_settings_singleton(view_model)
    yield view_model
