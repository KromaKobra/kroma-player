"""Focusing one half of Now Playing, driven through the real QML.

The page splits down the middle: the sleeve on the left, the words or the queue
on the right. Either half can now take the whole width, and that is entirely a
QML feature — three derived properties, two `visible` bindings and a pair of
conditional anchors, with no backend property behind any of it. So the failure
mode it has is the one a Python-level test cannot see: a binding written against
something that does not exist leaves the handles doing nothing at all, quietly.

Four things are pinned here, and they are the four that would break silently:

*The split is still the resting state.* Nothing about this feature is allowed to
change what the page looks like when nobody has asked for anything.

*Focus is one tri-state, not two booleans.* There is no "both halves focused" to
get into, and the test that would catch one appearing is the pair of `visible`
assertions in opposite directions.

*The gate is `wide`, not the stored value.* Under 900px there is no split to
return to, so a stored focus has to be ignored rather than obeyed — otherwise a
narrow window restores to a page with its artwork hidden and no way back.

*It survives the app.* The choice goes to session.json, beside navIndex and the
settings tab, which is the same call `tests/test_album_artwork.py` checks for
`albumArtwork` in settings.json.

The harness is `tests/test_lyrics_editor_qml.py`'s, and it is load-bearing for
the same two reasons: the content lives inside `visible: playerVM.has_current_track`
so a track has to be playing, and the second half only exists above 900px.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

# The view is sized itself rather than filling the window. A window manager is
# entitled to hand back a different geometry than the one asked for — the
# offscreen platform does, intermittently — and `wide` is read off the *view's*
# width, so a view anchored to the window inherits that argument. Here the
# window is only somewhere to put it.
HARNESS = """
import QtQuick
import Nocturne
import "views"

Window {{
    width: 1600
    height: 900
    visible: true
    NowPlayingView {{
        objectName: "npv"
        width: {width}
        height: 860
        active: true
    }}
}}
"""


def _call(obj, name, *args):
    """Invoke a QML-declared function. They are not Python attributes."""
    from PySide6.QtCore import Q_ARG, QMetaObject, Qt

    return QMetaObject.invokeMethod(
        obj, name, Qt.ConnectionType.DirectConnection,
        *[Q_ARG("QVariant", value) for value in args],
    )


class _Page:
    """The Now Playing view, its two halves, and a way to settle the engine."""

    def __init__(self, view, window, registry, app, warnings):
        self.view = view
        self.window = window
        self.registry = registry
        self.app = app
        self.warnings = warnings

    def settle(self, ticks: int = 10):
        for _ in range(ticks):
            self.app.processEvents()

    def focus(self, half: str):
        _call(self.view, "toggleFocus", half)
        self.settle()

    def item(self, name):
        from PySide6.QtCore import QObject

        found = self.window.findChild(QObject, name)
        assert found is not None, f"no item named {name!r} in the page"
        return found

    @property
    def art(self):
        return self.item("artSide")

    @property
    def words(self):
        return self.item("rightSide")

    @property
    def content_width(self) -> float:
        """The room the two halves share out.

        Read off the halves' own parent rather than computed from the window,
        so a change to the page's margins does not fail this test for a reason
        that is not about focus.
        """
        return float(self.art.parent().property("width"))


@pytest.fixture
def open_page(qt_app, tmp_path_factory):
    """Open the page at a given width, optionally with a focus already stored."""
    from PySide6.QtCore import QObject, QUrl
    from PySide6.QtQml import QQmlApplicationEngine
    from PySide6.QtQuickControls2 import QQuickStyle

    opened: list = []

    def open_it(width: int = 1600, stored: str | None = None) -> _Page:
        from app.artwork.cover_provider import CoverImageProvider
        from app.artwork.local_art_provider import LocalArtImageProvider
        from app.gui.gui_paths import bundled_gui_root
        from app.main import CONTEXT_PROPERTIES, _register_qml_types
        from app.models.track import Track
        from app.utils.library_paths import set_library_root

        QQuickStyle.setStyle("Fusion")
        vault = tmp_path_factory.mktemp("vault") / "venomvault"
        (vault / ".data").mkdir(parents=True)
        set_library_root(vault)

        from app.bootstrap import create_service_registry

        registry = create_service_registry()
        # Seeded before the engine is built, because Theme's prefs read the
        # store once as they are created — which is exactly how a stored focus
        # arrives on a real launch.
        if stored is not None:
            registry.get_service("settings_view_model").setSessionValue(
                "nowPlayingFocus", stored
            )
        _register_qml_types(registry)

        warnings: list[str] = []
        directory = bundled_gui_root() / "nocturne"
        engine = QQmlApplicationEngine()
        engine.warnings.connect(
            lambda errors: warnings.extend(e.toString() for e in errors)
        )
        engine.addImageProvider(
            "cover", CoverImageProvider(registry.get_service("cover_store"))
        )
        engine.addImageProvider("localart", LocalArtImageProvider())
        context = engine.rootContext()
        for qml_name, service in CONTEXT_PROPERTIES.items():
            context.setContextProperty(qml_name, registry.get_service(service))
        engine.addImportPath(str(directory))

        # The harness has to sit inside the package so `import "views"` resolves.
        harness = directory / "__NowPlayingFocusHarness.qml"
        harness.write_text(HARNESS.format(width=width))
        opened.append((engine, harness))

        engine.load(QUrl.fromLocalFile(str(harness)))
        roots = engine.rootObjects()
        assert roots, f"the harness failed to load: {warnings}"
        window = roots[0]

        track_service = registry.get_service("track_service")
        track_id = track_service.create_track_with_relationships(
            {"title": "A track", "artists": "An artist"}
        )
        player = registry.get_service("player_view_model")
        player._current_track = track_service.track_repo.session.get(Track, track_id)
        player.current_track_changed.emit()

        view = window.findChild(QObject, "npv")
        assert view is not None

        page = _Page(view, window, registry, qt_app, warnings)
        page.settle()
        return page

    try:
        yield open_it
    finally:
        for engine, harness in opened:
            harness.unlink(missing_ok=True)
            del engine


def test_the_page_still_opens_split(open_page):
    """Nothing asked for, so both halves are there and each has half the room."""
    page = open_page()

    assert page.art.property("visible") is True
    assert page.words.property("visible") is True

    art_width = float(page.art.property("width"))
    # The gutter, taken from where the halves actually sit rather than from a
    # copy of Theme.gapXl — the point is that the two of them share the room
    # out, not what the number between them is.
    gutter = float(page.words.property("x")) - art_width
    assert gutter > 0, "the two halves are overlapping"
    assert art_width == pytest.approx((page.content_width - gutter) / 2, abs=1.0)

    assert page.warnings == [], f"the page loads with warnings: {page.warnings}"


def test_focusing_the_art_takes_the_whole_width(open_page):
    page = open_page()
    page.focus("art")

    assert page.words.property("visible") is False, (
        "the words half is still showing beside a focused sleeve"
    )
    assert page.art.property("visible") is True
    assert float(page.art.property("width")) == pytest.approx(
        page.content_width, abs=1.0
    ), "the art half was focused and did not grow"


def test_focusing_the_words_takes_the_whole_width(open_page):
    """The mirror, and the one the conditional anchor exists for.

    An invisible QML item keeps its geometry, so a words half still anchored to
    `artSide.right` would be focused and yet start halfway across the page —
    which is why `x` is asserted here and not only the width.
    """
    page = open_page()
    page.focus("words")

    assert page.art.property("visible") is False, (
        "the sleeve is still showing beside focused words"
    )
    assert page.words.property("visible") is True
    assert float(page.words.property("x")) == pytest.approx(0, abs=1.0), (
        "the focused words half is still anchored to the hidden sleeve"
    )
    assert float(page.words.property("width")) == pytest.approx(
        page.content_width, abs=1.0
    )


def test_pressing_the_handle_of_the_focused_half_gives_the_split_back(open_page):
    """The handle is a toggle: the way out is the control that got you in."""
    page = open_page()
    page.focus("art")
    assert page.words.property("visible") is False

    page.focus("art")

    assert page.art.property("visible") is True
    assert page.words.property("visible") is True
    assert float(page.art.property("width")) < page.content_width / 2 + 1


def test_only_one_half_is_ever_focused(open_page):
    """Focusing the other half is a move from one state, not a second flag."""
    page = open_page()
    page.focus("art")
    page.focus("words")

    assert page.view.property("focusHalf") == "words"
    assert page.art.property("visible") is False
    assert page.words.property("visible") is True


def test_the_focused_half_survives_the_app(open_page):
    """Where you were, so it goes to session.json beside navIndex."""
    from app.session import session_path

    page = open_page()
    page.focus("words")

    page.registry.get_service("settings_view_model").sync()

    stored = json.loads(session_path().read_text())
    assert stored["gui"]["nocturne"]["nowPlayingFocus"] == "words", (
        f"the choice never reached session.json: {stored.get('gui')}"
    )


def _glyph(handle):
    """The Icon inside a handle — found by the one property only it has.

    `source` is what catches a glyph that does not exist: `Icons.svg()` hands
    back an empty string for a name it has never heard of, and the Image then
    renders nothing, silently. Walked with `childItems()` because these are
    items inside a component instance, not QObject children of it.
    """
    def walk(item):
        for child in item.childItems():
            yield child
            yield from walk(child)

    icons = [c for c in walk(handle) if c.property("name") is not None]
    assert icons, "the handle has no icon in it at all"
    return icons[0]


def test_the_handles_are_wired_to_the_halves_and_draw_both_glyphs(open_page):
    """The control itself: what it offers, what it says, and that it acts.

    `toggled` is emitted rather than the view's function called, so this covers
    the wiring between the two — a handle connected to the wrong half, or to
    nothing, passes every other test in this file.
    """
    page = open_page()
    art_handle = page.item("artFocusHandle")
    words_handle = page.item("wordsFocusHandle")

    assert art_handle.property("offered") is True
    assert words_handle.property("offered") is True
    # Hidden at rest: it is not offered until the pointer is in its half.
    assert art_handle.property("visible") is False
    assert _glyph(art_handle).property("source").toString() != "", (
        "the unfocused handle's glyph is not in Icons.shapes"
    )

    art_handle.toggled.emit()
    page.settle()

    assert page.view.property("focusHalf") == "art", (
        "the art half's handle is not wired to the art half"
    )
    assert art_handle.property("focused") is True
    assert _glyph(art_handle).property("source").toString() != "", (
        "the focused handle's glyph is not in Icons.shapes"
    )

    art_handle.toggled.emit()
    page.settle()
    words_handle.toggled.emit()
    page.settle()

    assert page.view.property("focusHalf") == "words"


def test_the_handles_are_not_offered_without_a_split(open_page):
    page = open_page(width=800)

    assert page.item("artFocusHandle").property("offered") is False, (
        "a window with one half is offering to give that half the whole width"
    )


def test_a_narrow_window_ignores_a_stored_focus(open_page):
    """Under 900px there is no split to come back to.

    The stored value is kept rather than cleared — widening the window restores
    the half you left — but it is not in effect, and the art-only layout that
    narrow windows have always had is what shows.
    """
    page = open_page(width=800, stored="words")

    assert page.view.property("wide") is False
    assert page.view.property("focusHalf") == "", (
        "a stored focus is in effect on a window with no second half"
    )
    assert page.art.property("visible") is True, (
        "a narrow window came up with its artwork hidden and no way back"
    )
    assert page.words.property("visible") is False
    assert float(page.art.property("width")) == pytest.approx(
        page.content_width, abs=1.0
    )
    assert page.warnings == [], f"the narrow page loads with warnings: {page.warnings}"
