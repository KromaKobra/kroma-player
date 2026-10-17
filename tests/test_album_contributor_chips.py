"""The contributing-artist chips in the album hero, loaded for real.

Modelled on tests/test_album_page_actions.py, and it exists for that file's
reason: a binding error in QML is a runtime warning nobody sees, and a strip that
measures itself to zero draws nothing at all — which on an album page looks
exactly like a record with no credits on it. Neither is something a Python-level
test can notice, and `tests/test_album_contributors.py` already covers everything
that happens below QML.

What is pinned here is the wiring: that the page asks the view model, hands the
answer to the hero in the order it arrived, asks again when the album changes
under it, and can send a pressed chip off to the artist page. Nothing here
asserts a chip *count* — the hero's width comes from a session value, a
`minimumWidth` and whatever screen the test finds, so "four names fit" measures
a different strip on every machine. That arithmetic is pinned in
tests/test_contributor_chips_fit.py, where the width is an input.
"""
from __future__ import annotations

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtQml import QQmlApplicationEngine

from app.gui.gui_paths import bundled_gui_root
from app.main import CONTEXT_PROPERTIES, _register_qml_types
from app.models.track import Album, Artist, Track
from app.repositories.track_repository import write_artist_positions


ALBUM_VIEW = 11
ARTIST_VIEW = 9


@pytest.fixture
def backend(tmp_path, monkeypatch):
    """A compilation with four artists, and a record with none.

    The names are long on purpose. A strip measured against "M83" fits
    everything at any window size and would pass the truncation tests without
    ever truncating.
    """
    from app.utils.library_paths import set_library_root

    monkeypatch.setenv("KROMA_DATA_DIR", str(tmp_path))
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    set_library_root(vault)

    from app.bootstrap import create_service_registry

    registry = create_service_registry()
    session = registry.get_service("db_session")

    names = [
        "The Chemical Brothers",
        "Massive Attack Featuring Horace Andy",
        "Underworld",
        "Leftfield",
    ]
    artists = [Artist(name=name) for name in names]
    session.add_all(artists)

    compilation = Album(name="A Very Long Night")
    uncredited = Album(name="Field Recordings")
    session.add_all([compilation, uncredited])
    session.flush()

    # Descending contributions, so the expected order is unambiguous and no
    # tie-break is involved — the ordering rule itself is pinned next door, in
    # tests/test_album_contributors.py.
    membership = [
        ("Opener", [artists[0]]),
        ("Second", [artists[0], artists[1]]),
        ("Third", [artists[0], artists[1]]),
        ("Fourth", [artists[0], artists[2]]),
        ("Fifth", [artists[1], artists[3]]),
        ("Sixth", [artists[2]]),
    ]
    for title, credited in membership:
        row = Track(title=title, duration_seconds=200, artists=list(credited))
        row.albums = [compilation]
        session.add(row)
        session.flush()
        write_artist_positions(
            session, {int(row.id): [int(a.id) for a in credited]}
        )

    silent = Track(title="Rain", duration_seconds=120)
    silent.albums = [uncredited]
    session.add(silent)

    # More names than fit any window this application will open in, which is how
    # the truncation is tested: the window has a `minimumWidth` and the test
    # platform will not honour an arbitrary resize, so the overflow has to come
    # from the album rather than from the geometry.
    crowded = Album(name="Everyone At Once")
    session.add(crowded)
    session.flush()
    for index in range(14):
        guest = Artist(name="Guest Contributor Number %02d" % index)
        session.add(guest)
        session.flush()
        row = Track(title="Piece %02d" % index, duration_seconds=90, artists=[guest])
        row.albums = [crowded]
        session.add(row)
    session.commit()

    repo = registry.get_service("album_service").album_repo
    for album in (compilation, uncredited, crowded):
        repo.set_order(int(album.id), repo.track_ids(int(album.id)))
    session.commit()
    registry.get_service("albums_view_model").refresh()

    _register_qml_types(registry)
    registry.compilation_id = int(compilation.id)
    registry.uncredited_id = int(uncredited.id)
    registry.crowded_id = int(crowded.id)
    registry.lead_artist_id = int(artists[0].id)
    return registry


def _load(qt_app, backend, album_id):
    """Open the album page on *album_id*.

    The window keeps whatever width it opens at. Resizing it from here is not
    worth attempting — `main.qml` binds `width` to a session value and holds a
    `minimumWidth`, and the offscreen platform clamps what is left — which is
    why the truncation below is produced by an album with more names on it
    rather than by a narrow window.
    """
    directory = bundled_gui_root() / "nocturne"
    engine = QQmlApplicationEngine()

    from app.artwork.cover_provider import CoverImageProvider
    from app.artwork.local_art_provider import LocalArtImageProvider

    engine.addImageProvider("cover", CoverImageProvider(backend.get_service("cover_store")))
    engine.addImageProvider("localart", LocalArtImageProvider())

    context = engine.rootContext()
    for qml_name, service in CONTEXT_PROPERTIES.items():
        context.setContextProperty(qml_name, backend.get_service(service))

    warnings: list = []
    engine.warnings.connect(lambda w: warnings.extend(w))

    engine.addImportPath(str(directory))
    engine.load(QUrl.fromLocalFile(str(directory / "main.qml")))
    assert engine.rootObjects(), "nocturne failed to load:\n" + "\n".join(
        w.toString() for w in warnings
    )
    root = engine.rootObjects()[0]

    backend.get_service("album_edit_view_model").load(album_id)
    backend.get_service("album_tracks_view_model").load(album_id)
    root.setProperty("viewIndex", ALBUM_VIEW)
    for _ in range(8):
        qt_app.processEvents()

    return engine, root, warnings


def _strip(root):
    """The album page's strip.

    Scoped to `albumView` rather than searched for from the window, because
    `DetailHero` is the artist and playlist heroes too and all three carry a
    strip with this name. An unscoped `findChild` finds whichever page was
    declared first, which is not this one — the same trap `albumTable` documents
    on the track table.
    """
    page = root.findChild(object, "albumView")
    assert page is not None, "the album page is not in the window"
    return page.findChild(object, "contributorChips")


def _settle(qt_app, strip, rounds=200):
    """Pump events until the strip stops changing its mind, and check it did.

    What arrives late is the width: the window resolves its own over several
    event-loop turns, and the strip is anchored into whatever gap that leaves.
    The count the strip draws is now a binding over that width rather than a
    pass deferred through `Qt.callLater`, so there is nothing left to wait for
    *after* the width lands — but the width itself still lands when it lands. A
    fixed count of `processEvents()` passes on an idle machine and fails on a
    loaded one, which is the flake this file's first version had: it measured a
    strip a hundred pixels wide and concluded, correctly for that width, that
    nothing fitted.

    So this waits for a run of identical samples long enough not to be a lull.
    It does not assert anything about the width it settled on: how wide the hero
    ends up is a property of the machine, which is why nothing in this file
    asserts a chip *count*. The arithmetic lives in
    tests/test_contributor_chips_fit.py, where the width is an input.
    """
    last = None
    stable = 0
    for _ in range(rounds):
        qt_app.processEvents()
        now = (strip.property("shown"), strip.property("hidden"),
               strip.property("width"))
        if now == last:
            stable += 1
            if stable >= 12:
                break
        else:
            stable = 0
            last = now


def _contributors(strip):
    """The strip's list as Python. A `property var` arrives as a QJSValue."""
    value = strip.property("contributors")
    return value.toVariant() if hasattr(value, "toVariant") else (value or [])


def _chip_names(strip):
    """The names on the chips that are actually being drawn, left to right."""
    return [c["name"] for c in _contributors(strip)[:strip.property("shown")]]


# --------------------------------------------------------------- it is there


def test_the_strip_is_built_with_the_backend_s_order(qt_app, backend):
    engine, root, warnings = _load(qt_app, backend, backend.compilation_id)
    strip = _strip(root)
    assert strip is not None, "no contributor strip:\n" + "\n".join(
        w.toString() for w in warnings
    )

    expected = [
        row["name"]
        for row in backend.get_service("album_edit_view_model").contributingArtists()
    ]
    assert [c["name"] for c in _contributors(strip)] == expected
    # Never re-sorted in QML: the order carries a tie-break the strip cannot see.
    assert expected[0] == "The Chemical Brothers"

    del engine


def test_every_name_is_shown_or_counted(qt_app, backend):
    """Nobody falls between the two. True at any width, which is the point."""
    engine, root, _ = _load(qt_app, backend, backend.compilation_id)
    strip = _strip(root)
    _settle(qt_app, strip)

    assert strip.property("count") == 4
    assert strip.property("shown") + strip.property("hidden") == 4

    del engine


def test_an_album_with_no_credits_draws_nothing(qt_app, backend):
    """Not a "+0", and not a strip of blank chips."""
    engine, root, _ = _load(qt_app, backend, backend.uncredited_id)
    strip = _strip(root)

    assert strip.property("count") == 0
    assert strip.property("shown") == 0
    assert strip.property("hidden") == 0

    del engine


# ------------------------------------------------------------ it says who led


def _drawn_widths(strip):
    """The widths of the chips actually on screen, left to right.

    Off the Row, which is the only thing a Repeater's delegates are children of,
    and only the visible ones: how many of those there are is a property of the
    machine's idea of a window, so nothing here counts them.
    """
    row = strip.findChild(object, "contributorRow")
    assert row is not None, "the strip no longer has an object-named row"
    chips = [c for c in row.childItems()
             if c.objectName() == "contributorChip" and c.isVisible()]
    chips.sort(key=lambda c: c.property("index"))
    return [c.property("width") for c in chips]


def test_the_artist_on_most_of_the_record_is_the_large_chip(qt_app, backend):
    """The whole path, end to end: four tracks of six is a majority, and a
    majority is a chip half again the size of the guests beside it.

    The compilation is built so exactly one artist clears the bar — the second
    name is on three of the six, which is the half that is not a majority, so
    this also pins that the strip is drawing the backend's answer rather than
    simply enlarging whoever came first.
    """
    engine, root, _ = _load(qt_app, backend, backend.compilation_id)
    strip = _strip(root)
    _settle(qt_app, strip)

    assert [c["isMain"] for c in _contributors(strip)] == [True, False, False, False]

    collapsed = strip.property("collapsedWidth")
    widths = _drawn_widths(strip)
    assert widths, "no chips were drawn at all"
    assert widths[0] == collapsed * strip.property("prominentScale")
    assert all(w == collapsed for w in widths[1:])

    del engine


def test_nobody_leads_a_compilation(qt_app, backend):
    """Fourteen guests with a track each: every chip stays the same size.

    The case the feature is *for* is the record with two leads and a long tail,
    and this is the other end of it — a strip where emphasising somebody would
    be inventing a billing the record does not have.
    """
    engine, root, _ = _load(qt_app, backend, backend.crowded_id)
    strip = _strip(root)
    _settle(qt_app, strip)

    assert not any(c["isMain"] for c in _contributors(strip))
    collapsed = strip.property("collapsedWidth")
    assert all(w == collapsed for w in _drawn_widths(strip))

    del engine


# --------------------------------------------------------------- it truncates


def test_too_many_names_are_held_back(qt_app, backend):
    engine, root, _ = _load(qt_app, backend, backend.crowded_id)
    strip = _strip(root)
    _settle(qt_app, strip)

    shown, hidden = strip.property("shown"), strip.property("hidden")
    assert hidden > 0, "fourteen long names fitted the hero"
    assert shown + hidden == 14

    overflow = strip.findChild(object, "contributorOverflow")
    assert overflow is not None
    assert overflow.property("width") > 0

    del engine


def test_what_is_held_back_is_the_tail(qt_app, backend):
    """The names on screen are the most-credited ones, in the backend's order.

    Not whichever happened to be short — a strip that packed itself would put
    the least important chip first on exactly the albums it matters for.
    """
    engine, root, _ = _load(qt_app, backend, backend.crowded_id)
    strip = _strip(root)
    _settle(qt_app, strip)

    everyone = [c["name"] for c in _contributors(strip)]
    assert _chip_names(strip) == everyone[:strip.property("shown")]

    del engine


# ---------------------------------------------------------------- it refreshes


def test_a_drag_reorders_a_tie(qt_app, backend):
    """The subtlest trigger, and the one that was wrong first time round.

    A drag changes no count, so the chips can only move because the running
    order is the tie-break. The trap is *when* to re-read: Qt's own `rowsMoved`
    goes out once per move, while `track_albums.position` still holds the order
    the drag started from — so a page listening to that reads the database a
    moment too early and shows the answer to the previous question.

    On the crowded album every guest is on exactly one track, so the whole
    ordering *is* the tie-break and a single drag has to rewrite all of it.
    """
    engine, root, _ = _load(qt_app, backend, backend.crowded_id)
    page = root.findChild(object, "albumView")

    def names():
        value = page.property("contributors")
        value = value.toVariant() if hasattr(value, "toVariant") else value
        return [c["name"] for c in value]

    assert names()[0] == "Guest Contributor Number 00"

    backend.get_service("album_tracks_view_model").moveRows([5], 0)
    for _ in range(40):
        qt_app.processEvents()

    assert names()[0] == "Guest Contributor Number 05", (
        "the chips did not follow the new running order"
    )
    expected = [
        row["name"]
        for row in backend.get_service("album_edit_view_model").contributingArtists()
    ]
    assert names() == expected

    del engine


# -------------------------------------------------------------- it navigates


def test_a_chip_opens_the_artist(qt_app, backend):
    """Driven through the signal rather than a synthesised press.

    The chips sit in a band that cross-fades, and clicking one by coordinate is
    the flakiness tests/test_album_page_actions.py already warns about. What is
    worth pinning is the wiring — page signal to `openArtist` — not Qt's ability
    to deliver a mouse event.
    """
    engine, root, _ = _load(qt_app, backend, backend.compilation_id)
    page = root.findChild(object, "albumView")
    assert page is not None

    page.openArtistRequested.emit(backend.lead_artist_id)
    for _ in range(8):
        qt_app.processEvents()

    assert root.property("viewIndex") == ARTIST_VIEW
    assert backend.get_service("artist_edit_view_model").artistId == backend.lead_artist_id

    del engine


# ----------------------------------------------------- the two shared heroes


def test_the_other_pages_are_untouched(qt_app, backend):
    """DetailHero is the artist and playlist heroes too.

    `contributors` defaults empty and those pages never set it, so their strips
    have nothing to draw. This is the regression guard on the shared component:
    the album page gaining a section must not put an empty band on two others.
    """
    engine, root, _ = _load(qt_app, backend, backend.compilation_id)

    backend.get_service("artist_edit_view_model").load(backend.lead_artist_id)
    root.setProperty("viewIndex", ARTIST_VIEW)
    for _ in range(8):
        qt_app.processEvents()

    artist_page = root.findChild(object, "artistView")
    assert artist_page is not None
    for strip in artist_page.findChildren(object, "contributorChips"):
        assert strip.property("count") == 0
        assert strip.property("shown") == 0

    del engine


def test_the_hero_binds_cleanly(qt_app, backend):
    """A typo in a new property is a warning, not a failure, without this."""
    engine, root, warnings = _load(qt_app, backend, backend.compilation_id)

    fatal = [
        w.toString() for w in warnings
        if "is not defined" in w.toString()
        or "Unable to assign" in w.toString()
        or "Cannot read property" in w.toString()
        or "Binding loop" in w.toString()
    ]
    assert not fatal, "binding errors:\n" + "\n".join(fatal)

    del engine
